"""X2 — WHICH COMPONENT carries the alpha, and the base-colour routing that
depends on it.

`layer1_alpha_map` ships in FOUR DXGI formats in archive `0703fd2acd5803e9`
(BC1_UNORM 71, BC1_UNORM_SRGB 72, BC3_UNORM_SRGB 78, BC4_UNORM 80). Before this
file the consumer inferred the component from the format — "the format has an
alpha block, so read `.a`" — which is wrong for the BC3 one and right by luck
for the other three. The engine answers it outright:

    params.alphamap = k_alpha_map[i].x;        <-- RED, every format
                                                             (`shader-confirmed`)

    layers[i].alpha = k_composite_diffuse[i].w * params.albedovertex.w;
                                                             (`shader-confirmed`)

Pure stdlib (no oodle, no bpy). Every shipped-byte number is embedded as a
literal so the suite runs on a clean checkout — `blender_tool/exports/` is
gitignored — and the optional sweeps skip themselves when it is absent.

Evidence labels: `shader-confirmed` (matches the arithmetic the engine's own
shaders perform) · `stream-confirmed` (block-decoded shipped DDS bytes) ·
`inferred`.
"""

import json
import struct
from pathlib import Path

from le_mesh import materials as mat
from le_mesh import material_scalars as msc

EXPORTS = Path(__file__).resolve().parents[1] / "exports"
FIXTURES = EXPORTS / "fixtures_mat3"


# ---------------------------------------------------------------------------
# Shipped-byte literals (`stream-confirmed`, archive 0703fd2acd5803e9,
# exports/fixtures_mat3, 51 packages / 100 unique materials)
# ---------------------------------------------------------------------------

# role -> {dxgi: how many materials bind it in that format}
ALPHA_ROLE_FORMAT_CENSUS = {
    "layer0_alpha_map":         {71: 9},
    "layer1_alpha_map":         {72: 1, 78: 1, 80: 9},
    "layer0_composite_diffuse": {72: 14, 78: 5},
    "layer1_composite_diffuse": {72: 1},
    "layer0_opacity_map":       {72: 9},
    "layer1_opacity_map":       {72: 1},
}

# The four alpha-map textures, one per shipped format.
ALPHA_MAP_TEXTURES = {
    71: "69eb4e694b6a13f1",     # layer0_alpha_map, 1024^2, BC1_UNORM
    72: "e738bd05b6f52a44",     # layer1_alpha_map, 1024^2, BC1_UNORM_SRGB
    78: "494a47bd33bb1e20",     # layer1_alpha_map,  512^2, BC3_UNORM_SRGB
    80: "bb897558f047959b",     # layer1_alpha_map, 1024^2, BC4_UNORM
}

# `stream-confirmed`: EVERY BC1 texture in the fixture corpus, fully block-decoded
# at mip 0 — 42 distinct textures, 4,100,096 blocks, 65,601,536 texels, spanning
# 14 distinct roles. 1,155,487 blocks (28.2 %) are in `c0 <= c1` mode, and index
# 3 — the only way BC1 can emit alpha != 1 — is selected ZERO times. BC1 alpha is
# authored nowhere in this archive; the mode is a compressor quality choice.
BC1_TEXTURES = 42
BC1_TEXELS_DECODED = 65_601_536
BC1_PUNCHTHROUGH_BLOCKS = 1_155_487
BC1_TRANSPARENT_TEXELS = 0

# `stream-confirmed`, `494a47bd33bb1e20` (the BC3_UNORM_SRGB alpha map),
# 262,144 texels: the alpha plane and the red plane are the SAME mask
# (pearson +0.9942, mean chroma spread |R-G|+|G-B| = 1.17 of 255) stored twice,
# but they are not the same VALUE — mean raw alpha 44.7/255 = 0.175 against
# mean sRGB-decoded red 0.036. Reading `.a` is ~4.8x too strong.
BC3_ALPHA_MAP_PEARSON_A_R = 0.9942
BC3_ALPHA_MAP_MEAN_RAW_ALPHA_255 = 44.7
BC3_ALPHA_MAP_MEAN_RAW_RED_255 = 53.7

