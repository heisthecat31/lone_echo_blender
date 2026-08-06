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

Pass `materials=<sidecar.json>` to bind a `<master>_materials.json`. Both the
legacy v1 (flat fields) and the v2+ (full `.lemesh` spec under `"spec"`) sidecars
are accepted -- see `scatter_import`.

PER-INSTANCE LIGHTMAP (package v5). `instlm=1` turns on
`scatter_import`'s per-instance lightmap mode: every lightmapped instance gets
its OWN mesh datablock carrying its OWN atlas UVs, on the INSTANCE's page.
`lmdir=` / `lmtex=` name the level atlas, `lmintensity=` is the documented
exposure aid, `lmbasis=sg5|single`, `lmslicedir=` caches the per-page splits.

⛔ `uvsource=uv1` renders the DOCUMENTED FAILURE MODE instead -- the naive
consumer that reuses the `.lemesh` model and takes the light UV from the vertex
stream. 1046 of 1050 shipped `uv1` blobs are entirely ZERO, so it samples atlas
texel (0,0) for 99.6 % of the level, on the per-MESH page rather than the
instance's. It exists so that can be SEEN, not only described.

`SCATTER_LIGHTMAP:` reports the wiring; `SCATTER_MEMORY:` reports the datablock
counts and Blender's own memory statistic, so the cost of breaking instancing is
a measured number rather than an estimate.

⚠ COLOUR MANAGEMENT. The view transform is forced to **`Standard`** and read back
(`viewtransform=` overrides). Blender 4.0+ defaults to **AgX**, which heavily
desaturates highlights: comparing an emissive panel under AgX against anything
numeric makes correct values look wrong.

⚠ DEPSGRAPH. `bpy.context.view_layer.update()` is called before the camera is
framed. `matrix_world`/`bound_box` read stale (identity / unit cube) on objects
created in this same script until the depsgraph evaluates, and framing off stale
bounds returns a black picture.

After the render the harness prints `SCATTER_STATS:` -- mean/percentile luma of
the written PNG plus the material readout (`surface_render_method` counts read
BACK off each material, emission/alpha link counts, images loaded) so a
before/after pair is comparable numerically and not only by eye.
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


def _material_stats(objs):
    """Read the material state BACK off the datablocks.

    ⚠ `mat.blend_method` is a dead alias on Blender 4.2+ -- OPAQUE/CLIP/HASHED all
    collapse to `surface_render_method == 'DITHERED'` and only BLEND yields
    'BLENDED'. So the pass is counted from `surface_render_method`, read back
    after the write, never assumed from what was written.
    """
    mats = []
    seen = set()
    for o in objs:
        for slot in o.data.materials:
            if slot is not None and slot.name not in seen:
                seen.add(slot.name)
                mats.append(slot)
    stats = {"materials": len(mats), "BLENDED": 0, "DITHERED": 0, "other": 0,
             "with_nodes": 0, "image_textures": 0, "emission_linked": 0,
             "emission_strength_gt0": 0, "alpha_linked": 0, "normal_linked": 0,
             "roughness_linked": 0, "specular_tint_wired": 0,
             "backface_culled": 0, "render_mode": {}, "mattype": {}}
    for m in mats:
        srm = getattr(m, "surface_render_method", None)
        if srm in ("BLENDED", "DITHERED"):
            stats[srm] += 1
        else:
            stats["other"] += 1
        rm = m.get("le_render_mode")
        if rm:
            stats["render_mode"][rm] = stats["render_mode"].get(rm, 0) + 1
        mtn = m.get("le_mattype_name")
        if mtn:
            stats["mattype"][mtn] = stats["mattype"].get(mtn, 0) + 1
        if getattr(m, "use_backface_culling", False):
            stats["backface_culled"] += 1
        if not m.use_nodes or m.node_tree is None:
            continue
        stats["with_nodes"] += 1
        nt = m.node_tree
        stats["image_textures"] += sum(
            1 for n in nt.nodes if n.type == "TEX_IMAGE" and n.image is not None)
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        for label, names in (("emission_linked", ("Emission Color", "Emission")),
                             ("alpha_linked", ("Alpha",)),
                             ("normal_linked", ("Normal",)),
                             ("roughness_linked", ("Roughness",))):
            for nm in names:
                if nm in bsdf.inputs and bsdf.inputs[nm].links:
                    stats[label] += 1
                    break
        es = bsdf.inputs.get("Emission Strength")
        if es is not None and (es.links or es.default_value > 0.0):
            stats["emission_strength_gt0"] += 1
        # ⚠ `Specular Tint` is LINKED whenever a specular map resolves
        # (material_builder wires `specalbedo / F0(IOR)` into it), so testing
        # `default_value` alone under-reports it to zero.
        st = bsdf.inputs.get("Specular Tint")
        if st is not None and (st.links
                               or tuple(st.default_value)[:3] != (1.0, 1.0, 1.0)):
            stats["specular_tint_wired"] += 1
    return stats


