"""M2 material tests: SGMaterialData scalar decode + role classification wiring.

Runs under `python3 tests/run_tests.py` and under pytest unchanged. Pure stdlib
(no oodle, no bpy) — exercises the le_mesh core only.
"""

import struct

from le_mesh import materials as mat
from le_mesh import material_scalars as msc


# --- helpers ----------------------------------------------------------------

def _f32(x: float) -> int:
    """float -> its u32 bit pattern (as stored in the materialprops word array)."""
    return struct.unpack("<I", struct.pack("<f", x))[0]


def _build_material_slice(*, bakecolor, emissive, blendmode, mattype, flags,
                          props: list[tuple[int, float]]) -> bytes:
    """Synthesize a CGMaterialResourceWin7 primary slice with known contents.

    `props` is a list of (name_hash, value); each becomes one materialprops word
    plus one materialpropoffsets entry pointing at it (byteoffset = index*4).
    """
    header = bytearray(msc.HEADER_SIZE)
    struct.pack_into("<Q", header, msc.OFF_MATERIALFX, 0xABCDEF0123456789)
    struct.pack_into("<4f", header, msc.OFF_BAKECOLOR, *bakecolor)
    struct.pack_into("<4f", header, msc.OFF_BAKEEMISSIVECOLOR, *emissive)
    struct.pack_into("<H", header, msc.OFF_BLENDMODE, blendmode)
    struct.pack_into("<H", header, msc.OFF_MATTYPE, mattype)
    struct.pack_into("<I", header, msc.OFF_FLAGS, flags)
    n = len(props)
    struct.pack_into("<Q", header, msc.OFF_MATERIALPROPS_IUSED, n)
    struct.pack_into("<Q", header, msc.OFF_PROPOFFSETS_IUSED, n)

    words = b"".join(struct.pack("<I", _f32(v)) for _h, v in props)
    # each materialpropoffsets entry: key u64 @0, byteoffset u32 @8, pad u32 @12
    offsets = b"".join(struct.pack("<QII", h, i * 4, 0) for i, (h, _v) in enumerate(props))
    return bytes(header) + words + offsets


# --- scalar decode ----------------------------------------------------------

def test_decode_scalars_full():
    slice_bytes = _build_material_slice(
        bakecolor=(0.5, 0.25, 0.75, 0.8),
        emissive=(1.0, 0.5, 0.0, 1.0),
        blendmode=2, mattype=7,
        flags=msc.EFLAGS["eDoubleSided"] | msc.EFLAGS["eCastShadows"],
        props=[(msc.HASH_K_ALPHA, 0.35),
               (msc.HASH_EMISSIVE_INTENSITY[0], 4.0)],
    )
    s = msc.decode_material_scalars(slice_bytes)

    assert s["base_color_factor"][0] == 0.5
    assert s["base_color_factor"][1] == 0.25
    assert s["base_color_factor"][2] == 0.75
    assert abs(s["base_color_factor"][3] - 0.8) < 1e-6
    assert s["emissive_color"] == [1.0, 0.5, 0.0]
    assert s["is_emissive"] is True
    assert s["emissive_intensity"] == 4.0
    assert abs(s["alpha"] - 0.35) < 1e-6
    assert s["blend_mode"] == 2
    assert s["mattype"] == 7
    assert s["double_sided"] is True
    assert "eDoubleSided" in s["flag_names"]
    assert "eCastShadows" in s["flag_names"]


def test_emissive_intensity_layer_fallback():
    # no layer0 intensity; only layer2 present -> fallback max over layerN
    slice_bytes = _build_material_slice(
        bakecolor=(1, 1, 1, 1), emissive=(0, 0, 0, 1),
        blendmode=0, mattype=0, flags=0,
        props=[(msc.HASH_EMISSIVE_INTENSITY[2], 2.5)],
    )
    s = msc.decode_material_scalars(slice_bytes)
    assert s["emissive_intensity"] == 2.5
    # `is_emissive` is no longer gated on bakeemissivecolor: that field is
    # (0,0,0) on 19 of 19 shipped materials inspected, INCLUDING every genuinely
    # emissive one, so it under-reports emission and must not be the gate. The
    # bake-time signal survives as `bake_emissive_nonzero`.
    assert s["bake_emissive_nonzero"] is False   # bakeemissivecolor RGB all zero
    assert s["is_emissive"] is True              # layer2_emissive_intensity = 2.5
    assert s["emissive_layer_indices"] == [2]
    assert s["alpha"] == 1.0               # k_alpha absent -> default
    assert s["double_sided"] is False


