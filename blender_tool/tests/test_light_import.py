"""Archive-free, bpy-free tests for `addon/lone_echo_import/light_import.py`.

Everything here runs under plain `python3` (and unchanged under pytest):
`light_import` keeps `bpy` inside the functions that need it, so its sidecar
reader, selection policy, unit conversions and axis transform are all testable
without Blender. The in-Blender half lives in `tests/blender_light_probe.py`.

FIXTURES ARE CONSTRUCTED, NOT EXTRACTED. This repository ships no game data, so
the sidecar under test is built here: six `SGLightParams` records are packed byte
by byte at literal offsets, decoded by `le_mesh.lights`, and serialised by the
real extractor (`extractor/le_lights.write_lights_json`). That exercises the
whole chain — pack -> decode -> sidecar -> add-on — without a single shipped byte.

The *shapes* the fixtures encode are the ones measured across 118 shipped light
records (see `docs/LIGHTING.md`): mostly specular-only lights, a single
directional, `direction == R(orientation)·(0,0,1)`, `farp == attenuation.z`,
`2·acos(penumbra.y) == fovy` on spots and `penumbra == (-1,-1)` elsewhere, and
`cachedjointidx`/`jointoffsetidx` both `0xffffffff`. Asserting them here locks
the conversion code; it does not re-prove the corpus measurement, which lives in
the docs.

To run the same tests against YOUR OWN extraction instead, point
`LONE_ECHO_LIGHTS_JSON` at a `lights.json` written by `extractor/le_lights.py`;
the cross-check below then also compares this module's arithmetic against the
`blender` block that extractor independently computed.
"""
from __future__ import annotations

import json
import math
import os
import struct
import sys
from pathlib import Path
from unittest import SkipTest

