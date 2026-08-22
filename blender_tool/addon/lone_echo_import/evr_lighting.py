"""Import an Echo VR package's `lightmaps.json`: baked atlases + placed lights.

Written by `scripts/evr_apply_lighting.py`, always beside `manifest.json`, so
the importer finds it without the user selecting anything.

Two independent halves, because they come from different places in the game
data and either can be present alone:

* **lights** -- `SGLightParams` from `CGSceneResource` section 1: type, colour,
  intensity, range and direction, all authored. `mpl_arena_a` carries 138 --
  2 directional, 26 spot, 110 point -- and its warm/cool team split is right
  there in the two directional lights, `(1.000, 0.583, 0.431)` against
  `(0.584, 0.820, 1.000)`. Only type >= 2 (SUN) enters the engine's dynamic
  shading list; POINT and SPOT are the static-bake rig, so importing them
  alongside the lightmap double-counts their contribution -- hence
  `dynamic_only`.

* **lightmaps** -- per-page diffuse irradiance, already collapsed out of the
  SG5/SH4 basis with the shader's own weights. The same warm/cool split shows
  up baked here, which is what those POINT and SPOT lights were baked into.

Wiring an atlas needs a lightmap UV layer on the mesh. When the package has no
such layer the atlases are still loaded and reported, and the reason is stated
plainly rather than silently doing nothing.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import bpy                                              # type: ignore
import mathutils                                        # type: ignore

SIDECAR_NAME = "lightmaps.json"
SIDECAR_FORMAT = "evr_lighting"

#: The importer's own name for the lightmap UV layer, matching what the engine
#: authoring path calls it (`custom_level_importer` creates "EchoLightmap").
LIGHTMAP_UV = "EchoLightmap"

#: Watts for an intensity-1.0 light. The records store a relative multiplier
#: with no unit, so this is a viewing default, not a decoded quantity.
DEFAULT_WATTS = 25.0
DEFAULT_RADIUS = 0.05


def sidecar_path(package) -> Path | None:
    """The `lightmaps.json` beside a package, or None."""
    p = Path(package)
    if p.is_file():
        p = p.parent
    candidate = p / SIDECAR_NAME
    return candidate if candidate.is_file() else None


def load(package) -> dict | None:
    """Parse the sidecar, or None when absent / not ours."""
    path = sidecar_path(package)
    if path is None:
        return None
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if doc.get("format") == SIDECAR_FORMAT else None


def _to_blender(vec, y_up_to_z_up: bool):
    x, y, z = vec
    return mathutils.Vector((x, -z, y) if y_up_to_z_up else (x, y, z))


#: `SGLightParams.type` names as the sidecar writes them.
_LIGHT_TYPES = {"POINT": "POINT", "SPOT": "SPOT", "SUN": "SUN"}


def import_lights(doc: dict, context, y_up_to_z_up: bool = True,
                  collection=None, scale: float = 1.0,
                  dynamic_only: bool = False) -> dict:
    """Build Blender lights from the level's `SGLightParams` records.

    Type, colour, intensity and range all come from the record. A SUN is aimed
    by its `direction`; point and spot lights use their position. `range` maps
    to Blender's custom distance, so falloff keeps roughly the authored reach.

    `dynamic_only` applies the engine's own gate -- only type >= 2 (SUN) lights
    enter the runtime list that shades dynamic objects; POINT and SPOT belong to
    the static bake and are already in the lightmap, so importing them too
    double-counts that light.
    """
    records = doc.get("lights") or []
    if not records:
        return {"created": 0, "reason": "no lights in sidecar"}

    target = collection or context.scene.collection
    created = 0
    skipped = 0
    for rec in records:
        if dynamic_only and not rec.get("shades_dynamic"):
            skipped += 1
            continue
        kind = _LIGHT_TYPES.get(str(rec.get("type", "POINT")).upper(), "POINT")
        data = bpy.data.lights.new(name=f"evr_{kind.lower()}", type=kind)
        try:
            data.color = tuple(float(c) for c in (rec.get("color") or [1, 1, 1])[:3])
        except (TypeError, ValueError):
            pass

        intensity = float(rec.get("intensity") or 1.0)
        if kind == "SUN":
            # A Blender sun is irradiance in W/m2, so the authored intensity
            # transfers directly rather than through the watt scale below.
            data.energy = max(intensity, 0.0)
        else:
            data.energy = DEFAULT_WATTS * max(intensity, 0.0)
            data.shadow_soft_size = DEFAULT_RADIUS
            reach = (rec.get("range") or [0.0])[0]
            if reach:
                data.use_custom_distance = True
                data.cutoff_distance = float(reach)

        obj = bpy.data.objects.new(data.name, data)
        obj.location = _to_blender(rec.get("position") or (0, 0, 0),
                                   y_up_to_z_up) * scale
        # A directional light has no meaningful position -- only its aim
        # matters, and the record gives that as a vector, not a quaternion.
        direction = rec.get("direction")
        if direction and len(direction) == 3 and any(direction):
            aim = _to_blender(direction, y_up_to_z_up)
            if aim.length > 1e-6:
                obj.rotation_euler = aim.to_track_quat("-Z", "Y").to_euler()

        obj["evr_light_type"] = kind
        obj["evr_light_intensity"] = intensity
        obj["evr_light_range"] = (rec.get("range") or [None])[0]
        obj["evr_shades_dynamic"] = bool(rec.get("shades_dynamic"))
        if rec.get("level"):
            obj["evr_level"] = rec["level"]

        target.objects.link(obj)
        created += 1
    out = {"created": created}
    if skipped:
        out["skipped_static"] = skipped
    return out


def _load_image(directory: Path, name: str, colorspace: str = "sRGB"):
    path = directory / name
    if not path.is_file():
        return None
    existing = bpy.data.images.get(name)
    if existing is not None:
        return existing
    image = bpy.data.images.load(str(path))
    image.name = name
    # `_write_page` encodes with the sRGB transfer curve, so decoding as sRGB
    # is its exact inverse and hands the shader linear irradiance (still scaled
    # by the page's `gain`, which `_wire_radiance` restores).
    try:
        image.colorspace_settings.name = colorspace
    except (AttributeError, TypeError):
        pass
    return image


def wire_lightmaps(doc: dict, package, objects_by_mesh: dict,
                   intensity: float = 1.0) -> dict:
    """Multiply each bound mesh's base colour by its lightmap page.

    `objects_by_mesh` maps a manifest mesh index to the Blender objects built
    from it. Meshes with no lightmap UV layer are counted and reported rather
    than wired against the wrong coordinates.
    """
    bindings = doc.get("meshes") or {}
    if not bindings:
        return {"wired": 0, "reason": "no mesh bindings in sidecar"}

    root = Path(package)
    if root.is_file():
        root = root.parent
    directory = root / (doc.get("dir") or "lightmaps")
    gains = _gain_table(doc)

    wired = missing_uv = missing_image = 0
    for key, binding in bindings.items():
        try:
            mesh_index = int(key)
        except (TypeError, ValueError):
            continue
        for obj in objects_by_mesh.get(mesh_index, ()):
            mesh = getattr(obj, "data", None)
            if mesh is None or not getattr(mesh, "uv_layers", None):
                continue
            if LIGHTMAP_UV not in mesh.uv_layers:
                missing_uv += 1
                continue
            image = _load_image(directory, binding.get("image", ""))
            if image is None:
                missing_image += 1
                continue
            for slot in obj.material_slots:
                if slot.material and _wire_radiance(
                        slot.material, image,
                        gains.get(binding.get("image", ""), 1.0), intensity):
                    wired += 1
    out = {"wired": wired}
    if missing_uv:
        out["missing_uv"] = missing_uv
        out["reason"] = (f"{missing_uv} bound mesh(es) have no '{LIGHTMAP_UV}' "
                         f"UV layer, so the atlas cannot be sampled")
    if missing_image:
        out["missing_image"] = missing_image
    return out


def _gain_table(doc: dict) -> dict:
    """`{atlas filename: gain}`.

    `evr_apply_lighting._write_page` divides each page by its own 99.5th
    percentile so an 8-bit PNG stays viewable, and records the divisor. The
    linear value is `stored * gain` -- so a reader that ignores `gain` gets
    every page scaled by a DIFFERENT amount. On `mpl_arena_a` the gains span
    0.231 to 1.0, a 4.3x brightness error between neighbouring pages.
    """
    images = doc.get("images") or {}
    gains = doc.get("gains") or {}
    table = {}
    for key, name in images.items():
        try:
            table[name] = float(gains.get(key, 1.0)) or 1.0
        except (TypeError, ValueError):
            table[name] = 1.0
    return table


def _emission_socket(principled):
    """The emission colour input, whatever this Blender calls it."""
    return (principled.inputs.get("Emission Color")
            or principled.inputs.get("Emission"))


def _wire_radiance(material, image, gain: float = 1.0,
                   intensity: float = 1.0) -> bool:
    """Wire the baked page as EMITTED RADIANCE: `emission = albedo * irradiance`.

    ⚠ Deliberately NOT base colour. The atlas holds baked *irradiance* -- light
    that already arrived at the surface. Multiplying it into base colour leaves
    it as a reflectance, so the renderer lights it AGAIN with whatever lamps the
    scene has. Echo VR bakes nearly everything and ships almost no dynamic
    lights (`mpl_arena_a`: 136 of 138 are static-bake, so 2 survive import), and
    the result is a scene that renders essentially black -- the reported
    "applying baked lights doesn't work properly".

    Feeding `albedo * irradiance` to emission reproduces the outgoing radiance
    the bake represents, which is what the engine displays. Base colour is left
    on the albedo texture, so the two surviving dynamic lights still shade the
    surface normally and nothing is double-counted.
    """
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return False
    principled = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled is None:
        return False
    emission = _emission_socket(principled)
    base = principled.inputs.get("Base Color")
    if emission is None or any(n.label == "evr_lightmap" for n in tree.nodes):
        return False

    tex = tree.nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.label = "evr_lightmap"
    tex.location = (principled.location.x - 900, principled.location.y - 420)

    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = LIGHTMAP_UV
    uv.location = (tex.location.x - 220, tex.location.y)
    tree.links.new(tex.inputs["Vector"], uv.outputs["UV"])

    light = tex.outputs["Color"]

    # Undo the per-page exposure divisor, so pages are mutually consistent.
    scale = float(gain) * float(intensity)
    if abs(scale - 1.0) > 1e-6:
        gain_node = tree.nodes.new("ShaderNodeMix")
        gain_node.data_type = "RGBA"
        gain_node.blend_type = "MULTIPLY"
        gain_node.label = "evr_lightmap_gain"
        gain_node.location = (tex.location.x + 220, tex.location.y)
        gain_node.inputs["Factor"].default_value = 1.0
        gain_node.inputs[7].default_value = (scale, scale, scale, 1.0)
        tree.links.new(gain_node.inputs[6], light)
        light = gain_node.outputs[2]

    mix = tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.label = "evr_lightmap_multiply"
    mix.location = (principled.location.x - 300, principled.location.y - 200)
    mix.inputs["Factor"].default_value = 1.0

    # Albedo feeds the multiply WITHOUT being unlinked from base colour.
    if base is not None and base.is_linked:
        tree.links.new(mix.inputs[6], base.links[0].from_socket)
    elif base is not None:
        mix.inputs[6].default_value = tuple(base.default_value)
    tree.links.new(mix.inputs[7], light)

    tree.links.new(emission, mix.outputs[2])
    strength = principled.inputs.get("Emission Strength")
    if strength is not None:
        strength.default_value = 1.0
    return True


#: Back-compat alias -- the old name multiplied into base colour.
_multiply_lightmap = _wire_radiance


def wire_instance_lightmaps(doc: dict, package, objects_by_instance: dict,
                            intensity: float = 1.0,
                            y_up_to_z_up: bool = True) -> dict:
    """Wire the PER-INSTANCE lightmap: static-instanced geometry.

    Instances of one mesh sit in different atlas regions, so each carries its
    own UV set. Honouring that means giving every lit instance its own mesh
    datablock -- the same cost the Lone Echo per-instance path pays, and the
    only way the atlas lands on the right pixels.
    """
    entries = doc.get("instances") or {}
    if not entries:
        return {"wired": 0, "reason": "no per-instance lightmap in sidecar"}

    root = Path(package)
    if root.is_file():
        root = root.parent
    blob_rel = doc.get("instance_uv_blob")
    blob_path = root / blob_rel if blob_rel else None
    if not blob_path or not blob_path.is_file():
        return {"wired": 0, "reason": f"missing UV blob ({blob_rel})"}
    raw = blob_path.read_bytes()

    import array
    uv = array.array("f")
    uv.frombytes(raw)

    directory = root / (doc.get("dir") or "lightmaps")
    gains = _gain_table(doc)
    sh_pages = doc.get("sh_pages") or {}
    wired = mismatched = sh_wired = already = 0
    for key, entry in entries.items():
        try:
            instance_index = int(key)
        except (TypeError, ValueError):
            continue
        for obj in objects_by_instance.get(instance_index, ()):
            mesh = getattr(obj, "data", None)
            if mesh is None:
                continue
            count = int(entry.get("uv_count") or 0)
            if count != len(mesh.vertices):
                # The UV run is per VERTEX; a length mismatch means this
                # instance's mesh is not the one the bake was made against.
                mismatched += 1
                continue
            # SH4 keeps four RAW coefficient slices; they are data, so they
            # load as Non-Color and are evaluated against the surface normal
            # rather than multiplied in directly.
            sh_entry = sh_pages.get(entry.get("page_key") or "")
            sh_images = None
            if sh_entry and sh_entry.get("basis") == "SH4":
                # The slices ship as BC6H DDS and Blender decodes them to
                # FLOAT -- 10332 distinct values per channel against 256 from an
                # 8-bit PNG, with HDR above 1.0 intact. Non-Color because these
                # are SH coefficients, not a picture.
                loaded = [_load_image(directory, n, "Non-Color")
                          for n in sh_entry.get("slices", ())]
                if len(loaded) == 4 and all(loaded):
                    sh_images = loaded

            image = _load_image(directory, entry.get("image", ""))
            if image is None and sh_images is None:
                continue

            mesh = mesh.copy()             # per-instance UVs break sharing
            obj.data = mesh
            layer = mesh.uv_layers.get(LIGHTMAP_UV) or mesh.uv_layers.new(
                name=LIGHTMAP_UV)
            start = int(entry.get("uv_offset") or 0) * 2
            for loop in mesh.loops:
                base = start + loop.vertex_index * 2
                if base + 1 < len(uv):
                    layer.data[loop.index].uv = (uv[base], uv[base + 1])

            obj["evr_lightmap_page"] = entry.get("page")
            for slot in obj.material_slots:
                if slot.material:
                    # Already wired (re-import over the same objects): copying
                    # here would duplicate the material and then fail to wire
                    # it, leaving orphans behind.
                    if any(n.label == "evr_lightmap"
                           for n in (slot.material.node_tree.nodes
                                     if slot.material.node_tree else ())):
                        already += 1
                        continue
                    material = slot.material.copy()
                    slot.material = material
                    if sh_images is not None:
                        if _wire_sh4(material, sh_images, intensity,
                                     y_up_to_z_up):
                            wired += 1
                            sh_wired += 1
                    elif image is not None and _wire_radiance(
                            material, image,
                            gains.get(entry.get("image", ""), 1.0), intensity):
                        wired += 1
    out = {"wired": wired}
    if sh_wired:
        out["sh4_evaluated"] = sh_wired
    if already and not wired:
        out["reason"] = ("%d material(s) were already wired -- these objects "
                         "have had a lightmap applied before" % already)
    if mismatched:
        out["mismatched"] = mismatched
    return out


def summarize(doc: dict) -> dict:
    """Counts for the operator's report, without touching the scene."""
    return {
        "lights": len(doc.get("lights") or []),
        "atlases": len(doc.get("images") or {}),
        "bound_meshes": len(doc.get("meshes") or {}),
        "bound_instances": len(doc.get("instances") or {}),
    }


