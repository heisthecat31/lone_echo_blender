"""Geometric fits for the exterior (vista) objects — pure stdlib, no bpy/numpy.

Why this module exists
----------------------
docs/SCENES.md §2d states the skydome is *"a sphere of
radius 59,172.5 centred on the WORLD ORIGIN to 0.64 % RMS"* and §5c tabulates how
far Saturn and the rings stick out of it.  Both numbers were produced by a
throwaway script.  Anything a render is going to be built on top of has to be
re-derivable and testable, so the four fits the vista needs live here:

* :func:`sphere_residuals` — radius statistics about a **given** centre.  This is
  the instrument that decides "is the dome on the origin or on its own centroid",
  and it is deliberately separate from the fit so a *hypothesis* can be scored.
* :func:`fit_sphere` — algebraic least-squares sphere (centre **and** radius).
* :func:`fit_plane` — least-squares plane through a point set (the ring planes).
* :func:`ring_metrics` — in-plane radii, thickness and the axis ratio of the
  outer boundary, i.e. everything needed to say whether a ring is a circular
  annulus and where its plane cuts the play area.

⚠ **Coordinate space.** Every function here takes points in the game's NATIVE
space (Y-up, the raw `.lemesh` / `.lescatter` position blobs).  Nothing in this
module knows about Blender's Z-up basis; converting is the importer's job.

Evidence label for anything computed here: ``measured``.
"""

from __future__ import annotations

import math

__all__ = [
    "centroid", "aabb", "sphere_residuals", "fit_sphere", "fit_plane",
    "ring_metrics", "solve", "eigen_sym3", "point_plane_distance",
    "cap_extent", "angular_extent", "fit_oblate_spheroid",
]


# ---------------------------------------------------------------------------
# small linear algebra (stdlib only)
# ---------------------------------------------------------------------------

