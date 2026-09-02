"""Per-submesh vertex TINT, from the `vertex_tints.json` sidecar.

## What this fixes

`mpl_combat_combustion`'s water imports WHITE and unlit while in game it is
blue and glowing. Its material cannot explain the blue: both emissive maps are
greyscale facet patterns, the material resource carries no property words, and
`bakecolor` is `[1, 1, 1, 1]`. The colour lives in the GEOMETRY -- a flat BGRA
value in stream 0 that the mesh decoder steps over on its way to the UVs (see
`scripts/evr_vertex_color.py`).

Applying it multiplies the surface's colour, so the greyscale emissive facets
become blue facets and the surface glows blue instead of glowing white.

## Where it is applied

Wherever a submesh carries a constant tint that is neither WHITE nor BLACK.

⛔ An earlier version gated this on "the material samples no albedo", reasoning
that only white-fallback surfaces could safely be touched. That was wrong: it
skipped `mpl_combat_combustion`'s orange lava -- 20 submeshes at
`(1.0, 0.75, 0.25)` on `eMTForwardOpaque`, which all sample an albedo AND are
plainly orange in game. So the tint multiplies whatever colour the material
produces, albedo included, which is what a vertex colour does in the engine.

⛔ A later version then applied BLACK as well, on the strength of one case:
`mpl_lobby_b2`'s 825 x 845 m skybox enclosure carries `(0, 0, 0)` and its
skymap **is** black in game while importing white. That generalisation does not
survive contact with the corpus. Counting the levels:

    mpl_arena_a              188 of 244 constant tints are BLACK  (77%)
    mpl_combat_combustion     61 of 540                           (11%)

and `mpl_arena_a`'s black rows include ordinary lit architecture -- i1209,
i1250 and i1831 among them -- which a black multiply erases completely. A lane
that reads black across three quarters of a level is not painting three
quarters of that level black; black is this lane's UNSET value, and the lobby
skybox is a surface that happens to look the same either way.

⚠ The alpha byte does NOT separate the two cases. Every constant tint in every
level measured carries `A = 255`, black ones included -- so an unset lane is
`0xff000000`, not a zero-filled word, and there is no in-band flag to test.
Black is therefore refused on the same footing as white: as a value that cannot
be told apart from "no tint", not as a value known to mean nothing.

⚠ Still only submeshes whose every vertex agrees; `evr_vertex_color` omits the
rest rather than averaging them.
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy   # type: ignore

SIDECAR_NAME = "vertex_tints.json"
SIDECAR_FORMAT = "evr_vertex_tints"

#: Within this of 1.0 on every channel, a multiply changes nothing.
WHITE_EPSILON = 1e-6
#: Within this of 0.0 on every channel, a multiply erases the surface.
BLACK_EPSILON = 1e-6
#: Channel spread above which a colour is a HUE rather than a brightness.
CHROMATIC_SPREAD = 0.05

_COLOUR_CHANNELS = frozenset({"base_color", "albedo", "emission", "emissive"})
_ALBEDO_CHANNELS = frozenset({"base_color", "albedo"})


def load(package) -> dict | None:
    root = Path(package)
    if root.is_file():
        root = root.parent
    path = root / SIDECAR_NAME
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if doc.get("format") == SIDECAR_FORMAT else None


def material_has_albedo(spec) -> bool:
    """Does this material sample a colour texture of its own?"""
    if not spec:
        return False
    channels = spec.get("channels") or {}
    keys = set(channels) if isinstance(channels, (dict, list, set)) else set()
    if keys & _ALBEDO_CHANNELS:
        return True
    return any("albedo" in role for role in (spec.get("role_textures") or {}))


def is_chromatic(rgb) -> bool:
    """Does this colour claim a HUE, as opposed to a brightness?

    Grey (0.58, 0.58, 0.58) and white are brightness factors and say nothing
    about hue; pink (1.0, 0.21, 0.44) does. The spread between the strongest
    and weakest channel separates them.
    """
    if not rgb or len(rgb) < 3:
        return False
    vals = [float(c) for c in rgb[:3]]
    return (max(vals) - min(vals)) > CHROMATIC_SPREAD


def material_owns_its_colour(entry) -> bool:
    """Has the MATERIAL already declared a hue of its own?

    ⛔ Two earlier gates were tried here and both were wrong -- see the module
    docstring. This is not either of them. "Skips an albedo-sampling material"
    failed because `mpl_combat_combustion`'s water and lava sample an albedo and
    genuinely take their colour from the tint; that water's own `bakecolor` is
    `[1, 1, 1, 1]`, i.e. NOT chromatic, so it still applies under this rule.
    What this catches is the opposite case: a material that already carries a
    hue, where multiplying a second hue over it destroys both. `mpl_arena_a`
    mesh 148/149 are pink (1.0, 0.214, 0.444) under a blue (0.0, 0.447, 1.0)
    tint, and the product is a dark blue that matches neither.
    """
    if not isinstance(entry, dict):
        return False
    # Either shape: the materials.json ENTRY carries `base_color`, its inner
    # `spec` carries the same quantity as `base_color_factor`. Accepting both
    # means a caller that hands over one or the other cannot silently disable
    # the gate -- which is exactly how it came to be passed and never read.
    colour = entry.get("base_color")
    if colour is None:
        colour = entry.get("base_color_factor")
    if colour is None:
        colour = (entry.get("spec") or {}).get("base_color_factor")
    return is_chromatic(colour)


def is_applicable(rgba) -> bool:
    """Would this tint change anything, and is it safe to believe?

    WHITE is refused because a multiply by white is a no-op. BLACK is refused
    because it is indistinguishable from an unset lane and a black multiply
    erases the surface -- see the module docstring for the counts that settle
    it.
    """
    if not rgba or len(rgba) < 3:
        return False
    if all(abs(c - 1.0) <= WHITE_EPSILON for c in rgba[:3]):
        return False
    return not all(abs(c) <= BLACK_EPSILON for c in rgba[:3])


def _tint_material(material, rgba):
    """Multiply `material`'s colour output by `rgba`. Returns True if changed."""
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return False
    bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return False
    changed = False
    for socket_name in ("Emission Color", "Base Color"):
        socket = bsdf.inputs.get(socket_name)
        if socket is None:
            continue
        if socket.is_linked:
            upstream = socket.links[0].from_socket
            mix = tree.nodes.new("ShaderNodeMix")
            mix.data_type = "RGBA"
            mix.blend_type = "MULTIPLY"
            mix.label = "vertex tint"
            mix.location = (bsdf.location[0] - 260, bsdf.location[1] - 320)
            mix.inputs["Factor"].default_value = 1.0
            tree.links.new(upstream, mix.inputs[6])          # A (colour)
            mix.inputs[7].default_value = (rgba[0], rgba[1], rgba[2], 1.0)
            tree.links.new(mix.outputs[2], socket)
            changed = True
        elif socket_name == "Emission Color":
            # unlinked emission colour: scale the constant directly
            base = list(socket.default_value)
            socket.default_value = (base[0] * rgba[0], base[1] * rgba[1],
                                    base[2] * rgba[2], base[3])
            changed = True
    if changed:
        material["le_vertex_tint"] = [round(c, 6) for c in rgba[:4]]
    return changed


