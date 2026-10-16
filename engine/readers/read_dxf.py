"""DXF -> triangles, with ezdxf. Runs in Blender's Python.
Faces come from 3DFACE, SOLID/TRACE, MESH, POLYFACE and POLYGON MESH entities and from ACIS solids that ezdxf can
turn into meshes. Blocks are expanded. Material = layer name (colour ByLayer) or the explicit colour; entities on
layers that are off or frozen (or marked invisible) stay hidden in the .blend.
Writes geometry.npz/json (see geometry_io.py). Usage: read_dxf.py <in.dxf> <outdir>"""
import os
import sys
import time
from collections import Counter

import ezdxf
import numpy as np
from ezdxf import colors, recover, units
from ezdxf.acis import api as acis
from ezdxf.render import MeshBuilder

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geometry_io import DEFAULT_MATERIAL, MeshCollector, colour_name  # noqa: E402
from polygons import triangulate_rings  # noqa: E402

SOLID_KINDS = ("3DSOLID", "BODY", "REGION", "SURFACE", "EXTRUDEDSURFACE", "REVOLVEDSURFACE", "SWEPTSURFACE")
MAX_BLOCK_DEPTH = 20


def triangulate(P):
    """Polygon (n, 3) -> triangles (m, 3) of local indices."""
    n = len(P)
    if n < 3:
        return np.zeros((0, 3), np.int64)
    if n == 3:
        return np.array([[0, 1, 2]], np.int64)
    if n == 4:  # split along the diagonal that stays inside
        a = np.cross(P[1] - P[0], P[2] - P[0])
        b = np.cross(P[2] - P[0], P[3] - P[0])
        if np.dot(a, b) >= 0:
            return np.array([[0, 1, 2], [0, 2, 3]], np.int64)
        return np.array([[0, 1, 3], [1, 2, 3]], np.int64)
    triangles = triangulate_rings([P])
    if len(triangles):
        return triangles
    return np.array([[0, i, i + 1] for i in range(1, n - 1)], np.int64)  # degenerate polygon: plain fan


def vec(v):
    return np.array([v[0], v[1], v[2] if len(v) > 2 else 0.0], np.float64)


def grid_faces(m, n, closed_m, closed_n):
    """Quads of an m x n polygon mesh (vertex index = row * n + column)."""
    faces = []
    for i in range(m if closed_m else m - 1):
        for j in range(n if closed_n else n - 1):
            a, b = i * n + j, i * n + (j + 1) % n
            c, d = ((i + 1) % m) * n + (j + 1) % n, ((i + 1) % m) * n + j
            faces.append((a, b, c, d))
    return faces


