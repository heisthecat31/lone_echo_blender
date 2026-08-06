"""Render a level as the ENGINE assembles it: a child level's actor layer on top
of its PARENT level's static scatter.

    "<BLENDER 5.1>/blender.exe" --background --factory-startup \
        --python blender_tool/tests/blender_level_combine_render.py -- \
        scatter=<PARENT.lescatter> packages=<CHILD export dir> out=<OUT.png>

WHY THIS EXISTS
---------------
A Lone Echo level archive is NOT self-contained. `CGameLevelResourceWin7` carries
a parent-level `CSymbol64` at +0x00 (`le_mesh/level_link.py`), and on the reference
pair the ROOM SHELL — hull, floor, ceiling — is baked into the PARENT level's
static-instance master, while the child archive ships only the props/actors. Its
own self-named `CGStaticInstanceResourceWin7` is a 148-byte empty placeholder and
its own self-named `CGMeshListResourceWin7` is a 48-byte stub. So a child rendered
alone is props floating in a void, and that is a faithful render of one HALF of
the level. This harness renders both halves in one world.

Both layers land in the SAME world space with no extra offset: the scatter's
per-instance TRS and the child's `scene.json` `world_xf` are both RAD world.
⛔ Nothing here fabricates a placement — an unresolved `scene.json` row is skipped
by the importer exactly as it is skipped everywhere else, and the count is printed.

Args after `--` (all `key=value`):
    scatter=      parent `.lescatter` package dir (or its manifest.json)
    packages=     directory of `<archive>_<model>.lemesh` packages; a `scene.json`
                  beside them is auto-detected by the add-on
    scene=        explicit scene.json (e.g. an `--eauto=world` variant)
    out=          absolute WINDOWS path for the PNG
    lod=          scatter LOD level (default 0)
    camloc=/camtarget=/lens=/camdir=/camdist=   camera control (as blender_scatter_render)
    clipend=      camera far clip; DEFAULT is derived from the framed size, because
                  Blender's factory default is 100 m and a station-sized level is
                  then clipped to an empty frame.
    resx=/resy=/engine=/viewtransform=

Prints `LEVEL_COMBINE:` + a `LEVEL_COMBINE_RESULT: PASS|FAIL` sentinel.
"""

import sys
from pathlib import Path

import bpy                                    # type: ignore
from mathutils import Vector                  # type: ignore

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import lone_echo_import                        # noqa: E402

#: Blender's factory camera far clip. A level is hundreds of metres across, so
#: framing one and leaving this alone renders an empty frame -- the failure looks
#: exactly like "no geometry imported", which is why it is named here.
BLENDER_DEFAULT_CLIP_END = 100.0


def _parse_args():
    rest = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts = {}
    for a in rest:
        if "=" in a:
            k, v = a.split("=", 1)
            opts[k] = v
    return opts


def _rendered_instances():
    """(matrix_world, object) for everything the DEPSGRAPH will actually draw.

    ⚠ A `scene.json` placement is a collection-INSTANCING empty whose source
    collection is unlinked from the view layer, so `bpy.data.objects` neither
    finds the drawn copies nor excludes the parked source. Framing off that set
    puts the camera on geometry that is not in the picture. The depsgraph
    instance iterator is the only view that matches the render.
    """
    dg = bpy.context.evaluated_depsgraph_get()
    out = []
    for inst in dg.object_instances:
        ob = inst.object
        if ob is None or ob.type != "MESH":
            continue
        out.append((inst.matrix_world.copy(), ob))
    return out


def _bbox(entries):
    mn = Vector((1e18, 1e18, 1e18))
    mx = Vector((-1e18, -1e18, -1e18))
    for mw, ob in entries:
        for corner in ob.bound_box:
            wc = mw @ Vector(corner)
            mn = Vector(map(min, mn, wc))
            mx = Vector(map(max, mx, wc))
    return mn, mx


