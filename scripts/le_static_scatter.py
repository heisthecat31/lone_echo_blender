"""Decode a Lone Echo (Win7) static-scatter master.

Every geometry-bearing LE level bakes ONE populated `CGStaticInstanceResourceWin7`
master holding the bulk environment scatter that `CTransformCR` placement does NOT
enumerate. This recovers its structure.

CORRECTION: the leading stride-16 `vec4`
table is `SGStaticInstanceLODData.nodes` (the LOD hierarchy, the FIRST struct
field), NOT the instance positions. An earlier revision mislabeled it. The real
per-instance data lives in the GPU sibling.

Disk layout = `SGStaticInstancesData` in AttachToStream order. The tail is fixed
(the last 24 bytes are six u32 scalars) and is what this decoder anchors on;
`instancescount`/`instanceoffsets` are found by their (count==num_meshes,
sum==totalinstances) signature and cross-checked as a prefix-sum pair:

    lod.nodes             CTableA<CReal4,16>   [u32 n][12 pad][n*16]  (finite coords, w==0)
    lod.lodfadeslopeoffs  CTableA<CReal4,16>
    lod.hierlods          CTable<SHierLOD=12>  parent/firstchild/numchildren
    lod.nodelookup        CTable<u16>
    lod.totalnumlods      u64
    meshlist              CGMeshListData (inline; num_meshes meshes)
    instancescount        CTable<u32>   per-mesh instance count; sum == totalinstances
    instanceoffsets       CTable<u32>   prefix-sum of instancescount
    irrsamplelocs         CTable<C3Vector=12>   per-mesh
    dirlightmasks/visstrlookup/lodfadelookup/ditherfadeflags  per-instance (len==totalinstances)
    [u32 totalinstances][u32 numvisentries][u32 instancedataoffset][u32 instancedatasize]
    [u32 instancetypedataoffset][u32 instancetypedatasize]   <- the last 24 bytes

station_front (942c829457a04a62) totalinstances=21394,
num_meshes=1050 (instancetypedatasize 37800 = 1050*36), the three tail scalars
partition the master's GPU sub-blob (size 139426128) exactly; min_itc_master
(4c47d84c1e52447a) totalinstances=8616, num_meshes=194.

Per-instance WORLD TRANSFORMS are in the GPU sibling `instancedata` region and
are now DECODED. instance i belongs to mesh m where instanceoffsets[m]
<= i < instanceoffsets[m] + instancescount[m]. Two gotchas the layout hinges on:

  * The tail `instancedataoffset`/`instancetypedataoffset` are RELATIVE to the
    master's GPU resource base G, NOT absolute in the GPU file. G = pos of the
    populated `CGStaticInstanceResourceWin7GPU` blob (name_hash == master;
    size == instancetypedataoffset+instancetypedatasize) in the PRIMARY's GPU
    resource table (header1). The GPU sibling holds many resources (reflection
    probe, meshlist-GPU, 16-B stubs) so its true uncompressed size is much larger
    (station_front 267620540) than this one sub-blob.
  * `instancetypedata` is num_meshes x 36 B (9x u32). Fields +0 block_offset and
    +8 stride are in 4-byte WORDS (x4 for bytes); +4 = firstinstance (==
    instanceoffsets[m]); +24 = totalinstances; +12==2, +28/+32 = bookkeeping.

Each instance record = a fixed 44-byte header (`NRadEngine::SGPackedInstanceData`,
0x2c, `name-confirmed`) then `nverts` x `C2Vector` of BAKED LIGHTMAP UVs
(8 B/vertex), so `stride == 44 + 8*nverts` -- which is why bytes/instance is
non-uniform (station 2513.6 vs min_itc 6543.9) while the header is constant:

  +0x00 pos          C3Vector    3x f32, world
  +0x0C orientation  C4VectorS16N 4x int16 snorm (x,y,z,w) unit quat
  +0x14 scale        C3HVector   3x f16
  +0x1A lightmapidx  uint16      <- the PER-INSTANCE lightmap page (was "pad")
  +0x1C probeidx_lmask_dlmask uint32
  +0x20 color        C4HVector   4x f16
  +0x28 lodfadeidx   uint32
  +0x2C lightmapuvs  C2Vector[nverts]   8 B/vertex, per-instance per-vertex

H2 (full TRS) confirmed: |q|==1 over 302 sampled instances, matching
SGInstanceData (transform C44Matrix). The UV tail and the +0x1a page are
`stream-confirmed` on 942c829457a04a62 (station_front): stride 900/636/844 on
meshes 0/1/468 == 44+8*nverts, and `44*C + 8*sum(count*nverts)` reproduces
`instancedatasize` with residual exactly 0 on station_front AND min_itc.
See docs/LIGHTING.md.

⚠ ERA TRAP: the later engine revision uses a 48-B record and a 4-byte
`C2VectorU16N` UV in a SEPARATE offsets-indexed buffer
(`k_instancelightuvs`/`lmuvoffsetidx`), absent from this era. Never carry one
era's formula to the other.

`decode_static_master`, `decode_instancetype_table`, `decode_instance_transform`
are archive-independent (unit-tested on synthetic blobs); `decode_gpu_transforms`
pulls + decodes real transforms with the OOM-safe ranged reader.

`decode_static_master(blob)` is archive-independent (unit-tested on a synthetic
blob). The CLI pulls the master with the OOM-safe ranged reader.

Run from LE_ROOT under Windows Python:
    python.exe scripts/le_static_scatter.py <archive_hash> [--out scatter.json]
"""
from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass, field
from pathlib import Path

