"""AMF (.amf, plain XML or zipped) -> triangles. Runs in Blender's Python, no extra libraries. Reads every object's
volumes (colour from the volume, else its material, else the object), the unit, and constellations (instances with
offset and rotation); coordinates are written in metres.
Writes geometry.npz/json (see geometry_io.py). Usage: read_amf.py <in.amf> <outdir>"""
import io
import math
import os
import sys
import time
import xml.etree.ElementTree as ET
import zipfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geometry_io import DEFAULT_MATERIAL, MeshCollector, colour_name  # noqa: E402

UNITS = {"micrometer": 1e-6, "millimeter": 1e-3, "inch": 0.0254, "feet": 0.3048, "meter": 1.0}
MAX_DEPTH = 20


class ReadError(Exception):
    pass


def local(tag):
    return tag.rsplit("}", 1)[-1]


def number(el, default=0.0):
    try:
        return float(el.text)
    except (TypeError, ValueError):
        return default


def rotation(rx, ry, rz):
    """Rotations about x, then y, then z (degrees) as a column-vector matrix."""
    def axis(angle, i, j):
        c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        m = np.eye(3)
        m[i, i], m[i, j], m[j, i], m[j, j] = c, -s, s, c
        return m
    return axis(rz, 0, 1) @ axis(ry, 2, 0) @ axis(rx, 1, 2)


def open_amf(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                name = next((n for n in z.namelist() if n.lower().endswith(".amf")), None)
                if name is None:
                    raise ReadError("No .amf file found inside this zip.")
                raw = z.read(name)
        except zipfile.BadZipFile:
            raise ReadError("This is not a valid AMF file.") from None
    return io.BytesIO(raw)


def rgb_of(el):
    return tuple(number(el.find("{*}" + k), 0.8) for k in "rgb")


def parse(stream):
    """-> unit, objects {id: [(material id, colour, vertices (n, 3), triangles (m, 3))]},
    materials {id: (name, rgb)}, constellations {id: [(object or constellation id, offset, rotation)]}"""
    unit, objects, materials, constellations = "millimeter", {}, {}, {}
    stack = []
    obj = vol = mat = cons_id = None
    for event, el in ET.iterparse(stream, events=("start", "end")):
        tag = local(el.tag)
        if event == "start":
            stack.append(tag)
            if tag == "amf":
                unit = el.get("unit", "millimeter")
            elif tag == "object":
                obj = dict(id=el.get("id"), vertices=[], volumes=[], colour=None)
            elif tag == "volume":
                vol = dict(material=el.get("materialid"), colour=None, tris=[])
            elif tag == "material":
                mat = dict(id=el.get("id"), name="", rgb=None)
            elif tag == "constellation":
                cons_id = el.get("id")
                constellations[cons_id] = []
            continue
        stack.pop()
        parent = stack[-1] if stack else None
        if tag == "vertex" and obj is not None:
            c = el.find("{*}coordinates")
            if c is not None:
                obj["vertices"].append([number(c.find("{*}" + a)) for a in "xyz"])
            el.clear()
        elif tag == "triangle" and vol is not None:
            vol["tris"].append([int(number(el.find("{*}" + k))) for k in ("v1", "v2", "v3")])
            el.clear()
        elif tag == "color":
            if parent == "material" and mat is not None:
                mat["rgb"] = rgb_of(el)
            elif parent == "volume" and vol is not None:
                vol["colour"] = rgb_of(el)
            elif parent == "object" and obj is not None:
                obj["colour"] = rgb_of(el)
        elif tag == "metadata" and parent == "material" and mat is not None and el.get("type") == "name":
            mat["name"] = el.text or ""
        elif tag == "volume" and obj is not None and vol is not None:
            obj["volumes"].append(vol)
            vol = None
        elif tag == "object" and obj is not None:
            V = np.array(obj["vertices"], np.float64).reshape(-1, 3)
            objects[obj["id"]] = [(v["material"], v["colour"] or obj["colour"], V,
                                   np.array(v["tris"], np.int64).reshape(-1, 3)) for v in obj["volumes"]]
            obj = None
        elif tag == "material" and mat is not None:
            materials[mat["id"]] = (mat["name"], mat["rgb"])
            mat = None
        elif tag == "instance" and cons_id is not None:
            offset = np.array([number(el.find("{*}" + k)) for k in ("deltax", "deltay", "deltaz")])
            angles = [number(el.find("{*}" + k)) for k in ("rx", "ry", "rz")]
            constellations[cons_id].append((el.get("objectid"), offset, rotation(*angles)))
        elif tag == "constellation":
            cons_id = None
    return unit, objects, materials, constellations


def emit(data, key, R, t, scale, collector, names, depth=0):
    objects, materials, constellations = data
    if depth > MAX_DEPTH:
        return
    if key in objects:
        for material_id, colour, V, F in objects[key]:
            name, rgb = materials.get(material_id, (None, None))
            rgb = colour or rgb
            if rgb is None:
                name = DEFAULT_MATERIAL
            else:
                name = name or colour_name(*rgb)
                if names.setdefault(name, rgb) != rgb:
                    name = colour_name(*rgb)
            valid = (F >= 0).all(1) & (F < len(V)).all(1)
            faces = F[valid][:, ::-1] if np.linalg.det(R) < 0 else F[valid]
            collector.add(name, (V @ R.T + t) * scale, faces, False, rgb)
    elif key in constellations:
        for sub, delta, rot in constellations[key]:
            emit(data, sub, R @ rot, R @ delta + t, scale, collector, names, depth + 1)


def run(src, outdir):
    t0 = time.time()
    print("PROGRESS 5 Reading AMF", flush=True)
    try:
        unit, objects, materials, constellations = parse(open_amf(src))
    except ET.ParseError as ex:
        raise ReadError("This AMF file is not valid XML: %s" % ex) from None
    scale = UNITS.get(unit.lower(), 1e-3)
    used = {sub for items in constellations.values() for sub, _, _ in items}
    roots = [k for k in constellations if k not in used] or list(objects)
    collector, names = MeshCollector(), {}
    print("PROGRESS 40 Collecting geometry", flush=True)
    for key in roots:
        emit((objects, materials, constellations), key, np.eye(3), np.zeros(3), scale, collector, names)
    if not len(collector):
        raise ReadError("No triangles found in this AMF file")
    collector.save(outdir)
    print("amf: %d triangles in %d meshes [%.0fs]" % (collector.triangles, len(collector), time.time() - t0),
          flush=True)


if __name__ == "__main__":
    try:
        run(sys.argv[1], sys.argv[2])
    except ReadError as ex:
        print(ex, file=sys.stderr, flush=True)
        sys.exit(1)
