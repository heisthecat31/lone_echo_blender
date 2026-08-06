"""Camera framing math — pure stdlib, no ``bpy``.

Every existing render harness in ``tests/`` frames its camera the same ad-hoc
way: take the bounding box, pick a direction, put the camera at
``centre + dir * size * K`` for a hand-tuned ``K``.  That works for a smoke
render and fails for a hero render, because ``K`` is only correct for one lens,
one aspect ratio and one object.  Change the focal length from 50 mm to 85 mm
for a portrait and the subject leaves the frame.

This module solves the actual question instead: **given a lens, a sensor, a
render resolution and a set of world points, where must the camera sit so that
every point lands inside the frame?**  It is pure arithmetic, so it is unit
tested under plain ``python3`` (``tests/test_framing.py``) rather than only
being eyeballed in a PNG.

Conventions match Blender exactly:

* a camera looks down its own **-Z**; its +X is screen-right and +Y is
  screen-up, so the rotation matrix's columns are ``(right, up, -view_dir)``;
* ``sensor_fit`` is ``'AUTO' | 'HORIZONTAL' | 'VERTICAL'`` and ``'AUTO'`` maps
  ``sensor_width`` onto whichever render dimension is **larger** — which is why
  a portrait (``res_y > res_x``) silently reinterprets the sensor and is the
  single easiest way to get a wrong crop;
* ``lens``/``sensor`` are millimetres, everything else is scene units.

⚠ Framing input should be **vertices**, not ``object.bound_box``: the importer
puts the Y-up→Z-up conversion on ``matrix_basis``, so ``bound_box`` and
``matrix_world`` read stale until the depsgraph has evaluated
(``mesh_builder.build_object`` docstring; ``blender_lightmap_render._frame``
pays this cost explicitly).  ``fit_view`` takes bare points precisely so the
caller cannot pass an object and hope.
"""

from __future__ import annotations

import math
from typing import Iterable, Sequence

Vec3 = Sequence[float]

__all__ = [
    "normalize",
    "cross",
    "dot",
    "sensor_extents",
    "half_angles",
    "camera_basis",
    "fit_view",
    "orbit_direction",
]


# --------------------------------------------------------------------------
# small vector helpers (a 3-vector is any indexable of three floats)
# --------------------------------------------------------------------------

def dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a: Vec3, b: Vec3) -> tuple:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def normalize(v: Vec3) -> tuple:
    n = math.sqrt(dot(v, v))
    if n <= 0.0:
        raise ValueError("cannot normalize a zero-length vector")
    return (v[0] / n, v[1] / n, v[2] / n)


# --------------------------------------------------------------------------
# lens / sensor
# --------------------------------------------------------------------------

def sensor_extents(sensor: float, res_x: int, res_y: int,
                   sensor_fit: str = "AUTO",
                   sensor_height: float | None = None) -> tuple:
    """Return ``(sensor_x_mm, sensor_y_mm)`` the way Blender resolves them.

    ``sensor`` is ``camera.sensor_width``.  ``sensor_height`` is only consulted
    for ``sensor_fit='VERTICAL'``, matching Blender, where ``sensor_height`` is
    ignored in AUTO and HORIZONTAL fits.
    """
    if res_x <= 0 or res_y <= 0:
        raise ValueError("resolution must be positive")
    fit = (sensor_fit or "AUTO").upper()
    aspect = res_x / res_y

    if fit == "AUTO":
        fit = "HORIZONTAL" if res_x >= res_y else "VERTICAL"
        if fit == "VERTICAL":
            # AUTO maps sensor_width onto the LARGER dimension; in a portrait
            # that dimension is the height, and sensor_height is not used.
            sensor_height = sensor

    if fit == "HORIZONTAL":
        return (sensor, sensor / aspect)
    if fit == "VERTICAL":
        sy = sensor if sensor_height is None else sensor_height
        return (sy * aspect, sy)
    raise ValueError(f"unknown sensor_fit {sensor_fit!r}")


def half_angles(lens: float, sensor: float, res_x: int, res_y: int,
                sensor_fit: str = "AUTO",
                sensor_height: float | None = None) -> tuple:
    """Return ``(tan_half_fov_x, tan_half_fov_y)`` for a perspective camera."""
    if lens <= 0.0:
        raise ValueError("lens must be > 0")
    sx, sy = sensor_extents(sensor, res_x, res_y, sensor_fit, sensor_height)
    return (0.5 * sx / lens, 0.5 * sy / lens)


