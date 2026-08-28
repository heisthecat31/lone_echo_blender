"""Which array slices a lightmap page owns, and how the page count is found.

Both numbers here were measured against `H:/pcvr-extracted` (the shipped Echo VR
resources) and against the exported slices, so the suite runs with no extract,
no `bpy` and no BC6H decoder.

The layout claim -- that the ambient array is PAGE-MAJOR, `page * lobes + i` --
was not taken from the shader comment that asserts it. It was measured: decode
every slice, mask the texels a chart actually covers, and score every pair by
Jaccard. Charts belong to a PAGE, so slices of one page share a mask and slices
of different pages do not.

    mpl_arena_a         SH4, 5 pages x 4 lobes    within 0.963   across 0.433
    mpl_tutorial_lobby  SG5, 8 pages x 5 lobes    within 0.964   across 0.486
                                                  min within 0.903 > max across 0.665

and forcing `mpl_tutorial_lobby` into groups of FOUR drops the within-group
score to 0.813, so the lobe count is measured too, not assumed.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import evr_lightmap as LM                                        # noqa: E402
import evr_apply_lighting as AL                                  # noqa: E402


# --- the page count, and the fallback that recovers a level -----------------

def test_occlusion_slice_count_is_the_page_count():
    assert LM.page_count(5, [5]) == 5          # mpl_arena_a
    assert LM.page_count(13, [13]) == 13       # the Lone Echo station


def test_ao_stands_in_when_the_occlusion_map_is_absent():
    """`836c5b14ccc58201`: 40-slice ambient, 8-slice AO, NO BC4 map at all.

    Requiring the BC4 dropped the level -- and with it 2366 of
    `mpl_combat_fission`'s instances -- rather than reporting anything.
    """
    assert LM.page_count(0, [8]) == 8


def test_a_row_with_neither_is_refused_not_guessed():
    assert LM.page_count(0, []) == 0
    assert LM.page_count(0, [0]) == 0


# --- the SG5 collapse weights ----------------------------------------------

def test_sg5_weights_are_the_shader_constants():
    w = LM.sg5_weights()
    assert len(w) == 5
    assert w == sorted(w)                       # rises with the lobe's z
    for z, got in zip(LM.SG5_LOBE_Z, w):
        assert abs(got - z * (2.0 / LM.SG5_LAMBDA) * LM.SG5_SCALE) < 1e-12


def test_weight_sum_is_the_ceiling_the_8bit_path_clipped_against():
    """0.68912 is not a curiosity -- it is the bug's fingerprint.

    `page_irradiance` sums `texture2ddecoder.decode_bc6` output, which is 8-BIT
    and saturates at 1.0, so the collapsed page can never exceed the weight sum
    however bright the bake is. Measured on `mpl_tutorial_lobby` pages 0-2 the
    shipped PNG maxes at exactly 0.6891 while the float sum of the same slices
    reaches 17.4, 17.8 and 18.0 -- a 25x range flattened.
    """
    assert abs(sum(LM.sg5_weights()) - 0.68912) < 1e-5


# --- page-major slice addressing -------------------------------------------

def _array_dds(width: int, height: int, arraysize: int, dxgi: int = 95) -> bytes:
    """A DX10 DDS array whose every slice is filled with its own index byte."""
    header = bytearray(148)
    header[0:4] = b"DDS "
    struct.pack_into("<II", header, 12, height, width)
    struct.pack_into("<I", header, 28, 1)               # one mip
    header[84:88] = b"DX10"
    struct.pack_into("<I", header, 128, dxgi)
    struct.pack_into("<I", header, 140, arraysize)
    blocks = max(1, (width + 3) // 4) * max(1, (height + 3) // 4)
    body = b"".join(bytes([i]) * (blocks * 16) for i in range(arraysize))
    return bytes(header) + body


def _written(tmp_path, basis, pages, lobes):
    blob = _array_dds(8, 8, pages * lobes)
    out = {}
    for page in range(pages):
        names = AL._write_basis_slices(tmp_path, "lvl_p%d" % page, blob, page, basis)
        out[page] = [(n, (tmp_path / n).read_bytes()[148]) for n in names]
    return out


def test_sh4_page_owns_four_consecutive_slices(tmp_path):
    got = _written(tmp_path, "SH4", pages=3, lobes=4)
    assert [v for _n, v in got[0]] == [0, 1, 2, 3]
    assert [v for _n, v in got[1]] == [4, 5, 6, 7]
    assert [v for _n, v in got[2]] == [8, 9, 10, 11]
    assert [n for n, _v in got[1]] == ["lvl_p1_sh%d.dds" % i for i in range(4)]


def test_sg5_page_owns_five_consecutive_slices(tmp_path):
    """The lobe count changes the STRIDE, not just the file count."""
    got = _written(tmp_path, "SG5", pages=3, lobes=5)
    assert [v for _n, v in got[0]] == [0, 1, 2, 3, 4]
    assert [v for _n, v in got[1]] == [5, 6, 7, 8, 9]
    assert [v for _n, v in got[2]] == [10, 11, 12, 13, 14]
    assert [n for n, _v in got[2]] == ["lvl_p2_sg%d.dds" % i for i in range(5)]


def test_an_unknown_basis_writes_nothing(tmp_path):
    assert AL._write_basis_slices(tmp_path, "k", _array_dds(8, 8, 4), 0, "SH9") == []
    assert not list(tmp_path.iterdir())


def test_a_short_array_stops_rather_than_running_off_the_end(tmp_path):
    """Page 1 of a 5-slice array: only slices 5.. exist, and there are none."""
    assert AL._write_basis_slices(tmp_path, "k", _array_dds(8, 8, 5), 1, "SG5") == []
