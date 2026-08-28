"""Quest level -> a `.lescatter` package the existing Blender importer reads.

## Where a Quest level's geometry actually is

Not where the PC path looks. On `mpl_arena_a_lowspec` the `CModelCR` route binds
**6** actors and **none** of their models has a mesh list; the level's world
geometry is `CGInstancedModelResource`, reached through the static-instance
tables:

    CGStaticInstanceResource.assetdata[i][0]  -> CGInstancedModelResource hash
    CStaticInstanceModelCR  group A (+0x08)   -> entity nodeid,   one per instance
    CStaticInstanceModelCR  group B (+0x20)   -> model hash,      one per instance
    CActorDataResource                        -> that entity's world transform

Measured on the arena: 34 instances, **34/34** entities resolve to a real actor
and **34/34** models resolve to a `CGInstancedModelResource` that exists on disk.

⭐ `CGInstancedModelResource` is byte-identical between PC and Quest (the port
project scores it `cross-build`, 1.0000x, 0 differ), so this reuses the PC
`evr_mesh_importer.decode` for the payload instead of writing a second decoder.
`CGMeshListResource` is NOT reused that way -- see `evr_quest.mesh`, which reads
the Quest vertex format properly rather than pattern-scanning for it.

⚠ Instance count is low because these are `_lowspec` cooks: the arena ships as
34 pre-merged clusters, not a few thousand props.
"""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evr_quest import types as T          # noqa: E402

#: `CStaticInstanceModelCR`: two parallel groups, the same layout the PC build
#: uses (`evr_component_cr`). Group B is located from the END of the file.
CSIMCR_COUNT = 0x28
CSIMCR_GROUP_A = 0x2A8
CSIMCR_A_STRIDE = 24
CSIMCR_A_ENTITY = 0x08
CSIMCR_B_STRIDE = 88
CSIMCR_B_MODEL = 0x20


@dataclass
class QuestMesh:
    index: int
    model: str
    positions: list
    indices: list
    uv0: list = field(default_factory=list)
    aabb_min: tuple = (0.0, 0.0, 0.0)
    aabb_max: tuple = (0.0, 0.0, 0.0)
    #: Which submesh of `model` this came from. NOT the position in the
    #: emitted list -- empty submeshes are skipped, so the two diverge, and the
    #: material table is indexed by the SOURCE index.
    submesh: int = 0
    #: Baked-lightmap UVs, when the vertex stream carries them.
    uv1: list = field(default_factory=list)
    #: Per-vertex BAKED LIGHTING, linear RGB. See `baked_vertex_light`.
    color0: list = field(default_factory=list)


@dataclass
class QuestInstance:
    mesh_index: int
    translation: tuple
    rotation: tuple      # xyzw
    scale: tuple
    entity: int
    model: str


