"""Material resolution: shaderset/material -> texture roles -> Principled BSDF spec.

Pure stdlib. Produces the `materials` list embedded in a `.lemesh` manifest; the
Blender addon's material_builder wires these onto a Principled BSDF.

Resolution chain:
  CGRenderParams.shadersetidx -> scene shaderset table -> CGShaderSetResource
    -> SShaderInputData rows {inputname(CSymbol64), textureassetid(CSymbol64), ...}
    -> CGTextureResource -> DDS  (DXGI format decides colorspace)
  CGRenderParams.materialidx -> CGSceneData.materials -> SGMaterialData
    -> scalar params: bakecolor, bakeemissivecolor, blendmode, EFlags(eDoubleSided),
       materialprops(k_alpha, layerN_emissive_intensity, uv offsets)

The shaderset->texture join can be read either from precomputed scan TSVs or
directly out of the archives. The role/colorspace/Principled tables below are
durable format knowledge and independent of that source.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from .material_scalars import symbol64


# --- inputname CSymbol64 hash -> role key -----------------------------------
# confidence: "confirmed" (preimage cracked) vs "tentative" (DXGI-format inferred)
INPUTNAME_ROLE: dict[str, tuple[str, str]] = {
    # confirmed
    "2249a2ab88ae66f0": ("layer0_specular_map", "confirmed"),
    "e61f1a40b0f64878": ("layer0_normal_map", "confirmed"),
    "a0790a952a361b16": ("layer0_opacity_map", "confirmed"),
    "6dd500693d77b342": ("layer0_albedo_map", "confirmed"),
    "36edc221250ba1a0": ("layer0_emissive_map", "confirmed"),
    "dcfcc0a30933479e": ("layer1_emissive_map", "confirmed"),
    "b188cecfb9c75902": ("layer2_emissive_map", "confirmed"),
    "63942a40279db62a": ("layer1_opacity_map", "confirmed"),
    "f340cfaa0e533ab5": ("layer1_blend_mask", "confirmed"),
    "18405b9104db1997": ("layer2_blend_mask", "confirmed"),
    "bebfd787fd5cf889": ("layer3_blend_mask", "confirmed"),
    "174d6978fb021e30": ("layer0_flowmap_map", "confirmed"),
    "d4a049adf6a9b30c": ("layer1_flowmap_map", "confirmed"),
    # ⛔ These ten were labelled "tentative (DDS-format inferred)" before 0.2.0 and
    # were INVENTED names — none of them hashed to its own key. Every one below is
    # now the exact recovered preimage: `material_scalars.symbol64(name)` reproduces
    # the key it is filed under (locked by tests/test_transparency.py).
    # Two of the fakes were wrong about MEANING, not just spelling:
    #   * there is NO glass-specific role — "layer1_glass_*" is just layer 1
    #   * "layer1_mask_b" (wired to Roughness) is really layer1_alpha_map = OPACITY
    # See docs/MATERIALS.md.
    "e342db88d8e9d701": ("layer0_composite_normals", "confirmed"),
    "96ac91cb13fe5be7": ("layer1_composite_normals", "confirmed"),
    "33d1823268b0a40c": ("layer0_composite_specular", "confirmed"),
    "e348dd9cd3fdc817": ("layer0_composite_diffuse", "confirmed"),
    "96a697df18ea44f1": ("layer1_composite_diffuse", "confirmed"),
    "5359456ffb9a1dae": ("layer1_composite_specular", "confirmed"),
    "39d68102257d6d24": ("layer0_back_lighting_map", "confirmed"),
    "228838c1c7770d21": ("layer1_composite_components", "confirmed"),
    "d000069cc9204803": ("layer0_composite_components", "confirmed"),
    "8ed4ab4792aaf806": ("layer1_alpha_map", "confirmed"),
    # Recovered in the same pass but only wired in for 0.3.0 (9 corpus rows each).
    # Both verified the same way: symbol64(name) == key.
    "9dba2dc44433be64": ("layer0_alpha_map", "confirmed"),
    "571b8c6b2599c12a": ("layer0_secondary_emissive_map", "confirmed"),
}

# --- layer-aware role parsing ------------------------------------------------
# Every recovered inputname is `layer{N}_{suffix}` — the engine's material sampler
# names are all per-layer. Routing is therefore
# (suffix -> Principled channel) x (layer index), NOT a flat list.
_LAYER_RE = re.compile(r"^layer(\d+)_(.+)$")
MAX_LAYER = 7


def split_role(role_key: str) -> tuple[int, str]:
    """`layer1_emissive_map` -> (1, 'emissive_map'); non-layer roles -> (0, key)."""
    m = _LAYER_RE.match(role_key or "")
    if m:
        return int(m.group(1)), m.group(2)
    return 0, role_key or ""


# --- Principled channel priorities (per layer, first present wins) -----------
# ⚠ Re-derived twice. The fabricated names (see above) had mis-assigned three
# channels; 0.3.0 fixes three more, each against how the engine's shader actually
# consumes the map:
#   * `layerN_opacity_map` is a float3 TRANSMISSION TINT — the shader adds
#     `background * opacity` into the output colour — not alpha. It is out of the
#     alpha path and into `transmission`.
#   * `layerN_composite_specular` is specular data (its alpha is specintensity and
#     specalbedo is `rgb * a`) — it was in ROUGHNESS_ROLES and, before that, in
#     BASE_COLOR_ROLES. Neither is right; it gets its own `specular` channel.
#   * `layerN_alpha_map` is the scalar alpha multiplier of the alpha chain
#     (`alpha = ... * alphamap * k_alpha`) and drives the `alpha` channel.
# `layerN_specular_map` was FIRST in BASE_COLOR_ROLES, which made a specular map
# the Base Color of any material carrying one. Measured over 51 fixture packages /
# 100 unique materials: 8 materials carry `layer0_specular_map` and *none* of them
# carries any diffuse/albedo, so the ordering never actually resolved a conflict —
# the entry was simply routing specular data into Base Color. It now routes to
# `specular` only.
CHANNEL_ROLE_SUFFIXES: dict[str, tuple[str, ...]] = {
    "base_color":         ("composite_diffuse", "albedo_map"),
    "normal":             ("composite_normals", "normal_map"),
    "roughness":          ("composite_components",),
    "specular":           ("composite_specular", "specular_map"),
    "alpha":              ("alpha_map",),
    "transmission":       ("opacity_map",),
    "emission":           ("emissive_map",),
    "secondary_emission": ("secondary_emissive_map",),
    "translucency":       ("back_lighting_map",),
    "blend_mask":         ("blend_mask",),
    "flowmap":            ("flowmap_map",),
}

# Channels with no faithful Principled target — carried for audit only.
# `blend_mask` is NOT a Principled input: it is the per-layer compositing weight,
# consumed by `layer_blend_for()` below and applied by material_builder to the
# other channels of the same layer.
AUDIT_ONLY_CHANNELS = frozenset({"translucency", "blend_mask", "flowmap",
                                 "secondary_emission"})

# role_key -> confidence (derived from INPUTNAME_ROLE)
INPUTNAME_ROLE_CONF = {v[0]: v[1] for v in INPUTNAME_ROLE.values()}
KNOWN_ROLES = frozenset(INPUTNAME_ROLE_CONF)


def _flat_roles(channel: str) -> list[str]:
    """Legacy flat role list for `channel`: suffix-major, then layer-ascending."""
    out: list[str] = []
    for suffix in CHANNEL_ROLE_SUFFIXES[channel]:
        for layer in range(MAX_LAYER + 1):
            role = f"layer{layer}_{suffix}"
            if role in KNOWN_ROLES:
                out.append(role)
    return out


BASE_COLOR_ROLES = _flat_roles("base_color")
NORMAL_ROLES = _flat_roles("normal")
ROUGHNESS_ROLES = _flat_roles("roughness")
SPECULAR_ROLES = _flat_roles("specular")
ALPHA_ROLES = _flat_roles("alpha")
TRANSMISSION_ROLES = _flat_roles("transmission")
EMISSION_ROLES = _flat_roles("emission")
SECONDARY_EMISSION_ROLES = _flat_roles("secondary_emission")
# Translucency/back-lighting, NOT emission — kept out of EMISSION_ROLES on purpose.
TRANSLUCENCY_ROLES = _flat_roles("translucency")
BLEND_MASK_ROLES = _flat_roles("blend_mask")
FLOWMAP_ROLES = _flat_roles("flowmap")
# ⚠ DEPRECATED alias. The engine's "opacity" IS the transmission tint, so this
# list is now exactly TRANSMISSION_ROLES — `layerN_alpha_map` has moved to
# ALPHA_ROLES. `channels["opacity"]` is still emitted as a mirror of
# `channels["transmission"]` for old consumers; new code must read
# `channels["transmission"]` and must NOT wire it to Blender's Alpha socket.
OPACITY_ROLES = TRANSMISSION_ROLES

# ---------------------------------------------------------------------------
# Layer compositing — how `layerN_blend_mask` combines layers
# ---------------------------------------------------------------------------
# The engine's layer composite, as its shader performs it:
#
#   * layer 0 is the BASE. The accumulator starts as `layers[0]`, so layer 0 is
#     never itself blended and carries no blend record.
#   * layers 1..N-1 are blended on in ASCENDING order, each onto the running
#     accumulation of the layers below it.
#   * for layer i:
#       fade    = max(blend_fade[i]    * fade_scale_offset_map[i].x, 0.01)
#       scale   = blend_mask_scale[i]  * fade_scale_offset_map[i].y
#       offset  = blend_mask_offset[i] + fade_scale_offset_map[i].z
#       _scale  = scale  * scale_regions_map[i].x
#       _offset = offset * (1.0 - offset_regions_map[i].x)
#       _mask   = saturate(blend_mask[i].R * _scale + _offset)
#       blend   = saturate((vertblend - height) / fade) * _mask
#       result  = BlendValue(result, layers[i], blend, blend_mode[i])
#     (the normal-vector path repeats the identical arithmetic)
#   * the composite is per-property, and each property scales `blend` by its own
#     alpha: diffusealbedo x diff_albedo_blend_alpha, specalbedo x
#     spec_albedo_blend_alpha, lighting x lighting_blend_alpha, sqrtroughness x
#     roughness_blend_alpha, alpha x transparency_blend_alpha, normal x
#     normal_blend_alpha. Opacity and flowmap take the bare mask.
#   * EMISSIVE rides inside `lighting` (`lighting = layer * (spec + emissive) *
#     k_emissive_scale`), so an upper layer's emissive map is gated by that
#     layer's blend amount.
#
# The neutral-default self-check: every map that participates defaults to the
# value that makes its own term vanish — the blend mask defaults to white (1), the
# fade/scale/offset map to (1, 1, 0), the scale-regions map to white (1) and the
# offset-regions map to black (0). So with authored defaults the whole thing
# collapses to
#
#     blend_amount = saturate(vertblend / fade) * saturate(mask.R * scale + offset)
#
# ⚠ Both region maps are weighted-mask samplers: texture arrays whose slices carry
# an ANIMATED weight and are additively flattened before rendering. And
# `blend_mask_offset` is itself an animatable parameter with a soft range of
# [-1, 1]. A shipped `layerN_blend_mask_offset = -1.0` therefore means "this layer
# is parked at its animated OFF extreme" — a runtime state we cannot reproduce,
# not a bug. The decode reports it (`suppressed_at_rest`) rather than
# editorialising it.

# The blend mask is sampled through its RED channel.
BLEND_MASK_COMPONENT = "R"
# The per-layer blend operator. NOT the same enum as `BLENDMODE_NAMES` below,
# which is the material-level `EBlendMode` (18 values, RT blend equation).
LAYER_BLEND_MODE_NAMES = {
    0: "eBlendNone", 1: "eBlendAdditive", 2: "eBlendSubtractive",
    3: "eBlendMultiply", 4: "eBlendDarken", 5: "eBlendLighten",
    6: "eBlendTransparent", 7: "eBlendLinearDodge", 8: "eBlendLinearBurn",
    9: "eBlendOverlay", 10: "eBlendDetailOverride",
}
LAYER_BLEND_LERP_MODES = frozenset({6, 10})     # (1-m)*base + m*layer
LAYER_BLEND_ADD_MODES = frozenset({1, 7})       # base + layer*m

# Authored defaults of the engine's uber-material.
DEFAULT_BLEND_MASK_SCALE = 1.0
DEFAULT_BLEND_MASK_OFFSET = 0.0
DEFAULT_BLEND_FADE = 1.0
DEFAULT_LAYER_BLEND_MODE = 6          # eBlendTransparent, i.e. a lerp
DEFAULT_BLEND_ALPHA = 1.0             # every `*_blend_alpha`
MIN_BLEND_FADE = 0.01                 # the shader clamps fade with max(..., 0.01)
# The `blend_mask` sampler defaults to a white texture -> mask.R = 1.0.
DEFAULT_BLEND_MASK_VALUE = 1.0
# Height blending is authored off per layer AND the height maps default to values
# that make `height` 0 either way — so the height term is 0 for every material in
# the corpus (no material binds a blend-height or detail-height map).
DEFAULT_BLEND_HEIGHT = 0.0

# The authored `fresnel` default is 0.01. It is the layer's `specintensity`, and
# therefore the scalar that turns a `layerN_specular_map` texel into F0. Corpus
# check over all 100 unique materials of a 51-package fixture set: NO shipped
# material overrides `fresnel`, `specular_tint_color`, `specular_gloss` or
# `enable_specular`, so every `specular_map` material in the corpus runs on this
# authored default.
SPEC_MAP_FRESNEL_DEFAULT = 0.01
# The authored `specular_gloss` default is 1.0, at which the engine's
# `lerp(specalbedo * albedo, specalbedo, gloss)` is a no-op.
SPEC_MAP_GLOSS_DEFAULT = 1.0

# Which `layerN_*_blend_alpha` scales the mask for each of our channel names.
CHANNEL_BLEND_ALPHA_PARAM = {
    "base_color":         "diff_albedo_blend_alpha",
    "specular":           "spec_albedo_blend_alpha",
    "roughness":          "roughness_blend_alpha",
    "emission":           "lighting_blend_alpha",
    "secondary_emission": "lighting_blend_alpha",   # ditto: emissive rides in lighting
    "translucency":       "backlighting_blend_alpha",
    "alpha":              "transparency_blend_alpha",
    "normal":             "normal_blend_alpha",
    "transmission":       None,                     # bare mask
    "flowmap":            None,                     # bare mask
    "blend_mask":         None,                     # the mask itself
}

# `vertblend` for layer i is component (i-1) of the shader's `blend` input, which
# is the SECOND vertex colour stream. Our exporter names the second eColor set
# `color1` (`le_mesh/vertex_format.py::attribute_key`), so layer i reads component
# (i-1) of `color1`. The "colour-set order == register order" linkage is inferred.
VERTEX_BLEND_ATTRIBUTE = "color1"
VERTEX_BLEND_COMPONENTS = ("R", "G", "B")


def _saturate(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else float(v))


def blend_amount_bounds(mask_scale: float, mask_offset: float,
                        has_mask_texture: bool) -> tuple[float, float]:
    """(min, max) of `saturate(mask.R * scale + offset)` over `mask.R in [0,1]`.

    `blend_mask_scale` is authored non-negative, so the expression is monotone
    non-decreasing in the mask and the bounds are just its endpoints. Without a
    bound mask texture the sampler returns white == 1.0, so the two bounds
    collapse onto the single constant.
    """
    hi = _saturate(mask_scale * 1.0 + mask_offset)
    lo = hi if not has_mask_texture else _saturate(mask_scale * 0.0 + mask_offset)
    return lo, hi


# --- DXGI format -> colorspace ----------------------------------------------
# Standard DXGI enum values. An `_SRGB` format is decoded to linear BY THE
# SAMPLER, so Blender must apply the same decode -> the format is authoritative.
# (Corpus check: no normal/components/blend-mask texture in the 51 fixture
# packages is an _SRGB format, and every diffuse / specular / emissive / opacity
# texture that IS _SRGB is genuinely colour data.)
SRGB_DXGI = frozenset({
    29,   # R8G8B8A8_UNORM_SRGB
    72,   # BC1_UNORM_SRGB
    75,   # BC2_UNORM_SRGB
    78,   # BC3_UNORM_SRGB
    91,   # B8G8R8A8_UNORM_SRGB
    93,   # B8G8R8X8_UNORM_SRGB
    99,   # BC7_UNORM_SRGB
})
# BC5 (82..84) is two-channel XY normal -> reconstruct Z.
BC5_DXGI = frozenset({82, 83, 84})
# Formats with a real, independently-addressable alpha block.
ALPHA_CAPABLE_DXGI = frozenset({
    10, 11, 12, 13,            # R16G16B16A16_*
    24, 25,                    # R10G10B10A2_*
    28, 29,                    # R8G8B8A8_UNORM / _SRGB
    73, 74, 75,                # BC2
    76, 77, 78,                # BC3
    87, 91,                    # B8G8R8A8
    97, 98, 99,                # BC7
    70, 71, 72,                # BC1 — 1-bit punch-through only (see PUNCHTHROUGH)
})
PUNCHTHROUGH_ALPHA_DXGI = frozenset({70, 71, 72})   # BC1: 1 bit of alpha
# Single-channel formats: a scalar map's value is unambiguously in R.
SINGLE_CHANNEL_DXGI = frozenset({54, 55, 56, 61, 62, 63, 79, 80, 81})

# --- Blender image alpha_mode hints -----------------------------------------
ALPHA_MODE_CHANNEL_PACKED = "CHANNEL_PACKED"   # alpha is data we read separately
ALPHA_MODE_STRAIGHT = "STRAIGHT"               # genuine unassociated alpha
ALPHA_MODE_NONE = "NONE"                       # alpha must be ignored entirely
# Suffixes whose alpha channel we read as an independent signal. Every one is a
# term of the engine's alpha chain, except `composite_specular` whose alpha is
# specintensity. For all of them Blender must NOT un-premultiply the RGB, i.e.
# alpha_mode = CHANNEL_PACKED.
CHANNEL_PACKED_SUFFIXES = frozenset({
    "composite_diffuse", "albedo_map", "emissive_map", "secondary_emissive_map",
    "alpha_map", "composite_specular",
})
# ⛔ ALPHA_MODE_STRAIGHT is never emitted automatically. Choosing it would need the
# premultiplied-alpha / output-alpha *shader permutation* bits, which are NOT on
# disk. The constant exists so it can be set by hand.


# --- material type / blend mode enums ---------------------------------------
MATTYPE_NAMES = {
    0: "eMTDeferredOpaque", 1: "eMTForwardOpaque", 2: "eMTForwardTransparent",
    3: "eMTLowResTransparent", 4: "eMTSolidTransparent", 5: "eMTFullScreenEffect",
    6: "eMTParticles", 7: "eMT2D", 8: "eMTDebug", 9: "eMTAlphaTested",
    10: "eMTSkirt", 11: "eMTRefraction", 12: "eMTHair", 13: "eMTSkydome",
    14: "eMTOutline", 15: "eMTOutlineDepthFail", 16: "eMTTransparentPostAA",
}
BLENDMODE_NAMES = {
    0: "eBlendOpaque", 1: "eBlendAdditive", 2: "eBlendSubtractive",
    3: "eBlendMultiply", 4: "eBlendDarken", 5: "eBlendLighten", 6: "eBlendScreen",
    7: "eBlendTransparent", 8: "eBlendLinearDodge", 9: "eBlendLinearBurn",
    10: "eBlendSkirt", 11: "eBlendPremultipledAlpha", 12: "eBlendTranslucent",
    13: "eBlendMin", 14: "eBlendMax", 15: "eBlendAlphaToCoverage",
    16: "eBlendNoColorWrites", 17: "eBlendReverseSubtractive",
}

# How the two enums map onto a render mode — see docs/MATERIALS.md.
ALPHA_TESTED_MATTYPES = frozenset({9})                     # eMTAlphaTested
TRANSPARENT_MATTYPES = frozenset({2, 3, 4, 11, 16})        # Forward/LowRes/Solid/Refraction/PostAA
OPAQUE_BLENDMODES = frozenset({0, 10, 16})                 # Opaque / Skirt / NoColorWrites
# EEVEE has no additive blend — approximating it is lossy and must be flagged.
ADDITIVE_BLENDMODES = frozenset({1, 8})                    # eBlendAdditive / eBlendLinearDodge
COVERAGE_BLENDMODES = frozenset({15})                      # eBlendAlphaToCoverage

RENDER_MODE_OPAQUE = "OPAQUE"
RENDER_MODE_CLIP = "CLIP"
RENDER_MODE_BLEND = "BLEND"


def render_mode_for(mattype: int, blend_mode: int) -> tuple[str, bool]:
    """(render_mode, alpha_blend_lossy) from the two on-disk u16 fields.

    `mattype` picks the pass and `blend_mode` picks the equation.
    Shader-permutation bits (clip vs dither, alpha-to-coverage, premultiplied) are
    NOT on disk and are deliberately not guessed here — CLIP means "the engine cuts
    out", not "use Blender's legacy CLIP blend_method" (which is a dead alias on
    4.2+).
    """
    lossy = blend_mode in ADDITIVE_BLENDMODES
    if lossy:
        return RENDER_MODE_BLEND, True
    if mattype in ALPHA_TESTED_MATTYPES or blend_mode in COVERAGE_BLENDMODES:
        return RENDER_MODE_CLIP, False
    if mattype in TRANSPARENT_MATTYPES:
        return RENDER_MODE_BLEND, False
    if blend_mode not in OPAQUE_BLENDMODES:
        return RENDER_MODE_BLEND, False
    return RENDER_MODE_OPAQUE, False


def colorspace_for(dxgi: int | None, role_key: str) -> str:
    """Blender Image colorspace for a texture's RGB: 'sRGB' or 'Non-Color'.

    The DXGI format is authoritative when known: an `_SRGB` view is linearised by
    the sampler, anything else is not. Only when the format is unknown do we fall
    back to what the role means.

    ⚠ This is about RGB only. A texture's ALPHA channel is always linear, even in
    an `_SRGB` format — see `alpha_is_linear()`.
    """
    _layer, suffix = split_role(role_key)
    # Narrow structural override: a tangent-space normal is never colour. If a
    # normal map ever turned up in an _SRGB format that would be an authoring bug
    # and sRGB-decoding it is the single most visually catastrophic mistake here.
    # (Zero effect on the shipped corpus — every observed normal map is BC5_UNORM.)
    if suffix in CHANNEL_ROLE_SUFFIXES["normal"]:
        return "Non-Color"
    if dxgi is not None:
        return "sRGB" if dxgi in SRGB_DXGI else "Non-Color"
    colour_suffixes = (CHANNEL_ROLE_SUFFIXES["base_color"]
                       + CHANNEL_ROLE_SUFFIXES["emission"]
                       + CHANNEL_ROLE_SUFFIXES["secondary_emission"]
                       + CHANNEL_ROLE_SUFFIXES["transmission"])
    return "sRGB" if suffix in colour_suffixes else "Non-Color"


def alpha_is_linear(_dxgi: int | None = None, _role_key: str = "") -> bool:
    """Always True. A DXGI `_SRGB` format sRGB-decodes RGB only; alpha stays linear.

    Kept as a function (rather than a bare constant) so the fact is discoverable
    at the call site: an sRGB base-colour texture still has a linear alpha channel
    and no transform may be applied to it.
    """
    return True


def alpha_mode_for(role_key: str) -> str:
    """Blender `image.alpha_mode` hint for the texture bound to `role_key`."""
    _layer, suffix = split_role(role_key)
    if suffix in CHANNEL_PACKED_SUFFIXES:
        return ALPHA_MODE_CHANNEL_PACKED
    return ALPHA_MODE_NONE


def _first_present(roles: list[str], role_textures: dict[str, str]) -> str | None:
    for r in roles:
        if role_textures.get(r):
            return r
    return None


def _channel(role_key: str, tex_hash: str, dxgi_by_tex: dict[str, int],
             *, layer: int | None = None, is_normal: bool = False) -> dict:
    dxgi = dxgi_by_tex.get(tex_hash)
    conf = INPUTNAME_ROLE_CONF.get(role_key, "tentative")
    role_layer, suffix = split_role(role_key)
    if dxgi is None:
        reconstruct_z = is_normal or suffix in CHANNEL_ROLE_SUFFIXES["normal"]
    else:
        reconstruct_z = dxgi in BC5_DXGI
    ch = {
        "texture": tex_hash,
        "role_key": role_key,
        "dxgi": dxgi,
        "colorspace": colorspace_for(dxgi, role_key),
        "reconstruct_z": bool(reconstruct_z),
        "confidence": conf,
        "layer": int(role_layer if layer is None else layer),
        "alpha_mode": alpha_mode_for(role_key),
        "alpha_is_linear": True,
    }
    # --- per-suffix packing facts, as the engine's shader unpacks them --------
    if suffix == "composite_components":
        # sqrtroughness[0] = .x ; ambientocclusion = .y ; brdfblends.y = .z ;
        # sqrtroughness[1] = .w   and  roughness = sqrtroughness^2
        ch["roughness_channel"] = "R"
        ch["roughness_is_sqrt"] = True
        ch["ao_channel"] = "G"
    elif suffix == "composite_specular":
        # specintensity = .w ; specalbedo = .xyz * .w
        ch["spec_intensity_channel"] = "A"
        ch["spec_albedo_channel"] = "RGB"
        ch["spec_albedo_scaled_by"] = "A"
        ch["packing"] = "specalbedo = rgb * a ; specintensity = a"
    elif suffix == "specular_map":
        # Non-composite sibling. It reaches the SAME `specalbedo[0]` slot as
        # `composite_specular` -- i.e. it is also F0 -- but it is scaled by a
        # material SCALAR, not by its own alpha:
        #   specalbedo[0]    = enable_specular * specular_tint_color *
        #                      specular_map * fresnel
        #   specintensity[0] = fresnel * enable_specular
        # `fresnel` is authored 0.010, and `specular_gloss` (authored 1.0) lerps
        # the `specalbedo *= albedo` term in at 0, i.e. not at all.
        # `enable_specular` is a per-layer shader PERMUTATION bit and permutation
        # bits are not on disk, so it is assumed 1 wherever the map is bound.
        ch["spec_albedo_channel"] = "RGB"
        ch["spec_albedo_scaled_by"] = "fresnel"
        ch["spec_intensity_source"] = "fresnel"
        ch["spec_fresnel_default"] = SPEC_MAP_FRESNEL_DEFAULT
        ch["packing"] = ("specalbedo = rgb * layerN_fresnel * "
                         "layerN_specular_tint_color ; specintensity = layerN_fresnel")
    elif suffix == "composite_diffuse":
        # diffusealbedo = .xyz ; alpha = .w * vertexcolor.w
        ch["alpha_channel"] = "A" if dxgi in ALPHA_CAPABLE_DXGI else None
    elif suffix == "alpha_map":
        # scalar multiplier of the alpha chain; on a single-channel format the
        # value can only be in R, otherwise the swizzle is not knowable from disk.
        ch["scalar_channel"] = "R" if dxgi in SINGLE_CHANNEL_DXGI else None
    elif suffix == "opacity_map":
        # float3 transmission tint: color.rgb += background * opacity
        ch["is_transmission_tint"] = True
    if role_key in AUDIT_ONLY_ROLE_KEYS:
        ch["audit_only"] = True
    return ch


AUDIT_ONLY_ROLE_KEYS = frozenset(
    r for c in AUDIT_ONLY_CHANNELS for r in _flat_roles(c))


def _unknown_slot(role_key: str) -> int:
    try:
        return int(role_key[len("unknown_s"):])
    except (TypeError, ValueError):
        return 1 << 30


def classify_roles_layered(role_textures: dict[str, str],
                           dxgi_by_tex: dict[str, int]) -> dict:
    """Full layer-aware routing of a shaderset's {role_key -> tex_hash}.

    Returns::

        {"channels": {...},        # merged view, lowest layer index wins
         "layers":   [{"index": N, "channels": {...}}, ...],
         "primary_layer": int,
         "unrouted": [role_key, ...]}

    `channels` is the back-compat merged view: for each channel name the lowest
    layer that provides it wins, which reproduces the old first-present-wins
    behaviour (the old flat lists were layer-ascending) *without* dropping a
    channel that only exists on a higher layer. Before this change a material
    carrying `layer0_emissive_map` and `layer1_emissive_map` routed only layer 0,
    and `layer0_composite_specular` was not routed at all.
    """
    dxgi_by_tex = dxgi_by_tex or {}
    by_layer: dict[int, dict[str, dict]] = {}
    routed: set[str] = set()

    # group the incoming roles by layer index
    layer_roles: dict[int, dict[str, str]] = {}
    for role_key, tex in role_textures.items():
        if not tex or role_key.startswith("unknown_s"):
            continue
        layer, suffix = split_role(role_key)
        layer_roles.setdefault(layer, {})[suffix] = role_key

    for layer in sorted(layer_roles):
        present = layer_roles[layer]
        chans: dict[str, dict] = {}
        for channel, suffixes in CHANNEL_ROLE_SUFFIXES.items():
            for suffix in suffixes:
                role_key = present.get(suffix)
                if role_key:
                    chans[channel] = _channel(role_key, role_textures[role_key],
                                              dxgi_by_tex, layer=layer)
                    routed.add(role_key)
                    break
        if chans:
            by_layer[layer] = chans

    # merged view: lowest layer index wins per channel name
    channels: dict[str, dict] = {}
    for layer in sorted(by_layer):
        for name, ch in by_layer[layer].items():
            channels.setdefault(name, ch)

    # ⚠ deprecated mirror: the engine's "opacity" is the transmission tint.
    if "transmission" in channels and "opacity" not in channels:
        mirror = dict(channels["transmission"])
        mirror["deprecated"] = True
        mirror["deprecated_note"] = (
            "transmission tint (color.rgb += background * opacity); "
            "do NOT wire to Blender's Alpha socket — read channels['transmission']")
        channels["opacity"] = mirror

    # DXGI fallback for any still-unassigned unknown_s{slot} textures.
    assigned = {c["texture"] for c in channels.values()}
    for role_key in sorted((r for r in role_textures if r.startswith("unknown_s")),
                           key=_unknown_slot):
        tex = role_textures[role_key]
        if not tex or tex in assigned:
            continue
        dxgi = dxgi_by_tex.get(tex, 0)
        if dxgi in BC5_DXGI and "normal" not in channels:
            ch = _channel(role_key, tex, dxgi_by_tex, layer=0, is_normal=True)
            ch["inferred_from"] = "dxgi"
            channels["normal"] = ch
            by_layer.setdefault(0, {}).setdefault("normal", ch)
        elif "base_color" not in channels:
            ch = _channel(role_key, tex, dxgi_by_tex, layer=0)
            ch["inferred_from"] = "dxgi"
            channels["base_color"] = ch
            by_layer.setdefault(0, {}).setdefault("base_color", ch)
        else:
            continue
        routed.add(role_key)
        assigned.add(tex)

    layers = [{"index": layer, "channels": by_layer[layer]}
              for layer in sorted(by_layer)]
    primary = layers[0]["index"] if layers else 0
    unrouted = sorted(r for r, t in role_textures.items() if t and r not in routed)
    return {"channels": channels, "layers": layers,
            "primary_layer": primary, "unrouted": unrouted}


def classify_roles(role_textures: dict[str, str], dxgi_by_tex: dict[str, int]) -> dict:
    """Map a shaderset's {role_key -> tex_hash} to Principled channels (merged view).

    Back-compat wrapper around `classify_roles_layered`; use that when you need
    the per-layer breakdown. Includes the DXGI fallback for unknown
    `unknown_s{slot}` roles: BC5 -> normal, otherwise -> base color.
    """
    return classify_roles_layered(role_textures, dxgi_by_tex)["channels"]


# --- named material scalars this module reads -------------------------------
HASH_K_ALPHA_THRESHOLD = symbol64("k_alpha_threshold")
HASH_K_EMISSIVE_SCALE = symbol64("k_emissive_scale")
HASH_K_REFRACTIVE_INDEX = symbol64("k_refractive_index")
HASH_LAYER_EMISSIVE_INTENSITY = {
    L: symbol64(f"layer{L}_emissive_intensity") for L in range(MAX_LAYER + 1)}
# Every per-layer blend parameter this module reads. The names are members of the
# engine's per-layer uber-material parameter block, and `symbol64(name) == the
# shipped hash` is asserted by
# `tests/test_layer_compositing.py::test_blend_param_hashes_are_real_preimages`
# — nothing here may be invented.
LAYER_BLEND_PARAMS = ("blend_mask_offset", "blend_mask_scale", "blend_fade",
                      "diff_albedo_blend_alpha", "spec_albedo_blend_alpha",
                      "roughness_blend_alpha", "lighting_blend_alpha",
                      "subsurface_blend_alpha", "backlighting_blend_alpha",
                      "brdf_blend_alpha", "transparency_blend_alpha",
                      "normal_blend_alpha")
HASH_LAYER_BLEND_PARAM = {
    (L, p): symbol64(f"layer{L}_{p}")
    for L in range(MAX_LAYER + 1) for p in LAYER_BLEND_PARAMS}
# Authored defaults — used only where the parameter is ABSENT, which is itself an
# inference ("a material that does not override a parameter omits it").
DEFAULT_ALPHA_THRESHOLD = 0.5
DEFAULT_EMISSIVE_SCALE = 1.0


def _named_value(scalars: dict, name: str, name_hash: int) -> float | None:
    """Look a named material scalar up through every shape the decoder may emit."""
    resolved = scalars.get("named_scalars_resolved") or {}
    if name in resolved and resolved[name] is not None:
        return float(resolved[name])
    named = scalars.get("named_scalars") or {}
    hexkey = f"{name_hash:016x}"
    if hexkey in named:
        return float(named[hexkey])
    if name_hash in named:                       # int-keyed variant
        return float(named[name_hash])
    return None


def _layer_scalar(scalars: dict, layer: int | None, field: str):
    """Read `field` from the material scalars' per-layer `layers[]`, if present."""
    if layer is None:
        return None
    for entry in scalars.get("layers") or []:
        try:
            if int(entry.get("index", -1)) == int(layer):
                value = entry.get(field)
                return value
        except (TypeError, ValueError):
            continue
    return None


