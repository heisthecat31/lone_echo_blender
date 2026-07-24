"""Phase 2b — extract textures from cross-archive homes.

Reads the combined shader scan TSV from Phase 2a and extracts DDS files from
all referenced external archives. Handles both inline GPU-slice textures and
streaming-packfile textures.

After this script runs, add exports/textures/ to TEX_DIRS in
the mesh-export module and update SCAN_TSV to point to
combined_shader_scan.tsv, then re-run the mesh export.

Inputs:
  generic_rebuilds/combined_shader_scan.tsv  (Phase 2a output)

Outputs:
  exports/textures/{tex_hash}.dds
  exports/textures/{tex_hash}_{inputname_decoded}.dds  (role-tagged copy)
  generic_rebuilds/texture_manifest.tsv

Run with Windows Python so le_oodle can load the game Oodle DLL:
    python.exe scripts/le_cross_archive_texture.py
    python.exe scripts/le_cross_archive_texture.py --verbose
    python.exe scripts/le_cross_archive_texture.py --archive 4c47d84c1e52447a
"""

from __future__ import annotations

import argparse
import csv
import struct
from collections import defaultdict
from pathlib import Path

from le_oodle import DATA_ROOT, load_decompressed
from le_archive_decode import (
    ARCHIVE_GPU,
    ARCHIVE_PRIMARY,
    entry_at,
    parse_header,
)


TEXTURE_PRIM_TYPE = 0xe8017b774f2b6327   # CGTextureResourceWin7
TEXTURE_GPU_TYPE  = 0xe2f9e022d8519ca9   # CGTextureResourceWin7GPU

# Packfile directory for streaming textures (global, not per-archive)
PACKFILE_DIR = DATA_ROOT / "primary" / "51e6cb2d64c65e4f" / "v4487852041"

STEXTURESTREAM_SIZE  = 192   # STextureStreamData: 3 × 16 uint32_t
OFF_MAXWIDTH         = STEXTURESTREAM_SIZE + 4
OFF_MAXHEIGHT        = STEXTURESTREAM_SIZE + 8
OFF_MAXMIPCOUNT      = STEXTURESTREAM_SIZE + 12
OFF_FORMAT           = STEXTURESTREAM_SIZE + 24

COMBINED_SCAN_TSV = Path("generic_rebuilds/combined_shader_scan.tsv")
OUT_DIR           = Path("exports/textures")
MANIFEST_TSV      = Path("generic_rebuilds/texture_manifest.tsv")

BRIDGE_ARCHIVE = "0703fd2acd5803e9"

# DXGI BC block bytes for DDS pitch calculation
BC_BLOCK_BYTES = {
    71: 8, 72: 8, 73: 8,    # BC1
    74: 16, 75: 16, 76: 16, # BC2
    77: 16, 78: 16, 79: 16, # BC3
    80: 8, 81: 8, 82: 8,    # BC4
    83: 16, 84: 16,          # BC5
    94: 16, 95: 16, 96: 16, # BC6H
    97: 16, 98: 16, 99: 16, # BC7
}

DDSD_CAPS = 0x1; DDSD_HEIGHT = 0x2; DDSD_WIDTH = 0x4
DDSD_PIXELFORMAT = 0x1000; DDSD_MIPMAPCOUNT = 0x20000; DDSD_LINEARSIZE = 0x80000
DDSCAPS_TEXTURE = 0x1000; DDSCAPS_MIPMAP = 0x400000; DDSCAPS_COMPLEX = 0x8
DDPF_FOURCC = 0x4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]

def u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]

def hex64(v: int) -> str:
    return f"{v:016x}"

def compressed_stub(path: Path) -> bool:
    return path.stat().st_size in (44, 57)


# ---------------------------------------------------------------------------
# Archive layout (primary-only, no GPU bounds check)
# ---------------------------------------------------------------------------

def compute_header_off(primary_bytes: bytes) -> tuple[int, int]:
    """Return (data_off, header_off) from the decompressed primary stream.

    Mirrors archive_offsets() but does not validate GPU bounds, so the GPU
    file does not need to be decompressed.
    """
    primary_size = u64(primary_bytes, 0)
    extra_skip   = u64(primary_bytes, 24)    # at offset 0x18
    data_off     = 32 + extra_skip           # = 0x20 + extra_skip
    header_off   = data_off + primary_size
    return data_off, header_off


