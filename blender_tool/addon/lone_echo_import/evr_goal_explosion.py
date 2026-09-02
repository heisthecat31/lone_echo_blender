"""Build the goal explosion: hidden, then it fires, in the scoring team's colour.

Reads the `goal_explosion.json` sidecar written by `scripts/evr_goal_explosion.py`
for WHICH props and WHAT colour, and `data/evr_goal_animation.json` for the
motion.

## Where each half comes from

**From the level** -- which goal mouth each prop serves, where every shockwave
ring sits, and both team colours, read off the accent tints the package already
carries: `(1.0, 0.264, 0.059)` at `mpl_arena_a`'s orange end and
`(0.0, 0.569, 1.0)` at its blue end. A goal flashes the colour of the team that
scored INTO it, so each end takes the other's.

**From the demo viewer** -- the motion. `OrangeGoalAnim.anim` in the user's own
Unity project animates six `Line` beams coincident at the goal mouth: each
scales from 0 to full along its length in 0.367 s, swings to a yaw of +-70 deg
by 0.833 s, rolls to 0/+-10 and then 0/+-30 by 2.317 s, returns, and closes at
5.0 s, while a point light opens to range 20 / intensity 1.4 and a flare light
to range 2.

⚠ **That clip is a RECONSTRUCTION, not shipped data.** The levels carry the
beam props with no motion of their own; the trigger and schedule live in
`CScriptCR`, which is not decoded -- the same wall `evr_movers` hits. It is used
because it is a far better reference than an invented curve. Every object
carries `evr_goal_explosion_source` saying so.

## Aiming, not copying

⛔ The Unity euler angles are NOT applied directly. That rig points its beams
along local +X under Y-up axes; Echo VR authors the same beam along game +Y,
which this importer stands up as Blender +Z. Copying the angles would fan the
beams in the wrong plane. `_beam_direction` turns each key into a DIRECTION
instead, maps it through the arena's own axes, and the object is rotated from
its rest direction onto it -- so the fan lands correctly whatever the prop's
authored orientation.

⛔ The props are also HIDDEN before the effect starts, which is the one change
here that is not cosmetic: they sit inside the goal permanently in the extracted
level, and in game they are not there until someone scores.
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy
from mathutils import Vector

SIDECAR_NAME = "goal_explosion.json"
SIDECAR_FORMAT = "evr_goal_explosion"
CLIP_NAME = "evr_goal_animation.json"
CLIP_FORMAT = "evr_goal_animation"

DEFAULT_START = 1
#: Frames per second the reference clip's SECONDS are laid out at. The clip is
#: 5 s, so 24 fps puts the whole effect in 120 frames.
DEFAULT_FPS = 24
#: Beams per goal. Six is what `OrangeScoreFX.prefab` builds; the level itself
#: ships ONE, which the game instances at runtime. 0 animates only the props
#: the level actually contains.
DEFAULT_BEAMS = 6
#: A zero-scale object has a degenerate matrix and Blender warns about it.
CLOSED_SCALE = 0.001


def sidecar_path(package) -> Path | None:
    root = Path(package)
    if root.is_file():
        root = root.parent
    candidate = root / SIDECAR_NAME
    return candidate if candidate.is_file() else None


def load(package) -> dict | None:
    """The parsed sidecar, or None when the level has no goal explosion."""
    path = sidecar_path(package)
    if path is None:
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if doc.get("format") == SIDECAR_FORMAT else None


def clip(doc: dict | None = None, data_dir=None) -> dict | None:
    """The reference goal clip.

    ⚠ Taken from the SIDECAR first. The addon is installed outside this repo,
    so a clip that only ever lived in `data/` would be missing exactly where it
    is needed; the extractor carries it into the package for that reason. The
    `data/` copy is the fallback for a package written before it did.
    """
    embedded = (doc or {}).get("animation")
    if embedded and embedded.get("format") == CLIP_FORMAT:
        return embedded
    root = Path(data_dir) if data_dir else (
        Path(__file__).resolve().parents[3] / "data")
    path = root / CLIP_NAME
    if not path.is_file():
        return None
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return found if found.get("format") == CLIP_FORMAT else None


def _beam_direction(euler, outward):
    """A Unity key `(x, y, z)` turned into a direction in THIS scene's axes.

    ⭐ The reference beam rests along Unity local +X and Unity applies euler
    ZXY, so with `x == 0` the aimed direction is
    `(cos z cos y, sin z, -cos z sin y)`. The arena's long axis is Unity X (the
    rig sits at the goal mouth, x = -35.25), its up is Unity Y and its lateral
    is Unity Z; this importer's scene is game axes stood upright, i.e. game
    `(x, y, z) -> (x, -z, y)`. Composing those gives the mapping below.

    `outward` is +1 or -1: the goal at the far end of the arena fans the other
    way, and the sign is the only difference between the two ends.
    """
    from math import cos, radians, sin
    _x, y, z = (radians(float(c)) for c in euler)
    ux, uy, uz = cos(z) * cos(y), sin(z), -cos(z) * sin(y)
    # Unity -> game: the rig's X is the arena's long axis (game Z), its Z the
    # lateral (game X). Then game -> Blender.
    gx, gy, gz = uz, uy, ux * float(outward)
    return Vector((gx, -gz, gy)).normalized()


def _aim(obj, base_quat, rest_dir, direction):
    """Point `obj`'s rest axis along `direction`, keeping its placement."""
    obj.rotation_mode = "QUATERNION"
    obj.rotation_quaternion = rest_dir.rotation_difference(direction) @ base_quat


