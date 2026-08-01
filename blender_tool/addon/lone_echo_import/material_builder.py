"""Build a Principled BSDF material from a .lemesh material spec.

Targets Blender 4.x / 5.x (developed and RNA-probed on **5.1.1**). Handles missing
texture files gracefully: the material is still created with scalar defaults and the
intended texture hash recorded as a custom property, so nothing is silently lost.

Two things in here are load-bearing and easy to get wrong:

1. **`image.alpha_mode`**, measured on Blender 5.1.1. Blender defaults to
   `'STRAIGHT'`, which *multiplies the RGB by the alpha channel* on load. Measured on
   a `layer0_composite_diffuse` texel (431,11) of `6f51c495d957d59a.dds`
   (BC3_UNORM_SRGB): raw sRGB8 `(192,151,0,28)` -> Image Texture `Color` out was
   `(0.007499, 0.005182, 0)` under `'STRAIGHT'` versus the ground-truth
   `(0.527115, 0.309469, 0)` -- **70x too dark**; a texel with `alpha == 0` came out
   pure black. `'CHANNEL_PACKED'` reproduced the DDS bit-exactly. Every texture in
   this data packs alpha as an independent signal -- the engine's alpha term is
   `composite_diffuse.w` and the albedo is *not* premultiplied -- so
   `'CHANNEL_PACKED'` is right for all of them. Alpha itself is always linear
   regardless of the RGB colour space.

2. **`blend_method` is a dead alias on 4.2+**, measured on Blender 5.1.1:
   writing `OPAQUE`, `CLIP` or `HASHED` all read back as `HASHED` and collapse to
   `surface_render_method = 'DITHERED'`; only `BLEND` gives `'BLENDED'`. So the old
   `mat.blend_method = "CLIP"` could never clip. The pass is driven from
   `surface_render_method` here, and a cutout is a `Math(GREATER_THAN)` node.
"""

from __future__ import annotations

from pathlib import Path

import bpy   # type: ignore


# ---------------------------------------------------------------------------
# Pure-python decision layer (no bpy) -- unit-tested by
# tests/test_material_builder_nodes.py without Blender.
# ---------------------------------------------------------------------------

# CGMaterial::EMaterialType -- the render PASS (`SGMaterialData +0x2a`).
# See docs/MATERIALS.md for how these map onto a Blender render mode.
MATTYPE_OPAQUE = frozenset({0, 1})            # eMTDeferredOpaque, eMTForwardOpaque
MATTYPE_ALPHA_TESTED = 9                      # eMTAlphaTested   -> clip()
MATTYPE_BLEND = frozenset({2, 3, 4, 16})      # Forward/LowRes/Solid transparent, PostAA
MATTYPE_REFRACTION = 11                       # eMTRefraction
MATTYPE_SKIRT = 10                            # eMTSkirt -- no Blender equivalent

# EBlendMode -- the blend EQUATION (`SGMaterialData +0x28`).
BLENDMODE_OPAQUE = 0
BLENDMODE_BLEND = frozenset({7, 11, 12})      # transparent, premultiplied, translucent
BLENDMODE_ADDITIVE = frozenset({1, 8})        # additive, linear dodge -- LOSSY in EEVEE

# `k_alpha_threshold`, at the engine's authored default.
DEFAULT_ALPHA_THRESHOLD = 0.5
# `k_refractive_index` is authored 1.0; 1.45 is Blender's glass convention and is only
# used when the material carries no index at all.
DEFAULT_IOR = 1.45

# BC3/DXT5-family DXGI formats: a real 8-bit alpha block, so a dedicated alpha map in
# one of these carries its signal in `.a`. Single-channel/BC4 and friends carry it in
# `.r`. Overridable per channel with `"component": "A"|"R"`.
DXGI_HAS_ALPHA_BLOCK = frozenset({
    2, 10, 26, 28, 29, 40, 87, 88, 91, 93,    # uncompressed RGBA
    74, 75, 76, 77, 78,                        # BC2/BC3
    97, 98, 99,                                # BC7
})

MESH_FLAG_DIFFUSE_VERTEX_COLOR = 0x2000       # CGMeshData eDiffuseVertexColor

