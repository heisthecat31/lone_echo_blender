"""Flowmap distortion -- the moving water.

## What this is for

`mpl_combat_combustion`'s water reads a `flowmap` channel that the importer had
no handling for at all, so the surface imported static. The texture
(`bb76c06eff9d0e2a`, 1024x1024, mean 0.5 / 0.5 / 0.99) is a tangent-space
direction field: R and G encode a 2D flow vector around the 0.5 midpoint, and
B is unused.

A flowmap is not a UV scroll. Scrolling slides the whole texture in one
direction; a flowmap pushes each texel along its OWN direction, so the surface
swirls where the field swirls. The importer's existing `_drive_uv_scroll`
cannot express that, which is why this is a separate pass.

## The graph it builds

The standard two-phase flow, which is what makes the effect loop without
visibly stretching:

    t       = frame / fps * rate            (a driver, as `_drive_uv_scroll` uses)
    phase0  = fract(t)
    phase1  = fract(t + 0.5)
    flow    = (flowmap.rg - 0.5) * 2 * strength
    uvA     = uv - flow * phase0
    uvB     = uv - flow * phase1
    blend   = abs(1 - 2 * phase0)
    colour  = mix(sample(uvA), sample(uvB), blend)

Each phase is a texture sample that drifts, resets, and is cross-faded against
its half-cycle-offset twin, so the reset is always hidden behind the other
phase's midpoint. That costs one extra sample of the distorted texture.

## ⚠ The rate and strength are NOT in the shipped data

Searched for, exhaustively, and absent. This is a measured negative, not an
assumption:

  * **The material carries nothing.** `d438877910a8a281` (the water) reports
    `materialprops: 0` -- no property words at all -- and its only two
    `auxillaryinputs` are the engine-global `cutting_cut_decal` and
    `cutting_scorch_decal`, both with a plain `(1.0, 1.0)` uv scale.
  * **No material anywhere uses `uvscrollspeed`.** The name DOES appear as a
    string in the water's shader set, which looks promising until you hash it
    (`3d5396a2b8446926`) and check: **0 of 1713** corpus materials bind that
    key. It belongs to the decal path every shader includes, not to the flow.
  * **The shader sets carry no reflection.** They embed DXIL (LLVM bitcode),
    not classic DXBC, so there is no RDEF chunk with a variable's default
    value to read.
  * **`CScriptCR` has no floats at all** -- checked field by field over 1141
    records; see `evr_script`.
  * **A corpus-wide key search finds nothing.** 172 candidate names
    (`flowspeed`, `flowstrength`, `flowscale`, `distortionstrength`,
    `wavespeed`, ...) hashed to CSymbol64 and searched as 8-byte keys across
    **all 68,602 files** in the extract: zero hits.

So the values are compiled into the shader's DXIL and would need bitcode
disassembly to recover. The defaults below are a presentation choice in the
conventional range for this technique, exposed as import options. Treat the
speed the same way `evr_movers` asks you to treat mover timing.

## ⚠ Atlas-packed materials are skipped

See the guard in `apply_flowmap`. A displacement in texture space is only safe
when the material owns the whole texture; where it reads a packed band, the
offset samples its neighbours instead.

⭐ One thing the shader set DID confirm: its vertex input signature lists
`COLOR`, which is the per-vertex lane `evr_vertex_color` decodes as the tint.
"""

from __future__ import annotations

from pathlib import Path

import bpy   # type: ignore

#: How fast the flow advances, in flow-cycles per second. NOT decoded.
FLOW_RATE_DEFAULT = 0.15
#: How far a texel is pushed, in UV units at full flow. NOT decoded.
#: 0.15 rather than something smaller because the surfaces this runs on are
#: LOW CONTRAST -- combustion's emissive facet map spans roughly 0.33..0.42, so
#: a displacement that would be obvious on a detailed texture is nearly
#: invisible here. Measured on a lit plane, frame 1 vs 12 at rate 1.0:
#: strength 0.05 moves the image by 0.01/255 mean, 0.3 by 0.16/255 mean.
FLOW_STRENGTH_DEFAULT = 0.15

