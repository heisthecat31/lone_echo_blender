"""CGMaterialResourceWin10 -- header arithmetic, scalars, and the bind scan.

Synthetic buffers only; no game data, no Oodle, no Blender, no pytest.

Two things are worth stating about what these tests do and do not prove:

* The **derived offsets** are checked against the numbers the reference parser
  and Lone Echo's own constants independently imply. That is a consistency
  check across two sources, which is stronger than re-typing one of them, but it
  is still not a read of a shipped Echo VR material.
* The **bind scanner** is checked for both recall and rejection. The rejection
  tests matter more: an anchored scan that accepts too much silently invents
  texture bindings, which is worse than finding none.
"""

import struct
import sys
from pathlib import Path

# `run_tests.py` adds `blender_tool/` and `tests/` to sys.path but does NOT read
# conftest.py -- that is a pytest mechanism. The Echo VR decoders live in
# `scripts/`, so each test module bootstraps that path itself and works under
# both runners.
_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import evr_material_resource as emr


def _approx(a, b, eps=1e-6):
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(abs(x - y) <= eps for x, y in zip(a, b))
    if isinstance(a, dict):
        return set(a) == set(b) and all(abs(a[k] - b[k]) <= eps for k in a)
    return abs(a - b) <= eps


def _raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as caught:
        return caught
    raise AssertionError(f"expected {exc.__name__}, nothing was raised")


# --- fixtures ---------------------------------------------------------------

#: Shipped materials over-allocate: `capacity` is 32 (the default inline block)
#: or a CMap bucket count, while `count` is what is used. The fixtures bake in a
#: DIFFERENT capacity on purpose, so any code that reads +40 instead of the
#: `dataByteSize` at +8 fails these tests immediately.
FIXTURE_CAPACITY = 32

#: One `CMap<CSymbol64, CSymbol64>` entry in the sixth container: slot hash
#: then texture hash. Matches `evr_material_textures.CONTAINERS[5]`'s stride.
SLOT_TEXTURE_SIZE = 16

#: One `permutations` entry: `CMap<unsigned int, unsigned int>`.
PERM_ENTRY_SIZE = 8


def _descriptor(count, *, element_size, stride=56, capacity=FIXTURE_CAPACITY):
    """A RadArrayDescriptor: dataByteSize at +8, capacity at +40, count at +48.

    `dataByteSize` is `capacity * element_size` -- the ALLOCATED buffer, not the
    used portion. That is what shipped data does, and getting it wrong is what
    made the payload walk overshoot by thousands of bytes.
    """
    capacity = max(capacity, count)
    buf = bytearray(stride)
    struct.pack_into("<Q", buf, emr.DESC_BYTESIZE, capacity * element_size)
    struct.pack_into("<Q", buf, emr.DESC_CAPACITY, capacity)
    struct.pack_into("<Q", buf, emr.DESC_COUNT, count)
    return bytes(buf)


