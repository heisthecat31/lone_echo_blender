"""Camera framing math (`le_mesh.framing`) — the fit, not a picture.

These tests exist because the previous harnesses framed with
`centre + dir * size * K` and a hand-tuned `K`.  That is only right for one
lens and one aspect ratio, so the failures it produces are silent: the subject
leaves the frame and the render still "succeeds".  Every assertion below
re-projects the fitted camera and checks containment analytically.
"""

import math

from le_mesh import framing as fr


CUBE = [(x, y, z) for x in (-1.0, 1.0) for y in (-1.0, 1.0) for z in (-1.0, 1.0)]


def _project(fit, p):
    """Return (ndc_x, ndc_y, depth) of a world point under a fit result."""
    right, up, back = fit["basis"]
    loc = fit["location"]
    v = (p[0] - loc[0], p[1] - loc[1], p[2] - loc[2])
    depth = -fr.dot(v, back)               # camera looks down -Z => +depth ahead
    tan_x, tan_y = fit["tan_half"]
    return (fr.dot(v, right) / (depth * tan_x),
            fr.dot(v, up) / (depth * tan_y),
            depth)


# --------------------------------------------------------------------------
# sensor / fov
# --------------------------------------------------------------------------

def test_sensor_fit_auto_follows_the_longer_side():
    # landscape: sensor_width is the horizontal extent
    assert fr.sensor_extents(36.0, 1920, 1080, "AUTO") == (36.0, 36.0 / (1920 / 1080))
    # portrait: AUTO re-maps sensor_width onto the VERTICAL -- the trap
    sx, sy = fr.sensor_extents(36.0, 1080, 1920, "AUTO")
    assert math.isclose(sy, 36.0)
    assert math.isclose(sx, 36.0 * (1080 / 1920))


def test_sensor_fit_horizontal_is_orientation_blind():
    assert fr.sensor_extents(36.0, 1080, 1920, "HORIZONTAL")[0] == 36.0
    assert fr.sensor_extents(36.0, 1920, 1080, "HORIZONTAL")[0] == 36.0


def test_sensor_fit_vertical_uses_sensor_height():
    sx, sy = fr.sensor_extents(36.0, 1920, 1080, "VERTICAL", sensor_height=24.0)
    assert math.isclose(sy, 24.0)
    assert math.isclose(sx, 24.0 * (1920 / 1080))


def test_half_angle_matches_the_lens_formula():
    tan_x, _ = fr.half_angles(50.0, 36.0, 1920, 1080)
    assert math.isclose(2 * math.degrees(math.atan(tan_x)), 39.5978, abs_tol=1e-3)


# --------------------------------------------------------------------------
# basis
# --------------------------------------------------------------------------

def test_camera_basis_is_orthonormal_and_right_handed():
    right, up, back = fr.camera_basis((0.6, -1.0, 0.35))
    for v in (right, up, back):
        assert math.isclose(math.sqrt(fr.dot(v, v)), 1.0, abs_tol=1e-12)
    assert abs(fr.dot(right, up)) < 1e-12
    assert abs(fr.dot(right, back)) < 1e-12
    assert abs(fr.dot(up, back)) < 1e-12
    # columns (right, up, back) must form a right-handed frame: right x up == back
    assert all(math.isclose(a, b, abs_tol=1e-12)
               for a, b in zip(fr.cross(right, up), back))


def test_camera_basis_survives_a_degenerate_up_hint():
    # straight down: eye_dir parallel to the default up hint
    right, up, back = fr.camera_basis((0.0, 0.0, 1.0))
    assert math.isclose(math.sqrt(fr.dot(up, up)), 1.0, abs_tol=1e-12)
    assert abs(fr.dot(right, back)) < 1e-12


def test_orbit_direction_zero_azimuth_is_the_front_view():
    d = fr.orbit_direction(0.0, 0.0)
    assert math.isclose(d[0], 0.0, abs_tol=1e-12)
    assert math.isclose(d[1], -1.0, abs_tol=1e-12)
    assert math.isclose(d[2], 0.0, abs_tol=1e-12)


# --------------------------------------------------------------------------
# the fit itself
# --------------------------------------------------------------------------

def test_every_point_lands_inside_the_frame():
    for lens in (24.0, 35.0, 50.0, 85.0, 135.0):
        for res in ((1920, 1080), (1080, 1920), (1000, 1000)):
            fit = fr.fit_view(CUBE, (0.6, -1.0, 0.35), lens=lens,
                              res_x=res[0], res_y=res[1], margin=1.0)
            for p in CUBE:
                nx, ny, depth = _project(fit, p)
                assert depth > 0.0, (lens, res, p)
                assert abs(nx) <= 1.0 + 1e-9, (lens, res, p, nx)
                assert abs(ny) <= 1.0 + 1e-9, (lens, res, p, ny)


def test_the_fit_is_tight_at_least_one_point_touches_an_edge():
    fit = fr.fit_view(CUBE, (0.6, -1.0, 0.35), lens=50.0, margin=1.0)
    worst = max(max(abs(_project(fit, p)[0]), abs(_project(fit, p)[1])) for p in CUBE)
    assert math.isclose(worst, 1.0, abs_tol=1e-9), worst


def test_longer_lens_needs_a_longer_throw():
    d = [fr.fit_view(CUBE, lens=L)["distance"] for L in (24.0, 50.0, 85.0, 135.0)]
    assert d == sorted(d)
    # a perspective fit is very nearly linear in focal length for a small subject
    assert d[3] / d[0] > 4.0