def emissive_intensity_for_layer(scalars: dict, layer: int | None) -> float:
    """`layer{N}_emissive_intensity` for the layer whose emissive map was routed.

    The single biggest emissive error was reading layer 0 unconditionally while
    the emissive *map* sat on another layer — 25.0 vs 2.0 on the bridge material
    `0613ef69c99cbbc6`, i.e. 12.5x too dim.
    """
    value = _layer_scalar(scalars, layer, "emissive_intensity")
    if value is not None:
        return float(value)
    if layer is not None and layer in HASH_LAYER_EMISSIVE_INTENSITY:
        value = _named_value(scalars, f"layer{layer}_emissive_intensity",
                             HASH_LAYER_EMISSIVE_INTENSITY[layer])
        if value is not None:
            return float(value)
    return float(scalars.get("emissive_intensity", 1.0))


def _layer_blend_param(scalars: dict, layer: int, param: str, default: float
                       ) -> tuple[float, bool]:
    """(value, came_from_material) for `layer{N}_{param}`.

    Falls back to the engine's authored default.
    """
    value = _layer_scalar(scalars, layer, param)
    if value is not None:
        return float(value), True
    name_hash = HASH_LAYER_BLEND_PARAM.get((layer, param))
    if name_hash is not None:
        value = _named_value(scalars, f"layer{layer}_{param}", name_hash)
        if value is not None:
            return float(value), True
    return float(default), False


