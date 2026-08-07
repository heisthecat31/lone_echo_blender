"""`le_mesh.vista_shader` — the exterior vista's shading, against the shipped DXBC.

Every expected value in this file is a **verbatim copy of a literal from the
disassembled pixel shader**, typed in again here rather than imported, so a test
failure means the module and the shipped instruction stream disagree — not that
a constant was compared with itself.  The shaders are those of `6f67762bf83d59fd`
(Saturn), `35a8c5ad5fb8d894` (the sun card), `a849eddeb321dcc7` (the skydome),
`340f6ff7265f0077` (the ring haze), `ba863c7b2cb61616` (the ring sheet),
`a1e53ff754dd1443` (the moons), `b9588078adab3e49` (the dig-site FX cards) and
`44538616b0138eb3` (a debris rock), all out of archive `4c47d84c1e52447a`.

⛔ WHAT THIS FILE CANNOT DO.  The disassembler is not part of this repository
(see `le_mesh/vista_shader.py`), so re-typing a literal catches a transcription
error and nothing else — it cannot re-derive the reading.  These are consistency
tests over a transcription, not a reproduction of it, and no other test module
here carries that caveat.

★ THE LIGHT-SIDE FIXTURE IS CONSTRUCTED, NOT EXTRACTED.  This repository ships
no game data, so `min_itc_master`'s four `CGLight` records are built field by
field below and pushed through the public codec (`le_mesh.lights`, which encodes
to the 352-byte `SGLightParams` grid and decodes back).  What is typed in is the
small set of measured quantities the claims rest on — two unit directions, two
positions, the ranges and the option words; everything else is a default.

Pure stdlib.  Runs under `python3 blender_tool/tests/run_tests.py` and unchanged
under pytest.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]          # .../blender_tool
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from le_mesh import vista_shader as VS  # noqa: E402
from le_mesh import lights as L         # noqa: E402

# --- verbatim from the shipped instruction streams ---------------------------

# `mul rN.xyz, rN.xyzx, l(0.275649, 0.275649, 0.275649, 0.000000)` — five times
# in Saturn's pixel shader and five more in the debris rock's.
SHIPPED_SG5_K = 0.275649

# `dp3 rN.w, l(...), r6.xyzx` — the five SG lobe directions, in shipped order.
SHIPPED_SG5_DIRS = (
    (0.839526, -0.534037, 0.100000),
    (-0.247648, 0.921233, 0.300000),
    (-0.399156, -0.768553, 0.500000),
    (0.670809, 0.244979, 0.700000),
    (-0.402913, 0.166316, 0.900000),
)

SHIPPED_SATURN_PLATE_COEFF = 0.434154        # mad r4.xyz, r6.xyzx, l(0.434154..)
SHIPPED_SATURN_DIFFUSE_GLOBAL = 0.822        # mad r0.xzw, r1.yyzw, l(0.822000..)
SHIPPED_WRAP_OFFSET = 0.250000               # add r5.w, r4.w, l(0.250000)
SHIPPED_WRAP_SLOPE = 0.800000                # mul_sat r5.w, r5.w, l(0.800000)
SHIPPED_DIRLIGHT_K = 0.261651                # mul r9.xyz, r9.xyzx, l(0.261651..)
SHIPPED_SUN_RGB_SCALE = 0.200000             # mul r0.yzw, .. l(0,0.2,0.2,0.2)
SHIPPED_SUN_OPC_SCALE = 0.999924             # mul r1.x, r1.x, l(0.999924)
SHIPPED_SUN_ALPHA_GAMMA = 2.200000           # mul r0.x, r0.x, l(2.200000)
SHIPPED_SUN_CLAMP = 11000.0                  # min o0.xyz, .., l(11000, ..)

# `measured` off the shipped mesh manifest / position blobs of
# `4c47d84c1e52447a_4c47d84c1e52447a.lemesh`.
SATURN_DIRECTION = (0.558, -0.022, 0.830)
SUN_CARD_DIRECTION = (0.5401, 0.8008, 0.2588)
SATURN_BASE_COLOR_FACTOR = (0.10034521669149399, 0.10082881897687912,
                            0.10188877582550049)


def _close(a, b, tol=1e-6):
    return abs(a - b) <= tol


# =============================================================================
# the SG5 basis
# =============================================================================

def test_sg5_k_equals_the_shipped_literal():
    """`2 / kLambdaSG5 * kSG5Scale` must reproduce the pre-multiplied constant
    the shipped shaders actually carry.  This is the join between the engine's
    own named constants and the compiled bytes, and it is what licenses the whole
    reconstruction."""
    assert _close(VS.SG5_K, SHIPPED_SG5_K, 5e-7), (VS.SG5_K, SHIPPED_SG5_K)
    assert _close(VS.SG5_K_SHIPPED_LITERAL, SHIPPED_SG5_K, 1e-12)


def test_sg5_dirs_match_the_shipped_literals():
    assert len(VS.SG5_DIRS) == VS.SG5_LOBES == 5
    for got, want in zip(VS.SG5_DIRS, SHIPPED_SG5_DIRS):
        for a, b in zip(got, want):
            assert _close(a, b, 1e-6), (got, want)


def test_sg5_dirs_are_unit_vectors_on_a_stratified_hemisphere():
    zs = []
    for d in VS.SG5_DIRS:
        assert _close(math.sqrt(sum(c * c for c in d)), 1.0, 1e-6), d
        zs.append(d[2])
    # the z components are the giveaway that this is a hemispherical basis
    assert [round(z, 1) for z in zs] == [0.1, 0.3, 0.5, 0.7, 0.9]


def test_sg5_weights_flat_are_all_positive_and_sum_correctly():
    w = VS.sg5_weights()
    assert len(w) == 5
    assert all(x > 0.0 for x in w), w
    # n_ts == (0,0,1) so each weight is z_k * SG5_K
    for wk, d in zip(w, VS.SG5_DIRS):
        assert _close(wk, d[2] * VS.SG5_K, 1e-9)
    assert _close(sum(w), 2.5 * VS.SG5_K, 1e-7)


def test_sg5_ambient_is_linear_in_the_lobes_and_honours_world_ambient():
    lobes = [(0.1, 0.2, 0.3)] * 5
    a = VS.sg5_ambient(lobes)
    b = VS.sg5_ambient([(0.2, 0.4, 0.6)] * 5)
    for c in range(3):
        assert _close(b[c], 2.0 * a[c], 1e-9)
    c2 = VS.sg5_ambient(lobes, world_ambient=(0.5, 0.5, 0.5))
    for c in range(3):
        assert _close(c2[c], 0.5 * a[c], 1e-9)


def test_sg5_ambient_refuses_a_wrong_lobe_count():
    try:
        VS.sg5_ambient([(1.0, 1.0, 1.0)] * 4)
    except ValueError:
        return
    raise AssertionError("sg5_ambient accepted 4 lobes; the basis is 5")


def test_slices_per_page_matches_the_shader_addressing():
    """`mad rN.xyz, v6.xyzx, l(1,1,5), l(0,0,k)` for k = 0..4."""
    assert VS.SG5_SLICES_PER_PAGE == 5
    # and the page arithmetic the lightmap module already uses agrees
    from le_mesh import lightmap as LM
    assert LM.colour_slice_indices(13, 5) == [65, 66, 67, 68, 69]


# =============================================================================
# the wrapped diffuse — the term that decides whether Saturn is sunlit
# =============================================================================

def test_wrap_constants_match_the_shipped_literals():
    assert _close(VS.WRAP_OFFSET, SHIPPED_WRAP_OFFSET)
    assert _close(VS.WRAP_SLOPE, SHIPPED_WRAP_SLOPE)
    assert _close(VS.DIRLIGHT_DIFFUSE_K, SHIPPED_DIRLIGHT_K)


def test_wrap_diffuse_is_zero_below_the_wrapped_terminator():
    assert VS.wrap_diffuse(-0.25) == 0.0
    assert VS.wrap_diffuse(-0.26) == 0.0
    assert VS.wrap_diffuse(-1.0) == 0.0
    assert VS.dirlight_is_dark(-0.5)
    assert not VS.dirlight_is_dark(0.0)


def test_wrap_diffuse_is_monotone_and_saturates_at_one():
    xs = [i / 50.0 - 1.0 for i in range(101)]
    ys = [VS.wrap_diffuse(x) for x in xs]
    assert all(b >= a - 1e-12 for a, b in zip(ys, ys[1:]))
    assert _close(VS.wrap_diffuse(1.0), 1.0)
    assert _close(VS.wrap_diffuse(2.0), 1.0)
    # the wrap moves the terminator 14.48 deg past the geometric one: the term
    # only dies at N.L == -0.25, i.e. 104.48 deg from the light
    assert _close(90.0 - math.degrees(math.acos(0.25)), 14.4775, 1e-3)
    assert _close(math.degrees(math.acos(-0.25)), 104.4775, 1e-3)


# =============================================================================
# Saturn
# =============================================================================

def test_saturn_constants_match_the_shipped_literals():
    assert _close(VS.SATURN_PLATE_COEFF, SHIPPED_SATURN_PLATE_COEFF)
    assert _close(VS.SATURN_DIFFUSE_GLOBAL, SHIPPED_SATURN_DIFFUSE_GLOBAL)
    assert VS.SATURN_BLEND_ATTRIBUTE == "color1"
    assert VS.SATURN_BINDS_CLUSTERED_LIGHTS is False


def test_blend_multiplier_is_four_on_the_shipped_mesh():
    """Saturn's `color1` reads 1.0 on 3703/3703 vertices (min 0.9961), and the
    shader's `1 + bx + by + bz` therefore contributes exactly 4."""
    assert _close(VS.blend_multiplier((1.0, 1.0, 1.0)), 4.0)
    assert _close(VS.blend_multiplier((0.0, 0.0, 0.0)), 1.0)
    assert _close(VS.blend_multiplier((0.9961, 0.9961, 0.9961)), 3.9883, 1e-4)
    # saturate: an out-of-range vertex colour cannot inflate it
    assert _close(VS.blend_multiplier((5.0, 5.0, 5.0)), 4.0)
    assert _close(VS.blend_multiplier((-1.0, -1.0, -1.0)), 1.0)


