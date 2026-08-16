"""Static-instance placement, ported from `rad-archive-viewer/app.py`.

## Why this exists

`evr_scene_extract` was using `evr_mesh_importer_core.level_reader` for scene
placement.  `app.py` -- which renders this level correctly -- does NOT use that
module; it carries its own parsers, and they differ in ways that matter:

## Where static-instance placement actually comes from

`CGStaticInstanceResourceWin10` is the obvious-looking candidate and it is the
WRONG one.  It is the in-place payload of `NRadEngine::SGStaticInstancesData`:
a 0x178 header of six table descriptors (`assetdata`, `instancedata`,
`meshdata`, `shadersetoverrides`, `brokennodes`, `brokenassets`) holding the
asset, shaderset and lightmap bindings the renderer needs.  **Not one of those
six tables contains a transform.**

This module used to read that resource as an array of 24-byte records with a
9-bit-quantized position expanded inside the level's BVH bounds.  That decode
was measured against the level's own geometry and came out *indistinguishable
from uniformly random points* -- it was expanding lighting metadata into
coordinates, which is what filled the viewport with floating debris.  Two
compounding errors made it look plausible: the value at `+0x08` is a table's
byte SIZE and was being used as its file offset, and the record count ran to
EOF, straddling two unrelated 16-byte tables.

The real join is:

    CStaticInstanceModelCR   [dir] n x 24B  @0x2A8  {marker, ENTITY @+8}
                             [recs] n x 88B @EOF-88n {marker, MODEL @+0x20}
    CTransformCR             176B rows      {KEY @+0x30 == ENTITY,
                                             rotation @+0x48, translation
                                             @+0x58, scale @+0x64}

Plain world-space floats -- nothing quantized, no BVH bounds involved.  The
join is total: 465/465 instances on `mpl_lobby_b2` and 732/732 on
`mpl_arena_a`, whose placements come out symmetric about the field centre to
the decimal (x +/-16.0, z +/-78.1), as the real arena is.

Structures are per the DWARF-confirmed readers in
`quest_combat_port/tools/resource_io/` (`cstaticinstancemodelcr.py`,
`cgstaticinstanceresource.py`, `ctransformcr.py`) and the prop-join RE in
`tools/convert/policy/scene_prop_decode.md`, which independently reports the
same counts on these same two maps.

`parse_bvh_resource` is retained because callers still use the level bounds for
framing, but it no longer has anything to do with instance placement.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

#: One static instance record. 24 bytes, and NOT one byte of it is a scale.
INSTANCE_STRIDE = 24

#: BVH node stride when scanning for global bounds: 6 floats + 8 skipped bytes.
BVH_NODE_STRIDE = 32
BVH_NODES_START = 64

#: Position is quantized to 9 bits per axis.
POS_BITS = 9
POS_MAX = 511.0
POS_MASK = 0x1FF

#: The material selector shares the position word.
MATERIAL_SHIFT = 27
MATERIAL_MASK = 0x1F


class _Reader:
    """Minimal little-endian cursor, so this module needs no echomod import."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def seek(self, offset: int) -> None:
        self.pos = offset

    def tell(self) -> int:
        return self.pos

    def u32(self) -> int:
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def u64(self) -> int:
        value = struct.unpack_from("<Q", self.data, self.pos)[0]
        self.pos += 8
        return value

    def u16(self) -> int:
        value = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return value

    def f32(self) -> float:
        value = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return value


