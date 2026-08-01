"""Wire a Lone Echo baked lightmap into a Blender material node graph.

Standalone: it imports only `bpy` and takes the material / node-tree / BSDF it is
given, so it can be driven from a probe script without the rest of the addon.
`material_builder.build_material` can call it at the end; see `docs/LIGHTING.md`
for the current wiring status.

Why "baked / unlit" is the default
----------------------------------
Lone Echo is a hybrid renderer, but the *diffuse* term of every lit surface is
baked.  What was measured on the shipped data:

* the bake is **101.8 MB** of irradiance SH + 1024^2 HDR lightmaps against
  **108 KB** of light records — a 936x ratio;
* only **49 of 118** shipped light records set `eEnableDiffuse`, while 112 of 118
  set `eEnableSpecular` — over half the level lights are *specular only*;
* 86 of the 87 lit-surface shaders bind the baked ambient path *and* the
  clustered realtime path; **0 are baked-only, 1 is clustered-only**.

Blender has neither a specular-only light nor a baked diffuse layer underneath.
So importing the lights as ordinary Blender lights *on top of* the lightmap
double-lights the scene.  Mode `"baked"` therefore reproduces the shipped look
by making the surface unlit — `Emission = albedo x lightmap` — and zeroing the
BSDF's own diffuse/specular response so scene lights cannot add a second copy.
`"ambient"` is the documented alternative for anyone who wants real lights on
top; it *will* double-count unless only the `eEnableDiffuse` subset is imported.

Colour management (measured on Blender 5.1.1)
---------------------------------------------
The HDR map is BC6H_UF16.  Blender 5.1.1 loads that DDS natively as a **float**
image and its loader auto-assigns `'Linear Rec.709'`; `'Linear Rec.709'` and
`'Non-Color'` return the exact on-disk texel, `'sRGB'` returns the double-gammaed
value.  We set the colour space explicitly rather than trusting the default, and
we never let the HDR map take an sRGB transform.  See
`le_mesh.lightmap.COLORSPACE_LIGHTMAP` and `tests/blender_lightmap_probe.py`.

UVs
---
The lightmap samples `uv1`, which `mesh_builder` already imports as a UV layer,
already V-flipped by the shared `flip_v` option.  A `UV Map` node pins the
lightmap texture to that layer so it is independent of the active UV set.

The bake is SG5, not a single colour map
----------------------------------------
The shipped colour map is a **texture array**: `0178fa39b1b95d2f.dds` is
DXGI 95 BC6H_UF16 1024x1024 **arraySize 65**, and its AO siblings are
**arraySize 13** (headers parsed in `tests/blender_lightmap_probe.py`).
65 == 13 x 5, and the engine's own lightmap fetch indexes the array as

        lightmapuv.z = lightmapuv.z * 5 + i;          // i = 0..4

so the array is **13 lightmap pages x 5 SG lobes**, page-major.  The lightmap
therefore does not hold irradiance directly; each page holds five spherical-
gaussian radiance lobes in TANGENT space, and the engine's diffuse term is

        diffuse(n_ts) = SUM_i  saturate(dot(kLobeDirsSG5[i], n_ts))
                             * (2 / kLambdaSG5) * kSG5Scale * lobe_i

`wire_lightmap` implements that as `lightmap_basis="sg5"` when the spec carries
five per-lobe files.  With no normal map the tangent-space normal is (0,0,1),
which collapses the lobe weights to five constants (`SG5_WEIGHTS_FLAT`) and
makes the node graph a weighted sum of five image textures.  With only one file
available it falls back to `"single"`, which is lobe 0 alone — visibly darker
and directionally wrong, but a defined, reported fallback rather than a lie.
"""

from __future__ import annotations

import struct
from pathlib import Path

try:                                     # pragma: no cover - Blender always has it
    import bpy   # type: ignore
except ImportError:                      # importable under plain python3 so the
    bpy = None                           # pure helpers below are unit-testable

