"""Headless Blender test for the M4 scene-placement APPLY step.

Proves the .lemesh importer consumes a `scene.json` (from scripts/le_scene.py) and
places each imported meshlist at its WORLD position -- CONJUGATED by the addon's
axis correction A (+90deg X) -- instead of stacking at the origin.

    "<BLENDER 5.1>/blender.exe" --background --factory-startup \
        --python blender_tool/tests/blender_scene_apply.py

No archive / Oodle: a trivial synthetic .lemesh package + a synthetic scene.json
(a known Y-rotation + translation (0.01, 1.378, -19.876)) are built in a temp dir
in-Blender. Prints a `SCENE_APPLY_RESULT: PASS|FAIL` sentinel.

Correctness proof: the addon applies A at the OBJECT level (mesh_builder sets
`ob.matrix_basis = A @ ...`), so a placement empty must carry `A @ world_xf @ A^-1`
and the NET (empty @ child_A) must equal `A @ world_xf`. We assert BOTH -- the
conjugation, and that A@W (un-conjugated) is a genuinely different matrix.
"""

import json
import math
import sys
import tempfile
from pathlib import Path

import bpy                                   # type: ignore
from mathutils import Matrix, Vector          # type: ignore

HERE = Path(__file__).resolve().parent        # blender_tool/tests
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import lone_echo_import                        # noqa: E402
from lone_echo_import import mesh_builder, scene_reader   # noqa: E402

ARCHIVE = "testarc"
MESHLIST = "quadmesh0000beef"
T = (0.01, 1.378, -19.876)      # the named RAD-space translation
THETA_DEG = 30.0                # a non-trivial RAD Y-rotation (so conjugation bites)
TOL = 1e-5


# --- synthetic fixtures ------------------------------------------------------

def _build_pkg(dirpath: Path) -> Path:
    from le_mesh import meshlist as ml
    from le_mesh import materials as mat
    from le_mesh import package as pkg
    import synthetic

    fx = synthetic.build_single_quad()
    t = fx["tables"]
    objs = ml.build_objects(
        fx["primary"], fx["gpu"], fx["gpu_base"],
        meshes=ml.Table(*t["meshes"]), renderparams=ml.Table(*t["renderparams"]),
        vertexbuffers=ml.Table(*t["vertexbuffers"]), indexbuffers=ml.Table(*t["indexbuffers"]),
    )
    objs[0].draws[0].material_key = "m"
    material = mat.build_material_spec("m", shaderset_hash="m")
    out = Path(dirpath) / "quad.lemesh"
    pkg.write_package(out, source={"archive": ARCHIVE, "meshlist": MESHLIST},
                      objects=objs, materials=[material])
    return out


def _ry_rowmajor(theta_deg, tx, ty, tz):
    """Row-major 4x4: RAD Y-axis rotation + translation in the last column."""
    c, s = math.cos(math.radians(theta_deg)), math.sin(math.radians(theta_deg))
    return [c, 0.0, s, tx,
            0.0, 1.0, 0.0, ty,
            -s, 0.0, c, tz,
            0.0, 0.0, 0.0, 1.0]


def _ident_rowmajor(tx, ty, tz):
    return [1.0, 0.0, 0.0, tx, 0.0, 1.0, 0.0, ty,
            0.0, 0.0, 1.0, tz, 0.0, 0.0, 0.0, 1.0]


def _write_scene(dirpath: Path) -> Path:
    scene = {
        "format": "lescene", "version": 1, "archive": ARCHIVE,
        "coordinate_system": "rad_engine",
        "placements": {MESHLIST: [
            {"actornodeid": "A", "world_xf": _ry_rowmajor(THETA_DEG, *T),
             "parent_type": 0, "parent_type_name": "eNone", "scale": 1.0,
             "start_visible": True, "resolved": True},
            {"actornodeid": "B", "world_xf": _ident_rowmajor(5.0, 0.0, 2.0),
             "parent_type": 0, "parent_type_name": "eNone", "scale": 1.0,
             "start_visible": True, "resolved": True},
            {"actornodeid": "C", "world_xf": _ident_rowmajor(0.0, 0.0, 0.0),
             "parent_type": 1, "parent_type_name": "eAuto", "scale": 1.0,
             "start_visible": True, "resolved": False,
             "reason": "eAuto = runtime-selected parent, not decoded"},
        ]},
    }
    p = Path(dirpath) / "scene.json"          # beside the .lemesh dir -> auto-detectable
    p.write_text(json.dumps(scene, indent=1), encoding="utf-8")
    return p


# --- matrix helpers ----------------------------------------------------------

def _vclose(a, b, tol=TOL):
    return (Vector(a) - Vector(b)).length <= tol


def _mclose(a, b, tol=TOL):
    return all(abs(a[i][j] - b[i][j]) <= tol for i in range(4) for j in range(4))


# --- test body ---------------------------------------------------------------

CHECKS = []


def chk(label, cond):
    CHECKS.append((label, bool(cond)))


def _base_opts(**over):
    o = {"import_materials": True, "import_shadow_only": False, "flip_v": True,
         "y_up_to_z_up": True, "import_armature": True}
    o.update(over)
    return o


