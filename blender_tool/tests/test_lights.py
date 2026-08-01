"""Archive-free tests for `le_mesh.lights` — SGLightParams decode + Blender units.

Fixtures are **constructed, not extracted**: `_pack_light` writes a 352-byte
`SGLightParams` record field by field at literal offsets, independently of the
decoder's own offset table, so the two must agree or the tests fail. This repo
ships no game bytes, so the alternative — pasting shipped records in as hex — is
deliberately not used.

The *shapes* the fixtures encode are the ones measured across 118 shipped light
records (see `docs/LIGHTING.md`): `direction == R(orientation)·(0,0,1)`,
`farp == attenuation.z`, `attenuation.y == (x + z)/2`, `2·acos(penumbra.y) == fovy`
for spots and `penumbra == (-1,-1)` for everything else, and the `SPad<4>` at
`0x154` being zero. Those are asserted here on records built to satisfy them, which
locks the decoder and the unit arithmetic — it does not re-prove the corpus
measurement, which lives in the docs.

Runs under `python3 blender_tool/tests/run_tests.py` and unchanged under pytest.
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]          # .../blender_tool
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from le_mesh import lights as L  # noqa: E402

# The pure basis lives in the addon package; import it standalone so the test can
# assert le_mesh.lights uses THE SAME Y-up->Z-up convention (no second basis).
_ADDON = _ROOT / "addon" / "lone_echo_import"
if str(_ADDON) not in sys.path:
    sys.path.insert(0, str(_ADDON))
from scatter_reader import basis_matrix  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture builder — the 352-byte record, written at literal offsets
# ---------------------------------------------------------------------------

NULL_U32 = 0xFFFFFFFF
NULL_SYM = 0xFFFFFFFFFFFFFFFF


def _quat_forward(q):
    """R(q) * (0,0,1) — the light's stored `direction`."""
    x, y, z, w = q
    return (2.0 * (x * z + y * w), 2.0 * (y * z - x * w), 1.0 - 2.0 * (x * x + y * y))


def _pack_light(*, options, lighttype, pos, primarycolor, attenuation,
                orientation, fovy=0.0, nearp=0.01, filtersize=2.0,
                penumbra=(-1.0, -1.0), falloff=0.0, attenmethod=1.0,
                name=0x0123456789ABCDEF, qualitylevel=7, shadowqualitylevel=4,
                lightmask=0) -> bytes:
    """One `SGLightParams` record. `farp` and `direction` are derived, exactly as
    every shipped record stores them (`farp == attenuation.z`,
    `direction == R(orientation)·(0,0,1)`)."""
    rec = bytearray(L.STRIDE)
    struct.pack_into("<II", rec, 0x00, options, lighttype)
    struct.pack_into("<3f", rec, 0x08, *pos)
    struct.pack_into("<3f", rec, 0x14, *primarycolor)
    struct.pack_into("<3f", rec, 0x20, 1.0, 1.0, 1.0)         # secondarycolor
    struct.pack_into("<4f", rec, 0x2C, *attenuation)
    struct.pack_into("<4f", rec, 0x3C, *orientation)
    struct.pack_into("<f", rec, 0x4C, fovy)
    struct.pack_into("<f", rec, 0x50, nearp)
    struct.pack_into("<f", rec, 0x54, attenuation[2])         # farp == attenuation.z
    struct.pack_into("<f", rec, 0x58, filtersize)
    struct.pack_into("<3f", rec, 0x5C, *_quat_forward(orientation))
    struct.pack_into("<2f", rec, 0x68, *penumbra)
    struct.pack_into("<f", rec, 0x70, falloff)
    struct.pack_into("<f", rec, 0x74, attenmethod)
    struct.pack_into("<f", rec, 0x78, 1e-4)                   # bias
    struct.pack_into("<Q", rec, 0xD0, NULL_SYM)               # lightshaft goboassetid
    struct.pack_into("<I", rec, 0xDC, lightmask)
    struct.pack_into("<I", rec, 0xE0, NULL_U32)               # visindex
    struct.pack_into("<I", rec, 0xE4, qualitylevel)
    struct.pack_into("<Q", rec, 0xE8, NULL_SYM)               # quantizer
    struct.pack_into("<Q", rec, 0x120, name)
    struct.pack_into("<I", rec, 0x150, shadowqualitylevel)
    # 0x154 SPad<4> stays zero — the layout alignment check
    struct.pack_into("<I", rec, 0x158, NULL_U32)              # cachedjointidx
    struct.pack_into("<I", rec, 0x15C, NULL_U32)              # jointoffsetidx
    return bytes(rec)


