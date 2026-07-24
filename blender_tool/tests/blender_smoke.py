"""Headless Blender smoke test for the .lemesh addon.

    blender.exe --background --python blender_tool/tests/blender_smoke.py -- [PKG.lemesh]

If a package path is given after `--` it is imported; otherwise a synthetic
single-quad package is built in a temp dir (le_mesh is pure stdlib, so it runs
inside Blender's Python). Prints a `SMOKE_RESULT: PASS|FAIL` sentinel line.
"""

import sys
import tempfile
from pathlib import Path

import bpy   # type: ignore

# --- locate the tool tree relative to this file -----------------------------
HERE = Path(__file__).resolve().parent               # blender_tool/tests
BLENDER_TOOL = HERE.parent
ADDON_DIR = BLENDER_TOOL / "addon"
for p in (str(BLENDER_TOOL), str(ADDON_DIR), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import lone_echo_import   # noqa: E402


def _argv_pkg():
    if "--" in sys.argv:
        rest = sys.argv[sys.argv.index("--") + 1:]
        if rest:
            return rest[0]
    return None


def _build_synthetic_package() -> Path:
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
    objs[0].draws[0].material_key = "synthetic_mat"
    material = mat.build_material_spec("synthetic_mat", shaderset_hash="synthetic_mat")
    out = Path(tempfile.mkdtemp(prefix="lemesh_smoke_")) / "quad.lemesh"
    pkg.write_package(out, source={"archive": "synthetic", "meshlist": "quad"},
                      objects=objs, materials=[material])
    return out


def main() -> None:
    # start from an empty scene
    bpy.ops.wm.read_factory_settings(use_empty=True)

    pkg_path = _argv_pkg() or str(_build_synthetic_package())
    print(f"[smoke] importing {pkg_path}")

    opts = {"import_materials": True, "import_shadow_only": False,
            "flip_v": True, "y_up_to_z_up": True}
    summary = lone_echo_import.import_lemesh(pkg_path, bpy.context, opts)
    print(f"[smoke] summary: {summary}")

    checks = []
    checks.append(("objects>0", summary["objects"] > 0))
    checks.append(("vertices>0", summary["vertices"] > 0))
    checks.append(("triangles>0", summary["triangles"] > 0))
    checks.append(("materials>0", summary["materials"] > 0))

    # inspect the first imported mesh object
    objs = [o for o in bpy.data.objects if o.type == "MESH"]
    checks.append(("mesh object exists", len(objs) > 0))
    if objs:
        me = objs[0].data
        checks.append(("has polygons", len(me.polygons) > 0))
        checks.append(("has uv layer", len(me.uv_layers) > 0))
        checks.append(("has color attr", len(me.color_attributes) > 0))
        checks.append(("has material slot", len(me.materials) > 0))
        checks.append(("custom prop name_hash", "le_name_hash" in objs[0]))

    ok = all(v for _, v in checks)
    for label, v in checks:
        print(f"    [{'ok' if v else 'XX'}] {label}")
    print(f"SMOKE_RESULT: {'PASS' if ok else 'FAIL'}")


main()
