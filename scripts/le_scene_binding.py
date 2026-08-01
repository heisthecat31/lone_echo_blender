"""P7 — parse CGSceneResourceWin7 binding tables for all mesh lists.

CGSceneResourceWin7 stores, near its end, a compact binding table:

    u32   n_materials
    u64   mat_hash[0..n_mat-1]     <- materialidx=0, 1, ...
    u32   n_shadersets
    u64   shd_hash[0..n_shd-1]     <- shadersetidx=0, 1, ...
    u64   0xffffffffffffffff        <- sentinel

Key parsing notes:
  - Entries are 4-byte packed (NOT 8-byte aligned); u64 hashes start 4 bytes
    after each u32 count, not at the next 8-byte boundary.
  - The binding block's start offset varies by resource.
  - Locate sentinel by scanning backward from slice end in 4-byte steps.

Manually confirmed for three mesh lists:
  98f7ed0990acd7cf  n_mat=4  n_shd=4
  81348f18cd3e36d9  n_mat=2  n_shd=2
  892cca9de00b30a6  n_mat=3  n_shd=7

This script extends the analysis to all 54 mesh lists in the bridge archive.

Type hashes:
  CGMeshListResourceWin7       0x366b22153d894fe1  (for companion matching)
  CGSceneResourceWin7          0x86f4cd162e7da857
  CGMaterialResourceWin7       0x117d2b6509c8ff79

Run with Windows Python so le_oodle can load the game Oodle DLL:
    python.exe scripts/le_scene_binding.py
    python.exe scripts/le_scene_binding.py --hash 0703fd2acd5803e9
    python.exe scripts/le_scene_binding.py --verbose
"""

from __future__ import annotations

import argparse
import csv
import json
import struct
from dataclasses import asdict, dataclass
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
import sys

# Windows consoles default to cp1252 and argparse echoes this module's docstring
# on --help, so any non-ASCII in it raises UnicodeEncodeError the moment stdout
# is not a console (a pipe, a redirect, CI). Force UTF-8 on the streams we own.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):   # already-wrapped or non-reconfigurable
        pass


MESHLIST_TYPE    = 0x366b22153d894fe1
SCENE_TYPE       = 0x86f4cd162e7da857
MATERIAL_TYPE    = 0x117d2b6509c8ff79
SHADERSET_TYPE   = 0x5fa019d27a511a3b

SENTINEL         = 0xffffffffffffffff
MAX_TABLE_HASHES = 256  # upper bound for sanity

DEFAULT_ARCHIVE  = "0703fd2acd5803e9"
DEFAULT_OUT_TSV  = Path("generic_rebuilds/scene_binding_parse.tsv")
DEFAULT_OUT_JSON = Path("generic_rebuilds/scene_binding_summary.json")


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]

def u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]

def hex64(v: int) -> str:
    return f"{v:016x}"


@dataclass
class BindingRow:
    archive_hash: str
    meshlist_hash: str
    scene_resource_hash: str
    scene_slice_size: int
    sentinel_offset: int       # byte offset within slice where sentinel was found
    n_materials: int
    n_shadersets: int
    material_hashes: str       # semicolon-separated hex64 strings
    shaderset_hashes: str
    mat_in_archive: str        # semicolon-separated bool (T/F) for each material
    shd_in_archive: str
    parse_ok: bool
    parse_note: str


