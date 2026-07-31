"""audit_lod_fields — corpus check of the MESH-LIST LOD fields (they are inert).

READ-ONLY corpus audit. For every archive in the archive manifest it loads ONLY
the decompressed PRIMARY (one archive at a time, freed between — memory guard; the
GPU sibling is never opened, its per-resource size comes from the primary's header1)
and reports, over every paired `CGMeshListResourceWin7`:

  * `CGMeshListData.lodchildindices.count`
  * `CGRenderParams.lodprimsetidx` / `lodchildrenstart` / `lodchildrencount`

WHY: `le_mesh.meshlist.Draw.is_lod_parent` documents these as INERT in retail, which
is the basis for the importer treating every mesh as its own object. That claim was
made from a handful of samples; this re-derives it over the whole corpus so the
"mesh-list LOD is a no-op" statement is corpus-scale rather than anecdotal.

It does NOT contradict `le_mesh.static_lod`: static scatter carries a SEPARATE,
fully populated LOD system (`SGStaticInstanceLODData`) that this audit does not
touch. Mesh-list LOD inert + static-instance LOD populated are both true.

MUST run under Windows Python (python.exe) so le_oodle can load the Oodle DLL:

    python.exe blender_tool/tests/audit_lod_fields.py                 # whole manifest
    python.exe blender_tool/tests/audit_lod_fields.py --limit-archives 20
    python.exe blender_tool/tests/audit_lod_fields.py --archive <hash>

Not named `test_*.py`, so `tests/run_tests.py` never imports it.
"""

from __future__ import annotations

import argparse
import csv
import gc
import os
import struct
import sys
from collections import Counter
from pathlib import Path

THIS = Path(__file__).resolve()
REPO_ROOT = THIS.parents[2]
for p in (str(REPO_ROOT / "scripts"), str(REPO_ROOT / "blender_tool")):
    if p not in sys.path:
        sys.path.insert(0, p)

# The corpus list: one row per archive, with an `archive` column. Generate it for
# your own copy of the game data, or point `LONE_ECHO_SCAN_ROOT` at wherever you
# keep it. Not shipped — it is derived from game data.
MANIFEST_TSV = Path(os.environ.get(
    "LONE_ECHO_ARCHIVE_MANIFEST",
    str(Path(os.environ.get("LONE_ECHO_SCAN_ROOT", str(REPO_ROOT / "generic_rebuilds")))
        / "archive_mesh_manifest.tsv")))
NO_LOD_PRIMSET = 0xFFFFFFFF     # CGRenderParams.lodprimsetidx "unset" sentinel


def manifest_archives(path: Path) -> list[str]:
    seen, out = set(), []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            h = (row.get("archive_hash") or "").strip().lower()
            if h and h not in seen:
                seen.add(h)
                out.append(h)
    return out


