"""Decode SGMaterialData scalar parameters from a material primary slice.

Pure stdlib. Importable without oodle or bpy (the CSymbol64 hash is inlined so
this module has no dependency on the `scripts/` decode stack). The extractor
hands this an already-decompressed CGMaterialResourceWin7 primary slice; it
returns the durable scalar knobs the Blender addon needs:

    {
      base_color_factor: [r,g,b,a],   # SGMaterialData.bakecolor  @0x08
      emissive_color:    [r,g,b],     # SGMaterialData.bakeemissivecolor @0x18 (RGB)
      emissive_intensity: float,      # LEGACY flat value; see `layers` below
      alpha:             float,       # materialprop k_alpha (fallback 1.0)
      blend_mode:        int,         # SGMaterialData.blendmode  @0x28 (u16)
      double_sided:      bool,        # flags & eDoubleSided
      # extras (audit / consumers that want them):
      mattype, flags, flag_names, materialfx, is_emissive, named_scalars,
      # added for the transparency/emissive front:
      layers, emissive_scale, alpha_threshold, refractive_index,
      mattype_name, blend_mode_name, named_scalars_resolved,
      emissive_layer_indices, bake_emissive_nonzero, scalar_defaults_applied
    }

Evidence labels used in the comments below:
`name-only` (the engine's own struct / enum names, nothing shipped exercises
them) · `shader-confirmed` (matches the arithmetic the engine's own shaders and
material schema perform) · `stream-confirmed` (decoded from shipped archive
bytes) · `inferred`.

Disk layout (`name-confirmed` against the engine's own type names; framing
stream-validated by the reference exporters):

  [0x000 .. 0x160)  SGMaterialData header (direct memory image)
     +0x000 u64   materialfx (CSymbol64)
     +0x008 4×f32 bakecolor -- the authored `k_hardware_color`, UI "Bake Color"
                  (the material asset schema). ⛔ NOT a runtime multiplier: the
                  symbol appears in the authoring schema and in ZERO engine
                  shader. RGB only; its 4th float has no `:a` widget in
                  the schema and is unauthored (0.0 on 27/100 level materials and
                  8/11 of character c6bc.._64b4b5b2..). Consumed as a flat
                  FALLBACK when no base-colour map resolves --
                  `material_builder.base_color_fallback`.
     +0x018 4×f32 bakeemissivecolor (RGBA; RGB != 0 => emissive)
     +0x028 u16   blendmode
     +0x02a u16   mattype
     +0x02c u32   flags (EFlags)
     +0x030 f32   shadowfadedist
     +0x060 u64   materialprops.iused         (CTable<u32> count)
     +0x098 u64   materialpropoffsets.iused   (CMap<CSymbol64,u32> count)
     +0x0d8 u64   uvsets.iused                (CTable<CSymbol64> count)
     +0x110 u64   permutations.iused          (CMap count)
     +0x150 u64   auxillaryinputs.iused       (CTable<SShaderInputData> count)
  [0x160 ..)  trailing arrays, in order:
     materialprops        n_props     × 4      (u32 words; decode as float32)
     materialpropoffsets  n_propoff   × 16     (key u64 @0, byteoffset u32 @8, pad @12)
     uvsets               n_uvsets    × 8
     permutations         n_perms     × 16
     auxillaryinputs      n_inputs    × 0x20

A materialpropoffsets entry maps a property-name hash -> a byte offset into the
materialprops word array (offset/4 = word index).  The pointed-at u32 word
reinterpreted as a float32 is the scalar value (stream-confirmed).  Multi-word
parameters (WidgetColor4, range) are read as `arity` CONSECUTIVE words starting
at that index — `inferred` from the authored parameter type, not yet
stream-confirmed (no colour-valued materialprop appears in any shipped material
decoded so far).

Only *authored overrides* are serialized: shipped materials carry 0-8
materialprops out of the several hundred declarable parameters
(`stream-confirmed`), so an absent parameter means "left at the authored
default" (see AUTHORED_DEFAULTS_*).
"""

from __future__ import annotations

import struct

# --- SGMaterialData::EFlags (`name-confirmed`) -------------------------------
EFLAGS = {
    "eDoubleSided":               0x001,
    "eCastShadows":               0x002,
    "eGIOccluder":                0x004,
    "eGIReceiver":                0x008,
    "eUseAmbientSpecular":        0x010,
    "eUseVertexLighting":         0x020,
    "eUseFoliageAnimation":       0x040,
    "eEyeMaterial":               0x080,
    "eOutputTransparentVelocity": 0x100,
}
E_DOUBLE_SIDED = 0x001

