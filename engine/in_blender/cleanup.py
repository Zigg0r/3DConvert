"""Headless mesh cleaning pass over every object of the open scene, using bmesh instead of edit-mode operators.
Each step is one switch in the app's cleanup list (CLEANUP_STEPS in app/main.py); the steps the user unticked arrive
in the CLEANUP_OFF environment variable."""
import gc
import hashlib
import math
import os
import re
from collections import Counter

import bmesh
import bpy
import numpy as np
from mathutils import Matrix

MERGE_DISTANCE = 0.0001
DEGENERATE_DISTANCE = 0.000001
POINTS_KEY = "point_set"  # custom property of an object that is a set of loose points on purpose (GIS points)
GRID_KEY = "grid_mesh"  # temporary custom property of a terrain grid (quads only): its face layout needs no cleaning
# Steps the user unticked in the app, comma separated (see CLEANUP_STEPS in app/main.py)
OFF = {key for key in os.environ.get("CLEANUP_OFF", "").split(",") if key}


def enabled(step):
    return step not in OFF


def peak_memory_mb():
    """Highest RAM this process has used so far (Windows); 0 when it cannot be read. Used to tune the RAM budget."""
    try:
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD), ("peak", ctypes.c_size_t),
                        ("working", ctypes.c_size_t), ("pool_peak", ctypes.c_size_t), ("pool", ctypes.c_size_t),
                        ("npool_peak", ctypes.c_size_t), ("npool", ctypes.c_size_t), ("pagefile", ctypes.c_size_t),
                        ("pagefile_peak", ctypes.c_size_t)]
        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.windll.psapi
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        ok = psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
        return counters.peak // 2 ** 20 if ok else 0
    except Exception:  # only a diagnostic: it must never stop a conversion
        return 0


def _remove_attributes(mesh, names):
    for name in names:
        if name in mesh.attributes:
            try:
                mesh.attributes.remove(mesh.attributes[name])
            except Exception:
                pass


def _strip_uv_and_colour(mesh):
    try:
        while len(mesh.uv_layers):
            mesh.uv_layers.remove(mesh.uv_layers[0])
        for attr in list(mesh.color_attributes):
            mesh.color_attributes.remove(attr)
    except Exception:
        pass


def _strip_extras(obj, mesh):
    """Shape keys, weight groups, bevel/crease weights and custom properties."""
    try:
        if mesh.shape_keys:
            for block in list(mesh.shape_keys.key_blocks)[::-1]:
                obj.shape_key_remove(block)
        obj.vertex_groups.clear()
    except Exception:
        pass
    _remove_attributes(mesh, ("bevel_weight_vert", "bevel_weight_edge", "crease_vert", "crease_edge"))
    for block in (obj, mesh):
        for key in list(block.keys()):
            try:
                del block[key]
            except Exception:
                pass


def _remove_ios_data(obj, mesh):
    for block in (obj, mesh):
        for key in [k for k in block.keys() if k.lower().startswith("ios_")]:
            try:
                del block[key]
            except Exception:
                pass
    for attr in list(mesh.attributes):
        if attr.name.lower().startswith("ios_"):
            try:
                mesh.attributes.remove(attr)
            except Exception:
                pass


def _clear_special_data(obj, mesh):
    """Paint mask, skin and custom split normals; operators need an object in the view layer."""
    needs_ops = mesh.has_custom_normals or bool(mesh.skin_vertices)
    for name in (".sculpt_mask", "sculpt_mask"):
        if name in mesh.attributes:
            try:
                mesh.attributes.remove(mesh.attributes[name])
            except Exception:
                pass
    if not needs_ops:
        return
    try:
        with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
            if mesh.has_custom_normals:
                bpy.ops.mesh.customdata_custom_splitnormals_clear()
            if mesh.skin_vertices:
                bpy.ops.mesh.customdata_skin_clear()
    except Exception:
        pass


def _drop_unused_material_slots(mesh):
    n_poly = len(mesh.polygons)
    mats = list(mesh.materials)
    if not mats:
        return
    idx = np.empty(n_poly, np.int32)
    if n_poly:
        mesh.polygons.foreach_get("material_index", idx)
    used = np.unique(idx) if n_poly else np.empty(0, np.int32)
    if len(used) == len(mats) and (len(used) == 0 or used[-1] == len(mats) - 1):
        return
    if len(used) and used[-1] >= len(mats):
        return  # faces point past the slot list; leave untouched
    lut = np.zeros(len(mats), np.int32)
    lut[used] = np.arange(len(used), dtype=np.int32)
    mesh.materials.clear()
    for i in used:
        mesh.materials.append(mats[i])
    if n_poly:
        mesh.polygons.foreach_set("material_index", lut[idx])


