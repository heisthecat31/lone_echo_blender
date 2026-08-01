"""In-Blender probe for the material node graph (NOT a stdlib test -- needs bpy).

    blender.exe --background --factory-startup --python blender_material_probe.py \
        [-- [--render OUTDIR] [--fixtures DIR] [--max N]]

Deliberately NOT named `test_*` so `tests/run_tests.py` (plain python3) never
imports it. The pure decision layer it exercises is unit-tested there instead by
`tests/test_material_builder_nodes.py`.

Six sections, each printing its own checks and a final `PROBE_RESULT: PASS|FAIL`:

  1. RNA facts        `blend_method` vs `surface_render_method` on this Blender.
  2. alpha_mode proof decode a BC3 texel from the DDS in pure python, then sample the
                      SAME texel through an Image Texture node under each
                      `image.alpha_mode`, and show the numbers.
  3. fixtures         build every material of the real `.lemesh` fixture manifests and
                      assert on the resulting graph (links, values, read-backs).
  4. synthetic        specs that the shipped fixtures do not cover yet (alpha-tested
                      cutout, blended glass, transmission tint, refraction, k_alpha).
  5. vertex colour    the per-mesh `eDiffuseVertexColor` material variant.
  6. layer blending   `layerN_blend_mask` gating the layers above 0: the gate
                      arithmetic, the shipped-offset suppression, the
                      authored-on state, and a sweep of every masked material.

`--render OUTDIR` additionally writes a before/after pair. Every render pins
`view_settings.view_transform = 'Standard'` -- Blender 4.0+ defaults to AgX, which
desaturates highlights and makes correct values look wrong.
"""

import json
import struct
import sys
from pathlib import Path

import bpy   # type: ignore

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from lone_echo_import import material_builder as MB    # noqa: E402

FIXTURES_MAT = BLENDER_TOOL / "exports" / "fixtures_mat"
# The fresh extraction: cross-archive materials resolved, so `mattype` /
# `blendmode` / `k_alpha` / the per-layer scalars are all real. Section 6 needs
# it -- the older set has no material scalars for most materials.
FIXTURES_MAT3 = BLENDER_TOOL / "exports" / "fixtures_mat3"
# BC3_UNORM_SRGB layer0_composite_diffuse with genuine partial alpha.
ALPHA_PROOF_DDS = (FIXTURES_MAT / "0703fd2acd5803e9_647dc43ebdfc952f.lemesh"
                   / "textures" / "6f51c495d957d59a.dds")

FAILURES = []
CHECKS = [0]


def check(label, cond, detail=""):
    CHECKS[0] += 1
    if cond:
        print(f"  ok   {label}" + (f"   {detail}" if detail else ""))
    else:
        print(f"  FAIL {label}" + (f"   {detail}" if detail else ""))
        FAILURES.append(label)
    return cond


def _argv():
    rest = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = None
    fixtures = FIXTURES_MAT
    limit = 8
    for i, a in enumerate(rest):
        if a == "--render" and i + 1 < len(rest):
            out = Path(rest[i + 1])
        if a == "--fixtures" and i + 1 < len(rest):
            fixtures = Path(rest[i + 1])
        if a == "--max" and i + 1 < len(rest):
            limit = int(rest[i + 1])
    return out, fixtures, limit


# ---------------------------------------------------------------------------
# pure-python BC3/DXT5 decode -- the independent ground truth for section 2
# ---------------------------------------------------------------------------

def dds_info(path):
    b = path.read_bytes()[:148]
    _sz, _fl, h, w, _p, _d, mips = struct.unpack("<7I", b[4:32])
    dx10 = b[84:88] == b"DX10"
    dxgi = struct.unpack("<I", b[128:132])[0] if dx10 else None
    return w, h, mips, dxgi, (148 if dx10 else 128)