def test_saturn_plate_scale_is_the_product_of_three_shipped_literals():
    want = SHIPPED_SATURN_DIFFUSE_GLOBAL * SHIPPED_SATURN_PLATE_COEFF * 4.0
    assert _close(VS.saturn_plate_scale(), want, 1e-9)
    assert _close(VS.saturn_plate_scale(), 1.4275, 1e-4)


def test_the_albedo_correction_is_14x_and_names_its_own_arithmetic():
    """★ Q2's number.  The importer builds Saturn from `base_color_factor`
    (= the material record's `bakecolor`), but `6f67762bf83d59fd`'s pixel shader
    declares NO material constant buffer, so that value cannot reach the GPU."""
    scale, why = VS.albedo_correction(VS.SATURN_SHADERSET,
                                      SATURN_BASE_COLOR_FACTOR)
    assert scale is not None, why
    assert 14.0 < scale[0] < 14.3, scale
    for s in scale:
        assert 14.0 < s < 14.3, scale
    assert "plate_coeff" in why and "base_color_factor" in why


def test_albedo_correction_refuses_an_undisassembled_shaderset():
    scale, why = VS.albedo_correction("0000000000000000", (1.0, 1.0, 1.0))
    assert scale is None
    assert "disassembled" in why


def test_albedo_correction_refuses_a_zero_base_color_factor():
    scale, why = VS.albedo_correction(VS.SATURN_SHADERSET, (0.0, 0.1, 0.1))
    assert scale is None
    assert "zero" in why


def test_saturn_albedo_without_detail_plates_is_just_the_plate_term():
    a = VS.saturn_albedo(1.0)
    for c in a:
        assert _close(c, VS.SATURN_PLATE_COEFF, 1e-9)


def test_saturn_albedo_adds_each_detail_plate_with_its_shipped_tint():
    detail = {r: 1.0 for r, _n, _t, _w, _c in VS.SATURN_DETAIL}
    a = VS.saturn_albedo(1.0, detail)
    want = [VS.SATURN_PLATE_COEFF] * 3
    for _r, _n, tint, weight, _chan in VS.SATURN_DETAIL:
        for c in range(3):
            want[c] += tint[c] * weight
    for got, w in zip(a, want):
        assert _close(got, w, 1e-9)
    # the detail plates are dropped when BLEND is zero, exactly as the shader does
    a0 = VS.saturn_albedo(1.0, detail, color1=(0.0, 0.0, 0.0))
    for c in a0:
        assert _close(c, VS.SATURN_PLATE_COEFF, 1e-9)


def test_saturn_detail_roles_are_the_five_shipped_rdef_names():
    names = [n for _r, n, _t, _w, _c in VS.SATURN_DETAIL]
    assert names == ["vst_saturn_planet_detail_spots_clr",
                     "vst_saturn_planet_detail_wind_clr",
                     "vst_saturn_planet_detail_clouds_clr"]
    roles = [r for r, _n, _t, _w, _c in VS.SATURN_DETAIL]
    assert roles == ["layer1_albedo_map", "layer2_albedo_map", "layer3_albedo_map"]


def test_saturn_diffuse_composes_albedo_ambient_and_the_blend_multiplier():
    lobes = [(0.01, 0.01, 0.01)] * 5
    d = VS.saturn_diffuse(1.0, lobes)
    amb = VS.sg5_ambient(lobes)
    for c in range(3):
        want = VS.SATURN_PLATE_COEFF * amb[c] * VS.SATURN_DIFFUSE_GLOBAL * 4.0
        assert _close(d[c], want, 1e-12)


# =============================================================================
# the sun card — Q4
# =============================================================================

def test_sun_card_constants_match_the_shipped_literals():
    assert _close(VS.SUN_CARD_RGB_SCALE, SHIPPED_SUN_RGB_SCALE)
    assert _close(VS.SUN_CARD_OPC_SCALE, SHIPPED_SUN_OPC_SCALE)
    assert _close(VS.SUN_CARD_ALPHA_GAMMA, SHIPPED_SUN_ALPHA_GAMMA)
    assert _close(VS.SUN_CARD_OUTPUT_CLAMP, SHIPPED_SUN_CLAMP)
    assert VS.SHADERSET_TERMS[VS.SUN_CARD_SHADERSET]["opacity_channel"] == "R"


def test_sun_card_rgb_applies_the_fifth_and_clamps():
    assert VS.sun_card_rgb(1.0) == (0.2, 0.2, 0.2)
    assert VS.sun_card_rgb((10.0, 20.0, 30.0)) == (2.0, 4.0, 6.0)
    assert VS.sun_card_rgb(1e9) == (11000.0, 11000.0, 11000.0)
    # the vertex colour multiplies too, and the importer applies neither term
    assert VS.sun_card_rgb(1.0, (0.5, 0.5, 0.5)) == (0.1, 0.1, 0.1)


def test_sun_card_alpha_is_the_opc_plate_times_a_gamma_decoded_plate_alpha():
    assert _close(VS.sun_card_alpha(1.0, 1.0), SHIPPED_SUN_OPC_SCALE, 1e-9)
    assert VS.sun_card_alpha(0.0, 1.0) == 0.0
    # gamma 2.2 makes a mid alpha much more transparent than a linear read
    assert _close(VS.sun_card_alpha(1.0, 0.5), 0.999924 * 0.5 ** 2.2, 1e-9)
    assert VS.sun_card_alpha(1.0, 0.5) < 0.25
    assert VS.sun_card_alpha(2.0, 2.0) == 1.0          # saturated both ends


# =============================================================================
# the level's own lights, and whether they light Saturn
# =============================================================================
# ★ CONSTRUCTED, NOT EXTRACTED — see the module docstring.  Each record is a
# plain field dict pushed through `le_mesh.lights.record_from_fields`, which
# packs it into the 352-byte `SGLightParams` grid and decodes it back, so what
# these tests see is what a real decode would have produced and never a
# hand-written object that skipped the codec.

_DIR_BASE = ["eEnableDiffuse", "eCastLevelShadows", "eCastActorShadows",
             "eLightTransparents", "eLightOpaques", "eLightEnabled",
             "eCastOpaqueShadows", "eCastAlphaTestShadows",
             "eCastTransparentShadows"]