def _flat_shade(mesh):
    """Same result as mesh.shade_flat() (sharp_face on every face), which sets the flags one face at a time in Python."""
    try:
        attrs = mesh.attributes
        attr = attrs.get("sharp_face")
        if attr is not None and (attr.domain != "FACE" or attr.data_type != "BOOLEAN"):
            attrs.remove(attr)
            attr = None
        if attr is None:
            attr = attrs.new("sharp_face", "BOOLEAN", "FACE")
        count = len(attr.data)
        if count:
            attr.data.foreach_set("value", np.ones(count, np.bool_))
    except Exception:
        mesh.shade_flat()


def _topology(bm):
    """Faces and edges of the bmesh as numpy arrays, in bmesh order, read through a scratch mesh so that no Python
    loop over the elements is needed: (face sizes, face first corner, corner vertex, corner edge, edge vertices)."""
    scratch = bpy.data.meshes.new("cleanup_scratch")
    try:
        bm.to_mesh(scratch)
        faces, corners, edges = len(scratch.polygons), len(scratch.loops), len(scratch.edges)
        starts = np.empty(faces, np.int32)
        corner_vert = np.empty(corners, np.int32)
        corner_edge = np.empty(corners, np.int32)
        edge_verts = np.empty(edges * 2, np.int32)
        scratch.polygons.foreach_get("loop_start", starts)
        scratch.loops.foreach_get("vertex_index", corner_vert)
        scratch.loops.foreach_get("edge_index", corner_edge)
        scratch.edges.foreach_get("vertices", edge_verts)
    finally:
        bpy.data.meshes.remove(scratch)
    sizes = np.diff(np.append(starts, corners))
    return sizes, starts, corner_vert, corner_edge, edge_verts.reshape(-1, 2)


def _twin_faces(sizes, starts, corner_vert):
    """Indices of the faces that sit on exactly the same vertices as an earlier face."""
    twins = []
    for size in np.unique(sizes):
        ids = np.nonzero(sizes == size)[0]
        rows = np.sort(corner_vert[starts[ids][:, None] + np.arange(size)], axis=1)
        order = np.lexsort(rows.T[::-1])  # stable: of equal rows the earliest face comes first
        ordered = rows[order]
        same = (ordered[1:] == ordered[:-1]).all(axis=1)
        if same.any():
            twins.append(ids[order[1:][same]])
    return np.concatenate(twins) if twins else np.empty(0, np.int64)


