"""The corpus-wide `tex_hash -> role` index and its conflict policy.

Fixtures are synthetic rows built here — no game file, no Oodle — so this runs
under WSL `python3` with the rest of the core suite. The policy under test, and
the conflict rates it is calibrated against, are `stream-confirmed` on
`generic_rebuilds/role_index.tsv` (25,694 binds / 138 archives / 4,507
shadersets / 2,194 distinct textures): 90 textures carry more than one role, of
which **74 differ only in the layer index** and **16 differ in the suffix**. See
docs/MATERIALS.md.

The load-bearing distinction: the Principled channel is chosen by the role's
SUFFIX (`materials.CHANNEL_ROLE_SUFFIXES`), so a layer-index disagreement still
lands the texture in the right socket, while a suffix disagreement would route a
normal map into Base Color. The index therefore applies a role only when the
corpus agrees on the suffix.
"""

import csv

from le_mesh import materials as mat
from le_mesh import role_index as ridx


def _rows(*triples):
    """`(tex, role, shaderset)` -> the dict rows `index_from_rows` consumes."""
    return [dict(tex_hash=t, role=r, shaderset_hash=s, archive_hash="a", slot="0")
            for t, r, s in triples]


# --- the three outcomes -----------------------------------------------------

def test_unanimous_role_is_applied():
    idx = ridx.index_from_rows(_rows(
        ("aa", "layer0_composite_diffuse", "s1"),
        ("aa", "layer0_composite_diffuse", "s2"),
    ))
    assert idx.resolve("aa") == ("layer0_composite_diffuse", ridx.STATUS_UNANIMOUS)
    assert idx.roles_for("aa") == {"layer0_composite_diffuse": 2}


def test_layer_only_conflict_applies_the_majority_layer():
    """74 of the 90 corpus conflicts are this shape, 50 of them on a
    `generated_composite_*` used at layer0 by one material and layer1 by another.
    The suffix — and so the Principled channel — is identical either way."""
    idx = ridx.index_from_rows(_rows(
        ("bb", "layer1_composite_diffuse", "s1"),
        ("bb", "layer1_composite_diffuse", "s2"),
        ("bb", "layer0_composite_diffuse", "s3"),
    ))
    role, status = idx.resolve("bb")
    assert status == ridx.STATUS_LAYER_AMBIGUOUS
    assert role == "layer1_composite_diffuse"
    assert mat.split_role(role)[1] == "composite_diffuse"


def test_suffix_conflict_is_refused_not_guessed():
    """⛔ The 16 corpus cases are all reusable greyscale utility maps —
    `fx_cmn_scrolling_noise_swirls_liquid_clr` is bound as albedo / alpha / blend
    mask / emissive by 38 shadersets. There is no right answer to pick."""
    idx = ridx.index_from_rows(_rows(
        ("cc", "layer0_albedo_map", "s1"),
        ("cc", "layer0_alpha_map", "s2"),
        ("cc", "layer1_blend_mask", "s3"),
    ))
    assert idx.resolve("cc") == (None, ridx.STATUS_SUFFIX_CONFLICT)
    assert ridx.STATUS_SUFFIX_CONFLICT not in ridx.APPLICABLE
    # the disagreement is still readable — refused, not discarded
    assert len(idx.roles_for("cc")) == 3


def test_absent_texture_resolves_to_nothing():
    idx = ridx.index_from_rows(_rows(("aa", "layer0_albedo_map", "s1")))
    assert idx.resolve("ffffffffffffffff") == (None, ridx.STATUS_ABSENT)
    assert "ffffffffffffffff" not in idx


# --- the vote ---------------------------------------------------------------

def test_votes_count_distinct_shadersets_not_rows():
    """A shaderset resource is byte-shared across archives, so counting ROWS
    would weight one shaderset by how many archives happen to embed it. Here the
    layer0 reading appears in 3 archives but only 1 shaderset, and must lose to
    the 2 genuinely different shadersets that say layer1."""
    rows = _rows(("dd", "layer1_composite_normals", "s1"),
                 ("dd", "layer1_composite_normals", "s2"))
    rows += [dict(tex_hash="dd", role="layer0_composite_normals",
                  shaderset_hash="s9", archive_hash=a, slot="0")
             for a in ("a1", "a2", "a3")]
    idx = ridx.index_from_rows(rows)
    assert idx.roles_for("dd") == {"layer1_composite_normals": 2,
                                   "layer0_composite_normals": 1}
    assert idx.resolve("dd")[0] == "layer1_composite_normals"


