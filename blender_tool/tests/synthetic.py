"""Build a synthetic in-memory CGMeshListData (primary + GPU bytes) with the
stride-44 vertex layout, for testing the decode core without any
game files or Oodle.

Stride-44 element set (archive 455295a65f8dbb6d):
  ePosition eF32 x3 @0   | eColor eU8n x4 @12 | eTexCoord(UV0) eF32 x2 @16
  eTexCoord(UV1) eU16n x2 @24 | eNormal eS16n x4 @28 | eTangent eS16n x4 @36
"""

from __future__ import annotations

import struct


def enc_s16n(v: float) -> int:
    return max(-32768, min(32767, int(round(v * 32767.0))))


def enc_u8n(v: float) -> int:
    return max(0, min(255, int(round(v * 255.0))))


def enc_u16n(v: float) -> int:
    return max(0, min(65535, int(round(v * 65535.0))))


STRIDE44_ELEMENTS = [
    # (usage, offset, type, count, slot, size, stream, inst)
    (0, 0,  8, 3, 0, 12, 0, 0),   # position eF32
    (1, 12, 1, 4, 0, 4,  0, 0),   # color0   eU8n
    (4, 16, 8, 2, 0, 8,  0, 0),   # uv0      eF32
    (4, 24, 3, 2, 1, 4,  0, 0),   # uv1      eU16n
    (2, 28, 5, 4, 0, 8,  0, 0),   # normal   eS16n
    (3, 36, 5, 4, 0, 8,  0, 0),   # tangent  eS16n
]
STRIDE44 = 44


def make_vertex(pos, color, uv0, uv1, normal, tangent) -> bytes:
    b = bytearray(STRIDE44)
    struct.pack_into("<3f", b, 0, *pos)
    struct.pack_into("<4B", b, 12, *(enc_u8n(c) for c in color))
    struct.pack_into("<2f", b, 16, *uv0)
    struct.pack_into("<2H", b, 24, *(enc_u16n(c) for c in uv1))
    struct.pack_into("<4h", b, 28, *(enc_s16n(c) for c in normal))
    struct.pack_into("<4h", b, 36, *(enc_s16n(c) for c in tangent))
    return bytes(b)


def make_vb_record(active_count: int, rel_gpu: int, numverts: int,
                   elements=STRIDE44_ELEMENTS) -> bytes:
    """One CGVertexBufferData record (0x130 bytes)."""
    rec = bytearray(0x130)
    for i, el in enumerate(elements):
        struct.pack_into("<8B", rec, i * 8, *el)
    struct.pack_into("<I", rec, 0x120, active_count)
    struct.pack_into("<I", rec, 0x128, rel_gpu)
    struct.pack_into("<I", rec, 0x12C, numverts)
    return bytes(rec)


def make_mesh_record(name_hash: int, vb_index: int, ib_index: int,
                     rp_idx: int, rp_count: int, flags: int = 0x20,
                     aabb=(-1, -1, -1, 1, 1, 1)) -> bytes:
    """One CGMeshData record (0x80 bytes)."""
    rec = bytearray(0x80)
    struct.pack_into("<Q", rec, 0x00, name_hash)
    struct.pack_into("<I", rec, 0x08, vb_index)
    struct.pack_into("<I", rec, 0x0C, ib_index)
    struct.pack_into("<I", rec, 0x1C, rp_idx)
    struct.pack_into("<I", rec, 0x20, rp_count)
    struct.pack_into("<6f", rec, 0x24, *aabb)
    struct.pack_into("<I", rec, 0x4C, flags)
    return bytes(rec)


def make_renderparam(material_idx: int, shaderset_idx: int, idx_start: int,
                     idx_count: int, primtype: int = 4) -> bytes:
    """One CGRenderParams record (0x68 bytes)."""
    rec = bytearray(0x68)
    struct.pack_into("<I", rec, 0x28, material_idx)
    struct.pack_into("<I", rec, 0x2C, shaderset_idx)
    struct.pack_into("<I", rec, 0x40, primtype)
    struct.pack_into("<I", rec, 0x44, idx_start)
    struct.pack_into("<I", rec, 0x48, idx_count)
    struct.pack_into("<I", rec, 0x58, 0xFFFFFFFF)   # lodprimsetidx = none
    return bytes(rec)


def make_ib_record(rel_gpu: int, num_indices: int, index_size: int = 2) -> bytes:
    """One CGIndexBufferData record (0x10 bytes)."""
    rec = bytearray(0x10)
    struct.pack_into("<I", rec, 0x00, rel_gpu)
    struct.pack_into("<I", rec, 0x04, num_indices)
    struct.pack_into("<I", rec, 0x08, index_size)
    return bytes(rec)


def build_single_quad():
    """A one-mesh, one-draw quad (4 verts, 2 tris). Returns everything the
    decode core needs plus the expected values for assertions."""
    verts = [
        # pos,               color,                 uv0,        uv1,        normal,        tangent
        ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 1.0), (0.0, 0.0), (0.0, 0.0), (0.0, 0.0, 1.0, 1.0), (1.0, 0.0, 0.0, 1.0)),
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0, 1.0), (1.0, 0.0), (1.0, 0.0), (0.0, 0.0, 1.0, 1.0), (1.0, 0.0, 0.0, 1.0)),
        ((1.0, 1.0, 0.0), (0.0, 0.0, 1.0, 1.0), (1.0, 1.0), (1.0, 1.0), (0.0, 0.0, 1.0, 1.0), (1.0, 0.0, 0.0, 1.0)),
        ((0.0, 1.0, 0.0), (1.0, 1.0, 1.0, 1.0), (0.0, 1.0), (0.0, 1.0), (0.0, 0.0, 1.0, 1.0), (1.0, 0.0, 0.0, 1.0)),
    ]
    indices = [0, 1, 2, 0, 2, 3]

    # --- GPU blob: vertices at rel_gpu=0, indices right after ---------------
    gpu = bytearray()
    for v in verts:
        gpu += make_vertex(*v)
    ib_rel = len(gpu)
    gpu += struct.pack(f"<{len(indices)}H", *indices)

    # --- primary blob: four tables laid end to end --------------------------
    mesh_tbl = make_mesh_record(0xABCDEF0123456789, 0, 0, 0, 1, flags=0x20)
    rp_tbl = make_renderparam(0, 0, 0, len(indices))
    vb_tbl = make_vb_record(len(STRIDE44_ELEMENTS), 0, len(verts))
    ib_tbl = make_ib_record(ib_rel, len(indices), 2)

    primary = bytearray()
    meshes_off = len(primary); primary += mesh_tbl
    rp_off = len(primary); primary += rp_tbl
    vb_off = len(primary); primary += vb_tbl
    ib_off = len(primary); primary += ib_tbl

    return {
        "primary": bytes(primary),
        "gpu": bytes(gpu),
        "gpu_base": 0,
        "tables": {
            "meshes": (1, meshes_off),
            "renderparams": (1, rp_off),
            "vertexbuffers": (1, vb_off),
            "indexbuffers": (1, ib_off),
        },
        "expected": {
            "vertices": [v[0] for v in verts],
            "colors": [v[1] for v in verts],
            "uv0": [v[2] for v in verts],
            "uv1": [v[3] for v in verts],
            "indices": indices,
            "stride": STRIDE44,
        },
    }