# `stream-confirmed`, `4dc6b9d29339295f` — a BC3_UNORM_SRGB *composite diffuse*
# on an `eMTAlphaTested` material. Here `.a` is a genuinely independent signal
# from the colour: pearson(A, R) = -0.7678 and the alpha histogram is bimodal
# (1,807,436 texels in [0,16) and 1,367,293 in [240,256) of 3,200,000). That is
# a cutout mask, and it is why `composite_diffuse.a` IS the opacity.
BC3_COMPOSITE_DIFFUSE_PEARSON_A_R = -0.7678

# BC4_UNORM stores ONE 8-byte block plane per 4x4 and the SRV returns
# (r, 0, 0, 1) — mip 0 of `bb897558f047959b.dds` is exactly 256*256*8 bytes, so
# there is no alpha DATA in the file to read. Reading `.a` yields constant 1.0,
# i.e. the alpha map becomes a no-op. 9 of the 11 `layer1_alpha_map` materials
# are in this format, so this is the failure X2 was opened to prevent.
BC4_BLOCK_BYTES = 8

# S1 (`stream-confirmed`, same corpus): `layer0_specular_map` and any
# diffuse/albedo role are mutually exclusive, so the ordering of BASE_COLOR_ROLES
# never resolved a conflict — the entry was simply routing specular into Base
# Color. All 8 carriers are eMTForwardTransparent(2)/eBlendTranslucent(12).
SPECULAR_MAP_CARRIERS = 8
SPECULAR_MAP_CARRIERS_WITH_DIFFUSE = 0
ALBEDO_MAP_CARRIERS = 2
COMPOSITE_DIFFUSE_CARRIERS = 20          # 19 layer0 + 1 layer1
ALBEDO_AND_COMPOSITE_BOTH = 0

# The misparsed scan rows (`stream-confirmed`,
# generic_rebuilds/shaderset_texture_scan.tsv:2 and :33).
ARTEFACT_SHADERSETS = ("80a6642707ce0367", "05575a94091f1839")
ARTEFACT_TEXTURE = "a33d0790d3cbab49"


def _fixture_specs():
    """[(package_dir, material_spec)] from exports/fixtures_mat3, or []."""
    if not FIXTURES.is_dir():
        return []
    out, seen = [], set()
    for manifest in sorted(FIXTURES.glob("*.lemesh/manifest.json")):
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for spec in data.get("materials", []):
            if spec.get("key") in seen:
                continue
            seen.add(spec.get("key"))
            out.append((manifest.parent, spec))
    return out


# ---------------------------------------------------------------------------
# 1. The DXGI enum itself — the recollection in the brief was wrong
# ---------------------------------------------------------------------------

def test_dxgi_format_numbers_are_the_real_enum():
    """72 is BC1_UNORM_SRGB, not BC2. Getting this backwards is how a
    1-bit-punchthrough DXT1 gets treated as a DXT3 with a real alpha block."""
    # BC1 = 70/71/72, BC2 = 73/74/75, BC3 = 76/77/78, BC4 = 79/80/81, BC5 = 82/83/84
    assert mat.PUNCHTHROUGH_ALPHA_DXGI == frozenset({70, 71, 72})     # BC1
    assert mat.BC5_DXGI == frozenset({82, 83, 84})
    assert mat.SINGLE_CHANNEL_DXGI >= {79, 80, 81}                    # BC4
    # the `_SRGB` views, and only those, linearise RGB in the sampler
    assert 72 in mat.SRGB_DXGI and 71 not in mat.SRGB_DXGI            # BC1
    assert 75 in mat.SRGB_DXGI and 74 not in mat.SRGB_DXGI            # BC2
    assert 78 in mat.SRGB_DXGI and 77 not in mat.SRGB_DXGI            # BC3
    assert 80 not in mat.SRGB_DXGI                                    # BC4 never


