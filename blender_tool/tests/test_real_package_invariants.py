"""Open the REAL packages on disk and assert the laws this tree has actually broken.

★ WHY THIS FILE EXISTS.

Every other `test_*.py` in this directory either builds a synthetic manifest or
hardcodes a fixture that was *copied out of* a probe run. Both are fine tests of a
FUNCTION. Neither is a test of the DATA — and the data is where this tree's worst
defects have lived:

  * D1 — the scene-set mask read as an LOD axis deleted the player
    avatar's left arm and both hands (7 of 14 meshes at "LOD 0") while the suite
    was green at 780 tests, because no test opened `64b4b5b2a0153f7e`.
  * The stacked-proxy defect — Liv's LOD-3 body proxy was drawn on top of her in
    every render this tree had ever made, for the same reason.
  * D4 — 48,374 co-planar duplicate triangles on that same avatar.
  * D2 — `select_lod_objects` returning the EMPTY SET.
  * D3 — an ungated mesh (Jack's whole lower body, 14,424 v) vanishing
    at every level >= 1.

Each fix landed with a unit test over a hardcoded fixture. That proves the POLICY
is right. It cannot prove the packages on disk — which is what the importer
actually reads — satisfy it. This file closes that gap.

⚠ SKIPS ARE LOUD, NEVER SILENT. `blender_tool/exports/` and `fixtures/*.lemesh/`
are gitignored (extracted game data must not be committed), so on a clean
checkout there is nothing here to open. Every test then raises
`unittest.SkipTest` with a reason — counted and reprinted by `tests/run_tests.py`
and by pytest. A silently-returning test would recreate the exact blind spot this
file exists to close.

⛔ SCOPE. Only STABLE contracts: `le_mesh.package`'s manifest schema,
`le_mesh.vertex_format`'s element table, and `package_reader`'s public selection
surface. No material node building, no in-flight module.

COST. Manifest laws sweep the WHOLE corpus from one cached parse (~300 manifests,
13 MB, parsed once per process). Filesystem laws (`stat` per blob, per texture)
are bounded to a curated set by default because the corpus lives on a mounted
Windows drive where `stat` dominates; `LE_FULL_SWEEP=1` runs them over everything.
"""

from __future__ import annotations

import json
import os
import sys
from array import array
from collections import Counter
from pathlib import Path
from unittest import SkipTest

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for _p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon" / "lone_echo_import")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ⚠ import the MODULE, not the package: `lone_echo_import/__init__.py` imports
# bpy, which `run_tests.py` runs without.
from package_reader import (                                    # noqa: E402
    object_is_scene_set_ungated, scene_lod_is_geometric_chain,
    select_lod_draws, select_lod_objects,
)
from le_mesh import materials as lemat                          # noqa: E402
from le_mesh.package import lightmap_uv_for_manifest_object     # noqa: E402
from le_mesh.vertex_format import lightmap_uv_attr_name         # noqa: E402

SEARCH_ROOTS = (BLENDER_TOOL / "fixtures", BLENDER_TOOL / "exports")

#: the packages that carry `scene_lod_level` — where D1-D4 live.
CHARS = BLENDER_TOOL / "exports" / "chars"

#: every dtype the writer emits is 4 bytes wide (`package._DTYPE_TO_ARRAYCODE`).
DTYPE_SIZE = {"float32": 4, "uint32": 4, "int32": 4}

#: how many `exports/` packages the filesystem laws touch when not sweeping all.
_FS_SAMPLE = 24


# ---------------------------------------------------------------------------
# discovery + a one-parse cache
# ---------------------------------------------------------------------------

_CACHE: list[tuple[Path, dict]] | None = None


def corpus() -> list[tuple[Path, dict]]:
    """`[(package_dir, manifest)]` for every real package, parsed once."""
    global _CACHE
    if _CACHE is None:
        found: list[Path] = []
        for r in SEARCH_ROOTS:
            if r.is_dir():
                found += [mf.parent for mf in r.glob("**/manifest.json")]
        _CACHE = []
        for pd in sorted(set(found)):
            try:
                _CACHE.append((pd, json.loads(
                    (pd / "manifest.json").read_text(encoding="utf-8"))))
            except Exception as exc:                             # noqa: BLE001
                _CACHE.append((pd, {"format": "UNREADABLE", "error": str(exc)}))
    return _CACHE


def meshes() -> list[tuple[Path, dict]]:
    return [(p, m) for p, m in corpus()
            if m.get("format") == "lemesh" and m.get("objects")]


def scatters() -> list[tuple[Path, dict]]:
    return [(p, m) for p, m in corpus() if m.get("format") == "le_scatter"]