# ---------------------------------------------------------------------------
# SH4 -- the shipped world-space basis (mpl_arena_a and the tutorial maps)
# ---------------------------------------------------------------------------
#
# Transcribed from the engine's own HLSL, not inferred from appearance:
#
#   core/shaders/materials/material_base_ps.hlsl:1129   sampling + unpack
#   core/shaders/common/sh.hlsl:174                     EvalSH4IrradianceGeomerics
#
# The four slices of a page are L1 spherical-harmonic coefficients of incoming
# radiance, baked in WORLD space. Irradiance therefore depends on the surface
# normal and CANNOT be resolved into a single image ahead of shading -- which is
# exactly what the old collapse did (it kept slice 0 and dropped all three
# directional terms), and why lit surfaces came out flat and unlike the game.
#
#   sh[i] = texel                                  i == 0 is the DC, used as-is
#   sh[i] = (sh[i] * 2 - 1) * (sh[0] * 2)          for i > 0
#
#   r1         = 0.5 * (sh[3], sh[1], sh[2])
#   q          = 0.5 * (1 + dot(normalize(r1), n))
#   p          = 1 + 2 * |r1| / sh[0]
#   a          = (1 - |r1| / sh[0]) / (1 + |r1| / sh[0])
#   irradiance = sh[0] * (a + (1 - a) * (p + 1) * pow(|q|, p))
#
#   diffuse    = irradiance * albedo / Pi
#
# Both the `*2-1` unpack and the rescale by `dc*2` are load-bearing.
SH4_GROUP_NAME = "EVR_SH4_Irradiance"


