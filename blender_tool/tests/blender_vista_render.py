"""Assemble and render the EXTERIOR level — Saturn, its rings, the debris field
and the dig site — as one scene, with the skydome special-cased on purpose.

    blender.exe --background --factory-startup \
        --python <ABS WINDOWS PATH>\\blender_vista_render.py -- \
        mesh=<...>.lemesh scatter=<...>.lescatter out=<...>.png

NOT named `test_*`: `tests/run_tests.py` imports every `test_*.py` under plain
`python3` and this needs `bpy`.  The placement math it reports is pure and IS
tested there (`le_mesh/vista_fit.py` / `tests/test_vista_fit.py`); the report
generator is `tests/vista_measure.py`.

★★ THE PROBLEM THIS FILE EXISTS TO SOLVE
========================================
docs/SCENES.md §5b/§5c: the skydome is an ordinary mesh
whose material carries `mattype == 13 (eMTSkydome)`, and that one value routes it
to `ERenderPool::eSkydome = 19` — **after** forward opaques (10), alpha-tested
(11), transparents (12) and refractions (18) — with its own `skydomertstate`.
The artists therefore never had to keep the vista inside the dome, and they did
not: **26.1 % of Saturn's vertices and 24.8 % of ring `obj035`'s lie outside the
59,172-unit shell** (`measured`, re-derived by `vista_measure.py`).

Blender is an ordinary depth-sorted renderer.  Import the level as-is and the
shell punches a circular hole through Saturn's far limb and the outer rings.
`skydome=` selects the special case, and every mode is renderable so the choice
is falsifiable rather than asserted:

  ``engine``     ★ DEFAULT since 0.5.0, and the one the shipped shader picks.
                 The dome is left as ORDINARY DEPTH-TESTED GEOMETRY, exactly
                 where it sits, and only its NON-CAMERA ray visibility is
                 cleared.  See "WHAT THE SHADER SETTLED" below for why that is
                 the engine's behaviour and why the ray-visibility half is a
                 path-tracer correction rather than a second special case.
  ``composite``  The pre-0.5.0 default, and the reading the shader refuted.  The
                 skydome goes into its own collection and its own view layer;
                 the scene renders with `film_transparent`, and the compositor
                 puts the main layer OVER the sky layer.  "Draw the sky into the
                 pixels the scene did not cover" is exactly what pool 19 after
                 12/18 amounts to, and it is exact — no geometry is moved and no
                 depth is faked.
                 ⚠ Cycles writes PREMULTIPLIED alpha, and Blender's Alpha Over
                 with `use_premultiply = False` computes `fg + (1 - a_fg)·bg`.
                 That means an ADDITIVE surface (the `eBlendLinearDodge` ring
                 haze: colour > 0, alpha ≈ 0) lands on the sky as `sky + haze`,
                 i.e. additive, which is the correct engine behaviour and is why
                 this mode was preferred over the geometric one below.
  ``scale``      Scale the dome about the WORLD ORIGIN by `skydome_scale` until
                 nothing pierces it.  ⚠ NOT free: it is EXACT for a camera at
                 the origin (scaling about the origin does not move any
                 direction) and costs starfield PARALLAX everywhere else — at
                 the play area's max |T| = 1,720 the stars shift from 1.665° to
                 1.234° at K = 1.35, a stated 0.43°.  Single-pass, so blend and
                 additive surfaces composite through the normal Cycles path.
  ``depth``      `engine`'s depth behaviour with the ray visibility left alone,
                 i.e. a LIGHT-TIGHT dome.  Renderable so that artefact stays a
                 picture rather than a warning.
  ``off``        Delete the dome entirely (the vista against black).

★★ WHAT THE SHADER SETTLED.  The depth behaviour of the engine's skydome pass
used to be an open question here — nothing in the engine's own material
authoring carries a depth state, and the code that derives one from the material
type is not readable from the shipped bytes — so the harness offered `composite`
and `depth` side by side and asserted neither.  `a849eddeb321dcc7`'s own shaders
answer it: the vertex shader APPLIES the view matrix's translation row, so the
dome does not follow the camera; it passes the full projection to `SV_Position`
and never rewrites it, so there is no reversed-Z far-plane pin; and the pixel
shader declares `SV_Target 0/1` only, with no `SV_Depth` and no discard.  ⇒ the
dome is drawn at its own true projected depth and OVERWRITES anything farther
than the shell.  `composite` was the competing reading and it is refuted.

⚠ One thing that reading does NOT license: a closed dome is light-tight to a
PATH TRACER, and a Cycles SUN sits at infinity, hence always outside it.  That
is a property of the renderer, not of the engine — the engine's dome blocks no
light because the engine traces none.  `engine` therefore keeps the depth
behaviour and clears the dome's non-camera ray visibility; `depth` keeps both.

★ THE SECOND HONEST GAP: the vista's own colour
===============================================
Four vista materials resolve **no colour channel at all** and carry their texture
as an `rdef_bindN` whose ROLE no array in the corpus declares:

    obj018 skydome  a849edde…__9741ae71…  rdef_bind0 -> vst_starfield_nebula_clr
    obj002 sun      35a8c5ad…__2fc19c8b…  rdef_bind1/2 -> vst_sun, vst_sun_hdr_opc
    obj003/4 haze   340f6ff7…__d319e14b…  rdef_bind0 -> vst_saturn_rings_horizon_haze_clr

⛔ Routing a bind whose role is unknown into a colour socket is a GUESS, so the
default here is the same one `material_builder.additive_unrouted_color` already
took: **contribute nothing**.  For the skydome that means an unlit dome, not a
white shell — the `bakecolor` fallback is `(1,1,1,1)` and drawing it would
fabricate a white sky.  `vista_unrouted_color=1` opts into the inferred wiring
(single unrouted bind, or the one bind whose asset name is not a mask/height/
normal suffix), stamps `le_vista_unrouted_color` on the material and prints it.

OPTIONS (`key=value` after `--`, same convention as blender_hero_render.py)
--------------------------------------------------------------------------
    mesh=<pkg>.lemesh          the level's ROOT mesh-list (the vista lives here)
    scatter=<pkg>.lescatter    the level's static instances (comma-separated ok)
    materials_json=<path>      scatter material sidecar (auto-found by default)
    lightmap=baked|ambient|none        sets BOTH paths at once
    mesh_lightmap=             the ROOT mesh-list's mode, default `ambient`
    scatter_lightmap=          the static instances' mode, default `baked`
                               ⛔ they differ for a MEASURED reason: Saturn and
                               the moons point at atlas page 13 and page 13 is
                               empty (all 5 SG slices: median 0.0000, p95
                               0.0000, max <= 0.09), so `baked` renders the
                               planet BLACK.  The dig site's pages are real.
    lightmap_texture=<dds>     the level's BC6H atlas
    lightmap_slice_dir=<dir>   pre-split per-page slices
    instance_lightmap=1|0      default 1 — MANDATORY for a level bake
    lod=N                      scatter LOD, default 0
    max_instances=N            cap for a fast/cheap render (0 = all)
    layer=all|vista|scatter|meshlist    what to import at all
    only=/drop=                substring object filter (same-camera A/B)
    skydome=engine|composite|scale|depth|off   default `engine` (0.5.0; it was
                               `composite` before, and the shader refuted that —
                               see "WHAT THE SHADER SETTLED" above)
    skydome_scale=K            default 1.35 (>= the measured minimum, printed)
    vista_unrouted_color=1|0   default 0 — see above
    additive_unrouted_color=1|0        passed to material_builder (haze)
    sun=auto|lights|card|rig|none      default `auto`
                               ★★ `lights` (and `auto` when `lights=` is given)
                               builds the level's OWN `CGLight` rig from the
                               extracted sidecar.  `card` is the OLD behaviour and
                               it FABRICATES a light the engine does not have —
                               `min_itc_master` ships 2 eDirectionalLight records
                               at 6.6 deg and 16.5 deg from the card's centroid,
                               with peak 80 and 10 W/m^2, not 14.83.  It is kept
                               only so old renders remain reproducible and it
                               prints a warning every time.
    lights=<lights.json>       the `le_lights` sidecar for this level
                               (`extractor/le_lights.py <archive> --out ...`)
    light_set=diffuse|all|enabled      default diffuse (eEnableDiffuse only)
    sun_energy=                default 14.83 = the REAL solar constant at
                               Saturn's orbit (1361 / 9.5826^2 W/m^2).  A
                               defensible anchor, NOT a decoded engine value.
                               ⛔ only used by `card`/`rig`; the decoded rig
                               carries the shipped `primarycolor` instead.
    vista_shader=1|0           default 1 — apply the SHADER-CONFIRMED terms from
                               `le_mesh.vista_shader` (disassembled pixel
                               shaders) to the vista materials the importer
                               builds from `base_color_factor`.  ⛔ Those
                               constants are TRANSCRIBED, not derived here — see
                               the module docstring.
    saturn_detail=1|0          default 1 — ★★ Saturn's THREE detail plates and
                               the four flow-warped UV chains
                               of `6f67762bf83d59fd`.  This is the banding;
                               without it the disc is one unwarped 4096² plate.
    saturn_time=               default 0.0 — `k_time0_x` (cb0[0].y), the ONLY
                               time driver in the Saturn shader.  Per-FRAME and
                               in no level resource, so 0.0 (a still) and
                               ⛔ NOT fitted.  At 0 the three `uv0` flow taps
                               collapse onto one texture fetch.
    ring_env=<exr|cube dds>    ★★ the ring sheet's WHOLE ambient
                               (`ba863c7b2cb61616` binds no colour lightmap).
                               A `.dds` is read as a probe CUBE STRIP and
                               resampled to an equirect here, which is the only
                               way to reach the shipped mip PREFILTER — the
                               shader fetches lod 3.56 and Blender's
                               `TexEnvironment` has no LOD input.
    colourless_surface=transparent|flat
                               default `transparent` — ★★ a material that
                               resolves NO colour channel and no wired bind gets
                               a Transparent BSDF instead of a flat
                               `base_color_factor` card.  5 of this level's
                               mesh-list materials are in that class and their
                               flat cards are the pale straight-edged polygons
                               across Saturn.  `flat` keeps the defect.
    fx_card_additive=1|0       default 1 — ★★ `b9588078adab3e49` (`obj013`-
                               `obj017`), the dig-site steam/dust cards.  They
                               are `eBlendLinearDodge` AND their `o0.rgb` reads
                               **no texture at all**: the colour is a constant
                               and both colour plates are sampled for their ALPHA
                               lane only.  Drawn as the importer's lit dust plate
                               they are the pale straight-edged quads across
                               Saturn.  0 leaves that defect renderable.
    fx_card_src_alpha=1|0      default 1 — `eBlendLinearDodge`'s unrecovered
                               SOURCE factor.  1 = `dst += rgb*a`, 0 = `dst +=
                               rgb`, which draws the flat pale polygon the
                               engine's own probe does not show.  `inferred`.
    fx_card_time=              default = `saturn_time` — the SAME `k_time0_x`
                               (cb0[0].y).  Per-FRAME, ⛔ not fitted.  At 0 all
                               three UV scrolls vanish.
    haze_additive=1|0          default 1 — ★★ the ring-haze cards are
                               `eBlendLinearDodge`, so they ADD and never
                               occlude.  0 reproduces the two defects they
                               shipped with here (opaque card => black polygonal
                               wedges through Saturn and the rings; tint applied
                               to the wrong Emission => 5-6x too bright).
    world_ambient_spec=        default 1.0 — `k_world_ambient_spec` (cb0[2],
                               `SGPerFrameConstants` +32).  ★ ONE constant, TWO
                               consumers: Saturn's ambient-spec CUBE branch and
                               the ring sheet's entire ambient, so it defaults
                               BOTH `rim_gain` and `ring_ambient_spec`.  It is
                               per-FRAME and in NO level resource.  ⛔ 1.0 is not
                               a decoded value and nothing here is fitted to art;
                               any other value must be justified against the
                               reflection-probe cube and stated in the log.
    rim_gain= ring_ambient_spec=   per-term overrides of the above.
    fog=                       default 0.0 — ★★ the SCENE-FOG epilogue at the
                               tail of `6f67762bf83d59fd`'s pixel shader (and
                               verbatim in the ring sheet and the sun card):
                                 `o0.rgb = lerp(colour, C.rgb*k_fog_color.rgb, f)`
                               with `f = C.a*k_fog_color.a*k_fog_ramp(distance)`
                               and `C = lerp(k_fog_low_color, k_fog_hi_color,
                               k_fog_ramp(height))`.  Saturn is 19-38 kilo-units
                               away and its disc spans ±31,600 units of world
                               HEIGHT, so both ramps are fully engaged on it, and
                               this is the whole of the `a+30` anomaly: the
                               engine's own probe reads **0.150** of what the
                               unfogged terms compute over the sunward disc.
                               ⛔ Every value is per-FRAME and NONE is decoded, so
                               the default is 0 = OFF and any other value must be
                               stated.
    fog_color=r,g,b            default 0,0,0 — the already-multiplied
                               `C.rgb * k_fog_color.rgb`.  Per-FRAME.
    fog_shadersets=saturn|fogged   default `saturn`.  All three of
                               `SHADERSET_BINDS_FOG` carry the epilogue, but the
                               Mix Shader this builds is exact only for an OPAQUE
                               consumer: the shipped shader fogs `o0.rgb` and
                               leaves `o0.a` alone, so on the ring sheet and the
                               sun card `fogged` would also attenuate the sky that
                               comes through them.  Stated, and renderable.
    ring_ao=                   default 1.0 — `AO_R`.  The ring's `uv2` is (0,0)
                               on all 3638 vertices and its page index is the
                               0xFFFFFFFF sentinel, so this is ONE texel of an
                               engine-CREATED default resource. `inferred`.
    world_ambient=             default 1.0 — `k_world_ambient`, the per-FRAME
                               engine constant that multiplies the SG5 ambient in
                               every lit shader.  It is NOT in any level resource;
                               1.0 is what a matched-rock control implies.
    world=                     world background strength, default 0.0
    cam=reference|orbit|explicit       default reference
    cam_loc= cam_target=       explicit camera (Blender space)
    cam_back= cam_lift=        reference-camera standoff / lift, in game units
    lens= resx= resy= samples= engine=cycles|eevee device=optix|cuda|cpu
    tile_size=N                default 0 = leave Blender's own auto-tile
                               settings alone.  A non-zero value forces
                               `use_auto_tile` on with that tile size, which is
                               what lets a 66 MP frame fit a 4 GB card.  The
                               effective tiling is LOGGED either way.
    denoise=1|0                default 1 (unchanged).  ⚠ Cycles keeps every pass
                               at FULL FRAME on the HOST even when it renders in
                               tiles, and denoising adds a whole extra float4
                               ("Noisy Image") plus the guide passes.  At
                               12288x5376 one float4 pass is 1.057 GB, and on a
                               host that only leaves blender.exe ~10 GB that is
                               the difference between a picture and an
                               ALL-BLACK frame (Cycles logs `Calloc returns
                               null` and saves anyway).  0 trades denoising for
                               the resolution.
    strips=N                   default 0 = one whole-frame render (unchanged).
                               N > 1 renders the frame as N horizontal border
                               regions (`use_border` + `use_crop_to_border`) in
                               ONE session and writes `<out>.stripNN.png` plus a
                               `<out>.strips.json` manifest; the caller stitches.
                               `tile_size` bounds the DEVICE buffer only — the
                               full-res render result, the compositor and the
                               denoising albedo/normal passes are host RAM and
                               scale with image AREA, so 66 MP needs this.
    strip_overlap=N            default 0 — extra rows rendered on each inner
                               strip edge so the stitcher can cross-fade.  The
                               denoiser sees one strip at a time, so its output
                               differs slightly across a hard join; the fade is
                               what makes the seam invisible.
    view= look= exposure=      colour management (view read back, as always)
    out=<png>                  output path; a <out>.log.txt is written beside it
"""

from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import bpy                                          # type: ignore
from mathutils import Matrix, Vector                # type: ignore

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for _p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import lone_echo_import                             # noqa: E402
from le_mesh import framing                         # noqa: E402
from le_mesh import vista_fit as VF                 # noqa: E402
from le_mesh import vista_shader as VSH             # noqa: E402

#: `eMTSkydome` — `CGMaterial::EMaterialType` (`name-confirmed`).
#: Stream-confirmed on disk: `min_itc_master`'s dome material carries `mattype 13`.
MATTYPE_SKYDOME = 13

#: Asset-name suffixes that are certainly NOT a colour map.  Used ONLY to break a
#: two-way tie under `vista_unrouted_color`; the result is still `inferred`.
NON_COLOUR_SUFFIXES = ("_opc", "_msk", "_hgt", "_nml", "_occ", "_wgt", "_spc")

SKYDOME_COLLECTION = "le_skydome"
LAYER_MAIN = "main"
LAYER_SKY = "sky"

_log: list[str] = []


def say(tag, msg):
    line = f"[{tag}] {msg}"
    print(line, flush=True)
    _log.append(line)


# ---------------------------------------------------------------------------
# option plumbing
# ---------------------------------------------------------------------------

def parse_argv():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = {}
    for a in argv:
        if "=" in a:
            k, v = a.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def opt_f(o, k, d):
    return float(o[k]) if k in o else d


def opt_i(o, k, d):
    return int(o[k]) if k in o else d


def opt_b(o, k, d):
    return o[k].lower() in ("1", "true", "yes", "on") if k in o else d


def opt_vec(o, k):
    if k not in o:
        return None
    parts = [p for p in o[k].replace(" ", "").split(",") if p]
    if len(parts) != 3:
        raise SystemExit(f"{k} must be x,y,z (got {o[k]!r})")
    return tuple(float(p) for p in parts)


# ---------------------------------------------------------------------------
# game <-> Blender
# ---------------------------------------------------------------------------

def game_to_blender(v):
    """The ONE axis convention, restated here so a direction never gets guessed.

    `mesh_builder._axis_matrix` / `scatter_reader.basis_matrix` apply a pure +90°
    rotation about X, i.e. game ``(x, y, z) -> (x, -z, y)``.  Determinant +1, no
    mirror (`AXIS_CALIBRATION.md`).
    """
    return (v[0], -v[2], v[1])


# ---------------------------------------------------------------------------
# the vista roster, read off the package rather than hardcoded
# ---------------------------------------------------------------------------

def load_manifest(pkg_path):
    p = Path(pkg_path)
    if p.name == "manifest.json":
        p = p.parent
    return p, json.loads((p / "manifest.json").read_text("utf-8"))


def rdef_names(tsv_path, archive):
    """{texture_hash: name} from the RDEF harvest.

    ⛔ NO `is_texture_resource` filter — that column is a RESIDENCY flag and all
    30 of this level's `vst_*` binds carry 0.  Filtering on it is exactly what
    produced (and forced the retraction of) "Saturn binds nothing".
    """
    out = {}
    if not tsv_path:
        return out
    with open(tsv_path, "r", encoding="utf-8") as fh:
        head = fh.readline().rstrip("\n").split("\t")
        ia, ih, inm = head.index("archive_hash"), head.index("name_hash"), \
            head.index("name")
        for line in fh:
            f = line.rstrip("\n").split("\t")
            if len(f) <= max(ia, ih, inm):
                continue
            if archive and f[ia] != archive:
                continue
            out.setdefault(f[ih], f[inm])
    return out


def skydome_objects(objects):
    """Objects carrying a material with `le_mattype == 13`.

    Identified by the MATERIAL TYPE, never by name or by "it is the big sphere" —
    docs/SCENES.md records exactly that mistake: geometry cannot
    tell a planet from the sky it hangs in, and the shaders can.
    """
    hits = []
    for ob in objects:
        if ob.type != "MESH" or not ob.data:
            continue
        for slot in ob.data.materials:
            if slot is not None and slot.get("le_mattype") == MATTYPE_SKYDOME:
                hits.append(ob)
                break
    return hits


# ---------------------------------------------------------------------------
# the honest wiring of an unrouted vista bind
# ---------------------------------------------------------------------------

def _pick_unrouted(spec, names):
    """The single unrouted bind that could be the colour, or None (REFUSE).

    Same shape as `material_builder.additive_unrouted_color_role`: one candidate
    and only one, or nothing.  The only addition is that a two-bind material may
    be disambiguated when exactly one of the names is not a mask/height/normal —
    which is `inferred` and is stamped as such by the caller.
    """
    unrouted = list(spec.get("unrouted_roles") or [])
    tex = spec.get("role_textures") or {}
    cands = [r for r in unrouted if tex.get(r)]
    if not cands:
        return None, "no unrouted bind carries a texture hash"
    if len(cands) == 1:
        return cands[0], "single unrouted bind"
    keep = [r for r in cands
            if not names.get(tex[r], "").endswith(NON_COLOUR_SUFFIXES)]
    if len(keep) == 1:
        return keep[0], ("%d binds, %d after dropping mask/height/normal suffixes"
                         % (len(cands), len(keep)))
    return None, f"{len(cands)} unrouted binds and no rule to choose ({cands})"