def layer_blend_for(index: int, layer_channels: dict, scalars: dict | None = None) -> dict | None:
    """The composite record for one entry of `layers[]`, or None for layer 0.

    Layer 0 is the base of the engine's layer composite (the accumulator starts as
    `layers[0]`) and is never blended, so it carries no record. For layer i >= 1
    the engine computes, with authored-default region/fade maps::

        mask_amount = saturate(mask.R * mask_scale + mask_offset)
        blend       = saturate((vertex_blend - height) / fade) * mask_amount
        composited  = BlendValue(lower_layers, layer_i, blend * <channel>_blend_alpha,
                                 blend_mode)

    The only inferred parts are the vertex-blend colour-set linkage
    (`vertex_blend_*`) and the "absent parameter means authored default" rule.
    """
    if index is None or int(index) <= 0:
        return None
    index = int(index)
    scalars = scalars or {}
    mask = (layer_channels or {}).get("blend_mask")

    scale, scale_authored = _layer_blend_param(scalars, index, "blend_mask_scale",
                                               DEFAULT_BLEND_MASK_SCALE)
    offset, offset_authored = _layer_blend_param(scalars, index, "blend_mask_offset",
                                                 DEFAULT_BLEND_MASK_OFFSET)
    fade, fade_authored = _layer_blend_param(scalars, index, "blend_fade",
                                             DEFAULT_BLEND_FADE)
    fade = max(fade, MIN_BLEND_FADE)          # the shader's own clamp

    channel_alpha: dict[str, float] = {}
    for channel, param in CHANNEL_BLEND_ALPHA_PARAM.items():
        if param is None:
            continue
        value, _ = _layer_blend_param(scalars, index, param, DEFAULT_BLEND_ALPHA)
        if value != DEFAULT_BLEND_ALPHA:
            channel_alpha[channel] = value

    lo, hi = blend_amount_bounds(scale, offset, mask is not None)
    gated = sorted(c for c in (layer_channels or {}) if c != "blend_mask")

    # `layerN_blend_mode` is an INT material prop; `materialprops` is decoded as
    # f32 words, so an integer-valued prop cannot be read back through that path.
    # No shipped material in the corpus carries one, so the authored default is
    # used and the fact that it was NOT read is recorded rather than hidden.
    return {
        "layer": index,
        "mask": (dict(mask) if mask else None),
        "mask_component": BLEND_MASK_COMPONENT,
        "mask_default": DEFAULT_BLEND_MASK_VALUE,
        "mask_scale": scale,
        "mask_offset": offset,
        "blend_fade": fade,
        "blend_mode": DEFAULT_LAYER_BLEND_MODE,
        "blend_mode_name": LAYER_BLEND_MODE_NAMES[DEFAULT_LAYER_BLEND_MODE],
        "blend_mode_from_material": False,
        "height": DEFAULT_BLEND_HEIGHT,
        "channel_alpha": channel_alpha,
        "gated_channels": gated,
        "amount_min": lo,
        "amount_max": hi,
        "amount_constant": (lo if lo == hi else None),
        "suppressed_at_rest": hi <= 0.0,
        "from_material": sorted(
            n for n, ok in (("blend_mask_scale", scale_authored),
                            ("blend_mask_offset", offset_authored),
                            ("blend_fade", fade_authored)) if ok),
        "vertex_blend_attribute": VERTEX_BLEND_ATTRIBUTE,
        "vertex_blend_component": (VERTEX_BLEND_COMPONENTS[index - 1]
                                   if index - 1 < len(VERTEX_BLEND_COMPONENTS) else None),
        "vertex_blend_applied": False,
    }


