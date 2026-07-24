"""P4 full texture extraction — joins binding and shader-set scan TSVs.

Strategy:
  1. Load scene_binding_parse.tsv (mesh list → material + shaderset hash lists).
  2. Load shaderset_texture_scan.tsv (shaderset hash → texture bindings).
  3. Re-decode inputname hashes using current hash_lookup.json.
  4. For each unique texture hash referenced by bridge-archive shader sets, extract
     the CGTextureResourceWin7GPU GPU slice from the archive and write as a DDS file.
  5. Parse the DDS header to record format/dimensions.
  6. Write per-mesh manifest TSV: mesh × shaderset × inputname × texture.

CGTextureResourceData primary slice layout:
  [0..192)  STextureStreamData  (3 × 16 uint32_t: mipoffsets, cmpmipsizes, mipsizes)
  [192]     uint32_t streamingdisabled
  [196]     uint32_t maxwidth
  [200]     uint32_t maxheight
  [204]     uint32_t maxmipcount
  [208]     uint32_t arraysize
  [212]     uint32_t cubemap
  [216]     uint32_t format           (DXGI_FORMAT)
  [220]     uint32_t srgb_or_tilemode
  [224]     uint32_t createasarray
  [228]     uint32_t volume

CGTextureResourceWin7GPU GPU slice layout:
  [0..N)   zero prefix (variable length; N is typically 48–176 bytes)
  [N..)    DDS file (DDS_HEADER 124 B + DDS_HEADER_DXT10 20 B + BC block data)

Run with Windows Python so le_oodle can load the game Oodle DLL:
    python.exe scripts/le_texture_extract.py
    python.exe scripts/le_texture_extract.py --hash 0703fd2acd5803e9
    python.exe scripts/le_texture_extract.py --verbose
"""

from __future__ import annotations

import argparse
import csv
import struct
from dataclasses import dataclass
from pathlib import Path

from le_oodle import load_decompressed
from le_archive_decode import (
    ARCHIVE_GPU,
    ARCHIVE_PRIMARY,
    DEFAULT_HASH_LOOKUP,
    archive_offsets,
    entry_at,
    load_hash_lookup,
    parse_header,
)


TEXTURE_PRIMARY_TYPE = 0xe8017b774f2b6327   # CGTextureResourceWin7
TEXTURE_GPU_TYPE     = 0xe2f9e022d8519ca9   # CGTextureResourceWin7GPU

# STextureStreamData: 3 arrays of 16 uint32_t = 192 bytes
STEXTURESTREAM_SIZE  = 192
# Scalar fields in CGTextureResourceData after STextureStreamData
OFF_STREAMING_DISABLED = STEXTURESTREAM_SIZE + 0   # uint32_t
OFF_MAXWIDTH           = STEXTURESTREAM_SIZE + 4   # uint32_t
OFF_MAXHEIGHT          = STEXTURESTREAM_SIZE + 8   # uint32_t
OFF_MAXMIPCOUNT        = STEXTURESTREAM_SIZE + 12  # uint32_t
OFF_ARRAYSIZE          = STEXTURESTREAM_SIZE + 16  # uint32_t
OFF_CUBEMAP            = STEXTURESTREAM_SIZE + 20  # uint32_t
OFF_FORMAT             = STEXTURESTREAM_SIZE + 24  # uint32_t  DXGI_FORMAT
OFF_SRGB_TILEMODE      = STEXTURESTREAM_SIZE + 28  # uint32_t
OFF_CREATEASARRAY      = STEXTURESTREAM_SIZE + 32  # uint32_t
OFF_VOLUME             = STEXTURESTREAM_SIZE + 36  # uint32_t

DEFAULT_ARCHIVE   = "0703fd2acd5803e9"
DEFAULT_OUT_DIR   = Path("exports/textures")
DEFAULT_MANIFEST  = Path("generic_rebuilds/texture_manifest.tsv")
BINDING_TSV       = Path("generic_rebuilds/scene_binding_parse.tsv")
SCAN_TSV          = Path("generic_rebuilds/shaderset_texture_scan.tsv")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def hex64(v: int) -> str:
    return f"{v:016x}"


def compressed_stub(path: Path) -> bool:
    return path.stat().st_size in (44, 57)


def collect_resource_map(
    primary: bytes, header_off: int, type_hash: int
) -> dict[int, tuple[int, int, int]]:
    """Return name_hash -> (entry_index, pos, size) for all matching type entries."""
    result: dict[int, tuple[int, int, int]] = {}
    header = None
    for hdr_idx in range(2):
        header = parse_header(primary, header_off if hdr_idx == 0 else header.end)
        for i in range(header.contents.count):
            th, nh, value = struct.unpack_from(
                "<QQQ", primary, header.contents.off + i * 24
            )
            if th == type_hash and value < header.entries.count:
                pos, size = entry_at(primary, header, value)
                result[nh] = (value, pos, size)
    return result