def _material(*, props=(), propoffsets=(), uvsets=(), aux_blob=b"",
              mattype=1, blendmode=0, flags=0, bakecolor=(1.0, 1.0, 1.0, 1.0),
              materialfx=0xFFFFFFFFFFFFFFFF, perms=b"", trailing=b"",
              slot_textures=()):
    """Build a synthetic Echo VR SGMaterialData blob.

    `perms` is raw bytes, because its element type is unreversed -- which is
    exactly the situation `dataByteSize` has to handle.

    The SIXTH container is the `CMap<CSymbol64 slot, CSymbol64 texture>` that
    `evr_material_textures` reads as the material's own texture table. Pass
    `slot_textures` as `(slot_hash, texture_hash)` pairs to populate it; pass
    `trailing` instead to put arbitrary bytes there, which is only useful for
    testing the size bookkeeping itself.

    Every table is kept a whole number of 8-byte units so that no alignment
    padding is needed. That matters because the two readers of this format
    disagree about padding -- `evr_material_resource` aligns each table to 8,
    `evr_material_textures.read_containers` packs them contiguously and
    demands the arrays consume the file exactly -- and a fixture with padding
    would be readable by one and not the other.
    """
    head = bytearray(40)
    struct.pack_into("<Q", head, emr.OFF_MATERIALFX, materialfx)
    struct.pack_into("<4f", head, emr.OFF_BAKECOLOR, *bakecolor)
    struct.pack_into("<H", head, emr.OFF_BLENDMODE, blendmode)
    struct.pack_into("<H", head, emr.OFF_MATTYPE, mattype)
    struct.pack_into("<I", head, emr.OFF_FLAGS, flags)
    struct.pack_into("<f", head, emr.OFF_SHADOWFADEDIST, -1.0)

    descriptors = b"".join((
        # materialprops is a CTable<u8>: its count is in BYTES, so a float
        # occupies four of them.
        _descriptor(len(props) * emr.PROP_WORD_SIZE,
                    element_size=emr.PROP_BYTE_SIZE),
        _descriptor(len(propoffsets), element_size=emr.PROPOFF_ENTRY_SIZE,
                    stride=64),
        _descriptor(len(uvsets), element_size=emr.UVSET_ENTRY_SIZE),
        # `permutations` is a CMap<u32,u32>: 8-byte elements. Declaring 1-byte
        # elements still adds up for the payload walk, but contradicts the
        # stride `evr_material_textures.CONTAINERS` validates against.
        _descriptor(len(perms) // PERM_ENTRY_SIZE, element_size=PERM_ENTRY_SIZE,
                    stride=64),
        _descriptor(len(aux_blob) // emr.SHADER_INPUT_SIZE,
                    element_size=emr.SHADER_INPUT_SIZE),
        _descriptor(len(slot_textures) if slot_textures else len(trailing),
                    element_size=SLOT_TEXTURE_SIZE if slot_textures else 1),
    ))

    # The undescribed 32-byte block between the descriptors and the first
    # table. No descriptor accounts for it, so a fixture that omits it leaves
    # every table 32 bytes adrift of where the reader looks.
    preamble = struct.pack("<4Q", 0, 0, 0, 0xFFFFFFFFFFFFFFFF)

    payload = bytearray()

    def _emit(chunk):
        """Append a table, 8-byte aligned like the reader expects.

        Only `materialprops` normally needs this -- it is the one CTable<u8>,
        so its byte count is arbitrary -- but aligning every table keeps the
        fixture honest rather than accidentally right.
        """
        payload.extend(bytes(-len(payload) % emr.TABLE_ALIGNMENT))
        payload.extend(chunk)

    _emit(b"".join(struct.pack("<f", word) for word in props))
    _emit(b"".join(struct.pack("<QII", key, byteoffset, 0)
                   for key, byteoffset in propoffsets))
    _emit(b"".join(struct.pack("<Q", uvset) for uvset in uvsets))
    _emit(perms)                      # declaration order: perms before aux
    _emit(aux_blob)
    _emit(b"".join(struct.pack("<QQ", slot, texture)
                   for slot, texture in slot_textures)
          if slot_textures else trailing)

    return bytes(head) + descriptors + preamble + bytes(payload)


def _bind(inputname, texture, *, slot=0, layer=0, type_=1,
          engineresource=0, uscale=1.0, vscale=1.0):
    """A 32-byte SShaderInputData entry."""
    buf = bytearray(emr.SHADER_INPUT_SIZE)
    struct.pack_into("<Q", buf, emr.IN_INPUTNAME, inputname)
    struct.pack_into("<Q", buf, emr.IN_TEXTUREASSETID, texture)
    struct.pack_into("<H", buf, emr.IN_TYPE, type_)
    struct.pack_into("<H", buf, emr.IN_LAYER, layer)
    struct.pack_into("<H", buf, emr.IN_ENGINERESOURCE, engineresource)
    struct.pack_into("<H", buf, emr.IN_SLOT, slot)
    struct.pack_into("<f", buf, emr.IN_USCALE, uscale)
    struct.pack_into("<f", buf, emr.IN_VSCALE, vscale)
    return bytes(buf)


# --- the derived layout -----------------------------------------------------

def test_payload_starts_at_392():
    """40-byte header + 4 CTables (56) + 2 CMaps (64) = 392."""
    assert emr.HEADER_SIZE == 392
    assert emr.HEADER_SIZE == 0x188


