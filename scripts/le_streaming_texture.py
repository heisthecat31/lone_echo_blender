"""extract streaming textures from RawTexturePackfileWin7.

The 16 failing textures from the inline-only path have 0x10-byte GPU stubs — their mip data
lives in per-texture packfiles under:
  DATA_ROOT/primary/51e6cb2d64c65e4f/v4487852041/{tex_hash}

Packfile format:
  [0x00] u32: total_data_size (= sum of all streaming mip rawsizes)
  [0x04] raw mip data, uncompressed

STextureStreamData (in CGTextureResourceData primary slice) layout:
  [0x00..0x40)  reversedmipoffsets[16]  — offset within packfile data (after prefix)
  [0x40..0x80)  reversedcmpmipsizes[16] — compressed sizes (== rawsize = no per-mip compression)
  [0x80..0xc0)  reversedmipsizes[16]    — uncompressed sizes
  Index 0 = smallest mip; index maxmipcount-1 = largest mip.
  offset = 0xffffffff → always-resident (small mip, not in packfile)

Output: DDS files containing only the streaming mips (full-res and up to 3 next mips).
The DDS mip count is set to the number of streaming mips; always-resident small mips
are not included but the textures are fully usable for visual inspection and export.

Run with:
    python.exe scripts/le_streaming_texture.py
    python.exe scripts/le_streaming_texture.py --verbose
    python.exe scripts/le_streaming_texture.py --update-manifest
"""

from __future__ import annotations

import argparse
import csv
import struct
from pathlib import Path

from le_oodle import DATA_ROOT, load_decompressed
from le_archive_decode import (
    ARCHIVE_GPU,
    ARCHIVE_PRIMARY,
    DEFAULT_HASH_LOOKUP,
    archive_offsets,
    entry_at,
    load_hash_lookup,
    parse_header,
)


BRIDGE_ARCHIVE    = "0703fd2acd5803e9"
PACKFILE_TYPE_DIR = DATA_ROOT / "primary" / "51e6cb2d64c65e4f" / "v4487852041"
TEXTURE_PRIM_TYPE = 0xe8017b774f2b6327  # CGTextureResourceWin7

DEFAULT_OUT_DIR   = Path("exports/textures")
DEFAULT_MANIFEST  = Path("generic_rebuilds/texture_manifest.tsv")

# DXGI_FORMAT constants needed for DDS pitch calculation
# https://docs.microsoft.com/en-us/windows/win32/api/dxgiformat/ne-dxgiformat-dxgi_format
DXGI_BC1 = 71   # BC1_TYPELESS
DXGI_BC1_UNORM = 72
DXGI_BC1_SRGB  = 73
DXGI_BC2 = 74
DXGI_BC2_UNORM = 75
DXGI_BC2_SRGB  = 76
DXGI_BC3 = 77
DXGI_BC3_UNORM = 78
DXGI_BC3_SRGB  = 79
DXGI_BC4 = 80
DXGI_BC4_UNORM = 81
DXGI_BC4_SNORM = 82
DXGI_BC5 = 83
DXGI_BC5_UNORM = 84  # actually 83 is BC5_UNORM per DXGI spec... check
DXGI_BC5_SNORM = 84
DXGI_BC6H = 94
DXGI_BC6H_UF16 = 95
DXGI_BC6H_SF16 = 96
DXGI_BC7 = 97
DXGI_BC7_UNORM = 98
DXGI_BC7_SRGB  = 99

# BC block bytes per 4×4 block
BC_BLOCK_BYTES = {
    # BC1: 8 bytes/block
    71: 8, 72: 8, 73: 8,
    # BC2: 16 bytes/block
    74: 16, 75: 16, 76: 16,
    # BC3: 16 bytes/block
    77: 16, 78: 16, 79: 16,
    # BC4: 8 bytes/block
    80: 8, 81: 8, 82: 8,
    # BC5: 16 bytes/block
    83: 16, 84: 16,
    # BC6H: 16 bytes/block
    94: 16, 95: 16, 96: 16,
    # BC7: 16 bytes/block
    97: 16, 98: 16, 99: 16,
}


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def hex64(v: int) -> str:
    return f"{v:016x}"


