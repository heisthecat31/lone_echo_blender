"""Per-layer material scalars: the B4 layer-selection fix and the added knobs.

Pure stdlib (no oodle, no bpy). Locks:

  * every newly added scalar name is the exact CSymbol64 preimage of the key it
    is filed under (the guard against the fabricated-name defect — see
    docs/MATERIALS.md);
  * `decode_material_scalars` emits a per-layer `layers[]` array so a consumer
    can read the intensity of the layer whose emissive map was actually routed;
  * the shipped material `0613ef69c99cbbc6` reads `layers[1].emissive_intensity
    == 25.0` where the legacy flat key still reads 2.0 (12.5x too dim);
  * absent authored knobs fall back to the engine's own authored default, never
    to an invented one.

Evidence labels: `name-confirmed` (matches the engine's own type / field / enum
names), `stream-confirmed` (shipped archive bytes), `inferred`.
"""

import json
import struct
from pathlib import Path

from le_mesh import material_scalars as msc


HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "exports" / "fixtures_mat"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _slice(*, bakecolor=(1, 1, 1, 1), emissive=(0, 0, 0, 1), blendmode=0,
           mattype=1, flags=0, props=()) -> bytes:
    """Synthesize a material primary slice.

    `props` is a list of (name_hash, value) where value is a float or a sequence
    of floats; a sequence occupies that many CONSECUTIVE materialprops words and
    the offsets entry points at the first (the multi-word packing is `inferred`
    from the authored WidgetColor4/range types — no colour-valued materialprop
    has been observed in shipped bytes yet).
    """
    words: list[float] = []
    entries: list[tuple[int, int]] = []
    for name_hash, value in props:
        entries.append((name_hash, len(words)))
        if isinstance(value, (list, tuple)):
            words.extend(float(v) for v in value)
        else:
            words.append(float(value))

    header = bytearray(msc.HEADER_SIZE)
    struct.pack_into("<4f", header, msc.OFF_BAKECOLOR, *bakecolor)
    struct.pack_into("<4f", header, msc.OFF_BAKEEMISSIVECOLOR, *emissive)
    struct.pack_into("<H", header, msc.OFF_BLENDMODE, blendmode)
    struct.pack_into("<H", header, msc.OFF_MATTYPE, mattype)
    struct.pack_into("<I", header, msc.OFF_FLAGS, flags)
    struct.pack_into("<Q", header, msc.OFF_MATERIALPROPS_IUSED, len(words))
    struct.pack_into("<Q", header, msc.OFF_PROPOFFSETS_IUSED, len(entries))
    blob = b"".join(struct.pack("<f", w) for w in words)
    offs = b"".join(struct.pack("<QII", h, i * 4, 0) for h, i in entries)
    return bytes(header) + blob + offs


# ---------------------------------------------------------------------------
# 1. Every added name is a verified preimage
# ---------------------------------------------------------------------------

# Global knobs. Names `name-confirmed`: `k_*` are the engine's ubermaterial
# parameters and `k_bake_*` are the material asset schema's.
ADDED_GLOBAL_NAMES = {
    "k_alpha": msc.HASH_K_ALPHA,
    "k_alpha_threshold": msc.HASH_K_ALPHA_THRESHOLD,
    "k_transparent_alpha_threshold": msc.HASH_K_TRANSPARENT_ALPHA_THRESHOLD,
    "k_emissive_scale": msc.HASH_K_EMISSIVE_SCALE,
    "k_refractive_index": msc.HASH_K_REFRACTIVE_INDEX,
    "k_refraction_amount": msc.HASH_K_REFRACTION_AMOUNT,
    "k_bake_emissive_intensity": msc.HASH_K_BAKE_EMISSIVE_INTENSITY,
}


def test_added_global_scalar_names_are_preimages():
    for name, want in ADDED_GLOBAL_NAMES.items():
        assert msc.symbol64(name) == want, name
        assert msc.resolve_name(want) == name, name


