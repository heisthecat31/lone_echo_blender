"""Material resolution: shaderset/material -> texture roles -> Principled BSDF spec.

Pure stdlib. Produces the `materials` list embedded in a `.lemesh` manifest; the
Blender addon's material_builder wires these onto a Principled BSDF.

Resolution chain (`name-confirmed` + `stream-confirmed`):
  CGRenderParams.shadersetidx -> scene shaderset table -> CGShaderSetResource
    -> SShaderInputData rows {inputname(CSymbol64), textureassetid(CSymbol64), ...}
    -> CGTextureResource -> DDS  (DXGI format decides colorspace)
  CGRenderParams.materialidx -> CGSceneData.materials -> SGMaterialData
    -> scalar params: bakecolor, bakeemissivecolor, blendmode, EFlags(eDoubleSided),
       materialprops(k_alpha, layerN_emissive_intensity, uv offsets)

For the prototype the shaderset->texture join is read from the proven precomputed
scan TSVs (same inputs the the reference exporter uses); a direct-from-archive
resolver is a later hardening step. The role/colorspace/Principled tables below
are durable RE knowledge and independent of that source.
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
    # ⛔ These ten were previously labelled "tentative (DDS-format inferred)" with
    # INVENTED names — none of them hashed to its own key, and the fakes had been
    # written into corpus/hash_lookup.json as if recovered. Every one is now the
    # cracked exact preimage, verified by `symbol64(name) == key`.
    # Two of the fakes were actively wrong about MEANING, not just spelling:
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
    # Cracked in the same pass but never wired in (findings §3 "The real names",
    # 9 rows each). Both verified: symbol64(name) == key.
    "9dba2dc44433be64": ("layer0_alpha_map", "confirmed"),
    "571b8c6b2599c12a": ("layer0_secondary_emissive_map", "confirmed"),
}

# --- layer-aware role parsing ------------------------------------------------
# Every cracked inputname is `layer{N}_{suffix}` (`name-confirmed`: the sampler
# names the engine's UberMaterial declares are per-layer). Routing is therefore
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
# channels; this pass fixes three more, all `shader-confirmed` against the
# engine's own ubershader:
#   * `layerN_opacity_map` is a float3 TRANSMISSION TINT
#     (`output.color.rgb += background * material.opacity`), not alpha. It is out
#     of the alpha path and into `transmission`.
#   * `layerN_composite_specular` is specular data
#     (`specintensity = .w`, `specalbedo = .xyz * .w`) — it was in ROUGHNESS_ROLES
#     and, before that, in BASE_COLOR_ROLES. Neither is right; it gets its own
#     `specular` channel.
#   * `layerN_alpha_map` is the scalar alpha multiplier of the alpha chain
#     (`alpha = ... * alphamap * k_alpha`) and drives the `alpha` channel.
# `layerN_specular_map` was FIRST in BASE_COLOR_ROLES, which made a specular map
# the Base Color of any material carrying one. Corpus evidence (51 fixture
# packages / 100 unique materials, `stream-confirmed`): 8 materials carry
# `layer0_specular_map` and *none* of them carries any diffuse/albedo, so the
# ordering never actually resolved a conflict — the entry was simply routing
# specular data into Base Color. It now routes to `specular` only.
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
    # ★ SKIN. `layerN_thickness_mask` is the second axis of RAD's pre-integrated
    # skin scatter LUT: `Skin_Diffuse` samples
    # `scattermap[float2(dot(n_c, L) * 0.5 + 0.5, thickness)]` per channel with
    # three bent normals weighted (0.1747936, 0.3413353, 0.4868717) for R/G/B
    # (`shader-confirmed`, the engine's `Skin_Diffuse`, dispatched for
    # `k_brdf == kSkinBRDF (4)`). It is authored under the `enable_skin_` group
    # of the material asset schema (thickness dflt 0.1,
    # normal_softness dflt 1.0) and you only author one for BARE SKIN, which makes
    # its mere presence the detector.
    "skin_thickness":     ("thickness_mask",),
}

# Channels with no faithful Principled target — carried for audit / A3 to decide.
# `blend_mask` is NOT a Principled input: it is the per-layer compositing weight,
# consumed by `layer_blend_for()` below and applied by material_builder to the
# other channels of the same layer.
AUDIT_ONLY_CHANNELS = frozenset({"translucency", "blend_mask", "flowmap",
                                 "secondary_emission"})

# role_key -> confidence (derived from INPUTNAME_ROLE)
INPUTNAME_ROLE_CONF = {v[0]: v[1] for v in INPUTNAME_ROLE.values()}
KNOWN_ROLES = frozenset(INPUTNAME_ROLE_CONF)

# ---------------------------------------------------------------------------
# E3 — closing the `unrouted_roles` residue
# ---------------------------------------------------------------------------
# Measured first, then fixed. `export-validated`, station_front `942c829457a04a62`
# v2 sidecar: **all 62 `unrouted_roles` bindings are `unknown_s{slot}`** — not one
# is a cracked role name. Same on the mesh corpus (archive `0703fd2acd5803e9`,
# `exports/fixtures_mat3`, 100 unique materials): the single unrouted entry is
# `unknown_s23`. So the residue was never a routing-table gap; it was an
# inputname-RESOLUTION gap — 25 cracked hashes against a name space that is a
# GRID (`layer{0..3}` x sampler), of which the crackers had happened to observe
# only some cells.
#
# `name-confirmed`, RAD build `2799580733489822` (the engine's own UberMaterial
# declaration): the shader input names are the material's property tree
# flattened with `_`. The uber material instantiates the SAME
# `UberMaterialLayer` group exactly four times —
#   `:layer0 := UberMaterialLayer  … :layer3 := UberMaterialLayer`
# — and every suffix already cracked at one layer is a declared member of that
# group (`albedo_map`, `alpha_map`, `emissive_map`, `secondary_emissive_map`,
# `specular_map`, `blend_mask`, `normal_map`, `opacity_map`,
# `back_lighting_map`, `composite_normals`, `composite_diffuse`,
# `composite_specular`, `composite_components`, `composite_data0`,
# `flowmap_map`).
# So `layer2_composite_diffuse` is exactly as real a name as the already-cracked
# `layer0_composite_diffuse`; only its hash had never been written down.
#
# ⚠ WHY THIS IS NOT A FABRICATION. The mistake this table exists to prevent was
# inventing a NAME for an OBSERVED hash. This is
# the inverse, and it is the repo's own already-blessed pattern — exactly what
# `material_scalars.build_name_table()` does for materialprop names ("every entry
# is a candidate preimage generated from the authoring schema; because the key IS
# symbol64(name), any hit is a verified preimage by construction"). We forward-hash
# a *`name-confirmed`* name and let the shipped bytes decide. `symbol64(name) ==
# key` holds by definition, so `test_materials.test_every_role_name_hashes_to_its_own_key`'s
# invariant can never be violated; and an entry for a name the cook never emits is
# simply inert — no row will ever carry that hash. Checked against
# `hash_lookup.json` (13,958 names) and the CSymbol64 literal pools of the two
# shipped game binaries (11,391 + 4,630 entries): **zero** of the generated
# hashes collides with a different name.
#
# ★ ROUTING IS STILL GATED BY THE CURATED TABLE. A resolved name only reaches a
# Principled channel if its suffix is in `CHANNEL_ROLE_SUFFIXES`, so widening the
# NAME space cannot mis-route: an unexpected name lands in `unrouted_roles` with
# a classification, never in `channels`.
UBERMATERIAL_LAYER_COUNT = 4          # layer0..layer3 of `UberMaterial`

# Suffixes whose per-layer name is corroborated by at least one cracked preimage
# above AND is a declared `UberMaterialLayer` member.
CONFIRMED_LAYER_SUFFIXES: tuple[str, ...] = tuple(sorted(
    {split_role(r)[1] for r in INPUTNAME_ROLE_CONF}))

# Real per-layer samplers that no Principled socket can consume. Kept apart from
# CONFIRMED_LAYER_SUFFIXES so "we can name it" and "we route it" stay separable.
UNROUTABLE_LAYER_SUFFIXES: tuple[str, ...] = ("composite_data0",)


def _build_layer_grid() -> dict[str, tuple[str, str]]:
    """`layer{0..3}_{sampler}` -> its own CSymbol64, for every sampler above."""
    grid: dict[str, tuple[str, str]] = {}
    for layer in range(UBERMATERIAL_LAYER_COUNT):
        for suffix in CONFIRMED_LAYER_SUFFIXES + UNROUTABLE_LAYER_SUFFIXES:
            name = f"layer{layer}_{suffix}"
            grid["%016x" % symbol64(name)] = (name, "forward-hashed")
    return grid


GENERATED_LAYER_INPUTNAME: dict[str, tuple[str, str]] = _build_layer_grid()

# Cracked preimages that are REAL inputnames with no Blender equivalent. They are
# deliberately kept OUT of `INPUTNAME_ROLE` / `KNOWN_ROLES`: that set means "roles
# the router routes", and `test_material_routing.test_fixture_corpus_routes_every_known_role`
# asserts a KNOWN_ROLE is never left unrouted. Resolving them here still upgrades
# the audit line from `unknown_s{slot}` to a name plus a reason.
UNROUTABLE_INPUTNAME: dict[str, tuple[str, str]] = {
    # MaterialPOMProperties.height_map, flattened under the material-level `pom`
    # group -> `pom_height_map` (`name-confirmed`:
    # `:pom := ( MaterialPOMProperties )`; the same `pom_` flattening shows
    # up shader-side as `pom_parallax_scale_` / `pom_mip_fade_start_`).
    # NOT per-layer: it is one height map for
    # the whole material. symbol64("pom_height_map") == 602e82b525713c1c — the
    # hash docs/MATERIALS.md §5 lists as uncracked.
    "602e82b525713c1c": ("pom_height_map", "confirmed"),
}

# Every inputname hash we can put a name to. `INPUTNAME_ROLE` wins on any key it
# defines, so an observed+cracked entry always beats a generated one.
ROLE_BY_INPUTNAME: dict[str, tuple[str, str]] = {
    **GENERATED_LAYER_INPUTNAME, **UNROUTABLE_INPUTNAME, **INPUTNAME_ROLE}

# Confidence for names that are not in INPUTNAME_ROLE (which stays frozen so the
# legacy flat role lists and their tests do not move).
ROLE_CONF_EXTRA: dict[str, str] = {
    name: conf for name, conf in
    list(GENERATED_LAYER_INPUTNAME.values()) + list(UNROUTABLE_INPUTNAME.values())}


def role_confidence(role_key: str) -> str:
    """`confirmed` (cracked from an observed hash) | `forward-hashed` | `tentative`."""
    conf = INPUTNAME_ROLE_CONF.get(role_key)
    if conf is None:
        conf = ROLE_CONF_EXTRA.get(role_key, "tentative")
    return conf


def _authored_name_table() -> dict[int, str]:
    """`material_scalars.build_name_table()`, imported lazily and cached there.

    The repo's existing generated authored-parameter table (14,995 names built
    from the engine's UberMaterial declaration). It already carries the whole
    sampler grid —
    `layer2_composite_diffuse`, `layer0_composite_data0`, `pom_height_map` — it
    had simply never been wired into *inputname* resolution, only into
    *materialprop* resolution. Same verified-by-construction guarantee.
    """
    try:
        from .material_scalars import build_name_table
    except ImportError:                       # pragma: no cover
        return {}
    return build_name_table()


def role_for_inputname(inputname_hash: str, slot=None,
                       names: dict[int, str] | None = None) -> str:
    """inputname CSymbol64 hex -> role key, in provenance order.

    1. `INPUTNAME_ROLE`             cracked from an observed hash  (`confirmed`)
    2. the forward-hashed sampler grid / `UNROUTABLE_INPUTNAME`  (`forward-hashed`)
    3. `names` — the caller's harvested `hash_lookup.json`
    4. `material_scalars.build_name_table()` — the generated authored-name table
    5. `unknown_s{slot}` — the scan SLOT, which is not a name
    """
    ihex = (inputname_hash or "").lower().zfill(16)
    entry = ROLE_BY_INPUTNAME.get(ihex)
    if entry is not None:
        return entry[0]
    try:
        ihash = int(ihex, 16)
    except ValueError:
        return f"unknown_s{'x' if slot is None else slot}"
    if names:
        role = names.get(ihash)
        if role:
            return role
    role = _authored_name_table().get(ihash)
    if role:
        return role
    return f"unknown_s{'x' if slot is None else slot}"


# Why a named role stays out of `channels` — so `unrouted_roles` is explainable
# instead of merely carried. `shader-confirmed` reason per row.
UNROUTED_ROLE_REASONS: dict[str, tuple[str, str]] = {
    "composite_data0": (
        "deliberately unrouted",
        "the SECOND SPECULAR LOBE of the composite path (`shader-confirmed`): "
        "`specintensity[1] = k_composite_data0[i].w` and `specalbedo[1] = "
        ".xyz * .w` — the exact packing `composite_specular` uses for lobe [0], "
        "one index up. Its roughness is `k_composite_components[i].w` (vs `.x` "
        "for lobe 0), and without it the engine sets `specalbedo[1] = "
        "specalbedo[0]`. RAD's BRDF is TWO-lobe; Blender's Principled BSDF has "
        "one specular lobe, so there is no faithful target. 5th "
        "`compositesampler` of `UberMaterialLayer`, default `common_black`. "
        "⚠ gated by the `use_composite_` permutation bit, which is NOT on disk."),
    "pom_height_map": (
        "deliberately unrouted",
        "parallax-occlusion height field (`MaterialPOMProperties.height_map`). "
        "It displaces the UV inside the pixel shader (`ApplyParallaxMapping`) "
        "behind the "
        "`pom.enable_` compile option. Blender's Principled BSDF has no parallax "
        "input; the only faithful target is real displacement geometry."),
}


def explain_unrouted(role_key: str, texture_name: str | None = None) -> dict:
    """Classify one `unrouted_roles` entry: routable / deliberate / unresolved.

    `texture_name` is the exact RDEF name of the bound texture when the extractor
    recovered one. It is what separates two states that used to read identically:

      * `rdef_bind{n}` on a `generated_composite_*` atlas — the cook baked this
        map, no array declares it, and `composite_roles_from_format` REFUSED it.
        The role is recoverable in principle; the rule declined to guess.
      * `rdef_bind{n}` on an AUTHORED name (`liv_basesuit_fx_clr`) — the artist
        named this texture and no array in the corpus declares its role. ⛔ The
        name suffix does NOT determine the role: `_clr` maps to **21 distinct
        roles** across 1,136 corpus observations (`layer0_albedo_map` 433,
        `layer1_albedo_map` 99, `layer2_emissive_map` 82, `layer0_alpha_map` 78,
        …), and `_fx_clr` specifically splits 2 albedo / 1 emissive. Measured
        by joining the corpus RDEF-name harvest to the corpus role index.

    ⚠ The name is EVIDENCE, never a route. Nothing here promotes a bind into a
    channel — a `rdef_bind{n}` stays unrouted whatever it is called.
    """
    def _named(rec: dict) -> dict:
        if texture_name:
            rec["texture_name"] = texture_name
            rec["texture_name_is_generated"] = bool(
                texture_name.startswith(COMPOSITE_NAME_PREFIX))
        return rec

    if (role_key or "").startswith("unknown_s"):
        return _named({"role": role_key, "classification": "unresolved",
                       "reason": "inputname CSymbol64 has no known preimage; "
                                 "`unknown_s{slot}` is the scan slot, not a name",
                       "named": False})
    if (role_key or "").startswith("rdef_bind"):
        if texture_name and texture_name.startswith(COMPOSITE_NAME_PREFIX):
            return _named({
                "role": role_key, "classification": "unresolved",
                "reason": "a `generated_composite_*` atlas that no array "
                          "declares and `composite_roles_from_format` REFUSED — "
                          "replay the rule over this shaderset's pending binds "
                          "for the code (`many_unresolved_resolution_groups` / "
                          "`format_not_unique_in_group` / …). The role is "
                          "recoverable in principle; the rule declined to guess "
                          "rather than emit a wrong layer.",
                "named": True})
        return _named({
            "role": role_key, "classification": "unresolved",
            "reason": "RDEF named the TEXTURE, nothing named its ROLE. ⛔ The "
                      "name suffix is not a role: `_clr` maps to 21 distinct "
                      "roles corpus-wide, so no suffix rule can route it.",
            "named": bool(texture_name)})
    _layer, suffix = split_role(role_key)
    known = UNROUTED_ROLE_REASONS.get(suffix)
    if known is not None:
        return _named({"role": role_key, "classification": known[0],
                       "reason": known[1], "named": True})
    if suffix in {s for sufs in CHANNEL_ROLE_SUFFIXES.values() for s in sufs}:
        return _named({"role": role_key, "classification": "routable",
                       "reason": "a higher-priority suffix already claimed this "
                                 "channel on the same layer",
                       "named": True})
    return _named({"role": role_key, "classification": "unresolved",
                   "reason": "named, but no channel rule covers this suffix",
                   "named": True})


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
# ALL `shader-confirmed` against the engine's own layer compositing. Nothing
# here is inferred except where the comment says so.
#
#   BlendLayers()   the composite loop
#     output = layers[0]                        <- layer 0 is the BASE
#     for (i = 1; i < num_layers_; ++i)         <- ASCENDING, each layer is
#                                                  blended onto the running
#                                                  accumulation of 0..i-1
#     fade   = max(blend_fade[i] * blend_fade_scale_offset_map[i].x, 0.01)
#     scale  = blend_mask_scale[i]  * blend_fade_scale_offset_map[i].y
#     offset = blend_mask_offset[i] + blend_fade_scale_offset_map[i].z
#     b      = ComputeBlend(scale, offset, blend_mask[i].x, ...)
#     output = BlendLayer(output, layers[i], b, blend_mode[i], layeralphas)
#   (the normal path repeats the identical arithmetic)
#
#   ComputeBlend()
#     _scale  = scale  * blend_scale_regions_map[i].x
#     _offset = offset * (1.0 - blend_offset_regions_map[i].x)
#     _mask   = saturate(mask * _scale + _offset)          <- `mask` is `.x` == RED
#     use_blend_mask_as_height_ off  =>  _height = baseheight, _blend = _mask
#     return BlendAmount(vertblend, _height, fade, 0.0) * _blend
#
#   BlendAmount()   saturate(((vertblend - height) / fade) - 0)
#
#   BlendLayer()    per-property, each with its own alpha:
#     diffusealbedo x diff_albedo_blend_alpha   specalbedo    x spec_albedo_blend_alpha
#     lighting      x lighting_blend_alpha      sqrtroughness x roughness_blend_alpha
#     alpha         x transparency_blend_alpha  opacity       (bare mask)
#     normal        x normal_blend_alpha
#   and EMISSIVE rides in `lighting` (`output.lighting = params.layer *
#   (specoutput.xyz + emmisiveoutput.xyz) * k_emissive_scale_`), so an upper
#   layer's emissive map is gated by that layer's blend amount.
#
#   BlendValue()    the operator selected by `layerN_blend_mode`:
#     0  eBlendNone         base
#     1  eBlendAdditive     base + layer * m           <- an ADD
#     6  eBlendTransparent  (1 - m) * base + m * layer <- a LERP  (authored default)
#     7  eBlendLinearDodge  base + layer * m
#     10 eBlendDetailOverride  lerp, and forces vertblend = 1
#
# The neutral-default self-check: every map that participates defaults to the
# value that makes its term vanish — `blend_mask` = `common_white` (1),
# `blend_fade_scale_offset_map` = `common_yellow` (1, 1, 0),
# `blend_scale_regions_map` = `common_white_array` (1),
# `blend_offset_regions_map` = `common_black_array` (0). So with authored
# defaults the whole thing collapses to
#
#     blend_amount = saturate(vertblend / fade) * saturate(mask.R * scale + offset)
#
# ⚠ Both region maps are `Sampler2DWeightedMask` = "a texture array where each
# slice has an ANIMATED weight and gets additively flattened before rendering"
# (`name-confirmed`, the engine's own material attribute schema), and
# `blend_mask_offset` itself is `-animatable true -softmin -1.0 -softmax 1.0`
# (`name-confirmed`, that schema's UI declaration). A shipped
# `layerN_blend_mask_offset = -1.0` therefore means "this layer is parked at its
# animated OFF extreme"; it is a runtime state we cannot reproduce, not a bug.
# The decode reports it (`suppressed_at_rest`) rather than editorialising it.

# `k_blend_mask[i].x` — the engine's layer compositing (`shader-confirmed`).
BLEND_MASK_COMPONENT = "R"
# The engine's own layer-blend operator enum (`name-confirmed`). NOT the same
# enum as `BLENDMODE_NAMES` above, which is the material-level `EBlendMode`
# (18 values, RT blend equation).
LAYER_BLEND_MODE_NAMES = {
    0: "eBlendNone", 1: "eBlendAdditive", 2: "eBlendSubtractive",
    3: "eBlendMultiply", 4: "eBlendDarken", 5: "eBlendLighten",
    6: "eBlendTransparent", 7: "eBlendLinearDodge", 8: "eBlendLinearBurn",
    9: "eBlendOverlay", 10: "eBlendDetailOverride",
}
LAYER_BLEND_LERP_MODES = frozenset({6, 10})     # (1-m)*base + m*layer
LAYER_BLEND_ADD_MODES = frozenset({1, 7})       # base + layer*m

# Authored defaults, the engine's UberMaterial declaration (`name-confirmed`).
DEFAULT_BLEND_MASK_SCALE = 1.0
DEFAULT_BLEND_MASK_OFFSET = 0.0
DEFAULT_BLEND_FADE = 1.0
DEFAULT_LAYER_BLEND_MODE = 6          # `:blend_mode := 6` = eBlendTransparent
DEFAULT_BLEND_ALPHA = 1.0             # every `*_blend_alpha`
MIN_BLEND_FADE = 0.01                 # `max(..., 0.01f)`; the schema authors
                                      # the parameter itself `-min 0.01`
# `blend_mask` sampler default is `common_white` -> mask.R = 1.0.
DEFAULT_BLEND_MASK_VALUE = 1.0
# `enable_height_blend_` is authored false for a layer AND the height maps
# default to `common_black_alpha` / `common_gray`, which make `height` 0 either
# way — so the height term is 0 for every material in the corpus (no material
# binds `blend_height` or `detail_height_map`).
DEFAULT_BLEND_HEIGHT = 0.0

# `:fresnel := ( WidgetReal :value = 0.010000 )` (`name-confirmed`, the
# UberMaterial declaration). It is `params.specintensity[0]`
# (`shader-confirmed`) and therefore the scalar that turns a
# `layerN_specular_map` texel into F0. Corpus check (`stream-confirmed`, all 100
# unique materials of exports/fixtures_mat3): NO shipped material overrides
# `fresnel`, `specular_tint_color`, `specular_gloss` or `enable_specular`, so
# every `specular_map` material in the corpus runs on this authored default.
SPEC_MAP_FRESNEL_DEFAULT = 0.01
# `:specular_gloss := ( WidgetReal :value = 1.000000 )`. At 1.0 the engine's
# `lerp(specalbedo * albedo, specalbedo, gloss)` is a no-op.
SPEC_MAP_GLOSS_DEFAULT = 1.0

# Which `layerN_*_blend_alpha` scales the mask for each of our channel names
# (`shader-confirmed`, the `BlendLayer` term named beside each).
CHANNEL_BLEND_ALPHA_PARAM = {
    "base_color":         "diff_albedo_blend_alpha",     # diffusealbedo
    "specular":           "spec_albedo_blend_alpha",     # specalbedo
    "roughness":          "roughness_blend_alpha",       # sqrtroughness
    "emission":           "lighting_blend_alpha",        # lighting
    "secondary_emission": "lighting_blend_alpha",        # ditto (rides lighting)
    "translucency":       "backlighting_blend_alpha",    # backlighting
    "alpha":              "transparency_blend_alpha",    # alpha
    "normal":             "normal_blend_alpha",          # normal
    "transmission":       None,                          # opacity, bare mask
    "flowmap":            None,                          # flowmap, bare mask
    "blend_mask":         None,                          # the mask itself
}

# `vertblend` = `blend[i-1]`, and `blend` is `LayerBlendWeights()` =
# `psin.blend` = the `float4 blend : COLOR1` vertex stream
# (`shader-confirmed`; `float4 blend : COLOR1` is the engine's own vertex-shader
# input declaration). Our exporter names the
# second eColor set `color1` (`le_mesh/vertex_format.py::attribute_key`), so
# layer i reads component (i-1) of `color1`. `inferred` linkage: color set order
# == COLOR0/COLOR1 register order.
VERTEX_BLEND_ATTRIBUTE = "color1"
VERTEX_BLEND_COMPONENTS = ("R", "G", "B")


def _saturate(v: float) -> float:
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else float(v))


def blend_amount_bounds(mask_scale: float, mask_offset: float,
                        has_mask_texture: bool) -> tuple[float, float]:
    """(min, max) of `saturate(mask.R * scale + offset)` over `mask.R in [0,1]`.

    `blend_mask_scale` is authored `-min 0.0` so the expression is
    monotone non-decreasing in the mask, and the bounds are just its endpoints.
    Without a bound mask texture the sampler returns `common_white` == 1.0, so
    the two bounds collapse onto the single constant.
    """
    hi = _saturate(mask_scale * 1.0 + mask_offset)
    lo = hi if not has_mask_texture else _saturate(mask_scale * 0.0 + mask_offset)
    return lo, hi


# --- DXGI format -> colorspace ----------------------------------------------
# Standard DXGI enum values. An `_SRGB` format is decoded to linear BY THE
# SAMPLER, so Blender must apply the same decode -> the format is authoritative.
# (Corpus check, `stream-confirmed`: no normal/components/blend-mask texture in
# the 51 fixture packages is an _SRGB format, and every diffuse / specular /
# emissive / opacity texture that IS _SRGB is genuinely colour data.)
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
# Formats that carry a full, independently-addressable alpha PLANE — i.e. reading
# `.a` returns authored data rather than a hardware constant. This is
# `ALPHA_CAPABLE_DXGI` minus BC1: DXGI 70/71/72 encode alpha as *one bit per
# texel*, selected by index 3 of a `c0 <= c1` block, and nothing else.
#
# ⚠ `stream-confirmed`, archive `0703fd2acd5803e9`: across all 17 shipped BC1
# textures bound to a colour-ish role in `exports/fixtures_mat3` — 21,626,496
# mip-0 texels fully block-decoded — **zero** texels select index 3. Every BC1
# texture in the corpus therefore samples alpha == 1.0 everywhere, even though
# 2.6 %-76 % of its blocks are in `c0 <= c1` mode (that mode is a compressor
# quality choice, not authored alpha). A consumer that wires a BC1 `.a` to an
# opacity socket is wiring a constant. See docs/MATERIALS.md.
ALPHA_PLANE_DXGI = frozenset(ALPHA_CAPABLE_DXGI - PUNCHTHROUGH_ALPHA_DXGI)
# Single-channel formats: a scalar map's value is unambiguously in R. BC4 (79-81)
# stores ONE 8-byte block plane per 4x4 and the SRV returns (r, 0, 0, 1) — there
# is no alpha data in the file at all (`stream-confirmed`: mip 0 of
# `bb897558f047959b.dds` is exactly blocks*8 bytes).
SINGLE_CHANNEL_DXGI = frozenset({54, 55, 56, 61, 62, 63, 79, 80, 81})

# --- which texture COMPONENT carries each channel's signal -------------------
# Every entry is the literal swizzle the engine writes, `shader-confirmed`
# against the engine's own ubershader, with the expression quoted beside it.
# This table exists so the consumer never has to infer a component from the DXGI
# format — the format is a red herring, the shader is the authority.
#
# ★ The load-bearing row is `alpha_map`. `layerN_alpha_map` ships in FOUR formats
#   in archive `0703fd2acd5803e9` (BC1_UNORM 71, BC1_UNORM_SRGB 72,
#   BC3_UNORM_SRGB 78, BC4_UNORM 80) and the engine reads **`.x` == RED** from
#   every one of them:  `params.alphamap = k_alpha_map[i].x;`.
#   It is the ONLY map term of the alpha chain that is not `.a`, and
#   the only one the `use_output_alpha_` branch does not wrap in
#   `QuickSRGBToLinear` — consistent with it arriving already linearised through
#   an `_SRGB` sampler view.
ROLE_COMPONENT: dict[str, str] = {
    "composite_diffuse":      "RGB",   # layers[i].diffusealbedo = ....xyz
    "albedo_map":             "RGB",   # -> albedo.xyz
    "composite_normals":      "RGB",   # tangent-space normal (RG when BC5)
    "normal_map":             "RGB",
    "composite_components":   "R",     # sqrtroughness[0] = ....x  (AO = .y)
    "composite_specular":     "RGB",   # specalbedo = .xyz * .w
    "specular_map":           "RGB",   # layers[i].specalbedo[0] *= ....xyz
    "alpha_map":              "R",     # params.alphamap = k_alpha_map[i].x
    "opacity_map":            "RGB",   # float3 transmission tint
    "emissive_map":           "RGB",   # emissive.xyz (.a is an alpha term)
    "secondary_emissive_map": "RGB",   # emissivemap2
    "back_lighting_map":      "RGB",   # k_back_lighting_map[i].xyz
    "blend_mask":             "R",     # ComputeBlend(..., k_blend_mask[i].x)
    "flowmap_map":            "RGB",   # normalize(k_flow_map[i].xyz * 2 - 1)
    # `material.thickness` feeds `Skin_Diffuse(..., thickness)` as the LUT's V
    # axis; it is a scalar, and every scalar map in this table reads `.x`.
    "thickness_mask":         "R",
}
# `layers[i].alpha = k_composite_diffuse[i].w * albedovertex.w` — the one place
# the engine really does read an alpha plane as opacity.
BASE_COLOR_ALPHA_COMPONENT = "A"


def component_for(role_key: str, dxgi: int | None = None) -> str | None:
    """Which component(s) of the bound texture feed this role's channel.

    'R' | 'RG' | 'RGB' | 'A', or None when the role is unknown.
    `shader-confirmed` per `ROLE_COMPONENT`; the only format-dependent case is a
    BC5 normal, which
    stores XY and has Z reconstructed.
    """
    _layer, suffix = split_role(role_key)
    comp = ROLE_COMPONENT.get(suffix)
    if comp == "RGB" and dxgi in BC5_DXGI:
        return "RG"
    return comp

# --- Blender image alpha_mode hints -----------------------------------------
ALPHA_MODE_CHANNEL_PACKED = "CHANNEL_PACKED"   # alpha is data we read separately
ALPHA_MODE_STRAIGHT = "STRAIGHT"               # genuine unassociated alpha
ALPHA_MODE_NONE = "NONE"                       # alpha must be ignored entirely
# Suffixes whose alpha channel we read as an independent signal. Every one is a
# term of the engine's alpha chain (`shader-confirmed`) except
# `composite_specular`, whose `.w` is specintensity
# (`shader-confirmed`). For all of them Blender must NOT
# un-premultiply the RGB, i.e. alpha_mode = CHANNEL_PACKED.
CHANNEL_PACKED_SUFFIXES = frozenset({
    "composite_diffuse", "albedo_map", "emissive_map", "secondary_emissive_map",
    "alpha_map", "composite_specular",
    # ★ Added with the two-lobe fix. Both carry a real signal in `.w`
    # (`shader-confirmed`): `sqrtroughness[1] = k_composite_components[i].w`
    # and `specintensity[1] = k_composite_data0[i].w`. Declaring
    # them CHANNEL_PACKED is the honest label — "the alpha is data, do not
    # un-premultiply the RGB". It cannot change any RGB value (only `'STRAIGHT'`
    # does that) and, measured, it does not change the ALPHA either: on Blender
    # 5.1.1 an Image Texture node's `Alpha` output returns the file's alpha plane
    # under `'NONE'` and `'CHANNEL_PACKED'` alike (`engine-confirmed`, a 1-px
    # Cycles render of `Alpha -> Emission` on `f39d4dde40acc404.dds` gave the
    # identical 0.542/0.4949/0.5395 under both). So manifests written before this
    # change, which carry `alpha_mode: "NONE"` for these two, still decode the
    # second lobe correctly.
    "composite_components", "composite_data0",
})
# ⛔ ALPHA_MODE_STRAIGHT is never emitted automatically. Choosing it needs the
# `premultiplied_alpha_` / `use_output_alpha_` *shader permutation* bits, which
# are NOT on disk (findings §2c). The constant exists so A3 can set it manually.


# --- material type / blend mode enums (`name-only`) --------------------------
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

# docs/MATERIALS.md §4 mapping table.
ALPHA_TESTED_MATTYPES = frozenset({9})                     # eMTAlphaTested
TRANSPARENT_MATTYPES = frozenset({2, 3, 4, 11, 16})        # Forward/LowRes/Solid/Refraction/PostAA
# ★ `eMTSkirt` / `eBlendSkirt` is the DECAL pass, not an opaque one. It used to be
# listed as opaque ("no Blender equivalent; import opaque and tag",
# docs/MATERIALS.md §4) and that suppressed the alpha chain, which is
# what rendered Jack's shoulder/thigh patches as solid BLACK CARDS —
# docs/MATERIALS.md. `eSkirts` is its own render pass — pass 8, `name-only` —
# and every one of the five
# skirt materials in this corpus binds a BC3_SRGB composite diffuse whose alpha
# plane is a bimodal cut-out mask (`measured`).
SKIRT_MATTYPES = frozenset({10})                           # eMTSkirt   -- decal pass
SKIRT_BLENDMODES = frozenset({10})                         # eBlendSkirt
OPAQUE_BLENDMODES = frozenset({0, 16})                     # Opaque / NoColorWrites
# EEVEE has no additive blend — approximating it is lossy and must be flagged.
ADDITIVE_BLENDMODES = frozenset({1, 8})                    # eBlendAdditive / eBlendLinearDodge
COVERAGE_BLENDMODES = frozenset({15})                      # eBlendAlphaToCoverage

RENDER_MODE_OPAQUE = "OPAQUE"
RENDER_MODE_CLIP = "CLIP"
RENDER_MODE_BLEND = "BLEND"


def render_mode_for(mattype: int, blend_mode: int) -> tuple[str, bool]:
    """(render_mode, alpha_blend_lossy) from the two on-disk u16 fields.

    `mattype` picks the pass, `blend_mode` picks the equation
    (`name-confirmed`; observed pairing `stream-confirmed`). Shader-permutation
    bits (clip vs dither, alpha-to-coverage, premultiplied) are NOT on disk and
    are deliberately not guessed here — CLIP means "the engine cuts out", not
    "use Blender's legacy CLIP blend_method" (which is a dead alias on 4.2+).

    ⚠ `eBlendSkirt`'s framebuffer equation is NOT on disk (the engine's own
    declarations give the enum NAME only, and its blend-operator shader is not
    in the corpus), so BLEND is `inferred`
    from two measured facts: RAD pairs a *cut-out* with `eBlendOpaque` (all 8
    `eMTAlphaTested` materials in the corpus do), and the skirt pass instead
    carries a blend mode of its own while binding a coverage-alpha diffuse.
    What is NOT inferred is that the alpha must be READ: it is a decal sheet.
    """
    lossy = blend_mode in ADDITIVE_BLENDMODES
    if lossy:
        return RENDER_MODE_BLEND, True
    if mattype in ALPHA_TESTED_MATTYPES or blend_mode in COVERAGE_BLENDMODES:
        return RENDER_MODE_CLIP, False
    if mattype in SKIRT_MATTYPES or blend_mode in SKIRT_BLENDMODES:
        return RENDER_MODE_BLEND, False
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
    conf = role_confidence(role_key)
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
    # Which component carries the signal — `shader-confirmed`, see ROLE_COMPONENT.
    # OPTIONAL for consumers: a manifest written before this key existed simply
    # has no "component" and the reader falls back to its own inference.
    component = component_for(role_key, dxgi)
    if component is not None:
        ch["component"] = component
    # Does `.a` of this texture hold authored data, or a hardware constant?
    # BC1 = 1 bit, measured unused corpus-wide; BC4 = no alpha data in the file.
    ch["alpha_plane"] = bool(dxgi in ALPHA_PLANE_DXGI)
    # --- per-suffix packing facts (all `shader-confirmed`) -------------------
    if suffix == "composite_components":
        # sqrtroughness[0] = .x ; ambientocclusion = .y ; brdfblends.y = .z ;
        # sqrtroughness[1] = .w   and  roughness = sqrtroughness^2
        ch["roughness_channel"] = "R"
        ch["roughness_is_sqrt"] = True
        ch["ao_channel"] = "G"
        # ★ The other two components of the SAME texel, which the decode used to
        # drop on the floor (`shader-confirmed`):
        #     layers[i].sqrtroughness[1] = k_composite_components[i].w
        #     layers[i].brdfblends.y     = k_composite_components[i].z
        #     layers[i].brdfblends.x     = saturate(1 - brdfblends.y)
        # `brdfblends` is the two-lobe WEIGHT PAIR — see `brdf_lobe_blend()`.
        ch["roughness2_channel"] = "A"
        ch["brdf_blend_channel"] = "B"
        ch["brdf_weight0_is"] = "1 - B"
    elif suffix in ("composite_specular", "composite_data0"):
        # specintensity = .w ; specalbedo = .xyz * .w. `composite_data0`
        # is the identical packing one lobe index up.
        ch["spec_intensity_channel"] = "A"
        ch["spec_albedo_channel"] = "RGB"
        ch["spec_albedo_scaled_by"] = "A"
        ch["packing"] = "specalbedo = rgb * a ; specintensity = a"
        ch["lobe"] = 0 if suffix == "composite_specular" else 1
    elif suffix == "specular_map":
        # Non-composite sibling. It reaches the SAME `layers[i].specalbedo[0]`
        # slot as `composite_specular` -- i.e. it is also F0 -- but it is scaled
        # by a material SCALAR, not by its own alpha (`shader-confirmed`):
        #   specularalbedo[0] = params.specular * params.speculartint *
        #                       params.specularmap * params.specintensity[0]
        #   params.specular         = k_enable_specular[i]
        #   params.speculartint     = sRGBToLinear(k_specular_tint_color[i])
        #   params.specintensity[0] = k_fresnel[i]
        #   params.specularmap      = k_specular_map[i]
        #   output.specalbedo[0]    = params.layer * specularalbedo[0].xyz
        #   output.specintensity[0] = params.layer * k_fresnel * k_enable_specular
        # `k_fresnel` is authored 0.010 and `specular_gloss` (authored 1.0)
        # lerps `specalbedo *= albedo` in at 0 (`name-confirmed`, the
        # UberMaterial declaration). `k_enable_specular` is the
        # `layerN_enable_specular_` PERMUTATION bit (`DefineLayerOption_` ->
        # k_white/k_black); permutation bits are not on disk, so it is assumed 1
        # wherever the map is bound (`inferred`).
        ch["spec_albedo_channel"] = "RGB"
        ch["spec_albedo_scaled_by"] = "fresnel"
        ch["spec_intensity_source"] = "fresnel"
        ch["spec_fresnel_default"] = SPEC_MAP_FRESNEL_DEFAULT
        ch["packing"] = ("specalbedo = rgb * layerN_fresnel * "
                         "layerN_specular_tint_color ; specintensity = layerN_fresnel")
    elif suffix == "composite_diffuse":
        # diffusealbedo = .xyz ; alpha = .w * vertexcolor.w
        # `alpha_channel` says WHICH component the engine reads; `alpha_plane`
        # (set above, for every channel) says whether that component holds real
        # data. On BC1 the two disagree — the engine still reads `.w`, but the
        # format has only 1 bit of it and the corpus never sets it.
        ch["alpha_channel"] = "A" if dxgi in ALPHA_CAPABLE_DXGI else None
    elif suffix == "alpha_map":
        # Scalar multiplier of the alpha chain, read from RED in EVERY format:
        # `params.alphamap = k_alpha_map[i].x` (`shader-confirmed`).
        # This used to be answered from the DXGI
        # format ("R" only when single-channel, else None), which left the
        # BC1/BC2/BC3-format alpha maps unlabelled and let the consumer guess `.a`
        # — wrong for the shipped BC3_UNORM_SRGB one (`494a47bd33bb1e20`, archive
        # `0703fd2acd5803e9`), whose alpha plane and red plane are the same mask
        # at different encodings (pearson +0.994) but different VALUES: mean
        # raw alpha 44.7/255 vs mean sRGB-decoded red 0.036, ~4.8x apart.
        ch["scalar_channel"] = "R"
        ch["scalar_channel_from"] = "shader"     # not inferred from the format
        # True only where the format itself also forces the answer.
        ch["single_channel_format"] = bool(dxgi in SINGLE_CHANNEL_DXGI)
    elif suffix == "opacity_map":
        # float3 transmission tint: color.rgb += background * opacity
        ch["is_transmission_tint"] = True
    if suffix in AUDIT_ONLY_SUFFIXES:
        ch["audit_only"] = True
    return ch


#: Every SUFFIX whose channel has no faithful Principled target.
#:
#: ⚠ This replaces a `role_key in AUDIT_ONLY_ROLE_KEYS` test, and the difference
#: is not cosmetic: `AUDIT_ONLY_ROLE_KEYS` is built from `_flat_roles`, which
#: filters to `KNOWN_ROLES` — the 25 hashes that were CRACKED FROM AN OBSERVED
#: BIND. So `layer0_flowmap_map` and `layer1_flowmap_map` were flagged
#: `audit_only` and `layer2_flowmap_map` / `layer3_flowmap_map` — reached through
#: the forward-hashed grid, and both shipped on Jack
#: (`c6bc8607972268c9 / 64b4b5b2a0153f7e`, materials `950c024e245bc4e9` and
#: `4ed5b4765bcdc695`, `stream-confirmed`) — were not, purely because nobody had
#: happened to observe those two cells. `audit_only` is a property of the
#: SAMPLER, not of how its hash was recovered.
AUDIT_ONLY_SUFFIXES = frozenset(
    s for c in AUDIT_ONLY_CHANNELS for s in CHANNEL_ROLE_SUFFIXES[c])

#: Back-compat: the observed-only subset. Kept because callers import it.
AUDIT_ONLY_ROLE_KEYS = frozenset(
    r for c in AUDIT_ONLY_CHANNELS for r in _flat_roles(c))


# ---------------------------------------------------------------------------
# The TWO-LOBE composite BRDF and its weight  (`brdfblends`)
# ---------------------------------------------------------------------------
# ⛔ WHAT THIS FIXES. The router picks lobe [0] — `composite_specular` for F0,
# `composite_components.x` for roughness — and used to hand it to Blender at
# **weight 1**, while dropping lobe [1] entirely as "no faithful target". Both
# halves of that are wrong on shipped data: the weight is not 1, it is authored
# per texel in the same components map the router already reads.
#
#     layers[i].brdfblends.y = k_composite_components[i].z
#     layers[i].brdfblends.x = saturate(1 - brdfblends.y)
#
# and the engine spends that pair on BOTH terms of the ambient response —
# `shader-confirmed`, the engine's ubershader:
#
#     ambientspecalbedo  = brdfweights.x * material.specalbedo[0]
#         #if enable_brdf_compositing1_
#                        + brdfweights.y * material.specalbedo[1]
#     ambientspec       += brdfweights.x * CalculateAmbientSpecularLighting(
#                              specalbedo[0], sqrtambientroughness[0]^2, …)
#         #if enable_brdf_compositing1_
#                        + brdfweights.y * CalculateAmbientSpecularLighting(
#                              specalbedo[1], sqrtambientroughness[1]^2, …)
#     output.color      += brdfweights.x * ambient * ambientalbedo
#                                        * (1 - specintensity[0])
#         #if enable_brdf_compositing1_
#                        + brdfweights.y * ambient * ambientalbedo
#                                        * (1 - specintensity[1])
#
# and on the irradiance-volume path that lights the CHARACTERS
# (`results.specular *= brdfintensity * specintensity * params.composite.brdfblend`,
# with `params.composite.brdfblend = brdfblends.x`) and inside the velvet BRDF
# (`spec *= p.composite.brdfblend; spec += GGX_Specular(composite.roughness, …)
# * Fresnel(composite.specalbedo) * saturate(1 - composite.brdfblend)`).
#
# ★ `brdfweights.x` scaling lobe [0] is **outside** every `#if
# enable_brdf_compositing1_` — so it applies whether or not the permutation that
# ADDS lobe [1] is compiled in. That is what makes this fix
# permutation-independent, which matters because permutation bits are not on disk.
#
# ⚠ THE ONE EXCEPTION, stated rather than hidden: `GGX_BRDF`
# — the direct-light path of the plain GGX permutation — reads
# `params.specalbedo = material.specalbedo[0]` with
# no weight at all. Three of the four paths weight it, including the two that
# actually light a character in-game (irradiance volume + ambient probe); the
# fourth does not. Blender has one BSDF for both, so a choice is forced, and the
# measured cost of choosing wrong is asymmetric — see
# docs/MATERIALS.md.
#
# Measured on the subject that prompted this (`stream-confirmed`, Liv
# `2fd6839161785e9c_ff91757c910ea7b6`, material `9b5a77b3ada5af7b`, all 1,048,326
# mip-0 texels whose albedo is the orange gel-coat):
#     brdfweights.x mean 0.245   <- lobe [0] carries a QUARTER, not all of it
#     lobe [0]  sqrtroughness 0.049  F0 0.311     (a near-mirror at metal F0)
#     lobe [1]  sqrtroughness 0.479  F0 0.017     (an ordinary dielectric)
#     weighted  sqrtroughness 0.337  F0 0.113     (F0 2.75x lower, 6.9x rougher)
# On the legs (`49a960afce4d4f2b`) `brdfweights.x` on the orange is **0.001** —
# lobe [0] is switched off there outright, and was being rendered at full power.
#
# The self-check that says compositing1 IS live on these materials: with it off,
# the lobe-[0] ambient-diffuse term alone gives `brdfweights.x *
# (1 - specintensity[0])` = 0.245 * (1 - 0.996) = **0.001**, i.e. a black suit.
# With both lobes it gives 0.245*0.004 + 0.755*(1 - 0.114) = **0.67** and the
# orange shows. So a
# bound `composite_data0` is taken as the on-disk evidence that the permutation
# is compiled in (`inferred`, but the alternative is a self-evidently wrong
# picture).
BRDF_BLEND_COMPONENT = "B"          # brdfblends.y = k_composite_components[i].z
LOBE1_ROUGHNESS_COMPONENT = "A"     # sqrtroughness[1] = k_composite_components[i].w
LOBE1_ALBEDO_SUFFIX = "composite_data0"
# With no `composite_data0` bound the sampler returns its authored default
# `common_black` (`name-confirmed`, the UberMaterial declaration), so
# `specalbedo[1] = .xyz * .w = 0` and the weighted sum degenerates to
# `brdfweights.x * specalbedo[0]` — which is exactly the un-gated ambient
# specular-albedo term.
LOBE1_ABSENT_ALBEDO = 0.0


def brdf_lobe_blend(layers: list, role_textures: dict, dxgi_by_tex: dict,
                    texture_files: dict | None = None) -> dict | None:
    """The two-lobe weight record for the layer that owns the routed specular.

    Returns None unless that same layer also binds a `composite_components` —
    without it `brdfblends` is the `common_black` default `(1, 0)` and lobe [0]
    already stands at full weight, i.e. today's behaviour is already right.
    """
    texture_files = texture_files or {}
    for entry in layers:
        chans = entry.get("channels") or {}
        spec_ch = chans.get("specular")
        rough_ch = chans.get("roughness")
        if not spec_ch or not rough_ch:
            continue
        if split_role(spec_ch.get("role_key", ""))[1] != "composite_specular":
            continue
        if split_role(rough_ch.get("role_key", ""))[1] != "composite_components":
            continue
        index = int(entry.get("index", 0))
        lobe1_role = f"layer{index}_{LOBE1_ALBEDO_SUFFIX}"
        lobe1_tex = (role_textures or {}).get(lobe1_role) or ""
        lobe1 = None
        if lobe1_tex:
            lobe1 = _channel(lobe1_role, lobe1_tex, dxgi_by_tex, layer=index)
            lobe1["file"] = texture_files.get(lobe1_tex, "")
        return {
            "layer": index,
            "weight_texture": rough_ch.get("texture", ""),
            "weight_file": rough_ch.get("file", ""),
            "weight_channel": BRDF_BLEND_COMPONENT,
            "weight0_expression": "1 - components.B",
            "lobe0_roughness_channel": rough_ch.get("roughness_channel", "R"),
            "lobe1_roughness_channel": LOBE1_ROUGHNESS_COMPONENT,
            # None when nothing is bound: `specalbedo[1]` is then a hard 0 and
            # only lobe [0]'s weight applies (see LOBE1_ABSENT_ALBEDO).
            "lobe1": lobe1,
            "lobe1_absent_albedo": LOBE1_ABSENT_ALBEDO,
            # Blend the roughness only when lobe [1] carries energy — a zero
            # albedo contributes no specular, so weighting its roughness in
            # would be a fudge, not a decode.
            "blend_roughness": bool(lobe1),
            "confidence": "shader-confirmed",
            "unweighted_path": "GGX_BRDF direct light",
        }
    return None


# --- composite role recovery from the TEXTURE FORMAT -------------------------
# The last route for a `generated_composite_*` bind that NO array declares
# anywhere. Every name/identity route is a measured closed negative:
#
#   * corpus-wide propagation — 0 of Liv's 11, coverage all 149 shaderset-bearing
#     archives (docs/MATERIALS.md §4b, `stream-confirmed`);
#   * the composite NAME — `generated_composite_<h1>_<h2>` has **2,241 distinct
#     h1 and 2,241 distinct h2 over 2,241 distinct names**, i.e. both halves are
#     per-texture identity hashes carrying no channel code, and **0 of the 4,482
#     inner hashes** appear in the 27,995-entry `hash_lookup.json` (container:
#     the corpus RDEF-name harvest, 54,232 rows, 100 % of its composite names);
#   * the BIND REGISTER — refuted far harder than the old bind-register
#     heuristic claimed: over
#     3,146 `(shaderset, layer)` groups binding the full 4-role composite set,
#     **all 24 register permutations occur, the modal one at 10.5 %**.
#
# What IS a function is the FORMAT. Measured over the 216 `generated_composite_*`
# textures that carry both a role and a measured DXGI format:
#
#   | class                                     |  n  | role                    |
#   | BC5_UNORM (82..84)                        |  52 | composite_normals 52/52 |
#   | non-sRGB, non-BC5 (BC1/BC3/BC4 _UNORM)    |  52 | composite_components    |
#   | any _SRGB                                 | 112 | diffuse/specular/data0  |
#
# ⛔ The sRGB class is NOT separable by format alone (BC3_UNORM_SRGB is specular
# 51, diffuse 11, data0 4), which is why the two uniqueness guards below exist
# and why `composite_data0` is never emitted: within one resolution group the
# single BC1_UNORM_SRGB is the diffuse and the single BC3_UNORM_SRGB is the
# specular, and any group that is not that shape is REFUSED outright.
COMPOSITE_NAME_PREFIX = "generated_composite_"
COMPOSITE_DIFFUSE_DXGI = 72          # BC1_UNORM_SRGB
COMPOSITE_SPECULAR_DXGI = 78         # BC3_UNORM_SRGB

# Refusal reasons, so a bind that stays `rdef_bind{n}` says WHY.
REFUSE_NO_FORMAT = "no_format"
REFUSE_MANY_GROUPS = "many_unresolved_resolution_groups"
REFUSE_NOT_UNIQUE = "format_not_unique_in_group"
REFUSE_UNKNOWN_FORMAT = "format_matches_no_composite_class"
REFUSE_NO_FREE_LAYER = "no_free_layer_index"
REFUSE_ROLE_TAKEN = "role_already_carried_by_this_shaderset"


def _composite_class(dxgi: int) -> str | None:
    """The role SUFFIX a composite atlas's format implies, or None."""
    if dxgi in BC5_DXGI:
        return "composite_normals"
    if dxgi not in SRGB_DXGI:
        return "composite_components"
    if dxgi == COMPOSITE_DIFFUSE_DXGI:
        return "composite_diffuse"
    if dxgi == COMPOSITE_SPECULAR_DXGI:
        return "composite_specular"
    return None


