"""Tests for `tests/vista_measure.py` — the exterior-level report generator.

Every case here locks down something that was actually WRONG on the first pass
of the vista build, so each one is a regression test rather than a coverage
exercise:

* the `.lescatter` instance record is **44 B = u32 + 10 f32**, not 11.  Reading
  it as 11 floats silently parsed 7,898 records out of an 8,616-record blob and
  every number downstream — play-area extent, max |T|, per-mesh counts — came
  out short without any error.
* `rdef_name_harvest.tsv` must be read **without** the `is_texture_resource`
  filter.  That column is a RESIDENCY flag; filtering on it drops all 30 of this
  level's `vst_saturn_*` binds and reproduces the published-and-retracted
  "Saturn binds nothing" exactly.
* `generated_composite_*` must not vote in the scatter classification.  It is a
  cook artefact on nearly every shaderset and letting it vote put 192 of 194
  meshes in one meaningless bucket.
* `max |T|` must be measured over the ACTUAL instances, not over the AABB
  corners — a corner need not be occupied (1,945 vs the true 1,720 here).
"""

from __future__ import annotations

import json
import struct

import vista_measure as VM


# ---------------------------------------------------------------------------
# the instance record
# ---------------------------------------------------------------------------

def test_instance_record_is_44_bytes():
    """u32 mesh_index + 3 translation + 4 rotation + 3 scale = 44 B."""
    assert VM.INSTANCE_STRUCT.size == 44, VM.INSTANCE_STRUCT.size
    assert VM.INSTANCE_STRUCT.format in ("<I10f", b"<I10f")


