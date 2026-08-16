"""Import a package's `skeleton.json`: armature + skin weights.

Written by `scripts/evr_apply_skeleton.py`, always beside `manifest.json`, so
the importer picks it up with nothing extra to select -- same contract as
`lightmaps.json`.

A model either has a `CSkeletonResourceWin10` at its own hash or has none, so
this is simply absent for props and level geometry.

## Honest limits

* **Bones are PARENTLESS.** The bind translations in the resource are
  parent-relative and the hierarchy is not decoded, so they cannot be composed
  into model space. Each bone is instead placed at the weighted centroid of the
  vertices it influences -- correct rest position, correct skinning, but posing
  a bone will not carry its children.
* **Bone names are indices** (`bone_000`). Their CSymbol64 name hashes are in
  the resource but not yet cracked.
* Bones with no weighted vertices (helpers/IK -- 47 of 125 on the reference
  model) are omitted rather than stacked on the origin, which is what produced
  the spike-pile at the feet before.
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

#: Bone display length. Nothing in the data implies a length -- without a
#: hierarchy there is no child to point at -- so this is a viewing choice.
BONE_LENGTH = 0.05


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
                   name=None):
    """Create the armature object. Returns `(object, {bone_index: name})`."""
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
    names = {}
    for rec in records:
        index = int(rec.get("index", 0))
        bone_name = f"bone_{index:03d}"
        eb = arm.edit_bones.new(bone_name)
        head = _to_blender(rec.get("position") or (0, 0, 0), y_up_to_z_up)
        quat = rec.get("rotation") or [0.0, 0.0, 0.0, 1.0]
        rotation = mathutils.Quaternion((quat[3], quat[0], quat[1], quat[2]))
        aim = rotation @ mathutils.Vector((0.0, 1.0, 0.0))
        aim = _to_blender((aim.x, aim.y, aim.z), y_up_to_z_up)
        if aim.length < 1e-6:
            aim = mathutils.Vector((0.0, 0.0, 1.0))
        eb.head = head
        eb.tail = head + aim.normalized() * BONE_LENGTH
        names[index] = bone_name
    bpy.ops.object.mode_set(mode="OBJECT")
    if previous is not None:
        context.view_layer.objects.active = previous

    # Keep the source values so a rebuilt rig loses nothing once the
    # hierarchy is decoded.
    obj["evr_bone_count"] = int(doc.get("bone_count") or len(records))
    obj["evr_bones_parented"] = False
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
