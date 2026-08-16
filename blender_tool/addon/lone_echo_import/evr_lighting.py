"""Import an Echo VR package's `lightmaps.json`: baked atlases + placed lights.

Written by `scripts/evr_apply_lighting.py`, always beside `manifest.json`, so
the importer finds it without the user selecting anything.

Two independent halves, because they come from different places in the game
data and either can be present alone:

* **lights** -- `SGLightParams` from `CGSceneResource` section 1: type, colour,
  intensity, range and direction, all authored. `mpl_arena_a` carries 138 --
  2 directional, 26 spot, 110 point -- and its warm/cool team split is right
  there in the two directional lights, `(1.000, 0.583, 0.431)` against
  `(0.584, 0.820, 1.000)`. Only type >= 2 (SUN) enters the engine's dynamic
  shading list; POINT and SPOT are the static-bake rig, so importing them
  alongside the lightmap double-counts their contribution -- hence
  `dynamic_only`.

* **lightmaps** -- per-page diffuse irradiance, already collapsed out of the
  SG5/SH4 basis with the shader's own weights. The same warm/cool split shows
  up baked here, which is what those POINT and SPOT lights were baked into.

Wiring an atlas needs a lightmap UV layer on the mesh. When the package has no
such layer the atlases are still loaded and reported, and the reason is stated
plainly rather than silently doing nothing.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import bpy                                              # type: ignore
import mathutils                                        # type: ignore

SIDECAR_NAME = "lightmaps.json"
SIDECAR_FORMAT = "evr_lighting"

#: The importer's own name for the lightmap UV layer, matching what the engine
#: authoring path calls it (`custom_level_importer` creates "EchoLightmap").
LIGHTMAP_UV = "EchoLightmap"

#: Watts for an intensity-1.0 light. The records store a relative multiplier
#: with no unit, so this is a viewing default, not a decoded quantity.
DEFAULT_WATTS = 25.0
DEFAULT_RADIUS = 0.05


def sidecar_path(package) -> Path | None:
    """The `lightmaps.json` beside a package, or None."""
    p = Path(package)
    if p.is_file():
        p = p.parent
    candidate = p / SIDECAR_NAME
    return candidate if candidate.is_file() else None


def load(package) -> dict | None:
    """Parse the sidecar, or None when absent / not ours."""
    path = sidecar_path(package)
    if path is None:
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if doc.get("format") == SIDECAR_FORMAT else None


def _to_blender(vec, y_up_to_z_up: bool):
    x, y, z = vec
    return mathutils.Vector((x, -z, y) if y_up_to_z_up else (x, y, z))


#: `SGLightParams.type` names as the sidecar writes them.
_LIGHT_TYPES = {"POINT": "POINT", "SPOT": "SPOT", "SUN": "SUN"}


def import_lights(doc: dict, context, y_up_to_z_up: bool = True,
                  collection=None, scale: float = 1.0,
                  dynamic_only: bool = False) -> dict:
    """Build Blender lights from the level's `SGLightParams` records.

    Type, colour, intensity and range all come from the record. A SUN is aimed
    by its `direction`; point and spot lights use their position. `range` maps
    to Blender's custom distance, so falloff keeps roughly the authored reach.

    `dynamic_only` applies the engine's own gate -- only type >= 2 (SUN) lights
    enter the runtime list that shades dynamic objects; POINT and SPOT belong to
    the static bake and are already in the lightmap, so importing them too
    double-counts that light.
    """
    records = doc.get("lights") or []
    if not records:
        return {"created": 0, "reason": "no lights in sidecar"}

    target = collection or context.scene.collection
    created = 0
    skipped = 0
    for rec in records:
        if dynamic_only and not rec.get("shades_dynamic"):
            skipped += 1
            continue
        kind = _LIGHT_TYPES.get(str(rec.get("type", "POINT")).upper(), "POINT")
        data = bpy.data.lights.new(name=f"evr_{kind.lower()}", type=kind)
        try:
            data.color = tuple(float(c) for c in (rec.get("color") or [1, 1, 1])[:3])
        except (TypeError, ValueError):
            pass

        intensity = float(rec.get("intensity") or 1.0)
        if kind == "SUN":
            # A Blender sun is irradiance in W/m2, so the authored intensity
            # transfers directly rather than through the watt scale below.
            data.energy = max(intensity, 0.0)
        else:
            data.energy = DEFAULT_WATTS * max(intensity, 0.0)
            data.shadow_soft_size = DEFAULT_RADIUS
            reach = (rec.get("range") or [0.0])[0]
            if reach:
                data.use_custom_distance = True
                data.cutoff_distance = float(reach)

        obj = bpy.data.objects.new(data.name, data)
        obj.location = _to_blender(rec.get("position") or (0, 0, 0),
                                   y_up_to_z_up) * scale
        # A directional light has no meaningful position -- only its aim
        # matters, and the record gives that as a vector, not a quaternion.
        direction = rec.get("direction")
        if direction and len(direction) == 3 and any(direction):
            aim = _to_blender(direction, y_up_to_z_up)
            if aim.length > 1e-6:
                obj.rotation_euler = aim.to_track_quat("-Z", "Y").to_euler()

        obj["evr_light_type"] = kind
        obj["evr_light_intensity"] = intensity
        obj["evr_light_range"] = (rec.get("range") or [None])[0]
        obj["evr_shades_dynamic"] = bool(rec.get("shades_dynamic"))
        if rec.get("level"):
            obj["evr_level"] = rec["level"]

        target.objects.link(obj)
        created += 1
    out = {"created": created}
    if skipped:
        out["skipped_static"] = skipped
    return out


def _load_image(directory: Path, name: str):
    path = directory / name
    if not path.is_file():
        return None
    existing = bpy.data.images.get(name)
    if existing is not None:
        return existing
    image = bpy.data.images.load(str(path))
    image.name = name
    # The atlas holds light, not colour: it was sRGB-encoded only so the PNG is
    # viewable. Reading it as sRGB would apply that curve twice.
    try:
        image.colorspace_settings.name = "sRGB"
    except (AttributeError, TypeError):
        pass
    return image


def wire_lightmaps(doc: dict, package, objects_by_mesh: dict) -> dict:
    """Multiply each bound mesh's base colour by its lightmap page.

    `objects_by_mesh` maps a manifest mesh index to the Blender objects built
    from it. Meshes with no lightmap UV layer are counted and reported rather
    than wired against the wrong coordinates.
    """
    bindings = doc.get("meshes") or {}
    if not bindings:
        return {"wired": 0, "reason": "no mesh bindings in sidecar"}

    root = Path(package)
    if root.is_file():
        root = root.parent
    directory = root / (doc.get("dir") or "lightmaps")

    wired = missing_uv = missing_image = 0
    for key, binding in bindings.items():
        try:
            mesh_index = int(key)
        except (TypeError, ValueError):
            continue
        for obj in objects_by_mesh.get(mesh_index, ()):
            mesh = getattr(obj, "data", None)
            if mesh is None or not getattr(mesh, "uv_layers", None):
                continue
            if LIGHTMAP_UV not in mesh.uv_layers:
                missing_uv += 1
                continue
            image = _load_image(directory, binding.get("image", ""))
            if image is None:
                missing_image += 1
                continue
            for slot in obj.material_slots:
                if slot.material and _multiply_lightmap(slot.material, image):
                    wired += 1
    out = {"wired": wired}
    if missing_uv:
        out["missing_uv"] = missing_uv
        out["reason"] = (f"{missing_uv} bound mesh(es) have no '{LIGHTMAP_UV}' "
                         f"UV layer, so the atlas cannot be sampled")
    if missing_image:
        out["missing_image"] = missing_image
    return out


def _multiply_lightmap(material, image) -> bool:
    """Insert `base_colour * lightmap` ahead of the Principled BSDF."""
    if not material.use_nodes:
        return False
    tree = material.node_tree
    principled = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled is None:
        return False
    base = principled.inputs.get("Base Color")
    if base is None or any(n.label == "evr_lightmap" for n in tree.nodes):
        return False

    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.label = "evr_lightmap"
    tex.location = (principled.location.x - 900, principled.location.y - 420)

    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = LIGHTMAP_UV
    uv.location = (tex.location.x - 220, tex.location.y)
    tree.links.new(tex.inputs["Vector"], uv.outputs["UV"])

    mix = tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.label = "evr_lightmap_multiply"
    mix.location = (principled.location.x - 300, principled.location.y - 200)
    mix.inputs["Factor"].default_value = 1.0

    if base.is_linked:
        source = base.links[0].from_socket
        tree.links.new(mix.inputs[6], source)
    else:
        mix.inputs[6].default_value = tuple(base.default_value)
    tree.links.new(mix.inputs[7], tex.outputs["Color"])
    tree.links.new(base, mix.outputs[2])
    return True


def wire_instance_lightmaps(doc: dict, package, objects_by_instance: dict) -> dict:
    """Wire the PER-INSTANCE lightmap: static-instanced geometry.

    Instances of one mesh sit in different atlas regions, so each carries its
    own UV set. Honouring that means giving every lit instance its own mesh
    datablock -- the same cost the Lone Echo per-instance path pays, and the
    only way the atlas lands on the right pixels.
    """
    entries = doc.get("instances") or {}
    if not entries:
        return {"wired": 0, "reason": "no per-instance lightmap in sidecar"}

    root = Path(package)
    if root.is_file():
        root = root.parent
    blob_rel = doc.get("instance_uv_blob")
    blob_path = root / blob_rel if blob_rel else None
    if not blob_path or not blob_path.is_file():
        return {"wired": 0, "reason": f"missing UV blob ({blob_rel})"}
    raw = blob_path.read_bytes()

    import array
    uv = array.array("f")
    uv.frombytes(raw)

    directory = root / (doc.get("dir") or "lightmaps")
    wired = mismatched = 0
    for key, entry in entries.items():
        try:
            instance_index = int(key)
        except (TypeError, ValueError):
            continue
        for obj in objects_by_instance.get(instance_index, ()):
            mesh = getattr(obj, "data", None)
            if mesh is None:
                continue
            count = int(entry.get("uv_count") or 0)
            if count != len(mesh.vertices):
                # The UV run is per VERTEX; a length mismatch means this
                # instance's mesh is not the one the bake was made against.
                mismatched += 1
                continue
            image = _load_image(directory, entry.get("image", ""))
            if image is None:
                continue

            mesh = mesh.copy()             # per-instance UVs break sharing
            obj.data = mesh
            layer = mesh.uv_layers.get(LIGHTMAP_UV) or mesh.uv_layers.new(
                name=LIGHTMAP_UV)
            start = int(entry.get("uv_offset") or 0) * 2
            for loop in mesh.loops:
                base = start + loop.vertex_index * 2
                if base + 1 < len(uv):
                    layer.data[loop.index].uv = (uv[base], uv[base + 1])

            obj["evr_lightmap_page"] = entry.get("page")
            for slot in obj.material_slots:
                if slot.material:
                    material = slot.material.copy()
                    slot.material = material
                    if _multiply_lightmap(material, image):
                        wired += 1
    out = {"wired": wired}
    if mismatched:
        out["mismatched"] = mismatched
    return out


def summarize(doc: dict) -> dict:
    """Counts for the operator's report, without touching the scene."""
    return {
        "lights": len(doc.get("lights") or []),
        "atlases": len(doc.get("images") or {}),
        "bound_meshes": len(doc.get("meshes") or {}),
        "bound_instances": len(doc.get("instances") or {}),
    }
