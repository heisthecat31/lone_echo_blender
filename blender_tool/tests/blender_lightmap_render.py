"""PICTORIAL verification of the baked-lightmap path on MATCHED bytes.

    blender.exe --background --factory-startup --python <ABS WINDOWS PATH>\\blender_lightmap_render.py

NOT named `test_*` on purpose: `tests/run_tests.py` imports every `test_*.py`
under plain `python3`, and this file needs `bpy`.

★ What is new here versus `blender_lightmap_probe.py`
----------------------------------------------------------
That probe verified the colour space, the 13x5 page model, the DX10 array split and the
SG5 sum **numerically**, but every *picture* it made was a bridge-archive mesh
sampled through the station_front atlas — wiring, not artwork
- no shipped mesh package had a `uv1` into the extracted atlas.  This file runs
on the matched pair:

    exports/station_lm/942c829457a04a62_942c829457a04a62.lemesh
        obj000 page 3 | obj001 page 3 | obj002 page 6 | obj003 page 10
        all four `lightmap_index == 1`, all four carry a real `uv1`
    exports/lightmap_probe/0178fa39b1b95d2f.dds
        the row-1 colour map those four index: DXGI 95 BC6H_UF16 1024^2 arr 65

so the mesh's `uv1` and the atlas are from the *same bake*.

Sections
--------
  1. env          — Blender build, the package, the pages
  2. registration — the numeric core.  For every object x {flip_v on, flip_v off,
                    N random v-offset controls} x {own page, wrong pages}:
                    rasterise the uv1 footprint into the 1024^2 atlas and score
                    how well the atlas's own chart structure lines up with the
                    island outlines.  The random controls are the null model —
                    without them "flip beats no-flip" could be luck.
  3. atlas pics   — the atlas page with the mesh's uv1 island OUTLINES drawn on
                    top, flip on vs flip off, full page and zoomed crop.
                    This is the picture that decides `flip_v`.
  4. mesh pics    — the four real objects rendered with `wire_lightmap`'s SG5
                    path: own page vs a deliberately wrong page, SG5 vs single,
                    flip on vs off.
  5. hdr          — does anything on THIS mesh exceed 1.0 on the film?

Every render forces `view_settings.view_transform = 'Standard'`.  Blender 4.0+
defaults to AgX, which would desaturate the highlights and mislead the eye.
"""

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
from lone_echo_import import lightmap_builder as LB       # noqa: E402
import lone_echo_import                                   # noqa: E402
from render_engine_util import resolve_render_engine      # noqa: E402

# Renders go under `exports/` (gitignored): they are game-derived imagery and
# must never land in a tracked directory.
VERIFY = BLENDER_TOOL / "exports" / "lightmap_renders"
VERIFY.mkdir(parents=True, exist_ok=True)
TMP = Path(os.environ.get("TEMP", "/tmp")) / "le_lightmap_render"
TMP.mkdir(parents=True, exist_ok=True)

#: ★ the matched pair
PKG = BLENDER_TOOL / "exports" / "station_lm" / "942c829457a04a62_942c829457a04a62.lemesh"
REAL = BLENDER_TOOL / "exports" / "lightmap_probe"
REAL_LM = REAL / "0178fa39b1b95d2f.dds"
CACHE = REAL / "_lmslices"

ATLAS = 1024
#: pages actually referenced by the package, plus page 0 as the "wrong page"
#: control (it is what a broken importer renders for every mesh).
PAGES = (0, 3, 6, 10)
WRONG_PAGE = 0

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


def pixels_of(img):
    buf = np.empty(img.size[0] * img.size[1] * img.channels, dtype="float32")
    img.pixels.foreach_get(buf)
    return buf.reshape(img.size[1], img.size[0], img.channels)


def render_png(sc, out_name, res):
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = "PNG"
    sc.render.film_transparent = False
    out = VERIFY / out_name
    sc.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    return out


def render_exr(sc, res, name="hdr"):
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.image_settings.file_format = "OPEN_EXR"
    sc.render.image_settings.color_depth = "32"
    out = TMP / name
    sc.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    f = out.with_suffix(".exr")
    im = bpy.data.images.load(str(f), check_existing=False)
    a = pixels_of(im)
    bpy.data.images.remove(im)
    os.remove(f)
    return a


def png_stats(path):
    im = bpy.data.images.load(str(path), check_existing=False)
    a = pixels_of(im)[:, :, :3]
    bpy.data.images.remove(im)
    return {"mean": float(a.mean()), "max": float(a.max()),
            "lit_frac": float((a.max(axis=2) > 0.02).mean())}