def test_descriptor_starts_are_derived_not_transcribed():
    assert emr.DESC_STARTS == {
        "materialprops": 0x28,
        "materialpropoffsets": 0x28 + 56,
        "uvsets": 0x28 + 56 + 64,
        "permutations": 0x28 + 56 + 64 + 56,
        "auxillaryinputs": 0x28 + 56 + 64 + 56 + 64,
        "trailing": 0x28 + 56 + 64 + 56 + 64 + 56,
    }
    for name, start in emr.DESC_STARTS.items():
        assert emr.DESC_OFFSETS[name] == start + emr.DESC_COUNT


def test_layout_matches_lone_echo_once_the_two_differences_are_applied():
    """Echo VR == Lone Echo, minus `bakeemissivecolor`, plus one descriptor.

    Lone Echo's header is 56 bytes and has five descriptors ending at 0x160.
    Echo VR drops the 16-byte `bakeemissivecolor` (56 -> 40) and appends a
    sixth 56-byte CTable. If both are true, the descriptor *spacing* must be
    identical in the two formats -- which is the real cross-check, because the
    spacing is what the scalar reader depends on.
    """
    le_header, le_descriptor_base = 56, 56
    le_starts = {}
    cursor = le_descriptor_base
    for name, stride, _elem in emr.DESCRIPTORS[:5]:   # Lone Echo has no `trailing`
        le_starts[name] = cursor
        cursor += stride

    # Lone Echo's published constants, from le_mesh/material_scalars.py. That
    # module reads +40 and calls it `iused`, so its constants land on the
    # CAPACITY slot -- see the note below.
    assert le_starts["materialprops"] + emr.DESC_CAPACITY == 0x060
    assert le_starts["materialpropoffsets"] + emr.DESC_CAPACITY == 0x098
    assert le_starts["uvsets"] + emr.DESC_CAPACITY == 0x0D8
    assert le_starts["permutations"] + emr.DESC_CAPACITY == 0x110
    assert le_starts["auxillaryinputs"] + emr.DESC_CAPACITY == 0x150
    assert cursor == 0x160                        # Lone Echo's HEADER_SIZE

    # The actual cross-check: identical spacing, shifted down by the 16 dropped
    # header bytes. Compare descriptor STARTS, because the two readers do not
    # read the same slot within a descriptor and comparing slot addresses would
    # be comparing different fields.
    for name in ("materialprops", "materialpropoffsets", "uvsets",
                 "permutations", "auxillaryinputs"):
        assert emr.DESC_STARTS[name] == le_starts[name] - 16
    assert le_header - emr.DESC_BASE == 16

    # ⚠ The two readers disagree about which slot holds the count, and this
    # module is the one that matches the data: capacity (+40) and count (+48)
    # differ on 1059 of 2400 descriptor slots across 400 shipped Echo VR
    # materials, so they are NOT interchangeable. Lone Echo reading +40 as
    # `iused` is only safe where that game's own materials never over-allocate.
    assert emr.DESC_OFFSETS["materialprops"] == emr.DESC_STARTS["materialprops"] + emr.DESC_COUNT
    assert emr.DESC_COUNT != emr.DESC_CAPACITY


def test_lone_echo_constants_are_still_what_we_compared_against():
    """Guards the comparison above against drift in the Lone Echo module."""
    from le_mesh import material_scalars as msc

    assert msc.HEADER_SIZE == 0x160
    assert msc.OFF_MATERIALPROPS_IUSED == 0x060
    assert msc.OFF_PROPOFFSETS_IUSED == 0x098
    assert msc.OFF_UVSETS_IUSED == 0x0D8
    assert msc.OFF_PERMS_IUSED == 0x110
    assert msc.OFF_AUXINPUTS_IUSED == 0x150


# --- header -----------------------------------------------------------------