def test_layer_tie_breaks_deterministically_on_the_lowest_layer():
    """Row order must not decide a binding. With equal votes the lowest layer
    index wins, both ways round."""
    a = ridx.index_from_rows(_rows(("ee", "layer2_blend_mask", "s1"),
                                   ("ee", "layer1_blend_mask", "s2")))
    b = ridx.index_from_rows(_rows(("ee", "layer1_blend_mask", "s2"),
                                   ("ee", "layer2_blend_mask", "s1")))
    assert a.resolve("ee") == b.resolve("ee")
    assert a.resolve("ee")[0] == "layer1_blend_mask"


def test_resolve_is_case_insensitive_and_cached():
    idx = ridx.index_from_rows(_rows(("ab12", "layer0_normal_map", "s1")))
    assert idx.resolve("AB12")[0] == "layer0_normal_map"
    assert idx.resolve("ab12")[0] == "layer0_normal_map"


# --- the artifact contract --------------------------------------------------

def test_missing_tsv_yields_an_empty_index_not_an_error(tmp_path):
    """Same contract as `load_global_texture_index` / `load_global_material_index`:
    a tree that has not built the artifact yet resolves fewer roles, it does not
    crash."""
    idx = ridx.load_role_index(tmp_path / "nope.tsv")
    assert len(idx) == 0
    assert idx.resolve("aa") == (None, ridx.STATUS_ABSENT)


def test_tsv_round_trip_matches_the_builder_columns(tmp_path):
    """The columns `scripts/le_role_index.py` writes are the columns the
    reader consumes — a rename on either side must fail here, not silently
    produce an empty index."""
    path = tmp_path / "role_index.tsv"
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, delimiter="\t", lineterminator="\n",
                           fieldnames=["archive_hash", "shaderset_hash", "tex_hash",
                                       "role", "slot", "n_rows"])
        w.writeheader()
        w.writerow(dict(archive_hash="2fd6839161785e9c",
                        shaderset_hash="49a960afce4d4f2b",
                        tex_hash="85e08905201cadc1", role="layer1_blend_mask",
                        slot=27, n_rows=1))
    idx = ridx.load_role_index(path)
    assert len(idx) == 1
    assert idx.resolve("85e08905201cadc1") == ("layer1_blend_mask",
                                               ridx.STATUS_UNANIMOUS)


def test_stats_counts_multi_role_and_splits_it_by_suffix():
    idx = ridx.index_from_rows(_rows(
        ("aa", "layer0_composite_diffuse", "s1"),           # clean
        ("bb", "layer0_composite_diffuse", "s1"),           # layer-only
        ("bb", "layer1_composite_diffuse", "s2"),
        ("cc", "layer0_albedo_map", "s1"),                  # suffix conflict
        ("cc", "layer0_alpha_map", "s2"),
    ))
    assert idx.stats() == {"textures": 3, "pairs": 5, "multi_role": 2,
                           "suffix_conflict": 1, "layer_only": 1}


# --- the policy's own invariants --------------------------------------------

def test_every_role_the_index_can_emit_is_a_known_inputname():
    """The builder anchors on `materials.ROLE_BY_INPUTNAME`, every entry of which
    is a verified preimage (`test_materials.test_every_role_name_hashes_to_its_own_key`).
    So an index role is always a real inputname — never an invented one."""
    known = {v[0] for v in mat.ROLE_BY_INPUTNAME.values()}
    idx = ridx.index_from_rows(_rows(("aa", "layer2_composite_specular", "s1")))
    role, _status = idx.resolve("aa")
    assert role in known