def main():
    tmp = Path(tempfile.mkdtemp(prefix="lemesh_scene_"))
    pkg = _build_pkg(tmp)
    scene_path = _write_scene(tmp)

    # the SAME A the addon uses, and the expected placement matrices
    A = mesh_builder._axis_matrix({"y_up_to_z_up": True})
    A_inv = A.inverted()
    W = Matrix(scene_reader.world_xf_rows(_ry_rowmajor(THETA_DEG, *T)))
    expect_empty_mw = A @ W @ A_inv
    expect_translation = A @ Vector(T)                     # (0.01, 19.876, 1.378)

    # sanity: the row-major -> Matrix mapping puts translation in .translation
    chk("world_xf row-major -> Matrix.translation == (t3,t7,t11)",
        _vclose(W.translation, Vector(T)))
    # sanity: A sends (x,y,z) -> (x,-z,y)
    chk("A(0.01,1.378,-19.876) == (0.01,19.876,1.378)",
        _vclose(expect_translation, Vector((0.01, 19.876, 1.378))))

    # === run 1: explicit scene path, place unresolved (default) ==============
    bpy.ops.wm.read_factory_settings(use_empty=True)
    summary = lone_echo_import.import_lemesh(
        str(pkg), bpy.context,
        _base_opts(apply_scene_placement=True, scene_json_path=str(scene_path),
                   skip_unresolved=False))
    bpy.context.view_layer.update()

    arch = bpy.data.collections.get(f"lescene_{ARCHIVE}")
    chk("archive collection lescene_testarc created", arch is not None)
    empties = list(arch.objects) if arch else []
    chk("3 placements -> 3 objects", len(empties) == 3)
    chk("all placement objects are EMPTY/collection-instances",
        all(o.type == "EMPTY" and o.instance_type == "COLLECTION" for o in empties))

    a_empty = next((o for o in empties if o.get("le_placement_actor") == "A"), None)
    chk("placement A empty exists", a_empty is not None)
    if a_empty is not None:
        tw = a_empty.matrix_world.translation
        chk("A empty translation == A . (0.01,1.378,-19.876)",
            _vclose(tw, expect_translation))
        chk("A empty translation == (0.01,19.876,1.378) [named check]",
            _vclose(tw, Vector((0.01, 19.876, 1.378))))
        chk("A empty.matrix_world == A . W . A^-1 (conjugated, full 4x4)",
            _mclose(a_empty.matrix_world, expect_empty_mw))
        # THE proof: instanced children carry A, so net = empty.mw @ A == A . W
        chk("net (empty.mw @ child_A) == A . W  [correct world display]",
            _mclose(a_empty.matrix_world @ A, A @ W))
        # and the conjugation is doing real work: A.W.A^-1 != A.W for a rotation
        chk("conjugation matters: A.W.A^-1 != A.W (rotation differs)",
            not _mclose(a_empty.matrix_world, A @ W))
        chk("un-conjugated world_xf != conjugated (regression guard)",
            not _mclose(a_empty.matrix_world, W))

    b_empty = next((o for o in empties if o.get("le_placement_actor") == "B"), None)
    chk("placement B empty exists", b_empty is not None)
    if b_empty is not None:
        chk("B empty translation == A . (5,0,2) == (5,-2,0)",
            _vclose(b_empty.matrix_world.translation, A @ Vector((5.0, 0.0, 2.0))))

    c_empty = next((o for o in empties if o.get("le_placement_actor") == "C"), None)
    chk("unresolved C present & tagged le_unresolved", c_empty is not None
        and bool(c_empty.get("le_unresolved")) and c_empty.get("le_resolved") == 0)

    # source meshes carry the base A and are detached from the view layer (only
    # the instances render -- no duplicate stack at the origin)
    src_meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    chk("source mesh exists as instance source", len(src_meshes) > 0)
    if src_meshes:
        chk("source mesh carries base axis A", _mclose(src_meshes[0].matrix_world, A))
    scene_root_objs = set(bpy.context.scene.collection.all_objects)
    chk("source mesh NOT left at scene origin (only instances render)",
        all(o not in scene_root_objs for o in src_meshes))

    pl = summary.get("placement") or {}
    chk("summary placement placed==3", pl.get("placed") == 3)
    chk("summary placement unresolved==1", pl.get("unresolved") == 1)
    chk("summary placement skipped==0", pl.get("skipped") == 0)

    # === run 2: auto-detect scene.json + skip_unresolved =====================
    bpy.ops.wm.read_factory_settings(use_empty=True)
    summary2 = lone_echo_import.import_lemesh(
        str(pkg), bpy.context,
        _base_opts(apply_scene_placement=True, scene_json_path="",
                   skip_unresolved=True))
    arch2 = bpy.data.collections.get(f"lescene_{ARCHIVE}")
    empties2 = list(arch2.objects) if arch2 else []
    chk("auto-detect scene.json found beside package", arch2 is not None)
    chk("skip_unresolved -> 2 objects (the resolved eNone pair)", len(empties2) == 2)
    pl2 = summary2.get("placement") or {}
    chk("run2 placed==2, skipped==1", pl2.get("placed") == 2 and pl2.get("skipped") == 1)

    # === run 3: placement OFF -> classic behavior, mesh at origin ============
    bpy.ops.wm.read_factory_settings(use_empty=True)
    lone_echo_import.import_lemesh(str(pkg), bpy.context, _base_opts())
    bpy.context.view_layer.update()
    chk("placement OFF -> no archive collection",
        bpy.data.collections.get(f"lescene_{ARCHIVE}") is None)
    off_meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    chk("placement OFF -> mesh sits at base A (origin, upright)",
        len(off_meshes) > 0 and _mclose(off_meshes[0].matrix_world, A))

    ok = all(v for _, v in CHECKS)
    for label, v in CHECKS:
        print(f"    [{'ok' if v else 'XX'}] {label}")
    print(f"SCENE_APPLY_RESULT: {'PASS' if ok else 'FAIL'}")


main()