STATIC_TYPE = "CGStaticInstanceResourceWin7"
# Stable resource-type-name hash for CGStaticInstanceResourceWin7. Using the
# constant lets the master be identified WITHOUT a hash_lookup name table, so the
# extractor runs against a bare game-data tree (the table stays an optional
# fallback for name resolution).
STATIC_TYPE_HASH = 0xE83CF7FAAEC4CAB5
EMPTY_MASTER_MAX = 300  # populated masters are >>300 B; empty placeholders are 148 B
_TAIL = 24              # six trailing u32 scalars


@dataclass
class StaticMasterDecode:
    lod_node_count: int                 # leading CTableA<CReal4,16> count (was mislabeled "positions")
    num_instances: int                  # totalinstances (real per-instance count)
    num_meshes: int
    instancescount: list[int]           # per-mesh instance count (sum == num_instances)
    instanceoffsets: list[int]          # prefix-sum of instancescount
    gpu_instancedata: tuple[int, int]   # (offset, size) of the per-instance transforms in the GPU sibling
    gpu_instancetypedata: tuple[int, int]
    warnings: list[str] = field(default_factory=list)

    def mesh_for_instance(self, i: int) -> int:
        # instances are laid out as contiguous per-mesh runs
        lo, hi = 0, len(self.instanceoffsets)
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if self.instanceoffsets[mid] <= i:
                lo = mid
            else:
                hi = mid
        return lo


