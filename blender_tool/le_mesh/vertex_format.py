"""NRadEngine vertex-format decoding — the "support every mesh format" core.

Pure stdlib (struct/math only). No Oodle, no bpy, no numpy — so it can be unit
tested with plain `python3` and imported unchanged inside Blender's bundled
Python.

Ground truth:
  struct CGVertexFormat::SVertexElement  (8 bytes each)
    +0x00 uint8 usage         (EUsage)
    +0x01 uint8 offset        byte offset of this attribute WITHIN the vertex stride
    +0x02 uint8 type          (EType) component format
    +0x03 uint8 count         number of components (1..4)
    +0x04 uint8 slot          semantic index (e.g. which UV / color set)
    +0x05 uint8 size          AUTHORITATIVE byte footprint of this element on disk
    +0x06 uint8 stream        vertex stream index (0..3)
    +0x07 uint8 instancerate  instancing step rate (0 = per-vertex)

  struct CGVertexFormat
    +0x000 SVertexElement data[36]   (0x120 bytes)
    +0x120 uint64 used              (on disk: the ACTIVE ELEMENT COUNT, not a bitmask)

  struct CGVertexBufferData  (stride 0x130)
    +0x000 CGVertexFormat format
    +0x120 used            (active element count on disk)
    +0x128 uint32 offset   relative byte offset into the paired GPU slice
    +0x12C uint32 numvertices

Key rule: stride = max(element.offset + element.size). Trust the `size` byte for
each element's footprint; do NOT recompute width from type*count (packed types
7/9/10 have no simple width).

Packed types (M3 probe result): `tests/probe_vertex_types.py` walked 92 vertex
buffers across 9 archives (incl. the skinned character archive 0703fd2acd5803e9)
and found ONLY eF32 (position/texcoord), eS16n (normal/tangent), eU8n (color/
skin-weights), eU16n (lightmap uv) and eU8 (skin-indices). The packed types
eCmp(7)/eSphN(9)/eSphT(10) did NOT appear on any usage, renderable or otherwise.
So their (undocumented) bit layout is left `packed_unresolved` and decoding its
exact bit layout is NOT warranted for this corpus. When a packed element IS
encountered, we still record its raw on-disk footprint (`raw_byte_stride`) so no
information is silently dropped; decoding it remains a labelled TODO.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


# --- CGVertexFormat::EUsage -------------------------------------------------
class EUsage:
    ePosition = 0
    eColor = 1
    eNormal = 2
    eTangent = 3
    eTexCoord = 4
    eSkinIndices = 5
    eSkinWeights = 6
    eSOPosition = 7   # stream-out (transform feedback) — not import geometry
    eSONormal = 8
    eSOTangent = 9
    eInstanceData = 10
    eNeverUsage = 11


USAGE_NAMES = {
    0: "ePosition", 1: "eColor", 2: "eNormal", 3: "eTangent", 4: "eTexCoord",
    5: "eSkinIndices", 6: "eSkinWeights", 7: "eSOPosition", 8: "eSONormal",
    9: "eSOTangent", 10: "eInstanceData", 11: "eNeverUsage",
}


# --- CGVertexFormat::EType --------------------------------------------------
class EType:
    eU8 = 0     # uint8 integer
    eU8n = 1    # uint8 normalized -> [0,1]  (/255)
    eU16 = 2    # uint16 integer
    eU16n = 3   # uint16 normalized -> [0,1] (/65535)
    eS16 = 4    # int16 integer
    eS16n = 5   # int16 normalized -> [-1,1] (/32767)
    eF16 = 6    # half float (IEEE binary16)
    eCmp = 7    # packed vector (use `size`) — bit layout not decoded
    eF32 = 8    # float32
    eSphN = 9   # spherical/octahedral normal (packed) — not decoded (unused in M3 probe)
    eSphT = 10  # spherical/octahedral tangent (packed) — not decoded (unused in M3 probe)


TYPE_NAMES = {
    0: "eU8", 1: "eU8n", 2: "eU16", 3: "eU16n", 4: "eS16", 5: "eS16n",
    6: "eF16", 7: "eCmp", 8: "eF32", 9: "eSphN", 10: "eSphT",
}

# Per-component byte width for the "regular" (unpacked) types. Packed types
# (eCmp/eSphN/eSphT) are absent on purpose — use the element `size` byte.
_TYPE_WIDTH = {0: 1, 1: 1, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 8: 4}

# struct.unpack format char + normaliser for the regular types.
_TYPE_UNPACK = {
    EType.eU8:   ("B", None),
    EType.eU8n:  ("B", 255.0),
    EType.eU16:  ("H", None),
    EType.eU16n: ("H", 65535.0),
    EType.eS16:  ("h", None),
    EType.eS16n: ("h", 32767.0),   # clamped to >= -1.0 below
    EType.eF16:  ("e", None),
    EType.eF32:  ("f", None),
}

PACKED_TYPES = frozenset({EType.eCmp, EType.eSphN, EType.eSphT})

# Usages that are not renderable import attributes (stream-out, instancing).
_SKIP_USAGES = frozenset({
    EUsage.eSOPosition, EUsage.eSONormal, EUsage.eSOTangent,
    EUsage.eInstanceData, EUsage.eNeverUsage,
})


@dataclass
class VertexElement:
    usage: int
    offset: int
    type: int
    count: int
    slot: int
    size: int
    stream: int
    instancerate: int

    @property
    def usage_name(self) -> str:
        return USAGE_NAMES.get(self.usage, f"usage{self.usage}")

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type, f"type{self.type}")

    @property
    def is_packed(self) -> bool:
        return self.type in PACKED_TYPES

    def as_dict(self) -> dict:
        return {
            "usage": self.usage, "usage_name": self.usage_name,
            "offset": self.offset, "type": self.type, "type_name": self.type_name,
            "count": self.count, "slot": self.slot, "size": self.size,
            "stream": self.stream, "instancerate": self.instancerate,
        }


# Offsets inside a CGVertexBufferData record (stride 0x130).
VB_RECORD_STRIDE = 0x130
VB_USED_OFF = 0x120        # active element count (on disk)
VB_GPU_OFFSET_OFF = 0x128  # relative GPU byte offset
VB_NUMVERTS_OFF = 0x12C
MAX_ELEMENTS = 36


def parse_elements(data: bytes, base: int, active_count: int) -> list[VertexElement]:
    """Read `active_count` contiguous 8-byte SVertexElement records at `base`."""
    if not 0 < active_count <= MAX_ELEMENTS:
        raise ValueError(f"unsupported active element count {active_count}")
    out: list[VertexElement] = []
    for i in range(active_count):
        u, o, t, c, s, sz, st, ir = struct.unpack_from("<8B", data, base + i * 8)
        out.append(VertexElement(u, o, t, c, s, sz, st, ir))
    return out


def read_vertex_format(primary: bytes, vb_off: int) -> tuple[list[VertexElement], int, int, int]:
    """Parse one CGVertexBufferData record.

    Returns (elements, stride, rel_gpu_offset, vertex_count).
    """
    active = struct.unpack_from("<I", primary, vb_off + VB_USED_OFF)[0]
    elements = parse_elements(primary, vb_off, active)
    rel_gpu = struct.unpack_from("<I", primary, vb_off + VB_GPU_OFFSET_OFF)[0]
    count = struct.unpack_from("<I", primary, vb_off + VB_NUMVERTS_OFF)[0]
    stride = compute_stride(elements)
    return elements, stride, rel_gpu, count


def compute_stride(elements: list[VertexElement]) -> int:
    """Stride = max(offset + size) over all active elements (authoritative)."""
    return max((e.offset + e.size for e in elements), default=0)


def _decode_component(buf: bytes, off: int, etype: int, count: int,
                      normalize: bool) -> list[float] | list[int] | None:
    """Decode `count` scalars of `etype` at absolute offset `off`.

    Returns None for packed/unsupported types (caller records them raw).
    When `normalize` is False, *n types are returned as raw integers.
    """
    spec = _TYPE_UNPACK.get(etype)
    if spec is None:
        return None  # packed eCmp/eSphN/eSphT — not decoded
    ch, norm = spec
    vals = struct.unpack_from(f"<{count}{ch}", buf, off)
    if norm is None or not normalize:
        # integer/float value as-is
        if etype in (EType.eF16, EType.eF32):
            return [float(v) for v in vals]
        return [int(v) for v in vals]
    if etype == EType.eS16n:
        # signed normalized: /32767, clamp low end to -1.0 (DX convention)
        return [max(-1.0, v / norm) for v in vals]
    return [v / norm for v in vals]


# Canonical attribute-name assignment ----------------------------------------

def attribute_key(elem: VertexElement, seen: dict[int, int]) -> str | None:
    """Map an element to a stable canonical attribute name.

    Multi-set usages (color / texcoord) get a numeric suffix by appearance
    order: color0/color1, uv0/uv1/uv2. Returns None for usages we do not import.
    """
    u = elem.usage
    if u in _SKIP_USAGES:
        return None
    if u == EUsage.ePosition:
        return "position"
    if u == EUsage.eNormal:
        return "normal"
    if u == EUsage.eTangent:
        return "tangent"
    if u == EUsage.eSkinIndices:
        return "skin_indices"
    if u == EUsage.eSkinWeights:
        return "skin_weights"
    if u == EUsage.eColor:
        n = seen.get(EUsage.eColor, 0)
        seen[EUsage.eColor] = n + 1
        return f"color{n}"
    if u == EUsage.eTexCoord:
        n = seen.get(EUsage.eTexCoord, 0)
        seen[EUsage.eTexCoord] = n + 1
        return f"uv{n}"
    return None


@dataclass
class DecodedAttribute:
    key: str
    usage: int
    comps: int            # components per vertex actually decoded
    is_integer: bool      # True -> values are ints (skin indices)
    packed_unresolved: bool
    element: VertexElement
    data: list            # flat, row-major: len == vertex_count * comps
    # For packed elements: raw on-disk bytes per vertex, so the
    # undecoded footprint is recorded rather than silently dropped. None for
    # normally-decoded attributes. See the module docstring / M3 probe.
    raw_byte_stride: int | None = None


def decode_vertex_buffer(gpu: bytes, gpu_base: int, rel_gpu: int, stride: int,
                         vertex_count: int, elements: list[VertexElement],
                         ) -> dict[str, DecodedAttribute]:
    """Decode every importable element of a vertex buffer into flat arrays.

    `gpu`      : the decompressed paired GPU slice bytes
    `gpu_base` : absolute base of this resource's GPU slice within `gpu`
    `rel_gpu`  : CGVertexBufferData.offset (relative into the slice)
    Values are stored flat row-major so a package can pack them straight to
    little-endian .bin blobs.
    """
    attrs: dict[str, DecodedAttribute] = {}
    seen: dict[int, int] = {}
    start = gpu_base + rel_gpu
    for elem in elements:
        key = attribute_key(elem, seen)
        if key is None:
            continue
        is_integer = elem.usage == EUsage.eSkinIndices
        normalize = not is_integer
        flat: list = []
        packed = elem.is_packed
        if packed:
            # Store nothing decoded; record presence + raw footprint
            # so callers/manifest note it and no bytes are silently dropped.
            attrs[key] = DecodedAttribute(key, elem.usage, elem.count, is_integer,
                                          True, elem, [], raw_byte_stride=elem.size)
            continue
        for i in range(vertex_count):
            off = start + i * stride + elem.offset
            vals = _decode_component(gpu, off, elem.type, elem.count, normalize)
            if vals is None:
                packed = True
                break
            flat.extend(vals)
        if packed:
            attrs[key] = DecodedAttribute(key, elem.usage, elem.count, is_integer,
                                          True, elem, [], raw_byte_stride=elem.size)
            continue
        attrs[key] = DecodedAttribute(key, elem.usage, elem.count, is_integer,
                                      False, elem, flat)
    return attrs