def test_header_fields_round_trip():
    data = _material(mattype=2, blendmode=7, flags=0x21,
                     bakecolor=(0.25, 0.5, 0.75, 1.0), materialfx=0xABCD)
    header = emr.parse_header(data)
    assert header.mattype == 2
    assert header.blendmode == 7
    assert header.flags == 0x21
    assert _approx(header.bakecolor, [0.25, 0.5, 0.75, 1.0])
    assert header.materialfx == f"{0xABCD:016x}"


def test_short_file_raises():
    _raises(emr.MaterialParseError, emr.parse_header, b"\x00" * 100)


def test_absurd_count_raises():
    data = bytearray(_material())
    struct.pack_into("<Q", data, emr.DESC_OFFSETS["materialprops"], 999_999)
    _raises(emr.MaterialParseError, emr.parse_header, bytes(data))


def test_capacity_is_not_count_and_capacity_is_ignored():
    """The bug that rejected 300/300 shipped materials.

    Real Echo VR materials over-allocate: `materialprops capacity=32 count=8`,
    `uvsets capacity=32 count=3`. Reading +40 as the element count both sizes
    the payload wrongly AND, in the first version of this module, raised a
    spurious error. Sizing from `dataByteSize` is immune to it.
    """
    data = _material(props=(1.0, 2.0), propoffsets=((0xAA, 0),))
    header = emr.parse_header(data)

    # materialprops counts bytes: two floats = 8, allocated 32.
    assert header.capacities["materialprops"] == FIXTURE_CAPACITY
    assert header.raw_counts["materialprops"] == 8
    assert header.element_sizes["materialprops"] == 1
    # uvsets is empty but still allocated, and must not consume payload.
    assert header.counts["uvsets"] == 0
    assert header.capacities["uvsets"] == FIXTURE_CAPACITY
    assert not header.size_mismatches


def test_element_size_is_derived_from_bytesize_over_capacity():
    """The descriptor describes its own element size; nothing is hard-coded."""
    data = _material(props=(1.0,), propoffsets=((0xAA, 0),), uvsets=(0x11,),
                     aux_blob=_bind(0x1111, 0xDEAD))
    sizes = emr.parse_header(data).element_sizes
    assert sizes["materialprops"] == emr.PROP_BYTE_SIZE
    assert sizes["materialpropoffsets"] == emr.PROPOFF_ENTRY_SIZE
    assert sizes["uvsets"] == emr.UVSET_ENTRY_SIZE
    assert sizes["auxillaryinputs"] == emr.SHADER_INPUT_SIZE


def test_payload_uses_count_not_capacity():
    """Allocated != stored. Sizing the walk off capacity overshoots massively."""
    data = _material(props=(1.0, 2.0), propoffsets=((0xAA, 0),))
    header = emr.parse_header(data)
    # Tables begin after the descriptors AND the undescribed preamble.
    base = emr.HEADER_SIZE + emr.PREAMBLE_SIZE
    # 8 bytes of props + one 16-byte offset entry, NOT 32 + 512.
    assert header.payload_offsets["materialpropoffsets"] == base + 8
    assert header.payload_end == base + 8 + 16


def test_a_materialprops_that_is_four_mod_eight_pads_the_next_table():
    """The common case: 174 of 400 shipped materials need this padding.

    `materialprops` is the one CTable<u8>, so its byte count is arbitrary; an
    odd number of 4-byte words leaves the cursor 4 past an 8-byte boundary and
    the NEXT table has to be pushed out to realign. Getting this wrong shifts
    every following table by four bytes, which still parses and still looks
    plausible -- the scalars stay word-aligned either way -- so it is worth
    pinning rather than eyeballing.
    """
    data = _material(props=(1.0,), propoffsets=((0xAA, 0),),
                     slot_textures=((0x1001, 0xB0),))
    header = emr.parse_header(data)
    base = emr.HEADER_SIZE + emr.PREAMBLE_SIZE

    assert header.payload_offsets["materialprops"] == base
    assert header.alignment_padding == {"materialpropoffsets": 4}
    assert header.payload_offsets["materialpropoffsets"] == base + 8
    assert not header.size_mismatches