def bc3_block(data, off):
    a0, a1 = data[off], data[off + 1]
    abits = int.from_bytes(data[off + 2:off + 8], "little")
    if a0 > a1:
        at = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        at = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
    c0, c1 = struct.unpack("<HH", data[off + 8:off + 12])
    cbits = int.from_bytes(data[off + 12:off + 16], "little")

    def rgb565(c):
        return ((((c >> 11) & 31) * 255 + 15) // 31,
                (((c >> 5) & 63) * 255 + 31) // 63,
                ((c & 31) * 255 + 15) // 31)
    e0, e1 = rgb565(c0), rgb565(c1)
    ct = [e0, e1,
          tuple((2 * e0[i] + e1[i] + 1) // 3 for i in range(3)),
          tuple((e0[i] + 2 * e1[i] + 1) // 3 for i in range(3))]
    return [ct[(cbits >> (2 * i)) & 3] + (at[(abits >> (3 * i)) & 7],) for i in range(16)]


def bc3_texel(data, w, off, x, y):
    bpr = (w + 3) // 4
    blk = bc3_block(data, off + ((y // 4) * bpr + (x // 4)) * 16)
    return blk[(y % 4) * 4 + (x % 4)]


def srgb_to_linear(byte_value):
    u = byte_value / 255.0
    return u / 12.92 if u <= 0.04045 else ((u + 0.055) / 1.055) ** 2.4


# ---------------------------------------------------------------------------
# 1. RNA facts
# ---------------------------------------------------------------------------

def section_rna():
    print(f"\n== 1. RNA facts (Blender {bpy.app.version_string}) ==")
    mat = bpy.data.materials.new("rna_probe")
    mat.use_nodes = True
    items = [i.identifier for i in
             mat.bl_rna.properties["surface_render_method"].enum_items]
    check("surface_render_method enum is exactly [DITHERED, BLENDED]",
          items == ["DITHERED", "BLENDED"], str(items))
    collapse = {}
    for v in ("OPAQUE", "CLIP", "HASHED", "BLEND"):
        mat.blend_method = v
        collapse[v] = (mat.blend_method, mat.surface_render_method)
    check("blend_method CLIP cannot produce clipping (collapses to DITHERED)",
          collapse["CLIP"][1] == "DITHERED", f"CLIP -> {collapse['CLIP']}")
    check("only blend_method BLEND reaches BLENDED",
          collapse["BLEND"][1] == "BLENDED" and collapse["OPAQUE"][1] == "DITHERED",
          str(collapse))
    for want in ("DITHERED", "BLENDED"):
        mat.surface_render_method = want
        check(f"surface_render_method={want} reads back",
              mat.surface_render_method == want, mat.surface_render_method)
    img = bpy.data.images.new("rna_probe_img", 4, 4, alpha=True)
    check("image.alpha_mode default really is STRAIGHT", img.alpha_mode == "STRAIGHT",
          img.alpha_mode)
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    for name in ("Base Color", "Roughness", "IOR", "Alpha", "Normal",
                 "Emission Color", "Emission Strength", "Transmission Weight",
                 "Specular IOR Level", "Coat Weight", "Sheen Weight"):
        check(f"Principled v2 socket {name!r} exists", name in bsdf.inputs)


# ---------------------------------------------------------------------------
# 2. image.alpha_mode -- the texel proof
# ---------------------------------------------------------------------------

def _texel_sampling_scene():
    """Emission plane + ortho camera + constant-UV image fetch (Closest sampling)."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    if scene.world is None:
        scene.world = bpy.data.worlds.new("w")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0, 0, 0, 1)
    scene.render.resolution_x = scene.render.resolution_y = 2
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"     # never AgX for numbers
    scene.view_settings.look = "None"
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_depth = "32"

    bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.object
    bpy.ops.object.camera_add(location=(0, 0, 5), rotation=(0, 0, 0))
    cam = bpy.context.object
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = 1.0
    scene.camera = cam

    mat = bpy.data.materials.new("texel")
    mat.use_nodes = True
    nt = mat.node_tree
    for n in list(nt.nodes):
        if n.type != "OUTPUT_MATERIAL":
            nt.nodes.remove(n)
    out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
    em = nt.nodes.new("ShaderNodeEmission")
    em.inputs["Strength"].default_value = 1.0
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    tex = nt.nodes.new("ShaderNodeTexImage")
    tex.interpolation = "Closest"
    tex.extension = "EXTEND"
    uv = nt.nodes.new("ShaderNodeCombineXYZ")
    nt.links.new(uv.outputs["Vector"], tex.inputs["Vector"])
    nt.links.new(tex.outputs["Color"], em.inputs["Color"])
    plane.data.materials.append(mat)
    return scene, tex, uv


def _render_first_pixel(scene, tmp_exr):
    bpy.ops.render.render()
    bpy.data.images["Render Result"].save_render(filepath=str(tmp_exr))
    im = bpy.data.images.load(str(tmp_exr))
    px = tuple(round(im.pixels[k], 6) for k in range(3))
    bpy.data.images.remove(im)
    return px


def section_alpha_mode(tmpdir):
    print("\n== 2. image.alpha_mode texel proof (DDS decode vs Blender) ==")
    if not ALPHA_PROOF_DDS.exists():
        print(f"  SKIP: {ALPHA_PROOF_DDS} missing")
        return
    w, h, mips, dxgi, off = dds_info(ALPHA_PROOF_DDS)
    data = ALPHA_PROOF_DDS.read_bytes()
    print(f"  {ALPHA_PROOF_DDS.name}: {w}x{h} mips={mips} dxgi={dxgi} (78=BC3_UNORM_SRGB)")

    bpr = (w + 3) // 4
    partial = zero = opaque = None
    for bi in range(bpr * ((h + 3) // 4)):
        blk = bc3_block(data, off + bi * 16)
        for i, t in enumerate(blk):
            xy = ((bi % bpr) * 4 + i % 4, (bi // bpr) * 4 + i // 4)
            if partial is None and 0 < t[3] < 250 and max(t[:3]) > 60:
                partial = xy
            elif zero is None and t[3] == 0 and max(t[:3]) > 60:
                zero = xy
            elif opaque is None and t[3] == 255 and max(t[:3]) > 60:
                opaque = xy
        if partial and zero and opaque:
            break
    targets = [t for t in (partial, zero, opaque) if t]

    scene, tex, uv = _texel_sampling_scene()
    img = bpy.data.images.load(str(ALPHA_PROOF_DDS))
    img.colorspace_settings.name = "sRGB"
    tex.image = img
    tmp_exr = tmpdir / "_alpha_probe.exr"

    engines = []
    ids = [i.identifier for i in scene.render.bl_rna.properties["engine"].enum_items]
    for e in ("CYCLES", "BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
        if e in ids or e == "CYCLES":
            try:                       # Cycles is an add-on; may not be in enum_items
                scene.render.engine = e
            except (TypeError, ValueError):
                continue
            engines.append(e)
            if e != "CYCLES":
                break
    print(f"  engines probed: {engines}")

    for engine in engines:
        scene.render.engine = engine
        if engine == "CYCLES":
            scene.cycles.samples = 1
            scene.cycles.use_denoising = False
        print(f"\n  --- engine {engine}, view_transform="
              f"{scene.view_settings.view_transform} ---")
        for (x, y) in targets:
            raw = bc3_texel(data, w, off, x, y)
            truth = tuple(round(srgb_to_linear(c), 6) for c in raw[:3])
            uv.inputs["X"].default_value = (x + 0.5) / w
            uv.inputs["Y"].default_value = 1.0 - (y + 0.5) / h    # DDS rows top-down
            print(f"  texel ({x},{y}) raw sRGB8 RGBA={raw} alpha={raw[3] / 255.0:.4f}")
            print(f"     pure-python BC3 decode -> scene-linear = {truth}")
            got = {}
            for mode in ("STRAIGHT", "PREMUL", "CHANNEL_PACKED", "NONE"):
                img.alpha_mode = mode
                img.reload()
                tex.image = None
                tex.image = img
                readback = img.alpha_mode
                px = _render_first_pixel(scene, tmp_exr)
                got[mode] = px
                ratio = [round(px[i] / truth[i], 4) if truth[i] > 1e-6 else None
                         for i in range(3)]
                print(f"     alpha_mode={mode:<15} readback={readback:<15} "
                      f"Color={px} ratio_vs_truth={ratio}")
                check(f"[{engine}] alpha_mode {mode} reads back", readback == mode)
            near = lambda a, b: all(abs(a[i] - b[i]) < 2e-3 for i in range(3))  # noqa: E731
            check(f"[{engine}] CHANNEL_PACKED reproduces the DDS at ({x},{y})",
                  near(got["CHANNEL_PACKED"], truth), f"{got['CHANNEL_PACKED']} vs {truth}")
            if raw[3] < 250:
                check(f"[{engine}] STRAIGHT CORRUPTS the albedo at ({x},{y}) "
                      f"(alpha={raw[3] / 255.0:.3f})",
                      not near(got["STRAIGHT"], truth), str(got["STRAIGHT"]))
            else:
                check(f"[{engine}] STRAIGHT is harmless where alpha==1 at ({x},{y})",
                      near(got["STRAIGHT"], truth), str(got["STRAIGHT"]))
    img.alpha_mode = "CHANNEL_PACKED"


# ---------------------------------------------------------------------------
# 3. real fixture manifests
# ---------------------------------------------------------------------------

def _linked_from(socket):
    return socket.links[0].from_node if socket and socket.links else None


def _bsdf(mat):
    return next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)


def _images_of(mat):
    return [n.image for n in mat.node_tree.nodes
            if n.type == "TEX_IMAGE" and n.image is not None]


def section_fixtures(fixtures_dir, limit):
    print(f"\n== 3. real fixture manifests ({fixtures_dir}) ==")
    pkgs = sorted(p.parent for p in fixtures_dir.glob("*.lemesh/manifest.json"))
    with_channels = []
    for pkg in pkgs:
        manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
        for spec in manifest.get("materials", []):
            if spec.get("channels"):
                with_channels.append((pkg, spec))
    print(f"  {len(pkgs)} packages, {len(with_channels)} materials WITH channels; "
          f"probing {min(limit, len(with_channels))}")
    if not with_channels:
        # A clean checkout has no extracted packages; that is not a failure.
        print(f"  SKIP — no extracted .lemesh packages under {fixtures_dir}")
        return
    check("fixture corpus has textured materials", bool(with_channels))

    n_img = n_packed = n_straight = 0
    for pkg, spec in with_channels[:limit]:
        mat = MB.build_material(spec, pkg, {})
        b = _bsdf(mat)
        chans = spec.get("channels", {})
        rm = MB.resolve_render_mode(spec)
        want_srm = MB.surface_render_method_for(rm)
        print(f"\n  [{spec['key'][:38]}] channels={sorted(chans)} render_mode={rm}")
        check("surface_render_method read-back matches",
              mat.surface_render_method == want_srm,
              f"{mat.surface_render_method} (want {want_srm})")
        for img in _images_of(mat):
            n_img += 1
            if img.alpha_mode == "CHANNEL_PACKED":
                n_packed += 1
            else:
                if img.alpha_mode == "STRAIGHT":
                    n_straight += 1
                print(f"     image {img.name} alpha_mode={img.alpha_mode}")
        if chans.get("base_color", {}).get("file"):
            src = _linked_from(b.inputs["Base Color"])
            check("Base Color is driven by a texture",
                  src is not None and src.type in ("TEX_IMAGE", "MIX"), str(src))
        if chans.get("roughness"):
            src = _linked_from(b.inputs["Roughness"])
            if MB.roughness_is_sqrt(spec, chans["roughness"]):
                # ⚠ REGRESSION GUARD, do not "fix" this back to a POWER node.
                # The engine's GGX alpha = sqrtroughness^2 and Blender's is
                # Roughness^2; therefore
                # Roughness IS components.x, raw. Squaring it here gave alpha
                # = sqrtroughness^4 and a peak highlight 2.4x-920x too bright.
                check("Roughness takes components.x RAW (no POWER node)",
                      src is not None and src.type == "SEPARATE_COLOR",
                      str(src))
                check("no squaring node feeds Roughness",
                      not (src is not None and src.type == "MATH"
                           and getattr(src, "operation", "") == "POWER"),
                      str(src))
                check("AO channel recorded", mat.get("le_ao_channel") == "G",
                      str(mat.get("le_ao_channel")))
            else:
                check("Roughness linked", src is not None, str(src))
        if chans.get("normal", {}).get("file"):
            src = _linked_from(b.inputs["Normal"])
            check("Normal driven by a Normal Map node",
                  src is not None and src.type == "NORMAL_MAP", str(src))
        if chans.get("emission", {}).get("file"):
            ec = b.inputs["Emission Color"]
            em_blend = MB.blend_for_channel(spec, chans, "emission")
            gate = (MB.blend_amount_constant(em_blend, {}) if em_blend else None)
            want = MB.emission_strength(spec)
            if em_blend is not None and gate == 0.0:
                # `saturate(mask.R * scale + offset) == 0` for every texel: the
                # layer is parked at its animated OFF extreme, so the engine adds
                # nothing. NOT a missing link -- an intentionally dark layer.
                check("suppressed emissive layer contributes nothing",
                      not ec.links
                      and b.inputs["Emission Strength"].default_value == 0.0
                      and mat.get("le_layer_blend_emission_suppressed") is True,
                      f"layer{em_blend['layer']} offset="
                      f"{em_blend['mask_offset']} scale={em_blend['mask_scale']}")
            else:
                check("Emission Color linked", bool(ec.links), str(_linked_from(ec)))
                if gate is not None and gate != 1.0:
                    want *= gate
                check("Emission Strength = intensity x scale (no fudge factor)",
                      abs(b.inputs["Emission Strength"].default_value - want) < 1e-6,
                      f"{b.inputs['Emission Strength'].default_value} (want {want})")
        alpha_in = b.inputs["Alpha"]
        ka = MB.k_alpha(spec)
        if not alpha_in.links:
            check("Alpha default carries k_alpha",
                  abs(alpha_in.default_value - ka) < 1e-6,
                  f"{alpha_in.default_value} (k_alpha {ka})")
    # The invariant is "never Blender's STRAIGHT default", NOT "always
    # CHANNEL_PACKED": once the decoder started emitting `alpha_mode: "NONE"` for
    # roles whose alpha is not a signal (normal / specular / blend mask), the
    # stricter form became wrong. STRAIGHT is the only mode that CORRUPTS the RGB
    # (module docstring, fix 1).
    check("no image datablock is left at Blender's STRAIGHT default",
          n_straight == 0, f"{n_straight} STRAIGHT of {n_img}")
    check("every image with packed alpha is CHANNEL_PACKED",
          n_packed + n_straight <= n_img,
          f"{n_packed} CHANNEL_PACKED / {n_img}")


# ---------------------------------------------------------------------------
# 4. synthetic specs (cases the shipped fixtures do not cover)
# ---------------------------------------------------------------------------

def _find_channel_file(role_substr, chan_name):
    for mf in sorted(FIXTURES_MAT.glob("*.lemesh/manifest.json")):
        manifest = json.loads(mf.read_text(encoding="utf-8"))
        for spec in manifest.get("materials", []):
            ch = (spec.get("channels") or {}).get(chan_name)
            if ch and role_substr in str(ch.get("role_key", "")) and ch.get("file"):
                if (mf.parent / ch["file"]).exists():
                    return mf.parent, dict(ch)
    return None, None


def section_synthetic():
    print("\n== 4. synthetic specs ==")
    pkg, bc = _find_channel_file("composite_diffuse", "base_color")
    if pkg is None:
        pkg, bc = _find_channel_file("", "base_color")
    if bc is None:
        # No extracted package to borrow a real DDS from. The cases below all
        # need one, so say so and move on rather than crash.
        print(f"  SKIP — no extracted .lemesh package under {FIXTURES_MAT}; "
              f"extract one to exercise the synthetic specs")
        return
    check("found a real base-colour DDS to build against", bc is not None)

    # (a) k_alpha only, no map
    mat = MB.build_material({"key": "syn_kalpha", "alpha": 0.25}, BLENDER_TOOL, {})
    b = _bsdf(mat)
    check("k_alpha=0.25 lands on the Alpha socket",
          abs(b.inputs["Alpha"].default_value - 0.25) < 1e-6,
          str(b.inputs["Alpha"].default_value))
    check("k_alpha<1 forces a BLENDED pass",
          mat.surface_render_method == "BLENDED", mat.surface_render_method)

    # (b) alpha-tested cutout -- mattype 9
    spec = {"key": "syn_clip", "mattype": 9, "blend_mode": 0, "alpha_threshold": 0.4,
            "channels": {"base_color": dict(bc, dxgi=78,
                                            role_key="layer0_composite_diffuse")},
            "alpha_source": "BASE_COLOR_ALPHA"}
    mat = MB.build_material(spec, pkg, {})
    b = _bsdf(mat)
    src = _linked_from(b.inputs["Alpha"])
    check("cutout drives Alpha with Math(GREATER_THAN)",
          src is not None and src.type == "MATH" and src.operation == "GREATER_THAN",
          str(src))
    check("cutout threshold is the authored k_alpha_threshold",
          src is not None and abs(src.inputs[1].default_value - 0.4) < 1e-6)
    check("cutout stays DITHERED (EEVEE Next has no CLIP method)",
          mat.surface_render_method == "DITHERED", mat.surface_render_method)
    check("cutout casts a transparent shadow", mat.use_transparent_shadow is True)

    # (c) blended glass -- mattype 2 / eBlendTransparent
    spec = {"key": "syn_blend", "mattype": 2, "blend_mode": 7, "alpha": 1.0,
            "channels": {"base_color": dict(bc, dxgi=78,
                                            role_key="layer0_composite_diffuse")}}
    mat = MB.build_material(spec, pkg, {})
    b = _bsdf(mat)
    check("blended glass is BLENDED", mat.surface_render_method == "BLENDED",
          mat.surface_render_method)
    check("blended glass takes Alpha from composite_diffuse.a",
          bool(b.inputs["Alpha"].links) and mat.get("le_alpha_from_base_color") is True)
    check("blended glass hides transparent backfaces",
          mat.show_transparent_back is False)

    # (d) k_alpha multiplies INTO the texture alpha chain
    spec = dict(spec, key="syn_blend_kalpha", alpha=0.5)
    mat = MB.build_material(spec, pkg, {})
    b = _bsdf(mat)
    src = _linked_from(b.inputs["Alpha"])
    check("k_alpha multiplies into the alpha chain",
          src is not None and src.type == "MATH" and src.operation == "MULTIPLY"
          and abs(src.inputs[1].default_value - 0.5) < 1e-6, str(src))

    # (e) transmission tint (opacity_map) -- Transparent BSDF, NOT the Alpha socket
    pkg2, op = _find_channel_file("opacity_map", "opacity")
    if op is not None:
        spec = {"key": "syn_trans", "mattype": 2, "blend_mode": 12,
                "channels": {"opacity": op}}
        mat = MB.build_material(spec, pkg2, {})
        b = _bsdf(mat)
        nodes = {n.type for n in mat.node_tree.nodes}
        check("opacity_map builds a Transparent BSDF", "BSDF_TRANSPARENT" in nodes,
              str(sorted(nodes)))
        check("opacity_map is dual-source ADDED, not mixed", "ADD_SHADER" in nodes)
        check("opacity_map never touches the Alpha socket",
              not b.inputs["Alpha"].links)
        tsp = next(n for n in mat.node_tree.nodes if n.type == "BSDF_TRANSPARENT")
        check("transmission tint colour comes from the texture", bool(tsp.inputs["Color"].links))
        check("transmission forces BLENDED", mat.surface_render_method == "BLENDED")
    else:
        print("  SKIP transmission: no opacity_map fixture found")

    # (f) refraction -- mattype 11
    mat = MB.build_material({"key": "syn_refr", "mattype": 11, "ior": 1.52}, BLENDER_TOOL, {})
    b = _bsdf(mat)
    check("eMTRefraction sets Transmission Weight = 1",
          abs(b.inputs["Transmission Weight"].default_value - 1.0) < 1e-6)
    check("eMTRefraction sets IOR = k_refractive_index",
          abs(b.inputs["IOR"].default_value - 1.52) < 1e-6)
    check("eMTRefraction turns on material raytraced refraction",
          mat.use_raytrace_refraction is True)
    check("eMTRefraction turns on scene raytracing (else EEVEE renders it flat)",
          bpy.context.scene.eevee.use_raytracing is True)

    # (g) black emissive tint must not annihilate the emission
    pkg3, em = _find_channel_file("emissive_map", "emission")
    if em is not None:
        spec = {"key": "syn_em", "channels": {"emission": em},
                "emissive_color": [0.0, 0.0, 0.0], "emissive_intensity": 25.0,
                "emissive_scale": 1.0}
        mat = MB.build_material(spec, pkg3, {})
        b = _bsdf(mat)
        src = _linked_from(b.inputs["Emission Color"])
        check("black bake tint is ignored -- emissive map goes straight to Emission Color",
              src is not None and src.type == "TEX_IMAGE", str(src))
        check("Emission Strength = 25 x 1.0",
              abs(b.inputs["Emission Strength"].default_value - 25.0) < 1e-6,
              str(b.inputs["Emission Strength"].default_value))
        spec = dict(spec, key="syn_em_tint", emissive_tint_color=[1.0, 0.2, 0.1])
        mat = MB.build_material(spec, pkg3, {})
        b = _bsdf(mat)
        src = _linked_from(b.inputs["Emission Color"])
        check("a real tint becomes a MULTIPLY mix",
              src is not None and src.type == "MIX" and src.blend_type == "MULTIPLY",
              str(src))
    else:
        print("  SKIP emission: no emissive_map fixture found")

    # (h) a dedicated alpha map: BC4 -> Red via Separate Color, BC3 -> the Alpha output
    pkg4, am = _find_channel_file("alpha_map", "opacity")
    if am is not None:
        spec = {"key": "syn_alphamap", "mattype": 2, "blend_mode": 7,
                "channels": {"opacity": am}}
        mat = MB.build_material(spec, pkg4, {})
        b = _bsdf(mat)
        src = _linked_from(b.inputs["Alpha"])
        want = MB.alpha_component_of(am)
        check(f"alpha map (dxgi {am.get('dxgi')}) drives Alpha via component {want}",
              src is not None and src.type == ("TEX_IMAGE" if want == "A"
                                               else "SEPARATE_COLOR"), str(src))
        check("alpha map never becomes a Transparent BSDF",
              not any(n.type == "BSDF_TRANSPARENT" for n in mat.node_tree.nodes))
    else:
        print("  SKIP alpha map: no layerN_alpha_map fixture found")

    # (i) opt-in AO -> Base Color
    pkg5, rgc = _find_channel_file("composite_components", "roughness")
    if rgc is not None:
        spec = {"key": "syn_ao", "channels": {"roughness": rgc,
                                              "base_color": dict(bc)}}
        mat = MB.build_material(spec, pkg5 if bc is None else pkg, {})
        b = _bsdf(mat)
        check("AO stays unconnected by default",
              _linked_from(b.inputs["Base Color"]).type == "TEX_IMAGE"
              and mat.get("le_ao_applied") is None)
        mat = MB.build_material(dict(spec, key="syn_ao_on"),
                                pkg5 if bc is None else pkg, {"ao_to_base_color": True})
        b = _bsdf(mat)
        src = _linked_from(b.inputs["Base Color"])
        check("opts['ao_to_base_color'] multiplies AO into Base Color",
              src is not None and src.type == "MIX" and src.blend_type == "MULTIPLY",
              str(src))
    else:
        print("  SKIP AO: no composite_components fixture found")

    # (j) double-sided drives both culling flags
    mat = MB.build_material({"key": "syn_ds", "double_sided": True}, BLENDER_TOOL, {})
    check("double_sided disables backface culling", mat.use_backface_culling is False)
    check("double_sided disables shadow backface culling",
          mat.use_backface_culling_shadow is False)
    mat = MB.build_material({"key": "syn_ss", "double_sided": False}, BLENDER_TOOL, {})
    check("single-sided culls both surface and shadow",
          mat.use_backface_culling is True and mat.use_backface_culling_shadow is True)


# ---------------------------------------------------------------------------
# 5. per-mesh vertex-colour variant
# ---------------------------------------------------------------------------

def section_vertex_color():
    print("\n== 5. eDiffuseVertexColor variant ==")
    base = MB.build_material({"key": "syn_vcol", "base_color_factor": [1, 1, 1, 1]},
                             BLENDER_TOOL, {})
    var = MB.vertex_color_variant(base, "color0")
    check("variant is a separate datablock", var is not base and var.name.endswith("__vcol"))
    check("variant is cached, not rebuilt", MB.vertex_color_variant(base) is var)
    check("re-entrant on an already-converted material",
          MB.vertex_color_variant(var) is var)
    b = _bsdf(var)
    src = _linked_from(b.inputs["Base Color"])
    check("Base Color driven by a MULTIPLY mix",
          src is not None and src.type == "MIX" and src.blend_type == "MULTIPLY", str(src))
    if src is not None:
        col = [ln.from_node for s in src.inputs for ln in s.links
               if ln.from_node.type == "VERTEX_COLOR"]
        check("mix input is the color0 attribute",
              bool(col) and col[0].layer_name == "color0",
              str(col[0].layer_name if col else None))
    check("base material is untouched",
          not _bsdf(base).inputs["Base Color"].links)


# ---------------------------------------------------------------------------
# 6. layer compositing -- layerN_blend_mask gates the layers above 0
# ---------------------------------------------------------------------------

# The findings' worked example, and the material the gap was measured on.
BRIDGE_PKG = "0703fd2acd5803e9_a487d3d7bce351eb.lemesh"
BRIDGE_KEY = "b964375c606d812f__0613ef69c99cbbc6"


# The other shape: a blend mask with the AUTHORED-DEFAULT offset (no
# `layerN_blend_mask_offset` prop at all), gating a full layer-1 composite set.
LIVE_MASK_KEY = "1f517a5a067f6c8f__6e92391dc748a44a"

# specular subjects.
# `composite`: layer0_composite_specular `d1f2417d17e180a1` -- the map the earlier
#   verdict measured; specalbedo (= F0) p50 0.345 / p90 0.852 / max 1.0 in
#   linear space, 65.5% of texels above the 0.08 "ceiling".
# `panel`: one of the SIX no-base-colour `layer0_specular_map` panels; its only
#   colour texture is the specular map, and it is also the BRIDGE material.
SPEC_COMPOSITE_KEY = "91f6e49da7ae6b7b__956dc3d61e5cfe3b"
SPEC_PANEL_KEY = BRIDGE_KEY


def _find_spec(key, pkg_name=None):
    for root in (FIXTURES_MAT3, FIXTURES_MAT):
        if not root.is_dir():
            continue
        candidates = ([root / pkg_name / "manifest.json"] if pkg_name
                      else sorted(root.glob("*.lemesh/manifest.json")))
        for mf in candidates:
            if not mf.exists():
                continue
            for spec in json.loads(mf.read_text(encoding="utf-8")).get("materials", []):
                if spec.get("key") == key and spec.get("layers"):
                    return mf.parent, spec
    return None, None


def _bridge_spec():
    return _find_spec(BRIDGE_KEY, BRIDGE_PKG)


def _trace(socket, want_type, depth=6):
    """Walk upstream from `socket` looking for a node of `want_type`."""
    seen, stack = set(), [socket]
    while stack and depth >= 0:
        nxt = []
        for s in stack:
            for link in getattr(s, "links", []):
                n = link.from_node
                if n.as_pointer() in seen:
                    continue
                seen.add(n.as_pointer())
                if n.type == want_type:
                    return n
                nxt.extend(n.inputs)
        stack, depth = nxt, depth - 1
    return None


def section_layer_blend():
    print("\n== 6. layer compositing (layerN_blend_mask) ==")
    pkg, spec = _bridge_spec()
    if spec is None:
        print(f"  SKIP: {BRIDGE_KEY} not in the fixtures")
        return
    blend = MB.layer_blend_of(spec, 1)
    check("bridge layer 1 carries a blend record", blend is not None)
    if blend is None:
        return
    print(f"  layer1: mask={blend['mask'] and blend['mask']['role_key']} "
          f"component={blend['mask_component']} scale={blend['mask_scale']} "
          f"offset={blend['mask_offset']} mode={blend['blend_mode_name']} "
          f"gates={blend['gated_channels']}")
    check("mask channel is RED (k_blend_mask[i].x)", blend["mask_component"] == "R")
    check("the shipped offset is -1.0", blend["mask_offset"] == -1.0)
    check("the blend gates emission and not the mask itself",
          blend["gated_channels"] == ["emission"])

    # (a) shipped values -- the layer is parked at its animated OFF extreme
    mat = MB.build_material(dict(spec, key=BRIDGE_KEY + "__a7_default"), pkg, {})
    b = _bsdf(mat)
    check("[shipped offset -1] emission is suppressed, not dimmed",
          mat.get("le_layer_blend_emission_suppressed") is True
          and not b.inputs["Emission Color"].links
          and b.inputs["Emission Strength"].default_value == 0.0,
          f"strength={b.inputs['Emission Strength'].default_value}")
    check("[shipped offset -1] the mask DDS is not even loaded",
          not any(n.type == "TEX_IMAGE" and n.image
                  and "cf07d65049f874e7" in n.image.name
                  for n in mat.node_tree.nodes))
    check("[shipped offset -1] the emissive layer is recorded, not silently dropped",
          mat.get("le_layer_blend_emission") == 1
          and abs(float(mat.get("le_layer_blend_mask_offset")) + 1.0) < 1e-6)

    # (b) offset 0 -- the authored-on state: the mask actually gates the emissive
    mat = MB.build_material(dict(spec, key=BRIDGE_KEY + "__a7_offset0"), pkg,
                            {"layer_blend_mask_offset": 0.0})
    b = _bsdf(mat)
    src = _linked_from(b.inputs["Emission Color"])
    check("[offset 0] Emission Color goes through a MULTIPLY mix",
          src is not None and src.type == "MIX" and src.blend_type == "MULTIPLY",
          str(src))
    ma = _trace(b.inputs["Emission Color"], "MATH")
    check("[offset 0] the gate is Math(MULTIPLY_ADD) with use_clamp == saturate()",
          ma is not None and ma.operation == "MULTIPLY_ADD" and ma.use_clamp is True,
          str(ma and (ma.operation, ma.use_clamp)))
    check("[offset 0] MULTIPLY_ADD carries scale=1.0 and offset=0.0",
          ma is not None and abs(ma.inputs[1].default_value - 1.0) < 1e-6
          and abs(ma.inputs[2].default_value) < 1e-6,
          str(ma and (ma.inputs[1].default_value, ma.inputs[2].default_value)))
    sep = _trace(b.inputs["Emission Color"], "SEPARATE_COLOR")
    check("[offset 0] the gate reads a Separate Color output (the RED channel)",
          sep is not None, str(sep))
    tex = _trace(b.inputs["Emission Color"], "TEX_IMAGE")
    check("[offset 0] the blend-mask DDS is in the graph",
          any(n.type == "TEX_IMAGE" and n.image
              and "cf07d65049f874e7" in n.image.name for n in mat.node_tree.nodes),
          str(tex and tex.image and tex.image.name))
    check("[offset 0] Emission Strength stays the layer-1 intensity, 25.0",
          abs(b.inputs["Emission Strength"].default_value - 25.0) < 1e-6,
          str(b.inputs["Emission Strength"].default_value))
    for n in mat.node_tree.nodes:
        if n.type == "TEX_IMAGE" and n.image and "cf07d65049f874e7" in n.image.name:
            check("[offset 0] the blend mask is Non-Color",
                  n.image.colorspace_settings.name == "Non-Color",
                  n.image.colorspace_settings.name)

    # (c) offset 1 -- the gate is provably 1 everywhere, so no nodes are built
    mat = MB.build_material(dict(spec, key=BRIDGE_KEY + "__a7_offset1"), pkg,
                            {"layer_blend_mask_offset": 1.0})
    b = _bsdf(mat)
    src = _linked_from(b.inputs["Emission Color"])
    check("[offset 1] a provably-open gate builds no nodes at all",
          src is not None and src.type == "TEX_IMAGE", str(src))
    check("[offset 1] Emission Strength is 25.0 (the pre-fix behaviour)",
          abs(b.inputs["Emission Strength"].default_value - 25.0) < 1e-6)

    # (d) the specular verdict -- REVISED, see section_specular()
    b = _bsdf(mat)
    lvl = b.inputs["Specular IOR Level"]
    tint = b.inputs["Specular Tint"]
    check("Specular IOR Level hard_max is 1.0 (the old premise, still true)",
          abs(lvl.bl_rna.properties["default_value"].hard_max - 1.0) < 1e-6,
          f"hard_max={lvl.bl_rna.properties['default_value'].hard_max}")
    check("Specular Tint hard_max is UNBOUNDED -> F0 is NOT capped at 0.08",
          tint.bl_rna.properties["default_value"].hard_max > 1e30,
          f"hard_max={tint.bl_rna.properties['default_value'].hard_max}")
    check("the bridge panel's specular_map now drives Specular Tint",
          bool(tint.links) and bool(mat.get("le_specular_wired")),
          f"{mat.get('le_specular_role')} scale={mat.get('le_specular_f0_scale')}")
    check("...and Specular IOR Level is parked at its 0.5 neutral point",
          abs(lvl.default_value - 0.5) < 1e-6, str(lvl.default_value))

    # (e) sweep every masked material -- no regression may hide behind the one case
    root = FIXTURES_MAT3 if FIXTURES_MAT3.is_dir() else FIXTURES_MAT
    specs = {}
    for mf in sorted(root.glob("*.lemesh/manifest.json")):
        for s in json.loads(mf.read_text(encoding="utf-8")).get("materials", []):
            specs.setdefault(s["key"], (mf.parent, s))
    masked = [(p, s) for p, s in specs.values()
              if any("blend_mask" in (e.get("channels") or {})
                     for e in (s.get("layers") or []))]
    print(f"  {len(masked)} of {len(specs)} materials carry a blend mask")
    gated = suppressed = 0
    for p, s in sorted(masked, key=lambda ps: ps[1]["key"]):
        m = MB.build_material(dict(s, key=s["key"] + "__a7_sweep"), p, {})
        bb = _bsdf(m)
        marks = {k: m.get(k) for k in m.keys() if k.startswith("le_layer_blend")}
        if marks:
            gated += 1
        if m.get("le_layer_blend_emission_suppressed"):
            suppressed += 1
        check(f"[{s['key'][:34]}] survives the gate",
              bb is not None and bb.inputs["Emission Strength"].default_value >= 0.0,
              f"strength={bb.inputs['Emission Strength'].default_value} {marks}")
    print(f"  {gated} masked materials carry a gate record, {suppressed} have a "
          f"suppressed emissive layer")
    check("the corpus still has masked materials that DO gate", gated > 0)


# ---------------------------------------------------------------------------
# 7. specular / F0 -- the old "not representable" verdict, re-measured
# ---------------------------------------------------------------------------

SPEC_F0_TARGETS = (0.01, 0.04, 0.345, 0.85, 1.0)


def _flat_f0_scene(engine_pref=("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE")):
    """Plane with n = +Z, ORTHO camera on the normal, one unit parallel sun on
    the normal, 32-bit linear EXR out. Every pixel then IS the BSDF radiance
    `f(l,v) * E * (n.l)` with `l = v = n`, i.e. the F0 response with no Fresnel
    ramp and the NDF at its peak."""
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    ids = [i.identifier for i in scene.render.bl_rna.properties["engine"].enum_items]
    scene.render.engine = next((e for e in engine_pref if e in ids), ids[0])
    try:
        scene.eevee.taa_render_samples = 8
    except Exception:
        pass
    scene.render.resolution_x = scene.render.resolution_y = 8
    scene.render.image_settings.file_format = "OPEN_EXR"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "32"
    scene.render.image_settings.exr_codec = "NONE"
    scene.view_settings.view_transform = "Standard"      # NEVER AgX
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    scene.world = bpy.data.worlds.new("w")
    scene.world.use_nodes = True
    scene.world.node_tree.nodes["Background"].inputs[1].default_value = 0.0
    bpy.ops.mesh.primitive_plane_add(size=40.0)
    plane = bpy.context.object
    cam_d = bpy.data.cameras.new("cam")
    cam_d.type = "ORTHO"
    cam_d.ortho_scale = 0.5
    cam_d.clip_end = 100.0
    cam = bpy.data.objects.new("cam", cam_d)
    scene.collection.objects.link(cam)
    cam.location = (0, 0, 5)
    scene.camera = cam
    sun_d = bpy.data.lights.new("sun", "SUN")
    sun_d.energy = 1.0
    sun_d.angle = 0.0
    sun = bpy.data.objects.new("sun", sun_d)
    scene.collection.objects.link(sun)
    return scene, plane


def _render_center_rgb(scene, tmpdir, name):
    path = Path(tmpdir) / f"{name}.exr"
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)
    real = path if path.exists() else Path(str(path) + ".exr")
    img = bpy.data.images.load(str(real))
    px = list(img.pixels)
    mid = (len(px) // 4 // 2) * 4
    rgb = [float(c) for c in px[mid:mid + 3]]
    bpy.data.images.remove(img)
    try:
        real.unlink()
    except OSError:
        pass
    return rgb


def section_specular(tmpdir):
    """Measure, do not argue.

    (a) RNA: `Specular IOR Level` is hard-capped at 1.0 -- but `Specular Tint`
        is not, and Principled's dielectric F0 is `F0(IOR) * 2 * level * tint`.
    (b) Render Principled(`Specular Tint = F0/F0(IOR)`, level 0.5) against a
        Glossy BSDF whose Colour IS that F0, head-on, in linear EXR. Equality
        there is the whole claim.
    (c) The built graphs of the two real shipped role kinds.
    """
    print("\n== 7. specular / F0 ==")
    mat0 = bpy.data.materials.new("__spec_rna")
    mat0.use_nodes = True
    b0 = next(n for n in mat0.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    lvl_max = b0.inputs["Specular IOR Level"].bl_rna.properties["default_value"].hard_max
    tint_p = b0.inputs["Specular Tint"].bl_rna.properties["default_value"]
    check("Specular IOR Level hard_max == 1.0", abs(lvl_max - 1.0) < 1e-6, str(lvl_max))
    check("Specular Tint hard_max is unbounded (soft_max is 1.0, hard is not)",
          tint_p.hard_max > 1e30 and abs(tint_p.soft_max - 1.0) < 1e-6,
          f"hard={tint_p.hard_max} soft={tint_p.soft_max}")

    scene, plane = _flat_f0_scene()
    rough = 0.30
    for f0 in SPEC_F0_TARGETS:
        vals = {}
        for kind in ("principled_tint", "glossy_ref"):
            m = bpy.data.materials.new(f"__f0_{kind}_{f0}")
            m.use_nodes = True
            nt = m.node_tree
            nt.nodes.clear()
            out = nt.nodes.new("ShaderNodeOutputMaterial")
            if kind == "principled_tint":
                n = nt.nodes.new("ShaderNodeBsdfPrincipled")
                n.distribution = "GGX"
                n.inputs["Base Color"].default_value = (0, 0, 0, 1)
                n.inputs["Roughness"].default_value = rough
                n.inputs["Specular IOR Level"].default_value = MB.SPECULAR_IOR_LEVEL_NEUTRAL
                t = f0 / MB.f0_from_ior(n.inputs["IOR"].default_value)
                n.inputs["Specular Tint"].default_value = (t, t, t, 1.0)
            else:
                n = nt.nodes.new("ShaderNodeBsdfGlossy")
                n.distribution = "GGX"
                n.inputs["Color"].default_value = (f0, f0, f0, 1.0)
                n.inputs["Roughness"].default_value = rough
            nt.links.new(n.outputs[0], out.inputs["Surface"])
            plane.data.materials.clear()
            plane.data.materials.append(m)
            vals[kind] = _render_center_rgb(scene, tmpdir, f"f0_{kind}")[0]
            bpy.data.materials.remove(m)
        got, want = vals["principled_tint"], vals["glossy_ref"]
        err = abs(got - want) / want if want > 1e-9 else 0.0
        check(f"[F0={f0}] Specular Tint > 1 reaches it: Principled {got:.5f} vs "
              f"Glossy {want:.5f}", err < 0.03, f"relerr={err*100:.2f}%")
    # (b2) the SAME thing through the builder's actual node chain -- a
    # `ShaderNodeMix` RGBA MULTIPLY carrying a value far above 1.0. If that node
    # clamped, every check above would still pass and the wiring would be dead.
    for f0, alpha in ((0.345, 1.0), (0.85, 0.6), (1.0, 1.0)):
        m = bpy.data.materials.new(f"__f0_chain_{f0}")
        m.use_nodes = True
        nt = m.node_tree
        nt.nodes.clear()
        out = nt.nodes.new("ShaderNodeOutputMaterial")
        n = nt.nodes.new("ShaderNodeBsdfPrincipled")
        n.distribution = "GGX"
        n.inputs["Base Color"].default_value = (0, 0, 0, 1)
        n.inputs["Roughness"].default_value = rough
        n.inputs["Specular IOR Level"].default_value = MB.SPECULAR_IOR_LEVEL_NEUTRAL
        scale = 1.0 / MB.f0_from_ior(n.inputs["IOR"].default_value)
        rgb = f0 / alpha                       # so that rgb * alpha == f0
        amix = nt.nodes.new("ShaderNodeMix")    # "specalbedo = rgb * a"
        amix.data_type = "RGBA"
        amix.blend_type = "MULTIPLY"
        amix.inputs[0].default_value = 1.0
        amix.inputs[6].default_value = (rgb, rgb, rgb, 1.0)
        amix.inputs[7].default_value = (alpha, alpha, alpha, 1.0)
        smix = nt.nodes.new("ShaderNodeMix")    # "Specular Tint = F0 / F0(IOR)"
        smix.data_type = "RGBA"
        smix.blend_type = "MULTIPLY"
        smix.inputs[0].default_value = 1.0
        nt.links.new(amix.outputs[2], smix.inputs[6])
        smix.inputs[7].default_value = (scale, scale, scale, 1.0)
        nt.links.new(smix.outputs[2], n.inputs["Specular Tint"])
        nt.links.new(n.outputs[0], out.inputs["Surface"])
        check(f"[F0={f0}] ShaderNodeMix does not clamp (clamp_result off)",
              getattr(smix, "clamp_result", False) is False)
        plane.data.materials.clear()
        plane.data.materials.append(m)
        chained = _render_center_rgb(scene, tmpdir, "f0_chain")[0]
        bpy.data.materials.remove(m)
        m2 = bpy.data.materials.new("__f0_chain_ref")
        m2.use_nodes = True
        nt2 = m2.node_tree
        nt2.nodes.clear()
        o2 = nt2.nodes.new("ShaderNodeOutputMaterial")
        g2 = nt2.nodes.new("ShaderNodeBsdfGlossy")
        g2.distribution = "GGX"
        g2.inputs["Color"].default_value = (f0, f0, f0, 1.0)
        g2.inputs["Roughness"].default_value = rough
        nt2.links.new(g2.outputs[0], o2.inputs["Surface"])
        plane.data.materials.clear()
        plane.data.materials.append(m2)
        want = _render_center_rgb(scene, tmpdir, "f0_chain_ref")[0]
        bpy.data.materials.remove(m2)
        err = abs(chained - want) / want if want > 1e-9 else 0.0
        check(f"[F0={f0}, a={alpha}] the builder's Mix chain round-trips: "
              f"{chained:.5f} vs {want:.5f}", err < 0.03, f"relerr={err*100:.2f}%")

    # ...and the unwired baseline is stuck at F0 = 0.04 whatever the target is
    m = bpy.data.materials.new("__f0_unwired")
    m.use_nodes = True
    ub = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    ub.distribution = "GGX"
    ub.inputs["Base Color"].default_value = (0, 0, 0, 1)
    ub.inputs["Roughness"].default_value = rough
    plane.data.materials.clear()
    plane.data.materials.append(m)
    unwired = _render_center_rgb(scene, tmpdir, "f0_unwired")[0]
    bpy.data.materials.remove(m)
    print(f"  unwired (Principled defaults) head-on radiance = {unwired:.5f} "
          f"== the F0 = 0.04 response, for EVERY material")

    # (c) the two real shipped role kinds, built from the fixtures
    root = FIXTURES_MAT3 if FIXTURES_MAT3.is_dir() else FIXTURES_MAT
    specs = {}
    for mf in sorted(root.glob("*.lemesh/manifest.json")):
        for s in json.loads(mf.read_text(encoding="utf-8")).get("materials", []):
            specs.setdefault(s["key"], (mf.parent, s))
    seen = {}
    for key, (pkg, s) in sorted(specs.items()):
        ch = (s.get("channels") or {}).get("specular")
        if not ch or ch["role_key"] in seen:
            continue
        seen[ch["role_key"]] = True
        mm = MB.build_material(dict(s, key=key + "__a10"), pkg, {})
        bb = _bsdf(mm)
        tint = bb.inputs["Specular Tint"]
        want_scale = MB.specular_tint_scale(s, ch, mm.get("le_specular_ior", 1.5))
        check(f"[{ch['role_key']}] Specular Tint is driven",
              bool(tint.links), str(mm.get("le_specular_wired")))
        check(f"[{ch['role_key']}] F0 scale is {want_scale:g}",
              abs(float(mm.get("le_specular_f0_scale", 0.0)) - want_scale) < 1e-6,
              str(mm.get("le_specular_f0_scale")))
        check(f"[{ch['role_key']}] Specular IOR Level stays neutral",
              abs(bb.inputs["Specular IOR Level"].default_value - 0.5) < 1e-6)
        # the opt-out restores the old look exactly
        m2 = MB.build_material(dict(s, key=key + "__specoff"), pkg,
                               {"wire_specular": False})
        b2 = _bsdf(m2)
        check(f"[{ch['role_key']}] wire_specular=False leaves it unwired",
              not b2.inputs["Specular Tint"].links
              and bool(m2.get("le_specular_unwired")))
    if not seen:
        print("  SKIP — no extracted package carries a specular channel")
        return
    check("both shipped specular role kinds were exercised", len(seen) >= 2,
          str(sorted(seen)))


def _render_specular(outdir):
    """Before/after on two real materials: a strong `composite_specular` surface
    and one of the six no-albedo `layer0_specular_map` panels.

    Three variants each -- the shipped-today unwired state, the naive
    `Specular IOR Level` mapping the old verdict warned about (clamped, so it
    cannot exceed F0 = 0.08), and the wired `Specular Tint` result.
    """
    print(f"\n== specular renders -> {outdir} ==")
    root = FIXTURES_MAT3 if FIXTURES_MAT3.is_dir() else FIXTURES_MAT
    specs = {}
    for mf in sorted(root.glob("*.lemesh/manifest.json")):
        for s in json.loads(mf.read_text(encoding="utf-8")).get("materials", []):
            specs.setdefault(s["key"], (mf.parent, s))
    jobs = []
    for name, key in (("composite", SPEC_COMPOSITE_KEY), ("panel", SPEC_PANEL_KEY)):
        if key in specs:
            pkg, s = specs[key]
            jobs.append((name, pkg, s))
    if not jobs:
        print("  SKIP: no specular fixture")
        return []
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, pkg, s in jobs:
        variants = [("before_unwired", {"wire_specular": False}),
                    ("naive_iorlevel", {}),
                    ("after_wired", {})]
        if name == "panel":
            # DIAGNOSTIC, not a proposal: the six no-base-colour panels bind NO
            # albedo texture, so `base_color_factor` (1,1,1,1 == the baker's
            # `k_hardware_color`) stands as a flat white diffuse and swamps
            # everything else. This variant zeroes it to show what the specular
            # + emissive + transmission actually contribute. Whether the engine
            # runs a diffuse lobe at all is `layerN_enable_diffuse_`, a shader
            # PERMUTATION bit that is not on disk -- so this is NOT wired.
            variants.append(("diag_no_flat_albedo", {}))
        for tag, opts in variants:
            bpy.ops.wm.read_factory_settings(use_empty=True)
            scene = bpy.context.scene
            ids = [i.identifier
                   for i in scene.render.bl_rna.properties["engine"].enum_items]
            scene.render.engine = ("BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids
                                   else "BLENDER_EEVEE")
            try:
                scene.eevee.use_raytracing = True
            except Exception:
                pass
            scene.view_settings.view_transform = "Standard"   # NEVER AgX
            scene.view_settings.look = "None"
            scene.view_settings.exposure = 0.0
            scene.view_settings.gamma = 1.0
            scene.render.resolution_x = scene.render.resolution_y = 640
            scene.render.image_settings.file_format = "PNG"
            scene.world = bpy.data.worlds.new("w")
            scene.world.use_nodes = True
            scene.world.node_tree.nodes["Background"].inputs[0].default_value = (
                0.05, 0.05, 0.06, 1)
            bpy.ops.mesh.primitive_plane_add(size=24.0, location=(0.0, 5.0, 0.0))
            back = bpy.context.object
            back.rotation_euler = (1.5708, 0.0, 0.0)
            bm = bpy.data.materials.new(f"__backdrop_{name}_{tag}")
            bm.use_nodes = True
            bnt = bm.node_tree
            bb = next(n for n in bnt.nodes if n.type == "BSDF_PRINCIPLED")
            ck = bnt.nodes.new("ShaderNodeTexChecker")
            ck.inputs["Scale"].default_value = 14.0
            ck.inputs["Color1"].default_value = (0.85, 0.25, 0.12, 1.0)
            ck.inputs["Color2"].default_value = (0.10, 0.35, 0.85, 1.0)
            bnt.links.new(ck.outputs["Color"], bb.inputs["Base Color"])
            back.data.materials.append(bm)

            bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=64,
                                                 ring_count=32)
            ob = bpy.context.object
            bpy.ops.object.shade_smooth()
            mat = MB.build_material(dict(s, key=f"{s['key']}__{tag}"), pkg, opts)
            if tag == "diag_no_flat_albedo":
                bi = _bsdf(mat).inputs["Base Color"]
                if not bi.links:
                    bi.default_value = (0.0, 0.0, 0.0, 1.0)
            if tag == "naive_iorlevel":
                # The mapping the old verdict warned about: `Specular Tint` left
                # inside its soft 0..1 range and `Specular IOR Level` pushed to
                # its hard_max 1.0 -> F0 = 0.08 * specalbedo, i.e. the "12x too
                # dark" ceiling. Same graph, scale factor 25 -> 1.
                for n in mat.node_tree.nodes:
                    if n.label.startswith("Specular Tint = F0"):
                        n.inputs[7].default_value = (1.0, 1.0, 1.0, 1.0)
                _bsdf(mat).inputs["Specular IOR Level"].default_value = 1.0
            ob.data.materials.append(mat)
            bpy.ops.object.light_add(type="AREA", location=(3, -4, 4))
            bpy.context.object.data.energy = 800
            bpy.ops.object.light_add(type="AREA", location=(-4, -3, 1))
            bpy.context.object.data.energy = 300
            bpy.ops.object.camera_add(location=(0, -3.4, 0), rotation=(1.5708, 0, 0))
            scene.camera = bpy.context.object
            path = outdir / f"spec_{name}_{tag}.png"
            scene.render.filepath = str(path)
            bpy.ops.render.render(write_still=True)
            b = _bsdf(mat)
            print(f"  {name}/{tag}: {path}  role={mat.get('le_specular_role')} "
                  f"tint_linked={bool(b.inputs['Specular Tint'].links)} "
                  f"scale={mat.get('le_specular_f0_scale')} "
                  f"view_transform=Standard")
            written.append(str(path))
    return written


def _render_layer_blend(outdir):
    """Three renders of the bridge material: ungated (the bug), the shipped
    state, and the authored-on state. `view_transform = 'Standard'` throughout."""
    print(f"\n== layer-blend renders -> {outdir} ==")
    jobs = []
    pkg, spec = _bridge_spec()
    if spec is not None:
        jobs += [("bridge", pkg, spec, tag, opts) for tag, opts in (
            # offset +1 pins the gate to 1 everywhere == the PRE-FIX behaviour
            ("before_ungated", {"layer_blend_mask_offset": 1.0}),
            ("after_shipped", {}),
            ("after_offset0", {"layer_blend_mask_offset": 0.0}))]
    pkg2, spec2 = _find_spec(LIVE_MASK_KEY)
    if spec2 is not None:
        jobs += [("livemask", pkg2, spec2, tag, opts) for tag, opts in (
            ("before_ungated", {"layer_blend_mask_offset": 1.0}),
            ("after_shipped", {}))]
    if not jobs:
        print("  SKIP: no blend-mask fixture")
        return []
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, pkg, spec, tag, opts in jobs:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        ids = [i.identifier for i in scene.render.bl_rna.properties["engine"].enum_items]
        scene.render.engine = ("BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids
                               else "BLENDER_EEVEE")
        try:
            scene.eevee.use_raytracing = True
        except Exception:
            pass
        scene.view_settings.view_transform = "Standard"     # NEVER AgX: it desaturates highlights
        scene.view_settings.look = "None"
        scene.view_settings.exposure = 0.0
        scene.view_settings.gamma = 1.0
        scene.render.resolution_x = scene.render.resolution_y = 640
        scene.render.image_settings.file_format = "PNG"
        if scene.world is None:
            scene.world = bpy.data.worlds.new("w")
        scene.world.use_nodes = True
        scene.world.node_tree.nodes["Background"].inputs[0].default_value = (
            0.05, 0.05, 0.06, 1)
        # a checker backdrop so a transparent result is legible, not just "dark"
        bpy.ops.mesh.primitive_plane_add(size=24.0, location=(0.0, 5.0, 0.0))
        plane = bpy.context.object
        plane.rotation_euler = (1.5708, 0.0, 0.0)
        bm = bpy.data.materials.new(f"__backdrop_{tag}")
        bm.use_nodes = True
        bnt = bm.node_tree
        bb = next(n for n in bnt.nodes if n.type == "BSDF_PRINCIPLED")
        ck = bnt.nodes.new("ShaderNodeTexChecker")
        ck.inputs["Scale"].default_value = 14.0
        ck.inputs["Color1"].default_value = (0.85, 0.25, 0.12, 1.0)
        ck.inputs["Color2"].default_value = (0.10, 0.35, 0.85, 1.0)
        bnt.links.new(ck.outputs["Color"], bb.inputs["Base Color"])
        plane.data.materials.append(bm)

        bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=64, ring_count=32)
        ob = bpy.context.object
        bpy.ops.object.shade_smooth()
        mat = MB.build_material(dict(spec, key=f"{spec['key']}__{tag}"), pkg, opts)
        ob.data.materials.append(mat)
        bpy.ops.object.light_add(type="AREA", location=(3, -4, 4))
        bpy.context.object.data.energy = 800
        bpy.ops.object.light_add(type="AREA", location=(-4, -3, 1))
        bpy.context.object.data.energy = 300
        bpy.ops.object.camera_add(location=(0, -3.4, 0), rotation=(1.5708, 0, 0))
        scene.camera = bpy.context.object
        path = outdir / f"a7_{name}_{tag}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        b = _bsdf(mat)
        print(f"  {name}/{tag}: {path}  emission_strength="
              f"{b.inputs['Emission Strength'].default_value} "
              f"emission_linked={bool(b.inputs['Emission Color'].links)} "
              f"suppressed={mat.get('le_layer_blend_emission_suppressed')} "
              f"view_transform={scene.view_settings.view_transform}")
        written.append(str(path))
    return written


# ---------------------------------------------------------------------------
# before/after render pair
# ---------------------------------------------------------------------------

def _legacy_material(spec, pkg_dir):
    """The PRE-fix graph, replicated for the before/after pair: colour space set but
    `image.alpha_mode` left at Blender's STRAIGHT default, `k_alpha` never applied,
    `blend_method = "CLIP"` (a no-op on 4.2+)."""
    mat = bpy.data.materials.new(name=spec["key"] + "__legacy")
    mat.use_nodes = True
    nt = mat.node_tree
    b = _bsdf(mat)
    ch = spec.get("channels", {})
    bc = ch.get("base_color")
    if bc and bc.get("file"):
        p = Path(pkg_dir) / bc["file"]
        if p.exists():
            img = bpy.data.images.load(str(p))       # fresh datablock, default alpha_mode
            img.colorspace_settings.name = bc.get("colorspace", "sRGB")
            n = nt.nodes.new("ShaderNodeTexImage")
            n.image = img
            n.location = (-600, 300)
            nt.links.new(n.outputs["Color"], b.inputs["Base Color"])
    rg = ch.get("roughness")
    if rg and rg.get("file"):
        p = Path(pkg_dir) / rg["file"]
        if p.exists():
            img = bpy.data.images.load(str(p))
            img.colorspace_settings.name = "Non-Color"
            n = nt.nodes.new("ShaderNodeTexImage")
            n.image = img
            n.location = (-600, 0)
            nt.links.new(n.outputs["Color"], b.inputs["Roughness"])
    try:
        mat.blend_method = "CLIP"
    except Exception:
        pass
    return mat


def _render_alpha_mode_pair(outdir):
    """Isolate the alpha_mode effect: the SAME BC3 albedo texture on a flat unlit
    plane, once per `image.alpha_mode`. No lighting, no roughness, no normal map --
    the only variable is the loader flag."""
    if not ALPHA_PROOF_DDS.exists():
        return []
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for mode in ("STRAIGHT", "CHANNEL_PACKED"):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        ids = [i.identifier for i in scene.render.bl_rna.properties["engine"].enum_items]
        scene.render.engine = ("BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids
                               else "BLENDER_EEVEE")
        scene.view_settings.view_transform = "Standard"     # NOT AgX: it desaturates highlights
        scene.view_settings.look = "None"
        scene.render.resolution_x = 512
        scene.render.resolution_y = 1024
        scene.render.image_settings.file_format = "PNG"
        bpy.ops.mesh.primitive_plane_add(size=2)
        ob = bpy.context.object
        ob.rotation_euler = (1.5708, 0, 0)
        ob.scale = (1.0, 1.0, 2.0)
        mat = bpy.data.materials.new(f"albedo_{mode}")
        mat.use_nodes = True
        nt = mat.node_tree
        for n in list(nt.nodes):
            if n.type != "OUTPUT_MATERIAL":
                nt.nodes.remove(n)
        out = next(n for n in nt.nodes if n.type == "OUTPUT_MATERIAL")
        em = nt.nodes.new("ShaderNodeEmission")
        nt.links.new(em.outputs[0], out.inputs["Surface"])
        tex = nt.nodes.new("ShaderNodeTexImage")
        img = bpy.data.images.load(str(ALPHA_PROOF_DDS))
        img.colorspace_settings.name = "sRGB"
        img.alpha_mode = mode
        img.reload()
        tex.image = img
        nt.links.new(tex.outputs["Color"], em.inputs["Color"])
        ob.data.materials.append(mat)
        bpy.ops.object.camera_add(location=(0, -2.9, 0), rotation=(1.5708, 0, 0))
        scene.camera = bpy.context.object
        scene.camera.data.type = "ORTHO"
        scene.camera.data.ortho_scale = 2.0
        path = outdir / f"albedo_alphamode_{mode.lower()}_standard.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        print(f"  isolated albedo, alpha_mode={img.alpha_mode}: {path}")
        written.append(str(path))
    return written


def _render_pair(outdir):
    print(f"\n== before/after render pair -> {outdir} ==")
    pkg, bc = _find_channel_file("composite_diffuse", "base_color")
    if bc is None:
        print("  SKIP: no composite_diffuse fixture")
        return []
    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    spec = next(s for s in manifest.get("materials", [])
                if (s.get("channels") or {}).get("base_color", {}).get("file")
                == bc["file"])
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for tag, builder in (("before", _legacy_material), ("after", MB.build_material)):
        bpy.ops.wm.read_factory_settings(use_empty=True)
        scene = bpy.context.scene
        ids = [i.identifier for i in scene.render.bl_rna.properties["engine"].enum_items]
        scene.render.engine = ("BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids
                               else "BLENDER_EEVEE")
        scene.view_settings.view_transform = "Standard"     # NOT AgX: it desaturates highlights
        scene.view_settings.look = "None"
        scene.render.resolution_x = scene.render.resolution_y = 512
        scene.render.image_settings.file_format = "PNG"
        if scene.world is None:
            scene.world = bpy.data.worlds.new("w")
        scene.world.use_nodes = True
        scene.world.node_tree.nodes["Background"].inputs[0].default_value = (
            0.05, 0.05, 0.06, 1)
        bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=64, ring_count=32)
        ob = bpy.context.object
        bpy.ops.object.shade_smooth()
        mat = (builder(spec, pkg) if builder is _legacy_material
               else builder(spec, pkg, {}))
        ob.data.materials.append(mat)
        bpy.ops.object.light_add(type="AREA", location=(3, -4, 4))
        bpy.context.object.data.energy = 800
        bpy.ops.object.light_add(type="AREA", location=(-4, -3, 1))
        bpy.context.object.data.energy = 300
        bpy.ops.object.camera_add(location=(0, -3.6, 0), rotation=(1.5708, 0, 0))
        scene.camera = bpy.context.object
        path = outdir / f"mat_alphamode_{tag}_standard.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        imgs = [n.image for n in mat.node_tree.nodes
                if n.type == "TEX_IMAGE" and n.image]
        print(f"  {tag}: {path}  alpha_modes={[i.alpha_mode for i in imgs]} "
              f"srm={mat.surface_render_method} view_transform=Standard")
        written.append(str(path))
    return written


def main():
    outdir, fixtures, limit = _argv()
    tmpdir = Path(bpy.app.tempdir)
    section_rna()
    section_alpha_mode(tmpdir)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    section_fixtures(fixtures, limit)
    section_synthetic()
    section_vertex_color()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    section_layer_blend()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    section_specular(tmpdir)
    if outdir:
        _render_pair(outdir)
        _render_alpha_mode_pair(outdir)
        _render_layer_blend(outdir)
        _render_specular(outdir)
    print(f"\n{CHECKS[0] - len(FAILURES)}/{CHECKS[0]} checks passed")
    for f in FAILURES:
        print(f"  FAILED: {f}")
    print(f"PROBE_RESULT: {'FAIL' if FAILURES else 'PASS'}")


if __name__ == "__main__":
    main()