# --- modes -------------------------------------------------------------------
#: Emission = albedo x lightmap, BSDF diffuse/specular zeroed. No scene lights
#: needed; adding them changes nothing. The default.
MODE_BAKED = "baked"
#: lightmap added as an extra emissive ambient term, BSDF left intact so real
#: lights still light the surface. Double-counts unless only the eEnableDiffuse
#: lights are imported.
MODE_AMBIENT = "ambient"
#: do nothing (but still record provenance on the material).
MODE_NONE = "none"

MODES = (MODE_BAKED, MODE_AMBIENT, MODE_NONE)

DEFAULT_MODE = MODE_BAKED
DEFAULT_INTENSITY = 1.0
#: weight of the baked term in MODE_AMBIENT; < 1 because real lights add on top.
DEFAULT_AMBIENT_WEIGHT = 1.0

#: `le_mesh.lightmap.COLORSPACE_LIGHTMAP`, duplicated so the addon package stays
#: importable without `le_mesh` on sys.path (the addon ships standalone).
COLORSPACE_LIGHTMAP = "Linear Rec.709"
COLORSPACE_LIGHTMAP_FALLBACK = "Non-Color"
COLORSPACE_DATA = "Non-Color"
UV_LAYER = "uv1"

# --- the SG5 basis -----------------------------------------------------------
#: the engine's five SG5 lobe directions
SG5_DIRS = (
    (0.839526355, -0.534037054, 0.100000001),
    (-0.247647554, 0.921233237, 0.300000042),
    (-0.399156392, -0.768553317, 0.500000000),
    (0.670809269, 0.244979382, 0.700000107),
    (-0.402912945, 0.166315958, 0.900000095),
)
SG5_LAMBDA = 3.62780595                     # kLambdaSG5
SG5_SCALE = 0.5                             # kSG5Scale
SG5_LOBES = 5

BASIS_SINGLE = "single"
BASIS_SG5 = "sg5"
#: SG5 is what the engine actually does, so it is the default; it degrades to
#: BASIS_SINGLE — loudly, via `report["basis"]` / `report["basis_reason"]` —
#: whenever the five per-lobe slices cannot be obtained.
DEFAULT_BASIS = BASIS_SG5


def sg5_weights(normal_ts=(0.0, 0.0, 1.0)):
    """DiffuseTermSG's per-lobe scalar for a tangent-space normal.

    The engine's DiffuseTermSG is `saturate(dot(mean, n)) * 2 / sharpness *
    color`, with `color = lobe * kSG5Scale` and `sharpness = kLambdaSG5`.
    """
    k = 2.0 / SG5_LAMBDA * SG5_SCALE
    return [max(0.0, sum(a * b for a, b in zip(d, normal_ts))) * k
            for d in SG5_DIRS]


#: the weights for a surface with no normal map (n_ts == (0,0,1)); the lobe
#: directions' z components are 0.1/0.3/0.5/0.7/0.9, all positive, so no lobe
#: drops out.
SG5_WEIGHTS_FLAT = tuple(sg5_weights())

_NODE_X = -2000.0
_NODE_Y = 900.0


# --- DX10 DDS texture arrays -------------------------------------------------
# Blender 5.1.1 exposes **exactly one** slice — slice 0 — of an `arraySize > 1`
# DX10 DDS (measured: the array file's pixels are bit-identical to split slice 0
# and differ from every other slice; see `tests/blender_lightmap_probe.py`
# section `[slice-exposed]`).  So a mesh whose
# `lm_slice_index` is 7 would silently render page 0's lobe 0.  Rather than push
# that onto the extractor, the importer splits the array itself: pure stdlib,
# one 1 MiB file per slice, cached next to the source.

DDS_MAGIC = b"DDS "
DDS_HEADER_BYTES = 148          # 4 magic + 124 DDS_HEADER + 20 DDS_HEADER_DXT10
_DX10_ARRAYSIZE_OFF = 140       # 128 + 12
_DX10_DXGI_OFF = 128
_PITCH_OFF = 20