def _decoder():
    """The PC `evr_mesh_importer.decode`, loaded without its `bpy` package init."""
    path = _ROOT / "app" / "extract" / "evr_mesh_importer" / "decode.py"
    spec = importlib.util.spec_from_file_location("_evr_quest_decode", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def static_instances(root, level: str) -> list:
    """`[(entity, model hash), ...]` from `CStaticInstanceModelCR`."""
    path = T.resource(root, T.STATIC_MODEL_CR, level)
    if path is None:
        return []
    blob = path.read_bytes()
    if len(blob) < CSIMCR_GROUP_A:
        return []
    count = struct.unpack_from("<I", blob, CSIMCR_COUNT)[0]
    group_b = len(blob) - count * CSIMCR_B_STRIDE
    if count <= 0 or group_b < CSIMCR_GROUP_A + count * CSIMCR_A_STRIDE:
        return []
    out = []
    for i in range(count):
        entity = struct.unpack_from(
            "<Q", blob, CSIMCR_GROUP_A + i * CSIMCR_A_STRIDE + CSIMCR_A_ENTITY)[0]
        model = struct.unpack_from(
            "<Q", blob, group_b + i * CSIMCR_B_STRIDE + CSIMCR_B_MODEL)[0]
        out.append((entity, f"{model:016x}"))
    return out


def component_bindings(root, level: str, component, known_models: set,
                       actor_ids: set) -> list:
    """`[(entity, model hash), ...]` from `CModelCRAndroid`.

    ⛔ The PC reader finds nothing here. `evr_component_cr._model_cr_bindings`
    validates a record by reading the component type at `model-0x20` and the
    selector at `model-0x18`; on Android the payload record is laid out
    differently and that test rejects 460 of 461 real model references on
    `mpl_arena_a_lowspec` -- which is why the PC route reports 6 bindings for a
    level that has 150.

    The record lattice is found instead of assumed: every offset holding a real
    actor nodeid is collected, the payload stride is the most common gap between
    them, and the model column is the offset inside that stride at which a known
    model hash appears in EVERY record. On the arena that resolves to

        payload stride 296, actor @ +0x00, model @ +0x18       150/150 records

    and the same search re-derives it per level, so a level with another stride
    still reads.
    """
    import collections

    path = T.resource(root, component, level)
    if path is None:
        return []
    blob = path.read_bytes()
    hits = [o for o in range(0, len(blob) - 8, 4)
            if struct.unpack_from("<Q", blob, o)[0] in actor_ids]
    if len(hits) < 4:
        return []
    gaps = collections.Counter(hits[i + 1] - hits[i] for i in range(len(hits) - 1))
    # the component INDEX is a tight lattice (24 B) and the PAYLOAD a wide one;
    # the payload is the widest stride that still repeats across most records
    strides = [g for g, n in gaps.most_common() if n >= 4]
    if not strides:
        return []
    stride = max(strides)
    records = [o for i, o in enumerate(hits)
               if (i + 1 < len(hits) and hits[i + 1] - o == stride)
               or (i and o - hits[i - 1] == stride)]
    if not records:
        return []

    column = collections.Counter()
    for at in records:
        for k in range(0, stride - 8, 8):
            if struct.unpack_from("<Q", blob, at + k)[0] in known_models:
                column[k] += 1
    if not column:
        return []
    offset, seen = column.most_common(1)[0]
    if seen < len(records) // 2:
        return []

    out = []
    for at in records:
        model = struct.unpack_from("<Q", blob, at + offset)[0]
        if model in known_models:
            out.append((struct.unpack_from("<Q", blob, at)[0], f"{model:016x}"))
    return out


def actor_transforms(root, level: str) -> dict:
    """`{nodeid: (translation, rotation xyzw, scale)}`."""
    import evr_actor_data

    path = T.resource(root, T.ACTOR_DATA, level)
    if path is None:
        return {}
    actors = evr_actor_data.parse_actor_data(path.read_bytes()).get("actors") or []
    out = {}
    for actor in actors:
        transform = actor.get("transform") or {}
        position = transform.get("position") or {}
        rotation = transform.get("rotation") or {}
        scale = transform.get("scale") or {}
        out[actor["nodeid"]] = (
            (position.get("x", 0.0), position.get("y", 0.0), position.get("z", 0.0)),
            (rotation.get("x", 0.0), rotation.get("y", 0.0),
             rotation.get("z", 0.0), rotation.get("w", 1.0)),
            (scale.get("x", 1.0), scale.get("y", 1.0), scale.get("z", 1.0)))
    return out


def build(root, level: str, verbose: bool = True) -> tuple:
    """`(meshes, instances)` for one level."""
    root = Path(root)
    transforms = actor_transforms(root, level)
    primary = T.resources(root, T.INSTANCED_MODEL)
    gpu = T.resources(root, T.INSTANCED_MODEL_GPU)
    mesh_primary = T.resources(root, T.MESH_LIST)
    mesh_gpu = T.resources(root, T.MESH_LIST_GPU)

    # Two placement routes, and a level uses BOTH: the static-instance tables
    # place pre-merged clusters, `CModelCR` places per-actor models.
    known = {int(h, 16) for h in primary} | {int(h, 16) for h in mesh_primary}
    pairs = list(static_instances(root, level))
    seen = {(e, m) for e, m in pairs}
    for component in (T.MODEL_CR, T.INSTANCE_MODEL_CR):
        for entity, model in component_bindings(
                root, level, component, known, set(transforms)):
            if (entity, model) not in seen:
                pairs.append((entity, model))
                seen.add((entity, model))

    meshes: list = []
    instances: list = []
    cache: dict = {}
    missing = placed = 0
    # Per-instance lightmap UVs, when the level bakes them.
    inst_uv = instance_lightmap_uvs(root, level)
    lit_instances = 0

    # ⭐ THE LEVEL'S OWN GEOMETRY. A mesh list named after the LEVEL holds the
    # merged world hull, and NOTHING places it: it is not in the static-instance
    # tables and not in `CModelCR`, because it sits at identity. On
    # `mpl_arena_a_lowspec` that one resource is 29 meshes and 125,920
    # triangles -- the hull, both goal ends, the mid sections and the skybox --
    # against 88,171 for every placed instance combined. Missing it is why the
    # first renders showed goals floating in empty space.
    if level in mesh_primary and level in mesh_gpu:
        base = _decode_meshlist(mesh_primary[level], mesh_gpu[level],
                                level, meshes)
        for index in base:
            instances.append(QuestInstance(
                mesh_index=index, translation=(0.0, 0.0, 0.0),
                rotation=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0),
                entity=0, model=level))
        if verbose and base:
            print(f"  level hull: {len(base)} mesh(es) at identity")

    for entity, model in pairs:
        if entity not in transforms:
            missing += 1
            continue
        if model not in cache:
            # A MESH LIST is decoded by `evr_quest.mesh`, which reads the Quest
            # vertex format; only CGInstancedModelResource -- byte-identical
            # across platforms -- goes through the PC pattern-scanner.
            if model in mesh_primary and model in mesh_gpu:
                cache[model] = _decode_meshlist(
                    mesh_primary[model], mesh_gpu[model], model, meshes)
            elif model in primary and model in gpu:
                cache[model] = _decode_meshlist(
                    primary[model], gpu[model], model, meshes, instanced=True)
            else:
                cache[model] = []
        indexes = cache[model]
        if not indexes:
            missing += 1
            continue

        # An instance with its OWN lightmap UVs cannot share a mesh with its
        # siblings -- they sample different regions of the atlas. Decode a
        # private copy for it rather than letting one instance's chart leak
        # onto every other placement of the same model.
        own = {sub: uv for (ent, sub), uv in inst_uv.items() if ent == entity}
        if own:
            private = _decode_meshlist(
                mesh_primary[model], mesh_gpu[model], model, meshes) \
                if model in mesh_primary and model in mesh_gpu else \
                _decode_meshlist(primary[model], gpu[model], model, meshes,
                                 instanced=True) \
                if model in primary and model in gpu else []
            if private:
                attached = 0
                for index in private:
                    mesh = meshes[index]
                    uvs = own.get(mesh.submesh)
                    if uvs and len(uvs) == len(mesh.positions) // 3:
                        mesh.uv1 = [c for uv in uvs for c in uv[:2]]
                        attached += 1
                if attached:
                    indexes = private
                    lit_instances += 1
                else:
                    # Nothing matched -- drop the copy and reuse the shared
                    # meshes rather than emitting duplicate geometry.
                    del meshes[private[0]:]
        translation, rotation, scale = transforms[entity]
        for index in indexes:
            instances.append(QuestInstance(
                mesh_index=index, translation=translation, rotation=rotation,
                scale=scale, entity=entity, model=model))
        placed += 1

    if verbose:
        print(f"  {level}: {placed}/{len(pairs)} instances placed over "
              f"{len(cache)} model(s), {len(meshes)} mesh(es), "
              f"{sum(len(m.indices) for m in meshes) // 3} triangles")
        if inst_uv:
            print(f"  per-instance lightmap UVs: {len(inst_uv)} chart(s) over "
                  f"{lit_instances} instance(s)")
        if missing:
            print(f"  warning: {missing} instance(s) had no transform "
                  f"or no decodable geometry")
    return meshes, instances