def test_decode_scalars_defaults_on_short_slice():
    for bad in (b"", b"\x00" * 8, b"\xff" * (msc.HEADER_SIZE - 1)):
        s = msc.decode_material_scalars(bad)
        assert s["base_color_factor"] == [1.0, 1.0, 1.0, 1.0]
        assert s["alpha"] == 1.0
        assert s["double_sided"] is False
        assert s["named_scalars"] == {}


def test_symbol64_matches_known_hashes():
    # reference values (from le_symbol_names.symbol64)
    assert msc.symbol64("k_alpha") == 0x2fdcb09e52645f8b
    assert msc.symbol64("layer0_emissive_intensity") == 0x31e35f7a5feb8441
    # case-insensitive
    assert msc.symbol64("K_Alpha") == msc.symbol64("k_alpha")


# --- classification + spec wiring -------------------------------------------

def test_role_classification_and_spec_wiring():
    role_textures = {
        "layer0_albedo_map": "aaaa",
        "layer0_normal_map": "bbbb",
        "layer0_emissive_map": "dddd",
    }
    dxgi = {"aaaa": 72, "bbbb": 83, "dddd": 72}          # BC1_SRGB, BC5, BC1_SRGB
    scalars = msc.decode_material_scalars(_build_material_slice(
        bakecolor=(0.2, 0.3, 0.4, 1.0), emissive=(0.0, 1.0, 0.0, 1.0),
        blendmode=1, mattype=0, flags=msc.EFLAGS["eDoubleSided"],
        props=[(msc.HASH_K_ALPHA, 0.5), (msc.HASH_EMISSIVE_INTENSITY[0], 3.0)],
    ))
    texture_files = {"aaaa": "textures/aaaa.dds", "bbbb": "textures/bbbb.dds",
                     "dddd": "textures/dddd.dds"}

    spec = mat.build_material_spec(
        "shd__matX", shaderset_hash="shd", material_hash="matX",
        role_textures=role_textures, dxgi_by_tex=dxgi,
        scalars=scalars, texture_files=texture_files)

    ch = spec["channels"]
    assert ch["base_color"]["texture"] == "aaaa"
    assert ch["base_color"]["colorspace"] == "sRGB"
    assert ch["base_color"]["file"] == "textures/aaaa.dds"
    assert ch["normal"]["reconstruct_z"] is True         # BC5
    assert ch["normal"]["file"] == "textures/bbbb.dds"
    assert ch["emission"]["file"] == "textures/dddd.dds"
    # scalars merged
    assert spec["double_sided"] is True
    assert spec["blend_mode"] == 1
    assert abs(spec["alpha"] - 0.5) < 1e-6
    assert spec["emissive_intensity"] == 3.0
    assert spec["emissive_color"] == [0.0, 1.0, 0.0]
    # audit extras carried through
    assert "flag_names" in spec and "eDoubleSided" in spec["flag_names"]


def test_load_texture_homes_and_binding_full(tmp_path):
    scan = tmp_path / "scan.tsv"
    scan.write_text(
        "shaderset_hash\tinputname_hash\ttextureassetid_hash\ttexture_archive_hash\tslot\n"
        "aa\t6dd500693d77b342\tTEX1\tHOME1\t1\n"
        "aa\te61f1a40b0f64878\tTEX2\tHOME2\t2\n",
        encoding="utf-8")
    homes = mat.load_texture_homes(scan)
    assert homes["tex1"] == "home1"
    assert homes["tex2"] == "home2"

    binding = tmp_path / "binding.tsv"
    binding.write_text(
        "meshlist_hash\tmaterial_hashes\tshaderset_hashes\tparse_ok\n"
        "MESH1\tMAT_A;MAT_B\tSHD_A;SHD_B;SHD_C\tTrue\n"
        "MESH2\t\t\tFalse\n",
        encoding="utf-8")
    full = mat.load_binding_full(binding)
    assert full["mesh1"]["materials"] == ["mat_a", "mat_b"]
    assert full["mesh1"]["shadersets"] == ["shd_a", "shd_b", "shd_c"]
    assert "mesh2" not in full          # parse_ok False dropped