def dds_dx10_header(path):
    """`{dxgi, width, height, mips, arraysize}` for a DX10 DDS, else None."""
    try:
        with open(str(path), "rb") as fh:
            b = fh.read(DDS_HEADER_BYTES)
    except OSError:
        return None
    if len(b) < DDS_HEADER_BYTES or b[:4] != DDS_MAGIC or b[84:88] != b"DX10":
        return None
    height, width = struct.unpack_from("<2I", b, 12)
    mips = struct.unpack_from("<I", b, 28)[0]
    dxgi = struct.unpack_from("<I", b, _DX10_DXGI_OFF)[0]
    arraysize = struct.unpack_from("<I", b, _DX10_ARRAYSIZE_OFF)[0]
    return {"dxgi": dxgi, "width": width, "height": height,
            "mips": mips, "arraysize": max(1, arraysize)}


def split_array_slice(src, slice_index, dst):
    """Write array slice `slice_index` of a DX10 DDS out as its own arraySize-1
    DDS.  Single-mip only — which is what the shipped lightmaps are (`mips=1`).
    """
    src, dst = Path(str(src)), Path(str(dst))
    hdr_info = dds_dx10_header(src)
    if hdr_info is None:
        raise ValueError(f"{src} is not a DX10 DDS")
    if hdr_info["mips"] > 1:
        raise ValueError(
            f"{src} has {hdr_info['mips']} mips; per-slice extraction of a "
            f"mipped array is not implemented (the shipped lightmaps are mips=1)")
    arr = hdr_info["arraysize"]
    if not 0 <= slice_index < arr:
        raise IndexError(f"slice {slice_index} out of range (arraySize {arr})")
    data = src.read_bytes()
    body = len(data) - DDS_HEADER_BYTES
    per = body // arr
    if per * arr != body:
        raise ValueError(f"{src}: {body} payload bytes do not divide by {arr}")
    hdr = bytearray(data[:DDS_HEADER_BYTES])
    struct.pack_into("<I", hdr, _DX10_ARRAYSIZE_OFF, 1)
    struct.pack_into("<I", hdr, _PITCH_OFF, per)
    off = DDS_HEADER_BYTES + slice_index * per
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bytes(hdr) + data[off:off + per])
    return dst


def sg5_slice_indices(page):
    """Array slices holding page `page`'s five SG lobes.

    The engine indexes the array as `lightmapuv.z = lightmapuv.z * 5 + i`
    (i = 0..4): page-major, lobe-minor.
    """
    p = int(page)
    return [p * SG5_LOBES + i for i in range(SG5_LOBES)]


def materialise_slices(src, indices, out_dir):
    """Split the named array slices out of `src`; return their file paths.

    Cached: an existing file of the right size is reused, so re-importing does
    not rewrite megabytes every time.  Returns `[]` — never a wrong file — when
    any index is out of range or `src` is not a DX10 array.
    """
    src, out_dir = Path(str(src)), Path(str(out_dir))
    info = dds_dx10_header(src)
    if info is None:
        return []
    arr = info["arraysize"]
    want = list(indices)
    if not want or any(not 0 <= int(s) < arr for s in want):
        return []
    expect = DDS_HEADER_BYTES + (src.stat().st_size - DDS_HEADER_BYTES) // arr
    out = []
    for s in want:
        dst = out_dir / f"{src.stem}.slice{int(s):03d}.dds"
        if not (dst.exists() and dst.stat().st_size == expect):
            split_array_slice(src, int(s), dst)
        out.append(str(dst))
    return out


def materialise_page_slices(src, page, out_dir, lobes=SG5_LOBES):
    """`materialise_slices` for page `page`'s five SG lobes."""
    return materialise_slices(src, sg5_slice_indices(page)[:lobes], out_dir)


# --- small helpers -----------------------------------------------------------

def _principled_input(node, *names):
    """First matching socket. Principled v2 renamed sockets in Blender 4.0, so
    every lookup passes the new name first and the legacy name after."""
    for n in names:
        if n in node.inputs:
            return node.inputs[n]
    return None


