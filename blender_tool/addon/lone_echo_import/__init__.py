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
    "version": (0, 5, 0),
    "blender": (4, 1, 0),
    "location": "File > Import > Lone Echo (.lemesh) / Lone Echo Scatter (.lescatter)",
    "description": "Import Lone Echo / NRadEngine meshes, characters and whole "
                   "scatter levels with full attributes, per-draw PBR materials, "
                   "skeletons and level-of-detail selection",
    "category": "Import-Export",
}

import bpy   # type: ignore  # noqa: E402
from bpy.props import (   # type: ignore  # noqa: E402
    BoolProperty, EnumProperty, FloatProperty, StringProperty,
)
from bpy_extras.io_utils import ImportHelper          # type: ignore  # noqa: E402

from . import (package_reader, mesh_builder, material_builder, scene_reader,   # noqa: E402
               scatter_reader, scatter_import, light_import, lightmap_builder)

# Re-export the scatter import entry point so headless callers can use
# `lone_echo_import.import_lescatter(pkg, context, opts)` alongside import_lemesh.
import_lescatter = scatter_import.import_lescatter

# Scene lights. OFF BY DEFAULT and, when on, defaults to the eEnableDiffuse
# subset only: most shipped Lone Echo lights are SPECULAR-ONLY (49 of 118 set
# eEnableDiffuse; 15 of 47 on station_front) and sit on top of a BAKED lightmap,
# so importing them all double-lights the scene -- measured at 7.06x brighter on
# identical receivers. See light_import.py's header and docs/LIGHTING.md §0.
import_lights = light_import.import_lights


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


