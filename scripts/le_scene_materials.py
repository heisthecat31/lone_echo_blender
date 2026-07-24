"""le_scene_materials — resolve a static-scatter's per-material base-color texture
(+ scalar) and extract the DDS, for the Blender static-scatter render path.

Given a Lone Echo (Win7) static-scatter MASTER archive (a populated
`CGStaticInstanceResourceWin7` whose companion `CGSceneResourceWin7` holds the
level material/shaderset binding table) and its already-built `.lescatter`
geometry package (whose per-mesh `matidx`/`shdidx` index that binding table),
this produces, for every DISTINCT (matidx, shdidx) pair used by the scatter:

  * material_hash  = scene binding mat_hashes[matidx]
  * shaderset_hash = scene binding shd_hashes[shdidx]
  * base-color texture hash (from the shaderset's SShaderInputData rows -> role
    classification, `le_mesh.materials`), extracted to <out>/<texhash>.dds
  * SGMaterialData scalars (base_color, mattype, double_sided) from the material
    slice, `le_mesh.material_scalars`.

Cross-archive resolution: the scatter's materials/textures live overwhelmingly
in PARENT archives. We build the transitive parent closure from the master's
`CArchiveHeaderData.parents` (u32 count at header.start+0xB0, u64 hash array at
header.start+0xB4) and index every TEXTURE / MATERIAL resource across it.

Reuse (no re-implementation):
  * le_shaderset_scan.scan_shaderset_slice  (SShaderInputData scan)
  * le_mesh.materials  (role -> base-color/normal classification)
  * le_mesh.material_scalars.decode_material_scalars  (SGMaterialData scalars)
  * le_scene_binding.parse_binding_table  (matidx/shdidx tables)
  * le_cross_archive_texture  (streaming + inline DDS handling)
  * le_oodle.decompress_range  (OOM-safe ranged reads; never full-decompress)

Two decode facts on station_front 942c829457a04a62:
  * The scan must cover 4-byte-aligned SShaderInputData tables: ~2/3 of these
    shadersets place the input table on a 4-byte (not 8-byte) boundary, which the
    proven 8-byte-stride scanner misses. We run it on BOTH `d` and `d[4:]`.
  * The base-color texture is almost always streaming (data in the global
    packfile keyed by tex_hash, GPU entry a 16-byte stub); a few are inline.

Run under Windows Python (Oodle DLL):
    python.exe scripts/le_scene_materials.py 942c829457a04a62 \
        --manifest blender_tool/exports/942c829457a04a62.lescatter/manifest.json \
        --out-textures blender_tool/exports/942c829457a04a62_textures \
        --out-json     blender_tool/exports/942c829457a04a62_materials.json
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from le_oodle import DATA_ROOT, chunk_table, decompress_range          # noqa: E402
import le_scene_binding as sb                          # noqa: E402
import le_shaderset_scan as sts                      # noqa: E402
import le_cross_archive_texture as s67                   # noqa: E402
from le_mesh import materials as mat                                   # noqa: E402
from le_mesh import material_scalars as msc                            # noqa: E402

ARCHIVE_PRIMARY = s67.ARCHIVE_PRIMARY
ARCHIVE_GPU     = s67.ARCHIVE_GPU

SCENE_TYPE    = 0x86f4cd162e7da857   # CGSceneResourceWin7
MATERIAL_TYPE = 0x117d2b6509c8ff79   # CGMaterialResourceWin7
SHADERSET_TYPE = 0x5fa019d27a511a3b  # CGShaderSetResourceWin7
TEXTURE_PRIM  = 0xe8017b774f2b6327   # CGTextureResourceWin7
TEXTURE_GPU   = 0xe2f9e022d8519ca9   # CGTextureResourceWin7GPU


# ---------------------------------------------------------------------------
# Ranged archive header parsing (never full-decompress)
# ---------------------------------------------------------------------------

class ArchiveHeader:
    """The two CArchiveHeaderData records of an archive, parsed from a ranged
    tail decompress. Absolute offsets are relative to the decompressed primary;
    we translate with base = header_off."""

    def __init__(self, path: Path):
        self.path = path
        raw = path.read_bytes()
        uncomp_total, _ = chunk_table(raw)
        head = decompress_range(raw, 0, 32)
        primary_size = struct.unpack_from("<Q", head, 0)[0]
        extra_skip = struct.unpack_from("<Q", head, 24)[0]
        self.data_off = 32 + extra_skip
        self.header_off = self.data_off + primary_size
        self.base = self.header_off
        self.tail = decompress_range(raw, self.base, uncomp_total)
        self.h0 = self._parse(self.base)
        self.h1 = self._parse(self.h0["end"])

    def _u32(self, o: int) -> int:
        return struct.unpack_from("<I", self.tail, o - self.base)[0]

    def _u64(self, o: int) -> int:
        return struct.unpack_from("<Q", self.tail, o - self.base)[0]

    def _parse(self, off: int) -> dict:
        start = off
        off += 0xB0                              # SLanguageSelection fixed blob
        pc = self._u32(off); poff = off + 4; off += 4 + pc * 8
        ec = self._u32(off); off += 4; eo = off; off += ec * 8
        cc = self._u32(off); off += 4; co = off; off += cc * 24
        off += 4                                  # contents_seed
        vc = self._u32(off); off += 4 + vc * 16 + 4
        hc = self._u32(off); off += 4 + hc * 16
        ppc = self._u32(off); off += 4 + ppc * 32
        parents = [self._u64(poff + i * 8) for i in range(pc)]
        return dict(start=start, end=off, entries_off=eo, entries_count=ec,
                    contents_off=co, contents_count=cc, parents=parents)

    def entry(self, h: dict, value: int) -> tuple[int, int]:
        o = h["entries_off"] + value * 8
        return struct.unpack_from("<II", self.tail, o - self.base)

    def collect(self, h: dict, type_hash: int) -> dict[int, tuple[int, int]]:
        out: dict[int, tuple[int, int]] = {}
        for i in range(h["contents_count"]):
            th, nh, val = struct.unpack_from(
                "<QQQ", self.tail, (h["contents_off"] + i * 24) - self.base)
            if th == type_hash and val < h["entries_count"]:
                out[nh] = self.entry(h, val)
        return out

    @property
    def parents(self) -> list[int]:
        return self.h0["parents"]


# ---------------------------------------------------------------------------
# Parent-closure index
# ---------------------------------------------------------------------------

def build_index(master_hash: str, verbose: bool = False) -> dict:
    """BFS the transitive parent closure; index TEXTURE + MATERIAL homes.

    Returns a dict with: data_off_master, scene_slice(pos,size), shd_map,
    tex_home{hexhash->[arch,data_off,pos,size]}, tex_gpu{hexhash->[pos,size]},
    mat_home{hexhash->[arch,data_off,pos,size]}, parents(list hex), n_archives.
    Master-local entries win (queued first).
    """
    master_i = int(master_hash, 16)
    seen: set[int] = set()
    queue: list[int] = [master_i]
    tex_home: dict[int, list] = {}
    tex_gpu: dict[int, list] = {}
    mat_home: dict[int, list] = {}
    master_hdr: ArchiveHeader | None = None
    n = 0
    parents_of_master: list[int] = []

    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        hs = f"{cur:016x}"
        fp = ARCHIVE_PRIMARY / hs
        if not fp.exists() or fp.stat().st_size in (44, 57):
            continue
        try:
            hdr = ArchiveHeader(fp)
        except Exception as exc:                 # noqa: BLE001
            if verbose:
                print(f"  skip {hs}: {exc}")
            continue
        n += 1
        ptex = hdr.collect(hdr.h0, TEXTURE_PRIM)
        pmat = hdr.collect(hdr.h0, MATERIAL_TYPE)
        ptexg = hdr.collect(hdr.h1, TEXTURE_GPU)
        for th, (p, s) in ptex.items():
            tex_home.setdefault(th, [hs, hdr.data_off, p, s])
        for th, (p, s) in ptexg.items():
            tex_gpu.setdefault(th, [p, s])
        for mh, (p, s) in pmat.items():
            mat_home.setdefault(mh, [hs, hdr.data_off, p, s])
        if cur == master_i:
            master_hdr = hdr
            parents_of_master = hdr.parents
        for pp in hdr.parents:
            if pp not in seen:
                queue.append(pp)
        if verbose and n % 40 == 0:
            print(f"  ... indexed {n} archives ({len(tex_home)} tex, {len(mat_home)} mat)")

    if master_hdr is None:
        raise RuntimeError(f"master {master_hash} not found / unreadable")

    scene_map = master_hdr.collect(master_hdr.h0, SCENE_TYPE)
    shd_map = master_hdr.collect(master_hdr.h0, SHADERSET_TYPE)
    if master_i not in scene_map:
        raise RuntimeError("no CGSceneResourceWin7 named == master hash")

    return dict(
        master=master_hash,
        data_off_master=master_hdr.data_off,
        scene_slice=list(scene_map[master_i]),
        shd_map={f"{k:016x}": list(v) for k, v in shd_map.items()},
        tex_home={f"{k:016x}": v for k, v in tex_home.items()},
        tex_gpu={f"{k:016x}": v for k, v in tex_gpu.items()},
        mat_home={f"{k:016x}": v for k, v in mat_home.items()},
        parents=[f"{p:016x}" for p in parents_of_master],
        n_archives=n,
    )


# ---------------------------------------------------------------------------
# Scene binding table
# ---------------------------------------------------------------------------

def parse_scene_binding(master_hash: str, data_off: int, scene_pos: int,
                        scene_size: int) -> tuple[list[str], list[str]]:
    """(mat_hashes[], shd_hashes[]) index-ordered. Ranged tail read of the scene
    slice (binding table is at its very end)."""
    raw = (ARCHIVE_PRIMARY / master_hash).read_bytes()
    lo = data_off + scene_pos
    win = min(scene_size, 4_000_000)             # binding block lives in the tail
    slc = decompress_range(raw, lo + scene_size - win, lo + scene_size)
    ok, note, res = sb.parse_binding_table(slc, 0, len(slc), set(), set())
    if not ok:
        raise RuntimeError(f"scene binding parse failed: {note}")
    return ([f"{h:016x}" for h in res["mat_hashes"]],
            [f"{h:016x}" for h in res["shd_hashes"]])


# ---------------------------------------------------------------------------
# Shaderset -> role textures  (4-byte-coverage; proven scanner reused)
# ---------------------------------------------------------------------------

def scan_shaderset_roles(slice_bytes: bytes, size: int, shd_hash: str,
                         tex_set: set[int], names: dict, pbr: dict) -> dict[str, str]:
    """{role_key -> tex_hash}. Runs the proven 8-byte-stride scanner over both the
    slice and slice[4:] so 4-byte-aligned SShaderInputData tables are covered."""
    rows = sts.scan_shaderset_slice(slice_bytes, 0, size, "", shd_hash,
                                    tex_set, names, pbr)
    rows += sts.scan_shaderset_slice(slice_bytes[4:], 0, size - 4, "", shd_hash,
                                     tex_set, names, pbr)
    return mat.roles_from_input_rows(rows, names)


# ---------------------------------------------------------------------------
# Texture extraction (ranged; reuse le_cross_archive_texture DDS handling)
# ---------------------------------------------------------------------------

def extract_texture(tex_hex: str, idx: dict, out_dir: Path) -> dict | None:
    """Extract one texture's DDS via ranged reads. Returns meta or None."""
    home = idx["tex_home"].get(tex_hex)
    if home is None:
        return None
    arch, arch_data_off, prim_pos, prim_size = home
    praw = (ARCHIVE_PRIMARY / arch).read_bytes()
    prim_slice = decompress_range(praw, arch_data_off + prim_pos,
                                  arch_data_off + prim_pos + prim_size)
    del praw
    tex_int = int(tex_hex, 16)
    gpu_entry = idx["tex_gpu"].get(tex_hex)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Streaming (GPU entry is a <=16-byte stub or missing): data in global packfile.
    if gpu_entry is None or gpu_entry[1] <= 0x10:
        result = s67.try_streaming_extract(tex_int, prim_slice)
        if result is None:
            return None
        dds_data, note = result
        (out_dir / f"{tex_hex}.dds").write_bytes(dds_data)
        meta = s67.parse_dds_meta(dds_data)
        meta.update(home_archive=arch, note=note, storage="streaming")
        return meta

    # Inline: DDS lives in the home GPU file at gpu_pos.
    gpu_pos, gpu_size = gpu_entry
    graw = (ARCHIVE_GPU / arch).read_bytes()
    gpu_slice = decompress_range(graw, gpu_pos, gpu_pos + gpu_size)
    del graw
    dds_off = s67.find_dds_in_slice(gpu_slice)
    if dds_off is None:
        return None
    dds_data = gpu_slice[dds_off:]
    (out_dir / f"{tex_hex}.dds").write_bytes(dds_data)
    meta = s67.parse_dds_meta(dds_data)
    meta.update(home_archive=arch, note=f"inline zero_prefix={dds_off}", storage="inline")
    return meta