def clean_object(obj, shared):
    mesh = obj.data
    if len(mesh.polygons) == 0 and len(mesh.edges) == 0:
        return  # point sets (e.g. instance points) must keep coincident vertices
    layout = enabled("quads") and not obj.get(GRID_KEY)  # a terrain grid already has the clean layout
    if GRID_KEY in obj:
        del obj[GRID_KEY]

    # Importers may leave mesh.users wrong, so count objects instead. Every sharer gets its own copy and
    # the orphaned original is purged at the end (never remove it by hand: a wrong count would drop live data).
    if shared[mesh.name] > 1:
        mesh = mesh.copy()
        obj.data = mesh

    if enabled("leftovers"):
        _remove_ios_data(obj, mesh)
        _clear_special_data(obj, mesh)
    if enabled("flat"):
        _flat_shade(mesh)
        for name in (".uv_seam", "uv_seam", "sharp_edge"):
            if name in mesh.attributes:
                try:
                    mesh.attributes.remove(mesh.attributes[name])
                except Exception:
                    pass

    if enabled("uvcolor"):
        _strip_uv_and_colour(mesh)
    if enabled("extras"):
        _strip_extras(obj, mesh)
    if enabled("unhide"):
        _remove_attributes(mesh, (".hide_vert", ".hide_edge", ".hide_poly"))

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        if bm.verts and enabled("merge"):
            bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=MERGE_DISTANCE)
        if bm.edges and enabled("degenerate"):
            bmesh.ops.dissolve_degenerate(bm, edges=bm.edges, dist=DEGENERATE_DISTANCE)

        # Meshes without faces keep their edges so line objects are not wiped.
        if bm.faces:
            twins = loose_edges = loose_verts = ngons = ()
            if enabled("duplicates") or enabled("loose") or layout:
                # Everything is looked up first (and on arrays), because deleting shifts the bmesh indices.
                sizes, starts, corner_vert, corner_edge, edge_verts = _topology(bm)
                bm.faces.ensure_lookup_table()
                bm.edges.ensure_lookup_table()
                bm.verts.ensure_lookup_table()
                twin_ids = _twin_faces(sizes, starts, corner_vert) if enabled("duplicates") else np.empty(0, np.int64)
                twins = [bm.faces[i] for i in twin_ids]  # faces that sit on exactly the same vertices
                if enabled("loose"):
                    kept = np.ones(len(sizes), np.bool_)
                    kept[twin_ids] = False  # edges that only a deleted twin used become loose too
                    has_face = np.zeros(len(edge_verts), np.bool_)
                    has_face[corner_edge[np.repeat(kept, sizes)]] = True
                    in_edge = np.zeros(len(bm.verts), np.bool_)
                    in_edge[edge_verts[has_face].ravel()] = True
                    loose_edges = [bm.edges[i] for i in np.nonzero(~has_face)[0]]
                    loose_verts = [bm.verts[i] for i in np.nonzero(~in_edge)[0]]
                if layout:  # triangles need no triangulating
                    big = sizes > 3
                    big[twin_ids] = False
                    ngons = [bm.faces[i] for i in np.nonzero(big)[0]]
            if twins:
                bmesh.ops.delete(bm, geom=twins, context="FACES_ONLY")
            if enabled("loose"):
                if loose_edges:
                    bmesh.ops.delete(bm, geom=loose_edges, context="EDGES")
                loose_verts = [v for v in loose_verts if v.is_valid]  # deleting the edges can remove some vertices too
                if loose_verts:
                    bmesh.ops.delete(bm, geom=loose_verts, context="VERTS")
            if enabled("normals"):
                bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
            if layout:
                if ngons:
                    bmesh.ops.triangulate(bm, faces=ngons, quad_method="BEAUTY", ngon_method="BEAUTY")
                bmesh.ops.join_triangles(
                    bm,
                    faces=bm.faces,
                    angle_face_threshold=math.radians(40),
                    angle_shape_threshold=math.radians(40),
                    cmp_materials=True,
                )
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    if enabled("slots"):
        _drop_unused_material_slots(mesh)


def _base_colour(mat):
    """Principled base colour when it is a plain value, otherwise the viewport colour (e.g. textured materials)."""
    tree = mat.node_tree if mat.use_nodes else None
    if tree is not None:
        for node in tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                socket = node.inputs["Base Color"]
                if not socket.is_linked:
                    return tuple(socket.default_value[:3])
                break
    return tuple(mat.diffuse_color[:3])


def simplify_materials():
    """Remake every material with only a base colour and the same viewport colour: no alpha, no textures."""
    for mat in bpy.data.materials:
        colour = _base_colour(mat)
        rgba = (*[c if math.isfinite(c) else 0.8 for c in colour], 1.0)
        mat.diffuse_color = rgba
        mat.metallic = 0.0
        mat.roughness = 0.5
        try:
            mat.use_nodes = True
            tree = mat.node_tree
            tree.nodes.clear()
            out = tree.nodes.new("ShaderNodeOutputMaterial")
            bsdf = tree.nodes.new("ShaderNodeBsdfPrincipled")
            bsdf.inputs["Base Color"].default_value = rgba
            tree.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
        except (AttributeError, KeyError, RuntimeError):
            pass
        for attr, value in (("surface_render_method", "DITHERED"), ("use_backface_culling", False)):
            try:
                setattr(mat, attr, value)
            except (AttributeError, TypeError):
                pass


HIDDEN_COLLECTION = "!HIDDEN"


def remove_orphan_objects():
    """Objects that sit in no scene or collection."""
    if not enabled("orphans"):
        return
    for obj in list(bpy.data.objects):
        if obj.use_fake_user:
            continue
        try:
            linked = len(obj.users_collection) > 0 or len(obj.users_scene) > 0
        except Exception:
            linked = True
        if not linked:
            bpy.data.objects.remove(obj, do_unlink=True)


def fix_viewport_flags():
    """Show in Viewport switched off -> back on; objects that were hidden that way stay hidden via the eye icon."""
    if not enabled("viewport"):
        return
    for obj in bpy.data.objects:
        if not obj.hide_viewport:
            continue
        obj.hide_viewport = False
        try:
            obj.hide_set(True)
        except RuntimeError:
            pass  # not in the view layer


