"""Resolve the arena's named obstacle field into rigid clusters with collision.

    python scripts/evr_arena_objects.py --out <clusters.json>

Reads `data/arena_objects.json` (the names, seeded by Blender instance index)
and produces, for every named object, the full set of mirror copies, grouped
into rigid clusters, each bound to the collision components it owns.

HOW A NAME BECOMES A CLUSTER
----------------------------
1. `lescatter_576ed3f8428ebc4b_i<N>` indexes `blobs/instances.bin`;
   `static_entities.json` maps that index to the level's static-instance
   ENTITY, which is the join key into `CTransformCR`.
2. Mirror copies are found at `(+-x, y, +-z)` among instances built from the
   same mesh or from that mesh's opposite-half twin -- the cook ships a separate
   model per half of the level (`3ebd34be987debab` for `z > 0`,
   `1bc9ca79ca8870f7` for `z < 0`, 51 instances each), paired here by matching
   two meshes whose instance sets are exact z-mirrors.
3. Copies the mirror search cannot reach are listed explicitly as `extra`.
   Station is the one that needs it: its left pair sits at `(6.67, 0, 8.33)`
   against the right pair's `(5.83, 0, 10.83)`, so it is not a reflection of
   anything and an automatic search silently returns half the object.
4. Instances are grouped into rigid clusters by AABB overlap, because several
   models make one object (pacman is 2 pyramids, popcorn 4, spawn_tri 3).
5. Each cluster is bound to the collision components that lie entirely inside
   its render AABB plus `BIND_SLACK`.

The `pieces` field in the data file is the expected number of models per
cluster; a mismatch means the mirror search under- or over-reached and is
reported rather than silently accepted.
"""
from __future__ import annotations

import argparse
import collections
import json
import struct
import sys
from pathlib import Path

import numpy as np

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import evr_geo_move as geo                     # noqa: E402

PACKAGE = Path(r"J:\EchoVRModels_half\scenes\mpl_arena_a")
EXTRACT = Path(r"H:\pcvr-extracted")
LEVEL = "576ed3f8428ebc4b"
SPEC = _SCRIPTS.parent / "data" / "arena_objects.json"
BIND_SLACK = 0.30
CLUSTER_GAP = 0.15


