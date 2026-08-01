"""CGMeshListData -> clean object model (pure stdlib).

Operates on ALREADY-DECOMPRESSED primary + paired-GPU byte buffers plus the
four table descriptors (count + data_off) that the loader-order parser
(`le_meshlist_decode.parse_candidate`) produces. Keeping the
Oodle + archive-framing concerns in the extractor lets this module be unit
tested with synthetic bytes and imported inside Blender unchanged.

Struct offsets below match the on-disk record layout.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .vertex_format import (
    VertexElement,
    read_vertex_format,
    decode_vertex_buffer,
    DecodedAttribute,
)


# --- record strides ----------------------------------------------------------
MESH_STRIDE = 0x80
RENDERPARAM_STRIDE = 0x68
INDEXBUFFER_STRIDE = 0x10

# --- CGMeshData (0x80) field offsets ----------------------------------------
M_NAME = 0x00          # u64 CSymbol64
M_VBINDEX = 0x08       # u32
M_IBINDEX = 0x0C       # u32
M_RENDERPARAMIDX = 0x1C
M_NUMRENDERPARAMS = 0x20
M_AABB = 0x24          # 6 x f32 (min.xyz, max.xyz)
M_SPHERE = 0x3C        # 4 x f32 (center.xyz, radius)
M_FLAGS = 0x4C         # u32 EFlags
M_PROBEIDX = 0x50
M_LIGHTMAPINDEX = 0x6C
M_LMSLICEINDEX = 0x70
M_NUMLOBES = 0x74      # u32, lightmap SG lobe count (4 on 1221/1221 shipped)
M_OUTLINEMODE = 0x7C

# --- CGRenderParams (0x68) field offsets ------------------------------------
RP_MATERIALIDX = 0x28
RP_SHADERSETIDX = 0x2C
RP_PRIMTYPE = 0x40     # 4 == triangle list
RP_IDXSTART = 0x44
RP_IDXCOUNT = 0x48
RP_PERMUTATION = 0x4C
RP_SORTPRIORITY = 0x50   # i32
RP_LODPRIMSETIDX = 0x58
RP_LODCHILDRENSTART = 0x5C
RP_LODCHILDRENCOUNT = 0x60

PRIMTYPE_TRIANGLES = 4

# --- CGIndexBufferData (0x10) -----------------------------------------------
IB_OFFSET = 0x00
IB_NUMINDICES = 0x04
IB_INDEXSIZE = 0x08
IB_PAD = 0x0C

# --- CGMeshData::EFlags -----------------------------------------------------
MESH_FLAGS = [
    (0x000001, "eCastsShadow"), (0x000002, "eShadowOnly"),
    (0x000004, "eLightUVTangent"), (0x000008, "eSampleIrradiance"),
    (0x000010, "eMotionBlur"), (0x000020, "eStatic"),
    (0x000040, "eEnablePropEdit"), (0x000080, "eForceSingleSided"),
    (0x000100, "eTessellateShadows"), (0x000200, "eUseAlternateFX"),
    (0x000400, "eExportAllVtxData"), (0x000800, "eRigidPhysSkin"),
    (0x001000, "ePinnedPhysSkin"), (0x002000, "eDiffuseVertexColor"),
    (0x004000, "eOccluder"), (0x008000, "eReceiver"),
    (0x010000, "eHasOccluderProp"), (0x020000, "eHasReceiverProp"),
    (0x040000, "eEnableRaycast"),
]
FLAG_SHADOW_ONLY = 0x2
FLAG_FORCE_SINGLE_SIDED = 0x80


def flag_names(flags: int) -> list[str]:
    return [name for bit, name in MESH_FLAGS if flags & bit]


@dataclass
class Table:
    """A parsed sub-table: element count and absolute offset into `primary`."""
    count: int
    data_off: int


@dataclass
class Draw:
    renderparam_index: int
    idx_start: int
    idx_count: int
    primtype: int
    shaderset_index: int
    material_index: int
    permutation: int
    sort_priority: int
    lod_primset_idx: int
    lod_children_start: int
    lod_children_count: int
    material_key: str = ""   # resolved by the extractor (shaderset/material hash)
    lod_level: int = 0       # 0 = highest detail; filled in by `assign_lod_levels`

    @property
    def is_triangles(self) -> bool:
        return self.primtype == PRIMTYPE_TRIANGLES

    @property
    def is_lod_parent(self) -> bool:
        """This draw is an LOD-0 ROOT: it owns `lod_children_count` coarser draws.

        ⚠ `lod_children_count != 0` is the ONLY reliable root predicate.
        `lod_children_start` is a RUNNING CURSOR into `CGMeshListData.lodchildindices`
        that stays non-zero on child draws too (corpus: 142 draws carry a non-zero
        start but only 51 are roots), and `lod_primset_idx` marks CHILDREN, not
        parents — an earlier revision OR-ed all three and so called every child a
        parent.
        """
        return self.lod_children_count != 0

    @property
    def is_lod_child(self) -> bool:
        """This draw is a coarser LOD of another draw in the same mesh."""
        return self.lod_primset_idx != 0xFFFFFFFF


@dataclass
class MeshObject:
    mesh_index: int
    name_hash: int
    flags: int
    vb_index: int
    ib_index: int
    aabb_min: tuple
    aabb_max: tuple
    lightmap_index: int
    lm_slice_index: int
    outline_mode: int
    vertex_count: int
    vertex_stride: int
    elements: list[VertexElement]
    attributes: dict[str, DecodedAttribute]
    index_count: int
    indices: list[int]
    index_size: int
    draws: list[Draw] = field(default_factory=list)
    # `CGMeshData.numlobes @0x74` -- the lightmap's spherical-gaussian lobe count.
    # Reads 4 on 1221/1221 shipped meshes, while the colour lightmap array holds
    # 5 slices per page, so 5 == numlobes + 1. Which of "4 lobes + 1 extra" or
    # "a 5-lobe bake whose numlobes means something else" is correct is still
    # unresolved -- see `le_mesh/lightmap.py`. Defaulted so the dangling-reference
    # path and any older caller keep working.
    numlobes: int = 0

    @property
    def shadow_only(self) -> bool:
        return bool(self.flags & FLAG_SHADOW_ONLY)

    @property
    def force_single_sided(self) -> bool:
        return bool(self.flags & FLAG_FORCE_SINGLE_SIDED)

    @property
    def flag_names(self) -> list[str]:
        return flag_names(self.flags)


def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]


def _read_draws(primary: bytes, rp_table: Table, rp_idx: int, rp_count: int) -> list[Draw]:
    draws: list[Draw] = []
    for i in range(rp_count):
        gi = rp_idx + i
        if gi >= rp_table.count:
            break
        base = rp_table.data_off + gi * RENDERPARAM_STRIDE
        draws.append(Draw(
            renderparam_index=gi,
            material_index=_u32(primary, base + RP_MATERIALIDX),
            shaderset_index=_u32(primary, base + RP_SHADERSETIDX),
            primtype=_u32(primary, base + RP_PRIMTYPE),
            idx_start=_u32(primary, base + RP_IDXSTART),
            idx_count=_u32(primary, base + RP_IDXCOUNT),
            permutation=_u32(primary, base + RP_PERMUTATION),
            sort_priority=struct.unpack_from("<i", primary, base + RP_SORTPRIORITY)[0],
            lod_primset_idx=_u32(primary, base + RP_LODPRIMSETIDX),
            lod_children_start=_u32(primary, base + RP_LODCHILDRENSTART),
            lod_children_count=_u32(primary, base + RP_LODCHILDRENCOUNT),
        ))
    return draws


def _draws_with_lod(primary, rp_table, rp_idx, rp_count, lod_children):
    draws = _read_draws(primary, rp_table, rp_idx, rp_count)
    assign_lod_levels(draws, lod_children, rp_idx)
    return draws


def assign_lod_levels(draws: list, lodchildindices: list, rp_base: int) -> None:
    """Stamp `Draw.lod_level` for ONE mesh's draws, in place.

    The mesh-list LOD chain is an INDEX-RANGE LOD *within a single mesh*: the
    coarser levels are extra `CGRenderParams` covering later slices of the SAME
    index buffer, not separate meshes (that is the static-scatter system — see
    `le_mesh.static_lod`). A root draw (`lod_children_count != 0`) is level 0 and
    `CGMeshListData.lodchildindices[start : start+count]` lists its children as
    MESH-LOCAL renderparam indices, in level order.

    Stream-confirmed on `4a405738bee7a74b` / `001e3b0be3b357af`: mesh 0 has one
    root (`rp0`, indices [0, 17262), 5,754 tris) whose two children `[1, 2]` are
    `rp1` [17262, 28824) 3,854 tris and `rp2` [28824, 34518) 1,898 tris — three
    disjoint, monotonically shrinking slices of one 34,518-index buffer.

    Draws that are neither roots nor children keep level 0 (a plain material
    split), so a mesh with no LOD chain is untouched.
    """
    for d in draws:
        d.lod_level = 0
    by_local = {d.renderparam_index - rp_base: d for d in draws}
    for d in draws:
        if not d.is_lod_parent:
            continue
        start = d.lod_children_start
        for level, k in enumerate(range(start, start + d.lod_children_count), start=1):
            if k >= len(lodchildindices):
                continue
            child = by_local.get(lodchildindices[k])
            if child is not None:
                child.lod_level = level


def build_objects(primary: bytes, gpu: bytes, gpu_base: int, *,
                  meshes: Table, renderparams: Table,
                  vertexbuffers: Table, indexbuffers: Table,
                  lodchildindices: Table | None = None,
                  ) -> list[MeshObject]:
    """Decode every CGMeshData in a mesh-list into a MeshObject with full attrs.

    `gpu_base` is the absolute offset of this resource's paired GPU slice inside
    the decompressed `gpu` buffer. `lodchildindices` (the mesh-list's own
    `CTable<u32>`) enables per-draw LOD levels; omit it and every draw reads as
    level 0. It is empty in all but 11 of the corpus's 1,240 mesh-lists.
    """
    lod_children: list = []
    if lodchildindices is not None and lodchildindices.count:
        lod_children = list(struct.unpack_from(
            f"<{lodchildindices.count}I", primary, lodchildindices.data_off))
    objects: list[MeshObject] = []
    for mi in range(meshes.count):
        m = meshes.data_off + mi * MESH_STRIDE
        name_hash = struct.unpack_from("<Q", primary, m + M_NAME)[0]
        vb_index = _u32(primary, m + M_VBINDEX)
        ib_index = _u32(primary, m + M_IBINDEX)
        rp_idx = _u32(primary, m + M_RENDERPARAMIDX)
        rp_count = _u32(primary, m + M_NUMRENDERPARAMS)
        flags = _u32(primary, m + M_FLAGS)
        aabb = struct.unpack_from("<6f", primary, m + M_AABB)
        lightmap_index = _u32(primary, m + M_LIGHTMAPINDEX)
        lm_slice_index = _u32(primary, m + M_LMSLICEINDEX)
        numlobes = _u32(primary, m + M_NUMLOBES)
        outline_mode = _u32(primary, m + M_OUTLINEMODE)

        if vb_index >= vertexbuffers.count or ib_index >= indexbuffers.count:
            # dangling reference — record an empty object so nothing is silently lost
            objects.append(MeshObject(
                mi, name_hash, flags, vb_index, ib_index,
                aabb[0:3], aabb[3:6], lightmap_index, lm_slice_index, outline_mode,
                0, 0, [], {}, 0, [], 0,
                _draws_with_lod(primary, renderparams, rp_idx, rp_count, lod_children)))
            continue

        vb_off = vertexbuffers.data_off + vb_index * 0x130
        elements, stride, rel_gpu, vcount = read_vertex_format(primary, vb_off)
        attributes = decode_vertex_buffer(gpu, gpu_base, rel_gpu, stride, vcount, elements)

        # index buffer record (primary) -> slice from GPU
        ib_base = indexbuffers.data_off + ib_index * INDEXBUFFER_STRIDE
        ib_rel = _u32(primary, ib_base + IB_OFFSET)
        ib_num = _u32(primary, ib_base + IB_NUMINDICES)
        ib_size = _u32(primary, ib_base + IB_INDEXSIZE)
        indices: list[int] = []
        if ib_size in (2, 4) and ib_num:
            fmt = "H" if ib_size == 2 else "I"
            indices = list(struct.unpack_from(f"<{ib_num}{fmt}", gpu, gpu_base + ib_rel))

        objects.append(MeshObject(
            mesh_index=mi,
            name_hash=name_hash,
            flags=flags,
            vb_index=vb_index,
            ib_index=ib_index,
            aabb_min=aabb[0:3],
            aabb_max=aabb[3:6],
            lightmap_index=lightmap_index,
            lm_slice_index=lm_slice_index,
            numlobes=numlobes,
            outline_mode=outline_mode,
            vertex_count=vcount,
            vertex_stride=stride,
            elements=elements,
            attributes=attributes,
            index_count=ib_num,
            indices=indices,
            index_size=ib_size,
            draws=_draws_with_lod(primary, renderparams, rp_idx, rp_count, lod_children),
        ))
    return objects