#: The level's four lights.  Only the fields the claims below rest on are given;
#: `record_from_fields` supplies the engine's defaults for the rest.
MIN_ITC_MASTER_LIGHTS = (
    dict(index=0, type="eDirectionalLight", options=_DIR_BASE,
         pos=(163.616, -414.78845, 721.915),
         primarycolor=(80.0, 78.93553, 75.78485),
         attenuation=(1.0, 75.5, 150.0, 150.0), farp=150.0, nearp=0.01,
         orientation=(0.77329034, -0.27045405, -0.18932539, 0.54132485),
         direction=(-0.5856140, -0.7347949, -0.3422466),
         attenmethod=1.0, lightmask=511),
    dict(index=1, type="ePointLight",
         options=_DIR_BASE + ["eEnableSpecular", "eBakeShadow"],
         pos=(348.31613, 331.69937, 1923.5134),
         primarycolor=(12.0, 7.7452965, 4.8),
         attenuation=(1.0, 50.5, 100.0, 100.0), farp=100.0, nearp=0.01,
         orientation=(0.70710677, 0.70710677, -0.0, 0.0),
         direction=(-0.0, -0.0, -1.0),
         attenmethod=1.0, lightmask=65535),
    dict(index=2, type="ePointLight",
         options=_DIR_BASE + ["eEnableSpecular", "eBakeShadow"],
         pos=(-65.38832, 319.94055, 1616.5039),
         primarycolor=(100.0, 64.544136, 40.0),
         attenuation=(1.0, 50.5, 100.0, 1000.0), farp=100.0, nearp=0.01,
         orientation=(0.70710677, 0.70710677, -0.0, 0.0),
         direction=(-0.0, -0.0, -1.0),
         attenmethod=1.0, lightmask=65535),
    dict(index=3, type="eDirectionalLight",
         options=_DIR_BASE + ["eEnableSpecular", "eCastShadows",
                              "eLightParticles", "eBakeIndirect",
                              "ePrimaryDirLight"],
         pos=(163.616, -124.747, 721.315),
         primarycolor=(10.0, 9.8669415, 9.473106),
         attenuation=(1.0, 75.5, 150.0, 150.0), farp=150.0, nearp=0.01,
         orientation=(0.70767355, -0.33660474, -0.26682955, 0.56097901),
         direction=(-0.7553128, -0.6143478, -0.2282090),
         attenmethod=1.0, lightmask=2047),
)


def _rig():
    """The four records, each round-tripped through the on-disk 352-byte grid."""
    return [L.record_from_fields(d, d["index"]) for d in MIN_ITC_MASTER_LIGHTS]


def _dir_records(rig=None):
    return [r for r in (rig or _rig()) if r.type_name == "eDirectionalLight"]


def test_the_constructed_rig_obeys_the_shipped_direction_invariant():
    """★ What makes a CONSTRUCTED fixture usable as a stand-in: it satisfies the
    invariant `docs/LIGHTING.md` measured on 118/118 shipped records, that the
    stored `direction` is the record's own orientation applied to local +Z.  A
    fixture that failed this would be testing a light the engine cannot store."""
    for rec in _rig():
        x, y, z, w = rec.orientation
        want = (2.0 * (x * z + y * w), 2.0 * (y * z - x * w),
                1.0 - 2.0 * (x * x + y * y))
        for got, exp in zip(rec.direction, want):
            assert _close(got, exp, 1e-6), (rec.index, rec.direction, want)
        assert _close(math.sqrt(sum(c * c for c in rec.direction)), 1.0, 1e-6)
        # and `farp == attenuation.z` on every shipped record
        assert _close(rec.farp, rec.attenuation[2], 1e-6), rec.index


def test_min_itc_master_ships_four_lights_two_of_them_directional():
    rig = _rig()
    assert len(rig) == 4
    by_type = {}
    for r in rig:
        by_type[r.type_name] = by_type.get(r.type_name, 0) + 1
    assert by_type == {"eDirectionalLight": 2, "ePointLight": 2}
    assert sum(1 for r in rig if r.enabled) == 4
    assert sum(1 for r in rig if r.affects_diffuse) == 4
    # ⚠ no light is specular-ONLY: every one of them lights the diffuse pass too
    assert sum(1 for r in rig if r.affects_specular and not r.affects_diffuse) == 0


def test_the_primary_directional_light_is_flagged_and_bakes_indirect():
    """★★ The vista is lit by the light the engine FLAGS, not by the brightest
    one.  `ePrimaryDirLight` is on exactly one record, and it is the 10 W/m^2
    light rather than the 80 W/m^2 one."""
    rig = _rig()
    prim = [r for r in rig if "ePrimaryDirLight" in r.option_names]
    assert len(prim) == 1, [r.option_names for r in rig]
    assert prim[0].type_name == "eDirectionalLight"
    assert "eBakeIndirect" in prim[0].option_names
    assert prim[0].index == 3
    strongest = max(_dir_records(rig), key=lambda r: max(r.primarycolor))
    assert strongest.index == 0 and strongest is not prim[0]
    assert max(strongest.primarycolor) == 80.0
    assert max(prim[0].primarycolor) == 10.0


def test_every_directional_light_leaves_saturns_visible_disc_dark():
    """★★ Arithmetic on the shipped directions plus one shipped shader literal:
    the engine's own sun contributes EXACTLY ZERO to the face of Saturn that the
    player sees."""
    dirs = [r.direction for r in _dir_records()]
    assert len(dirs) == 2
    lit, rows = VS.body_is_sunlit(SATURN_DIRECTION, dirs)
    assert not lit, rows
    for ndotl, wrap, dark in rows:
        assert ndotl < -0.5, rows
        assert wrap == 0.0 and dark


def test_the_sun_card_direction_is_not_a_light_direction():
    """The harness used to orient a Blender SUN from the sun CARD's centroid.
    The shipped lights disagree with it by 6.6 and 16.5 degrees — and the card is
    a 13.7-degree glow sprite, not a light record."""
    angles = []
    for d in (r.direction for r in _dir_records()):
        toward = [-c for c in d]
        n = math.sqrt(sum(c * c for c in toward))
        toward = [c / n for c in toward]
        m = math.sqrt(sum(c * c for c in SUN_CARD_DIRECTION))
        dot = sum(a * b / m for a, b in zip(toward, SUN_CARD_DIRECTION))
        angles.append(math.degrees(math.acos(max(-1.0, min(1.0, dot)))))
    angles.sort()
    assert 6.0 < angles[0] < 7.5, angles
    assert 15.5 < angles[1] < 17.5, angles


def test_the_sun_card_read_as_a_directional_light_is_also_dark_on_saturn():
    """Both readings are settled here, and they do NOT agree: as a DIRECTION the
    card leaves the disc dark (like the real lights); as a finite POINT at 40,117
    units it would light it.  The shipped records say directional, so the dark
    reading is the engine's."""
    n = VS.sub_observer_normal(SATURN_DIRECTION)
    # travel direction of a light coming FROM the card
    travel = tuple(-c for c in SUN_CARD_DIRECTION)
    ndotl = VS.ndotl_at(n, travel)
    assert -0.51 < ndotl < -0.49, ndotl
    assert VS.dirlight_is_dark(ndotl)


def test_the_point_lights_are_dig_site_lights_not_vista_lights():
    """Both point lights sit inside the 2,438-unit play area with a 100-unit
    range; Saturn's centre is 54,862 units away.  And Saturn's shaderset binds no
    clustered-light path at all, so they could not reach it even at range."""
    pts = [r for r in _rig() if r.type_name == "ePointLight"]
    assert len(pts) == 2
    for p in pts:
        assert math.dist(p.pos, (0.0, 0.0, 0.0)) < 2500.0
        assert p.attenuation[2] == 100.0
        assert p.range == 100.0
    assert VS.SHADERSET_TERMS[VS.SATURN_SHADERSET]["binds_clustered_lights"] is False


def test_the_constructed_records_round_trip_through_the_352_byte_grid():
    """Every record must survive another encode/decode pass byte-identically —
    which is what licenses building the rig in code instead of shipping one."""
    for rec in _rig():
        blob = L.encode_light(rec)
        assert len(blob) == L.STRIDE == 352
        assert L.encode_light(L.decode_light(blob, rec.index)) == blob


def test_directional_lights_carry_the_dirlightdirections_rows_verbatim():
    """`CGSceneData.dirlightdirections` holds 2 unit vectors and
    `dirlightindices == [0, 3]`.  They are the `direction` fields of the two
    `eDirectionalLight` records, which is what makes that table self-checking."""
    rig = _rig()
    dir_idx = [r.index for r in rig if r.type_name == "eDirectionalLight"]
    assert dir_idx == [0, 3]
    shipped = [(-0.585614, -0.734795, -0.342247),
               (-0.755313, -0.614348, -0.228209)]
    for i, want in zip(dir_idx, shipped):
        got = rig[i].direction
        for a, b in zip(got, want):
            assert _close(a, b, 1e-5), (i, got, want)