def test_added_layer_scalar_names_are_preimages():
    """`layerN_*` members of the engine's `UberMaterialLayer`."""
    tables = {
        "emissive_intensity": msc.HASH_EMISSIVE_INTENSITY,
        "emissive_tint_color": msc.HASH_EMISSIVE_TINT,
        "opacity_tint_color": msc.HASH_OPACITY_TINT,
        "emissive_map_uoffset": msc.HASH_EMISSIVE_MAP_UOFFSET,
        "emissive_map_voffset": msc.HASH_EMISSIVE_MAP_VOFFSET,
    }
    for suffix, table in tables.items():
        for layer, want in table.items():
            name = f"layer{layer}_{suffix}"
            assert msc.symbol64(name) == want, name
            assert msc.resolve_name(want) == name, name


def test_name_table_never_returns_a_non_preimage():
    """The structural guard: resolve_name is only ever right by construction."""
    table = msc.build_name_table()
    bad = [(h, n) for h, n in table.items() if msc.symbol64(n) != h]
    assert not bad, f"table entries that are not their key's preimage: {bad[:5]}"


def test_stream_confirmed_hashes_resolve():
    """Hashes actually observed in shipped materialprops tables.

    All 11 distinct materialprop hashes in the 51-package fixture corpus
    (`stream-confirmed`, archive 0703fd2acd5803e9).
    """
    observed = {
        "2fdcb09e52645f8b": "k_alpha",
        "31e35f7a5feb8441": "layer0_emissive_intensity",
        "516b9827ccc13de3": "layer1_emissive_intensity",
        "c7e40edd6f299f19": "layer2_emissive_intensity",
        "a76cc980fc0326bb": "layer3_emissive_intensity",
        "25f0f7652abbc480": "layer1_emissive_map_voffset",
        "6307b553dfa1091c": "layer0_albedo_map_uoffset",
        "c6f8f070a09880a0": "layer1_blend_mask_offset",
        "2f0e118582db9c08": "layer2_blend_mask_offset",
        "5ae05bc649cc10ee": "layer3_blend_mask_offset",
        "823cd9897f5d6113": "layer1_blend_mask_scale",
    }
    for hexhash, name in observed.items():
        assert msc.resolve_name(int(hexhash, 16)) == name, hexhash


# ---------------------------------------------------------------------------
# 2. Per-layer extraction
# ---------------------------------------------------------------------------

def test_layers_array_shape():
    """`layers` always covers the four declared UberMaterialLayer slots."""
    s = msc.decode_material_scalars(_slice())
    assert len(s["layers"]) == msc.N_DECLARED_LAYERS == 4
    assert [lay["index"] for lay in s["layers"]] == [0, 1, 2, 3]
    for lay in s["layers"]:
        assert lay["emissive_intensity"] is None
        assert lay["emissive_tint"] is None
        assert lay["opacity_tint"] is None
        assert lay["uv_offset"] is None


def test_layers_extend_past_the_declared_four_when_data_demands():
    s = msc.decode_material_scalars(_slice(
        props=[(msc.HASH_EMISSIVE_INTENSITY[5], 9.0)]))
    assert len(s["layers"]) == 6
    assert s["layers"][5]["emissive_intensity"] == 9.0
    assert s["emissive_layer_indices"] == [5]


def test_per_layer_intensity_tint_and_uv_offset():
    s = msc.decode_material_scalars(_slice(props=[
        (msc.HASH_EMISSIVE_INTENSITY[0], 2.0),
        (msc.HASH_EMISSIVE_INTENSITY[1], 25.0),
        (msc.HASH_EMISSIVE_TINT[1], (0.25, 0.5, 0.75, 1.0)),
        (msc.HASH_OPACITY_TINT[1], (0.125, 0.25, 0.375, 1.0)),
        (msc.HASH_EMISSIVE_MAP_VOFFSET[1], -0.2),
    ]))
    lay0, lay1 = s["layers"][0], s["layers"][1]
    assert lay0["emissive_intensity"] == 2.0
    assert lay0["emissive_tint"] is None
    assert lay1["emissive_intensity"] == 25.0
    assert lay1["emissive_tint"] == [0.25, 0.5, 0.75]        # RGB of the Color4
    assert lay1["opacity_tint"] == [0.125, 0.25, 0.375]
    assert lay1["uv_offset"][0] == 0.0
    assert abs(lay1["uv_offset"][1] + 0.2) < 1e-6
    assert abs(lay1["uv_offsets"]["emissive_map"][1] + 0.2) < 1e-6
    assert s["emissive_layer_indices"] == [0, 1]


