"""audit_static_lod_corpus — adversarial corpus check of `le_mesh.static_lod`.

READ-ONLY. The `SGStaticInstanceLODData` claim set was derived from exactly TWO
static-scatter masters (`942c829457a04a62` station_front, `4c47d84c1e52447a`
min_itc). This walks EVERY archive in the archive manifest, pulls its populated
`CGStaticInstanceResourceWin7` master with the OOM-safe ranged reader, runs the
shipped decoder, and tries to BREAK each load-bearing claim:

  G1  the walk is byte-exact: the inline `CGMeshListData` parsed forward from
      `meshlist_offset` must END exactly where the backwards tail walk starts
      (`instancescount` header). No inter-table padding, no slack.
  G2  `ditherfadeflags.count == ceil(num_meshes/32)` (a per-MESH bitset).
  G3  `totalnumlods` is authoritative: `<= len(nodelookup)`, and every
      `lodfadelookup[i] < totalnumlods`.
  G4  `nodelookup` is monotonic non-decreasing (what makes
      `level = lod_index - first_lod_index_of(node)` well defined) and each
      node's LOD entries are contiguous.
  G5  after decode, EVERY LOD group is represented at the importer's default
      LOD 0 (nothing silently vanishes).
  L2  every LOD level of a group uses a DISJOINT mesh set.
  Q1  `nodes[i].w == 0` in every row.
  Q2  `hierlods` ranges are in-bounds and NO instance references a parent entry.
  Q3  `visstrlookup` is constant per LOD group, a bijection onto `0..ngroups-1`,
      and `numvisentries` equals its distinct count.

FALSIFIED here, so counted rather than asserted (see docs/LOD.md):

  T1  "per LOD group, TOTAL triangles are NON-INCREASING with level" — true on
      station_front and min_itc, false on 196 of 72,004 groups corpus-wide.
  T2  `hierlods[i].parent == i` — true on 13 of the 14 masters that carry any.

Anything that fails is printed with the offending bytes/indices. A per-master
JSON detail dump (`--dump DIR`) carries the raw `hierlods`, node-w stats and
`lodfadeslopeoffs` histograms so the open questions can be worked offline without
re-reading the archives; `--save-blobs DIR` writes each master's primary bytes so
every later pass runs under plain `python3` with no archive access at all.

MUST run under Windows Python (python.exe) so le_oodle can load the Oodle DLL,
from the repository root with a RELATIVE hash_lookup path:

    python.exe blender_tool/tests/audit_static_lod_corpus.py --dump /tmp/lodaudit

Heavy: one archive at a time, freed between (memory guard). Never parallel.
Not named `test_*.py`, so `tests/run_tests.py` never imports it.
"""

from __future__ import annotations

import argparse
import csv
import gc
import os
import json
import math
import struct
import sys
import traceback
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

MESH_STRIDE = 0x80
RP_STRIDE = 0x68
VB_STRIDE = 0x130
IB_STRIDE = 0x10
M_RENDERPARAMIDX = 0x1C
M_NUMRENDERPARAMS = 0x20
RP_PRIMTYPE = 0x40
RP_IDXCOUNT = 0x48
PRIMTYPE_TRIANGLES = 4
_TAIL = 24


def manifest_archives(path: Path) -> list[str]:
    seen, out = set(), []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            h = (row.get("archive_hash") or "").strip().lower()
            if h and h not in seen:
                seen.add(h)
                out.append(h)
    return out


def walk_inline_meshlist(blob: bytes, off: int) -> dict:
    """Forward-walk the inline CGMeshListData in loader order; return tables+end.

    Deliberately NOT `parse_candidate`: that one needs the paired GPU size and
    applies heuristic accept/reject. Here the point is the raw arithmetic, so a
    drift shows up as a mismatched end offset rather than a rejected candidate.
    """
    tables = {}
    for name, stride in (("meshes", MESH_STRIDE), ("renderparams", RP_STRIDE),
                         ("vertexbuffers", VB_STRIDE), ("morphbuffers", VB_STRIDE),
                         ("morphindexbuffers", IB_STRIDE), ("indexbuffers", IB_STRIDE),
                         ("lodchildindices", 4), ("cbufferidx", 4)):
        count = struct.unpack_from("<I", blob, off)[0]
        data = off + 4
        off = data + count * stride
        if off > len(blob):
            raise ValueError(f"inline meshlist table {name} count={count} runs off the blob")
        tables[name] = (count, data)
    numcbuffers, cbufferoffset = struct.unpack_from("<2I", blob, off)
    gpudatasize = struct.unpack_from("<Q", blob, off + 8)[0]
    tables["end"] = off + 16
    tables["numcbuffers"] = numcbuffers
    tables["cbufferoffset"] = cbufferoffset
    tables["gpudatasize"] = gpudatasize
    return tables


