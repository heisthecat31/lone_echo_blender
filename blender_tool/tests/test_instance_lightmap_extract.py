"""Per-instance baked lightmap: the decoder, the `.lescatter` v5 section, and
the `.lemesh` `lightmap` manifest section.

Archive-free — everything here runs on synthetic bytes built to the SAME record
model the shipped data uses, so the byte contract is pinned without Oodle:

  * one static-instance record = 44 B `SGPackedInstanceData` + `nverts` x
    `C2Vector` (2x f32) of lightmap UVs, i.e. `stride == 44 + 8*nverts`
    (`stream-confirmed` on 942c829457a04a62: meshes 0/1/468 -> 900/636/844);
  * the per-instance lightmap PAGE is `u16 @ rec+0x1a`, and it is what an
    INSTANCED draw uses (it disagrees with `CGMeshData.lmsliceindex` on 13,909
    of station_front's 21,394 instances);
  * `44*C + 8*sum(count*nverts)` reproduces `instancedatasize` exactly.

⛔ Do NOT port the Echo r15 model here: r15 packs a 48-B record and a 4-byte
`C2VectorU16N` UV held in a separate offsets-indexed buffer.
"""
from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from le_static_scatter import (  # noqa: E402
    INSTANCE_HEADER_BYTES, INSTANCE_UV_BYTES, INSTANCE_LIGHTMAPIDX_OFF,
    StaticMasterDecode, decode_instancetype_table, decode_instance_lightmap,
    decode_instance_transform, instance_lightmap_page, instance_uv_count,
)
from le_scene_extract import (  # noqa: E402
    PACKAGE_VERSION, SceneInstance, SceneMesh, write_package,
)

from le_mesh import lightmap as lm  # noqa: E402
from le_mesh import package as pkg  # noqa: E402


# ---------------------------------------------------------------------------
# synthetic master: 2 mesh-types, 5 instances, matching the real record model
# ---------------------------------------------------------------------------

MESHES = [
    # (nverts, instance_count, pages per instance)
    (3, 2, [7, 5]),
    (2, 3, [0, 12, 7]),
]


def _uv_pair(mesh: int, inst: int, vert: int) -> tuple[float, float]:
    """Distinct, exactly-representable f32s so a byte compare is meaningful."""
    return (mesh + inst / 4.0 + vert / 64.0, 0.5 + inst / 8.0)


def _build_region():
    """(region_bytes, itd_bytes, decode, expected_uv_floats) for MESHES."""
    strides = [INSTANCE_HEADER_BYTES + INSTANCE_UV_BYTES * nv
               for nv, _c, _p in MESHES]
    blocks, off = [], 0
    for (nv, cnt, _pages), stride in zip(MESHES, strides):
        blocks.append(off)
        off += cnt * stride
    ids = off

    region = bytearray(ids)
    expected: list[list[float]] = []
    for mi, ((nv, cnt, pages), stride, block) in enumerate(
            zip(MESHES, strides, blocks)):
        for j in range(cnt):
            rec = block + j * stride
            struct.pack_into("<3f", region, rec, mi, j, 1.0)          # pos
            struct.pack_into("<4h", region, rec + 12, 0, 0, 0, 32767)  # quat
            struct.pack_into("<3e", region, rec + 20, 1.0, 1.0, 1.0)   # scale
            struct.pack_into("<H", region, rec + INSTANCE_LIGHTMAPIDX_OFF, pages[j])
            flat: list[float] = []
            for v in range(nv):
                u, vv = _uv_pair(mi, j, v)
                struct.pack_into("<2f", region, rec + INSTANCE_HEADER_BYTES + v * 8,
                                 u, vv)
                flat += [u, vv]
            expected.append(flat)

    itd = bytearray()
    first = 0
    for mi, ((nv, cnt, _p), stride, block) in enumerate(
            zip(MESHES, strides, blocks)):
        words = [block // 4, first, stride // 4, INSTANCE_UV_BYTES // 4,
                 0, 0, sum(c for _n, c, _q in MESHES), 0, mi]
        itd += struct.pack("<9I", *words)
        first += cnt

    counts = [c for _n, c, _p in MESHES]
    offsets, acc = [], 0
    for c in counts:
        offsets.append(acc)
        acc += c
    decode = StaticMasterDecode(
        lod_node_count=0, num_instances=sum(counts), num_meshes=len(MESHES),
        instancescount=counts, instanceoffsets=offsets,
        gpu_instancedata=(0, ids), gpu_instancetypedata=(ids, len(itd)))
    return bytes(region), bytes(itd), decode, expected, ids