def test_emissive_tint_alone_marks_a_layer_emissive():
    """A non-black authored emissive tint is enough; the bake colour is not."""
    s = msc.decode_material_scalars(_slice(
        emissive=(0.0, 0.0, 0.0, 1.0),
        props=[(msc.HASH_EMISSIVE_TINT[2], (1.0, 0.5, 0.0, 1.0))]))
    assert s["is_emissive"] is True
    assert s["bake_emissive_nonzero"] is False
    assert s["emissive_layer_indices"] == [2]


def test_zero_intensity_is_not_emissive():
    s = msc.decode_material_scalars(_slice(
        props=[(msc.HASH_EMISSIVE_INTENSITY[0], 0.0)]))
    assert s["is_emissive"] is False
    assert s["emissive_layer_indices"] == []
    assert s["layers"][0]["emissive_intensity"] == 0.0   # present, but zero


# ---------------------------------------------------------------------------
# 3. The shipped worked example: 0613ef69c99cbbc6
# ---------------------------------------------------------------------------

# materialprops of bridge material 0613ef69c99cbbc6 (`stream-confirmed`,
# archive 0703fd2acd5803e9; identical table in exports/fixtures_mat and in
# scratchpad/bridge_materials.tsv). eMTForwardTransparent / eBlendTranslucent.
BRIDGE_0613_PROPS = {
    "31e35f7a5feb8441": 2.0,                    # layer0_emissive_intensity
    "516b9827ccc13de3": 25.0,                   # layer1_emissive_intensity
    "c7e40edd6f299f19": 2.0,                    # layer2_emissive_intensity
    "25f0f7652abbc480": -0.20000000298023224,   # layer1_emissive_map_voffset
    "c6f8f070a09880a0": -1.0,                   # layer1_blend_mask_offset
    "2f0e118582db9c08": -1.0,                   # layer2_blend_mask_offset
}


def _bridge_0613_slice() -> bytes:
    return _slice(mattype=2, blendmode=12, emissive=(0.0, 0.0, 0.0, 1.0),
                  flags=284,
                  props=[(int(h, 16), v) for h, v in BRIDGE_0613_PROPS.items()])


def test_bridge_0613_reads_layer1_intensity_25():
    """The B4 regression: the emissive map is `layer1_emissive_map`, so the
    intensity is layer1's 25.0 — not layer0's 2.0 (12.5x too dim)."""
    s = msc.decode_material_scalars(_bridge_0613_slice())

    assert s["layers"][1]["emissive_intensity"] == 25.0     # the correct value
    assert s["layers"][0]["emissive_intensity"] == 2.0
    assert s["layers"][2]["emissive_intensity"] == 2.0
    assert s["layers"][3]["emissive_intensity"] is None
    assert s["emissive_layer_indices"] == [0, 1, 2]

    # legacy flat key deliberately unchanged (layer0 wins) — it is what the old
    # consumers read, and it is the wrong answer here.
    assert s["emissive_intensity"] == 2.0

    # Emission Strength = layerN_emissive_intensity x k_emissive_scale, and
    # k_emissive_scale is absent => authored default 1.0 (`name-confirmed`).
    assert s["emissive_scale"] == 1.0
    assert "k_emissive_scale" in s["scalar_defaults_applied"]
    assert s["layers"][1]["emissive_intensity"] * s["emissive_scale"] == 25.0

    assert s["mattype_name"] == "eMTForwardTransparent"
    assert s["blend_mode_name"] == "eBlendTranslucent"
    assert s["is_emissive"] is True
    assert s["bake_emissive_nonzero"] is False

    resolved = s["named_scalars_resolved"]
    assert resolved["layer1_emissive_intensity"] == 25.0
    assert abs(resolved["layer1_emissive_map_voffset"] + 0.2) < 1e-6
    assert len(resolved) == len(BRIDGE_0613_PROPS)      # all 6 hashes cracked