# --- NRadEngine::CGMaterial::EMaterialType (name-only, kNumMatTypes = 17) -----
# Values marked (*) have been seen in shipped bytes (stream-confirmed).
MATTYPE_NAMES = {
    0: "eMTDeferredOpaque", 1: "eMTForwardOpaque",       # (*) 1
    2: "eMTForwardTransparent",                          # (*)
    3: "eMTLowResTransparent", 4: "eMTSolidTransparent",
    5: "eMTFullScreenEffect", 6: "eMTParticles", 7: "eMT2D", 8: "eMTDebug",
    9: "eMTAlphaTested",                                 # (*)
    10: "eMTSkirt",                                      # (*)
    11: "eMTRefraction", 12: "eMTHair", 13: "eMTSkydome",
    14: "eMTOutline", 15: "eMTOutlineDepthFail",
    16: "eMTTransparentPostAA",                          # (*)
}

# --- NRadEngine::EBlendMode (name-only, kNumBlendModes = 18) ------------------
# 0, 7, 8, 12 stream-confirmed.
BLENDMODE_NAMES = {
    0: "eBlendOpaque", 1: "eBlendAdditive", 2: "eBlendSubtractive",
    3: "eBlendMultiply", 4: "eBlendDarken", 5: "eBlendLighten", 6: "eBlendScreen",
    7: "eBlendTransparent", 8: "eBlendLinearDodge", 9: "eBlendLinearBurn",
    10: "eBlendSkirt", 11: "eBlendPremultipledAlpha", 12: "eBlendTranslucent",
    13: "eBlendMin", 14: "eBlendMax", 15: "eBlendAlphaToCoverage",
    16: "eBlendNoColorWrites", 17: "eBlendReverseSubtractive",
}

HEADER_SIZE = 0x160
OFF_MATERIALFX          = 0x000
OFF_BAKECOLOR           = 0x008
OFF_BAKEEMISSIVECOLOR   = 0x018
OFF_BLENDMODE           = 0x028
OFF_MATTYPE             = 0x02A
OFF_FLAGS               = 0x02C
OFF_SHADOWFADEDIST      = 0x030
OFF_MATERIALPROPS_IUSED = 0x060
OFF_PROPOFFSETS_IUSED   = 0x098
OFF_UVSETS_IUSED        = 0x0D8
OFF_PERMS_IUSED         = 0x110
OFF_AUXINPUTS_IUSED     = 0x150

SIZEOF_SHADERINPUTDATA  = 0x20
MAX_REASONABLE          = 10_000

# The ubershader declares exactly four material layers
# (`:layer0 .. :layer3 := UberMaterialLayer`, `name-confirmed` in the material
# asset schema).  `layers[]` is always at least this long so a consumer
# can index the layer its texture role named without a bounds check.
N_DECLARED_LAYERS = 4
MAX_LAYER = 8            # how far the name table is generated (defensive)


# --- CSymbol64 hash (inlined; matches scripts/le_symbol_names) -----
_MASK = 0x95AC9329AC4BC9B5


def _init_seeds() -> list[int]:
    seeds: list[int] = []
    for i in range(256):
        value = 0x2B5926535897936A if (i & 0x80) else 0
        if i & 0x40:
            value ^= _MASK
        shift = 0x20
        while shift:
            value = (2 * value) & 0xFFFFFFFFFFFFFFFF
            if i & shift:
                value ^= _MASK
            shift >>= 1
        seeds.append((2 * value) & 0xFFFFFFFFFFFFFFFF)
    return seeds


_SEEDS = _init_seeds()


def symbol64(text: str) -> int:
    """CSymbol64 hash of an ASCII name (case-insensitive), as an int."""
    result = 0xFFFFFFFFFFFFFFFF
    for byte in text.encode("utf-8", "ignore"):
        if 0x41 <= byte <= 0x5A:      # to-lower
            byte += 0x20
        result = (((result << 8) & 0xFFFFFFFFFFFFFFFF) ^ _SEEDS[(result >> 56) & 0xFF] ^ byte)
    return result & 0xFFFFFFFFFFFFFFFF


# ---------------------------------------------------------------------------
# Authored parameter vocabulary
#
# Extracted from the engine's own material asset schema -- the UberMaterial
# declaration and the base material schema it derives from (`name-confirmed`).
# Every name below, once prefixed, is used ONLY as a CSymbol64 preimage
# candidate: a name is accepted for a hash iff symbol64(name) == hash exactly, so
# a stale or wrong entry can never produce a fabricated label (that is the defect
# `tests/test_transparency.py::test_no_fabricated_role_names` guards).
# ---------------------------------------------------------------------------

