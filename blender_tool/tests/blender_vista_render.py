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

  ``composite``  ★ DEFAULT, and the one that reproduces the MECHANISM.  The
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
                 i.e. additive, which is the correct engine behaviour and is the
                 reason this mode is preferred over the geometric one below.
  ``scale``      Scale the dome about the WORLD ORIGIN by `skydome_scale` until
                 nothing pierces it.  ⚠ NOT free: it is EXACT for a camera at
                 the origin (scaling about the origin does not move any
                 direction) and costs starfield PARALLAX everywhere else — at
                 the play area's max |T| = 1,720 the stars shift from 1.665° to
                 1.234° at K = 1.35, a stated 0.43°.  Single-pass, so blend and
                 additive surfaces composite through the normal Cycles path.
  ``depth``      ⛔ NO special case: the documented FAILURE MODE, renderable on
                 demand so the defect is a picture and not a warning.
  ``off``        Delete the dome entirely (the vista against black).

⚠ WHAT THIS HARNESS DOES **NOT** SETTLE.  The actual depth-test / depth-write
state of the engine's skydome pass is NOT established: nothing in the engine's
own material authoring carries a depth state, and the code that derives it from
the material type is not readable from the shipped bytes.  In particular the
skydome ALSO has a prepass at pool 2, and if that prepass writes depth then the
engine itself would reject Saturn's far limb — the opposite of what `composite`
draws.  `composite` implements the reading docs/SCENES.md takes; `depth` renders
the competing one.  Neither is proof about the shipped renderer.  Only probe P1
(disassemble `a849eddeb321dcc7`'s vertex shader) can settle it.

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
    skydome=composite|scale|depth|off   default composite
    skydome_scale=K            default 1.35 (>= the measured minimum, printed)
    vista_unrouted_color=1|0   default 0 — see above
    additive_unrouted_color=1|0        passed to material_builder (haze)
    sun=card|rig|none          default card: a SUN along the DECODED direction
                               of the shipped sun card obj002
    sun_energy=                default 14.83 = the REAL solar constant at
                               Saturn's orbit (1361 / 9.5826^2 W/m^2).  A
                               defensible anchor, NOT a decoded engine value.
    world=                     world background strength, default 0.0
    cam=reference|orbit|explicit       default reference
    cam_loc= cam_target=       explicit camera (Blender space)
    cam_back= cam_lift=        reference-camera standoff / lift, in game units
    lens= resx= resy= samples= engine=cycles|eevee device=optix|cuda|cpu
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


# ---------------------------------------------------------------------------
# camera
# ---------------------------------------------------------------------------

def reference_camera(manifest, pkg_dir, scatter_centre, back, lift, lens,
                     resx, resy):
    """The composition of `examples/nathan-phail-liff-mining-vista-01.jpg`, built
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
                     view="Standard", look="None", exposure=0.0):
    scene.render.resolution_x = resx
    scene.render.resolution_y = resy
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"

    if engine == "cycles":
        scene.render.engine = "CYCLES"
        scene.cycles.samples = samples
        scene.cycles.use_denoising = True
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
        say("engine", f"CYCLES samples={samples} device={chosen}")
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
# main
# ---------------------------------------------------------------------------

def main():
    opts = parse_argv()
    say("module", f"lone_echo_import <- {lone_echo_import.__file__}")

    out = Path(opts.get("out", "/tmp/le_vista.png"))
    out.parent.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene

    layer_sel = opts.get("layer", "all")
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
    mesh_lm = opts.get("mesh_lightmap", lm_mode or "ambient")
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
        for mat in bpy.data.materials:
            key = f"{mat.get('le_shaderset', '')}__{mat.get('le_material', '')}"
            spec = specs.get(key)
            if spec is None or spec.get("channels"):
                continue
            if not spec.get("unrouted_roles"):
                say("unrouted", f"{key}: no channels AND no unrouted bind — "
                                f"this material shows nothing, and that is what "
                                f"the archive says")
                continue
            if not wanted:
                role, why = _pick_unrouted(spec, names)
                say("unrouted", f"{key} ({spec.get('mattype_name')}): REFUSED — "
                                f"role unknown, contributing nothing "
                                f"(candidate {role}, {why}). "
                                f"vista_unrouted_color=1 renders it as `inferred`")
                continue
            ok, why = wire_unrouted_emission(mat, spec, pkg_dir, names)
            say("unrouted", f"{key} ({spec.get('mattype_name')}): "
                            f"{'WIRED (inferred)' if ok else 'REFUSED'} — {why}")

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

    mode = opts.get("skydome", "composite")
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
    elif domes and mode == "depth":
        say("skydome", "⛔ depth: NO special case. This is the documented FAILURE "
                       "MODE — the shell will punch a circular hole through "
                       "Saturn's far limb and the outer rings.")
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

    sun_mode = opts.get("sun", "card")
    # ⚠ 14.8 W/m^2 is the REAL solar constant at Saturn's orbit
    # (1361 / 9.5826^2).  It is a defensible anchor for a scene whose units read
    # as metres, NOT a decoded engine value: this level's own `CGLight` records
    # have not been extracted, and Blender SUN strength is irradiance, so the
    # number is directly comparable.  Treat it as a LOOK CHOICE with a reason.
    SOLAR_AT_SATURN = 1361.0 / (9.5826 ** 2)
    if sun_mode == "card" and manifest is not None:
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

    configure_render(scene,
                     engine=opts.get("engine", "cycles"),
                     device=opts.get("device", ""),
                     samples=opt_i(opts, "samples", 96),
                     resx=resx, resy=resy,
                     view=opts.get("view", "Standard"),
                     look=opts.get("look", "None"),
                     exposure=opt_f(opts, "exposure", 0.0))

    if sky_coll is not None:
        setup_composite(bpy.context, sky_coll)
    else:
        scene.render.film_transparent = opt_b(opts, "transparent", False)

    scene.render.filepath = str(out)
    t0 = time.time()
    bpy.ops.render.render(write_still=True)
    say("render", f"{out} {resx}x{resy} {time.time() - t0:.1f}s")

    log = out.with_suffix(".log.txt")
    log.write_text("\n".join(_log), encoding="utf-8")
    say("done", str(log))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
