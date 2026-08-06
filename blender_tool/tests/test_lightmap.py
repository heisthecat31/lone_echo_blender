"""Archive-free tests for `le_mesh.lightmap`.

Covers the stride-0x28 `SLightMapTextureNames` decode, the compact
`[u32 count][count x 0x28]` container, the `lightmapindex`/`lmsliceindex` ->
texture-set resolution, the Blender-facing spec, and the synthetic BC6H_UF16
stand-in used by `tests/blender_lightmap_probe.py`.

Fixtures are SYNTHETIC bytes assembled to the shipped layout, plus the real
hashes and the real container size recorded for station_front `942c829457a04a62`
in docs/SCENES.md §4b.  The real lightmap texture *bytes*
are not reachable from the checked-in fixtures (no DXGI-95 DDS exists in the
tree) — see docs/LIGHTING.md.

No Oodle, no archive, no `bpy`.  Runs under `python3 tests/run_tests.py` and
unchanged under pytest.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]          # .../blender_tool
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from le_mesh import lightmap as LM  # noqa: E402


# --- the one fully-resolved shipped row (station_front master, slot 1) -------
# `stream-confirmed`, docs/SCENES.md §4b.
REAL_LIGHTMAPID = 0x0178FA39B1B95D2F     # DXGI 95 BC6H_UF16, 1024^2 HDR colour
REAL_AO0 = 0x81A8FCF99B655A42            # DXGI 83 BC5_UNORM
REAL_AO1 = 0x81A8FCF99B655A43            # DXGI 83 BC5_UNORM
REAL_OCC = 0xBD2F79F1F1F1F1F1            # DXGI 80 BC4_UNORM (low bits synthetic)

#: the master resource's on-disk size, straight from the finding
REAL_MASTER_SIZE = 0x194
REAL_MASTER_COUNT = 10


def _row(lightmapid=LM.NULL_HASH, ao0=LM.NULL_HASH, ao1=LM.NULL_HASH,
         dloc=LM.NULL_HASH, poocc=LM.NULL_HASH) -> bytes:
    return struct.pack("<5Q", lightmapid, ao0, ao1, dloc, poocc)


def _null_row() -> bytes:
    return b"\xff" * LM.STRIDE


def _table(rows) -> bytes:
    return struct.pack("<I", len(rows)) + b"".join(rows)


def _station_front_master() -> bytes:
    """10 rows, only row 1 populated — the shipped master's shape."""
    rows = [_null_row() for _ in range(REAL_MASTER_COUNT)]
    rows[1] = _row(REAL_LIGHTMAPID, REAL_AO0, REAL_AO1, REAL_OCC, REAL_OCC)
    return _table(rows)


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------

def test_stride_is_0x28_and_is_five_symbol64s():
    assert LM.STRIDE == 0x28 == 40
    assert LM.STRIDE == 5 * 8
    assert (LM.F_LIGHTMAPID, LM.F_AO0, LM.F_AO1, LM.F_DLOC, LM.F_POOCC) == \
        (0x00, 0x08, 0x10, 0x18, 0x20)
    assert LM.ROLES == ("lightmapid", "ao0", "ao1", "dloc", "poocc")


def test_container_size_matches_the_shipped_master():
    """0x194 == 4 + 10*0x28 — the count-prefixed compact array, byte-exactly."""
    assert LM.table_size(REAL_MASTER_COUNT) == REAL_MASTER_SIZE
    assert len(_station_front_master()) == REAL_MASTER_SIZE


def test_subscene_lightmap_is_one_entry():
    """The 16 sub-scene lightmaps ship as [u32 1][1 entry] = 44 bytes."""
    blob = _table([_row(REAL_LIGHTMAPID)])
    assert len(blob) == LM.table_size(1) == 44
    assert len(LM.parse_lightmap_table(blob)) == 1


# ---------------------------------------------------------------------------
# record decode
# ---------------------------------------------------------------------------

def test_decode_texture_names_reads_five_hashes_in_field_order():
    rec = _row(REAL_LIGHTMAPID, REAL_AO0, REAL_AO1, REAL_OCC, REAL_OCC)
    s = LM.decode_texture_names(rec, 0, 7)
    assert s.index == 7
    assert s.lightmapid == REAL_LIGHTMAPID
    assert s.ao0 == REAL_AO0
    assert s.ao1 == REAL_AO1
    assert s.dloc == REAL_OCC and s.poocc == REAL_OCC
    assert not s.is_null and s.has_color
    assert s.textures == {
        "lightmapid": f"{REAL_LIGHTMAPID:016x}",
        "ao0": f"{REAL_AO0:016x}",
        "ao1": f"{REAL_AO1:016x}",
        "dloc": f"{REAL_OCC:016x}",
        "poocc": f"{REAL_OCC:016x}",
    }