def composite_roles_from_format(binds: dict, meta: dict, *,
                                claimed_layers=(), taken_roles=()) -> dict:
    """Recover `layerN_composite_*` roles for unresolved composite binds.

    `binds` is `{bind_register -> tex_hash}` restricted to binds whose RDEF name
    starts with `generated_composite_` and which no earlier source could name.
    `meta` is `{tex_hash -> {"dxgi", "width", "height"}}`.

    Returns `{"roles": {bind -> role_key}, "refused": {bind -> reason},
    "layer": int | None}`. ⛔ It emits a SUFFIX-derived role only; the LAYER is
    the lowest index this shaderset has not already claimed, because layer is
    **not** recoverable from the format (resolution-group and layer-group agree
    on only 95.5 % of shadersets, and layer 0 is the strictly largest group in
    just 5 of 19 multi-layer shadersets). That is sound exactly when one
    unresolved group remains, which is why more than one is refused.
    """
    roles: dict[int, str] = {}
    refused: dict[int, str] = {}

    sized: dict[tuple[int, int], dict[int, int]] = {}
    for bind, tex in (binds or {}).items():
        m = (meta or {}).get(tex) or {}
        dxgi, w, h = m.get("dxgi"), m.get("width"), m.get("height")
        if not dxgi or not w or not h:
            refused[bind] = REFUSE_NO_FORMAT
            continue
        sized.setdefault((int(w), int(h)), {})[bind] = int(dxgi)

    if not sized:
        return {"roles": roles, "refused": refused, "layer": None}
    if len(sized) > 1:
        for group in sized.values():
            for bind in group:
                refused[bind] = REFUSE_MANY_GROUPS
        return {"roles": roles, "refused": refused, "layer": None}

    (group,) = sized.values()
    by_class: dict[str, list[int]] = {}
    for bind, dxgi in group.items():
        cls = _composite_class(dxgi)
        if cls is None:
            refused[bind] = REFUSE_UNKNOWN_FORMAT
            continue
        by_class.setdefault(cls, []).append(bind)

    # The specular guard needs the diffuse to be unambiguous too: the one
    # measured counterexample to the bare format rule is a BC1_UNORM_SRGB
    # `composite_specular` sharing its group with a second BC1_UNORM_SRGB.
    diffuse_unique = len(by_class.get("composite_diffuse", ())) == 1
    layer = None
    for cls, hits in sorted(by_class.items()):
        if len(hits) != 1 or (cls == "composite_specular" and not diffuse_unique):
            for bind in hits:
                refused[bind] = REFUSE_NOT_UNIQUE
            continue
        if layer is None:
            # ⛔ `UBERMATERIAL_LAYER_COUNT`, not `MAX_LAYER`: the ubermaterial
            # declares `:layer0 … :layer3` and
            # only those four are real inputnames. `MAX_LAYER = 7` is the parser's
            # tolerance, and emitting `layer4_composite_*` would fabricate a name.
            claimed = {int(c) for c in (claimed_layers or ())}
            free = [i for i in range(UBERMATERIAL_LAYER_COUNT) if i not in claimed]
            if not free:
                for bind in hits:
                    refused[bind] = REFUSE_NO_FREE_LAYER
                continue
            layer = free[0]
        role = f"layer{layer}_{cls}"
        if role in set(taken_roles or ()):
            refused[hits[0]] = REFUSE_ROLE_TAKEN
            continue
        roles[hits[0]] = role
    return {"roles": roles, "refused": refused,
            "layer": layer if roles else None}