# `:layer0 .. :layer3 := UberMaterialLayer` -> every member is `layerN_<name>`.
LAYER_PARAMS = (
    "additive_thin_map", "albedo_map", "albedo_tint_color", "alpha_map",
    "ambient_specular_spread", "anisotropicrotation", "anisotropy", "ao_lighting_scale",
    "ao_map", "arbitrary_emissive_far_fade", "arbitrary_emissive_far_fade_x",
    "arbitrary_emissive_far_fade_y", "arbitrary_emissive_far_fade_z",
    "arbitrary_emissive_near_fade", "arbitrary_emissive_near_fade_x",
    "arbitrary_emissive_near_fade_y", "arbitrary_emissive_near_fade_z",
    "back_lighting_intensity", "back_lighting_map", "back_lighting_tint_color",
    "backlighting_blend_alpha", "blend_fade", "blend_fade_scale_offset_map", "blend_height",
    "blend_height_offset", "blend_height_scale", "blend_mask", "blend_mask_offset",
    "blend_mask_scale", "blend_mode", "blend_offset", "blend_offset_regions_map",
    "blend_scale", "blend_scale_regions_map", "brdf_blend_alpha", "cavity_map",
    "color_animate_map0", "color_animate_map1", "color_animate_map2", "color_animate_map3",
    "color_animate_map_intensity", "color_replace_blend", "color_replace_bucket0",
    "color_replace_bucket1", "color_replace_bucket2", "color_replace_bucket3",
    "color_replace_bucket4", "composite_components", "composite_data0", "composite_diffuse",
    "composite_normals", "composite_specular", "detail_albedo_blend_mode",
    "detail_albedo_map", "detail_albedo_map_intensity", "detail_ao_map",
    "detail_ao_map_intensity", "detail_height_map", "detail_height_map_intensity",
    "detail_normal_map", "detail_normal_map_intensity", "detail_uvoffsetu",
    "detail_uvoffsetv", "detail_uvscalepivotu", "detail_uvscalepivotv", "detail_uvscaleu",
    "detail_uvscalev", "diff_albedo_blend_alpha", "diffuse_map", "diffuse_tint_color",
    "displacement_map", "displacement_map_intensity", "displacement_maxdistance",
    "displacement_maxlevel", "emissive_far_fade", "emissive_intensity", "emissive_map",
    "emissive_near_fade", "emissive_tint_color", "enable_metallic_roughness_map",
    "flipbook_cols", "flipbook_count", "flipbook_index", "flipbook_loopmode",
    "flipbook_offset", "flipbook_phaseoffsetu", "flipbook_phaseoffsetv", "flipbook_quantizer",
    "flipbook_rows", "flipbook_speed", "flow_map", "flowmap_begin", "flowmap_end",
    "flowmap_loopmode", "flowmap_map", "flowmap_offset", "flowmap_quantizer",
    "flowmap_speed", "fresnel", "grid_color", "grid_line_width", "grid_opacity",
    "grid_unit_size", "layer_disable_mode", "layeruvset", "lighting_blend_alpha",
    "metallic_roughness_map", "normal_blend_alpha", "normal_map", "normal_map_intensity",
    "normal_softness", "opacity_map", "opacity_tint_color", "pom_amount",
    "reflection_attenuation", "reflection_intensity", "reflection_tint_color",
    "rim_albedo_intensity", "rim_albedo_tint_color", "rim_alpha_intensity", "rim_intensity",
    "rim_light_intensity", "rim_map", "rim_max_intensity", "rim_min_intensity",
    "rim_near_fade", "rim_opacity_intensity", "rim_opacity_tint_color", "rim_pow",
    "rim_ramp", "rim_range", "rim_tint_color", "roughness_blend_alpha",
    "secondary_emissive_map", "secondary_rim_map", "shadow_alpha", "shift_map_intensity",
    "skirt_normal_blend_map", "spec_albedo_blend_alpha", "spec_noise_roughness",
    "spec_noise_shift", "spec_noise_tint", "specular_gloss", "specular_map",
    "specular_noise_map", "specular_shift_map", "specular_spread", "specular_spread2",
    "specular_tint_color", "spherical_blend_offset", "spherical_blend_scale",
    "subsurface_amount", "subsurface_blend_alpha", "subsurface_falloff",
    "subsurface_intensity", "subsurface_map", "subsurface_secondary_spec_intensity",
    "subsurface_secondary_spec_roughness_offset", "subsurface_shadow_penumbra",
    "subsurface_shadow_scatter", "subsurface_small_scale", "thickness", "thickness_mask",
    "transparency_blend_alpha", "uv_transform_regions_uvset",
    "uv_transform_regions_uvset_name", "uvblendamount", "uvblenduvset", "uvblenduvsetname",
    "uvoffsetu", "uvoffsetv", "uvscalepivotu", "uvscalepivotv", "uvscaleu", "uvscalev",
    "uvset", "uvtransformregions", "velvet_fresnel", "velvet_front_spec", "wrinkle_map0",
    "wrinkle_map1", "wrinkle_map2", "wrinkle_map3", "wrinkle_map_intensity",
    # names carried over from the hand-curated the reference table; kept so the
    # generated table never shrinks.  (Several of these are really group members
    # — see GROUP_PARAMS — and simply never match as `layerN_*`.)
    "detail_uvscale", "flow_map_map", "height_map", "height_scale", "mip_fade_end",
    "mip_fade_start", "masks", "parallax_scale", "pooling", "roughness", "scale",
    "spec_intensity", "fade", "normal_bevel", "normal_fade", "weights_map",
)

