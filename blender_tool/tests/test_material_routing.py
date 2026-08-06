"""Material ROUTING ground truth: roles -> channels -> Principled spec.

Companion to `test_transparency.py` (which locks the CSymbol64 preimages and the
SGMaterialData layout) and `test_materials.py` (which locks the scalar decode).
This file locks what `le_mesh.materials` does with those facts.

Pure stdlib (no oodle, no bpy). Every number here is either
`shader-confirmed` (matches the arithmetic the engine's own shaders perform),
`stream-confirmed` (decoded from shipped archive bytes) or explicitly labelled
`inferred`. The shipped-byte facts are embedded as literals so the suite runs on
a clean checkout — `blender_tool/exports/` is gitignored — and the optional
sweep over `exports/fixtures_mat/` skips itself when those packages are absent.
"""

import json
import struct
from pathlib import Path

from le_mesh import materials as mat
from le_mesh import material_scalars as msc


# ---------------------------------------------------------------------------
# Shipped fixture data (`stream-confirmed`, archive 0703fd2acd5803e9)
# ---------------------------------------------------------------------------

# Bridge material 0613ef69c99cbbc6 / shaderset b964375c606d812f.
# eMTForwardTransparent(2) + eBlendTranslucent(12). The emissive MAP is on
# layer 1 while layer0_emissive_intensity exists and is 2.0 — reading layer 0
# unconditionally made it 12.5x too dim — see docs/MATERIALS.md.
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
    "9cef9cbe9bc742ff": 72,    # BC1_UNORM_SRGB
    "9cef9cbe9bda5ff0": 83,    # BC5_UNORM
    "9cef9cbe9bdb42ff": 72,    # BC1_UNORM_SRGB
    "cf07d65049f874e7": 80,    # BC4_UNORM
    "0408e9e71fdebd0f": 72,    # BC1_UNORM_SRGB
    "63c63b9027cf5e8b": 72,    # BC1_UNORM_SRGB
}
BRIDGE_NAMED_SCALARS = {
    "c6f8f070a09880a0": -1.0,
    "c7e40edd6f299f19": 2.0,      # layer2_emissive_intensity
    "25f0f7652abbc480": -0.2,     # layer1_emissive_map_voffset
    "2f0e118582db9c08": -1.0,
    "31e35f7a5feb8441": 2.0,      # layer0_emissive_intensity
    "516b9827ccc13de3": 25.0,     # layer1_emissive_intensity  <-- the right one
}
BRIDGE_SCALARS = {
    "base_color_factor": [1.0, 1.0, 1.0, 1.0],
    "emissive_color": [0.0, 0.0, 0.0],
    "emissive_intensity": 2.0,          # what the old layer0-wins decode produced
    "alpha": 1.0,
    "blend_mode": 12,
    "mattype": 2,
    "double_sided": False,
    "named_scalars": BRIDGE_NAMED_SCALARS,
}

# Observed (mattype, blendmode) joint distribution, `stream-confirmed`
# (reproduced over the 51 fixture packages).
OBSERVED_MODE_PAIRS = {
    (0, 0): ("OPAQUE", False),     # eMTDeferredOpaque   / eBlendOpaque (unresolved default)
    (1, 0): ("OPAQUE", False),     # eMTForwardOpaque    / eBlendOpaque
    (9, 0): ("CLIP", False),       # eMTAlphaTested      / eBlendOpaque  (clip(), not a blend)
    (2, 7): ("BLEND", False),      # eMTForwardTransparent / eBlendTransparent
    (2, 12): ("BLEND", False),     # eMTForwardTransparent / eBlendTranslucent
    (16, 8): ("BLEND", True),      # eMTTransparentPostAA / eBlendLinearDodge -> LOSSY
    (10, 10): ("BLEND", False),    # eMTSkirt / eBlendSkirt -- the DECAL pass
}

FIXTURES = Path(__file__).resolve().parents[1] / "exports" / "fixtures_mat"


def _bridge_spec():
    return mat.build_material_spec(
        BRIDGE_KEY, shaderset_hash="b964375c606d812f",
        material_hash="0613ef69c99cbbc6",
        role_textures=BRIDGE_ROLE_TEXTURES, dxgi_by_tex=BRIDGE_DXGI,
        scalars=BRIDGE_SCALARS, texture_files={})