_ROOT = Path(__file__).resolve().parents[1]          # .../blender_tool
_ADDON = _ROOT / "addon" / "lone_echo_import"
_EXTRACTOR = _ROOT / "extractor"
for _p in (str(_ROOT), str(_ADDON), str(_EXTRACTOR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import light_import as LI                            # noqa: E402
import le_lights as LX                               # noqa: E402  (the extractor)
from le_mesh import lights as L                      # noqa: E402
from scatter_reader import basis_matrix              # noqa: E402


# ---------------------------------------------------------------------------
# Fixture builder — the 352-byte record, written at literal offsets
# ---------------------------------------------------------------------------

NULL_U32 = 0xFFFFFFFF
NULL_SYM = 0xFFFFFFFFFFFFFFFF


def _quat_forward(q):
    """R(q) * (0,0,1) — the light's stored `direction`."""
    x, y, z, w = q
    return (2.0 * (x * z + y * w), 2.0 * (y * z - x * w), 1.0 - 2.0 * (x * x + y * y))


def _pack_light(*, options, lighttype, pos, primarycolor, attenuation, orientation,
                fovy=0.0, penumbra=(-1.0, -1.0), attenmethod=2.0, filtersize=2.0,
                name=0x0123456789ABCDEF) -> bytes:
    """One `SGLightParams`. `farp` and `direction` are derived, exactly as every
    shipped record stores them."""
    rec = bytearray(L.STRIDE)
    struct.pack_into("<II", rec, 0x00, options, lighttype)
    struct.pack_into("<3f", rec, 0x08, *pos)
    struct.pack_into("<3f", rec, 0x14, *primarycolor)
    struct.pack_into("<3f", rec, 0x20, 1.0, 1.0, 1.0)          # secondarycolor
    struct.pack_into("<4f", rec, 0x2C, *attenuation)
    struct.pack_into("<4f", rec, 0x3C, *orientation)
    struct.pack_into("<f", rec, 0x4C, fovy)
    struct.pack_into("<f", rec, 0x50, 0.01)                    # nearp
    struct.pack_into("<f", rec, 0x54, attenuation[2])          # farp == atten.z
    struct.pack_into("<f", rec, 0x58, filtersize)
    struct.pack_into("<3f", rec, 0x5C, *_quat_forward(orientation))
    struct.pack_into("<2f", rec, 0x68, *penumbra)
    struct.pack_into("<f", rec, 0x74, attenmethod)
    struct.pack_into("<f", rec, 0x78, 1e-4)                    # bias
    struct.pack_into("<Q", rec, 0xD0, NULL_SYM)                # gobo asset id
    struct.pack_into("<I", rec, 0xE0, NULL_U32)                # visindex
    struct.pack_into("<I", rec, 0xE4, 7)                       # qualitylevel
    struct.pack_into("<Q", rec, 0xE8, NULL_SYM)                # quantizer
    struct.pack_into("<Q", rec, 0x120, name)
    struct.pack_into("<I", rec, 0x150, 4)                      # shadowqualitylevel
    struct.pack_into("<I", rec, 0x158, NULL_U32)               # cachedjointidx
    struct.pack_into("<I", rec, 0x15C, NULL_U32)               # jointoffsetidx
    return bytes(rec)


_ON = L.eLightTransparents | L.eLightOpaques | L.eLightEnabled | L.eCastShadows
_D = L.eEnableDiffuse
_S = L.eEnableSpecular


def _spot(**kw):
    ci, co = math.cos(0.2), math.cos(0.5)      # -> fovy = 2*acos(co) = 1.0 rad
    kw.setdefault("fovy", 2.0 * math.acos(co))
    kw.setdefault("penumbra", (ci, co))
    return _pack_light(lighttype=L.eSpotLight, **kw)


_Q_YAW90 = (0.0, math.sin(math.pi / 4), 0.0, math.cos(math.pi / 4))
_Q_PITCH = (math.sin(-math.pi / 12), 0.0, 0.0, math.cos(math.pi / 12))
_Q_SUN = (math.sin(-math.pi / 8), 0.0, 0.0, math.cos(math.pi / 8))

#: six records shaped like a shipped level: mostly specular-only, one directional,
#: one disabled, and one non-quadratic (lossy) falloff.
RECORD_BYTES = (
    # 0: point, enabled, diffuse + specular
    _pack_light(options=_ON | _D | _S, lighttype=L.ePointLight,
                pos=(2.5, 12.0, -4.0), primarycolor=(12.0, 15.0, 20.0),
                attenuation=(1.0, 75.5, 150.0, 150.0), orientation=_Q_YAW90),
    # 1: spot, enabled, SPECULAR ONLY
    _spot(options=_ON | _S, pos=(-3.0, 8.0, 40.0), primarycolor=(5.0, 4.0, 3.0),
          attenuation=(1.0, 500.5, 1000.0, 1000.0), orientation=_Q_PITCH),
    # 2: spot, enabled, diffuse + specular, attenmethod 1 -> LOSSY in Blender
    _spot(options=_ON | _D | _S, pos=(10.0, 4.0, 0.0), primarycolor=(6.0, 6.0, 6.0),
          attenuation=(1.0, 100.5, 200.0, 800.0), orientation=_Q_PITCH,
          attenmethod=1.0, filtersize=5.0),
    # 3: spot, DISABLED (shipped levels do carry these), specular only
    _spot(options=_S | L.eLightOpaques, pos=(0.0, 0.0, 0.0),
          primarycolor=(1.0, 1.0, 1.0), attenuation=(1.0, 25.5, 50.0, 50.0),
          orientation=_Q_PITCH, filtersize=1.0),
    # 4: the level's single directional light
    _pack_light(options=_ON | _D | _S | L.ePrimaryDirLight,
                lighttype=L.eDirectionalLight, pos=(0.0, 300.0, 0.0),
                primarycolor=(10.0, 8.0, 6.0),
                attenuation=(1.0, 250.5, 500.0, 500.0), orientation=_Q_SUN,
                filtersize=7.0),
    # 5: point, enabled, SPECULAR ONLY
    _pack_light(options=_ON | _S, lighttype=L.ePointLight,
                pos=(-20.0, 1.5, 6.0), primarycolor=(3.0, 3.5, 4.0),
                attenuation=(1.0, 40.5, 80.0, 80.0), orientation=_Q_YAW90),
)

SCENE_NAME = "synthetic_test_scene"


def _build_doc() -> dict:
    """The v2 sidecar, produced by the REAL extractor serialiser."""
    recs = [L.decode_light(b, i) for i, b in enumerate(RECORD_BYTES)]
    scenes = [{"scene_hash": "0000000000000001", "scene_name": SCENE_NAME,
               "scene_size": 0, "bvh_triangle_bytes": 0, "bvh_node_bytes": 0,
               "num_lights": len(recs), "lights": recs}]
    doc = {
        "format": LX.SIDECAR_FORMAT, "version": LX.SIDECAR_VERSION,
        "archive": "0000000000000000", "source": "synthetic",
        "axis": "native", "record": "SGLightParams/352",
        "summary": LX.summarise(scenes), "scenes": [],
    }
    for s in scenes:
        doc["scenes"].append({
            "scene_hash": s["scene_hash"], "scene_name": s["scene_name"],
            "num_lights": s["num_lights"],
            "lights": [LX._light_json(r) for r in s["lights"]],
        })
    return doc


def _to_v1(doc: dict) -> dict:
    """The v1 shape: no `summary`, `options` as a NAME LIST, no `lighttype`/`matrix`."""
    out = json.loads(json.dumps(doc))
    out["version"] = 1
    out.pop("summary", None)
    out.pop("source", None)
    for scene in out["scenes"]:
        for rec in scene["lights"]:
            rec.pop("lighttype", None)
            rec.pop("index", None)
            rec["blender"].pop("matrix", None)
            for k in ("not_derivable", "peak_radiance", "use_shadow", "attenmethod",
                      "enabled", "affects_diffuse", "affects_specular"):
                rec["blender"].pop(k, None)
    return out


DOC = _build_doc()
DOC_V1 = _to_v1(DOC)

#: optional: your own extraction, for a real-data cross-check
REAL_SIDECAR = os.environ.get("LONE_ECHO_LIGHTS_JSON")


def _approx(a, b, tol=1e-6):
    assert abs(a - b) <= tol, f"{a} != {b} (tol {tol})"


def _doc():
    return LI.load_lights(json.loads(json.dumps(DOC)))


def _records():
    return [r for _, _, r in LI.iter_lights(_doc())]


def _as_le_record(rec):
    """Same light, decoded through `le_mesh.lights` — the reference implementation."""
    return L.record_from_fields(rec, int(rec.get("index", 0)))


# ---------------------------------------------------------------------------
# sidecar contract
# ---------------------------------------------------------------------------

def test_fixture_sidecar_is_well_formed():
    doc = _doc()
    assert doc["format"] == "le_lights" and doc["version"] == 2
    assert doc["record"] == "SGLightParams/352", "the 352 B stride, not the 360 B one"
    assert doc["axis"] == "native", "the sidecar stays in GAME space"
    s = doc["summary"]
    assert s["lights"] == 6
    assert s["by_type"] == {"eSpotLight": 3, "ePointLight": 2, "eDirectionalLight": 1}
    assert s["diffuse_enabled"] == 3
    assert s["specular_enabled"] == 6
    assert s["specular_only"] == 3
    assert s["enabled"] == 5, "one spot ships with eLightEnabled clear"
    assert s["lossy_falloff"] == 1, "one light uses attenmethod 1"


def test_load_lights_accepts_dict_and_rejects_a_non_sidecar():
    doc = _doc()
    assert LI.load_lights(doc) is doc
    try:
        LI.load_lights({"format": "le_scatter"})
    except ValueError:
        return
    raise AssertionError("expected ValueError on a non-le_lights document")


def test_v1_sidecar_still_loads_and_decodes_its_option_names():
    doc = LI.load_lights(json.loads(json.dumps(DOC_V1)))
    assert doc["version"] == 1
    recs = [r for _, _, r in LI.iter_lights(doc)]
    assert len(recs) == 6
    # v1 carries `options` as a NAME LIST and no `lighttype` int
    assert isinstance(recs[0]["options"], list)
    assert "lighttype" not in recs[0]
    assert LI.options_word(recs[0]) == recs[0]["options_raw"]
    assert LI.light_type_enum(recs[0]) == 0            # ePointLight
    assert LI.light_type_enum(recs[1]) == 1            # eSpotLight
    assert LI.light_type_enum(recs[4]) == 2            # eDirectionalLight


def test_scene_filter_selects_one_scene():
    doc = _doc()
    assert len(LI.iter_lights(doc, SCENE_NAME)) == 6
    assert len(LI.iter_lights(doc, "no_such_scene")) == 0


# ---------------------------------------------------------------------------
# THE AXIS — a light rig rotated relative to the geometry is a silent bug
# ---------------------------------------------------------------------------

def test_axis_basis_is_THE_basis_not_a_second_convention():
    """`light_import.axis_rows` must equal `scatter_reader.basis_matrix()` (which
    `mesh_builder._axis_matrix` and `le_mesh.lights.to_blender_vec` also match)."""
    B = basis_matrix()
    A = LI.axis_rows(True)
    for r in range(3):
        for c in range(3):
            _approx(A[r][c], B[r][c], 0.0)
    for v in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (3.0, -7.0, 11.5)):
        assert LI.to_blender_vec(v) == L.to_blender_vec(v)


def test_axis_matrix_is_a_pure_rotation_det_plus_one_no_mirror():
    A = LI.axis_rows(True)
    det = (A[0][0] * (A[1][1] * A[2][2] - A[1][2] * A[2][1])
           - A[0][1] * (A[1][0] * A[2][2] - A[1][2] * A[2][0])
           + A[0][2] * (A[1][0] * A[2][1] - A[1][1] * A[2][0]))
    _approx(det, 1.0, 1e-12)


def test_light_matrix_forward_is_the_records_direction_on_every_light():
    """The alignment proof: a Blender lamp emits along its local -Z, so
    `M[:3,:3] @ (0,0,-1)` must equal the axis-converted stored `direction`
    (which is `R(orientation)*(0,0,1)` on 118/118 shipped records)."""
    for rec in _records():
        M = LI.light_matrix_rows(rec)
        fwd = tuple(-M[i][2] for i in range(3))
        want = LI.to_blender_vec(rec["direction"])
        n = math.sqrt(sum(c * c for c in want)) or 1.0
        want = tuple(c / n for c in want)
        for i in range(3):
            _approx(fwd[i], want[i], 2e-6)


def test_light_matrix_rotation_is_orthonormal_det_plus_one():
    for rec in _records():
        M = LI.light_matrix_rows(rec)
        det = (M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1])
               - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
               + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0]))
        _approx(det, 1.0, 1e-5)
        for c in range(3):
            col = [M[r][c] for r in range(3)]
            _approx(math.sqrt(sum(v * v for v in col)), 1.0, 1e-5)
        assert M[3] == (0.0, 0.0, 0.0, 1.0)


