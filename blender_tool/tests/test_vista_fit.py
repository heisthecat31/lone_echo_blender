"""Tests for `le_mesh.vista_fit` — the sphere / plane / ring fits the exterior
(Saturn vista) build measures its placement with.

Every case here is synthetic and closed-form: a fit is only trustworthy if it
recovers a shape whose answer is known before the fit runs.  The two cases that
matter most are the ones that used to be done by eye:

* a sphere fit must return the centre it was built around, and
  `sphere_residuals` must score the TRUE centre better than a decoy — that is
  the exact instrument docs/SCENES.md §2d used to
  conclude the skydome is authored on the world origin rather than its own
  vertex centroid;
* a ring's `axis_ratio` must read 1.0 on a circle and `b/a` on an ellipse,
  because "is the ring plane circular and concentric with the planet" is a
  placement claim, not an impression.
"""

from __future__ import annotations

import math
import random

from le_mesh import vista_fit as V


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _sphere_points(centre, radius, n=600, seed=11):
    rng = random.Random(seed)
    pts = []
    for _ in range(n):
        z = rng.uniform(-1.0, 1.0)
        t = rng.uniform(0.0, 2.0 * math.pi)
        r = math.sqrt(max(0.0, 1.0 - z * z))
        pts.append((centre[0] + radius * r * math.cos(t),
                    centre[1] + radius * r * math.sin(t),
                    centre[2] + radius * z))
    return pts


def _ring_points(centre, normal, a, b, n=720, rim_only=True):
    """Points on (or filling) an ellipse of semi-axes `a`,`b` in a tilted plane.

    `rim_only` puts every point exactly on the boundary — which is what a
    tessellated annulus mesh actually gives at its outer edge, and the regime
    `axis_ratio` is exact in.
    """
    nn = V._unit(list(normal))
    seed = [0.0, 0.0, 1.0] if abs(nn[2]) < 0.9 else [1.0, 0.0, 0.0]
    e1 = V._unit(_cross(nn, seed))
    e2 = V._unit(_cross(nn, e1))
    pts = []
    for i in range(n):
        t = 2.0 * math.pi * i / n
        k = 1.0 if rim_only else (0.5 + 0.5 * ((i * 37) % 11) / 10.0)
        u, v = a * k * math.cos(t), b * k * math.sin(t)
        pts.append(tuple(centre[j] + u * e1[j] + v * e2[j] for j in range(3)))
    return pts


# ---------------------------------------------------------------------------
# linear algebra
# ---------------------------------------------------------------------------

def test_solve_recovers_a_known_solution():
    a = [[2.0, 1.0, -1.0], [-3.0, -1.0, 2.0], [-2.0, 1.0, 2.0]]
    x = V.solve(a, [8.0, -11.0, -3.0])
    for got, want in zip(x, (2.0, 3.0, -1.0)):
        assert abs(got - want) < 1e-9, x


def test_solve_refuses_a_singular_system():
    try:
        V.solve([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0])
    except ValueError:
        return
    raise AssertionError("a singular system must raise, never return NaNs")


def test_eigen_sym3_diagonalises_a_known_matrix():
    # eigenvalues 5, 2, 1 along the axes, rotated 45 deg about Z
    c = 1.0 / math.sqrt(2.0)
    r = [[c, -c, 0.0], [c, c, 0.0], [0.0, 0.0, 1.0]]
    d = [[5.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 0.0, 1.0]]
    m = [[sum(r[i][k] * d[k][l] * r[j][l] for k in range(3) for l in range(3))
          for j in range(3)] for i in range(3)]
    vals, vecs = V.eigen_sym3(m)
    assert [round(v, 9) for v in vals] == [5.0, 2.0, 1.0], vals
    for vec in vecs:
        assert abs(math.sqrt(sum(x * x for x in vec)) - 1.0) < 1e-9


