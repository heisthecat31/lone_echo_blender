"""Echo VR resource constants, and the material pipeline end to end.

Synthetic buffers written into a tmp_path that mimics a flat extract, so this
runs with no game data and no pytest.

The point of the pipeline tests is narrow and worth stating: they prove that an
Echo VR material blob reaches `le_mesh.materials.build_material_spec` with roles
attached, and that the resulting sidecar is **v2**. They do NOT prove the roles
are the ones the artist authored -- that depends on cracked inputname hashes and
can only be confirmed against real data.
"""

import json
import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import evr_material_resource as emr
import evr_materials
import evr_resource_types as ert
import evr_texture_resource as etr

from test_evr_material_resource import _bind, _material
from test_evr_texture_resource import _dds, _texture


def _approx(a, b, eps=1e-6):
    if isinstance(a, (list, tuple)):
        return len(a) == len(b) and all(abs(x - y) <= eps for x, y in zip(a, b))
    return abs(a - b) <= eps


# --- resource type constants ------------------------------------------------

def test_mesh_list_hash_is_not_the_instanced_model_hash():
    """The old MESH_DIRS comment conflated these two; they are different types."""
    assert ert.MESH_LIST_RESOURCE == "4e426f88c1b5d7ac"
    assert ert.INSTANCED_MODEL_RESOURCE == "37102e4b27955a14"
    assert ert.MESH_LIST_RESOURCE != ert.INSTANCED_MODEL_RESOURCE


def test_mesh_dirs_preserve_the_proven_geometry_search_order():
    """The old comments were wrong; the ORDER was not, and must not change.

    `decode.extract_mesh` consumes the raw vertex/index payload. Putting the
    stream-0 `CGMeshListResourceWin10` descriptor in this list would break
    models that currently extract, which is why it is excluded on purpose.
    """
    assert ert.MESH_DIRS == (
        ert.MESH_GPU_BUCKET,
        ert.RAW_TEXTURE_PACK,
        ert.UNKNOWN_MESH_BUCKET,
        ert.INSTANCED_MODEL_RESOURCE,
    )
    assert ert.MESH_LIST_RESOURCE not in ert.MESH_DIRS


def test_every_type_hash_is_16_hex_digits():
    for name, value in ert.TYPE_NAMES.items():
        assert len(value) == 16, name
        int(value, 16)          # raises if not hex


def test_type_hashes_are_unique():
    assert len(set(ert.TYPE_NAMES.values())) == len(ert.TYPE_NAMES)


def test_normalise_hash():
    cases = [
        (0xABCD, "000000000000abcd"),
        ("0xABCD", "000000000000abcd"),
        ("ABCD", "000000000000abcd"),
        ("000000000000abcd", "000000000000abcd"),
        (None, ""),
        ("", ""),
    ]
    for raw, expected in cases:
        assert ert.normalise_hash(raw) == expected, raw


def test_resource_path_tolerates_suffix_and_stripped_zeroes(tmp_path):
    directory = tmp_path / ert.MATERIAL_RESOURCE
    directory.mkdir(parents=True)
    (directory / "abc").write_bytes(b"x")
    assert ert.resource_path(tmp_path, ert.MATERIAL_RESOURCE, "abc") is not None
    assert ert.resource_path(tmp_path, ert.MATERIAL_RESOURCE,
                             "0000000000000abc") is not None
    assert ert.resource_path(tmp_path, ert.MATERIAL_RESOURCE, "def") is None


def test_resource_path_finds_dot_bin(tmp_path):
    directory = tmp_path / ert.MATERIAL_RESOURCE
    directory.mkdir(parents=True)
    (directory / "0000000000000abc.bin").write_bytes(b"x")
    assert ert.resource_path(tmp_path, ert.MATERIAL_RESOURCE, "abc") is not None


# --- a synthetic flat extract ----------------------------------------------

TEX_BASE = 0xB0
TEX_NORMAL = 0xB1
MAT_HASH = 0xEE01
MODEL_HASH = 0xDD01

INPUT_DIFFUSE = 0x1001
INPUT_NORMAL = 0x1002

