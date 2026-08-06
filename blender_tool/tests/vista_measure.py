"""Measure the exterior (Saturn vista) level — placement, rings, rocks, materials.

    python3 tests/vista_measure.py \
        --mesh   exports/min_itc_master/4c47d84c1e52447a_4c47d84c1e52447a.lemesh \
        --scatter exports/4c47d84c1e52447a_v4.lescatter \
        --materials exports/4c47d84c1e52447a_materials.json \
        --rdef   ../generic_rebuilds/rdef_name_harvest.tsv \
        --json   exports/hero/vista_measure.json

Pure stdlib, **no Blender and no archive access** — it reads only the extracted
`.lemesh` / `.lescatter` packages, so it is safe to run without the heavy lock.
NOT named `test_*`: `tests/run_tests.py` imports every `test_*.py`, and this is a
report generator.  The math it uses is `le_mesh/vista_fit.py`, which *is* tested
there (`tests/test_vista_fit.py`).

What it answers, and why each question is here
---------------------------------------------
1. **Where is everything.**  Sphere fit for the skydome and for Saturn, plane fit
   + annulus metrics for each ring object, centroid/radius for the moons, the
   sun card's direction, and the scatter's AABB.  Nothing is assumed from an
   earlier note; every number is recomputed from the position blobs.
2. **Does the ring plane pass through the play area.**  The reference art shows
   the station sitting *in* the ring plane, so this is a falsifiable claim about
   the shipped geometry, not a composition preference.
3. **Which vertices lie outside the skydome shell.**  This is the number that
   forces the skydome special-case in a depth-sorted renderer.
4. **Which scatter meshes are ring debris and which are the dig site.**  Joined
   mesh -> (matidx, shdidx) -> materials sidecar -> shaderset -> RDEF asset
   names, then bucketed by the shipped name prefix.  ⚠ The join is reported with
   its coverage; a mesh whose shaderset has no RDEF row is counted as
   `unnamed`, never silently dropped.

⛔ `generic_rebuilds/rdef_name_harvest.tsv` is read WITHOUT filtering on
`is_texture_resource` — that column is a RESIDENCY flag and filtering on it drops
the entire `vst_saturn_*` group (all 30 of this level's vista binds carry 0).
"""

from __future__ import annotations

import argparse
import array
import csv
import json
import math
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(ROOT),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from le_mesh import vista_fit as V      # noqa: E402

#: Instance record in `blobs/instances.bin` — see `addon/.../scatter_reader.py`.
#: ⚠ 44 B == `u32 + 10 f32`, NOT `u32 + 11 f32`.  Getting that wrong reads 7,898
#: records out of a 8,616-record blob and every number downstream is quietly
#: short, which is exactly what happened on the first pass here.
INSTANCE_STRUCT = struct.Struct("<I10f")     # mesh_index, t3, q4, s3 = 44 B
assert INSTANCE_STRUCT.size == 44

#: Cooked composite textures.  They dominate the RDEF name list on every mesh
#: (192 of 194 here) and carry no asset identity, so they are excluded from the
#: bucket VOTE — but still counted, so their share is visible.
COOK_PREFIX = "generated_composite_"

#: Prefix -> bucket for the scatter classification.  Order matters: the first
#: matching prefix wins, so the specific ring-debris family is tested before the
#: generic `vst_`.
NAME_BUCKETS = (
    ("vst_saturn_rings_debris_rock_", "ring_debris_rock"),
    ("vst_saturn_", "vista_saturn"),
    ("vst_", "vista_other"),
    ("min_", "mining_site"),
    ("mfx_min_", "mining_site_fx"),
    ("gfx_min_", "mining_site_fx"),
    ("prp_", "prop"),
    ("stn_", "station"),
    ("itt_", "itt_hardware"),
    ("cmn_", "common_material"),
    ("fx_", "fx"),
    ("gfx_", "fx"),
    ("pfx_", "fx"),
    ("mfx_", "fx"),
    ("sr8_", "sr8_marker"),
    ("cyn_", "cyn_swatch"),
    ("gpp_", "gpp"),
    ("generated_composite_", "generated_composite"),
)


