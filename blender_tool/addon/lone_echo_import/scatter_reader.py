"""`.lescatter` reader + placement math — pure stdlib, NO bpy / NO numpy / NO oodle.

Consumes the static-scatter package produced offline by the extractor
(scripts/le_static_scatter.py + le_mesh packaging). It parses the manifest + raw
blobs and — crucially — owns the ONE coordinate-math function the addon relies on
(`compose_instance_matrix`), so that math is unit-tested archive-free under plain
`python3` and reused verbatim by the Blender operator (which only wraps the result
in a `mathutils.Matrix`).

PINNED PACKAGE CONTRACT (`<name>.lescatter/`):

    manifest.json = {
      "format":"le_scatter","version":2,"master":"hex","axis":"native",
      "num_meshes":M,"num_instances":N,
      "meshes":[ {"index":m,"name_hash":"hex16","matidx":int,"shdidx":int,
         "draws":[ {"matidx":int,"shdidx":int,"idx_start":int,"idx_count":int},
                   ... ],                     # v2: EVERY draw, renderparam order;
                                              # idx_start/idx_count are MESH-RELATIVE
                                              # positions into this mesh's `indices`;
                                              # draws[0] == the top-level pair.
         "aabb_min":[x,y,z],"aabb_max":[x,y,z],"instance_offset":int,
         "instance_count":int,"nverts":int,"nindices":int,
         "positions":"blobs/m<m>_pos.bin",   # float32 x3 x nverts, LE, GAME space (Y-up)
         "normals":"blobs/m<m>_nrm.bin",      # optional (key absent if none)
         "uv0":"blobs/m<m>_uv0.bin",          # optional
         "indices":"blobs/m<m>_idx.bin",      # uint32 x nindices, LE
         "proxy":bool }, ... ],
      "instances_blob":"blobs/instances.bin",  # N records, GLOBAL order i=0..N-1,
                                               # 44 B each, LE:
    #   mesh_index:u32, tx,ty,tz:f32, qx,qy,qz,qw:f32 (xyzw), sx,sy,sz:f32
      "lod": {                                 # v3, optional
         "blob":"blobs/instance_lod.bin",      # N records PARALLEL to instances.bin,
                                               # 12 B each, LE:
    #      lod_group:u32 (0xFFFFFFFF == none), lod_level:u32, lod_group_levels:u32
         "num_groups":int, "max_level":int, "levels_histogram":{"0":n, ...} }
    }
    #
    # v1 packages (version 1, no per-mesh "draws" key) still load unchanged; the
    # reader normalizes them to a single whole-buffer draw via `ScatterPackage.draws`.
    # v1/v2 have no "lod" block -- `read_instance_lod` then reports one level for
    # every instance, so `filter_by_lod` is a no-op and old packages behave as before.
    # Only `format` is validated on load (all versions are accepted).
    #
    # WHY LOD MATTERS HERE: every LOD level of a prop is a separate mesh with its
    # own instances in the same master, so importing all N instances stacks all
    # levels on top of each other (61.3 % of station_front's 21,394 instances are
    # lower-LOD duplicates). See `le_mesh.static_lod`.

Geometry blobs are in NATIVE GAME SPACE (Y-up) and are NOT axis-converted here —
the addon builds each unique mesh datablock once in native space and applies the
Y-up->Z-up basis (B) on the per-instance OBJECT matrix, never baked into the mesh.
"""

from __future__ import annotations

import json
import math
import struct
from array import array
from dataclasses import dataclass
from pathlib import Path

SCATTER_FORMAT = "le_scatter"
SCATTER_VERSION = 1

# instances.bin record: mesh_index u32 | translation 3xf32 | rotation 4xf32 (xyzw)
# | scale 3xf32  ==  4 + 12 + 16 + 12 = 44 bytes, little-endian.
INSTANCE_STRUCT = "<I3f4f3f"
INSTANCE_STRIDE = struct.calcsize(INSTANCE_STRUCT)   # 44
assert INSTANCE_STRIDE == 44, INSTANCE_STRIDE

# instance_lod.bin record (v3): lod_group u32 | lod_level u32 | lod_group_levels u32
INSTANCE_LOD_STRUCT = "<3I"
INSTANCE_LOD_STRIDE = struct.calcsize(INSTANCE_LOD_STRUCT)   # 12
assert INSTANCE_LOD_STRIDE == 12, INSTANCE_LOD_STRIDE

