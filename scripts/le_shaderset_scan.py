"""P4/P6 — scan CGShaderSetResourceWin7 slices for texture bindings.

Goal: find the 17 shader sets that reference bridge-archive textures at unnamed
high slot numbers (18-23+), decode their inputname hashes, and determine whether
these are the PBR surface texture slots (albedo, normal, roughness, etc.).

Strategy:
  - Build set of all CGTextureResourceWin7 resource hashes in the archive.
  - For each CGShaderSetResourceWin7 slice, scan in 8-byte strides for u64 values
    that match a bridge texture hash.  When found, back up 8 bytes to read the
    full SShaderInputData (0x20 bytes).
  - Validate the entry: slot < 64, plausible float uscale/vscale, non-zero inputname.
  - Decode inputname hashes via hash_lookup.json.
  - Try a PBR wordlist through symbol64() to crack unknown inputname hashes.
  - Also emit ALL SShaderInputData entries (not just bridge-texture ones) for
    the shader sets that have any bridge-texture ref, to capture context.

SShaderInputData layout (0x20 bytes):
  +0x00  u64  inputname      (CSymbol64 hash of slot name)
  +0x08  u64  textureassetid (CSymbol64 hash of CGTextureResourceWin7)
  +0x10  u16  type
  +0x12  u16  layer
  +0x14  u16  engineresource
  +0x16  u16  slot
  +0x18  f32  uscale
  +0x1c  f32  vscale

Run with Windows Python so le_oodle can load the game Oodle DLL:
    python.exe scripts/le_shaderset_scan.py
    python.exe scripts/le_shaderset_scan.py --hash 0703fd2acd5803e9
    python.exe scripts/le_shaderset_scan.py --verbose
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from le_symbol_names import SEEDS, symbol64
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
import sys

# Windows consoles default to cp1252 and argparse echoes this module's docstring
# on --help, so any non-ASCII in it raises UnicodeEncodeError the moment stdout
# is not a console (a pipe, a redirect, CI). Force UTF-8 on the streams we own.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):   # already-wrapped or non-reconfigurable
        pass


SHADERSET_TYPE = 0x5fa019d27a511a3b
TEXTURE_TYPE   = 0xe8017b774f2b6327

SIZEOF_SHADER_INPUT = 0x20
SLOT_MAX_VALID      = 63      # heuristic upper bound for a valid slot number

DEFAULT_ARCHIVE  = "0703fd2acd5803e9"
DEFAULT_OUT_TSV  = Path("generic_rebuilds/shaderset_texture_scan.tsv")
DEFAULT_OUT_JSON = Path("generic_rebuilds/shaderset_texture_summary.json")


# ---------------------------------------------------------------------------
# PBR wordlist — candidate names to crack unknown inputname hashes
# ---------------------------------------------------------------------------

PBR_CANDIDATES: list[str] = [
    # bare PBR names
    "albedo", "albedo_map",
    "normal", "normal_map", "nrm", "nrm_map",
    "roughness", "roughness_map",
    "metallic", "metallic_map", "metalness", "metalness_map",
    "ao", "ao_map", "ambient_occlusion", "ambient_occlusion_map",
    "emissive", "emissive_map", "emissivemap",
    "height", "heightmap", "displacement",
    "detail", "detail_mask", "detail_albedo", "detail_normal",
    "orm", "orm_map",                    # occlusion-roughness-metallic packed
    "base_color", "base_color_map", "basecolor", "basecolor_map",
    "opacity", "opacity_map",
    "specular", "specular_map",
    "diffuse", "diffuse_map",
    # k_-prefixed variants
    "k_albedo", "k_albedo_map",
    "k_normal", "k_normal_map",
    "k_roughness", "k_roughness_map",
    "k_metallic", "k_metallic_map",
    "k_ao", "k_ao_map",
    "k_emissive", "k_emissive_map",
    "k_orm", "k_orm_map",
    "k_base_color", "k_base_color_map",
    "k_height", "k_height_map",
    "k_opacity", "k_opacity_map",
    "k_diffuse", "k_diffuse_map",
    "k_specular", "k_specular_map",
    # layer-prefixed variants
    "layer0_albedo", "layer1_albedo", "layer2_albedo",
    "layer0_normal", "layer1_normal", "layer2_normal",
    "layer0_roughness", "layer1_roughness",
    "layer0_metallic", "layer1_metallic",
    "layer0_ao", "layer1_ao",
    "layer0_emissive", "layer1_emissive",
    "layer0_orm", "layer1_orm",
    # surface / material name variants
    "k_surface_albedo", "k_surface_normal", "k_surface_roughness",
    "k_surface_metallic", "k_surface_ao", "k_surface_emissive",
    "k_material_albedo", "k_material_normal",
    # generic texture slots
    "texture0", "texture1", "texture2", "texture3",
    "k_texture0", "k_texture1", "k_texture2", "k_texture3",
    "texture_0", "texture_1", "texture_2", "texture_3",
    "tex0", "tex1", "tex2", "tex3",
    # RAD naming patterns
    "k_color_map", "k_nrm_map", "k_spec_map", "k_rough_map",
    "k_metal_map", "k_emis_map", "k_ao_rough_metal",
    # additional variants seen in shader contexts
    "albedo_texture", "normal_texture", "roughness_texture",
    "k_albedo_texture", "k_normal_texture",
]


def build_pbr_hash_table(extra_lookup: dict[int, str]) -> dict[int, str]:
    """Return hash->name for all PBR candidates not already in hash_lookup."""
    table: dict[int, str] = {}
    for name in PBR_CANDIDATES:
        h = int(symbol64(name), 16)
        if h not in extra_lookup:
            table[h] = name
    return table


# ---------------------------------------------------------------------------
# Binary helpers
# ---------------------------------------------------------------------------

def u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]

def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]

def u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]

def f32(data: bytes, off: int) -> float:
    return struct.unpack_from("<f", data, off)[0]

def hex64(v: int) -> str:
    return f"{v:016x}"

def is_valid_scale(v: float) -> bool:
    """Return True if v looks like a plausible UV scale (finite, sane range)."""
    return math.isfinite(v) and -1024.0 <= v <= 1024.0


# ---------------------------------------------------------------------------
# Data rows
# ---------------------------------------------------------------------------

@dataclass
class ShaderTexRow:
    archive_hash: str
    shaderset_hash: str
    shaderset_size: int
    entry_offset: int          # byte offset within the slice
    inputname_hash: str
    inputname_decoded: str     # from hash_lookup or PBR crack; "unknown" if neither
    textureassetid_hash: str
    texture_in_archive: bool   # True if textureassetid is a local texture
    slot: int
    type_: int
    layer: int
    engineresource: int
    uscale: float
    vscale: float


# ---------------------------------------------------------------------------
# Scan one shader-set slice
# ---------------------------------------------------------------------------

def scan_shaderset_slice(
    data: bytes,
    start: int,
    size: int,
    archive_hash: str,
    res_hash: str,
    texture_hashes: set[int],
    names: dict[int, str],
    pbr_table: dict[int, str],
) -> list[ShaderTexRow]:
    """Scan a CGShaderSetResourceWin7 slice for SShaderInputData entries that
    reference bridge archive textures.

    Scan strategy: walk in 8-byte strides looking for textureassetid matches.
    For each match at position `tex_off`, the SShaderInputData struct starts at
    `tex_off - 8` (since textureassetid is at +0x08 from struct start).
    Also try `tex_off` as the start (in case it is at +0x00, i.e. the inputname
    field happens to collide — unlikely but guards against alignment edge cases).
    """

    rows: list[ShaderTexRow] = []
    end = start + size
    seen_offsets: set[int] = set()

    # Scan every 8-byte-aligned position within the slice for a texture hash.
    # Using a set for O(1) lookup is cheap; the loop runs at most ~size/8 iters.
    for tex_off in range(start, end - 7, 8):
        val = u64(data, tex_off)
        if val not in texture_hashes:
            continue

        # Candidate: treat val as textureassetid at +0x08 from struct start
        entry_start = tex_off - 8
        if entry_start < start or entry_start + SIZEOF_SHADER_INPUT > end:
            continue
        if entry_start in seen_offsets:
            continue

        inputname_val = u64(data, entry_start + 0x00)
        tex_hash      = val
        type_val      = u16(data, entry_start + 0x10)
        layer_val     = u16(data, entry_start + 0x12)
        engres_val    = u16(data, entry_start + 0x14)
        slot_val      = u16(data, entry_start + 0x16)
        uscale_val    = f32(data, entry_start + 0x18)
        vscale_val    = f32(data, entry_start + 0x1c)

        if inputname_val == 0:
            continue
        if slot_val > SLOT_MAX_VALID:
            continue
        if not is_valid_scale(uscale_val) or not is_valid_scale(vscale_val):
            continue

        seen_offsets.add(entry_start)
        decoded = names.get(inputname_val) or pbr_table.get(inputname_val) or "unknown"
        rel_off = entry_start - start

        rows.append(ShaderTexRow(
            archive_hash=archive_hash,
            shaderset_hash=res_hash,
            shaderset_size=size,
            entry_offset=rel_off,
            inputname_hash=hex64(inputname_val),
            inputname_decoded=decoded,
            textureassetid_hash=hex64(tex_hash),
            texture_in_archive=True,
            slot=slot_val,
            type_=type_val,
            layer=layer_val,
            engineresource=engres_val,
            uscale=uscale_val,
            vscale=vscale_val,
        ))

    return rows


# ---------------------------------------------------------------------------
# Archive scan
# ---------------------------------------------------------------------------

def compressed_stub(path: Path) -> bool:
    return path.stat().st_size in (44, 57)


def collect_type_hashes(
    primary_bytes: bytes, data_off: int, header_off: int, type_hash: int
) -> set[int]:
    """Return all resource name hashes for a given type in both archive headers."""
    result: set[int] = set()
    header = None
    for hdr_idx in range(2):
        header = parse_header(primary_bytes, header_off if hdr_idx == 0 else header.end)
        for i in range(header.contents.count):
            th, nh, val = struct.unpack_from("<QQQ", primary_bytes, header.contents.off + i * 24)
            if th == type_hash:
                result.add(nh)
    return result


def scan_archive(
    archive_hash: str,
    names: dict[int, str],
    pbr_table: dict[int, str],
    verbose: bool = False,
) -> list[ShaderTexRow]:
    primary_path = ARCHIVE_PRIMARY / archive_hash
    gpu_path     = ARCHIVE_GPU / archive_hash
    if compressed_stub(primary_path) or compressed_stub(gpu_path):
        return []

    primary_bytes = load_decompressed(primary_path)
    gpu_bytes     = load_decompressed(gpu_path)
    prim_size, gpu_size, data_off, header_off = archive_offsets(primary_bytes, gpu_bytes)

    # Collect bridge-local texture resource hashes
    texture_hashes = collect_type_hashes(primary_bytes, data_off, header_off, TEXTURE_TYPE)
    if verbose:
        print(f"  {len(texture_hashes)} CGTextureResourceWin7 hashes in archive")

    rows: list[ShaderTexRow] = []
    header = None

    for hdr_idx in range(2):
        header = parse_header(primary_bytes, header_off if hdr_idx == 0 else header.end)
        for i in range(header.contents.count):
            type_hash, name_hash, value = struct.unpack_from(
                "<QQQ", primary_bytes, header.contents.off + i * 24
            )
            if type_hash != SHADERSET_TYPE or value >= header.entries.count:
                continue

            pos, size = entry_at(primary_bytes, header, value)
            abs_start = data_off + pos
            if abs_start + size > len(primary_bytes):
                continue

            res_hash_str = hex64(name_hash)
            found = scan_shaderset_slice(
                primary_bytes, abs_start, size,
                archive_hash, res_hash_str,
                texture_hashes, names, pbr_table,
            )
            if found:
                if verbose:
                    print(f"  shader set {res_hash_str} (size {size:#x}): "
                          f"{len(found)} bridge-texture entries")
                    for r in found:
                        print(f"    slot={r.slot:2d}  inputname={r.inputname_hash}"
                              f"  ({r.inputname_decoded})  tex={r.textureassetid_hash}"
                              f"  @+{r.entry_offset:#x}")
            rows.extend(found)

    return rows


# ---------------------------------------------------------------------------
# TSV / JSON output
# ---------------------------------------------------------------------------

TSV_FIELDS = [
    "archive_hash", "shaderset_hash", "shaderset_size", "entry_offset",
    "inputname_hash", "inputname_decoded", "textureassetid_hash",
    "texture_in_archive", "slot", "type", "layer", "engineresource",
    "uscale", "vscale",
]


def row_to_dict(r: ShaderTexRow) -> dict:
    d = asdict(r)
    d["type"] = d.pop("type_")
    return d


def write_tsv(path: Path, rows: list[ShaderTexRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=TSV_FIELDS, delimiter="\t")
        writer.writeheader()
        for r in rows:
            writer.writerow(row_to_dict(r))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hash", default=DEFAULT_ARCHIVE)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--out-tsv",  type=Path, default=DEFAULT_OUT_TSV)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    args = parser.parse_args()

    names = load_hash_lookup(DEFAULT_HASH_LOOKUP)
    pbr_table = build_pbr_hash_table(names)
    print(f"PBR wordlist: {len(pbr_table)} candidate hashes (not in hash_lookup)")

    if args.all:
        import re
        lookup = json.loads(DEFAULT_HASH_LOOKUP.read_text()) if DEFAULT_HASH_LOOKUP.exists() else {}
        archive_hashes = sorted(set(
            k.lstrip("0x").lower() for k in lookup
            if re.fullmatch(r"0x[0-9a-fA-F]{16}", k)
        ))
    else:
        archive_hashes = [args.hash]

    all_rows: list[ShaderTexRow] = []
    for ahash in archive_hashes:
        print(f"scanning {ahash} ...", flush=True)
        try:
            rows = scan_archive(ahash, names, pbr_table, verbose=args.verbose)
            print(f"  {len(rows)} texture-binding entries across "
                  f"{len(set(r.shaderset_hash for r in rows))} shader sets")
            all_rows.extend(rows)
        except Exception as exc:
            import traceback
            print(f"  FAILED: {exc}")
            if args.verbose:
                traceback.print_exc()

    if all_rows:
        write_tsv(args.out_tsv, all_rows)
        print(f"\nwrote {len(all_rows)} rows -> {args.out_tsv}")

    # Summarize
    sets_with_refs  = set(r.shaderset_hash for r in all_rows)
    unique_textures = set(r.textureassetid_hash for r in all_rows)
    decoded_count   = sum(1 for r in all_rows if r.inputname_decoded != "unknown")
    slot_counts: dict[int, int] = {}
    for r in all_rows:
        slot_counts[r.slot] = slot_counts.get(r.slot, 0) + 1
    inputname_freq: dict[str, int] = {}
    for r in all_rows:
        k = f"{r.inputname_hash}|{r.inputname_decoded}"
        inputname_freq[k] = inputname_freq.get(k, 0) + 1

    summary = {
        "archive_count": len(archive_hashes),
        "total_entries": len(all_rows),
        "shader_sets_with_bridge_tex": len(sets_with_refs),
        "unique_bridge_textures_referenced": len(unique_textures),
        "inputname_decoded_count": decoded_count,
        "inputname_unknown_count": len(all_rows) - decoded_count,
        "slot_distribution": {str(k): v for k, v in sorted(slot_counts.items())},
        "inputname_frequency": {k: v for k, v in sorted(inputname_freq.items(), key=lambda x: -x[1])},
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(summary, indent=2))
    print(f"wrote summary -> {args.out_json}")

    # Console summary
    print(f"\n=== Summary ===")
    print(f"  Shader sets with bridge-archive texture refs: {len(sets_with_refs)}")
    print(f"  Unique textures referenced: {len(unique_textures)}")
    print(f"  Decoded inputname hashes: {decoded_count} / {len(all_rows)}")
    print(f"  Slot distribution: {dict(sorted(slot_counts.items()))}")
    print(f"\nTop inputname hashes:")
    for k, cnt in sorted(inputname_freq.items(), key=lambda x: -x[1])[:20]:
        print(f"  {k}  (×{cnt})")


if __name__ == "__main__":
    main()