# ---------------------------------------------------------------------------
# package readers (thin — the addon's readers need bpy on some paths)
# ---------------------------------------------------------------------------

def _f32(path):
    a = array.array("f")
    with open(path, "rb") as fh:
        a.frombytes(fh.read())
    if sys.byteorder != "little":
        a.byteswap()
    return a


def _points(a):
    return [(a[i], a[i + 1], a[i + 2]) for i in range(0, len(a) - 2, 3)]


class MeshListPkg:
    """The level's root `.lemesh` (39 objects for `min_itc_master`)."""

    def __init__(self, path):
        self.dir = Path(path)
        if self.dir.name == "manifest.json":
            self.dir = self.dir.parent
        self.manifest = json.loads((self.dir / "manifest.json").read_text("utf-8"))
        self.objects = self.manifest["objects"]
        self.materials = {m["key"]: m for m in self.manifest.get("materials", [])}

    def positions(self, obj):
        blob = obj["attributes"]["position"]["blob"]
        return _points(_f32(self.dir / blob))

    def material_keys(self, obj):
        return sorted({d.get("material_key") for d in obj.get("draws", [])
                       if d.get("material_key")})


class ScatterPkg:
    """The level's `.lescatter` (194 meshes / 8,616 instances)."""

    def __init__(self, path):
        self.dir = Path(path)
        if self.dir.name == "manifest.json":
            self.dir = self.dir.parent
        self.manifest = json.loads((self.dir / "manifest.json").read_text("utf-8"))
        self.meshes = self.manifest["meshes"]
        self.by_index = {m["index"]: m for m in self.meshes}

    def instances(self):
        raw = (self.dir / self.manifest["instances_blob"]).read_bytes()
        n = len(raw) // INSTANCE_STRUCT.size
        for i in range(n):
            v = INSTANCE_STRUCT.unpack_from(raw, i * INSTANCE_STRUCT.size)
            yield i, v[0], v[1:4], v[4:8], v[8:11]

    def lod_levels(self):
        lod = self.manifest.get("lod") or {}
        blob = lod.get("blob")
        if not blob:
            return None
        raw = (self.dir / blob).read_bytes()
        n = len(raw) // 12
        out = []
        for i in range(n):
            g, lvl, lvls = struct.unpack_from("<III", raw, i * 12)
            out.append((g, lvl, lvls))
        return out


# ---------------------------------------------------------------------------
# RDEF names
# ---------------------------------------------------------------------------

