"""D7 picture: lightmap sampled through `uv1` (today) vs the resolved slot-4 set.

    "$BLENDER" --background --factory-startup \
      --python blender_tool/tests/blender_d7_uv_slot_render.py

Subject: `obj002_f98bbf8b2761ffad` (144 verts, stride 56) from mesh-list
`37670868d7884949`, archive `0703fd2acd5803e9` — one of the FOUR corpus objects
whose texcoord slots are `(0, 1, 4)`, so appearance order says `uv1` and the
engine (`vb_texcoord4`, texcoord slot 4) says `uv2`.

Control: `obj006_3206b5cb4e4b1f8f` (1076 verts, stride 44) from the SAME
mesh-list, slots `(0, 4)` — there `uv1` IS slot 4 and the fix is a no-op.

⚠ WHAT THESE PICTURES CAN AND CANNOT SHOW
  * All four affected objects carry `lightmapindex == 0xffffffff`, i.e. they have
    NO shipped bake, and their slot-4 UV set is all-zero on disk. So these frames
    are NOT "the shipped lightmap rendered wrong vs right" — they show WHICH
    TEXELS each UV set fetches, which is the thing the slot picks.
  * The atlas used is `0178fa39b1b95d2f` (station_front, archive
    `942c829457a04a62`) — a stand-in, not this object's bake.
  * `d7_*_chart_*` uses a synthetic high-contrast chart image instead of the HDR
    atlas, purely for legibility: the real atlas is dark and the structural
    difference is hard to see on a PNG.

Every render forces `view_settings.view_transform = 'Standard'` (never AgX).
"""

import json
import os
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

from le_mesh import lightmap as LM                        # noqa: E402
from le_mesh import package as LEPKG                      # noqa: E402
from le_mesh import vertex_format as VF                   # noqa: E402
from lone_echo_import import package_reader, mesh_builder # noqa: E402
from lone_echo_import import lightmap_builder as LB       # noqa: E402
from render_engine_util import resolve_render_engine      # noqa: E402

VERIFY = BLENDER_TOOL / "fixtures" / "verify"
VERIFY.mkdir(parents=True, exist_ok=True)
TMP = Path(os.environ.get("TEMP", "/tmp")) / "le_d7_uv_slot"
TMP.mkdir(parents=True, exist_ok=True)

PKG = BLENDER_TOOL / "exports" / "0703fd2acd5803e9_37670868d7884949.lemesh"
STATION = (BLENDER_TOOL / "exports" / "station_lm"
           / "942c829457a04a62_942c829457a04a62.lemesh")
REAL = BLENDER_TOOL / "exports" / "lightmap_probe"
REAL_LM = REAL / "0178fa39b1b95d2f.dds"
CACHE = REAL / "_lmslices"
ATLAS_PAGE = 3

SUBJECT = "obj002_f98bbf8b2761ffad"
#: control: a REAL lightmapped `(0, 4)` object — station_front, archive
#: `942c829457a04a62` — where the resolved name IS `uv1` and the fix is a no-op.
#: 1050 verts, extent 9.97 x 8.19 x 12.59 — the one station_lm object that is a
#: solid volume rather than a long thin plate, so it does not render edge-on.
CONTROL = "obj002_e1279d85ec1a5d13"
RES = (900, 700)

_log = []


def say(tag, msg):
    line = f"[{tag}] {msg}"
    print(line, flush=True)
    _log.append(line)


# ---------------------------------------------------------------------------
# scene plumbing (mirrors tests/blender_lightmap_render.py)
# ---------------------------------------------------------------------------

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
    sc.view_settings.view_transform = "Standard"     # NEVER AgX
    sc.render.engine = eevee()
    w = bpy.data.worlds.new("black")
    w.use_nodes = True
    for n in w.node_tree.nodes:
        if n.type == "BACKGROUND":
            n.inputs["Color"].default_value = (0, 0, 0, 1)
            n.inputs["Strength"].default_value = 0.0
    sc.world = w
    return sc


def pixels_of(img):
    buf = np.empty(img.size[0] * img.size[1] * img.channels, dtype="float32")
    img.pixels.foreach_get(buf)
    return buf.reshape(img.size[1], img.size[0], img.channels)


