"""Level geometry that MOVES -- from TWO different components.

## What this is, and what it is not

Echo VR does not animate level geometry with animation curves. There are only
53 `CAnimSetResource` files in the whole extract and they are character rigs;
a moving platform has none. Movement comes from two places, and a level may use
either:

* `CR15LinearPositionConstraintCR` -- the R15 constraint system. Used by
  `mpl_combat_fission` (17 movers over 45 instances).
* `CPlatformCR` -- the platform component. Used by `mpl_arena_a`, which has NO
  R15 constraints at all, which is why scanning only for those reported the
  arena as having no movers while its launchers and tunnel mouths visibly slide
  in game.

⚠ A level having no `CR15LinearPositionConstraintCR` does NOT mean it is static.
Check both.

## `CAnimationCR` is NOT a third source

`mpl_arena_a` also carries `CAnimationCR` (48 actors over 6 models), but its
216-byte records hold no curve, no asset reference and no endpoints -- every
field is either a `0xFFFFFFFF` sentinel, a constant `32`, or the class symbol
`8459bc252c90f074`, byte-identical between records apart from the actor id. It
marks WHICH actors animate and says nothing about how, so there is nothing here
to emit from it.

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

#: `CPlatformCR` payload: stride, and the two inline endpoint vectors.
PLATFORM_STRIDE = 384
PLATFORM_INDEX_BASE = 0x128       # region A, stride 24 -- the component index
PLATFORM_INDEX_STRIDE = 24
P_POINT_A = 0x158
P_POINT_B = 0x164

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


def _platform_records(blob: bytes, known_actors) -> list:
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
    count = min(len(hits) - jump - 1, (len(blob) - base) // PLATFORM_STRIDE)

    out = []
    for i in range(count):
        rec = base + i * PLATFORM_STRIDE
        actor = struct.unpack_from("<Q", blob, rec)[0]
        if actor not in known_actors:
            continue
        a = struct.unpack_from("<fff", blob, rec + P_POINT_A)
        b = struct.unpack_from("<fff", blob, rec + P_POINT_B)
        if any(v != v or abs(v) > 1e6 for v in a + b):
            continue
        out.append((actor, a, b))
    return out


def platform_movers(root: Path, members, positions: dict | None = None) -> dict:
    """`CPlatformCR` movers, in the same shape as `movers_for`.

    Endpoints are inline world positions here, so no anchor lookup is needed --
    but the actor's own transform is still used to order the pair rest -> far
    end, and to drop the duplicate return-leg record for an actor already seen.
    """
    from evr_resource_types import resolve_type_dir

    if positions is None:
        positions = actor_positions(root, members)
    known = set(positions)
    directory = resolve_type_dir(root, PLATFORM_CR)
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
        for actor, pa, pb in _platform_records(blob, known):
            travel = tuple(pb[i] - pa[i] for i in range(3))
            if sum(v * v for v in travel) ** 0.5 < MIN_TRAVEL:
                continue
            own = positions.get(actor)
            if own is not None and all(abs(own[i] - pb[i]) < 5e-2 for i in range(3)):
                pa, pb = pb, pa
                travel = tuple(-v for v in travel)
            key = str(actor)
            if key in out:
                # the return leg of a pair already recorded -- same motion
                continue
            out[key] = {
                "rest": [round(v, 5) for v in pa],
                "travel": [round(v, 5) for v in travel],
                "distance": round(sum(v * v for v in travel) ** 0.5, 5),
                "level": member,
                "source": "CPlatformCR",
            }
    return out


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

    # A level may use either component, or neither -- `mpl_arena_a` has no R15
    # constraints at all and every one of its movers is a `CPlatformCR`. The R15
    # entries win a collision: that path resolves its endpoints from real anchor
    # actors, which is the stronger evidence.
    for actor, rec in platform_movers(root, members, positions).items():
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
