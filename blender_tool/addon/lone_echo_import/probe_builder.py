"""Reflection probes -> the ambient SPECULAR term, in Blender.

Sibling of `lightmap_builder`, which wires the ambient DIFFUSE term.  The
decode, the grammar and every corpus count live in the pure-stdlib core
(`le_mesh.reflection_probe`) and in docs/LIGHTING.md;
this file only does the Blender part.

What it does
------------
1. Reads the package manifest's `reflection_probes` section (level-scoped) and
   each object's `probe_index` (`CGMeshData.probeidx@0x50`).
2. Loads that probe's shipped BC6H cube DDS.  ⚠ **Blender has no cube-texture
   image type**: `bpy.data.images.load` on a DX10 cubemap DDS yields a
   `dim x 6*dim` vertical strip of the six faces' mip 0, and nothing else of the
   cube is reachable.  `engine-confirmed (Blender 5.1.1)` — and byte-for-byte
   the same pixels as `le_mesh.reflection_probe.cube_strip_bytes` produces by
   hand, which is what pins the face/mip arithmetic.
3. Resamples that strip to an equirectangular float image and wires it through a
   `ShaderNodeTexEnvironment` driven by `Texture Coordinate -> Reflection`.
4. Adds it as an EMISSION, weighted by `gloss^2 * Fresnel * intensity`, so a
   `roughness == 1` surface is provably unchanged.

⛔ **Default OFF** (`probe_mode == "off"`), like every other new light path.

What it does NOT do — the honest limit
--------------------------------------
* **No roughness-dependent prefilter.**  The engine samples a mip chain
  (`mipcounts == 9` on 60/60 shipped probe sets) and scales it by the per-mip
  `SGProbeBoundingBox.normalizations`; Blender exposes only mip 0 of the DDS, so
  the wired reflection is always the SHARP one and gets darker-with-roughness
  only through the `gloss^2` weight.  Mips 1..8 ARE on disk and reachable — see
  the findings doc's ranked next steps.
* **No box projection / parallax.**  The shared OBB is decoded and reported
  (`obb_min_world` / `obb_max_world`) and not applied.
* **No F0 from the material.**  The Fresnel factor is a plain dielectric
  Schlick term, not the material's own specular colour, because
  `material_builder.py` is another agent's file and this module does not reach
  into its node names.  The seam where it should is `PROPOSED_MATERIAL_HOOK`.
"""

from __future__ import annotations

import math
from pathlib import Path

try:                                   # inside Blender
    import bpy                         # type: ignore
except Exception:                      # pragma: no cover - importable outside Blender
    bpy = None                         # type: ignore


def _le_mesh_reflection_probe():
    """`le_mesh.reflection_probe`, or None.

    The grammar is owned by the pure core and is deliberately NOT duplicated
    here.  The add-on is meant to install standalone and `build_addon_zip.py`
    ships only `lone_echo_import/`, so the import is SOFT and bootstraps the
    research-tree layout (`<blender_tool>/addon/lone_echo_import/` ->
    `<blender_tool>/le_mesh/`) before giving up — exactly what
    `lightmap_builder._le_mesh_lightmap` does.  When it is genuinely
    unavailable every entry point below reports `le_mesh unavailable` and wires
    nothing, rather than exploding at import time and taking the add-on with it.
    """
    import sys
    try:
        from le_mesh import reflection_probe as rp      # type: ignore
        return rp
    except ImportError:
        pass
    here = Path(__file__).resolve()
    for cand in (here.parents[2], here.parents[1]):
        if str(cand) not in sys.path:
            sys.path.insert(0, str(cand))
    try:
        from le_mesh import reflection_probe as rp      # type: ignore
        return rp
    except ImportError:
        return None


RP = _le_mesh_reflection_probe()

#: manifest key, must match `le_mesh.reflection_probe.MANIFEST_KEY`
MANIFEST_KEY = getattr(RP, "MANIFEST_KEY", "reflection_probes")
#: colour space for the HDR cube; mirrors `le_mesh.lightmap.COLORSPACE_LIGHTMAP`
COLORSPACE_PROBE = getattr(RP, "COLORSPACE_PROBE", "Linear Rec.709")
COLORSPACE_PROBE_FALLBACK = getattr(RP, "COLORSPACE_PROBE_FALLBACK", "Non-Color")
#: "this mesh names no probe"
PROBE_INDEX_NONE = getattr(RP, "PROBE_INDEX_NONE", 0xFFFFFFFF)


