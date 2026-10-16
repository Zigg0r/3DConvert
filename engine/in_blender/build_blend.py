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


def srgb_to_linear(values):
    c = values / 255.0
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4).astype(np.float32)


def add_point_cloud(name, points, colours, radius, origin):
    """A Blender point cloud object; colours are uint8 sRGB and end up in the 'Col' attribute."""
    cloud = bpy.data.pointclouds.new(name)
    cloud.resize(len(points))
    cloud.attributes["position"].data.foreach_set("vector", points.ravel())
    cloud.attributes.new("radius", "FLOAT", "POINT").data.foreach_set("value", np.full(len(points), radius, np.float32))
    if colours is not None:
        rgba = np.ones((len(points), 4), np.float32)
        rgba[:, :3] = srgb_to_linear(colours[:, :3].astype(np.float32))
        cloud.attributes.new("Col", "FLOAT_COLOR", "POINT").data.foreach_set("color", rgba.ravel())
    obj = bpy.data.objects.new(name, cloud)
    obj.location = origin
    bpy.context.scene.collection.objects.link(obj)
    return obj


def colour_material(obj):
    """Material that shows the point colours in renders (the viewport shows them with Solid > Color > Attribute)."""
    material = bpy.data.materials.new(obj.name)
    material.use_nodes = True
    if "Col" in obj.data.attributes:
        nodes = material.node_tree.nodes
        node = nodes.new("ShaderNodeAttribute")
        node.attribute_name = "Col"
        material.node_tree.links.new(node.outputs["Color"], nodes["Principled BSDF"].inputs["Base Color"])
    obj.data.materials.append(material)


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
    origin = meta.get("origin", [0.0, 0.0, 0.0])
    clouds = []
    for entry in meta["meshes"]:
        name, hidden = entry["material"], entry["hidden"]
        i = entry["i"]
        if entry.get("pointcloud"):
            clouds.append(add_point_cloud(name, data["v%d" % i], data["c%d" % i] if "c%d" % i in data.files else None,
                                          entry.get("radius", 0.01), origin))
            continue
        V, F = data["v%d" % i], data["f%d" % i]
        Q = data["q%d" % i] if "q%d" % i in data.files else np.zeros((0, 4), np.int32)
        E = data["e%d" % i] if "e%d" % i in data.files else np.zeros((0, 2), np.int32)
        label = name + " (hidden)" if hidden else name
        mesh = bpy.data.meshes.new(label)
        mesh.vertices.add(len(V))
        mesh.vertices.foreach_set("co", V.ravel())
        mesh.loops.add(F.size + Q.size)
        mesh.loops.foreach_set("vertex_index", np.concatenate([F.ravel(), Q.ravel()]))
        mesh.polygons.add(len(F) + len(Q))
        mesh.polygons.foreach_set("loop_start", np.concatenate(
            [np.arange(0, F.size, 3, dtype=np.int32), F.size + np.arange(0, Q.size, 4, dtype=np.int32)]))
        if len(E):  # lines: a mesh holds faces or lines, not both
            mesh.edges.add(len(E))
            mesh.edges.foreach_set("vertices", E.ravel())
        if name not in materials:
            materials[name] = simple_material(name, colours)
        mesh.materials.append(materials[name])
        mesh.update()
        mesh.validate(clean_customdata=False)
        obj = bpy.data.objects.new(label, mesh)
        obj.location = origin
        if not len(F) and not len(Q) and not len(E):
            obj[cleanup.POINTS_KEY] = True  # loose points: cleanup must not take this for an empty mesh
        if len(Q) and not len(F):
            obj[cleanup.GRID_KEY] = True  # a terrain grid: quads only, no triangulating and rejoining needed
        if hidden and cleanup.enabled("hidden"):  # layers hidden in the source go to the !HIDDEN collection
            cleanup.hidden_collection().objects.link(obj)
        else:
            bpy.context.scene.collection.objects.link(obj)
            if hidden:
                obj.hide_set(True)  # the eye icon, not 'Show in Viewport'

    print("STEP Cleaning meshes", flush=True)
    print("PROGRESS 50 Cleaning meshes", flush=True)
    cleanup.clean_all(lo=50, hi=95)
    for cloud in clouds:
        colour_material(cloud)  # after the cleanup, which would reduce it to a plain colour
    cleanup.finish_scene()
    bpy.ops.wm.save_as_mainfile(filepath=dst, compress=True)
    print("blend: %d objects, %d materials [%.0fs, peak %d MB]" % (len(meta["meshes"]), len(materials), time.time() - t0,
                                                                   cleanup.peak_memory_mb()))


if __name__ == "__main__":
    main()