def test_a_bytesize_that_is_not_a_whole_element_is_flagged():
    """THIS is a real layout error, unlike a capacity/count difference."""
    data = bytearray(_material(props=(1.0, 2.0)))
    struct.pack_into("<Q", data,
                     emr.DESC_STARTS["materialprops"] + emr.DESC_BYTESIZE, 7)
    header = emr.parse_header(bytes(data))
    assert any("whole multiple" in m for m in header.size_mismatches)


def test_count_above_capacity_is_rejected():
    """Would mean the descriptor fields are not where we think they are."""
    data = bytearray(_material(props=(1.0,)))
    struct.pack_into("<Q", data,
                     emr.DESC_STARTS["uvsets"] + emr.DESC_COUNT, 9999)
    _raises(emr.MaterialParseError, emr.parse_header, bytes(data))


def test_descriptors_account_for_the_whole_file():
    """`payload_end == len(data)` is the check that the layout closes."""
    data = _material(props=(1.0, 2.0), propoffsets=((0xAA, 0),),
                     uvsets=(0x11,), perms=b"\x00" * 24,
                     aux_blob=_bind(0x1111, 0xDEAD), trailing=b"\x00" * 8)
    assert emr.parse_header(data).payload_end == len(data)


# --- scalars ----------------------------------------------------------------

def test_prop_slots_map_hash_to_word():
    data = _material(props=(0.5, 0.25, 0.125),
                     propoffsets=((0xAAAA, 4), (0xBBBB, 8)))
    words, slots = emr.parse_material_prop_slots(data)
    assert _approx(words, [0.5, 0.25, 0.125])
    assert slots == {0xAAAA: 1, 0xBBBB: 2}


def test_byteoffset_is_bytes_not_index():
    """Offset 8 means word 2. Reading it as an index would give word 8."""
    data = _material(props=(9.0, 8.0, 7.0), propoffsets=((0xAAAA, 8),))
    _words, slots = emr.parse_material_prop_slots(data)
    assert slots[0xAAAA] == 2


def test_out_of_range_offsets_are_dropped_not_clamped():
    data = _material(props=(1.0,), propoffsets=((0xAAAA, 400),))
    _words, slots = emr.parse_material_prop_slots(data)
    assert slots == {}


def test_malformed_slice_returns_empty_rather_than_raising():
    """Matches the Lone Echo contract callers already rely on."""
    assert emr.parse_material_prop_slots(b"\x00" * 10) == ([], {})


def test_uvsets_are_located_after_the_prop_tables():
    data = _material(props=(1.0, 2.0), propoffsets=((0xAA, 0),),
                     uvsets=(0x1234, 0x5678))
    assert emr.parse_uvsets(data) == [f"{0x1234:016x}", f"{0x5678:016x}"]


def test_every_table_is_now_locatable_including_auxillaryinputs():
    """`dataByteSize` makes `permutations`' unknown element type irrelevant."""
    data = _material(props=(1.0, 2.0), propoffsets=((0xAA, 0),),
                     uvsets=(0x11,), perms=b"\x00" * 24,
                     aux_blob=_bind(0x1111, 0xDEAD))
    header = emr.parse_header(data)

    base = emr.HEADER_SIZE + emr.PREAMBLE_SIZE
    assert emr.payload_offset("materialprops", header) == base
    expected = base + 2 * 4 + 1 * 16 + 1 * 8 + 24
    assert emr.payload_offset("auxillaryinputs", header) == expected


# --- reading binds directly, by offset --------------------------------------

def test_binds_are_read_by_offset_with_no_anchor():
    """The direct read needs no `known_textures` and cannot miss an entry."""
    data = _material(perms=b"\x00" * 24,
                     aux_blob=_bind(0x1111, 0xDEAD, slot=3, layer=1))
    binds = emr.read_texture_binds(data)
    assert len(binds) == 1
    assert binds[0].inputname_hash == f"{0x1111:016x}"
    assert binds[0].textureassetid_hash == f"{0xDEAD:016x}"
    assert binds[0].slot == 3


def test_direct_read_finds_a_bind_whose_texture_is_absent_from_the_extract():
    """The scan cannot do this -- it is anchored on textures that exist."""
    data = _material(aux_blob=_bind(0x1111, 0xDEAD))
    assert len(emr.read_texture_binds(data)) == 1
    assert emr.scan_texture_binds(data, {f"{0xFEED:016x}"}) == []


