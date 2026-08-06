"""Tests for `le_mesh.scene_build` — the M4 scene.json generator for a real level.

These assert PROPERTIES OF THE CODE (dedupe, parent-table coverage, drop
accounting, the reason for every unresolved placement), never a census of what
happens to be on disk today. The real-data tests state `>=` floors and raise
`unittest.SkipTest` with a reason when their fixture is absent, so re-extracting
the corpus can never turn a passing suite red — and a missing fixture is
reported, never counted as a pass.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import SkipTest

_ROOT = Path(__file__).resolve().parents[1]          # .../blender_tool
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from le_mesh import scene_build as SB                # noqa: E402

_REPO = _ROOT.parent
MANIFEST_DIR = _REPO / "generic_rebuilds"
BRIDGE = "0703fd2acd5803e9"
BRIDGE_EXPORTS = _ROOT / "exports" / "bridge"
BRIDGE_SCENE = BRIDGE_EXPORTS / "scene.json"


# --- synthetic rows -----------------------------------------------------------

def _placed(actor, model, pt=SB.E_NONE, parent="", pos=(0.0, 0.0, 0.0),
            meshlist="1", meshes="2", offset_type=None, offset=(0.0, 0.0, 0.0)):
    x, y, z = pos
    r = {
        "actor_node_hash": actor, "model_asset_hash": model,
        "pos_x": str(x), "pos_y": str(y), "pos_z": str(z),
        "rot_x": "0", "rot_y": "0", "rot_z": "0", "rot_w": "1",
        "scale": "1", "parent_type": str(pt), "parent_actor_hash": parent,
        "start_visible": "1", "meshlist_present": meshlist, "mesh_count": meshes,
        "transform_resource_hash": "", "transform_row_index": "0",
    }
    if offset_type is not None:
        # The transform manifest's own columns; absent on the placed-model TSV,
        # which is exactly why an unknown `offset_type` must stay unresolvable.
        r["offset_type"] = str(offset_type)
        r["offset_x"], r["offset_y"], r["offset_z"] = (str(v) for v in offset)
        r["offset_rot_x"] = r["offset_rot_y"] = r["offset_rot_z"] = "0"
        r["offset_rot_w"] = "1"
    return r


def _xform(actor, pt=SB.E_NONE, parent="", pos=(0.0, 0.0, 0.0),
           offset_type=None, offset=(0.0, 0.0, 0.0)):
    r = _placed(actor, "", pt, parent, pos, offset_type=offset_type, offset=offset)
    r.pop("model_asset_hash")
    return r


def _t(entry):
    w = entry["world_xf"]
    return (w[3], w[7], w[11])


# --- dedupe + drop accounting -------------------------------------------------

def test_join_rows_dedupe_to_actor_model_pairs():
    """the reference join cross-products two containers; the dedupe is counted."""
    rows = [_placed("A", "M"), _placed("A", "M"), _placed("A", "M"),
            _placed("B", "M")]
    sc = SB.build_scene(rows, "arc")
    assert sc["stats"]["rows"]["total"] == 4
    assert sc["stats"]["rows"]["duplicate_pairs"] == 2
    assert sc["stats"]["placement_count"] == 2
    assert len(sc["placements"]["M"]) == 2


def test_rows_without_a_model_or_actor_are_counted_not_silent():
    rows = [_placed("A", "M"), _placed("B", ""), _placed("", "M")]
    sc = SB.build_scene(rows, "arc")
    assert sc["stats"]["rows"]["dropped_no_model_asset"] == 1
    assert sc["stats"]["rows"]["dropped_no_actor_node"] == 1
    assert sc["stats"]["placement_count"] == 1
    # every dropped row is accounted for exactly once
    r = sc["stats"]["rows"]
    assert (r["distinct_placements"] + r["duplicate_pairs"]
            + r["dropped_no_model_asset"] + r["dropped_no_actor_node"]
            == r["total"])


# --- THE fix: the parent table comes from the full transform container ---------

def test_transform_only_parent_resolves_an_etransform_child():
    """A parent carrying no model of its own must still be a usable chain link.

    This is the defect the level front-end exists to fix: with a placed-rows-only
    table the child reports `parent ... not in archive manifest`, which is a false
    negative, not a real gap.
    """
    child = _placed("C", "M", SB.E_TRANSFORM, parent="P", pos=(1.0, 0.0, 0.0))
    parent_row = _xform("P", SB.E_NONE, pos=(10.0, 0.0, 0.0))

    without = SB.build_scene([child], "arc", etransform=SB.ETRANSFORM_COMPOSE)
    assert without["placements"]["M"][0]["resolved"] is False

    with_table = SB.build_scene([child], "arc", transform_rows=[parent_row],
                                etransform=SB.ETRANSFORM_COMPOSE)
    entry = with_table["placements"]["M"][0]
    assert entry["resolved"] is True
    assert "reason" not in entry                  # resolved => nothing to explain
    assert _t(entry) == (11.0, 0.0, 0.0)          # parentWorld . M_init


def test_placed_rows_still_build_a_table_without_the_transform_manifest():
    rows = [_placed("A", "M", pos=(2.0, 3.0, 4.0))]
    sc = SB.build_scene(rows, "arc", transform_rows=None)
    assert sc["stats"]["rows"]["transform_table_actors"] >= 1
    assert _t(sc["placements"]["M"][0]) == (2.0, 3.0, 4.0)


def test_transform_table_covers_every_placed_actor():
    rows = [_placed("A", "M"), _placed("B", "M")]
    table = SB.build_transform_table([_xform("A")], rows)
    assert set(table) >= {"A", "B"}


# --- unresolved placements are named, never faked -----------------------------

def test_eauto_is_unresolved_with_a_reason_and_still_carries_its_local():
    """`--eauto=unresolved` keeps the pre-disassembly verdict available."""
    rows = [_placed("A", "M", SB.E_AUTO, pos=(1.0, 2.0, 3.0))]
    sc = SB.build_scene(rows, "arc", eauto=SB.EAUTO_UNRESOLVED)
    e = sc["placements"]["M"][0]
    assert e["resolved"] is False
    assert e["parent_type_name"] == "eAuto"
    assert e["reason"]
    assert _t(e) == (1.0, 2.0, 3.0)               # its own matrix, not identity
    assert sc["stats"]["unresolved_reasons"][e["reason"]] == 1


def test_every_unresolved_placement_has_a_reason():
    rows = [_placed("A", "M", SB.E_AUTO), _placed("B", "M", SB.E_JOINT),
            _placed("C", "M", SB.E_TRANSFORM, parent="nope"), _placed("D", "M")]
    sc = SB.build_scene(rows, "arc", etransform=SB.ETRANSFORM_COMPOSE,
                        eauto=SB.EAUTO_UNRESOLVED)
    unresolved = [p for p in sc["placements"]["M"] if not p["resolved"]]
    assert len(unresolved) >= 3
    assert all(p.get("reason") for p in unresolved)
    assert sum(sc["stats"]["unresolved_reasons"].values()) == len(unresolved)


def test_resolved_and_unresolved_partition_the_placements():
    rows = [_placed("A", "M"), _placed("B", "M", SB.E_AUTO)]
    st = SB.build_scene(rows, "arc")["stats"]
    assert st["resolved"] + st["unresolved"] == st["placement_count"]
    assert sum(st["by_parent_type"].values()) == st["placement_count"]


# --- eTransform: relative or already world? -----------------------------------

def test_evidence_calls_small_offsets_parent_relative():
    """A child 6 m from its parent cannot be "attached" to it -- that is a
    genuine parent-relative offset, so composing is right."""
    rows = [_xform("P", SB.E_NONE, pos=(10.0, 0.0, 0.0)),
            _xform("C", SB.E_TRANSFORM, parent="P", pos=(0.0, 0.0, 6.0))]
    ev = SB.etransform_evidence(rows)
    assert ev["children"] == 1
    assert ev["near_parent"] == 0
    assert ev["suggests"] == SB.ETRANSFORM_COMPOSE


def test_evidence_calls_parent_copies_already_world():
    rows = [_xform("P", SB.E_NONE, pos=(10.0, 1.0, -6.0)),
            _xform("C", SB.E_TRANSFORM, parent="P", pos=(10.0, 1.0, -6.0)),
            _xform("D", SB.E_TRANSFORM, parent="P", pos=(10.02, 1.1, -6.01))]
    ev = SB.etransform_evidence(rows)
    assert ev["children"] == 2 and ev["near_parent"] == 2
    assert ev["identical"] == 1
    assert ev["max_distance"] < 0.25
    assert ev["suggests"] == SB.ETRANSFORM_WORLD


def test_evidence_counts_a_missing_parent_separately():
    rows = [_xform("C", SB.E_TRANSFORM, parent="gone", pos=(1.0, 0.0, 0.0))]
    ev = SB.etransform_evidence(rows)
    assert ev["children"] == 1 and ev["parent_missing"] == 1


def test_world_mode_uses_the_childs_own_matrix():
    child = _placed("C", "M", SB.E_TRANSFORM, parent="P", pos=(1.0, 2.0, 3.0))
    parent = _xform("P", SB.E_NONE, pos=(1.0, 2.0, 3.0))
    composed = SB.build_scene([child], "arc", [parent],
                              etransform=SB.ETRANSFORM_COMPOSE)
    world = SB.build_scene([child], "arc", [parent],
                           etransform=SB.ETRANSFORM_WORLD)
    assert _t(composed["placements"]["M"][0]) == (2.0, 4.0, 6.0)   # doubled
    assert _t(world["placements"]["M"][0]) == (1.0, 2.0, 3.0)


def test_world_mode_still_reports_the_parent_type_on_disk():
    child = _placed("C", "M", SB.E_TRANSFORM, parent="P")
    sc = SB.build_scene([child], "arc", [_xform("P")],
                        etransform=SB.ETRANSFORM_WORLD)
    e = sc["placements"]["M"][0]
    assert e["parent_type"] == SB.E_TRANSFORM
    assert e["parent_type_name"] == "eTransform"
    assert e["resolved"] is True
    assert sc["etransform_mode"] == SB.ETRANSFORM_WORLD


def test_default_mode_is_the_runtime_rule_read_out_of_the_executable():
    """⛔ `le_scene`'s `parentWorld . M_init . M_offset` is REFUTED by the code
    (`CTransformCS::AttachToTransform` 0x186240 OVERWRITES the local transform);
    the default must therefore be the disassembly-derived rule, not that one."""
    sc = SB.build_scene([_placed("A", "M")], "arc")
    assert sc["etransform_mode"] == SB.ETRANSFORM_RUNTIME
    assert sc["eauto_mode"] == SB.EAUTO_RUNTIME
    assert SB.ETRANSFORM_COMPOSE in SB.ETRANSFORM_MODES   # still reproducible


# --- the runtime rule, per EAttachOffsetType (CTransformCS::AttachToTransform) --

def test_runtime_etransform_auto_preserves_the_childs_world():
    """`eAttachOffsetAuto` -> local := parent^-1 . child, so world == initialxf."""
    child = _placed("C", "M", SB.E_TRANSFORM, parent="P", pos=(1.0, 2.0, 3.0),
                    offset_type=SB.OFFSET_AUTO)
    parent = _xform("P", SB.E_NONE, pos=(10.0, 0.0, 0.0))
    sc = SB.build_scene([child], "arc", [parent])
    e = sc["placements"]["M"][0]
    assert e["resolved"] is True
    assert e["offset_type_name"] == "eAttachOffsetAuto"
    assert _t(e) == (1.0, 2.0, 3.0)


def test_runtime_etransform_snap_takes_the_parents_world_not_its_own():
    """`eAttachOffsetSnap` -> local := IDENTITY, so `initialxf` is DISCARDED."""
    child = _placed("C", "M", SB.E_TRANSFORM, parent="P", pos=(1.0, 2.0, 3.0),
                    offset_type=SB.OFFSET_SNAP)
    parent = _xform("P", SB.E_NONE, pos=(10.0, 20.0, 30.0))
    sc = SB.build_scene([child], "arc", [parent])
    e = sc["placements"]["M"][0]
    assert e["resolved"] is True
    assert e["offset_type_name"] == "eAttachOffsetSnap"
    assert _t(e) == (10.0, 20.0, 30.0)            # the PARENT's world, not (1,2,3)


def test_runtime_etransform_fixed_is_the_parent_plus_the_offset_only():
    """`eAttachOffsetFixed` -> local := {identity rot, transformoffset.offset}."""
    child = _placed("C", "M", SB.E_TRANSFORM, parent="P", pos=(1.0, 2.0, 3.0),
                    offset_type=SB.OFFSET_FIXED, offset=(0.5, 0.0, 0.0))
    child["transform_resource_hash"] = "cc"
    parent = _xform("P", SB.E_NONE, pos=(10.0, 0.0, 0.0))
    offsets = {("cc", "0"): ((0.0, 0.0, 0.0, 1.0), (0.5, 0.0, 0.0))}
    sc = SB.build_scene([child], "arc", [parent], offsets=offsets)
    e = sc["placements"]["M"][0]
    assert e["resolved"] is True
    assert e["offset_type_name"] == "eAttachOffsetFixed"
    assert _t(e) == (10.5, 0.0, 0.0)              # parentWorld . T(offset)


def test_runtime_etransform_without_an_offset_type_stays_unresolved():
    """A lookup MISS must be distinguishable from a legitimate 0 (Snap)."""
    child = _placed("C", "M", SB.E_TRANSFORM, parent="P", pos=(1.0, 2.0, 3.0))
    parent = _xform("P", SB.E_NONE, pos=(10.0, 0.0, 0.0))
    sc = SB.build_scene([child], "arc", [parent])
    e = sc["placements"]["M"][0]
    assert e["resolved"] is False
    assert "offset_type" in e["reason"]
    assert e["offset_type"] is None
    assert e["offset_type_name"] is None
    assert _t(e) == (1.0, 2.0, 3.0)               # its own matrix, never a guess


def test_runtime_eauto_resolves_to_the_rows_own_matrix_and_says_why():
    """`FinishInitTransformCI` 0x187709 hardcodes `EAttachOffsetType` to Auto, so
    whichever node-graph parent is chosen the world transform is preserved."""
    sc = SB.build_scene([_placed("A", "M", SB.E_AUTO, pos=(1.0, 2.0, 3.0))], "arc")
    e = sc["placements"]["M"][0]
    assert e["resolved"] is True
    assert e["parent_type_name"] == "eAuto"       # the DISK type is still reported
    assert _t(e) == (1.0, 2.0, 3.0)
    assert "0x187709" in e["resolved_by"]


def test_runtime_ejoint_and_erefpoint_stay_unresolved_and_name_the_function():
    rows = [_placed("J", "M", SB.E_JOINT), _placed("R", "M", SB.E_REFPOINT)]
    sc = SB.build_scene(rows, "arc")
    for e in sc["placements"]["M"]:
        assert e["resolved"] is False
        assert "InitializeModelAttach" in e["reason"]


def test_offset_type_lookup_omits_a_miss_instead_of_defaulting_it():
    """A row with no usable `offset_type` must be ABSENT from the map, so a
    later `.get()` returns None -- never a silently defaulted 0 (= Snap)."""
    rows = [{"resource_hash": "AA", "row_index": "0", "offset_type": "2"},
            {"resource_hash": "AA", "row_index": "1", "offset_type": ""},
            {"resource_hash": "AA", "row_index": "2", "offset_type": "junk"},
            {"resource_hash": "AA", "row_index": "3"}]
    m = SB.offset_types_from_transform_rows(rows)
    assert m == {("aa", "0"): SB.OFFSET_FIXED}
    for i in ("1", "2", "3"):
        assert m.get(("aa", i)) is None
    assert SB.OFFSET_TYPE_NAME[SB.OFFSET_SNAP] == "eAttachOffsetSnap"


def test_runtime_mode_is_cycle_guarded():
    a = _placed("A", "M", SB.E_TRANSFORM, parent="B", offset_type=SB.OFFSET_SNAP)
    b = _xform("B", SB.E_TRANSFORM, parent="A", offset_type=SB.OFFSET_SNAP)
    sc = SB.build_scene([a], "arc", [b])
    e = sc["placements"]["M"][0]
    assert e["resolved"] is False
    assert e["reason"]


def test_an_unknown_etransform_mode_is_rejected():
    try:
        SB.build_scene([_placed("A", "M")], "arc", etransform="magic")
    except ValueError as exc:
        assert "etransform" in str(exc)
    else:
        raise AssertionError("expected ValueError on an unknown mode")


def test_report_warns_when_the_mode_contradicts_the_evidence():
    child = _placed("C", "M", SB.E_TRANSFORM, parent="P", pos=(1.0, 2.0, 3.0))
    parent = _xform("P", SB.E_NONE, pos=(1.0, 2.0, 3.0))
    ev = SB.etransform_evidence([parent, child])
    composed = SB.build_scene([child], "arc", [parent],
                              etransform=SB.ETRANSFORM_COMPOSE)
    assert "WARNING" in SB.format_report(composed, ev)
    world = SB.build_scene([child], "arc", [parent],
                           etransform=SB.ETRANSFORM_WORLD)
    assert "WARNING" not in SB.format_report(world, ev)


# --- geometry / bounds / coverage ---------------------------------------------

def test_geometry_stats_track_meshlist_presence():
    rows = [_placed("A", "M", meshlist="1"), _placed("B", "N", meshlist="0")]
    g = SB.build_scene(rows, "arc")["stats"]["geometry"]
    assert g["placements_with_meshlist"] == 1
    assert g["meshlist_keys_with_geometry"] == 1


def test_translation_bounds_ignores_geometry_less_placements():
    rows = [_placed("A", "M", pos=(0.0, 0.0, 0.0), meshlist="1"),
            _placed("B", "M", pos=(2.0, 0.0, 0.0), meshlist="1"),
            _placed("C", "N", pos=(99.0, 0.0, 0.0), meshlist="0")]
    sc = SB.build_scene(rows, "arc")
    lo, hi = SB.translation_bounds(sc, geometry_only=True)
    assert hi[0] == 2.0
    lo_all, hi_all = SB.translation_bounds(sc, geometry_only=False)
    assert hi_all[0] == 99.0


def test_coverage_names_both_kinds_of_gap(tmp_path):
    rows = [_placed("A", "have"), _placed("B", "orphan_placement")]
    sc = SB.build_scene(rows, "arc")
    (tmp_path / "arc_have.lemesh").mkdir()
    (tmp_path / "arc_orphan_package.lemesh").mkdir()
    cov = SB.coverage_against_packages(sc, tmp_path)
    assert cov["packages_placed"] == 1
    assert cov["packages_without_placement"] == ["orphan_package"]
    assert cov["placements_without_package"] == ["orphan_placement"]


def test_format_report_names_the_drops():
    # eJoint is the one type the CODE says stays unresolved (InitializeModelAttach
    # re-attaches it to a bone), so it is what an UNRESOLVED line must report now.
    rows = [_placed("A", "M"), _placed("A", "M"), _placed("B", "M", SB.E_AUTO),
            _placed("J", "M", SB.E_JOINT)]
    text = SB.format_report(SB.build_scene(rows, "arc"))
    assert "duplicate" in text and "eAuto" in text and "UNRESOLVED" in text


def test_scene_json_shape_matches_the_addon_reader():
    """`scene_reader.world_xf_rows` reads translation from indices 3/7/11."""
    sys.path.insert(0, str(_ROOT / "addon" / "lone_echo_import"))
    import scene_reader                                   # noqa: PLC0415
    sc = SB.build_scene([_placed("A", "M", pos=(1.0, 2.0, 3.0))], "arc")
    assert sc["format"] == scene_reader.SCENE_FORMAT
    rows = scene_reader.world_xf_rows(sc["placements"]["M"][0]["world_xf"])
    assert (rows[0][3], rows[1][3], rows[2][3]) == (1.0, 2.0, 3.0)
    assert scene_reader.placements_for(sc, "M")
    assert scene_reader.placements_for(sc, "nope") == []


# --- real data: floors only, and a clean SKIP when the fixture is gone ---------

def test_bridge_manifests_build_a_mostly_resolved_scene():
    placed_p, transform_p = SB.manifest_paths(BRIDGE, MANIFEST_DIR)
    if not placed_p.exists() or not transform_p.exists():
        raise SkipTest(
            f"the bridge placement manifests are absent — this needs BOTH "
            f"{placed_p.name} and {transform_p.name} under {MANIFEST_DIR}, "
            f"which is untracked pre-baked join data, not part of a clean "
            f"checkout. Produce them from your own archive dump (they are the "
            f"`CTransformCR` join `scripts/le_scene.py --manifest` consumes) "
            f"to make this test able to run. ⛔ WHILE THIS SKIP IS ACTIVE "
            f"NOTHING BUILDS A SCENE FROM REAL MANIFESTS, SO THE PARENT-TABLE "
            f"SUPERSET FIX AND THE RESOLVED/DROPPED FLOORS ARE UNVERIFIED.")
    placed = SB.read_tsv(placed_p)
    xforms = SB.read_tsv(transform_p)
    sc = SB.build_scene(placed, BRIDGE, xforms,
                        SB.offsets_from_transform_rows(xforms))
    st = sc["stats"]
    assert st["rows"]["total"] >= 1000
    assert st["placement_count"] >= 250
    assert st["resolved"] >= 150
    assert st["geometry"]["placements_with_meshlist"] >= 100
    # the parent table must be a superset of the placed actors -- that IS the fix
    assert st["rows"]["transform_table_actors"] >= 600
    # and no eTransform placement may fail for "parent not in archive manifest"
    assert not any("not in archive manifest" in r
                   for r in st["unresolved_reasons"])
    lo, hi = SB.translation_bounds(sc)
    ext = [hi[i] - lo[i] for i in range(3)]
    assert ext[0] >= 8.0 and ext[2] >= 25.0        # a room, not a pile
    assert all(e < 1000.0 for e in ext)            # ... and not a unit blow-up


def test_emitted_bridge_scene_json_is_well_formed():
    if not BRIDGE_SCENE.is_file():
        raise SkipTest(
            f"{BRIDGE_SCENE} has not been generated — `blender_tool/exports/` "
            f"is gitignored extracted game data, so a clean checkout has none. "
            f"Write it with `python3 blender_tool/le_mesh/scene_build.py "
            f"--archive {BRIDGE} --out {BRIDGE_SCENE}` to make this test able "
            f"to run. ⛔ WHILE THIS SKIP IS ACTIVE NOTHING CHECKS THE SHAPE OF "
            f"AN EMITTED scene.json — 16-ELEMENT world_xf, A REASON ON EVERY "
            f"UNRESOLVED PLACEMENT — AS THE ADD-ON WILL READ IT.")
    sc = json.loads(BRIDGE_SCENE.read_text(encoding="utf-8"))
    assert sc["format"] == SB.SCENE_FORMAT
    assert sc["coordinate_system"] == "rad_engine"
    assert len(sc["placements"]) >= 40
    for plist in sc["placements"].values():
        for p in plist:
            assert len(p["world_xf"]) == 16
            assert p["world_xf"][15] == 1.0
            assert isinstance(p["resolved"], bool)
            if not p["resolved"]:
                assert p.get("reason")


def test_bridge_etransform_children_are_stored_in_world_space():
    """The measured fact behind the shipped scene.json's `world` mode."""
    _, transform_p = SB.manifest_paths(BRIDGE, MANIFEST_DIR)
    if not transform_p.exists():
        raise SkipTest(
            f"{transform_p} is absent — {MANIFEST_DIR} holds untracked "
            f"pre-baked `CTransformCR` join TSVs, not part of a clean "
            f"checkout. Produce {transform_p.name} from your own archive dump "
            f"to make this test able to run. ⛔ WHILE THIS SKIP IS ACTIVE THE "
            f"MEASURED FACT BEHIND THE SHIPPED scene.json's `world` eTransform "
            f"MODE IS NOT RE-MEASURED ON REAL ROWS.")
    ev = SB.etransform_evidence(SB.read_tsv(transform_p))
    assert ev["children"] >= 10
    assert ev["parent_missing"] == 0
    assert ev["near_parent"] == ev["children"]
    assert ev["max_distance"] <= 0.5
    assert ev["suggests"] == SB.ETRANSFORM_WORLD