def instance_lightmap_uvs(root, level: str) -> dict:
    """`{(entity, submesh): [(u, v), ...]}` for a level's static instances.

    Static-instanced geometry does NOT carry its lightmap UV in the vertex
    stream: instances of one mesh sit in different regions of the atlas, so the
    UVs are per instance and live in the CGSI GPU sibling, addressed by
    `meshdata.uvoffset/uvcount`.

    The PC reader works on Quest unchanged, and the layout is pinned three
    ways: `4 * sum(uvcount)` equals the header word at +0x170 AND the GPU
    file's byte size (3744 on the arena). The row key is
    `MakeInstancedMeshBakeID(entity, "mesh-<i>")`, which resolves 16 of 16 rows
    on both arena levels with none left unaddressed.
    """
    try:
        import evr_lightmap as evr_lm
    except ImportError:
        return {}
    cgsi_path = T.resource(root, T.STATIC_RESOURCE, level)
    gpu_path = T.resource(root, T.STATIC_RESOURCE_GPU, level)
    if cgsi_path is None or gpu_path is None:
        return {}
    try:
        cgsi = cgsi_path.read_bytes()
        gpu = gpu_path.read_bytes()
    except OSError:
        return {}
    tables = evr_lm.read_cgsi(cgsi)
    if not tables or not tables.get("meshdata"):
        return {}
    keys = {key for key, _off, _count in tables["meshdata"]}

    out = {}
    for entity, _model in static_instances(root, level):
        for submesh in range(MAX_INSTANCE_SUBMESH):
            if evr_lm.instanced_mesh_bake_id(entity, submesh) not in keys:
                continue
            uvs = evr_lm.static_instance_uvs(cgsi, gpu, entity, submesh)
            if uvs:
                out[(entity, submesh)] = uvs
    return out


#: How many submeshes of one static instance to probe for a bake-id row.
MAX_INSTANCE_SUBMESH = 16


