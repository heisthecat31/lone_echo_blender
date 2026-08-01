"""Build a corpus-wide `material_hash -> home archive` index.

WHY THIS EXISTS
---------------
**Material resources are overwhelmingly NOT resident in the archive that binds
them.** Measured on reference archive `0703fd2acd5803e9`, over its 51 parseable
mesh-lists:

    material bindings  = 127, resident in the binding archive =  24  (18.9 %)
    shaderset bindings = 112, resident                        = 112  (100.0 %)

Shadersets are always local; materials are not. Without this index,
`blender_tool/extractor/le_extract.py` resolves `{}` for ~80 % of materials and
the `.lemesh` manifest silently falls back to `SGMaterialData` defaults for
`mattype`, `blendmode`, `flags`/`eDoubleSided`, `k_alpha`, `bakecolor`,
`bakeemissivecolor` and every `materialprop` — i.e. everything the Blender
material builder needs to pick a render mode, an alpha or an emissive layer. An
`eMTForwardTransparent` material comes out reading as plain opaque.

The index is YOUR data, built from YOUR game install. Nothing is shipped.

Output (TSV, two columns: `material_hash`, `archive_hash`):
    $LONE_ECHO_SCAN_ROOT/material_archive_index.tsv
`le_extract.py` reads exactly that path, and warns loudly when it is missing.

Only the PRIMARY file is decompressed per archive: the primary's two header
blocks enumerate both primary and GPU resource entries, so the (much larger) GPU
file is never touched.

⚠ HEAVY — this decompresses every archive primary in turn. Run it single-stream;
do not run two of these, or one of these alongside another archive-loading tool.

Run with Windows Python so `le_oodle` can load the Oodle runtime:
    python.exe scripts/le_material_archive_index.py
    python.exe scripts/le_material_archive_index.py --priority-only
    python.exe scripts/le_material_archive_index.py --type materialfx

`le_texture_archive_index.py` reuses `build_index` / `write_index_tsv` from here
for the texture side of the same problem.
"""

from __future__ import annotations

import argparse
import csv
import os
import struct
import sys
import traceback
from pathlib import Path

from le_oodle import load_decompressed
from le_archive_decode import ARCHIVE_PRIMARY, parse_header

# Windows consoles default to cp1252 and argparse echoes this module's docstring
# on --help, so any non-ASCII in it raises UnicodeEncodeError the moment stdout
# is not a console (a pipe, a redirect, CI). Force UTF-8 on the streams we own.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):   # already-wrapped or non-reconfigurable
        pass


# Resource type hashes (the same constants `le_material_slice.py` pins).
MATERIAL_TYPE = 0x117d2b6509c8ff79      # CGMaterialResourceWin7
MATERIAL_FX_TYPE = 0x525e5a64a59eb745   # CGMaterialFXResourceWin7

TYPE_BY_NAME = {"material": MATERIAL_TYPE, "materialfx": MATERIAL_FX_TYPE}

#: where `blender_tool/extractor/le_extract.py` looks for its scan inputs
REPO_ROOT = Path(__file__).resolve().parents[1]
SCAN_ROOT = Path(os.environ.get("LONE_ECHO_SCAN_ROOT", str(REPO_ROOT / "scan_inputs")))

#: Archives worth indexing first — the shared/master ones that own most of the
#: cross-archive material and texture resources. `--priority-only` stops here.
PRIORITY_ARCHIVES = [
    "0703fd2acd5803e9",   # the bridge: the reference geometry source
    "4c47d84c1e52447a",   # a master archive
    "455295a65f8dbb6d",   # a master scene
    "cb3a93530d07fdc5",   # shared engine assets
    "1dd491938895e179",   # bridge, day variant
    "59aa329ecb23f106",   # bridge, night variant
    "4a405738bee7a74b",   # resolves 0 texture roles without a global index
]


def compressed_stub(path: Path) -> bool:
    """A 44/57-byte placeholder standing in for an archive you do not have."""
    return path.stat().st_size in (44, 57)


