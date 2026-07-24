"""Import a .lemesh package and render a Workbench preview PNG (geometry proof).

    blender.exe --background --factory-startup --python blender_render_preview.py -- PKG.lemesh OUT.png
"""

import sys
from pathlib import Path

import bpy       # type: ignore
from mathutils import Vector   # type: ignore

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import lone_echo_import   # noqa: E402


def main():
    rest = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    pkg_path = rest[0]
    out_png = rest[1] if len(rest) > 1 else "/tmp/lemesh_preview.png"

    bpy.ops.wm.read_factory_settings(use_empty=True)
    lone_echo_import.import_lemesh(pkg_path, bpy.context,
                                   {"import_materials": True, "flip_v": True,
                                    "y_up_to_z_up": True})

    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        print("no meshes"); return

    # combined world-space bbox
    mn = Vector((1e9, 1e9, 1e9)); mx = Vector((-1e9, -1e9, -1e9))
    for o in meshes:
        for corner in o.bound_box:
            wc = o.matrix_world @ Vector(corner)
            mn = Vector(map(min, mn, wc)); mx = Vector(map(max, mx, wc))
    center = (mn + mx) * 0.5
    size = (mx - mn).length or 1.0

    # camera
    cam_data = bpy.data.cameras.new("cam"); cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    direction = Vector((1.0, -1.2, 0.8)).normalized()
    cam.location = center + direction * size * 1.6
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    # sun
    sun_data = bpy.data.lights.new("sun", "SUN"); sun_data.energy = 3.0
    sun = bpy.data.objects.new("sun", sun_data)
    sun.rotation_euler = (0.6, 0.2, 0.4)
    bpy.context.scene.collection.objects.link(sun)

    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    try:
        scene.display.shading.light = "STUDIO"
        scene.display.shading.show_cavity = True
        # TEXTURE shows the bound base-color image (M2); falls back gracefully
        # to a flat colour for procedural/textureless materials.
        scene.display.shading.color_type = "TEXTURE"
    except Exception:
        pass
    scene.render.resolution_x = 960
    scene.render.resolution_y = 720
    scene.render.film_transparent = False
    scene.render.filepath = out_png
    bpy.ops.render.render(write_still=True)
    print(f"RENDERED: {out_png}")


main()