def test_decode_at_a_nonzero_offset():
    blob = b"\xaa" * 13 + _row(REAL_LIGHTMAPID, ao0=REAL_AO0)
    s = LM.decode_texture_names(blob, 13)
    assert s.lightmapid == REAL_LIGHTMAPID and s.ao0 == REAL_AO0
    assert s.ao1 == LM.NULL_HASH


def test_decode_short_record_raises():
    try:
        LM.decode_texture_names(b"\x00" * (LM.STRIDE - 1))
    except ValueError:
        return
    raise AssertionError("a short SLightMapTextureNames must raise")


def test_all_ones_row_is_null_and_carries_no_textures():
    s = LM.decode_texture_names(_null_row())
    assert s.is_null and not s.has_color and s.textures == {}


def test_partially_null_row_reports_only_populated_slots():
    s = LM.decode_texture_names(_row(REAL_LIGHTMAPID, ao0=REAL_AO0))
    assert not s.is_null and s.has_color
    assert set(s.textures) == {"lightmapid", "ao0"}


# ---------------------------------------------------------------------------
# table decode
# ---------------------------------------------------------------------------

def test_parse_station_front_master_shape():
    table = LM.parse_lightmap_table(_station_front_master())
    assert len(table) == REAL_MASTER_COUNT
    populated = [s for s in table if not s.is_null]
    assert [s.index for s in populated] == [1]
    assert populated[0].lightmapid == REAL_LIGHTMAPID


def test_parse_at_a_slice_offset():
    blob = b"\x5a" * 32 + _station_front_master()
    table = LM.parse_lightmap_table(blob, 32)
    assert len(table) == REAL_MASTER_COUNT
    assert table[1].lightmapid == REAL_LIGHTMAPID


def test_truncated_table_raises_in_strict_mode_and_clamps_otherwise():
    blob = _station_front_master()[:-LM.STRIDE]     # one row short
    try:
        LM.parse_lightmap_table(blob)
    except ValueError:
        pass
    else:
        raise AssertionError("a truncated table must raise under strict=True")
    table = LM.parse_lightmap_table(blob, strict=False)
    assert len(table) == REAL_MASTER_COUNT - 1


def test_implausible_count_raises():
    """A wrong slice offset decodes as a giant count; that must be loud."""
    blob = struct.pack("<I", 0xDEADBEEF) + b"\x00" * 100
    try:
        LM.parse_lightmap_table(blob)
    except ValueError:
        return
    raise AssertionError("an implausible entry count must raise")


def test_empty_table_is_allowed():
    assert LM.parse_lightmap_table(struct.pack("<I", 0)) == []


# ---------------------------------------------------------------------------
# mesh binding: lightmapindex / lmsliceindex
# ---------------------------------------------------------------------------

def test_sentinels():
    assert LM.LIGHTMAP_INDEX_NONE == 0xFFFFFFFF
    assert LM.LM_SLICE_NONE == 0xFFFFFFFF
    assert LM.NULL_HASH == 0xFFFFFFFFFFFFFFFF


def test_is_lightmapped_rejects_the_none_sentinel():
    assert LM.is_lightmapped(0)
    assert LM.is_lightmapped(1)
    assert not LM.is_lightmapped(LM.LIGHTMAP_INDEX_NONE)
    assert not LM.is_lightmapped(None)
    assert not LM.is_lightmapped("")


def test_resolve_picks_the_row_and_keeps_the_slice():
    table = LM.parse_lightmap_table(_station_front_master())
    b = LM.resolve(table, 1, 3)
    assert b is not None
    assert b.lightmap_index == 1 and b.slice_index == 3
    assert b.texture_set.lightmapid == REAL_LIGHTMAPID
    assert b.has_color


def test_resolve_returns_none_for_unlightmapped_out_of_range_and_null_rows():
    table = LM.parse_lightmap_table(_station_front_master())
    assert LM.resolve(table, LM.LIGHTMAP_INDEX_NONE, LM.LM_SLICE_NONE) is None
    assert LM.resolve(table, 99, 0) is None
    assert LM.resolve(table, 0, 0) is None          # row 0 is an all-ones row