def _decode_meshlist(primary_path, gpu_path, model, meshes,
                     instanced: bool = False) -> list:
    """Geometry from `CGMeshListResourceAndroid` via the Quest vertex format."""
    from evr_quest import mesh as qmesh

    reader = qmesh.read_instanced if instanced else qmesh.read
    try:
        doc = reader(primary_path.read_bytes())
    except Exception:                                       # noqa: BLE001
        return []
    if doc.empty:
        return []
    blob = gpu_path.read_bytes()
    out = []
    for source, entry in enumerate(doc.meshes):
        points = qmesh.positions(blob, entry)
        faces = qmesh.indices(blob, entry)
        if not points or not faces:
            continue
        # ⛔ Drop whole TRIANGLES, never individual indices: filtering indices
        # re-groups every triangle after the first bad one, which turns a mesh
        # into a fan of long thin slivers radiating from one vertex. That is
        # what the first Quest render looked like.
        triangles = []
        for k in range(0, len(faces) - 2, 3):
            tri = faces[k:k + 3]
            if max(tri) < len(points) and len(set(tri)) == 3:
                triangles.extend(tri)
        faces = triangles
        if not faces:
            continue
        uvs = qmesh.texcoords(blob, entry).get(0) or []
        bake = baked_vertex_light(blob, entry)
        # ⛔ Vertex texcoord slot 4 is NOT the lightmap UV. It was emitted here
        # until ground truth disproved it on 16 of 16 submeshes -- see
        # `evr_quest.lightmap`. Only the CGSI per-instance charts are trusted,
        # and those are attached in `build`.
        index = len(meshes)
        meshes.append(QuestMesh(
            index=index, model=model,
            positions=[c for p in points for c in p[:3]],
            indices=faces,
            uv0=([c for uv in uvs for c in uv[:2]]
                 if len(uvs) == len(points) else []),
            color0=(bake if len(bake) == 3 * len(points) else []),
            aabb_min=tuple(entry.aabb[:3]), aabb_max=tuple(entry.aabb[3:]),
            submesh=source))
        out.append(index)
    return out


#: Vertex-colour usage, and the byte offset of the BAKE within stream 0.
#: Slot 0 (offset 0) is a flat unused lane -- black on every hull mesh
#: measured. The bake is the second one.
USAGE_COLOR = 1
BAKE_COLOR_OFFSET = 4


def _srgb_to_linear(value: float) -> float:
    return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4


def baked_vertex_light(gpu: bytes, entry) -> list:
    """Flat linear `[r, g, b, ...]` per vertex, or `[]`.

    ⭐ This is the level's baked lighting, and dropping it is what made the
    first Quest arena import render flat and blown out. Two independent checks
    say what it is:

    **It is the light rig.** The arena's two directional lights are a warm key
    and a cool fill. Across the hull's 162,145 vertices the colours' warmth
    tracks position along the arena's long axis at **r = -0.846** against a
    shuffled control of +0.001 -- warm `(71.5, 57.5, 50.0)` at one end, cool
    `(35.0, 42.8, 72.8)` at the other. That is the orange-vs-blue team split,
    baked.

    **It is sRGB-encoded.** Decoded to linear it lands on the same distribution
    as the level's HDR lightmap atlas -- two independent bakes of one level:

        percentile        p10     p25     p50     p75     p90
        atlas          0.0070  0.0274  0.0736  0.1484  0.3424
        sRGB-decoded   0.0090  0.0221  0.0548  0.1515  0.3116
        raw / 255      0.0915  0.1542  0.2444  0.3882  0.5359

    Raw values run 3-4x hot at the low end, so the decode is not cosmetic.

    ⛔ Not to be confused with texcoord set 4, which packs into the atlas like
    a chart but is NOT the lightmap UV -- see `evr_quest.lightmap`, and the
    correlation test that killed it (median r = +0.17, several meshes
    NEGATIVE, against ground truth that disagrees on 16 of 16 submeshes).
    """
    from evr_quest import mesh as qmesh                    # noqa: PLC0415

    element = next((e for e in entry.elements
                    if e.usage == USAGE_COLOR
                    and e.offset == BAKE_COLOR_OFFSET), None)
    if element is None or not entry.count:
        return []
    block, stride = qmesh.stream_block(gpu, entry, element.stream)
    if block is None:
        return []
    need = (entry.count - 1) * stride + element.offset + 4
    if need > len(block):
        return []
    out = []
    for i in range(entry.count):
        b, g, r, _a = struct.unpack_from(
            "<4B", block, i * stride + element.offset)
        out.extend(_srgb_to_linear(c / 255.0) for c in (r, g, b))
    return out


