"""3MF (.3mf) -> triangles. Runs in Blender's Python, no extra libraries. Reads the build items of the model through
components and nested model parts (production extension), base materials and colour groups (per object and per
triangle), and the model unit; coordinates are written in metres.
Writes geometry.npz/json (see geometry_io.py). Usage: read_3mf.py <in.3mf> <outdir>"""
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geometry_io import DEFAULT_MATERIAL, MeshCollector, colour_name  # noqa: E402

UNITS = {"micron": 1e-6, "millimeter": 1e-3, "centimeter": 1e-2, "inch": 0.0254, "foot": 0.3048, "meter": 1.0}
SKIPPED_TYPES = {"support", "solidsupport"}
MAX_DEPTH = 20


class ReadError(Exception):
    pass


def local(tag):
    return tag.rsplit("}", 1)[-1]


def colour(text):
    """'#RRGGBB' or '#RRGGBBAA' -> (r, g, b) in 0..1, or None."""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})(?:[0-9a-fA-F]{2})?", (text or "").strip())
    return tuple(int(m.group(1)[i:i + 2], 16) / 255 for i in (0, 2, 4)) if m else None


def matrix(text):
    """transform='m00 m01 m02 m10 m11 m12 m20 m21 m22 m30 m31 m32' (row vectors) -> 4 x 4, or identity."""
    try:
        v = [float(x) for x in (text or "").split()]
    except ValueError:
        v = []
    m = np.eye(4)
    if len(v) == 12:
        m[:3, :3] = np.array(v[:9]).reshape(3, 3)
        m[3, :3] = v[9:]
    return m


def parse_model(stream):
    """One model part -> dict(unit, objects {id: object}, groups {id: [(name, rgb)]}, items [(id, matrix)])."""
    out = dict(unit="millimeter", objects={}, groups={}, items=[])
    obj = group = None
    verts, tris, tri_mat = [], [], []
    for event, el in ET.iterparse(stream, events=("start", "end")):
        tag = local(el.tag)
        if event == "start":
            if tag == "model":
                out["unit"] = el.get("unit", "millimeter")
            elif tag == "object":
                obj = dict(id=el.get("id"), type=el.get("type", "model"), pid=el.get("pid"), pindex=el.get("pindex"),
                           name=el.get("name") or "", components=[])
                verts, tris, tri_mat = [], [], []
            elif tag in ("basematerials", "colorgroup"):
                group = []
                out["groups"][el.get("id")] = group
            continue
        if tag == "vertex":
            verts.append((float(el.get("x")), float(el.get("y")), float(el.get("z"))))
        elif tag == "triangle":
            tris.append((int(el.get("v1")), int(el.get("v2")), int(el.get("v3"))))
            tri_mat.append((el.get("pid"), el.get("p1")))
        elif tag == "base" and group is not None:
            group.append((el.get("name"), colour(el.get("displaycolor"))))
        elif tag == "color" and group is not None:
            group.append((None, colour(el.get("color"))))
        elif tag == "component" and obj is not None:
            path = next((v for k, v in el.attrib.items() if local(k) == "path"), None)
            obj["components"].append((el.get("objectid"), path, matrix(el.get("transform"))))
        elif tag == "object" and obj is not None:
            obj["V"] = np.array(verts, np.float64).reshape(-1, 3)
            obj["F"] = np.array(tris, np.int64).reshape(-1, 3)
            obj["tri_mat"] = tri_mat if any(p is not None or i is not None for p, i in tri_mat) else None
            out["objects"][obj["id"]] = obj
            obj = None
        elif tag == "item":
            out["items"].append((el.get("objectid"), matrix(el.get("transform"))))
        if tag in ("vertex", "triangle", "base", "color", "component", "item"):
            el.clear()
    return out


