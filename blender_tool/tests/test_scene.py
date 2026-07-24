"""le_scene M4 placement tests (pure stdlib; no archives, no bpy).

Covers the world-placement math: M_init . M_offset, the eTransform parent chain,
offset composition, the parent-cycle guard, the EParentType resolved/unresolved
policy, and the manifest-row -> scene grouping.
"""

import sys
from pathlib import Path

# le_scene lives in ../../scripts; it in turn puts blender_tool on sys.path for le_mesh.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import le_scene  # noqa: E402
from le_scene import (E_AUTO, E_NONE, E_TRANSFORM, build_scene,  # noqa: E402
                      local_matrix, resolve_world)


def _last_col(m):
    return (m[3], m[7], m[11])


def _tx(actor, pos=(0.0, 0.0, 0.0), rot=(0.0, 0.0, 0.0, 1.0), scale=1.0,
        pt=E_NONE, parent=""):
    return {"actor": actor, "pos": pos, "rot": rot, "scale": scale,
            "parent_type": pt, "parent_actor": parent,
            "offset_rot": (0.0, 0.0, 0.0, 1.0), "offset_vec": (0.0, 0.0, 0.0)}


def _row(actor, model, pt=0, pos=(0.0, 0.0, 0.0)):
    return {"actor_node_hash": actor, "model_asset_hash": model,
            "pos_x": str(pos[0]), "pos_y": str(pos[1]), "pos_z": str(pos[2]),
            "rot_x": "0", "rot_y": "0", "rot_z": "0", "rot_w": "1", "scale": "1",
            "parent_type": str(pt), "parent_actor_hash": "",
            "start_visible": "1", "meshlist_present": "1", "mesh_count": "2"}


def test_eparent_values():
    # guard against regression to the wrong 0/1/2/3 enum (there IS an eAuto=1)
    assert (le_scene.E_NONE, le_scene.E_AUTO, le_scene.E_TRANSFORM,
            le_scene.E_JOINT, le_scene.E_REFPOINT) == (0, 1, 2, 3, 4)


def test_local_matrix_translation():
    # parent_type=0 world translation == pos (identity rot + offset)
    m = local_matrix((1.0, 2.0, 3.0), (0, 0, 0, 1), 1.0)
    assert _last_col(m) == (1.0, 2.0, 3.0)


def test_offset_translation_composed():
    # M_offset translation adds under identity M_init
    m = local_matrix((0.0, 0.0, 0.0), (0, 0, 0, 1), 1.0,
                     offset_rot=(0, 0, 0, 1), offset_vec=(5.0, -2.0, 0.0))
    assert abs(_last_col(m)[0] - 5.0) < 1e-6
    assert abs(_last_col(m)[1] + 2.0) < 1e-6


def test_eNone_resolved():
    transforms = {"A": _tx("A", pos=(7.0, 0.0, 0.0), pt=E_NONE)}
    world, resolved, reason = resolve_world("A", transforms, {}, set())
    assert resolved is True and reason is None
    assert abs(world[3] - 7.0) < 1e-6


def test_eTransform_chain_resolved():
    # child B parented to A: world(B) = world(A) . local(B)  -> x = 10 + 1 = 11
    transforms = {
        "A": _tx("A", pos=(10.0, 0.0, 0.0), pt=E_NONE),
        "B": _tx("B", pos=(1.0, 0.0, 0.0), pt=E_TRANSFORM, parent="A"),
    }
    world, resolved, reason = resolve_world("B", transforms, {}, set())
    assert resolved is True and reason is None
    assert abs(world[3] - 11.0) < 1e-6


def test_eTransform_missing_parent_unresolved():
    transforms = {"B": _tx("B", pt=E_TRANSFORM, parent="ghost")}
    world, resolved, reason = resolve_world("B", transforms, {}, set())
    assert resolved is False
    assert "not in archive" in (reason or "")


def test_parent_cycle_guarded():
    transforms = {"A": _tx("A", pt=E_TRANSFORM, parent="A")}  # self-parent
    world, resolved, reason = resolve_world("A", transforms, {}, set())
    assert resolved is False
    assert "cycle" in (reason or "").lower()


def test_eAuto_unresolved_but_emits_local():
    transforms = {"A": _tx("A", pos=(3.0, 0.0, 0.0), pt=E_AUTO)}
    world, resolved, reason = resolve_world("A", transforms, {}, set())
    assert resolved is False and "eAuto" in (reason or "")
    # still emits the node's own local placement, clearly flagged unresolved
    assert abs(world[3] - 3.0) < 1e-6


def test_build_scene_groups_and_flags():
    rows = [_row("A", "meshX", pt=0, pos=(1.0, 2.0, 3.0)),
            _row("B", "meshX", pt=1)]
    scene = build_scene(rows, "testarc")
    assert scene["format"] == "lescene" and scene["archive"] == "testarc"
    plist = scene["placements"]["meshX"]
    assert len(plist) == 2
    a = next(p for p in plist if p["actornodeid"] == "A")
    b = next(p for p in plist if p["actornodeid"] == "B")
    assert a["resolved"] is True and a["parent_type_name"] == "eNone"
    assert (a["world_xf"][3], a["world_xf"][7], a["world_xf"][11]) == (1.0, 2.0, 3.0)
    assert b["resolved"] is False and b["parent_type_name"] == "eAuto"
    assert scene["stats"]["resolved"] == 1 and scene["stats"]["unresolved"] == 1