def _decode_model(decode, primary, gpu, model, meshes) -> list:
    if model not in primary or model not in gpu:
        return []
    try:
        submeshes, _label = decode.extract_mesh(
            str(gpu[model]), primary[model].read_bytes(), auto_find_primary=False)
    except Exception:                                       # noqa: BLE001
        return []
    out = []
    for source, sub in enumerate(submeshes or ()):
        verts = sub[0] if len(sub) > 0 else None
        faces = sub[1] if len(sub) > 1 else None
        uvs = sub[2] if len(sub) > 2 else None
        if not verts or not faces:
            continue
        flat_pos = [c for v in verts for c in v[:3]]
        flat_idx = [i for f in faces for i in f[:3]]
        flat_uv = [c for uv in (uvs or ()) for c in uv[:2]]
        if len(flat_uv) != 2 * len(verts):
            flat_uv = []
        lo = [min(v[i] for v in verts) for i in range(3)]
        hi = [max(v[i] for v in verts) for i in range(3)]
        index = len(meshes)
        meshes.append(QuestMesh(index=index, model=model, positions=flat_pos,
                                indices=flat_idx, uv0=flat_uv,
                                aabb_min=tuple(lo), aabb_max=tuple(hi),
                                submesh=source))
        out.append(index)
    return out


def write_package(out_dir, master: str, meshes, instances,
                  matidx: dict | None = None,
                  lightmap: dict | None = None,
                  uv_scales: dict | None = None) -> Path:
    """Write the `le_scatter` package the Blender scatter importer already reads."""
    out_dir = Path(out_dir)
    blobs = out_dir / "blobs"
    blobs.mkdir(parents=True, exist_ok=True)

    entries = []
    for mesh in meshes:
        pos_name = f"blobs/m{mesh.index}_pos.bin"
        idx_name = f"blobs/m{mesh.index}_idx.bin"
        (out_dir / pos_name).write_bytes(
            struct.pack(f"<{len(mesh.positions)}f", *mesh.positions))
        (out_dir / idx_name).write_bytes(
            struct.pack(f"<{len(mesh.indices)}I", *mesh.indices))
        slot = int((matidx or {}).get(mesh.index, 0))
        entry = {
            "index": mesh.index,
            "name_hash": mesh.model,
            "matidx": slot, "shdidx": 0,
            "draws": [{"matidx": slot, "shdidx": 0, "idx_start": 0,
                       "idx_count": len(mesh.indices)}],
            "aabb_min": list(mesh.aabb_min),
            "aabb_max": list(mesh.aabb_max),
            "instance_offset": 0, "instance_count": 0,
            "nverts": len(mesh.positions) // 3,
            "nindices": len(mesh.indices),
            "positions": pos_name, "indices": idx_name,
            "proxy": False,
            "lightmap_index": 0xFFFFFFFF, "lm_slice_index": 0xFFFFFFFF,
            "numlobes": 0,
        }
        if mesh.uv0:
            # ⭐ Bake the material's UV multiplier into the channel. Each entry
            # here is ONE submesh with ONE material, so this is exact -- and it
            # keeps the shared Blender loader untouched. Tiling multipliers push
            # UVs past 1.0, which is what image REPEAT wrapping is for.
            scale = (uv_scales or {}).get(mesh.index)
            uvs = mesh.uv0
            if scale:
                su, sv = scale
                uvs = [c * (su if i % 2 == 0 else sv)
                       for i, c in enumerate(uvs)]
                entry["uv_scale"] = [su, sv]
            uv_name = f"blobs/m{mesh.index}_uv0.bin"
            (out_dir / uv_name).write_bytes(
                struct.pack(f"<{len(uvs)}f", *uvs))
            entry["uv0"] = uv_name
        if mesh.color0:
            # Per-vertex BAKED LIGHTING, linear rgb. Quest bakes the level into
            # the vertex stream; without this the import is lit by the four
            # scene lights alone and reads flat and blown out.
            col_name = f"blobs/m{mesh.index}_col.bin"
            (out_dir / col_name).write_bytes(
                struct.pack(f"<{len(mesh.color0)}f", *mesh.color0))
            entry["color0"] = col_name
        if mesh.uv1:
            lm_name = f"blobs/m{mesh.index}_uv1.bin"
            (out_dir / lm_name).write_bytes(
                struct.pack(f"<{len(mesh.uv1)}f", *mesh.uv1))
            entry["uv1"] = lm_name
        entries.append(entry)

    packed = bytearray()
    for inst in instances:
        packed += struct.pack("<I3f4f3f", inst.mesh_index, *inst.translation,
                              *inst.rotation, *inst.scale)
    (out_dir / "blobs" / "instances.bin").write_bytes(bytes(packed))

    (out_dir / "manifest.json").write_text(json.dumps({
        "format": "le_scatter",
        "version": 5,
        "master": master,
        "axis": "native",
        "source": "evr_quest",
        "note": ("Echo VR QUEST (android_vulkan) level. Geometry is "
                 "CGInstancedModelResource placed by CStaticInstanceModelCR "
                 "entities at their CActorDataResource transforms."),
        "num_meshes": len(entries),
        "num_instances": len(instances),
        "meshes": entries,
        "instances_blob": "blobs/instances.bin",
        "lod": {"blob": "", "num_groups": 0, "max_level": 0,
                "levels_histogram": {}},
        # Quest only: meshes carry per-vertex baked lighting in `color0`. PC
        # packages never set this, so their material path is untouched.
        "baked_vertex_light": any(m.color0 for m in meshes),
        # Names the lightmap UV set for the importer, using the same manifest
        # key the `.lemesh` path already writes. PC scatter packages omit it
        # and keep the legacy transport name.
        "lightmap_uv": ("EchoLightmap" if any(m.uv1 for m in meshes)
                        else None),
        "lightmap_stats": {
            "meshes_lightmapped": sum(1 for m in meshes if m.uv1),
            "meshes_unlit": sum(1 for m in meshes if not m.uv1)},
        "instance_lightmap": {"present": False,
                              "reason": "static-instance lightmap UVs live in "
                                        "the CGSI GPU sibling and are not "
                                        "emitted yet"},
        "lightmap": lightmap or {},
    }, indent=1), encoding="utf-8")
    return out_dir


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("level", help="level hash or name")
    ap.add_argument("--dir", default="H:/quest-extracted")
    ap.add_argument("--out", required=True)
    ap.add_argument("--texture-size", type=int, default=0, metavar="N",
                    help="cap textures at N pixels per edge by SELECTING that "
                         "mip from the engine's own chain (no resampling); "
                         "0 exports each texture at full resolution")
    ap.add_argument("--no-materials", action="store_true",
                    help="geometry only -- skip materials.json and textures")
    args = ap.parse_args(argv)

    root = Path(args.dir)
    level = args.level
    if not all(c in "0123456789abcdef" for c in level.lower()):
        table = T.names()
        found = [h for h in T.levels(root)
                 if table.get(h, "").lower() == level.lower()]
        if not found:
            import json as _json
            names = _json.loads(
                (_ROOT / "data" / "level_names_echovr.json").read_text())
            found = [h for h in T.levels(root)
                     if names.get(h, names.get(h.lstrip("0"), "")) == level]
        if not found:
            print(f"no level named {level!r} in {root}")
            return 1
        level = found[0]

    meshes, instances = build(root, level)
    if not meshes:
        print("no geometry decoded")
        return 1

    matidx, uv_scales = {}, {}
    if not args.no_materials:
        matidx, uv_scales = _materials(root, Path(args.out), meshes,
                                       cap=args.texture_size)

    lm = _lightmap(root, Path(args.out), level, meshes)
    matidx = _privatise_lit_materials(Path(args.out), meshes, matidx)
    _lights(root, Path(args.out), level, lm, meshes)
    out = write_package(Path(args.out), level, meshes, instances, matidx, lm,
                        uv_scales=uv_scales)
    _movers(root, Path(args.out), level, instances)
    print(f"-> {out}")
    return 0


