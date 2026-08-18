"""`CActorDataResource` -- the level's actor table, read build-agnostically.

## Why this is in-repo

`evr_scene_extract` previously reached into a hard-coded
`C:\\Users\\lucas\\Desktop\\FreshEVR\\evrFileTools` for this parser, which makes
the project unshippable and pins it to one machine.  This is the same reader,
vendored, with the two build assumptions removed.

## The two assumptions that broke on other builds

The resource opens with a fixed prologue, then a VARIABLE run of 56-byte array
descriptors, then a run of 64-byte "bits" descriptors:

    0x000  desc_headers      64B
    0x040  desc_components   64B
    0x080  desc_nodeids      56B
    0x0B8  desc_names        56B
    0x0F0  desc_prefabids    56B
    0x128  N x 56B           intermediate descriptors   <- N varies
    ...    M x 64B           bits descriptors           <- M varies

The upstream reader (a) located the bits block by requiring `pad0 == 0 and
flags == 1` on five descriptors, and (b) hard-coded **M = 5**.  Measured across
four builds, both vary:

| build | N | M |
|---|---:|---:|
| Echo VR PCVR (`H:/pcvr-extracted`) | 3 | 5 |
| Lone Echo 2 | 3 | 5 |
| Echo VR Summer lobby (Win7) | 4 | 5 |
| **Lone Echo 2 trailer (Win7)** | **3** | **3** |

The trailer has only three bits descriptors, so a reader that consumes five
runs past the end of the header and desyncs the whole file -- which is exactly
the `EOFError: Expected 2 bytes at offset 75096` this replaces.

## How the block is located now

By the descriptor's own invariants rather than by `flags`.  A `RadArrayDescriptor`
is three POINTERS that are nulled on disk (`p_data` @+0x00, `p_allocator`
@+0x10, `p_base` @+0x20) plus `capacity` @+0x28 and `count` @+0x30.  So a
candidate is valid when all three pointers are zero and `count <= capacity`.
`flags` is not consulted: it is 1 on Summer and 0 on the trailer for descriptors
that are otherwise identical, which is what made it a bad discriminator.

`N` and `M` are then chosen as the pair that maximises the run of valid 64-byte
descriptors -- self-checking, because a wrong `N` produces at most one or two
valid entries before hitting string data (measured: N=2 gives 1/5, N=4 gives
0/5, N=3 gives 3/3 on the trailer).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

DESC56 = 56
DESC64 = 64
#: 64 + 64 + 56 + 56 + 56 -- the fixed prologue before the variable run.
PROLOGUE = 296
#: A bits block shorter than this is not believed.
MIN_BITS = 3
#: Transform record sizes seen in the wild: full TRS, and TR with no scale.
TRANSFORM_STRIDE_TRS = 48
TRANSFORM_STRIDE_TR = 32
MAX_N = 12
MAX_BITS = 8


class ActorDataError(ValueError):
    """The resource does not parse as a `CActorDataResource`."""


@dataclass
class Descriptor:
    data_byte_size: int = 0
    capacity: int = 0
    count: int = 0

    @classmethod
    def at(cls, data: bytes, offset: int) -> "Descriptor":
        dbs = struct.unpack_from("<Q", data, offset + 0x08)[0]
        cap = struct.unpack_from("<Q", data, offset + 0x28)[0]
        cnt = struct.unpack_from("<Q", data, offset + 0x30)[0]
        return cls(dbs, cap, cnt)


def _valid(data: bytes, offset: int) -> bool:
    """A descriptor's three on-disk pointers are null and count <= capacity."""
    if offset + DESC56 > len(data):
        return False
    p_data, _dbs, p_alloc = struct.unpack_from("<3Q", data, offset)
    p_base, capacity, count = struct.unpack_from("<3Q", data, offset + 0x20)
    if p_data or p_alloc or p_base:
        return False
    return count <= capacity < 10 ** 7


def detect_layout(data: bytes) -> tuple:
    """`(N, M)` -- intermediate descriptor count and bits-descriptor count."""
    best = (None, 0)
    for n in range(MAX_N):
        base = PROLOGUE + DESC56 * n
        if base + DESC64 > len(data):
            break
        run = 0
        while run < MAX_BITS and _valid(data, base + run * DESC64):
            run += 1
        if run > best[1]:
            best = (n, run)
    if best[0] is None or best[1] < MIN_BITS:
        raise ActorDataError(
            f"no descriptor block found (best run {best[1]} at N={best[0]})")
    return best


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if self.pos + n > len(self.data):
            raise ActorDataError(
                f"expected {n} bytes at {self.pos}, file is {len(self.data)}")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def f32(self) -> float:
        v = struct.unpack("<f", self.take(4))[0]
        return 0.0 if (math.isnan(v) or math.isinf(v)) else v

    def align(self, to: int) -> None:
        pad = (-self.pos) % to
        if pad:
            self.take(pad)


