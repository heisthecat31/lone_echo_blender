"""Validate CGMeshListData serialization from the runtime loader.

CGMeshListData::AttachToStream reads
runtime-layout tables in this exact order:

  u32 mesh_count,          CGMeshData[mesh_count]              (0x80 each)
  u32 renderparam_count,   CGRenderParams[renderparam_count]   (0x68 each)
  u32 vertexbuffer_count,  CGVertexBufferData[vertexbuffer_count] (0x130 each)
  u32 morphbuffer_count,   CGVertexBufferData[morphbuffer_count]
  u32 morphib_count,       CGIndexBufferData[morphib_count]    (0x10 each)
  u32 indexbuffer_count,   CGIndexBufferData[indexbuffer_count]
  u32 lodchild_count,      u32[lodchild_count]
  u32 cbufferidx_count,    u32[cbufferidx_count]
  u32 numcbuffers, u32 cbufferoffset, u64 gpudatasize

The current retail target is a CArchiveResourceWin7 wrapper. This scanner is
useful as a negative control for "does this byte range already match the
runtime table order?", but archive subresources must be located through
le_archive_decode.py before interpreting offsets.

Run with Windows Python so le_oodle can load the game Oodle DLL:
    python.exe scripts/le_meshlist_decode.py
"""

from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass

from le_oodle import DATA_ROOT, load_decompressed


ARCHIVE_PRIMARY = DATA_ROOT / "primary" / "e5bd8207135b8887" / "v13363680368"
ARCHIVE_GPU = DATA_ROOT / "GPU" / "005a5579fb36b249" / "v13363680368"
DEFAULT_HASH = "455295a65f8dbb6d"

MESH_SIZE = 0x80
RP_SIZE = 0x68
VB_SIZE = 0x130
IB_SIZE = 0x10


@dataclass
class Table:
    count_off: int
    data_off: int
    count: int
    elem_size: int

    @property
    def end(self) -> int:
        return self.data_off + self.count * self.elem_size


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def f32x6(data: bytes, off: int) -> tuple[float, ...]:
    return struct.unpack_from("<6f", data, off)


def parse_table(data: bytes, off: int, elem_size: int, max_count: int) -> tuple[Table, int] | None:
    if off + 4 > len(data):
        return None
    count = u32(data, off)
    if count > max_count:
        return None
    table = Table(off, off + 4, count, elem_size)
    if table.end > len(data):
        return None
    return table, table.end


def vertex_stride(data: bytes, vb_off: int) -> int:
    elem_count = u32(data, vb_off + 0x120)
    if 0 < elem_count <= 36:
        stride = 0
        for i in range(elem_count):
            elem = vb_off + i * 8
            usage, offset, typ, count, _slot, size, stream, inst = data[elem : elem + 8]
            if usage > 11 or typ > 10 or count > 4 or size > 16 or stream > 3 or inst > 1:
                return -1
            if count == 0 or size == 0:
                return -1
            stride = max(stride, offset + size)
        return stride

    used = u64(data, vb_off + 0x120)
    stride = 0
    for i in range(36):
        if not (used & (1 << i)):
            continue
        elem = vb_off + i * 8
        usage, offset, typ, count, _slot, size, stream, inst = data[elem : elem + 8]
        if usage > 11 or typ > 10 or count > 4 or size > 16 or stream > 3 or inst > 1:
            return -1
        if count == 0 or size == 0:
            return -1
        stride = max(stride, offset + size)
    return stride