#: ⚠ `layer0_diffuse_map` is NOT a routable role, despite being the string the
#: original `evr_scene_extract.py` wrote into `basecolor_role`.
#: `materials.CHANNEL_ROLE_SUFFIXES["base_color"]` is exactly
#: `("composite_diffuse", "albedo_map")` -- a role whose suffix is not in that
#: tuple never reaches the Base Color socket. See
#: `test_diffuse_map_is_not_a_real_base_colour_role`.
NAMES = {INPUT_DIFFUSE: "layer0_albedo_map", INPUT_NORMAL: "layer0_normal_map"}

MODEL = ert.normalise_hash(MODEL_HASH)
MAT = ert.normalise_hash(MAT_HASH)
BASE = ert.normalise_hash(TEX_BASE)
NORMAL = ert.normalise_hash(TEX_NORMAL)


def _write(root, type_hash, resource_hash, blob):
    directory = root / type_hash
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ert.normalise_hash(resource_hash)).write_bytes(blob)


def _mesh_list(material_hashes, *, material_offset=16):
    """A CGMeshListResourceWin10 whose CGMeshData records carry a material hash."""
    out = bytearray(struct.pack("<I", len(material_hashes)))
    for material_hash in material_hashes:
        record = bytearray(evr_materials.MESH_STRIDE)
        struct.pack_into("<Q", record, material_offset, material_hash)
        out += record
    return bytes(out)


def _extract(tmp_path):
    """A minimal flat extract: one model, one material, two textures."""
    _write(tmp_path, ert.TEXTURE_RESOURCE, TEX_BASE,
           _texture(fmt=99, inline=_dds()))      # BC7_UNORM_SRGB
    _write(tmp_path, ert.TEXTURE_RESOURCE, TEX_NORMAL,
           _texture(fmt=83, inline=_dds()))      # BC5_UNORM
    _write(tmp_path, ert.MATERIAL_RESOURCE, MAT_HASH, _material(
        mattype=1, blendmode=0,
        aux_blob=(_bind(INPUT_DIFFUSE, TEX_BASE, slot=0)
                  + _bind(INPUT_NORMAL, TEX_NORMAL, slot=1)),
    ))
    _write(tmp_path, ert.MESH_LIST_RESOURCE, MODEL_HASH, _mesh_list([MAT_HASH]))
    return tmp_path


def _ctx(tmp_path, *, named=True):
    ctx = evr_materials.build_context(tmp_path, [MODEL])
    if named:
        ctx.names = NAMES
    return ctx


# --- corpus discovery -------------------------------------------------------

def test_finds_materials_and_textures(tmp_path):
    _extract(tmp_path)
    assert emr.all_material_hashes(tmp_path) == {MAT}
    assert emr.all_texture_hashes(tmp_path) == {BASE, NORMAL}


def test_missing_directories_yield_empty_sets(tmp_path):
    assert emr.all_material_hashes(tmp_path) == set()
    assert emr.all_texture_hashes(tmp_path) == set()


def test_context_warns_when_the_extract_is_empty(tmp_path):
    ctx = evr_materials.build_context(tmp_path, [])
    assert any("CGMaterialResourceWin10" in w for w in ctx.warnings)
    assert any("cgtextureresourceWin10" in w for w in ctx.warnings)


# --- the CGMeshData material probe -----------------------------------------

def test_probe_finds_the_planted_material_offset(tmp_path):
    _extract(tmp_path)
    report = evr_materials.probe_mesh_material_offset(
        tmp_path, [MODEL], emr.all_material_hashes(tmp_path))
    assert report["offset"] == 16
    assert report["hits"] == 1
    assert report["records_examined"] == 1


def test_probe_reports_low_confidence_when_nothing_matches(tmp_path):
    _write(tmp_path, ert.MESH_LIST_RESOURCE, MODEL_HASH, _mesh_list([0x9999]))
    report = evr_materials.probe_mesh_material_offset(
        tmp_path, [MODEL], {MAT})
    assert not report["confident"]
    assert report["hits"] == 0


def test_probe_ignores_stub_mesh_lists(tmp_path):
    _write(tmp_path, ert.MESH_LIST_RESOURCE, MODEL_HASH, b"\x00" * 56)
    report = evr_materials.probe_mesh_material_offset(tmp_path, [MODEL], {MAT})
    assert report["records_examined"] == 0


