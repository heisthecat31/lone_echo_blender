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
PACKAGE_VERSION = 2


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


def _pack_f32(flat) -> bytes:
    return struct.pack(f"<{len(flat)}f", *flat)


def _pack_u32(flat) -> bytes:
    return struct.pack(f"<{len(flat)}I", *flat)


def write_package(out_dir: Path, master: str, meshes: list[SceneMesh],
                  instances: list[SceneInstance]) -> Path:
    """Write a `<name>.lescatter/` package (manifest.json + blobs/) to `out_dir`.

    Archive-independent: takes fully-decoded Python data and serialises the pinned
    contract. Returns the package directory path.
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
        }
        if mesh.normals:
            nrm_name = f"blobs/m{mesh.index}_nrm.bin"
            (out_dir / nrm_name).write_bytes(_pack_f32(mesh.normals))
            entry["normals"] = nrm_name
        if mesh.uv0:
            uv_name = f"blobs/m{mesh.index}_uv0.bin"
            (out_dir / uv_name).write_bytes(_pack_f32(mesh.uv0))
            entry["uv0"] = uv_name
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

    manifest = {
        "format": PACKAGE_FORMAT,
        "version": PACKAGE_VERSION,
        "master": master,
        "axis": "native",
        "num_meshes": len(mesh_entries),
        "num_instances": len(instances),
        "meshes": mesh_entries,
        "instances_blob": "blobs/instances.bin",
    }
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
    nonf32_position: int = 0
    dangling: int = 0
    total_verts: int = 0
    total_indices: int = 0
    num_instances_emitted: int = 0
    capped_to: int | None = None
    notes: list = field(default_factory=list)


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
                  progress=print) -> ExtractStats:
    """Decode the static-scatter master + inline meshlist and write a `.lescatter`.

    `subset`: if set, keep only the top-`subset` mesh-types by instance count (ALL
    of their instances). Otherwise emit every mesh + every instance.
    """
    from le_oodle import decompress_range
    from le_archive_decode import ARCHIVE_GPU
    from le_static_scatter import (
        load_master_blob, decode_static_master, decode_gpu_transforms,
    )
    from le_mesh.vertex_format import (
        read_vertex_format, decode_vertex_buffer, EUsage,
    )
    from le_mesh.meshlist import (
        MESH_STRIDE, M_NAME, M_VBINDEX, M_IBINDEX, M_RENDERPARAMIDX,
        M_NUMRENDERPARAMS, M_AABB, RENDERPARAM_STRIDE, RP_MATERIALIDX,
        RP_SHADERSETIDX, RP_IDXSTART, RP_IDXCOUNT, INDEXBUFFER_STRIDE,
        IB_OFFSET, IB_NUMINDICES, IB_INDEXSIZE,
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

    cand, mstart = _find_meshlist(blob, gpudatasize, d.num_meshes)
    if cand is None:
        raise ValueError("could not locate inline CGMeshListData stream")
    meshes_t = cand["meshes"]
    rps_t = cand["renderparams"]
    vbs_t = cand["vertexbuffers"]
    ibs_t = cand["indexbuffers"]
    progress(f"meshlist @prim[{mstart}]: meshes={meshes_t.count} rp={rps_t.count} "
             f"vb={vbs_t.count} ib={ibs_t.count}")

    # per-instance transforms (all of them) + resolve G
    G, _typetable, transforms = decode_gpu_transforms(
        archive_hash, name_hash, d, hash_lookup)
    progress(f"G={G}  decoded {len(transforms)} transforms")

    # decompress the geometry window [G, G+ido) once (~85 MiB)
    gpu_raw = (ARCHIVE_GPU / archive_hash).read_bytes()
    geo = decompress_range(gpu_raw, G, G + ido)
    del gpu_raw
    progress(f"geometry window: {len(geo)} bytes (== ido? {len(geo) == ido})")

    # --- pick the mesh-type set to emit ---
    order = list(range(d.num_meshes))
    if subset is not None and subset < d.num_meshes:
        order = sorted(order, key=lambda m: d.instancescount[m], reverse=True)[:subset]
        stats.capped_to = subset
    selected = set(order)

    def read_mesh_header(mi):
        m = meshes_t.data_off + mi * MESH_STRIDE
        return {
            "name_hash": struct.unpack_from("<Q", blob, m + M_NAME)[0],
            "vbi": struct.unpack_from("<I", blob, m + M_VBINDEX)[0],
            "ibi": struct.unpack_from("<I", blob, m + M_IBINDEX)[0],
            "rp_idx": struct.unpack_from("<I", blob, m + M_RENDERPARAMIDX)[0],
            "rp_n": struct.unpack_from("<I", blob, m + M_NUMRENDERPARAMS)[0],
            "aabb": struct.unpack_from("<6f", blob, m + M_AABB),
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
        positions = normals = uv0 = None
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
            normals = uv0 = None
            stats.meshes_proxied += 1
        else:
            stats.meshes_decoded += 1
            if normals:
                stats.with_normals += 1
            if uv0:
                stats.with_uv0 += 1

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
            proxy=proxy, draws=draws))

    del geo

    # --- instances (global order; filtered to selected meshes) ---
    scene_instances: list[SceneInstance] = []
    for i, t in enumerate(transforms):
        m = d.mesh_for_instance(i)
        if m not in selected:
            continue
        scene_instances.append(SceneInstance(
            mesh_index=m, translation=t.translation,
            rotation=t.rotation, scale=t.scale))
    stats.num_instances_emitted = len(scene_instances)
    stats.meshes_emitted = len(scene_meshes)

    write_package(out_dir, archive_hash, scene_meshes, scene_instances)
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
    args = ap.parse_args()

    stats = extract_scene(args.hash, args.out, subset=args.subset,
                          hash_lookup=args.hash_lookup)
    print("\n=== extract summary ===")
    print(f"  meshes total={stats.num_meshes_total} emitted={stats.meshes_emitted} "
          f"decoded={stats.meshes_decoded} proxied={stats.meshes_proxied}"
          + (f" (capped to {stats.capped_to})" if stats.capped_to else ""))
    print(f"  aabb: exact={stats.aabb_exact} contained={stats.aabb_contained} "
          f"out={stats.aabb_out}")
    print(f"  attrs: with_normals={stats.with_normals} with_uv0={stats.with_uv0} "
          f"nonf32_pos={stats.nonf32_position} dangling={stats.dangling}")
    print(f"  totals: verts={stats.total_verts} indices={stats.total_indices} "
          f"instances={stats.num_instances_emitted}")
    for note in stats.notes[:12]:
        print(f"  note: {note}")
    if len(stats.notes) > 12:
        print(f"  ... {len(stats.notes) - 12} more notes")


if __name__ == "__main__":
    main()
