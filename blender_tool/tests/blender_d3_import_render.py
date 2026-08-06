"""D3 — the baked lightmap through the REAL import path, in pictures.

    blender.exe --background --factory-startup --python <ABS WINDOWS PATH>\\blender_d3_import_render.py

NOT named `test_*` on purpose: `tests/run_tests.py` imports every `test_*.py`
under plain `python3`, and this file needs `bpy`.

★ What is new here versus `blender_lightmap_render.py` (A11)
------------------------------------------------------------
A11's `mesh_picture` imported the package with `import_materials=False`, threw
the materials away and called `wire_lightmap` itself.  Those pictures prove the
NODE GRAPH.  They cannot prove the IMPORT, because at the time nothing in the
import path called `wire_lightmap` at all.

Every render below goes through `lone_echo_import.import_lemesh(pkg, ctx, opts)`
with nothing but option keys — the same call `IMPORT_OT_lemesh.execute` makes.
The material a face ends up with, the page it samples and the colour space it
loads in are all decided by the importer, not by this file.

    exports/station_lm/942c829457a04a62_942c829457a04a62.lemesh
        obj000 page 3 | obj001 page 3 | obj002 page 6 | obj003 page 10
        obj001 and obj002 SHARE material key ae4aa9ff9320fcb1__6eac75dad7fc016d
        while sitting on DIFFERENT pages -- the case that forces the
        per-(material, page) variant
    exports/lightmap_probe/0178fa39b1b95d2f.dds
        DXGI 95 BC6H_UF16 1024^2 arraySize 65 = 13 pages x 5 SG lobes

⚠ What these pictures can and cannot show (unchanged from A11 §9.5/§9.6, and
not re-litigated here):
  * SG5 vs single-lobe CANNOT be separated by a flat-normal picture — the five
    lobe weights collapse to constants, so SG5 is a fixed weighted average of
    five co-located lobes and differs from lobe 0 only in LEVEL, never in
    structure.  The SG5 case rests on `shader-confirmed` shader math plus A9's
    2.39e-05 numeric match.
  * HDR does NOT reach the film on this mesh set.  The atlas pages carry values
    to 3.29, but none of those texels lie under these four objects' charts.
  * obj000 and obj003 are thin rails with almost no screen-facing surface and
    yield no usable mesh picture at any page.  They are imported (they prove the
    JOIN, in the summary numbers) but they are not the pictures.

Every render forces `view_settings.view_transform = 'Standard'`.
"""

import sys
from pathlib import Path

import bpy   # type: ignore
import numpy as np
from mathutils import Vector   # type: ignore

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import lone_echo_import                                   # noqa: E402
from lone_echo_import import lightmap_builder as LB       # noqa: E402
from render_engine_util import resolve_render_engine      # noqa: E402

VERIFY = BLENDER_TOOL / "fixtures" / "verify"
VERIFY.mkdir(parents=True, exist_ok=True)

PKG = BLENDER_TOOL / "exports" / "station_lm" / "942c829457a04a62_942c829457a04a62.lemesh"
ATLAS_DIR = BLENDER_TOOL / "exports" / "lightmap_probe"
ATLAS = ATLAS_DIR / "0178fa39b1b95d2f.dds"

OBJ001 = "obj001_294372d551facd97"      # two I-beams, page 3  -- 352 tris
OBJ002 = "obj002_e1279d85ec1a5d13"      # a bent strut, page 6 -- 1464 tris
SHARED_KEY = "ae4aa9ff9320fcb1__6eac75dad7fc016d"

_results = []


def say(tag, msg):
    line = f"[{tag}] {msg}"
    print(line)
    _results.append(line)


# =============================================================================
# scene plumbing
# =============================================================================

def eevee():
    ids = [i.identifier for i in
           bpy.context.scene.render.bl_rna.properties["engine"].enum_items]
    try:
        return resolve_render_engine("eevee", ids)
    except ValueError:
        return "BLENDER_WORKBENCH"