def test_probe_is_confident_given_enough_agreeing_records(tmp_path):
    """One record is not evidence; a corpus that agrees at one offset is.

    The confidence rule is "covers >=60% of records and at least double the
    runner-up", so a single planted record deliberately does NOT clear it.
    """
    materials = set()
    models = []
    for i in range(5):
        material_hash = 0xEE00 + i
        materials.add(ert.normalise_hash(material_hash))
        _write(tmp_path, ert.MATERIAL_RESOURCE, material_hash, _material())
        model = 0xDD00 + i
        models.append(ert.normalise_hash(model))
        _write(tmp_path, ert.MESH_LIST_RESOURCE, model, _mesh_list([material_hash]))

    report = evr_materials.probe_mesh_material_offset(tmp_path, models, materials)
    assert report["offset"] == 16
    assert report["records_examined"] == 5
    assert report["coverage"] == 1.0
    assert report["confident"] is True


def test_single_record_is_not_treated_as_confident(tmp_path):
    _extract(tmp_path)
    report = evr_materials.probe_mesh_material_offset(
        tmp_path, [MODEL], emr.all_material_hashes(tmp_path))
    assert report["hits"] == 1
    assert report["confident"] is False


def test_materials_for_model_reads_the_planted_hash(tmp_path):
    _extract(tmp_path)
    assert evr_materials.materials_for_model(
        tmp_path, MODEL, emr.all_material_hashes(tmp_path), 16) == [MAT]


def test_materials_for_model_returns_empty_without_an_offset(tmp_path):
    _extract(tmp_path)
    assert evr_materials.materials_for_model(
        tmp_path, MODEL, emr.all_material_hashes(tmp_path), None) == []


# --- DXGI map ---------------------------------------------------------------

def test_dxgi_map_reports_real_formats(tmp_path):
    _extract(tmp_path)
    mapping = etr.dxgi_map(tmp_path, [TEX_BASE, TEX_NORMAL, 0xDEAD])
    assert mapping[BASE] == 99
    assert mapping[NORMAL] == 83
    assert ert.normalise_hash(0xDEAD) not in mapping   # absent, not guessed


# --- spec construction ------------------------------------------------------

def test_diffuse_map_is_not_a_real_base_colour_role(tmp_path):
    """Pins the naming bug the original extractor shipped.

    `evr_scene_extract.build_materials_for_model` wrote
    `"basecolor_role": "layer0_diffuse_map"`. That suffix is not in
    `CHANNEL_ROLE_SUFFIXES["base_color"]`, so it routes NOWHERE -- the texture
    would have been dropped rather than bound, on top of the grouping being
    wrong. The two real base-colour suffixes are asserted here so a future
    rename cannot quietly reintroduce it.
    """
    from le_mesh import materials as le_materials

    assert le_materials.CHANNEL_ROLE_SUFFIXES["base_color"] == (
        "composite_diffuse", "albedo_map")
    assert "diffuse_map" not in le_materials.CHANNEL_ROLE_SUFFIXES["base_color"]

    _extract(tmp_path)
    ctx = evr_materials.build_context(tmp_path, [MODEL])
    ctx.names = {INPUT_DIFFUSE: "layer0_diffuse_map",
                 INPUT_NORMAL: "layer0_normal_map"}
    spec = evr_materials.build_spec(ctx, MAT)
    assert "layer0_diffuse_map" in spec["unrouted_roles"]


def test_build_spec_routes_roles_into_principled_channels(tmp_path):
    _extract(tmp_path)
    spec = evr_materials.build_spec(_ctx(tmp_path), MAT)
    assert spec["channels"]["base_color"]["texture"] == BASE
    assert spec["channels"]["normal"]["texture"] == NORMAL


def test_normal_channel_carries_bc5_reconstruction(tmp_path):
    _extract(tmp_path)
    normal = evr_materials.build_spec(_ctx(tmp_path), MAT)["channels"]["normal"]
    assert normal["dxgi"] == 83
    assert normal["reconstruct_z"] is True


def test_base_color_is_srgb_and_normal_is_not(tmp_path):
    _extract(tmp_path)
    channels = evr_materials.build_spec(_ctx(tmp_path), MAT)["channels"]
    assert channels["base_color"]["colorspace"] == "sRGB"
    assert channels["normal"]["colorspace"] == "Non-Color"


