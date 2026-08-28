"""Quest geometry: `CGMeshListResourceAndroid` + its GPU sibling.

## Why this is format-driven and the PC path is not

The PC importer finds vertex data by SEARCHING the GPU blob for byte runs that
repeat at a plausible stride (`app/extract/evr_mesh_importer/decode.py`). It has
to, because nothing told it the layout. On Quest nothing needs guessing: the
Primary descriptor carries the full `CGVertexFormat`, so every attribute's
stream, offset, format and size is stated, and the GPU sibling is sliced rather
than probed.

## The Primary walk

Count-driven, from `quest_combat_port`'s `resource_io/cgmeshlistresource.py`
(MIT), which validated it byte-exact on 565/565 non-empty descriptors:

    [u32 N]                          meshes
    N  x 152 B                       24 B cook identity + 128 B CGMeshData
    [u32 Nrp] Nrp x 112 B            CGRenderParams (draw sections)
    [u32 Nvb] Nvb x 336 B            288 B CGVertexFormat + u64 used + 40 B trailer
    [u32 0][u32 0]                   morph buffers
    [u32 Nib] Nib x 16 B             CGIndexBufferData
    [u32 n][n x u32]                 lod child indices
    [u32 n][n x u32]                 cbuffer indices
    [u32][u32]                       numcbuffers, cbufferoffset
    [u64 extra][u64 gpudatasize]     gpudatasize == the GPU sibling's size

## `SVertexElement`, 8 bytes -- decoded here

    +0  u8  usage        0 position, 4 texcoord (see `USAGE_*`)
    +1  u8  offset       byte offset inside its stream
    +2  u8  format       component type (see `FORMAT_*`)
    +3  u8  components   component count
    +4  u8  usage_index  which texcoord / colour set
    +5  u8  size         total bytes, == components * sizeof(format)
    +6  u8  stream       WHICH BUFFER -- streams are separate blocks, not
                         interleaved fields of one vertex
    +7  u8  pad
    0x0C in +0 terminates the table.

⭐ Two independent checks, run over every vertex buffer in the shipped tree
(**1741 of 1741**, and `--verify` re-runs them):

  * each stream's extent -- `max(offset + size)` over its own elements -- times
    the vertex count equals the byte size the GPU trailer states;
  * the float3 at the POSITION element reproduces the mesh AABB **from the
    Primary descriptor**, worst error 0.00000 over the corpus. Those two numbers
    come from different files, so a wrong field assignment cannot fake it.

⚠ Streams are SEPARATE BLOCKS laid out in stream order, each `count` records
long. Reading them as one interleaved vertex is the mistake this format invites:
a 36-byte stream 0 followed by a 28-byte stream 1 looks exactly like a 64-byte
interleaved vertex until the floats come out as garbage.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# ── Primary record sizes ───────────────────────────────────────────────
EMPTY_SIZE = 56
MESH_REC = 152
RENDERPARAM_REC = 112
ATTR_BYTES = 288
VERTEX_BUFFER_REC = ATTR_BYTES + 8 + 40
INDEX_BUFFER_REC = 16

#: Offsets of the named fields inside a 152-byte mesh record.
M_NAME = 0x14
M_AABB = 0x3C
M_SPHERE = 0x54

# ── SVertexElement ─────────────────────────────────────────────────────
ELEMENT_BYTES = 8
ELEMENT_TERMINATOR = 0x0C

USAGE_POSITION = 0
USAGE_PACKED = 1          # two ubyte4s: normal / tangent, sign in w
USAGE_TANGENT_A = 2
USAGE_TANGENT_B = 3
USAGE_TEXCOORD = 4
USAGE_POSITION_ALT = 11

FORMAT_UBYTE = 1
FORMAT_SHORT = 3
FORMAT_HALF = 5
FORMAT_FLOAT = 8

#: Bytes per component, by `format`. Only the four the corpus uses are mapped;
#: an unknown format falls back to `size // components`, which is always right
#: because `size` is stated too.
COMPONENT_BYTES = {FORMAT_UBYTE: 1, FORMAT_SHORT: 2, FORMAT_HALF: 2,
                   FORMAT_FLOAT: 4}

#: The GPU trailer, 10 u32. Only three slots have call sites here.
T_BASE = 0        # byte offset of this buffer's FIRST stream in the GPU sibling
T_FIRST_BYTES = 2  # byte size of stream 0
T_COUNT = 5       # vertex count


@dataclass
class VertexElement:
    usage: int
    offset: int
    format: int
    components: int
    usage_index: int
    size: int
    stream: int


@dataclass
class Mesh:
    index: int
    name: int
    aabb: tuple
    sphere: tuple
    elements: list = field(default_factory=list)
    count: int = 0
    base: int = 0
    strides: dict = field(default_factory=dict)
    index_offset: int = 0
    index_count: int = 0
    index_size: int = 2

    def stream_base(self, stream: int) -> int:
        """Byte offset of one stream's block inside the GPU sibling."""
        at = self.base
        for other in sorted(self.strides):
            if other == stream:
                break
            at += self.strides[other] * self.count
        return at