def _set_colorspace(img, name, fallback=None):
    """Set `image.colorspace_settings.name`, returning what actually stuck.

    Never assume the write took: an OCIO config that lacks the name raises, and
    a silent miss here is a whole-scene gamma error.
    """
    for cand in (name, fallback):
        if not cand:
            continue
        try:
            img.colorspace_settings.name = cand
            return img.colorspace_settings.name
        except Exception:
            continue
    return img.colorspace_settings.name


def _load_image(pkg_dir, rel_file, colorspace, fallback=None):
    if not rel_file:
        return None, ""
    path = Path(pkg_dir) / rel_file if pkg_dir is not None else Path(rel_file)
    if not path.exists():
        return None, ""
    try:
        img = bpy.data.images.load(str(path), check_existing=True)
    except Exception:
        return None, ""
    actual = _set_colorspace(img, colorspace, fallback)
    # The HDR lightmap has no alpha; the AO maps carry data in their channels.
    # Either way Blender's default 'STRAIGHT' un-premultiplies RGB against alpha,
    # which corrupts packed data — so never leave it at the default.
    try:
        img.alpha_mode = "CHANNEL_PACKED"
    except Exception:
        pass
    return img, actual


def _rgba_sockets(node):
    """The RGBA-typed input sockets of a node, in order.

    `ShaderNodeMix` exposes one A/B pair per data type and they all share the
    names "A"/"B", so name lookup silently grabs the float pair. Selecting by
    socket `.type` is the only stable way.
    """
    return [s for s in node.inputs if s.type == "RGBA"]


def _rgba_output(node):
    for s in node.outputs:
        if s.type == "RGBA":
            return s
    return node.outputs[0]


def _mix_blend(nt, blend_type, x, y):
    """A colour mix node of `blend_type`; returns `(node, output_socket)`.

    `x` / `y` are each either a NodeSocket to link or an RGB(A) tuple. Tries the
    modern `ShaderNodeMix` (Blender 3.4+) and falls back to `ShaderNodeMixRGB`.
    """
    node = None
    try:
        node = nt.nodes.new("ShaderNodeMix")
        node.data_type = "RGBA"
        node.blend_type = blend_type
        # Factor: the float one (index 0) drives every data type.
        for s in node.inputs:
            if s.type == "VALUE" and s.name == "Factor":
                s.default_value = 1.0
                break
        slots = _rgba_sockets(node)
    except Exception:
        if node is not None:
            nt.nodes.remove(node)
        node = nt.nodes.new("ShaderNodeMixRGB")
        node.blend_type = blend_type
        node.inputs[0].default_value = 1.0
        slots = [node.inputs[1], node.inputs[2]]

    for socket, value in zip(slots, (x, y)):
        if hasattr(value, "is_output"):
            nt.links.new(value, socket)
        elif value is not None:
            v = tuple(value)
            socket.default_value = (v + (1.0,) * 4)[:4] if len(v) < 4 else v[:4]
    return node, _rgba_output(node)


def _mix_multiply(nt, x, y):
    """A colour MULTIPLY node; returns `(node, output_socket)`."""
    return _mix_blend(nt, "MULTIPLY", x, y)


def _broadcast_red(nt, color_socket):
    """(R,R,R) from a colour socket: Separate Color -> Combine Color.

    Returns `([nodes], out_socket)` or None if this Blender has neither node
    pair. Needed because BC5 puts data in R and G and leaves B at 0 — a straight
    colour multiply would zero the blue channel.
    """
    for sep_id, com_id in (("ShaderNodeSeparateColor", "ShaderNodeCombineColor"),
                           ("ShaderNodeSeparateRGB", "ShaderNodeCombineRGB")):
        try:
            sep = nt.nodes.new(sep_id)
            com = nt.nodes.new(com_id)
        except Exception:
            continue
        try:
            nt.links.new(color_socket, sep.inputs[0])
            red = sep.outputs[0]
            for i in range(3):
                nt.links.new(red, com.inputs[i])
            sep.location = (_NODE_X + 400, _NODE_Y - 320)
            com.location = (_NODE_X + 560, _NODE_Y - 320)
            com.label = "ao0.R broadcast"
            return [sep, com], com.outputs[0]
        except Exception:
            nt.nodes.remove(sep)
            nt.nodes.remove(com)
    return None


