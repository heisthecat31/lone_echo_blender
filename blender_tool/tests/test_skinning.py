"""Skinning / skeleton decode tests (pure stdlib, no bpy, no game files).

Covers: synthetic CSkeletonData decode (names / parents / bind transforms),
the jointlookup-only fallback, robustness on short/garbage slices, tree
validation, and the skin-weight -> vertex-group grouping logic.
"""

import math
import struct

from le_mesh import skinning


# A tiny 4-joint hierarchy: 0 root -> 1 -> 2 ; 0 -> 3
JOINTS = [
    # (name_hash, parent, firstchild, nextsibling, flags)
    (0xAAAA0000AAAA0000, -1, 1, -1, 0),
    (0xBBBB1111BBBB1111,  0, 2, 3, 0),
    (0xCCCC2222CCCC2222,  1, -1, -1, 0),
    (0xDDDD3333DDDD3333,  0, -1, -1, 0),
]
# parallel local bind transforms (unit quaternions + small translations)
TRANSFORMS = [
    ((0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0), 1.0),
    ((0.0, 0.0, 0.0, 1.0), (0.5, 0.0, 0.0), 1.0),
    ((0.0, 0.0, 0.7071, 0.7071), (0.25, 0.0, 0.0), 1.0),
    ((0.7071, 0.0, 0.0, 0.7071), (0.0, 0.3, 0.0), 1.0),
]


def test_decode_full_skeleton():
    blob = skinning.build_skeleton_slice(0x1234567890ABCDEF, JOINTS, TRANSFORMS)
    skel = skinning.decode_skeleton(blob)

    assert skel.name_hash == 0x1234567890ABCDEF
    assert skel.joint_count == 4
    assert skel.has_hierarchy is True
    assert skel.has_names is True
    assert skel.has_bindpose is True
    assert skel.is_tree() is True

    # names + hierarchy match the input, in index order
    assert [j.name_hash for j in skel.joints] == [j[0] for j in JOINTS]
    assert [j.parent for j in skel.joints] == [-1, 0, 1, 0]
    assert [j.firstchild for j in skel.joints] == [1, 2, -1, -1]
    assert [j.nextsibling for j in skel.joints] == [-1, 3, -1, -1]

    # stable joint names
    assert skel.joints[0].name == "joint_0"
    assert skel.joints[3].name == "joint_3"

    # bind transforms round-trip (float32 tolerance)
    j2 = skel.joints[2]
    assert math.isclose(j2.r[2], 0.7071, abs_tol=1e-4)
    assert math.isclose(j2.t[0], 0.25, abs_tol=1e-4)
    assert math.isclose(j2.s, 1.0, abs_tol=1e-6)

    # inverse_bind is intentionally not decoded yet (needs-disasm)
    assert skel.joints[0].inverse_bind is None

    # dict form is JSON-friendly
    d = skel.as_dict()
    assert d["joint_count"] == 4 and d["is_tree"] is True
    assert d["joints"][1]["name_hash"] == f"{JOINTS[1][0]:016x}"


def test_decode_lookup_only_flat_hierarchy():
    # no hierarchy table, only the jointlookup -> names present, flat parenting
    blob = skinning.build_skeleton_slice(0xFEED, JOINTS, transforms=None,
                                         with_hierarchy=False, with_lookup=True)
    skel = skinning.decode_skeleton(blob)
    assert skel.joint_count == 4
    assert skel.has_names is True
    assert skel.has_hierarchy is False
    assert all(j.parent == -1 for j in skel.joints)     # flat
    assert [j.name_hash for j in skel.joints] == [j[0] for j in JOINTS]
    assert skel.is_tree() is True                       # all-roots forest is a tree


def test_decode_name_only_is_robust():
    # just a name and filler -> no joints, no crash
    blob = struct.pack("<Q", 0xABCDEF) + b"\x00" * 40
    skel = skinning.decode_skeleton(blob)
    assert skel.name_hash == 0xABCDEF
    assert skel.joint_count == 0
    assert skel.is_tree() is True

    # truncated / empty slices never raise
    assert skinning.decode_skeleton(b"").joint_count == 0
    assert skinning.decode_skeleton(b"\x01\x02\x03").joint_count == 0


