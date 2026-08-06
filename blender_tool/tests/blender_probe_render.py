"""Package-level before/after render for the reflection-probe path (R2).

Imports a real `.lemesh` package (materials, lightmap section, probe section),
renders it, wires `probe_builder` onto every object that names a probe, renders
again, and reports the measured delta plus a re-render noise floor.

    blender.exe --background --factory-startup --python <ABS>\\blender_probe_render.py -- \
        --package "C:\\...\\exports\\probe_e2e\\a3c5dda68751813c_a3c5dda68751813c.lemesh"

NOT named `test_*`: `tests/run_tests.py` imports every `test_*.py` under plain
`python3` and this one needs `bpy`.
"""

import json
import math
import sys
from pathlib import Path

import bpy   # type: ignore
from mathutils import Matrix, Vector   # type: ignore

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from le_mesh import framing                                  # noqa: E402
from lone_echo_import import import_lemesh                   # noqa: E402
from lone_echo_import import probe_builder as PB             # noqa: E402

OUT = BLENDER_TOOL / "exports" / "hero"
OUT.mkdir(parents=True, exist_ok=True)

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
PKG = Path(argv[argv.index("--package") + 1]) if "--package" in argv else (
    BLENDER_TOOL / "exports" / "probe_e2e" /
    "a3c5dda68751813c_a3c5dda68751813c.lemesh")
INTENSITY = float(argv[argv.index("--intensity") + 1]) if "--intensity" in argv else 1.0

FAILURES = []


def say(tag, msg):
    print(f"[{tag}] {msg}")


def check(tag, ok, msg):
    say(tag, ("OK   " if ok else "FAIL ") + msg)
    if not ok:
        FAILURES.append(f"{tag}: {msg}")
    return ok


# ---------------------------------------------------------------------------
say("env", f"blender {bpy.app.version_string}")
say("env", f"package {PKG}")

scene = bpy.context.scene
scene.render.engine = "CYCLES"
scene.cycles.samples = 256
scene.cycles.use_denoising = False
scene.render.resolution_x = 640
scene.render.resolution_y = 400
scene.view_settings.view_transform = "Standard"
scene.view_settings.look = "None"
scene.view_settings.exposure = 0.0

world = bpy.data.worlds.new("dark")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0, 0, 0, 1)
world.node_tree.nodes["Background"].inputs[1].default_value = 0.0
scene.world = world

opts = {"import_materials": True, "lightmap_mode": "none", "flip_v": True}
summary = import_lemesh(PKG, bpy.context, opts)
say("import", json.dumps({k: v for k, v in summary.items() if k != "lightmap"}))

manifest = json.loads((PKG / "manifest.json").read_text(encoding="utf-8"))
by_index = {o["mesh_index"]: o for o in manifest["objects"]}
ctx = PB.resolve_probe_context(PKG, manifest, opts)
say("probes", f"count={ctx['count']} files={len(ctx['files'])} "
              f"source={ctx['source']} notes={ctx['notes']}")
check("probes", ctx["count"] > 0 and len(ctx["files"]) > 0,
      "the package carries a probe set AND its extracted cubes")

meshes = [ob for ob in bpy.context.scene.objects if ob.type == "MESH"]
say("import", f"{len(meshes)} mesh objects")

# --- frame the whole thing --------------------------------------------------
# ⚠ STANDING RULE: the axis conversion lives on `ob.matrix_basis`, so
# `matrix_world` is STALE until the depsgraph updates.  Framing off a stale
# matrix aims the camera at the pre-rotation bbox and renders a black frame
# (measured: mean 0.000325 with the subject entirely out of shot).
bpy.context.view_layer.update()

# ⚠ A level package spans kilometres (skydome / far props), so fitting the WHOLE
# cloud renders the subject at one pixel — measured: mean 0.000325, a black
# frame.  Frame the single heaviest mesh instead: it is a real subject, it is
# guaranteed to be probe-bound, and the before/after difference is then a
# difference on something visible.
hero = max(meshes, key=lambda o: len(o.data.vertices))
say("frame", f"hero object {hero.name} ({len(hero.data.vertices)} verts, "
             f"probe={hero.get('le_probe_index')})")
pts = [tuple((hero.matrix_world @ Vector(c))[:]) for c in hero.bound_box]
fit = framing.fit_view(pts, eye_dir=framing.orbit_direction(35.0, 18.0),
                       lens=35.0, sensor=36.0,
                       res_x=scene.render.resolution_x,
                       res_y=scene.render.resolution_y, margin=1.06)
