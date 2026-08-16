"""CGMaterialResourceWin10 -- SGMaterialData for Echo VR.

This is where texture ROLES come from.  `CGTextureStreamingResourceWin10` gives
an ordered texture inventory and nothing else; the material resource is what
says "this texture is `layer0_diffuse_map`, that one is `layer0_normal_map`".

## Layout, and how it differs from Lone Echo

Echo VR and Lone Echo are both NRadEngine, and `SGMaterialData` is structurally
the same object in both.  Two differences, and only two:

| | Lone Echo | Echo VR |
| --- | --- | --- |
| typed header | 56 B (carries `bakeemissivecolor`) | 40 B (**no** `bakeemissivecolor`) |
| descriptors | 5 | 6 (an extra trailing `CTable`) |
| payload starts | 352 (`0x160`) | 392 (`0x188`) |

Echo VR header::

    0x00  u64        materialfx        (CSymbol64, -1 = none)
    0x08  f32[4]     bakecolor
    0x18  u16        blendmode
    0x1A  u16        mattype
    0x1C  u32        flags
    0x20  f32        shadowfadedist
    0x24  u32        _pad
    0x28  ...        six descriptors, 352 B total
    0x188 ...        payload, in descriptor declaration order

Descriptor order and stride (a `CTable` is 56 B, a `CMap` is 56 + 8 = 64 B)::

    0x028  56  CTable<u32>            materialprops
    0x060  64  CMap<CSymbol64,u32>    materialpropoffsets
    0x0A0  56  CTable<CSymbol64>      uvsets
    0x0D8  64  CMap<...>              permutations
    0x118  56  CTable<SShaderInputData>  auxillaryinputs
    0x150  56  CTable<?>              trailing (unreversed)

`RadArrayDescriptor56` puts `capacity` at +40 and `count` at +48, and on disk
the two are equal.  Lone Echo's `le_mesh.material_scalars` reads the +40 slot
and calls it `iused`; this module reads the same slot for the same reason, and
cross-checks it against +48.

The header offsets above are *derived* from the reference parser
(`rad-archive-viewer/echomod/resources/cgmaterial_resource.py`, byte-identical
round-trip on 1713/1713 samples) and from Lone Echo's own constants, which agree
field-for-field once the 16-byte `bakeemissivecolor` is removed and the extra
descriptor added.  `probe_layout()` re-derives them from a real file so you can
confirm rather than trust.

## Why the texture binds are SCANNED, not indexed

`auxillaryinputs` sits sixth in the payload, behind `permutations` -- and the
per-element size of a `permutations` entry has never been reversed.  Without it
the byte offset of `auxillaryinputs` is not computable in the general case.

Rather than guess a stride, this module reuses the scanner Lone Echo already
proved on this exact struct (`le_shaderset_scan.scan_shaderset_slice`): walk the
payload at 8-byte strides looking for a u64 that is a KNOWN texture hash, back
up 8 bytes to the struct start, and validate the entry.  A hit is only accepted
when the surrounding fields are self-consistent, so the false-positive rate is
governed by the validators, not by a stride assumption.

`SShaderInputData` (0x20 bytes)::

    +0x00  u64  inputname       (CSymbol64 of the slot name -- THE ROLE)
    +0x08  u64  textureassetid  (CSymbol64 of the texture resource)
    +0x10  u16  type
    +0x12  u16  layer
    +0x14  u16  engineresource
    +0x16  u16  slot
    +0x18  f32  uscale
    +0x1c  f32  vscale

The scalar half (`materialprops` + `materialpropoffsets`) IS computable -- both
tables are first in the payload -- so those are read by offset, not scanned.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import Path

from evr_resource_types import MATERIAL_RESOURCE, normalise_hash, resource_path

# ---------------------------------------------------------------------------
# Layout constants
# ---------------------------------------------------------------------------
CTABLE_SIZE = 56
CMAP_SIZE = 64

#: Offset of the count slot within a descriptor.  Lone Echo reads +40
#: Field offsets inside a `RadArrayDescriptor56`.
#:
#: ⚠ `capacity` (+40) and `count` (+48) are NOT the same number in Echo VR, and
#: assuming they were is what made the first version of this module reject
#: 300/300 shipped materials.  Measured on a real extract::
#:
#:     materialprops        capacity=32   count=8
#:     uvsets               capacity=32   count=3
#:     auxillaryinputs      capacity=32   count=2
#:     materialpropoffsets  capacity=216  count=108
#:
#: `capacity` is allocation size -- 32 is the default inline block, and the CMap
#: over-allocates buckets against its load factor (216/108, 370/185, 182/91 are
#: exactly 2x; 170/107 and 124/74 are not, so it is a bucket count and not a
#: simple doubling).  `count` is what is actually used.
#:
#: Lone Echo's `material_scalars` reads +40 and calls it `iused`; that works
#: there because Lone Echo's shipped materials happen to have the two equal.
#: It is not a safe assumption here.
DESC_PDATA = 0
DESC_BYTESIZE = 8      # dataByteSize -- THE authoritative payload size
DESC_ALLOCATOR = 16
DESC_PAD = 24
DESC_FLAGS = 28
DESC_PBASE = 32
DESC_CAPACITY = 40
DESC_COUNT = 48

OFF_MATERIALFX = 0x00
OFF_BAKECOLOR = 0x08
OFF_BLENDMODE = 0x18
OFF_MATTYPE = 0x1A
OFF_FLAGS = 0x1C
OFF_SHADOWFADEDIST = 0x20

#: First descriptor starts here (immediately after the 40-byte typed header).
DESC_BASE = 0x28

#: Element sizes for the payload tables whose element type we know.
#:
#: ⚠ `materialprops` is a `CTable<u8>` -- the descriptor counts **BYTES**, not
#: f32 words.  A scalar is 4 bytes at a byte offset given by `materialpropoffsets`,
#: which is why `parse_material_prop_slots` divides the byte offset by 4.  The
#: reference parser says this outright ("CTable<u8> materialprops_desc, cnt * 1
#: byte payload") and the shipped data agrees: 556 bytes / capacity 556 = 1.
PROP_BYTE_SIZE = 1
PROP_WORD_SIZE = 4        # a scalar is still 4 bytes wide inside that buffer
PROPOFF_ENTRY_SIZE = 16   # key u64 @0, byteoffset u32 @8, pad @12
UVSET_ENTRY_SIZE = 8      # CSymbol64
SHADER_INPUT_SIZE = 0x20  # SShaderInputData

#: (name, descriptor stride, expected element size or None when unreversed)
#:
#: The expected size is a CROSS-CHECK, not the source of truth.  Every descriptor
#: describes its own element size as `dataByteSize / capacity`, so the parser
#: derives it per file and only warns when a derived size contradicts a known
#: one.  That is what makes `permutations` and `trailing` -- whose element types
#: nobody has reversed -- skippable rather than blocking.
DESCRIPTORS: tuple[tuple[str, int, "int | None"], ...] = (
    ("materialprops", CTABLE_SIZE, PROP_BYTE_SIZE),
    ("materialpropoffsets", CMAP_SIZE, PROPOFF_ENTRY_SIZE),
    ("uvsets", CTABLE_SIZE, UVSET_ENTRY_SIZE),
    ("permutations", CMAP_SIZE, None),
    ("auxillaryinputs", CTABLE_SIZE, SHADER_INPUT_SIZE),
    ("trailing", CTABLE_SIZE, None),
)

#: Byte offset of each descriptor's START, derived not transcribed.
DESC_STARTS: dict[str, int] = {}
_cursor = DESC_BASE
for _name, _stride, _elem in DESCRIPTORS:
    DESC_STARTS[_name] = _cursor
    _cursor += _stride
HEADER_SIZE = _cursor          # 0x188 = 392, where the payload begins
del _cursor, _name, _stride, _elem

#: Byte offset of each descriptor's `count` slot. Kept for tests and diagnostics.
DESC_OFFSETS: dict[str, int] = {
    name: start + DESC_COUNT for name, start in DESC_STARTS.items()
}

MAX_REASONABLE = 10_000   # matches le_mesh.material_scalars

#: The undescribed block between the descriptor block and the first table:
#: FOUR u64s, 32 bytes.  On a shipped material it reads::
#:
#:     u64 0 | u64 <CSymbol64> | u64 <u32 0, u32 ?> | u64 -1
#:
#: ⚠ Solving for this from the residual gives 32 **or** 36, and 36 is wrong.
#: The extra four bytes are TAIL padding, not preamble: a file is padded up to
#: an 8-byte boundary, so a material whose tables total `4 (mod 8)` bytes ends
#: with four spare bytes.  Attributing those to the preamble shifts every
#: `materialprops` word by one index -- the scalars still look plausible
#: (they are word-aligned either way), which is exactly why this is worth
#: pinning rather than eyeballing.
PREAMBLE_SIZE = 32

#: Every table STARTS on an 8-byte boundary.  Only `materialprops` normally
#: needs the padding, because it is the one table whose byte count is arbitrary
#: (a `CTable<u8>`); the rest have 8- or 16-byte elements and are aligned
#: already.
#:
#: This is what produced the 32/36 "preamble" split: a material whose
#: `materialprops` is `4 (mod 8)` bytes long gets four bytes of padding after
#: it, and a solver that blames the preamble for the residual reports 36.
#: `materialpropoffsets` has a 16-byte stride, so starting it four bytes early
#: does not degrade gracefully -- every entry decodes to garbage, which is why
#: the in-range check caught this immediately.
TABLE_ALIGNMENT = 8

# `SShaderInputData` field offsets
IN_INPUTNAME = 0x00
IN_TEXTUREASSETID = 0x08
IN_TYPE = 0x10
IN_LAYER = 0x12
IN_ENGINERESOURCE = 0x14
IN_SLOT = 0x16
IN_USCALE = 0x18
IN_VSCALE = 0x1C

#: Validation bounds for a candidate bind.  Deliberately loose: these exist to
#: reject random bytes that happen to sit next to a texture hash, not to encode
#: authoring policy.  Tightening them silently DROPS real binds, so any change
#: here should be justified against a corpus, not intuition.
MAX_SLOT = 64
MAX_LAYER = 16
MAX_TYPE = 256
MAX_ENGINERESOURCE = 256


class MaterialParseError(ValueError):
    """The bytes do not match the Echo VR SGMaterialData layout."""


# ---------------------------------------------------------------------------
# Primitive readers
# ---------------------------------------------------------------------------

def _u16(data: bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def _f32(data: bytes, offset: int) -> float:
    return struct.unpack_from("<f", data, offset)[0]


def _f32_from_u32(word: int) -> float:
    return struct.unpack("<f", struct.pack("<I", word))[0]


def _is_plausible_scale(value: float) -> bool:
    """A UV scale the engine could actually have authored.

    Same test Lone Echo's shaderset scanner applies: finite, non-zero, and
    within a few orders of magnitude of 1.  Random bytes clear this rarely.
    """
    if not math.isfinite(value):
        return False
    magnitude = abs(value)
    return magnitude == 0.0 or (1e-4 <= magnitude <= 1e4)


# ---------------------------------------------------------------------------
# Descriptor / header
# ---------------------------------------------------------------------------

@dataclass
class MaterialHeader:
    """The typed header plus every descriptor's size, count and capacity."""

    materialfx: str = ""
    bakecolor: list = field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])
    blendmode: int = 0
    mattype: int = 0
    flags: int = 0
    shadowfadedist: float = -1.0
    #: `{name -> element count}` -- from `dataByteSize / element_size` where the
    #: element type is known, else the descriptor's own `count`.
    counts: dict = field(default_factory=dict)
    #: `{name -> dataByteSize}`. This is what the payload walk uses.
    byte_sizes: dict = field(default_factory=dict)
    #: `{name -> capacity}`, allocation size in elements.
    capacities: dict = field(default_factory=dict)
    #: `{name -> element size}`, derived per file as `dataByteSize / capacity`.
    element_sizes: dict = field(default_factory=dict)
    #: `{name -> bytes actually on disk}` = `count * element_size`.
    used_sizes: dict = field(default_factory=dict)
    #: Undescribed bytes between the descriptor block and the first table.
    #: Always `PREAMBLE_SIZE`; kept as a field so diagnostics can print it.
    preamble: int = PREAMBLE_SIZE
    #: File-alignment slack after the last table. Must be `0..7`.
    tail_padding: int = 0
    #: `{name -> padding bytes inserted before it}` to reach an 8-byte boundary.
    alignment_padding: dict = field(default_factory=dict)
    #: `{name -> count}` exactly as stored, before any element-size derivation.
    raw_counts: dict = field(default_factory=dict)
    #: Tables whose `dataByteSize` is not a whole number of elements. THIS is a
    #: real layout error -- it means the element size or the field offset is
    #: wrong -- unlike a capacity/count difference, which is normal.
    size_mismatches: list = field(default_factory=list)
    #: `{name -> byte offset}` of each table in the payload.
    payload_offsets: dict = field(default_factory=dict)
    #: Total bytes the descriptors claim, i.e. the expected file size.
    payload_end: int = HEADER_SIZE