#: A prop whose two cross-section extents are both under this fraction of its
#: length is a BEAM, and only a beam gets fanned into the reference rig's six.
BEAM_SLENDERNESS = 0.1


def _is_beam(obj) -> bool:
    """Is this prop one of the reference rig's `Line` beams?

    ⭐ Measured, not named. `mpl_arena_a`'s beam is 0.26 x 35.06 x 0.27 -- thin
    on both cross axes -- while the fan sheet beside it is 0.76 x 34.2 x 35.2
    and the shockwave rings are 0.06 x 36.1 x 24.7. Fanning a 35-unit-wide
    sheet six ways is not what the clip does; only the beams multiply.
    """
    data = getattr(obj, "data", None)
    verts = getattr(data, "vertices", None)
    if not verts:
        return False
    size = [max(v.co[k] for v in verts) - min(v.co[k] for v in verts)
            for k in range(3)]
    length = max(size)
    if length <= 0:
        return False
    return sorted(size)[1] <= BEAM_SLENDERNESS * length


def _scale_only(obj, beam, start: int, fps: int) -> tuple:
    """The clip's open/close, without the fan.

    The shockwave rings are placed along the arena, not at the mouth; the
    reference rig has nothing at those positions, so swinging them through its
    beam angles would be inventing motion the level does not imply. They take
    the timing and nothing else.
    """
    full = tuple(obj.scale)
    frames = []
    for frame, vec in _key_frames(beam.get("scale") or (), start, fps):
        grow = max(CLOSED_SCALE, float(vec[0]))
        obj.scale = tuple(c * grow for c in full)
        obj.keyframe_insert("scale", frame=frame)
        frames.append(frame)
    obj.scale = full
    return (min(frames), max(frames)) if frames else (start, start)


def _rest_axis(obj) -> "Vector":
    """The object's own LONG axis, in world space -- the beam's length.

    Read from the mesh rather than assumed: the props are authored along game
    +Y, but a prop that is not a beam (the fan sheet, a ring) has its own
    longest axis and aiming it by the same rule keeps it consistent.
    """
    data = getattr(obj, "data", None)
    verts = getattr(data, "vertices", None)
    if not verts:
        return Vector((0.0, 0.0, 1.0))
    lo = [min(v.co[k] for v in verts) for k in range(3)]
    hi = [max(v.co[k] for v in verts) for k in range(3)]
    axis = max(range(3), key=lambda k: hi[k] - lo[k])
    local = Vector((0.0, 0.0, 0.0))
    local[axis] = 1.0 if hi[axis] >= -lo[axis] else -1.0
    return (obj.matrix_world.to_quaternion() @ local).normalized()


