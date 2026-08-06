"""E2 — archive-free tests for the PER-INSTANCE lightmap on the `.lescatter` path.

The settled facts these lock (all from docs/LIGHTING.md §8,
`stream-confirmed` / `export-validated` — none of them re-derived here):

  * per-instance per-vertex lightmap UVs exist on disk, one record per instance;
  * ⛔ instances of the SAME mesh carry DIFFERENT UVs, so a lightmapped static
    instance cannot ride a shared Blender mesh datablock;
  * ⛔ 1046/1050 `.lescatter` `uv1` blobs are entirely ZERO — `uv1` is NOT the
    level lightmap UV set on this path;
  * ⛔ the per-INSTANCE page and the per-MESH `lm_slice_index` disagree for
    65.1 % of station_front's instances; the instance wins.

⚠ EVERY `instance_lightmap` blob written by this module is **SYNTHETIC**. It is
shaped by the pinned v5 contract, so it exercises the parse/plumbing exactly, but
no assertion here is evidence about the real bake. The real package is E1's.

`bpy` / `mathutils` are stubbed (shared with `test_scatter_import`), the REAL
`scatter_reader` + `lightmap_builder` are used, and `material_builder` is the
recorder — so "the instance's page reached the wiring" is an assertion about the
value that crossed the boundary, not about a re-implementation of it.

Runs under `python3 blender_tool/tests/run_tests.py` and unchanged under pytest.
"""

from __future__ import annotations

import json
import struct
import sys
from array import array
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ADDON = _HERE.parent / "addon" / "lone_echo_import"
for _p in (str(_ADDON), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import scatter_reader                              # noqa: E402  (the REAL reader)
import make_synthetic_scatter                      # noqa: E402
from test_scatter_import import (                  # noqa: E402
    _scatter_import, _fresh_context,
)

EXPORTS = _HERE.parent / "exports"
REAL_V4_PKG = EXPORTS / "942c829457a04a62.lescatter"
#: E1's REAL v5 station_front export. Optional — every test that touches it
#: raises `unittest.SkipTest` with a reason when it is absent, so this module
#: never depends on an artefact it does not own.
REAL_V5_PKG = EXPORTS / "942c829457a04a62_instlm.lescatter"

PACKAGE_VERSION_INSTANCE_LM = 5


# =============================================================================
# SYNTHETIC v5 fixture writers  (⚠ synthetic — see the module docstring)
# =============================================================================

def write_instance_lightmap(pkg_dir, uv_of, page_of, *, flip_v_applied=False,
                            present=True, reason="", version=PACKAGE_VERSION_INSTANCE_LM,
                            count=None, truncate_uv=0):
    """Add the pinned v5 `instance_lightmap` section to an existing package.

    `uv_of(i, mesh_entry) -> [u, v, ...]` (RAW, unflipped) and
    `page_of(i, mesh_entry) -> int` are called per GLOBAL instance index, so a
    test can make instances of the same mesh disagree — which is the whole point
    of the stream.

    `present=False` writes the "declared but not extracted" shape, which the
    contract keeps DISTINCT from a pre-v5 package that has no section at all.
    `truncate_uv` chops N floats off the end of the UV blob to exercise the
    short-blob path.
    """
    pkg_dir = Path(pkg_dir)
    manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = max(int(manifest.get("version", 1)), int(version))

    if not present:
        manifest["instance_lightmap"] = {"present": False,
                                         "reason": reason or "not extracted"}
        (pkg_dir / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                               encoding="utf-8")
        return manifest["instance_lightmap"]

    by_index = {m["index"]: m for m in manifest["meshes"]}
    data = (pkg_dir / manifest["instances_blob"]).read_bytes()
    n = int(manifest["num_instances"])

    uv_flat, offsets, counts, pages = [], [], [], []
    cursor = 0
    for i in range(n):
        mesh_index = struct.unpack_from("<I", data, i * 44)[0]
        entry = by_index[mesh_index]
        uv = list(uv_of(i, entry))
        offsets.append(cursor)
        counts.append(len(uv) // 2)
        pages.append(int(page_of(i, entry)))
        uv_flat.extend(uv)
        cursor += len(uv) // 2

    blobs = pkg_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)
    if truncate_uv:
        uv_flat = uv_flat[:-truncate_uv]
    array("f", uv_flat).tofile(open(blobs / "instance_lm_uv.bin", "wb"))
    array("I", offsets).tofile(open(blobs / "instance_lm_uvoff.bin", "wb"))
    array("I", counts).tofile(open(blobs / "instance_lm_count.bin", "wb"))
    array("I", pages).tofile(open(blobs / "instance_lm_page.bin", "wb"))

    section = {
        "present": True,
        "count": n if count is None else int(count),
        "uv_blob": "blobs/instance_lm_uv.bin",
        "offsets_blob": "blobs/instance_lm_uvoff.bin",
        "counts_blob": "blobs/instance_lm_count.bin",
        "page_blob": "blobs/instance_lm_page.bin",
        "total_uv_pairs": cursor,
        "flip_v_applied": bool(flip_v_applied),
        "synthetic": True,          # ⚠ never let a fixture masquerade as extractor output
    }
    manifest["instance_lightmap"] = section
    (pkg_dir / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                           encoding="utf-8")
    return section


def _marching_uv(i, entry):
    """A per-instance UV set that is DISTINCT for every instance of a mesh.

    Mirrors the shipped shape (findings 8.2): `v` held constant across the
    instance's vertices, `u` marching per instance, so `uv(i) != uv(j)` and a
    shared-datablock import is detectably wrong.
    """
    n = int(entry["nverts"])
    u0 = 0.01 + (i % 50) * 0.017
    v0 = 0.02 + (i % 7) * 0.031
    return [c for k in range(n)
            for c in (u0 + k * 0.001, v0 + (k % 3) * 0.002)]


def _page_cycle(i, entry):
    """13 real pages, deterministic, and deliberately NOT the mesh's own field."""
    return (i * 7 + int(entry["index"])) % 13


def _fake_dx10_dds(path, dxgi=95, width=1024, height=1024, arraysize=65):
    """A 148-byte DX10 DDS HEADER only — enough for `dds_dx10_header`.

    `wire_lightmap` is never reached in these tests (the recorder stands in for
    `material_builder`), so no pixel bytes are needed and none are invented.
    """
    hdr = bytearray(148)
    hdr[0:4] = b"DDS "
    struct.pack_into("<I", hdr, 4, 124)
    struct.pack_into("<2I", hdr, 12, height, width)
    struct.pack_into("<I", hdr, 28, 1)                 # mips
    hdr[84:88] = b"DX10"
    struct.pack_into("<I", hdr, 128, dxgi)
    struct.pack_into("<I", hdr, 128 + 12, arraysize)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(bytes(hdr))
    return str(path)


def _atlas_opts(tmp_path):
    """Options pointing at a header-only stand-in atlas (13 pages x 5 lobes)."""
    d = Path(tmp_path) / "lm"
    _fake_dx10_dds(d / "0178fa39b1b95d2f.dds", dxgi=95, arraysize=65)
    _fake_dx10_dds(d / "81a8fcf99b655a42.dds", dxgi=83, arraysize=13)
    return {"lightmap_dir": str(d)}


def _build(tmp_path, *, uv_of=_marching_uv, page_of=_page_cycle, mesh_patch=None,
           **section_kw):
    """Write a synthetic package, patch its mesh entries, add the v5 section."""
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    if mesh_patch:
        manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))
        for m in manifest["meshes"]:
            m.update(mesh_patch(m) or {})
        (pkg_dir / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                               encoding="utf-8")
    write_instance_lightmap(pkg_dir, uv_of, page_of, **section_kw)
    return pkg_dir