# A point light: enabled, diffuse-only (no eEnableSpecular), yawed 90 deg about +Y
# so its forward is +X and the direction check is non-trivial.
_Q_YAW90 = (0.0, math.sin(math.pi / 4), 0.0, math.cos(math.pi / 4))
REC0 = _pack_light(
    options=(L.eEnableDiffuse | L.eLightTransparents | L.eLightOpaques
             | L.eLightEnabled),
    lighttype=L.ePointLight,
    pos=(2.5, 12.0, -4.0),
    primarycolor=(12.0, 15.0, 20.0),
    attenuation=(1.0, 75.5, 150.0, 150.0),      # y == (x + z) / 2
    orientation=_Q_YAW90,
    attenmethod=1.0,
)

# A spot: NOT enabled (shipped levels do carry disabled spots), a 1.0 rad full
# cone, and `attenuation.w != attenuation.z` — the 11-of-118 shape.
_COS_INNER = math.cos(0.2)      # theta_inner = 0.2 rad
_COS_OUTER = math.cos(0.5)      # theta_outer = 0.5 rad -> fovy = 1.0 rad
_Q_PITCH = (math.sin(-math.pi / 12), 0.0, 0.0, math.cos(math.pi / 12))
REC2 = _pack_light(
    options=(L.eEnableSpecular | L.eLightOpaques),
    lighttype=L.eSpotLight,
    pos=(-3.0, 8.0, 40.0),
    primarycolor=(5.0, 4.0, 3.0),
    attenuation=(1.0, 500.5, 1000.0, 5000.0),   # .w deliberately != .z
    orientation=_Q_PITCH,
    fovy=1.0,
    penumbra=(_COS_INNER, _COS_OUTER),
    attenmethod=1.0,
)

# A directional light (`ePrimaryDirLight`), so the SUN unit path — irradiance,
# no distance term — is exercised too. Enabled and diffuse-enabled.
_Q_SUN = (math.sin(-math.pi / 8), 0.0, 0.0, math.cos(math.pi / 8))
REC_SUN = _pack_light(
    options=(L.eEnableDiffuse | L.eEnableSpecular | L.eLightOpaques
             | L.eLightEnabled | L.ePrimaryDirLight),
    lighttype=L.eDirectionalLight,
    pos=(0.0, 300.0, 0.0),
    primarycolor=(10.0, 8.0, 6.0),
    attenuation=(1.0, 250.5, 500.0, 500.0),
    orientation=_Q_SUN,
    attenmethod=2.0,
)

ALL_RECS = (REC0, REC2, REC_SUN)


def _approx(a, b, tol=1e-4):
    assert abs(a - b) <= tol, f"{a} != {b} (tol {tol})"


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------

def test_stride_and_fixture_sizes():
    assert L.STRIDE == 0x160 == 352
    assert L.STRIDE != L.STRIDE_R15, "352 must not be confused with the 360-B revision"
    assert len(REC0) == L.STRIDE and len(REC2) == L.STRIDE


def test_decode_point_light_fields():
    r = L.decode_light(REC0, index=0)
    assert r.lighttype == L.ePointLight and r.type_name == "ePointLight"
    assert r.enabled and r.affects_diffuse and not r.affects_specular
    assert not r.bake_only
    _approx(r.pos[0], 2.5); _approx(r.pos[1], 12.0); _approx(r.pos[2], -4.0)
    _approx(r.primarycolor[0], 12.0)
    _approx(r.primarycolor[1], 15.0)
    _approx(r.primarycolor[2], 20.0)
    assert r.secondarycolor == (1.0, 1.0, 1.0)
    _approx(r.attenuation[0], 1.0); _approx(r.attenuation[1], 75.5)
    _approx(r.attenuation[2], 150.0); _approx(r.attenuation[3], 150.0)
    _approx(r.farp, 150.0)
    _approx(r.attenmethod, 1.0)
    assert r.penumbra == (-1.0, -1.0), "point lights carry no cone"
    assert r.cachedjointidx == L.NULL_U32 and r.jointoffsetidx == L.NULL_U32
    assert r.quantizer == L.NULL_SYMBOL
    assert r.lightshaft["goboassetid"] == L.NULL_SYMBOL
    assert len(r.scenemask) == 0x28