def _sh4_node_group():
    """Build (or fetch) the node group evaluating SH4 irradiance."""
    existing = bpy.data.node_groups.get(SH4_GROUP_NAME)
    if existing is not None:
        return existing

    group = bpy.data.node_groups.new(SH4_GROUP_NAME, "ShaderNodeTree")
    iface = group.interface
    for name in ("sh0", "sh1", "sh2", "sh3"):
        iface.new_socket(name=name, in_out="INPUT", socket_type="NodeSocketColor")
    iface.new_socket(name="Normal", in_out="INPUT", socket_type="NodeSocketVector")
    iface.new_socket(name="Irradiance", in_out="OUTPUT",
                     socket_type="NodeSocketColor")

    nodes, links = group.nodes, group.links
    gin = nodes.new("NodeGroupInput")
    gin.location = (-1600, 0)
    gout = nodes.new("NodeGroupOutput")
    gout.location = (1100, 0)

    def math(op, a=None, b=None, c=None, loc=(0, 0)):
        n = nodes.new("ShaderNodeMath")
        n.operation = op
        n.location = loc
        for i, v in enumerate((a, b, c)):
            if v is None:
                continue
            if hasattr(v, "node"):
                links.new(n.inputs[i], v)
            else:
                n.inputs[i].default_value = v
        return n.outputs[0]

    def vmath(op, a=None, b=None, scale=None, loc=(0, 0), out=0):
        n = nodes.new("ShaderNodeVectorMath")
        n.operation = op
        n.location = loc
        if a is not None:
            links.new(n.inputs[0], a)
        if b is not None:
            links.new(n.inputs[1], b)
        if scale is not None:
            n.inputs[3].default_value = scale
        return n.outputs[out]

    sep = []
    for i in range(4):
        n = nodes.new("ShaderNodeSeparateColor")
        n.location = (-1400, 400 - i * 200)
        links.new(n.inputs[0], gin.outputs[i])
        sep.append(n)

    normal = gin.outputs[4]
    chan_out = []
    for c in range(3):                     # R, G, B each carry their own SH set
        y = 600 - c * 1000
        dc = sep[0].outputs[c]
        dc2 = math("MULTIPLY", dc, 2.0, loc=(-1150, y))

        unpacked = []
        for i in (1, 2, 3):
            t = math("MULTIPLY_ADD", sep[i].outputs[c], 2.0, -1.0,
                     loc=(-1150, y - 130 * i))
            unpacked.append(math("MULTIPLY", t, dc2, loc=(-960, y - 130 * i)))

        # r1 = 0.5 * (sh3, sh1, sh2) -- the shader's swizzle, kept verbatim
        comb = nodes.new("ShaderNodeCombineXYZ")
        comb.location = (-780, y - 260)
        links.new(comb.inputs[0], unpacked[2])
        links.new(comb.inputs[1], unpacked[0])
        links.new(comb.inputs[2], unpacked[1])
        r1 = vmath("SCALE", comb.outputs[0], scale=0.5, loc=(-600, y - 260))

        lenr1 = vmath("LENGTH", r1, loc=(-420, y - 400), out=1)
        ndir = vmath("NORMALIZE", r1, loc=(-420, y - 260))
        dotnd = vmath("DOT_PRODUCT", ndir, normal, loc=(-240, y - 260), out=1)

        q = math("MULTIPLY_ADD", dotnd, 0.5, 0.5, loc=(-60, y - 260))
        # A zero DC divides to 0 in Blender, giving p=1, a=1 and irradiance
        # 0 * (...) = 0 -- the shader's `if(sh[0] > 0)` guard, for free.
        ratio = math("DIVIDE", lenr1, dc, loc=(-60, y - 400))
        pexp = math("MULTIPLY_ADD", ratio, 2.0, 1.0, loc=(120, y - 400))
        a = math("DIVIDE",
                 math("SUBTRACT", 1.0, ratio, loc=(120, y - 520)),
                 math("ADD", 1.0, ratio, loc=(120, y - 640)),
                 loc=(300, y - 560))
        qp = math("POWER", math("ABSOLUTE", q, loc=(120, y - 260)), pexp,
                  loc=(300, y - 260))
        term = math("MULTIPLY",
                    math("MULTIPLY",
                         math("SUBTRACT", 1.0, a, loc=(480, y - 560)),
                         math("ADD", pexp, 1.0, loc=(480, y - 680)),
                         loc=(660, y - 600)),
                    qp, loc=(660, y - 400))
        irr = math("MULTIPLY", dc,
                   math("ADD", a, term, loc=(840, y - 500)),
                   loc=(900, y - 300))
        chan_out.append(math("MAXIMUM", irr, 0.0, loc=(960, y - 300)))

    out = nodes.new("ShaderNodeCombineColor")
    out.location = (1000, 0)
    for c in range(3):
        links.new(out.inputs[c], chan_out[c])
    links.new(gout.inputs[0], out.outputs[0])
    return group