def test_decode_prefers_the_offset_read():
    data = _material(aux_blob=_bind(0x1111, 0xDEAD))
    result = emr.decode(data, known_textures=set())
    assert result.bind_source == "offset"
    assert len(result.binds) == 1


def test_decode_falls_back_to_the_scan_when_offsets_are_useless():
    """Zeroed descriptors: nothing is locatable, but a hash match still is."""
    data = bytearray(_material(aux_blob=_bind(0x1111, 0xDEAD)))
    struct.pack_into("<Q", data,
                     emr.DESC_STARTS["auxillaryinputs"] + emr.DESC_BYTESIZE, 0)
    struct.pack_into("<Q", data,
                     emr.DESC_STARTS["auxillaryinputs"] + emr.DESC_COUNT, 0)
    result = emr.decode(bytes(data), known_textures={f"{0xDEAD:016x}"})
    assert result.bind_source == "scan"
    assert len(result.binds) == 1


def test_direct_read_skips_null_inputnames():
    data = _material(aux_blob=_bind(0, 0xDEAD) + _bind(0x2222, 0xBEEF))
    binds = emr.read_texture_binds(data)
    assert [b.inputname_hash for b in binds] == [f"{0x2222:016x}"]


# --- the bind scan ----------------------------------------------------------

def test_finds_a_bind_anchored_on_a_known_texture():
    data = _material(aux_blob=_bind(0x1111, 0xDEAD, slot=3, layer=1))
    binds = emr.scan_texture_binds(data, {f"{0xDEAD:016x}"})
    assert len(binds) == 1
    assert binds[0].inputname_hash == f"{0x1111:016x}"
    assert binds[0].textureassetid_hash == f"{0xDEAD:016x}"
    assert binds[0].slot == 3
    assert binds[0].layer == 1


def test_finds_several_binds_in_order():
    blob = (_bind(0x1111, 0xAAA, slot=0)
            + _bind(0x2222, 0xBBB, slot=1)
            + _bind(0x3333, 0xCCC, slot=2))
    binds = emr.scan_texture_binds(
        _material(aux_blob=blob),
        {f"{h:016x}" for h in (0xAAA, 0xBBB, 0xCCC)})
    assert [b.slot for b in binds] == [0, 1, 2]
    assert [b.inputname_hash for b in binds] == [
        f"{h:016x}" for h in (0x1111, 0x2222, 0x3333)]


def test_empty_anchor_finds_nothing():
    """An unanchored scan over this struct is noise, so it must refuse."""
    data = _material(aux_blob=_bind(0x1111, 0xDEAD))
    assert emr.scan_texture_binds(data, set()) == []


def test_unknown_texture_is_not_returned():
    data = _material(aux_blob=_bind(0x1111, 0xDEAD))
    assert emr.scan_texture_binds(data, {f"{0xFEED:016x}"}) == []


def test_a_texture_hash_in_the_descriptor_block_is_ignored():
    """Only the payload can hold binds; a match in the header is a coincidence."""
    data = bytearray(_material(aux_blob=_bind(0x1111, 0xDEAD)))
    struct.pack_into("<Q", data, 0x60, 0xDEAD)     # inside the descriptors
    binds = emr.scan_texture_binds(bytes(data), {f"{0xDEAD:016x}"})
    assert len(binds) == 1
    assert binds[0].offset >= emr.HEADER_SIZE


def test_rejects_implausible_uv_scale():
    for bad in (float("nan"), float("inf"), 1e30):
        data = _material(aux_blob=_bind(0x1111, 0xDEAD, uscale=bad))
        assert emr.scan_texture_binds(data, {f"{0xDEAD:016x}"}) == [], bad


def test_rejects_out_of_range_slot_and_layer():
    for kwargs in ({"slot": 9999}, {"layer": 9999}, {"type_": 60000}):
        data = _material(aux_blob=_bind(0x1111, 0xDEAD, **kwargs))
        assert emr.scan_texture_binds(data, {f"{0xDEAD:016x}"}) == [], kwargs