def test_decode_spot_light_fields():
    r = L.decode_light(REC2, index=2)
    assert r.lighttype == L.eSpotLight
    assert not r.enabled, "a disabled spot must decode as disabled"
    _approx(r.fovy, 1.0, 1e-4)
    _approx(r.penumbra[0], _COS_INNER, 1e-5)
    _approx(r.penumbra[1], _COS_OUTER, 1e-5)
    _approx(r.attenuation[2], 1000.0)
    _approx(r.attenuation[3], 5000.0), "the 4th component is NOT always the range"
    _approx(r.falloff, 0.0)
    _approx(r.attenmethod, 1.0)


def test_option_flag_names_roundtrip():
    r = L.decode_light(REC0)
    names = r.option_names
    assert "eLightEnabled" in names and "eEnableDiffuse" in names
    assert not any(n.startswith("unk:") for n in names), names
    # every named bit is a real ELightOptions value and reconstructs the raw word
    rebuilt = 0
    for bit, name in L.OPTION_NAMES:
        if name in names:
            rebuilt |= bit
    assert rebuilt == r.options


def test_pad_at_0x154_is_zero():
    """SPad<4> between shadowqualitylevel and cachedjointidx — a cheap alignment
    check that the 352-byte layout is not off by a field."""
    for rec in (REC0, REC2):
        assert struct.unpack_from("<I", rec, 0x154)[0] == 0


# ---------------------------------------------------------------------------
# cross-field invariants (measured on 118 shipped lights; see docs/LIGHTING.md)
# ---------------------------------------------------------------------------

def test_direction_equals_quaternion_local_plus_z():
    for rec in (REC0, REC2):
        r = L.decode_light(rec)
        f = _quat_forward(r.orientation)
        for i in range(3):
            _approx(r.direction[i], f[i], 1e-5)


def test_farp_equals_attenuation_z():
    for rec in (REC0, REC2):
        r = L.decode_light(rec)
        _approx(r.farp, r.attenuation[2], 1e-3)
        assert r.range == r.attenuation[2]


def test_fovy_is_twice_the_outer_half_angle():
    r = L.decode_light(REC2)
    _approx(2.0 * math.acos(r.penumbra[1]), r.fovy, 2e-3)


def test_attenuation_y_is_the_derived_midpoint():
    for rec in (REC0, REC2):
        a = L.decode_light(rec).attenuation
        _approx(a[1], (a[0] + a[2]) / 2.0, 1e-3)


# ---------------------------------------------------------------------------
# table decode
# ---------------------------------------------------------------------------

def test_decode_lights_table():
    buf = struct.pack("<I", 2) + REC0 + REC2
    recs, end = L.decode_lights_table(buf, 0)
    assert len(recs) == 2 and end == len(buf)
    assert [r.index for r in recs] == [0, 1]
    assert recs[0].lighttype == L.ePointLight
    assert recs[1].lighttype == L.eSpotLight


def test_decode_lights_table_empty():
    recs, end = L.decode_lights_table(struct.pack("<I", 0), 0)
    assert recs == [] and end == 4


def test_decode_lights_table_rejects_truncation():
    buf = struct.pack("<I", 4) + REC0
    try:
        L.decode_lights_table(buf, 0)
    except ValueError:
        return
    raise AssertionError("expected ValueError on a truncated lights table")


# ---------------------------------------------------------------------------
# game -> Blender conversion
# ---------------------------------------------------------------------------

def test_basis_matches_scatter_reader_exactly():
    """le_mesh.lights must use THE basis, not a second convention."""
    B = basis_matrix()
    for v in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (3.0, -7.0, 11.5)):
        expect = tuple(B[r][0] * v[0] + B[r][1] * v[1] + B[r][2] * v[2] for r in range(3))
        assert L.to_blender_vec(v) == expect, (v, L.to_blender_vec(v), expect)