def _fs_subset() -> list[tuple[Path, dict]]:
    """Packages the `stat`-heavy laws touch. Curated + a sample, or everything."""
    all_m = meshes()
    if os.environ.get("LE_FULL_SWEEP"):
        return all_m
    # ⚠ partition by PATH, not by (path, manifest) membership: comparing whole
    # manifest dicts for `in` is O(n * m) over megabytes of nested data.
    keep = {p for p, _m in all_m if p.parent.name in ("fixtures", "chars")}
    curated = [(p, m) for p, m in all_m if p in keep]
    rest = [(p, m) for p, m in all_m if p not in keep]
    return curated + rest[:_FS_SAMPLE]


def _rel(pd: Path) -> str:
    """A package's path relative to `blender_tool/` — the BASENAME is ambiguous
    (`exports/` and `exports/jack_probe/` hold same-named copies of Jack, one
    fixed and one stale), and telling them apart is the whole point of the
    staleness report below."""
    try:
        return str(pd.relative_to(BLENDER_TOOL))
    except ValueError:
        return str(pd)


def _dir_sizes(d: Path) -> dict[str, int]:
    """`{filename: size}` for one directory, in ONE scan.

    ⚠ The corpus lives on a mounted Windows drive where a single `Path.is_file()`
    costs ~100 ms; the naive per-blob `stat` took 110 s over 33 packages. One
    `os.scandir` per directory instead of one syscall per file is the difference
    between this test being run and being switched off.
    """
    try:
        with os.scandir(d) as it:
            return {e.name: e.stat().st_size for e in it if e.is_file()}
    except OSError:
        return {}


def _require(items, what: str) -> None:
    if not items:
        raise SkipTest(
            f"no {what} on disk — `exports/` and `fixtures/*.lemesh/` are "
            f"gitignored extracted game data, so a clean checkout has none. "
            f"Re-extract with `python.exe blender_tool/extractor/le_extract.py "
            f"--archive <hash> --all` to make this test able to run. ⛔ WHILE "
            f"THIS SKIP IS ACTIVE THE SUITE CANNOT SEE ANY DATA-SIDE DEFECT.")


def _report(viol: Counter, ex: dict, checked: int, label: str) -> None:
    """Fail with EVERY violation class at once, not just the first."""
    if not viol:
        return
    lines = [f"{label}: {sum(viol.values())} violation(s) over {checked} package(s)"]
    for rule, n in viol.most_common():
        lines.append(f"  {rule}: {n}")
        for e in ex.get(rule, [])[:5]:
            lines.append(f"      {e}")
    raise AssertionError("\n".join(lines))


# ---------------------------------------------------------------------------
# 0. the inventory — what this run can and cannot see
# ---------------------------------------------------------------------------

def test_package_corpus_inventory_is_reported():
    """State out loud how many real packages this run opened.

    Not an assertion about the data: an assertion about the RUN. A suite that
    quietly checks nothing is the failure mode this file was written for, so the
    count is printed every time and the test skips — loudly — at zero.
    """
    c = corpus()
    _require(c, "packages")
    kinds = Counter(m.get("format", "?") for _p, m in c)
    print(f"    [real-package corpus] {len(c)} packages: "
          + ", ".join(f"{k}={n}" for k, n in sorted(kinds.items()))
          + ("" if os.environ.get("LE_FULL_SWEEP")
             else f"; filesystem laws bounded to {len(_fs_subset())} "
                  f"(set LE_FULL_SWEEP=1 for all)"))
    assert kinds.get("UNREADABLE", 0) == 0, [p for p, m in c
                                             if m.get("format") == "UNREADABLE"]
    assert kinds.get("lemesh", 0) > 0, kinds


# ---------------------------------------------------------------------------
# 1. manifest laws — the whole corpus, no filesystem access
# ---------------------------------------------------------------------------