# =============================================================================
# 2026-08-06 — the four shadersets disassembled after the module was written
# =============================================================================
# Same rule as the rest of this file: every expected value below is typed in
# again from the disassembly, never imported, so a failure means the module and
# the shipped instruction stream disagree.  The four are `a849eddeb321dcc7`
# (skydome), `340f6ff7265f0077` (haze), `ba863c7b2cb61616` (rings) and
# `a1e53ff754dd1443` (moons).


def test_skydome_tint_and_floor_match_the_shipped_mad():
    """`mad r0.xyz, r0.xyzx, l(0.099382, 0.114076, 0.192477, 0),
    l(0.000488, 0.000595, 0.000717, 0)` then `max o0.xyz, r0.xyzx, l(0,0,0,0)`."""
    for got, want in zip(VS.SKYDOME_TINT, (0.099382, 0.114076, 0.192477)):
        assert _close(got, want, 1e-9)
    for got, want in zip(VS.SKYDOME_FLOOR, (0.000488, 0.000595, 0.000717)):
        assert _close(got, want, 1e-9)
    # the tint is blue-DOMINANT, which is the whole reason the ring sheet reads
    # cold: it turns a red-brown starfield plate into a blue sky.
    assert VS.SKYDOME_TINT[2] > VS.SKYDOME_TINT[0]
    assert _close(VS.SKYDOME_TINT[2] / VS.SKYDOME_TINT[0], 1.9367, 1e-3)


def test_skydome_rgb_clamps_at_zero_and_lands_on_the_floor_for_a_black_plate():
    assert VS.skydome_rgb((0.0, 0.0, 0.0)) == VS.SKYDOME_FLOOR
    assert VS.skydome_rgb((-5.0, -5.0, -5.0)) == (0.0, 0.0, 0.0)
    white = VS.skydome_rgb((1.0, 1.0, 1.0))
    for got, want in zip(white, (0.099870, 0.114671, 0.193194)):
        assert _close(got, want, 1e-6)


def test_the_skydome_uv_doubling_covers_the_plate_exactly_once():
    """`mul r0.xy, v2.xyxx, l(1.0, 2.0, 0, 0)`.  ★ MEASURED on the shipped mesh:
    `obj018`'s `uv0.v` spans exactly [0.5, 1.0], so `2v` spans [1, 2] — one
    full wrap, not a tiling.  The CLAMP reading is degenerate."""
    assert VS.SKYDOME_UV_V_SCALE == 2.0
    lo, hi = VS.SKYDOME_UV_V_RANGE
    assert (lo, hi) == (0.5, 1.0)
    assert _close(VS.SKYDOME_UV_V_SCALE * (hi - lo), 1.0, 1e-12)


def test_haze_tint_gamma_and_clamp_match_the_shipped_literals():
    """`mul r0.xyz, r1.xyzx, l(0.127469, 0.167655, 0.227420, 0)`;
    `min o0.xyz, r0.xyzx, l(11000, 11000, 11000, 0)`; VS `mul 2.2 / exp`."""
    for got, want in zip(VS.HAZE_TINT, (0.127469, 0.167655, 0.227420)):
        assert _close(got, want, 1e-9)
    assert VS.HAZE_VCOL_GAMMA == 2.2
    assert VS.HAZE_OUTPUT_CLAMP == 11000.0
    assert VS.HAZE_ALPHA_DISCARD == 1e-4


def test_haze_rgb_applies_the_gamma_before_the_tint():
    """`obj004`'s measured vertex rgb median is 0.796, so its cards carry a real
    0.796^2.2 modulation while `obj003`'s (1.0) do not."""
    plain = VS.haze_rgb((1.0, 1.0, 1.0), (1.0, 1.0, 1.0))
    assert plain == VS.HAZE_TINT
    dim = VS.haze_rgb((1.0, 1.0, 1.0), (0.796, 0.796, 0.796))
    k = 0.796 ** 2.2
    for got, want in zip(dim, [k * c for c in VS.HAZE_TINT]):
        assert _close(got, want, 1e-9)
    # the shipped measurement, kept so a regression in the gamma is visible
    assert _close(k, 0.605353, 1e-5)


def test_both_haze_cards_ship_alpha_one_so_the_blend_factor_is_moot():
    for name in ("obj003", "obj004"):
        assert VS.HAZE_VCOL_MEASURED[name][3] == 1.0


def test_ring_constants_match_the_shipped_literals():
    """`mad_sat r3.w, r2.w, l(0.419), l(0.890)`; `S`, the pre-arrival triple, and
    the F0 expansion — all from `ba863c7b2cb61616`."""
    assert _close(VS.RING_MASK_SCALE, 0.419, 1e-9)
    assert _close(VS.RING_MASK_BIAS, 0.890, 1e-9)
    for got, want in zip(VS.RING_ALBEDO_TINT, (0.364327, 0.398072, 0.614965)):
        assert _close(got, want, 1e-9)
    for got, want in zip(VS.RING_PREARRIVAL, (0.000589, 0.010000, 0.434241)):
        assert _close(got, want, 1e-9)
    for got, want in zip(VS.RING_F0_CONST, (0.197137, 0.298266, 0.487311)):
        assert _close(got, want, 1e-9)
    assert _close(VS.RING_ALPHA_GAMMA, 2.2, 1e-9)
    assert VS.RING_DETAIL_UV_SCALE == 40.0 and VS.RING_BASE_UV_SCALE == 10.0


def test_the_ring_mask_modulator_is_narrow_and_never_leaves_its_band():
    """★ `saturate(msk*0.419 + 0.890)` is a BRIGHTNESS MODULATOR in [0.890, 1.0],
    not a blend weight, which is what the plate statistics alone could not
    settle."""
    for m in (0.0, 0.25, 0.5, 0.75, 1.0):
        v = min(max(m * VS.RING_MASK_SCALE + VS.RING_MASK_BIAS, 0.0), 1.0)
        assert VS.RING_MASK_BIAS <= v <= 1.0
    assert _close(min(1.0, 0.0 * 0.419 + 0.890), 0.890, 1e-9)
    assert _close(min(1.0, 1.0 * 0.419 + 0.890), 1.0, 1e-9)


def test_the_rings_take_a_plain_lambert_and_never_flip_their_normal():
    """★★ NOT Saturn's wrap — no +0.25, no x0.8, no square.  And with no
    `SV_IsFrontFace` the anti-sun face gets exactly zero, which is the 255x."""
    assert VS.RING_DIFFUSE_IS_WRAPPED is False
    assert VS.RING_BACKFACE_DIRECT_IS_ZERO is True


def test_moon_constants_and_the_strongly_blue_emissive():
    """`albedo x 0.552011`, ambient global `0.719`, and
    `o0.rgb += emi.rgb * (0.403480, 0.425726, 2.000000)` — it ADDS."""
    assert _close(VS.MOON_ALBEDO_COEFF, 0.552011, 1e-9)
    assert _close(VS.MOON_AMBIENT_GLOBAL, 0.719, 1e-9)
    for got, want in zip(VS.MOON_EMISSIVE_TINT, (0.403480, 0.425726, 2.000000)):
        assert _close(got, want, 1e-9)
    assert VS.MOON_EMISSIVE_TINT[2] / VS.MOON_EMISSIVE_TINT[0] > 4.9
    assert VS.MOON_HAS_ATMOSPHERE_RIM is False


def test_the_wrapped_diffuse_normaliser_is_the_saturn_global_over_pi():
    """⛔ The module used to call `0.261651` "not derived".  It is exactly
    `SATURN_DIFFUSE_GLOBAL / pi`, and two shaders that do NOT fold the global in
    (the moons and the debris rock) carry the bare `0.318310` instead."""
    assert _close(VS.DIRLIGHT_DIFFUSE_K, VS.SATURN_DIFFUSE_GLOBAL / math.pi, 1e-6)
    assert _close(VS.LAMBERT_INV_PI, 1.0 / math.pi, 1e-5)
    assert _close(VS.LAMBERT_INV_PI * VS.SATURN_DIFFUSE_GLOBAL,
                  VS.DIRLIGHT_DIFFUSE_K, 1e-5)


def test_schlick5_runs_from_f0_at_normal_incidence_to_one_at_the_limb():
    f0 = (0.02, 0.03, 0.08)
    for got, want in zip(VS.schlick5(f0, 1.0), f0):
        assert _close(got, want, 1e-12)
    for got in VS.schlick5(f0, 0.0):
        assert _close(got, 1.0, 1e-12)
    assert VS.FRESNEL_EXPONENT == 5.0
    mid = VS.schlick5(f0, 0.5)
    for a, b, c in zip(f0, mid, VS.schlick5(f0, 0.0)):
        assert a < b < c