@dataclass
class MeshList:
    empty: bool
    meshes: list
    gpu_size: int


def _u32(blob, at):
    return struct.unpack_from("<I", blob, at)[0]


def vertex_elements(attr: bytes, used: int) -> list:
    """The `used` elements of one `CGVertexFormat`, terminator-aware."""
    out = []
    for i in range(min(used, ATTR_BYTES // ELEMENT_BYTES)):
        raw = attr[i * ELEMENT_BYTES:(i + 1) * ELEMENT_BYTES]
        if len(raw) < ELEMENT_BYTES or raw[0] == ELEMENT_TERMINATOR:
            break
        out.append(VertexElement(usage=raw[0], offset=raw[1], format=raw[2],
                                 components=raw[3], usage_index=raw[4],
                                 size=raw[5], stream=raw[6]))
    return out


def stream_strides(elements) -> dict:
    """`{stream: stride}` -- each stream's extent over its OWN elements."""
    out = {}
    for element in elements:
        end = element.offset + element.size
        if end > out.get(element.stream, 0):
            out[element.stream] = end
    return out


def read(blob: bytes) -> MeshList:
    """Walk a `CGMeshListResourceAndroid` Primary descriptor."""
    if len(blob) <= EMPTY_SIZE and not any(blob):
        return MeshList(empty=True, meshes=[], gpu_size=0)

    at = 0
    count = _u32(blob, at); at += 4
    raw_meshes = []
    for _ in range(count):
        record = blob[at:at + MESH_REC]
        raw_meshes.append((
            struct.unpack_from("<Q", record, M_NAME)[0],
            struct.unpack_from("<6f", record, M_AABB),
            struct.unpack_from("<4f", record, M_SPHERE)))
        at += MESH_REC

    n_rp = _u32(blob, at); at += 4 + n_rp * RENDERPARAM_REC

    n_vb = _u32(blob, at); at += 4
    buffers = []
    for _ in range(n_vb):
        attr = blob[at:at + ATTR_BYTES]
        used = struct.unpack_from("<Q", blob, at + ATTR_BYTES)[0]
        trailer = struct.unpack_from("<10I", blob, at + ATTR_BYTES + 8)
        buffers.append((vertex_elements(attr, used), trailer))
        at += VERTEX_BUFFER_REC

    at += 4 + _u32(blob, at) * 0          # morph buffers, always empty
    at += 4 + _u32(blob, at) * 0          # morph index buffers, always empty

    n_ib = _u32(blob, at); at += 4
    index_buffers = []
    for _ in range(n_ib):
        index_buffers.append(struct.unpack_from("<4I", blob, at))
        at += INDEX_BUFFER_REC

    for _ in range(2):                    # lodchildindices, cbufferidx
        n = _u32(blob, at)
        at += 4 + n * 4
    at += 8                               # numcbuffers, cbufferoffset
    at += 8                               # extra
    gpu_size = struct.unpack_from("<Q", blob, at)[0] if at + 8 <= len(blob) else 0

    meshes = []
    for i, (name, aabb, sphere) in enumerate(raw_meshes):
        mesh = Mesh(index=i, name=name, aabb=aabb, sphere=sphere)
        if i < len(buffers):
            elements, trailer = buffers[i]
            mesh.elements = elements
            mesh.strides = stream_strides(elements)
            mesh.base = trailer[T_BASE]
            mesh.count = trailer[T_COUNT]
        if i < len(index_buffers):
            offset, n_index, size, _pad = index_buffers[i]
            mesh.index_offset, mesh.index_count, mesh.index_size = (
                offset, n_index, size or 2)
        meshes.append(mesh)
    return MeshList(empty=False, meshes=meshes, gpu_size=gpu_size)


#: The unused tail of a `CGVertexFormat`: every slot past `used` reads this.
TERMINATOR_RUN = bytes([ELEMENT_TERMINATOR]) + bytes([0xFF]) * 7

#: Bytes after the last table in a CIMR primary, measured on the corpus.
VERTEX_TAIL = 32

#: A `RadArrayDescriptor`: 56 bytes, `mark == 32` at +0x20 with mirrored counts.
DESCRIPTOR_SIZE = 0x08
DESCRIPTOR_MARK = 0x20
DESCRIPTOR_COUNT = 0x28
DESCRIPTOR_MARK_VALUE = 32


def _descriptors(blob: bytes) -> list:
    """`[(size, count), ...]` in declaration order."""
    out = []
    at = 0
    while at + 0x38 <= len(blob):
        size = struct.unpack_from("<Q", blob, at + DESCRIPTOR_SIZE)[0]
        mark, total, used = struct.unpack_from("<QQQ", blob, at + DESCRIPTOR_MARK)
        if mark == DESCRIPTOR_MARK_VALUE and total == used and used:
            out.append((size, used))
        at += 8
    return out


def _data_base(blob: bytes, tables: list):
    """Where the CIMR data tables begin, anchored on the vertex format.

    The attr block of vertex buffer 0 ends in a run of `0c ff ff ff ff ff ff ff`
    terminators, so the first such run minus `used * 8` is the block's start,
    and stepping back over the mesh and render-param tables gives the origin.
    A fallback of `len - sum(sizes) - VERTEX_TAIL` is used when no terminator is
    found; both agree on every file in the corpus.
    """
    at = blob.find(TERMINATOR_RUN)
    total = sum(size for size, _count in tables)
    guess = len(blob) - total - VERTEX_TAIL
    if at < 0:
        return guess if guess >= 0 else None
    ahead = 0
    for size, count in tables:
        stride = size // count if count else 0
        if stride == VERTEX_BUFFER_REC:
            break
        ahead += size
    # `at` is the FIRST terminator. Walking BACK from it cannot find the block
    # start -- the live elements ahead of it look like ordinary data and the
    # walk just runs to the 288-byte cap. Count the terminators FORWARD instead:
    # they run to the end of the block, so the block ENDS at `at + k*8` and
    # therefore starts 288 bytes before that.
    end = at
    while (end + ELEMENT_BYTES <= len(blob)
           and blob[end:end + ELEMENT_BYTES] == TERMINATOR_RUN):
        end += ELEMENT_BYTES
    base = end - ATTR_BYTES - ahead
    return base if 0 <= base < len(blob) else (guess if guess >= 0 else None)


def read_instanced(blob: bytes) -> MeshList:
    """Walk a `CGInstancedModelResourceAndroid` Primary descriptor.

    ⭐ Same tables as a mesh list -- 152-byte mesh records, 112-byte render
    params, 336-byte vertex buffers, 16-byte index buffers -- but declared
    through `RadArrayDescriptor`s instead of the mesh list's compact
    `[u32 count][records]` form. The strides in the descriptors are literally
    `MESH_REC` / `RENDERPARAM_REC` / `VERTEX_BUFFER_REC` / `INDEX_BUFFER_REC`,
    which is what identifies the tables without guessing.

    ⛔ Written because the PC pattern-scanner UNDER-READS these. On the arena's
    two 65,280-byte instanced models it recovered 1,212 vertices each; the
    descriptors say what is really there, and reading them properly is what
    turned a sparse render into a populated one.

    The data region starts at `len(blob) - sum(declared sizes)` -- the tables
    pack flush to the end of the file, in descriptor order.
    """
    tables = _descriptors(blob)
    if not tables:
        return MeshList(empty=True, meshes=[], gpu_size=0)
    # ⚠ NOT `len(blob) - total`: that lands 32 bytes late on every file tried,
    # so the tables sit flush against a 32-byte tail rather than against EOF.
    # The base is confirmed independently by the vertex-format terminator run
    # (`0c ff*7`), which pins the attr block and therefore the table origin.
    base = _data_base(blob, tables)
    if base is None:
        return MeshList(empty=True, meshes=[], gpu_size=0)

    wanted = {MESH_REC: None, RENDERPARAM_REC: None,
              VERTEX_BUFFER_REC: None, INDEX_BUFFER_REC: None}
    at = base
    for size, count in tables:
        stride = size // count if count else 0
        if stride in wanted and wanted[stride] is None and size:
            wanted[stride] = (at, count)
        at += size

    if not wanted[MESH_REC] or not wanted[VERTEX_BUFFER_REC]:
        return MeshList(empty=True, meshes=[], gpu_size=0)

    mesh_at, mesh_count = wanted[MESH_REC]
    meshes = []
    for i in range(mesh_count):
        record = blob[mesh_at + i * MESH_REC:mesh_at + (i + 1) * MESH_REC]
        if len(record) < MESH_REC:
            break
        meshes.append(Mesh(
            index=i,
            name=struct.unpack_from("<Q", record, M_NAME)[0],
            aabb=struct.unpack_from("<6f", record, M_AABB),
            sphere=struct.unpack_from("<4f", record, M_SPHERE)))

    vb_at, vb_count = wanted[VERTEX_BUFFER_REC]
    for i in range(min(vb_count, len(meshes))):
        at = vb_at + i * VERTEX_BUFFER_REC
        used = struct.unpack_from("<Q", blob, at + ATTR_BYTES)[0]
        trailer = struct.unpack_from("<10I", blob, at + ATTR_BYTES + 8)
        mesh = meshes[i]
        mesh.elements = vertex_elements(blob[at:at + ATTR_BYTES], used)
        mesh.strides = stream_strides(mesh.elements)
        mesh.base = trailer[T_BASE]
        mesh.count = trailer[T_COUNT]

    if wanted[INDEX_BUFFER_REC]:
        ib_at, ib_count = wanted[INDEX_BUFFER_REC]
        for i in range(min(ib_count, len(meshes))):
            offset, n_index, size, _pad = struct.unpack_from(
                "<4I", blob, ib_at + i * INDEX_BUFFER_REC)
            meshes[i].index_offset = offset
            meshes[i].index_count = n_index
            meshes[i].index_size = size or 2
    return MeshList(empty=False, meshes=meshes, gpu_size=0)


def stream_block(gpu: bytes, mesh: Mesh, stream: int):
    """`(memoryview, stride)` of one stream's block, or `(None, 0)`."""
    stride = mesh.strides.get(stream)
    if not stride or not mesh.count:
        return None, 0
    at = mesh.stream_base(stream)
    span = stride * mesh.count
    if at + span > len(gpu):
        return None, 0
    return memoryview(gpu)[at:at + span], stride


def attribute(gpu: bytes, mesh: Mesh, element: VertexElement):
    """One attribute as a list of tuples, `None` when it cannot be sliced."""
    block, stride = stream_block(gpu, mesh, element.stream)
    if block is None:
        return None
    per = COMPONENT_BYTES.get(element.format)
    if not per:
        per = element.size // max(element.components, 1)
    code = {(FORMAT_FLOAT, 4): "f", (FORMAT_HALF, 2): "e",
            (FORMAT_SHORT, 2): "h", (FORMAT_UBYTE, 1): "B"}.get(
                (element.format, per))
    if code is None:
        return None
    layout = "<" + code * element.components
    out = []
    for i in range(mesh.count):
        at = i * stride + element.offset
        out.append(struct.unpack_from(layout, block, at))
    return out


def positions(gpu: bytes, mesh: Mesh):
    element = next((e for e in mesh.elements
                    if e.usage == USAGE_POSITION and e.size == 12), None)
    return attribute(gpu, mesh, element) if element else None


def texcoords(gpu: bytes, mesh: Mesh) -> dict:
    """`{usage_index: [(u, v), ...]}` for every texcoord element."""
    out = {}
    for element in mesh.elements:
        if element.usage != USAGE_TEXCOORD or element.components < 2:
            continue
        values = attribute(gpu, mesh, element)
        if values is None:
            continue
        out.setdefault(element.usage_index, [(v[0], v[1]) for v in values])
    return out


def indices(gpu: bytes, mesh: Mesh):
    if not mesh.index_count:
        return None
    code = "<%d%s" % (mesh.index_count, "H" if mesh.index_size == 2 else "I")
    span = mesh.index_count * mesh.index_size
    if mesh.index_offset + span > len(gpu):
        return None
    return list(struct.unpack_from(code, gpu, mesh.index_offset))