def solve(a, b):
    """Solve the dense linear system ``a @ x = b`` by Gauss-Jordan with partial
    pivoting.  `a` is an n-list of n-lists, `b` an n-list.  Raises
    ``ZeroDivisionError``-free: a singular system raises ``ValueError`` so a
    degenerate point set is a *result*, never a silent NaN."""
    n = len(b)
    m = [list(row) + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-30:
            raise ValueError("singular system")
        m[col], m[piv] = m[piv], m[col]
        d = m[col][col]
        m[col] = [v / d for v in m[col]]
        for r in range(n):
            if r == col:
                continue
            f = m[r][col]
            if f:
                m[r] = [v - f * w for v, w in zip(m[r], m[col])]
    return [m[i][n] for i in range(n)]


def eigen_sym3(c):
    """Eigen-decomposition of a symmetric 3x3 matrix by cyclic Jacobi.

    Returns ``(values, vectors)`` sorted by **descending** eigenvalue, where
    ``vectors[k]`` is the unit eigenvector for ``values[k]``.  Jacobi is used
    rather than a characteristic-polynomial closed form because the covariance of
    a ring — one eigenvalue ~1e9, one ~1e4 — is exactly the ill-conditioned case
    the closed form loses digits on.
    """
    a = [list(r) for r in c]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(64):
        off = sum(a[i][j] ** 2 for i in range(3) for j in range(3) if i != j)
        if off <= 1e-24 * max(1.0, sum(a[i][i] ** 2 for i in range(3))):
            break
        for p in range(2):
            for q in range(p + 1, 3):
                if abs(a[p][q]) < 1e-300:
                    continue
                theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
                t = math.copysign(1.0, theta) / (abs(theta) + math.sqrt(theta * theta + 1.0))
                cth = 1.0 / math.sqrt(t * t + 1.0)
                s = t * cth
                for k in range(3):
                    akp, akq = a[k][p], a[k][q]
                    a[k][p] = cth * akp - s * akq
                    a[k][q] = s * akp + cth * akq
                for k in range(3):
                    apk, aqk = a[p][k], a[q][k]
                    a[p][k] = cth * apk - s * aqk
                    a[q][k] = s * apk + cth * aqk
                for k in range(3):
                    vkp, vkq = v[k][p], v[k][q]
                    v[k][p] = cth * vkp - s * vkq
                    v[k][q] = s * vkp + cth * vkq
    vals = [a[i][i] for i in range(3)]
    vecs = [[v[0][i], v[1][i], v[2][i]] for i in range(3)]
    order = sorted(range(3), key=lambda i: -vals[i])
    return [vals[i] for i in order], [_unit(vecs[i]) for i in order]


def _unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


# ---------------------------------------------------------------------------
# basic descriptors
# ---------------------------------------------------------------------------

def centroid(points):
    n = len(points)
    if not n:
        raise ValueError("empty point set")
    return tuple(sum(p[i] for p in points) / n for i in range(3))


def aabb(points):
    if not points:
        raise ValueError("empty point set")
    lo = [min(p[i] for p in points) for i in range(3)]
    hi = [max(p[i] for p in points) for i in range(3)]
    return tuple(lo), tuple(hi)


def point_plane_distance(point, plane_point, normal):
    """Signed distance of `point` from the plane, positive along `normal`."""
    n = _unit(list(normal))
    return sum((point[i] - plane_point[i]) * n[i] for i in range(3))


# ---------------------------------------------------------------------------
# sphere
# ---------------------------------------------------------------------------

def sphere_residuals(points, centre):
    """Radius statistics of `points` about a HYPOTHESISED `centre`.

    ★ This is the test that settles "the dome is authored on the world origin":
    score `(0,0,0)` and score the vertex centroid, and compare `rms_rel`.  A
    sphere fits its true centre better than any other point, so the smaller
    `rms_rel` wins — no fit required, and no way for a fitted centre to launder
    a bad hypothesis.

    Returns a dict: ``r_min r_max r_mean rms rms_rel ratio`` where `rms_rel` is
    the RMS deviation from the mean radius as a fraction of that mean and
    `ratio` is `r_max / r_min`.
    """
    if not points:
        raise ValueError("empty point set")
    rs = [math.dist(p, centre) for p in points]
    mean = sum(rs) / len(rs)
    var = sum((r - mean) ** 2 for r in rs) / len(rs)
    rms = math.sqrt(var)
    return {
        "centre": tuple(float(c) for c in centre),
        "r_min": min(rs), "r_max": max(rs), "r_mean": mean,
        "rms": rms, "rms_rel": (rms / mean) if mean else float("inf"),
        "ratio": (max(rs) / min(rs)) if min(rs) else float("inf"),
        "count": len(rs),
    }


def fit_sphere(points):
    """Algebraic least-squares sphere fit (Coope / Pratt linearisation).

    Minimises ``sum(|p - c|^2 - R^2)^2`` by solving the linear system in
    ``u = [2cx, 2cy, 2cz, R^2 - |c|^2]`` against ``|p|^2``.  Returns the same
    dict shape as :func:`sphere_residuals` (scored at the fitted centre) plus
    ``r_fit``, the radius the fit itself reports.
    """
    if len(points) < 4:
        raise ValueError("need >= 4 points for a sphere fit")
    ata = [[0.0] * 4 for _ in range(4)]
    atb = [0.0] * 4
    for p in points:
        row = (p[0], p[1], p[2], 1.0)
        rhs = p[0] * p[0] + p[1] * p[1] + p[2] * p[2]
        for i in range(4):
            atb[i] += row[i] * rhs
            for j in range(4):
                ata[i][j] += row[i] * row[j]
    u = solve(ata, atb)
    c = (u[0] / 2.0, u[1] / 2.0, u[2] / 2.0)
    r2 = u[3] + c[0] ** 2 + c[1] ** 2 + c[2] ** 2
    out = sphere_residuals(points, c)
    out["r_fit"] = math.sqrt(r2) if r2 > 0 else 0.0
    return out


# ---------------------------------------------------------------------------
# plane / ring
# ---------------------------------------------------------------------------

def fit_plane(points):
    """Least-squares plane: the covariance eigenvector of SMALLEST eigenvalue.

    Returns ``{point, normal, rms, max_abs, thickness, flatness}`` where `point`
    is the centroid, `rms`/`max_abs` are out-of-plane distances and `flatness` is
    ``sqrt(lambda_min / lambda_max)`` — a dimensionless "how planar is this",
    ~0 for a sheet and ~1 for a blob.
    """
    if len(points) < 3:
        raise ValueError("need >= 3 points for a plane fit")
    c = centroid(points)
    cov = [[0.0] * 3 for _ in range(3)]
    for p in points:
        d = (p[0] - c[0], p[1] - c[1], p[2] - c[2])
        for i in range(3):
            for j in range(3):
                cov[i][j] += d[i] * d[j]
    n = len(points)
    cov = [[v / n for v in row] for row in cov]
    vals, vecs = eigen_sym3(cov)
    normal = vecs[2]                       # smallest eigenvalue
    ds = [point_plane_distance(p, c, normal) for p in points]
    rms = math.sqrt(sum(d * d for d in ds) / n)
    lam_max = max(abs(vals[0]), 1e-300)
    return {
        "point": c, "normal": tuple(normal),
        "rms": rms, "max_abs": max(abs(d) for d in ds),
        "thickness": max(ds) - min(ds),
        "flatness": math.sqrt(max(vals[2], 0.0) / lam_max),
        "eigenvalues": tuple(vals),
        "count": n,
    }


def ring_metrics(points, centre=None, normal=None, bins=36):
    """Annulus descriptors for a ring object, in its OWN best-fit plane.

    `centre` defaults to the plane-fit centroid; pass Saturn's fitted centre to
    ask "is this ring concentric with the planet", which is the question that
    matters for placement.  Returns:

    ``plane``            the :func:`fit_plane` result
    ``centre``           the centre radii were measured from
    ``centre_offset``    distance from `centre` to the plane-fit centroid
    ``r_inner/r_outer``  min / max in-plane radius
    ``axis_ratio``       ``min/max`` of the OUTER radius measured per azimuth
                         bin.  1.0 = a circle about `centre`.
    ``eccentricity``     ``sqrt(1 - axis_ratio^2)`` — 0 for a circle.
    ``azimuth_span_deg`` how much of the full turn is actually populated: a
                         partial arc reports < 360 and its `axis_ratio` then
                         describes the arc, not an ellipse.
    ``tilt_deg``         angle between the plane normal and world +Y (game up)

    ⚠ `axis_ratio` is an azimuthal max-radius statistic, NOT a conic fit.  It is
    computed per bin (not from the point covariance) because on a thin sampled
    boundary the covariance ratio is dominated by sample noise — measured at
    0.90 on a synthetic *circle* of 80 boundary points, which would have read as
    an eccentricity of 0.43 that is not there.  It is always reported alongside
    `r_inner`/`r_outer`/`azimuth_span_deg`, never instead of them.

    ⚠ Second bias, stated because it only ever pushes ONE way: the per-bin max
    is a *sample* max, so a bin that happens to hold no rim vertex under-reports
    its radius and `axis_ratio` is therefore a LOWER bound on circularity.  A
    tessellated annulus puts real vertices on its rim, so on shipped geometry
    the bound is tight; on interior-sampled synthetic points it is not (a
    uniformly-filled synthetic circle scores ~0.95, not 1.00).  Read a high
    `axis_ratio` as proof of a circle; never read a middling one as proof of an
    ellipse without checking `azimuth_span_deg`.
    """
    plane = fit_plane(points)
    n = list(normal) if normal is not None else list(plane["normal"])
    n = _unit(n)
    c = tuple(centre) if centre is not None else plane["point"]

    # in-plane orthonormal basis
    seed = [1.0, 0.0, 0.0] if abs(n[0]) < 0.9 else [0.0, 1.0, 0.0]
    e1 = _unit([seed[1] * n[2] - seed[2] * n[1],
                seed[2] * n[0] - seed[0] * n[2],
                seed[0] * n[1] - seed[1] * n[0]])
    e2 = _unit([n[1] * e1[2] - n[2] * e1[1],
                n[2] * e1[0] - n[0] * e1[2],
                n[0] * e1[1] - n[1] * e1[0]])

    uv = []
    for p in points:
        d = (p[0] - c[0], p[1] - c[1], p[2] - c[2])
        uv.append((sum(d[i] * e1[i] for i in range(3)),
                   sum(d[i] * e2[i] for i in range(3))))
    rs = [math.hypot(u, v) for u, v in uv]

    # outer radius per azimuth bin -> a shape statistic that is not sample-noise
    per_bin = [0.0] * bins
    for (u, v), r in zip(uv, rs):
        b = int(((math.atan2(v, u) + math.pi) / (2.0 * math.pi)) * bins) % bins
        if r > per_bin[b]:
            per_bin[b] = r
    filled = [r for r in per_bin if r > 0.0]
    axis_ratio = (min(filled) / max(filled)) if len(filled) >= 2 else 1.0
    span = 360.0 * len(filled) / bins

    cos_tilt = min(1.0, max(-1.0, abs(n[1])))
    return {
        "plane": plane,
        "centre": c,
        "centre_offset": math.dist(c, plane["point"]),
        "normal": tuple(n),
        "r_inner": min(rs), "r_outer": max(rs),
        "axis_ratio": axis_ratio,
        "eccentricity": math.sqrt(max(0.0, 1.0 - axis_ratio ** 2)),
        "azimuth_span_deg": span,
        "tilt_deg": math.degrees(math.acos(cos_tilt)),
        "count": len(points),
    }


# ---------------------------------------------------------------------------
# partial spheres — the shape the vista is ACTUALLY authored in
# ---------------------------------------------------------------------------

def cap_extent(points, centre):
    """How much of a sphere about `centre` the points actually cover.

    ★ Why this exists: none of the vista's "spheres" is a whole sphere.  Saturn
    is modelled slightly past a hemisphere and the moons are shallow caps, and a
    least-squares sphere through a shallow cap is **ill-conditioned** — the
    radius it reports is nearly free.  Publishing such a radius without saying
    how much of the sphere was sampled is how a 15-degree cap ends up quoted as
    a 72,000-unit moon.

    Returns ``{mean_dir, concentration, half_angle_deg, p95_half_angle_deg}``:
    `concentration` is the length of the mean unit direction — 0 for a full
    sphere, 1 for a point — and `half_angle_deg` is the largest angle any vertex
    makes with that mean direction.  Treat a fit with `half_angle_deg < 45` as
    indicative only.
    """
    dirs = []
    for p in points:
        d = [p[i] - centre[i] for i in range(3)]
        n = math.sqrt(sum(x * x for x in d))
        if n > 0:
            dirs.append([x / n for x in d])
    if not dirs:
        raise ValueError("no points off the centre")
    mean = [sum(d[i] for d in dirs) / len(dirs) for i in range(3)]
    conc = math.sqrt(sum(x * x for x in mean))
    if conc < 1e-12:
        return {"mean_dir": (0.0, 0.0, 0.0), "concentration": 0.0,
                "half_angle_deg": 180.0, "p95_half_angle_deg": 180.0}
    u = [x / conc for x in mean]
    angs = sorted(math.degrees(math.acos(max(-1.0, min(1.0, sum(d[i] * u[i]
                                                               for i in range(3))))))
                  for d in dirs)
    return {"mean_dir": tuple(u), "concentration": conc,
            "half_angle_deg": angs[-1],
            "p95_half_angle_deg": angs[min(len(angs) - 1, int(0.95 * len(angs)))]}


def angular_extent(points, eye=(0.0, 0.0, 0.0)):
    """Where an object sits in the sky from `eye`, and how big it looks.

    This is the composition-relevant descriptor and — unlike a radius — it is
    well conditioned for *any* shape.  Returns ``{direction, angular_radius_deg,
    angular_diameter_deg, d_min, d_max}`` where `direction` is the unit vector to
    the mean of the vertex directions and `angular_radius_deg` is the largest
    angle any vertex subtends from it.
    """
    dirs, ds = [], []
    for p in points:
        d = [p[i] - eye[i] for i in range(3)]
        n = math.sqrt(sum(x * x for x in d))
        ds.append(n)
        if n > 0:
            dirs.append([x / n for x in d])
    if not dirs:
        raise ValueError("no points off the eye")
    mean = [sum(d[i] for d in dirs) / len(dirs) for i in range(3)]
    u = _unit(mean)
    angs = []
    for d in dirs:
        dot = max(-1.0, min(1.0, sum(d[i] * u[i] for i in range(3))))
        angs.append(math.degrees(math.acos(dot)))
    return {"direction": tuple(u), "angular_radius_deg": max(angs),
            "angular_diameter_deg": 2.0 * max(angs),
            "d_min": min(ds), "d_max": max(ds), "count": len(ds)}


def _spheroid_axes(points, centre, axis):
    """Closed-form (a, c) for ``r^2/a^2 + z^2/c^2 = 1`` at a fixed centre+axis.

    Linear in ``(1/a^2, 1/c^2)``, so it is one 2x2 solve — the only thing the
    search below has to iterate over is the centre and the axis.  Returns
    ``(a, c, mean_rel_residual, max_rel_residual)`` or ``None`` when the solve
    lands on a non-elliptical (negative) solution.
    """
    n = _unit(list(axis))
    zs, rs = [], []
    for p in points:
        d = [p[i] - centre[i] for i in range(3)]
        z = sum(d[i] * n[i] for i in range(3))
        r2 = sum(x * x for x in d) - z * z
        zs.append(z)
        rs.append(math.sqrt(max(r2, 0.0)))
    s11 = sum(r ** 4 for r in rs)
    s12 = sum((r * z) ** 2 for r, z in zip(rs, zs))
    s22 = sum(z ** 4 for z in zs)
    b1 = sum(r * r for r in rs)
    b2 = sum(z * z for z in zs)
    try:
        u = solve([[s11, s12], [s12, s22]], [b1, b2])
    except ValueError:
        return None
    if u[0] <= 0.0 or u[1] <= 0.0:
        return None
    a, c = 1.0 / math.sqrt(u[0]), 1.0 / math.sqrt(u[1])
    res = [abs(math.hypot(r / a, z / c) - 1.0) for r, z in zip(rs, zs)]
    return a, c, sum(res) / len(res), max(res)


def fit_oblate_spheroid(points, axis, centre=None, refine_axis=True,
                        rounds=7, step=None):
    """Fit ``r^2/a^2 + z^2/c^2 = 1`` about `axis` — the Saturn shape.

    A sphere fit through Saturn's cap leaves a **3.4 %** RMS residual, which is
    not noise: the planet is authored OBLATE.  This fits the equatorial radius
    `a`, the polar radius `c` and the centre, by a deterministic shrinking
    pattern search (no randomness, so the answer is reproducible) with the
    closed-form ``(a, c)`` solved at every trial centre.

    `axis` is the initial polar axis — pass the RING plane normal, then read
    `axis_deviation_deg` to see whether the geometry agrees that the poles are
    perpendicular to the rings.  With `refine_axis` the axis is itself searched
    over a small tangent-plane grid.

    Returns ``{centre, axis, a, c, flattening, mean_residual, max_residual,
    axis_deviation_deg}``; `flattening` is ``1 - c/a``.
    """
    if len(points) < 6:
        raise ValueError("need >= 6 points for a spheroid fit")
    axis0 = _unit(list(axis))
    if centre is None:
        centre = fit_sphere(points)["centre"]
    c = list(centre)
    n = list(axis0)
    if step is None:
        lo, hi = aabb(points)
        step = max(hi[i] - lo[i] for i in range(3)) / 16.0

    best = _spheroid_axes(points, c, n)
    if best is None:
        raise ValueError("points do not admit a spheroid fit")
    for _ in range(rounds):
        improved = True
        while improved:
            improved = False
            for k in range(3):
                for sgn in (-1.0, 1.0):
                    trial = list(c)
                    trial[k] += sgn * step
                    r = _spheroid_axes(points, trial, n)
                    if r is not None and r[2] < best[2]:
                        best, c = r, trial
                        improved = True
            if refine_axis:
                # perturb the axis inside its own tangent plane
                seed = [1.0, 0.0, 0.0] if abs(n[0]) < 0.9 else [0.0, 1.0, 0.0]
                t1 = _unit([seed[1] * n[2] - seed[2] * n[1],
                            seed[2] * n[0] - seed[0] * n[2],
                            seed[0] * n[1] - seed[1] * n[0]])
                t2 = _unit([n[1] * t1[2] - n[2] * t1[1],
                            n[2] * t1[0] - n[0] * t1[2],
                            n[0] * t1[1] - n[1] * t1[0]])
                d = step / max(best[0], 1e-9)
                for t in (t1, t2):
                    for sgn in (-1.0, 1.0):
                        trial = _unit([n[i] + sgn * d * t[i] for i in range(3)])
                        r = _spheroid_axes(points, c, trial)
                        if r is not None and r[2] < best[2]:
                            best, n = r, trial
                            improved = True
        step *= 0.5

    dot = abs(sum(n[i] * axis0[i] for i in range(3)))
    return {
        "centre": tuple(c), "axis": tuple(n),
        "a": best[0], "c": best[1],
        "flattening": 1.0 - best[1] / best[0] if best[0] else 0.0,
        "mean_residual": best[2], "max_residual": best[3],
        "axis_deviation_deg": math.degrees(math.acos(max(-1.0, min(1.0, dot)))),
        "count": len(points),
    }
