"""The hand-over between a reader script and build_blend.py.

A reader turns a foreign file into faces and leaves two files in its output folder:

    geometry.npz   v<i> float32 (n, 3) vertices, one pair per mesh i with f<i> int32 (m, 3) triangle indices and,
                   optionally, q<i> int32 (k, 4) quad indices and e<i> int32 (l, 2) edge indices
                   (a mesh without any face or edge is a set of loose points)
    geometry.json  {"meshes": [{"i": 0, "material": "Red", "hidden": false}, ...],
                    "colours": {"Red": [r, g, b, a]}, "origin": [x, y, z]}

There is one mesh per (material, hidden) pair. Coordinates are metres, as stored in the source: nothing is moved.
Readers of data far from the world origin (GIS in RD or UTM) subtract a round `origin` from the vertices so that
the 32-bit floats of a mesh keep their millimetres; every object is then placed at `origin`, so its position in the
world is the same as in the source.
A meshes entry with "pointcloud": true is a Blender point cloud: v<i> holds the points, c<i> (optional) uint8 colours
(n, 3) and "radius" the point size.
"""
import json
import os

import numpy as np

DEFAULT_MATERIAL = "DefaultMaterial"
GEOMETRY_NPZ = "geometry.npz"
GEOMETRY_JSON = "geometry.json"
ORIGIN_STEP = 1000.0  # origins are multiples of this, so that they are exact in 32-bit floats


def colour_name(r, g, b):
    """Material name for a plain colour given as 0..1 floats."""
    return "Color_%02x%02x%02x" % tuple(int(round(max(0.0, min(1.0, c)) * 255)) for c in (r, g, b))


def origin_for(low, high, limit=10000.0):
    """Round origin (x, y, 0) near the centre of data between the corners `low` and `high`; the zero origin when the
    data lies within `limit` metres of the world origin."""
    low, high = np.asarray(low, np.float64), np.asarray(high, np.float64)
    if max(abs(low[0]), abs(low[1]), abs(high[0]), abs(high[1])) <= limit:
        return (0.0, 0.0, 0.0)
    return (round((low[0] + high[0]) / 2 / ORIGIN_STEP) * ORIGIN_STEP,
            round((low[1] + high[1]) / 2 / ORIGIN_STEP) * ORIGIN_STEP, 0.0)


class MeshCollector:
    """Gathers faces, edges and points per (material, hidden) and writes them in the format described above."""

    def __init__(self):
        self._buckets = {}   # (material, hidden) -> {"v": [...], "f": [...], "q": [...], "e": [...], "n": vertex count}
        self._clouds = []
        self.colours = {}
        self.origin = (0.0, 0.0, 0.0)
        self.triangles = 0
        self.quads = 0
        self.edges = 0
        self.points = 0

    def _bucket(self, material, hidden, colour):
        if colour is not None and material not in self.colours:
            self.colours[material] = [float(c) for c in colour[:3]] + [1.0]
        return self._buckets.setdefault((material, bool(hidden)), {"v": [], "f": [], "q": [], "e": [], "n": 0})

    @staticmethod
    def _append(bucket, kind, vertices, indices):
        bucket["v"].append(np.asarray(vertices, np.float32))
        bucket[kind].append(np.asarray(indices, np.int32) + bucket["n"])
        bucket["n"] += len(vertices)

    def add(self, material, vertices, faces, hidden=False, colour=None):
        """vertices (n, 3), faces (m, 3) indices into these vertices. `colour` is only kept for a new material."""
        if len(faces) == 0:
            return
        self._append(self._bucket(material, hidden, colour), "f", vertices, faces)
        self.triangles += len(faces)

    def add_quads(self, material, vertices, quads, hidden=False, colour=None):
        """vertices (n, 3), quads (m, 4)."""
        if len(quads) == 0:
            return
        self._append(self._bucket(material, hidden, colour), "q", vertices, quads)
        self.quads += len(quads)

    def add_edges(self, material, vertices, edges, hidden=False, colour=None):
        """vertices (n, 3), edges (m, 2)."""
        if len(edges) == 0:
            return
        self._append(self._bucket(material, hidden, colour), "e", vertices, edges)
        self.edges += len(edges)

    def add_points(self, material, vertices, hidden=False, colour=None):
        """Loose vertices without any face or edge."""
        if len(vertices) == 0:
            return
        bucket = self._bucket(material, hidden, colour)
        bucket["v"].append(np.asarray(vertices, np.float32))
        bucket["n"] += len(vertices)
        self.points += len(vertices)

    def add_pointcloud(self, name, vertices, colours=None, radius=0.01):
        """A Blender point cloud: vertices (n, 3), colours uint8 (n, 3) or None."""
        if len(vertices):
            self._clouds.append((name, np.asarray(vertices, np.float32), colours, float(radius)))
            self.points += len(vertices)

    def __len__(self):
        return len(self._buckets) + len(self._clouds)

    def save(self, outdir):
        os.makedirs(outdir, exist_ok=True)
        arrays, meshes = {}, []
        for i, ((material, hidden), bucket) in enumerate(sorted(self._buckets.items())):
            arrays["v%d" % i] = np.concatenate(bucket["v"])
            arrays["f%d" % i] = np.concatenate(bucket["f"]) if bucket["f"] else np.zeros((0, 3), np.int32)
            if bucket["q"]:
                arrays["q%d" % i] = np.concatenate(bucket["q"])
            if bucket["e"]:
                arrays["e%d" % i] = np.concatenate(bucket["e"])
            meshes.append(dict(i=i, material=material, hidden=hidden))
        for name, vertices, colours, radius in self._clouds:
            i = len(meshes)
            arrays["v%d" % i] = vertices
            if colours is not None:
                arrays["c%d" % i] = np.asarray(colours, np.uint8)
            meshes.append(dict(i=i, material=name, hidden=False, pointcloud=True, radius=radius))
        np.savez(os.path.join(outdir, GEOMETRY_NPZ), **arrays)
        with open(os.path.join(outdir, GEOMETRY_JSON), "w", encoding="utf-8") as f:
            json.dump(dict(meshes=meshes, colours=self.colours, origin=list(self.origin)), f, indent=1)
