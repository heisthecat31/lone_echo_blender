"""The MESH-level (scene-set) LOD system, and the component attach transform.

Both landed 2026-08-05 from the `liv_head` z-fight and the eyeballed `+1.64`
head placement. Properties of the CODE only — no test here asserts an on-disk
census, and every fixture-dependent test raises `unittest.SkipTest` with a
reason when the package is absent — never a silent pass.
"""

import json
import struct
import sys
from pathlib import Path
from unittest import SkipTest

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL),
          str(BLENDER_TOOL / "addon" / "lone_echo_import")):
    if p not in sys.path:
        sys.path.insert(0, p)

from le_mesh import meshlist as ml                             # noqa: E402
from le_mesh.attach import (                                   # noqa: E402
    IDENTITY4, asset_matrix_to_blender, component_attach_matrix, mat4_mul,
)
# ⚠ import the MODULE, not the package: `lone_echo_import/__init__.py`
# imports bpy, which `run_tests.py` runs without.
from package_reader import select_lod_objects           # noqa: E402



# --- tiny assertion helpers (the runner collects plain functions) -----------
def assert_eq(a, b, msg=""):
    assert a == b, msg or f"{a!r} != {b!r}"


def assert_close(a, b, places=7, msg=""):
    assert abs(a - b) < 10 ** -places, msg or f"{a!r} !~ {b!r}"


def assert_ge(a, b, msg=""):
    assert a >= b, msg or f"{a!r} < {b!r}"


def assert_le(a, b, msg=""):
    assert a <= b, msg or f"{a!r} > {b!r}"


def assert_lt(a, b, msg=""):
    assert a < b, msg or f"{a!r} >= {b!r}"


def assert_true(a, msg=""):
    assert a, msg or "expected truthy"


CHARS = BLENDER_TOOL / "exports" / "chars"
LIV_HEAD = CHARS / "9a65d254b0c73e61_3ae4822821fa8562.lemesh"
LIV_BODY = CHARS / "2fd6839161785e9c_ff91757c910ea7b6.lemesh"


def _rp_bytes(mask: int, *, material=0, shaderset=0, primtype=4,
              idx_start=0, idx_count=3):
    """One synthetic `CGRenderParams` record."""
    b = bytearray(ml.RENDERPARAM_STRIDE)
    b[ml.RP_SCENEMASK:ml.RP_SCENEMASK + ml.RP_SCENEMASK_BYTES] = \
        mask.to_bytes(ml.RP_SCENEMASK_BYTES, "little")
    struct.pack_into("<I", b, ml.RP_SCENEMASK_MINCOUNT, 1)
    struct.pack_into("<I", b, ml.RP_MATERIALIDX, material)
    struct.pack_into("<I", b, ml.RP_SHADERSETIDX, shaderset)
    struct.pack_into("<I", b, ml.RP_PRIMTYPE, primtype)
    struct.pack_into("<I", b, ml.RP_IDXSTART, idx_start)
    struct.pack_into("<I", b, ml.RP_IDXCOUNT, idx_count)
    struct.pack_into("<I", b, ml.RP_LODPRIMSETIDX, 0xFFFFFFFF)
    return bytes(b)



def test_scenemask_occupies_the_head_of_the_record():
    # The mask must not overlap the fields the decoder already reads.
    assert_eq(ml.RP_SCENEMASK, 0x00)
    assert_le(ml.RP_SCENEMASK + ml.RP_SCENEMASK_BYTES,
                         ml.RP_MATERIALIDX)
    assert_lt(ml.RP_SCENEMASK_MINCOUNT, ml.RP_MATERIALIDX)

def test_mask_and_bit_are_read_from_the_record():
    primary = _rp_bytes(0x4) + _rp_bytes(0x1)
    table = ml.Table(count=2, data_off=0)
    draws = ml._read_draws(primary, table, 0, 2)
    assert_eq([d.scene_mask for d in draws], [0x4, 0x1])
    assert_eq([d.scene_set_bit for d in draws], [2, 0])
    assert_eq(draws[0].scene_set_min_count, 1)

def test_zero_mask_is_ungated():
    primary = _rp_bytes(0)
    draws = ml._read_draws(primary, ml.Table(1, 0), 0, 1)
    assert_eq(draws[0].scene_mask, 0)
    assert_eq(draws[0].scene_set_bit, -1)

def test_lowest_bit_wins_for_a_multi_set_draw():
    primary = _rp_bytes(0b1010)
    draws = ml._read_draws(primary, ml.Table(1, 0), 0, 1)
    assert_eq(draws[0].scene_set_bit, 1)