def _decoded():
    region, itd, decode, expected, ids = _build_region()
    tt = decode_instancetype_table(itd, decode.num_meshes)
    return region, tt, decode, expected, ids


# ---------------------------------------------------------------------------
# the record model
# ---------------------------------------------------------------------------

def test_stride_formula_matches_the_shipped_strides():
    # station_front 942c829457a04a62, meshes 0 / 1 / 468 -- `stream-confirmed`.
    assert instance_uv_count(900) == 107
    assert instance_uv_count(636) == 74
    assert instance_uv_count(844) == 100
    assert INSTANCE_HEADER_BYTES == 44 and INSTANCE_UV_BYTES == 8


def test_a_stride_that_is_not_44_plus_8n_is_loud():
    for bad in (43, 45, 47, 0 - 1):
        try:
            instance_uv_count(bad)
        except ValueError:
            continue
        raise AssertionError(f"stride {bad} should not decode")
    assert instance_uv_count(44) == 0        # a 0-vertex type is still legal


def test_page_is_u16_at_0x1a_and_does_not_disturb_the_transform():
    region, _tt, _d, _e, _ids = _decoded()
    assert INSTANCE_LIGHTMAPIDX_OFF == 0x1A
    assert instance_lightmap_page(region, 0) == 7
    # the f16 scale ends at +0x1a, so reading the page must leave scale.z intact
    t = decode_instance_transform(region, 0)
    assert t.scale == (1.0, 1.0, 1.0)
    assert t.translation == (0.0, 0.0, 1.0)


# ---------------------------------------------------------------------------
# decode_instance_lightmap
# ---------------------------------------------------------------------------

def test_decode_emits_global_instance_order():
    region, tt, d, expected, _ids = _decoded()
    got = decode_instance_lightmap(region, d, tt)
    assert got.count == d.num_instances == 5
    assert got.warnings == []
    assert got.counts == [3, 3, 2, 2, 2]
    assert got.pages == [7, 5, 0, 12, 7]
    # offsets are in PAIRS, and are the running sum of counts
    assert got.offsets == [0, 3, 6, 8, 10]
    assert got.total_uv_pairs == 12
    assert len(got.uv_bytes) == 12 * INSTANCE_UV_BYTES


def test_uvs_are_copied_verbatim_not_re_encoded():
    region, tt, d, expected, _ids = _decoded()
    got = decode_instance_lightmap(region, d, tt)
    for i, want in enumerate(expected):
        start = got.offsets[i] * INSTANCE_UV_BYTES
        n = got.counts[i] * 2
        vals = list(struct.unpack_from(f"<{n}f", got.uv_bytes, start))
        assert vals == want, f"instance {i}: {vals} != {want}"


def test_instances_of_one_mesh_can_carry_different_uvs():
    # ⛔ the shipped data DOES vary them (station_front: each instance owns its
    # own atlas strip), so the decoder must never dedupe by mesh-type.
    region, tt, d, _e, _ids = _decoded()
    got = decode_instance_lightmap(region, d, tt)
    a = bytes(got.uv_bytes[0:24])
    b = bytes(got.uv_bytes[24:48])
    assert a != b


def test_predicted_instancedatasize_has_zero_residual():
    region, tt, d, _e, ids = _decoded()
    got = decode_instance_lightmap(region, d, tt)
    # 44*C + 8*sum(count*nverts) must reproduce instancedatasize EXACTLY
    hand = sum(c * (44 + 8 * nv) for nv, c, _p in MESHES)
    assert got.predicted_instancedatasize == hand == ids
    assert got.predicted_instancedatasize - d.gpu_instancedata[1] == 0


