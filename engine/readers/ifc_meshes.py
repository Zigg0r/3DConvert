"""IFC whose geometry is already a mesh (IfcPolygonalFaceSet, IfcTriangulatedFaceSet; written by Rhino, Revit and
similar exporters), read directly as text without IfcOpenShell. Memory stays small: points and faces go straight into
compact numpy arrays. Placement (IfcLocalPlacement chain, IfcMappedItem), units (mm/m) and the colour per face set
(IfcStyledItem -> IfcSurfaceStyle) are resolved. Polygons with more than four corners, and polygons with holes, are ear clipped.
Writes geometry.npz/json (see geometry_io.py). Usage: ifc_meshes.py <in.ifc> <outdir>"""
import os
import re
import sys
import time
from array import array

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geometry_io import DEFAULT_MATERIAL, MeshCollector  # noqa: E402
from ifc_text import MESH_TYPES, REF, entities  # noqa: E402
from polygons import triangulate_rings  # noqa: E402

NUM = re.compile(rb"[-+]?\d+\.?\d*(?:[Ee][-+]?\d+)?")
INT = re.compile(rb"\d+")
# the face set without normals: '#points,$,.T.,((' - the coordinate index list follows
TRIANGULATED_START = re.compile(rb"\s*#(\d+)\s*,\s*\$\s*,\s*(?:\.[TF]\.|\$)\s*,\s*\(\(")
LENGTH_PREFIX = {"MILLI": 1e-3, "CENTI": 1e-2, "DECI": 0.1, "KILO": 1e3}
NOT_ELEMENTS = {b"IFCGRID", b"IFCANNOTATION", b"IFCSPACE", b"IFCOPENINGELEMENT", b"IFCPROJECT", b"IFCSITE",
                b"IFCBUILDING", b"IFCBUILDINGSTOREY"}
ELEMENT = re.compile(rb"^#\d+\s*=\s*IFC[A-Z]+\(\s*'.{22}'")  # an entity that starts with a GlobalId
GREY = [0.8, 0.8, 0.8]


def parse_arguments(line):
    """Arguments of a (small) entity line -> nested lists; '#n' -> ('#', n), '$' and '*' -> None."""
    s = line[line.index(b"(") + 1: line.rstrip().rindex(b")")].decode("latin-1")
    i = 0

    def value():
        nonlocal i
        while s[i] == " ":
            i += 1
        c = s[i]
        if c == "(":
            i += 1
            out = []
            while True:
                while s[i] == " ":
                    i += 1
                if s[i] == ")":
                    i += 1
                    return out
                out.append(value())
                while s[i] == " ":
                    i += 1
                if s[i] == ",":
                    i += 1
        if c == "'":
            j = i + 1
            text = []
            while True:
                if s[j] == "'" and j + 1 < len(s) and s[j + 1] == "'":
                    text.append("'")
                    j += 2
                    continue
                if s[j] == "'":
                    break
                text.append(s[j])
                j += 1
            i = j + 1
            return "".join(text)
        if c == "#":
            m = re.match(r"#(\d+)", s[i:])
            i += m.end()
            return ("#", int(m.group(1)))
        if c in "$*":
            i += 1
            return None
        if c == ".":
            j = s.index(".", i + 1)
            enum = s[i + 1:j]
            i = j + 1
            return enum
        m = re.match(r"[A-Z0-9_]+\(", s[i:])
        if m:  # typed value, e.g. IFCLENGTHMEASURE(1.)
            i += m.end() - 1
            return value()
        m = re.match(r"[-+]?\d+\.?\d*(?:[Ee][-+]?\d+)?", s[i:])
        i += m.end()
        return float(m.group(0))

    out = []
    while i < len(s):
        out.append(value())
        while i < len(s) and s[i] in " ,":
            i += 1
    return out


