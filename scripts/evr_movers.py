"""Level geometry that MOVES -- from FOUR different components.

## What this is, and what it is not

Movement comes from four places, and a level may use any of them:

* `CR15LinearPositionConstraintCR` -- the R15 constraint system. Used by
  `mpl_combat_fission` (17 movers over 45 instances).
* `CPlatformCR` -- the platform component. Used by `mpl_arena_a`, which has NO
  R15 constraints at all, which is why scanning only for those reported the
  arena as having no movers while its launchers and tunnel mouths visibly slide
  in game.
* `CR15PlatformCR` -- the R15 rewrite of that same component, and the ONLY
  straight-line source `mpl_combat_dyson` has. Four levels carry it.
* **SKELETAL** -- an actor listed in `CAnimationCR` whose model owns BOTH a
  `CSkeletonResource` and a `CAnimSetResource`. This one does not slide along a
  line at all: it deforms a rig. `mpl_combat_dyson`'s fire fixtures are the
  case that found it, and no straight-line source sees them: they do not
  translate at all.

⚠ A level having no `CR15LinearPositionConstraintCR` does NOT mean it is static.
Check all four. `mpl_combat_dyson` was reported as having no movers at all while
carrying six `CR15PlatformCR` platforms and two rigged fire fixtures.

## The `CAnimationCR` correction

An earlier version of this module said `CAnimationCR` was "NOT a third source",
on the evidence that its 216-byte payload records hold no curve, no asset
reference and no endpoints -- every field is a `0xFFFFFFFF` sentinel, a constant
`32`, or the class symbol `8459bc252c90f074`, byte-identical between records
apart from the actor id. That reading of the PAYLOAD still stands.

What was wrong was the conclusion. `CAnimationCR` marks WHICH actors animate,
and that is a usable half of the answer as soon as it is joined to the model:
the other half -- the rig and the animation inventory -- is in the model's own
`CSkeletonResource` and `CAnimSetResource`. The join is what makes it a source.

It is also a real filter rather than a restatement. On `mpl_arena_a` **48** of
48 marked actors fail it (none of their models has a skeleton), which is why the
arena still reports zero skeletal movers; on `mpl_combat_dyson` **242** of 311
pass, over 9 models.

⚠ Most of those 242 are pooled character/weapon rigs PARKED, not placed. What
says so is not that they sit far out -- `mpl_combat_dyson`'s own instances reach
+/-203 m, so distance proves nothing -- but that **240 of the 242 have z exactly
0.0** and lie on a regular half-metre lattice (x = 62.8..69.8, y = 15.0..18.5,
and a second line at y = 0). Authored furniture does not land on a lattice with
one coordinate identically zero. The two that do not are the fire fixtures, at
z = -9.70.

Callers that want level furniture rather than spawn stock should filter on that,
or simply on being placed as a package instance; this module reports what it
finds and does not decide it for them.

⛔ **The motion itself is still not decoded.** `CAnimSetResource` names the
animations and says where each one's channels start; the channels are
lossy-compressed fitted curves and `evr_animset` does not read them. A skeletal
mover therefore carries its rig and its animation LIST, and no keyframes.

## `CPlatformCR` layout (verified on `mpl_arena_a`, 99 records)

Two regions, found the same way as every other CR component -- by scanning for
u64s that are known actor nodeids and looking at the gaps:

    region A  0x128, stride 24, `count` entries   (the component index)
    region B  payload, stride 384, actor id at +0x00

        +0x000  u64      actor
        +0x158  f32[3]   endpoint A          <- a WORLD POSITION, not a delta
        +0x164  f32[3]   endpoint B
        +0x17c  f32      ~26.35, 4 distinct values (timing? not identified)

⭐ Unlike the R15 constraint -- where the travel is the vector between two
anchor ACTORS and the record itself holds no geometry -- `CPlatformCR` stores
both endpoints inline as world positions.

Verified: on all 99 records one of the two endpoints coincides with the actor's
own rest transform (99/99, tolerance 5 cm), which is what makes the pair
orderable into rest -> far end. Travel distances cluster at 2.0 m (40 records),
2.2 m (39) and 9.297 m (20); 79 records travel along X and 20 along Z. Records
come in PAIRS per actor -- an out leg and a return leg with A and B swapped --
so the same actor appears twice and the second one is redundant.

⚠ The TIMING is still not decoded, exactly as for the R15 path. `+0x17c` varies
over only four values and `+0x124` is a constant 6.0; neither has been tied to a
duration. Consumers should treat the emitted keyframe timing as a placeholder.

## `CR15LinearPositionConstraintCR` layout (verified, zero remainder on every file tried)

    header:  u32 table byte size @ +0x08
             records start at `len(file) - size`, which is 0x38
    record:  136 bytes
        +0x08  u64   the constrained actor
        +0x40  u64   anchor A
        +0x50  u64   class symbol (0xabfe651c8d260515 on every record seen)
        +0x58  u64   anchor B
        +0x74  f32   0.5    <- CONSTANT, not travel
        +0x78  f32   0.95   <- CONSTANT, not travel

⭐ **The travel is not in the record.** It is the vector between the two anchor
ACTORS named at +0x40 and +0x58; one of them sits on the constrained actor's
own transform (its rest pose) and the other is the far end. No axis and no
distance is stored anywhere in the record.

That is not a guess from one sample. `+0x74`/`+0x78` are byte-identical between
a platform that drops once (`mpl_combat_fission` i1713, 2.747 m down) and one
that oscillates (i2162, 2.5 m up), which is what rules them out as travel
parameters -- a single sample could not have shown that.

## What is still missing

⚠ **The trigger and the timing are NOT decoded.** *When* a mover fires, how
long it takes, and whether it returns live in `CScriptCR` (383 KB on
`mpl_combat_fission`) together with `CR15LinearConstraintTouchInteractCR` and
`CR15InteractOutputCR`. This module recovers the geometry of the motion --
where it starts and where it ends -- and nothing about its schedule. Consumers
should treat the emitted keyframe timing as a placeholder.
"""