def available() -> bool:
    """Is the pure core reachable?  Everything below no-ops when it is not."""
    return RP is not None


def _has_probe(v) -> bool:
    if RP is not None:
        return RP.has_probe(v)
    if v is None:
        return False
    try:
        i = int(v)
    except (TypeError, ValueError):
        return False
    return i != PROBE_INDEX_NONE and i >= 0

MODE_OFF = "off"
MODE_SPECULAR = "specular"
MODES = (MODE_OFF, MODE_SPECULAR)
DEFAULT_MODE = MODE_OFF

#: equirect resolution the cube is resampled to.  256^2 x 6 faces carries
#: ~393k texels; 512x256 is 131k, i.e. deliberately no upsampling.
DEFAULT_EQUIRECT_WIDTH = 512
DEFAULT_EQUIRECT_HEIGHT = 256

#: dielectric IOR for the Fresnel weight.  Not the material's own F0 — see the
#: module docstring's honest-limit list.
DEFAULT_FRESNEL_IOR = 1.45

#: ★ `engine-confirmed (Blender 5.1.1)`, `tests/blender_probe_probe.py` §rows:
#: Blender's DDS reader returns the image with row 0 at the BOTTOM, i.e. the
#: file's first stored row ends up at the TOP of `image.pixels`.  A cube DDS is
#: therefore exposed as face 5 at the bottom of the buffer, each face's rows
#: reversed.  Flip this and every reflection is upside down and face-swapped.
STRIP_IMAGE_IS_FLIPPED = True

#: where `wire_ambient_specular` writes its provenance
PROP_PROBE = "le_probe_index"
PROP_MODE = "le_probe_mode"
PROP_FILE = "le_probe_file"

#: node names, so a re-wire is idempotent instead of stacking duplicates
N_TEXCOORD = "le_probe_texcoord"
N_ENV = "le_probe_env"
N_GLOSS = "le_probe_gloss"
N_GLOSS2 = "le_probe_gloss2"
N_FRESNEL = "le_probe_fresnel"
N_WEIGHT = "le_probe_weight"
N_INTENSITY = "le_probe_intensity"
N_EMISSION = "le_probe_emission"
N_ADD = "le_probe_add"

#: ⚠ The seam this module deliberately does NOT cut into `material_builder.py`
#: (another agent owns that file).  The proposed diff is in
#: docs/LIGHTING.md §7.
PROPOSED_MATERIAL_HOOK = "material_builder.build_material.probe_spec"


# =============================================================================
# options
# =============================================================================

def resolved_mode(opts=None) -> str:
    mode = str((opts or {}).get("probe_mode", DEFAULT_MODE) or DEFAULT_MODE).lower()
    return mode if mode in MODES else DEFAULT_MODE


def _f(opts, key, default):
    try:
        return float((opts or {}).get(key, default))
    except (TypeError, ValueError):
        return default


# =============================================================================
# strip sampling (pure — no bpy, so it is unit-tested)
# =============================================================================

def strip_texel(px, dim: int, face: int, x: int, y: int, *,
                flipped: bool = STRIP_IMAGE_IS_FLIPPED):
    """One RGB texel of a `dim x 6*dim` face strip held in a flat RGBA buffer.

    `y == 0` is the face's FIRST STORED row (the DDS convention), regardless of
    which way up the host buffer is.  `flipped=True` means row 0 of `px` is the
    LAST row of the file, which is what Blender hands back.
    """
    if flipped:
        row = (5 - face) * dim + (dim - 1 - y)
    else:
        row = face * dim + y
    i = (row * dim + x) * 4
    return px[i], px[i + 1], px[i + 2]


def make_strip_sampler(px, dim: int, *, flipped: bool = STRIP_IMAGE_IS_FLIPPED):
    """`(face, u, v) -> (r, g, b)`, nearest-neighbour, for `resample_cube_to_equirect`."""
    last = dim - 1

    def sample(face, u, v):
        x = int(u * dim)
        y = int(v * dim)
        x = 0 if x < 0 else (last if x > last else x)
        y = 0 if y < 0 else (last if y > last else y)
        return strip_texel(px, dim, face, x, y, flipped=flipped)

    return sample