def test_margin_pushes_the_camera_back():
    tight = fr.fit_view(CUBE, margin=1.0)["distance"]
    loose = fr.fit_view(CUBE, margin=1.25)["distance"]
    assert loose > tight
    fit = fr.fit_view(CUBE, margin=1.25)
    worst = max(max(abs(_project(fit, p)[0]), abs(_project(fit, p)[1])) for p in CUBE)
    assert worst < 1.0


def test_shift_is_honoured_so_an_offcentre_crop_still_contains_the_subject():
    fit = fr.fit_view(CUBE, lens=85.0, res_x=1080, res_y=1920,
                      margin=1.0, shift_x=0.15)
    tan_x, tan_y = fit["tan_half"]
    span = 2.0 * max(tan_x, tan_y)
    for p in CUBE:
        right, up, back = fit["basis"]
        loc = fit["location"]
        v = (p[0] - loc[0], p[1] - loc[1], p[2] - loc[2])
        depth = -fr.dot(v, back)
        # apply the shift the way Blender does: the frame moves, the point does not
        nx = (fr.dot(v, right) - 0.15 * span * depth) / (depth * tan_x)
        assert abs(nx) <= 1.0 + 1e-9, (p, nx)


def test_clip_planes_bracket_the_subject():
    far_cube = [(p[0], p[1] + 500.0, p[2]) for p in CUBE]
    fit = fr.fit_view(far_cube, lens=50.0)
    for p in far_cube:
        depth = _project(fit, p)[2]
        assert fit["clip_start"] < depth < fit["clip_end"]
    assert fit["clip_start"] > 0.0


def test_explicit_target_is_respected_and_still_contains_everything():
    aim = (0.0, 0.0, 1.0)                       # aim at the top of the cube
    fit = fr.fit_view(CUBE, target=aim, lens=50.0, margin=1.0)
    assert fit["target"] == aim
    for p in CUBE:
        nx, ny, depth = _project(fit, p)
        assert depth > 0.0
        assert abs(nx) <= 1.0 + 1e-9 and abs(ny) <= 1.0 + 1e-9


def test_a_single_point_does_not_divide_by_zero():
    fit = fr.fit_view([(3.0, 4.0, 5.0)])
    assert fit["distance"] > 0.0
    assert fit["clip_end"] > fit["clip_start"] > 0.0


def test_empty_and_bad_inputs_raise():
    for bad in (lambda: fr.fit_view([]),
                lambda: fr.fit_view(CUBE, lens=0.0),
                lambda: fr.fit_view(CUBE, margin=0.0),
                lambda: fr.fit_view(CUBE, res_x=0),
                lambda: fr.sensor_extents(36.0, 100, 100, "SIDEWAYS"),
                lambda: fr.normalize((0.0, 0.0, 0.0))):
        try:
            bad()
        except ValueError:
            continue
        raise AssertionError("expected ValueError")


# --------------------------------------------------------------------------
# look_at — the EXPLICIT camera, for interiors the orbit fit cannot reach
# --------------------------------------------------------------------------

def test_look_at_puts_the_camera_exactly_where_it_was_told():
    fit = fr.look_at((1.0, -2.0, 3.0), (0.0, 0.0, 0.0), CUBE)
    assert fit["location"] == (1.0, -2.0, 3.0)
    assert fit["target"] == (0.0, 0.0, 0.0)
    assert abs(fit["distance"] - math.sqrt(14.0)) < 1e-9


def test_look_at_aims_at_the_target():
    fit = fr.look_at((0.0, -5.0, 0.0), (0.0, 0.0, 0.0), CUBE)
    nx, ny, depth = _project(fit, (0.0, 0.0, 0.0))
    assert depth > 0.0
    assert abs(nx) < 1e-9 and abs(ny) < 1e-9


def test_look_at_can_stand_inside_the_point_cloud():
    """The whole point: fit_view always parks the camera OUTSIDE the cloud."""
    inside = (0.0, 0.0, 0.0)                    # dead centre of CUBE
    fit = fr.look_at(inside, (1.0, 1.0, 1.0), CUBE)
    assert fit["location"] == inside
    behind = [p for p in CUBE if _project(fit, p)[2] < 0.0]
    assert behind, "a camera inside a cloud must have points behind it"
    assert fit["clip_start"] > 0.0


def test_look_at_near_plane_ignores_points_behind_the_camera():
    fit = fr.look_at((0.0, 0.0, 0.0), (0.0, 1.0, 0.0), CUBE)
    ahead = [_project(fit, p)[2] for p in CUBE if _project(fit, p)[2] > 0.0]
    assert fit["clip_start"] <= min(ahead)
    assert fit["clip_end"] > fit["clip_start"]


def test_look_at_returns_the_same_shape_as_fit_view():
    a = fr.fit_view(CUBE)
    b = fr.look_at((5.0, 0.0, 0.0), (0.0, 0.0, 0.0), CUBE)
    assert set(a) == set(b)
    right, up, back = b["basis"]
    for v in (right, up, back):
        assert abs(fr.dot(v, v) - 1.0) < 1e-9
    assert abs(fr.dot(right, up)) < 1e-9 and abs(fr.dot(up, back)) < 1e-9


def test_look_at_without_points_still_gives_usable_clipping():
    fit = fr.look_at((0.0, -3.0, 0.0), (0.0, 0.0, 0.0))
    assert fit["clip_end"] > fit["clip_start"] > 0.0


def test_look_at_rejects_a_degenerate_aim():
    try:
        fr.look_at((1.0, 1.0, 1.0), (1.0, 1.0, 1.0), CUBE)
    except ValueError:
        return
    raise AssertionError("expected ValueError when eye == target")
