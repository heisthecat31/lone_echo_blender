"""`CGLightMapResourceWin7` container, join and slice semantics — archive-free.

Every number here was measured on shipped LE Win7 bytes on 2026-07-31 and is
embedded as a literal, so the suite runs with no Oodle, no archive and no `bpy`.
The full write-up is docs/LIGHTING.md.

Sources of the literals:
  * `blender_tool/exports/lightmap_probe/a8_bridge*.json`   (0703fd2acd5803e9)
  * `blender_tool/exports/lightmap_probe/a8_station*.json`  (942c829457a04a62)
  * `blender_tool/exports/lightmap_probe/*.dds`             (the shipped textures)
"""

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from le_mesh import lightmap as LM


# ---------------------------------------------------------------------------
# shipped bytes, verbatim
# ---------------------------------------------------------------------------

#: `942c829457a04a62` (stn_ext_itc_station_front), master CGLightMapResourceWin7
#: at archive pos 0x16119ac0, slice size 404.  The only fully-populated row in
#: the corpus: row 1 names all five textures.
STATION_MASTER = bytes.fromhex(
    "0a000000"
    "ffffffffffffffff" "ffffffffffffffff" "ffffffffffffffff" "ffffffffffffffff" "ffffffffffffffff"
    "2f5db9b139fa7801" "425a659bf9fca881" "435a659bf9fca881" "f157b58ff7792fbd" "f143b58ff7792fbd"
    + "ff" * (8 * 5 * 8)
)

#: `0703fd2acd5803e9` (stn_int_itc_bridge), master CGLightMapResourceWin7 at
#: pos 0xb55c90, slice size 284 — seven rows, every one null.
BRIDGE_MASTER = bytes.fromhex("07000000" + "ff" * (7 * 0x28))

#: bridge `892cca9de00b30a6`, pos 0xb55330, size 44 — the single-row shape that
#: 96 of the corpus's 98 resources use.  ao0/ao1 only; no lobe basis.
BRIDGE_SINGLE = bytes.fromhex(
    "01000000"
    "ffffffffffffffff" "bacabdf6727ed06c" "bbcabdf6727ed06c"
    "ffffffffffffffff" "ffffffffffffffff"
)

#: station_front `65eea2df86dd4e8d`, pos 0x16119ea0, size 44 — the resource the
#: dynamic-instance resource `0cc03859517e7805` names via its trailing
#: `lightmapsid`.
STATION_DYNAMIC_TARGET = bytes.fromhex(
    "01000000"
    "ffffffffffffffff" "fc91d1305e8d7f0f" "fd91d1305e8d7f0f"
    "ffffffffffffffff" "ffffffffffffffff"
)

#: `CGTextureResourceData` reads for the five textures of station_front row 1
#: (`maxwidth@0xc4`, `maxheight@0xc8`, `arraysize@0xd0`, `format@0xd8`).
STATION_ROW1_TEXTURES = {
    "lightmapid": ("0178fa39b1b95d2f", 1024, 1024, 65, 95),   # BC6H_UF16
    "ao0":        ("81a8fcf99b655a42", 1024, 1024, 13, 83),   # BC5_UNORM
    "ao1":        ("81a8fcf99b655a43", 1024, 1024, 13, 83),   # BC5_UNORM
    "dloc":       ("bd2f79f78fb557f1", 1024, 1024, 13, 80),   # BC4_UNORM
    "poocc":      ("bd2f79f78fb543f1", 1024, 1024, 13, 80),   # BC4_UNORM
}

#: every (slice size, declared count) shape seen across all 98 shipped
#: `CGLightMapResourceWin7` resources in the two archives, with its multiplicity.
SHIPPED_SIZE_SHAPES = {(44, 1): 96, (284, 7): 1, (404, 10): 1}

#: station_front's 1050 static-instance meshes, `lmsliceindex` histogram.
STATION_SLICE_HIST = {0: 15, 1: 42, 2: 10, 3: 36, 4: 28, 6: 57, 7: 208,
                      8: 91, 9: 99, 10: 185, 11: 160, 12: 112}