def _u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def collect_type_hashes(primary_bytes: bytes, type_hash: int) -> set[int]:
    """All resource name hashes of `type_hash` across both archive header blocks.

    `header_off` is computed from the primary alone (no GPU bytes needed):
      [0x00] u64 primary_size · [0x18] u64 extra_skip
      data_off = 0x20 + extra_skip ; header_off = data_off + primary_size
    """
    header_off = 0x20 + _u64(primary_bytes, 0x18) + _u64(primary_bytes, 0)

    result: set[int] = set()
    off = header_off
    for _ in range(2):
        try:
            hdr = parse_header(primary_bytes, off)
        except Exception:                       # noqa: BLE001 - truncated header
            break
        for i in range(hdr.contents.count):
            th, nh, _val = struct.unpack_from("<QQQ", primary_bytes,
                                              hdr.contents.off + i * 24)
            if th == type_hash:
                result.add(nh)
        off = hdr.end
    return result


def scan_archive(archive_hash: str, type_hash: int, verbose: bool = False) -> set[int]:
    """One archive's resource-name hashes of `type_hash`; `set()` on any failure."""
    path = ARCHIVE_PRIMARY / archive_hash
    if not path.exists() or compressed_stub(path):
        return set()
    primary_bytes = None
    try:
        primary_bytes = load_decompressed(path)
        hashes = collect_type_hashes(primary_bytes, type_hash)
        if verbose:
            print(f"  {archive_hash}: {len(hashes)} hashes")
        return hashes
    except Exception as exc:   # noqa: BLE001
        print(f"  {archive_hash}: ERROR - {exc}", file=sys.stderr)
        if verbose:
            traceback.print_exc()
        return set()
    finally:
        # drop the decompressed archive promptly; these are multi-hundred-MB buffers
        primary_bytes = None   # noqa: F841


def archive_list(priority_only: bool = False) -> list[str]:
    """Priority archives first, then everything else in the data root."""
    if priority_only:
        return list(PRIORITY_ARCHIVES)
    found = sorted(f.name for f in ARCHIVE_PRIMARY.iterdir()
                   if f.is_file() and not compressed_stub(f))
    return PRIORITY_ARCHIVES + [h for h in found if h not in PRIORITY_ARCHIVES]


def build_index(type_hash: int, archives: list[str], verbose: bool = False) -> dict[int, str]:
    """{resource name hash -> the FIRST archive found carrying it}.

    First-wins is deliberate and is why `PRIORITY_ARCHIVES` leads: a resource
    present in several archives resolves to the shared/master copy.
    """
    index: dict[int, str] = {}
    scanned = skipped = 0
    for i, ahash in enumerate(archives):
        if i % 50 == 0:
            print(f"  [{i + 1}/{len(archives)}] {len(index)} unique hashes ...",
                  flush=True)
        path = ARCHIVE_PRIMARY / ahash
        if not path.exists() or compressed_stub(path):
            skipped += 1
            continue
        for h in scan_archive(ahash, type_hash, verbose=verbose):
            index.setdefault(h, ahash)
        scanned += 1
    print(f"\nscanned {scanned}, skipped {skipped} (stub/missing), "
          f"{len(index)} unique hashes")
    return index


def write_index_tsv(index: dict[int, str], out_tsv: Path, key_column: str) -> Path:
    out_tsv = Path(out_tsv)
    out_tsv.parent.mkdir(parents=True, exist_ok=True)
    with out_tsv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow([key_column, "archive_hash"])
        for h, ahash in sorted(index.items()):
            w.writerow([f"{h:016x}", ahash])
    print(f"wrote {len(index)} rows -> {out_tsv}")
    return out_tsv


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--priority-only", action="store_true",
                    help="scan only the priority archives (a fast partial index)")
    ap.add_argument("--type", choices=sorted(TYPE_BY_NAME), default="material")
    ap.add_argument("--out-tsv", type=Path, default=None,
                    help=f"default: {SCAN_ROOT}/<type>_archive_index.tsv")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    out_tsv = args.out_tsv or (SCAN_ROOT / f"{args.type}_archive_index.tsv")
    archives = archive_list(args.priority_only)
    print(f"{'priority-only' if args.priority_only else 'full'} scan: "
          f"{len(archives)} archives -> {out_tsv}")

    index = build_index(TYPE_BY_NAME[args.type], archives, verbose=args.verbose)
    write_index_tsv(index, out_tsv, f"{args.type}_hash")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