def fresh():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.view_settings.view_transform = "Standard"     # NEVER leave this at AgX
    sc.render.engine = eevee()
    w = bpy.data.worlds.new("black")
    w.use_nodes = True
    for n in w.node_tree.nodes:
        if n.type == "BACKGROUND":
            n.inputs["Color"].default_value = (0, 0, 0, 1)
            n.inputs["Strength"].default_value = 0.0
    sc.world = w
    return sc


def add_sun(strength=5.0):
    d = bpy.data.lights.new("sun", "SUN")
    d.energy = strength
    ob = bpy.data.objects.new("sun", d)
    bpy.context.scene.collection.objects.link(ob)
    ob.rotation_euler = (0.7, 0.2, 0.3)
    return ob


def pixels_of(img):
    buf = np.empty(img.size[0] * img.size[1] * img.channels, dtype="float32")
    img.pixels.foreach_get(buf)
    return buf.reshape(img.size[1], img.size[0], img.channels)


def png_stats(path):
    im = bpy.data.images.load(str(path), check_existing=False)
    a = pixels_of(im)[:, :, :3]
    bpy.data.images.remove(im)
    # Rec.709 luma on the already-`Standard`-transformed film.
    luma = a[:, :, 0] * 0.2126 + a[:, :, 1] * 0.7152 + a[:, :, 2] * 0.0722
    return {"mean": float(a.mean()), "luma": float(luma.mean()),
            "max": float(a.max()), "lit_frac": float((a.max(axis=2) > 0.02).mean())}


def render_png(sc, out_name, res=(960, 720)):
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    out = VERIFY / out_name
    sc.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    return out


def frame(sc, objs, margin=1.6):
    # ⚠ Two traps, both of which produce a plausible-looking all-BLACK frame:
    #   1. `mesh_builder` puts the Y-up->Z-up conversion on `ob.matrix_basis`,
    #      so `matrix_world` reports IDENTITY until the depsgraph updates.
    #   2. `object.bound_box` is stale the same way; read the vertices instead.
    # Blender's default camera `clip_end` is 100 and obj003 is 167 units long.
    bpy.context.view_layer.update()
    mn = Vector((1e18,) * 3)
    mx = Vector((-1e18,) * 3)
    for o in objs:
        mw = o.matrix_world
        co = np.empty(len(o.data.vertices) * 3, dtype="float64")
        o.data.vertices.foreach_get("co", co)
        for v in co.reshape(-1, 3):
            wc = mw @ Vector(tuple(v))
            mn = Vector(map(min, mn, wc))
            mx = Vector(map(max, mx, wc))
    centre = (mn + mx) * 0.5
    ext = mx - mn
    size = ext.length or 1.0
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    sc.collection.objects.link(cam)
    # Look down the SHORTEST axis so the largest face -- the one carrying the
    # bake -- faces the film. These are long thin structural members.
    thin = min(range(3), key=lambda i: ext[i])
    d = Vector((0.0, 0.0, 0.0))
    d[thin] = 1.0
    d[(thin + 1) % 3] = 0.35
    d[(thin + 2) % 3] = -0.25
    d = d.normalized()
    cam.location = centre + d * size * margin
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.clip_start = max(1e-3, size * 1e-4)
    cam.data.clip_end = size * 100.0 + 1000.0
    sc.camera = cam
    bpy.context.view_layer.update()


# =============================================================================
# the import itself -- nothing but option keys
# =============================================================================

def base_opts(**over):
    o = {
        "import_materials": True,
        "flip_v": True,                 # picture-confirmed for uv1, A11 §9.3
        "y_up_to_z_up": True,
        "lightmap_mode": "baked",
        "lightmap_dir": str(ATLAS_DIR),
        "lightmap_basis": "sg5",
        "lightmap_auto_split": True,
        "lightmap_intensity": 1.0,
        "lightmap_use_ao": False,
    }
    o.update(over)
    return o


def do_import(opts):
    return lone_echo_import.import_lemesh(str(PKG), bpy.context, opts)