def main():
    opts = _parse_args()
    out_png = opts.get("out")
    if not out_png:
        print("LEVEL_COMBINE_RESULT: FAIL (no out= given)")
        return

    bpy.ops.wm.read_factory_settings(use_empty=True)

    counts = {"scatter_instances": 0, "packages": 0, "drawn_mesh_instances": 0,
              "placements_applied": 0, "placements_unresolved": 0}

    # --- layer 1: the PARENT level's static scatter (the shell) --------------
    if opts.get("scatter"):
        summary = lone_echo_import.import_lescatter(
            opts["scatter"], bpy.context,
            {"flip_v": True, "y_up_to_z_up": True, "import_proxy": False,
             "max_instances": int(opts.get("max", "0")),
             "lod_level": int(opts.get("lod", "0"))})
        counts["scatter_instances"] = summary.get("instances_placed", 0)
        print("LEVEL_COMBINE_SCATTER:", {k: v for k, v in summary.items()
                                         if k != "instance_lightmap"})

    # --- layer 2: the CHILD level's actor packages + its scene.json ----------
    if opts.get("packages"):
        pkg_dir = Path(opts["packages"])
        pkgs = sorted(p for p in pkg_dir.iterdir()
                      if p.is_dir() and p.name.endswith(".lemesh"))
        # ⛔ DEFAULT is skip_unresolved=True: an eAuto/eJoint row has no known
        # world transform, so drawing it anywhere is a fabricated placement. It
        # is COUNTED, never drawn, unless `unresolved=show` is passed.
        skip_unresolved = opts.get("unresolved", "skip") != "show"
        for p in pkgs:
            try:
                s = lone_echo_import.import_lemesh(
                    str(p), bpy.context,
                    {"flip_v": True, "y_up_to_z_up": True,
                     "apply_scene_placement": True, "import_proxy": False,
                     "skip_unresolved": skip_unresolved,
                     "scene_json_path": opts.get("scene", "")})
            except Exception as exc:                       # noqa: BLE001
                print(f"  package {p.name}: IMPORT ERROR {exc}")
                continue
            counts["packages"] += 1
            pl = s.get("placement") or {}
            counts["placements_applied"] += int(pl.get("placed", 0) or 0)
            counts["placements_unresolved"] += int(pl.get("skipped", 0) or 0) \
                + int(pl.get("unresolved", 0) or 0)
        print(f"LEVEL_COMBINE_PACKAGES: {counts['packages']} of {len(pkgs)} imported")

    bpy.context.view_layer.update()
    entries = _rendered_instances()
    counts["drawn_mesh_instances"] = len(entries)
    if not entries:
        print("LEVEL_COMBINE_RESULT: FAIL (nothing imported)")
        return

    mn, mx = _bbox(entries)
    center = (mn + mx) * 0.5
    size = (mx - mn).length or 1.0

    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    if opts.get("camloc"):
        cam.location = Vector([float(v) for v in opts["camloc"].split(",")])
        target = (Vector([float(v) for v in opts["camtarget"].split(",")])
                  if opts.get("camtarget") else center)
    else:
        direction = (Vector([float(v) for v in opts["camdir"].split(",")])
                     if opts.get("camdir") else Vector((1.0, -1.2, 0.7))).normalized()
        cam.location = center + direction * size * float(opts.get("camdist", "1.1"))
        target = center
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    if opts.get("lens"):
        cam_data.lens = float(opts["lens"])

    # ⚠ far clip: see BLENDER_DEFAULT_CLIP_END. Derived from the framed distance
    # so a bigger level cannot silently clip itself away.
    reach = (cam.location - target).length + size
    cam_data.clip_end = float(opts.get("clipend", max(BLENDER_DEFAULT_CLIP_END,
                                                      reach * 4.0)))
    cam_data.clip_start = float(opts.get("clipstart", "0.05"))
    bpy.context.scene.camera = cam
    print(f"LEVEL_COMBINE_CAMERA: loc=({cam.location.x:.3f}, {cam.location.y:.3f}, "
          f"{cam.location.z:.3f}) target=({target.x:.3f}, {target.y:.3f}, "
          f"{target.z:.3f}) size={size:.3f} clip_end={cam_data.clip_end:.1f}")
    print(f"LEVEL_COMBINE_BBOX: min=({mn.x:.2f},{mn.y:.2f},{mn.z:.2f}) "
          f"max=({mx.x:.2f},{mx.y:.2f},{mx.z:.2f})")

    sun_data = bpy.data.lights.new("sun", "SUN")
    sun_data.energy = 3.0
    sun = bpy.data.objects.new("sun", sun_data)
    sun.rotation_euler = (0.6, 0.2, 0.4)
    bpy.context.scene.collection.objects.link(sun)

    scene = bpy.context.scene
    engine_mode = opts.get("engine", "workbench").lower()
    if engine_mode.startswith("eevee"):
        ids = [i.identifier
               for i in scene.render.bl_rna.properties["engine"].enum_items]
        scene.render.engine = ("BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids
                               else "BLENDER_EEVEE")
    else:
        scene.render.engine = "BLENDER_WORKBENCH"
        sh = scene.display.shading
        sh.light = "STUDIO"
        sh.show_cavity = True
        sh.cavity_type = "BOTH"
        sh.show_shadows = True
        sh.color_type = {"material": "MATERIAL", "texture": "TEXTURE",
                         "object": "OBJECT"}.get(opts.get("color", "single"), "SINGLE")
        sh.single_color = (0.70, 0.72, 0.78)
        sh.background_type = "VIEWPORT"
        sh.background_color = (0.05, 0.06, 0.08)

    # ⚠ Blender 4.0+ defaults to AgX; force + read back Standard.
    want_vt = opts.get("viewtransform", "Standard")
    try:
        scene.view_settings.view_transform = want_vt
    except Exception as e:                                  # noqa: BLE001
        print("view_transform warn:", e)
    print(f"VIEW_TRANSFORM: wanted={want_vt} "
          f"got={getattr(scene.view_settings, 'view_transform', '?')}")

    scene.render.resolution_x = int(opts.get("resx", "1600"))
    scene.render.resolution_y = int(opts.get("resy", "1000"))
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = out_png
    bpy.ops.render.render(write_still=True)

    print("LEVEL_COMBINE:", counts)
    ok = counts["drawn_mesh_instances"] > 0
    print(f"RENDERED: {out_png}")
    print(f"LEVEL_COMBINE_RESULT: {'PASS' if ok else 'FAIL'}")


main()
