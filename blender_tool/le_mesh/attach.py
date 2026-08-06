"""Where a two-part character's HEAD (or any model component) actually sits.

★ 2026-08-05. Lone Echo characters are two resources: the body mesh-list, bound
to an actor node as component `ncaModel`, and the head mesh-list bound to the
SAME node as component `head` (Liv: actor `1d6d5746a7f89a9f` in
`956a00b1a4b3c37e`, `ncaModel` -> `ff91757c910ea7b6`, `head` ->
`3ae4822821fa8562`). Every picture in `exports/hero` before this module placed
the head with a hand-picked `+1.64` Z and said so.

What the runtime does, and what it does NOT do
----------------------------------------------
⛔ It is NOT a stored component transform. `SModelCD::SInitData` has
`attachmode` /*+0x38*/, `transformname`
/*+0x40*/, `attachmodel` /*+0x48*/ and `attachname` /*+0x50*/, and
`CTransformCS::InitializeModelAttach` (offset 0x187ff0 in the build-verified
the game executable) can attach a component to a joint or ref-point via
`CTransformCS::AttachToJointOrRefPoint` (offset 0x185e70) — but on
`CModelCRWin7 956a00b1a4b3c37e` **both** `SInitData` rows read `attachmode = 0`
with `transformname` / `attachmodel` / `attachname` all NULL, and all three rows
of `CTransformCRWin7 956a00b1a4b3c37e` read `parenttype = eNone` with an identity
transform (`stream-confirmed`). Body and head share ONE identity node transform.

★ The head's placement therefore comes from SKINNING, through the shared joint
NAME SPACE. `liv_head` is a 14-joint rig whose root is `EXP_C1_Head1`
(`symbol64 == dc009aa0b878fd03`, a verified preimage in `hash_lookup.json`) and
whose own bind pose is the identity; the body's 219-joint rig carries a joint of
that same name-hash whose object-space bind matrix is the head's world seat.
Placing the head is then exactly

    M = body.object_bind[j] @ head.inverse_bind[root]      # `stream-confirmed`

with `j` the body joint whose `name_hash` equals the head rig's root name-hash.
On Liv that is body joint **168** and

    M = scale(1.06) * translate(0, +1.650622, -0.027587)   # ASSET space, Y up

— i.e. the eyeballed `+1.64` was the body's own AABB top and silently dropped
both the **1.06 uniform scale** and the -0.0276 depth.

⚠ This is the RIGID placement. Full fidelity is per-vertex:
`sum_j w_j * (body.object_bind[name_hash_j] @ head.inverse_bind[j])`, which also
animates; the two head-only joints (Liv: `1da466721cd370db`, `1dba66721cd370db`,
children of the head root) have no body twin and stay local.

Pure stdlib, no bpy, no numpy — unit tested outside Blender.
"""

from __future__ import annotations

# A 4x4 is a flat row-major list of 16 floats, the same shape
# `extractor/le_skeleton.py` writes for `object_bind` / `inverse_bind`.

IDENTITY4 = [1.0, 0.0, 0.0, 0.0,
             0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0,
             0.0, 0.0, 0.0, 1.0]


def mat4_mul(a: list, b: list) -> list:
    """Row-major 4x4 product `a @ b`."""
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[r * 4 + c] = sum(a[r * 4 + k] * b[k * 4 + c] for k in range(4))
    return out


def _joints(skel) -> list:
    """Accept either the `le_skeleton` JSON dict or a bare joint list."""
    if isinstance(skel, dict):
        return skel.get("joints") or []
    return list(skel or [])


