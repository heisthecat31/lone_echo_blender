"""Transparency / emissive ground-truth tests.

Pure stdlib (no oodle, no bpy). These lock in facts established by cracking
CSymbol64 preimages against the game's own authored material vocabulary and by
decoding shipped material slices — NOT by guessing.

Why this file exists: ten entries in `le_mesh.materials.INPUTNAME_ROLE` were
*fabricated* labels that do not hash to the key they are filed under.
`test_no_fabricated_role_names` is the regression guard;
`test_cracked_inputname_preimages` is the replacement truth. See docs/MATERIALS.md.
"""

import struct

from le_mesh import material_scalars as msc

import audit_material_modes as amm


# ---------------------------------------------------------------------------
# 1. Verified CSymbol64 preimages
# ---------------------------------------------------------------------------

# Every pair here satisfies symbol64(name) == hash exactly, so the name IS the
# on-disk preimage (a 64-bit accidental collision is not a practical concern).
CRACKED_INPUTNAMES = {
    "6dd500693d77b342": "layer0_albedo_map",
    "e61f1a40b0f64878": "layer0_normal_map",
    "2249a2ab88ae66f0": "layer0_specular_map",
    "a0790a952a361b16": "layer0_opacity_map",
    "63942a40279db62a": "layer1_opacity_map",
    "9dba2dc44433be64": "layer0_alpha_map",
    "8ed4ab4792aaf806": "layer1_alpha_map",
    "36edc221250ba1a0": "layer0_emissive_map",
    "dcfcc0a30933479e": "layer1_emissive_map",
    "b188cecfb9c75902": "layer2_emissive_map",
    "571b8c6b2599c12a": "layer0_secondary_emissive_map",
    "39d68102257d6d24": "layer0_back_lighting_map",
    "e348dd9cd3fdc817": "layer0_composite_diffuse",
    "e342db88d8e9d701": "layer0_composite_normals",
    "33d1823268b0a40c": "layer0_composite_specular",
    "d000069cc9204803": "layer0_composite_components",
    "96a697df18ea44f1": "layer1_composite_diffuse",
    "96ac91cb13fe5be7": "layer1_composite_normals",
    "5359456ffb9a1dae": "layer1_composite_specular",
    "228838c1c7770d21": "layer1_composite_components",
    "f340cfaa0e533ab5": "layer1_blend_mask",
    "18405b9104db1997": "layer2_blend_mask",
    "bebfd787fd5cf889": "layer3_blend_mask",
    "174d6978fb021e30": "layer0_flowmap_map",
    "d4a049adf6a9b30c": "layer1_flowmap_map",
}

# Labels previously carried in le_mesh.materials.INPUTNAME_ROLE as "tentative".
# None of them is a preimage of the hash it was filed under.
FABRICATED_LABELS = {
    "e342db88d8e9d701": "layer0_normal_map_alt",
    "96ac91cb13fe5be7": "layer1_glass_normal",
    "33d1823268b0a40c": "layer0_rgba_surface",
    "e348dd9cd3fdc817": "layer0_diffuse_map",
    "96a697df18ea44f1": "layer1_glass_diffuse",
    "5359456ffb9a1dae": "layer1_glass_rgba",
    "39d68102257d6d24": "layer0_emissive_rgba",
    "228838c1c7770d21": "layer1_glass_mask",
    "d000069cc9204803": "layer0_linear_map",
    "8ed4ab4792aaf806": "layer1_mask_b",
}


def test_cracked_inputname_preimages():
    for hexhash, name in CRACKED_INPUTNAMES.items():
        assert f"{msc.symbol64(name):016x}" == hexhash, (
            f"{name} does not hash to {hexhash}")


def test_no_fabricated_role_names():
    """The old 'tentative' labels must never be treated as cracked names."""
    for hexhash, label in FABRICATED_LABELS.items():
        assert f"{msc.symbol64(label):016x}" != hexhash, (
            f"{label} unexpectedly hashes to {hexhash}")


def test_no_glass_specific_role_exists():
    """'Glass' roles were invented; the real names are the layer1 composite set."""
    for label in ("layer1_glass_normal", "layer1_glass_diffuse",
                  "layer1_glass_rgba", "layer1_glass_mask"):
        h = f"{msc.symbol64(label):016x}"
        assert h not in CRACKED_INPUTNAMES, f"{label} collides with a real name"