def test_resolve_accepts_string_indices_from_a_manifest():
    """`mesh_builder` stores uint32 ids as strings (they overflow a C int)."""
    table = LM.parse_lightmap_table(_station_front_master())
    b = LM.resolve(table, "1", "2")
    assert b is not None and b.lightmap_index == 1 and b.slice_index == 2
    assert LM.resolve(table, str(LM.LIGHTMAP_INDEX_NONE), "0") is None


def test_slice_index_defaults_to_the_sentinel():
    table = LM.parse_lightmap_table(_station_front_master())
    assert LM.resolve(table, 1).slice_index == LM.LM_SLICE_NONE


# ---------------------------------------------------------------------------
# Blender-facing spec
# ---------------------------------------------------------------------------

def test_spec_names_files_colorspaces_and_expected_formats():
    table = LM.parse_lightmap_table(_station_front_master())
    files = {
        f"{REAL_LIGHTMAPID:016x}": "textures/0178fa39b1b95d2f.dds",
        f"{REAL_AO0:016x}": "textures/81a8fcf99b655a42.dds",
    }
    meta = {f"{REAL_LIGHTMAPID:016x}": {"dxgi": LM.DXGI_BC6H_UF16,
                                        "width": 1024, "height": 1024}}
    spec = LM.spec_for_mesh(table, 1, 0, files, texture_meta=meta)

    assert spec["lightmap_index"] == 1 and spec["slice_index"] == 0
    assert spec["uv_layer"] == "uv1"

    color = spec["color"]
    assert color["role"] == "lightmapid"
    assert color["file"] == "textures/0178fa39b1b95d2f.dds"
    assert color["expected_dxgi"] == LM.DXGI_BC6H_UF16 == 95
    assert color["dxgi"] == 95 and color["dxgi_unexpected"] is False
    assert (color["width"], color["height"]) == (1024, 1024)

    # the HDR colour map must never get an sRGB transform
    assert color["colorspace"] == LM.COLORSPACE_LIGHTMAP == "Linear Rec.709"
    assert "sRGB" not in color["colorspace"]

    # AO / occlusion are data
    assert spec["ao0"]["colorspace"] == "Non-Color"
    assert spec["ao1"]["colorspace"] == "Non-Color"
    assert spec["poocc"]["colorspace"] == "Non-Color"
    assert spec["ao0"]["expected_dxgi"] == LM.DXGI_BC5_UNORM == 83
    assert spec["poocc"]["expected_dxgi"] == LM.DXGI_BC4_UNORM == 80

    # a role with no extracted file is reported, not dropped
    assert spec["ao1"]["file"] == ""


def test_spec_flags_a_format_that_disagrees_with_the_role():
    table = LM.parse_lightmap_table(_station_front_master())
    meta = {f"{REAL_LIGHTMAPID:016x}": {"dxgi": 78}}     # BC3_UNORM_SRGB, wrong
    spec = LM.spec_for_mesh(table, 1, 0, {}, texture_meta=meta)
    assert spec["color"]["dxgi_unexpected"] is True


def test_spec_is_empty_for_an_unlightmapped_mesh():
    table = LM.parse_lightmap_table(_station_front_master())
    assert LM.spec_for_mesh(table, LM.LIGHTMAP_INDEX_NONE, LM.LM_SLICE_NONE) == {}
    assert LM.build_lightmap_spec(None) == {}


def test_colorspace_for_role():
    assert LM.colorspace_for_role("lightmapid") == "Linear Rec.709"
    for r in ("ao0", "ao1", "dloc", "poocc", "who_knows"):
        assert LM.colorspace_for_role(r) == "Non-Color"


# ---------------------------------------------------------------------------
# BC6H_UF16 synthetic stand-in
# ---------------------------------------------------------------------------

def test_bc6h_block_is_16_bytes_and_mode_11():
    blk = LM.bc6h_uf16_solid_block(462, 462, 462)
    assert len(blk) == LM.BC6H_BLOCK_BYTES == 16
    assert (blk[0] & 0x1F) == 0b00011, "mode field must be the 5-bit mode 11"


def test_bc6h_endpoint_bits_round_trip_into_the_block():
    qr, qg, qb = 100, 511, 900
    blk = LM.bc6h_uf16_solid_block(qr, qg, qb)
    v = int.from_bytes(blk, "little")
    got = [(v >> (5 + 10 * i)) & 0x3FF for i in range(6)]
    assert got == [qr, qg, qb, qr, qg, qb], "both endpoints must be equal"
    assert (v >> 65) == 0, "indices must all be 0"


