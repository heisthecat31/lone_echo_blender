"""Build a Principled BSDF material from a .lemesh material spec.

Targets Blender 4.x / 5.x (developed and RNA-probed on **5.1.1**). Handles missing
texture files gracefully: the material is still created with scalar defaults and the
intended texture hash recorded as a custom property, so nothing is silently lost.

Evidence labels used in the comments below:
  `shader-confirmed`  matches the arithmetic the engine's own shaders and
                      material asset schema perform
  `name-confirmed`    matches the engine's own type / field / enum names
  `stream-confirmed`  decoded from shipped archive bytes
  `engine-confirmed`  Blender RNA probe on 5.1.1, value read back after writing
  `inferred`          reasoned, not proven

Two things in here are load-bearing and easy to get wrong:

1. **`image.alpha_mode`** (`engine-confirmed (Blender 5.1.1)`). Blender defaults to
   `'STRAIGHT'`, which *multiplies the RGB by the alpha channel* on load. Measured on
   `layer0_composite_diffuse` texel (431,11) of `6f51c495d957d59a.dds` (BC3_UNORM_SRGB):
   raw sRGB8 `(192,151,0,28)` -> Image Texture `Color` out was `(0.007499, 0.005182, 0)`
   under `'STRAIGHT'` versus the ground-truth `(0.527115, 0.309469, 0)` -- **70x too
   dark**; a texel with `alpha == 0` came out pure black. `'CHANNEL_PACKED'` reproduced
   the DDS bit-exactly. Every RAD texture packs alpha as an independent signal
   (`layers[i].alpha = k_composite_diffuse[i].w * ...` -- the albedo is *not*
   premultiplied, `shader-confirmed`), so `'CHANNEL_PACKED'` is right for all of
   them. Alpha itself is always linear regardless of the RGB colour space.

2. **`blend_method` is a dead alias on 4.2+** (`engine-confirmed (Blender 5.1.1)`):
   writing `OPAQUE`, `CLIP` or `HASHED` all read back as `HASHED` and collapse to
   `surface_render_method = 'DITHERED'`; only `BLEND` gives `'BLENDED'`. So the old
   `mat.blend_method = "CLIP"` could never clip. The pass is driven from
   `surface_render_method` here, and a cutout is a `Math(GREATER_THAN)` node.
"""

from __future__ import annotations

from pathlib import Path

import bpy   # type: ignore

# ⚠ `lightmap_builder` is imported LAZILY, inside `lightmap_variant`, and not at
# module scope. `tests/test_material_builder_nodes.py` loads this file straight
# off disk with `spec_from_file_location` and a stub `bpy` — which works only
# while this module has no package-relative import at module scope.


# ---------------------------------------------------------------------------
# Pure-python decision layer (no bpy) -- unit-tested by
# tests/test_material_builder_nodes.py without Blender.
# ---------------------------------------------------------------------------

# CGMaterial::EMaterialType -- the render PASS (`SGMaterialData +0x2a`).
# Values `stream-confirmed`; names `name-only`. docs/MATERIALS.md 2a.
MATTYPE_OPAQUE = frozenset({0, 1})            # eMTDeferredOpaque, eMTForwardOpaque
MATTYPE_ALPHA_TESTED = 9                      # eMTAlphaTested   -> clip()
MATTYPE_BLEND = frozenset({2, 3, 4, 16})      # Forward/LowRes/Solid transparent, PostAA
MATTYPE_REFRACTION = 11                       # eMTRefraction
# ★ eMTSkirt is the DECAL pass (`eSkirts`), not an opaque one. Treating it as
# opaque suppressed the alpha chain and rendered Jack's shoulder/thigh patches as
# solid black cards -- docs/MATERIALS.md.
MATTYPE_SKIRT = 10                            # eMTSkirt   -- decal pass

# EBlendMode -- the blend EQUATION (`SGMaterialData +0x28`).
BLENDMODE_OPAQUE = 0
BLENDMODE_BLEND = frozenset({7, 11, 12})      # transparent, premultiplied, translucent
BLENDMODE_ADDITIVE = frozenset({1, 8})        # additive, linear dodge -- LOSSY in EEVEE
BLENDMODE_SKIRT = 10                          # eBlendSkirt

# `k_alpha_threshold` authored default (`name-confirmed`, the UberMaterial
# declaration).
DEFAULT_ALPHA_THRESHOLD = 0.5
# `k_refractive_index` is authored 1.0; 1.45 is Blender's glass convention and is only
# used when the material carries no index at all (`inferred`).
DEFAULT_IOR = 1.45

# Formats with a real, independently-addressable alpha PLANE. Verified against the
# DXGI_FORMAT enum: BC1 = 70/71/72 (1-bit punchthrough only, EXCLUDED), BC2 = 73/74/75,
# BC3 = 76/77/78, BC4 = 79/80/81 (single channel, NO alpha), BC5 = 82/83/84,
# BC6H = 94/95/96, BC7 = 97/98/99.
# ⚠ This set says whether `.a` EXISTS, not whether it is the signal -- for a dedicated
# alpha map the signal is `.r` in EVERY format (`alpha_component_of`).
DXGI_HAS_ALPHA_BLOCK = frozenset({
    2, 3, 4,                                   # R32G32B32A32_*
    10, 11, 12, 13,                            # R16G16B16A16_*
    24, 25,                                    # R10G10B10A2_*
    27, 28, 29, 30, 31, 32,                    # R8G8B8A8_*
    65,                                        # A8_UNORM
    86,                                        # B5G5R5A1_UNORM
    73, 74, 75,                                # BC2
    76, 77, 78,                                # BC3
    87, 90, 91,                                # B8G8R8A8_*
    97, 98, 99,                                # BC7
})
# REMOVED, and why: 26 = R11G11B10_FLOAT (no alpha), 40 = D32_FLOAT (depth),
# 88 = B8G8R8X8_UNORM and 93 = B8G8R8X8_UNORM_SRGB (the X is padding, not alpha).
# ⚠ BC1 (70/71/72) is excluded deliberately: `stream-confirmed` over all 42 BC1
# textures in `exports/fixtures_mat3` (archive 0703fd2acd5803e9), 65,601,536 mip-0
# texels block-decoded, the punchthrough index 3 is selected ZERO times -- although
# 1,155,487 blocks (28.2%) sit in the `c0 <= c1` mode that allows it. Wiring `.a`
# there would wire a constant 1.0.

MESH_FLAG_DIFFUSE_VERTEX_COLOR = 0x2000       # CGMeshData eDiffuseVertexColor

# --- layer compositing (`shader-confirmed`, the engine's layer compositing) --
# `blend = saturate((vertex_blend - height) / fade) * saturate(mask.R * scale + offset)`
# then `BlendValue(lower, layer, blend * <channel>_blend_alpha, blend_mode)`.
# `BlendValue` gives the operator; 6 `eBlendTransparent` is the authored
# default and is a LERP, `(1 - m) * base + m * layer`.
LAYER_BLEND_LERP_MODES = frozenset({6, 10})   # transparent, detail-override
LAYER_BLEND_ADD_MODES = frozenset({1, 7})     # additive, linear dodge
DEFAULT_BLEND_MASK_COMPONENT = "R"            # `k_blend_mask[i].x`
# `blend_mask` sampler default is `common_white` (`name-confirmed`), so a
# layer with no mask texture bound samples 1.0 -- NOT 0.0.
DEFAULT_BLEND_MASK_VALUE = 1.0

# Blender 5.1.1 `ShaderNodeMix` socket indices (`engine-confirmed`: the named
# sockets are ambiguous -- "A" exists four times, once per data type -- so the
# index is the only safe accessor). FLOAT: Factor 0, A 2, B 3, Result outputs[0].
# RGBA:  Factor 0, A 6, B 7, Result outputs[2].
MIX_FLOAT_SOCKETS = (0, 2, 3, 0)
MIX_RGBA_SOCKETS = (0, 6, 7, 2)
# VECTOR: Factor 0, A 4, B 5, Result outputs[1] (`engine-confirmed` by
# `tests/blender_tangent_probe.py`, which reads the socket names back).
MIX_VECTOR_SOCKETS = (0, 4, 5, 1)

# `ShaderNodeVectorMath` input indices. The node always exposes three Vector
# inputs plus one Scale float, whatever the operation, and the named lookup is
# ambiguous for the same reason `ShaderNodeMix`'s is.
VECMATH_A, VECMATH_B, VECMATH_C, VECMATH_SCALE = 0, 1, 2, 3

# The identity tangent-space normal in the encoding a `Normal Map` node expects:
# (x, y, z) = (0, 0, 1) stored as (0.5, 0.5, 1.0). It is the `base.normal` term of
# the layer lerp whenever no lower layer binds a normal map -- see the `normal`
# block in `build_material`.
FLAT_TANGENT_NORMAL = (0.5, 0.5, 1.0, 1.0)


def _sat(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else float(v))


def layer_blend_of(spec: dict, layer) -> dict | None:
    """The `layers[i]["blend"]` record for layer index `layer`, or None.

    None for layer 0 (the base of `BlendLayers()`), for a manifest written before
    this key existed, and for a layer that gates nothing.
    """
    if not isinstance(layer, int) or layer <= 0:
        return None
    for entry in spec.get("layers") or []:
        if entry.get("index") == layer:
            blend = entry.get("blend")
            return blend if isinstance(blend, dict) else None
    return None


def blend_mask_offset_for(blend: dict, opts: dict | None = None) -> float:
    """`layerN_blend_mask_offset`, with the import-time override applied.

    The parameter is authored `-animatable true -softmin -1.0 -softmax 1.0`
    (`name-confirmed`, the material asset schema) and every shipped value in the
    corpus
    is `-1.0`, i.e. the layer is parked at its animated OFF extreme. The stored
    value is what the engine uses at load, so it is the default here; passing
    `opts["layer_blend_mask_offset"] = 0.0` shows the layer at its authored-on
    state instead. It is an override of a RUNTIME-ANIMATED value, not a fudge
    factor -- nothing else in this file may be nudged to make a render look nicer.
    """
    override = (opts or {}).get("layer_blend_mask_offset")
    if isinstance(override, (int, float)) and not isinstance(override, bool):
        return float(override)
    try:
        return float(blend.get("mask_offset", 0.0))
    except (TypeError, ValueError):
        return 0.0


def blend_mask_scale_for(blend: dict) -> float:
    try:
        return float(blend.get("mask_scale", 1.0))
    except (TypeError, ValueError):
        return 1.0


def blend_amount_bounds(blend: dict, opts: dict | None = None,
                        has_mask_texture: bool | None = None) -> tuple[float, float]:
    """(min, max) of `saturate(mask.R * scale + offset)` for this layer."""
    scale = blend_mask_scale_for(blend)
    offset = blend_mask_offset_for(blend, opts)
    if has_mask_texture is None:
        has_mask_texture = bool((blend.get("mask") or {}).get("file"))
    hi = _sat(scale + offset)
    lo = _sat(offset) if has_mask_texture else hi
    return lo, hi


def blend_amount_constant(blend: dict, opts: dict | None = None,
                          has_mask_texture: bool | None = None) -> float | None:
    """The blend amount when it is provably spatially constant, else None.

    Constant in three cases: no mask texture bound (the sampler returns
    `common_white` = 1.0), `saturate` pinned to 0 because `scale + offset <= 0`
    (the shipped `offset = -1` case -- the layer contributes nothing no matter
    what the mask contains), or pinned to 1 because `offset >= 1`.
    """
    lo, hi = blend_amount_bounds(blend, opts, has_mask_texture)
    return lo if lo == hi else None


def channel_blend_alpha(blend: dict, channel: str) -> float:
    """`layerN_<channel>_blend_alpha` -- the per-property scale on the mask."""
    try:
        return float((blend.get("channel_alpha") or {}).get(channel, 1.0))
    except (TypeError, ValueError):
        return 1.0


def blend_gates_channel(blend: dict | None, channel: str) -> bool:
    return bool(blend) and channel in (blend.get("gated_channels") or [])


def blend_for_channel(spec: dict, channels: dict, name: str) -> dict | None:
    """The layer-blend record that gates `channels[name]`, or None.

    `channels` is the merged view and keeps the LOWEST layer that provides each
    channel, so when it hands back a layer >= 1 no lower layer supplies that
    channel at all -- which is why the engine's
    `BlendValue(base, layer, m, eBlendTransparent) = (1-m)*base + m*layer`
    can be built here against a known `base` (0 for emission, the scalar
    fallback for the rest) instead of against a composited lower layer.
    """
    ch = (channels or {}).get(name)
    if not isinstance(ch, dict):
        return None
    layer = ch.get("blend_layer")
    if layer is None:
        layer = ch.get("layer")
    blend = layer_blend_of(spec, layer)
    return blend if blend_gates_channel(blend, name) else None


def blend_mask_component_of(blend: dict) -> str:
    comp = blend.get("mask_component")
    if isinstance(comp, str) and comp.upper() in ("R", "G", "B", "A"):
        return comp.upper()
    return DEFAULT_BLEND_MASK_COMPONENT


def k_alpha(spec: dict) -> float:
    """`k_alpha` -- the authored global alpha multiplier (`shader-confirmed`).

    B1: this was never applied, so `k_alpha = 0.25` with no opacity map rendered
    fully opaque. It is the last term of the engine's alpha chain.
    """
    try:
        v = float(spec.get("alpha", 1.0))
    except (TypeError, ValueError):
        return 1.0
    return min(max(v, 0.0), 1.0)


def alpha_threshold_for(spec: dict) -> float:
    v = spec.get("alpha_threshold")
    if v is None:
        return DEFAULT_ALPHA_THRESHOLD
    try:
        return float(v)
    except (TypeError, ValueError):
        return DEFAULT_ALPHA_THRESHOLD


def is_skirt(spec: dict) -> bool:
    """`eMTSkirt` / `eBlendSkirt` -- the engine's DECAL pass (`eSkirts`).

    Either field alone is enough: the two always co-occur in the shipped corpus
    (11 of 11 rows, `measured`) but the pass and the equation
    are independent u16s and a manifest may carry only one of them.
    """
    return (spec.get("mattype") == MATTYPE_SKIRT
            or spec.get("blend_mode") == BLENDMODE_SKIRT)


def resolve_render_mode(spec: dict) -> str:
    """-> 'OPAQUE' | 'CLIP' | 'BLEND'.

    Prefers an explicit `render_mode` from the manifest (the decoder owns that
    decision). Otherwise derives it from `mattype` (the pass) and falls back to
    `blend_mode` (the equation) -- B3: both were carried in the spec and never read.

    A material with `k_alpha < 1` and no other transparency evidence is upgraded to
    BLEND, otherwise applying `k_alpha` would be invisible (B1).

    ⚠ A skirt whose manifest says OPAQUE is REPAIRED to BLEND. That is the one
    place this function overrides the decoder, and it is deliberate: every
    `.lemesh` written before docs/MATERIALS.md carries
    `render_mode: "OPAQUE"` for the decal pass, and re-cooking every package to
    fix a picture is not a reasonable prerequisite. `le_mesh.materials.
    render_mode_for` now agrees, so fresh manifests never take this branch
    (asserted in tests/test_skirt_decal_alpha.py).
    """
    rm = spec.get("render_mode")
    if isinstance(rm, str) and rm.upper() in ("OPAQUE", "CLIP", "BLEND"):
        mode = rm.upper()
    else:
        mode = None
        mt = spec.get("mattype")
        if isinstance(mt, int):
            if mt == MATTYPE_ALPHA_TESTED:
                mode = "CLIP"
            elif mt in MATTYPE_BLEND or mt == MATTYPE_SKIRT:
                mode = "BLEND"
            elif mt in MATTYPE_OPAQUE or mt == MATTYPE_REFRACTION:
                mode = "OPAQUE"
        if mode is None:
            bm = spec.get("blend_mode")
            if isinstance(bm, int) and (bm in BLENDMODE_BLEND or bm in BLENDMODE_ADDITIVE
                                        or bm == BLENDMODE_SKIRT):
                mode = "BLEND"
            else:
                mode = "OPAQUE"
    if mode == "OPAQUE" and is_skirt(spec):
        return "BLEND"                      # stale-manifest repair, see docstring
    if mode == "OPAQUE" and k_alpha(spec) < 1.0:
        return "BLEND"
    return mode


def surface_render_method_for(render_mode: str) -> str:
    """EEVEE Next has exactly two methods (`engine-confirmed (Blender 5.1.1)`:
    enum items are `['DITHERED', 'BLENDED']`). A cutout is DITHERED plus a
    `Math(GREATER_THAN)` node -- there is no `CLIP` method to select.
    """
    return "BLENDED" if render_mode == "BLEND" else "DITHERED"


def is_lossy_blend(spec: dict) -> bool:
    """Additive / linear-dodge has no EEVEE equivalent; flag the approximation."""
    bm = spec.get("blend_mode")
    return isinstance(bm, int) and bm in BLENDMODE_ADDITIVE


def is_additive_blend(spec: dict) -> bool:
    """`eBlendAdditive` (1) / `eBlendLinearDodge` (8) — the framebuffer op is ADD.

    ⚠ `bool` is excluded explicitly: `isinstance(True, int)` is True and
    `True in {1, 8}` is True, so a `blend_mode` that arrived as a boolean would
    otherwise be read as `eBlendAdditive`. Same guard as `specular_ior_level_for`.
    """
    bm = spec.get("blend_mode")
    return isinstance(bm, int) and not isinstance(bm, bool) and bm in BLENDMODE_ADDITIVE


def additive_blend_enabled(opts: dict | None) -> bool:
    """`opts['additive_blend']` -- default ON.

    Off restores the pre-2026-08-05 behaviour, in which an `eBlendAdditive`
    material was merely TAGGED `le_blend_lossy` and then rendered as an ordinary
    Principled surface with `Alpha = 1` — i.e. **opaque**, the one thing an
    additive surface can never be.
    """
    if not opts:
        return True
    return bool(opts.get("additive_blend", True))


def skirt_alpha_enabled(opts: dict | None) -> bool:
    """`opts['skirt_alpha']` -- default ON.

    Off restores the pre-`jack-patch-layers` behaviour: the `eMTSkirt` DECAL pass
    rendered OPAQUE with its diffuse alpha ignored, i.e. every decal a solid card
    of the sheet's black backing. It exists so the before/after pictures are a
    CLI flag rather than an edit, exactly like `additive_blend`.
    """
    if not opts:
        return True
    return bool(opts.get("skirt_alpha", True))


#: the FOUR values `tangent.w` takes on disk. `s16n` maps int16 -> [-1, 1], so
#: this is a deliberate 2-bit quantisation: a SIGN and a MAGNITUDE.
#: `tests/test_vertex_streams.py` proves the set is exactly this and nothing else
#: over 40 packages / 509,266 vertices.
TANGENT_W_STATES = (-1.0, -0.5, 0.5, 1.0)


def tangent_w_meaning(w: float) -> dict:
    """What ONE `le_tangent_w` value means, both halves of it, or a refusal.

    ★ MEASURED 2026-08-05, both halves, `stream-confirmed`:

    **The SIGN is the bitangent handedness.** Over every character package on
    disk — `exports/chars`, 5 packages / 36 objects / **397,082 vertices** —
    `sign(w)` agrees with the handedness derived from the shipped UVs
    (`sign(dot(cross(N, T), B_uv))`, Lengyel accumulation, disk space, no V flip)
    on **397,082 of 397,082 vertices, 100.00 %**, and it agrees at that rate
    inside each of the four states separately (`scratch/tangent_w_handedness.py`).
    So `B = cross(N, T) * sign(w)` is the reconstruction, and it is not an
    assumption.

    **The MAGNITUDE tags a duplicated BACK-FACE SHELL.** Container:
    `exports/chars`; coverage: 5 character packages / **63 objects** carrying a
    4-component tangent. 26 carry BOTH magnitudes, 37 carry only `|w| = 1.0`, and
    **0 carry only 0.5** — there is never a back shell without a front, which is
    also what the earlier 136-object census recorded. In all 26 the two classes
    are exactly equal in size, and:

      * **109,400 of 109,400 (100.00 %)** `|w| = 0.5` vertices have a
        position-identical `|w| = 1.0` partner;
      * **109,317 of 109,400 (99.92 %)** of those pairs have exactly NEGATED
        normals;
      * only **65.67 %** have exactly negated tangents — the back shell carries
        its OWN frame, it is not a sign flip of the front one;
      * every triangle appears twice, once per shell (7,302 × 2 on
        `64b4b5b2a0153f7e/obj000`, where the pair's tangents are 180.0° apart at
        p10 = median = p90).

    ⚠ The ORDER is not part of the law: 25 of the 26 lay the buffer out
    fronts-then-backs, `2fd6839161785e9c_3a80cdb80b7e60c0/obj001` interleaves
    them. Read the tag, never the index.

    ⇒ **the shader needs the sign and nothing else**: the back shell's flipped
    frame is already in that shell's own `normal` and `tangent` values, so a
    per-vertex `sign(w)` reconstructs both sides correctly with no special case.

    ⛔ A fifth value is refused, not rounded. `{"known": False}` is the caller's
    signal to fall back to Blender's own tangent and say so.
    """
    try:
        f = float(w)
    except (TypeError, ValueError):
        return {"known": False, "w": w, "sign": 0.0, "shell": "unknown"}
    if not any(abs(f - s) < 1e-3 for s in TANGENT_W_STATES):
        return {"known": False, "w": f, "sign": 0.0, "shell": "unknown"}
    return {"known": True, "w": f,
            "sign": 1.0 if f > 0 else -1.0,
            "shell": "front" if abs(f) > 0.75 else "back"}


