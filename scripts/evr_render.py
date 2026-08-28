"""Headless render of an extracted `.lescatter` scene -- the verification loop.

## Why this exists

Every material/UV mistake in this project's history was shipped because the only
available signal was COUNTS -- materials built, images loaded, roles routed.
Counts cannot tell you whether the right texture is on the right mesh, and they
repeatedly went UP while the scene got worse. Four material-assignment attempts
and three UV attempts were reverted on visual evidence that arrived only after a
human imported the package by hand.

This renders the real import, headlessly, to a PNG that can be looked at.

## Not OOM-ing

A naive full import exhausts memory (`Malloc returns null ... total 1098623060`)
because a level's whole texture set is multiple GB decompressed. Three levers,
applied in this order:

* `--max-instances` caps placements at import time (the importer's own option).
* `--focus` keeps only objects whose name contains a substring, deletes the
  rest, then purges orphaned data. This is what makes single-model inspection
  cheap: after the purge only the focused object's textures remain resident.
* `--max-texture` downscales every loaded image in place. Texture fidelity does
  not matter for "is this the right texture on this mesh"; resident bytes do.

Usage (Blender must run the script, it needs `bpy`):

    blender -b -noaudio --python scripts/evr_render.py -- \
        --manifest <out_dir>/scenes/<hash>/manifest.json \
        --out J:/TMP/shot.png --lod 0 --max-instances 200

    # one object, framed automatically
    blender -b -noaudio --python scripts/evr_render.py -- \
        --manifest .../manifest.json --out shot.png --focus _i1045
"""

from __future__ import annotations

import json

import math

import argparse
import sys
from pathlib import Path

