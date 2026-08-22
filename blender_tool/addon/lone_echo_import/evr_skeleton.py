"""Import a package's `skeleton.json`: armature + skin weights.

Written by `scripts/evr_apply_skeleton.py`, always beside `manifest.json`, so
the importer picks it up with nothing extra to select -- same contract as
`lightmaps.json`.

A model either has a `CSkeletonResourceWin10` at its own hash or has none, so
this is simply absent for props and level geometry.

## What this builds

A fully parented armature: bones sit at their composed model-space bind
positions, tails point at their children, and a bone whose head coincides with
its parent's tail is CONNECTED, so posing a shoulder carries the arm.

## Honest limits

* **Bone names are partial.** Bones carry their authored name where its
  CSymbol64 preimage is recovered (`EXP_R1_Hand1`, `EXP_C1_Spine2`) and fall
  back to `bone_042` where it is not -- roughly half the slots, with the
  anatomical joints resolving and most helper/attachment joints not. The raw
  hash is always kept as `evr_name_hash`.
* Helper/IK bones with no skinned vertices are KEPT -- they are load-bearing
  parents for the bones below them.
* **Attachment sockets sit at the origin, and that is correct.** A few bones
  (`EXP_R1_HandPistol1`, `EXP_L1_HandGrenade1`) compose to exactly the
  identity matrix -- bit-exact, not rounding -- so they bind at the model
  origin rather than in the hand they hang off. Their stored local transform
  is the exact inverse of the parent's world transform, which is how an
  authored socket parks when no weapon is attached. They carry no skin
  weights, so nothing deforms; check `evr_weighted` to filter them out.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import bpy                                              # type: ignore
import mathutils                                        # type: ignore

SIDECAR_NAME = "skeleton.json"
SIDECAR_FORMAT = "evr_skeleton"

#: One vertex of skin data: four bone indices then four byte weights.
WEIGHT_STRUCT = "<4H4B"
WEIGHT_STRIDE = struct.calcsize(WEIGHT_STRUCT)

#: A LEAF bone has no child to aim at, so its length is derived from the
#: segment that FEEDS it -- a fingertip continues a 4 cm finger bone and must
#: not be drawn longer than the finger itself. A fixed length cannot work: one
#: value that suits a spine tip dwarfs every fingertip on the same rig.
LEAF_RATIO = 0.5
#: Floor for a leaf, so a leaf off a near-zero segment stays visible.
MIN_LEAF_LENGTH = 0.005
#: Fallback when a bone has no parent to measure against.
BONE_LENGTH = 0.05
#: Distance from the model origin under which a bone counts as sitting ON it.
#: The sockets compose to a bit-exact identity, so this only has to absorb
#: float noise from the matrix composition, not real slack.
ORIGIN_EPSILON = 1e-6
#: Blender removes bones shorter than this, which would break the parent chain.
MIN_BONE_LENGTH = 1e-4
#: Head-to-parent-tail distance under which a bone is CONNECTED. Connecting
#: snaps the head onto the parent tail, so this must stay tight enough that the
#: bind pose is never moved.
CONNECT_EPSILON = 1e-5


def sidecar_path(package):
    p = Path(package)
    if p.is_file():
        p = p.parent
    candidate = p / SIDECAR_NAME
    return candidate if candidate.is_file() else None


def load(package):
    """Parse the sidecar, or None when absent / not ours."""
    path = sidecar_path(package)
    if path is None:
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if doc.get("format") == SIDECAR_FORMAT else None


def _to_blender(vec, y_up_to_z_up):
    x, y, z = vec
    return mathutils.Vector((x, -z, y) if y_up_to_z_up else (x, y, z))


def build_armature(doc, context, y_up_to_z_up=True, collection=None,
                   name=None, hide_origin_sockets=True):
    """Create the armature. Returns `(object, {bone_index: name})`.

    Bones are parented from the decoded hierarchy, and each bone's TAIL points
    at its child so the rig reads like a normal skeleton rather than a field of
    disconnected stubs. A bone with one child is CONNECTED to it, which is what
    makes the chain drag properly when posed.

    With `hide_origin_sockets`, attachment sockets parked on the model origin
    (`zeroJoint`, `synchJoint`, `EXP_R1_HandPistol1`) are created but hidden,
    so they stop crowding the viewport around the origin. They are HIDDEN, not
    dropped: they stay in the armature, keep their index and hash, and unhide
    from the Armature tab. A bone only qualifies if it is unweighted AND
    childless AND on the origin, so no deforming bone and no load-bearing
    parent can ever be caught by it.
    """
    records = doc.get("bones") or []
    if not records:
        return None, {}

    label = name or f"{doc.get('model', 'evr')}_skeleton"
    arm = bpy.data.armatures.new(label)
    obj = bpy.data.objects.new(label, arm)
    (collection or context.scene.collection).objects.link(obj)

    previous = context.view_layer.objects.active
    context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")

    by_index = {int(r["index"]): r for r in records}
    children = {}
    for r in records:
        parent = r.get("parent")
        if parent is not None:
            children.setdefault(int(parent), []).append(int(r["index"]))

    def head_of(rec):
        return _to_blender(rec.get("position") or (0, 0, 0), y_up_to_z_up)

    depth_cache = {}

    def subtree_depth(index, guard=0):
        """Longest chain below a bone -- how the limb-continuing child is picked."""
        if index in depth_cache:
            return depth_cache[index]
        if guard > len(records):
            return 0
        kids = children.get(index, ())
        depth_cache[index] = (1 + max((subtree_depth(k, guard + 1) for k in kids),
                                      default=0)) if kids else 0
        return depth_cache[index]

    # Median parent->child distance: the scale this particular rig is built
    # at, used to cap leaf bones. Taken as a median so the handful of outsized
    # segments (root to pelvis) do not drag the cap up.
    spans = sorted((head_of(r) - head_of(by_index[int(r["parent"])])).length
                   for r in records
                   if r.get("parent") is not None
                   and int(r["parent"]) in by_index)
    leaf_cap = spans[len(spans) // 2] if spans else BONE_LENGTH
    if leaf_cap < MIN_LEAF_LENGTH:
        leaf_cap = BONE_LENGTH

    names, edit, sockets = {}, {}, []
    for r in records:
        index = int(r["index"])
        # Prefer the authored name; fall back to the index where its CSymbol64
        # preimage was never recovered.
        bone_name = r.get("name") or f"bone_{index:03d}"
        eb = arm.edit_bones.new(bone_name)
        # Blender uniquifies a colliding name ("zeroJoint" -> "zeroJoint.001"),
        # so take back what it actually assigned: `names` drives the vertex
        # group names in bind_weights, and a stale name binds nothing.
        bone_name = eb.name
        head = head_of(r)
        kids = children.get(index, [])

        # Tail: aim at the child that CONTINUES the limb -- the one with the
        # deepest subtree under it. Aiming at the average of all children
        # instead (the obvious choice) leaves the tail floating between them,
        # so no child is ever coincident and NOTHING connects: a hand with five
        # fingers rendered as six disjoint stubs. Picking the primary child
        # keeps every chain -- spine, arm, each finger -- visually joined, and
        # only genuine branch points stay separate, which is how a hand-built
        # rig looks.
        if kids:
            primary = max(kids, key=lambda k: (subtree_depth(k), -k))
            tail = head_of(by_index[primary])
        else:
            # Leaf: carry on in the direction the parent segment was heading,
            # at a length proportional to that segment. Capped at the model's
            # median segment so a bone whose parent is far away -- an
            # attachment socket sitting at the origin, metres from the hand it
            # hangs off -- cannot spawn a metre-long spike across the mesh.
            parent = r.get("parent")
            direction = (head - head_of(by_index[int(parent)])
                         if parent is not None and int(parent) in by_index
                         else mathutils.Vector((0.0, 0.0, 1.0)))
            span = direction.length
            if span < 1e-6:
                direction = mathutils.Vector((0.0, 0.0, 1.0))
                span = BONE_LENGTH
            length = min(max(span * LEAF_RATIO, MIN_LEAF_LENGTH), leaf_cap)
            tail = head + direction.normalized() * length

        if (tail - head).length < MIN_BONE_LENGTH:
            # Blender deletes a zero-length bone outright, which would punch a
            # hole in the parent chain, so nudge it along the parent direction.
            tail = head + mathutils.Vector((0.0, 0.0, MIN_BONE_LENGTH))
        eb.head, eb.tail = head, tail
        eb["evr_bone_index"] = index
        eb["evr_name_hash"] = r.get("name_hash", "")
        eb["evr_weighted"] = bool(r.get("weighted", True))
        names[index] = bone_name
        edit[index] = eb
        # Deliberately conservative: `weighted` defaults to True when the
        # sidecar predates the field, so an unknown bone is kept visible.
        if (hide_origin_sockets and not r.get("weighted", True)
                and not kids and head.length < ORIGIN_EPSILON):
            sockets.append(bone_name)

    # Parent second: every bone must exist before it can be referenced.
    for r in records:
        parent = r.get("parent")
        if parent is None:
            continue
        eb, pb = edit[int(r["index"])], edit.get(int(parent))
        if pb is None:
            continue
        eb.parent = pb
        # Connect only when the joint really is coincident, otherwise Blender
        # would MOVE the head to the parent's tail and shift the bind pose.
        eb.use_connect = (eb.head - pb.tail).length < CONNECT_EPSILON

    bpy.ops.object.mode_set(mode="OBJECT")
    # Hiding is applied on the Bone rather than the EditBone: the edit-mode
    # copy is discarded on leaving edit mode, so it has to be set on the bone
    # that survives.
    for bone_name in sockets:
        bone = arm.bones.get(bone_name)
        if bone is not None:
            bone.hide = True
    if previous is not None:
        context.view_layer.objects.active = previous

    obj["evr_bone_count"] = int(doc.get("bone_count") or len(records))
    obj["evr_bones_parented"] = bool(doc.get("hierarchy"))
    obj["evr_roots"] = list(doc.get("roots") or [])
    obj["evr_hidden_sockets"] = len(sockets)
    return obj, names


def bind_weights(doc, package, armature, names, objects_by_mesh):
    """Add vertex groups from the weight blob and parent meshes to the rig."""
    root = Path(package)
    if root.is_file():
        root = root.parent
    blob_name = doc.get("weights_blob") or "skeleton_weights.bin"
    blob_path = root / blob_name
    if not blob_path.is_file():
        return {"bound": 0, "reason": f"{blob_name} missing"}
    blob = blob_path.read_bytes()

    bound = skipped = 0
    for entry in doc.get("meshes") or []:
        index = int(entry.get("mesh", -1))
        offset = int(entry.get("offset", 0))
        nverts = int(entry.get("nverts", 0))
        if not entry.get("weighted"):
            continue
        for obj in objects_by_mesh.get(index, ()):
            mesh = getattr(obj, "data", None)
            if mesh is None or len(mesh.vertices) != nverts:
                skipped += 1
                continue
            groups = {}
            for vi in range(nverts):
                base = offset + vi * WEIGHT_STRIDE
                if base + WEIGHT_STRIDE > len(blob):
                    break
                values = struct.unpack_from(WEIGHT_STRUCT, blob, base)
                for bone_index, weight in zip(values[:4], values[4:]):
                    if not weight:
                        continue
                    bone_name = names.get(bone_index)
                    if bone_name is None:
                        continue
                    group = groups.get(bone_name)
                    if group is None:
                        group = obj.vertex_groups.new(name=bone_name)
                        groups[bone_name] = group
                    group.add([vi], weight / 255.0, "REPLACE")
            if groups:
                modifier = obj.modifiers.new("Armature", "ARMATURE")
                modifier.object = armature
                obj.parent = armature
                bound += 1
    out = {"bound": bound}
    if skipped:
        # A vertex-count mismatch means the mesh was split or LOD-filtered
        # differently than when the sidecar was written -- binding it would
        # attach weights to the wrong vertices.
        out["skipped_vertex_mismatch"] = skipped
    return out


def summarize(doc):
    return {"bones": len(doc.get("bones") or []),
            "bone_count": int(doc.get("bone_count") or 0),
            "meshes": len(doc.get("meshes") or [])}
