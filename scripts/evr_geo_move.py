"""Relocate level geometry -- render placement AND both collision structures together.

    import evr_geo_move as G
    lv = G.Level(Path(r"H:\\pcvr-extracted"), "576ed3f8428ebc4b")
    lv.move(box=(lo, hi), entities=[...], delta=(dx, dy, dz))
    lv.write(Path(stage), "f229b70ac9f62369")

WHAT HAS TO MOVE TOGETHER
-------------------------
A level's geometry lives in three places, and an edit that touches fewer than
all three ships a map whose collision does not match what you can see:

| resource | holds | how it moves |
|---|---|---|
| `CTransformCR` | the render instance's world TRS | translation at `+0x58` |
| `CPhysicsResource` | the body the player collides with | vertex array + a refit of the mid-phase kDOP tree |
| `CBVHResource` | the Embree raycast tree (disc, laser, LOS) | `v0` of each Triangle4 lane + a refit of the node AABBs |

`CMaterialTypesBVHResource` is NOT geometry despite the name -- it is a sorted
`(u64 key, CSymbol64 value)` map -- so it needs no edit.

TRANSLATE, ROTATE AND SCALE
---------------------------
Every derived quantity a transform invalidates is recomputed from the geometry,
so an object can be moved, turned and resized. The per-edge record was the
blocker and is now fully decoded, each field measured against the stock arena:

    +0   u32  va, vb, oppA, oppB, triA, triB, facecount
    +28  f32  LENGTH          == |v[vb] - v[va]|           32,765 / 32,765
    +32  f32  DIHEDRAL        == pi - acos(nA . nB)        31,588 / 31,588
                                 (+inf on the 1,177 boundary edges)
    +36  f32  OPP SEPARATION  == |v[oppB] - v[oppA]|       31,588 / 31,588
    +40  u32  slotA, slotB

Those three floats are the only per-edge quantities a non-rigid transform
changes, and all three now come straight back out of the transformed vertices.

THE `+400` DIRECTION TABLE
-------------------------
Each body carries a small table of UNIT DIRECTIONS at `+400` -- 52 entries / 18
distinct for the arena's body 0, 24 / 6 for body 8, every one measured at
|v| = 1.0. A translation leaves a direction alone, so a move needs no edit here;
a rotate or a non-uniform scale changes every direction that describes the moved
geometry. Leaving the table stale was a real bug: collision VERTICES turned with
the object while these directions kept pointing the old way, which is exactly
why moving a model worked and turning one did not.

Nothing indexes the table per primitive -- `+224` and `+240` hold OWNING
TRIANGLE indices (range 0..21450, not table slots) and `+624` is all-ones on
body 8 -- so entries are attributed BY DIRECTION: an entry is turned only when a
triangle the edit moved produces it and no triangle left behind does. Directions
map by the INVERSE TRANSPOSE and are renormalised, which is right for a rotation
and for a non-uniform scale alike.

An entry that a static triangle also backs still has to serve that triangle, so
it is left alone and counted in `plane_dirs_shared`. That is the conservative
half of the rule and it matters: body 8 is 144 axis-aligned boxes sharing six
face normals, so turning ONE of them can rotate nothing without breaking the
other 143. Growing the table to hold both directions would move every later
array in the body, so the residue is reported rather than guessed at. The table
is also not a faithful face list to begin with -- 6 of body 0's 17 directions
match no triangle in the body at all -- so it cannot be regenerated from
scratch.

MOVING WHOLE OBJECTS, NOT LOOSE VERTICES
----------------------------------------
Edge records store a precomputed LENGTH. Translating one endpoint of an edge and
not the other would change the real length while the stored one stayed put, so a
selection has to be closed under connectivity. This module therefore selects
**connected components** of the collision mesh (union-find over triangle
corners, welded at 1e-3) and moves a component only when it lies entirely inside
the requested box. A component that straddles the boundary is left alone and
reported, so a bad box under-selects instead of tearing the mesh.

THE kDOP REFIT
--------------
Each 84-byte mid-phase node is three packed `(count<<16 | start)` range words
over verts/tris/edges, then 18 floats: min[9] and max[9] of the projections onto
the axes `x, y, z, x+y, x+z, y+z, x-y, x-z, y-z`. Recomputing those from
`verts[start:start+count]` reproduces **699 of 699** stock arena nodes to within
4e-6 (float32 rounding), which is what licenses rewriting them: the engine reads
the serialized tree verbatim and never rebuilds it, so a stale node makes the
body go to sleep exactly where the geometry moved to.
"""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

# ── resource type directories ────────────────────────────────────────────────
T_PHYSICS = "b7d338793fa37832"
T_BVH = "358b53c17825d154"
T_TRANSFORM = "92abd3e1432bf5e8"
T_SCENE = "a388ea69e5108f4c"

# ── CPhData / SPhGeoFull ─────────────────────────────────────────────────────
PH_BODIES_COUNT = 32
PH_FIRST_BODY = 36
GEO_HEADER = 648
BODY_TAIL = 880
#: (count field offset inside the geo header, element stride), in
#: `CPhData::AttachToStream` declaration order.
ARRAY_ORDER = ((8, 12), (24, 52), (40, 48), (56, 40), (176, 84), (192, 12),
               (208, 12), (224, 4), (240, 4), (256, 4), (272, 4), (288, 8),
               (304, 8), (320, 8), (336, 8), (352, 4), (368, 8), (400, 12),
               (416, 48), (432, 8), (448, 64), (464, 160), (480, 2), (496, 4),
               (512, 12), (528, 40), (544, 2), (560, 4), (576, 12), (592, 48),
               (608, 100), (624, 8), (640, 16))