# --------------------------------------------------------------------------
# orientation
# --------------------------------------------------------------------------

def camera_basis(eye_dir: Vec3, up_hint: Vec3 = (0.0, 0.0, 1.0)) -> tuple:
    """Orthonormal camera basis ``(right, up, back)`` for a camera placed along
    ``eye_dir`` from its target and aimed back at it.

    ``eye_dir`` points **from the target toward the camera**.  ``back`` is the
    camera's +Z (i.e. ``-view_dir``), so ``(right, up, back)`` are exactly the
    columns of Blender's camera rotation matrix.

    A degenerate ``up_hint`` (parallel to ``eye_dir`` — e.g. a straight
    top-down shot with the default world up) is not an error here: the hint is
    swung to +Y so the caller still gets a usable, deterministic basis.
    """
    back = normalize(eye_dir)
    hint = normalize(up_hint)
    right = cross(hint, back)
    if dot(right, right) < 1e-12:
        hint = (0.0, 1.0, 0.0)
        right = cross(hint, back)
        if dot(right, right) < 1e-12:      # eye_dir was ±Y as well
            hint = (1.0, 0.0, 0.0)
            right = cross(hint, back)
    right = normalize(right)
    up = cross(back, right)
    return (right, up, back)


def orbit_direction(azimuth_deg: float, elevation_deg: float) -> tuple:
    """``eye_dir`` for a turntable-style placement, Z-up.

    ``azimuth`` 0° puts the camera on -Y (Blender's "front" view) and increases
    counter-clockwise seen from +Z; ``elevation`` lifts it toward +Z.
    """
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    c = math.cos(el)
    return (math.sin(az) * c, -math.cos(az) * c, math.sin(el))


# --------------------------------------------------------------------------
# the fit
# --------------------------------------------------------------------------

def fit_view(points: Iterable[Vec3],
             eye_dir: Vec3 = (0.6, -1.0, 0.35),
             *,
             lens: float = 50.0,
             sensor: float = 36.0,
             res_x: int = 1920,
             res_y: int = 1080,
             sensor_fit: str = "AUTO",
             sensor_height: float | None = None,
             up_hint: Vec3 = (0.0, 0.0, 1.0),
             margin: float = 1.06,
             target: Vec3 | None = None,
             shift_x: float = 0.0,
             shift_y: float = 0.0) -> dict:
    """Place a perspective camera so every point in ``points`` is inside frame.

    Returns a dict with ``location``, ``basis`` (the three rotation-matrix
    columns), ``target``, ``distance``, ``clip_start``, ``clip_end``,
    ``tan_half`` and ``size``.

    ``margin`` > 1 leaves breathing room (1.06 ≈ a 6 % border).  ``shift_x`` /
    ``shift_y`` are Blender's ``camera.shift_x``/``shift_y`` in *frame widths*;
    they are honoured in the fit, so an off-centre portrait composition still
    contains the subject instead of clipping it against the shifted edge.

    The fit is exact for a perspective frustum: for a point at camera-space
    depth ``t`` and lateral offset ``x``, the constraint is
    ``|x| <= t * tan_half_x``, and every point contributes a lower bound on the
    camera distance.  The maximum of those bounds is the answer — no magic
    multiplier on the bounding-box diagonal, which is what every previous
    harness used and why none of them survived a lens change.
    """
    pts = [tuple(float(c) for c in p) for p in points]
    if not pts:
        raise ValueError("fit_view needs at least one point")

    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    size = math.dist(lo, hi)
    if target is None:
        target = tuple((lo[i] + hi[i]) * 0.5 for i in range(3))
    else:
        target = tuple(float(c) for c in target)

    right, up, back = camera_basis(eye_dir, up_hint)
    tan_x, tan_y = half_angles(lens, sensor, res_x, res_y, sensor_fit, sensor_height)

    # camera.shift_x/y move the frame by that fraction of the LARGER sensor
    # dimension; a shift eats budget on one side, so the safe half-extent is
    # the smaller of the two remaining halves.
    span = 2.0 * max(tan_x, tan_y)
    eff_x = max(tan_x - abs(shift_x) * span, 1e-6)
    eff_y = max(tan_y - abs(shift_y) * span, 1e-6)

    if margin <= 0.0:
        raise ValueError("margin must be > 0")

    distance = 0.0
    for p in pts:
        v = (p[0] - target[0], p[1] - target[1], p[2] - target[2])
        # camera-space coordinates relative to the target
        x = dot(v, right)
        y = dot(v, up)
        z = dot(v, back)          # +z is toward the camera
        distance = max(distance,
                       z + abs(x) * margin / eff_x,
                       z + abs(y) * margin / eff_y)

    if distance <= 0.0:           # every point behind the target plane
        distance = max(size, 1e-3)

    location = tuple(target[i] + back[i] * distance for i in range(3))

    depths = [distance - dot((p[0] - target[0], p[1] - target[1], p[2] - target[2]), back)
              for p in pts]
    near = min(depths)
    far = max(depths)
    clip_start = max(near * 0.5, 1e-4, size * 1e-5)
    clip_end = max(far * 2.0, clip_start * 1000.0)

    return {
        "location": location,
        "basis": (right, up, back),
        "target": target,
        "distance": distance,
        "clip_start": clip_start,
        "clip_end": clip_end,
        "tan_half": (tan_x, tan_y),
        "size": size,
        "bbox": (tuple(lo), tuple(hi)),
    }