def test_manifest_identity_and_reference_laws_hold_corpus_wide():
    """The self-consistency laws of `le_mesh.package.write_package`:

      `name_prefix` / `name_hash`   the object `name` must be
          `obj{mesh_index:03d}_{name_hash}`; the addon and every audit table
          join on that string.
      `draw_out_of_range`           a draw's `[idx_start, idx_start+idx_count)`
          must lie inside the index buffer — an off-by-one in the
          `CGRenderParams` walk shows up here and nowhere else offline.
      `material_key_composition`    `key == f"{shaderset_hash}__{material_hash}"`.
      `material_key_unresolved`     every draw's `material_key` must name a
          material the manifest actually carries. 243 packages ship a materials
          section and 0 dangling references — a real, tight invariant.
      `role_layer_disagrees`        a channel's `layer` must equal the layer its
          own `role_key` splits to.
      `scatter_mesh_count`          `num_meshes == len(meshes)`.

    ⛔ Deliberately NOT asserted: `draws[].material_index`. It indexes the
    ARCHIVE's material table, not `manifest["materials"]`; 201 draws corpus-wide
    carry an index >= `len(materials)` and every one of them resolves correctly
    by `material_key`. Asserting on it would have pinned a wrong guess as law.
    """
    _require(corpus(), "packages")
    viol: Counter = Counter()
    ex: dict[str, list[str]] = {}

    def bad(rule: str, detail: str) -> None:
        viol[rule] += 1
        ex.setdefault(rule, []).append(detail)

    for pd, m in meshes():
        mat_keys = {mm.get("key") for mm in (m.get("materials") or [])}
        for mm in (m.get("materials") or []):
            key = mm.get("key") or ""
            sh, _, mh = key.partition("__")
            if sh != mm.get("shaderset_hash") or mh != mm.get("material_hash"):
                bad("material_key_composition", f"{pd.name} {key}")
            for cname, ch in (mm.get("channels") or {}).items():
                rk = ch.get("role_key") or ""
                if not rk:
                    bad("role_key_empty", f"{pd.name} {key}.{cname}")
                    continue
                layer, _suffix = lemat.split_role(rk)
                if ch.get("layer") is not None and ch["layer"] != layer:
                    bad("role_layer_disagrees",
                        f"{pd.name} {rk} layer={ch['layer']} split={layer}")
        for o in m["objects"]:
            name, mi = o.get("name", ""), o.get("mesh_index", -1)
            if not name.startswith(f"obj{mi:03d}_"):
                bad("name_prefix", f"{pd.name} {name} mesh_index={mi}")
            if o.get("name_hash") and not name.endswith(o["name_hash"]):
                bad("name_hash", f"{pd.name} {name} != {o.get('name_hash')}")
            icount = int((o.get("index") or {}).get("count") or 0)
            for d in o.get("draws") or []:
                start = int(d.get("idx_start") or 0)
                count = int(d.get("idx_count") or 0)
                if start + count > icount:
                    bad("draw_out_of_range",
                        f"{pd.name} {name} rp{d.get('renderparam_index')} "
                        f"[{start},{start + count}) > {icount}")
                mk = d.get("material_key")
                if mk and mat_keys and mk not in mat_keys:
                    bad("material_key_unresolved", f"{pd.name} {name} {mk}")

    for pd, m in scatters():
        if m.get("num_meshes") is not None and m["num_meshes"] != len(m.get("meshes") or []):
            bad("scatter_mesh_count",
                f"{pd.name} num_meshes={m['num_meshes']} "
                f"len(meshes)={len(m.get('meshes') or [])}")

    print(f"    [manifest laws] {len(meshes())} .lemesh + {len(scatters())} "
          f".lescatter checked")
    _report(viol, ex, len(corpus()), "manifest identity/reference laws")


#: ★ FOUND BY THIS TEST, ON ITS FIRST RUN.
#: `fixtures/0703fd2acd5803e9_8f76d470b7ca990f.lemesh` has an EMPTY `blobs/`
#: directory: its manifest declares six attribute blobs and an index blob and
#: not one of them is on disk. It has been in that state since 2026-07-23, and
#: nothing in a 780- then 828- then 840-test green suite noticed, because
#: nothing opened it. Any consumer that reads it gets a `FileNotFoundError` —
#: or, in the addon's `array` fallback path, an empty mesh.
#:
#: NOT repaired here: extracted packages are data, and `fixtures/*.lemesh/` is
#: gitignored. `tests/test_extractor_e2e.py` re-extracts this exact asset and
#: proves the extractor still produces all seven blobs correctly, so the repair
#: is one command — see docs/TESTING.md. Pinned so the suite stays honest
#: about it rather than green about it.
KNOWN_INCOMPLETE_PACKAGES = {"fixtures/0703fd2acd5803e9_8f76d470b7ca990f.lemesh"}