# =============================================================================
# 1  the package and the pages
# =============================================================================

_pages = {}


def load_pages():
    """Split + load lobe 0 of every page we touch, as float arrays."""
    for pg in PAGES:
        files = LB.materialise_page_slices(REAL_LM, pg, CACHE)
        if len(files) != LB.SG5_LOBES:
            say("pages", f"page {pg}: SKIP, got {len(files)} slice files")
            continue
        im = bpy.data.images.load(files[0], check_existing=False)
        im.colorspace_settings.name = LM.COLORSPACE_LIGHTMAP
        a = pixels_of(im)[:, :, :3].astype("float64")
        bpy.data.images.remove(im)
        lum = a.mean(axis=2)
        gy, gx = np.gradient(lum)
        _pages[pg] = {"files": files, "rgb": a, "lum": lum,
                      "grad": np.hypot(gx, gy)}
        say("pages",
            f"page {pg:2d} -> slices {[int(Path(f).stem.split('slice')[1]) for f in files]} "
            f"(page*5+i = {LB.sg5_slice_indices(pg)})  lobe0: mean {lum.mean():.5f} "
            f"max {a.max():.4f} zero-texels {(lum <= 1e-6).mean()*100:.2f}%")


def import_pkg(flip):
    fresh()
    lone_echo_import.import_lemesh(str(PKG), bpy.context,
                                   {"import_materials": False, "flip_v": flip,
                                    "y_up_to_z_up": True})
    out = {}
    for ob in bpy.data.objects:
        if ob.type != "MESH":
            continue
        me = ob.data
        lay = me.uv_layers.get("uv1")
        if lay is None:
            continue
        uv = np.empty(len(me.loops) * 2, dtype="float64")
        lay.data.foreach_get("uv", uv)
        tris = []
        for p in me.polygons:
            s, k = p.loop_start, len(p.vertices)
            for j in range(1, k - 1):
                tris.append((s, s + j, s + j + 1))
        out[ob.name.split(".")[0]] = {
            "uv": uv.reshape(-1, 2), "tris": np.array(tris, dtype=int),
            "page": int(ob.get("le_lm_slice_index", -1))
            if "le_lm_slice_index" in ob.keys() else None,
            "lightmap_index": ob.get("le_lightmap_index"),
        }
    return out


def own_pages():
    """`lm_slice_index` straight out of the manifest — the ground truth for
    "which page is this object's own"."""
    import json
    man = json.loads((PKG / "manifest.json").read_text(encoding="utf-8"))
    return {o["name"]: int(o["lm_slice_index"]) for o in man["objects"]}, man


# =============================================================================
# 2  registration scoring — does the atlas's chart structure line up with uv1?
# =============================================================================

def rasterise(uv, tris, size=ATLAS):
    """Boolean coverage mask of the uv1 footprint, in Blender image row order
    (row 0 = v 0 = bottom), which is exactly how `pixels_of` returns the atlas."""
    mask = np.zeros((size, size), dtype=bool)
    P = uv * size
    for t in tris:
        a, b, c = P[t[0]], P[t[1]], P[t[2]]
        x0 = max(int(np.floor(min(a[0], b[0], c[0]))), 0)
        x1 = min(int(np.ceil(max(a[0], b[0], c[0]))) + 1, size)
        y0 = max(int(np.floor(min(a[1], b[1], c[1]))), 0)
        y1 = min(int(np.ceil(max(a[1], b[1], c[1]))) + 1, size)
        if x1 <= x0 or y1 <= y0:
            continue
        xs = np.arange(x0, x1) + 0.5
        ys = np.arange(y0, y1) + 0.5
        gx, gy = np.meshgrid(xs, ys)
        d = ((b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]))
        if abs(d) < 1e-12:
            continue
        w0 = ((b[1] - c[1]) * (gx - c[0]) + (c[0] - b[0]) * (gy - c[1])) / d
        w1 = ((c[1] - a[1]) * (gx - c[0]) + (a[0] - c[0]) * (gy - c[1])) / d
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9)
        mask[y0:y1, x0:x1] |= inside
    return mask


def _shift_or(m):
    o = m.copy()
    o[1:, :] |= m[:-1, :]
    o[:-1, :] |= m[1:, :]
    o[:, 1:] |= m[:, :-1]
    o[:, :-1] |= m[:, 1:]
    return o


