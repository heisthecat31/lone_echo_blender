"""Tests for `le_mesh.lights_sidecar` — the probe-dump -> `le_lights` adapter.

The adapter's whole contract is "copy through, re-shape the two option fields,
invent nothing", so that is what is asserted here. The real-data tests state
`>=` floors and raise `unittest.SkipTest` with a reason when the probe dump is
absent — a missing artefact is reported, never counted as a pass.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import SkipTest

_ROOT = Path(__file__).resolve().parents[1]          # .../blender_tool
_ADDON = _ROOT / "addon" / "lone_echo_import"
for _p in (str(_ROOT), str(_ADDON)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from le_mesh import lights_sidecar as LS             # noqa: E402
import light_import as LI                            # noqa: E402

_REPO = _ROOT.parent
PROBE = _REPO / "scratchpad" / "lights_bridge_night.json"
BRIDGE_LIGHTS = _ROOT / "exports" / "bridge" / "lights_night.json"


def _probe_light(options=0b100000001, **over):
    rec = {
        "options": options,
        "options_names": "eEnableDiffuse|eLightEnabled",
        "lighttype": 1, "lighttype_name": "eSpotLight",
        "pos": [1.0, 2.0, 3.0], "primarycolor": [4.0, 2.0, 1.0],
        "attenuation": [1.0, 0.5, 6.0, 6.0],
        "orientation": [0.0, 0.0, 0.0, 1.0], "fovy": 1.0,
        "penumbra": [0.9, 0.7], "attenmethod": 2.0, "name": "abc",
    }
    rec.update(over)
    return rec


def _probe_doc(lights=None, archive="deadbeefdeadbeef"):
    return [{"archive": archive, "scenes": [
        {"scene_hash": archive, "scene_name": "a_scene",
         "lights": lights if lights is not None else [_probe_light()]},
        {"scene_hash": "0" * 16, "scene_name": "", "lights": []},
    ]}]


# --- the option pair ----------------------------------------------------------

def test_option_names_round_trip_through_the_bit_table():
    for word in (0, 1, 0b101, 229722, 237915):
        names = LS.option_names(word)
        assert sum(LS.OPTION_BITS[n] for n in names) == word


def test_options_word_reads_the_int_form():
    assert LS.options_word({"options": 229722}) == 229722
    assert LS.options_word({"options_raw": 7, "options": ["eEnableDiffuse"]}) == 7


def test_options_word_reads_the_pipe_string_form():
    rec = {"options_names": "eEnableDiffuse|eLightEnabled"}
    assert LS.options_word(rec) == LS.eEnableDiffuse | LS.eLightEnabled


def test_normalize_emits_both_spellings_and_drops_the_probe_only_key():
    out = LS.normalize_record(_probe_light(), 3)
    assert out["options_raw"] == 0b100000001
    assert out["options"] == ["eEnableDiffuse", "eLightEnabled"]
    assert "options_names" not in out
    assert out["index"] == 3
    assert out["type"] == "eSpotLight"


def test_every_decoded_field_is_copied_through_untouched():
    src = _probe_light()
    out = LS.normalize_record(src, 0)
    for k, v in src.items():
        if k in ("options", "options_names"):
            continue
        assert out[k] == v, k


# --- the sidecar --------------------------------------------------------------

def test_sidecar_declares_the_format_the_addon_requires():
    sc = LS.sidecar_from_probe(_probe_doc())
    assert sc["format"] == LI.SIDECAR_FORMAT
    assert sc["version"] >= 2
    assert sc["archive"] == "deadbeefdeadbeef"


def test_sidecar_keeps_empty_scenes_and_counts_only_real_lights():
    sc = LS.sidecar_from_probe(_probe_doc([_probe_light(), _probe_light()]))
    assert len(sc["scenes"]) == 2
    assert sc["summary"]["lights"] == 2
    assert sc["summary"]["scenes_with_lights"] == 1


def test_summary_counts_the_diffuse_subset():
    spec_only = _probe_light(options=LS.eEnableSpecular | LS.eLightEnabled)
    sc = LS.sidecar_from_probe(_probe_doc([_probe_light(), spec_only]))
    assert sc["summary"]["diffuse_enabled"] == 1
    assert sc["summary"]["specular_enabled"] == 1


def test_multiple_archives_require_an_explicit_choice():
    doc = _probe_doc(archive="aaaa") + _probe_doc(archive="bbbb")
    try:
        LS.sidecar_from_probe(doc)
    except ValueError as exc:
        assert "archive" in str(exc)
    else:
        raise AssertionError("expected a ValueError naming the ambiguity")
    assert LS.sidecar_from_probe(doc, "bbbb")["archive"] == "bbbb"


def test_unknown_archive_names_what_was_available():
    try:
        LS.sidecar_from_probe(_probe_doc(archive="aaaa"), "zzzz")
    except ValueError as exc:
        assert "aaaa" in str(exc)
    else:
        raise AssertionError("expected a ValueError listing the archives present")


# --- the addon must accept what we emit ---------------------------------------

def test_addon_light_importer_accepts_the_emitted_sidecar():
    sc = LS.sidecar_from_probe(_probe_doc())
    doc = LI.load_lights(sc)
    recs = [r for _, _, r in LI.iter_lights(doc)]
    assert len(recs) == 1
    assert LI.affects_diffuse(recs[0]) and LI.is_enabled(recs[0])
    params = LI.blender_params(recs[0])
    assert params["type"] == "SPOT"
    assert params["energy"] > 0.0


# --- real data: floors only ---------------------------------------------------

def test_real_bridge_night_probe_converts_and_selects():
    if not PROBE.is_file():
        raise SkipTest(
            f"{PROBE} is absent — it is a raw `SGLightParams` probe dump of the "
            f"bridge (0703fd2acd5803e9) night light set, written by "
            f"`scratchpad/le_lights_probe.py` as a list of archive dicts, and "
            f"`scratchpad/` is untracked per-machine data. Drop such a dump at "
            f"that path to make this test able to run. ⛔ WHILE THIS SKIP IS "
            f"ACTIVE NOTHING RUNS `lights_sidecar.sidecar_from_probe` ON A "
            f"REAL PROBE DUMP.")
    import json                                          # noqa: PLC0415
    sc = LS.sidecar_from_probe(json.loads(PROBE.read_text(encoding="utf-8")))
    assert sc["summary"]["lights"] >= 50
    assert sc["summary"]["diffuse_enabled"] >= 1
    assert sc["summary"]["diffuse_enabled"] < sc["summary"]["lights"], \
        "most shipped lights are specular-only; a full diffuse set means a decode bug"
    stats = LI.summarize_doc(LI.load_lights(sc), {"light_set": "diffuse"})
    assert stats["kept"] == sc["summary"]["diffuse_enabled"]
    assert stats["kept"] < stats["total"]


def test_emitted_bridge_lights_sidecar_loads():
    if not BRIDGE_LIGHTS.is_file():
        raise SkipTest(
            f"{BRIDGE_LIGHTS} has not been generated — `blender_tool/exports/` "
            f"is gitignored extracted game data. Write it with `python3 "
            f"blender_tool/le_mesh/lights_sidecar.py --probe {PROBE} --out "
            f"{BRIDGE_LIGHTS}` to make this test able to run. ⛔ WHILE THIS "
            f"SKIP IS ACTIVE NOTHING VERIFIES THAT AN EMITTED SIDECAR LOADS "
            f"THROUGH `light_import` AND PUTS ITS LAMPS IN A ROOM-SIZED VOLUME "
            f"RATHER THAN AT THE ORIGIN.")
    doc = LI.load_lights(BRIDGE_LIGHTS)
    stats = LI.summarize_doc(doc, {"light_set": "diffuse"})
    assert stats["total"] >= 50
    assert stats["kept"] >= 1
    # the lamps must land inside a room-sized volume, not at the origin
    recs = [r for _, _, r in LI.iter_lights(doc)]
    xs = [LI.light_matrix_rows(r)[0][3] for r in recs]
    ys = [LI.light_matrix_rows(r)[1][3] for r in recs]
    assert max(xs) - min(xs) >= 1.0
    assert max(ys) - min(ys) >= 1.0