def test_alpha_plane_excludes_bc1_and_bc4():
    """A real alpha PLANE means `.a` returns authored data, not a constant."""
    for dxgi in (74, 75, 77, 78, 98, 99, 28, 29):
        assert dxgi in mat.ALPHA_PLANE_DXGI, dxgi
    for dxgi in (70, 71, 72, 79, 80, 81, 83):
        assert dxgi not in mat.ALPHA_PLANE_DXGI, dxgi
    # BC1 stays in ALPHA_CAPABLE_DXGI: the engine really does read `.w` there,
    # it just only has 1 bit of it. The two sets say different things.
    assert mat.PUNCHTHROUGH_ALPHA_DXGI < mat.ALPHA_CAPABLE_DXGI
    assert not (mat.PUNCHTHROUGH_ALPHA_DXGI & mat.ALPHA_PLANE_DXGI)


# ---------------------------------------------------------------------------
# 2. X2 — the component, from the shader
# ---------------------------------------------------------------------------

def test_alpha_map_component_is_red_in_every_shipped_format():
    """`params.alphamap = k_alpha_map[i].x` — `shader-confirmed`.

    All four shipped formats, and the answer never depends on the format.
    """
    for dxgi in sorted(ALPHA_MAP_TEXTURES):
        spec = mat.build_material_spec(
            "k", role_textures={"layer1_alpha_map": "am"}, dxgi_by_tex={"am": dxgi},
            scalars={"mattype": 2, "blend_mode": 7})
        alpha = spec["channels"]["alpha"]
        assert alpha["component"] == "R", (dxgi, alpha.get("component"))
        assert alpha["scalar_channel"] == "R"
        assert alpha["scalar_channel_from"] == "shader"
        assert spec["alpha_source"] == "ALPHA_MAP"
    # ...and the same for layer 0, whose role name was cracked in the same pass
    spec0 = mat.build_material_spec(
        "k", role_textures={"layer0_alpha_map": "am"}, dxgi_by_tex={"am": 71},
        scalars={"mattype": 2, "blend_mode": 7})
    assert spec0["channels"]["alpha"]["component"] == "R"


def test_alpha_map_component_is_not_derived_from_the_format():
    """The regression this closes: a format-driven guess says `.a` for BC3."""
    bc3 = mat.build_material_spec(
        "k", role_textures={"layer1_alpha_map": "am"}, dxgi_by_tex={"am": 78},
        scalars={"mattype": 2, "blend_mode": 7})["channels"]["alpha"]
    # BC3 HAS a real alpha plane...
    assert bc3["alpha_plane"] is True
    # ...and the signal is STILL in R.
    assert bc3["component"] == "R"
    # BC4 has no alpha data at all, so `.a` would read a hardware constant 1.0.
    bc4 = mat.build_material_spec(
        "k", role_textures={"layer1_alpha_map": "am"}, dxgi_by_tex={"am": 80},
        scalars={"mattype": 2, "blend_mode": 7})["channels"]["alpha"]
    assert bc4["alpha_plane"] is False
    assert bc4["single_channel_format"] is True
    assert bc4["component"] == "R"


def test_base_colour_alpha_component_is_A():
    """`layers[i].alpha = k_composite_diffuse[i].w` — the ONE `.a` read."""
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_composite_diffuse": "cd"},
        dxgi_by_tex={"cd": 78}, scalars={"mattype": 9, "blend_mode": 0})
    assert spec["alpha_source"] == "BASE_COLOR_ALPHA"
    alpha = spec["channels"]["alpha"]
    assert alpha["component"] == mat.BASE_COLOR_ALPHA_COMPONENT == "A"
    assert alpha["alpha_channel"] == "A"
    assert alpha["alpha_plane"] is True
    assert alpha["punchthrough"] is False
    # The base-colour channel it was derived FROM still reports RGB — the
    # `dict(base)` copy must not leak "RGB" into the alpha channel.
    assert spec["channels"]["base_color"]["component"] == "RGB"


