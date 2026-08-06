"""In-Blender probe for `addon/lone_echo_import/light_import.py`.

Three jobs, all `engine-confirmed` (they read the live Blender RNA):

  1. **RNA probe** — which Light properties actually exist on this build
     (`use_custom_distance`, `cutoff_distance`, `shadow_soft_size`, `angle`,
     `use_nodes`, `ShaderNodeLightFalloff`), and what the writes read back as.
  2. **Axis proof** — `light_import.axis_rows()` must equal
     `mesh_builder._axis_matrix({"y_up_to_z_up": True})`, and every imported
     lamp's world `-Z` must equal the axis-converted stored `direction`. A light
     rig rotated relative to the geometry is a silent, expensive bug.
  3. **Verification renders** — the SAME receiver geometry and the SAME camera,
     lit ONLY by imported lights, with `view_transform = 'Standard'` (Blender
     4.0+ defaults to AgX, which desaturates highlights and would make correct
     values look wrong):

        lights_A_diffuse_only.png     light_set="diffuse" (the DEFAULT, 15/47)
        lights_B_all_lights.png       light_set="all"     (47/47 -> DOUBLE-LIT)
        lights_C_all_hidden_spec.png  light_set="all" + hide_specular_only
                                      (the shipped opt-in: must match A)
        lights_D_diffuse_only_view2.png / lights_E_all_lights_view2.png
                                      the same A/B pair from the second
                                      viewpoint (the shipped lights aim in
                                      opposing directions, so no one camera
                                      sees every lit hemisphere)

     A vs B (and D vs E) is the double-lighting risk made visible; the "all"
     renders light surfaces whose diffuse response the game had already BAKED.

Run (ABSOLUTE WINDOWS paths -- blender.exe starts with cwd C:\\):

    "/mnt/c/Program Files/Blender Foundation/Blender 5.1/blender.exe" \
        --background --factory-startup \
        --python blender_tool/tests/blender_light_probe.py -- \
        <ABS-WINDOWS lights.json> <ABS-WINDOWS outdir>

NOT named `test_*` on purpose: `tests/run_tests.py` auto-imports `test_*.py`
under plain python3, where `bpy` does not exist.
"""

import json
import math
import sys
from pathlib import Path

