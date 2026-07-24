"""Lone Echo (.lemesh) importer — Blender add-on.

Stage 2 of the Blender tool. Imports a `.lemesh` package (produced offline by
blender_tool/extractor/le_extract.py) into Blender with full vertex attributes,
per-draw material slots, PBR material graphs, and provenance metadata.

Install: zip the `lone_echo_import` folder and install via Preferences > Add-ons,
or point Blender at it directly. Also usable headlessly:

    import lone_echo_import
    lone_echo_import.import_lemesh("path/to/foo.lemesh", bpy.context, {})
"""

from __future__ import annotations

import json
import math
from pathlib import Path

bl_info = {
    "name": "Lone Echo Importer (.lemesh / .lescatter)",
    "author": "Dualgame",
    "version": (0, 1, 0),
    "blender": (4, 1, 0),
    "location": "File > Import > Lone Echo (.lemesh) / Lone Echo Scatter (.lescatter)",
    "description": "Import Lone Echo / NRadEngine meshes and whole scatter levels "
                   "with full attributes, per-draw PBR materials, and skeletons",
    "category": "Import-Export",
}

import bpy   # type: ignore  # noqa: E402
from bpy.props import BoolProperty, StringProperty   # type: ignore  # noqa: E402
from bpy_extras.io_utils import ImportHelper          # type: ignore  # noqa: E402

from . import (package_reader, mesh_builder, material_builder, scene_reader,   # noqa: E402
               scatter_reader, scatter_import)

# Re-export the scatter import entry point so headless callers can use
# `lone_echo_import.import_lescatter(pkg, context, opts)` alongside import_lemesh.
import_lescatter = scatter_import.import_lescatter


def _load_skeleton(pkg_path: Path):
    """Load skeleton.json (produced by extractor/le_skeleton.py) if present.

    Returns (skeleton_dict, {joint_index: joint_name}) or (None, None).
    """
    sk = Path(pkg_path) / "skeleton.json"
    if not sk.exists():
        return None, None
    try:
        data = json.loads(sk.read_text(encoding="utf-8"))
    except Exception:
        return None, None
    names = {j["index"]: j.get("name", f"joint_{j['index']}")
             for j in data.get("joints", [])}
    return data, names


def _build_armature(context, coll, skeleton, mesh_objects, opts):
    """Build an Armature from skeleton.json joints, parent meshes, add modifiers.

    Bones are placed at their object/model-space rest pose. When the decoder recovered
    the objectjoints matrices (`object_bind`) that rest pose is used directly (no FK
    accumulation needed); otherwise it is FK-composed from the local TRS down the
    parent chain. When both are absent, joints fall back to a small stacked layout so
    the hierarchy is still visible.
    """
    import bpy  # type: ignore
    from mathutils import Matrix, Quaternion, Vector  # type: ignore

    joints = skeleton.get("joints", [])
    if not joints:
        return None

    def _mat(rows16):
        return Matrix((rows16[0:4], rows16[4:8], rows16[8:12], rows16[12:16]))

    # Prefer the decoded object-space bind (authoritative rest pose); else FK.
    if all(j.get("object_bind") for j in joints):
        world = [_mat(j["object_bind"]) for j in joints]
    else:
        local = []
        for j in joints:
            loc = j.get("local", {})
            r = loc.get("r", [0.0, 0.0, 0.0, 1.0])   # x,y,z,w
            t = loc.get("t", [0.0, 0.0, 0.0])
            s = loc.get("s", 1.0) or 1.0
            quat = Quaternion((r[3], r[0], r[1], r[2]))   # mathutils wants w,x,y,z
            m = Matrix.Translation(Vector(t)) @ quat.to_matrix().to_4x4()
            m = m @ Matrix.Diagonal((s, s, s, 1.0))
            local.append(m)

        world = [None] * len(joints)

        def world_of(i):
            if world[i] is not None:
                return world[i]
            p = joints[i].get("parent", -1)
            world[i] = local[i] if p < 0 else (world_of(p) @ local[i])
            return world[i]

        for i in range(len(joints)):
            world_of(i)

    arm_data = bpy.data.armatures.new(f"{coll.name}_skeleton")
    arm_obj = bpy.data.objects.new(f"{coll.name}_skeleton", arm_data)
    coll.objects.link(arm_obj)

    # edit bones
    view_layer = context.view_layer
    view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode="EDIT")
    ebones = []
    for i, j in enumerate(joints):
        eb = arm_data.edit_bones.new(j.get("name", f"joint_{i}"))
        head = world[i].to_translation()
        child = j.get("firstchild", -1)
        tail = None
        if 0 <= child < len(joints):
            ctail = world[child].to_translation()
            if (ctail - head).length > 1e-5:
                tail = ctail
        if tail is None:
            # default: short bone along the joint's local X axis
            d = world[i].to_3x3() @ Vector((0.05, 0.0, 0.0))
            if d.length < 1e-5:
                d = Vector((0.0, 0.05, 0.0))
            tail = head + d
        eb.head = head
        eb.tail = tail
        ebones.append(eb)
    for i, j in enumerate(joints):
        p = j.get("parent", -1)
        if 0 <= p < len(ebones):
            ebones[i].parent = ebones[p]
    bpy.ops.object.mode_set(mode="OBJECT")

    # stash the authoritative inverse-bind matrix on each bone (downstream skinning)
    for i, j in enumerate(joints):
        ib = j.get("inverse_bind")
        if ib:
            bone = arm_data.bones.get(j.get("name", f"joint_{i}"))
            if bone is not None:
                bone["le_inverse_bind"] = list(ib)

    # match the mesh axis convention so the armature aligns with the meshes
    if opts.get("y_up_to_z_up", True):
        arm_obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)
    context.view_layer.update()

    # parent meshes to the armature (preserve their existing world transform)
    inv = arm_obj.matrix_world.inverted()
    for ob in mesh_objects:
        ob.parent = arm_obj
        ob.matrix_parent_inverse = inv
        if not any(m.type == "ARMATURE" for m in ob.modifiers):
            mod = ob.modifiers.new("Armature", "ARMATURE")
            mod.object = arm_obj
    arm_obj["le_joint_count"] = len(joints)
    return arm_obj


