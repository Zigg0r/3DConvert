"""Format scanners for the 'Imported file information' panel. Each returns a list of (label, value) rows.
Pure Python (no numpy); only 3DM, SketchUp, USD, Alembic and MAX need a short Blender background run."""
import json
import io
import os
import re
import shutil
import struct
import tempfile
import zipfile
import zlib
import xml.etree.ElementTree as ET
from array import array
from collections import Counter

from formats import SUPPORTED
from obj_reader import ObjInfo, read_obj_info
from pipeline import ConvertError, find_oda_converter, scan_in_blender

MAX_BBOX_POINTS = 3_000_000  # skip dimensions on huge meshes to keep the scan quick


def format_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def num(n):
    return f"{n:,}"


def dims_text(lo, hi):
    return " x ".join(f"{h - l:.3g}" for l, h in zip(lo, hi))


class Box:
    """Running bounding box."""

    def __init__(self):
        self.lo = [float("inf")] * 3
        self.hi = [float("-inf")] * 3

    def add_flat(self, flat):
        """flat: array('f'/'d') of x,y,z,x,y,z..."""
        if len(flat) < 3:
            return
        for i in range(3):
            axis = flat[i::3]
            self.lo[i] = min(self.lo[i], min(axis))
            self.hi[i] = max(self.hi[i], max(axis))

    def row(self, label="Dimensions (X/Y/Z)"):
        if self.lo[0] == float("inf"):
            return []
        return [(label, dims_text(self.lo, self.hi))]


def base_rows(path, ext):
    return [("Detected format", SUPPORTED.get(ext, ext)), ("File size", format_size(os.path.getsize(path)))]


# ---------------------------------------------------------------- OBJ
def obj_rows(i: ObjInfo):
    rows = [
        ("Detected format", i.format_name),
        ("File size", format_size(i.file_size)),
        ("Vertices", num(i.vertices)),
        ("Polygons (faces)", num(i.faces)),
        ("Triangles", num(i.triangles)),
        ("Normals", num(i.normals)),
        ("UV coords", num(i.uvs)),
        ("Objects", num(i.objects)),
        ("Groups", num(i.groups)),
        ("Materials", f"{len(i.materials)}" + (f" ({', '.join(i.mtl_files)})" if i.mtl_files else "")),
    ]
    if i.bbox_min and i.bbox_max:
        rows.append(("Dimensions (X/Y/Z)", dims_text(i.bbox_min, i.bbox_max)))
    return rows