def _dds_dxgi(path: Path):
    data = path.read_bytes()[:140]
    if len(data) < 132 or data[:4] != b"DDS " or data[84:88] != b"DX10":
        return None
    return struct.unpack_from("<I", data, 128)[0]


def _fixture_materials():
    """[(spec, manifest_material)] over exports/fixtures_mat, or [] if absent."""
    out = []
    if not FIXTURES.is_dir():
        return out
    for manifest in sorted(FIXTURES.glob("*.lemesh/manifest.json")):
        pkg = manifest.parent
        dxgi = {}
        tex_dir = pkg / "textures"
        if tex_dir.is_dir():
            for dds in tex_dir.glob("*.dds"):
                fmt = _dds_dxgi(dds)
                if fmt is not None:
                    dxgi[dds.stem] = fmt
        for m in json.loads(manifest.read_text()).get("materials", []):
            scalars = {k: m[k] for k in
                       ("base_color_factor", "emissive_color", "emissive_intensity",
                        "alpha", "blend_mode", "double_sided", "mattype", "flags",
                        "flag_names", "materialfx", "is_emissive", "named_scalars")
                       if k in m}
            spec = mat.build_material_spec(
                m["key"], shaderset_hash=m.get("shaderset_hash", ""),
                material_hash=m.get("material_hash", ""),
                role_textures=m.get("role_textures", {}), dxgi_by_tex=dxgi,
                scalars=scalars,
                texture_files={t: f"textures/{t}.dds" for t in dxgi})
            out.append((spec, m))
    return out


# ---------------------------------------------------------------------------
# 1. The two roles that were cracked but never wired in
# ---------------------------------------------------------------------------

def test_new_role_preimages_are_real():
    """`layer0_alpha_map` / `layer0_secondary_emissive_map` (9 corpus rows each)."""
    expected = {
        "9dba2dc44433be64": "layer0_alpha_map",
        "571b8c6b2599c12a": "layer0_secondary_emissive_map",
    }
    for hexhash, name in expected.items():
        assert f"{msc.symbol64(name):016x}" == hexhash, f"{name} != {hexhash}"
        assert mat.INPUTNAME_ROLE[hexhash] == (name, "confirmed")


def test_new_roles_actually_route():
    roles = {"layer0_alpha_map": "a1", "layer0_secondary_emissive_map": "e2"}
    ch = mat.classify_roles(roles, {"a1": 80, "e2": 72})
    assert ch["alpha"]["role_key"] == "layer0_alpha_map"
    assert ch["secondary_emission"]["role_key"] == "layer0_secondary_emissive_map"
    # neither may end up as an unknown_s* slot any more
    spec = mat.build_material_spec("k", role_textures=roles,
                                   dxgi_by_tex={"a1": 80, "e2": 72})
    assert spec["unrouted_roles"] == []


# ---------------------------------------------------------------------------
# 2. `opacity_map` is a transmission tint, NOT alpha
# ---------------------------------------------------------------------------

def test_opacity_map_is_transmission_not_alpha():
    """`color.rgb += background * opacity` — float3 tint (`shader-confirmed`)."""
    assert "layer0_opacity_map" not in mat.ALPHA_ROLES
    assert "layer1_opacity_map" not in mat.ALPHA_ROLES
    assert mat.TRANSMISSION_ROLES == ["layer0_opacity_map", "layer1_opacity_map"]
    # OPACITY_ROLES survives only as a deprecated alias of TRANSMISSION_ROLES
    assert mat.OPACITY_ROLES == mat.TRANSMISSION_ROLES

    ch = mat.classify_roles({"layer0_opacity_map": "op"}, {"op": 72})
    assert ch["transmission"]["role_key"] == "layer0_opacity_map"
    assert ch["transmission"]["is_transmission_tint"] is True
    # no alpha channel may be manufactured from it
    assert "alpha" not in ch
    # the back-compat mirror is present but explicitly marked
    assert ch["opacity"]["deprecated"] is True
    assert "Alpha" in ch["opacity"]["deprecated_note"]