def _movers(root, out_dir, level, instances) -> dict:
    """Write `movers.json` for the level's moving geometry.

    Runs after `write_package` because it keys on the package instance index,
    which is the position in the same `instances` list that was just packed.
    A level with no movers writes no sidecar -- the add-on treats an absent
    file as "static", so an empty one would be noise.
    """
    from evr_quest import movers as qmovers

    try:
        found = qmovers.movers_for(root, level)
    except Exception as exc:                                # noqa: BLE001
        print(f"  movers: skipped ({exc})")
        return {}
    if not found:
        return {}
    rows = qmovers.rows_for_instances(found, instances)
    if not rows:
        # Movers were decoded but none of their actors is a placed instance.
        # Say so: silence here would read as "this level is static".
        print(f"  movers: {len(found)} decoded, none placed as an instance")
        return {}
    qmovers.write_sidecar(out_dir, rows)
    distinct = {tuple(r["travel"]) for r in rows.values()}
    print(f"  movers: {len(distinct)} distinct motion(s) over {len(rows)} "
          f"instance(s) -> movers.json (timing is a placeholder)")
    return rows


def level_lights(root, level: str) -> list:
    """The level's authored `SGLightParams`, or `[]`.

    ⭐ Quest reuses the PC record verbatim -- `CGSceneResourceAndroid` opens
    with the same `[u32 count][count x 360]` section-1 array, and
    `evr_lights.parse_scene_lights` reads it unchanged. The arena's two
    directional lights come out as

        warm  (1.000, 0.583, 0.431)
        cool  (0.584, 0.820, 1.000)

    which are the exact values `evr_lights` records for the PC build of the
    same level -- an independent cross-check that the stride and field map
    carry over rather than merely producing plausible-looking floats.

    Quest ships FOUR lights where PC ships 138. The rest is baked; these four
    are the warm/cool key pair and the two team goal spots (orange intensity
    18, blue 15).
    """
    path = T.resource(root, T.SCENE_RESOURCE, level)
    if path is None:
        return []
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import evr_lights                                      # noqa: PLC0415
    try:
        return evr_lights.parse_scene_lights(path.read_bytes())
    except (OSError, struct.error):
        return []


