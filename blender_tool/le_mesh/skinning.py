"""CSkeletonData decode -> Skeleton (joints + hierarchy + bind pose).

Pure stdlib (struct only). No Oodle, no bpy. Testable with synthetic bytes and
importable unchanged inside Blender's Python.

------------------------------------------------------------------------------
EVIDENCE / how this was established
------------------------------------------------------------------------------
The CSkeletonData struct, as the engine's own type names describe it, is an in-*memory* image
(name@0x00, usageoffsets CTable@0x08, bindpose SAnimPoseData@0x40, jointhierarchy
CTable<SSkeletonJoint>@0x160, jointlookup CMap@0x3a8, ...). Those fixed offsets do
**NOT** match the on-disk archive slice: the resource is serialized as a *stream*
(counts + inline data, like CGMeshListData), and every CTable's data lives in an
appended region rather than at its struct offset. This was confirmed by decoding
34 real `CSkeletonResourceWin7` slices from archive 0703fd2acd5803e9:

  * `name` (CSymbol64, u64 @ +0x00) matches the resource name hash.            [stream-confirmed]
  * jointlookup is serialized as: u32 count; {u64 name; u32 index; u32 pad=0}[count],
    where `index` is a permutation of [0,count) -> gives joint name-by-index.   [stream-confirmed, 29/34 slices]
  * jointhierarchy is serialized as: u32 count; SSkeletonJoint[count] (0x18 each:
    u64 name; u32 parent; u32 firstchild; u32 nextsibling; u32 flags), with
    0xFFFFFFFF meaning "none". Where present it forms a valid forest AND its
    per-index names agree with jointlookup.                                     [stream-confirmed on the target + several slices]
  * bind pose is serialized as: u32 count; CTransfQ[count] (0x20 each: quaternion
    r[4] x,y,z,w; C3Vector t[3]; float s). The right run has ~unit quaternions
    and count == joint count; these are LOCAL (parent-relative) transforms.     [stream-confirmed on the target]

Because the exact *field order* / interleave of the full stream (usageoffsets,
CTableXT jointgroups, the many CMaps, CMemBlockAttach object/inv-object joints,
etc.) is undocumented and would need disassembly, this module does NOT walk the
stream positionally. Instead it **scans** the slice for each of the three
self-validating tables above and cross-checks them. That is robust to the parts
we cannot yet frame, and degrades cleanly (name-only) on slices where a table is
absent or does not validate.

objectjoints / invobjectjoints (CMemBlockAttach objectjoints @CSkeletonData+0x1e0,
invobjectjoints @+0x200) ARE now decoded. They do NOT hold raw 3x4/4x4 matrices:
each serializes as a `CMemBlockAttach` framed as `u32 size_bytes; CTransfQ[N]`
(N == jointhierarchy count, CTransfQ = 0x20: quat r[4], C3Vector t[3], float s).
The two blocks are adjacent in the stream. Cross-validated on 5 full skeletons of
archive 0703fd2acd5803e9 (byte-exact, max abs diff 0.000000):
  * objectjoints[i]    == FK accumulation of the LOCAL bind transforms down the
    parent chain  (object/model-space bind pose).                    [stream-confirmed]
  * objectjoints[i] @ invobjectjoints[i] == identity, i.e. invobjectjoints is the
    matrix inverse of objectjoints -> the classic per-joint INVERSE-BIND matrix
    used for skinning.                                               [stream-confirmed]
So `object_bind` (object-space rest, row-major 4x4) and `inverse_bind` (the
inverse-bind, row-major 4x4) are emitted per joint when present. They appear only
where a real jointhierarchy is serialized (never for names-only skeletons).

STILL not decoded (needs-disasm, intentionally omitted): joint groups
(CTableXT<SSkeletonGroup>), channel/alias tables. Non-uniform-scale bind
(transformsnu CTransfQS 0x30, per-axis scale) OCCURS in 20/57 archives (5 shared skeletons;
`3eff95282bf0807f` fully non-uniform) per the 2026-07-23 corpus scan, and is NOT decoded
-- but its PRESENCE is now surfaced loudly by a guard
(`has_nonuniform_scale` / `nonuniform_count` + a note), so a per-axis-scaled rig is
never silently imported as a uniform approximation. The authoritative on-disk signal
is `bindpose.transformsnu.count` (inside SAnimPoseData, reachable by the positional
walk); the CSkeletonData.nonuniformjointscale flag @+0x1d8 sits after jointhierarchy
+ the un-framed jointgroups CTableXT and is NOT positionally reachable. The corpus scan HAS
tripped the guard (5 shared skeletons, one fully non-uniform), so the full CTransfQS decode is
now a DEMONSTRATED need for faithful import of those rigs.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

NONE = 0xFFFFFFFF

# on-disk element strides (bytes)
JOINT_STRIDE = 0x18       # SSkeletonJoint
LOOKUP_STRIDE = 0x10      # CMap<CSymbol64,u32> serialized record
TRANSFORM_STRIDE = 0x20   # CTransfQ

MAX_JOINTS = 4096


@dataclass
class Joint:
    index: int
    name_hash: int
    parent: int              # -1 for root
    firstchild: int          # -1 for none
    nextsibling: int         # -1 for none
    flags: int
    # local (parent-relative) bind transform; identity if not decoded
    r: tuple = (0.0, 0.0, 0.0, 1.0)   # quaternion x,y,z,w
    t: tuple = (0.0, 0.0, 0.0)        # translation
    s: float = 1.0                    # uniform scale
    # object/model-space rest transform (objectjoints) and its inverse
    # (invobjectjoints), each a row-major 4x4 flattened to 16 floats. None until
    # the object matrix blocks are located + cross-validated. [stream-confirmed]
    object_bind: tuple | None = None
    inverse_bind: tuple | None = None

    @property
    def name(self) -> str:
        return f"joint_{self.index}"

    def as_dict(self) -> dict:
        d = {
            "index": self.index,
            "name": self.name,
            "name_hash": f"{self.name_hash:016x}",
            "parent": self.parent,
            "firstchild": self.firstchild,
            "nextsibling": self.nextsibling,
            "flags": self.flags,
            "local": {"r": list(self.r), "t": list(self.t), "s": self.s},
        }
        if self.object_bind is not None:
            d["object_bind"] = list(self.object_bind)
        if self.inverse_bind is not None:
            d["inverse_bind"] = list(self.inverse_bind)
        return d


@dataclass
class Skeleton:
    name_hash: int
    joints: list = field(default_factory=list)
    # provenance / confidence, per the module docstring
    has_hierarchy: bool = False       # parents came from a validated jointhierarchy
    has_names: bool = False           # names came from a validated jointlookup
    has_bindpose: bool = False        # local transforms decoded
    has_inverse_bind: bool = False    # object/inverse-bind matrices decoded
    # bindpose.transformsnu (CTransfQS 0x30, per-axis scale) populated on disk. The
    # importer decodes only the uniform CTransfQ path, so per-axis LOCAL scale is
    # dropped -- surfaced (never silently lost) via this flag + a loud note. [guard]
    has_nonuniform_scale: bool = False
    nonuniform_count: int = 0
    notes: list = field(default_factory=list)

    @property
    def joint_count(self) -> int:
        return len(self.joints)

    def is_tree(self) -> bool:
        """Every non-root parent in range and the parent chains are acyclic."""
        n = len(self.joints)
        for j in self.joints:
            if j.parent != -1 and not (0 <= j.parent < n):
                return False
        for start in range(n):
            seen = set()
            c = start
            steps = 0
            while self.joints[c].parent != -1:
                c = self.joints[c].parent
                if c in seen or steps > n:
                    return False
                seen.add(c)
                steps += 1
        return True

    def as_dict(self) -> dict:
        return {
            "name_hash": f"{self.name_hash:016x}",
            "joint_count": self.joint_count,
            "has_hierarchy": self.has_hierarchy,
            "has_names": self.has_names,
            "has_bindpose": self.has_bindpose,
            "has_inverse_bind": self.has_inverse_bind,
            "has_nonuniform_scale": self.has_nonuniform_scale,
            "nonuniform_count": self.nonuniform_count,
            "is_tree": self.is_tree(),
            "notes": list(self.notes),
            "joints": [j.as_dict() for j in self.joints],
        }


# --- low-level scans ---------------------------------------------------------

def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def _signed(v: int) -> int:
    return -1 if v == NONE else v


def _find_jointlookup(blob: bytes):
    """Find `u32 count; {u64 name, u32 index, u32 pad}[count]` whose indices are a
    permutation of [0,count). Returns (count, {index: name_hash}) or None.

    Picks the largest such map (the joint lookup; smaller ones are camera/rig
    lookups). [stream-confirmed]
    """
    best = None
    n = len(blob)
    for o in range(0, n - 4, 4):
        count = _u32(blob, o)
        if not (2 <= count <= MAX_JOINTS):
            continue
        if o + 4 + count * LOOKUP_STRIDE > n:
            continue
        recs = {}
        ok = True
        vals = []
        for i in range(count):
            ro = o + 4 + i * LOOKUP_STRIDE
            key, val, pad = struct.unpack_from("<QII", blob, ro)
            if key == 0 or pad != 0 or val >= count:
                ok = False
                break
            recs[val] = key
            vals.append(val)
        if not ok:
            continue
        if sorted(vals) != list(range(count)):
            continue
        if best is None or count > best[0]:
            best = (count, recs)
    return best


def _valid_forest(records) -> bool:
    n = len(records)
    roots = 0
    for i, (jn, par, fc, ns, fl) in enumerate(records):
        if jn == 0 or fl > 0xFFFF:
            return False
        for v in (par, fc, ns):
            if v != NONE and v >= n:
                return False
        if par == NONE:
            roots += 1
        elif par == i:
            return False
    if roots < 1:
        return False
    for start in range(n):
        seen = set()
        c = start
        steps = 0
        while records[c][1] != NONE:
            c = records[c][1]
            if c in seen or steps > n:
                return False
            seen.add(c)
            steps += 1
    return True


def _find_jointhierarchy(blob: bytes):
    """Find `u32 count; SSkeletonJoint[count]` forming a valid forest.
    Returns (count, [(name,parent,fc,ns,flags), ...]) or None. [stream-confirmed]
    """
    best = None
    n = len(blob)
    for o in range(0, n - 4, 4):
        count = _u32(blob, o)
        if not (1 <= count <= MAX_JOINTS):
            continue
        if o + 4 + count * JOINT_STRIDE > n:
            continue
        recs = [struct.unpack_from("<QIIII", blob, o + 4 + i * JOINT_STRIDE)
                for i in range(count)]
        if not _valid_forest(recs):
            continue
        if best is None or count > best[0]:
            best = (count, recs)
    return best


def _find_bindpose(blob: bytes, joint_count: int):
    """Find `u32 count; CTransfQ[count]` with count == joint_count and (nearly)
    unit quaternions. Returns list[(r, t, s)] or None. [stream-confirmed]
    """
    if joint_count <= 0:
        return None
    best = None            # (unit_score, transforms)
    n = len(blob)
    need = max(1, joint_count - 1)
    for o in range(0, n - 4, 4):
        if _u32(blob, o) != joint_count:
            continue
        if o + 4 + joint_count * TRANSFORM_STRIDE > n:
            continue
        transforms = []
        unit = 0
        for i in range(joint_count):
            base = o + 4 + i * TRANSFORM_STRIDE
            r = struct.unpack_from("<4f", blob, base)
            t = struct.unpack_from("<3f", blob, base + 0x10)
            s = struct.unpack_from("<f", blob, base + 0x1C)[0]
            qn = math.sqrt(sum(c * c for c in r))
            if 0.9 < qn < 1.1:
                unit += 1
            transforms.append((r, t, s))
        if unit >= need and (best is None or unit > best[0]):
            best = (unit, transforms)
    return best[1] if best else None


# --- object-space / inverse-bind matrices (objectjoints/invobjectjoints) ------
#
# 4x4 matrices are represented as a flat tuple of 16 floats, ROW-major, so that a
# point is transformed as p' = M . [p, 1]. Pure stdlib (no numpy/mathutils) so this
# stays importable inside Blender and testable without either.

_IDENT4 = (1.0, 0.0, 0.0, 0.0,
           0.0, 1.0, 0.0, 0.0,
           0.0, 0.0, 1.0, 0.0,
           0.0, 0.0, 0.0, 1.0)


def _mat_from_transfq(r, t, s) -> tuple:
    """Row-major 4x4 from a CTransfQ (quaternion r x,y,z,w; translation t; uniform
    scale s). Rotation columns are scaled by s; translation goes in the last column."""
    x, y, z, w = r
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return (
        (1 - 2 * (yy + zz)) * s, (2 * (xy - wz)) * s, (2 * (xz + wy)) * s, t[0],
        (2 * (xy + wz)) * s, (1 - 2 * (xx + zz)) * s, (2 * (yz - wx)) * s, t[1],
        (2 * (xz - wy)) * s, (2 * (yz + wx)) * s, (1 - 2 * (xx + yy)) * s, t[2],
        0.0, 0.0, 0.0, 1.0,
    )


def _mat_mul(a, b) -> tuple:
    out = [0.0] * 16
    for i in range(4):
        for j in range(4):
            out[i * 4 + j] = (a[i * 4] * b[j] + a[i * 4 + 1] * b[4 + j]
                              + a[i * 4 + 2] * b[8 + j] + a[i * 4 + 3] * b[12 + j])
    return tuple(out)


def _mat_max_abs_diff(a, b) -> float:
    return max(abs(a[i] - b[i]) for i in range(16))


def _read_transfq(blob: bytes, o: int):
    r = struct.unpack_from("<4f", blob, o)
    t = struct.unpack_from("<3f", blob, o + 0x10)
    s = struct.unpack_from("<f", blob, o + 0x1C)[0]
    return r, t, s


def _find_transfq_size_blocks(blob: bytes, joint_count: int):
    """Find CMemBlockAttach blocks framed as `u32 size_bytes; CTransfQ[joint_count]`
    where `size_bytes == joint_count * 0x20` and every quaternion is ~unit. Returns
    a list of (data_offset, [(r,t,s), ...]). [stream-confirmed]

    The leading u32 is the *byte size* (joint_count*0x20), which distinguishes these
    from the bind-pose `transforms` table (whose leading u32 is the element *count*).
    """
    if joint_count <= 0:
        return []
    want = joint_count * TRANSFORM_STRIDE
    blocks = []
    n = len(blob)
    for o in range(8, n - 4, 4):
        if _u32(blob, o) != want:
            continue
        if o + 4 + want > n:
            continue
        transforms = []
        ok = True
        for i in range(joint_count):
            r, t, s = _read_transfq(blob, o + 4 + i * TRANSFORM_STRIDE)
            qn = math.sqrt(sum(c * c for c in r))
            if not (0.85 < qn < 1.15):
                ok = False
                break
            transforms.append((r, t, s))
        if ok:
            blocks.append((o + 4, transforms))
    return blocks


def _find_object_matrices(blob: bytes, joint_count: int):
    """Locate objectjoints (object-space bind) + invobjectjoints (inverse-bind).

    They serialize as two adjacent `{u32 size; CTransfQ[N]}` blocks (N==joint_count).
    Accept the first adjacent pair for which object[i] @ inverse[i] == identity (the
    defining property of an inverse-bind pair). Returns (object_mats, inverse_mats)
    as parallel lists of row-major 4x4 (16-float) matrices, or (None, None).
    [stream-confirmed on 5 full skeletons of 0703fd2acd5803e9]
    """
    blocks = _find_transfq_size_blocks(blob, joint_count)
    if len(blocks) < 2:
        return None, None
    for a in range(len(blocks) - 1):
        obj = [_mat_from_transfq(*trs) for trs in blocks[a][1]]
        inv = [_mat_from_transfq(*trs) for trs in blocks[a + 1][1]]
        if all(_mat_max_abs_diff(_mat_mul(obj[i], inv[i]), _IDENT4) < 1e-2
               for i in range(joint_count)):
            return obj, inv
    return None, None


def _disk_pose_counts(blob: bytes):
    """Deterministic positional read of the SAnimPoseData + jointhierarchy element
    counts. The on-disk stream serializes CSkeletonData in *declaration order*, so
    walking  name -> usageoffsets -> SAnimPoseData(space,pad,reals,transforms,
    transformsnu,symbols) -> rigs -> jointhierarchy  yields exact counts. Returns
    `(reals, transf, transfnu, symbols, rigs, jh)`, or None if the walk is internally
    inconsistent (caller then relies on the scans alone).

    `transfnu` is the CTransfQS (0x30, per-axis scale) `transformsnu` element count --
    the authoritative on-disk signal that a rig carries NON-UNIFORM joint scale. It is
    reachable here (inside SAnimPoseData, which the walk traverses) whereas the
    CSkeletonData.nonuniformjointscale flag @+0x1d8 is serialized AFTER jointhierarchy
    and the un-framed jointgroups CTableXT, so it is NOT positionally reachable.

    `jh` (jointhierarchy count) makes the names-only verdict authoritative: where it is
    0 the hierarchy is genuinely *absent* on disk (not merely unmatched by the scanner).
    [stream-confirmed: byte-exact jointhierarchy offset on every full skeleton.]
    """
    try:
        n = len(blob)

        def table(off, stride):
            c = _u32(blob, off)
            return c, off + 4 + c * stride

        o = 8                                    # after CSymbol64 name
        usage, o = table(o, 4)                   # usageoffsets CTable<uint> (14)
        if usage > 64:
            return None
        space = _u32(blob, o); o += 4            # SAnimPoseData.space
        o += 4                                   # _pad
        if space != 0:                           # only local-space seen; bail otherwise
            return None
        reals, o = table(o, 4)                   # reals   CTable<float>
        transf, o = table(o, TRANSFORM_STRIDE)   # transforms   CTable<CTransfQ 0x20>
        transfnu, o = table(o, 0x30)             # transformsnu CTable<CTransfQS 0x30>
        symbols, o = table(o, 8)                 # symbols CTable<CSymbol64>
        rigs, o = table(o, 0x40)                 # rigs    CTable<SSkeletonRig 0x40>
        if o + 4 > n:
            return None
        jh = _u32(blob, o)                        # jointhierarchy count
        if not all(0 <= c <= MAX_JOINTS
                   for c in (reals, transf, transfnu, symbols, rigs, jh)):
            return None
        if o + 4 + jh * JOINT_STRIDE > n:
            return None
        return (reals, transf, transfnu, symbols, rigs, jh)
    except struct.error:
        return None


def _disk_jointhierarchy_count(blob: bytes):
    """Back-compat thin wrapper: the authoritative on-disk jointhierarchy element
    count (or None). See `_disk_pose_counts` for the full walk."""
    counts = _disk_pose_counts(blob)
    return counts[5] if counts is not None else None


# --- public API --------------------------------------------------------------

def decode_skeleton(blob: bytes) -> Skeleton:
    """Decode a decompressed CSkeletonResource primary slice into a Skeleton.

    Robust to short / partial / non-standard slices: always returns a Skeleton
    (at minimum with `name_hash`), never raises on malformed content.
    """
    if len(blob) < 8:
        return Skeleton(name_hash=0, notes=["slice too short"])
    name_hash = struct.unpack_from("<Q", blob, 0)[0]
    skel = Skeleton(name_hash=name_hash)

    lookup = _find_jointlookup(blob)
    hier = _find_jointhierarchy(blob)

    idx2name = lookup[1] if lookup else {}
    lookup_n = lookup[0] if lookup else 0

    # Trust the hierarchy only if it agrees with the lookup (same count and its
    # per-index names match), OR if there is no lookup at all to check against.
    hier_records = None
    if hier is not None:
        hn, hrecs = hier
        if lookup is None:
            hier_records = hrecs
            skel.notes.append("hierarchy accepted without a lookup cross-check")
        elif hn == lookup_n and all(
                hrecs[i][0] == idx2name.get(i) for i in range(hn)):
            hier_records = hrecs
            skel.notes.append("hierarchy cross-validated against jointlookup")
        else:
            skel.notes.append(
                f"hierarchy candidate (n={hn}) rejected: disagrees with "
                f"jointlookup (n={lookup_n}); using flat hierarchy")

    joint_count = 0
    if hier_records is not None:
        joint_count = len(hier_records)
        skel.has_hierarchy = True
    elif lookup is not None:
        joint_count = lookup_n

    # Authoritative on-disk pose/hierarchy counts (deterministic stream walk).
    pose_counts = _disk_pose_counts(blob)

    # GUARD (do-soon #1): non-uniform joint scale. transformsnu (CTransfQS 0x30,
    # per-axis scale) is never decoded -- only the uniform CTransfQ path is -- so if it
    # is populated the LOCAL bind pose silently loses its per-axis scale. The
    # objectjoints/invobjectjoints blocks are uniform CTransfQ by struct definition, so
    # object@inverse==I still passes and the loss would otherwise be invisible. Surface
    # it loudly instead of importing a wrong uniform approximation.
    if pose_counts is not None and pose_counts[2] > 0:
        skel.has_nonuniform_scale = True
        skel.nonuniform_count = pose_counts[2]
        skel.notes.append(
            f"NON-UNIFORM JOINT SCALE PRESENT: bindpose.transformsnu.count == "
            f"{pose_counts[2]} (CTransfQS 0x30, per-axis scale); the importer decodes "
            f"only the uniform CTransfQ approximation, so per-axis LOCAL scale is "
            f"DROPPED [gate CSkeletonData.nonuniformjointscale@+0x1d8, name-only]")
    elif pose_counts is None:
        skel.notes.append(
            "transformsnu status UNKNOWN (positional pose walk inconclusive); relying "
            "on the FK-consistency check for non-uniform-scale detection")

    # Where we did NOT accept a hierarchy, distinguish "genuinely no hierarchy
    # serialized" (count==0) from "a smaller hierarchy exists but was rejected".
    if not skel.has_hierarchy:
        disk_jh = pose_counts[5] if pose_counts is not None else None
        if disk_jh == 0:
            skel.notes.append(
                "jointhierarchy.count == 0 on disk: no hierarchy/bind pose is "
                "serialized in this resource; names-only is authoritative "
                "[stream-confirmed]")
        elif disk_jh is not None and lookup is not None and 0 < disk_jh < lookup_n:
            skel.notes.append(
                f"a smaller {disk_jh}-joint hierarchy IS serialized (< {lookup_n} "
                f"named joints); kept flat because it cannot be mapped 1:1 onto the "
                f"jointlookup index space")

    if joint_count == 0:
        skel.notes.append("no jointhierarchy or jointlookup found (name only)")
        return skel

    bind = _find_bindpose(blob, joint_count)
    if bind is not None:
        skel.has_bindpose = True

    skel.has_names = bool(idx2name)

    for i in range(joint_count):
        if hier_records is not None:
            jn, par, fc, ns, fl = hier_records[i]
        else:
            jn = idx2name.get(i, 0)
            par = fc = ns = NONE
            fl = 0
        j = Joint(
            index=i,
            name_hash=jn or idx2name.get(i, 0),
            parent=_signed(par),
            firstchild=_signed(fc),
            nextsibling=_signed(ns),
            flags=fl,
        )
        if bind is not None:
            r, t, s = bind[i]
            j.r, j.t, j.s = tuple(r), tuple(t), s
        skel.joints.append(j)

    # objectjoints (object-space bind) + invobjectjoints (inverse-bind) matrices.
    object_mats, inverse_mats = _find_object_matrices(blob, joint_count)
    if object_mats is not None:
        skel.has_inverse_bind = True
        for i, j in enumerate(skel.joints):
            j.object_bind = object_mats[i]
            j.inverse_bind = inverse_mats[i]
        note = ("object/inverse-bind matrices decoded (objectjoints + "
                "invobjectjoints, CTransfQ blocks); object[i]@inverse[i]==I "
                "[stream-confirmed]")
        # extra corroboration when a local bind pose + hierarchy are also present
        if skel.has_bindpose and skel.has_hierarchy:
            local = [_mat_from_transfq(j.r, j.t, j.s) for j in skel.joints]
            world = [None] * joint_count

            def _world(i):
                if world[i] is not None:
                    return world[i]
                p = skel.joints[i].parent
                world[i] = local[i] if p < 0 else _mat_mul(_world(p), local[i])
                return world[i]

            worst = max(_mat_max_abs_diff(_world(i), object_mats[i])
                        for i in range(joint_count))
            if worst < 1e-2:
                note += "; object[i]==FK(local) (max diff %.1e)" % worst
            else:
                # object != FK(local): the uniform local bind cannot reconstruct the
                # object-space rest. A likely cause is unmodeled per-axis scale
                # (transformsnu). Warn loudly; inverse_bind is kept (it is correct on
                # disk) but the LOCAL bind pose is approximate. [secondary signal]
                skel.notes.append(
                    "WARNING: object[i] != FK(local) (max diff %.1e) -- the uniform "
                    "local bind is approximate (possible non-uniform joint scale / "
                    "unmodeled transform); inverse_bind kept, local bind approximate"
                    % worst)
        skel.notes.append(note)

    return skel


# --- skin-weight grouping (pure; mirrored by the bpy addon) ------------------

def joint_group_name(joint_names, idx: int) -> str:
    """Vertex-group / bone name: a joint name from skeleton.json if available,
    else the stable fallback `joint_<idx>`."""
    if joint_names:
        n = joint_names.get(idx)
        if n:
            return n
    return f"joint_{idx}"


def skin_vertex_groups(skin_indices, skin_weights, comps: int, n_verts: int,
                       joint_names=None) -> dict:
    """Map skin indices/weights to `{group_name: [(vertex_index, weight), ...]}`.

    Zero (and negative) weights are dropped. This is the exact grouping the
    Blender addon applies to `bpy` vertex groups, factored out so it is testable
    without Blender.
    """
    groups: dict = {}
    if not skin_indices or not skin_weights or comps <= 0:
        return groups
    for vi in range(n_verts):
        base = vi * comps
        for c in range(comps):
            k = base + c
            if k >= len(skin_indices) or k >= len(skin_weights):
                break
            w = float(skin_weights[k])
            if w <= 0.0:
                continue
            name = joint_group_name(joint_names, int(skin_indices[k]))
            groups.setdefault(name, []).append((vi, w))
    return groups


# --- synthetic builder (for tests; mirrors the confirmed on-disk framing) -----

def _pack_transfq(r, t, s) -> bytes:
    return struct.pack("<4f", *r) + struct.pack("<3f", *t) + struct.pack("<f", s)


def build_skeleton_slice(name_hash: int, joints, transforms=None,
                         *, with_lookup=True, with_hierarchy=True,
                         object_transforms=None, invobject_transforms=None) -> bytes:
    """Assemble a minimal CSkeletonData-like slice the scanner can decode.

    `joints`: list of (name_hash, parent, firstchild, nextsibling, flags) with
              -1 meaning "none".
    `transforms`: optional list of (r(4), t(3), s) parallel to `joints` (local bind).
    `object_transforms` / `invobject_transforms`: optional parallel CTransfQ lists
              serialized as the objectjoints/invobjectjoints `{u32 size; CTransfQ[N]}`
              blocks (size == N*0x20). Provide inverse pairs to exercise the
              object/inverse-bind decode.

    The real serialization has many more fields; the decoder locates the tables by
    scanning, so this compact layout is sufficient and representative.
    """
    def enc(v):
        return NONE if v == -1 else v

    def size_block(rows):
        b = struct.pack("<I", len(rows) * TRANSFORM_STRIDE)
        for (r, t, s) in rows:
            b += _pack_transfq(r, t, s)
        return b

    out = bytearray()
    out += struct.pack("<Q", name_hash)      # name @ +0x00
    out += b"\x00" * 16                       # filler (stands in for usageoffsets etc.)

    if with_hierarchy:
        out += struct.pack("<I", len(joints))
        for (jn, par, fc, ns, fl) in joints:
            out += struct.pack("<QIIII", jn, enc(par), enc(fc), enc(ns), fl)

    if transforms is not None:
        out += struct.pack("<I", len(transforms))
        for (r, t, s) in transforms:
            out += _pack_transfq(r, t, s)

    if object_transforms is not None:
        out += size_block(object_transforms)
    if invobject_transforms is not None:
        out += size_block(invobject_transforms)

    out += b"\x00" * 8                         # separator

    if with_lookup:
        out += struct.pack("<I", len(joints))
        for i, (jn, *_rest) in enumerate(joints):
            out += struct.pack("<QII", jn, i, 0)

    return bytes(out)