def _import(pkg_dir, **opts):
    si, mb = _scatter_import()
    mb.calls.clear()
    mb.lm_calls.clear()
    base = {"flip_v": True, "y_up_to_z_up": True, "import_proxy": False,
            "lod_level": -1}
    base.update(opts)
    summary = si.import_lescatter(pkg_dir, _fresh_context(), base)
    return si, mb, summary


def _objects(si, summary):
    import bpy   # type: ignore
    coll = bpy.data.collections.get(summary["collection"])
    return {o["le_instance_index"]: o for o in coll.objects}


# =============================================================================
# T1 — the reader
# =============================================================================

def test_v5_section_parses_uv_page_and_count(tmp_path):
    pkg_dir = _build(tmp_path)
    pkg = scatter_reader.ScatterPackage(pkg_dir)
    ilm = pkg.instance_lightmap
    assert ilm.present is True
    assert ilm.declared is True
    assert ilm.count == pkg.num_instances == 8
    assert ilm.flip_v_applied is False
    entries = {m["index"]: m for m in pkg.meshes}
    recs = scatter_reader.read_instances(pkg)
    for rec in recs:
        entry = entries[rec.mesh_index]
        want = _marching_uv(rec.index, entry)
        got = list(ilm.uv(rec.index))
        assert len(got) == len(want) == entry["nverts"] * 2
        assert max(abs(a - b) for a, b in zip(got, want)) < 1e-6
        assert ilm.page(rec.index) == _page_cycle(rec.index, entry)
        assert ilm.vertex_count(rec.index) == entry["nverts"]


def test_package_level_accessors_delegate(tmp_path):
    pkg = scatter_reader.ScatterPackage(_build(tmp_path))
    assert list(pkg.instance_lightmap_uv(3)) == list(pkg.instance_lightmap.uv(3))
    assert pkg.instance_lightmap_page(3) == pkg.instance_lightmap.page(3)
    # the accessor object is cached, so the 54 MB station_front blob loads once
    assert pkg.instance_lightmap is pkg.instance_lightmap


def test_instances_of_the_same_mesh_get_different_uvs(tmp_path):
    """The fact the whole design turns on (findings 8.2), asserted on the stream."""
    pkg = scatter_reader.ScatterPackage(_build(tmp_path))
    recs = scatter_reader.read_instances(pkg)
    same_mesh = [r.index for r in recs if r.mesh_index == 0]
    assert len(same_mesh) >= 2
    a, b = (list(pkg.instance_lightmap.uv(i)) for i in same_mesh[:2])
    assert a != b


def test_v4_package_without_the_section_is_unchanged(tmp_path):
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    ilm = scatter_reader.ScatterPackage(pkg_dir).instance_lightmap
    assert ilm.present is False
    assert ilm.declared is False
    assert "pre-v5" in ilm.reason
    assert ilm.uv(0) is None and ilm.page(0) is None
    assert ilm.vertex_count(0) is None
    assert ilm.pages_histogram() == {}


def test_declared_but_absent_is_distinct_from_never_declared(tmp_path):
    """`{"present": false, "reason": ...}` is a bug report; a missing section is
    an old package. The contract keeps them apart, so the reader must too."""
    pkg_dir = _build(tmp_path, present=False, reason="instancedata not decompressed")
    ilm = scatter_reader.ScatterPackage(pkg_dir).instance_lightmap
    assert ilm.present is False
    assert ilm.declared is True
    assert ilm.reason == "instancedata not decompressed"


def test_the_shipped_v4_package_still_reports_no_stream():
    """Back-compat locked on a REAL export, not only on a fixture."""
    from unittest import SkipTest
    if not (REAL_V4_PKG / "manifest.json").is_file():
        raise SkipTest(
            f"{REAL_V4_PKG.name} is not extracted in this checkout — the v4 "
            "back-compat path has nothing to run on. Re-extract with "
            "`python.exe scripts/le_scene_extract.py <hash>` to enable it.")
    pkg = scatter_reader.ScatterPackage(REAL_V4_PKG)
    if pkg.manifest["version"] != 4:
        raise SkipTest(
            f"{REAL_V4_PKG.name} is package version {pkg.manifest['version']}, "
            "not 4 — this test needs a v4-era export to prove back-compat. "
            "⛔ WHILE THIS SKIP IS ACTIVE THE v4 READER PATH IS UNTESTED.")
    ilm = pkg.instance_lightmap
    assert ilm.present is False and ilm.declared is False
    assert pkg.instance_lightmap_uv(0) is None
    assert pkg.instance_lightmap_page(0) is None