def mesh_triangles(blob: bytes, tables: dict) -> list[int]:
    """Triangles per mesh = sum of its triangle-list renderparams' idxcount/3."""
    n_mesh, mesh_at = tables["meshes"]
    n_rp, rp_at = tables["renderparams"]
    tris = []
    for m in range(n_mesh):
        base = mesh_at + m * MESH_STRIDE
        first = struct.unpack_from("<I", blob, base + M_RENDERPARAMIDX)[0]
        num = struct.unpack_from("<I", blob, base + M_NUMRENDERPARAMS)[0]
        t = 0
        for r in range(first, min(first + num, n_rp)):
            rb = rp_at + r * RP_STRIDE
            if struct.unpack_from("<I", blob, rb + RP_PRIMTYPE)[0] == PRIMTYPE_TRIANGLES:
                t += struct.unpack_from("<I", blob, rb + RP_IDXCOUNT)[0] // 3
        tris.append(t)
    return tris


def tail_table_starts(n: int, num_meshes: int, num_instances: int) -> dict:
    """Re-derive the backwards-walk header offsets (the decoder does not expose them)."""
    end = n - _TAIL
    nwords = -(-num_meshes // 32)
    e6 = end - 4 - nwords * 4
    e5 = e6 - 4 - num_instances * 4
    e4 = e5 - 4 - num_instances * 2
    e3 = e4 - 4 - num_instances * 1
    e2 = e3 - 4 - num_meshes * 12
    e1 = e2 - 4 - num_meshes * 4
    e0 = e1 - 4 - num_meshes * 4
    return {"ditherfadeflags": e6, "lodfadelookup": e5, "visstrlookup": e4,
            "dirlightmasks": e3, "irrsamplelocs": e2, "instanceoffsets": e1,
            "instancescount": e0}


def audit_master(archive: str, blob: bytes, name_hash: int, detail: dict) -> list[str]:
    """Run every invariant against one decoded master. Returns failure strings."""
    from le_static_scatter import decode_static_master
    from le_mesh.static_lod import decode_static_lod

    fails: list[str] = []
    d = decode_static_master(blob)
    detail.update(master=f"{name_hash:016x}", blob_size=len(blob),
                  num_meshes=d.num_meshes, num_instances=d.num_instances,
                  master_warnings=list(d.warnings))
    lod = decode_static_lod(blob, d.num_meshes, d.num_instances)
    detail["lod_warnings"] = list(lod.warnings)
    detail["n_nodes"] = len(lod.nodes)
    detail["n_fades"] = len(lod.fades)
    detail["n_nodelookup"] = len(lod.nodelookup)
    detail["totalnumlods"] = lod.totalnumlods
    detail["n_hierlods"] = len(lod.hierlods)
    detail["meshlist_offset"] = lod.meshlist_offset

    starts = tail_table_starts(len(blob), d.num_meshes, d.num_instances)

    # --- G1: forward meshlist walk must land exactly on the tail walk ---------
    try:
        ml = walk_inline_meshlist(blob, lod.meshlist_offset)
    except ValueError as exc:
        fails.append(f"G1 inline meshlist unwalkable: {exc}")
        ml = None
    if ml is not None:
        detail["meshlist_end"] = ml["end"]
        detail["instancescount_hdr"] = starts["instancescount"]
        detail["meshlist_counts"] = {k: v[0] for k, v in ml.items()
                                     if isinstance(v, tuple)}
        if ml["meshes"][0] != d.num_meshes:
            fails.append(f"G1 meshlist mesh count {ml['meshes'][0]} != num_meshes {d.num_meshes}")
        if ml["end"] != starts["instancescount"]:
            gap = starts["instancescount"] - ml["end"]
            fails.append(
                f"G1 PADDING/SLACK: meshlist ends at {ml['end']}, instancescount "
                f"header at {starts['instancescount']} (gap {gap} B); "
                f"bytes@end={blob[ml['end']:ml['end'] + 16].hex()}")
        if ml["gpudatasize"] != d.gpu_instancetypedata[0] + d.gpu_instancetypedata[1]:
            detail["gpudatasize_vs_tail"] = [ml["gpudatasize"],
                                             d.gpu_instancetypedata[0] + d.gpu_instancetypedata[1]]
        if ml["lodchildindices"][0]:
            detail["inline_lodchildindices"] = ml["lodchildindices"][0]

    # --- G2: ditherfadeflags is a ceil(num_meshes/32) bitset -----------------
    want_words = -(-d.num_meshes // 32)
    if len(lod.ditherfadeflags) != want_words:
        fails.append(f"G2 ditherfadeflags {len(lod.ditherfadeflags)} words != ceil({d.num_meshes}/32)={want_words}")
    pop = sum(bin(w).count("1") for w in lod.ditherfadeflags)
    detail["dither_bits_set"] = pop
    detail["dither_words"] = len(lod.ditherfadeflags)
    # bits above num_meshes must be zero if it really is a per-mesh bitset
    stray = [m for m in range(d.num_meshes, want_words * 32) if lod.dither_fade(m)]
    if stray:
        fails.append(f"G2 ditherfadeflags has {len(stray)} bits set ABOVE num_meshes "
                     f"(first {stray[:8]}) -> not a tight per-mesh bitset")

    # --- G3: totalnumlods authoritative --------------------------------------
    raw_rows = len(lod.nodelookup) + (
        lod.totalnumlods - len(lod.nodelookup) if lod.totalnumlods > len(lod.nodelookup) else 0)
    if lod.totalnumlods > raw_rows:
        fails.append(f"G3 totalnumlods {lod.totalnumlods} > nodelookup rows")
    mx = max(lod.lodfadelookup) if lod.lodfadelookup else -1
    detail["max_lodfadelookup"] = mx
    if lod.totalnumlods and mx >= lod.totalnumlods:
        fails.append(f"G3 max(lodfadelookup)={mx} >= totalnumlods={lod.totalnumlods}")
    if mx >= len(lod.nodelookup):
        fails.append(f"G3 max(lodfadelookup)={mx} out of nodelookup range {len(lod.nodelookup)}")
    if lod.totalnumlods != len(lod.nodelookup):
        detail["totalnumlods_slack"] = len(lod.nodelookup) - lod.totalnumlods

    # --- G4: nodelookup monotonic + contiguous -------------------------------
    nl = lod.nodelookup
    bad_mono = [i for i in range(1, len(nl)) if nl[i] < nl[i - 1]]
    detail["nodelookup_monotonic"] = not bad_mono
    hier_parents = {h[0] for h in lod.hierlods}
    front = (max(hier_parents) + 1) if hier_parents else 0
    detail["hier_parent_region"] = front
    if bad_mono:
        i = bad_mono[0]
        where = "inside the hierlods parent region" if max(bad_mono) < front else "in LEAF entries"
        msg = (f"nodelookup NOT monotonic: {len(bad_mono)} descents, first at "
               f"{i} ({nl[i-1]} -> {nl[i]}), {where}")
        # a descent among unreferenced parent entries cannot change any level
        (detail.setdefault("notes", []).append(msg) if max(bad_mono) < front
         else fails.append("G4 " + msg))
    if any("not contiguous" in w for w in lod.warnings):
        w = "; ".join(x for x in lod.warnings if "not contiguous" in x)
        (detail.setdefault("notes", []).append(w) if front
         else fails.append("G4 " + w))
    if any("out of range" in w for w in lod.warnings):
        fails.append("G4 " + "; ".join(w for w in lod.warnings if "out of range" in w)[:300])
    # do the node ids used by nodelookup index the nodes table?
    if nl and max(nl) >= len(lod.nodes):
        fails.append(f"G4 nodelookup max node {max(nl)} >= nodes count {len(lod.nodes)}")
    detail["distinct_nodes_used"] = len(set(nl))

    # --- L1/L2: triangles non-increasing; levels use disjoint meshes ---------
    if ml is not None:
        tris = mesh_triangles(blob, ml)
        per = {}     # (group, level) -> [tris, set(meshes)]
        for i, node in enumerate(lod.group_of_instance):
            if node < 0:
                continue
            m = d.mesh_for_instance(i)
            slot = per.setdefault((node, lod.level_of_instance[i]), [0, set()])
            slot[0] += tris[m] if m < len(tris) else 0
            slot[1].add(m)
        multi = 0
        viol = []
        shared = []
        by_group = {}
        for (g, lv), v in per.items():
            by_group.setdefault(g, {})[lv] = v
        for g, lvls in by_group.items():
            if len(lvls) < 2:
                continue
            multi += 1
            seq = [lvls[k][0] for k in sorted(lvls)]
            if any(seq[k + 1] > seq[k] for k in range(len(seq) - 1)):
                viol.append((g, seq))
            seen_m = set()
            for k in sorted(lvls):
                if seen_m & lvls[k][1]:
                    shared.append((g, k, sorted(seen_m & lvls[k][1])[:4]))
                    break
                seen_m |= lvls[k][1]
        detail["multi_level_groups"] = multi
        detail["tri_violations"] = len(viol)
        detail["tri_violation_examples"] = [[g, s] for g, s in viol[:4]]
        detail["mesh_reuse_across_levels"] = len(shared)
        detail["level_histogram"] = dict(Counter(len(v) for v in by_group.values()))
        if viol:
            detail.setdefault("notes", []).append(
                f"T1 {len(viol)}/{multi} multi-level groups INCREASE triangles; "
                f"first: group {viol[0][0]} seq {viol[0][1]}")
        if shared:
            fails.append(f"L2 {len(shared)} groups reuse a mesh across levels; "
                         f"first: group {shared[0][0]} level {shared[0][1]} meshes {shared[0][2]}")

    # --- Q1: nodes[i].w ------------------------------------------------------
    ws = {n[3] for n in lod.nodes}
    detail["node_w_distinct"] = sorted(ws)[:8]
    detail["node_w_all_zero"] = ws in ({0.0}, set())
    if not detail["node_w_all_zero"]:
        nz = [(i, lod.nodes[i][3]) for i in range(len(lod.nodes)) if lod.nodes[i][3] != 0.0]
        fails.append(f"Q1 nodes[i].w NOT all zero: {len(nz)} rows, first {nz[:4]}")

    # --- Q2: hierlods = {parent, firstchild, numchildren}  -------
    hl = lod.hierlods
    detail["hierlods"] = [list(h) for h in hl]
    if any(h[0] != i for i, h in enumerate(hl)):
        bad = [(i, list(h)) for i, h in enumerate(hl) if h[0] != i]
        detail["hierlod_parent_not_index"] = bad[:8]      # T2, corpus: 1 of 14
    refcount = Counter(lod.lodfadelookup)
    ref_par = sum(refcount.get(h[0], 0) for h in hl)
    detail["instances_on_hierlod_parents"] = ref_par
    if ref_par:
        fails.append(f"Q2 {ref_par} instances reference a hierlods PARENT entry "
                     f"(corpus: 0 on all 14 masters that carry hierlods)")
    covered = set()
    oob = []
    for h in hl:
        if h[1] + h[2] > len(nl):
            oob.append(h)
        covered |= set(range(h[1], min(h[1] + h[2], len(nl))))
    detail["hierlod_covered"] = len(covered)
    detail["hierlod_uncovered"] = len(nl) - len(covered)
    detail["hierlod_oob"] = oob[:4]
    detail["hierlod_sum_count"] = sum(h[2] for h in hl)
    # do the record heads sit at the front of the LOD array?
    detail["hierlod_heads_are_prefix"] = sorted(h[1] for h in hl) == list(range(len(hl))) \
        if hl else None

    # --- G5: nothing vanishes at the importer's default LOD 0 ----------------
    from le_mesh.static_lod import select_lod_instances
    keep = select_lod_instances(0, lod.group_of_instance, lod.level_of_instance,
                                lod.group_num_levels)
    at0 = {lod.group_of_instance[i] for i in keep}
    allg = {g for g in lod.group_of_instance if g >= 0}
    if allg - at0:
        miss = sorted(allg - at0)
        ninst = sum(1 for g in lod.group_of_instance if g in set(miss))
        fails.append(f"G5 {len(miss)} LOD groups / {ninst} instances vanish at the "
                     f"default LOD 0 (first groups {miss[:6]})")

    # --- Q3: visstrlookup identity / per-group constancy ---------------------
    detail["numvisentries"] = lod.numvisentries
    vs_at = starts["visstrlookup"] + 4
    if d.num_instances and vs_at + d.num_instances * 2 <= len(blob):
        vs = lod.visstrlookup
        ident = all(vs[i] == (i & 0xFFFF) for i in range(len(vs)))
        detail["visstrlookup_identity"] = ident
        if not ident:
            first = next(i for i in range(len(vs)) if vs[i] != (i & 0xFFFF))
            detail["visstrlookup_first_diff"] = [first, vs[first]]
        detail["visstrlookup_distinct"] = len(set(vs))
        detail["visstrlookup_max"] = max(vs)
        # is it constant across every instance of a LOD group, and a bijection
        # group <-> value? (that would make it a per-PROP visibility index)
        per_group: dict = {}
        multi = 0
        for i, g in enumerate(lod.group_of_instance):
            s = per_group.setdefault(g, set())
            s.add(vs[i])
        multi = sum(1 for s in per_group.values() if len(s) > 1)
        detail["visstr_groups_with_multiple_values"] = multi
        detail["visstr_constant_per_group"] = (multi == 0)
        vals = [next(iter(s)) for s in per_group.values() if len(s) == 1]
        detail["visstr_bijective_with_group"] = (multi == 0 and len(set(vals)) == len(vals))
        detail["visstr_sorted_equals_range"] = (
            multi == 0 and sorted(vals) == list(range(len(vals))))
        if not (multi == 0 and sorted(vals) == list(range(len(vals)))
                and lod.numvisentries == len(set(vs))):
            fails.append(
                f"Q3 visstrlookup is not a per-group bijection onto 0..n-1 "
                f"(groups with >1 value {multi}, distinct {len(set(vs))}, "
                f"numvisentries {lod.numvisentries}, groups {len(per_group)})")

    # --- lodfadeslopeoffs corpus stats (STILL OPEN) --------------------------
    # The one hard structural fact: a row is either ALL-positive or ALL-negative
    # (59 mixed rows in 262,132 corpus-wide) and the split is within +/-1 of
    # exactly half on 62/62 masters. No proposed model explains that yet.
    fades = lod.fades
    detail["fade_rows_all_positive"] = sum(
        1 for f in fades if all(math.copysign(1.0, v) > 0 for v in f))
    detail["fade_rows_all_negative"] = sum(
        1 for f in fades if all(math.copysign(1.0, v) < 0 for v in f))
    detail["fade_rows_mixed_sign"] = (
        len(fades) - detail["fade_rows_all_positive"] - detail["fade_rows_all_negative"])
    detail["fade_distinct"] = len(set(fades))
    lvl_of_lod = {}
    firsts = {}
    for li, node in enumerate(nl):
        firsts.setdefault(node, li)
        lvl_of_lod[li] = li - firsts[node]
    by_level = {}
    for li, f in enumerate(fades):
        by_level.setdefault(lvl_of_lod.get(li, -1), Counter())[tuple(round(v, 4) for v in f)] += 1
    detail["fade_top_by_level"] = {
        str(k): [[list(row), cnt] for row, cnt in c.most_common(4)]
        for k, c in sorted(by_level.items())}
    detail["fade_xy_eq_zw"] = sum(1 for f in fades if f[0] == f[2] and f[1] == f[3])
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", action="append", help="audit only these archives")
    ap.add_argument("--limit-archives", type=int, default=None)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--dump", type=Path, default=None, help="per-master JSON detail dir")
    ap.add_argument("--save-blobs", type=Path, default=None,
                    help="write each master's raw primary bytes here so all later "
                         "analysis runs under python3 with no archive access")
    ap.add_argument("--tsv", type=Path, default=None, help="one summary row per archive")
    args = ap.parse_args()

    from le_archive_decode import load_hash_lookup
    from le_static_scatter import load_master_blob

    names = load_hash_lookup(Path("hash_lookup.json"))
    if not names:
        print("WARN: hash_lookup.json resolved empty — run from the repository root with a "
              "RELATIVE path (an absolute one silently yields {})")
        return 2

    archives = args.archive or manifest_archives(MANIFEST_TSV)
    archives = archives[args.start:]
    if args.limit_archives:
        archives = archives[:args.limit_archives]
    if args.dump:
        args.dump.mkdir(parents=True, exist_ok=True)
    if args.save_blobs:
        args.save_blobs.mkdir(parents=True, exist_ok=True)

    rows = []
    n_master = n_empty = n_raised = n_failed = 0
    for k, h in enumerate(archives):
        detail = {"archive": h}
        try:
            name_hash, blob = load_master_blob(h)
        except Exception as exc:                                   # noqa: BLE001
            print(f"[{k+1}/{len(archives)}] {h}: LOAD ERROR {type(exc).__name__}: {exc}")
            n_raised += 1
            continue
        if blob is None:
            print(f"[{k+1}/{len(archives)}] {h}: no populated master")
            n_empty += 1
            gc.collect()
            continue
        n_master += 1
        if args.save_blobs:
            (args.save_blobs / f"{h}.{name_hash:016x}.bin").write_bytes(blob)
        try:
            fails = audit_master(h, blob, name_hash, detail)
        except Exception as exc:                                   # noqa: BLE001
            n_raised += 1
            detail["raised"] = f"{type(exc).__name__}: {exc}"
            detail["traceback"] = traceback.format_exc()
            print(f"[{k+1}/{len(archives)}] {h} master={name_hash:016x} "
                  f"size={len(blob)}: *** RAISED *** {detail['raised']}")
            print("    front64=" + blob[:64].hex())
            print("    tail64=" + blob[-64:].hex())
            fails = ["RAISED"]
        else:
            tag = "OK " if not fails else "FAIL"
            print(f"[{k+1}/{len(archives)}] {h} master={name_hash:016x} "
                  f"meshes={detail.get('num_meshes')} inst={detail.get('num_instances')} "
                  f"nodes={detail.get('n_nodes')} lods={detail.get('totalnumlods')} "
                  f"hier={detail.get('n_hierlods')} {tag}")
            for w in detail.get("lod_warnings", []):
                print(f"      warn: {w}")
            for note in detail.get("notes", []):
                print(f"      note: {note}")
            for f in fails:
                print(f"      FAIL: {f}")
        if fails:
            n_failed += 1
        detail["fails"] = fails
        rows.append(detail)
        if args.dump:
            (args.dump / f"{h}.json").write_text(json.dumps(detail), encoding="utf-8")
        del blob
        gc.collect()

    print(f"\n=== static-LOD corpus audit: {len(archives)} archives ===")
    print(f"  populated masters      {n_master}")
    print(f"  no populated master    {n_empty}")
    print(f"  raised                 {n_raised}")
    print(f"  archives with failures {n_failed}")
    agg = Counter()
    for r in rows:
        for f in r.get("fails", []):
            agg[f.split(" ", 1)[0]] += 1
    if agg:
        print("  failures by class: " + ", ".join(f"{k}={v}" for k, v in sorted(agg.items())))
    if args.tsv:
        cols = ["archive", "master", "num_meshes", "num_instances", "n_nodes",
                "n_fades", "n_nodelookup", "totalnumlods", "n_hierlods",
                "hierlod_covered", "hierlod_uncovered", "multi_level_groups",
                "tri_violations", "mesh_reuse_across_levels", "node_w_all_zero",
                "visstrlookup_identity", "dither_bits_set", "dither_words",
                "fade_distinct", "fade_rows_all_positive", "fade_rows_all_negative",
                "fade_rows_mixed_sign", "instances_on_hierlod_parents",
                "hier_parent_region", "numvisentries", "meshlist_offset",
                "meshlist_end", "instancescount_hdr", "nodelookup_monotonic"]
        with open(args.tsv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, delimiter="\t")
            w.writerow(cols + ["fails"])
            for r in rows:
                w.writerow([r.get(c, "") for c in cols] + ["|".join(r.get("fails", []))])
        print(f"  wrote {args.tsv}")
    return 1 if (n_failed or n_raised) else 0


if __name__ == "__main__":
    raise SystemExit(main())