import bpy                                   # type: ignore
from mathutils import Matrix, Vector         # type: ignore

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"),
          str(BLENDER_TOOL / "addon" / "lone_echo_import"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import light_import as LI                    # noqa: E402  (standalone, no bpy at module scope)
import lone_echo_import                      # noqa: E402  (for mesh_builder._axis_matrix)
from render_engine_util import resolve_render_engine, WORKBENCH_ID   # noqa: E402

RES_X, RES_Y = 1280, 720

# The corridor section rendered: it contains BOTH diffuse-enabled and
# specular-only lights, so A-vs-B shows the difference on one surface set.
SECTION_Y = (-118.0, -56.0)

# TWO viewpoints, because the shipped lights aim in opposing directions and no
# single camera can see every lit hemisphere:
#   VIEW_BELOW  favours the eEnableDiffuse wall-washers (they aim +X/+Z)
#   VIEW_SIDE   favours the specular-only down-lights (they aim straight -Z)
# Each is used for BOTH light sets, so every A/B pair is a fair comparison.
VIEW_BELOW = (-0.30, -0.34, -0.89)
VIEW_SIDE = (0.90, 0.40, -0.14)

PASS = []
FAIL = []


def check(label, ok, detail=""):
    (PASS if ok else FAIL).append(label)
    print(f"  [{'OK ' if ok else 'FAIL'}] {label}{(' -- ' + detail) if detail else ''}")
    return ok


# ---------------------------------------------------------------------------
# 1. RNA probe
# ---------------------------------------------------------------------------

def probe_rna():
    print(f"\n=== Blender {bpy.app.version_string} — Light RNA probe ===")
    out = {"blender": bpy.app.version_string}
    data = bpy.data.lights.new("__probe_spot", type="SPOT")
    for name in ("color", "energy", "use_shadow", "spot_size", "spot_blend",
                 "shadow_soft_size", "use_custom_distance", "cutoff_distance",
                 "use_nodes", "radius", "size", "shadow_buffer_bias"):
        out[f"Light.{name}"] = hasattr(data, name)
    sun = bpy.data.lights.new("__probe_sun", type="SUN")
    out["Sun.angle"] = hasattr(sun, "angle")
    out["ShaderNodeLightFalloff"] = hasattr(bpy.types, "ShaderNodeLightFalloff")

    # write-back check: do the values we set actually stick?
    data.energy = 201.06193
    data.spot_size = 1.65806
    data.spot_blend = 0.42101
    data.shadow_soft_size = 0.0
    if hasattr(data, "use_custom_distance"):
        data.use_custom_distance = True
        data.cutoff_distance = 6.0
    out["readback"] = {
        "energy": data.energy, "spot_size": data.spot_size,
        "spot_blend": data.spot_blend, "shadow_soft_size": data.shadow_soft_size,
        "cutoff_distance": getattr(data, "cutoff_distance", None),
        "use_custom_distance": getattr(data, "use_custom_distance", None),
    }
    for k, v in out.items():
        print(f"  {k}: {v}")
    check("Light exposes spot_size/spot_blend/shadow_soft_size",
          out["Light.spot_size"] and out["Light.spot_blend"]
          and out["Light.shadow_soft_size"])
    check("Light exposes use_custom_distance + cutoff_distance",
          out["Light.use_custom_distance"] and out["Light.cutoff_distance"])
    check("ShaderNodeLightFalloff exists (Cycles non-quadratic path)",
          out["ShaderNodeLightFalloff"])
    check("shadow_soft_size reads back 0.0 (no fabricated source radius)",
          abs(out["readback"]["shadow_soft_size"]) < 1e-9)
    check("Light has NO on-disk-equivalent 'radius'/'size' we could have used",
          not out["Light.radius"] and not out["Light.size"])
    return out


# ---------------------------------------------------------------------------
# 2. axis proof
# ---------------------------------------------------------------------------

def probe_axis(doc):
    print("\n=== Axis: the light rig must share the mesh basis ===")
    A = lone_echo_import.mesh_builder._axis_matrix({"y_up_to_z_up": True})
    rows = LI.axis_rows(True)
    worst = max(abs(A[r][c] - rows[r][c]) for r in range(3) for c in range(3))
    # 1e-6, not 0: `mesh_builder._axis_matrix` builds the same rotation via
    # `Matrix.Rotation(radians(90), 4, "X")`, whose cos(90 deg) lands at
    # -4.37e-08 in Blender's float precision. `axis_rows()` writes exact 0/+-1.
    # Same convention, different construction -- a real mismatch would be O(1).
    check("light_import.axis_rows() == mesh_builder._axis_matrix()", worst < 1e-6,
          f"max|delta| = {worst:g} (float noise from Matrix.Rotation, not a "
          "convention difference)")
    check("axis matrix is a PURE rotation (det +1, no mirror)",
          abs(A.to_3x3().determinant() - 1.0) < 1e-12,
          f"det = {A.to_3x3().determinant():.12f}")

    # every light's world -Z must be its axis-converted stored direction
    worst_dir = 0.0
    worst_loc = 0.0
    for _, _, rec in LI.iter_lights(doc):
        M = Matrix(LI.light_matrix_rows(rec))
        fwd = (M.to_3x3() @ Vector((0.0, 0.0, -1.0))).normalized()
        want = Vector(LI.to_blender_vec(rec["direction"])).normalized()
        worst_dir = max(worst_dir, (fwd - want).length)
        loc = M.to_translation()
        worst_loc = max(worst_loc,
                        (loc - Vector(LI.to_blender_vec(rec["pos"]))).length)
    check("every lamp's world -Z == A @ stored direction", worst_dir < 1e-5,
          f"max error = {worst_dir:g} over {len(doc['scenes'][0]['lights'])} lights")
    check("every lamp's world location == A @ pos", worst_loc < 1e-4,
          f"max error = {worst_loc:g}")
    return worst_dir


# ---------------------------------------------------------------------------
# 3. scene + renders
# ---------------------------------------------------------------------------

def _reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _matte(name, base=(0.55, 0.55, 0.58, 1.0), rough=0.45):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = base
    bsdf.inputs["Roughness"].default_value = rough
    if "Metallic" in bsdf.inputs:
        bsdf.inputs["Metallic"].default_value = 0.0
    return mat


def build_receivers(doc):
    """One matte sphere per shipped light, at that light's own beam midpoint.

    ⚠ These are NOT level geometry — the real corridor props are not loaded here.
    The light rig is 100 % real (real world positions, orientations, cones,
    colours, energies, ranges); only the receivers are synthetic.

    Why spheres at `pos + dir * range/2` rather than floors and walls: the
    shipped lights aim in mutually opposing directions (18 straight down, 12
    up-and-right at +45 deg, 3 down-and-left, 6 omni points, 4 up-and-right at
    +55 deg), each with a 2-10 m reach, so NO single flat surface is inside more
    than one group's range — the level's real receivers are small props a couple
    of metres from each lamp. A sphere at the beam midpoint is always inside the
    light's range and always shows a lit crescent from any camera, so one shot
    can compare the two light sets fairly.

    The sphere set is IDENTICAL in every render (it is built from the whole
    47-light document, not from the selection), so A vs B differ only in which
    lamps exist.
    """
    mat = _matte("le_probe_matte")
    obs = []
    y0, y1 = SECTION_Y
    for _, _, rec in LI.iter_lights(doc):
        if LI.light_type_enum(rec) == 2:                 # SUN: no position
            continue
        loc = Vector(LI.to_blender_vec(rec["pos"]))
        if not (y0 <= loc.y <= y1):
            continue
        d = Vector(LI.to_blender_vec(rec["direction"]))
        rng = LI.light_range(rec)
        if d.length > 1e-6 and LI.light_type_enum(rec) == 1:
            centre = loc + d.normalized() * (rng * 0.5)
        else:                                            # point light: below it
            centre = loc + Vector((0.0, 0.0, -rng * 0.5))
        r = max(0.9, min(2.4, rng * 0.35))
        bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=centre,
                                             segments=32, ring_count=16)
        ob = bpy.context.object
        ob.name = f"recv_{int(rec['index']):03d}"
        ob.data.materials.append(mat)
        for p in ob.data.polygons:
            p.use_smooth = True
        obs.append(ob)
    print(f"  receivers: {len(obs)} spheres in y {y0}..{y1}")
    return obs


