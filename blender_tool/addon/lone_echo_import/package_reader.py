"""Standalone .lemesh reader for the Blender addon.

No dependency on le_mesh / oodle — reads manifest.json + raw blobs directly so
the addon installs as a normal Blender add-on. Uses numpy (bundled in Blender)
for fast blob loads, with an `array` fallback.
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    import numpy as _np
except Exception:   # pragma: no cover - Blender always ships numpy
    _np = None

_NP_DTYPE = {"float32": "<f4", "uint32": "<u4", "int32": "<i4"}
_ARR_CODE = {"float32": "f", "uint32": "I", "int32": "i"}


class Package:
    def __init__(self, pkg_dir: Path):
        self.dir = Path(pkg_dir)
        self.manifest = json.loads((self.dir / "manifest.json").read_text(encoding="utf-8"))

    @property
    def objects(self):
        return self.manifest["objects"]

    @property
    def materials(self):
        return self.manifest.get("materials", [])

    @property
    def coordinate_system(self):
        return self.manifest.get("coordinate_system", "rad_engine")

    def load(self, rel_path: str, dtype: str):
        """Return a flat sequence (numpy array or array.array) for a blob."""
        data = (self.dir / rel_path).read_bytes()
        if _np is not None:
            return _np.frombuffer(data, dtype=_NP_DTYPE[dtype])
        from array import array
        a = array(_ARR_CODE[dtype])
        a.frombytes(data)
        return a

    def attribute(self, obj: dict, key: str):
        """Return (flat_values, comps) for an attribute, or (None, 0)."""
        entry = obj.get("attributes", {}).get(key)
        if not entry or "blob" not in entry:
            return None, 0
        return self.load(entry["blob"], entry["dtype"]), entry["comps"]

    def indices(self, obj: dict):
        entry = obj.get("index")
        if not entry:
            return None
        return self.load(entry["blob"], entry["dtype"])


def drop_scene_set_variant_draws(draws):
    """Collapse draws that cover the IDENTICAL index range under DIFFERENT scene sets.

    ★ 2026-08-05, measured. Eight of Jack's fourteen meshes
    (`c6bc8607972268c9 / 64b4b5b2a0153f7e`, obj006..obj013) carry **two**
    `CGRenderParams` over byte-identical `(idxstart, idxcount)` with different
    `shadersetidx` / `materialidx`, and scene masks that differ only in ONE bit —
    `0x11`/`0x21` and `0x12`/`0x22`, both with `SSceneSetMask.mincount == 2`
    (`stream-confirmed`). `ced8e54800890f2d` is the same shape on 6 of 12 draws
    (`0x9`/`0x11`, `0xa`/`0x12`, `0xc`/`0x14`).

    `mincount == 2` means the draw fires only when BOTH of its sets are active,
    and the two rows differ in exactly one of them — so they are alternatives: a
    material/skin VARIANT of the same triangles. The importer used to emit both,
    which put 145,122 duplicate indices (48,374 co-planar triangles) on the
    PLAYER's own avatar and 49,644 on `ced8e54800890f2d`, z-fighting two
    different materials against each other.

    ⛔ This is NOT an LOD rule and makes no claim about which variant ships: it
    keeps the FIRST row in renderparam order (deterministic) and records the ones
    it dropped in `draw["le_variant_dropped"]` so the alternative material is
    never lost, only unbuilt.

    A draw missing `idx_start` / `idx_count` / `scene_mask` (every package written
    before those keys existed) cannot be classified and is always kept.
    """
    out, first = [], {}
    for d in draws:
        start, count, mask = d.get("idx_start"), d.get("idx_count"), d.get("scene_mask")
        if start is None or count is None or mask is None:
            out.append(d)
            continue
        key = (int(start), int(count))
        prev = first.get(key)
        if prev is None:
            first[key] = (d, int(mask))
            out.append(d)
            continue
        kept, kept_mask = prev
        if int(mask) == kept_mask:
            out.append(d)               # same set: not a variant, keep both
            continue
        dropped = list(kept.get("le_variant_dropped") or [])
        dropped.append({"renderparam_index": d.get("renderparam_index"),
                        "material_key": d.get("material_key", ""),
                        "shaderset_index": d.get("shaderset_index"),
                        "scene_mask": int(mask)})
        kept["le_variant_dropped"] = dropped
    return out


def snap_to_ladder(level, present):
    """The rung of `present` (a set/list of LOD levels) that `level` selects.

    ★ THE LADDER RULE, in one place, because getting it wrong deletes the model.
    A ladder is neither dense nor zero-based on disk, and both facts have already
    cost a defect apiece:

      * **D2** — `3cee9f282bf0807f` partitions its gated meshes into levels
        `{3, 4}`, so the DEFAULT `level = 0` asked for a rung nothing carries and
        the importer produced nothing (`min(level, max)` alone, no floor).
      * **D13** (was numbered D9 in `docs/TESTING.md` §3.1)
        — `2fd6839161785e9c_ff91757c910ea7b6` (Liv's body) partitions its six
        meshes into `{0, 3}`, so levels 1 and 2 fall in a HOLE *between* the
        rungs, which the floor/ceiling clamp `min(max(level, min), max)` does not
        cover. Asking for LOD 1 imported **nothing at all**.

    ⇒ **snap DOWN to the greatest present level `<= level`, and only when the
    request is below the whole ladder snap UP to its finest rung.** That single
    expression subsumes the old floor and ceiling, so there is one rule, not
    three.

    ★ WHY DOWN (the nearest FINER rung), not up and not "draw everything":

      1. **It is the direction the module already commits to.**
         `scene_lod_is_geometric_chain`'s refusal is justified as *"over-draw is
         visible and reversible, a missing limb is silent"*. A finer rung is more
         geometry, i.e. the same bias: the failure mode is a model heavier than
         asked for, never a model that is not there.
      2. **It is what a threshold ladder does.** `ComponentLOD` switches rungs at
         distance thresholds; between two rungs the one still on screen is the
         finer one already selected. Snapping up would show LOD 3 to a viewer who
         asked for LOD 1 — a *coarser* model than requested, which no ladder
         semantics produces.
      3. **It stays monotone.** Selected detail never increases as `level` rises,
         so an LOD sweep still reads as a ladder rather than a sawtooth.

    ⛔ Refusing (returning every object, the `scene_lod_is_geometric_chain`
    response) was the third option and is rejected here: that refusal exists for
    a partition whose *meaning* is in doubt, and a hole in a ladder casts no
    doubt on the rungs that are present. Stacking all six of Liv's meshes to
    answer "LOD 1" would re-create the stacked-proxy defect — LOD 3 drawn on
    top of LOD 0 — which is a rendering error, where snapping down is merely a
    rounding.

    `present` must be non-empty; the callers all check.
    """
    below = [x for x in present if x <= level]
    return max(below) if below else min(present)


def select_lod_draws(draws, level):
    """The subset of a mesh's draws to emit for LOD `level`, clamped per mesh.

    A mesh's coarser LODs are extra draws covering LATER slices of the SAME index
    buffer (the mesh-list LOD chain — see `le_mesh.meshlist.assign_lod_levels`),
    so emitting every draw stacks the levels on top of each other. Selection is
    per mesh and clamped: a mesh whose chain stops at level 1 still emits its
    level 1 when level 3 is asked for, and a mesh with no chain at all (every draw
    level 0 — the case for all but 11 of the corpus's 1,240 mesh-lists) is
    returned unchanged.

    Scene-set VARIANT rows are collapsed first — see
    `drop_scene_set_variant_draws`. That is independent of the LOD chain and
    applies to every mesh.

    `level < 0` keeps every draw (all levels stacked, variants included — the
    pre-LOD behaviour, kept so the defect is reproducible for an A/B).
    A package written before the `lod.level` key existed reads as all-level-0, so
    it also passes through untouched.

    Pure / bpy-free so it is unit tested outside Blender.
    """
    if level is None or level < 0:
        return list(draws)
    draws = drop_scene_set_variant_draws(draws)
    levels = [int((d.get("lod") or {}).get("level", 0) or 0) for d in draws]
    if not levels or max(levels) == 0:
        return list(draws)
    # ⚠ ONE ladder rule, shared with `select_lod_objects` — see `snap_to_ladder`.
    # No mesh-list chain on disk is sparse or non-zero-based today (container:
    # `blender_tool/exports`, coverage: 301 manifests / 913 objects, 0 sparse and
    # 0 non-zero-based draw ladders, measured 2026-08-05), so this is a no-op on
    # every package present. It is here so the D2/D13 hole cannot land twice in
    # two modules that clamp the same thing.
    want = snap_to_ladder(level, set(levels))
    return [d for d, lv in zip(draws, levels) if lv == want]


#: A scene-set level is accepted as an LOD of the finest level only when BOTH
#: (a) its union AABB is at least this fraction of the finest level's union AABB
#: by volume, and (b) at least this fraction of its own volume lies inside the
#: finest level's union AABB.
#:
#: ★ MEASURED SEPARATION, not a taste. Over the 12 character mesh-lists of
#: the project notes §2 (a local working file,
#: `stream-confirmed` 2026-08-05) `min(volume_ratio, coverage)` is
#:
#:   accepted (a real LOD chain)   >= 0.845   liv_head 0.845/1.000 ·
#:       liv body 0.980/1.000 · liv helmet 0.998/0.995 · 3cee9f28 0.990/0.998 ·
#:       32a230d8 1.177/0.845 · 001e3b0be 0.991/1.000
#:   refused  (NOT an LOD chain)   <= 0.233   64b4b5b2 (Jack) 1.000/0.000 ·
#:       7e8663a5 (Jack FP) 0.040/0.797 · e2e02718 0.938/0.233 ·
#:       ced8e548 0.946/0.232
#:
#: so 0.5 sits in a 0.233..0.845 gap with no borderline case on either side.
SCENE_LOD_COLOCATION_MIN = 0.5


def _aabb(obj):
    lo, hi = obj.get("aabb_min"), obj.get("aabb_max")
    if not lo or not hi or len(lo) < 3 or len(hi) < 3:
        return None
    return (float(lo[0]), float(lo[1]), float(lo[2]),
            float(hi[0]), float(hi[1]), float(hi[2]))


def _aabb_volume(b):
    return (max(0.0, b[3] - b[0]) * max(0.0, b[4] - b[1]) * max(0.0, b[5] - b[2]))


def _aabb_union(boxes):
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), min(b[2] for b in boxes),
            max(b[3] for b in boxes), max(b[4] for b in boxes), max(b[5] for b in boxes))


def _aabb_intersection(a, b):
    return (max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2]),
            min(a[3], b[3]), min(a[4], b[4]), min(a[5], b[5]))


def object_is_scene_set_ungated(obj) -> bool:
    """True when EVERY draw of this object carries `scene_mask == 0`.

    ⛔ Such a mesh is not in any scene set, so no `ComponentLOD` can hide it: it
    always draws. `le_mesh.meshlist.scene_set_lod_levels` records it as level 0
    (`min(live) if live else 0`), which is indistinguishable from a genuine
    level-0 member once the manifest is written — so the ungated state is
    recovered here from the draws themselves.

    Four of Jack's fourteen meshes are in this state (`64b4b5b2a0153f7e` obj002
    -obj005, 14,424 v incl. his whole lower body), and one of Jack's FP body's
    seven (`7e8663a5dfe52104` obj003, 48,450 v — the largest mesh in the asset).
    Before this they vanished at every level >= 1.

    Returns False when no draw records a `scene_mask` at all (a package written
    before that key existed), so old packages keep their previous behaviour.
    """
    draws = obj.get("draws") or []
    masks = [d.get("scene_mask") for d in draws]
    known = [m for m in masks if m is not None]
    if not known:
        return False
    return all(int(m) == 0 for m in known)


def scene_lod_is_geometric_chain(objects, tol: float = SCENE_LOD_COLOCATION_MIN):
    """Is this package's scene-set partition really an LOD CHAIN? `(bool, diag)`.

    ⛔ THE PREMISE THIS GUARDS. `le_mesh.meshlist.RP_SCENEMASK`'s comment reads
    "vertex counts fall monotonically with bit index on every character checked",
    and on that basis the lowest set bit is taken as the LOD level. Checked
    against the WHOLE roster rather than Liv, that is false on 4 of 12 character
    mesh-lists — the bits there partition the body in SPACE, not in detail:

      * `64b4b5b2a0153f7e` (Jack, the player's own avatar): level 0's union AABB
        lies entirely in `x in [+0.05, +0.63]` and level 1's entirely in
        `x in [-0.63, -0.05]` — intersection volume **0** — at 18,440 vs 18,401
        vertices (0.2 % apart). A mirrored pair, not a detail reduction. Levels
        2 and 3 are the same story one limb out (8,904 / 8,888 v).
      * `7e8663a5dfe52104` (Jack's FP body): levels 1 and 2 are 8,824 / 8,826 v
        at mirrored `x` — both hands.
      * `e2e027188d1c6e29`, `ced8e54800890f2d`: three DISJOINT bands of `y`
        (1.24-1.55, 1.08-1.33, 0.94-1.12) with vertex counts *rising* 4k -> 7k ->
        21k.

    Selecting "level 0" on those imports 2 of 6 meshes (84 % of the vertices
    gone) or a one-armed, handless Jack. ⇒ when the partition does not look like
    an LOD chain this refuses, and the caller draws everything — over-draw is
    visible and reversible, a missing limb is silent.

    The test is geometric because the authoritative source is not in the
    mesh-list: the bit -> set-NAME mapping lives in the model's own
    `CGSceneSetsData` / `CComponentLODCRWin7` (which is how `liv_head`'s
    `lod0/lod1/lod2` were confirmed). Reading that per model is the real fix and
    is filed as such; this is the guard until then.

    `(True, diag)` — never a refusal — when the objects carry no AABBs, so a
    synthetic or pre-AABB package behaves exactly as before.
    """
    gated, levels = [], {}
    for o in objects:
        lv = o.get("scene_lod_level")
        if lv is None or object_is_scene_set_ungated(o):
            continue
        box = _aabb(o)
        if box is None:
            return True, {"evaluated": False, "reason": "no aabb on a gated object"}
        gated.append(o)
        levels.setdefault(int(lv), []).append(box)
    if len(levels) < 2:
        return True, {"evaluated": False, "reason": "fewer than two gated levels"}
    base = min(levels)
    b0 = _aabb_union(levels[base])
    v0 = _aabb_volume(b0)
    if v0 <= 0.0:
        return True, {"evaluated": False, "reason": "finest level has zero volume"}
    per_level, worst = {}, 1.0
    for lv in sorted(levels):
        bk = _aabb_union(levels[lv])
        vk = _aabb_volume(bk)
        cover = (_aabb_volume(_aabb_intersection(bk, b0)) / vk) if vk > 0 else 0.0
        ratio = vk / v0
        score = 1.0 if lv == base else min(ratio, cover)
        per_level[lv] = {"meshes": len(levels[lv]), "volume_ratio": ratio,
                         "coverage": cover, "score": score}
        worst = min(worst, score)
    return worst >= tol, {"evaluated": True, "base_level": base,
                          "tolerance": tol, "worst_score": worst,
                          "levels": per_level}


def select_lod_objects(objects, level):
    """The subset of a package's OBJECTS to emit for LOD `level`.

    ★ The SECOND LOD system. A character's mesh-list ships every LOD as a
    SEPARATE MESH and selects between them with `CGRenderParams`' leading
    `SSceneSetMask`, driven by the actor's `ComponentLOD` component; there is no
    `lodchildindices` chain at all. `liv_head` is the case that exposed it: 19
    meshes, masks partitioning them 10 / 8 / 1, and the importer drew all three
    levels at once, so the face z-fought against its own LOD 1
    (`exports/hero/v2_liv_head_bust.png`).

    Selection is by the manifest's `scene_lod_level`, written by
    `le_mesh.meshlist.scene_set_lod_levels`:

      * `None` on every object (or the key absent — every package written before
        2026-08-05) -> unchanged, so a level mesh-list and every older package
        import exactly as before;
      * otherwise keep the rung `snap_to_ladder` selects — the greatest gated
        level `<= level`, or the finest gated level when the request is below the
        whole ladder. That is the same rule `select_lod_draws` uses, and it is
        never empty: a mesh-list whose coarsest set is 1 still yields something at
        level 3, one whose finest set is 3 still yields something at level 0, and
        one whose ladder is {0, 3} yields its level 0 at levels 1 and 2 instead of
        yielding nothing (D13).

    `level < 0` keeps every object (all levels stacked — the pre-fix behaviour,
    kept so the z-fight is reproducible for an A/B).

    Pure / bpy-free so it is unit tested outside Blender.
    """
    if level is None or level < 0:
        return list(objects)
    lv = [o.get("scene_lod_level") for o in objects]
    known = [x for x in lv if x is not None]
    if not known or max(known) == 0:
        return list(objects)
    ok, _diag = scene_lod_is_geometric_chain(objects)
    if not ok:
        # ⛔ Not an LOD chain — the bits partition the BODY, not its detail.
        # See `scene_lod_is_geometric_chain`. Draw everything: over-draw is
        # visible and reversible, a missing limb is silent.
        return list(objects)
    ungated = [object_is_scene_set_ungated(o) for o in objects]
    gated = [x for x, u in zip(lv, ungated) if x is not None and not u]
    if not gated or max(gated) == 0:
        return list(objects)
    # ⚠ SNAP INTO THE LADDER, do not clamp to its interval. A floor and a ceiling
    # (`min(max(level, min(gated)), max(gated))`) fixed D2 — the finest set is not
    # always bit 0, `3cee9f282bf0807f` starts at 3 — but left D13: a HOLE between
    # two rungs still lands on a level nothing carries. `2fd6839161785e9c_
    # ff91757c910ea7b6` (Liv's body) is levels {0, 3}, and levels 1 and 2 imported
    # NOTHING AT ALL (`stream-confirmed`). `snap_to_ladder` is the whole rule and
    # states why it snaps to the nearest FINER rung.
    want = snap_to_ladder(level, set(gated))
    # An object with no recorded level, or one no scene set gates, always draws.
    return [o for o, x, u in zip(objects, lv, ungated) if u or x is None or x == want]


def resolve_package_file(path, wanted: str = "manifest.json"):
    """The `wanted` file for whatever the user actually picked.

    Blender's `filter_glob` can only filter by EXTENSION, so an import file
    browser pointed at a package shows every sidecar beside the manifest --
    `materials.json`, `lightmaps.json`, `movers.json`, `static_entities.json`
    -- and picking the wrong one is easy and annoying.

    Rather than fight the file browser, accept any of them: a package is a
    DIRECTORY, so anything inside it identifies the package unambiguously.
    Returns the resolved path, or the original when nothing better exists (the
    caller then reports its own error).
    """
    from pathlib import Path as _Path

    p = _Path(path)
    if p.is_dir():
        candidate = p / wanted
        return str(candidate) if candidate.is_file() else str(p)
    if p.name == wanted:
        return str(p)
    candidate = p.parent / wanted
    return str(candidate) if candidate.is_file() else str(p)