def test_smith_vis_matches_the_two_shipped_endpoints():
    """`2.346142 * Vt(c)^2`, `Vt(x) = 1/(x + sqrt(0.317010 x^2 + 0.682990))` —
    0.5865 at c = 1 and 3.4351 at c = 0, i.e. a 5.86x limb boost."""
    assert _close(VS.SATURN_VIS_A, 0.317010, 1e-9)
    assert _close(VS.SATURN_VIS_B, 0.682990, 1e-9)
    assert _close(VS.SATURN_VIS_K, 2.346142, 1e-9)
    assert _close(VS.smith_vis(1.0), 0.5865, 1e-3)
    assert _close(VS.smith_vis(0.0), 3.4351, 1e-3)
    assert VS.smith_vis(0.0) > VS.smith_vis(0.5) > VS.smith_vis(1.0)


def test_the_atmosphere_gate_is_identically_one():
    """`saturate(dot(A, (333,333,333)))`.  The minimum of `sum(F0)` over the
    plate's whole range is 0.005695, and 0.005695 * 333 = 1.896 > 1."""
    assert VS.SATURN_ATMOSPHERE_GATE_IS_ONE is True
    a_min, _ = VS.saturn_atmosphere_f0(0.0, (1.0, 1.0, 1.0))
    assert _close(sum(a_min), 0.005695, 1e-5)
    assert sum(a_min) * 333.0 > 1.0


def test_saturn_atmosphere_f0_applies_the_blend_multiplier_only_to_f0():
    """★ The asymmetry that is easy to get wrong: the CUBE branch uses `A'`
    WITHOUT the blend multiplier inside its Fresnel and applies it afterwards;
    the SG branch bakes it into `F0` BEFORE the Fresnel."""
    a, f0 = VS.saturn_atmosphere_f0(1.0, (1.0, 1.0, 1.0))
    for got, want in zip(a, VS.SATURN_ATMOSPHERE_TINT):
        assert _close(got, want, 1e-9)          # 0.05 + 0.95*1 == 1
    m = VS.blend_multiplier((1.0, 1.0, 1.0))
    assert _close(m, 4.0, 1e-9)
    for x, y in zip(f0, a):
        assert _close(x, y * m, 1e-9)
    a2, f2 = VS.saturn_atmosphere_f0(1.0, None)
    assert a2 == f2 == a


def test_the_rim_is_brighter_at_the_limb_than_at_the_disc_centre():
    """The whole point of the term: limb/centre runs 25x-480x on the shipped
    constants, which is why omitting it removes the bright limb entirely."""
    rad = (1.0, 1.0, 1.0)
    centre = VS.saturn_rim(0.5, 1.0, rad)
    limb = VS.saturn_rim(0.5, 0.0, rad)
    for c, l in zip(centre, limb):
        assert l > c
    assert limb[0] / centre[0] > 25.0


def test_the_lightmap_mode_table_gives_three_answers_not_two():
    """★★ Q3.  The mode is a property of the SHADERSET; the rings get a third
    answer because their shader binds no colour lightmap at all."""
    T = VS.LIGHTMAP_MODE_BY_SHADERSET
    assert T[VS.SATURN_SHADERSET] == "baked"
    assert T[VS.MOON_SHADERSET] == "ambient"
    assert T[VS.DEBRIS_ROCK_SHADERSET] == "ambient"
    for ss in (VS.RING_SHADERSET, VS.SKYDOME_SHADERSET, VS.HAZE_SHADERSET,
               VS.SUN_CARD_SHADERSET):
        assert T[ss] == "neither"
    assert sorted(set(T.values())) == ["ambient", "baked", "neither"]


def test_every_new_shaderset_row_names_the_shader_it_came_from():
    """⛔ The table's rule: a row only exists if its own pixel shader was
    disassembled, and the row has to say which shaderset that was."""
    for ss in (VS.SKYDOME_SHADERSET, VS.HAZE_SHADERSET, VS.RING_SHADERSET,
               VS.MOON_SHADERSET):
        row = VS.SHADERSET_TERMS[ss]
        assert row["shader"] == f"{ss} pixel shader", row
        assert row["objects"], row
        assert row["binds_clustered_lights"] in (True, False)


# =============================================================================
# 2026-08-06 — the UV chains, the layer->role join, and the ring's ambient
# =============================================================================
# Every expected number below is re-typed from the disassembly, never imported:
# Saturn's `6f67762bf83d59fd` and the ring sheet's `ba863c7b2cb61616`.

def test_the_flowmap_is_tapped_four_times_and_collapses_to_two_at_t_zero():
    """`shader-confirmed`: four taps with four
    different time scrolls, three on `uv0` and ONE (clouds) on `uv1`.  At
    `k_time0_x == 0` every scroll vanishes, so the three `uv0` taps are the same
    fetch and the whole chain needs TWO texture reads, not four."""
    uv0, uv1 = (0.2, 0.3), (0.4, 0.5)
    taps0 = {VS.saturn_flow_uv(k, uv0, uv1, 0.0) for k in
             ("plate", "spots", "wind", "clouds")}
    assert len(taps0) == 2, taps0
    taps10 = {VS.saturn_flow_uv(k, uv0, uv1, 10.0) for k in
              ("plate", "spots", "wind", "clouds")}
    assert len(taps10) == 4, taps10
    # the four scroll rates, re-typed: (0, +0.007) / (-0.010, 0) / (-0.005, 0) /
    # (-0.015, 0)
    assert VS.saturn_flow_uv("plate", uv0, uv1, 1.0) == (0.2, 0.3 + 0.007)
    assert _close(VS.saturn_flow_uv("spots", uv0, uv1, 1.0)[0], 0.2 - 0.010, 1e-12)
    assert _close(VS.saturn_flow_uv("wind", uv0, uv1, 1.0)[0], 0.2 - 0.005, 1e-12)
    assert _close(VS.saturn_flow_uv("clouds", uv0, uv1, 1.0)[0], 0.4 - 0.015, 1e-12)


def test_the_warp_quartet_is_plate_spots_wind_clouds_not_four_details():
    """⛔ The correction: `0.11` warps the BASE PLATE, it is not a fourth detail
    layer.  Re-typed: 0.110000 / 0.234690 / 0.391200 / 0.750000."""
    assert VS.SATURN_UV_CHAINS["plate"]["warp"] == 0.11
    assert VS.SATURN_UV_CHAINS["spots"]["warp"] == 0.23469
    assert VS.SATURN_UV_CHAINS["wind"]["warp"] == 0.3912
    assert VS.SATURN_UV_CHAINS["clouds"]["warp"] == 0.75
    # a flow sample of exactly 0.5 decodes to 0 and must leave the UV alone
    for k in ("plate", "spots", "wind", "clouds"):
        got = VS.saturn_layer_uv(k, (0.0, 0.0), (0.0, 0.0), (0.5, 0.5), 0.0)
        assert _close(got[0], VS.SATURN_UV_CHAINS[k]["tile_offset"][0], 1e-12)
        assert _close(got[1], VS.SATURN_UV_CHAINS[k]["tile_offset"][1], 1e-12)


def test_the_per_layer_tiles_and_the_clouds_uv_set():
    """`uv0*(9,12)` for spots, `uv0*3 + (0,-0.246)` for wind, and clouds alone on
    TEXCOORD1."""
    uv0, uv1 = (0.1, 0.2), (0.7, 0.8)
    flat = (0.5, 0.5)                                     # decodes to no warp
    assert VS.SATURN_UV_CHAINS["spots"]["tile"] == (9.0, 12.0)
    assert VS.SATURN_UV_CHAINS["wind"]["tile_offset"] == (0.0, -0.246)
    assert VS.SATURN_UV_CHAINS["clouds"]["uv"] == 1
    assert VS.SATURN_UV_CHAINS["plate"]["uv"] == 0
    s = VS.saturn_layer_uv("spots", uv0, uv1, flat, 0.0)
    assert _close(s[0], 0.1 * 9.0, 1e-12) and _close(s[1], 0.2 * 12.0, 1e-12)
    w = VS.saturn_layer_uv("wind", uv0, uv1, flat, 0.0)
    assert _close(w[0], 0.1 * 3.0, 1e-12) and _close(w[1], 0.2 * 3.0 - 0.246, 1e-12)
    c = VS.saturn_layer_uv("clouds", uv0, uv1, flat, 0.0)
    assert _close(c[0], 0.7, 1e-12) and _close(c[1], 0.8, 1e-12)