#: ...and their `lightmapindex` histogram (0xffffffff == not lightmapped).
STATION_INDEX_HIST = {1: 1045, 0xFFFFFFFF: 5}


# ---------------------------------------------------------------------------
# Q1 — the container
# ---------------------------------------------------------------------------

def test_shipped_slice_sizes_all_satisfy_the_count_arithmetic():
    """`size == 4 + count * 0x28` for every shape seen in 98/98 resources."""
    total = 0
    for (size, count), n in SHIPPED_SIZE_SHAPES.items():
        assert LM.table_size(count) == size, (size, count)
        total += n
    assert total == 98


def test_station_master_is_ten_rows_with_only_row_one_populated():
    assert len(STATION_MASTER) == 404 == LM.table_size(10)
    rows = LM.parse_lightmap_table(STATION_MASTER)
    assert len(rows) == 10
    populated = [r.index for r in rows if not r.is_null]
    assert populated == [1], populated
    # ...and it is the only row in the corpus with a lobe-basis map.
    assert [r.index for r in rows if r.has_color] == [1]


def test_station_master_row_one_names_the_five_shipped_textures():
    row = LM.parse_lightmap_table(STATION_MASTER)[1]
    got = row.textures
    assert set(got) == set(LM.ROLES)
    for role, (h, _w, _hh, _arr, _dxgi) in STATION_ROW1_TEXTURES.items():
        assert got[role] == h, (role, got[role], h)


def test_dloc_and_poocc_are_distinct_textures():
    """Corrects wave-2's 'the same texture in both slots' — they differ."""
    row = LM.parse_lightmap_table(STATION_MASTER)[1]
    assert row.dloc != row.poocc
    assert f"{row.dloc:016x}" == "bd2f79f78fb557f1"
    assert f"{row.poocc:016x}" == "bd2f79f78fb543f1"


def test_bridge_master_is_seven_null_rows():
    assert len(BRIDGE_MASTER) == 284 == LM.table_size(7)
    rows = LM.parse_lightmap_table(BRIDGE_MASTER)
    assert len(rows) == 7
    assert all(r.is_null for r in rows)
    assert not any(r.has_color for r in rows)


def test_single_row_shape_is_44_bytes_and_ao_only():
    for blob in (BRIDGE_SINGLE, STATION_DYNAMIC_TARGET):
        assert len(blob) == 44 == LM.table_size(1)
        rows = LM.parse_lightmap_table(blob)
        assert len(rows) == 1
        assert set(rows[0].textures) == {"ao0", "ao1"}
        assert not rows[0].has_color
        assert not rows[0].is_null


def test_ao1_hash_is_ao0_plus_one_on_every_shipped_row():
    """A measured regularity, not a rule we impose: ao1 == ao0 + 1, 4/4 rows."""
    for blob in (STATION_MASTER, BRIDGE_SINGLE, STATION_DYNAMIC_TARGET):
        for row in LM.parse_lightmap_table(blob):
            if row.ao0 != LM.NULL_HASH:
                assert row.ao1 == row.ao0 + 1, f"{row.ao0:016x}"


def test_shipped_bytes_round_trip_through_the_decoder():
    """Re-encoding the decoded rows reproduces the slice byte for byte."""
    for blob in (STATION_MASTER, BRIDGE_MASTER, BRIDGE_SINGLE, STATION_DYNAMIC_TARGET):
        rows = LM.parse_lightmap_table(blob)
        out = struct.pack("<I", len(rows))
        for r in rows:
            out += struct.pack("<5Q", *(getattr(r, k) for k in LM.ROLES))
        assert out == blob


# ---------------------------------------------------------------------------
# Q2 — lightmapindex is a DIRECT row index
# ---------------------------------------------------------------------------