# ---------------------------------------------------------------------------
# 2. Alpha / emissive scalar parameter names
# ---------------------------------------------------------------------------

def test_alpha_and_emissive_scalar_preimages():
    """Authored scalar knobs that decide transparency and emission."""
    expected = {
        "k_alpha": msc.HASH_K_ALPHA,
        "layer0_emissive_intensity": msc.HASH_EMISSIVE_INTENSITY[0],
    }
    for name, want in expected.items():
        assert msc.symbol64(name) == want

    # observed in shipped bytes (archive 0703fd2acd5803e9)
    assert f"{msc.symbol64('layer1_blend_mask_scale'):016x}" == "823cd9897f5d6113"


def test_name_table_covers_transparency_knobs():
    table = amm.build_name_table()
    for name in ("k_alpha", "k_alpha_threshold", "k_transparent_alpha_threshold",
                 "k_emissive_scale", "k_refractive_index", "k_refraction_amount",
                 "k_bake_emissive_intensity", "layer0_emissive_intensity",
                 "layer0_alpha_map", "layer0_opacity_map",
                 "layer0_composite_diffuse"):
        assert table.get(msc.symbol64(name)) == name, f"missing {name}"


def test_name_table_resolves_shipped_props():
    """Every materialprop hash seen in the shipped bridge archive resolves."""
    table = amm.build_name_table()
    observed = {
        "823cd9897f5d6113": "layer1_blend_mask_scale",
    }
    for hexhash, name in observed.items():
        assert table.get(int(hexhash, 16)) == name


# ---------------------------------------------------------------------------
# 3. Enum tables match the engine's own enumerations
# ---------------------------------------------------------------------------

def test_mattype_enum_matches_engine():
    assert len(amm.MATTYPE_NAMES) == 17            # kNumMatTypes
    assert amm.MATTYPE_NAMES[0] == "eMTDeferredOpaque"
    assert amm.MATTYPE_NAMES[2] == "eMTForwardTransparent"
    assert amm.MATTYPE_NAMES[9] == "eMTAlphaTested"
    assert amm.MATTYPE_NAMES[11] == "eMTRefraction"
    assert amm.MATTYPE_NAMES[13] == "eMTSkydome"
    assert amm.MATTYPE_NAMES[16] == "eMTTransparentPostAA"


def test_blendmode_enum_matches_engine():
    assert len(amm.BLENDMODE_NAMES) == 18          # kNumBlendModes
    assert amm.BLENDMODE_NAMES[0] == "eBlendOpaque"
    assert amm.BLENDMODE_NAMES[7] == "eBlendTransparent"
    assert amm.BLENDMODE_NAMES[10] == "eBlendSkirt"
    assert amm.BLENDMODE_NAMES[12] == "eBlendTranslucent"


# ---------------------------------------------------------------------------
# 4. Serialized SGMaterialData layout (confirmed by slice-size arithmetic)
# ---------------------------------------------------------------------------

# (slice_size, n_props, n_propoffsets, n_uvsets, n_perms, n_auxinputs) rows measured
# on the shipped materials of archive 0703fd2acd5803e9.
# size == 0x160 + 4n_props + 16n_offs + 8n_uv + 16n_perm + 32n_aux
SHIPPED_SLICE_SHAPES = [
    (424, 0, 0, 1, 0, 2),
    (444, 1, 1, 1, 0, 2),
    (464, 2, 2, 1, 0, 2),
    (544, 6, 6, 1, 0, 2),
    (584, 8, 8, 1, 0, 2),
]


def test_material_slice_size_arithmetic():
    for size, n_props, n_offs, n_uv, n_perm, n_aux in SHIPPED_SLICE_SHAPES:
        computed = (msc.HEADER_SIZE + 4 * n_props + 16 * n_offs + 8 * n_uv
                    + 16 * n_perm + msc.SIZEOF_SHADERINPUTDATA * n_aux)
        assert computed == size, f"{computed} != {size}"


def test_header_offsets_match_layout():
    assert msc.OFF_BAKECOLOR == 0x008
    assert msc.OFF_BAKEEMISSIVECOLOR == 0x018
    assert msc.OFF_BLENDMODE == 0x028
    assert msc.OFF_MATTYPE == 0x02A
    assert msc.OFF_FLAGS == 0x02C
    assert msc.HEADER_SIZE == 0x160


# ---------------------------------------------------------------------------
# 5. Decode of a transparent + emissive material
# ---------------------------------------------------------------------------