def test_eigen_sym3_survives_a_ring_conditioned_covariance():
    """1e9 : 1e9 : 1e2 — the actual conditioning of a ring's covariance."""
    m = [[1.0e9, 0.0, 0.0], [0.0, 1.0e9, 0.0], [0.0, 0.0, 1.0e2]]
    vals, vecs = V.eigen_sym3(m)
    assert abs(vals[2] - 1.0e2) < 1e-3, vals
    assert abs(abs(vecs[2][2]) - 1.0) < 1e-9, vecs[2]


# ---------------------------------------------------------------------------
# sphere
# ---------------------------------------------------------------------------

def test_fit_sphere_recovers_centre_and_radius():
    pts = _sphere_points((12.0, -3.0, 7.5), 41.0)
    f = V.fit_sphere(pts)
    for got, want in zip(f["centre"], (12.0, -3.0, 7.5)):
        assert abs(got - want) < 1e-6, f["centre"]
    assert abs(f["r_fit"] - 41.0) < 1e-6, f["r_fit"]
    assert f["rms_rel"] < 1e-9, f["rms_rel"]


def test_fit_sphere_at_saturn_scale():
    """The real numbers are ~6e4 units; a fit that only works at unit scale is
    useless here."""
    pts = _sphere_points((0.0, 0.0, 0.0), 59_172.5, n=800, seed=5)
    f = V.fit_sphere(pts)
    assert abs(f["r_fit"] - 59_172.5) < 1e-3, f["r_fit"]
    assert max(abs(c) for c in f["centre"]) < 1e-3, f["centre"]


def test_sphere_residuals_prefers_the_true_centre_over_a_decoy():
    """★ The skydome test, in miniature.

    An off-centre hypothesis must score a WORSE `rms_rel` than the true centre —
    that asymmetry is the whole argument for "the dome is authored on (0,0,0),
    not on its own vertex centroid".
    """
    true_c = (0.0, 0.0, 0.0)
    pts = _sphere_points(true_c, 1000.0, n=400, seed=3)
    good = V.sphere_residuals(pts, true_c)
    decoy = V.sphere_residuals(pts, (60.0, -25.0, 40.0))
    assert good["rms_rel"] < decoy["rms_rel"], (good["rms_rel"], decoy["rms_rel"])
    assert good["ratio"] < decoy["ratio"]


def test_sphere_residuals_reports_the_fields_the_findings_quote():
    pts = _sphere_points((0.0, 0.0, 0.0), 100.0, n=200, seed=9)
    r = V.sphere_residuals(pts, (0.0, 0.0, 0.0))
    for k in ("r_min", "r_max", "r_mean", "rms", "rms_rel", "ratio", "count"):
        assert k in r, k
    assert r["count"] == 200
    assert abs(r["r_mean"] - 100.0) < 1e-9


# ---------------------------------------------------------------------------
# plane / ring
# ---------------------------------------------------------------------------

def test_fit_plane_recovers_a_tilted_plane():
    n = V._unit([0.3, 1.0, -0.2])
    pts = _ring_points((5.0, 6.0, 7.0), n, 180.0, 180.0, n=500, rim_only=False)
    p = V.fit_plane(pts)
    dot = abs(sum(p["normal"][i] * n[i] for i in range(3)))
    assert abs(dot - 1.0) < 1e-9, p["normal"]
    assert p["rms"] < 1e-6, p["rms"]
    assert p["flatness"] < 1e-6, p["flatness"]


def test_fit_plane_flatness_separates_a_sheet_from_a_blob():
    sheet = _ring_points((0.0, 0.0, 0.0), (0.0, 1.0, 0.0), 100.0, 100.0,
                         n=400, rim_only=False)
    blob = _sphere_points((0.0, 0.0, 0.0), 100.0, n=400, seed=2)
    assert V.fit_plane(sheet)["flatness"] < 1e-6
    assert V.fit_plane(blob)["flatness"] > 0.5