def test_light_matrix_translation_is_the_axis_converted_world_pos():
    """`pos` is ALREADY world (no transform join, no parent chain), so the
    translation is just `A @ pos`."""
    for rec in _records():
        M = LI.light_matrix_rows(rec)
        t = (M[0][3], M[1][3], M[2][3])
        assert t == LI.to_blender_vec(rec["pos"])


def test_axis_can_be_disabled_and_then_is_identity():
    rec = _records()[0]
    M = LI.light_matrix_rows(rec, y_up_to_z_up=False)
    assert (M[0][3], M[1][3], M[2][3]) == tuple(float(c) for c in rec["pos"])


def test_light_matrix_matches_le_mesh_lights_exactly():
    for rec in _records():
        got = LI.light_matrix_rows(rec)
        want = L.blender_matrix_rows(_as_le_record(rec))
        for r in range(4):
            for c in range(4):
                _approx(got[r][c], want[r][c], 1e-6)


# ---------------------------------------------------------------------------
# units — the conversion table, no fudge factors
# ---------------------------------------------------------------------------

def test_color_is_primarycolor_over_its_peak_linear_both_sides():
    for rec in _records():
        color, peak = LI.normalized_color(rec["primarycolor"])
        _approx(peak, max(rec["primarycolor"]), 1e-6)
        assert max(color) <= 1.0 + 1e-9
        for i in range(3):
            _approx(color[i] * peak, rec["primarycolor"][i], 1e-4)


