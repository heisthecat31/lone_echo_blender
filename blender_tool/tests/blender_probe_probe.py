"""In-Blender probe + before/after render for the reflection-probe path (R2).

    blender.exe --background --factory-startup --python <ABS WINDOWS PATH>\\blender_probe_probe.py

NOT named `test_*` on purpose: `tests/run_tests.py` imports every `test_*.py`
under plain `python3`, and this file needs `bpy`.

Sections (each prints lines the write-up quotes verbatim):

  1. env      — Blender version, factory view transform
  2. rows     — GROUND TRUTH for `probe_builder.STRIP_IMAGE_IS_FLIPPED`: a
                synthetic BC6H DDS with a known top-to-bottom gradient, loaded
                and read back.  Settles which way up Blender hands DDS rows
                without appealing to any cube convention.
  3. cube     — the shipped cube DDS: what shape Blender exposes, and whether it
                is byte-identical to `cube_strip_bytes`' hand-built strip
  4. seams    — the face/row convention scored by cross-seam mismatch
  5. equirect — resample + a `Standard` PNG of the environment itself
  6. render   — BEFORE/AFTER on a reflective subject, plus the matte control
                (`roughness == 1` must be BIT-IDENTICAL)

Everything writes under `blender_tool/exports/hero/probe_*`.
"""

import math
import os
import struct
import sys
from pathlib import Path

import bpy   # type: ignore

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from le_mesh import lightmap as LM              # noqa: E402  (BC6H DDS fixture writer)
from le_mesh import reflection_probe as RP      # noqa: E402
from lone_echo_import import probe_builder as PB  # noqa: E402

PROBE_DIR = BLENDER_TOOL / "scratch" / "probe"
OUT = BLENDER_TOOL / "exports" / "hero"
OUT.mkdir(parents=True, exist_ok=True)
TMP = BLENDER_TOOL / "scratch" / "probe_tmp"
TMP.mkdir(parents=True, exist_ok=True)

FAILURES = []


def say(tag, msg):
    print(f"[{tag}] {msg}")


def check(tag, ok, msg):
    say(tag, ("OK   " if ok else "FAIL ") + msg)
    if not ok:
        FAILURES.append(f"{tag}: {msg}")
    return ok


# ---------------------------------------------------------------------------
# 1. env
# ---------------------------------------------------------------------------
say("env", f"blender {bpy.app.version_string}")
say("env", f"factory view_transform={bpy.context.scene.view_settings.view_transform!r}")


# ---------------------------------------------------------------------------
# 2. rows — which way up does Blender hand back a DDS?
# ---------------------------------------------------------------------------
# A 16 x 64 BC6H image whose block rows carry a strictly increasing value from
# the file's FIRST row to its LAST.  Whatever Blender reports at pixel row 0
# tells us the buffer's origin, with no cube maths involved.
grad_path = TMP / "row_order_gradient.dds"
NBY = 16                                    # 64 / 4 block rows


def _q(by):
    return LM.bc6h_quantise_for(0.05 + 0.9 * (by / (NBY - 1)))


LM.write_bc6h_dds(grad_path, 16, 64, lambda bx, by: (_q(by),) * 3)
img = bpy.data.images.load(str(grad_path), check_existing=False)
w, h = img.size
px = [0.0] * len(img.pixels)
img.pixels.foreach_get(px)
first_row = px[0]                                     # pixel row 0
last_row = px[((h - 1) * w) * 4]                      # pixel row h-1
want_first = LM.bc6h_uf16_decode_endpoint(_q(0))      # file's FIRST block row
want_last = LM.bc6h_uf16_decode_endpoint(_q(NBY - 1))  # file's LAST block row
say("rows", f"size={w}x{h} pixels[row0]={first_row:.5f} pixels[row{h-1}]={last_row:.5f}")
say("rows", f"file first row value={want_first:.5f} file last row value={want_last:.5f}")
flipped = abs(first_row - want_last) < abs(first_row - want_first)
say("rows", f"=> Blender buffer row 0 == file's {'LAST' if flipped else 'FIRST'} row "
            f"(image_is_flipped={flipped})")
check("rows", flipped == PB.STRIP_IMAGE_IS_FLIPPED,
      f"probe_builder.STRIP_IMAGE_IS_FLIPPED == {PB.STRIP_IMAGE_IS_FLIPPED} matches the measurement")
bpy.data.images.remove(img)


# ---------------------------------------------------------------------------
# 3. cube — what Blender exposes for a shipped cube DDS
# ---------------------------------------------------------------------------
cubes = sorted(p for p in PROBE_DIR.glob("probe_*.dds") if "_strip" not in p.name)
if not cubes:
    say("cube", f"SKIP: no cube DDS under {PROBE_DIR} — run a local working file")