def _hide_until(obj, frame_on: int, frame_off: int) -> None:
    """Keyframe the object invisible outside `[frame_on, frame_off]`.

    Both `hide_viewport` and `hide_render` are driven: viewport alone leaves the
    prop in a render, which is exactly the state being fixed.
    """
    for prop in ("hide_viewport", "hide_render"):
        setattr(obj, prop, True)
        obj.keyframe_insert(prop, frame=max(1, frame_on - 1))
        setattr(obj, prop, False)
        obj.keyframe_insert(prop, frame=frame_on)
        setattr(obj, prop, True)
        obj.keyframe_insert(prop, frame=frame_off + 1)
        setattr(obj, prop, True)


def _bezier(obj) -> None:
    action = getattr(getattr(obj, "animation_data", None), "action", None)
    if action is None:
        return
    curves = getattr(action, "fcurves", None)
    if curves is None:
        curves = []
        for layer in getattr(action, "layers", ()) or ():
            for strip in getattr(layer, "strips", ()) or ():
                for bag in getattr(strip, "channelbags", ()) or ():
                    curves.extend(getattr(bag, "fcurves", ()) or ())
    for curve in curves:
        for point in curve.keyframe_points:
            # A visibility switch must SNAP; only the motion eases.
            point.interpolation = (
                "CONSTANT" if curve.data_path.startswith("hide_") else "BEZIER")


def _tint(obj, rgb, tag: str) -> bool:
    """Swap every slot for a variant multiplied by `rgb`. True if anything changed."""
    try:
        from . import material_builder
    except ImportError:                                  # pragma: no cover
        import material_builder
    tinted = False
    for slot in obj.material_slots:
        # ⭐ OBJECT-linked, and read BEFORE relinking. A slot linked to DATA
        # writes through the shared mesh, so tinting one goal's prop retints
        # the other goal's -- which is exactly what happened: the second end
        # found `M__goal_ff430f` already in the slot and produced
        # `M__goal_ff430f__goal_0090ff`, both colours stacked on one material.
        base = slot.material
        if base is None:
            continue
        variant = material_builder.explosion_tinted_variant(base, rgb, tag)
        if variant is not None and variant is not base:
            slot.link = "OBJECT"
            slot.material = variant
            tinted = True
    return tinted


def _key_frames(keys, start: int, fps: int):
    """Reference key times (SECONDS) as scene frames."""
    return [(start + int(round(float(t) * fps)), v) for t, v in keys]


def _animate_beam(obj, beam, start: int, fps: int, outward: float) -> tuple:
    """Aim and scale one beam along the reference clip. -> (first, last) frame."""
    base_quat = obj.matrix_world.to_quaternion().copy()
    rest = _rest_axis(obj)
    full = tuple(obj.scale)
    obj.rotation_mode = "QUATERNION"

    for frame, euler in _key_frames(beam.get("euler") or (), start, fps):
        _aim(obj, base_quat, rest, _beam_direction(euler, outward))
        obj.keyframe_insert("rotation_quaternion", frame=frame)
    # ⭐ The clip scales the beam along its LENGTH only (Unity x), so the other
    # two axes keep the prop's own thickness -- scaling all three would shrink
    # a 35-unit beam into a dot instead of retracting it.
    axis = max(range(3), key=lambda k: abs(rest[k]))
    for frame, vec in _key_frames(beam.get("scale") or (), start, fps):
        grow = max(CLOSED_SCALE, float(vec[0]))
        obj.scale = tuple(full[k] * (grow if k == axis else 1.0) for k in range(3))
        obj.keyframe_insert("scale", frame=frame)
    obj.scale = full

    frames = [f for f, _v in _key_frames(
        (beam.get("scale") or beam.get("euler") or ()), start, fps)]
    return (min(frames), max(frames)) if frames else (start, start)