def _privatise_lit_materials(out_dir, meshes, matidx: dict) -> dict:
    """Give every lightmapped mesh a material slot of its OWN.

    ⛔ The add-on's lighting pass wires the atlas into the material datablock,
    not the object. On this arena all four lit materials are ALSO used by six
    meshes that carry no chart -- and a Blender image node with no matching UV
    layer falls back to the ACTIVE one, so those six would sample the lightmap
    atlas with their TEXTURE coordinates and come out lit by whatever texel
    that happens to hit.

    Splitting them here rather than in the add-on keeps the PC path untouched:
    the sidecar simply gains a few more entries, which is a shape it already
    supports.
    """
    path = Path(out_dir) / "materials.json"
    if not path.is_file():
        return matidx
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return matidx
    entries = doc.get("materials") or []
    by_slot = {int(e.get("matidx", -1)): e for e in entries}
    lit = {m.index for m in meshes if m.uv1}
    shared = set()
    for slot in {matidx.get(m.index, 0) for m in meshes if m.index in lit}:
        users = {m.index for m in meshes if matidx.get(m.index, 0) == slot}
        if users - lit:
            shared.add(slot)
    if not shared:
        return matidx

    out = dict(matidx)
    nextslot = max(by_slot, default=-1) + 1
    added = 0
    for mesh in meshes:
        slot = matidx.get(mesh.index, 0)
        if mesh.index not in lit or slot not in shared:
            continue
        source = by_slot.get(slot)
        if source is None:
            continue
        clone = json.loads(json.dumps(source))
        clone["matidx"] = nextslot
        if isinstance(clone.get("spec"), dict):
            clone["spec"]["key"] = f"{clone['spec'].get('key', '')}__lm"
        entries.append(clone)
        out[mesh.index] = nextslot
        nextslot += 1
        added += 1
    doc["materials"] = entries
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"  lightmap: split {added} material slot(s) so the atlas cannot "
          f"reach meshes with no chart")
    return out


def _lightmap_bindings(lightmap: dict, meshes) -> tuple:
    """`(images, gains, bindings)` binding every lit mesh to the atlas page.

    The Quest build already hands each lit instance a PRIVATE mesh carrying its
    own CGSI chart in `uv1`, so the binding is the simple per-mesh form rather
    than the per-instance blob the PC path needs.

    The atlas ships as Radiance HDR with its peak intact (3.69 on the arena),
    so the gain is 1.0 -- there is no 8-bit percentile divisor to undo.
    """
    name = (lightmap or {}).get("file") or ""
    if not name:
        return {}, {}, {}
    name = name.rsplit("/", 1)[-1]
    key = str((lightmap or {}).get("atlas") or "0")
    bindings = {str(m.index): {"image": name} for m in meshes if m.uv1}
    if not bindings:
        return {}, {}, {}
    return {key: name}, {key: 1.0}, bindings


def _lights(root, out_dir, level, lightmap: dict, meshes=()) -> int:
    """Write `lightmaps.json` -- the sidecar the add-on's lighting pass reads.

    The Quest build was writing its atlas into `manifest.json` only, so
    `evr_lighting.load` found no sidecar and the import placed no lights at
    all. Emitted in the PC schema so the same add-on code path serves both.
    """
    lights = level_lights(root, level)
    if not lights:
        print("  lights: none authored in this level's scene resource")
        return 0
    images, gains, bindings = _lightmap_bindings(lightmap, meshes)
    records = []
    for light in lights:
        records.append({
            "level": level,
            "type": light.type_name,
            "position": [round(v, 5) for v in light.position],
            "direction": [round(v, 6) for v in light.direction],
            "color": [round(v, 6) for v in light.color],
            "intensity": round(light.intensity, 5),
            "range": [round(v, 4) for v in light.range],
            "shades_dynamic": light.shades_dynamic,
            "owner": light.owner,
            "cone": (round(light.cone, 6)
                     if isinstance(light.cone, float) else None),
            "cone_inner": (round(light.cone_inner, 6)
                           if isinstance(light.cone_inner, float) else None),
        })
    payload = {
        "format": "evr_lighting",
        "version": 1,
        "note": ("Quest. lights: SGLightParams from CGSceneResourceAndroid "
                 "section 1, the same 360-byte record the PC build uses. Only "
                 "SUN (type 2) shades dynamic objects in-engine; POINT/SPOT "
                 "are the static-bake rig."),
        "dir": "textures",
        "images": images,
        "gains": gains,
        "meshes": bindings,
        "lights": records,
        "instances": {},
        "instance_uv_blob": None,
        "quest_lightmap": lightmap or {},
    }
    (Path(out_dir) / "lightmaps.json").write_text(
        json.dumps(payload, indent=1), encoding="utf-8")
    kinds = {}
    for light in lights:
        kinds[light.type_name] = kinds.get(light.type_name, 0) + 1
    summary = ", ".join(f"{v} {k.lower()}" for k, v in sorted(kinds.items()))
    dyn = sum(1 for light in lights if light.shades_dynamic)
    print(f"  lights: {len(lights)} ({summary}); {dyn} shade dynamic objects "
          f"-> lightmaps.json")
    if bindings:
        print(f"  baked atlas: {len(bindings)} mesh(es) bound to "
              f"{list(images.values())[0]}")
    else:
        print("  baked atlas: no mesh carries a lightmap UV to bind it to")
    return len(lights)


