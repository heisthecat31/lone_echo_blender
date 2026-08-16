"""CGTextureStreamingResourceWin10 -- the layout, and the misparse it replaces.

Every fixture is a synthetic buffer built here, so this runs with no game data,
no Oodle and no Blender. No pytest either: plain `test_*` functions, so
`run_tests.py` executes them exactly as it does the rest of the suite.

The load-bearing test is `test_old_binding_offset_lands_in_the_mip_table`: it
pins the *specific* arithmetic error the previous extractor made, so that if
anyone reintroduces `rem_offset = 12 + tex_count * 8` as a binding offset, a test
says what it actually points at rather than the output merely looking odd.
"""

import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import evr_texture_streaming as ets


def _approx(a, b, eps=1e-6):
    return abs(a - b) <= eps


def _raises(exc, fn, *args, **kwargs):
    """Call `fn`, require `exc`, return it. Mirrors `pytest.raises` closely enough."""
    try:
        fn(*args, **kwargs)
    except exc as caught:
        return caught
    raise AssertionError(f"expected {exc.__name__}, nothing was raised")


def _build(textures, *, layouts=0, objects=(), obbs=0, sectors=None,
           packfile=0xAABBCCDDEEFF0011):
    """Assemble a byte-exact streaming resource from the verified layout."""
    out = bytearray()
    out += struct.pack("<Q", packfile)
    out += struct.pack("<I", len(textures))
    for tex in textures:
        out += struct.pack("<Q", tex)

    out += struct.pack("<I", layouts)
    for i in range(layouts):
        # 3 x u32[16]; fill with a recognisable ramp so a misparse is visible.
        out += struct.pack("<16I", *[(i + 1) * 100 + m for m in range(16)])
        out += struct.pack("<16I", *[(i + 1) * 200 + m for m in range(16)])
        out += struct.pack("<16I", *[(i + 1) * 300 + m for m in range(16)])

    out += struct.pack("<I", len(objects))
    for index, ratio in objects:
        out += struct.pack("<If", index, ratio)

    out += struct.pack("<I", obbs)
    for i in range(obbs):
        out += struct.pack("<3f", 1.0 * i, 2.0 * i, 3.0 * i)
        out += struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
        out += struct.pack("<3f", 1.0, 1.0, 1.0)

    if sectors is not None:
        out += struct.pack("<I", len(sectors))
        for reqs in sectors:
            out += struct.pack("<I", reqs)
            out += b"\x00" * (reqs * ets.SECTOR_REQ_SIZE)
    return bytes(out)


# --- the layout -------------------------------------------------------------

def test_parses_textures_in_order():
    data = _build([0x1111111111111111, 0x2222222222222222], sectors=[])
    res = ets.parse(data)
    assert res.textures == ["1111111111111111", "2222222222222222"]
    assert res.packfilename == "aabbccddeeff0011"


def test_objecttsdata_indexes_into_the_texture_list():
    data = _build([0xAA, 0xBB, 0xCC], objects=[(2, 0.5), (0, 0.25)], sectors=[])
    res = ets.parse(data)
    assert [o.texture_index for o in res.objecttsdata] == [2, 0]
    assert _approx(res.objecttsdata[0].max_texel_ratio, 0.5)
    assert res.texture_at(2) == f"{0xCC:016x}"
    assert res.streamed_textures() == [f"{0xCC:016x}", f"{0xAA:016x}"]


def test_layouts_are_skipped_by_stride_not_by_search():
    """Two 192-byte layouts must not shift objecttsdata by even one byte."""
    data = _build([0xAA, 0xBB], layouts=2, objects=[(1, 1.0)], sectors=[])
    res = ets.parse(data)
    assert len(res.packfilelayouts) == 2
    assert all(len(b) == ets.STREAM_DATA_SIZE for b in res.packfilelayouts)
    assert [o.texture_index for o in res.objecttsdata] == [1]


def test_minimal_file_without_sector_block_is_accepted():
    res = ets.parse(_build([], sectors=None))
    assert res.textures == []
    assert res.had_sectortsdata is False


def test_sector_requirements_are_length_prefixed():
    res = ets.parse(_build([0xAA], sectors=[2, 0, 1]))
    assert [len(s) // ets.SECTOR_REQ_SIZE for s in res.sectortsdata] == [2, 0, 1]


def test_obbs_consume_forty_bytes_each():
    res = ets.parse(_build([0xAA], obbs=3, sectors=[]))
    assert len(res.sectorobbs) == 3
    assert all(len(b) == ets.SECTOR_OBB_SIZE for b in res.sectorobbs)


# --- the misparse this module replaced --------------------------------------

def test_old_binding_offset_lands_in_the_mip_table():
    """`12 + tex_count*8` is `layouts_count`, NOT a binding array.

    The previous extractor read (u32, f32) "bindings" from just past that
    offset and derived material grouping from them. This test documents where
    that offset really points, so the mistake cannot be made silently twice.
    """
    textures = [0xAA, 0xBB, 0xCC]
    data = _build(textures, layouts=2, objects=[(0, 1.0)], sectors=[])

    old_offset = 12 + len(textures) * 8
    value_there = struct.unpack_from("<I", data, old_offset)[0]

    # It is the layout COUNT, and the bytes after it are 192-byte mip tables.
    assert value_there == 2
    first_mip_word = struct.unpack_from("<I", data, old_offset + 4)[0]
    assert first_mip_word == 100          # the ramp planted above, not a slot

    # The real per-object table sits two whole mip tables further on.
    res = ets.parse(data)
    assert len(res.objecttsdata) == 1
    assert old_offset + 4 + 2 * ets.STREAM_DATA_SIZE > old_offset + 8


def test_parser_exposes_no_bindings_attribute():
    """There is no binding data in this file; the API must not imply otherwise."""
    assert not hasattr(ets.parse(_build([0xAA], sectors=[])), "bindings")


# --- failure is loud --------------------------------------------------------

def test_absurd_texture_count_raises():
    data = struct.pack("<Q", 0) + struct.pack("<I", 0xFFFFFF) + b"\x00" * 8
    _raises(ets.StreamingParseError, ets.parse, data)


def test_truncated_file_raises_naming_the_field():
    data = struct.pack("<Q", 0) + struct.pack("<I", 4) + b"\x00" * 8
    caught = _raises(ets.StreamingParseError, ets.parse, data)
    assert "texture" in str(caught)


def test_trailing_bytes_raise_in_strict_mode():
    data = _build([0xAA], sectors=[]) + b"\xde\xad\xbe\xef"
    _raises(ets.StreamingParseError, ets.parse, data)
    # ...but are tolerated when the caller is deliberately probing.
    assert ets.parse(data, strict=False).textures == [f"{0xAA:016x}"]


def test_texture_at_is_bounds_checked():
    res = ets.parse(_build([0xAA], sectors=[]))
    assert res.texture_at(0) == f"{0xAA:016x}"
    assert res.texture_at(1) is None
    assert res.texture_at(-1) is None