def test_bc6h_out_of_range_endpoint_raises():
    try:
        LM.bc6h_uf16_solid_block(1024, 0, 0)
    except ValueError:
        return
    raise AssertionError("an 11-bit endpoint must raise")


def test_bc6h_reference_decode_matches_the_d3d_unsigned_path():
    # q=0 -> 0.0; q=1023 -> the all-ones clamp; q=462 -> the probe's value,
    # which Blender 5.1.1's own BC6H decoder returns bit-identically
    # (`engine-confirmed`, tests/blender_lightmap_probe.py).
    assert LM.bc6h_uf16_decode_endpoint(0) == 0.0
    assert abs(LM.bc6h_uf16_decode_endpoint(462) - 0.50048828125) < 1e-9
    assert LM.bc6h_uf16_decode_endpoint(1023) > 1000.0     # 0xffff -> huge half


def test_bc6h_decode_is_monotonic():
    prev = -1.0
    for q in range(0, 1024, 37):
        v = LM.bc6h_uf16_decode_endpoint(q)
        assert v >= prev
        prev = v


def test_bc6h_quantise_round_trip():
    for target in (0.0, 0.25, 0.5, 1.0, 2.0):
        q = LM.bc6h_quantise_for(target)
        assert abs(LM.bc6h_uf16_decode_endpoint(q) - target) < 0.01


