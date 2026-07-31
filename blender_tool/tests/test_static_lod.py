"""Core tests for `le_mesh.static_lod` — the static-scatter LOD system.

Archive-free: builds synthetic `SGStaticInstancesData` blobs whose byte layout
mirrors the on-disk one (byte-packed tables, the two `CTableA<T,16>`
12-byte pads, the 8-aligned `totalnumlods`, the fixed 24-byte scalar tail) and
checks the decode, the derived instance -> (group, level) binding, and the
selection/clamping rules.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from le_mesh.static_lod import (  # noqa: E402
    LOD_ALL, LOD_COARSEST, decode_static_lod, select_lod_instances,
)


def _tableA16(vec4s):
    out = struct.pack("<I", len(vec4s)) + b"\x00" * 12
    for v in vec4s:
        out += struct.pack("<4f", *v)
    return out


def _table(fmt, values):
    return struct.pack("<I", len(values)) + struct.pack(f"<{len(values)}{fmt}", *values)


def _build(group_levels=(2, 3, 1), instances_per_level=1, num_meshes=6,
           meshlist_filler=64, align_u64=True, skip_first_level=(),
           lead_lods=0):
    """Synthesize a master blob whose LOD block describes `group_levels`.

    LOD entries are laid out group by group (contiguous per group, which is the
    invariant `_derive_levels` relies on). Instance k of a level points at that
    level's LOD index, so the expected (group, level) of every instance is known.

    `lead_lods` prepends that many `hierlods`-parent LOD entries (node ids of
    their own, never referenced by an instance) so the leaf runs start further in.
    `skip_first_level` names groups whose LOD-0 entry gets NO instances, which is
    the `1454b12d3ce15e38` / `cca8d487dd923ada` shape: raw levels then start at 1
    and must be rebased to 0.
    """
    nodelookup = []
    for p in range(lead_lods):                       # hierlods parent entries
        nodelookup.append(len(group_levels) + p)
    for g, k in enumerate(group_levels):
        nodelookup += [g] * k
    total_lods = len(nodelookup)
    nodes = [(float(g), 0.0, 0.0, 0.0)
             for g in range(len(group_levels) + lead_lods)]
    fades = [(float(i), 1.0, 2.0, 3.0) for i in range(total_lods)]
    hierlods = [(p, lead_lods, total_lods - lead_lods) for p in range(lead_lods)] \
        or [(0, 0, total_lods)]

    lodfadelookup = []
    expected = []      # (group, level) per instance
    for g, k in enumerate(group_levels):
        first = lead_lods + sum(group_levels[:g])
        drop = 1 if g in skip_first_level else 0
        for lvl in range(drop, k):
            for _ in range(instances_per_level):
                lodfadelookup.append(first + lvl)
                expected.append((g, lvl - drop))
    num_instances = len(lodfadelookup)

    # `visstrlookup` is per-instance but per-GROUP valued: constant across a
    # group's instances, a bijection group <-> value, values exactly 0..n-1 in
    # first-appearance order (measured on 62/62 masters). `numvisentries`
    # is its distinct count.
    visstr, vis_of_group = [], {}
    for g, _lvl in expected:
        visstr.append(vis_of_group.setdefault(g, len(vis_of_group)))
    numvisentries = len(vis_of_group)

    # per-mesh tables: spread the instances over `num_meshes` mesh-types
    base, rem = divmod(num_instances, num_meshes)
    counts = [base + (1 if i < rem else 0) for i in range(num_meshes)]
    offs, acc = [], 0
    for c in counts:
        offs.append(acc)
        acc += c

    blob = bytearray()
    blob += _tableA16(nodes)
    blob += _tableA16(fades)
    blob += struct.pack("<I", len(hierlods))
    for h in hierlods:
        blob += struct.pack("<3I", *h)
    blob += _table("H", nodelookup)
    if align_u64:
        blob += b"\x00" * ((8 - len(blob) % 8) % 8)
    # else: emit `totalnumlods` byte-packed, wherever nodelookup happened to end
    blob += struct.pack("<Q", total_lods)

    # inline mesh-list stand-in: only its FIRST u32 (the meshes CTable count) is
    # read by the decoder, which uses it to confirm where the LOD block ends.
    blob += struct.pack("<I", num_meshes) + b"\x00" * meshlist_filler

    blob += _table("I", counts)                                   # instancescount
    blob += _table("I", offs)                                     # instanceoffsets
    blob += struct.pack("<I", num_meshes) + b"\x00" * (num_meshes * 12)   # irrsamplelocs
    blob += struct.pack("<I", num_instances) + b"\x00" * num_instances    # dirlightmasks
    blob += _table("H", visstr)                                   # visstrlookup
    blob += _table("I", lodfadelookup)                            # lodfadelookup
    nwords = -(-num_meshes // 32)
    blob += _table("I", [0xA5A5A5A5] * nwords)                    # ditherfadeflags
    blob += struct.pack("<6I", num_instances, numvisentries, 1000, 500, 1500,
                        num_meshes * 36)
    return bytes(blob), num_meshes, num_instances, expected


def test_decode_tables_and_offsets():
    blob, nm, ni, _ = _build()
    d = decode_static_lod(blob, nm, ni)
    assert d.warnings == [], d.warnings
    assert len(d.nodes) == 3
    assert d.totalnumlods == 6 == len(d.nodelookup) == len(d.fades)
    assert d.hierlods == [(0, 0, 6)]
    assert len(d.lodfadelookup) == ni
    assert len(d.instancescount) == nm and len(d.instanceoffsets) == nm
    assert sum(d.instancescount) == ni
    # the mesh-list offset points at the meshes CTable header
    assert struct.unpack_from("<I", blob, d.meshlist_offset)[0] == nm


def test_derived_group_and_level():
    blob, nm, ni, expected = _build(group_levels=(2, 3, 1))
    d = decode_static_lod(blob, nm, ni)
    got = list(zip(d.group_of_instance, d.level_of_instance))
    assert got == expected, got
    assert d.group_num_levels == {0: 2, 1: 3, 2: 1}
    assert d.num_groups == 3
    assert d.max_level == 2
    assert d.levels_of_instance(0) == 2
    assert d.fade_of_instance(0) == (0.0, 1.0, 2.0, 3.0)


def test_multiple_instances_per_level():
    """A level may own several instances (multi-part LOD0 collapsing later)."""
    blob, nm, ni, expected = _build(group_levels=(3, 2), instances_per_level=3)
    d = decode_static_lod(blob, nm, ni)
    assert list(zip(d.group_of_instance, d.level_of_instance)) == expected
    assert d.group_num_levels == {0: 3, 1: 2}
    keep = select_lod_instances(0, d.group_of_instance, d.level_of_instance,
                                d.group_num_levels)
    assert len(keep) == 6          # 3 instances at level 0 in each of 2 groups


def test_selection_clamps_per_group():
    blob, nm, ni, _ = _build(group_levels=(2, 3, 1))
    d = decode_static_lod(blob, nm, ni)
    args = (d.group_of_instance, d.level_of_instance, d.group_num_levels)

    lvl0 = select_lod_instances(0, *args)
    assert [d.level_of_instance[i] for i in lvl0] == [0, 0, 0]
    assert sorted(d.group_of_instance[i] for i in lvl0) == [0, 1, 2]

    # LOD 2 clamps: group 0 (2 levels) -> 1, group 1 (3) -> 2, group 2 (1) -> 0.
    lvl2 = select_lod_instances(2, *args)
    assert [(d.group_of_instance[i], d.level_of_instance[i]) for i in lvl2] == \
        [(0, 1), (1, 2), (2, 0)]

    # every group is represented exactly once at any requested level
    for want in range(0, 6):
        sel = select_lod_instances(want, *args)
        assert sorted(d.group_of_instance[i] for i in sel) == [0, 1, 2]

    coarse = select_lod_instances(LOD_COARSEST, *args)
    assert [(d.group_of_instance[i], d.level_of_instance[i]) for i in coarse] == \
        [(0, 1), (1, 2), (2, 0)]

    assert select_lod_instances(LOD_ALL, *args) == list(range(ni))


def test_slack_lod_entries_past_totalnumlods_are_trimmed():
    """`totalnumlods` is authoritative and can be SMALLER than the table counts.

    min_itc_master stores 8,276 nodelookup entries but totalnumlods == 8,274; the
    two slack rows repeat node 0 and would otherwise read as a non-contiguous LOD
    run. Nothing references them, so they must be trimmed, not diagnosed.
    """
    blob, nm, ni, expected = _build(group_levels=(2, 3, 1))
    bad = bytearray(blob)
    # append 2 stale entries repeating node 0 + lower totalnumlods below the count
    live = 6
    look_hdr = 16 + 3 * 16 + 16 + live * 16 + 4 + 1 * 12
    assert struct.unpack_from("<I", bad, look_hdr)[0] == live
    struct.pack_into("<I", bad, look_hdr, live + 2)                 # 2 slack rows
    ins = look_hdr + 4 + live * 2
    bad[ins:ins] = struct.pack("<2H", 0, 0)                         # both -> node 0
    d = decode_static_lod(bytes(bad), nm, ni)
    assert d.totalnumlods == live
    assert len(d.nodelookup) == live
    assert any("trimmed 2 LOD entries" in w for w in d.warnings), d.warnings
    assert not any("not contiguous" in w for w in d.warnings), d.warnings
    assert list(zip(d.group_of_instance, d.level_of_instance)) == expected


def test_dither_fade_bitset():
    blob, nm, ni, _ = _build(num_meshes=6)
    d = decode_static_lod(blob, nm, ni)
    # 0xA5A5A5A5 = 1010...0101 -> bit0 set, bit1 clear, bit2 set, bit3 clear
    assert [d.dither_fade(i) for i in range(4)] == [True, False, True, False]


def test_byte_packed_u64_is_tolerated():
    """`totalnumlods` lands 8-aligned in both known masters — but that is also
    exactly where a byte-packed write would put it, so the two readings are
    indistinguishable there. Prove the decoder still finds the mesh-list when the
    u64 is packed onto a non-8-aligned offset (5 LOD entries -> nodelookup ends
    at 6 mod 8)."""
    blob, nm, ni, expected = _build(group_levels=(2, 2, 1), align_u64=False)
    assert len(blob) % 8 != 0 or True       # (layout asserted via the walk below)
    d = decode_static_lod(blob, nm, ni)
    assert d.meshlist_offset % 8 != 0, "expected a byte-packed (unaligned) u64"
    assert struct.unpack_from("<I", blob, d.meshlist_offset)[0] == nm
    assert list(zip(d.group_of_instance, d.level_of_instance)) == expected


def test_group_with_unreferenced_first_lod_is_rebased():
    """A group's FIRST LOD entry can be unreferenced — rebase or it vanishes.

    Corpus: 6 of 62 masters (`1454b12d3ce15e38` node 2194, `513c36c202cc3469`
    node 301, `5fc785e98bfa8179` x6, `87180b1b9bf8b3af` x5, `c660009ad6521671`,
    `cca8d487dd923ada` x7) have nodes whose run starts one entry before the first
    entry any instance points at. Un-rebased, those levels come out 1..k-1 and a
    request for LOD 0 selects NOTHING — 21 groups / 94 instances silently
    disappeared at the importer's DEFAULT level.
    """
    blob, nm, ni, expected = _build(group_levels=(3, 3, 2), skip_first_level=(1,))
    d = decode_static_lod(blob, nm, ni)
    assert list(zip(d.group_of_instance, d.level_of_instance)) == expected
    assert min(lv for g, lv in zip(d.group_of_instance, d.level_of_instance)
               if g == 1) == 0
    assert d.group_num_levels == {0: 3, 1: 2, 2: 2}
    assert any("rebased 1 LOD group" in w for w in d.warnings), d.warnings
    # every group is represented at the default LOD 0 — the actual regression
    keep = select_lod_instances(0, d.group_of_instance, d.level_of_instance,
                                d.group_num_levels)
    assert sorted({d.group_of_instance[i] for i in keep}) == [0, 1, 2]


def test_hierlods_parent_region_is_not_a_level_and_may_be_non_monotonic():
    """The LOD array's front is `hierlods` parents; they carry no instances.

    `SHierLOD` is `{parent, firstchild, numchildren}` — `parent` is
    a LOD index, NOT the record index: on `3c157c98a146325a` three records share
    `parent == 9`, and its `nodelookup[0:12]` is `0..10, 9`, i.e. non-monotonic.
    Leaf level derivation must be unaffected.
    """
    blob, nm, ni, expected = _build(group_levels=(2, 3), lead_lods=3)
    d = decode_static_lod(blob, nm, ni)
    assert len(d.hierlods) == 3
    assert [h[0] for h in d.hierlods] == [0, 1, 2]      # parent = a LOD index
    assert all(h[1] == 3 for h in d.hierlods)           # firstchild past the heads
    assert list(zip(d.group_of_instance, d.level_of_instance)) == expected
    assert d.group_num_levels == {0: 2, 1: 3}
    assert not any("rebased" in w for w in d.warnings), d.warnings


def test_visstrlookup_is_per_group_not_per_instance():
    """`visstrlookup[i]` is the LOD GROUP's visibility entry, not the identity.

    Stream-confirmed on 62/62 masters: constant across a group's instances, a
    bijection group <-> value onto `0..numgroups-1`, and `numvisentries` equals
    its distinct count. station_front's is NOT the identity (it first diverges at
    instance 16, 5,779 distinct values over 21,394 instances).
    """
    blob, nm, ni, _ = _build(group_levels=(3, 2), instances_per_level=2)
    d = decode_static_lod(blob, nm, ni)
    assert len(d.visstrlookup) == ni
    assert d.numvisentries == len(set(d.visstrlookup)) == 2
    per_group = {}
    for g, v in zip(d.group_of_instance, d.visstrlookup):
        per_group.setdefault(g, set()).add(v)
    assert all(len(s) == 1 for s in per_group.values())
    vals = [next(iter(s)) for s in per_group.values()]
    assert sorted(vals) == list(range(len(vals)))
    assert d.visstrlookup != list(range(ni))     # not the identity permutation
    assert d.warnings == [], d.warnings


def test_numvisentries_mismatch_warns():
    blob, nm, ni, _ = _build(group_levels=(2, 2))
    bad = bytearray(blob)
    struct.pack_into("<I", bad, len(bad) - 24 + 4, 99)
    d = decode_static_lod(bytes(bad), nm, ni)
    assert any("numvisentries=99" in w for w in d.warnings), d.warnings


def test_bad_tail_table_raises():
    blob, nm, ni, _ = _build()
    bad = bytearray(blob)
    # corrupt the lodfadelookup header -> the backwards walk must refuse to guess
    hdr = len(bad) - 24 - (4 + (-(-nm // 32)) * 4) - (4 + ni * 4)
    struct.pack_into("<I", bad, hdr, ni + 7)
    try:
        decode_static_lod(bytes(bad), nm, ni)
    except ValueError as exc:
        assert "lodfadelookup" in str(exc), exc
    else:                                   # pragma: no cover
        raise AssertionError("expected ValueError on a corrupt table header")