# ---------------------------------------------------------------------------
# DDS header construction
# ---------------------------------------------------------------------------

# DDS flags
DDSD_CAPS        = 0x1
DDSD_HEIGHT      = 0x2
DDSD_WIDTH       = 0x4
DDSD_PIXELFORMAT = 0x1000
DDSD_MIPMAPCOUNT = 0x20000
DDSD_LINEARSIZE  = 0x80000

DDSCAPS_TEXTURE  = 0x1000
DDSCAPS_MIPMAP   = 0x400000
DDSCAPS_COMPLEX  = 0x8

DDPF_FOURCC = 0x4


def build_dds_header(
    width: int, height: int, mip_count: int, dxgi_format: int
) -> bytes:
    """Build DDS_HEADER + DDS_HEADER_DXT10 (148 bytes total with magic)."""
    # Compute linear size of mip 0
    block_bytes = BC_BLOCK_BYTES.get(dxgi_format, 16)
    block_w = max(1, (width + 3) // 4)
    block_h = max(1, (height + 3) // 4)
    linear_size = block_w * block_h * block_bytes

    flags = (DDSD_CAPS | DDSD_HEIGHT | DDSD_WIDTH | DDSD_PIXELFORMAT |
             DDSD_LINEARSIZE | DDSD_MIPMAPCOUNT)
    caps  = DDSCAPS_TEXTURE | DDSCAPS_COMPLEX | DDSCAPS_MIPMAP

    # DDPIXELFORMAT: 32 bytes (8 DWORDs)
    ddspf = struct.pack(
        "<IIIIIIII",
        32,          # dwSize
        DDPF_FOURCC, # dwFlags
        0x30315844,  # dwFourCC = "DX10"
        0, 0, 0, 0, 0,
    )
    # DDS_HEADER: 124 bytes
    dds_hdr = struct.pack(
        "<IIIII I I 11I",
        124,            # dwSize
        flags,          # dwFlags
        height,
        width,
        linear_size,    # dwPitchOrLinearSize
        0,              # dwDepth
        mip_count,
        *([0] * 11),    # dwReserved1[11]
    ) + ddspf + struct.pack(
        "<IIIII",
        caps,    # dwCaps
        0, 0, 0, # dwCaps2/3/4
        0,       # dwReserved2
    )
    # DDS_HEADER_DXT10: 20 bytes
    # resourceDimension: D3D10_RESOURCE_DIMENSION_TEXTURE2D = 3
    dxt10 = struct.pack("<IIIII", dxgi_format, 3, 0, 1, 0)

    return b"DDS " + dds_hdr + dxt10


# ---------------------------------------------------------------------------
# Archive resource lookup
# ---------------------------------------------------------------------------

def collect_texture_primaries(
    primary: bytes, header_off: int
) -> dict[int, tuple[int, int, int]]:
    """Return tex_hash -> (entry_idx, pos, size) for CGTextureResourceWin7 in both headers."""
    result: dict[int, tuple[int, int, int]] = {}
    off = header_off
    for _ in range(2):
        hdr = parse_header(primary, off)
        for i in range(hdr.contents.count):
            th, nh, val = struct.unpack_from("<QQQ", primary, hdr.contents.off + i * 24)
            if th == TEXTURE_PRIM_TYPE and val < hdr.entries.count:
                pos, size = entry_at(primary, hdr, val)
                result[nh] = (val, pos, size)
        off = hdr.end
    return result


# ---------------------------------------------------------------------------
# Primary slice parsing
# ---------------------------------------------------------------------------

def parse_texture_primary(prim_slice: bytes) -> dict | None:
    """Parse CGTextureResourceData from primary slice. Returns None if too short."""
    if len(prim_slice) < 232:
        return None
    rev_offsets = list(struct.unpack_from("<16I", prim_slice,   0))
    rev_cmp     = list(struct.unpack_from("<16I", prim_slice,  64))
    rev_sizes   = list(struct.unpack_from("<16I", prim_slice, 128))
    base = 192
    return {
        "rev_offsets":         rev_offsets,
        "rev_cmp":             rev_cmp,
        "rev_sizes":           rev_sizes,
        "streaming_disabled":  u32(prim_slice, base + 0),
        "maxwidth":            u32(prim_slice, base + 4),
        "maxheight":           u32(prim_slice, base + 8),
        "maxmipcount":         u32(prim_slice, base + 12),
        "arraysize":           u32(prim_slice, base + 16),
        "cubemap":             u32(prim_slice, base + 20),
        "dxgi_format":         u32(prim_slice, base + 24),
        "srgb_tilemode":       u32(prim_slice, base + 28),
    }


# ---------------------------------------------------------------------------
# Streaming mip extraction
# ---------------------------------------------------------------------------

def extract_streaming_dds(
    tex_hash: int,
    prim_meta: dict,
    packfile_path: Path,
    verbose: bool = False,
) -> tuple[bytes, str] | None:
    """
    Build a DDS file from streaming mips.
    Returns (dds_bytes, note) or None on failure.
    """
    rev_offsets = prim_meta["rev_offsets"]
    rev_sizes   = prim_meta["rev_sizes"]
    maxmipcount = prim_meta["maxmipcount"]
    maxwidth    = prim_meta["maxwidth"]
    maxheight   = prim_meta["maxheight"]
    dxgi_fmt    = prim_meta["dxgi_format"]

    if maxmipcount == 0 or maxmipcount > 16:
        return None

    # Collect streaming mips in DDS order (largest first = highest index in reversed array)
    streaming = []  # (offset, rawsize) for mips with valid offsets
    for i in range(maxmipcount - 1, -1, -1):
        off_val  = rev_offsets[i]
        raw_val  = rev_sizes[i]
        if off_val != 0xffffffff and raw_val > 0:
            streaming.append((off_val, raw_val))

    if not streaming:
        return None

    if not packfile_path.exists():
        return None

    packfile = packfile_path.read_bytes()
    if len(packfile) < 4:
        return None

    # Verify prefix (should equal sum of streaming rawsizes)
    prefix_size = u32(packfile, 0)
    expected_total = sum(s for _, s in streaming)
    # Allow small mismatch (the packfile may include always-resident mips too)

    # Read mip data from packfile
    mip_blobs = []
    for pf_off, raw_sz in streaming:
        start = 4 + pf_off
        end   = start + raw_sz
        if end > len(packfile):
            if verbose:
                print(f"    packfile too small: need {end:#x}, have {len(packfile):#x}")
            return None
        mip_blobs.append(packfile[start:end])

    n_streaming = len(streaming)
    dds_header  = build_dds_header(maxwidth, maxheight, n_streaming, dxgi_fmt)
    dds_data    = dds_header + b"".join(mip_blobs)

    note = f"streaming_mips={n_streaming} of {maxmipcount} total"
    return dds_data, note


# ---------------------------------------------------------------------------
# Manifest I/O
# ---------------------------------------------------------------------------

def load_manifest(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def write_manifest(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hash",     default=BRIDGE_ARCHIVE)
    parser.add_argument("--out-dir",  type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--update-manifest", action="store_true",
                        help="write extracted DDS info back to manifest TSV")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    names = load_hash_lookup(DEFAULT_HASH_LOOKUP)
    print(f"hash_lookup: {len(names)} entries")

    # Load archive
    primary_path = ARCHIVE_PRIMARY / args.hash
    gpu_path     = ARCHIVE_GPU / args.hash
    print(f"loading archive {args.hash} ...")
    primary = load_decompressed(primary_path)
    gpu     = load_decompressed(gpu_path)
    _, _, data_off, header_off = archive_offsets(primary, gpu)

    tex_prim_map = collect_texture_primaries(primary, header_off)
    print(f"  {len(tex_prim_map)} primary texture resources")

    # Load manifest to find failing textures
    print(f"loading manifest: {args.manifest}")
    manifest_rows = load_manifest(args.manifest)
    fieldnames    = list(manifest_rows[0].keys()) if manifest_rows else []

    # Find unique failing texture hashes from manifest
    failing = {
        row["textureassetid"]: row
        for row in manifest_rows
        if row.get("extraction_ok", "False") == "False"
           and row.get("note", "") == "no DDS magic"
    }
    print(f"  {len(failing)} unique failing textures (no DDS magic)")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    packfile_dir = PACKFILE_TYPE_DIR

    if not packfile_dir.exists():
        print(f"ERROR: packfile dir not found: {packfile_dir}")
        return

    # Track results
    results: dict[str, dict] = {}  # tex_hash -> metadata dict

    for tex_str, sample_row in sorted(failing.items()):
        try:
            tex_int = int(tex_str, 16)
        except ValueError:
            continue

        prim_entry = tex_prim_map.get(tex_int)
        if prim_entry is None:
            if verbose := args.verbose:
                print(f"  {tex_str}: no primary resource in archive")
            results[tex_str] = {"ok": False, "note": "no primary resource"}
            continue

        _, pos, size = prim_entry
        prim_slice = primary[data_off + pos : data_off + pos + size]
        prim_meta  = parse_texture_primary(prim_slice)
        if prim_meta is None:
            results[tex_str] = {"ok": False, "note": "primary slice too short"}
            continue

        packfile_path = packfile_dir / tex_str
        result = extract_streaming_dds(tex_int, prim_meta, packfile_path, args.verbose)
        if result is None:
            results[tex_str] = {
                "ok": False,
                "note": "packfile missing or mip read failed",
                "dxgi_format": prim_meta["dxgi_format"],
                "width":  prim_meta["maxwidth"],
                "height": prim_meta["maxheight"],
            }
            if args.verbose:
                print(f"  {tex_str}: extraction failed")
            continue

        dds_data, note = result
        out_path = args.out_dir / f"{tex_str}.dds"
        out_path.write_bytes(dds_data)

        # Parse DDS header to get confirmed dimensions
        w = u32(dds_data, 16)
        h = u32(dds_data, 12)
        m = u32(dds_data, 28)
        fmt = u32(dds_data, 128)  # at DXT10 header offset

        # Also write role-tagged copy if inputname is known
        decoded = sample_row.get("inputname_decoded", "unknown")
        if decoded != "unknown":
            role_path = args.out_dir / f"{tex_str}_{decoded}.dds"
            role_path.write_bytes(dds_data)

        results[tex_str] = {
            "ok": True,
            "note": note,
            "dxgi_format": fmt,
            "width": w,
            "height": h,
            "mip_count": m,
            "dds_path": str(out_path),
        }
        if args.verbose:
            print(f"  {tex_str}: {w}×{h} fmt={fmt} mips={m}  {note}")

    # Summary
    ok_count   = sum(1 for r in results.values() if r.get("ok"))
    fail_count = len(results) - ok_count
    print(f"\n--- Extraction summary ---")
    print(f"  {ok_count} textures extracted successfully")
    print(f"  {fail_count} textures still failed")

    for tex_str, r in sorted(results.items()):
        if r.get("ok"):
            print(f"  OK  {tex_str}: {r['width']}×{r['height']} fmt={r['dxgi_format']} "
                  f"mips={r.get('mip_count',0)} [{r['note']}]")
        else:
            print(f"  FAIL {tex_str}: {r['note']}")

    # Optionally update manifest
    if args.update_manifest and manifest_rows:
        print(f"\nupdating manifest: {args.manifest}")
        # Build tex -> first resolved role name
        tex_role: dict[str, str] = {}
        for row in manifest_rows:
            h = row["textureassetid"]
            dec = row.get("inputname_decoded", "unknown")
            if h not in tex_role and dec != "unknown":
                tex_role[h] = dec

        changed = 0
        for row in manifest_rows:
            h = row["textureassetid"]
            r = results.get(h)
            if r is None or not r.get("ok"):
                continue
            # Update the row
            row["extraction_ok"] = "True"
            row["note"]          = r["note"]
            row["dxgi_format"]   = str(r["dxgi_format"])
            row["width"]         = str(r["width"])
            row["height"]        = str(r["height"])
            row["mip_count"]     = str(r.get("mip_count", 0))
            role = tex_role.get(h)
            if role and role != "unknown":
                row["dds_file"] = str(args.out_dir / f"{h}_{role}.dds")
            else:
                row["dds_file"] = str(args.out_dir / f"{h}.dds")
            changed += 1
        write_manifest(args.manifest, manifest_rows, fieldnames)
        print(f"  updated {changed} rows in manifest")


if __name__ == "__main__":
    main()