def _erode(m):
    return ~_shift_or(~m)


def score(mask, pg):
    """How well does page `pg`'s own structure agree with this footprint?

    * `rel_grad_in` — mean |grad| STRICTLY INSIDE the footprint, divided by the
      mean level there.  A correctly registered island is the smooth interior of
      one bake chart, so its interior gradient is small relative to its level.
      A mis-registered island lands across OTHER charts' boundaries, so foreign
      discontinuities appear *inside* it.  Lower is better.
    * `edge_ratio` — mean |grad| on the footprint's own boundary ring divided by
      `rel_grad_in`'s numerator.  A registered island's discontinuities are
      concentrated ON its outline (that is where the atlas hands over to the next
      chart), so higher is better.
    * `zero_frac` — fraction of covered texels the bake never wrote (exactly 0).
      ★ THIS is the metric that discriminates; see the verdict note below.

    ⚠ MEASURED CAVEAT: `rel_grad_in` and `edge_ratio` were the metrics this front
    expected to work, and they DO NOT.  On the shipped data they disagree with
    each other and with the pictures (`rel_grad_in` picks flip_v=ON for obj001
    and flip_v=OFF for obj002/obj003, while the renders show obj001 flip-OFF is
    black and obj002/obj003 flip-OFF is blotchy).  They are kept, and printed,
    precisely so the failure is on the record rather than quietly dropped.
    """
    lum, grad = _pages[pg]["lum"], _pages[pg]["grad"]
    inner = _erode(mask)
    ring = _shift_or(mask) & ~inner
    # obj000's island is 3 texels tall — erosion empties it.  Fall back to the
    # raw mask rather than returning nothing.
    thin = False
    if inner.sum() < 16:
        inner, thin = mask, True
    if inner.sum() < 4 or ring.sum() < 4:
        return None
    lin = float(lum[inner].mean())
    gin = float(grad[inner].mean())
    gring = float(grad[ring].mean())
    return {"n": int(mask.sum()), "lum": lin, "max": float(lum[mask].max()),
            "grad_in": gin, "grad_edge": gring, "thin": thin,
            "rel_grad_in": gin / lin if lin > 1e-9 else float("inf"),
            "edge_ratio": gring / gin if gin > 1e-12 else float("inf"),
            "zero_frac": float((lum[mask] <= 1e-6).mean())}


def offset_uv(uv, dv):
    """The null-model control: slide the whole footprint in v, wrapping."""
    out = uv.copy()
    out[:, 1] = (out[:, 1] + dv) % 1.0
    return out


