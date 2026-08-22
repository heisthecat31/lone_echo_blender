"""Build Blender animation for Echo VR's moving level geometry.

Reads the `movers.json` sidecar written by `scripts/evr_movers.py`: a package
instance index mapped to `rest` and `travel`, both in GAME axes.

## What is authored and what is not

**Authored:** where the motion starts and where it ends. That comes from an
`CR15LinearPositionConstraintCR` naming two anchor ACTORS; the travel is the
vector between them.

⚠ **NOT authored: the timing.** When a mover fires, how long it takes, and
whether it returns are decided by `CScriptCR` and the R15 touch-interact
components, none of which are decoded. The keyframes here are therefore a
PLACEHOLDER schedule -- a linear there-and-back over `frames` frames -- chosen
so the motion is visible and scrubbable, not because the game does it that way.
Every object gets `evr_mover_timing_is_placeholder` set so this cannot be
mistaken for real data later.

## Axis conversion

`mesh_builder` stands the scene upright with a +90 deg rotation about X, i.e.
game `(x, y, z)` -> Blender `(x, -z, y)` (a pure rotation, det +1). The travel
vector is a DIRECTION in the same space, so it takes the same transform: a
game-space drop of `(0, -2.747, 0)` becomes `(0, 0, -2.747)`, straight down in
Blender's Z.
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy

SIDECAR_NAME = "movers.json"
SIDECAR_FORMAT = "evr_movers"

#: Placeholder half-cycle length, in frames. Not authored -- see the module docstring.
DEFAULT_FRAMES = 48


def sidecar_path(package) -> Path | None:
    root = Path(package)
    if root.is_file():
        root = root.parent
    candidate = root / SIDECAR_NAME
    return candidate if candidate.is_file() else None


def load(package) -> dict | None:
    """The parsed sidecar, or None when the level has no movers."""
    path = sidecar_path(package)
    if path is None:
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if doc.get("format") == SIDECAR_FORMAT else None


def _to_blender(vec, y_up_to_z_up: bool):
    x, y, z = float(vec[0]), float(vec[1]), float(vec[2])
    return (x, -z, y) if y_up_to_z_up else (x, y, z)



def _fcurves(action):
    """Every F-curve of `action`, across Blender's two Action layouts.

    Blender 4.4+ ("slotted actions") removed `Action.fcurves`; curves now sit
    under `layers[].strips[].channelbags[].fcurves`. Blender 5 raises
    AttributeError on the old path, so both are tried rather than version-sniffed.
    """
    curves = getattr(action, "fcurves", None)
    if curves is not None:
        return list(curves)
    out = []
    for layer in getattr(action, "layers", ()) or ():
        for strip in getattr(layer, "strips", ()) or ():
            for bag in getattr(strip, "channelbags", ()) or ():
                out.extend(getattr(bag, "fcurves", ()) or ())
    return out


def apply(doc: dict, objects_by_instance: dict, *, y_up_to_z_up: bool = True,
          frames: int = DEFAULT_FRAMES, ping_pong: bool = True,
          scene=None) -> dict:
    """Keyframe every mover between its rest pose and the far end.

    `objects_by_instance` maps a package instance index to the objects built
    from it -- the same map the lighting pass uses.
    """
    entries = (doc or {}).get("instances") or {}
    if not entries:
        return {"animated": 0, "reason": "no movers in sidecar"}

    frames = max(1, int(frames))
    animated = missing = 0
    distances = []

    for key, rec in entries.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            continue
        objects = objects_by_instance.get(index) or ()
        if not objects:
            missing += 1
            continue
        travel = _to_blender(rec.get("travel") or (0, 0, 0), y_up_to_z_up)
        if not any(travel):
            continue
        for obj in objects:
            base = tuple(obj.location)
            moved = tuple(base[i] + travel[i] for i in range(3))

            obj.animation_data_clear()
            obj.location = base
            obj.keyframe_insert("location", frame=1)
            obj.location = moved
            obj.keyframe_insert("location", frame=1 + frames)
            if ping_pong:
                obj.location = base
                obj.keyframe_insert("location", frame=1 + 2 * frames)
            obj.location = base

            action = getattr(getattr(obj, "animation_data", None), "action", None)
            if action is not None:
                action.name = f"EVR_mover_{index}"
                for curve in _fcurves(action):
                    for point in curve.keyframe_points:
                        point.interpolation = "BEZIER"

            obj["evr_mover_travel"] = list(travel)
            obj["evr_mover_distance"] = float(rec.get("distance") or 0.0)
            obj["evr_mover_level"] = str(rec.get("level") or "")
            obj["evr_mover_timing_is_placeholder"] = (
                "start/end are authored (R15 linear constraint anchors); the "
                "FRAME TIMING is not -- the trigger and duration live in "
                "CScriptCR, which is not decoded")
            animated += 1
        distances.append(float(rec.get("distance") or 0.0))

    target = scene or bpy.context.scene
    if animated and target is not None:
        span = 1 + (2 if ping_pong else 1) * frames
        if target.frame_end < span:
            target.frame_end = span

    out = {"animated": animated}
    if missing:
        out["no_object"] = missing
    if distances:
        out["distances"] = sorted({round(d, 3) for d in distances})
    return out


def summarize(doc: dict) -> dict:
    entries = (doc or {}).get("instances") or {}
    return {
        "movers": len(entries),
        "distances": sorted({round(float(r.get("distance") or 0.0), 3)
                             for r in entries.values()}),
    }