def setup_camera(receivers, view=None):
    """Orthographic view auto-fitted to the receiver set, IDENTICAL every render.

    ORTHO because the corridor section is tens of metres long: under perspective
    the far lamps shrink to nothing and the two light sets stop being comparable.
    The camera sits slightly BELOW the horizontal — 18 of the shipped spots aim
    straight down, so a camera above them would only ever see their unlit tops.
    `ortho_scale` is fitted from the receivers projected into camera space, so
    the frame is tight regardless of which section is chosen.
    """
    pts = []
    for o in receivers:
        for c in o.bound_box:
            pts.append(o.matrix_world @ Vector(c))
    centre = sum(pts, Vector((0.0, 0.0, 0.0))) / len(pts)

    cam_data = bpy.data.cameras.new("probe_cam")
    cam_data.type = "ORTHO"
    cam = bpy.data.objects.new("probe_cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)

    view = Vector(view if view is not None else VIEW_BELOW).normalized()
    # Roll the camera so the corridor axis (world -Y) lands on the image X axis;
    # otherwise the run of lamps crosses the frame diagonally and the fit wastes
    # most of the picture.
    fwd = -view                                        # camera local -Z
    corridor = Vector((0.0, -1.0, 0.0))
    right = (corridor - fwd * corridor.dot(fwd)).normalized()
    up = fwd.cross(right).normalized() * -1.0
    R = Matrix((right, up, -fwd)).transposed()         # columns = local X, Y, Z
    half_u = max(abs((p - centre).dot(right)) for p in pts)
    half_v = max(abs((p - centre).dot(up)) for p in pts)
    aspect = RES_X / RES_Y
    cam_data.ortho_scale = max(2.0 * half_u, 2.0 * half_v * aspect) * 1.08
    depth = max((p - centre).length for p in pts) + 40.0
    cam.location = centre + view * depth
    cam.rotation_euler = R.to_euler()
    cam_data.clip_start = 0.1
    cam_data.clip_end = depth * 3.0
    bpy.context.scene.camera = cam
    print(f"  camera(ORTHO scale={cam_data.ortho_scale:.1f}) at "
          f"{tuple(round(v, 2) for v in cam.location)} -> "
          f"{tuple(round(v, 2) for v in centre)}")
    return cam


def setup_render(engine_mode="eevee"):
    sc = bpy.context.scene
    ids = [e.identifier for e in sc.render.bl_rna.properties["engine"].enum_items]
    try:
        sc.render.engine = resolve_render_engine(engine_mode, ids)
    except ValueError as exc:
        print(f"  WARNING: {exc}; falling back to Workbench")
        sc.render.engine = WORKBENCH_ID
    sc.render.resolution_x, sc.render.resolution_y = RES_X, RES_Y
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    # ⚠ Blender 4.0+ defaults to AgX, which desaturates highlights and makes
    # correct HDR values look wrong. Standard for any numeric comparison.
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.look = "None"
    sc.view_settings.exposure = 0.0
    sc.view_settings.gamma = 1.0
    if getattr(sc, "eevee", None) is not None:
        for attr, val in (("taa_render_samples", 32), ("use_raytracing", True)):
            if hasattr(sc.eevee, attr):
                try:
                    setattr(sc.eevee, attr, val)
                except Exception:      # noqa: BLE001
                    pass
    # a BLACK world: the imported lights are the ONLY light in the scene
    world = bpy.data.worlds.new("black")
    world.use_nodes = True
    bg = next((n for n in world.node_tree.nodes if n.type == "BACKGROUND"), None)
    if bg is not None:
        bg.inputs["Color"].default_value = (0.0, 0.0, 0.0, 1.0)
        bg.inputs["Strength"].default_value = 0.0
    bpy.context.scene.world = world
    print(f"  engine = {sc.render.engine}, view_transform = "
          f"{sc.view_settings.view_transform}")
    return sc.render.engine


def render_case(label, lights_path, opts, outdir, view=None):
    _reset()
    setup_render()
    recv = build_receivers(LI.load_lights(lights_path))
    setup_camera(recv, view)
    summary = LI.import_lights(lights_path, bpy.context, opts)
    lamps = [o for o in bpy.data.objects if o.type == "LIGHT"]
    visible = [o for o in lamps if not o.hide_render]     # as the IMPORTER left them

    # The ePrimaryDirLight is imported and numerically verified (10.0 W/m^2, see
    # the RNA readback below) but hidden in the BEAUTY render only: these
    # receivers float in vacuum with no corridor shell, so an unoccluded 10 W/m^2
    # sun clips every sphere to white and hides the local lamps. In the level the
    # geometry occludes it. This is a probe-scene choice, not importer behaviour.
    n_sun = 0
    for o in lamps:
        if o.data.type == "SUN":
            o.hide_render = True
            n_sun += 1
    out = Path(outdir) / f"{label}.png"
    bpy.context.scene.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    print(f"\n--- {label} ---")
    print("  " + json.dumps({k: v for k, v in summary.items() if k != "warnings"}))
    for w in summary["warnings"]:
        print(f"  WARN: {w}")
    print(f"  lamp objects = {len(lamps)}, render-visible = {len(visible)} "
          f"({n_sun} SUN hidden for the beauty pass only)")
    print(f"  wrote {out}")
    return summary, lamps, visible


def probe_blender_side(lights_path, outdir):
    print("\n=== Import + render ===")
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    a, a_lamps, a_vis = render_case(
        "lights_A_diffuse_only", lights_path,
        {"scene_filter": "stn_ext_itc_station_front"}, outdir)
    check("default light_set is 'diffuse'", a["light_set"] == "diffuse")
    check("default import keeps only the 15 eEnableDiffuse lights",
          a["imported"] == 15 and a["total"] == 47,
          f"imported {a['imported']}/{a['total']}, "
          f"skipped_specular_only={a['skipped_specular_only']}")
    check("15 lamp objects created, all render-visible",
          len(a_lamps) == 15 and len(a_vis) == 15)
    check("no specular-only collection when defaulting to diffuse",
          a["specular_collection"] is None)

    b, b_lamps, b_vis = render_case(
        "lights_B_all_lights", lights_path,
        {"scene_filter": "stn_ext_itc_station_front", "light_set": "all",
         "hide_specular_only": False}, outdir)
    check("light_set='all' imports all 47", b["imported"] == 47)
    check("all 47 lamps render-visible with hide_specular_only=False",
          len(b_lamps) == 47 and len(b_vis) == 47)
    check("light_set='all' warns about double-lighting",
          any("DOUBLE-LIGHTING" in w for w in b["warnings"]))

    # second viewpoint: the specular-only down-lights face away from VIEW_BELOW,
    # so repeat the A/B pair from the side where they read clearly.
    render_case("lights_D_diffuse_only_view2", lights_path,
                {"scene_filter": "stn_ext_itc_station_front"}, outdir,
                view=VIEW_SIDE)
    render_case("lights_E_all_lights_view2", lights_path,
                {"scene_filter": "stn_ext_itc_station_front", "light_set": "all",
                 "hide_specular_only": False}, outdir, view=VIEW_SIDE)

    c, c_lamps, c_vis = render_case(
        "lights_C_all_hidden_spec", lights_path,
        {"scene_filter": "stn_ext_itc_station_front", "light_set": "all"}, outdir)
    check("shipped opt-in hides the 32 specular-only lamps",
          len(c_lamps) == 47 and len(c_vis) == 15,
          f"{len(c_vis)} visible of {len(c_lamps)}")
    check("hidden lamps live in a '_specular_only' child collection",
          c["specular_collection"] is not None, str(c["specular_collection"]))

    # the hidden lamps must contribute NOTHING: C must match A pixel-for-pixel,
    # and B must be strictly brighter than A on the identical receivers.
    means = {}
    for lbl in ("lights_A_diffuse_only", "lights_B_all_lights",
                "lights_C_all_hidden_spec", "lights_D_diffuse_only_view2",
                "lights_E_all_lights_view2"):
        img = bpy.data.images.load(str(Path(outdir) / f"{lbl}.png"))
        px = list(img.pixels)
        means[lbl] = sum(px[0::4]) / (len(px) / 4)
        bpy.data.images.remove(img)
    print("  mean red channel: " + json.dumps({k: round(v, 6)
                                               for k, v in means.items()}))
    check("hidden specular-only lamps contribute nothing (C == A)",
          abs(means["lights_C_all_hidden_spec"]
              - means["lights_A_diffuse_only"]) < 1e-6,
          f"{means['lights_C_all_hidden_spec']:.6f} vs "
          f"{means['lights_A_diffuse_only']:.6f}")
    check("importing ALL lights is measurably brighter (the double-lighting)",
          means["lights_B_all_lights"] > means["lights_A_diffuse_only"] * 1.05
          and means["lights_E_all_lights_view2"]
          > means["lights_D_diffuse_only_view2"] * 1.05,
          f"B/A = {means['lights_B_all_lights'] / max(1e-9, means['lights_A_diffuse_only']):.2f}x, "
          f"E/D = {means['lights_E_all_lights_view2'] / max(1e-9, means['lights_D_diffuse_only_view2']):.2f}x")

    # per-lamp RNA readback on the default import
    _reset()
    setup_render()
    LI.import_lights(lights_path, bpy.context,
                     {"scene_filter": "stn_ext_itc_station_front"})
    doc = LI.load_lights(lights_path)
    by_name = {o.name: o for o in bpy.data.objects if o.type == "LIGHT"}
    worst_e = worst_s = worst_b = 0.0
    soft = set()
    for _, _, rec in LI.iter_lights(doc, "stn_ext_itc_station_front"):
        p = LI.blender_params(rec)
        ob = by_name.get(p["name"])
        if ob is None:
            continue
        worst_e = max(worst_e, abs(ob.data.energy - p["energy"]) / max(1.0, p["energy"]))
        soft.add(round(ob.data.shadow_soft_size, 9))
        if ob.data.type == "SPOT":
            worst_s = max(worst_s, abs(ob.data.spot_size - p["spot_size"]))
            worst_b = max(worst_b, abs(ob.data.spot_blend - p["spot_blend"]))
        fwd = (ob.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
        want = Vector(LI.to_blender_vec(rec["direction"])).normalized()
        assert (fwd - want).length < 1e-4, (ob.name, fwd, want)
    check("energy round-trips through the RNA", worst_e < 1e-5, f"rel err {worst_e:g}")
    check("spot_size/spot_blend round-trip", worst_s < 1e-6 and worst_b < 1e-6,
          f"{worst_s:g} / {worst_b:g}")
    check("shadow_soft_size is 0.0 on every imported lamp", soft == {0.0}, str(soft))

    sun = next((o for o in bpy.data.objects
                if o.type == "LIGHT" and o.data.type == "SUN"), None)
    check("the ePrimaryDirLight imports as a SUN at 10.0 W/m^2",
          sun is not None and abs(sun.data.energy - 10.0) < 1e-4,
          f"energy = {sun.data.energy if sun else None}")
    if sun is not None:
        check("SUN angle is 0 (no source size on disk)",
              abs(sun.data.angle) < 1e-9, f"angle = {sun.data.angle}")

    nodetreed = [o for o in bpy.data.objects
                 if o.type == "LIGHT" and o.get("le_falloff_node_built")]
    check("Cycles Light Falloff nodes built for the attenmethod!=2 lights",
          len(nodetreed) >= 1, f"{len(nodetreed)} lamp(s)")

    # custom properties: the not-derivable fields are carried, inert
    any_lamp = next(o for o in bpy.data.objects if o.type == "LIGHT")
    keys = set(any_lamp.keys())
    check("not-derivable fields ride along as le_* custom properties",
          {"le_filtersize_pcf_not_a_radius", "le_cone_falloff_exponent",
           "le_faderangeoffset_runtime", "le_lightmask", "le_visindex"} <= keys,
          f"{len(keys)} props")
    return a, b, c


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    lights = argv[0] if argv else str(BLENDER_TOOL / "fixtures" / "station_front_lights.json")
    outdir = argv[1] if len(argv) > 1 else str(BLENDER_TOOL / "fixtures")

    probe_rna()
    doc = LI.load_lights(lights)
    probe_axis(doc)
    probe_blender_side(lights, outdir)

    print(f"\n=== {len(PASS)} checks passed, {len(FAIL)} failed ===")
    for f in FAIL:
        print(f"  FAILED: {f}")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