def test_opacity_map_never_becomes_alpha_source():
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_opacity_map": "op"}, dxgi_by_tex={"op": 72},
        scalars={"mattype": 2, "blend_mode": 12})
    assert spec["alpha_source"] == "NONE"
    assert "transmission" in spec["channels"]


# ---------------------------------------------------------------------------
# 3. The alpha chain (`shader-confirmed`, the engine's ubershader)
# ---------------------------------------------------------------------------

def test_alpha_source_alpha_map():
    spec = mat.build_material_spec(
        "k", role_textures={"layer1_alpha_map": "am"}, dxgi_by_tex={"am": 80},
        scalars={"mattype": 2, "blend_mode": 12})
    assert spec["alpha_source"] == "ALPHA_MAP"
    assert spec["channels"]["alpha"]["role_key"] == "layer1_alpha_map"
    # BC4 is single-channel, so the scalar can only be R
    assert spec["channels"]["alpha"]["scalar_channel"] == "R"


def test_alpha_source_base_color_alpha():
    """`alpha = composite_diffuse.w * vertexcolor.w` (`shader-confirmed`)."""
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_composite_diffuse": "cd"},
        dxgi_by_tex={"cd": 78},                 # BC3_UNORM_SRGB: real 8-bit alpha
        scalars={"mattype": 9, "blend_mode": 0})   # eMTAlphaTested -> CLIP
    assert spec["alpha_source"] == "BASE_COLOR_ALPHA"
    alpha = spec["channels"]["alpha"]
    assert alpha["texture"] == "cd"
    assert alpha["alpha_channel"] == "A"
    assert alpha["from_channel"] == "base_color"
    assert alpha["punchthrough"] is False
    # BC1 carries only 1 bit of alpha and must say so
    spec_bc1 = mat.build_material_spec(
        "k", role_textures={"layer0_composite_diffuse": "cd"},
        dxgi_by_tex={"cd": 72}, scalars={"mattype": 9, "blend_mode": 0})
    assert spec_bc1["channels"]["alpha"]["punchthrough"] is True


def test_alpha_source_scalar_only_and_none():
    scalar = mat.build_material_spec(
        "k", role_textures={"layer0_composite_normals": "nm"},
        dxgi_by_tex={"nm": 83},                 # BC5_UNORM: no alpha anywhere
        scalars={"mattype": 2, "blend_mode": 7, "alpha": 0.25})
    assert scalar["alpha_source"] == "SCALAR_ONLY"    # k_alpha alone
    assert scalar["alpha"] == 0.25
    assert "alpha" not in scalar["channels"]         # nothing to sample

    none = mat.build_material_spec(
        "k", role_textures={"layer0_composite_diffuse": "cd"},
        dxgi_by_tex={"cd": 78}, scalars={"mattype": 1, "blend_mode": 0})
    # an OPAQUE material's base-colour alpha is not transparency
    assert none["alpha_source"] == "NONE"
    assert "alpha" not in none["channels"]


def test_alpha_terms_are_a_product_not_a_choice():
    """The engine MULTIPLIES the terms; `alpha_source` is only the primary one."""
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_composite_diffuse": "cd", "layer0_alpha_map": "am"},
        dxgi_by_tex={"cd": 78, "am": 80},
        scalars={"mattype": 2, "blend_mode": 12, "alpha": 0.5})
    assert spec["alpha_source"] == "ALPHA_MAP"
    assert spec["alpha_terms"] == ["ALPHA_MAP", "BASE_COLOR_ALPHA", "SCALAR_ONLY"]


# ---------------------------------------------------------------------------
# 4. Multi-layer routing — layer 1+ must stop being silently dropped
# ---------------------------------------------------------------------------

def test_multi_layer_emissive_both_layers_routed():
    roles = {"layer0_emissive_map": "e0", "layer1_emissive_map": "e1"}
    spec = mat.build_material_spec("k", role_textures=roles,
                                   dxgi_by_tex={"e0": 72, "e1": 72})
    assert [layer["index"] for layer in spec["layers"]] == [0, 1]
    assert spec["layers"][0]["channels"]["emission"]["texture"] == "e0"
    assert spec["layers"][1]["channels"]["emission"]["texture"] == "e1"
    # merged view keeps the old lowest-layer-wins behaviour
    assert spec["channels"]["emission"]["texture"] == "e0"
    assert spec["channels"]["emission"]["layer"] == 0
    assert spec["unrouted_roles"] == []