def test_roles_from_input_rows():
    class Row:
        def __init__(self, inputname_hash, textureassetid_hash, slot):
            self.inputname_hash = inputname_hash
            self.textureassetid_hash = textureassetid_hash
            self.slot = slot
    rows = [
        Row("6dd500693d77b342", "TEXALB", 1),   # cracked -> layer0_albedo_map
        Row("e61f1a40b0f64878", "TEXNRM", 2),   # cracked -> layer0_normal_map
        Row("deadbeefdeadbeef", "TEXUNK", 18),  # unknown -> unknown_s18
    ]
    roles = mat.roles_from_input_rows(rows, names={})
    assert roles["layer0_albedo_map"] == "texalb"
    assert roles["layer0_normal_map"] == "texnrm"
    assert roles["unknown_s18"] == "texunk"


def test_every_role_name_hashes_to_its_own_key():
    """Every INPUTNAME_ROLE entry must be a real recovered preimage.

    Ten entries here were once INVENTED labels marked "tentative" — none hashed to
    its key, and the fakes changed rendering behaviour (a non-existent
    `layer1_glass_*` family; `layer1_alpha_map`, which is OPACITY, wired to
    Roughness). This is the guard that stops that recurring: a role name is either
    the exact CSymbol64 preimage of its key or it does not belong in the table.
    """
    bad = [(h, role) for h, (role, _conf) in mat.INPUTNAME_ROLE.items()
           if "%016x" % msc.symbol64(role) != h]
    assert not bad, f"role names that are not their key's preimage: {bad}"

    # and no entry may still be labelled tentative
    tentative = [r for r, c in mat.INPUTNAME_ROLE.values() if c != "confirmed"]
    assert not tentative, f"unverified role names: {tentative}"


def test_channel_role_lists_reference_known_roles():
    """A channel list may not route to a role the table does not define."""
    known = {role for role, _ in mat.INPUTNAME_ROLE.values()}
    for name in ("BASE_COLOR_ROLES", "NORMAL_ROLES", "ROUGHNESS_ROLES",
                 "SPECULAR_ROLES", "ALPHA_ROLES", "TRANSMISSION_ROLES",
                 "OPACITY_ROLES", "EMISSION_ROLES", "SECONDARY_EMISSION_ROLES",
                 "TRANSLUCENCY_ROLES", "BLEND_MASK_ROLES", "FLOWMAP_ROLES"):
        unknown = [r for r in getattr(mat, name) if r not in known]
        assert not unknown, f"{name} routes to undefined roles: {unknown}"

    # the three mis-assignments the fabricated names caused must not come back
    assert "layer1_alpha_map" not in mat.ROUGHNESS_ROLES      # it is the alpha chain
    assert "layer1_alpha_map" in mat.ALPHA_ROLES
    # ...and `layerN_alpha_map` is no longer conflated with the opacity/transmission
    # tint: OPACITY_ROLES is now a deprecated alias of TRANSMISSION_ROLES.
    assert "layer1_alpha_map" not in mat.OPACITY_ROLES
    assert mat.OPACITY_ROLES == mat.TRANSMISSION_ROLES
    assert "layer0_back_lighting_map" not in mat.EMISSION_ROLES   # it is translucency
    # the two *_composite_specular maps were routed to BASE COLOUR under their
    # fabricated names ("layer0_rgba_surface" / "layer1_glass_rgba"); they are
    # specular/roughness data. (`layer0_specular_map` is a separate, correctly-named
    # pre-existing entry and is deliberately left where it was.)
    assert not any(r.endswith("_composite_specular") for r in mat.BASE_COLOR_ROLES)
