"""Add the selected Blender objects to a shipped Echo VR level.

    3D View > sidebar > EVR Level > Add to Level

Exports the selection, then hands it to `scripts/evr_add_blend_models.py`, which
grows the level's tables and stages the result into `input-pcvr`. Repack from
there and the geometry is in the map.

## The two things this file exists to get right

**Axes.** `evr_lighting._to_blender((x, y, z))` is `(x, -z, y)`, so the inverse
-- Blender to game -- is `(bx, bz, -by)`. World space is baked into the vertices
so the placement transform stays identity.

**⛔ WINDING.** That axis change is a ROTATION (determinant +1), so it preserves
Blender's triangle winding -- but the engine's front face is the opposite sense.
Copy the winding straight through and every triangle ships inside-out: the model
then draws only where you are looking at its BACK faces, so it appears from one
side of the map and vanishes from the other, and up close you see the insides of
the walls. It looks for all the world like a culling or LOD bug, and it is not.
`reversed(tri.loops)` is the whole fix.

Vertices are emitted per LOOP, not per mesh vertex, so UVs and split normals are
per corner as the engine wants; `split_u16` on the other side then breaks any
group over the 65,535-vertex u16 index limit.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy.types import Operator, Panel, PropertyGroup

#: `(bx, by, bz) -> (x, y, z)`. See the module header.
def to_game(v):
    return (v.x, v.z, -v.y)


def _repo_script() -> Path | None:
    """`scripts/evr_add_blend_models.py`, from the add-on's own location."""
    here = Path(__file__).resolve()
    for base in list(here.parents)[:8]:
        cand = base / "scripts" / "evr_add_blend_models.py"
        if cand.is_file():
            return cand
    return None