def test_lightmapindex_is_a_direct_row_index_not_a_populated_rank():
    """The discriminating case: 1049 meshes carry 1 against a 10-row table
    whose only populated row is row 1.  Populated-rank would demand 0."""
    table = LM.parse_lightmap_table(STATION_MASTER)
    binding = LM.resolve(table, 1, 7)
    assert binding is not None
    assert binding.texture_set.index == 1
    assert binding.texture_set.textures["lightmapid"] == "0178fa39b1b95d2f"
    # the populated-rank reading would have made 0 the live index; it is a null row
    assert LM.resolve(table, 0, 7) is None


def test_station_index_histogram_is_consistent_with_the_master_table():
    table = LM.parse_lightmap_table(STATION_MASTER)
    for idx, n in STATION_INDEX_HIST.items():
        assert n > 0
        if idx == LM.LIGHTMAP_INDEX_NONE:
            assert not LM.is_lightmapped(idx)
            continue
        assert idx < len(table), idx
        assert LM.resolve(table, idx, 0) is not None
    assert sum(STATION_INDEX_HIST.values()) == 1050


def test_no_shipped_mesh_overruns_its_bound_table():
    """0 violations over 1221 shipped meshes; locked here for the two masters."""
    for blob, indices in ((STATION_MASTER, STATION_INDEX_HIST),
                          (BRIDGE_MASTER, {LM.LIGHTMAP_INDEX_NONE: 1})):
        table = LM.parse_lightmap_table(blob)
        for idx in indices:
            if idx == LM.LIGHTMAP_INDEX_NONE:
                continue
            assert idx < len(table)


# ---------------------------------------------------------------------------
# Q3 — the join
# ---------------------------------------------------------------------------

def test_scene_binds_its_lightmap_by_its_own_name_hash():
    name = 0x942C829457A04A62
    assert LM.lightmap_resource_name_for_scene(name) == name


def test_dynamic_lightmapsid_reads_the_slice_tail():
    """`SGDynamicInstancesData.lightmapsid` is the last 8 bytes of the slice."""
    target = 0x65EEA2DF86DD4E8D          # station_front, named by 0cc03859517e7805
    body = b"\x11" * 3664 + struct.pack("<Q", target)
    assert len(body) == 3672             # the real slice size of 0cc03859517e7805
    assert LM.dynamic_lightmapsid(body) == target


def test_dynamic_lightmapsid_treats_all_ones_as_no_lightmap():
    """6 of the bridge's 33 dynamic instances ship the sentinel."""
    body = b"\x00" * 648 + struct.pack("<Q", LM.NULL_HASH)
    assert len(body) == 656              # the real slice size of 590cdab56eb06a29
    assert LM.dynamic_lightmapsid(body) is None


def test_dynamic_lightmapsid_is_safe_on_a_short_slice():
    assert LM.dynamic_lightmapsid(b"\x00" * 4) is None
    assert LM.dynamic_lightmapsid(b"") is None
    assert LM.dynamic_lightmapsid(None) is None


def test_lightmap_resources_partition_into_conamed_plus_dynamic_named():
    """Every shipped lightmap resource is reached by exactly one of the two
    mechanisms, in both archives, with no leftovers and no overlap."""
    # (co-named, dynamic-named, total, populated, lit mesh-lists)
    for coname, dynamic, total, populated, lit in ((54, 27, 81, 36, 9),
                                                   (16, 1, 17, 5, 4)):
        assert coname + dynamic == total
        # populated == the lit mesh-lists' co-named tables + every dynamic-named one
        assert lit + dynamic == populated


def test_coname_predicts_populated_with_no_exceptions():
    """bridge 9 lit / 42 unlit of 51 parsed; station 4 lit / 9 unlit of 13."""
    for lit, unlit, parsed, total in ((9, 42, 51, 54), (4, 9, 13, 16)):
        assert lit + unlit == parsed
        assert parsed <= total


# ---------------------------------------------------------------------------
# Q4 — lmsliceindex is the page; the colour array is page-major
# ---------------------------------------------------------------------------