def look_at(eye: Vec3,
            target: Vec3,
            points: Iterable[Vec3] = (),
            *,
            lens: float = 50.0,
            sensor: float = 36.0,
            res_x: int = 1920,
            res_y: int = 1080,
            sensor_fit: str = "AUTO",
            sensor_height: float | None = None,
            up_hint: Vec3 = (0.0, 0.0, 1.0)) -> dict:
    """An EXPLICITLY placed camera at ``eye`` aimed at ``target``.

    Returns the same dict shape as :func:`fit_view` so every consumer (camera
    build, light rigs, DOF) works unchanged — but the camera position is the
    caller's, not solved from the subject.

    ★ Why this exists: :func:`fit_view` always places the camera *outside* the
    point cloud, on a ray from the target.  That is right for a prop on a
    turntable and wrong for a room — an interior shot has the camera *inside*
    the geometry, so there is no orbit direction and no margin that produces it.

    ``points`` (optional, and normally the whole scene) is used only to size the
    clip planes and to report ``size``/``bbox``; it never moves the camera.
    Points *behind* the camera are ignored for ``clip_start`` — in a room most
    of the scene is behind you — so the near plane is set from the nearest
    point actually in front, floored at 1 mm.
    """
    eye = tuple(float(c) for c in eye)
    target = tuple(float(c) for c in target)
    eye_dir = (eye[0] - target[0], eye[1] - target[1], eye[2] - target[2])
    if dot(eye_dir, eye_dir) < 1e-18:
        raise ValueError("look_at needs eye != target")
    right, up, back = camera_basis(eye_dir, up_hint)
    tan_x, tan_y = half_angles(lens, sensor, res_x, res_y, sensor_fit, sensor_height)
    distance = math.sqrt(dot(eye_dir, eye_dir))

    pts = [tuple(float(c) for c in p) for p in points]
    if pts:
        lo = [min(p[i] for p in pts) for i in range(3)]
        hi = [max(p[i] for p in pts) for i in range(3)]
        size = math.dist(lo, hi)
        # depth along the view direction, measured from the EYE
        depths = [-dot((p[0] - eye[0], p[1] - eye[1], p[2] - eye[2]), back)
                  for p in pts]
        ahead = [d for d in depths if d > 0.0]
        near = min(ahead) if ahead else distance
        far = max(depths) if depths else distance
    else:
        lo = hi = list(target)
        size = distance
        near, far = distance, distance

    clip_start = max(near * 0.5, 1e-3)
    clip_end = max(far * 2.0, clip_start * 1000.0)

    return {
        "location": eye,
        "basis": (right, up, back),
        "target": target,
        "distance": distance,
        "clip_start": clip_start,
        "clip_end": clip_end,
        "tan_half": (tan_x, tan_y),
        "size": size,
        "bbox": (tuple(lo), tuple(hi)),
    }
