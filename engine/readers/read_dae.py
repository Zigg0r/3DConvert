"""COLLADA (.dae, e.g. a SketchUp export) -> triangles, without Blender (Blender 5 no longer imports COLLADA).
Runs in Blender's Python.
- Read as a stream: geometries are converted one by one into compact arrays and released.
- Components (instance_node) and groups are expanded with their matrices, nested ones too. Every placement becomes
  plain, editable geometry (nothing is instanced). Lines (edges) and cameras are ignored.
- Material = the material bound to each triangle set; its colour comes from the effect.
Writes geometry.npz/json (see geometry_io.py). Usage: read_dae.py <model.dae> <outdir>"""
import os
import sys
import time
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geometry_io import DEFAULT_MATERIAL, MeshCollector  # noqa: E402
from polygons import triangulate_rings  # noqa: E402

NS = "{http://www.collada.org/2005/11/COLLADASchema}"
MAX_DEPTH = 40
UP_AXIS = {"value": "Z_UP"}
GREY = [0.8, 0.8, 0.8]


def q(tag):
    return NS + tag


def numbers(text, dtype=np.float64):
    return np.array(text.split(), dtype=dtype) if text else np.zeros(0, dtype)


def read_geometry(element):
    """<geometry> -> (vertices (n, 3), [(material symbol, triangles (m, 3))]), or None without triangles."""
    mesh = element.find(q("mesh"))
    if mesh is None:
        return None
    sources = {}
    for source in mesh.findall(q("source")):
        float_array = source.find(q("float_array"))
        if float_array is not None:
            sources[source.get("id")] = float_array.text
    vertices = mesh.find(q("vertices"))
    if vertices is None:
        return None
    position = None
    for item in vertices.findall(q("input")):
        if item.get("semantic") == "POSITION":
            position = item.get("source")[1:]
    if position is None or position not in sources:
        return None
    V = numbers(sources[position]).reshape(-1, 3)
    parts = []
    for kind in ("triangles", "polylist", "polygons"):
        for prim in mesh.findall(q(kind)):
            inputs = prim.findall(q("input"))
            stride = max(int(i.get("offset")) for i in inputs) + 1
            offset = [int(i.get("offset")) for i in inputs if i.get("semantic") == "VERTEX"][0]
            if kind == "triangles":
                p = prim.find(q("p"))
                if p is None or not p.text:
                    continue
                F = numbers(p.text, np.int64)[offset::stride].reshape(-1, 3)
            elif kind == "polylist":
                p, vcount = prim.find(q("p")), prim.find(q("vcount"))
                if p is None or not p.text or vcount is None or not vcount.text:
                    continue
                F = polygon_triangles(numbers(vcount.text, np.int64), numbers(p.text, np.int64)[offset::stride], V)
            else:  # <polygons>: one <p> per polygon
                flat, counts = [], []
                for p in prim.findall(q("p")):
                    if p.text:
                        ring = numbers(p.text, np.int64)[offset::stride]
                        flat.append(ring)
                        counts.append(len(ring))
                if not flat:
                    continue
                F = polygon_triangles(np.array(counts, np.int64), np.concatenate(flat), V)
            if len(F):
                parts.append((prim.get("material"), F))
    return (V, parts) if parts else None


def polygon_triangles(counts, flat, V):
    """Polygons (vertex counts and the vertex indices one after the other) -> triangles (m, 3): triangles as they are,
    quads cut along the diagonal that stays inside, larger polygons by ear clipping."""
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    out = []
    for n in np.unique(counts):
        if n < 3:
            continue
        idx = flat[starts[counts == n][:, None] + np.arange(n)]
        if n == 3:
            out.append(idx)
        elif n == 4:
            P = V[idx]
            inside = (np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0]) * np.cross(P[:, 2] - P[:, 0], P[:, 3] - P[:, 0])).sum(1) >= 0
            out.append(np.where(inside[:, None], idx[:, [0, 1, 2]], idx[:, [0, 1, 3]]))
            out.append(np.where(inside[:, None], idx[:, [0, 2, 3]], idx[:, [1, 2, 3]]))
        else:
            for ring in idx:
                try:
                    tri = triangulate_rings([V[ring]])
                except Exception:  # noqa: BLE001 - degenerate polygon
                    tri = np.zeros((0, 3), np.int64)
                if not len(tri):
                    tri = np.array([[0, i, i + 1] for i in range(1, n - 1)])  # plain fan
                out.append(ring[tri])
    return np.concatenate(out) if out else np.zeros((0, 3), np.int64)