def shipped_tangent_enabled(opts: dict | None) -> bool:
    """`opts['shipped_tangent']` -- default ON. R1.

    ON wires the SHIPPED tangent basis (`le_tangent` / `le_tangent_w`, written by
    `mesh_builder` on 913 of 913 objects) into the normal-map path. OFF restores
    Blender's UV-derived (mikktspace) tangent via `ShaderNodeNormalMap`, which is
    what every render in this tree before 2026-08-05 used.

    ★ Why it defaults ON, measured against Blender's ACTUAL tangent
    (`mesh.calc_tangents()`, i.e. mikktspace) by `tests/blender_tangent_probe.py`
    on `64b4b5b2a0153f7e` — 13 meshes, 277,336 loops, `engine-confirmed`:

      * on the two meshes that ship a duplicated BACK-FACE shell (`obj000`,
        `obj001`) the two bases are **median 93.1° apart, p90 179.8°, max
        180.0°, 50.6 % of loops past 15°** — mikktspace derives the back shell's
        frame from its reversed winding and lands exactly opposite the shipped
        one on every back-face loop;
      * on the single-shell meshes they are close — median 0.05-1.9°, p99
        1.1-22.1°, **1-3 % of loops past 15°**.

    ⚠ That second row CORRECTS the earlier tangent audit, which
    measured 20-25 % past 15° against a *naive area-weighted* UV tangent and said
    so: as an estimate of Blender's own error it was an over-estimate by an order
    of magnitude. The defect is real, but it is concentrated in the back shell,
    not spread across the body.

    ⛔ And the handedness is simply wrong without this. The importer flips V for
    Blender (`flip_v`), so Blender's `loop.bitangent_sign` agrees with
    `sign(le_tangent_w)` on **0.0-0.8 % of loops** on 11 of the 13 meshes (and on
    exactly 50.0 % of the two back-shell meshes, where the reversed winding flips
    it back on one shell). An inverted bitangent inverts the green channel of
    every tangent-space normal map. The shipped basis never consults the UV
    derivative, so it cannot inherit that.
    """
    if not opts:
        return True
    return bool(opts.get("shipped_tangent", True))


def additive_unrouted_color_enabled(opts: dict | None) -> bool:
    """`opts['additive_unrouted_color']` -- default OFF, deliberately.

    ⛔ It routes a bind whose ROLE IS UNKNOWN into a colour socket. That is a
    guess, so it is not the default and it is tagged `inferred` on the material.
    It exists because the alternative is unfalsifiable: Liv's obj001 FX cards
    bind exactly one texture (`liv_basesuit_fx_clr`) whose role no array in the
    corpus declares, and the only way to see whether that texture belongs on
    those cards is to render it both ways.
    """
    if not opts:
        return False
    return bool(opts.get("additive_unrouted_color", False))


def additive_unrouted_color_role(spec: dict, channels: dict) -> str | None:
    """The single unrouted bind an additive material could be adding, or None.

    Requires ALL of: additive blend mode; no routed colour channel at all; and
    exactly ONE unrouted role. More than one and there is nothing to choose
    between them, so it stays refused.
    """
    if not is_additive_blend(spec):
        return None
    if channels.get("base_color") or channels.get("emission"):
        return None
    unrouted = [r for r in (spec.get("unrouted_roles") or [])]
    if len(unrouted) != 1:
        return None
    tex = (spec.get("role_textures") or {}).get(unrouted[0])
    return unrouted[0] if tex else None


def image_alpha_mode(chan: dict | None, default: str = "CHANNEL_PACKED") -> str:
    """`image.alpha_mode` for one channel -- see the module docstring (fix 1).

    Honours an `alpha_mode` hint from the manifest when the decoder supplies one;
    otherwise CHANNEL_PACKED, because every RAD texture reads RGB and alpha as
    independent signals.
    """
    if chan:
        hint = chan.get("alpha_mode")
        if isinstance(hint, str) and hint.upper() in (
                "STRAIGHT", "PREMUL", "CHANNEL_PACKED", "NONE"):
            return hint.upper()
    return default


def alpha_component_of(chan: dict) -> str:
    """Which component of a dedicated alpha/opacity map is the signal.

    The decoder answers this outright now: `"component"` is `shader-confirmed`
    from `params.alphamap = k_alpha_map[i].x` for an alpha map, and from
    `layers[i].alpha = k_composite_diffuse[i].w` for the base-colour-derived
    case.

    ⛔ THE DXGI FORMAT IS NOT THE ANSWER. `layer1_alpha_map` ships in BC1_UNORM(71),
    BC1_UNORM_SRGB(72), BC3_UNORM_SRGB(78) and BC4_UNORM(80) in archive
    `0703fd2acd5803e9`, and the engine reads `.x` from all four. The old
    format-driven fallback survives only for manifests written before the key
    existed -- and even then the role name is consulted first.
    """
    comp = chan.get("component")
    if isinstance(comp, str) and comp.upper() in ("A", "R", "G", "B"):
        return comp.upper()
    # legacy manifest: `alpha_map` is R regardless of format, a base-colour-derived
    # alpha is A, and only then do we fall back to the format.
    role = str(chan.get("role_key", ""))
    if role.endswith("_alpha_map"):
        return "R"
    if chan.get("from_channel") == "base_color" or role.endswith("_composite_diffuse"):
        return "A"
    dxgi = chan.get("dxgi")
    return "A" if isinstance(dxgi, int) and dxgi in DXGI_HAS_ALPHA_BLOCK else "R"


def split_opacity_channels(channels: dict) -> tuple[dict | None, dict | None]:
    """-> (alpha_channel, transmission_channel).

    The new manifest contract separates `alpha` (a scalar opacity multiplier) from
    `transmission` (`opacity_map`, a float3 *tint*: `output.color.rgb += background *
    material.opacity`, `shader-confirmed`). Older manifests
    lump both into one `opacity` channel, so split it on the role name -- routing an
    `opacity_map` to Blender's Alpha socket makes coloured glass uniformly
    see-through instead of tinting what is behind it (findings 3a).
    """
    alpha = channels.get("alpha")
    trans = channels.get("transmission")
    legacy = channels.get("opacity")
    if legacy is not None:
        role = str(legacy.get("role_key", ""))
        if "opacity_map" in role:
            trans = trans or legacy
        else:                       # layerN_alpha_map and anything unlabelled
            alpha = alpha or legacy
    return alpha, trans


def alpha_channel_is_base_color(alpha_ch: dict | None, base_ch: dict | None) -> bool:
    """Is the manifest's `alpha` channel just the BASE COLOUR's own `.a`?

    `build_material_spec` derives `channels["alpha"]` by copying `base_color` and
    stamping `from_channel="base_color"`, so the two are the same sampler. Wiring
    both the derived channel AND `bc_node.outputs["Alpha"]` multiplies the alpha
    by ITSELF -- `engine-confirmed` on `bcb9caff4b7a4d37__ed972c98f19abfdc`
    (a local working file: the Alpha socket lists `0917328f9ecabf70.dds`
    twice, joined by a MULTIPLY). Harmless while every such base colour was BC1
    punch-through (0 or 1 square to themselves); NOT harmless for the BC3 decal
    sheets the skirt pass binds, where 0.5 would become 0.25.
    """
    if not alpha_ch or not base_ch:
        return False
    if alpha_ch.get("from_channel") == "base_color":
        return True
    return bool(alpha_ch.get("texture")
                and alpha_ch.get("texture") == base_ch.get("texture")
                and str(alpha_ch.get("component", "")).upper() == "A"
                and str(alpha_ch.get("role_key", "")) == str(base_ch.get("role_key", "")))


def uses_base_color_alpha(spec: dict, channels: dict) -> bool:
    """Is the base-colour texture's `.a` the opacity term?

    `layer0_composite_diffuse.a` IS the opacity (`shader-confirmed`). Honours an
    explicit `alpha_source` when present.

    ⚠ One exception, and only one: a SKIRT whose manifest says `alpha_source:
    "NONE"` was written by a decoder that called the decal pass opaque, so the
    stored answer is stale and the heuristic below is re-run instead. Every other
    stored `alpha_source` — including `"NONE"` — is still obeyed verbatim.
    """
    src = spec.get("alpha_source")
    if isinstance(src, str) and not (src.upper() == "NONE" and is_skirt(spec)):
        return src.upper() == "BASE_COLOR_ALPHA"
    bc = channels.get("base_color")
    if not bc:
        return False
    if "composite_diffuse" not in str(bc.get("role_key", "")):
        return False
    dxgi = bc.get("dxgi")
    if not (isinstance(dxgi, int) and dxgi in DXGI_HAS_ALPHA_BLOCK):
        return False
    # Only when the material is actually meant to be non-opaque: BC3 albedo is used
    # for plenty of fully opaque surfaces whose alpha block is all 0xFF or junk.
    return resolve_render_mode(spec) != "OPAQUE"


def roughness_is_sqrt(spec: dict, chan: dict | None) -> bool:
    """`composite_components.x` is *sqrt*roughness (`shader-confirmed`: the
    engine squares it, `roughness = s*s`)."""
    flag = spec.get("roughness_is_sqrt")
    if isinstance(flag, bool):
        return flag
    return bool(chan) and "composite_components" in str(chan.get("role_key", ""))


def ao_channel_of(spec: dict, chan: dict | None) -> str | None:
    """AO is `composite_components.y` (`shader-confirmed`)."""
    ao = spec.get("ao_channel")
    if isinstance(ao, str) and ao.upper() in ("R", "G", "B", "A"):
        return ao.upper()
    if chan and "composite_components" in str(chan.get("role_key", "")):
        return "G"
    return None


#: An emissive map is treated as ambient occlusion when the material has a base
#: colour to occlude (see the long note at the use site). That test is right for
#: a lot of level geometry and WRONG for anything with a real glow mask, so it
#: is narrowed by two facts that outrank it.
#:
#: A map that is mostly BLACK cannot be occlusion. AO is a [0,1] visibility
#: term where 1 = unoccluded, so a mostly-black map asserts near-total
#: occlusion everywhere -- nobody authors that.
#:
#: The threshold is a MAJORITY (more than half the map is black), not a number
#: fitted to the sample. The two populations do not overlap or come close;
#: measured over the reference models::
#:
#:     composite_components (the real AO map)   0.000 - 0.000   n=9
#:     albedo maps                              0.000 - 0.373   n=14
#:     emissive maps                            0.587 - 1.000   n=11
#:
#: Anything in 0.38-0.58 gives an identical answer, so this sits in an empty
#: gap rather than on a boundary.
EMISSIVE_BLACK_FRACTION = 0.50


def _binds_composite_components(spec: dict, channels: dict) -> bool:
    """Does this material carry its own `composite_components` texture?"""
    roughness = channels.get("roughness") or {}
    if "composite_components" in str(roughness.get("role_key", "")):
        return True
    return any("composite_components" in role
               for role in (spec.get("role_textures") or {}))


def emissive_is_ao(spec: dict, channels: dict) -> bool:
    """Should this material's emissive map be read as ambient occlusion?

    ⛔ Read the note at the use site before touching this. Two earlier attempts
    to refine the plain `has_albedo` test were reverted, both because they
    guessed from how the texture LOOKED: gating on colorspace (false premise --
    the masks are sRGB), then on per-texture saturation (these masks are
    greyscale, so it flips genuine AO the wrong way). Neither is used here.

    The two tests below are different in kind. The first is structural and
    comes from the shader; the second rejects a map that could not be an
    occlusion term whatever it depicts.
    """
    if not channels.get("emission"):
        return False
    # Nothing to occlude -- the original discriminator, verified against the
    # running game on `d09afd15b1c75c04` i1535.
    if not channels.get("base_color"):
        return False

    # ⛔ NOTHING TO OCCLUDE, TWICE OVER. Both tests below are STRUCTURAL --
    # they read what the material IS, not what its texture looks like, which is
    # the trap the two reverted attempts fell into.
    #
    #   * a TRANSPARENT surface has no ambient term to occlude. Ambient
    #     occlusion darkens light arriving at an opaque surface; a blended one
    #     is showing what is behind it.
    #   * a layer that binds a FLOWMAP beside its emissive is an ANIMATED
    #     GLOW. Occlusion does not flow.
    #
    # ⭐ `mpl_arena_a`'s geodesic panels are the case that found this.
    # `a759b750b7db7253` (9621 m2 over 8 objects) and `a759b750b7db7153`
    # (1264 m2) are both `eMTForwardTransparent` / BLEND, the second binds
    # `layer1_flowmap_map` beside its emissive, and both share emissive map
    # `031c12f11108f92d` -- a greyscale gradient averaging 0.381 with NO pure
    # black at all. `black_fraction == 0.0` sent them down the AO path, which
    # inverted a glow into Base Colour and left the panels unlit.
    #
    # ⚠ A mean of 0.381 is also far too dark to BE an occlusion map (those sit
    # near white), but brightness is exactly the kind of appearance test that
    # was reverted twice, so it is noted and NOT used. These two tests stand on
    # the material's declared type instead.
    #
    # Corpus-wide this moves 20 of the 117 materials the black-fraction test
    # calls AO: arena 4, combustion 8, dyson 5, lobby_b2 3.
    if resolve_render_mode(spec) != "OPAQUE" \
            or spec.get("mattype_name") == "eMTForwardTransparent":
        return False
    if channels.get("flowmap"):
        return False

    # ★ WHAT THE MAP ACTUALLY CONTAINS, where the extractor could measure it.
    # This decides BOTH ways and outranks the structural test below, so a
    # material carrying a genuine occlusion map in its emissive slot still
    # gets occlusion -- which is the failure the two reverted attempts caused.
    # Absent on sidecars written before this existed, in which case it simply
    # does not fire and the structural test decides.
    black = (channels.get("emission") or {}).get("black_fraction")
    if isinstance(black, (int, float)):
        return black < EMISSIVE_BLACK_FRACTION

    # ★ AO ALREADY HAS A HOME. `composite_components.y` IS the ambient
    # occlusion (`shader-confirmed`, see le_mesh/materials.py's
    # `ao_channel`). A material that binds that texture has its occlusion
    # accounted for, so reading the emissive map as a SECOND AO double-counts
    # it -- and inverting a glow mask into Base Colour punches the glowing
    # texels to black, which is the visible bug this fixes. 1282 of the 1347
    # emissive-binding materials in the reference extract are in this class.
    if _binds_composite_components(spec, channels):
        return False

    return True


# --- the two-lobe composite BRDF ------------------------------------------
# `le_mesh/materials.py::brdf_lobe_blend` carries the full derivation. Short
# version: RAD's composite path runs TWO specular lobes and weights them per
# texel with `brdfblends = (1 - components.z, components.z)`; this builder used
# to render lobe [0] at weight 1 and drop lobe [1], which on Liv's orange
# gel-coat over-drove F0 by 2.75x and under-drove roughness by 6.9x -- the
# "wet vinyl" look. Blender has one specular lobe, so the faithful single-lobe
# stand-in is the engine's own weighted combination of the two.

# `_SRGB` DXGI formats -- the same set as `le_mesh/materials.py::SRGB_DXGI`, kept
# local because this module is loaded standalone (no package-relative imports).
DXGI_SRGB = frozenset({29, 72, 75, 78, 91, 93, 99})


def dds_dxgi_format(path) -> int | None:
    """The `DXGI_FORMAT` of a DX10-header DDS, or None.

    148-byte read of a file that is ALREADY in the package -- no archive is
    opened. Needed because `composite_data0` is (correctly) never routed to a
    channel, so a manifest carries no `dxgi` for it, and reading an `_SRGB`
    specular map as linear would inflate F0 -- the exact class of error this
    whole change is fixing.
    """
    try:
        with open(str(path), "rb") as fh:
            head = fh.read(148)
    except OSError:
        return None
    if len(head) < 132 or head[:4] != b"DDS " or head[84:88] != b"DX10":
        return None
    return int.from_bytes(head[128:132], "little")


def derive_brdf_lobes(spec: dict, pkg_dir) -> dict | None:
    """Reconstruct the `brdf_lobes` record for a manifest written before it existed.

    Everything needed is already in such a manifest: the components texture is
    the `roughness` channel, the weight is its `.z`, and lobe [1]'s albedo hash is
    `role_textures["layer{N}_composite_data0"]` -- which the decoder records but
    deliberately leaves unrouted. The only thing missing is that texture's DXGI
    format, and that is read from the DDS header in the package.

    Returns None when the material has no composite specular+components pair on
    one layer, which is exactly when `brdfblends` is its `(1, 0)` default and the
    unweighted lobe [0] was already right.
    """
    channels = spec.get("channels") or {}
    sp, rg = channels.get("specular"), channels.get("roughness")
    if not isinstance(sp, dict) or not isinstance(rg, dict):
        return None
    if "composite_specular" not in str(sp.get("role_key", "")):
        return None
    if "composite_components" not in str(rg.get("role_key", "")):
        return None
    if sp.get("layer") != rg.get("layer"):
        return None
    layer = sp.get("layer") or 0
    rec = {
        "layer": layer,
        "weight_texture": rg.get("texture", ""),
        "weight_file": rg.get("file", ""),
        "weight_channel": "B",
        "weight0_expression": "1 - components.B",
        "lobe0_roughness_channel": rg.get("roughness_channel", "R"),
        "lobe1_roughness_channel": "A",
        "lobe1": None,
        "lobe1_absent_albedo": 0.0,
        "blend_roughness": False,
        "confidence": "shader-confirmed",
        "derived_from": "channels + role_textures (manifest predates brdf_lobes)",
    }
    tex = (spec.get("role_textures") or {}).get(f"layer{layer}_composite_data0") or ""
    ref = str(rg.get("file", "") or sp.get("file", ""))
    if tex and ref and rg.get("texture"):
        rel = ref.replace(str(rg.get("texture")), tex)
        try:
            exists = (pkg_dir / rel).exists()
        except Exception:
            exists = False
        if exists:
            dxgi = dds_dxgi_format(pkg_dir / rel)
            rec["lobe1"] = {
                "role_key": f"layer{layer}_composite_data0",
                "texture": tex,
                "file": rel,
                "dxgi": dxgi,
                "colorspace": "sRGB" if dxgi in DXGI_SRGB else "Non-Color",
                "alpha_mode": "CHANNEL_PACKED",
                "spec_albedo_scaled_by": "A",
                "packing": "specalbedo = rgb * a ; specintensity = a",
                "lobe": 1,
            }
            rec["blend_roughness"] = True
    return rec


def brdf_lobes_of(spec: dict, pkg_dir=None) -> dict | None:
    """The manifest's `brdf_lobes` record, or one derived for an old manifest."""
    rec = spec.get("brdf_lobes")
    if isinstance(rec, dict):
        return rec
    if pkg_dir is None:
        return None
    return derive_brdf_lobes(spec, pkg_dir)


def lobe_blend_enabled(opts: dict | None) -> bool:
    """`opts['brdf_lobe_blend']` -- default ON.

    Off restores the pre-fix single-lobe-at-full-weight look, which is what
    every render in `exports/hero` before 2026-08 was made with.
    """
    if not opts:
        return True
    return bool(opts.get("brdf_lobe_blend", True))


def lobe_zero_roughness_gate(opts: dict | None) -> bool:
    """`opts['brdf_lobe_zero_roughness_gate']` -- default ON.

    Off restores the pre-2026-08-05 behaviour, in which a `composite_components.x`
    of exactly 0 reached Blender's `Roughness` and became a PERFECT MIRROR, where
    the engine's own GGX numerator (`m2 = sqrtroughness**4`) makes it contribute
    nothing at all. See the gate's comment at the roughness blend for the measured
    per-asset texel fractions.
    """
    if not opts:
        return True
    return bool(opts.get("brdf_lobe_zero_roughness_gate", True))


def lobe_weight_component(rec: dict | None) -> str:
    comp = (rec or {}).get("weight_channel")
    if isinstance(comp, str) and comp.upper() in ("R", "G", "B", "A"):
        return comp.upper()
    return "B"


def lobe1_roughness_component(rec: dict | None) -> str:
    comp = (rec or {}).get("lobe1_roughness_channel")
    if isinstance(comp, str) and comp.upper() in ("R", "G", "B", "A"):
        return comp.upper()
    return "A"


def blends_roughness(rec: dict | None) -> bool:
    """Only when lobe [1] actually carries specular energy -- see the decoder."""
    if not rec:
        return False
    if "blend_roughness" in rec:
        return bool(rec["blend_roughness"])
    return bool(rec.get("lobe1"))