def probe_registration(n_controls=24):
    truth, _ = own_pages()
    flipped = import_pkg(True)
    plain = import_pkg(False)
    verdicts = []
    for name in sorted(truth):
        base = name
        if base not in flipped:
            say("registration", f"{name}: SKIP (no uv1 imported)")
            continue
        own = truth[name]
        if own not in _pages:
            say("registration", f"{name}: SKIP (page {own} not loaded)")
            continue
        d_on, d_off = flipped[base], plain[base]
        say("registration",
            f"--- {name}  lightmap_index={d_on['lightmap_index']} "
            f"own page={own} (slices {LB.sg5_slice_indices(own)})  "
            f"tris={len(d_on['tris'])}")

        rows = {}
        for label, uv in (("flip_v=ON ", d_on["uv"]), ("flip_v=OFF", d_off["uv"])):
            m = rasterise(uv, d_on["tris"])
            s = score(m, own)
            rows[label] = s
            if s is None:
                say("registration", f"    {label}: footprint too thin to score")
                continue
            say("registration",
                f"    {label} on OWN page {own}: covered {s['n']:6d} texels  "
                f"lum {s['lum']:.5f}  ZERO {s['zero_frac']*100:5.2f}%  "
                f"rel_grad_in {s['rel_grad_in']:.4f}  edge_ratio {s['edge_ratio']:.3f}"
                f"{'  (thin: no erosion)' if s['thin'] else ''}")

        # null model: the same island slid to 24 arbitrary v offsets
        rng = np.random.default_rng(7)
        ctl = []
        for dv in rng.uniform(0.05, 0.95, n_controls):
            s = score(rasterise(offset_uv(d_on["uv"], float(dv)), d_on["tris"]), own)
            if s:
                ctl.append(s)
        if ctl:
            rg = np.array([c["rel_grad_in"] for c in ctl])
            rg = rg[np.isfinite(rg)]
            er = np.array([c["edge_ratio"] for c in ctl])
            er = er[np.isfinite(er)]
            zf = np.array([c["zero_frac"] for c in ctl])
            say("registration",
                f"    NULL ({len(ctl)} random v-offsets, same island, same page): "
                f"rel_grad_in {rg.mean():.4f} +- {rg.std():.4f} "
                f"[{rg.min():.4f}..{rg.max():.4f}]  edge_ratio {er.mean():.3f} "
                f"+- {er.std():.3f}  zero {zf.mean()*100:.2f}%")
            on, off = rows.get("flip_v=ON "), rows.get("flip_v=OFF")
            if on and off and len(rg):
                better_g = ("flip_v=ON" if on["rel_grad_in"] < off["rel_grad_in"]
                            else "flip_v=OFF")
                say("registration",
                    f"    => rel_grad_in (NOT discriminating, see docstring): ON "
                    f"{on['rel_grad_in']:.4f} vs OFF {off['rel_grad_in']:.4f} vs "
                    f"null {rg.mean():.4f} -> would pick {better_g}")
                # ★ the metric that DOES discriminate
                better_z = ("flip_v=ON" if on["zero_frac"] < off["zero_frac"]
                            else "flip_v=OFF")
                say("registration",
                    f"    ★ ZERO-texel fraction: ON {on['zero_frac']*100:.2f}% vs "
                    f"OFF {off['zero_frac']*100:.2f}% vs null "
                    f"{zf.mean()*100:.2f}% -> {better_z} lands on WRITTEN atlas "
                    f"texels; the other straddles texels the bake never wrote")
                verdicts.append((name, better_g, better_z,
                                 on["zero_frac"], off["zero_frac"], float(zf.mean())))

        # and the wrong-page controls, at the CORRECT flip
        m_on = rasterise(d_on["uv"], d_on["tris"])
        for pg in PAGES:
            if pg == own or pg not in _pages:
                continue
            s = score(m_on, pg)
            if s:
                say("registration",
                    f"    same island, WRONG page {pg:2d}: lum {s['lum']:.5f} "
                    f"zero {s['zero_frac']*100:5.2f}% rel_grad_in "
                    f"{s['rel_grad_in']:.4f} edge_ratio {s['edge_ratio']:.3f}")
    if verdicts:
        g_on = sum(1 for v in verdicts if v[1] == "flip_v=ON")
        z_on = sum(1 for v in verdicts if v[2] == "flip_v=ON")
        say("verdict-flipv-numeric",
            f"zero-texel fraction favours flip_v=ON on {z_on}/{len(verdicts)} "
            f"objects; the rel_grad_in proxy favours it on only {g_on}/"
            f"{len(verdicts)} and is therefore reported as NOT discriminating")
    return flipped, plain, truth


# =============================================================================
# 3  the atlas pictures — uv1 island outlines drawn on the real atlas
# =============================================================================

def _emission_mat(name, color=None, image=None, colorspace=None, strength=1.0):
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        if n.type != "OUTPUT_MATERIAL":
            nt.nodes.remove(n)
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = strength
    nt.links.new(em.outputs["Emission"], out.inputs["Surface"])
    if image is not None:
        img = bpy.data.images.load(str(image), check_existing=True)
        try:
            img.colorspace_settings.name = colorspace or LM.COLORSPACE_LIGHTMAP
        except Exception:
            img.colorspace_settings.name = LM.COLORSPACE_LIGHTMAP_FALLBACK
        img.alpha_mode = "CHANNEL_PACKED"
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.interpolation = "Closest"
        tex.extension = "EXTEND"
        uvn = nt.nodes.new("ShaderNodeUVMap")
        uvn.uv_map = "uv0"
        nt.links.new(uvn.outputs["UV"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Color"], em.inputs["Color"])
    else:
        em.inputs["Color"].default_value = tuple(color or (1, 0, 1)) + (1.0,)
    return mat


def _atlas_plane(sc, dds):
    me = bpy.data.meshes.new("atlas")
    me.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)], [], [(0, 1, 2, 3)])
    me.update()
    lay = me.uv_layers.new(name="uv0")
    lay.data.foreach_set("uv", [0, 0, 1, 0, 1, 1, 0, 1])
    me.materials.append(_emission_mat("atlasmat", image=dds))
    ob = bpy.data.objects.new("atlas", me)
    sc.collection.objects.link(ob)
    return ob


