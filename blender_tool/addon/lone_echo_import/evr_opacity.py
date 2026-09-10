"""Set the opacity of imported materials, so the change actually takes.

    3D View > sidebar > EVR Level > Opacity

## Why the Principled slider does nothing

An imported material's alpha is a NODE CHAIN, not a value:

    alpha = albedovertex.a * ... * albedomap.a * ... * alphamap * k_alpha

`material_builder` multiplies whatever terms the material carries and links the
product into `Principled BSDF > Alpha`. **A linked socket ignores its
`default_value`**, so dragging the BSDF's Alpha slider changes a number nothing
reads -- the edit is stored and has no effect, which is exactly what "changing
opacity doesn't stick" looks like.

The knob that works is the `k_alpha` multiply at the end of that chain, named
`material_builder.OPACITY_NODE`. That node is now created on every material with
an alpha chain (it used to be skipped when `k_alpha == 1.0`, leaving nothing to
turn), so this operator has something to set on anything imported since.

Materials with NO alpha chain have an unlinked Alpha socket, and there the
`default_value` is the right place to write -- both cases are handled.

⚠ Opacity only shows if the surface is actually blended. An OPAQUE material
ignores alpha however it is driven, so `set_opacity` switches the render method
to blended when asked for less than 1.0, and says so in the report rather than
leaving a silent no-op.
"""

from __future__ import annotations

import bpy
from bpy.props import BoolProperty, FloatProperty
from bpy.types import Operator, Panel

from .material_builder import OPACITY_NODE


def _blend(mat, on: bool) -> bool:
    """Put the material on the blended pass. Returns True if it changed.

    `blend_method` is a dead alias on 4.2+: only `surface_render_method`
    decides, and only `'BLENDED'` gives real transparency.
    """
    changed = False
    if hasattr(mat, "surface_render_method"):
        want = "BLENDED" if on else "DITHERED"
        if mat.surface_render_method != want:
            mat.surface_render_method = want
            changed = True
    elif hasattr(mat, "blend_method"):                       # 4.1 and older
        want = "BLEND" if on else "OPAQUE"
        if mat.blend_method != want:
            mat.blend_method = want
            changed = True
    return changed


def set_opacity(materials, value: float, force_blend: bool = True) -> dict:
    """Set `value` on every material's opacity knob. Returns a report."""
    node_hits = slider_hits = blended = skipped = 0
    for mat in materials:
        if mat is None or not mat.use_nodes:
            skipped += 1
            continue
        node = mat.node_tree.nodes.get(OPACITY_NODE)
        if node is not None and len(node.inputs) > 1:
            # input 0 is the chain, input 1 is k_alpha -- write the constant
            target = node.inputs[1] if not node.inputs[1].is_linked else None
            if target is None and not node.inputs[0].is_linked:
                target = node.inputs[0]
            if target is not None:
                target.default_value = value
                node_hits += 1
            else:
                skipped += 1
                continue
        else:
            bsdf = next((n for n in mat.node_tree.nodes
                         if n.type == "BSDF_PRINCIPLED"), None)
            sock = bsdf.inputs.get("Alpha") if bsdf else None
            if sock is None or sock.is_linked:
                # linked with no opacity node: an older import, before the
                # k_alpha multiply was created unconditionally. Re-import to
                # get the knob rather than editing a socket nothing reads.
                skipped += 1
                continue
            sock.default_value = value
            slider_hits += 1
        if force_blend and _blend(mat, value < 1.0):
            blended += 1
    return {"node": node_hits, "slider": slider_hits, "blended": blended,
            "skipped": skipped}


def _materials(context, selected_only: bool):
    objects = context.selected_objects if selected_only else context.scene.objects
    seen, out = set(), []
    for o in objects:
        for slot in getattr(o, "material_slots", ()):
            m = slot.material
            if m is not None and m.name not in seen:
                seen.add(m.name)
                out.append(m)
    return out


class EVR_OT_set_opacity(Operator):
    bl_idname = "evr.set_opacity"
    bl_label = "Apply Opacity"
    bl_description = ("Set the opacity of the selected objects' materials. "
                      "Writes the k_alpha node the alpha chain actually reads, "
                      "not the Principled slider, which a linked socket ignores")
    bl_options = {"REGISTER", "UNDO"}

    value: FloatProperty(name="Opacity", default=1.0, min=0.0, max=1.0,
                         subtype="FACTOR")                    # type: ignore
    selected_only: BoolProperty(name="Selected Only", default=True)  # type: ignore
    force_blend: BoolProperty(
        name="Switch to blended", default=True,
        description="An OPAQUE material ignores alpha however it is driven, so "
                    "put it on the blended pass when opacity is below 1")  # type: ignore

    def execute(self, context):
        mats = _materials(context, self.selected_only)
        if not mats:
            self.report({"WARNING"}, "no materials on the selection")
            return {"CANCELLED"}
        r = set_opacity(mats, self.value, self.force_blend)
        msg = "opacity %.2f on %d material(s)" % (
            self.value, r["node"] + r["slider"])
        if r["blended"]:
            msg += ", %d switched to blended" % r["blended"]
        if r["skipped"]:
            msg += ", %d skipped (re-import for the opacity node)" % r["skipped"]
        self.report({"INFO"}, msg)
        return {"FINISHED"}


class EVR_PT_opacity(Panel):
    bl_label = "Opacity"
    bl_idname = "EVR_PT_opacity"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "EVR Level"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        L = self.layout
        mats = _materials(context, True)
        L.label(text="%d material(s) on the selection" % len(mats),
                icon="MATERIAL")
        col = L.column(align=True)
        op = col.operator("evr.set_opacity", text="50%")
        op.value = 0.5
        op = col.operator("evr.set_opacity", text="25%")
        op.value = 0.25
        op = col.operator("evr.set_opacity", text="Opaque (100%)")
        op.value = 1.0
        L.label(text="the BSDF Alpha slider is inert once linked",
                icon="INFO")


CLASSES = (EVR_OT_set_opacity, EVR_PT_opacity)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