def test_bc1_derived_alpha_is_flagged_as_having_no_plane():
    """BC1 punchthrough: the engine still reads `.w`, but there is 1 bit of it
    and the corpus never sets it (`stream-confirmed`, 0 / 65,601,536 texels)."""
    assert BC1_TRANSPARENT_TEXELS == 0
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_composite_diffuse": "cd"},
        dxgi_by_tex={"cd": 72}, scalars={"mattype": 9, "blend_mode": 0})
    alpha = spec["channels"]["alpha"]
    assert alpha["component"] == "A"        # what the engine reads
    assert alpha["punchthrough"] is True    # 1 bit
    assert alpha["alpha_plane"] is False    # ...and no authored data behind it


def test_every_channel_carries_a_component_hint():
    role_textures = {
        "layer0_composite_diffuse": "cd", "layer0_composite_normals": "nm",
        "layer0_composite_components": "cc", "layer0_composite_specular": "cs",
        "layer0_emissive_map": "em", "layer0_secondary_emissive_map": "e2",
        "layer0_opacity_map": "op", "layer0_back_lighting_map": "bl",
        "layer1_blend_mask": "bm", "layer0_flowmap_map": "fm",
        "layer1_alpha_map": "am",
    }
    dxgi = {"cd": 78, "nm": 83, "cc": 71, "cs": 78, "em": 72, "e2": 72,
            "op": 72, "bl": 72, "bm": 80, "fm": 72, "am": 80}
    spec = mat.build_material_spec("k", role_textures=role_textures,
                                   dxgi_by_tex=dxgi,
                                   scalars={"mattype": 2, "blend_mode": 7})
    expected = {
        "base_color": "RGB", "normal": "RG",       # BC5 -> Z reconstructed
        "roughness": "R", "specular": "RGB", "emission": "RGB",
        "secondary_emission": "RGB", "transmission": "RGB",
        "translucency": "RGB", "blend_mask": "R", "flowmap": "RGB",
        "alpha": "R",
    }
    for name, comp in expected.items():
        assert spec["channels"][name]["component"] == comp, name
    # the deprecated `opacity` mirror inherits the transmission tint's component
    assert spec["channels"]["opacity"]["component"] == "RGB"
    # every per-layer channel carries it too
    for entry in spec["layers"]:
        for ch in entry["channels"].values():
            assert "component" in ch, ch["role_key"]


def test_component_is_optional_and_old_manifests_still_load():
    """A manifest written before this key existed must keep working."""
    assert mat.component_for("no_such_role") is None
    assert mat.component_for("unknown_s4") is None
    # a channel dict with no "component" is still a valid input downstream
    legacy = {"texture": "t", "role_key": "layer1_alpha_map", "dxgi": 80,
              "colorspace": "Non-Color"}
    assert "component" not in legacy
    assert mat.component_for(legacy["role_key"], legacy["dxgi"]) == "R"


def test_normal_component_depends_only_on_bc5():
    assert mat.component_for("layer0_composite_normals", 83) == "RG"
    assert mat.component_for("layer0_composite_normals", 99) == "RGB"
    assert mat.component_for("layer0_normal_map", 82) == "RG"


# ---------------------------------------------------------------------------
# 3. Role-name hygiene — no name may be added that is not a real preimage
# ---------------------------------------------------------------------------

def test_alpha_role_names_are_exact_preimages():
    """Ten role names were fabricated once. Both alpha roles are real."""
    for name in ("layer0_alpha_map", "layer1_alpha_map",
                 "layer0_composite_diffuse", "layer1_composite_diffuse"):
        key = f"{msc.symbol64(name):016x}"
        assert mat.INPUTNAME_ROLE[key][0] == name, (name, key)
    assert f"{msc.symbol64('layer0_alpha_map'):016x}" == "9dba2dc44433be64"
    assert f"{msc.symbol64('layer1_alpha_map'):016x}" == "8ed4ab4792aaf806"


def test_no_glass_role_and_opacity_is_not_alpha():
    assert not [r for r in mat.KNOWN_ROLES if "glass" in r]
    assert "layer0_opacity_map" not in mat.ALPHA_ROLES
    assert "layer0_opacity_map" in mat.TRANSMISSION_ROLES
    assert "layer1_alpha_map" in mat.ALPHA_ROLES


