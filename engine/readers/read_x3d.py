"""X3D (.x3d, XML encoding) -> triangles. Runs in Blender's Python, no extra libraries. Reads Shape nodes
(IndexedFaceSet, IndexedTriangleSet, TriangleSet, Box) through Transform/Group/Switch/LOD/Collision/Anchor nodes,
with DEF/USE; material = Material node (its DEF name, else its colour). Coordinates are used as stored (X3D is in
metres). The classic encoding (.x3dv) is read by read_wrl.py.
Writes geometry.npz/json (see geometry_io.py). Usage: read_x3d.py <in.x3d> <outdir>"""
import os
import re
import sys
import time
import xml.etree.ElementTree as ET

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geometry_io import DEFAULT_MATERIAL, MeshCollector, colour_name  # noqa: E402
from read_wrl import box_geometry, transform_matrix, triangulate  # noqa: E402

GROUPING = {"Group", "Transform", "Collision", "Anchor", "Billboard", "Switch", "LOD", "StaticGroup"}
SEPARATORS = re.compile(r"[\s,]+")
MAX_DEPTH = 60


class ReadError(Exception):
    pass


def local(tag):
    return tag.rsplit("}", 1)[-1]


def numbers(text):
    out = []
    for part in SEPARATORS.split((text or "").strip()):
        try:
            out.append(float(part))
        except ValueError:
            pass
    return out


def resolve(el, defs):
    use = el.get("USE")
    return defs.get(use, el) if use else el


def child(el, defs, *names):
    for c in el:
        c = resolve(c, defs)
        if local(c.tag) in names:
            return c
    return None


def points_of(geometry, defs):
    coord = child(geometry, defs, "Coordinate")
    if coord is None:
        return None
    p = numbers(coord.get("point"))
    return np.array(p[:len(p) // 3 * 3], np.float64).reshape(-1, 3)


def material_of(shape, defs):
    appearance = child(shape, defs, "Appearance")
    material = child(appearance, defs, "Material") if appearance is not None else None
    if material is None:
        return DEFAULT_MATERIAL, None
    diffuse = numbers(material.get("diffuseColor"))
    rgb = diffuse[:3] if len(diffuse) == 3 else [0.8, 0.8, 0.8]
    name = material.get("DEF")
    return name or colour_name(*rgb), rgb


def geometry_of(geometry, defs):
    kind = local(geometry.tag)
    if kind == "Box":
        size = numbers(geometry.get("size")) or [2, 2, 2]
        return box_geometry(dict(f={"size": size}))
    V = points_of(geometry, defs)
    if V is None or not len(V):
        return None
    if kind == "IndexedFaceSet":
        ccw = (geometry.get("ccw", "true").lower() != "false")
        return V, triangulate(V, numbers(geometry.get("coordIndex")), ccw)
    if kind == "IndexedTriangleSet":
        index = np.array([int(i) for i in numbers(geometry.get("index"))], np.int64)
        F = index[:len(index) // 3 * 3].reshape(-1, 3)
        F = F[(F >= 0).all(1) & (F < len(V)).all(1)]
        return V, (F if geometry.get("ccw", "true").lower() != "false" else F[:, ::-1])
    if kind == "TriangleSet":
        F = np.arange(len(V) // 3 * 3).reshape(-1, 3)
        return V, (F if geometry.get("ccw", "true").lower() != "false" else F[:, ::-1])
    return None


def visit(el, matrix, collector, defs, depth=0):
    el = resolve(el, defs)
    if depth > MAX_DEPTH:
        return
    kind = local(el.tag)
    if kind == "Shape":
        geometry = child(el, defs, "IndexedFaceSet", "IndexedTriangleSet", "TriangleSet", "Box")
        built = geometry_of(geometry, defs) if geometry is not None else None
        if built is None or not len(built[1]):
            return
        V, F = built
        world = V @ matrix[:3, :3].T + matrix[:3, 3]
        if np.linalg.det(matrix[:3, :3]) < 0:  # mirrored: keep the triangles facing outward
            F = F[:, ::-1]
        material, rgb = material_of(el, defs)
        collector.add(material, world, F, False, rgb)
        return
    if kind not in GROUPING:
        return
    if kind == "Transform":
        fields = {k: numbers(el.get(k)) for k in ("translation", "center", "scale", "scaleOrientation", "rotation")}
        matrix = matrix @ transform_matrix(dict(f={k: v for k, v in fields.items() if v}))
    children = list(el)
    if kind == "Switch":
        which = int(numbers(el.get("whichChoice"))[0]) if numbers(el.get("whichChoice")) else -1
        children = [children[which]] if 0 <= which < len(children) else []
    elif kind == "LOD":
        children = children[:1]  # the most detailed level
    for c in children:
        visit(c, matrix, collector, defs, depth + 1)


def run(src, outdir):
    t0 = time.time()
    print("PROGRESS 5 Parsing X3D", flush=True)
    try:
        root = ET.parse(src).getroot()
    except ET.ParseError as ex:
        raise ReadError("This X3D file is not valid XML: %s" % ex) from None
    defs = {el.get("DEF"): el for el in root.iter() if el.get("DEF")}
    scene = next((el for el in root.iter() if local(el.tag) == "Scene"), root)
    print("PROGRESS 50 Collecting geometry", flush=True)
    collector = MeshCollector()
    for node in scene:
        visit(node, np.eye(4), collector, defs)
    if not len(collector):
        raise ReadError("No faces found in this X3D file")
    collector.save(outdir)
    print("x3d: %d triangles in %d meshes [%.0fs]" % (collector.triangles, len(collector), time.time() - t0),
          flush=True)


if __name__ == "__main__":
    try:
        run(sys.argv[1], sys.argv[2])
    except ReadError as ex:
        print(ex, file=sys.stderr, flush=True)
        sys.exit(1)