class _Obj:
    def __init__(self, mesh_index, masks):
        self.mesh_index = mesh_index
        self.draws = [ml.Draw(renderparam_index=i, idx_start=0, idx_count=3,
                              primtype=4, shaderset_index=0, material_index=0,
                              permutation=0, sort_priority=0,
                              lod_primset_idx=0xFFFFFFFF, lod_children_start=0,
                              lod_children_count=0, scene_mask=m)
                      for i, m in enumerate(masks)]



def test_no_scene_lod_when_fewer_than_two_masks():
    assert_eq(ml.scene_set_lod_levels([_Obj(0, [0]), _Obj(1, [0])]), {})
    assert_eq(ml.scene_set_lod_levels([_Obj(0, [1]), _Obj(1, [1])]), {})

def test_levels_follow_the_lowest_set_bit():
    levels = ml.scene_set_lod_levels(
        [_Obj(0, [0x1, 0x1]), _Obj(1, [0x2]), _Obj(2, [0x4])])
    assert_eq(levels, {0: 0, 1: 1, 2: 2})

def test_ungated_mesh_reads_as_level_zero():
    levels = ml.scene_set_lod_levels([_Obj(0, [0x1]), _Obj(1, [0x2]), _Obj(2, [0])])
    assert_eq(levels[2], 0)



OBJS = [{"name": "a", "scene_lod_level": 0},
        {"name": "b", "scene_lod_level": 1},
        {"name": "c", "scene_lod_level": 3},
        {"name": "d", "scene_lod_level": None}]

def test_level_zero_keeps_level_zero_and_ungated():
    got = [o["name"] for o in select_lod_objects(OBJS, 0)]
    assert_eq(got, ["a", "d"])

def test_negative_level_keeps_everything():
    assert_eq(len(select_lod_objects(OBJS, -1)), 4)

def test_clamped_to_the_coarsest_level_present():
    got = [o["name"] for o in select_lod_objects(OBJS, 9)]
    assert_eq(got, ["c", "d"])

def test_package_without_the_key_is_untouched():
    objs = [{"name": "a"}, {"name": "b"}]
    assert_eq(len(select_lod_objects(objs, 0)), 2)

def test_all_level_zero_is_untouched():
    objs = [{"name": "a", "scene_lod_level": 0}, {"name": "b", "scene_lod_level": 0}]
    assert_eq(len(select_lod_objects(objs, 0)), 2)



def test_mat4_mul_identity():
    m = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]
    assert_eq(mat4_mul(m, IDENTITY4), [float(x) for x in m])

def test_unresolved_when_the_root_has_no_twin():
    host = {"joints": [{"index": 0, "name_hash": "aaaa", "parent": -1,
                        "object_bind": IDENTITY4, "inverse_bind": IDENTITY4}]}
    part = {"joints": [{"index": 0, "name_hash": "bbbb", "parent": -1,
                        "object_bind": IDENTITY4, "inverse_bind": IDENTITY4}]}
    got = component_attach_matrix(host, part)
    assert_eq(got["confidence"], "unresolved")
    assert_eq(got["matrix"], IDENTITY4)

def test_matrix_is_host_bind_times_part_inverse_bind():
    seat = [2.0, 0, 0, 0,
            0, 2.0, 0, 1.5,
            0, 0, 2.0, -0.25,
            0, 0, 0, 1.0]
    host = {"joints": [{"index": 7, "name_hash": "deadbeef", "parent": -1,
                        "object_bind": seat, "inverse_bind": IDENTITY4}]}
    part = {"joints": [{"index": 0, "name_hash": "deadbeef", "parent": -1,
                        "object_bind": IDENTITY4, "inverse_bind": IDENTITY4}]}
    got = component_attach_matrix(host, part)
    assert_eq(got["confidence"], "stream-confirmed")
    assert_eq(got["host_joint_index"], 7)
    for a, b in zip(got["matrix"], seat):
        assert_close(a, b, places=6)

def test_part_root_is_the_parentless_joint_not_index_zero():
    part = {"joints": [
        {"index": 0, "name_hash": "child", "parent": 1,
         "object_bind": IDENTITY4, "inverse_bind": IDENTITY4},
        {"index": 1, "name_hash": "root", "parent": -1,
         "object_bind": IDENTITY4, "inverse_bind": IDENTITY4}]}
    host = {"joints": [{"index": 3, "name_hash": "root", "parent": -1,
                        "object_bind": IDENTITY4, "inverse_bind": IDENTITY4}]}
    got = component_attach_matrix(host, part)
    assert_eq(got["part_root_index"], 1)
    assert_eq(got["host_joint_index"], 3)