def test_the_flip_v_round_trip_is_its_own_inverse():
    """The importer stores `v_bl = 1 - v_dx`, so the node graph converts into DX
    space, does the shader's arithmetic verbatim, and converts back."""
    for v in (0.0, 0.246, 0.5, 1.0, 3.75):
        assert _close(VS.dx_uv_to_blender(VS.dx_uv_to_blender((0.3, v)))[1], v, 1e-12)
    assert VS.dx_uv_to_blender((0.3, 0.25)) == (0.3, 0.75)


def test_every_saturn_layer_names_a_role_and_the_roles_are_distinct():
    """★ The RDEF bind order `t5 nml / t6 spots / t7 wind / t8 clouds / t9 hdr`
    joined to the package's own role table, which is what makes the plate role
    verifiable.  The four chains and the three detail rows must agree."""
    roles = VS.SATURN_LAYER_ROLE
    assert set(roles) == set(VS.SATURN_UV_CHAINS)
    assert len(set(roles.values())) == 4
    assert roles["plate"] == "layer0_albedo_map"
    assert roles["spots"] == "layer1_albedo_map"
    assert roles["wind"] == "layer2_albedo_map"
    assert roles["clouds"] == "layer3_albedo_map"
    assert VS.SATURN_FLOWMAP_TEX_ROLE == "layer0_flowmap_map"
    for role, _name, _tint, _w, _c in VS.SATURN_DETAIL:
        assert role in roles.values()


def test_the_detail_plates_are_additive_with_their_own_tints_and_weights():
    """Re-typed: 0.3 x BLEND.x, 1.0 x BLEND.y, 0.6 x BLEND.z, and the plate's own
    0.434154."""
    alb = VS.saturn_albedo(1.0, {"layer1_albedo_map": 1.0,
                                 "layer2_albedo_map": 1.0,
                                 "layer3_albedo_map": 1.0}, (1.0, 1.0, 1.0))
    want_r = (0.434154
              + 0.671031 * 0.3
              + 0.389517 * 1.0
              + 0.153506 * 0.6)
    assert _close(alb[0], want_r, 1e-9)
    # gating on BLEND: with BLEND == 0 only the plate survives
    off = VS.saturn_albedo(1.0, {"layer1_albedo_map": 1.0,
                                 "layer2_albedo_map": 1.0,
                                 "layer3_albedo_map": 1.0}, (0.0, 0.0, 0.0))
    assert _close(off[0], 0.434154, 1e-9)


def test_the_ring_ambient_is_a_probe_cube_at_lod_three_point_five_six():
    """`ba863c7b2cb61616`: `aR = sqrt(saturate(rough^2 - 0.010))`,
    `lod = max(0, 10*aR - 1)`.  At the measured `M = 0.890` the spec's
    own arithmetic gives roughness 0.46691, aR 0.456070, lod 3.5607."""
    m = VS.ring_mask_m(0.0, 1.0)
    assert _close(m, 0.890, 1e-9)               # mad_sat(msk.R, 0.419, 0.890)
    r = VS.ring_roughness(m)
    assert _close(r, 0.46691, 1e-4)
    # the spec quotes 0.456070 / 3.5607 from a rounded roughness; ours is
    # 0.4560805 / 3.5608 from the unrounded literals, which agree to 1e-5
    assert _close(VS.ring_alpha_roughness(r), 0.456070, 2e-5)
    assert _close(VS.ring_env_lod(r), 3.5607, 1e-3)
    assert VS.ring_env_lod(0.05) == 0.0         # rough^2 < 0.010 -> aR = 0


def test_the_ring_env_fresnel_is_damped_by_the_roughness_not_mixed_to_white():
    """`saturate(F0col + (1-F0col)*(1-sat(N.V))^5 / (2*(aR+0.001)+1))`.
    ★ The divisor is the difference from Saturn's rim: at grazing incidence this
    Fresnel reaches 1/1.914140 = 0.5224, never 1.0."""
    plate = (0.27468, 0.28744, 0.26225)          # the measured plate median
    m = 0.890
    f0 = VS.ring_f0col(plate, m)
    assert _close(f0[0], 0.02321, 1e-4)          # re-typed from the spec table
    assert _close(f0[1], 0.03496, 1e-4)
    assert _close(f0[2], 0.05640, 1e-4)
    r = VS.ring_roughness(m)
    at_normal = VS.ring_env_fresnel(f0, 1.0, r)
    for got, want in zip(at_normal, f0):
        assert _close(got, want, 1e-12)          # f5 == 0 at N.V == 1
    at_grazing = VS.ring_env_fresnel(f0, 0.0, r)
    denom = 2.0 * (0.456070 + 0.001) + 1.0
    assert _close(denom, 1.914140, 1e-5)
    for i in range(3):
        assert _close(at_grazing[i], f0[i] + (1.0 - f0[i]) / denom, 1e-4)
        assert at_grazing[i] < 1.0               # damped, NOT mixed to white


def test_the_ring_spec_mask_kills_the_pre_arrival_band():
    """`saturate(dot(F0col, 333))`.  ⚠ Unlike Saturn's identical-looking
    gate this one DOES fire: `F0col` is driven to (0,0,0) as `M -> 1`, so the
    13.9 % pre-arrival region loses its environment term entirely."""
    plate = (0.27468, 0.28744, 0.26225)
    assert VS.RING_SPEC_MASK_K == 333.0
    assert _close(VS.ring_spec_mask(VS.ring_f0col(plate, 0.890)), 1.0, 1e-12)
    assert _close(VS.ring_spec_mask(VS.ring_f0col(plate, 1.0)), 0.0, 1e-12)
    # it only lets go in the last 0.3 % of M
    assert VS.ring_spec_mask(VS.ring_f0col(plate, 0.999)) > 0.0


def test_the_ring_ambient_scales_linearly_with_the_unfitted_per_frame_scalars():
    """`ambientSpec = envF * specMask * cube * AO_R`, then `* k_world_ambient_spec`.
    Both `AO_R` and `k_world_ambient_spec` are free here — the
    first is one texel of an engine-created default, the second is per-FRAME —
    so the test asserts LINEARITY and the 1.0 default, not a value."""
    plate = (0.27468, 0.28744, 0.26225)
    f0 = VS.ring_f0col(plate, 0.890)
    r = VS.ring_roughness(0.890)
    base = VS.ring_ambient_spec((0.02, 0.02, 0.02), f0, 0.3, r)
    for k in (2.0, 5.0, 10.0):
        got = VS.ring_ambient_spec((0.02, 0.02, 0.02), f0, 0.3, r,
                                   world_ambient_spec=k)
        for a, b in zip(got, base):
            assert _close(a, b * k, 1e-9)
        got_ao = VS.ring_ambient_spec((0.02, 0.02, 0.02), f0, 0.3, r, ao=k)
        for a, b in zip(got_ao, base):
            assert _close(a, b * k, 1e-9)
    # M = 1 -> specMask 0 -> the whole ambient term is exactly zero there
    dead = VS.ring_ambient_spec((0.02, 0.02, 0.02), VS.ring_f0col(plate, 1.0),
                                0.3, VS.ring_roughness(1.0))
    assert dead == (0.0, 0.0, 0.0)


# =============================================================================
# 2026-08-06 (late) — b9588078adab3e49, the dig-site steam/dust FX cards
# =============================================================================
# Two more shaders, and the same rule as the rest of this file: every expected
# value below is typed in again from the disassembly, never imported.  The pixel
# shader has 41 body lines and the vertex shader 56.