class Reader:
    def __init__(self, doc):
        self.layers = doc.layers
        try:
            self.scale = float(units.conversion_factor(doc.units, units.M)) if doc.units else 1.0
        except Exception:
            self.scale = 1.0
        self.collector = MeshCollector()
        self.skipped = Counter()

    def expand(self, insert, depth=0):
        """Block contents with the insert's transform; layer 0 and ByBlock colour follow the insert, as in AutoCAD."""
        out = []
        if depth > MAX_BLOCK_DEPTH:
            return out
        try:
            virtuals = list(insert.virtual_entities())
        except Exception:
            self.skipped["INSERT (error)"] += 1
            return out
        for virtual in virtuals:
            if virtual.dxf.get("layer", "0") == "0":
                virtual.dxf.layer = insert.dxf.get("layer", "0")
            if virtual.dxf.get("color", 256) == 0:
                if insert.dxf.hasattr("true_color"):
                    virtual.dxf.true_color = insert.dxf.true_color
                else:
                    virtual.dxf.color = insert.dxf.get("color", 256)
            if virtual.dxftype() == "INSERT":
                out += self.expand(virtual, depth + 1)
            else:
                out.append(virtual)
        return out

    def layer(self, name):
        try:
            return self.layers.get(name)
        except ezdxf.DXFTableEntryError:
            return None

    def material(self, entity, layer):
        """(material name, rgb 0..1)"""
        if entity.dxf.hasattr("true_color"):
            r, g, b = colors.int2rgb(entity.dxf.true_color)
        else:
            aci = entity.dxf.get("color", 256)
            if aci not in (0, 256) and 1 <= abs(aci) <= 255:
                r, g, b = colors.aci2rgb(abs(aci))
            elif layer is not None:  # ByLayer: the material is the layer
                if layer.dxf.hasattr("true_color"):
                    r, g, b = colors.int2rgb(layer.dxf.true_color)
                else:
                    r, g, b = colors.aci2rgb(max(1, min(255, abs(layer.dxf.get("color", 7)))))
                return layer.dxf.name, (r / 255, g / 255, b / 255)
            else:
                return DEFAULT_MATERIAL, (0.8, 0.8, 0.8)
        rgb = (r / 255, g / 255, b / 255)
        return colour_name(*rgb), rgb

    def hidden(self, entity):
        if entity.dxf.get("invisible", 0):
            return True
        layer = self.layer(entity.dxf.get("layer", "0"))
        return bool(layer is not None and (layer.is_off() or layer.is_frozen()))

    def add_polygons(self, entity, polygons, hidden):
        """polygons: list of (n, 3) arrays in drawing units"""
        material, rgb = self.material(entity, self.layer(entity.dxf.get("layer", "0")))
        for P in polygons:
            triangles = triangulate(P)
            if len(triangles):
                self.collector.add(material, P * self.scale, triangles, hidden, rgb)

    def convert(self, entity):
        kind = entity.dxftype()
        hidden = self.hidden(entity)
        if kind == "3DFACE":
            pts = [vec(entity.dxf.get(f"vtx{i}", (0, 0, 0))) for i in range(4)]
            self.add_polygons(entity, [np.array(pts[:3] if np.allclose(pts[2], pts[3]) else pts)], hidden)
        elif kind in ("SOLID", "TRACE"):  # corners are stored in zig-zag order
            v = [vec(entity.dxf.get(f"vtx{i}", (0, 0, 0))) for i in range(4)]
            self.add_polygons(entity, [np.array([v[0], v[1], v[2]] if np.allclose(v[2], v[3])
                                                 else [v[0], v[1], v[3], v[2]])], hidden)
        elif kind == "MESH":
            V = [vec(v) for v in entity.vertices]
            self.add_polygons(entity, [np.array([V[i] for i in f]) for f in entity.faces if len(f) >= 3], hidden)
        elif kind == "POLYLINE" and entity.is_poly_face_mesh:
            builder = MeshBuilder.from_polyface(entity)
            V = [vec(v) for v in builder.vertices]
            self.add_polygons(entity, [np.array([V[i] for i in f]) for f in builder.faces if len(f) >= 3], hidden)
        elif kind == "POLYLINE" and entity.is_polygon_mesh:
            V = [vec(v.dxf.location) for v in entity.vertices]
            faces = grid_faces(entity.dxf.m_count, entity.dxf.n_count, entity.is_closed, entity.is_n_closed)
            self.add_polygons(entity, [np.array([V[i] for i in f]) for f in faces], hidden)
        elif kind in SOLID_KINDS:
            polygons = []
            for body in acis.load_dxf(entity):
                for mesh in acis.mesh_from_body(body, merge_lumps=True):
                    V = [vec(v) for v in mesh.vertices]
                    polygons += [np.array([V[i] for i in f]) for f in mesh.faces if len(f) >= 3]
            if polygons:
                self.add_polygons(entity, polygons, hidden)
            else:
                self.skipped[kind] += 1
        else:
            self.skipped[kind] += 1


def run(src, outdir):
    t0 = time.time()
    try:
        doc = ezdxf.readfile(src)
    except ezdxf.DXFStructureError:
        doc, _ = recover.readfile(src)
    reader = Reader(doc)
    print("PROGRESS 5 Reading DXF", flush=True)

    entities = []
    for entity in doc.modelspace():
        if entity.dxftype() == "INSERT":
            entities += reader.expand(entity)
        else:
            entities.append(entity)
    for k, entity in enumerate(entities):
        try:
            reader.convert(entity)
        except Exception:  # a broken entity must not stop the whole file
            reader.skipped[entity.dxftype() + " (error)"] += 1
        if k % 500 == 0:
            print("PROGRESS %d Reading DXF" % (5 + 90 * k // len(entities)), flush=True)

    if not len(reader.collector):
        print("No faces found in this DXF (only %s)" % dict(reader.skipped.most_common(6)), file=sys.stderr, flush=True)
        return 1
    reader.collector.save(outdir)
    print("faces: %d triangles in %d meshes, unit scale %g [%.0fs]"
          % (reader.collector.triangles, len(reader.collector), reader.scale, time.time() - t0))
    if reader.skipped:
        print("skipped (no faces): %s" % dict(reader.skipped.most_common(10)))
    return 0


if __name__ == "__main__":
    code = run(sys.argv[1], sys.argv[2])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
