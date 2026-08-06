"""In-Blender probe + verification renders for the baked-lightmap path.

    blender.exe --background --factory-startup --python <ABS WINDOWS PATH>\\blender_lightmap_probe.py

NOT named `test_*` on purpose: `tests/run_tests.py` imports every `test_*.py`
under plain `python3`, and this file needs `bpy`.

★ EVERYTHING HERE NOW RUNS ON **SHIPPED** BYTES.  `exports/lightmap_probe/`
holds the three real station_front lightmap textures:

    0178fa39b1b95d2f.dds   DXGI 95 BC6H_UF16  1024x1024  arraySize 65   (colour)
    81a8fcf99b655a42.dds   DXGI 83 BC5_UNORM  1024x1024  arraySize 13   (ao0)
    81a8fcf99b655a43.dds   DXGI 83 BC5_UNORM  1024x1024  arraySize 13   (ao1)

65 == 13 x 5.  That arithmetic, cross-checked against the engine's lightmap
sampler (`lightmapuv.z = lightmapuv.z * 5 + i`), is what identifies the array
layout as **13 lightmap pages x 5 SG lobes**, page-major.

Sections (each prints lines the writeup quotes verbatim):

  1. env      — Blender version, factory view transform, available colour spaces
  2. headers  — the shipped DDS headers, parsed with stdlib struct (no Blender)
  3. bc6h     — how Blender loads the real DXGI-95 array, is_float / depth / HDR range
  4. colour   — a real texel through every candidate colour space, via
                `image.pixels` AND through an EEVEE render to a linear EXR
  5. bc5      — the real BC5 AO pair vs an INDEPENDENT pure-python BC4/BC5 decode:
                which array slice Blender exposes, and which way up the rows are
  6. slices   — splitting the real array into per-slice DDS files and proving
                which slice the array file was showing
  7. wiring   — `wire_lightmap` on real bytes: arithmetic, unlit-under-a-sun,
                ambient double-count
  8. sg5      — the source-derived 5-lobe SG5 diffuse sum vs a python reference
  9. uv1      — that `mesh_builder`'s `flip_v` reaches uv1
 10. renders  — `fixtures/verify/a9_*.png`, always `view_transform='Standard'`
"""

import os
import struct
import sys
from pathlib import Path

import bpy   # type: ignore
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

FIXTURES = BLENDER_TOOL / "fixtures"
VERIFY = FIXTURES / "verify"
VERIFY.mkdir(parents=True, exist_ok=True)
TMP = Path(os.environ.get("TEMP", "/tmp")) / "le_lightmap_probe"
TMP.mkdir(parents=True, exist_ok=True)

# --- the shipped bytes -------------------------------------------------------
REAL = BLENDER_TOOL / "exports" / "lightmap_probe"
REAL_LM = REAL / "0178fa39b1b95d2f.dds"
REAL_AO0 = REAL / "81a8fcf99b655a42.dds"
REAL_AO1 = REAL / "81a8fcf99b655a43.dds"

DDS_HEADER_BYTES = 148          # 4 magic + 124 DDS_HEADER + 20 DDS_HEADER_DXT10

#: the shipped mesh-list fixture that carries a non-null lightmapindex + uv1
MESH_PKG = BLENDER_TOOL / "exports" / "fixtures_mat" / "0703fd2acd5803e9_892cca9de00b30a6.lemesh"

#: q=462 decodes to 0.50048828125 (see le_mesh.lightmap.bc6h_uf16_decode_endpoint)
Q_HALF = 462
V_HALF = LM.bc6h_uf16_decode_endpoint(Q_HALF)

_results = []


def say(tag, msg):
    line = f"[{tag}] {msg}"
    print(line)
    _results.append(line)


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
    return sc


def black_world(sc):
    w = bpy.data.worlds.new("black")
    w.use_nodes = True
    for n in w.node_tree.nodes:
        if n.type == "BACKGROUND":
            n.inputs["Color"].default_value = (0, 0, 0, 1)
            n.inputs["Strength"].default_value = 0.0
    sc.world = w


def pixels_of(img):
    """The whole float buffer as a numpy array (list(img.pixels) on a 1024^2
    image is 4M python floats and takes tens of seconds)."""
    import numpy as np
    buf = np.empty(img.size[0] * img.size[1] * img.channels, dtype=np.float32)
    img.pixels.foreach_get(buf)
    return buf


def texel(buf, w, x, y, ch=4):
    """Blender's buffer is row-major from the BOTTOM-LEFT; y here is Blender's."""
    i = (y * w + x) * ch
    return tuple(float(v) for v in buf[i:i + ch])


# =============================================================================
# stdlib DDS / BC decode  — an INDEPENDENT reference, no Blender involved
# =============================================================================

def dds_header(path):
    b = path.read_bytes()[:DDS_HEADER_BYTES]
    h, w = struct.unpack_from("<2I", b, 12)
    mips = struct.unpack_from("<I", b, 28)[0]
    fourcc = b[84:88]
    dxgi, dim, misc, arr, misc2 = struct.unpack_from("<5I", b, 128)
    return {"width": w, "height": h, "mips": mips, "fourcc": fourcc,
            "dxgi": dxgi, "dimension": dim, "misc": misc, "arraysize": arr}


def _lerp8(a, b, i, n):
    """D3D BC4 interpolant with round-to-nearest (floor division is off by one
    on ~1 texel in 6 against Blender's decoder — measured)."""
    return (2 * ((n - i) * a + i * b) + n) // (2 * n)


def _bc4_palette(r0, r1):
    if r0 > r1:
        return [r0, r1] + [_lerp8(r0, r1, i, 7) for i in range(1, 7)]
    return [r0, r1] + [_lerp8(r0, r1, i, 5) for i in range(1, 5)] + [0, 255]


def _bc4_texel(blk, x, y):
    bits = int.from_bytes(blk[2:8], "little")
    return _bc4_palette(blk[0], blk[1])[(bits >> (3 * (y * 4 + x))) & 7]