# --- layer compositing -------------------------------------------------------
# `blend = saturate((vertex_blend - height) / fade) * saturate(mask.R * scale + offset)`
# then `BlendValue(lower, layer, blend * <channel>_blend_alpha, blend_mode)`.
# Mode 6 `eBlendTransparent` is the authored default and is a LERP,
# `(1 - m) * base + m * layer`. See `le_mesh/materials.py` for the whole composite.
LAYER_BLEND_LERP_MODES = frozenset({6, 10})   # transparent, detail-override
LAYER_BLEND_ADD_MODES = frozenset({1, 7})     # additive, linear dodge
DEFAULT_BLEND_MASK_COMPONENT = "R"            # `k_blend_mask[i].x`
# The `blend_mask` sampler defaults to a white texture, so a layer with no mask
# texture bound samples 1.0 -- NOT 0.0.
DEFAULT_BLEND_MASK_VALUE = 1.0

# Blender 5.1.1 `ShaderNodeMix` socket indices (measured: the named
# sockets are ambiguous -- "A" exists four times, once per data type -- so the
# index is the only safe accessor). FLOAT: Factor 0, A 2, B 3, Result outputs[0].
# RGBA:  Factor 0, A 6, B 7, Result outputs[2].
MIX_FLOAT_SOCKETS = (0, 2, 3, 0)
MIX_RGBA_SOCKETS = (0, 6, 7, 2)


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

    The parameter is animatable with a soft range of [-1, 1], and every shipped
    value in the corpus is `-1.0`, i.e. the layer is parked at its animated OFF
    extreme. The stored
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

    Constant in three cases: no mask texture bound (the sampler returns white =
    1.0), `saturate` pinned to 0 because `scale + offset <= 0`
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
    """`k_alpha` -- the authored global alpha multiplier.

    Before 0.3.0 this was never applied, so `k_alpha = 0.25` with no opacity map
    rendered fully opaque. It is the last term of the engine's alpha chain.
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


def resolve_render_mode(spec: dict) -> str:
    """-> 'OPAQUE' | 'CLIP' | 'BLEND'.

    Prefers an explicit `render_mode` from the manifest (the decoder owns that
    decision). Otherwise derives it from `mattype` (the pass) and falls back to
    `blend_mode` (the equation). Before 0.3.0 both were carried in the spec and
    never read.

    A material with `k_alpha < 1` and no other transparency evidence is upgraded to
    BLEND, otherwise applying `k_alpha` would be invisible.
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
            elif mt in MATTYPE_BLEND:
                mode = "BLEND"
            elif mt in MATTYPE_OPAQUE or mt in (MATTYPE_SKIRT, MATTYPE_REFRACTION):
                mode = "OPAQUE"
        if mode is None:
            bm = spec.get("blend_mode")
            if isinstance(bm, int) and (bm in BLENDMODE_BLEND or bm in BLENDMODE_ADDITIVE):
                mode = "BLEND"
            else:
                mode = "OPAQUE"
    if mode == "OPAQUE" and k_alpha(spec) < 1.0:
        return "BLEND"
    return mode


def surface_render_method_for(render_mode: str) -> str:
    """EEVEE Next has exactly two methods (measured on Blender 5.1.1: the enum
    items are `['DITHERED', 'BLENDED']`). A cutout is DITHERED plus a
    `Math(GREATER_THAN)` node -- there is no `CLIP` method to select.
    """
    return "BLENDED" if render_mode == "BLEND" else "DITHERED"


def is_lossy_blend(spec: dict) -> bool:
    """Additive / linear-dodge has no EEVEE equivalent; flag the approximation."""
    bm = spec.get("blend_mode")
    return isinstance(bm, int) and bm in BLENDMODE_ADDITIVE


def image_alpha_mode(chan: dict | None, default: str = "CHANNEL_PACKED") -> str:
    """`image.alpha_mode` for one channel -- see the module docstring (fix 1).

    Honours an `alpha_mode` hint from the manifest when the decoder supplies one;
    otherwise CHANNEL_PACKED, because every texture in this data reads RGB and
    alpha as independent signals.
    """
    if chan:
        hint = chan.get("alpha_mode")
        if isinstance(hint, str) and hint.upper() in (
                "STRAIGHT", "PREMUL", "CHANNEL_PACKED", "NONE"):
            return hint.upper()
    return default


