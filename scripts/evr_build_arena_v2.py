"""Build `mpl_arena_v2_a`: the arena cloned under a new name, obstacle field redesigned.

    python scripts/evr_build_arena_v2.py --clusters <c.json> --components <k.json>
                                         [--out <dir>] [--dry-run]

`--out` defaults to the packer's `input-pcvr` staging folder. `--dry-run`
validates and reports, writing nothing.

WHAT IT PRODUCES
----------------
1. A complete clone of `mpl_arena_a` as `mpl_arena_v2_a` -- all 90 per-level
   resources with their self-references repointed (`evr_clone_level`).
2. The obstacle field moved, turned and resized per `data/arena_v2_layout.json`,
   with **render placement, player collision and raycast geometry transformed
   together** (`evr_geo_move`).

Everything else -- tunnels, launchers, goals, spawns, UI, scripts, audio,
lighting, probes -- is stock data under a new key.

SYMMETRY IS GENERATED, NOT AUTHORED TWICE
-----------------------------------------
The layout describes only the `z > 0` half. The other half is derived here by
the arena's own symmetry, the 180-degree turn about y (`P = diag(-1, 1, -1)`):
a target `p` becomes `P p`, and a rotation `R` becomes `P R P`, which for a spin
about the long axis just reverses the angle. Authoring one half means the two
ends cannot drift apart, so neither team can get the better side.

VALIDATION -- all of it before a byte is written
------------------------------------------------
* every cluster the layout names exists, matched by its stock centre;
* the scale budget (0.80 - 1.20) is respected;
* nothing is pushed further out of the level than it already was;
* no transformed object's new box overlaps another's;
* no new box lands inside static collision -- tested against collision
  VERTICES, not boxes, because the shell and chambers are hollow and their
  AABBs cover the whole play space;
* after the edit, every cluster moved all of its bound collision components and
  all of its instances -- geometry left behind is an abort, not a warning.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import evr_clone_level as clone_mod           # noqa: E402
import evr_geo_move as geo                    # noqa: E402

SRC_NAME = "mpl_arena_a"
DST_NAME = "mpl_arena_v2_a"
EXTRACT = Path(r"H:\pcvr-extracted")
LAYOUT = _SCRIPTS.parent / "data" / "arena_v2_layout.json"
DEFAULT_OUT = clone_mod.DEFAULT_OUT

BIND_SLACK = 0.30
SCALE_MIN, SCALE_MAX = 0.80, 1.20
#: the 180-degree turn about y that maps one half of the arena onto the other.
P = np.diag([-1.0, 1.0, -1.0])


def spin_z(deg: float) -> np.ndarray:
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def plan(layout, clusters):
    """One entry per cluster: the box to select, the transform, the new box."""
    # nearest-centre lookup rather than an exact key: the stock halves are not
    # bit-symmetric (pacman sits at z 22.24 on one side and 22.23 on the other),
    # so an exact mirrored key misses by a centimetre.
    index = {}
    for name, cls in clusters.items():
        index[name] = [(np.array([(c["min"][i] + c["max"][i]) / 2 for i in range(3)]), c)
                       for c in cls]
    out, errs, taken = [], [], set()
    for m in layout["moves"]:
        name = m["object"]
        src = np.array(m["from"], float)
        for mirror in (False, True):
            here = P @ src if mirror else src
            cands = [(np.abs(c - here).max(), i, cl)
                     for i, (c, cl) in enumerate(index.get(name, []))
                     if (name, i) not in taken]
            cands.sort(key=lambda t: t[0])
            if not cands or cands[0][0] > 0.15:
                errs.append("%s: no free cluster near %s (have %s)"
                            % (name, np.round(here, 2),
                               [np.round(c, 2) for c, _ in index.get(name, [])]))
                continue
            _, ci, cl = cands[0]
            taken.add((name, ci))
            lo, hi = np.array(cl["min"]), np.array(cl["max"])
            ctr = (lo + hi) / 2
            sc = m.get("scale", 1.0)
            S = np.array([sc] * 3, float) if np.isscalar(sc) else np.array(sc, float)
            R = spin_z(m.get("spin_z_deg", 0.0) * (-1.0 if mirror else 1.0))
            tgt = np.array(m["to"], float)
            if mirror:
                tgt = P @ tgt
            M = R @ np.diag(S)
            corners = np.array([[lo[0] if k & 1 else hi[0], lo[1] if k & 2 else hi[1],
                                 lo[2] if k & 4 else hi[2]] for k in range(8)])
            w = (M @ (corners - ctr).T).T + tgt
            out.append({"name": name, "cluster": cl, "lo": lo, "hi": hi,
                        "R": R, "S": S, "pivot": ctr, "target": tgt,
                        "new_lo": w.min(0), "new_hi": w.max(0), "mirror": mirror})
    return out, errs


def static_points(lv, clusters):
    """Vertices of collision that is NOT part of a transformed object.

    Boxes do not work for this: the shell, tunnels and spawn chambers are
    hollow, so their AABBs cover the whole play space and every candidate
    position would look occupied. Vertices give the real occupancy.
    """
    moving = {c for v in clusters.values() for cl in v for c in cl["phys"]}
    keep, base = [], 0
    for verts, comp in zip(lv.pverts, lv.pcomp):
        for cid in np.unique(comp):
            if int(cid) + base in moving:
                continue
            keep.append(verts[comp == cid])
        base += 100000
    return np.concatenate(keep) if keep else np.zeros((0, 3))


def validate(planned, static_pts, level_lo, level_hi):
    errs = []
    for p in planned:
        if (p["S"] < SCALE_MIN - 1e-6).any() or (p["S"] > SCALE_MAX + 1e-6).any():
            errs.append("%-15s scale %s is outside the %.2f-%.2f budget"
                        % (p["name"], np.round(p["S"], 3), SCALE_MIN, SCALE_MAX))
        for ax, nm in enumerate("xyz"):
            new = max(level_lo[ax] - p["new_lo"][ax], p["new_hi"][ax] - level_hi[ax], 0.0)
            old = max(level_lo[ax] - p["lo"][ax], p["hi"][ax] - level_hi[ax], 0.0)
            if new > old + 1e-3:
                errs.append("%-15s at %s pushed %.2f m further out of the level in %s"
                            % (p["name"], np.round(p["target"], 1), new - old, nm))
    for i, a in enumerate(planned):
        for b in planned[i + 1:]:
            if (a["new_lo"] <= b["new_hi"]).all() and (a["new_hi"] >= b["new_lo"]).all():
                errs.append("%-15s overlaps %-15s at %s"
                            % (a["name"], b["name"], np.round(a["target"], 1)))
    if len(static_pts):
        for p in planned:
            hit = ((static_pts >= p["new_lo"]) & (static_pts <= p["new_hi"])).all(1).sum()
            was = ((static_pts >= p["lo"]) & (static_pts <= p["hi"])).all(1).sum()
            if hit > was:
                errs.append("%-15s at %s contains %d static collision vertices "
                            "(stock position had %d)"
                            % (p["name"], np.round(p["target"], 1), hit, was))
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--dir", default=str(EXTRACT))
    ap.add_argument("--layout", default=str(LAYOUT))
    ap.add_argument("--clusters", required=True)
    ap.add_argument("--components", required=True)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    layout = json.loads(Path(a.layout).read_text())
    clusters = json.loads(Path(a.clusters).read_text())
    root = Path(a.dir)
    src_hex = clone_mod.rad_hash(SRC_NAME)
    dst_hex = clone_mod.rad_hash(DST_NAME)

    lv = geo.Level(root, src_hex)
    planned, errs = plan(layout, clusters)
    if errs:
        print("LAYOUT DOES NOT MATCH THE OBJECT TABLE -- %d problem(s):" % len(errs))
        for e in errs[:12]:
            print("   " + e)
        return 1
    pts = static_points(lv, clusters)
    allv = np.concatenate(lv.pverts)
    errs = validate(planned, pts, allv.min(0), allv.max(0))
    print("planned  : %d clusters over %d objects"
          % (len(planned), len({p["name"] for p in planned})))
    if errs:
        print("\nLAYOUT REJECTED -- %d problem(s):" % len(errs))
        for e in errs[:24]:
            print("   " + e)
        return 1
    print("validated: scale budget, level bounds, self-overlap, static-overlap all clear")
    if a.dry_run:
        return 0

    out = Path(a.out)
    print("\ncloning %s -> %s" % (SRC_NAME, DST_NAME))
    clone_mod.clone(SRC_NAME, DST_NAME, root, out)

    print("\ntransforming geometry")
    tot = {"instances": 0, "phys_components": 0, "bvh_triangles": 0,
           "phys_straddled": 0, "lights": 0}
    short = []
    for p in planned:
        box = (p["lo"] - BIND_SLACK, p["hi"] + BIND_SLACK)
        r = lv.transform(box=box, entities=p["cluster"]["entities"],
                         delta=p["target"] - p["pivot"], rot=p["R"], scale=p["S"],
                         pivot=p["pivot"], label=p["name"])
        for k in tot:
            tot[k] += r[k]
        if r["phys_components"] != len(p["cluster"]["phys"]):
            short.append("%-15s moved %d of its %d collision components"
                         % (p["name"], r["phys_components"], len(p["cluster"]["phys"])))
        if r["instances"] != len(p["cluster"]["entities"]):
            short.append("%-15s moved %d of its %d instances"
                         % (p["name"], r["instances"], len(p["cluster"]["entities"])))
    if short:
        print("  ABORT -- geometry left behind:")
        for m in short[:20]:
            print("     " + m)
        return 1
    lv.write(out, dst_hex)
    print("  instances transformed  : %d" % tot["instances"])
    print("  collision components   : %d" % tot["phys_components"])
    print("  raycast triangles      : %d" % tot["bvh_triangles"])
    print("  scene lights moved     : %d" % tot["lights"])
    print("  bodies rebuilt non-rigidly : %s" % sorted(lv.nonrigid))
    print("\n-> %s   (level %s = %s)" % (out, DST_NAME, dst_hex))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