# `ubermaterial:parameters` members that are NOT layer- or group-scoped.
GLOBAL_PARAMS = (
    "enable_composite_texture_streaming", "k_alpha", "k_alpha_threshold",
    "k_ao_volume_dynlight_scale", "k_baked_occlusion_dynlight_scale", "k_brdf", "k_brdf1",
    "k_damage_height_mode", "k_damage_height_scale", "k_depth_fade_distance", "k_edge_amp",
    "k_edge_cycle_speed", "k_emissive_scale", "k_enable_brdf_compositing",
    "k_global_aniso_rotation", "k_height_deform_amp", "k_height_deform_cycle_speed",
    "k_irradiance_diffuse_scale", "k_irradiance_sg_sharpness_scale",
    "k_irradiance_spec_scale", "k_refraction_amount", "k_refractive_index",
    "k_shadow_fade_distance", "k_skirt_normal_blend_amt", "k_subsurface_curvature_scale",
    "k_temporal_aa_scale", "k_transparent_alpha_threshold", "k_updown_amp",
    "k_updown_cycle_speed", "k_wind_dir", "k_wind_strength",
    # the base material asset schema
    "k_hardware_color", "k_bake_emissive_color", "k_bake_emissive_intensity",
    "blendingoptions", "materialtype", "materialfx", "materialswf",
)

# Non-layer `[PREFIXPROPERTY]` groups: `:pom := (MaterialPOMProperties)` etc.
# (the material asset schema and its UI).  Members are `<group>_<name>`.
# This axis is what recovered `pom_height_map` — see `build_name_table`.
GROUP_PARAMS = {
    "pom": ("height_map", "max_steps", "min_steps", "mip_fade_end", "mip_fade_start",
            "parallax_scale", "amount"),
    "blood": ("color", "fade", "height_scale", "normal_bevel", "normal_fade", "pooling",
              "roughness", "scale", "spec_intensity"),
    "scorch": ("color", "fade", "roughness", "scale", "spec_intensity"),
    "cutting": ("cut_decal", "glow_color", "glow_edge_fade", "glow_fade", "glow_intensity",
                "raymarch_steps", "scorch_color", "scorch_decal", "scorch_roughness",
                "scorch_specular_color", "scorch_specular_intensity"),
    "creepybio": ("activevtx0", "activevtx1", "blend0", "blend1"),
    "layerblend": ("weights_map",),
}

# Suffixes the material compiler appends to a sampler parameter to expose its UV
# transform as a scalar.  `_uoffset` / `_voffset` are stream-confirmed
# (`layer1_emissive_map_voffset`, `layer0_albedo_map_uoffset`); `_scrollspeed` is
# `stream-confirmed` from the shipped compiled-shader matparamcb variable
# `layer1_alpha_map_scrollspeed`.
SUFFIXED = ("_uoffset", "_voffset", "_uscale", "_vscale", "_intensity", "_scale",
            "_offset", "_scrollspeed")

# Material-level auxillaryinputs seen in shipped SShaderInputData tables.
AUX_INPUT_NAMES = ("cutting_cut_decal", "cutting_scorch_decal")

