"""Package round-trip + material spec classification."""

import math

from le_mesh import meshlist as ml
from le_mesh import materials as mat
from le_mesh import package as pkg
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


def test_package_roundtrip(tmp_path):
    fx = build_single_quad()
    objs = _objects(fx)
    objs[0].draws[0].material_key = "deadbeef00000001"
    material = mat.build_material_spec("deadbeef00000001", shaderset_hash="deadbeef00000001")

    out = pkg.write_package(tmp_path / "quad.lemesh",
                            source={"archive": "test", "meshlist": "quad"},
                            objects=objs, materials=[material])

    m = pkg.read_manifest(out)
    assert m["format"] == "lemesh" and m["version"] == 1
    assert len(m["objects"]) == 1
    mo = m["objects"][0]
    assert mo["vertex_count"] == 4
    assert mo["vertex_stride"] == 44
    assert len(mo["raw_vertex_format"]) == 6      # audit trail preserved
    assert mo["draws"][0]["material_key"] == "deadbeef00000001"

    # position blob round-trips exactly
    pos = pkg.load_blob(out, mo["attributes"]["position"]["blob"], "float32")
    assert list(pos[0:3]) == [0.0, 0.0, 0.0]
    assert list(pos[6:9]) == [1.0, 1.0, 0.0]

    # index blob round-trips
    idx = pkg.load_blob(out, mo["index"]["blob"], "uint32")
    assert list(idx) == [0, 1, 2, 0, 2, 3]


def test_material_role_classification():
    # a Group-A style shaderset: albedo(sRGB BC1), normal(BC5), opacity(BC4), emissive(BC1 sRGB)
    role_textures = {
        "layer0_albedo_map": "aaaa",
        "layer0_normal_map": "bbbb",
        "layer0_opacity_map": "cccc",
        "layer0_emissive_map": "dddd",
        "layer0_linear_map": "eeee",
    }
    dxgi = {"aaaa": 72, "bbbb": 83, "cccc": 80, "dddd": 72, "eeee": 71}
    ch = mat.classify_roles(role_textures, dxgi)
    assert ch["base_color"]["texture"] == "aaaa"
    assert ch["base_color"]["colorspace"] == "sRGB"
    assert ch["normal"]["texture"] == "bbbb"
    assert ch["normal"]["colorspace"] == "Non-Color"
    assert ch["normal"]["reconstruct_z"] is True     # BC5
    assert ch["opacity"]["texture"] == "cccc"
    assert ch["opacity"]["colorspace"] == "Non-Color"
    assert ch["emission"]["texture"] == "dddd"
    assert ch["emission"]["colorspace"] == "sRGB"
    assert ch["roughness"]["texture"] == "eeee"
    assert ch["roughness"]["colorspace"] == "Non-Color"   # linear BC1


def test_dxgi_fallback_for_unknown_slots():
    # unknown slots resolved purely by DXGI: BC5 -> normal, BC1 -> base color
    role_textures = {"unknown_s18": "n1", "unknown_s19": "c1"}
    dxgi = {"n1": 83, "c1": 72}
    ch = mat.classify_roles(role_textures, dxgi)
    assert ch["normal"]["texture"] == "n1"
    assert ch["base_color"]["texture"] == "c1"