# --- skin -------------------------------------------------------------------
# Blender's own "skin" radius in metres (red scatters furthest), the same
# ordering RAD's three bent-normal weights imply. ⚠ A LOOK CHOICE: RAD's scatter
# map is baked and carries no length scale.
SKIN_SUBSURFACE_RADIUS = (0.0143, 0.0056, 0.0027)
SKIN_SUBSURFACE_WEIGHT_DEFAULT = 0.35


def skin_subsurface_enabled(opts: dict | None) -> bool:
    """`opts['skin_subsurface']` -- default ON.

    Fires only on a material that binds `layerN_thickness_mask`, which is
    authored only for bare skin. Off restores the pre-2026-08-05 flat-diffuse
    face.
    """
    if not opts:
        return True
    return bool(opts.get("skin_subsurface", True))


def skin_subsurface_weight(opts: dict | None) -> float:
    try:
        return float((opts or {}).get("skin_subsurface_weight",
                                      SKIN_SUBSURFACE_WEIGHT_DEFAULT))
    except (TypeError, ValueError):
        return SKIN_SUBSURFACE_WEIGHT_DEFAULT


def is_composite_path(spec: dict, channels: dict) -> bool:
    """Prefer the decoder's answer; fall back to the role keys (old manifests)."""
    flag = spec.get("composite_path")
    if isinstance(flag, bool):
        return flag
    return any("composite_" in str(ch.get("role_key", ""))
               for ch in (channels or {}).values())


def specular_ior_level_for(spec: dict, channels: dict, sp_chan: dict | None) -> float:
    """`Specular IOR Level` -- 0.5 ("no adjustment") unless F0 is a hard ZERO.

    ⛔ A composite-path material that binds no `composite_specular` does not get
    Blender's 0.04 dielectric: the sampler's authored default is `common_black`
    (`name-confirmed`), so `specalbedo[0] = .xyz * .w` is exactly **0**
    (`shader-confirmed`) and the surface has no specular lobe at all. Leaving
    the socket at 0.5 put a 4 % mirror on every such material -- and because
    those materials also read
    `sqrtroughness[0]` from a components map that is frequently 0, that mirror was
    perfectly sharp. 25 of the 440 materials in `blender_tool/exports` are in this
    state, including Liv's harness `11ff222d38a601f3__364ff94a1d8c8805`, whose
    components map has `.x == 0` at **every** texel.
    """
    if sp_chan is not None:
        return SPECULAR_IOR_LEVEL_NEUTRAL
    explicit = spec.get("specular_f0_when_absent")
    if isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
        return 0.0 if float(explicit) == 0.0 else SPECULAR_IOR_LEVEL_NEUTRAL
    return 0.0 if is_composite_path(spec, channels) else SPECULAR_IOR_LEVEL_NEUTRAL


# --- specular / F0 ----------------------------------------------------------
# `layers[i].specalbedo[0]` IS the Schlick F0 term: `Fresnel()` is
# `specalbedo + (1 - specalbedo) * (1 - dot(l,h))^5` (`shader-confirmed`, the
# engine's own `Fresnel()`). Two samplers feed that one slot:
#
#   composite_specular : specalbedo = .xyz * .w ; specintensity = .w
#   specular_map       : specalbedo = k_enable_specular * speculartint *
#                        specular_map.xyz * k_fresnel ; specintensity = k_fresnel
#
# Same quantity, different scale: the composite map carries its own intensity in
# alpha, the non-composite one is scaled by the material scalar `k_fresnel`
# (authored 0.010, `name-confirmed`).
SPEC_MAP_FRESNEL_DEFAULT = 0.01
# Blender: Principled's dielectric F0 == F0(IOR) * 2 * `Specular IOR Level` *
# `Specular Tint`, LINEAR and UNCLAMPED (`engine-confirmed`, Cycles + EEVEE on
# 5.1.1; see docs/MATERIALS.md. `Specular IOR Level` is hard-capped
# at 1.0 but `Specular Tint` is `hard_max = FLT_MAX` (soft_max 1.0), so the
# reachable F0 is NOT capped at 0.08 -- leave the level at its 0.5 "no
# adjustment" point and put the whole of F0 into the tint.
SPECULAR_IOR_LEVEL_NEUTRAL = 0.5
MIN_F0_FOR_TINT = 1e-4          # guard against IOR ~= 1 (F0 -> 0 -> divide by 0)


def f0_from_ior(ior: float) -> float:
    """Normal-incidence dielectric reflectance, `((n-1)/(n+1))^2`."""
    try:
        n = float(ior)
    except (TypeError, ValueError):
        return 0.04
    if n <= 0.0:
        return 0.04
    return ((n - 1.0) / (n + 1.0)) ** 2


def specular_fresnel_scalar(spec: dict, chan: dict | None) -> float:
    """`k_fresnel` for the layer that owns a `specular_map` channel.

    Authored default 0.010 (`name-confirmed`); overridden only if the material
    actually serialises `layerN_fresnel` (no shipped material in the 51-package
    corpus does -- `stream-confirmed`).
    """
    layer = 0
    if chan is not None:
        try:
            layer = int(chan.get("layer", 0))
        except (TypeError, ValueError):
            layer = 0
    named = spec.get("named_scalars_resolved") or {}
    for key in (f"layer{layer}_fresnel", "fresnel"):
        if key in named:
            try:
                return float(named[key])
            except (TypeError, ValueError):
                pass
    if chan is not None and chan.get("spec_fresnel_default") is not None:
        try:
            return float(chan["spec_fresnel_default"])
        except (TypeError, ValueError):
            pass
    return SPEC_MAP_FRESNEL_DEFAULT


def specular_albedo_scale(spec: dict, chan: dict | None) -> float:
    """Constant that multiplies the texture RGB to give `specalbedo` (= F0).

    1.0 for `composite_specular` (its own `.w` supplies the scale and is applied
    as a node), `k_fresnel` for `specular_map`.
    """
    if not chan:
        return 1.0
    if str(chan.get("spec_albedo_scaled_by", "")).upper() == "A":
        return 1.0
    return specular_fresnel_scalar(spec, chan)


def specular_scales_by_alpha(chan: dict | None) -> bool:
    """Does `specalbedo` need the map's own alpha multiplied in?"""
    return bool(chan) and str(chan.get("spec_albedo_scaled_by", "")).upper() == "A"


def specular_tint_scale(spec: dict, chan: dict | None, ior: float) -> float:
    """Factor from `specalbedo` to Principled's `Specular Tint`.

    With `Specular IOR Level` left at 0.5, F0 = F0(IOR) * `Specular Tint`, so the
    tint is `specalbedo / F0(IOR)` -- 25.0 at the default IOR 1.5.
    """
    f0 = max(f0_from_ior(ior), MIN_F0_FOR_TINT)
    return specular_albedo_scale(spec, chan) / f0


def wire_specular_enabled(opts: dict | None) -> bool:
    """`opts['wire_specular']` -- default ON.

    Off restores the pre-A10 look (Principled's flat F0 = 0.04), which measured
    6x-20x too dark on shipped `composite_specular` data and 4x too bright on the
    `specular_map` panels (`engine-confirmed`, docs/MATERIALS.md.
    """
    if not opts:
        return True
    return bool(opts.get("wire_specular", True))


def emission_tint(spec: dict) -> tuple[float, float, float]:
    """The emissive tint, with the black-tint trap guarded.

    `emissive_color` is decoded from `SGMaterialData.bakeemissivecolor`, which is
    `(0,0,0)` on **every** genuinely emissive material inspected (`stream-confirmed`;
    findings 4 "Also:"). It is the *bake-time* colour, not the runtime tint (the
    runtime tint is `layerN_emissive_tint_color`, authored default `1,1,1,1`,
    `name-confirmed`). Multiplying an emissive map by a black tint annihilates the
    emission, so an all-zero tint is treated as "no tint" = white.
    """
    tint = spec.get("emissive_tint_color")
    if not tint:
        tint = spec.get("emissive_color")
    if not tint:
        return (1.0, 1.0, 1.0)
    try:
        rgb = tuple(float(c) for c in list(tint)[:3])
    except (TypeError, ValueError):
        return (1.0, 1.0, 1.0)
    if len(rgb) < 3 or not any(rgb):
        return (1.0, 1.0, 1.0)
    return rgb


def emission_strength(spec: dict) -> float:
    """`Emission Strength = layerN_emissive_intensity * k_emissive_scale`.

    There is NO unit-conversion constant -- both sides are linear radiance
    multipliers (`shader-confirmed`, findings 4). Any fudge factor here would be a
    bug, not a calibration.
    """
    def _f(key, default):
        try:
            return float(spec.get(key, default))
        except (TypeError, ValueError):
            return default
    return _f("emissive_intensity", 1.0) * _f("emissive_scale", 1.0)


#: Multiplier that leaves emission exactly as the material authored it.
EMISSION_STRENGTH_AUTHORED = 1.0


def emission_multiplier(opts: dict | None) -> float:
    """The user's `emission_strength` multiplier, coerced.

    Negatives clamp to zero rather than passing through: Blender's Emission
    Strength is a radiance scale, and a negative one subtracts light from the
    surface -- a black hole where a glow should be, which reads as a decode
    bug rather than a setting.

    Nonsense falls back to the authored 1.0, never to 0.0, for the same reason
    the bloom multiplier does: silently emitting nothing looks exactly like the
    bug this pass exists to fix, and gets blamed on the material.
    """
    try:
        return max(0.0, float((opts or {}).get("emission_strength",
                                               EMISSION_STRENGTH_AUTHORED)))
    except (TypeError, ValueError):
        return EMISSION_STRENGTH_AUTHORED


def refractive_index(spec: dict) -> float:
    v = spec.get("ior")
    if v is None:
        v = spec.get("refractive_index")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return DEFAULT_IOR
    return v if v > 1.0 else DEFAULT_IOR


def rim_lighting_enabled(opts) -> bool:
    """Is the rim-light term built? Default yes."""
    if not opts:
        return True
    return bool(opts.get("rim_lighting", True))


def translucent_emissive_alpha_enabled(opts: dict | None) -> bool:
    """`opts['translucent_emissive_alpha']` -- default ON.

    (The default is `True` on the line below; this line read "default OFF" and
    was simply wrong about the code beneath it.)

    ON: a blended surface left at Alpha = 1.0 is an opaque card, which is the
    one thing an `eBlendTranslucent` surface can never be, and 262 of the 3128
    materials in the corpus are in that state.

    ⚠ The alpha itself is `inferred`, not shader-confirmed -- the engine reads
    it from a source these materials do not bind, so the emissive luminance is
    a reconstruction. It over-dissolves where the winning layer is dark: on
    `mpl_combat_dyson`'s capture point the sphere goes nearly invisible.
    `opts['translucent_emissive_alpha'] = False` restores the opaque card.
    """
    if not opts:
        return True
    return bool(opts.get("translucent_emissive_alpha", True))


def build_translucent_emissive_alpha(nt, bsdf, spec, channels, mat) -> bool:
    """Drive Alpha from the emission for a BLENDED surface that binds no alpha.

    `eBlendTranslucent` (12) and the other non-additive blend equations are
    alpha blending: `dst = src*a + dst*(1-a)`. The importer sets
    `surface_render_method` to BLENDED for them and then leaves `Alpha` at 1.0,
    which is the one value that makes blending a no-op -- the surface renders
    as a solid card. `eBlendAdditive` had exactly this bug and was fixed by
    adding a Transparent BSDF beside it; the translucent modes were never
    given an equivalent.

    **262 of 3128 materials across 18 levels** are blended, bind no alpha
    anywhere (no alpha plane, no alpha map, `base_color_factor.a == 1`) and are
    therefore drawn fully opaque today -- combustion 53, fission 49, dyson 47,
    lobby_b2 34.

    ⭐ Restricted to EMISSION-DRIVEN surfaces (an emission channel and no base
    colour). For those the emitted colour IS the whole contribution, so its
    luminance is the coverage: a black texel emits nothing and must not occlude
    what is behind it, exactly the identity argument the additive path makes.
    A blended surface that binds a BASE COLOUR is left alone -- there the alpha
    is a real authored quantity this cannot reconstruct, and guessing would
    dissolve solid geometry.

    ⚠ `inferred`, not shader-confirmed: the engine reads its alpha from a
    source this material does not bind, so the luminance is a reconstruction.
    It can only ever make a surface MORE transparent than the opaque card it
    replaces. `opts['translucent_emissive_alpha']` turns it off.

    `mpl_combat_dyson`'s capture point (i1822 / i1826, `eMTForwardTransparent`,
    `eBlendTranslucent`) is the reported case: a hollow holographic sphere that
    imported as a solid ball.
    """
    alpha_in = _principled_input(bsdf, "Alpha")
    emis_in = _principled_input(bsdf, "Emission Color", "Emission")
    if alpha_in is None or emis_in is None:
        return False
    if alpha_in.is_linked or not emis_in.is_linked:
        return False
    try:
        if float(alpha_in.default_value) < 0.999:
            return False                      # a real alpha is already set
    except (TypeError, ValueError):
        return False
    if resolve_render_mode(spec) == "OPAQUE":
        return False
    if is_additive_blend(spec):
        return False                          # the Add/Transparent path owns it
    if channels.get("base_color"):
        return False
    # ⭐ A BARE RIM is an emission source too, even though it is a ROLE and
    # never appears in `channels`. `build_rim_lighting` runs immediately before
    # this and, for a material that binds nothing but a rim mask, links the
    # glow straight into Emission Color and sets `le_rim_lighting` WITHOUT an
    # `le_rim_mode` (there was no prior emission to gate or add to). That is
    # `rim_gates_emission`'s class 2 -- "the rim is the entire contribution" --
    # which is the same sentence this function's own docstring uses to justify
    # luminance-as-coverage. Keying only on `channels` made the two rules
    # disagree: the surface got its rim glow and then kept Alpha = 1.0, so it
    # still drew as the opaque card this function exists to eliminate.
    #
    # `mpl_combat_war_room`'s 3fde3ed3052c03af and 3aad23060860cdf3 are the
    # case -- both bind ONLY `layer0_rim_map`, both are eBlendTranslucent, and
    # both rendered as flat white slabs (the ceiling panels) against dark
    # panels with bright edges in game.
    #
    # Deliberately NOT widened past this: a rim that GATED or ADDED to an
    # existing emission (`le_rim_mode` set) has a real emissive surface
    # underneath, and that surface's coverage is not the rim's business.
    bare_rim = bool(mat.get("le_rim_lighting")) and not mat.get("le_rim_mode")
    if not channels.get("emission") and not bare_rim:
        return False

    lum = nt.nodes.new("ShaderNodeRGBToBW")
    lum.label = "translucent alpha = emissive luminance"
    lum.location = (bsdf.location.x - 320, bsdf.location.y - 620)
    nt.links.new(emis_in.links[0].from_socket, lum.inputs[0])
    nt.links.new(lum.outputs[0], alpha_in)
    mat["le_translucent_emissive_alpha"] = (
        "alpha = luminance(emission); the material binds no alpha source "
        "(inferred, opts['translucent_emissive_alpha'])")
    return True


#: Role keys that name a rim MASK. The corpus spells it two ways and the
#: matcher only ever knew one of them.
#:
#: ⛔ `next(k for k in roles if "rimlighting" in k)` missed every `*_rim_map`.
#: Across the 18 shipped levels 138 materials bind a rim mask under EIGHT
#: distinct keys -- `layer0_rimlighting_map` (54) and `layer1_rimlighting_map`
#: (21) matched, while `layer0_rim_map` (38), `layer2_rim_map` (22),
#: `layer1_rim_map` (16), `layer3_rim_map` (14) and `layer1_secondary_rim_map`
#: (2) did not. 72 of 138 never matched at all.
#:
#: `_rim_map` as a SUFFIX rather than `"rim" in k`, so a future
#: `*_primary_*` role cannot be swept up by the substring.
RIM_ROLE_SUFFIX = "_rim_map"


def rim_role_of(roles) -> str | None:
    """The rim mask role in `roles`, under either spelling, or None."""
    return next((k for k in sorted(roles or ())
                 if "rimlighting" in k or k.endswith(RIM_ROLE_SUFFIX)), None)


_LAYER_RE = __import__("re").compile(r"^layer(\d+)_")


def rim_gates_emission(spec: dict, rim_role: str) -> bool:
    """Is this rim mask a GATE on the emission rather than a glow of its own?

    ⭐ Three structural classes across the 138 rim-binding materials in the
    shipped tree, and only the third can be a gate:

      78  the rim shares its layer with an albedo/emissive -- it belongs to
          that surface and ADDS an edge glow to it. `mpl_arena_a` matidx 95
          (rim + albedo + normal + specular, accent `(1.0, 0.323, 0.204)`) is
          the case the additive path was validated on.
      34  the rim is bare AND the material binds nothing else at all --
          `mpl_arena_a` matidx 66 and 172 bind ONLY `layer0_rimlighting_map`.
          The rim is the entire contribution, so additive is the only reading.
      16  the rim is bare, the material carries EMISSION on other layers, and
          binds no base colour. A mask on a layer with no surface of its own,
          sitting above emissive layers, has nothing to add to -- the only
          thing it can be modulating is that emission.

    ⚠ The third class is one authored archetype, not a statistical residue:
    every member binds exactly `layer0_emissive_map`, `layer1_emissive_map`,
    `layer2_rim_map`, `layer3_rim_map` -- the glowing panel, reused across
    `mpl_combat_war_room`, both celebration rooms, combustion, dyson and the
    global menus.

    Gating it is what makes such a panel dark face-on with bright edges. Left
    additive, layer 1's broad fill lights the whole face flat.
    """
    roles = spec.get("role_textures") or {}
    hit = _LAYER_RE.match(rim_role or "")
    if hit is None:
        return False
    index = int(hit.group(1))
    for key in roles:
        other = _LAYER_RE.match(key)
        if key != rim_role and other and int(other.group(1)) == index:
            return False                       # class 1: shares its layer
    if not any("emissive" in k for k in roles):
        return False                           # class 2: nothing to gate
    if any(("albedo" in k) or ("diffuse" in k) for k in roles):
        return False                           # a real surface -- leave it
    return True


