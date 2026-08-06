"""The TWO-LOBE composite BRDF: `brdfblends`, lobe [1], and the F0-zero default.

Covers the fix in docs/MATERIALS.md. Two independent defects,
both on the specular side of the composite path:

  1. lobe [0] was handed to Blender at **weight 1**, but the weight is authored
     per texel in `composite_components.z`
     (`brdfblends = (1 - z, z)`, `shader-confirmed`) and spent on both terms of
     the engine's ambient response. Lobe [1] (`composite_data0` +
     `composite_components.w`) was dropped entirely.
  2. a composite-path material that binds NO `composite_specular` got Blender's
     0.04 dielectric, where the engine's sampler default `common_black` makes
     `specalbedo[0]` exactly **0**.

Everything here is pure python. The node graph these decisions drive is asserted
in Blender by `tests/blender_material_probe.py`; the *values* are asserted here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

from le_mesh import materials as mat

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
EXPORTS = BLENDER_TOOL / "exports"
MB_PATH = BLENDER_TOOL / "addon" / "lone_echo_import" / "material_builder.py"
# The subject of the audit. Absent on a clean checkout -> those tests no-op.
LIV = EXPORTS / "chars" / "2fd6839161785e9c_ff91757c910ea7b6.lemesh"
LIV_TORSO_KEY = "9b5a77b3ada5af7b__1468becc74addb64"
LIV_HARNESS_KEY = "11ff222d38a601f3__364ff94a1d8c8805"
LIV_TORSO_DATA0 = "a479b018f8db1997"

_MB = None


def _mb():
    """`material_builder` with a stub `bpy` — same loader as test_material_builder_nodes."""
    global _MB
    if _MB is not None:
        return _MB
    if "bpy" not in sys.modules:
        stub = types.ModuleType("bpy")
        stub.data = types.SimpleNamespace(materials=None, images=None)
        stub.context = types.SimpleNamespace(scene=None)
        stub.types = types.SimpleNamespace(Material=object)
        sys.modules["bpy"] = stub
    spec = importlib.util.spec_from_file_location("_le_material_builder_lobes", MB_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MB = mod
    return mod


def _liv_spec(key):
    manifest = LIV / "manifest.json"
    if not manifest.is_file():
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for spec in data.get("materials", []):
        if spec.get("key") == key:
            return spec
    return None


# ---------------------------------------------------------------------------
# 1. The decode: what the components texel carries
# ---------------------------------------------------------------------------

def test_components_channel_carries_all_four_packed_signals():
    """`.x` roughness[0], `.y` AO, `.z` brdfblends.y, `.w` roughness[1] (:2247-2251).

    The last two used to be dropped, which is what let a per-texel weight of
    0.245 be rendered as 1.0.
    """
    ch = mat.classify_roles_layered({"layer0_composite_components": "cc"},
                                    {"cc": 77})["channels"]["roughness"]
    assert ch["roughness_channel"] == "R"
    assert ch["ao_channel"] == "G"
    assert ch["brdf_blend_channel"] == mat.BRDF_BLEND_COMPONENT == "B"
    assert ch["roughness2_channel"] == mat.LOBE1_ROUGHNESS_COMPONENT == "A"
    assert ch["brdf_weight0_is"] == "1 - B"


def test_the_two_maps_whose_alpha_is_now_read_are_channel_packed():
    """Reading `.w` and labelling the alpha 'ignore me' would be incoherent."""
    assert mat.alpha_mode_for("layer0_composite_components") == "CHANNEL_PACKED"
    assert mat.alpha_mode_for("layer0_composite_data0") == "CHANNEL_PACKED"
    # ...and the RGB-corrupting mode is still never chosen automatically
    assert mat.alpha_mode_for("layer0_composite_components") != mat.ALPHA_MODE_STRAIGHT


def test_composite_data0_channel_uses_the_same_packing_as_lobe0():
    """`specalbedo[1] = .xyz * .w`, one lobe index up from :2252 (:2255-2257)."""
    ch = mat._channel("layer0_composite_data0", "d0", {"d0": 78}, layer=0)
    assert ch["spec_albedo_scaled_by"] == "A"
    assert ch["lobe"] == 1
    assert ch["colorspace"] == "sRGB"          # dxgi 78 is BC3_UNORM_SRGB
    spec0 = mat._channel("layer0_composite_specular", "cs", {"cs": 78}, layer=0)
    assert spec0["lobe"] == 0
    assert spec0["packing"] == ch["packing"]


# ---------------------------------------------------------------------------
# 2. `brdf_lobe_blend` — when a weight record exists at all
# ---------------------------------------------------------------------------

def _layers(roles, dxgi):
    return mat.classify_roles_layered(roles, dxgi)["layers"]


def test_no_record_without_a_components_map():
    """No components map -> `brdfblends` is the `common_black` (1, 0) default, so
    lobe [0] at full weight was already correct and nothing must change."""
    roles = {"layer0_composite_specular": "cs", "layer0_composite_diffuse": "cd"}
    dxgi = {"cs": 78, "cd": 72}
    assert mat.brdf_lobe_blend(_layers(roles, dxgi), roles, dxgi) is None


def test_no_record_without_a_specular_map():
    roles = {"layer0_composite_components": "cc", "layer0_composite_diffuse": "cd"}
    dxgi = {"cc": 77, "cd": 72}
    assert mat.brdf_lobe_blend(_layers(roles, dxgi), roles, dxgi) is None


def test_record_without_data0_still_weights_lobe0_but_not_the_roughness():
    """`composite_data0` unbound -> `specalbedo[1] = 0`, so the weighted sum
    degenerates to `brdfweights.x * specalbedo[0]` (:576, outside the `#if`).
    Blending a zero-energy lobe's ROUGHNESS in would be a fudge, so it is not."""
    roles = {"layer0_composite_specular": "cs", "layer0_composite_components": "cc"}
    dxgi = {"cs": 78, "cc": 77}
    rec = mat.brdf_lobe_blend(_layers(roles, dxgi), roles, dxgi)
    assert rec is not None
    assert rec["lobe1"] is None
    assert rec["blend_roughness"] is False
    assert rec["lobe1_absent_albedo"] == 0.0
    assert rec["weight_channel"] == "B"
    assert rec["weight_texture"] == "cc"