def test_declared_vertex_counts_match_the_blobs_on_disk():
    """A declared attribute's blob must be exactly
    `vertex_count * comps * 4` bytes, and it must exist.

    This is the shape a truncated or mis-strided vertex decode takes on disk: the
    manifest still claims N vertices, and Blender's `foreach_set` then reads past
    the end or silently short-fills. Also checks the index blob against its own
    declared `count`, and every `textures/<hash>.dds` a channel promises.

    A package whose `blobs/` directory is entirely EMPTY is a different animal —
    an aborted or half-copied extraction, not a decode defect — so it is reported
    separately against `KNOWN_INCOMPLETE_PACKAGES` and a NEW one fails.

    ⚠ Filesystem-bound, so bounded by default; `LE_FULL_SWEEP=1` runs it over
    every package. Measured over all 292 `.lemesh` packages (1,173 objects):
    0 size mismatches, 0 missing textures, and exactly the one empty package.
    """
    subset = _fs_subset()
    _require(subset, "packages")
    viol: Counter = Counter()
    ex: dict[str, list[str]] = {}
    incomplete: list[str] = []

    def bad(rule: str, detail: str) -> None:
        viol[rule] += 1
        ex.setdefault(rule, []).append(detail)

    n_blobs = 0
    for pd, m in subset:
        blobs = _dir_sizes(pd / "blobs")
        textures = _dir_sizes(pd / "textures")
        wants_blobs = any(a.get("blob")
                          for o in m["objects"]
                          for a in (o.get("attributes") or {}).values())
        if wants_blobs and not blobs:
            incomplete.append(_rel(pd))
            continue
        for mm in (m.get("materials") or []):
            for cname, ch in (mm.get("channels") or {}).items():
                rel = ch.get("file")
                if not rel:
                    continue
                if Path(rel).name not in textures:
                    bad("texture_file_missing",
                        f"{_rel(pd)} {mm.get('key')}.{cname} -> {rel}")
                elif ch.get("texture") and Path(rel).stem != ch["texture"]:
                    bad("texture_file_name", f"{_rel(pd)} {rel}")
        for o in m["objects"]:
            vc = int(o.get("vertex_count") or 0)
            for akey, a in (o.get("attributes") or {}).items():
                rel = a.get("blob")
                if not rel:
                    continue
                got = blobs.get(Path(rel).name)
                if got is None:
                    bad("blob_missing", f"{_rel(pd)} {o.get('name')}.{akey} -> {rel}")
                    continue
                n_blobs += 1
                want = vc * int(a.get("comps") or 0) * DTYPE_SIZE.get(a.get("dtype"), 0)
                if want and got != want:
                    bad("blob_size",
                        f"{_rel(pd)} {o.get('name')}.{akey} want={want} got={got} "
                        f"(vertex_count={vc} comps={a.get('comps')})")
            ie = o.get("index") or {}
            if ie.get("blob"):
                got = blobs.get(Path(ie["blob"]).name)
                if got is None:
                    bad("index_blob_missing", f"{_rel(pd)} {o.get('name')}")
                elif got != int(ie.get("count") or 0) * 4:
                    bad("index_size",
                        f"{_rel(pd)} {o.get('name')} "
                        f"want={int(ie.get('count') or 0) * 4} got={got}")

    print(f"    [blob sizes] {n_blobs} blob(s) across {len(subset)} package(s)")
    _report(viol, ex, len(subset), "declared counts vs blobs on disk")
    new_incomplete = set(incomplete) - KNOWN_INCOMPLETE_PACKAGES
    assert not new_incomplete, (
        "package(s) whose `blobs/` directory is EMPTY while the manifest "
        f"declares blobs — an aborted extraction: {sorted(new_incomplete)}")
    if incomplete:
        print(f"    [incomplete] {len(incomplete)} package(s) declare blobs and "
              f"have none on disk — re-extract: {sorted(incomplete)}")


def test_indices_are_within_the_declared_vertex_count():
    """An index >= `vertex_count` is a crash or silent garbage in Blender's
    `foreach_set`. Reads the index blobs of the curated set (fixtures +
    `exports/chars`); `LE_INDEX_CHECK_ALL=1` sweeps everything.
    """
    if os.environ.get("LE_INDEX_CHECK_ALL"):
        subset = meshes()
    else:
        subset = [(p, m) for p, m in meshes()
                  if p.parent.name in ("fixtures", "chars")]
    _require(subset, "packages")
    checked = 0
    for pd, m in subset:
        for o in m["objects"]:
            ie = o.get("index") or {}
            if not ie.get("blob"):
                continue
            a = array("I")
            a.frombytes((pd / ie["blob"]).read_bytes())
            if a:
                assert max(a) < int(o["vertex_count"]), (
                    f"{pd.name} {o['name']}: index {max(a)} >= "
                    f"vertex_count {o['vertex_count']}")
            checked += 1
            del a
    print(f"    [indices] {checked} index buffer(s) range-checked")
    assert checked, "no index buffer was checked"


# ---------------------------------------------------------------------------
# 2. D4 — the scene-set VARIANT duplicate
# ---------------------------------------------------------------------------

def _duplicate_ranges(objects) -> list[str]:
    """Selected draws that cover a BYTE-IDENTICAL index range twice.

    ⚠ Goes through `select_lod_draws` ALONE — the public selection surface the
    addon calls — rather than invoking `drop_scene_set_variant_draws` directly.
    That matters: a test that calls the fix cannot fail when the fix is removed.
    `select_lod_draws` applies the de-dup internally, so deleting it turns this
    test red, which is the whole point.
    """
    dups = []
    for o in objects:
        seen: set[tuple[int, int]] = set()
        for d in select_lod_draws(o.get("draws") or [], 0):
            count = int(d.get("idx_count") or 0)
            if not count:
                continue
            k = (int(d.get("idx_start") or 0), count)
            if k in seen:
                dups.append(f"{o.get('name')} {k}")
            seen.add(k)
    return dups


def _draws_are_classifiable(objects) -> bool:
    """True when every draw carries the keys `drop_scene_set_variant_draws` needs."""
    for o in objects:
        for d in o.get("draws") or []:
            if (d.get("idx_start") is None or d.get("idx_count") is None
                    or d.get("scene_mask") is None):
                return False
    return True