def test_the_real_v5_export_parses_and_matches_the_findings():
    """★ THE CONTRACT MEETING A REAL EXPORT — the one test here that is not a
    fixture. Locks the reader against the shipped stream, and re-checks the two
    numbers docs/LIGHTING.md §8 states independently of it."""
    from unittest import SkipTest
    if not (REAL_V5_PKG / "manifest.json").is_file():
        raise SkipTest(
            f"{REAL_V5_PKG.name} is not extracted in this checkout. Re-extract "
            "with `--instance-lightmap` to enable it. ⛔ WHILE THIS SKIP IS "
            "ACTIVE NO REAL v5 STREAM IS PARSED.")
    pkg = scatter_reader.ScatterPackage(REAL_V5_PKG)
    ilm = pkg.instance_lightmap
    if not ilm.present:
        raise SkipTest(
            f"{REAL_V5_PKG.name} carries no instance-lightmap stream — "
            "re-extract with `--instance-lightmap`.")
    assert ilm.present is True
    assert ilm.count == pkg.num_instances == 21394
    assert ilm.flip_v_applied is False      # RAW — flipping is the consumer's job

    # §8.4's page histogram, recomputed from the blob by this reader.
    assert ilm.pages_histogram() == {
        0: 1676, 1: 2838, 2: 2629, 3: 1901, 4: 938, 5: 589, 6: 587,
        7: 3081, 8: 847, 9: 1726, 10: 1772, 11: 2326, 12: 484}

    entries = {m["index"]: m for m in pkg.meshes}
    recs = scatter_reader.read_instances(pkg)
    # §8.1: every record carries exactly its mesh's nverts UV pairs.
    for rec in recs:
        assert ilm.vertex_count(rec.index) == entries[rec.mesh_index]["nverts"]

    # §8.2: instances of the SAME mesh carry DIFFERENT UVs — the fact that
    # forces one datablock per instance. Checked on the mesh with the most.
    biggest = max(entries.values(), key=lambda m: m["instance_count"])
    same = [r.index for r in recs if r.mesh_index == biggest["index"]][:3]
    uvs = [list(ilm.uv(i)) for i in same]
    assert uvs[0] != uvs[1] != uvs[2]

    # §8.4: the instance page disagrees with the per-mesh lm_slice_index for
    # 65.1 % of instances. This is what makes the instance field load-bearing.
    disagree = sum(
        1 for r in recs
        if ilm.page(r.index) != scatter_reader.ScatterPackage.lightmap_ids(
            entries[r.mesh_index])[1])
    assert 0.60 < disagree / len(recs) < 0.70, disagree


def test_missing_blob_downgrades_instead_of_raising(tmp_path):
    pkg_dir = _build(tmp_path)
    (Path(pkg_dir) / "blobs" / "instance_lm_uv.bin").unlink()
    ilm = scatter_reader.ScatterPackage(pkg_dir).instance_lightmap
    assert ilm.uv(0) is None                     # forces the lazy load
    assert ilm.present is False
    assert "unreadable" in ilm.reason


def test_short_uv_blob_yields_none_for_the_truncated_records(tmp_path):
    """A record whose slice runs off the end returns None — NEVER zeros. A
    zero-filled UV set samples atlas texel (0,0) and looks like a valid answer."""
    pkg_dir = _build(tmp_path, truncate_uv=8)
    ilm = scatter_reader.ScatterPackage(pkg_dir).instance_lightmap
    assert ilm.uv(0) is not None
    assert ilm.uv(7) is None


def test_page_sentinel_is_none_never_zero(tmp_path):
    """⛔ page 0 is a real page (1,676 station_front instances use it), so the
    'no page' sentinel must not collapse onto it."""
    pkg_dir = _build(tmp_path, page_of=lambda i, e: 0xFFFFFFFF if i == 2 else 4)
    ilm = scatter_reader.ScatterPackage(pkg_dir).instance_lightmap
    assert ilm.page(2) is None
    assert ilm.page(1) == 4


def test_out_of_range_index_is_none(tmp_path):
    ilm = scatter_reader.ScatterPackage(_build(tmp_path)).instance_lightmap
    assert ilm.uv(-1) is None and ilm.page(-1) is None
    assert ilm.uv(999) is None and ilm.page(999) is None


def test_pages_histogram_counts_the_stream(tmp_path):
    pkg = scatter_reader.ScatterPackage(_build(tmp_path, page_of=lambda i, e: i % 3))
    assert pkg.instance_lightmap.pages_histogram() == {0: 3, 1: 3, 2: 2}


# =============================================================================
# T2 — the importer: DEFAULT OFF
# =============================================================================

def test_default_off_is_byte_identical_to_today(tmp_path):
    """A v5 package imported with the option OFF must produce exactly what a v4
    package produces: shared datablocks, no copies, no variants, no atlas read."""
    pkg_dir = _build(tmp_path)
    si, mb, summary = _import(pkg_dir)
    assert summary["instance_lightmap"]["enabled"] is False
    assert "off (default)" in summary["instance_lightmap"]["reason"]
    assert mb.lm_calls == []
    objs = _objects(si, summary)
    # every instance of a mesh shares ONE datablock — the pre-existing contract
    by_mesh = {}
    for i, ob in objs.items():
        by_mesh.setdefault(ob["le_mesh_index"], set()).add(id(ob.data))
    assert all(len(v) == 1 for v in by_mesh.values())
    assert all("le_lightmap_page" not in ob.keys() for ob in objs.values())


def test_option_on_without_a_stream_places_exactly_as_off(tmp_path):
    """A v4 package + `instance_lightmap=True` must not degrade: it reports the
    reason and shares datablocks, it does not invent UVs."""
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, **_atlas_opts(tmp_path))
    lm = summary["instance_lightmap"]
    assert lm["enabled"] is True
    assert lm["stream_present"] is False
    assert "pre-v5" in lm["stream_reason"]
    assert lm["instances_wired"] == 0
    assert lm["datablocks_created"] == 0
    assert mb.lm_calls == []
    objs = _objects(si, summary)
    assert all("le_lightmap_page" not in ob.keys() for ob in objs.values())


# =============================================================================
# T2 — the importer: per-instance mode
# =============================================================================