# ---------------------------------------------------------------------------
# 4. The scanner-artefact row
# ---------------------------------------------------------------------------

def test_scanner_artefact_row_is_dropped():
    """`inputname_hash == shaderset_hash` is a misparse, never a name."""
    for shd in ARTEFACT_SHADERSETS:
        assert mat.is_scanner_artefact_row(shd, shd) is True
        assert mat.is_scanner_artefact_row(shd, "8ed4ab4792aaf806") is False
    assert mat.is_scanner_artefact_row("", "") is False
    # zero-padding must not defeat the comparison
    assert mat.is_scanner_artefact_row("05575a94091f1839",
                                       "05575a94091f1839".zfill(16)) is True

    class _Row:
        def __init__(self, shd, inp, tex, slot):
            self.shaderset_hash, self.inputname_hash = shd, inp
            self.textureassetid_hash, self.slot = tex, slot

    rows = [_Row(ARTEFACT_SHADERSETS[0], ARTEFACT_SHADERSETS[0], ARTEFACT_TEXTURE, 0),
            _Row(ARTEFACT_SHADERSETS[0], "2249a2ab88ae66f0", "9cef9cbe9bc742ff", 18)]
    roles = mat.roles_from_input_rows(rows, names={})
    assert roles == {"layer0_specular_map": "9cef9cbe9bc742ff"}
    assert ARTEFACT_TEXTURE not in roles.values()


def test_artefact_texture_no_longer_becomes_base_colour():
    """Left in, it lands as `unknown_s0` and the DXGI fallback promotes a
    BC7_UNORM_SRGB teal texture (mean RGB 63/131/111) to Base Color on 2
    materials whose engine albedo is the authored `common_white` default."""
    with_artefact = mat.classify_roles(
        {"unknown_s0": ARTEFACT_TEXTURE, "layer0_specular_map": "sp"},
        {ARTEFACT_TEXTURE: 99, "sp": 72})
    assert with_artefact["base_color"]["texture"] == ARTEFACT_TEXTURE
    assert with_artefact["base_color"]["inferred_from"] == "dxgi"
    # ...which is exactly why the row is dropped upstream, in
    # roles_from_input_rows / load_shaderset_textures, not here.
    without = mat.classify_roles({"layer0_specular_map": "sp"}, {"sp": 72})
    assert "base_color" not in without
    assert without["specular"]["role_key"] == "layer0_specular_map"


# ---------------------------------------------------------------------------
# 5. S1 — BASE_COLOR_ROLES ordering, settled with data
# ---------------------------------------------------------------------------

def test_specular_map_is_not_a_base_colour_role():
    """`layer0_specular_map` was FIRST in BASE_COLOR_ROLES. All 8 carriers have
    no diffuse at all (`stream-confirmed`), so the ordering never resolved a
    conflict — the entry was routing specular data into Base Color."""
    assert SPECULAR_MAP_CARRIERS_WITH_DIFFUSE == 0
    assert "layer0_specular_map" not in mat.BASE_COLOR_ROLES
    assert "layer0_specular_map" in mat.SPECULAR_ROLES
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_specular_map": "sp"}, dxgi_by_tex={"sp": 72},
        scalars={"mattype": 2, "blend_mode": 12})
    assert "base_color" not in spec["channels"]
    assert spec["channels"]["specular"]["role_key"] == "layer0_specular_map"


def test_base_colour_role_order_is_composite_then_albedo():
    """`layer0_albedo_map` and `layerN_composite_diffuse` are ALTERNATIVES —
    0 of 100 corpus materials carry both — so the order is unobservable on
    shipped data. It is fixed as composite-first because `use_composite_`
    OVERRIDES the albedo path (`shader-confirmed`: the ubershader "override[s]
    the layer values with the composite textures")."""
    assert ALBEDO_AND_COMPOSITE_BOTH == 0
    assert mat.CHANNEL_ROLE_SUFFIXES["base_color"] == ("composite_diffuse",
                                                       "albedo_map")
    both = mat.classify_roles(
        {"layer0_composite_diffuse": "cd", "layer0_albedo_map": "al"},
        {"cd": 78, "al": 77})
    assert both["base_color"]["role_key"] == "layer0_composite_diffuse"