def test_ring_metrics_reads_a_circle_as_circular():
    pts = _ring_points((0.0, 0.0, 0.0), (0.0, 1.0, 0.0), 500.0, 500.0)
    m = V.ring_metrics(pts)
    assert abs(m["axis_ratio"] - 1.0) < 1e-6, m["axis_ratio"]
    assert m["eccentricity"] < 1e-3, m["eccentricity"]
    assert abs(m["azimuth_span_deg"] - 360.0) < 1e-9
    assert abs(m["r_outer"] - 500.0) < 1e-6


def test_ring_metrics_measures_a_known_ellipse():
    pts = _ring_points((0.0, 0.0, 0.0), (0.0, 1.0, 0.0), 200.0, 120.0)
    m = V.ring_metrics(pts)
    assert abs(m["axis_ratio"] - 0.6) < 0.02, m["axis_ratio"]
    assert abs(m["eccentricity"] - math.sqrt(1 - 0.36)) < 0.03, m["eccentricity"]


def test_ring_metrics_tilt_is_measured_against_game_up():
    flat = V.ring_metrics(_ring_points((0, 0, 0), (0.0, 1.0, 0.0), 10.0, 10.0))
    assert flat["tilt_deg"] < 1e-6, flat["tilt_deg"]
    edge = V.ring_metrics(_ring_points((0, 0, 0), (1.0, 0.0, 0.0), 10.0, 10.0))
    assert abs(edge["tilt_deg"] - 90.0) < 1e-6, edge["tilt_deg"]


def test_ring_metrics_flags_a_partial_arc():
    """A 90-degree arc must NOT be reported as a very eccentric ellipse.

    ⚠ The span is measured about the CENTRE that was passed in.  A partial arc's
    plane-fit centroid sits inside the arc rather than at the circle's centre,
    and the same arc then subtends 220 deg about that point — which is exactly
    why every ring below is measured about SATURN's fitted centre, not about its
    own centroid.  Both readings are asserted here so the difference is a
    documented property and not a trap someone rediscovers.
    """
    full = _ring_points((0, 0, 0), (0.0, 1.0, 0.0), 100.0, 100.0, n=720)
    arc = full[: len(full) // 4]
    about_centre = V.ring_metrics(arc, centre=(0.0, 0.0, 0.0))
    assert about_centre["azimuth_span_deg"] < 120.0, about_centre["azimuth_span_deg"]
    about_centroid = V.ring_metrics(arc)
    assert about_centroid["azimuth_span_deg"] > about_centre["azimuth_span_deg"]


def test_ring_metrics_centre_offset_is_measured_not_assumed():
    """Passing a decoy centre must move `centre_offset`, not silently re-centre."""
    pts = _ring_points((100.0, 0.0, 0.0), (0.0, 1.0, 0.0), 50.0, 50.0)
    m = V.ring_metrics(pts, centre=(0.0, 0.0, 0.0))
    assert abs(m["centre_offset"] - 100.0) < 1e-6, m["centre_offset"]
    assert m["axis_ratio"] < 0.9, m["axis_ratio"]     # off-centre reads as non-circular


def test_point_plane_distance_is_signed():
    d = V.point_plane_distance((0.0, 5.0, 0.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    assert abs(d - 5.0) < 1e-12
    d = V.point_plane_distance((0.0, -5.0, 0.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0))
    assert abs(d + 5.0) < 1e-12


def test_aabb_and_centroid():
    pts = [(0.0, 0.0, 0.0), (2.0, 4.0, 6.0), (-2.0, 0.0, 3.0)]
    lo, hi = V.aabb(pts)
    assert lo == (-2.0, 0.0, 0.0) and hi == (2.0, 4.0, 6.0)
    c = V.centroid(pts)
    assert abs(c[0] - 0.0) < 1e-12 and abs(c[1] - 4.0 / 3.0) < 1e-12