def _slice(*, bakecolor=(1, 1, 1, 1), emissive=(0, 0, 0, 1), blendmode=0,
           mattype=1, flags=0, props=()) -> bytes:
    header = bytearray(msc.HEADER_SIZE)
    struct.pack_into("<4f", header, msc.OFF_BAKECOLOR, *bakecolor)
    struct.pack_into("<4f", header, msc.OFF_BAKEEMISSIVECOLOR, *emissive)
    struct.pack_into("<H", header, msc.OFF_BLENDMODE, blendmode)
    struct.pack_into("<H", header, msc.OFF_MATTYPE, mattype)
    struct.pack_into("<I", header, msc.OFF_FLAGS, flags)
    struct.pack_into("<Q", header, msc.OFF_MATERIALPROPS_IUSED, len(props))
    struct.pack_into("<Q", header, msc.OFF_PROPOFFSETS_IUSED, len(props))
    words = b"".join(struct.pack("<I", struct.unpack("<I", struct.pack("<f", v))[0])
                     for _h, v in props)
    offs = b"".join(struct.pack("<QII", h, i * 4, 0)
                    for i, (h, _v) in enumerate(props))
    return bytes(header) + words + offs


def test_translucent_material_decode():
    """eMTForwardTransparent + eBlendTranslucent is the shipped glass combo."""
    s = msc.decode_material_scalars(_slice(
        blendmode=12, mattype=2, flags=msc.EFLAGS["eDoubleSided"],
        props=[(msc.HASH_K_ALPHA, 0.25)]))
    assert s["mattype"] == 2
    assert s["blend_mode"] == 12
    assert abs(s["alpha"] - 0.25) < 1e-6
    assert s["double_sided"] is True


def test_emissive_intensity_without_bake_emissive_color():
    """Shipped emissive materials carry layerN_emissive_intensity while
    bakeemissivecolor stays (0,0,0).

    `is_emissive` used to be `any(bakeemissivecolor.rgb)`, which is False for
    EVERY genuinely emissive material inspected (19 of 19 decoded fixture
    materials, 8 of which carry a non-zero layer0_emissive_intensity). The gate
    is now the authored per-layer emissive state; the old bake-time signal is
    preserved as `bake_emissive_nonzero`.
    """
    s = msc.decode_material_scalars(_slice(
        emissive=(0.0, 0.0, 0.0, 1.0),
        props=[(msc.HASH_EMISSIVE_INTENSITY[0], 6.0)]))
    assert s["bake_emissive_nonzero"] is False   # bakeemissivecolor is black
    assert s["is_emissive"] is True              # ...but the layer knob is set
    assert s["emissive_layer_indices"] == [0]
    assert s["emissive_intensity"] == 6.0


# ---------------------------------------------------------------------------
# 6. The three previously-unresolved texture-input hashes
# ---------------------------------------------------------------------------

def test_pom_height_map_preimage():
    """`602e82b525713c1c` is `pom_height_map` — recovered, not guessed.

    The missing axis was the non-layer parameter GROUPS: the uber-material
    declares a parallax-occlusion group alongside `layer0..layer3`, and that
    group declares a `height_map` sampler — so the input is `pom_<member>`,
    exactly like `layer0_<member>`. `symbol64` reproduces the shipped hash
    exactly, which is the only reason the name is accepted.
    """
    assert f"{msc.symbol64('pom_height_map'):016x}" == "602e82b525713c1c"
    assert amm.build_name_table()[0x602e82b525713c1c] == "pom_height_map"


def test_two_remaining_unknowns_are_scan_artifacts_not_names():
    """`05575a94091f1839` and `80a6642707ce0367` are NOT parameter names.

    Each equals the *shaderset's own hash* and appears once, at entry_offset 768
    with slot=0 type=0 layer=1032 engineresource=4096 and denormal uscale/vscale
    (1.6e-36 / 5.7e-42) — the scanner mistook the shaderset name field for an
    input entry. They are the only two rows anywhere in the scan where
    `inputname_hash == shaderset_hash`.

    Guard: they must never acquire a "cracked" name. A name may only be added
    here when symbol64(name) reproduces the hash exactly.
    """
    table = amm.build_name_table()
    for h in (0x05575a94091f1839, 0x80a6642707ce0367):
        assert h not in table, f"{h:016x} must stay unnamed (it is a scan artifact)"
