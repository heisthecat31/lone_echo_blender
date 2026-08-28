"""Quest baked lighting: the atlas binding, and the chart that ISN'T one.

Quest bakes this level's lighting two ways, and they are not interchangeable:

* the HULL carries it PER VERTEX (`evr_quest.scene.baked_vertex_light`), and
* eight static-instanced props carry CGSI charts into an HDR atlas page.

⛔ Texcoord set 4 is NOT the hull's chart, and it looks enough like one to keep
getting picked up. Four independent tests reject it -- ground truth disagrees
on 16 of 16 submeshes; sampled colour anti-correlates with the vertex bake
(r = -0.34) with no coordinate convention fixing it (best +0.05 across all
eight swap/flip combinations); and its texel density is LESS uniform than the
plain texture UV's (spread 1.47 against 0.58), which inverts the one property
a lightmap parameterization exists to provide.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from evr_quest import scene as QS                          # noqa: E402

ATLAS = {"atlas": "baba1a3b4547dde5", "file": "textures/baba1a3b4547dde5.hdr"}


def _mesh(index, uv1=False, color0=False):
    return QS.QuestMesh(index=index, model="ab" * 8, positions=[0.0] * 9,
                        indices=[0, 1, 2], uv0=[0.0] * 6,
                        uv1=([0.0] * 6 if uv1 else []),
                        color0=([0.0] * 9 if color0 else []))


# ── atlas bindings ──────────────────────────────────────────────────────

def test_only_meshes_with_a_chart_are_bound():
    meshes = [_mesh(0, uv1=True), _mesh(1), _mesh(2, uv1=True)]
    images, gains, bindings = QS._lightmap_bindings(ATLAS, meshes)
    assert sorted(bindings) == ["0", "2"]
    assert list(images.values()) == ["baba1a3b4547dde5.hdr"]
    assert set(bindings["0"]) == {"image"}


def test_the_gain_is_one_because_the_page_ships_as_hdr():
    """⛔ The PC path divides 8-bit pages by a percentile and records the
    divisor. This page keeps its peak, so applying a gain would double-scale."""
    _images, gains, _b = QS._lightmap_bindings(ATLAS, [_mesh(0, uv1=True)])
    assert set(gains.values()) == {1.0}


def test_no_chart_means_no_binding_rather_than_an_empty_image():
    assert QS._lightmap_bindings(ATLAS, [_mesh(0)]) == ({}, {}, {})


def test_a_level_with_no_atlas_binds_nothing():
    assert QS._lightmap_bindings({}, [_mesh(0, uv1=True)]) == ({}, {}, {})


# ── the shared-material hazard ──────────────────────────────────────────

def _sidecar(tmp_path, slots):
    doc = {"materials": [{"matidx": s, "shdidx": 0, "spec": {"key": f"k{s}"}}
                         for s in slots]}
    (tmp_path / "materials.json").write_text(json.dumps(doc), encoding="utf-8")


def test_a_material_shared_with_an_unlit_mesh_is_split(tmp_path):
    """⛔ The add-on wires the atlas into the MATERIAL.

    A lit and an unlit mesh sharing one material means the unlit one samples
    the atlas through whatever UV layer is active -- its texture coordinates.
    """
    _sidecar(tmp_path, [7])
    meshes = [_mesh(0, uv1=True), _mesh(1)]
    out = QS._privatise_lit_materials(tmp_path, meshes, {0: 7, 1: 7})
    assert out[1] == 7                       # the unlit mesh keeps the original
    assert out[0] != 7                       # the lit one moves to its own slot
    doc = json.loads((tmp_path / "materials.json").read_text(encoding="utf-8"))
    slots = {e["matidx"] for e in doc["materials"]}
    assert out[0] in slots and len(doc["materials"]) == 2


def test_a_material_used_only_by_lit_meshes_is_left_alone(tmp_path):
    """No leak is possible, so no split -- splitting anyway would multiply
    material datablocks across a level for nothing."""
    _sidecar(tmp_path, [7])
    meshes = [_mesh(0, uv1=True), _mesh(1, uv1=True)]
    out = QS._privatise_lit_materials(tmp_path, meshes, {0: 7, 1: 7})
    assert out == {0: 7, 1: 7}
    doc = json.loads((tmp_path / "materials.json").read_text(encoding="utf-8"))
    assert len(doc["materials"]) == 1


def test_a_split_clone_keeps_the_spec_but_takes_a_new_key(tmp_path):
    _sidecar(tmp_path, [7])
    meshes = [_mesh(0, uv1=True), _mesh(1)]
    QS._privatise_lit_materials(tmp_path, meshes, {0: 7, 1: 7})
    doc = json.loads((tmp_path / "materials.json").read_text(encoding="utf-8"))
    clone = [e for e in doc["materials"] if e["matidx"] != 7][0]
    assert clone["spec"]["key"].endswith("__lm")


def test_a_missing_sidecar_is_a_no_op(tmp_path):
    assert QS._privatise_lit_materials(tmp_path, [_mesh(0, uv1=True)],
                                       {0: 7}) == {0: 7}


# ── irradiance is EMITTED, never multiplied into base colour ────────────

def _variant_source(name):
    src = (Path(__file__).resolve().parents[1] / "addon" / "lone_echo_import" /
           "material_builder.py").read_text(encoding="utf-8")
    body = src[src.index(f"def {name}("):]
    return body[:body.index("\n    return var")]


def test_the_vertex_bake_is_wired_to_emission_not_base_colour():
    """★ GUARD. The bake is IRRADIANCE, so `emission = albedo * bake`.

    ⛔ Multiplying it into Base Color leaves it as a reflectance and the
    renderer lights it AGAIN with the scene lamps. Quest ships four lights for
    a level it bakes almost entirely, so that second pass adds nearly nothing
    and the arena renders essentially black -- which is exactly what the first
    attempt produced. `evr_lighting._wire_radiance` documents the same trap for
    the atlas; this is the vertex-attribute twin of it.
    """
    body = _variant_source("vertex_radiance_variant")
    assert "Emission Color" in body
    # it reads Base Color only to SOURCE the albedo, never to write it
    assert 'nt.links.new(out, emission)' in body
    assert 'nt.links.new(mix.outputs[2], base_in)' not in body


def test_existing_emission_is_summed_not_replaced():
    """⛔ Linking straight into the socket is how the lightmap path once threw
    away every rim glow in the level -- the team colour on the geo panels."""
    body = _variant_source("vertex_radiance_variant")
    assert 'if emission.is_linked:' in body
    assert '"ADD"' in body


def test_the_base_colour_multiply_variant_is_still_available_for_TINTS():
    """`eDiffuseVertexColor` is a genuine tint and DOES multiply base colour.
    The two must not be confused -- different data, different wiring."""
    body = _variant_source("vertex_color_variant")
    assert 'nt.links.new(mix.outputs[2], base_in)' in body


def test_the_two_variants_cannot_collide_in_the_material_cache():
    tint = _variant_source("vertex_color_variant")
    bake = _variant_source("vertex_radiance_variant")
    assert '__vcol' in tint and '__bake_' in bake
