"""Unit tests for the pure-python decision layer of the addon's material_builder.

`material_builder` imports `bpy` at module scope, which does not exist outside
Blender, so a minimal stub is injected into `sys.modules` *inside* the loader below
and the module is loaded straight off disk (it has no relative imports). Nothing here
imports `bpy` at module scope, so `tests/run_tests.py` can discover this file.

The node-graph half -- sockets actually linked, `surface_render_method` read-back,
`image.alpha_mode` read-back -- is asserted by `tests/blender_material_probe.py`,
which must run inside Blender.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
MB_PATH = BLENDER_TOOL / "addon" / "lone_echo_import" / "material_builder.py"
FIXTURES_MAT = BLENDER_TOOL / "exports" / "fixtures_mat"

_MB = None


def _mb():
    """Load material_builder with a stub `bpy` (never the real one)."""
    global _MB
    if _MB is not None:
        return _MB
    if "bpy" not in sys.modules:
        stub = types.ModuleType("bpy")
        stub.data = types.SimpleNamespace(materials=None, images=None)
        stub.context = types.SimpleNamespace(scene=None)
        stub.types = types.SimpleNamespace(Material=object)
        sys.modules["bpy"] = stub
    spec = importlib.util.spec_from_file_location("_le_material_builder", MB_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MB = mod
    return mod


# --- k_alpha (B1) ------------------------------------------------------------

def test_k_alpha_default_and_clamp():
    mb = _mb()
    assert mb.k_alpha({}) == 1.0
    assert mb.k_alpha({"alpha": 0.25}) == 0.25
    assert mb.k_alpha({"alpha": 2.0}) == 1.0
    assert mb.k_alpha({"alpha": -1.0}) == 0.0
    assert mb.k_alpha({"alpha": None}) == 1.0


def test_k_alpha_under_one_forces_a_blended_pass():
    """B1: k_alpha=0.25 with no opacity map must not render opaque."""
    mb = _mb()
    assert mb.resolve_render_mode({"alpha": 0.25}) == "BLEND"
    assert mb.surface_render_method_for(mb.resolve_render_mode({"alpha": 0.25})) == "BLENDED"
    assert mb.resolve_render_mode({"alpha": 1.0}) == "OPAQUE"


# --- render mode (B2/B3) -----------------------------------------------------

def test_render_mode_prefers_explicit_manifest_field():
    mb = _mb()
    assert mb.resolve_render_mode({"render_mode": "CLIP", "mattype": 1}) == "CLIP"
    assert mb.resolve_render_mode({"render_mode": "blend"}) == "BLEND"


def test_render_mode_from_mattype():
    mb = _mb()
    assert mb.resolve_render_mode({"mattype": 1}) == "OPAQUE"     # eMTForwardOpaque
    assert mb.resolve_render_mode({"mattype": 9}) == "CLIP"       # eMTAlphaTested
    assert mb.resolve_render_mode({"mattype": 2}) == "BLEND"      # eMTForwardTransparent
    assert mb.resolve_render_mode({"mattype": 16}) == "BLEND"     # eMTTransparentPostAA
    # eMTSkirt is the DECAL pass: it reads its diffuse alpha, it is not opaque.
    # (Was OPAQUE until docs/MATERIALS.md — that is the defect that
    # rendered Jack's shoulder and thigh patches as solid black cards.)
    assert mb.resolve_render_mode({"mattype": 10}) == "BLEND"     # eMTSkirt


def test_render_mode_falls_back_to_blend_mode():
    mb = _mb()
    assert mb.resolve_render_mode({"blend_mode": 0}) == "OPAQUE"   # eBlendOpaque
    assert mb.resolve_render_mode({"blend_mode": 7}) == "BLEND"    # eBlendTransparent
    assert mb.resolve_render_mode({"blend_mode": 12}) == "BLEND"   # eBlendTranslucent
    assert mb.resolve_render_mode({"blend_mode": 1}) == "BLEND"    # eBlendAdditive
    # mattype wins over blend_mode: alpha-tested ships with eBlendOpaque
    assert mb.resolve_render_mode({"mattype": 9, "blend_mode": 0}) == "CLIP"


def test_surface_render_method_has_only_two_values():
    mb = _mb()
    assert mb.surface_render_method_for("OPAQUE") == "DITHERED"
    assert mb.surface_render_method_for("CLIP") == "DITHERED"     # EEVEE Next has no CLIP
    assert mb.surface_render_method_for("BLEND") == "BLENDED"


def test_additive_blend_is_flagged_lossy():
    mb = _mb()
    assert mb.is_lossy_blend({"blend_mode": 1}) is True            # eBlendAdditive
    assert mb.is_lossy_blend({"blend_mode": 8}) is True            # eBlendLinearDodge
    assert mb.is_lossy_blend({"blend_mode": 7}) is False


def test_alpha_threshold_default_is_the_authored_default():
    mb = _mb()
    assert mb.alpha_threshold_for({}) == 0.5                       # k_alpha_threshold
    assert mb.alpha_threshold_for({"alpha_threshold": 0.33}) == 0.33
    assert mb.alpha_threshold_for({"alpha_threshold": None}) == 0.5


# --- image.alpha_mode (the highest-value fix) --------------------------------

def test_image_alpha_mode_defaults_to_channel_packed():
    mb = _mb()
    assert mb.image_alpha_mode(None) == "CHANNEL_PACKED"
    assert mb.image_alpha_mode({}) == "CHANNEL_PACKED"
    assert mb.image_alpha_mode({"role_key": "layer0_composite_diffuse"}) == "CHANNEL_PACKED"


def test_image_alpha_mode_honours_a_manifest_hint():
    mb = _mb()
    assert mb.image_alpha_mode({"alpha_mode": "STRAIGHT"}) == "STRAIGHT"
    assert mb.image_alpha_mode({"alpha_mode": "none"}) == "NONE"
    assert mb.image_alpha_mode({"alpha_mode": "garbage"}) == "CHANNEL_PACKED"


def test_alpha_component_follows_the_dxgi_format():
    mb = _mb()
    assert mb.alpha_component_of({"dxgi": 78}) == "A"              # BC3_UNORM_SRGB
    assert mb.alpha_component_of({"dxgi": 80}) == "R"              # BC4_UNORM
    assert mb.alpha_component_of({"dxgi": 80, "component": "A"}) == "A"


# --- opacity vs transmission ------------------------------------------------

def test_opacity_map_is_a_transmission_tint_not_alpha():
    mb = _mb()
    ch = {"opacity": {"role_key": "layer0_opacity_map", "file": "x.dds"}}
    alpha, trans = mb.split_opacity_channels(ch)
    assert alpha is None
    assert trans is ch["opacity"]


def test_alpha_map_routes_to_alpha():
    mb = _mb()
    ch = {"opacity": {"role_key": "layer1_alpha_map", "file": "x.dds"}}
    alpha, trans = mb.split_opacity_channels(ch)
    assert alpha is ch["opacity"]
    assert trans is None


def test_new_contract_alpha_and_transmission_pass_through():
    mb = _mb()
    ch = {"alpha": {"role_key": "layer0_alpha_map"},
          "transmission": {"role_key": "layer0_opacity_map"}}
    alpha, trans = mb.split_opacity_channels(ch)
    assert alpha is ch["alpha"] and trans is ch["transmission"]


def test_base_color_alpha_is_opacity_only_for_non_opaque_composite_bc3():
    mb = _mb()
    bc = {"role_key": "layer0_composite_diffuse", "dxgi": 78}
    assert mb.uses_base_color_alpha({"mattype": 1}, {"base_color": bc}) is False
    assert mb.uses_base_color_alpha({"mattype": 2}, {"base_color": bc}) is True
    # DXGI without an alpha block (BC1) can't carry opacity
    bc1 = {"role_key": "layer0_composite_diffuse", "dxgi": 71}
    assert mb.uses_base_color_alpha({"mattype": 2}, {"base_color": bc1}) is False
    # explicit alpha_source wins
    assert mb.uses_base_color_alpha({"alpha_source": "BASE_COLOR_ALPHA"}, {}) is True
    assert mb.uses_base_color_alpha({"alpha_source": "ALPHA_MAP"},
                                    {"base_color": bc}) is False


# --- components packing ------------------------------------------------------

def test_roughness_is_sqrt_for_composite_components():
    mb = _mb()
    assert mb.roughness_is_sqrt({}, {"role_key": "layer0_composite_components"}) is True
    assert mb.roughness_is_sqrt({}, {"role_key": "layer0_specular_map"}) is False
    assert mb.roughness_is_sqrt({"roughness_is_sqrt": False},
                                {"role_key": "layer0_composite_components"}) is False


def test_ao_channel_is_green_of_composite_components():
    mb = _mb()
    assert mb.ao_channel_of({}, {"role_key": "layer0_composite_components"}) == "G"
    assert mb.ao_channel_of({}, {"role_key": "layer0_normal_map"}) is None
    assert mb.ao_channel_of({"ao_channel": "R"}, None) == "R"


# --- emission ----------------------------------------------------------------

def test_black_emissive_tint_is_treated_as_no_tint():
    """bakeemissivecolor is (0,0,0) on every genuinely emissive material inspected;
    multiplying an emissive map by it would annihilate the emission."""
    mb = _mb()
    assert mb.emission_tint({"emissive_color": [0.0, 0.0, 0.0]}) == (1.0, 1.0, 1.0)
    assert mb.emission_tint({}) == (1.0, 1.0, 1.0)
    assert mb.emission_tint({"emissive_color": [1.0, 0.5, 0.25]}) == (1.0, 0.5, 0.25)
    assert mb.emission_tint({"emissive_tint_color": [0.2, 0.3, 0.4],
                             "emissive_color": [0.0, 0.0, 0.0]}) == (0.2, 0.3, 0.4)


def test_emission_strength_has_no_unit_conversion_constant():
    mb = _mb()
    assert mb.emission_strength({}) == 1.0
    assert mb.emission_strength({"emissive_intensity": 25.0}) == 25.0
    assert mb.emission_strength({"emissive_intensity": 25.0, "emissive_scale": 2.0}) == 50.0
    # the layer-selection bug's worked example: 25 must not come out as 2
    assert mb.emission_strength({"emissive_intensity": 2.0}) != 25.0


def test_refractive_index_prefers_the_authored_value():
    mb = _mb()
    assert mb.refractive_index({"ior": 1.52}) == 1.52
    assert mb.refractive_index({}) == 1.45
    assert mb.refractive_index({"ior": 1.0}) == 1.45      # authored default = no data


# --- vertex colour gate (1c) -------------------------------------------------

def test_vertex_color_gate_reads_the_mesh_flag():
    mb = _mb()
    assert mb.wants_vertex_color_diffuse({"flags": 0x2000}) is True
    assert mb.wants_vertex_color_diffuse({"flag_names": ["eDiffuseVertexColor"]}) is True
    assert mb.wants_vertex_color_diffuse({"flags": 0x40001,
                                          "flag_names": ["eCastsShadow"]}) is False
    assert mb.wants_vertex_color_diffuse({}) is False


# --- corpus ------------------------------------------------------------------

def test_decision_layer_runs_over_every_fixture_material():
    """Every shipped fixture manifest must survive the decision layer."""
    mb = _mb()
    if not FIXTURES_MAT.is_dir():
        return
    n = 0
    for mf in sorted(FIXTURES_MAT.glob("*.lemesh/manifest.json")):
        manifest = json.loads(mf.read_text(encoding="utf-8"))
        for spec in manifest.get("materials", []):
            mode = mb.resolve_render_mode(spec)
            assert mode in ("OPAQUE", "CLIP", "BLEND")
            assert mb.surface_render_method_for(mode) in ("DITHERED", "BLENDED")
            assert 0.0 <= mb.k_alpha(spec) <= 1.0
            assert mb.emission_strength(spec) >= 0.0
            assert len(mb.emission_tint(spec)) == 3
            mb.split_opacity_channels(spec.get("channels", {}) or {})
            n += 1
    assert n > 0, "fixture corpus present but no materials found"


def test_no_fixture_mesh_sets_ediffusevertexcolor():
    """Corpus count for 1c: 0 of 121 objects across the 51 fixture packages set
    eDiffuseVertexColor, so the vertex-colour multiply MUST stay gated -- wiring it
    unconditionally would tint every mesh in the corpus with an unused attribute."""
    mb = _mb()
    if not FIXTURES_MAT.is_dir():
        return
    total = flagged = 0
    for mf in sorted(FIXTURES_MAT.glob("*.lemesh/manifest.json")):
        manifest = json.loads(mf.read_text(encoding="utf-8"))
        for obj in manifest.get("objects", []):
            total += 1
            if mb.wants_vertex_color_diffuse(obj):
                flagged += 1
    assert total > 0
    assert flagged == 0, f"corpus changed: {flagged}/{total} meshes now set the flag"


# ---------------------------------------------------------------------------
# base_color_factor -- the flat FALLBACK, and its unauthored 4th float
# ---------------------------------------------------------------------------

def test_base_color_fallback_forces_alpha_to_one():
    """`bakecolor`'s 4th float is unauthored — `k_hardware_color` is a `Color4`
    with no `:a` widget in the material asset schema, and no engine shader reads
    the member. It must never reach a socket verbatim."""
    mb = _mb()
    assert mb.base_color_fallback(
        {"base_color_factor": [0.16612, 0.13933, 0.11532, 0.0]}
    ) == (0.16612, 0.13933, 0.11532, 1.0)
    # the two shipped non-zero cases are equally normalised
    assert mb.base_color_fallback(
        {"base_color_factor": [0.09163, 0.07908, 0.07286, 0.10113]}
    ) == (0.09163, 0.07908, 0.07286, 1.0)
    assert mb.base_color_fallback({"base_color_factor": [1.0, 1.0, 1.0, 1.0]}) \
        == (1.0, 1.0, 1.0, 1.0)


def test_base_color_fallback_defaults_to_white_and_tolerates_short_input():
    mb = _mb()
    assert mb.base_color_fallback({}) == (1.0, 1.0, 1.0, 1.0)
    assert mb.base_color_fallback({"base_color_factor": None}) == (1.0, 1.0, 1.0, 1.0)
    assert mb.base_color_fallback({"base_color_factor": [0.5, 0.25]}) \
        == (0.5, 0.25, 1.0, 1.0)


def test_base_color_fallback_matches_the_scatter_paths_normalisation():
    """`scatter_import.py:696` already did `list(...)[:3] + [1.0]` on the v1
    level path. The mesh path silently did not; the two must not disagree."""
    mb = _mb()
    for raw in ([0.2, 0.3, 0.4, 0.0], [0.2, 0.3, 0.4, 1.0], [0.2, 0.3, 0.4, 0.5]):
        scatter_style = tuple(list(raw)[:3] + [1.0])
        assert mb.base_color_fallback({"base_color_factor": raw}) == scatter_style


def test_the_shipped_corpus_really_does_ship_zero_alpha_bakecolors():
    """The measurement the rule rests on, asserted rather than only written down
    (docs/MATERIALS.md recorded 27/100 in prose
    and no test ever checked it). Skips cleanly if the fixtures are absent."""
    mb = _mb()
    if not FIXTURES_MAT.is_dir():
        return
    zero = total = 0
    for mf in sorted(FIXTURES_MAT.rglob("manifest.json")):
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except ValueError:
            continue
        for spec in m.get("materials", []):
            f = spec.get("base_color_factor")
            if not f:
                continue
            total += 1
            if float(f[3]) == 0.0:
                zero += 1
            # whatever it is, the fallback never passes it through
            assert mb.base_color_fallback(spec)[3] == 1.0
    assert total > 0
    assert zero > 0, "the .a == 0.0 case is what the rule is about"


# ---------------------------------------------------------------------------
# the NORMAL map is gated by its layer's blend mask, like every other channel
#
# `output.normal = BlendValue(base.normal, layer.normal, m * normal_blend_alpha,
# mode)` — `shader-confirmed`.
#
# The gate was absent until 2026-08-05 on the premise (a local working file
# §156) that "the lowest layer wins for `normal` in every corpus material". That
# premise expired once the corpus role index began naming a layer-1 composite
# quartet whose layer-0 counterpart stays unrouted, which is exactly Jack's legs.
# ---------------------------------------------------------------------------

# Jack's legs: `28b682b9af140fbf__2fdd8c946178528d` in `c6bc8607972268c9`.
# layer1_blend_mask == `jck_body_damage_bubble_a_msk`, `layer1_blend_mask_offset`
# == -1.0, so the BATTLE-DAMAGE layer is parked at its animated OFF extreme —
# and it was the only layer whose roles the corpus index could name.
JACK_LEGS_KEY = "28b682b9af140fbf__2fdd8c946178528d"


def _suppressed_normal_spec():
    """The Jack-legs shape, as a synthetic spec: normal only on a gated layer 1."""
    normal_ch = {"texture": "15d96c006b692612", "role_key": "layer1_composite_normals",
                 "dxgi": 83, "colorspace": "Non-Color", "reconstruct_z": True,
                 "layer": 1, "blend_layer": 1, "file": "textures/x.dds"}
    return {
        "key": JACK_LEGS_KEY,
        "channels": {"normal": dict(normal_ch)},
        "layers": [{"index": 1, "channels": {"normal": dict(normal_ch)},
                    "blend": {"layer": 1, "mask": None, "mask_component": "R",
                              "mask_default": 1.0, "mask_scale": 1.0,
                              "mask_offset": -1.0, "blend_fade": 1.0,
                              "blend_mode": 6, "channel_alpha": {},
                              "gated_channels": ["base_color", "normal",
                                                 "roughness", "specular"],
                              "amount_min": 0.0, "amount_max": 0.0,
                              "amount_constant": 0.0, "suppressed_at_rest": True}}],
    }


def test_a_normal_on_a_gated_layer_reports_its_blend_record():
    """`blend_for_channel` must answer for `normal` — the builder's gate reads it."""
    mb = _mb()
    spec = _suppressed_normal_spec()
    blend = mb.blend_for_channel(spec, spec["channels"], "normal")
    assert blend is not None, "normal is in gated_channels; it must be gated"
    assert blend["layer"] == 1
    assert mb.channel_blend_alpha(blend, "normal") == 1.0