def collect_resource_map(
    primary: bytes, header_off: int, type_hash: int
) -> dict[int, tuple[int, int]]:
    """Return name_hash -> (pos, size) for all matching type entries."""
    result: dict[int, tuple[int, int]] = {}
    off = header_off
    for _ in range(2):
        try:
            hdr = parse_header(primary, off)
        except Exception:
            break
        for i in range(hdr.contents.count):
            th, nh, value = struct.unpack_from("<QQQ", primary, hdr.contents.off + i * 24)
            if th == type_hash and value < hdr.entries.count:
                pos, size = entry_at(primary, hdr, value)
                result[nh] = (pos, size)
        off = hdr.end
    return result


# ---------------------------------------------------------------------------
# DDS helpers — inline extraction
# ---------------------------------------------------------------------------

def find_dds_in_slice(data: bytes) -> int | None:
    nz = next((i for i, b in enumerate(data) if b != 0), None)
    if nz is None:
        return None
    return nz if data[nz:nz+4] == b"DDS " else None


def parse_dds_meta(dds: bytes) -> dict:
    if len(dds) < 128 or dds[:4] != b"DDS ":
        return {}
    height = u32(dds, 12); width = u32(dds, 16); mips = u32(dds, 28)
    fourcc = dds[84:88]
    if fourcc == b"DX10" and len(dds) >= 148:
        dxgi_fmt = u32(dds, 128)
    else:
        dxgi_fmt = 0
    return {"dxgi_format": dxgi_fmt, "width": width, "height": height, "mip_count": mips}


# ---------------------------------------------------------------------------
# DDS helpers — streaming (packfile) extraction
# ---------------------------------------------------------------------------