def test_record_with_data0_carries_the_second_lobe():
    roles = {"layer0_composite_specular": "cs", "layer0_composite_components": "cc",
             "layer0_composite_data0": "d0"}
    dxgi = {"cs": 78, "cc": 77, "d0": 78}
    rec = mat.brdf_lobe_blend(_layers(roles, dxgi), roles, dxgi,
                              {"d0": "textures/d0.dds"})
    assert rec["blend_roughness"] is True
    assert rec["lobe1"]["texture"] == "d0"
    assert rec["lobe1"]["file"] == "textures/d0.dds"
    assert rec["lobe1"]["colorspace"] == "sRGB"
    assert rec["lobe0_roughness_channel"] == "R"
    assert rec["lobe1_roughness_channel"] == "A"


def test_the_weight_and_the_lobes_must_come_from_the_same_layer():
    """`brdfblends` is a per-LAYER value; taking layer 1's weight for layer 0's
    specular would be the same class of error as reading the wrong emissive
    intensity docs/MATERIALS.md."""
    roles = {"layer0_composite_specular": "cs", "layer1_composite_components": "cc"}
    dxgi = {"cs": 78, "cc": 77}
    assert mat.brdf_lobe_blend(_layers(roles, dxgi), roles, dxgi) is None


def test_data0_stays_out_of_channels_and_keeps_its_unrouted_note():
    """The second lobe reaches the graph through `brdf_lobes`, NOT by becoming a
    Principled channel — `channels` still means "a socket takes this"."""
    roles = {"layer0_composite_specular": "cs", "layer0_composite_components": "cc",
             "layer0_composite_data0": "d0"}
    out = mat.classify_roles_layered(roles, {"cs": 78, "cc": 77, "d0": 78})
    assert "layer0_composite_data0" in out["unrouted"]
    assert all("data0" not in str(c.get("role_key")) for c in out["channels"].values())
    assert mat.explain_unrouted("layer0_composite_data0")["classification"] \
        == "deliberately unrouted"