def test_rejects_null_and_sentinel_inputnames():
    for inputname in (0, 0xFFFFFFFFFFFFFFFF):
        data = _material(aux_blob=_bind(inputname, 0xDEAD))
        assert emr.scan_texture_binds(data, {f"{0xDEAD:016x}"}) == []


def test_each_entry_is_reported_once():
    """The two alignment passes must not double-report the same offset."""
    data = _material(aux_blob=_bind(0x1111, 0xDEAD))
    binds = emr.scan_texture_binds(data, {f"{0xDEAD:016x}"})
    assert len({b.offset for b in binds}) == len(binds) == 1


# --- decode() ---------------------------------------------------------------

def test_decode_reads_both_binds_regardless_of_the_anchor():
    """The offset read is not limited by `known_textures`, so both come back."""
    data = _material(aux_blob=_bind(0x1111, 0xDEAD) + _bind(0x2222, 0xBEEF))
    result = emr.decode(data, material_hash="ab", known_textures={f"{0xDEAD:016x}"})
    assert result.bind_source == "offset"
    assert len(result.binds) == 2
    assert not result.warnings


def test_scan_shortfall_is_warned_about_only_when_the_scan_was_used():
    data = bytearray(_material(aux_blob=_bind(0x1111, 0xDEAD)
                               + _bind(0x2222, 0xBEEF)))
    # Break the offset path so decode falls back to the anchored scan, which
    # can only see the one texture the anchor names. Inflating an EARLIER
    # table's count is the way to do it: it walks `auxillaryinputs` off the end
    # of the file, which is exactly the "descriptors do not add up" case the
    # scan exists for. Zeroing this table's own `dataByteSize` would not --
    # the count field at +48 is what sizes the read, and `dataByteSize` only
    # derives the element size.
    struct.pack_into("<Q", data,
                     emr.DESC_STARTS["uvsets"] + emr.DESC_COUNT,
                     FIXTURE_CAPACITY)
    result = emr.decode(bytes(data), known_textures={f"{0xDEAD:016x}"})
    assert result.bind_source == "scan"
    assert len(result.binds) == 1
    assert any("invisible to the scan" in w for w in result.warnings)


def test_a_file_whose_descriptors_do_not_close_is_warned_about():
    data = _material(props=(1.0,)) + b"\x00" * 16
    result = emr.decode(data)
    assert any("descriptors account for" in w for w in result.warnings)


def test_decode_exposes_props_by_hash():
    data = _material(props=(0.75,), propoffsets=((0xAAAA, 0),))
    assert _approx(emr.decode(data).props, {0xAAAA: 0.75})


def test_binds_carry_the_fields_roles_from_input_rows_reads():
    """`roles_from_input_rows` reads `inputname_hash`, `slot`, `shaderset_hash`."""
    data = _material(aux_blob=_bind(0x1111, 0xDEAD, slot=5))
    bind = emr.decode(data, known_textures={f"{0xDEAD:016x}"}).binds[0]
    for attribute in ("inputname_hash", "textureassetid_hash", "slot",
                      "shaderset_hash"):
        assert hasattr(bind, attribute), attribute


def test_role_textures_resolves_through_lone_echo():
    """An unnamed inputname must degrade to `unknown_s{slot}`, not vanish."""
    data = _material(aux_blob=_bind(0x1111, 0xDEAD, slot=7))
    result = emr.decode(data, known_textures={f"{0xDEAD:016x}"})
    assert result.role_textures({}) == {"unknown_s7": f"{0xDEAD:016x}"}


def test_role_textures_uses_a_supplied_name_table():
    data = _material(aux_blob=_bind(0x1111, 0xDEAD, slot=7))
    result = emr.decode(data, known_textures={f"{0xDEAD:016x}"})
    roles = result.role_textures({0x1111: "layer0_diffuse_map"})
    assert roles == {"layer0_diffuse_map": f"{0xDEAD:016x}"}


# --- the layout probe -------------------------------------------------------

def test_probe_accepts_a_well_formed_material():
    data = _material(props=(1.0, 2.0), propoffsets=((0xAA, 0), (0xBB, 4)))
    report = emr.probe_layout(data)
    assert report["ok"], report["problems"]
    assert report["propoffsets_in_range"] == 2