def _albedo_source(nt, bsdf):
    """What is currently driving Base Color: (socket_or_rgba_tuple, kind)."""
    base_in = _principled_input(bsdf, "Base Color")
    if base_in is None:
        return (1.0, 1.0, 1.0, 1.0), "missing"
    if base_in.links:
        return base_in.links[0].from_socket, "texture"
    try:
        return tuple(base_in.default_value), "factor"
    except Exception:
        return (1.0, 1.0, 1.0, 1.0), "factor"


def _zero_bsdf_response(nt, bsdf):
    """Make the Principled BSDF contribute nothing under scene lights.

    Base Color black, Metallic 0, Roughness 1, and every specular-ish weight 0.
    That is what "unlit" means here: the surface's whole appearance comes from
    the Emission sockets, exactly as a fully-baked engine surface does.
    """
    zeroed = []
    for names, value in (
        (("Base Color",), (0.0, 0.0, 0.0, 1.0)),
        (("Metallic",), 0.0),
        (("Roughness",), 1.0),
        (("Specular IOR Level", "Specular"), 0.0),
        (("Coat Weight", "Clearcoat"), 0.0),
        (("Sheen Weight", "Sheen"), 0.0),
        (("Transmission Weight", "Transmission"), 0.0),
    ):
        sock = _principled_input(bsdf, *names)
        if sock is None:
            continue
        for link in list(sock.links):
            nt.links.remove(link)
        try:
            sock.default_value = value
            zeroed.append(sock.name)
        except Exception:
            pass
    return zeroed


# --- the entry point ---------------------------------------------------------