def test_per_instance_uvs_land_on_the_right_object(tmp_path):
    pkg_dir = _build(tmp_path)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, **_atlas_opts(tmp_path))
    lm = summary["instance_lightmap"]
    assert lm["instances_wired"] == 8
    assert lm["datablocks_created"] == 8          # every instance owns a datablock
    assert lm["datablocks_shared"] == 0
    assert lm["uv_layer"] == si.INSTANCE_LM_UV_LAYER

    pkg = scatter_reader.ScatterPackage(pkg_dir)
    entries = {m["index"]: m for m in pkg.meshes}
    objs = _objects(si, summary)
    for rec in scatter_reader.read_instances(pkg):
        ob = objs[rec.index]
        layer = ob.data.uv_layers.get(si.INSTANCE_LM_UV_LAYER)
        assert layer is not None, rec.index
        want = _marching_uv(rec.index, entries[rec.mesh_index])
        # per LOOP, so read back through the loop->vertex map
        loops = [l.vertex_index for l in ob.data.loops]
        for li, vi in enumerate(loops):
            assert abs(layer.uv[li * 2] - want[vi * 2]) < 1e-6
            assert abs(layer.uv[li * 2 + 1] - (1.0 - want[vi * 2 + 1])) < 1e-6


def test_each_instance_gets_its_own_datablock(tmp_path):
    """⛔ The cost this option exists to make explicit: instancing does not
    survive a per-instance bake."""
    pkg_dir = _build(tmp_path)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, **_atlas_opts(tmp_path))
    objs = _objects(si, summary)
    assert len({id(ob.data) for ob in objs.values()}) == len(objs) == 8
    assert summary["instance_lightmap"]["base_datablocks"] == 3
    assert summary["instance_lightmap"]["datablocks_total"] == 3 + 8


def test_identical_uv_sets_are_shared_not_recopied(tmp_path):
    """The dedup is a MEASUREMENT of how much instancing survives. On the real
    stream it is expected to hit ~never (findings 8.2)."""
    pkg_dir = _build(tmp_path, uv_of=lambda i, e: [0.25, 0.5] * int(e["nverts"]),
                     page_of=lambda i, e: 3)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, **_atlas_opts(tmp_path))
    lm = summary["instance_lightmap"]
    assert lm["instances_wired"] == 8
    assert lm["datablocks_created"] == 3           # one per MESH, all UVs equal
    assert lm["datablocks_shared"] == 5


def test_dedup_can_be_turned_off(tmp_path):
    pkg_dir = _build(tmp_path, uv_of=lambda i, e: [0.25, 0.5] * int(e["nverts"]),
                     page_of=lambda i, e: 3)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True,
                              instance_lightmap_dedup=False, **_atlas_opts(tmp_path))
    assert summary["instance_lightmap"]["datablocks_created"] == 8
    assert summary["instance_lightmap"]["datablocks_shared"] == 0


# =============================================================================
# T2 — the PAGE comes from the instance, not the mesh
# =============================================================================

def test_page_comes_from_the_instance_not_the_mesh(tmp_path):
    """⛔ 65.1 % of station_front's instances disagree with their mesh's
    `lm_slice_index` (findings 8.4). For an instanced draw the instance wins."""
    pkg_dir = _build(tmp_path,
                     page_of=lambda i, e: 11,
                     mesh_patch=lambda m: {"lightmap_index": 1,
                                           "lm_slice_index": 2, "numlobes": 4})
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, **_atlas_opts(tmp_path))
    assert summary["instance_lightmap"]["pages"] == {11: 8}
    objs = _objects(si, summary)
    assert {ob["le_lightmap_page"] for ob in objs.values()} == {11}
    # ... and the value that actually crossed into the wiring is 11, not 2.
    assert mb.lm_calls, "no material variant was requested"
    assert {c["spec"]["slice_index"] for c in mb.lm_calls} == {11}
    assert {c["spec"]["page_index"] for c in mb.lm_calls} == {11}


def test_multiple_pages_yield_per_material_page_variants(tmp_path):
    """D3's per-(material, page) cache is reused verbatim — one variant per
    (material, page) ACTUALLY USED, never per instance."""
    pkg_dir = _build(tmp_path, page_of=lambda i, e: 3 if i < 4 else 6)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, **_atlas_opts(tmp_path))
    lm = summary["instance_lightmap"]
    assert lm["pages"] == {3: 4, 6: 4}
    # 3 meshes -> 3 placeholder materials; 2 pages in use; but only the
    # (material, page) pairs a mesh actually names are materialised.
    assert 0 < lm["material_variants"] <= 3 * 2
    assert lm["variant_uv_layer_conflicts"] == 0


def test_an_unlit_mesh_is_skipped_and_keeps_the_shared_datablock(tmp_path):
    """`lightmapindex == 0xffffffff` — 5 of station_front's 1050 meshes."""
    pkg_dir = _build(tmp_path,
                     mesh_patch=lambda m: ({"lightmap_index": 0xFFFFFFFF}
                                           if m["index"] == 1 else {}))
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, **_atlas_opts(tmp_path))
    lm = summary["instance_lightmap"]
    assert lm["skipped_unlit_mesh"] == 3          # mesh 1 has 3 instances
    assert lm["instances_wired"] == 5
    objs = _objects(si, summary)
    unlit = [ob for ob in objs.values() if ob["le_mesh_index"] == 1]
    assert len({id(ob.data) for ob in unlit}) == 1          # still shared
    assert all("le_lightmap_page" not in ob.keys() for ob in unlit)


def test_an_instance_without_a_page_is_skipped(tmp_path):
    pkg_dir = _build(tmp_path, page_of=lambda i, e: 0xFFFFFFFF if i in (0, 5) else 4)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, **_atlas_opts(tmp_path))
    lm = summary["instance_lightmap"]
    assert lm["skipped_no_page"] == 2
    assert lm["instances_wired"] == 6
    objs = _objects(si, summary)
    assert "le_lightmap_page" not in objs[0].keys()


def test_a_vertex_count_mismatch_is_refused_not_guessed(tmp_path):
    """`stride == 44 + 8*nverts` (findings 8.1). If the record and the mesh
    disagree, guessing which is right misplaces the whole chart."""
    pkg_dir = _build(tmp_path,
                     uv_of=lambda i, e: (_marching_uv(i, e)[:-2] if i == 4
                                         else _marching_uv(i, e)))
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, **_atlas_opts(tmp_path))
    lm = summary["instance_lightmap"]
    assert lm["skipped_vertex_count_mismatch"] == 1
    assert lm["instances_wired"] == 7