def picture(out_name, opts, only=(), label="", sun=0.0, res=(960, 720),
            expect_black=False):
    """One render, start to finish, THROUGH `import_lemesh`.

    `only` names the objects to frame and render; the rest of the package is
    still imported (so the summary reports the whole join) but hidden from the
    film, because obj000/obj003 are hairline rails that just add noise.
    """
    sc = fresh()
    summary = do_import(opts)
    objs, shown = [], []
    for ob in list(bpy.data.objects):
        if ob.type != "MESH":
            continue
        objs.append(ob)
        base = ob.name.split(".")[0]
        if only and base not in only:
            ob.hide_render = True
        else:
            shown.append(ob)
    if not shown:
        shown = objs
    if sun:
        add_sun(sun)
    frame(sc, shown)
    p = render_png(sc, out_name, res)
    st = png_stats(p)
    lm = summary.get("lightmap") or {}
    pages = {ob.name.split(".")[0]: ob.get("le_lightmap_page", "-") for ob in objs}
    say("render",
        f"{out_name}: {label} | mode={lm.get('mode')} available={lm.get('available')} "
        f"wired={lm.get('objects_wired')}/{summary['objects']} variants={lm.get('variants')} "
        f"pages={lm.get('pages')} per-object={pages} sun={sun} "
        f"MEAN LUMA {st['luma']:.5f} (mean rgb {st['mean']:.5f}, max {st['max']:.4f}, "
        f"lit {st['lit_frac']*100:.1f}%)")
    if lm.get("reason"):
        say("render", f"{out_name}: lightmap reason -> {lm['reason']}")
    if st["max"] <= 0.004:
        if expect_black:
            # No lightmap, no lights, black world -> nothing is lighting the
            # surface. That IS the result, not a fault: it is what a `.lemesh`
            # import gave you before this front existed.
            say("render-black-EXPECTED",
                f"{out_name}: frame peak {st['max']:.5f} — expected: no lightmap "
                f"and no lights, so there is nothing to light the surface")
        else:
            say("render-BLACK",
                f"{out_name}: frame peak {st['max']:.5f} — treat as a HARNESS fault "
                f"(framing/clip) until proven otherwise, not as a lightmap result")
    return st, summary


# =============================================================================

def report_graph():
    """What the import actually built, read back off the datablocks."""
    sc = fresh()
    summary = do_import(base_opts())
    lm = summary["lightmap"]
    say("import", f"summary lightmap = {lm}")
    variants = sorted(m.name for m in bpy.data.materials
                      if "le_lightmap_page" in m.keys())
    say("import", f"{len(variants)} (material, page) variants: {variants}")
    # ★ the sharing verdict: one material key, two pages, two datablocks.
    shared = [n for n in variants if n.startswith(SHARED_KEY)]
    say("import",
        f"shared key {SHARED_KEY} -> {shared} "
        f"(pages {[bpy.data.materials[n]['le_lightmap_page'] for n in shared]})")
    for ob in bpy.data.objects:
        if ob.type != "MESH":
            continue
        mats = [m.name if m else None for m in ob.data.materials]
        say("import",
            f"{ob.name.split('.')[0]}: lm_slice_index={ob['le_lm_slice_index']} "
            f"-> le_lightmap_page={ob.get('le_lightmap_page', '-')} "
            f"wired={ob.get('le_lightmap_wired')} materials={mats}")
    # colour space + image set, read back from Blender rather than assumed
    imgs = sorted(bpy.data.images, key=lambda i: i.name)
    for im in imgs:
        if "slice" not in im.name:
            continue
        say("import",
            f"image {im.name}: colorspace={im.colorspace_settings.name!r} "
            f"alpha_mode={im.alpha_mode!r} float={im.is_float} depth={im.depth}")
    bad = [im.name for im in imgs
           if "slice" in im.name and im.colorspace_settings.name != LB.COLORSPACE_LIGHTMAP]
    say("import", f"images NOT in {LB.COLORSPACE_LIGHTMAP!r}: {bad or 'none'}")
    return summary


