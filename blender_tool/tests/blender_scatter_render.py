"""Import a `.lescatter` package, place the scatter, and render a preview PNG.

    "<BLENDER 5.1>/blender.exe" --background --factory-startup \
        --python blender_tool/tests/blender_scatter_render.py -- PKG.lescatter OUT.png [MAX]

Args after `--`:
    PKG   absolute path to the `.lescatter` dir (or its manifest.json)
    OUT   absolute WINDOWS path for the output PNG (blender.exe from WSL starts at C:/)
    MAX   optional cap on placed instances for a fast first render (0/absent = all)

Builds unique meshes once (native space), places every instance at
`B @ (T @ R @ S)`, frames the combined bbox with a camera + sun, and renders with
the Workbench engine (MATERIAL color -> distinct color per mesh binding). Prints a
`SCATTER_RENDER: PASS|FAIL` sentinel plus a per-instance world-position dump so the
placement is verifiable from the log alone.

Pass `lod=N` after `--` to choose the LOD level to place (default `0` = highest
detail; `-1` = every level stacked, the pre-LOD behaviour; `-2` = coarsest). Every
LOD level of a prop is a separate mesh with its own instances, so `lod=-1` renders
all of them on top of each other.

Pass `engine=eevee` after `--` to render with Blender 5.1 EEVEE
(`BLENDER_EEVEE_NEXT`) instead, so the addon's normal/roughness/opacity/emission
material channels contribute. Workbench stays the default; if EEVEE is
unavailable the harness prints a warning and falls back to Workbench.
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
from render_engine_util import resolve_render_engine  # noqa: E402


def main():
    rest = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not rest:
        print("SCATTER_RENDER: FAIL (no package path given)")
        return
    pkg_path = rest[0]
    out_png = rest[1] if len(rest) > 1 else "/tmp/lescatter_preview.png"
    # remaining args: a bare integer = max instances; key=value = camdir/camdist/color
    opts = {}
    for a in rest[2:]:
        if "=" in a:
            k, v = a.split("=", 1); opts[k] = v
        elif a.isdigit():
            opts["max"] = a
    max_instances = int(opts.get("max", "0"))
    color_mode = opts.get("color", "single").lower()
    camdir = opts.get("camdir")
    camdist = float(opts.get("camdist", "1.1"))
    engine_mode = opts.get("engine", "workbench").lower()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    summary = lone_echo_import.import_lescatter(
        pkg_path, bpy.context,
        {"flip_v": True, "y_up_to_z_up": True, "import_proxy": False,
         "max_instances": max_instances,
         "lod_level": int(opts.get("lod", "0")),
         "materials_json": opts.get("materials")})
    print("SCATTER_SUMMARY:", summary)

    coll = bpy.data.collections.get(summary["collection"])
    objs = [o for o in (coll.objects if coll else []) if o.type == "MESH"]
    if not objs:
        print("SCATTER_RENDER: FAIL (no placed instances)")
        return

    # per-instance world positions (origin of each instance) — placement proof
    print("SCATTER_POSITIONS:")
    for o in sorted(objs, key=lambda o: o.get("le_instance_index", 0)):
        t = o.matrix_world.translation
        print(f"    i{o.get('le_instance_index')} mesh{o.get('le_mesh_index')} "
              f"-> ({t.x:.3f}, {t.y:.3f}, {t.z:.3f})")
    distinct = {(round(o.matrix_world.translation.x, 3),
                 round(o.matrix_world.translation.y, 3),
                 round(o.matrix_world.translation.z, 3)) for o in objs}

    # combined world-space bbox over all instance objects
    mn = Vector((1e18, 1e18, 1e18)); mx = Vector((-1e18, -1e18, -1e18))
    for o in objs:
        for corner in o.bound_box:
            wc = o.matrix_world @ Vector(corner)
            mn = Vector(map(min, mn, wc)); mx = Vector(map(max, mx, wc))
    center = (mn + mx) * 0.5
    size = (mx - mn).length or 1.0

    # camera (camdir="dx,dy,dz" + camdist=scale args override the 3/4 default)
    cam_data = bpy.data.cameras.new("cam"); cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    direction = (Vector([float(v) for v in camdir.split(",")]) if camdir
                 else Vector((1.0, -1.2, 0.7))).normalized()
    cam.location = center + direction * size * camdist
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    # sun
    sun_data = bpy.data.lights.new("sun", "SUN"); sun_data.energy = 3.0
    sun = bpy.data.objects.new("sun", sun_data)
    sun.rotation_euler = (0.6, 0.2, 0.4)
    bpy.context.scene.collection.objects.link(sun)

    scene = bpy.context.scene

    # resolve the render engine (Workbench is the default; `engine=eevee` opts
    # into Blender 5.1 EEVEE so the addon's normal/roughness/opacity/emission
    # material channels contribute). Fall back to Workbench with a warning if
    # the requested EEVEE identifier is not exposed by this Blender build.
    available_ids = [i.identifier
                     for i in scene.render.bl_rna.properties["engine"].enum_items]
    try:
        resolved_engine = resolve_render_engine(engine_mode, available_ids)
    except ValueError as e:
        print(f"engine warn: {e}; falling back to BLENDER_WORKBENCH")
        resolved_engine = "BLENDER_WORKBENCH"

    if resolved_engine in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        scene.render.engine = resolved_engine
        print(f"ENGINE: {resolved_engine}")
        # EEVEE needs a lit world (Workbench used a solid studio background).
        # Mid-grey ambient so PBR normals/roughness read without blowing out.
        world = scene.world
        if world is None:
            world = bpy.data.worlds.new("scatter_world")
            scene.world = world
        world.use_nodes = True
        bg = world.node_tree.nodes.get("Background")
        if bg is None:
            bg = world.node_tree.nodes.new("ShaderNodeBackground")
        bg.inputs["Color"].default_value = (0.05, 0.05, 0.05, 1.0)
        bg.inputs["Strength"].default_value = 1.0
        # brighter key light for PBR shading
        sun_data.energy = 4.5
        try:
            scene.eevee.use_raytracing = True   # older builds lack this attr
        except Exception as e:
            print("eevee raytracing warn:", e)
    else:
        scene.render.engine = "BLENDER_WORKBENCH"
        # shading: clean clay on a dark studio background (reads well for
        # untextured geometry; MATERIAL/per-binding color washed out on the
        # default white world). Pass `color=material` after `--` to color by
        # material binding instead.
        try:
            sh = scene.display.shading
            sh.light = "STUDIO"
            sh.show_cavity = True
            sh.cavity_type = "BOTH"
            sh.show_shadows = True
            sh.shadow_intensity = 0.5
            sh.color_type = {"material": "MATERIAL", "texture": "TEXTURE"}.get(
                color_mode, "SINGLE")
            sh.single_color = (0.70, 0.72, 0.78)
            sh.background_type = "VIEWPORT"
            sh.background_color = (0.08, 0.09, 0.11)
        except Exception as e:
            print("shading setup warn:", e)
    scene.render.resolution_x = 1280
    scene.render.resolution_y = 960
    scene.render.film_transparent = False
    scene.render.filepath = out_png
    bpy.ops.render.render(write_still=True)

    ok = len(objs) == summary["instances_placed"] and len(distinct) > 1
    print(f"RENDERED: {out_png}")
    print(f"SCATTER_RENDER: {'PASS' if ok else 'FAIL'} "
          f"(placed={len(objs)}, distinct_positions={len(distinct)})")


main()