def test_point_and_spot_energy_is_four_pi_times_peak():
    for rec in _records():
        if LI.light_type_enum(rec) == 2:
            continue
        peak = max(rec["primarycolor"])
        _approx(LI.blender_energy(rec), 4.0 * math.pi * peak, 1e-4)
        # the physical identity: Blender's P*C/(4*pi*d^2) == primarycolor/d^2
        color, _ = LI.normalized_color(rec["primarycolor"])
        for i in range(3):
            _approx(LI.blender_energy(rec) * color[i] / (4.0 * math.pi),
                    rec["primarycolor"][i], 1e-3)


def test_sun_energy_is_irradiance_not_watts():
    suns = [r for r in _records() if LI.light_type_enum(r) == 2]
    assert len(suns) == 1, "a level ships one ePrimaryDirLight"
    sun = suns[0]
    # no distance term at all: energy is the peak irradiance, W/m^2
    _approx(LI.blender_energy(sun), 10.0, 1e-4)
    color, _ = LI.normalized_color(sun["primarycolor"])
    _approx(color[0], 1.0, 1e-6)
    _approx(color[1], 0.8, 1e-6)
    _approx(color[2], 0.6, 1e-6)
    assert LI.blender_params(sun)["type"] == "SUN"


def test_spot_size_is_fovy_and_blend_is_the_acos_ratio():
    spots = [r for r in _records() if LI.light_type_enum(r) == 1]
    assert len(spots) == 3
    for rec in spots:
        size, blend = LI.blender_spot(rec)
        _approx(size, rec["fovy"], 0.0)
        ti = math.acos(rec["penumbra"][0])
        to = math.acos(rec["penumbra"][1])
        _approx(blend, 1.0 - ti / to, 1e-9)
        assert 0.0 <= blend <= 1.0
    # non-spots carry no cone at all
    for rec in _records():
        if LI.light_type_enum(rec) != 1:
            assert LI.blender_spot(rec) == (0.0, 0.0)
            assert tuple(rec["penumbra"]) == (-1.0, -1.0)