def parse_header(data: bytes) -> MaterialHeader:
    """Read the typed header and every descriptor, sizing tables by `dataByteSize`.

    Element counts are DERIVED from `dataByteSize` rather than read from the
    `count` slot wherever the element type is known, and the two are
    cross-checked.  That ordering matters: `dataByteSize` is the field the
    payload walk has to agree with, so it is the one that must be authoritative.
    """
    if len(data) < HEADER_SIZE:
        raise MaterialParseError(
            f"file is {len(data)}B, need at least {HEADER_SIZE}B for the header"
        )

    header = MaterialHeader(
        materialfx=normalise_hash(_u64(data, OFF_MATERIALFX)),
        bakecolor=list(struct.unpack_from("<4f", data, OFF_BAKECOLOR)),
        blendmode=_u16(data, OFF_BLENDMODE),
        mattype=_u16(data, OFF_MATTYPE),
        flags=_u32(data, OFF_FLAGS),
        shadowfadedist=_f32(data, OFF_SHADOWFADEDIST),
    )

    for name, _stride, expected_element in DESCRIPTORS:
        start = DESC_STARTS[name]
        byte_size = _u64(data, start + DESC_BYTESIZE)
        capacity = _u64(data, start + DESC_CAPACITY)
        count = _u64(data, start + DESC_COUNT)

        if count > MAX_REASONABLE or capacity > MAX_REASONABLE:
            raise MaterialParseError(
                f"{name}: count={count} capacity={capacity}, both bounded by "
                f"{MAX_REASONABLE} -- not an Echo VR SGMaterialData blob"
            )
        if count > capacity:
            raise MaterialParseError(
                f"{name}: count {count} exceeds capacity {capacity} -- "
                f"the descriptor fields are not where this parser thinks"
            )

        # `dataByteSize` is the ALLOCATED buffer -- capacity * element_size, not
        # count * element_size. Measured on shipped data:
        #   materialpropoffsets  3456 / capacity 216 = 16   (count was 108)
        #   uvsets                256 / capacity  32 =  8   (count was   1)
        #   auxillaryinputs      1024 / capacity  32 = 32   (count was   2)
        # So the descriptor DESCRIBES ITS OWN ELEMENT SIZE, and the bytes that
        # are actually on disk are `count * element_size`. Deriving it per file
        # beats hard-coding it: it works for `permutations` and `trailing` too.
        element_size = 0
        if capacity:
            element_size, remainder = divmod(byte_size, capacity)
            if remainder:
                header.size_mismatches.append(
                    f"{name}: dataByteSize {byte_size} is not a whole multiple "
                    f"of capacity {capacity} -- cannot derive an element size"
                )
        if expected_element and element_size and element_size != expected_element:
            header.size_mismatches.append(
                f"{name}: derived element size {element_size} contradicts the "
                f"expected {expected_element}"
            )

        header.byte_sizes[name] = byte_size
        header.capacities[name] = capacity
        header.raw_counts[name] = count
        header.element_sizes[name] = element_size
        header.counts[name] = count
        header.used_sizes[name] = count * element_size

    # --- walk the payload, aligning each table ------------------------------
    header.preamble = PREAMBLE_SIZE
    cursor = HEADER_SIZE + PREAMBLE_SIZE
    for name, _stride, _expected in DESCRIPTORS:
        remainder = cursor % TABLE_ALIGNMENT
        if remainder:
            cursor += TABLE_ALIGNMENT - remainder
            header.alignment_padding[name] = TABLE_ALIGNMENT - remainder
        header.payload_offsets[name] = cursor
        cursor += header.used_sizes[name]

    header.payload_end = cursor
    header.tail_padding = len(data) - cursor

    # The real check, and not one this walk can satisfy by construction: with
    # every table sized and aligned, the last one has to land on the end of the
    # file. A wrong element size overshoots or undershoots by far more than 7.
    if not 0 <= header.tail_padding < TABLE_ALIGNMENT:
        header.size_mismatches.append(
            f"tables end at {cursor} but the file is {len(data)}B "
            f"({header.tail_padding:+d}) -- expected 0..{TABLE_ALIGNMENT - 1} "
            f"bytes of slack, so a table size or the preamble is wrong"
        )
    return header