def test_spec_carries_the_fields_the_addon_reads(tmp_path):
    """`material_builder` is handed this dict verbatim, so shape matters."""
    _extract(tmp_path)
    spec = evr_materials.build_spec(_ctx(tmp_path), MAT)
    for key in ("channels", "layers", "material_hash", "mattype",
                "base_color_factor", "render_mode", "alpha", "unrouted_roles"):
        assert key in spec, key


def test_unnamed_inputs_still_route_by_dxgi_fallback(tmp_path):
    """No hash_lookup: roles become `unknown_s{slot}` and DXGI must rescue them.

    This is the realistic case until inputname hashes are cracked, so it is
    worth pinning: a BC5 texture still lands in `normal`, not `base_color`.
    """
    _extract(tmp_path)
    spec = evr_materials.build_spec(_ctx(tmp_path, named=False), MAT)
    channels = spec["channels"]
    assert channels["normal"]["texture"] == NORMAL
    assert channels["normal"].get("inferred_from") == "dxgi"
    assert channels["base_color"]["texture"] == BASE


def test_named_roles_are_declared_and_unnamed_ones_are_not(tmp_path):
    """A guessed role must never read as a declared one.

    With names, both binds resolve to real role keys -> SOURCE_ARRAY. Without
    them the roles are `unknown_s{slot}` and the channel is chosen by DXGI, so
    the provenance has to say SOURCE_FORMAT instead.
    """
    from le_mesh import role_index

    _extract(tmp_path)

    named = evr_materials.build_spec(_ctx(tmp_path), MAT)["role_sources"]
    assert set(named.values()) == {role_index.SOURCE_ARRAY}

    unnamed = evr_materials.build_spec(
        _ctx(tmp_path, named=False), MAT)["role_sources"]
    assert set(unnamed.values()) == {role_index.SOURCE_FORMAT}


def test_unknown_material_returns_none(tmp_path):
    _extract(tmp_path)
    assert evr_materials.build_spec(_ctx(tmp_path), "dead") is None


def test_double_sided_comes_from_the_flag(tmp_path):
    from le_mesh import material_scalars as msc

    _write(tmp_path, ert.MATERIAL_RESOURCE, MAT_HASH,
           _material(flags=msc.EFLAGS["eDoubleSided"]))
    spec = evr_materials.build_spec(evr_materials.build_context(tmp_path, []), MAT)
    assert spec["double_sided"] is True


def test_bakecolor_becomes_the_base_color_factor(tmp_path):
    _write(tmp_path, ert.MATERIAL_RESOURCE, MAT_HASH,
           _material(bakecolor=(0.1, 0.2, 0.3, 1.0)))
    spec = evr_materials.build_spec(evr_materials.build_context(tmp_path, []), MAT)
    assert _approx(spec["base_color_factor"][:3], [0.1, 0.2, 0.3])


def test_k_alpha_is_read_from_materialprops(tmp_path):
    from le_mesh import material_scalars as msc

    _write(tmp_path, ert.MATERIAL_RESOURCE, MAT_HASH, _material(
        props=(0.25,), propoffsets=((msc.HASH_K_ALPHA, 0),)))
    spec = evr_materials.build_spec(evr_materials.build_context(tmp_path, []), MAT)
    assert _approx(spec["alpha"], 0.25)


def test_absent_scalars_fall_back_to_authored_defaults(tmp_path):
    from le_mesh import material_scalars as msc

    _write(tmp_path, ert.MATERIAL_RESOURCE, MAT_HASH, _material())
    spec = evr_materials.build_spec(evr_materials.build_context(tmp_path, []), MAT)
    assert _approx(spec["alpha"], msc.AUTHORED_DEFAULTS_GLOBAL["k_alpha"])
    assert _approx(spec["emissive_scale"],
                   msc.AUTHORED_DEFAULTS_GLOBAL["k_emissive_scale"])


def test_emissive_color_is_zero_because_echo_vr_has_no_bakeemissivecolor(tmp_path):
    """Echo VR's header genuinely lacks the field; inventing one would be a lie."""
    _write(tmp_path, ert.MATERIAL_RESOURCE, MAT_HASH, _material())
    spec = evr_materials.build_spec(evr_materials.build_context(tmp_path, []), MAT)
    assert spec["emissive_color"] == [0.0, 0.0, 0.0]


