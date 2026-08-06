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
  * SEMANTIC SLOTS are recorded, not just the transport names. `uvN`/`colorN` are
    appearance order; the engine binds by `SVertexElement.slot`. Each attribute
    entry carries its `slot`, and each object carries `lightmap_uv` — the
    RESOLVED name of the slot-4 texcoord set. Consumers must read that, never the
    literal `"uv1"` (see `lightmap_uv_for_manifest_object`).

Pure stdlib. Writing uses `array` (assumes a little-endian host — all targets are
x86-64 LE). The addon has its own numpy-based fast reader; the reader here is for
tests and non-Blender consumers.
"""

from __future__ import annotations

import json
from array import array
from dataclasses import dataclass
from pathlib import Path

from .lightmap import MANIFEST_KEY as LIGHTMAP_KEY
from .meshlist import scene_set_lod_levels
from .reflection_probe import MANIFEST_KEY as PROBE_KEY
from .vertex_format import lightmap_uv_attr_name

FORMAT = "lemesh"
# v2 adds `draws[].lod.level` / `.is_lod_child` (the mesh-list LOD chain). Purely
# additive: a v1 package reads as all-level-0, which `select_lod_draws` passes
# through unchanged, so v1 packages import exactly as before.
#
# ★ Also purely additive, and deliberately NOT a version bump (see
# `lightmap_uv_for_manifest_object`): every object now carries
#   * `attributes[<key>].slot`  — `SVertexElement.slot`, the semantic index the
#     appearance-order `uvN`/`colorN` names throw away, and
#   * `lightmap_uv`             — the RESOLVED attribute name of the slot-4
#     texcoord set (`str`, or `null` when the mesh has none).
# The keys are self-describing (present == new writer), a v1/v2 manifest without
# them still resolves correctly from its `raw_vertex_format`, and NOT bumping
# `VERSION` keeps `tests/test_package.py`'s `version == 2` pin — owned by another
# agent — green. Bumping to 3 is a fine follow-up once that one line moves.
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
                  drop_shadow_only: bool = False,
                  lightmap: dict | None = None,
                  reflection_probes: dict | None = None) -> Path:
    """Write a `.lemesh` package.

    `objects`   : list[le_mesh.meshlist.MeshObject]
    `materials` : list[dict] material specs (see materials.build_material_spec)
    `lightmap`  : optional LEVEL lightmap section (see
        `le_mesh.lightmap.manifest_lightmap_section`).  Emitted only when the
        extractor could actually resolve the scene's `CGLightMapResourceWin7`;
        omitted — never guessed — otherwise, and the addon then falls back to
        `lightmap_texture` / `lightmap_dir` / a directory scan exactly as before.
        ★ Additive: this does NOT bump `VERSION`, and a package without the key
        imports unchanged.
    `reflection_probes` : optional LEVEL reflection-probe section (see
        `le_mesh.reflection_probe.manifest_probe_section`) — the ambient
        SPECULAR sibling of `lightmap`.  Emitted only when the extractor could
        resolve the scene's `CGReflectionProbeResourceWin7`; omitted, never
        guessed, otherwise.  Also additive, also no `VERSION` bump.
    Returns the package directory path.
    """
    out_dir = Path(out_dir)
    blobs = out_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)

    # MESH-level (scene-set) LOD, computed once over the whole mesh-list because
    # the decision is "which masks occur in this resource", not a per-mesh fact.
    scene_lods = scene_set_lod_levels(list(objects))

    manifest_objects = []
    for obj in objects:
        if drop_shadow_only and obj.shadow_only:
            continue
        prefix = f"obj{obj.mesh_index:03d}"
        attr_manifest = {}
        for key, attr in obj.attributes.items():
            entry = {
                "usage": attr.usage,
                # `SVertexElement.slot @+0x04` — the SEMANTIC index of this set
                # (which UV / which colour set), as opposed to the `uvN`/`colorN`
                # suffix, which is only appearance order. The engine binds by
                # slot (lightmap == texcoord slot 4, `shader-confirmed`), so
                # throwing it away is what made the importer pick the wrong UV
                # set.
                "slot": attr.element.slot,
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
            # `CGRenderParams`' leading `SSceneSetMask` -- the MESH-level LOD
            # system a character uses (see `meshlist.RP_SCENEMASK`). 0 == this
            # draw is not gated by any scene set.
            "scene_mask": d.scene_mask,
            "scene_set_bit": d.scene_set_bit,
            "scene_set_min_count": d.scene_set_min_count,
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
            # ★ MESH-level LOD from the scene-set masks. `null` == this
            # mesh-list has no scene-set LOD and every mesh always draws;
            # otherwise 0 is the highest detail. `select_lod_objects` filters
            # on it. See `meshlist.scene_set_lod_levels`.
            "scene_lod_level": scene_lods.get(obj.mesh_index),
            "name_hash": f"{obj.name_hash:016x}",
            "flags": obj.flags,
            "flag_names": obj.flag_names,
            "shadow_only": obj.shadow_only,
            "force_single_sided": obj.force_single_sided,
            "aabb_min": list(obj.aabb_min),
            "aabb_max": list(obj.aabb_max),
            "lightmap_index": obj.lightmap_index,
            "lm_slice_index": obj.lm_slice_index,
            # ★ `CGMeshData.probeidx@0x50` — which reflection probe this mesh
            # reflects.  `null` == the extractor did not read it; `4294967295`
            # == the shipped "no probe" sentinel, which is NOT probe 0.  Read
            # off the mesh table by `le_mesh.reflection_probe` and stamped onto
            # the object by the extractor, so `le_mesh.meshlist` is unchanged.
            "probe_index": getattr(obj, "probe_index", None),
            "numlobes": getattr(obj, "numlobes", 0),
            "outline_mode": obj.outline_mode,
            "vertex_count": obj.vertex_count,
            "vertex_stride": obj.vertex_stride,
            # RESOLVED lightmap UV set: the attribute whose texcoord element sits
            # on semantic slot 4 (`shader-confirmed`). `null` == this
            # mesh carries no lightmap UV set; a consumer must then wire NO
            # lightmap rather than substitute another UV set. Consumers read this
            # instead of guessing the literal "uv1" — which is only right when
            # the texcoord slots happen to be (0, 4).
            "lightmap_uv": lightmap_uv_attr_name(obj.elements),
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
    if lightmap:
        manifest[LIGHTMAP_KEY] = lightmap
    if reflection_probes:
        manifest[PROBE_KEY] = reflection_probes
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out_dir


def read_manifest(pkg_dir: Path) -> dict:
    return json.loads((Path(pkg_dir) / "manifest.json").read_text(encoding="utf-8"))


def lightmap_uv_for_manifest_object(obj: dict) -> str | None:
    """The lightmap UV attribute name for ONE manifest object entry.

    THE consumer-facing entry point — the addon and the scene extractor call
    this instead of hardcoding a UV-set name. Resolution order:

      1. `obj["lightmap_uv"]`        — written by this module (above).
      2. `obj["raw_vertex_format"]`  — every `.lemesh` manifest ever written
         carries the full `SVertexElement` table including `slot`, so a v1/v2
         package on disk resolves EXACTLY as a freshly written one. No
         re-extraction is needed to get the fix.
      3. `None` — the manifest has neither (not a `.lemesh` object entry, or the
         audit trail was stripped). The caller then falls back to its own legacy
         appearance-order behaviour and should say so; see the delta note in
         a local working file.

    `None` also legitimately means "this mesh has NO slot-4 texcoord", i.e. no
    lightmap UV set exists. Substituting another set there is the bug this
    function exists to prevent.
    """
    if not isinstance(obj, dict):
        return None
    name = obj.get("lightmap_uv")
    if isinstance(name, str) and name:
        return name
    raw = obj.get("raw_vertex_format")
    if isinstance(raw, list) and raw:
        return lightmap_uv_attr_name(raw)
    return None