def parse_candidate(prim: bytes, start: int, expected_gpu_size: int) -> dict[str, object] | None:
    off = start
    parsed = {}
    for name, size, max_count in (
        ("meshes", MESH_SIZE, 100_000),
        ("renderparams", RP_SIZE, 100_000),
        ("vertexbuffers", VB_SIZE, 20_000),
        ("morphbuffers", VB_SIZE, 20_000),
        ("morphindexbuffers", IB_SIZE, 20_000),
        ("indexbuffers", IB_SIZE, 20_000),
        ("lodchildindices", 4, 200_000),
        ("cbufferidx", 4, 200_000),
    ):
        result = parse_table(prim, off, size, max_count)
        if result is None:
            return None
        table, off = result
        parsed[name] = table

    if off + 16 > len(prim):
        return None
    numcbuffers = u32(prim, off)
    cbufferoffset = u32(prim, off + 4)
    gpudatasize = u64(prim, off + 8)
    end = off + 16
    if gpudatasize != expected_gpu_size:
        return None
    if cbufferoffset > expected_gpu_size or numcbuffers > 100_000:
        return None

    meshes: Table = parsed["meshes"]  # type: ignore[assignment]
    renderparams: Table = parsed["renderparams"]  # type: ignore[assignment]
    vertexbuffers: Table = parsed["vertexbuffers"]  # type: ignore[assignment]
    indexbuffers: Table = parsed["indexbuffers"]  # type: ignore[assignment]

    # Strong cross-table validation from CGMeshData references.
    checked_meshes = min(meshes.count, 2048)
    valid_mesh_refs = 0
    plausible_aabbs = 0
    for i in range(checked_meshes):
        m = meshes.data_off + i * MESH_SIZE
        vbindex = u32(prim, m + 0x08)
        ibindex = u32(prim, m + 0x0C)
        renderidx = u32(prim, m + 0x1C)
        rendern = u32(prim, m + 0x20)
        if (
            vbindex < vertexbuffers.count
            and ibindex < indexbuffers.count
            and renderidx <= renderparams.count
            and renderidx + rendern <= renderparams.count
        ):
            valid_mesh_refs += 1
        aabb = f32x6(prim, m + 0x24)
        if all(abs(v) < 100_000 for v in aabb) and aabb[0] <= aabb[3] and aabb[1] <= aabb[4] and aabb[2] <= aabb[5]:
            plausible_aabbs += 1
    if checked_meshes and valid_mesh_refs < checked_meshes * 0.90:
        return None

    # Validate buffer descriptors against the GPU payload size.
    vb_valid = 0
    vb_checked = min(vertexbuffers.count, 4096)
    for i in range(vb_checked):
        vb = vertexbuffers.data_off + i * VB_SIZE
        stride = vertex_stride(prim, vb)
        gpu_off = u32(prim, vb + 0x128)
        nverts = u32(prim, vb + 0x12C)
        if stride > 0 and gpu_off <= expected_gpu_size and gpu_off + stride * nverts <= expected_gpu_size:
            vb_valid += 1
    if vb_checked and vb_valid < vb_checked * 0.75:
        return None

    ib_valid = 0
    ib_checked = min(indexbuffers.count, 4096)
    for i in range(ib_checked):
        ib = indexbuffers.data_off + i * IB_SIZE
        gpu_off, nidx, indexsize, pad = struct.unpack_from("<4I", prim, ib)
        if indexsize in (2, 4) and pad == 0 and gpu_off <= expected_gpu_size and gpu_off + nidx * indexsize <= expected_gpu_size:
            ib_valid += 1
    if ib_checked and ib_valid < ib_checked * 0.75:
        return None

    return {
        **parsed,
        "start": start,
        "end": end,
        "numcbuffers": numcbuffers,
        "cbufferoffset": cbufferoffset,
        "gpudatasize": gpudatasize,
        "valid_mesh_refs": valid_mesh_refs,
        "checked_meshes": checked_meshes,
        "plausible_aabbs": plausible_aabbs,
        "vb_valid": vb_valid,
        "vb_checked": vb_checked,
        "ib_valid": ib_valid,
        "ib_checked": ib_checked,
    }