def hidden_collection():
    """The '!HIDDEN' collection, created on demand and switched off with its eye icon."""
    coll = bpy.data.collections.get(HIDDEN_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(HIDDEN_COLLECTION)
        bpy.context.scene.collection.children.link(coll)
    layer = bpy.context.view_layer.layer_collection.children.get(HIDDEN_COLLECTION)
    if layer is not None:
        layer.hide_viewport = True
    return coll


def move_hidden_objects():
    """Objects hidden in the imported file go to the '!HIDDEN' collection."""
    if not enabled("hidden"):
        return
    hidden = []
    for obj in bpy.data.objects:
        try:
            if obj.hide_get() or obj.hide_viewport:
                hidden.append(obj)
        except RuntimeError:
            pass
    if not hidden:
        return
    coll = hidden_collection()
    for obj in hidden:
        for old in list(obj.users_collection):
            old.objects.unlink(obj)
        coll.objects.link(obj)
    hidden_collection()


PHYSICS_MODIFIERS = {"CLOTH", "COLLISION", "SOFT_BODY", "FLUID", "DYNAMIC_PAINT", "PARTICLE_SYSTEM",
                     "PARTICLE_INSTANCE"}


def _unparent_keep_world(objs):
    """Clear Parent > Keep Transformation for these objects."""
    objs = [o for o in objs if o.parent]
    if not objs:
        return
    bpy.context.view_layer.update()
    worlds = [o.matrix_world.copy() for o in objs]
    for obj in objs:
        obj.parent = None
    for obj, world in zip(objs, worlds):
        obj.matrix_world = world


def _delete_keeping_children(objs):
    """Delete objects; whatever hangs below them keeps its place in the world."""
    names = {o.name for o in objs}
    _unparent_keep_world([o for o in bpy.data.objects if o.parent and o.parent.name in names and o.name not in names])
    for obj in objs:
        bpy.data.objects.remove(obj, do_unlink=True)


def remove_physics():
    """Cloth, soft body, collision, fluid, dynamic paint, particles, rigid bodies and force fields."""
    if not enabled("physics"):
        return
    for obj in list(bpy.data.objects):
        for mod in [m for m in obj.modifiers if m.type in PHYSICS_MODIFIERS]:
            obj.modifiers.remove(mod)
        try:
            if obj.field and obj.field.type != "NONE":
                obj.field.type = "NONE"
            for kind, op in (("rigid_body_constraint", "constraint_remove"), ("rigid_body", "object_remove")):
                if getattr(obj, kind, None):
                    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
                        getattr(bpy.ops.rigidbody, op)()
        except Exception:
            pass
    for scene in bpy.data.scenes:
        if scene.rigidbody_world:
            try:
                with bpy.context.temp_override(scene=scene):
                    bpy.ops.rigidbody.world_remove()
            except Exception:
                pass


MODIFIERS_NOTE = " [modifiers removed]"


def _tag_name(block, note):
    if not block.name.endswith(note):
        block.name = block.name[:63 - len(note)] + note  # Blender names are at most 63 characters


def remove_modifiers():
    """Deletes every modifier without applying it; the object (and its own mesh) get a note in the name."""
    if not enabled("modifiers"):
        return
    for obj in bpy.data.objects:
        if not len(obj.modifiers):
            continue
        obj.modifiers.clear()
        _tag_name(obj, MODIFIERS_NOTE)
        if obj.type == "MESH" and obj.data.users == 1:
            _tag_name(obj.data, MODIFIERS_NOTE)


def remove_armatures():
    """Armature objects, armature modifiers and the weight groups named after their bones."""
    if not enabled("armatures"):
        return
    arms = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    bones = {b.name for o in arms for b in o.data.bones}
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for mod in [m for m in obj.modifiers if m.type == "ARMATURE"]:
            obj.modifiers.remove(mod)
        for group in [g for g in obj.vertex_groups if g.name in bones]:
            obj.vertex_groups.remove(group)
    _delete_keeping_children(arms)


def clear_animation():
    """Keyframes, drivers, NLA and every action."""
    if not enabled("animation"):
        return
    for kind in ("objects", "meshes", "materials", "cameras", "lights", "curves", "armatures", "shape_keys", "worlds",
                 "scenes", "node_groups"):
        for block in getattr(bpy.data, kind):
            for target in (block, getattr(block, "node_tree", None)):
                if target is not None and getattr(target, "animation_data", None):
                    target.animation_data_clear()
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)