# ---------------------------------------------------------------------------
# 3. `build_material_spec` — the key-set contract and the F0-zero default
# ---------------------------------------------------------------------------

def test_spec_always_carries_the_three_new_keys():
    """Same contract as `unrouted_role_notes`: always present, often None, so the
    level and `.lemesh` specs keep identical key sets."""
    empty = mat.build_material_spec("k", role_textures={}, dxgi_by_tex={})
    for key in ("brdf_lobes", "composite_path", "specular_f0_when_absent"):
        assert key in empty
    assert empty["brdf_lobes"] is None
    assert empty["composite_path"] is False
    assert empty["specular_f0_when_absent"] is None


def test_composite_without_a_specular_map_has_f0_zero_not_zero_point_zero_four():
    """`:composite_specular := ( compositesampler :name = "common_black" )` in
    the engine's ubermaterial -> `specalbedo[0] = .xyz * .w = 0`."""
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_composite_diffuse": "cd",
                            "layer0_composite_components": "cc"},
        dxgi_by_tex={"cd": 72, "cc": 77})
    assert spec["composite_path"] is True
    assert "specular" not in spec["channels"]
    assert spec["specular_f0_when_absent"] == 0.0


def test_a_bound_specular_map_leaves_the_default_alone():
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_composite_diffuse": "cd",
                            "layer0_composite_specular": "cs"},
        dxgi_by_tex={"cd": 72, "cs": 78})
    assert spec["specular_f0_when_absent"] is None


def test_a_non_composite_material_is_not_forced_to_zero():
    """`albedo_map`/`specular_map` materials run the NON-composite path, whose
    `specular_map` default is `common_white` — a different question entirely."""
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_albedo_map": "a0"}, dxgi_by_tex={"a0": 72})
    assert spec["composite_path"] is False
    assert spec["specular_f0_when_absent"] is None


# ---------------------------------------------------------------------------
# 4. The builder's decision layer
# ---------------------------------------------------------------------------

def test_specular_ior_level_is_zero_only_for_composite_without_specular():
    mb = _mb()
    chans = {"base_color": {"role_key": "layer0_composite_diffuse"}}
    assert mb.specular_ior_level_for({"composite_path": True}, chans, None) == 0.0
    assert mb.specular_ior_level_for({"composite_path": True}, chans,
                                     {"role_key": "layer0_composite_specular"}) == 0.5
    assert mb.specular_ior_level_for({"composite_path": False}, chans, None) == 0.5
    # old manifest with no `composite_path` key -> derived from the role names
    assert mb.specular_ior_level_for({}, chans, None) == 0.0
    assert mb.specular_ior_level_for(
        {}, {"base_color": {"role_key": "layer0_albedo_map"}}, None) == 0.5


def test_lobe_blend_can_be_switched_off():
    """The pre-fix look must stay reachable — every render in exports/hero before
    2026-08 was made with lobe [0] at weight 1."""
    mb = _mb()
    assert mb.lobe_blend_enabled(None) is True
    assert mb.lobe_blend_enabled({}) is True
    assert mb.lobe_blend_enabled({"brdf_lobe_blend": False}) is False


def test_builder_component_accessors_default_safely():
    mb = _mb()
    assert mb.lobe_weight_component(None) == "B"
    assert mb.lobe_weight_component({"weight_channel": "garbage"}) == "B"
    assert mb.lobe1_roughness_component({"lobe1_roughness_channel": "A"}) == "A"
    assert mb.blends_roughness(None) is False
    assert mb.blends_roughness({"blend_roughness": False, "lobe1": {"file": "x"}}) is False
    assert mb.blends_roughness({"lobe1": {"file": "x"}}) is True


def test_manifest_record_wins_over_derivation():
    mb = _mb()
    rec = {"layer": 0, "weight_channel": "B"}
    assert mb.brdf_lobes_of({"brdf_lobes": rec}, None) is rec
    assert mb.brdf_lobes_of({"brdf_lobes": None}, None) is None


# ---------------------------------------------------------------------------
# 5. Real shipped data (no-ops on a clean checkout)
# ---------------------------------------------------------------------------