def test_a_suppressed_layers_normal_gate_is_a_hard_zero():
    """`saturate(mask.R * scale + offset)` with the shipped `offset = -1.0` can
    never open, so the damage normal must contribute nothing at all — not a
    dimmed version of itself."""
    mb = _mb()
    spec = _suppressed_normal_spec()
    blend = mb.blend_for_channel(spec, spec["channels"], "normal")
    scale = mb.blend_mask_scale_for(blend)
    offset = mb.blend_mask_offset_for(blend, {})
    assert mb._sat(scale + offset) <= 0.0
    # the import-time override is the escape hatch, and it must still work
    assert mb._sat(scale + mb.blend_mask_offset_for(blend, {"layer_blend_mask_offset": 0.0})) > 0.0


def test_layer_zero_normal_is_never_gated():
    """A normal that lives on layer 0 is the base of `BlendLayers()` and has no
    mask — the fix must not touch the overwhelmingly common case."""
    mb = _mb()
    spec = _suppressed_normal_spec()
    for ch in (spec["channels"]["normal"], spec["layers"][0]["channels"]["normal"]):
        ch["layer"] = 0
        ch["blend_layer"] = 0
    assert mb.blend_for_channel(spec, spec["channels"], "normal") is None


def test_flat_tangent_normal_is_the_identity_encoding():
    """The `base.normal` term of the lerp. (0,0,1) stored unsigned is (.5,.5,1)."""
    mb = _mb()
    assert mb.FLAT_TANGENT_NORMAL == (0.5, 0.5, 1.0, 1.0)