def _default_material(key: str):
    mat = bpy.data.materials.get(f"__le_default_{key}")
    if mat:
        return mat
    mat = bpy.data.materials.new(name=f"__le_default_{key}")
    mat.use_nodes = True
    mat["le_shaderset"] = key
    return mat


# --- scene placement (M4 APPLY step) -----------------------------------------

def _world_matrix(world_xf):
    """Row-major 16-float `world_xf` -> mathutils.Matrix (rows; translation col 3)."""
    from mathutils import Matrix  # type: ignore
    return Matrix(scene_reader.world_xf_rows(world_xf))


def _resolve_scene(pkg_path, opts):
    """Resolve the scene.json to consume: explicit `scene_json_path` else auto-detect.

    Returns the parsed scene dict, or None when there is no usable scene.json.
    """
    explicit = opts.get("scene_json_path")
    path = None
    if explicit:
        p = Path(explicit)
        if p.is_file():
            path = p
    if path is None:
        path = scene_reader.find_scene_json(pkg_path)
    if path is None:
        return None
    try:
        return scene_reader.load_scene(path)
    except Exception:
        return None


def _apply_placements(context, source_coll, placements, scene, opts) -> dict:
    """Instance `source_coll` once per scene placement, at each WORLD transform.

    The imported meshes carry the axis correction A on their OBJECT matrices
    (mesh_builder sets `ob.matrix_basis = A @ ...`; A = +90deg X when
    `y_up_to_z_up`, per AXIS_CALIBRATION.md). A placement's `world_xf` is a
    RAD-engine-space transform, so to stay correct in Blender it is CONJUGATED by A
    and set on a collection-instance empty:

        empty.matrix_world = A @ Matrix(world_xf) @ A^-1

    Because the instanced child meshes already carry A, the NET world transform of
    an instanced vertex is `(A W A^-1) @ A = A @ W` -- exactly the RAD world
    placement W viewed through the Y-up->Z-up correction. Applying W un-conjugated
    (or A@W directly on the empty) would rotate/mirror the copies even though the
    matrix is "correct". The empty's translation works out to `A @ t_world`.

    One empty is created per placement (N placements -> N objects), grouped under a
    `lescene_<archive>` collection. `resolved:false` placements (eAuto/eJoint/
    eRefPoint -- no fabricated world) are, by default, still placed at their own
    local matrix but tagged with a `le_unresolved` custom property (+ reason); pass
    `skip_unresolved` to drop them with a count instead.
    """
    A = mesh_builder._axis_matrix(opts)
    A_inv = A.inverted()
    archive = scene.get("archive", "scene")

    arch_coll = bpy.data.collections.new(f"lescene_{archive}")
    context.scene.collection.children.link(arch_coll)

    skip_unresolved = opts.get("skip_unresolved", False)
    placed = unresolved = skipped = 0
    for i, p in enumerate(placements):
        resolved = p.get("resolved", True)
        if not resolved and skip_unresolved:
            skipped += 1
            continue
        actor = p.get("actornodeid", f"p{i}")
        w = _world_matrix(p["world_xf"])

        empty = bpy.data.objects.new(f"{source_coll.name}__{actor}", None)
        empty.instance_type = "COLLECTION"
        empty.instance_collection = source_coll
        empty.empty_display_size = 0.25
        # conjugate the RAD world transform into Blender (Z-up) space; the instanced
        # children already carry A, so the net is A @ world_xf (correct).
        empty.matrix_world = A @ w @ A_inv
        arch_coll.objects.link(empty)

        empty["le_placement_actor"] = actor
        empty["le_parent_type"] = p.get("parent_type_name", "")
        empty["le_scale"] = p.get("scale", 1.0)
        empty["le_start_visible"] = bool(p.get("start_visible", True))
        empty["le_resolved"] = bool(resolved)
        if not resolved:
            empty["le_unresolved"] = True
            empty["le_unresolved_reason"] = p.get("reason", "")
            unresolved += 1
        if not p.get("start_visible", True):
            empty.hide_render = True
        placed += 1

    return {"collection": arch_coll.name, "placements": len(placements),
            "placed": placed, "unresolved": unresolved, "skipped": skipped}