def alpha_component_of(chan: dict) -> str:
    """'A' or 'R' -- which component of a dedicated alpha/opacity map is the signal."""
    comp = chan.get("component")
    if isinstance(comp, str) and comp.upper() in ("A", "R", "G", "B"):
        return comp.upper()
    dxgi = chan.get("dxgi")
    return "A" if isinstance(dxgi, int) and dxgi in DXGI_HAS_ALPHA_BLOCK else "R"


def split_opacity_channels(channels: dict) -> tuple[dict | None, dict | None]:
    """-> (alpha_channel, transmission_channel).

    The new manifest contract separates `alpha` (a scalar opacity multiplier) from
    `transmission` (`opacity_map`, a float3 *tint*: the engine does
    `output.color.rgb += background * material.opacity`). Older manifests lump both
    into one `opacity` channel, so split it on the role name -- routing an
    `opacity_map` to Blender's Alpha socket makes coloured glass uniformly
    see-through instead of tinting what is behind it.
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


def uses_base_color_alpha(spec: dict, channels: dict) -> bool:
    """Is the base-colour texture's `.a` the opacity term?

    `layer0_composite_diffuse.a` IS the opacity. Honours an explicit
    `alpha_source` when present.
    """
    src = spec.get("alpha_source")
    if isinstance(src, str):
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
    """`composite_components.x` is *sqrt*roughness: the engine squares it
    (`roughness = s*s`) before use."""
    flag = spec.get("roughness_is_sqrt")
    if isinstance(flag, bool):
        return flag
    return bool(chan) and "composite_components" in str(chan.get("role_key", ""))


def ao_channel_of(spec: dict, chan: dict | None) -> str | None:
    """AO is `composite_components.y`."""
    ao = spec.get("ao_channel")
    if isinstance(ao, str) and ao.upper() in ("R", "G", "B", "A"):
        return ao.upper()
    if chan and "composite_components" in str(chan.get("role_key", "")):
        return "G"
    return None


# --- specular / F0 ----------------------------------------------------------
# `layers[i].specalbedo[0]` IS the Schlick F0 term: the engine's Fresnel is
# `specalbedo + (1 - specalbedo) * (1 - dot(l,h))^5`. Two samplers feed that one
# slot:
#
#   composite_specular : specalbedo = .xyz * .w ; specintensity = .w
#   specular_map       : specalbedo = k_enable_specular * speculartint *
#                        specular_map.xyz * k_fresnel ; specintensity = k_fresnel
#
# Same quantity, different scale: the composite map carries its own intensity in
# alpha, the non-composite one is scaled by the material scalar `k_fresnel`
# (authored 0.010).
SPEC_MAP_FRESNEL_DEFAULT = 0.01
# Blender: Principled's dielectric F0 == F0(IOR) * 2 * `Specular IOR Level` *
# `Specular Tint`, LINEAR and UNCLAMPED (measured in Cycles + EEVEE on 5.1.1; see
# docs/MATERIALS.md). `Specular IOR Level` is hard-capped
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

    Authored default 0.010; overridden only if the material actually serialises
    `layerN_fresnel` (no shipped material in the 51-package corpus does).
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

    Off restores the pre-0.3.0 look (Principled's flat F0 = 0.04), which measured
    6x-20x too dark on shipped `composite_specular` data and 4x too bright on the
    `specular_map` panels.
    """
    if not opts:
        return True
    return bool(opts.get("wire_specular", True))


def emission_tint(spec: dict) -> tuple[float, float, float]:
    """The emissive tint, with the black-tint trap guarded.

    `emissive_color` is decoded from `SGMaterialData.bakeemissivecolor`, which is
    `(0,0,0)` on **every** genuinely emissive material inspected. It is the
    *bake-time* colour, not the runtime tint (the runtime tint is
    `layerN_emissive_tint_color`, whose authored default is `1,1,1,1`).
    Multiplying an emissive map by a black tint annihilates the
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
    multipliers. Any fudge factor here would be a bug, not a calibration.
    """
    def _f(key, default):
        try:
            return float(spec.get(key, default))
        except (TypeError, ValueError):
            return default
    return _f("emissive_intensity", 1.0) * _f("emissive_scale", 1.0)