def test_selected_subset_keeps_the_arrays_parallel():
    region, tt, d, _e, _ids = _decoded()
    got = decode_instance_lightmap(region, d, tt, selected={1})
    assert got.count == 3
    assert got.counts == [2, 2, 2]
    assert got.pages == [0, 12, 7]
    assert got.offsets == [0, 2, 4]
    # the residual identity is over EVERY instance, not just the emitted subset
    assert got.predicted_instancedatasize == sum(
        c * (44 + 8 * nv) for nv, c, _p in MESHES)


def test_vertex_count_disagreement_is_reported_not_silent():
    region, tt, d, _e, _ids = _decoded()
    got = decode_instance_lightmap(region, d, tt, nverts_by_mesh={0: 99})
    assert any("mesh 0" in w and "99" in w for w in got.warnings)
    # ... and it still decodes: a warning, never a silent drop
    assert got.count == 5


def test_page_histogram():
    region, tt, d, _e, _ids = _decoded()
    got = decode_instance_lightmap(region, d, tt)
    assert got.page_histogram() == {0: 1, 5: 1, 7: 2, 12: 1}


# ---------------------------------------------------------------------------
# .lescatter v5 package
# ---------------------------------------------------------------------------

def _sample_mesh(index=0, nverts=3):
    return SceneMesh(
        index=index, name_hash=0xABCDEF0123456789, matidx=1, shdidx=2,
        aabb_min=(0.0, 0.0, 0.0), aabb_max=(1.0, 1.0, 1.0),
        instance_offset=0, instance_count=2,
        positions=[0.0] * (3 * nverts), indices=[0, 1, 2])


def test_v5_section_absent_says_why(tmp_path):
    p = write_package(tmp_path / "s.lescatter", "H", [_sample_mesh()], [])
    m = json.loads((p / "manifest.json").read_text())
    assert m["version"] == PACKAGE_VERSION == 5
    sec = m["instance_lightmap"]
    assert sec["present"] is False
    assert sec["reason"]                       # "not extracted", never silent
    assert "uv_blob" not in sec                # nothing that implies bytes exist
    assert not (p / "blobs" / "instance_lm_uv.bin").exists()


def test_v5_blobs_round_trip(tmp_path):
    region, tt, d, expected, ids = _decoded()
    instlm = decode_instance_lightmap(region, d, tt)
    insts = [SceneInstance(0, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))
             for _ in range(instlm.count)]
    p = write_package(tmp_path / "s.lescatter", "942c829457a04a62",
                      [_sample_mesh()], insts, instance_lightmap=instlm,
                      instancedatasize=ids)
    m = json.loads((p / "manifest.json").read_text())
    sec = m["instance_lightmap"]

    assert sec["present"] is True
    assert sec["count"] == instlm.count == len(insts)
    assert sec["total_uv_pairs"] == 12
    assert sec["flip_v_applied"] is False      # raw UVs; flipping is the consumer's
    assert sec["instancedata_residual"] == 0
    assert sec["uv_blob"] == "blobs/instance_lm_uv.bin"
    assert sec["offsets_blob"] == "blobs/instance_lm_uvoff.bin"
    assert sec["counts_blob"] == "blobs/instance_lm_count.bin"
    assert sec["page_blob"] == "blobs/instance_lm_page.bin"

    uv = (p / sec["uv_blob"]).read_bytes()
    off = (p / sec["offsets_blob"]).read_bytes()
    cnt = (p / sec["counts_blob"]).read_bytes()
    page = (p / sec["page_blob"]).read_bytes()
    assert len(uv) == sec["total_uv_pairs"] * 8 == sec["uv_bytes"]
    # the three index blobs are u32 and PARALLEL to instances.bin
    for blob in (off, cnt, page):
        assert len(blob) == sec["count"] * 4
    assert len((p / "blobs" / "instances.bin").read_bytes()) == sec["count"] * 44

    offs = list(struct.unpack(f"<{sec['count']}I", off))
    cnts = list(struct.unpack(f"<{sec['count']}I", cnt))
    pages = list(struct.unpack(f"<{sec['count']}I", page))
    assert offs == [0, 3, 6, 8, 10]
    assert cnts == [3, 3, 2, 2, 2]
    assert pages == [7, 5, 0, 12, 7]           # u16 on disk, widened to u32 here
    for i, want in enumerate(expected):
        n = cnts[i] * 2
        got = list(struct.unpack_from(f"<{n}f", uv, offs[i] * 8))
        assert got == want