def _place_scene(context, coll, pkg_path, src, opts):
    """Load the scene.json and instance `coll` at every placement of this meshlist.

    Returns a placement-summary dict, or None when no scene.json applies. Leaves the
    imported mesh at the origin (source collection still linked) when the scene has
    no placement for this meshlist hash.
    """
    scene = _resolve_scene(pkg_path, opts)
    if scene is None:
        return None
    meshlist_hash = (opts.get("scene_meshlist_hash")
                     or src.get("meshlist") or Path(pkg_path).stem)
    plist = scene_reader.placements_for(scene, meshlist_hash)
    if not plist:
        return {"collection": None, "placements": 0, "placed": 0,
                "unresolved": 0, "skipped": 0,
                "note": f"no placements for meshlist {meshlist_hash}"}
    # Detach the source meshlist from the view layer so it only serves as the
    # instance source (no duplicate copy left sitting at the world origin).
    try:
        context.scene.collection.children.unlink(coll)
    except Exception:
        pass
    return _apply_placements(context, coll, plist, scene, opts)


def import_lemesh(pkg_path, context, opts: dict) -> dict:
    """Core import routine. Returns a summary dict. Usable without the operator."""
    pkg_path = Path(pkg_path)
    if pkg_path.name == "manifest.json":
        pkg_path = pkg_path.parent
    pkg = package_reader.Package(pkg_path)

    # skeleton (optional): joint names feed vertex-group naming + the armature
    skeleton, joint_names = _load_skeleton(pkg_path)
    if joint_names is not None:
        opts = dict(opts)
        opts["skeleton_joint_names"] = joint_names

    # build materials up front, deduped by key
    materials: dict[str, "bpy.types.Material"] = {}
    if opts.get("import_materials", True):
        for spec in pkg.materials:
            materials[spec["key"]] = material_builder.build_material(spec, pkg_path, opts)

    def get_material(key: str):
        m = materials.get(key)
        if m is None:
            m = _default_material(key)
            materials[key] = m
        return m

    src = pkg.manifest.get("source", {})
    coll_name = f"lemesh_{src.get('meshlist', pkg_path.stem)}"
    coll = bpy.data.collections.new(coll_name)
    context.scene.collection.children.link(coll)

    n_obj = n_vert = n_tri = 0
    mesh_objects = []
    for obj in pkg.objects:
        if obj.get("shadow_only") and not opts.get("import_shadow_only", False):
            continue
        ob = mesh_builder.build_object(pkg, obj, get_material, opts)
        coll.objects.link(ob)
        mesh_objects.append(ob)
        n_obj += 1
        n_vert += obj.get("vertex_count", 0)
        n_tri += len(ob.data.polygons)

    n_bones = 0
    if skeleton is not None and opts.get("import_armature", True):
        arm = _build_armature(context, coll, skeleton, mesh_objects, opts)
        if arm is not None:
            n_bones = len(skeleton.get("joints", []))

    placement = None
    if opts.get("apply_scene_placement"):
        placement = _place_scene(context, coll, pkg_path, src, opts)

    return {"collection": coll_name, "objects": n_obj, "vertices": n_vert,
            "triangles": n_tri, "materials": len(materials), "bones": n_bones,
            "placement": placement}