def test_cutoff_distance_is_attenuation_z():
    for rec in _records():
        _approx(LI.light_range(rec), rec["attenuation"][2], 0.0)
        assert LI.blender_params(rec)["cutoff_distance"] == rec["attenuation"][2]


def test_units_match_le_mesh_lights_on_every_record():
    """The add-on duplicates the math so it can ship self-contained; this pins it
    against `le_mesh.lights` so the two can never drift."""
    for rec in _records():
        ref = L.to_blender(_as_le_record(rec))
        got = LI.blender_params(rec)
        assert got["type"] == ref["type"]
        _approx(got["energy"], ref["energy"], 1e-4)
        for i in range(3):
            _approx(got["color"][i], ref["color"][i], 1e-9)
        _approx(got["spot_size"], ref["spot_size"], 0.0)
        _approx(got["spot_blend"], ref["spot_blend"], 1e-12)
        _approx(got["cutoff_distance"], ref["cutoff_distance"], 0.0)
        assert got["use_shadow"] == ref["use_shadow"]
        assert got["physical_falloff"] == ref["physical_falloff"]
        assert got["shadow_soft_size"] == ref["shadow_soft_size"] == 0.0


def test_addon_math_matches_the_extractor_sidecar_blender_block():
    """Cross-implementation check against the numbers the extractor's serialiser
    wrote out on its own, in BOTH sidecar versions."""
    for src in (DOC, DOC_V1):
        doc = LI.load_lights(json.loads(json.dumps(src)))
        for _, _, rec in LI.iter_lights(doc):
            b = rec["blender"]
            got = LI.blender_params(rec)
            assert got["type"] == b["type"]
            _approx(got["energy"], b["energy"], 1e-6)
            for i in range(3):
                _approx(got["color"][i], b["color"][i], 1e-9)
            _approx(got["spot_size"], b["spot_size"], 0.0)
            _approx(got["spot_blend"], b["spot_blend"], 1e-9)
            _approx(got["cutoff_distance"], b["cutoff_distance"], 0.0)
            loc = (got["matrix"][0][3], got["matrix"][1][3], got["matrix"][2][3])
            for i in range(3):
                _approx(loc[i], b["location"][i], 1e-6)


def test_exposure_scale_defaults_to_one_no_fudge_factor():
    rec = _records()[0]
    assert LI.DEFAULT_OPTS["exposure_scale"] == 1.0
    base = LI.blender_params(rec)["energy"]
    _approx(base, 4.0 * math.pi * max(rec["primarycolor"]), 1e-4)
    scaled = LI.blender_params(rec, {"exposure_scale": 2.5})["energy"]
    _approx(scaled, base * 2.5, 1e-4)