def is_composite_path(channels: dict) -> bool:
    """Does any routed channel come from a `composite_*` sampler?

    `use_composite_` / `k_material_composite_enable` are not on disk, so a bound
    composite sampler is the only evidence available — and it is decisive in one
    direction: the cook does not emit a `composite_*` binding for a material that
    never reads one.
    """
    for ch in (channels or {}).values():
        if "composite_" in str(ch.get("role_key", "")):
            return True
    return False


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
# Every per-layer blend parameter this module reads. The names are members of
# `UberMaterialLayer`, the engine's own per-layer property group
# (`name-confirmed`); `symbol64(name) == the shipped hash` is asserted by
# `tests/test_layer_compositing.py::test_blend_param_hashes_are_real_preimages`
# — nothing here may be invented (findings §3).
LAYER_BLEND_PARAMS = ("blend_mask_offset", "blend_mask_scale", "blend_fade",
                      "diff_albedo_blend_alpha", "spec_albedo_blend_alpha",
                      "roughness_blend_alpha", "lighting_blend_alpha",
                      "subsurface_blend_alpha", "backlighting_blend_alpha",
                      "brdf_blend_alpha", "transparency_blend_alpha",
                      "normal_blend_alpha")
HASH_LAYER_BLEND_PARAM = {
    (L, p): symbol64(f"layer{L}_{p}")
    for L in range(MAX_LAYER + 1) for p in LAYER_BLEND_PARAMS}
