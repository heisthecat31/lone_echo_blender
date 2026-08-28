"""Echo VR `CGMeshData` baked-lightmap ids, and why they are where they are.

Lone Echo puts `lightmapindex` / `lmsliceindex` / `numlobes` at 0x6C / 0x70 /
0x74 of an 0x80 array0 record. Echo VR's record is 0x98, and the triple does
NOT keep its absolute offset -- it keeps its distance from the END of the
record (0x14), so all three shift by exactly the 0x18 the struct grew by.

⛔ The Echo VR extractor used to emit the `0xFFFFFFFF` / `0` defaults for every
mesh, which made a manifest that could not distinguish "this mesh has no bake
authored" from "the extractor never looked". A room full of legitimately
unbaked props then reads as an extraction bug -- which is exactly how a level
whose dark meshes are all authored unlit got investigated as broken.

Located by scanning every u32 column of 90 array0 records and confirmed three
ways: `+0x8C` reads 4 on 90/90 (Lone Echo ships 4 on 1221/1221); `+0x84` holds
the unlit sentinel on 57 and a real row on 33 -- the same 33 submeshes
`evr_apply_lighting` binds by a wholly independent route; and the three sit
contiguous, as in Lone Echo.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import le_scene_extract as LE                                # noqa: E402

#: Echo VR (Win10) offsets, restated here so the test fails if either side moves.
W10_LIGHTMAPINDEX = 0x84
W10_LMSLICEINDEX = 0x88
W10_NUMLOBES = 0x8C
W10_REC = 0x98

#: Lone Echo (Win7) offsets, from `le_mesh.meshlist`.
W7_LIGHTMAPINDEX = 0x6C
W7_LMSLICEINDEX = 0x70
W7_NUMLOBES = 0x74
W7_REC = 0x80


def test_the_triple_keeps_its_distance_from_the_END_of_the_record():
    """★ The rule that put the offsets where they are.

    Not "same absolute offset" and not "same field order from the front" --
    the same 0x14 from the end. Getting this wrong reads three unrelated words.
    """
    for w10, w7 in ((W10_LIGHTMAPINDEX, W7_LIGHTMAPINDEX),
                    (W10_LMSLICEINDEX, W7_LMSLICEINDEX),
                    (W10_NUMLOBES, W7_NUMLOBES)):
        assert W10_REC - w10 == W7_REC - w7
    assert W10_REC - W10_LIGHTMAPINDEX == 0x14


def test_the_shift_is_exactly_how_much_the_record_grew():
    assert W10_REC - W7_REC == 0x18
    assert W10_LIGHTMAPINDEX - W7_LIGHTMAPINDEX == 0x18


def test_the_three_fields_are_contiguous_u32s():
    assert W10_LMSLICEINDEX - W10_LIGHTMAPINDEX == 4
    assert W10_NUMLOBES - W10_LMSLICEINDEX == 4
    assert W10_NUMLOBES + 4 <= W10_REC


def test_the_unlit_sentinel_is_not_row_zero():
    """⛔ `0xFFFFFFFF` means UNLIT. Treating it as an index reads row 4294967295,
    and treating it as 0 silently lights every unbaked mesh from page 0."""
    assert LE.LIGHTMAP_NONE == 0xFFFFFFFF
    assert LE.LIGHTMAP_NONE != 0


def _record(lightmap_index, slice_index, numlobes):
    rec = bytearray(W10_REC)
    struct.pack_into("<I", rec, W10_LIGHTMAPINDEX, lightmap_index)
    struct.pack_into("<I", rec, W10_LMSLICEINDEX, slice_index)
    struct.pack_into("<I", rec, W10_NUMLOBES, numlobes)
    return bytes(rec)


def test_a_lit_record_round_trips():
    rec = _record(1, 3, 4)
    assert struct.unpack_from("<I", rec, W10_LIGHTMAPINDEX)[0] == 1
    assert struct.unpack_from("<I", rec, W10_LMSLICEINDEX)[0] == 3
    assert struct.unpack_from("<I", rec, W10_NUMLOBES)[0] == 4


def test_an_unlit_record_round_trips():
    rec = _record(LE.LIGHTMAP_NONE, LE.LIGHTMAP_NONE, 4)
    assert struct.unpack_from("<I", rec, W10_LIGHTMAPINDEX)[0] == LE.LIGHTMAP_NONE
    # ⚠ numlobes stays 4 on an UNLIT mesh -- it is a format constant, not a
    # lit/unlit flag, so it must never be used to decide whether to bind.
    assert struct.unpack_from("<I", rec, W10_NUMLOBES)[0] == 4


def test_scene_mesh_still_defaults_to_unlit_when_nothing_was_read():
    """A model that is not a Win10 mesh list yields no ids, and the mesh must
    stay at the sentinel rather than inheriting a neighbour's row."""
    mesh = LE.SceneMesh(index=0, name_hash=0, matidx=0, shdidx=0,
                        aabb_min=(0, 0, 0), aabb_max=(0, 0, 0),
                        instance_offset=0, instance_count=0,
                        positions=[], indices=[])
    assert mesh.lightmap_index == LE.LIGHTMAP_NONE
    assert mesh.lm_slice_index == LE.LIGHTMAP_NONE
    assert mesh.numlobes == 0