def equirect_pixels_from_strip(px, dim: int, width: int, height: int, *,
                               flipped: bool = STRIP_IMAGE_IS_FLIPPED) -> list:
    """Face strip -> a flat RGBA float list ready for `Image.pixels.foreach_set`."""
    if RP is None:
        return []
    return RP.resample_cube_to_equirect(
        make_strip_sampler(px, dim, flipped=flipped), width, height)


def _edge_direction_pairs(dim: int, samples: int = 96) -> list:
    """Direction pairs a hair apart that land on DIFFERENT cube faces.

    Built by walking each face's four borders and stepping just outside, which
    is exactly where a wrong face order or row order shows up.
    """
    if RP is None:
        return []
    eps = 0.75 / dim
    out = []
    for i in range(samples):
        t = (i + 0.5) / samples
        for face in range(6):
            for a, b in ((-eps, t), (1.0 + eps, t), (t, -eps), (t, 1.0 + eps)):
                inside_u = min(max(a, eps), 1.0 - eps)
                inside_v = min(max(b, eps), 1.0 - eps)
                d_in = RP.face_uv_to_direction(face, inside_u, inside_v)
                d_out = RP.face_uv_to_direction(face, a, b)
                f_in, _, _ = RP.direction_to_face_uv(d_in)
                f_out, _, _ = RP.direction_to_face_uv(d_out)
                if f_in != f_out:
                    out.append((d_in, d_out))
    return out


def cube_seam_error(px, dim: int, *, flipped: bool = STRIP_IMAGE_IS_FLIPPED,
                    pairs=None, samples: int = 96) -> float:
    """Mean |log| mismatch across the cube's face seams — a convention scorer.

    A face order or a row order that is wrong makes neighbouring faces disagree
    at their shared edge.  Comparing texels a hair either side of every seam
    gives a single number whose MINIMUM picks the convention, with no reference
    image and no eyeballing — the same trick that settled the lightmap's
    page-major layout.  Used by `tests/blender_probe_probe.py` to choose
    `STRIP_IMAGE_IS_FLIPPED`.
    """
    if RP is None:
        return float("inf")
    smp = make_strip_sampler(px, dim, flipped=flipped)
    total, n = 0.0, 0
    for d_in, d_out in (pairs if pairs is not None else _edge_direction_pairs(dim, samples)):
        f1, u1, v1 = RP.direction_to_face_uv(d_in)
        f2, u2, v2 = RP.direction_to_face_uv(d_out)
        a = smp(f1, u1, v1)
        b = smp(f2, u2, v2)
        for k in range(3):
            total += abs(math.log(max(a[k], 1e-6)) - math.log(max(b[k], 1e-6)))
            n += 1
    return total / max(1, n)


# =============================================================================
# package context
# =============================================================================

def resolve_probe_context(pkg_dir, manifest, opts=None) -> dict:
    """`{}` or a context: the level probe set plus where its cube files are.

    Nothing here is guessed.  A package with no `reflection_probes` section, or
    a section whose probes have no extracted `cube_file`, yields a context that
    reports itself as unusable rather than one that silently wires nothing.
    """
    if RP is None:
        return {"section": {}, "count": 0, "files": {}, "equirects": {},
                "source": "unavailable", "notes": ["le_mesh unavailable"]}
    section = (manifest or {}).get(MANIFEST_KEY) or {}
    ctx = {
        "section": section,
        "pkg_dir": str(pkg_dir) if pkg_dir else "",
        "count": int(section.get("count") or 0),
        "resource": section.get("resource"),
        "colorspace": section.get("colorspace") or COLORSPACE_PROBE,
        "files": {},
        "equirects": {},
        "source": "manifest" if section else "absent",
        "notes": [],
    }
    if not section:
        ctx["notes"].append("no `reflection_probes` section in the manifest")
        return ctx
    base = Path(pkg_dir) if pkg_dir else None
    for spec in section.get("probes") or []:
        rel = spec.get("cube_file") or ""
        if not rel:
            continue
        p = Path(rel)
        if base is not None and not p.is_absolute():
            p = base / rel
        if p.is_file():
            ctx["files"][int(spec["index"])] = str(p)
    if not ctx["files"]:
        ctx["notes"].append(
            "the section names no extracted cube DDS — re-run the extractor "
            "with --probe-textures")
    return ctx