# --- texture extraction -----------------------------------------------------

def test_textures_are_written_and_recorded(tmp_path):
    _extract(tmp_path)
    out = tmp_path / "out" / "textures"
    written = evr_materials.extract_textures(_ctx(tmp_path), [TEX_BASE, TEX_NORMAL], out)

    assert set(written) == {BASE, NORMAL}
    for tex_hash, relative in written.items():
        assert relative == f"textures/{tex_hash}.dds"
        assert (out / f"{tex_hash}.dds").exists()


def test_texture_files_feed_the_spec_file_paths(tmp_path):
    _extract(tmp_path)
    spec = evr_materials.build_spec(
        _ctx(tmp_path), MAT, out_dir=tmp_path / "out" / "textures")
    assert spec["channels"]["base_color"]["file"].endswith(".dds")
    assert spec["channels"]["normal"]["file"].endswith(".dds")


def test_missing_texture_is_skipped_not_faked(tmp_path):
    _extract(tmp_path)
    assert evr_materials.extract_textures(
        _ctx(tmp_path), [0xDEAD], tmp_path / "out") == {}


# --- the sidecar ------------------------------------------------------------

def _sidecar(tmp_path):
    ctx = _ctx(tmp_path)
    table = evr_materials.MaterialTable()
    table.intern(ctx, MAT, out_dir=tmp_path / "t")
    return table, ctx, table.to_sidecar("beef", ctx)


def test_sidecar_is_version_two(tmp_path):
    _extract(tmp_path)
    _table, _ctx_, sidecar = _sidecar(tmp_path)
    assert sidecar["version"] == 2
    assert sidecar["materials"][0]["matidx"] == 0
    assert "spec" in sidecar["materials"][0]


def test_sidecar_v1_fields_are_derived_from_the_spec(tmp_path):
    """v1 and v2 cannot disagree, because v1 is projected from v2."""
    _extract(tmp_path)
    _table, _ctx_, sidecar = _sidecar(tmp_path)
    entry = sidecar["materials"][0]
    assert entry["basecolor_texture"] == entry["spec"]["channels"]["base_color"]["texture"]
    assert entry["normal_texture"] == entry["spec"]["channels"]["normal"]["texture"]


def test_a_shared_material_is_interned_once(tmp_path):
    _extract(tmp_path)
    ctx = _ctx(tmp_path)
    table = evr_materials.MaterialTable()
    first = table.intern(ctx, MAT, out_dir=tmp_path / "t")
    second = table.intern(ctx, MAT, out_dir=tmp_path / "t")
    assert first == second == 0
    assert len(table.entries) == 1


def test_sidecar_is_json_serialisable(tmp_path):
    _extract(tmp_path)
    _table, _ctx_, sidecar = _sidecar(tmp_path)
    json.dumps(sidecar)       # raises on anything non-serialisable


def test_sidecar_records_the_probe_for_audit(tmp_path):
    _extract(tmp_path)
    _table, ctx, sidecar = _sidecar(tmp_path)
    diagnostics = sidecar["diagnostics"]
    assert "mesh_material_probe" in diagnostics
    assert diagnostics["mesh_material_offset"] == ctx.mesh_material_offset
    assert diagnostics["materials_built"] == 1


# --- the texture-overlap fallback ------------------------------------------

def test_overlap_fallback_ranks_the_owning_material(tmp_path):
    _extract(tmp_path)
    ranked = evr_materials.materials_by_texture_overlap(
        _ctx(tmp_path), [TEX_BASE, TEX_NORMAL])
    assert ranked[0][0] == MAT
    assert ranked[0][1] == 2          # binds both of the model's textures


def test_overlap_fallback_is_empty_without_textures(tmp_path):
    _extract(tmp_path)
    assert evr_materials.materials_by_texture_overlap(_ctx(tmp_path), []) == []


def test_owner_index_is_cached(tmp_path):
    _extract(tmp_path)
    ctx = _ctx(tmp_path)
    first = evr_materials.build_texture_owner_index(ctx)
    assert evr_materials.build_texture_owner_index(ctx) is first


# --- hash_lookup ------------------------------------------------------------

