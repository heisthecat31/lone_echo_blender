"""Core tests for scripts/le_static_scatter.decode_static_master (no archive).

Verifies the corrected layout: the tail scalars carry the real
totalinstances + GPU instancedata pointer, and instancescount/instanceoffsets
are recovered as a contiguous-per-mesh-run binding.
"""
from __future__ import annotations

import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from le_static_scatter import (  # noqa: E402
    decode_static_master,
    decode_instancetype_table,
    decode_instance_transform,
    instance_record_offset,
)


def _build(lod_nodes=3, counts=(10, 15, 25), ido=1000, ids=500, its=None,
           total=None, numvis=7):
    num_meshes = len(counts)
    total = sum(counts) if total is None else total
    its = num_meshes * 36 if its is None else its
    ito = ido + ids
    out = bytearray()
    # lod.nodes CTableA<CReal4,16>: [u32 n][12 pad][n*16]
    out += struct.pack("<I", lod_nodes) + b"\x00" * 12 + b"\x00" * (lod_nodes * 16)
    out += b"\x00" * 16  # filler
    # instancescount CTable<u32>
    out += struct.pack("<I", num_meshes) + struct.pack("<" + "I" * num_meshes, *counts)
    # instanceoffsets CTable<u32> = prefix sum
    offs = [0]
    for c in counts[:-1]:
        offs.append(offs[-1] + c)
    out += struct.pack("<I", num_meshes) + struct.pack("<" + "I" * num_meshes, *offs)
    out += b"\x00" * 16  # filler
    out += struct.pack("<6I", total, numvis, ido, ids, ito, its)  # 6 trailing scalars
    return bytes(out)


def test_totalinstances_and_binding():
    d = decode_static_master(_build(counts=(10, 15, 25)))
    assert d.num_instances == 50
    assert d.num_meshes == 3
    assert d.lod_node_count == 3
    assert d.instancescount == [10, 15, 25]
    assert d.instanceoffsets == [0, 10, 25]
    assert d.gpu_instancedata == (1000, 500)
    assert d.warnings == []


def test_lod_nodes_not_treated_as_instances():
    # leading count (lod.nodes) differs from real instance total
    d = decode_static_master(_build(lod_nodes=5, counts=(2, 3), total=5))
    assert d.lod_node_count == 5
    assert d.num_instances == 5      # from the tail scalar, not the leading count
    assert d.num_meshes == 2
    assert d.instancescount == [2, 3]


def test_mesh_for_instance_contiguous_runs():
    d = decode_static_master(_build(counts=(10, 15, 25)))  # offsets [0,10,25]
    assert d.mesh_for_instance(0) == 0
    assert d.mesh_for_instance(9) == 0
    assert d.mesh_for_instance(10) == 1
    assert d.mesh_for_instance(24) == 1
    assert d.mesh_for_instance(25) == 2
    assert d.mesh_for_instance(49) == 2


def test_gpu_partition_warns_when_inconsistent():
    # ido+ids != ito -> warning
    d = decode_static_master(_build(ido=1000, ids=500, its=72))  # ito forced = 1500 ok;
    # force inconsistency by overriding its so its%36 != 0
    bad = _build(its=70)
    d2 = decode_static_master(bad)
    assert any("multiple of 36" in w for w in d2.warnings)


def test_truncated_raises():
    try:
        decode_static_master(b"\x00" * 8)
    except ValueError:
        return
    raise AssertionError("expected ValueError on too-small blob")


# --- GPU instancedata decode ---------------------------------------------

def _itrec(block_words, first, stride_words, total, bk0=0, bk1=0):
    # one 36-byte instancetypedata record (9x u32): +0 blockoff_words, +4 first,
    # +8 stride_words, +12=2, +16=0, +20=0, +24 total, +28/+32 bookkeeping
    return struct.pack("<9I", block_words, first, stride_words, 2, 0, 0, total, bk0, bk1)


def test_instancetype_table_word_units_and_fields():
    # two mesh-types: counts (2,3); strides (10,8) words; blocks tile contiguously
    itd = _itrec(0, 0, 10, 5) + _itrec(20, 2, 8, 5)   # block1 words = count0*stride0 = 2*10
    recs = decode_instancetype_table(itd, 2)
    assert len(recs) == 2
    # offsets/strides normalized from 4-byte words to bytes
    assert recs[0].block_offset == 0 and recs[0].stride == 40
    assert recs[1].block_offset == 80 and recs[1].stride == 32
    assert recs[0].first_instance == 0 and recs[1].first_instance == 2
    assert recs[0].num_instances == 5 and recs[1].num_instances == 5
    assert recs[0].raw[3] == 2  # +12 constant tag
    # tiling: block_offset[1] == count0 * stride0 (bytes)
    assert recs[1].block_offset == 2 * recs[0].stride


def test_instancetype_table_short_raises():
    try:
        decode_instancetype_table(b"\x00" * 35, 1)
    except ValueError:
        return
    raise AssertionError("expected ValueError on short instancetypedata")


def test_instance_record_offset():
    rec = decode_instancetype_table(_itrec(5, 0, 8, 100), 1)[0]  # block 20 B, stride 32 B
    assert instance_record_offset(rec, 0) == 20
    assert instance_record_offset(rec, 1) == 52
    assert instance_record_offset(rec, 3) == 20 + 3 * 32


def test_instance_transform_roundtrip_identity():
    buf = (struct.pack("<3f", 1.5, -2.5, 3.0)      # translation
           + struct.pack("<4h", 0, 0, 0, 32767)    # rotation (identity, x,y,z,w)
           + struct.pack("<3e", 0.5, 0.5, 2.0))    # scale (f16)
    t = decode_instance_transform(buf)
    assert t.translation == (1.5, -2.5, 3.0)
    assert abs(t.rotation[3] - 1.0) < 1e-4 and t.rotation[:3] == (0.0, 0.0, 0.0)
    assert t.scale == (0.5, 0.5, 2.0)          # exact in f16


def test_instance_transform_unit_quat():
    # equal components -> each snorm 16384/32767; |q| must be ~1
    q = struct.pack("<4h", 16384, 16384, 16384, 16384)
    buf = struct.pack("<3f", 0.0, 0.0, 0.0) + q + struct.pack("<3e", 1.0, 1.0, 1.0)
    t = decode_instance_transform(buf)
    n = math.sqrt(sum(c * c for c in t.rotation))
    assert abs(n - 1.0) < 1e-3


def test_instance_transform_at_offset():
    # decode a record that is not at buffer start (exercises `off`)
    pad = b"\xab" * 7
    buf = pad + struct.pack("<3f", 4.0, 5.0, 6.0) + struct.pack("<4h", 0, 0, 0, 32767) \
        + struct.pack("<3e", 1.0, 1.0, 1.0)
    t = decode_instance_transform(buf, len(pad))
    assert t.translation == (4.0, 5.0, 6.0)