LOD_NONE = 0xFFFFFFFF   # `lod_group` sentinel: this instance has no LOD group
LOD_ALL = -1            # keep every instance (all levels stacked)
LOD_COARSEST = -2       # keep each group's last level


# ---------------------------------------------------------------------------
# Package parsing (pure stdlib; array-based blob loads)
# ---------------------------------------------------------------------------

@dataclass
class InstanceRecord:
    index: int            # global instance index i (0..N-1)
    mesh_index: int       # which mesh datablock this instance draws
    translation: tuple    # (tx, ty, tz)  game-space
    rotation: tuple       # (qx, qy, qz, qw)  xyzw, unit quaternion
    scale: tuple          # (sx, sy, sz)


@dataclass
class InstanceLod:
    """v3 LOD binding for one instance (see `le_mesh.static_lod`)."""
    group: int            # LOD-group / node id, or -1 when ungrouped
    level: int            # 0 = highest detail
    group_levels: int     # how many levels this instance's group has (>= 1)


class ScatterPackage:
    """A parsed `<name>.lescatter/` directory (manifest + blobs), stdlib only."""

    def __init__(self, pkg_dir):
        pkg_dir = Path(pkg_dir)
        if pkg_dir.name == "manifest.json":
            pkg_dir = pkg_dir.parent
        self.dir = pkg_dir
        self.manifest = json.loads(
            (self.dir / "manifest.json").read_text(encoding="utf-8"))
        fmt = self.manifest.get("format")
        if fmt != SCATTER_FORMAT:
            raise ValueError(
                f"{self.dir}: not a {SCATTER_FORMAT} package (format={fmt!r})")

    @property
    def meshes(self):
        return self.manifest.get("meshes", [])

    @property
    def num_meshes(self):
        return int(self.manifest.get("num_meshes", len(self.meshes)))

    @property
    def num_instances(self):
        return int(self.manifest.get("num_instances", 0))

    @property
    def master(self):
        return self.manifest.get("master", "")

    # --- blob loads (flat array.array; supports the buffer protocol for
    #     Blender foreach_set, and plain indexing for tests) ------------------

    def _floats(self, rel):
        a = array("f")
        a.frombytes((self.dir / rel).read_bytes())
        return a

    def _uints(self, rel):
        a = array("I")
        a.frombytes((self.dir / rel).read_bytes())
        return a

    def positions(self, mesh):
        """Flat float32 [x,y,z, ...] in native game space, or [] if absent."""
        rel = mesh.get("positions")
        return self._floats(rel) if rel else array("f")

    def normals(self, mesh):
        """Flat float32 [nx,ny,nz, ...], or None when the mesh has no normals."""
        rel = mesh.get("normals")
        return self._floats(rel) if rel else None

    def uv0(self, mesh):
        """Flat float32 [u,v, ...], or None when the mesh has no uv0."""
        rel = mesh.get("uv0")
        return self._floats(rel) if rel else None

    def indices(self, mesh):
        """Flat uint32 triangle indices, or [] if absent."""
        rel = mesh.get("indices")
        return self._uints(rel) if rel else array("I")

    @property
    def lod(self):
        """The v3 `lod` manifest block, or `{}` for v1/v2 packages."""
        return self.manifest.get("lod") or {}

    @property
    def max_lod_level(self):
        """Coarsest LOD level present, or 0 when the package carries no LOD."""
        return int(self.lod.get("max_level", 0))

    @staticmethod
    def draws(mesh):
        """Normalized draw list for a mesh entry (v2 native, v1 back-compat).

        v2 (has "draws"): the stored list of {matidx,shdidx,idx_start,idx_count}.
        v1 (no "draws" key): one synthetic whole-buffer draw = the mesh's top-level
        (matidx, shdidx) over [0, nindices), so a v1 mesh reads as a single draw.
        Pure / bpy-free; `idx_start`/`idx_count` are mesh-relative index positions.
        """
        if "draws" in mesh:
            return mesh["draws"]
        return [{"matidx": mesh["matidx"], "shdidx": mesh["shdidx"],
                 "idx_start": 0, "idx_count": mesh["nindices"]}]