def test_fx_card_literals_match_the_shipped_instruction_stream():
    """Re-typed from the ps:
        mul  r0.xyzw, cb0[0].yyyy, l(-0.000647,-0.002415,-0.000434,-0.002462)
        mad  r0.xy, v3.xyxx, l(0.5,0.5,0,0), r0.xyxx
        mad  r0.xy, r0.xyxx, l(0.22,0.22,0,0), v3.xyxx
        mad  r0.zw, r0.xxxy, l(0,0,2,2), r0.zzzw
        mad_sat r0.z, r0.z, l(0.721569), l(0.082353)
        mul  r1.xy, cb0[0].yyyy, l(-0.000244, 0.002789, 0, 0)
        mad  r0.xy, r0.xyxx, l(0.5,0.5,0,0), r1.xyxx
        mul_sat r0.x, r0.x, l(0.784314)
        mul  r0.xyz, v2.xyzx, l(1.120846, 1.343614, 1.856059, 0)
    """
    for got, want in zip(VS.FX_CARD_TINT, (1.120846, 1.343614, 1.856059)):
        assert _close(got, want, 1e-9)
    for got, want in zip(VS.FX_CARD_FLOW_SCROLL, (-0.000647, -0.002415)):
        assert _close(got, want, 1e-12)
    for got, want in zip(VS.FX_CARD_DUST_SCROLL, (-0.000434, -0.002462)):
        assert _close(got, want, 1e-12)
    for got, want in zip(VS.FX_CARD_STEAM_SCROLL, (-0.000244, 0.002789)):
        assert _close(got, want, 1e-12)
    assert VS.FX_CARD_FLOW_UV_SCALE == 0.5
    assert VS.FX_CARD_WARP == 0.22
    assert VS.FX_CARD_DUST_UV_SCALE == 2.0
    assert VS.FX_CARD_STEAM_UV_SCALE == 0.5
    assert _close(VS.FX_CARD_DUST_SCALE, 0.721569, 1e-9)
    assert _close(VS.FX_CARD_DUST_BIAS, 0.082353, 1e-9)
    assert _close(VS.FX_CARD_STEAM_SCALE, 0.784314, 1e-9)
    assert VS.FX_CARD_ALPHA_GAMMA == 2.2
    assert VS.FX_CARD_VCOL_GAMMA == 2.2
    assert VS.FX_CARD_VCOL_ALPHA_POWER == 2
    assert VS.FX_CARD_ALPHA_DISCARD == 1e-4
    assert VS.FX_CARD_OUTPUT_CLAMP == 11000.0
    # the three scale/bias literals are exact 8-bit fractions, which is what
    # authored "184 / 21 / 200 out of 255" looks like after the compiler
    assert _close(VS.FX_CARD_DUST_SCALE, 184.0 / 255.0, 1e-6)
    assert _close(VS.FX_CARD_DUST_BIAS, 21.0 / 255.0, 1e-6)
    assert _close(VS.FX_CARD_STEAM_SCALE, 200.0 / 255.0, 1e-6)


def test_fx_card_rgb_reads_no_texture_and_is_constant_on_this_level():
    """★★ The whole colour path is `mul r0.xyz, v2.xyzx, C` — no sample feeds it.
    `measured`: the vertex rgb is 1.0 on all 208 vertices of obj013-obj017, so
    the shipped card's colour is a CONSTANT and every one of its three plates is
    an opacity input."""
    assert VS.fx_card_rgb((1.0, 1.0, 1.0)) == VS.FX_CARD_TINT
    for name in ("obj013", "obj014", "obj015", "obj016", "obj017"):
        assert VS.FX_CARD_VCOL_MEASURED[name]["rgb"] == (1.0, 1.0, 1.0)
    # the gamma is on rgb only, and it bites when the rgb is not 1
    dim = VS.fx_card_rgb((0.5, 0.5, 0.5))
    k = 0.5 ** 2.2
    for got, want in zip(dim, [k * c for c in VS.FX_CARD_TINT]):
        assert _close(got, want, 1e-9)
    assert _close(k, 0.217638, 1e-6)


def test_fx_card_alpha_is_the_product_of_two_gamma_curves_and_the_squared_vcol():
    """`min(1, pow(sat(aS*0.784314),2.2) * pow(sat(aD*0.721569+0.082353),2.2)
    * vcol.a^2)`.  Re-typed check at the plates' MEASURED medians (dust .A
    0.4078, steam .A 0.2275, off their own BC3/BC5 mip 0)."""
    a = VS.fx_card_alpha(0.4078, 0.2275, 1.0)
    d = (0.4078 * 0.721569 + 0.082353) ** 2.2
    s = (0.2275 * 0.784314) ** 2.2
    assert _close(a, s * d, 1e-9)
    assert _close(a, 0.0026, 5e-5)            # 0.26 % of the card's own colour
    # the vertex alpha enters SQUARED, not linearly
    assert _close(VS.fx_card_alpha(0.4078, 0.2275, 0.5), a * 0.25, 1e-12)
    # and it saturates rather than exceeding 1
    assert VS.fx_card_alpha(10.0, 10.0, 1.0) == 1.0
    assert VS.fx_card_alpha(0.0, 1.0, 1.0) == VS.fx_card_alpha(0.0, 1.0, 1.0)
    # a black steam texel kills the card outright
    assert VS.fx_card_alpha(1.0, 0.0, 1.0) == 0.0


def test_fx_card_src_alpha_is_what_separates_a_wisp_from_a_flat_pale_quad():
    """⛔ `eBlendLinearDodge`'s source factor is still unrecovered, and here it is
    NOT moot: under `(ONE, ONE)` the engine would add a constant 1.12/1.34/1.86
    over the whole card, ~430x what the alpha-weighted reading adds at the
    plates' medians.  The engine's own probe shows nothing of the kind."""
    assert VS.FX_CARD_SRC_FACTOR_IS_SRC_ALPHA is True
    weighted = VS.fx_card_added_rgb(0.4078, 0.2275, (1.0, 1.0, 1.0), 1.0,
                                    src_alpha=True)
    flat = VS.fx_card_added_rgb(0.4078, 0.2275, (1.0, 1.0, 1.0), 1.0,
                                src_alpha=False)
    assert flat == VS.FX_CARD_TINT
    a = VS.fx_card_alpha(0.4078, 0.2275, 1.0)
    for w, f in zip(weighted, flat):
        assert _close(w, f * a, 1e-12)
    assert flat[0] / weighted[0] > 300.0
    # the 1e-4 discard zeroes the contribution under BOTH readings
    assert VS.fx_card_added_rgb(1.0, 0.0, (1.0, 1.0, 1.0), 1.0) == (0.0, 0.0, 0.0)
    assert VS.fx_card_added_rgb(1.0, 0.0, (1.0, 1.0, 1.0), 1.0,
                                src_alpha=False) == (0.0, 0.0, 0.0)
    # ★ and it fires on the SHIPPED data: at the plates' medians the discard
    # threshold sits at vertex alpha 0.19494, and the five cards' measured mean
    # vertex alpha is 0.1556-0.1745 — i.e. over the average vertex this card is
    # thrown away entirely, which is why the reference art shows nothing there.
    assert _close((VS.FX_CARD_ALPHA_DISCARD / a) ** 0.5, 0.19494, 1e-4)
    for name in ("obj013", "obj014", "obj015", "obj016", "obj017"):
        mean_a = VS.FX_CARD_VCOL_MEASURED[name]["a_mean"]
        assert mean_a < 0.19494
        assert VS.fx_card_added_rgb(0.4078, 0.2275, (1.0, 1.0, 1.0),
                                    mean_a) == (0.0, 0.0, 0.0)


def test_fx_card_uv_chain_collapses_its_scrolls_at_t_zero():
    """The three scrolls all come off `k_time0_x`, so a still (t = 0) leaves a
    plain `uv*0.5` flow tap and the two plate UVs at `warped*2` / `warped*0.5`.
    ⚠ `mad r0.xy, r0.xyxx, l(0.22,...), v3.xyxx` warps the RAW uv, not `uv*0.5`."""
    uv = (0.2, 0.3)
    assert VS.fx_card_flow_uv(uv, 0.0) == (0.1, 0.15)
    assert _close(VS.fx_card_flow_uv(uv, 1.0)[0], 0.1 - 0.000647, 1e-12)
    assert _close(VS.fx_card_flow_uv(uv, 1.0)[1], 0.15 - 0.002415, 1e-12)
    # a flow sample of exactly 0.5 decodes to 0 and must leave the UV alone
    assert VS.fx_card_warped_uv(uv, (0.5, 0.5)) == uv
    warped = VS.fx_card_warped_uv(uv, (1.0, 0.0))
    assert _close(warped[0], 0.2 + 0.22, 1e-12)
    assert _close(warped[1], 0.3 - 0.22, 1e-12)
    assert VS.fx_card_plate_uv("dust", uv, 0.0) == (0.4, 0.6)
    assert VS.fx_card_plate_uv("steam", uv, 0.0) == (0.1, 0.15)
    assert _close(VS.fx_card_plate_uv("dust", uv, 1.0)[1], 0.6 - 0.002462, 1e-12)
    assert _close(VS.fx_card_plate_uv("steam", uv, 1.0)[1], 0.15 + 0.002789, 1e-12)