def build_rim_lighting(nt, bsdf, spec, pkg_dir, mat):
    """A Fresnel rim glow, tinted by the material's accent colour.

    ## Why this exists

    `mpl_arena_a`'s goal ends bind `layer0_rimlighting_map` and
    `layer1_rimlighting_map` and BOTH sit in `unrouted_roles` -- nothing
    consumed them, so the only lit term these surfaces had was a greyscale
    albedo. In game they read as a coloured edge glow, which is what a rim
    term is: brightest where the surface turns away from the viewer.

    ## What drives it

    The colour is `accent_tint` (see `evr_materials`, where the slot and the
    evidence are documented). Its ALPHA carries an intensity -- the arena's two
    ends author 0.8 and 0.5 -- which is why `PARAM_ARITY` types a rim tint as a
    Color4 rather than a Real3.

    The rim MAP is a mask, not a colour: `0dc73d47fd33fc29` averages 0.040 and
    `75501b0000b96699` 0.059, i.e. mostly black with bright edges. It is
    multiplied in so the glow appears only where the artist painted it.

    ## The Fresnel exponent (`accent_pow`)

    RECOVERED. The accent group is SIX words, not four -- RGB, intensity, a
    0.0 separator, then the exponent -- so the constant needs no slot name at
    all, only a fixed offset. `evr_materials` decodes it as `accent_pow`; the
    arena's blue goal end reads 3.333 and 4.5, the two values that comment
    always cited. Applied here as `facing ** accent_pow`, which narrows the
    glow to the silhouette the way the engine does.

    Measured, not assumed: Blender's Layer Weight `Facing` output is 0.0
    head-on and rises to ~0.80 approaching the silhouette (sphere probe, blend
    0.35), so it is already the right way round and the power only sharpens it.

    Where no accent group is authored -- `mpl_combat_war_room` has none on any
    of its 69 materials -- `accent_pow` is None and the term is left linear,
    exactly as before.
    """
    roles = spec.get("role_textures") or {}
    rim_role = rim_role_of(roles)
    if rim_role is None:
        return False
    accent = spec.get("accent_tint")
    coloured = bool(accent) and len(accent) >= 3
    emis_in = _principled_input(bsdf, "Emission Color")
    str_in = _principled_input(bsdf, "Emission Strength")
    if emis_in is None:
        return False

    weight = nt.nodes.new("ShaderNodeLayerWeight")
    weight.label = "rim fresnel"
    weight.location = (-900, -700)
    weight.inputs["Blend"].default_value = 0.35

    rim_img = _load_image(pkg_dir, "textures/%s.dds" % roles[rim_role],
                          "Non-Color", "CHANNEL_PACKED")
    factor = weight.outputs["Facing"]
    rim_pow = spec.get("accent_pow")
    if rim_pow and float(rim_pow) > 0.0 and abs(float(rim_pow) - 1.0) > 1e-6:
        powed = nt.nodes.new("ShaderNodeMath")
        powed.operation = "POWER"
        powed.label = "rim falloff (accent_pow)"
        powed.location = (-820, -700)
        nt.links.new(weight.outputs["Facing"], powed.inputs[0])
        powed.inputs[1].default_value = float(rim_pow)
        factor = powed.outputs[0]
    if rim_img is not None:
        rim_node = _tex_node(nt, rim_img, "Non-Color", -900, -420,
                             "CHANNEL_PACKED", label="rimlighting_map")
        masked, (mfi, mai, mbi, mri) = _mix_node(
            nt, "FLOAT", "MULTIPLY", -680, -560, label="rim x mask")
        masked.inputs[mfi].default_value = 1.0
        nt.links.new(factor, masked.inputs[mai])
        nt.links.new(rim_node.outputs["Color"], masked.inputs[mbi])
        factor = masked.outputs[mri]

    # ⚠ UNCOLOURED fallback. `accent_tint` is the authored rim colour, but only
    # `mpl_arena_a` carries the slot at all -- 123 of the 138 rim-binding
    # materials in the corpus have none. Dropping the rim for want of a colour
    # threw away the view-angle term as well, which is the part that does not
    # need a colour: white x mask x facing is the neutral reading, and the mask
    # still confines the glow to where the artist painted it. The choice is
    # recorded on the material so a white rim is never mistaken for a decoded
    # one.
    tint_rgb = ((float(accent[0]), float(accent[1]), float(accent[2]))
                if coloured else (1.0, 1.0, 1.0))
    intensity = (float(accent[3]) if coloured and len(accent) > 3 else 1.0)
    tinted, (tfi, tai, tbi, tri) = _mix_node(
        nt, "RGBA", "MULTIPLY", -460, -560, label="rim tint")
    tinted.inputs[tfi].default_value = 1.0
    tinted.inputs[tai].default_value = tint_rgb + (1.0,)
    scale = max(0.0, intensity)
    tinted.inputs[tbi].default_value = (scale, scale, scale, 1.0)

    glow, (gfi, gai, gbi, gri) = _mix_node(
        nt, "RGBA", "MULTIPLY", -260, -560, label="rim glow")
    glow.inputs[gfi].default_value = 1.0
    nt.links.new(tinted.outputs[tri], glow.inputs[gai])
    # the facing/mask term as a colour
    nt.links.new(factor, glow.inputs[gbi])

    gating = rim_gates_emission(spec, rim_role) and emis_in.is_linked
    if gating:
        # MULTIPLY, not add: the mask has no surface of its own, so it can only
        # be modulating the emission beneath it. See `rim_gates_emission`.
        upstream = emis_in.links[0].from_socket
        gate, (qfi, qai, qbi, qri) = _mix_node(
            nt, "RGBA", "MULTIPLY", -60, -560, label="rim gate (emission x rim)")
        gate.inputs[qfi].default_value = 1.0
        nt.links.new(upstream, gate.inputs[qai])
        nt.links.new(glow.outputs[gri], gate.inputs[qbi])
        nt.links.new(gate.outputs[qri], emis_in)
        mat["le_rim_mode"] = "gate (emission x rim x facing)"
    elif emis_in.is_linked:
        upstream = emis_in.links[0].from_socket
        add, (afi, aai, abi, ari) = _mix_node(
            nt, "RGBA", "ADD", -60, -560, label="rim add")
        add.inputs[afi].default_value = 1.0
        nt.links.new(upstream, add.inputs[aai])
        nt.links.new(glow.outputs[gri], add.inputs[abi])
        nt.links.new(add.outputs[ari], emis_in)
        mat["le_rim_mode"] = "additive"
    else:
        nt.links.new(glow.outputs[gri], emis_in)
        if str_in is not None and not str_in.is_linked and str_in.default_value <= 0.0:
            str_in.default_value = 1.0
    mat["le_rim_lighting"] = [round(float(c), 6) for c in tint_rgb] + [intensity]
    mat["le_rim_lighting_source"] = ("accent_tint" if coloured
                                     else "uncoloured (no accent_tint authored)")
    mat["le_rim_role"] = rim_role
    return True


def accent_tint_enabled(opts) -> bool:
    """Is the team/accent tint applied? Default yes."""
    if not opts:
        return True
    return bool(opts.get("accent_tint", True))


def base_color_fallback(spec: dict) -> tuple:
    """`base_color_factor` as a Blender colour: RGB kept, **alpha forced to 1.0**.

    `base_color_factor` is `SGMaterialData.bakecolor` == the authored
    `k_hardware_color`, whose schema is a `Color4` with **no `:a` widget**
    (the material asset schema), and no shader in the shipped tree reads the
    member at all.  Its 4th float is therefore *unauthored* — whatever
    the bake pipeline left in the slot — and it ships as `0.0` on 27/100 level
    materials and on **8 of the 11** materials of character
    `c6bc8607972268c9_64b4b5b2a0153f7e`, **including two that bind a full texture
    set**.  That last point is what rules out reading it as an alpha: those two
    render correctly.

    Blender's Principled `Base Color` socket ignores the 4th component today, so
    letting `0.0` through is currently inert — which is exactly why it is worth
    normalising at the one place it can leak.  The scatter path already does
    this (`scatter_import.py`, `list(...)[:3] + [1.0]`); the mesh path did
    not, and the inconsistency was silent.

    ⚠ Unresolved and deliberately NOT converted here: whether `bakecolor` is
    sRGB.  Every other authored `Color4` reaching the shader is linearised
    (`QuickSRGBToLinear(k_albedo_tint_color[i])`) and one shipped material
    authors `0.74902 == 191/255`, an
    sRGB-8 value — but since no shader reads `k_hardware_color`, that is an
    analogy, not proof.  Applying it would make these surfaces DARKER.
    """
    v = list(spec.get("base_color_factor") or (1.0, 1.0, 1.0, 1.0))
    v = [float(c) for c in v[:3]] + [1.0]
    while len(v) < 4:
        v.insert(-1, 1.0)
    return tuple(v)


def wants_vertex_color_diffuse(obj: dict) -> bool:
    """Does this MESH declare `eDiffuseVertexColor` (0x2000)?

    The engine's composite path does `diffusealbedo = k_composite_diffuse.xyz *
    params.albedovertex.xyz` (`shader-confirmed`) with
    the vertex colour selected by the per-layer `enable_albedo_vertex_color` option;
    `eDiffuseVertexColor` is the mesh-side counterpart (`inferred` linkage). Gated,
    never unconditional: most meshes carry white or unused `color0`.
    """
    names = obj.get("flag_names") or []
    if "eDiffuseVertexColor" in names:
        return True
    flags = obj.get("flags", 0)
    try:
        flags = int(flags)
    except (TypeError, ValueError):
        return False
    return bool(flags & MESH_FLAG_DIFFUSE_VERTEX_COLOR)


# ---------------------------------------------------------------------------
# Blender node graph
# ---------------------------------------------------------------------------

def _principled_input(node, *names):
    """Principled v2 renamed its sockets in 4.0; hardcoding an old name silently
    no-ops. Always pass the new name first and the pre-4.0 name as a fallback."""
    for n in names:
        if n in node.inputs:
            return node.inputs[n]
    return None


def _load_image(pkg_dir: Path, rel_file: str, colorspace: str,
                alpha_mode: str = "CHANNEL_PACKED"):
    if not rel_file:
        return None
    path = pkg_dir / rel_file
    if not path.exists():
        return None
    try:
        img = bpy.data.images.load(str(path), check_existing=True)
    except Exception:
        return None
    _apply_image_settings(img, colorspace, alpha_mode)
    return img


def _apply_image_settings(img, colorspace: str, alpha_mode: str):
    """Set colour space AND alpha mode, then read both back.

    Never leave `alpha_mode` at Blender's `'STRAIGHT'` default for packed data -- see
    the module docstring; it multiplies RGB by alpha and silently corrupts albedo.
    """
    try:
        img.colorspace_settings.name = colorspace
    except Exception:
        pass
    try:
        img.alpha_mode = alpha_mode
        if img.alpha_mode != alpha_mode:       # read-back guard
            img["le_alpha_mode_write_failed"] = alpha_mode
    except Exception:
        pass
    return img


def _tex_node(nt, img, colorspace, x, y, alpha_mode="CHANNEL_PACKED", label=""):
    node = nt.nodes.new("ShaderNodeTexImage")
    node.image = img
    node.location = (x, y)
    if label:
        node.label = label
    if node.image is not None:
        _apply_image_settings(node.image, colorspace, alpha_mode)
    return node


def _math(nt, op, x, y, a=None, b=None, label=""):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    n.location = (x, y)
    if label:
        n.label = label
    for i, v in ((0, a), (1, b)):
        if v is None:
            continue
        if hasattr(v, "node"):          # a socket -> link it
            nt.links.new(v, n.inputs[i])
        else:
            n.inputs[i].default_value = float(v)
    return n


def _normal_chain(nt, tex_node, reconstruct_z, x, y):
    """Return a socket carrying a tangent-space normal for the Normal Map node."""
    if not reconstruct_z:
        return tex_node.outputs["Color"]
    # BC5 stores XY in RG; reconstruct Z = sqrt(1 - (2X-1)^2 - (2Y-1)^2).
    sep = nt.nodes.new("ShaderNodeSeparateColor"); sep.location = (x, y)
    nt.links.new(tex_node.outputs["Color"], sep.inputs["Color"])
    # remap X,Y from [0,1] to [-1,1], square, sum, 1-sum, sqrt, remap back
    def _mul_add(inp, mul, add, yy):
        m = nt.nodes.new("ShaderNodeMath"); m.operation = "MULTIPLY_ADD"
        m.location = (x + 160, yy); m.inputs[1].default_value = mul
        m.inputs[2].default_value = add
        nt.links.new(inp, m.inputs[0]); return m
    xr = _mul_add(sep.outputs[0], 2.0, -1.0, y + 60)
    yr = _mul_add(sep.outputs[1], 2.0, -1.0, y - 60)
    xsq = nt.nodes.new("ShaderNodeMath"); xsq.operation = "POWER"; xsq.location = (x + 320, y + 60)
    xsq.inputs[1].default_value = 2.0; nt.links.new(xr.outputs[0], xsq.inputs[0])
    ysq = nt.nodes.new("ShaderNodeMath"); ysq.operation = "POWER"; ysq.location = (x + 320, y - 60)
    ysq.inputs[1].default_value = 2.0; nt.links.new(yr.outputs[0], ysq.inputs[0])
    ssum = nt.nodes.new("ShaderNodeMath"); ssum.operation = "ADD"; ssum.location = (x + 480, y)
    nt.links.new(xsq.outputs[0], ssum.inputs[0]); nt.links.new(ysq.outputs[0], ssum.inputs[1])
    inv = nt.nodes.new("ShaderNodeMath"); inv.operation = "SUBTRACT"; inv.location = (x + 640, y)
    inv.inputs[0].default_value = 1.0; nt.links.new(ssum.outputs[0], inv.inputs[1])
    zsqrt = nt.nodes.new("ShaderNodeMath"); zsqrt.operation = "SQRT"; zsqrt.location = (x + 800, y)
    nt.links.new(inv.outputs[0], zsqrt.inputs[0])
    zr = nt.nodes.new("ShaderNodeMath"); zr.operation = "MULTIPLY_ADD"; zr.location = (x + 960, y)
    zr.inputs[1].default_value = 0.5; zr.inputs[2].default_value = 0.5
    nt.links.new(zsqrt.outputs[0], zr.inputs[0])
    # Normal Map node does its own *2-1 remap, so feed it raw R,G plus Z-in-[0,1].
    comb = nt.nodes.new("ShaderNodeCombineColor"); comb.location = (x + 1120, y)
    nt.links.new(sep.outputs[0], comb.inputs[0])
    nt.links.new(sep.outputs[1], comb.inputs[1])
    nt.links.new(zr.outputs[0], comb.inputs[2])
    return comb.outputs["Color"]


def _vec(nt, op, x, y, a=None, b=None, label=""):
    """A `ShaderNodeVectorMath` node, wired by INDEX (see `VECMATH_*`)."""
    n = nt.nodes.new("ShaderNodeVectorMath")
    n.operation = op
    n.location = (x, y)
    if label:
        n.label = label
    for i, s in ((VECMATH_A, a), (VECMATH_B, b)):
        if s is None:
            continue
        if hasattr(s, "is_output") or hasattr(s, "links"):
            nt.links.new(s, n.inputs[i])
        else:
            n.inputs[i].default_value = s
    return n


def _shipped_tangent_normal(nt, color_socket, x, y):
    """World-space normal from a tangent-space map on the SHIPPED basis. R1.

    Returns `(socket, nodes)`; `socket` carries the world normal for the
    Principled `Normal` input, and `nodes["have_tangent"]` is a 0/1 float that is
    1 only where `le_tangent` actually exists.

    ★ WHY THE GRAPH IS BUILT BY HAND. Blender will not accept an authored tangent
    anywhere: `mesh.loops[].tangent` is read-only and recomputed by mikktspace
    from the active UV layer, and `ShaderNodeNormalMap` has no tangent input.
    `ShaderNodeTangent` only offers radial axes and a UV map. So the TBN is built
    in nodes, which is the only place the shipped basis can reach the shader:

        T  = normalize(object_to_world(le_tangent))
        T' = normalize(T - N * dot(N, T))          Gram-Schmidt against N
        B  = cross(N, T') * sign(le_tangent_w)     (`tangent_w_meaning`)
        n  = 2 * color - 1                         the same remap NormalMap does
        out = normalize(T' * n.x + B * n.y + N * n.z)

    `N` is `ShaderNodeNewGeometry.Normal`, the WORLD-space shading normal — which
    is the custom split normal this importer set from the shipped `normal` stream,
    so both legs of the frame come from the same source.

    ⚠ THE OBJECT->WORLD TRANSFORM IS REQUIRED AND IS NOT DECORATION. `le_tangent`
    is stored in mesh space (the vertex blobs stay byte-faithful to disk and the
    Y-up -> Z-up conversion lives on `ob.matrix_basis`), while `Geometry.Normal`
    is world space. Without `ShaderNodeVectorTransform` the two legs would be in
    different frames and the result would be silently wrong rather than visibly
    broken. The default axis matrix is a PURE ROTATION (det +1), so it carries
    the handedness across unchanged; `mirror_axis` (diagnostic, default off) is
    det -1 and WOULD flip it — that toggle already documents itself as the wrong
    convention.

    ⛔ DEGRADES, NEVER BLACKENS. A mesh with no `le_tangent` attribute reads
    (0, 0, 0) from the Attribute node, and normalizing that gives a zero normal —
    a black surface. So the graph keeps the `ShaderNodeNormalMap` leg as well and
    mixes to it wherever `length(le_tangent) < 0.5`. An object without the stream
    therefore behaves EXACTLY as it did before this change, per pixel.
    """
    n_out = {}
    # --- the shipped basis ---------------------------------------------------
    ta = nt.nodes.new("ShaderNodeAttribute")
    ta.attribute_type = "GEOMETRY"
    ta.attribute_name = "le_tangent"
    ta.location = (x, y + 260)
    ta.label = "le_tangent (shipped)"
    tw = nt.nodes.new("ShaderNodeAttribute")
    tw.attribute_type = "GEOMETRY"
    tw.attribute_name = "le_tangent_w"
    tw.location = (x, y + 120)
    tw.label = "le_tangent_w"

    xf = nt.nodes.new("ShaderNodeVectorTransform")
    xf.vector_type = "VECTOR"
    xf.convert_from = "OBJECT"
    xf.convert_to = "WORLD"
    xf.location = (x + 180, y + 260)
    nt.links.new(ta.outputs["Vector"], xf.inputs[0])

    geo = nt.nodes.new("ShaderNodeNewGeometry")
    geo.location = (x + 180, y - 60)
    nrm = geo.outputs["Normal"]

    tnorm = _vec(nt, "NORMALIZE", x + 360, y + 260, xf.outputs["Vector"])
    dot = _vec(nt, "DOT_PRODUCT", x + 520, y + 160, nrm, tnorm.outputs["Vector"])
    proj = nt.nodes.new("ShaderNodeVectorMath")
    proj.operation = "SCALE"
    proj.location = (x + 680, y + 160)
    nt.links.new(nrm, proj.inputs[VECMATH_A])
    nt.links.new(dot.outputs["Value"], proj.inputs[VECMATH_SCALE])
    ortho = _vec(nt, "SUBTRACT", x + 840, y + 260,
                 tnorm.outputs["Vector"], proj.outputs["Vector"])
    tgt = _vec(nt, "NORMALIZE", x + 1000, y + 260, ortho.outputs["Vector"],
               label="T (Gram-Schmidt)")

    sign = nt.nodes.new("ShaderNodeMath")
    sign.operation = "SIGN"
    sign.location = (x + 360, y + 120)
    sign.label = "sign(w) = handedness"
    nt.links.new(tw.outputs["Fac"], sign.inputs[0])
    cross = _vec(nt, "CROSS_PRODUCT", x + 1160, y + 120, nrm, tgt.outputs["Vector"])
    bit = nt.nodes.new("ShaderNodeVectorMath")
    bit.operation = "SCALE"
    bit.location = (x + 1320, y + 120)
    bit.label = "B = cross(N, T) * sign(w)"
    nt.links.new(cross.outputs["Vector"], bit.inputs[VECMATH_A])
    nt.links.new(sign.outputs[0], bit.inputs[VECMATH_SCALE])

    # --- decode the map and rotate it into world -----------------------------
    dec = nt.nodes.new("ShaderNodeVectorMath")
    dec.operation = "MULTIPLY_ADD"
    dec.location = (x + 1000, y - 220)
    dec.label = "2c - 1"
    nt.links.new(color_socket, dec.inputs[VECMATH_A])
    dec.inputs[VECMATH_B].default_value = (2.0, 2.0, 2.0)
    dec.inputs[VECMATH_C].default_value = (-1.0, -1.0, -1.0)
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    sep.location = (x + 1160, y - 220)
    nt.links.new(dec.outputs["Vector"], sep.inputs[0])

    def _scaled(vec_socket, val_socket, yy, label):
        n = nt.nodes.new("ShaderNodeVectorMath")
        n.operation = "SCALE"
        n.location = (x + 1480, yy)
        n.label = label
        nt.links.new(vec_socket, n.inputs[VECMATH_A])
        nt.links.new(val_socket, n.inputs[VECMATH_SCALE])
        return n

    sx = _scaled(tgt.outputs["Vector"], sep.outputs["X"], y + 260, "T * n.x")
    sy = _scaled(bit.outputs["Vector"], sep.outputs["Y"], y + 60, "B * n.y")
    sz = _scaled(nrm, sep.outputs["Z"], y - 140, "N * n.z")
    add1 = _vec(nt, "ADD", x + 1640, y + 160, sx.outputs["Vector"], sy.outputs["Vector"])
    add2 = _vec(nt, "ADD", x + 1800, y + 60, add1.outputs["Vector"], sz.outputs["Vector"])
    world = _vec(nt, "NORMALIZE", x + 1960, y + 60, add2.outputs["Vector"],
                 label="shipped-basis normal")

    # --- fall back where the stream is absent --------------------------------
    length = _vec(nt, "LENGTH", x + 360, y - 300, ta.outputs["Vector"])
    have = nt.nodes.new("ShaderNodeMath")
    have.operation = "GREATER_THAN"
    have.location = (x + 520, y - 300)
    have.label = "has le_tangent?"
    have.inputs[1].default_value = 0.5
    nt.links.new(length.outputs["Value"], have.inputs[0])
    n_out["have_tangent"] = have.outputs[0]
    n_out["tangent_node"] = ta
    n_out["world"] = world.outputs["Vector"]
    return world.outputs["Vector"], n_out


def _component_socket(nt, tex_node, comp, x, y):
    """Socket carrying one component of a texture (R/G/B via Separate Color, A direct)."""
    if comp == "A":
        return tex_node.outputs["Alpha"]
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.location = (x, y)
    nt.links.new(tex_node.outputs["Color"], sep.inputs["Color"])
    return sep.outputs[{"R": 0, "G": 1, "B": 2}.get(comp, 0)]


def _mix_node(nt, data_type, blend_type, x, y, label=""):
    n = nt.nodes.new("ShaderNodeMix")
    n.data_type = data_type
    n.blend_type = blend_type
    n.location = (x, y)
    if label:
        n.label = label
    return n, {"FLOAT": MIX_FLOAT_SOCKETS,
               "VECTOR": MIX_VECTOR_SOCKETS}.get(data_type, MIX_RGBA_SOCKETS)


#: Scroll rate for an animated UV layer, in UV units per SECOND.
#:
#: ⚠ NOT FROM THE DATA. The materials that scroll say only THAT they scroll --
#: `cff42270c09d1c79` carries exactly one property, `layer0_albedo_map_uoffset
#: = 0.0`, and `cdaf8112b0b3b173` exactly one, `layer0_flipbook_offset = 0.0`.
#: Both are 520-byte near-stubs; there is no rate, no frame count and no
#: direction anywhere in them. The speed is applied at runtime, and the only
#: place left that could hold it is `CScriptCR`, which is not decoded.
#:
#: So this value is authored to match the reported look -- "slow, sluggish, a
#: background thing", then tuned 5x up from 0.004 on a second look, and is
#: exposed as an import option rather than being
#: presented as recovered. Change it freely; nothing downstream depends on it.
UV_SCROLL_RATE_DEFAULT = 0.02