def test_no_selected_draw_duplicates_an_index_range():
    """D4. Two `CGRenderParams` over a byte-identical `(idx_start, idx_count)`
    under different scene sets are material VARIANTS of the same triangles, not
    two surfaces. Emitting both put 48,374 co-planar duplicate triangles on the
    player's own avatar, z-fighting two materials against each other.

    Asserted on every package whose draws can be CLASSIFIED — i.e. that carry
    `scene_mask`. Older ones are covered by the companion test below.
    """
    _require(meshes(), "packages")
    viol: Counter = Counter()
    ex: dict[str, list[str]] = {}
    checked = 0
    for pd, m in meshes():
        if not _draws_are_classifiable(m["objects"]):
            continue
        checked += 1
        for d in _duplicate_ranges(m["objects"]):
            viol["duplicate_index_range"] += 1
            ex.setdefault("duplicate_index_range", []).append(f"{pd.name} {d}")
    if not checked:
        raise SkipTest("no package on disk carries per-draw `scene_mask` — every "
                       "one predates the scene-set decode, so D4 cannot be tested")
    print(f"    [D4] {checked} classifiable package(s) checked")
    _report(viol, ex, checked, "D4 scene-set variant duplicates")


def test_only_unclassifiable_packages_can_still_duplicate_a_range():
    """The staleness law — and it is currently LOAD-BEARING.

    `drop_scene_set_variant_draws` keeps both rows when a draw is missing
    `idx_start` / `idx_count` / `scene_mask`: its documented, deliberate
    fallback. So a package written before those keys existed is UNPROTECTED
    against D4, and four such packages are on disk right now — the pre-backfill
    copies of `64b4b5b2a0153f7e` and `c05a38189f7edd79` under `exports/` and
    `exports/jack_probe/`, which carry `scene_mask: null` on every draw and still
    duplicate 8 and 1 ranges respectively.

    ★ THIS IS THE POINT OF THE FILE. The fix is in the code and the data is a
    generation behind it — exactly the "package freshness" trap of
    docs/CHARACTERS.md §1.7, where D1/D2/D3 were latent purely
    because nothing had been re-extracted. A test over hardcoded fixtures can
    never see that; a test that opens the real packages sees it immediately.

    Asserted: a duplicate range is ONLY ever possible on an unclassifiable
    package. A package that HAS `scene_mask` and still duplicates means the
    de-dup itself regressed.
    """
    _require(meshes(), "packages")
    stale, offenders = [], []
    for pd, m in meshes():
        dups = _duplicate_ranges(m["objects"])
        if not dups:
            continue
        (offenders if _draws_are_classifiable(m["objects"]) else stale).append(
            f"{_rel(pd)}: {len(dups)} dup(s)")
    assert not offenders, (
        "a package WITH scene_mask still duplicates an index range — "
        "`drop_scene_set_variant_draws` has regressed:\n  " + "\n  ".join(offenders))
    if stale:
        print(f"    [D4 staleness] {len(stale)} package(s) predate the scene-set "
              f"decode and are UNPROTECTED against D4 — re-extract: "
              + "; ".join(stale))


# ---------------------------------------------------------------------------
# 3. D2 / D3 — LOD selection must never silently return nothing
# ---------------------------------------------------------------------------

def _gated_levels(objects) -> list[int]:
    return sorted({o.get("scene_lod_level") for o in objects
                   if o.get("scene_lod_level") is not None
                   and not object_is_scene_set_ungated(o)})


def test_lod_selection_is_never_empty_at_level_zero_or_a_level_carried():
    """D2. `select_lod_objects` returned the EMPTY SET whenever the finest scene
    set was not bit 0 — `3cee9f282bf0807f` partitions its 14 meshes into levels
    3 and 4 ONLY, so the default `lod_level = 0` asked for a level no object
    carried and the importer produced nothing. The fix clamped at both ends
    (`want = min(max(level, min(gated)), max(gated))`) and, since D13, snaps into
    the ladder instead — `package_reader.snap_to_ladder`.

    ★ LEVEL 0 IS CHECKED EXPLICITLY, ALWAYS. Checking only the levels a package
    carries would not have caught D2 at all — on `3cee9f282bf0807f` those are 3
    and 4, both non-empty even with the broken clamp. Level 0 is the default
    every importer run uses and the exact level that returned nothing.
    """
    _require(meshes(), "packages")
    viol: Counter = Counter()
    ex: dict[str, list[str]] = {}
    for pd, m in meshes():
        gated = _gated_levels(m["objects"])
        for lv in sorted({0} | set(gated)):
            if not select_lod_objects(m["objects"], lv):
                viol["empty_selection"] += 1
                ex.setdefault("empty_selection", []).append(
                    f"{_rel(pd)} level {lv} of gated {gated}")
    print(f"    [D2] {len(meshes())} package(s) checked at level 0 + every "
          f"gated level")
    _report(viol, ex, len(meshes()), "D2 empty LOD selection")


