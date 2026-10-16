"""VRML97 (.wrl) -> triangles. Runs in Blender's Python. Reads Shape nodes (IndexedFaceSet, Box) through
Transform/Group/Switch/LOD/Anchor/Collision nodes; material = Material node (its DEF name, else its colour).
Coordinates are used as stored (metres), turned from Y-up to Z-up.
Writes geometry.npz/json (see geometry_io.py). Usage: read_wrl.py <in.wrl> <outdir>"""
import gzip
import math
import os
import re
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geometry_io import DEFAULT_MATERIAL, MeshCollector, colour_name  # noqa: E402
from polygons import triangulate_rings  # noqa: E402

TOKEN = re.compile(r'#[^\n]*|"(?:[^"\\]|\\.)*"|[\[\]{}]|[^\s,\[\]{}"#]+')
NUMBER = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?$")
GROUPING = {"Group", "Transform", "Collision", "Anchor", "Billboard", "Switch", "LOD", "Inline"}
MAX_DEPTH = 60
# VRML and X3D are Y-up (like Blender's own importer assumes); the result is Z-up
Y_UP_TO_Z_UP = np.array([[1.0, 0, 0, 0], [0, 0, -1.0, 0], [0, 1.0, 0, 0], [0, 0, 0, 1.0]])


class ReadError(Exception):
    pass


class Parser:
    def __init__(self, tokens):
        self.t, self.i, self.defs = tokens, 0, {}

    def peek(self, k=0):
        return self.t[self.i + k] if self.i + k < len(self.t) else None

    def next(self):
        tok = self.t[self.i]
        self.i += 1
        return tok

    def skip_block(self, opening="{", closing="}"):
        depth = 0
        while self.i < len(self.t):
            tok = self.next()
            depth += (tok == opening) - (tok == closing)
            if depth == 0:
                return

    def is_node_start(self):
        tok = self.peek()
        return tok in ("DEF", "USE") or (tok is not None and self.peek(1) == "{" and tok[0].isalpha())

    def node(self):
        tok, name = self.next(), None
        if tok == "USE":
            return self.defs.get(self.next())
        if tok == "DEF":
            name, tok = self.next(), self.next()
        node = dict(type=tok, name=name, f={})
        if name:
            self.defs[name] = node
        if self.next() != "{":
            raise ReadError("VRML syntax error near %r" % tok)
        if tok == "Script":
            self.i -= 1
            self.skip_block()
            return node
        while self.peek() not in ("}", None):
            key = self.next()
            if key == "ROUTE":
                self.i += 3
            elif key in ("PROTO", "EXTERNPROTO"):
                while self.peek() not in ("{", None):
                    self.i += 1
                self.skip_block()
            else:
                node["f"][key] = self.value()
        self.i += 1
        return node

    def item(self):
        if self.is_node_start():
            return self.node()
        tok = self.next()
        if NUMBER.match(tok):
            return float(tok)
        if tok in ("TRUE", "FALSE"):
            return tok == "TRUE"
        if tok == "NULL":
            return None
        return tok.strip('"')

    def value(self):
        if self.peek() == "[":
            self.i += 1
            items = []
            while self.peek() not in ("]", None):
                items.append(self.item())
            self.i += 1
            return items
        first = self.item()
        items = [first]
        if isinstance(first, float):
            while self.peek() is not None and NUMBER.match(self.peek()):
                items.append(float(self.next()))
        return items

    def top(self):
        nodes = []
        while self.peek() is not None:
            if self.peek() in ("PROTO", "EXTERNPROTO"):
                while self.peek() not in ("{", "[", None):
                    self.i += 1
                if self.peek() == "[":
                    self.skip_block("[", "]")
                if self.peek() == "{":
                    self.skip_block()
            elif self.peek() == "ROUTE":
                self.i += 4
            elif self.is_node_start():
                node = self.node()
                if node:
                    nodes.append(node)
            else:
                self.i += 1
        return nodes


def one(node, key, default=None):
    v = node["f"].get(key)
    return v[0] if v else default


def floats(node, key):
    return np.array([x for x in node["f"].get(key, []) if isinstance(x, float)], np.float64)