def test_fx_card_row_is_in_the_table_and_binds_no_lightmap():
    row = VS.SHADERSET_TERMS[VS.FX_CARD_SHADERSET]
    assert VS.FX_CARD_SHADERSET == "b9588078adab3e49"
    assert row["shader"] == "b9588078adab3e49 pixel shader"
    assert row["objects"] == ("obj013", "obj014", "obj015", "obj016", "obj017")
    assert row["binds_clustered_lights"] is False
    assert row["binds_perframe"] is True          # cb0 only, for k_time0_x
    assert row["ambient"] is None
    assert VS.LIGHTMAP_MODE_BY_SHADERSET[VS.FX_CARD_SHADERSET] == "neither"
    # the roles the material record carries do NOT describe what the shader does
    assert row["roles"]["dust"] == "layer0_albedo_map"
    assert row["roles"]["steam"] == "layer0_emissive_map"
    assert row["roles"]["flow"] == "layer0_flowmap_map"


# ---------------------------------------------------------------------------
# ★★ The scene-fog epilogue — the tail of `6f67762bf83d59fd`'s pixel shader
# ---------------------------------------------------------------------------

def test_the_fog_ramp_lookup_is_a_256_texel_half_texel_inset():
    """Re-typed from the disassembly, both ramps:

        mad r2.x, r1.x, l(0.996094), l(0.001953)
        mad r2.x, r1.y, l(0.996094), l(0.001953)

    `0.996094 == 255/256` and `0.001953 == 1/512`, i.e. the classic "sample the
    centre of texel `t*255`" inset for a **256-wide** ramp — which is how wide
    `k_fog_ramp` is, without having to open it."""
    assert VS.FOG_RAMP_U_SCALE == 0.996094
    assert VS.FOG_RAMP_U_BIAS == 0.001953
    assert _close(VS.FOG_RAMP_U_SCALE, 255.0 / 256.0, 4e-7)
    assert _close(VS.FOG_RAMP_U_BIAS, 1.0 / 512.0, 4e-7)
    # the inset lands the two ends on the centres of texel 0 and texel 255
    assert _close(VS.fog_ramp_u(0.0) * 256.0, 0.5, 1e-3)
    assert _close(VS.fog_ramp_u(1.0) * 256.0, 255.5, 1e-3)
    # and it saturates, because the stream's ramp input is `div_sat`
    assert VS.fog_ramp_u(-5.0) == VS.fog_ramp_u(0.0)
    assert VS.fog_ramp_u(5.0) == VS.fog_ramp_u(1.0)
    # slice 0 is the DISTANCE ramp (`mov r2.yz, l(0,0,0,0)`) and slice 1 the
    # HEIGHT ramp (`mov r2.yz, l(0,0,1.000000,0)`) — not the other way round
    assert VS.FOG_RAMP_SLICE_DISTANCE == 0
    assert VS.FOG_RAMP_SLICE_HEIGHT == 1


def test_fog_ramp_t_is_the_div_sat_of_the_two_depth_pairs():
    """`div_sat` for the distance ramp and again for the height ramp."""
    assert VS.fog_ramp_t(500.0, 100.0, 900.0) == 0.5
    assert VS.fog_ramp_t(50.0, 100.0, 900.0) == 0.0     # saturate, not negative
    assert VS.fog_ramp_t(9000.0, 100.0, 900.0) == 1.0
    assert VS.fog_ramp_t(5.0, 3.0, 3.0) == 0.0          # degenerate range


def test_the_fog_lerp_is_a_lerp_and_its_endpoints_are_the_two_readings():
    """The stream's `mad r0 = f * (C.rgb*k_fog_color.rgb - colour) + colour` is
    an ordinary lerp, and the `max o0.xyz, ..., 0` after it clamps at 0."""
    c = (0.04, 0.03, 0.02)
    assert VS.scene_fog(c, (1.0, 1.0, 1.0), 0.0) == c
    assert VS.scene_fog(c, (0.5, 0.25, 0.125), 1.0) == (0.5, 0.25, 0.125)
    half = VS.scene_fog(c, (0.0, 0.0, 0.0), 0.5)
    assert _close(half[0], 0.02, 1e-12)
    # `max o0.xyz, r0.xzwx, l(0,0,0,0)` — the output cannot go negative
    assert VS.scene_fog((0.1, 0.1, 0.1), (-1.0, -1.0, -1.0), 1.0) == (0.0, 0.0, 0.0)


def test_fog_colour_and_factor_lerps_low_to_hi_by_the_HEIGHT_ramp():
    """
        C = lerp(k_fog_low_color, k_fog_hi_color, Ah)      /* rgba, all four */
        f = C.a * k_fog_color.a * Ad
    ★ the ALPHA is lerped by the same height ramp as the colour, which is what
    lets one ramp make the fog both dimmer AND thinner with altitude."""
    low = (0.20, 0.30, 0.40, 0.90)
    hi = (0.00, 0.00, 0.00, 0.10)
    kfog = (1.0, 1.0, 1.0, 1.0)
    rgb, f = VS.fog_colour_and_factor(1.0, 0.0, low, hi, kfog)
    assert rgb == (0.20, 0.30, 0.40) and _close(f, 0.90, 1e-12)
    rgb, f = VS.fog_colour_and_factor(1.0, 1.0, low, hi, kfog)
    assert rgb == (0.0, 0.0, 0.0) and _close(f, 0.10, 1e-12)
    # the DISTANCE ramp multiplies the factor only, never the colour
    rgb2, f2 = VS.fog_colour_and_factor(0.5, 0.0, low, hi, kfog)
    assert rgb2 == (0.20, 0.30, 0.40)
    assert _close(f2, 0.45, 1e-12)
    # k_fog_color multiplies both lanes
    rgb3, f3 = VS.fog_colour_and_factor(1.0, 0.0, low, hi, (0.5, 0.5, 0.5, 0.5))
    assert _close(rgb3[0], 0.10, 1e-12) and _close(f3, 0.45, 1e-12)


def test_which_vista_shadersets_carry_the_fog_epilogue():
    """`measured` by searching every disassembled shader for `k_fog_ramp`.
    ★ The split is the load-bearing
    part: the debris rock multiplies the SAME `cb0[1].yzw` `k_world_ambient` and
    carries NO fog, which is what separates `k_world_ambient` from `(1 - f)` in
    the probe measurement."""
    assert VS.SATURN_SHADERSET in VS.SHADERSET_BINDS_FOG
    assert VS.RING_SHADERSET in VS.SHADERSET_BINDS_FOG
    assert VS.SUN_CARD_SHADERSET in VS.SHADERSET_BINDS_FOG
    assert len(VS.SHADERSET_BINDS_FOG) == 3
    for ss in (VS.SKYDOME_SHADERSET, VS.HAZE_SHADERSET, VS.FX_CARD_SHADERSET,
               VS.MOON_SHADERSET, VS.DEBRIS_ROCK_SHADERSET):
        assert ss in VS.SHADERSET_NO_FOG
        assert ss not in VS.SHADERSET_BINDS_FOG
    assert not (VS.SHADERSET_BINDS_FOG & VS.SHADERSET_NO_FOG)


def test_the_probe_residual_bounds_the_fog_factor_without_assuming_a_colour():
    """`p = (1-f)*c + f*F` with `F >= 0` gives `1 - f <= p/c` POINTWISE, so the
    measured render/probe ratio on Saturn's sunward disc is a hard bound.
    ⚠ It bounds the PRODUCT `k_world_ambient * (1 - f)`; the unfogged debris-rock
    control is what pins `k_world_ambient` near 1."""
    ratios = (0.1372, 0.1476, 0.1535, 0.1455, 0.1500, 0.1524,
              0.1493, 0.1447, 0.1658)
    mean = sum(ratios) / len(ratios)
    assert _close(VS.SATURN_PROBE_UNFOGGED_RESIDUAL, mean, 5e-4)
    sd = (sum((r - mean) ** 2 for r in ratios) / (len(ratios) - 1)) ** 0.5
    assert _close(VS.SATURN_PROBE_UNFOGGED_RESIDUAL_SD, sd, 1e-3)
    # ⇒ at least 85 % of the engine's own Saturn disc is fog, not surface
    assert 1.0 - max(ratios) > 0.83
    assert VS.SATURN_PROBE_UNFOGGED_RESIDUAL < 0.16