def test_v5_page_histogram_is_in_the_manifest(tmp_path):
    region, tt, d, _e, ids = _decoded()
    instlm = decode_instance_lightmap(region, d, tt)
    p = write_package(tmp_path / "s.lescatter", "H", [_sample_mesh()], [],
                      instance_lightmap=instlm, instancedatasize=ids)
    sec = json.loads((p / "manifest.json").read_text())["instance_lightmap"]
    assert sec["page_histogram"] == {"0": 1, "5": 1, "7": 2, "12": 1}


def test_v5_is_purely_additive_over_v4(tmp_path):
    """Every v1..v4 key keeps its name and layout in a v5 package."""
    mesh = _sample_mesh()
    mesh.uv1 = [0.25, 0.75, 0.5, 0.5, 0.0, 1.0]
    mesh.lightmap_index = 1
    mesh.lm_slice_index = 6
    mesh.numlobes = 4
    insts = [SceneInstance(0, (1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0))]
    p = write_package(tmp_path / "s.lescatter", "H", [mesh], insts,
                      lightmap={"resource_name": "deadbeefdeadbeef", "present": True})
    m = json.loads((p / "manifest.json").read_text())
    for key in ("format", "version", "master", "axis", "num_meshes",
                "num_instances", "meshes", "instances_blob", "lod",
                "lightmap_stats", "lightmap"):
        assert key in m, key
    e = m["meshes"][0]
    for key in ("index", "name_hash", "matidx", "shdidx", "draws", "aabb_min",
                "aabb_max", "instance_offset", "instance_count", "nverts",
                "nindices", "positions", "indices", "proxy", "uv1",
                "lightmap_index", "lm_slice_index", "numlobes"):
        assert key in e, key
    assert len((p / "blobs" / "instances.bin").read_bytes()) == 44   # unchanged
    assert len((p / "blobs" / "instance_lod.bin").read_bytes()) == 12


# ---------------------------------------------------------------------------
# .lemesh manifest `lightmap` section  (D3 delta §2)
# ---------------------------------------------------------------------------

STATION_COLOR = "0178fa39b1b95d2f"
STATION_AO0 = "81a8fcf99b655a42"


def _station_row():
    """station_front's master lightmap table, shape-faithful.

    The shipped table has 10 rows of which only ROW 1 is populated, and every
    lightmapped mesh carries `lightmapindex == 1` — which is what makes
    `lightmapindex` a DIRECT row index rather than an index over populated rows
    (`stream-confirmed`). Row 0 must therefore stay null here.
    """
    blob = struct.pack("<I", 2) + b"\xff" * lm.STRIDE + struct.pack(
        "<5Q", int(STATION_COLOR, 16), int(STATION_AO0, 16),
        int("81a8fcf99b655a43", 16), int("bd2f79f78fb557f1", 16),
        int("bd2f79f78fb543f1", 16))
    return lm.parse_lightmap_table(blob)


def _station_meta():
    return {
        STATION_COLOR: {"dxgi": 95, "width": 1024, "height": 1024, "arraysize": 65},
        STATION_AO0: {"dxgi": 83, "width": 1024, "height": 1024, "arraysize": 13},
    }