def boundary_edges(uv, tris):
    """Edges used by exactly one triangle, after welding by uv position — i.e.
    the outlines of the uv1 charts."""
    keys = {}
    ids = np.empty(uv.shape[0], dtype=int)
    for i, (u, v) in enumerate(uv):
        k = (round(float(u), 6), round(float(v), 6))
        ids[i] = keys.setdefault(k, len(keys))
    pos = np.zeros((len(keys), 2))
    for i, (u, v) in enumerate(uv):
        pos[ids[i]] = (u, v)
    cnt = {}
    for t in tris:
        a, b, c = ids[t[0]], ids[t[1]], ids[t[2]]
        for e in ((a, b), (b, c), (c, a)):
            k = (min(e), max(e))
            cnt[k] = cnt.get(k, 0) + 1
    return pos, [k for k, v in cnt.items() if v == 1]


def _outline_object(sc, uv, tris, thickness, color, name, z=0.01):
    pos, edges = boundary_edges(uv, tris)
    verts, faces = [], []
    for a, b in edges:
        p, q = pos[a], pos[b]
        d = q - p
        n = np.hypot(*d)
        if n < 1e-12:
            continue
        nx, ny = -d[1] / n * thickness * 0.5, d[0] / n * thickness * 0.5
        i = len(verts)
        verts += [(p[0] + nx, p[1] + ny, z), (q[0] + nx, q[1] + ny, z),
                  (q[0] - nx, q[1] - ny, z), (p[0] - nx, p[1] - ny, z)]
        faces.append((i, i + 1, i + 2, i + 3))
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    me.materials.append(_emission_mat(name + "_mat", color=color, strength=1.0))
    ob = bpy.data.objects.new(name, me)
    sc.collection.objects.link(ob)
    return ob, len(edges)


def atlas_picture(page, islands, out_name, rect=None, label="", max_px=1100,
                  boost=1.0):
    """`islands` = [(uv, tris, colour, name)] drawn over page `page`'s lobe 0."""
    sc = fresh()
    dds = _pages[page]["files"][0]
    _atlas_plane(sc, dds)
    if boost != 1.0:
        m = bpy.data.materials["atlasmat"]
        em = next(n for n in m.node_tree.nodes if n.type == "EMISSION")
        em.inputs["Strength"].default_value = boost
    x0, y0, x1, y1 = rect or (0.0, 0.0, 1.0, 1.0)
    w, h = x1 - x0, y1 - y0
    # Line width in RENDER PIXELS, not UV units.  Several of these islands are
    # 1-3 texels tall; a UV-relative width draws an outline fatter than the thing
    # it outlines and hides exactly the content the picture exists to show.
    thick = max(w, h) / max_px * 1.6
    n_edges = 0
    for uv, tris, col, nm in islands:
        _, ne = _outline_object(sc, uv, tris, thick, col, nm)
        n_edges += ne
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = max(w, h)
    cam.location = ((x0 + x1) / 2, (y0 + y1) / 2, 4.0)
    sc.collection.objects.link(cam)
    sc.camera = cam
    if w >= h:
        res = (max_px, max(8, int(round(max_px * h / w))))
    else:
        res = (max(8, int(round(max_px * w / h))), max_px)
    p = render_png(sc, out_name, res)
    st = png_stats(p)
    say("render",
        f"{out_name}: {label}  page={page} slice={LB.sg5_slice_indices(page)[0]} "
        f"rect=({x0:.4f},{y0:.4f})-({x1:.4f},{y1:.4f}) res={res} "
        f"outline_edges={n_edges} boost={boost} "
        f"mean={st['mean']:.5f} max={st['max']:.4f}")
    return p


def bbox_of(uv, pad=0.02):
    x0, y0 = uv.min(axis=0)
    x1, y1 = uv.max(axis=0)
    px, py = (x1 - x0) * pad + 0.004, (y1 - y0) * pad + 0.004
    return (max(0.0, x0 - px), max(0.0, y0 - py),
            min(1.0, x1 + px), min(1.0, y1 + py))


MAGENTA = (1.0, 0.0, 1.0)
CYAN = (0.0, 1.0, 1.0)