def test_jacks_legs_carry_a_suppressed_normal_gating_damage_layer():
    """The shipped measurement the fix rests on, asserted rather than only
    written down.

    ⚠ Deliberately does NOT assert which layer the MERGED view selects. It
    asserted `layer1_composite_normals` for about an hour, and then the format
    rule (`materials.composite_roles_from_format`) named the layer-0 quartet and
    the merged view moved to layer 0 — correctly. Pinning the broken state would
    have made the fix look like the regression. What is durable is the shipped
    layer record: layer 1 gates `normal`, its mask is Jack's battle damage, and
    `blend_mask_offset = -1.0` parks it at its animated OFF extreme. Skips
    cleanly when the package is not extracted.
    """
    mb = _mb()
    pkg = BLENDER_TOOL / "exports" / "chars" / \
        "c6bc8607972268c9_64b4b5b2a0153f7e.lemesh" / "manifest.json"
    if not pkg.exists():
        return
    specs = {s["key"]: s for s in
             json.loads(pkg.read_text(encoding="utf-8")).get("materials", [])}
    spec = specs.get(JACK_LEGS_KEY)
    if spec is None:                      # pre-RDEF extraction of the same asset
        return
    blend = mb.layer_blend_of(spec, 1)
    assert blend is not None, "layer 1 is the damage overlay and must carry a mask"
    assert "normal" in blend["gated_channels"]
    assert blend["suppressed_at_rest"] is True
    assert blend["mask_offset"] == -1.0
    assert (blend["mask"] or {}).get("texture") == "331bd11a0f032117"
    # and whichever layer the merged view lands on, the gate must agree with it
    ch_layer = spec["channels"]["normal"].get("blend_layer")
    got = mb.blend_for_channel(spec, spec["channels"], "normal")
    assert (got is None) == (ch_layer is None)
    if got is not None:
        assert got["layer"] == ch_layer