#: Material properties that mean "this layer's UVs move at runtime".
UV_SCROLL_PROPERTIES = (
    "layer0_albedo_map_uoffset", "layer1_albedo_map_uoffset",
    "layer2_albedo_map_uoffset", "layer3_albedo_map_uoffset",
)
UV_SCROLL_V_PROPERTIES = (
    "layer0_albedo_map_voffset", "layer1_albedo_map_voffset",
    "layer2_albedo_map_voffset", "layer3_albedo_map_voffset",
)
#: A flipbook advances through frames rather than sliding, but with no frame
#: count on disk the only honest approximation is the same slow slide.
UV_FLIPBOOK_PROPERTIES = (
    "layer0_flipbook_offset", "layer1_flipbook_offset",
    "layer2_flipbook_offset", "layer3_flipbook_offset",
)


def uv_scroll_axes(spec: dict) -> tuple:
    """`(scrolls_u, scrolls_v)` -- does this material animate its UVs?

    Presence of the property is the signal, not its value: every one of them is
    `0.0` on disk because that is the REST offset, the state before the engine
    starts advancing it.
    """
    named = spec.get("named_scalars") or {}
    scroll_u = any(key in named for key in UV_SCROLL_PROPERTIES)
    scroll_v = any(key in named for key in UV_SCROLL_V_PROPERTIES)
    if any(key in named for key in UV_FLIPBOOK_PROPERTIES):
        scroll_u = True
    return scroll_u, scroll_v


def _drive_uv_scroll(mapping, axes, rate: float) -> list:
    """Drive the Mapping node's Location from the scene clock.

    A driver rather than keyframes: it stays correct however long the timeline
    is and needs no bake, and `frame / fps` makes the rate independent of the
    scene's frame rate.
    """
    driven = []
    for index, active in enumerate(axes):
        if not active:
            continue
        try:
            fcurve = mapping.inputs["Location"].driver_add("default_value", index)
        except (RuntimeError, TypeError):
            continue
        driver = fcurve.driver
        driver.type = "SCRIPTED"
        for existing in list(driver.variables):
            driver.variables.remove(existing)
        var = driver.variables.new()
        var.name = "f"
        var.targets[0].id_type = "SCENE"
        var.targets[0].id = bpy.context.scene
        var.targets[0].data_path = "frame_current"
        fps = getattr(getattr(bpy.context.scene, "render", None), "fps", 24) or 24
        driver.expression = "f / %d * %r" % (int(fps), float(rate))
        driven.append("uv"[index] if index < 2 else str(index))
    return driven


def base_color_layers(spec: dict) -> list:
    """Every layer that binds a base colour, lowest index first.

    `channels["base_color"]` is a MERGED view that keeps only "the lowest layer
    that provides it" (see `le_mesh.materials.classify_roles_layered`), so a
    material painting two albedos on top of each other arrives with the upper
    one silently dropped. `spec["layers"]` still carries them all.

    ⭐ That is not a corner case, it is how the sky is built. `mpl_arena_a`'s
    sky-layer materials (matidx 113-116, 520-byte near-stubs that nonetheless
    declare `auxillaryinputs: 2`) bind:

        layer0  e36cdae9c6eaa43a  2048x2048  bright wisps, ~5% alpha
        layer1  50988725d240e5fe    64x64    PURE BLACK, fully opaque

    Keeping only layer0 renders the wisps with nothing behind them, which is why
    the skymap came in as a white haze instead of a black sky with moving
    wisps. 18 of the arena's 238 materials paint base colour on more than one
    layer; 13 of those carry a blend mask and 5 do not.
    """
    out = []
    for entry in spec.get("layers") or []:
        channel = (entry.get("channels") or {}).get("base_color")
        if channel and channel.get("file"):
            out.append((int(entry.get("index") or 0), channel))
    out.sort(key=lambda pair: pair[0])
    return out


def emission_layers(spec: dict) -> list:
    """Every layer that binds an EMISSIVE map, lowest index first.

    The exact analogue of `base_color_layers`, and for the same reason:
    `channels["emission"]` is the MERGED view, which keeps only "the lowest
    layer that provides it" (`le_mesh.materials.classify_roles_layered`), so a
    material painting two or three emissive maps on top of each other arrives
    with every upper one silently dropped.

    ⛔ The emission wiring used to assert this could not happen -- "No lower
    layer binds an emissive map: if one did, the merged view would have
    selected it" -- and collapsed the engine's lerp to a plain multiply on that
    basis. The corpus disagrees: **225 of 3128 materials across 18 levels bind
    an emissive map on more than one layer**, led by mpl_lobby_b2 (45),
    combustion (37) and dyson (32).

    ⭐ `mpl_combat_dyson`'s capture point is the case that found it. Material
    `2fa509e6d8ff9b42` (i1826) binds THREE emissive layers -- 7b279387 on
    layer0, 76ee2e8c on layer1, fd5bd6bd on layer2 -- and only layer0 reached
    the graph, so the sphere lost the lock artwork the level wears at the start
    of a round. Layers 1 and 2 both carry `amount_constant: 1.0`, i.e. they are
    composited at full strength with no mask and no vertex modulation.
    """
    out = []
    for entry in spec.get("layers") or []:
        channel = (entry.get("channels") or {}).get("emission")
        if channel and channel.get("file"):
            out.append((int(entry.get("index") or 0), channel))
    out.sort(key=lambda pair: pair[0])
    return out


def supersede_opaque_top_layer(spec: dict, channels: dict) -> dict:
    """Let a fully-replacing upper albedo layer win over the merged view.

    `channels` keeps only "the lowest layer that provides it"
    (`le_mesh.materials.classify_roles_layered`), which silently discards an
    upper albedo painted on top of a lower one -- COLOUR AND ALPHA BOTH.

    ⭐ The sky is built exactly that way. `mpl_arena_a`'s sky-layer materials
    (matidx 114/115 -- 520-byte near-stubs that still declare
    `auxillaryinputs: 2`, read cleanly as `bind_source=offset`) bind:

        layer0  e36cdae9c6eaa43a  2048x2048  bright wisps, ~5% alpha
        layer1  50988725d240e5fe    64x64    PURE BLACK, fully OPAQUE

    Keeping layer0 gives a 95%-transparent white haze; the black never appears
    and neither does the opacity. That is why the skymap imported as a pale
    smear instead of the black sky it is in game.

    ## Only when the upper layer provably covers everything

    The test is the spec's own `blend["amount_constant"]`: the layer's blend
    amount resolved to a compile-time constant, which is `1.0` exactly when the
    mask is absent (`"mask": null`, so the engine's `blend_mask` sampler falls
    back to `common_white` and `mask.R == 1.0`) and the scale/offset leave it
    saturated. A masked layer has `amount_constant: null` because it opens and
    closes per texel -- it gates, it does not replace, and those keep the
    existing behaviour untouched.

    ⛔ Do NOT use "has no blend record" as the test. Tried, and it fires on
    NOTHING: layer 1 of matidx 113/114/115 does carry a blend record, it simply
    has `"mask": null` inside it. The record's presence says nothing; its
    resolved amount says everything.

    ⚠ Deliberately narrow. On `mpl_arena_a` 18 of 238 materials paint base
    colour on more than one layer and this fires only on those whose upper layer
    resolves to a constant 1.0. Widening it to masked layers needs a rendered
    comparison, not a reading of the file.
    """
    stack = base_color_layers(spec)
    if len(stack) < 2:
        return channels
    top_index, top_channel = stack[-1]
    blend = layer_blend_of(spec, top_index)
    if blend is None:
        return channels                      # layer 0 is the base; nothing above it
    if blend.get("amount_constant") != 1.0:
        return channels                      # masked: it gates, it does not replace

    merged = dict(channels)
    merged["base_color"] = top_channel

    # ⛔ COLOUR ONLY. Do NOT move `alpha` onto the same layer as well -- tried,
    # and every superseded surface VANISHED. `50988725d240e5fe`, the black tile
    # these sky layers stack on, is `BC1_UNORM_SRGB`: BC1 carries at most one bit
    # of alpha, and Blender's decoder reads a pure-black block as PUNCH-THROUGH
    # TRANSPARENT, so alpha comes back 0 for the whole texture. (An external
    # decoder reports alpha 255 for the same file, which is why this looked safe
    # on paper.) Driving opacity from it deletes the surface.
    #
    # Leaving alpha on the lower layer keeps the authored coverage and still
    # fixes the colour, which is the part that was demonstrably wrong.
    return merged


def _blend_amount_socket(nt, pkg_dir: Path, blend: dict, opts: dict, x, y):
    """-> (socket, constant). Exactly one is not None.

    Builds `saturate(mask.R * blend_mask_scale + blend_mask_offset)`
    (`shader-confirmed`, the engine's `ComputeBlend`) as
    `Math(MULTIPLY_ADD, use_clamp=True)` -- `use_clamp` IS `saturate()`.

    The vertex-blend factor `saturate(vertex_blend / blend_fade)` is deliberately
    NOT built: `vertblend` is `blend[i-1]` of the `float4 blend : COLOR1` vertex
    stream (`shader-confirmed`), which `mesh_builder`
    does not import today, and whether it is even sampled is the `use_vertex_blend_`
    permutation bit -- not on disk. See defect D1.
    """
    scale = blend_mask_scale_for(blend)
    offset = blend_mask_offset_for(blend, opts)
    mask = blend.get("mask") or {}
    # A layer whose mask can never open is constant 0 -- do not even load the DDS.
    if _sat(scale + offset) <= 0.0:
        return None, 0.0
    img = None
    if mask.get("file"):
        img = _load_image(pkg_dir, mask.get("file", ""),
                          mask.get("colorspace", "Non-Color"), image_alpha_mode(mask, "NONE"))
    if img is None:
        # `blend_mask` sampler default is `common_white` -> mask.R == 1.0.
        return None, _sat(scale * DEFAULT_BLEND_MASK_VALUE + offset)
    if _sat(offset) >= 1.0:
        return None, 1.0
    node = _tex_node(nt, img, mask.get("colorspace", "Non-Color"), x, y,
                     image_alpha_mode(mask, "NONE"),
                     label=str(mask.get("role_key") or "blend_mask"))
    src = _component_socket(nt, node, blend_mask_component_of(blend), x + 260, y)
    ma = _math(nt, "MULTIPLY_ADD", x + 460, y, src, scale,
               label="saturate(mask.R * scale + offset)")
    ma.inputs[2].default_value = offset
    ma.use_clamp = True                    # HLSL saturate()
    return ma.outputs[0], None


def _layer_gate(nt, pkg_dir: Path, blend: dict, channel: str, opts: dict, x, y):
    """-> (socket, constant) for `blend_amount * layerN_<channel>_blend_alpha`."""
    sock, const = _blend_amount_socket(nt, pkg_dir, blend, opts, x, y)
    alpha = channel_blend_alpha(blend, channel)
    if alpha == 1.0:
        return sock, const
    if const is not None:
        return None, _sat(const * alpha)
    node = _math(nt, "MULTIPLY", x + 660, y, sock, alpha,
                 label=f"{channel}_blend_alpha")
    return node.outputs[0], None


def _set_render_mode(mat, render_mode: str) -> str:
    """Drive `surface_render_method` directly and READ IT BACK.

    `mat.blend_method` is a legacy alias on 4.2+ and cannot express CLIP
    (`engine-confirmed (Blender 5.1.1)`), so it is not used at all here.
    """
    want = surface_render_method_for(render_mode)
    got = None
    try:
        mat.surface_render_method = want
        got = mat.surface_render_method
    except (AttributeError, TypeError):
        # Blender 4.1 and earlier: only the legacy alias exists.
        try:
            mat.blend_method = {"BLEND": "BLEND", "CLIP": "CLIP"}.get(render_mode, "OPAQUE")
            got = mat.blend_method
        except Exception:
            pass
    mat["le_surface_render_method"] = got or ""
    return got or ""


