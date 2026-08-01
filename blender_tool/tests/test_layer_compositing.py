"""Layer compositing: `layerN_blend_mask` gates the layers above 0.

Locks the rule the engine's own layer composite follows, against the real
shipped values of the bridge material `0613ef69c99cbbc6`.

THE RULE::

    the composite loop
      output = layers[0]                          # layer 0 IS the base
      for i = 1 .. num_layers-1                   # ASCENDING, accumulating
        fade   = max(blend_fade[i] * fade_scale_offset_map[i].x, 0.01)
        scale  = blend_mask_scale[i]  * fade_scale_offset_map[i].y
        offset = blend_mask_offset[i] + fade_scale_offset_map[i].z
        b      = ComputeBlend(scale, offset, blend_mask[i].R, ...)
        output = BlendLayer(output, layers[i], b, blend_mode[i], alphas)

    ComputeBlend
      _scale  = scale  * scale_regions_map[i].x
      _offset = offset * (1.0 - offset_regions_map[i].x)
      _mask   = saturate(mask * _scale + _offset)        # `mask` is `.x` == RED
      return BlendAmount(vertblend, height, fade, 0.0) * _mask

    BlendAmount     saturate(((vertblend - height) / fade) - 0)
    BlendValue      6 eBlendTransparent is the authored default and is a LERP:
                    (1 - m) * base + m * layer

Every participating map defaults to the value that makes its own term vanish
(the blend mask to white 1, the fade/scale/offset map to (1,1,0), the
scale-regions map to white 1, the offset-regions map to black 0), so with
authored defaults the whole thing is::

    blend = saturate(vertex_blend / fade) * saturate(mask.R * scale + offset)
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

from le_mesh import materials as mat
from le_mesh.material_scalars import symbol64

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
MB_PATH = BLENDER_TOOL / "addon" / "lone_echo_import" / "material_builder.py"
FIXTURE_DIRS = (BLENDER_TOOL / "exports" / "fixtures_mat3",
                BLENDER_TOOL / "exports" / "fixtures_mat")

_MB = None


def _mb():
    """Load material_builder with a stub `bpy` (never at module scope)."""
    global _MB
    if _MB is not None:
        return _MB
    if "bpy" not in sys.modules:
        stub = types.ModuleType("bpy")
        stub.data = types.SimpleNamespace(materials=None, images=None)
        stub.context = types.SimpleNamespace(scene=None)
        stub.types = types.SimpleNamespace(Material=object)
        sys.modules["bpy"] = stub
    spec = importlib.util.spec_from_file_location("_le_material_builder_a7", MB_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MB = mod
    return mod


def _fixtures():
    for d in FIXTURE_DIRS:
        if d.is_dir():
            return d
    return None


# ---------------------------------------------------------------------------
# Shipped values, decoded from bridge material 0613ef69c99cbbc6
# ---------------------------------------------------------------------------
BRIDGE_KEY = "b964375c606d812f__0613ef69c99cbbc6"
BRIDGE_ROLE_TEXTURES = {
    "layer0_specular_map": "9cef9cbe9bc742ff",
    "layer0_normal_map": "9cef9cbe9bda5ff0",
    "layer0_opacity_map": "9cef9cbe9bdb42ff",
    "layer1_blend_mask": "cf07d65049f874e7",
    "layer2_emissive_map": "0408e9e71fdebd0f",
    "layer1_emissive_map": "63c63b9027cf5e8b",
}
BRIDGE_DXGI = {
    "9cef9cbe9bc742ff": 72, "9cef9cbe9bda5ff0": 83, "9cef9cbe9bdb42ff": 72,
    "cf07d65049f874e7": 80, "0408e9e71fdebd0f": 72, "63c63b9027cf5e8b": 72,
}
# The exact `materialprops` of the shipped material, hash-keyed.
BRIDGE_NAMED_SCALARS = {
    "c6f8f070a09880a0": -1.0,     # layer1_blend_mask_offset   <-- THIS test's subject
    "2f0e118582db9c08": -1.0,     # layer2_blend_mask_offset
    "516b9827ccc13de3": 25.0,     # layer1_emissive_intensity
    "c7e40edd6f299f19": 2.0,      # layer2_emissive_intensity
    "31e35f7a5feb8441": 2.0,      # layer0_emissive_intensity
    "25f0f7652abbc480": -0.2,     # layer1_emissive_map_voffset
}
BRIDGE_SCALARS = {
    "mattype": 2, "blend_mode": 12, "alpha": 1.0,
    "base_color_factor": [1.0, 1.0, 1.0, 1.0],
    "emissive_color": [0.0, 0.0, 0.0],
    "named_scalars": dict(BRIDGE_NAMED_SCALARS),
}


def _bridge_spec(**overrides):
    scalars = dict(BRIDGE_SCALARS)
    scalars.update(overrides)
    return mat.build_material_spec(BRIDGE_KEY, shaderset_hash="b964375c606d812f",
                                   material_hash="0613ef69c99cbbc6",
                                   role_textures=BRIDGE_ROLE_TEXTURES,
                                   dxgi_by_tex=BRIDGE_DXGI, scalars=scalars)


def _blend(spec, index):
    for entry in spec["layers"]:
        if entry["index"] == index:
            return entry["blend"]
    raise AssertionError(f"no layer {index}")


# ---------------------------------------------------------------------------
# 1. no invented names
# ---------------------------------------------------------------------------

def test_blend_param_hashes_are_real_preimages():
    """Ten role names WERE fabricated on this front once (findings 3). Every
    per-layer blend parameter name must hash to the key it is filed under."""
    for (layer, param), name_hash in mat.HASH_LAYER_BLEND_PARAM.items():
        name = f"layer{layer}_{param}"
        assert symbol64(name) == name_hash, name


def test_the_shipped_bridge_hashes_resolve_to_the_blend_offsets():
    """These two words are in the bridge material's `materialprops`, and each is
    the verified preimage of its name."""
    assert f"{symbol64('layer1_blend_mask_offset'):016x}" == "c6f8f070a09880a0"
    assert f"{symbol64('layer2_blend_mask_offset'):016x}" == "2f0e118582db9c08"
    assert BRIDGE_NAMED_SCALARS["c6f8f070a09880a0"] == -1.0
    assert BRIDGE_NAMED_SCALARS["2f0e118582db9c08"] == -1.0


def test_blend_mask_role_hashes_are_real_preimages():
    for layer in (1, 2, 3):
        name = f"layer{layer}_blend_mask"
        key = f"{symbol64(name):016x}"
        assert mat.INPUTNAME_ROLE[key][0] == name


# ---------------------------------------------------------------------------
# 2. the rule's constants
# ---------------------------------------------------------------------------

def test_mask_channel_is_red():
    """The engine samples the blend mask through its RED channel."""
    assert mat.BLEND_MASK_COMPONENT == "R"


def test_default_blend_operator_is_a_lerp_not_an_add():
    """The authored `blend_mode` default is 6, and the engine's operator 6 is
    `(1 - mask) * base + mask * layer`."""
    assert mat.DEFAULT_LAYER_BLEND_MODE == 6
    assert mat.LAYER_BLEND_MODE_NAMES[6] == "eBlendTransparent"
    assert 6 in mat.LAYER_BLEND_LERP_MODES
    assert mat.LAYER_BLEND_MODE_NAMES[1] == "eBlendAdditive"
    assert mat.LAYER_BLEND_ADD_MODES == frozenset({1, 7})


def test_authored_defaults_are_the_neutral_values():
    assert mat.DEFAULT_BLEND_MASK_SCALE == 1.0
    assert mat.DEFAULT_BLEND_MASK_OFFSET == 0.0
    assert mat.DEFAULT_BLEND_FADE == 1.0
    assert mat.MIN_BLEND_FADE == 0.01                 # the shader's `max(..., 0.01)`
    assert mat.DEFAULT_BLEND_ALPHA == 1.0
    assert mat.DEFAULT_BLEND_MASK_VALUE == 1.0        # sampler defaults to white
    assert mat.DEFAULT_BLEND_HEIGHT == 0.0


def test_per_channel_blend_alpha_table():
    """Each property gets its own `layerN_*_blend_alpha` (BlendLayer, :747-860)."""
    t = mat.CHANNEL_BLEND_ALPHA_PARAM
    assert t["emission"] == "lighting_blend_alpha"        # emissive rides in lighting
    assert t["base_color"] == "diff_albedo_blend_alpha"
    assert t["specular"] == "spec_albedo_blend_alpha"
    assert t["roughness"] == "roughness_blend_alpha"
    assert t["alpha"] == "transparency_blend_alpha"
    assert t["normal"] == "normal_blend_alpha"
    assert t["transmission"] is None                     # :787-789 uses the bare mask
    for channel in t:
        assert channel in mat.CHANNEL_ROLE_SUFFIXES, channel


def test_blend_amount_bounds_are_the_saturate_endpoints():
    sat = mat.blend_amount_bounds
    assert sat(1.0, 0.0, True) == (0.0, 1.0)      # authored default: mask passes through
    assert sat(1.0, -1.0, True) == (0.0, 0.0)     # the shipped bridge value
    assert sat(1.0, 1.0, True) == (1.0, 1.0)      # fully open
    assert sat(0.5, 0.25, True) == (0.25, 0.75)
    # no mask texture -> the sampler returns common_white == 1.0, so it collapses
    assert sat(1.0, 0.0, False) == (1.0, 1.0)
    assert sat(1.0, -1.0, False) == (0.0, 0.0)


# ---------------------------------------------------------------------------
# 3. the rule against the driving case
# ---------------------------------------------------------------------------

def test_layer0_carries_no_blend_record():
    """`output = layers[0]` (:941) -- the base is never blended."""
    assert _blend(_bridge_spec(), 0) is None
    assert mat.layer_blend_for(0, {"blend_mask": {}}, {}) is None


def test_bridge_layer1_reads_its_own_mask_and_offset():
    b = _blend(_bridge_spec(), 1)
    assert b["layer"] == 1
    assert b["mask"]["role_key"] == "layer1_blend_mask"
    assert b["mask"]["texture"] == "cf07d65049f874e7"
    assert b["mask_component"] == "R"
    assert b["mask_offset"] == -1.0                 # the shipped value
    assert b["mask_scale"] == 1.0                   # authored default
    assert b["blend_fade"] == 1.0
    assert "blend_mask_offset" in b["from_material"]
    assert "blend_mask_scale" not in b["from_material"]
    assert b["gated_channels"] == ["emission"]


def test_bridge_layer1_is_parked_at_its_animated_off_extreme():
    """`saturate(mask.R * 1.0 + (-1.0)) == 0` for every mask value in [0,1], so
    the layer contributes nothing at rest. `blend_mask_offset` is animatable with
    a soft minimum of -1.0, and the two region maps are weighted masks with
    ANIMATED per-slice weights -- so this is a runtime state, not a decode bug."""
    b = _blend(_bridge_spec(), 1)
    assert b["amount_min"] == 0.0
    assert b["amount_max"] == 0.0
    assert b["amount_constant"] == 0.0
    assert b["suppressed_at_rest"] is True


def test_bridge_layer2_has_no_mask_texture_but_still_suppressed():
    """Layer 2 binds no `blend_mask`, so the sampler returns `common_white` = 1.0
    and the amount is `saturate(1 * 1 + (-1)) == 0` -- a constant, no texture."""
    b = _blend(_bridge_spec(), 2)
    assert b["mask"] is None
    assert b["mask_offset"] == -1.0
    assert b["amount_constant"] == 0.0
    assert b["gated_channels"] == ["emission"]


def test_bridge_reports_both_suppressed_layers():
    spec = _bridge_spec()
    assert spec["layer_blend_suppressed"] == [1, 2]
    # the merged emission channel points at the layer that gates it
    assert spec["channels"]["emission"]["blend_layer"] == 1
    # ...and the emissive intensity is still the layer-1 value (25, not 2)
    assert spec["emissive_layer"] == 1
    assert spec["emissive_intensity"] == 25.0


def test_authored_default_offset_lets_the_mask_through():
    """With `layerN_blend_mask_offset` absent it is the authored 0.0 and the mask
    gates directly -- this is the shape of the one corpus material that carries a
    blend mask and no blend scalars at all (`1f517a5a...__6e92391dc748a44a`)."""
    spec = _bridge_spec(named_scalars={"516b9827ccc13de3": 25.0})
    b = _blend(spec, 1)
    assert b["mask_offset"] == 0.0
    assert (b["amount_min"], b["amount_max"]) == (0.0, 1.0)
    assert b["amount_constant"] is None       # spatially varying: needs the texture
    assert b["suppressed_at_rest"] is False
    assert spec["layer_blend_suppressed"] == []


def test_named_scalars_resolved_path_is_honoured():
    spec = _bridge_spec(named_scalars={},
                        named_scalars_resolved={"layer1_blend_mask_offset": -0.25,
                                                "layer1_blend_mask_scale": 2.0,
                                                "layer1_lighting_blend_alpha": 0.5})
    b = _blend(spec, 1)
    assert b["mask_offset"] == -0.25
    assert b["mask_scale"] == 2.0
    assert b["channel_alpha"]["emission"] == 0.5
    assert b["amount_min"] == 0.0
    assert b["amount_max"] == 1.0


def test_blend_fade_is_clamped_to_the_authored_minimum():
    spec = _bridge_spec(named_scalars_resolved={"layer1_blend_fade": 0.0})
    assert _blend(spec, 1)["blend_fade"] == mat.MIN_BLEND_FADE


def test_vertex_blend_contract_is_recorded_but_not_applied():
    """`vertblend` for layer i is component (i-1) of the SECOND vertex colour
    stream. We record where it comes from; wiring it needs `mesh_builder` to
    import `color1` AND a shader permutation bit that is not on disk."""
    spec = _bridge_spec()
    for index, comp in ((1, "R"), (2, "G")):
        b = _blend(spec, index)
        assert b["vertex_blend_attribute"] == "color1"
        assert b["vertex_blend_component"] == comp
        assert b["vertex_blend_applied"] is False


def test_blend_mode_is_the_authored_default_and_says_so():
    """`layerN_blend_mode` is an INT materialprop and `materialprops` is decoded
    as f32 words, so it cannot be read back through that path. Record that it
    was NOT read rather than implying it was."""
    b = _blend(_bridge_spec(), 1)
    assert b["blend_mode"] == 6
    assert b["blend_mode_from_material"] is False


# ---------------------------------------------------------------------------
# 4. back-compat -- every old key survives
# ---------------------------------------------------------------------------

def test_layer_entries_keep_every_previous_key():
    for entry in _bridge_spec()["layers"]:
        for key in ("index", "channels", "emissive_intensity"):
            assert key in entry, key
        assert "blend" in entry


def test_blend_mask_is_not_a_principled_channel():
    """It is the compositing weight, never a Principled input."""
    assert "blend_mask" in mat.AUDIT_ONLY_CHANNELS
    assert "layer1_blend_mask" in mat.BLEND_MASK_ROLES
    for name in ("base_color", "roughness", "specular", "emission", "alpha"):
        assert "blend_mask" not in mat.CHANNEL_ROLE_SUFFIXES[name]


def test_spec_survives_an_empty_scalar_dict():
    spec = mat.build_material_spec("k", role_textures=BRIDGE_ROLE_TEXTURES,
                                   dxgi_by_tex=BRIDGE_DXGI, scalars={})
    b = _blend(spec, 1)
    assert b["mask_offset"] == 0.0 and b["mask_scale"] == 1.0
    assert b["from_material"] == []
    assert spec["layer_blend_suppressed"] == []


# ---------------------------------------------------------------------------
# 5. the builder's pure decision layer
# ---------------------------------------------------------------------------

def test_builder_finds_the_blend_that_gates_a_channel():
    mb = _mb()
    spec = _bridge_spec()
    blend = mb.blend_for_channel(spec, spec["channels"], "emission")
    assert blend is not None and blend["layer"] == 1
    # a channel the mask does not gate gets nothing
    assert mb.blend_for_channel(spec, spec["channels"], "normal") is None
    assert mb.blend_for_channel(spec, spec["channels"], "transmission") is None
    assert mb.layer_blend_of(spec, 0) is None
    assert mb.layer_blend_of(spec, 9) is None


def test_builder_blend_amount_is_constant_zero_for_the_shipped_offset():
    mb = _mb()
    blend = _blend(_bridge_spec(), 1)
    assert mb.blend_amount_bounds(blend, {}, True) == (0.0, 0.0)
    assert mb.blend_amount_constant(blend, {}, True) == 0.0
    assert mb.blend_mask_offset_for(blend, {}) == -1.0
    assert mb.blend_mask_scale_for(blend) == 1.0
    assert mb.blend_mask_component_of(blend) == "R"


def test_builder_offset_override_reopens_the_layer():
    """`layerN_blend_mask_offset` is a runtime-ANIMATED parameter; the override
    picks a different point on that curve. It is not an intensity fudge -- it
    cannot scale anything, only slide the mask threshold."""
    mb = _mb()
    blend = _blend(_bridge_spec(), 1)
    assert mb.blend_mask_offset_for(blend, {"layer_blend_mask_offset": 0.0}) == 0.0
    assert mb.blend_amount_bounds(blend, {"layer_blend_mask_offset": 0.0}, True) == (0.0, 1.0)
    assert mb.blend_amount_constant(blend, {"layer_blend_mask_offset": 0.0}, True) is None
    # a bool must not be mistaken for a float override
    assert mb.blend_mask_offset_for(blend, {"layer_blend_mask_offset": True}) == -1.0


def test_builder_channel_blend_alpha_defaults_to_one():
    mb = _mb()
    blend = _blend(_bridge_spec(), 1)
    assert mb.channel_blend_alpha(blend, "emission") == 1.0
    spec = _bridge_spec(named_scalars_resolved={"layer1_lighting_blend_alpha": 0.25})
    assert mb.channel_blend_alpha(_blend(spec, 1), "emission") == 0.25


def test_builder_ignores_a_manifest_without_blend_records():
    """Old manifests have no `layers[*]["blend"]` -- nothing may crash or gate."""
    mb = _mb()
    legacy = {"layers": [{"index": 1, "channels": {}}],
              "channels": {"emission": {"layer": 1, "file": "x.dds"}}}
    assert mb.layer_blend_of(legacy, 1) is None
    assert mb.blend_for_channel(legacy, legacy["channels"], "emission") is None
    assert mb.blend_gates_channel(None, "emission") is False


def test_builder_specular_is_wired_to_specular_tint():
    """Supersedes the earlier "specular is not representable" verdict.

    That verdict said `specalbedo` (= F0, reaching 1.0) could not be expressed
    because `Specular IOR Level` is `hard_max = 1.0` -> F0 <= 0.08. Measured
    refutation on Blender 5.1.1 (Cycles + EEVEE): `Specular Tint` is
    `hard_max = FLT_MAX` and Principled's dielectric F0 is
    `F0(IOR) * 2 * level * tint`, linear and UNCLAMPED -- with the level at its
    0.5 neutral point and the tint at `F0 / F0(IOR)`, the rendered
    normal-incidence specular matched a Glossy BSDF of colour F0 to 0.00 % for
    every F0 in {0.01 .. 1.0}. See docs/MATERIALS.md and tests/test_specular.py.
    The builder now wires it, behind `opts['wire_specular']` (default True).
    """
    mb = _mb()
    src = MB_PATH.read_text(encoding="utf-8")
    assert '_principled_input(bsdf, "Specular Tint")' in src
    assert '_principled_input(bsdf, "Specular IOR Level")' in src
    assert mb.SPECULAR_IOR_LEVEL_NEUTRAL == 0.5
    assert mb.wire_specular_enabled(None) is True
    assert mb.wire_specular_enabled({"wire_specular": False}) is False
    assert hasattr(mb, "DEFAULT_BLEND_MASK_COMPONENT")


# ---------------------------------------------------------------------------
# 6. corpus sweep (skips itself without the gitignored fixtures)
# ---------------------------------------------------------------------------

def test_fixture_corpus_blend_records():
    """Corpus facts measured over a 51-package / 100-unique-material fixture
    export: 18 materials carry a blend mask, 9 of them also bind a layer>=1
    non-mask texture, and EVERY shipped `layerN_blend_mask_offset` is -1.0.
    Skipped when the (gitignored) fixture export is absent."""
    root = _fixtures()
    if root is None:
        return
    seen = {}
    for mf in sorted(root.glob("*.lemesh/manifest.json")):
        for spec in json.loads(mf.read_text(encoding="utf-8")).get("materials", []):
            seen[spec["key"]] = spec
    if not seen:
        return
    with_mask = offsets = gating = 0
    for spec in seen.values():
        layers = spec.get("layers") or []
        masked = [e for e in layers if "blend_mask" in (e.get("channels") or {})]
        if not masked:
            continue
        with_mask += 1
        for entry in masked:
            blend = entry.get("blend")
            if blend is None:              # manifest predates this key
                continue
            assert blend["layer"] == entry["index"]
            assert blend["mask_component"] == "R"
            assert "blend_mask" not in blend["gated_channels"]
            lo, hi = mat.blend_amount_bounds(blend["mask_scale"], blend["mask_offset"],
                                             blend["mask"] is not None)
            assert (lo, hi) == (blend["amount_min"], blend["amount_max"])
            if blend["mask_offset"] != mat.DEFAULT_BLEND_MASK_OFFSET:
                offsets += 1
                assert blend["mask_offset"] == -1.0, blend["mask_offset"]
            if blend["gated_channels"]:
                gating += 1
    assert with_mask > 0
    assert gating > 0