# ---------------------------------------------------------------------------
# DDS helpers
# ---------------------------------------------------------------------------

def find_dds_in_slice(data: bytes) -> int | None:
    """Return the byte offset of DDS magic within data, or None."""
    nz = next((i for i, b in enumerate(data) if b != 0), None)
    if nz is None:
        return None
    if data[nz:nz+4] == b"DDS ":
        return nz
    return None


def parse_dds_header(dds: bytes) -> dict:
    """Parse DDS_HEADER and optional DX10 extension; return metadata dict."""
    if len(dds) < 128 or dds[:4] != b"DDS ":
        return {}
    height   = u32(dds, 12)
    width    = u32(dds, 16)
    mips     = u32(dds, 28)
    fourcc   = dds[84:88]
    if fourcc == b"DX10" and len(dds) >= 148:
        dxgi_fmt = u32(dds, 128)
        arr_size = u32(dds, 140)
    else:
        dxgi_fmt = 0
        arr_size = 1
    return {
        "dxgi_format":  dxgi_fmt,
        "width":        width,
        "height":       height,
        "mip_count":    mips,
        "array_size":   arr_size,
    }


# ---------------------------------------------------------------------------
# Primary metadata parse
# ---------------------------------------------------------------------------

def parse_texture_metadata(prim_slice: bytes) -> dict:
    """Read CGTextureResourceData scalar fields from a primary texture slice."""
    if len(prim_slice) < STEXTURESTREAM_SIZE + 40:
        return {}
    return {
        "streaming_disabled": u32(prim_slice, OFF_STREAMING_DISABLED),
        "maxwidth":           u32(prim_slice, OFF_MAXWIDTH),
        "maxheight":          u32(prim_slice, OFF_MAXHEIGHT),
        "maxmipcount":        u32(prim_slice, OFF_MAXMIPCOUNT),
        "dxgi_format":        u32(prim_slice, OFF_FORMAT),
        "cubemap":            u32(prim_slice, OFF_CUBEMAP),
        "volume":             u32(prim_slice, OFF_VOLUME),
    }


# ---------------------------------------------------------------------------
# TSV loading
# ---------------------------------------------------------------------------

def load_binding_tsv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def load_scan_tsv(path: Path, names: dict[int, str]) -> list[dict]:
    """Load scan TSV and re-decode inputname hashes with current hash_lookup."""
    rows = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            try:
                ih = int(row["inputname_hash"], 16)
            except ValueError:
                ih = 0
            decoded = names.get(ih, "unknown")
            row["inputname_decoded"] = decoded
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Join logic
# ---------------------------------------------------------------------------

@dataclass
class ManifestRow:
    archive_hash:      str
    meshlist_hash:     str
    shadersetidx:      int
    shaderset_hash:    str
    inputname_hash:    str
    inputname_decoded: str
    slot:              int
    layer:             int
    uscale:            str
    vscale:            str
    textureassetid:    str
    dxgi_format:       int
    width:             int
    height:            int
    mip_count:         int
    dds_file:          str
    extraction_ok:     bool
    note:              str