def build_material(spec: dict, pkg_dir: Path, opts: dict | None = None) -> "bpy.types.Material":
    opts = opts or {}
    key = spec["key"]
    mat = bpy.data.materials.new(name=key)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    out_node = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)

    channels = spec.get("channels", {}) or {}
    channels = supersede_opaque_top_layer(spec, channels)
    alpha_ch, trans_ch = split_opacity_channels(channels)
    render_mode = resolve_render_mode(spec)
    mattype = spec.get("mattype")
    # A/B only (`opts['skirt_alpha']=0`): reproduce the pre-fix picture, in which
    # the decal pass was opaque and its cut-out alpha was never read.
    skirt_off = is_skirt(spec) and not skirt_alpha_enabled(opts)
    if skirt_off:
        render_mode = "OPAQUE"
        alpha_ch = None

    # provenance -------------------------------------------------------------
    mat["le_shaderset"] = spec.get("shaderset_hash", "")
    mat["le_material"] = spec.get("material_hash", "")
    for ch, data in channels.items():
        mat[f"le_tex_{ch}"] = data.get("texture", "")
    mat["le_render_mode"] = render_mode
    mat["le_mattype"] = mattype if isinstance(mattype, int) else -1
    mat["le_mattype_name"] = str(spec.get("mattype_name") or "")
    bm = spec.get("blend_mode")
    mat["le_blend_mode"] = bm if isinstance(bm, int) else -1
    mat["le_blend_mode_name"] = str(spec.get("blend_mode_name") or "")
    mat["le_k_alpha"] = k_alpha(spec)
    if is_lossy_blend(spec) or spec.get("alpha_blend_lossy"):
        # eBlendAdditive / eBlendLinearDodge have no EEVEE equivalent (findings 4).
        mat["le_blend_lossy"] = True
    if is_skirt(spec):
        # The DECAL pass. Blender has no separate pass, so it is imported as an
        # alpha-blended surface (the equation itself is `inferred` -- see
        # le_mesh.materials.render_mode_for) and tagged so a caller can still
        # find every skirt in the scene.
        mat["le_skirt"] = True
        if spec.get("render_mode") == "OPAQUE" and render_mode == "BLEND":
            mat["le_skirt_render_mode_repaired"] = (
                "manifest said OPAQUE (pre-jack-patch-layers decoder); "
                "the decal pass reads its diffuse alpha")

    # base colour ------------------------------------------------------------
    # `base_color_factor` is `SGMaterialData.bakecolor` == the authored
    # `k_hardware_color`, UI name "Bake Color" (`name-confirmed`, the material
    # asset schema). Across the whole shipped shader tree that symbol appears
    # ONLY in the authoring schema and NEVER in a shader -- the runtime albedo
    # tint is the per-layer `k_albedo_tint_color`
    # (`shader-confirmed`). So it is NOT multiplied into the texture; it is
    # used only as the flat fallback colour when there is no base-colour map, where
    # it is the baker's own approximation of the surface. Multiply-vs-replace
    # decided on that evidence: REPLACE (fallback only).
    bc = channels.get("base_color")
    base_in = _principled_input(bsdf, "Base Color")
    bc_node = None
    bc_factor = base_color_fallback(spec)

    # ⚠ An emissive TRANSPARENT surface that binds no albedo must not fall back
    # to the white multiplier.
    #
    # `base_color_factor` is `bakecolor`, and bakecolor is an identity
    # multiplier -- (1,1,1,1) on 294 of 318 `mpl_combat_fission` materials. The
    # engine's own default is the same (`MatShaderDefaultOutput`:
    # `output.basecolor = 1.0`), and that is harmless in-engine because these
    # surfaces are drawn BLENDED and their look comes from the emissive term.
    # Imported into a DCC they land as OPAQUE WHITE, which is why light strips,
    # holograms and glow panels showed up as solid white blobs.
    #
    # Measured on `mpl_combat_fission`: 117 of 318 materials bind no albedo or
    # diffuse role at ALL (not a resolution failure -- every texture their
    # models stream is already bound to some other role). 112 of those are
    # BLEND and 107 carry an emission channel.
    #
    # Restricted to exactly that case: no base-colour channel AND a real
    # emissive source AND a blended render mode. A genuinely opaque untextured
    # material keeps the flat fallback, since black would be a worse answer
    # there than white.
    # Widened: the emission channel is NOT required. `aa7dfd7c3d316d01`
    # (mpl_combat_fission matidx 88, the 5286- and 1764-vertex pieces of
    # `570677a85028cfa9`) binds rim, blend-mask, normal and alpha roles and no
    # emission at all, and came out an opaque white body. A BLENDED surface
    # that binds no albedo has no diffuse reflectance to show either way, so
    # white is never the better answer there. 112 of the 117 albedo-less
    # fission materials are BLEND; the 5 OPAQUE ones keep the flat fallback.
    # ⛔ Only when the material is AUTHORED as blended. `resolve_render_mode`
    # forces BLEND onto `eMTSkirt` (the decal pass) because Blender has no
    # separate decal pass -- that is an IMPORT decision, not something the
    # material said. Treating an imposed BLEND as "this surface is transparent"
    # made every rim-only DECAL vanish: `a2630eab179ee4a2` is `eMTSkirt` +
    # `eBlendOpaque`, and its props (`mpl_lobby_b2` i663/i899) imported
    # see-through instead of merely untextured.
    #
    # The material this rule WAS written for, `aa7dfd7c3d316d01`, is
    # `eMTForwardTransparent` + `eBlendTransparent` -- authored blended, and
    # still handled exactly as before. The two cases separate cleanly: of the
    # lobby's 31 no-colour-source BLEND materials, the 16 that are
    # `eBlendOpaque` are all `eMTSkirt`, and the other 15 are Additive /
    # LinearDodge / Translucent.
    #
    # An opaque decal with nothing to draw is the case the rim-map note already
    # calls correct: "a flat colour with NO texture is the faithful result" --
    # untextured, NOT invisible.
    authored_blended = int(spec.get("blend_mode") or 0) != 0
    nothing_to_draw = (bc is None
                       and channels.get("emission") is None
                       and resolve_render_mode(spec) == "BLEND"
                       and authored_blended)
    # Black base ONLY when emission is the colour source -- otherwise a white
    # base would be added on top of the glow and double it. With no emission
    # either there is nothing to double, and zeroing turns an untextured decal
    # into a black one; the flat `bakecolor` fallback is the faithful result.
    if (bc is None and not nothing_to_draw
            and channels.get("emission") is not None
            and resolve_render_mode(spec) == "BLEND"):
        bc_factor = (0.0, 0.0, 0.0, 1.0)
        mat["le_basecolor_zeroed_emissive"] = True
    # A base colour that lives on layer >= 1 is composited over the lower layers
    # by that layer's blend mask; with no lower-layer albedo the engine's lerp
    # runs from the flat fallback colour to the texture (`inferred` base value --
    # the lower layers genuinely bind no albedo, so the engine samples its own
    # `albedo_map` default there and we cannot see that value from disk).
    bc_blend = blend_for_channel(spec, channels, "base_color")
    bc_gate = (None, None)
    if bc_blend is not None:
        bc_gate = _layer_gate(nt, pkg_dir, bc_blend, "base_color", opts, -2400, 700)

    if bc and base_in and bc_gate[1] != 0.0:
        img = _load_image(pkg_dir, bc.get("file", ""), bc.get("colorspace", "sRGB"),
                          image_alpha_mode(bc))
        if img:
            bc_node = _tex_node(nt, img, bc.get("colorspace", "sRGB"), -900, 300,
                                image_alpha_mode(bc), label="base_color")
            if bc_blend is None or bc_gate == (None, 1.0):
                nt.links.new(bc_node.outputs["Color"], base_in)
            else:
                mix, (fi, ai, bi, ri) = _mix_node(
                    nt, "RGBA", "MIX", -560, 420,
                    label=f"layer{bc_blend['layer']} blend mask")
                mix.inputs[ai].default_value = bc_factor
                nt.links.new(bc_node.outputs["Color"], mix.inputs[bi])
                if bc_gate[0] is not None:
                    nt.links.new(bc_gate[0], mix.inputs[fi])
                else:
                    mix.inputs[fi].default_value = float(bc_gate[1])
                nt.links.new(mix.outputs[ri], base_in)
                mat["le_layer_blend_base_color"] = bc_blend["layer"]
    if base_in and bc_node is None:
        base_in.default_value = bc_factor

    # ── team / accent tint ──────────────────────────────────────────────
    # ⭐ `mpl_arena_a`'s goal ends are the same mesh with the same four
    # GREYSCALE textures; the only thing that differs between the blue end and
    # the orange end is this per-material colour (see `evr_materials`, where it
    # is decoded). Without it both import grey, which is what they did.
    #
    # ⚠ Multiplied into base colour, which makes the surface read as the right
    # team colour. The slot's own name is NOT recovered and it sits beside two
    # unrouted rim maps in a tint+intensity+power group, so a faithful renderer
    # would more likely drive a Fresnel rim term with it -- that is a bigger
    # change and is not attempted here. Disable with `evr_accent_tint`.
    _accent = spec.get("accent_tint") if accent_tint_enabled(opts) else None
    if _accent and base_in is not None and len(_accent) >= 3:
        _tint = (float(_accent[0]), float(_accent[1]), float(_accent[2]), 1.0)
        if base_in.is_linked:
            _up = base_in.links[0].from_socket
            _mix, (_fi, _ai, _bi, _ri) = _mix_node(
                nt, "RGBA", "MULTIPLY", -300, 520, label="accent tint")
            _mix.inputs[_fi].default_value = 1.0
            nt.links.new(_up, _mix.inputs[_ai])
            _mix.inputs[_bi].default_value = _tint
            nt.links.new(_mix.outputs[_ri], base_in)
        else:
            _base = list(base_in.default_value)
            base_in.default_value = (_base[0] * _tint[0], _base[1] * _tint[1],
                                     _base[2] * _tint[2], _base[3])
        mat["le_accent_tint"] = [round(float(c), 6) for c in _accent[:3]]

    # roughness / AO ---------------------------------------------------------
    rg = channels.get("roughness")
    rough_in = _principled_input(bsdf, "Roughness")
    ao_socket = None
    # two-lobe weight, shared with the specular block below
    lobes = brdf_lobes_of(spec, pkg_dir) if lobe_blend_enabled(opts) else None
    rg_node = None          # the components Image Texture node, once built
    rg_sep = None           # its Separate Color, so B is split only once
    lobe_weight_socket = None
    rg_blend = blend_for_channel(spec, channels, "roughness")
    rg_gate = (None, None)
    if rg_blend is not None:
        rg_gate = _layer_gate(nt, pkg_dir, rg_blend, "roughness", opts, -2400, 200)
    if rg and rough_in and rg_gate[1] != 0.0:
        # ⚠ Was hardcoded `"Non-Color"`. The DXGI format is the authority -- an
        # `_SRGB` view is linearised BY THE SAMPLER, so the shader sees linear and
        # Blender must apply the same decode; the hardcode would have read the raw
        # stored value instead. Provably a NO-OP on everything shipped: across all
        # 440 materials in `blender_tool/exports`, **zero** roughness or normal
        # channels are an `_SRGB` format (`stream-confirmed` -- 67 BC1_UNORM,
        # 17 BC4_UNORM, 4 BC3_UNORM, 11 with no recorded format, all Non-Color),
        # so this only removes a trap. The normal map keeps its hardcode on
        # purpose: `colorspace_for()` already forces Non-Color there structurally
        # because sRGB-decoding a tangent-space normal is the worst single
        # mistake available in this file.
        rcs = rg.get("colorspace", "Non-Color")
        img = _load_image(pkg_dir, rg.get("file", ""), rcs, image_alpha_mode(rg))
        if img:
            node = _tex_node(nt, img, rcs, -1200, 0, image_alpha_mode(rg),
                             label="composite_components")
            rough_src = node.outputs["Color"]
            if roughness_is_sqrt(spec, rg):
                # ⚠ DO NOT SQUARE HERE. RAD's GGX alpha is `sqrtroughness^2`
                # (`params.m = sqrtroughness*sqrtroughness`; `GGX_Specular`
                # puts `m2 = m*m` in the NDF numerator). Blender's GGX alpha is
                # `Roughness^2`. Equating the two gives
                #     Roughness == sqrtroughness == composite_components.x, RAW.
                # Squaring it made Blender's alpha `sqrtroughness^4` and the peak
                # highlight 2.4x (at 0.80) to 920x (at 0.15) too bright, measured
                # against the RAD closed form -- `engine-confirmed`, see
                # docs/MATERIALS.md 6.
                # `roughness_is_sqrt` still means "this texel is in sqrt space";
                # only the Blender-side conversion is the identity.
                sep = nt.nodes.new("ShaderNodeSeparateColor")
                sep.location = (-900, 0)
                nt.links.new(node.outputs["Color"], sep.inputs["Color"])
                # ROUGHNESS IS THE GREEN CHANNEL, not red.
                # Verified against the game on `d09afd15b1c75c04` instance
                # i1535: red produced visibly wrong gloss, green matches.
                rough_src = sep.outputs[1]
                rg_node, rg_sep = node, sep
                ao = ao_channel_of(spec, rg)
                if ao:
                    ao_socket = sep.outputs[{"R": 0, "G": 1, "B": 2}.get(ao, 1)]
                    mat["le_ao_channel"] = ao
            # --- two-lobe weight and roughness blend -------------------------
            # `brdfblends.y` is `.z` of THIS texture, so the weight socket is
            # free once the Separate Color exists. The blend is
            #   sqrtroughness = lerp(.x, .w, .z)
            # i.e. `brdfweights.x * sqrtroughness[0] + brdfweights.y *
            # sqrtroughness[1]` written as Blender's Mix(factor = brdfblends.y).
            # `inferred`: the engine keeps the two lobes separate and weights
            # their RADIANCE; Blender has one lobe, so the same convex
            # weight is applied to the roughness instead. The albedo half of the
            # same weighting is `shader-confirmed`.
            if (lobes is not None and rg_sep is not None and rg_node is not None
                    and str(rg.get("texture", "")) == str(lobes.get("weight_texture", ""))):
                wcomp = lobe_weight_component(lobes)
                lobe_weight_socket = (rg_sep.outputs[{"R": 0, "G": 1, "B": 2}[wcomp]]
                                      if wcomp != "A" else rg_node.outputs["Alpha"])
                mat["le_brdf_weight_channel"] = wcomp
                # ★★ THE ZERO-ROUGHNESS GATE — the two renderers have OPPOSITE
                # semantics at `sqrtroughness == 0`, and that is the value most of
                # Liv's atlas carries.
                #
                #   RAD:     GGX numerator `m2 = sqrtroughness**4` (the engine's
                #            own NDF; docs/MATERIALS.md §1) => at 0 the lobe
                #            contributes IDENTICALLY NOTHING.
                #   Blender: `Roughness = 0` is a special-cased PERFECT MIRROR at
                #            full energy.
                #
                # Measured `components.x == 0` fraction of mip-0 texels
                # (`stream-confirmed`, decoded from the shipped bytes):
                #   Liv     68.1 % · 53.7 % · 42.9 % · 42.1 % · **100.0 %** (harness)
                #   Jack    0.00 – 0.03 %      android  0.00 – 0.05 %
                # Same rig, same graph, same package format — which is exactly why
                # she reads as wet lacquer and they do not. Those texels also carry
                # a lobe-0 F0 of ~0.43-0.48 median, so Blender was being told 38.9 %
                # of her suit is "mirror-smooth and more reflective than aluminium".
                #
                # ⛔ The earlier shading audit's reading of these texels as "a
                # perfectly sharp black-lacquer mirror" is REFUTED: by the engine's
                # own NDF they are the OFF state, not the shiniest one.
                #
                # The gate: `factor' = max(z, 1 - (x > 0))`. Where lobe 0 is live
                # (x > 0) it is `z`, unchanged — a provable no-op on every Jack and
                # android texel. Where lobe 0 is degenerate it is 1, i.e. lobe 1
                # only, so roughness becomes `.w` and F0 becomes lobe 1's.
                #
                # ⚠ HONEST LIMIT: where `x == 0` AND `z == 0` the engine renders NO
                # specular at all, while this renders lobe 1's parameters at full
                # weight (F0 ~0.02, roughness ~0.42) — a faint matte sheen instead
                # of nothing. Single-lobe Blender cannot express the attenuation;
                # this is strictly closer than a mirror, and it is `inferred`.
                if lobe_zero_roughness_gate(opts):
                    live = _math(nt, "GREATER_THAN", -960, 200, rough_src, 0.0,
                                 label="lobe 0 is live (sqrtroughness > 0)")
                    dead = _math(nt, "SUBTRACT", -880, 200, 1.0, live.outputs[0],
                                 label="lobe 0 is degenerate")
                    gated = _math(nt, "MAXIMUM", -800, 200, lobe_weight_socket,
                                  dead.outputs[0],
                                  label="max(brdfblends.y, lobe0 degenerate)")
                    lobe_weight_socket = gated.outputs[0]
                    mat["le_brdf_zero_roughness_gate"] = True
                if blends_roughness(lobes):
                    r1comp = lobe1_roughness_component(lobes)
                    r1 = (rg_node.outputs["Alpha"] if r1comp == "A"
                          else rg_sep.outputs[{"R": 0, "G": 1, "B": 2}[r1comp]])
                    rmix, (fi, ai, bi, ri) = _mix_node(
                        nt, "FLOAT", "MIX", -740, 120,
                        label="sqrtroughness = lerp(lobe0.x, lobe1.w, brdfblends.y)")
                    nt.links.new(lobe_weight_socket, rmix.inputs[fi])
                    nt.links.new(rough_src, rmix.inputs[ai])
                    nt.links.new(r1, rmix.inputs[bi])
                    rough_src = rmix.outputs[ri]
                    mat["le_brdf_roughness_blended"] = True
            if rg_blend is None or rg_gate == (None, 1.0):
                nt.links.new(rough_src, rough_in)
            else:
                # `BlendValue(base.sqrtroughness, layer.sqrtroughness, m *
                # roughness_blend_alpha, mode)`. The lower layers bind
                # no components map, so `base` is the socket value that would
                # otherwise stand (`inferred`).
                mix, (fi, ai, bi, ri) = _mix_node(
                    nt, "FLOAT", "MIX", -520, 40,
                    label=f"layer{rg_blend['layer']} blend mask")
                mix.inputs[ai].default_value = float(rough_in.default_value)
                nt.links.new(rough_src, mix.inputs[bi])
                if rg_gate[0] is not None:
                    nt.links.new(rg_gate[0], mix.inputs[fi])
                else:
                    mix.inputs[fi].default_value = float(rg_gate[1])
                nt.links.new(mix.outputs[ri], rough_in)
                mat["le_layer_blend_roughness"] = rg_blend["layer"]

    # AO wiring, documented choice: LEFT UNCONNECTED by default.
    # `layers[i].ambientocclusion` only ever multiplies the engine's *ambient /
    # indirect* diffuse term (`shader-confirmed`); Principled has no occlusion input,
    # so the only place to put it is Base Color -- which would also darken direct
    # light, double-darkening every lit surface. The socket is exposed (the Separate
    # Color node is in the graph, and `le_ao_channel` records which output) so a user
    # can wire it, and `opts["ao_to_base_color"]` opts in to the approximation.
    if ao_socket is not None and opts.get("ao_to_base_color") and base_in is not None:
        mix = nt.nodes.new("ShaderNodeMix")
        mix.data_type = "RGBA"
        mix.blend_type = "MULTIPLY"
        mix.location = (-500, 300)
        mix.label = "AO x albedo (opt-in)"
        mix.inputs["Factor"].default_value = 1.0
        src = base_in.links[0].from_socket if base_in.links else None
        if src is not None:
            nt.links.new(src, mix.inputs[6])            # A (RGBA)
        else:
            mix.inputs[6].default_value = tuple(base_in.default_value)
        nt.links.new(ao_socket, mix.inputs[7])          # B (RGBA, float broadcast)
        nt.links.new(mix.outputs[2], base_in)
        mat["le_ao_applied"] = True

    # normal -----------------------------------------------------------------
    # `output.normal = BlendValue(base.normal, layer.normal, m * normal_blend_alpha,
    # mode)` (`shader-confirmed`). The base is the
    # lower layers' normal, and the merged view only ever hands back a layer >= 1
    # when no lower layer binds one at all -- so `base.normal` is the FLAT
    # tangent-space normal (0.5, 0.5, 1.0) and the lerp is exact for the same
    # reason `alpha`'s is (`inferred` only in that the engine's own sampler
    # default for an unbound `composite_normals` is assumed flat).
    #
    # ⚠ This gate was absent until 2026-08-05 on the premise that "the lowest
    # layer wins for `normal` in every corpus material". That premise expired
    # when the corpus role index started naming a
    # layer-1 quartet whose layer-0 counterpart stays unrouted: Jack's legs
    # (`28b682b9af140fbf`, `layer1_blend_mask = jck_body_damage_bubble_a_msk`,
    # `mask_offset = -1.0` => `suppressed_at_rest`) rendered his BATTLE-DAMAGE
    # relief across the whole limb while every other channel correctly fell back.
    nm = channels.get("normal")
    norm_in = _principled_input(bsdf, "Normal")
    nm_blend = blend_for_channel(spec, channels, "normal")
    nm_gate = (None, None)
    if nm_blend is not None:
        nm_gate = _layer_gate(nt, pkg_dir, nm_blend, "normal", opts, -2400, -300)
    if nm and norm_in and nm_gate[1] == 0.0:
        mat["le_layer_blend_normal_suppressed"] = True
    if nm and norm_in and nm_gate[1] != 0.0:
        img = _load_image(pkg_dir, nm.get("file", ""), "Non-Color", image_alpha_mode(nm))
        if img:
            tex = _tex_node(nt, img, "Non-Color", -2200, -300, image_alpha_mode(nm),
                            label="normal")
            src = _normal_chain(nt, tex, nm.get("reconstruct_z", False), -2000, -300)
            if nm_blend is not None and nm_gate != (None, 1.0):
                mix, (fi, ai, bi, ri) = _mix_node(
                    nt, "RGBA", "MIX", -760, -300,
                    label=f"layer{nm_blend['layer']} blend mask")
                mix.inputs[ai].default_value = FLAT_TANGENT_NORMAL
                nt.links.new(src, mix.inputs[bi])
                if nm_gate[0] is not None:
                    nt.links.new(nm_gate[0], mix.inputs[fi])
                else:
                    mix.inputs[fi].default_value = float(nm_gate[1])
                src = mix.outputs[ri]
                mat["le_layer_blend_normal"] = nm_blend["layer"]
            # R1 — the SHIPPED tangent basis, or Blender's UV-derived one.
            # `mesh_builder` has written `le_tangent`/`le_tangent_w` on 913 of 913
            # objects since 2026-08-05 and NOTHING read them: three writers, zero
            # readers, so every normal map ran on mikktspace. See
            # `shipped_tangent_enabled` for the divergence numbers and
            # `_shipped_tangent_normal` for the graph.
            nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-500, -300)
            nt.links.new(src, nmap.inputs["Color"])
            if shipped_tangent_enabled(opts):
                shipped, sn = _shipped_tangent_normal(nt, src, -3000, -1400)
                # ⛔ Mix, do not switch: an object with no `le_tangent` gets the
                # mikktspace leg per PIXEL, so this can never blacken a surface
                # that used to render.
                mix, (fi, ai, bi, ri) = _mix_node(
                    nt, "VECTOR", "MIX", -300, -300, label="tangent basis")
                nt.links.new(sn["have_tangent"], mix.inputs[fi])
                nt.links.new(nmap.outputs["Normal"], mix.inputs[ai])
                nt.links.new(shipped, mix.inputs[bi])
                nt.links.new(mix.outputs[ri], norm_in)
                mat["le_tangent_basis"] = "shipped"
            else:
                nt.links.new(nmap.outputs["Normal"], norm_in)
                mat["le_tangent_basis"] = "mikktspace"

    # alpha chain ------------------------------------------------------------
    # engine (`shader-confirmed`):
    #   alpha = albedovertex.a * ... * albedomap.a * ... * alphamap * k_alpha
    # Blender: multiply the available terms and drive Alpha with the product. The
    # per-map sRGB->linear that the engine applies to each alpha term in the
    # `use_output_alpha_` permutation is NOT reproduced -- which permutation is live
    # is not on disk (findings 4 "NOT derivable").
    alpha_in = _principled_input(bsdf, "Alpha")
    terms = []
    # D9: `channels["alpha"]` derived from the base colour is the SAME sampler as
    # `bc_node`; taking both squares the alpha. Prefer the node that already
    # exists and skip the duplicate load. (`bc_node is None` when a layer gate
    # suppressed the base colour, in which case the derived channel is the only
    # way to reach that alpha and the normal path below runs.)
    alpha_is_bc = (bc_node is not None
                   and alpha_channel_is_base_color(alpha_ch, channels.get("base_color")))
    if alpha_ch and not alpha_is_bc:
        # ⚠ Do NOT hardcode "Non-Color" here. Two of the four shipped alpha-map
        # formats are `_SRGB` views (72, 78); an `_SRGB` SRV is linearised BY THE
        # SAMPLER, so `k_alpha_map[i].x` reaches the shader already linear. Forcing
        # Non-Color makes Blender's `.r` the raw stored value instead -- 0.211 vs the
        # engine's 0.036 on `494a47bd33bb1e20`, a 5.8x error, and the SAME error
        # `alpha_component_of` fixes from the other side. `inferred`: this assumes the
        # SRV format matches the DDS header format, which is exactly the assumption
        # `colorspace_for()` already makes for every other channel.
        # (The ALPHA plane of any texture stays linear either way -- this is RGB only.)
        acs = alpha_ch.get("colorspace", "Non-Color")   # DXGI-authoritative
        img = _load_image(pkg_dir, alpha_ch.get("file", ""), acs,
                          image_alpha_mode(alpha_ch))
        if img:
            node = _tex_node(nt, img, acs, -1200, -700, image_alpha_mode(alpha_ch),
                             label="alpha_map")
            terms.append(_component_socket(nt, node, alpha_component_of(alpha_ch),
                                           -900, -700))
    if bc_node is not None and not skirt_off \
            and (alpha_is_bc or uses_base_color_alpha(spec, channels)):
        # layer0_composite_diffuse.a IS the opacity (`shader-confirmed`).
        terms.append(bc_node.outputs["Alpha"])
        mat["le_alpha_from_base_color"] = True

    ka = k_alpha(spec)
    alpha_socket = None
    if alpha_in is not None:
        acc = None
        if terms:
            acc = terms[0]
            for i, t in enumerate(terms[1:]):
                acc = _math(nt, "MULTIPLY", -600 + i * 180, -700, acc, t).outputs[0]
            # `output.alpha = BlendValue(base.alpha, layer.alpha, m *
            # transparency_blend_alpha, mode)`. The lower layers bind
            # no alpha source, so `base.alpha` is 1.0 and the lerp is
            # `1 - m + m * a`.
            a_blend = blend_for_channel(spec, channels, "alpha")
            if a_blend is not None:
                sock, const = _layer_gate(nt, pkg_dir, a_blend, "alpha", opts,
                                          -2400, -900)
                if const == 0.0:
                    acc = None                 # layer never shows -> alpha stays 1
                    mat["le_layer_blend_alpha_suppressed"] = True
                elif const != 1.0:
                    mix, (fi, ai_, bi, ri) = _mix_node(
                        nt, "FLOAT", "MIX", -470, -760,
                        label=f"layer{a_blend['layer']} blend mask")
                    mix.inputs[ai_].default_value = 1.0
                    nt.links.new(acc, mix.inputs[bi])
                    if sock is not None:
                        nt.links.new(sock, mix.inputs[fi])
                    else:
                        mix.inputs[fi].default_value = float(const)
                    acc = mix.outputs[ri]
                    mat["le_layer_blend_alpha"] = a_blend["layer"]
        if nothing_to_draw:
            # ⚠ A BLENDED surface that binds NO base colour and NO emission has
            # no colour source at all -- the same situation the eBlendAdditive
            # branch below already resolves by contributing the identity rather
            # than guessing. `aa7dfd7c3d316d01` (mpl_combat_fission matidx 88)
            # is one: rim + normal + blend masks + an alpha map that is 1.0
            # across every texel, wrapped around the model as a second shell.
            #
            # In-engine it is a rim term that shows only at grazing angles. A
            # Principled BSDF cannot express that, and BOTH flat answers are
            # wrong in the same way -- white hid the model behind a white shell,
            # black (this file's previous fix) hid it behind a black one. The
            # honest result is a surface that contributes nothing, which is
            # what deleting the object by hand achieves.
            #
            # 10 of `mpl_combat_fission`'s 117 albedo-less materials are in
            # this class; the other 107 carry emission and keep it.
            acc = None
            alpha_in.default_value = 0.0
            mat["le_no_color_source_transparent"] = (
                "BLEND with no base colour and no emission: rim/mask-only "
                "shell, contributes nothing rather than a flat guess")
        elif acc is not None:
            if ka != 1.0:
                acc = _math(nt, "MULTIPLY", -400, -700, acc, ka, label="k_alpha").outputs[0]
            alpha_socket = acc
        elif ka != 1.0:
            alpha_in.default_value = ka          # B1: k_alpha with no map
        if render_mode == "CLIP":
            # EEVEE Next has no CLIP render method -- the cutout is a node op
            # (the engine's `clip(alpha - k_alpha_threshold_)`).
            thr = alpha_threshold_for(spec)
            src = alpha_socket if alpha_socket is not None else ka
            cut = _math(nt, "GREATER_THAN", -220, -700, src, thr, label="alpha test")
            alpha_socket = cut.outputs[0]
            mat["le_alpha_threshold"] = thr
        if alpha_socket is not None:
            nt.links.new(alpha_socket, alpha_in)

    # emission ---------------------------------------------------------------
    # Emission Color    = layerN_emissive_map x layerN_emissive_tint_color
    # Emission Strength = layerN_emissive_intensity x k_emissive_scale
    # No unit conversion: the factor is 1.0 (`shader-confirmed`, findings 4).
    em = channels.get("emission")
    em_col_in = _principled_input(bsdf, "Emission Color", "Emission")
    em_str_in = _principled_input(bsdf, "Emission Strength")
    tint = emission_tint(spec)
    authored_strength = emission_strength(spec)
    # Scale the AUTHORED value rather than replacing it, so a material that
    # deliberately emits at 2.0 stays twice as bright as its 1.0 neighbour at
    # every setting -- the relative grading the artists authored survives.
    emission_scale = emission_multiplier(opts)
    strength = authored_strength * emission_scale
    # An emissive map on layer >= 1 rides in `LayerOutput.lighting` and is
    # composited by that layer's blend mask. No lower layer binds an
    # emissive map -- if one did, the merged view would have selected it -- so
    # `base.lighting` is 0 and the engine's lerp collapses EXACTLY to
    # `m * emissive`, i.e. a plain multiply. That is why this is not an
    # approximation and why it is applied to Emission Strength when `m` is
    # spatially constant.
    em_blend = blend_for_channel(spec, channels, "emission")
    em_gate = (None, None)
    if em_blend is not None:
        em_gate = _layer_gate(nt, pkg_dir, em_blend, "emission", opts, -2400, -1300)
        mat["le_layer_blend_emission"] = em_blend["layer"]
        mat["le_layer_blend_emission_mask"] = str((em_blend.get("mask") or {}).get("texture", ""))
        mat["le_layer_blend_mask_offset"] = blend_mask_offset_for(em_blend, opts)
        if em_gate[1] is not None:
            mat["le_layer_blend_emission_amount"] = float(em_gate[1])
        if em_gate == (None, 0.0):
            # `saturate(mask.R * scale + offset) == 0` for every possible texel:
            # the layer is parked at its animated OFF extreme (see
            # `blend_mask_offset_for`). Emission is zero, not dimmed.
            strength = 0.0
            mat["le_layer_blend_emission_suppressed"] = True
            if em_str_in:
                em_str_in.default_value = 0.0
    if em and em_col_in and em_gate != (None, 0.0):
        img = _load_image(pkg_dir, em.get("file", ""), em.get("colorspace", "sRGB"),
                          image_alpha_mode(em))
        if img:
            node = _tex_node(nt, img, em.get("colorspace", "sRGB"), -900, -1100,
                             image_alpha_mode(em), label="emissive_map")
            em_src = node.outputs["Color"]
            if tint != (1.0, 1.0, 1.0):
                mix = nt.nodes.new("ShaderNodeMix")
                mix.data_type = "RGBA"
                mix.blend_type = "MULTIPLY"
                mix.location = (-500, -1100)
                mix.label = "emissive tint"
                mix.inputs["Factor"].default_value = 1.0
                nt.links.new(node.outputs["Color"], mix.inputs[6])
                mix.inputs[7].default_value = (tint[0], tint[1], tint[2], 1.0)
                em_src = mix.outputs[2]
            if em_gate[0] is not None:
                gmix, (fi, ai_, bi, ri) = _mix_node(
                    nt, "RGBA", "MULTIPLY", -300, -1180,
                    label=f"layer{em_blend['layer']} blend mask")
                gmix.inputs[fi].default_value = 1.0
                nt.links.new(em_src, gmix.inputs[ai_])
                nt.links.new(em_gate[0], gmix.inputs[bi])   # float -> RGBA broadcast
                em_src = gmix.outputs[ri]
            elif em_gate[1] is not None:
                strength *= float(em_gate[1])

            # ── EMISSIVE LAYERS ABOVE THE LOWEST ──────────────────────────
            # `em`/`em_src` above is the MERGED view, i.e. the lowest layer
            # only. Composite the rest over it in index order, each through its
            # own blend gate, exactly as `BlendLayers()` does. With a layer's
            # `amount_constant` at 1.0 the lerp collapses to "upper replaces
            # lower", which is how a lock/overlay layer is authored.
            _stack = emission_layers(spec)
            if len(_stack) > 1:
                _y = -1100
                for _idx, _chan in _stack[1:]:
                    _img = _load_image(pkg_dir, _chan.get("file", ""),
                                       _chan.get("colorspace", "sRGB"),
                                       image_alpha_mode(_chan))
                    if _img is None:
                        continue
                    _y -= 260
                    _node = _tex_node(nt, _img, _chan.get("colorspace", "sRGB"),
                                      -900, _y, image_alpha_mode(_chan),
                                      label=f"emissive_map L{_idx}")
                    _blend = layer_blend_of(spec, _idx)
                    _sock, _const = (None, 1.0)
                    if _blend is not None:
                        _sock, _const = _layer_gate(nt, pkg_dir, _blend,
                                                    "emission", opts, -2400, _y)
                    if _sock is None and _const is not None and _const <= 0.0:
                        continue                 # parked at its OFF extreme
                    # `le_mesh.materials`: mode 6/10 are the LERP forms
                    # `(1-m)*base + m*layer` (6 is the authored default), while
                    # 1/7 are ADDITIVE `base + layer*m`. Using one for the other
                    # is the difference between an overlay replacing what is
                    # under it and glowing on top of it.
                    _lbm = int((_blend or {}).get("blend_mode", 6) or 6)
                    _op = "ADD" if _lbm in (1, 7) else "MIX"
                    _mix, (_fi, _ai, _bi, _ri) = _mix_node(
                        nt, "RGBA", _op, -500, _y,
                        label=f"emissive layer {_idx} ({_op.lower()})")
                    nt.links.new(em_src, _mix.inputs[_ai])
                    nt.links.new(_node.outputs["Color"], _mix.inputs[_bi])
                    if _sock is not None:
                        nt.links.new(_sock, _mix.inputs[_fi])
                    else:
                        _mix.inputs[_fi].default_value = (
                            1.0 if _const is None else float(_const))
                    em_src = _mix.outputs[_ri]
                mat["le_emissive_layers"] = [int(i) for i, _c in _stack]

            # ── AO, OR GENUINELY EMISSIVE? ───────────────────────────────
            # The AO reading is right for most materials (verified against the
            # game on `d09afd15b1c75c04` i1535) but NOT for all, and the
            # discriminator is structural rather than a guess:
            #
            #   AO MODULATES A BASE COLOUR. A material that binds an emissive
            #   map and NO albedo has nothing to occlude, so the map cannot be
            #   ambient occlusion -- it is the surface's own light.
            #
            # `mpl_combat_dyson`'s sky (material 845a3a0897d15ecf) is exactly
            # that: one channel, `layer0_emissive_map`, no base colour. Treated
            # as AO it was inverted and multiplied into an empty Base Colour,
            # which painted the sky dome with its own signage texture.
            # ★ NARROWED. `has_albedo` alone said "AO" for every emissive
            # map in the reference extract -- all 1347 of them bind an albedo,
            # so NOTHING ever reached Emission and every glow mask was
            # inverted into Base Colour instead. `emissive_is_ao` keeps this
            # test and adds two that outrank it; see its docstring.
            treat_as_ao = emissive_is_ao(spec, spec.get("channels", {}))

            # ⛔ DO NOT gate this on the texture's COLORSPACE. Tried, wrong,
            # reverted -- and it broke 41 materials before it was caught.
            #
            # The reasoning was "an _SRGB view means the texture is authored as
            # colour, so it cannot be an occlusion mask". Measured, that is
            # false for this data. Of the 22 distinct emissive textures on
            # `mpl_combat_fission` materials that bind BOTH an albedo and an
            # emissive map, **20 are near-greyscale** (mean channel saturation
            # 0.000-0.096) -- i.e. masks -- and every single one of them is
            # sRGB. The engine stores greyscale AO masks in sRGB views quite
            # happily.
            #
            # Gating on colorspace therefore flipped those 20 from "invert and
            # multiply into base colour" to "drive Emission", so surfaces that
            # should have been DARKENED by ambient occlusion instead GLOWED
            # with the occlusion pattern.
            #
            # ⛔ TWO REFINEMENTS WERE TRIED AND REVERTED, both because they
            # guessed from how the texture LOOKED:
            #
            #   1. gating on the texture's COLORSPACE -- false premise, all 22
            #      candidates are sRGB yet 20 are greyscale masks;
            #   2. MEASURING per-texture saturation and calling coloured maps
            #      emissive -- flipped maps that are genuinely AO over to
            #      emissive in the viewport.
            #
            # Neither is used. Note that (2) would fail on the very case this
            # code now fixes: a character glow mask is GREYSCALE (measured
            # saturation 0.013), so "coloured means emissive" calls it AO.
            #
            # ⚠ `has_albedo` alone is no longer the whole test, because it
            # could not be: every one of the 1347 emissive-binding materials
            # in the reference extract binds an albedo, so it classified ALL
            # of them as AO and no material could ever emit light. It is kept
            # as the first test inside `emissive_is_ao`, which adds two that
            # outrank it -- one structural (the shader's own AO channel is
            # already bound), one that rejects a map too black to be an
            # occlusion term at all.
            #
            # The bar for changing this remains evidence from the RENDERED
            # result. What motivated the narrowing: a user-reported character
            # import where the glow mask was inverted into Base Colour, and a
            # decode of that map showing 98.1% of its texels pure black.
            if not treat_as_ao:
                # Genuinely emissive: drive Emission with the map and leave
                # Base Colour alone.
                node.label = "emissive_map"
                mat["le_emissive_is_ao"] = False
                if em_col_in is not None and not em_col_in.links:
                    nt.links.new(em_src, em_col_in)
                if em_str_in:
                    em_str_in.default_value = strength
                # Keep the authored value alongside the rendered one, so a
                # scaled import can always be told from a bright material.
                mat["le_emission_strength"] = strength
                mat["le_emission_strength_authored"] = authored_strength
                if emission_scale != EMISSION_STRENGTH_AUTHORED:
                    mat["le_emission_strength_multiplier"] = emission_scale
            else:
                inv = nt.nodes.new("ShaderNodeInvert")
                inv.location = (-700, -1100)
                inv.label = "ao invert"
                inv.inputs["Fac"].default_value = 1.0
                nt.links.new(em_src, inv.inputs["Color"])

                aomix, (afi, aai, abi, ari) = _mix_node(
                    nt, "RGBA", "MULTIPLY", -420, -980, label="ao multiply")
                aomix.inputs[afi].default_value = 1.0

                bc_in = _principled_input(bsdf, "Base Color")
                if bc_in is not None and bc_in.links:
                    nt.links.new(bc_in.links[0].from_socket, aomix.inputs[aai])
                elif bc_in is not None:
                    try:
                        aomix.inputs[aai].default_value = tuple(bc_in.default_value)
                    except Exception:
                        aomix.inputs[aai].default_value = (1.0, 1.0, 1.0, 1.0)
                nt.links.new(inv.outputs["Color"], aomix.inputs[abi])
                if bc_in is not None:
                    nt.links.new(aomix.outputs[ari], bc_in)

                node.label = "ao_map"
                mat["le_emissive_is_ao"] = True
                if em_str_in:
                    em_str_in.default_value = 0.0
    elif em_col_in:
        ec = spec.get("emissive_color", [0, 0, 0])
        if any(ec):
            em_col_in.default_value = (ec[0], ec[1], ec[2], 1.0)
            if em_str_in:
                em_str_in.default_value = strength

    # specular -> Principled `Specular Tint` ----------------------------------
    # `layers[i].specalbedo[0]` IS the Schlick F0 term (`shader-confirmed`),
    # fed either by `composite_specular.xyz * .w` or by
    # `specular_map.xyz * k_fresnel`. Both reach 1.0 in principle and shipped
    # `composite_specular` data reaches it in practice (`stream-confirmed`, 17
    # unique maps, mip 0, sRGB-decoded RGB x linear alpha: 3 maps hit 1.0, one
    # has p50 = 0.345 / p90 = 0.852 with 65.5% of texels above 0.08).
    #
    # The A7 verdict read that as unrepresentable because `Specular IOR Level` is
    # `hard_max = 1.0` -> F0 <= 0.08. That is only half the socket. `Specular
    # Tint` is `hard_max = FLT_MAX` (soft_max 1.0) and Principled's dielectric
    # normal-incidence reflectance is
    #
    #     F0 = F0(IOR) * 2 * `Specular IOR Level` * `Specular Tint`
    #
    # LINEAR and UNCLAMPED (`engine-confirmed`, Blender 5.1.1: with the level left
    # at its 0.5 "no adjustment" point and the tint set to F0/F0(IOR), the
    # rendered normal-incidence specular matched a Glossy BSDF of colour F0 to
    # 0.00% at every F0 in {0.01 .. 1.0} and for IOR in {1.33, 1.5, 2.0} in
    # Cycles; EEVEE Next tracks the same curve within 2%).
    #
    # `.w` is NOT double-counted: it is folded into F0 here (`specalbedo = .xyz *
    # .w`) and its OTHER engine role -- scaling the diffuse lobe by
    # `(1 - Fresnel(specintensity))` in both the direct and the irradiance path
    # -- is what Principled already does for free from the same F0 (measured: at
    # F0 = 0.85 the Principled total minus the specular-only lobe was 0.004782 vs
    # the engine's 0.004775 diffuse term, 0.15%).
    #
    # Residual, common to EVERY Blender construction and NOT fixable by wiring:
    # RAD's GGX visibility uses the Burley remap `alpha = ((m+1)/2)^2`
    # where Blender uses Smith with `alpha = roughness^2`. Equal
    # at normal incidence, Blender is ~1.4x brighter at 60 deg and ~9x at 85 deg
    # in the mirror configuration. See docs/MATERIALS.md.
    sp = channels.get("specular")
    sp_tint_in = _principled_input(bsdf, "Specular Tint")
    sp_level_in = _principled_input(bsdf, "Specular IOR Level")
    _ior_in = _principled_input(bsdf, "IOR")
    ior_used = (refractive_index(spec) if mattype == MATTYPE_REFRACTION
                else (float(_ior_in.default_value) if _ior_in is not None else 1.5))
    if sp:
        mat["le_specular_role"] = str(sp.get("role_key", ""))
    # F0 == 0 when the composite path binds no `composite_specular` -- see
    # `specular_ior_level_for`. Written BEFORE the wiring branch so it also holds
    # for a material whose specular texture turns out to be missing on disk.
    if sp_level_in is not None and wire_specular_enabled(opts):
        level = specular_ior_level_for(spec, channels, sp)
        sp_level_in.default_value = level
        if level == 0.0:
            mat["le_specular_f0_zero"] = (
                "composite path binds no composite_specular -> specalbedo[0] = "
                "common_black.xyz * .w = 0")
    if sp and not wire_specular_enabled(opts):
        mat["le_specular_unwired"] = "opts['wire_specular'] is False"
    elif sp and sp_tint_in is not None:
        sp_blend = blend_for_channel(spec, channels, "specular")
        sp_gate = (None, None)
        if sp_blend is not None:
            sp_gate = _layer_gate(nt, pkg_dir, sp_blend, "specular", opts, -2400, -650)
        img = None
        if sp_gate[1] != 0.0:
            img = _load_image(pkg_dir, sp.get("file", ""),
                              sp.get("colorspace", "sRGB"), image_alpha_mode(sp))
        if img is None:
            mat["le_specular_unwired"] = "specular texture unavailable"
        else:
            scale = specular_tint_scale(spec, sp, ior_used)
            node = _tex_node(nt, img, sp.get("colorspace", "sRGB"), -1300, -700,
                             image_alpha_mode(sp),
                             label=str(sp.get("role_key") or "specular"))
            src = node.outputs["Color"]
            if specular_scales_by_alpha(sp):
                amix, (fi, ai, bi, ri) = _mix_node(
                    nt, "RGBA", "MULTIPLY", -1020, -700,
                    label="specalbedo = rgb * a")
                amix.inputs[fi].default_value = 1.0
                nt.links.new(src, amix.inputs[ai])
                nt.links.new(node.outputs["Alpha"], amix.inputs[bi])
                src = amix.outputs[ri]
            # --- weight lobe [0] and add lobe [1] ----------------------------
            #   ambientspecalbedo = brdfweights.x * specalbedo[0]
            #                     + brdfweights.y * specalbedo[1]
            # (`shader-confirmed`) == Mix(factor = brdfblends.y, A = lobe0,
            # B = lobe1). With no `composite_data0` bound, lobe [1] is the
            # `common_black` default and B stays (0,0,0), so the same node
            # degenerates to the un-gated `brdfweights.x * specalbedo[0]`
            # -- one code path, both permutations.
            if lobes is not None and lobe_weight_socket is not None:
                lmix, (fi, ai, bi, ri) = _mix_node(
                    nt, "RGBA", "MIX", -900, -820,
                    label="specalbedo = lerp(lobe0, lobe1, brdfblends.y)")
                nt.links.new(lobe_weight_socket, lmix.inputs[fi])
                nt.links.new(src, lmix.inputs[ai])
                lobe1 = lobes.get("lobe1")
                lobe1_img = None
                if isinstance(lobe1, dict) and lobe1.get("file"):
                    lobe1_img = _load_image(pkg_dir, lobe1.get("file", ""),
                                            lobe1.get("colorspace", "sRGB"),
                                            image_alpha_mode(lobe1))
                if lobe1_img is not None:
                    l1 = _tex_node(nt, lobe1_img, lobe1.get("colorspace", "sRGB"),
                                   -1300, -1000, image_alpha_mode(lobe1),
                                   label=str(lobe1.get("role_key") or "composite_data0"))
                    l1mix, (f2, a2, b2, r2) = _mix_node(
                        nt, "RGBA", "MULTIPLY", -1020, -1000,
                        label="specalbedo[1] = rgb * a")
                    l1mix.inputs[f2].default_value = 1.0
                    nt.links.new(l1.outputs["Color"], l1mix.inputs[a2])
                    nt.links.new(l1.outputs["Alpha"], l1mix.inputs[b2])
                    nt.links.new(l1mix.outputs[r2], lmix.inputs[bi])
                    mat["le_brdf_lobe1_texture"] = str(lobe1.get("texture", ""))
                else:
                    # `composite_data0` default `common_black` -> specalbedo[1] = 0
                    lmix.inputs[bi].default_value = (0.0, 0.0, 0.0, 1.0)
                    mat["le_brdf_lobe1_texture"] = ""
                src = lmix.outputs[ri]
                mat["le_brdf_lobe_blend"] = (
                    "specalbedo = lerp(lobe0, lobe1, composite_components.B); "
                    "brdfweights = (1 - B, B)")
            smix, (fi, ai, bi, ri) = _mix_node(
                nt, "RGBA", "MULTIPLY", -780, -700,
                label=f"Specular Tint = F0 / F0(IOR)  (x{scale:g})")
            smix.inputs[fi].default_value = 1.0
            nt.links.new(src, smix.inputs[ai])
            smix.inputs[bi].default_value = (scale, scale, scale, 1.0)
            src = smix.outputs[ri]
            if sp_blend is not None and sp_gate != (None, 1.0):
                # `BlendValue(base.specalbedo, layer.specalbedo, m *
                # spec_albedo_blend_alpha, mode)`. No lower layer binds
                # a specular map, so `base` is the tint that would otherwise stand
                # -- 1.0, i.e. F0 = F0(IOR) (`inferred`, same choice as roughness).
                gmix, (fi, ai, bi, ri) = _mix_node(
                    nt, "RGBA", "MIX", -560, -780,
                    label=f"layer{sp_blend['layer']} blend mask")
                gmix.inputs[ai].default_value = (1.0, 1.0, 1.0, 1.0)
                nt.links.new(src, gmix.inputs[bi])
                if sp_gate[0] is not None:
                    nt.links.new(sp_gate[0], gmix.inputs[fi])
                else:
                    gmix.inputs[fi].default_value = float(sp_gate[1])
                src = gmix.outputs[ri]
                mat["le_layer_blend_specular"] = sp_blend["layer"]
            nt.links.new(src, sp_tint_in)
            if sp_level_in is not None:
                sp_level_in.default_value = SPECULAR_IOR_LEVEL_NEUTRAL
            mat["le_specular_wired"] = (
                "Specular Tint = specalbedo / F0(IOR); Specular IOR Level = 0.5")
            mat["le_specular_f0_scale"] = float(scale)
            mat["le_specular_ior"] = float(ior_used)

    # skin -> Principled Subsurface --------------------------------------------
    # ★ RAD has a DEDICATED skin BRDF, not an ubershader permutation of GGX:
    # `k_brdf == kSkinBRDF (4)` dispatches `Skin_BRDF`, whose diffuse is a
    # PRE-INTEGRATED 2-D scatter LUT:
    #
    #     for c in (R, G, B):
    #         n_c   = lerp(bentnormal_c, basenormal, normal_softness)
    #         out_c = scattermap.Sample(dot(n_c, L) * 0.5 + 0.5, thickness)[c]
    #
    # with the three bent normals weighted (0.1747936, 0.3413353, 0.4868717) for
    # R/G/B (`shader-confirmed`) — i.e. RED is bent furthest and scatters widest.
    # That is a per-channel diffusion profile, so the faithful Blender target is
    # Principled's **Subsurface** (a diffusion profile with a per-channel radius),
    # NOT `Translucent` and NOT `Transmission`. ⚠ `Translucent` is the right
    # target for the OTHER, unrelated RAD feature — `backlighting`
    # (a `-dot(N,L)` wrap lobe) — which nothing on this character binds.
    #
    # What is `shader-confirmed` here: that the material is skin (it binds
    # `layerN_thickness_mask`, authored only under the material schema's
    # `enable_skin_` group), and that `thickness` is the LUT's V
    # axis. What is `inferred` and is a LOOK CHOICE: the RADIUS in metres. RAD's
    # LUT is baked and carries no length scale, so `SKIN_SUBSURFACE_RADIUS` is a
    # convention (Blender's own skin default), stamped on the material so it can
    # never be mistaken for a decoded value.
    #
    # ⚠ Also `shader-confirmed` and NOT represented: the ubershader forces the
    # cubemap ambient specular to 0 for `kSkinBRDF`, and the lighting path gives
    # skin its own irradiance intensity and
    # gathers from ALL SG lobes including back-facing ones. Both are engine
    # lighting-path terms with no Principled equivalent.
    sk = channels.get("skin_thickness")
    if sk is not None and skin_subsurface_enabled(opts):
        w_in = _principled_input(bsdf, "Subsurface Weight")
        r_in = _principled_input(bsdf, "Subsurface Radius")
        img = _load_image(pkg_dir, sk.get("file", ""),
                          sk.get("colorspace", "Non-Color"), image_alpha_mode(sk))
        if w_in is not None:
            src = None
            if img is not None:
                node = _tex_node(nt, img, sk.get("colorspace", "Non-Color"),
                                 -1300, -1300, image_alpha_mode(sk),
                                 label=str(sk.get("role_key") or "thickness_mask"))
                sep = nt.nodes.new("ShaderNodeSeparateColor")
                sep.location = (-1050, -1300)
                nt.links.new(node.outputs["Color"], sep.inputs["Color"])
                # thickness is the LUT's V axis: THIN scatters more, so the
                # subsurface weight is `1 - thickness` (`inferred`).
                inv = _math(nt, "SUBTRACT", -880, -1300, 1.0, sep.outputs[0],
                            label="subsurface weight = 1 - thickness")
                scale = _math(nt, "MULTIPLY", -720, -1300, inv.outputs[0],
                              skin_subsurface_weight(opts),
                              label="x skin_subsurface_weight")
                src = scale.outputs[0]
            if src is not None:
                nt.links.new(src, w_in)
            else:
                w_in.default_value = skin_subsurface_weight(opts)
            if r_in is not None:
                r_in.default_value = SKIN_SUBSURFACE_RADIUS
            try:
                bsdf.subsurface_method = "RANDOM_WALK_SKIN"
            except (AttributeError, TypeError):
                pass
            mat["le_skin_role"] = str(sk.get("role_key", ""))
            mat["le_skin_subsurface"] = (
                "kSkinBRDF: Skin_Diffuse pre-integrated scatter LUT "
                "-> Principled Subsurface; "
                "weight = (1 - thickness_mask.R) * skin_subsurface_weight; "
                "RADIUS IS A LOOK CHOICE, not decoded")
            mat["le_skin_subsurface_radius"] = list(SKIN_SUBSURFACE_RADIUS)

    # refraction (mattype 11 eMTRefraction) ----------------------------------
    if mattype == MATTYPE_REFRACTION:
        tw = _principled_input(bsdf, "Transmission Weight", "Transmission")
        if tw is not None:
            tw.default_value = 1.0
        ior_in = _principled_input(bsdf, "IOR")
        if ior_in is not None:
            ior_in.default_value = refractive_index(spec)
        try:
            mat.use_raytrace_refraction = True
        except Exception:
            pass
        if opts.get("enable_scene_raytracing", True):
            # EEVEE Next renders Transmission flat/black without this.
            try:
                bpy.context.scene.eevee.use_raytracing = True
            except Exception:
                pass

    # transmission tint (opacity_map) ----------------------------------------
    # `output.color.rgb += background * material.opacity` (`shader-confirmed`)
    # -- a dual-source ADD of a per-channel tinted background,
    # NOT the Alpha socket and NOT Principled Transmission (that is refraction).
    # Blender equivalent: Add Shader(Principled, Transparent BSDF(Color = tint)).
    if trans_ch is not None and out_node is not None:
        img = _load_image(pkg_dir, trans_ch.get("file", ""),
                          trans_ch.get("colorspace", "Non-Color"),
                          image_alpha_mode(trans_ch))
        tsp = nt.nodes.new("ShaderNodeBsdfTransparent")
        tsp.location = (-250, -1500)
        tsp.label = "opacity_map transmission tint"
        if img:
            node = _tex_node(nt, img, trans_ch.get("colorspace", "Non-Color"),
                             -900, -1500, image_alpha_mode(trans_ch),
                             label="opacity_map (transmission tint)")
            nt.links.new(node.outputs["Color"], tsp.inputs["Color"])
        add = nt.nodes.new("ShaderNodeAddShader")
        add.location = (200, -300)
        surf = out_node.inputs["Surface"]
        src = surf.links[0].from_socket if surf.links else bsdf.outputs[0]
        nt.links.new(src, add.inputs[0])
        nt.links.new(tsp.outputs[0], add.inputs[1])
        nt.links.new(add.outputs[0], surf)
        mat["le_transmission_tint"] = True
        render_mode = "BLEND"

    # --- eBlendAdditive / eBlendLinearDodge ---------------------------------
    # The framebuffer op is `dst = dst + src`. Two consequences the old build got
    # backwards, and they are not a matter of taste:
    #
    #   1. an additive surface NEVER occludes what is behind it -- `dst` survives
    #      unscaled. Blender's `BLENDED` surface method is ALPHA blending, so a
    #      Principled BSDF left at `Alpha = 1.0` renders a fully OPAQUE card.
    #      Adding a `Transparent BSDF` beside it restores `dst`.
    #   2. `src` is ADDED, so a black texel contributes nothing and there is no
    #      such thing as a "dark" additive surface darkening the frame.
    #
    # Before this, `eBlendAdditive` was only TAGGED (`le_blend_lossy`) and then
    # rendered opaque: 22 of the 551 materials in `blender_tool/exports` are
    # additive and **16 of them bind no colour channel at all**, so they drew as
    # solid `bakecolor` chips — Liv's obj001 FX cards are seven such chips at the
    # collar, shoulders, wrists and thighs, sitting in front of her gorget.
    #
    # ⚠ With no colour source the ADDED value is unknown. `base_color_factor`
    # (`bakecolor`) is the baker's flat stand-in for a SURFACE, and adding it
    # would fabricate a glowing white card; the identity of `+` is 0, so the
    # material contributes NOTHING and says so. `opts['additive_unrouted_color']`
    # opts into using the single unrouted bind instead (`inferred`, off).
    if is_additive_blend(spec) and out_node is not None and additive_blend_enabled(opts):
        surf = out_node.inputs["Surface"]
        src = surf.links[0].from_socket if surf.links else bsdf.outputs[0]
        tsp = nt.nodes.new("ShaderNodeBsdfTransparent")
        tsp.location = (200, -900)
        tsp.label = "eBlendAdditive: dst passes through"
        mat["le_additive_blend"] = "dst = dst + src (Add Shader over Transparent)"
        unrouted_role = additive_unrouted_color_role(spec, channels)
        emitted = None
        if unrouted_role is not None and additive_unrouted_color_enabled(opts):
            tex = (spec.get("role_textures") or {}).get(unrouted_role, "")
            rel = f"textures/{tex}.dds"
            dxgi = dds_dxgi_format(pkg_dir / rel)
            cs = "sRGB" if dxgi in DXGI_SRGB else "Non-Color"
            img = _load_image(pkg_dir, rel, cs, "CHANNEL_PACKED")
            if img is not None:
                node = _tex_node(nt, img, cs, -900, -1900, "CHANNEL_PACKED",
                                 label=f"{unrouted_role} (INFERRED additive colour)")
                emi = nt.nodes.new("ShaderNodeEmission")
                emi.location = (-300, -1900)
                emi.label = "additive src (role UNKNOWN -- inferred)"
                nt.links.new(node.outputs["Color"], emi.inputs["Color"])
                emitted = emi.outputs[0]
                mat["le_additive_unrouted_color"] = (
                    f"{unrouted_role} -> {tex}; role is UNKNOWN, this is "
                    f"`inferred` and opt-in (opts['additive_unrouted_color'])")
        if emitted is None and not channels.get("base_color") \
                and not channels.get("emission"):
            # Nothing is known to be added -- contribute the identity, not a guess.
            nt.links.new(tsp.outputs[0], surf)
            mat["le_additive_no_color_source"] = (
                "eBlendAdditive with no routed colour channel: the ADDED value is "
                "unknown, so this contributes 0 rather than a fabricated "
                f"bakecolor card. unrouted_roles={list(spec.get('unrouted_roles') or [])}")
        else:
            add = nt.nodes.new("ShaderNodeAddShader")
            add.location = (400, -700)
            nt.links.new(emitted if emitted is not None else src, add.inputs[0])
            nt.links.new(tsp.outputs[0], add.inputs[1])
            nt.links.new(add.outputs[0], surf)
        render_mode = "BLEND"

    # pass / blend / shadows / culling ---------------------------------------
    got = _set_render_mode(mat, render_mode)
    double_sided = bool(spec.get("double_sided", False))
    try:
        mat.use_backface_culling = not double_sided
    except Exception:
        pass
    try:
        # A cutout whose shadow is still a solid box is the classic wrong result.
        mat.use_backface_culling_shadow = not double_sided
    except Exception:
        pass
    try:
        mat.use_transparent_shadow = render_mode in ("CLIP", "BLEND")
    except Exception:
        pass
    if got == "BLENDED":
        try:
            # Single-sided shells: showing the back faces through the front double-
            # darkens the glass. Opt out with opts["show_transparent_back"].
            mat.show_transparent_back = bool(opts.get("show_transparent_back", False))
        except Exception:
            pass
    # UV SCALE -- `SShaderInputData.uscale/vscale`, which the Echo VR extractor
    # parsed and discarded until now. ~10% of shader-set binds are non-unit
    # ((1,2), (0.25,1), (0.35,1.4) ...), so without this those materials tile at
    # the wrong density. One TexCoord->Mapping feeds every image node that has
    # nothing driving its Vector yet, which is the whole graph in practice and
    # keeps this to a single hook instead of eleven call sites.
    uv_scale = spec.get("uv_scale")
    scroll_u, scroll_v = uv_scroll_axes(spec)
    scroll_rate = opts.get("uv_scroll_rate", UV_SCROLL_RATE_DEFAULT)
    wants_scroll = (scroll_u or scroll_v) and scroll_rate
    if (uv_scale and tuple(uv_scale[:2]) != (1.0, 1.0)) or wants_scroll:
        try:
            targets = [n for n in nt.nodes
                       if n.type == "TEX_IMAGE" and not n.inputs["Vector"].links]
            if targets:
                coord = nt.nodes.new("ShaderNodeTexCoord")
                coord.location = (-2800, 400)
                mapping = nt.nodes.new("ShaderNodeMapping")
                mapping.location = (-2600, 400)
                labels = []
                if uv_scale and tuple(uv_scale[:2]) != (1.0, 1.0):
                    mapping.inputs["Scale"].default_value = (
                        float(uv_scale[0]), float(uv_scale[1]), 1.0)
                    labels.append(f"uv scale {uv_scale[0]:g}x{uv_scale[1]:g}")
                nt.links.new(coord.outputs["UV"], mapping.inputs["Vector"])
                for node in targets:
                    nt.links.new(mapping.outputs["Vector"], node.inputs["Vector"])
                if wants_scroll:
                    driven = _drive_uv_scroll(
                        mapping, (scroll_u, scroll_v), float(scroll_rate))
                    if driven:
                        labels.append("uv scroll %s @ %g/s"
                                      % ("".join(driven), scroll_rate))
                        mat["le_uv_scroll_rate"] = float(scroll_rate)
                        mat["le_uv_scroll_axes"] = "".join(driven)
                        mat["le_uv_scroll_note"] = (
                            "the material says its UVs animate but NOT how fast "
                            "-- no rate, frame count or direction is on disk. "
                            "This speed is an import option, not a recovered "
                            "value.")
                if labels:
                    mapping.label = " + ".join(labels)
        except Exception:
            pass

    # Rim glow: the accent colour through a Fresnel term, masked by the
    # material's rimlighting map. Those maps are otherwise in
    # `unrouted_roles` -- see `build_rim_lighting`.
    if rim_lighting_enabled(opts):
        try:
            build_rim_lighting(nt, bsdf, spec, pkg_dir, mat)
        except Exception:
            pass

    # A blended surface left at Alpha = 1.0 is an opaque card. Runs LAST so the
    # emission chain (tint, layer stack, gates) is final before it is measured.
    if translucent_emissive_alpha_enabled(opts):
        try:
            build_translucent_emissive_alpha(nt, bsdf, spec, channels, mat)
        except Exception:
            pass

    return mat