def build_dds_header(width: int, height: int, mip_count: int, dxgi_format: int) -> bytes:
    block_bytes = BC_BLOCK_BYTES.get(dxgi_format, 16)
    bw = max(1, (width + 3) // 4)
    bh = max(1, (height + 3) // 4)
    linear_size = bw * bh * block_bytes
    flags = DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT | DDSD_LINEARSIZE | DDSD_MIPMAPCOUNT
    caps  = DDSCAPS_TEXTURE | DDSCAPS_COMPLEX | DDSCAPS_MIPMAP
    ddspf = struct.pack("<IIIIIIII", 32, DDPF_FOURCC, 0x30315844, 0, 0, 0, 0, 0)
    dds_hdr = struct.pack("<IIIII I I 11I", 124, flags, height, width, linear_size,
                          0, mip_count, *([0]*11)) + ddspf + struct.pack("<IIIII", caps, 0, 0, 0, 0)
    dxt10 = struct.pack("<IIIII", dxgi_format, 3, 0, 1, 0)
    return b"DDS " + dds_hdr + dxt10


def try_streaming_extract(tex_hash_int: int, prim_slice: bytes) -> tuple[bytes, str] | None:
    """Build DDS from streaming packfile. Returns (dds_bytes, note) or None on failure."""
    if len(prim_slice) < 232:
        return None

    rev_offsets = list(struct.unpack_from("<16I", prim_slice,   0))
    rev_sizes   = list(struct.unpack_from("<16I", prim_slice, 128))
    maxmipcount = u32(prim_slice, OFF_MAXMIPCOUNT)
    maxwidth    = u32(prim_slice, OFF_MAXWIDTH)
    maxheight   = u32(prim_slice, OFF_MAXHEIGHT)
    dxgi_fmt    = u32(prim_slice, OFF_FORMAT)

    if maxmipcount == 0 or maxmipcount > 16:
        return None

    # Collect streaming mips (largest first in DDS order)
    streaming = []
    for i in range(maxmipcount - 1, -1, -1):
        off_val = rev_offsets[i]
        raw_val = rev_sizes[i]
        if off_val != 0xffffffff and raw_val > 0:
            streaming.append((off_val, raw_val))
    if not streaming:
        return None

    packfile_path = PACKFILE_DIR / hex64(tex_hash_int)
    if not packfile_path.exists():
        return None
    packfile = packfile_path.read_bytes()
    if len(packfile) < 4:
        return None

    mip_blobs = []
    for pf_off, raw_sz in streaming:
        start = 4 + pf_off
        end   = start + raw_sz
        if end > len(packfile):
            return None
        mip_blobs.append(packfile[start:end])

    dds_hdr = build_dds_header(maxwidth, maxheight, len(streaming), dxgi_fmt)
    return dds_hdr + b"".join(mip_blobs), f"streaming_mips={len(streaming)} of {maxmipcount}"


# ---------------------------------------------------------------------------
# Per-archive extraction
# ---------------------------------------------------------------------------

def extract_from_archive(
    archive_hash: str,
    tex_hashes: list[int],
    out_dir: Path,
    verbose: bool = False,
) -> dict[int, dict]:
    """Extract textures from one archive. Returns tex_hash_int -> meta dict."""
    primary_path = ARCHIVE_PRIMARY / archive_hash
    gpu_path     = ARCHIVE_GPU / archive_hash

    results: dict[int, dict] = {h: {"ok": False, "note": "not started"} for h in tex_hashes}

    if not primary_path.exists() or compressed_stub(primary_path):
        for h in tex_hashes:
            results[h] = {"ok": False, "note": "primary missing or stub"}
        return results

    try:
        primary_bytes = load_decompressed(primary_path)
    except Exception as exc:
        for h in tex_hashes:
            results[h] = {"ok": False, "note": f"primary decomp error: {exc}"}
        return results

    try:
        data_off, header_off = compute_header_off(primary_bytes)
    except Exception as exc:
        for h in tex_hashes:
            results[h] = {"ok": False, "note": f"header offset error: {exc}"}
        return results

    prim_map = collect_resource_map(primary_bytes, header_off, TEXTURE_PRIM_TYPE)
    gpu_map  = collect_resource_map(primary_bytes, header_off, TEXTURE_GPU_TYPE)

    # Decompress GPU only if needed (at least one texture needs inline extraction)
    gpu_bytes: bytes | None = None
    has_gpu = gpu_path.exists() and not compressed_stub(gpu_path)

    if verbose:
        print(f"  archive {archive_hash}: {len(prim_map)} prim / {len(gpu_map)} gpu entries")

    out_dir.mkdir(parents=True, exist_ok=True)

    for tex_int in tex_hashes:
        tex_str = hex64(tex_int)

        prim_entry = prim_map.get(tex_int)
        if prim_entry is None:
            results[tex_int] = {"ok": False, "note": "not in archive prim_map"}
            continue

        prim_pos, prim_size = prim_entry
        prim_slice = primary_bytes[data_off + prim_pos : data_off + prim_pos + prim_size]

        gpu_entry = gpu_map.get(tex_int)

        # Streaming: GPU slice is very small (≤ 0x10 bytes) or GPU entry missing
        gpu_is_stub = (gpu_entry is not None and gpu_entry[1] <= 0x10)
        gpu_missing = (gpu_entry is None)

        if gpu_is_stub or gpu_missing:
            # Streaming texture — data in global packfile directory
            result = try_streaming_extract(tex_int, prim_slice)
            if result is not None:
                dds_data, note = result
                (out_dir / f"{tex_str}.dds").write_bytes(dds_data)
                meta = parse_dds_meta(dds_data)
                if verbose:
                    print(f"  {tex_str}: streaming {meta.get('width')}×{meta.get('height')} "
                          f"fmt={meta.get('dxgi_format')} {note}")
                results[tex_int] = {"ok": True, "note": note, **meta}
            else:
                reason = "GPU stub, packfile not found" if gpu_is_stub else "GPU entry missing, packfile not found"
                results[tex_int] = {"ok": False, "note": reason}
            continue

        # Inline texture — need GPU bytes
        if gpu_bytes is None and has_gpu:
            try:
                gpu_bytes = load_decompressed(gpu_path)
            except Exception as exc:
                has_gpu = False
                gpu_bytes = None
                if verbose:
                    print(f"  WARNING: GPU decomp failed for {archive_hash}: {exc}")

        if gpu_bytes is None:
            results[tex_int] = {"ok": False, "note": "GPU file unavailable"}
            continue

        gpu_pos, gpu_size = gpu_entry
        gpu_slice = gpu_bytes[gpu_pos : gpu_pos + gpu_size]

        dds_off = find_dds_in_slice(gpu_slice)
        if dds_off is None:
            results[tex_int] = {"ok": False, "note": f"no DDS magic in GPU slice (size={gpu_size:#x})"}
            continue

        dds_data = gpu_slice[dds_off:]
        meta = parse_dds_meta(dds_data)
        (out_dir / f"{tex_str}.dds").write_bytes(dds_data)
        if verbose:
            print(f"  {tex_str}: inline {meta.get('width')}×{meta.get('height')} "
                  f"fmt={meta.get('dxgi_format')} zero_prefix={dds_off}")
        results[tex_int] = {"ok": True, "note": f"inline zero_prefix={dds_off}", **meta}

    return results


# ---------------------------------------------------------------------------
# Role-tagged copies
# ---------------------------------------------------------------------------

def make_role_copies(tex_roles: dict[str, str], out_dir: Path) -> None:
    """Write {hash}_{decoded_role}.dds copies for extracted textures."""
    for tex_str, role in tex_roles.items():
        src = out_dir / f"{tex_str}.dds"
        dst = out_dir / f"{tex_str}_{role}.dds"
        if src.exists() and not dst.exists():
            dst.write_bytes(src.read_bytes())


# ---------------------------------------------------------------------------
# TSV I/O
# ---------------------------------------------------------------------------

def load_scan(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


MANIFEST_FIELDS = [
    "tex_hash", "source_archive", "inputname_decoded",
    "dxgi_format", "width", "height", "mip_count",
    "dds_file", "extraction_ok", "note",
]


def write_manifest(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=MANIFEST_FIELDS, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scan-tsv",  type=Path, default=COMBINED_SCAN_TSV)
    parser.add_argument("--out-dir",   type=Path, default=OUT_DIR)
    parser.add_argument("--manifest",  type=Path, default=MANIFEST_TSV)
    parser.add_argument("--archive",   help="only extract from this specific archive hash")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    print(f"loading combined scan TSV: {args.scan_tsv}")
    scan_rows = load_scan(args.scan_tsv)
    print(f"  {len(scan_rows)} scan rows")

    # Group cross-archive textures by their home archive.
    # Skip bridge-local textures (already extracted by the bridge-local pass).
    # Use a set to deduplicate across rows.
    seen_tex: set[int] = set()
    by_archive: dict[str, list[int]] = defaultdict(list)
    tex_first_role: dict[str, str] = {}

    for row in scan_rows:
        arch = row.get("texture_archive_hash", "").lower()
        in_bridge_str = row.get("texture_in_archive", "False").lower()
        tex_str = row.get("textureassetid_hash", "").lower()
        decoded = row.get("inputname_decoded", "unknown")
        if not arch or not tex_str:
            continue
        if in_bridge_str == "true" and arch == BRIDGE_ARCHIVE:
            continue   # already extracted by the bridge-local pass
        if args.archive and arch != args.archive.lower():
            continue
        try:
            tex_int = int(tex_str, 16)
        except ValueError:
            continue
        if tex_int not in seen_tex:
            seen_tex.add(tex_int)
            by_archive[arch].append(tex_int)
        if tex_str not in tex_first_role and decoded != "unknown":
            tex_first_role[tex_str] = decoded

    total_unique = sum(len(v) for v in by_archive.values())
    print(f"\nCross-archive textures to extract: {total_unique} unique across {len(by_archive)} archives")
    for arch, hlist in sorted(by_archive.items(), key=lambda x: -len(x[1])):
        print(f"  {arch}: {len(hlist)} textures")

    if total_unique == 0:
        print("Nothing to extract — run Phase 1 and 2a first, or check filter args.")
        return

    # Extract per archive
    all_results:      dict[int, dict] = {}
    all_result_arch:  dict[int, str]  = {}

    for archive_hash, tex_ints in by_archive.items():
        print(f"\nextracting from {archive_hash} ({len(tex_ints)} textures) ...", flush=True)
        results = extract_from_archive(archive_hash, tex_ints, args.out_dir, verbose=args.verbose)
        for h, meta in results.items():
            all_results[h] = meta
            all_result_arch[h] = archive_hash

    # Role-tagged copies
    make_role_copies(tex_first_role, args.out_dir)

    # Summary
    n_ok   = sum(1 for m in all_results.values() if m.get("ok"))
    n_fail = len(all_results) - n_ok
    print(f"\nExtraction summary: {n_ok} ok / {n_fail} failed")
    if n_fail > 0:
        for h in sorted(all_results):
            meta = all_results[h]
            if not meta.get("ok"):
                arch = all_result_arch.get(h, "?")
                print(f"  FAIL {hex64(h)} from {arch}: {meta.get('note')}")

    # Manifest
    manifest_rows = []
    for h in sorted(all_results):
        meta = all_results[h]
        tex_str  = hex64(h)
        role     = tex_first_role.get(tex_str, "unknown")
        arch     = all_result_arch.get(h, "?")
        ok       = meta.get("ok", False)
        dds_path = str(args.out_dir / f"{tex_str}.dds") if ok else ""
        manifest_rows.append({
            "tex_hash":          tex_str,
            "source_archive":    arch,
            "inputname_decoded": role,
            "dxgi_format":       meta.get("dxgi_format", 0),
            "width":             meta.get("width", 0),
            "height":            meta.get("height", 0),
            "mip_count":         meta.get("mip_count", 0),
            "dds_file":          dds_path,
            "extraction_ok":     ok,
            "note":              meta.get("note", ""),
        })
    write_manifest(args.manifest, manifest_rows)
    print(f"wrote manifest: {args.manifest} ({len(manifest_rows)} rows)")

    print("\nNext steps:")
    print("  1. In full_mesh_export.py:")
    print("       TEX_DIRS = [Path('exports/textures'), Path('exports/textures'), ...]")
    print("       SCAN_TSV = Path('generic_rebuilds/combined_shader_scan.tsv')")
    print("  2. Re-run: python.exe scripts/full_mesh_export.py")


if __name__ == "__main__":
    main()