def apply(doc: dict, objects_by_instance: dict, *, start: int = DEFAULT_START,
          fps: int = DEFAULT_FPS, beams: int = DEFAULT_BEAMS,
          colour: bool = True, scene=None, clip_doc=None) -> dict:
    """Hide, aim, scale and tint every goal-explosion prop.

    `objects_by_instance` maps a package instance index to the objects built
    from it -- the same map the mover and lighting passes use.
    """
    ends = (doc or {}).get("ends") or []
    if not ends:
        return {"animated": 0, "reason": "no ends in sidecar"}
    ref = clip_doc if clip_doc is not None else clip(doc)
    if not ref or not (ref.get("beams") or ()):
        return {"animated": 0, "reason": "no reference clip in data/"}

    start = max(1, int(start))
    fps = max(1, int(fps))
    ref_beams = ref["beams"]
    want = len(ref_beams) if beams is None else max(0, int(beams))
    span = int(round(float(ref.get("length_seconds") or 5.0) * fps))
    animated = tinted = missing = copies = 0

    for which, end in enumerate(ends):
        rgb = end.get("explosion_tint") if colour else None
        tag = "goal%d" % which
        # The two ends fan opposite ways; the sign of the mouth's long axis is
        # the whole difference between them.
        pos = end.get("position") or [0.0, 0.0, 0.0]
        axis = max(range(3), key=lambda k: abs(float(pos[k])))
        outward = -1.0 if float(pos[axis]) > 0 else 1.0

        for role in ("burst", "shockwave"):
            for rec in end.get(role) or ():
                objects = objects_by_instance.get(int(rec.get("instance", -1))) or ()
                if not objects:
                    missing += 1
                    continue
                for obj in objects:
                    # ⭐ Only a BEAM is aimed. The reference rig is six beams and
                    # nothing else, so the fan sheet beside them (34 x 35 units,
                    # no meaningful long axis) and the rings out in the arena
                    # take the clip's TIMING and keep their authored
                    # orientation -- swinging them through beam angles would be
                    # inventing motion the level does not imply.
                    aim = _is_beam(obj)
                    fan = [(obj, ref_beams[0])]
                    if role == "burst" and want > 1 and aim:
                        for k in range(1, min(want, len(ref_beams))):
                            dup = obj.copy()       # linked: shares mesh + slots
                            dup.name = "%s_beam%d" % (obj.name, k)
                            for coll in obj.users_collection:
                                coll.objects.link(dup)
                            dup["evr_goal_explosion_generated"] = (
                                "copy %d of the reference rig's six beams; the "
                                "level ships ONE and the game instances the "
                                "rest at runtime" % k)
                            copies += 1
                            fan.append((dup, ref_beams[k]))
                    for target, beam in fan:
                        target.animation_data_clear()
                        if aim:
                            first, last = _animate_beam(target, beam, start,
                                                        fps, outward)
                        else:
                            first, last = _scale_only(target, beam, start, fps)
                        _hide_until(target, first, last)
                        _bezier(target)
                        action = getattr(getattr(target, "animation_data", None),
                                         "action", None)
                        if action is not None:
                            action.name = "EVR_goal_%d_%s_%s" % (
                                which, role, rec.get("instance"))
                        target["evr_goal_explosion"] = role
                        target["evr_goal_explosion_aimed"] = bool(aim)
                        target["evr_goal_explosion_end"] = which
                        target["evr_goal_explosion_frames"] = [first, last]
                        target["evr_goal_explosion_source"] = (
                            "motion from the demo viewer's OrangeGoalAnim clip "
                            "-- a RECONSTRUCTION, not shipped data; the props, "
                            "their goal end and both team colours ARE read "
                            "from the level")
                        if rgb:
                            target["evr_goal_explosion_tint"] = list(rgb)
                            if _tint(target, rgb, tag):
                                tinted += 1
                        animated += 1

    target_scene = scene or bpy.context.scene
    if animated and target_scene is not None:
        if target_scene.frame_end < start + span + 2:
            target_scene.frame_end = start + span + 2

    out = {"animated": animated, "tinted": tinted, "ends": len(ends),
           "beam_copies": copies, "frames": [start, start + span]}
    if missing:
        out["no_object"] = missing
    return out


def summarize(doc: dict) -> dict:
    ends = (doc or {}).get("ends") or []
    return {
        "ends": len(ends),
        "props": sum(len(e.get("burst") or ()) + len(e.get("shockwave") or ())
                     for e in ends),
        "tints": [e.get("explosion_tint") for e in ends],
    }
