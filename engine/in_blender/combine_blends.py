"""Blender (background): combines several finished .blend files into one. Every file gets its own collection (named
after the source file); the file's '!HIDDEN' collection, when it has one, sits inside it and stays switched off.
Usage: blender -b --factory-startup --python combine_blends.py -- <manifest.json> <out.blend>
The manifest is a list of {"blend": path, "name": collection name}."""
import json
import os
import sys

import bpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cleanup  # noqa: E402

MARK = "hidden_in_source"  # set by cleanup.mark_hidden_for_combine


def find_layer(layer, coll):
    if layer.collection == coll:
        return layer
    for child in layer.children:
        found = find_layer(child, coll)
        if found:
            return found
    return None


def main():
    manifest, dst = sys.argv[sys.argv.index("--") + 1:][:2]
    with open(manifest, encoding="utf-8") as f:
        parts = json.load(f)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    switched_off = []
    for n, part in enumerate(parts):
        print(f"PROGRESS {100 * n // len(parts)} Combining {n + 1}/{len(parts)}", flush=True)
        old_objects = {o.name for o in bpy.data.objects}
        old_collections = {c.name for c in bpy.data.collections}
        with bpy.data.libraries.load(part["blend"], link=False) as (source, target):
            target.objects = list(source.objects)
            target.collections = list(source.collections)
        objects = [o for o in bpy.data.objects if o.name not in old_objects]
        collections = [c for c in bpy.data.collections if c.name not in old_collections]

        root = bpy.data.collections.new(part["name"])
        scene.collection.children.link(root)
        nested = {child for c in collections for child in c.children}
        for coll in collections:
            if coll.name.startswith(cleanup.HIDDEN_COLLECTION):
                coll.name = f"{cleanup.HIDDEN_COLLECTION} ({part['name']})"
                switched_off.append(coll)
            if coll not in nested:
                root.children.link(coll)
        for obj in objects:
            if not obj.users_collection:  # it sat in the file's own scene collection
                root.objects.link(obj)

        for obj in objects:
            if obj.get(MARK):
                del obj[MARK]
                try:
                    obj.hide_set(True)
                except RuntimeError:
                    pass

    bpy.context.view_layer.update()
    for coll in switched_off:
        layer = find_layer(bpy.context.view_layer.layer_collection, coll)
        if layer is not None:
            layer.hide_viewport = True
    print("PROGRESS 95 Saving .blend", flush=True)
    bpy.ops.wm.save_as_mainfile(filepath=dst, compress=True)
    print("PROGRESS 100 Done", flush=True)


if __name__ == "__main__":
    main()