def pictures_atlas(flipped, plain, truth):
    by_page = {}
    for name, page in truth.items():
        by_page.setdefault(page, []).append(name)

    for page, names in sorted(by_page.items()):
        if page not in _pages:
            continue
        for tag, data in (("flipON", flipped), ("flipOFF", plain)):
            isl = [(data[n]["uv"], data[n]["tris"],
                    MAGENTA if i == 0 else CYAN, f"isl{i}")
                   for i, n in enumerate(sorted(names)) if n in data]
            atlas_picture(page, isl, f"lm_atlas_p{page}_{tag}.png",
                          label=f"WHOLE page {page} + uv1 island outlines of "
                                f"{', '.join(sorted(names))} ({tag})")
        # zoomed crop, per object
        for i, n in enumerate(sorted(names)):
            for tag, data in (("flipON", flipped), ("flipOFF", plain)):
                if n not in data:
                    continue
                uv, tris = data[n]["uv"], data[n]["tris"]
                bb = bbox_of(uv)
                atlas_picture(page, [(uv, tris, MAGENTA, "isl")],
                              f"lm_crop_{n[:6]}_p{page}_{tag}.png", rect=bb,
                              label=f"CROP on {n}'s uv1 bbox, page {page} ({tag})")
                # A footprint wider than ~8:1 is unreadable as one strip: the
                # shipped bake packs these objects' charts into a band ~1000 x 35
                # texels, so the whole-band view is 1 render pixel per texel.
                # Take DEEP zoom windows ~48 texels wide instead, where a texel
                # is ~20 px and the chart edges are actually visible.
                w, h = bb[2] - bb[0], bb[3] - bb[1]
                if w / max(h, 1e-9) > 8.0:
                    zw = 48.0 / ATLAS
                    for j, frac in enumerate((0.10, 0.45, 0.80)):
                        cx = bb[0] + w * frac
                        sub = (max(0.0, cx - zw / 2), bb[1],
                               min(1.0, cx + zw / 2), bb[3])
                        atlas_picture(
                            page, [(uv, tris, MAGENTA, "isl")],
                            f"lm_zoom_{n[:6]}_p{page}_{tag}_u{j}.png", rect=sub,
                            label=f"DEEP ZOOM {j+1}/3 (~48 texels wide) into "
                                  f"{n}'s uv1 band, page {page} ({tag})")


def pictures_wrong_page(flipped, truth):
    """The same island outline over the WRONG page, at the correct flip."""
    for name, own in sorted(truth.items()):
        if name not in flipped or own not in _pages:
            continue
        uv, tris = flipped[name]["uv"], flipped[name]["tris"]
        for pg, tag in ((own, "ownpage"), (WRONG_PAGE, "wrongpage")):
            if pg not in _pages:
                continue
            atlas_picture(pg, [(uv, tris, MAGENTA, "isl")],
                          f"lm_crop_{name[:6]}_{tag}{pg}.png", rect=bbox_of(uv),
                          label=f"{name} uv1 island over page {pg} "
                                f"({'OWN' if pg == own else 'WRONG'})")


def picture_hdr_mask(page):
    """Which atlas texels exceed 1.0 — white where HDR, grey where lit, black
    where unlit — and where the four meshes' islands fall relative to them."""
    lum = _pages[page]["rgb"].max(axis=2)
    img = bpy.data.images.new(f"hdrmask{page}", ATLAS, ATLAS, float_buffer=True)
    v = np.zeros((ATLAS, ATLAS, 4), dtype="float32")
    v[:, :, 3] = 1.0
    v[:, :, 0] = np.where(lum > 1.0, 1.0, np.where(lum > 1e-6, 0.15, 0.0))
    v[:, :, 1] = v[:, :, 0]
    v[:, :, 2] = np.where(lum > 1.0, 0.0, v[:, :, 0])
    img.pixels.foreach_set(v.ravel())
    p = TMP / f"hdrmask{page}.png"
    img.filepath_raw = str(p)
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)
    say("hdr-mask",
        f"page {page}: {(lum > 1.0).mean()*100:.3f}% of atlas texels exceed 1.0 "
        f"(max {lum.max():.4f})")
    return p


# =============================================================================
# 4  the mesh pictures
# =============================================================================

def _spec(page):
    """Point `wire_lightmap` at the RAW 65-slice array with `slice_index = page`
    so the picture exercises the production auto-split + page*5+i arithmetic."""
    return {"lightmap_index": 1, "slice_index": page, "uv_layer": "uv1",
            "color": {"role": "lightmapid", "hash": "0178fa39b1b95d2f",
                      "file": REAL_LM.name,
                      "colorspace": LM.COLORSPACE_LIGHTMAP,
                      "expected_dxgi": 95, "dxgi": 95, "dxgi_unexpected": False}}