def wire_lightmap(mat, node_tree, bsdf, lm_spec, opts=None) -> dict:
    """Wire `lm_spec`'s baked lightmap into `mat`'s node graph.

    Args:
        mat: the `bpy.types.Material` (used for provenance custom properties).
        node_tree: `mat.node_tree`.
        bsdf: the Principled BSDF node to drive.
        lm_spec: `le_mesh.lightmap.build_lightmap_spec(...)` output. `{}`/None
            means "this mesh is not lightmapped" and is a clean no-op.
        opts: importer options. Recognised keys:
            `lightmap_mode`        "baked" (default) | "ambient" | "none"
            `lightmap_intensity`   float, default 1.0 — multiplies Emission Strength
            `lightmap_ambient_weight` float, default 1.0 — MODE_AMBIENT only
            `lightmap_use_ao`      bool, default False — multiply ao0.R in.
                                   OFF because the ENGINE does not do it for a
                                   lightmapped surface: `ao0.R` is the H-basis
                                   band-0 term and `saturate(DotH4(0, (h.x,0,0,0)))`
                                   reduces to exactly `ao0.R`, but the engine
                                   applies that scalar only on the
                                   irradiance-volume branch. On the lightmap path
                                   the AO pair drives ambient SPECULAR only.
            `lightmap_basis`       "sg5" (default) | "single". "sg5" is the
                                   engine's own diffuse math and needs the five
                                   per-lobe slices of this mesh's page — from
                                   `spec["color_slices"]`, from
                                   `color["slices"]`, or split out of the array
                                   DDS automatically. It falls back to "single"
                                   (lobe 0 alone) and says so in the report.
            `lightmap_auto_split`  bool, default True — split an arraySize>1
                                   colour DDS into per-page slices. With it off,
                                   every mesh renders page 0 (Blender exposes
                                   only slice 0 of an array DDS).
            `lightmap_slice_dir`   where the split slices are cached
                                   (default `<dds dir>/_lmslices`)
            `lightmap_colorspace`  override for the HDR map
            `lightmap_uv_layer`    override for the UV set (default from lm_spec)
            `pkg_dir`              base dir the spec's `file` paths are relative to

    Returns a report dict — never raises for a missing texture or an old Blender:
        {"wired": bool, "mode": str, "reason": str, "colorspace": str,
         "uv_layer": str, "image": str, "albedo_source": str,
         "basis": str, "basis_reason": str, "lobes": int, "lobe_weights": [float],
         "emission_strength": float, "zeroed": [socket names],
         "nodes": [node names], "ao_used": bool}
    """
    opts = opts or {}
    mode = str(opts.get("lightmap_mode", DEFAULT_MODE) or DEFAULT_MODE).lower()
    report = {
        "wired": False, "mode": mode, "reason": "", "colorspace": "",
        "uv_layer": "", "image": "", "albedo_source": "", "ao_used": False,
        "emission_added": False, "basis": BASIS_SINGLE, "basis_reason": "",
        "lobes": 0, "lobe_weights": [], "page": 0, "auto_split": False,
        "slice_files": [],
        "emission_strength": 0.0, "zeroed": [], "nodes": [],
    }

    if not lm_spec:
        report["reason"] = "mesh is not lightmapped (lightmapindex == 0xffffffff or unresolved)"
        return report
    if mode not in MODES:
        report["reason"] = f"unknown lightmap_mode {mode!r} (want one of {MODES})"
        return report

    # provenance survives even when nothing is wired, so a missing texture is
    # visible in the .blend rather than silently absent.
    color = lm_spec.get("color") or {}
    try:
        mat["le_lightmap_index"] = str(lm_spec.get("lightmap_index", ""))
        mat["le_lightmap_slice"] = str(lm_spec.get("slice_index", ""))
        mat["le_lightmap_tex"] = color.get("hash", "")
        mat["le_lightmap_mode"] = mode
    except Exception:
        pass

    if mode == MODE_NONE:
        report["reason"] = "lightmap_mode == 'none'"
        return report
    if not color.get("file"):
        report["reason"] = (
            "lightmap texture not extracted "
            f"(hash {color.get('hash', '?')}) — extract it and re-import")
        return report
    if color.get("dxgi_unexpected"):
        report["reason"] = (
            f"lightmap texture DXGI {color.get('dxgi')} != expected "
            f"{color.get('expected_dxgi')} (BC6H_UF16) — role mapping suspect")
        return report

    nt = node_tree
    uv_layer = opts.get("lightmap_uv_layer") or lm_spec.get("uv_layer") or UV_LAYER
    colorspace = opts.get("lightmap_colorspace") or color.get("colorspace") or COLORSPACE_LIGHTMAP
    pkg_dir = opts.get("pkg_dir")

    # --- which basis? -------------------------------------------------------
    # The shipped colour map is an SG5 texture ARRAY (5 tangent-space radiance
    # lobes per lightmap page).  If the spec hands us the five per-lobe files we
    # reproduce the engine's DiffuseTermSG sum; otherwise we fall back to lobe 0
    # alone and say so in the report.
    basis = str(opts.get("lightmap_basis")
                or color.get("basis") or lm_spec.get("basis")
                or DEFAULT_BASIS).lower()
    slices = [s for s in (color.get("slices") or []) if s]

    # The shipped colour map is an arraySize>1 DDS and Blender shows only slice
    # 0 of it, so if the spec did not already hand us per-slice files, split them
    # out here. This is also what makes `lm_slice_index` mean anything at all.
    page = lm_spec.get("slice_index", 0)
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 0
    if page < 0 or page == 0xFFFFFFFF:
        page = 0
    report["page"] = page
    if not slices and opts.get("lightmap_auto_split", True):
        src = Path(str(pkg_dir)) / color["file"] if pkg_dir else Path(color["file"])
        info = dds_dx10_header(src)
        if info and info["arraysize"] > 1:
            cache = opts.get("lightmap_slice_dir") or (src.parent / "_lmslices")
            # `le_mesh.lightmap.build_lightmap_spec` derives the slice INDICES
            # from the two arraysizes (`spec["color_slices"]`). Prefer them over
            # our own page arithmetic so the two modules cannot drift apart.
            want = [i for i in (lm_spec.get("color_slices") or [])
                    if isinstance(i, int)]
            try:
                slices = (materialise_slices(src, want, cache) if want
                          else materialise_page_slices(src, page, cache))
            except Exception as exc:
                report["basis_reason"] = f"array split failed: {exc}"
                slices = []
            if slices:
                report["auto_split"] = True
    report["slice_files"] = list(slices)

    if basis == BASIS_SG5 and len(slices) >= SG5_LOBES:
        lobe_files = list(slices[:SG5_LOBES])
    else:
        if basis == BASIS_SG5:
            report["basis_reason"] = (
                f"lightmap_basis='sg5' asked for but only {len(slices)} per-lobe "
                f"file(s) are available, need {SG5_LOBES}")
        basis = BASIS_SINGLE
        # even single-lobe wiring must use THIS mesh's page, not whatever slice
        # the array file happens to expose.
        lobe_files = [slices[0]] if slices else [color["file"]]
    report["basis"] = basis

    images = []
    actual_cs = ""
    for f in lobe_files:
        im, cs = _load_image(pkg_dir, f, colorspace, COLORSPACE_LIGHTMAP_FALLBACK)
        if im is None:
            report["reason"] = f"could not load lightmap image {f!r}"
            return report
        images.append(im)
        if len(images) == 1:
            actual_cs = cs
    report["colorspace"] = actual_cs
    report["image"] = images[0].name
    report["uv_layer"] = uv_layer
    report["lobes"] = len(images)

    created = []

    uvnode = nt.nodes.new("ShaderNodeUVMap")
    uvnode.uv_map = uv_layer
    uvnode.location = (_NODE_X, _NODE_Y)
    uvnode.label = "lightmap UV (uv1)"
    created.append(uvnode)

    tex_nodes = []
    for i, im in enumerate(images):
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = im
        tex.location = (_NODE_X + 220, _NODE_Y - i * 300)
        tex.label = ("lightmap (BC6H_UF16 HDR)" if basis == BASIS_SINGLE
                     else f"lightmap SG5 lobe {i} (BC6H_UF16 HDR)")
        try:
            tex.interpolation = "Linear"
            tex.extension = "EXTEND"
        except Exception:
            pass
        nt.links.new(uvnode.outputs["UV"], tex.inputs["Vector"])
        created.append(tex)
        tex_nodes.append(tex)

    if basis == BASIS_SG5:
        weights = list(SG5_WEIGHTS_FLAT)
        report["lobe_weights"] = weights
        lm_socket = None
        for i, (tex, w) in enumerate(zip(tex_nodes, weights)):
            scale, s = _mix_multiply(nt, tex.outputs["Color"], (w, w, w, 1.0))
            scale.location = (_NODE_X + 520, _NODE_Y - i * 300)
            scale.label = f"lobe {i} x {w:.6f}"
            created.append(scale)
            if lm_socket is None:
                lm_socket = s
            else:
                add, lm_socket = _mix_blend(nt, "ADD", lm_socket, s)
                add.location = (_NODE_X + 700, _NODE_Y - i * 300 + 150)
                add.label = f"SG5 sum 0..{i}"
                created.append(add)
    else:
        lm_socket = tex_nodes[0].outputs["Color"]

    # optional AO multiply — OFF by default, and for a reason read out of the
    # engine's own maths rather than an unknown one.  `ao0.xy` + `ao1.xy` are four
    # H-basis occlusion coefficients; the scalar diffuse AO is
    # `saturate(DotH4(0, (h.x,0,0,0)))`, which — because `band0scale == sqrt(2pi)`
    # and `DotH4`'s band-0 factor is `1/sqrt(2pi)` — is exactly `ao0.R`.  But the
    # engine multiplies that scalar into the ambient diffuse ONLY on the
    # irradiance-volume branch, never on the lightmap path.  So for a lightmapped
    # surface, multiplying AO in would
    # DOUBLE-darken relative to the shipped look.
    ao = lm_spec.get("ao0") or {}
    if opts.get("lightmap_use_ao", False) and ao.get("file"):
        ao_img, _ = _load_image(pkg_dir, ao["file"],
                                ao.get("colorspace", COLORSPACE_DATA))
        if ao_img is not None:
            ao_tex = nt.nodes.new("ShaderNodeTexImage")
            ao_tex.image = ao_img
            ao_tex.location = (_NODE_X + 220, _NODE_Y - 320)
            ao_tex.label = ("lightmap ao0 (BC5: R = H-basis band 0 = the scalar "
                            "diffuse AO; the engine does NOT apply it on the "
                            "lightmap path)")
            nt.links.new(uvnode.outputs["UV"], ao_tex.inputs["Vector"])
            created.append(ao_tex)
            # ao0 is BC5: G carries a band-1 coefficient and B is 0. Multiplying
            # the Color output straight in would zero the blue channel, so take
            # R alone and broadcast it.
            grey = _broadcast_red(nt, ao_tex.outputs["Color"])
            if grey is not None:
                node, sock = grey
                created.extend(node)
                ao_mix, lm_socket = _mix_multiply(nt, lm_socket, sock)
                ao_mix.location = (_NODE_X + 520, _NODE_Y - 160)
                ao_mix.label = "lightmap x ao0.R"
                created.append(ao_mix)
                report["ao_used"] = True

    albedo, albedo_kind = _albedo_source(nt, bsdf)
    report["albedo_source"] = albedo_kind

    mix, mixed = _mix_multiply(nt, albedo, lm_socket)
    mix.location = (_NODE_X + 760, _NODE_Y)
    mix.label = "albedo x lightmap"
    created.append(mix)

    em_col = _principled_input(bsdf, "Emission Color", "Emission")
    em_str = _principled_input(bsdf, "Emission Strength")
    if em_col is None:
        for n in created:
            nt.nodes.remove(n)
        report["reason"] = "Principled BSDF has no Emission Color socket"
        return report

    # An emissive material already driving Emission would be overwritten; add
    # instead of replacing so §4f emission survives the lightmap.
    if em_col.links:
        prev = em_col.links[0].from_socket
        add = nt.nodes.new("ShaderNodeMix")
        try:
            add.data_type = "RGBA"
            add.blend_type = "ADD"
            for s in add.inputs:
                if s.type == "VALUE" and s.name == "Factor":
                    s.default_value = 1.0
                    break
            slots = _rgba_sockets(add)
            nt.links.new(prev, slots[0])
            nt.links.new(mixed, slots[1])
            mixed = _rgba_output(add)
            add.location = (_NODE_X + 1000, _NODE_Y)
            add.label = "emissive + lightmap"
            created.append(add)
            report["emission_added"] = True
        except Exception:
            nt.nodes.remove(add)
    nt.links.new(mixed, em_col)

    intensity = float(opts.get("lightmap_intensity", DEFAULT_INTENSITY))
    if mode == MODE_AMBIENT:
        intensity *= float(opts.get("lightmap_ambient_weight", DEFAULT_AMBIENT_WEIGHT))
    if em_str is not None and not em_str.links:
        em_str.default_value = intensity
    report["emission_strength"] = intensity

    if mode == MODE_BAKED:
        # unlit: the BSDF must not add a second, unbaked copy of the lighting.
        report["zeroed"] = _zero_bsdf_response(nt, bsdf)

    report["wired"] = True
    report["nodes"] = [n.name for n in created]
    try:
        mat["le_lightmap_wired"] = True
        mat["le_lightmap_colorspace"] = actual_cs
    except Exception:
        pass
    return report