#: Material custom property the extractor writes when a flowmap is bound.
FLOWMAP_PROP = "le_tex_flowmap"


def _load_image(pkg_dir: Path, texture_hash: str):
    name = "%s.dds" % texture_hash
    existing = bpy.data.images.get(name)
    if existing is not None:
        return existing
    path = pkg_dir / "textures" / name
    if not path.is_file():
        return None
    try:
        image = bpy.data.images.load(str(path))
    except RuntimeError:
        return None
    image.name = name
    try:
        image.colorspace_settings.name = "Non-Color"
    except (AttributeError, TypeError):
        pass
    return image


def _time_value(tree, rate: float, x: int, y: int):
    """A Value node carrying `frame / fps * rate`, driven by the scene clock."""
    node = tree.nodes.new("ShaderNodeValue")
    node.label = "flow time"
    node.location = (x, y)
    try:
        fcurve = node.outputs[0].driver_add("default_value")
    except (RuntimeError, TypeError):
        node.outputs[0].default_value = 0.0
        return node
    driver = fcurve.driver
    driver.type = "SCRIPTED"
    for existing in list(driver.variables):
        driver.variables.remove(existing)
    var = driver.variables.new()
    var.name = "f"
    var.targets[0].id_type = "SCENE"
    var.targets[0].id = bpy.context.scene
    var.targets[0].data_path = "frame_current"
    fps = getattr(getattr(bpy.context.scene, "render", None), "fps", 24) or 24
    driver.expression = "f / %d * %r" % (int(fps), float(rate))
    return node


def _math(tree, operation, x, y, value1=None, label=""):
    node = tree.nodes.new("ShaderNodeMath")
    node.operation = operation
    node.location = (x, y)
    if label:
        node.label = label
    if value1 is not None:
        node.inputs[1].default_value = value1
    return node


#: A Mapping scale below this on either axis means the material reads a
#: SUB-RECTANGLE of a shared texture rather than the whole of its own.
PACKED_UV_SCALE = 1.0 - 1e-6


def _packed_scale(target):
    """The Mapping scale feeding `target`, if it reads a packed sub-rectangle.

    Returns `(x, y)` when the texture is atlas-packed and `None` when the
    material owns the whole texture (scale >= 1 on both axes, i.e. the texture
    is used fully or tiled, both of which survive a UV offset).
    """
    socket = target.inputs.get("Vector")
    for _hop in range(4):
        if socket is None or not socket.is_linked:
            return None
        node = socket.links[0].from_node
        if node.type == "MAPPING":
            scale = node.inputs.get("Scale")
            if scale is None:
                return None
            value = tuple(scale.default_value)[:2]
            if any(v < PACKED_UV_SCALE for v in value):
                return value
            return None
        socket = node.inputs.get("Vector")
    return None