def _wire_object(ob, page, basis, intensity=1.0):
    mat = bpy.data.materials.new(f"lm_{ob.name}_{page}_{basis}")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    ob.data.materials.clear()
    ob.data.materials.append(mat)
    return LB.wire_lightmap(mat, nt, bsdf, _spec(page),
                            {"pkg_dir": str(REAL), "lightmap_mode": LB.MODE_BAKED,
                             "lightmap_basis": basis, "lightmap_intensity": intensity,
                             "lightmap_slice_dir": str(CACHE)})


def _frame(sc, objs, margin=1.6):
    # ⚠ TWO traps here, both of which render a silent, plausible-looking BLACK
    # frame, and both of which cost this harness a run:
    #   1. `object.bound_box` on a just-built object is still the pre-depsgraph
    #      (0,0,0) box.  These meshes sit 80..280 units out.  Read the vertices.
    #   2. `mesh_builder` puts the Y-up -> Z-up conversion on `ob.matrix_basis`
    #      (`mesh_builder.py:242`), and `matrix_world` keeps reporting IDENTITY
    #      until the depsgraph is updated — so the camera gets aimed at the
    #      un-rotated position and the object is off-frame.  Force the update.
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
    # Three of these four objects are long thin plates/beams.  A fixed camera
    # direction shows obj003 (167 units long, a few units thick) EDGE-ON, which
    # renders as a couple of hairlines and looks exactly like "the lightmap is
    # black".  Look down the SHORTEST axis instead, so the largest face — the one
    # that actually carries the bake — faces the film.
    thin = min(range(3), key=lambda i: ext[i])
    d = Vector((0.0, 0.0, 0.0))
    d[thin] = 1.0
    d[(thin + 1) % 3] = 0.35
    d[(thin + 2) % 3] = -0.25
    d = d.normalized()
    cam.location = centre + d * size * margin
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    # obj003 is 167 units long; Blender's default clip_end of 100 would cut it in
    # half without saying anything.
    cam.data.clip_start = max(1e-3, size * 1e-4)
    cam.data.clip_end = size * 100.0 + 1000.0
    sc.camera = cam
    bpy.context.view_layer.update()


def mesh_picture(out_name, page_of, basis, flip=True, label="", only=None,
                 exr=False, intensity=1.0):
    """Import the real package and render it with each object wired to
    `page_of(name)`.

    `intensity` goes straight into `lightmap_intensity` (Emission Strength).  It
    exists ONLY so the two dimmest objects are legible; every such render is
    named `_x<N>` and prints its multiplier.  The verdict renders are all
    `intensity == 1.0`.
    """
    sc = fresh()
    lone_echo_import.import_lemesh(str(PKG), bpy.context,
                                   {"import_materials": False, "flip_v": flip,
                                    "y_up_to_z_up": True})
    objs, reps = [], []
    for ob in list(bpy.data.objects):
        if ob.type != "MESH":
            continue
        base = ob.name.split(".")[0]
        if only and base != only:
            bpy.data.objects.remove(ob)
            continue
        rep = _wire_object(ob, page_of(base), basis, intensity=intensity)
        reps.append((base, rep))
        objs.append(ob)
    if not objs:
        say("render", f"{out_name}: SKIP — nothing to render")
        return None
    _frame(sc, objs)
    res = (960, 720)
    p = render_png(sc, out_name, res)
    st = png_stats(p)
    r0 = reps[0][1]
    say("render",
        f"{out_name}: {label} basis={r0.get('basis')!r} lobes={r0.get('lobes')} "
        f"auto_split={r0.get('auto_split')} cs={r0.get('colorspace')!r} "
        f"uv={r0.get('uv_layer')!r} flip_v={flip} intensity={intensity} "
        f"pages={[(n, rp.get('page')) for n, rp in reps]} "
        f"slices={[Path(f).stem.split('slice')[-1] for f in r0.get('slice_files', [])]} "
        f"mean={st['mean']:.5f} max={st['max']:.4f} lit={st['lit_frac']*100:.1f}%")
    # A silent all-black frame reads exactly like "the lightmap is black" but is
    # usually a framing bug.  Never let it pass unannounced.
    if st["max"] <= 0.004:
        say("render-BLACK",
            f"{out_name}: frame is BLACK (max {st['max']:.5f}) — treat as a "
            f"harness fault, not a lightmap result, until the framing is proven")
    if exr:
        a = render_exr(sc, res, name=out_name.replace(".png", ""))
        rgb = a[:, :, :3]
        say("hdr-film",
            f"{out_name}: linear EXR max {rgb.max():.5f} "
            f"({int((rgb > 1.0).sum())} sub-pixel samples > 1.0 of "
            f"{rgb.size}) -> HDR reaches the film: {bool(rgb.max() > 1.0)}")
    return st


