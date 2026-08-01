"""The `.lemesh` package — the extractor <-> Blender-addon contract.

A `.lemesh` package is a directory:

    <name>.lemesh/
      manifest.json          fully self-describing: objects, attributes, draws, materials
      blobs/*.bin            raw little-endian arrays (float32 / uint32 / int32)
      textures/*.dds         source textures referenced by materials

Design choices:
  * The extractor DECODES every vertex attribute to canonical arrays (float for
    positions/normals/uv/color, int for skin indices) so the addon stays trivial
    and the tricky format decode is covered by pytest, not Blender.
  * Blobs are raw flat little-endian so the addon reads them with
    numpy.frombuffer(...).reshape(...) and Blender foreach_set — no per-vertex
    Python loops in Blender.
  * manifest.json still carries the RAW SVertexElement table per object, so the
    package is auditable / lossless-of-information even though geometry is stored
    decoded.

Pure stdlib. Writing uses `array` (assumes a little-endian host — all targets are
x86-64 LE). The addon has its own numpy-based fast reader; the reader here is for
tests and non-Blender consumers.
"""

from __future__ import annotations

import json
from array import array
from dataclasses import dataclass
from pathlib import Path

FORMAT = "lemesh"
# v2 adds `draws[].lod.level` / `.is_lod_child` (the mesh-list LOD chain). Purely
# additive: a v1 package reads as all-level-0, which `select_lod_draws` passes
# through unchanged, so v1 packages import exactly as before.
VERSION = 2

_DTYPE_TO_ARRAYCODE = {"float32": "f", "uint32": "I", "int32": "i"}


def _write_blob(path: Path, values, dtype: str) -> None:
    code = _DTYPE_TO_ARRAYCODE[dtype]
    a = array(code, values)
    # array uses native byte order; all supported hosts are little-endian.
    path.write_bytes(a.tobytes())


def load_blob(pkg_dir: Path, rel_path: str, dtype: str):
    """Read a blob back as an `array`. Used by tests / non-Blender consumers."""
    code = _DTYPE_TO_ARRAYCODE[dtype]
    a = array(code)
    data = (pkg_dir / rel_path).read_bytes()
    a.frombytes(data)
    return a


def write_package(out_dir: Path, *, source: dict, objects, materials: list,
                  coordinate_system: str = "rad_engine",
                  drop_shadow_only: bool = False) -> Path:
    """Write a `.lemesh` package.

    `objects`   : list[le_mesh.meshlist.MeshObject]
    `materials` : list[dict] material specs (see materials.build_material_spec)
    Returns the package directory path.
    """
    out_dir = Path(out_dir)
    blobs = out_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)

    manifest_objects = []
    for obj in objects:
        if drop_shadow_only and obj.shadow_only:
            continue
        prefix = f"obj{obj.mesh_index:03d}"
        attr_manifest = {}
        for key, attr in obj.attributes.items():
            entry = {
                "usage": attr.usage,
                "comps": attr.comps,
                "encoding": attr.element.type_name,
                "packed_unresolved": attr.packed_unresolved,
            }
            if not attr.packed_unresolved and attr.data:
                dtype = "int32" if attr.is_integer else "float32"
                rel = f"blobs/{prefix}_{key}.bin"
                _write_blob(out_dir / rel, attr.data, dtype)
                entry["blob"] = rel
                entry["dtype"] = dtype
            attr_manifest[key] = entry

        index_entry = None
        if obj.index_count and obj.indices:
            rel = f"blobs/{prefix}_indices.bin"
            _write_blob(out_dir / rel, obj.indices, "uint32")
            index_entry = {"blob": rel, "dtype": "uint32", "count": obj.index_count}

        draws = [{
            "renderparam_index": d.renderparam_index,
            "idx_start": d.idx_start,
            "idx_count": d.idx_count,
            "primtype": d.primtype,
            "is_triangles": d.is_triangles,
            "shaderset_index": d.shaderset_index,
            "material_index": d.material_index,
            "material_key": d.material_key,
            "sort_priority": d.sort_priority,
            "permutation": d.permutation,
            "lod": {
                # `level` is what a consumer selects on: 0 = highest detail. The
                # coarser levels are extra draws over LATER slices of the SAME
                # index buffer, so importing every draw stacks the levels.
                "level": d.lod_level,
                "is_lod_parent": d.is_lod_parent,
                "is_lod_child": d.is_lod_child,
                "primset_idx": d.lod_primset_idx,
                "children_start": d.lod_children_start,
                "children_count": d.lod_children_count,
            },
        } for d in obj.draws]

        manifest_objects.append({
            "name": f"{prefix}_{obj.name_hash:016x}",
            "mesh_index": obj.mesh_index,
            "name_hash": f"{obj.name_hash:016x}",
            "flags": obj.flags,
            "flag_names": obj.flag_names,
            "shadow_only": obj.shadow_only,
            "force_single_sided": obj.force_single_sided,
            "aabb_min": list(obj.aabb_min),
            "aabb_max": list(obj.aabb_max),
            "lightmap_index": obj.lightmap_index,
            "lm_slice_index": obj.lm_slice_index,
            "numlobes": getattr(obj, "numlobes", 0),
            "outline_mode": obj.outline_mode,
            "vertex_count": obj.vertex_count,
            "vertex_stride": obj.vertex_stride,
            "raw_vertex_format": [e.as_dict() for e in obj.elements],
            "attributes": attr_manifest,
            "index": index_entry,
            "draws": draws,
        })

    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "coordinate_system": coordinate_system,
        "source": source,
        "objects": manifest_objects,
        "materials": materials,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out_dir


def read_manifest(pkg_dir: Path) -> dict:
    return json.loads((Path(pkg_dir) / "manifest.json").read_text(encoding="utf-8"))
