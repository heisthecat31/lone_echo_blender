"""Generate a small, valid `.lescatter` package for placement testing.

Writes 3 unique meshes (a cube, a triangle, a tall box) and 8 instances at
DISTINCT translations / rotations / scales, laid out as contiguous per-mesh runs
(matching the real static-scatter layout: instance_offset = prefix sum). Geometry
is in NATIVE GAME SPACE (Y-up), exactly as the real extractor emits it.

Pure stdlib (struct/json) — no bpy, no numpy. Reusable:

    from make_synthetic_scatter import write_synthetic_scatter
    pkg_dir = write_synthetic_scatter(some_dir)   # returns the .lescatter Path

or as a CLI:  python3 make_synthetic_scatter.py [out_dir]
"""

from __future__ import annotations

import json
import math
import struct
import sys
from array import array
from pathlib import Path

# Mirror of scatter_reader.INSTANCE_STRUCT (kept literal so this stays a pure,
# self-contained producer independent of the addon package).
INSTANCE_STRUCT = "<I3f4f3f"     # mesh_index u32 | T 3f | Q(xyzw) 4f | S 3f == 44 B


# --- geometry (native game space, Y-up) --------------------------------------

def _box(hx, hy, hz):
    """A 24-vertex axis-aligned box (4 verts/face) with per-vertex face normals.

    24 unique verts -> crisp faces under smooth+split-normal shading. Returns
    (positions[flat f32], normals[flat f32], uv0[flat f32], indices[flat u32]).
    """
    # (normal, 4 corners CCW seen from outside)
    faces = [
        ((1, 0, 0),  [(hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz), (hx, -hy, hz)]),
        ((-1, 0, 0), [(-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz), (-hx, -hy, -hz)]),
        ((0, 1, 0),  [(-hx, hy, -hz), (-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz)]),
        ((0, -1, 0), [(-hx, -hy, hz), (-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz)]),
        ((0, 0, 1),  [(-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]),
        ((0, 0, -1), [(hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz)]),
    ]
    pos, nrm, uv, idx = [], [], [], []
    for fi, (n, corners) in enumerate(faces):
        base = fi * 4
        for (cx, cy, cz), (u, v) in zip(corners, [(0, 0), (1, 0), (1, 1), (0, 1)]):
            pos += [cx, cy, cz]
            nrm += [n[0], n[1], n[2]]
            uv += [u, v]
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    return pos, nrm, uv, idx


def _tri():
    """A single upright triangle (no normals key -> tests the optional path)."""
    pos = [-0.6, 0.0, 0.0,  0.6, 0.0, 0.0,  0.0, 1.2, 0.0]
    uv = [0.0, 0.0, 1.0, 0.0, 0.5, 1.0]
    idx = [0, 1, 2]
    return pos, uv, idx


# --- instance transforms -----------------------------------------------------

def _axis_quat(axis, deg):
    """Unit quaternion (x,y,z,w) for a rotation `deg` about a unit axis."""
    h = math.radians(deg) * 0.5
    s = math.sin(h)
    ax, ay, az = axis
    return (ax * s, ay * s, az * s, math.cos(h))


_IDENT = (0.0, 0.0, 0.0, 1.0)


def _instances():
    """8 instances as (mesh_index, T, Q(xyzw), S), grouped by mesh (contiguous runs)."""
    Y, X, Z = (0, 1, 0), (1, 0, 0), (0, 0, 1)
    return [
        # mesh 0 (cube) x3
        (0, (0.0, 0.0, 0.0),  _IDENT,             (1.0, 1.0, 1.0)),
        (0, (4.0, 0.0, 0.0),  _axis_quat(Y, 45),  (1.0, 1.0, 1.0)),
        (0, (-4.0, 0.0, 0.0), _axis_quat(Y, 90),  (1.5, 1.5, 1.5)),
        # mesh 1 (tri) x3
        (1, (0.0, 0.0, 4.0),  _IDENT,             (2.0, 2.0, 2.0)),
        (1, (4.0, 0.0, 4.0),  _axis_quat(X, 90),  (1.0, 1.0, 1.0)),
        (1, (-4.0, 0.0, 4.0), _axis_quat(Z, 30),  (1.0, 1.0, 1.0)),
        # mesh 2 (tall box) x2
        (2, (0.0, 3.0, -4.0), _IDENT,             (1.0, 1.0, 1.0)),
        (2, (6.0, 0.0, -4.0), _axis_quat(Y, 60),  (0.5, 2.0, 0.5)),
    ]


# --- writer ------------------------------------------------------------------

def _wfloats(path, flat):
    array("f", flat).tofile(open(path, "wb"))


def _wuints(path, flat):
    array("I", flat).tofile(open(path, "wb"))


def _aabb(pos):
    xs, ys, zs = pos[0::3], pos[1::3], pos[2::3]
    return [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]


def write_synthetic_scatter(out_dir, name: str = "synthetic") -> Path:
    """Write `<out_dir>/<name>.lescatter/` and return its Path."""
    pkg = Path(out_dir) / f"{name}.lescatter"
    blobs = pkg / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)

    cube_pos, cube_nrm, cube_uv, cube_idx = _box(0.5, 0.5, 0.5)
    tri_pos, tri_uv, tri_idx = _tri()
    tall_pos, tall_nrm, tall_uv, tall_idx = _box(0.4, 1.4, 0.4)

    insts = _instances()

    # per-mesh instance counts -> contiguous offsets (prefix sum)
    counts = [sum(1 for it in insts if it[0] == m) for m in range(3)]
    offsets = [0, counts[0], counts[0] + counts[1]]

    meshes = []

    # mesh 0: cube (pos + nrm + uv0 + idx)
    _wfloats(blobs / "m0_pos.bin", cube_pos)
    _wfloats(blobs / "m0_nrm.bin", cube_nrm)
    _wfloats(blobs / "m0_uv0.bin", cube_uv)
    _wuints(blobs / "m0_idx.bin", cube_idx)
    lo, hi = _aabb(cube_pos)
    meshes.append({
        "index": 0, "name_hash": "00000000cube0001", "matidx": 0, "shdidx": 0,
        "aabb_min": lo, "aabb_max": hi,
        "instance_offset": offsets[0], "instance_count": counts[0],
        "nverts": len(cube_pos) // 3, "nindices": len(cube_idx),
        "positions": "blobs/m0_pos.bin", "normals": "blobs/m0_nrm.bin",
        "uv0": "blobs/m0_uv0.bin", "indices": "blobs/m0_idx.bin", "proxy": False,
    })

    # mesh 1: triangle (pos + uv0 + idx; NO normals -> optional-absent path)
    _wfloats(blobs / "m1_pos.bin", tri_pos)
    _wfloats(blobs / "m1_uv0.bin", tri_uv)
    _wuints(blobs / "m1_idx.bin", tri_idx)
    lo, hi = _aabb(tri_pos)
    meshes.append({
        "index": 1, "name_hash": "00000000tri00002", "matidx": 1, "shdidx": 1,
        "aabb_min": lo, "aabb_max": hi,
        "instance_offset": offsets[1], "instance_count": counts[1],
        "nverts": len(tri_pos) // 3, "nindices": len(tri_idx),
        "positions": "blobs/m1_pos.bin", "uv0": "blobs/m1_uv0.bin",
        "indices": "blobs/m1_idx.bin", "proxy": False,
    })

    # mesh 2: tall box (pos + nrm + idx; NO uv0 -> optional-absent path)
    _wfloats(blobs / "m2_pos.bin", tall_pos)
    _wfloats(blobs / "m2_nrm.bin", tall_nrm)
    _wuints(blobs / "m2_idx.bin", tall_idx)
    lo, hi = _aabb(tall_pos)
    meshes.append({
        "index": 2, "name_hash": "0000000tallbox03", "matidx": 2, "shdidx": 2,
        "aabb_min": lo, "aabb_max": hi,
        "instance_offset": offsets[2], "instance_count": counts[2],
        "nverts": len(tall_pos) // 3, "nindices": len(tall_idx),
        "positions": "blobs/m2_pos.bin", "normals": "blobs/m2_nrm.bin",
        "indices": "blobs/m2_idx.bin", "proxy": False,
    })

    # instances.bin (global order, 44 B each)
    with open(blobs / "instances.bin", "wb") as fh:
        for mesh_index, t, q, s in insts:
            fh.write(struct.pack(INSTANCE_STRUCT, mesh_index,
                                 t[0], t[1], t[2], q[0], q[1], q[2], q[3],
                                 s[0], s[1], s[2]))

    manifest = {
        "format": "le_scatter", "version": 1,
        "master": "0000000000ca77e5", "axis": "native",
        "num_meshes": len(meshes), "num_instances": len(insts),
        "meshes": meshes, "instances_blob": "blobs/instances.bin",
    }
    (pkg / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return pkg


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    pkg = write_synthetic_scatter(out)
    print(f"wrote {pkg}  ({pkg / 'manifest.json'})")


if __name__ == "__main__":
    main()
