"""Quest submesh -> material: the render-param SECTION mapping.

Render params are per DRAW SECTION, not per submesh, and a submesh routinely
owns several -- `34918b365c4b7940` has 6 submeshes and 14 render params. Reading
`renderparams[i]` for submesh `i` therefore hands every submesh after the first
multi-section one a neighbour's material, which is what put banner atlases on
`mpl_arena_a_lowspec`'s hull.

`CGMeshData +0x34` (first section) and `+0x38` (section count) state the real
mapping, and they tile the render-param array exactly on 475/475 mesh lists and
225/225 instanced models in the shipped tree.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from evr_quest import material as QM                       # noqa: E402
from evr_quest import mesh as QMESH                        # noqa: E402

#: (first section, section count) per submesh -- submesh 1 owns THREE sections,
#: so a per-submesh reading diverges from a per-section one at submesh 2.
SECTIONS = [(0, 1), (1, 3), (4, 1), (5, 2)]

#: The palette index each render param carries, one per section.
SECTION_MATIDX = [7, 1, 1, 1, 4, 9, 9]


def _compact_meshlist() -> bytes:
    """A `CGMeshListResource` in the compact `[u32 count][records]` form."""
    n = len(SECTIONS)
    blob = bytearray()
    blob += struct.pack("<I", n)
    for first, count in SECTIONS:
        record = bytearray(QMESH.MESH_REC)
        struct.pack_into("<I", record, QM.M_FIRST_SECTION, first)
        struct.pack_into("<I", record, QM.M_SECTION_COUNT, count)
        blob += record
    blob += struct.pack("<I", len(SECTION_MATIDX))
    for matidx in SECTION_MATIDX:
        record = bytearray(QMESH.RENDERPARAM_REC)
        struct.pack_into("<I", record, QM.RENDERPARAM_MATIDX, matidx)
        blob += record
    blob += bytes(64)                      # room for the tables that follow
    return bytes(blob)


def test_sections_tile_the_render_params():
    """The invariant the corpus check rests on, restated as a unit test."""
    assert sum(count for _first, count in SECTIONS) == len(SECTION_MATIDX)
    running = 0
    for first, count in SECTIONS:
        assert first == running
        running += count


def test_material_index_comes_from_the_submesh_s_first_section():
    blob = _compact_meshlist()
    index, _tables, _base = QM._render_param_indices(blob, len(SECTIONS))
    assert index == [SECTION_MATIDX[first] for first, _count in SECTIONS]
    assert index == [7, 1, 4, 9]


def test_the_per_submesh_reading_is_wrong_and_this_proves_it():
    """⛔ The bug this guards: `renderparams[i]` for submesh `i`.

    It agrees on submeshes 0 and 1 and diverges from submesh 2 on, which is why
    a casual spot-check of the first couple of meshes did not catch it.
    """
    naive = SECTION_MATIDX[:len(SECTIONS)]
    correct = [SECTION_MATIDX[first] for first, _count in SECTIONS]
    assert naive[:2] == correct[:2]
    assert naive != correct


def test_sections_are_read_per_submesh():
    blob = _compact_meshlist()
    assert QM._sections(blob, 4, len(SECTIONS)) == [f for f, _c in SECTIONS]


def test_a_truncated_record_yields_nothing_rather_than_garbage():
    assert QM._sections(b"\x00" * 8, 4, 4) == []


def test_an_out_of_range_section_refuses_the_whole_table():
    """A first-section past the render-param array is a decode failure."""
    n = len(SECTIONS)
    blob = bytearray(_compact_meshlist())
    struct.pack_into("<I", blob, 4 + QM.M_FIRST_SECTION, 999)
    index, _tables, _base = QM._render_param_indices(bytes(blob), n)
    assert index is None


# ── render state: read, never inferred ──────────────────────────────────

def test_transparent_mattypes_and_blendmodes_are_named():
    """The two fields that decide BLEND vs OPAQUE, pinned to their enums."""
    assert QM.OPAQUE_BLENDMODE == 0
    assert 2 in QM.TRANSPARENT_MATTYPES        # eMTForwardTransparent


def test_render_state_is_absent_rather_than_guessed(tmp_path):
    """No material file means no render state -- not a default of 'opaque'."""
    assert QM.render_state(tmp_path, "de" * 8) == {}


# ── the material's UV multiplier is layer-safe in the shared cache ───────

def test_the_vertex_colour_variant_is_keyed_by_LAYER_not_just_material():
    """⛔ The variant cache is keyed by NAME.

    Quest's baked-light variant multiplies by `EchoBake`; the PC path's
    `eDiffuseVertexColor` variant multiplies by `color0`. Sharing one name
    across both hands whichever was built first back to the other, silently
    multiplying by the wrong attribute. The default layer keeps the bare
    suffix so existing datablock names do not move.
    """
    import re
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "addon" /
              "lone_echo_import" / "material_builder.py").read_text(
                  encoding="utf-8")
    body = source[source.index("def vertex_color_variant"):]
    body = body[:body.index("\n    return var")]
    assert '__vcol_{layer_name}' in body
    assert re.search(r'layer_name == "color0"', body)