# ---------------------------------------------------------------------------
# 6. S2 — base_color_factor is NOT a runtime term
# ---------------------------------------------------------------------------

def test_base_color_factor_is_the_bake_colour_not_a_runtime_tint():
    """`base_color_factor` is `SGMaterialData.bakecolor` == the authored
    `k_hardware_color`, UI name "Bake Color" (`name-confirmed`, the material
    asset schema). It is authored in 6 places across the engine's material
    assets and read by **0** of its shaders, so the runtime shader never reads
    it: RAD neither multiplies nor replaces the base-colour texture with it.
    The runtime albedo tint is the per-layer `layerN_albedo_tint_color`
    (`params.albedotint`, multiplied into `albedo`) — which no corpus material
    overrides.

    So `build_material_spec` must keep carrying it as a flat FALLBACK and must
    never fold it into a texture channel."""
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_composite_diffuse": "cd"},
        dxgi_by_tex={"cd": 78},
        scalars={"base_color_factor": [0.25, 0.5, 0.75, 0.0],
                 "mattype": 1, "blend_mode": 0})
    assert spec["base_color_factor"] == [0.25, 0.5, 0.75, 0.0]
    bc = spec["channels"]["base_color"]
    # untouched by the factor — no premultiply, no tint key, no alpha from it
    assert "base_color_factor" not in bc
    assert "tint" not in bc
    assert spec["alpha"] == 1.0            # the factor's .a is NOT k_alpha


# ---------------------------------------------------------------------------
# 7. Optional corpus sweeps (skip on a clean checkout)
# ---------------------------------------------------------------------------

def test_fixture_corpus_alpha_role_format_census():
    specs = _fixture_specs()
    if not specs:
        return
    census: dict[str, dict[int, int]] = {}
    for _pkg, spec in specs:
        chans = dict(spec.get("channels") or {})
        for entry in spec.get("layers") or []:
            chans.update(entry.get("channels") or {})
        by_role = {c.get("role_key"): c for c in chans.values()}
        for role in spec.get("role_textures") or {}:
            if role not in ALPHA_ROLE_FORMAT_CENSUS:
                continue
            dxgi = (by_role.get(role) or {}).get("dxgi")
            census.setdefault(role, {}).setdefault(dxgi, 0)
            census[role][dxgi] += 1
    assert census == ALPHA_ROLE_FORMAT_CENSUS, census


def test_fixture_corpus_every_alpha_channel_says_R_or_A():
    """Re-route every shipped material through the LIVE code.

    The fixture manifests were written before `"component"` existed, so this
    deliberately rebuilds the spec from the corpus's own `role_textures` +
    formats rather than reading the stored channel — which is also the
    back-compat proof that an old manifest is still a valid input.
    """
    specs = _fixture_specs()
    if not specs:
        return
    seen: dict[str, int] = {}
    for _pkg, spec in specs:
        chans = dict(spec.get("channels") or {})
        for entry in spec.get("layers") or []:
            chans.update(entry.get("channels") or {})
        dxgi_by_tex = {c["texture"]: c.get("dxgi") for c in chans.values()
                       if c.get("texture")}
        rebuilt = mat.build_material_spec(
            spec["key"], role_textures=spec.get("role_textures") or {},
            dxgi_by_tex=dxgi_by_tex,
            scalars={"mattype": spec.get("mattype", 0),
                     "blend_mode": spec.get("blend_mode", 0),
                     "alpha": spec.get("alpha", 1.0)})
        alpha = rebuilt["channels"].get("alpha")
        if not alpha:
            continue
        comp = alpha.get("component")
        assert comp in ("R", "A"), (spec["key"], comp)
        role = str(alpha.get("role_key", ""))
        if alpha.get("from_channel") == "base_color":
            assert comp == "A", spec["key"]
        elif role.endswith("_alpha_map"):
            assert comp == "R", spec["key"]
        seen[comp] = seen.get(comp, 0) + 1
    # 20 alpha maps (9 layer0 + 11 layer1) read R; the BASE_COLOR_ALPHA cases
    # are the composite-diffuse materials whose render mode is not OPAQUE.
    assert seen.get("R") == 20, seen
    assert seen.get("A"), seen


