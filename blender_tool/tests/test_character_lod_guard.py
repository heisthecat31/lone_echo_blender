"""The character scene-set LOD guard, the ungated mesh, and the variant de-dup.

All three landed 2026-08-05 from a full-roster audit of the twelve character
mesh-lists in the project notes §2
(a local working file, `stream-confirmed`), which
falsified the premise that a `SSceneSetMask` bit index is an LOD level.

Every fixture below is REAL SHIPPED DATA copied out of that probe, so a test
failing here means the shipped bytes changed or the policy did — not that a
synthetic case drifted.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon" / "lone_echo_import")):
    if p not in sys.path:
        sys.path.insert(0, p)

# ⚠ import the MODULE, not the package: `lone_echo_import/__init__.py` imports
# bpy, which `run_tests.py` runs without.
from package_reader import (                                   # noqa: E402
    SCENE_LOD_COLOCATION_MIN, drop_scene_set_variant_draws,
    object_is_scene_set_ungated, scene_lod_is_geometric_chain,
    select_lod_draws, select_lod_objects,
)
from le_mesh import materials as lemat                         # noqa: E402

CHARS = BLENDER_TOOL / "exports" / "chars"


def _obj(name, level, verts, lo, hi, masks):
    return {"name": name, "scene_lod_level": level, "vertex_count": verts,
            "aabb_min": list(lo), "aabb_max": list(hi),
            "draws": [{"renderparam_index": i, "idx_start": 0, "idx_count": 3,
                       "scene_mask": m} for i, m in enumerate(masks)]}


# ---------------------------------------------------------------------------
# ★ `64b4b5b2a0153f7e` — Jack, THE PLAYER'S OWN AVATAR. Levels 0 and 1 are the
# RIGHT and LEFT halves of the same suit: 18,440 vs 18,401 vertices (0.2 % apart)
# with union AABBs whose intersection volume is exactly 0.
# ---------------------------------------------------------------------------
JACK = [
    _obj("obj000", 3, 8888, (-0.6248, 1.0081, -0.1136), (-0.3102, 1.3323, 0.1334), [8]),
    _obj("obj001", 2, 8904, (0.3102, 1.0081, -0.1136), (0.6249, 1.3323, 0.1333), [4]),
    _obj("obj002", 0, 1754, (-0.1151, 1.4723, -0.1653), (0.1151, 1.8601, 0.1354), [0]),
    _obj("obj003", 0, 2501, (-0.1946, 0.5742, -0.1815), (0.1946, 1.5561, 0.1113), [0]),
    _obj("obj004", 0, 9781, (-0.2092, -0.0054, -0.1311), (0.2092, 1.1345, 0.1434), [0]),
    _obj("obj005", 0, 388, (-0.4936, 0.0551, -0.1606), (0.4936, 1.5462, 0.0692), [0]),
    _obj("obj006", 4, 160, (-0.1763, 0.6154, -0.0001), (0.1763, 0.9472, 0.0873), [16, 32]),
    _obj("obj007", 4, 362, (-0.176, 0.5928, -0.0936), (0.176, 1.283, 0.0809), [16, 32]),
    _obj("obj008", 0, 6017, (0.3113, 1.099, -0.1083), (0.5449, 1.3461, 0.0716), [17, 33]),
    _obj("obj009", 0, 1079, (0.0549, 1.2376, -0.1677), (0.4139, 1.5813, 0.0691), [17, 33]),
    _obj("obj010", 0, 11344, (0.4976, 0.9365, 0.016), (0.6279, 1.1481, 0.1458), [17, 33]),
    _obj("obj011", 1, 6022, (-0.5449, 1.099, -0.1083), (-0.3113, 1.3461, 0.0716), [18, 34]),
    _obj("obj012", 1, 1107, (-0.4139, 1.2376, -0.1677), (-0.0549, 1.5813, 0.0691), [18, 34]),
    _obj("obj013", 1, 11272, (-0.6279, 0.9365, 0.016), (-0.4976, 1.1481, 0.1458), [18, 34]),
]

# ★ `3ae4822821fa8562` = `liv_head` — a REAL chain: three co-located sets,
# 20,357 / 8,672 / 823 vertices, `ComponentLOD` naming them lod0/lod1/lod2.
# All 19 meshes, so the union AABBs are the shipped ones.
LIV_HEAD = [
    _obj("obj000", 0, 2178, (-0.076, -0.0089, -0.0057), (0.06, 0.1486, 0.1121), [1, 1]),
    _obj("obj001", 0, 1512, (-0.0749, -0.0004, -0.0004), (0.0685, 0.146, 0.1154), [1, 1]),
    _obj("obj002", 0, 814, (-0.0748, -0.0008, -0.0004), (0.072, 0.1486, 0.1093), [1]),
    _obj("obj003", 0, 1588, (-0.0721, -0.0006, -0.0004), (0.064, 0.1457, 0.1142), [1, 1]),
    _obj("obj004", 0, 1440, (-0.0739, -0.0005, -0.0004), (0.067, 0.1451, 0.1158), [1, 1]),
    _obj("obj005", 0, 327, (-0.0455, 0.0453, 0.0735), (0.0445, 0.0717, 0.0948), [1]),
    _obj("obj006", 0, 1563, (-0.038, -0.0438, -0.0222), (0.0359, 0.0301, 0.1008), [1]),
    _obj("obj007", 0, 3046, (-0.1035, -0.1742, -0.1025), (0.0982, 0.1713, 0.107), [1]),
    _obj("obj008", 0, 3156, (-0.0571, -0.0135, -0.0046), (0.0568, 0.0655, 0.1078), [1]),
    _obj("obj009", 0, 4733, (-0.0728, -0.0895, -0.0649), (0.0694, 0.1514, 0.1232), [1]),
    _obj("obj010", 1, 1088, (-0.0751, 0.0499, 0.0373), (0.06, 0.1486, 0.1116), [2, 2]),
    _obj("obj011", 1, 754, (-0.0749, 0.0609, 0.0512), (0.0685, 0.146, 0.1151), [2, 2]),
    _obj("obj012", 1, 402, (-0.0748, 0.0549, 0.0154), (0.072, 0.1486, 0.1093), [2]),
    _obj("obj013", 1, 792, (-0.0721, 0.0541, 0.0604), (0.064, 0.1457, 0.1142), [2, 2]),
    _obj("obj014", 1, 720, (-0.0734, 0.0642, 0.0542), (0.067, 0.1448, 0.1158), [2, 2]),
    _obj("obj015", 1, 1774, (-0.1026, -0.1742, -0.1025), (0.0982, 0.1713, 0.107), [2]),
    _obj("obj016", 1, 2815, (-0.0728, -0.0895, -0.0649), (0.0694, 0.1514, 0.1224), [2]),
    _obj("obj017", 1, 327, (-0.0455, 0.0453, 0.0735), (0.0445, 0.0717, 0.0948), [2]),
    _obj("obj018", 2, 823, (-0.1029, -0.1315, -0.0959), (0.0976, 0.1713, 0.1231), [4]),
]

# ★ `3cee9f282bf0807f` — a REAL chain whose FINEST set is bit 3, not bit 0.
# Two levels, 52,802 -> 9,002 vertices, and every mesh pairs with a sibling whose
# name-hash differs in ONE nibble (`42423bc7…` / `42423ac7…`). All 14 meshes.
ANDROID_D = [
    _obj("obj000", 3, 2468, (-0.1946, 1.0472, -0.1815), (0.1946, 1.5561, 0.1113), [8]),
    _obj("obj001", 3, 9781, (-0.2092, -0.0054, -0.1311), (0.2092, 1.1345, 0.1434), [8]),
    _obj("obj002", 3, 12201, (-0.5449, 0.6154, -0.1083), (0.5449, 1.3461, 0.0873), [8]),
    _obj("obj003", 3, 2549, (-0.4139, 0.5928, -0.1677), (0.4139, 1.5813, 0.0809), [8]),
    _obj("obj004", 3, 22612, (-0.6279, 0.9365, 0.016), (0.6279, 1.1481, 0.1458), [8]),
    _obj("obj005", 3, 388, (-0.4936, 0.0551, -0.1606), (0.4936, 1.5462, 0.0692), [8]),
    _obj("obj006", 3, 2803, (-0.0928, 1.2491, -0.2626), (0.0928, 1.5376, -0.1359), [8]),
    _obj("obj007", 4, 805, (-0.1946, 1.0552, -0.1759), (0.1946, 1.5561, 0.1054), [16]),
    _obj("obj008", 4, 3776, (-0.2072, -0.0031, -0.1311), (0.2072, 1.1345, 0.1434), [16]),
    _obj("obj009", 4, 1226, (-0.5446, 1.1015, -0.1076), (0.5446, 1.3308, 0.0697), [16]),
    _obj("obj010", 4, 158, (-0.4124, 1.2382, -0.163), (0.4124, 1.5795, 0.0642), [16]),
    _obj("obj011", 4, 2246, (-0.6243, 0.9405, 0.0203), (0.6243, 1.1464, 0.1445), [16]),
    _obj("obj012", 4, 598, (-0.0922, 1.2526, -0.2633), (0.0922, 1.537, -0.1374), [16]),
    _obj("obj013", 4, 193, (-0.4966, 0.0551, -0.1606), (0.4964, 1.5426, 0.0635), [16]),
]


# --- the guard --------------------------------------------------------------

def test_jack_is_not_an_lod_chain():
    ok, diag = scene_lod_is_geometric_chain(JACK)
    assert ok is False, diag
    assert diag["evaluated"] is True
    # level 1 is the mirrored half of level 0: zero intersection volume
    assert diag["levels"][1]["coverage"] == 0.0, diag["levels"][1]


def test_liv_head_is_an_lod_chain():
    ok, diag = scene_lod_is_geometric_chain(LIV_HEAD)
    assert ok is True, diag
    assert diag["worst_score"] >= SCENE_LOD_COLOCATION_MIN


def test_android_d_is_an_lod_chain_even_though_it_starts_at_bit_three():
    ok, diag = scene_lod_is_geometric_chain(ANDROID_D)
    assert ok is True, diag
    assert diag["base_level"] == 3


def test_the_measured_separation_is_wide():
    """0.5 sits in a gap with no borderline case on either side."""
    accepted = min(scene_lod_is_geometric_chain(o)[1]["worst_score"]
                   for o in (LIV_HEAD, ANDROID_D))
    refused = scene_lod_is_geometric_chain(JACK)[1]["worst_score"]
    assert accepted >= 0.8, accepted
    assert refused <= 0.25, refused
    assert refused < SCENE_LOD_COLOCATION_MIN < accepted


def test_guard_is_not_evaluated_without_aabbs():
    objs = [{"name": "a", "scene_lod_level": 0}, {"name": "b", "scene_lod_level": 1}]
    ok, diag = scene_lod_is_geometric_chain(objs)
    assert ok is True
    assert diag["evaluated"] is False


# --- ungated meshes ---------------------------------------------------------

def test_ungated_when_every_draw_mask_is_zero():
    assert object_is_scene_set_ungated(JACK[3]) is True      # obj003, mask 0
    assert object_is_scene_set_ungated(JACK[0]) is False     # obj000, mask 8


def test_not_ungated_when_no_draw_records_a_mask():
    o = {"draws": [{"idx_start": 0, "idx_count": 3}]}
    assert object_is_scene_set_ungated(o) is False


# --- selection --------------------------------------------------------------

def test_a_refused_partition_draws_everything():
    """⛔ Over-draw is visible and reversible; a missing limb is silent."""
    assert len(select_lod_objects(JACK, 0)) == len(JACK)


def test_level_zero_on_a_finest_set_of_three_is_not_empty():
    """`min(level, max(known))` alone returned NOTHING on `3cee9f282bf0807f`."""
    kept = select_lod_objects(ANDROID_D, 0)
    assert [o["name"] for o in kept] == [f"obj{i:03d}" for i in range(7)]
    assert sum(o["vertex_count"] for o in kept) == 52802


def test_level_one_on_that_same_chain_still_clamps_up():
    kept = select_lod_objects(ANDROID_D, 1)
    assert {o["scene_lod_level"] for o in kept} == {3}


def test_coarsest_level_is_still_reachable():
    kept = select_lod_objects(ANDROID_D, 4)
    assert {o["scene_lod_level"] for o in kept} == {4}


def test_an_ungated_mesh_survives_every_level():
    objs = LIV_HEAD + [_obj("always", 0, 10, (-0.1, -0.1, -0.1), (0.1, 0.1, 0.1), [0])]
    for level in (0, 1, 2):
        assert "always" in [o["name"] for o in select_lod_objects(objs, level)]


def test_negative_level_still_stacks_everything():
    assert len(select_lod_objects(LIV_HEAD, -1)) == len(LIV_HEAD)


# --- scene-set VARIANT draws ------------------------------------------------
# ★ Jack obj006..obj013: two `CGRenderParams` over the byte-identical index
# range with different shadersets and masks that differ in one bit, `mincount 2`.

def _draw(rp, start, count, mask, key):
    return {"renderparam_index": rp, "idx_start": start, "idx_count": count,
            "scene_mask": mask, "material_key": key, "shaderset_index": rp,
            "lod": {"level": 0}, "is_triangles": True}


def test_identical_range_under_different_sets_collapses_to_one():
    draws = [_draw(10, 0, 24546, 0x11, "ced3a456"), _draw(11, 0, 24546, 0x21, "950c024e")]
    kept = drop_scene_set_variant_draws(draws)
    assert len(kept) == 1
    assert kept[0]["renderparam_index"] == 10
    assert kept[0]["le_variant_dropped"][0]["material_key"] == "950c024e"


def test_identical_range_under_the_SAME_set_is_kept():
    draws = [_draw(0, 0, 12, 0x11, "a"), _draw(1, 0, 12, 0x11, "b")]
    assert len(drop_scene_set_variant_draws(draws)) == 2


def test_different_ranges_are_kept():
    """`liv_head` obj000 is two primsets of one mesh, not a variant."""
    draws = [_draw(0, 3318, 3318, 1, "x"), _draw(1, 0, 3318, 1, "x")]
    assert len(drop_scene_set_variant_draws(draws)) == 2


def test_a_draw_without_a_scene_mask_is_never_collapsed():
    draws = [{"idx_start": 0, "idx_count": 6}, {"idx_start": 0, "idx_count": 6}]
    assert len(drop_scene_set_variant_draws(draws)) == 2


def test_select_lod_draws_collapses_variants_at_level_zero():
    draws = [_draw(10, 0, 24546, 0x11, "a"), _draw(11, 0, 24546, 0x21, "b")]
    assert len(select_lod_draws(draws, 0)) == 1


def test_select_lod_draws_keeps_variants_at_minus_one_for_the_ab():
    draws = [_draw(10, 0, 24546, 0x11, "a"), _draw(11, 0, 24546, 0x21, "b")]
    assert len(select_lod_draws(draws, -1)) == 2


# --- audit-only suffixes ----------------------------------------------------

def test_audit_only_covers_every_layer_not_just_the_cracked_ones():
    """`layer2/3_flowmap_map` ship on Jack and were NOT flagged `audit_only`."""
    for layer in range(lemat.UBERMATERIAL_LAYER_COUNT):
        assert f"flowmap_map" in lemat.AUDIT_ONLY_SUFFIXES
        ch = lemat._channel(f"layer{layer}_flowmap_map", "deadbeef", {"deadbeef": 83})
        assert ch.get("audit_only") is True, layer


def test_audit_only_suffixes_are_exactly_the_no_socket_channels():
    want = {s for c in lemat.AUDIT_ONLY_CHANNELS
            for s in lemat.CHANNEL_ROLE_SUFFIXES[c]}
    assert lemat.AUDIT_ONLY_SUFFIXES == want


def test_a_routed_suffix_is_never_audit_only():
    ch = lemat._channel("layer3_composite_diffuse", "deadbeef", {"deadbeef": 72})
    assert "audit_only" not in ch


# --- shipped-package regressions (skip cleanly when the package is absent) --

def _manifest(pkg):
    p = CHARS / pkg / "manifest.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def test_shipped_jack_refuses_the_scene_lod_partition():
    man = _manifest("c6bc8607972268c9_64b4b5b2a0153f7e.lemesh")
    if man is None:
        return
    objs = man["objects"]
    if all(o.get("scene_lod_level") is None for o in objs):
        return                      # package predates the scene-LOD backfill
    ok, _diag = scene_lod_is_geometric_chain(objs)
    assert ok is False
    assert len(select_lod_objects(objs, 0)) == len(objs)


def test_shipped_jack_collapses_eight_variant_draws():
    man = _manifest("c6bc8607972268c9_64b4b5b2a0153f7e.lemesh")
    if man is None:
        return
    dropped = sum(len(o.get("draws", [])) - len(select_lod_draws(o.get("draws", []), 0))
                  for o in man["objects"])
    if not any(d.get("scene_mask") for o in man["objects"] for d in o.get("draws", [])):
        return                      # package predates the scene-mask backfill
    assert dropped == 8, dropped


def test_shipped_liv_body_still_selects_its_lod_zero():
    man = _manifest("2fd6839161785e9c_ff91757c910ea7b6.lemesh")
    if man is None:
        return
    objs = man["objects"]
    if all(o.get("scene_lod_level") is None for o in objs):
        return
    ok, _diag = scene_lod_is_geometric_chain(objs)
    assert ok is True
    kept = select_lod_objects(objs, 0)
    assert len(kept) == 5 and len(objs) == 6


def test_shipped_liv_head_still_selects_its_lod_zero():
    man = _manifest("9a65d254b0c73e61_3ae4822821fa8562.lemesh")
    if man is None:
        return
    objs = man["objects"]
    if all(o.get("scene_lod_level") is None for o in objs):
        return
    assert scene_lod_is_geometric_chain(objs)[0] is True
    assert len(select_lod_objects(objs, 0)) == 10
