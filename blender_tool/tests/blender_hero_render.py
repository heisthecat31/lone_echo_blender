"""Hero render harness — presentation-quality stills from a real package.

    blender.exe --background --factory-startup \
        --python <ABS WINDOWS PATH>\\blender_hero_render.py -- \
        pkg=<...>.lemesh out=<...>.png preset=portrait

NOT named `test_*` on purpose: `tests/run_tests.py` imports every `test_*.py`
under plain `python3`, and this file needs `bpy`.  The math it depends on is
pure and *is* tested there — `le_mesh/framing.py` / `tests/test_framing.py`.

★ Why this exists alongside the five harnesses already in this directory
-----------------------------------------------------------------------
Those are *diagnostic*: black world, strength 0.0, Workbench or EEVEE, a
bounding-box camera at `size * K` for a hand-tuned `K`.  They answer "did the
importer wire the right page / the right material / the right UV set".  They
cannot answer "does this read like the game", because nothing in them is a
lighting or camera decision — and docs/LIGHTING.md §9 is explicit
that internal-consistency pictures are all this tree has ever produced.

This harness makes the *presentation* variables explicit and reproducible:

* **camera** — solved, not guessed.  `framing.fit_view` places the camera from
  the lens, the sensor, the render aspect and the actual vertices, so changing
  `lens=50` to `lens=135` reframes correctly instead of cropping the subject.
* **light** — two named rigs (`studio`, `space`) whose power is computed from
  the subject's size, so exposure does not drift when the subject does.
* **colour** — `view_transform` is set, then **read back and printed**, because
  every scatter PNG in this tree before 2026-08 silently inherited AgX.

⚠ What a picture from this harness is and is not
------------------------------------------------
It is the shipped geometry, the shipped textures and the importer's material
graph under a *stated* studio rig.  It is **not** a frame of the game: the game
lights this character from irradiance volumes, which this tree has no reader
for.  Compare it to concept art for
albedo/roughness/normal legibility.  Do not read a match as "we reproduced the
renderer", and do not read a mismatch as a defect in the decode.

Options are `key=value` after `--` (same convention as `blender_scatter_render.py`):

    pkg=A.lemesh[,B.lemesh]   one or more packages (dir or manifest.json)
    pkg_dir=<dir>             import EVERY *.lemesh in this directory (a level's
                              worth of packages; combine with scene= to assemble
                              them into a room instead of a pile at the origin)
    scene=<scene.json>        M4 placement file (blender_tool/le_mesh/scene_build.py).
                              Implies place=1: each package is instanced once per
                              placement at its level WORLD transform
    place=1|0                 force scene placement on/off
    skip_unresolved=1|0       drop eAuto/eJoint placements (default 0: they are
                              placed at their own local matrix and tagged)
    cam_loc=x,y,z             EXPLICIT camera position (Blender space). Required
                              for interior shots -- the orbit fit always parks the
                              camera outside the point cloud
    cam_target=x,y,z          what the explicit camera aims at
    pkg_xform=m00,..,m15      DECODED 4x4 (row-major, Blender space) per package
                              -- the component attach transform. Prefer this over
                              pkg_offset; see le_mesh/attach.py
    only=obj002[,obj004]      keep ONLY objects whose name contains one of these
    drop=obj002[,obj004]      delete objects whose name contains one of these.
                              `drop=` + an explicit `cam_loc=`/`cam_target=` is
                              the same-camera A/B that attributes a pixel to an
                              object: diff against the unfiltered plate
    scatter=A.lescatter       a level package (repeatable via commas)
    out=<png>                 output path (default /tmp/le_hero.png)
    preset=portrait|bust|fullbody|vista|turntable
    engine=cycles|eevee       default cycles
    device=optix|cuda|cpu     Cycles device, default optix then cuda then cpu
    samples=N                 default per preset
    resx= resy=               default per preset
    lens= azimuth= elevation= margin= shift_x= shift_y=
    rig=studio|space|none     default per preset
    key= fill= rim=           per-light multipliers (default 1.0)
    backdrop=1|0              studio backdrop plane
    view=Standard|AgX|Filmic  default Standard
    look=                     colour-management look, default None
    exposure=                 scene exposure stops, default 0.0
    fstop=                    enable DOF at this f-number
    lightmap=none|baked|ambient   default none (characters carry no lightmap)
    lightmap_texture=<dds>    the level's colour atlas
    instance_lightmap=1|0     per-instance lightmap UVs — REQUIRED for a level
                              bake; the `.lescatter` `uv1` stream is all-zero on
                              1046/1050 meshes, so without it 99.6 % of the
                              level samples atlas texel (0,0)
    lights=<lights.json>      import the level's own lights
    light_set=diffuse|all|enabled   default diffuse (all double-lights, 7.06x)
    light_exposure=           multiplier on imported light energy
    armature=0|1              default 0
    lod=N                     default 0
    max_instances=N           scatter only
    turns=N                   turntable frame count, default 8
    transparent=1|0           film transparency
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import bpy                                        # type: ignore
from mathutils import Matrix, Vector              # type: ignore

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import lone_echo_import                           # noqa: E402
from le_mesh import framing                       # noqa: E402

# --------------------------------------------------------------------------
# presets — every number here is a presentation choice, not a decoded fact
# --------------------------------------------------------------------------

PRESETS = {
    # tight 3/4 upper body, long lens, the ArtStation portrait framing
    "bust": dict(lens=135.0, azimuth=26.0, elevation=3.0, margin=1.01,
                 resx=1400, resy=1750, rig="studio", samples=256,
                 crop="upper", crop_from=0.42, shift_y=0.0),
    # 3/4 body, 85 mm — the classic character-sheet portrait
    "portrait": dict(lens=85.0, azimuth=32.0, elevation=4.0, margin=1.10,
                     resx=1600, resy=2000, rig="studio", samples=256,
                     crop="none"),
    # full figure, slightly wider
    "fullbody": dict(lens=70.0, azimuth=25.0, elevation=2.0, margin=1.18,
                     resx=1400, resy=2000, rig="studio", samples=192,
                     crop="none"),
    # wide landscape for a level / exterior
    "vista": dict(lens=35.0, azimuth=18.0, elevation=8.0, margin=1.04,
                  resx=2048, resy=1152, rig="space", samples=128,
                  crop="none"),
    "turntable": dict(lens=85.0, azimuth=0.0, elevation=6.0, margin=1.12,
                      resx=900, resy=1200, rig="studio", samples=96,
                      crop="none"),
}

_log: list[str] = []


def say(tag: str, msg: str) -> None:
    line = f"[{tag}] {msg}"
    print(line, flush=True)
    _log.append(line)


def parse_argv() -> dict:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts = {}
    for a in argv:
        if "=" in a:
            k, v = a.split("=", 1)
            opts[k.strip()] = v.strip()
    return opts


def opt_f(opts, key, default):
    return float(opts[key]) if key in opts else default


def opt_i(opts, key, default):
    return int(opts[key]) if key in opts else default


def opt_b(opts, key, default):
    if key not in opts:
        return default
    return opts[key].lower() in ("1", "true", "yes", "on")


def opt_vec(opts, key):
    """`x,y,z` -> a 3-tuple of floats, or None when the option is absent."""
    if key not in opts:
        return None
    parts = [p for p in opts[key].replace(" ", "").split(",") if p]
    if len(parts) != 3:
        raise SystemExit(f"{key} must be x,y,z (got {opts[key]!r})")
    return tuple(float(p) for p in parts)


def package_specs(opts) -> list:
    """Every package to import: explicit `pkg=` first, then all of `pkg_dir=`.

    Sorted and de-duplicated so a level assembles in a stable order (the render
    is then reproducible, and the log lists the packages in a diffable order).
    """
    specs = [s for s in opts.get("pkg", "").split(",") if s]
    d = opts.get("pkg_dir", "")
    if d:
        root = Path(d)
        if not root.is_dir():
            raise SystemExit(f"pkg_dir is not a directory: {root}")
        found = sorted(str(p) for p in root.glob("*.lemesh"))
        if not found:
            raise SystemExit(f"no *.lemesh packages in {root}")
        specs += found
    out, seen = [], set()
    for s in specs:
        k = str(Path(s).resolve())
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def imported_objects(result):
    """The importers report `collection` as a NAME (a str), not a datablock."""
    coll = result.get("collection")
    if isinstance(coll, str):
        coll = bpy.data.collections.get(coll)
    if coll is None:
        return [o for o in bpy.context.scene.objects if o.type == "MESH"]
    return list(coll.all_objects)


def apply_object_filter(objects, opts):
    """`only=` / `drop=` -- a SUBSTRING filter over object names, applied by DELETION.

    The point is a same-camera A/B: `drop=obj002` renders every surface except
    that one, so a straight image diff against the unfiltered plate is exactly
    the pixels that object owns. Deleting (rather than hiding) also keeps the
    object out of `subject_points`, so pass `cam_loc=`/`cam_target=` when the
    framing must not move. Comma-separated; matching is case-insensitive
    substring, so `only=obj002` catches `obj002_819de28102ca85fb`.
    """
    only = [s.strip().lower() for s in opts.get("only", "").split(",") if s.strip()]
    drop = [s.strip().lower() for s in opts.get("drop", "").split(",") if s.strip()]
    if not only and not drop:
        return objects
    keep, removed = [], []
    for ob in objects:
        n = ob.name.lower()
        ok = (not only or any(s in n for s in only)) and not any(s in n for s in drop)
        (keep if ok else removed).append(ob)
    for ob in removed:
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            pass
    say("filter", f"only={only or '-'} drop={drop or '-'} -> kept {len(keep)}, "
                  f"removed {len(removed)}")
    return keep


# --------------------------------------------------------------------------
# scene
# --------------------------------------------------------------------------

def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    return bpy.context.scene


def subject_points(objects, cap: int = 250_000):
    """World-space vertices of `objects`, subsampled to at most `cap` points.

    ⚠ `bpy.context.view_layer.update()` MUST have run first: the importer puts
    the Y-up→Z-up conversion on `matrix_basis`, so `matrix_world` reads
    identity until the depsgraph evaluates (`mesh_builder.build_object`).
    """
    meshes = [o for o in objects if o.type == "MESH" and o.data and len(o.data.vertices)]
    total = sum(len(o.data.vertices) for o in meshes)
    step = max(1, total // cap)
    pts = []
    for ob in meshes:
        mw = ob.matrix_world
        vs = ob.data.vertices
        for i in range(0, len(vs), step):
            co = mw @ vs[i].co
            pts.append((co.x, co.y, co.z))
        # always include the extremes of this object so subsampling cannot
        # silently crop a thin limb out of frame
        for corner in ob.bound_box:
            co = mw @ Vector(corner)
            pts.append((co.x, co.y, co.z))
    return pts


def instanced_points(context, cap: int = 250_000):
    """World-space vertices of everything the VIEW LAYER actually renders.

    ⚠ Required whenever scene placement is on. `import_lemesh` unlinks the source
    meshlist collection and renders it through collection-instance empties, so
    `collection.all_objects` hands back the SOURCE meshes — every one of them
    still sitting at the origin with the placement not applied. Framing off those
    would fit a pile, not the room. The depsgraph is the only place the instanced
    world matrices exist.

    ⚠ `DepsgraphObjectInstance` is a temporary the iterator RE-USES; holding
    `inst.object` past the loop is an access violation, not a Python error. Both
    passes below therefore do all their work inside their own iteration.
    """
    dg = context.evaluated_depsgraph_get()

    def usable(ob):
        return (ob is not None and ob.type == "MESH" and ob.data
                and len(ob.data.vertices) > 0)

    total = n_inst = 0
    for inst in dg.object_instances:
        ob = inst.object
        if not usable(ob):
            continue
        total += len(ob.data.vertices)
        n_inst += 1

    step = max(1, total // cap)
    pts = []
    for inst in dg.object_instances:
        ob = inst.object
        if not usable(ob):
            continue
        mw = inst.matrix_world
        vs = ob.data.vertices
        for i in range(0, len(vs), step):
            co = mw @ vs[i].co
            pts.append((co.x, co.y, co.z))
        for corner in ob.bound_box:            # never crop a thin prop away
            co = mw @ Vector(corner)
            pts.append((co.x, co.y, co.z))
    return pts, n_inst


def placement_dump(context, top: int = 24):
    """Per-placement world AABBs, biggest first — `dump=1`.

    Answers "is this package where the room needs it" without a render: a prop
    whose instance AABB is a point, or sits outside the room, is a placement bug;
    one that is simply dark is a lighting choice. Grouped by the instancing empty
    (`lemesh_<meshlist>__<actornodeid>`), so a line names both the package and
    the actor node it came from.
    """
    dg = context.evaluated_depsgraph_get()
    boxes = {}
    for inst in dg.object_instances:
        ob = inst.object
        if ob is None or ob.type != "MESH" or not ob.data or not len(ob.data.vertices):
            continue
        parent = inst.parent
        key = parent.name if parent is not None else ob.name
        mw = inst.matrix_world
        lo, hi = boxes.get(key, (None, None))
        for corner in ob.bound_box:
            co = mw @ Vector(corner)
            v = (co.x, co.y, co.z)
            lo = v if lo is None else tuple(min(lo[i], v[i]) for i in range(3))
            hi = v if hi is None else tuple(max(hi[i], v[i]) for i in range(3))
        boxes[key] = (lo, hi)
    rows = []
    for key, (lo, hi) in boxes.items():
        ext = tuple(hi[i] - lo[i] for i in range(3))
        rows.append((ext[0] * ext[1] * ext[2], key, lo, hi, ext))
    rows.sort(reverse=True)
    out = [f"{len(rows)} placed instances, biggest AABB first"]
    for vol, key, lo, hi, ext in rows[:top]:
        out.append(f"  {key:<44} ext={[round(v, 2) for v in ext]} "
                   f"at {[round(v, 2) for v in lo]}..{[round(v, 2) for v in hi]}")
    return out


def place_camera(scene, fit, *, lens, shift_x=0.0, shift_y=0.0, fstop=None,
                 sensor=36.0, sensor_fit="AUTO"):
    cam_data = bpy.data.cameras.new("hero_cam")
    cam_data.lens = lens
    cam_data.sensor_width = sensor
    cam_data.sensor_fit = sensor_fit
    cam_data.shift_x = shift_x
    cam_data.shift_y = shift_y
    cam_data.clip_start = fit["clip_start"]
    cam_data.clip_end = fit["clip_end"]
    if fstop:
        cam_data.dof.use_dof = True
        cam_data.dof.focus_distance = fit["distance"]
        cam_data.dof.aperture_fstop = fstop

    cam = bpy.data.objects.new("hero_cam", cam_data)
    scene.collection.objects.link(cam)

    right, up, back = fit["basis"]
    rot = Matrix(((right[0], up[0], back[0]),
                  (right[1], up[1], back[1]),
                  (right[2], up[2], back[2]))).to_4x4()
    cam.matrix_world = Matrix.Translation(Vector(fit["location"])) @ rot
    scene.camera = cam
    return cam


def _area(name, loc, target, size, power, color=(1.0, 1.0, 1.0)):
    d = bpy.data.lights.new(name, type="AREA")
    d.shape = "SQUARE"
    d.size = size
    d.energy = power
    d.color = color
    ob = bpy.data.objects.new(name, d)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = Vector(loc)
    direction = Vector(target) - Vector(loc)
    ob.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()
    return ob


def rig_studio(scene, fit, *, key=1.0, fill=1.0, rim=1.0, backdrop=True):
    """Three-point studio rig, sized from the subject.

    Power is `irradiance * 4*pi*d^2` so the *exposure* is invariant to subject
    scale — swap a 1.8 m character for a 40 m station and the key still lands
    at the same stops.  The reference this is aimed at (a dark seamless with a
    strong key from camera-left, a cool fill and a hard rim) is art direction,
    not a decoded engine value; every constant below is a look choice.
    """
    right, up, back = fit["basis"]
    target = Vector(fit["target"])
    size = max(fit["size"], 1e-3)
    d = max(fit["distance"], size)

    def at(az_deg, el_deg, dist_mul):
        """Position relative to the CAMERA azimuth, so the rig follows the shot."""
        cam_dir = Vector(back)
        # build a camera-relative frame with world Z as up
        flat = Vector((cam_dir.x, cam_dir.y, 0.0))
        if flat.length < 1e-6:
            flat = Vector((0.0, -1.0, 0.0))
        flat.normalize()
        az = math.radians(az_deg)
        el = math.radians(el_deg)
        rot = Matrix.Rotation(az, 3, "Z")
        v = rot @ flat
        v = Vector((v.x * math.cos(el), v.y * math.cos(el), math.sin(el)))
        return target + v * (d * dist_mul)

    lights = []
    key_loc = at(-38.0, 22.0, 0.85)
    fill_loc = at(52.0, 6.0, 1.05)
    rim_loc = at(168.0, 34.0, 0.95)

    def watts(irr, loc):
        r2 = (Vector(loc) - target).length_squared
        return irr * 4.0 * math.pi * r2

    # Irradiance targets in W/m^2 at the subject. Calibrated once, on the
    # shipped character `c6bc8607972268c9_64b4b5b2a0153f7e`, whose untextured
    # plates carry `bakecolor` albedos around 0.13-0.17 -- at the first-pass
    # values (3.2 / 0.55 / 4.2) those plates blew out to near-white and read as
    # "the textures are missing", which they were not.
    lights.append(_area("key", key_loc, target, size * 1.6, watts(1.15 * key, key_loc),
                        color=(1.0, 0.97, 0.93)))
    lights.append(_area("fill", fill_loc, target, size * 2.4, watts(0.20 * fill, fill_loc),
                        color=(0.80, 0.87, 1.0)))
    lights.append(_area("rim", rim_loc, target, size * 0.9, watts(1.9 * rim, rim_loc),
                        color=(0.86, 0.93, 1.0)))

    world = bpy.data.worlds.new("studio")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.018, 0.019, 0.022, 1.0)
    bg.inputs[1].default_value = 1.0
    scene.world = world

    if backdrop:
        # a seamless behind the subject; the key/fill falloff across it IS the
        # gradient in the reference, so nothing here paints one by hand
        bpy.ops.mesh.primitive_plane_add(size=size * 10.0)
        plane = bpy.context.active_object
        plane.name = "backdrop"
        plane.location = target + Vector(back) * (-size * 3.0)
        plane.rotation_euler = (Vector(back) * -1).to_track_quat("-Z", "Y").to_euler()
        mat = bpy.data.materials.new("backdrop")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        # dark seamless: the visible gradient is the key/fill falloff across it,
        # not a painted ramp
        bsdf.inputs["Base Color"].default_value = (0.055, 0.055, 0.062, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.9
        if "Metallic" in bsdf.inputs:
            bsdf.inputs["Metallic"].default_value = 0.0
        plane.data.materials.append(mat)
        lights.append(plane)

    return lights


def rig_space(scene, fit, *, key=1.0, fill=1.0, rim=1.0, backdrop=False):
    """Hard single key + a dim cool bounce — vacuum lighting.

    A SUN is used deliberately: its strength is irradiance (W/m^2) and is
    therefore scale-free, which matters when the subject is a station hundreds
    of units across.
    """
    right, up, back = fit["basis"]
    target = Vector(fit["target"])

    sun = bpy.data.lights.new("sun", type="SUN")
    sun.energy = 3.0 * key
    sun.angle = math.radians(0.53)          # the sun's real angular diameter
    sun.color = (1.0, 0.96, 0.90)
    ob = bpy.data.objects.new("sun", sun)
    scene.collection.objects.link(ob)
    ob.rotation_euler = Vector((-0.55, -0.35, 0.25)).to_track_quat("-Z", "Y").to_euler()

    bounce = bpy.data.lights.new("bounce", type="SUN")
    bounce.energy = 0.20 * fill
    bounce.color = (0.62, 0.72, 1.0)
    ob2 = bpy.data.objects.new("bounce", bounce)
    scene.collection.objects.link(ob2)
    ob2.rotation_euler = Vector((0.8, 0.6, -0.3)).to_track_quat("-Z", "Y").to_euler()

    world = bpy.data.worlds.new("space")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.004, 0.005, 0.008, 1.0)
    bg.inputs[1].default_value = 1.0 * rim
    scene.world = world
    return [ob, ob2]


RIGS = {"studio": rig_studio, "space": rig_space, "none": lambda *a, **k: []}


def configure_render(scene, *, engine, device, samples, resx, resy,
                     view="Standard", look="None", exposure=0.0,
                     transparent=False):
    scene.render.resolution_x = resx
    scene.render.resolution_y = resy
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
    scene.render.film_transparent = transparent

    if engine == "cycles":
        scene.render.engine = "CYCLES"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = True
        scene.cycles.max_bounces = 12
        scene.cycles.transmission_bounces = 12
        scene.cycles.transparent_max_bounces = 16
        prefs = bpy.context.preferences.addons.get("cycles")
        chosen = "CPU"
        if prefs:
            cp = prefs.preferences
            order = [device.upper()] if device else []
            order += ["OPTIX", "CUDA", "HIP", "ONEAPI"]
            for dt in order:
                if dt in ("CPU", ""):
                    break
                try:
                    cp.compute_device_type = dt
                    devs = [d for d in cp.get_devices_for_type(dt)]
                except Exception:
                    continue
                if devs:
                    for d in cp.devices:
                        d.use = (d.type == dt)
                    scene.cycles.device = "GPU"
                    chosen = f"{dt}:{','.join(d.name for d in devs if d.type == dt)}"
                    break
        say("engine", f"CYCLES samples={samples} device={chosen}")
    else:
        ids = {e.identifier for e in
               bpy.types.Scene.bl_rna.properties["render"].fixed_type
               .properties["engine"].enum_items}
        scene.render.engine = ("BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids
                               else "BLENDER_EEVEE")
        try:
            scene.eevee.taa_render_samples = samples
            scene.eevee.use_raytracing = True      # or Transmission renders flat
        except Exception:
            pass
        say("engine", f"{scene.render.engine} samples={samples}")

    scene.view_settings.view_transform = view
    scene.view_settings.look = look
    scene.view_settings.exposure = exposure
    scene.view_settings.gamma = 1.0
    # read it BACK -- every scatter PNG before 2026-08 silently inherited AgX
    got = scene.view_settings.view_transform
    say("colour", f"view_transform={got!r} look={scene.view_settings.look!r} "
                  f"exposure={scene.view_settings.exposure}")
    if got != view:
        raise SystemExit(f"view_transform did not stick: wanted {view!r}, got {got!r}")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    opts = parse_argv()
    preset_name = opts.get("preset", "portrait")
    if preset_name not in PRESETS:
        raise SystemExit(f"unknown preset {preset_name!r}; have {sorted(PRESETS)}")
    P = dict(PRESETS[preset_name])
    say("preset", f"{preset_name} {P}")
    # ⚠ A WSL path passed to `--python` silently loads the STALE add-on installed
    # under %APPDATA%; print the module that actually answered so every log says
    # which code made the picture.
    say("module", f"lone_echo_import <- {lone_echo_import.__file__}")

    out = Path(opts.get("out", "/tmp/le_hero.png"))
    out.parent.mkdir(parents=True, exist_ok=True)

    scene = reset_scene()
    imported = []

    lm_mode = opts.get("lightmap", "none")
    lod = opt_i(opts, "lod", 0)

    scene_json = opts.get("scene", "")
    place = opt_b(opts, "place", bool(scene_json))
    if place and not scene_json:
        raise SystemExit("place=1 needs scene=<scene.json>")
    placed_total = unresolved_total = no_placement = 0

    specs = package_specs(opts)
    for spec in specs:
        mopts = {
            "import_materials": True,
            "import_armature": opt_b(opts, "armature", False),
            "lod_level": lod,
            "lightmap_mode": lm_mode,
            "flip_v": True,
            "y_up_to_z_up": True,
            "apply_scene_placement": place,
            "scene_json_path": scene_json,
            "skip_unresolved": opt_b(opts, "skip_unresolved", False),
        }
        # Shading escape hatches, so an A/B is a CLI flag rather than an edit.
        # Absent => the builder's own default (both ON).
        for _k in ("brdf_lobe_blend", "brdf_lobe_zero_roughness_gate",
                   "ao_to_base_color", "additive_blend",
                   "additive_unrouted_color", "wire_specular",
                   "skin_subsurface", "skirt_alpha", "shipped_tangent"):
            if _k in opts:
                mopts[_k] = opt_b(opts, _k, True)
        # ⚠ LOG WHICH HATCHES WERE SET. An A/B plate whose log does not name the
        # flag that produced it is not reproducible: the pair
        # `tangent_jack_portrait_shipped` / `_mikktspace` differ by one CLI flag
        # and nothing in the picture says which is which.
        _hatch = {k: v for k, v in mopts.items() if isinstance(v, bool)
                  and k not in ("import_materials", "import_armature", "flip_v",
                                "y_up_to_z_up", "apply_scene_placement",
                                "skip_unresolved")}
        say("shading", ", ".join(f"{k}={int(v)}" for k, v in sorted(_hatch.items()))
            or "all defaults")
        if "layer_blend_mask_offset" in opts:
            mopts["layer_blend_mask_offset"] = opt_f(opts, "layer_blend_mask_offset", 0.0)
        if "skin_subsurface_weight" in opts:
            mopts["skin_subsurface_weight"] = opt_f(opts, "skin_subsurface_weight", 0.35)
        res = lone_echo_import.import_lemesh(spec, bpy.context, mopts)
        pl = res.get("placement") or {}
        note = ""
        if place:
            placed_total += pl.get("placed", 0)
            unresolved_total += pl.get("unresolved", 0)
            if not pl.get("placements"):
                no_placement += 1
                note = f"  [NO PLACEMENT: {pl.get('note', 'scene.json not applied')}]"
            else:
                note = (f"  placed {pl['placed']} "
                        f"({pl['unresolved']} unresolved, {pl['skipped']} skipped)")
        say("import", f"{Path(spec).name}: {res['objects']} objs "
                      f"{res['vertices']} verts {res['triangles']} tris "
                      f"{res['materials']} mats{note}")
        got = imported_objects(res)
        # `pkg_offset=x,y,z[;x,y,z...]` -- a WORLD translation per package, in
        # Blender space, positionally matched to `pkg=`. ⚠ It is an ASSEMBLY
        # CONVENIENCE, not a decode: a two-part character (Liv = body
        # `ff91757c910ea7b6` + head `3ae4822821fa8562`, joined on actor node
        # `1d6d5746a7f89a9f` via the `head` component) ships its head in HEAD-LOCAL
        # space, and the component transform is not read by this harness. Any
        # picture that uses it must say the placement was eyeballed.
        _i = specs.index(spec) if spec in specs else -1
        # ★ `pkg_xform=m00,m01,...,m15[;...]` -- a DECODED 4x4 (row-major, BLENDER
        # space), positionally matched to `pkg=`, and the thing `pkg_offset` was
        # standing in for. `le_mesh.attach.component_attach_matrix` produces it
        # from the two rigs' shared joint name space; see that module for the
        # provenance. It is applied as `matrix_world = M @ matrix_world` so the
        # importer's own axis conversion is preserved.
        # ⚠ empty entries are KEPT: `pkg_xform=;<16 floats>` means "identity on
        # package 0, this matrix on package 1", which is the two-part-character case.
        _raw = str(opts.get("pkg_xform", ""))
        _xfs = _raw.split(";") if _raw else []
        if 0 <= _i < len(_xfs) and _xfs[_i].strip():
            try:
                vals = [float(v) for v in _xfs[_i].replace(" ", "").split(",")]
            except ValueError:
                raise SystemExit(f"pkg_xform[{_i}] is not 16 floats")
            if len(vals) != 16:
                raise SystemExit(f"pkg_xform[{_i}] needs 16 floats, got {len(vals)}")
            M = Matrix((tuple(vals[0:4]), tuple(vals[4:8]),
                        tuple(vals[8:12]), tuple(vals[12:16])))
            # ⚠ compose onto `matrix_basis`, NOT `matrix_world`: the importer
            # writes the y-up->z-up conversion into `matrix_basis` and
            # `matrix_world` is STALE until the depsgraph updates, so
            # `M @ matrix_world` silently wipes the axis conversion (it read as a
            # 90 deg pitch on the first attempt at this).
            for ob in got:
                if ob.parent is None:
                    ob.matrix_basis = M @ ob.matrix_basis
            bpy.context.view_layer.update()
            say("pkg_xform", f"{Path(spec).name} <- DECODED component attach "
                             f"{[round(v, 6) for v in vals]}")
        _rawo = str(opts.get("pkg_offset", ""))
        _offs = _rawo.split(";") if _rawo else []
        if 0 <= _i < len(_offs) and _offs[_i].strip():
            try:
                dx, dy, dz = (float(v) for v in _offs[_i].split(","))
            except ValueError:
                raise SystemExit(f"pkg_offset[{_i}] is not x,y,z: {_offs[_i]!r}")
            for ob in got:
                ob.location = (ob.location[0] + dx, ob.location[1] + dy,
                               ob.location[2] + dz)
            bpy.context.view_layer.update()
            say("pkg_offset", f"{Path(spec).name} += ({dx}, {dy}, {dz})  "
                              f"[ASSEMBLY CONVENIENCE, not a decoded transform]")
        imported += got

    if place:
        say("placement", f"{len(specs)} packages -> {placed_total} instances "
                         f"({unresolved_total} unresolved-but-placed); "
                         f"{no_placement} package(s) had no placement in scene.json")

    for spec in [s for s in opts.get("scatter", "").split(",") if s]:
        sopts = {
            "lod_level": lod,
            "flip_v": True,
            "y_up_to_z_up": True,
            "auto_materials": True,
            "lightmap_mode": lm_mode,
        }
        if "max_instances" in opts:
            sopts["max_instances"] = opt_i(opts, "max_instances", 0)
        for k in ("lightmap_dir", "lightmap_texture", "lightmap_slice_dir",
                  "instance_lightmap_uv_source", "instance_lightmap_uv_layer"):
            if k in opts:
                sopts[k] = opts[k]
        if "lightmap_intensity" in opts:
            sopts["lightmap_intensity"] = opt_f(opts, "lightmap_intensity", 1.0)
        # ⛔ Without this the atlas is sampled through the `.lescatter` `uv1`
        # stream, which is entirely ZERO on 1046/1050 meshes -- i.e. texel (0,0)
        # for 99.6 % of the level. The per-instance UVs are the only correct
        # source, and they need the `--instance-lightmap` package.
        if opt_b(opts, "instance_lightmap", False):
            sopts["instance_lightmap"] = True
            sopts.setdefault("instance_lightmap_uv_source", "instance")
        res = lone_echo_import.import_lescatter(spec, bpy.context, sopts)
        say("import", f"{Path(spec).name}: {res.get('objects')} objs "
                      f"{res.get('instances', '?')} instances")
        imported += imported_objects(res)

    imported = apply_object_filter(imported, opts)

    if not imported:
        raise SystemExit("nothing imported -- pass pkg= and/or scatter=")

    # the level's OWN lights, when asked for. Defaults to the diffuse subset:
    # 95 % of shipped lights set `eEnableSpecular` and only 42 % set
    # `eEnableDiffuse`, so importing all of them double-lights (measured 7.06x).
    if "lights" in opts:
        from lone_echo_import import light_import as LI      # noqa: PLC0415
        lres = LI.import_lights(opts["lights"], bpy.context, {
            "import_lights": True,
            "light_set": opts.get("light_set", "diffuse"),
            "exposure_scale": opt_f(opts, "light_exposure", 1.0),
        })
        say("lights", f"{opts['lights']}: {lres}")

    # ⚠ mandatory before ANY framing: the axis conversion lives on matrix_basis
    bpy.context.view_layer.update()

    if place:
        pts, n_inst = instanced_points(bpy.context)
        say("subject", f"depsgraph: {n_inst} rendered mesh instances")
        if opt_b(opts, "dump", False):
            for line in placement_dump(bpy.context):
                say("dump", line)
    else:
        pts = subject_points(imported)
    if not pts:
        raise SystemExit("imported objects carry no vertices")

    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    say("subject", f"{len(pts)} sampled pts  bbox={[round(v,3) for v in lo]}"
                   f"..{[round(v,3) for v in hi]}  height={hi[2]-lo[2]:.3f}")

    lens = opt_f(opts, "lens", P["lens"])
    az = opt_f(opts, "azimuth", P["azimuth"])
    el = opt_f(opts, "elevation", P["elevation"])
    margin = opt_f(opts, "margin", P["margin"])
    resx = opt_i(opts, "resx", P["resx"])
    resy = opt_i(opts, "resy", P["resy"])
    shift_x = opt_f(opts, "shift_x", P.get("shift_x", 0.0))
    shift_y = opt_f(opts, "shift_y", P.get("shift_y", 0.0))
    samples = opt_i(opts, "samples", P["samples"])
    rig_name = opts.get("rig", P["rig"])
    crop = opts.get("crop", P.get("crop", "none"))

    frame_pts = pts
    target = None
    if crop == "upper":
        # Frame only the top `1 - crop_from` of the subject.  Fitting the whole
        # figure and then "zooming" would change the crop but NOT the
        # perspective, which is the thing a longer lens is actually for.
        cut = lo[2] + opt_f(opts, "crop_from", P.get("crop_from", 0.55)) * (hi[2] - lo[2])
        frame_pts = [p for p in pts if p[2] >= cut] or pts
        flo = [min(p[i] for p in frame_pts) for i in range(3)]
        fhi = [max(p[i] for p in frame_pts) for i in range(3)]
        target = tuple((flo[i] + fhi[i]) * 0.5 for i in range(3))
        say("crop", f"upper: z>={cut:.3f} -> {len(frame_pts)} pts")

    cam_loc = opt_vec(opts, "cam_loc")
    cam_target = opt_vec(opts, "cam_target")
    if cam_loc is not None:
        if cam_target is None:
            cam_target = target or tuple((lo[i] + hi[i]) * 0.5 for i in range(3))
        fit = framing.look_at(cam_loc, cam_target, pts, lens=lens,
                              res_x=resx, res_y=resy)
        say("camera", f"EXPLICIT eye={cam_loc} target={cam_target}")
    else:
        fit = framing.fit_view(frame_pts, framing.orbit_direction(az, el),
                               lens=lens, res_x=resx, res_y=resy, margin=margin,
                               shift_x=shift_x, shift_y=shift_y, target=target)
    say("camera", f"lens={lens} az={az} el={el} dist={fit['distance']:.3f} "
                  f"loc=({fit['location'][0]:.3f},{fit['location'][1]:.3f},"
                  f"{fit['location'][2]:.3f}) clip={fit['clip_start']:.4f}"
                  f"..{fit['clip_end']:.1f}")

    place_camera(scene, fit, lens=lens, shift_x=shift_x, shift_y=shift_y,
                 fstop=opt_f(opts, "fstop", 0.0) or None)

    RIGS[rig_name](scene, fit,
                   key=opt_f(opts, "key", 1.0),
                   fill=opt_f(opts, "fill", 1.0),
                   rim=opt_f(opts, "rim", 1.0),
                   backdrop=opt_b(opts, "backdrop", rig_name == "studio"))
    say("rig", rig_name)

    configure_render(scene,
                     engine=opts.get("engine", "cycles"),
                     device=opts.get("device", ""),
                     samples=samples, resx=resx, resy=resy,
                     view=opts.get("view", "Standard"),
                     look=opts.get("look", "None"),
                     exposure=opt_f(opts, "exposure", 0.0),
                     transparent=opt_b(opts, "transparent", False))

    turns = opt_i(opts, "turns", 0)
    if preset_name == "turntable" and turns:
        for i in range(turns):
            a = az + 360.0 * i / turns
            f = framing.fit_view(frame_pts, framing.orbit_direction(a, el),
                                 lens=lens, res_x=resx, res_y=resy,
                                 margin=margin, target=target)
            for ob in list(scene.objects):
                if ob.type == "CAMERA":
                    bpy.data.objects.remove(ob, do_unlink=True)
            place_camera(scene, f, lens=lens)
            scene.render.filepath = str(out.with_name(f"{out.stem}_{i:02d}.png"))
            t0 = time.time()
            bpy.ops.render.render(write_still=True)
            say("render", f"{scene.render.filepath} az={a:.0f} "
                          f"{time.time()-t0:.1f}s")
    else:
        scene.render.filepath = str(out)
        t0 = time.time()
        bpy.ops.render.render(write_still=True)
        say("render", f"{out} {resx}x{resy} {time.time()-t0:.1f}s")

    log = out.with_suffix(".log.txt")
    log.write_text("\n".join(_log), encoding="utf-8")
    say("done", str(log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