def render_png(sc, name):
    sc.render.resolution_x, sc.render.resolution_y = RES
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    out = VERIFY / name
    sc.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    im = bpy.data.images.load(str(out), check_existing=False)
    a = pixels_of(im)[:, :, :3].astype("float64")
    bpy.data.images.remove(im)
    lum = a[:, :, 0] * 0.2126 + a[:, :, 1] * 0.7152 + a[:, :, 2] * 0.0722
    body = lum[lum > 1e-5]          # exclude the black background
    stats = {
        "mean_luma": float(lum.mean()),
        "mean_luma_lit": float(body.mean()) if body.size else 0.0,
        "std_luma_lit": float(body.std()) if body.size else 0.0,
        "lit_frac": float((lum > 1e-5).mean()),
        "max": float(a.max()),
        "distinct_colors": int(len(np.unique(
            (a[lum > 1e-5].reshape(-1, 3) * 255).astype("uint8")
            .view("uint8").reshape(-1, 3), axis=0))) if body.size else 0,
    }
    return out, stats


def frame(sc, ob, margin=1.7, tilt=(0.35, -0.25)):
    # ⚠ mesh_builder.py:242 puts the axis conversion on `matrix_basis`, so
    # `matrix_world` / `bound_box` report IDENTITY until the depsgraph updates.
    # Skipping this update renders a plausible-looking pure-black frame.
    bpy.context.view_layer.update()
    co = np.empty(len(ob.data.vertices) * 3, dtype="float64")
    ob.data.vertices.foreach_get("co", co)
    mw = ob.matrix_world
    mn = Vector((1e18,) * 3)
    mx = Vector((-1e18,) * 3)
    for v in co.reshape(-1, 3):
        wc = mw @ Vector(tuple(v))
        mn = Vector(map(min, mn, wc))
        mx = Vector(map(max, mx, wc))
    centre = (mn + mx) * 0.5
    ext = mx - mn
    size = ext.length or 1.0
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    sc.collection.objects.link(cam)
    # Look down the SHORTEST axis: these are long thin plates and a fixed camera
    # direction shows them edge-on, which reads exactly like "the lightmap is
    # black". `tilt` keeps a little perspective; pass (0, 0) for dead-on.
    thin = min(range(3), key=lambda i: ext[i])
    d = Vector((0.0, 0.0, 0.0))
    d[thin] = 1.0
    d[(thin + 1) % 3] = tilt[0]
    d[(thin + 2) % 3] = tilt[1]
    cam.location = centre + d.normalized() * size * margin
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    cam.data.clip_start = max(1e-3, size * 1e-4)
    cam.data.clip_end = size * 100.0 + 1000.0
    sc.camera = cam
    return centre, ext


# ---------------------------------------------------------------------------
# the object
# ---------------------------------------------------------------------------

MANIFEST = json.loads((PKG / "manifest.json").read_text(encoding="utf-8"))
OBJ = {o["name"]: o for o in MANIFEST["objects"]}
STATION_MANIFEST = json.loads((STATION / "manifest.json").read_text(encoding="utf-8"))
STATION_OBJ = {o["name"]: o for o in STATION_MANIFEST["objects"]}


