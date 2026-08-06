"""Tests for `le_mesh.level_link` — the `CGameLevelResourceWin7` parent edge.

These assert PROPERTIES OF THE CODE on synthetic blobs: the two field offsets,
that the null sentinel is reported as absent, that a short blob RAISES instead of
reporting "no parent", and that `scene_build` only emits a `level_link` block when
one was actually decoded. The real-data checks state `>=`/identity floors and
raise `unittest.SkipTest` with a reason when their fixture is absent, so
re-extracting the corpus can never turn a passing suite red — and a missing
fixture is reported, never counted as a pass.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path
from unittest import SkipTest

_ROOT = Path(__file__).resolve().parents[1]          # .../blender_tool
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from le_mesh import level_link as LL                 # noqa: E402
from le_mesh import scene_build as SB                # noqa: E402

BRIDGE = "0703fd2acd5803e9"
ITC_MASTER = "0703f239d74801fe"
BRIDGE_EXPORTS = _ROOT / "exports" / "bridge"


# --- synthetic blobs ----------------------------------------------------------

def _blob(parent: int, cspace: int, size: int = 328) -> bytes:
    b = bytearray(size)
    struct.pack_into("<Q", b, LL.PARENT_LEVEL_OFF, parent)
    struct.pack_into("<Q", b, LL.COMPONENT_SPACE_OFF, cspace)
    return bytes(b)


# --- the two fields -----------------------------------------------------------

def test_parent_and_componentspace_are_read_from_their_own_offsets():
    link = LL.decode_game_level(_blob(0x0703F239D74801FE, 0x0703FD2ACD5803E9),
                                BRIDGE)
    assert link.parent_level == ITC_MASTER
    assert link.component_space == BRIDGE
    assert link.has_parent is True
    assert link.is_self_parented is False
    assert link.warnings == []


def test_the_offsets_are_distinct_so_the_two_fields_cannot_alias():
    assert LL.PARENT_LEVEL_OFF != LL.COMPONENT_SPACE_OFF
    a = LL.decode_game_level(_blob(0x1111111111111111, 0x2222222222222222))
    assert a.parent_level != a.component_space


# --- a miss must not look like a value ----------------------------------------

def test_null_sentinel_reports_absent_not_a_hash():
    link = LL.decode_game_level(_blob(LL.NULL_SYMBOL, 0x2222222222222222))
    assert link.parent_level is None
    assert link.has_parent is False
    # the OTHER field must be unaffected -- absence is per-slot, not per-blob
    assert link.component_space == "2222222222222222"


def test_a_short_blob_raises_instead_of_reporting_no_parent():
    for n in (0, 8, LL.MIN_BLOB_BYTES - 1):
        try:
            LL.decode_game_level(bytes(n))
        except ValueError:
            continue
        raise AssertionError(f"a {n}-byte blob must raise, not decode")


def test_minimum_blob_size_is_exactly_enough_for_the_far_field():
    ok = LL.decode_game_level(_blob(1, 2, size=LL.MIN_BLOB_BYTES))
    assert ok.component_space == f"{2:016x}"


# --- loud on a broken identity ------------------------------------------------

def test_componentspace_not_self_named_is_warned_not_swallowed():
    link = LL.decode_game_level(_blob(0x3333333333333333, 0x4444444444444444),
                                BRIDGE)
    assert link.parent_level == "3333333333333333"
    assert len(link.warnings) >= 1
    assert any("componentspace" in w for w in link.warnings)


def test_self_parenting_is_flagged():
    link = LL.decode_game_level(
        _blob(int(BRIDGE, 16), int(BRIDGE, 16)), BRIDGE)
    assert link.is_self_parented is True
    assert len(link.warnings) >= 1


def test_to_dict_round_trips_the_format_tag():
    d = LL.decode_game_level(_blob(1, 2), None).to_dict()
    assert d["format"] == LL.LINK_FORMAT
    assert d["version"] >= 1
    assert set(("archive", "parent_level", "component_space",
                "blob_size", "warnings")) <= set(d)


# --- scene_build wiring: absent block != null parent --------------------------

def _rows():
    return [{
        "actor_node_hash": "a1", "model_asset_hash": "m1",
        "pos_x": "0", "pos_y": "0", "pos_z": "0",
        "rot_x": "0", "rot_y": "0", "rot_z": "0", "rot_w": "1",
        "scale": "1", "parent_type": str(SB.E_NONE), "parent_actor_hash": "",
        "start_visible": "1", "meshlist_present": "1", "mesh_count": "1",
        "transform_resource_hash": "", "transform_row_index": "0",
    }]


def test_scene_omits_the_block_entirely_when_no_link_was_decoded():
    scene = SB.build_scene(_rows(), BRIDGE)
    assert "level_link" not in scene, "absent must mean NOT LOOKED UP"


def test_scene_embeds_a_decoded_link_including_an_explicit_null_parent():
    link = LL.decode_game_level(_blob(LL.NULL_SYMBOL, int(BRIDGE, 16)),
                                BRIDGE).to_dict()
    scene = SB.build_scene(_rows(), BRIDGE, link=link)
    assert "level_link" in scene
    # present-with-null is a DIFFERENT fact from absent, and must survive
    assert scene["level_link"]["parent_level"] is None
    assert scene["level_link"]["component_space"] == BRIDGE


def test_load_level_link_rejects_a_foreign_format(tmp_path):
    p = tmp_path / "level_link.json"
    p.write_text(json.dumps({"format": "something-else"}), encoding="utf-8")
    try:
        SB.load_level_link(p)
    except ValueError:
        return
    raise AssertionError("a foreign format must raise, not return {}")


def test_find_level_link_returns_none_when_absent(tmp_path):
    assert SB.find_level_link(tmp_path) is None
    (tmp_path / "level_link.json").write_text("{}", encoding="utf-8")
    assert SB.find_level_link(tmp_path) is not None


# --- real data (SKIP-safe) ----------------------------------------------------

def test_shipped_bridge_level_link_names_a_parent_that_is_not_itself():
    p = BRIDGE_EXPORTS / "level_link.json"
    if not p.is_file():
        raise SkipTest(
            f"{p} is absent — `blender_tool/exports/` is gitignored extracted "
            f"game data, so a clean checkout has none. Write it with `python3 "
            f"blender_tool/le_mesh/level_link.py --archive {BRIDGE} --out {p}` "
            f"(needs a `hash_lookup.json` at the repo root) to make this test "
            f"able to run. ⛔ WHILE THIS SKIP IS ACTIVE NOTHING VERIFIES THAT THE "
            f"SHIPPED BRIDGE LINK NAMES A PARENT LEVEL THAT IS NOT ITSELF.")
    d = SB.load_level_link(p)
    assert d["archive"] == BRIDGE
    assert d["parent_level"], "the shipped bridge link must record a parent"
    assert d["parent_level"] != BRIDGE
    assert d["component_space"] == BRIDGE
    assert d["blob_size"] >= LL.MIN_BLOB_BYTES


def test_shipped_bridge_scene_carries_the_link_and_keeps_its_placements():
    p = BRIDGE_EXPORTS / "scene.json"
    if not p.is_file():
        raise SkipTest(
            f"{p} is absent — `blender_tool/exports/` is gitignored extracted "
            f"game data, so a clean checkout has none. Generate it with "
            f"`python3 blender_tool/le_mesh/scene_build.py --archive {BRIDGE} "
            f"--out {p}` to make this test able to run. ⛔ WHILE THIS SKIP IS "
            f"ACTIVE NOTHING VERIFIES THAT THE LEVEL LINK IS ADDITIVE — THAT "
            f"ADDING IT COST THE SHIPPED SCENE NO PLACEMENTS.")
    scene = json.loads(p.read_text(encoding="utf-8"))
    if "level_link" not in scene:
        raise SkipTest(
            f"the shipped {p} predates the `level_link` block — regenerate it "
            f"with `python3 blender_tool/le_mesh/scene_build.py --archive "
            f"{BRIDGE} --out {p}` to make this test able to run. ⛔ WHILE THIS "
            f"SKIP IS ACTIVE NOTHING VERIFIES THAT THE LEVEL LINK IS ADDITIVE — "
            f"THAT ADDING IT COST THE SHIPPED SCENE NO PLACEMENTS.")
    assert scene["version"] >= 3
    assert scene["level_link"]["parent_level"] != scene["archive"]
    # the link must be ADDITIVE -- it cannot have cost the scene any placements
    assert scene["stats"]["placement_count"] >= 100
    assert scene["stats"]["resolved"] >= 1


# --- eAuto evidence + the opt-in mode -----------------------------------------

def _auto_row(actor, pos, pt=SB.E_AUTO):
    x, y, z = pos
    return {
        "actor_node_hash": actor, "model_asset_hash": "m1",
        "pos_x": str(x), "pos_y": str(y), "pos_z": str(z),
        "rot_x": "0", "rot_y": "0", "rot_z": "0", "rot_w": "1",
        "scale": "1", "parent_type": str(pt), "parent_actor_hash": "",
        "start_visible": "1", "meshlist_present": "1", "mesh_count": "1",
        "transform_resource_hash": "", "transform_row_index": "0",
    }


def test_eauto_evidence_says_world_when_auto_rows_fill_the_none_hull():
    rows = [_auto_row(f"n{i}", (i, 0.0, -i), SB.E_NONE) for i in range(1, 11)]
    rows += [_auto_row(f"a{i}", (i + 0.5, 0.0, -i - 0.2)) for i in range(1, 9)]
    ev = SB.eauto_evidence(rows)
    assert ev["auto"] >= 5 and ev["none"] >= 5
    assert ev["auto_at_origin"] == 0
    assert ev["hull_overlap"] >= 0.95
    assert ev["suggests"] == SB.EAUTO_WORLD


def test_eauto_evidence_stays_unresolved_when_auto_rows_cluster_at_the_origin():
    rows = [_auto_row(f"n{i}", (i * 10.0, 0.0, -i * 10.0), SB.E_NONE)
            for i in range(1, 11)]
    rows += [_auto_row(f"a{i}", (0.0, 0.0, 0.0)) for i in range(1, 9)]
    ev = SB.eauto_evidence(rows)
    assert ev["auto_at_origin"] == ev["auto"]
    assert ev["suggests"] == SB.EAUTO_UNRESOLVED


def test_eauto_evidence_is_a_measurement_not_a_resolution():
    # It must never claim a parent -- there is no parent key in the result.
    ev = SB.eauto_evidence([_auto_row("a1", (1.0, 2.0, 3.0))])
    assert "parent" not in ev and "parent_actor_hash" not in ev


def test_eauto_default_resolves_on_the_code_and_stamps_how():
    """★ The DEFAULT is now the disassembly-derived answer.
    `CTransformCS::FinishInitTransformCI` `0x187709` issues the eAuto attach with
    `EAttachOffsetType` hardcoded to `eAttachOffsetAuto` (`mov r9d, 1` @`0x1879c9`),
    which preserves the world transform — so `world == initialxf` whatever parent
    the node graph yields. `--eauto=unresolved` keeps the old verdict available."""
    scene = SB.build_scene([_auto_row("a1", (1.0, 2.0, 3.0))], BRIDGE)
    p = scene["placements"]["m1"][0]
    assert p["resolved"] is True
    assert p["parent_type_name"] == "eAuto"      # the DISK type still survives
    assert "0x187709" in p["resolved_by"]
    assert scene["eauto_mode"] == SB.EAUTO_RUNTIME
    assert [p["world_xf"][3], p["world_xf"][7], p["world_xf"][11]] == [1.0, 2.0, 3.0]

    old = SB.build_scene([_auto_row("a1", (1.0, 2.0, 3.0))], BRIDGE,
                         eauto=SB.EAUTO_UNRESOLVED)
    q = old["placements"]["m1"][0]
    assert q["resolved"] is False and q.get("reason")
    assert "resolved_by" not in q


def test_eauto_world_is_opt_in_and_always_stamps_how_it_resolved():
    scene = SB.build_scene([_auto_row("a1", (1.0, 2.0, 3.0))], BRIDGE,
                           etransform=SB.ETRANSFORM_WORLD, eauto=SB.EAUTO_WORLD)
    p = scene["placements"]["m1"][0]
    assert p["resolved"] is True
    # the DISK parent type must survive -- the mode must not rewrite history
    assert p["parent_type_name"] == "eAuto"
    assert "eauto=world" in p["resolved_by"]
    assert scene["eauto_mode"] == SB.EAUTO_WORLD
    # and it must be the row's own translation, not a fabricated one
    assert [p["world_xf"][3], p["world_xf"][7], p["world_xf"][11]] == [1.0, 2.0, 3.0]


def test_an_unknown_eauto_mode_raises():
    try:
        SB.build_scene([_auto_row("a1", (1.0, 2.0, 3.0))], BRIDGE, eauto="magic")
    except ValueError:
        return
    raise AssertionError("an unknown eauto mode must raise")