# --- emissive map vs ambient occlusion --------------------------------------

def _spec(*, albedo=True, emission=True, components=False, black=None):
    """A spec carrying just the channels the AO/emissive decision reads."""
    channels = {}
    if albedo:
        channels["base_color"] = {"texture": "a" * 16, "role_key": "layer0_albedo_map"}
    if emission:
        channels["emission"] = {"texture": "e" * 16, "role_key": "layer0_emissive_map"}
        if black is not None:
            channels["emission"]["black_fraction"] = black
    role_textures = {"layer0_albedo_map": "a" * 16} if albedo else {}
    if components:
        channels["roughness"] = {"texture": "c" * 16,
                                 "role_key": "layer0_composite_components"}
        role_textures["layer0_composite_components"] = "c" * 16
    return {"channels": channels, "role_textures": role_textures}


def test_a_material_with_no_emissive_map_is_never_ao():
    spec = _spec(emission=False)
    assert _mb().emissive_is_ao(spec, spec["channels"]) is False


def test_an_emissive_map_with_no_albedo_has_nothing_to_occlude():
    """The original discriminator, verified in-game on `d09afd15b1c75c04` i1535."""
    spec = _spec(albedo=False)
    assert _mb().emissive_is_ao(spec, spec["channels"]) is False


def test_a_mostly_black_map_is_a_glow_mask_not_occlusion():
    """AO is a visibility term; a mostly-black one would occlude everything.

    This is the case that was rendering wrong: a character glow mask inverted
    and multiplied into Base Colour, which punches the glowing texels black.
    """
    spec = _spec(black=0.978)
    assert _mb().emissive_is_ao(spec, spec["channels"]) is False


