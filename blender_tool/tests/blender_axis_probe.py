"""Axis / UV / winding calibration probe for the .lemesh importer.

Renders the SAME mesh under several candidate axis/UV/winding conventions from a
single FIXED camera so the results are directly comparable, plus two backface-
culling diagnostics that reveal whether triangle winding agrees with the stored
outward normals (i.e. whether faces are front-facing / outward in Blender).

Run (paths are ABSOLUTE WINDOWS paths -- blender.exe starts with cwd C:\\ so a
/mnt/c/... path would be mangled to C:\\mnt\\c\\...):

    "/mnt/c/Program Files/Blender Foundation/Blender 5.1/blender.exe" \
        --background --factory-startup \
        --python blender_tool/tests/blender_axis_probe.py -- \
        <ABS-WINDOWS-pkg.lemesh> <ABS-WINDOWS-outdir>

Outputs (into <outdir>):
    axis_A_current.png        default convention (rot +90X, flip_v, CCW winding)
    axis_B_mirror.png         handedness flip (mirror X) -- WRONG, model reverses
    axis_C_flipv_off.png      flip_v disabled            -- WRONG, texture flips V
    axis_D_backface_current.png   backface-cull ON, default winding  -> SOLID (ok)
    axis_E_backface_windrev.png   backface-cull ON, reverse_winding  -> HOLLOW (bad)
    axis_F_mirror_windrev.png     mirror X + reverse_winding -> outward but mirrored
"""

import sys
from pathlib import Path

import bpy                       # type: ignore
from mathutils import Vector     # type: ignore

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import lone_echo_import          # noqa: E402

RES_X, RES_Y = 1280, 960

# Filled once from the default-convention import so every candidate uses the
# exact same camera (a mirror then visibly reads as a mirror, not re-framed).
_FIXED_CAM = {}


def _reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _world_bbox():
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    mn = Vector((1e9, 1e9, 1e9))
    mx = Vector((-1e9, -1e9, -1e9))
    for o in meshes:
        for corner in o.bound_box:
            wc = o.matrix_world @ Vector(corner)
            mn = Vector(map(min, mn, wc))
            mx = Vector(map(max, mx, wc))
    return mn, mx


def _setup_camera_and_lights(fixed):
    """Place a camera + sun. Uses the stored FIXED pose when available."""
    scene = bpy.context.scene
    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = fixed["loc"]
    cam.rotation_euler = fixed["rot"]
    scene.camera = cam

    sun_data = bpy.data.lights.new("sun", "SUN")
    sun_data.energy = 3.0
    sun = bpy.data.objects.new("sun", sun_data)
    sun.rotation_euler = (0.6, 0.2, 0.4)
    scene.collection.objects.link(sun)

    # a fill sun from the other side so both faces get some light
    fill_data = bpy.data.lights.new("fill", "SUN")
    fill_data.energy = 1.5
    fill = bpy.data.objects.new("fill", fill_data)
    fill.rotation_euler = (-0.6, -0.2, 3.5)
    scene.collection.objects.link(fill)


def _compute_fixed_cam():
    """Derive a fixed 3/4-front camera from the default-convention world bbox."""
    mn, mx = _world_bbox()
    center = (mn + mx) * 0.5
    size = (mx - mn).length or 1.0
    direction = Vector((1.0, -1.25, 0.55)).normalized()
    loc = center + direction * size * 1.5
    rot = (center - loc).to_track_quat("-Z", "Y").to_euler()
    _FIXED_CAM["loc"] = loc
    _FIXED_CAM["rot"] = rot
    _FIXED_CAM["center"] = center
    _FIXED_CAM["size"] = size


def _render(out_png, *, textured=True, backface_cull=False):
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    try:
        scene.display.shading.light = "STUDIO"
        scene.display.shading.show_cavity = True
        scene.display.shading.color_type = "TEXTURE" if textured else "MATERIAL"
        scene.display.shading.show_backface_culling = backface_cull
    except Exception as exc:
        print(f"[probe] shading setup note: {exc}")
    scene.render.resolution_x = RES_X
    scene.render.resolution_y = RES_Y
    scene.render.film_transparent = False
    scene.render.filepath = out_png
    bpy.ops.render.render(write_still=True)
    print(f"[probe] RENDERED {out_png}")


def _candidate(out_png, opts, *, textured=True, backface_cull=False):
    _reset()
    base = {"import_materials": True, "import_shadow_only": False,
            "import_armature": False}
    base.update(opts)
    lone_echo_import.import_lemesh(PKG, bpy.context, base)
    if not _FIXED_CAM:
        _compute_fixed_cam()
    _setup_camera_and_lights(_FIXED_CAM)
    _render(out_png, textured=textured, backface_cull=backface_cull)


def main():
    global PKG
    rest = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not rest:
        print("[probe] usage: -- <pkg.lemesh> <outdir>")
        return
    PKG = rest[0]
    outdir = rest[1] if len(rest) > 1 else "."
    op = lambda n: str(Path(outdir) / n)

    # A: current / chosen default -- pure +90X rotation, flip_v, CCW winding.
    _candidate(op("axis_A_current.png"),
               {"flip_v": True, "y_up_to_z_up": True})

    # B: handedness flip (mirror across X). WRONG: reverses left/right.
    _candidate(op("axis_B_mirror.png"),
               {"flip_v": True, "y_up_to_z_up": True, "mirror_axis": "X"})

    # C: flip_v disabled. WRONG: DX textures come out V-flipped (upside-down).
    _candidate(op("axis_C_flipv_off.png"),
               {"flip_v": False, "y_up_to_z_up": True})

    # D: default winding, backface culling ON -> should render SOLID (outward).
    _candidate(op("axis_D_backface_current.png"),
               {"flip_v": True, "y_up_to_z_up": True},
               textured=False, backface_cull=True)

    # E: reversed winding, backface culling ON -> should render HOLLOW/see-through
    #    (proves the on-disk winding is already the correct/outward one).
    _candidate(op("axis_E_backface_windrev.png"),
               {"flip_v": True, "y_up_to_z_up": True, "reverse_winding": True},
               textured=False, backface_cull=True)

    # F: mirror + reverse winding (the task's "swap that flips handedness with a
    #    winding fix"). Faces are outward again, but the model is MIRRORED --
    #    strictly worse than A, which is outward WITHOUT mirroring.
    _candidate(op("axis_F_mirror_windrev.png"),
               {"flip_v": True, "y_up_to_z_up": True,
                "mirror_axis": "X", "reverse_winding": True})

    print("[probe] DONE")


main()