# ---------------------------------------------------------------------------
# Material scalars (grouped by home archive for one raw-read per home)
# ---------------------------------------------------------------------------

def decode_all_material_scalars(mat_hexes: set[str], idx: dict) -> dict[str, dict]:
    by_home: dict[str, list[str]] = {}
    for mh in mat_hexes:
        home = idx["mat_home"].get(mh)
        if home:
            by_home.setdefault(home[0], []).append(mh)
    out: dict[str, dict] = {}
    for arch, mhs in by_home.items():
        raw = (ARCHIVE_PRIMARY / arch).read_bytes()
        for mh in mhs:
            _a, ado, pos, size = idx["mat_home"][mh]
            slc = decompress_range(raw, ado + pos, ado + pos + size)
            out[mh] = msc.decode_material_scalars(slc)
        del raw
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("master", help="static-scatter master archive hash")
    ap.add_argument("--manifest", type=Path, required=True,
                    help="the .lescatter/manifest.json (per-mesh matidx/shdidx)")
    ap.add_argument("--out-textures", type=Path, required=True)
    ap.add_argument("--out-json", type=Path, required=True)
    ap.add_argument("--no-textures", action="store_true",
                    help="resolve + write JSON but skip DDS extraction")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    master = args.master.lower()
    print(f"[1] indexing parent closure of {master} ...", flush=True)
    idx = build_index(master, verbose=args.verbose)
    print(f"    closure: {idx['n_archives']} archives "
          f"({len(idx['parents'])} direct parents); "
          f"{len(idx['tex_home'])} textures, {len(idx['mat_home'])} materials indexed")

    print("[2] parsing scene binding table ...", flush=True)
    scene_pos, scene_size = idx["scene_slice"]
    mat_hashes, shd_hashes = parse_scene_binding(
        master, idx["data_off_master"], scene_pos, scene_size)
    print(f"    binding: {len(mat_hashes)} materials, {len(shd_hashes)} shadersets")

    man = json.loads(args.manifest.read_text())
    # v2 packages carry EVERY draw per mesh; union all (matidx, shdidx) pairs
    # across draws so secondary-material bindings are resolved too. v1 manifests
    # (no "draws" key) fall back to the mesh's top-level pair.
    pair_set = set()
    for m in man["meshes"]:
        draws = m.get("draws")
        if draws:
            for d in draws:
                pair_set.add((d["matidx"], d["shdidx"]))
        else:
            pair_set.add((m["matidx"], m["shdidx"]))
    pairs = sorted(pair_set)
    print(f"[3] scatter uses {len(pairs)} distinct (matidx, shdidx) pairs")

    tex_set = {int(k, 16) for k in idx["tex_home"]}
    names: dict[int, str] = {}
    pbr = sts.build_pbr_hash_table(names)
    shd_map = {k: tuple(v) for k, v in idx["shd_map"].items()}

    # scan each distinct shaderset once
    print("[4] scanning shadersets for texture roles ...", flush=True)
    raw_master = (ARCHIVE_PRIMARY / master).read_bytes()
    data_off = idx["data_off_master"]
    roles_by_shd: dict[int, dict[str, str]] = {}
    for shdidx in sorted({p[1] for p in pairs}):
        if shdidx >= len(shd_hashes):
            roles_by_shd[shdidx] = {}
            continue
        shd_hex = shd_hashes[shdidx]
        ent = shd_map.get(shd_hex)
        if ent is None:
            roles_by_shd[shdidx] = {}
            continue
        p, s = ent
        d = decompress_range(raw_master, data_off + p, data_off + p + s)
        roles_by_shd[shdidx] = scan_shaderset_roles(d, s, shd_hex, tex_set, names, pbr)
    del raw_master

    # material scalars
    print("[5] decoding material scalars ...", flush=True)
    used_mats = {mat_hashes[mi] for mi, _ in pairs if mi < len(mat_hashes)}
    scalars = decode_all_material_scalars(used_mats, idx)

    # resolve per pair
    entries = []
    bc_to_extract: set[str] = set()
    nm_to_extract: set[str] = set()
    for matidx, shdidx in pairs:
        mat_hex = mat_hashes[matidx] if matidx < len(mat_hashes) else None
        roles = roles_by_shd.get(shdidx, {})
        chans = mat.classify_roles(roles, {})
        bc = chans.get("base_color")
        nm = chans.get("normal")
        bc_tex = bc["texture"] if bc else None
        nm_tex = nm["texture"] if nm else None
        if bc_tex:
            bc_to_extract.add(bc_tex)
        if nm_tex:
            nm_to_extract.add(nm_tex)
        sc = scalars.get(mat_hex, {})
        owning = idx["tex_home"].get(bc_tex, [None])[0] if bc_tex else None
        entries.append(dict(
            matidx=matidx, shdidx=shdidx, material_hash=mat_hex,
            mattype=int(sc.get("mattype", 0)),
            base_color=[float(x) for x in sc.get("base_color_factor", [1, 1, 1, 1])[:3]],
            basecolor_texture=bc_tex,
            basecolor_dds=None,          # filled after extraction
            basecolor_role=bc["role_key"] if bc else None,
            normal_texture=nm_tex,
            owning_archive=owning,
            double_sided=bool(sc.get("double_sided", False)),
        ))

    # extract textures
    tex_meta: dict[str, dict] = {}
    if not args.no_textures:
        want = bc_to_extract | nm_to_extract
        print(f"[6] extracting {len(want)} DDS "
              f"({len(bc_to_extract)} base-color, {len(nm_to_extract)} normal) ...", flush=True)
        ok = 0
        for i, tex_hex in enumerate(sorted(want)):
            meta = extract_texture(tex_hex, idx, args.out_textures)
            if meta:
                tex_meta[tex_hex] = meta
                ok += 1
            elif args.verbose:
                print(f"    FAIL {tex_hex}: no DDS (home={idx['tex_home'].get(tex_hex)})")
        print(f"    extracted {ok}/{len(want)} DDS -> {args.out_textures}")

    # stamp basecolor_dds relative path
    tex_dir_name = args.out_textures.name
    for e in entries:
        bt = e["basecolor_texture"]
        if bt and bt in tex_meta:
            e["basecolor_dds"] = f"{tex_dir_name}/{bt}.dds"

    out = dict(master=master, materials=entries)
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(out, indent=1))

    n_dds = sum(1 for e in entries if e["basecolor_dds"])
    n_scalar = sum(1 for e in entries if not e["basecolor_texture"])
    print(f"[7] wrote {args.out_json}")
    print(f"    {len(entries)} pairs: {n_dds} with base-color DDS, "
          f"{n_scalar} base-color-scalar-only, "
          f"{len(entries) - n_dds - n_scalar} unresolved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