# Parameter word count.  WidgetColor4/Color4 -> 4 words, `range` -> 2 (l, h),
# Real3 -> 3; everything else 1.  Type read from the material asset schema
# (`name-confirmed`); the CONSECUTIVE-word packing is `inferred`.
PARAM_ARITY = {
    "albedo_tint_color": 4, "arbitrary_emissive_far_fade": 2,
    "arbitrary_emissive_near_fade": 2, "back_lighting_tint_color": 4, "blood_color": 4,
    "color_replace_bucket0": 4, "color_replace_bucket1": 4, "color_replace_bucket2": 4,
    "color_replace_bucket3": 4, "color_replace_bucket4": 4, "cutting_glow_color": 4,
    "cutting_scorch_color": 4, "cutting_scorch_specular_color": 4, "diffuse_tint_color": 4,
    "emissive_far_fade": 2, "emissive_near_fade": 2, "emissive_tint_color": 4,
    "grid_color": 4, "k_bake_emissive_color": 4, "k_hardware_color": 4, "k_wind_dir": 3,
    "opacity_tint_color": 4, "reflection_tint_color": 4, "rim_albedo_tint_color": 4,
    "rim_near_fade": 2, "rim_opacity_tint_color": 4, "rim_range": 2, "rim_tint_color": 4,
    "scorch_color": 4, "spec_noise_tint": 4, "specular_tint_color": 4,
    "subsurface_amount": 4,
}

# Authored defaults, read straight out of the engine's own material asset
# schema.  NOTHING here is invented — every value is the default that schema
# declares for the parameter (`name-confirmed`).
AUTHORED_DEFAULTS_GLOBAL = {
    "k_alpha": 1.0,                             # UberMaterial
    "k_emissive_scale": 1.0,                    # UberMaterial
    "k_transparent_alpha_threshold": 0.0001,    # UberMaterial
    "k_alpha_threshold": 0.5,                   # UberMaterial
    "k_refractive_index": 1.0,                  # UberMaterial
    "k_refraction_amount": 1.0,                 # UberMaterial
    "k_depth_fade_distance": 0.25,              # UberMaterial
    "k_skirt_normal_blend_amt": 1.0,            # UberMaterial
    "k_bake_emissive_intensity": 1.0,           # base material schema
}
AUTHORED_DEFAULTS_LAYER = {
    "emissive_intensity": 1.0,                       # UberMaterialLayer
    "emissive_tint_color": (1.0, 1.0, 1.0, 1.0),     # UberMaterialLayer
    "opacity_tint_color": (1.0, 1.0, 1.0, 1.0),      # UberMaterialLayer
    "back_lighting_intensity": 1.0,                  # UberMaterialLayer
}
# back-compat alias for consumers that just want "the k_ defaults"
AUTHORED_DEFAULTS = AUTHORED_DEFAULTS_GLOBAL


# Named scalar hashes we care about (stream-confirmed names).
HASH_K_ALPHA = symbol64("k_alpha")
HASH_K_ALPHA_THRESHOLD = symbol64("k_alpha_threshold")
HASH_K_TRANSPARENT_ALPHA_THRESHOLD = symbol64("k_transparent_alpha_threshold")
HASH_K_EMISSIVE_SCALE = symbol64("k_emissive_scale")
HASH_K_REFRACTIVE_INDEX = symbol64("k_refractive_index")
HASH_K_REFRACTION_AMOUNT = symbol64("k_refraction_amount")
HASH_K_BAKE_EMISSIVE_INTENSITY = symbol64("k_bake_emissive_intensity")
HASH_EMISSIVE_INTENSITY = {L: symbol64(f"layer{L}_emissive_intensity") for L in range(MAX_LAYER)}
HASH_EMISSIVE_TINT = {L: symbol64(f"layer{L}_emissive_tint_color") for L in range(MAX_LAYER)}
HASH_OPACITY_TINT = {L: symbol64(f"layer{L}_opacity_tint_color") for L in range(MAX_LAYER)}
HASH_EMISSIVE_MAP_UOFFSET = {L: symbol64(f"layer{L}_emissive_map_uoffset") for L in range(MAX_LAYER)}
HASH_EMISSIVE_MAP_VOFFSET = {L: symbol64(f"layer{L}_emissive_map_voffset") for L in range(MAX_LAYER)}


# --- name table -------------------------------------------------------------

_NAME_TABLE_CACHE: dict[int, dict[int, str]] = {}