def component_attach_matrix(host_skeleton, part_skeleton) -> dict:
    """Where `part` sits on `host`, decoded from the shared joint name space.

    Returns::

        {"matrix": [16 floats, row-major, ASSET space],
         "host_joint_index": int, "host_joint_name_hash": str,
         "part_root_index": int, "shared_joints": int, "part_joints": int,
         "confidence": "stream-confirmed" | "unresolved", "note": str}

    `confidence` is `unresolved` (and `matrix` the identity) when the part's root
    joint name-hash has no twin in the host rig — in which case the caller must
    say the placement is undecoded rather than substitute a guess.
    """
    hj, pj = _joints(host_skeleton), _joints(part_skeleton)
    if not pj:
        return {"matrix": list(IDENTITY4), "host_joint_index": -1,
                "host_joint_name_hash": "", "part_root_index": -1,
                "shared_joints": 0, "part_joints": 0, "host_joints": len(hj),
                "host_roots": 0, "part_roots": 0, "same_rig": False,
                "confidence": "unresolved", "note": "part rig has no joints"}

    # ⚠ THE RIGS ARE FORESTS, not trees. `64b4b5b2a0153f7e` (Jack, 188 joints),
    # `916a82bd119c330f` (188) and `3a80cdb80b7e60c0` (219) each carry SEVERAL
    # parentless joints (`stream-confirmed`: indices 0, 1, 2, ... are all
    # `parent == -1`), so "the root" is index-order dependent. That is harmless
    # for a genuine component rig -- `liv_head` has 14 joints and exactly one
    # root, `EXP_C1_Head1` -- but it means the count is reported rather than
    # assumed.
    host_roots = sum(1 for j in hj if int(j.get("parent", -1)) < 0)
    part_roots = sum(1 for j in pj if int(j.get("parent", -1)) < 0)
    root_i = next((i for i, j in enumerate(pj) if int(j.get("parent", -1)) < 0), 0)
    root = pj[root_i]
    want = str(root.get("name_hash", ""))
    by_hash = {str(j.get("name_hash", "")): j for j in hj}
    shared = sum(1 for j in pj if str(j.get("name_hash", "")) in by_hash)
    # ★ SAME RIG, not an attachment. `916a82bd119c330f` -- the 188-joint android
    # head/helmet asset -- carries the WHOLE body rig: 188 of 188 joint name
    # hashes shared with `64b4b5b2a0153f7e`, and `component_attach_matrix`
    # accordingly returns the identity (`stream-confirmed`, 2026-08-05). That is
    # a different fact from "the attach resolved to the identity by luck", and a
    # caller must be able to tell them apart: the two assets are ALREADY in one
    # object space and any `pkg_offset` applied to them is a fabrication.
    same_rig = bool(hj) and {str(j.get("name_hash", "")) for j in pj} == \
        {str(j.get("name_hash", "")) for j in hj}
    host = by_hash.get(want)
    if host is None or not host.get("object_bind") or not root.get("inverse_bind"):
        return {"matrix": list(IDENTITY4), "host_joint_index": -1,
                "host_joint_name_hash": want, "part_root_index": root_i,
                "shared_joints": shared, "part_joints": len(pj),
                "host_joints": len(hj), "host_roots": host_roots,
                "part_roots": part_roots, "same_rig": same_rig,
                "confidence": "unresolved",
                "note": f"host rig carries no joint {want} (or no bind matrices)"}

    m = mat4_mul(list(host["object_bind"]), list(root["inverse_bind"]))
    return {"matrix": m,
            "host_joint_index": int(host.get("index", -1)),
            "host_joint_name_hash": want,
            "part_root_index": root_i,
            "shared_joints": shared, "part_joints": len(pj),
            "host_joints": len(hj), "host_roots": host_roots,
            "part_roots": part_roots, "same_rig": same_rig,
            "confidence": "stream-confirmed",
            "note": ("the part carries the WHOLE host rig -- one object space, "
                     "no component attach" if same_rig else
                     "M = host.object_bind[j] @ part.inverse_bind[root]")}


def asset_matrix_to_blender(m: list) -> list:
    """Re-express an ASSET-space (Y up) 4x4 in Blender's Z up, row-major.

    The importer's `y_up_to_z_up` is `(x, y, z)_asset -> (x, -z, y)_blender`,
    i.e. `B = P @ M @ P^-1` with `P = [[1,0,0],[0,0,-1],[0,1,0]]`. Written out
    rather than imported so this module stays bpy-free and testable.
    """
    P = [1.0, 0.0, 0.0, 0.0,
         0.0, 0.0, -1.0, 0.0,
         0.0, 1.0, 0.0, 0.0,
         0.0, 0.0, 0.0, 1.0]
    Pinv = [1.0, 0.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, -1.0, 0.0, 0.0,
            0.0, 0.0, 0.0, 1.0]
    return mat4_mul(mat4_mul(P, list(m)), Pinv)