# ---------------------------------------------------------------- STL
def scan_stl(path, progress):
    size = os.path.getsize(path)
    box = Box()
    with open(path, "rb") as f:
        head = f.read(84)
        count = struct.unpack("<I", head[80:84])[0] if len(head) == 84 else 0
        if size == 84 + 50 * count:  # binary
            kind, tris = "binary", count
            if count <= MAX_BBOX_POINTS // 3:
                pts = array("f")
                left = count
                while left:
                    n = min(left, 20000)
                    for rec in struct.iter_unpack("<12fH", f.read(n * 50)):
                        pts.extend(rec[3:12])
                    left -= n
                    progress(int(100 * (count - left) / count))
                box.add_flat(pts)
        else:  # ASCII
            kind, tris = "ASCII", 0
            f.seek(0)
            pts = array("f")
            read = 0
            for raw in f:
                read += len(raw)
                line = raw.strip()
                if line.startswith(b"facet"):
                    tris += 1
                    if tris % 50000 == 0:
                        progress(min(99, 100 * read // max(size, 1)))
                elif line.startswith(b"vertex"):
                    try:
                        pts.extend(float(x) for x in line.split()[1:4])
                    except ValueError:
                        pass
            if len(pts) <= MAX_BBOX_POINTS:
                box.add_flat(pts)
    rows = base_rows(path, ".stl") + [("Encoding", kind), ("Triangles", num(tris))]
    return rows + box.row()


# ---------------------------------------------------------------- PLY
def scan_ply(path, progress):
    fmt, elements, comments = "?", [], []
    with open(path, "rb") as f:
        if f.readline().strip() != b"ply":
            raise ValueError("Not a PLY file")
        for raw in f:
            line = raw.decode("latin-1").strip()
            if line == "end_header":
                break
            parts = line.split()
            if not parts:
                continue
            if parts[0] == "format":
                fmt = parts[1]
            elif parts[0] == "element":
                elements.append([parts[1], int(parts[2]), []])
            elif parts[0] == "property" and elements:
                elements[-1][2].append(parts[-1])
            elif parts[0] == "comment":
                comments.append(line[8:])
    el = {e[0]: e for e in elements}
    props = set(el.get("vertex", [0, 0, []])[2])
    rows = base_rows(path, ".ply") + [("Encoding", fmt.replace("_", " "))]
    rows.append(("Vertices", num(el.get("vertex", [0, 0])[1])))
    rows.append(("Faces", num(el.get("face", [0, 0])[1])))
    if "edge" in el:
        rows.append(("Edges", num(el["edge"][1])))
    rows.append(("Normals", "yes" if {"nx", "ny", "nz"} <= props else "no"))
    rows.append(("Vertex colours", "yes" if {"red", "green", "blue"} <= props else "no"))
    rows.append(("UV coords", "yes" if props & {"s", "t", "u", "v", "texture_u", "texture_v"} else "no"))
    if comments:
        rows.append(("Comment", comments[0][:80]))
    return rows


# ---------------------------------------------------------------- glTF / GLB
def scan_gltf(path, progress):
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as f:
        if ext == ".glb":
            magic, version, _ = struct.unpack("<4sII", f.read(12))
            if magic != b"glTF":
                raise ValueError("Not a GLB file")
            length, _ = struct.unpack("<II", f.read(8))
            doc = json.loads(f.read(length).decode("utf-8"))
        else:
            doc = json.load(f)
    acc = doc.get("accessors", [])
    verts = tris = prims = 0
    box = Box()
    for mesh in doc.get("meshes", []):
        for prim in mesh.get("primitives", []):
            prims += 1
            pos = prim.get("attributes", {}).get("POSITION")
            if pos is None:
                continue
            a = acc[pos]
            verts += a["count"]
            n = acc[prim["indices"]]["count"] if "indices" in prim else a["count"]
            mode = prim.get("mode", 4)
            tris += n // 3 if mode == 4 else max(n - 2, 0) if mode in (5, 6) else 0
            if "min" in a and "max" in a:
                for i in range(3):
                    box.lo[i] = min(box.lo[i], a["min"][i])
                    box.hi[i] = max(box.hi[i], a["max"][i])
    asset = doc.get("asset", {})
    rows = base_rows(path, ext) + [
        ("glTF version", str(asset.get("version", "?"))),
        ("Generator", asset.get("generator", "unknown")[:60]),
        ("Scenes / nodes", f"{len(doc.get('scenes', []))} / {len(doc.get('nodes', []))}"),
        ("Meshes (primitives)", f"{len(doc.get('meshes', []))} ({prims})"),
        ("Vertices", num(verts)),
        ("Triangles", num(tris)),
        ("Materials", num(len(doc.get("materials", [])))),
        ("Textures / images", f"{len(doc.get('textures', []))} / {len(doc.get('images', []))}"),
        ("Animations", num(len(doc.get("animations", [])))),
        ("Skins / cameras", f"{len(doc.get('skins', []))} / {len(doc.get('cameras', []))}"),
    ]
    return rows + box.row("Dimensions (mesh data)")


# ---------------------------------------------------------------- FBX
def _fbx_array(f, start):
    f.seek(start)
    kind = f.read(1).decode("latin-1")
    length, enc, clen = struct.unpack("<III", f.read(12))
    size = {"d": 8, "i": 4, "l": 8, "f": 4}[kind]
    data = f.read(clen if enc else length * size)
    if enc:
        data = zlib.decompress(data)
    arr = array({"d": "d", "i": "i", "l": "q", "f": "f"}[kind])
    arr.frombytes(data)
    return arr


def scan_fbx(path, progress):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        head = f.read(27)
        if not head.startswith(b"Kaydara FBX Binary"):
            return scan_fbx_ascii(path, progress)
        version = struct.unpack("<I", head[23:27])[0]
        wide = version >= 7500
        hdr_fmt, hdr_len = ("<QQQB", 25) if wide else ("<IIIB", 13)
        counts = Counter()
        geo = dict(verts=0, polys=0, tris=0)
        box = Box()
        creator = [""]

        def walk(end_limit, depth, parent):
            while f.tell() < end_limit:
                raw = f.read(hdr_len)
                if len(raw) < hdr_len:
                    return
                end, _, plen, nlen = struct.unpack(hdr_fmt, raw)
                if end == 0:
                    return
                name = f.read(nlen).decode("latin-1")
                props = f.tell()
                children = props + plen
                if depth == 0 and name == "Creator":
                    f.seek(props)
                    if f.read(1) == b"S":
                        creator[0] = f.read(struct.unpack("<I", f.read(4))[0]).decode("latin-1")
                if depth == 1 and parent == "Objects":
                    counts[name] += 1
                    if name == "Geometry" and end > children:
                        f.seek(children)
                        walk(end, depth + 1, "Geometry")
                elif parent == "Geometry":
                    if name == "Vertices":
                        arr = _fbx_array(f, props)
                        geo["verts"] += len(arr) // 3
                        if len(arr) <= MAX_BBOX_POINTS:
                            box.add_flat(arr)
                    elif name == "PolygonVertexIndex":
                        idx = _fbx_array(f, props)
                        run = 0
                        for v in idx:
                            run += 1
                            if v < 0:
                                geo["polys"] += 1
                                geo["tris"] += run - 2
                                run = 0
                elif depth == 0 and name == "Objects":
                    if end > children:
                        f.seek(children)
                        walk(end, depth + 1, name)
                f.seek(end)
                if depth == 0:
                    progress(min(99, int(100 * end / size)))

        walk(size, 0, None)
    rows = base_rows(path, ".fbx") + [
        ("FBX version", f"{version / 1000:.1f} binary"),
        ("Created by", creator[0] or "unknown"),
        ("Models (objects)", num(counts["Model"])),
        ("Geometries (meshes)", num(counts["Geometry"])),
        ("Vertices", num(geo["verts"])),
        ("Polygons", num(geo["polys"])),
        ("Triangles", num(geo["tris"])),
        ("Materials", num(counts["Material"])),
        ("Textures", num(counts["Texture"])),
        ("Animation stacks", num(counts["AnimationStack"])),
    ]
    return rows + box.row("Dimensions (mesh data)")


def scan_fbx_ascii(path, progress):
    counts = Counter()
    creator = ""
    with open(path, "rb") as f:
        for raw in f:
            line = raw.strip()
            for key in (b"Model:", b"Geometry:", b"Material:", b"Texture:"):
                if line.startswith(key):
                    counts[key[:-1].decode()] += 1
            if line.startswith(b"Creator:") and not creator:
                creator = line.split(b'"')[1].decode("latin-1") if b'"' in line else ""
    return base_rows(path, ".fbx") + [
        ("FBX type", "ASCII (limited scan)"),
        ("Created by", creator or "unknown"),
        ("Models (objects)", num(counts["Model"])),
        ("Geometries (meshes)", num(counts["Geometry"])),
        ("Materials", num(counts["Material"])),
        ("Textures", num(counts["Texture"])),
    ]


# ---------------------------------------------------------------- 3DS
def scan_3ds(path, progress):
    with open(path, "rb") as f:
        data = f.read()

    def chunks(start, end):
        pos = start
        while pos + 6 <= end:
            cid, length = struct.unpack_from("<HI", data, pos)
            if length < 6:
                return
            yield cid, pos + 6, min(pos + length, end)
            pos += length

    root = next(((s, e) for c, s, e in chunks(0, len(data)) if c == 0x4D4D), None)
    editor = next(((s, e) for c, s, e in chunks(*root) if c == 0x3D3D), None) if root else None
    if editor is None:
        raise ValueError("Not a valid 3DS file")
    meshes = verts = faces = mats = 0
    box = Box()
    for cid, s, e in chunks(*editor):
        if cid == 0xAFFF:
            mats += 1
        elif cid == 0x4000:
            p = data.index(b"\0", s) + 1
            for c2, s2, e2 in chunks(p, e):
                if c2 != 0x4100:
                    continue
                meshes += 1
                for c3, s3, e3 in chunks(s2, e2):
                    n = struct.unpack_from("<H", data, s3)[0] if e3 - s3 >= 2 else 0
                    if c3 == 0x4110:
                        verts += n
                        pts = array("f")
                        pts.frombytes(data[s3 + 2:s3 + 2 + n * 12])
                        box.add_flat(pts)
                    elif c3 == 0x4120:
                        faces += n
    rows = base_rows(path, ".3ds") + [
        ("Objects (meshes)", num(meshes)),
        ("Vertices", num(verts)),
        ("Triangles", num(faces)),
        ("Materials", num(mats)),
    ]
    return rows + box.row()


# ---------------------------------------------------------------- COLLADA
def scan_dae(path, progress):
    size = os.path.getsize(path)
    counts = Counter()
    unit = up = tool = None
    geoms = faces = verts = 0
    box = Box()
    stack = []
    with open(path, "rb") as f:
        for ev, el in ET.iterparse(f, events=("start", "end")):
            tag = el.tag.rsplit("}", 1)[-1]
            if ev == "start":
                stack.append(tag)
                continue
            stack.pop()
            if tag == "unit":
                unit = el.get("meter")
            elif tag == "up_axis":
                up = el.text
            elif tag == "authoring_tool":
                tool = el.text
            elif tag in ("material", "image", "effect", "camera", "light"):
                if stack and stack[-1].startswith("library"):
                    counts[tag] += 1
            elif tag in ("node",):
                counts["node"] += 1
            elif tag == "instance_geometry":
                counts["instance_geometry"] += 1
            elif tag == "geometry":
                geoms += 1
                for prim in el.iter():
                    ptag = prim.tag.rsplit("}", 1)[-1]
                    if ptag in ("triangles", "polylist", "polygons"):
                        faces += int(prim.get("count") or 0)
                mesh = el.find("{*}mesh")
                inp = mesh.find("{*}vertices/{*}input") if mesh is not None else None
                if inp is not None:
                    src = inp.get("source", "")[1:]
                    for s in mesh.findall("{*}source"):
                        if s.get("id") == src:
                            fa = s.find("{*}float_array")
                            if fa is not None and fa.text:
                                n = int(fa.get("count") or 0)
                                verts += n // 3
                                if n <= MAX_BBOX_POINTS:
                                    box.add_flat(array("f", (float(x) for x in fa.text.split())))
                el.clear()
            if len(stack) <= 2:
                el.clear()
                progress(min(99, int(100 * f.tell() / max(size, 1))))
    rows = base_rows(path, ".dae") + [
        ("Created by", tool or "unknown"),
        ("Unit (m)", unit or "1"),
        ("Up axis", up or "Y_UP"),
        ("Geometries", num(geoms)),
        ("Vertices", num(verts)),
        ("Faces", num(faces)),
        ("Nodes", num(counts["node"])),
        ("Geometry instances", num(counts["instance_geometry"])),
        ("Materials", num(counts["material"])),
        ("Images", num(counts["image"])),
    ]
    return rows + box.row("Dimensions (mesh data)")


# ---------------------------------------------------------------- IFC
IFC_ELEMENTS = {
    "IFCWALL", "IFCWALLSTANDARDCASE", "IFCSLAB", "IFCBEAM", "IFCCOLUMN", "IFCWINDOW", "IFCDOOR", "IFCROOF",
    "IFCSTAIR", "IFCSTAIRFLIGHT", "IFCRAMP", "IFCRAMPFLIGHT", "IFCRAILING", "IFCCOVERING", "IFCPLATE", "IFCMEMBER",
    "IFCCURTAINWALL", "IFCFOOTING", "IFCPILE", "IFCBUILDINGELEMENTPROXY", "IFCFURNISHINGELEMENT",
    "IFCFLOWTERMINAL", "IFCFLOWSEGMENT", "IFCFLOWFITTING", "IFCDISTRIBUTIONELEMENT", "IFCOPENINGELEMENT",
    "IFCTRANSPORTELEMENT", "IFCGEOGRAPHICELEMENT", "IFCCIVILELEMENT", "IFCBUILDINGELEMENTPART", "IFCSANITARYTERMINAL",
}
# Entity types that hold tessellated geometry; keep in sync with MESH_TYPES in engine/ifc_text.py, which decides
# how the file is read when it is converted.
IFC_MESH_TYPES = {"IFCINDEXEDPOLYGONALFACE", "IFCINDEXEDPOLYGONALFACEWITHVOIDS", "IFCCARTESIANPOINTLIST3D",
                  "IFCPOLYGONALFACESET", "IFCTRIANGULATEDFACESET", "IFCINDEXEDCOLOURMAP", "IFCCOLOURRGBLIST"}
IFC_HEAD = re.compile(rb"^#\d+\s*=\s*([A-Z0-9_]+)\s*\(")


def scan_ifc(path, progress):
    size = os.path.getsize(path)
    counts = Counter()
    mesh_bytes = total = read = 0
    schema = origin = project = ""
    last_mesh = False
    with open(path, "rb") as f:
        for i, raw in enumerate(f):
            read += len(raw)
            if i < 60:
                m = re.search(rb"FILE_SCHEMA\s*\(\s*\(\s*'([^']*)'", raw)
                if m:
                    schema = m.group(1).decode("latin-1")
                if raw.startswith(b"FILE_NAME"):
                    fields = re.findall(rb"'([^']*)'", raw)
                    origin = fields[-1].decode("latin-1") if fields else ""
            m = IFC_HEAD.match(raw)
            if m:
                typ = m.group(1).decode()
                counts[typ] += 1
                last_mesh = typ in IFC_MESH_TYPES
                if typ == "IFCPROJECT" and not project:
                    q = re.findall(rb"'([^']*)'", raw)
                    project = q[1].decode("latin-1") if len(q) > 1 else ""
            if last_mesh:
                mesh_bytes += len(raw)
            total += len(raw)
            if i % 200000 == 0:
                progress(min(99, int(100 * read / max(size, 1))))
    elements = Counter({k: v for k, v in counts.items() if k in IFC_ELEMENTS})
    top = ", ".join(f"{k[3:].title()} {v}" for k, v in elements.most_common(4))
    share = 100 * mesh_bytes / max(total, 1)
    rows = base_rows(path, ".ifc") + [
        ("IFC schema", schema or "?"),
        ("Exported by", origin or "unknown"),
        ("Project", project or "-"),
        ("Entities", num(sum(counts.values()))),
        ("Buildings / storeys", f"{counts['IFCBUILDING']} / {counts['IFCBUILDINGSTOREY']}"),
        ("Building elements", num(sum(elements.values()))),
        ("Most common", top or "-"),
        ("Spaces", num(counts["IFCSPACE"])),
        ("Materials", num(counts["IFCMATERIAL"])),
        ("Geometry type", "mesh-based (Rhino/Revit style)" if share > 50 else "parametric (needs tessellation)"),
    ]
    return rows


# ---------------------------------------------------------------- STEP
STEP_HEAD = re.compile(rb"^#\d+\s*=\s*([A-Z0-9_]+)\s*\(")
STEP_UNIT_PREFIX = {b".MILLI.": "mm", b".CENTI.": "cm", b".KILO.": "km"}


def scan_step(path, progress):
    size = os.path.getsize(path)
    counts = Counter()
    schema = origin = unit = ""
    read = 0
    with open(path, "rb") as f:
        for i, raw in enumerate(f):
            read += len(raw)
            if i < 80:
                m = re.search(rb"FILE_SCHEMA\s*\(\s*\(\s*'([^']*)'", raw)
                if m and not schema:
                    schema = m.group(1).decode("latin-1").split("{")[0].strip()
                if raw.lstrip().startswith(b"FILE_NAME"):
                    fields = re.findall(rb"'([^']*)'", raw)
                    if len(fields) >= 6:
                        origin = fields[5].decode("latin-1")
            m = STEP_HEAD.match(raw)
            if m:
                counts[m.group(1).decode()] += 1
            if not unit and b"LENGTH_UNIT" in raw:
                if b"METRE" in raw:
                    unit = next((v for k, v in STEP_UNIT_PREFIX.items() if k in raw), "m")
                elif b"INCH" in raw:
                    unit = "inch"
            if i % 200000 == 0:
                progress(min(99, int(100 * read / max(size, 1))))
    solids = counts["MANIFOLD_SOLID_BREP"] + counts["BREP_WITH_VOIDS"]
    return base_rows(path, ".stp") + [
        ("STEP schema", schema or "?"),
        ("Exported by", origin or "unknown"),
        ("Length unit", unit or "?"),
        ("Products (parts)", num(counts["PRODUCT"])),
        ("Assembly links", num(counts["NEXT_ASSEMBLY_USAGE_OCCURRENCE"])),
        ("Solids", num(solids)),
        ("Faces", num(counts["ADVANCED_FACE"])),
        ("Colours", num(counts["COLOUR_RGB"])),
        ("Entities", num(sum(counts.values()))),
    ]


# ---------------------------------------------------------------- IGES
IGES_UNITS = {1: "inch", 2: "mm", 4: "feet", 5: "miles", 6: "m", 7: "km", 8: "mil", 9: "micron", 10: "cm",
              11: "microinch"}
IGES_CURVES = {100, 102, 104, 106, 110, 112, 126}
IGES_SURFACES = {114, 118, 120, 122, 128, 140, 190, 192, 194, 196, 198}
IGES_TRIMMED = {143, 144, 510}  # bounded / trimmed surfaces and faces


def iges_globals(text):
    """Global section fields from the 3rd on (product id, file name, system id, ...). Strings are 'nHtext'; the
    delimiters are ',' and ';' unless the first two fields declare others."""
    pd, rd, pos = ",", ";", 0
    if text.startswith("1H"):
        pd, pos = text[2:3] or ",", 4
    elif text.startswith(","):
        pos = 1
    if text.startswith(pd, pos):
        pos += 1
    elif text.startswith("1H", pos):
        rd, pos = text[pos + 2:pos + 3] or ";", pos + 4
    fields = []
    while pos < len(text):
        m = re.match(r"(\d+)H", text[pos:pos + 12])
        if m:
            start = pos + m.end()
            fields.append(text[start:start + int(m.group(1))])
            pos = start + int(m.group(1))
        else:
            end = pos
            while end < len(text) and text[end] not in (pd, rd):
                end += 1
            fields.append(text[pos:end])
            pos = end
        if pos >= len(text) or text[pos] == rd:
            break
        pos += 1
    return fields


def scan_iges(path, progress):
    """Fixed 80-column file: the section letter sits in column 73 (S, G, D, P, T). Only G and D are read."""
    size = os.path.getsize(path)
    glob_text, counts, dir_lines, read = [], Counter(), 0, 0
    with open(path, "rb") as f:
        for i, raw in enumerate(f):
            read += len(raw)
            section = raw[72:73]
            if section == b"G":
                glob_text.append(raw[:72].decode("latin-1"))
            elif section == b"D":
                if dir_lines % 2 == 0:  # each entity has two directory lines; the type is on the first
                    kind = raw[:8].strip()
                    if kind.isdigit():
                        counts[int(kind)] += 1
                dir_lines += 1
            elif section == b"P":
                break
            if i % 200000 == 0:
                progress(min(99, int(100 * read / max(size, 1))))
    fields = iges_globals("".join(glob_text))

    def field(n):
        return fields[n].strip() if n < len(fields) else ""

    flag = field(11)
    unit = IGES_UNITS.get(int(flag)) if flag.isdigit() else None
    count = lambda kinds: sum(counts[k] for k in kinds)
    return base_rows(path, ".igs") + [
        ("Product", field(0) or "?"),
        ("Exported by", field(2) or "unknown"),
        ("Length unit", unit or field(12) or "?"),
        ("Trimmed surfaces", num(count(IGES_TRIMMED))),
        ("Surfaces", num(count(IGES_SURFACES))),
        ("Solids (B-rep)", num(counts[186])),
        ("Curves", num(count(IGES_CURVES))),
        ("Entities", num(sum(counts.values()))),
    ]


# ---------------------------------------------------------------- DXF / DWG
ACAD_VERSIONS = {"AC1006": "R10", "AC1009": "R11/R12", "AC1012": "R13", "AC1014": "R14", "AC1015": "2000",
                 "AC1018": "2004", "AC1021": "2007", "AC1024": "2010", "AC1027": "2013", "AC1032": "2018+"}
DXF_UNITS = {0: "unitless", 1: "inch", 2: "foot", 3: "mile", 4: "mm", 5: "cm", 6: "m", 7: "km", 14: "dm"}
DXF_FACE_TYPES = {"3DFACE", "MESH", "POLYLINE", "SOLID"}


def scan_dxf(path, progress):
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        if f.read(22).startswith(b"AutoCAD Binary DXF"):
            return base_rows(path, ".dxf") + [("Encoding", "binary DXF (limited scan)")]
    counts = Counter()
    layers = blocks = 0
    version = units = ""
    section = var = None
    lines = 0
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        while True:
            code = f.readline()
            value = f.readline()
            if not value:
                break
            lines += 2
            code, value = code.strip(), value.strip()
            if code == "0":
                if value == "SECTION":
                    section = "?"
                elif value == "ENDSEC":
                    section = None
                elif section == "TABLES" and value == "LAYER":
                    layers += 1
                elif section == "BLOCKS" and value == "BLOCK":
                    blocks += 1
                elif section == "ENTITIES":
                    counts[value] += 1
            elif code == "2" and section == "?":
                section = value
            elif section == "HEADER":
                if code == "9":
                    var = value
                elif var == "$ACADVER" and code == "1":
                    version = ACAD_VERSIONS.get(value, value)
                elif var == "$INSUNITS" and code == "70":
                    units = DXF_UNITS.get(int(value) if value.lstrip("-").isdigit() else -1, value)
            if lines % 200000 == 0:
                progress(min(99, int(100 * f.tell() / max(size, 1))))
    top = ", ".join(f"{k.title()} {v}" for k, v in counts.most_common(4))
    faces = sum(v for k, v in counts.items() if k in DXF_FACE_TYPES)
    return base_rows(path, ".dxf") + [
        ("AutoCAD version", version or "?"),
        ("Drawing units", units or "?"),
        ("Layers", num(layers)),
        ("Blocks", num(blocks)),
        ("Entities", num(sum(counts.values()))),
        ("Most common", top or "-"),
        ("Face-type entities", num(faces)),
        ("ACIS solids", num(counts["3DSOLID"] + counts["BODY"])),
    ]


def scan_dwg(path, progress):
    with open(path, "rb") as f:
        tag = f.read(6).decode("latin-1")

    return base_rows(path, ".dwg") + [
        ("AutoCAD version", ACAD_VERSIONS.get(tag, tag)),
        ("ODA File Converter", "found" if find_oda_converter() else "not installed (needed to convert DWG)"),
    ]


# ---------------------------------------------------------------- VRML
def scan_wrl(path, progress):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        import gzip

        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", errors="replace")
    header = text.lstrip("\ufeff").split("\n", 1)[0].strip()
    count = lambda word: len(re.findall(r"\b%s\s*\{" % word, text))  # noqa: E731
    rows = base_rows(path, ".wrl") + [("VRML header", header[:40] or "?")]
    if header.startswith("#VRML V1.0"):
        return rows + [("Note", "VRML 1.0 is not supported, only VRML 2.0 / VRML97")]
    return rows + [
        ("Shapes", num(count("Shape"))),
        ("Face sets", num(count("IndexedFaceSet"))),
        ("Boxes", num(count("Box"))),
        ("Transforms", num(count("Transform"))),
        ("Materials", num(count("Material"))),
        ("Not converted", num(sum(count(w) for w in ("Sphere", "Cylinder", "Cone", "Extrusion", "ElevationGrid")))),
    ]


# ---------------------------------------------------------------- 3MF / AMF / X3D / IFC containers
def xml_counts(stream, wanted, clear):
    """Counts the wanted elements of an XML stream without keeping it in memory; also returns the root attributes."""
    counts, root = Counter(), {}
    for event, el in ET.iterparse(stream, events=("start", "end")):
        tag = el.tag.rsplit("}", 1)[-1]
        if event == "start":
            if not root:
                root = dict(el.attrib)
            continue
        if tag in wanted:
            counts[tag] += 1
        if tag in clear:
            el.clear()
    return counts, root


def scan_3mf(path, progress):
    counts, unit, parts = Counter(), "", 0
    try:
        with zipfile.ZipFile(path) as z:
            models = [n for n in z.namelist() if n.lower().endswith(".model")]
            for i, name in enumerate(models):
                with z.open(name) as f:
                    found, root = xml_counts(f, ("vertex", "triangle", "object", "item", "component", "base"),
                                             ("vertex", "triangle", "item", "component", "base"))
                counts.update(found)
                unit = unit or root.get("unit", "millimeter")
                parts += 1
                progress(min(99, 100 * (i + 1) // len(models)))
    except zipfile.BadZipFile:
        raise ValueError("This is not a valid 3MF (zip) file") from None
    return base_rows(path, ".3mf") + [
        ("Unit", unit or "millimeter"),
        ("Model parts", num(parts)),
        ("Objects", num(counts["object"])),
        ("Build items", num(counts["item"])),
        ("Components", num(counts["component"])),
        ("Vertices", num(counts["vertex"])),
        ("Triangles", num(counts["triangle"])),
        ("Base materials", num(counts["base"])),
    ]


def scan_amf(path, progress):
    with open(path, "rb") as f:
        raw = f.read()
    zipped = raw[:2] == b"PK"
    if zipped:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            name = next((n for n in z.namelist() if n.lower().endswith(".amf")), None)
            if name is None:
                raise ValueError("No .amf file found inside this zip")
            raw = z.read(name)
    counts, root = xml_counts(io.BytesIO(raw), ("vertex", "triangle", "object", "volume", "material", "instance"),
                              ("vertex", "triangle"))
    return base_rows(path, ".amf") + [
        ("Encoding", "zipped XML" if zipped else "XML"),
        ("Unit", root.get("unit", "millimeter")),
        ("Objects", num(counts["object"])),
        ("Volumes", num(counts["volume"])),
        ("Vertices", num(counts["vertex"])),
        ("Triangles", num(counts["triangle"])),
        ("Materials", num(counts["material"])),
        ("Instances", num(counts["instance"])),
    ]


def scan_x3d(path, progress):
    names = ("Shape", "IndexedFaceSet", "IndexedTriangleSet", "TriangleSet", "Box", "Transform", "Material",
             "Sphere", "Cylinder", "Cone", "Extrusion", "ElevationGrid")
    counts, root = xml_counts(path, names, ())
    skipped = sum(counts[n] for n in ("Sphere", "Cylinder", "Cone", "Extrusion", "ElevationGrid"))
    return base_rows(path, ".x3d") + [
        ("X3D version", root.get("version", "?")),
        ("Shapes", num(counts["Shape"])),
        ("Face sets", num(counts["IndexedFaceSet"])),
        ("Triangle sets", num(counts["IndexedTriangleSet"] + counts["TriangleSet"])),
        ("Boxes", num(counts["Box"])),
        ("Transforms", num(counts["Transform"])),
        ("Materials", num(counts["Material"])),
        ("Not converted", num(skipped)),
    ]


def scan_ifczip(path, progress):
    with zipfile.ZipFile(path) as z:
        name = next((n for n in z.namelist() if n.lower().endswith(".ifc")), None)
        if name is None:
            raise ValueError("No .ifc file found inside this zip")
        with tempfile.TemporaryDirectory(prefix="3dconvert_scan_") as tmp:
            inner = os.path.join(tmp, "inner.ifc")
            with z.open(name) as a, open(inner, "wb") as b:
                shutil.copyfileobj(a, b, 1 << 20)
            rows = scan_ifc(inner, progress)
    return base_rows(path, ".ifczip") + rows[2:]


def scan_ifcxml(path, progress):
    return base_rows(path, ".ifcxml") + [("Note", "Converted to IFC text with IfcOpenShell before it is read")]


# ---------------------------------------------------------------- via Blender (3DM, SketchUp, USD, Alembic, MAX)
def scan_with_blender(path, ext, progress):
    progress(20)
    try:
        info = scan_in_blender(path, ext)
    except ConvertError as ex:
        return base_rows(path, ext) + [("Detailed scan", str(ex).splitlines()[0][:90])]
    progress(100)
    rows = base_rows(path, ext)
    for label, value in info.get("rows", []):
        rows.append((label, str(value)))
    return rows


def describe_file(path, progress):
    """Returns (label, value) rows for the info panel."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".obj":
        return obj_rows(read_obj_info(path, progress))
    scanners = {".stl": scan_stl, ".ply": scan_ply, ".gltf": scan_gltf, ".glb": scan_gltf, ".fbx": scan_fbx,
                ".3ds": scan_3ds, ".dae": scan_dae, ".ifc": scan_ifc, ".stp": scan_step, ".step": scan_step,
                ".igs": scan_iges, ".iges": scan_iges,
                ".dxf": scan_dxf, ".dwg": scan_dwg, ".wrl": scan_wrl, ".x3dv": scan_wrl, ".x3d": scan_x3d,
                ".3mf": scan_3mf, ".amf": scan_amf, ".ifczip": scan_ifczip, ".ifcxml": scan_ifcxml}
    if ext in scanners:
        rows = scanners[ext](path, progress)
        progress(100)
        return rows
    if ext in (".3dm", ".skp", ".usd", ".usda", ".usdc", ".usdz", ".abc", ".max", ".blend"):
        return scan_with_blender(path, ext, progress)
    progress(100)
    return base_rows(path, ext)