def read_instances(pkg: ScatterPackage) -> list:
    """Parse `instances_blob` into InstanceRecord list (global order i=0..N-1)."""
    rel = pkg.manifest["instances_blob"]
    data = (pkg.dir / rel).read_bytes()
    n = pkg.num_instances
    need = n * INSTANCE_STRIDE
    if len(data) < need:
        raise ValueError(
            f"{rel}: {len(data)} B < num_instances*{INSTANCE_STRIDE} ({need})")
    recs = []
    for i in range(n):
        v = struct.unpack_from(INSTANCE_STRUCT, data, i * INSTANCE_STRIDE)
        recs.append(InstanceRecord(
            index=i, mesh_index=v[0],
            translation=(v[1], v[2], v[3]),
            rotation=(v[4], v[5], v[6], v[7]),
            scale=(v[8], v[9], v[10])))
    return recs


def read_instance_lod(pkg: ScatterPackage) -> list:
    """Parse the v3 `lod` blob into `InstanceLod` records, parallel to instances.

    A v1/v2 package (or a v3 one whose blob is missing/short) yields
    `InstanceLod(-1, 0, 1)` for every instance — one level each — so downstream
    filtering degrades to "keep everything" instead of failing.
    """
    n = pkg.num_instances
    rel = pkg.lod.get("blob")
    if not rel or not (pkg.dir / rel).exists():
        return [InstanceLod(-1, 0, 1) for _ in range(n)]
    data = (pkg.dir / rel).read_bytes()
    if len(data) < n * INSTANCE_LOD_STRIDE:
        return [InstanceLod(-1, 0, 1) for _ in range(n)]
    out = []
    for i in range(n):
        g, lv, gl = struct.unpack_from(INSTANCE_LOD_STRUCT, data, i * INSTANCE_LOD_STRIDE)
        out.append(InstanceLod(-1 if g == LOD_NONE else g, lv, max(1, gl)))
    return out


def filter_by_lod(instances, lods, level):
    """Keep the instances that belong to LOD `level`, clamped per group.

    * `level >= 0` — that level, but never past a group's coarsest one, so a
      2-level prop asked for LOD 3 still contributes its LOD 1 rather than
      vanishing.
    * `LOD_ALL` (-1) — every instance (all levels stacked; the v1/v2 behaviour).
    * `LOD_COARSEST` (-2) — each group's last level.

    `instances` and `lods` must be parallel (same order, same length). Returns a
    new list of the kept `InstanceRecord`s.
    """
    if level == LOD_ALL:
        return list(instances)
    kept = []
    for inst, lod in zip(instances, lods):
        want = lod.group_levels - 1 if level == LOD_COARSEST else min(level, lod.group_levels - 1)
        if lod.level == want:
            kept.append(inst)
    return kept


# ---------------------------------------------------------------------------
# Multi-material face slotting — pure / bpy-free (the ONE place per-draw face
# assignment is defined, so build_scatter_mesh delegates here and it is unit
# tested archive-free). Mirrors the .lemesh `mesh_builder` degenerate/OOB filter.
# ---------------------------------------------------------------------------

def assign_face_materials(indices, draws, n_verts):
    """Slot each KEPT triangle to a material, given a mesh's normalized `draws`.

    Iterates the whole index buffer once (`for i in range(0, len-2, 3)`), applying
    the EXACT filter `build_scatter_mesh` uses (skip a==b/b==c/a==c or any vertex
    index >= n_verts), so the returned `faces` list matches build_scatter_mesh's
    order 1:1. Each KEPT triangle at start index-position `i` is assigned the draw
    whose [idx_start, idx_start+idx_count) contains `i`; a triangle not covered by
    any draw gets slot 0.

    Returns (faces, face_slot, slot_keys):
      * faces:     list of kept (a, b, c) vertex-index tuples, in buffer order.
      * face_slot: slot index (into slot_keys) for each kept face, same order.
      * slot_keys: de-duplicated (matidx, shdidx) pairs in first-occurrence order
                   across `draws` — one Blender material slot per entry.
    """
    slot_keys = []
    slot_of_pair = {}
    ranges = []                        # (idx_start, idx_end, slot)
    for d in draws:
        key = (d["matidx"], d["shdidx"])
        if key not in slot_of_pair:
            slot_of_pair[key] = len(slot_keys)
            slot_keys.append(key)
        s = d["idx_start"]
        ranges.append((s, s + d["idx_count"], slot_of_pair[key]))

    faces = []
    face_slot = []
    for i in range(0, len(indices) - 2, 3):
        a, b, c = int(indices[i]), int(indices[i + 1]), int(indices[i + 2])
        if a == b or b == c or a == c:
            continue                   # degenerate / primitive-restart guard
        if a >= n_verts or b >= n_verts or c >= n_verts:
            continue
        slot = 0
        for s, e, sl in ranges:
            if s <= i < e:
                slot = sl
                break
        faces.append((a, b, c))
        face_slot.append(slot)
    return faces, face_slot, slot_keys


