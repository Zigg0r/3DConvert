"""Blender (background) scan of formats without a pure-Python scanner (USD, Alembic).
Usage: blender -b --factory-startup --python scan_native.py -- <source>. Prints 'SCAN <json>'."""
import json
import os
import sys

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import convert_native  # noqa: E402

bpy.ops.wm.read_factory_settings(use_empty=True)
convert_native.import_file(sys.argv[sys.argv.index("--") + 1])

meshes = [o for o in bpy.data.objects if o.type == "MESH"]
verts = sum(len(o.data.vertices) for o in meshes)
polys = sum(len(o.data.polygons) for o in meshes)
tris = sum(len(p.vertices) - 2 for o in meshes for p in o.data.polygons) if polys < 2_000_000 else polys * 2
lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
for o in meshes:
    corners = np.array([o.matrix_world @ Vector(c) for c in o.bound_box])
    lo, hi = np.minimum(lo, corners.min(0)), np.maximum(hi, corners.max(0))

rows = [
    ["Objects (meshes)", f"{len(meshes):,}"],
    ["Vertices", f"{verts:,}"],
    ["Polygons", f"{polys:,}"],
    ["Triangles", f"{tris:,}" + ("" if polys < 2_000_000 else " (estimate)")],
    ["Materials", f"{len(bpy.data.materials):,}"],
    ["Other objects", f"{len(bpy.data.objects) - len(meshes):,}"],
]
if meshes:
    rows.append(["Dimensions (X/Y/Z)", " x ".join(f"{h - l:.3g}" for l, h in zip(lo, hi))])
print("SCAN " + json.dumps(dict(rows=rows)))