def test_write_bc6h_dds_header_is_a_dx10_bc6h_file(tmp_path):
    p = LM.write_bc6h_dds(tmp_path / "synth.dds", 64, 32, (462, 462, 462))
    b = Path(p).read_bytes()
    assert b[:4] == b"DDS "
    assert struct.unpack_from("<I", b, 4)[0] == 124
    assert struct.unpack_from("<I", b, 12)[0] == 32      # height
    assert struct.unpack_from("<I", b, 16)[0] == 64      # width
    assert b[84:88] == b"DX10"
    assert struct.unpack_from("<I", b, 128)[0] == LM.DXGI_BC6H_UF16 == 95
    assert struct.unpack_from("<I", b, 128 + 12)[0] == 1  # arraySize
    assert len(b) == 128 + 20 + (64 // 4) * (32 // 4) * 16


def test_write_bc6h_dds_gradient_and_array(tmp_path):
    p = LM.write_bc6h_dds(tmp_path / "grad.dds", 32, 32,
                          lambda x, y: (x * 8, y * 8, 300), arraysize=4)
    b = Path(p).read_bytes()
    assert struct.unpack_from("<I", b, 128 + 12)[0] == 4
    assert len(b) == 128 + 20 + 4 * (32 // 4) * (32 // 4) * 16


def test_write_bc6h_dds_rejects_non_multiple_of_four(tmp_path):
    try:
        LM.write_bc6h_dds(tmp_path / "bad.dds", 30, 32, (0, 0, 0))
    except ValueError:
        return
    raise AssertionError("non-multiple-of-4 dimensions must raise")


# ---------------------------------------------------------------------------
# integration with what the .lemesh manifest already carries
# ---------------------------------------------------------------------------

def test_meshlist_offsets_agree_with_this_module():
    from le_mesh import meshlist
    assert meshlist.M_LIGHTMAPINDEX == 0x6C
    assert meshlist.M_LMSLICEINDEX == 0x70


def test_fixture_manifests_split_cleanly_into_lightmapped_and_not():
    """The 51 shipped `.lemesh` fixtures: 15 objects carry lightmapindex 0, the
    other 106 carry the 0xffffffff sentinel — and every lightmapped one has uv1.

    `export-validated`. Skipped when the fixtures are absent.
    """
    import json
    fx = _ROOT / "exports" / "fixtures_mat"
    if not fx.is_dir():
        return
    lit = unlit = 0
    for pkg in sorted(fx.glob("*.lemesh")):
        mf = pkg / "manifest.json"
        if not mf.exists():
            continue
        for obj in json.loads(mf.read_text(encoding="utf-8"))["objects"]:
            if LM.is_lightmapped(obj.get("lightmap_index")):
                lit += 1
                assert "uv1" in obj.get("attributes", {}), (
                    f"{pkg.name}/{obj['name']} is lightmapped but has no uv1")
            else:
                unlit += 1
    if lit or unlit:
        assert lit == 15 and unlit == 106, (lit, unlit)


# ===========================================================================
# ★ THE SHIPPED LIGHTMAP TEXTURES  (blender_tool/exports/lightmap_probe/)
# ===========================================================================
# Everything below asserts values MEASURED on real bytes, either by parsing the
# DDS headers here or by `tests/blender_lightmap_probe.py` under Blender 5.1.1.
# Each test skips cleanly when the textures are not checked out.

import importlib.util  # noqa: E402

REAL_DIR = _ROOT / "exports" / "lightmap_probe"
REAL_LM_DDS = REAL_DIR / "0178fa39b1b95d2f.dds"
REAL_AO0_DDS = REAL_DIR / "81a8fcf99b655a42.dds"
REAL_AO1_DDS = REAL_DIR / "81a8fcf99b655a43.dds"


def _lightmap_builder():
    """Import `addon/lone_echo_import/lightmap_builder.py` WITHOUT its package.

    The package `__init__` imports `bpy` unconditionally; the builder module
    itself does not (it degrades to `bpy = None`), so its pure helpers — the SG5
    basis and the DX10 array splitter — are unit-testable under plain python3.
    """
    path = _ROOT / "addon" / "lone_echo_import" / "lightmap_builder.py"
    spec = importlib.util.spec_from_file_location("le_lightmap_builder", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _dx10(path):
    return _lightmap_builder().dds_dx10_header(path)


def test_shipped_colour_map_is_bc6h_uf16_1024_arraysize_65():
    """`engine-confirmed` on the shipped file: DXGI 95, 1024^2, arraySize 65,
    one mip, and a payload that divides exactly into 65 x 1 MiB slices."""
    if not REAL_LM_DDS.exists():
        return
    h = _dx10(REAL_LM_DDS)
    assert h == {"dxgi": LM.DXGI_BC6H_UF16, "width": 1024, "height": 1024,
                 "mips": 1, "arraysize": 65}, h
    per_slice = (1024 // 4) ** 2 * 16          # BC6H: 16 B per 4x4 block
    body = REAL_LM_DDS.stat().st_size - 148    # 148 = DDS + DX10 header
    assert per_slice == 1024 * 1024
    assert body == 65 * per_slice, (body, per_slice)


def test_shipped_ao_pair_is_bc5_1024_arraysize_13():
    if not REAL_AO0_DDS.exists():
        return
    for p in (REAL_AO0_DDS, REAL_AO1_DDS):
        h = _dx10(p)
        assert h == {"dxgi": LM.DXGI_BC5_UNORM, "width": 1024, "height": 1024,
                     "mips": 1, "arraysize": 13}, (p.name, h)


def test_colour_array_is_thirteen_pages_of_five_sg_lobes():
    """65 == 13 x 5.  The AO arrays carry exactly one slice per lightmap page,
    so the colour array's 65 slices are 13 pages x 5 SG lobes — which is what
    the engine's lightmap sampler (`lightmapuv.z = lightmapuv.z * 5 + i`) reads.
    """
    if not (REAL_LM_DDS.exists() and REAL_AO0_DDS.exists()):
        return
    LB = _lightmap_builder()
    pages = _dx10(REAL_AO0_DDS)["arraysize"]
    lobes = _dx10(REAL_LM_DDS)["arraysize"] // pages
    assert pages == 13 and lobes == LB.SG5_LOBES == 5
    assert pages * lobes == _dx10(REAL_LM_DDS)["arraysize"]


def test_sg5_slice_indices_are_page_major():
    LB = _lightmap_builder()
    assert LB.sg5_slice_indices(0) == [0, 1, 2, 3, 4]
    assert LB.sg5_slice_indices(1) == [5, 6, 7, 8, 9]
    assert LB.sg5_slice_indices(12) == [60, 61, 62, 63, 64]
    # the last page's last lobe is the last slice of the shipped array
    assert LB.sg5_slice_indices(12)[-1] == 64


def test_sg5_constants_match_the_shipped_shader_source():
    """`shader-confirmed`: `kLobeDirsSG5`, `kLambdaSG5` and `kSG5Scale`."""
    LB = _lightmap_builder()
    assert LB.SG5_LAMBDA == 3.62780595
    assert LB.SG5_SCALE == 0.5
    assert len(LB.SG5_DIRS) == 5
    # the lobes tile the hemisphere: z climbs 0.1 .. 0.9 and every direction is
    # a unit vector, so no lobe is ever culled by `saturate(dot(d, (0,0,1)))`.
    zs = [d[2] for d in LB.SG5_DIRS]
    assert [round(z, 6) for z in zs] == [0.1, 0.3, 0.5, 0.7, 0.9]
    for d in LB.SG5_DIRS:
        assert abs(sum(c * c for c in d) - 1.0) < 1e-6


def test_sg5_flat_weights_are_the_measured_ones():
    """`engine-confirmed`: these are the exact weights the Blender node graph
    used when it reproduced the python reference to 2.4e-05 on real texels
    (`blender_lightmap_probe.py` `[sg5-render]`)."""
    LB = _lightmap_builder()
    got = [round(w, 6) for w in LB.SG5_WEIGHTS_FLAT]
    assert got == [0.027565, 0.082695, 0.137824, 0.192954, 0.248084], got
    # a flat white bake does NOT come back as 1.0 — the engine's own SG
    # normalisation is deliberately approximate, and its own constants say so.
    assert abs(sum(LB.SG5_WEIGHTS_FLAT) - 0.689122) < 1e-6


def test_sg5_weights_clamp_lobes_facing_away():
    LB = _lightmap_builder()
    down = LB.sg5_weights((0.0, 0.0, -1.0))
    assert down == [0.0] * 5, down
    side = LB.sg5_weights((1.0, 0.0, 0.0))
    assert side[0] > 0.0 and side[1] == 0.0 and side[2] == 0.0


def test_split_array_slice_round_trips_a_synthetic_array(tmp_path):
    """Written against the synthetic writer so it runs with no shipped bytes."""
    LB = _lightmap_builder()
    src = tmp_path / "arr.dds"
    LM.write_bc6h_dds(src, 8, 8, lambda x, y: (400, 400, 400), arraysize=7)
    h = LB.dds_dx10_header(src)
    assert h["arraysize"] == 7 and h["dxgi"] == LM.DXGI_BC6H_UF16
    per = (src.stat().st_size - LB.DDS_HEADER_BYTES) // 7
    for i in range(7):
        dst = LB.split_array_slice(src, i, tmp_path / f"s{i}.dds")
        assert LB.dds_dx10_header(dst)["arraysize"] == 1
        assert dst.stat().st_size == LB.DDS_HEADER_BYTES + per
        assert dst.read_bytes()[LB.DDS_HEADER_BYTES:] == \
            src.read_bytes()[LB.DDS_HEADER_BYTES + i * per:][:per]


def test_split_array_slice_rejects_out_of_range(tmp_path):
    LB = _lightmap_builder()
    src = tmp_path / "arr.dds"
    LM.write_bc6h_dds(src, 8, 8, (400, 400, 400), arraysize=3)
    for bad in (-1, 3, 99):
        try:
            LB.split_array_slice(src, bad, tmp_path / "x.dds")
        except IndexError:
            continue
        raise AssertionError(f"slice {bad} should have raised")


def test_dds_dx10_header_rejects_non_dx10(tmp_path):
    LB = _lightmap_builder()
    p = tmp_path / "junk.dds"
    p.write_bytes(b"DDS " + b"\x00" * 200)
    assert LB.dds_dx10_header(p) is None
    assert LB.dds_dx10_header(tmp_path / "missing.dds") is None


def test_materialise_page_slices_is_cached_and_page_major(tmp_path):
    LB = _lightmap_builder()
    src = tmp_path / "arr.dds"
    LM.write_bc6h_dds(src, 8, 8, (400, 400, 400), arraysize=15)
    out = LB.materialise_page_slices(src, 2, tmp_path / "cache")
    assert [Path(p).name for p in out] == [
        f"arr.slice{i:03d}.dds" for i in (10, 11, 12, 13, 14)]
    stamps = [Path(p).stat().st_mtime_ns for p in out]
    again = LB.materialise_page_slices(src, 2, tmp_path / "cache")
    assert again == out
    assert [Path(p).stat().st_mtime_ns for p in again] == stamps  # not rewritten
    # a page beyond the array is reported as "no slices", never as a wrong file
    assert LB.materialise_page_slices(src, 3, tmp_path / "cache") == []


def test_shipped_array_splits_into_five_lobe_files(tmp_path):
    """The real 65-slice map -> page 12's five lobes, on shipped bytes."""
    if not REAL_LM_DDS.exists():
        return
    LB = _lightmap_builder()
    out = LB.materialise_page_slices(REAL_LM_DDS, 12, tmp_path)
    assert len(out) == 5
    for i, p in enumerate(out):
        h = LB.dds_dx10_header(p)
        assert h["arraysize"] == 1 and h["dxgi"] == 95 and h["width"] == 1024
        assert Path(p).name.endswith(f"slice{60 + i:03d}.dds")
    # page 13 does not exist in a 13-page array
    assert LB.materialise_page_slices(REAL_LM_DDS, 13, tmp_path) == []


def test_colourspace_table_is_the_measured_one():
    """`engine-confirmed (Blender 5.1.1)` on the shipped brightest texel
    (1.900391, 2.013672, 1.688477):

        Non-Color       -> identical
        Linear Rec.709  -> identical      <-- what we set
        sRGB            -> 4.396964, 5.033261, 3.338855   (x2.31 .. x2.50)

    The role table must therefore keep the HDR map off any sRGB transform and
    the AO maps on 'Non-Color' — Blender's DDS loader auto-assigns 'sRGB' to the
    BC5 AO pair, which would gamma-decode H-basis coefficients.
    """
    assert LM.COLORSPACE_LIGHTMAP == "Linear Rec.709"
    assert LM.COLORSPACE_LIGHTMAP_FALLBACK == "Non-Color"
    assert LM.colorspace_for_role("lightmapid") == "Linear Rec.709"
    for role in ("ao0", "ao1", "dloc", "poocc"):
        assert LM.colorspace_for_role(role) == "Non-Color", role


def test_default_basis_is_the_engines_own_math():
    """SG5 is what the engine does, so it is the importer default; the single-
    lobe path stays reachable and is what SG5 degrades to when the five per-lobe
    slices cannot be obtained."""
    LB = _lightmap_builder()
    assert LB.DEFAULT_BASIS == LB.BASIS_SG5 == "sg5"
    assert LB.BASIS_SINGLE == "single"
    assert LB.SG5_LOBES == 5


def test_builder_never_multiplies_the_bc5_colour_output():
    """⚠ Blender SYNTHESISES BC5's blue channel (it reconstructs a normal-map z;
    measured max err 0.0033 against `(sqrt(1-x^2-y^2)+1)/2` on shipped texels).
    So the AO path must broadcast R, never multiply the image's `Color`."""
    src = (_ROOT / "addon" / "lone_echo_import" / "lightmap_builder.py").read_text(
        encoding="utf-8")
    assert '_mix_multiply(nt, lm_socket, ao_tex.outputs["Color"])' not in src
    assert "_broadcast_red(nt, ao_tex.outputs[\"Color\"])" in src


# ===========================================================================
# ★ THE MATCHED PAIR — a station_front mesh-list whose uv1 indexes the
#   station_front atlas  (exports/station_lm/, added for front A11)
# ===========================================================================
# A9 could verify the colour/DXGI/page model only numerically: no shipped mesh
# package in the tree had a `uv1` into the extracted atlas
# (docs/LIGHTING.md §6.1).  This package closes that gap, and these
# tests lock what makes it usable as a PICTORIAL control.  Each skips cleanly
# when the export is not checked out.
#
# ⚠ Deliberately NOT asserted here: the flip_v / page-registration verdicts.
# Those need the atlas texels decoded, which needs a BC6H decoder — the shipped
# blocks are NOT all-zero even where they decode to black, so there is no
# stdlib shortcut.  They live in `tests/blender_lightmap_render.py`, which runs
# under Blender and reports them as measured texels.

import json  # noqa: E402

STATION_LM_PKG = (_ROOT / "exports" / "station_lm" /
                  "942c829457a04a62_942c829457a04a62.lemesh")

#: `stream-confirmed` via the export: every object indexes master row 1
#: (the one populated row), and these are its pages.
STATION_LM_PAGES = {
    "obj000_d83dfed24858e022": 3,
    "obj001_294372d551facd97": 3,
    "obj002_e1279d85ec1a5d13": 6,
    "obj003_c9081ba7f75ad73d": 10,
}


def _station_lm_manifest():
    if not STATION_LM_PKG.is_dir():
        return None
    return json.loads((STATION_LM_PKG / "manifest.json").read_text(encoding="utf-8"))


def test_station_lm_is_the_matched_mesh_plus_atlas_pair():
    """The export A9 asked for: station_front geometry with a real `uv1` and a
    non-null `lightmapindex`, from the same archive as the extracted atlas."""
    man = _station_lm_manifest()
    if man is None:
        return
    assert man["source"]["archive"] == "942c829457a04a62"
    objs = man["objects"]
    assert len(objs) == len(STATION_LM_PAGES)
    for o in objs:
        assert LM.is_lightmapped(o["lightmap_index"]), o["name"]
        # row 1 of the station_front master is the only populated row
        assert o["lightmap_index"] == 1, o["name"]
        assert o["lm_slice_index"] == STATION_LM_PAGES[o["name"]], o["name"]
        uv1 = o["attributes"].get("uv1")
        assert uv1 is not None, f"{o['name']} has no uv1 — not a lightmapped export"
        assert uv1["encoding"] == "eU16n" and uv1["comps"] == 2
        blob = STATION_LM_PKG / uv1["blob"]
        assert blob.stat().st_size == o["vertex_count"] * 2 * 4    # decoded f32


def test_station_lm_pages_resolve_to_the_expected_sg5_slices():
    """`page * 5 + i` on the real pages this package uses — the arithmetic the
    pictures in `blender_lightmap_render.py` exercise end to end."""
    LB = _lightmap_builder()
    want = {3: [15, 16, 17, 18, 19],
            6: [30, 31, 32, 33, 34],
            10: [50, 51, 52, 53, 54]}
    for page, slices in want.items():
        assert LB.sg5_slice_indices(page) == slices
        # and the two modules must not drift apart
        assert LM.colour_slice_indices(page, LM.colour_slices_per_page(65, 13)) == slices
    # every slice these pages need exists inside the shipped 65-slice array
    assert max(max(v) for v in want.values()) < 65


def test_station_lm_uv1_is_in_range_and_a_thin_atlas_band():
    """What a shipped lightmap chart set actually looks like: each object's
    `uv1` occupies a WIDE, SHORT band of the atlas (the bake packs its many
    small per-face charts into rows), not one square island."""
    man = _station_lm_manifest()
    if man is None:
        return
    import array as _array
    for o in man["objects"]:
        e = o["attributes"]["uv1"]
        raw = _array.array("f")
        raw.frombytes((STATION_LM_PKG / e["blob"]).read_bytes())
        n, c = o["vertex_count"], e["comps"]
        us = [raw[i * c] for i in range(n)]
        vs = [raw[i * c + 1] for i in range(n)]
        assert 0.0 <= min(us) and max(us) <= 1.0, o["name"]
        assert 0.0 <= min(vs) and max(vs) <= 1.0, o["name"]
        # taller than 1 texel, and much wider than tall
        dv, du = max(vs) - min(vs), max(us) - min(us)
        assert dv > 1.0 / 1024, o["name"]
        assert du / dv > 2.0, o["name"]


def test_station_lm_flip_v_is_a_decidable_test_on_this_package():
    """★ Why the pictures can settle `flip_v` at all.

    `flip_v` maps `v -> 1 - v`.  If an object's `uv1` band straddled v = 0.5 the
    flipped and unflipped footprints would overlap and the two renders would be
    partly the same picture — an inconclusive test.  On this package the two
    footprints are DISJOINT for all four objects, so flipped and unflipped
    address genuinely different atlas content and the comparison is meaningful.
    """
    man = _station_lm_manifest()
    if man is None:
        return
    import array as _array
    for o in man["objects"]:
        e = o["attributes"]["uv1"]
        raw = _array.array("f")
        raw.frombytes((STATION_LM_PKG / e["blob"]).read_bytes())
        n, c = o["vertex_count"], e["comps"]
        vs = [raw[i * c + 1] for i in range(n)]
        lo, hi = min(vs), max(vs)
        flo, fhi = 1.0 - hi, 1.0 - lo
        assert hi < flo or fhi < lo, (
            f"{o['name']}: uv1 v-band [{lo:.4f},{hi:.4f}] overlaps its own flip "
            f"[{flo:.4f},{fhi:.4f}] — the flip_v picture test would be degenerate")


def test_render_harness_forces_standard_view_transform():
    """Blender 4.0+ defaults to AgX, which desaturates highlights.  Any render
    used as evidence must force 'Standard' — see docs/LIGHTING.md."""
    src = (_ROOT / "tests" / "blender_lightmap_render.py").read_text(encoding="utf-8")
    assert 'view_transform = "Standard"' in src
    assert "AgX" in src            # and says why
