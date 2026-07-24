"""Archive-free core tests for the `.lescatter` reader + placement math (no bpy).

Covers the two pure, correctness-critical parts the Blender operator delegates to:
  * `scatter_reader` — manifest + instances.bin parsing (on a real synthetic pkg).
  * `compose_instance_matrix` — the B @ (T @ R @ S) placement transform, asserting
    known instances map to the expected Blender world positions.

Runs under `python3 blender_tool/tests/run_tests.py` and unchanged under pytest.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

# The pure reader lives inside the addon package; import it standalone (WITHOUT
# triggering the package __init__, which imports bpy).
_ADDON = Path(__file__).resolve().parents[1] / "addon" / "lone_echo_import"
if str(_ADDON) not in sys.path:
    sys.path.insert(0, str(_ADDON))

import scatter_reader  # noqa: E402
from scatter_reader import (  # noqa: E402
    ScatterPackage, read_instances, compose_instance_matrix, basis_matrix,
    transform_point, quat_to_matrix,
)
import make_synthetic_scatter  # noqa: E402  (tests dir is on sys.path via run_tests)

TOL = 1e-5


def _vclose(a, b, tol=TOL):
    return all(abs(a[i] - b[i]) <= tol for i in range(3))


def _axis_quat(axis, deg):
    h = math.radians(deg) * 0.5
    s = math.sin(h)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(h))


# --- reader ------------------------------------------------------------------

def test_instance_stride_is_44():
    assert scatter_reader.INSTANCE_STRIDE == 44


def test_reader_manifest_and_meshes(tmp_path):
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    pkg = ScatterPackage(pkg_dir)
    assert pkg.manifest["format"] == "le_scatter"
    assert pkg.num_meshes == 3
    assert pkg.num_instances == 8
    # contiguous per-mesh runs: offsets are the prefix sum of counts
    offs = [m["instance_offset"] for m in pkg.meshes]
    cnts = [m["instance_count"] for m in pkg.meshes]
    assert cnts == [3, 3, 2]
    assert offs == [0, 3, 6]
    # optional-key coverage: tri has no normals, tall box has no uv0
    by_idx = {m["index"]: m for m in pkg.meshes}
    assert "normals" in by_idx[0] and "uv0" in by_idx[0]
    assert "normals" not in by_idx[1]           # tri
    assert "uv0" not in by_idx[2]               # tall box


def test_reader_geometry_blobs(tmp_path):
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    pkg = ScatterPackage(pkg_dir)
    cube = pkg.meshes[0]
    pos = pkg.positions(cube)
    idx = pkg.indices(cube)
    assert len(pos) == cube["nverts"] * 3 == 24 * 3
    assert len(idx) == cube["nindices"] == 36     # 12 tris
    assert pkg.normals(cube) is not None
    assert pkg.uv0(cube) is not None
    # tri has no normals blob -> None
    assert pkg.normals(pkg.meshes[1]) is None
    # tall box has no uv0 blob -> None
    assert pkg.uv0(pkg.meshes[2]) is None


def test_reader_instances_roundtrip(tmp_path):
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    pkg = ScatterPackage(pkg_dir)
    recs = read_instances(pkg)
    assert len(recs) == 8
    assert [r.index for r in recs] == list(range(8))
    # instance 0: cube at origin, identity, unit scale
    r0 = recs[0]
    assert r0.mesh_index == 0
    assert _vclose(r0.translation, (0.0, 0.0, 0.0))
    assert _vclose(r0.scale, (1.0, 1.0, 1.0))
    # instance 1: cube at (4,0,0), 45deg about game-Y
    r1 = recs[1]
    assert _vclose(r1.translation, (4.0, 0.0, 0.0))
    exp_q = _axis_quat((0, 1, 0), 45)
    assert all(abs(r1.rotation[i] - exp_q[i]) <= 1e-5 for i in range(4))
    # instance 6: tall box elevated in game-Y
    assert recs[6].mesh_index == 2
    assert _vclose(recs[6].translation, (0.0, 3.0, -4.0))


# --- placement math (B @ T @ R @ S) ------------------------------------------

def test_basis_maps_yup_to_zup():
    # game (x,y,z) -> Blender (x,-z,y); pure rotation, determinant +1
    B = basis_matrix(True)
    assert _vclose(transform_point(B, (1, 2, 3)), (1.0, -3.0, 2.0))
    r = [row[:3] for row in B[:3]]
    det = (r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
           - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
           + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0]))
    assert abs(det - 1.0) < 1e-9


def test_basis_identity_when_disabled():
    B = basis_matrix(False)
    assert _vclose(transform_point(B, (1, 2, 3)), (1.0, 2.0, 3.0))


def test_translation_only_instance():
    # mesh origin of a translated, unrotated, unit-scale instance lands at B@T
    M = compose_instance_matrix((2.0, 3.0, 5.0), (0, 0, 0, 1), (1, 1, 1))
    assert _vclose(transform_point(M, (0, 0, 0)), (2.0, -5.0, 3.0))


def test_rotation_maps_point():
    # 90deg about game-Y sends local +X -> game (0,0,-1); B then -> Blender (0,1,0)
    M = compose_instance_matrix((0, 0, 0), _axis_quat((0, 1, 0), 90), (1, 1, 1))
    assert _vclose(transform_point(M, (1, 0, 0)), (0.0, 1.0, 0.0))


def test_nonuniform_scale():
    # scale (2,3,1): local (1,0,0)->(2,0,0)->B(2,0,0); local (0,1,0)->(0,3,0)->B(0,0,3)
    M = compose_instance_matrix((0, 0, 0), (0, 0, 0, 1), (2, 3, 1))
    assert _vclose(transform_point(M, (1, 0, 0)), (2.0, 0.0, 0.0))
    assert _vclose(transform_point(M, (0, 1, 0)), (0.0, 0.0, 3.0))


def test_combined_trs_known_point():
    # rotate local +X by 90deg-Y, scale x2, translate (10,0,0):
    #   S: (1,0,0)->(2,0,0);  R(Y90): ->(0,0,-2);  T: ->(10,0,-2);  B: ->(10,2,0)
    M = compose_instance_matrix((10.0, 0.0, 0.0), _axis_quat((0, 1, 0), 90), (2, 2, 2))
    assert _vclose(transform_point(M, (1, 0, 0)), (10.0, 2.0, 0.0))


def test_quat_identity_is_identity_rotation():
    R = quat_to_matrix(0, 0, 0, 1)
    assert _vclose(transform_point(R, (1, 2, 3)), (1.0, 2.0, 3.0))