def build_name_table(max_layer: int = MAX_LAYER) -> dict[int, str]:
    """hash -> authored parameter name.

    Every entry is a *candidate preimage generated from the authoring source*;
    because the key IS symbol64(name), any hit is a verified preimage by
    construction.  Cached per `max_layer` (the build costs ~0.3 s).
    """
    cached = _NAME_TABLE_CACHE.get(max_layer)
    if cached is not None:
        return cached

    table: dict[int, str] = {}

    def add(name: str) -> None:
        table.setdefault(symbol64(name), name)

    for n in GLOBAL_PARAMS:
        add(n)
        for suf in SUFFIXED:
            add(f"{n}{suf}")
    for group, members in GROUP_PARAMS.items():
        for n in members:
            add(f"{group}_{n}")
            for suf in SUFFIXED:
                add(f"{group}_{n}{suf}")
    for L in range(max_layer):
        for n in LAYER_PARAMS:
            add(f"layer{L}_{n}")
            for suf in SUFFIXED:
                add(f"layer{L}_{n}{suf}")
    for n in AUX_INPUT_NAMES:
        add(n)

    _NAME_TABLE_CACHE[max_layer] = table
    return table


def resolve_name(name_hash: int) -> str | None:
    """Cracked authored name for a CSymbol64 hash, or None if not recovered."""
    return build_name_table().get(name_hash)


def _arity(name: str) -> int:
    """Word count for a resolved parameter name (strips a layerN_ prefix)."""
    base = name
    if base.startswith("layer") and "_" in base:
        head, _, rest = base.partition("_")
        if head[5:].isdigit():
            base = rest
    for suf in SUFFIXED:
        if base.endswith(suf):
            return 1
    return PARAM_ARITY.get(base, PARAM_ARITY.get(name, 1))


# --- binary helpers ---------------------------------------------------------

def _u16(d: bytes, o: int) -> int:
    return struct.unpack_from("<H", d, o)[0]


def _u32(d: bytes, o: int) -> int:
    return struct.unpack_from("<I", d, o)[0]


def _u64(d: bytes, o: int) -> int:
    return struct.unpack_from("<Q", d, o)[0]


def _f32(d: bytes, o: int) -> float:
    return struct.unpack_from("<f", d, o)[0]


def _f32_from_u32(word: int) -> float:
    return struct.unpack("<f", struct.pack("<I", word))[0]


def flag_names(flags: int) -> list[str]:
    return [k for k, v in EFLAGS.items() if flags & v]