def _find_binding(blob: bytes, num_meshes: int, total: int):
    """Locate instancescount + instanceoffsets by their count/sum signature.

    Returns (instancescount, instanceoffsets) or (None, None). instancescount is a
    CTable<u32> of `num_meshes` entries summing to `total`; instanceoffsets is the
    immediately-following CTable<u32> equal to the prefix-sum.
    """
    if num_meshes <= 0:
        return None, None
    words = struct.unpack_from("<" + "I" * (len(blob) // 4), blob, 0)
    m = len(words)
    for oi in range(0, m):
        if words[oi] != num_meshes or oi + 1 + num_meshes > m:
            continue
        counts = words[oi + 1: oi + 1 + num_meshes]
        if sum(counts) != total:
            continue
        oo = oi + 1 + num_meshes           # start of the next CTable
        if oo >= m or words[oo] != num_meshes or oo + 1 + num_meshes > m:
            continue
        offs = words[oo + 1: oo + 1 + num_meshes]
        expect = [0]
        for c in counts[:-1]:
            expect.append(expect[-1] + c)
        if list(offs) == expect:
            return list(counts), list(offs)
    return None, None


def decode_static_master(blob: bytes) -> StaticMasterDecode:
    """Decode a populated CGStaticInstanceResourceWin7 master's primary bytes."""
    warnings: list[str] = []
    n = len(blob)
    if n < _TAIL + 16:
        raise ValueError(f"blob too small ({n} B) for a static master")

    lod_node_count = struct.unpack_from("<I", blob, 0)[0]
    (total, numvis, ido, ids, ito, its) = struct.unpack_from("<6I", blob, n - _TAIL)

    if ido + ids != ito:
        warnings.append(f"instancedataoffset+size ({ido + ids}) != instancetypedataoffset ({ito})")
    if its % 36 != 0:
        warnings.append(f"instancetypedatasize {its} not a multiple of 36 (mesh-type stride)")
    num_meshes = its // 36

    counts, offs = _find_binding(blob, num_meshes, total)
    if counts is None:
        warnings.append(f"could not locate instancescount/instanceoffsets (num_meshes={num_meshes}, total={total})")
        counts, offs = [], []

    return StaticMasterDecode(
        lod_node_count=lod_node_count,
        num_instances=total,
        num_meshes=num_meshes,
        instancescount=counts,
        instanceoffsets=offs,
        gpu_instancedata=(ido, ids),
        gpu_instancetypedata=(ito, its),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# GPU-side per-instance transform decode.
# See the module docstring for the two gotchas (G-relative offsets; 4-byte words).
# These three are archive-independent and unit-tested on synthetic blobs.
# ---------------------------------------------------------------------------

@dataclass
class InstanceTypeRecord:
    """One 36-byte `instancetypedata` record (per mesh-type), byte-normalized."""
    block_offset: int    # BYTE offset of this mesh-type's block into instancedata (raw +0 x4)
    first_instance: int  # global instance start index == instanceoffsets[m] (raw +4)
    stride: int          # per-instance record stride in BYTES (raw +8 x4)
    num_instances: int   # global totalinstances, constant across records (raw +24)
    raw: tuple = ()      # all 9 u32s for audit (+12==2, +28/+32 bookkeeping)


def decode_instancetype_table(itd: bytes, num_meshes: int) -> list[InstanceTypeRecord]:
    """Parse `instancetypedata` (num_meshes x 36 B, 9x u32 LE) to byte-normalized records.

    `block_offset`/`stride` are converted from 4-byte words to bytes here so callers
    index `instancedata` directly.
    """
    if len(itd) < num_meshes * 36:
        raise ValueError(f"instancetypedata {len(itd)} B < num_meshes*36 ({num_meshes * 36})")
    recs = []
    for m in range(num_meshes):
        r = struct.unpack_from("<9I", itd, m * 36)
        recs.append(InstanceTypeRecord(
            block_offset=r[0] * 4, first_instance=r[1], stride=r[2] * 4,
            num_instances=r[6], raw=r))
    return recs


@dataclass
class InstanceTransform:
    translation: tuple  # (x, y, z) world-space, from 3x f32
    rotation: tuple     # (x, y, z, w) unit quaternion, from 4x int16 snorm
    scale: tuple        # (sx, sy, sz), from 3x f16


def decode_instance_transform(buf: bytes, off: int = 0) -> InstanceTransform:
    """Decode the transform part of one `SGPackedInstanceData` record.

    +0x00 translation 3xf32; +0x0C rotation 4x int16 snorm (/32767, order x,y,z,w);
    +0x14 scale 3x f16. +0x1A is NOT padding -- it is `lightmapidx` (see
    `instance_lightmap_page`) -- and the record continues to +0x2C, after which the
    per-instance lightmap UV array runs to the end of `stride`.
    """
    t = struct.unpack_from("<3f", buf, off)
    q = tuple(v / 32767.0 for v in struct.unpack_from("<4h", buf, off + 12))
    s = struct.unpack_from("<3e", buf, off + 20)
    return InstanceTransform(translation=t, rotation=q, scale=s)


# ---------------------------------------------------------------------------
# Per-instance BAKED LIGHTMAP data (page + per-vertex UVs).
# `stream-confirmed` on 942c829457a04a62 / 4c47d84c1e52447a; see the module
# docstring and the project documentation.
# Archive-independent: everything below takes an already-decompressed
# `instancedata` region, so it is unit-tested on synthetic bytes.
# ---------------------------------------------------------------------------

#: `sizeof(NRadEngine::SGPackedInstanceData)` -- the fixed part of one record.
INSTANCE_HEADER_BYTES = 0x2C            # 44
#: `sizeof(C2Vector)` -- one vertex's lightmap UV pair (2x f32).  ⛔ Echo r15 uses
#: `C2VectorU16N` (4 B) instead; do not carry this constant across eras.
INSTANCE_UV_BYTES = 8
#: `SGPackedInstanceData.lightmapidx` -- the per-instance lightmap PAGE.
INSTANCE_LIGHTMAPIDX_OFF = 0x1A
#: where the `C2Vector lightmapuvs[vtxcount]` array starts inside a record.
INSTANCE_UV_OFF = INSTANCE_HEADER_BYTES
#: `SGStaticInstanceTypeData.vertexsize`, in DWORDS. 2 dwords == 8 B == C2Vector.
EXPECTED_VERTEXSIZE_DWORDS = INSTANCE_UV_BYTES // 4


def instance_uv_count(stride: int) -> int:
    """How many `C2Vector` UV pairs one instance record of `stride` bytes holds.

    Raises when the stride is not `44 + 8*n` -- a mis-strided read must be loud,
    because it silently produces plausible-looking floats otherwise.
    """
    tail = int(stride) - INSTANCE_HEADER_BYTES
    if tail < 0 or tail % INSTANCE_UV_BYTES:
        raise ValueError(
            f"instance stride {stride} is not {INSTANCE_HEADER_BYTES} + "
            f"{INSTANCE_UV_BYTES}*n -- wrong record model or wrong table")
    return tail // INSTANCE_UV_BYTES


def instance_lightmap_page(buf: bytes, off: int = 0) -> int:
    """`SGPackedInstanceData.lightmapidx` (u16 @ +0x1a) of the record at `off`.

    ⛔ This is the page an INSTANCED draw uses -- NOT `CGMeshData.lmsliceindex`.
    Measured on all 21,394 station_front instances they agree 7,444 / disagree
    13,909 (65.1%). `stream-confirmed`.
    """
    return struct.unpack_from("<H", buf, off + INSTANCE_LIGHTMAPIDX_OFF)[0]


@dataclass
class InstanceLightmap:
    """Per-instance baked lightmap stream, in GLOBAL instance order.

    `uv_bytes` is the concatenation of every emitted instance's UV array, copied
    VERBATIM off disk (the on-disk element is already little-endian float32 x2,
    so no unpack/repack happens and nothing is rounded). `offsets[i]` is where
    instance `i`'s UVs start **in PAIRS** (multiply by 8 for bytes), `counts[i]`
    is how many pairs it owns (== that mesh-type's vertex count), `pages[i]` is
    its `lightmapidx`.
    """
    count: int = 0                        # instances described
    uv_bytes: bytearray = field(default_factory=bytearray)
    offsets: list = field(default_factory=list)   # start index, in UV PAIRS
    counts: list = field(default_factory=list)    # UV pairs per instance
    pages: list = field(default_factory=list)     # u16 lightmapidx, widened
    total_uv_pairs: int = 0
    #: `44*count + 8*sum(counts)` over EVERY instance of the master (not just the
    #: emitted subset) -- compare against `instancedatasize` for a residual.
    predicted_instancedatasize: int = 0
    warnings: list = field(default_factory=list)

    def page_histogram(self) -> dict:
        hist: dict = {}
        for p in self.pages:
            hist[p] = hist.get(p, 0) + 1
        return dict(sorted(hist.items()))


def decode_instance_lightmap(region: bytes, decode: "StaticMasterDecode",
                             typetable: list, *,
                             nverts_by_mesh: dict | None = None,
                             selected: set | None = None,
                             max_instances: int | None = None) -> InstanceLightmap:
    """Pull per-instance lightmap page + UVs out of a decompressed `instancedata`.

    `region`         : the decompressed `instancedata` bytes (offset 0 == the
                       region base the typetable's `block_offset` indexes into).
    `nverts_by_mesh` : optional {mesh index -> decoded vertex count}. When given,
                       a mesh whose `stride`-derived UV count disagrees is
                       reported in `warnings` -- the Echo-era runtime raises
                       exactly this mismatch, so it is worth surfacing.
    `selected`       : optional set of mesh indices to emit (mirrors the scene
                       extractor's `--subset`). Instances of other meshes are
                       skipped, keeping the arrays PARALLEL to `instances.bin`.

    GLOBAL instance order is preserved because `instanceoffsets` is the prefix
    sum of `instancescount`: iterating mesh-types in index order and their
    instances in local order visits global instance 0, 1, 2, ... exactly.
    """
    out = InstanceLightmap()
    total_all = 0
    for m in range(decode.num_meshes):
        rec = typetable[m]
        n_inst = decode.instancescount[m] if m < len(decode.instancescount) else 0
        try:
            uvn = instance_uv_count(rec.stride)
        except ValueError as exc:
            out.warnings.append(f"mesh {m}: {exc}")
            uvn = 0
        if len(rec.raw) > 3 and rec.raw[3] != EXPECTED_VERTEXSIZE_DWORDS:
            out.warnings.append(
                f"mesh {m}: instancetypedata vertexsize {rec.raw[3]} dwords != "
                f"{EXPECTED_VERTEXSIZE_DWORDS} (C2Vector)")
        if nverts_by_mesh is not None:
            want = nverts_by_mesh.get(m)
            if want is not None and uvn and want != uvn:
                out.warnings.append(
                    f"mesh {m}: stride implies {uvn} lightmap UVs but the mesh "
                    f"decodes {want} vertices")
        total_all += n_inst * (INSTANCE_HEADER_BYTES + INSTANCE_UV_BYTES * uvn)
        if selected is not None and m not in selected:
            continue
        nbytes = uvn * INSTANCE_UV_BYTES
        for j in range(n_inst):
            if max_instances is not None and out.count >= max_instances:
                break
            off = instance_record_offset(rec, j)
            out.offsets.append(out.total_uv_pairs)
            out.counts.append(uvn)
            out.pages.append(instance_lightmap_page(region, off))
            if nbytes:
                out.uv_bytes += region[off + INSTANCE_UV_OFF:
                                       off + INSTANCE_UV_OFF + nbytes]
            out.total_uv_pairs += uvn
            out.count += 1
    out.predicted_instancedatasize = total_all
    return out


def instance_record_offset(rec: InstanceTypeRecord, local_index: int) -> int:
    """BYTE offset into instancedata of the `local_index`-th instance of a mesh-type."""
    return rec.block_offset + local_index * rec.stride


def load_master_blob(archive_hash: str, hash_lookup: Path = Path("hash_lookup.json")):
    """OOM-safe: pull the populated static master out of one archive primary.

    Returns (master_name_hash, blob) or (None, None) if the archive bakes only
    empty placeholders (geometry-light interiors).
    """
    from le_oodle import chunk_table, decompress_range
    from le_archive_decode import (
        ARCHIVE_PRIMARY, parse_header, entry_at, load_hash_lookup,
    )

    names = load_hash_lookup(hash_lookup)
    raw = (ARCHIVE_PRIMARY / archive_hash).read_bytes()
    uncomp_total, _ = chunk_table(raw)
    prelude = decompress_range(raw, 0, 64)
    primary_size = struct.unpack_from("<Q", prelude, 0)[0]
    extra_skip = struct.unpack_from("<Q", prelude, 24)[0]
    data_off = 32 + extra_skip
    header0_off = data_off + primary_size

    tail = decompress_range(raw, header0_off, uncomp_total)
    h0 = parse_header(tail, 0)
    type_hashes = {struct.unpack_from("<Q", tail, h0.contents.off + i * 24)[0]
                   for i in range(h0.contents.count)}
    # Identify the static-instance type by its known type-hash constant (no
    # hash_lookup needed); fall back to name resolution if a table was supplied.
    type_hash = (STATIC_TYPE_HASH if STATIC_TYPE_HASH in type_hashes
                 else next((th for th in type_hashes
                            if names.get(th) == STATIC_TYPE), None))
    if type_hash is None:
        del raw, tail
        return None, None

    best = None
    for i in range(h0.contents.count):
        th, name_hash, val = struct.unpack_from("<QQQ", tail, h0.contents.off + i * 24)
        if th != type_hash or val >= h0.entries.count:
            continue
        pos, size = entry_at(tail, h0, val)
        if size > EMPTY_MASTER_MAX and (best is None or size > best[2]):
            best = (name_hash, pos, size)
    del tail
    if best is None:
        del raw
        return None, None

    name_hash, pos, size = best
    abs_off = data_off + pos
    blob = decompress_range(raw, abs_off, abs_off + size)
    del raw
    return name_hash, blob


@dataclass
class GpuInstanceDecode:
    """What one pass over the GPU `instancedata` region yields."""
    G: int                              # the master's GPU resource base
    typetable: list                     # list[InstanceTypeRecord]
    transforms: list                    # list[InstanceTransform], global order
    lightmap: InstanceLightmap | None = None   # only when want_lightmap


def decode_gpu_instances(archive_hash: str, master_name_hash: int,
                         decode: StaticMasterDecode,
                         hash_lookup: Path = Path("hash_lookup.json"),
                         max_instances: int | None = None, *,
                         want_lightmap: bool = False,
                         nverts_by_mesh: dict | None = None,
                         selected: set | None = None) -> GpuInstanceDecode:
    """One pass over the GPU `instancedata` region -> transforms (+ lightmap).

    OOM-safe in the same way `decode_gpu_transforms` always was: reads only the
    primary tail (to resolve the GPU base G from the GPU resource table /
    header1) plus the [instancedata|instancetypedata] window of the GPU sibling,
    and drops both as soon as they are consumed.

    ⚠ `want_lightmap=True` accumulates ~8 B per instance-vertex -- 50.4 MiB on
    station_front. It is opt-in for exactly that reason.
    """
    from le_oodle import chunk_table, decompress_range
    from le_archive_decode import (
        ARCHIVE_PRIMARY, ARCHIVE_GPU, parse_header, entry_at,
    )

    ido, ids = decode.gpu_instancedata
    ito, its = decode.gpu_instancetypedata

    # --- resolve G from the primary's GPU resource table (header1) ---
    raw = (ARCHIVE_PRIMARY / archive_hash).read_bytes()
    uncomp_total, _ = chunk_table(raw)
    prelude = decompress_range(raw, 0, 64)
    primary_size = struct.unpack_from("<Q", prelude, 0)[0]
    extra_skip = struct.unpack_from("<Q", prelude, 24)[0]
    tail = decompress_range(raw, 32 + extra_skip + primary_size, uncomp_total)
    del raw
    h0 = parse_header(tail, 0)
    h1 = parse_header(tail, h0.end)
    G = None
    for i in range(h1.contents.count):
        _, name_hash, val = struct.unpack_from("<QQQ", tail, h1.contents.off + i * 24)
        if name_hash != master_name_hash or val >= h1.entries.count:
            continue
        gpos, gsize = entry_at(tail, h1, val)
        if gsize == ito + its:      # the populated master GPU blob, not a 16-B stub
            G = gpos
            break
    del tail
    if G is None:
        raise ValueError(
            f"no populated GPU static blob (size {ito + its}) for master "
            f"{master_name_hash:016x} in {archive_hash}")

    # --- ranged read of [instancedata | instancetypedata] (contiguous, ito==ido+ids) ---
    gpu_raw = (ARCHIVE_GPU / archive_hash).read_bytes()
    region = decompress_range(gpu_raw, G + ido, G + ito + its)
    del gpu_raw
    typetable = decode_instancetype_table(region[ids:ids + its], decode.num_meshes)

    n = decode.num_instances
    if max_instances is not None:
        n = min(n, max_instances)
    transforms = []
    for i in range(n):
        m = decode.mesh_for_instance(i)
        off = instance_record_offset(typetable[m], i - decode.instanceoffsets[m])
        transforms.append(decode_instance_transform(region, off))

    instlm = None
    if want_lightmap:
        instlm = decode_instance_lightmap(
            region, decode, typetable, nverts_by_mesh=nverts_by_mesh,
            selected=selected, max_instances=max_instances)
    del region
    return GpuInstanceDecode(G=G, typetable=typetable, transforms=transforms,
                             lightmap=instlm)


def decode_gpu_transforms(archive_hash: str, master_name_hash: int,
                          decode: StaticMasterDecode,
                          hash_lookup: Path = Path("hash_lookup.json"),
                          max_instances: int | None = None):
    """Back-compat wrapper: (G, typetable, transforms). See `decode_gpu_instances`."""
    d = decode_gpu_instances(archive_hash, master_name_hash, decode,
                             hash_lookup, max_instances)
    return d.G, d.typetable, d.transforms


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("hash")
    ap.add_argument("--hash-lookup", type=Path, default=Path("hash_lookup.json"))
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--gpu", action="store_true",
                    help="also decode per-instance world transforms from the GPU sibling")
    args = ap.parse_args()

    name_hash, blob = load_master_blob(args.hash, args.hash_lookup)
    if blob is None:
        print(f"archive {args.hash}: no populated static master (empty placeholders only)")
        return

    d = decode_static_master(blob)
    print(f"archive {args.hash}  master={name_hash:016x}  size={len(blob)} B")
    print(f"  num_instances(total)={d.num_instances}  num_meshes={d.num_meshes}  "
          f"lod_nodes={d.lod_node_count}")
    print(f"  binding: instancescount[{len(d.instancescount)}] sum="
          f"{sum(d.instancescount)}  offsets[:6]={d.instanceoffsets[:6]}")
    print(f"  GPU instancedata=off{d.gpu_instancedata[0]} sz{d.gpu_instancedata[1]}")
    for w in d.warnings:
        print(f"  WARN: {w}")

    gpu_summary = None
    if args.gpu:
        import math
        G, typetable, transforms = decode_gpu_transforms(
            args.hash, name_hash, d, args.hash_lookup)
        qns = [math.sqrt(sum(c * c for c in t.rotation)) for t in transforms]
        xs = [t.translation[0] for t in transforms]
        ys = [t.translation[1] for t in transforms]
        zs = [t.translation[2] for t in transforms]
        print(f"  GPU base G={G}  decoded {len(transforms)} transforms")
        print(f"    |q| in [{min(qns):.5f}, {max(qns):.5f}]  (H2: full TRS)")
        print(f"    translation bbox x[{min(xs):.2f},{max(xs):.2f}] "
              f"y[{min(ys):.2f},{max(ys):.2f}] z[{min(zs):.2f},{max(zs):.2f}]")
        t0 = transforms[0]
        print(f"    inst0 T={tuple(round(v, 3) for v in t0.translation)} "
              f"Q={tuple(round(v, 3) for v in t0.rotation)} "
              f"S={tuple(round(v, 3) for v in t0.scale)}")
        gpu_summary = {
            "gpu_base_G": G,
            "decoded_transforms": len(transforms),
            "quat_norm_min": min(qns), "quat_norm_max": max(qns),
            "translation_bbox": {"min": [min(xs), min(ys), min(zs)],
                                 "max": [max(xs), max(ys), max(zs)]},
            "sample": [{"i": i,
                        "translation": list(transforms[i].translation),
                        "rotation": list(transforms[i].rotation),
                        "scale": list(transforms[i].scale)}
                       for i in (0, len(transforms) // 2, len(transforms) - 1)],
        }

    if args.out:
        payload = {
            "format": "le_static_scatter",
            "version": 3,
            "master": f"{name_hash:016x}",
            "num_instances": d.num_instances,
            "num_meshes": d.num_meshes,
            "lod_node_count": d.lod_node_count,
            "instancescount": d.instancescount,
            "instanceoffsets": d.instanceoffsets,
            "gpu_instancedata": {"offset": d.gpu_instancedata[0], "size": d.gpu_instancedata[1]},
            "gpu_instancetypedata": {"offset": d.gpu_instancetypedata[0], "size": d.gpu_instancetypedata[1]},
            "transforms": (gpu_summary if gpu_summary else
                           "decode with --gpu / decode_gpu_transforms(); per-instance 26-B "
                           "TRS header (f32 translation, int16-snorm quat, f16 scale) in the "
                           "GPU sibling instancedata region (H2, full TRS)"),
            "warnings": d.warnings,
        }
        args.out.write_text(json.dumps(payload), encoding="utf-8")
        print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()