def test_manifest_lightmap_section_matches_the_shape_the_importer_reads():
    table = _station_row()
    binding = lm.resolve(table, 1, 6)
    files = {STATION_COLOR: f"lightmap/{STATION_COLOR}.dds",
             STATION_AO0: f"lightmap/{STATION_AO0}.dds"}
    sec = lm.manifest_lightmap_section(binding, files, texture_meta=_station_meta(),
                                       resource_name=0x942C829457A04A62)
    assert sec["color"]["hash"] == STATION_COLOR
    assert sec["color"]["file"] == f"lightmap/{STATION_COLOR}.dds"
    assert sec["ao0"]["hash"] == STATION_AO0
    assert sec["ao0"]["file"] == f"lightmap/{STATION_AO0}.dds"
    assert sec["resource"] == "942c829457a04a62"
    assert sec["row"] == 1
    # what makes slices_per_page DERIVED (65 / 13 == 5) rather than assumed
    assert sec["pages"] == 13
    assert sec["slices_per_page"] == 5
    assert sec["color"]["colorspace"] == lm.COLORSPACE_LIGHTMAP
    assert sec["ao0"]["colorspace"] == lm.COLORSPACE_DATA
    assert sec["color"]["dxgi_unexpected"] is False


def test_manifest_lightmap_section_reports_a_missing_copy_as_empty_file():
    binding = lm.resolve(_station_row(), 1)
    sec = lm.manifest_lightmap_section(binding, {}, texture_meta=_station_meta())
    # a path here would be a claim the bytes are in the package -- they are not
    assert sec["color"]["file"] == ""
    assert sec["color"]["hash"] == STATION_COLOR   # the hash still steers the scan
    assert sec["resource"] is None


def test_manifest_lightmap_section_is_empty_for_no_binding():
    assert lm.manifest_lightmap_section(None) == {}
    null = lm.parse_lightmap_table(struct.pack("<I", 1) + b"\xff" * 40)
    assert lm.resolve(null, 0) is None
    assert lm.manifest_lightmap_section(lm.resolve(null, 0)) == {}


def test_manifest_key_matches_the_addon_literal():
    """The extractor's key and the addon's `MANIFEST_KEY` must not drift.

    Read as TEXT: `lightmap_builder` imports `bpy` transitively in some paths and
    this suite must stay Blender-free.
    """
    src = (_ROOT / "blender_tool" / "addon" / "lone_echo_import"
           / "lightmap_builder.py").read_text(encoding="utf-8")
    m = re.search(r'^MANIFEST_KEY\s*=\s*"([^"]+)"', src, re.M)
    assert m, "lightmap_builder.MANIFEST_KEY not found"
    assert m.group(1) == lm.MANIFEST_KEY == "lightmap"


def _fake_object():
    class Elem:
        def __init__(self, usage, slot, type_name="eF32"):
            self.usage, self.slot, self.type_name = usage, slot, type_name

        def as_dict(self):
            return {"usage": self.usage, "slot": self.slot,
                    "type_name": self.type_name}

    class Obj:
        mesh_index = 0
        name_hash = 0x1122334455667788
        flags = 0
        flag_names = []
        shadow_only = False
        force_single_sided = False
        aabb_min = (0.0, 0.0, 0.0)
        aabb_max = (1.0, 1.0, 1.0)
        lightmap_index = 1
        lm_slice_index = 6
        numlobes = 4
        outline_mode = 0
        vertex_count = 3
        vertex_stride = 44
        attributes: dict = {}
        indices: list = []
        index_count = 0
        draws: list = []
        elements = [Elem(0, 0), Elem(4, 0), Elem(4, 4, "eU16n")]
    return Obj()


def test_lemesh_package_emits_the_section_only_when_resolved(tmp_path):
    binding = lm.resolve(_station_row(), 1)
    sec = lm.manifest_lightmap_section(binding, {}, texture_meta=_station_meta(),
                                       resource_name=0x942C829457A04A62)
    with_lm = pkg.write_package(tmp_path / "a.lemesh", source={}, objects=[_fake_object()],
                                materials=[], lightmap=sec)
    m = pkg.read_manifest(with_lm)
    assert m["lightmap"]["color"]["hash"] == STATION_COLOR
    assert m["version"] == pkg.VERSION == 2          # additive, no version bump

    without = pkg.write_package(tmp_path / "b.lemesh", source={},
                                objects=[_fake_object()], materials=[])
    m2 = pkg.read_manifest(without)
    assert "lightmap" not in m2                      # absent, never a guess
    assert m2["objects"][0]["lightmap_uv"] == "uv1"  # unchanged by this front
