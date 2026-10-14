"""Rhino (.3dm) -> triangles, without Rhino itself (library rhino3dm). Runs in Blender's Python.
- Faces come from the render meshes Rhino stored in the file (Brep, Extrusion) and from plain meshes. Planar faces
  without a render mesh are triangulated from their edge loops. Anything else that cannot be converted without
  Rhino is counted and reported in the log, never skipped silently.
- Blocks (InstanceReference) are expanded with their transforms, nested ones too.
- Curves, points and lights have no faces and are not converted (they are reported in the log).
- Material = the object's material, else its layer's material, else a colour material from the object or layer
  colour. Objects on hidden layers, or hidden themselves, stay hidden in the .blend.
Writes geometry.npz/json (see geometry_io.py). Usage: read_3dm.py <model.3dm> <outdir>"""
import os
import sys
import time
from collections import Counter

import numpy as np
import rhino3dm as r3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geometry_io import DEFAULT_MATERIAL, MeshCollector, colour_name  # noqa: E402
from polygons import triangulate_rings  # noqa: E402

UNIT_TO_METRE = {"Millimeters": 1e-3, "Centimeters": 1e-2, "Decimeters": 0.1, "Meters": 1.0,
                 "Inches": 0.0254, "Feet": 0.3048, "Kilometers": 1000.0}
CURVE_SAMPLES = 16
MAX_BLOCK_DEPTH = 20


def mesh_arrays(mesh):
    """Rhino mesh -> (vertices, triangles); quads are split into two triangles."""
    if mesh is None or mesh.Faces.Count == 0:
        return None
    points = np.array([(p.X, p.Y, p.Z) for p in mesh.Vertices.ToPoint3fArray()], np.float64)
    faces = np.array([mesh.Faces[i] for i in range(mesh.Faces.Count)], np.int64)
    quads = faces[:, 2] != faces[:, 3]
    return points, np.concatenate([faces[:, [0, 1, 2]], faces[quads][:, [0, 2, 3]]])


def loop_points(brep, loop):
    """Points along an edge loop (lines: end points; arcs and free curves: sampled)."""
    pts = []
    for trim in loop.Trims:
        if trim.EdgeIndex < 0:
            continue
        edge = brep.Edges[trim.EdgeIndex]
        domain = edge.Domain
        count = 1 if edge.IsLinear() else CURVE_SAMPLES
        params = [domain.T0 + (domain.T1 - domain.T0) * i / count for i in range(count + 1)]
        seg = [(p.X, p.Y, p.Z) for p in (edge.PointAt(t) for t in params)]
        if trim.IsReversed:
            seg = seg[::-1]
        if pts and seg:  # join up: turn the segment around when its other end is closer
            last, first, end = np.array(pts[-1]), np.array(seg[0]), np.array(seg[-1])
            if np.linalg.norm(last - end) < np.linalg.norm(last - first):
                seg = seg[::-1]
            seg = seg[1:]
        pts += seg
    points = np.array(pts, np.float64)
    if len(points) > 2 and np.linalg.norm(points[0] - points[-1]) < 1e-9:
        points = points[:-1]
    return points


def planar_face_arrays(brep, face):
    """Triangles of a planar face without a stored render mesh: edge loops + ear clipping."""
    if not face.IsPlanar(1e-3):
        return None
    rings = []
    for loop in face.Loops:
        points = loop_points(brep, loop)
        if len(points) >= 3:
            if "Outer" in str(loop.LoopType):
                rings.insert(0, points)
            else:
                rings.append(points)
    if not rings:
        return None
    triangles = triangulate_rings(rings)
    return (np.concatenate(rings), triangles) if len(triangles) else None


def transform_matrix(t):
    return np.array([[t.M00, t.M01, t.M02, t.M03], [t.M10, t.M11, t.M12, t.M13],
                     [t.M20, t.M21, t.M22, t.M23], [t.M30, t.M31, t.M32, t.M33]], np.float64)


