"""Build the CORPUS-WIDE `texture_hash -> role` index (research queue Q2).

★ Why this exists.

A shaderset's texture bindings come from two independent places, and only one of
them carries the ROLE:

  1. the `SShaderInputData` array — 0x20-byte rows
     `{u64 inputname; u64 textureassetid; u16 type, layer, engineresource, slot;
       f32 uscale, vscale}`. `inputname` hashes to e.g. `layer0_composite_diffuse`,
     so this is the ONLY place the role lives;
  2. the compiled DXBC `RDEF` chunk, where
     `symbol64(resource_name - "_decl") == that bind's textureassetid`. This
     covers the shadersets that ship NO array at all (4 of 17 in Liv's archive,
     docs/MATERIALS.md §1) — but it carries no
     role.

`Archive._ensure_role_by_tex` in `blender_tool/extractor/le_extract.py` bridges
them by propagating `tex_hash -> role` from array-bearing shadersets to
array-less ones — but only inside the ONE archive being extracted. That is why it
reaches 9/9 roles on `c5adf71288b87a23` in the 259-shaderset `r14_glb_global` and
only 4/15 in the 17-shaderset archive that holds Liv. This script lifts that
table to the whole corpus.

Method: for every `CGShaderSetResourceWin7` in every archive that can hold one,
scan the slice on an 8-byte stride and anchor on `inputname ∈
le_mesh.materials.ROLE_BY_INPUTNAME` (61 hashes — imported, never re-derived).
Anchoring on the NAME rather than on a texture needle set is what makes this
needle-free: a row is seen even when its texture is absent from every index.
Rows whose `textureassetid` is `0` or `0xFFFF_FFFF_FFFF_FFFF` are engine-supplied
inputs, not material binds, and are skipped.

⚠ `CGShaderSetData.inputs` is `CTable<SShaderInputData>[5]` — one table per shader
stage (`name-confirmed`) — so the same logical bind
can appear more than once in a slice. Rows are deduped per shaderset on
`(tex, role, slot)` and the raw hit count is kept in `n_rows`.

Run under Windows Python (Oodle DLL). ONE archive resident at a time; the GPU
file is never loaded. Resumable via a `.done` sidecar (rows alone are not a
sufficient key — an archive can legitimately emit zero rows).

    python.exe scripts/le_role_index.py \
        --archives-file generic_rebuilds/archive_census/shaderset_archives.txt
    python.exe scripts/le_role_index.py --archive 2fd6839161785e9c
"""

from __future__ import annotations

import argparse
import csv
import struct
import sys
from collections import Counter
from pathlib import Path