import bpy  # type: ignore
import mathutils  # type: ignore

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT / "blender_tool"), str(_ROOT / "blender_tool" / "addon")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def parse_args(argv) -> argparse.Namespace:
    if "--" in argv:
        argv = argv[argv.index("--") + 1:]
    else:
        argv = []
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", default=None)
    ap.add_argument("--fbx", default=None,
                    help="render an FBX instead of a .lescatter package -- used to put a reference recreation beside our extraction")
    ap.add_argument("--out", required=True)
    ap.add_argument("--lod", type=int, default=0)
    ap.add_argument("--max-instances", type=int, default=200)
    ap.add_argument("--focus", default=None,
                    help="keep only objects whose name contains this")
    ap.add_argument("--max-texture", type=int, default=512,
                    help="downscale loaded images to at most this on a side")
    ap.add_argument("--res", type=int, default=960)
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--engine", default="BLENDER_EEVEE")
    ap.add_argument("--angle", type=float, default=35.0,
                    help="camera azimuth in degrees about the target")
    ap.add_argument("--elevation", type=float, default=20.0)
    ap.add_argument("--inside", action="store_true",
                    help="place the camera INSIDE the scene, looking outward -- the view the reference screenshots use")
    # ── explicit camera placement ────────────────────────────────────────
    # The orbit controls below frame the scene automatically, which is right
    # for "is this the correct texture" but useless for comparing against an
    # in-game screenshot: that needs the SAME eye. `--cam-loc/--cam-rot` take a
    # Blender transform verbatim (metres, degrees, XYZ Euler) and bypass the
    # orbit entirely.
    ap.add_argument("--cam-loc", default=None,
                    help="explicit camera location 'X,Y,Z' (metres)")
    ap.add_argument("--cam-rot", default=None,
                    help="explicit camera rotation 'RX,RY,RZ' (degrees, XYZ Euler)")
    ap.add_argument("--near", default=None,
                    help="keep only objects whose ORIGIN is within R of a point: "
                         "'X,Y,Z,R'. The war room is instanced twice, so this is "
                         "how you look at one copy without the other in frame.")
    ap.add_argument("--exclude-larger-than", type=float, default=0.0,
                    help="delete objects whose bounding diagonal exceeds this "
                         "(metres). The war room ships a ~1250m backdrop DOME "
                         "(4 objects) that encloses the level; from inside it "
                         "every external view is a filled frame and every "
                         "game-look render is black.")
    ap.add_argument("--look-at", default=None,
                    help="aim the camera at 'X,Y,Z' (overrides --cam-rot). The "
                         "war room is instanced TWICE -- clusters at "
                         "(200,200,-10.6) and (-150,-200,-10.6) -- so the "
                         "auto-framed 'scene centre' is the midpoint of both "
                         "plus four 1000m+ objects, which is empty space.")
    ap.add_argument("--lens", type=float, default=0.0,
                    help="focal length in mm (0 = Blender default 50)")
    # ── look ─────────────────────────────────────────────────────────────
    # The default world here is FLAT WHITE at 1.4 on purpose (see below), which
    # makes every render look nothing like the game. `--look game` swaps it for
    # a near-black world so the level's own emissives and imported lights carry
    # the image, plus a compositor Glare for the bloom the game applies.
    # The package's `lightmaps.json` (written by `evr_apply_lighting.py`) is
    # NOT loaded by `import_lescatter` -- only the interactive operator does
    # that, via its `evr_lighting` toggle. Rendering through the module API
    # therefore produced an unlit scene no matter what the world was set to.
    ap.add_argument("--evr-lighting", action="store_true",
                    help="import the package's lightmaps.json (placed lights + baked atlases)")
    # `vertex_tints.json` is applied only by the interactive operator
    # (`_apply_vertex_tints`), NOT by `import_lescatter`. The war room's
    # no-albedo surfaces carry their colour there -- 40 BLACK, 22 orange,
    # 21 blue -- so without it those panels import white.
    ap.add_argument("--vertex-tints", action="store_true",
                    help="apply the package's vertex_tints.json")
    ap.add_argument("--dynamic-lights-only", action="store_true",
                    help="skip lights whose contribution is already baked")
    ap.add_argument("--lightmap-intensity", type=float, default=1.0)
    # `wire_instance_lightmaps(occlusion=...)` defaults to False and this script
    # never passed it, so the bake's occlusion masks were never sampled. Kept
    # opt-in rather than flipped on: occlusion REPLACES the radiance multiply
    # (see the if/elif in `evr_lighting`), so it is a different look, not a
    # strictly better one.
    ap.add_argument("--lightmap-occlusion", action="store_true",
                    help="sample the bake's occlusion masks instead of the "
                         "collapsed radiance page")
    # `evr_lighting` maps a SUN's authored intensity straight to W/m2 and other
    # types to DEFAULT_WATTS(25) * intensity. The record's "intensity" is a
    # relative multiplier with no unit -- the module says so itself -- and the
    # war room's suns are 20.0, i.e. ~20x Blender daylight, which blows its
    # 0.5-grey surfaces to pure white. Scale them here rather than guess a new
    # constant inside the importer.
    ap.add_argument("--light-scale", type=float, default=1.0,
                    help="multiply every imported light's energy")
    ap.add_argument("--look", choices=("flat", "game"), default="flat",
                    help="flat = shadowless white (texture checks); "
                         "game = dark world + bloom (screenshot comparison)")
    ap.add_argument("--world-strength", type=float, default=None,
                    help="override world background strength")
    ap.add_argument("--exposure", type=float, default=0.0,
                    help="scene exposure stops")
    ap.add_argument("--glare-threshold", type=float, default=1.0)
    ap.add_argument("--view-transform", default=None,
                    help="e.g. AgX, Filmic, Standard")
    ap.add_argument("--distance", type=float, default=0.0,
                    help="0 = auto-fit from the bounding sphere")
    return ap.parse_args(argv)


def scene_bounds(objects, percentile: float = 0.90):
    """Robust world-space (centre, radius) over mesh objects.

    Plain min/max is useless on a level: one far-flung instance stretched the
    radius to 1181 units and framed the camera on empty space. Instead take the
    MEDIAN object origin as the centre and the `percentile` distance as the
    radius, so outliers cannot drag the framing.
    """
    centres = []
    for ob in objects:
        if ob.type != "MESH":
            continue
        acc = mathutils.Vector((0.0, 0.0, 0.0))
        for corner in ob.bound_box:
            acc += ob.matrix_world @ mathutils.Vector(corner)
        centres.append(acc / 8.0)
    if not centres:
        return None, None

    def median(vals):
        vals = sorted(vals)
        return vals[len(vals) // 2]

    centre = mathutils.Vector((
        median([c.x for c in centres]),
        median([c.y for c in centres]),
        median([c.z for c in centres])))
    distances = sorted((c - centre).length for c in centres)
    radius = distances[min(len(distances) - 1,
                           int(len(distances) * percentile))]

    # With few objects the spread between CENTRES is ~0 (one object gives
    # exactly 0), which framed the camera inside the object. Fall back to the
    # largest object's own extent whenever the spread is degenerate.
    if radius < 1e-6 or len(centres) < 8:
        extent = 0.0
        for ob in objects:
            if ob.type != "MESH":
                continue
            corners = [ob.matrix_world @ mathutils.Vector(c) for c in ob.bound_box]
            for a in corners:
                for b in corners:
                    extent = max(extent, (a - b).length)
        radius = max(radius, extent * 0.5)
    return centre, max(radius, 0.001)


def downscale_images(limit: int) -> int:
    """Shrink every image in place; returns how many were touched.

    Done AFTER the import so the importer's own colourspace/alpha decisions are
    already applied -- `Image.scale` preserves them.
    """
    touched = 0
    for img in bpy.data.images:
        try:
            w, h = img.size
        except Exception:
            continue
        if not w or not h or max(w, h) <= limit:
            continue
        factor = limit / float(max(w, h))
        try:
            img.scale(max(1, int(w * factor)), max(1, int(h * factor)))
            touched += 1
        except Exception:
            pass
    return touched


def keep_only(substring: str) -> int:
    """Delete objects whose name lacks `substring`; returns how many kept."""
    kept = []
    for ob in list(bpy.data.objects):
        # endswith first so `_i104` cannot swallow `_i1045`; fall back to a
        # containment test for prefix-style filters.
        if ob.name.endswith(substring) or (
                not substring.startswith("_i") and substring in ob.name):
            kept.append(ob)
        else:
            bpy.data.objects.remove(ob, do_unlink=True)
    try:
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True,
                                       do_recursive=True)
    except Exception:
        pass
    return len(kept)


