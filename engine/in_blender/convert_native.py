"""Blender (background) script: import a file with Blender's own importer, clean the meshes, save .blend.
Usage: blender -b --factory-startup --python convert_native.py -- <source> <target.blend> [zup]
'zup' undoes the Y-up turn of the glTF importer (for STEP files tessellated to glTF)."""
import math
import os
import struct
import sys

import bpy
import numpy as np
from mathutils import Matrix

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "addons"))  # io_scene_max
import cleanup  # noqa: E402


def read_3ds(path):
    """Minimal 3D Studio reader: meshes (vertices, triangles, per-face materials) and diffuse colours."""
    with open(path, "rb") as f:
        data = f.read()

    def chunks(start, end):
        pos = start
        while pos + 6 <= end:
            cid, length = struct.unpack_from("<HI", data, pos)
            if length < 6:
                return
            yield cid, pos + 6, min(pos + length, end)
            pos += length

    def cstr(pos):
        end = data.index(b"\0", pos)
        return data[pos:end].decode("latin-1"), end + 1

    materials, meshes = {}, []
    root = next(((s, e) for c, s, e in chunks(0, len(data)) if c == 0x4D4D), None)
    if root is None:
        raise ValueError("Not a 3DS file")
    editor = next(((s, e) for c, s, e in chunks(*root) if c == 0x3D3D), None)
    if editor is None:
        raise ValueError("3DS file has no 3D editor chunk")

    for cid, s, e in chunks(*editor):
        if cid == 0xAFFF:
            name, colour = "", (0.8, 0.8, 0.8)
            for c2, s2, e2 in chunks(s, e):
                if c2 == 0xA000:
                    name = cstr(s2)[0]
                elif c2 == 0xA020:
                    for c3, s3, e3 in chunks(s2, e2):
                        if c3 == 0x0011:
                            colour = tuple(b / 255 for b in data[s3:s3 + 3])
                        elif c3 == 0x0010:
                            colour = struct.unpack_from("<3f", data, s3)
            materials[name] = colour
        elif cid == 0x4000:
            obj_name, p = cstr(s)
            for c2, s2, e2 in chunks(p, e):
                if c2 != 0x4100:
                    continue
                verts = faces = None
                face_mats = []
                for c3, s3, e3 in chunks(s2, e2):
                    if c3 == 0x4110:
                        n = struct.unpack_from("<H", data, s3)[0]
                        verts = np.frombuffer(data, "<f4", n * 3, s3 + 2).reshape(n, 3)
                    elif c3 == 0x4120:
                        n = struct.unpack_from("<H", data, s3)[0]
                        faces = np.frombuffer(data, "<u2", n * 4, s3 + 2).reshape(n, 4)[:, :3]
                        for c4, s4, _ in chunks(s3 + 2 + n * 8, e3):
                            if c4 == 0x4130:
                                mname, q = cstr(s4)
                                cnt = struct.unpack_from("<H", data, q)[0]
                                face_mats.append((mname, np.frombuffer(data, "<u2", cnt, q + 2)))
                if verts is not None and faces is not None and len(faces):
                    meshes.append((obj_name, verts, faces, face_mats))
    return materials, meshes


def import_3ds(path):
    materials, meshes = read_3ds(path)
    if not meshes:
        raise ValueError("No meshes found in 3DS file")
    mats = {}
    for name, colour in materials.items():
        m = bpy.data.materials.new(name)
        m.diffuse_color = (*colour, 1.0)
        mats[name] = m
    for name, V, F, face_mats in meshes:
        me = bpy.data.meshes.new(name)
        me.vertices.add(len(V))
        me.vertices.foreach_set("co", V.astype(np.float32).ravel())
        me.loops.add(F.size)
        me.loops.foreach_set("vertex_index", F.astype(np.int32).ravel())
        me.polygons.add(len(F))
        me.polygons.foreach_set("loop_start", np.arange(0, F.size, 3, dtype=np.int32))
        if face_mats:
            index = np.zeros(len(F), np.int32)
            for slot, (mname, faces_idx) in enumerate(face_mats):
                me.materials.append(mats.get(mname))
                index[faces_idx[faces_idx < len(F)]] = slot
            me.polygons.foreach_set("material_index", index)
        me.update()
        me.validate(clean_customdata=False)
        bpy.context.scene.collection.objects.link(bpy.data.objects.new(name, me))


def import_file(path):
    ext = os.path.splitext(path)[1].lower()
    ops = bpy.ops
    if ext == ".blend":
        ops.wm.open_mainfile(filepath=path)  # replaces the empty scene; the cleaning pass then runs on it
    elif ext == ".obj":
        ops.wm.obj_import(filepath=path)
    elif ext == ".stl":
        ops.wm.stl_import(filepath=path)
    elif ext == ".ply":
        ops.wm.ply_import(filepath=path)
    elif ext == ".fbx":
        try:
            ops.wm.fbx_import(filepath=path)
        except (AttributeError, RuntimeError):
            ops.import_scene.fbx(filepath=path)
    elif ext in (".gltf", ".glb"):
        ops.import_scene.gltf(filepath=path)
    elif ext in (".usd", ".usda", ".usdc", ".usdz"):
        ops.wm.usd_import(filepath=path)
    elif ext == ".abc":
        ops.wm.alembic_import(filepath=path)
    elif ext == ".3ds":
        import_3ds(path)
    elif ext == ".max":
        import io_scene_max  # Blender extension 'Autodesk MAX Importer', bundled next to this script
        io_scene_max.register()
        ops.import_scene.max(filepath=path, use_image_search=False)
    else:
        raise ValueError(f"No importer for {ext}")


def undo_gltf_axis_swap():
    """cascadio writes STEP coordinates as they are; Blender's glTF import assumes Y-up and turns the model 90 degrees.
    Bake every object into world space with that turn reversed (also flattens the assembly hierarchy)."""
    fix = Matrix.Rotation(math.radians(-90), 4, "X")
    objs = [o for o in bpy.data.objects if o.type == "MESH"]
    worlds = [o.matrix_world.copy() for o in objs]
    for obj, world in zip(objs, worlds):
        obj.data = obj.data.copy()
        obj.data.transform(fix @ world)
        obj.parent = None
        obj.matrix_world = Matrix.Identity(4)
    for obj in [o for o in bpy.data.objects if o.type != "MESH"]:
        bpy.data.objects.remove(obj)


def main():
    args = sys.argv[sys.argv.index("--") + 1:]
    src, dst = args[:2]
    bpy.ops.wm.read_factory_settings(use_empty=True)
    print("PROGRESS 5 Importing file", flush=True)
    import_file(src)
    if not any(o.type == "MESH" for o in bpy.data.objects):
        raise SystemExit("No mesh geometry found in this file")
    if "zup" in args[2:]:
        undo_gltf_axis_swap()
    print("STEP Cleaning meshes", flush=True)
    print("PROGRESS 40 Cleaning meshes", flush=True)
    cleanup.clean_all(lo=40, hi=92)
    cleanup.fix_viewport_flags()
    cleanup.move_hidden_objects()
    cleanup.finish_scene()
    print("PROGRESS 94 Saving .blend", flush=True)
    bpy.ops.wm.save_as_mainfile(filepath=dst, compress=True)
    print("PROGRESS 100 Done", flush=True)


if __name__ == "__main__":
    main()