def parse_binding_table(
    data: bytes,
    start: int,
    size: int,
    material_set: set[int],
    shaderset_set: set[int],
) -> tuple[bool, str, dict]:
    """Locate and parse the binding table in a CGSceneResourceWin7 slice.

    Returns (ok, note, result_dict).
    result_dict has keys: sentinel_off, n_materials, n_shadersets,
    mat_hashes, shd_hashes.
    """
    end = start + size

    # Step 1: backward scan from end for the sentinel in 4-byte steps.
    sentinel_abs = -1
    for off in range(end - 8, start - 1, -4):
        if u64(data, off) == SENTINEL:
            sentinel_abs = off
            break

    if sentinel_abs < 0:
        return False, "sentinel 0xffffffffffffffff not found", {}

    sentinel_off = sentinel_abs - start

    # Step 2: parse backward from sentinel.
    # Layout before sentinel (reading forwards):
    #   [binding_block_start]
    #       u32  n_materials
    #       u64  mat_hash[0..n_mat-1]
    #       u32  n_shadersets
    #       u64  shd_hash[0..n_shd-1]
    #   [sentinel at sentinel_abs]
    #
    # We know n_shadersets is at (sentinel_abs - 4 - n_shd*8 - 4).
    # But n_shd is unknown. Strategy: try all plausible n_shd in [0..MAX].

    # Read forward from just before sentinel:
    # sentinel_abs - 4 bytes = end of shaderset table; but we don't know n_shd.
    # More robust: scan forward from candidate positions.

    # Forward parse strategy: scan backward to find a plausible block start.
    # We know:
    #   block_end = sentinel_abs
    #   just before sentinel: u64 shd_hash[n_shd-1]  (if n_shd > 0)
    #   just before that:     u64 shd_hash[n_shd-2]  ...
    #   just before all those: u32 n_shadersets
    #   just before that:     u64 mat_hash[n_mat-1] (if n_mat > 0)
    #   ...
    #   just before all those: u32 n_materials

    # Try each possible n_shadersets (0..MAX):
    for n_shd in range(0, MAX_TABLE_HASHES + 1):
        shd_table_bytes = n_shd * 8
        n_shd_off = sentinel_abs - shd_table_bytes - 4  # position of n_shadersets u32
        if n_shd_off < start:
            break
        val_n_shd = u32(data, n_shd_off)
        if val_n_shd != n_shd:
            continue

        # n_shd found. Now try each n_mat:
        for n_mat in range(0, MAX_TABLE_HASHES + 1):
            mat_table_bytes = n_mat * 8
            n_mat_off = n_shd_off - mat_table_bytes - 4  # position of n_materials u32
            if n_mat_off < start:
                break
            val_n_mat = u32(data, n_mat_off)
            if val_n_mat != n_mat:
                continue

            # Both counts matched — require at least one entry to avoid false zeros.
            if n_mat == 0 and n_shd == 0:
                continue

            mat_start = n_mat_off + 4
            shd_start = n_shd_off + 4

            mat_hashes = [u64(data, mat_start + i * 8) for i in range(n_mat)]
            shd_hashes = [u64(data, shd_start + i * 8) for i in range(n_shd)]

            # Sanity: none of the hashes should be zero or the sentinel
            if any(h in (0, SENTINEL) for h in mat_hashes + shd_hashes):
                continue

            return True, "ok", {
                "sentinel_off": sentinel_off,
                "n_materials": n_mat,
                "n_shadersets": n_shd,
                "mat_hashes": mat_hashes,
                "shd_hashes": shd_hashes,
            }

    return False, f"could not find consistent n_materials/n_shadersets before sentinel@{sentinel_off:#x}", {}


def compressed_stub(path: Path) -> bool:
    return path.stat().st_size in (44, 57)


def collect_type_map(
    primary_bytes: bytes, header_off: int, type_hash: int
) -> dict[int, tuple[int, int]]:
    """Map name_hash → (position, size) for all entries of a given type."""
    result: dict[int, tuple[int, int]] = {}
    header = None
    for hdr_idx in range(2):
        header = parse_header(primary_bytes, header_off if hdr_idx == 0 else header.end)
        for i in range(header.contents.count):
            th, nh, val = struct.unpack_from("<QQQ", primary_bytes, header.contents.off + i * 24)
            if th == type_hash and val < header.entries.count:
                pos, sz = entry_at(primary_bytes, header, val)
                result[nh] = (pos, sz)
    return result