# ---------------------------------------------------------------------------
# Scalars -- computable by offset
# ---------------------------------------------------------------------------

def parse_material_prop_slots(data: bytes) -> tuple[list[float], dict[int, int]]:
    """`(materialprops words as f32, {property_name_hash -> word index})`.

    Mirrors `le_mesh.material_scalars.parse_material_prop_slots` exactly, with
    Echo VR's header size.  Returns `([], {})` on short or malformed input
    rather than raising, matching the Lone Echo contract callers already expect.
    """
    try:
        header = parse_header(data)
    except MaterialParseError:
        return [], {}

    # `materialprops` counts BYTES (CTable<u8>), so the number of f32 slots is
    # that byte count over 4. Treating the count as a word count would read four
    # times too far and walk off the end of the table.
    prop_bytes = header.counts["materialprops"] * (
        header.element_sizes.get("materialprops") or PROP_BYTE_SIZE)
    n_words = prop_bytes // PROP_WORD_SIZE
    n_propoff = header.counts["materialpropoffsets"]

    props_off = header.payload_offsets["materialprops"]
    propoff_off = header.payload_offsets["materialpropoffsets"]
    if propoff_off + n_propoff * PROPOFF_ENTRY_SIZE > len(data):
        return [], {}
    if props_off + n_words * PROP_WORD_SIZE > len(data):
        return [], {}

    words = [
        _f32_from_u32(_u32(data, props_off + i * PROP_WORD_SIZE))
        for i in range(n_words)
    ]

    slots: dict[int, int] = {}
    for i in range(n_propoff):
        entry = propoff_off + i * PROPOFF_ENTRY_SIZE
        key_hash = _u64(data, entry)
        byteoffset = _u32(data, entry + 8)
        # byteoffset indexes the u32 word array in BYTES; tolerate a raw index
        # encoding as a fallback, exactly as the Lone Echo reader does.
        if byteoffset % 4 == 0 and (byteoffset // 4) < len(words):
            index = byteoffset // 4
        elif byteoffset < len(words):
            index = byteoffset
        else:
            continue
        slots[key_hash] = index
    return words, slots


def payload_offset(name: str, header: MaterialHeader) -> int | None:
    """Byte offset of a payload table.

    Every table is now locatable, including `auxillaryinputs`.  The earlier
    version of this function returned None past `permutations`, because it tried
    to reconstruct offsets from element counts and a `permutations` element's
    size is unreversed.  Walking `dataByteSize` sidesteps that entirely -- the
    descriptor says how many bytes to skip whether or not we know what is in
    them.
    """
    return header.payload_offsets.get(name)


def parse_uvsets(data: bytes, header: MaterialHeader | None = None) -> list[str]:
    """The `uvsets` CTable<CSymbol64>, or `[]` if it cannot be located."""
    header = header or parse_header(data)
    offset = payload_offset("uvsets", header)
    count = header.counts["uvsets"]
    if offset is None or offset + count * UVSET_ENTRY_SIZE > len(data):
        return []
    return [
        normalise_hash(_u64(data, offset + i * UVSET_ENTRY_SIZE))
        for i in range(count)
    ]


def read_texture_binds(data: bytes,
                       header: MaterialHeader | None = None) -> list["TextureBind"]:
    """Read `auxillaryinputs` DIRECTLY, by offset -- no scanning, no anchor.

    This is strictly better than `scan_texture_binds` when the header parses:
    it needs no set of known textures, it cannot miss a bind whose texture is
    absent from the extract, and it cannot invent one from a coincidental hash
    match.  `decode()` prefers it and falls back to the scan only when this
    returns nothing.

    Entries are returned in declaration order and are NOT validated against the
    plausibility bounds the scanner uses -- those exist to reject coincidences,
    and there are none to reject when the offset is known.
    """
    header = header or parse_header(data)
    offset = header.payload_offsets.get("auxillaryinputs")
    count = header.counts.get("auxillaryinputs", 0)
    if offset is None or not count:
        return []
    if offset + count * SHADER_INPUT_SIZE > len(data):
        return []

    binds = []
    for i in range(count):
        start = offset + i * SHADER_INPUT_SIZE
        inputname = _u64(data, start + IN_INPUTNAME)
        if inputname == 0 or inputname == 0xFFFFFFFFFFFFFFFF:
            continue
        binds.append(TextureBind(
            inputname_hash=normalise_hash(inputname),
            textureassetid_hash=normalise_hash(_u64(data, start + IN_TEXTUREASSETID)),
            type=_u16(data, start + IN_TYPE),
            layer=_u16(data, start + IN_LAYER),
            engineresource=_u16(data, start + IN_ENGINERESOURCE),
            slot=_u16(data, start + IN_SLOT),
            uscale=_f32(data, start + IN_USCALE),
            vscale=_f32(data, start + IN_VSCALE),
            offset=start,
        ))
    return binds


# ---------------------------------------------------------------------------
# Texture binds -- scanned
# ---------------------------------------------------------------------------

@dataclass
class TextureBind:
    """One `SShaderInputData` entry.

    Field names match `le_shaderset_scan.ShaderTexRow` where they overlap, so
    these objects can be handed straight to
    `le_mesh.materials.roles_from_input_rows`.
    """

    inputname_hash: str = ""
    textureassetid_hash: str = ""
    type: int = 0
    layer: int = 0
    engineresource: int = 0
    slot: int = 0
    uscale: float = 1.0
    vscale: float = 1.0
    #: Byte offset the entry was found at -- kept for diagnostics.
    offset: int = 0
    #: Present so `roles_from_input_rows`' scanner-artefact filter can run.
    shaderset_hash: str = ""


def _read_bind(data: bytes, start: int, floor: int | None = None) -> TextureBind | None:
    """Validate and read a candidate `SShaderInputData` at `start`.

    Returns None when any field is out of range.  Every rejection here is a
    guard against a coincidental texture-hash match, so the checks are ordered
    cheapest-first.

    `start` must be at or after `HEADER_SIZE`: the descriptor block cannot
    contain a bind, so a texture hash appearing within 8 bytes of the payload
    boundary is a coincidence, not an entry that straddles it.
    """
    if floor is None:
        floor = HEADER_SIZE
    if start < floor or start + SHADER_INPUT_SIZE > len(data):
        return None

    inputname = _u64(data, start + IN_INPUTNAME)
    if inputname == 0 or inputname == 0xFFFFFFFFFFFFFFFF:
        return None

    slot = _u16(data, start + IN_SLOT)
    layer = _u16(data, start + IN_LAYER)
    type_ = _u16(data, start + IN_TYPE)
    engineresource = _u16(data, start + IN_ENGINERESOURCE)
    if slot >= MAX_SLOT or layer >= MAX_LAYER:
        return None
    if type_ >= MAX_TYPE or engineresource >= MAX_ENGINERESOURCE:
        return None

    uscale = _f32(data, start + IN_USCALE)
    vscale = _f32(data, start + IN_VSCALE)
    if not (_is_plausible_scale(uscale) and _is_plausible_scale(vscale)):
        return None

    return TextureBind(
        inputname_hash=normalise_hash(inputname),
        textureassetid_hash=normalise_hash(_u64(data, start + IN_TEXTUREASSETID)),
        type=type_,
        layer=layer,
        engineresource=engineresource,
        slot=slot,
        uscale=uscale,
        vscale=vscale,
        offset=start,
    )


def scan_texture_binds(data: bytes, known_textures: set[str],
                       floor: int | None = None) -> list[TextureBind]:
    """Find every `SShaderInputData` whose texture is in `known_textures`.

    `known_textures` is the anchor that makes this reliable -- pass the model's
    streaming-resource texture list, or the set of every `cgtextureresourceWin10`
    in the extract.  An EMPTY set finds nothing; that is intentional, because an
    unanchored scan over this struct produces mostly noise.

    Scans from the payload only (offsets below `HEADER_SIZE` are descriptors and
    cannot contain binds), at both 8-byte and 4-byte-offset alignments, matching
    the two-pass approach `le_scene_materials.scan_shaderset_roles` uses.
    """
    if not known_textures:
        return []

    wanted = {normalise_hash(t) for t in known_textures if t}
    found: dict[int, TextureBind] = {}

    # A bind's struct must start at or after `floor`, and its texture sits 8
    # bytes into the struct -- so the earliest a texture hash can legitimately
    # appear is `floor + 8`. For a material that floor is HEADER_SIZE (the
    # descriptor block cannot hold binds); for a shader set, pass 0.
    if floor is None:
        floor = HEADER_SIZE
    start_floor = min(floor + IN_TEXTUREASSETID, len(data))
    for alignment in (0, 4):
        position = start_floor + alignment
        while position + 8 <= len(data):
            if normalise_hash(_u64(data, position)) in wanted:
                # The texture sits at +0x08, so the struct starts 8 bytes back.
                bind = _read_bind(data, position - IN_TEXTUREASSETID, floor=floor)
                if bind is not None and bind.offset not in found:
                    found[bind.offset] = bind
            position += 8

    return [found[key] for key in sorted(found)]


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------

@dataclass
class MaterialResource:
    """A fully decoded CGMaterialResourceWin10."""

    material_hash: str = ""
    header: MaterialHeader = field(default_factory=MaterialHeader)
    #: `materialprops` words reinterpreted as f32.
    words: list = field(default_factory=list)
    #: `{property_name_hash -> word index}`.
    slots: dict = field(default_factory=dict)
    binds: list = field(default_factory=list)
    uvsets: list = field(default_factory=list)
    #: How `binds` was obtained: `offset` (exact), `scan` (anchored), or `none`.
    bind_source: str = "none"
    #: Non-fatal problems worth surfacing to the caller.
    warnings: list = field(default_factory=list)

    @property
    def props(self) -> dict:
        """`{property_name_hash -> float}` for single-word scalars."""
        return {h: self.words[i] for h, i in self.slots.items()
                if i < len(self.words)}

    def role_textures(self, names: dict | None = None) -> dict:
        """`{role_key -> texture_hash}` via Lone Echo's role resolver.

        Requires `blender_tool` on `sys.path`.  The resolver order is
        `ROLE_BY_INPUTNAME` (cracked preimages + the forward-hashed layer grid),
        then the caller's `hash_lookup.json` names, then `unknown_s{slot}`.
        """
        from le_mesh import materials as le_materials

        return le_materials.roles_from_input_rows(self.binds, names or {})


def decode(data: bytes, *, material_hash="", known_textures=None) -> MaterialResource:
    """Decode one material blob.

    `known_textures` anchors the bind scan; without it `binds` is empty and only
    the scalar half is populated.
    """
    header = parse_header(data)
    words, slots = parse_material_prop_slots(data)

    # Direct read first -- it is exact. The anchored scan is the fallback for a
    # file whose descriptors do not add up, where an offset cannot be trusted
    # but a texture-hash match still can.
    binds = read_texture_binds(data, header)
    bind_source = "offset"
    if not binds:
        binds = scan_texture_binds(data, set(known_textures or ()))
        bind_source = "scan" if binds else "none"

    res = MaterialResource(
        material_hash=normalise_hash(material_hash),
        header=header,
        words=words,
        slots=slots,
        uvsets=parse_uvsets(data, header),
        binds=binds,
        bind_source=bind_source,
    )

    # A capacity/count difference is NORMAL and is deliberately not a warning.
    # A dataByteSize that is not a whole number of elements is not.
    res.warnings.extend(header.size_mismatches)

    if header.payload_end != len(data):
        res.warnings.append(
            f"descriptors account for {header.payload_end}B but the file is "
            f"{len(data)}B (delta {len(data) - header.payload_end:+d}) -- a "
            f"table's element size or the descriptor order may be wrong"
        )
    if header.counts["materialprops"] and not words:
        res.warnings.append(
            f"materialprops declares {header.counts['materialprops']} words but "
            f"the payload could not be read -- scalars fell back to defaults"
        )
    # Compare against the file's OWN count, not the size-derived one -- when the
    # scan is in use it is precisely because the size field was not trustworthy.
    declared = header.raw_counts.get("auxillaryinputs", 0)
    if declared and len(res.binds) != declared and bind_source == "scan":
        res.warnings.append(
            f"auxillaryinputs declares {declared} entries, the anchored scan "
            f"found {len(res.binds)} -- textures absent from `known_textures` "
            f"are invisible to the scan"
        )
    return res


def load(root: Path, material_hash, known_textures=None) -> MaterialResource | None:
    """Read and decode one material from a flat extract; None when absent."""
    path = resource_path(root, MATERIAL_RESOURCE, material_hash)
    if path is None:
        return None
    return decode(path.read_bytes(), material_hash=material_hash,
                  known_textures=known_textures)


def all_material_hashes(root: Path) -> set[str]:
    """Every CGMaterialResourceWin10 present in the extract."""
    directory = Path(root) / MATERIAL_RESOURCE
    if not directory.is_dir():
        return set()
    return {
        normalise_hash(p.stem if p.suffix == ".bin" else p.name)
        for p in directory.iterdir() if p.is_file()
    }


def all_texture_hashes(root: Path) -> set[str]:
    """Every cgtextureresourceWin10 present -- the widest sane scan anchor."""
    from evr_resource_types import TEXTURE_RESOURCE

    directory = Path(root) / TEXTURE_RESOURCE
    if not directory.is_dir():
        return set()
    return {
        normalise_hash(p.stem if p.suffix == ".bin" else p.name)
        for p in directory.iterdir() if p.is_file()
    }


# ---------------------------------------------------------------------------
# Layout probe -- re-derive the constants instead of trusting them
# ---------------------------------------------------------------------------

def probe_layout(data: bytes) -> dict:
    """Report whether this file is consistent with the assumed layout.

    Checks the three things that would break first if the header size or the
    descriptor order were wrong:

    1. every descriptor's +40 and +48 slots agree and are sane;
    2. `materialprops` + `materialpropoffsets` fit inside the file;
    3. every `materialpropoffsets` byteoffset lands inside the word array.

    Run this across a sample of YOUR extract before trusting any material
    output.  A file that fails (3) in particular means the payload does not
    start where this module thinks it does.
    """
    report: dict = {"size": len(data), "ok": False, "problems": []}

    try:
        header = parse_header(data)
    except MaterialParseError as exc:
        report["problems"].append(str(exc))
        return report

    report["counts"] = dict(header.counts)
    report["capacities"] = dict(header.capacities)
    report["byte_sizes"] = dict(header.byte_sizes)
    report["mattype"] = header.mattype
    report["blendmode"] = header.blendmode
    report["payload_start"] = HEADER_SIZE + header.preamble
    report["payload_end"] = header.payload_end
    report["preamble"] = header.preamble
    report["tail_padding"] = header.tail_padding
    report["element_sizes"] = dict(header.element_sizes)

    # A capacity/count difference is normal and is NOT reported as a problem;
    # a tail slack outside 0..7 is, and `parse_header` has already said so.
    report["problems"].extend(header.size_mismatches)

    prop_bytes = header.counts["materialprops"] * (
        header.element_sizes.get("materialprops") or PROP_BYTE_SIZE)
    n_props = prop_bytes // PROP_WORD_SIZE
    n_propoff = header.counts["materialpropoffsets"]
    props_off = header.payload_offsets["materialprops"]
    propoff_off = header.payload_offsets["materialpropoffsets"]

    if propoff_off + n_propoff * PROPOFF_ENTRY_SIZE > len(data):
        report["problems"].append(
            f"materialpropoffsets runs to "
            f"{propoff_off + n_propoff * PROPOFF_ENTRY_SIZE}B past a "
            f"{len(data)}B file"
        )
        return report

    # (2) Semantic check: every property offset must land inside the word array.
    in_range = 0
    for i in range(n_propoff):
        entry = propoff_off + i * PROPOFF_ENTRY_SIZE
        byteoffset = _u32(data, entry + 8)
        if byteoffset % 4 == 0 and byteoffset // 4 < n_props:
            in_range += 1
    report["propoffsets_in_range"] = in_range
    report["propoffsets_total"] = n_propoff

    if n_propoff and in_range != n_propoff:
        report["problems"].append(
            f"{n_propoff - in_range}/{n_propoff} materialpropoffsets point "
            f"outside the {n_props}-word materialprops array -- the payload "
            f"probably does not start at {HEADER_SIZE}"
        )

    # (3) Are the binds directly readable? This is what the whole module is for.
    report["binds_by_offset"] = len(read_texture_binds(data, header))
    report["binds_declared"] = header.counts.get("auxillaryinputs", 0)

    report["ok"] = not report["problems"]
    return report


def dump_descriptors(data: bytes) -> str:
    """Print all eight u64 fields of every descriptor, raw and unassigned.

    Use this when the payload arithmetic does not close.  It makes no
    assumptions beyond `DESC_BASE` and the descriptor strides, so it can show
    that a field is not where this module thinks it is -- which no amount of
    further inference can.
    """
    lines = [f"file size {len(data)}   payload starts at {HEADER_SIZE}"]
    lines.append(f"{'table':<22} " + " ".join(f"+{o:<8}" for o in range(0, 56, 8)))

    for name, _stride, _elem in DESCRIPTORS:
        start = DESC_STARTS[name]
        fields = [_u64(data, start + off) for off in range(0, 56, 8)]
        lines.append(f"{name:<22} " + " ".join(f"{v:<9}" for v in fields))

    lines.append("")
    try:
        header = parse_header(data)
    except MaterialParseError as exc:
        lines.append(f"parse_header failed: {exc}")
        return "\n".join(lines)

    lines.append(f"preamble {header.preamble}B (tables start at "
                 f"{HEADER_SIZE + header.preamble}), "
                 f"tail padding {header.tail_padding}B")
    lines.append("")
    for name, _stride, _elem in DESCRIPTORS:
        pad = header.alignment_padding.get(name, 0)
        lines.append(
            f"  {name:<22} bytes={header.byte_sizes[name]:<7} "
            f"cap={header.capacities[name]:<6} count={header.counts[name]:<6} "
            f"elem={header.element_sizes[name]:<4} "
            f"used={header.used_sizes[name]:<7} "
            f"pad={pad} @{header.payload_offsets[name]}"
        )
    lines.append(f"  {'TOTAL':<22} tables end at {header.payload_end}, "
                 f"file is {len(data)} "
                 f"(slack {len(data) - header.payload_end:+d}, must be 0..7)")

    lines.append("")
    lines.append(f"preamble bytes = "
                 f"{data[HEADER_SIZE:HEADER_SIZE + header.preamble].hex(' ', 4)}")

    # THE decisive check. Both a 32- and a 36-byte preamble keep the words
    # 4-byte aligned, so "the floats look sane" cannot tell them apart -- an
    # off-by-one-word read still yields clean 0.0/1.0 values. Naming the
    # parameters can: `k_alpha` is authored 1.0, `k_alpha_threshold` 0.5, and a
    # one-word shift lands those names on a neighbour's value.
    words, slots = parse_material_prop_slots(data)
    lines.append(f"materialprops: {len(words)} f32 slots, "
                 f"{len(slots)} named offsets")
    try:
        from le_mesh import material_scalars as msc
        name_table = msc.build_name_table()
    except Exception:
        name_table = {}

    named = [(name_table.get(h), h, i) for h, i in sorted(slots.items())]
    known = [row for row in named if row[0]]
    lines.append(f"  {len(known)} of {len(slots)} offsets have a cracked name")
    for label, name_hash, index in (known or named)[:12]:
        value = words[index] if index < len(words) else float("nan")
        lines.append(f"    {label or f'{name_hash:016x}'}"
                     f" -> word[{index}] = {value!r}")

    binds = read_texture_binds(data, header)
    lines.append(f"auxillaryinputs: {len(binds)} bind(s) read by offset")
    for bind in binds[:8]:
        lines.append(f"    slot={bind.slot} layer={bind.layer} "
                     f"input={bind.inputname_hash} tex={bind.textureassetid_hash} "
                     f"uv=({bind.uscale:g},{bind.vscale:g})")
    return "\n".join(lines)


def audit(root: Path, limit: int | None = None) -> dict:
    """Probe every material in the extract. Read-only."""
    directory = Path(root) / MATERIAL_RESOURCE
    if not directory.is_dir():
        return {"error": f"no such directory: {directory}"}

    files = sorted(p for p in directory.iterdir() if p.is_file())
    if limit:
        files = files[:limit]

    ok = 0
    problems: list[str] = []
    mattypes: dict[int, int] = {}
    deltas: dict[int, int] = {}
    binds_total = 0
    with_binds = 0
    first_failure: dict = {}

    for path in files:
        data = path.read_bytes()
        report = probe_layout(data)

        # Tail padding must be 0..7. Anything else means a table size is wrong,
        # and unlike the old delta this cannot be satisfied by construction.
        delta = report.get("tail_padding", 0)
        deltas[delta] = deltas.get(delta, 0) + 1

        if report["ok"]:
            ok += 1
            mattype = report.get("mattype", 0)
            mattypes[mattype] = mattypes.get(mattype, 0) + 1
            found = report.get("binds_by_offset", 0)
            binds_total += found
            if found:
                with_binds += 1
        else:
            problems.append(f"{path.name}: {'; '.join(report['problems'][:2])}")
            if not first_failure:
                first_failure = {
                    "file": path.name,
                    "size": len(data),
                    "counts": report.get("counts"),
                    "capacities": report.get("capacities"),
                    "byte_sizes": report.get("byte_sizes"),
                    "payload_end": report.get("payload_end"),
                    "problems": report.get("problems"),
                }

    return {
        "files": len(files),
        "layout_ok": ok,
        "layout_failed": len(files) - ok,
        # Every entry must be 0..7. A key of 8 or more is a wrong table size.
        "tail_padding_histogram": dict(sorted(deltas.items())[:12]),
        "mattype_histogram": dict(sorted(mattypes.items())),
        "materials_with_binds": with_binds,
        "total_binds": binds_total,
        "first_failure": first_failure,
        "problems": problems[:10],
    }


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="flat Echo VR extract root")
    ap.add_argument("--material", help="decode and dump one material hash")
    ap.add_argument("--dump", help="raw descriptor dump for one material hash")
    ap.add_argument("--limit", type=int, default=None, help="cap the audit")
    args = ap.parse_args()

    if args.dump:
        path = resource_path(args.root, MATERIAL_RESOURCE, args.dump)
        if path is None:
            print(f"no material {args.dump}")
            raise SystemExit(1)
        print(dump_descriptors(path.read_bytes()))
    elif args.material:
        textures = all_texture_hashes(args.root)
        print(f"anchor set: {len(textures)} texture resources")
        material = load(args.root, args.material, known_textures=textures)
        if material is None:
            print(f"no material {args.material}")
            raise SystemExit(1)
        print(f"mattype={material.header.mattype} "
              f"blendmode={material.header.blendmode} "
              f"flags=0x{material.header.flags:08x}")
        print(f"counts: {material.header.counts}")
        print(f"binds: {len(material.binds)}")
        for bind in material.binds:
            print(f"  slot={bind.slot:2d} layer={bind.layer} "
                  f"inputname={bind.inputname_hash} tex={bind.textureassetid_hash} "
                  f"uv=({bind.uscale:g},{bind.vscale:g})")
        for warning in material.warnings:
            print(f"  WARN {warning}")
    else:
        print(json.dumps(audit(args.root, args.limit), indent=2))