def read_matrix(node):
    """Node transform: <matrix>, <translate>, <rotate>, <scale> multiplied in document order."""
    result = np.eye(4)
    for element in node:
        tag = element.tag.rsplit("}", 1)[-1]
        if tag not in ("matrix", "translate", "rotate", "scale") or not element.text:
            continue
        v = numbers(element.text)
        m = np.eye(4)
        if tag == "matrix" and len(v) >= 16:
            m = v[:16].reshape(4, 4)
        elif tag == "translate" and len(v) >= 3:
            m[:3, 3] = v[:3]
        elif tag == "scale" and len(v) >= 3:
            m[0, 0], m[1, 1], m[2, 2] = v[:3]
        elif tag == "rotate" and len(v) >= 4:
            axis = v[:3] / (np.linalg.norm(v[:3]) or 1.0)
            a = np.radians(v[3])
            c, s = np.cos(a), np.sin(a)
            x, y, z = axis
            m[:3, :3] = [[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                         [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                         [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]]
        result = result @ m
    return result


def read_node(node):
    """<node> -> dict(name, matrix, geometries [(geometry id, {symbol: material id})], instances [node id], children [dict])"""
    out = dict(name=node.get("name") or node.get("id") or "?", matrix=read_matrix(node), geometries=[], instances=[],
               children=[])
    for instance in node.findall(q("instance_geometry")):
        bound = {}
        for material in instance.iter(q("instance_material")):
            bound[material.get("symbol")] = material.get("target")[1:]
        out["geometries"].append((instance.get("url")[1:], bound))
    for instance in node.findall(q("instance_node")):
        out["instances"].append(instance.get("url")[1:])
    for child in node.findall(q("node")):
        out["children"].append(read_node(child))
    return out


def read_file(path):
    """One pass over the file -> (geometries, material names, colours, node library, scene roots, unit in metres)."""
    geometries = {}  # id -> (parts [(symbol, vertices around the centre float32, triangles int32)], centre float64)
    effects, materials, library, scene = {}, {}, {}, None
    unit = 1.0
    parents = []
    for event, element in ET.iterparse(path, events=("start", "end")):
        if event == "start":
            if not parents:  # the namespace differs per COLLADA version (1.4: 2005/11, 1.5: 2008/03)
                global NS
                NS = element.tag[:element.tag.index("}") + 1] if element.tag.startswith("{") else ""
            parents.append(element.tag)
            continue
        parents.pop()
        tag = element.tag
        if tag == q("unit"):
            unit = float(element.get("meter") or 1.0)
        elif tag == q("up_axis"):
            UP_AXIS["value"] = (element.text or "Z_UP").strip().upper()
        elif tag == q("effect"):
            colour = GREY
            c = element.find(".//" + q("diffuse") + "/" + q("color"))
            if c is not None:
                colour = list(numbers(c.text))[:3]
            effects[element.get("id")] = colour
            element.clear()
        elif tag == q("material"):
            effect = element.find(q("instance_effect"))
            materials[element.get("id")] = (element.get("name") or element.get("id"),
                                            effect.get("url")[1:] if effect is not None else None)
            element.clear()
        elif tag == q("geometry"):
            geometry = read_geometry(element)
            if geometry is not None:
                V, triangle_sets = geometry
                centre = (V.min(0) + V.max(0)) / 2  # around its own centre: float32 stays precise
                around = (V - centre).astype(np.float32)
                parts = []
                for symbol, F in triangle_sets:
                    used, local = np.unique(F, return_inverse=True)
                    parts.append((symbol, around[used], local.reshape(-1, 3).astype(np.int32)))
                geometries[element.get("id")] = (parts, centre)
            element.clear()
        elif tag == q("node") and parents and parents[-1] == q("library_nodes"):
            library[element.get("id")] = read_node(element)
            element.clear()
        elif tag == q("visual_scene"):
            scene = [read_node(n) for n in element.findall(q("node"))]
            element.clear()
    names = {mid: name for mid, (name, _) in materials.items()}
    colours = {name: effects.get(effect, GREY) for name, effect in materials.values()}
    return geometries, names, colours, library, scene or [], unit


def placements(scene, library, geometries):
    """Walk the node tree: [(geometry id, matrix in source units, {symbol: material id})]."""
    found = []

    def walk(node, matrix, depth):
        if depth > MAX_DEPTH:
            return
        matrix = matrix @ node["matrix"]
        for gid, bound in node["geometries"]:
            if gid in geometries:
                found.append((gid, matrix, bound))
        for node_id in node["instances"]:
            component = library.get(node_id)
            if component is not None:
                walk(component, matrix, depth + 1)
        for child in node["children"]:
            walk(child, matrix, depth + 1)

    for root in scene:
        own = dict(name=root["name"], matrix=np.eye(4), geometries=root["geometries"], instances=root["instances"],
                   children=[])
        for node in root["children"] + [own]:
            if not node["name"].startswith("skp_camera"):  # SketchUp scene cameras
                walk(node, root["matrix"], 0)
    return found


def run(path, outdir):
    t0 = time.time()
    print("PROGRESS 5 Reading COLLADA", flush=True)
    geometries, names, colours, library, scene, unit = read_file(path)
    print("read: %d geometries, %d components, %d materials [%.0fs]"
          % (len(geometries), len(library), len(names), time.time() - t0), flush=True)
    found = placements(scene, library, geometries)
    collector = MeshCollector()
    step = max(len(found) // 50, 1)
    for n, (gid, matrix, bound) in enumerate(found):
        parts, centre = geometries[gid]
        for symbol, V, F in parts:
            material_id = bound.get(symbol, symbol)
            name = names.get(material_id, material_id or DEFAULT_MATERIAL)
            world = ((V.astype(np.float64) + centre) @ matrix[:3, :3].T + matrix[:3, 3]) * unit
            if UP_AXIS["value"] == "Y_UP":  # COLLADA files say which axis points up; the result is Z-up
                world = np.stack([world[:, 0], -world[:, 2], world[:, 1]], axis=1)
            elif UP_AXIS["value"] == "X_UP":
                world = np.stack([world[:, 1], world[:, 2], world[:, 0]], axis=1)
            collector.add(name, world, F, False, colours.get(name))
        if n % step == 0:
            print("PROGRESS %d Placing geometry %d/%d" % (10 + 85 * n // len(found), n + 1, len(found)), flush=True)
    if not len(collector):
        print("No triangles or polygons found in this COLLADA file.", file=sys.stderr, flush=True)
        return 1
    collector.save(outdir)
    print("done: %d placements, %d triangles in %d meshes [%.0fs]"
          % (len(found), collector.triangles, len(collector), time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1], sys.argv[2]))