def load_rdef(tsv_path, archive=None):
    """-> ({shaderset_hash: [names]}, {name_hash: {names}}, coverage dict).

    ⛔ No `is_texture_resource` filter.  See the module docstring.
    """
    by_ss = defaultdict(list)
    by_hash = defaultdict(set)
    rows = arch_rows = 0
    with open(tsv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            rows += 1
            if archive and row["archive_hash"] != archive:
                continue
            arch_rows += 1
            by_ss[row["shaderset_hash"]].append(row["name"])
            by_hash[row["name_hash"]].add(row["name"])
    return by_ss, by_hash, {"rows_total": rows, "rows_for_archive": arch_rows,
                            "shadersets_for_archive": len(by_ss)}


def bucket_of(name):
    for prefix, bucket in NAME_BUCKETS:
        if name.startswith(prefix):
            return bucket
    return "other"


# ---------------------------------------------------------------------------
# report sections
# ---------------------------------------------------------------------------

def measure_meshlist(pkg, shell_radius=None, out=print):
    """Per-object distance-from-origin and the vista fits.  Returns a dict."""
    res = {"objects": [], "fits": {}}
    obj_by_name = {}
    for obj in pkg.objects:
        pts = pkg.positions(obj)
        obj_by_name[obj["name"][:6]] = (obj, pts)
        ds = [math.dist(p, (0.0, 0.0, 0.0)) for p in pts]
        keys = pkg.material_keys(obj)
        mt = sorted({pkg.materials[k]["mattype_name"] for k in keys
                     if k in pkg.materials})
        ang = V.angular_extent(pts) if len(pts) >= 2 else None
        res["objects"].append({
            "name": obj["name"], "verts": len(pts),
            "mattypes": mt, "material_keys": keys,
            "d_min": min(ds), "d_max": max(ds),
            "aabb_min": obj["aabb_min"], "aabb_max": obj["aabb_max"],
            "lightmap_index": obj.get("lightmap_index"),
            "lm_slice_index": obj.get("lm_slice_index"),
            "angular": ang,
            "outside_shell": (sum(1 for d in ds if d > shell_radius)
                              if shell_radius else None),
        })

    # --- skydome: score the WORLD ORIGIN against the object's own centroid ---
    sky = obj_by_name.get("obj018")
    if sky:
        pts = sky[1]
        origin = V.sphere_residuals(pts, (0.0, 0.0, 0.0))
        own = V.sphere_residuals(pts, V.centroid(pts))
        fit = V.fit_sphere(pts)
        res["fits"]["skydome"] = {"at_origin": origin, "at_centroid": own,
                                  "fitted": fit,
                                  "origin_beats_centroid":
                                      origin["rms_rel"] < own["rms_rel"]}
    sat = obj_by_name.get("obj030")
    if sat:
        f = V.fit_sphere(sat[1])
        res["fits"]["saturn"] = f
        res["fits"]["saturn_cap"] = V.cap_extent(sat[1], f["centre"])
    for m in ("obj031", "obj032", "obj033"):
        if m in obj_by_name:
            f = V.fit_sphere(obj_by_name[m][1])
            res["fits"][f"moon_{m}"] = f
            res["fits"][f"moon_{m}_cap"] = V.cap_extent(obj_by_name[m][1],
                                                        f["centre"])
    if "obj002" in obj_by_name:
        pts = obj_by_name["obj002"][1]
        c = V.centroid(pts)
        d = math.dist(c, (0.0, 0.0, 0.0))
        res["fits"]["sun_card"] = {
            "centroid": c, "distance": d,
            "direction": tuple(x / d for x in c) if d else (0.0, 0.0, 0.0),
            "corners": pts,
            "diag": max(math.dist(a, b) for a in pts for b in pts),
        }
    return res, obj_by_name


def measure_rings(obj_by_name, saturn_centre, names=("obj034", "obj035",
                                                     "obj036", "obj037", "obj038",
                                                     "obj003", "obj004")):
    out = {}
    for n in names:
        if n not in obj_by_name:
            continue
        pts = obj_by_name[n][1]
        out[n] = V.ring_metrics(pts, centre=saturn_centre)
    return out


def measure_scatter(sp, lod_level=0):
    """Instance AABB of the selected LOD, per-mesh counts, and the raw positions.

    `max_abs_t` is the largest |T| over the ACTUAL instances, not the AABB corner
    magnitude — an AABB corner need not be occupied, and using it inflates the
    play radius (1,945 vs the true 1,720 here).
    """
    lods = sp.lod_levels()
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    per_mesh = Counter()
    per_mesh_all = Counter()
    kept_pos = []
    total = kept = 0
    max_abs = 0.0
    for i, mi, t, _q, _s in sp.instances():
        total += 1
        per_mesh_all[mi] += 1
        if lods is not None and lod_level >= 0:
            g, lvl, lvls = lods[i]
            want = min(lod_level, max(0, lvls - 1)) if lvls else 0
            if g != 0xFFFFFFFF and lvl != want:
                continue
        kept += 1
        per_mesh[mi] += 1
        kept_pos.append((mi, t))
        max_abs = max(max_abs, math.dist((0.0, 0.0, 0.0), t))
        for k in range(3):
            lo[k] = min(lo[k], t[k])
            hi[k] = max(hi[k], t[k])
    centre = tuple((lo[k] + hi[k]) / 2.0 for k in range(3))
    return {
        "instances_total": total, "instances_at_lod": kept,
        "t_min": tuple(lo), "t_max": tuple(hi),
        "extent": tuple(hi[k] - lo[k] for k in range(3)),
        "centre": centre,
        "max_abs_t": max_abs,
        "per_mesh": per_mesh,
        "per_mesh_all": per_mesh_all,
        "positions": kept_pos,
    }


def classify_scatter(sp, materials_json, by_ss, per_mesh, per_mesh_all=None):
    """Bucket every scatter mesh by the RDEF asset names its shaderset binds."""
    sidecar = {}
    if materials_json:
        md = json.loads(Path(materials_json).read_text("utf-8"))
        for e in md.get("materials", []):
            sidecar[(int(e["matidx"]), int(e["shdidx"]))] = e
    per_mesh_all = per_mesh_all or per_mesh

    buckets = defaultdict(lambda: {"meshes": 0, "instances": 0,
                                   "instances_all_lod": 0, "verts": 0,
                                   "names": Counter(), "mesh_indices": []})
    unnamed = {"meshes": 0, "instances": 0}
    for m in sp.meshes:
        idx = m["index"]
        inst = per_mesh.get(idx, 0)
        pairs = {(int(d.get("matidx", m.get("matidx", -1))),
                  int(d.get("shdidx", m.get("shdidx", -1))))
                 for d in (m.get("draws") or [{}])}
        names = []
        for matidx, shdidx in pairs:
            e = sidecar.get((matidx, shdidx))
            ss = (e or {}).get("spec", {}).get("shaderset_hash") or \
                 (e or {}).get("shaderset_hash") or ""
            if ss:
                names += by_ss.get(ss, [])
        # ⚠ vote on the AUTHORED asset names only. `generated_composite_*` is a
        # cook artefact present on nearly every shaderset; letting it vote puts
        # 192 of 194 meshes in one meaningless bucket.
        votable = [n for n in names if not n.startswith(COOK_PREFIX)]
        if not names:
            unnamed["meshes"] += 1
            unnamed["instances"] += inst
            b = buckets["unnamed_shaderset"]
        elif not votable:
            unnamed["cook_only_meshes"] = unnamed.get("cook_only_meshes", 0) + 1
            b = buckets["cook_composites_only"]
            for n in names:
                b["names"][n] += 1
        else:
            counts = Counter(bucket_of(n) for n in votable)
            b = buckets[counts.most_common(1)[0][0]]
            for n in votable:
                b["names"][n] += 1
        b["meshes"] += 1
        b["instances"] += inst
        b["instances_all_lod"] += per_mesh_all.get(idx, 0)
        b["verts"] += int(m.get("nverts", 0))
        b["mesh_indices"].append(idx)
    return buckets, unnamed, len(sidecar)


def bucket_spatial(buckets, positions, plane_point, plane_normal):
    """Where each bucket's instances actually sit relative to the ring plane.

    The reference art's foreground is a debris FIELD; "is it a band in the ring
    plane or a shell around the site" is answerable from the instance
    translations alone, so it is answered rather than described.
    """
    owner = {}
    for name, b in buckets.items():
        for idx in b["mesh_indices"]:
            owner[idx] = name
    agg = defaultdict(lambda: {"n": 0, "d": [], "lo": [math.inf] * 3,
                               "hi": [-math.inf] * 3})
    for mi, t in positions:
        name = owner.get(mi)
        if name is None:
            continue
        a = agg[name]
        a["n"] += 1
        a["d"].append(V.point_plane_distance(t, plane_point, plane_normal))
        for k in range(3):
            a["lo"][k] = min(a["lo"][k], t[k])
            a["hi"][k] = max(a["hi"][k], t[k])
    out = {}
    for name, a in agg.items():
        d = sorted(a["d"])
        n = len(d)
        out[name] = {
            "instances": a["n"],
            "extent": tuple(a["hi"][k] - a["lo"][k] for k in range(3)),
            "plane_d_min": d[0], "plane_d_max": d[-1],
            "plane_d_median": d[n // 2],
            "plane_d_p05": d[int(0.05 * n)], "plane_d_p95": d[int(0.95 * n)],
            "plane_d_absmean": sum(abs(x) for x in d) / n,
        }
    return out


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mesh", required=True, help="the level root .lemesh package")
    ap.add_argument("--scatter", default="", help="the level .lescatter package")
    ap.add_argument("--materials", default="", help="<master>_materials.json")
    ap.add_argument("--rdef", default="", help="rdef_name_harvest.tsv")
    ap.add_argument("--archive", default="", help="archive hash to filter the harvest")
    ap.add_argument("--lod", type=int, default=0)
    ap.add_argument("--json", default="", help="write the full report here")
    a = ap.parse_args(argv)

    mp = MeshListPkg(a.mesh)
    archive = a.archive or mp.manifest.get("source", {}).get("archive", "")

    # pass 1: the skydome shell radius, then re-run with it so `outside_shell`
    # is measured against the fitted shell rather than a hardcoded number.
    first, obj_by_name = measure_meshlist(mp)
    shell = first["fits"].get("skydome", {}).get("at_origin", {}).get("r_mean")
    res, obj_by_name = measure_meshlist(mp, shell_radius=shell)

    print("=" * 78)
    print("MESH-LIST %s (archive %s) — %d objects"
          % (mp.manifest["source"]["meshlist"], archive, len(mp.objects)))
    print("=" * 78)
    sky = res["fits"].get("skydome")
    if sky:
        o, c = sky["at_origin"], sky["at_centroid"]
        print("\nSKYDOME sphere fit (obj018)")
        print("  about WORLD ORIGIN : R_mean %11.1f  rms %8.1f (%.3f %%)  "
              "max/min %.4f" % (o["r_mean"], o["rms"], 100 * o["rms_rel"], o["ratio"]))
        print("  about its CENTROID : R_mean %11.1f  rms %8.1f (%.3f %%)  "
              "max/min %.4f" % (c["r_mean"], c["rms"], 100 * c["rms_rel"], c["ratio"]))
        print("  least-squares fit  : centre (%.1f, %.1f, %.1f)  R %.1f"
              % (*sky["fitted"]["centre"], sky["fitted"]["r_fit"]))
        print("  -> origin fits BETTER than the centroid: %s"
              % sky["origin_beats_centroid"])
    sat = res["fits"].get("saturn")
    if sat:
        cap = res["fits"]["saturn_cap"]
        print("\nSATURN (obj030) — sphere fit")
        print("  centre (%.1f, %.1f, %.1f)  R %.1f  rms %.1f (%.2f %%)  max/min %.4f"
              % (*sat["centre"], sat["r_fit"], sat["rms"], 100 * sat["rms_rel"],
                 sat["ratio"]))
        print("  distance origin->centre %.1f" % math.dist(sat["centre"], (0, 0, 0)))
        print("  ⚠ only a CAP is modelled: half-angle %.1f deg (p95 %.1f), "
              "concentration %.3f" % (cap["half_angle_deg"],
                                      cap["p95_half_angle_deg"],
                                      cap["concentration"]))
    for k in sorted(res["fits"]):
        if not k.startswith("moon_") or k.endswith("_cap"):
            continue
        v = res["fits"][k]
        cap = res["fits"].get(k + "_cap", {})
        print("\nMOON %s: sphere centre (%.1f, %.1f, %.1f) R %.1f (max/min %.3f) "
              "dist from origin %.1f" % (k[5:], *v["centre"], v["r_fit"],
                                         v["ratio"], math.dist(v["centre"], (0, 0, 0))))
        print("  ⚠ cap half-angle %.1f deg, concentration %.3f — %s"
              % (cap.get("half_angle_deg", 0.0), cap.get("concentration", 0.0),
                 "the radius is INDICATIVE ONLY (shallow cap)"
                 if cap.get("half_angle_deg", 0.0) < 45.0 else "well conditioned"))
    sun = res["fits"].get("sun_card")
    if sun:
        print("\nSUN card (obj002): 4 verts, centroid (%.1f, %.1f, %.1f)"
              % sun["centroid"])
        print("  distance %.1f  direction (%.4f, %.4f, %.4f)  diagonal %.1f"
              % (sun["distance"], *sun["direction"], sun["diag"]))

    saturn_centre = sat["centre"] if sat else (0.0, 0.0, 0.0)
    rings = measure_rings(obj_by_name, saturn_centre)

    # ★ Saturn is OBLATE, and the hypothesis worth testing is "its poles are
    # perpendicular to its own rings".  Fit both ways: axis PINNED to the ring
    # normal, and axis free.  The pinned fit is the falsifiable claim; the free
    # fit says how well a cap constrains the pole at all.
    ring_normal = rings.get("obj038", rings.get("obj035", {})).get("normal")
    if sat and ring_normal:
        pts = obj_by_name["obj030"][1]
        pinned = V.fit_oblate_spheroid(pts, ring_normal, refine_axis=False)
        free = V.fit_oblate_spheroid(pts, ring_normal, refine_axis=True)
        res["fits"]["saturn_spheroid_ring_axis"] = pinned
        res["fits"]["saturn_spheroid_free_axis"] = free
        print("\nSATURN — OBLATE SPHEROID fit (the sphere's 3.4 %% residual is not "
              "noise)")
        print("  axis PINNED to the ring normal: a %.1f  c %.1f  flattening %.4f  "
              "mean residual %.4f  max %.4f"
              % (pinned["a"], pinned["c"], pinned["flattening"],
                 pinned["mean_residual"], pinned["max_residual"]))
        print("  axis FREE                     : a %.1f  c %.1f  flattening %.4f  "
              "mean residual %.4f  max %.4f  (axis %.2f deg off the ring normal)"
              % (free["a"], free["c"], free["flattening"], free["mean_residual"],
                 free["max_residual"], free["axis_deviation_deg"]))
        print("  centre (pinned) (%.1f, %.1f, %.1f)" % pinned["centre"])
        print("  for scale: real Saturn's flattening is 0.0980")

    print("\nANGULAR PLACEMENT, as seen from the WORLD ORIGIN")
    print("  %-30s %8s %9s %9s %11s %11s  direction"
          % ("object", "verts", "ang.rad°", "ang.diam°", "d_min", "d_max"))
    for o in res["objects"]:
        a_ = o.get("angular")
        if not a_ or a_["d_max"] < 3000.0:      # skip the near-field dig site
            continue
        print("  %-30s %8d %9.2f %9.2f %11.0f %11.0f  (%.3f, %.3f, %.3f)"
              % (o["name"], o["verts"], a_["angular_radius_deg"],
                 a_["angular_diameter_deg"], a_["d_min"], a_["d_max"],
                 *a_["direction"]))

    if rings:
        print("\nRING / HAZE PLANES  (radii measured about SATURN's fitted centre)")
        print("  %-8s %7s %10s %10s %9s %9s %8s %8s  normal"
              % ("obj", "verts", "r_inner", "r_outer", "tilt°", "flat", "axis", "span°"))
        for n, r in rings.items():
            print("  %-8s %7d %10.1f %10.1f %9.3f %9.2e %8.4f %8.0f  "
                  "(%.4f, %.4f, %.4f)"
                  % (n, r["count"], r["r_inner"], r["r_outer"], r["tilt_deg"],
                     r["plane"]["flatness"], r["axis_ratio"],
                     r["azimuth_span_deg"], *r["normal"]))
        res["rings"] = {k: {kk: vv for kk, vv in v.items() if kk != "plane"}
                        | {"plane": {kk: vv for kk, vv in v["plane"].items()}}
                        for k, v in rings.items()}

    if shell:
        print("\nVERTICES OUTSIDE THE FITTED SHELL R = %.1f" % shell)
        print("  ⛔ THE SKYDOME ITSELF IS EXCLUDED: it *is* the shell, so ~half its "
              "own vertices\n     sit above its own mean radius and the count means "
              "nothing there.")
        print("  %-30s %8s %11s %11s %9s" % ("object", "verts", "d_min", "d_max",
                                             "outside"))
        for o in res["objects"]:
            if not o["outside_shell"] or o["name"].startswith("obj018"):
                continue
            print("  %-30s %8d %11.1f %11.1f %6d (%.1f %%)"
                  % (o["name"], o["verts"], o["d_min"], o["d_max"],
                     o["outside_shell"], 100.0 * o["outside_shell"] / o["verts"]))

    scatter_res = None
    if a.scatter:
        sp = ScatterPkg(a.scatter)
        scatter_res = measure_scatter(sp, a.lod)
        print("\n" + "=" * 78)
        print("SCATTER %s — %d meshes, %d instances (LOD %d keeps %d)"
              % (sp.manifest["master"], len(sp.meshes),
                 scatter_res["instances_total"], a.lod,
                 scatter_res["instances_at_lod"]))
        print("=" * 78)
        print("  instance translation AABB %s .. %s"
              % (tuple(round(v, 1) for v in scatter_res["t_min"]),
                 tuple(round(v, 1) for v in scatter_res["t_max"])))
        print("  extent %s  centre %s  max |T| %.1f"
              % (tuple(round(v, 1) for v in scatter_res["extent"]),
                 tuple(round(v, 1) for v in scatter_res["centre"]),
                 scatter_res["max_abs_t"]))
        if rings:
            print("\n  RING PLANE vs THE PLAY AREA "
                  "(signed distance of the scatter centre from each plane)")
            for n, r in rings.items():
                d = V.point_plane_distance(scatter_res["centre"],
                                           r["plane"]["point"], r["normal"])
                lo_d = V.point_plane_distance(scatter_res["t_min"],
                                              r["plane"]["point"], r["normal"])
                hi_d = V.point_plane_distance(scatter_res["t_max"],
                                              r["plane"]["point"], r["normal"])
                print("    %-8s centre %12.1f   play-area corners %12.1f .. %12.1f  "
                      "%s" % (n, d, min(lo_d, hi_d), max(lo_d, hi_d),
                              "PLANE CUTS THE PLAY AREA"
                              if min(lo_d, hi_d) <= 0 <= max(lo_d, hi_d) else ""))

        if a.rdef:
            by_ss, _by_hash, cov = load_rdef(a.rdef, archive or None)
            buckets, unnamed, n_side = classify_scatter(
                sp, a.materials, by_ss, scatter_res["per_mesh"],
                scatter_res["per_mesh_all"])
            print("\n  RDEF join coverage: %d harvest rows total, %d for this archive, "
                  "%d shadersets; materials sidecar %d entries; %d mesh(es) had no "
                  "shaderset row"
                  % (cov["rows_total"], cov["rows_for_archive"],
                     cov["shadersets_for_archive"], n_side, unnamed["meshes"]))
            print("\n  %-22s %7s %10s %10s %12s  top asset names"
                  % ("bucket", "meshes", "inst@LOD0", "inst(all)", "verts"))
            for name, b in sorted(buckets.items(),
                                  key=lambda kv: -kv[1]["instances"]):
                top = ", ".join(n for n, _ in b["names"].most_common(3))
                print("  %-22s %7d %10d %10d %12d  %s"
                      % (name, b["meshes"], b["instances"],
                         b["instances_all_lod"], b["verts"], top[:64]))
            res["scatter_buckets"] = {
                k: {"meshes": v["meshes"], "instances": v["instances"],
                    "instances_all_lod": v["instances_all_lod"],
                    "verts": v["verts"], "mesh_indices": v["mesh_indices"],
                    "names": dict(v["names"].most_common(40))}
                for k, v in buckets.items()}

            if rings:
                r0 = rings.get("obj038") or next(iter(rings.values()))
                spat = bucket_spatial(buckets, scatter_res["positions"],
                                      r0["plane"]["point"], r0["normal"])
                print("\n  WHERE EACH BUCKET SITS (signed distance from the ring "
                      "plane of obj038, LOD %d)" % a.lod)
                print("  %-22s %8s %10s %10s %10s %10s  instance extent"
                      % ("bucket", "inst", "d_p05", "median", "d_p95", "|d| mean"))
                for name, s in sorted(spat.items(), key=lambda kv: -kv[1]["instances"]):
                    print("  %-22s %8d %10.1f %10.1f %10.1f %10.1f  %s"
                          % (name, s["instances"], s["plane_d_p05"],
                             s["plane_d_median"], s["plane_d_p95"],
                             s["plane_d_absmean"],
                             tuple(round(v, 1) for v in s["extent"])))
                res["scatter_spatial"] = spat
        res["scatter"] = {k: v for k, v in scatter_res.items()
                          if k not in ("per_mesh", "per_mesh_all", "positions")}

    if a.json:
        p = Path(a.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        print("\nwrote %s" % p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