def _quat_matrix(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def load_instances(package: Path, bounds: dict):
    man = json.loads((package / "manifest.json").read_text())
    blob = (package / "blobs" / "instances.bin").read_bytes()
    ents = json.loads((package / "static_entities.json").read_text())["instances"]
    mesh_of = {m["index"]: m["name_hash"] for m in man["meshes"]}
    by_ent: dict[str, dict] = {}
    idx_ent: dict[int, str] = {}
    for i in range(man["num_instances"]):
        e = ents[i]
        if not e:
            continue
        r = struct.unpack_from("<I10f", blob, i * 44)
        d = by_ent.setdefault(e[0], {"entity": e[0], "pos": list(r[1:4]),
                                     "rot": list(r[4:8]), "scale": list(r[8:11]),
                                     "meshes": set(), "idx": []})
        d["meshes"].add(mesh_of.get(r[0]))
        d["idx"].append(i)
        idx_ent[i] = e[0]
    for d in by_ent.values():
        lo = hi = None
        for mh in d["meshes"]:
            b = bounds.get(mh)
            if b is None:
                continue
            l, h = np.array(b["min"]), np.array(b["max"])
            lo = l if lo is None else np.minimum(lo, l)
            hi = h if hi is None else np.maximum(hi, h)
        if lo is None:
            d["aabb"] = None
            continue
        corners = np.array([[lo[0] if c & 1 else hi[0], lo[1] if c & 2 else hi[1],
                             lo[2] if c & 4 else hi[2]] for c in range(8)])
        w = (_quat_matrix(d["rot"]) @ (corners * np.array(d["scale"])).T).T + np.array(d["pos"])
        d["aabb"] = (w.min(0), w.max(0))
    return by_ent, idx_ent


def mirror_pairs(by_ent):
    """Mesh signature -> the signature holding its z-mirrored instance set."""
    bysig = collections.defaultdict(list)
    for d in by_ent.values():
        bysig[frozenset(d["meshes"])].append(d)
    pos = {s: np.array([d["pos"] for d in v]) for s, v in bysig.items()}
    pair = {}
    for s, P in pos.items():
        for t, Q in pos.items():
            if t is s or len(Q) != len(P):
                continue
            if abs(P[:, 2].min() + Q[:, 2].max()) < 0.05 and abs(P[:, 2].max() + Q[:, 2].min()) < 0.05:
                pair[s] = t
    return pair


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--package", default=str(PACKAGE))
    ap.add_argument("--dir", default=str(EXTRACT))
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--bounds", required=True, help="arena_model_bounds.json")
    ap.add_argument("--out", required=True)
    ap.add_argument("--components-out", default=None)
    a = ap.parse_args(argv)

    bounds = json.loads(Path(a.bounds).read_text())
    spec = json.loads(Path(a.spec).read_text())["objects"]
    by_ent, idx_ent = load_instances(Path(a.package), bounds)
    pair = mirror_pairs(by_ent)

    E = list(by_ent.values())
    POS = np.array([d["pos"] for d in E])
    SIG = [frozenset(d["meshes"]) for d in E]
    at = {d["entity"]: i for i, d in enumerate(E)}

    def partners(i, tol=0.06):
        p = POS[i]
        want = {SIG[i]} | ({pair[SIG[i]]} if SIG[i] in pair else set())
        out = {i}
        for sx in (1, -1):
            for sz in (1, -1):
                t = np.array([p[0] * sx, p[1], p[2] * sz])
                for j in np.nonzero(np.abs(POS - t).max(1) < tol)[0]:
                    if SIG[j] in want:
                        out.add(int(j))
        return out

    # collision decomposition, from the level itself
    lv = geo.Level(Path(a.dir), LEVEL)
    comps = []
    base = 0
    for verts, comp in zip(lv.pverts, lv.pcomp):
        for cid in np.unique(comp):
            sel = comp == cid
            seg = verts[sel]
            comps.append({"cid": int(cid) + base, "min": seg.min(0).tolist(),
                          "max": seg.max(0).tolist(), "verts": int(sel.sum())})
        base += 100000
    CM = np.array([c["min"] for c in comps])
    CX = np.array([c["max"] for c in comps])

    out = {}
    problems = []
    print("%-16s %5s %8s %8s  %s" % ("object", "inst", "clusters", "physC", "cluster centres"))
    for name, sp in spec.items():
        seeds = set()
        for n in sp["seed"]:
            e = idx_ent.get(n)
            if e is None:
                problems.append("%s: instance i%d is not a static instance" % (name, n))
                continue
            seeds |= partners(at[e])
        for n in sp.get("extra", []):
            e = idx_ent.get(n)
            if e is None:
                problems.append("%s: extra i%d is not a static instance" % (name, n))
                continue
            seeds |= partners(at[e])
        members = sorted(seeds)
        MN = np.array([E[i]["aabb"][0] for i in members])
        MX = np.array([E[i]["aabb"][1] for i in members])
        k = len(members)
        par = list(range(k))

        def find(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x

        for i in range(k):
            for j in range(i + 1, k):
                if (MN[i] <= MX[j] + CLUSTER_GAP).all() and (MX[i] >= MN[j] - CLUSTER_GAP).all():
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        par[rj] = ri
        groups = collections.defaultdict(list)
        for i in range(k):
            groups[find(i)].append(i)
        clusters = []
        for g in groups.values():
            lo, hi = MN[g].min(0), MX[g].max(0)
            sel = np.nonzero((CM >= lo - BIND_SLACK).all(1) & (CX <= hi + BIND_SLACK).all(1))[0]
            clusters.append({"entities": [E[members[i]]["entity"] for i in g],
                             "min": lo.tolist(), "max": hi.tolist(),
                             "phys": [comps[j]["cid"] for j in sel],
                             "pieces": len(g)})
            if len(g) != sp["pieces"]:
                problems.append("%s: a cluster has %d models, expected %d"
                                % (name, len(g), sp["pieces"]))
            if not sel.size:
                problems.append("%s: a cluster at %s has NO collision bound"
                                % (name, np.round((lo + hi) / 2, 1)))
        clusters.sort(key=lambda c: (-c["max"][2], c["min"][0]))
        out[name] = clusters
        ctr = [tuple(round((c["max"][i] + c["min"][i]) / 2, 1) for i in range(3)) for c in clusters]
        print("%-16s %5d %8d %8d  %s" % (name, k, len(clusters),
                                         sum(len(c["phys"]) for c in clusters),
                                         " ".join(map(str, ctr[:4])) + (" ..." if len(ctr) > 4 else "")))
    Path(a.out).write_text(json.dumps(out, indent=1))
    if a.components_out:
        Path(a.components_out).write_text(json.dumps(comps, indent=1))
    print("\nobjects %d, clusters %d, instances %d"
          % (len(out), sum(len(v) for v in out.values()),
             len({e for v in out.values() for c in v for e in c["entities"]})))
    if problems:
        print("\nPROBLEMS (%d):" % len(problems))
        for p in problems[:20]:
            print("   " + p)
        return 1
    print("every cluster has the expected model count and bound collision.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