def make_colourless_transparent(mat, mode="transparent"):
    """★★ Say "it contributes nothing" and MEAN it.

    `measured`: `min_itc_master`'s root mesh-list carries **5 materials that
    resolve no colour channel and no unrouted bind** (`54d28e9707ff66c5__…`,
    `912501e8145fc48b__…` x2, `b1c125e904af3e65__…` x2).  This harness has always
    printed "this material shows nothing, and that is what the archive says" for
    them — and then rendered them as a **flat opaque `base_color_factor` card**,
    because that is `material_builder`'s fallback when there is no base-colour
    map.  Those cards are the pale straight-edged polygons that lie across
    Saturn's disc in every hero render so far; they survive dropping the haze
    cards, the ring sheet and `obj009`-`obj012`, which is how they were pinned.

    ⛔ A flat `bakecolor` quad is a FABRICATED surface by exactly the argument
    `vista_unrouted_color` already makes about an unrouted bind: `bakecolor` is
    the *baker's* approximation of a surface, not a shipped albedo, and drawing
    it asserts a colour nobody decoded.  So the default is a Transparent BSDF —
    the material occludes nothing and adds nothing.  `colourless_surface=flat`
    keeps the old behaviour so the defect stays renderable.
    """
    if mode == "flat":
        return "left as the flat base_color_factor card (colourless_surface=flat)"
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        return "no output node — unchanged"
    tr = nt.nodes.new("ShaderNodeBsdfTransparent")
    tr.location = (out.location.x - 200, out.location.y)
    tr.label = "no colour channel -> contributes nothing"
    for l in list(out.inputs["Surface"].links):
        nt.links.remove(l)
    nt.links.new(tr.outputs[0], out.inputs["Surface"])
    mat["le_colourless_transparent"] = (
        "no channels and no wired bind: surface replaced with a Transparent BSDF "
        "so the material genuinely contributes nothing. The flat "
        "base_color_factor card it used to draw is `bakecolor`, which is the "
        "baker's approximation and not a shipped albedo.")
    return "surface -> Transparent BSDF (it now really shows nothing)"


def wire_unrouted_emission(mat, spec, pkg_dir, names, uv_layer="uv0"):
    """Replace `mat`'s surface with an Emission driven by its unrouted bind.

    ⛔ `inferred` AND opt-in.  The role of the bind is unknown; this asserts only
    that a material which resolves NO colour channel and binds exactly one image
    is probably showing that image.  Everything about it is stamped on the
    material so a reader of the .blend can see it was not decoded.
    """
    role, why = _pick_unrouted(spec, names)
    if role is None:
        return False, why
    tex = (spec.get("role_textures") or {})[role]
    rel = Path(pkg_dir) / "textures" / f"{tex}.dds"
    if not rel.is_file():
        return False, f"{rel.name} is not on disk"
    img = bpy.data.images.load(str(rel), check_existing=True)
    # ⛔⛔ THE TRAP THAT MADE THE SKY RENDER BLACK, and it is silent.
    # `bpy.data.images.load` defaults to `alpha_mode = 'STRAIGHT'`, and Blender
    # converts a straight-alpha image to PREMULTIPLIED for rendering — i.e. the
    # shader sees `RGB * A`.  `vst_starfield_nebula_clr` carries **alpha 0 on
    # every texel** (measured: A min/med/max = 0.0000/0.0000/0.0039), so the
    # starfield multiplied itself to nothing.  `image.pixels` still reads the
    # true RGB, so the image "looks fine" from Python while rendering pure black,
    # and raising Emission Strength 50x changes nothing — which is how the bug
    # announces itself.  `material_builder.image_alpha_mode` already defaults to
    # CHANNEL_PACKED for exactly this reason ("every RAD texture reads RGB and
    # alpha as independent signals"); this path has to say so too.
    img.alpha_mode = "CHANNEL_PACKED"
    # Colour, not data: these are `_clr` / HDR plates.  The DDS's own DXGI
    # decides sRGB-ness inside Blender's loader; force sRGB only for LDR formats
    # by leaving the default and recording what we got.
    nt = mat.node_tree
    out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        return False, "material has no output node"
    uvn = nt.nodes.new("ShaderNodeUVMap")
    uvn.uv_map = uv_layer
    uvn.location = (-1200, 600)
    tn = nt.nodes.new("ShaderNodeTexImage")
    tn.image = img
    tn.location = (-1000, 600)
    tn.label = f"{role} -> {names.get(tex, tex)} (INFERRED, role UNKNOWN)"
    nt.links.new(uvn.outputs["UV"], tn.inputs["Vector"])
    em = nt.nodes.new("ShaderNodeEmission")
    em.location = (-700, 600)
    em.inputs["Strength"].default_value = 1.0
    nt.links.new(tn.outputs["Color"], em.inputs["Color"])
    for link in list(out.inputs["Surface"].links):
        nt.links.remove(link)
    nt.links.new(em.outputs[0], out.inputs["Surface"])
    mat["le_vista_unrouted_color"] = (
        f"{role} -> {tex} ({names.get(tex, '?')}); role is UNKNOWN. "
        f"`inferred`, opt-in (vista_unrouted_color=1). reason: {why}")
    mat["le_vista_unrouted_colorspace"] = img.colorspace_settings.name
    mat["le_vista_unrouted_alpha_mode"] = img.alpha_mode
    return True, (f"{role} -> {names.get(tex, tex)} [{why}] "
                  f"cs={img.colorspace_settings.name} alpha={img.alpha_mode}")


# ---------------------------------------------------------------------------
# ★★ the SHADER-CONFIRMED vista terms  (le_mesh.vista_shader)
# ---------------------------------------------------------------------------
# Everything below replaces a value the importer *inferred* with one that was
# read out of the shipped pixel shader.  Nothing here is a look choice and
# nothing here is fitted; the shaderset each override came from is stamped on the
# material as `le_vista_shader_source` so a reader of the .blend can check the
# claim.

def _insert_scale(nt, socket, rgb, label):
    """Multiply everything `socket` drives by a constant RGB.

    A `ShaderNodeVectorMath` is used rather than a Mix node because its sockets
    are stable across Blender 4.x/5.x and a Vector output links to a Color input
    unchanged.  Every existing consumer is re-pointed, so the scale applies to
    the Base Color path, the Emission path and any lightmap multiply at once —
    which is what a coefficient on the texture SAMPLE means in the shader.
    """
    consumers = [(l.to_node, l.to_socket) for l in socket.links]
    if not consumers:
        return 0
    m = nt.nodes.new("ShaderNodeVectorMath")
    m.operation = "MULTIPLY"
    m.label = label
    m.location = (socket.node.location.x + 180, socket.node.location.y - 260)
    m.inputs[1].default_value = tuple(rgb)
    for node, insock in consumers:
        for l in list(insock.links):
            nt.links.remove(l)
    nt.links.new(socket, m.inputs[0])
    for node, insock in consumers:
        nt.links.new(m.outputs["Vector"], insock)
    return len(consumers)


def _image_node_for(nt, tex_hash):
    for n in nt.nodes:
        if n.type != "TEX_IMAGE" or n.image is None:
            continue
        if tex_hash and tex_hash in (n.image.filepath or "") + " " + (n.image.name or ""):
            return n
    return None


def apply_saturn_terms(mat, spec, color1=(1.0, 1.0, 1.0)):
    """Replace `base_color_factor` with the shaderset's own literals.

    ⛔ `6f67762bf83d59fd`'s pixel shader declares **no material constant buffer**
    (cb0 perframe, cb1 perview, cb6/cb7 reflection), so the material record's
    `bakecolor` — which is what the importer surfaces as `base_color_factor` —
    provably never reaches the GPU.  The shader's own coefficients are
    `plate x 0.434154`, a global `x 0.822`, and `x (1 + sum(saturate(BLEND)))`
    with `BLEND` = vertex colour set 1, which is 1.0 on 3703/3703 of Saturn's
    vertices.  Net **14.2x**.
    """
    shaderset = str(mat.get("le_shaderset", ""))
    scale, why = VSH.albedo_correction(shaderset,
                                       spec.get("base_color_factor",
                                                (1.0, 1.0, 1.0)),
                                       color1)
    if scale is None:
        return False, why
    plate = (spec.get("role_textures") or {}).get(
        VSH.SHADERSET_TERMS[shaderset]["plate_role"], "")
    node = _image_node_for(mat.node_tree, plate)
    if node is None:
        return False, f"no image node for plate {plate!r} in {mat.name}"
    n = _insert_scale(mat.node_tree, node.outputs["Color"], scale,
                      "shader-confirmed albedo (x%.3f)" % scale[0])
    if not n:
        return False, f"plate node {node.name} drives nothing"
    mat["le_vista_shader"] = why
    mat["le_vista_shader_scale"] = tuple(round(s, 6) for s in scale)
    mat["le_vista_shader_source"] = \
        VSH.SHADERSET_TERMS[shaderset]["shader"]
    return True, f"{why}; scale x{scale[0]:.3f} onto {n} consumer(s)"


def apply_sun_card_terms(mat, spec, pkg_dir, names):
    """The sun card's two shipped terms: `rgb x 0.2` and the `_opc` opacity.

    `35a8c5ad5fb8d894`'s pixel shader, in full:

        r0 = sample(vst_sun, uv).wxyz          ; .x = ALPHA, .yzw = RGB
        r1.x = sample(vst_sun_hdr_opc, uv).x   ; RED channel only
        r1.x *= 0.999924
        r0.x = pow(saturate(r0.x), 2.2) * vertexcolour.a
        alpha = saturate(r1.x * r0.x)          ; discard if <= 1e-4
        rgb   = vst_sun.rgb * vertexcolour.rgb * 0.2
        o0    = min(rgb_after_fog, 11000)

    The harness's `vista_unrouted_color` path wires the plate as a full-strength
    Emission and DROPS bind 2 entirely, which is 5x too bright before the opacity
    is even considered.  This restores both.
    """
    row = VSH.SHADERSET_TERMS.get(str(mat.get("le_shaderset", "")))
    if row is None or "rgb_scale" not in row:
        return False, "not the sun-card shaderset"
    nt = mat.node_tree
    em = next((n for n in nt.nodes if n.type == "EMISSION"), None)
    if em is None:
        return False, "no Emission node (was vista_unrouted_color=1 set?)"
    src = next((l.from_socket for l in em.inputs["Color"].links), None)
    if src is None:
        return False, "Emission Color is not driven by anything"
    _insert_scale(nt, src, (row["rgb_scale"],) * 3,
                  "shader-confirmed sun rgb (x%.2f)" % row["rgb_scale"])

    tex = (spec.get("role_textures") or {}).get(row["opacity_role"], "")
    opc = Path(pkg_dir) / "textures" / f"{tex}.dds"
    detail = f"rgb x{row['rgb_scale']}"
    if tex and opc.is_file():
        img = bpy.data.images.load(str(opc), check_existing=True)
        img.alpha_mode = "CHANNEL_PACKED"
        plate_node = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None)
        on = nt.nodes.new("ShaderNodeTexImage")
        on.image = img
        on.location = (em.location.x - 700, em.location.y - 400)
        on.label = f"{row['opacity_role']} -> {names.get(tex, tex)} (OPACITY, R)"
        if plate_node is not None:
            for l in plate_node.inputs["Vector"].links:
                nt.links.new(l.from_socket, on.inputs["Vector"])
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        sep.location = (on.location.x + 220, on.location.y)
        nt.links.new(on.outputs["Color"], sep.inputs["Color"])
        # pow(saturate(plate alpha), 2.2)
        gam = nt.nodes.new("ShaderNodeMath")
        gam.operation = "POWER"
        gam.inputs[1].default_value = row["alpha_gamma"]
        gam.location = (on.location.x + 220, on.location.y - 200)
        gam.label = "pow(vst_sun.a, 2.2)"
        if plate_node is not None:
            nt.links.new(plate_node.outputs["Alpha"], gam.inputs[0])
        else:
            gam.inputs[0].default_value = 1.0
        mul = nt.nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        mul.location = (sep.location.x + 220, sep.location.y - 100)
        mul.label = "alpha = opc.R * pow(a, 2.2)"
        nt.links.new(sep.outputs[0], mul.inputs[0])
        nt.links.new(gam.outputs[0], mul.inputs[1])
        scl = nt.nodes.new("ShaderNodeMath")
        scl.operation = "MULTIPLY"
        scl.inputs[1].default_value = VSH.SUN_CARD_OPC_SCALE
        scl.location = (mul.location.x + 200, mul.location.y)
        nt.links.new(mul.outputs[0], scl.inputs[0])
        # eBlendTransparent: mix Transparent <-> Emission on that alpha
        out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        tr.location = (em.location.x, em.location.y - 250)
        mixs = nt.nodes.new("ShaderNodeMixShader")
        mixs.location = (em.location.x + 250, em.location.y - 100)
        nt.links.new(scl.outputs[0], mixs.inputs["Fac"])
        nt.links.new(tr.outputs[0], mixs.inputs[1])
        nt.links.new(em.outputs[0], mixs.inputs[2])
        for l in list(out.inputs["Surface"].links):
            nt.links.remove(l)
        nt.links.new(mixs.outputs[0], out.inputs["Surface"])
        mat.blend_method = "BLEND" if hasattr(mat, "blend_method") else \
            mat.blend_method
        detail += f", opacity {names.get(tex, tex)} (R) x pow(a, 2.2)"
    else:
        detail += f", NO opacity plate on disk for {row['opacity_role']}={tex!r}"
    mat["le_vista_shader"] = detail
    mat["le_vista_shader_source"] = row["shader"]
    return True, detail


# ---------------------------------------------------------------------------
# ★★ the four terms recovered 2026-08-06 (P1 + Q2b + Q5 + the moons)
# ---------------------------------------------------------------------------
# Every literal below was read out of a shipped pixel shader and lives in
# `le_mesh/vista_shader.py`; the node graphs are here.  Five shadersets:
#   a849eddeb321dcc7  skydome
#   340f6ff7265f0077  haze
#   6f67762bf83d59fd  Saturn
#   ba863c7b2cb61616  rings
#   a1e53ff754dd1443  moons

def _tex_node(nt, pkg_dir, tex_hash, label, loc, colorspace=None):
    """Load `<pkg>/textures/<hash>.dds` as an image node, or return None."""
    p = Path(pkg_dir) / "textures" / f"{tex_hash}.dds"
    if not tex_hash or not p.is_file():
        return None
    img = bpy.data.images.load(str(p), check_existing=True)
    # Every RAD texture reads RGB and alpha as independent signals — a straight
    # -alpha load silently multiplies RGB by A (the bug that made the sky black).
    img.alpha_mode = "CHANNEL_PACKED"
    if colorspace:
        try:
            img.colorspace_settings.name = colorspace
        except (TypeError, ValueError):
            pass
    n = nt.nodes.new("ShaderNodeTexImage")
    n.image = img
    n.location = loc
    n.label = label
    return n


def _uv_node(nt, layer, loc):
    n = nt.nodes.new("ShaderNodeUVMap")
    n.uv_map = layer
    n.location = loc
    return n


def _env_image(path, label="env"):
    """An equirect environment image — from an EXR/HDR, or from a probe CUBE.

    ★ The `.dds` branch is what makes the shipped **prefilter** reachable.  Both
    vista specular terms fetch their cube at an explicit LOD —
    `SampleLevel(..., 7.017880)` on Saturn (`6f67762bf83d59fd`) and
    `max(0, 10*aR - 1) = 3.56` on the rings (`ba863c7b2cb61616`) —
    and `ShaderNodeTexEnvironment` has no LOD input at all.  A point sample of
    mip 0 therefore under-reads a blurry lobe by a large and unstated factor.
    The probe resource carries its OWN mip chain on disk (one cube strip per
    mip, M = 0..6), and those mips are read AS STORED: the reading in which the
    cube is pre-divided by the probe's `normalizations` is REFUTED, so nothing is
    applied to them here.

    ⚠ The mip is chosen by the CALLER and named in the log.  The cube has 7 mips
    (dim 128 down to 2), so Saturn's LOD 7.02 CLAMPS to mip 6 and the rings'
    3.56 falls between mips 3 and 4 — the fractional part is dropped, which is a
    stated approximation and not a fit.
    """
    p = Path(path)
    if not p.is_file():
        return None, f"{path!r} is not on disk"
    if p.suffix.lower() != ".dds":
        img = bpy.data.images.load(str(p), check_existing=True)
        return img, f"{p.name} — equirect image as-is"
    from lone_echo_import import probe_builder as PB
    src, dim = PB.load_cube_strip(str(p))
    px = [0.0] * len(src.pixels)
    src.pixels.foreach_get(px)
    w = max(64, min(1024, dim * 8))
    h = w
    flat = PB.equirect_pixels_from_strip(px, dim, w, h)
    name = f"le_env_{p.stem}"
    old = bpy.data.images.get(name)
    if old is not None:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(name, w, h, float_buffer=True)
    # ⚠ colour space FIRST, pixels LAST — a `images.new` datablock is GENERATED
    # and any re-generate (a colorspace write, `update()`) discards the buffer.
    PB._set_colorspace(img, PB.COLORSPACE_PROBE, PB.COLORSPACE_PROBE_FALLBACK)
    img.pixels.foreach_set(flat)
    return img, (f"{p.name} — probe cube, face dim {dim} -> {w}x{h} equirect; "
                 f"mip read AS STORED (normalizations NOT applied, H1/H3 refuted)")


def _math(nt, op, a=None, b=None, loc=(0, 0), label="", clamp=False):
    n = nt.nodes.new("ShaderNodeMath")
    n.operation = op
    n.location = loc
    n.label = label
    n.use_clamp = clamp
    for i, v in ((0, a), (1, b)):
        if v is None:
            continue
        if hasattr(v, "node"):
            nt.links.new(v, n.inputs[i])
        else:
            n.inputs[i].default_value = v
    return n


def _vmath(nt, op, a=None, b=None, c=None, loc=(0, 0), label=""):
    n = nt.nodes.new("ShaderNodeVectorMath")
    n.operation = op
    n.location = loc
    n.label = label
    for i, v in ((0, a), (1, b), (2, c)):
        if v is None:
            continue
        if hasattr(v, "node"):
            nt.links.new(v, n.inputs[i])
        elif isinstance(v, (int, float)):
            # SCALE puts its scalar in the dedicated "Scale" socket, not inputs[1]
            sock = n.inputs["Scale"] if op == "SCALE" and i == 1 else n.inputs[i]
            if hasattr(sock, "default_value"):
                try:
                    sock.default_value = v
                except (TypeError, ValueError):
                    sock.default_value = (v, v, v)
            if hasattr(v, "node"):
                nt.links.new(v, sock)
        else:
            n.inputs[i].default_value = tuple(v)
    return n


def _scale_socket(nt, op_node):
    return op_node.inputs["Scale"] if "Scale" in op_node.inputs else op_node.inputs[1]


def _mix_rgb(nt, fac, a, b, loc=(0, 0), label=""):
    """`lerp(a, b, fac)` as a Blender 4.x/5.x `ShaderNodeMix` in RGBA mode."""
    n = nt.nodes.new("ShaderNodeMix")
    n.data_type = "RGBA"
    n.blend_type = "MIX"
    n.location = loc
    n.label = label
    fs = n.inputs["Factor"]
    if hasattr(fac, "node"):
        nt.links.new(fac, fs)
    else:
        fs.default_value = fac
    for name, v in (("A", a), ("B", b)):
        sock = [s for s in n.inputs if s.name == name and s.type == "RGBA"][0]
        if hasattr(v, "node"):
            nt.links.new(v, sock)
        else:
            sock.default_value = (*tuple(v)[:3], 1.0)
    return n, [s for s in n.outputs if s.type == "RGBA"][0]


def _surface_of(mat):
    out = next((n for n in mat.node_tree.nodes if n.type == "OUTPUT_MATERIAL"), None)
    if out is None:
        return None, None
    src = next((l.from_socket for l in out.inputs["Surface"].links), None)
    return out, src


