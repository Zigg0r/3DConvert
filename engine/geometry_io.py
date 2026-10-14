"""The hand-over between a reader script and build_blend.py.

A reader turns a foreign file into triangles and leaves two files in its output folder:

    geometry.npz   v<i> float32 (n, 3) vertices and f<i> int32 (m, 3) triangle indices, one pair per mesh i
    geometry.json  {"meshes": [{"i": 0, "material": "Red", "hidden": false}, ...],
                    "colours": {"Red": [r, g, b, a]}}

There is one mesh per (material, hidden) pair. Coordinates are metres, as stored in the source: nothing is moved.
"""
import json
import os

import numpy as np

DEFAULT_MATERIAL = "DefaultMaterial"
GEOMETRY_NPZ = "geometry.npz"
GEOMETRY_JSON = "geometry.json"


def colour_name(r, g, b):
    """Material name for a plain colour given as 0..1 floats."""
    return "Color_%02x%02x%02x" % tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in (r, g, b))


class MeshCollector:
    """Gathers triangles per (material, hidden) and writes them in the format described above."""

    def __init__(self):
        self._buckets = {}   # (material, hidden) -> [vertex arrays, face arrays, vertex count]
        self.colours = {}
        self.triangles = 0

    def add(self, material, vertices, faces, hidden=False, colour=None):
        """vertices (n, 3), faces (m, 3) indices into these vertices. `colour` is only kept for a new material."""
        if len(faces) == 0:
            return
        bucket = self._buckets.setdefault((material, bool(hidden)), [[], [], 0])
        bucket[0].append(np.asarray(vertices, np.float32))
        bucket[1].append(np.asarray(faces, np.int32) + bucket[2])
        bucket[2] += len(vertices)
        self.triangles += len(faces)
        if colour is not None and material not in self.colours:
            self.colours[material] = [float(c) for c in colour[:3]] + [1.0]

    def __len__(self):
        return len(self._buckets)

    def save(self, outdir):
        os.makedirs(outdir, exist_ok=True)
        arrays, meshes = {}, []
        for i, ((material, hidden), (vertices, faces, _)) in enumerate(sorted(self._buckets.items())):
            arrays["v%d" % i] = np.concatenate(vertices)
            arrays["f%d" % i] = np.concatenate(faces)
            meshes.append(dict(i=i, material=material, hidden=hidden))
        np.savez(os.path.join(outdir, GEOMETRY_NPZ), **arrays)
        with open(os.path.join(outdir, GEOMETRY_JSON), "w", encoding="utf-8") as f:
            json.dump(dict(meshes=meshes, colours=self.colours), f, indent=1)