def build_material_spec(key: str, *, shaderset_hash: str = "", material_hash: str = "",
                        role_textures: dict[str, str] | None = None,
                        dxgi_by_tex: dict[str, int] | None = None,
                        scalars: dict | None = None,
                        texture_files: dict[str, str] | None = None) -> dict:
    """Produce one `materials[]` entry for the manifest.

    `scalars` may carry: base_color_factor[4], emissive_color[3],
    emissive_intensity, alpha, blend_mode(int), double_sided(bool) plus optional
    audit extras (mattype, flags, flag_names, materialfx, is_emissive,
    named_scalars) — see le_mesh.material_scalars.decode_material_scalars.
    It may additionally carry the extended keys (`layers`, `emissive_scale`,
    `alpha_threshold`, `refractive_index`, `named_scalars_resolved`,
    `mattype_name`, `blend_mode_name`); every one of those is optional and is
    reconstructed from `named_scalars` when absent, so this works against both
    the old and the new scalar dict.
    `texture_files` maps tex_hash -> package-relative file path (e.g. textures/<hash>.dds).
    """
    role_textures = role_textures or {}
    dxgi_by_tex = dxgi_by_tex or {}
    scalars = scalars or {}
    texture_files = texture_files or {}

    layered = classify_roles_layered(role_textures, dxgi_by_tex)
    channels = layered["channels"]
    layers = layered["layers"]

    def _attach_file(ch: dict) -> None:
        ch["file"] = texture_files.get(ch["texture"], "")

    for ch in channels.values():
        _attach_file(ch)
    for entry in layers:
        for ch in entry["channels"].values():
            _attach_file(ch)

    mattype = int(scalars.get("mattype", 0))
    blend_mode = int(scalars.get("blend_mode", 0))
    render_mode, alpha_blend_lossy = render_mode_for(mattype, blend_mode)

    # --- the alpha chain, as the engine multiplies its terms together ---------
    k_alpha = float(scalars.get("alpha", 1.0))
    alpha_terms: list[str] = []
    if "alpha" in channels:
        alpha_terms.append("ALPHA_MAP")
    base = channels.get("base_color")
    base_alpha_ok = bool(
        base and base.get("dxgi") in ALPHA_CAPABLE_DXGI
        and split_role(base["role_key"])[1] in CHANNEL_PACKED_SUFFIXES)
    if base_alpha_ok and render_mode != RENDER_MODE_OPAQUE:
        alpha_terms.append("BASE_COLOR_ALPHA")
        if "alpha" not in channels:
            derived = dict(base)
            derived["from_channel"] = "base_color"
            derived["alpha_channel"] = "A"
            derived["punchthrough"] = base.get("dxgi") in PUNCHTHROUGH_ALPHA_DXGI
            derived["alpha_mode"] = ALPHA_MODE_CHANNEL_PACKED
            channels["alpha"] = derived
    if k_alpha != 1.0:
        alpha_terms.append("SCALAR_ONLY")
    alpha_source = alpha_terms[0] if alpha_terms else "NONE"

    # --- emissive: intensity must come from the ROUTED emissive layer ---------
    emission = channels.get("emission")
    emissive_layer = emission.get("layer") if emission else None
    emissive_intensity = emissive_intensity_for_layer(scalars, emissive_layer)
    emissive_scale = scalars.get("emissive_scale")
    if emissive_scale is None:
        emissive_scale = _named_value(scalars, "k_emissive_scale",
                                      HASH_K_EMISSIVE_SCALE)
    if emissive_scale is None:
        emissive_scale = DEFAULT_EMISSIVE_SCALE
    emissive_tint = _layer_scalar(scalars, emissive_layer, "emissive_tint")

    alpha_threshold = scalars.get("alpha_threshold")
    if alpha_threshold is None:
        alpha_threshold = _named_value(scalars, "k_alpha_threshold",
                                       HASH_K_ALPHA_THRESHOLD)
    ior = scalars.get("refractive_index")
    if ior is None:
        ior = _named_value(scalars, "k_refractive_index", HASH_K_REFRACTIVE_INDEX)

    roughness = channels.get("roughness")
    roughness_is_sqrt = bool(roughness and roughness.get("roughness_is_sqrt"))
    ao_channel = roughness.get("ao_channel") if roughness else None

    spec = {
        "key": key,
        "shaderset_hash": shaderset_hash,
        "material_hash": material_hash,
        "double_sided": bool(scalars.get("double_sided", False)),
        "blend_mode": blend_mode,
        "base_color_factor": list(scalars.get("base_color_factor", [1.0, 1.0, 1.0, 1.0])),
        "emissive_color": list(scalars.get("emissive_color", [0.0, 0.0, 0.0])),
        "emissive_intensity": float(emissive_intensity),
        "alpha": k_alpha,
        "channels": channels,
        "role_textures": role_textures,   # keep raw for audit
        # --- additive keys ----------------------------------------------------
        "layers": layers,
        "primary_layer": layered["primary_layer"],
        "unrouted_roles": layered["unrouted"],
        "render_mode": render_mode,
        "alpha_source": alpha_source,
        "alpha_terms": alpha_terms,
        "alpha_blend_lossy": alpha_blend_lossy,
        "alpha_threshold": (None if alpha_threshold is None else float(alpha_threshold)),
        "alpha_threshold_default": DEFAULT_ALPHA_THRESHOLD,
        "emissive_layer": emissive_layer,
        "emissive_scale": float(emissive_scale),
        "emissive_tint": (list(emissive_tint) if emissive_tint is not None else None),
        "ior": (None if ior is None else float(ior)),
        "roughness_is_sqrt": roughness_is_sqrt,
        "ao_channel": ao_channel,
        "mattype": mattype,
        "mattype_name": scalars.get("mattype_name") or MATTYPE_NAMES.get(mattype, ""),
        "blend_mode_name": (scalars.get("blend_mode_name")
                            or BLENDMODE_NAMES.get(blend_mode, "")),
    }
    # per-layer emissive intensity + composite record alongside each layer's channels
    for entry in layers:
        entry["emissive_intensity"] = emissive_intensity_for_layer(
            scalars, entry["index"]) if "emission" in entry["channels"] else None
        entry["blend"] = layer_blend_for(entry["index"], entry["channels"], scalars)
    spec["layer_blend_suppressed"] = [e["index"] for e in layers
                                      if e.get("blend")
                                      and e["blend"]["suppressed_at_rest"]
                                      and e["blend"]["gated_channels"]]
    # merged view: point each channel that a mask gates at the layer that owns it
    blend_by_layer = {e["index"]: e.get("blend") for e in layers}
    for name, ch in channels.items():
        blend = blend_by_layer.get(ch.get("layer"))
        if blend is not None and name in blend["gated_channels"]:
            ch["blend_layer"] = blend["layer"]
    # `channels["alpha"]` derived from `composite_diffuse.w` is not in `layers[]`
    # (it is synthesised above), but it IS the same layer's alpha and the engine
    # blends it with `mask * transparency_blend_alpha`. Register it.
    derived_alpha = channels.get("alpha")
    if derived_alpha is not None and derived_alpha.get("from_channel") == "base_color":
        blend = blend_by_layer.get(derived_alpha.get("layer"))
        if blend is not None and "base_color" in blend["gated_channels"]:
            derived_alpha["blend_layer"] = blend["layer"]
            if "alpha" not in blend["gated_channels"]:
                blend["gated_channels"] = sorted(blend["gated_channels"] + ["alpha"])
    # carry through the material-scalar audit extras when present
    for extra in ("flags", "flag_names", "materialfx", "is_emissive",
                  "named_scalars", "named_scalars_resolved"):
        if extra in scalars:
            spec[extra] = scalars[extra]
    return spec