# authored defaults (`name-confirmed`, the UberMaterial declaration) — used only where
# the parameter is ABSENT, which the findings label `inferred`.
DEFAULT_ALPHA_THRESHOLD = 0.5
DEFAULT_EMISSIVE_SCALE = 1.0


def _named_value(scalars: dict, name: str, name_hash: int) -> float | None:
    """Look a named material scalar up through every shape A2 may emit."""
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
    """Read `field` from A2's per-layer `layers[]`, if it has landed."""
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
    `0613ef69c99cbbc6` (`stream-confirmed`), i.e. 12.5x too dim.
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

    Falls back to the authored default the UberMaterial declares. "Absent means
    default" is `inferred` — see findings §1 "Missing named scalars".
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

    Layer 0 is the base of `BlendLayers()` (`output = layers[0]`) and is never
    blended, so it carries no record.
    For layer i >= 1 the engine computes, with authored-default region/fade maps::

        mask_amount = saturate(mask.R * mask_scale + mask_offset)
        blend       = saturate((vertex_blend - height) / fade) * mask_amount
        composited  = BlendValue(lower_layers, layer_i, blend * <channel>_blend_alpha,
                                 blend_mode)

    Everything in the returned dict is `shader-confirmed` except the two labelled
    fields (`vertex_blend_*`, whose color-set linkage is `inferred`) and the
    "absent parameter means authored default" assumption.
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
    fade = max(fade, MIN_BLEND_FADE)          # the engine's `max(..., 0.01f)`

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
        "confidence": "shader-confirmed",
    }