def _write_scatter(tmp_path, instances, meshes=None, lods=None):
    d = tmp_path / "synthetic.lescatter"
    (d / "blobs").mkdir(parents=True)
    raw = b"".join(VM.INSTANCE_STRUCT.pack(mi, *t, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0)
                   for mi, t in instances)
    (d / "blobs" / "instances.bin").write_bytes(raw)
    man = {
        "format": "le_scatter", "version": 5, "master": "deadbeefdeadbeef",
        "num_meshes": len(meshes or []), "num_instances": len(instances),
        "meshes": meshes or [], "instances_blob": "blobs/instances.bin",
    }
    if lods is not None:
        (d / "blobs" / "instance_lod.bin").write_bytes(
            b"".join(struct.pack("<III", *r) for r in lods))
        man["lod"] = {"blob": "blobs/instance_lod.bin", "num_groups": 1,
                      "max_level": max(r[1] for r in lods)}
    (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return d


def test_scatter_reader_reads_every_instance(tmp_path):
    inst = [(i % 3, (float(i), -float(i), 2.0 * i)) for i in range(37)]
    sp = VM.ScatterPkg(_write_scatter(tmp_path, inst))
    got = list(sp.instances())
    assert len(got) == 37, len(got)
    assert got[5][1] == 5 % 3
    assert got[5][2] == (5.0, -5.0, 10.0)


def test_max_abs_t_is_over_instances_not_aabb_corners(tmp_path):
    """The AABB corner (3,4,0) has |T| = 5; no instance is there — the true max
    is 4.  Reporting the corner inflates the play radius, which is exactly the
    1,945-vs-1,720 error this replaced."""
    inst = [(0, (3.0, 0.0, 0.0)), (0, (0.0, 4.0, 0.0))]
    sp = VM.ScatterPkg(_write_scatter(tmp_path, inst))
    res = VM.measure_scatter(sp, lod_level=-1)
    assert abs(res["max_abs_t"] - 4.0) < 1e-9, res["max_abs_t"]
    corner = max(abs(v) for v in res["t_max"]), max(abs(v) for v in res["t_min"])
    assert (corner[0] ** 2 + 3.0 ** 2) ** 0.5 > res["max_abs_t"]


def test_lod_filter_keeps_only_the_requested_level(tmp_path):
    inst = [(0, (0.0, 0.0, 0.0)) for _ in range(4)]
    lods = [(0, 0, 2), (0, 1, 2), (1, 0, 2), (1, 1, 2)]
    sp = VM.ScatterPkg(_write_scatter(tmp_path, inst, lods=lods))
    assert VM.measure_scatter(sp, 0)["instances_at_lod"] == 2
    assert VM.measure_scatter(sp, 1)["instances_at_lod"] == 2
    assert VM.measure_scatter(sp, -1)["instances_at_lod"] == 4


def test_lod_filter_clamps_to_a_groups_coarsest_level(tmp_path):
    """A 1-level group asked for LOD 3 must still contribute, not vanish."""
    inst = [(0, (0.0, 0.0, 0.0)), (0, (1.0, 0.0, 0.0))]
    lods = [(0, 0, 1), (1, 0, 4)]
    sp = VM.ScatterPkg(_write_scatter(tmp_path, inst, lods=lods))
    assert VM.measure_scatter(sp, 3)["instances_at_lod"] == 1


# ---------------------------------------------------------------------------
# the RDEF harvest — the residency-flag trap
# ---------------------------------------------------------------------------

def _write_harvest(tmp_path):
    p = tmp_path / "harvest.tsv"
    rows = [
        "archive_hash\tshaderset_hash\tbind\tname\tname_hash\tis_texture_resource",
        # ⛔ the vista rows carry is_texture_resource == 0
        "4c47d84c1e52447a\ta849eddeb321dcc7\t0\tvst_starfield_nebula_clr\tee3f6836bb3ae832\t0",
        "4c47d84c1e52447a\t6f67762bf83d59fd\t9\tvst_saturn_planet_hdr\t5ac9f126a8a79928\t0",
        "4c47d84c1e52447a\t44538616b0138eb3\t28\tvst_saturn_rings_debris_rock_a_nml\taaaaaaaaaaaaaaaa\t0",
        "4c47d84c1e52447a\tdeadbeefdeadbeef\t0\tmin_stone_plated_a_tile_hgt\tbbbbbbbbbbbbbbbb\t1",
        "OTHERARCHIVE0000\tcafecafecafecafe\t0\tsomething_else_clr\tcccccccccccccccc\t1",
    ]
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_load_rdef_does_not_filter_on_is_texture_resource(tmp_path):
    """★ The single filter that produced (and forced the retraction of)
    'Saturn binds nothing'."""
    by_ss, by_hash, cov = VM.load_rdef(_write_harvest(tmp_path), "4c47d84c1e52447a")
    assert "vst_starfield_nebula_clr" in by_ss["a849eddeb321dcc7"]
    assert "vst_saturn_planet_hdr" in by_ss["6f67762bf83d59fd"]
    assert cov["rows_for_archive"] == 4, cov
    assert cov["shadersets_for_archive"] == 4, cov
    assert "vst_saturn_planet_hdr" in by_hash["5ac9f126a8a79928"]


def test_load_rdef_filters_by_archive(tmp_path):
    by_ss, _h, cov = VM.load_rdef(_write_harvest(tmp_path), "4c47d84c1e52447a")
    assert "cafecafecafecafe" not in by_ss
    assert cov["rows_total"] == 5


# ---------------------------------------------------------------------------
# name bucketing
# ---------------------------------------------------------------------------

def test_ring_debris_rock_wins_over_the_generic_vista_prefix():
    """`vst_saturn_rings_debris_rock_*` must not be swallowed by `vst_saturn_`
    or by `vst_` — prefix order is load-bearing."""
    assert VM.bucket_of("vst_saturn_rings_debris_rock_a_nml") == "ring_debris_rock"
    assert VM.bucket_of("vst_saturn_rings_clr") == "vista_saturn"
    assert VM.bucket_of("vst_stn_ice_containers_emi") == "vista_other"


def test_bucket_of_covers_the_shipped_prefixes():
    cases = {
        "min_stone_plated_a_tile_hgt": "mining_site",
        "prp_survey_station_lod_emi": "prop",
        "stn_metal_seams_a_ribbon_hgt": "station",
        "cmn_powdercoat_a_tile_grunge_hgt": "common_material",
        "generated_composite_deadbeef_cafebabe": "generated_composite",
        "totally_unknown_thing": "other",
    }
    for name, want in cases.items():
        assert VM.bucket_of(name) == want, (name, VM.bucket_of(name))


def _mesh(idx, matidx, shdidx, nverts=10):
    return {"index": idx, "name_hash": f"{idx:016x}", "matidx": matidx,
            "shdidx": shdidx, "nverts": nverts,
            "draws": [{"matidx": matidx, "shdidx": shdidx}]}


def test_classify_scatter_excludes_cook_composites_from_the_vote(tmp_path):
    """★ Letting `generated_composite_*` vote put 192 of 194 real meshes into one
    bucket.  A mesh whose shaderset binds a cook composite AND a real asset must
    bucket on the real asset."""
    meshes = [_mesh(0, 0, 0), _mesh(1, 1, 1)]
    inst = [(0, (0.0, 0.0, 0.0)), (1, (1.0, 0.0, 0.0))]
    d = _write_scatter(tmp_path, inst, meshes=meshes)
    sp = VM.ScatterPkg(d)
    sidecar = {"version": 2, "master": "deadbeefdeadbeef", "materials": [
        {"matidx": 0, "shdidx": 0, "spec": {"shaderset_hash": "rock"}},
        {"matidx": 1, "shdidx": 1, "spec": {"shaderset_hash": "cookonly"}},
    ]}
    mj = tmp_path / "mat.json"
    mj.write_text(json.dumps(sidecar), encoding="utf-8")
    by_ss = {
        # one real name buried under five cook composites
        "rock": ["generated_composite_a_b"] * 5 + ["vst_saturn_rings_debris_rock_a_nml"],
        "cookonly": ["generated_composite_c_d"] * 3,
    }
    per_mesh = {0: 1, 1: 1}
    buckets, unnamed, n = VM.classify_scatter(sp, str(mj), by_ss, per_mesh)
    assert buckets["ring_debris_rock"]["meshes"] == 1, dict(buckets)
    assert buckets["cook_composites_only"]["meshes"] == 1, dict(buckets)
    assert unnamed["meshes"] == 0
    assert n == 2
    # and the cook names must NOT pollute the real bucket's name census
    assert all(not k.startswith("generated_composite_")
               for k in buckets["ring_debris_rock"]["names"])


def test_classify_scatter_reports_a_mesh_with_no_shaderset_row(tmp_path):
    """A join miss is COUNTED, never silently dropped."""
    meshes = [_mesh(0, 7, 7)]
    d = _write_scatter(tmp_path, [(0, (0.0, 0.0, 0.0))], meshes=meshes)
    sp = VM.ScatterPkg(d)
    mj = tmp_path / "mat.json"
    mj.write_text(json.dumps({"version": 2, "materials": []}), encoding="utf-8")
    buckets, unnamed, _n = VM.classify_scatter(sp, str(mj), {}, {0: 1})
    assert unnamed["meshes"] == 1
    assert buckets["unnamed_shaderset"]["meshes"] == 1


def test_bucket_spatial_separates_an_in_plane_band_from_a_raised_cluster():
    """The measurement behind 'the rocks are a band IN the ring plane'."""
    buckets = {"rocks": {"mesh_indices": [0]}, "station": {"mesh_indices": [1]}}
    pos = [(0, (float(i), 0.0, 0.05 * ((i % 5) - 2))) for i in range(50)]
    pos += [(1, (float(i), 0.0, 90.0 + 0.1 * i)) for i in range(10)]
    out = VM.bucket_spatial(buckets, pos, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    assert abs(out["rocks"]["plane_d_median"]) < 0.2, out["rocks"]
    assert out["station"]["plane_d_median"] > 80.0, out["station"]
    assert out["rocks"]["plane_d_absmean"] < out["station"]["plane_d_absmean"]