def _lightmap(root, out_dir, level, meshes) -> dict:
    """Decode the level's baked-lightmap atlas. Returns a manifest fragment.

    The hull's colour comes from this, not from a material -- see
    `evr_quest.lightmap`. Reported either way so an unlit import is a stated
    result rather than a silent one.
    """
    from evr_quest import lightmap as qlightmap

    lit = sum(1 for m in meshes if m.uv1)
    info = qlightmap.export_atlas(root, out_dir, level)
    if not info:
        print(f"  lightmap: no atlas for this level ({lit} mesh(es) carry UVs)")
        return {}
    info["meshes_with_uv"] = lit
    if info.get("file"):
        kind = "HDR" if info.get("hdr") else "8-bit"
        peak = f" peak {info['peak']}" if info.get("peak") is not None else ""
        print(f"  lightmap: atlas {info['atlas']} {info.get('format')} "
              f"{info.get('width')}x{info.get('height')} -> {info['file']} "
              f"({kind}{peak}); {lit} mesh(es) carry UVs")
    else:
        print(f"  lightmap: atlas {info['atlas']} not written "
              f"({info.get('reason')})")
    return info


def _materials(root, out_dir, meshes, cap: int = 0) -> dict:
    """Resolve each mesh's material, write `materials.json` and the textures.

    Returns `{mesh_index: matidx}`. A failure here leaves the package as
    geometry-only rather than aborting the export -- and says so, because an
    untextured import that reported success is what sent the last one out the
    door looking wrong.
    """
    from evr_quest import material as qmaterial

    known = qmaterial.material_hashes(root)
    names = _hash_names()
    per_mesh, per_model = {}, {}
    for mesh in meshes:
        if mesh.model not in per_model:
            count = 1 + max((m.submesh for m in meshes if m.model == mesh.model),
                            default=0)
            per_model[mesh.model] = qmaterial.model_materials(
                root, mesh.model, count, known)
        table = per_model[mesh.model]
        if mesh.submesh < len(table):
            per_mesh[mesh.index] = table[mesh.submesh]

    bound = sum(1 for v in per_mesh.values() if v)
    print(f"  materials: {bound}/{len(meshes)} mesh(es) bound over "
          f"{len(per_model)} model(s)")
    if not bound:
        print("  warning: no material resolved -- package is geometry only")
        return {}
    document, matidx = qmaterial.build_sidecar(
        root, out_dir, per_mesh, names, cap=cap,
        progress=lambda note: print(f"  {note}"))
    diag = document["diagnostics"]
    shipped = diag["textures_wanted"] - diag["textures_not_shipped"]
    note = ""
    if diag["textures_not_shipped"]:
        note = (f" ({diag['textures_not_shipped']} more referenced but not "
                f"shipped in this build)")
    print(f"  wrote {diag['materials_built']} material(s), "
          f"{diag['textures_written']}/{shipped} texture(s){note}")

    # The material's UV multiplier, resolved per mesh. See
    # `evr_quest.material.uv_scale` -- without it the arena's panel art samples
    # the wrong half of its atlas.
    scales, scaled = {}, 0
    for index, material in per_mesh.items():
        if not material:
            continue
        pair = qmaterial.uv_scale(root, material)
        if pair != qmaterial.IDENTITY_UV_SCALE:
            scales[index] = pair
            scaled += 1
    if scaled:
        print(f"  uv scale: {scaled} mesh(es) carry a non-identity multiplier")
    return matidx, scales


def _hash_names() -> dict:
    """`hash_lookup.json` keyed the way `role_for_slot` expects."""
    import json as _json
    path = _ROOT / "data" / "hash_lookup.json"
    try:
        raw = _json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k.lower().replace("0x", ""): v for k, v in raw.items()}


if __name__ == "__main__":
    raise SystemExit(main())