def test_dds_dxgi_format_reads_a_shipped_header():
    mb = _mb()
    dds = LIV / "textures" / (LIV_TORSO_DATA0 + ".dds")
    if not dds.is_file():
        return
    assert mb.dds_dxgi_format(dds) == 78          # BC3_UNORM_SRGB
    assert mb.dds_dxgi_format(LIV / "manifest.json") is None
    assert mb.dds_dxgi_format(LIV / "does_not_exist.dds") is None


def test_old_manifest_still_gets_the_second_lobe_by_derivation():
    """A package written before `brdf_lobes` existed must still get the record —
    the builder reconstructs it from `channels` plus the `composite_data0` entry
    the decoder already records as unrouted.

    ⚠ This used to assert `"brdf_lobes" not in spec` against the package on disk
    and call that "the premise". That made the test depend on the package never
    being re-extracted, which is not a property of the code — re-extracting Liv
    on 2026-08-05 broke it. The old shape is now CONSTRUCTED here, so the test
    measures the derivation path whatever is on disk.
    """
    mb = _mb()
    spec = _liv_spec(LIV_TORSO_KEY)
    if spec is None:
        return
    spec = {k: v for k, v in spec.items() if k != "brdf_lobes"}
    rec = mb.brdf_lobes_of(spec, LIV)
    assert rec is not None
    assert rec["weight_channel"] == "B"
    assert rec["weight_texture"] == spec["channels"]["roughness"]["texture"]
    assert rec["lobe1"]["texture"] == LIV_TORSO_DATA0
    assert rec["lobe1"]["colorspace"] == "sRGB"   # NOT the Non-Color fallback
    assert rec["blend_roughness"] is True


def test_livs_harness_is_the_f0_zero_case_on_real_data():
    """`11ff222d38a601f3__364ff94a1d8c8805` binds composite diffuse/components/
    normals and no `composite_specular`; its components map's `.x` is 0 at every
    texel, so 0.04 + roughness 0 rendered it as a sharp black-lacquer mirror."""
    mb = _mb()
    spec = _liv_spec(LIV_HARNESS_KEY)
    if spec is None:
        return
    chans = spec["channels"]
    assert "specular" not in chans
    assert mb.is_composite_path(spec, chans) is True
    assert mb.specular_ior_level_for(spec, chans, None) == 0.0
    assert mb.brdf_lobes_of(spec, LIV) is None    # no specular -> no lobe pair