def test_asset_to_blender_swaps_y_and_minus_z():
    # asset translate (0, 1.65, -0.0276) -> blender (0, +0.0276, 1.65)
    m = list(IDENTITY4)
    m[3], m[7], m[11] = 0.0, 1.65, -0.0276
    b = asset_matrix_to_blender(m)
    assert_close(b[3], 0.0, places=6)
    assert_close(b[7], 0.0276, places=6)
    assert_close(b[11], 1.65, places=6)

def test_asset_to_blender_preserves_uniform_scale():
    m = [1.06 if i in (0, 5, 10) else (1.0 if i == 15 else 0.0) for i in range(16)]
    b = asset_matrix_to_blender(m)
    for i in (0, 5, 10):
        assert_close(b[i], 1.06, places=6)




def test_liv_head_partitions_into_at_least_two_scene_lods():
    man = LIV_HEAD / "manifest.json"
    if not man.is_file():
        raise SkipTest(
            f"{LIV_HEAD.name} (Liv's head) is not extracted — "
            f"`blender_tool/exports/` is gitignored extracted game data, so a "
            f"clean checkout has none. Re-extract with `python.exe "
            f"blender_tool/extractor/le_extract.py --archive 9a65d254b0c73e61 "
            f"--mesh 3ae4822821fa8562` to make this test able to run. ⛔ WHILE "
            f"THIS SKIP IS ACTIVE NOTHING VERIFIES THAT A REAL HEAD PARTITIONS "
            f"INTO AT LEAST TWO SCENE LODs — THE z-FIGHT THIS SYSTEM EXISTS "
            f"TO FIX.")
    objs = json.loads(man.read_text(encoding="utf-8"))["objects"]
    levels = {o.get("scene_lod_level") for o in objs}
    if levels == {None}:
        raise SkipTest(
            f"{LIV_HEAD.name} predates the `scene_lod_level` backfill — every "
            f"object's level is None, so there is no partition to check. "
            f"Re-extract with `python.exe blender_tool/extractor/le_extract.py "
            f"--archive 9a65d254b0c73e61 --mesh 3ae4822821fa8562` to make this "
            f"test able to run. ⛔ WHILE THIS SKIP IS ACTIVE NOTHING VERIFIES "
            f"THAT A REAL HEAD PARTITIONS INTO AT LEAST TWO SCENE LODs.")
    assert_ge(len({x for x in levels if x is not None}), 2)
    kept = select_lod_objects(objs, 0)
    assert_lt(len(kept), len(objs))
    assert_ge(len(kept), 1)

def test_scene_lod_agrees_with_the_meshlist_chain_where_both_exist():
    """The body carries BOTH systems, and they must agree draw for draw.

    That agreement is what promotes "scene-set bit N == LOD level N" from
    `inferred` to corroborated: on Liv's body every draw's `lod.level` from
    the `lodchildindices` chain equals its `scene_set_bit`.
    """
    man = LIV_BODY / "manifest.json"
    if not man.is_file():
        raise SkipTest(
            f"{LIV_BODY.name} (Liv's body, the one package carrying BOTH LOD "
            f"systems) is not extracted — `blender_tool/exports/` is "
            f"gitignored extracted game data, so a clean checkout has none. "
            f"Re-extract with `python.exe blender_tool/extractor/le_extract.py "
            f"--archive 2fd6839161785e9c --mesh ff91757c910ea7b6` to make this "
            f"test able to run. ⛔ WHILE THIS SKIP IS ACTIVE NOTHING "
            f"CORROBORATES \"scene-set bit N == LOD level N\" AGAINST THE "
            f"`lodchildindices` CHAIN — THAT CLAIM FALLS BACK TO `inferred`.")
    objs = json.loads(man.read_text(encoding="utf-8"))["objects"]
    pairs = [(d["lod"]["level"], d.get("scene_set_bit"))
             for o in objs for d in o.get("draws", [])
             if d.get("scene_set_bit") is not None and d.get("scene_set_bit") >= 0
             and len(o.get("draws", [])) > 1]
    if not pairs:
        raise SkipTest(
            f"{LIV_BODY.name} carries no object with >1 draw AND a "
            f"`scene_set_bit` >= 0, so the two LOD systems never overlap in "
            f"it and there is no pair to compare. Re-extract it with "
            f"`python.exe blender_tool/extractor/le_extract.py --archive "
            f"2fd6839161785e9c --mesh ff91757c910ea7b6` (a package predating "
            f"the scene-set decode stores no `scene_set_bit`) to make this "
            f"test able to run. ⛔ WHILE THIS SKIP IS ACTIVE NOTHING "
            f"CORROBORATES \"scene-set bit N == LOD level N\" AGAINST THE "
            f"`lodchildindices` CHAIN — THAT CLAIM FALLS BACK TO `inferred`.")
    assert_true(all(a == b for a, b in pairs),
                    f"chain level != scene-set bit: {sorted(set(pairs))}")