def build(name, opts=None, pkg_dir=None, table=None):
    """`mesh_builder.build_object` — the REAL import path — then add the UV sets
    it does not import.

    `mesh_builder.py:212` iterates `("uv0", "uv1")` only, so on a `(0, 1, 4)`
    object the slot-4 set NEVER REACHES BLENDER. This function reports that and
    then adds `uv2` with mesh_builder's own `flip_v` convention, so the two
    wirings can be compared at all.
    """
    pkg_dir = pkg_dir or PKG
    obj = (table or OBJ)[name]
    pkg = package_reader.Package(pkg_dir)
    o = dict(opts or {})
    o.setdefault("flip_v", True)
    o.setdefault("y_up_to_z_up", True)
    o.setdefault("import_materials", False)
    ob = mesh_builder.build_object(pkg, obj, lambda k: None, o)
    bpy.context.scene.collection.objects.link(ob)
    me = ob.data
    imported = [l.name for l in me.uv_layers]

    resolved = LEPKG.lightmap_uv_for_manifest_object(obj)
    slots = VF.texcoord_slots(obj["raw_vertex_format"])
    say("build", f"{name}: texcoord slots {slots} -> resolved lightmap UV "
                 f"{resolved!r}; mesh_builder imported {imported}")
    if resolved and resolved not in imported:
        say("gap", f"{name}: ⛔ mesh_builder did NOT import {resolved!r} "
                   f"(it iterates ('uv0','uv1') only) — adding it here so the "
                   f"comparison is possible at all")
        flat, comps = pkg.attribute(obj, resolved)
        loop_vidx = [0] * len(me.loops)
        me.loops.foreach_get("vertex_index", loop_vidx)
        layer = me.uv_layers.new(name=resolved)
        uv = [0.0] * (len(me.loops) * 2)
        for li, vi in enumerate(loop_vidx):
            uv[li * 2] = flat[vi * comps]
            uv[li * 2 + 1] = 1.0 - flat[vi * comps + 1] if o["flip_v"] \
                else flat[vi * comps + 1]
        layer.data.foreach_set("uv", uv)
    return ob, obj, resolved


def uv_stats(ob, layer_name):
    lay = ob.data.uv_layers.get(layer_name)
    if lay is None:
        return None
    uv = np.empty(len(ob.data.loops) * 2, dtype="float64")
    lay.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    return {"u": (float(uv[:, 0].min()), float(uv[:, 0].max())),
            "v": (float(uv[:, 1].min()), float(uv[:, 1].max())),
            "distinct": int(len(np.unique(np.round(uv, 6), axis=0)))}


# ---------------------------------------------------------------------------
# materials
# ---------------------------------------------------------------------------

def _atlas_spec(page=ATLAS_PAGE):
    return {"lightmap_index": 1, "slice_index": page, "uv_layer": "uv1",
            "color": {"role": "lightmapid", "hash": "0178fa39b1b95d2f",
                      "file": REAL_LM.name,
                      "colorspace": LM.COLORSPACE_LIGHTMAP,
                      "expected_dxgi": 95, "dxgi": 95, "dxgi_unexpected": False}}


def _extend_all_textures(nt):
    """Clamp-to-edge on every image node.

    The affected object's slot-4 UV set is ALL ZERO, so every loop samples the
    single point (0, 1) — exactly a texture boundary. Under the default REPEAT
    that wrap seam renders as stripe aliasing, which is a sampler artifact and
    not information about the UV sets. EXTEND makes the real fact — "the whole
    surface fetches ONE texel" — render as the flat colour it is.
    """
    for n in nt.nodes:
        if n.type == "TEX_IMAGE":
            n.extension = "EXTEND"


def wire_atlas(ob, uv_layer, tag, page=ATLAS_PAGE):
    """The PRODUCTION path: `lightmap_builder.wire_lightmap`, with only
    `lightmap_uv_layer` differing between the two frames."""
    mat = bpy.data.materials.new(f"lm_{tag}")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    ob.data.materials.clear()
    ob.data.materials.append(mat)
    rep = LB.wire_lightmap(mat, nt, bsdf, _atlas_spec(page),
                           {"pkg_dir": str(REAL), "lightmap_mode": LB.MODE_BAKED,
                            "lightmap_basis": "single",
                            "lightmap_intensity": 1.0,
                            "lightmap_slice_dir": str(CACHE),
                            "lightmap_uv_layer": uv_layer})
    _extend_all_textures(nt)
    say("wire", f"{tag}: wired={rep['wired']} uv_layer={rep['uv_layer']!r} "
                f"image={rep['image']!r} page={page} reason={rep['reason']!r}")
    return rep