def test_normalized_color_and_energy_point():
    r = L.decode_light(REC0)
    color, peak = L.normalized_color(r.primarycolor)
    _approx(peak, 20.0, 1e-3)
    _approx(max(color), 1.0)
    _approx(color[0], 12.0 / 20.0, 1e-5)
    # P = 4*pi*peak so that Blender's P*C/(4*pi*d^2) == primarycolor/d^2
    _approx(L.blender_energy(r), 4.0 * math.pi * 20.0, 1e-2)
    # round-trip the physical identity at d = 1 m
    d = 1.0
    for i in range(3):
        blender = L.blender_energy(r) * color[i] / (4.0 * math.pi * d * d)
        _approx(blender, r.primarycolor[i], 1e-3)


def test_sun_energy_is_irradiance_not_watts():
    r = L.decode_light(REC0)
    object.__setattr__(r, "lighttype", L.eDirectionalLight)
    _, peak = L.normalized_color(r.primarycolor)
    _approx(L.blender_energy(r), peak, 1e-3)
    # a directional light has no distance term in the engine either
    assert L.engine_irradiance(r, 1.0) == tuple(r.primarycolor)
    assert L.engine_irradiance(r, 42.0) == tuple(r.primarycolor)


def test_blender_spot_size_and_blend():
    r = L.decode_light(REC2)
    size, blend = L.blender_spot(r)
    _approx(size, r.fovy, 1e-6)
    ti = math.acos(r.penumbra[0])
    to = math.acos(r.penumbra[1])
    _approx(blend, 1.0 - ti / to, 1e-6)
    assert 0.0 <= blend <= 1.0
    # non-spots get no cone
    assert L.blender_spot(L.decode_light(REC0)) == (0.0, 0.0)


def test_blender_direction_is_unit_and_axis_converted():
    r = L.decode_light(REC2)
    d = L.blender_direction(r)
    _approx(math.sqrt(sum(c * c for c in d)), 1.0, 1e-5)
    assert d == L.to_blender_vec(tuple(
        c / math.sqrt(sum(v * v for v in r.direction)) for c in r.direction))


def test_range_offset_makes_attenuation_zero_at_range():
    r = L.decode_light(REC0)              # attenmethod 1, range 150
    assert not L.falloff_is_physical(r)
    val = L.engine_irradiance(r, r.range)
    for c in val:
        _approx(c, 0.0, 1e-3)
    # and it is strictly brighter closer in
    near = L.engine_irradiance(r, 1.0)
    assert near[0] > 0.0


def test_engine_irradiance_matches_inverse_square_when_attenmethod_is_2():
    r = L.decode_light(REC0)
    object.__setattr__(r, "attenmethod", 2.0)
    assert L.falloff_is_physical(r)
    _approx(L.range_offset(r), 1.0 / (r.range ** 2), 1e-9)
    d = 3.0
    expect = r.primarycolor[0] * (1.0 / (d * d) - 1.0 / (r.range ** 2))
    _approx(L.engine_irradiance(r, d)[0], expect, 1e-6)


def test_to_blender_shape():
    b = L.to_blender(L.decode_light(REC2, index=2))
    assert b["type"] == "SPOT"
    assert set(b) >= {"location", "direction", "color", "energy", "spot_size",
                      "spot_blend", "shadow_soft_size", "cutoff_distance",
                      "physical_falloff", "affects_diffuse", "affects_specular",
                      "matrix", "not_derivable"}
    assert b["shadow_soft_size"] == 0.0, "no source radius exists on disk"
    assert b["enabled"] is False
    assert len(b["color"]) == 3 and max(b["color"]) <= 1.0 + 1e-6


# ---------------------------------------------------------------------------
# SUN: irradiance, not watts
# ---------------------------------------------------------------------------

def test_sun_energy_is_irradiance_with_no_distance_term():
    r = L.decode_light(REC_SUN, index=2)
    assert r.lighttype == L.eDirectionalLight
    assert L.BLENDER_TYPE[r.lighttype] == "SUN"
    # the engine applies no attenuation to a directional light
    _approx(L.blender_energy(r), max(r.primarycolor), 1e-5)
    for d in (1.0, 50.0, 5000.0):
        assert L.engine_irradiance(r, d) == tuple(r.primarycolor)
    color, peak = L.normalized_color(r.primarycolor)
    _approx(peak, 10.0, 1e-5)
    _approx(color[0], 1.0, 1e-6)
    _approx(color[1], 0.8, 1e-6)
    _approx(color[2], 0.6, 1e-6)