def test_bridge_material_routes_layer1_emissive_and_composite_specular():
    """On the reference material these two came back UNROUTED."""
    spec = _bridge_spec()
    assert spec["unrouted_roles"] == []
    # layer1_emissive_map is now the routed emissive (layer 0 has no emissive map)
    assert spec["channels"]["emission"]["role_key"] == "layer1_emissive_map"
    assert spec["channels"]["emission"]["layer"] == 1
    # layer2_emissive_map is kept, on its own layer, instead of being dropped
    assert spec["layers"][-1]["index"] == 2
    assert spec["layers"][-1]["channels"]["emission"]["role_key"] == "layer2_emissive_map"

    # ...and a composite_specular is routed to `specular`, not dropped
    comp = mat.build_material_spec(
        "k", role_textures={"layer0_composite_diffuse": "cd",
                            "layer0_composite_specular": "cs"},
        dxgi_by_tex={"cd": 72, "cs": 78})
    assert comp["channels"]["specular"]["role_key"] == "layer0_composite_specular"
    assert comp["unrouted_roles"] == []


def test_fixture_corpus_routes_every_known_role():
    """Sweep the 51 shipped fixture packages, if present (gitignored)."""
    rows = _fixture_materials()
    if not rows:
        return                     # clean checkout: nothing to sweep
    known = {role for role, _c in mat.INPUTNAME_ROLE.values()}
    leftovers = set()
    for spec, _m in rows:
        leftovers |= {r for r in spec["unrouted_roles"] if r in known}
    assert not leftovers, f"cracked roles left unrouted: {sorted(leftovers)}"

    by_key = {}
    for spec, _m in rows:
        by_key.setdefault(spec["key"], spec)
    assert len(by_key) == 100, f"fixture corpus changed shape: {len(by_key)}"
    assert BRIDGE_KEY in by_key
    assert by_key[BRIDGE_KEY]["emissive_intensity"] == 25.0
    # counts reported in the A1 delta; locked so a routing regression is visible
    assert sum(1 for s in by_key.values() if "alpha" in s["channels"]) == 14
    assert sum(1 for s in by_key.values() if "transmission" in s["channels"]) == 9
    assert sum(1 for s in by_key.values() if "specular" in s["channels"]) == 28
    assert sum(1 for s in by_key.values()
               if any(layer["index"] >= 1 for layer in s["layers"])) == 33


# ---------------------------------------------------------------------------
# 5. render_mode from (mattype, blendmode)
# ---------------------------------------------------------------------------

def test_render_mode_for_every_observed_pair():
    for (mattype, blend), expected in OBSERVED_MODE_PAIRS.items():
        got = mat.render_mode_for(mattype, blend)
        assert got == expected, f"({mattype},{blend}) -> {got}, want {expected}"


def test_render_mode_additive_is_flagged_lossy():
    """EEVEE has no additive blend — flag the loss, do not fake it."""
    for blend in (1, 8):                       # eBlendAdditive / eBlendLinearDodge
        mode, lossy = mat.render_mode_for(1, blend)
        assert (mode, lossy) == ("BLEND", True)
    assert mat.render_mode_for(1, 0) == ("OPAQUE", False)


def test_render_mode_reaches_the_spec():
    spec = _bridge_spec()
    assert spec["render_mode"] == "BLEND"
    assert spec["alpha_blend_lossy"] is False
    assert spec["mattype_name"] == "eMTForwardTransparent"
    assert spec["blend_mode_name"] == "eBlendTranslucent"


def test_alpha_tested_is_clip_not_blend():
    """`eMTAlphaTested` always ships with `eBlendOpaque` — the cutout is clip()."""
    assert mat.render_mode_for(9, 0) == ("CLIP", False)