def _memory_stats():
    """Datablock counts + Blender's own memory figure.

    ⚠ The per-instance lightmap path trades instancing for correctness: one
    `bpy.data.meshes` datablock per lightmapped instance. `scene.statistics()` is
    read BACK from Blender rather than estimated, so the cost quoted in the
    findings is a measurement.
    """
    out = {"meshes": len(bpy.data.meshes), "objects": len(bpy.data.objects),
           "materials": len(bpy.data.materials), "images": len(bpy.data.images)}
    try:
        stats = bpy.context.scene.statistics(bpy.context.view_layer)
        out["statistics"] = stats
        for part in stats.split("|"):
            if "Memory" in part:
                out["memory"] = part.strip()
    except Exception as e:                                   # noqa: BLE001
        out["statistics_error"] = str(e)
    return out


def _lightmap_stats(objs):
    """Read the per-instance lightmap state BACK off the datablocks."""
    pages = {}
    uv_layers = {}
    wired = 0
    for o in objs:
        p = o.get("le_lightmap_page")
        if p is None:
            p = o.data.get("le_lightmap_page")
        if p is not None:
            pages[int(p)] = pages.get(int(p), 0) + 1
            wired += 1
        names = tuple(sorted(l.name for l in o.data.uv_layers))
        uv_layers[names] = uv_layers.get(names, 0) + 1
    lm_mats = {}
    for o in objs:
        for m in o.data.materials:
            if m is not None and m.get("le_lightmap_page") is not None:
                lm_mats[m.name] = int(m["le_lightmap_page"])
    return {"objects_with_page": wired, "pages": dict(sorted(pages.items())),
            "uv_layer_sets": {"+".join(k): v for k, v in uv_layers.items()},
            "lightmap_materials": len(lm_mats),
            "unique_mesh_datablocks": len({id(o.data) for o in objs})}