def test_a_bright_map_in_the_emissive_slot_is_still_occlusion():
    """The content test decides BOTH ways -- this is the reverted attempts' bug.

    Gating on colorspace or saturation flipped genuine AO maps over to
    emissive. A bright map keeps its occlusion reading even when the material
    also binds `composite_components`, so the structural rule cannot overrule
    what the texture plainly is.
    """
    spec = _spec(black=0.02, components=True)
    assert _mb().emissive_is_ao(spec, spec["channels"]) is True


def test_measurement_outranks_the_structural_rule():
    bright = _spec(black=0.10, components=True)
    dark = _spec(black=0.90, components=True)
    assert _mb().emissive_is_ao(bright, bright["channels"]) is True
    assert _mb().emissive_is_ao(dark, dark["channels"]) is False


def test_without_a_measurement_a_bound_components_map_settles_it():
    """Older sidecars carry no `black_fraction`, so the shader fact decides.

    AO is `composite_components.y`, so a material binding that texture already
    has its occlusion; reading the emissive map as a second AO double-counts.
    """
    spec = _spec(components=True)
    assert _mb().emissive_is_ao(spec, spec["channels"]) is False


def test_without_a_measurement_or_components_the_old_rule_stands():
    """Nothing to go on but `has_albedo` -- unchanged, so no silent widening."""
    spec = _spec()
    assert _mb().emissive_is_ao(spec, spec["channels"]) is True


def test_the_threshold_sits_in_the_gap_between_the_two_populations():
    """Measured: AO maps 0.000, albedo <=0.373, emissive >=0.587.

    The threshold must fall in that empty band, so it is a majority statement
    rather than a number fitted to one sample.
    """
    assert 0.373 < _mb().EMISSIVE_BLACK_FRACTION < 0.587