def test_fixture_corpus_no_bc1_texture_has_any_transparent_texel():
    """Block-decode every shipped BC1 texture at mip 0 and count index-3 texels.

    `stream-confirmed`: 0 of 65,601,536. BC1 alpha is unused corpus-wide, so a
    BC1 `.a` wired to an opacity socket is wiring a constant.
    """
    specs = _fixture_specs()
    if not specs:
        return
    files: dict[str, tuple[Path, int]] = {}
    for pkg, spec in specs:
        chans = dict(spec.get("channels") or {})
        for entry in spec.get("layers") or []:
            chans.update(entry.get("channels") or {})
        for ch in chans.values():
            dxgi, rel = ch.get("dxgi"), ch.get("file") or ""
            if dxgi in (70, 71, 72) and rel and (pkg / rel).exists():
                files[ch["texture"]] = (pkg / rel, dxgi)
    if not files:
        return
    transparent = texels = punchthrough = 0
    for path, _dxgi in files.values():
        head = path.read_bytes()[:148]
        assert head[:4] == b"DDS ", path
        _s, _f, height, width = struct.unpack_from("<4I", head, 4)
        off = 148 if head[84:88] == b"DX10" else 128
        bw, bh = max(1, (width + 3) // 4), max(1, (height + 3) // 4)
        with path.open("rb") as fh:
            fh.seek(off)
            data = fh.read(bw * bh * 8)
        for i in range(bw * bh):
            c0, c1 = struct.unpack_from("<HH", data, i * 8)
            bits = struct.unpack_from("<I", data, i * 8 + 4)[0]
            texels += 16
            if c0 <= c1:                      # 3-colour + 1-bit-alpha mode
                punchthrough += 1
                for k in range(16):
                    if (bits >> (2 * k)) & 3 == 3:
                        transparent += 1
    assert transparent == BC1_TRANSPARENT_TEXELS, (transparent, texels)
    # the corpus this literal was measured on; a re-extract may add textures, so
    # the counts are a floor and the transparent-texel assertion is the contract
    assert len(files) >= BC1_TEXTURES, len(files)
    assert texels >= BC1_TEXELS_DECODED, texels
    assert punchthrough >= BC1_PUNCHTHROUGH_BLOCKS, punchthrough


def test_fixture_corpus_bc4_alpha_maps_have_no_alpha_data():
    """A BC4 file is ONE 8-byte plane per block — mip 0 is exactly
    blocks * 8 bytes, so `.a` can only be the hardware constant 1.0."""
    specs = _fixture_specs()
    if not specs:
        return
    checked = 0
    for pkg, spec in specs:
        chans = dict(spec.get("channels") or {})
        for entry in spec.get("layers") or []:
            chans.update(entry.get("channels") or {})
        for ch in chans.values():
            if ch.get("dxgi") != 80 or not (ch.get("file") or ""):
                continue
            path = pkg / ch["file"]
            if not path.exists():
                continue
            head = path.read_bytes()[:148]
            _s, _f, height, width, _p, _d, mips = struct.unpack_from("<7I", head, 4)
            off = 148 if head[84:88] == b"DX10" else 128
            bw, bh = max(1, (width + 3) // 4), max(1, (height + 3) // 4)
            expect = 0
            w, h = width, height
            for _ in range(max(mips, 1)):
                expect += max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * BC4_BLOCK_BYTES
                w, h = max(w // 2, 1), max(h // 2, 1)
            assert path.stat().st_size == off + expect, (path, path.stat().st_size)
            assert bw * bh * BC4_BLOCK_BYTES <= expect
            checked += 1
    assert checked, "no BC4 alpha map in the fixture corpus"
