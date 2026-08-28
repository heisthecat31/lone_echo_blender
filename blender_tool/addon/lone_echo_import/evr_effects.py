"""Apply a level's fog, exposure and particle emitters.

Reads the `effects.json` sidecar written by `scripts/evr_fx.py`.

## Exposure and tonemapping

`ExposureParams.Exposure` is authored per level and maps straight onto
`scene.view_settings.exposure` -- same meaning, same units (stops).

The tonemap does NOT map straight across. The engine uses the Hable /
Uncharted-2 filmic curve with five authored coefficients (shoulder strength,
linear strength, linear angle, toe strength, white point) and Blender ships no
Hable view transform, so an exact match is impossible from the operator. The
coefficients are recorded on the scene as `evr_tonemap_*` so the value is not
lost, and a Filmic-family view transform is selected when one exists -- it is
the nearest of Blender's, not a reproduction.

⚠ This matters more than it sounds: baked irradiance is physically dark (a lit
arena texel averages ~0.03 linear) precisely because the engine exposes and
tonemaps downstream. Rendering it with neither is what makes an import look
flat and desaturated.

## Fog

The engine's fog is a POST-PROCESS: a linear ramp on view depth between
`StartDepth` and `EndDepth`, blending the image toward `Color` by `Intensity`.
It is not a participating medium.

⛔ Do NOT build it as a world Volume Scatter. Tried, and it renders the scene
BLACK: a world volume fills the entire scene, so across an arena ~150 m wide
even a density of 0.014 gives an optical depth over 2, and with nothing lighting
the medium it simply extinguishes everything. It also fogs geometry closer than
`StartDepth`, which the engine never does.

So this builds the engine's actual model in the compositor:

    Render Layers.Depth -> Map Range(StartDepth..EndDepth -> 0..1, clamped)
                        -> Multiply(Intensity)
                        -> Mix(Render Layers.Image, Color)

which is linear in depth, respects the start distance, and cannot darken
anything -- it can only pull pixels toward the fog colour. The Z pass is
enabled on the view layer because the graph needs it.

⚠ The height band (`StartHeight` / `EndHeight`) is NOT applied; that needs a
world-position pass. Every authored value is on the scene as `evr_fog_*`.

## Particles

Emitter PLACEMENTS only -- an Empty per emitter, named for the effect asset it
plays. The effect definitions are not decoded, so nothing here simulates
anything; these are markers saying "something emits here".
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy

SIDECAR_NAME = "effects.json"
SIDECAR_FORMAT = "evr_effects"

#: Blender view transforms to prefer, nearest-to-Hable first.
FILMIC_PREFERENCE = ("Filmic", "AgX", "Standard")


def sidecar_path(package) -> Path | None:
    root = Path(package)
    if root.is_file():
        root = root.parent
    candidate = root / SIDECAR_NAME
    return candidate if candidate.is_file() else None


def load(package) -> dict | None:
    path = sidecar_path(package)
    if path is None:
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if doc.get("format") == SIDECAR_FORMAT else None


def _to_blender(vec, y_up_to_z_up: bool):
    x, y, z = float(vec[0]), float(vec[1]), float(vec[2])
    return (x, -z, y) if y_up_to_z_up else (x, y, z)


def apply_exposure(doc: dict, scene) -> dict:
    """Authored exposure onto the scene; tonemap coefficients recorded."""
    out = {}
    exposure = (doc.get("exposure") or {}).get("exposure")
    if exposure is not None:
        try:
            scene.view_settings.exposure = float(exposure)
            out["exposure"] = float(exposure)
        except (AttributeError, TypeError, ValueError):
            pass

    tonemap = doc.get("tonemap") or {}
    for key, value in tonemap.items():
        scene["evr_tonemap_%s" % key] = float(value)
    if tonemap:
        scene["evr_tonemap_curve"] = (
            "Hable/Uncharted-2 filmic, authored per level. Blender has no Hable "
            "view transform, so the selected transform is the nearest available, "
            "NOT a reproduction of these coefficients.")
        out["tonemap"] = tonemap

    # A filmic-family transform is the FALLBACK only. `apply_tonemap` builds the
    # engine's actual Hable curve and then forces `Standard`, because a view
    # transform on top of it would tonemap the image twice. This runs first, so
    # whatever it picks here is overwritten in that (normal) case -- it only
    # survives for a level whose tonemap coefficients are missing.
    try:
        available = {i.identifier for i in
                     scene.view_settings.bl_rna.properties["view_transform"].enum_items}
        for want in FILMIC_PREFERENCE:
            if want in available:
                scene.view_settings.view_transform = want
                out["view_transform"] = want
                break
    except (AttributeError, KeyError, TypeError):
        pass
    return out


def apply_world_ambient(scene, strength: float) -> dict:
    """Set the world background, which is FAKE LIGHT unless you ask for it.

    ⭐ This is why an imported level looks washed out and why the skymap comes
    in white. Blender's default world is a mid-grey that lights everything from
    all directions; the engine has no such term. Its ambient comes from the
    baked SH4 / SG lightmaps, and those are off by default here (still marked
    unfinished), so leaving Blender's default in place substitutes a constant
    grey for lighting that should be directional and mostly absent.

    The skymap shows it most clearly. `32378e68ca516a1c`, the 955-unit dome, is
    a PURE WHITE 512x512 albedo plus a 64x64 normal map -- there is no colour in
    the material at all, and no lightmap binding on any of the sky meshes
    (`meshes_lightmapped: 0`). Under a grey world a white albedo is exactly as
    bright as the world; under none it is as dark as the light reaching it,
    which 478 units out is nothing. Measured on `mpl_arena_a`: sky 59.5 -> 15.6
    of 255, arena 109.8 -> 52.0.

    ⚠ It darkens the WHOLE level, not just the sky, because everything was
    getting that free light. That is faithful -- but if baked lighting is off
    and you want to see the geometry, raising this is the honest knob to reach
    for rather than leaving a grey world in and calling it lighting.
    """
    try:
        world = scene.world
        if world is None:
            world = bpy.data.worlds.new("EVR World")
            scene.world = world
        world.use_nodes = True
        background = next((n for n in world.node_tree.nodes
                           if n.type == "BACKGROUND"), None)
        if background is None:
            return {"world": "no background node"}
        background.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
        background.inputs[1].default_value = float(strength)
    except (AttributeError, TypeError, KeyError):
        return {"world": "could not be set"}
    scene["evr_world_ambient"] = float(strength)
    scene["evr_world_note"] = (
        "the engine has no constant ambient term -- its ambient is the baked "
        "SH4/SG lighting. Blender's default grey world is fake light; this is "
        "set to 0 so what you see comes from the level's own lights.")
    return {"world": "ambient %g" % strength}


#: What a scene compositing group must expose for the render pipeline to read
#: its result. See `_compositor_tree`.
COMPOSITOR_OUTPUT_NAME = "Image"


def _ensure_group_output(group):
    """Give a compositing node group its `Image` output socket.

    ⛔ Blender 5 will NOT run a scene compositing group that declares no
    output interface -- silently, with no error and no warning. The graph
    builds, the links report as made, a link dump looks completely correct, and
    the render comes out exactly as if no compositor existed.

    Measured: a group whose only node chain is `Render Layers -> multiply by
    0 -> Group Output` leaves the render at its full brightness (mean 0.61)
    without the interface socket, and at 0.0003 with it. Everything this module
    builds -- fog, exposure, the Hable curve, bloom -- was inert on Blender 5
    for want of these two lines.
    """
    interface = getattr(group, "interface", None)
    if interface is None:
        return
    for item in interface.items_tree:
        if getattr(item, "in_out", "") == "OUTPUT":
            return
    try:
        interface.new_socket(COMPOSITOR_OUTPUT_NAME, in_out="OUTPUT",
                             socket_type="NodeSocketColor")
    except (AttributeError, TypeError, RuntimeError):
        pass


def _connect_to_output(tree, is_group, result):
    """Link `result` to whatever this Blender uses as the compositor output.

    ⛔ A pass that builds a chain must TERMINATE it. `apply_bloom` used to
    stop at its own last node and rely on `apply_tonemap` running afterwards
    to carry the chain to the output. That holds on the level path, where the
    two always run together -- and silently fails anywhere bloom runs alone:
    the graph builds, every link reports as made, a node dump looks perfect,
    and the render comes back byte-identical to no compositor at all.

    Re-terminating is safe. A node input accepts a single link, so a later
    pass (`apply_tonemap` chains onto `evr_bloom_add` by design) simply takes
    the output socket back over.
    """
    if is_group:
        out_node = next((n for n in tree.nodes if n.type == "GROUP_OUTPUT"), None)
        if out_node is None:
            if not any(getattr(i, "in_out", "") == "OUTPUT"
                       for i in getattr(tree.interface, "items_tree", ())):
                tree.interface.new_socket(name=COMPOSITOR_OUTPUT_NAME,
                                          in_out="OUTPUT",
                                          socket_type="NodeSocketColor")
            out_node = _new_node(tree, "NodeGroupOutput")
            if out_node is None:
                return False
            out_node.location = (400, 0)
        tree.links.new(out_node.inputs[0], result)
        return True

    comp = next((n for n in tree.nodes if n.type == "COMPOSITE"), None)
    if comp is None:
        comp = _new_node(tree, "CompositorNodeComposite")
        if comp is None:
            return False
        comp.location = (400, 0)
    tree.links.new(comp.inputs["Image"], result)
    return True


def _compositor_tree(scene):
    """`(tree, is_group)` for whichever compositor this Blender has.

    Blender 5 removed `Scene.node_tree`: the compositor is now a node-group
    datablock on `Scene.compositing_node_group`, and `CompositorNodeComposite`
    /`CompositorNodeMixRGB` no longer exist -- the output is a `NodeGroupOutput`
    and mixing uses the shader-side `ShaderNodeMix`.
    """
    if hasattr(scene, "node_tree"):
        scene.use_nodes = True
        return scene.node_tree, False
    group = getattr(scene, "compositing_node_group", None)
    if group is None:
        group = bpy.data.node_groups.new("EVR Compositor", "CompositorNodeTree")
        try:
            scene.compositing_node_group = group
        except (AttributeError, TypeError):
            return None, False
    # Also for a group that already existed: without this the whole chain is
    # built and then ignored.
    _ensure_group_output(group)
    try:
        scene.use_nodes = True
    except AttributeError:
        pass
    return group, True


def _new_node(tree, *type_names):
    """The first of `type_names` this Blender knows, or None."""
    for name in type_names:
        try:
            return tree.nodes.new(name)
        except Exception:                                    # noqa: BLE001
            continue
    return None


def apply_fog(doc: dict, scene, *, y_up_to_z_up: bool = True) -> dict:
    """The authored depth fog, as a compositor ramp (the engine's own model).

    ## The fog AMOUNT is `colour alpha x intensity`, not intensity

    ⛔ The colour's fourth component is the fog DENSITY, and dropping it makes
    most levels wrong. The proof is the engine's own defaults: `evr_fx` reports
    that `AltFog` carries the unmodified `FogParams` on every file in both
    builds, and that default colour is `(1, 1, 1, 0)` with intensity `1.0`. If
    intensity alone were the amount, a level that touched nothing would render
    fully white -- so the zero alpha has to be what makes the default "no fog".

    Measured over the 32 levels that carry a fog block, alpha is authored as
    0.0 on 13, 1.0 on 11, 0.5 on 5, 0.25 on 1 and 0.03 on 2 -- a real spread,
    not a flag. Reading intensity alone fogged those 13 zero-alpha levels
    solidly, and clamping intensity to 1.0 also threw away the authored 1.25
    and 5.0.

    ⚠ This was invisible until the compositor was fixed to actually run (see
    `_ensure_group_output`); before that the fog node built and did nothing.
    """
    fog = doc.get("fog") or {}
    if not fog:
        return {}
    for key, value in fog.items():
        if key == "color":
            scene["evr_fog_color"] = list(value)
        else:
            scene["evr_fog_%s" % key] = value
    scene["evr_fog_note"] = (
        "built in the compositor as a linear depth ramp between start_depth "
        "and end_depth blending toward color by (colour alpha x intensity) -- "
        "the engine's own model; alpha is the density, see apply_fog. The "
        "height band multiplies that, dense at start_height and clear at "
        "end_height, read from the Position pass.")

    if fog.get("is_default"):
        return {"fog": "left at engine defaults, not built"}
    intensity = float(fog.get("intensity") or 0.0)
    if intensity <= 0.0:
        return {"fog": "intensity 0, not built"}
    start_d = float(fog.get("start_depth") or 0.0)
    end_d = float(fog.get("end_depth") or 0.0)
    if end_d <= start_d:
        return {"fog": "degenerate depth range, not built"}
    raw_colour = list(fog.get("color") or (1.0, 1.0, 1.0, 1.0))
    colour = raw_colour[:3]
    density = float(raw_colour[3]) if len(raw_colour) > 3 else 1.0
    amount = min(max(density * intensity, 0.0), 1.0)
    if amount <= 0.0:
        return {"fog": "colour alpha is 0 -- the engine's 'no fog' default, "
                       "not built"}

    # The Z pass is what makes this depth fog rather than a flat tint, and the
    # Position pass is what makes the height band possible.
    #
    # ⛔ Both must be enabled BEFORE the Render Layers node is created: a node
    # that already exists does not grow a `Position` output when the pass is
    # switched on later, so enabling it further down silently skipped the
    # height band with "no Position pass on this render layer".
    try:
        for layer in scene.view_layers:
            layer.use_pass_z = True
            layer.use_pass_position = True
    except AttributeError:
        pass

    tree, is_group = _compositor_tree(scene)
    if tree is None:
        return {"fog": "no compositor available"}
    if any(n.label == "evr_fog" for n in tree.nodes):
        return {"fog": "already present"}

    rl = next((n for n in tree.nodes if n.type == "R_LAYERS"), None)
    if rl is None:
        rl = _new_node(tree, "CompositorNodeRLayers")
        if rl is None:
            return {"fog": "no render-layer node type"}
        rl.location = (-600, 0)
    depth = rl.outputs.get("Depth") or rl.outputs.get("Z")
    image = rl.outputs.get("Image")
    if depth is None or image is None:
        return {"fog": "render layer has no Depth/Image output"}

    ramp = _new_node(tree, "CompositorNodeMapRange", "ShaderNodeMapRange")
    if ramp is None:
        return {"fog": "no map-range node type"}
    ramp.label = "evr_fog"
    ramp.location = (-300, -220)
    try:
        ramp.use_clamp = True
    except AttributeError:
        try:
            ramp.clamp = True
        except AttributeError:
            pass
    ramp.inputs[1].default_value = start_d
    ramp.inputs[2].default_value = end_d
    ramp.inputs[3].default_value = 0.0
    ramp.inputs[4].default_value = amount
    tree.links.new(ramp.inputs[0], depth)

    # ── height band ────────────────────────────────────────────────────
    # Density falls off with height: full at `start_height`, none at
    # `end_height`. Needs world position per pixel, which is the Position
    # pass; without it the band is skipped rather than guessed at.
    factor = ramp.outputs[0]
    height_note = None
    start_h = float(fog.get("start_height") or 0.0)
    end_h = float(fog.get("end_height") or 0.0)
    position = rl.outputs.get("Position")
    if end_h == start_h:
        height_note = "degenerate height band, not applied"
    elif position is None:
        height_note = "no Position pass on this render layer, not applied"
    else:
        split = _new_node(tree, "ShaderNodeSeparateXYZ", "CompositorNodeSeparateXYZ")
        band = _new_node(tree, "CompositorNodeMapRange", "ShaderNodeMapRange")
        scale, sa, sb, sout = _mix(tree, "MULTIPLY", "evr_fog_height_mul")
        if split is None or band is None or scale is None:
            height_note = "no node type for the height band, not applied"
        else:
            split.label = "evr_fog_pos"
            split.location = (-520, -420)
            tree.links.new(split.inputs[0], position)
            # game Y is up; the importer maps it to Blender Z (see `_to_blender`)
            axis = 2 if y_up_to_z_up else 1
            band.label = "evr_fog_height"
            band.location = (-300, -420)
            try:
                band.use_clamp = True
            except AttributeError:
                try:
                    band.clamp = True
                except AttributeError:
                    pass
            band.inputs[1].default_value = start_h
            band.inputs[2].default_value = end_h
            band.inputs[3].default_value = 1.0      # dense at start_height
            band.inputs[4].default_value = 0.0      # clear at end_height
            tree.links.new(band.inputs[0], split.outputs[axis])
            scale.location = (-120, -320)
            tree.links.new(sa, ramp.outputs[0])
            tree.links.new(sb, band.outputs[0])
            factor = sout
            height_note = {"band": [start_h, end_h], "axis": "Z" if axis == 2 else "Y"}

    mix = _new_node(tree, "CompositorNodeMixRGB", "ShaderNodeMix")
    if mix is None:
        return {"fog": "no mix node type"}
    mix.label = "evr_fog_mix"
    mix.location = (100, 0)
    if hasattr(mix, "data_type"):                # ShaderNodeMix (Blender 5)
        mix.data_type = "RGBA"
        mix.blend_type = "MIX"
        fac_in, a_in, b_in, result = mix.inputs[0], mix.inputs[6], mix.inputs[7], mix.outputs[2]
    else:                                        # CompositorNodeMixRGB
        mix.blend_type = "MIX"
        fac_in, a_in, b_in, result = mix.inputs[0], mix.inputs[1], mix.inputs[2], mix.outputs[0]
    b_in.default_value = (colour + [1.0])[:4]
    tree.links.new(fac_in, factor)
    tree.links.new(a_in, image)

    if is_group:
        out_node = next((n for n in tree.nodes if n.type == "GROUP_OUTPUT"), None)
        if out_node is None:
            if not any(getattr(i, "in_out", "") == "OUTPUT"
                       for i in getattr(tree.interface, "items_tree", ())):
                tree.interface.new_socket(name="Image", in_out="OUTPUT",
                                          socket_type="NodeSocketColor")
            out_node = _new_node(tree, "NodeGroupOutput")
            if out_node is None:
                return {"fog": "no group-output node type"}
            out_node.location = (400, 0)
        tree.links.new(out_node.inputs[0], result)
    else:
        comp = next((n for n in tree.nodes if n.type == "COMPOSITE"), None)
        if comp is None:
            comp = _new_node(tree, "CompositorNodeComposite")
            if comp is None:
                return {"fog": "no composite node type"}
            comp.location = (400, 0)
        tree.links.new(comp.inputs["Image"], result)

    return {"fog": "built", "color": colour, "band": [start_d, end_d],
            "intensity": round(amount, 4), "density": round(density, 4),
            "authored_intensity": round(intensity, 4), "height": height_note}


# ---------------------------------------------------------------------------
# Hable tonemapping -- the engine's own curve, not an approximation
# ---------------------------------------------------------------------------

#: `E` and `F` in the Hable rational function. NOT authored: the engine
#: hard-codes them in `shaders/common/tonemapping.hlsl`, with the comment
#: "hard-coding these to defaults, since they are very hard to tune right".
HABLE_E = 0.01
HABLE_F = 0.3


def hable_curve(x, shoulder, linear, angle, toe):
    """`HableFunction` from `shaders/common/tonemapping.hlsl`, verbatim.

        ((x*(A*x + C*B) + D*E) / (x*(A*x + B) + D*F)) - E/F

    with `A` = shoulder strength, `B` = linear strength, `C` = linear angle,
    `D` = toe strength.
    """
    den = x * (shoulder * x + linear) + toe * HABLE_F
    if den == 0.0:
        return 0.0
    num = x * (shoulder * x + angle * linear) + toe * HABLE_E
    return num / den - HABLE_E / HABLE_F


def _mix(tree, blend, label=""):
    """One colour-math node, across both compositor APIs.

    Returns `(node, a_input, b_input, output)`.  Blender 5 dropped
    `CompositorNodeMixRGB` for the shader-side `ShaderNodeMix`, whose sockets
    sit at different indices.
    """
    node = _new_node(tree, "CompositorNodeMixRGB", "ShaderNodeMix")
    if node is None:
        return (None, None, None, None)
    node.label = label
    if hasattr(node, "data_type"):                   # ShaderNodeMix (Blender 5)
        node.data_type = "RGBA"
        node.blend_type = blend
        node.inputs[0].default_value = 1.0
        return node, node.inputs[6], node.inputs[7], node.outputs[2]
    node.blend_type = blend                          # CompositorNodeMixRGB
    node.inputs[0].default_value = 1.0
    return node, node.inputs[1], node.inputs[2], node.outputs[0]


def _const(socket, value):
    socket.default_value = (value, value, value, 1.0)


#: The engine's most common AUTHORED bloom preset, for a package that carries
#: no `effects.json` of its own.
#:
#: Bloom is a per-LEVEL `FullScreenEffectSettings` value, so a standalone model
#: package has none -- and with none, an emissive surface renders at exactly
#: its emissive value and stops. In the game the glow IS this pass; without it
#: a character's accent lines come out coloured but flat.
#:
#: These are not invented numbers, nor a median assembled field-by-field.
#: Across the 25 level effect resources with bloom active there are 8 distinct
#: presets, and `(magnitude 5.0, exposure_offset 6.0, blur_iterations 2,
#: hi_quality_spread 3.0)` is a single real preset shared by 6 of them -- the
#: modal one, and simultaneously the median of every individual field.
#:
#: ⚠ A level package must always prefer its OWN `effects.json`. This is the
#: fallback for a model imported on its own, where the honest answer is "the
#: most common way the game lights this" rather than a guess.
DEFAULT_BLOOM = {
    "enabled": 1,
    "active": True,
    "magnitude": 5.0,
    "exposure_offset": 6.0,
    "blur_iterations": 2,
    "hi_quality_spread": 3.0,
    "is_default_preset": True,
}


#: Exposure that goes with `DEFAULT_BLOOM`, and it is not a neutral 0.0.
#:
#: The bloom gain is `2**(exposure - exposure_offset) * magnitude`, so the
#: level's own exposure is part of the preset. All SIX levels that author the
#: modal bloom preset also author `exposure = -0.5` -- unanimous, not a
#: median -- which gives `2**(-0.5-6) * 5 = 0.0552`, i.e. the blurred image
#: added back at ~5.5%. Substituting a neutral 0.0 would overstate the glow by
#: a factor of 1.41.
DEFAULT_BLOOM_EXPOSURE = -0.5


def default_bloom_doc():
    """A minimal `effects.json`-shaped doc carrying `DEFAULT_BLOOM`.

    Only `apply_bloom` reads this -- the exposure here feeds the gain formula
    and is NOT applied to the scene's view settings, which would darken a
    model import for a reason its own package never asked for.
    """
    return {"bloom": dict(DEFAULT_BLOOM),
            "exposure": {"exposure": DEFAULT_BLOOM_EXPOSURE}}


#: Multiplier that leaves the authored bloom exactly as the engine grades it.
#: Any other value is a deliberate departure, which is why it is reported.
BLOOM_STRENGTH_AUTHORED = 1.0


def bloom_strength(value):
    """Coerce a caller's bloom multiplier to a usable one.

    Negatives are clamped to zero rather than passed through: a negative gain
    would SUBTRACT the blurred image, darkening every bright edge instead of
    haloing it -- a silently wrong render rather than a visibly absent effect.

    Nonsense falls back to the authored 1.0, never to 0.0. Defaulting a junk
    value to "no bloom" would look exactly like the bug this whole pass exists
    to fix, and would be blamed on the material.
    """
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return BLOOM_STRENGTH_AUTHORED


def apply_bloom(doc, scene, strength=BLOOM_STRENGTH_AUTHORED):
    """Add the engine's bloom to the compositor, BEFORE the tonemap.

    `strength` scales the final gain ONLY. It deliberately does not touch
    `magnitude`, `exposure_offset`, the octave count or the blur radii, so the
    bloom keeps the shape the level authored and only its amount changes --
    and `1.0` reproduces the engine exactly. The authored gain is returned
    alongside the scaled one so a caller can always report both.

    ## Why nothing glowed

    `CGFSEffectsResource` carries a `BloomParams` block and `evr_fx` has
    decoded it since the effects pass was written -- `mpl_combat_combustion`
    reports `magnitude 5.0, exposure_offset 6.0, blur_iterations 4`. Nothing
    ever consumed it. An emissive surface therefore rendered at exactly its
    emissive value and stopped there: in the engine what reads as "glowing" is
    this pass bleeding that value into its surroundings.

    ## The formula, which is the sidecar's own

        bloom  = blur(colour * 2^(exposure - exposure_offset)) * magnitude
        graded = ToneMap_Hable(colour + bloom)

    So bloom is ADDITIVE and lands before the curve, not composited over the
    graded image. On combustion the gain is `2^-6 * 5 = 0.078`, i.e. the blurred
    image at ~8% -- subtle by design, and it is what lifts an emissive surface
    off its background.

    ⚠ The BLUR SHAPE is the one approximation. The engine's iterated
    downsample-blur has no direct equivalent in Blender's compositor, so this
    sums `blur_iterations` octaves at `spread * 2**i` and divides by their
    count. That reproduces a pyramid's bright-core-plus-wide-halo without
    matching its exact falloff. The magnitude and the exposure offset are
    exact.
    """
    bloom = doc.get("bloom") or {}
    if not bloom or not bloom.get("enabled") or not bloom.get("active"):
        return {}
    magnitude = float(bloom.get("magnitude") or 0.0)
    if magnitude <= 0.0:
        return {"bloom": "magnitude is zero, not built"}
    offset = float(bloom.get("exposure_offset") or 0.0)
    iterations = int(bloom.get("blur_iterations") or 1)
    spread = float(bloom.get("hi_quality_spread") or 1.0)
    exposure = float((doc.get("exposure") or {}).get("exposure") or 0.0)
    authored_gain = (2.0 ** (exposure - offset)) * magnitude
    strength = bloom_strength(strength)
    gain = authored_gain * strength
    if gain <= 0.0:
        # A zero multiplier means "no bloom", which is an answer -- build
        # nothing rather than a chain that adds zero.
        return {"bloom": "strength is zero, not built"}

    tree, is_group = _compositor_tree(scene)
    if tree is None:
        return {"bloom": "no compositor available"}
    if any(n.label == "evr_bloom_add" for n in tree.nodes):
        return {"bloom": "already present"}

    rl = next((n for n in tree.nodes if n.type == "R_LAYERS"), None)
    if rl is None:
        rl = _new_node(tree, "CompositorNodeRLayers")
        if rl is None:
            return {"bloom": "no render-layer node type"}
        rl.location = (-600, 0)

    # after fog when there is fog: the engine fogs the HDR colour first, and
    # bloom blooms what the camera actually sees.
    fog_mix = next((n for n in tree.nodes if n.label == "evr_fog_mix"), None)
    if fog_mix is not None:
        source = (fog_mix.outputs[2] if hasattr(fog_mix, "data_type")
                  else fog_mix.outputs[0])
    else:
        source = rl.outputs.get("Image")
    if source is None:
        return {"bloom": "no image to bloom"}

    # ⭐ A PYRAMID, not one blur. The engine runs `blur_iterations` passes on
    # progressively downsampled buffers and sums them, which yields a bright
    # core plus a wide halo. One big gaussian of the same reach preserves total
    # energy but spreads it over the square of the radius, so the core vanishes:
    # measured, a single 75px blur moved the background by 0.001/255 even on
    # surfaces emitting at strength 8+. Summing octaves is what makes it read.
    radii = [max(1, int(round(spread * (2 ** i)))) for i in range(max(1, iterations))]
    octaves = []
    for level, radius in enumerate(radii):
        blur = _new_node(tree, "CompositorNodeBlur")
        if blur is None:
            return {"bloom": "no blur node type"}
        blur.label = "evr_bloom_blur%d" % level
        blur.location = (-320, -320 - level * 170)
        for attr, value in (("filter_type", "FAST_GAUSS"), ("size_x", radius),
                            ("size_y", radius), ("use_relative", False)):
            try:
                setattr(blur, attr, value)
            except (AttributeError, TypeError):
                pass
        tree.links.new(blur.inputs[0], source)
        octaves.append(blur.outputs[0])

    # average the octaves, so `magnitude` keeps meaning "this much bloom"
    accumulated = octaves[0]
    for level, out in enumerate(octaves[1:], start=1):
        node, ia, ib, io = _mix(tree, "ADD", "evr_bloom_sum%d" % level)
        if node is None:
            return {"bloom": "no mix node type"}
        node.location = (-140, -320 - level * 170)
        tree.links.new(ia, accumulated)
        tree.links.new(ib, out)
        accumulated = io

    scaled, sa, sb, sout = _mix(tree, "MULTIPLY", "evr_bloom_gain")
    if scaled is None:
        return {"bloom": "no mix node type"}
    scaled.location = (-40, -320)
    tree.links.new(sa, accumulated)
    _const(sb, gain / float(len(octaves)))

    added, aa, ab, aout = _mix(tree, "ADD", "evr_bloom_add")
    added.location = (160, -120)
    tree.links.new(aa, source)
    tree.links.new(ab, sout)
    # Terminate the chain. Without this the whole pass is inert wherever
    # `apply_tonemap` does not follow it -- see `_connect_to_output`.
    if not _connect_to_output(tree, is_group, aout):
        return {"bloom": "no compositor output node type"}

    return {"bloom": {"magnitude": magnitude, "exposure_offset": offset,
                      "gain": round(gain, 6),
                      "authored_gain": round(authored_gain, 6),
                      "strength": strength,
                      "is_authored": strength == BLOOM_STRENGTH_AUTHORED,
                      "octave_radii_px": radii,
                      "iterations": iterations, "spread": spread}}


def apply_tonemap(doc, scene):
    """Build the engine's exposure + Hable curve in the compositor.

    ## Why not `view_settings.view_transform`

    Blender ships no Hable transform, so this used to pick the nearest filmic
    look and record the real coefficients as custom properties -- an
    approximation, and the reason an import never matched the game's contrast.
    The curve is five authored numbers and a rational function, so it can simply
    be built instead of approximated.

    ## Why exposure moves into the compositor as well

    Blender's pipeline is `render -> compositor -> view transform`, and
    `view_settings.exposure` belongs to that LAST stage.  The engine does
    `ToneMap_Hable(colour * exposure)` -- exposure FIRST.  Leaving exposure on
    the view settings would apply it to already-tonemapped pixels, which is
    visibly wrong because the curve is steeply non-linear near black.

    So exposure becomes a multiply inside the graph, `view_settings.exposure` is
    zeroed, and the view transform is forced to `Standard` (a plain sRGB encode)
    so nothing tonemaps the image a second time.

    ## The denominator

    `ToneMap_Hable` divides by `k_hable_denominator`, a per-frame constant
    rather than an asset field: it is absent from `fullscreeneffects.radattr`,
    and `WhitePoint` is passed into the shader but never used there.  That is
    the signature of the standard Hable normalisation `f(x) / f(WhitePoint)`
    computed CPU-side, so it is computed the same way here.  It is what makes
    `WhitePoint` map to exactly 1.0 -- verified on `mpl_arena_a`, where
    `f(7.0) = 0.835879` and the normalised curve is monotonic with `f(0) = 0`
    and `f(7) = 1`.
    """
    tone = doc.get("tonemap") or {}
    if not tone:
        return {}
    a_shoulder = float(tone.get("shoulder_strength") or 0.0)
    b_linear = float(tone.get("linear_strength") or 0.0)
    c_angle = float(tone.get("linear_angle") or 0.0)
    d_toe = float(tone.get("toe_strength") or 0.0)
    white = float(tone.get("white_point") or 0.0)
    if a_shoulder <= 0.0 or b_linear <= 0.0 or white <= 0.0:
        return {"tonemap": "coefficients missing, not built"}
    denominator = hable_curve(white, a_shoulder, b_linear, c_angle, d_toe)
    if denominator <= 0.0:
        return {"tonemap": "degenerate curve, not built"}

    exposure = float((doc.get("exposure") or {}).get("exposure") or 0.0)
    gain = 2.0 ** exposure               # authored in stops, as Blender's is

    tree, is_group = _compositor_tree(scene)
    if tree is None:
        return {"tonemap": "no compositor available"}
    if any(n.label == "evr_hable" for n in tree.nodes):
        return {"tonemap": "already present"}

    rl = next((n for n in tree.nodes if n.type == "R_LAYERS"), None)
    if rl is None:
        rl = _new_node(tree, "CompositorNodeRLayers")
        if rl is None:
            return {"tonemap": "no render-layer node type"}
        rl.location = (-600, 0)

    # Chain onto the fog mix when there is one: the engine fogs the HDR colour
    # before tonemapping it, so fog has to come first.
    # Bloom first when present -- the engine tonemaps `colour + bloom`, so the
    # curve has to see the sum. Then fog, then the raw render.
    bloom_add = next((n for n in tree.nodes if n.label == "evr_bloom_add"), None)
    fog_mix = next((n for n in tree.nodes if n.label == "evr_fog_mix"), None)
    if bloom_add is not None:
        source = (bloom_add.outputs[2] if hasattr(bloom_add, "data_type")
                  else bloom_add.outputs[0])
    elif fog_mix is not None:
        source = (fog_mix.outputs[2] if hasattr(fog_mix, "data_type")
                  else fog_mix.outputs[0])
    else:
        source = rl.outputs.get("Image")
    if source is None:
        return {"tonemap": "no image to tonemap"}

    node, xa, xb, x = _mix(tree, "MULTIPLY", "evr_exposure")
    if node is None:
        return {"tonemap": "no mix node type"}
    node.location = (300, 200)
    _const(xb, gain)
    tree.links.new(xa, source)

    def stage(blend, left, right, name, location):
        made, in_a, in_b, out = _mix(tree, blend, name)
        made.location = location
        if isinstance(left, float):
            _const(in_a, left)
        else:
            tree.links.new(in_a, left)
        if isinstance(right, float):
            _const(in_b, right)
        else:
            tree.links.new(in_b, right)
        return out

    #  num = x*(A*x + C*B) + D*E          den = x*(A*x + B) + D*F
    ax = stage("MULTIPLY", x, a_shoulder, "evr_hable", (500, 320))
    ax_cb = stage("ADD", ax, c_angle * b_linear, "evr_hable_axcb", (700, 400))
    num_t = stage("MULTIPLY", x, ax_cb, "evr_hable_numt", (900, 400))
    num = stage("ADD", num_t, d_toe * HABLE_E, "evr_hable_num", (1100, 400))
    ax_b = stage("ADD", ax, b_linear, "evr_hable_axb", (700, 120))
    den_t = stage("MULTIPLY", x, ax_b, "evr_hable_dent", (900, 120))
    den = stage("ADD", den_t, d_toe * HABLE_F, "evr_hable_den", (1100, 120))
    quot = stage("DIVIDE", num, den, "evr_hable_div", (1300, 260))
    shift = stage("SUBTRACT", quot, HABLE_E / HABLE_F, "evr_hable_sub", (1500, 260))
    result = stage("MULTIPLY", shift, 1.0 / denominator, "evr_hable_norm", (1700, 260))

    if is_group:
        out_node = next((n for n in tree.nodes if n.type == "GROUP_OUTPUT"), None)
        if out_node is None:
            if not any(getattr(i, "in_out", "") == "OUTPUT"
                       for i in getattr(tree.interface, "items_tree", ())):
                tree.interface.new_socket(name="Image", in_out="OUTPUT",
                                          socket_type="NodeSocketColor")
            out_node = _new_node(tree, "NodeGroupOutput")
            if out_node is None:
                return {"tonemap": "no group-output node type"}
        out_node.location = (1950, 260)
        tree.links.new(out_node.inputs[0], result)
    else:
        comp = next((n for n in tree.nodes if n.type == "COMPOSITE"), None)
        if comp is None:
            comp = _new_node(tree, "CompositorNodeComposite")
            if comp is None:
                return {"tonemap": "no composite node type"}
        comp.location = (1950, 260)
        tree.links.new(comp.inputs["Image"], result)

    # Nothing downstream may expose or tonemap again.
    try:
        scene.view_settings.exposure = 0.0
        scene.view_settings.view_transform = "Standard"
    except (AttributeError, TypeError):
        pass
    scene["evr_tonemap_denominator"] = denominator
    scene["evr_tonemap_applied"] = (
        "Hable built in the compositor from this level's own coefficients; "
        "exposure applied BEFORE it, view transform forced to Standard so the "
        "image is not tonemapped twice")
    return {"tonemap": "built (Hable)", "exposure_stops": exposure,
            "gain": round(gain, 4), "denominator": round(denominator, 6),
            "coefficients": {"shoulder": a_shoulder, "linear": b_linear,
                             "angle": c_angle, "toe": d_toe, "white": white}}


def apply_particles(doc: dict, context, *, y_up_to_z_up: bool = True) -> dict:
    """One Empty per emitter placement, named for the effect it plays."""
    placements = [p for p in (doc.get("particles") or []) if p.get("position")]
    if not placements:
        return {"emitters": 0,
                "reason": "no emitter placements with a known transform"}
    collection = bpy.data.collections.new("EVR Particle Emitters")
    context.scene.collection.children.link(collection)
    built = 0
    for p in placements:
        empty = bpy.data.objects.new("evr_fx_%s" % p["effect"], None)
        empty.empty_display_type = "SPHERE"
        empty.empty_display_size = 0.35
        empty.location = _to_blender(p["position"], y_up_to_z_up)
        empty["evr_particle_effect"] = p["effect"]
        empty["evr_particle_actor"] = p["actor"]
        empty["evr_particle_note"] = (
            "PLACEMENT ONLY -- CGParticleEffectResource / "
            "CGParticleGraphResource are not decoded, so nothing here says "
            "what this emitter looks like")
        collection.objects.link(empty)
        built += 1
    return {"emitters": built,
            "effects": len({p["effect"] for p in placements}),
            "collection": collection.name}


def summarize(doc: dict) -> dict:
    fog = doc.get("fog") or {}
    return {
        "fog": ("default" if fog.get("is_default") else "authored") if fog else "none",
        "tonemap": bool(doc.get("tonemap")),
        "particles": len(doc.get("particles") or []),
    }