def clear_parents():
    """Clear Parent > Keep Transformation on every object."""
    if enabled("unparent"):
        _unparent_keep_world(list(bpy.data.objects))


def remove_empties():
    """Empty objects (axes, group holders); collection instances stay because they carry geometry."""
    if not enabled("empties"):
        return
    _delete_keeping_children([o for o in bpy.data.objects if o.type == "EMPTY"
                              and not (o.instance_type == "COLLECTION" and o.instance_collection)])


def remove_cameras_lights_world():
    if not enabled("camlight"):
        return
    _delete_keeping_children([o for o in bpy.data.objects if o.type in ("CAMERA", "LIGHT")])
    for scene in bpy.data.scenes:
        scene.world = None
    for world in list(bpy.data.worlds):
        bpy.data.worlds.remove(world)


def convert_to_meshes():
    """Curves, surfaces, text and metaballs become mesh objects with the same name, place and parent."""
    if not enabled("tomesh"):
        return
    targets = [o for o in bpy.data.objects if o.type in ("CURVE", "SURFACE", "FONT", "META")]
    if not targets:
        return
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in targets:
        try:
            mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph), depsgraph=depsgraph)
        except Exception:
            continue
        if mesh is None or not len(mesh.vertices):
            if mesh is not None:
                bpy.data.meshes.remove(mesh)
            continue
        new = bpy.data.objects.new(obj.name + "_mesh", mesh)
        new.parent, new.parent_type = obj.parent, obj.parent_type
        new.matrix_parent_inverse = obj.matrix_parent_inverse.copy()
        new.matrix_basis = obj.matrix_basis.copy()
        hidden = obj.hide_viewport
        for coll in obj.users_collection:
            coll.objects.link(new)
        for child in list(obj.children):
            child.parent = new  # keeps the child's matrices, so it stays in place
        name = obj.name
        bpy.data.objects.remove(obj, do_unlink=True)
        new.name = name
        mesh.name = name
        new.hide_viewport = hidden


def apply_transforms():
    """Bakes rotation and scale into the mesh; the location, and so the position in the world, stays."""
    if not enabled("applyts"):
        return
    objs = [o for o in bpy.data.objects if o.type == "MESH"]
    shared = Counter(o.data.name for o in objs)
    for obj in objs:
        if obj.children:
            continue  # children would move with the parent's scale
        rot_scale = obj.matrix_basis.to_3x3()
        if np.abs(np.array(rot_scale) - np.eye(3)).max() < 1e-9:
            continue
        if shared[obj.data.name] > 1:
            obj.data = obj.data.copy()
        mesh = obj.data
        mesh.transform(rot_scale.to_4x4())
        if rot_scale.determinant() < 0:
            try:
                mesh.flip_normals()
            except Exception:
                pass
        obj.matrix_basis = Matrix.Translation(obj.location)


def merge_materials():
    """Materials with the same colour become one (files with a unique material per loose part)."""
    if not enabled("mergemats"):
        return
    groups = {}
    for mat in sorted(bpy.data.materials, key=lambda m: m.name):
        groups.setdefault(tuple(round(c * 255) for c in _base_colour(mat)), []).append(mat)
    for mats in groups.values():
        for other in mats[1:]:
            other.user_remap(mats[0])
            bpy.data.materials.remove(other)
    for mesh in {o.data.name: o.data for o in bpy.data.objects if o.type == "MESH"}.values():
        _merge_slots(mesh)


def _merge_slots(mesh):
    """One slot per material: slots that now hold the same material are combined."""
    mats = list(mesh.materials)
    position, lut, kept = {}, [], []
    for mat in mats:
        key = mat.name if mat else None
        if key not in position:
            position[key] = len(kept)
            kept.append(mat)
        lut.append(position[key])
    if len(kept) == len(mats):
        return
    count = len(mesh.polygons)
    index = np.zeros(count, np.int32)
    if count:
        mesh.polygons.foreach_get("material_index", index)
    lut = np.array(lut, np.int32)
    index = lut[np.clip(index, 0, len(lut) - 1)]
    mesh.materials.clear()
    for mat in kept:
        mesh.materials.append(mat)
    if count:
        mesh.polygons.foreach_set("material_index", index)


def remove_empty_meshes():
    """Mesh objects without a single face or edge."""
    if not enabled("emptymesh"):
        return
    _delete_keeping_children([o for o in bpy.data.objects
                              if o.type == "MESH" and not len(o.data.polygons) and not len(o.data.edges)
                              and not o.get(POINTS_KEY)])