def apply_skydome_terms(mat, spec, pkg_dir, names):
    """`max(0, plate.rgb * K + B)`, opaque, at `uv = (u, 2v)`.

    ★★ THIS IS ALSO THE RING-HUE FIX.  The harness's `vista_unrouted_color` path
    wires `vst_starfield_nebula_clr` RAW, and that plate is measurably RED-BROWN
    (linear mean `(0.00507, 0.00392, 0.00299)`, R/B = 1.695).  The shipped shader
    remaps it through a strongly BLUE tint (B/R = 1.94), which turns the sky
    `B/R = 1.30`.  Since the ring sheet passes ~92 % of the sky through its
    alpha, routing the plate raw is what made our rings read dark red-brown
    against the engine's cold blue-white — two independent disassemblies, of the
    dome and of the sheet, converged on this one cause.
    """
    row = VSH.SHADERSET_TERMS.get(str(mat.get("le_shaderset", "")))
    if row is None or "emissive_floor" not in row:
        return False, "not the skydome shaderset"
    nt = mat.node_tree
    em = next((n for n in nt.nodes if n.type == "EMISSION"), None)
    if em is None:
        return False, "no Emission node (was vista_unrouted_color=1 set?)"
    src = next((l.from_socket for l in em.inputs["Color"].links), None)
    if src is None:
        return False, "Emission Color is not driven by anything"

    tint = _vmath(nt, "MULTIPLY", src, tuple(row["emissive_tint"]),
                  loc=(em.location.x - 300, em.location.y - 200),
                  label="skydome tint K (shader-confirmed)")
    floor = _vmath(nt, "ADD", tint.outputs["Vector"], tuple(row["emissive_floor"]),
                   loc=(em.location.x - 150, em.location.y - 200),
                   label="skydome floor B")
    clampn = _vmath(nt, "MAXIMUM", floor.outputs["Vector"], (0.0, 0.0, 0.0),
                    loc=(em.location.x - 20, em.location.y - 200),
                    label="max(0, .)")
    for l in list(em.inputs["Color"].links):
        nt.links.remove(l)
    nt.links.new(clampn.outputs["Vector"], em.inputs["Color"])

    # uv = (u, 2v).  `measured`: obj018's uv0.v spans exactly [0.5, 1.0], so the
    # x2 covers the plate ONCE — a packing convention, not a tiling.  After the
    # importer's flip_v (v_bl = 1 - v_dx) the Blender-space equivalent is a plain
    # Scale Y = 2 with v_bl in [0, 0.5]; no wrap is even reached.
    plate_node = next((n for n in nt.nodes if n.type == "TEX_IMAGE"), None)
    uv_note = "no image node to remap"
    if plate_node is not None:
        uvsrc = next((l.from_socket for l in plate_node.inputs["Vector"].links), None)
        if uvsrc is not None:
            m = nt.nodes.new("ShaderNodeMapping")
            m.location = (plate_node.location.x - 250, plate_node.location.y - 150)
            m.label = "uv = (u, 2v) — shader-confirmed"
            m.inputs["Scale"].default_value = (1.0, row["uv_v_scale"], 1.0)
            nt.links.new(uvsrc, m.inputs["Vector"])
            for l in list(plate_node.inputs["Vector"].links):
                nt.links.remove(l)
            nt.links.new(m.outputs["Vector"], plate_node.inputs["Vector"])
            uv_note = f"uv scale Y x{row['uv_v_scale']}"

    mat["le_vista_shader"] = (
        "skydome: max(0, plate*%s + %s), opaque; %s"
        % (tuple(round(v, 6) for v in row["emissive_tint"]),
           tuple(round(v, 6) for v in row["emissive_floor"]), uv_note))
    mat["le_vista_shader_source"] = row["shader"]
    return True, mat["le_vista_shader"]


def apply_haze_terms(mat, spec, additive=True):
    """`plate * pow(saturate(vcol.rgb), 2.2) * C`, additive, alpha = vcol.a.

    The ps binds ZERO constant buffers and zero light resources: no Fresnel, no
    normals (the vertex format has none), no time scroll, no fog.  `measured`:
    both cards carry vertex alpha 1.0 everywhere, so the discard never fires and
    `eBlendLinearDodge`'s unrecovered source factor is moot on this level.

    ★★ `additive=False` reproduces the TWO defects this function shipped with,
    so both are a picture rather than a warning:
      1. the tint `C` was applied to the FIRST Emission in the node list, which
         is not the one driving Surface — so it reached a dead branch and the
         cards rendered at the raw plate value, `measured` 5-6x the engine's own
         probe on Saturn's disc-centre and limb patches;
      2. the card was left OPAQUE, so its dark texels punched black polygons
         through Saturn and the rings.
    """
    if str(mat.get("le_shaderset", "")) != VSH.HAZE_SHADERSET:
        return False, "not the haze shaderset"
    row = VSH.SHADERSET_TERMS.get(str(mat.get("le_shaderset", "")))
    if row is None or "vcol_gamma" not in row:
        return False, "not the haze shaderset"
    nt = mat.node_tree
    # ⚠ Take the Emission that actually DRIVES the surface, not the first one in
    # the node list: this material carries more than one and picking the wrong
    # one silently tints a dead branch.  `measured`: fixing only this took the
    # disc-centre patch from 5.26x the probe to 0.86x.
    out, surf = _surface_of(mat)
    em = surf.node if (additive and surf is not None
                       and surf.node.type == "EMISSION") else None
    if em is None:
        em = next((n for n in nt.nodes if n.type == "EMISSION"), None)
    if em is None:
        return False, "no Emission node (was additive_unrouted_color=1 set?)"
    src = next((l.from_socket for l in em.inputs["Color"].links), None)
    if src is None:
        return False, "Emission Color is not driven by anything"

    vc = nt.nodes.new("ShaderNodeVertexColor")
    vc.layer_name = "color0"
    vc.location = (em.location.x - 700, em.location.y - 300)
    gam = nt.nodes.new("ShaderNodeGamma")
    gam.location = (em.location.x - 500, em.location.y - 300)
    gam.label = "pow(saturate(vcol.rgb), 2.2)"
    gam.inputs["Gamma"].default_value = row["vcol_gamma"]
    nt.links.new(vc.outputs["Color"], gam.inputs["Color"])

    mulv = _vmath(nt, "MULTIPLY", src, gam.outputs["Color"],
                  loc=(em.location.x - 320, em.location.y - 220),
                  label="x pow(vcol, 2.2)")
    tint = _vmath(nt, "MULTIPLY", mulv.outputs["Vector"], tuple(row["emissive_tint"]),
                  loc=(em.location.x - 160, em.location.y - 220),
                  label="haze tint C (shader-confirmed)")
    for l in list(em.inputs["Color"].links):
        nt.links.remove(l)
    nt.links.new(tint.outputs["Vector"], em.inputs["Color"])

    # ★★ THE BLACK POLYGONAL WEDGES, and they were OURS, not the level's.
    # `wire_unrouted_emission` replaces the surface with a bare
    # `ShaderNodeEmission`, and a bare Emission in Cycles is an OPAQUE surface:
    # every texel where this plate is dark renders as a black quad that occludes
    # Saturn, the rings and the dig site behind it.  `measured`: with the two
    # cards on, Saturn's disc-centre patch is cut by two hard straight black
    # edges and most of the disc disappears.
    # ⛔ The shipped card cannot do that.  Its blend mode is `eBlendLinearDodge`
    # — `dst = src + dst` — so it NEVER occludes, whatever its colour, and
    # `measured` both cards carry vertex alpha 1.0 on every vertex so the
    # unrecovered source factor is moot ((ONE,ONE) == (SRC_ALPHA,ONE) at a = 1).
    # `AddShader(Emission, Transparent)` is that equation exactly: the ray passes
    # through and the emission is added on top.
    # It is deliberately applied to WHATEVER drives Surface rather than to `em`:
    # the equation is a property of the blend mode, not of one node, so it stays
    # right if the importer's surface is a Mix/Add rather than a bare Emission.
    out, surf = _surface_of(mat)
    add_note = "surface not re-pointed (nothing drives Surface)"
    if not additive:
        add_note = ("⛔ haze_additive=0: the card is left OPAQUE and the tint "
                    "targets the wrong Emission — the pre-fix defect, on purpose")
    elif out is not None and surf is not None:
        already = (surf.node.type == "ADD_SHADER"
                   and any(l.from_node.type == "BSDF_TRANSPARENT"
                           for i in surf.node.inputs for l in i.links))
        if already:
            add_note = "already additive (%s)" % surf.node.name
        else:
            tr = nt.nodes.new("ShaderNodeBsdfTransparent")
            tr.location = (out.location.x - 200, out.location.y - 220)
            addn = nt.nodes.new("ShaderNodeAddShader")
            addn.location = (out.location.x - 200, out.location.y - 100)
            addn.label = "eBlendLinearDodge: dst = src + dst (never occludes)"
            nt.links.new(surf, addn.inputs[0])
            nt.links.new(tr.outputs[0], addn.inputs[1])
            for l in list(out.inputs["Surface"].links):
                nt.links.remove(l)
            nt.links.new(addn.outputs[0], out.inputs["Surface"])
            add_note = ("ADDITIVE (%s + Transparent) — was OPAQUE, which is the "
                        "black polygonal wedge" % surf.node.name)
    mat["le_vista_shader"] = ("haze: plate * pow(vcol,2.2) * %s; %s"
                              % (tuple(round(v, 6) for v in row["emissive_tint"]),
                                 add_note))
    mat["le_vista_shader_source"] = row["shader"]
    return True, mat["le_vista_shader"]


def _dx_uv(nt, src, loc, label):
    """`(u, v) <-> (u, 1 - v)` — the importer's `flip_v`, as one node.

    It is its own inverse, so the SAME operation converts a Blender UV into the
    shader's DX space and a DX-space result back into a Blender lookup.  Doing
    the round trip is what makes the chain below a verbatim transcription of the
    disassembly instead of a per-term sign argument: `tile.y = 12` survives the
    flip modulo 1 (12·(1−v) ≡ −12v), but `tile_offset.y = −0.246` does NOT.
    """
    return _vmath(nt, "MULTIPLY_ADD", src, (1.0, -1.0, 0.0), (0.0, 1.0, 0.0),
                  loc=loc, label=label)


def apply_fx_card_terms(mat, spec, pkg_dir, names, time=0.0, src_alpha=True,
                        additive=True):
    """★★ THE PALE STRAIGHT-EDGED QUADS ACROSS SATURN — `b9588078adab3e49`.

    `shader-confirmed`, off `b9588078adab3e49`'s own pixel shader.  It is the
    ring-haze bug a second time, with one extra twist that makes it worse: not only is the card additive rather than opaque, **its colour is a
    constant and every texture it binds is an OPACITY input**.

        t   = k_time0_x                                            (cb0[0].y)
        f   = t1.Sample(uv*0.5 + t*(-0.000647,-0.002415)).rg
        uvW = uv + 0.22*(2f - 1)
        aD  = t2.Sample(uvW*2.0 + t*(-0.000434,-0.002462)).a
        aS  = t0.Sample(uvW*0.5 + t*(-0.000244, 0.002789)).a
        o0.a   = min(1, pow(sat(aS*0.784314),2.2)
                      * pow(sat(aD*0.721569+0.082353),2.2) * vcol.a^2)
        discard if o0.a <= 1e-4
        o0.rgb = clamp(pow(sat(vcol.rgb),2.2) * (1.120846,1.343614,1.856059), 0, 11000)

    ⛔ The importer could not have got this from the material record: that record
    routes `layer0_albedo_map` to Base Color and `layer0_emissive_map` to
    Emission, and the shipped shader reads the **`w` lane** of both
    (`t2.xywz -> r0.z`, `t0.wxyz -> r0.x`) and neither one's rgb.  So the harness
    was drawing a lit dust plate where the engine draws a soft additive fog.

    ⚠ `src_alpha` selects `eBlendLinearDodge`'s unrecovered SOURCE factor:
    `True` (default) is `dst += rgb*a`, `False` is `dst += rgb` — which is the
    flat pale polygon, kept renderable rather than argued away.  See
    `vista_shader.FX_CARD_SRC_FACTOR_IS_SRC_ALPHA` for why `True`.
    `additive=False` leaves the importer's OPAQUE surface alone entirely, i.e.
    the defect this function exists to remove.
    """
    if str(mat.get("le_shaderset", "")) != VSH.FX_CARD_SHADERSET:
        return False, "not the FX-card shaderset"
    row = VSH.SHADERSET_TERMS[VSH.FX_CARD_SHADERSET]
    nt = mat.node_tree
    out, surf = _surface_of(mat)
    if out is None:
        return False, "no Material Output"
    rt = spec.get("role_textures") or {}
    tex = {k: rt.get(v, "") for k, v in row["roles"].items()}
    missing = [f"{k}({row['roles'][k]})" for k, v in tex.items() if not v]
    if missing:
        return False, "no texture for " + ", ".join(missing)

    x0, y0 = out.location.x - 2800, out.location.y + 500
    uvn = _uv_node(nt, "uv0", (x0, y0))
    uvn.label = "TEXCOORD0 (the ps's only interpolated UV)"
    dxn = _dx_uv(nt, uvn.outputs["UV"], (x0 + 190, y0), "uv0 -> DX (v = 1 - v)")

    def _scroll(src, scale, scroll, loc, label):
        """`src*scale + t*scroll`, in DX UV space, as one MULTIPLY_ADD."""
        off = tuple(round(c * float(time), 9) for c in scroll)
        return _vmath(nt, "MULTIPLY_ADD", src, (scale, scale, 0.0),
                      (off[0], off[1], 0.0), loc=loc,
                      label="%s (t=%.6g -> %s)" % (label, time, off)).outputs["Vector"]

    # --- t1: the flowmap, the ONLY plate whose colour lanes are read ---------
    fuv = _scroll(dxn.outputs["Vector"], VSH.FX_CARD_FLOW_UV_SCALE,
                  VSH.FX_CARD_FLOW_SCROLL, (x0 + 380, y0), "uv*0.5 + t*flow_scroll")
    fback = _dx_uv(nt, fuv, (x0 + 600, y0), "DX -> uv (flow)")
    fimg = _tex_node(nt, pkg_dir, tex["flow"],
                     "t1 %s" % names.get(tex["flow"], tex["flow"]),
                     (x0 + 790, y0), colorspace="Non-Color")
    if fimg is None:
        return False, f"flowmap {tex['flow']!r} is not on disk"
    nt.links.new(fback.outputs["Vector"], fimg.inputs["Vector"])
    dec = _vmath(nt, "MULTIPLY_ADD", fimg.outputs["Color"], (2.0, 2.0, 0.0),
                 (-1.0, -1.0, 0.0), loc=(x0 + 1060, y0), label="flow*2 - 1")
    warp = _vmath(nt, "MULTIPLY_ADD", dec.outputs["Vector"],
                  (VSH.FX_CARD_WARP, VSH.FX_CARD_WARP, 0.0), dxn.outputs["Vector"],
                  loc=(x0 + 1240, y0), label="uv + %g*(2f-1)" % VSH.FX_CARD_WARP)

    # --- t2 / t0: two ALPHA taps off the same warped UV ----------------------
    plate = {"dust": (VSH.FX_CARD_DUST_UV_SCALE, VSH.FX_CARD_DUST_SCROLL, "t2"),
             "steam": (VSH.FX_CARD_STEAM_UV_SCALE, VSH.FX_CARD_STEAM_SCROLL, "t0")}
    alpha_of = {}
    for i, which in enumerate(("dust", "steam")):
        scale, scroll, slot = plate[which]
        puv = _scroll(warp.outputs["Vector"], scale, scroll,
                      (x0 + 1440, y0 - 330 * (i + 1)),
                      "warped*%g + t*scroll" % scale)
        back = _dx_uv(nt, puv, (x0 + 1640, y0 - 330 * (i + 1)),
                      "DX -> uv (%s)" % which)
        # ⚠ colorspace deliberately NOT set: this image datablock is shared with
        # the node the importer built, and only its ALPHA is read here — alpha is
        # never colour-managed, so touching the colour space would change another
        # material's colour to no purpose.
        img = _tex_node(nt, pkg_dir, tex[which],
                        "%s %s — ALPHA lane only" % (slot, names.get(tex[which],
                                                                    tex[which])),
                        (x0 + 1830, y0 - 330 * (i + 1)))
        if img is None:
            return False, f"{which} plate {tex[which]!r} is not on disk"
        nt.links.new(back.outputs["Vector"], img.inputs["Vector"])
        alpha_of[which] = img.outputs["Alpha"]

    # --- o0.a ----------------------------------------------------------------
    dmul = _math(nt, "MULTIPLY", alpha_of["dust"], VSH.FX_CARD_DUST_SCALE,
                 loc=(x0 + 2120, y0 - 330), label="aD x %g" % VSH.FX_CARD_DUST_SCALE)
    dsat = _math(nt, "ADD", dmul.outputs[0], VSH.FX_CARD_DUST_BIAS,
                 loc=(x0 + 2300, y0 - 330), clamp=True,
                 label="mad_sat + %g" % VSH.FX_CARD_DUST_BIAS)
    dpow = _math(nt, "POWER", dsat.outputs[0], VSH.FX_CARD_ALPHA_GAMMA,
                 loc=(x0 + 2480, y0 - 330), label="pow(., 2.2)")
    ssat = _math(nt, "MULTIPLY", alpha_of["steam"], VSH.FX_CARD_STEAM_SCALE,
                 loc=(x0 + 2120, y0 - 660), clamp=True,
                 label="mul_sat aS x %g" % VSH.FX_CARD_STEAM_SCALE)
    spow = _math(nt, "POWER", ssat.outputs[0], VSH.FX_CARD_ALPHA_GAMMA,
                 loc=(x0 + 2300, y0 - 660), label="pow(., 2.2)")

    vc = nt.nodes.new("ShaderNodeVertexColor")
    vc.layer_name = "color0"
    vc.location = (x0 + 2120, y0 - 990)
    va2 = _math(nt, "MULTIPLY", vc.outputs["Alpha"], vc.outputs["Alpha"],
                loc=(x0 + 2300, y0 - 990), label="vcol.a^2 (mul r0.y, v2.w, v2.w)")
    p1 = _math(nt, "MULTIPLY", spow.outputs[0], dpow.outputs[0],
               loc=(x0 + 2660, y0 - 500), label="steam x dust")
    p2 = _math(nt, "MULTIPLY", p1.outputs[0], va2.outputs[0],
               loc=(x0 + 2840, y0 - 500), label="x vcol.a^2")
    alpha = _math(nt, "MINIMUM", p2.outputs[0], 1.0,
                  loc=(x0 + 3020, y0 - 500), label="o0.a = min(., 1)")
    gate = _math(nt, "GREATER_THAN", alpha.outputs[0], VSH.FX_CARD_ALPHA_DISCARD,
                 loc=(x0 + 3200, y0 - 700),
                 label="discard_nz (a <= %g)" % VSH.FX_CARD_ALPHA_DISCARD)

    # --- o0.rgb: NO texture, only the vertex colour and one constant ---------
    gam = nt.nodes.new("ShaderNodeGamma")
    gam.location = (x0 + 2300, y0 - 1200)
    gam.label = "pow(saturate(vcol.rgb), 2.2)  [VS 282-284]"
    gam.inputs["Gamma"].default_value = row["vcol_gamma"]
    nt.links.new(vc.outputs["Color"], gam.inputs["Color"])
    tint = _vmath(nt, "MULTIPLY", gam.outputs["Color"], tuple(row["emissive_tint"]),
                  loc=(x0 + 2660, y0 - 1200), label="x C (shader-confirmed)")
    lo = _vmath(nt, "MAXIMUM", tint.outputs["Vector"], (0.0, 0.0, 0.0),
                loc=(x0 + 2840, y0 - 1200), label="max(., 0)")
    hi = _vmath(nt, "MINIMUM", lo.outputs["Vector"],
                (row["output_clamp"],) * 3,
                loc=(x0 + 3020, y0 - 1200), label="min(., %g)" % row["output_clamp"])

    col = hi.outputs["Vector"]
    if src_alpha:
        sa = _vmath(nt, "SCALE", col, loc=(x0 + 3200, y0 - 1200),
                    label="x o0.a  [eBlendLinearDodge = (SRC_ALPHA, ONE)]")
        nt.links.new(alpha.outputs[0], _scale_socket(nt, sa))
        col = sa.outputs["Vector"]
    disc = _vmath(nt, "SCALE", col, loc=(x0 + 3380, y0 - 1200),
                  label="x discard gate")
    nt.links.new(gate.outputs[0], _scale_socket(nt, disc))

    em = nt.nodes.new("ShaderNodeEmission")
    em.location = (x0 + 3560, y0 - 1200)
    em.label = "o0.rgb (scene-linear radiance)"
    em.inputs["Strength"].default_value = 1.0
    nt.links.new(disc.outputs["Vector"], em.inputs["Color"])

    note = "surface left OPAQUE (fx_card_additive=0) — the defect, on purpose"
    if additive:
        tr = nt.nodes.new("ShaderNodeBsdfTransparent")
        tr.location = (out.location.x - 400, out.location.y - 260)
        addn = nt.nodes.new("ShaderNodeAddShader")
        addn.location = (out.location.x - 220, out.location.y - 120)
        addn.label = "eBlendLinearDodge: dst = dst + src (never occludes)"
        nt.links.new(em.outputs[0], addn.inputs[0])
        nt.links.new(tr.outputs[0], addn.inputs[1])
        for l in list(out.inputs["Surface"].links):
            nt.links.remove(l)
        nt.links.new(addn.outputs[0], out.inputs["Surface"])
        note = ("ADDITIVE (Emission + Transparent); the importer's %s surface is "
                "DISCONNECTED — the shipped shader reads no plate rgb at all"
                % (surf.node.type if surf is not None else "previous"))

    mat["le_vista_shader"] = (
        "fx card: o0.rgb = pow(vcol,2.2) x %s (NO texture); "
        "o0.a = pow(sat(aS x %g),2.2) x pow(sat(aD x %g + %g),2.2) x vcol.a^2, "
        "discard <= %g; warp %g on uv0; k_time0_x=%.6g (per-FRAME, UNFITTED); "
        "src factor=%s; %s"
        % (tuple(round(v, 6) for v in row["emissive_tint"]),
           VSH.FX_CARD_STEAM_SCALE, VSH.FX_CARD_DUST_SCALE, VSH.FX_CARD_DUST_BIAS,
           VSH.FX_CARD_ALPHA_DISCARD, VSH.FX_CARD_WARP, time,
           "SRC_ALPHA (inferred)" if src_alpha else "ONE (the flat-quad reading)",
           note))
    mat["le_vista_shader_source"] = row["shader"]
    return True, mat["le_vista_shader"]


