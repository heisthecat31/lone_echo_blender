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
        --manifest J:/EchoVRModels/scenes/<hash>/manifest.json \
        --out J:/TMP/shot.png --lod 0 --max-instances 200

    # one object, framed automatically
    blender -b -noaudio --python scripts/evr_render.py -- \
        --manifest .../manifest.json --out shot.png --focus _i1045
"""

from __future__ import annotations

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
    cam.location = eye
    cam.rotation_euler = (centre - eye).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = cam

    # Even, shadowless lighting: the question is "which texture", not "how lit".
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    try:
        bg = world.node_tree.nodes["Background"]
        bg.inputs[0].default_value = (1, 1, 1, 1)
        bg.inputs[1].default_value = 1.4
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
    scene.render.filepath = str(Path(args.out).with_suffix(""))

    print(f"[render] {len(targets)} mesh object(s), centre={tuple(round(v,2) for v in centre)} "
          f"radius={radius:.2f} dist={distance:.2f}")
    bpy.ops.render.render(write_still=True)
    print(f"[render] wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
