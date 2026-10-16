"""Older and game 3D formats -> triangles, with the bundled ASSIMP library (assimp_py). Runs in Blender's Python.
DirectX .x, LightWave .lwo/.lws, Quake .md2/.md3/.md5mesh, MilkShape .ms3d, Source .smd, AC3D .ac, 3ds ASE .ase,
trueSpace .cob and Geomview .off. The scene graph is flattened (every mesh keeps its place), bones and animation
are dropped: the model is converted in its rest pose. Material = the file's material name and diffuse colour.
Writes geometry.npz/json (see geometry_io.py). Usage: read_assimp.py <in file> <outdir>"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from geometry_io import DEFAULT_MATERIAL, MeshCollector  # noqa: E402


def material_info(scene, index):
    """(name, rgb 0..1 or None) of a material; the keys of ASSIMP's material dictionaries differ per format."""
    if index is None or not 0 <= index < len(scene.materials):
        return DEFAULT_MATERIAL, None
    entry = scene.materials[index]
    name, rgb = None, None
    for key, value in entry.items():
        upper = str(key).upper()
        if upper in ("NAME", "?MAT.NAME") and isinstance(value, str) and value:
            name = value
        elif ("DIFFUSE" in upper and "COLOR" in upper) or upper == "$CLR.DIFFUSE":
            try:
                rgb = [float(c) for c in value][:3]
            except (TypeError, ValueError):
                pass
    return name or "Material%d" % index, rgb


def run(src, outdir):
    import assimp_py
    t0 = time.time()
    print("PROGRESS 5 Reading with ASSIMP", flush=True)
    # No FindDegenerates (the binding refuses the lines it makes) and no JoinIdenticalVertices (it collapses LightWave
    # meshes to one vertex); the clean-up merges duplicate vertices anyway.
    flags = assimp_py.Process_Triangulate | assimp_py.Process_PreTransformVertices
    try:
        scene = assimp_py.import_file(src, flags)
    except Exception as ex:  # assimp reports a broken or unsupported file as an exception
        print("ASSIMP could not read this file: %s" % ex, file=sys.stderr, flush=True)
        return 1
    collector = MeshCollector()
    skipped = 0
    collapsed = 0  # ASSIMP gives LightWave layers with several point chunks all-zero positions
    for k, mesh in enumerate(scene.meshes):
        faces = np.array(mesh.indices, np.int64)
        if not mesh.num_faces or faces.size != mesh.num_faces * 3:
            skipped += 1  # lines and points
            continue
        vertices = np.array(mesh.vertices, np.float32).reshape(-1, 3)
        if mesh.num_faces >= 10 and not vertices.any():
            collapsed += mesh.num_faces
            continue
        name, rgb = material_info(scene, mesh.material_index)
        collector.add(name, vertices, faces.reshape(-1, 3), colour=rgb)
        print("PROGRESS %d Reading with ASSIMP" % (10 + 80 * (k + 1) // len(scene.meshes)), flush=True)
    if collapsed:
        print("ASSIMP read %d faces of this file without any positions (a known limit with some LightWave files "
              "that have several point chunks per layer). Export the model as FBX or OBJ from the original program."
              % collapsed, file=sys.stderr, flush=True)
        return 1
    if not len(collector):
        print("No triangle meshes found in this file.", file=sys.stderr, flush=True)
        return 1
    collector.save(outdir)
    print("assimp: %d triangles in %d meshes%s [%.0fs]" % (
        collector.triangles, len(collector), ", %d line/point meshes skipped" % skipped if skipped else "",
        time.time() - t0))
    return 0


if __name__ == "__main__":
    code = run(sys.argv[1], sys.argv[2])
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