class Package:
    def __init__(self, path):
        self.zip = zipfile.ZipFile(path)
        self.parts = {}
        root = self.root_path()
        self.root = self.part(root)
        self.names = {}  # material name -> rgb, to keep one colour per name

    def root_path(self):
        names = {n.lower(): n for n in self.zip.namelist()}
        if "_rels/.rels" in names:
            for el in ET.fromstring(self.zip.read(names["_rels/.rels"])):
                if el.get("Type", "").lower().endswith("/3dmodel") and el.get("Target"):
                    target = el.get("Target").lstrip("/")
                    if target.lower() in names:
                        return names[target.lower()]
        for lower, name in names.items():
            if lower.endswith(".model"):
                return name
        raise ReadError("This 3MF file has no 3D model part.")

    def part(self, path):
        key = path.lstrip("/").lower()
        if key not in self.parts:
            names = {n.lower(): n for n in self.zip.namelist()}
            if key not in names:
                raise ReadError("3MF component file %s is missing" % path)
            with self.zip.open(names[key]) as f:
                self.parts[key] = parse_model(f)
        return self.parts[key]

    def lookup_group(self, part, pid):
        if pid in part["groups"]:
            return part["groups"][pid]
        for other in self.parts.values():
            if pid in other["groups"]:
                return other["groups"][pid]
        return None

    def material(self, part, pid, index):
        """(name, rgb) from a property group reference, or the default material."""
        group = self.lookup_group(part, pid) if pid is not None else None
        try:
            name, rgb = group[int(index or 0)]
        except (TypeError, IndexError, ValueError):
            return DEFAULT_MATERIAL, None
        if rgb is None:
            return DEFAULT_MATERIAL, None
        name = name or colour_name(*rgb)
        if self.names.setdefault(name, rgb) != rgb:
            name = colour_name(*rgb)
        return name, rgb


def emit(package, part, obj, world, scale, collector, depth=0):
    """Adds the object's mesh and its components, with the accumulated transform `world` (row vectors)."""
    if depth > MAX_DEPTH or obj["type"] in SKIPPED_TYPES:
        return
    V, F = obj["V"], obj["F"]
    if len(F):
        valid = (F >= 0).all(1) & (F < len(V)).all(1)
        W = (V @ world[:3, :3] + world[3, :3]) * scale
        flip = np.linalg.det(world[:3, :3]) < 0
        default = package.material(part, obj["pid"], obj["pindex"])
        materials = [default] * len(F)
        if obj["tri_mat"]:
            for i, (pid, p1) in enumerate(obj["tri_mat"]):
                if pid is not None or p1 is not None:
                    materials[i] = package.material(part, pid if pid is not None else obj["pid"], p1)
        keys = {}
        index = np.array([keys.setdefault(m, len(keys)) for m in materials])
        for (name, rgb), k in keys.items():
            faces = F[valid & (index == k)]
            if len(faces):
                collector.add(name, W, faces[:, ::-1] if flip else faces, False, rgb)
    for object_id, path, transform in obj["components"]:
        sub_part = package.part(path) if path else part
        sub = sub_part["objects"].get(object_id)
        if sub is not None:
            emit(package, sub_part, sub, transform @ world, scale, collector, depth + 1)


def run(src, outdir):
    t0 = time.time()
    print("PROGRESS 5 Reading 3MF", flush=True)
    try:
        package = Package(src)
    except zipfile.BadZipFile:
        raise ReadError("This is not a valid 3MF (zip) file.") from None
    root = package.root
    scale = UNITS.get(root["unit"].lower(), 1e-3)
    collector = MeshCollector()
    items = root["items"] or [(oid, np.eye(4)) for oid, o in root["objects"].items() if len(o["F"])]
    print("PROGRESS 40 Collecting geometry", flush=True)
    for object_id, transform in items:
        obj = root["objects"].get(object_id)
        if obj is not None:
            emit(package, root, obj, transform, scale, collector)
    if not len(collector):
        raise ReadError("No triangles found in this 3MF file")
    collector.save(outdir)
    print("3mf: %d triangles in %d meshes [%.0fs]" % (collector.triangles, len(collector), time.time() - t0),
          flush=True)


if __name__ == "__main__":
    try:
        run(sys.argv[1], sys.argv[2])
    except ReadError as ex:
        print(ex, file=sys.stderr, flush=True)
        sys.exit(1)