def main():
    say("env", f"Blender {bpy.app.version_string} "
               f"(hash {bpy.app.build_hash.decode() if isinstance(bpy.app.build_hash, bytes) else bpy.app.build_hash})")
    say("env", f"factory view_transform would be "
               f"{bpy.context.scene.view_settings.view_transform!r}; "
               f"every render below forces 'Standard'")
    if not PKG.is_dir() or not ATLAS.exists():
        say("env", "FATAL — the matched pair is not in this checkout")
        return

    report_graph()

    # ---- the three required deltas, all on obj001 (the only close-range object
    #      with real surface; A11 §9.4 established obj000/obj003 carry no picture)
    none_st, _ = picture("d3_import_lm_none.png", base_opts(lightmap_mode="none"),
                         only=(OBJ001,), label="imported TODAY'S WAY (no lightmap)",
                         expect_black=True)
    baked_st, _ = picture("d3_import_lm_baked.png", base_opts(),
                          only=(OBJ001,), label="the SAME import, lightmap_mode='baked'")
    wrong_st, _ = picture("d3_import_lm_wrongpage.png",
                          base_opts(lightmap_force_page=0), only=(OBJ001,),
                          label="the FAILURE MODE: every mesh forced to page 0")

    # ---- the unlit proof and the ambient double-count, through the importer
    picture("d3_import_lm_none_withsun.png",
            base_opts(lightmap_mode="none"), only=(OBJ001,), sun=5.0,
            label="no lightmap + a 5-unit sun (control)")
    picture("d3_import_lm_baked_withsun.png", base_opts(), only=(OBJ001,), sun=5.0,
            label="baked + the same sun — must be IDENTICAL to baked (unlit)")
    picture("d3_import_lm_ambient_withsun.png",
            base_opts(lightmap_mode="ambient"), only=(OBJ001,), sun=5.0,
            label="ambient + the same sun — the documented double-count")

    # ---- basis, and an exposure aid
    picture("d3_import_lm_baked_single.png", base_opts(lightmap_basis="single"),
            only=(OBJ001,), label="basis='single' (lobe 0 alone) — level, not structure")
    picture("d3_import_lm_baked_x8.png", base_opts(lightmap_intensity=8.0),
            only=(OBJ001,), label="baked, EXPOSURE x8 (documented multiplier)")

    # ---- ★ the sharing verdict in pictures: obj002 shares obj001's material key
    #      but lives on page 6.  Collapsing the shared material onto one page is
    #      exactly what wiring inside `build_material` would have done.
    picture("d3_import_obj002_ownpage6.png", base_opts(), only=(OBJ002,),
            label="obj002 (shares obj001's material key) on its OWN page 6")
    picture("d3_import_obj002_collapsed_to_page3.png",
            base_opts(lightmap_force_page=3), only=(OBJ002,),
            label="obj002 forced to obj001's page 3 — what a SHARED, once-wired "
                  "material would render")
    picture("d3_import_obj002_ownpage6_x8.png", base_opts(lightmap_intensity=8.0),
            only=(OBJ002,), label="obj002 own page 6, EXPOSURE x8")
    picture("d3_import_obj002_collapsed_to_page3_x8.png",
            base_opts(lightmap_force_page=3, lightmap_intensity=8.0), only=(OBJ002,),
            label="obj002 collapsed to page 3, EXPOSURE x8")

    # ---- the whole package, for the record (dominated by obj003; A11 §9.8)
    picture("d3_import_all_baked.png", base_opts(),
            label="all four objects, each on its own page (obj003 dominates the frame)")

    say("verdict",
        f"none {none_st['luma']:.5f} -> baked {baked_st['luma']:.5f} "
        f"(x{baked_st['luma'] / max(none_st['luma'], 1e-9):.3f}); "
        f"wrong page {wrong_st['luma']:.5f} "
        f"(x{wrong_st['luma'] / max(baked_st['luma'], 1e-9):.3f} vs own page)")

    print("\n===== SUMMARY =====")
    for line in _results:
        print(line)


main()