A_VERTS, A_TRIS, A_TREE, A_RESTVERTS, A_SKIN = 8, 24, 176, 208, 416
#: per-body table of unit directions; turned with the geometry that backs it.
A_PLANES = 400
#: extra slack, in metres, when an `Owners` index is offering CANDIDATE
#: components to the ownership test. A collision hull does not have to sit
#: inside its object's render bounds, so the box alone would never offer an
#: overhanging one; ownership, not this pad, decides what actually moves.
OWNER_CANDIDATE_PAD = 1.0
#: how close a triangle normal must sit to a table direction to back it. At this
#: tolerance the stock arena backs 11 of body 0's 17 directions and 6 of 6 on
#: body 8; the table is float32 and the cook's normals are not bit-exact.
PLANE_DIR_TOL = 2e-3
HDR_KDOP, HDR_VOLUME, HDR_RADIUS, HDR_CENTROID = 76, 148, 152, 156
TREE_NODE = 84
TREE_LINK_BYTES = 12
#: the nine kDOP axes, in the order the 18 floats are stored (min[9] then max[9]).
KDOP_AXES = np.array([[1, 0, 0], [0, 1, 0], [0, 0, 1],
                      [1, 1, 0], [1, 0, 1], [0, 1, 1],
                      [1, -1, 0], [1, 0, -1], [0, 1, -1]], dtype=np.float64)

# ── CBVHResource ─────────────────────────────────────────────────────────────
BVH_HEADER = 0x50
BVH_NODE = 64
BVH_PRIM = 448
BVH_TRI4 = 224
BVH_LEAF_BIT = 0x80000000
BVH_LANE_INVALID = 0xFFFFFFFF
#: bytes to spend on one candidate-box x moved-lane block in the node refit.
#: Both sides scale with the size of the edited object, so the pairing has to be
#: chunked or a large rotate allocates tens of gigabytes; see `_refit_bvh`.
BVH_PAIR_BUDGET = 128 << 20

# ── CTransformCR ─────────────────────────────────────────────────────────────
XF_STRIDE = 176
XF_KEY = 0x30
XF_ROT = 0x48
XF_POS = 0x58
XF_SCALE = 0x64

# ── CGSceneResource lights (section 1, SGLightParams) ────────────────────────
LIGHT_STRIDE = 360
LIGHT_POSITION = 0x10


def _write_if_changed(buf: bytearray, off: int, data: bytes) -> bool:
    """Write only when the bytes differ, so a no-op edit stays byte-identical."""
    if buf[off:off + len(data)] == data:
        return False
    buf[off:off + len(data)] = data
    return True


def _quat_to_matrix(q) -> np.ndarray:
    """`(x, y, z, w)` -> rotation matrix, matching the addon's placement math."""
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _matrix_to_quat(m) -> np.ndarray:
    """Rotation matrix -> `(x, y, z, w)`, via the largest-component branch."""
    t = m[0, 0] + m[1, 1] + m[2, 2]
    if t > 0:
        r = np.sqrt(1.0 + t) * 2
        q = np.array([(m[2, 1] - m[1, 2]) / r, (m[0, 2] - m[2, 0]) / r,
                      (m[1, 0] - m[0, 1]) / r, 0.25 * r])
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        r = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = np.array([0.25 * r, (m[0, 1] + m[1, 0]) / r,
                      (m[0, 2] + m[2, 0]) / r, (m[2, 1] - m[1, 2]) / r])
    elif m[1, 1] > m[2, 2]:
        r = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = np.array([(m[0, 1] + m[1, 0]) / r, 0.25 * r,
                      (m[1, 2] + m[2, 1]) / r, (m[0, 2] - m[2, 0]) / r])
    else:
        r = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = np.array([(m[0, 2] + m[2, 0]) / r, (m[1, 2] + m[2, 1]) / r,
                      0.25 * r, (m[1, 0] - m[0, 1]) / r])
    return q / np.linalg.norm(q)


def _welded_components(verts: np.ndarray, tris: np.ndarray) -> np.ndarray:
    """`component id per vertex`, welding coincident positions first.

    Two triangles that meet only at a duplicated vertex are still one rigid
    piece for our purposes -- the cook splits vertices per face -- so the
    union-find runs over positions rounded to 1e-3, not raw indices.
    """
    key = np.round(verts * 1000.0).astype(np.int64)
    _uniq, inv = np.unique(key, axis=0, return_inverse=True)
    parent = np.arange(inv.max() + 1)

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    w = inv[tris]
    for a, b in ((0, 1), (1, 2)):
        for x, y in zip(w[:, a], w[:, b]):
            rx, ry = find(int(x)), find(int(y))
            if rx != ry:
                parent[ry] = rx
    roots = np.array([find(i) for i in range(len(parent))])
    return roots[inv]


class Body:
    """One geo body of a `CPhData`, with its sub-array bases resolved."""

    def __init__(self, blob: bytes, off: int):
        if struct.unpack_from("<Q", blob, off)[0] != 1:
            raise ValueError("body at %d is not a geo body" % off)
        self.off = off
        self.geo = off + 8
        cursor = self.geo + GEO_HEADER
        self.bases: dict[int, tuple[int, int, int]] = {}
        for coff, stride in ARRAY_ORDER:
            n = struct.unpack_from("<I", blob, self.geo + coff)[0]
            if n:
                self.bases[coff] = (cursor, n, stride)
                cursor += n * stride
        self.end = cursor + BODY_TAIL

    def count(self, coff: int) -> int:
        return self.bases.get(coff, (0, 0, 0))[1]