def remove_duplicate_objects():
    """Identical objects in the same place: same matrix, vertices, face count and materials."""
    if not enabled("duplicates"):
        return
    bpy.context.view_layer.update()
    seen, twins = set(), []
    for obj in sorted((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: o.name):
        mesh = obj.data
        count = len(mesh.vertices)
        if not count:
            continue
        co = np.empty(count * 3, np.float32)
        mesh.vertices.foreach_get("co", co)
        world = np.round(np.array(obj.matrix_world), 4) + 0.0
        key = (world.tobytes(), count, len(mesh.polygons), tuple(m.name if m else "" for m in mesh.materials),
               hashlib.md5((np.round(co, 5) + 0.0).tobytes()).hexdigest())
        if key in seen:
            twins.append(obj)
        else:
            seen.add(key)
    _delete_keeping_children(twins)


def tidy_names():
    """Strips '.001'-style suffixes when the plain name is free, then drops empty collections."""
    if not enabled("names"):
        return
    for kind in (bpy.data.objects, bpy.data.meshes, bpy.data.materials, bpy.data.collections):
        names = {b.name for b in kind}
        for block in sorted(kind, key=lambda b: b.name):
            m = re.match(r"^(.+)\.\d{3}$", block.name)
            if m and m.group(1) not in names:
                names.discard(block.name)
                block.name = m.group(1)
                names.add(block.name)
    changed = True
    while changed:
        changed = False
        for coll in list(bpy.data.collections):
            if not coll.objects and not coll.children:
                bpy.data.collections.remove(coll)
                changed = True


def tidy_scene():
    """One scene (extra scenes are folded in), metric units at scale 1, timeline reset."""
    if not enabled("scene"):
        return
    main = bpy.context.scene
    for extra in [s for s in bpy.data.scenes if s != main]:
        for obj in list(extra.collection.objects):
            if obj.name not in main.collection.all_objects:
                main.collection.objects.link(obj)
        for coll in list(extra.collection.children):
            if coll.name not in main.collection.children_recursive:
                main.collection.children.link(coll)
        bpy.data.scenes.remove(extra)
    units = main.unit_settings
    units.system, units.scale_length, units.length_unit = "METRIC", 1.0, "METERS"
    main.frame_start, main.frame_end, main.frame_current = 1, 250, 1
    main.use_preview_range = False
    main.timeline_markers.clear()
    for layer in list(main.view_layers)[1:]:
        main.view_layers.remove(layer)


def finish_scene():
    """Last pass, after hidden objects were sorted into their collection."""
    tidy_names()
    tidy_scene()
    mark_hidden_for_combine()


def mark_hidden_for_combine():
    """Combined mode: the eye-icon state is not stored on the object, so note it; combine_blends.py hides them again."""
    if os.environ.get("COMBINE_MARK") != "1":
        return
    for obj in bpy.data.objects:
        try:
            if obj.hide_get():
                obj["hidden_in_source"] = True
        except RuntimeError:
            pass  # not in the view layer


def clean_all(lo=0, hi=100):
    remove_orphan_objects()
    remove_physics()
    remove_modifiers()
    remove_armatures()
    clear_animation()
    remove_cameras_lights_world()
    convert_to_meshes()
    clear_parents()
    remove_empties()
    apply_transforms()
    objs = [o for o in bpy.data.objects if o.type == "MESH"]
    shared = Counter(o.data.name for o in objs)
    total = max(len(objs), 1)
    step = max(total // 50, 1)
    for i, obj in enumerate(objs):
        try:
            clean_object(obj, shared)
        except Exception as ex:  # one bad mesh must not stop the conversion
            print(f"Clean mesh: skipped '{obj.name}': {ex}", flush=True)
        if i % step == 0:
            print(f"PROGRESS {lo + (hi - lo) * i // total} Cleaning meshes {i + 1}/{total}", flush=True)
        if i % 200 == 199:
            gc.collect()
    if enabled("materials"):
        simplify_materials()
    merge_materials()
    remove_empty_meshes()
    remove_duplicate_objects()
    if enabled("extras"):
        for mat in bpy.data.materials:
            for key in list(mat.keys()):
                try:
                    del mat[key]
                except Exception:
                    pass
    if enabled("purge"):
        for _ in range(6):  # nested orphan chains need more than one pass
            try:
                purged = bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=False, do_recursive=True)
            except Exception:
                break
            if not purged:
                break
    print(f"PROGRESS {hi} Cleaned {len(objs)} object(s)", flush=True)