# --- precomputed-TSV resolvers ----------------------------------------------

def load_binding_table(path: Path) -> dict[str, list[str]]:
    """meshlist_hash -> ordered [shaderset_hash] (only parse_ok rows)."""
    table: dict[str, list[str]] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row.get("parse_ok") != "True":
                continue
            ml = row["meshlist_hash"].lower()
            shd = [h.strip().lower() for h in (row.get("shaderset_hashes") or "").split(";") if h.strip()]
            table[ml] = shd
    return table


def load_shaderset_textures(scan_path: Path, names: dict[int, str]
                            ) -> dict[str, dict[str, str]]:
    """shaderset_hash -> {role_key -> tex_hash}, role from cracked inputname or unknown_s{slot}."""
    table: dict[str, dict[str, str]] = {}
    with Path(scan_path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            shd = row["shaderset_hash"].lower()
            ihex = row["inputname_hash"].lower().zfill(16)
            role, _conf = INPUTNAME_ROLE.get(ihex, (None, None))
            if role is None:
                try:
                    ih = int(ihex, 16)
                    role = names.get(ih)
                except ValueError:
                    role = None
            if role is None:
                role = f"unknown_s{row.get('slot', 'x')}"
            table.setdefault(shd, {})[role] = row["textureassetid_hash"].lower()
    return table


def load_dxgi_by_tex(*manifest_paths: Path) -> dict[str, int]:
    """tex_hash -> DXGI format int, from one or more texture-manifest TSVs."""
    out: dict[str, int] = {}
    for mp in manifest_paths:
        mp = Path(mp)
        if not mp.exists():
            continue
        with mp.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                th = (row.get("textureassetid") or row.get("tex_hash") or "").lower()
                fmt = row.get("dxgi_format", "")
                if th and fmt.isdigit():
                    out[th] = int(fmt)
    return out


def load_binding_full(path: Path) -> dict[str, dict[str, list[str]]]:
    """meshlist_hash -> {"materials": [hash], "shadersets": [hash]} (parse_ok rows).

    Both lists are index-ordered so a draw's materialidx / shadersetidx select
    directly into them.
    """
    table: dict[str, dict[str, list[str]]] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row.get("parse_ok") != "True":
                continue
            ml = row["meshlist_hash"].lower()
            table[ml] = {
                "materials": [h.strip().lower()
                              for h in (row.get("material_hashes") or "").split(";") if h.strip()],
                "shadersets": [h.strip().lower()
                               for h in (row.get("shaderset_hashes") or "").split(";") if h.strip()],
            }
    return table


def load_texture_homes(scan_path: Path | None, *manifest_paths: Path) -> dict[str, str]:
    """tex_hash -> home archive hash.

    Merges the shader-set scan TSV (`texture_archive_hash` column, per binding)
    and any texture-manifest TSVs (`source_archive` column). Lets the extractor
    pull a texture out of the archive it actually lives in, even when that is not
    the mesh's own archive (very common for shared character/prop textures).
    """
    out: dict[str, str] = {}
    if scan_path is not None and Path(scan_path).exists():
        with Path(scan_path).open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                th = (row.get("textureassetid_hash") or "").lower()
                home = (row.get("texture_archive_hash") or "").lower()
                if th and home:
                    out.setdefault(th, home)
    for mp in manifest_paths:
        mp = Path(mp)
        if not mp.exists():
            continue
        with mp.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                th = (row.get("tex_hash") or row.get("textureassetid") or "").lower()
                home = (row.get("source_archive") or "").lower()
                if th and home:
                    out.setdefault(th, home)
    return out


def roles_from_input_rows(rows, names: dict[int, str]) -> dict[str, str]:
    """{role_key -> tex_hash} from live SShaderInputData scan rows (direct mode).

    `rows` are `scripts/le_shaderset_scan.py::ShaderTexRow` objects (fields
    inputname_hash / textureassetid_hash / slot). Same role-cracking order as the
    TSV path: cracked INPUTNAME_ROLE, then hash_lookup name, then unknown_s{slot}.
    """
    table: dict[str, str] = {}
    for r in rows:
        ihex = str(r.inputname_hash).lower().zfill(16)
        role, _conf = INPUTNAME_ROLE.get(ihex, (None, None))
        if role is None:
            try:
                role = names.get(int(ihex, 16))
            except ValueError:
                role = None
        if role is None:
            role = f"unknown_s{getattr(r, 'slot', 'x')}"
        table[role] = str(r.textureassetid_hash).lower()
    return table