def refractive_index(spec: dict) -> float:
    v = spec.get("ior")
    if v is None:
        v = spec.get("refractive_index")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return DEFAULT_IOR
    return v if v > 1.0 else DEFAULT_IOR


def wants_vertex_color_diffuse(obj: dict) -> bool:
    """Does this MESH declare `eDiffuseVertexColor` (0x2000)?

    The engine's composite path does `diffusealbedo = composite_diffuse.xyz *
    albedovertex.xyz`, with the vertex colour selected by the per-layer
    `enable_albedo_vertex_color` option; `eDiffuseVertexColor` is the mesh-side
    counterpart, and that linkage is inferred. Gated, never unconditional: most
    meshes carry white or unused `color0`, and this path is not exercised by the
    checked-in tests.
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
    return n, (MIX_FLOAT_SOCKETS if data_type == "FLOAT" else MIX_RGBA_SOCKETS)


def _blend_amount_socket(nt, pkg_dir: Path, blend: dict, opts: dict, x, y):
    """-> (socket, constant). Exactly one is not None.

    Builds `saturate(mask.R * blend_mask_scale + blend_mask_offset)` as
    `Math(MULTIPLY_ADD, use_clamp=True)` -- `use_clamp` IS `saturate()`.

    The vertex-blend factor `saturate(vertex_blend / blend_fade)` is deliberately
    NOT built: `vertblend` is component (i-1) of the SECOND vertex colour stream,
    which `mesh_builder` does not import today, and whether it is even sampled is a
    shader permutation bit that is not on disk.
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
        # the `blend_mask` sampler defaults to white -> mask.R == 1.0.
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
    ma.use_clamp = True                    # this IS saturate()
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
    (measured on Blender 5.1.1), so it is not used at all here.
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
    alpha_ch, trans_ch = split_opacity_channels(channels)
    render_mode = resolve_render_mode(spec)
    mattype = spec.get("mattype")

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
        # eBlendAdditive / eBlendLinearDodge have no EEVEE equivalent.
        mat["le_blend_lossy"] = True
    if mattype == MATTYPE_SKIRT:
        mat["le_skirt"] = True          # no Blender equivalent; imported opaque + tagged

    # base colour ------------------------------------------------------------
    # `base_color_factor` is `SGMaterialData.bakecolor` == the authored
    # `k_hardware_color`, UI name "Bake Color". That parameter is an AUTHORING-side
    # value: it never reaches a shader, and the runtime albedo tint is the
    # per-layer `k_albedo_tint_color` instead. So `bakecolor` is NOT multiplied into
    # the texture; it is used only as the flat fallback colour when there is no
    # base-colour map, where it is the baker's own approximation of the surface.
    # Multiply-vs-replace decided on that: REPLACE (fallback only).
    bc = channels.get("base_color")
    base_in = _principled_input(bsdf, "Base Color")
    bc_node = None
    bc_factor = tuple(spec.get("base_color_factor", [1, 1, 1, 1]))
    # A base colour that lives on layer >= 1 is composited over the lower layers
    # by that layer's blend mask; with no lower-layer albedo the engine's lerp
    # runs from the flat fallback colour to the texture. That base value is
    # inferred -- the lower layers genuinely bind no albedo, so the engine samples
    # its own `albedo_map` default there and we cannot see that value from disk.
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

    # roughness / AO ---------------------------------------------------------
    rg = channels.get("roughness")
    rough_in = _principled_input(bsdf, "Roughness")
    ao_socket = None
    rg_blend = blend_for_channel(spec, channels, "roughness")
    rg_gate = (None, None)
    if rg_blend is not None:
        rg_gate = _layer_gate(nt, pkg_dir, rg_blend, "roughness", opts, -2400, 200)
    if rg and rough_in and rg_gate[1] != 0.0:
        img = _load_image(pkg_dir, rg.get("file", ""), "Non-Color", image_alpha_mode(rg))
        if img:
            node = _tex_node(nt, img, "Non-Color", -1200, 0, image_alpha_mode(rg),
                             label="composite_components")
            rough_src = node.outputs["Color"]
            if roughness_is_sqrt(spec, rg):
                # ⚠ DO NOT SQUARE HERE. The engine's GGX alpha is
                # `sqrtroughness^2` -- it sets `m = sqrtroughness*sqrtroughness`
                # and its GGX NDF then uses `m*m` -- while Blender's GGX alpha is
                # `Roughness^2`. Equating the two gives
                #     Roughness == sqrtroughness == composite_components.x, RAW.
                # Squaring it made Blender's alpha `sqrtroughness^4` and the peak
                # highlight 2.4x (at 0.80) to 920x (at 0.15) too bright, measured
                # against the engine's closed form.
                # `roughness_is_sqrt` still means "this texel is in sqrt space";
                # only the Blender-side conversion is the identity.
                sep = nt.nodes.new("ShaderNodeSeparateColor")
                sep.location = (-900, 0)
                nt.links.new(node.outputs["Color"], sep.inputs["Color"])
                rough_src = sep.outputs[0]
                ao = ao_channel_of(spec, rg)
                if ao:
                    ao_socket = sep.outputs[{"R": 0, "G": 1, "B": 2}.get(ao, 1)]
                    mat["le_ao_channel"] = ao
            if rg_blend is None or rg_gate == (None, 1.0):
                nt.links.new(rough_src, rough_in)
            else:
                # `BlendValue(base.sqrtroughness, layer.sqrtroughness, m *
                # roughness_blend_alpha, mode)`. The lower layers bind no
                # components map, so `base` is the socket value that would
                # otherwise stand -- an inferred choice.
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
    # indirect* diffuse term; Principled has no occlusion input,
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
    nm = channels.get("normal")
    norm_in = _principled_input(bsdf, "Normal")
    if nm and norm_in:
        img = _load_image(pkg_dir, nm.get("file", ""), "Non-Color", image_alpha_mode(nm))
        if img:
            tex = _tex_node(nt, img, "Non-Color", -2200, -300, image_alpha_mode(nm),
                            label="normal")
            src = _normal_chain(nt, tex, nm.get("reconstruct_z", False), -2000, -300)
            nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-500, -300)
            nt.links.new(src, nmap.inputs["Color"])
            nt.links.new(nmap.outputs["Normal"], norm_in)

    # alpha chain ------------------------------------------------------------
    # engine:
    #   alpha = albedovertex.a * ... * albedomap.a * ... * alphamap * k_alpha
    # Blender: multiply the available terms and drive Alpha with the product. The
    # per-map sRGB->linear that the engine applies to each alpha term under the
    # output-alpha permutation is NOT reproduced -- which permutation is live is
    # not on disk.
    alpha_in = _principled_input(bsdf, "Alpha")
    terms = []
    if alpha_ch:
        img = _load_image(pkg_dir, alpha_ch.get("file", ""), "Non-Color",
                          image_alpha_mode(alpha_ch))
        if img:
            node = _tex_node(nt, img, "Non-Color", -1200, -700, image_alpha_mode(alpha_ch),
                             label="alpha_map")
            terms.append(_component_socket(nt, node, alpha_component_of(alpha_ch),
                                           -900, -700))
    if bc_node is not None and uses_base_color_alpha(spec, channels):
        # layer0_composite_diffuse.a IS the opacity.
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
        if acc is not None:
            if ka != 1.0:
                acc = _math(nt, "MULTIPLY", -400, -700, acc, ka, label="k_alpha").outputs[0]
            alpha_socket = acc
        elif ka != 1.0:
            alpha_in.default_value = ka          # k_alpha with no map at all
        if render_mode == "CLIP":
            # EEVEE Next has no CLIP render method -- the cutout is a node op
            # (the engine's own `clip(alpha - k_alpha_threshold)`).
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
    # No unit conversion: the factor is 1.0.
    em = channels.get("emission")
    em_col_in = _principled_input(bsdf, "Emission Color", "Emission")
    em_str_in = _principled_input(bsdf, "Emission Strength")
    tint = emission_tint(spec)
    strength = emission_strength(spec)
    # An emissive map on layer >= 1 rides in the layer's `lighting` output and is
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
            nt.links.new(em_src, em_col_in)
            if em_str_in:
                em_str_in.default_value = strength
    elif em_col_in:
        ec = spec.get("emissive_color", [0, 0, 0])
        if any(ec):
            em_col_in.default_value = (ec[0], ec[1], ec[2], 1.0)
            if em_str_in:
                em_str_in.default_value = strength

    # specular -> Principled `Specular Tint` ----------------------------------
    # `layers[i].specalbedo[0]` IS the Schlick F0 term, fed either by
    # `composite_specular.xyz * .w` or by `specular_map.xyz * k_fresnel`. Both
    # reach 1.0 in principle and shipped `composite_specular` data reaches it in
    # practice (17 unique maps, mip 0, sRGB-decoded RGB x linear alpha: 3 maps hit
    # 1.0, one has p50 = 0.345 / p90 = 0.852 with 65.5% of texels above 0.08).
    #
    # An earlier pass read that as unrepresentable because `Specular IOR Level` is
    # `hard_max = 1.0` -> F0 <= 0.08. That is only half the socket. `Specular
    # Tint` is `hard_max = FLT_MAX` (soft_max 1.0) and Principled's dielectric
    # normal-incidence reflectance is
    #
    #     F0 = F0(IOR) * 2 * `Specular IOR Level` * `Specular Tint`
    #
    # LINEAR and UNCLAMPED (measured on Blender 5.1.1: with the level left
    # at its 0.5 "no adjustment" point and the tint set to F0/F0(IOR), the
    # rendered normal-incidence specular matched a Glossy BSDF of colour F0 to
    # 0.00% at every F0 in {0.01 .. 1.0} and for IOR in {1.33, 1.5, 2.0} in
    # Cycles; EEVEE Next tracks the same curve within 2%).
    #
    # `.w` is NOT double-counted: it is folded into F0 here (`specalbedo = .xyz *
    # .w`) and its OTHER engine role -- scaling the diffuse lobe by
    # `(1 - Fresnel(specintensity))` -- is what Principled already does for free
    # from the same F0 (measured: at
    # F0 = 0.85 the Principled total minus the specular-only lobe was 0.004782 vs
    # the engine's 0.004775 diffuse term, 0.15%).
    #
    # Residual, common to EVERY Blender construction and NOT fixable by wiring:
    # the engine's GGX visibility term uses the Burley remap `alpha = ((m+1)/2)^2`
    # where Blender uses Smith with `alpha = roughness^2`. Equal at normal
    # incidence, Blender is ~1.4x brighter at 60 deg and ~9x at 85 deg in the
    # mirror configuration. See docs/MATERIALS.md.
    sp = channels.get("specular")
    sp_tint_in = _principled_input(bsdf, "Specular Tint")
    sp_level_in = _principled_input(bsdf, "Specular IOR Level")
    _ior_in = _principled_input(bsdf, "IOR")
    ior_used = (refractive_index(spec) if mattype == MATTYPE_REFRACTION
                else (float(_ior_in.default_value) if _ior_in is not None else 1.5))
    if sp:
        mat["le_specular_role"] = str(sp.get("role_key", ""))
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
            smix, (fi, ai, bi, ri) = _mix_node(
                nt, "RGBA", "MULTIPLY", -780, -700,
                label=f"Specular Tint = F0 / F0(IOR)  (x{scale:g})")
            smix.inputs[fi].default_value = 1.0
            nt.links.new(src, smix.inputs[ai])
            smix.inputs[bi].default_value = (scale, scale, scale, 1.0)
            src = smix.outputs[ri]
            if sp_blend is not None and sp_gate != (None, 1.0):
                # `BlendValue(base.specalbedo, layer.specalbedo, m *
                # spec_albedo_blend_alpha, mode)`. No lower layer binds a specular
                # map, so `base` is the tint that would otherwise stand -- 1.0,
                # i.e. F0 = F0(IOR) (inferred, same choice as roughness).
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
    # The engine does `output.color.rgb += background * material.opacity`
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
    return mat


# ---------------------------------------------------------------------------
# Per-mesh vertex-colour variant
# ---------------------------------------------------------------------------

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
    name = f"{mat.name}__vcol"
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
