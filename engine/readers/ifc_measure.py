"""Measures an IFC file as text, without building the model: how much of it is mesh data (tessellated geometry, as
written by exporters such as Rhino or Revit) against parametric geometry. That decides the reader: mostly mesh data
goes to ifc_meshes.py, everything else through ifc_reduce.py and ifc_solids.py. Reads line by line, so memory stays small.
Writes {"mesh_share": 0..1, "megabytes": n}. Usage: ifc_measure.py <in.ifc> <out.json>"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ifc_text import MESH_TYPES, entities  # noqa: E402


def run(path, out_path):
    t0 = time.time()
    total = mesh = count = 0
    for count, (_, kind, text) in enumerate(entities(path), 1):
        total += len(text)
        if kind in MESH_TYPES:
            mesh += len(text)
        if count % 2_000_000 == 0:
            print("  %d entities [%.0fs]" % (count, time.time() - t0), flush=True)
    share = mesh / total if total else 0.0
    print("%d entities, %.0f MB, %.0f%% mesh data [%.0fs]" % (count, total / 1e6, 100 * share, time.time() - t0))
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(dict(mesh_share=share, megabytes=round(total / 1e6, 1)), f)


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2])