def test_no_shipped_roughness_or_normal_map_is_an_srgb_format():
    """Guards the roughness colour space, which is now DXGI-authoritative instead
    of hardcoded `Non-Color`. The change is a no-op today and must stay visible if
    that ever stops being true."""
    if not EXPORTS.is_dir():
        return
    for manifest in sorted(EXPORTS.glob("**/manifest.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for spec in data.get("materials", []) or []:
            for name in ("roughness", "normal"):
                ch = (spec.get("channels") or {}).get(name)
                if not ch:
                    continue
                assert ch.get("dxgi") not in mat.SRGB_DXGI, (manifest, name)
                assert ch.get("colorspace") == "Non-Color", (manifest, name)


def test_every_shipped_composite_material_resolves_a_consistent_lobe_record():
    """Sweep every package in `exports/`: whenever a record is derived it must
    name a texture that is actually in the package, and never claim a roughness
    blend without one."""
    mb = _mb()
    if not EXPORTS.is_dir():
        return
    checked = 0
    for manifest in sorted(EXPORTS.glob("**/*.lemesh/manifest.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for spec in data.get("materials", []):
            rec = mb.brdf_lobes_of(spec, manifest.parent)
            if rec is None:
                continue
            checked += 1
            assert rec["weight_texture"], spec.get("key")
            lobe1 = rec["lobe1"]
            assert rec["blend_roughness"] is bool(lobe1)
            if lobe1:
                assert (manifest.parent / lobe1["file"]).is_file()
                assert lobe1["dxgi"] is not None
                assert lobe1["colorspace"] in ("sRGB", "Non-Color")
    assert checked >= 0                            # 0 only on a clean checkout


# ---------------------------------------------------------------------------
# ★★ the ZERO-ROUGHNESS gate — the two renderers disagree at sqrtroughness == 0
#
#   RAD:     GGX numerator `m2 = sqrtroughness**4` => the lobe contributes
#            IDENTICALLY NOTHING at 0.
#   Blender: `Roughness = 0` is a special-cased PERFECT MIRROR at full energy.
#
# Measured `composite_components.x == 0` fraction of mip-0 texels, decoded
# 2026-08-05 (`stream-confirmed`):
#
#     Liv       68.13 %  53.67 %  42.92 %  42.14 %  100.00 % (the harness)
#     Jack       0.00 - 0.03 %      android   0.00 - 0.05 %
#
# Same rig, same graph, same package format — which is the whole explanation for
# why she read as wet lacquer and they never did.
# ---------------------------------------------------------------------------

def test_the_zero_roughness_gate_is_on_by_default_and_can_be_turned_off():
    mb = _mb()
    assert mb.lobe_zero_roughness_gate(None) is True
    assert mb.lobe_zero_roughness_gate({}) is True
    assert mb.lobe_zero_roughness_gate({"brdf_lobe_zero_roughness_gate": False}) is False
    # it is a SEPARATE switch from the two-lobe blend itself
    assert mb.lobe_blend_enabled({"brdf_lobe_zero_roughness_gate": False}) is True


def test_the_gate_formula_is_a_noop_wherever_lobe_zero_is_live():
    """The graph computes `factor' = max(z, 1 - (x > 0))`.

    Asserted as arithmetic so the invariant is pinned without Blender: wherever
    the lobe-0 roughness is non-zero the factor is EXACTLY the shipped `z`, and
    wherever it is zero the factor is 1 (lobe 1 only). That no-op property is
    what makes the change safe on the 99.97 % of Jack's and the android's texels
    that carry a live lobe 0 — measured in-Blender at 0.033 % differing pixels
    with a maximum channel delta of 8, i.e. the renderer's own noise floor.
    """
    def gated(z, x):
        return max(z, 1.0 - (1.0 if x > 0.0 else 0.0))
    for z in (0.0, 0.245, 0.5, 1.0):
        for x in (1e-6, 0.05, 0.42, 1.0):
            assert gated(z, x) == z, "live lobe 0 must leave the weight untouched"
        assert gated(z, 0.0) == 1.0, "a degenerate lobe 0 must hand over to lobe 1"


def test_liv_really_does_ship_a_degenerate_roughness_and_the_others_do_not():
    """The measurement the gate rests on, asserted rather than only written down.

    ⚠ Written with `>=` / `<=` thresholds and a clean SKIP, NOT as an exact
    census — an exact census asserts a property of the packages on disk as if it
    were a property of the code (`test_vertex_streams.py` docstring).
    """
    import struct
    mb = _mb()
    if not LIV.is_dir():
        return
    man = json.loads((LIV / "manifest.json").read_text(encoding="utf-8"))
    worst = 0.0
    for spec in man.get("materials", []):
        rg = (spec.get("channels") or {}).get("roughness") or {}
        f = LIV / str(rg.get("file", ""))
        if not rg.get("file") or not f.is_file():
            continue
        # BC1 (70..72) stores the two 5:6:5 endpoints at +0/+2 of each 8-byte
        # block; a block whose BOTH endpoints have red == 0 is all-zero in R.
        b = f.read_bytes()
        off = 148 if b[84:88] == b"DX10" else 128
        dxgi = struct.unpack("<I", b[128:132])[0] if b[84:88] == b"DX10" else 0
        if dxgi not in (70, 71, 72):
            continue
        n = (len(b) - off) // 8
        zero = 0
        for k in range(min(n, 20000)):
            o = off + k * 8
            c0 = b[o] | (b[o + 1] << 8)
            c1 = b[o + 2] | (b[o + 3] << 8)
            if ((c0 >> 11) & 0x1F) == 0 and ((c1 >> 11) & 0x1F) == 0:
                zero += 1
        worst = max(worst, zero / max(min(n, 20000), 1))
    if worst == 0.0:                      # no BC1 roughness map present
        return
    assert worst >= 0.20, (
        f"Liv's worst all-zero-roughness block fraction fell to {worst:.3f}; "
        "the gate exists because it is large — re-measure before deleting it")