def test_hierarchy_rejected_when_disagrees_with_lookup():
    # Build a valid hierarchy, then append a lookup whose names differ: the
    # decoder must fall back to the (name) lookup, not trust the mismatched tree.
    good = skinning.build_skeleton_slice(0x1, JOINTS, transforms=None,
                                         with_hierarchy=True, with_lookup=False)
    # different-named lookup of the same length
    alt = [(0x9000 + i, i, 0) for i in range(len(JOINTS))]
    lut = struct.pack("<I", len(alt))
    for name, idx, pad in alt:
        lut += struct.pack("<QII", name, idx, pad)
    skel = skinning.decode_skeleton(good + b"\x00" * 8 + lut)
    # hierarchy disagrees with lookup -> not trusted
    assert skel.has_hierarchy is False
    assert any("rejected" in n or "flat" in n for n in skel.notes)


def test_is_tree_detects_cycle():
    # manual cyclic parent set must be reported as not-a-tree
    skel = skinning.Skeleton(name_hash=1, joints=[
        skinning.Joint(0, 0xA, parent=1, firstchild=-1, nextsibling=-1, flags=0),
        skinning.Joint(1, 0xB, parent=0, firstchild=-1, nextsibling=-1, flags=0),
    ])
    assert skel.is_tree() is False


def test_skin_vertex_groups_grouping():
    # 2 verts, 4 influences each; weight 0 is dropped
    skin_indices = [0, 1, 2, 3,   1, 1, 0, 0]
    skin_weights = [0.6, 0.4, 0.0, 0.0,   0.5, 0.0, 0.5, 0.0]
    groups = skinning.skin_vertex_groups(skin_indices, skin_weights, 4, 2)
    assert set(groups) == {"joint_0", "joint_1"}
    assert (0, 0.6) in groups["joint_0"]
    assert (1, 0.5) in groups["joint_0"]
    assert (0, 0.4) in groups["joint_1"]
    # zero-weight influences never create a group
    assert "joint_2" not in groups and "joint_3" not in groups


def test_skin_vertex_groups_uses_joint_names():
    names = {0: "wrist", 1: "thumb"}
    groups = skinning.skin_vertex_groups([0, 1], [1.0, 1.0], 2, 1, joint_names=names)
    assert set(groups) == {"wrist", "thumb"}
    # missing name falls back to joint_<idx>
    groups2 = skinning.skin_vertex_groups([5], [1.0], 1, 1, joint_names=names)
    assert set(groups2) == {"joint_5"}


# --- matrix helpers ----------------------------------------------------------

def test_mat_from_transfq_and_mul():
    ident = skinning._mat_from_transfq((0, 0, 0, 1), (0, 0, 0), 1.0)
    assert skinning._mat_max_abs_diff(ident, skinning._IDENT4) < 1e-6

    # pure translation lands in the last column (row-major, point as column)
    trans = skinning._mat_from_transfq((0, 0, 0, 1), (2.0, -3.0, 4.0), 1.0)
    assert (trans[3], trans[7], trans[11]) == (2.0, -3.0, 4.0)

    # T(t) @ T(-t) == identity
    inv = skinning._mat_from_transfq((0, 0, 0, 1), (-2.0, 3.0, -4.0), 1.0)
    assert skinning._mat_max_abs_diff(skinning._mat_mul(trans, inv),
                                      skinning._IDENT4) < 1e-6

    # a 90deg rotation about Z composed with its conjugate == identity
    import math as _m
    h = _m.sqrt(0.5)
    rz = skinning._mat_from_transfq((0, 0, h, h), (0, 0, 0), 1.0)
    rz_inv = skinning._mat_from_transfq((0, 0, -h, h), (0, 0, 0), 1.0)
    assert skinning._mat_max_abs_diff(skinning._mat_mul(rz, rz_inv),
                                      skinning._IDENT4) < 1e-5


# --- object / inverse-bind matrices ------------------------------------------

def test_decode_object_and_inverse_bind():
    # objectjoints = pure translations; invobjectjoints = their inverses.
    obj = [((0, 0, 0, 1), (float(i), 0.0, 0.0), 1.0) for i in range(len(JOINTS))]
    inv = [((0, 0, 0, 1), (-float(i), 0.0, 0.0), 1.0) for i in range(len(JOINTS))]
    blob = skinning.build_skeleton_slice(
        0xABCD, JOINTS, TRANSFORMS, object_transforms=obj, invobject_transforms=inv)
    skel = skinning.decode_skeleton(blob)

    assert skel.has_inverse_bind is True
    assert skel.as_dict()["has_inverse_bind"] is True
    j2 = skel.joints[2]
    assert j2.object_bind is not None and j2.inverse_bind is not None
    assert len(j2.object_bind) == 16 and len(j2.inverse_bind) == 16
    # object_bind holds the object-space translation in the last column
    assert math.isclose(j2.object_bind[3], 2.0, abs_tol=1e-5)
    assert math.isclose(j2.inverse_bind[3], -2.0, abs_tol=1e-5)
    # object @ inverse == identity (the defining inverse-bind property)
    prod = skinning._mat_mul(j2.object_bind, j2.inverse_bind)
    assert skinning._mat_max_abs_diff(prod, skinning._IDENT4) < 1e-4
    # emitted in the JSON dict
    assert "object_bind" in skel.as_dict()["joints"][2]
    assert "inverse_bind" in skel.as_dict()["joints"][2]