def print_candidate(prim: bytes, cand: dict[str, object], limit: int) -> None:
    print(f"\nCGMeshListData stream at prim[{cand['start']}] end={cand['end']}")
    for name in (
        "meshes",
        "renderparams",
        "vertexbuffers",
        "morphbuffers",
        "morphindexbuffers",
        "indexbuffers",
        "lodchildindices",
        "cbufferidx",
    ):
        table: Table = cand[name]  # type: ignore[assignment]
        print(f"  {name:<18} count={table.count:<6} data=prim[{table.data_off}] end={table.end}")
    print(
        f"  numcbuffers={cand['numcbuffers']} cbufferoffset={cand['cbufferoffset']} "
        f"gpudatasize={cand['gpudatasize']}"
    )
    print(
        f"  validation: mesh_refs={cand['valid_mesh_refs']}/{cand['checked_meshes']} "
        f"aabb={cand['plausible_aabbs']}/{cand['checked_meshes']} "
        f"vb={cand['vb_valid']}/{cand['vb_checked']} ib={cand['ib_valid']}/{cand['ib_checked']}"
    )

    meshes: Table = cand["meshes"]  # type: ignore[assignment]
    renderparams: Table = cand["renderparams"]  # type: ignore[assignment]
    vertexbuffers: Table = cand["vertexbuffers"]  # type: ignore[assignment]
    indexbuffers: Table = cand["indexbuffers"]  # type: ignore[assignment]
    for i in range(min(meshes.count, limit)):
        m = meshes.data_off + i * MESH_SIZE
        name_hash = u64(prim, m)
        vbindex = u32(prim, m + 0x08)
        ibindex = u32(prim, m + 0x0C)
        rpidx = u32(prim, m + 0x1C)
        rpn = u32(prim, m + 0x20)
        aabb = f32x6(prim, m + 0x24)
        print(
            f"  mesh[{i:03d}] name={name_hash:016x} vb={vbindex} ib={ibindex} "
            f"rp={rpidx}+{rpn} aabb=({aabb[0]:.3g},{aabb[1]:.3g},{aabb[2]:.3g}).."
            f"({aabb[3]:.3g},{aabb[4]:.3g},{aabb[5]:.3g})"
        )
        if rpidx < renderparams.count:
            rp = renderparams.data_off + rpidx * RP_SIZE
            print(
                f"           rp0 primtype={u32(prim, rp + 0x40)} "
                f"idxstart={u32(prim, rp + 0x44)} idxcount={u32(prim, rp + 0x48)} "
                f"mat={u32(prim, rp + 0x28)} shader={u32(prim, rp + 0x2c)}"
            )
        if vbindex < vertexbuffers.count:
            vb = vertexbuffers.data_off + vbindex * VB_SIZE
            print(
                f"           vb gpu={u32(prim, vb + 0x128)} verts={u32(prim, vb + 0x12c)} "
                f"stride={vertex_stride(prim, vb)} used={u64(prim, vb + 0x120):#x}"
            )
        if ibindex < indexbuffers.count:
            ib = indexbuffers.data_off + ibindex * IB_SIZE
            gpu_off, nidx, indexsize, pad = struct.unpack_from("<4I", prim, ib)
            print(f"           ib gpu={gpu_off} indices={nidx} size={indexsize} pad={pad}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("hash", nargs="?", default=DEFAULT_HASH)
    parser.add_argument("--start", type=int, default=2048)
    parser.add_argument("--end", type=int, default=0)
    parser.add_argument("--limit", type=int, default=12)
    args = parser.parse_args()

    prim_path = ARCHIVE_PRIMARY / args.hash
    gpu_path = ARCHIVE_GPU / args.hash
    prim = load_decompressed(prim_path)
    gpu = load_decompressed(gpu_path)
    expected_gpu_size = len(gpu)
    field1 = u64(prim, 8)

    print(f"resource={args.hash}")
    print(f"primary={prim_path}")
    print(f"gpu={gpu_path}")
    print(f"decompressed primary={len(prim):,} gpu={len(gpu):,} primary.field1={field1:,}")

    scan_end = args.end or len(prim) - 32
    hits = []
    for off in range(args.start, scan_end, 4):
        cand = parse_candidate(prim, off, expected_gpu_size)
        if cand:
            hits.append(cand)

    print(f"\nloader-layout candidates: {len(hits)}")
    for cand in hits[:8]:
        print_candidate(prim, cand, args.limit)
    if len(hits) > 8:
        print(f"\n... {len(hits) - 8} more candidates omitted")


if __name__ == "__main__":
    main()