# ---------------------------------------------------------------------------
# corpus invariants (proved on 118 shipped records; re-asserted on the fixture)
# ---------------------------------------------------------------------------

def test_direction_equals_quaternion_local_plus_z():
    worst = 0.0
    for rec in _records():
        R = LI.quat_matrix_rows(rec["orientation"])
        f = (R[0][2], R[1][2], R[2][2])                # R @ (0,0,1)
        for i in range(3):
            worst = max(worst, abs(f[i] - rec["direction"][i]))
    assert worst < 2e-6, worst


def test_farp_equals_attenuation_z_and_attenuation_x_is_one():
    for rec in _records():
        _approx(rec["farp"], rec["attenuation"][2], 1e-3)
        _approx(rec["attenuation"][0], 1.0, 0.0)


def test_two_acos_penumbra_y_equals_fovy_on_every_spot():
    spots = [r for r in _records() if LI.light_type_enum(r) == 1]
    for rec in spots:
        _approx(2.0 * math.acos(rec["penumbra"][1]), rec["fovy"], 2e-3)


def test_no_shipped_light_is_joint_attached():
    """No transform join is needed: `cachedjointidx`/`jointoffsetidx` are
    0xFFFFFFFF on every shipped level light."""
    for rec in _records():
        assert rec["cachedjointidx"] == 0xFFFFFFFF
        assert rec["jointoffsetidx"] == 0xFFFFFFFF


# ---------------------------------------------------------------------------
# import policy — the double-lighting guard
# ---------------------------------------------------------------------------

def test_importer_is_off_by_default_and_defaults_to_the_diffuse_subset():
    assert LI.DEFAULT_OPTS["import_lights"] is False
    assert LI.DEFAULT_OPTS["light_set"] == LI.LIGHT_SET_DIFFUSE
    assert LI.DEFAULT_OPTS["skip_disabled"] is True
    assert LI.DEFAULT_OPTS["hide_specular_only"] is True


def test_default_selection_keeps_only_the_diffuse_lights():
    kept, stats = LI.select(_records())
    assert stats["total"] == 6
    assert stats["kept"] == 3 == len(kept)
    assert stats["skipped_specular_only"] == 2
    assert stats["skipped_disabled"] == 1
    assert stats["specular_only_kept"] == 0
    assert all(LI.affects_diffuse(r) for r in kept)


def test_all_light_set_keeps_everything_enabled_and_counts_the_double_lighters():
    kept, stats = LI.select(_records(), {"light_set": LI.LIGHT_SET_ALL})
    assert stats["kept"] == 5 == len(kept)
    assert stats["specular_only_kept"] == 2
    assert stats["skipped_specular_only"] == 0
    assert stats["specular_only"] == 3


def test_disabled_lights_are_skipped_by_default():
    recs = _records()
    kept, stats = LI.select(recs, {"light_set": LI.LIGHT_SET_ALL})
    assert stats["skipped_disabled"] == 1
    assert len(kept) == 5
    _, stats2 = LI.select(recs, {"light_set": LI.LIGHT_SET_ALL,
                                 "skip_disabled": False})
    assert stats2["kept"] == 6 and stats2["skipped_disabled"] == 0


def test_selection_matches_le_mesh_lights_policy():
    recs = _records()
    le_recs = [_as_le_record(r) for r in recs]
    for mode in (LI.LIGHT_SET_DIFFUSE, LI.LIGHT_SET_ALL):
        _, a = LI.select(recs, {"light_set": mode})
        _, b = L.select_lights(le_recs, mode)
        for k in ("total", "kept", "skipped_disabled", "skipped_specular_only",
                  "specular_only_kept", "diffuse_enabled", "specular_enabled"):
            assert a[k] == b[k], (mode, k, a[k], b[k])


def test_select_rejects_an_unknown_light_set():
    try:
        LI.select(_records(), {"light_set": "everything"})
    except ValueError:
        return
    raise AssertionError("expected ValueError on an unknown light_set")


def test_summarize_doc_is_a_dry_run_needing_no_bpy():
    s = LI.summarize_doc(_doc())
    assert s["total"] == 6 and s["kept"] == 3
    s_all = LI.summarize_doc(_doc(), {"light_set": LI.LIGHT_SET_ALL})
    assert s_all["kept"] == 5