class Owners:
    """Which ENTITY each collision component belongs to.

    A box is a poor way to decide what collision an object owns, and it fails in
    both directions. The arena's instance 1207 has a 32 x 17 m render AABB, so
    box containment sweeps up the collision of every small prop standing in
    front of it; meanwhile a hull that hugs its own object but pokes 4 cm past
    the render bounds is rejected as "straddling" and left behind.

    Ownership answers the question directly: a component belongs to an entity
    when that entity's stock RENDER SURFACE runs along it -- `cover` of the
    component's vertices within `tol` of a render vertex. Measured on the stock
    arena that separates cleanly: body 2's component 5849 is covered 1.00 by
    entity `daa8136c` and 0.00 by `0530be45`, which the box rule moved anyway.

    Judged per ENTITY, never per instance: the cook ships one logical object as
    several models, so instances 2777..2781 are all entity `daa8136c` and each
    covers only part of the shared hull (0.77 for 2781 alone, 1.00 for the
    entity). Coverage is therefore unioned across an entity's instances before
    the threshold is applied.

    Build it from the level's own stock geometry -- `(entity, world vertices)`
    per static instance, in GAME space, at the placement the collision was
    cooked against.
    """

    def __init__(self, items, tol=0.08, cover=0.5):
        self.tol = float(tol)
        self.cover = float(cover)
        self.ent: list[str] = []
        self.pts: list[np.ndarray] = []
        lo, hi = [], []
        for entity, verts in items:
            v = np.asarray(verts, dtype=np.float64)
            if v.ndim != 2 or not len(v):
                continue
            self.ent.append(_ent_key(entity))
            self.pts.append(v)
            lo.append(v.min(0)); hi.append(v.max(0))
        self.lo = np.array(lo) if lo else np.zeros((0, 3))
        self.hi = np.array(hi) if hi else np.zeros((0, 3))

    def __len__(self) -> int:
        return len(self.ent)

    def of(self, pts, max_pts: int = 256, max_ref: int = 2048) -> set:
        """The entities whose render surface covers `pts`.

        Both sides are subsampled -- a hull and a render mesh that share a
        surface do so everywhere, so a few hundred points settle it as well as
        all of them, and it keeps the pairing bounded on 4,384-vertex meshes.
        """
        pts = np.asarray(pts, dtype=np.float64)
        if not len(pts) or not len(self.ent):
            return set()
        if len(pts) > max_pts:
            pts = pts[np.linspace(0, len(pts) - 1, max_pts).astype(int)]
        lo = pts.min(0) - self.tol
        hi = pts.max(0) + self.tol
        cand = np.flatnonzero(((self.lo <= hi) & (self.hi >= lo)).all(1))
        hit: dict[str, np.ndarray] = {}
        t2 = self.tol * self.tol
        for k in cand:
            ref = self.pts[k]
            m = ((ref >= lo) & (ref <= hi)).all(1)
            if not m.any():
                continue
            ref = ref[m]
            if len(ref) > max_ref:
                ref = ref[np.linspace(0, len(ref) - 1, max_ref).astype(int)]
            near = ((pts[:, None, :] - ref[None]) ** 2).sum(2).min(1) <= t2
            if not near.any():
                continue
            e = self.ent[k]
            hit[e] = near if e not in hit else (hit[e] | near)
        return {e for e, m in hit.items() if m.mean() >= self.cover}


def _ent_key(e) -> str:
    """Entities travel as hex strings or ints; compare them one way."""
    if isinstance(e, str):
        return "%016x" % int(e, 16)
    return "%016x" % int(e)


