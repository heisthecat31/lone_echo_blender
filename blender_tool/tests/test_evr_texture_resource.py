"""cgtextureresourceWin10 -- header layout, DXGI classification, DDS patching.

Synthetic buffers only; no pytest.

The DXGI format is the field the material specs depend on for colourspace, BC5
normal reconstruction, alpha capability and composite role classification, so
`test_format_is_at_0xd8` is doing real work: if that offset drifts, every
material in every export is subtly wrong in four ways at once and nothing
crashes.
"""

import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import evr_texture_resource as etr


def _raises(exc, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except exc as caught:
        return caught
    raise AssertionError(f"expected {exc.__name__}, nothing was raised")


def _texture(*, fmt=98, width=256, height=256, mipcount=3,
             maxwidth=1024, maxheight=1024, maxmipcount=5,
             streamingdisabled=0, arraysize=1, inline=b"",
             mip_offsets=None, cmp_sizes=None, mip_sizes=None):
    """Build a synthetic cgtextureresourceWin10 blob."""
    buf = bytearray(etr.HEADER_SIZE)
    struct.pack_into("<16I", buf, etr.OFF_REVERSED_MIP_OFFSETS,
                     *(mip_offsets or [0] * 16))
    struct.pack_into("<16I", buf, etr.OFF_REVERSED_CMP_MIP_SIZES,
                     *(cmp_sizes or [0] * 16))
    struct.pack_into("<16I", buf, etr.OFF_REVERSED_MIP_SIZES,
                     *(mip_sizes or [0] * 16))
    struct.pack_into("<I", buf, etr.OFF_STREAMINGDISABLED, streamingdisabled)
    struct.pack_into("<I", buf, etr.OFF_MAXWIDTH, maxwidth)
    struct.pack_into("<I", buf, etr.OFF_MAXHEIGHT, maxheight)
    struct.pack_into("<I", buf, etr.OFF_MAXMIPCOUNT, maxmipcount)
    struct.pack_into("<I", buf, etr.OFF_ARRAYSIZE, arraysize)
    struct.pack_into("<I", buf, etr.OFF_FORMAT, fmt)
    struct.pack_into("<I", buf, etr.OFF_WIDTH, width)
    struct.pack_into("<I", buf, etr.OFF_HEIGHT, height)
    struct.pack_into("<I", buf, etr.OFF_MIPCOUNT, mipcount)
    struct.pack_into("<I", buf, etr.OFF_RESMEMSIZE, len(inline))
    return bytes(buf) + inline


def _dds(*, width=256, height=256, mipcount=3, dx10=False, arraysize=1,
         pixels=b"\x00" * 64):
    """A DDS blob with just enough header for the patcher to work on."""
    length = etr.DDS_HEADER_LEN_DX10 if dx10 else etr.DDS_HEADER_LEN
    buf = bytearray(length)
    buf[0:4] = etr.DDS_MAGIC
    struct.pack_into("<I", buf, etr.DDS_OFF_HEIGHT, height)
    struct.pack_into("<I", buf, etr.DDS_OFF_WIDTH, width)
    struct.pack_into("<I", buf, etr.DDS_OFF_MIPCOUNT, mipcount)
    if dx10:
        buf[etr.DDS_OFF_FOURCC:etr.DDS_OFF_FOURCC + 4] = b"DX10"
        struct.pack_into("<I", buf, etr.DDS_OFF_DX10_ARRAYSIZE, arraysize)
    return bytes(buf) + pixels


# --- layout -----------------------------------------------------------------

def test_header_is_256_bytes():
    assert etr.HEADER_SIZE == 256


def test_format_is_at_0xd8():
    """192 bytes of mip tables, then six u32s, puts `format` at 0xD8."""
    assert etr.OFF_FORMAT == 0xD8
    assert etr.OFF_FORMAT == 192 + 6 * 4


def test_mip_tables_occupy_the_first_192_bytes():
    assert etr.OFF_REVERSED_MIP_OFFSETS == 0
    assert etr.OFF_REVERSED_CMP_MIP_SIZES == 64
    assert etr.OFF_REVERSED_MIP_SIZES == 128
    assert etr.OFF_STREAMINGDISABLED == 192


def test_fields_round_trip():
    res = etr.parse(_texture(fmt=83, width=512, height=256, mipcount=4,
                             maxwidth=2048), texture_hash="abc")
    assert res.format == 83
    assert res.width == 512
    assert res.height == 256
    assert res.mipcount == 4
    assert res.maxwidth == 2048
    assert res.texture_hash == "0000000000000abc"


def test_short_file_raises():
    _raises(etr.TextureParseError, etr.parse, b"\x00" * 100)


# --- DXGI classification ----------------------------------------------------

def test_bc5_is_recognised_as_a_normal_format():
    for fmt in sorted(etr.BC5_FORMATS):
        assert etr.parse(_texture(fmt=fmt)).is_normal_format, fmt


def test_non_bc5_is_not_a_normal_format():
    for fmt in (71, 77, 98, 28):
        assert not etr.parse(_texture(fmt=fmt)).is_normal_format, fmt


def test_srgb_formats_are_flagged():
    for fmt in (29, 72, 78, 99):
        assert etr.parse(_texture(fmt=fmt)).is_srgb, fmt


def test_linear_formats_are_not_flagged_srgb():
    for fmt in (28, 71, 83, 98):
        assert not etr.parse(_texture(fmt=fmt)).is_srgb, fmt


def test_format_sets_match_lone_echos():
    """A disagreement would report one colourspace and apply another.

    `le_mesh.materials` is authoritative -- it is what `colorspace_for` and the
    BC5 Z-reconstruction actually consult. These constants exist only so the
    extractor can classify without importing the Blender-side module, so they
    must be the same sets, not merely similar ones.
    """
    from le_mesh import materials as le_materials

    assert etr.SRGB_FORMATS == le_materials.SRGB_DXGI
    assert etr.BC5_FORMATS == le_materials.BC5_DXGI


def test_unknown_format_still_gets_a_name():
    assert etr.parse(_texture(fmt=12345)).format_name == "DXGI_12345"


# --- mip tables -------------------------------------------------------------

def test_streamed_mips_drops_empty_and_sentinel_slots():
    offsets = [0, 100, 0xFFFFFFFF] + [0] * 13
    cmp_sizes = [64, 32, 16] + [0] * 13
    mips = etr.parse(_texture(mip_offsets=offsets,
                              cmp_sizes=cmp_sizes)).streamed_mips()
    assert [m[0] for m in mips] == [0, 100]        # sentinel slot dropped
    assert [m[1] for m in mips] == [64, 32]


def test_all_zero_tables_yield_no_streamed_mips():
    assert etr.parse(_texture()).streamed_mips() == []


# --- inline DDS -------------------------------------------------------------

def test_inline_dds_is_detected_by_magic():
    res = etr.parse(_texture(inline=_dds()))
    assert res.has_inline_dds
    assert res.inline_dds().startswith(etr.DDS_MAGIC)


def test_non_dds_inline_payload_is_not_offered_as_dds():
    res = etr.parse(_texture(inline=b"NOTADDS!" * 8))
    assert not res.has_inline_dds
    assert res.inline_dds() == b""


def test_dx10_header_length_is_detected():
    assert etr._dds_header_length(_dds(dx10=True)) == etr.DDS_HEADER_LEN_DX10
    assert etr._dds_header_length(_dds(dx10=False)) == etr.DDS_HEADER_LEN


# --- header patching --------------------------------------------------------

def test_patch_rewrites_dimensions_and_mipcount():
    header = bytearray(_dds()[:etr.DDS_HEADER_LEN])
    etr._patch_dds_header(header, width=1024, height=512, mipcount=6)
    assert struct.unpack_from("<I", header, etr.DDS_OFF_WIDTH)[0] == 1024
    assert struct.unpack_from("<I", header, etr.DDS_OFF_HEIGHT)[0] == 512
    assert struct.unpack_from("<I", header, etr.DDS_OFF_MIPCOUNT)[0] == 6


def test_array_texture_is_collapsed_to_one_slice():
    """Blender cannot read a DDS array; leaving the count breaks the file."""
    header = bytearray(_dds(dx10=True, arraysize=6)[:etr.DDS_HEADER_LEN_DX10])
    etr._patch_dds_header(header, width=64, height=64, mipcount=1)
    assert struct.unpack_from("<I", header, etr.DDS_OFF_DX10_ARRAYSIZE)[0] == 1


def test_single_slice_array_is_left_alone():
    header = bytearray(_dds(dx10=True, arraysize=1)[:etr.DDS_HEADER_LEN_DX10])
    etr._patch_dds_header(header, width=64, height=64, mipcount=1)
    assert struct.unpack_from("<I", header, etr.DDS_OFF_DX10_ARRAYSIZE)[0] == 1


# --- rebuild strategies -----------------------------------------------------

def test_inline_strategy_returns_the_resident_dds(tmp_path):
    directory = tmp_path / etr.TEXTURE_RESOURCE
    directory.mkdir(parents=True)
    (directory / f"{0xAA:016x}").write_bytes(_texture(inline=_dds()))

    blob, note = etr.rebuild_dds(tmp_path, 0xAA, strategy="inline")
    assert blob.startswith(etr.DDS_MAGIC)
    assert "inline" in note


def test_missing_texture_reports_rather_than_raises(tmp_path):
    blob, note = etr.rebuild_dds(tmp_path, 0xAA)
    assert blob is None
    assert "no texture resource" in note


def test_fully_streamed_texture_has_nothing_to_return(tmp_path):
    directory = tmp_path / etr.TEXTURE_RESOURCE
    directory.mkdir(parents=True)
    (directory / f"{0xAA:016x}").write_bytes(_texture(streamingdisabled=1))

    blob, note = etr.rebuild_dds(tmp_path, 0xAA)
    assert blob is None
    assert "nothing resident" in note


def test_layout_strategy_slices_mips_from_the_pack_file(tmp_path):
    offsets = [0, 16] + [0] * 14
    cmp_sizes = [16, 8] + [0] * 14
    directory = tmp_path / etr.TEXTURE_RESOURCE
    directory.mkdir(parents=True)
    (directory / f"{0xAA:016x}").write_bytes(
        _texture(inline=_dds(), mip_offsets=offsets, cmp_sizes=cmp_sizes))

    pack_dir = tmp_path / etr.RAW_TEXTURE_PACK
    pack_dir.mkdir(parents=True)
    (pack_dir / f"{0xBB:016x}").write_bytes(bytes(range(64)))

    blob, note = etr.rebuild_dds(tmp_path, 0xAA, packfile_hash=0xBB,
                                 strategy="layout")
    assert blob is not None
    assert "sliced 2 mip" in note
    # header + 16 + 8 bytes of mip data + the original inline pixels
    assert len(blob) == etr.DDS_HEADER_LEN + 16 + 8 + 64


def test_layout_strategy_refuses_a_mip_past_the_end_of_the_pack(tmp_path):
    offsets = [900] + [0] * 15
    cmp_sizes = [100] + [0] * 15
    directory = tmp_path / etr.TEXTURE_RESOURCE
    directory.mkdir(parents=True)
    (directory / f"{0xAA:016x}").write_bytes(
        _texture(inline=_dds(), mip_offsets=offsets, cmp_sizes=cmp_sizes))

    pack_dir = tmp_path / etr.RAW_TEXTURE_PACK
    pack_dir.mkdir(parents=True)
    (pack_dir / f"{0xBB:016x}").write_bytes(b"\x00" * 64)

    blob, note = etr.rebuild_dds(tmp_path, 0xAA, packfile_hash=0xBB,
                                 strategy="layout")
    assert blob is None
    assert "runs past" in note


def test_auto_falls_through_to_inline(tmp_path):
    """No pack file, no legacy hashes -- the resident DDS is still returned."""
    directory = tmp_path / etr.TEXTURE_RESOURCE
    directory.mkdir(parents=True)
    (directory / f"{0xAA:016x}").write_bytes(_texture(inline=_dds()))

    blob, note = etr.rebuild_dds(tmp_path, 0xAA)
    assert blob is not None
    assert "declined" in note and "inline" in note


def test_diagnose_warns_when_high_res_was_not_recovered(tmp_path):
    directory = tmp_path / etr.TEXTURE_RESOURCE
    directory.mkdir(parents=True)
    (directory / f"{0xAA:016x}").write_bytes(
        _texture(width=64, height=64, maxwidth=1024, maxheight=1024,
                 inline=_dds()))

    report = etr.diagnose(tmp_path, 0xAA)
    assert report["present"]
    assert "warning" in report
    assert "high-res mips were not recovered" in report["warning"]


def test_diagnose_reports_absence_plainly(tmp_path):
    assert etr.diagnose(tmp_path, 0xAA) == {"texture": f"{0xAA:016x}",
                                            "present": False}


# --- the legacy hash scan this module documents as wrong --------------------

def test_legacy_scan_reads_the_mip_size_tables():
    """`0x40..0x100` is mip sizes and typed fields, not a hash list.

    Pinned so the fallback's provenance stays visible: any "hash" it yields is
    a reinterpretation of mip metadata, which is why every candidate is
    validated against the pack directory before use.
    """
    data = _texture(cmp_sizes=[0x11111111] * 16, fmt=98)
    hashes = etr._legacy_high_res_hashes(data)
    # The first "hash" is two adjacent mip sizes glued into one u64.
    assert hashes[0] == f"{0x1111111111111111:016x}"


def test_legacy_scan_stops_at_an_all_ff_word():
    assert etr._legacy_high_res_hashes(_texture(cmp_sizes=[0xFFFFFFFF] * 16)) == []