def _wire_sh4(material, slice_images, intensity: float = 1.0,
              y_up_to_z_up: bool = True) -> bool:
    """Wire a page's four SH4 slices as `irradiance * albedo / Pi` -> emission.

    `slice_images` is the four loaded images in slice order.
    """
    tree = getattr(material, "node_tree", None)
    if tree is None or len(slice_images) != 4:
        return False
    principled = next((n for n in tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled is None:
        return False
    emission = _emission_socket(principled)
    if emission is None or any(n.label == "evr_lightmap" for n in tree.nodes):
        return False
    base = principled.inputs.get("Base Color")

    uv = tree.nodes.new("ShaderNodeUVMap")
    uv.uv_map = LIGHTMAP_UV
    uv.location = (principled.location.x - 2000, principled.location.y - 400)

    tex_nodes = []
    for i, image in enumerate(slice_images):
        tex = tree.nodes.new("ShaderNodeTexImage")
        tex.image = image
        tex.label = "evr_lightmap" if i == 0 else "evr_lightmap_sh%d" % i
        tex.location = (principled.location.x - 1800,
                        principled.location.y - 200 - i * 300)
        tree.links.new(tex.inputs["Vector"], uv.outputs["UV"])
        tex_nodes.append(tex)

    group_node = tree.nodes.new("ShaderNodeGroup")
    group_node.node_tree = _sh4_node_group()
    group_node.label = "evr_sh4"
    group_node.location = (principled.location.x - 1200,
                           principled.location.y - 300)
    for i, tex in enumerate(tex_nodes):
        tree.links.new(group_node.inputs[i], tex.outputs["Color"])

    geo = tree.nodes.new("ShaderNodeNewGeometry")
    geo.location = (principled.location.x - 1600, principled.location.y - 900)
    normal_socket = geo.outputs["Normal"]

    # ⚠ SPACE MISMATCH, and it is not cosmetic.
    #
    # `material_base_ps.hlsl:1130` says the SH4 coefficients are baked "in
    # world-space" -- the GAME's world. `ShaderNodeNewGeometry.Normal` is
    # Blender's world normal, and the import stands the scene upright with a
    # +90 deg rotation about X (`mesh_builder`), so the two frames differ:
    #
    #     game (x, y, z)  ->  blender (x, -z, y)
    #     blender (x, y, z) -> game (x, z, -y)          <- the inverse, used here
    #
    # Feeding Blender's normal straight in evaluates the harmonic along the
    # wrong axis: a floor faces +Z in Blender but +Y in game, so it was being
    # lit as though it faced a horizontal direction. The irradiance stays
    # smooth and plausible, which is exactly why it survives a numeric check of
    # the evaluator -- `EvalSH4IrradianceGeomerics` was verified to 2.4e-07 and
    # was never the problem; its INPUT was.
    if y_up_to_z_up:
        sep = tree.nodes.new("ShaderNodeSeparateXYZ")
        sep.location = (principled.location.x - 1450, principled.location.y - 900)
        tree.links.new(sep.inputs[0], normal_socket)
        neg = tree.nodes.new("ShaderNodeMath")
        neg.operation = "MULTIPLY"
        neg.label = "-Y"
        neg.location = (principled.location.x - 1300, principled.location.y - 1000)
        tree.links.new(neg.inputs[0], sep.outputs["Y"])
        neg.inputs[1].default_value = -1.0
        comb = tree.nodes.new("ShaderNodeCombineXYZ")
        comb.label = "blender normal -> GAME space"
        comb.location = (principled.location.x - 1400, principled.location.y - 800)
        tree.links.new(comb.inputs["X"], sep.outputs["X"])
        tree.links.new(comb.inputs["Y"], sep.outputs["Z"])
        tree.links.new(comb.inputs["Z"], neg.outputs[0])
        normal_socket = comb.outputs[0]

    tree.links.new(group_node.inputs[4], normal_socket)

    mix = tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.label = "evr_lightmap_multiply"
    mix.location = (principled.location.x - 400, principled.location.y - 300)
    mix.inputs["Factor"].default_value = 1.0
    if base is not None and base.is_linked:
        tree.links.new(mix.inputs[6], base.links[0].from_socket)
    elif base is not None:
        mix.inputs[6].default_value = tuple(base.default_value)
    tree.links.new(mix.inputs[7], group_node.outputs[0])

    tree.links.new(emission, mix.outputs[2])
    strength = principled.inputs.get("Emission Strength")
    if strength is not None:
        # The shader's `k1_Pi`: EvalSH4IrradianceGeomerics returns irradiance,
        # and diffuse reflectance divides it by Pi.
        strength.default_value = float(intensity) / 3.14159265358979
    return True