# =============================================================================
# T2 — flip_v, applied ONCE and only once
# =============================================================================

def _lm_v(si, summary, instance):
    ob = _objects(si, summary)[instance]
    layer = ob.data.uv_layers.get(si.INSTANCE_LM_UV_LAYER)
    loops = [l.vertex_index for l in ob.data.loops]
    return [(loops[li], layer.uv[li * 2 + 1]) for li in range(len(loops))]


def test_flip_v_is_applied_when_the_stream_is_raw(tmp_path):
    pkg_dir = _build(tmp_path, flip_v_applied=False)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, **_atlas_opts(tmp_path))
    assert summary["instance_lightmap"]["flip_v_applied_by_importer"] is True
    pkg = scatter_reader.ScatterPackage(pkg_dir)
    raw = list(pkg.instance_lightmap.uv(0))
    for vi, v in _lm_v(si, summary, 0):
        assert abs(v - (1.0 - raw[vi * 2 + 1])) < 1e-6


def test_flip_v_is_NOT_applied_twice_when_the_stream_says_it_already_was(tmp_path):
    """⛔ The double-flip bug: `1 - (1 - v)` lands on a different atlas strip and
    still looks like a plausible lightmap."""
    pkg_dir = _build(tmp_path, flip_v_applied=True)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, **_atlas_opts(tmp_path))
    assert summary["instance_lightmap"]["stream_flip_v_applied"] is True
    assert summary["instance_lightmap"]["flip_v_applied_by_importer"] is False
    pkg = scatter_reader.ScatterPackage(pkg_dir)
    raw = list(pkg.instance_lightmap.uv(0))
    for vi, v in _lm_v(si, summary, 0):
        assert abs(v - raw[vi * 2 + 1]) < 1e-6


def test_flip_v_off_is_honoured(tmp_path):
    pkg_dir = _build(tmp_path)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, flip_v=False,
                              **_atlas_opts(tmp_path))
    assert summary["instance_lightmap"]["flip_v_applied_by_importer"] is False
    pkg = scatter_reader.ScatterPackage(pkg_dir)
    raw = list(pkg.instance_lightmap.uv(0))
    for vi, v in _lm_v(si, summary, 0):
        assert abs(v - raw[vi * 2 + 1]) < 1e-6


# =============================================================================
# T4 — ⛔ THE GUARD: the scatter path never wires `uv1` to a lightmap
# =============================================================================

def test_the_scatter_path_never_wires_uv1_to_a_lightmap(tmp_path):
    """1046/1050 shipped `uv1` blobs are entirely ZERO (findings 8.3), so wiring
    them samples atlas texel (0,0) for 99.6 % of the level. No default path may
    reach that layer."""
    pkg_dir = _build(tmp_path)
    for extra in ({}, {"instance_lightmap": True}):
        opts = dict(extra)
        opts.update(_atlas_opts(tmp_path))
        si, mb, summary = _import(pkg_dir, **opts)
        for call in mb.lm_calls:
            assert call["spec"].get("uv_layer") != "uv1", call
        lm = summary["instance_lightmap"]
        assert lm.get("uv_layer", "") != "uv1"


def test_the_uv1_failure_mode_requires_an_explicit_diagnostic_key(tmp_path):
    """It is renderable on demand — and ONLY on demand. The operator has no
    property for it, so it cannot be reached from the UI."""
    si, _mb = _scatter_import()
    assert not hasattr(si.IMPORT_OT_lescatter, "instance_lightmap_uv_source")
    src = (_ADDON / "scatter_import.py").read_text(encoding="utf-8")
    assert '"instance_lightmap_uv_source"' not in src.split("def execute", 1)[1]


def test_the_uv1_diagnostic_renders_the_documented_failure_mode(tmp_path):
    """Wired to `uv1` it takes the per-MESH page and copies NO datablock — the
    two things that make the naive path both cheap and wrong."""
    pkg_dir = _build(tmp_path, page_of=lambda i, e: 11,
                     mesh_patch=lambda m: {"lightmap_index": 1, "lm_slice_index": 2})
    # give every mesh a uv1 blob so the naive path has something to sample
    manifest = json.loads((Path(pkg_dir) / "manifest.json").read_text(encoding="utf-8"))
    for m in manifest["meshes"]:
        rel = f"blobs/m{m['index']}_uv1.bin"
        array("f", [0.0] * (m["nverts"] * 2)).tofile(
            open(Path(pkg_dir) / rel, "wb"))
        m["uv1"] = rel
    (Path(pkg_dir) / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                                 encoding="utf-8")
    si, mb, summary = _import(pkg_dir, instance_lightmap=True,
                              instance_lightmap_uv_source="uv1",
                              **_atlas_opts(tmp_path))
    lm = summary["instance_lightmap"]
    assert lm["uv_source"] == "uv1"
    assert lm["uv_layer"] == "uv1"
    assert lm["meshes_wired_shared"] == 3
    assert lm["datablocks_created"] == 0          # nothing copied: cheap AND wrong
    assert lm["pages"] == {2: 3}                  # the MESH's page, not the 11s
    assert {c["spec"]["uv_layer"] for c in mb.lm_calls} == {"uv1"}


def test_an_unknown_uv_source_falls_back_to_the_correct_one(tmp_path):
    pkg_dir = _build(tmp_path)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True,
                              instance_lightmap_uv_source="nonsense",
                              **_atlas_opts(tmp_path))
    assert summary["instance_lightmap"]["uv_source"] == "instance"


# =============================================================================
# atlas resolution / summary shape
# =============================================================================

def test_uvs_are_imported_even_when_the_atlas_is_missing(tmp_path):
    """The option means 'honour the per-instance UVs'. Wiring is a separate,
    reported concern — a missing atlas must not silently drop the data."""
    pkg_dir = _build(tmp_path)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True)
    lm = summary["instance_lightmap"]
    assert lm["atlas_available"] is False
    assert "no lightmap atlas found" in lm["atlas_reason"]
    assert lm["instances_wired"] == 8
    assert lm["material_variants"] == 0
    assert mb.lm_calls == []
    objs = _objects(si, summary)
    assert objs[0].data.uv_layers.get(si.INSTANCE_LM_UV_LAYER) is not None