def test_a_real_lod_chain_selects_a_proper_subset_at_level_zero():
    """The stacked-proxy defect. A character ships every LOD as its
    OWN MESH selected by `SSceneSetMask`, so importing all of them draws Liv's
    LOD-3 body proxy on top of Liv. Every render this tree made before the fix
    had it, and the suite stayed green throughout because nothing opened a
    package and counted.

    On a package `scene_lod_is_geometric_chain` ACCEPTS, level 0 must therefore
    select strictly fewer objects than the package holds — a real chain has
    coarser levels present by definition, and selecting all of them is exactly
    the defect. Measured: helmet 2 of 4, Liv's body 5 of 6, and
    `3ae4822821fa8562` 10 of 19.
    """
    _require(meshes(), "packages")
    checked = []
    for pd, m in meshes():
        levels = [o.get("scene_lod_level") for o in m["objects"]]
        known = [x for x in levels if x is not None]
        if not known or max(known) == 0:
            continue
        ok, _diag = scene_lod_is_geometric_chain(m["objects"])
        if not ok:
            continue
        kept = select_lod_objects(m["objects"], 0)
        checked.append(f"{pd.name} {len(kept)}/{len(m['objects'])}")
        assert len(kept) < len(m["objects"]), (
            f"{_rel(pd)} is an accepted LOD chain yet level 0 selected ALL "
            f"{len(m['objects'])} of its meshes — the coarser levels are being "
            f"stacked on the fine one (the b792c21 proxy defect)")
    if not checked:
        raise SkipTest("no package on disk is an accepted scene-set LOD chain; "
                       "the stacked-proxy law has nothing to run on")
    print(f"    [proxy] LOD 0 selects a proper subset on {'; '.join(checked)}")


#: ★ D13 (FOUND BY THIS FILE; it was numbered **D9** here until the collision
#: with the squared-alpha D9 was resolved — see docs/TESTING.md). FIXED.
#:
#: `select_lod_objects` used to clamp `want = min(max(level, min(gated)),
#: max(gated))` — a FLOOR and a CEILING, and no handling of a HOLE.
#: `2fd6839161785e9c_ff91757c910ea7b6` (Liv's body) partitions its six meshes
#: into levels {0, 3}, so asking for level 1 or 2 landed on a level no object
#: carried and the ENTIRE character disappeared. It was D2 one step further out:
#: D2 fixed "below the ladder"; this was "between its rungs".
#:
#: `package_reader.snap_to_ladder` now snaps to the greatest present rung
#: `<= level` (and to the finest rung only from below the ladder). The rule and
#: its three laws are pinned by `tests/test_lod_ladder_hole.py`; what this file
#: keeps is the CORPUS half — that no package on disk selects nothing.
#:
#: ⚠ THE MEANING OF THIS SET CHANGED WITH THE FIX. It used to list packages the
#: importer DELETED at some level — a defect register that had to empty. It now
#: lists packages whose gated ladder is SPARSE, which is a fact about the shipped
#: data and stays true forever; the defect is gone, the sparse ladder is not. A
#: new name appearing here is not a failure, it is a package whose hole levels
#: must be re-checked by hand.
KNOWN_SPARSE_LOD_LADDERS = {"2fd6839161785e9c_ff91757c910ea7b6.lemesh"}


def test_a_sparse_lod_ladder_no_longer_deletes_the_model():
    """D13. No (package, level) may select NOTHING — including levels that lie in
    a HOLE between two rungs of the package's gated ladder.

    ⚠ THIS ASSERTION WAS FLIPPED DELIBERATELY. It was written as "every empty
    selection is explained by a ladder hole, and the package is a known one",
    which passed *because* D13 was live. The fix does not make that formulation
    fail — it just empties the list — so leaving it would have turned a defect
    report into silence. The law being asserted is now the one the fix
    establishes, and the sparse ladder is checked positively: it must still be
    sparse (the data has not changed) and must now select its finer rung.
    """
    _require(meshes(), "packages")
    empty: list[str] = []
    sparse: list[tuple[Path, dict, list[int]]] = []
    for pd, m in meshes():
        gated = _gated_levels(m["objects"])
        if gated and set(range(min(gated), max(gated) + 1)) - set(gated):
            sparse.append((pd, m, gated))
        for lv in range(0, (max(gated) if gated else 0) + 1):
            if not select_lod_objects(m["objects"], lv):
                empty.append(f"{_rel(pd)} level {lv} gated={gated}")
    assert not empty, (
        "LOD selection returned NOTHING — the whole model would import empty "
        "(D13 is back, or a new shape of it):\n  " + "\n  ".join(empty))
    for pd, m, gated in sparse:
        base = {o.get("name") for o in select_lod_objects(m["objects"], min(gated))}
        for lv in range(min(gated), max(gated) + 1):
            if lv in gated:
                continue
            got = {o.get("name") for o in select_lod_objects(m["objects"], lv)}
            assert got == base, (
                f"{_rel(pd)} level {lv} lies in a ladder HOLE and must select "
                f"exactly what the nearest FINER rung {min(gated)} selects; "
                f"got {len(got)} object(s) instead of {len(base)}")
    unknown = {pd.name for pd, _m, _g in sparse} - KNOWN_SPARSE_LOD_LADDERS
    assert not unknown, (
        "a package with a NEW sparse LOD ladder appeared; its hole levels are "
        f"handled by the snap but the ladder itself wants a look: {sorted(unknown)}")
    print(f"    [D13] {len(meshes())} package(s), 0 empty selections; "
          + ("; ".join(f"{pd.name} gated {g}" for pd, _m, g in sparse)
             or "no sparse ladder on disk"))