# ---------------------------------------------------------------------------
# ⛔ nothing fabricated
# ---------------------------------------------------------------------------

def test_shadow_soft_size_is_zero_filtersize_is_never_used_as_a_radius():
    """`SGLightParams` has NO source-size field. `filtersize` is a shadow-map PCF
    width (1/2/5/7 texels) — using it as a radius would be fabrication."""
    seen = set()
    for rec in _records():
        p = LI.blender_params(rec)
        assert p["shadow_soft_size"] == 0.0
        seen.add(rec["filtersize"])
        assert p["custom"]["le_filtersize_pcf_not_a_radius"] == rec["filtersize"]
    assert seen and seen <= {1.0, 2.0, 5.0, 7.0}, seen


def test_undecodable_fields_are_carried_as_inert_custom_props():
    rec = _records()[0]
    c = LI.blender_params(rec)["custom"]
    for k in ("le_filtersize_pcf_not_a_radius", "le_cone_falloff_exponent",
              "le_faderangeoffset_runtime", "le_lightmask", "le_scenemask",
              "le_visindex", "le_qualitylevel", "le_attenuation_maxfadedistance",
              "le_affects_diffuse", "le_affects_specular"):
        assert k in c, k
    # ... and none of them leaks into a converted value
    p = LI.blender_params(rec)
    assert p["shadow_soft_size"] == 0.0
    assert "falloff" not in p and "lightmask" not in p


def test_uint32_custom_props_do_not_overflow_a_signed_int():
    """`visindex == 0xFFFFFFFF` overflows Blender's 32-bit signed ID int prop."""
    rec = _records()[0]
    c = LI.blender_params(rec)["custom"]
    assert rec["visindex"] == 0xFFFFFFFF
    assert c["le_visindex"] == str(0xFFFFFFFF)
    assert isinstance(c["le_qualitylevel"], int)


# ---------------------------------------------------------------------------
# the one systematic, quantified divergence
# ---------------------------------------------------------------------------

def test_range_offset_divergence_is_quantified_not_hidden():
    """Blender has no range-offset term: at half range an imported inverse-square
    light is 4/3 as bright as the game (the game is 25 % dimmer).

    ⚠ That closed form is `(1/d^m) / (1/d^m - 1/w^m)` at `d = range/2`, and it
    collapses to 4/3 (m=2) or 2 (m=1) ONLY when `w == range`. Since `w` is
    `attenuation.w` = `maxfadedistance`, not the range, the identity holds on the
    lights where the two agree and NOT on the one where they diverge — which is
    the whole point of resolving `.w`, so it is asserted separately instead of
    being averaged away. One fixture light is built to diverge for exactly this
    reason (the shipped corpus has 11 such lights in 118).
    """
    same, diff = [], []
    for r in _records():
        a = list(r.get("attenuation") or ())
        (diff if (len(a) == 4 and float(a[3]) > 0.0
                  and abs(float(a[3]) - float(a[2])) > 1e-9) else same).append(r)
    assert len(diff) == 1, len(diff)

    quad = [r for r in same
            if LI.falloff_is_physical(r) and LI.light_type_enum(r) != 2]
    assert len(quad) == 4
    for rec in quad:
        _approx(LI.brightness_divergence(rec, 0.5), 4.0 / 3.0, 1e-9)
    # linear (attenmethod 1) lights are 2x at half range
    lin = [r for r in same if abs(LI.attenmethod(r) - 1.0) < 1e-6
           and LI.light_type_enum(r) != 2]
    for rec in lin:
        _approx(LI.brightness_divergence(rec, 0.5), 2.0, 1e-9)

    # the divergent light: the closed form does NOT collapse, and resolving `.w`
    # made the import MORE faithful there, not less.
    rec = diff[0]
    z, w = float(rec["attenuation"][2]), float(rec["attenuation"][3])
    m = LI.attenmethod(rec)
    d = z * 0.5
    _approx(LI.brightness_divergence(rec, 0.5),
            (1.0 / d ** m) / (1.0 / d ** m - 1.0 / w ** m), 1e-9)
    assert LI.brightness_divergence(rec, 0.5) < 4.0 / 3.0