def collect_type_set(
    primary_bytes: bytes, header_off: int, type_hash: int
) -> set[int]:
    """Return set of all resource name hashes for a type in both headers."""
    result: set[int] = set()
    header = None
    for hdr_idx in range(2):
        header = parse_header(primary_bytes, header_off if hdr_idx == 0 else header.end)
        for i in range(header.contents.count):
            th, nh, _ = struct.unpack_from("<QQQ", primary_bytes, header.contents.off + i * 24)
            if th == type_hash:
                result.add(nh)
    return result


def scan_archive(
    archive_hash: str,
    names: dict[int, str],
    verbose: bool = False,
) -> list[BindingRow]:
    primary_path = ARCHIVE_PRIMARY / archive_hash
    gpu_path     = ARCHIVE_GPU / archive_hash
    if compressed_stub(primary_path) or compressed_stub(gpu_path):
        return []

    primary_bytes = load_decompressed(primary_path)
    gpu_bytes     = load_decompressed(gpu_path)
    prim_size, gpu_size, data_off, header_off = archive_offsets(primary_bytes, gpu_bytes)

    # Build lookup maps
    scene_map     = collect_type_map(primary_bytes, header_off, SCENE_TYPE)
    material_set  = collect_type_set(primary_bytes, header_off, MATERIAL_TYPE)
    shaderset_set = collect_type_set(primary_bytes, header_off, SHADERSET_TYPE)

    if verbose:
        print(f"  scene resources: {len(scene_map)}")
        print(f"  material resources: {len(material_set)}")
        print(f"  shaderset resources: {len(shaderset_set)}")

    rows: list[BindingRow] = []
    header = None

    for hdr_idx in range(2):
        header = parse_header(primary_bytes, header_off if hdr_idx == 0 else header.end)
        for i in range(header.contents.count):
            type_hash, name_hash, value = struct.unpack_from(
                "<QQQ", primary_bytes, header.contents.off + i * 24
            )
            if type_hash != MESHLIST_TYPE or value >= header.entries.count:
                continue

            ml_hash = name_hash
            ml_hash_str = hex64(ml_hash)

            # Find companion CGSceneResourceWin7 with the same resource name hash
            if ml_hash not in scene_map:
                if verbose:
                    print(f"  mesh list {ml_hash_str}: no companion CGSceneResourceWin7")
                continue

            scene_pos, scene_size = scene_map[ml_hash]
            scene_abs = data_off + scene_pos
            if scene_abs + scene_size > len(primary_bytes):
                rows.append(BindingRow(
                    archive_hash=archive_hash, meshlist_hash=ml_hash_str,
                    scene_resource_hash=ml_hash_str, scene_slice_size=scene_size,
                    sentinel_offset=-1, n_materials=0, n_shadersets=0,
                    material_hashes="", shaderset_hashes="",
                    mat_in_archive="", shd_in_archive="",
                    parse_ok=False, parse_note="scene slice out of bounds",
                ))
                continue

            ok, note, result = parse_binding_table(
                primary_bytes, scene_abs, scene_size, material_set, shaderset_set
            )

            if not ok:
                rows.append(BindingRow(
                    archive_hash=archive_hash, meshlist_hash=ml_hash_str,
                    scene_resource_hash=ml_hash_str, scene_slice_size=scene_size,
                    sentinel_offset=-1, n_materials=0, n_shadersets=0,
                    material_hashes="", shaderset_hashes="",
                    mat_in_archive="", shd_in_archive="",
                    parse_ok=False, parse_note=note,
                ))
                if verbose:
                    print(f"  mesh list {ml_hash_str}: FAIL — {note}")
                continue

            mat_hashes  = result["mat_hashes"]
            shd_hashes  = result["shd_hashes"]
            mat_in_arch = [h in material_set  for h in mat_hashes]
            shd_in_arch = [h in shaderset_set for h in shd_hashes]

            if verbose:
                n_mat = result["n_materials"]
                n_shd = result["n_shadersets"]
                print(f"  mesh list {ml_hash_str}: n_mat={n_mat} n_shd={n_shd}"
                      f" mat_local={sum(mat_in_arch)}/{n_mat}"
                      f" shd_local={sum(shd_in_arch)}/{n_shd}")

            rows.append(BindingRow(
                archive_hash=archive_hash,
                meshlist_hash=ml_hash_str,
                scene_resource_hash=ml_hash_str,
                scene_slice_size=scene_size,
                sentinel_offset=result["sentinel_off"],
                n_materials=result["n_materials"],
                n_shadersets=result["n_shadersets"],
                material_hashes=";".join(hex64(h) for h in mat_hashes),
                shaderset_hashes=";".join(hex64(h) for h in shd_hashes),
                mat_in_archive=";".join("T" if b else "F" for b in mat_in_arch),
                shd_in_archive=";".join("T" if b else "F" for b in shd_in_arch),
                parse_ok=True,
                parse_note=note,
            ))

    return rows