def main() -> int:
    args = parse_args(sys.argv)

    bpy.ops.wm.read_factory_settings(use_empty=True)

    if args.fbx:
        # `use_custom_props` off and lights skipped: the FBX importer trips on
        # Cycles light settings in this build, and we light the scene ourselves.
        bpy.ops.import_scene.fbx(filepath=args.fbx,
                                 use_custom_props=False,
                                 ignore_leaf_bones=True)
        print(f"[render] imported FBX: "
              f"{len([o for o in bpy.data.objects if o.type=='MESH'])} mesh object(s)")
    else:
        import lone_echo_import  # noqa: E402  (needs the empty scene first)
        result = lone_echo_import.import_lescatter(
            args.manifest, bpy.context,
            {"lod_level": args.lod, "max_instances": args.max_instances})
        print(f"[render] imported: meshes={result.get('meshes_built')} "
              f"instances={result.get('instances_placed')} "
              f"materials={result.get('materials')}")

    if args.evr_lighting and not args.fbx:
        try:
            from lone_echo_import import evr_lighting  # noqa: E402
        except Exception as exc:
            print(f"[render] evr_lighting unavailable: {exc}")
            evr_lighting = None
        if evr_lighting is not None:
            doc = evr_lighting.load(args.manifest)
            if doc is None:
                print("[render] no lightmaps.json beside the manifest -- "
                      "run scripts/evr_apply_lighting.py first")
            else:
                counts = evr_lighting.summarize(doc)
                lit = evr_lighting.import_lights(
                    doc, bpy.context, y_up_to_z_up=True,
                    dynamic_only=args.dynamic_lights_only)
                print(f"[render] evr lights: {lit.get('created')} created, "
                      f"{lit.get('skipped_static', 0)} static-bake skipped "
                      f"(atlases={counts.get('atlases')} "
                      f"bound_meshes={counts.get('bound_meshes')} "
                      f"bound_instances={counts.get('bound_instances')})")
                by_mesh, by_inst = {}, {}
                for obj in bpy.data.objects:
                    i = obj.get("le_mesh_index")
                    if i is not None:
                        by_mesh.setdefault(int(i), []).append(obj)
                    i = obj.get("le_instance_index")
                    if i is not None:
                        by_inst.setdefault(int(i), []).append(obj)
                wired = 0
                if counts.get("bound_instances"):
                    r = evr_lighting.wire_instance_lightmaps(
                        doc, args.manifest, by_inst,
                        intensity=args.lightmap_intensity, y_up_to_z_up=True,
                        occlusion=args.lightmap_occlusion)
                    wired += r.get("wired", 0)
                    print(f"[render] instance lightmaps: {r}")
                if counts.get("bound_meshes"):
                    # per-MESH wiring has no axis argument (the UVs are already
                    # in the mesh); only the per-INSTANCE path needs y_up_to_z_up
                    r = evr_lighting.wire_lightmaps(
                        doc, args.manifest, by_mesh,
                        intensity=args.lightmap_intensity)
                    wired += r.get("wired", 0)
                print(f"[render] lightmaps wired onto {wired} object(s)")
                if args.light_scale != 1.0:
                    n = 0
                    for lo in bpy.data.objects:
                        if lo.type == "LIGHT":
                            lo.data.energy *= args.light_scale
                            n += 1
                    print(f"[render] scaled {n} light(s) by {args.light_scale}")

    if args.vertex_tints and not args.fbx:
        try:
            from lone_echo_import import evr_vertex_tints  # noqa: E402
        except Exception as exc:
            print(f"[render] evr_vertex_tints unavailable: {exc}")
            evr_vertex_tints = None
        if evr_vertex_tints is not None:
            doc = evr_vertex_tints.load(args.manifest)
            if doc is None:
                print("[render] no vertex_tints.json beside the manifest")
            else:
                specs = {}
                try:
                    root = Path(args.manifest)
                    root = root.parent if root.is_file() else root
                    raw = json.loads((root / "materials.json").read_text(encoding="utf-8"))
                    for entry in raw.get("materials") or ():
                        specs[entry.get("matidx")] = entry.get("spec") or {}
                except (OSError, ValueError, AttributeError):
                    specs = {}
                by_mesh = {}
                for obj in bpy.data.objects:
                    i = obj.get("le_mesh_index")
                    if i is not None:
                        by_mesh.setdefault(int(i), []).append(obj)
                r = evr_vertex_tints.apply_tints(doc, args.manifest, by_mesh, specs)
                print(f"[render] vertex tints: {r.get('applied')} object(s) tinted, "
                      f"{r.get('variants', 0)} variant(s), "
                      f"{r.get('skipped_has_albedo', 0)} left alone (own albedo)")

    if args.near:
        x, y, z, r = (float(v) for v in args.near.split(","))
        anchor = mathutils.Vector((x, y, z))
        kept = 0
        for ob in list(bpy.data.objects):
            if ob.type != "MESH":
                continue
            if (ob.matrix_world.translation - anchor).length <= r:
                kept += 1
            else:
                bpy.data.objects.remove(ob, do_unlink=True)
        print(f"[render] --near {args.near}: kept {kept} object(s)")

    if args.exclude_larger_than:
        removed = []
        for ob in list(bpy.data.objects):
            if ob.type != "MESH":
                continue
            ws = [ob.matrix_world @ mathutils.Vector(c) for c in ob.bound_box]
            diag = max((a - b).length for a in ws for b in ws)
            if diag > args.exclude_larger_than:
                removed.append((round(diag, 1), ob.name))
                bpy.data.objects.remove(ob, do_unlink=True)
        print(f"[render] excluded {len(removed)} object(s) larger than "
              f"{args.exclude_larger_than}m: {removed[:6]}")

    if args.focus:
        kept = keep_only(args.focus)
        print(f"[render] focus {args.focus!r}: kept {kept} object(s)")
        if not kept:
            print("[render] ERROR nothing matched --focus")
            return 2

    scaled = downscale_images(args.max_texture)
    print(f"[render] downscaled {scaled} image(s) to <= {args.max_texture}px")

    targets = [ob for ob in bpy.data.objects if ob.type == "MESH"]
    centre, radius = scene_bounds(targets)
    if centre is None:
        print("[render] ERROR no mesh geometry to frame")
        return 2
    distance = args.distance or radius * (0.05 if args.inside else 2.6)

    import math
    az = math.radians(args.angle)
    el = math.radians(args.elevation)
    eye = centre + mathutils.Vector((
        math.cos(el) * math.cos(az) * distance,
        math.cos(el) * math.sin(az) * distance,
        math.sin(el) * distance))

    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    bpy.context.scene.collection.objects.link(cam)
    if args.cam_loc:
        cam.location = tuple(float(v) for v in args.cam_loc.split(","))
        if args.look_at:
            target = mathutils.Vector(
                tuple(float(v) for v in args.look_at.split(",")))
            cam.rotation_euler = (target - cam.location).to_track_quat(
                "-Z", "Y").to_euler()
        elif args.cam_rot:
            cam.rotation_mode = "XYZ"
            cam.rotation_euler = tuple(
                math.radians(float(v)) for v in args.cam_rot.split(","))
        else:
            cam.rotation_euler = (centre - cam.location).to_track_quat(
                "-Z", "Y").to_euler()
        print(f"[render] explicit camera loc={tuple(round(v,3) for v in cam.location)} "
              f"rot_deg={tuple(round(math.degrees(v),3) for v in cam.rotation_euler)}")
    else:
        cam.location = eye
        cam.rotation_euler = (centre - eye).to_track_quat("-Z", "Y").to_euler()
    if args.lens:
        cam_data.lens = args.lens
    bpy.context.scene.camera = cam

    # `flat`: even, shadowless -- the question is "which texture", not "how lit".
    # `game`:  near-black world so the imported lights and the level's own
    #          emissive materials are what light the frame, which is how the
    #          real level reads. A white world at 1.4 washes both out entirely.
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    game = args.look == "game"
    strength = (args.world_strength if args.world_strength is not None
                else (0.02 if game else 1.4))
    try:
        bg = world.node_tree.nodes["Background"]
        bg.inputs[0].default_value = ((0.01, 0.012, 0.02, 1) if game
                                      else (1, 1, 1, 1))
        bg.inputs[1].default_value = strength
    except Exception:
        pass
    bpy.context.scene.world = world

    scene = bpy.context.scene
    scene.render.engine = args.engine
    try:
        scene.eevee.taa_render_samples = args.samples
    except Exception:
        pass
    scene.render.resolution_x = args.res
    scene.render.resolution_y = int(args.res * 0.62)
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = "PNG"
    if args.exposure:
        scene.view_settings.exposure = args.exposure
    if args.view_transform:
        try:
            scene.view_settings.view_transform = args.view_transform
        except Exception as exc:
            print(f"[render] view transform {args.view_transform!r} rejected: {exc}")
    if game:
        # EEVEE Next dropped the built-in bloom toggle; the equivalent is a
        # compositor Glare pass, which is also what the game's post chain does.
        scene.use_nodes = True
        # Blender 5.0 replaced `scene.node_tree` with `compositing_node_group`
        # (a real node group you create and assign). Support both so this runs
        # on 4.x and 5.x.
        nt = getattr(scene, "node_tree", None)
        if nt is None:
            nt = scene.compositing_node_group
            if nt is None:
                nt = bpy.data.node_groups.new("comp", "CompositorNodeTree")
                scene.compositing_node_group = nt
        for n in list(nt.nodes):
            nt.nodes.remove(n)
        rl = nt.nodes.new("CompositorNodeRLayers")
        glare = nt.nodes.new("CompositorNodeGlare")

        def _set(name, value):
            """Glare moved its settings from properties (4.x) to input sockets
            (5.0); try both and stay silent when a build has neither."""
            sock = glare.inputs.get(name)
            if sock is not None:
                try:
                    sock.default_value = value
                    return True
                except Exception:
                    return False
            attr = name.lower().replace(" ", "_")
            if hasattr(glare, attr):
                try:
                    setattr(glare, attr, value)
                    return True
                except Exception:
                    return False
            return False

        # 5.0's Type is a MENU socket whose values are title-case display
        # names ('Bloom', 'Fog Glow', ...), not the 4.x enum identifiers
        # ('BLOOM', 'FOG_GLOW'). Try both spellings, else it silently stays on
        # the default 'Streaks' and every light becomes a star.
        if not any(_set("Type", v) for v in ("Bloom", "BLOOM", "Fog Glow", "FOG_GLOW")):
            for alt in ("BLOOM", "FOG_GLOW"):
                if hasattr(glare, "glare_type"):
                    try:
                        glare.glare_type = alt
                        break
                    except Exception:
                        continue
        _set("Threshold", args.glare_threshold)
        _set("Size", 8.0)
        _set("Strength", 0.35)
        # 4.x ends the chain in a Composite node; 5.0's compositing NODE GROUP
        # ends in a Group Output whose socket must be declared on the interface.
        try:
            comp = nt.nodes.new("CompositorNodeComposite")
            sink = comp.inputs["Image"]
        except Exception:
            nt.interface.new_socket("Image", in_out="OUTPUT",
                                    socket_type="NodeSocketColor")
            comp = nt.nodes.new("NodeGroupOutput")
            sink = comp.inputs[0]
        nt.links.new(rl.outputs["Image"], glare.inputs["Image"])
        nt.links.new(glare.outputs["Image"], sink)
        _gt = getattr(glare, "glare_type", None)
        if _gt is None:
            _sock = glare.inputs.get("Type")
            _gt = getattr(_sock, "default_value", "?") if _sock else "?"
        print(f"[render] game look: world={strength} glare={_gt}")
    scene.render.filepath = str(Path(args.out).with_suffix(""))

    print(f"[render] {len(targets)} mesh object(s), centre={tuple(round(v,2) for v in centre)} "
          f"radius={radius:.2f} dist={distance:.2f}")
    bpy.ops.render.render(write_still=True)
    print(f"[render] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