def build_manifest(
    binding_rows: list[dict],
    scan_rows:    list[dict],
) -> list[ManifestRow]:
    """Cross-join binding and scan TSVs to get per-mesh texture assignments."""
    # Build shaderset_hash -> list[scan_row] map
    shd_to_tex: dict[str, list[dict]] = {}
    for r in scan_rows:
        shd_hash = r["shaderset_hash"].lower()
        shd_to_tex.setdefault(shd_hash, []).append(r)

    rows: list[ManifestRow] = []
    for br in binding_rows:
        if br.get("parse_ok", "False") != "True":
            continue
        archive_hash   = br["archive_hash"]
        meshlist_hash  = br["meshlist_hash"]
        shd_hashes_str = br.get("shaderset_hashes", "")
        if not shd_hashes_str:
            continue
        shd_hashes = [h.strip().lower() for h in shd_hashes_str.split(";") if h.strip()]

        for shadersetidx, shd_hash in enumerate(shd_hashes):
            tex_entries = shd_to_tex.get(shd_hash, [])
            for te in tex_entries:
                if te.get("texture_in_archive", "False") != "True":
                    continue
                try:
                    slot  = int(te["slot"])
                    layer = int(te["layer"])
                except ValueError:
                    slot = layer = -1
                rows.append(ManifestRow(
                    archive_hash=archive_hash,
                    meshlist_hash=meshlist_hash,
                    shadersetidx=shadersetidx,
                    shaderset_hash=shd_hash,
                    inputname_hash=te["inputname_hash"],
                    inputname_decoded=te["inputname_decoded"],
                    slot=slot,
                    layer=layer,
                    uscale=te.get("uscale", ""),
                    vscale=te.get("vscale", ""),
                    textureassetid=te["textureassetid_hash"].lower(),
                    dxgi_format=0,
                    width=0,
                    height=0,
                    mip_count=0,
                    dds_file="",
                    extraction_ok=False,
                    note="",
                ))
    return rows


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_textures(
    archive_hash: str,
    manifest_rows: list[ManifestRow],
    out_dir: Path,
    verbose: bool = False,
) -> dict[str, dict]:
    """Extract unique textures; return tex_hash -> DDS metadata dict."""
    primary_path = ARCHIVE_PRIMARY / archive_hash
    gpu_path     = ARCHIVE_GPU / archive_hash

    if compressed_stub(primary_path) or compressed_stub(gpu_path):
        print(f"  archive {archive_hash}: compressed stub, skipping")
        return {}

    primary_bytes = load_decompressed(primary_path)
    gpu_bytes     = load_decompressed(gpu_path)
    _prim_size, _gpu_size, data_off, header_off = archive_offsets(primary_bytes, gpu_bytes)

    # Collect primary and GPU texture resource maps
    prim_map = collect_resource_map(primary_bytes, header_off, TEXTURE_PRIMARY_TYPE)
    gpu_map  = collect_resource_map(primary_bytes, header_off, TEXTURE_GPU_TYPE)

    if verbose:
        print(f"  primary texture resources: {len(prim_map)}")
        print(f"  GPU texture resources:     {len(gpu_map)}")

    # Unique texture hashes needed by this archive's manifest rows
    needed = {
        int(r.textureassetid, 16)
        for r in manifest_rows
        if r.archive_hash == archive_hash
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    tex_meta: dict[str, dict] = {}

    for tex_int in sorted(needed):
        tex_str = hex64(tex_int)

        # --- Primary metadata ---
        prim_entry = prim_map.get(tex_int)
        prim_meta: dict = {}
        if prim_entry is not None:
            _idx, pos, size = prim_entry
            prim_slice = primary_bytes[data_off + pos : data_off + pos + size]
            prim_meta = parse_texture_metadata(prim_slice)

        # --- GPU slice extraction ---
        gpu_entry = gpu_map.get(tex_int)
        if gpu_entry is None:
            if verbose:
                print(f"  texture {tex_str}: no GPU entry found")
            tex_meta[tex_str] = {**prim_meta, "extraction_ok": False, "note": "no GPU entry"}
            continue

        _idx, gpu_pos, gpu_size = gpu_entry
        gpu_slice = gpu_bytes[gpu_pos : gpu_pos + gpu_size]

        dds_off = find_dds_in_slice(gpu_slice)
        if dds_off is None:
            if verbose:
                print(f"  texture {tex_str}: DDS magic not found in GPU slice (size {gpu_size:#x})")
            tex_meta[tex_str] = {**prim_meta, "extraction_ok": False, "note": "no DDS magic"}
            continue

        dds_data = gpu_slice[dds_off:]
        dds_info = parse_dds_header(dds_data)

        # Choose filename: tex_hash (role names filled in from manifest rows below)
        out_path = out_dir / f"{tex_str}.dds"
        out_path.write_bytes(dds_data)

        meta = {
            **prim_meta,
            **dds_info,
            "extraction_ok": True,
            "note": f"zero_prefix={dds_off}",
            "dds_path": str(out_path),
        }
        tex_meta[tex_str] = meta

        if verbose:
            w = dds_info.get("width", 0)
            h = dds_info.get("height", 0)
            m = dds_info.get("mip_count", 0)
            fmt = dds_info.get("dxgi_format", 0)
            print(f"  texture {tex_str}: {w}×{h} {m}mips fmt={fmt} "
                  f"zero_prefix={dds_off} -> {out_path.name}")

    return tex_meta


# ---------------------------------------------------------------------------
# Rename extracted DDS files with role tag
# ---------------------------------------------------------------------------

def rename_with_roles(
    manifest_rows: list[ManifestRow],
    out_dir: Path,
    tex_meta: dict[str, dict],
) -> None:
    """
    Add a role-tagged copy of each DDS named {hash}_{inputname_decoded}.dds.
    The plain {hash}.dds file is kept; the tagged copy is the primary reference.
    Only the first observed role name per texture is used for the tagged copy.
    """
    # Gather first decoded role per texture
    tex_role: dict[str, str] = {}
    for r in manifest_rows:
        h = r.textureassetid
        if h not in tex_role and r.inputname_decoded != "unknown":
            tex_role[h] = r.inputname_decoded

    for tex_str, role in tex_role.items():
        src = out_dir / f"{tex_str}.dds"
        dst = out_dir / f"{tex_str}_{role}.dds"
        if src.exists() and not dst.exists():
            dst.write_bytes(src.read_bytes())


# ---------------------------------------------------------------------------
# Manifest row population
# ---------------------------------------------------------------------------

def populate_manifest(
    manifest_rows: list[ManifestRow],
    tex_meta: dict[str, dict],
    out_dir: Path,
) -> None:
    for r in manifest_rows:
        meta = tex_meta.get(r.textureassetid, {})
        r.extraction_ok  = meta.get("extraction_ok", False)
        r.note           = meta.get("note", "not attempted")
        r.dxgi_format    = meta.get("dxgi_format", 0)
        r.width          = meta.get("width", 0)
        r.height         = meta.get("height", 0)
        r.mip_count      = meta.get("mip_count", 0)
        if r.extraction_ok:
            role = r.inputname_decoded if r.inputname_decoded != "unknown" else None
            if role:
                fname = f"{r.textureassetid}_{role}.dds"
            else:
                fname = f"{r.textureassetid}.dds"
            r.dds_file = str(out_dir / fname)


# ---------------------------------------------------------------------------
# TSV output
# ---------------------------------------------------------------------------

MANIFEST_FIELDS = [
    "archive_hash", "meshlist_hash", "shadersetidx", "shaderset_hash",
    "inputname_hash", "inputname_decoded", "slot", "layer",
    "uscale", "vscale", "textureassetid",
    "dxgi_format", "width", "height", "mip_count",
    "dds_file", "extraction_ok", "note",
]


def write_manifest_tsv(path: Path, rows: list[ManifestRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(MANIFEST_FIELDS)
        for r in rows:
            writer.writerow([
                r.archive_hash, r.meshlist_hash, r.shadersetidx, r.shaderset_hash,
                r.inputname_hash, r.inputname_decoded, r.slot, r.layer,
                r.uscale, r.vscale, r.textureassetid,
                r.dxgi_format, r.width, r.height, r.mip_count,
                r.dds_file, r.extraction_ok, r.note,
            ])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hash",     default=DEFAULT_ARCHIVE, help="archive hash")
    parser.add_argument("--out-dir",  type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--binding-tsv", type=Path, default=BINDING_TSV)
    parser.add_argument("--scan-tsv",    type=Path, default=SCAN_TSV)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    names = load_hash_lookup(DEFAULT_HASH_LOOKUP)
    print(f"hash_lookup: {len(names)} entries")

    print(f"loading binding TSV: {args.binding_tsv}")
    binding_rows = load_binding_tsv(args.binding_tsv)
    print(f"  {len(binding_rows)} mesh list rows")

    print(f"loading scan TSV: {args.scan_tsv}")
    scan_rows = load_scan_tsv(args.scan_tsv, names)
    decoded_scan = sum(1 for r in scan_rows if r["inputname_decoded"] != "unknown")
    print(f"  {len(scan_rows)} texture binding entries ({decoded_scan} with decoded inputnames)")

    print("building per-mesh manifest ...")
    manifest_rows = build_manifest(binding_rows, scan_rows)
    print(f"  {len(manifest_rows)} manifest rows across "
          f"{len({r.meshlist_hash for r in manifest_rows})} mesh lists")
    unique_tex = {r.textureassetid for r in manifest_rows}
    print(f"  {len(unique_tex)} unique textures referenced")

    print(f"\nextracting textures from archive {args.hash} -> {args.out_dir}")
    tex_meta = extract_textures(args.hash, manifest_rows, args.out_dir, verbose=args.verbose)
    ok_count = sum(1 for m in tex_meta.values() if m.get("extraction_ok"))
    print(f"  extracted {ok_count} of {len(tex_meta)} unique textures successfully")

    rename_with_roles(manifest_rows, args.out_dir, tex_meta)
    populate_manifest(manifest_rows, tex_meta, args.out_dir)
    write_manifest_tsv(args.manifest, manifest_rows)
    print(f"\nwrote manifest: {args.manifest} ({len(manifest_rows)} rows)")

    # Summary by mesh
    print("\nPer-mesh texture summary:")
    by_mesh: dict[str, list[ManifestRow]] = {}
    for r in manifest_rows:
        by_mesh.setdefault(r.meshlist_hash, []).append(r)
    for mh, rows in sorted(by_mesh.items()):
        ok_rows = [r for r in rows if r.extraction_ok]
        roles = sorted({r.inputname_decoded for r in ok_rows})
        print(f"  {mh}: {len(rows)} bindings ({len(ok_rows)} extracted) roles={roles}")


if __name__ == "__main__":
    main()