from __future__ import annotations

import struct
from pathlib import Path

#: `CR15LinearPositionConstraintCRWin10`. No Win7 twin is known -- the R15
#: system postdates the Win7 builds, so a Win7 extract simply has no movers.
LINEAR_POSITION_CONSTRAINT = "68c32c04284fb022"

#: `CPlatformCRWin10`. Name recovered by forward-hashing the engine's authoring
#: identifiers as `CSymbol64(name + "Win10")`.
PLATFORM_CR = "40861b479cac8cd8"

#: `CAnimationCRWin10` -- marks WHICH actors animate. Joined to the model's
#: skeleton and animation set, that is the skeletal mover source.
ANIMATION_CR = "db098c12ad9b9844"

#: The class symbol every `CAnimationCR` index row leads with.
ANIMATION_CLASS = 0x8459BC252C90F074

#: `CAnimationCR` index row: class symbol, actor, 0xFFFFFFFF, 0.
ANIMATION_INDEX_STRIDE = 24

#: `CModelCRWin10` -- actor -> model.
MODEL_CR = "ea51a0d76eb90142"

#: `CPlatformCR` payload: stride, and the two inline endpoint vectors.
PLATFORM_STRIDE = 384
PLATFORM_INDEX_BASE = 0x128       # region A, stride 24 -- the component index
PLATFORM_INDEX_STRIDE = 24
P_POINT_A = 0x158
P_POINT_B = 0x164

#: `CR15PlatformCRWin10` -- the R15 rewrite of the same component, and a FOURTH
#: mover source. `mpl_combat_dyson` has six of these and no `CPlatformCR` file
#: at all, so a scan that knew only the two older components reported it as
#: having no straight-line movers.
#:
#: Same shape, shifted: stride 392 instead of 384, endpoints at +0x160/+0x16c
#: instead of +0x158/+0x164, and the same unexplained constant 6.0 one word
#: further along. Verified by the property `CPlatformCR` was verified with --
#: one of the two endpoints coincides with the constrained actor's own rest
#: transform -- which holds for **30 of 30** records across all four levels that
#: carry the component, to 0.0000 m. Travel comes out 3.0-7.0 m.
R15_PLATFORM_CR = "6dcacf3be89109a0"
R15_PLATFORM_STRIDE = 392
R15_P_POINT_A = 0x160
R15_P_POINT_B = 0x16C

