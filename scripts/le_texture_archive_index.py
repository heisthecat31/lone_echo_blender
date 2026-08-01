"""Build a corpus-wide `texture_hash -> home archive` index.

WHY THIS EXISTS
---------------
A shaderset lives in the archive that binds it, but **the textures it binds
mostly do not**. Measured: 88 of 115 texture bindings are external on reference
archive `0703fd2acd5803e9`, and 31 of 31 on `4a405738bee7a74b` — which is exactly
why that second archive used to resolve ZERO texture roles.

`blender_tool/extractor/le_extract.py` scans an archive's `SShaderInputData`
rows by looking for known texture hashes. With only archive-LOCAL hashes as the
needle set it finds almost nothing; unioned with this index it finds them all
(60 bindings vs 212 on the reference archive). And when a texture IS found, this
index is what tells `--textures` which archive to pull the DDS out of, instead of
silently extracting nothing.

The index is YOUR data, built from YOUR game install. Nothing is shipped.

Output (TSV, two columns: `tex_hash`, `archive_hash`):
    $LONE_ECHO_SCAN_ROOT/texture_archive_index.tsv
`le_extract.py` reads exactly that path, and warns loudly when it is missing.

Only the PRIMARY file is decompressed per archive: the primary's two header
blocks enumerate both primary and GPU resource entries, so the (much larger) GPU
file is never touched.

⚠ HEAVY — this decompresses every archive primary in turn. Run it single-stream;
do not run two of these, or one of these alongside another archive-loading tool.

Run with Windows Python so `le_oodle` can load the Oodle runtime:
    python.exe scripts/le_texture_archive_index.py
    python.exe scripts/le_texture_archive_index.py --priority-only
    python.exe scripts/le_texture_archive_index.py --verbose

The archive walk itself is shared with `le_material_archive_index.py`, which
solves the same problem for `CGMaterialResourceWin7`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from le_material_archive_index import (
    SCAN_ROOT, archive_list, build_index, write_index_tsv,
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


TEXTURE_TYPE = 0xe8017b774f2b6327        # CGTextureResourceWin7

DEFAULT_OUT = SCAN_ROOT / "texture_archive_index.tsv"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--priority-only", action="store_true",
                    help="scan only the priority archives (a fast partial index)")
    ap.add_argument("--out-tsv", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args(argv)

    archives = archive_list(args.priority_only)
    print(f"{'priority-only' if args.priority_only else 'full'} scan: "
          f"{len(archives)} archives -> {args.out_tsv}")

    index = build_index(TEXTURE_TYPE, archives, verbose=args.verbose)
    write_index_tsv(index, args.out_tsv, "tex_hash")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
