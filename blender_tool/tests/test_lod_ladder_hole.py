"""D13 — a HOLE between two rungs of an LOD ladder must not delete the model.

★ WHAT THIS PINS. `select_lod_objects` used to clamp `want` to the *interval*
`[min(gated), max(gated)]`. An interval is not a ladder: `2fd6839161785e9c_
ff91757c910ea7b6` (Liv's body) partitions its six meshes into levels **{0, 3}**,
so levels 1 and 2 lie inside the interval, carry no object, and imported
**nothing at all**. Filed as D13 (originally numbered D9); see
`docs/TESTING.md` §3.1.

The rule that replaced it lives in `package_reader.snap_to_ladder` and is stated
there: **snap DOWN to the greatest present rung `<= level`; snap UP to the finest
rung only when the request is below the whole ladder.** That one expression
subsumes D2's floor and the old ceiling.

⚠ THE THREE LAWS, so a future edit cannot quietly pick a different rule:

  1. **never empty** — a package with a ladder always selects something, at every
     level from 0 to its coarsest;
  2. **never coarser than asked** — the selected rung is `<= level` whenever the
     ladder has any rung that low, i.e. the snap goes toward MORE detail. This is
     the law that separates "snap down" from "snap up", and it is the reason the
     over-draw bias of `scene_lod_is_geometric_chain` is preserved;
  3. **monotone** — as `level` rises the selected rung never falls, so a sweep
     still reads as a ladder.

The real-package half skips loudly when `exports/` is absent (it is gitignored),
exactly as `test_real_package_invariants.py` does.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import SkipTest

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for _p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon" / "lone_echo_import")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from package_reader import (                                    # noqa: E402
    scene_lod_is_geometric_chain, select_lod_draws, select_lod_objects,
    snap_to_ladder,
)

#: the package the defect was found on, by basename.
SPARSE_LADDER_PACKAGE = "2fd6839161785e9c_ff91757c910ea7b6.lemesh"


# ---------------------------------------------------------------------------
# 1. the rule itself
# ---------------------------------------------------------------------------

def test_a_dense_zero_based_ladder_is_the_identity():
    for lv in (0, 1, 2):
        assert snap_to_ladder(lv, {0, 1, 2}) == lv


def test_above_the_ladder_snaps_to_its_coarsest_rung():
    """The old ceiling, preserved."""
    assert snap_to_ladder(9, {0, 1, 2}) == 2
    assert snap_to_ladder(4, {3, 4}) == 4


def test_below_the_ladder_snaps_to_its_finest_rung():
    """D2, preserved: `3cee9f282bf0807f` starts at 3, and level 0 must still
    select its level 3 rather than nothing."""
    assert snap_to_ladder(0, {3, 4}) == 3
    assert snap_to_ladder(2, {3, 4}) == 3


def test_a_hole_snaps_DOWN_to_the_nearest_finer_rung():
    """D13. {0, 3} with 1 and 2 asked for."""
    assert snap_to_ladder(1, {0, 3}) == 0
    assert snap_to_ladder(2, {0, 3}) == 0
    assert snap_to_ladder(3, {0, 3}) == 3


def test_the_snap_is_never_COARSER_than_the_level_asked_for():
    """★ The law that distinguishes this rule from "nearest rung by distance".

    On {0, 3} a nearest-by-distance rule answers level 2 with rung **3** — a
    coarser model than the caller asked for, which no threshold ladder produces
    and which would put Liv's LOD-3 proxy on screen for a request meaning "almost
    full detail". Asserted over every subset of 0..5 that has a rung at or below
    the request.
    """
    from itertools import combinations
    for n in range(1, 7):
        for ladder in combinations(range(6), n):
            present = set(ladder)
            for lv in range(0, 8):
                got = snap_to_ladder(lv, present)
                assert got in present
                if any(x <= lv for x in present):
                    assert got <= lv, f"ladder {sorted(present)} level {lv} -> {got}"
                else:
                    assert got == min(present)


def test_the_snap_is_monotone_in_the_level_asked_for():
    from itertools import combinations
    for n in range(1, 7):
        for ladder in combinations(range(6), n):
            present = set(ladder)
            seq = [snap_to_ladder(lv, present) for lv in range(0, 8)]
            assert seq == sorted(seq), f"ladder {sorted(present)} -> {seq}"


def test_a_single_rung_ladder_always_answers_that_rung():
    for lv in range(0, 6):
        assert snap_to_ladder(lv, {2}) == 2


# ---------------------------------------------------------------------------
# 2. through the public selection surface, on a synthetic sparse ladder
# ---------------------------------------------------------------------------

#: no `aabb_min`/`aabb_max` ⇒ `scene_lod_is_geometric_chain` does not evaluate
#: and the selection runs, which is the behaviour a pre-AABB package gets.
_SPARSE_OBJS = [{"name": "fine_a", "scene_lod_level": 0},
                {"name": "fine_b", "scene_lod_level": 0},
                {"name": "coarse", "scene_lod_level": 3},
                {"name": "ungated", "scene_lod_level": None}]


def test_select_lod_objects_never_returns_nothing_in_a_hole():
    """The defect, at the call site. Levels 1 and 2 used to return only the
    level-less object; on a real package where every object carries a level they
    returned the EMPTY LIST."""
    for lv in (1, 2):
        got = [o["name"] for o in select_lod_objects(_SPARSE_OBJS, lv)]
        assert got == ["fine_a", "fine_b", "ungated"], f"level {lv} -> {got}"


def test_select_lod_objects_still_honours_the_rungs_that_exist():
    assert [o["name"] for o in select_lod_objects(_SPARSE_OBJS, 0)] == \
        ["fine_a", "fine_b", "ungated"]
    assert [o["name"] for o in select_lod_objects(_SPARSE_OBJS, 3)] == \
        ["coarse", "ungated"]
    assert [o["name"] for o in select_lod_objects(_SPARSE_OBJS, 9)] == \
        ["coarse", "ungated"]
    # `level < 0` is still "stack everything", the A/B path.
    assert len(select_lod_objects(_SPARSE_OBJS, -1)) == 4


def test_a_hole_in_a_DRAW_ladder_is_closed_the_same_way():
    """`select_lod_draws` clamped with a ceiling and no floor at all, which is
    D2's shape one module over. No mesh-list chain on disk is sparse or
    non-zero-based (container: `blender_tool/exports`, coverage: 301 manifests /
    913 objects, measured 2026-08-05), so this is synthetic by necessity — the
    point is that both clamps now share one rule."""
    entries = [{"lod": {"level": 0}, "name": "fine"},
               {"lod": {"level": 3}, "name": "coarse"}]
    assert [e["name"] for e in select_lod_draws(entries, 0)] == ["fine"]
    assert [e["name"] for e in select_lod_draws(entries, 1)] == ["fine"]
    assert [e["name"] for e in select_lod_draws(entries, 2)] == ["fine"]
    assert [e["name"] for e in select_lod_draws(entries, 3)] == ["coarse"]
    assert [e["name"] for e in select_lod_draws(entries, 9)] == ["coarse"]

    below = [{"lod": {"level": 3}, "name": "only"}]
    assert [e["name"] for e in select_lod_draws(below, 0)] == ["only"], \
        "a chain that starts above 0 must not vanish at the DEFAULT level"


# ---------------------------------------------------------------------------
# 3. on the package the defect was found on
# ---------------------------------------------------------------------------

def _packages() -> list[tuple[Path, dict]]:
    out = []
    for root in (BLENDER_TOOL / "fixtures", BLENDER_TOOL / "exports"):
        if not root.is_dir():
            continue
        for mf in root.glob("**/manifest.json"):
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
            except Exception:                                    # noqa: BLE001
                continue
            if m.get("format") == "lemesh" and m.get("objects"):
                out.append((mf.parent, m))
    return sorted(out, key=lambda t: str(t[0]))


def _gated(objs) -> list[int]:
    """Levels carried by objects a scene set actually gates, re-derived from the
    draw masks rather than borrowed from `object_is_scene_set_ungated`."""
    out = []
    for o in objs:
        lv = o.get("scene_lod_level")
        if lv is None:
            continue
        masks = [d.get("scene_mask") for d in (o.get("draws") or [])]
        known = [m for m in masks if m is not None]
        if known and all(int(m) == 0 for m in known):
            continue                                   # ungated: always drawn
        out.append(int(lv))
    return sorted(set(out))


def test_the_sparse_ladder_package_selects_its_finer_rung_in_the_hole():
    """D13 on the real bytes. Liv's body is levels {0, 3}: levels 1 and 2 must
    now select exactly what level 0 selects — 5 of 6 meshes — and never the
    level-3 proxy alone."""
    hit = [(pd, m) for pd, m in _packages() if pd.name == SPARSE_LADDER_PACKAGE]
    if not hit:
        raise SkipTest(f"{SPARSE_LADDER_PACKAGE} is not extracted in this "
                       "checkout (exports/ is gitignored)")
    pd, m = hit[0]
    objs = m["objects"]
    assert _gated(objs) == [0, 3], \
        f"the sparse ladder moved: {pd.name} is now {_gated(objs)}"
    fine = [o["name"] for o in select_lod_objects(objs, 0)]
    assert len(fine) == 5 and len(objs) == 6
    for lv in (1, 2):
        got = [o["name"] for o in select_lod_objects(objs, lv)]
        assert got == fine, f"level {lv} on {pd.name} -> {got}"
    coarse = [o["name"] for o in select_lod_objects(objs, 3)]
    assert len(coarse) == 1 and coarse != fine
    print(f"    [D13] {pd.name} ladder {{0, 3}}: levels 1-2 select "
          f"{len(fine)}/{len(objs)} (was 0/{len(objs)})")


def test_no_package_on_disk_selects_NOTHING_at_any_level():
    """The general law D13 closes, swept over the corpus. This is the test that
    was written to pass with one known exception; it now passes with none."""
    pkgs = _packages()
    if not pkgs:
        raise SkipTest("no extracted packages under fixtures/ or exports/")
    empty, laddered = [], 0
    for pd, m in pkgs:
        gated = _gated(m["objects"])
        if not gated or max(gated) == 0:
            continue
        laddered += 1
        for lv in range(0, max(gated) + 2):
            if not select_lod_objects(m["objects"], lv):
                empty.append(f"{pd.name} level {lv} gated={gated}")
    assert not empty, ("LOD selection returned NOTHING — D13 is back:\n  "
                       + "\n  ".join(empty))
    print(f"    [D13] {laddered} laddered package(s) of {len(pkgs)}: no level "
          "selects nothing")


def test_every_real_ladder_is_monotone_and_never_coarser_than_asked():
    """Laws 2 and 3 on the corpus, not on synthetic ladders."""
    pkgs = _packages()
    if not pkgs:
        raise SkipTest("no extracted packages under fixtures/ or exports/")
    checked = 0
    for pd, m in pkgs:
        objs = m["objects"]
        gated = _gated(objs)
        if not gated or max(gated) == 0:
            continue
        if not scene_lod_is_geometric_chain(objs)[0]:
            continue            # D1 refusal: everything draws, no rung selected
        checked += 1
        seq = []
        for lv in range(0, max(gated) + 2):
            sel = {o.get("name") for o in select_lod_objects(objs, lv)}
            rungs = {int(o["scene_lod_level"]) for o in objs
                     if o.get("name") in sel and o.get("scene_lod_level") is not None
                     and int(o["scene_lod_level"]) in gated}
            rung = max(rungs) if rungs else None
            if rung is None:
                continue
            seq.append((lv, rung))
            if any(x <= lv for x in gated):
                assert rung <= lv, (f"{pd.name} level {lv} selected the COARSER "
                                    f"rung {rung}; the snap must go finer")
        assert [r for _, r in seq] == sorted(r for _, r in seq), \
            f"{pd.name} rung sequence is not monotone: {seq}"
    if not checked:
        raise SkipTest("no package on disk is an accepted scene-set LOD chain")
    print(f"    [D13] {checked} accepted chain(s) monotone, never coarser than asked")
