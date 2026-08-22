"""Decode a model's geometry from its OWN tables, not by pattern-scanning.

## Why

`decode.extract_mesh` finds meshes by scanning the GPU blob for runs of
plausible vertex records. It works for most models but fails silently and
badly on others -- and it fails in ways that look like texture bugs:

* `c48412e86560721e` declares **10 meshes**; the scanner returned **2**, one of
  them 7453 vertices with 32 faces (thousands of orphaned vertices).
* `9033ab9ab066393e` declares 2 draws; the scanner returned 1.
* Nine models in `d09afd15b1c75c04` produce no geometry at all.

The model already states its layout. `CGInstancedModelResourceWin10` /
`CGMeshListResourceWin10` open with ten 56-byte `CTable` headers whose counts
are exact, and carry:

    vertexbuffers  336B/rec   base_offset@0x128  stream0_size@0x130
                              vertex_count@0x13C (== @0x140)
    indexbuffers    16B/rec   offset, numindices, index_kind (2 or 4), pad

GPU layout per mesh, from the decoder's own described path:

    stream0 (uv/colour/etc) at base_offset, stride = stream0_size/vertex_count
    stream1 (positions)     at base_offset + stream0_size, stride 28
    indices                 at indexbuffers.offset, `kind` bytes each

## Locating the arrays

The header counts are trustworthy; the array OFFSETS a naive header walk
predicts are not (`ff5afb4e96897159`: headers imply 0x788, the real base is
0x8D8). So each array is found by scanning for the offset where every record
satisfies its own invariants -- vertex counts non-zero and self-consistent
(`@0x13C == @0x140`), `stream0_size` divisible by the count, and every byte
range inside the GPU blob. Wrong offsets fail these immediately.

Run standalone to compare against the current decoder:

    python scripts/evr_structural_decode.py c48412e86560721e
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evr_resource_types import (
    INSTANCED_MODEL_RESOURCE,
    MESH_LIST_RESOURCE,
    normalise_hash,
    resource_path,
)

CTABLE_HEADER = 0x38
VB_STRIDE = 336
VB_BASE_OFFSET = 0x128
VB_STREAM0_SIZE = 0x130
VB_VERTEX_COUNT = 0x13C
VB_VERTEX_COUNT_2 = 0x140
IB_STRIDE = 16
POSITION_STRIDE = 28          # stream-1 record: xyz + normal/tangent packing
UV_OFFSET = 8                 # float2 UV0 inside stream 0 (see docs §7.7)


def _uv_offset(gpu, base, count, stride):
    """`UV_OFFSET`, but VALIDATED against the vertices actually present.

    +8 is right for stride 20 and 28 and WRONG for stride 16 and 24 (+4 on
    both). Reading half a vertex late yields finite nonsense -- 1.1e38 on the
    arena's rules panel -- which no `isfinite` check catches. Defers to
    `evr_mesh_importer.decode.uv_stream_offset`, which does the probing; falls
    back to the constant when this module is used standalone without it.
    """
    try:
        from decode import uv_stream_offset
    except ImportError:
        return UV_OFFSET
    return uv_stream_offset(gpu, base, count, stride, UV_OFFSET)

#: Header slot -> the array it counts.
HDR_MESHES, HDR_RENDERPARAMS, HDR_VERTEXBUFFERS, HDR_INDEXBUFFERS = 0, 1, 2, 5


def table_counts(primary: bytes) -> list:
    """`iused` for each of the ten leading `CTable` headers."""
    counts = []
    cursor = 0
    while cursor + CTABLE_HEADER <= len(primary) and len(counts) < 10:
        ptr, size, z10 = struct.unpack_from("<QQQ", primary, cursor)
        z18, _flags = struct.unpack_from("<II", primary, cursor + 0x18)
        mark, total, iused = struct.unpack_from("<QQQ", primary, cursor + 0x20)
        if ptr or z10 or z18 or mark not in (0, 32) or total > 100000:
            break
        counts.append(iused)
        cursor += CTABLE_HEADER
    return counts


def find_vertex_buffers(primary: bytes, gpu: bytes, count: int) -> list:
    """`[(base_offset, stream0_size, vertex_count, stride), ...]` or `[]`."""
    if not count:
        return []
    for base in range(0, max(0, len(primary) - VB_STRIDE * count) + 1, 4):
        records = []
        for k in range(count):
            rec = base + k * VB_STRIDE
            if rec + VB_VERTEX_COUNT_2 + 4 > len(primary):
                records = []
                break
            offset = struct.unpack_from("<I", primary, rec + VB_BASE_OFFSET)[0]
            stream0 = struct.unpack_from("<I", primary, rec + VB_STREAM0_SIZE)[0]
            vcount = struct.unpack_from("<I", primary, rec + VB_VERTEX_COUNT)[0]
            vcount2 = struct.unpack_from("<I", primary, rec + VB_VERTEX_COUNT_2)[0]
            if (not vcount or vcount != vcount2 or not stream0
                    or vcount > 200000 or stream0 % vcount
                    or offset + stream0 > len(gpu)):
                records = []
                break
            records.append((offset, stream0, vcount, stream0 // vcount))
        if records:
            return records
    return []


def find_index_buffers(primary: bytes, gpu: bytes, count: int) -> list:
    """`[(offset, numindices, kind), ...]` or `[]`."""
    if not count:
        return []
    for base in range(0, max(0, len(primary) - IB_STRIDE * count) + 1, 4):
        records = []
        for k in range(count):
            rec = base + k * IB_STRIDE
            if rec + IB_STRIDE > len(primary):
                records = []
                break
            offset, numindices, kind, pad = struct.unpack_from("<4I", primary, rec)
            if (pad or kind not in (2, 4) or not numindices
                    or numindices > 400000
                    or offset + numindices * kind > len(gpu)):
                records = []
                break
            records.append((offset, numindices, kind))
        if records:
            return records
    return []


def decode(root: Path, model_hash) -> tuple:
    """`(submeshes, note)` where each submesh is `(verts, faces, uvs)`."""
    primary_path = (resource_path(root, INSTANCED_MODEL_RESOURCE, model_hash)
                    or resource_path(root, MESH_LIST_RESOURCE, model_hash))
    if primary_path is None:
        return [], "no primary"
    from evr_resource_types import find_mesh_and_primary
    gpu_path, _ = find_mesh_and_primary(root, model_hash)
    if gpu_path is None:
        return [], "no GPU blob"

    primary = primary_path.read_bytes()
    gpu = gpu_path.read_bytes()
    counts = table_counts(primary)
    if len(counts) <= HDR_INDEXBUFFERS:
        return [], "header walk failed"

    n_vb = counts[HDR_VERTEXBUFFERS]
    n_ib = counts[HDR_INDEXBUFFERS]
    vbs = find_vertex_buffers(primary, gpu, n_vb)
    ibs = find_index_buffers(primary, gpu, n_ib)
    if not vbs:
        return [], f"vertex-buffer table ({n_vb}) not located"
    if not ibs:
        return [], f"index-buffer table ({n_ib}) not located"
    if len(vbs) != len(ibs):
        return [], f"{len(vbs)} vertex buffers vs {len(ibs)} index buffers"

    out = []
    for (base, stream0, vcount, stride), (ioff, nidx, kind) in zip(vbs, ibs):
        position_start = base + stream0
        if position_start + vcount * POSITION_STRIDE > len(gpu):
            continue
        verts = [struct.unpack_from("<fff", gpu, position_start + j * POSITION_STRIDE)
                 for j in range(vcount)]

        uvs = None
        if stride >= UV_OFFSET + 8:
            _uv = _uv_offset(gpu, base, vcount, stride)
            uvs = [struct.unpack_from("<ff", gpu, base + j * stride + _uv)
                   for j in range(vcount)]

        fmt = "<H" if kind == 2 else "<I"
        raw = [struct.unpack_from(fmt, gpu, ioff + k * kind)[0] for k in range(nidx)]
        faces = []
        for k in range(0, len(raw) - len(raw) % 3, 3):
            a, b, c = raw[k:k + 3]
            if a >= vcount or b >= vcount or c >= vcount:
                continue
            if a == b or b == c or a == c:
                continue          # degenerate: strip padding
            faces.append((a, b, c))
        out.append((verts, faces, uvs))
    return out, f"{len(out)} mesh(es) from the model's own tables"


def main(argv) -> int:
    import evr_scene_extract as extractor

    import evr_paths
    root = evr_paths.require_extract(None)
    for model_hash in argv or ["c48412e86560721e"]:
        model_hash = normalise_hash(model_hash)
        current, path_label = extractor._decode_model_cached(root, model_hash)
        structural, note = decode(root, model_hash)
        print(f"\n=== {model_hash} ===")
        print(f"  current decoder ({path_label}): {len(current or [])} submesh(es)")
        for i, rg in enumerate(current or []):
            print(f"     #{i} v={len(rg[0]):6d} f={len(rg[1]):6d}")
        print(f"  structural: {note}")
        for i, (verts, faces, _uv) in enumerate(structural):
            print(f"     #{i} v={len(verts):6d} f={len(faces):6d}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