else:
    cube_path = cubes[0]
    src, dim = PB.load_cube_strip(cube_path)
    check("cube", (src.size[0], src.size[1]) == (dim, dim * 6),
          f"{cube_path.name}: Blender exposes {src.size[0]}x{src.size[1]} (face {dim})")
    check("cube", src.is_float, "the BC6H cube loads as a FLOAT image")
    cube_px = [0.0] * len(src.pixels)
    src.pixels.foreach_get(cube_px)
    say("cube", f"colorspace={src.colorspace_settings.name!r} "
                f"max={max(cube_px[0:len(cube_px):4]):.3f}")

    strip_path = cube_path.with_name(cube_path.stem + "_strip.dds")
    if strip_path.is_file():
        s2 = bpy.data.images.load(str(strip_path), check_existing=False)
        p2 = [0.0] * len(s2.pixels)
        s2.pixels.foreach_get(p2)
        same = (len(p2) == len(cube_px)
                and max(abs(a - b) for a, b in zip(p2, cube_px)) == 0.0)
        check("cube", same,
              "Blender's own cube-DDS parse is BIT-IDENTICAL to cube_strip_bytes' "
              "hand-built face-major strip")
        bpy.data.images.remove(s2)

    # ---------------------------------------------------------------------
    # 4. seams
    # ---------------------------------------------------------------------
    pairs = PB._edge_direction_pairs(dim, samples=96)
    say("seams", f"{len(pairs)} cross-seam direction pairs")
    scores = {f: PB.cube_seam_error(cube_px, dim, flipped=f, pairs=pairs)
              for f in (True, False)}
    for f, s in sorted(scores.items(), key=lambda kv: kv[1]):
        say("seams", f"flipped={f}: mean |log| seam mismatch = {s:.4f}")
    best = min(scores, key=scores.get)
    check("seams", best == PB.STRIP_IMAGE_IS_FLIPPED,
          f"the seam scorer independently picks flipped={best}")

    # ---------------------------------------------------------------------
    # 5. equirect
    # ---------------------------------------------------------------------
    ctx = {"files": {0: str(cube_path)}, "colorspace": RP.COLORSPACE_PROBE,
           "equirects": {}, "section": {"probes": []}}
    env = PB.equirect_image_for_probe(ctx, 0, {})
    check("equirect", env is not None and tuple(env.size) == (512, 256),
          f"equirect built: {tuple(env.size) if env else None}")
    stats = ctx.get("equirect_stats", {}).get(0, {})
    say("equirect", f"resampled buffer: mean R={stats.get('mean', 0):.5f} "
                    f"max R={stats.get('max', 0):.3f}")
    epx = [0.0] * len(env.pixels)
    env.pixels.foreach_get(epx)
    n = env.size[0] * env.size[1]
    mean = sum(epx[0:n * 4:4]) / n
    say("equirect", f"datablock read-back: mean R={mean:.5f} max R={max(epx[0:n*4:4]):.3f}")
    check("equirect", mean > 0.0 and abs(mean - stats.get("mean", -1)) < 1e-6,
          "the pixels survived the datablock write")
    env.filepath_raw = str(OUT / "probe_environment.png")
    env.file_format = "PNG"
    bpy.context.scene.view_settings.view_transform = "Standard"
    env.save()
    say("equirect", f"wrote {env.filepath_raw}")


# ---------------------------------------------------------------------------
# 6. render — before / after on a reflective subject + a matte control
# ---------------------------------------------------------------------------
def build_scene():
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 48
    scene.cycles.use_denoising = False
    scene.render.resolution_x = 480
    scene.render.resolution_y = 320
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0

    world = bpy.data.worlds.new("dark")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0, 0, 0, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.0
    scene.world = world

    def sphere(name, x, roughness):
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, location=(x, 0, 0))
        ob = bpy.context.active_object
        ob.name = name
        bpy.ops.object.shade_smooth()
        mat = bpy.data.materials.new(name + "_mat")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes["Principled BSDF"]
        bsdf.inputs["Base Color"].default_value = (0.18, 0.18, 0.18, 1.0)
        bsdf.inputs["Roughness"].default_value = roughness
        if "Metallic" in bsdf.inputs:
            bsdf.inputs["Metallic"].default_value = 0.0
        ob.data.materials.append(mat)
        return ob

    shiny = sphere("shiny", -1.3, 0.05)
    matte = sphere("matte", 1.3, 1.0)

    cam_data = bpy.data.cameras.new("cam")
    cam_data.clip_start = 0.01
    cam_data.clip_end = 1000.0
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0.0, -6.5, 0.0)
    cam.rotation_euler = (math.radians(90), 0, 0)
    scene.camera = cam

    lamp_data = bpy.data.lights.new("key", type="AREA")
    lamp_data.energy = 60.0
    lamp_data.size = 3.0
    lamp = bpy.data.objects.new("key", lamp_data)
    scene.collection.objects.link(lamp)
    lamp.location = (2.5, -3.0, 3.0)
    lamp.rotation_euler = (math.radians(50), 0, math.radians(35))
    bpy.context.view_layer.update()
    return shiny, matte