# ---------------------------------------------------------------------------
# 6. Emissive layer selection — the single biggest emissive error
# ---------------------------------------------------------------------------

def test_bridge_emissive_intensity_is_25_not_2():
    spec = _bridge_spec()
    assert spec["emissive_layer"] == 1
    assert spec["emissive_intensity"] == 25.0      # was 2.0 => 12.5x too dim
    assert spec["emissive_scale"] == 1.0           # k_emissive_scale absent -> 1.0
    # per-layer breakdown carries each layer's own intensity
    per_layer = {layer["index"]: layer["emissive_intensity"] for layer in spec["layers"]}
    assert per_layer[1] == 25.0
    assert per_layer[2] == 2.0


def test_emissive_intensity_reads_a2_layers_when_present():
    """A2 may emit a per-layer `layers[]`; it wins over the raw hash lookup."""
    scalars = dict(BRIDGE_SCALARS)
    scalars["layers"] = [{"index": 0, "emissive_intensity": 2.0},
                         {"index": 1, "emissive_intensity": 25.0}]
    scalars["emissive_scale"] = 3.0
    spec = mat.build_material_spec(BRIDGE_KEY, role_textures=BRIDGE_ROLE_TEXTURES,
                                   dxgi_by_tex=BRIDGE_DXGI, scalars=scalars)
    assert spec["emissive_intensity"] == 25.0
    assert spec["emissive_scale"] == 3.0


def test_emissive_intensity_falls_back_without_a2():
    """No emissive map routed -> keep the scalar decoder's own value."""
    spec = mat.build_material_spec("k", role_textures={}, dxgi_by_tex={},
                                   scalars={"emissive_intensity": 6.0})
    assert spec["emissive_layer"] is None
    assert spec["emissive_intensity"] == 6.0


# ---------------------------------------------------------------------------
# 7. composite_specular -> `specular`, not roughness and not base colour
# ---------------------------------------------------------------------------

def test_composite_specular_is_its_own_channel():
    assert "layer0_composite_specular" not in mat.ROUGHNESS_ROLES
    assert "layer0_composite_specular" not in mat.BASE_COLOR_ROLES
    assert "layer0_composite_specular" in mat.SPECULAR_ROLES
    ch = mat.classify_roles({"layer0_composite_specular": "cs"}, {"cs": 78})
    spec_ch = ch["specular"]
    # specintensity = .w ; specalbedo = .xyz * .w  (`shader-confirmed`)
    assert spec_ch["spec_intensity_channel"] == "A"
    assert spec_ch["spec_albedo_channel"] == "RGB"
    assert spec_ch["spec_albedo_scaled_by"] == "A"


def test_specular_map_is_not_base_color():
    """8 corpus materials carried one; none carried a diffuse (`stream-confirmed`)."""
    assert "layer0_specular_map" not in mat.BASE_COLOR_ROLES
    assert "layer0_specular_map" in mat.SPECULAR_ROLES
    spec = _bridge_spec()
    assert spec["channels"]["specular"]["role_key"] == "layer0_specular_map"
    assert "base_color" not in spec["channels"]


def test_specular_map_packing_is_fresnel_scaled_not_alpha_scaled():
    """A10: `layer0_specular_map` is the SAME slot as `composite_specular.xyz`
    (both land in `layers[i].specalbedo[0]`, which is F0) but a DIFFERENT scale.

    `specularalbedo[0] = params.specular * params.speculartint *
    params.specularmap * params.specintensity[0]` (`shader-confirmed`) with
    `params.specintensity[0] = k_fresnel[i]` and
    `output.specalbedo[0] = params.layer * specularalbedo[0].xyz` -- so the
    scale is the material scalar `k_fresnel` (authored 0.010 in the engine's
    ubermaterial), NOT the map's own alpha.
    """
    ch = mat.classify_roles({"layer0_specular_map": "sm"}, {"sm": 72})["specular"]
    assert ch["spec_albedo_channel"] == "RGB"
    assert ch["spec_albedo_scaled_by"] == "fresnel"
    assert ch["spec_intensity_source"] == "fresnel"
    assert ch["spec_fresnel_default"] == mat.SPEC_MAP_FRESNEL_DEFAULT == 0.01
    assert "fresnel" in ch["packing"]
    # and the composite sibling keeps ITS scale, the alpha channel
    cs = mat.classify_roles({"layer0_composite_specular": "cs"}, {"cs": 78})["specular"]
    assert cs["spec_albedo_scaled_by"] == "A"
    assert cs.get("spec_intensity_source") is None