def triangulated_face_set(line):
    """IFCTRIANGULATEDFACESET -> (point list id, (m, 3) 1-based triangles, point index or None)."""
    body = line[line.index(b"(") + 1:]
    m = TRIANGULATED_START.match(body)
    if m:  # no normals (the usual case): read the numbers directly, much faster than a full parse
        start = m.end() - 2
        end = body.index(b"))", start)
        rest = body[end + 2:]
        point_index = np.array(INT.findall(rest), np.int64) if b"(" in rest else None
        return int(m.group(1)), np.array(INT.findall(body[start:end + 2]), np.int64).reshape(-1, 3), point_index
    args = parse_arguments(line)
    point_index = np.array(args[4], dtype=np.int64) if len(args) > 4 and args[4] else None
    return args[0][1], np.array(args[3], dtype=np.int64).reshape(-1, 3), point_index


def ref(x):
    """Entity id of a parsed '#n' argument, else None."""
    return x[1] if isinstance(x, tuple) else None


def unescape(name):
    """IFC \\X2\\...\\X0\\ escapes -> text."""
    return re.sub(r"\\X2\\([0-9A-F]+)\\X0\\",
                  lambda m: "".join(chr(int(m.group(1)[k:k + 4], 16)) for k in range(0, len(m.group(1)), 4)), name)


