"""STEP or IGES -> glTF with OpenCASCADE (cascadio), run in Blender's Python. Usage: step_to_glb.py <in> <out.glb>.
Tolerances are relative to edge length so large assemblies do not explode into millions of triangles."""
import os
import sys

import cascadio

src, dst = sys.argv[1], sys.argv[2]
kind = "IGES" if os.path.splitext(src)[1].lower() in (".igs", ".iges") else "STEP"
print(f"PROGRESS 5 Tessellating {kind}", flush=True)
tol = dict(tol_linear=0.003, tol_angular=0.3, tol_relative=True)
if kind == "IGES":
    with open(src, "rb") as f:
        glb = cascadio.to_glb_bytes(f.read(), file_type=cascadio.IGES, **tol)
    if glb:
        with open(dst, "wb") as f:
            f.write(glb)
    rc = 0 if glb else 1
else:
    rc = cascadio.step_to_glb(src, dst, **tol)
if rc != 0 or not os.path.isfile(dst):
    print(f"OpenCASCADE could not read this {kind} file", file=sys.stderr, flush=True)
    os._exit(1)
print("PROGRESS 100 Tessellated", flush=True)
os._exit(0)