def apply_saturn_detail(mat, spec, pkg_dir, names, color1=(1.0, 1.0, 1.0),
                        time=0.0):
    """★★ SATURN'S THREE DETAIL PLATES AND THE FLOW-WARPED UV CHAINS.

    `shader-confirmed`, off `6f67762bf83d59fd`'s pixel shader.  This is the
    banding: without it the disc is one 4096² plate at an unwarped `uv0` and the
    three plates the artists layered on it contribute nothing.

        t   = k_time0_x                                    // cb0[0].y
        fX  = t5.Sample(base + t*flow_scroll).rg           // 4 taps
        uvL = (fX*2-1)*warp + base*tile + tile_offset + t*uv_scroll

        albedo = plate  * 0.434154
               + spots  * (0.671031,0.610880,0.306828) * 0.3 * BLEND.x
               + wind   * (0.389517,0.340856,0.061280) * 1.0 * BLEND.y
               + clouds * (0.153506,0.140215,0.051804) * 0.6 * BLEND.z

    ★ `0.11` warps the **base plate**, not a detail layer — the quartet
    `0.11 / 0.23469 / 0.3912 / 0.75` is *plate / spots / wind / clouds*.  So the
    plate node's own UV is re-pointed too, and the rim's `A'` (which reads the
    same register `r6`) follows it for free.
    ★ The clouds layer alone is on **TEXCOORD1** (our `uv1`); everything else is
    TEXCOORD0.  `measured`: obj030's `uv1.u` spans [−2.169, 3.753], i.e. ~6
    tiles, so putting clouds on `uv0` would have been visibly wrong.
    ⚠ `k_time0_x` is per-FRAME and in no level resource — `saturn_time=`
    defaults to **0.0** (a still) and is NOT fitted.  At `t = 0` every scroll
    term vanishes and the three `uv0` taps collapse onto ONE flowmap fetch.
    """
    if str(mat.get("le_shaderset", "")) != VSH.SATURN_SHADERSET:
        return False, "not the Saturn shaderset"
    nt = mat.node_tree
    rt = spec.get("role_textures") or {}
    plate = _image_node_for(nt, rt.get(VSH.SATURN_LAYER_ROLE["plate"], ""))
    if plate is None:
        return False, "no image node for layer0_albedo_map"
    # The scale `apply_saturn_terms` already inserted; the detail sum has to
    # enter the accumulator in the SAME units as the plate does, so it is scaled
    # by `scale / 0.434154` — i.e. the shader's own relative weights, whatever
    # absolute calibration the plate path happens to carry.
    scale, why = VSH.albedo_correction(
        str(mat.get("le_shaderset", "")),
        spec.get("base_color_factor", (1.0, 1.0, 1.0)), color1)
    if scale is None:
        return False, why
    acc = next((n for n in nt.nodes if n.type == "VECT_MATH"
                and str(n.label).startswith("shader-confirmed albedo")), None)
    if acc is None:
        return False, ("no `shader-confirmed albedo` node — apply_saturn_terms "
                       "must run first")

    flow_hash = rt.get(VSH.SATURN_FLOWMAP_TEX_ROLE, "")
    x0, y0 = plate.location.x - 2400, plate.location.y + 900

    uvn = {0: _uv_node(nt, "uv0", (x0, y0)),
           1: _uv_node(nt, "uv1", (x0, y0 - 220))}
    dxn = {k: _dx_uv(nt, v.outputs["UV"], (x0 + 200, y0 - 220 * k),
                     "uv%d -> DX (v = 1 - v)" % k)
           for k, v in uvn.items()}

    # --- the four flow taps, collapsed by (uv set, time offset) --------------
    taps, tap_nodes = {}, 0
    for i, layer in enumerate(("plate", "spots", "wind", "clouds")):
        ch = VSH.SATURN_UV_CHAINS[layer]
        off = tuple(round(c * float(time), 9) for c in ch["flow_scroll"])
        key = (ch["uv"], off)
        if key in taps:
            continue
        src = dxn[ch["uv"]].outputs["Vector"]
        if any(off):
            src = _vmath(nt, "ADD", src, (off[0], off[1], 0.0),
                         loc=(x0 + 400, y0 - 260 * tap_nodes),
                         label="+ t*flow_scroll %s" % (off,)).outputs["Vector"]
        back = _dx_uv(nt, src, (x0 + 580, y0 - 260 * tap_nodes), "DX -> uv")
        img = _tex_node(nt, pkg_dir, flow_hash,
                        "flow tap %s @ uv%d %s" % (layer, ch["uv"], off),
                        (x0 + 760, y0 - 260 * tap_nodes), colorspace="Non-Color")
        if img is None:
            return False, f"flowmap {flow_hash!r} is not on disk"
        nt.links.new(back.outputs["Vector"], img.inputs["Vector"])
        dec = _vmath(nt, "MULTIPLY_ADD", img.outputs["Color"], (2.0, 2.0, 0.0),
                     (-1.0, -1.0, 0.0), loc=(x0 + 1020, y0 - 260 * tap_nodes),
                     label="flow*2-1")
        taps[key] = dec.outputs["Vector"]
        tap_nodes += 1

    def layer_uv(layer, row):
        ch = VSH.SATURN_UV_CHAINS[layer]
        off = tuple(round(c * float(time), 9) for c in ch["flow_scroll"])
        tiled = _vmath(nt, "MULTIPLY_ADD", dxn[ch["uv"]].outputs["Vector"],
                       (ch["tile"][0], ch["tile"][1], 0.0),
                       (ch["tile_offset"][0], ch["tile_offset"][1], 0.0),
                       loc=(x0 + 1200, y0 - 260 * row),
                       label="base*%s + %s" % (ch["tile"], ch["tile_offset"]))
        warped = _vmath(nt, "MULTIPLY_ADD", taps[(ch["uv"], off)],
                        (ch["warp"], ch["warp"], 0.0), tiled.outputs["Vector"],
                        loc=(x0 + 1380, y0 - 260 * row),
                        label="+ flow*%g" % ch["warp"])
        out = warped.outputs["Vector"]
        sc = tuple(c * float(time) for c in ch["uv_scroll"])
        if any(sc):
            out = _vmath(nt, "ADD", out, (sc[0], sc[1], 0.0),
                         loc=(x0 + 1560, y0 - 260 * row),
                         label="+ t*uv_scroll").outputs["Vector"]
        return _dx_uv(nt, out, (x0 + 1740, y0 - 260 * row),
                      "DX -> uv (%s)" % layer).outputs["Vector"]

    # --- the base plate is itself flow-warped and U-scrolled -----------------
    for l in list(plate.inputs["Vector"].links):
        nt.links.remove(l)
    nt.links.new(layer_uv("plate", 0), plate.inputs["Vector"])

    # --- BLEND = vertex colour set 1, clamped per channel --------------------
    vc = nt.nodes.new("ShaderNodeVertexColor")
    vc.layer_name = VSH.SATURN_BLEND_ATTRIBUTE
    vc.location = (x0 + 1200, y0 - 1100)
    bsep = nt.nodes.new("ShaderNodeSeparateColor")
    bsep.location = (x0 + 1380, y0 - 1100)
    nt.links.new(vc.outputs["Color"], bsep.inputs["Color"])

    total = None
    built = []
    for row, (role, tex_name, tint, weight, chan) in enumerate(VSH.SATURN_DETAIL,
                                                              start=1):
        layer = {v: k for k, v in VSH.SATURN_LAYER_ROLE.items()}[role]
        h = rt.get(role, "")
        img = _tex_node(nt, pkg_dir, h, f"{role} -> {names.get(h, tex_name)}",
                        (x0 + 1920, y0 - 260 * row), colorspace="sRGB")
        if img is None:
            built.append(f"{layer}: {h!r} NOT on disk")
            continue
        nt.links.new(layer_uv(layer, row), img.inputs["Vector"])
        # tint x weight x (scale / plate_coeff) folded into one constant vector:
        # every factor in it is a compile-time literal of the shipped shader.
        k = tuple(tint[c] * weight * scale[c] / VSH.SATURN_PLATE_COEFF
                  for c in range(3))
        tinted = _vmath(nt, "MULTIPLY", img.outputs["Color"], k,
                        loc=(x0 + 2160, y0 - 260 * row),
                        label="x tint %s x %.1f (x scale/0.434154)"
                              % (tuple(round(v, 6) for v in tint), weight))
        gated = _vmath(nt, "SCALE", tinted.outputs["Vector"],
                       loc=(x0 + 2340, y0 - 260 * row),
                       label="x saturate(BLEND.%s)" % "xyz"[chan])
        clampb = _math(nt, "MAXIMUM", bsep.outputs[chan], 0.0,
                       loc=(x0 + 1560, y0 - 1100 - 120 * row),
                       label="saturate(BLEND.%s)" % "xyz"[chan], clamp=True)
        nt.links.new(clampb.outputs[0], _scale_socket(nt, gated))
        if total is None:
            total = gated.outputs["Vector"]
        else:
            total = _vmath(nt, "ADD", total, gated.outputs["Vector"],
                           loc=(x0 + 2520, y0 - 260 * row),
                           label="albedo accumulator").outputs["Vector"]
        built.append(f"{layer} x{tint} x{weight} gated on BLEND.{'xyz'[chan]}")

    if total is None:
        return False, "no detail plate is on disk: " + "; ".join(built)

    consumers = [(l.to_node, l.to_socket) for l in acc.outputs["Vector"].links]
    add = _vmath(nt, "ADD", acc.outputs["Vector"], total,
                 loc=(acc.location.x + 200, acc.location.y - 200),
                 label="+ 3 detail plates (shader-confirmed)")
    for _node, insock in consumers:
        for l in list(insock.links):
            nt.links.remove(l)
        nt.links.new(add.outputs["Vector"], insock)

    mat["le_saturn_detail"] = (
        "k_time0_x=%.6g (per-FRAME, UNFITTED); flow taps=%d; %s"
        % (time, tap_nodes, "; ".join(built)))
    mat["le_saturn_uv_chains"] = json.dumps(VSH.SATURN_UV_CHAINS)
    return True, mat["le_saturn_detail"]


def apply_saturn_rim(mat, spec, color1=(1.0, 1.0, 1.0), gain=1.0, env_path=""):
    """★★ THE BRIGHT LIMB — the term whose absence is the biggest single
    difference between our Saturn and the engine's.

        A'  = (0.016033, 0.018544, 0.079322) * (0.05 + 0.95 * plate)
        F0  = A' * (1 + sum(saturate(BLEND)))
        c   = saturate(N.V);   f5 = (1 - c)^5
        F   = F0 + (1 - F0) * f5                     [Schlick, exponent 5]
        vis = 2.346142 / (c + sqrt(0.317010 c^2 + 0.682990))^2
        rim = F * vis * L_spec * gain

    `A'` is the specular **F0** and the Fresnel mixes it toward WHITE, so the
    disc centre is the blue end and the limb is the neutral bright end.
    Limb/centre runs 25x-480x depending on the plate.

    ⚠ `L_spec` — the incident specular radiance — is an ANISOTROPIC sum over the
    same five SG5 lobes the diffuse uses.  The lobe geometry and its
    `norm*pi*exp(...)` weighting are NOT reproduced: we substitute the isotropic
    SG5 lobe sum the importer already builds (`SG5 sum 0..4`).  Same five lobes,
    same texture, different weighting -> `inferred (structural substitution)`.
    `gain` folds the per-FRAME `k_sgopts.z/.w * k_world_ambient_spec`, which is
    not in the bytes.  ⛔ It defaults to 1.0 and is NOT fitted to art.
    """
    if str(mat.get("le_shaderset", "")) != VSH.SATURN_SHADERSET:
        return False, "not the Saturn shaderset"
    nt = mat.node_tree
    out, surf = _surface_of(mat)
    if surf is None:
        return False, "material output is not driven"
    plate_hash = (spec.get("role_textures") or {}).get("layer0_albedo_map", "")
    plate = _image_node_for(nt, plate_hash)
    if plate is None:
        return False, f"no image node for the plate {plate_hash!r}"
    # ------------------------------------------------------------------
    # WHICH branch supplies L_spec.  The shader crossfades between an
    # SG-lightmap anisotropic specular sum and an ambient-specular CUBE on
    # `wSG`, computed from the material roughness 0.808
    # against `k_sgopts.x/.y` — both per-FRAME, so which branch runs is NOT
    # decidable from the bytes.  Both are offered; neither is a default fit.
    #
    # ⚠ MEASURED, and it is why `sg` alone cannot make a limb: Saturn's own
    # atlas page 13 carries ~3e-4, so at gain 1.0 the SG branch contributes
    # ~0.04 % of the disc — rim-on vs rim-off differ in the 4th decimal.
    # The CUBE branch's source is the reflection probe, which contains the sun
    # at 26-68 luminance and the whole sky, i.e. ~1000x more radiance.
    branch = str(env_path and "cube" or "sg")
    env_note = ""
    if env_path:
        img, env_note = _env_image(env_path, "rim_env")
        if img is None:
            return False, f"rim_env {env_note}"
        env = nt.nodes.new("ShaderNodeTexEnvironment")
        env.image = img
        env.location = (plate.location.x, plate.location.y - 1500)
        env.label = "ambient-spec cube (the engine's OWN probe capture)"
        if hasattr(env, "interpolation"):
            env.interpolation = "Cubic"
        tc = nt.nodes.new("ShaderNodeTexCoord")
        tc.location = (plate.location.x - 250, plate.location.y - 1500)
        nt.links.new(tc.outputs["Reflection"], env.inputs["Vector"])
        lsock = env.outputs["Color"]
    else:
        lspec = next((n for n in nt.nodes
                      if n.type in ("MIX", "MIX_RGB")
                      and str(n.label).startswith("SG5 sum")), None)
        if lspec is None:
            return False, ("no `SG5 sum` node — the lightmap is not wired, so the "
                           "rim has no radiance source (need mesh_lightmap != none)")
        lsock = next((s for s in lspec.outputs if s.type == "RGBA"), lspec.outputs[0])

    x0, y0 = plate.location.x, plate.location.y - 900

    # c = saturate(N . V)
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    geo.location = (x0, y0)
    dot = _vmath(nt, "DOT_PRODUCT", geo.outputs["Normal"], geo.outputs["Incoming"],
                 loc=(x0 + 200, y0), label="N.V")
    c = _math(nt, "MAXIMUM", dot.outputs["Value"], 0.0, loc=(x0 + 380, y0),
              label="c = saturate(N.V)", clamp=True)
    # f5 = (1 - c)^5
    one_c = _math(nt, "SUBTRACT", 1.0, c.outputs[0], loc=(x0 + 380, y0 - 180),
                  label="1 - c")
    f5 = _math(nt, "POWER", one_c.outputs[0], VSH.FRESNEL_EXPONENT,
               loc=(x0 + 560, y0 - 180), label="f5 = (1-c)^5")
    # vis = 2.346142 * Vt(c)^2
    cc = _math(nt, "MULTIPLY", c.outputs[0], c.outputs[0], loc=(x0 + 560, y0))
    inner = _math(nt, "MULTIPLY_ADD", cc.outputs[0], VSH.SATURN_VIS_A,
                  loc=(x0 + 720, y0), label="0.317010 c^2 + 0.682990")
    inner.inputs[2].default_value = VSH.SATURN_VIS_B
    sq = _math(nt, "SQRT", inner.outputs[0], loc=(x0 + 880, y0))
    den = _math(nt, "ADD", sq.outputs[0], c.outputs[0], loc=(x0 + 1040, y0))
    vt = _math(nt, "DIVIDE", 1.0, den.outputs[0], loc=(x0 + 1200, y0), label="Vt(c)")
    vt2 = _math(nt, "POWER", vt.outputs[0], 2.0, loc=(x0 + 1360, y0))
    vis = _math(nt, "MULTIPLY", vt2.outputs[0], VSH.SATURN_VIS_K,
                loc=(x0 + 1520, y0), label="vis = 2.346142 Vt^2")
    # A' and F0
    ap = _vmath(nt, "MULTIPLY_ADD", plate.outputs["Color"],
                (VSH.SATURN_ATMOSPHERE_PLATE_SCALE,) * 3,
                (VSH.SATURN_ATMOSPHERE_PLATE_BIAS,) * 3,
                loc=(x0 + 200, y0 - 360), label="0.05 + 0.95 plate")
    ap2 = _vmath(nt, "MULTIPLY", ap.outputs["Vector"], VSH.SATURN_ATMOSPHERE_TINT,
                 loc=(x0 + 380, y0 - 360), label="A' = tint x (.)")
    blm = VSH.blend_multiplier(color1)
    if branch == "cube":
        # ★ Note the asymmetry, and it is easy to get wrong: the cube branch
        # uses A' WITHOUT the blend multiplier inside its Fresnel and applies
        # the multiplier afterwards, and its Fresnel is CAPPED at 0.383792
        # rather than mixed to white.  There is no `vis` term on this branch.
        cap = _math(nt, "MULTIPLY", f5.outputs[0], VSH.SATURN_CUBE_FRESNEL_CAP,
                    loc=(x0 + 560, y0 - 240), label="0.383792 f5")
        fnode, fsock = _mix_rgb(nt, cap.outputs[0], ap2.outputs["Vector"],
                                (1.0, 1.0, 1.0), loc=(x0 + 760, y0 - 360),
                                label="Famb = A' + 0.383792(1-A')f5")
        rim1 = _vmath(nt, "MULTIPLY", fsock, lsock, loc=(x0 + 960, y0 - 360),
                      label="Famb x cube(R)")
        rim2 = _vmath(nt, "SCALE", rim1.outputs["Vector"],
                      loc=(x0 + 1140, y0 - 360), label="x blm (%.4f)" % blm)
        _scale_socket(nt, rim2).default_value = blm
    else:
        f0 = _vmath(nt, "SCALE", ap2.outputs["Vector"], loc=(x0 + 560, y0 - 360),
                    label="F0 = A' x blm (%.4f)" % blm)
        _scale_socket(nt, f0).default_value = blm
        # F = mix(F0, white, f5)  ==  F0 + (1-F0) f5
        fnode, fsock = _mix_rgb(nt, f5.outputs[0], f0.outputs["Vector"],
                                (1.0, 1.0, 1.0), loc=(x0 + 760, y0 - 360),
                                label="Schlick F")
        rim1 = _vmath(nt, "MULTIPLY", fsock, lsock, loc=(x0 + 960, y0 - 360),
                      label="F x L_spec (SG5 sum, structural substitution)")
        rim2 = _vmath(nt, "SCALE", rim1.outputs["Vector"],
                      loc=(x0 + 1140, y0 - 360), label="x vis")
        nt.links.new(vis.outputs[0], _scale_socket(nt, rim2))
    rim3 = _vmath(nt, "SCALE", rim2.outputs["Vector"], loc=(x0 + 1320, y0 - 360),
                  label="x rim_gain (%.4g, UNFITTED)" % gain)
    _scale_socket(nt, rim3).default_value = gain

    em = nt.nodes.new("ShaderNodeEmission")
    em.location = (x0 + 1520, y0 - 360)
    em.label = "Saturn atmospheric rim"
    em.inputs["Strength"].default_value = 1.0
    nt.links.new(rim3.outputs["Vector"], em.inputs["Color"])
    add = nt.nodes.new("ShaderNodeAddShader")
    add.location = (out.location.x - 200, out.location.y - 200)
    nt.links.new(surf, add.inputs[0])
    nt.links.new(em.outputs[0], add.inputs[1])
    for l in list(out.inputs["Surface"].links):
        nt.links.remove(l)
    nt.links.new(add.outputs[0], out.inputs["Surface"])

    mat["le_saturn_rim"] = (
        "branch=%s; A' = %s x (0.05 + 0.95 plate); blm %.4f; Schlick^5 on N.V; "
        "L_spec = %s; gain %.4g UNFITTED"
        % (branch, VSH.SATURN_ATMOSPHERE_TINT, blm,
           ("probe cube along R [%s]" % env_note if branch == "cube"
            else "SG5 sum (structural substitution)"), gain))
    return True, mat["le_saturn_rim"]