RECORD_STRIDE = 136
SIZE_OFFSET = 0x08          # u32 table byte size, in the header
R_ACTOR = 0x08
R_ANCHOR_A = 0x40
R_ANCHOR_B = 0x58

#: Travel shorter than this is a modelling artefact, not a mover.
MIN_TRAVEL = 1e-3


def _table(blob: bytes):
    """Yield `(actor, anchor_a, anchor_b)` per record, or nothing."""
    if len(blob) < SIZE_OFFSET + 4:
        return
    size = struct.unpack_from("<I", blob, SIZE_OFFSET)[0]
    if size <= 0 or size > len(blob) or size % RECORD_STRIDE:
        return                      # not this layout -- refuse rather than guess
    base = len(blob) - size
    for i in range(size // RECORD_STRIDE):
        off = base + i * RECORD_STRIDE
        yield (struct.unpack_from("<Q", blob, off + R_ACTOR)[0],
               struct.unpack_from("<Q", blob, off + R_ANCHOR_A)[0],
               struct.unpack_from("<Q", blob, off + R_ANCHOR_B)[0])


def actor_positions(root: Path, members) -> dict:
    """`{nodeid: (x, y, z)}` over every member's actor table."""
    import evr_actor_data
    from evr_resource_types import ACTOR_DATA, resolve_type_dir

    out: dict = {}
    directory = resolve_type_dir(root, ACTOR_DATA)
    for member in members:
        path = directory / member
        if not path.exists():
            path = path.with_suffix(".bin")
        if not path.exists():
            continue
        try:
            actors = evr_actor_data.parse(path.read_bytes()).get("actors") or []
        except Exception:                                   # noqa: BLE001
            continue
        for actor in actors:
            transform = actor.get("transform") or {}
            pos = transform.get("position")
            if not pos:
                continue
            if isinstance(pos, dict):
                out[actor["nodeid"]] = (pos.get("x", 0.0), pos.get("y", 0.0),
                                        pos.get("z", 0.0))
            else:
                out[actor["nodeid"]] = (pos[0], pos[1], pos[2])
    return out


def animated_actors(blob: bytes) -> set:
    """Actor nodeids a `CAnimationCR` marks as animated.

    The index is a flat run of 24-byte rows -- class symbol, actor,
    `0xFFFFFFFF`, `0` -- so the rows are found by that four-field signature
    rather than by a header walk, which is what makes it survive the payload
    region repeating each actor a second time.

    Validated against the count the previous reading of this file reported:
    `mpl_arena_a` yields 96 rows over **48 distinct actors**, which is the 48
    that reading named.
    """
    out: set = set()
    needle = struct.pack("<Q", ANIMATION_CLASS)
    at = 0
    while True:
        at = blob.find(needle, at)
        if at < 0:
            break
        if at + ANIMATION_INDEX_STRIDE <= len(blob):
            tail, zero = struct.unpack_from("<II", blob, at + 16)
            if tail == 0xFFFFFFFF and zero == 0:
                actor = struct.unpack_from("<Q", blob, at + 8)[0]
                if actor not in (0, 0xFFFFFFFFFFFFFFFF):
                    out.add(actor)
        at += 8
    return out


def skeletal_movers(root: Path, members, positions: dict | None = None) -> dict:
    """Actors that move by DEFORMING A RIG, keyed the same way as `movers_for`.

    An actor qualifies when `CAnimationCR` marks it AND the model it binds owns
    both a `CSkeletonResource` and a `CAnimSetResource`. Each record carries the
    rig (bone count, root bones, named bones) and the animation inventory.

    ⚠ `travel` is deliberately absent: this kind of mover does not translate,
    and the pose curves are not decoded. A consumer that keyframes `travel`
    will correctly skip these and should tag them instead.
    """
    from evr_resource_types import ACTOR_DATA, resolve_type_dir

    import evr_animset
    import evr_apply_skeleton as skel

    if positions is None:
        positions = actor_positions(root, members)
    names = evr_animset.load_names()
    bone_table = skel.bone_name_table()

    out: dict = {}
    for member in members:
        anim_blob = _read(root, ANIMATION_CR, member)
        model_blob = _read(root, MODEL_CR, member)
        if not anim_blob or not model_blob:
            continue
        marked = animated_actors(anim_blob)
        if not marked:
            continue

        actor_ids = set(positions)
        if not actor_ids:
            actor_ids = _actor_ids(resolve_type_dir(root, ACTOR_DATA), member)
        bindings = _model_bindings(model_blob, actor_ids)

        for actor in sorted(marked):
            for model in bindings.get(actor, ()):
                if not skel.has_skeleton(root, model):
                    continue
                anim_set = evr_animset.read(root, model, names)
                if anim_set is None:
                    continue
                rig = _rig_summary(root, model, skel, bone_table)
                if rig is None:
                    continue
                rest = positions.get(actor)
                out[str(actor)] = {
                    "rest": ([round(v, 5) for v in rest] if rest else None),
                    "level": member,
                    "source": "CAnimationCR + CSkeletonResource/CAnimSetResource",
                    "kind": "skeletal",
                    "model": model,
                    "animations": [
                        {"name": a.name, "hash": a.name_hash}
                        for a in anim_set.animations],
                    "motion_decoded": False,
                    **rig,
                }
                break
    return out


def _rig_summary(root: Path, model: str, skel, bone_table: dict):
    """`{bones, bone_roots, bone_names}` from the model's `CSkeletonResource`.

    Reads through `evr_apply_skeleton`, which is the project's decoder for this
    resource -- the same one that builds the importer's armature -- so a mover
    record and the armature can never disagree about the rig.
    """
    from evr_resource_types import resource_path
    path = resource_path(root, skel.SKELETON_RESOURCE, model)
    if path is None:
        return None
    try:
        blob = path.read_bytes()
    except OSError:
        return None
    located = skel.locate_tables(blob)
    if located is None:
        return None
    count, _bind, hier = located
    tree = skel.find_hierarchy(blob, count)
    if tree is None:
        return None
    parent = tree[0]
    hashes = skel.bone_names(blob, count, hier)
    return {
        "bones": count,
        "bone_roots": [i for i, p in enumerate(parent) if p == skel.NO_BONE],
        "bone_names": [bone_table.get(h) or f"{h:016x}" for h in hashes],
    }


def _read(root: Path, type_hash: str, member: str) -> bytes:
    from evr_resource_types import resource_path
    # tolerates the stripped-leading-zero spelling (see evr_resource_types)
    path = resource_path(root, type_hash, member)
    if path is None:
        return b""
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _actor_ids(directory: Path, member: str) -> set:
    import evr_actor_data
    path = directory / member
    if not path.exists():
        path = path.with_suffix(".bin")
    if not path.exists():
        return set()
    try:
        actors = evr_actor_data.parse(path.read_bytes()).get("actors") or []
    except Exception:                                       # noqa: BLE001
        return set()
    return {a["nodeid"] for a in actors}


def _model_bindings(data: bytes, actor_ids: set) -> dict:
    """`{actor: [model hash, ...]}` from a `CModelCR` blob.

    The same record framing and validity rule `evr_component_cr.parse_model_cr`
    uses, kept here so this module does not have to import the extractor that
    imports it.
    """
    import evr_component_cr

    out: dict = {}
    for off in range(0x20, max(0, len(data) - 8), 8):
        model = struct.unpack_from("<Q", data, off)[0]
        if model in (0, 0xFFFFFFFFFFFFFFFF):
            continue
        component_type = struct.unpack_from("<Q", data, off - 0x20)[0]
        selector = struct.unpack_from("<Q", data, off - 0x18)[0]
        record_id = struct.unpack_from("<Q", data, off - 0x08)[0]
        valid = component_type in evr_component_cr.CMODEL_COMPONENT_TYPES
        if not valid and record_id == 0x1C:
            valid = struct.unpack_from("<Q", data, off - 0x10)[0] == 0x000FFFFF
        if not (valid and selector in actor_ids):
            continue
        slot = out.setdefault(selector, [])
        value = f"{model:016x}"
        if value not in slot:
            slot.append(value)
    return out


def _platform_records(blob: bytes, known_actors, stride: int = PLATFORM_STRIDE,
                      point_a: int = P_POINT_A, point_b: int = P_POINT_B) -> list:
    """`[(actor, pointA, pointB), ...]` from a `CPlatformCR` blob.

    The payload base is not in the header, so it is located the same way it was
    reverse engineered: region A is a 24-byte index at `PLATFORM_INDEX_BASE`
    holding one actor id per component, and the payload is the NEXT run of
    actor ids after the gap that follows it. Anchoring on ids that are real
    actors keeps a wrong stride from silently producing garbage vectors.
    """
    hits = []
    for off in range(0, len(blob) - 8, 4):
        if struct.unpack_from("<Q", blob, off)[0] in known_actors:
            hits.append(off)
    if len(hits) < 2:
        return []
    # the one big gap separates the index from the payload
    jump = max(range(len(hits) - 1), key=lambda i: hits[i + 1] - hits[i])
    if hits[jump + 1] - hits[jump] < 1000:
        return []
    base = hits[jump + 1]
    count = min(len(hits) - jump - 1, (len(blob) - base) // stride)

    out = []
    for i in range(count):
        rec = base + i * stride
        actor = struct.unpack_from("<Q", blob, rec)[0]
        if actor not in known_actors:
            continue
        a = struct.unpack_from("<fff", blob, rec + point_a)
        b = struct.unpack_from("<fff", blob, rec + point_b)
        if any(v != v or abs(v) > 1e6 for v in a + b):
            continue
        out.append((actor, a, b))
    return out


def platform_movers(root: Path, members, positions: dict | None = None, *,
                    component: str = PLATFORM_CR,
                    stride: int = PLATFORM_STRIDE,
                    point_a: int = P_POINT_A, point_b: int = P_POINT_B,
                    label: str = "CPlatformCR") -> dict:
    """`CPlatformCR` movers, in the same shape as `movers_for`.

    Endpoints are inline world positions here, so no anchor lookup is needed --
    but the actor's own transform is still used to order the pair rest -> far
    end, and to drop the duplicate return-leg record for an actor already seen.

    The keyword arguments retarget it at `CR15PlatformCR`, which is the same
    record one word wider; `r15_platform_movers` is that call.
    """
    from evr_resource_types import resolve_type_dir

    if positions is None:
        positions = actor_positions(root, members)
    directory = resolve_type_dir(root, component)
    out: dict = {}
    for member in members:
        path = directory / member
        if not path.exists():
            path = path.with_suffix(".bin")
        if not path.exists():
            continue
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        out.update(platform_movers_from_blob(
            blob, positions, level=member, stride=stride,
            point_a=point_a, point_b=point_b, label=label, seen=out))
    return out


def platform_movers_from_blob(blob: bytes, positions: dict, *, level: str,
                              stride: int = PLATFORM_STRIDE,
                              point_a: int = P_POINT_A,
                              point_b: int = P_POINT_B,
                              label: str = "CPlatformCR",
                              seen: dict | None = None) -> dict:
    """`CPlatformCR` movers out of ONE blob, keyed by actor nodeid.

    Split out of `platform_movers` so a non-PC extract can reuse it: the record
    is the same on `CPlatformCRAndroid`, so the Quest path must not re-derive
    the ordering and de-duplication rules and risk drifting from this one. Only
    the resource lookup differs between platforms, and that stays with the
    caller.

    `seen` lets a caller thread results across several blobs, since the
    return-leg duplicate may land in a different file than the out leg.
    """
    out: dict = {}
    already = seen if seen is not None else {}
    for actor, pa, pb in _platform_records(
            blob, set(positions), stride, point_a, point_b):
        travel = tuple(pb[i] - pa[i] for i in range(3))
        if sum(v * v for v in travel) ** 0.5 < MIN_TRAVEL:
            continue
        own = positions.get(actor)
        if own is not None and all(abs(own[i] - pb[i]) < 5e-2 for i in range(3)):
            pa, pb = pb, pa
            travel = tuple(-v for v in travel)
        key = str(actor)
        if key in already or key in out:
            # the return leg of a pair already recorded -- same motion
            continue
        out[key] = {
            "rest": [round(v, 5) for v in pa],
            "travel": [round(v, 5) for v in travel],
            "distance": round(sum(v * v for v in travel) ** 0.5, 5),
            "level": level,
            "source": label,
        }
    return out


def r15_platform_movers(root: Path, members,
                        positions: dict | None = None) -> dict:
    """`CR15PlatformCR` movers -- the same record, one word wider."""
    return platform_movers(root, members, positions,
                           component=R15_PLATFORM_CR,
                           stride=R15_PLATFORM_STRIDE,
                           point_a=R15_P_POINT_A, point_b=R15_P_POINT_B,
                           label="CR15PlatformCR")


def movers_for(root: Path, members, positions: dict | None = None) -> dict:
    """`{actor_nodeid: {"rest": [x,y,z], "travel": [dx,dy,dz], "level": hash}}`.

    `rest` is the anchor that coincides with the constrained actor when one
    does; otherwise anchor A, so the pair is always ordered rest -> far end.
    """
    from evr_resource_types import resolve_type_dir

    if positions is None:
        positions = actor_positions(root, members)
    directory = resolve_type_dir(root, LINEAR_POSITION_CONSTRAINT)
    out: dict = {}
    for member in members:
        path = directory / member
        if not path.exists():
            path = path.with_suffix(".bin")
        if not path.exists():
            continue
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        for actor, a, b in _table(blob):
            pa, pb = positions.get(a), positions.get(b)
            if pa is None or pb is None:
                continue
            travel = tuple(pb[i] - pa[i] for i in range(3))
            if sum(v * v for v in travel) ** 0.5 < MIN_TRAVEL:
                continue
            # Order rest -> far end using the constrained actor's own transform.
            own = positions.get(actor)
            if own is not None and all(abs(own[i] - pb[i]) < 1e-3 for i in range(3)):
                pa, pb = pb, pa
                travel = tuple(-v for v in travel)
            out[str(actor)] = {
                "rest": [round(v, 5) for v in pa],
                "travel": [round(v, 5) for v in travel],
                "distance": round(sum(v * v for v in travel) ** 0.5, 5),
                "level": member,
                "source": "CR15LinearPositionConstraintCR",
            }

    # A level may use any of the three straight-line components, or none --
    # `mpl_arena_a` has no R15 constraints at all and every one of its movers is
    # a `CPlatformCR`, while `mpl_combat_dyson` has only `CR15PlatformCR`. The
    # R15 CONSTRAINT entries win a collision: that path resolves its endpoints
    # from real anchor actors, which is the stronger evidence.
    for source in (platform_movers, r15_platform_movers):
        for actor, rec in source(root, members, positions).items():
            out.setdefault(actor, rec)
    return out


def main(argv=None) -> int:
    import argparse
    import json
    import sys

    here = Path(__file__).resolve().parent
    for extra in (str(here), str(here.parent / "blender_tool")):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    import evr_paths
    evr_paths.install_import_paths()

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("level")
    ap.add_argument("--dir", default=None)
    ap.add_argument("--members", nargs="*", default=None)
    args = ap.parse_args(argv)
    root = evr_paths.require_extract(args.dir)
    members = args.members or [args.level]
    found = movers_for(root, members)
    print(json.dumps(found, indent=1))
    print(f"\n{len(found)} mover(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
