"""Static-scatter SCENE extractor -> a renderable `.lescatter` package.

Ties together the three solved pieces of the Lone Echo (Win7) static-scatter
pipeline and writes the pinned `.lescatter` package the importer consumes:

  * `le_static_scatter` decodes the populated `CGStaticInstanceResourceWin7`
    master (num_meshes / num_instances / per-mesh instancescount+offsets) and the
    per-instance world TRS transforms out of the GPU sibling.
  * `le_meshlist_decode.parse_candidate` locates the INLINE
    `CGMeshListData` stream inside the master primary and yields its four sub-table
    descriptors (meshes / renderparams / vertexbuffers / indexbuffers).
  * `blender_tool/le_mesh` (`vertex_format`, `meshlist`) decodes each mesh's
    vertex/index buffers.

GEOMETRY SOURCE (station_front 942c829457a04a62):
the 1050 inline meshes' vertex/index buffers are NOT standalone resources -- they
live in the LEADING region of the master GPU blob. With G = the master GPU base
(from `le_static_scatter.decode_gpu_transforms`) and `instancedataoffset` = ido
from the master tail, the master GPU blob is laid out

    [ G+0 .. G+ido )   vertex + index buffers   (the inline-meshlist geometry)
    [ G+ido .. G+ito ) instancedata             (per-instance TRS)
    [ G+ito .. G+ito+its ) instancetypedata

The `CGVertexBufferData.offset` / `CGIndexBufferData.offset` fields are G-RELATIVE
(0-based into the master GPU blob). On station_front: max vb-end =
85,611,856 and max ib-end = 85,612,360 == ido exactly, and every sampled decoded
mesh's position bbox equals its stored CGMeshData AABB bit-exactly (the M1 corpus
invariant). So decoding reads only the [G, G+ido) window (~85 MiB, held once).

The `write_package` writer + its dataclasses are archive-free (pure stdlib) so the
package format is unit tested on synthetic meshes without any game files/Oodle.

Run the extractor with Windows Python from LE_ROOT (Oodle needs the game DLL):
    python.exe scripts/le_scene_extract.py <archive_hash> --out <dir> [--subset N]
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

# Make both `scripts/` and `blender_tool/` importable whether run as the CLI from
# LE_ROOT or imported by the test harness (which already adds blender_tool).
_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Package data model + writer  (archive-free / pure stdlib -> unit testable)
# ---------------------------------------------------------------------------

PACKAGE_FORMAT = "le_scatter"
#: v1 base · v2 per-mesh `draws` · v3 `blobs/instance_lod.bin`
#: v4 per-mesh `uv1` blob + the three `CGMeshData` lightmap ids
#:    (`lightmap_index`/`lm_slice_index`/`numlobes`) + the master's
#:    `lightmap` resource binding.
#: v5 (this) the `instance_lightmap` section: the PER-INSTANCE baked lightmap
#:    stream (page + per-vertex UVs) read out of `SGPackedInstanceData`.
#:    ⛔ This, not the per-mesh `uv1` blob, is the level's lightmap UV input for
#:    instanced statics -- 1046 of station_front's 1050 `uv1` blobs are entirely
#:    zero because the engine overrides that slot per instance
#:    (docs/LIGHTING.md §8.3). The section is OPT-IN
#:    (`--instance-lightmap`) because it is ~52 MB on station_front.
#: Purely ADDITIVE at every step: every v1..v4 key keeps its name, type and byte
#: layout, so an older reader loads a v5 package unchanged and a v5 reader loads
#: an older one (the new keys are absent, and absent means "no lightmap UV / no
#: lightmap id / not extracted", never a different value).
PACKAGE_VERSION = 5

#: `CGMeshData.lightmapindex @0x6C` / `lmsliceindex @0x70` sentinel.
LIGHTMAP_NONE = 0xFFFFFFFF


@dataclass
class SceneMesh:
    """One emitted mesh-type. Geometry arrays are FLAT, row-major, GAME space."""
    index: int                       # ORIGINAL global mesh index m (matches instance mesh_index)
    name_hash: int
    matidx: int                      # draw[0] material index (back-compat top-level)
    shdidx: int                      # draw[0] shaderset index (back-compat top-level)
    aabb_min: tuple                  # (x, y, z)
    aabb_max: tuple
    instance_offset: int             # instanceoffsets[m]
    instance_count: int              # instancescount[m]
    positions: list                 # flat f32: x,y,z * nverts
    indices: list                   # u32 * nindices
    normals: list | None = None     # flat f32: x,y,z * nverts (optional)
    uv0: list | None = None         # flat f32: u,v   * nverts (optional)
    proxy: bool = False
    # v2: EVERY draw (CGRenderParams) of this mesh, in renderparam order. Each is
    # {"matidx","shdidx","idx_start","idx_count"}; idx_start/idx_count are
    # MESH-RELATIVE positions into this mesh's own `indices` (see le_mesh.meshlist
    # / mesh_builder). draws[0] mirrors the top-level (matidx, shdidx).
    draws: list = field(default_factory=list)
    # --- v4: the baked-lightmap inputs -------------------------------------
    # `uv1` is the LIGHTMAP UV set (stride-44 layout: uv1 = u16n x2 @0x18,
    # `stream-confirmed`, README "Confirmed format facts"). Same flat
    # f32 u,v * nverts layout and same optional-key convention as `uv0`.
    # Without it no baked lightmap can ever be sampled on a level render.
    uv1: list | None = None
    # `CGMeshData.lightmapindex @0x6C` — which row of the bound
    # CGLightMapResourceWin7 table this mesh reads (0xFFFFFFFF == unlit).
    lightmap_index: int = LIGHTMAP_NONE
    # `CGMeshData.lmsliceindex @0x70` — the PAGE within that row's texture array.
    lm_slice_index: int = LIGHTMAP_NONE
    # `CGMeshData.numlobes @0x74` — SG lobe count of the bake (4 on 1221/1221
    # shipped meshes; the colour array is 5 slices per page, and which of
    # "4 + 1 extra" / "5-lobe bake" is right is `unresolved` — le_mesh/lightmap.py).
    numlobes: int = 0

    @property
    def nverts(self) -> int:
        return len(self.positions) // 3

    @property
    def nindices(self) -> int:
        return len(self.indices)


@dataclass
class SceneInstance:
    """One placed instance in GLOBAL order."""
    mesh_index: int                  # references SceneMesh.index
    translation: tuple               # (x, y, z)   f32
    rotation: tuple                  # (x, y, z, w) unit quat
    scale: tuple                     # (sx, sy, sz) f32
    # v3 LOD binding (see le_mesh.static_lod). `lod_group` is the LOD-group /
    # node id, `lod_level` this instance's level within it, `lod_group_levels`
    # how many levels that group has (so a consumer can clamp without a group
    # table). Defaults describe a group of one level -- i.e. no LOD.
    lod_group: int = -1
    lod_level: int = 0
    lod_group_levels: int = 1


def _pack_f32(flat) -> bytes:
    return struct.pack(f"<{len(flat)}f", *flat)


def _pack_u32(flat) -> bytes:
    return struct.pack(f"<{len(flat)}I", *flat)


def _levels_histogram(instances: list[SceneInstance]) -> dict:
    """{"<level>": instance count} — a cheap manifest-level sanity readout."""
    hist: dict = {}
    for inst in instances:
        key = str(int(inst.lod_level))
        hist[key] = hist.get(key, 0) + 1
    return dict(sorted(hist.items(), key=lambda kv: int(kv[0])))


def _lightmap_stats(meshes: list[SceneMesh]) -> dict:
    """Manifest-level readout of the v4 lightmap ids (cheap sanity check)."""
    lit = [m for m in meshes if m.lightmap_index != LIGHTMAP_NONE]
    slices = sorted({m.lm_slice_index for m in lit
                     if m.lm_slice_index != LIGHTMAP_NONE})
    lobes = sorted({m.numlobes for m in lit})
    return {
        "meshes_lightmapped": len(lit),
        "meshes_unlit": len(meshes) - len(lit),
        "meshes_with_uv1": sum(1 for m in meshes if m.uv1),
        "slice_indices": slices,
        "numlobes_values": lobes,
    }


#: v5 `instance_lightmap` blob names. Fixed (not per-mesh) because the stream is
#: one flat array in GLOBAL instance order.
INSTLM_UV_BLOB = "blobs/instance_lm_uv.bin"
INSTLM_OFFSETS_BLOB = "blobs/instance_lm_uvoff.bin"
INSTLM_COUNTS_BLOB = "blobs/instance_lm_count.bin"
INSTLM_PAGE_BLOB = "blobs/instance_lm_page.bin"

#: what goes in `instance_lightmap.reason` when the section is absent.
INSTLM_REASON_OFF = ("not extracted: pass --instance-lightmap (the stream is "
                     "~52 MB on station_front and breaks mesh-datablock sharing)")


def _instance_lightmap_section(out_dir: Path, instlm, *,
                               instancedatasize: int | None = None,
                               subset: bool = False) -> dict:
    """Write the four v5 blobs and return the `instance_lightmap` manifest dict.

    ⚠ ORDER INVARIANT: `offsets`/`counts`/`page` are PARALLEL to
    `blobs/instances.bin` — index `i` is the same instance `read_instances()[i]`
    is. `uv_blob` is their concatenation in that same order.

    `flip_v_applied` is always **False**: the UVs are copied verbatim off disk,
    exactly as `uv0`/`uv1` are, and flipping V is the consumer's job.
    """
    if instlm is None:
        return {"present": False, "reason": INSTLM_REASON_OFF}
    (out_dir / INSTLM_UV_BLOB).write_bytes(bytes(instlm.uv_bytes))
    (out_dir / INSTLM_OFFSETS_BLOB).write_bytes(_pack_u32(instlm.offsets))
    (out_dir / INSTLM_COUNTS_BLOB).write_bytes(_pack_u32(instlm.counts))
    (out_dir / INSTLM_PAGE_BLOB).write_bytes(_pack_u32(instlm.pages))
    section = {
        "present": True,
        "count": int(instlm.count),
        "uv_blob": INSTLM_UV_BLOB,
        "offsets_blob": INSTLM_OFFSETS_BLOB,
        "counts_blob": INSTLM_COUNTS_BLOB,
        "page_blob": INSTLM_PAGE_BLOB,
        "total_uv_pairs": int(instlm.total_uv_pairs),
        "flip_v_applied": False,
        # --- self-describing byte contract (so a consumer needs no other doc) ---
        "order": "global instance order — parallel to instances_blob",
        "uv_dtype": "float32",
        "uv_record": "u,v float32 pair; offsets/counts are in PAIRS, not bytes",
        "index_dtype": "uint32",
        "source": ("SGPackedInstanceData: page = u16 @rec+0x1a, UVs = "
                   "C2Vector[nverts] @rec+0x2c, stride 44+8*nverts"),
        "uv_bytes": len(instlm.uv_bytes),
        "page_histogram": {str(k): v for k, v in instlm.page_histogram().items()},
        "warnings": list(instlm.warnings),
    }
    # The arithmetic self-check: 44*C + 8*Σcounts must reproduce the master's
    # `instancedatasize` EXACTLY. A non-zero residual means a mis-strided read.
    section["predicted_instancedatasize"] = int(instlm.predicted_instancedatasize)
    if instancedatasize is not None:
        section["instancedatasize"] = int(instancedatasize)
        section["instancedata_residual"] = (
            int(instlm.predicted_instancedatasize) - int(instancedatasize))
    if subset:
        section["subset"] = True
    return section


def write_package(out_dir: Path, master: str, meshes: list[SceneMesh],
                  instances: list[SceneInstance],
                  lightmap: dict | None = None,
                  instance_lightmap=None,
                  instancedatasize: int | None = None,
                  subset: bool = False) -> Path:
    """Write a `<name>.lescatter/` package (manifest.json + blobs/) to `out_dir`.

    Archive-independent: takes fully-decoded Python data and serialises the pinned
    contract. Returns the package directory path.

    `lightmap` (v4, optional) is the MASTER-level resource binding — see
    `extract_scene` / `le_mesh.lightmap.lightmap_resource_name_for_scene`. Omitted
    entirely when the caller cannot see it, rather than guessed.

    `instance_lightmap` (v5, optional) is a
    `le_static_scatter.InstanceLightmap` — the per-instance baked lightmap
    stream. `None` writes `{"present": false, "reason": ...}` so a consumer can
    tell "not extracted" from "not available".
    """
    out_dir = Path(out_dir)
    blobs = out_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)

    mesh_entries = []
    for mesh in meshes:
        pos_name = f"blobs/m{mesh.index}_pos.bin"
        idx_name = f"blobs/m{mesh.index}_idx.bin"
        (out_dir / pos_name).write_bytes(_pack_f32(mesh.positions))
        (out_dir / idx_name).write_bytes(_pack_u32(mesh.indices))
        entry = {
            "index": mesh.index,
            "name_hash": f"{mesh.name_hash & 0xFFFFFFFFFFFFFFFF:016x}",
            "matidx": int(mesh.matidx),
            "shdidx": int(mesh.shdidx),
            "draws": [
                {"matidx": int(d["matidx"]), "shdidx": int(d["shdidx"]),
                 "idx_start": int(d["idx_start"]), "idx_count": int(d["idx_count"])}
                for d in mesh.draws
            ],
            "aabb_min": [float(v) for v in mesh.aabb_min],
            "aabb_max": [float(v) for v in mesh.aabb_max],
            "instance_offset": int(mesh.instance_offset),
            "instance_count": int(mesh.instance_count),
            "nverts": mesh.nverts,
            "nindices": mesh.nindices,
            "positions": pos_name,
            "indices": idx_name,
            "proxy": bool(mesh.proxy),
            # --- v4 lightmap ids (always present; sentinel == not lightmapped) ---
            "lightmap_index": int(mesh.lightmap_index) & 0xFFFFFFFF,
            "lm_slice_index": int(mesh.lm_slice_index) & 0xFFFFFFFF,
            "numlobes": int(mesh.numlobes),
        }
        if mesh.normals:
            nrm_name = f"blobs/m{mesh.index}_nrm.bin"
            (out_dir / nrm_name).write_bytes(_pack_f32(mesh.normals))
            entry["normals"] = nrm_name
        if mesh.uv0:
            uv_name = f"blobs/m{mesh.index}_uv0.bin"
            (out_dir / uv_name).write_bytes(_pack_f32(mesh.uv0))
            entry["uv0"] = uv_name
        # v4: the lightmap UV set. Same naming/layout convention as uv0 so a
        # reader can stream it with the identical code path.
        if mesh.uv1:
            uv1_name = f"blobs/m{mesh.index}_uv1.bin"
            (out_dir / uv1_name).write_bytes(_pack_f32(mesh.uv1))
            entry["uv1"] = uv1_name
        mesh_entries.append(entry)

    # instances.bin -- N records, GLOBAL order, 44 B each, LE:
    #   mesh_index:u32, tx,ty,tz:f32, qx,qy,qz,qw:f32, sx,sy,sz:f32
    inst_buf = bytearray()
    for inst in instances:
        inst_buf += struct.pack(
            "<I10f", inst.mesh_index & 0xFFFFFFFF,
            inst.translation[0], inst.translation[1], inst.translation[2],
            inst.rotation[0], inst.rotation[1], inst.rotation[2], inst.rotation[3],
            inst.scale[0], inst.scale[1], inst.scale[2])
    (out_dir / "blobs" / "instances.bin").write_bytes(bytes(inst_buf))

    # v3: instance_lod.bin -- N records PARALLEL to instances.bin, 12 B each, LE:
    #   lod_group:u32 (0xFFFFFFFF == none), lod_level:u32, lod_group_levels:u32
    # Kept in its own blob so the 44-B instances.bin contract stays byte-identical
    # and v1/v2 readers keep working.
    lod_buf = bytearray()
    for inst in instances:
        lod_buf += struct.pack("<3I", inst.lod_group & 0xFFFFFFFF,
                               int(inst.lod_level), max(1, int(inst.lod_group_levels)))
    (out_dir / "blobs" / "instance_lod.bin").write_bytes(bytes(lod_buf))

    lod_groups = {i.lod_group for i in instances if i.lod_group >= 0}
    manifest = {
        "format": PACKAGE_FORMAT,
        "version": PACKAGE_VERSION,
        "master": master,
        "axis": "native",
        "num_meshes": len(mesh_entries),
        "num_instances": len(instances),
        "meshes": mesh_entries,
        "instances_blob": "blobs/instances.bin",
        "lod": {
            "blob": "blobs/instance_lod.bin",
            "record": "lod_group:u32,lod_level:u32,lod_group_levels:u32",
            "num_groups": len(lod_groups),
            "max_level": max((i.lod_level for i in instances), default=0),
            "levels_histogram": _levels_histogram(instances),
        },
        # v4: per-mesh lightmap id readout. The per-mesh ids themselves live on
        # each mesh entry; this is only a summary.
        "lightmap_stats": _lightmap_stats(meshes),
        # v5: the per-instance baked lightmap stream (page + per-vertex UVs).
        "instance_lightmap": _instance_lightmap_section(
            out_dir, instance_lightmap,
            instancedatasize=instancedatasize, subset=subset),
    }
    if lightmap is not None:
        manifest["lightmap"] = lightmap
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1),
                                           encoding="utf-8")
    return out_dir


# ---------------------------------------------------------------------------
# Geometry decode helpers
# ---------------------------------------------------------------------------

def box_proxy(aabb_min, aabb_max):
    """8-vert / 12-tri (36-index) box spanning aabb_min..aabb_max, GAME space."""
    x0, y0, z0 = aabb_min
    x1, y1, z1 = aabb_max
    positions = [
        x0, y0, z0,  x1, y0, z0,  x1, y1, z0,  x0, y1, z0,
        x0, y0, z1,  x1, y0, z1,  x1, y1, z1,  x0, y1, z1,
    ]
    indices = [
        0, 1, 2, 0, 2, 3,   # -z
        4, 6, 5, 4, 7, 6,   # +z
        0, 4, 5, 0, 5, 1,   # -y
        3, 2, 6, 3, 6, 7,   # +y
        0, 3, 7, 0, 7, 4,   # -x
        1, 5, 6, 1, 6, 2,   # +x
    ]
    return positions, indices


def _first_k(flat: list, comps: int, k: int, nverts: int) -> list:
    """Reshape a flat per-vertex array to keep the first `k` of `comps`."""
    if comps == k:
        return flat
    out: list = []
    for i in range(nverts):
        base = i * comps
        row = flat[base:base + comps]
        if len(row) < k:
            row = list(row) + [0.0] * (k - len(row))
        out.extend(row[:k])
    return out


# ---------------------------------------------------------------------------
# Full extraction  (archive-dependent; Oodle only touched here)
# ---------------------------------------------------------------------------

@dataclass
class ExtractStats:
    num_meshes_total: int = 0
    meshes_emitted: int = 0
    meshes_decoded: int = 0
    meshes_proxied: int = 0
    aabb_exact: int = 0
    aabb_contained: int = 0
    aabb_out: int = 0
    with_normals: int = 0
    with_uv0: int = 0
    with_uv1: int = 0
    lightmapped_meshes: int = 0
    lightmap_resource: str | None = None
    nonf32_position: int = 0
    dangling: int = 0
    total_verts: int = 0
    total_indices: int = 0
    num_instances_emitted: int = 0
    capped_to: int | None = None
    lod_groups: int = 0
    lod_max_level: int = 0
    # v5 per-instance lightmap stream
    instance_lightmap: bool = False
    instance_lm_uv_pairs: int = 0
    instance_lm_bytes: int = 0
    instance_lm_residual: int | None = None
    instance_lm_pages: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)


#: `CGLightMapResourceWin7` resource-TYPE hash (hash_lookup.json).
LIGHTMAP_TYPE_WIN7 = 0x6665BEDFEADF8B79


def resolve_master_lightmap(archive_hash: str, master_name_hash: int) -> dict:
    """The master's `CGLightMapResourceWin7` binding, or an explicit unresolved.

    Mechanism (`stream-confirmed`, `le_mesh.lightmap`): a scene's
    mesh-list / scene / static-instance / lightmap resources all carry the SAME
    resource name hash — `CGScene.lightmapresource` is a sibling `CResourceInstanceT`
    and `CGSceneData` stores no id for it. So the static-instance master's own
    name IS the lightmap resource name (`lightmap_resource_name_for_scene` is
    deliberately an identity function that names the mechanism).

    We do not guess: the returned dict says `present` only when a resource of
    type `CGLightMapResourceWin7` with that exact name is actually in the master
    archive's contents table. The OTHER join mechanism —
    `SGDynamicInstancesData.lightmapsid` (the trailing CSymbol64 of a
    `CGDynamicInstanceResourceWin7`) — does not apply here: a static-instance
    master is not a dynamic-instance resource, so it is reported as N/A rather
    than searched for.
    """
    from le_oodle import chunk_table, decompress_range
    from le_archive_decode import ARCHIVE_PRIMARY, parse_header, entry_at
    from le_mesh.lightmap import lightmap_resource_name_for_scene

    want = lightmap_resource_name_for_scene(master_name_hash)
    out = {
        "resource_name": f"{want:016x}",
        "mechanism": "sibling-by-name (CGScene.lightmapresource)",
        "dynamic_lightmapsid": None,          # N/A for a static-instance master
        "present": False,
        "slice": None,
        "confidence": "stream-confirmed",
    }
    try:
        raw = (ARCHIVE_PRIMARY / archive_hash).read_bytes()
        uncomp_total, _ = chunk_table(raw)
        prelude = decompress_range(raw, 0, 64)
        primary_size = struct.unpack_from("<Q", prelude, 0)[0]
        extra_skip = struct.unpack_from("<Q", prelude, 24)[0]
        header0_off = 32 + extra_skip + primary_size
        tail = decompress_range(raw, header0_off, uncomp_total)
        del raw
        hdr = parse_header(tail, 0)
        for _ in range(2):
            for i in range(hdr.contents.count):
                th, nh, val = struct.unpack_from("<QQQ", tail, hdr.contents.off + i * 24)
                if th == LIGHTMAP_TYPE_WIN7 and nh == want and val < hdr.entries.count:
                    pos, size = entry_at(tail, hdr, val)
                    out["present"] = True
                    out["slice"] = {"pos": int(pos), "size": int(size)}
                    return out
            hdr = parse_header(tail, hdr.end)
    except Exception as exc:            # noqa: BLE001
        out["error"] = str(exc)
    return out


def _find_meshlist(blob: bytes, gpudatasize: int, num_meshes: int, hint: int = 369032):
    """Locate the inline CGMeshListData stream; return its parse_candidate dict."""
    import le_meshlist_decode as ml
    cand = ml.parse_candidate(blob, hint, gpudatasize)
    if cand is not None and cand["meshes"].count == num_meshes:
        return cand, hint
    for off in range(2048, len(blob) - 32, 4):
        cand = ml.parse_candidate(blob, off, gpudatasize)
        if cand is not None and cand["meshes"].count == num_meshes:
            return cand, off
    return None, None


def extract_scene(archive_hash: str, out_dir: Path, subset: int | None = None,
                  hash_lookup: Path = Path("hash_lookup.json"),
                  progress=print, instance_lightmap: bool = False) -> ExtractStats:
    """Decode the static-scatter master + inline meshlist and write a `.lescatter`.

    `subset`: if set, keep only the top-`subset` mesh-types by instance count (ALL
    of their instances). Otherwise emit every mesh + every instance.

    `instance_lightmap`: also emit the v5 per-instance baked lightmap stream
    (page + per-vertex UVs). Default OFF — it is ~52 MB on station_front and
    forces per-instance mesh copies downstream, which must be the user's choice.
    """
    from le_oodle import decompress_range
    from le_archive_decode import ARCHIVE_GPU
    from le_static_scatter import (
        load_master_blob, decode_static_master, decode_gpu_instances,
    )
    from le_mesh.vertex_format import (
        read_vertex_format, decode_vertex_buffer, EUsage,
        lightmap_uv_attr_name,
    )
    from le_mesh.meshlist import (
        MESH_STRIDE, M_NAME, M_VBINDEX, M_IBINDEX, M_RENDERPARAMIDX,
        M_NUMRENDERPARAMS, M_AABB, RENDERPARAM_STRIDE, RP_MATERIALIDX,
        RP_SHADERSETIDX, RP_IDXSTART, RP_IDXCOUNT, INDEXBUFFER_STRIDE,
        IB_OFFSET, IB_NUMINDICES, IB_INDEXSIZE,
        M_LIGHTMAPINDEX, M_LMSLICEINDEX, M_NUMLOBES,
    )
    from le_mesh.vertex_format import VB_RECORD_STRIDE

    stats = ExtractStats()

    name_hash, blob = load_master_blob(archive_hash, hash_lookup)
    if blob is None:
        raise ValueError(f"archive {archive_hash}: no populated static master")
    d = decode_static_master(blob)
    ido, ids = d.gpu_instancedata
    ito, its = d.gpu_instancetypedata
    gpudatasize = ito + its
    stats.num_meshes_total = d.num_meshes
    for w in d.warnings:
        stats.notes.append(f"master-warn: {w}")
    progress(f"master {name_hash:016x}: num_meshes={d.num_meshes} "
             f"num_instances={d.num_instances} ido={ido} gpudatasize={gpudatasize}")

    # LOD system (SGStaticInstanceLODData). Non-fatal: a master whose LOD block
    # fails to validate still extracts, just without per-instance LOD levels.
    from le_mesh.static_lod import decode_static_lod
    try:
        lod = decode_static_lod(blob, d.num_meshes, d.num_instances)
    except ValueError as exc:
        lod = None
        stats.notes.append(f"lod-decode failed: {exc}")
        progress(f"LOD: DECODE FAILED ({exc}) -- extracting without LOD levels")
    else:
        for w in lod.warnings:
            stats.notes.append(f"lod-warn: {w}")
        stats.lod_groups = lod.num_groups
        stats.lod_max_level = lod.max_level
        progress(f"LOD: {lod.num_groups} groups, levels 0..{lod.max_level}, "
                 f"{len(lod.nodes)} nodes, {len(lod.nodelookup)} lod entries")

    cand, mstart = _find_meshlist(blob, gpudatasize, d.num_meshes,
                                  hint=(lod.meshlist_offset if lod else 369032))
    if cand is None:
        raise ValueError("could not locate inline CGMeshListData stream")
    meshes_t = cand["meshes"]
    rps_t = cand["renderparams"]
    vbs_t = cand["vertexbuffers"]
    ibs_t = cand["indexbuffers"]
    progress(f"meshlist @prim[{mstart}]: meshes={meshes_t.count} rp={rps_t.count} "
             f"vb={vbs_t.count} ib={ibs_t.count}")

    # --- pick the mesh-type set to emit (needed BEFORE the GPU pass so the v5
    # per-instance lightmap arrays stay parallel to the emitted instances) ---
    order = list(range(d.num_meshes))
    if subset is not None and subset < d.num_meshes:
        order = sorted(order, key=lambda m: d.instancescount[m], reverse=True)[:subset]
        stats.capped_to = subset
    selected = set(order)

    # Per-mesh vertex counts, read from the PRIMARY vertex-buffer records only
    # (no GPU bytes): `SGStaticInstanceTypeData.stride` must equal
    # `44 + 8*nverts`, and this is what makes that assertion checkable.
    nverts_by_mesh: dict[int, int] = {}
    if instance_lightmap:
        for mi in range(d.num_meshes):
            mm = meshes_t.data_off + mi * MESH_STRIDE
            vbi = struct.unpack_from("<I", blob, mm + M_VBINDEX)[0]
            if vbi >= vbs_t.count:
                continue
            _els, _st, _rel, vc = read_vertex_format(
                blob, vbs_t.data_off + vbi * VB_RECORD_STRIDE)
            nverts_by_mesh[mi] = vc

    # per-instance transforms (all of them) + resolve G (+ optional lightmap)
    gi = decode_gpu_instances(
        archive_hash, name_hash, d, hash_lookup,
        want_lightmap=instance_lightmap,
        nverts_by_mesh=nverts_by_mesh or None,
        selected=selected if subset is not None else None)
    G, transforms, instlm = gi.G, gi.transforms, gi.lightmap
    progress(f"G={G}  decoded {len(transforms)} transforms")
    if instlm is not None:
        stats.instance_lightmap = True
        stats.instance_lm_uv_pairs = instlm.total_uv_pairs
        stats.instance_lm_bytes = len(instlm.uv_bytes)
        stats.instance_lm_residual = instlm.predicted_instancedatasize - ids
        stats.instance_lm_pages = instlm.page_histogram()
        for w in instlm.warnings[:8]:
            stats.notes.append(f"instlm-warn: {w}")
        progress(f"instance lightmap: {instlm.count} instances, "
                 f"{instlm.total_uv_pairs} UV pairs, "
                 f"{len(instlm.uv_bytes)} B, "
                 f"instancedatasize residual={stats.instance_lm_residual} "
                 f"(0 == the 44+8*nverts model reproduces the shipped size)")

    # decompress the geometry window [G, G+ido) once (~85 MiB)
    gpu_raw = (ARCHIVE_GPU / archive_hash).read_bytes()
    geo = decompress_range(gpu_raw, G, G + ido)
    del gpu_raw
    progress(f"geometry window: {len(geo)} bytes (== ido? {len(geo) == ido})")

    def read_mesh_header(mi):
        m = meshes_t.data_off + mi * MESH_STRIDE
        return {
            "name_hash": struct.unpack_from("<Q", blob, m + M_NAME)[0],
            "vbi": struct.unpack_from("<I", blob, m + M_VBINDEX)[0],
            "ibi": struct.unpack_from("<I", blob, m + M_IBINDEX)[0],
            "rp_idx": struct.unpack_from("<I", blob, m + M_RENDERPARAMIDX)[0],
            "rp_n": struct.unpack_from("<I", blob, m + M_NUMRENDERPARAMS)[0],
            "aabb": struct.unpack_from("<6f", blob, m + M_AABB),
            # v4 lightmap ids — same CGMeshData layout as a standalone mesh-list
            # (MESH_STRIDE 0x80); the inline static-instance meshlist is not a
            # different struct. `stream-confirmed`.
            "lightmap_index": struct.unpack_from("<I", blob, m + M_LIGHTMAPINDEX)[0],
            "lm_slice_index": struct.unpack_from("<I", blob, m + M_LMSLICEINDEX)[0],
            "numlobes": struct.unpack_from("<I", blob, m + M_NUMLOBES)[0],
        }

    def first_draw_mat_shd(rp_idx, rp_n):
        if rp_n <= 0 or rp_idx >= rps_t.count:
            return 0xFFFFFFFF, 0xFFFFFFFF
        base = rps_t.data_off + rp_idx * RENDERPARAM_STRIDE
        return (struct.unpack_from("<I", blob, base + RP_MATERIALIDX)[0],
                struct.unpack_from("<I", blob, base + RP_SHADERSETIDX)[0])

    def all_draws(rp_idx, rp_n):
        """Every CGRenderParams of the mesh, in renderparam order (clamped to the
        renderparam table). idx_start/idx_count are mesh-relative index positions."""
        out = []
        for i in range(max(int(rp_n), 0)):
            gi = rp_idx + i
            if gi >= rps_t.count:
                break
            base = rps_t.data_off + gi * RENDERPARAM_STRIDE
            out.append({
                "matidx": struct.unpack_from("<I", blob, base + RP_MATERIALIDX)[0],
                "shdidx": struct.unpack_from("<I", blob, base + RP_SHADERSETIDX)[0],
                "idx_start": struct.unpack_from("<I", blob, base + RP_IDXSTART)[0],
                "idx_count": struct.unpack_from("<I", blob, base + RP_IDXCOUNT)[0],
            })
        return out

    scene_meshes: list[SceneMesh] = []
    for mi in order:
        h = read_mesh_header(mi)
        aabb = h["aabb"]
        aabb_min, aabb_max = aabb[0:3], aabb[3:6]
        matidx, shdidx = first_draw_mat_shd(h["rp_idx"], h["rp_n"])
        inst_off = d.instanceoffsets[mi] if mi < len(d.instanceoffsets) else 0
        inst_cnt = d.instancescount[mi] if mi < len(d.instancescount) else 0

        proxy = False
        positions = normals = uv0 = uv1 = None
        indices: list = []

        if h["vbi"] >= vbs_t.count or h["ibi"] >= ibs_t.count:
            proxy = True
            stats.dangling += 1
        else:
            vb_off = vbs_t.data_off + h["vbi"] * VB_RECORD_STRIDE
            elements, stride, rel_gpu, vcount = read_vertex_format(blob, vb_off)
            pos_elem = next((e for e in elements if e.usage == EUsage.ePosition), None)
            if pos_elem is None or vcount == 0 or stride <= 0:
                proxy = True
            elif pos_elem.type != 8:  # not eF32 -> positions would be wrong; proxy
                proxy = True
                stats.nonf32_position += 1
            else:
                # decode only position/normal/texcoord (drop tangent/color to save time)
                want = {EUsage.ePosition, EUsage.eNormal, EUsage.eTexCoord}
                sub = [e for e in elements if e.usage in want]
                attrs = decode_vertex_buffer(geo, 0, rel_gpu, stride, vcount, sub)
                pa = attrs.get("position")
                if pa is None or not pa.data:
                    proxy = True
                else:
                    positions = _first_k(pa.data, pa.comps, 3, vcount)
                    na = attrs.get("normal")
                    if na is not None and na.data and not na.packed_unresolved:
                        normals = _first_k(na.data, na.comps, 3, vcount)
                    ua = attrs.get("uv0")
                    if ua is not None and ua.data and not ua.packed_unresolved:
                        uv0 = _first_k(ua.data, ua.comps, 2, vcount)
                    # The scatter package's `uv1` blob is THE LIGHTMAP UV SET.
                    # Pick it by SEMANTIC SLOT 4 (`shader-confirmed`, the
                    # engine's `vb_texcoord4`), NOT by the
                    # appearance-order attribute name: on a (0, 1, 4) object the
                    # attribute called `uv1` is a copy of the albedo UV set
                    # docs/LIGHTING.md. Resolved against the FULL
                    # element table; decoded from the same `sub` list that
                    # already covers eTexCoord — no extra buffer read.
                    # (Filtering `elements` to `sub` does not change texcoord
                    # numbering — the counter is per usage — but `elements` is
                    # the authoritative table, so resolve against it.)
                    lm_key = lightmap_uv_attr_name(elements)
                    u1 = attrs.get(lm_key) if lm_key else None
                    if u1 is not None and u1.data and not u1.packed_unresolved:
                        uv1 = _first_k(u1.data, u1.comps, 2, vcount)

            if not proxy:
                # index buffer
                ib_base = ibs_t.data_off + h["ibi"] * INDEXBUFFER_STRIDE
                ib_rel = struct.unpack_from("<I", blob, ib_base + IB_OFFSET)[0]
                ib_num = struct.unpack_from("<I", blob, ib_base + IB_NUMINDICES)[0]
                ib_size = struct.unpack_from("<I", blob, ib_base + IB_INDEXSIZE)[0]
                if ib_size in (2, 4) and ib_num:
                    fmt = "H" if ib_size == 2 else "I"
                    raw = struct.unpack_from(f"<{ib_num}{fmt}", geo, ib_rel)
                    indices = list(raw)
                else:
                    indices = list(range(len(positions)))  # fallback point list

                # AABB validation vs decoded position bbox
                xs = positions[0::3]; ys = positions[1::3]; zs = positions[2::3]
                dbb_min = (min(xs), min(ys), min(zs))
                dbb_max = (max(xs), max(ys), max(zs))
                tol = 1e-2
                exact = all(abs(dbb_min[k] - aabb_min[k]) < tol and
                            abs(dbb_max[k] - aabb_max[k]) < tol for k in range(3))
                contained = all(dbb_min[k] >= aabb_min[k] - tol and
                                dbb_max[k] <= aabb_max[k] + tol for k in range(3))
                if exact:
                    stats.aabb_exact += 1
                if contained:
                    stats.aabb_contained += 1
                else:
                    stats.aabb_out += 1
                    stats.notes.append(
                        f"mesh {mi}: decoded bbox outside stored AABB "
                        f"(min {tuple(round(v,2) for v in dbb_min)} vs "
                        f"{tuple(round(v,2) for v in aabb_min)})")

        if proxy:
            positions, indices = box_proxy(aabb_min, aabb_max)
            normals = uv0 = uv1 = None
            stats.meshes_proxied += 1
        else:
            stats.meshes_decoded += 1
            if normals:
                stats.with_normals += 1
            if uv0:
                stats.with_uv0 += 1
            if uv1:
                stats.with_uv1 += 1

        stats.total_verts += len(positions) // 3
        stats.total_indices += len(indices)

        # v2 draws: proxy meshes carry the box geometry (not the original index
        # ranges) so they get ONE synthetic draw over the whole proxy index buffer;
        # decoded meshes carry every real renderparam draw. A decoded mesh with no
        # renderparams falls back to one synthetic draw = its top-level pair.
        if proxy:
            draws = [{"matidx": matidx, "shdidx": shdidx,
                      "idx_start": 0, "idx_count": len(indices)}]
        else:
            draws = all_draws(h["rp_idx"], h["rp_n"]) or \
                [{"matidx": matidx, "shdidx": shdidx,
                  "idx_start": 0, "idx_count": len(indices)}]

        scene_meshes.append(SceneMesh(
            index=mi, name_hash=h["name_hash"], matidx=matidx, shdidx=shdidx,
            aabb_min=aabb_min, aabb_max=aabb_max,
            instance_offset=inst_off, instance_count=inst_cnt,
            positions=positions, indices=indices, normals=normals, uv0=uv0,
            proxy=proxy, draws=draws,
            uv1=uv1,
            # The ids come from the mesh header and are emitted even for a
            # proxied mesh: the id is a property of the mesh, not of whether we
            # managed to decode its vertex buffer.
            lightmap_index=h["lightmap_index"],
            lm_slice_index=h["lm_slice_index"],
            numlobes=h["numlobes"]))

    del geo

    # --- instances (global order; filtered to selected meshes) ---
    scene_instances: list[SceneInstance] = []
    for i, t in enumerate(transforms):
        m = d.mesh_for_instance(i)
        if m not in selected:
            continue
        if lod is not None and i < len(lod.level_of_instance):
            g, lv = lod.group_of_instance[i], lod.level_of_instance[i]
            gl = lod.group_num_levels.get(g, 1)
        else:
            g, lv, gl = -1, 0, 1
        scene_instances.append(SceneInstance(
            mesh_index=m, translation=t.translation,
            rotation=t.rotation, scale=t.scale,
            lod_group=g, lod_level=lv, lod_group_levels=gl))
    stats.num_instances_emitted = len(scene_instances)
    stats.meshes_emitted = len(scene_meshes)
    stats.lightmapped_meshes = sum(1 for m in scene_meshes
                                   if m.lightmap_index != LIGHTMAP_NONE)

    # v4: the MASTER-level lightmap resource binding. Reachable from here (the
    # master's own name hash IS the binding), so it is emitted rather than left
    # to the consumer to guess.
    lm_binding = resolve_master_lightmap(archive_hash, name_hash)
    stats.lightmap_resource = (lm_binding["resource_name"]
                               if lm_binding.get("present") else None)
    progress(f"lightmap: resource {lm_binding['resource_name']} "
             f"present={lm_binding['present']}; "
             f"{stats.lightmapped_meshes}/{len(scene_meshes)} meshes lightmapped, "
             f"{stats.with_uv1} with uv1")

    write_package(out_dir, archive_hash, scene_meshes, scene_instances,
                  lightmap=lm_binding, instance_lightmap=instlm,
                  instancedatasize=ids, subset=subset is not None)
    progress(f"wrote {out_dir}  ({stats.meshes_emitted} meshes, "
             f"{stats.num_instances_emitted} instances)")
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("hash")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--subset", type=int, default=None,
                    help="cap to the top-N mesh-types by instance count")
    ap.add_argument("--hash-lookup", type=Path, default=Path("hash_lookup.json"))
    ap.add_argument("--instance-lightmap", action="store_true",
                    help="emit the v5 per-instance baked lightmap stream (page + "
                         "per-vertex UVs). ~52 MB on station_front; default OFF")
    args = ap.parse_args()

    stats = extract_scene(args.hash, args.out, subset=args.subset,
                          hash_lookup=args.hash_lookup,
                          instance_lightmap=args.instance_lightmap)
    print("\n=== extract summary ===")
    print(f"  meshes total={stats.num_meshes_total} emitted={stats.meshes_emitted} "
          f"decoded={stats.meshes_decoded} proxied={stats.meshes_proxied}"
          + (f" (capped to {stats.capped_to})" if stats.capped_to else ""))
    print(f"  aabb: exact={stats.aabb_exact} contained={stats.aabb_contained} "
          f"out={stats.aabb_out}")
    print(f"  attrs: with_normals={stats.with_normals} with_uv0={stats.with_uv0} "
          f"with_uv1={stats.with_uv1} "
          f"nonf32_pos={stats.nonf32_position} dangling={stats.dangling}")
    print(f"  lightmap: resource={stats.lightmap_resource} "
          f"lightmapped_meshes={stats.lightmapped_meshes}")
    print(f"  totals: verts={stats.total_verts} indices={stats.total_indices} "
          f"instances={stats.num_instances_emitted}")
    print(f"  lod: groups={stats.lod_groups} levels=0..{stats.lod_max_level}")
    if stats.instance_lightmap:
        print(f"  instance lightmap: {stats.instance_lm_uv_pairs} UV pairs, "
              f"{stats.instance_lm_bytes} B, "
              f"instancedatasize residual={stats.instance_lm_residual}")
        print(f"    page histogram: {stats.instance_lm_pages}")
    else:
        print("  instance lightmap: not extracted (pass --instance-lightmap)")
    for note in stats.notes[:12]:
        print(f"  note: {note}")
    if len(stats.notes) > 12:
        print(f"  ... {len(stats.notes) - 12} more notes")


if __name__ == "__main__":
    main()
