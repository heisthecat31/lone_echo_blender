"""A LOD cluster that swallowed a second part must be split before levels land.

`_bbox_close` matches on the bounding box, and on `mpl_lobby_b2` that merged the
combat module's wall panel into the shell's LOD chain: the panel is 3.502 across
against the shell's 3.691, and the 5% tolerance is 0.195 against a 0.189
difference, so it passes by 3%. Merged, the panel's own LOD 0 was demoted to
level 4 of 8 and the importer -- which places level 0 -- dropped all four copies.

The signal that the cluster is wrong is that a LOD chain is STORED in decreasing
detail order, so its face counts never RISE. These pin that rule, the split it
licenses, and the two ways the split declines to act.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from evr_scene_extract import (  # noqa: E402
    _split_interleaved_cluster, _faces_non_increasing, _group_submeshes_by_lod,
    _model_lod_period, _cluster_holds_distinct_parts,
)


def _sigs(n, same=()):
    """Distinct geometry hashes per submesh; indices in `same` share one."""
    out = ["geom%02d" % i for i in range(n)]
    for group in same:
        for i in group:
            out[i] = "shared%d" % group[0]
    return out

SHELL = ((-3.50, -2.56, -3.69), (3.691, 5.126, 3.691))
SHELL_LOD = ((-3.50, -2.56, -3.69), (3.688, 5.126, 3.688))
PANEL = ((-3.44, -2.48, -3.56), (3.502, 4.952, 3.502))

#: `9c9a7e6f6014702c` as it decodes: shell and panel interleaved, LOD-major.
ENTRIES_9C9A = [
    (SHELL, 212), (PANEL, 4), (SHELL_LOD, 152), (PANEL, 4),
    (SHELL_LOD, 112), (PANEL, 4), (SHELL_LOD, 72), (PANEL, 4),
]


def test_face_counts_rise_is_the_violation_signal():
    assert not _faces_non_increasing(list(range(8)), ENTRIES_9C9A)
    assert _faces_non_increasing([0, 2, 4, 6], ENTRIES_9C9A)
    assert _faces_non_increasing([1, 3, 5, 7], ENTRIES_9C9A)


def test_interleaved_cluster_splits_into_shell_and_panel():
    # The panel's four levels are byte-identical copies -- measured on
    # `9c9a7e6f6014702d`, all four hash to `c34f671bead8`. The shell's four
    # levels are genuinely different meshes.
    sigs = _sigs(8, same=[(1, 3, 5, 7)])
    assert _split_interleaved_cluster(list(range(8)), ENTRIES_9C9A, sigs) == [
        [0, 2, 4, 6], [1, 3, 5, 7]]


def test_monotonic_cluster_is_left_alone():
    """The quiet path: 174 of 181 confirmed chains never rise, and none move."""
    assert _split_interleaved_cluster([0, 2, 4, 6], ENTRIES_9C9A, _sigs(8)) == [[0, 2, 4, 6]]
    chain = [(SHELL, 712), (SHELL_LOD, 528), (SHELL_LOD, 230), (SHELL_LOD, 118)]
    assert _split_interleaved_cluster([0, 1, 2, 3], chain, _sigs(4)) == [[0, 1, 2, 3]]


def test_single_rise_cuts_a_concatenated_pair_in_two():
    """Two members that RISE cannot be one chain, so they are two parts.

    `3eff95282bf0807f` is this shape (`[6680, 6872]`): identical bounding box,
    no period to find with only two members, and the second more detailed than
    the first -- which a LOD chain never is.
    """
    same = [(SHELL, 192), (SHELL, 288)]
    assert _split_interleaved_cluster([0, 1], same, _sigs(2)) == [[0], [1]]


def test_periodic_split_separates_near_congruent_shells():
    """The bbox is useless at 0.05% apart; the LOD-major period is not.

    `b4860fbc69ee178f`: six shells whose extents differ far below any usable
    bbox tolerance, splitting at P=2 into [192, 96, 32] and [288, 192, 96].
    """
    def shell(scale, faces):
        return (((-1.0, -1.25, -12.48), (1.969 * scale, 6.920 * scale, 12.273)),
                faces)
    entries = [shell(1.0, 192), shell(0.9995, 288), shell(1.0, 96),
               shell(0.9995, 192), shell(1.0, 32), shell(0.9995, 96)]
    assert _split_interleaved_cluster(list(range(6)), entries, _sigs(6)) == [
        [0, 2, 4], [1, 3, 5]]


def test_period_declines_without_a_rectangular_layout():
    """`ff5afb4e96897159` is already clustered right and must not be touched.

    Its nine submeshes admit a period only with singleton classes, which the
    divides-evenly and at-least-two-members guards reject.
    """
    seq = [1968, 2, 176, 614, 48, 328, 14, 184, 14]
    entries = [(SHELL, f) for f in seq]
    parts = _split_interleaved_cluster(list(range(9)), entries, _sigs(9))
    # one rise? no -- several, so the single-rise cut declines too
    assert parts == [list(range(9))]


def test_both_parts_get_their_own_level_zero():
    """The point of the fix: each part keeps a level 0, so each is placed."""
    results = []
    for (bmin, bsize), nfaces in ENTRIES_9C9A:
        hi = tuple(bmin[k] + bsize[k] for k in range(3))
        results.append((
            [bmin, hi], [(0, 1, 0)] * nfaces, None,
        ))
    out = _group_submeshes_by_lod(results)
    groups = {}
    for i, (cluster, level, size) in enumerate(out):
        groups.setdefault(cluster, []).append((level, ENTRIES_9C9A[i][1]))
    assert len(groups) == 2, out
    for members in groups.values():
        assert 0 in [lvl for lvl, _f in members]
    # the shell chain and the panel chain, each 4 deep -- not one chain of 8
    assert sorted(len(m) for m in groups.values()) == [4, 4]


# ── the level-major period, which is the primary path ───────────────────────

#: `2576bbc41db98406` (mpl_lobby_b2, the model beside i5066): 4 parts x 2
#: levels. sub2 and sub3 share a bounding box exactly, which is what made the
#: bbox clustering read all four as one chain and hide sub3 entirely.
BOX_A = ((58.69, 2.01, 29.91), (11.19, 5.04, 11.19))
BOX_B = ((58.69, -3.03, 29.91), (11.19, 5.04, 11.19))
BOX_C = ((52.86, -3.03, 24.09), (17.01, 10.08, 17.01))
ENTRIES_2576 = [(BOX_A, 42), (BOX_B, 98), (BOX_C, 38), (BOX_C, 38),
                (BOX_A, 26), (BOX_B, 62), (BOX_C, 26), (BOX_C, 26)]


def test_period_found_for_four_parts_two_levels():
    assert _model_lod_period(ENTRIES_2576) == 4


def test_period_gives_every_part_its_own_level_zero():
    """sub2 AND sub3 must both be level 0 -- the whole point."""
    out = _group_submeshes_by_lod(
        [([e[0][0], tuple(e[0][0][k] + e[0][1][k] for k in range(3))],
          [(0, 1, 0)] * e[1], None) for e in ENTRIES_2576])
    level0 = {i for i, (_c, level, _s) in enumerate(out) if level == 0}
    assert level0 == {0, 1, 2, 3}, out
    assert len({c for c, _l, _s in out}) == 4


def test_period_two_for_an_interleaved_shell_and_panel():
    assert _model_lod_period(ENTRIES_9C9A) == 2


def test_period_one_for_a_plain_single_part_chain():
    chain = [(SHELL, 712), (SHELL_LOD, 528), (SHELL_LOD, 230), (SHELL_LOD, 118)]
    assert _model_lod_period(chain) == 1


def test_period_declines_when_faces_gain_across_the_stride():
    """A pair that gains detail one stride on is not a part and its level."""
    assert _model_lod_period([(SHELL, 100), (SHELL, 400)]) is None


def test_distinct_parts_needs_differing_geometry():
    ent = [(SHELL, 38), (SHELL, 38)]
    assert _cluster_holds_distinct_parts([0, 1], ent, ["a", "b"])
    assert not _cluster_holds_distinct_parts([0, 1], ent, ["a", "a"])
