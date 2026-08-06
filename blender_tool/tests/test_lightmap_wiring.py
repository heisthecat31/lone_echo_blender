"""D3 — the baked lightmap is wired into the REAL `.lemesh` import path.

Front D3.  A9 built `wire_lightmap`, A11 proved it pictorially, and neither
front connected it to anything: `wire_lightmap` was referenced only from
`tests/`.  `import_lemesh` never called it, `build_object` never called it, and
`IMPORT_OT_lemesh` had no lightmap option at all.  These tests lock the join.

Everything here runs under plain `python3` (`tests/run_tests.py`) — no Blender.
`lightmap_builder` keeps `import bpy` optional exactly so this is possible; the
one function that genuinely needs `bpy.data.images` (`_load_image`) is driven
against a minimal fake so the colour-space forcing is still covered.

Evidence labels: `stream-confirmed` (read out of shipped bytes),
`export-validated` (checked against an already-extracted artifact),
`shader-confirmed` (matches the arithmetic RAD's own shaders perform),
`inferred`.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from le_mesh import lightmap as LM                      # noqa: E402


def _load_lightmap_builder():
    """Load `lightmap_builder` straight off disk, NOT via the addon package.

    `lone_echo_import/__init__.py` imports `bpy.props` at module scope, so
    importing the package outside Blender fails.  `lightmap_builder` itself
    guards `import bpy` and has no package-relative imports, which is exactly
    what makes this front's resolver unit-testable under plain `python3`.
    """
    path = ROOT / "addon" / "lone_echo_import" / "lightmap_builder.py"
    spec = importlib.util.spec_from_file_location("_le_lightmap_builder", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LB = _load_lightmap_builder()

#: ★ the matched pair (`export-validated`, docs/LIGHTING.md §9.1).
#: station_front master mesh-list, 4 objects, all `lightmapindex == 1`, pages
#: 3/3/6/10, real `uv1`; against the row-1 colour map they index.
PKG = ROOT / "exports" / "station_lm" / "942c829457a04a62_942c829457a04a62.lemesh"
ATLAS_DIR = ROOT / "exports" / "lightmap_probe"
ATLAS = ATLAS_DIR / "0178fa39b1b95d2f.dds"

#: `stream-confirmed` — the four objects' pages, and the two objects
#: that SHARE a material key while sitting on DIFFERENT pages.  That pair is the
#: whole reason the lightmap cannot be wired inside `build_material`.
STATION_PAGES = {
    "obj000_d83dfed24858e022": 3,
    "obj001_294372d551facd97": 3,
    "obj002_e1279d85ec1a5d13": 6,
    "obj003_c9081ba7f75ad73d": 10,
}
SHARED_KEY = "ae4aa9ff9320fcb1__6eac75dad7fc016d"


def _manifest():
    return json.loads((PKG / "manifest.json").read_text(encoding="utf-8"))


def _objects():
    return {o["name"]: o for o in _manifest()["objects"]}


def _synthetic_atlas(tmp_path, arraysize=65, name="deadbeefdeadbeef.dds"):
    """A tiny DXGI-95 arraySize-N DDS with the real header shape."""
    p = Path(tmp_path) / name
    LM.write_bc6h_dds(p, 8, 8, lambda x, y: (512, 512, 512), arraysize=arraysize)
    return p


# =============================================================================
# 1.  the page is never guessed
# =============================================================================

def test_page_sentinel_is_not_page_zero():
    """⛔ `lmsliceindex == 0xffffffff` must NOT fall back to page 0.

    `lightmapindex` and `lmsliceindex` are INDEPENDENT fields: 2 of the 1045
    station_front static meshes carry the valid row 1 together with the page
    sentinel (`627bcb577b88816d`, `1d3ad4aa38198392` —
    `stream-confirmed`, docs/LIGHTING.md §5).  Page 0 is a
    real page that 15 other meshes legitimately use, so substituting it renders
    a different part of the level's bake, not a degraded version of the right one.
    """
    assert LB._page_of(0xFFFFFFFF) is None
    assert LB._page_of("4294967295") is None          # `_int_prop` stringifies it
    assert LB._page_of(-1) is None
    assert LB._page_of(None) is None
    assert LB._page_of("nonsense") is None
    # ...and 0 is a real page, not a synonym for "missing".
    assert LB._page_of(0) == 0
    assert LB._page_of("0") == 0
    assert LB._page_of(12) == 12


def test_lightmap_index_sentinel_means_not_lightmapped():
    assert LB.is_lightmapped(0xFFFFFFFF) is False
    assert LB.is_lightmapped("4294967295") is False
    assert LB.is_lightmapped(None) is False
    assert LB.is_lightmapped(0) is True               # 96 of 98 tables have 1 row
    assert LB.is_lightmapped(1) is True               # the station_front master row
    # the two sentinels are the same value on two INDEPENDENT fields
    assert LB.LIGHTMAP_INDEX_NONE == LM.LIGHTMAP_INDEX_NONE == 0xFFFFFFFF
    assert LB.LM_SLICE_NONE == LM.LM_SLICE_NONE == 0xFFFFFFFF


def test_spec_for_object_refuses_a_pageless_mesh(tmp_path):
    """A valid row with no page yields `{}` — no spec, so nothing gets wired."""
    ctx = LB.resolve_lightmap_context(
        tmp_path, {}, {"lightmap_texture": str(_synthetic_atlas(tmp_path))})
    assert ctx["available"], ctx["reason"]
    ok = LB.lightmap_spec_for_object(ctx, {"lightmap_index": 1, "lm_slice_index": 3}, {})
    assert ok and ok["slice_index"] == 3
    for pageless in (0xFFFFFFFF, "4294967295", -1, None):
        assert LB.lightmap_spec_for_object(
            ctx, {"lightmap_index": 1, "lm_slice_index": pageless}, {}) == {}


def test_wire_lightmap_refuses_a_pageless_spec(tmp_path):
    """The same refusal one layer down, so a hand-built spec cannot sneak past.

    `wire_lightmap` needs neither a node tree nor a BSDF to reach the page check,
    so a plain dict stands in for the material (it only takes `mat[k] = v`).
    """
    atlas = _synthetic_atlas(tmp_path)
    spec = {"lightmap_index": 1, "slice_index": LB.LM_SLICE_NONE, "uv_layer": "uv1",
            "color": {"file": str(atlas), "hash": "deadbeefdeadbeef",
                      "colorspace": LB.COLORSPACE_LIGHTMAP}}
    rep = LB.wire_lightmap({}, None, None, spec, {"pkg_dir": None})
    assert rep["wired"] is False
    assert rep["page"] is None, "page must stay None, never default to 0"
    assert "page" in rep["reason"] and "page 0" in rep["reason"]


def test_report_page_defaults_to_none_not_zero():
    """`report['page'] == 0` must mean "page 0", never "no page read yet"."""
    rep = LB.wire_lightmap({}, None, None, {}, {})
    assert rep["page"] is None and rep["wired"] is False


# =============================================================================
# 2.  the real package: the right page per object, off the real manifest
# =============================================================================

def test_station_lm_import_path_requests_the_right_page_per_object(tmp_path):
    """`export-validated`: the import path derives 3/3/6/10 from the manifest.

    This is the join the whole front is about — the manifest's `lm_slice_index`
    reaching `wire_lightmap`'s `slice_index`, per object, through the same
    resolver the operator uses.
    """
    if not PKG.is_dir() or not ATLAS.exists():
        return                                 # matched pair not in this checkout
    man = _manifest()
    ctx = LB.resolve_lightmap_context(PKG, man, {"lightmap_dir": str(ATLAS_DIR)})
    assert ctx["available"], ctx["reason"]
    assert Path(ctx["color_file"]).name == ATLAS.name
    got = {o["name"]: LB.lightmap_spec_for_object(ctx, o, {})["slice_index"]
           for o in man["objects"]}
    assert got == STATION_PAGES, got


def test_station_lm_pages_map_to_the_right_sg5_slices(tmp_path):
    """`shader-confirmed`: the engine samples slice = page*5 + i.

    Derived, not assumed: `slices_per_page` comes out of the two shipped
    `arraysize` values (65 colour / 13 AO), which the resolver reads off the DDS
    headers.
    """
    if not PKG.is_dir() or not ATLAS.exists():
        return
    man = _manifest()
    ctx = LB.resolve_lightmap_context(PKG, man, {"lightmap_dir": str(ATLAS_DIR)})
    assert ctx["color_meta"]["arraysize"] == 65
    assert ctx["ao_meta"]["arraysize"] == 13, "the AO pair gives the page count"
    want = {"obj000_d83dfed24858e022": [15, 16, 17, 18, 19],
            "obj001_294372d551facd97": [15, 16, 17, 18, 19],
            "obj002_e1279d85ec1a5d13": [30, 31, 32, 33, 34],
            "obj003_c9081ba7f75ad73d": [50, 51, 52, 53, 54]}
    for o in man["objects"]:
        spec = LB.lightmap_spec_for_object(ctx, o, {})
        assert spec["slices_per_page"] == 5
        assert spec["color_slices"] == want[o["name"]], o["name"]
        assert spec["color_slices"] == LB.sg5_slice_indices(spec["slice_index"])


def test_ao_file_is_withheld_so_it_cannot_be_wired_unsplit(tmp_path):
    """The AO pair is an `arraySize 13` array too.

    Its arraysize is wanted (it is the page count); its BYTES are not — handing
    the unsplit array to Blender would sample slice 0 for every mesh, and the
    engine does not apply AO on the lightmap path at all (`shader-confirmed`;
    docs/LIGHTING.md §5).
    """
    if not PKG.is_dir() or not ATLAS.exists():
        return
    ctx = LB.resolve_lightmap_context(PKG, _manifest(), {"lightmap_dir": str(ATLAS_DIR)})
    spec = LB.lightmap_spec_for_object(
        ctx, {"lightmap_index": 1, "lm_slice_index": 3}, {})
    assert spec["ao0"]["arraysize"] == 13
    assert spec["ao0"]["file"] == ""
    assert spec["color"]["file"] == ctx["color_file"]


# =============================================================================
# 3.  a shared material on two pages must not collapse
# =============================================================================

def test_station_lm_really_does_share_a_material_across_two_pages():
    """The precondition, `export-validated` on the shipped manifest.

    Without this the "per-(material, page) variant" design would be solving a
    hypothetical: obj001 (page 3) and obj002 (page 6) use the SAME material key.
    """
    if not PKG.is_dir():
        return
    objs = _objects()
    keys = {n: {d["material_key"] for d in o["draws"]} for n, o in objs.items()}
    assert keys["obj001_294372d551facd97"] == {SHARED_KEY}
    assert keys["obj002_e1279d85ec1a5d13"] == {SHARED_KEY}
    assert STATION_PAGES["obj001_294372d551facd97"] != STATION_PAGES["obj002_e1279d85ec1a5d13"]


def test_shared_material_on_two_pages_yields_two_variants():
    """One material key + two pages -> two distinct datablock names.

    `variant_name` is the cache key `material_builder.lightmap_variant` uses, so
    a single name for both pages IS the collapse this test exists to prevent.
    """
    a = LB.variant_name(SHARED_KEY, 3)
    b = LB.variant_name(SHARED_KEY, 6)
    assert a != b
    assert a.endswith("__lm3") and b.endswith("__lm6")
    # page 0 must be spelled out, not elided -- "__lm" with nothing after it
    # would collide with a page-less variant.
    assert LB.variant_name(SHARED_KEY, 0) == f"{SHARED_KEY}__lm0"
    # composes with the vertex-colour split, which is keyed the same way
    assert LB.variant_name(f"{SHARED_KEY}__vcol", 6) == f"{SHARED_KEY}__vcol__lm6"


def test_variant_names_are_unique_across_the_whole_package():
    """No two (key, page) pairs in the shipped package share a variant name."""
    if not PKG.is_dir():
        return
    objs = _objects()
    names = set()
    for name, o in objs.items():
        for d in o["draws"]:
            names.add(LB.variant_name(d["material_key"], STATION_PAGES[name]))
    # 3 material keys x the pages each is used on = 4 distinct (key, page) pairs
    # (the shared key appears on pages 3 and 6).
    assert len(names) == 4, sorted(names)


# =============================================================================
# 4.  mode "none" is a true no-op
# =============================================================================

def test_mode_none_leaves_the_graph_untouched(tmp_path):
    """`lightmap_mode='none'` records provenance and creates NO nodes.

    `wire_lightmap` never touches the node tree in this mode, which is why
    passing `None` for it is safe here — if a future edit added a node it would
    raise instead of silently editing the graph.
    """
    atlas = _synthetic_atlas(tmp_path)
    spec = {"lightmap_index": 1, "slice_index": 3, "uv_layer": "uv1",
            "color": {"file": str(atlas), "hash": "deadbeefdeadbeef",
                      "colorspace": LB.COLORSPACE_LIGHTMAP}}
    mat = {}
    rep = LB.wire_lightmap(mat, None, None, spec, {"lightmap_mode": "none"})
    assert rep["wired"] is False and rep["nodes"] == []
    assert rep["reason"] == "lightmap_mode == 'none'"
    assert mat["le_lightmap_mode"] == "none"
    assert "le_lightmap_wired" not in mat


def test_resolved_mode_normalises_and_defaults():
    assert LB.resolved_mode({}) == LB.MODE_BAKED == "baked"
    assert LB.resolved_mode({"lightmap_mode": "AMBIENT"}) == "ambient"
    assert LB.resolved_mode({"lightmap_mode": "none"}) == "none"
    # an unknown value must not silently disable the lightmap, and must not
    # silently enable an unintended mode either -- it falls back to the default.
    assert LB.resolved_mode({"lightmap_mode": "sg5"}) == LB.DEFAULT_MODE
    assert LB.resolved_mode({"lightmap_mode": None}) == LB.DEFAULT_MODE


def test_unwired_package_is_a_clean_no_op(tmp_path):
    """A package with no atlas anywhere: reported, not guessed, not crashed."""
    pkg = Path(tmp_path) / "empty.lemesh"
    pkg.mkdir()
    ctx = LB.resolve_lightmap_context(pkg, {"objects": []}, {})
    assert ctx["available"] is False
    assert "no lightmap atlas found" in ctx["reason"]
    assert LB.lightmap_spec_for_object(ctx, {"lightmap_index": 1,
                                             "lm_slice_index": 3}, {}) == {}
    assert LB.lightmap_spec_for_object(None, {"lightmap_index": 1,
                                              "lm_slice_index": 3}, {}) == {}


def test_unlightmapped_mesh_is_a_clean_no_op(tmp_path):
    ctx = LB.resolve_lightmap_context(
        tmp_path, {}, {"lightmap_texture": str(_synthetic_atlas(tmp_path))})
    assert LB.lightmap_spec_for_object(
        ctx, {"lightmap_index": 0xFFFFFFFF, "lm_slice_index": 3}, {}) == {}
    assert LB.lightmap_spec_for_object(
        ctx, {"lightmap_index": "4294967295", "lm_slice_index": 3}, {}) == {}


# =============================================================================
# 5.  atlas resolution
# =============================================================================

def test_explicit_texture_beats_a_directory_scan(tmp_path):
    d = Path(tmp_path) / "atlases"
    d.mkdir()
    wanted = _synthetic_atlas(d, name="aaaaaaaaaaaaaaaa.dds")
    _synthetic_atlas(d, name="bbbbbbbbbbbbbbbb.dds")
    ctx = LB.resolve_lightmap_context(tmp_path, {}, {"lightmap_texture": str(wanted),
                                                    "lightmap_dir": str(d)})
    assert ctx["color_file"] == str(wanted) and ctx["source"] == "lightmap_texture"
    assert ctx["color_hash"] == "aaaaaaaaaaaaaaaa"


def test_missing_explicit_texture_is_reported_not_silently_scanned(tmp_path):
    """An explicit path that does not exist is an ERROR, not an invitation to
    go looking elsewhere: silently wiring a different level's bake would be
    worse than wiring none."""
    d = Path(tmp_path) / "atlases"
    d.mkdir()
    _synthetic_atlas(d)
    ctx = LB.resolve_lightmap_context(
        tmp_path, {}, {"lightmap_texture": str(Path(tmp_path) / "nope.dds"),
                       "lightmap_dir": str(d)})
    assert ctx["available"] is False and "nope.dds" in ctx["reason"]


def test_directory_scan_picks_the_bc6h_array_and_the_bc5_page_count(tmp_path):
    """Format-driven, not name-driven: DXGI 95 is the lobe basis, 83 is the AO
    pair (`stream-confirmed`, docs/LIGHTING.md §1.2)."""
    d = Path(tmp_path) / "lm"
    d.mkdir()
    colour = _synthetic_atlas(d, arraysize=65, name="cccccccccccccccc.dds")
    # a BC5-shaped stand-in: same DX10 header, DXGI 83, arraySize 13
    ao = d / "dddddddddddddddd.dds"
    raw = bytearray(_synthetic_atlas(d, arraysize=13, name="_tmp.dds").read_bytes())
    import struct
    struct.pack_into("<I", raw, 128, LB.DXGI_BC5_UNORM)
    ao.write_bytes(bytes(raw))
    (d / "_tmp.dds").unlink()

    ctx = LB.resolve_lightmap_context(tmp_path, {}, {"lightmap_dir": str(d)})
    assert ctx["color_file"] == str(colour)
    assert ctx["color_meta"]["arraysize"] == 65
    assert ctx["ao_meta"]["arraysize"] == 13
    spec = LB.lightmap_spec_for_object(ctx, {"lightmap_index": 0,
                                             "lm_slice_index": 7}, {})
    assert spec["slices_per_page"] == 5
    assert spec["color_slices"] == [35, 36, 37, 38, 39]


def test_manifest_lightmap_section_is_honoured_when_present(tmp_path):
    """Forward-compatible: an extractor that grows a `lightmap` manifest section
    is used before any directory scan.  No shipped export has one today."""
    pkg = Path(tmp_path) / "pkg"
    (pkg / "tex").mkdir(parents=True)
    LM.write_bc6h_dds(pkg / "tex" / "0178fa39b1b95d2f.dds", 8, 8, (512, 512, 512),
                      arraysize=65)
    man = {"lightmap": {"color": {"hash": "0178fa39b1b95d2f",
                                  "file": "tex/0178fa39b1b95d2f.dds"}}}
    ctx = LB.resolve_lightmap_context(pkg, man, {})
    assert ctx["available"] and ctx["color_hash"] == "0178fa39b1b95d2f"
    assert ctx["source"].startswith("manifest[")


def test_slice_cache_defaults_beside_the_atlas_and_is_overridable(tmp_path):
    atlas = _synthetic_atlas(tmp_path)
    ctx = LB.resolve_lightmap_context(tmp_path, {}, {"lightmap_texture": str(atlas)})
    assert Path(ctx["slice_dir"]).name == "_lmslices"
    assert Path(ctx["slice_dir"]).parent == atlas.parent
    other = Path(tmp_path) / "cache"
    ctx2 = LB.resolve_lightmap_context(
        tmp_path, {}, {"lightmap_texture": str(atlas),
                       "lightmap_slice_dir": str(other)})
    assert ctx2["slice_dir"] == str(other)
    # ...and that is what reaches `wire_lightmap`
    assert LB.wiring_opts(ctx2, {})["lightmap_slice_dir"] == str(other)
    assert LB.wiring_opts(ctx2, {})["pkg_dir"] is None


# =============================================================================
# 6.  the colour space is FORCED, never inherited from Blender's loader
# =============================================================================

class _FakeColorspace:
    def __init__(self, name):
        self.name = name


class _FakeImage:
    """Just enough of `bpy.types.Image` for `_load_image`.

    Mirrors the measured hazard: Blender's DDS loader auto-assigns `'sRGB'` to
    the BC5 AO pair and `'Linear Rec.709'` to BC6H (`engine-confirmed`,
    docs/LIGHTING.md §2.1), and `'STRAIGHT'` alpha un-premultiplies
    RGB.  Both defaults are wrong for at least one lightmap role, so the
    importer must overwrite both and read back what stuck.
    """
    def __init__(self, path):
        self.name = Path(path).name
        self.filepath = str(path)
        self.colorspace_settings = _FakeColorspace("sRGB")     # the wrong default
        self.alpha_mode = "STRAIGHT"                           # the wrong default


class _FakeImages:
    def __init__(self):
        self.loaded = []

    def load(self, path, check_existing=False):
        self.loaded.append((path, check_existing))
        return _FakeImage(path)


class _FakeBpy:
    def __init__(self):
        self.data = type("D", (), {"images": _FakeImages()})()


def test_load_image_forces_the_colour_space_and_channel_packed(tmp_path):
    """⛔ `sRGB` on the HDR map inflates 1.900 -> 4.397 and drops mean luma
    0.2264 -> 0.0768 (`engine-confirmed`; docs/LIGHTING.md).  The loader's default
    must never be inherited."""
    atlas = _synthetic_atlas(tmp_path)
    saved = LB.bpy
    LB.bpy = _FakeBpy()
    try:
        img, actual = LB._load_image(None, str(atlas), LB.COLORSPACE_LIGHTMAP,
                                     LB.COLORSPACE_LIGHTMAP_FALLBACK)
    finally:
        LB.bpy = saved
    assert img is not None
    assert actual == LB.COLORSPACE_LIGHTMAP == "Linear Rec.709"
    assert img.colorspace_settings.name == "Linear Rec.709"
    assert img.alpha_mode == "CHANNEL_PACKED"


def test_load_image_falls_back_when_the_ocio_config_lacks_the_name(tmp_path):
    """A build whose OCIO config has no 'Linear Rec.709' must land on
    'Non-Color' — numerically identical under the stock config —
    and must REPORT which one stuck, never assume the write took."""
    class _Strict(_FakeColorspace):
        def __setattr__(self, k, v):
            if k == "name" and v == "Linear Rec.709":
                raise TypeError("no such colour space in this config")
            object.__setattr__(self, k, v)

    class _StrictImage(_FakeImage):
        def __init__(self, path):
            super().__init__(path)
            object.__setattr__(self, "colorspace_settings", _Strict("sRGB"))

    atlas = _synthetic_atlas(tmp_path)
    fake = _FakeBpy()
    fake.data.images.load = lambda p, check_existing=False: _StrictImage(p)
    saved = LB.bpy
    LB.bpy = fake
    try:
        _, actual = LB._load_image(None, str(atlas), LB.COLORSPACE_LIGHTMAP,
                                   LB.COLORSPACE_LIGHTMAP_FALLBACK)
    finally:
        LB.bpy = saved
    assert actual == LB.COLORSPACE_LIGHTMAP_FALLBACK == "Non-Color"


def test_the_spec_names_the_colour_space_per_role(tmp_path):
    """The spec the import path hands `wire_lightmap` carries the colour space
    for every role, so nothing downstream has to know the DXGI format."""
    if not PKG.is_dir() or not ATLAS.exists():
        return
    ctx = LB.resolve_lightmap_context(PKG, _manifest(), {"lightmap_dir": str(ATLAS_DIR)})
    spec = LB.lightmap_spec_for_object(
        ctx, {"lightmap_index": 1, "lm_slice_index": 3}, {})
    assert spec["color"]["colorspace"] == "Linear Rec.709"
    assert spec["ao0"]["colorspace"] == "Non-Color"
    assert spec["color"]["expected_dxgi"] == 95
    assert spec["color"]["dxgi"] == 95 and spec["color"]["dxgi_unexpected"] is False


# =============================================================================
# 7.  the diagnostic page override, and the failure it exists to render
# =============================================================================

def test_force_page_is_diagnostic_only_and_overrides_every_object():
    """`lightmap_force_page` reproduces the "every mesh renders page 0" bug on
    demand.  It is never set by the operator; it exists so the
    failure mode has a PICTURE (`d3_import_lm_wrongpage.png`) instead of only a
    description."""
    if not PKG.is_dir() or not ATLAS.exists():
        return
    man = _manifest()
    ctx = LB.resolve_lightmap_context(PKG, man, {"lightmap_dir": str(ATLAS_DIR)})
    forced = {o["name"]: LB.lightmap_spec_for_object(
        ctx, o, {"lightmap_force_page": 0})["slice_index"] for o in man["objects"]}
    assert set(forced.values()) == {0}
    # ...and it cannot resurrect a mesh that has no page of its own.
    assert LB.lightmap_spec_for_object(
        ctx, {"lightmap_index": 1, "lm_slice_index": 0xFFFFFFFF},
        {"lightmap_force_page": 0}) == {}


def test_force_page_ignores_a_nonsense_value():
    """A bad override must not silently become page 0 either."""
    if not PKG.is_dir() or not ATLAS.exists():
        return
    ctx = LB.resolve_lightmap_context(PKG, _manifest(), {"lightmap_dir": str(ATLAS_DIR)})
    obj = {"lightmap_index": 1, "lm_slice_index": 6}
    assert LB.lightmap_spec_for_object(
        ctx, obj, {"lightmap_force_page": "banana"})["slice_index"] == 6
    assert LB.lightmap_spec_for_object(
        ctx, obj, {"lightmap_force_page": 0xFFFFFFFF})["slice_index"] == 6


# =============================================================================
# 8.  the operator exposes the option block
# =============================================================================

def _addon_source():
    return (ROOT / "addon" / "lone_echo_import" / "__init__.py").read_text(encoding="utf-8")


def test_operator_exposes_every_lightmap_option():
    """A9's delta §4 asked for this block and it was never built; grepping the
    file for "lightmap" used to return one comment about *lights*."""
    src = _addon_source()
    for prop in ("lightmap_mode", "lightmap_basis", "lightmap_texture",
                 "lightmap_dir", "lightmap_auto_split", "lightmap_slice_dir",
                 "lightmap_intensity", "lightmap_use_ao"):
        assert f"{prop}:" in src, f"IMPORT_OT_lemesh has no {prop} property"
        assert f'"{prop}": ' in src, f"{prop} is not passed into opts"
        assert f'lm.prop(self, "{prop}")' in src or f'layout.prop(self, "{prop}")' in src, \
            f"{prop} is not drawn in the operator UI"


def test_operator_lightmap_mode_offers_exactly_the_three_modes():
    src = _addon_source()
    block = src.split("lightmap_mode: EnumProperty", 1)[1].split("lightmap_texture", 1)[0]
    for mode in LB.MODES:
        assert f'("{mode}",' in block, f"lightmap_mode is missing {mode!r}"
    assert 'default="baked"' in block


def test_operator_uses_the_new_name_first_socket_pattern():
    """Principled sockets were renamed in Blender 4.0 and a hardcoded old name
    silently no-ops (handoff §4b).  Every Principled socket the lightmap path
    touches goes through `_principled_input(new, legacy)`."""
    lb = (ROOT / "addon" / "lone_echo_import" / "lightmap_builder.py").read_text(
        encoding="utf-8")
    assert '_principled_input(bsdf, "Emission Color", "Emission")' in lb
    assert '"Specular IOR Level", "Specular"' in lb
    assert '"Transmission Weight", "Transmission"' in lb
    assert '"Coat Weight", "Clearcoat"' in lb
    assert '"Sheen Weight", "Sheen"' in lb


def test_import_lemesh_resolves_the_context_once_and_reports_it():
    """The 68 MB atlas is resolved per IMPORT, not per mesh, and the summary
    carries the outcome so an unwired lightmap is a reported result."""
    src = _addon_source()
    assert 'opts["lightmap_context"] = lm_ctx' in src
    assert src.count("resolve_lightmap_context(") == 1, \
        "the atlas must be resolved once per import, not once per mesh"
    block = src.split("lightmap = {", 1)[1].split("\n\n", 1)[0]
    for key in ('"mode"', '"available"', '"reason"', '"pages"', '"objects_wired"',
                '"variants"'):
        assert key in block, key


def test_mesh_builder_wires_per_object_not_per_material():
    """The call site: `build_object` derives the spec per MESH and asks for a
    per-(material, page) variant.  `build_material` must NOT call
    `wire_lightmap` -- it has no page to wire."""
    mb = (ROOT / "addon" / "lone_echo_import" / "mesh_builder.py").read_text(encoding="utf-8")
    assert "lightmap_builder.lightmap_spec_for_object(" in mb
    assert "material_builder.lightmap_variant(" in mb
    matb = (ROOT / "addon" / "lone_echo_import" / "material_builder.py").read_text(
        encoding="utf-8")
    body = matb.split("def build_material(", 1)[1].split("\ndef ", 1)[0]
    assert "wire_lightmap" not in body, \
        "build_material cannot wire the lightmap: the page is per-MESH"