def _ungated_from_the_manifest(obj) -> bool:
    """Is this object in NO scene set, read straight off its draws?

    ⚠ Deliberately re-derived here instead of calling
    `object_is_scene_set_ungated`. The DEFINITION is "every draw that records a
    mask records zero"; the helper is the implementation under test. A D3 test
    that identified its subjects with the very predicate the fix introduced
    would go quiet the moment that predicate was removed.
    """
    masks = [d.get("scene_mask") for d in (obj.get("draws") or [])]
    known = [x for x in masks if x is not None]
    return bool(known) and all(int(x) == 0 for x in known)


def test_scene_set_ungated_meshes_survive_every_level():
    """D3. A mesh gated by NO scene set (`scene_mask == 0` on every draw) is
    recorded `scene_lod_level = 0`, indistinguishable from a genuine level-0
    member — so it vanished at every level >= 1. That silently deleted Jack's
    entire lower body (14,424 v) and the FP body's 48,450-v mesh, the largest in
    the asset.
    """
    _require(meshes(), "packages")
    viol: Counter = Counter()
    ex: dict[str, list[str]] = {}
    n_ungated = 0
    for pd, m in meshes():
        ungated = [o for o in m["objects"] if _ungated_from_the_manifest(o)]
        n_ungated += len(ungated)
        if not ungated:
            continue
        for lv in range(0, 6):
            keep = {o.get("name") for o in select_lod_objects(m["objects"], lv)}
            for o in ungated:
                if o.get("name") not in keep:
                    viol["ungated_mesh_dropped"] += 1
                    ex.setdefault("ungated_mesh_dropped", []).append(
                        f"{_rel(pd)} {o.get('name')} ({o.get('vertex_count')} v) "
                        f"at level {lv}")
    print(f"    [D3] {n_ungated} scene-set-ungated mesh(es) checked")
    _report(viol, ex, len(meshes()), "D3 ungated mesh dropped")


# ---------------------------------------------------------------------------
# 4. D1 — the refusal heuristic, on the rigs that motivated it
# ---------------------------------------------------------------------------

def test_scene_lod_refusal_draws_everything_on_the_real_rigs():
    """D1. `SSceneSetMask` bit N == level N is FALSE on 4 of the 12 roster
    mesh-lists, where the bits partition the body in SPACE, not in detail. On
    `64b4b5b2a0153f7e` — the PLAYER's own avatar — levels 0 and 1 are the right
    and left halves of one suit, so reading them as an LOD chain imported 7 of 14
    meshes: no left arm, no hands.

    `scene_lod_is_geometric_chain` refuses those rigs, and on refusal the caller
    must draw EVERYTHING — over-draw is visible and reversible, a missing limb is
    silent. Asserted against the shipped packages, not against copied numbers:
    the refusal must hold AND must not cost a single object. The 0.5 threshold
    must also still sit in an empty gap on real data.
    """
    _require(meshes(), "packages")
    accepted, refused = [], []
    for pd, m in meshes():
        levels = [o.get("scene_lod_level") for o in m["objects"]]
        known = [x for x in levels if x is not None]
        if not known or max(known) == 0:
            continue
        ok, diag = scene_lod_is_geometric_chain(m["objects"])
        (accepted if ok else refused).append((pd.name, diag.get("worst_score")))
        if not ok:
            kept = select_lod_objects(m["objects"], 0)
            assert len(kept) == len(m["objects"]), (
                f"{pd.name} was REFUSED as an LOD chain but selection still "
                f"dropped {len(m['objects']) - len(kept)} of {len(m['objects'])} "
                f"objects — the refusal must draw everything (D1)")
    if not (accepted or refused):
        raise SkipTest("no package on disk carries a non-zero `scene_lod_level`; "
                       "D1's refusal heuristic has nothing to run on")
    print(f"    [D1] accepted={[n for n, _ in accepted]} "
          f"refused={[n for n, _ in refused]}")
    for name, score in accepted:
        assert score is None or score >= 0.5, (name, score)
    for name, score in refused:
        assert score is None or score < 0.5, (name, score)