# ---------------------------------------------------------------------------
# 8. components.x is sqrt-space; AO is .G
# ---------------------------------------------------------------------------

def test_roughness_is_sqrt_and_ao_channel():
    """sqrtroughness[0] = components.x; RAD's own roughness = x^2.

    ⚠ `roughness_is_sqrt` describes the TEXEL SPACE, not a Blender conversion.
    Blender's GGX alpha is `Roughness^2` and RAD's is `sqrtroughness^2`
    (`shader-confirmed`), so the Blender socket
    takes components.x RAW. Squaring it there is a bug -- guarded in
    tests/blender_material_probe.py.
    """
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_composite_components": "cc"},
        dxgi_by_tex={"cc": 71})
    assert spec["roughness_is_sqrt"] is True
    assert spec["ao_channel"] == "G"
    rough = spec["channels"]["roughness"]
    assert rough["roughness_channel"] == "R"
    assert rough["roughness_is_sqrt"] is True
    assert rough["ao_channel"] == "G"


def test_roughness_flags_absent_without_components_map():
    spec = mat.build_material_spec("k", role_textures={}, dxgi_by_tex={})
    assert spec["roughness_is_sqrt"] is False
    assert spec["ao_channel"] is None


# ---------------------------------------------------------------------------
# 9. Colour space and alpha_mode
# ---------------------------------------------------------------------------

def test_colorspace_of_the_new_roles():
    # BC4_UNORM alpha map: linear data
    assert mat.colorspace_for(80, "layer0_alpha_map") == "Non-Color"
    # BC1_UNORM_SRGB secondary emissive: the sampler linearises it, so must Blender
    assert mat.colorspace_for(72, "layer0_secondary_emissive_map") == "sRGB"
    # BC1_UNORM (no _SRGB) emissive: the sampler does NOT linearise it
    assert mat.colorspace_for(71, "layer0_secondary_emissive_map") == "Non-Color"


def test_normals_are_never_srgb():
    for dxgi in (83, 72, 78, 99, None):
        assert mat.colorspace_for(dxgi, "layer0_composite_normals") == "Non-Color"
        assert mat.colorspace_for(dxgi, "layer0_normal_map") == "Non-Color"


def test_alpha_is_always_linear_even_in_an_srgb_texture():
    assert mat.alpha_is_linear(78, "layer0_composite_diffuse") is True
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_composite_diffuse": "cd"},
        dxgi_by_tex={"cd": 78}, scalars={"mattype": 9, "blend_mode": 0})
    base = spec["channels"]["base_color"]
    assert base["colorspace"] == "sRGB"           # RGB is sRGB...
    assert base["alpha_is_linear"] is True        # ...its alpha is not
    assert spec["channels"]["alpha"]["alpha_is_linear"] is True


def test_alpha_mode_hints():
    """STRAIGHT silently corrupts albedo when alpha is packed data."""
    assert mat.alpha_mode_for("layer0_composite_diffuse") == "CHANNEL_PACKED"
    assert mat.alpha_mode_for("layer0_albedo_map") == "CHANNEL_PACKED"
    assert mat.alpha_mode_for("layer1_emissive_map") == "CHANNEL_PACKED"
    assert mat.alpha_mode_for("layer0_secondary_emissive_map") == "CHANNEL_PACKED"
    assert mat.alpha_mode_for("layer0_alpha_map") == "CHANNEL_PACKED"
    assert mat.alpha_mode_for("layer0_composite_specular") == "CHANNEL_PACKED"
    # ⚠ MOVED from the "never read" list below by the two-lobe fix: we DO read
    # `.w` of both of these now -- `sqrtroughness[1] = k_composite_components[i].w`
    # and `specintensity[1] = k_composite_data0[i].w`, both in the engine's
    # ubershader. The hint is a statement about what the alpha MEANS; leaving it at
    # NONE while reading the plane would have been the dishonest half of the pair.
    assert mat.alpha_mode_for("layer0_composite_components") == "CHANNEL_PACKED"
    assert mat.alpha_mode_for("layer0_composite_data0") == "CHANNEL_PACKED"
    # alpha we never read -> ignore it entirely rather than un-premultiply RGB
    assert mat.alpha_mode_for("layer0_composite_normals") == "NONE"
    assert mat.alpha_mode_for("layer0_opacity_map") == "NONE"
    assert mat.alpha_mode_for("layer1_blend_mask") == "NONE"
    # every emitted channel carries the hint
    for ch in _bridge_spec()["channels"].values():
        assert ch["alpha_mode"] in ("CHANNEL_PACKED", "STRAIGHT", "NONE")