# ---------------------------------------------------------------------------
# Per-mesh lightmap-PAGE variant
# ---------------------------------------------------------------------------

def lightmap_variant(mat, lm_spec: dict, opts: dict | None = None, ctx: dict | None = None):
    """Return `mat` wired to this MESH's lightmap page, as a cached variant.

    ★ Why a variant and not a call inside `build_material`.
    `wire_lightmap` needs the material, its node tree and its Principled BSDF —
    all of which `build_material` has — but the thing it wires is chosen by
    `CGMeshData.lmsliceindex`, which is a **per-MESH** field.  Materials are
    shared per material-key, and the two axes genuinely cross: in the shipped
    station_front matched pair, `obj001` (page 3) and `obj002` (page 6) use the
    SAME material key `ae4aa9ff9320fcb1__6eac75dad7fc016d`
    (`stream-confirmed`, exports/station_lm manifest).  Wiring that material
    once would put one of those two meshes on the other's bake, which is exactly
    the page-0 failure the auto-splitter exists to prevent, reintroduced one
    layer up.

    So the split is one extra material datablock per **(material, page)** pair
    that is actually used — the same shape of fix as `vertex_color_variant`,
    which already splits per (material, `eDiffuseVertexColor`).  It composes
    with it: the variant is keyed off `mat.name`, which already carries the
    `__vcol` suffix when that applies, so a flagged mesh on page 6 gets
    `<key>__vcol__lm6`.

    Cost, measured on the station_lm package (4 objects / 3 material keys / 3
    distinct pages): 3 extra material datablocks, 15 `bpy.data.images` (5 SG
    lobes x 3 pages, shared between materials by `check_existing=True`) and 15
    cached 1 MiB slice files on disk.  The upper bound is `keys x pages_used`,
    not `keys x 13`: only pages a mesh actually names are ever materialised.

    `mode == "none"` returns `mat` UNCHANGED — no copy, no nodes, no custom
    properties — so the option is a true no-op on the graph.
    """
    from . import lightmap_builder        # lazy: see the note at the imports

    if mat is None or not lm_spec:
        return mat
    opts = opts or {}
    if lightmap_builder.resolved_mode(opts) == lightmap_builder.MODE_NONE:
        return mat
    page = lightmap_builder._page_of(lm_spec.get("slice_index"))
    if page is None:
        # No page => no wiring, and NEVER page 0 docs/LIGHTING.md
        # §5). `wire_lightmap` refuses too; this just avoids the pointless copy.
        return mat
    if mat.get("le_lightmap_page") == page:
        return mat
    name = lightmap_builder.variant_name(mat.name, page)
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing
    var = mat.copy()
    var.name = name
    var["le_lightmap_page"] = page
    nt = var.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        var["le_lightmap_unwired"] = "material has no Principled BSDF"
        return var
    report = lightmap_builder.wire_lightmap(
        var, nt, bsdf, lm_spec, lightmap_builder.wiring_opts(ctx, opts))
    if not report.get("wired"):
        var["le_lightmap_unwired"] = report.get("reason", "")
    var["le_lightmap_basis"] = str(report.get("basis") or "")
    if report.get("basis_reason"):
        var["le_lightmap_basis_reason"] = report["basis_reason"]
    var["le_lightmap_lobes"] = int(report.get("lobes") or 0)
    return var