class Reader:
    def __init__(self, model):
        self.model = model
        self.scale = UNIT_TO_METRE.get(str(model.Settings.ModelUnitSystem).split(".")[-1], 1.0)
        self.layers = {layer.Index: layer for layer in model.Layers}
        self.layer_by_id = {str(layer.Id): layer for layer in model.Layers}
        self.materials = list(model.Materials)
        self.definitions = {str(d.Id): d for d in model.InstanceDefinitions}
        self.objects = {str(o.Attributes.Id): o for o in model.Objects}
        self.collector = MeshCollector()
        self.stats = Counter()
        self.without_mesh = Counter()  # curved objects without a render mesh, per layer

    def layer_visible(self, layer):
        while layer is not None:
            if not layer.Visible:
                return False
            layer = self.layer_by_id.get(str(layer.ParentLayerId))
        return True

    def material(self, attrs, parent, layer):
        """(name, rgb) of an object: its own material, its layer's, or its colour."""
        source = str(attrs.MaterialSource).split(".")[-1]
        if "Parent" in source and parent is not None:
            return parent
        index = attrs.MaterialIndex if "Object" in source else layer.RenderMaterialIndex
        if 0 <= index < len(self.materials):
            mat = self.materials[index]
            c = mat.DiffuseColor
            rgb = [c[0] / 255, c[1] / 255, c[2] / 255]
            try:  # black diffuse means a Rhino 7+ PBR material: its colour is the PBR base colour
                pbr = mat.PhysicallyBased
                if pbr.Supported and max(c[0], c[1], c[2]) == 0:
                    rgb = [float(x) for x in pbr.BaseColor[:3]]
            except Exception:
                pass
            return mat.Name or "material%d" % index, rgb
        colour_source = str(attrs.ColorSource).split(".")[-1]
        c = attrs.ObjectColor if "Object" in colour_source else layer.Color
        rgb = [c[0] / 255, c[1] / 255, c[2] / 255]
        return colour_name(*rgb), rgb

    def convert(self, obj, matrix, parent_material=None, hidden=False, depth=0):
        geometry, attrs = obj.Geometry, obj.Attributes
        layer = self.layers.get(attrs.LayerIndex)
        hidden = hidden or not attrs.Visible or (layer is not None and not self.layer_visible(layer))
        layer_label = layer.FullPath if layer is not None else "?"
        material = self.material(attrs, parent_material, layer) if layer is not None else (DEFAULT_MATERIAL, [0.8, 0.8, 0.8])

        if isinstance(geometry, r3.InstanceReference):
            definition = self.definitions.get(str(geometry.ParentIdefId))
            if definition is None or depth > MAX_BLOCK_DEPTH:
                self.stats["block not found"] += 1
                return
            inner = matrix @ transform_matrix(geometry.Xform)
            for object_id in definition.GetObjectIds():
                child = self.objects.get(str(object_id))
                if child is not None:
                    self.convert(child, inner, material, hidden, depth + 1)
            self.stats["blocks expanded"] += 1
            return

        if isinstance(geometry, r3.Extrusion) and geometry.GetMesh(r3.MeshType.Any) is None and hasattr(geometry, "ToBrep"):
            geometry = geometry.ToBrep(False)
        if isinstance(geometry, r3.Brep):
            parts = []
            for face in geometry.Faces:
                part = mesh_arrays(face.GetMesh(r3.MeshType.Any))
                if part is None:
                    part = planar_face_arrays(geometry, face)
                    if part is not None:
                        self.stats["planar faces triangulated (no render mesh)"] += 1
                    else:
                        self.without_mesh[layer_label] += 1
                if part is not None:
                    parts.append(part)
        elif isinstance(geometry, r3.Extrusion):
            part = mesh_arrays(geometry.GetMesh(r3.MeshType.Any))
            if part is None:
                self.without_mesh[layer_label] += 1
            parts = [part] if part is not None else []
        elif isinstance(geometry, r3.Mesh):
            parts = [mesh_arrays(geometry)]
        else:
            self.stats["no faces (%s)" % type(geometry).__name__] += 1
            return
        parts = [p for p in parts if p is not None]
        if not parts:
            return
        points = np.concatenate([p[0] for p in parts])
        offsets = np.cumsum([0] + [len(p[0]) for p in parts[:-1]])
        faces = np.concatenate([p[1] + k for p, k in zip(parts, offsets)])
        world = (points @ matrix[:3, :3].T + matrix[:3, 3]) * self.scale
        self.collector.add(material[0], world, faces, hidden, material[1])
        self.stats["objects with faces"] += 1


def run(path, outdir):
    t0 = time.time()
    model = r3.File3dm.Read(path)
    if model is None:
        print("Rhino could not read this file.", file=sys.stderr, flush=True)
        return 1
    reader = Reader(model)
    objects = [o for o in model.Objects if not o.Attributes.IsInstanceDefinitionObject]
    identity = np.eye(4)
    step = max(len(objects) // 50, 1)
    for i, obj in enumerate(objects):
        reader.convert(obj, identity)
        if i % step == 0:
            print("PROGRESS %d Reading object %d/%d" % (100 * i // len(objects), i + 1, len(objects)), flush=True)
    reader.collector.save(outdir)
    print("unit scale to metres: %g" % reader.scale)
    print("counts:", dict(reader.stats))
    if reader.without_mesh:
        print("NOTE: %d curved faces without a render mesh were not converted; per layer: %s"
              % (sum(reader.without_mesh.values()), dict(reader.without_mesh.most_common(10))))
    print("done: %d triangles in %d meshes [%.0fs]" % (reader.collector.triangles, len(reader.collector), time.time() - t0))
    return 0


if __name__ == "__main__":
    code = run(sys.argv[1], sys.argv[2])
    sys.stdout.flush()
    os._exit(code)  # rhino3dm can crash while the interpreter shuts down