def apply_ring_terms(mat, spec, pkg_dir, names, spec_on=True, env_path="",
                     ambient_spec=1.0, ao=1.0):
    """The ring sheet, from `ba863c7b2cb61616`'s own literals.

    ⛔ Four things the importer had wrong, all `shader-confirmed`:

      1. `base_color_factor` never reaches the GPU (no material cbuffer).  The
         diffuse albedo is `plate*S*(1 - 0.971 M) + 0.000589 M`, all times
         `diffGlobal = 1 - 0.56 (1 - 0.081 M)`.
      2. The "inverted mix" of the earlier note is the specular **F0**, not the
         albedo — the two registers were read the other way round.
      3. Alpha is `pow(plate.a, 2.2)`, not the raw alpha: ours was 3.90x too
         OPAQUE at the median, which is why too little sky came through.
      4. `layer2_normal_map` (`vst_saturn_rings_dtl_nml`, at uv1 x (1, 40)) is
         the ONLY normal that shades — the wired `layer0_normal_map` contributes
         nothing, because the lerp weight `color1.G` is 1.0 on every vertex.

    `M = saturate(msk.R * 0.419 + 0.890) * saturate(color1.R)`; `measured`, the
    mask's median is 0 so `M` sits at 0.890 over most of the sheet and reaches
    1.0 on 13.9 % of it, where the material goes nearly black.  That is shipped
    behaviour.
    """
    if str(mat.get("le_shaderset", "")) != VSH.RING_SHADERSET:
        return False, "not the ring shaderset"
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return False, "no Principled BSDF"
    rt = spec.get("role_textures") or {}
    plate = _image_node_for(nt, rt.get("layer0_albedo_map", ""))
    if plate is None:
        return False, "no image node for layer0_albedo_map"
    x0, y0 = plate.location.x, plate.location.y - 700

    # ★ THE UV SETS, read off the shipped VS+PS rather than assumed.  The VS
    # packs TWO texcoords into one interpolator — `mov o3.xy, v2.xyxx` (TEXCOORD
    # 0) and `mov o3.zw, v3.xxxy` (TEXCOORD 1) — and the PS then samples the
    # PLATE and both normals at `v3.zw`, i.e. **TEXCOORD 1**, while only the
    # blend mask uses `v3.xy` (TEXCOORD 0).  The importer wires the plate at the
    # primary UV, which is the wrong set and puts the wrong banding on the sheet.
    uv0 = _uv_node(nt, "uv0", (x0 - 400, y0))
    uv1 = _uv_node(nt, "uv1", (x0 - 400, y0 - 700))
    for l in list(plate.inputs["Vector"].links):
        nt.links.remove(l)
    nt.links.new(uv1.outputs["UV"], plate.inputs["Vector"])
    uvsrc = uv1.outputs["UV"]

    # --- M -----------------------------------------------------------------
    msk = _tex_node(nt, pkg_dir, rt.get("layer1_blend_mask", ""),
                    "layer1_blend_mask (R) @ uv0", (x0, y0), colorspace="Non-Color")
    vc = nt.nodes.new("ShaderNodeVertexColor")
    vc.layer_name = "color1"
    vc.location = (x0, y0 - 260)
    sep = nt.nodes.new("ShaderNodeSeparateColor")
    sep.location = (x0 + 180, y0 - 260)
    nt.links.new(vc.outputs["Color"], sep.inputs["Color"])
    if msk is not None:
        nt.links.new(uv0.outputs["UV"], msk.inputs["Vector"])
        msep = nt.nodes.new("ShaderNodeSeparateColor")
        msep.location = (x0 + 180, y0)
        nt.links.new(msk.outputs["Color"], msep.inputs["Color"])
        mnode = _math(nt, "MULTIPLY_ADD", msep.outputs[0], VSH.RING_MASK_SCALE,
                      loc=(x0 + 360, y0), label="saturate(msk.R*0.419+0.890)",
                      clamp=True)
        mnode.inputs[2].default_value = VSH.RING_MASK_BIAS
        m_src = mnode.outputs[0]
    else:
        mnode = _math(nt, "ADD", VSH.RING_MASK_BIAS, 0.0, loc=(x0 + 360, y0),
                      label="msk absent -> M = 0.890 (the measured median)")
        m_src = mnode.outputs[0]
    M = _math(nt, "MULTIPLY", m_src, sep.outputs[0], loc=(x0 + 540, y0),
              label="M = m * saturate(color1.R)", clamp=True)

    # --- diffuse albedo ------------------------------------------------------
    k971 = _math(nt, "MULTIPLY_ADD", M.outputs[0], -VSH.RING_ALBEDO_M_SLOPE,
                 loc=(x0 + 720, y0 - 140), label="1 - 0.971 M")
    k971.inputs[2].default_value = 1.0
    tinted = _vmath(nt, "MULTIPLY", plate.outputs["Color"], VSH.RING_ALBEDO_TINT,
                    loc=(x0 + 720, y0 - 320), label="plate x S")
    scaled = _vmath(nt, "SCALE", tinted.outputs["Vector"], loc=(x0 + 900, y0 - 320),
                    label="x (1 - 0.971 M)")
    nt.links.new(k971.outputs[0], _scale_socket(nt, scaled))
    pre = _math(nt, "MULTIPLY", M.outputs[0], VSH.RING_PREARRIVAL[0],
                loc=(x0 + 900, y0 - 140), label="0.000589 M")
    added = _vmath(nt, "ADD", scaled.outputs["Vector"], (0.0, 0.0, 0.0),
                   loc=(x0 + 1080, y0 - 320), label="+ 0.000589 M")
    combine = nt.nodes.new("ShaderNodeCombineColor")
    combine.location = (x0 + 1080, y0 - 140)
    for i in range(3):
        nt.links.new(pre.outputs[0], combine.inputs[i])
    nt.links.new(combine.outputs["Color"], added.inputs[1])
    dg = _math(nt, "MULTIPLY_ADD", M.outputs[0],
               VSH.RING_DIFF_GLOBAL_A * VSH.RING_DIFF_GLOBAL_B,
               loc=(x0 + 1080, y0 + 40), label="diffGlobal = 1 - 0.56(1 - 0.081 M)")
    dg.inputs[2].default_value = 1.0 - VSH.RING_DIFF_GLOBAL_A
    base = _vmath(nt, "SCALE", added.outputs["Vector"], loc=(x0 + 1260, y0 - 320),
                  label="x diffGlobal")
    nt.links.new(dg.outputs[0], _scale_socket(nt, base))

    for l in list(bsdf.inputs["Base Color"].links):
        nt.links.remove(l)
    nt.links.new(base.outputs["Vector"], bsdf.inputs["Base Color"])

    # --- specular F0 / roughness --------------------------------------------
    invM = _math(nt, "SUBTRACT", 1.0, M.outputs[0], loc=(x0 + 720, y0 + 200),
                 label="1 - M")
    f0s = _math(nt, "MULTIPLY_ADD", invM.outputs[0], VSH.RING_F0_SCALAR,
                loc=(x0 + 900, y0 + 200), label="F0scalar")
    f0s.inputs[2].default_value = 0.0
    f0m = _math(nt, "MULTIPLY_ADD", M.outputs[0], VSH.RING_PREARRIVAL[1],
                loc=(x0 + 900, y0 + 340), label="+ 0.010 M")
    f0sum = _math(nt, "ADD", f0s.outputs[0], f0m.outputs[0], loc=(x0 + 1080, y0 + 260))
    spec_lvl = _math(nt, "DIVIDE", f0sum.outputs[0], 0.08,
                     loc=(x0 + 1260, y0 + 260), label="Specular IOR Level = F0/0.08")
    for name in ("Specular IOR Level", "Specular"):
        if name in bsdf.inputs:
            for l in list(bsdf.inputs[name].links):
                nt.links.remove(l)
            nt.links.new(spec_lvl.outputs[0], bsdf.inputs[name])
            break
    # `F0col = ((0.197137,0.298266,0.487311) + plate*(0.050405,0.068113,0.096851))
    #  * (1 - M)` — the "inverted mix".  It is a COLOUR, so it belongs on
    # Specular Tint; leaving it white (the default) made the sheet's specular
    # both untinted and, at M = 0.890, far too strong.
    f0c = _vmath(nt, "MULTIPLY_ADD", plate.outputs["Color"], VSH.RING_F0_PLATE,
                 VSH.RING_F0_CONST, loc=(x0 + 900, y0 + 460),
                 label="F0col = const + plate x k")
    f0c2 = _vmath(nt, "SCALE", f0c.outputs["Vector"], loc=(x0 + 1080, y0 + 460),
                  label="x (1 - M)")
    nt.links.new(invM.outputs[0], _scale_socket(nt, f0c2))
    if "Specular Tint" in bsdf.inputs:
        for l in list(bsdf.inputs["Specular Tint"].links):
            nt.links.remove(l)
        nt.links.new(f0c2.outputs["Vector"], bsdf.inputs["Specular Tint"])
    if not spec_on:
        # diagnostic only — isolates the diffuse term so "too bright" can be
        # attributed instead of guessed.  Never a default.
        for name in ("Specular IOR Level", "Specular"):
            if name in bsdf.inputs:
                for l in list(bsdf.inputs[name].links):
                    nt.links.remove(l)
                bsdf.inputs[name].default_value = 0.0
                break
    rough = _math(nt, "MULTIPLY_ADD", k971.outputs[0], VSH.RING_ROUGHNESS_BASE,
                  loc=(x0 + 1260, y0 + 100), label="roughness")
    rough_m = _math(nt, "MULTIPLY", M.outputs[0], VSH.RING_PREARRIVAL[2],
                    loc=(x0 + 1080, y0 + 100))
    nt.links.new(rough_m.outputs[0], rough.inputs[2])
    for l in list(bsdf.inputs["Roughness"].links):
        nt.links.remove(l)
    nt.links.new(rough.outputs[0], bsdf.inputs["Roughness"])

    # --- alpha ---------------------------------------------------------------
    ag = _math(nt, "POWER", plate.outputs["Alpha"], VSH.RING_ALPHA_GAMMA,
               loc=(x0 + 720, y0 - 500), label="alpha = pow(plate.a, 2.2)")
    for l in list(bsdf.inputs["Alpha"].links):
        nt.links.remove(l)
    nt.links.new(ag.outputs[0], bsdf.inputs["Alpha"])

    # --- the shading normal is layer2, at uv1 x (1, 40) ----------------------
    nrm_note = ("layer2_normal_map absent (hash %r under %s)"
                % (rt.get("layer2_normal_map", ""), Path(pkg_dir) / "textures"))
    dtl = _tex_node(nt, pkg_dir, rt.get("layer2_normal_map", ""),
                    "layer2_normal_map (THE one that shades)",
                    (x0 + 360, y0 - 700), colorspace="Non-Color")
    if dtl is not None:
        mp = nt.nodes.new("ShaderNodeMapping")
        mp.location = (x0 + 180, y0 - 700)
        mp.label = "TEXCOORD1 x (1, 40)"
        mp.inputs["Scale"].default_value = (1.0, VSH.RING_DETAIL_UV_SCALE, 1.0)
        nt.links.new(uvsrc, mp.inputs["Vector"])
        nt.links.new(mp.outputs["Vector"], dtl.inputs["Vector"])
        nmap = nt.nodes.new("ShaderNodeNormalMap")
        nmap.location = (x0 + 600, y0 - 700)
        nmap.label = "approximation: engine builds this frame from ddx/ddy"
        nt.links.new(dtl.outputs["Color"], nmap.inputs["Color"])
        for l in list(bsdf.inputs["Normal"].links):
            nt.links.remove(l)
        nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        nrm_note = "layer2 @ uv1 x(1,40) (approximation: ddx/ddy frame)"

    # --- ★★ THE RING'S ENTIRE AMBIENT, which we rendered as ZERO --------------
    # `ba863c7b2cb61616` binds NO colour lightmap and never reads
    # `k_world_ambient`.  Its whole ambient is a reflection-probe cube fetched
    # along R:
    #     aR   = sqrt(saturate(rough^2 - 0.010))
    #     lod  = max(0, 10*aR - 1)               (= 3.56 at M=0.890)
    #     envF = saturate(F0col + (1-F0col)*(1-sat(N.V))^5 / (2*(aR+0.001)+1))
    #
    #     ambientSpec = envF * specMask * cube * AO_R
    #     col += ambientSpec * k_world_ambient_spec
    # It goes on the Principled's own EMISSION so the sheet's `pow(plate.a,2.2)`
    # alpha still gates it — an Add Shader would leak it through the 92 %-
    # transparent parts of the sheet, which the engine's blend does not.
    amb_note = "ring_env not given — the sheet keeps ZERO ambient (Q7 open)"
    if env_path:
        img, amb_note = _env_image(env_path, "ring_env")
        if img is None:
            amb_note = "ring_env " + amb_note
        else:
            ex, ey = x0 + 720, y0 - 900
            env = nt.nodes.new("ShaderNodeTexEnvironment")
            env.image = img
            env.location = (ex + 200, ey)
            env.label = "k_ambient_spec_cubemaps along R (probe capture)"
            if hasattr(env, "interpolation"):
                env.interpolation = "Cubic"
            tc = nt.nodes.new("ShaderNodeTexCoord")
            tc.location = (ex, ey)
            nt.links.new(tc.outputs["Reflection"], env.inputs["Vector"])
            geo = nt.nodes.new("ShaderNodeNewGeometry")
            geo.location = (ex, ey - 240)
            nov = _vmath(nt, "DOT_PRODUCT", geo.outputs["Normal"],
                         geo.outputs["Incoming"], loc=(ex + 200, ey - 240),
                         label="N.V")
            c = _math(nt, "MAXIMUM", nov.outputs["Value"], 0.0,
                      loc=(ex + 380, ey - 240), label="saturate(N.V)", clamp=True)
            omc = _math(nt, "SUBTRACT", 1.0, c.outputs[0],
                        loc=(ex + 560, ey - 240), label="1 - N.V")
            p5 = _math(nt, "POWER", omc.outputs[0], VSH.FRESNEL_EXPONENT,
                       loc=(ex + 740, ey - 240), label="(1-N.V)^5")
            r2 = _math(nt, "MULTIPLY", rough.outputs[0], rough.outputs[0],
                       loc=(ex + 380, ey - 420), label="rough^2")
            r2e = _math(nt, "SUBTRACT", r2.outputs[0], VSH.RING_ENV_ROUGH_EPS,
                        loc=(ex + 560, ey - 420), label="- 0.010", clamp=True)
            aR = _math(nt, "SQRT", r2e.outputs[0], loc=(ex + 740, ey - 420),
                       label="aR = sqrt(saturate(rough^2 - 0.010))")
            den = _math(nt, "MULTIPLY_ADD", aR.outputs[0], 2.0,
                        loc=(ex + 920, ey - 420),
                        label="2(aR + 0.001) + 1")
            den.inputs[2].default_value = 2.0 * VSH.RING_ENV_FRESNEL_EPS + 1.0
            fac = _math(nt, "DIVIDE", p5.outputs[0], den.outputs[0],
                        loc=(ex + 1100, ey - 300), label="(1-N.V)^5 / denom")
            fnode, fsock = _mix_rgb(nt, fac.outputs[0], f0c2.outputs["Vector"],
                                    (1.0, 1.0, 1.0), loc=(ex + 1280, ey - 300),
                                    label="envF = F0col + (1-F0col)*f5/denom")
            # specMask = saturate(dot(F0col, 333)) — 1 everywhere except the
            # 13.9 % pre-arrival band, where F0col is driven to zero.
            msep = nt.nodes.new("ShaderNodeSeparateColor")
            msep.location = (ex + 1280, ey - 540)
            nt.links.new(f0c2.outputs["Vector"], msep.inputs["Color"])
            s1 = _math(nt, "ADD", msep.outputs[0], msep.outputs[1],
                       loc=(ex + 1460, ey - 540))
            s2 = _math(nt, "ADD", s1.outputs[0], msep.outputs[2],
                       loc=(ex + 1640, ey - 540))
            smask = _math(nt, "MULTIPLY", s2.outputs[0], VSH.RING_SPEC_MASK_K,
                          loc=(ex + 1820, ey - 540),
                          label="specMask = saturate(dot(F0col, 333))",
                          clamp=True)
            a1 = _vmath(nt, "MULTIPLY", fsock, env.outputs["Color"],
                        loc=(ex + 1460, ey - 300), label="envF x cube(R)")
            a2 = _vmath(nt, "SCALE", a1.outputs["Vector"],
                        loc=(ex + 2000, ey - 300), label="x specMask")
            nt.links.new(smask.outputs[0], _scale_socket(nt, a2))
            a3 = _vmath(nt, "SCALE", a2.outputs["Vector"],
                        loc=(ex + 2180, ey - 300),
                        label="x AO_R %.4g (INFERRED default texel) "
                              "x k_world_ambient_spec %.4g (per-FRAME, UNFITTED)"
                              % (ao, ambient_spec))
            _scale_socket(nt, a3).default_value = float(ao) * float(ambient_spec)
            for nm in ("Emission Color", "Emission"):
                if nm in bsdf.inputs and bsdf.inputs[nm].type == "RGBA":
                    for l in list(bsdf.inputs[nm].links):
                        nt.links.remove(l)
                    nt.links.new(a3.outputs["Vector"], bsdf.inputs[nm])
                    break
            if "Emission Strength" in bsdf.inputs:
                bsdf.inputs["Emission Strength"].default_value = 1.0
            amb_note = ("ambientSpec = envF x specMask x cube(R) x AO_R %.4g "
                        "x k_world_ambient_spec %.4g [%s]; lod the shader asks "
                        "for = %.3f at M=0.890"
                        % (ao, ambient_spec, amb_note,
                           VSH.ring_env_lod(VSH.ring_roughness(0.890))))
            mat["le_ring_ambient"] = amb_note

    mat["le_ring_terms"] = (
        "albedo = (plate x %s x (1-0.971M) + 0.000589M) x diffGlobal; "
        "F0 = 0.6434(1-M)+0.010M; rough = 0.5923(1-0.971M)+0.434241M; "
        "alpha = pow(plate.a, 2.2); normal = %s; ambient: %s"
        % (VSH.RING_ALBEDO_TINT, nrm_note, amb_note))
    mat["le_vista_shader_source"] = VSH.SHADERSET_TERMS[VSH.RING_SHADERSET]["shader"]
    return True, mat["le_ring_terms"]


def apply_moon_terms(mat, spec):
    """`albedo x 0.552011`, ambient global `x 0.719`, and the emissive plate
    ADDED through its own strongly-blue tint `(0.403480, 0.425726, 2.000000)`.

    ★ The moons do NOT use Saturn's wrapped diffuse (0 hits for `0.261651` and
    `l(0.800000)`) and carry NO blue Fresnel rim: they take a hard Lambert
    `saturate(N.L) * 1/pi` with the terminator at `N.L == 0`, so unlike Saturn
    they DO receive real direct light.
    """
    if str(mat.get("le_shaderset", "")) != VSH.MOON_SHADERSET:
        return False, "not the moon shaderset"
    nt = mat.node_tree
    rt = spec.get("role_textures") or {}
    plate = _image_node_for(nt, rt.get("layer0_albedo_map", ""))
    if plate is None:
        return False, "no image node for layer0_albedo_map"
    n = _insert_scale(nt, plate.outputs["Color"],
                      (VSH.MOON_ALBEDO_COEFF * VSH.MOON_AMBIENT_GLOBAL,) * 3,
                      "shader-confirmed moon albedo (x%.6f)"
                      % (VSH.MOON_ALBEDO_COEFF * VSH.MOON_AMBIENT_GLOBAL))
    emi = _image_node_for(nt, rt.get("layer0_emissive_map", ""))
    emi_note = "no emissive plate node"
    if emi is not None:
        k = _insert_scale(nt, emi.outputs["Color"], VSH.MOON_EMISSIVE_TINT,
                          "shader-confirmed moon emissive tint")
        emi_note = f"emissive x {VSH.MOON_EMISSIVE_TINT} onto {k} consumer(s)"
    mat["le_moon_terms"] = ("albedo x %.6f x ambient global %.3f; %s"
                            % (VSH.MOON_ALBEDO_COEFF, VSH.MOON_AMBIENT_GLOBAL,
                               emi_note))
    mat["le_vista_shader_source"] = VSH.SHADERSET_TERMS[VSH.MOON_SHADERSET]["shader"]
    return True, mat["le_moon_terms"]


def apply_scene_fog(mat, f, fog_rgb):
    """★★ The shipped SCENE-FOG epilogue — `lerp(colour, C.rgb*k_fog_color.rgb, f)`.

    `shader-confirmed`, off the tail of `6f67762bf83d59fd`'s pixel shader (and
    verbatim in the ring sheet and the sun card).  It is the LAST thing those
    shaders do, and it is the whole of the residual-brightness anomaly:
    `measured`, Saturn's disc reaches the engine's own reflection probe at
    **0.150** of the radiance the unfogged terms compute, flat over nine
    directions spanning 3.4x.

    ⛔ EVERY VALUE IS PER-FRAME.  `k_fog_depth` / `k_fog_color` /
    `k_fog_low_color` / `k_fog_hi_color` live in `SGPerFrameConstants` and
    `k_fog_ramp` is an engine-bound texture array, so this function takes `f` and
    the fog colour as explicit free parameters and the harness default is `f = 0`
    — i.e. OFF, and nothing changes unless a caller says so and says why.

    ⚠ It is implemented as `MixShader(surface, Emission(fog))`, which is exact
    for an OPAQUE consumer (Saturn) and NOT exact for a blended one: the shipped
    shader fogs `o0.rgb` and leaves `o0.a` alone, whereas a Mix Shader also mixes
    the surface's transparency, so the sky behind the ring sheet would come
    through attenuated by `(1 - f)`.  That is why `fog_shadersets` defaults to
    Saturn alone.
    """
    if f <= 0.0:
        return False, ("fog=0 — the epilogue's FORM is shader-confirmed and every "
                       "one of its VALUES is per-FRAME, so the default draws none "
                       "of it")
    nt = mat.node_tree
    out, surf = _surface_of(mat)
    if out is None or surf is None:
        return False, "nothing drives Surface"
    if mat.get("le_scene_fog"):
        return False, "already fogged"
    em = nt.nodes.new("ShaderNodeEmission")
    em.location = (out.location.x - 200, out.location.y - 260)
    em.label = "k_fog_low/hi_color x k_fog_color (per-FRAME)"
    em.inputs["Color"].default_value = (fog_rgb[0], fog_rgb[1], fog_rgb[2], 1.0)
    em.inputs["Strength"].default_value = 1.0
    mix = nt.nodes.new("ShaderNodeMixShader")
    mix.location = (out.location.x - 200, out.location.y - 60)
    mix.label = "scene fog: lerp(colour, fog, f=%.4g)" % f
    mix.inputs["Fac"].default_value = float(f)
    nt.links.new(surf, mix.inputs[1])
    nt.links.new(em.outputs[0], mix.inputs[2])
    for l in list(out.inputs["Surface"].links):
        nt.links.remove(l)
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    mat["le_scene_fog"] = ("lerp(colour, %s, %.4g) — ps 761-777, per-FRAME "
                           "constants, NOT decoded" % (tuple(fog_rgb), f))
    return True, mat["le_scene_fog"]