def test_probe_flags_offsets_that_miss_the_word_array():
    """This is the signal that the payload does not start where we think."""
    report = emr.probe_layout(_material(props=(1.0,), propoffsets=((0xAA, 4000),)))
    assert not report["ok"]
    assert any("outside" in p for p in report["problems"])


# ---------------------------------------------------------------------------
# Per-material UV scale
#
# The two property names are UNCRACKED, so the pair is found structurally: the
# hashes of `...u...` and `...v...` differ by a fixed XOR because CSymbol64 is a
# CRC and `ord('u') ^ ord('v') == 3`. These tests pin the structure and the
# ordering, because getting the ordering backwards silently transposes every
# atlas-shared texture instead of failing loudly.
# ---------------------------------------------------------------------------

_D = emr.UV_SCALE_PAIR_DELTA


def test_uv_scale_pairs_are_found_by_the_xor_delta():
    """Low-hash member is `vscale`, high-hash member is `uscale`."""
    low = 0xB038EC9606824F9F
    assert low ^ _D == 0xB038EF9606824F9F      # the shipped CATAPULT sign pair
    assert emr.uv_scale_from_props({low: 2.0, low ^ _D: 1.0}) == (1.0, 2.0)


def test_uv_scale_is_identity_when_nothing_is_authored():
    assert emr.uv_scale_from_props({}) == (1.0, 1.0)
    assert emr.uv_scale_from_props({0x1234: 2.0}) == (1.0, 1.0)   # no partner


def test_a_unit_pair_is_not_reported_as_a_transform():
    """`(1,1)` means "no Mapping node", not "a scale that happens to be 1"."""
    assert emr.uv_scale_from_props({0x40: 1.0, 0x40 ^ _D: 1.0}) == (1.0, 1.0)


def test_an_isotropic_pair_still_counts():
    """0.5/0.5 does not stretch, but it does still re-tile."""
    assert emr.uv_scale_from_props({0x40: 0.5, 0x40 ^ _D: 0.5}) == (0.5, 0.5)


def test_a_pair_outside_the_measured_slots_is_ignored():
    """The reason `slots` exists.

    Three pair hashes in the shipped corpus hold `(3,5)`/`(5,3)` on all 710
    materials that carry them -- fixed engine constants that merely sit a
    `UV_SCALE_PAIR_DELTA` apart. Treating them as UV scales put a bogus 3x5
    mapping on 41% of the corpus.
    """
    constant = 0x983B599CD5EDB4C9
    props = {constant: 5.0, constant ^ _D: 3.0}
    assert emr.uv_scale_from_props(props, slots=set()) == (1.0, 1.0)
    assert emr.uv_scale_from_props(props, slots={constant}) == (3.0, 5.0)


def test_the_most_common_non_unit_pair_wins():
    """One pair per texture layer, and no decoded edge from a pair to a layer.

    Same policy as `evr_shaderset.dominant_uv_scale` for the same ambiguity.
    """
    props = {
        0x10: 3.0, 0x10 ^ _D: 5.0,
        0x20: 3.0, 0x20 ^ _D: 5.0,
        0x30: 2.0, 0x30 ^ _D: 1.0,
        0x40: 1.0, 0x40 ^ _D: 1.0,          # identity, must not dilute the vote
    }
    assert emr.uv_scale_from_props(props) == (5.0, 3.0)


def test_nonsense_values_are_rejected():
    """A zero or negative scale collapses the UVs; NaN poisons the Mapping node."""
    assert emr.uv_scale_from_props({0x40: 0.0, 0x40 ^ _D: 1.0}) == (1.0, 1.0)
    assert emr.uv_scale_from_props({0x40: -2.0, 0x40 ^ _D: 1.0}) == (1.0, 1.0)
    assert emr.uv_scale_from_props({0x40: float("nan"), 0x40 ^ _D: 1.0}) == (1.0, 1.0)
    assert emr.uv_scale_from_props({0x40: 1e9, 0x40 ^ _D: 1.0}) == (1.0, 1.0)