def probe_spec_for_object(ctx, obj) -> dict:
    """The probe spec for one manifest object, or `{}`.

    `obj["probe_index"]` is `CGMeshData.probeidx@0x50`.  `null` / `0xffffffff`
    means the mesh names no probe and must get NO ambient specular — never
    probe 0 as a stand-in.
    """
    if not ctx or not ctx.get("section"):
        return {}
    idx = (obj or {}).get("probe_index")
    if not _has_probe(idx):
        return {}
    i = int(idx)
    probes = ctx["section"].get("probes") or []
    if i >= len(probes):
        return {}
    spec = dict(probes[i])
    spec["resolved_file"] = ctx.get("files", {}).get(i, "")
    return spec


# =============================================================================
# images
# =============================================================================

def _set_colorspace(img, name, fallback=None):
    try:
        img.colorspace_settings.name = name
    except Exception:                                  # pragma: no cover
        if fallback:
            try:
                img.colorspace_settings.name = fallback
            except Exception:
                pass
    return img.colorspace_settings.name


def load_cube_strip(path, colorspace=COLORSPACE_PROBE):
    """Load a probe cube DDS.  Returns `(image, dim)`; `dim` is the face size."""
    if bpy is None:                                    # pragma: no cover
        raise RuntimeError("load_cube_strip needs Blender")
    img = bpy.data.images.load(str(path), check_existing=True)
    _set_colorspace(img, colorspace, COLORSPACE_PROBE_FALLBACK)
    w, h = img.size
    if not w or h != w * 6:
        raise ValueError(
            f"{path}: Blender exposed {w}x{h}; a cube DDS must read as dim x 6*dim "
            f"(this is the only shape the strip sampler understands)")
    return img, w


def equirect_image_for_probe(ctx, probe_index: int, opts=None):
    """Build (and cache) the equirectangular environment image for one probe."""
    if bpy is None:                                    # pragma: no cover
        raise RuntimeError("equirect_image_for_probe needs Blender")
    i = int(probe_index)
    cached = ctx.setdefault("equirects", {}).get(i)
    if cached is not None and cached.name in bpy.data.images:
        return cached
    path = ctx.get("files", {}).get(i)
    if not path:
        return None
    width = int(_f(opts, "probe_equirect_width", DEFAULT_EQUIRECT_WIDTH))
    height = int(_f(opts, "probe_equirect_height", DEFAULT_EQUIRECT_HEIGHT))
    src, dim = load_cube_strip(path, ctx.get("colorspace") or COLORSPACE_PROBE)
    px = [0.0] * (len(src.pixels))
    src.pixels.foreach_get(px)
    flat = equirect_pixels_from_strip(px, dim, width, height)
    name = f"le_probe_{i:02d}_equirect"
    img = bpy.data.images.get(name)
    if img is not None:
        bpy.data.images.remove(img)
    img = bpy.data.images.new(name, width, height, float_buffer=True)
    # ⚠ Order matters and is `engine-confirmed (Blender 5.1.1)`: a `images.new`
    # datablock is GENERATED, and anything that makes Blender re-generate it —
    # `Image.update()`, a `generated_*` write, a colour-space change — discards
    # the buffer.  Set the colour space FIRST, write the pixels LAST, and never
    # call `update()`.  Doing it the other way round silently yields a black
    # environment (measured: mean 0.0 through an EEVEE/Cycles render).
    _set_colorspace(img, ctx.get("colorspace") or COLORSPACE_PROBE,
                    COLORSPACE_PROBE_FALLBACK)
    img.pixels.foreach_set(flat)
    ctx["equirects"][i] = img
    ctx.setdefault("equirect_stats", {})[i] = {
        "width": width, "height": height,
        "max": max(flat[0:len(flat):4]) if flat else 0.0,
        "mean": (sum(flat[0:len(flat):4]) / max(1, width * height)) if flat else 0.0,
    }
    return img


# =============================================================================
# node wiring
# =============================================================================

def variant_name(base_name: str, probe: int) -> str:
    """Datablock name for a (material, probe) variant.

    The probe is in the key for the same reason the lightmap PAGE is in
    `lightmap_builder.variant_name`: one material used by two meshes bound to two
    probes must yield two datablocks, not collapse onto whichever was wired
    first.  It composes with the lightmap's `__lmN` suffix because it is built
    from `mat.name`.
    """
    return f"{base_name}__probe{int(probe)}"