def audit_archive(archive_hash: str, names: dict, hits: list | None = None) -> Counter:
    """Header-only LOD-field tally for one archive's paired mesh-lists.

    Any mesh-list that actually populates the chain is appended to `hits` as
    (archive, meshlist, n_meshes, n_rp, n_lodchildindices, rp_primset_set,
    rp_childrencount_set) so the populated minority can be inspected directly.
    """
    from le_oodle import load_decompressed
    from le_archive_decode import (
        ARCHIVE_PRIMARY, parse_header, resource_entries,
    )
    from le_meshlist_decode import parse_candidate
    from le_mesh.meshlist import (
        RENDERPARAM_STRIDE, RP_LODPRIMSETIDX, RP_LODCHILDRENSTART, RP_LODCHILDRENCOUNT,
    )

    tally = Counter()
    primary = load_decompressed(ARCHIVE_PRIMARY / archive_hash)
    try:
        # `archive_offsets` is not used: it validates the GPU block against the GPU
        # bytes, and this audit deliberately never opens the (much larger) GPU
        # sibling. The primary-side arithmetic is the same, and the per-resource
        # GPU sizes we need are in the primary's header1.
        primary_size = struct.unpack_from("<Q", primary, 0)[0]
        extra_skip = struct.unpack_from("<Q", primary, 24)[0]
        data_off = 32 + extra_skip
        header_off = data_off + primary_size
        header0 = parse_header(primary, header_off)
        header1 = parse_header(primary, header0.end)
        prim_rows = resource_entries(primary, header0, names, "CGMeshListResourceWin7")
        gpu_rows = resource_entries(primary, header1, names, "CGMeshListResourceWin7GPU")

        for mi in sorted(set(prim_rows) & set(gpu_rows)):
            _pe, pos, _size = prim_rows[mi]
            _ge, _gpu_pos, gpu_size = gpu_rows[mi]
            start = data_off + pos
            if not any(struct.unpack_from("<8I", primary, start)):
                tally["meshlists_empty"] += 1
                continue
            parsed = parse_candidate(primary, start, gpu_size)
            if parsed is None:
                tally["meshlists_unparsed"] += 1
                continue
            tally["meshlists"] += 1
            n_lodchild = parsed["lodchildindices"].count
            tally["lodchildindices_total"] += n_lodchild
            if n_lodchild:
                tally["meshlists_with_lodchildindices"] += 1

            rp = parsed["renderparams"]
            ml_primset = ml_count = 0
            for i in range(rp.count):
                base = rp.data_off + i * RENDERPARAM_STRIDE
                primset = struct.unpack_from("<I", primary, base + RP_LODPRIMSETIDX)[0]
                start_i = struct.unpack_from("<I", primary, base + RP_LODCHILDRENSTART)[0]
                count_i = struct.unpack_from("<I", primary, base + RP_LODCHILDRENCOUNT)[0]
                tally["renderparams"] += 1
                if primset != NO_LOD_PRIMSET:
                    tally["rp_primset_set"] += 1
                    ml_primset += 1
                if start_i:
                    tally["rp_childrenstart_set"] += 1
                if count_i:
                    tally["rp_childrencount_set"] += 1
                    ml_count += 1
            if hits is not None and (n_lodchild or ml_primset or ml_count):
                hits.append((archive_hash, f"{mi:016x}", parsed["meshes"].count,
                             rp.count, n_lodchild, ml_primset, ml_count,
                             names.get(mi, "")))
    finally:
        del primary
        gc.collect()
    return tally


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", help="audit one archive instead of the manifest")
    ap.add_argument("--limit-archives", type=int, default=None)
    args = ap.parse_args()

    from le_archive_decode import load_hash_lookup
    names = load_hash_lookup(Path("hash_lookup.json"))
    if not names:
        print("WARN: hash_lookup.json resolved empty — run from the repository root with a "
              "RELATIVE path (an absolute one silently yields {})")
        return 2

    archives = [args.archive.lower()] if args.archive else manifest_archives(MANIFEST_TSV)
    if args.limit_archives:
        archives = archives[:args.limit_archives]

    total = Counter()
    hits: list = []
    done = failed = 0
    for h in archives:
        try:
            t = audit_archive(h, names, hits)
        except Exception as exc:   # noqa: BLE001
            failed += 1
            print(f"  SKIP {h}: {exc}")
            continue
        total.update(t)
        done += 1
        print(f"  {h}: meshlists={t['meshlists']} rp={t['renderparams']} "
              f"lodchildindices={t['lodchildindices_total']} "
              f"rp_lod_set={t['rp_primset_set']}/{t['rp_childrenstart_set']}/"
              f"{t['rp_childrencount_set']}")

    print(f"\n=== mesh-list LOD field audit: {done} archives ({failed} skipped) ===")
    print(f"  mesh-lists parsed            {total['meshlists']}")
    print(f"  mesh-lists empty (all-zero)  {total['meshlists_empty']}")
    print(f"  mesh-lists unparsed          {total['meshlists_unparsed']}")
    print(f"  renderparams examined        {total['renderparams']}")
    print(f"  lodchildindices entries      {total['lodchildindices_total']} "
          f"(in {total['meshlists_with_lodchildindices']} mesh-lists)")
    print(f"  lodprimsetidx != 0xFFFFFFFF  {total['rp_primset_set']}")
    print(f"  lodchildrenstart != 0        {total['rp_childrenstart_set']}")
    print(f"  lodchildrencount != 0        {total['rp_childrencount_set']}")
    if hits:
        print(f"\n  --- {len(hits)} mesh-lists POPULATE the chain ---")
        print("  archive          meshlist         meshes    rp  lodchild  rp_primset  rp_count  name")
        for a, m, nm, nrp, nlc, nps, nct, name in hits:
            print(f"  {a} {m} {nm:6d} {nrp:5d} {nlc:9d} {nps:11d} {nct:9d}  {name}")
    inert = (total["lodchildindices_total"] == 0 and total["rp_primset_set"] == 0
             and total["rp_childrenstart_set"] == 0 and total["rp_childrencount_set"] == 0)
    print(f"\n  VERDICT: mesh-list LOD is {'INERT (no-op) corpus-wide' if inert else 'POPULATED in a minority of mesh-lists'}")
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main())