class IMPORT_OT_lemesh(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.lemesh"
    bl_label = "Import Lone Echo (.lemesh)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})   # type: ignore

    import_materials: BoolProperty(name="Import Materials", default=True)   # type: ignore
    import_shadow_only: BoolProperty(name="Include Shadow-Only Meshes", default=False)   # type: ignore
    flip_v: BoolProperty(name="Flip UV V", default=True,
                         description="Convert DX top-left UV origin to Blender bottom-left")   # type: ignore
    y_up_to_z_up: BoolProperty(name="Y-up to Z-up", default=True,
                               description="Rotate RAD (Y-up) into Blender (Z-up)")   # type: ignore
    import_armature: BoolProperty(name="Import Armature", default=True,
                                  description="Build an Armature from skeleton.json "
                                              "(if present) and skin the meshes")   # type: ignore
    apply_scene_placement: BoolProperty(
        name="Apply Scene Placement", default=False,
        description="Place imported meshes at their level WORLD positions using a "
                    "scene.json (from scripts/le_scene.py) beside the package")   # type: ignore
    scene_json_path: StringProperty(
        name="scene.json", default="", subtype="FILE_PATH",
        description="Explicit scene.json path (blank = auto-detect beside the "
                    "package/manifest)")   # type: ignore
    skip_unresolved: BoolProperty(
        name="Skip Unresolved Placements", default=False,
        description="Skip eAuto/eJoint/eRefPoint placements whose world could not be "
                    "resolved. Default: place them at their own local matrix, tagged "
                    "with a 'le_unresolved' custom property")   # type: ignore

    def draw(self, context):
        layout = self.layout
        for prop in ("import_materials", "import_shadow_only", "flip_v",
                     "y_up_to_z_up", "import_armature"):
            layout.prop(self, prop)
        layout.separator()
        layout.prop(self, "apply_scene_placement")
        col = layout.column()
        col.enabled = self.apply_scene_placement
        col.prop(self, "scene_json_path")
        col.prop(self, "skip_unresolved")

    def execute(self, context):
        opts = {
            "import_materials": self.import_materials,
            "import_shadow_only": self.import_shadow_only,
            "flip_v": self.flip_v,
            "y_up_to_z_up": self.y_up_to_z_up,
            "import_armature": self.import_armature,
            "apply_scene_placement": self.apply_scene_placement,
            "scene_json_path": self.scene_json_path,
            "skip_unresolved": self.skip_unresolved,
        }
        try:
            summary = import_lemesh(self.filepath, context, opts)
        except Exception as exc:   # noqa: BLE001
            self.report({"ERROR"}, f"lemesh import failed: {exc}")
            return {"CANCELLED"}
        msg = ("Imported {objects} meshes, {vertices} verts, {triangles} tris, "
               "{materials} materials, {bones} bones".format(**summary))
        pl = summary.get("placement")
        if pl:
            if pl.get("placements"):
                msg += (f"; placed {pl['placed']} ({pl['unresolved']} unresolved, "
                        f"{pl['skipped']} skipped)")
            elif pl.get("note"):
                msg += f"; placement: {pl['note']}"
        self.report({"INFO"}, msg)
        return {"FINISHED"}


def _menu(self, context):
    self.layout.operator(IMPORT_OT_lemesh.bl_idname, text="Lone Echo (.lemesh)")


_CLASSES = (IMPORT_OT_lemesh, scatter_import.IMPORT_OT_lescatter)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(_menu)
    bpy.types.TOPBAR_MT_file_import.append(scatter_import.menu_func)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(scatter_import.menu_func)
    bpy.types.TOPBAR_MT_file_import.remove(_menu)
    for c in _CLASSES:
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