THIS = Path(__file__).resolve()
LE_ROOT = THIS.parents[1]
for p in (LE_ROOT / "scripts", LE_ROOT / "blender_tool"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from le_oodle import load_decompressed                        # noqa: E402
from le_archive_decode import (                     # noqa: E402
    ARCHIVE_PRIMARY, DEFAULT_HASH_LOOKUP, entry_at, load_hash_lookup, parse_header,
)
import le_shaderset_scan as sts             # noqa: E402
from le_mesh import materials as mat                          # noqa: E402

OUT_TSV = LE_ROOT / "generic_rebuilds" / "role_index.tsv"
DEFAULT_ARCHIVES_FILE = (LE_ROOT / "generic_rebuilds" / "archive_census"
                         / "shaderset_archives.txt")
FIELDS = ["archive_hash", "shaderset_hash", "tex_hash", "role", "slot", "n_rows"]

SIZEOF_SHADER_INPUT = 0x20

#: inputname CSymbol64 -> role key. Imported from the single source of truth in
#: `le_mesh.materials`; `test_materials.test_every_role_name_hashes_to_its_own_key`
#: guarantees every entry is a verified preimage (`symbol64(name) == key`).
ROLE_ANCHOR: dict[int, str] = {
    int(k, 16): v[0] for k, v in mat.ROLE_BY_INPUTNAME.items()}

#: textureassetid values that are NOT a material bind. 0 = unbound; -1 = an
#: engine-supplied input (`k_irradiance_0…8`, `k_shadow_map`, …).
NON_BINDS = (0, 0xFFFFFFFFFFFFFFFF)


def _compressed_stub(path: Path) -> bool:
    return path.exists() and path.stat().st_size in (44, 57)


def scan_shaderset_roles(primary: bytes, start: int, size: int) -> Counter:
    """Counter of `(tex_hash:int, role:str, slot:int)` in one shaderset slice.

    Role-anchored: the needle is the inputname, so no texture index is consulted
    and a bind whose texture lives in an unindexed archive is still seen.
    """
    hits: Counter = Counter()
    for off in range(start, start + size - SIZEOF_SHADER_INPUT + 1, 8):
        role = ROLE_ANCHOR.get(struct.unpack_from("<Q", primary, off)[0])
        if role is None:
            continue
        tex = struct.unpack_from("<Q", primary, off + 0x08)[0]
        if tex in NON_BINDS:
            continue
        slot = struct.unpack_from("<H", primary, off + 0x16)[0]
        hits[(tex, role, slot)] += 1
    return hits


def index_archive(archive: str) -> list[dict]:
    """Every role-anchored material bind in one archive. Primary only."""
    path = ARCHIVE_PRIMARY / archive
    if not path.exists() or _compressed_stub(path):
        return []
    primary = load_decompressed(path)
    data_off = 0x20 + struct.unpack_from("<Q", primary, 0x18)[0]
    header_off = data_off + struct.unpack_from("<Q", primary, 0)[0]

    smap: dict[int, tuple[int, int]] = {}
    header = None
    for hi in range(2):
        header = parse_header(primary, header_off if hi == 0 else header.end)
        for i in range(header.contents.count):
            th, nh, val = struct.unpack_from("<QQQ", primary, header.contents.off + i * 24)
            if th == sts.SHADERSET_TYPE and val < header.entries.count:
                smap[nh] = entry_at(primary, header, val)

    rows: list[dict] = []
    for nh, (pos, size) in sorted(smap.items()):
        hits = scan_shaderset_roles(primary, data_off + pos, size)
        for (tex, role, slot), n in sorted(hits.items()):
            rows.append(dict(
                archive_hash=archive, shaderset_hash=f"{nh:016x}",
                tex_hash=f"{tex:016x}", role=role, slot=slot, n_rows=n))
    return rows


def summarise(path: Path) -> None:
    """Soundness readout: how often does the corpus DISAGREE about a role?"""
    by_tex: dict[str, set[str]] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        n_rows = 0
        for row in csv.DictReader(fh, delimiter="\t"):
            n_rows += 1
            by_tex.setdefault(row["tex_hash"], set()).add(row["role"])
    pairs = sum(len(v) for v in by_tex.values())
    multi = {t: r for t, r in by_tex.items() if len(r) > 1}
    suffix_conf = {t: r for t, r in multi.items()
                   if len({mat.split_role(x)[1] for x in r}) > 1}
    print(f"\n{n_rows} rows -> {path}")
    print(f"  distinct textures with a role : {len(by_tex)}")
    print(f"  distinct (tex, role) pairs    : {pairs}")
    print(f"  textures with >1 role         : {len(multi)} "
          f"({100.0 * len(multi) / max(len(by_tex), 1):.2f}%)")
    print(f"    of those, SUFFIX conflicts  : {len(suffix_conf)} "
          f"({100.0 * len(suffix_conf) / max(len(by_tex), 1):.2f}% of all textures)")
    print(f"    layer-index only            : {len(multi) - len(suffix_conf)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", action="append", default=[],
                    help="archive name hash (repeatable)")
    ap.add_argument("--archives-file", type=Path,
                    help="file of archive hashes, one per line (# comments ok). "
                         "★ PREFER THIS: only 149 of the 1,244 archives contain a "
                         "CGShaderSetResourceWin7 at all, and they hold all 17,265 "
                         f"of them. Default list: {DEFAULT_ARCHIVES_FILE}")
    ap.add_argument("--all", action="store_true",
                    help="sweep every archive in the primary tree (resumable). "
                         "Slower than --archives-file for no extra rows")
    ap.add_argument("--limit", type=int, default=0, help="stop after N new archives")
    ap.add_argument("--out", type=Path, default=OUT_TSV)
    ap.add_argument("--summary-only", action="store_true",
                    help="re-print the soundness readout without scanning")
    args = ap.parse_args()

    if args.summary_only:
        summarise(args.out)
        return 0

    load_hash_lookup(DEFAULT_HASH_LOOKUP)

    # Resume: `.done` records "visited", which is what resume actually means. A
    # row-derived key would re-sweep every legitimately empty archive forever
    # (5 such archives exist for the sibling RDEF harvest). Rows are APPENDED per
    # archive so an OOM kill mid-sweep loses one archive, not the run.
    done_path = args.out.with_suffix(args.out.suffix + ".done")
    done: set[str] = set()
    if done_path.exists():
        done |= {ln.strip() for ln in done_path.read_text(encoding="utf-8").split()
                 if ln.strip()}
    n_existing = 0
    if args.out.exists():
        with args.out.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                done.add(row["archive_hash"])
                n_existing += 1

    targets = [a.lower() for a in args.archive]
    archives_file = args.archives_file
    if archives_file is None and not args.archive and not args.all:
        archives_file = DEFAULT_ARCHIVES_FILE
    if archives_file:
        for line in archives_file.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip().lower()
            if line:
                targets.append(line)
    if args.all:
        targets += sorted(p.name for p in ARCHIVE_PRIMARY.iterdir() if p.is_file())
    todo = [a for a in dict.fromkeys(targets) if a not in done]
    if args.limit:
        todo = todo[:args.limit]
    print(f"{len(done)} archives already indexed ({n_existing} rows); "
          f"{len(todo)} to do", flush=True)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fresh = not args.out.exists()
    written = n_existing
    for i, archive in enumerate(todo, 1):
        try:
            new = index_archive(archive)
        except Exception as exc:                     # noqa: BLE001
            print(f"  [{i}/{len(todo)}] {archive}: WARN {exc}", flush=True)
            continue
        with args.out.open("a", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS, delimiter="\t",
                               lineterminator="\n")
            if fresh:
                w.writeheader()
                fresh = False
            w.writerows(new)
        with done_path.open("a", encoding="utf-8") as fh:
            fh.write(archive + "\n")
        written += len(new)
        print(f"  [{i}/{len(todo)}] {archive}: {len(new)} binds "
              f"({written} rows total)", flush=True)

    summarise(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