cam_data = bpy.data.cameras.new("cam")
cam_data.lens = 35.0
cam_data.sensor_width = 36.0
cam_data.clip_start = max(0.01, fit["clip_start"])
cam_data.clip_end = fit["clip_end"]
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
right, up, back = fit["basis"]
rot = Matrix(((right[0], up[0], back[0]),
              (right[1], up[1], back[1]),
              (right[2], up[2], back[2]))).to_4x4()
cam.matrix_world = Matrix.Translation(Vector(fit["location"])) @ rot
scene.camera = cam

# a key light aligned with the camera so the BEFORE frame is not black, plus a
# rim from the far side.  Both are ordinary Blender lights: nothing here claims
# to be the game's lighting, it is a controlled stage for an A/B.
for name, rot, energy in (("key", (math.radians(72), 0.0, math.radians(35)), 5.0),
                          ("rim", (math.radians(115), 0.0, math.radians(215)), 3.0)):
    sun_data = bpy.data.lights.new(name, type="SUN")
    sun_data.energy = energy
    sun = bpy.data.objects.new(name, sun_data)
    scene.collection.objects.link(sun)
    sun.rotation_euler = rot
bpy.context.view_layer.update()
say("frame", f"eye={tuple(round(v,2) for v in fit['location'])} "
             f"clip {fit['clip_start']:.2f}..{fit['clip_end']:.1f}")


def render(path):
    scene.render.filepath = str(path)
    scene.render.image_settings.file_format = "PNG"
    got = scene.view_settings.view_transform
    if got != "Standard":
        raise SystemExit(f"view_transform did not stick: {got!r}")
    bpy.ops.render.render(write_still=True)
    img = bpy.data.images.load(str(path), check_existing=False)
    px = [0.0] * len(img.pixels)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    n = scene.render.resolution_x * scene.render.resolution_y
    mean = sum(px[0:n * 4:4] + px[1:n * 4:4] + px[2:n * 4:4]) / (3 * n)
    return px, mean


before_px, before_mean = render(OUT / "probe_level_before.png")
say("render", f"BEFORE mean={before_mean:.6f}")
check("render", before_mean > 1e-3,
      "the BEFORE frame actually contains a lit subject "
      "(a black stage makes the A/B meaningless)")
noise_px, noise_mean = render(OUT / "probe_level_noise.png")
floor = abs(noise_mean - before_mean)
say("render", f"NOISE FLOOR (identical scene re-rendered) = {floor:.6f} "
              f"({floor / max(before_mean, 1e-9):.4%})")

opts_on = dict(opts)
opts_on["probe_mode"] = PB.MODE_SPECULAR
opts_on["probe_intensity"] = INTENSITY
wired = skipped = 0
probes_seen = set()
for ob in meshes:
    mi = ob.get("le_mesh_index")
    entry = by_index.get(int(mi)) if mi is not None else None
    rep = PB.wire_object(ob, ctx, entry, opts_on)
    if rep.get("wired"):
        wired += rep["wired"]
        probes_seen.add(rep["probe"])
    else:
        skipped += 1
say("wire", f"{wired} material slots wired over {len(meshes)} objects; "
            f"{skipped} objects skipped; probes used {sorted(probes_seen)}")
check("wire", wired > 0, "at least one material was wired")

after_px, after_mean = render(OUT / "probe_level_after.png")
delta = after_mean - before_mean
say("render", f"AFTER  mean={after_mean:.6f}  delta={delta:+.6f} "
              f"({delta / max(before_mean, 1e-9):+.4%})")
check("render", delta > floor * 3,
      f"the ambient-specular term moved the image well past the noise floor "
      f"({delta:.6f} vs {floor:.6f})")

n = scene.render.resolution_x * scene.render.resolution_y
darker = sum(1 for i in range(0, n * 4, 4) if after_px[i] < before_px[i] - 0.02)
brighter = sum(1 for i in range(0, n * 4, 4) if after_px[i] > before_px[i] + 0.02)
say("render", f"pixels brighter by >0.02: {brighter}; darker by >0.02: {darker}")
# ⚠ DIAGNOSTIC, not a pass/fail.  The identical-scene noise floor bounds a
# re-render of the SAME node graph; it does not bound the difference between two
# DIFFERENT graphs.  Adding a closure changes which closure Cycles follows per
# path, so individual pixels move both ways even though the estimator is
# unbiased and the MEAN can only rise.  The bit-exact "matte control is
# unchanged" proof lives in `blender_probe_probe.py`, where the weight is
# analytically zero.
check("render", brighter > 0, "the term reaches real pixels, not just the mean")

print(f"PROBE_RESULT: {'FAIL' if FAILURES else 'PASS'}")
for f in FAILURES:
    print("  - " + f)