def render_to(path):
    bpy.context.scene.render.filepath = str(path)
    bpy.context.scene.render.image_settings.file_format = "PNG"
    got = bpy.context.scene.view_settings.view_transform
    if got != "Standard":
        raise SystemExit(f"view_transform did not stick: {got!r}")
    bpy.ops.render.render(write_still=True)
    return Path(path)


def png_stats(path):
    img = bpy.data.images.load(str(path), check_existing=False)
    px = [0.0] * len(img.pixels)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px


def region_mean(px, w, h, x0, x1):
    tot, n = 0.0, 0
    for y in range(h):
        base = y * w * 4
        for x in range(x0, x1):
            i = base + x * 4
            tot += px[i] + px[i + 1] + px[i + 2]
            n += 3
    return tot / max(1, n)


if cubes:
    shiny, matte = build_scene()
    before = render_to(OUT / "probe_before.png")
    say("render", f"wrote {before}")

    opts = {"probe_mode": PB.MODE_SPECULAR, "probe_intensity": 1.0}
    spec = {"index": 0, "mipcount": 9, "cube_file": str(cubes[0]),
            "resolved_file": str(cubes[0])}
    for ob in (shiny, matte):
        mat = ob.material_slots[0].material
        rep = PB.wire_ambient_specular(
            mat, mat.node_tree, mat.node_tree.nodes["Principled BSDF"],
            spec, env, opts)
        say("render", f"{ob.name}: {rep}")
        check("render", rep.get("wired"), f"{ob.name} wired")

    after = render_to(OUT / "probe_after.png")
    say("render", f"wrote {after}")

    W, H = bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y
    b = png_stats(before)
    a = png_stats(after)
    # left half == the shiny sphere, right half == the matte control
    b_shiny = region_mean(b, W, H, 0, W // 2)
    a_shiny = region_mean(a, W, H, 0, W // 2)
    b_matte = region_mean(b, W, H, W // 2, W)
    a_matte = region_mean(a, W, H, W // 2, W)
    say("render", f"shiny half: before={b_shiny:.6f} after={a_shiny:.6f} "
                  f"delta={a_shiny - b_shiny:+.6f}")
    say("render", f"matte half: before={b_matte:.6f} after={a_matte:.6f} "
                  f"delta={a_matte - b_matte:+.6f}")
    # per-pixel: an ADD must never darken beyond the render's own noise
    darker = sum(1 for i in range(0, W * H * 4, 4) if a[i] < b[i] - 0.01)
    brighter = sum(1 for i in range(0, W * H * 4, 4) if a[i] > b[i] + 0.01)
    say("render", f"pixels brighter by >0.01: {brighter}; darker by >0.01: {darker}")
    check("render", a_shiny > b_shiny * 1.02,
          "the roughness=0.05 sphere GAINED ambient specular")
    check("render", abs(a_matte - b_matte) < 1e-3,
          "the roughness=1.0 control is unchanged (the term is specular, not diffuse)")

    # and the unwire path restores the original graph
    for ob in (shiny, matte):
        mat = ob.material_slots[0].material
        PB.unwire(mat.node_tree)
    undone = render_to(OUT / "probe_unwired.png")
    u = png_stats(undone)
    u_shiny = region_mean(u, W, H, 0, W // 2)
    u_matte = region_mean(u, W, H, W // 2, W)
    say("render", f"NOISE FLOOR (identical scene re-rendered): shiny "
                  f"{abs(u_shiny - b_shiny):.6f} ({abs(u_shiny - b_shiny) / b_shiny:.4%}), "
                  f"matte {abs(u_matte - b_matte):.6f} "
                  f"({abs(u_matte - b_matte) / b_matte:.4%})")
    check("render", abs(u_shiny - b_shiny) < 1e-3,
          "unwire() restores the BEFORE image to within the noise floor")

print(f"PROBE_RESULT: {'FAIL' if FAILURES else 'PASS'}")
for f in FAILURES:
    print("  - " + f)
