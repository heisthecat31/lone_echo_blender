"""Bind Lone Echo 1's authored scene names to the hashes the game ships.

The names come from the install's own authoring database
(`sourcedb/game/json/config/scene_ordering.json`), harvested into
`data/le1_scene_names.json`. They are real authored identifiers, so
`CSymbol64(name)` is the hash the engine uses -- any match is a verified
preimage rather than a guess.

The thing that had to be worked out is WHAT they name. Hashing them against the
1244 ARCHIVE names scores 0/1244: an archive is a bundle, and a bundle's name is
not a scene's name. The scene identity is a resource INSIDE the bundle, in the
archive's contents table -- `(type_hash, name_hash, value)` triples, of which
this reads every `name_hash` regardless of type.

    python scripts/le_bind_scene_names.py --limit 40      # quick probe
    python scripts/le_bind_scene_names.py                 # every archive
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool"),
           str(_ROOT / "app" / "extract" / "pyoodle")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from le_mesh.material_scalars import symbol64

MASK = (1 << 64) - 1
NAMES_FILE = _ROOT / "data" / "le1_scene_names.json"
OUT_FILE = _ROOT / "data" / "level_names_loneecho1.json"


def name_variants(name: str):
    """Casings/separators worth trying for one authored name."""
    base = name.strip()
    lower = base.lower()
    return {base, lower, lower.replace("-", "_"), lower.replace(" ", "_"),
            base.upper()}


def archive_name_hashes(primary: bytes) -> set:
    """Every `name_hash` in an archive's contents tables."""
    from le_archive_decode import archive_offsets, parse_header

    found = set()
    try:
        _, _, _data_off, header_off = archive_offsets(primary, primary)
        header = parse_header(primary, header_off)
    except Exception:                                   # noqa: BLE001
        return found
    # Walk both headers the archive carries; a scene resource can live in either.
    for _ in range(2):
        try:
            count = header.contents.count
            base = header.contents.off
        except AttributeError:
            break
        for i in range(count):
            if base + i * 24 + 24 > len(primary):
                break
            _t, name_hash, _v = struct.unpack_from("<QQQ", primary, base + i * 24)
            found.add(name_hash)
        try:
            header = parse_header(primary, header.end)
        except Exception:                               # noqa: BLE001
            break
    return found


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0,
                    help="only scan this many archives (largest first)")
    ap.add_argument("--out", default=str(OUT_FILE))
    args = ap.parse_args(argv)

    from le_archive_decode import ARCHIVE_PRIMARY
    from le_oodle import load_decompressed

    names = json.loads(NAMES_FILE.read_text(encoding="utf-8"))["scenes"]
    wanted = {}
    for name in names:
        for variant in name_variants(name):
            wanted.setdefault(symbol64(variant) & MASK, name)
    print(f"{len(names)} authored scene names -> {len(wanted)} candidate hashes")

    archives = sorted((p for p in Path(ARCHIVE_PRIMARY).iterdir() if p.is_file()),
                      key=lambda p: p.stat().st_size, reverse=True)
    if args.limit:
        archives = archives[:args.limit]
    print(f"scanning {len(archives)} archive(s) from {ARCHIVE_PRIMARY}")

    bound, scanned, failed = {}, 0, 0
    for i, path in enumerate(archives, 1):
        try:
            primary = load_decompressed(path)
        except Exception:                               # noqa: BLE001
            failed += 1
            continue
        scanned += 1
        for name_hash in archive_name_hashes(primary):
            if name_hash in wanted:
                bound.setdefault(f"{name_hash:016x}",
                                 {"name": wanted[name_hash],
                                  "archive": path.name})
        if i % 25 == 0 or i == len(archives):
            print(f"  [{i}/{len(archives)}] scanned={scanned} failed={failed} "
                  f"bound={len(bound)}")

    print(f"\n>>> {len(bound)} of {len(names)} scene names bound")
    for h, rec in sorted(bound.items(), key=lambda kv: kv[1]["name"])[:20]:
        print(f"   {h}  {rec['name']}   (in {rec['archive']})")

    out = Path(args.out)
    out.write_text(json.dumps({
        "levels": {h: rec["name"] for h, rec in sorted(
            bound.items(), key=lambda kv: kv[1]["name"])},
        "archives": {h: rec["archive"] for h, rec in bound.items()},
        "_note": (f"{len(bound)} of {len(names)} Lone Echo 1 scene names bound. "
                  f"Names are authored identifiers from the install's sourcedb; "
                  f"a hash IS CSymbol64 of its name, so every entry is a "
                  f"verified preimage. Hashes name a RESOURCE INSIDE an archive, "
                  f"not the archive itself."),
    }, indent=1), encoding="utf-8")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