def write_tsv(path: Path, rows: list[BindingRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(asdict(rows[0]).keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for r in rows:
            writer.writerow(asdict(r))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hash", default=DEFAULT_ARCHIVE)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--out-tsv",  type=Path, default=DEFAULT_OUT_TSV)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    args = parser.parse_args()

    names = load_hash_lookup(DEFAULT_HASH_LOOKUP)

    if args.all:
        import re
        lookup = json.loads(DEFAULT_HASH_LOOKUP.read_text()) if DEFAULT_HASH_LOOKUP.exists() else {}
        archive_hashes = sorted(set(
            k.lstrip("0x").lower() for k in lookup
            if re.fullmatch(r"0x[0-9a-fA-F]{16}", k)
        ))
    else:
        archive_hashes = [args.hash]

    all_rows: list[BindingRow] = []
    for ahash in archive_hashes:
        print(f"scanning {ahash} ...", flush=True)
        try:
            rows = scan_archive(ahash, names, verbose=args.verbose)
            ok_count  = sum(1 for r in rows if r.parse_ok)
            fail_count = len(rows) - ok_count
            print(f"  {len(rows)} mesh lists: {ok_count} parsed OK, {fail_count} failed")
            all_rows.extend(rows)
        except Exception as exc:
            import traceback
            print(f"  FAILED: {exc}")
            if args.verbose:
                traceback.print_exc()

    if all_rows:
        write_tsv(args.out_tsv, all_rows)
        print(f"\nwrote {len(all_rows)} rows -> {args.out_tsv}")

    ok_rows = [r for r in all_rows if r.parse_ok]
    cross_archive_mat = sum(
        r.mat_in_archive.count("F") for r in ok_rows if r.mat_in_archive
    )

    summary = {
        "archive_count": len(archive_hashes),
        "total_mesh_lists": len(all_rows),
        "parsed_ok": len(ok_rows),
        "parse_failed": len(all_rows) - len(ok_rows),
        "total_material_bindings": sum(r.n_materials for r in ok_rows),
        "total_shaderset_bindings": sum(r.n_shadersets for r in ok_rows),
        "cross_archive_material_refs": cross_archive_mat,
        "rows": [asdict(r) for r in all_rows],
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(summary, indent=2))
    print(f"wrote summary -> {args.out_json}")

    # Console summary
    print(f"\n=== Summary ===")
    print(f"  Mesh lists with CGSceneResourceWin7: {len(all_rows)}")
    print(f"  Parsed OK: {len(ok_rows)} / {len(all_rows)}")
    if ok_rows:
        all_mat_counts = [r.n_materials for r in ok_rows]
        all_shd_counts = [r.n_shadersets for r in ok_rows]
        print(f"  Material bindings per mesh list: min={min(all_mat_counts)} max={max(all_mat_counts)}")
        print(f"  Shaderset bindings per mesh list: min={min(all_shd_counts)} max={max(all_shd_counts)}")
        print(f"  Cross-archive material refs: {cross_archive_mat}")


if __name__ == "__main__":
    main()