def test_shipped_bridge_scene_puts_the_hologram_over_the_senna_console():
    """The visual oracle as an assertion.

    `37670868d7884949` is the holotable hologram and `19557c94c6d17883` the
    console beneath it; reference `bridge-004` shows the hologram floating
    directly above the table. Composing the eTransform chain instead puts it
    6.4 m down-room at ceiling height, which matches no reference — so this is
    the regression guard for the placement bug, not a magic-number check.
    """
    if not BRIDGE_SCENE.is_file():
        raise SkipTest(
            f"{BRIDGE_SCENE} has not been generated — `blender_tool/exports/` "
            f"is gitignored extracted game data, so a clean checkout has none. "
            f"Write it with `python3 blender_tool/le_mesh/scene_build.py "
            f"--archive {BRIDGE} --out {BRIDGE_SCENE}` to make this test able "
            f"to run. ⛔ WHILE THIS SKIP IS ACTIVE THE REGRESSION GUARD FOR "
            f"THE HOLOGRAM PLACEMENT BUG IS NOT RUNNING.")
    sc = json.loads(BRIDGE_SCENE.read_text(encoding="utf-8"))
    holo = sc["placements"].get("37670868d7884949")
    console = sc["placements"].get("19557c94c6d17883")
    if not holo or not console:
        raise SkipTest(
            f"{BRIDGE_SCENE} carries no placement for the holotable hologram "
            f"(37670868d7884949) and/or the console beneath it "
            f"(19557c94c6d17883) — it was built from a different archive or an "
            f"older manifest. Regenerate it with `python3 "
            f"blender_tool/le_mesh/scene_build.py --archive {BRIDGE} --out "
            f"{BRIDGE_SCENE}` to make this test able to run. ⛔ WHILE THIS "
            f"SKIP IS ACTIVE THE REGRESSION GUARD FOR THE HOLOGRAM PLACEMENT "
            f"BUG IS NOT RUNNING.")
    h, c = holo[0]["world_xf"], console[0]["world_xf"]
    horizontal = ((h[3] - c[3]) ** 2 + (h[11] - c[11]) ** 2) ** 0.5
    assert horizontal <= 0.5, "the hologram must sit over the console, not beside it"
    assert 0.0 < (h[7] - c[7]) <= 3.0, "and above it, within the room's height"


def test_offsets_agree_with_le_scene_load_offsets():
    """This module must not drift from `scripts/le_scene.py`'s own loader."""
    _, transform_p = SB.manifest_paths(BRIDGE, MANIFEST_DIR)
    if not transform_p.exists():
        raise SkipTest(
            f"{transform_p} is absent — {MANIFEST_DIR} holds untracked "
            f"pre-baked `CTransformCR` join TSVs, not part of a clean "
            f"checkout. Produce {transform_p.name} from your own archive dump "
            f"to make this test able to run. ⛔ WHILE THIS SKIP IS ACTIVE "
            f"NOTHING STOPS `scene_build.offsets_from_transform_rows` FROM "
            f"DRIFTING AWAY FROM `scripts/le_scene.py`'s OWN LOADER.")
    import le_scene                                        # noqa: PLC0415
    mine = SB.offsets_from_transform_rows(SB.read_tsv(transform_p))
    theirs = le_scene.load_offsets(transform_p)
    assert mine == theirs