class Level:
    """The three geometry resources of one level, edited together."""

    def __init__(self, root: Path, level_hash: str):
        self.root = Path(root)
        self.level = level_hash
        self.phys = bytearray(self._read(T_PHYSICS))
        self.bvh = bytearray(self._read(T_BVH))
        self.xf = bytearray(self._read(T_TRANSFORM))
        try:
            self.scene = bytearray(self._read(T_SCENE))
        except FileNotFoundError:
            self.scene = None
        self.log: list[dict] = []
        self._load_physics()
        self._load_bvh()

    # ── loading ──────────────────────────────────────────────────────────
    def _read(self, type_hash: str) -> bytes:
        for tname in (type_hash, type_hash.lstrip("0")):
            for lname in (self.level, self.level.lstrip("0")):
                p = self.root / tname / lname
                if p.is_file():
                    return p.read_bytes()
        raise FileNotFoundError("%s/%s" % (type_hash, self.level))

    def _load_physics(self) -> None:
        n = struct.unpack_from("<I", self.phys, PH_BODIES_COUNT)[0]
        self.bodies: list[Body] = []
        off = PH_FIRST_BODY
        for _ in range(n):
            b = Body(self.phys, off)
            self.bodies.append(b)
            off = b.end
        self.pverts = []
        self.pverts_orig = []
        self.pcomp = []
        self.moved_verts: dict[int, np.ndarray] = {}
        #: `+400` unit directions per body, and which of those need writing back.
        self.pdirs: dict[int, np.ndarray] = {}
        self.pdirs_dirty: set[int] = set()
        #: `(body, component) -> owning entities`, resolved once per export.
        self._own_cache: dict[tuple, set] = {}
        for bi, b in enumerate(self.bodies):
            if A_PLANES in b.bases:
                pb, pn, _ = b.bases[A_PLANES]
                self.pdirs[bi] = np.frombuffer(
                    self.phys, dtype="<f4", count=pn * 3,
                    offset=pb).reshape(-1, 3).astype(np.float64)
        for b in self.bodies:
            vb, vn, _ = b.bases[A_VERTS]
            tb, tn, _ = b.bases[A_TRIS]
            v = np.frombuffer(self.phys, dtype="<f4", count=vn * 3,
                              offset=vb).reshape(-1, 3).astype(np.float64)
            t = np.frombuffer(self.phys, dtype="<u4", count=tn * 13,
                              offset=tb).reshape(-1, 13)[:, :3].astype(np.int64)
            self.pverts.append(v.copy())
            self.pverts_orig.append(v.copy())
            self.pcomp.append(_welded_components(v, t))

    def _load_bvh(self) -> None:
        s1 = struct.unpack_from("<Q", self.bvh, 0x08)[0]
        self.bvh_s1 = s1
        self.bvh_s2 = struct.unpack_from("<Q", self.bvh, 0x28)[0]
        self.bvh_root = struct.unpack_from("<I", self.bvh, 0x40)[0] >> 3
        self.nprim = s1 // BVH_PRIM
        self.nnode = self.bvh_s2 // BVH_NODE
        n = self.nprim
        raw = np.frombuffer(self.bvh, dtype=np.uint8,
                            count=n * BVH_PRIM, offset=BVH_HEADER).reshape(n, BVH_PRIM)

        def f4(offset):
            out = np.empty((n, 2, 3, 4), dtype=np.float32)
            for bi, blk in enumerate((0, BVH_TRI4)):
                for a in range(3):
                    o = blk + offset + a * 16
                    out[:, bi, a, :] = raw[:, o:o + 16].copy().view(np.float32)
            return out

        def u4(offset):
            out = np.empty((n, 2, 4), dtype=np.uint32)
            for bi, blk in enumerate((0, BVH_TRI4)):
                o = blk + offset
                out[:, bi, :] = raw[:, o:o + 16].copy().view(np.uint32)
            return out

        self.b_v0 = f4(0x00).astype(np.float64)
        self.b_e1 = f4(0x30).astype(np.float64)
        self.b_e2 = f4(0x60).astype(np.float64)
        geom, prim = u4(0xC0), u4(0xD0)
        self.b_valid = ~((geom == BVH_LANE_INVALID) & (prim == BVH_LANE_INVALID))
        #: which Triangle4 blocks hold a moved lane -- `(prim, half)`, so the
        #: flat index `prim*2 + half` is exactly a leaf's block index.
        self.moved_prims = np.zeros((n, 2), dtype=bool)
        self._bvh_nodes_grown: set[int] = set()
        #: one entry per edit: `(old_lo, old_hi, new_lo, new_hi)` per moved lane,
        #: which is all the geometric node refit needs.
        self._bvh_moves: list[tuple] = []
        #: lanes whose e1/e2/Ng must be rewritten, not just v0.
        self._bvh_lanes = np.zeros((n, 2, 4), dtype=bool)
        #: bodies that saw a non-rigid edit, so their edge features need rebuilding.
        self.nonrigid: set[int] = set()

    # ── the edit ─────────────────────────────────────────────────────────
    def move(self, box, entities, delta, label: str = "") -> dict:
        """Translate one object -- the rigid special case of `transform`."""
        return self.transform(box, entities, delta=delta, label=label)

    def transform(self, box, entities, delta=(0.0, 0.0, 0.0), rot=None,
                  scale=None, pivot=None, label: str = "", owners=None) -> dict:
        """Move / turn / resize one object -- instances, collision and raycast.

        `box` is `(lo, hi)` in world space and must contain the object with a
        little slack. `rot` is a 3x3 world-space rotation and `scale` a per-axis
        triple, both applied about `pivot` (default: the box centre), and
        `delta` after.

        Pass an `Owners` index to select collision by WHO OWNS IT rather than by
        what fits the box: a component then moves when the edited entities are
        the only ones whose render surface runs along it, however far it reaches
        past the box, and a component another entity owns is left alone even
        when the box swallows it whole. Without one, the old rule applies --
        entirely inside the box or not at all -- which under-selects a hull that
        overhangs its object and over-selects anything standing in front of a
        large one.
        """
        lo = np.asarray(box[0], dtype=np.float64)
        hi = np.asarray(box[1], dtype=np.float64)
        d = np.asarray(delta, dtype=np.float64)
        R = np.eye(3) if rot is None else np.asarray(rot, dtype=np.float64)
        S = np.ones(3) if scale is None else np.asarray(scale, dtype=np.float64)
        C = (lo + hi) / 2 if pivot is None else np.asarray(pivot, dtype=np.float64)
        M = R @ np.diag(S)
        self._M, self._C, self._d = M, C, d
        rigid = np.allclose(M, np.eye(3), atol=1e-12)

        def xf(p):
            """The affine map, on an (N,3) array of world points."""
            return (M @ (np.asarray(p) - C).T).T + C + d
        rec = {"label": label, "delta": d.tolist(),
               "box": [lo.tolist(), hi.tolist()],
               "instances": 0, "phys_components": 0, "phys_verts": 0,
               "bvh_triangles": 0, "phys_straddled": 0, "lights": 0,
               "plane_dirs": 0, "plane_dirs_shared": 0,
               "phys_foreign": 0, "phys_unclaimed": 0, "phys_overhang": 0}

        # 1. render placement
        for ent in entities:
            key = int(ent, 16) if isinstance(ent, str) else int(ent)
            if self._place_transform(key, M, C, d, rigid):
                rec["instances"] += 1

        # 2. player collision -- whole connected components only
        want = {_ent_key(e) for e in entities} if owners is not None else set()
        for bi, (verts, comp) in enumerate(zip(self.pverts, self.pcomp)):
            inside = ((verts >= lo) & (verts <= hi)).all(1)
            if owners is None:
                near = inside
            else:
                # a wider net to pick CANDIDATES only -- a hull that overhangs
                # its object still has to be offered to the ownership test.
                # Ownership, not this pad, decides what actually moves.
                near = ((verts >= lo - OWNER_CANDIDATE_PAD)
                        & (verts <= hi + OWNER_CANDIDATE_PAD)).all(1)
            if not near.any():
                continue
            flag = self.moved_verts.setdefault(bi, np.zeros(len(verts), dtype=bool))
            # what THIS edit moves, kept apart from `flag` (which accumulates
            # across edits) so the direction table is attributed to this
            # transform and not to an earlier one with a different rotation.
            just = np.zeros(len(verts), dtype=bool)
            pre = None if rigid else verts.copy()
            for cid in np.unique(comp[near]):
                sel = comp == cid
                if owners is not None:
                    own = self._owner_of(owners, bi, cid, verts, sel)
                    if own:
                        if not own <= want:
                            # another entity's render surface runs along this
                            # component; it has to stay where that entity is.
                            rec["phys_foreign"] += 1
                            continue
                        if not inside[sel].all():
                            rec["phys_overhang"] += 1
                    else:
                        # nothing claims it -- fall back to the box, which is
                        # the conservative reading when ownership is unknown.
                        rec["phys_unclaimed"] += 1
                        if not inside[sel].all():
                            rec["phys_straddled"] += 1
                            continue
                elif not inside[sel].all():
                    rec["phys_straddled"] += 1
                    continue
                verts[sel] = xf(verts[sel])
                flag |= sel
                just |= sel
                rec["phys_components"] += 1
                rec["phys_verts"] += int(sel.sum())
            if not rigid:
                self.nonrigid.add(bi)
                # a translation leaves every direction exactly as it was, so
                # this only runs for a rotate or a scale.
                self._turn_plane_dirs(bi, pre, just, M, rec)

        # 3. raycast triangles -- each Triangle4 lane is standalone, so any
        #    subset can be transformed without disturbing its neighbours. All
        #    three corners move, then e1/e2 are re-derived (and the stored
        #    geometric normal with them, in `_refit_bvh`).
        v0 = self.b_v0
        v1, v2 = v0 + self.b_e1, v0 + self.b_e2

        def _in(p):
            return ((p >= lo.reshape(1, 1, 3, 1)) & (p <= hi.reshape(1, 1, 3, 1))).all(2)

        sel = _in(v0) & _in(v1) & _in(v2) & self.b_valid
        if sel.any():
            tri = np.stack([v0, v1, v2])            # (3, n, 2, 3, 4)
            old_lo = np.stack([tri[:, :, :, a, :].min(0)[sel] for a in range(3)], axis=1)
            old_hi = np.stack([tri[:, :, :, a, :].max(0)[sel] for a in range(3)], axis=1)
            P = [np.stack([tri[k][:, :, a, :][sel] for a in range(3)], axis=1)
                 for k in range(3)]
            Q = [xf(p) for p in P]
            for a in range(3):
                self.b_v0[:, :, a, :][sel] = Q[0][:, a]
                self.b_e1[:, :, a, :][sel] = Q[1][:, a] - Q[0][:, a]
                self.b_e2[:, :, a, :][sel] = Q[2][:, a] - Q[0][:, a]
            new_lo = np.minimum.reduce(Q)
            new_hi = np.maximum.reduce(Q)
            self._bvh_moves.append((old_lo, old_hi, new_lo, new_hi))
            self.moved_prims |= sel.any(2)
            if not rigid:
                # e1/e2 and the stored Ng only change under rotate/scale; a
                # translation leaves them exact, and rewriting them would just
                # bake in the difference between the cook's cross product and
                # ours (up to 4e-4).
                self._bvh_lanes |= sel
            rec["bvh_triangles"] = int(sel.sum())

        # 4. scene lights that belong to the object
        if self.scene is not None:
            rec["lights"] = self._move_lights(lo, hi, xf)

        self.log.append(rec)
        return rec

    def _place_transform(self, key: int, M, C, d, rigid: bool) -> bool:
        """Re-place one instance's `CTransformCR` row under the world map `M`.

        The row stores (quaternion, translation, scale), so the composed world
        matrix `M . R_q . diag(s)` has to come back apart into that form. The
        columns of the composed matrix give the new scale and, divided out, the
        new rotation; if what is left is not orthonormal the requested transform
        simply cannot be expressed in this row (a non-uniform scale applied
        across an instance's own tilted axes), and that raises rather than
        writing a sheared placement the renderer will draw differently from the
        collision.
        """
        blob = self.xf
        want = struct.pack("<Q", key)
        start = 0
        while True:
            at = blob.find(want, start)
            if at < 0:
                return False
            start = at + 1
            row = at - XF_KEY
            if row < 0 or row + XF_STRIDE > len(blob):
                continue
            q = np.array(struct.unpack_from("<4f", blob, row + XF_ROT))
            if abs((q * q).sum() - 1.0) > 0.02:
                continue
            p = np.array(struct.unpack_from("<3f", blob, row + XF_POS))
            s = np.array(struct.unpack_from("<3f", blob, row + XF_SCALE))
            struct.pack_into("<3f", blob, row + XF_POS, *(M @ (p - C) + C + d))
            if rigid:
                # a pure translation leaves orientation and size alone; taking
                # them apart and back would only add float32 noise.
                return True
            A = M @ _quat_to_matrix(q) @ np.diag(s)
            s_new = np.linalg.norm(A, axis=0)
            if (s_new < 1e-9).any():
                raise ValueError("degenerate scale on instance %016x" % key)
            R_new = A / s_new
            if not np.allclose(R_new.T @ R_new, np.eye(3), atol=2e-3):
                raise ValueError(
                    "instance %016x cannot hold this transform: a non-uniform "
                    "scale across its own rotated axes would shear it" % key)
            # only write rotation/scale when they really changed: repacking a
            # stock float through a decompose/recompose does not always
            # reproduce its bits, and a no-op edit must stay byte-identical.
            _write_if_changed(blob, row + XF_SCALE,
                              struct.pack("<3f", *s_new))
            q_new = _matrix_to_quat(R_new)
            if np.dot(q_new, q) < 0:            # same rotation, opposite sign
                q_new = -q_new
            _write_if_changed(blob, row + XF_ROT, struct.pack("<4f", *q_new))
            return True

    def _move_lights(self, lo, hi, xf) -> int:
        """Translate `SGLightParams` whose position lies inside the box.

        The light table is the first `CTable` of the scene stream, after the
        version-head prefix; it is located by reading that prefix the same way
        `evr_lights` does.
        """
        base = self._light_table_base()
        if base is None:
            return 0
        count = struct.unpack_from("<I", self.scene, base)[0]
        moved = 0
        for i in range(count):
            o = base + 4 + i * LIGHT_STRIDE + LIGHT_POSITION
            if o + 12 > len(self.scene):
                break
            p = np.array(struct.unpack_from("<3f", self.scene, o), dtype=np.float64)
            if (p >= lo).all() and (p <= hi).all():
                struct.pack_into("<3f", self.scene, o, *xf(p.reshape(1, 3))[0])
                moved += 1
        return moved

    def _light_table_base(self):
        if getattr(self, "_lbase", "x") != "x":
            return self._lbase
        self._lbase = None
        try:
            import sys
            sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\quest_combat_port\tools")
            from resource_io import cgsceneresource as cs
            self._lbase = cs._vhead_len(bytes(self.scene))
        except Exception:                                        # noqa: BLE001
            self._lbase = None
        return self._lbase

    def _owner_of(self, owners, bi: int, cid, verts, sel) -> set:
        """`owners.of` for one component, cached -- an export runs a group at a
        time and the same component is offered to several of them.

        Measured on `pverts_orig`, never on the live array: ownership is a fact
        about the STOCK level, and the index it is matched against is built from
        stock render geometry. An earlier group in the same export may already
        have moved this component, and asking where it is now would compare it
        against render meshes it no longer sits on.
        """
        key = (bi, int(cid))
        got = self._own_cache.get(key)
        if got is None:
            got = owners.of(self.pverts_orig[bi][sel])
            self._own_cache[key] = got
        return got

    def _turn_plane_dirs(self, bi: int, pre, just, M, rec) -> None:
        """Turn the body's `+400` unit directions with the geometry they describe.

        Entries are attributed by direction, because nothing indexes the table
        per primitive: one is turned only when a triangle this edit moved
        produces it -- measured on `pre`, the positions the table was built
        against -- and no triangle left behind does. An entry a static triangle
        still backs is counted in `plane_dirs_shared` and left alone: a stale
        direction is recoverable, corrupting geometry that never moved is not.

        Directions map by the INVERSE TRANSPOSE and are renormalised, so a
        rotation and a non-uniform scale are both handled; for a pure rotation
        that reduces to `M` itself.
        """
        dirs = self.pdirs.get(bi)
        if dirs is None or pre is None or not just.any():
            return
        b = self.bodies[bi]
        if A_TRIS not in b.bases:
            return
        try:
            N = np.linalg.inv(M).T
        except np.linalg.LinAlgError:
            return
        tb, tn, _ = b.bases[A_TRIS]
        T = np.frombuffer(self.phys, dtype="<u4", count=tn * 13,
                          offset=tb).reshape(-1, 13)[:, :3].astype(np.int64)
        mov = just[T].all(1)
        if not mov.any():
            return

        def normals(v, sel):
            """Unit face normals of `sel`, deduped -- the pools are only searched
            for a match, so duplicates cost time and change nothing."""
            p = v[T[sel]]
            n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
            L = np.linalg.norm(n, axis=1)
            keep = L > 1e-12
            if not keep.any():
                return np.empty((0, 3))
            return np.unique((n[keep] / L[keep, None]).round(4), axis=0)

        n_moved = normals(pre, mov)                    # as the table saw them
        n_static = normals(self.pverts[bi], ~mov)      # untouched by this edit
        if not len(n_moved):
            return

        def backs(pool, d):
            return bool(len(pool)) and bool(
                (np.abs(pool - d).max(1) < PLANE_DIR_TOL).any())

        turned = 0
        for k, d in enumerate(dirs):
            if not backs(n_moved, d):
                continue
            if backs(n_static, d):
                rec["plane_dirs_shared"] += 1
                continue
            q = N @ d
            L = float(np.linalg.norm(q))
            if L < 1e-12:
                continue
            dirs[k] = q / L
            turned += 1
        rec["plane_dirs"] += turned
        if turned:
            self.pdirs_dirty.add(bi)

    # ── rebuild + write ──────────────────────────────────────────────────
    def _refit_physics(self) -> None:
        """Write moved vertices back and rebuild only what those moves changed.

        Every rewrite is scoped to a body -- and within it, to a node -- that
        actually holds a moved vertex, and the header centroid is SHIFTED by the
        measured change in the vertex mean rather than recomputed. That is what
        makes a zero-delta edit byte-identical: recomputing a stock float from
        stock geometry and packing it back through float32 does not always
        reproduce the stock bits, so an unconditional rebuild would rewrite
        thousands of bytes on a no-op and hide a real error in the noise.
        """
        for bi, (b, verts) in enumerate(zip(self.bodies, self.pverts)):
            moved = self.moved_verts.get(bi)
            if moved is None or not moved.any():
                continue
            vb, vn, _ = b.bases[A_VERTS]
            self.phys[vb:vb + vn * 12] = verts.astype(np.float32).tobytes()

            # `+400` unit directions, turned with the geometry in `transform`
            if bi in self.pdirs_dirty:
                pb, pn, _ = b.bases[A_PLANES]
                self.phys[pb:pb + pn * 12] = (
                    self.pdirs[bi].astype(np.float32).tobytes())

            # rest-verts are `v - centroid`; only from-scratch bodies carry them
            # and the stock level bodies do not, but handle a populated array
            # rather than silently leaving it stale.
            if A_RESTVERTS in b.bases:
                rb, rn, _ = b.bases[A_RESTVERTS]
                if rn == vn:
                    self.phys[rb:rb + rn * 12] = (
                        verts - verts.mean(0)).astype(np.float32).tobytes()

            # per-edge features. Under a pure translation all three floats are
            # invariant, so this only runs for a body that saw a rotate/scale.
            if bi in self.nonrigid and 40 in b.bases and A_TRIS in b.bases:
                self._rebuild_edges(b, verts, moved)

            # mid-phase tree: recompute the 9-axis kDOP of every node whose
            # vertex span contains a moved vertex.
            if A_TREE in b.bases:
                tb, tn, _ = b.bases[A_TREE]
                proj_all = verts @ KDOP_AXES.T
                touched = np.flatnonzero(moved)
                for k in range(tn):
                    p = tb + TREE_NODE * k
                    w0 = struct.unpack_from("<I", self.phys, p)[0]
                    start, cnt = w0 & 0xFFFF, w0 >> 16
                    if not cnt or not moved[start:start + cnt].any():
                        continue
                    seg = proj_all[start:start + cnt]
                    struct.pack_into("<18f", self.phys, p + TREE_LINK_BYTES,
                                     *seg.min(0).astype(np.float32),
                                     *seg.max(0).astype(np.float32))
                del touched

            # header centroid: shift by the exact change in the vertex mean, so
            # whatever definition the cook used stays consistent. An all-zero
            # centroid is the "not computed" sentinel these level bodies ship
            # (all nine read 0,0,0 next to real +-78 m geometry), so shifting it
            # would turn a sentinel into a bogus point -- leave it.
            c = np.array(struct.unpack_from("<3f", self.phys, b.geo + HDR_CENTROID),
                         dtype=np.float64)
            if c.any():
                c = c + (verts - self.pverts_orig[bi]).mean(0)
                struct.pack_into("<3f", self.phys, b.geo + HDR_CENTROID, *c.astype(np.float32))
            # radius may only GROW -- shrinking it could put geometry outside
            # the body's own broad-phase sphere. A stock radius of 0 is the
            # "not computed" sentinel the level bodies ship with (arena body 0
            # reads 0.0 against a real 78.7 m extent), so filling it in would be
            # inventing a bound the engine never had: leave it alone.
            r_old = struct.unpack_from("<f", self.phys, b.geo + HDR_RADIUS)[0]
            if r_old > 0.0:
                r_new = float(np.linalg.norm(verts - c, axis=1).max())
                if r_new > r_old:
                    struct.pack_into("<f", self.phys, b.geo + HDR_RADIUS, np.float32(r_new))
            # the +76 kDOP is the +-inf empty sentinel on the stock level
            # bodies; only touch it when it is a real bound, and only grow it.
            kd = np.array(struct.unpack_from("<18f", self.phys, b.geo + HDR_KDOP))
            if np.isfinite(kd).all():
                proj = verts @ KDOP_AXES.T
                lo = np.minimum(kd[:9], proj.min(0))
                hi = np.maximum(kd[9:], proj.max(0))
                struct.pack_into("<18f", self.phys, b.geo + HDR_KDOP,
                                 *lo.astype(np.float32), *hi.astype(np.float32))

    def _rebuild_edges(self, b, verts, moved) -> None:
        """Recompute the three per-edge floats for edges touching moved verts.

        All three were measured exactly against the stock arena body 0:
        `+28` = `|v[vb] - v[va]|` (32,765/32,765), `+36` =
        `|v[oppB] - v[oppA]|` (31,588/31,588), and `+32` =
        `pi - acos(nA . nB)` (31,588/31,588). The `+inf` at `+32` marks a
        boundary edge with only one adjacent face and is preserved as-is --
        it is a sentinel, not a stale angle.
        """
        eb, en, es = b.bases[40]
        tb, tn, _ = b.bases[A_TRIS]
        E = np.frombuffer(self.phys, dtype="<u4", count=en * 12,
                          offset=eb).reshape(-1, 12)
        T = np.frombuffer(self.phys, dtype="<u4", count=tn * 13,
                          offset=tb).reshape(-1, 13)[:, :3]
        va, vb_, oA, oB, tA, tB = (E[:, 0], E[:, 1], E[:, 2], E[:, 3], E[:, 4], E[:, 5])
        touch = moved[np.minimum(va, len(verts) - 1)] | moved[np.minimum(vb_, len(verts) - 1)]
        idx = np.flatnonzero(touch)
        if not len(idx):
            return
        p = verts[T]
        n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        n /= np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-30)
        for e in idx:
            o = eb + int(e) * es
            a, c = int(va[e]), int(vb_[e])
            struct.pack_into("<f", self.phys, o + 28,
                             np.float32(np.linalg.norm(verts[c] - verts[a])))
            ia, ib = int(oA[e]), int(oB[e])
            if ia < len(verts) and ib < len(verts):
                struct.pack_into("<f", self.phys, o + 36,
                                 np.float32(np.linalg.norm(verts[ib] - verts[ia])))
            ta, tbi = int(tA[e]), int(tB[e])
            old = struct.unpack_from("<f", self.phys, o + 32)[0]
            if np.isfinite(old) and ta < tn and tbi < tn:
                dot = float(np.clip(np.dot(n[ta], n[tbi]), -1.0, 1.0))
                struct.pack_into("<f", self.phys, o + 32,
                                 np.float32(np.pi - np.arccos(dot)))

    def _refit_bvh(self) -> None:
        """Write moved `v0` lanes back, then GROW every node box that held them.

        The growth is GEOMETRIC, not structural: a node whose child box
        contained a triangle's old bounds must still contain that triangle after
        it moves, so that child box is unioned with the triangle's new bounds.
        Every ancestor of the moved triangle's leaf is caught by definition,
        because a valid BVH box contains its whole subtree.

        This deliberately avoids decoding the leaf payload. The documented
        `block = payload >> 5, count = payload & 0x1f` mapping does not hold on
        this file -- checked against every leaf child, the stock box fails to
        enclose the triangles it names on 141,192 of 141,194 -- so a
        structural refit would rewrite boxes from the wrong triangles. The
        geometric rule needs no such mapping and cannot mis-attribute.

        Boxes only ever GROW. A BVH box that is larger than its subtree costs
        traversal time and nothing else, whereas one that is too small silently
        drops hits -- which for this level would mean the disc or a laser
        passing through the moved geometry.
        """
        if not self._bvh_moves:
            return
        # Ng is stored alongside v0/e1/e2 and the engine uses it as the face
        # normal, so a rotate or scale has to rewrite it too: Ng == e1 x e2,
        # which holds to 4e-4 across all 831,088 stock lanes.
        e1, e2 = self.b_e1, self.b_e2
        ng = np.cross(e1.transpose(0, 1, 3, 2), e2.transpose(0, 1, 3, 2)).transpose(0, 1, 3, 2)
        v0 = self.b_v0.astype(np.float32)
        e1f, e2f, ngf = e1.astype(np.float32), e2.astype(np.float32), ng.astype(np.float32)
        lanes = self._bvh_lanes.any(2)          # (n, 2) -- which blocks changed
        for i in np.flatnonzero(self.moved_prims.any(1)):
            base = BVH_HEADER + int(i) * BVH_PRIM
            for half, blk in enumerate((0, BVH_TRI4)):
                for a in range(3):
                    self.bvh[base + blk + a * 16:base + blk + a * 16 + 16] = \
                        v0[i, half, a, :].tobytes()
                if lanes[i, half]:
                    for src, off in ((e1f, 0x30), (e2f, 0x60), (ngf, 0x90)):
                        for a in range(3):
                            _write_if_changed(self.bvh, base + blk + off + a * 16,
                                              src[i, half, a, :].tobytes())

        base2 = BVH_HEADER + self.bvh_s1
        aabb = np.frombuffer(self.bvh, dtype=np.float32, count=self.nnode * 16,
                             offset=base2).reshape(self.nnode, 16)[:, :12].astype(np.float64)
        # child c: lo = (f[0+c], f[4+c], f[8+c]), hi = (f[2+c], f[6+c], f[10+c])
        lo = np.stack([aabb[:, [0, 1]], aabb[:, [4, 5]], aabb[:, [8, 9]]], axis=2)
        hi = np.stack([aabb[:, [2, 3]], aabb[:, [6, 7]], aabb[:, [10, 11]]], axis=2)
        touched = np.zeros(self.nnode, dtype=bool)

        for old_lo, old_hi, new_lo_t, new_hi_t in self._bvh_moves:
            reg_lo, reg_hi = old_lo.min(0), old_hi.max(0)
            # only child boxes that meet the region can hold one of these tris
            cand = np.nonzero(((lo <= reg_hi) & (hi >= reg_lo)).all(2))
            if not len(cand[0]):
                continue
            clo = lo[cand]; chi = hi[cand]
            # ... and of those, only the ones that CONTAIN a moved triangle.
            #
            # Chunked over the moved lanes, because the pairing is
            # candidates x lanes and both scale with the object: rotating the
            # arena's largest instance pairs 16,098 child boxes with 51,302
            # lanes, which is a 2.3 GiB mask and a 19.8 GiB float temporary in
            # one shot. The chunk only splits the reduction -- min/max
            # accumulate across chunks -- so the result is identical.
            nc = len(clo)
            step = max(1, BVH_PAIR_BUDGET // max(nc * 3 * 8, 1))
            any_hold = np.zeros(nc, dtype=bool)
            new_lo = np.full((nc, 3), np.inf)
            new_hi = np.full((nc, 3), -np.inf)
            for s in range(0, len(old_lo), step):
                ol, oh = old_lo[s:s + step], old_hi[s:s + step]
                holds = ((clo[:, None, :] <= ol[None, :, :] + 1e-4) &
                         (chi[:, None, :] >= oh[None, :, :] - 1e-4)).all(2)
                if not holds.any():
                    continue
                any_hold |= holds.any(1)
                np.minimum(new_lo, np.where(holds[..., None],
                                            new_lo_t[s:s + step][None],
                                            np.inf).min(1), out=new_lo)
                np.maximum(new_hi, np.where(holds[..., None],
                                            new_hi_t[s:s + step][None],
                                            -np.inf).max(1), out=new_hi)
            if not any_hold.any():
                continue
            ni = cand[0][any_hold]; ci = cand[1][any_hold]
            lo[ni, ci] = np.minimum(lo[ni, ci], new_lo[any_hold])
            hi[ni, ci] = np.maximum(hi[ni, ci], new_hi[any_hold])
            touched[ni] = True

        for c in range(2):
            aabb[:, 0 + c] = lo[:, c, 0]; aabb[:, 2 + c] = hi[:, c, 0]
            aabb[:, 4 + c] = lo[:, c, 1]; aabb[:, 6 + c] = hi[:, c, 1]
            aabb[:, 8 + c] = lo[:, c, 2]; aabb[:, 10 + c] = hi[:, c, 2]
        packed = aabb.astype(np.float32)
        self._bvh_nodes_grown = set(np.flatnonzero(touched).tolist())
        for ni in self._bvh_nodes_grown:
            o = base2 + ni * BVH_NODE
            self.bvh[o:o + 48] = packed[ni].tobytes()

    def write(self, out: Path, level_hash: str) -> dict:
        self._refit_physics()
        self._refit_bvh()
        out = Path(out)
        wrote = {}
        for tdir, blob in ((T_PHYSICS, self.phys), (T_BVH, self.bvh),
                           (T_TRANSFORM, self.xf), (T_SCENE, self.scene)):
            if blob is None:
                continue
            d = out / tdir
            d.mkdir(parents=True, exist_ok=True)
            (d / level_hash).write_bytes(bytes(blob))
            wrote[tdir] = len(blob)
        return wrote
