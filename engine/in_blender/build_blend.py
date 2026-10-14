"""Blender (background): reader output (geometry.npz/json, see geometry_io.py) -> .blend with one mesh object per
material. Everything is kept: nothing is merged per storey, instanced or moved; only objects that were hidden in the
source stay hidden. Usage: blender -b --factory-startup --python build_blend.py -- <geometry.npz> <out.blend>"""
import json
import math
import os
import sys
import time

import bpy
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cleanup  # noqa: E402


def simple_material(name, colours):
    """Plain colour; cleanup.simplify_materials finishes it (one base-colour node, no alpha)."""
    rgb = list(colours.get(name, [0.8, 0.8, 0.8]))[:3]
    rgb = [c if math.isfinite(c) else 0.8 for c in rgb]
    material = bpy.data.materials.new(name)
    material.diffuse_color = (*rgb, 1.0)
    try:
        material.use_nodes = True
        material.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (*rgb, 1.0)
    except (AttributeError, KeyError):
        pass
    return material


def main():
    src, dst = sys.argv[sys.argv.index("--") + 1:][:2]
    t0 = time.time()
    with open(os.path.splitext(src)[0] + ".json", encoding="utf-8") as f:
        meta = json.load(f)
    data = np.load(src)
    colours = meta["colours"]

    bpy.ops.wm.read_factory_settings(use_empty=True)
    print("PROGRESS 10 Building meshes", flush=True)
    materials = {}
    for entry in meta["meshes"]:
        name, hidden = entry["material"], entry["hidden"]
        V, F = data["v%d" % entry["i"]], data["f%d" % entry["i"]]
        label = name + " (hidden)" if hidden else name
        mesh = bpy.data.meshes.new(label)
        mesh.vertices.add(len(V))
        mesh.vertices.foreach_set("co", V.ravel())
        mesh.loops.add(F.size)
        mesh.loops.foreach_set("vertex_index", F.ravel())
        mesh.polygons.add(len(F))
        mesh.polygons.foreach_set("loop_start", np.arange(0, F.size, 3, dtype=np.int32))
        if name not in materials:
            materials[name] = simple_material(name, colours)
        mesh.materials.append(materials[name])
        mesh.update()
        mesh.validate(clean_customdata=False)
        obj = bpy.data.objects.new(label, mesh)
        if hidden and cleanup.enabled("hidden"):  # layers hidden in the source go to the !HIDDEN collection
            cleanup.hidden_collection().objects.link(obj)
        else:
            bpy.context.scene.collection.objects.link(obj)
            if hidden:
                obj.hide_set(True)  # the eye icon, not 'Show in Viewport'

    print("STEP Cleaning meshes", flush=True)
    print("PROGRESS 50 Cleaning meshes", flush=True)
    cleanup.clean_all(lo=50, hi=95)
    cleanup.finish_scene()
    bpy.ops.wm.save_as_mainfile(filepath=dst, compress=True)
    print("blend: %d objects, %d materials [%.0fs]" % (len(meta["meshes"]), len(materials), time.time() - t0))


if __name__ == "__main__":
    main()