def chart_image():
    """A high-contrast 256x256 chart: 8x8 cells, each a distinct saturated hue
    with a per-cell gradient. Legibility diagnostic only — not engine data."""
    p = TMP / "d7_chart.png"
    n, cells = 256, 8
    img = bpy.data.images.new("d7_chart", n, n, alpha=False, float_buffer=False)
    px = np.zeros((n, n, 4), dtype="float32")
    px[:, :, 3] = 1.0
    palette = [(1, 0, 0), (1, .5, 0), (1, 1, 0), (0, 1, 0),
               (0, 1, 1), (0, .4, 1), (.6, 0, 1), (1, 0, .7)]
    step = n // cells
    for cy in range(cells):
        for cx in range(cells):
            r, g, b = palette[(cx + cy) % len(palette)]
            k = 0.35 + 0.65 * ((cx * cells + cy) / (cells * cells - 1))
            blk = px[cy * step:(cy + 1) * step, cx * step:(cx + 1) * step]
            blk[:, :, 0] = r * k
            blk[:, :, 1] = g * k
            blk[:, :, 2] = b * k
            # cell borders — INTERIOR edges only, so the (0, 1) corner texel the
            # all-zero slot-4 set lands on is a plain cell colour and not a
            # 1-pixel white line straddling the boundary.
            if cy:
                blk[0, :, :3] = 1.0
            if cx:
                blk[:, 0, :3] = 1.0
    img.pixels.foreach_set(px.reshape(-1))
    img.filepath_raw = str(p)
    img.file_format = "PNG"
    img.save()
    return img


def wire_chart(ob, uv_layer, img, tag):
    """Minimal, isolated wiring: UVMap -> Image -> Emission -> Output. Only the
    UV Map node's `uv_map` differs between the two frames."""
    mat = bpy.data.materials.new(f"chart_{tag}")
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        if n.type != "OUTPUT_MATERIAL":
            nt.nodes.remove(n)
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    uvn = nt.nodes.new("ShaderNodeUVMap")
    uvn.uv_map = uv_layer
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.image = img
    tex.interpolation = "Closest"
    tex.extension = "EXTEND"       # see `_extend_all_textures`
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 1.0
    nt.links.new(uvn.outputs["UV"], tex.inputs["Vector"])
    nt.links.new(tex.outputs["Color"], em.inputs["Color"])
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    ob.data.materials.clear()
    ob.data.materials.append(mat)
    say("wire", f"{tag}: chart image through UV set {uv_layer!r}")


# ---------------------------------------------------------------------------
# passes
# ---------------------------------------------------------------------------

def pass_chart(name, uv_layer, out_name, label, pkg_dir=None, table=None):
    sc = fresh()
    ob, _obj, _res = build(name, pkg_dir=pkg_dir, table=table)
    st = uv_stats(ob, uv_layer)
    img = chart_image()
    wire_chart(ob, uv_layer, img, out_name)
    frame(sc, ob)
    path, stats = render_png(sc, out_name)
    say("pic", f"{out_name}  [{label}]  uv={uv_layer} uvstats={st}")
    say("pic", f"{out_name}  mean_luma={stats['mean_luma']:.5f} "
               f"mean_luma_lit={stats['mean_luma_lit']:.5f} "
               f"std_lit={stats['std_luma_lit']:.5f} "
               f"lit_frac={stats['lit_frac']:.4f} "
               f"distinct_colours_on_surface={stats['distinct_colors']}")
    return stats, path


def pass_atlas(name, uv_layer, out_name, label, page=ATLAS_PAGE,
               pkg_dir=None, table=None, tilt=(0.35, -0.25), margin=1.7):
    sc = fresh()
    ob, _obj, _res = build(name, pkg_dir=pkg_dir, table=table)
    wire_atlas(ob, uv_layer, out_name, page=page)
    frame(sc, ob, margin=margin, tilt=tilt)
    path, stats = render_png(sc, out_name)
    say("pic", f"{out_name}  [{label}]  uv={uv_layer}")
    say("pic", f"{out_name}  mean_luma={stats['mean_luma']:.6f} "
               f"mean_luma_lit={stats['mean_luma_lit']:.6f} "
               f"std_lit={stats['std_luma_lit']:.6f} "
               f"lit_frac={stats['lit_frac']:.4f} max={stats['max']:.4f} "
               f"distinct_colours_on_surface={stats['distinct_colors']}")
    return stats, path


