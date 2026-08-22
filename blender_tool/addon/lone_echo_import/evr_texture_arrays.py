"""Per-instance texture-ARRAY slices, from the `texture_arrays.json` sidecar.

## What this fixes

Some art ships as a texture array and the material never names it, so every
object bound to one shows slice 0. `mpl_lobby_b2`'s poster boards are the
visible case: `a240a4bc051b2f23` is 37 slices of 2048x1024, one per poster, and
without this every board in the lobby displays the same image.

`scripts/evr_texture_array_binding.py` decodes which slice each ACTOR uses and
`evr_materials.write_array_slices` writes each slice as a standalone DDS; the
sidecar pairs them and adds the scatter instance the binding sits on. This is
the consumer.

## Why the material is linked to the OBJECT, not the mesh

Instances of one mesh share a single mesh datablock -- that sharing is the whole
point of the scatter path, and `mpl_lobby_b2` places 7786 instances over 2322
meshes. Giving one board its own poster by copying its mesh would defeat that
for every board.

Blender lets a material slot be linked to the OBJECT instead of its data
(`slot.link = 'OBJECT'`), which overrides the material for that one object while
every instance keeps sharing the same mesh. So the cost here is one extra
material datablock per distinct slice, and zero extra meshes.

## What it swaps

Only the image on the node that is already showing the array's BASE texture --
the one the material binds and the sidecar records as `base_texture`. Nothing
else about the material is touched, so a board keeps its own normal map,
specular and alpha exactly as built.

⚠ A binding whose slice file is missing, or whose material never shows the base
texture, is skipped and counted rather than guessed at.
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy   # type: ignore

SIDECAR_NAME = "texture_arrays.json"
SIDECAR_FORMAT = "evr_texture_arrays"


def sidecar_path(package) -> Path | None:
    root = Path(package)
    if root.is_file():
        root = root.parent
    candidate = root / SIDECAR_NAME
    return candidate if candidate.is_file() else None


def load(package) -> dict | None:
    path = sidecar_path(package)
    if path is None:
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if doc.get("format") == SIDECAR_FORMAT else None


def _slice_image(pkg_dir: Path, rel: str):
    """Load a slice DDS once and reuse it."""
    name = Path(rel).name
    existing = bpy.data.images.get(name)
    if existing is not None:
        return existing
    path = pkg_dir / rel
    if not path.is_file():
        return None
    try:
        image = bpy.data.images.load(str(path))
    except RuntimeError:
        return None
    image.name = name
    try:
        image.colorspace_settings.name = "sRGB"
    except (AttributeError, TypeError):
        pass
    return image


def _nodes_showing(material, texture_hash: str) -> list:
    """Image-texture nodes in `material` currently showing `texture_hash`."""
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return []
    stem = str(texture_hash).lower()
    return [n for n in tree.nodes
            if n.type == "TEX_IMAGE" and n.image is not None
            and stem in n.image.name.lower()]


def apply_slices(doc: dict, package, objects_by_instance: dict) -> dict:
    """Give each bound object its own slice. Returns a summary."""
    pkg_dir = Path(package)
    if pkg_dir.is_file():
        pkg_dir = pkg_dir.parent

    bindings = [b for b in (doc.get("bindings") or [])
                if b.get("instance") is not None and b.get("slice") is not None]
    if not bindings:
        return {"applied": 0, "reason": "no bindings carry an instance and a slice file"}

    variants: dict = {}
    applied = missing_file = no_node = no_object = 0

    for binding in bindings:
        obj = objects_by_instance.get(int(binding["instance"]))
        if obj is None or not obj.material_slots:
            no_object += 1
            continue

        base_material = obj.material_slots[0].material
        if base_material is None:
            no_object += 1
            continue

        # ⭐ The FILE is derived from `array` + `slice`, not read from the
        # sidecar's `file` field, so editing the slice NUMBER by hand is enough
        # to re-point a board. That matters because some boards' slices are not
        # in the level data at all: `mpl_lobby_b2`'s four `ea17a1c953a43e21`
        # panels are byte-identical in every component that mentions them --
        # same binding record, same script record, differing only in actor id --
        # yet they show four different posters in game. Nothing on disk says
        # which, so the sidecar is the place to record it.
        rel = binding.get("file")
        if binding.get("array"):
            derived = "textures/%s.s%02d.dds" % (binding["array"], int(binding["slice"]))
            if (pkg_dir / derived).is_file():
                rel = derived
        if not rel:
            missing_file += 1
            continue

        key = (base_material.name, rel)
        variant = variants.get(key)
        if variant is None:
            image = _slice_image(pkg_dir, rel)
            if image is None:
                missing_file += 1
                continue
            targets_on_base = _nodes_showing(base_material, binding.get("base_texture", ""))
            if not targets_on_base:
                no_node += 1
                continue
            variant = base_material.copy()
            variant.name = "%s_s%02d" % (base_material.name, int(binding["slice"]))
            for node in _nodes_showing(variant, binding.get("base_texture", "")):
                node.image = image
                node.label = "array slice %d" % int(binding["slice"])
            variant["le_texture_array"] = binding.get("array", "")
            variant["le_texture_array_slice"] = int(binding["slice"])
            variants[key] = variant

        # OBJECT-linked, so the mesh datablock stays shared -- see the module
        # docstring. Setting `link` first matters: the slot's material is read
        # from whichever source `link` names.
        slot = obj.material_slots[0]
        slot.link = "OBJECT"
        slot.material = variant
        obj["le_texture_array_slice"] = int(binding["slice"])
        applied += 1

    return {"applied": applied, "variants": len(variants),
            "missing_file": missing_file, "no_base_node": no_node,
            "no_object": no_object}