def build_material_spec(key: str, *, shaderset_hash: str = "", material_hash: str = "",
                        role_textures: dict[str, str] | None = None,
                        dxgi_by_tex: dict[str, int] | None = None,
                        scalars: dict | None = None,
                        texture_files: dict[str, str] | None = None,
                        role_sources: dict[str, str] | None = None,
                        role_ambiguity: dict[str, dict[str, int]] | None = None,
                        texture_names: dict[str, str] | None = None) -> dict:
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

    `role_sources` maps role_key -> where that binding's ROLE came from
    (`le_mesh.role_index.SOURCE_*`: `array` / `archive` / `corpus` / `rdef`), and
    `role_ambiguity` maps tex_hash -> {role: votes} for any bind the corpus role
    index disagreed about. Both are optional, always present in the output (often
    `{}`) and purely audit: nothing in the routing reads them. They exist so a
    corpus-VOTED role can never be mistaken for an array-DECLARED one.
    """
    role_textures = role_textures or {}
    dxgi_by_tex = dxgi_by_tex or {}
    scalars = scalars or {}
    texture_files = texture_files or {}
    texture_names = texture_names or {}
    role_sources = role_sources or {}
    role_ambiguity = role_ambiguity or {}

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

    # --- the alpha chain (`shader-confirmed`) --------------------------------
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
            # X2: the component this channel is read from — `.w` of the composite
            # diffuse. NOT the base colour's own component ("RGB"), which
            # `dict(base)` copied in, so it must be overwritten here.
            derived["component"] = BASE_COLOR_ALPHA_COMPONENT
            derived["punchthrough"] = base.get("dxgi") in PUNCHTHROUGH_ALPHA_DXGI
            derived["alpha_plane"] = bool(base.get("dxgi") in ALPHA_PLANE_DXGI)
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

    # --- the two-lobe composite BRDF -----------------------------------------
    lobes = brdf_lobe_blend(layers, role_textures, dxgi_by_tex, texture_files)
    composite = is_composite_path(channels)
    # A composite-path material that binds NO `composite_specular` has
    # `specalbedo[0] = common_black.xyz * common_black.w = 0` — it is NOT the
    # 0.04 dielectric Blender falls back to (`name-confirmed`,
    # `:composite_specular := ( compositesampler :name = "common_black" )`, plus
    # the shader's own `specalbedo = .xyz * .w`). 25 of the
    # 440 materials in `blender_tool/exports` are in exactly that state.
    specular_f0_when_absent = 0.0 if (composite and "specular" not in channels) else None

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
        # Provenance of each role key, and every corpus disagreement, so a voted
        # role never reads as a declared one. Always present (often `{}`) so the
        # level and `.lemesh` specs keep identical key sets.
        "role_sources": role_sources,
        "role_ambiguity": role_ambiguity,
        # tex_hash -> the exact RDEF texture name, for every bind this material
        # carries. The extractor already recovers these (`Archive.rdef_names`,
        # `symbol64(name) == tex_hash`, 74/0 exact) and used to drop them on the
        # floor, which is why an unrouted bind read as an anonymous `rdef_bind{n}`
        # even when the artist had named it. Audit only — nothing routes on it.
        "texture_names": {t: texture_names[t] for t in sorted(set(role_textures.values()))
                          if t in texture_names},
        # --- additive keys (A3 contract) -------------------------------------
        "layers": layers,
        "primary_layer": layered["primary_layer"],
        "unrouted_roles": layered["unrouted"],
        # E3: every entry of `unrouted_roles`, classified. Always present (often
        # `{}`) so the level and `.lemesh` specs keep identical key sets.
        "unrouted_role_notes": {
            r: explain_unrouted(r, texture_names.get(role_textures.get(r, "")))
            for r in layered["unrouted"]},
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
        # Always present (often None) so the level and `.lemesh` specs keep
        # identical key sets — the same contract `unrouted_role_notes` follows.
        "brdf_lobes": lobes,
        "composite_path": composite,
        "specular_f0_when_absent": specular_f0_when_absent,
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


# --- proven-TSV resolver (the reference style) ---------------------------------

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


def is_scanner_artefact_row(shaderset_hash: str, inputname_hash: str) -> bool:
    """A scan row whose `inputname_hash` EQUALS its own `shaderset_hash`.

    Two such rows exist in archive `0703fd2acd5803e9` — shadersets
    `80a6642707ce0367` and `05575a94091f1839` (`stream-confirmed`,
    generic_rebuilds/shaderset_texture_scan.tsv:2 and :33). They are
    **not** input names and must never be cracked; the whole row is a misparse:

        entry_offset 768   (every real row in the same slice is >= 70040)
        slot 0  layer 1032  engineresource 4096
        uscale 1.599e-36   vscale 5.740e-42        <- denormal garbage
        textureassetid a33d0790d3cbab49            <- BC7_UNORM_SRGB, teal

    Left in, the row lands as `unknown_s0`, and the DXGI fallback in
    `classify_roles_layered` promotes it to **Base Color** — measured mean RGB
    (63, 131, 111) on 2 materials that the engine renders with the authored
    `common_white` albedo default. Dropping it here is the narrowest fix: the
    signature is exact and self-describing, no hash list is hard-coded.
    """
    a = (shaderset_hash or "").lower().lstrip("0")
    b = (inputname_hash or "").lower().lstrip("0")
    return bool(a) and a == b


def load_shaderset_textures(scan_path: Path, names: dict[int, str]
                            ) -> dict[str, dict[str, str]]:
    """shaderset_hash -> {role_key -> tex_hash}.

    Role name via `role_for_inputname`: `ROLE_BY_INPUTNAME` (cracked +
    forward-hashed layer grid) -> `hash_lookup` name -> `unknown_s{slot}`.
    """
    table: dict[str, dict[str, str]] = {}
    with Path(scan_path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            shd = row["shaderset_hash"].lower()
            ihex = row["inputname_hash"].lower().zfill(16)
            if is_scanner_artefact_row(shd, ihex):
                continue
            role = role_for_inputname(ihex, row.get("slot", "x"), names)
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
    directly into them (the scene-binding schema).
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

    `rows` are le_shaderset_scan.ShaderTexRow objects (fields
    inputname_hash / textureassetid_hash / slot). Same role-cracking order as the
    TSV path (`role_for_inputname`): `ROLE_BY_INPUTNAME` — cracked preimages plus
    the forward-hashed `layer{0..3}_{suffix}` grid — then the `hash_lookup` name,
    then `unknown_s{slot}`.
    """
    table: dict[str, str] = {}
    for r in rows:
        ihex = str(r.inputname_hash).lower().zfill(16)
        if is_scanner_artefact_row(getattr(r, "shaderset_hash", ""), ihex):
            continue                     # misparsed header row, see the helper
        role = role_for_inputname(ihex, getattr(r, "slot", "x"), names)
        table[role] = str(r.textureassetid_hash).lower()
    return table