# ── export ──────────────────────────────────────────────────────────────────
def export_selection(context, out_dir: Path, objects=None) -> dict:
    """Write the selection as per-material geometry, images and lights.

    Returns a summary. Geometry is grouped by material because one model per
    material is what the engine's asset/instance pair wants -- a model carries
    one material, and every extra group is another nine-link chain to grow.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    objects = list(objects if objects is not None else context.selected_objects)
    meshes = [o for o in objects if o.type == "MESH"]
    lights = [o for o in objects if o.type == "LIGHT"]

    groups = defaultdict(lambda: {"pos": [], "nrm": [], "uv": [], "idx": [],
                                  "objs": []})
    dg = context.evaluated_depsgraph_get()
    for o in meshes:
        slot = o.material_slots[0].material if o.material_slots else None
        mat = slot.name if slot else "none"
        g = groups[mat]
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        me.calc_loop_triangles()
        try:
            me.calc_normals_split()
        except Exception:                                      # noqa: BLE001
            pass
        uvl = me.uv_layers.active.data if me.uv_layers.active else None
        base = len(g["pos"]) // 3
        mw = o.matrix_world
        nm = mw.to_3x3()
        seen = {}
        for tri in me.loop_triangles:
            for li in tri.loops:
                if li in seen:
                    continue
                seen[li] = base + len(seen)
                lv = me.loops[li]
                co = to_game(mw @ me.vertices[lv.vertex_index].co)
                no = to_game((nm @ lv.normal).normalized())
                g["pos"] += list(co)
                g["nrm"] += list(no)
                if uvl:
                    uv = uvl[li].uv
                    g["uv"] += [uv.x, 1.0 - uv.y]
                else:
                    g["uv"] += [0.0, 0.0]
        for tri in me.loop_triangles:
            # ⛔ reversed -- see the module header. This is the whole
            # difference between a model you can see and one you cannot.
            g["idx"] += [seen[li] for li in reversed(tri.loops)]
        g["objs"].append(o.name)
        ev.to_mesh_clear()

    import struct
    manifest, images = [], {}
    for mat, g in sorted(groups.items()):
        stem = "".join(c if c.isalnum() or c == "_" else "_" for c in mat)
        for key, fmt in (("pos", "f"), ("nrm", "f"), ("uv", "f"), ("idx", "I")):
            (out_dir / ("%s_%s.bin" % (stem, key))).write_bytes(
                struct.pack("<%d%s" % (len(g[key]), fmt), *g[key]))
        row = {"material": mat, "stem": stem, "nverts": len(g["pos"]) // 3,
               "ntris": len(g["idx"]) // 3, "objects": g["objs"], "images": []}
        m = bpy.data.materials.get(mat)
        if m and m.use_nodes:
            for n in m.node_tree.nodes:
                if n.type == "TEX_IMAGE" and n.image:
                    row["images"].append(
                        {"image": n.image.name,
                         "colorspace": n.image.colorspace_settings.name})
                    images[n.image.name] = n.image
        manifest.append(row)

    written = []
    for name, im in images.items():
        data = bytes(im.packed_file.data) if im.packed_file else None
        if data is None:
            src = bpy.path.abspath(im.filepath)
            if src and Path(src).is_file():
                data = Path(src).read_bytes()
        if not data:
            continue
        ext = ".png" if data[:8] == b"\x89PNG\r\n\x1a\n" else (
            ".dds" if data[:4] == b"DDS " else ".jpg")
        safe = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
        (out_dir / (safe + ext)).write_bytes(data)
        written.append({"name": name, "file": safe + ext,
                        "colorspace": im.colorspace_settings.name})
    (out_dir / "images.json").write_text(json.dumps(written, indent=1),
                                         encoding="utf-8")

    out_lights = []
    for o in lights:
        d = o.data
        mwq = o.matrix_world.to_quaternion()
        import mathutils
        aim = mwq @ mathutils.Vector((0.0, 0.0, -1.0))
        kind = {"POINT": "POINT", "SPOT": "SPOT", "SUN": "SUN",
                "AREA": "POINT"}.get(d.type, "POINT")
        cmax = max(d.color) or 1.0
        intensity = (d.energy * cmax if kind == "SUN"
                     else d.energy / (4.0 * math.pi * cmax))
        rec = {"name": "blend_" + o.name.replace(".", "_").lower(),
               "type": kind, "color": list(d.color),
               "intensity": round(float(o.get("evr_light_intensity", intensity)), 6),
               "position": list(to_game(o.matrix_world.translation)),
               "direction": list(to_game(aim)),
               "range": float(getattr(d, "cutoff_distance", 0.0) or 0.0)}
        if kind == "SPOT":
            cone = float(getattr(d, "spot_size", 0.0) or 0.0)
            rec["cone"] = cone
            rec["cone_inner"] = cone * (1.0 - float(getattr(d, "spot_blend", 0.0)))
        out_lights.append(rec)
    (out_dir / "lights.json").write_text(json.dumps(out_lights, indent=1),
                                         encoding="utf-8")
    (out_dir / "geo.json").write_text(json.dumps(manifest, indent=1),
                                      encoding="utf-8")
    return {"groups": len(manifest), "objects": len(meshes),
            "verts": sum(r["nverts"] for r in manifest),
            "tris": sum(r["ntris"] for r in manifest),
            "images": len(written), "lights": len(out_lights)}


# ── properties ──────────────────────────────────────────────────────────────
class EVRAddModelsProps(PropertyGroup):
    extract_dir: StringProperty(
        name="Extract", subtype="DIR_PATH",
        description="The flat game extract (<type hash>/<resource hash>)")  # type: ignore
    input_dir: StringProperty(
        name="input-pcvr", subtype="DIR_PATH",
        description="The tools' staging directory the repacker reads")  # type: ignore
    level: StringProperty(
        name="New Level", default="mpl_arena_custom",
        description="The NEW level to build. Must not already exist -- this "
                    "tool refuses to modify a shipped level, because its "
                    "resources are keyed by name hash and are GLOBAL: editing "
                    "one changes the base game for every map that loads it, "
                    "and there is nothing to ship but a diff against someone "
                    "else's install")  # type: ignore
    clone_from: StringProperty(
        name="Clone From", default="mpl_arena_a",
        description="The shipped level to copy first. Its ~90 files are "
                    "written out under the new name hash (only four carry a "
                    "self-reference, and nothing outside the level indexes "
                    "it), and the models land on the COPY. The original is "
                    "never opened for writing")  # type: ignore
    prefix: StringProperty(
        name="Prefix", default="blend_",
        description="Name prefix for the new models, materials and textures. "
                    "New assets are keyed by a FRESH hash so no stock asset is "
                    "overwritten")  # type: ignore
    collision: BoolProperty(
        name="Collision", default=False,
        description="Author CPhysics + CBVH too. Off means the geometry renders "
                    "and the player passes straight through it")  # type: ignore
    visibility: EnumProperty(
        name="Visibility",
        items=[("counted", "Replace (counted-92)",
                "Replace the level's precomputed visibility set so the new "
                "geometry is never culled. It is the only way it draws today -- "
                "but it also throws away the level's own culling, so the map "
                "stops going black when you leave it"),
               ("keep", "Keep the level's PVS",
                "Leave the visibility set alone. The new geometry is not in it, "
                "so it will be culled and you will not see it")],
        default="counted")  # type: ignore
    lod_scale: FloatProperty(
        name="LOD Distance", default=1000.0, min=1.0,
        description="loddistancescales for the new instances. Every shipped "
                    "instance carries 1.0, but a grafted model is outside the "
                    "level's boxtree and fades out with distance; scaling this "
                    "holds it at full detail")  # type: ignore
    texconv: StringProperty(
        name="texconv", subtype="FILE_PATH",
        description="texconv.exe, to encode the materials' images to BC7")  # type: ignore


# ── operators ───────────────────────────────────────────────────────────────
class EVR_OT_add_models(Operator):
    bl_idname = "evr.add_models_to_level"
    bl_label = "Add to Level"
    bl_description = ("Export the selected objects and graft them into the "
                      "level, staging the result into input-pcvr")
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return any(o.type in {"MESH", "LIGHT"} for o in context.selected_objects)

    def execute(self, context):
        p = context.scene.evr_add_models
        script = _repo_script()
        if script is None:
            self.report({"ERROR"}, "scripts/evr_add_blend_models.py not found "
                                   "next to the add-on")
            return {"CANCELLED"}
        extract = bpy.path.abspath(p.extract_dir).rstrip("\\/")
        out = bpy.path.abspath(p.input_dir).rstrip("\\/")
        if not (extract and Path(extract).is_dir()):
            self.report({"ERROR"}, "set a valid Extract directory")
            return {"CANCELLED"}
        if not out:
            self.report({"ERROR"}, "set the input-pcvr directory")
            return {"CANCELLED"}
        if not p.level.strip():
            self.report({"ERROR"}, "name the new level")
            return {"CANCELLED"}
        if p.level.strip().lower() == p.clone_from.strip().lower():
            self.report({"ERROR"}, "the new level must not be the one it is "
                                   "cloned from")
            return {"CANCELLED"}

        geo = Path(bpy.app.tempdir) / ("evr_add_" + p.level)
        try:
            summary = export_selection(context, geo)
        except Exception as exc:                               # noqa: BLE001
            self.report({"ERROR"}, "export failed: %s" % exc)
            return {"CANCELLED"}
        if not summary["groups"] and not summary["lights"]:
            self.report({"ERROR"}, "nothing to add -- select meshes or lights")
            return {"CANCELLED"}

        cmd = [sys.executable, str(script), "--geo", str(geo),
               "--level", p.level.strip(), "--dir", extract, "--out", out,
               "--prefix", p.prefix, "--visibility", p.visibility,
               "--lod-scale", str(p.lod_scale)]
        if p.clone_from.strip():
            cmd += ["--clone-from", p.clone_from.strip()]
        if p.collision:
            cmd.append("--collision")
        tex = bpy.path.abspath(p.texconv)
        if tex and Path(tex).is_file():
            cmd += ["--texconv", tex]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace")
        except OSError as exc:
            self.report({"ERROR"}, "could not run the grafter: %s" % exc)
            return {"CANCELLED"}
        for line in (proc.stdout or "").splitlines()[-4:]:
            if line.strip():
                self.report({"INFO"}, line.strip())
        if proc.returncode != 0:
            tail = [x for x in (proc.stderr or "").splitlines() if x.strip()]
            self.report({"ERROR"}, tail[-1] if tail else
                        "grafter exited %d" % proc.returncode)
            return {"CANCELLED"}
        self.report({"INFO"}, "%d group(s), %d verts, %d tris, %d image(s), "
                              "%d light(s) -> %s"
                    % (summary["groups"], summary["verts"], summary["tris"],
                       summary["images"], summary["lights"], out))
        return {"FINISHED"}


class EVR_OT_add_models_export_only(Operator):
    bl_idname = "evr.add_models_export"
    bl_label = "Export Only"
    bl_description = ("Write the export the grafter consumes, without touching "
                      "the level. For inspecting what would be added")
    bl_options = {"REGISTER"}
    directory: StringProperty(subtype="DIR_PATH")            # type: ignore

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        try:
            s = export_selection(context, Path(self.directory))
        except Exception as exc:                               # noqa: BLE001
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, "%d group(s), %d verts, %d tris, %d image(s), "
                              "%d light(s)"
                    % (s["groups"], s["verts"], s["tris"], s["images"],
                       s["lights"]))
        return {"FINISHED"}


# ── panel ───────────────────────────────────────────────────────────────────
class EVR_PT_add_models(Panel):
    bl_label = "Add Models"
    bl_idname = "EVR_PT_add_models"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "EVR Level"

    def draw(self, context):
        L = self.layout
        p = context.scene.evr_add_models
        box = L.box()
        box.label(text="Paths", icon="FILE_FOLDER")
        box.prop(p, "extract_dir")
        box.prop(p, "input_dir")
        box.prop(p, "texconv")
        box = L.box()
        box.label(text="New Level", icon="WORLD")
        box.prop(p, "clone_from")
        box.prop(p, "level")
        box.prop(p, "prefix")
        box.label(text="the original is never modified", icon="LOCKED")
        box = L.box()
        box.label(text="Options", icon="MODIFIER")
        box.prop(p, "collision")
        box.prop(p, "visibility")
        box.prop(p, "lod_scale")

        sel = [o for o in context.selected_objects if o.type in {"MESH", "LIGHT"}]
        box = L.box()
        box.label(text="Selection", icon="INFO")
        if not sel:
            box.label(text="select meshes or lights", icon="ERROR")
        else:
            nm = sum(1 for o in sel if o.type == "MESH")
            nl = sum(1 for o in sel if o.type == "LIGHT")
            box.label(text="%d mesh(es), %d light(s)" % (nm, nl))
            verts = sum(len(o.data.vertices) for o in sel if o.type == "MESH")
            box.label(text="%d vertices" % verts)
            if verts > 65535:
                box.label(text="over the u16 limit - will be split", icon="INFO")
        col = L.column(align=True)
        col.operator("evr.add_models_export", icon="EXPORT")
        col.scale_y = 1.3
        col.operator("evr.add_models_to_level", icon="ADD")


CLASSES = (EVRAddModelsProps, EVR_OT_add_models, EVR_OT_add_models_export_only,
           EVR_PT_add_models)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.evr_add_models = PointerProperty(type=EVRAddModelsProps)


def unregister():
    del bpy.types.Scene.evr_add_models
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