# =============================================================================

#: `... --python <this> -- reg atlas mesh` runs a subset — the full pass makes
#: ~50 renders and iterating on one section should not cost the other two.
#: (An env var is NOT used: this Blender is a Windows .exe usually driven from
#: WSL, and the environment does not cross that boundary.)
STAGES = set(sys.argv[sys.argv.index("--") + 1:]) if "--" in sys.argv else set()


def want(stage):
    return not STAGES or stage in STAGES


def main():
    say("env", f"Blender {bpy.app.version_string} (hash {bpy.app.build_hash.decode() if isinstance(bpy.app.build_hash, bytes) else bpy.app.build_hash})")
    say("env", f"factory view_transform would be "
               f"{bpy.context.scene.view_settings.view_transform!r}; every render below forces 'Standard'")
    if not PKG.is_dir():
        say("env", f"FATAL — package missing: {PKG}")
        return
    if not REAL_LM.exists():
        say("env", f"FATAL — atlas missing: {REAL_LM}")
        return
    fresh()
    load_pages()
    truth, man = own_pages()
    say("pkg", f"{PKG.name}: {len(man['objects'])} objects, "
               f"lm_slice_index = {truth}")

    flipped, plain, truth = (probe_registration() if want("reg")
                             else (import_pkg(True), import_pkg(False), truth))

    if want("atlas"):
        pictures_atlas(flipped, plain, truth)
        pictures_wrong_page(flipped, truth)
        for pg in sorted({p for p in truth.values() if p in _pages}):
            picture_hdr_mask(pg)

    own = truth.get
    if want("mesh"):
        # all four, each on its own page, SG5
        mesh_picture("lm_mesh_all_ownpage_sg5.png", lambda n: own(n, 0), "sg5",
                     label="all four objects, EACH on its OWN page, SG5", exr=True)
        mesh_picture("lm_mesh_all_page0_sg5.png", lambda n: WRONG_PAGE, "sg5",
                     label=f"all four forced to page {WRONG_PAGE} (the page bug)")
        mesh_picture("lm_mesh_all_ownpage_sg5_flipOFF.png", lambda n: own(n, 0),
                     "sg5", flip=False, label="own pages, SG5, flip_v OFF")
        for name in sorted(truth):
            short = name[:6]
            mesh_picture(f"lm_mesh_{short}_ownpage{truth[name]}_sg5.png",
                         lambda n: own(n, 0), "sg5", only=name,
                         label=f"{name} alone, own page {truth[name]}, SG5", exr=True)
            mesh_picture(f"lm_mesh_{short}_wrongpage{WRONG_PAGE}_sg5.png",
                         lambda n: WRONG_PAGE, "sg5", only=name,
                         label=f"{name} alone, WRONG page {WRONG_PAGE}, SG5")
            mesh_picture(f"lm_mesh_{short}_ownpage{truth[name]}_single.png",
                         lambda n: own(n, 0), "single", only=name,
                         label=f"{name} alone, own page {truth[name]}, single lobe 0")
            mesh_picture(f"lm_mesh_{short}_ownpage{truth[name]}_sg5_flipOFF.png",
                         lambda n: own(n, 0), "sg5", only=name, flip=False,
                         label=f"{name} alone, own page {truth[name]}, SG5, flip_v OFF")
            # x8 exposure — two of these four objects sit in genuinely dim parts
            # of the bake (peak film value < 0.14) and are unreadable at 1.0.
            # The multiplier is in the filename and in the log; it changes the
            # exposure, nothing else.
            for tag, pg, fl in (("ownpage%d" % truth[name], own(name, 0), True),
                                ("wrongpage%d" % WRONG_PAGE, WRONG_PAGE, True),
                                ("ownpage%d_flipOFF" % truth[name], own(name, 0), False)):
                mesh_picture(f"lm_mesh_{short}_{tag}_sg5_x8.png",
                             (lambda p: (lambda n: p))(pg), "sg5", only=name,
                             flip=fl, intensity=8.0,
                             label=f"{name}, {tag}, SG5, EXPOSURE x8")

    print("\n===== SUMMARY =====")
    for line in _results:
        print(line)


main()