def test_summary_reports_created_versus_shared(tmp_path):
    """The brief's requirement: the memory cost must be in the summary."""
    pkg_dir = _build(tmp_path)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, **_atlas_opts(tmp_path))
    lm = summary["instance_lightmap"]
    for key in ("datablocks_created", "datablocks_shared", "base_datablocks",
                "datablocks_total", "instances_sharing_base", "instances_wired",
                "material_variants", "pages", "atlas_available", "stream_present"):
        assert key in lm, key
    assert lm["instances_sharing_base"] == summary["instances_placed"] - lm["instances_wired"]


def test_max_instances_still_bounds_the_copies(tmp_path):
    """A bounded preview must be bounded in MEMORY too, not only in objects."""
    pkg_dir = _build(tmp_path)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, max_instances=3,
                              **_atlas_opts(tmp_path))
    assert summary["instances_placed"] == 3
    assert summary["instance_lightmap"]["datablocks_created"] == 3


def test_lod_level_still_bounds_the_copies(tmp_path):
    pkg_dir = _build(tmp_path)
    si, mb, summary = _import(pkg_dir, instance_lightmap=True, lod_level=0,
                              **_atlas_opts(tmp_path))
    assert (summary["instance_lightmap"]["datablocks_created"]
            <= summary["instances_placed"])


# =============================================================================
# the affine-fit measurement helper (open question, not a claim)
# =============================================================================

def test_uv_affine_fit_error_detects_an_affine_pair(tmp_path):
    """If instance B's chart were instance A's chart scaled+translated, one
    per-object attribute could replace the datablock copy. This measures it;
    it does not assert anything about the real stream."""
    def uv_of(i, e):
        base = [c for k in range(int(e["nverts"]))
                for c in (0.1 + k * 0.01, 0.2 + (k % 4) * 0.02)]
        if i % 2 == 0:
            return base
        return [(base[j] * 0.5 + 0.3) if j % 2 == 0 else (base[j] * 0.5 + 0.4)
                for j in range(len(base))]
    pkg_dir = _build(tmp_path, uv_of=uv_of, page_of=lambda i, e: 3)
    si, _mb = _scatter_import()
    pkg = scatter_reader.ScatterPackage(pkg_dir)
    err = si.uv_affine_fit_error(pkg, 0, 0, 1)
    assert err is not None and err < 1e-5


def test_uv_affine_fit_error_rejects_a_nonaffine_pair(tmp_path):
    def uv_of(i, e):
        n = int(e["nverts"])
        if i % 2 == 0:
            return [c for k in range(n) for c in (0.1 + k * 0.01, 0.2)]
        return [c for k in range(n) for c in (0.1 + (k * k) * 0.004, 0.2)]
    pkg_dir = _build(tmp_path, uv_of=uv_of, page_of=lambda i, e: 3)
    si, _mb = _scatter_import()
    pkg = scatter_reader.ScatterPackage(pkg_dir)
    err = si.uv_affine_fit_error(pkg, 0, 0, 1)
    assert err is not None and err > 1e-3