def test_lmsliceindex_range_matches_the_ao_array_size_exactly():
    pages = sorted(STATION_SLICE_HIST)
    ao_arraysize = STATION_ROW1_TEXTURES["ao0"][3]
    assert ao_arraysize == 13
    assert min(pages) == 0
    assert max(pages) == ao_arraysize - 1
    # every page except 5 is actually used by some mesh; 5 is unused, not absent
    assert set(pages) <= set(range(ao_arraysize))
    assert sum(STATION_SLICE_HIST.values()) == 1043


def test_two_shipped_meshes_carry_a_row_but_no_page():
    """`lightmapindex` and `lmsliceindex` are independent: 1045 station_front
    meshes carry row 1 but only 1043 carry a page.  Meshes 155
    (`627bcb577b88816d`) and 169 (`1d3ad4aa38198392`) have row 1 with the page
    sentinel.  They must resolve to a texture set with no colour slices, not
    to page 0 and not to nothing."""
    assert STATION_INDEX_HIST[1] - sum(STATION_SLICE_HIST.values()) == 2
    table = LM.parse_lightmap_table(STATION_MASTER)
    b = LM.resolve(table, 1, LM.LM_SLICE_NONE)
    assert b is not None and b.slice_index == LM.LM_SLICE_NONE
    assert LM.colour_slice_indices(b.slice_index, 5) == []


def test_page_five_is_unused_by_every_station_front_mesh():
    """12 of the 13 pages are referenced; page 5 is allocated but unreferenced.
    Locked so a future decode that 'finds' page 5 traffic is treated as news."""
    assert 5 not in STATION_SLICE_HIST
    assert sorted(STATION_SLICE_HIST) == [0, 1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12]


def test_colour_array_is_exactly_five_slices_per_page():
    colour = STATION_ROW1_TEXTURES["lightmapid"][3]
    ao = STATION_ROW1_TEXTURES["ao0"][3]
    assert colour == 65 and ao == 13
    assert LM.colour_slices_per_page(colour, ao) == 5