def test_applicable_statuses_are_exactly_the_ones_that_return_a_role():
    """The guard the extractor relies on: a status outside `APPLICABLE` must
    never come back with a role attached."""
    cases = ridx.index_from_rows(_rows(
        ("aa", "layer0_albedo_map", "s1"),
        ("bb", "layer0_albedo_map", "s1"), ("bb", "layer1_albedo_map", "s2"),
        ("cc", "layer0_albedo_map", "s1"), ("cc", "layer0_normal_map", "s2"),
    ))
    for tex in ("aa", "bb", "cc", "zz"):
        role, status = cases.resolve(tex)
        assert (role is not None) == (status in ridx.APPLICABLE), (tex, role, status)


def test_source_labels_are_distinct():
    """Provenance must be able to tell an array-DECLARED role from a
    corpus-VOTED one; four sources, four distinct labels."""
    labels = {ridx.SOURCE_ARRAY, ridx.SOURCE_ARCHIVE,
              ridx.SOURCE_CORPUS, ridx.SOURCE_RDEF}
    assert len(labels) == 4


def test_material_spec_carries_role_sources_and_ambiguity():
    """The audit keys are ALWAYS present (often `{}`) so the `.lemesh` and level
    sidecar specs keep identical key sets."""
    empty = mat.build_material_spec("k", role_textures={}, dxgi_by_tex={})
    assert empty["role_sources"] == {} and empty["role_ambiguity"] == {}
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_composite_diffuse": "aa", "rdef_bind23": "cc"},
        dxgi_by_tex={},
        role_sources={"layer0_composite_diffuse": ridx.SOURCE_CORPUS,
                      "rdef_bind23": ridx.SOURCE_RDEF},
        role_ambiguity={"cc": {"layer0_albedo_map": 3, "layer0_alpha_map": 2}})
    assert spec["role_sources"]["layer0_composite_diffuse"] == ridx.SOURCE_CORPUS
    assert spec["role_ambiguity"]["cc"]["layer0_alpha_map"] == 2
    # a refused bind still lands in the unrouted residue, never in a channel
    assert "rdef_bind23" in spec["unrouted_roles"]
    assert all(ch.get("texture") != "cc" for ch in spec["channels"].values())


# ---------------------------------------------------------------------------
# step 4 -- composite roles recovered from the TEXTURE FORMAT
#
# The last route for a `generated_composite_*` bind no array declares anywhere.
# Policy lives in `le_mesh.materials.composite_roles_from_format` and is pure, so
# it is testable without a game file. Held-out measurement against the shipped
# arrays (461 shadersets, arrays hidden): 830 fired / 494 refused / 0 wrong.
# ---------------------------------------------------------------------------

from le_mesh import materials as _M       # noqa: E402

BC5, BC1_SRGB, BC3_SRGB, BC3, BC1, BC4 = 83, 72, 78, 77, 71, 80


def _meta(**kw):
    return {t: {"dxgi": d, "width": w, "height": h} for t, (d, w, h) in kw.items()}


def test_the_four_composite_classes_are_recovered_from_format():
    """Liv's gloves, `af000261129c23bd`: five 2048^2 binds, zero routed before."""
    binds = {18: "n", 19: "d", 20: "s0", 21: "s1", 22: "c"}
    meta = _meta(n=(BC5, 2048, 2048), d=(BC1_SRGB, 2048, 2048),
                 s0=(BC3_SRGB, 2048, 2048), s1=(BC3_SRGB, 2048, 2048),
                 c=(BC3, 2048, 2048))
    out = _M.composite_roles_from_format(binds, meta)
    assert out["roles"] == {18: "layer0_composite_normals",
                            19: "layer0_composite_diffuse",
                            22: "layer0_composite_components"}
    # two BC3_UNORM_SRGB in one group == {specular, data0} and no tie-break
    # exists (register(specular) < register(data0) is 23 vs 21 corpus-wide)
    assert out["refused"] == {20: _M.REFUSE_NOT_UNIQUE, 21: _M.REFUSE_NOT_UNIQUE}
    assert out["layer"] == 0