def test_hash_lookup_accepts_both_key_spellings(tmp_path):
    path = tmp_path / "hash_lookup.json"
    path.write_text(json.dumps({"0xAABB": "layer0_diffuse_map",
                                "ccdd": "layer0_normal_map",
                                "bad": 12}))
    names = evr_materials.load_hash_lookup(path)
    assert names[0xAABB] == "layer0_diffuse_map"
    assert names[0xCCDD] == "layer0_normal_map"
    assert 12 not in names.values()


def test_missing_hash_lookup_is_not_an_error(tmp_path):
    assert evr_materials.load_hash_lookup(tmp_path / "nope.json") == {}
    assert evr_materials.load_hash_lookup(None) == {}


# --- DXGI fallback round-robin (§7.1: "several models wear the same texture")

def test_roles_from_texture_list_gives_sibling_materials_different_textures(tmp_path):
    """The bug: every material on a model that fell through to DXGI classifying
    got the identical FIRST BC5 / FIRST sRGB texture, because the fallback had
    no notion of "which material is this for" -- only "what is this model's
    texture list". Two unrelated texture SETS (a "family" each, per
    `_texture_families` -- see its docstring), rank 0 vs rank 1 must land on
    a DIFFERENT family, not repeat the first.

    `tex_a_*` share an 8-hex-char prefix with each other (one family) and
    `tex_b_*` share a DIFFERENT 8-hex-char prefix (a second family), with no
    accidental prefix/suffix overlap between the two -- real texture-set
    hashes cluster the same way (`_texture_families`'s docstring has the
    measured examples).
    """
    tex_a_normal, tex_a_base = 0xAAAAAAAA00000001, 0xAAAAAAAA00000002
    tex_b_normal, tex_b_base = 0xBBBBBBBB00001001, 0xBBBBBBBB00001002
    _write(tmp_path, ert.TEXTURE_RESOURCE, tex_a_normal, _texture(fmt=83, inline=_dds()))
    _write(tmp_path, ert.TEXTURE_RESOURCE, tex_b_normal, _texture(fmt=84, inline=_dds()))
    _write(tmp_path, ert.TEXTURE_RESOURCE, tex_a_base, _texture(fmt=99, inline=_dds()))
    _write(tmp_path, ert.TEXTURE_RESOURCE, tex_b_base, _texture(fmt=72, inline=_dds()))

    ctx = evr_materials.MaterialContext(root=tmp_path)
    model_textures = [tex_a_normal, tex_a_base, tex_b_normal, tex_b_base]

    rank0 = evr_materials.roles_from_texture_list(ctx, model_textures, rank=0)
    rank1 = evr_materials.roles_from_texture_list(ctx, model_textures, rank=1)

    assert rank0[evr_materials.DXGI_ROLE_NORMAL] == ert.normalise_hash(tex_a_normal)
    assert rank1[evr_materials.DXGI_ROLE_NORMAL] == ert.normalise_hash(tex_b_normal)
    assert rank0[evr_materials.DXGI_ROLE_BASE_COLOR] == ert.normalise_hash(tex_a_base)
    assert rank1[evr_materials.DXGI_ROLE_BASE_COLOR] == ert.normalise_hash(tex_b_base)


def test_texture_families_clusters_by_shared_prefix_or_suffix(tmp_path):
    """The other half of the fix: textures that DO belong together must land
    in the SAME family, whether they share a long prefix or a long suffix
    (both patterns are confirmed real -- see `_texture_families`'s docstring).
    """
    prefix_family = ["c29a7d30d8154550", "c29a7d30d813444b", "c29a7d30d81e4e56"]
    suffix_family = ["e42a132ddab1e343", "e42a132edab1e343", "e42a132fdab1e343"]
    clusters = evr_materials._texture_families(prefix_family + suffix_family)
    assert sorted(map(sorted, clusters)) == sorted(
        map(sorted, [prefix_family, suffix_family]))