def test_colour_slices_per_page_is_loud_when_the_ratio_is_not_whole():
    for bad in ((64, 13), (65, 12), (7, 2)):
        try:
            LM.colour_slices_per_page(*bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{bad} should not have produced a page count")
    try:
        LM.colour_slices_per_page(65, 0)
    except ValueError:
        pass
    else:
        raise AssertionError("a zero page count must raise")


def test_colour_slices_are_page_major():
    """page p owns [5p, 5p+5) — export-validated 65/65 against lobe-major 0/65."""
    assert LM.colour_slice_indices(0, 5) == [0, 1, 2, 3, 4]
    assert LM.colour_slice_indices(7, 5) == [35, 36, 37, 38, 39]
    assert LM.colour_slice_indices(12, 5) == [60, 61, 62, 63, 64]
    # the whole 65-slice array is covered exactly once by the 13 pages
    seen = [s for p in range(13) for s in LM.colour_slice_indices(p, 5)]
    assert seen == list(range(65))


def test_colour_slice_indices_handles_the_none_sentinel():
    assert LM.colour_slice_indices(LM.LM_SLICE_NONE, 5) == []
    assert LM.colour_slice_indices(None, 5) == []
    assert LM.colour_slice_indices("nope", 5) == []
    assert LM.colour_slice_indices(3, 0) == []


def test_dds_payload_arithmetic_closes_for_all_three_extracted_textures():
    """1 mip, no padding: payload == arraysize * 1024*1024 bytes (1 B/texel for
    BC6H, BC5 and BC4 alike at 1024^2)."""
    for _role, (_h, w, h, arr, _dxgi) in STATION_ROW1_TEXTURES.items():
        assert w == h == 1024
        assert arr * w * h == arr * 1048576
    assert 65 * 1048576 == 68157440      # measured payload of 0178fa39b1b95d2f.dds
    assert 13 * 1048576 == 13631488      # measured payload of 81a8fcf99b655a42.dds


# ---------------------------------------------------------------------------
# Q5 — role names and lobe count
# ---------------------------------------------------------------------------

def test_role_name_tables_cover_every_role_exactly():
    assert set(LM.ROLE_PDB_FIELD) == set(LM.ROLES)
    assert set(LM.ROLE_RUNTIME_NAME) == set(LM.ROLES)
    assert LM.ROLE_PDB_FIELD["dloc"] == "dlocclusionid"
    assert LM.ROLE_PDB_FIELD["poocc"] == "poocclusionid"
    assert LM.ROLE_RUNTIME_NAME["dloc"] == "dirlightocclusion"
    assert LM.ROLE_RUNTIME_NAME["poocc"] == "punctualocclusion"
    # slot 0 is a LOBE BASIS, not a plain colour map
    assert LM.ROLE_RUNTIME_NAME["lightmapid"] == "lobebasis"


def test_numlobes_offset_and_the_observed_value():
    assert (LM.M_LIGHTMAPINDEX, LM.M_LMSLICEINDEX, LM.M_NUMLOBES) == (0x6C, 0x70, 0x74)
    assert LM.OBSERVED_NUMLOBES == 4
    # the 5-per-page vs numlobes-4 gap is the open question; assert it is a gap
    assert LM.colour_slices_per_page(65, 13) == LM.OBSERVED_NUMLOBES + 1


def test_ebasis_type_enum_is_the_pdb_one():
    assert LM.EBASIS_TYPE[0] == "eSH4Basis"
    assert LM.EBASIS_TYPE[2] == "eSG5Basis"
    assert len(LM.EBASIS_TYPE) == 8


# ---------------------------------------------------------------------------
# end-to-end: one real mesh -> five real texture hashes
# ---------------------------------------------------------------------------

def test_end_to_end_station_front_static_mesh_resolves_to_five_textures():
    """A shipped mesh from `942c829457a04a62`'s static-instance inline mesh-list:
    lightmapindex=1, lmsliceindex=7 (208 of the 1050 meshes use page 7)."""
    table = LM.parse_lightmap_table(STATION_MASTER)
    meta = {h: {"dxgi": dxgi, "width": w, "height": ht, "arraysize": arr}
            for (h, w, ht, arr, dxgi) in STATION_ROW1_TEXTURES.values()}
    spec = LM.spec_for_mesh(table, 1, 7, texture_meta=meta)

    assert spec["lightmap_index"] == 1
    assert spec["page_index"] == 7
    assert spec["slices_per_page"] == 5
    assert spec["color_slices"] == [35, 36, 37, 38, 39]
    assert spec["uv_layer"] == "uv1"

    for role, (h, _w, _ht, _arr, dxgi) in STATION_ROW1_TEXTURES.items():
        e = spec["roles"][role]
        assert e is not None and e["hash"] == h
        assert e["dxgi"] == dxgi
        assert e["dxgi_unexpected"] is False, role
    assert spec["color"]["colorspace"] == LM.COLORSPACE_LIGHTMAP
    assert spec["ao0"]["colorspace"] == LM.COLORSPACE_DATA


def test_spec_reports_slices_per_page_unresolved_rather_than_guessing():
    table = LM.parse_lightmap_table(STATION_MASTER)
    meta = {"0178fa39b1b95d2f": {"arraysize": 64},
            "81a8fcf99b655a42": {"arraysize": 13}}
    spec = LM.spec_for_mesh(table, 1, 7, texture_meta=meta)
    assert spec["slices_per_page"] == "unresolved"
    assert spec["color_slices"] == []


def test_spec_without_texture_meta_leaves_the_page_model_empty():
    table = LM.parse_lightmap_table(STATION_MASTER)
    spec = LM.spec_for_mesh(table, 1, 7)
    assert spec["page_index"] == 7
    assert spec["slices_per_page"] is None
    assert spec["color_slices"] == []
