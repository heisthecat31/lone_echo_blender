"""Archive-free tests for the .lescatter package writer (scripts/le_scene_extract).

Round-trips synthetic meshes + instances through write_package and re-reads the
manifest.json + blobs, asserting the PINNED contract (byte layout, blob shapes,
optional-attr omission, proxy flag, instance record = u32 + 11 f32). No game
files / Oodle -- only the pure-stdlib writer half of the module is exercised.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

# le_scene_extract lives in scripts/ (run_tests only adds blender_tool + tests).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from le_scene_extract import (  # noqa: E402
    SceneMesh, SceneInstance, write_package, box_proxy, _first_k,
    PACKAGE_FORMAT, PACKAGE_VERSION,
)


def _sample_meshes():
    # mesh A: full attrs (position + normal + uv0), 3 verts / 3 indices
    a = SceneMesh(
        index=0, name_hash=0xABCDEF0123456789, matidx=5, shdidx=9,
        aabb_min=(-1.0, -2.0, -3.0), aabb_max=(1.0, 2.0, 3.0),
        instance_offset=0, instance_count=2,
        positions=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        indices=[0, 1, 2],
        normals=[0.0, 0.0, 1.0] * 3,
        uv0=[0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        proxy=False)
    # mesh B: position + indices only (no normals/uv0), a proxy box
    pos, idx = box_proxy((-1.0, -1.0, -1.0), (2.0, 3.0, 4.0))
    b = SceneMesh(
        index=7, name_hash=0x1122334455667788, matidx=0xFFFFFFFF, shdidx=0xFFFFFFFF,
        aabb_min=(-1.0, -1.0, -1.0), aabb_max=(2.0, 3.0, 4.0),
        instance_offset=2, instance_count=1,
        positions=pos, indices=idx, proxy=True)
    return a, b


def _sample_instances():
    return [
        SceneInstance(0, (10.0, 20.0, 30.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0)),
        SceneInstance(0, (1.5, -2.5, 3.0), (0.0, 0.0, 0.0, 1.0), (2.0, 0.5, 1.0)),
        SceneInstance(7, (-4.0, 0.0, 8.0), (0.5, 0.5, 0.5, 0.5), (1.0, 1.0, 1.0)),
    ]


def test_manifest_shape_and_contract(tmp_path):
    a, b = _sample_meshes()
    insts = _sample_instances()
    pkg = write_package(tmp_path / "scene.lescatter", "942c829457a04a62", [a, b], insts)

    manifest = json.loads((pkg / "manifest.json").read_text())
    assert manifest["format"] == PACKAGE_FORMAT == "le_scatter"
    # v5 == the per-instance baked lightmap stream, on top of v4's uv1 blob +
    # CGMeshData lightmap ids + master lightmap binding (purely additive at every
    # step; see le_scene_extract.PACKAGE_VERSION).
    assert manifest["version"] == PACKAGE_VERSION == 5
    assert manifest["master"] == "942c829457a04a62"
    assert manifest["axis"] == "native"
    assert manifest["num_meshes"] == 2
    assert manifest["num_instances"] == 3
    assert manifest["instances_blob"] == "blobs/instances.bin"

    ma, mb = manifest["meshes"]
    assert ma["index"] == 0 and mb["index"] == 7
    assert ma["name_hash"] == "abcdef0123456789"
    assert ma["matidx"] == 5 and ma["shdidx"] == 9
    assert ma["aabb_min"] == [-1.0, -2.0, -3.0]
    assert ma["aabb_max"] == [1.0, 2.0, 3.0]
    assert ma["instance_offset"] == 0 and ma["instance_count"] == 2
    assert ma["nverts"] == 3 and ma["nindices"] == 3
    assert ma["proxy"] is False and mb["proxy"] is True
    # optional keys present only when data exists
    assert "normals" in ma and "uv0" in ma
    assert "normals" not in mb and "uv0" not in mb


def test_position_and_index_blobs_roundtrip(tmp_path):
    a, _ = _sample_meshes()
    pkg = write_package(tmp_path / "s.lescatter", "H", [a], [])
    manifest = json.loads((pkg / "manifest.json").read_text())
    ma = manifest["meshes"][0]

    pos = (pkg / ma["positions"]).read_bytes()
    assert len(pos) == a.nverts * 3 * 4
    got = struct.unpack(f"<{a.nverts * 3}f", pos)
    assert list(got) == a.positions

    idx = (pkg / ma["indices"]).read_bytes()
    assert len(idx) == a.nindices * 4
    assert list(struct.unpack(f"<{a.nindices}I", idx)) == a.indices

    nrm = (pkg / ma["normals"]).read_bytes()
    assert list(struct.unpack(f"<{a.nverts * 3}f", nrm)) == a.normals
    uv = (pkg / ma["uv0"]).read_bytes()
    assert list(struct.unpack(f"<{a.nverts * 2}f", uv)) == a.uv0


def test_instances_blob_layout(tmp_path):
    a, b = _sample_meshes()
    insts = _sample_instances()
    pkg = write_package(tmp_path / "s.lescatter", "H", [a, b], insts)

    raw = (pkg / "blobs" / "instances.bin").read_bytes()
    assert len(raw) == len(insts) * 44          # 4 + 11*4
    for i, inst in enumerate(insts):
        rec = struct.unpack_from("<I10f", raw, i * 44)
        assert rec[0] == inst.mesh_index
        assert tuple(rec[1:4]) == inst.translation
        assert tuple(round(v, 5) for v in rec[4:8]) == inst.rotation
        assert tuple(rec[8:11]) == inst.scale


def test_box_proxy_bounds_and_topology():
    pos, idx = box_proxy((-1.0, -2.0, -3.0), (4.0, 5.0, 6.0))
    assert len(pos) == 8 * 3
    assert len(idx) == 12 * 3
    xs = pos[0::3]; ys = pos[1::3]; zs = pos[2::3]
    assert (min(xs), min(ys), min(zs)) == (-1.0, -2.0, -3.0)
    assert (max(xs), max(ys), max(zs)) == (4.0, 5.0, 6.0)
    assert max(idx) == 7 and min(idx) == 0     # references all 8 verts, no OOB


def test_first_k_reshape():
    # 2 verts, 4 comps each -> keep first 3
    flat = [1, 2, 3, 99, 4, 5, 6, 99]
    assert _first_k(flat, 4, 3, 2) == [1, 2, 3, 4, 5, 6]
    # comps == k -> identity (same object ok)
    assert _first_k([1, 2, 3, 4], 2, 2, 2) == [1, 2, 3, 4]
    # short row padded
    assert _first_k([1, 2], 2, 3, 1) == [1, 2, 0.0]


def test_draws_roundtrip_multidraw(tmp_path):
    # a 4-vert quad with 12 indices (4 tris) split across TWO draws with distinct
    # (matidx, shdidx) pairs -> assert the v2 `draws` list round-trips per draw.
    mesh = SceneMesh(
        index=3, name_hash=0xFEEDFACECAFEBEEF, matidx=5, shdidx=9,
        aabb_min=(0.0, 0.0, 0.0), aabb_max=(1.0, 1.0, 0.0),
        instance_offset=0, instance_count=1,
        positions=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0],
        indices=[0, 1, 2, 0, 2, 3, 0, 1, 3, 1, 2, 3],
        draws=[
            {"matidx": 5, "shdidx": 9, "idx_start": 0, "idx_count": 6},
            {"matidx": 7, "shdidx": 3, "idx_start": 6, "idx_count": 6},
        ],
        proxy=False)
    pkg = write_package(tmp_path / "md.lescatter", "H", [mesh], [])
    manifest = json.loads((pkg / "manifest.json").read_text())
    assert manifest["version"] == 5

    m0 = manifest["meshes"][0]
    # top-level pair stays draw[0] (back-compat)
    assert m0["matidx"] == 5 and m0["shdidx"] == 9
    # draws present and round-trip exactly (matidx/shdidx/idx_start/idx_count)
    assert "draws" in m0 and len(m0["draws"]) == 2
    assert m0["draws"][0] == {"matidx": 5, "shdidx": 9,
                              "idx_start": 0, "idx_count": 6}
    assert m0["draws"][1] == {"matidx": 7, "shdidx": 3,
                              "idx_start": 6, "idx_count": 6}


def test_draws_key_present_for_every_mesh(tmp_path):
    # write_package emits "draws" for every mesh, even ones built without explicit
    # draws (default empty list) -> the key must exist (value may be []).
    a, b = _sample_meshes()
    pkg = write_package(tmp_path / "s.lescatter", "H", [a, b], [])
    manifest = json.loads((pkg / "manifest.json").read_text())
    for m in manifest["meshes"]:
        assert "draws" in m and isinstance(m["draws"], list)