# ---------------------------------------------------------------------------
# encode: the exact inverse of decode (fixtures without an archive)
# ---------------------------------------------------------------------------

def test_encode_light_is_byte_exact_on_every_fixture():
    """Re-encoding a record must reproduce its 352 bytes exactly — the strongest
    available check that no field is mis-sized or mis-placed. The three unnamed
    pad words at 0xA4/0xCC/0x154 are zero on every shipped record inspected, so
    the encoder writing zeros there is not a divergence."""
    for rec in ALL_RECS:
        r = L.decode_light(rec)
        assert L.encode_light(r) == rec
    for off in L.PAD_OFFSETS:
        for rec in ALL_RECS:
            assert struct.unpack_from("<I", rec, off)[0] == 0, off


def test_decode_encode_roundtrip_preserves_every_field():
    for rec in ALL_RECS:
        a = L.decode_light(rec, 7)
        b = L.decode_light(L.encode_light(a), 7)
        assert a == b


def test_encode_rejects_a_bad_scenemask_length():
    r = L.decode_light(REC0)
    object.__setattr__(r, "scenemask", b"\x00" * 8)
    try:
        L.encode_light(r)
    except ValueError:
        return
    raise AssertionError("expected ValueError on a short scenemask")


def _fields_of(src, *, option_names=False):
    """The decoded record as the plain field dict a sidecar carries."""
    d = {
        "index": src.index,
        "pos": list(src.pos), "primarycolor": list(src.primarycolor),
        "secondarycolor": list(src.secondarycolor),
        "attenuation": list(src.attenuation), "orientation": list(src.orientation),
        "direction": list(src.direction), "penumbra": list(src.penumbra),
        "fovy": src.fovy, "nearp": src.nearp, "farp": src.farp,
        "filtersize": src.filtersize, "falloff": src.falloff,
        "attenmethod": src.attenmethod, "bias": src.bias,
        "shadowfadestart": src.shadowfadestart, "shadowfadeend": src.shadowfadeend,
        "shadowthrottledist": src.shadowthrottledist, "fade": src.fade,
        "shadowresolution": src.shadowresolution,
        "shadowoffsetscale": src.shadowoffsetscale,
        "lightoffsetstart": src.lightoffsetstart,
        "lightoffsetdist": src.lightoffsetdist,
        "lightshaft": {**src.lightshaft,
                       "goboassetid": f"{src.lightshaft['goboassetid']:016x}"},
        "airlightminradius": src.airlightminradius, "lightmask": src.lightmask,
        "visindex": src.visindex, "qualitylevel": src.qualitylevel,
        "quantizer": f"{src.quantizer:016x}", "signal": list(src.signal),
        "shadowangularfade": list(src.shadowangularfade),
        "name": f"{src.name:016x}", "scenemask": src.scenemask.hex(),
        "shadowqualitylevel": src.shadowqualitylevel,
        "cachedjointidx": src.cachedjointidx, "jointoffsetidx": src.jointoffsetidx,
    }
    if option_names:                       # the v1 sidecar form
        d["options"] = src.option_names
        d["type"] = src.type_name
    else:
        d["options_raw"] = src.options
        d["lighttype"] = src.lighttype
    return d


def test_record_from_fields_rebuilds_a_record_from_a_plain_dict():
    """The archive-free fixture path: a decoded field dict -> LightRecord, via
    encode/decode so the result is exactly what a real decode would give."""
    src = L.decode_light(REC2, 2)
    d = _fields_of(src)
    assert L.record_from_fields(d) == src
    assert L.encode_light(L.record_from_fields(d)) == REC2


def test_record_from_fields_accepts_option_names_and_a_type_name():
    """The v1 sidecar form: `options` as a NAME LIST, `type` as a name."""
    src = L.decode_light(REC0, 0)
    r = L.record_from_fields(_fields_of(src, option_names=True))
    assert r.options == src.options and r.lighttype == src.lighttype


# ---------------------------------------------------------------------------
# the object matrix — same basis as the meshes, lamp-forward flip
# ---------------------------------------------------------------------------