def apply_auto_lightmap_mode():
    """★★ Q3 — `mesh_lightmap=auto`.  ONE flag cannot serve 39 objects, and the
    right answer is a property of the SHADERSET, not of how bright the object's
    atlas page happens to be.  Three answers, all `shader-confirmed`:

      `baked`    the shader's ONLY light is the SG5 colour lightmap.  Saturn:
                 its wrapped diffuse `saturate((N.L+0.25)*0.8)^2` is identically
                 zero at `N.L = -0.595`, so the ambient bake is literally all the
                 light the engine gives it.  Not a lesser evil — exactly right.
      `ambient`  the shader ADDS a live directional to that sum.  Proven on the
                 debris rock (`44538616b0138eb3`): `add r0.xzw, r0.xxzw, r13.xxyz`
                 is unconditional and `k_dirlight_occlusion_map` scales only the
                 LIVE light, so there is NO double-count.  The moons are the same
                 family and take a hard Lambert, so they DO get direct light.
      `neither`  the shader binds no colour lightmap at all (the rings bind
                 `k_ambient_lightmap_ao0/ao1` only) — their 0xFFFFFFFF page
                 sentinel is CORRECT and `baked` can never serve them.

    Implementation: the caller imports at `ambient` (BSDF live + bake added) and
    this pass zeroes the BSDF response on exactly the `baked` shadersets, which
    is what `baked` means.  `neither` needs no action here — those materials have
    no lightmap to add.
    """
    try:
        from lone_echo_import import lightmap_builder as LB
    except ImportError:
        return ["mesh_lightmap=auto: lightmap_builder not importable"]
    out = []
    for mat in bpy.data.materials:
        ss = str(mat.get("le_shaderset", ""))
        want = VSH.LIGHTMAP_MODE_BY_SHADERSET.get(ss)
        if want != "baked":
            continue
        bsdf = next((n for n in mat.node_tree.nodes
                     if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        z = LB._zero_bsdf_response(mat.node_tree, bsdf)
        mat["le_lightmap_mode_auto"] = "baked (shaderset %s)" % ss
        out.append("%s -> baked (BSDF zeroed: %s)" % (mat.name, ", ".join(z)))
    modes = sorted({v for v in VSH.LIGHTMAP_MODE_BY_SHADERSET.values()})
    return out or ["mesh_lightmap=auto: no `baked` shaderset in this import "
                   "(table covers %s)" % modes]


def select_dirlight(scene, lights_path, mode):
    """Choose WHICH of the level's directional lights the vista is lit by.

    ⚠ This is a real open question, not a convenience.  The ring shader
    evaluates exactly ONE directional light — `t5[cb6[0].w & ~0x80]`, where
    `cb6[0].w` is `k_dirlight_mask`, set per DRAW by the CPU — and the DXBC
    cannot say which index lands there.  `min_itc_master` ships two:

        index 0  `64af852c97831c1d`  eEnableDiffuse only,          peak 80
        index 3  `69e7416e388ce8ef`  ePrimaryDirLight + eBakeIndirect + specular,
                                                                    peak 10

    They differ by 8x in strength, so the choice is worth 8x on every unbaked
    vista surface.  `strongest` keeps them both (today's behaviour); `primary`
    keeps only the one the engine itself flags `ePrimaryDirLight`.
    """
    if mode not in ("primary", "strongest", "all"):
        raise SystemExit(f"dirlight={mode!r} must be primary|strongest|all")
    if mode != "primary" or not lights_path:
        return [f"dirlight={mode}: every shipped directional kept"]
    try:
        doc = json.loads(Path(lights_path).read_text())
    except (OSError, ValueError) as exc:
        return [f"dirlight=primary: could not read {lights_path} ({exc})"]
    primary = set()
    other = set()
    for sc in doc.get("scenes", []):
        for rec in sc.get("lights") or []:
            if rec.get("type") != "eDirectionalLight":
                continue
            (primary if "ePrimaryDirLight" in (rec.get("options") or [])
             else other).add(str(rec.get("name", "")))
    if not primary:
        return ["dirlight=primary: no record carries ePrimaryDirLight — kept all"]
    dropped = []
    for ob in list(scene.objects):
        if ob.type != "LIGHT" or ob.data.type != "SUN":
            continue
        if any(h and h in ob.name for h in other) and \
           not any(h and h in ob.name for h in primary):
            dropped.append((ob.name, getattr(ob.data, "energy", 0.0)))
            bpy.data.objects.remove(ob, do_unlink=True)
    kept = [(o.name, o.data.energy) for o in scene.objects
            if o.type == "LIGHT" and o.data.type == "SUN"]
    return ["dirlight=primary ★ kept %s; dropped %s (the non-primary "
            "directional(s)) — which index k_dirlight_mask selects is NOT in the "
            "DXBC, so this is a NAMED hypothesis, testable against the probe"
            % (kept, dropped)]


def apply_ring_backface_gate(scene):
    """⛔ THE 255x, AND IT IS A RENDERER DIFFERENCE, NOT A MATERIAL ONE.

    `ba863c7b2cb61616`'s ps has **no `SV_IsFrontFace` and never flips the shading
    normal**, and its diffuse is a plain `saturate(N.L)`.  So a ring face whose
    own normal points away from the key light receives EXACTLY ZERO direct light,
    from any viewpoint.  Cycles flips the shading normal toward the ray on a
    backface hit, so it lights that same face fully — which is what took the
    anti-sun patch to 255x the engine and made the DECODED 80 W/m^2 rig
    unusable on the whole scene.

    The fix is one multiplicative gate `step(N_true . L)` on the albedo and the
    specular level, evaluated with the GEOMETRIC normal so Cycles' flip cannot
    reach it.  With the gate, all four cases land on the engine's answer:
    front/lit -> Cycles' own `N.L`; back/lit -> Cycles' `saturate(-N.L) == 0`;
    either side of an unlit face -> the gate is 0.

    ⚠ `inferred` in one respect: the shader evaluates exactly ONE directional
    light, chosen per draw by `k_dirlight_mask` on the CPU, and the DXBC cannot
    say which of the level's two it is.  They sit 6.6 deg and 16.5 deg from the
    sun card, so the hemisphere they define differs only within ~16 deg of the
    terminator.  The strongest SUN lamp in the scene is used and named in the log.
    """
    suns = [o for o in scene.objects
            if o.type == "LIGHT" and o.data.type == "SUN"]
    if not suns:
        return ["ring backface gate: no SUN lamp in the scene — not applied"]
    key = max(suns, key=lambda o: getattr(o.data, "energy", 0.0))
    L = key.matrix_world.to_quaternion() @ Vector((0.0, 0.0, 1.0))
    out = []
    for mat in bpy.data.materials:
        if str(mat.get("le_shaderset", "")) != VSH.RING_SHADERSET:
            continue
        nt = mat.node_tree
        bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf is None:
            continue
        x0 = bsdf.location.x - 700
        y0 = bsdf.location.y - 900
        geo = nt.nodes.new("ShaderNodeNewGeometry")
        geo.location = (x0, y0)
        dot = _vmath(nt, "DOT_PRODUCT", geo.outputs["True Normal"],
                     (L.x, L.y, L.z), loc=(x0 + 200, y0),
                     label="N_true . L (%.3f, %.3f, %.3f)" % (L.x, L.y, L.z))
        gate = _math(nt, "GREATER_THAN", dot.outputs["Value"], 0.0,
                     loc=(x0 + 380, y0),
                     label="step(N.L) — the engine never flips the normal")
        n = 0
        for name in ("Base Color", "Specular IOR Level", "Specular"):
            if name not in bsdf.inputs:
                continue
            src = next((l.from_socket for l in bsdf.inputs[name].links), None)
            if src is None:
                continue
            if name == "Base Color":
                g = _vmath(nt, "SCALE", src, loc=(x0 + 560, y0 - 200),
                           label="x step(N.L)")
                nt.links.new(gate.outputs[0], _scale_socket(nt, g))
                sock = g.outputs["Vector"]
            else:
                g = _math(nt, "MULTIPLY", src, gate.outputs[0],
                          loc=(x0 + 560, y0 - 380), label="x step(N.L)")
                sock = g.outputs[0]
            for l in list(bsdf.inputs[name].links):
                nt.links.remove(l)
            nt.links.new(sock, bsdf.inputs[name])
            n += 1
        mat["le_ring_backface_gate"] = (
            "step(N_true . (%.4f, %.4f, %.4f)) on %d input(s); key lamp %r"
            % (L.x, L.y, L.z, n, key.name))
        out.append("%s: ring backface gate on %d input(s), L=(%.3f, %.3f, %.3f) "
                   "from lamp %r" % (mat.name, n, L.x, L.y, L.z, key.name))
    return out or ["ring backface gate: no ring material in this import"]


#: The shadersets whose terms this tree already had before 2026-08-06.  `baseline=1`
#: restricts the override pass to these so a before/after pair is ONE variable.
BASELINE_SHADERSETS = (VSH.SATURN_SHADERSET, VSH.SUN_CARD_SHADERSET)

#: The option values `baseline=1` forces, i.e. the state this session started from.
BASELINE_OPTS = {
    "skydome": "composite",
    "mesh_lightmap": "ambient",
    "scatter_lightmap": "baked",
    "sun": "card",
    "dirlight": "strongest",
    "saturn_rim": "0",
    "saturn_detail": "0",
    "haze_additive": "0",
    "fx_card_additive": "0",
    "colourless_surface": "flat",
    "ring_backface_gate": "0",
    "rim_env": "",
    "ring_env": "",
}


def apply_vista_shader_terms(manifest, pkg_dir, names, mesh_color1, opts=None):
    """Run every shader-confirmed override the disassembled table covers."""
    opts = opts or {}
    baseline = opt_b(opts, "baseline", False)
    # ★ ONE per-frame constant, TWO consumers.  `k_world_ambient_spec` (cb0[2],
    # `SGPerFrameConstants` +32) multiplies Saturn's ambient-spec CUBE branch in
    # `6f67762bf83d59fd` and the ring sheet's entire ambient in
    # `ba863c7b2cb61616`.  On the cube branch it is the ONLY per-frame
    # factor, so `rim_gain` there IS `k_world_ambient_spec` — which is why they
    # share a default here instead of being tuned independently.  ⛔ Still not in
    # any level resource; the default is 1.0 and any other value must be stated.
    was = opt_f(opts, "world_ambient_spec", 1.0)
    if was != 1.0:
        say("world_ambient_spec",
            "k_world_ambient_spec=%.4g — a per-FRAME engine constant in NO level "
            "resource. NOT a decoded value and NOT fitted to the reference art; "
            "state where it came from. It scales Saturn's cube branch and the "
            "ring sheet's whole ambient TOGETHER, because the shipped shaders "
            "read the same cb0[2]." % was)
    # ★★ The scene-fog epilogue.  `fog=` is the lerp factor `f`, `fog_color=`
    # the already-multiplied `C.rgb * k_fog_color.rgb`.  Both are per-FRAME and
    # the default is OFF; `fog_shadersets=saturn|fogged` picks the consumers —
    # `saturn` because a Mix Shader is exact only for an opaque surface.
    fog_f = opt_f(opts, "fog", 0.0)
    fog_rgb = opt_vec(opts, "fog_color") or (0.0, 0.0, 0.0)
    fog_which = (opts.get("fog_shadersets", "saturn") or "saturn").strip().lower()
    fog_set = (VSH.SHADERSET_BINDS_FOG if fog_which == "fogged"
               else frozenset({VSH.SATURN_SHADERSET}))
    if fog_f > 0.0:
        say("fog", "f=%.4g colour=%s on %s — the epilogue in "
                   "`6f67762bf83d59fd` is shader-confirmed; "
                   "k_fog_depth/color/low/hi are "
                   "SGPerFrameConstants and k_fog_ramp is an engine texture, so "
                   "BOTH of these are free parameters and neither is decoded. "
                   "The probe bounds the product k_world_ambient*(1-f) at "
                   "%.4f on Saturn's sunward disc."
            % (fog_f, tuple(fog_rgb), sorted(fog_set),
               VSH.SATURN_PROBE_UNFOGGED_RESIDUAL))
    specs = {m["key"]: m for m in manifest.get("materials", [])}
    done = []
    for mat in bpy.data.materials:
        key = f"{mat.get('le_shaderset', '')}__{mat.get('le_material', '')}"
        spec = specs.get(key)
        if spec is None:
            continue
        ss = str(mat.get("le_shaderset", ""))
        if baseline and ss not in BASELINE_SHADERSETS:
            continue
        if ss == VSH.SATURN_SHADERSET:
            ok, why = apply_saturn_terms(mat, spec,
                                         mesh_color1.get("obj030", (1.0, 1.0, 1.0)))
            done.append((key, ok, why))
            if opt_b(opts, "saturn_detail", True):
                ok, why = apply_saturn_detail(
                    mat, spec, pkg_dir, names,
                    mesh_color1.get("obj030", (1.0, 1.0, 1.0)),
                    opt_f(opts, "saturn_time", 0.0))
                done.append((key + " [detail]", ok, why))
            if opt_b(opts, "saturn_rim", True):
                ok, why = apply_saturn_rim(
                    mat, spec, mesh_color1.get("obj030", (1.0, 1.0, 1.0)),
                    opt_f(opts, "rim_gain", was), opts.get("rim_env", ""))
                done.append((key + " [rim]", ok, why))
            if ss in fog_set:
                ok, why = apply_scene_fog(mat, fog_f, fog_rgb)
                if ok:
                    done.append((key + " [fog]", ok, why))
            continue
        if ss == VSH.SUN_CARD_SHADERSET:
            ok, why = apply_sun_card_terms(mat, spec, pkg_dir, names)
        elif ss == VSH.SKYDOME_SHADERSET:
            ok, why = apply_skydome_terms(mat, spec, pkg_dir, names)
        elif ss == VSH.HAZE_SHADERSET:
            ok, why = apply_haze_terms(mat, spec,
                                       opt_b(opts, "haze_additive", True))
        elif ss == VSH.FX_CARD_SHADERSET:
            ok, why = apply_fx_card_terms(
                mat, spec, pkg_dir, names,
                opt_f(opts, "fx_card_time", opt_f(opts, "saturn_time", 0.0)),
                opt_b(opts, "fx_card_src_alpha",
                      VSH.FX_CARD_SRC_FACTOR_IS_SRC_ALPHA),
                opt_b(opts, "fx_card_additive", True))
        elif ss == VSH.RING_SHADERSET:
            ok, why = apply_ring_terms(mat, spec, pkg_dir, names,
                                       opt_b(opts, "ring_specular", True),
                                       opts.get("ring_env", ""),
                                       opt_f(opts, "ring_ambient_spec", was),
                                       opt_f(opts, "ring_ao", 1.0))
        elif ss == VSH.MOON_SHADERSET:
            ok, why = apply_moon_terms(mat, spec)
        else:
            continue
        done.append((key, ok, why))
        if ss in fog_set:
            ok2, why2 = apply_scene_fog(mat, fog_f, fog_rgb)
            if ok2:
                done.append((key + " [fog]", ok2, why2))
    return done


def mesh_vertex_colour_means(pkg_dir, manifest, layer="color1"):
    """The mean of each object's `color1` stream, straight off the blob.

    The shader multiplies its whole diffuse by `1 + sum(saturate(BLEND.xyz))`
    with `BLEND` = COLOR set 1, so this value is a light-transport term, not a
    tint — and the importer does not carry it.  Read here rather than assumed.
    """
    import array
    out = {}
    for obj in manifest.get("objects", []):
        att = (obj.get("attributes") or {}).get(layer)
        if not att:
            continue
        a = array.array("f")
        try:
            a.frombytes((Path(pkg_dir) / att["blob"]).read_bytes())
        except OSError:
            continue
        n = int(att.get("count", 4))
        if n <= 0 or not len(a):
            continue
        cnt = len(a) // n
        out[obj["name"][:6]] = tuple(
            sum(a[i * n + c] for i in range(cnt)) / cnt for c in range(min(3, n)))
    return out


# ---------------------------------------------------------------------------
# the skydome special case
# ---------------------------------------------------------------------------

def isolate_skydome(context, domes):
    """Move `domes` into their own top-level collection (idempotent)."""
    coll = bpy.data.collections.get(SKYDOME_COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(SKYDOME_COLLECTION)
        context.scene.collection.children.link(coll)
    for ob in domes:
        for c in list(ob.users_collection):
            c.objects.unlink(ob)
        coll.objects.link(ob)
    return coll


def _set_exclude(view_layer, coll_name, excluded):
    """Set `exclude` on a top-level layer-collection by name; True when found."""
    for lc in view_layer.layer_collection.children:
        if lc.collection.name == coll_name:
            lc.exclude = excluded
            return True
    return False


def setup_composite(context, sky_coll):
    """Two view layers + Alpha Over — the pool-19 background fill, exactly.

    `main` renders everything EXCEPT the dome on a transparent film; `sky`
    renders the dome alone.  The compositor puts `main` over `sky`.  No geometry
    moves, no depth is faked, and (because Cycles emits premultiplied alpha and
    Alpha Over with `use_premultiply=False` computes `fg + (1-a)·bg`) an additive
    surface still ADDS onto the sky instead of blending over it.
    """
    scene = context.scene
    scene.render.film_transparent = True

    vl_main = scene.view_layers[0]
    vl_main.name = LAYER_MAIN
    vl_sky = scene.view_layers.new(LAYER_SKY)

    if not _set_exclude(vl_main, sky_coll.name, True):
        raise SystemExit("skydome collection is not a child of the scene collection")
    for lc in vl_sky.layer_collection.children:
        lc.exclude = (lc.collection.name != sky_coll.name)

    # ⚠ Blender 5.x moved the scene compositor to a NODE GROUP
    # (`scene.compositing_node_group`, a `CompositorNodeTree`) and DELETED
    # `CompositorNodeComposite` — the result now leaves through a
    # `NodeGroupOutput`.  `scene.node_tree` no longer exists, and
    # `scene.use_nodes` is deprecated.  Both APIs are handled so this harness is
    # not pinned to one Blender.
    legacy = hasattr(scene, "node_tree")
    if legacy:
        scene.use_nodes = True
        tree = scene.node_tree
        tree.nodes.clear()
    else:
        tree = bpy.data.node_groups.new("le_vista_composite", "CompositorNodeTree")
        tree.nodes.clear()
        tree.interface.new_socket("Image", in_out="OUTPUT",
                                  socket_type="NodeSocketColor")
        scene.compositing_node_group = tree

    rl_sky = tree.nodes.new("CompositorNodeRLayers")
    rl_sky.scene = scene
    rl_sky.layer = LAYER_SKY
    rl_sky.location = (-400, 200)
    rl_main = tree.nodes.new("CompositorNodeRLayers")
    rl_main.scene = scene
    rl_main.layer = LAYER_MAIN
    rl_main.location = (-400, -200)
    over = tree.nodes.new("CompositorNodeAlphaOver")
    over.location = (0, 0)
    # ★ THE ONE SETTING THE MODE DEPENDS ON.  "Straight Alpha" OFF means
    # PREMULTIPLIED over: `out = fg + (1 - a_fg) * bg`.  Cycles writes
    # premultiplied alpha, so an ADDITIVE surface (colour > 0, alpha ~ 0) lands
    # on the sky as `sky + fg` — additive, which is what `eBlendLinearDodge`
    # means.  Turning it on would blend the haze over the sky instead of adding
    # it, and would silently mute every additive vista card.
    if "Straight Alpha" in over.inputs:
        over.inputs["Straight Alpha"].default_value = False
        premul_note = "Straight Alpha=False (premultiplied over)"
    else:                                       # Blender <= 4.x node property
        over.use_premultiply = False
        premul_note = f"use_premultiply={over.use_premultiply}"
    if "Factor" in over.inputs:
        over.inputs["Factor"].default_value = 1.0
    else:
        over.inputs[0].default_value = 1.0

    bg_in = over.inputs["Background"] if "Background" in over.inputs else over.inputs[1]
    fg_in = over.inputs["Foreground"] if "Foreground" in over.inputs else over.inputs[2]
    tree.links.new(rl_sky.outputs["Image"], bg_in)
    tree.links.new(rl_main.outputs["Image"], fg_in)

    if legacy:
        comp = tree.nodes.new("CompositorNodeComposite")
        comp.location = (300, 0)
        tree.links.new(over.outputs["Image"], comp.inputs["Image"])
    else:
        out_node = tree.nodes.new("NodeGroupOutput")
        out_node.location = (300, 0)
        tree.links.new(over.outputs["Image"], out_node.inputs["Image"])

    say("skydome", f"composite: view layers {LAYER_MAIN!r} (dome excluded) over "
                   f"{LAYER_SKY!r} (dome only); film_transparent="
                   f"{scene.render.film_transparent}; AlphaOver {premul_note}; "
                   f"api={'scene.node_tree' if legacy else 'compositing_node_group'}")
    return vl_main, vl_sky


def camera_only_visibility(domes):
    """Make the dome visible to CAMERA rays only — never to light transport.

    ★★ THE THING THAT MATTERS MOST, AND IT IS NOT THE OCCLUSION.  Imported as an
    ordinary mesh, the skydome is a **closed opaque shell around the entire
    level**, so in a path tracer it is LIGHT-TIGHT: a Blender SUN is at infinity
    and is therefore always outside it, and every ray toward it is blocked.
    Measured on this level: with the dome present, switching the level's sun
    off changes **0.011 %** of pixels (max delta 27/255); with the dome removed,
    the same switch changes **0.795 %** (max 137).  The level's key light is
    simply gone, silently, and the render just looks like dim ambient.

    ⛔ This is why the geometric `scale` workaround is NOT sufficient on its own:
    a bigger shell is still a closed shell.  Scaling fixes piercing and does
    nothing about the light.

    Cycles' per-object ray visibility fixes it exactly: keep `visible_camera`
    and clear every other ray type, so the dome still draws but never blocks,
    shadows or bounces.
    """
    changed = []
    for ob in domes:
        for attr in ("visible_diffuse", "visible_glossy", "visible_transmission",
                     "visible_volume_scatter", "visible_shadow"):
            if hasattr(ob, attr):
                setattr(ob, attr, False)
                changed.append(attr)
        if hasattr(ob, "visible_camera"):
            ob.visible_camera = True
    return sorted(set(changed))


def scale_skydome(domes, k):
    """Scale the dome about the WORLD ORIGIN.

    Applied on `matrix_basis` because the importer's axis conversion lives there
    and `matrix_world` is stale until the depsgraph runs.  A uniform scale about
    the origin commutes with that rotation, so this is the same as scaling the
    game-space geometry about the game origin — which is where the dome is
    authored (`vista_measure.py`: the origin fits it BETTER than its own vertex
    centroid, 0.64 % vs 1.45 % RMS).
    """
    S = Matrix.Scale(k, 4)
    for ob in domes:
        ob.matrix_basis = S @ ob.matrix_basis
    bpy.context.view_layer.update()


# ---------------------------------------------------------------------------
# scene inspection
# ---------------------------------------------------------------------------

def world_points(context, cap=200_000, skip=(), corners=True):
    """World-space vertices of everything the view layer renders.

    `corners` adds each object's bound-box corners so a thin prop is never
    subsampled out of the camera fit.  ⚠ Turn it OFF for any MEASUREMENT: a
    bound-box corner need not be occupied by geometry, and including them
    inflates "how far the scene reaches" (94,170 vs the true 77,408 here).
    """
    dg = context.evaluated_depsgraph_get()
    skip = set(skip)

    def usable(ob):
        return (ob is not None and ob.type == "MESH" and ob.data
                and len(ob.data.vertices) > 0 and ob.name not in skip)

    total = 0
    for inst in dg.object_instances:
        if usable(inst.object):
            total += len(inst.object.data.vertices)
    step = max(1, total // cap)
    pts = []
    for inst in dg.object_instances:
        ob = inst.object
        if not usable(ob):
            continue
        mw = inst.matrix_world
        vs = ob.data.vertices
        for i in range(0, len(vs), step):
            co = mw @ vs[i].co
            pts.append((co.x, co.y, co.z))
        if corners:
            for corner in ob.bound_box:
                co = mw @ Vector(corner)
                pts.append((co.x, co.y, co.z))
    return pts


def pierce_report(context, domes):
    """How far the scene reaches vs the dome's radius — the number that forces
    the special case, measured INSIDE Blender on the built scene."""
    if not domes:
        return None
    dome_pts = []
    for ob in domes:
        mw = ob.matrix_world
        for v in ob.data.vertices:
            co = mw @ v.co
            dome_pts.append((co.x, co.y, co.z))
    r = VF.sphere_residuals(dome_pts, (0.0, 0.0, 0.0))
    scene_pts = world_points(context, cap=120_000,
                             skip={ob.name for ob in domes}, corners=False)
    if not scene_pts:
        return {"shell": r, "outside": 0, "total": 0, "d_max": 0.0}
    ds = [math.dist(p, (0.0, 0.0, 0.0)) for p in scene_pts]
    outside = sum(1 for d in ds if d > r["r_mean"])
    return {"shell": r, "outside": outside, "total": len(ds),
            "d_max": max(ds), "k_min": max(ds) / r["r_mean"]}


# ---------------------------------------------------------------------------
# lights
# ---------------------------------------------------------------------------

def sun_from_card(scene, manifest, pkg_dir, energy, angle_deg=0.53):
    """A SUN lamp along the DECODED direction of the shipped sun card `obj002`.

    ★ The direction is decoded, not chosen: `obj002` is a 4-vertex card
    (`vst_sun`, `vst_sun_hdr_opc`) whose four corners all sit at the same
    distance from the world origin, so its centroid direction IS where the level
    puts its sun.  ⚠ The lamp's ENERGY and angular size are look choices and are
    labelled as such.
    """
    card = None
    for obj in manifest["objects"]:
        keys = {d.get("material_key") for d in obj.get("draws", [])}
        for m in manifest.get("materials", []):
            if m["key"] in keys and len(obj.get("attributes", {})) and \
                    obj["vertex_count"] == 4:
                card = obj
                break
        if card is not None:
            break
    if card is None:
        return None, "no 4-vertex card object found"
    import array
    a = array.array("f")
    a.frombytes((Path(pkg_dir) / card["attributes"]["position"]["blob"]).read_bytes())
    pts = [(a[3 * i], a[3 * i + 1], a[3 * i + 2]) for i in range(len(a) // 3)]
    c = VF.centroid(pts)
    d = math.dist(c, (0.0, 0.0, 0.0))
    if d <= 0:
        return None, "sun card centroid is at the origin"
    game_dir = tuple(x / d for x in c)
    bl_dir = Vector(game_to_blender(game_dir))

    light = bpy.data.lights.new("sun_from_card", type="SUN")
    light.energy = energy
    light.angle = math.radians(angle_deg)
    light.color = (1.0, 0.97, 0.92)
    ob = bpy.data.objects.new("sun_from_card", light)
    scene.collection.objects.link(ob)
    # a SUN shines along its -Z; point -Z at the scene, i.e. from the card toward
    # the origin
    ob.rotation_euler = (-bl_dir).to_track_quat("-Z", "Y").to_euler()
    return ob, ("%s at %.0f units; game dir (%.4f, %.4f, %.4f) -> Blender "
                "(%.4f, %.4f, %.4f); energy %.3f W/m^2 is a LOOK CHOICE"
                % (card["name"], d, *game_dir, *bl_dir, energy))


def lights_from_sidecar(path, light_set="diffuse"):
    """★★ The level's OWN `CGLight` rig, from `extractor/le_lights.py`'s sidecar.

    This is what replaces `sun_from_card`.  `min_itc_master` ships **4** records
    — 2 `eDirectionalLight` (one flagged `ePrimaryDirLight` + `eBakeIndirect`)
    and 2 `ePointLight` — and the scene's own `dirlightdirections` table holds
    exactly those two directions with `dirlightindices == [0, 3]`, so the sun is
    DIRECTIONAL and there is no positional reading to choose.

    ⚠ It does NOT light Saturn, and that is correct: both directional lights put
    the sub-observer point of the planet at `N.L ~ -0.595`, and the vista
    shaderset's own diffuse is `saturate((N.L + 0.25) * 0.8)^2`, i.e. identically
    zero there.  `le_mesh.vista_shader.body_is_sunlit` is the assertion.
    """
    summary = lone_echo_import.import_lights(
        str(path), bpy.context,
        {"light_set": light_set, "hide_specular_only": False})
    return summary


# ---------------------------------------------------------------------------
# camera
# ---------------------------------------------------------------------------

def reference_camera(manifest, pkg_dir, scatter_centre, back, lift, lens,
                     resx, resy):
    """The composition of the game's own published mining-vista still, built
    from DECODED directions: stand in the play area, back off along the ring
    plane away from Saturn, and look at Saturn.

    ⚠ `back` and `lift` are framing choices (how much foreground debris to
    include).  The two DIRECTIONS — toward Saturn, and the ring-plane normal —
    are measured off the shipped geometry.
    """
    import array

    def pts_of(name_prefix):
        for obj in manifest["objects"]:
            if obj["name"].startswith(name_prefix):
                a = array.array("f")
                a.frombytes((Path(pkg_dir) /
                             obj["attributes"]["position"]["blob"]).read_bytes())
                return [(a[3 * i], a[3 * i + 1], a[3 * i + 2])
                        for i in range(len(a) // 3)]
        return None

    saturn = pts_of("obj030")
    ring = pts_of("obj038") or pts_of("obj035")
    if saturn is None or ring is None:
        return None, "obj030 / obj038 not in this package"
    sat_dir = VF.angular_extent(saturn)["direction"]           # game space
    plane = VF.fit_plane(ring)
    n = plane["normal"]
    if sum(n[i] * (0.0, 1.0, 0.0)[i] for i in range(3)) < 0:   # keep it "up"
        n = tuple(-x for x in n)

    eye_g = tuple(scatter_centre[i] - sat_dir[i] * back + n[i] * lift
                  for i in range(3))
    eye = game_to_blender(eye_g)
    target = game_to_blender(tuple(scatter_centre[i] + sat_dir[i] * back * 4.0
                                   for i in range(3)))
    return (eye, target), ("saturn dir (game) (%.4f, %.4f, %.4f); ring normal "
                           "(%.4f, %.4f, %.4f); back %.0f lift %.0f about the "
                           "play-area centre (%.1f, %.1f, %.1f)"
                           % (*sat_dir, *n, back, lift, *scatter_centre))


# ---------------------------------------------------------------------------
# render config
# ---------------------------------------------------------------------------

def configure_render(scene, *, engine, device, samples, resx, resy,
                     view="Standard", look="None", exposure=0.0, tile_size=0,
                     denoise=True):
    scene.render.resolution_x = resx
    scene.render.resolution_y = resy
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"

    if engine == "cycles":
        scene.render.engine = "CYCLES"
        scene.cycles.samples = samples
        # ⚠ HOST memory, not device memory, is what caps resolution here.  With
        # tiling the DEVICE holds one tile, but Cycles still keeps every pass at
        # FULL FRAME on the host, and denoising adds a second full-frame float4
        # ("Noisy Image") plus the albedo/normal guides.  At 12288x5376 that is
        # 1.057 GB per float4 pass and it is what makes the frame fail.
        scene.cycles.use_denoising = bool(denoise)
        scene.cycles.max_bounces = 8
        scene.cycles.transparent_max_bounces = 24
        prefs = bpy.context.preferences.addons.get("cycles")
        chosen = "CPU"
        if prefs and device.upper() != "CPU":
            cp = prefs.preferences
            order = ([device.upper()] if device else []) + ["OPTIX", "CUDA",
                                                            "HIP", "ONEAPI"]
            for dt in order:
                if dt in ("CPU", ""):
                    break
                try:
                    cp.compute_device_type = dt
                    devs = list(cp.get_devices_for_type(dt))
                except Exception:
                    continue
                if devs:
                    for d in cp.devices:
                        d.use = (d.type == dt)
                    scene.cycles.device = "GPU"
                    chosen = f"{dt}:{','.join(d.name for d in devs if d.type == dt)}"
                    break
        else:
            scene.cycles.device = "CPU"
        # ★ Big frames only fit a 4 GB card because Cycles renders them in
        # TILES: the device buffer is one tile, not the whole image.  `tile_size`
        # is 0 (= leave Blender's own default alone) unless a caller asks, and
        # the effective state is logged either way so a large render's memory
        # behaviour is on the record rather than assumed.
        if tile_size:
            scene.cycles.use_auto_tile = True
            scene.cycles.tile_size = int(tile_size)
        try:
            say("tiles", "use_auto_tile=%s tile_size=%d -> %dx%d = %d tile(s) "
                         "for %dx%d"
                         % (scene.cycles.use_auto_tile, scene.cycles.tile_size,
                            -(-resx // scene.cycles.tile_size),
                            -(-resy // scene.cycles.tile_size),
                            (-(-resx // scene.cycles.tile_size)
                             * -(-resy // scene.cycles.tile_size))
                            if scene.cycles.use_auto_tile else 1,
                            resx, resy))
        except Exception as exc:                    # pragma: no cover
            say("tiles", f"unavailable on this build: {exc}")
        say("engine", f"CYCLES samples={samples} device={chosen} "
                      f"denoise={scene.cycles.use_denoising}")
    else:
        ids = {e.identifier for e in
               bpy.types.Scene.bl_rna.properties["render"].fixed_type
               .properties["engine"].enum_items}
        scene.render.engine = ("BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in ids
                               else "BLENDER_EEVEE")
        try:
            scene.eevee.taa_render_samples = samples
            scene.eevee.use_raytracing = True
        except Exception:
            pass
        say("engine", f"{scene.render.engine} samples={samples}")

    scene.view_settings.view_transform = view
    scene.view_settings.look = look
    scene.view_settings.exposure = exposure
    scene.view_settings.gamma = 1.0
    got = scene.view_settings.view_transform
    say("colour", f"view_transform={got!r} look={scene.view_settings.look!r} "
                  f"exposure={scene.view_settings.exposure}")
    if got != view:
        raise SystemExit(f"view_transform did not stick: wanted {view!r}, got {got!r}")


# ---------------------------------------------------------------------------
# strip rendering — how a 66 MP frame fits in host RAM
# ---------------------------------------------------------------------------
#
# ★ `tile_size` bounds the DEVICE buffer.  It does nothing for host RAM: the
#   RenderResult, the compositor buffers and the denoising albedo/normal passes
#   are all sized by image AREA.  A 12288x5376 frame is 66.1 MP, so ONE float
#   RGBA buffer is 1,056,964,608 B — and this scene composites two view layers,
#   each with its own denoising passes, so a whole-frame 12K render asked for
#   >20 GB and died with `Malloc returns null: len=1056964608`.
#
#   Rendering the frame as N horizontal border regions with `use_crop_to_border`
#   makes every one of those buffers scale with the STRIP, not the frame, and
#   the strips stitch back losslessly.  The loop is inside one Blender session
#   on purpose: the scene is imported once and Cycles keeps its BVH between
#   renders, so the per-strip cost is the sampling, not another scene build.

def png_size(path):
    """`(w, h)` from a PNG's IHDR, or `(0, 0)`.  No image library involved."""
    try:
        head = Path(path).read_bytes()[:24]
    except OSError:
        return (0, 0)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return (0, 0)
    return (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))


def strip_bands(resy, strips, overlap=0):
    """Bottom-origin `(y0, y1, core0, core1)` row ranges, one per strip.

    `core0..core1` is the band this strip OWNS; `y0..y1` is what it renders,
    which is the band grown by `overlap` on every inner edge so that the
    stitcher has material to cross-fade.  Rows are half-open, bottom-origin
    (Blender's border convention), and the cores tile `0..resy` exactly.
    """
    strips = max(1, int(strips))
    overlap = max(0, int(overlap))
    edges = [round(i * resy / strips) for i in range(strips + 1)]
    bands = []
    for i in range(strips):
        c0, c1 = edges[i], edges[i + 1]
        y0 = 0 if i == 0 else max(0, c0 - overlap)
        y1 = resy if i == strips - 1 else min(resy, c1 + overlap)
        bands.append((y0, y1, c0, c1))
    return bands


def render_strips(scene, out, resx, resy, strips, overlap=0):
    """Render `out` as `strips` horizontal border regions.  Returns the manifest.

    Writes `<out>.stripNN.png` per strip and `<out>.strips.json` describing where
    each one belongs.  The manifest carries the row range this code ASKED for and
    the pixel height Blender actually produced; a mismatch is logged loudly
    rather than silently stitched into a one-row shear.
    """
    r = scene.render
    bands = strip_bands(resy, strips, overlap)
    r.use_border = True
    r.use_crop_to_border = True
    r.border_min_x, r.border_max_x = 0.0, 1.0
    manifest = {"resx": resx, "resy": resy, "strips": len(bands),
                "overlap": int(overlap), "bands": []}
    ok = True
    for i, (y0, y1, c0, c1) in enumerate(bands):
        # +0.5 px so the float->int truncation in RE_InitState can only land on
        # the row we mean; the outer edges are pinned to the exact 0.0/1.0.
        r.border_min_y = 0.0 if y0 == 0 else (y0 + 0.5) / resy
        r.border_max_y = 1.0 if y1 == resy else (y1 + 0.5) / resy
        path = out.with_name(f"{out.stem}.strip{i:02d}.png")
        r.filepath = str(path)
        t0 = time.time()
        bpy.ops.render.render(write_still=True)
        dt = time.time() - t0
        got = png_size(path)          # the FILE, not `Render Result`: in
        #                               background mode that datablock reports
        #                               0x0, which would make this check a lie.
        want = (resx, y1 - y0)
        if got != want:
            ok = False
            say("strip", f"⚠ strip {i}: Blender produced {got[0]}x{got[1]}, "
                         f"asked for {want[0]}x{want[1]} — DO NOT STITCH")
        say("strip", f"{i + 1}/{len(bands)} rows[{y0},{y1}) core[{c0},{c1}) "
                     f"{got[0]}x{got[1]} {dt:.1f}s -> {path.name}")
        manifest["bands"].append({"i": i, "y0": y0, "y1": y1, "c0": c0, "c1": c1,
                                  "path": path.name, "got_w": got[0],
                                  "got_h": got[1], "seconds": round(dt, 1)})
    manifest["consistent"] = ok
    mpath = out.with_name(f"{out.stem}.strips.json")
    mpath.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    say("strips", f"{len(bands)} strip(s) overlap={overlap} manifest={mpath.name} "
                  f"consistent={ok}")
    return manifest


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    opts = parse_argv()
    if opt_b(opts, "baseline", False):
        # Reproduce the state this tree was in before 2026-08-06, so a
        # before/after pair differs by ONE variable and not by five.
        for k, v in BASELINE_OPTS.items():
            opts[k] = v
    say("module", f"lone_echo_import <- {lone_echo_import.__file__}")

    out = Path(opts.get("out", "/tmp/le_vista.png"))
    out.parent.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    layer_sel = opts.get("layer", "all")
    # ★ `k_world_ambient` (`SGPerFrameConstants` +20) multiplies the SG5 ambient
    # sum in every lit shader disassembled from this level (Saturn, a debris
    # rock, the moons).  It is a per-FRAME engine constant and is in NO level
    # resource, so it cannot be decoded — it is exposed here as an explicit
    # scalar rather than folded silently into a literal.  It maps exactly onto
    # `lightmap_intensity`, which multiplies the baked Emission Strength.
    # ⚠ 1.0 is not a decoded value: it is what a matched-rock control
    # implies to within its own ±20 %, and that control measures the PRODUCT of
    # `k_world_ambient` and our rock albedo error, not `k_world_ambient` alone.
    world_ambient = opt_f(opts, "world_ambient", 1.0)
    if world_ambient != 1.0:
        opts["lightmap_intensity"] = str(
            opt_f(opts, "lightmap_intensity", 1.0) * world_ambient)
        say("world_ambient", "k_world_ambient=%.4f folded into "
                             "lightmap_intensity=%s (a per-FRAME engine constant, "
                             "NOT decodable from the level)"
                             % (world_ambient, opts["lightmap_intensity"]))
    lm_mode = opts.get("lightmap", "")
    # ★ The two paths need DIFFERENT lightmap modes, and the reason is measured,
    # not stylistic.  The dig-site scatter's pages carry real bake (page 10:
    # median 0.008-0.014, max 0.36), so `baked` (Emission = albedo x lightmap,
    # BSDF zeroed) reproduces the shipped look exactly as docs/LIGHTING.md says.
    # ⛔ Saturn and the three moons point at PAGE 13, and page 13 is EMPTY:
    # all five of its SG slices read median 0.0000, p95 0.0000, max <= 0.09
    # (`measured`, all 70 slices split from `facc145a62773061.dds`).  Under
    # `baked` the planet therefore renders BLACK — the BSDF is zeroed and it is
    # multiplied by nothing.  They are `eMTForwardOpaque`, so the forward path is
    # where their light comes from; `ambient` keeps the BSDF live and adds the
    # (here negligible) bake on top.
    # ★ Q3: `auto` imports at `ambient` and then zeroes the BSDF on exactly
    # the shadersets whose own shader has no live light — see
    # `apply_auto_lightmap_mode`.  It is now the default.
    mesh_lm = opts.get("mesh_lightmap", lm_mode or "auto")
    mesh_lm_auto = (mesh_lm == "auto")
    if mesh_lm_auto:
        mesh_lm = "ambient"
    scatter_lm = opts.get("scatter_lightmap", lm_mode or "baked")
    lod = opt_i(opts, "lod", 0)

    mesh_pkg = opts.get("mesh", "")
    scatter_specs = [s for s in opts.get("scatter", "").split(",") if s]
    if layer_sel in ("vista", "meshlist"):
        scatter_specs = []
    if layer_sel == "scatter":
        mesh_pkg = ""
    if not mesh_pkg and not scatter_specs:
        raise SystemExit("nothing to import: pass mesh= and/or scatter=")

    pkg_dir = manifest = None
    imported = []
    domes = []

    if mesh_pkg:
        pkg_dir, manifest = load_manifest(mesh_pkg)
        archive = manifest.get("source", {}).get("archive", "")
        names = rdef_names(opts.get("rdef", ""), opts.get("archive", archive))
        say("rdef", f"{len(names)} texture names for archive {archive!r} "
                    f"(NO is_texture_resource filter)")

        mopts = {
            "import_materials": True,
            "import_armature": False,
            "lod_level": 0,
            "lightmap_mode": mesh_lm,
            "flip_v": True,
            "y_up_to_z_up": True,
        }
        for k in ("lightmap_texture", "lightmap_dir", "lightmap_slice_dir"):
            if k in opts:
                mopts[k] = opts[k]
        if "lightmap_intensity" in opts:
            mopts["lightmap_intensity"] = opt_f(opts, "lightmap_intensity", 1.0)
        for k in ("additive_blend", "additive_unrouted_color", "wire_specular",
                  "brdf_lobe_blend", "ao_to_base_color"):
            if k in opts:
                mopts[k] = opt_b(opts, k, True)
        res = lone_echo_import.import_lemesh(str(pkg_dir), bpy.context, mopts)
        coll = bpy.data.collections.get(res["collection"])
        got = list(coll.all_objects) if coll else []
        imported += got
        say("import", "%s: %d objs %d verts %d tris %d mats"
                      % (pkg_dir.name, res["objects"], res["vertices"],
                         res["triangles"], res["materials"]))
        lm = res.get("lightmap") or {}
        say("lightmap", "mesh path: mode=%s available=%s reason=%r pages=%s "
                        "objects_wired=%s"
                        % (lm.get("mode"), lm.get("available"), lm.get("reason"),
                           lm.get("pages"), lm.get("objects_wired")))

        # --- the honest wiring of the unrouted vista binds -------------------
        specs = {m["key"]: m for m in manifest.get("materials", [])}
        wanted = opt_b(opts, "vista_unrouted_color", False)
        blank_mode = opts.get("colourless_surface", "transparent")
        for mat in bpy.data.materials:
            key = f"{mat.get('le_shaderset', '')}__{mat.get('le_material', '')}"
            spec = specs.get(key)
            if spec is None or spec.get("channels"):
                continue
            if not spec.get("unrouted_roles"):
                got = make_colourless_transparent(mat, blank_mode)
                say("unrouted", f"{key}: no channels AND no unrouted bind — "
                                f"this material shows nothing, and that is what "
                                f"the archive says; {got}")
                continue
            if not wanted:
                role, why = _pick_unrouted(spec, names)
                got = make_colourless_transparent(mat, blank_mode)
                say("unrouted", f"{key} ({spec.get('mattype_name')}): REFUSED — "
                                f"role unknown, contributing nothing "
                                f"(candidate {role}, {why}); {got}. "
                                f"vista_unrouted_color=1 renders it as `inferred`")
                continue
            ok, why = wire_unrouted_emission(mat, spec, pkg_dir, names)
            say("unrouted", f"{key} ({spec.get('mattype_name')}): "
                            f"{'WIRED (inferred)' if ok else 'REFUSED'} — {why}")

        # --- ★★ the shader-confirmed terms, AFTER every other wiring ---------
        if opt_b(opts, "vista_shader", True):
            c1 = mesh_vertex_colour_means(pkg_dir, manifest, "color1")
            for name, mean in sorted(c1.items()):
                say("vista_shader", "BLEND (color1) mean for %s = "
                                    "(%.4f, %.4f, %.4f) -> multiplier %.4f"
                                    % (name, *mean, VSH.blend_multiplier(mean)))
            done = apply_vista_shader_terms(manifest, pkg_dir, names, c1, opts)
            for key, ok, why in done:
                say("vista_shader", f"{key}: "
                                    f"{'APPLIED (shader-confirmed)' if ok else 'skipped'}"
                                    f" — {why}")
            if not done:
                say("vista_shader", "no material in this package is in the "
                                    "disassembled table (le_mesh.vista_shader."
                                    "SHADERSET_TERMS)")
        else:
            say("vista_shader", "OFF (vista_shader=0): Saturn keeps the "
                                "importer's base_color_factor, which the shipped "
                                "pixel shader shows is 14.2x too dark, and the sun card "
                                "keeps its dropped 0.2 and its dropped opacity")

    for spec in scatter_specs:
        sopts = {
            "lod_level": lod,
            "flip_v": True,
            "y_up_to_z_up": True,
            "auto_materials": True,
            "lightmap_mode": scatter_lm,
        }
        for k in ("materials_json", "textures_base", "lightmap_texture",
                  "lightmap_dir", "lightmap_slice_dir"):
            if k in opts:
                sopts[k] = opts[k]
        if "max_instances" in opts:
            sopts["max_instances"] = opt_i(opts, "max_instances", 0)
        if "lightmap_intensity" in opts:
            sopts["lightmap_intensity"] = opt_f(opts, "lightmap_intensity", 1.0)
        if opt_b(opts, "instance_lightmap", True):
            sopts["instance_lightmap"] = True
            sopts.setdefault("instance_lightmap_uv_source", "instance")
        r = lone_echo_import.import_lescatter(spec, bpy.context, sopts)
        coll = bpy.data.collections.get(r["collection"])
        imported += list(coll.all_objects) if coll else []
        say("import", "%s: %d/%d meshes, %d/%d instances placed at LOD %s"
                      % (Path(spec).name, r["meshes_built"], r["meshes_total"],
                         r["instances_placed"], r["instances_total"],
                         r["lod_level"]))
        ilm = r.get("instance_lightmap") or {}
        say("lightmap", "scatter path: enabled=%s stream=%s atlas=%s wired=%s "
                        "datablocks=+%s pages=%s reason=%r/%r"
                        % (ilm.get("enabled"), ilm.get("stream_present"),
                           ilm.get("atlas_available"), ilm.get("instances_wired"),
                           ilm.get("datablocks_created"), ilm.get("pages"),
                           ilm.get("stream_reason"), ilm.get("atlas_reason")))

    bpy.context.view_layer.update()

    # --- only= / drop= : a same-camera A/B, by DELETION --------------------
    only = [s.strip().lower() for s in opts.get("only", "").split(",") if s.strip()]
    drop = [s.strip().lower() for s in opts.get("drop", "").split(",") if s.strip()]
    if only or drop:
        keep, removed = [], []
        for ob in imported:
            n = ob.name.lower()
            ok = (not only or any(s in n for s in only)) and \
                 not any(s in n for s in drop)
            (keep if ok else removed).append(ob)
        for ob in removed:
            try:
                bpy.data.objects.remove(ob, do_unlink=True)
            except Exception:
                pass
        imported = keep
        bpy.context.view_layer.update()
        say("filter", f"only={only or '-'} drop={drop or '-'} -> kept "
                      f"{len(keep)}, removed {len(removed)}")

    # --- the skydome ------------------------------------------------------
    domes = skydome_objects(imported)
    say("skydome", "found %d object(s) with mattype %d (eMTSkydome): %s"
                   % (len(domes), MATTYPE_SKYDOME, [o.name for o in domes]))
    pr = pierce_report(bpy.context, domes)
    if pr:
        say("skydome", "shell R_mean %.1f (rms %.3f %% about the world origin); "
                       "scene reaches %.1f => %d of %d sampled vertices (%.1f %%) "
                       "lie OUTSIDE the shell; minimum non-piercing scale "
                       "K_min = %.4f"
                       % (pr["shell"]["r_mean"], 100 * pr["shell"]["rms_rel"],
                          pr["d_max"], pr["outside"], pr["total"],
                          100.0 * pr["outside"] / max(pr["total"], 1),
                          pr.get("k_min", 0.0)))

    mode = opts.get("skydome", "engine")
    sky_coll = None
    if domes and mode == "off":
        # ⚠ capture the names BEFORE the removal: touching `.name` on a removed
        # datablock raises `ReferenceError: StructRNA of type Object has been
        # removed`, not a Python error you can ignore.
        dome_ids = {d.as_pointer() for d in domes}
        imported = [o for o in imported if o.as_pointer() not in dome_ids]
        for ob in domes:
            bpy.data.objects.remove(ob, do_unlink=True)
        domes = []
        say("skydome", "off: the dome is DELETED — the vista against black")
    elif domes and mode == "scale":
        k = opt_f(opts, "skydome_scale", 1.35)
        if pr and k < pr.get("k_min", 0.0):
            say("skydome", "⚠ skydome_scale=%.4f is BELOW the measured K_min "
                           "%.4f — geometry will still pierce"
                           % (k, pr["k_min"]))
        scale_skydome(domes, k)
        p_in = math.degrees(math.atan2(1720.0, pr["shell"]["r_mean"])) if pr else 0.0
        p_out = math.degrees(math.atan2(1720.0, pr["shell"]["r_mean"] * k)) if pr else 0.0
        say("skydome", "scale: x%.4f about the WORLD ORIGIN. EXACT from the "
                       "origin; starfield parallax at |T|=1720 goes %.3f deg -> "
                       "%.3f deg (a stated %.3f deg deviation)"
                       % (k, p_in, p_out, p_in - p_out))
        if opt_b(opts, "skydome_camera_only", True):
            got = camera_only_visibility(domes)
            say("skydome", "scale: + CAMERA-ONLY ray visibility (cleared %s). "
                           "Without this the scaled shell is still LIGHT-TIGHT "
                           "and the level's sun stays blocked — scaling alone "
                           "fixes piercing, not lighting."
                           % ", ".join(got))
    elif domes and mode == "engine":
        # ★★ P1 IS SETTLED, and it overturns the old `composite` default.
        # `a849eddeb321dcc7`'s vertex shader APPLIES the view matrix's translation row
        # (`add r0.xyz, r2.xyzx, cb1[r1.y+24].xyzx`) — no camera pin — and
        # `mov o0.xyzw, r2.xyzw` passes the full projection through with no
        # reversed-Z far-plane pin; the ps declares SV_Target 0/1 only, no
        # SV_Depth and no discard.  ⇒ the dome is ORDINARY GEOMETRY at its own
        # projected depth and overwrites anything beyond the shell.  `depth` is
        # the engine's reading; `composite` was the competing one.
        #
        # But a rasteriser's opaque shell and a PATH TRACER's opaque shell are
        # not the same object: in Cycles the closed dome is LIGHT-TIGHT, and a
        # SUN lamp is at infinity, hence always outside it.  That is an artefact
        # of the renderer, not a property of the engine — the engine's dome
        # blocks no light because the engine traces none.  So: keep the depth
        # behaviour, drop the light blocking.
        got = camera_only_visibility(domes)
        say("skydome", "engine ★ P1-CONFIRMED: ordinary depth (no camera pin, no "
                       "z pin, no SV_Depth in a849eddeb321dcc7) + CAMERA-ONLY ray "
                       "visibility (cleared %s). The occlusion is the engine's; "
                       "the light-blocking would be a path-tracer artefact."
                       % ", ".join(got))
    elif domes and mode == "depth":
        say("skydome", "depth: the engine's occlusion (P1-confirmed) but the dome "
                       "is left LIGHT-TIGHT — every lamp outside the shell is "
                       "blocked. Use skydome=engine unless you want that.")
    elif domes and mode == "composite":
        sky_coll = isolate_skydome(bpy.context, domes)
    elif not domes:
        say("skydome", "no eMTSkydome material in this import — nothing to do")

    # --- camera ------------------------------------------------------------
    resx = opt_i(opts, "resx", 2048)
    resy = opt_i(opts, "resy", 1152)
    lens = opt_f(opts, "lens", 32.0)

    pts = world_points(bpy.context, cap=150_000)
    if not pts:
        raise SystemExit("nothing renderable was imported")
    lo = [min(p[i] for p in pts) for i in range(3)]
    hi = [max(p[i] for p in pts) for i in range(3)]
    say("subject", "%d sampled pts bbox=%s..%s"
                   % (len(pts), [round(v, 1) for v in lo], [round(v, 1) for v in hi]))

    cam_loc = opt_vec(opts, "cam_loc")
    cam_target = opt_vec(opts, "cam_target")
    cam_mode = opts.get("cam", "explicit" if cam_loc else "reference")
    if cam_mode == "reference" and manifest is not None:
        centre = (-321.1, -39.4, 262.2)          # play-area centre, game space
        if "play_centre" in opts:
            centre = opt_vec(opts, "play_centre")
        got, why = reference_camera(manifest, pkg_dir, centre,
                                    opt_f(opts, "cam_back", 1500.0),
                                    opt_f(opts, "cam_lift", 180.0),
                                    lens, resx, resy)
        if got is None:
            raise SystemExit(f"reference camera: {why}")
        cam_loc, cam_target = got
        say("camera", f"reference composition — {why}")
    if cam_loc is None:
        az = opt_f(opts, "azimuth", 24.0)
        el = opt_f(opts, "elevation", 8.0)
        fit = framing.fit_view(pts, framing.orbit_direction(az, el), lens=lens,
                               res_x=resx, res_y=resy,
                               margin=opt_f(opts, "margin", 1.05))
        say("camera", f"orbit fit az={az} el={el} dist={fit['distance']:.1f}")
    else:
        if cam_target is None:
            cam_target = tuple((lo[i] + hi[i]) * 0.5 for i in range(3))
        fit = framing.look_at(cam_loc, cam_target, pts, lens=lens,
                              res_x=resx, res_y=resy)
        say("camera", "explicit eye=%s target=%s"
                      % ([round(v, 1) for v in cam_loc],
                         [round(v, 1) for v in cam_target]))

    cam_data = bpy.data.cameras.new("vista_cam")
    cam_data.lens = lens
    cam_data.sensor_width = 36.0
    cam_data.sensor_fit = "AUTO"
    # ★ `pano=1` — a full-sky EQUIRECTANGULAR frame from the camera position.
    # This is the RIGHT instrument for the skydome question: the shell surrounds
    # the viewer, so "what does it cover" is a whole-sphere question and a 60-deg
    # crop can miss the answer entirely (it did on the first attempt here).
    if opt_b(opts, "pano", False):
        cam_data.type = "PANO"
        for attr, val in (("panorama_type", "EQUIRECTANGULAR"),):
            if hasattr(cam_data, attr):
                setattr(cam_data, attr, val)
            elif hasattr(cam_data, "cycles") and hasattr(cam_data.cycles, attr):
                setattr(cam_data.cycles, attr, val)
        say("camera", "PANORAMIC equirectangular (full sky from the eye)")
    # The dome is ~60 km out and the rocks are ~10 units; the clip range has to
    # span both or the sky is culled and the picture silently loses its sky.
    cam_data.clip_start = max(opt_f(opts, "clip_start", 1.0), 1e-3)
    cam_data.clip_end = max(fit["clip_end"], 500_000.0)
    cam = bpy.data.objects.new("vista_cam", cam_data)
    scene.collection.objects.link(cam)
    right, up, back = fit["basis"]
    rot = Matrix(((right[0], up[0], back[0]),
                  (right[1], up[1], back[1]),
                  (right[2], up[2], back[2]))).to_4x4()
    cam.matrix_world = Matrix.Translation(Vector(fit["location"])) @ rot
    scene.camera = cam
    say("camera", "lens=%.1f clip=%.2f..%.0f loc=(%.1f, %.1f, %.1f)"
                  % (lens, cam_data.clip_start, cam_data.clip_end,
                     *fit["location"]))

    # --- world + sun -------------------------------------------------------
    world = bpy.data.worlds.new("vacuum")
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.0, 0.0, 0.0, 1.0)
    bg.inputs[1].default_value = opt_f(opts, "world", 0.0)
    scene.world = world
    say("world", f"background strength {bg.inputs[1].default_value} "
                 f"(0 = the level supplies all its own light)")

    lights_path = opts.get("lights", "")
    sun_mode = opts.get("sun", "auto")
    if sun_mode == "auto":
        sun_mode = "lights" if lights_path else "card"
    # ⚠ 14.8 W/m^2 is the REAL solar constant at Saturn's orbit
    # (1361 / 9.5826^2).  It is a defensible anchor for a scene whose units read
    # as metres, NOT a decoded engine value, and it is now SUPERSEDED for this
    # level: `extractor/le_lights.py 4c47d84c1e52447a` yields the real records
    # and their `primarycolor` peaks are 80 and 10 W/m^2 on the two directional
    # lights.  `card`/`rig` keep the old look choice so old renders reproduce.
    SOLAR_AT_SATURN = 1361.0 / (9.5826 ** 2)
    if sun_mode == "lights":
        if not lights_path:
            raise SystemExit("sun=lights needs lights=<le_lights sidecar .json>")
        s = lights_from_sidecar(lights_path, opts.get("light_set", "diffuse"))
        say("sun", "★★ DECODED CGLight rig from %s: %s/%s records kept "
                   "(light_set=%s) by_type=%s diffuse_enabled=%s "
                   "specular_enabled=%s lossy_falloff=%s"
                   % (Path(lights_path).name, s.get("imported"), s.get("total"),
                      s.get("light_set"), s.get("by_type"),
                      s.get("diffuse_enabled"), s.get("specular_enabled"),
                      s.get("lossy_falloff")))
        for w in s.get("warnings", []):
            say("sun", "  ⚠ " + w)
        say("sun", "  ⛔ NONE of these lights reaches Saturn's visible disc: "
                   "the vista shaderset's diffuse is saturate((N.L+0.25)*0.8)^2 "
                   "and both directional lights give N.L ~ -0.595 there "
                   "(le_mesh.vista_shader.body_is_sunlit).")
    elif sun_mode == "card" and manifest is not None:
        say("sun", "⚠⚠ `sun=card` FABRICATES a light the engine does not have. "
                   "min_itc_master ships 2 eDirectionalLight records at 6.6 and "
                   "16.5 deg from this card's centroid, peak 80 and 10 W/m^2. "
                   "Pass lights=<sidecar> for the decoded rig.")
        ob, why = sun_from_card(scene, manifest, pkg_dir,
                                opt_f(opts, "sun_energy", SOLAR_AT_SATURN))
        say("sun", ("DECODED from the shipped sun card: " + why) if ob else
                   f"card mode unavailable: {why}")
    elif sun_mode == "rig":
        light = bpy.data.lights.new("key", type="SUN")
        light.energy = opt_f(opts, "sun_energy", SOLAR_AT_SATURN)
        ob = bpy.data.objects.new("key", light)
        scene.collection.objects.link(ob)
        ob.rotation_euler = Vector((-0.55, -0.35, 0.25)).to_track_quat(
            "-Z", "Y").to_euler()
        say("sun", "generic rig sun — a LOOK CHOICE, not decoded")
    else:
        say("sun", "none")

    if mesh_lm_auto:
        for why in apply_auto_lightmap_mode():
            say("lightmap", "auto: " + why)

    for why in select_dirlight(scene, lights_path, opts.get("dirlight", "primary")):
        say("sun", why)

    if opt_b(opts, "ring_backface_gate", True):
        for why in apply_ring_backface_gate(scene):
            say("vista_shader", why)

    configure_render(scene,
                     engine=opts.get("engine", "cycles"),
                     device=opts.get("device", ""),
                     samples=opt_i(opts, "samples", 96),
                     resx=resx, resy=resy,
                     view=opts.get("view", "Standard"),
                     look=opts.get("look", "None"),
                     exposure=opt_f(opts, "exposure", 0.0),
                     tile_size=opt_i(opts, "tile_size", 0),
                     denoise=opt_b(opts, "denoise", True))

    if sky_coll is not None:
        setup_composite(bpy.context, sky_coll)
    else:
        scene.render.film_transparent = opt_b(opts, "transparent", False)

    strips = opt_i(opts, "strips", 0)
    t0 = time.time()
    if strips > 1:
        render_strips(scene, out, resx, resy, strips,
                      opt_i(opts, "strip_overlap", 0))
        say("render", f"{out} {resx}x{resy} in {strips} strip(s) "
                      f"{time.time() - t0:.1f}s — STITCH PENDING (no whole-frame "
                      f"buffer was ever allocated, which is the point)")
    else:
        scene.render.filepath = str(out)
        bpy.ops.render.render(write_still=True)
        say("render", f"{out} {resx}x{resy} {time.time() - t0:.1f}s")

    log = out.with_suffix(".log.txt")
    log.write_text("\n".join(_log), encoding="utf-8")
    say("done", str(log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
