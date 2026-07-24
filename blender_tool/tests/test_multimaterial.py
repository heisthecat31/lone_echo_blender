"""Archive-free tests for v2 multi-material face slotting (no bpy).

Exercises the two pure, bpy-free reader pieces the Blender operator delegates to
for per-draw material assignment:
  * `scatter_reader.assign_face_materials` — slot each KEPT triangle to a draw.
  * `ScatterPackage.draws` — v2 (native) / v1 (back-compat) draw normalization.

Imports `scatter_reader` standalone (WITHOUT the addon package __init__, which
imports bpy), exactly like test_scatter_import.py. Runs under
`python3 blender_tool/tests/run_tests.py` and unchanged under pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The pure reader lives inside the addon package; import it standalone.
_ADDON = Path(__file__).resolve().parents[1] / "addon" / "lone_echo_import"
if str(_ADDON) not in sys.path:
    sys.path.insert(0, str(_ADDON))

import scatter_reader  # noqa: E402
from scatter_reader import ScatterPackage, assign_face_materials  # noqa: E402
import make_synthetic_scatter  # noqa: E402  (tests dir on sys.path via run_tests)


def _draw(matidx, shdidx, start, count):
    return {"matidx": matidx, "shdidx": shdidx,
            "idx_start": start, "idx_count": count}


def _ref_filter(indices, n_verts):
    """The EXACT kept-triangle filter build_scatter_mesh uses, replicated here so
    the test proves assign_face_materials keeps the same faces in the same order."""
    faces = []
    for i in range(0, len(indices) - 2, 3):
        a, b, c = int(indices[i]), int(indices[i + 1]), int(indices[i + 2])
        if a == b or b == c or a == c:
            continue
        if a >= n_verts or b >= n_verts or c >= n_verts:
            continue
        faces.append((a, b, c))
    return faces


# --- assign_face_materials ---------------------------------------------------

def test_single_draw_all_slot_zero():
    # 2 tris, one draw covering the whole buffer -> one slot, every face slot 0.
    indices = [0, 1, 2, 0, 2, 3]
    draws = [_draw(1, 1, 0, 6)]
    faces, face_slot, slot_keys = assign_face_materials(indices, draws, 4)
    assert faces == [(0, 1, 2), (0, 2, 3)]
    assert face_slot == [0, 0]
    assert slot_keys == [(1, 1)]


def test_two_draws_split_at_boundary():
    # 4 tris; draw0 owns index range [0,6) (tris 0,1), draw1 owns [6,12) (tris 2,3).
    indices = [0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7]
    draws = [_draw(5, 9, 0, 6), _draw(7, 3, 6, 6)]
    faces, face_slot, slot_keys = assign_face_materials(indices, draws, 8)
    assert faces == [(0, 1, 2), (0, 2, 3), (4, 5, 6), (4, 6, 7)]
    assert face_slot == [0, 0, 1, 1]
    assert slot_keys == [(5, 9), (7, 3)]


def test_degenerate_and_oob_filtered_identically():
    # i=0 degenerate (a==b) -> drop; i=3 keep; i=6 OOB (5>=4) -> drop; i=9 keep.
    indices = [0, 0, 1,   0, 1, 2,   5, 1, 2,   1, 2, 3]
    draws = [_draw(2, 2, 0, 12)]
    faces, face_slot, slot_keys = assign_face_materials(indices, draws, 4)
    # matches the reference build_scatter_mesh filter exactly (faces + order)
    assert faces == _ref_filter(indices, 4) == [(0, 1, 2), (1, 2, 3)]
    assert face_slot == [0, 0]
    assert slot_keys == [(2, 2)]


def test_triangle_uncovered_by_any_draw_gets_slot_zero():
    # draw0 covers a far range (nothing here) -> tri at i=0 is uncovered -> slot 0;
    # draw1 covers [3,6) -> tri at i=3 -> slot 1. Proves both branches.
    indices = [0, 1, 2, 3, 4, 5]
    draws = [_draw(5, 9, 100, 3), _draw(7, 3, 3, 3)]
    faces, face_slot, slot_keys = assign_face_materials(indices, draws, 6)
    assert faces == [(0, 1, 2), (3, 4, 5)]
    assert slot_keys == [(5, 9), (7, 3)]
    assert face_slot == [0, 1]          # uncovered -> slot 0; covered -> slot 1


def test_duplicate_pair_draws_share_one_slot():
    # two draws with the SAME (matidx, shdidx) de-dup to a single slot.
    indices = [0, 1, 2, 3, 4, 5]
    draws = [_draw(5, 9, 0, 3), _draw(5, 9, 3, 3)]
    faces, face_slot, slot_keys = assign_face_materials(indices, draws, 6)
    assert slot_keys == [(5, 9)]        # de-duplicated
    assert face_slot == [0, 0]


def test_empty_index_buffer():
    faces, face_slot, slot_keys = assign_face_materials([], [_draw(0, 0, 0, 0)], 0)
    assert faces == [] and face_slot == [] and slot_keys == [(0, 0)]


# --- ScatterPackage.draws normalization --------------------------------------

def test_draws_v2_returns_stored_list():
    stored = [_draw(5, 9, 0, 6), _draw(7, 3, 6, 6)]
    mesh = {"matidx": 5, "shdidx": 9, "nindices": 12, "draws": stored}
    assert ScatterPackage.draws(mesh) == stored


def test_draws_v1_synthesizes_single_whole_buffer_draw():
    # no "draws" key -> one synthetic draw over [0, nindices) = the top-level pair.
    mesh = {"matidx": 2, "shdidx": 3, "nindices": 36}
    got = ScatterPackage.draws(mesh)
    assert got == [{"matidx": 2, "shdidx": 3, "idx_start": 0, "idx_count": 36}]


def test_draws_v1_on_real_synthetic_package(tmp_path):
    # the synthetic writer stays v1 (no per-mesh "draws"); the reader must still
    # normalize each mesh to one whole-buffer draw matching its top-level pair.
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    pkg = ScatterPackage(pkg_dir)
    for m in pkg.meshes:
        dr = pkg.draws(m)
        assert dr == [{"matidx": m["matidx"], "shdidx": m["shdidx"],
                       "idx_start": 0, "idx_count": m["nindices"]}]


def test_draws_feeds_assign_face_materials_end_to_end(tmp_path):
    # v1 package: draws() -> single draw -> assign_face_materials -> all faces slot 0,
    # and the kept faces equal the reference filter over the mesh's own indices.
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    pkg = ScatterPackage(pkg_dir)
    cube = pkg.meshes[0]
    idx = pkg.indices(cube)
    n_verts = cube["nverts"]
    faces, face_slot, slot_keys = assign_face_materials(idx, pkg.draws(cube), n_verts)
    assert faces == _ref_filter(idx, n_verts)
    assert set(face_slot) == {0}                 # single slot
    assert slot_keys == [(cube["matidx"], cube["shdidx"])]