def test_inverse_bind_rejected_without_valid_pair():
    # a single object block (no inverse partner) must NOT yield inverse_bind
    obj = [((0, 0, 0, 1), (float(i), 0.0, 0.0), 1.0) for i in range(len(JOINTS))]
    blob = skinning.build_skeleton_slice(
        0xBEEF, JOINTS, TRANSFORMS, object_transforms=obj)
    skel = skinning.decode_skeleton(blob)
    assert skel.has_inverse_bind is False
    assert all(j.inverse_bind is None for j in skel.joints)


def test_names_only_has_no_inverse_bind():
    # lookup-only slice: no object matrices, inverse_bind stays absent
    blob = skinning.build_skeleton_slice(0xFEED, JOINTS, transforms=None,
                                         with_hierarchy=False, with_lookup=True)
    skel = skinning.decode_skeleton(blob)
    assert skel.has_inverse_bind is False
    assert "inverse_bind" not in skel.as_dict()["joints"][0]


# --- non-uniform joint scale guard (transformsnu) ----------------------------

def _pose_walk_slice(name_hash, *, transf=0, transfnu=0, jh=0,
                     reals=0, symbols=0, rigs=0):
    """Build a slice matching the on-disk positional stream that `_disk_pose_counts`
    walks: name; usageoffsets; SAnimPoseData(space,_pad,reals,transforms,transformsnu,
    symbols); rigs; jointhierarchy. Table CONTENTS are zero-filled (the walk reads only
    the counts), sized so the final bounds check passes. This exercises the positional
    walk / guard, which the scan-oriented `build_skeleton_slice` cannot."""
    b = bytearray()
    b += struct.pack("<Q", name_hash)                              # name
    b += struct.pack("<I", 0)                                      # usageoffsets count
    b += struct.pack("<I", 0)                                      # SAnimPoseData.space
    b += struct.pack("<I", 0)                                      # _pad
    b += struct.pack("<I", reals) + b"\x00" * (reals * 4)          # reals   CTable<float>
    b += struct.pack("<I", transf) + b"\x00" * (transf * 0x20)     # transforms  CTransfQ
    b += struct.pack("<I", transfnu) + b"\x00" * (transfnu * 0x30)  # transformsnu CTransfQS
    b += struct.pack("<I", symbols) + b"\x00" * (symbols * 8)      # symbols CSymbol64
    b += struct.pack("<I", rigs) + b"\x00" * (rigs * 0x40)         # rigs    SSkeletonRig
    b += struct.pack("<I", jh) + b"\x00" * (jh * 0x18)             # jointhierarchy
    return bytes(b)


def test_disk_pose_counts_reads_transformsnu():
    blob = _pose_walk_slice(0xABCD, transf=5, transfnu=5, jh=5, symbols=5)
    counts = skinning._disk_pose_counts(blob)
    assert counts is not None
    reals, transf, transfnu, symbols, rigs, jh = counts
    assert (transf, transfnu, jh) == (5, 5, 5)
    # back-compat wrapper still returns just the jointhierarchy count
    assert skinning._disk_jointhierarchy_count(blob) == 5


def test_nonuniform_scale_guard_fires():
    # a skeleton whose bindpose carries transformsnu (per-axis scale) must be flagged
    blob = _pose_walk_slice(0x1111, transf=8, transfnu=8, jh=8, symbols=8)
    skel = skinning.decode_skeleton(blob)
    assert skel.has_nonuniform_scale is True
    assert skel.nonuniform_count == 8
    assert skel.as_dict()["has_nonuniform_scale"] is True
    assert any("NON-UNIFORM JOINT SCALE" in n for n in skel.notes)


def test_uniform_skeleton_not_flagged():
    # transformsnu == 0 -> no non-uniform flag, no loud note
    blob = _pose_walk_slice(0x2222, transf=8, transfnu=0, jh=8, symbols=8)
    skel = skinning.decode_skeleton(blob)
    assert skel.has_nonuniform_scale is False
    assert skel.nonuniform_count == 0
    assert not any("NON-UNIFORM JOINT SCALE" in n for n in skel.notes)