def parse_bvh_resource(data: bytes) -> dict | None:
    """Global spatial bounds of a level, by scanning every BVH node.

    Returns `{"min": [x, y, z], "max": [x, y, z]}` or None.  These bounds are
    the coordinate space every quantized instance position is expressed in, so
    an error here misplaces the entire level rather than one prop.
    """
    if len(data) < 12:
        return None
    reader = _Reader(data)
    reader.seek(8)
    buf_size = reader.u32()

    if len(data) < BVH_NODES_START + buf_size:
        return None

    global_min = [float("inf")] * 3
    global_max = [float("-inf")] * 3

    reader.seek(BVH_NODES_START)
    for _ in range(buf_size // BVH_NODE_STRIDE):
        min_x, min_y, min_z = reader.f32(), reader.f32(), reader.f32()
        max_x, max_y, max_z = reader.f32(), reader.f32(), reader.f32()
        reader.seek(reader.tell() + 8)          # two unused u32s

        global_min[0] = min(global_min[0], min_x)
        global_min[1] = min(global_min[1], min_y)
        global_min[2] = min(global_min[2], min_z)
        global_max[0] = max(global_max[0], max_x)
        global_max[1] = max(global_max[1], max_y)
        global_max[2] = max(global_max[2], max_z)

    if global_min[0] == float("inf"):
        return None
    return {"min": global_min, "max": global_max}


@dataclass
class StaticInstance:
    """One placed instance: a model hash and a full world TRS.

    Every field comes from `CTransformCR` as plain floats -- there is no
    quantization and no packed rotation, so there is nothing here to expand.
    """

    index: int = 0
    #: Index into the caller's model list; parallel to the CSIMCR arrays.
    model_index: int = 0
    #: The model this instance draws, straight from `CSIMCR[i].record+0x20`.
    model_hash: str = ""
    #: `CSIMCR[i].dir+0x08`. The join key to `CTransformCR` for placement, and
    #: to `CGStaticInstanceResource.instancedata` for this instance's lightmap
    #: page and per-instance lightmap UVs.
    entity: int = 0
    #: Which level's CGSI this instance came from; a merged scene mixes several.
    level: str = ""
    position: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    #: `(x, y, z, w)` -- identity reads (0, 0, 0, 1), and the level's props come
    #: out as clean yaw rotations about Y, which is what pins the order.
    rotation: list = field(default_factory=lambda: [0.0, 0.0, 0.0, 1.0])
    scale: list = field(default_factory=lambda: [1.0, 1.0, 1.0])


def parse_static_instances(csimcr: bytes, transform_cr: bytes) -> list:
    """Static-instance placement, by joining CSIMCR entities to `CTransformCR`.

    `CGStaticInstanceResource` -- the resource this used to read -- is
    `SGStaticInstancesData`, six tables of asset/lighting bindings keyed by
    model and by entity.  It carries **no transform of any kind**, so the
    quantized-position decode that used to live here was expanding rendering
    metadata into coordinates; measured against the level's own geometry the
    result was indistinguishable from uniformly random points.

    The real join, per instance:

        CSIMCR[i].entity  ->  CTransformCR row .key
                              .translation / .rotation / .scale

    These are plain floats in world space -- nothing is quantized, so no BVH
    bounds are involved.  An instance whose entity has no transform row is
    dropped rather than defaulted to the origin.
    """
    pairs = parse_static_instance_models(csimcr)
    if not pairs:
        return []
    placements = parse_transform_components(transform_cr,
                                            {entity for entity, _ in pairs})

    out: list = []
    for index, (entity, model) in enumerate(pairs):
        found = placements.get(entity)
        if found is None:
            continue
        position, rotation, scale = found
        out.append(StaticInstance(
            index=index,
            model_index=index,        # CSIMCR is parallel: one model per instance
            model_hash=model,
            entity=entity,
            position=position,
            rotation=rotation,
            scale=scale,
        ))
    return out


#: `CStaticInstanceModelCR` type symbol; leads every directory entry AND every
#: 88-byte instance record, so it doubles as the stride validator.
CSIMCR_MARKER = 0x0D61FA822EBE57A8
CSIMCR_HEADER = 0x2A8            # fixed descriptor header, 25/25 shipped files
CSIMCR_DIR_STRIDE = 24           # {marker, entity CSymbol64 @+8, 8B trailer}
CSIMCR_REC_STRIDE = 88           # instance records, anchored at EOF
CSIMCR_MODEL_OFF = 0x20          # model CSymbol64 inside a record

#: `CTransformCR` component row. 32-byte `SncaComponentData::SProperties`
#: header + 144-byte body; the TRS block is contiguous at the offsets below.
TRANSFORM_STRIDE = 176
TRANSFORM_KEY = 0x30             # == the CSIMCR entity
TRANSFORM_ROT = 0x48             # unit quaternion (x, y, z, w)
TRANSFORM_POS = 0x58             # 3x f32 WORLD translation
TRANSFORM_SCALE = 0x64           # 3x f32 per-instance scale


def parse_static_instance_models(data: bytes) -> list:
    """`CStaticInstanceModelCR` -> per-instance `(entity, model_hash)`.

    The file is the in-place CD payload of `SStaticInstanceModelCD::SResource`:
    a fixed 0x2A8 descriptor header, then a pooled body whose regions are
    located from the header rather than walked.  Two of those regions are
    parallel per-instance arrays:

        [dir]  n x 24B at 0x2A8      {marker, ENTITY @+8, trailer}
        [recs] n x 88B anchored EOF  {marker, ..., MODEL @+0x20}

    `[dir][i]` and `[recs][i]` describe the same instance, so this returns them
    zipped.  The ENTITY is the join key into `CTransformCR`, which is where the
    world transform actually lives -- this resource holds no placement at all.

    Both regions lead with `CSIMCR_MARKER`, which is checked on every entry: a
    wrong stride or a mislocated region fails immediately instead of returning
    plausible garbage.
    """
    if len(data) < CSIMCR_HEADER + 8:
        return []

    count = struct.unpack_from("<Q", data, 0x28)[0]
    # @0x28 / @0x30 / @0x70 are three mirrors of the instance count, and
    # @0x08 == 24*n is the directory byte size. Disagreement means this is not
    # the layout described above.
    if (count == 0 or count > 1_000_000
            or struct.unpack_from("<Q", data, 0x30)[0] != count
            or struct.unpack_from("<Q", data, 0x70)[0] != count
            or struct.unpack_from("<Q", data, 0x08)[0] != CSIMCR_DIR_STRIDE * count):
        return []

    rec0 = len(data) - CSIMCR_REC_STRIDE * count
    if rec0 < CSIMCR_HEADER + CSIMCR_DIR_STRIDE * count:
        return []

    out: list = []
    for i in range(count):
        d = CSIMCR_HEADER + CSIMCR_DIR_STRIDE * i
        r = rec0 + CSIMCR_REC_STRIDE * i
        if (struct.unpack_from("<Q", data, d)[0] != CSIMCR_MARKER
                or struct.unpack_from("<Q", data, r)[0] != CSIMCR_MARKER):
            return []
        entity = struct.unpack_from("<Q", data, d + 8)[0]
        model = struct.unpack_from("<Q", data, r + CSIMCR_MODEL_OFF)[0]
        out.append((entity, f"{model:016x}"))
    return out


def parse_transform_components(data: bytes, wanted) -> dict:
    """`CTransformCR` -> `{entity: (translation, rotation, scale)}`.

    Rows are 176 bytes, but the row block does not start at a fixed offset and
    the table also holds rows for components that are not static instances. So
    rather than framing the file, this looks up each wanted entity directly:
    scan for the key, then read the TRS at fixed deltas from it.

    Every hit is checked for a unit quaternion, which rejects a coincidental
    8-byte match on a non-key field.
    """
    if not wanted:
        return {}
    targets = set(wanted)
    out: dict = {}
    for off in range(0, len(data) - TRANSFORM_STRIDE + TRANSFORM_KEY, 4):
        key = struct.unpack_from("<Q", data, off)[0]
        if key not in targets or key in out:
            continue
        row = off - TRANSFORM_KEY
        if row < 0 or row + TRANSFORM_STRIDE > len(data):
            continue
        rot = struct.unpack_from("<4f", data, row + TRANSFORM_ROT)
        if abs(sum(c * c for c in rot) - 1.0) > 0.02:
            continue
        out[key] = (list(struct.unpack_from("<3f", data, row + TRANSFORM_POS)),
                    list(rot),
                    list(struct.unpack_from("<3f", data, row + TRANSFORM_SCALE)))
    return out


def unpack_rotation(packed: int) -> tuple:
    """Packed u32 -> `(x, y, z, w)` quaternion.

    ⚠ `app.py` carries the packed value through to its frontend and does not
    expand it in Python, so unlike everything else here this is NOT ported from
    a working implementation -- it is the 10/10/10/2 layout the field size
    implies, with the largest component reconstructed from the other three.
    If rotations come out wrong while positions are right, this is the suspect.
    """
    scale = 1.0 / 511.0
    x = ((packed & 0x3FF) - 511) * scale
    y = (((packed >> 10) & 0x3FF) - 511) * scale
    z = (((packed >> 20) & 0x3FF) - 511) * scale
    total = x * x + y * y + z * z
    w = (1.0 - total) ** 0.5 if total < 1.0 else 0.0
    return (x, y, z, w)