def test_the_player_avatar_keeps_both_arms_and_its_hands():
    """The named regression: `64b4b5b2a0153f7e` is the player's own avatar and it
    must import ALL of its meshes at the default LOD 0.

    D1 measured the defect as "7 of 14 meshes; no left arm, no hands",
    with the level-0 union AABB in x=[+0.05,+0.63] and level-1 in [-0.63,-0.05],
    intersection volume exactly 0. Asserted on the real package: every object
    survives, and the selected set spans BOTH sides of x = 0.
    """
    pkg = CHARS / "c6bc8607972268c9_64b4b5b2a0153f7e.lemesh"
    if not (pkg / "manifest.json").is_file():
        raise SkipTest(f"{pkg.name} is not extracted in this checkout — the "
                       f"player-avatar regression cannot be checked")
    objs = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))["objects"]
    kept = select_lod_objects(objs, 0)
    assert len(kept) == len(objs), (
        f"LOD 0 dropped {len(objs) - len(kept)} of {len(objs)} meshes on the "
        f"PLAYER's avatar: "
        f"{sorted({o['name'] for o in objs} - {o['name'] for o in kept})}")
    lo = min(float(o["aabb_min"][0]) for o in kept)
    hi = max(float(o["aabb_max"][0]) for o in kept)
    assert lo < -0.3 and hi > 0.3, (
        f"the selected set spans x=[{lo:.3f},{hi:.3f}] — one whole side of the "
        f"body is missing (D1)")


# ---------------------------------------------------------------------------
# 5. the lightmap UV slot  (D7)
# ---------------------------------------------------------------------------

def test_every_object_names_the_slot4_uv_set_it_actually_carries():
    """`lightmap_uv` is the RESOLVED name of the slot-4 texcoord set. An object
    that names one must carry that attribute; an object carrying a slot-4
    texcoord must name it. Substituting another UV set is the D7 defect that
    `lightmap_uv_for_manifest_object` exists to prevent — the literal `"uv1"` is
    only right when the texcoord slots happen to be `(0, 4)`.

    Also asserts the manifest's stored `lightmap_uv` agrees with what
    `lightmap_uv_attr_name` re-derives from the object's own element table, so a
    package on disk and a freshly written one resolve identically.
    """
    _require(meshes(), "packages")
    viol: Counter = Counter()
    ex: dict[str, list[str]] = {}
    names: Counter = Counter()
    named = 0
    for pd, m in meshes():
        for o in m["objects"]:
            raw = o.get("raw_vertex_format") or []
            has_slot4 = any(int(e.get("usage", -1)) == 4 and int(e.get("slot", -1)) == 4
                            for e in raw)
            got = lightmap_uv_for_manifest_object(o)
            want = lightmap_uv_attr_name(raw)
            if got != want:
                viol["stored_vs_derived"] += 1
                ex.setdefault("stored_vs_derived", []).append(
                    f"{pd.name} {o.get('name')} {got!r} vs {want!r}")
            if has_slot4 and not got:
                viol["slot4_present_but_unnamed"] += 1
                ex.setdefault("slot4_present_but_unnamed", []).append(
                    f"{pd.name} {o.get('name')}")
            if got and not has_slot4:
                viol["named_without_slot4"] += 1
                ex.setdefault("named_without_slot4", []).append(
                    f"{pd.name} {o.get('name')} -> {got}")
            if got:
                named += 1
                names[got] += 1
                if got not in (o.get("attributes") or {}):
                    viol["named_attribute_absent"] += 1
                    ex.setdefault("named_attribute_absent", []).append(
                        f"{_rel(pd)} {o.get('name')} -> {got}")
    print(f"    [lightmap uv] {named} object(s) name a slot-4 texcoord set: "
          + ", ".join(f"{k}={v}" for k, v in sorted(names.items())))
    _report(viol, ex, len(meshes()), "lightmap UV slot")
    # ★ D7, refuted from the data rather than from the docstring. The addon used
    # to hardcode the literal "uv1" for the bake. The corpus contains objects
    # whose slot-4 texcoord is NOT the second UV set — texcoord slots (0,1,4)
    # give "uv2", and a third arrangement gives "uv3" — so the literal is wrong
    # on those, and no amount of unit testing a synthetic (0,4) mesh can show it.
    # Measured: uv1=861, uv2=64, uv3=29.
    if sum(v for k, v in names.items() if k != "uv1") == 0:
        from unittest import SkipTest
        raise SkipTest(
            "no object in this checkout's packages carries a slot-4 texcoord "
            "outside `uv1`, so this run cannot refute the hardcoded literal. "
            "Extract a level whose objects use texcoord slots (0, 1, 4) to "
            "enable it. ⛔ WHILE THIS SKIP IS ACTIVE THE D7 COUNTEREXAMPLE IS "
            "NOT EXERCISED ON REAL DATA.")