def test_bridge_0613_matches_the_shipped_fixture_package():
    """Cross-check the embedded table against the exported fixture manifest.

    Skips when the fixture corpus is not present (it is a working export, not a
    committed artefact).
    """
    man = FIXTURES / "0703fd2acd5803e9_892cca9de00b30a6.lemesh" / "manifest.json"
    if not man.exists():
        return
    entry = next((m for m in json.loads(man.read_text(encoding="utf-8"))["materials"]
                  if m["material_hash"] == "0613ef69c99cbbc6"), None)
    if entry is None or "named_scalars" not in entry:
        return
    assert entry["named_scalars"] == BRIDGE_0613_PROPS
    assert entry["mattype"] == 2 and entry["blend_mode"] == 12
    # the routed emissive map is on layer 1 — that is what selects layers[1]
    assert entry["channels"]["emission"]["role_key"] == "layer1_emissive_map"


# ---------------------------------------------------------------------------
# 4. Enum names + authored defaults
# ---------------------------------------------------------------------------

def test_mattype_and_blend_mode_names_are_emitted():
    for mattype, name in ((1, "eMTForwardOpaque"), (2, "eMTForwardTransparent"),
                          (9, "eMTAlphaTested"), (16, "eMTTransparentPostAA")):
        s = msc.decode_material_scalars(_slice(mattype=mattype))
        assert s["mattype_name"] == name
    for blend, name in ((0, "eBlendOpaque"), (7, "eBlendTransparent"),
                        (8, "eBlendLinearDodge"), (12, "eBlendTranslucent")):
        s = msc.decode_material_scalars(_slice(blendmode=blend))
        assert s["blend_mode_name"] == name


def test_unknown_enum_values_are_labelled_not_guessed():
    s = msc.decode_material_scalars(_slice(mattype=200, blendmode=201))
    assert s["mattype_name"] == "unknown_mattype_200"
    assert s["blend_mode_name"] == "unknown_blendmode_201"


def test_authored_defaults_match_the_authoring_source():
    """Every default is a value the engine's ubermaterial and the material asset
    schema authored (`name-confirmed`).

    Nothing here may be invented; a changed number needs new evidence.
    """
    assert msc.AUTHORED_DEFAULTS_GLOBAL == {
        "k_alpha": 1.0,
        "k_emissive_scale": 1.0,
        "k_transparent_alpha_threshold": 0.0001,
        "k_alpha_threshold": 0.5,
        "k_refractive_index": 1.0,
        "k_refraction_amount": 1.0,
        "k_depth_fade_distance": 0.25,
        "k_skirt_normal_blend_amt": 1.0,
        "k_bake_emissive_intensity": 1.0,
    }
    assert msc.AUTHORED_DEFAULTS_LAYER["emissive_intensity"] == 1.0
    assert msc.AUTHORED_DEFAULTS_LAYER["emissive_tint_color"] == (1.0, 1.0, 1.0, 1.0)
    assert msc.AUTHORED_DEFAULTS_LAYER["opacity_tint_color"] == (1.0, 1.0, 1.0, 1.0)


def test_absent_knobs_fall_back_to_authored_defaults():
    s = msc.decode_material_scalars(_slice())
    # each is the engine's own authored default for that parameter
    assert s["alpha_threshold"] == 0.5
    assert s["emissive_scale"] == 1.0
    assert s["refractive_index"] == 1.0
    assert set(s["scalar_defaults_applied"]) == {
        "k_alpha_threshold", "k_emissive_scale", "k_refractive_index"}


def test_present_knobs_override_the_defaults():
    s = msc.decode_material_scalars(_slice(mattype=9, props=[
        (msc.HASH_K_ALPHA_THRESHOLD, 0.75),
        (msc.HASH_K_EMISSIVE_SCALE, 2.5),
        (msc.HASH_K_REFRACTIVE_INDEX, 1.5),
    ]))
    assert s["alpha_threshold"] == 0.75
    assert s["emissive_scale"] == 2.5
    assert s["refractive_index"] == 1.5
    assert s["scalar_defaults_applied"] == []
    assert s["named_scalars_resolved"]["k_alpha_threshold"] == 0.75