def test_lod_propagation_is_scoped_to_the_confirming_model(monkeypatch):
    """Propagation fires ONLY for the model a material was confirmed on.

    The bug this pins: `materials_for_model`'s file scan was a raw byte search,
    so one material's hash surfaced inside unrelated models' files too, and
    those models' OWN LOD clustering grouped it with DIFFERENT materials.
    Propagating on an unrelated model's cluster membership handed unconfirmed
    materials a confirmed answer for the wrong reason.

    `CONFIRMED_MATERIAL_ROLES` is now EMPTY -- the real per-material texture
    table (`evr_material_textures`) and the real per-model palette
    (`evr_model_materials`) superseded the one hand-confirmation, which turned
    out to be attributed to the wrong material. The override MECHANISM is still
    live for a future genuine confirmation, so this test injects its own data
    rather than depending on shipped entries.
    """
    confirmed_hash, real_model = "aaaa000000000001", "model_confirmed_on"
    roles = {"layer0_albedo_map": "cccc000000000001"}
    monkeypatch.setattr(evr_materials, "CONFIRMED_MATERIAL_ROLES",
                        {confirmed_hash: roles})
    monkeypatch.setattr(evr_materials, "CONFIRMED_MATERIAL_MODEL",
                        {confirmed_hash: real_model})

    material_lod_group = {confirmed_hash: 0, "sibling_a": 0}
    materials_by_group = {0: [confirmed_hash, "sibling_a"]}

    # The model actually confirmed on: propagation fires.
    extended = evr_materials.propagate_confirmed_roles_to_lod_siblings(
        real_model, material_lod_group, materials_by_group)
    assert extended.get("sibling_a") == roles

    # A DIFFERENT model whose own clustering happens to contain the same hash:
    # propagation must NOT fire, even though the shapes look identical.
    extended_wrong_model = evr_materials.propagate_confirmed_roles_to_lod_siblings(
        "some_other_unrelated_model", material_lod_group, materials_by_group)
    assert "sibling_a" not in extended_wrong_model


def test_roles_from_texture_list_wraps_rank_when_candidates_run_out(tmp_path):
    """More materials than candidates of a class: wrap, don't crash or drop it."""
    tex_normal = 0xC010
    _write(tmp_path, ert.TEXTURE_RESOURCE, tex_normal, _texture(fmt=83, inline=_dds()))
    ctx = evr_materials.MaterialContext(root=tmp_path)

    rank0 = evr_materials.roles_from_texture_list(ctx, [tex_normal], rank=0)
    rank5 = evr_materials.roles_from_texture_list(ctx, [tex_normal], rank=5)
    assert rank0[evr_materials.DXGI_ROLE_NORMAL] == ert.normalise_hash(tex_normal)
    assert rank5[evr_materials.DXGI_ROLE_NORMAL] == ert.normalise_hash(tex_normal)


def test_intern_keys_on_rank_so_sibling_materials_get_separate_specs(tmp_path):
    """Without `rank` in the cache key, the second material of a model would
    reuse the first material's memoized (and identically-DXGI-guessed) spec
    the instant they shared a (material_hash, shaderset_hash) pair -- which two
    DISTINCT materials on the same model with no shader set always do.
    """
    tex_a_normal, tex_b_normal = 0xC021000000000021, 0xD022000000000022
    _write(tmp_path, ert.TEXTURE_RESOURCE, tex_a_normal, _texture(fmt=83, inline=_dds()))
    _write(tmp_path, ert.TEXTURE_RESOURCE, tex_b_normal, _texture(fmt=84, inline=_dds()))
    mat_a, mat_b = 0xEE10, 0xEE11
    _write(tmp_path, ert.MATERIAL_RESOURCE, mat_a, _material())
    _write(tmp_path, ert.MATERIAL_RESOURCE, mat_b, _material())

    ctx = evr_materials.build_context(tmp_path, [])
    table = evr_materials.MaterialTable()
    model_textures = [tex_a_normal, tex_b_normal]

    idx_a = table.intern(ctx, mat_a, model_textures=model_textures, rank=0)
    idx_b = table.intern(ctx, mat_b, model_textures=model_textures, rank=1)

    assert idx_a != idx_b
    spec_a = table.entries[idx_a]["spec"]
    spec_b = table.entries[idx_b]["spec"]
    assert (spec_a["channels"]["normal"]["texture"]
            != spec_b["channels"]["normal"]["texture"])


# --- summary ----------------------------------------------------------------

def test_summarise_mentions_counts(tmp_path):
    _extract(tmp_path)
    _table, _ctx_, sidecar = _sidecar(tmp_path)
    text = evr_materials.summarise(sidecar)
    assert "materials" in text
    assert "base_color" in text
