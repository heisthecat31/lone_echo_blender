"""The goal-explosion set for a level, resolved against a written package.

## What this is

When a team scores, the goal they scored into fires a burst: two shells at the
goal mouth plus a run of ring props down the arena. `mpl_arena_a` ships them as
ordinary `eMTForwardTransparent` models with white textures, no accent tint, no
mover constraint and no script of their own, so the extractor placed them
static, white and always visible.

## What is authored and what is not

**Authored, and read here:**

* **the ends** -- the `burst` models occur at exactly two transforms in the
  level and nowhere else, so those transforms ARE the goal mouths;
* **each end's team colour** -- the accent tint carried by the most instances
  near it. `mpl_arena_a` reads `(1.0, 0.264, 0.059)` on 225 instances at +z and
  `(0.0, 0.569, 1.0)` on 225 at -z, which is the orange end and the blue end;
* **where every ring sits** -- the shockwave props are placed along the arena
  at +-16 and +-32, so the wave's PATH is authored even though its schedule is
  not;
* **which end an instance serves** -- by position, and for the pair sitting on
  the arena origin by rotation, since the burst at +z is placed with a 180-deg
  turn about Y and the one at -z with identity.

⚠ **NOT authored: which props these are.** That is game-code knowledge and it
comes from `data/evr_goal_explosion.json`, which records who supplied it. Every
structural rule tried failed to separate them from the permanent goal ring:
they share `mattype` (all transparent), they share material hashes with the
ring, the actor chain from the `mp_arena_goal` script actor sweeps up 58 actors
and half the level's models, and neither carries a lightmap.

⚠ **NOT authored: the motion or the timing.** Nothing in the level says when
the burst fires, how the beams fan, or how long it runs. That comes from
`data/evr_goal_animation.json` -- the goal clip out of the user's Unity demo
viewer, which is a RECONSTRUCTION, not shipped data. It is embedded in this
sidecar under `animation` so the addon can reach it from an install directory
with no view of this repo, and every object it touches says where it came from.

## The colour rule

A goal flashes the colour of the team that scored INTO it, so each end takes
the OTHER end's team tint -- the orange goal explodes blue. Both values are
authored in the package; only the swap comes from outside it.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

FORMAT = "evr_goal_explosion"
DATA_NAME = "evr_goal_explosion.json"
#: The reference motion, carried INTO the package. The addon runs from an
#: install directory with no view of this repo, so a sidecar that needs a
#: second file beside it would silently do nothing there.
CLIP_NAME = "evr_goal_animation.json"

#: `instances.bin` record: mesh_index u32 then 10 f32 (pos, quat xyzw, scale).
INSTANCE_STRUCT = "<I10f"
INSTANCE_STRIDE = struct.calcsize(INSTANCE_STRUCT)

#: Two placements count as the same goal mouth within this many units.
END_TOLERANCE = 1.0

#: An instance this far from the origin is assigned to an end by POSITION;
#: closer than this, by rotation (the pair that sits on the arena centre).
ORIGIN_RADIUS = 1.0


def curated(level_hash: str, data_dir: Path | None = None) -> dict | None:
    """The `burst`/`shockwave` model lists for a level, or None.

    Returns None -- not an empty set -- for a level nobody has identified, so a
    caller can tell "no goal explosion here" from "this level has none".
    """
    root = Path(data_dir) if data_dir else Path(__file__).resolve().parent.parent / "data"
    path = root / DATA_NAME
    if not path.is_file():
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if doc.get("format") != FORMAT:
        return None
    key = str(level_hash).lower().rjust(16, "0")
    return (doc.get("levels") or {}).get(key)


def _instances(pkg: Path, count: int):
    data = (pkg / "blobs" / "instances.bin").read_bytes()
    for i in range(min(count, len(data) // INSTANCE_STRIDE)):
        v = struct.unpack_from(INSTANCE_STRUCT, data, i * INSTANCE_STRIDE)
        yield i, v[0], v[1:4], v[4:8]


def _duplicates(pkg: Path, count: int) -> list:
    path = pkg / "blobs" / "instance_duplicate.bin"
    if not path.is_file():
        return [False] * count
    data = path.read_bytes()
    if len(data) < count:
        return [False] * count
    return [bool(data[i]) for i in range(count)]


def _near(a, b, tol=END_TOLERANCE) -> bool:
    return all(abs(a[k] - b[k]) <= tol for k in range(3))


def _dominant_tint(instances, meshes, tints, end):
    """The accent tint the most instances near `end` carry, or None."""
    votes: dict = {}
    for _i, mesh_index, pos, _rot in instances:
        if mesh_index >= len(meshes):
            continue
        tint = tints.get(meshes[mesh_index].get("matidx"))
        if not tint:
            continue
        # Nearest end wins; `end` is one of two, so a sign test on the long
        # axis is enough and is robust to the ends not being exactly mirrored.
        axis = max(range(3), key=lambda k: abs(end[k]))
        if (pos[axis] >= 0) != (end[axis] >= 0):
            continue
        key = tuple(round(float(c), 4) for c in tint[:3])
        votes[key] = votes.get(key, 0) + 1
    if not votes:
        return None, 0
    best = max(votes.items(), key=lambda kv: kv[1])
    return list(best[0]), best[1]


def resolve(pkg: Path, level_hash: str, data_dir: Path | None = None) -> dict | None:
    """The `goal_explosion.json` document for a written package, or None."""
    pkg = Path(pkg)
    entry = curated(level_hash, data_dir)
    if not entry:
        return None
    try:
        manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        materials = json.loads((pkg / "materials.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    meshes = manifest.get("meshes") or []
    count = int(manifest.get("num_instances") or 0)
    if not meshes or not count:
        return None

    tints = {}
    for rec in materials.get("materials") or ():
        tint = (rec.get("spec") or {}).get("accent_tint")
        if tint:
            tints[rec.get("matidx")] = tint

    burst = {h.lower() for h in entry.get("burst") or ()}
    shock = {h.lower() for h in entry.get("shockwave") or ()}
    dups = _duplicates(pkg, count)
    live = [rec for rec in _instances(pkg, count) if not dups[rec[0]]]

    # ── the ends: every distinct transform a BURST model is placed at ──
    ends: list = []
    for _i, mesh_index, pos, rot in live:
        if mesh_index >= len(meshes):
            continue
        if str(meshes[mesh_index].get("name_hash", "")).lower() not in burst:
            continue
        for end in ends:
            if _near(end["position"], pos):
                break
        else:
            ends.append({"position": [round(float(c), 4) for c in pos],
                         "rotation": [round(float(c), 4) for c in rot],
                         "burst": [], "shockwave": []})
    if len(ends) < 2:
        return None                       # not a two-goal level: say nothing

    for end in ends:
        tint, votes = _dominant_tint(live, meshes, tints, end["position"])
        end["team_tint"] = tint
        end["team_tint_votes"] = votes

    # A goal flashes the colour of the team that scored INTO it.
    for a, b in ((0, 1), (1, 0)):
        ends[a]["explosion_tint"] = ends[b].get("team_tint")

    def assign(pos, rot):
        """The end an instance serves: by position, or by rotation at the origin."""
        if sum(c * c for c in pos) > ORIGIN_RADIUS ** 2:
            return min(range(len(ends)), key=lambda k: sum(
                (pos[j] - ends[k]["position"][j]) ** 2 for j in range(3)))
        return min(range(len(ends)), key=lambda k: sum(
            (rot[j] - ends[k]["rotation"][j]) ** 2 for j in range(4)))

    for i, mesh_index, pos, rot in live:
        if mesh_index >= len(meshes):
            continue
        model = str(meshes[mesh_index].get("name_hash", "")).lower()
        if model in burst:
            key, k = "burst", assign(pos, rot)
        elif model in shock:
            key, k = "shockwave", assign(pos, rot)
        else:
            continue
        # Distance from the mouth orders the wave; the schedule is the addon's.
        reach = sum((pos[j] - ends[k]["position"][j]) ** 2 for j in range(3)) ** 0.5
        ends[k][key].append({"instance": i, "model": model,
                             "distance_from_goal": round(reach, 3)})

    for end in ends:
        end["shockwave"].sort(key=lambda r: r["distance_from_goal"])
        end.pop("rotation", None)
    clip = None
    clip_path = (Path(data_dir) if data_dir else
                 Path(__file__).resolve().parent.parent / "data") / CLIP_NAME
    try:
        clip = json.loads(clip_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        clip = None
    return {
        "format": FORMAT,
        "version": 1,
        "animation": clip,
        "note": ("Goal-explosion props. MEMBERSHIP is user-supplied (see "
                 "data/evr_goal_explosion.json); the ends, the team colours "
                 "and the per-end assignment are read from this package. "
                 "TIMING IS NOT AUTHORED -- the addon keyframes a placeholder."),
        "level": str(level_hash),
        "source": entry.get("source", ""),
        "ends": ends,
    }