class Reader:
    def __init__(self):
        self.points = {}                                 # point list id -> (n, 3) float64
        self.face_start = np.full(1 << 20, -1, np.int64)  # face id -> start in `corners`
        self.face_len = np.zeros(1 << 20, np.int32)       # face id -> number of corners
        self.corners = array("i")                        # all corner indices one after the other
        self.face_sets = {}                              # id -> (kind, point list id, faces, point index)
        self.other = {}                                  # all other entities: id -> (type, line)
        self.styled = {}                                 # item id -> style ids
        self.holes = {}                                  # face id -> inner rings
        self.parsed = {}                                 # id -> parsed arguments
        self.styles = {}                                 # surface style id -> (name, rgb)
        self.scale = 1.0
        self.ear_clipped = 0
        self.collector = MeshCollector()

    # ---- pass 1: one run through the file ----
    def read(self, path):
        n_faces = 0
        for eid, kind, line in entities(path):
            if kind == b"IFCCARTESIANPOINTLIST3D":
                self.points[eid] = np.array(NUM.findall(line[line.index(b"(") + 1:]), dtype=np.float64).reshape(-1, 3)
            elif kind in (b"IFCINDEXEDPOLYGONALFACE", b"IFCINDEXEDPOLYGONALFACEWITHVOIDS"):
                body = line[line.index(b"((") + 2:] if b"((" in line else line[line.index(b"(") + 1:]
                corners = [int(x) for x in body[:body.index(b")")].split(b",")]
                if eid >= len(self.face_start):  # grow the id-indexed tables, at least to fit this id
                    extra = max(len(self.face_start), eid + 1 - len(self.face_start))
                    self.face_start = np.concatenate([self.face_start, np.full(extra, -1, np.int64)])
                    self.face_len = np.concatenate([self.face_len, np.zeros(extra, np.int32)])
                self.face_start[eid] = len(self.corners)
                self.face_len[eid] = len(corners)
                self.corners.extend(corners)
                n_faces += 1
                if kind.endswith(b"VOIDS"):
                    rest = body[body.index(b")") + 1:]
                    self.holes[eid] = [np.array([int(x) for x in g.split(b",")], np.int64)
                                       for g in re.findall(rb"\(([\d,\s]+)\)", rest)]
            elif kind == b"IFCPOLYGONALFACESET":
                found = REF.findall(line[line.index(b"("):])
                point_index = None
                m = re.search(rb"\)\s*,\s*(\$|\([^#]*?\))\s*\)\s*;\s*$", line)
                if m and m.group(1) != b"$":
                    point_index = np.array(INT.findall(m.group(1)), dtype=np.int64)
                self.face_sets[eid] = ("poly", int(found[0]), np.array([int(x) for x in found[1:]], np.int64), point_index)
            elif kind == b"IFCTRIANGULATEDFACESET":
                coords, triangles, point_index = triangulated_face_set(line)
                self.face_sets[eid] = ("tri", coords, triangles, point_index)
            elif kind in MESH_TYPES:
                pass  # colour maps and colour lists: colours come from the styles
            else:
                if kind == b"IFCSTYLEDITEM":
                    found = [int(x) for x in REF.findall(line[line.index(b"("):])]
                    self.styled.setdefault(found[0], []).extend(found[1:])
                self.other[eid] = (kind, line)
        self.corners = np.array(self.corners, dtype=np.int64)
        print("pass 1: %d point lists, %d faces (%d with holes), %d face sets, %d other entities"
              % (len(self.points), n_faces, len(self.holes), len(self.face_sets), len(self.other)), flush=True)
        for eid, (kind, line) in self.other.items():
            if kind == b"IFCSIUNIT" and b".LENGTHUNIT." in line:
                self.scale = LENGTH_PREFIX.get(self.args(eid)[2], 1.0)

    # ---- entity access ----
    def args(self, eid):
        if eid not in self.parsed:
            self.parsed[eid] = parse_arguments(self.other[eid][1])
        return self.parsed[eid]

    def kind(self, eid):
        return self.other[eid][0].decode() if eid in self.other else ""

    def vector(self, point_id, default):
        return np.array(self.args(point_id)[0], float) if point_id else np.array(default, float)

    # ---- placement ----
    def axis_placement(self, eid):
        if eid is None:
            return np.eye(4)
        p = self.args(eid)
        m = np.eye(4)
        m[:3, 3] = np.array(self.args(ref(p[0]))[0], dtype=float) * self.scale
        if self.kind(eid) == "IFCAXIS2PLACEMENT3D":
            z = self.vector(ref(p[1]), [0, 0, 1.0])
            x = self.vector(ref(p[2]), [1.0, 0, 0])
            z /= np.linalg.norm(z)
            x = x - z * (x @ z)
            x /= np.linalg.norm(x)
            m[:3, 0], m[:3, 1], m[:3, 2] = x, np.cross(z, x), z
        return m

    def placement(self, eid):
        if eid is None:
            return np.eye(4)
        p = self.args(eid)
        return self.placement(ref(p[0])) @ self.axis_placement(ref(p[1]))

    def operator(self, eid):
        """IfcCartesianTransformationOperator3D: Axis1, Axis2, LocalOrigin, Scale, Axis3"""
        p = self.args(eid)
        x = self.vector(ref(p[0]), [1.0, 0, 0])
        z = self.vector(ref(p[4]) if len(p) > 4 else None, [0, 0, 1.0])
        y = self.vector(ref(p[1]), [0, 0, 0]) if p[1] else np.cross(z, x)
        s = p[3] if p[3] else 1.0
        m = np.eye(4)
        m[:3, 0], m[:3, 1], m[:3, 2] = x * s, y * s, z * s
        m[:3, 3] = np.array(self.args(ref(p[2]))[0], float) * self.scale
        return m

    # ---- style ----
    def surface_style(self, eid):
        if eid not in self.styles:
            args = self.args(eid)
            name = unescape(args[0] or "style%d" % eid)
            rgb = list(GREY)
            for r in args[2]:
                if self.kind(ref(r)).startswith("IFCSURFACESTYLE"):  # rendering or shading
                    q = self.args(ref(r))
                    if ref(q[0]) and self.kind(ref(q[0])) == "IFCCOLOURRGB":
                        rgb = list(self.args(ref(q[0]))[1:4])
            self.styles[eid] = (name, rgb)
        return self.styles[eid]

    def style_of(self, item):
        """(name, rgb) of a face set's surface style; the default material when it has none."""
        for start in self.styled.get(item, []):
            todo, seen = [start], set()
            while todo:
                e = todo.pop()
                if e in seen:
                    continue
                seen.add(e)
                if self.kind(e) == "IFCSURFACESTYLE":
                    return self.surface_style(e)
                if e in self.other:  # e.g. IfcPresentationStyleAssignment: look at what it refers to
                    todo += [int(v) for v in REF.findall(self.other[e][1])[1:]]
        return DEFAULT_MATERIAL, GREY

    # ---- geometry ----
    def triangulate(self, P, faces, point_index):
        """Faces (ids) -> triangles (0-based point indices). Triangles directly; quads along the diagonal that
        stays inside; larger faces and faces with holes by ear clipping (a fan from one corner gives huge
        triangles outside a concave face)."""
        def idx(q):
            return point_index[q - 1] - 1 if point_index is not None else q - 1
        start = self.face_start[faces]
        length = self.face_len[faces].astype(np.int64)
        has_holes = np.array([f in self.holes for f in faces.tolist()]) if self.holes else np.zeros(len(faces), bool)
        out = []
        b = start[(length == 3) & ~has_holes]
        out.append(idx(np.stack([self.corners[b], self.corners[b + 1], self.corners[b + 2]], 1)))
        b = start[(length == 4) & ~has_holes]
        quads = idx(np.stack([self.corners[b + k] for k in range(4)], 1))
        if len(quads):
            X = P[quads]
            n1 = np.cross(X[:, 1] - X[:, 0], X[:, 2] - X[:, 0])
            n2 = np.cross(X[:, 2] - X[:, 0], X[:, 3] - X[:, 0])
            first = (n1 * n2).sum(1) >= 0
            out.append(np.where(first[:, None], quads[:, [0, 1, 2]], quads[:, [0, 1, 3]]))
            out.append(np.where(first[:, None], quads[:, [0, 2, 3]], quads[:, [1, 2, 3]]))
        big = (length > 4) | has_holes
        for f, s0, n in zip(faces[big], start[big], length[big]):
            rings = [idx(self.corners[s0:s0 + n])] + [idx(h) for h in self.holes.get(int(f), [])]
            triangles = triangulate_rings([P[r] for r in rings])
            if len(triangles):
                out.append(np.concatenate(rings)[triangles])
            self.ear_clipped += 1
        return np.concatenate(out)

    def emit(self, item, matrix):
        """A face set -> triangles in world space."""
        kind, coords, faces, point_index = self.face_sets[item]
        P = self.points[coords] * self.scale
        if kind == "poly":
            F = self.triangulate(P, faces, point_index)
        else:
            F = faces - 1
            if point_index is not None:
                F = point_index[F] - 1
        name, rgb = self.style_of(item)
        self.collector.add(name, P @ matrix[:3, :3].T + matrix[:3, 3], F, False, rgb)

    def items_of(self, representation, matrix):
        p = self.args(representation)  # IfcShapeRepresentation: context, identifier, type, items
        if p[1] not in (None, "Body", "Facetation"):
            return
        for item in p[3]:
            i = ref(item)
            if i in self.face_sets:
                self.emit(i, matrix)
            elif self.kind(i) == "IFCMAPPEDITEM":
                mapped = self.args(i)  # source (IfcRepresentationMap: origin, representation), target (operator)
                rep_map = self.args(ref(mapped[0]))
                inner = matrix @ self.operator(ref(mapped[1])) @ self.axis_placement(ref(rep_map[0]))
                self.items_of(ref(rep_map[1]), inner)

    def elements(self):
        return [e for e, (kind, line) in self.other.items()
                if kind not in NOT_ELEMENTS and b"IFCREL" not in kind and ELEMENT.match(line)]


def run(path, outdir):
    t0 = time.time()
    reader = Reader()
    reader.read(path)
    elements = reader.elements()
    step = max(len(elements) // 100, 1)
    for n, e in enumerate(elements):
        p = reader.args(e)  # IfcProduct: ..., ObjectPlacement, Representation
        if len(p) >= 7 and ref(p[6]) is not None:
            matrix = reader.placement(ref(p[5]))
            for representation in reader.args(ref(p[6]))[2]:
                reader.items_of(ref(representation), matrix)
        if n % step == 0:
            print("PROGRESS %d Reading element %d/%d" % (100 * n // len(elements), n + 1, len(elements)), flush=True)
    if not len(reader.collector):
        print("No mesh geometry found in this IFC file.", file=sys.stderr, flush=True)
        return 1
    reader.collector.save(outdir)
    print("faces ear clipped: %d" % reader.ear_clipped)
    print("done: %d triangles in %d meshes [%.0fs]" % (reader.collector.triangles, len(reader.collector), time.time() - t0))
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1], sys.argv[2]))