def test_straight_alpha_is_never_auto_selected():
    """Choosing STRAIGHT needs `premultiplied_alpha_`/`use_output_alpha_`, which
    are shader-permutation bits and are NOT on disk."""
    for role, _conf in mat.INPUTNAME_ROLE.values():
        assert mat.alpha_mode_for(role) != mat.ALPHA_MODE_STRAIGHT


# ---------------------------------------------------------------------------
# 10. Hygiene + back-compat
# ---------------------------------------------------------------------------

def test_dxgi_fallback_does_not_emit_a_fabricated_role_name():
    """The old fallback labelled unknown slots `layer0_diffuse_map` — which is one
    of the FABRICATED names from the §3 incident. Keep the real slot name."""
    spec = mat.build_material_spec("k", role_textures={"unknown_s7": "u1"},
                                   dxgi_by_tex={"u1": 72})
    base = spec["channels"]["base_color"]
    assert base["role_key"] == "unknown_s7"
    assert base["inferred_from"] == "dxgi"
    assert base["confidence"] == "tentative"


def test_every_channel_carries_layer_and_alpha_mode():
    spec = _bridge_spec()
    for name, ch in spec["channels"].items():
        assert isinstance(ch["layer"], int), name
        assert "alpha_mode" in ch, name
        assert "file" in ch, name
    for layer in spec["layers"]:
        for name, ch in layer["channels"].items():
            assert ch["layer"] == layer["index"], name
            assert "file" in ch, name


def test_old_spec_keys_are_unchanged():
    """Old consumers keep working: everything is additive."""
    spec = _bridge_spec()
    for key in ("key", "shaderset_hash", "material_hash", "double_sided",
                "blend_mode", "base_color_factor", "emissive_color",
                "emissive_intensity", "alpha", "channels", "role_textures"):
        assert key in spec, key
    assert spec["blend_mode"] == 12
    assert spec["base_color_factor"] == [1.0, 1.0, 1.0, 1.0]


def test_works_with_an_empty_scalar_dict():
    """A2's extended keys may not have landed yet — nothing may be required."""
    spec = mat.build_material_spec("k", role_textures=BRIDGE_ROLE_TEXTURES,
                                   dxgi_by_tex=BRIDGE_DXGI, scalars={})
    assert spec["render_mode"] == "OPAQUE"      # mattype/blend default to 0
    assert spec["alpha_threshold"] is None
    assert spec["ior"] is None
    assert spec["emissive_scale"] == 1.0
    assert spec["emissive_intensity"] == 1.0


def test_named_scalars_resolved_is_honoured():
    scalars = dict(BRIDGE_SCALARS)
    scalars["named_scalars_resolved"] = {
        "layer1_emissive_intensity": 7.0,
        "k_alpha_threshold": 0.25,
        "k_refractive_index": 1.52,
        "k_emissive_scale": 2.0,
    }
    spec = mat.build_material_spec(BRIDGE_KEY, role_textures=BRIDGE_ROLE_TEXTURES,
                                   dxgi_by_tex=BRIDGE_DXGI, scalars=scalars)
    assert spec["emissive_intensity"] == 7.0
    assert spec["alpha_threshold"] == 0.25
    assert spec["ior"] == 1.52
    assert spec["emissive_scale"] == 2.0
    assert spec["alpha_threshold_default"] == 0.5