def parse_material_prop_slots(slice_bytes: bytes) -> tuple[list[float], dict[int, int]]:
    """Return (materialprops words as float32, {property_name_hash -> word index}).

    This is the primitive `parse_material_props` is built on; it keeps the word
    array so multi-word parameters (colours, ranges) can be read.
    Robust to short/malformed slices (returns ([], {}) rather than raising).
    """
    d = slice_bytes
    if len(d) < HEADER_SIZE:
        return [], {}
    n_props = _u64(d, OFF_MATERIALPROPS_IUSED)
    n_propoff = _u64(d, OFF_PROPOFFSETS_IUSED)
    if n_props > MAX_REASONABLE or n_propoff > MAX_REASONABLE:
        return [], {}

    props_off = HEADER_SIZE
    propoff_off = props_off + n_props * 4
    if propoff_off + n_propoff * 16 > len(d):
        return [], {}

    words = [_f32_from_u32(_u32(d, props_off + i * 4)) for i in range(n_props)]
    slots: dict[int, int] = {}
    for i in range(n_propoff):
        e = propoff_off + i * 16
        key_hash = _u64(d, e)
        byteoffset = _u32(d, e + 8)
        # byteoffset is a byte offset into the u32 word array (offset/4 = index);
        # tolerate a raw index encoding as a fallback.
        if byteoffset % 4 == 0 and (byteoffset // 4) < len(words):
            idx = byteoffset // 4
        elif byteoffset < len(words):
            idx = byteoffset
        else:
            continue
        slots[key_hash] = idx
    return words, slots


def parse_material_props(slice_bytes: bytes) -> dict[int, float]:
    """Return {property_name_hash -> float value} from the materialprops table.

    Robust to short/malformed slices (returns {} rather than raising).
    """
    words, slots = parse_material_prop_slots(slice_bytes)
    return {h: words[i] for h, i in slots.items()}


def _read(words: list[float], slots: dict[int, int], name_hash: int, arity: int = 1):
    """Read `arity` consecutive words for a hash; None if the hash is absent."""
    idx = slots.get(name_hash)
    if idx is None:
        return None
    if arity <= 1:
        return float(words[idx])
    end = min(idx + arity, len(words))
    return [float(x) for x in words[idx:end]]


def decode_material_scalars(slice_bytes: bytes) -> dict:
    """Decode SGMaterialData scalars from a material primary slice.

    Returns the durable scalar dict documented in the module docstring. Always
    returns a full dict with safe defaults; never raises on short/garbage input.

    ## What a consumer should read

    * `layers[L]` — per-layer emissive/opacity knobs.  **Pick L from the layer of
      the emissive texture role that was actually routed** (`layer1_emissive_map`
      -> `layers[1]`); the flat `emissive_intensity` key is the legacy
      layer0-wins-unconditionally value and is 12.5x too dim on shipped material
      `0613ef69c99cbbc6`.  A `None` field means the author left it at
      `AUTHORED_DEFAULTS_LAYER[...]`.
    * `Emission Strength = layers[L]["emissive_intensity"] * emissive_scale`
      (`shader-confirmed` in the engine's ubershader).  There is no
      unit-conversion constant; the factor is 1.0.
    * `alpha_threshold` / `emissive_scale` / `refractive_index` are always
      populated: the decoded override when present, otherwise the authored
      default (which parameters fell back is listed in
      `scalar_defaults_applied`).
    """
    defaults = {
        "base_color_factor": [1.0, 1.0, 1.0, 1.0],
        "emissive_color": [0.0, 0.0, 0.0],
        "emissive_intensity": 1.0,
        "alpha": 1.0,
        "blend_mode": 0,
        "double_sided": False,
        "mattype": 0,
        "flags": 0,
        "flag_names": [],
        "materialfx": "",
        "is_emissive": False,
        "named_scalars": {},
        # --- added keys ---
        "layers": [{"index": L, "emissive_intensity": None, "emissive_tint": None,
                    "opacity_tint": None, "uv_offset": None, "uv_offsets": {}}
                   for L in range(N_DECLARED_LAYERS)],
        "emissive_layer_indices": [],
        "emissive_scale": AUTHORED_DEFAULTS_GLOBAL["k_emissive_scale"],
        "alpha_threshold": AUTHORED_DEFAULTS_GLOBAL["k_alpha_threshold"],
        "refractive_index": AUTHORED_DEFAULTS_GLOBAL["k_refractive_index"],
        "mattype_name": MATTYPE_NAMES[0],
        "blend_mode_name": BLENDMODE_NAMES[0],
        "named_scalars_resolved": {},
        "bake_emissive_nonzero": False,
        "scalar_defaults_applied": ["k_emissive_scale", "k_alpha_threshold",
                                    "k_refractive_index"],
    }
    d = slice_bytes
    if len(d) < HEADER_SIZE:
        return defaults

    bakecolor = list(struct.unpack_from("<4f", d, OFF_BAKECOLOR))
    emissive = list(struct.unpack_from("<4f", d, OFF_BAKEEMISSIVECOLOR))
    blendmode = _u16(d, OFF_BLENDMODE)
    mattype = _u16(d, OFF_MATTYPE)
    flags = _u32(d, OFF_FLAGS)
    materialfx = f"{_u64(d, OFF_MATERIALFX):016x}"

    words, slots = parse_material_prop_slots(d)
    props = {h: words[i] for h, i in slots.items()}

    # alpha <- k_alpha materialprop (fallback authored default 1.0)
    alpha = float(props.get(HASH_K_ALPHA, AUTHORED_DEFAULTS_GLOBAL["k_alpha"]))

    # LEGACY flat emissive_intensity: layer0 first, else max over layerN.
    # Kept byte-for-byte compatible; `layers[]` is the correct source.
    emissive_intensity = 1.0
    if HASH_EMISSIVE_INTENSITY[0] in props:
        emissive_intensity = float(props[HASH_EMISSIVE_INTENSITY[0]])
    else:
        layer_vals = [props[h] for h in HASH_EMISSIVE_INTENSITY.values() if h in props]
        if layer_vals:
            emissive_intensity = float(max(layer_vals))

    # --- per-layer records --------------------------------------------------
    n_layers = N_DECLARED_LAYERS
    for L in range(MAX_LAYER - 1, N_DECLARED_LAYERS - 1, -1):
        if (HASH_EMISSIVE_INTENSITY[L] in slots or HASH_EMISSIVE_TINT[L] in slots
                or HASH_OPACITY_TINT[L] in slots):
            n_layers = L + 1
            break

    name_table = build_name_table()
    layers: list[dict] = []
    emissive_layers: list[int] = []
    for L in range(n_layers):
        tint = _read(words, slots, HASH_EMISSIVE_TINT[L], 4)
        otint = _read(words, slots, HASH_OPACITY_TINT[L], 4)
        uoff = _read(words, slots, HASH_EMISSIVE_MAP_UOFFSET[L])
        voff = _read(words, slots, HASH_EMISSIVE_MAP_VOFFSET[L])
        intensity = _read(words, slots, HASH_EMISSIVE_INTENSITY[L])

        # every `layerL_<map>_[uv]offset` present, keyed by map parameter name
        uv_offsets: dict[str, list[float]] = {}
        prefix = f"layer{L}_"
        for h, idx in slots.items():
            nm = name_table.get(h)
            if nm is None or not nm.startswith(prefix):
                continue
            if nm.endswith("_uoffset"):
                uv_offsets.setdefault(nm[len(prefix):-8], [0.0, 0.0])[0] = float(words[idx])
            elif nm.endswith("_voffset"):
                uv_offsets.setdefault(nm[len(prefix):-8], [0.0, 0.0])[1] = float(words[idx])

        rec = {
            "index": L,
            "emissive_intensity": intensity,
            "emissive_tint": tint[:3] if tint else None,
            "opacity_tint": otint[:3] if otint else None,
            # UV offset of THIS layer's emissive map (the only offset the
            # emission chain needs); see uv_offsets for every map on the layer.
            "uv_offset": ([uoff or 0.0, voff or 0.0]
                          if (uoff is not None or voff is not None) else None),
            "uv_offsets": uv_offsets,
        }
        layers.append(rec)
        if (intensity not in (None, 0.0)) or (tint is not None and any(c != 0.0 for c in tint[:3])):
            emissive_layers.append(L)

    # --- global authored knobs ---------------------------------------------
    defaults_applied: list[str] = []

    def _global(name: str, name_hash: int) -> float:
        val = _read(words, slots, name_hash)
        if val is None:
            defaults_applied.append(name)
            return float(AUTHORED_DEFAULTS_GLOBAL[name])
        return float(val)

    emissive_scale = _global("k_emissive_scale", HASH_K_EMISSIVE_SCALE)
    alpha_threshold = _global("k_alpha_threshold", HASH_K_ALPHA_THRESHOLD)
    refractive_index = _global("k_refractive_index", HASH_K_REFRACTIVE_INDEX)

    # --- resolved names -----------------------------------------------------
    resolved: dict[str, object] = {}
    for h, idx in slots.items():
        nm = name_table.get(h)
        if nm is None:
            continue
        n = _arity(nm)
        resolved[nm] = float(words[idx]) if n <= 1 else [
            float(x) for x in words[idx:min(idx + n, len(words))]]

    return {
        "base_color_factor": [float(x) for x in bakecolor],
        "emissive_color": [float(x) for x in emissive[:3]],
        "emissive_intensity": emissive_intensity,
        "alpha": alpha,
        "blend_mode": int(blendmode),
        "double_sided": bool(flags & E_DOUBLE_SIDED),
        "mattype": int(mattype),
        "flags": int(flags),
        "flag_names": flag_names(flags),
        "materialfx": materialfx,
        # `bakeemissivecolor` is the BAKE-TIME emissive and ships (0,0,0) for
        # every genuinely emissive material inspected (`stream-confirmed`), so it
        # must NOT gate emission.  The gate is a non-zero authored per-layer
        # emissive intensity or a non-black authored emissive tint.  The presence
        # of an emissive *map* is texture-role information the caller holds, not
        # this decoder — a caller with a routed emissive map should treat that as
        # emissive too.
        "is_emissive": bool(emissive_layers),
        "bake_emissive_nonzero": any(v != 0.0 for v in emissive[:3]),
        "named_scalars": {f"{k:016x}": float(v) for k, v in props.items()},
        # --- added keys ---
        "layers": layers,
        "emissive_layer_indices": emissive_layers,
        "emissive_scale": emissive_scale,
        "alpha_threshold": alpha_threshold,
        "refractive_index": refractive_index,
        "mattype_name": MATTYPE_NAMES.get(int(mattype), f"unknown_mattype_{int(mattype)}"),
        "blend_mode_name": BLENDMODE_NAMES.get(int(blendmode),
                                               f"unknown_blendmode_{int(blendmode)}"),
        "named_scalars_resolved": resolved,
        "scalar_defaults_applied": defaults_applied,
    }