def pixel_diff(a, b):
    ia = bpy.data.images.load(str(a), check_existing=False)
    ib = bpy.data.images.load(str(b), check_existing=False)
    pa, pb = pixels_of(ia), pixels_of(ib)
    bpy.data.images.remove(ia)
    bpy.data.images.remove(ib)
    if pa.shape != pb.shape:
        return None
    d = np.abs(pa.astype("float64") - pb.astype("float64"))
    return {"max_abs": float(d.max()), "mean_abs": float(d.mean()),
            "differing_px": int((d.max(axis=2) > 1e-6).sum())}


def main():
    say("subject", f"{SUBJECT} from {PKG.name} (archive 0703fd2acd5803e9, "
                   f"mesh-list 37670868d7884949) — texcoord slots (0, 1, 4)")
    say("subject", "⚠ lightmapindex == 0xffffffff on this object: it has NO "
                   "shipped bake, and its slot-4 UV set is all-zero on disk. "
                   "These frames show WHICH TEXELS each UV set fetches, not a "
                   "mis-rendered shipped bake.")

    # legibility trio — synthetic chart
    a, pa = pass_chart(SUBJECT, "uv1", "d7_affected_chart_uv1_WRONG.png",
                       "today: literal 'uv1' == texcoord slot 1 == the "
                       "material's SECOND TEXTURE uv set")
    b, pb = pass_chart(SUBJECT, "uv2", "d7_affected_chart_uv2_slot4.png",
                       "fixed: the resolved slot-4 set")
    z, pz = pass_chart(SUBJECT, "uv0", "d7_affected_chart_uv0_texture.png",
                       "what uv1 REALLY is here: slot 0, the texture UV set")
    say("identical", f"uv0 vs uv1 frames: {pixel_diff(pz, pa)}  "
                     f"(uv0 and uv1 blobs are byte-identical on all four "
                     f"affected objects — `export-validated`)")

    # production pair — the real (stand-in) HDR atlas through wire_lightmap
    c, pc = pass_atlas(SUBJECT, "uv1", "d7_affected_atlas_uv1_WRONG.png",
                       "today, wire_lightmap + real atlas page 3")
    d, pd = pass_atlas(SUBJECT, "uv2", "d7_affected_atlas_uv2_slot4.png",
                       "fixed, wire_lightmap + real atlas page 3")

    # control — a REAL lightmapped (0,4) object: `uv1` IS slot 4 there
    cobj = STATION_OBJ[CONTROL]
    cpage = int(cobj["lm_slice_index"])
    say("control", f"{CONTROL} from {STATION.name} (archive 942c829457a04a62) "
                   f"slots {VF.texcoord_slots(cobj['raw_vertex_format'])} "
                   f"-> resolved {LEPKG.lightmap_uv_for_manifest_object(cobj)!r}"
                   f", lightmapindex={cobj['lightmap_index']} page={cpage}")
    e, pe = pass_atlas(CONTROL, "uv1", "d7_control_station_uv1_is_slot4.png",
                       "control: slots (0,4), resolved == 'uv1' -> the fix is a "
                       "no-op on the 119/123 majority", page=cpage,
                       pkg_dir=STATION, table=STATION_OBJ, margin=1.3)

    say("verdict", f"chart pair  mean_luma {a['mean_luma']:.5f} (uv1, wrong) vs "
                   f"{b['mean_luma']:.5f} (uv2, slot 4); surface std "
                   f"{a['std_luma_lit']:.5f} vs {b['std_luma_lit']:.5f}; "
                   f"distinct surface colours {a['distinct_colors']} vs "
                   f"{b['distinct_colors']}")
    say("verdict", f"atlas pair  mean_luma {c['mean_luma']:.6f} (uv1, wrong) vs "
                   f"{d['mean_luma']:.6f} (uv2, slot 4); surface std "
                   f"{c['std_luma_lit']:.6f} vs {d['std_luma_lit']:.6f}; "
                   f"distinct surface colours {c['distinct_colors']} vs "
                   f"{d['distinct_colors']}")
    say("verdict", f"control     mean_luma {e['mean_luma']:.6f} "
                   f"(station_front, unchanged by the fix)")

    (VERIFY / "d7_uv_slot_render.log").write_text("\n".join(_log) + "\n",
                                                  encoding="utf-8")
    print("\n".join(_log))


main()
