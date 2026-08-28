"""Quest level geometry that MOVES -- `CPlatformCRAndroid`.

## The record is the PC record

`CPlatformCRAndroid` (`311ebc6087c00544`) is byte-for-byte the layout
`evr_movers` documents for `CPlatformCRWin10`, so this module does NOT re-derive
it. It locates the resource and hands the blob to
`evr_movers.platform_movers_from_blob`; the ordering, the rest -> far-end
orientation and the return-leg de-duplication are that module's, once.

Measured on the arena (`7691d78d414c5701`, 45,896 B) rather than assumed:

    index region  0x128, stride 24        <- identical to Win10
    payload       stride 384, 59 records  <- identical to Win10
        +0x000  u64      actor
        +0x158  f32[3]   endpoint A       <- world position, not a delta
        +0x164  f32[3]   endpoint B

⭐ The check that pins it is the same one the PC module uses: over ALL SIX
shipped `CPlatformCRAndroid` files, **130 of 130** records have one of their two
endpoints coincide with the actor's own rest transform to within 5 cm, and ZERO
match neither. A wrong stride or a wrong field offset cannot produce that -- it
would scatter the endpoints away from the actors entirely. Which slot holds the
rest pose is not fixed (68 in A, 62 in B), which is exactly why the pair has to
be ordered against the actor rather than taken as stored.

Travel distances land on the same clusters the PC arena has -- 2.0 m, 2.2 m and
9.297 m -- which is what you would expect, since it is the same level.

⚠ The payload base is NOT at a fixed offset and must not be guessed at. It is
22,864 in the two 45,896 B files and 1,808 in the four 3,336 B ones; the small
files are NOT empty, they carry 3 records and 2 movers each. Finding it by the
big gap after the index -- what `evr_movers._platform_records` does -- is what
gets all six right; an offset cutoff picked off the arena reports the four
small levels as static.

## What is NOT decoded -- inherited from the PC path

⛔ **The timing and the trigger.** When a mover fires, how long it takes and
whether it returns live in `CScriptCR`, which is not decoded. The sidecar
carries geometry only; a consumer's keyframe schedule is a PLACEHOLDER. The
Blender side already marks every such object `evr_mover_timing_is_placeholder`.

⛔ **Skeletal movers.** The PC path has a second kind -- an actor `CAnimationCR`
marks as animated whose model owns a skeleton and an animation set. That join
is not implemented here, so `skeletal` is always empty on Quest. It is a real
gap, not an absence of data: the Quest extract does carry `CAnimationCRAndroid`
(15 files) and `CSkeletonResource` (113).
"""

from __future__ import annotations

import sys
from pathlib import Path

_QUEST = Path(__file__).resolve().parent
_SCRIPTS = _QUEST.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import evr_movers as pc_movers                              # noqa: E402
from evr_quest import types as qtypes                       # noqa: E402
from evr_quest import scene as qscene                       # noqa: E402

#: The sidecar the Blender add-on already reads. Same name and format as the
#: PC path writes, so the import side needs no Quest-specific branch.
SIDECAR_NAME = "movers.json"
SIDECAR_FORMAT = "evr_movers"

#: `CPlatformCRAndroid` carries the Win10 record unchanged -- see the docstring.
LABEL = "CPlatformCRAndroid"


def actor_positions(root, level: str) -> dict:
    """`{nodeid: (x, y, z)}` -- the rest transform of every actor."""
    return {actor: tuple(transform[0])
            for actor, transform in qscene.actor_transforms(root, level).items()}


def movers_for(root, level: str, positions: dict | None = None) -> dict:
    """`{actor_nodeid: {"rest", "travel", "distance", "level", "source"}}`."""
    path = qtypes.resource(root, qtypes.PLATFORM_CR, level)
    if path is None:
        return {}
    try:
        blob = path.read_bytes()
    except OSError:
        return {}
    if positions is None:
        positions = actor_positions(root, level)
    if not positions:
        return {}
    return pc_movers.platform_movers_from_blob(
        blob, positions, level=level, label=LABEL)


def rows_for_instances(movers: dict, instances) -> dict:
    """`{package instance index: mover record}`.

    The add-on keys movers by instance index, not by actor, because that is
    what it can resolve to objects. `QuestInstance.entity` is the actor, and
    instances are packed in list order, so the index is the list position.
    """
    rows = {}
    for index, instance in enumerate(instances):
        record = movers.get(str(getattr(instance, "entity", None)))
        if record is not None:
            rows[str(index)] = record
    return rows


def document(rows: dict) -> dict:
    """The `movers.json` payload, in the PC sidecar's shape."""
    return {
        "format": SIDECAR_FORMAT,
        "version": 1,
        "source": "evr_quest",
        "note": ("Package instance index -> a straight-line mover. `rest` is "
                 "the instance's authored position and `travel` the offset to "
                 "the far end, both in GAME axes (Y up). Recovered from "
                 "CPlatformCRAndroid, which stores both endpoints inline as "
                 "world positions. \u26a0 TIMING AND TRIGGER ARE NOT DECODED: "
                 "when a mover fires and whether it returns live in CScriptCR, "
                 "so a consumer's keyframe timing is a placeholder, not "
                 "authored data. `skeletal` is empty on Quest -- the "
                 "CAnimationCR-to-skeleton join the PC path does is not "
                 "implemented here, so rigged movers are MISSING, not absent."),
        "instances": rows,
        "skeletal": {},
    }


def write_sidecar(out_dir, rows: dict) -> Path | None:
    """Write `movers.json`, or nothing at all when the level has no movers."""
    import json

    if not rows:
        return None
    path = Path(out_dir) / SIDECAR_NAME
    path.write_text(json.dumps(document(rows), indent=1), encoding="utf-8")
    return path


def main(argv=None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("level", help="level hash")
    ap.add_argument("--dir", default="H:/quest-extracted")
    args = ap.parse_args(argv)

    root = Path(args.dir)
    found = movers_for(root, args.level)
    if not found:
        print("no movers in %s" % args.level)
        return 0
    print(json.dumps(found, indent=1))
    distinct = {tuple(r["travel"]) for r in found.values()}
    print("\n%d mover(s), %d distinct motion(s)" % (len(found), len(distinct)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
