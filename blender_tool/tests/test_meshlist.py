"""Whole-meshlist decode on a synthetic single-quad CGMeshListData."""

import math

from le_mesh import meshlist as ml
from synthetic import build_single_quad


def _objects(fx):
    t = fx["tables"]
    return ml.build_objects(
        fx["primary"], fx["gpu"], fx["gpu_base"],
        meshes=ml.Table(*t["meshes"]),
        renderparams=ml.Table(*t["renderparams"]),
        vertexbuffers=ml.Table(*t["vertexbuffers"]),
        indexbuffers=ml.Table(*t["indexbuffers"]),
    )


def test_build_objects_geometry():
    fx = build_single_quad()
    objs = _objects(fx)
    assert len(objs) == 1
    o = objs[0]
    assert o.name_hash == 0xABCDEF0123456789
    assert o.vertex_count == 4
    assert o.vertex_stride == fx["expected"]["stride"]
    assert o.index_count == 6
    assert o.indices == fx["expected"]["indices"]
    assert not o.shadow_only

    exp = fx["expected"]
    pos = o.attributes["position"].data
    for i, (x, y, z) in enumerate(exp["vertices"]):
        assert pos[i * 3:i * 3 + 3] == [x, y, z]

    # uv1 (lightmap) present and decoded
    assert "uv1" in o.attributes
    assert math.isclose(o.attributes["uv1"].data[2], 1.0, abs_tol=1 / 65535)


def test_draws_and_material_index():
    fx = build_single_quad()
    o = _objects(fx)[0]
    assert len(o.draws) == 1
    d = o.draws[0]
    assert d.is_triangles
    assert d.idx_start == 0 and d.idx_count == 6
    assert d.shaderset_index == 0 and d.material_index == 0
    assert not d.is_lod_parent   # lodprimsetidx sentinel + count 0


def test_shadow_only_flag():
    fx = build_single_quad()
    # flip flags to eShadowOnly
    primary = bytearray(fx["primary"])
    import struct
    meshes_off = fx["tables"]["meshes"][1]
    struct.pack_into("<I", primary, meshes_off + ml.M_FLAGS, ml.FLAG_SHADOW_ONLY)
    fx["primary"] = bytes(primary)
    o = _objects(fx)[0]
    assert o.shadow_only is True
    assert "eShadowOnly" in o.flag_names
