"""DXBC RDEF reader: bound-resource parse, `_decl` stripping, engine filtering.

Fixtures are synthetic containers built here — no game file, no Oodle — so this
runs under WSL `python3` with the rest of the core suite. The field offsets and
the two rules under test are `stream-confirmed` against
`2fd6839161785e9c`/`9a65d254b0c73e61` (74 binds, 0 mismatched); see
docs/MATERIALS.md.
"""

import struct

from le_mesh import dxbc
from le_mesh.material_scalars import symbol64


def _rdef(resources):
    """An RDEF chunk BODY for `[(name, input_type, bind)]`."""
    head = struct.pack("<IIII", 0, 0, len(resources), 16)
    table = b""
    strings = b""
    string_base = 16 + len(resources) * 32
    for name, input_type, bind in resources:
        table += struct.pack("<IIIIIIII", string_base + len(strings), input_type,
                             0, 0, 1, bind, 1, 0)
        strings += name.encode("ascii") + b"\x00"
    return head + table + strings


def _dxbc(chunks):
    """A DXBC container carrying `[(tag, body)]`."""
    n = len(chunks)
    header_len = 0x20 + n * 4
    offsets, blob = [], b""
    for tag, body in chunks:
        offsets.append(header_len + len(blob))
        blob += tag + struct.pack("<I", len(body)) + body
    total = header_len + len(blob)
    # "DXBC" + 16-byte digest + u32 version + u32 total size + u32 chunk count
    out = b"DXBC" + b"\x00" * 16 + struct.pack("<III", 1, total, n)
    out += b"".join(struct.pack("<I", o) for o in offsets)
    return out + blob


def test_bound_resources_reads_name_type_and_bind():
    slab = _dxbc([(b"RDEF", _rdef([
        ("k_shadow_map_decl", 2, 16),
        ("liv_helmet_glass_nml_decl", 2, 7),
        ("k_linear_clamp_sampler_decl", 3, 0),      # sampler: not an SRV
        ("perframecb_cb", 0, 0),                    # cbuffer: not an SRV
    ]))])
    res = dxbc.bound_resources(slab)
    assert [r.name for r in res] == ["k_shadow_map_decl", "liv_helmet_glass_nml_decl"]
    assert [r.bind for r in res] == [16, 7]
    assert all(r.dxbc_index == 0 for r in res)


def test_preimage_strips_the_cook_decl_suffix():
    slab = _dxbc([(b"RDEF", _rdef([("liv_hair_clr_decl", 2, 18)]))])
    r = dxbc.bound_resources(slab)[0]
    assert r.preimage == "liv_hair_clr"
    assert not r.is_engine_input


def test_material_binds_drop_engine_inputs():
    """`k_*` is bound by the renderer, never by the material (its array row
    carries `textureassetid == -1`)."""
    slab = _dxbc([(b"RDEF", _rdef([
        ("k_irradiance_0_decl", 2, 1),
        ("k_clustered_lights_decl", 5, 15),
        ("liv_basesuit_detail_a_msk_decl", 2, 26),
    ]))])
    assert dxbc.material_texture_binds(slab) == {26: "liv_basesuit_detail_a_msk"}


def test_the_law_name_hashes_to_the_textureassetid():
    """★ `symbol64(rdef_name - "_decl") == textureassetid`.

    Anchored on a real observed pair: `49a960afce4d4f2b` bind 27 declares
    `liv_evasuit_pack_a_detail_msk_decl` and its SShaderInputData slot 27 carries
    `textureassetid = 85e08905201cadc1`. `stream-confirmed`
    """
    slab = _dxbc([(b"RDEF", _rdef([("liv_evasuit_pack_a_detail_msk_decl", 2, 27)]))])
    name = dxbc.material_texture_binds(slab)[27]
    assert f"{symbol64(name):016x}" == "85e08905201cadc1"


def test_later_stage_wins_a_duplicated_register():
    """Material samplers live in the pixel shader; a vertex-stage collision on
    the same register must not shadow it."""
    slab = (_dxbc([(b"RDEF", _rdef([("jck_tool_emi_decl", 2, 18)]))])
            + _dxbc([(b"RDEF", _rdef([("liv_hair_nml_decl", 2, 18)]))]))
    assert dxbc.material_texture_binds(slab) == {18: "liv_hair_nml"}
    assert [r.dxbc_index for r in dxbc.bound_resources(slab)] == [0, 1]


def test_incidental_dxbc_bytes_are_not_a_container():
    """A shaderset slice is mostly bytecode; a stray b"DXBC" must be rejected on
    its own declared total-size / chunk-count fields, not parsed."""
    good = _dxbc([(b"RDEF", _rdef([("liv_hair_spc_decl", 2, 18)]))])
    slab = b"DXBC" + b"\xff" * 28 + good
    assert dxbc.material_texture_binds(slab) == {18: "liv_hair_spc"}
