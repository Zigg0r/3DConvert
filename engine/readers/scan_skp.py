"""Scan a SketchUp file (runs in Blender's Python with the bundled libraries on PYTHONPATH). Prints 'SCAN <json>'.
Uses the SketchUp C API binding when it is bundled and can open the file, else OpenSKP."""
import json
import os
import sys

import numpy as np

try:
    import sketchup
except ImportError:
    sketchup = None


def scan_with_openskp(path):
    try:
        from openskp import SkpFile
        model = SkpFile.open(path).parse()
    except Exception as ex:  # noqa: BLE001
        rows = [["Detailed scan", "SketchUp could not open this file (%s)" % str(ex)[:80]]]
        print("SCAN " + json.dumps(dict(rows=rows)), flush=True)
        os._exit(0)
    definitions = list(model.definitions.values())
    rows = [
        ["Component definitions", f"{len(definitions):,}"],
        ["Faces (all definitions)", f"{sum(len(d.faces) for d in definitions):,}"],
        ["Tags (layers)", f"{len(model.layers):,}"],
    ]
    hidden = [layer.name for layer in model.layers if layer.hidden]
    if hidden:
        rows.append(["Hidden tags", f"{len(hidden):,}"])
    rows.append(["Read with", "OpenSKP"])
    print("SCAN " + json.dumps(dict(rows=rows)), flush=True)
    os._exit(0)


if sketchup is None:
    scan_with_openskp(sys.argv[1])
try:
    model = sketchup.Model.from_file(sys.argv[1])
except Exception:
    scan_with_openskp(sys.argv[1])

stats = dict(faces=0, groups=0, hidden=0)
lo, hi = np.full(3, np.inf), np.full(3, -np.inf)


def walk(entities, depth=0):
    for face in entities.faces:
        stats["faces"] += 1
        if depth == 0 and stats["faces"] <= 20000:
            vs = np.array(face.tessfaces[0])
            if len(vs):
                lo[:] = np.minimum(lo, vs.min(0))
                hi[:] = np.maximum(hi, vs.max(0))
    for group in entities.groups:
        stats["groups"] += 1
        stats["hidden"] += bool(group.hidden)
        if depth < 8:
            walk(group.entities, depth + 1)


walk(model.entities)
top = model.entities
definitions = list(model.component_definitions)
rows = [
    ["Loose faces (top level)", f"{top.NumFaces():,}"],
    ["Groups (all levels)", f"{stats['groups']:,}"],
    ["Top-level components", f"{top.NumInstances():,}"],
    ["Component definitions", f"{len(definitions):,}"],
    ["Materials", f"{model.NumMaterials():,}"],
    ["Tags (layers)", f"{model.NumLayers():,}"],
    ["Scenes", f"{model.NumScenes():,}"],
]
if stats["hidden"]:
    rows.append(["Hidden groups", f"{stats['hidden']:,}"])
if lo[0] != np.inf:
    rows.append(["Dimensions (loose faces)", " x ".join(f"{h - l:.3g}" for l, h in zip(lo, hi))])
print("SCAN " + json.dumps(dict(rows=rows)), flush=True)
os._exit(0)