def parse(data: bytes) -> dict:
    """`{'actors': [...], 'layout': {...}}`.

    Each actor carries `nodeid`, `name_hash`, `prefab_hash`, `transform`
    (rotation/position/scale) and `parent_index`, matching what the scene
    extractor already consumes.
    """
    n_middle, n_bits = detect_layout(data)

    d_headers = Descriptor.at(data, 0)
    d_components = Descriptor.at(data, DESC64)
    d_nodeids = Descriptor.at(data, 2 * DESC64)
    d_names = Descriptor.at(data, 2 * DESC64 + DESC56)
    d_prefabs = Descriptor.at(data, 2 * DESC64 + 2 * DESC56)
    middle = [Descriptor.at(data, PROLOGUE + i * DESC56)
              for i in range(n_middle)]

    # ⛔ Do NOT take "the last three" as (transforms, numpooled, parents).
    # That holds on Echo VR and Lone Echo 2 but not on the LE2 trailer, which
    # has three middle descriptors and no separate `numpooled` at all.
    # Classify by ELEMENT SIZE (data_byte_size / count) instead, which is a
    # property of the array rather than of its position:
    #
    #   Summer  : 8, 48, 2, 2   -> transform is the 48
    #   trailer : 8, 32, 2      -> transform is the 32, and there is no numpooled
    #
    # 48 = quat(16) + pos(12) + scale(12) + pad(8);  32 = quat(16) + pos(12) +
    # pad(4), i.e. the same record without a scale channel.
    d_transforms = d_numpooled = d_parents = Descriptor()
    transform_stride = 0
    u16_descs = []
    for desc in middle:
        elem = (desc.data_byte_size // desc.count) if desc.count else 0
        if elem in (TRANSFORM_STRIDE_TRS, TRANSFORM_STRIDE_TR) and not transform_stride:
            d_transforms, transform_stride = desc, elem
        elif elem == 2:
            u16_descs.append(desc)
    if len(u16_descs) >= 2:
        d_numpooled, d_parents = u16_descs[-2], u16_descs[-1]
    elif u16_descs:
        d_parents = u16_descs[-1]

    r = _Reader(data)
    r.pos = PROLOGUE + n_middle * DESC56 + n_bits * DESC64

    for _ in range(d_headers.count):          # (type_hash, count) pairs
        r.u64(), r.u64()

    component_descs = []
    for _ in range(d_components.count):
        type_hash = r.u64()
        component_descs.append((type_hash, Descriptor.at(data, r.pos)))
        r.take(DESC56)
    for _type_hash, desc in component_descs:  # inline actor indices
        for _ in range(desc.count):
            r.u16()

    if d_nodeids.count:
        r.align(8)
    nodeids = [r.u64() for _ in range(d_nodeids.count)]
    names = [r.u64() for _ in range(d_names.count)]
    prefabs = [r.u64() for _ in range(d_prefabs.count)]

    for i in range(max(0, n_middle - 3)):     # unknown intermediate arrays
        if middle[i].count:
            r.take(middle[i].data_byte_size)

    if d_transforms.count:
        r.align(4)
    transforms = []
    for _ in range(d_transforms.count):
        rx, ry, rz, rw = r.f32(), r.f32(), r.f32(), r.f32()
        px, py, pz = r.f32(), r.f32(), r.f32()
        if transform_stride == TRANSFORM_STRIDE_TRS:
            sx, sy, sz = r.f32(), r.f32(), r.f32()
            r.take(8)
        else:
            sx = sy = sz = 1.0        # no scale channel in this build
            r.take(transform_stride - 28)
        transforms.append({
            "rotation": {"x": rx, "y": ry, "z": rz, "w": rw},
            "position": {"x": px, "y": py, "z": pz},
            "scale": {"x": sx, "y": sy, "z": sz},
        })

    numpooled = [r.u16() for _ in range(d_numpooled.count)]
    if d_parents.count:
        r.align(2)
    parents = [r.u16() for _ in range(d_parents.count)]

    actors = []
    for i, nodeid in enumerate(nodeids):
        actors.append({
            "index": i,
            "nodeid": nodeid,
            "nodeid_str": str(nodeid),
            "name_hash": names[i] if i < len(names) else 0,
            "prefab_hash": prefabs[i] if i < len(prefabs) else 0,
            "transform": transforms[i] if i < len(transforms) else None,
            "numpooled": numpooled[i] if i < len(numpooled) else 0,
            "parent_index": parents[i] if i < len(parents) else 0xFFFF,
        })
    return {"actors": actors,
            "layout": {"middle_descriptors": n_middle,
                       "bits_descriptors": n_bits,
                       "transform_stride": transform_stride},
            "count": len(actors)}


def parse_actor_data(data: bytes) -> dict:
    """Drop-in name-compatible with the vendored `level_reader`."""
    return parse(data)