def _find_principled(node_tree):
    for n in node_tree.nodes:
        if n.type == "BSDF_PRINCIPLED":
            return n
    return None


def _find_output(node_tree):
    for n in node_tree.nodes:
        if n.type == "OUTPUT_MATERIAL" and getattr(n, "is_active_output", True):
            return n
    for n in node_tree.nodes:
        if n.type == "OUTPUT_MATERIAL":
            return n
    return None


def _node(node_tree, name, ntype, location):
    n = node_tree.nodes.get(name)
    if n is None or n.bl_idname != ntype:
        if n is not None:
            node_tree.nodes.remove(n)
        n = node_tree.nodes.new(ntype)
        n.name = name
        n.label = name
    n.location = location
    return n


def unwire(node_tree) -> bool:
    """Remove this module's nodes and restore the original Surface link."""
    add = node_tree.nodes.get(N_ADD)
    out = _find_output(node_tree)
    restored = False
    if add is not None and out is not None:
        src = add.inputs[0].links[0].from_socket if add.inputs[0].links else None
        if src is not None:
            node_tree.links.new(src, out.inputs["Surface"])
            restored = True
    for name in (N_ADD, N_EMISSION, N_WEIGHT, N_INTENSITY, N_FRESNEL,
                 N_GLOSS2, N_GLOSS, N_ENV, N_TEXCOORD):
        n = node_tree.nodes.get(name)
        if n is not None:
            node_tree.nodes.remove(n)
    return restored


def wire_ambient_specular(mat, node_tree, bsdf, probe_spec, env_image, opts=None) -> dict:
    """Add `env_image` as an ambient-specular emission on top of `mat`'s surface.

    Returns a report dict; `{"wired": False, "reason": …}` for every no-op, so a
    caller can print exactly why a material was left alone.

    The weight is `gloss^2 * Fresnel(ior) * intensity` with
    `gloss = 1 - roughness`.  ⇒ a `roughness == 1` surface gets weight **0** and
    is bit-identical to the unwired render, which is the acceptance test.
    """
    if bpy is None:                                    # pragma: no cover
        return {"wired": False, "reason": "no bpy"}
    if RP is None:
        return {"wired": False, "reason": "le_mesh unavailable"}
    if resolved_mode(opts) == MODE_OFF:
        return {"wired": False, "reason": "probe_mode=off"}
    if not probe_spec:
        return {"wired": False, "reason": "mesh names no probe"}
    if env_image is None:
        return {"wired": False, "reason": "no equirect image (cube DDS not extracted?)"}
    if node_tree is None:
        return {"wired": False, "reason": "material has no node tree"}
    out = _find_output(node_tree)
    if out is None:
        return {"wired": False, "reason": "material has no output node"}
    bsdf = bsdf or _find_principled(node_tree)

    intensity = _f(opts, "probe_intensity", 1.0)
    ior = _f(opts, "probe_fresnel_ior", DEFAULT_FRESNEL_IOR)

    surface_link = out.inputs["Surface"].links
    surface_src = surface_link[0].from_socket if surface_link else None
    if surface_src is None:
        return {"wired": False, "reason": "nothing is connected to Surface"}
    if surface_src.node.name == N_ADD:
        unwire(node_tree)
        surface_link = out.inputs["Surface"].links
        surface_src = surface_link[0].from_socket if surface_link else None
        if surface_src is None:
            return {"wired": False, "reason": "could not restore the surface link"}

    x, y = out.location[0] - 900.0, out.location[1] - 420.0
    texco = _node(node_tree, N_TEXCOORD, "ShaderNodeTexCoord", (x, y))
    env = _node(node_tree, N_ENV, "ShaderNodeTexEnvironment", (x + 200, y))
    env.image = env_image
    try:
        env.projection = "EQUIRECTANGULAR"
    except Exception:                                  # pragma: no cover
        pass
    node_tree.links.new(texco.outputs["Reflection"], env.inputs["Vector"])

    gloss = _node(node_tree, N_GLOSS, "ShaderNodeMath", (x + 200, y - 240))
    gloss.operation = "SUBTRACT"
    gloss.inputs[0].default_value = 1.0
    rough_in = bsdf.inputs.get("Roughness") if bsdf is not None else None
    roughness_source = "none"
    if rough_in is not None and rough_in.links:
        node_tree.links.new(rough_in.links[0].from_socket, gloss.inputs[1])
        roughness_source = "linked"
    elif rough_in is not None:
        gloss.inputs[1].default_value = float(rough_in.default_value)
        roughness_source = "constant"
    else:
        gloss.inputs[1].default_value = 0.0

    gloss2 = _node(node_tree, N_GLOSS2, "ShaderNodeMath", (x + 380, y - 240))
    gloss2.operation = "MULTIPLY"
    node_tree.links.new(gloss.outputs[0], gloss2.inputs[0])
    node_tree.links.new(gloss.outputs[0], gloss2.inputs[1])

    fres = _node(node_tree, N_FRESNEL, "ShaderNodeFresnel", (x + 380, y - 420))
    fres.inputs["IOR"].default_value = ior

    w1 = _node(node_tree, N_WEIGHT, "ShaderNodeMath", (x + 560, y - 300))
    w1.operation = "MULTIPLY"
    node_tree.links.new(gloss2.outputs[0], w1.inputs[0])
    node_tree.links.new(fres.outputs[0], w1.inputs[1])

    w2 = _node(node_tree, N_INTENSITY, "ShaderNodeMath", (x + 740, y - 300))
    w2.operation = "MULTIPLY"
    node_tree.links.new(w1.outputs[0], w2.inputs[0])
    w2.inputs[1].default_value = intensity

    emis = _node(node_tree, N_EMISSION, "ShaderNodeEmission", (x + 740, y))
    node_tree.links.new(env.outputs["Color"], emis.inputs["Color"])
    node_tree.links.new(w2.outputs[0], emis.inputs["Strength"])

    add = _node(node_tree, N_ADD, "ShaderNodeAddShader", (x + 940, y - 120))
    node_tree.links.new(surface_src, add.inputs[0])
    node_tree.links.new(emis.outputs[0], add.inputs[1])
    node_tree.links.new(add.outputs[0], out.inputs["Surface"])

    if mat is not None:
        mat[PROP_PROBE] = int(probe_spec.get("index", -1))
        mat[PROP_MODE] = MODE_SPECULAR
        mat[PROP_FILE] = str(probe_spec.get("resolved_file")
                             or probe_spec.get("cube_file") or "")
    return {
        "wired": True,
        "probe": int(probe_spec.get("index", -1)),
        "image": env_image.name,
        "intensity": intensity,
        "fresnel_ior": ior,
        "roughness_source": roughness_source,
        "mip_used": 0,
        "mipcount_on_disk": int(probe_spec.get("mipcount") or 0),
    }