def apply_flowmap(material, pkg_dir: Path, rate: float, strength: float) -> bool:
    """Re-sample `material`'s lit texture through its flowmap. True if changed."""
    texture_hash = material.get(FLOWMAP_PROP)
    tree = getattr(material, "node_tree", None)
    if not texture_hash or tree is None:
        return False
    flow_image = _load_image(Path(pkg_dir), str(texture_hash))
    if flow_image is None:
        return False

    bsdf = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return False
    # ⛔ EMISSION ONLY. An earlier version fell back to Base Color when the
    # emission chain did not end in a texture, which meant most materials had
    # their ALBEDO distorted -- a flowmap material is emissive-driven (the
    # combustion water binds no albedo at all), so a base-colour hit means the
    # target was not found, not that the albedo should flow.
    target = None
    socket = bsdf.inputs.get("Emission Color")
    if socket is not None and socket.is_linked:
        node = socket.links[0].from_node
        # step back through tint/glow multiplies that sit in the way
        for _hop in range(4):
            if node.type == "MIX" and node.inputs[6].is_linked:
                node = node.inputs[6].links[0].from_node
                continue
            break
        if node.type == "TEX_IMAGE" and node.image is not None:
            target = node
    if target is None:
        return False

    # ⛔ Refuse ATLAS-PACKED targets. A flow offset is a displacement in
    # TEXTURE space, so on a material that reads only a sub-rectangle of a
    # shared texture it walks the sample straight out of that rectangle and
    # into unrelated art -- or, past the edge under EXTEND, smears one row of
    # texels into long spikes. `mpl_arena_a` i3768 is the case that found this:
    # emissive `a17ad278ff960e2f` is shared by six materials at four different
    # scales, i3768 reads it at (0.5, 0.5), and 0.15 UV units of push is 30% of
    # its band. `mpl_combat_combustion`'s water -- the surface this pass exists
    # for -- carries NO uv scale at all and is unaffected.
    #
    # Wrapping the offset back inside the band would need the rectangle's
    # ORIGIN as well, and the material spec carries a `uv_scale` with no
    # matching offset, so the band cannot be reconstructed. Skipping is the
    # honest answer until it can.
    packed = _packed_scale(target)
    if packed is not None:
        return False

    # ⛔ Capture who reads this texture BEFORE touching the graph. Blender
    # invalidates NodeLink references as soon as links are added, so a list of
    # link objects collected earlier and used later silently does nothing --
    # which left the cross-fade built but unread, and the surface static.
    # ⛔ Compare nodes by NAME, never with `is`. Every attribute access on a
    # bpy collection hands back a FRESH Python wrapper, so `link.from_node is
    # target` is False even when they are the same node. Two earlier versions
    # of this rewire used `is` and silently linked nothing: the cross-fade was
    # built, the graph looked right in a link dump, and the surface stayed
    # static because the BSDF still read the undistorted sample.
    #
    # The socket is located by walking each node's inputs and asking which is
    # fed by `target`; `list(inputs).index(socket)` is no good either, for the
    # same wrapper-identity reason, and a Mix node has ten inputs of which four
    # are named "A".
    target_name = target.name
    consumers = []
    for node in tree.nodes:
        for socket_index, socket in enumerate(node.inputs):
            # only the COLOR output moves to the cross-fade -- a consumer
            # reading the texture's ALPHA must keep reading alpha, not be
            # handed a colour.
            if socket.is_linked and any(l.from_node.name == target_name
                                        and l.from_socket.name == "Color"
                                        for l in socket.links):
                consumers.append((node.name, socket_index))

    ox, oy = target.location

    # ⛔ Do NOT replace whatever already drives the texture's Vector input.
    # An earlier version linked a bare UV Map node there, which silently threw
    # away the Mapping node carrying the material's `uv_scale` -- every
    # atlas-shared texture then sampled the wrong band of its atlas, and a band
    # that happens to be empty renders BLACK. The flow offset is built on top
    # of the existing source instead, and a UV Map node is only created when
    # there was nothing there to begin with.
    vector_in = target.inputs.get("Vector")
    if vector_in is not None and vector_in.is_linked:
        uv_source = vector_in.links[0].from_socket
    else:
        uv = tree.nodes.new("ShaderNodeUVMap")
        uv.location = (ox - 1500, oy)
        uv_source = uv.outputs["UV"]

    flow_tex = tree.nodes.new("ShaderNodeTexImage")
    flow_tex.image = flow_image
    flow_tex.label = "flowmap"
    flow_tex.location = (ox - 1500, oy - 320)
    tree.links.new(uv_source, flow_tex.inputs["Vector"])

    # (rgb - 0.5) * 2 * strength  ->  a signed push in UV units
    centre = tree.nodes.new("ShaderNodeVectorMath")
    centre.operation = "SUBTRACT"
    centre.location = (ox - 1200, oy - 320)
    centre.inputs[1].default_value = (0.5, 0.5, 0.5)
    tree.links.new(flow_tex.outputs["Color"], centre.inputs[0])

    scale = tree.nodes.new("ShaderNodeVectorMath")
    scale.operation = "SCALE"
    scale.label = "flow strength"
    scale.location = (ox - 1020, oy - 320)
    scale.inputs["Scale"].default_value = 2.0 * float(strength)
    tree.links.new(centre.outputs["Vector"], scale.inputs[0])

    time = _time_value(tree, rate, ox - 1500, oy - 620)
    phase0 = _math(tree, "FRACT", ox - 1320, oy - 620, label="phase 0")
    tree.links.new(time.outputs[0], phase0.inputs[0])
    shifted = _math(tree, "ADD", ox - 1320, oy - 780, value1=0.5)
    tree.links.new(time.outputs[0], shifted.inputs[0])
    phase1 = _math(tree, "FRACT", ox - 1140, oy - 780, label="phase 1")
    tree.links.new(shifted.outputs[0], phase1.inputs[0])

    samples = []
    for index, phase in enumerate((phase0, phase1)):
        push = tree.nodes.new("ShaderNodeVectorMath")
        push.operation = "SCALE"
        push.location = (ox - 840, oy - 320 - index * 200)
        tree.links.new(scale.outputs["Vector"], push.inputs[0])
        tree.links.new(phase.outputs[0], push.inputs["Scale"])

        offset = tree.nodes.new("ShaderNodeVectorMath")
        offset.operation = "SUBTRACT"
        offset.label = "uv - flow * phase%d" % index
        offset.location = (ox - 660, oy - 320 - index * 200)
        tree.links.new(uv_source, offset.inputs[0])
        tree.links.new(push.outputs["Vector"], offset.inputs[1])

        sample = target if index == 0 else target.id_data.nodes.new("ShaderNodeTexImage")
        if index:
            sample.image = target.image
            sample.interpolation = target.interpolation
            sample.extension = target.extension
            try:
                sample.image.colorspace_settings.name = \
                    target.image.colorspace_settings.name
            except (AttributeError, TypeError):
                pass
            sample.location = (ox, oy - 340)
        sample.label = "flow phase %d" % index
        tree.links.new(offset.outputs["Vector"], sample.inputs["Vector"])
        samples.append(sample)

    # blend = abs(1 - 2 * phase0): 0 at each phase's own midpoint, 1 at its reset
    doubled = _math(tree, "MULTIPLY", ox - 960, oy - 620, value1=2.0)
    tree.links.new(phase0.outputs[0], doubled.inputs[0])
    inverted = _math(tree, "SUBTRACT", ox - 780, oy - 620)
    inverted.inputs[0].default_value = 1.0
    tree.links.new(doubled.outputs[0], inverted.inputs[1])
    blend = _math(tree, "ABSOLUTE", ox - 600, oy - 620, label="phase blend")
    tree.links.new(inverted.outputs[0], blend.inputs[0])

    mix = tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MIX"
    mix.label = "flow cross-fade"
    mix.location = (ox + 200, oy - 160)
    tree.links.new(blend.outputs[0], mix.inputs["Factor"])
    tree.links.new(samples[0].outputs["Color"], mix.inputs[6])
    tree.links.new(samples[1].outputs["Color"], mix.inputs[7])

    # everything that read the original sample now reads the cross-fade
    for node_name, socket_index in consumers:
        if node_name == mix.name:
            continue
        node = tree.nodes.get(node_name)
        if node is None or socket_index >= len(node.inputs):
            continue
        try:
            tree.links.new(mix.outputs[2], node.inputs[socket_index])
        except (IndexError, RuntimeError, TypeError):
            continue

    material["le_flowmap_rate"] = float(rate)
    material["le_flowmap_strength"] = float(strength)
    return True


def apply_to_objects(objects, pkg_dir, rate: float = FLOW_RATE_DEFAULT,
                     strength: float = FLOW_STRENGTH_DEFAULT) -> dict:
    """Run `apply_flowmap` over every distinct material these objects use."""
    done: dict = {}
    applied = 0
    for obj in objects:
        for slot in getattr(obj, "material_slots", ()):
            material = slot.material
            if material is None or not material.get(FLOWMAP_PROP):
                continue
            if material.name in done:
                continue
            done[material.name] = apply_flowmap(material, Path(pkg_dir),
                                                rate, strength)
            if done[material.name]:
                applied += 1
    return {"materials": applied, "considered": len(done)}