def test_range_offset_uses_maxfadedistance_not_the_range():
    """★ `attenuation.w` is `maxfadedistance` (`shader-confirmed`, see
    `le_mesh.lights.LightRecord.maxfadedistance`), NOT a second cull radius. The
    range stays `.z` and is still what `cutoff_distance` uses; only the offset
    term moves. One fixture light is built with `.w != .z` so the distinction is
    witnessed rather than silently agreeing everywhere."""
    recs = _records()
    assert recs
    moved = 0
    for rec in recs:
        a = list(rec.get("attenuation") or ())
        assert len(a) == 4
        z, w = float(a[2]), float(a[3])
        assert LI.maxfadedistance(rec) == (w if w > 0.0 else z)
        assert LI.light_range(rec) == z
        assert LI.blender_params(rec)["cutoff_distance"] == z
        m = LI.attenmethod(rec)
        if w > 0.0 and abs(w - z) > 1e-9:
            moved += 1
            expect = 1.0 / (w ** m) if m != 0.0 else w
            _approx(LI.range_offset(rec), expect, 1e-12)
    assert moved == 1, moved

def test_range_offset_matches_le_mesh_lights():
    for rec in _records():
        _approx(LI.range_offset(rec), L.range_offset(_as_le_record(rec)), 1e-12)


def test_non_quadratic_falloff_is_flagged_lossy():
    lossy = [r for r in _records() if not LI.falloff_is_physical(r)]
    assert len(lossy) == 1
    for rec in lossy:
        assert LI.blender_params(rec)["custom"]["le_falloff_lossy"] is True
    for rec in _records():
        if LI.falloff_is_physical(rec):
            assert LI.blender_params(rec)["custom"]["le_falloff_lossy"] is False


def test_light_names_tag_the_diffuse_split():
    for rec in _records():
        n = LI.light_name(rec)
        assert n.startswith("lelight_")
        assert ("_D_" in n) == LI.affects_diffuse(rec)
        assert ("_S_" in n) != LI.affects_diffuse(rec)


def test_sidecar_json_is_loadable_and_self_consistent():
    raw = json.loads(json.dumps(DOC))
    assert raw["summary"]["lights"] == sum(s["num_lights"] for s in raw["scenes"])
    for s in raw["scenes"]:
        assert len(s["lights"]) == s["num_lights"]


# ---------------------------------------------------------------------------
# optional: the same checks against YOUR OWN extraction
# ---------------------------------------------------------------------------

def test_real_sidecar_if_supplied():
    """Set `LONE_ECHO_LIGHTS_JSON` to a `lights.json` you extracted yourself and
    this re-runs the invariants and the cross-implementation check on it."""
    if not REAL_SIDECAR or not Path(REAL_SIDECAR).is_file():
        _why = ("is unset" if not REAL_SIDECAR
                else f"points at {REAL_SIDECAR}, which is not a file")
        raise SkipTest(
            f"the `LONE_ECHO_LIGHTS_JSON` environment variable {_why}"
            f" — it names a `lights.json` you extracted yourself with "
            f"`python.exe blender_tool/extractor/le_lights.py <archive-hash> "
            f"--out lights.json`, and setting it re-runs the light invariants "
            f"and the cross-implementation check against that REAL sidecar. "
            f"⛔ WHILE THIS SKIP IS ACTIVE EVERY LIGHT ASSERTION IN THIS FILE "
            f"RUNS ONLY ON SYNTHETIC AND HARDCODED RECORDS — NO EXTRACTED "
            f"`SGLightParams` RECORD IS CHECKED.")
    doc = LI.load_lights(Path(REAL_SIDECAR))
    recs = [r for _, _, r in LI.iter_lights(doc)]
    assert recs, "the supplied sidecar carries no lights"
    for rec in recs:
        R = LI.quat_matrix_rows(rec["orientation"])
        for i in range(3):
            _approx((R[0][2], R[1][2], R[2][2])[i], rec["direction"][i], 2e-6)
        _approx(rec["farp"], rec["attenuation"][2], 1e-3)
        ref = L.to_blender(_as_le_record(rec))
        got = LI.blender_params(rec)
        assert got["type"] == ref["type"]
        _approx(got["energy"], ref["energy"], 1e-4)
        assert got["shadow_soft_size"] == 0.0