def apply_tints(doc: dict, package, objects_by_mesh: dict,
                materials_by_mesh: dict) -> dict:
    """Tint the surfaces named in the sidecar. Returns a summary.

    `materials_by_mesh` maps a MESH index to its materials.json entry. The
    sidecar rows are keyed by mesh, so a matidx-keyed map cannot be joined to
    them -- which is why the previous parameter was passed but never read.
    """
    rows = doc.get("tints") or []
    if not rows:
        return {"applied": 0}

    applied = skipped_owns_colour = skipped_identity = no_object = 0
    variants: dict = {}
    for row in rows:
        rgba = row.get("rgba")
        if not is_applicable(rgba):
            skipped_identity += 1
            continue
        entry = (materials_by_mesh or {}).get(int(row.get("mesh", -1)))
        if is_chromatic(rgba) and material_owns_its_colour(entry):
            skipped_owns_colour += 1
            continue
        objects = objects_by_mesh.get(int(row.get("mesh", -1))) or ()
        if not objects:
            no_object += 1
            continue
        first = objects[0]
        if not first.material_slots or first.material_slots[0].material is None:
            no_object += 1
            continue
        base_material = first.material_slots[0].material

        key = (base_material.name, tuple(round(c, 6) for c in rgba[:3]))
        variant = variants.get(key)
        if variant is None:
            variant = base_material.copy()
            variant.name = "%s_tint" % base_material.name
            if not _tint_material(variant, rgba):
                bpy.data.materials.remove(variant)
                skipped_identity += 1
                continue
            variants[key] = variant
        for obj in objects:
            if not obj.material_slots:
                continue
            # OBJECT-linked so the mesh datablock stays shared, exactly as
            # `evr_texture_arrays` does for per-instance poster slices.
            slot = obj.material_slots[0]
            slot.link = "OBJECT"
            slot.material = variant
            obj["le_vertex_tint"] = [round(c, 6) for c in rgba[:3]]
            applied += 1

    # ⚠ NOT `skipped_has_albedo`. That key named the gate that was REMOVED
    # (skip when the material samples an albedo), and keeping it as an alias
    # for this one would report a different rule under the old name.
    return {"applied": applied, "variants": len(variants),
            "skipped_material_owns_colour": skipped_owns_colour,
            "skipped_identity_or_black": skipped_identity,
            "no_object": no_object}