def rotation(axis_angle):
    x, y, z, a = axis_angle
    n = math.sqrt(x * x + y * y + z * z)
    if n == 0 or a == 0:
        return np.eye(3)
    x, y, z = x / n, y / n, z / n
    c, s = math.cos(a), math.sin(a)
    return np.array([[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                     [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                     [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]])


def translate(t):
    m = np.eye(4)
    m[:3, 3] = t
    return m


def linear(r):
    m = np.eye(4)
    m[:3, :3] = r
    return m


def transform_matrix(node):
    def vec(key, default, n):
        v = node["f"].get(key)
        return list(v) if v and len(v) == n else default
    t, c = vec("translation", [0, 0, 0], 3), vec("center", [0, 0, 0], 3)
    s, sr = vec("scale", [1, 1, 1], 3), vec("scaleOrientation", [0, 0, 1, 0], 4)
    r = vec("rotation", [0, 0, 1, 0], 4)
    rs = rotation(sr)
    return (translate(t) @ translate(c) @ linear(rotation(r)) @ linear(rs) @ linear(np.diag(s))
            @ linear(rs.T) @ translate([-v for v in c]))


def triangulate(points, index, ccw):
    """coordIndex list (polygons separated by -1) -> (m, 3) triangles. Triangles and quads are fanned, larger
    polygons are ear clipped."""
    idx = np.array([int(i) for i in index if isinstance(i, float)], np.int64)
    if not len(idx):
        return np.zeros((0, 3), np.int64)
    if idx[-1] != -1:
        idx = np.append(idx, -1)
    ends = np.flatnonzero(idx == -1)
    starts = np.concatenate([[0], ends[:-1] + 1])
    lens = ends - starts
    ok = lens >= 3
    big = ok & (lens > 4)
    ntri = np.where(ok & ~big, lens - 2, 0)
    polygon = np.repeat(np.arange(len(lens)), ntri)
    k = np.arange(ntri.sum()) - np.repeat(np.cumsum(ntri) - ntri, ntri) + 1
    s = starts[polygon]
    tris = [np.stack([idx[s], idx[s + k], idx[s + k + 1]], 1)]
    extra = []
    for p in np.flatnonzero(big):
        ring = idx[starts[p]:ends[p]]
        ring = ring[(ring >= 0) & (ring < len(points))]
        if len(ring) >= 3:
            extra.append(ring[triangulate_rings([points[ring]])])
    if extra:
        tris.append(np.concatenate(extra))
    out = np.concatenate(tris)
    out = out[(out >= 0).all(1) & (out < len(points)).all(1)]
    return out if ccw else out[:, ::-1]


def box_geometry(node):
    size = list(node["f"].get("size", [2, 2, 2]))
    x, y, z = (size + [2, 2, 2])[:3]
    V = np.array([[sx * x / 2, sy * y / 2, sz * z / 2] for sz in (-1, 1) for sy in (-1, 1) for sx in (-1, 1)], float)
    F = np.array([[0, 2, 3], [0, 3, 1], [4, 5, 7], [4, 7, 6], [0, 1, 5], [0, 5, 4],
                  [2, 6, 7], [2, 7, 3], [0, 4, 6], [0, 6, 2], [1, 3, 7], [1, 7, 5]], np.int64)
    return V, F


def material_of(shape):
    """(name, rgb or None) of a Shape's material."""
    appearance = one(shape, "appearance")
    material = one(appearance, "material") if appearance else None
    if not material:
        return DEFAULT_MATERIAL, None
    diffuse = material["f"].get("diffuseColor", [])
    rgb = list(diffuse) if len(diffuse) == 3 else [0.8, 0.8, 0.8]
    return material["name"] or colour_name(*rgb), rgb


def visit(node, matrix, collector, depth=0):
    if not node or depth > MAX_DEPTH:
        return
    kind = node["type"]
    if kind == "Shape":
        geometry = one(node, "geometry")
        if not geometry:
            return
        if geometry["type"] == "IndexedFaceSet":
            coord = one(geometry, "coord")
            if not coord:
                return
            P = floats(coord, "point")
            V = P[:len(P) // 3 * 3].reshape(-1, 3)
            F = triangulate(V, geometry["f"].get("coordIndex", []), one(geometry, "ccw", True))
        elif geometry["type"] == "Box":
            V, F = box_geometry(geometry)
        else:
            return
        if not len(F):
            return
        world = V @ matrix[:3, :3].T + matrix[:3, 3]
        if np.linalg.det(matrix[:3, :3]) < 0:  # mirrored: keep the triangles facing outward
            F = F[:, ::-1]
        material, rgb = material_of(node)
        collector.add(material, world, F, False, rgb)
        return
    if kind not in GROUPING:
        return
    if kind == "Transform":
        matrix = matrix @ transform_matrix(node)
    children = node["f"].get("children", []) or node["f"].get("choice", []) or node["f"].get("level", [])
    if kind == "Switch":
        choice = node["f"].get("choice", [])
        which = int(one(node, "whichChoice", -1))
        children = [choice[which]] if 0 <= which < len(choice) else []
    elif kind == "LOD":
        children = children[:1]  # the most detailed level
    for child in children:
        if isinstance(child, dict):
            visit(child, matrix, collector, depth + 1)


def run(src, outdir):
    t0 = time.time()
    with open(src, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", errors="replace")
    if text.lstrip("\ufeff").startswith("#VRML V1.0"):
        raise ReadError("VRML 1.0 files are not supported (only VRML 2.0 / VRML97 and X3D classic).")
    print("PROGRESS 5 Parsing VRML", flush=True)
    nodes = Parser(TOKEN.findall(text)).top()
    print("PROGRESS 50 Collecting geometry", flush=True)
    collector = MeshCollector()
    for node in nodes:
        visit(node, Y_UP_TO_Z_UP, collector)
    if not len(collector):
        raise ReadError("No faces found in this VRML file")
    collector.save(outdir)
    print("wrl: %d triangles in %d meshes [%.0fs]" % (collector.triangles, len(collector), time.time() - t0), flush=True)


if __name__ == "__main__":
    try:
        run(sys.argv[1], sys.argv[2])
    except ReadError as ex:
        print(ex, file=sys.stderr, flush=True)
        sys.exit(1)
