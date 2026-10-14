"""Scan a Rhino .3dm (runs in Blender's Python with rhino3dm on PYTHONPATH). Prints 'SCAN <json>'."""
import json
import os
import sys
from collections import Counter

import rhino3dm as r3

m = r3.File3dm.Read(sys.argv[1])
kinds, no_render_mesh, mesh_tris = Counter(), 0, 0
box = None
for o in m.Objects:
    g = o.Geometry
    kinds[type(g).__name__] += 1
    if isinstance(g, r3.Brep):
        if not all(f.GetMesh(r3.MeshType.Any) is not None for f in g.Faces):
            no_render_mesh += 1
    elif isinstance(g, r3.Extrusion):
        if g.GetMesh(r3.MeshType.Any) is None:
            no_render_mesh += 1
    elif isinstance(g, r3.Mesh):
        mesh_tris += g.Faces.TriangleCount + 2 * g.Faces.QuadCount
    b = g.GetBoundingBox()
    if b.IsValid:
        box = b if box is None else r3.BoundingBox.Union(box, b)

rows = [
    ["Rhino units", str(m.Settings.ModelUnitSystem).split(".")[-1]],
    ["Objects", f"{sum(kinds.values()):,}"],
    ["Object types", ", ".join(f"{k} {v}" for k, v in kinds.most_common(5)) or "-"],
    ["Mesh triangles", f"{mesh_tris:,}"],
    ["Layers", f"{len(m.Layers):,}"],
    ["Block definitions", f"{len(list(m.InstanceDefinitions)):,}"],
    ["Materials", f"{len(m.Materials):,}"],
]
if no_render_mesh:
    rows.append(["No render mesh", f"{no_render_mesh:,} curved objects (will be skipped)"])
if box is not None:
    rows.append(["Dimensions (model units)",
                 " x ".join(f"{hi - lo:.3g}" for lo, hi in zip((box.Min.X, box.Min.Y, box.Min.Z),
                                                              (box.Max.X, box.Max.Y, box.Max.Z)))])
print("SCAN " + json.dumps(dict(rows=rows)), flush=True)
os._exit(0)  # rhino3dm can crash while the interpreter shuts down