def _image_stats(png_path):
    """Mean / percentile luma of the WRITTEN png, in display-referred values.

    The image is re-loaded as `Non-Color` so `pixels` hands back the encoded 8-bit
    values rather than an sRGB->linear inverse of them; that makes the number the
    luma of the picture a human sees, which is what a before/after comparison is
    about.
    """
    img = None
    for cand in (png_path, png_path + ".png"):      # Blender may append the ext
        try:
            img = bpy.data.images.load(cand, check_existing=False)
            break
        except Exception as e:                               # noqa: BLE001
            err = e
    if img is None:
        return {"error": f"load failed: {err}"}
    try:
        img.colorspace_settings.name = "Non-Color"
    except Exception:
        pass
    n = len(img.pixels)
    if not n:
        return {"error": "no pixels"}
    try:
        import numpy as np
        buf = np.empty(n, dtype=np.float32)
        img.pixels.foreach_get(buf)
        rgb = buf.reshape(-1, 4)[:, :3]
        luma = 0.2126 * rgb[:, 0] + 0.7152 * rgb[:, 1] + 0.0722 * rgb[:, 2]
        out = {
            "pixels": int(luma.size),
            "mean_luma": round(float(luma.mean()), 6),
            "median_luma": round(float(np.median(luma)), 6),
            "p90_luma": round(float(np.percentile(luma, 90)), 6),
            "p99_luma": round(float(np.percentile(luma, 99)), 6),
            "max_luma": round(float(luma.max()), 6),
            "frac_above_0.5": round(float((luma > 0.5).mean()), 6),
            "frac_below_0.02": round(float((luma < 0.02).mean()), 6),
            "mean_rgb": [round(float(rgb[:, i].mean()), 6) for i in range(3)],
        }
    except ImportError:
        px = list(img.pixels)
        cnt = len(px) // 4
        tot = sum(0.2126 * px[i * 4] + 0.7152 * px[i * 4 + 1] + 0.0722 * px[i * 4 + 2]
                  for i in range(cnt))
        out = {"pixels": cnt, "mean_luma": round(tot / cnt, 6)}
    bpy.data.images.remove(img)
    return out


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
    import_opts = {"flip_v": True, "y_up_to_z_up": True, "import_proxy": False,
                   "max_instances": max_instances,
                   "lod_level": int(opts.get("lod", "0")),
                   "materials_json": opts.get("materials")}
    if opts.get("instlm", "0") not in ("0", "", "false", "False"):
        import_opts["instance_lightmap"] = True
        for key, opt in (("lightmap_texture", "lmtex"),
                         ("lightmap_dir", "lmdir"),
                         ("lightmap_slice_dir", "lmslicedir"),
                         ("lightmap_basis", "lmbasis"),
                         ("lightmap_mode", "lmmode"),
                         ("instance_lightmap_uv_source", "uvsource")):
            if opts.get(opt):
                import_opts[key] = opts[opt]
        if opts.get("lmintensity"):
            import_opts["lightmap_intensity"] = float(opts["lmintensity"])
    summary = lone_echo_import.import_lescatter(pkg_path, bpy.context, import_opts)
    print("SCATTER_SUMMARY:", {k: v for k, v in summary.items()
                               if k != "instance_lightmap"})
    print("SCATTER_LIGHTMAP:", summary.get("instance_lightmap"))

    coll = bpy.data.collections.get(summary["collection"])
    objs = [o for o in (coll.objects if coll else []) if o.type == "MESH"]
    if not objs:
        print("SCATTER_RENDER: FAIL (no placed instances)")
        return

    # ⚠ DEPSGRAPH: object matrices/bounds are stale until the view layer
    # evaluates. Framing a camera off stale bounds renders black.
    bpy.context.view_layer.update()

    # per-instance world positions (origin of each instance) — placement proof.
    # Suppressed above `dump=` instances (a level has thousands).
    dump_limit = int(opts.get("dump", "200"))
    if len(objs) <= dump_limit:
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

    # camera (camdir="dx,dy,dz" + camdist=scale args override the 3/4 default).
    # The framing depends ONLY on geometry, so a before/after pair over the same
    # package + same lod + same max_instances gets a bit-identical camera; it is
    # printed so that can be checked rather than assumed.
    cam_data = bpy.data.cameras.new("cam"); cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    # `camloc=x,y,z` + `camtarget=x,y,z` pin the camera in WORLD space. Needed for
    # a level: auto-framing a 320-m corridor puts every prop at a few pixels, and
    # a material change that is obvious close up is then invisible.
    if opts.get("camloc"):
        cam.location = Vector([float(v) for v in opts["camloc"].split(",")])
        target = (Vector([float(v) for v in opts["camtarget"].split(",")])
                  if opts.get("camtarget") else center)
    else:
        direction = (Vector([float(v) for v in camdir.split(",")]) if camdir
                     else Vector((1.0, -1.2, 0.7))).normalized()
        cam.location = center + direction * size * camdist
        target = center
    center = target
    cam.rotation_euler = (target - cam.location).to_track_quat("-Z", "Y").to_euler()
    if opts.get("lens"):
        cam_data.lens = float(opts["lens"])
    bpy.context.scene.camera = cam
    print(f"SCATTER_CAMERA: loc=({cam.location.x:.4f}, {cam.location.y:.4f}, "
          f"{cam.location.z:.4f}) center=({center.x:.4f}, {center.y:.4f}, "
          f"{center.z:.4f}) size={size:.4f}")

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
    # ⚠ Colour management: Blender 4.0+ defaults to AgX, which desaturates
    # highlights hard and invalidates any numeric before/after comparison.
    want_vt = opts.get("viewtransform", "Standard")
    try:
        scene.view_settings.view_transform = want_vt
    except Exception as e:                                   # noqa: BLE001
        print("view_transform warn:", e)
    got_vt = getattr(scene.view_settings, "view_transform", "?")
    print(f"VIEW_TRANSFORM: wanted={want_vt} got={got_vt} "
          f"look={getattr(scene.view_settings, 'look', '?')} "
          f"exposure={getattr(scene.view_settings, 'exposure', '?')}")

    scene.render.resolution_x = int(opts.get("resx", "1280"))
    scene.render.resolution_y = int(opts.get("resy", "960"))
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.filepath = out_png
    bpy.ops.render.render(write_still=True)

    mstats = _material_stats(objs)
    print("SCATTER_MATERIALS:", mstats)
    print("SCATTER_LM_READBACK:", _lightmap_stats(objs))
    print("SCATTER_MEMORY:", _memory_stats())
    print("SCATTER_STATS:", _image_stats(out_png))

    ok = len(objs) == summary["instances_placed"] and len(distinct) > 1
    print(f"RENDERED: {out_png}")
    print(f"SCATTER_RENDER: {'PASS' if ok else 'FAIL'} "
          f"(placed={len(objs)}, distinct_positions={len(distinct)})")


main()