# ---------------------------------------------------------------------------
# Per-mesh vertex-colour variant
# ---------------------------------------------------------------------------

def vertex_radiance_variant(mat, layer_name: str):
    """`emission = albedo * baked irradiance`, from a colour ATTRIBUTE.

    ⛔ NOT a base-colour multiply. This is the same trap `evr_lighting.
    _wire_radiance` documents for the atlas, and the vertex bake falls into it
    identically: the attribute holds IRRADIANCE -- light that already arrived
    at the surface -- so multiplying it into base colour leaves it as a
    reflectance and the renderer lights it AGAIN with the scene lamps. Quest
    ships four lights for a level it bakes almost entirely, so the second
    lighting pass contributes nearly nothing and the scene renders essentially
    black. That is exactly what the first attempt did.

    Feeding `albedo * irradiance` to EMISSION reproduces the outgoing radiance
    the bake represents, which is what the engine displays. Base Color is left
    on the albedo texture so the surviving lights still shade normally and
    nothing is double-counted.

    ⚠ Anything already driving emission -- `build_rim_lighting`'s accent glow,
    which is the team colour on the arena's geo panels -- is SUMMED rather than
    replaced. Linking straight into the socket is how the lightmap path once
    threw away every rim glow in the level.
    """
    if mat is None or mat.get("le_vertex_radiance"):
        return mat
    name = f"{mat.name}__bake_{layer_name}"
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing
    var = mat.copy()
    var.name = name
    var["le_vertex_radiance"] = True
    nt = var.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return var
    emission = (bsdf.inputs.get("Emission Color")
                or bsdf.inputs.get("Emission"))
    base_in = _principled_input(bsdf, "Base Color")
    if emission is None or base_in is None:
        return var

    col = nt.nodes.new("ShaderNodeVertexColor")
    col.layer_name = layer_name
    col.location = (bsdf.location.x - 900, bsdf.location.y - 420)

    mul = nt.nodes.new("ShaderNodeMix")
    mul.data_type = "RGBA"
    mul.blend_type = "MULTIPLY"
    mul.label = "evr_vertex_bake"
    mul.location = (bsdf.location.x - 560, bsdf.location.y - 400)
    mul.inputs["Factor"].default_value = 1.0
    src = base_in.links[0].from_socket if base_in.links else None
    if src is not None:
        nt.links.new(src, mul.inputs[6])
    else:
        mul.inputs[6].default_value = tuple(base_in.default_value)
    nt.links.new(col.outputs["Color"], mul.inputs[7])

    out = mul.outputs[2]
    if emission.is_linked:
        keep = emission.links[0].from_socket
        add = nt.nodes.new("ShaderNodeMix")
        add.data_type = "RGBA"
        add.blend_type = "ADD"
        add.label = "bake + existing emission"
        add.location = (bsdf.location.x - 320, bsdf.location.y - 380)
        add.inputs["Factor"].default_value = 1.0
        nt.links.new(keep, add.inputs[6])
        nt.links.new(out, add.inputs[7])
        out = add.outputs[2]
    nt.links.new(out, emission)
    strength = bsdf.inputs.get("Emission Strength")
    if strength is not None and not strength.is_linked:
        try:
            if float(strength.default_value) == 0.0:
                strength.default_value = 1.0
        except (TypeError, ValueError):
            pass
    return var


def vertex_color_variant(mat, layer_name: str = "color0"):
    """Return a copy of `mat` with `Color Attribute(layer) -> Mix(MULTIPLY) -> Base Color`.

    Materials are shared per material-key, but `eDiffuseVertexColor` is a per-MESH
    flag, so the tint cannot be baked into the shared material: meshes that do not
    set the flag would be tinted too. The clean split is one extra material datablock
    per (material, vertex-colour) pair, created lazily here and cached by name, and
    mesh_builder swaps the slots over for the meshes that declare the flag.
    """
    if mat is None or mat.get("le_vertex_color_diffuse"):
        return mat
    # The cache is keyed by NAME, so a second layer needs a second name --
    # otherwise a Quest package's `EchoBake` variant would be handed back for a
    # later `color0` request, silently multiplying by the wrong attribute. The
    # default layer keeps the bare suffix so existing names do not move.
    name = (f"{mat.name}__vcol" if layer_name == "color0"
            else f"{mat.name}__vcol_{layer_name}")
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing
    var = mat.copy()
    var.name = name
    var["le_vertex_color_diffuse"] = True
    nt = var.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return var
    base_in = _principled_input(bsdf, "Base Color")
    if base_in is None:
        return var
    col = nt.nodes.new("ShaderNodeVertexColor")
    col.layer_name = layer_name
    col.location = (-900, 600)
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.location = (-350, 450)
    mix.label = "eDiffuseVertexColor"
    mix.inputs["Factor"].default_value = 1.0
    src = base_in.links[0].from_socket if base_in.links else None
    if src is not None:
        nt.links.new(src, mix.inputs[6])
    else:
        mix.inputs[6].default_value = tuple(base_in.default_value)
    nt.links.new(col.outputs["Color"], mix.inputs[7])
    nt.links.new(mix.outputs[2], base_in)
    return var