# ---------------------------------------------------------------------------
# 5. Back-compat: the producer contract must not lose a key
# ---------------------------------------------------------------------------

LEGACY_KEYS = ("base_color_factor", "emissive_color", "emissive_intensity", "alpha",
               "blend_mode", "double_sided", "mattype", "flags", "flag_names",
               "materialfx", "is_emissive", "named_scalars")
ADDED_KEYS = ("layers", "emissive_scale", "alpha_threshold", "refractive_index",
              "mattype_name", "blend_mode_name", "named_scalars_resolved",
              "emissive_layer_indices", "bake_emissive_nonzero",
              "scalar_defaults_applied")


def test_every_contract_key_is_present_on_good_and_bad_input():
    good = msc.decode_material_scalars(_slice(props=[(msc.HASH_K_ALPHA, 0.25)]))
    for bad_input in (b"", b"\x00" * 8, b"\xff" * (msc.HEADER_SIZE - 1)):
        bad = msc.decode_material_scalars(bad_input)
        for key in LEGACY_KEYS + ADDED_KEYS:
            assert key in good, f"missing {key} (valid slice)"
            assert key in bad, f"missing {key} (short slice)"
    assert abs(good["alpha"] - 0.25) < 1e-6


def test_named_scalars_still_hash_keyed_and_float_valued():
    """`named_scalars` is unchanged; `named_scalars_resolved` is the addition."""
    s = msc.decode_material_scalars(_slice(props=[
        (msc.HASH_EMISSIVE_INTENSITY[1], 25.0),
        (0xDEADBEEFDEADBEEF, 3.0),          # deliberately un-crackable
    ]))
    assert s["named_scalars"]["516b9827ccc13de3"] == 25.0
    assert s["named_scalars"]["deadbeefdeadbeef"] == 3.0
    assert s["named_scalars_resolved"] == {"layer1_emissive_intensity": 25.0}


def test_parse_material_props_wrapper_unchanged():
    raw = _slice(props=[(msc.HASH_K_ALPHA, 0.5), (msc.HASH_EMISSIVE_INTENSITY[0], 4.0)])
    props = msc.parse_material_props(raw)
    assert props[msc.HASH_K_ALPHA] == 0.5
    assert props[msc.HASH_EMISSIVE_INTENSITY[0]] == 4.0
    words, slots = msc.parse_material_prop_slots(raw)
    assert words == [0.5, 4.0]
    assert slots[msc.HASH_K_ALPHA] == 0


# ---------------------------------------------------------------------------
# 6. Corpus invariants (skip when the fixture export is absent)
# ---------------------------------------------------------------------------

def _fixture_materials() -> dict:
    by_hash: dict[str, dict] = {}
    for man in sorted(FIXTURES.glob("*.lemesh/manifest.json")):
        for mat in json.loads(man.read_text(encoding="utf-8")).get("materials", []):
            h = mat.get("material_hash", "")
            prev = by_hash.get(h)
            if prev is None or ("named_scalars" in mat and "named_scalars" not in prev):
                by_hash[h] = mat
    return by_hash


def test_fixture_corpus_materialprops_all_resolve():
    """Every materialprop hash in the 51-package corpus has a cracked name."""
    mats = _fixture_materials()
    if not mats:
        return
    unresolved = sorted({h for m in mats.values()
                         for h in m.get("named_scalars", {})
                         if msc.resolve_name(int(h, 16)) is None})
    assert not unresolved, f"unresolved materialprop hashes: {unresolved}"


def test_fixture_corpus_bake_emissive_is_always_black():
    """The reason `is_emissive` may not be gated on `bakeemissivecolor`."""
    mats = _fixture_materials()
    decoded = [m for m in mats.values() if "named_scalars" in m]
    if not decoded:
        return
    assert all(m["emissive_color"] == [0.0, 0.0, 0.0] for m in decoded)
    # ...while 8 of them do carry a non-zero layer0_emissive_intensity
    hot = [m for m in decoded
           if any(int(h, 16) in msc.HASH_EMISSIVE_INTENSITY.values() and v != 0.0
                  for h, v in m["named_scalars"].items())]
    assert len(hot) >= 8