def test_a_clean_quartet_resolves_all_four():
    binds = {0: "n", 1: "d", 2: "s", 3: "c"}
    meta = _meta(n=(BC5, 1024, 1024), d=(BC1_SRGB, 1024, 1024),
                 s=(BC3_SRGB, 1024, 1024), c=(BC1, 1024, 1024))
    out = _M.composite_roles_from_format(binds, meta)
    assert out["roles"] == {0: "layer0_composite_normals",
                            1: "layer0_composite_diffuse",
                            2: "layer0_composite_specular",
                            3: "layer0_composite_components"}
    assert out["refused"] == {}


def test_the_layer_is_the_lowest_unclaimed_one():
    """Jack's legs: the corpus named the layer-1 quartet, so the unresolved one
    is layer 0 -- and rendering it AS layer 1 put his battle damage on his shins."""
    binds = {22: "n", 23: "d", 24: "s", 25: "c"}
    meta = _meta(n=(BC5, 2048, 2048), d=(BC1_SRGB, 2048, 2048),
                 s=(BC3_SRGB, 2048, 2048), c=(BC4, 2048, 2048))
    out = _M.composite_roles_from_format(binds, meta, claimed_layers={1})
    assert out["layer"] == 0
    assert set(out["roles"].values()) == {
        "layer0_composite_normals", "layer0_composite_diffuse",
        "layer0_composite_specular", "layer0_composite_components"}


def test_two_unresolved_resolution_groups_are_refused_outright():
    """Resolution-group != layer-group on 4.5 % of shadersets, and layer 0 is the
    strictly largest group in only 5 of 19 multi-layer ones -- so a second group
    makes the layer unrecoverable, and a guessed layer is still a guess."""
    binds = {0: "a", 1: "b"}
    meta = _meta(a=(BC5, 2048, 2048), b=(BC5, 512, 512))
    out = _M.composite_roles_from_format(binds, meta)
    assert out["roles"] == {}
    assert set(out["refused"].values()) == {_M.REFUSE_MANY_GROUPS}


def test_a_second_bc1_srgb_disables_the_specular_guard():
    """The one measured counterexample to the bare format rule: a BC1_UNORM_SRGB
    `layer1_composite_specular` sharing a group with a second BC1_UNORM_SRGB."""
    binds = {0: "d0", 1: "d1", 2: "s"}
    meta = _meta(d0=(BC1_SRGB, 512, 512), d1=(BC1_SRGB, 512, 512),
                 s=(BC3_SRGB, 512, 512))
    out = _M.composite_roles_from_format(binds, meta)
    assert out["roles"] == {}
    assert out["refused"] == {0: _M.REFUSE_NOT_UNIQUE, 1: _M.REFUSE_NOT_UNIQUE,
                             2: _M.REFUSE_NOT_UNIQUE}


def test_a_texture_with_no_measured_format_is_refused_not_guessed():
    out = _M.composite_roles_from_format({7: "x"}, {})
    assert out["roles"] == {} and out["refused"] == {7: _M.REFUSE_NO_FORMAT}


def test_it_never_overwrites_a_role_the_shaderset_already_carries():
    binds = {0: "n"}
    meta = _meta(n=(BC5, 256, 256))
    out = _M.composite_roles_from_format(binds, meta,
                                         taken_roles={"layer0_composite_normals"})
    assert out["roles"] == {}
    assert out["refused"] == {0: _M.REFUSE_ROLE_TAKEN}


def test_every_role_it_can_emit_is_a_real_inputname():
    """⛔ L1: ten `layerN_*` names were fabricated once. Every emitted key must be
    a declared ubermaterial sampler with `symbol64(name) == key`."""
    for layer in range(_M.UBERMATERIAL_LAYER_COUNT):
        for suffix in ("composite_normals", "composite_diffuse",
                       "composite_specular", "composite_components"):
            name = f"layer{layer}_{suffix}"
            key = "%016x" % _M.symbol64(name)
            assert key in _M.ROLE_BY_INPUTNAME, name
            assert _M.ROLE_BY_INPUTNAME[key][0] == name
    # and it must refuse rather than reach past the four declared layers
    out = _M.composite_roles_from_format(
        {0: "n"}, {"n": {"dxgi": BC5, "width": 8, "height": 8}},
        claimed_layers={0, 1, 2, 3})
    assert out["roles"] == {} and out["refused"] == {0: _M.REFUSE_NO_FREE_LAYER}