def test_the_shared_mesh_affine_alternative_is_REFUTED_on_the_real_stream():
    """★ CLOSES the standing design alternative.

    If instance B's lightmap chart were instance A's chart rigidly scaled and
    translated into another atlas rect, one shared mesh datablock plus four
    per-object floats (`Attribute(OBJECT)` -> `Mapping`) would be both CORRECT
    and O(1) in memory, and the 21k datablock copies would be unnecessary.

    Measured over 2,320 same-mesh instance pairs of E1's real v5 export, with
    mirrored charts admitted so a flip cannot be mistaken for a refutation: the
    MEDIAN residual is ~0.24 in UV space — about 242 texels on a 1024^2 page —
    and only ~13 % of pairs are affine-related at all. The alternative is
    refuted; per-instance mesh data is required. `export-validated`.
    """
    from unittest import SkipTest
    if not (REAL_V5_PKG / "manifest.json").is_file():
        raise SkipTest(
            f"{REAL_V5_PKG.name} is not extracted in this checkout — the 2,320 "
            f"same-mesh instance pairs this measurement is made over live in "
            f"the REAL v5 stream, and `blender_tool/exports/` is gitignored "
            f"extracted game data. Re-extract with `python.exe "
            f"scripts/le_scene_extract.py 942c829457a04a62 --instance-lightmap "
            f"--out {REAL_V5_PKG}` to make this test able to run. ⛔ WHILE THIS "
            f"SKIP IS ACTIVE NOTHING REFUTES THE SHARED-MESH AFFINE "
            f"ALTERNATIVE ON REAL DATA — i.e. nothing shows that per-instance "
            f"mesh data is required.")
    si, _mb = _scatter_import()
    pkg = scatter_reader.ScatterPackage(REAL_V5_PKG)
    per = {}
    for r in scatter_reader.read_instances(pkg):
        per.setdefault(r.mesh_index, []).append(r.index)
    errs = []
    for mi, idx in per.items():
        if len(idx) < 2:
            continue
        for j in range(1, min(len(idx), 4)):
            e = si.uv_affine_fit_error(pkg, mi, idx[0], idx[j])
            if e is not None:
                errs.append(e)
    errs.sort()
    assert len(errs) > 1000, len(errs)
    median = errs[len(errs) // 2]
    affine = sum(1 for e in errs if e < 1e-4) / len(errs)
    assert median > 0.05, median             # ⛔ not a scale+offset of each other
    assert affine < 0.25, affine             # ⛔ and not even mostly


def test_uv_affine_fit_error_is_none_without_a_stream(tmp_path):
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    si, _mb = _scatter_import()
    assert si.uv_affine_fit_error(
        scatter_reader.ScatterPackage(pkg_dir), 0, 0, 1) is None


# =============================================================================
# the render fixture (⚠ SYNTHETIC UVs over REAL geometry) — built, then parsed
# =============================================================================

def build_e2_render_fixture(out_dir, src_pkg=REAL_V4_PKG, *, center=(0.0, -108.0, -13.0),
                            radius=20.0, lod_level=0, max_instances=1400,
                            name="e2_instlm_fixture", instance_lightmap="synthetic",
                            mesh_filter=None):
    """A bounded SPATIAL subset of a real `.lescatter`, plus a v5 stream.

    `instance_lightmap="real"` CARRIES THROUGH the source package's own
    per-instance UVs and pages (requires a v5 source — E1's
    `942c829457a04a62_instlm.lescatter`); `"synthetic"` fabricates them.

    ⚠ `max_instances` is NOT a usable spread control on this data (D2 §3c: the
    layout is contiguous per-mesh runs, so the first 2000 LOD-0 instances of
    station_front span ONE mesh). This selects by **distance from a point** at a
    fixed LOD instead, which is why a few hundred instances still span >100
    distinct meshes and >10 materials.

    ⚠ With `instance_lightmap="synthetic"` the geometry, the materials and the
    atlas are REAL but the per-instance lightmap UVs and pages are **SYNTHETIC**:
    each instance is given its own rectangle of its own page, derived from its
    own `uv0`. That demonstrates the PLUMBING and is **not** evidence about the
    shipped bake. With `"real"` nothing is fabricated.
    """
    import math
    src = scatter_reader.ScatterPackage(src_pkg)
    recs = scatter_reader.read_instances(src)
    lods = scatter_reader.read_instance_lod(src)
    sel = scatter_reader.filter_by_lod(recs, lods, lod_level)

    def blender_pos(r):
        x, y, z = r.translation
        return (x, -z, y)                       # the Y-up -> Z-up basis

    keep = [r for r in sel
            if sum((blender_pos(r)[i] - center[i]) ** 2 for i in range(3)) <= radius ** 2]
    if mesh_filter is not None:
        # ⚠ Keeping ONLY named mesh indices is how the same-mesh comparison shot
        # is made possible at all: in the full bounded subset those props are
        # occluded from every camera, and a picture of an occluder proves nothing.
        want = set(mesh_filter)
        keep = [r for r in keep if r.mesh_index in want]
    keep.sort(key=lambda r: (r.mesh_index, r.index))
    if max_instances:
        keep = keep[:max_instances]

    used = sorted({r.mesh_index for r in keep})
    remap = {old: new for new, old in enumerate(used)}
    by_index = {m["index"]: m for m in src.meshes}

    pkg = Path(out_dir) / f"{name}.lescatter"
    blobs = pkg / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)

    meshes = []
    counts = {}
    for r in keep:
        counts[remap[r.mesh_index]] = counts.get(remap[r.mesh_index], 0) + 1
    offset = 0
    for old in used:
        src_entry = by_index[old]
        new = remap[old]
        entry = dict(src_entry)
        entry["index"] = new
        for key in ("positions", "normals", "uv0", "uv1", "indices"):
            rel = src_entry.get(key)
            if not rel:
                entry.pop(key, None)
                continue
            dst_rel = f"blobs/m{new}_{key}.bin"
            (pkg / dst_rel).write_bytes((src.dir / rel).read_bytes())
            entry[key] = dst_rel
        entry["instance_offset"] = offset
        entry["instance_count"] = counts[new]
        offset += counts[new]
        meshes.append(entry)

    with open(blobs / "instances.bin", "wb") as fh:
        for r in keep:
            fh.write(struct.pack(scatter_reader.INSTANCE_STRUCT, remap[r.mesh_index],
                                 r.translation[0], r.translation[1], r.translation[2],
                                 r.rotation[0], r.rotation[1], r.rotation[2],
                                 r.rotation[3], r.scale[0], r.scale[1], r.scale[2]))

    manifest = {
        "format": "le_scatter", "version": 5, "master": src.master, "axis": "native",
        "num_meshes": len(meshes), "num_instances": len(keep),
        "meshes": meshes, "instances_blob": "blobs/instances.bin",
        "lightmap_stats": src.manifest.get("lightmap_stats", {}),
        "e2_fixture": {
            "synthetic_instance_lightmap": instance_lightmap != "real",
            "source_package": str(src_pkg),
            "selection": {"center": list(center), "radius": radius,
                          "lod_level": lod_level, "space": "blender"},
        },
    }
    (pkg / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # --- carry the REAL stream through, in the compacted instance order ------
    if instance_lightmap == "real":
        src_ilm = src.instance_lightmap
        if not src_ilm.present:
            raise ValueError(f"{src_pkg}: no v5 instance_lightmap stream "
                             f"({src_ilm.reason})")
        uv_flat, offsets, counts, pages = [], [], [], []
        cursor = 0
        for r in keep:
            uv = src_ilm.uv(r.index)
            page = src_ilm.page(r.index)
            uv = list(uv) if uv is not None else []
            offsets.append(cursor)
            counts.append(len(uv) // 2)
            pages.append(0xFFFFFFFF if page is None else int(page))
            uv_flat.extend(uv)
            cursor += len(uv) // 2
        array("f", uv_flat).tofile(open(blobs / "instance_lm_uv.bin", "wb"))
        array("I", offsets).tofile(open(blobs / "instance_lm_uvoff.bin", "wb"))
        array("I", counts).tofile(open(blobs / "instance_lm_count.bin", "wb"))
        array("I", pages).tofile(open(blobs / "instance_lm_page.bin", "wb"))
        manifest["instance_lightmap"] = {
            "present": True, "count": len(keep),
            "uv_blob": "blobs/instance_lm_uv.bin",
            "offsets_blob": "blobs/instance_lm_uvoff.bin",
            "counts_blob": "blobs/instance_lm_count.bin",
            "page_blob": "blobs/instance_lm_page.bin",
            "total_uv_pairs": cursor,
            "flip_v_applied": bool(src_ilm.flip_v_applied),
            "carried_from": str(src_pkg),
        }
        (pkg / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                           encoding="utf-8")
        return pkg, {"instances": len(keep), "meshes": len(meshes),
                     "total_uv_pairs": cursor, "instance_lightmap": "real"}

    # --- the SYNTHETIC per-instance stream ----------------------------------
    fixture = scatter_reader.ScatterPackage(pkg)
    uv0_cache = {}

    def _chart(entry):
        """The mesh's own uv0, normalised to its bbox -> a unit chart."""
        m = entry["index"]
        if m in uv0_cache:
            return uv0_cache[m]
        n = int(entry["nverts"])
        uv = fixture.uv0(entry)
        if uv is None or len(uv) < n * 2:
            pos = fixture.positions(entry)
            uv = [0.0] * (n * 2)
            for k in range(n):
                uv[k * 2] = pos[k * 3]
                uv[k * 2 + 1] = pos[k * 3 + 2]
        us, vs = uv[0::2][:n], uv[1::2][:n]
        lo_u, hi_u = min(us), max(us)
        lo_v, hi_v = min(vs), max(vs)
        du = (hi_u - lo_u) or 1.0
        dv = (hi_v - lo_v) or 1.0
        chart = [c for k in range(n)
                 for c in ((us[k] - lo_u) / du, (vs[k] - lo_v) / dv)]
        uv0_cache[m] = chart
        return chart

    def uv_of(i, entry):
        chart = _chart(entry)
        h = (i * 2654435761) & 0xFFFFFFFF
        w = 0.06 + ((h >> 3) % 90) / 1000.0            # 0.060 .. 0.149
        hgt = 0.06 + ((h >> 11) % 90) / 1000.0
        ox = 0.02 + ((h >> 17) % 800) / 1000.0 * (0.96 - w)
        oy = 0.02 + ((h >> 23) % 800) / 1000.0 * (0.96 - hgt)
        return [(ox + chart[j] * w) if j % 2 == 0 else (oy + chart[j] * hgt)
                for j in range(len(chart))]

    def page_of(i, entry):
        return (i * 7 + int(entry["index"]) * 3) % 13

    section = write_instance_lightmap(pkg, uv_of, page_of, flip_v_applied=False)
    return pkg, {"instances": len(keep), "meshes": len(meshes),
                 "total_uv_pairs": section["total_uv_pairs"],
                 "instance_lightmap": "synthetic"}


def test_the_render_fixture_builds_a_readable_v5_package(tmp_path):
    """The fixture the pictures are rendered from is itself parseable by the
    reader — so a broken picture is never blamed on the fixture by guesswork."""
    from unittest import SkipTest
    if not (REAL_V4_PKG / "manifest.json").is_file():
        raise SkipTest(
            f"{REAL_V4_PKG.name} is not extracted in this checkout — the E2 "
            f"render fixture is built FROM it, and `blender_tool/exports/` is "
            f"gitignored extracted game data. Re-extract with `python.exe "
            f"scripts/le_scene_extract.py 942c829457a04a62 --out "
            f"{REAL_V4_PKG}` to make this test able to run. ⛔ WHILE THIS SKIP "
            f"IS ACTIVE THE FIXTURE THE E2 PICTURES ARE RENDERED FROM IS NEVER "
            f"READ BACK, SO A BROKEN PICTURE CANNOT BE CLEARED OF BEING A "
            f"BROKEN FIXTURE.")
    pkg_dir, info = build_e2_render_fixture(tmp_path, radius=6.0, max_instances=40)
    pkg = scatter_reader.ScatterPackage(pkg_dir)
    assert pkg.manifest["version"] == 5
    assert pkg.manifest["e2_fixture"]["synthetic_instance_lightmap"] is True
    ilm = pkg.instance_lightmap
    assert ilm.present and ilm.count == pkg.num_instances == info["instances"]
    entries = {m["index"]: m for m in pkg.meshes}
    seen_pages = set()
    for rec in scatter_reader.read_instances(pkg):
        uv = ilm.uv(rec.index)
        assert uv is not None
        assert len(uv) == entries[rec.mesh_index]["nverts"] * 2
        assert all(0.0 <= c <= 1.0 for c in uv)      # inside the atlas
        seen_pages.add(ilm.page(rec.index))
    assert len(seen_pages) > 1                       # multiple pages exercised
    # instances of the same mesh must disagree, or the fixture proves nothing
    per_mesh = {}
    for rec in scatter_reader.read_instances(pkg):
        per_mesh.setdefault(rec.mesh_index, []).append(rec.index)
    multi = [v for v in per_mesh.values() if len(v) > 1]
    if multi:
        a, b = multi[0][:2]
        assert list(ilm.uv(a)) != list(ilm.uv(b))


def test_the_real_subset_fixture_carries_the_stream_through_unaltered(tmp_path):
    """The picture fixture must not perturb what it is a picture of: every kept
    instance's UVs and page come out byte-for-byte as they went in."""
    from unittest import SkipTest
    if not (REAL_V5_PKG / "manifest.json").is_file():
        raise SkipTest(
            f"{REAL_V5_PKG.name} is not extracted in this checkout — the "
            f"subset fixture is cut FROM its real instance-lightmap stream, "
            f"and `blender_tool/exports/` is gitignored extracted game data. "
            f"Re-extract with `python.exe scripts/le_scene_extract.py "
            f"942c829457a04a62 --instance-lightmap --out {REAL_V5_PKG}` to "
            f"make this test able to run. ⛔ WHILE THIS SKIP IS ACTIVE NOTHING "
            f"VERIFIES THAT THE PICTURE FIXTURE CARRIES REAL PER-INSTANCE UVs "
            f"AND PAGES THROUGH UNALTERED.")
    pkg_dir, info = build_e2_render_fixture(tmp_path, REAL_V5_PKG, radius=6.0,
                                            max_instances=60,
                                            instance_lightmap="real")
    assert info["instance_lightmap"] == "real"
    out = scatter_reader.ScatterPackage(pkg_dir)
    assert out.manifest["e2_fixture"]["synthetic_instance_lightmap"] is False
    assert out.instance_lightmap.flip_v_applied is False

    src = scatter_reader.ScatterPackage(REAL_V5_PKG)
    src_recs = {r.index: r for r in scatter_reader.read_instances(src)}
    # match by (mesh name_hash, translation) — the subset renumbers meshes
    src_name = {m["index"]: m["name_hash"] for m in src.meshes}
    out_name = {m["index"]: m["name_hash"] for m in out.meshes}
    by_key = {(src_name[r.mesh_index], tuple(round(c, 4) for c in r.translation)): i
              for i, r in src_recs.items()}
    checked = 0
    for r in scatter_reader.read_instances(out):
        key = (out_name[r.mesh_index], tuple(round(c, 4) for c in r.translation))
        src_i = by_key[key]
        assert list(out.instance_lightmap.uv(r.index)) == \
            list(src.instance_lightmap.uv(src_i))
        assert out.instance_lightmap.page(r.index) == \
            src.instance_lightmap.page(src_i)
        checked += 1
    assert checked == info["instances"] > 0