# ---------------------------------------------------------------------------
# Coordinate math — pure 4x4 (row-major, translation in column 3), the ONE
# place the placement transform is defined. `mathutils.Matrix(rows)` consumes
# the same row-major layout, so the addon wraps these results directly.
# ---------------------------------------------------------------------------

Mat4 = list   # 4 rows of 4 floats


def identity() -> Mat4:
    return [[1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def mat_mul(a: Mat4, b: Mat4) -> Mat4:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
            for i in range(4)]


def transform_point(m: Mat4, p) -> tuple:
    """Apply a 4x4 to a 3-point (w=1): returns (x', y', z')."""
    x, y, z = p[0], p[1], p[2]
    return (m[0][0] * x + m[0][1] * y + m[0][2] * z + m[0][3],
            m[1][0] * x + m[1][1] * y + m[1][2] * z + m[1][3],
            m[2][0] * x + m[2][1] * y + m[2][2] * z + m[2][3])


def translation_matrix(t) -> Mat4:
    m = identity()
    m[0][3], m[1][3], m[2][3] = float(t[0]), float(t[1]), float(t[2])
    return m


def scale_matrix(s) -> Mat4:
    m = identity()
    m[0][0], m[1][1], m[2][2] = float(s[0]), float(s[1]), float(s[2])
    return m


def quat_to_matrix(x, y, z, w) -> Mat4:
    """Unit quaternion (x,y,z,w) -> 4x4 rotation (M @ v convention).

    Normalizes first (guards against snorm-decoded quats that are ~unit but not
    exactly). Matches `mathutils.Quaternion((w,x,y,z)).to_matrix()`.
    """
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n < 1e-12:
        return identity()
    x, y, z, w = x / n, y / n, z / n, w / n
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return [[1 - 2 * (yy + zz), 2 * (xy - wz),     2 * (xz + wy),     0.0],
            [2 * (xy + wz),     1 - 2 * (xx + zz), 2 * (yz - wx),     0.0],
            [2 * (xz - wy),     2 * (yz + wx),     1 - 2 * (xx + yy), 0.0],
            [0.0,               0.0,               0.0,               1.0]]


def basis_matrix(y_up_to_z_up: bool = True) -> Mat4:
    """B: the global game(Y-up) -> Blender(Z-up) basis change.

    THE single place the basis is defined (tweak here at integration time). The
    default is a PURE +90 deg rotation about X (determinant +1, no mirror): it
    sends game (x, y, z) -> Blender (x, -z, y), identical to the .lemesh
    importer's `mesh_builder._axis_matrix` (see docs/FORMATS.md). When
    `y_up_to_z_up` is False, B is identity (native passthrough).
    """
    if not y_up_to_z_up:
        return identity()
    # Rotation about X by +90 deg: [[1,0,0],[0,0,-1],[0,1,0]]
    return [[1.0, 0.0, 0.0, 0.0],
            [0.0, 0.0, -1.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 0.0, 1.0]]


def compose_instance_matrix(translation, rotation_xyzw, scale,
                            basis: Mat4 = None) -> Mat4:
    """THE placement transform: world = B @ (T @ R @ S).

    * S: per-instance scale (may be non-uniform).
    * R: per-instance rotation, from the (x,y,z,w) unit quaternion.
    * T: per-instance translation (game space).
    * B: the Y-up->Z-up basis change (default `basis_matrix()`); pass an explicit
      basis to tweak the exact convention without touching callers.

    Geometry is stored in native game space (B is NOT baked into the mesh), so
    B is applied exactly once, here, on the object matrix. Returns a row-major
    4x4 ready for `mathutils.Matrix(...)`.
    """
    b = basis_matrix(True) if basis is None else basis
    t = translation_matrix(translation)
    r = quat_to_matrix(*rotation_xyzw)
    s = scale_matrix(scale)
    return mat_mul(b, mat_mul(t, mat_mul(r, s)))