def test_blender_matrix_forward_is_the_records_direction():
    """A Blender lamp emits along local -Z; the engine's forward is local +Z. So
    `M[:3,:3] @ (0,0,-1)` must equal the axis-converted stored `direction`."""
    for rec in ALL_RECS:
        r = L.decode_light(rec)
        M = L.blender_matrix_rows(r)
        fwd = tuple(-M[i][2] for i in range(3))
        want = L.blender_direction(r)
        for i in range(3):
            _approx(fwd[i], want[i], 2e-6)


def test_blender_matrix_is_a_pure_rotation_and_places_the_light_at_A_pos():
    for rec in ALL_RECS:
        r = L.decode_light(rec)
        M = L.blender_matrix_rows(r)
        det = (M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1])
               - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
               + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0]))
        _approx(det, 1.0, 1e-5)
        assert (M[0][3], M[1][3], M[2][3]) == L.blender_position(r)
        assert M[3] == (0.0, 0.0, 0.0, 1.0)


def test_blender_matrix_uses_the_shared_basis():
    """With `y_up_to_z_up=False` the matrix is the raw game transform, and the
    difference between the two is exactly `basis_matrix()`."""
    r = L.decode_light(REC2)
    M = L.blender_matrix_rows(r, y_up_to_z_up=True)
    N = L.blender_matrix_rows(r, y_up_to_z_up=False)
    B = basis_matrix()
    for i in range(3):
        for j in range(4):
            expect = sum(B[i][k] * N[k][j] for k in range(3))
            _approx(M[i][j], expect, 1e-9)


# ---------------------------------------------------------------------------
# import policy + the not-derivable list
# ---------------------------------------------------------------------------

def test_select_lights_defaults_to_the_diffuse_subset():
    recs = [L.decode_light(b, i) for i, b in enumerate(ALL_RECS)]
    # REC0: enabled + diffuse. REC2: DISABLED. REC_SUN: enabled + diffuse.
    kept, stats = L.select_lights(recs)
    assert stats["total"] == 3
    assert stats["skipped_disabled"] == 1
    assert [r.index for r in kept] == [0, 2]
    assert stats["kept"] == 2 and stats["specular_only_kept"] == 0


def test_select_lights_all_keeps_specular_only_and_counts_them():
    r = L.decode_light(REC0, 0)
    object.__setattr__(r, "options", (r.options & ~L.eEnableDiffuse) | L.eEnableSpecular)
    kept, stats = L.select_lights([r], L.LIGHT_SET_ALL)
    assert stats["kept"] == 1 and stats["specular_only_kept"] == 1
    kept2, stats2 = L.select_lights([r], L.LIGHT_SET_DIFFUSE)
    assert kept2 == [] and stats2["skipped_specular_only"] == 1


def test_select_lights_rejects_an_unknown_set():
    try:
        L.select_lights([], "everything")
    except ValueError:
        return
    raise AssertionError("expected ValueError on an unknown light_set")


def test_not_derivable_carries_the_unconvertible_fields():
    """Nothing here may be turned into a Blender value — `filtersize` is a
    shadow-map PCF width, not a light radius, and there is no source-size field
    on disk at all."""
    r = L.decode_light(REC2, 2)
    nd = L.not_derivable(r)
    for k in ("filtersize_pcf", "cone_falloff_exponent", "faderangeoffset_runtime",
              "lightmask", "scenemask", "visindex", "qualitylevel",
              "attenuation_w", "affects_diffuse", "affects_specular"):
        assert k in nd, k
    assert nd["filtersize_pcf"] == r.filtersize
    assert L.to_blender(r)["shadow_soft_size"] == 0.0


def test_range_offset_divergence_is_quantified_not_hidden():
    """Blender has no range-offset term: at half range an imported inverse-square
    light is 4/3 as bright as the game (the game is 25 % dimmer)."""
    r = L.decode_light(REC0)
    object.__setattr__(r, "attenmethod", 2.0)
    game_over_blender, blender_over_game = L.range_offset_divergence(r, 0.5)
    _approx(game_over_blender, 0.75, 1e-9)
    _approx(blender_over_game, 4.0 / 3.0, 1e-9)
    # a linear (attenmethod 1) light is 2x at half range
    object.__setattr__(r, "attenmethod", 1.0)
    _approx(L.range_offset_divergence(r, 0.5)[1], 2.0, 1e-9)
    # a directional light has no distance term at all
    assert L.range_offset_divergence(L.decode_light(REC_SUN), 0.5) == (1.0, 1.0)