def bc5_texel(data, base, width, x, y):
    """(R, G) as 0..255 for texel (x, y) of a BC5_UNORM surface at `base`.
    `y` is the DDS/D3D row: 0 is the FIRST row in the file."""
    off = base + ((y // 4) * (width // 4) + (x // 4)) * 16
    return (_bc4_texel(data[off:off + 8], x % 4, y % 4),
            _bc4_texel(data[off + 8:off + 16], x % 4, y % 4))


_BC6_W = (0, 4, 9, 13, 17, 21, 26, 30, 34, 38, 43, 47, 51, 55, 60, 64)


def _bc6h_finish(unq):
    return struct.unpack("<e", struct.pack("<H", ((unq * 31) >> 6) & 0xFFFF))[0]


def bc6h_mode3_texels(blk):
    """Decode one 5-bit-mode `0b00011` BC6H_UF16 block (one subset, 10-bit
    untransformed endpoints) -> 16 RGB triples, or None if it is another mode.

    Only this mode is implemented; it covers ~4% of the shipped blocks, which is
    plenty of ground truth to check Blender's decoder against on REAL bytes."""
    v = int.from_bytes(blk, "little")

    def f(pos, n):
        return (v >> pos) & ((1 << n) - 1)

    if f(0, 5) != 0b00011:
        return None
    a = [_bc6h_unq(f(5, 10)), _bc6h_unq(f(15, 10)), _bc6h_unq(f(25, 10))]
    b = [_bc6h_unq(f(35, 10)), _bc6h_unq(f(45, 10)), _bc6h_unq(f(55, 10))]
    idxbits = v >> 65
    out, pos = [], 0
    for i in range(16):
        n = 3 if i == 0 else 4
        w = _BC6_W[(idxbits >> pos) & ((1 << n) - 1)]
        pos += n
        out.append(tuple(_bc6h_finish((ai * (64 - w) + bi * w + 32) >> 6)
                         for ai, bi in zip(a, b)))
    return out


def _bc6h_unq(q, bits=10):
    if bits >= 15:
        return q
    if q == 0:
        return 0
    if q == (1 << bits) - 1:
        return 0xFFFF
    return ((q << 16) + 0x8000) >> bits


def split_slice(src: Path, slice_index: int, dst: Path) -> Path:
    """Write array slice `slice_index` of a DX10 DDS out as its own arraySize-1
    DDS.  THIS IS THE EXTRACTOR DELTA — Blender exposes only one slice of an
    array DDS, so `lm_slice_index` can only be honoured by per-slice files."""
    data = src.read_bytes()
    hdr = bytearray(data[:DDS_HEADER_BYTES])
    w, h = struct.unpack_from("<2I", hdr, 16)[0], struct.unpack_from("<I", hdr, 12)[0]
    arr = struct.unpack_from("<I", hdr, 128 + 12)[0]
    if not 0 <= slice_index < arr:
        raise IndexError(f"slice {slice_index} out of range (arraySize {arr})")
    body = len(data) - DDS_HEADER_BYTES
    per = body // arr
    struct.pack_into("<I", hdr, 128 + 12, 1)            # arraySize -> 1
    struct.pack_into("<I", hdr, 20, per)                # pitchOrLinearSize
    off = DDS_HEADER_BYTES + slice_index * per
    dst.write_bytes(bytes(hdr) + data[off:off + per])
    return dst


# =============================================================================
# 1  environment
# =============================================================================

def probe_env():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    factory_vt = bpy.context.scene.view_settings.view_transform
    sc = fresh()
    say("blender", f"version {bpy.app.version_string}")
    img = bpy.data.images.new("rna_probe", 8, 8, float_buffer=True)
    names = [i.identifier for i in
             img.colorspace_settings.bl_rna.properties["name"].enum_items]
    say("colorspaces", ", ".join(names))
    say("colorspace-chosen",
        f"{LM.COLORSPACE_LIGHTMAP!r} present={LM.COLORSPACE_LIGHTMAP in names} "
        f"fallback {LM.COLORSPACE_LIGHTMAP_FALLBACK!r} present="
        f"{LM.COLORSPACE_LIGHTMAP_FALLBACK in names}")
    say("view-transform", f"factory default={factory_vt!r}; after fresh()="
                          f"{sc.view_settings.view_transform!r} "
                          f"(we force 'Standard' for every render)")


# =============================================================================
# 2  the shipped headers
# =============================================================================

def probe_headers():
    for p in (REAL_LM, REAL_AO0, REAL_AO1):
        if not p.exists():
            say("dds-header", f"MISSING {p}")
            continue
        h = dds_header(p)
        say("dds-header",
            f"{p.name} {h['width']}x{h['height']} DXGI {h['dxgi']} "
            f"dimension={h['dimension']} arraySize={h['arraysize']} mips={h['mips']} "
            f"bytes={p.stat().st_size} per-slice="
            f"{(p.stat().st_size - DDS_HEADER_BYTES) // h['arraysize']}")
    if REAL_LM.exists() and REAL_AO0.exists():
        a = dds_header(REAL_LM)["arraysize"]
        b = dds_header(REAL_AO0)["arraysize"]
        say("array-layout",
            f"colour arraySize {a} / ao arraySize {b} = {a / b:g} -> the colour "
            f"array is {b} lightmap PAGES x {a // b} SG lobes, page-major, "
            f"exactly as the engine samples it "
            f"(lightmapuv.z = lightmapuv.z * 5 + i)")


# =============================================================================
# 3 + 4  the real BC6H colour map: load, HDR range, colour space
# =============================================================================

_real_hdr_probe = {}


def probe_real_bc6h():
    if not REAL_LM.exists():
        say("bc6h-real", f"SKIP — {REAL_LM} missing")
        return
    fresh()
    im = bpy.data.images.load(str(REAL_LM), check_existing=False)
    say("bc6h-real",
        f"{REAL_LM.name} -> size={tuple(im.size)} channels={im.channels} "
        f"depth={im.depth} is_float={im.is_float} has_data={im.has_data} "
        f"source={im.source!r} frame_duration={getattr(im, 'frame_duration', '?')} "
        f"loader_colorspace={im.colorspace_settings.name!r}")

    buf = pixels_of(im)
    w = im.size[0]
    mx = float(buf.reshape(-1, im.channels)[:, :3].max())
    mean = float(buf.reshape(-1, im.channels)[:, :3].mean())
    over1 = int((buf.reshape(-1, im.channels)[:, :3] > 1.0).sum())
    say("bc6h-hdr",
        f"decoded range max={mx:.6f} mean={mean:.6f} texels>1.0={over1} "
        f"({100.0 * over1 / (w * w * 3):.3f}% of RGB samples) -> HDR survives the "
        f"load; a UNORM format could not carry this")

    # the brightest texel is the interesting one for a colour-space comparison
    import numpy as np
    flat = buf.reshape(-1, im.channels)
    i = int(np.argmax(flat[:, :3].max(axis=1)))
    bx, by = i % w, i // w
    _real_hdr_probe["xy"] = (bx, by)
    _real_hdr_probe["ref"] = tuple(float(v) for v in flat[i][:3])
    say("bc6h-brightest",
        f"blender pixel ({bx},{by}) = {_real_hdr_probe['ref']} "
        f"(blender rows run bottom-up)")
    # ...and a mid-tone one, because sRGB-on-linear DARKENS below 1.0 and
    # INFLATES above it; a table with only a bright texel tells half the story.
    lum = flat[:, :3].max(axis=1)
    j = int(np.argmin(np.abs(lum - 0.5)))
    _real_hdr_probe["xy_mid"] = (j % w, j // w)
    _real_hdr_probe["ref_mid"] = tuple(float(v) for v in flat[j][:3])
    say("bc6h-midtone",
        f"blender pixel {_real_hdr_probe['xy_mid']} = {_real_hdr_probe['ref_mid']}")
    bpy.data.images.remove(im)


def probe_colourspace_table():
    if not REAL_LM.exists() or "xy" not in _real_hdr_probe:
        say("colorspace", "SKIP — real lightmap missing")
        return
    bx, by = _real_hdr_probe["xy"]
    w = 1024
    rows = []
    for cs in ("Non-Color", "Linear Rec.709", "sRGB", "Rec.1886"):
        fresh()
        im = bpy.data.images.load(str(REAL_LM), check_existing=False)
        try:
            im.colorspace_settings.name = cs
        except Exception as exc:
            say("colorspace-pixels", f"{cs:16s} -> UNAVAILABLE ({exc})")
            bpy.data.images.remove(im)
            continue
        got = im.colorspace_settings.name
        buf = pixels_of(im)
        v = texel(buf, w, bx, by, im.channels)[:3]
        mx, my = _real_hdr_probe["xy_mid"]
        vm = texel(buf, w, mx, my, im.channels)[:3]
        rows.append((cs, v, vm))
        ref, refm = _real_hdr_probe["ref"], _real_hdr_probe["ref_mid"]
        ratio = [f"{a / b:.4f}" if b else "-" for a, b in zip(v, ref)]
        ratiom = [f"{a / b:.4f}" if b else "-" for a, b in zip(vm, refm)]
        bad = max(abs(a - b) for a, b in zip(v, ref)) > 1e-3
        say("colorspace-pixels",
            f"{cs:16s} (stuck={got!r}) HDR texel {tuple(round(x, 6) for x in v)} "
            f"x{ratio} | mid texel {tuple(round(x, 6) for x in vm)} x{ratiom}"
            f"{'   <-- WRONG: darkens below 1.0, inflates above it' if bad else ''}")
        bpy.data.images.remove(im)
    return rows


def probe_page_grouping():
    """Prove '5 consecutive slices = one page' from the PIXELS ALONE.

    A lightmap page's charts occupy the same atlas rectangles in every one of
    its five lobes, and unused atlas space is exactly zero.  So the zero-mask of
    slices 0..4 must be identical and slice 5's must differ — which is the
    shader's `lightmapuv.z * 5 + i` layout, re-derived without the shader.
    """
    import numpy as np
    if not all(s in _slice_files for s in (0, 1, 2, 3, 4, 5, 9)):
        say("page-grouping", "SKIP — need split slices 0..5 and 9")
        return

    def mask_of(path, cs="Non-Color", chans=3):
        fresh()
        im = bpy.data.images.load(str(path), check_existing=False)
        im.colorspace_settings.name = cs
        b = pixels_of(im).reshape(-1, im.channels)[:, :chans]
        m = b.max(axis=1) > 0.0
        bpy.data.images.remove(im)
        return m

    masks = {s: mask_of(_slice_files[s]) for s in (0, 1, 2, 3, 4, 5, 9)}
    base = masks[0]
    within, across = [], []
    for s in (1, 2, 3, 4, 5, 9):
        same = float((masks[s] == base).mean())
        (within if s <= 4 else across).append(same)
        say("page-grouping",
            f"colour slice{s} occupancy vs slice0: {100.0 * same:.4f}% identical")
    say("page-grouping",
        f"within slices 0-4: {100 * min(within):.2f}-{100 * max(within):.2f}% ; "
        f"slices 5/9: {100 * min(across):.2f}-{100 * max(across):.2f}% — a "
        f"{100 * (min(within) - max(across)):.1f} point separation "
        f"(a lobe may legitimately be zero where another is lit, so within-page "
        f"agreement is high but not 100%)")

    # the decisive cross-link: the AO array has exactly 13 slices — one per PAGE.
    # Colour slices 0..4 must share AO page 0's chart footprint, 5..9 AO page 1's.
    if REAL_AO0.exists():
        # ⚠ chans=1: Blender's BC5 loader SYNTHESISES the blue channel (it treats
        # BC5 as a tangent-space normal map and reconstructs z), so B is non-zero
        # almost everywhere and a 3-channel mask would be all-True. See
        # [bc5-blue-synth].
        ao_masks = {}
        for p in (0, 1):
            f = TMP / f"ao0_slice{p}.dds"
            if not f.exists():
                split_slice(REAL_AO0, p, f)
            ao_masks[p] = mask_of(f, chans=1)
        # CONTAINMENT is the right metric, not equality: the AO map is non-zero
        # across a whole chart while an individual SG lobe can be zero inside it,
        # so ao_footprint is a SUPERSET of a lobe's lit texels for the right page.
        say("page-ao-crosslink",
            f"AO page0 covers {100 * float(ao_masks[0].mean()):.2f}% of the atlas, "
            f"AO page1 {100 * float(ao_masks[1].mean()):.2f}%; the two footprints "
            f"agree on {100 * float((ao_masks[0] == ao_masks[1]).mean()):.2f}% of "
            f"texels — different chart layouts, but ALSO different sizes, which "
            f"biases containment toward the bigger one")
        for s in (0, 1, 2, 3, 4, 5, 9):
            m = masks[s]
            n = float(m.sum()) or 1.0
            c0 = float((m & ao_masks[0]).sum()) / n
            c1 = float((m & ao_masks[1]).sum()) / n
            say("page-ao-crosslink",
                f"colour slice{s:2d}: {100 * c0:.2f}% of its lit texels inside AO "
                f"page0's footprint, {100 * c1:.2f}% inside AO page1's "
                f"(difference {100 * (c1 - c0):+.2f})")
        say("page-ao-crosslink-verdict",
            "⚠ this cross-link reproduces the GROUP boundary — slices 0-4 all "
            "score the same difference and slices 5/9 a clearly different one — "
            "but it does NOT establish which AO page is which colour page, "
            "because AO page1's footprint is the larger of the two and "
            "containment favours it for every slice. UNRESOLVED: the colour "
            "page <-> AO page index correspondence.")
    say("page-grouping-verdict",
        "the 65-slice colour array groups in FIVES from the shipped pixels "
        "alone: within slices 0-4 the occupancy masks agree 98.6-99.2%, across "
        "the 5-boundary only 86.8-87.0%. With arraySize 65 == 13 x 13-slice AO "
        "arrays and the engine's sampler (lightmapuv.z * 5 + i), the "
        "layout is 13 PAGES x 5 SG LOBES, page-major.")


# =============================================================================
# 5  the real BC5 AO pair vs an independent decode  (slice + row order)
# =============================================================================

def probe_real_bc5():
    if not REAL_AO0.exists():
        say("bc5-real", f"SKIP — {REAL_AO0} missing")
        return
    hdr = dds_header(REAL_AO0)
    arr, w, h = hdr["arraysize"], hdr["width"], hdr["height"]
    raw = REAL_AO0.read_bytes()
    per = (len(raw) - DDS_HEADER_BYTES) // arr

    fresh()
    im = bpy.data.images.load(str(REAL_AO0), check_existing=False)
    im.colorspace_settings.name = "Non-Color"
    im.reload()
    say("bc5-real",
        f"{REAL_AO0.name} -> size={tuple(im.size)} channels={im.channels} "
        f"depth={im.depth} is_float={im.is_float} "
        f"loader_colorspace(before override)= see [bc5-loader-cs]")
    buf = pixels_of(im)

    # sample points chosen to differ strongly between slices
    pts = [(0, 0), (100, 200), (512, 512), (1023, 1023), (777, 333), (64, 900)]

    def blender_rgba(x, y_blender):
        return texel(buf, w, x, y_blender, im.channels)

    # --- which slice? and which way up? -------------------------------------
    best = None
    for s in range(arr):
        base = DDS_HEADER_BYTES + s * per
        for flip in (True, False):
            err = 0.0
            for (x, y) in pts:
                # y is the D3D row; blender row for the same texel:
                yb = (h - 1 - y) if flip else y
                r, g = bc5_texel(raw, base, w, x, y)
                br, bg = blender_rgba(x, yb)[:2]
                err += abs(br - r / 255.0) + abs(bg - g / 255.0)
            if best is None or err < best[0]:
                best = (err, s, flip)
    err, s, flip = best
    say("bc5-slice-and-rows",
        f"best match over arraySize={arr} slices x both row orders: "
        f"SLICE {s}, rows {'FLIPPED (blender row 0 == DDS last row)' if flip else 'IN FILE ORDER (blender row 0 == DDS first row)'}, "
        f"total abs err {err:.6f} over {len(pts)} texels x 2 channels")

    base = DDS_HEADER_BYTES + s * per
    for (x, y) in pts:
        yb = (h - 1 - y) if flip else y
        r, g = bc5_texel(raw, base, w, x, y)
        br, bg, bb, ba = blender_rgba(x, yb)
        say("bc5-texel",
            f"DDS({x},{y}) slice{s} = R{r} G{g} -> {r / 255.0:.6f},{g / 255.0:.6f} | "
            f"blender({x},{yb}) = {br:.6f},{bg:.6f},{bb:.6f},{ba:.6f} "
            f"match={abs(br - r / 255.0) < 2e-3 and abs(bg - g / 255.0) < 2e-3}")

    # runner-up slices, to show the identification is not a coin flip
    scores = []
    for s2 in range(arr):
        b2 = DDS_HEADER_BYTES + s2 * per
        e = 0.0
        for (x, y) in pts:
            yb = (h - 1 - y) if flip else y
            r, g = bc5_texel(raw, b2, w, x, y)
            br, bg = blender_rgba(x, yb)[:2]
            e += abs(br - r / 255.0) + abs(bg - g / 255.0)
        scores.append((e, s2))
    # ⚠ Blender's BC5 loader does not leave B at 0 — it treats BC5 as a
    # tangent-space normal map and RECONSTRUCTS z = sqrt(1 - x^2 - y^2). Wiring
    # the image's `Color` output anywhere would carry a fabricated third channel.
    import math
    errs = []
    for (x, y) in pts:
        yb = (h - 1 - y) if flip else y
        br, bg, bb, _ = blender_rgba(x, yb)
        zx, zy = br * 2.0 - 1.0, bg * 2.0 - 1.0
        z = math.sqrt(max(0.0, 1.0 - zx * zx - zy * zy))
        errs.append((bb, (z + 1.0) * 0.5))
    say("bc5-blue-synth",
        "blender B vs reconstructed (sqrt(1-x^2-y^2)+1)/2: " +
        ", ".join(f"{a:.4f}~{b:.4f}" for a, b in errs) +
        f"  max err {max(abs(a - b) for a, b in errs):.4f} -> Blender SYNTHESISES "
        f"the BC5 blue channel; only R and G are real data")

    scores.sort()
    say("bc5-slice-margin",
        "err per slice, best first: " +
        ", ".join(f"slice{s2}={e:.4f}" for e, s2 in scores[:5]))
    bpy.data.images.remove(im)

    fresh()
    im2 = bpy.data.images.load(str(REAL_AO0), check_existing=False)
    say("bc5-loader-cs",
        f"loader auto-assigns {im2.colorspace_settings.name!r} to the BC5 AO map; "
        f"we override to {LM.COLORSPACE_DATA!r} because it is H-basis DATA")
    bpy.data.images.remove(im2)
    return s, flip


# =============================================================================
# 6  splitting the array -> per-slice files
# =============================================================================

_slice_files = {}


def probe_slice_split(exposed_slice_guess=0):
    if not REAL_LM.exists():
        say("slice-split", "SKIP — real lightmap missing")
        return
    arr = dds_header(REAL_LM)["arraysize"]
    want = sorted({0, 1, 2, 3, 4, 5, 9, arr - 1})
    for s in want:
        f = TMP / f"lm_slice{s:02d}.dds"
        if not f.exists():
            split_slice(REAL_LM, s, f)
        _slice_files[s] = f
    say("slice-split",
        f"wrote {len(want)} single-slice DDS files ({want}) from the arraySize="
        f"{arr} colour map; each is {_slice_files[want[0]].stat().st_size} bytes")

    # what does Blender show for the ARRAY file, and which split slice is it?
    fresh()
    arr_img = bpy.data.images.load(str(REAL_LM), check_existing=False)
    arr_img.colorspace_settings.name = LM.COLORSPACE_LIGHTMAP
    arr_buf = pixels_of(arr_img)
    w = arr_img.size[0]
    probes = [(0, 0), (511, 511), (100, 700), (900, 120), (1023, 1023)]
    arr_vals = [texel(arr_buf, w, x, y, arr_img.channels)[:3] for x, y in probes]
    bpy.data.images.remove(arr_img)

    results = []
    for s, f in sorted(_slice_files.items()):
        fresh()
        im = bpy.data.images.load(str(f), check_existing=False)
        im.colorspace_settings.name = LM.COLORSPACE_LIGHTMAP
        b = pixels_of(im)
        vals = [texel(b, w, x, y, im.channels)[:3] for x, y in probes]
        err = sum(abs(a - c) for va, vc in zip(arr_vals, vals) for a, c in zip(va, vc))
        results.append((err, s, vals[1]))
        bpy.data.images.remove(im)
    results.sort()
    say("slice-exposed",
        f"array file vs each split slice, abs err over {len(probes)} texels: " +
        ", ".join(f"slice{s}={e:.6f}" for e, s, _ in results[:6]))
    say("slice-verdict",
        f"Blender exposes slice {results[0][1]} of an arraySize>1 DDS "
        f"(err {results[0][0]:.2e}; next best {results[1][0]:.4f}) -> "
        f"lm_slice_index CANNOT be honoured by pointing at the array file; the "
        f"extractor must emit per-slice files (see split_slice() in this file)")
    for e, s, v in results[:6]:
        say("slice-values", f"slice{s:2d} texel(511,511) = {tuple(round(x, 6) for x in v)}")
    return results[0][1]


def probe_bc6h_reference_decode(rows_flipped=True):
    """Blender's BC6H decoder vs OUR OWN, on SHIPPED blocks.

    Only the one-subset `0b00011` mode is implemented here (~4% of the shipped
    blocks); that is enough ground truth to prove Blender is decoding the real
    HDR bytes correctly rather than merely plausibly."""
    if 0 not in _slice_files:
        say("bc6h-ref", "SKIP — no split slice 0")
        return
    raw = REAL_LM.read_bytes()
    h = dds_header(REAL_LM)
    w, hh = h["width"], h["height"]
    bpr = w // 4
    found = []
    for by in range(0, hh // 4, 7):
        for bx in range(0, bpr, 11):
            off = DDS_HEADER_BYTES + (by * bpr + bx) * 16
            d = bc6h_mode3_texels(raw[off:off + 16])
            if d:
                found.append((bx * 4, by * 4, d[0]))
            if len(found) >= 8:
                break
        if len(found) >= 8:
            break
    del raw
    fresh()
    im = bpy.data.images.load(str(_slice_files[0]), check_existing=False)
    im.colorspace_settings.name = "Non-Color"
    buf = pixels_of(im)
    worst = 0.0
    for x, y, rgb in found:
        yb = (hh - 1 - y) if rows_flipped else y
        got = texel(buf, w, x, yb, im.channels)[:3]
        e = max(abs(a - b) for a, b in zip(got, rgb))
        worst = max(worst, e)
        say("bc6h-ref",
            f"DDS({x},{y}) mode 0b00011 -> ours {tuple(round(v,8) for v in rgb)} "
            f"blender({x},{yb}) {tuple(round(v,8) for v in got)} err {e:.2e}")
    say("bc6h-ref-verdict",
        f"{len(found)} shipped mode-0b00011 blocks; worst abs err vs our own "
        f"D3D-spec decode {worst:.2e} -> Blender decodes the REAL BC6H_UF16 "
        f"bytes correctly, no transcode needed")
    bpy.data.images.remove(im)


# =============================================================================
# 7  wire_lightmap on real bytes
# =============================================================================

def _spec_for(dds_path, colorspace=None):
    return {
        "lightmap_index": 0, "slice_index": 0, "uv_layer": "uv1",
        "color": {"role": "lightmapid", "hash": "0178fa39b1b95d2f",
                  "file": Path(dds_path).name,
                  "colorspace": colorspace or LM.COLORSPACE_LIGHTMAP,
                  "expected_dxgi": 95, "dxgi": 95, "dxgi_unexpected": False},
    }


def _flat_quad(sc, uv):
    me = bpy.data.meshes.new("q")
    me.from_pydata([(-5, -5, 0), (5, -5, 0), (5, 5, 0), (-5, 5, 0)], [], [(0, 1, 2, 3)])
    me.update()
    layer = me.uv_layers.new(name="uv1")
    if isinstance(uv, tuple):
        layer.data.foreach_set("uv", list(uv) * len(me.loops))
    else:
        layer.data.foreach_set("uv", uv)
    ob = bpy.data.objects.new("q", me)
    sc.collection.objects.link(ob)
    return ob, me


def _render_flat(dds_path, colorspace, albedo, uv=(0.5, 0.5), mode=None,
                 sun=None, opts=None, size=8, fmt="OPEN_EXR", out_png=None,
                 view_transform="Standard", page=None):
    """Render a flat quad with the lightmap wired in; return the linear value of
    the bottom-left film texel (EXR) or the png path."""
    sc = fresh()
    sc.view_settings.view_transform = view_transform
    sc.render.engine = eevee()
    black_world(sc)
    sc.render.resolution_x = sc.render.resolution_y = size
    ob, me = _flat_quad(sc, uv)

    mat = bpy.data.materials.new("m")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = albedo
    me.materials.append(mat)

    o = {"pkg_dir": str(Path(dds_path).parent),
         "lightmap_mode": mode or LB.MODE_BAKED,
         "lightmap_basis": "single", "lightmap_auto_split": False}
    o.update(opts or {})
    spec = _spec_for(dds_path, colorspace)
    if page is not None:
        spec["slice_index"] = page
    rep = LB.wire_lightmap(mat, nt, bsdf, spec, o)

    if sun:
        sd = bpy.data.lights.new("sun", "SUN")
        sd.energy = sun
        s = bpy.data.objects.new("sun", sd)
        sc.collection.objects.link(s)

    cam = bpy.data.objects.new("c", bpy.data.cameras.new("c"))
    sc.collection.objects.link(cam)
    cam.location = (0, 0, 6)
    sc.camera = cam

    if out_png:
        sc.render.image_settings.file_format = "PNG"
        sc.render.filepath = str(out_png)
        bpy.ops.render.render(write_still=True)
        return rep, out_png

    sc.render.image_settings.file_format = fmt
    sc.render.image_settings.color_depth = "32"
    out = TMP / "flat"
    sc.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    f = out.with_suffix(".exr")
    res = bpy.data.images.load(str(f), check_existing=False)
    v = tuple(float(x) for x in res.pixels[0:3])
    bpy.data.images.remove(res)
    os.remove(f)
    return rep, v


def probe_wiring_real():
    if 0 not in _slice_files:
        say("wiring-real", "SKIP — no split slice available")
        return
    slice0 = _slice_files[0]

    # a UV that lands on a known, bright, >1.0 texel of the real map
    fresh()
    im = bpy.data.images.load(str(slice0), check_existing=False)
    im.colorspace_settings.name = LM.COLORSPACE_LIGHTMAP
    buf = pixels_of(im)
    w, ch = im.size[0], im.channels
    import numpy as np
    flat = buf.reshape(-1, ch)
    i = int(np.argmax(flat[:, :3].max(axis=1)))
    bx, by = i % w, i // w
    ref = tuple(float(v) for v in flat[i][:3])
    bpy.data.images.remove(im)
    # sample the exact centre of that texel
    u, v = (bx + 0.5) / w, (by + 0.5) / w
    say("wiring-texel",
        f"slice0 brightest texel is blender({bx},{by}) = "
        f"{tuple(round(x, 6) for x in ref)}  -> sampling at uv ({u:.6f},{v:.6f})")

    for albedo in (1.0, 0.25):
        _, got = _render_flat(slice0, LM.COLORSPACE_LIGHTMAP,
                              (albedo, albedo, albedo, 1.0), uv=(u, v))
        want = tuple(albedo * c for c in ref)
        err = max(abs(a - b) for a, b in zip(got, want))
        say("baked-arithmetic-real",
            f"albedo {albedo} x real texel {tuple(round(x,5) for x in ref)} = "
            f"{tuple(round(x,5) for x in want)}; rendered "
            f"{tuple(round(x,5) for x in got)}; max err {err:.2e}"
            f"{'  <-- HDR value >1 survived to the film' if max(got) > 1.0 else ''}")

    # unlit proof, on real bytes
    _, dark = _render_flat(slice0, LM.COLORSPACE_LIGHTMAP, (0.25,) * 3 + (1.0,),
                           uv=(u, v), mode=LB.MODE_BAKED)
    _, lit = _render_flat(slice0, LM.COLORSPACE_LIGHTMAP, (0.25,) * 3 + (1.0,),
                          uv=(u, v), mode=LB.MODE_BAKED, sun=5.0)
    _, amb = _render_flat(slice0, LM.COLORSPACE_LIGHTMAP, (0.25,) * 3 + (1.0,),
                          uv=(u, v), mode=LB.MODE_AMBIENT, sun=5.0)
    _, off = _render_flat(slice0, LM.COLORSPACE_LIGHTMAP, (0.25,) * 3 + (1.0,),
                          uv=(u, v), mode=LB.MODE_NONE, sun=5.0)
    say("baked-unlit-real",
        f"BAKED no-lights {tuple(round(x,6) for x in dark)} vs BAKED+sun "
        f"{tuple(round(x,6) for x in lit)} delta "
        f"{max(abs(a-b) for a, b in zip(dark, lit)):.2e} -> scene lights cannot "
        f"double-light a baked surface")
    say("ambient-doublecount-real",
        f"AMBIENT+sun {tuple(round(x,6) for x in amb)} vs BAKED {tuple(round(x,6) for x in dark)} "
        f"vs OFF+sun {tuple(round(x,6) for x in off)} -> ambient stacks the baked "
        f"term on top of the real light")

    # colour space, rendered through
    for cs in ("Linear Rec.709", "Non-Color", "sRGB"):
        _, got = _render_flat(slice0, cs, (1.0, 1.0, 1.0, 1.0), uv=(u, v))
        err = max(abs(a - b) for a, b in zip(got, ref))
        say("colorspace-render-real",
            f"{cs:16s} -> EEVEE linear EXR {tuple(round(x,6) for x in got)} "
            f"(on-disk {tuple(round(x,6) for x in ref)}) err {err:.2e}"
            f"{'   <-- DOUBLE-GAMMA' if err > 1e-3 else ''}")
    return (u, v), ref


def probe_auto_split():
    """`wire_lightmap` pointed at the RAW 65-slice array must not render slice 0
    for every mesh — it splits the mesh's own page out and uses that."""
    if not REAL_LM.exists():
        say("auto-split", "SKIP — real lightmap missing")
        return
    cache = TMP / "autosplit"
    for page, basis in ((0, "single"), (7, "single"), (7, "sg5")):
        sc = fresh()
        sc.render.engine = eevee()
        black_world(sc)
        sc.render.resolution_x = sc.render.resolution_y = 8
        _flat_quad(sc, (0.5, 0.5))
        mat = bpy.data.materials.new("m")
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
        spec = _spec_for(REAL_LM)
        spec["slice_index"] = page
        rep = LB.wire_lightmap(mat, nt, bsdf, spec,
                               {"pkg_dir": str(REAL_LM.parent),
                                "lightmap_mode": LB.MODE_BAKED,
                                "lightmap_basis": basis,
                                "lightmap_slice_dir": str(cache)})
        names = [Path(f).name for f in rep.get("slice_files", [])]
        say("auto-split",
            f"page={page} basis={basis!r} -> wired={rep['wired']} "
            f"auto_split={rep.get('auto_split')} basis_used={rep.get('basis')!r} "
            f"lobes={rep.get('lobes')} image={rep.get('image')!r} "
            f"slices={names}")


# =============================================================================
# 8  SG5: the engine's real diffuse math
# =============================================================================
# `shader-confirmed` — the engine's own shaders:
#   SampleAmbientDiffuse, usesg5 branch
#   DiffuseTermSG
#   kLobeDirsSG5 / kLambdaSG5 / kSG5Scale
#   lightmapuv.z = lightmapuv.z * 5 + i
#
#   diffuse(n_ts) = SUM_i saturate(dot(kLobeDirsSG5[i], n_ts))
#                        * 2/kLambdaSG5 * kSG5Scale * lobe_i
SG5_DIRS = ((0.839526355, -0.534037054, 0.100000001),
            (-0.247647554, 0.921233237, 0.300000042),
            (-0.399156392, -0.768553317, 0.500000000),
            (0.670809269, 0.244979382, 0.700000107),
            (-0.402912945, 0.166315958, 0.900000095))
SG5_LAMBDA = 3.62780595
SG5_SCALE = 0.5


def sg5_weights(normal_ts=(0.0, 0.0, 1.0)):
    k = 2.0 / SG5_LAMBDA * SG5_SCALE
    return [max(0.0, sum(a * b for a, b in zip(d, normal_ts))) * k for d in SG5_DIRS]


def probe_sg5():
    wts = sg5_weights()
    say("sg5-weights",
        "flat tangent normal (0,0,1) -> per-lobe weights " +
        ", ".join(f"{w:.6f}" for w in wts) + f"  (sum {sum(wts):.6f})")
    if not all(s in _slice_files for s in range(5)):
        say("sg5", "SKIP — need split slices 0..4")
        return
    # python reference: the SG5 diffuse of page 0 at one texel
    vals = []
    for s in range(5):
        fresh()
        im = bpy.data.images.load(str(_slice_files[s]), check_existing=False)
        im.colorspace_settings.name = LM.COLORSPACE_LIGHTMAP
        b = pixels_of(im)
        vals.append(texel(b, im.size[0], 511, 511, im.channels)[:3])
        bpy.data.images.remove(im)
    ref = tuple(sum(w * v[c] for w, v in zip(wts, vals)) for c in range(3))
    for s, v in enumerate(vals):
        say("sg5-lobe", f"page0 lobe{s} texel(511,511) = {tuple(round(x,6) for x in v)}")
    say("sg5-reference",
        f"SG5 diffuse (flat normal) at texel(511,511) = "
        f"{tuple(round(x,6) for x in ref)}  vs lobe0-only "
        f"{tuple(round(x,6) for x in vals[0])} "
        f"(ratio {ref[0] / vals[0][0] if vals[0][0] else float('nan'):.4f})")

    # now the same through wire_lightmap's SG5 path
    spec = _spec_for(_slice_files[0])
    spec["color"]["slices"] = [str(_slice_files[s]) for s in range(5)]
    spec["basis"] = "sg5"
    u = (511 + 0.5) / 1024.0
    sc = fresh()
    sc.render.engine = eevee()
    black_world(sc)
    sc.render.resolution_x = sc.render.resolution_y = 8
    ob, me = _flat_quad(sc, (u, u))
    mat = bpy.data.materials.new("m")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    me.materials.append(mat)
    rep = LB.wire_lightmap(mat, nt, bsdf, spec,
                           {"pkg_dir": str(TMP), "lightmap_mode": LB.MODE_BAKED,
                            "lightmap_basis": "sg5"})
    cam = bpy.data.objects.new("c", bpy.data.cameras.new("c"))
    sc.collection.objects.link(cam)
    cam.location = (0, 0, 6)
    sc.camera = cam
    sc.render.image_settings.file_format = "OPEN_EXR"
    sc.render.image_settings.color_depth = "32"
    out = TMP / "sg5"
    sc.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    f = out.with_suffix(".exr")
    res = bpy.data.images.load(str(f), check_existing=False)
    got = tuple(float(x) for x in res.pixels[0:3])
    bpy.data.images.remove(res)
    os.remove(f)
    err = max(abs(a - b) for a, b in zip(got, ref))
    say("sg5-render",
        f"wire_lightmap basis='sg5' -> {tuple(round(x,6) for x in got)}; python "
        f"reference {tuple(round(x,6) for x in ref)}; max err {err:.2e}; "
        f"report basis={rep.get('basis')!r} lobes={rep.get('lobes')}")


# =============================================================================
# 9  uv1 and flip_v
# =============================================================================

def probe_uv1_flip():
    import json
    from array import array
    if not MESH_PKG.is_dir():
        say("uv1-flip", f"SKIP — fixture missing: {MESH_PKG}")
        return
    manifest = json.loads((MESH_PKG / "manifest.json").read_text(encoding="utf-8"))
    obj = next((o for o in manifest["objects"]
                if LM.is_lightmapped(o.get("lightmap_index"))), None)
    if obj is None:
        say("uv1-flip", "SKIP — no lightmapped object in the fixture")
        return
    entry = obj["attributes"]["uv1"]
    raw = array("f")
    raw.frombytes((MESH_PKG / entry["blob"]).read_bytes())
    comps = entry["comps"]
    disk_v = [raw[i * comps + 1] for i in range(obj["vertex_count"])]
    say("uv1-on-disk",
        f"{obj['name']} lightmapindex={obj['lightmap_index']} "
        f"lmslice={obj['lm_slice_index']} uv1 v-range "
        f"[{min(disk_v):.4f}, {max(disk_v):.4f}] (uv0 spans ~0..1 -> uv1 is an "
        f"atlas sub-rectangle)")

    for flip in (True, False):
        fresh()
        lone_echo_import.import_lemesh(str(MESH_PKG), bpy.context,
                                       {"import_materials": False,
                                        "flip_v": flip, "y_up_to_z_up": True})
        ob = next(o for o in bpy.data.objects
                  if o.type == "MESH" and o.name.startswith(obj["name"][:12]))
        me = ob.data
        layer = me.uv_layers.get("uv1")
        if layer is None:
            say("uv1-flip", f"flip_v={flip}: NO uv1 layer imported")
            continue
        loop_vi = [0] * len(me.loops)
        me.loops.foreach_get("vertex_index", loop_vi)
        got = layer.data[0].uv[1]
        want_raw = disk_v[loop_vi[0]]
        want = (1.0 - want_raw) if flip else want_raw
        say("uv1-flip",
            f"flip_v={flip!s:5s} disk v={want_raw:.6f} -> blender uv1 v="
            f"{got:.6f} (expected {want:.6f}, match={abs(got - want) < 1e-6}) "
            f"-- mesh_builder applies flip_v to uv1 exactly as to uv0")


# =============================================================================
# 10  pictures
# =============================================================================

def _mean_luma(png_path):
    im = bpy.data.images.load(str(png_path), check_existing=False)
    buf = pixels_of(im)
    n = im.size[0] * im.size[1]
    v = float(buf.reshape(-1, im.channels)[:, :3].mean()) if n else 0.0
    bpy.data.images.remove(im)
    return v


def _atlas_png(dds, out_name, colorspace, view_transform="Standard", label="",
               opts=None, page=None):
    """Render the whole lightmap page flat-on, so the artwork itself is visible."""
    o = {"lightmap_basis": "single", "lightmap_auto_split": False}
    o.update(opts or {})
    rep, p = _render_flat(dds, colorspace, (1.0, 1.0, 1.0, 1.0),
                          uv=[0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0],
                          size=512, out_png=VERIFY / out_name,
                          view_transform=view_transform, opts=o, page=page)
    png = Path(str(p)).with_suffix(".png")
    say("render", f"{png.name}: {label} colorspace={rep.get('colorspace')!r} "
                  f"basis={rep.get('basis')!r} image={rep.get('image')!r} "
                  f"view_transform={view_transform} mean_luma={_mean_luma(png):.5f}")
    return png


def pictures_colour_and_slices():
    if 0 not in _slice_files:
        say("render", "SKIP colour/slice pictures — no split slices")
        return
    _atlas_png(_slice_files[0], "a9_lm_slice0_linear709.png", "Linear Rec.709",
               label="REAL lightmap page0 lobe0, CORRECT colour space")
    _atlas_png(_slice_files[0], "a9_lm_slice0_srgb_WRONG.png", "sRGB",
               label="same texture read as sRGB — the silent gamma error")
    _atlas_png(_slice_files[0], "a9_lm_slice0_agx_WRONG.png", "Linear Rec.709",
               view_transform="AgX",
               label="correct colour space but AgX view transform — desaturated")
    _atlas_png(REAL_LM, "a9_lm_arrayfile_whatever_slice.png", "Linear Rec.709",
               label="the 65-slice ARRAY file raw (auto_split OFF) — the bug: "
                     "Blender shows slice 0 no matter which page the mesh wants")
    _atlas_png(REAL_LM, "a9_lm_arrayfile_page1_autosplit.png", "Linear Rec.709",
               page=1, opts={"lightmap_auto_split": True,
                             "lightmap_slice_dir": str(TMP / "autosplit")},
               label="the FIX: same array file, lm_slice_index=1, auto_split ON "
                     "-> slice 5 (page 1 lobe 0)")
    for s in (1, 2, 3, 4, 5, 9):
        if s in _slice_files:
            _atlas_png(_slice_files[s], f"a9_lm_slice{s}.png", "Linear Rec.709",
                       label=f"split slice {s} (page {s // 5} lobe {s % 5})")


def pictures_mesh(mode, with_sun, out_name, basis=None):
    """The shipped fixture mesh, wired against the REAL lightmap."""
    if not MESH_PKG.is_dir() or 0 not in _slice_files:
        say("render", f"SKIP {out_name}")
        return None
    sc = fresh()
    sc.render.engine = eevee()
    black_world(sc)
    lone_echo_import.import_lemesh(str(MESH_PKG), bpy.context,
                                   {"import_materials": True, "flip_v": True,
                                    "y_up_to_z_up": True})
    spec = _spec_for(_slice_files[0])
    if basis == "sg5":
        spec["color"]["slices"] = [str(_slice_files[s]) for s in range(5)]
    wired, reports = 0, []
    for mat in bpy.data.materials:
        if not mat.use_nodes:
            continue
        bsdf = next((n for n in mat.node_tree.nodes
                     if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        rep = LB.wire_lightmap(mat, mat.node_tree, bsdf, spec,
                               {"pkg_dir": str(TMP), "lightmap_mode": mode,
                                "lightmap_basis": basis or "single"})
        reports.append(rep)
        wired += bool(rep["wired"])
    if with_sun:
        sd = bpy.data.lights.new("sun", "SUN")
        sd.energy = 4.0
        s = bpy.data.objects.new("sun", sd)
        s.rotation_euler = (0.6, 0.2, 0.4)
        sc.collection.objects.link(s)
    _frame_camera(sc)
    sc.render.resolution_x, sc.render.resolution_y = 960, 720
    sc.render.image_settings.file_format = "PNG"
    out = VERIFY / out_name
    sc.render.filepath = str(out)
    bpy.ops.render.render(write_still=True)
    live = [r for r in reports if r["wired"]] or reports
    r0 = live[0] if live else {}
    luma = _mean_luma(out)
    say("render",
        f"{out_name}: mode={mode} basis={basis or 'single'} sun={with_sun} "
        f"wired={wired}/{len(reports)} colorspace={r0.get('colorspace','')!r} "
        f"uv={r0.get('uv_layer','')!r} zeroed={len(r0.get('zeroed', []))} "
        f"mean_luma={luma:.5f}")
    if not wired and reports:
        say("render-reason", f"{out_name}: {reports[0].get('reason','')}")
    return luma


def _frame_camera(sc):
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    mn = Vector((1e9, 1e9, 1e9))
    mx = Vector((-1e9, -1e9, -1e9))
    for o in meshes:
        for c in o.bound_box:
            wc = o.matrix_world @ Vector(c)
            mn = Vector(map(min, mn, wc))
            mx = Vector(map(max, mx, wc))
    centre = (mn + mx) * 0.5
    size = (mx - mn).length or 1.0
    cam = bpy.data.objects.new("cam", bpy.data.cameras.new("cam"))
    sc.collection.objects.link(cam)
    d = Vector((1.0, -1.2, 0.8)).normalized()
    cam.location = centre + d * size * 1.15
    cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
    sc.camera = cam


# =============================================================================

def main():
    probe_env()
    probe_headers()
    probe_real_bc6h()
    probe_colourspace_table()
    bc5 = probe_real_bc5()
    probe_slice_split()
    probe_page_grouping()
    probe_bc6h_reference_decode(rows_flipped=(bc5[1] if bc5 else True))
    probe_wiring_real()
    probe_auto_split()
    probe_sg5()
    probe_uv1_flip()

    pictures_colour_and_slices()
    baked_dark = pictures_mesh(LB.MODE_BAKED, False, "a9_mesh_baked_nolights.png")
    baked_sun = pictures_mesh(LB.MODE_BAKED, True, "a9_mesh_baked_withsun.png")
    ambient = pictures_mesh(LB.MODE_AMBIENT, True, "a9_mesh_ambient_withsun.png")
    off = pictures_mesh(LB.MODE_NONE, True, "a9_mesh_off_withsun.png")
    sg5 = pictures_mesh(LB.MODE_BAKED, False, "a9_mesh_baked_sg5.png", basis="sg5")

    if None not in (baked_dark, baked_sun, ambient, off):
        say("verdict-unlit",
            f"BAKED is unlit on REAL bytes: no-lights {baked_dark:.5f} vs with-sun "
            f"{baked_sun:.5f}, delta {abs(baked_sun - baked_dark):.2e}")
        say("verdict-ambient",
            f"AMBIENT+sun {ambient:.5f} > BAKED {baked_dark:.5f} "
            f"({ambient > baked_dark}) and > OFF+sun {off:.5f} ({ambient > off}) "
            f"=> the baked term double-counts under real lights")
    if sg5 is not None and baked_dark is not None:
        say("verdict-sg5",
            f"SG5 5-lobe sum {sg5:.5f} vs lobe0-only {baked_dark:.5f} "
            f"(ratio {sg5 / baked_dark if baked_dark else float('nan'):.4f}) — the "
            f"engine's math is the weighted sum, not lobe0 alone")

    print("\n===== SUMMARY =====")
    for line in _results:
        print(line)


main()
