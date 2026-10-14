"""SketchUp (.skp) -> triangles, through the SketchUp C API bindings (module `sketchup`, bundled in engine/wheels).
Runs in Blender's Python. Material = SketchUp material (face, else group/component, else default); hidden entities
and entities on hidden tags stay hidden. Coordinates are metres, nothing is moved.
Writes geometry.npz/json (see geometry_io.py). Usage: read_skp.py <in.skp> <outdir>"""
import os
import sys
import time

import numpy as np
import sketchup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geometry_io import DEFAULT_MATERIAL, MeshCollector  # noqa: E402

MAX_DEPTH = 40


def apply(parts, matrix):
    """Transform (material, hidden, V, F) parts; a mirroring transform flips the triangle winding."""
    mirrored = np.linalg.det(matrix[:3, :3]) < 0
    return [(material, hidden, V @ matrix[:3, :3].T + matrix[:3, 3], F[:, ::-1] if mirrored else F)
            for material, hidden, V, F in parts]


def element_hidden(element):
    if element.hidden:
        return True
    layer = element.layer
    return bool(layer is not None and not layer.visible)


class Reader:
    def __init__(self):
        self.cache = {}  # (definition name, default material) -> parts in the definition's own space

    def collect(self, entities, default_material, hidden, depth=0):
        """Triangles of these entities (faces, groups, component instances) as (material, hidden, V, F) parts."""
        verts, tris = {}, {}
        for face in entities.faces:
            name = face.material.name if face.material else default_material
            vs, tri, _ = face.tessfaces
            if not tri:
                continue
            base = len(verts.setdefault(name, []))
            verts[name].extend(vs)
            tris.setdefault(name, []).extend((a + base, b + base, c + base) for a, b, c in tri)
        parts = [(name, hidden, np.array(verts[name], np.float64), np.array(tris[name], np.int64)) for name in verts]
        if depth > MAX_DEPTH:
            return parts
        for group in entities.groups:
            inner = self.collect(group.entities, group.material.name if group.material else default_material,
                                 hidden or element_hidden(group), depth + 1)
            parts += apply(inner, np.array(group.transform, np.float64))
        for instance in entities.instances:
            material = instance.material.name if instance.material else default_material
            key = (instance.definition.name, material)
            if key not in self.cache:
                self.cache[key] = self.collect(instance.definition.entities, material, False, depth + 1)
            inner = [(m, h or hidden or element_hidden(instance), V, F) for m, h, V, F in self.cache[key]]
            parts += apply(inner, np.array(instance.transform, np.float64))
        return parts


def run(src, outdir):
    t0 = time.time()
    try:
        model = sketchup.Model.from_file(src)
    except Exception as ex:
        print("SketchUp could not open this file (%s). It may be newer than the bundled SketchUp API." % ex,
              file=sys.stderr, flush=True)
        return 1
    colours = {}
    for mat in model.materials:
        r, g, b, _ = mat.color
        colours[mat.name] = (r / 255, g / 255, b / 255)
    print("PROGRESS 5 Reading SketchUp model", flush=True)

    parts = Reader().collect(model.entities, DEFAULT_MATERIAL, False)
    print("PROGRESS 80 Collecting geometry", flush=True)
    collector = MeshCollector()
    for material, hidden, V, F in parts:
        collector.add(material, V, F, hidden, colours.get(material))
    if not len(collector):
        print("No faces found in this SketchUp file", file=sys.stderr, flush=True)
        return 1
    collector.save(outdir)
    print("skp: %d triangles in %d meshes [%.0fs]" % (collector.triangles, len(collector), time.time() - t0), flush=True)
    return 0


if __name__ == "__main__":
    code = run(sys.argv[1], sys.argv[2])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)  # the SketchUp bindings can crash while the interpreter shuts down