def _archive_collection(context, archive: str):
    """The ONE `lescene_<archive>` collection, created on first use and REUSED.

    ⚠ Assembling a level means calling `import_lemesh` once per package (51 of
    them for the bridge) against the SAME scene.json. `bpy.data.collections.new`
    would hand back `lescene_<archive>.001 … .050`, one per package, so the room
    would arrive as fifty sibling collections that no consumer can select, hide
    or export as a unit. Look the name up first, and re-link it to the scene when
    an earlier call left it unlinked.
    """
    name = f"lescene_{archive}"
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in context.scene.collection.children:
        try:
            context.scene.collection.children.link(coll)
        except Exception:      # noqa: BLE001 - already linked elsewhere in the tree
            pass
    return coll


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

    arch_coll = _archive_collection(context, archive)

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
    """Core import routine. Returns a summary dict. Usable without the operator.

    Lightmap options (the same keys `IMPORT_OT_lemesh` exposes; see
    `lightmap_builder.wire_lightmap` for the full contract):

        lightmap_mode        "baked" (default) | "ambient" | "none"
        lightmap_basis       "sg5" (default) | "single"
        lightmap_texture     explicit path to the level's BC6H_UF16 atlas
        lightmap_dir         directory to search for it
        lightmap_slice_dir   where the per-page slices are cached
        lightmap_auto_split  bool, default True
        lightmap_intensity   float, default 1.0
        lightmap_use_ao      bool, default False -- leave OFF (findings §5)
        lightmap_uv_layer    OVERRIDE for the lightmap UV set.  Default: the
                             object's OWN resolved set -- the manifest's
                             `lightmap_uv`, i.e. the texcoord on semantic slot 4
                             (`shader-confirmed`).  Usually `uv1`;
                             `uv2` on a (0, 1, 4) object.  Falls back to the
                             literal "uv1" only for a package whose vertex
                             format cannot be resolved at all.  ⚠ If this ever
                             grows a UI field it must default to EMPTY (meaning
                             "resolve per object"), never to the string "uv1".

    ⚠ The whole block is inert unless an atlas actually resolves: the atlas is a
    LEVEL asset (one 68 MB `arraySize 65` DDS per scene) and is not part of a
    `.lemesh` package, so `lightmap_mode` defaults to the faithful `"baked"`
    without any risk of firing on a package that has no bake. The summary's
    `lightmap.reason` says why when nothing was wired.
    """
    pkg_path = Path(pkg_path)
    if pkg_path.name == "manifest.json":
        pkg_path = pkg_path.parent
    pkg = package_reader.Package(pkg_path)

    # skeleton (optional): joint names feed vertex-group naming + the armature
    skeleton, joint_names = _load_skeleton(pkg_path)
    if joint_names is not None:
        opts = dict(opts)
        opts["skeleton_joint_names"] = joint_names

    # Lightmap: resolve the LEVEL atlas ONCE, not once per mesh (it is a 68 MB
    # texture array and the resolver stats directories). `mesh_builder` reads
    # the context out of `opts` and derives the per-MESH spec from it.
    lm_mode = lightmap_builder.resolved_mode(opts)
    lm_ctx = {"available": False, "reason": "lightmap_mode == 'none'"}
    if lm_mode != lightmap_builder.MODE_NONE:
        lm_ctx = lightmap_builder.resolve_lightmap_context(
            pkg_path, pkg.manifest, opts)
    opts = dict(opts)
    opts["lightmap_context"] = lm_ctx

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
    # ★ MESH-level (scene-set) LOD. A character ships each LOD as its OWN mesh
    # and selects with `CGRenderParams.scenemask`, not with the mesh-list LOD
    # chain -- without this `liv_head` imports all 19 meshes and the face
    # z-fights against its own LOD 1. See `package_reader.select_lod_objects`.
    _objs = package_reader.select_lod_objects(pkg.objects, opts.get("lod_level", 0))
    if len(_objs) != len(pkg.objects):
        print("[lone_echo_import] scene-set LOD %d: %d of %d meshes"
              % (opts.get("lod_level", 0), len(_objs), len(pkg.objects)))
    for obj in _objs:
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

    # Lightmap summary, read back off the objects rather than predicted: which
    # pages were actually wired, and how many extra (material, page) variants
    # that cost. `pages` is a sorted list, never a count, so a package whose
    # meshes all collapsed onto one page is visible at a glance.
    lm_pages = sorted({ob["le_lightmap_page"] for ob in mesh_objects
                       if "le_lightmap_page" in ob.keys()})
    lm_wired = sum(1 for ob in mesh_objects if ob.get("le_lightmap_wired"))
    lightmap = {
        "mode": lm_mode,
        "available": bool(lm_ctx.get("available")),
        "reason": lm_ctx.get("reason", ""),
        "source": lm_ctx.get("source", ""),
        "texture": Path(lm_ctx["color_file"]).name if lm_ctx.get("color_file") else "",
        "arraysize": (lm_ctx.get("color_meta") or {}).get("arraysize"),
        "pages": lm_pages,
        "objects_wired": lm_wired,
        "variants": sum(1 for m in bpy.data.materials if "le_lightmap_page" in m.keys()),
    }

    return {"collection": coll_name, "objects": n_obj, "vertices": n_vert,
            "triangles": n_tri, "materials": len(materials), "bones": n_bones,
            "placement": placement, "lightmap": lightmap}


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
    lod_level: EnumProperty(
        name="LOD Level",
        description="Which level of a mesh's LOD chain to emit. Coarser levels are "
                    "extra draws over LATER slices of the SAME index buffer, so "
                    "'All levels' stacks them. Clamped per mesh, and a no-op for the "
                    "vast majority of mesh-lists, which carry no chain",
        items=[
            ("0", "LOD 0 (highest detail)", "Emit each mesh's most detailed level"),
            ("1", "LOD 1", "One step coarser where available"),
            ("2", "LOD 2", "Two steps coarser where available"),
            ("3", "LOD 3", "Three steps coarser where available"),
            ("-1", "All levels (stacked)", "Emit every draw — levels overlap"),
        ],
        default="0")   # type: ignore
    skip_unresolved: BoolProperty(
        name="Skip Unresolved Placements", default=False,
        description="Skip eAuto/eJoint/eRefPoint placements whose world could not be "
                    "resolved. Default: place them at their own local matrix, tagged "
                    "with a 'le_unresolved' custom property")   # type: ignore

    # --- baked lightmap ------------------------------------------------------
    # The bake is the term that carries the diffuse look of every lit surface:
    # 101.8 MB of baked GI against 108 KB of light records, a 936x ratio, and
    # most level lights are SPECULAR-ONLY (49 of 118 records set eEnableDiffuse;
    # 15 of 47 on station_front). See docs/LIGHTING.md §0 and
    # docs/LIGHTING.md.
    lightmap_mode: EnumProperty(
        name="Lightmap",
        description="How to apply the level's baked lightmap. Inert unless a "
                    "lightmap atlas is actually found -- it is a LEVEL asset and "
                    "is not part of a .lemesh package, so give it a path below",
        items=[
            ("baked", "Baked (unlit)",
             "Emission = albedo x lightmap, BSDF response zeroed. Reproduces the "
             "shipped look; scene lights cannot double-light it"),
            ("ambient", "Ambient (lit + baked)",
             "Lightmap added as an emissive ambient term with the BSDF left live. "
             "DOUBLE-COUNTS unless only the eEnableDiffuse lights are imported"),
            ("none", "None",
             "Leave the material graph untouched"),
        ],
        default="baked")   # type: ignore
    lightmap_texture: StringProperty(
        name="Lightmap Atlas", default="", subtype="FILE_PATH",
        description="The level's lobe-basis DDS (DXGI 95 BC6H_UF16, arraySize = "
                    "13 pages x 5 SG lobes). Blank = search the Lightmap Folder, "
                    "then the package's own directory")   # type: ignore
    lightmap_dir: StringProperty(
        name="Lightmap Folder", default="", subtype="DIR_PATH",
        description="Directory to search for the atlas (and for the BC5 AO pair, "
                    "whose arraySize gives the page count). Used when no explicit "
                    "atlas path is given")   # type: ignore
    lightmap_basis: EnumProperty(
        name="Basis",
        description="How the five SG lobes of a page are combined",
        items=[
            ("sg5", "SG5 (engine math)",
             "Weighted sum of the page's five tangent-space spherical-gaussian "
             "lobes, the weights the engine's own DiffuseTermSG gives a flat "
             "normal"),
            ("single", "Single lobe",
             "Lobe 0 of the page alone. Cheaper, NOT the engine's math"),
        ],
        default="sg5")   # type: ignore
    lightmap_auto_split: BoolProperty(
        name="Split Texture Array", default=True,
        description="Split the arraySize>1 atlas into per-page slice files. "
                    "Blender exposes only slice 0 of an array DDS, so turning "
                    "this off makes EVERY mesh render page 0")   # type: ignore
    lightmap_slice_dir: StringProperty(
        name="Slice Cache", default="", subtype="DIR_PATH",
        description="Where the split per-page slices are cached "
                    "(blank = '_lmslices' beside the atlas)")   # type: ignore
    lightmap_intensity: FloatProperty(
        name="Intensity", default=1.0, min=0.0, soft_max=8.0,
        description="Multiplies Emission Strength. 1.0 is the faithful value; "
                    "anything else is an exposure choice, not a calibration")   # type: ignore
    lightmap_use_ao: BoolProperty(
        name="Apply Lightmap AO", default=False,
        description="Multiply the ao0 H-basis band-0 term into the baked diffuse. "
                    "OFF because the ENGINE does not: on the lightmap path the AO "
                    "pair drives ambient SPECULAR only, in its own ubershader, so "
                    "switching this on DOUBLE-darkens relative to the shipped look")   # type: ignore

    def draw(self, context):
        layout = self.layout
        for prop in ("lod_level", "import_materials", "import_shadow_only", "flip_v",
                     "y_up_to_z_up", "import_armature"):
            layout.prop(self, prop)
        layout.separator()
        layout.prop(self, "lightmap_mode")
        lm = layout.column()
        lm.enabled = self.lightmap_mode != "none"
        lm.prop(self, "lightmap_texture")
        lm.prop(self, "lightmap_dir")
        lm.prop(self, "lightmap_basis")
        lm.prop(self, "lightmap_auto_split")
        lm.prop(self, "lightmap_slice_dir")
        lm.prop(self, "lightmap_intensity")
        lm.prop(self, "lightmap_use_ao")
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
            "lod_level": int(self.lod_level),
            "apply_scene_placement": self.apply_scene_placement,
            "scene_json_path": self.scene_json_path,
            "skip_unresolved": self.skip_unresolved,
            "lightmap_mode": self.lightmap_mode,
            "lightmap_texture": bpy.path.abspath(self.lightmap_texture)
                                if self.lightmap_texture else "",
            "lightmap_dir": bpy.path.abspath(self.lightmap_dir)
                            if self.lightmap_dir else "",
            "lightmap_basis": self.lightmap_basis,
            "lightmap_auto_split": self.lightmap_auto_split,
            "lightmap_slice_dir": bpy.path.abspath(self.lightmap_slice_dir)
                                  if self.lightmap_slice_dir else "",
            "lightmap_intensity": float(self.lightmap_intensity),
            "lightmap_use_ao": self.lightmap_use_ao,
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
        lm = summary.get("lightmap") or {}
        if lm.get("objects_wired"):
            msg += (f"; lightmap {lm['mode']} on {lm['objects_wired']} meshes, "
                    f"pages {lm['pages']} ({lm['variants']} material variants)")
        elif lm.get("mode") != "none" and lm.get("reason"):
            # Never silent: an unwired lightmap is a result, not an absence.
            self.report({"WARNING"}, f"lightmap not wired: {lm['reason']}")
        self.report({"INFO"}, msg)
        return {"FINISHED"}


def _menu(self, context):
    self.layout.operator(IMPORT_OT_lemesh.bl_idname, text="Lone Echo (.lemesh)")


_CLASSES = (IMPORT_OT_lemesh, scatter_import.IMPORT_OT_lescatter,
            light_import.IMPORT_OT_lelights)


def register():
    for c in _CLASSES:
        bpy.utils.register_class(c)
    bpy.types.TOPBAR_MT_file_import.append(_menu)
    bpy.types.TOPBAR_MT_file_import.append(scatter_import.menu_func)
    bpy.types.TOPBAR_MT_file_import.append(light_import.menu_func)


def unregister():
    bpy.types.TOPBAR_MT_file_import.remove(light_import.menu_func)
    bpy.types.TOPBAR_MT_file_import.remove(scatter_import.menu_func)
    bpy.types.TOPBAR_MT_file_import.remove(_menu)
    for c in _CLASSES:
        bpy.utils.unregister_class(c)


if __name__ == "__main__":
    register()