def wire_object(ob, ctx, obj_manifest, opts=None) -> dict:
    """Wire every material slot of `ob` for the probe its mesh record names.

    Materials are datablocks and probes are per-MESH, so a material shared by two
    meshes on two probes is copied into a `__probeN` variant — the same rule
    `lightmap_builder` uses for the lightmap page.
    """
    if bpy is None:                                    # pragma: no cover
        return {"wired": 0, "reason": "no bpy"}
    if RP is None:
        return {"wired": 0, "reason": "le_mesh unavailable"}
    spec = probe_spec_for_object(ctx, obj_manifest)
    if not spec:
        return {"wired": 0, "reason": "mesh names no probe"}
    if resolved_mode(opts) == MODE_OFF:
        return {"wired": 0, "reason": "probe_mode=off"}
    probe = int(spec["index"])
    img = equirect_image_for_probe(ctx, probe, opts)
    if img is None:
        return {"wired": 0, "reason": "no cube DDS for probe %d" % probe}
    reports = []
    for slot in ob.material_slots:
        mat = slot.material
        if mat is None or not mat.use_nodes:
            continue
        if mat.get(PROP_PROBE) not in (None, probe):
            want = variant_name(mat.name.split("__probe")[0], probe)
            variant = bpy.data.materials.get(want)
            if variant is None:
                variant = mat.copy()
                variant.name = want
            slot.material = variant
            mat = variant
        elif mat.get(PROP_PROBE) == probe:
            reports.append({"wired": True, "probe": probe, "already": True})
            continue
        reports.append(wire_ambient_specular(
            mat, mat.node_tree, _find_principled(mat.node_tree), spec, img, opts))
    return {"wired": sum(1 for r in reports if r.get("wired")),
            "probe": probe, "reports": reports}
