"""SketchUp (.skp) -> triangles with OpenSKP (pure Python, MIT licence, no SketchUp SDK). Used when the SketchUp C API
binding (read_skp.py) is not bundled or cannot open the file, for example a file saved by a newer SketchUp.
Runs in Blender's Python. Uses the baked scene of OpenSKP: world-space triangles per material, in metres (Y up, turned
to Z up here). Only tags (layers) that are switched off are marked hidden; single hidden groups or faces are not.
A face whose front and back colours differ comes out twice, once per side.
Writes geometry.npz/json (see geometry_io.py). Usage: read_skp_open.py <in.skp> <outdir>"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geometry_io import DEFAULT_MATERIAL, MeshCollector, colour_name  # noqa: E402


def run(src, outdir):
    t0 = time.time()
    print("PROGRESS 5 Reading SketchUp model", flush=True)
    try:
        from openskp import SkpFile
        skp = SkpFile.open(src)
        model = skp.parse()
        print("PROGRESS 40 Building scene", flush=True)
        scene = skp.build_scene()
    except Exception as ex:  # noqa: BLE001 - report any parser problem as a failed read
        print("OpenSKP could not read this file (%s: %s)" % (type(ex).__name__, str(ex)[:200]), file=sys.stderr,
              flush=True)
        return 1
    hidden_layers = {layer.name for layer in model.layers if layer.hidden}
    collector, names = MeshCollector(), {}
    for prim in scene.glb_primitives:
        indices = np.array(prim.indices, np.int64).reshape(-1, 3)
        if not len(indices):
            continue
        P = np.array(prim.positions, np.float64).reshape(-1, 3)
        V = np.stack([P[:, 0], -P[:, 2], P[:, 1]], 1)  # glTF Y-up -> Z-up (a pure rotation, winding is kept)
        mat = scene.gltf_materials[prim.material_index] if 0 <= prim.material_index < len(scene.gltf_materials) else {}
        factor = mat.get("pbrMetallicRoughness", {}).get("baseColorFactor", [0.8, 0.8, 0.8, 1.0])
        rgb = [float(c) for c in factor[:3]]
        name = mat.get("name") or colour_name(*rgb)
        if names.setdefault(name, rgb) != rgb:  # one colour per material name
            name = colour_name(*rgb)
        meta = scene.mesh_index.get(prim.geom_name)
        hidden = bool(meta and meta.layer in hidden_layers)
        collector.add(name or DEFAULT_MATERIAL, V, indices, hidden, rgb)
    if not len(collector):
        print("No faces found in this SketchUp file", file=sys.stderr, flush=True)
        return 1
    collector.save(outdir)
    print("skp (OpenSKP): %d triangles in %d meshes [%.0fs]" % (collector.triangles, len(collector), time.time() - t0),
          flush=True)
    return 0


if __name__ == "__main__":
    code = run(sys.argv[1], sys.argv[2])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
