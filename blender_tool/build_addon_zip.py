#!/usr/bin/env python3
"""Package the Blender add-on (`addon/lone_echo_import/`) into an installable zip.

Produces `dist/lone_echo_import-<version>.zip` whose single top-level entry is the
`lone_echo_import/` package folder — the layout Blender's *Preferences > Add-ons >
Install from Disk* expects. `__pycache__/` and `*.pyc` are always excluded (they
are build artefacts that must never ship).

The Stage-2 add-on is fully self-contained (only `bpy`/`mathutils`/stdlib +
intra-package imports), so this zip is all an end user needs to import a `.lemesh`
mesh package or a `.lescatter` level package. The Stage-1 extractor is NOT packaged
here — it depends on the research repo's Oodle/decode stack and the local game
corpus.

    python3 blender_tool/build_addon_zip.py            # -> dist/lone_echo_import-<v>.zip
    python3 blender_tool/build_addon_zip.py --out /tmp/x.zip
    python3 blender_tool/build_addon_zip.py --list     # dry-run: list what would ship
"""
from __future__ import annotations

import argparse
import ast
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_DIR = HERE / "addon" / "lone_echo_import"
PKG_NAME = PKG_DIR.name

EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo"}


def read_version() -> str:
    """Parse `bl_info["version"]` from the add-on's __init__.py without importing bpy."""
    src = (PKG_DIR / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id == "bl_info":
                    info = ast.literal_eval(node.value)
                    v = info.get("version", (0, 0, 0))
                    return ".".join(str(x) for x in v)
    return "0.0.0"


def collect_files() -> list[Path]:
    """Every shippable file under the package, pycache/pyc excluded, sorted."""
    files = []
    for p in sorted(PKG_DIR.rglob("*")):
        if not p.is_file():
            continue
        if any(part in EXCLUDE_DIRS for part in p.relative_to(PKG_DIR).parts):
            continue
        if p.suffix in EXCLUDE_SUFFIXES:
            continue
        files.append(p)
    return files


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=None,
                    help="output zip path (default dist/lone_echo_import-<version>.zip)")
    ap.add_argument("--list", action="store_true",
                    help="dry-run: list the files that would be packaged, then exit")
    args = ap.parse_args()

    if not PKG_DIR.is_dir():
        print(f"ERROR: add-on package not found at {PKG_DIR}")
        return 1

    version = read_version()
    files = collect_files()

    print(f"add-on: {PKG_NAME}  version {version}")
    print(f"source: {PKG_DIR}")
    print(f"files ({len(files)}):")
    for p in files:
        print(f"    {PKG_NAME}/{p.relative_to(PKG_DIR).as_posix()}")

    if args.list:
        return 0

    out = args.out or (HERE / "dist" / f"{PKG_NAME}-{version}.zip")
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            arc = f"{PKG_NAME}/{p.relative_to(PKG_DIR).as_posix()}"
            zf.write(p, arc)

    size = out.stat().st_size
    print(f"\nwrote {out}  ({size:,} bytes, {len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
