"""Build a Principled BSDF material from a .lemesh material spec.

Targets Blender 4.x / 5.x. Handles missing texture files (M3 not yet extracted)
gracefully: the material is still created with scalar defaults and the intended
texture hash recorded as a custom property, so nothing is silently lost.
"""

from __future__ import annotations

from pathlib import Path

import bpy   # type: ignore


def _principled_input(node, *names):
    for n in names:
        if n in node.inputs:
            return node.inputs[n]
    return None


def _load_image(pkg_dir: Path, rel_file: str, colorspace: str):
    if not rel_file:
        return None
    path = pkg_dir / rel_file
    if not path.exists():
        return None
    try:
        img = bpy.data.images.load(str(path), check_existing=True)
        try:
            img.colorspace_settings.name = colorspace
        except Exception:
            pass
        return img
    except Exception:
        return None


def _tex_node(nt, img, colorspace, x, y):
    node = nt.nodes.new("ShaderNodeTexImage")
    node.image = img
    node.location = (x, y)
    try:
        node.image.colorspace_settings.name = colorspace
    except Exception:
        pass
    return node


def _normal_chain(nt, tex_node, reconstruct_z, x, y):
    """Return a socket carrying a tangent-space normal for the Normal Map node."""
    if not reconstruct_z:
        return tex_node.outputs["Color"]
    # BC5 stores XY in RG; reconstruct Z = sqrt(1 - (2X-1)^2 - (2Y-1)^2).
    sep = nt.nodes.new("ShaderNodeSeparateColor"); sep.location = (x, y)
    nt.links.new(tex_node.outputs["Color"], sep.inputs["Color"])
    # remap X,Y from [0,1] to [-1,1], square, sum, 1-sum, sqrt, remap back
    def _mul_add(inp, mul, add, yy):
        m = nt.nodes.new("ShaderNodeMath"); m.operation = "MULTIPLY_ADD"
        m.location = (x + 160, yy); m.inputs[1].default_value = mul
        m.inputs[2].default_value = add
        nt.links.new(inp, m.inputs[0]); return m
    xr = _mul_add(sep.outputs[0], 2.0, -1.0, y + 60)
    yr = _mul_add(sep.outputs[1], 2.0, -1.0, y - 60)
    xsq = nt.nodes.new("ShaderNodeMath"); xsq.operation = "POWER"; xsq.location = (x + 320, y + 60)
    xsq.inputs[1].default_value = 2.0; nt.links.new(xr.outputs[0], xsq.inputs[0])
    ysq = nt.nodes.new("ShaderNodeMath"); ysq.operation = "POWER"; ysq.location = (x + 320, y - 60)
    ysq.inputs[1].default_value = 2.0; nt.links.new(yr.outputs[0], ysq.inputs[0])
    ssum = nt.nodes.new("ShaderNodeMath"); ssum.operation = "ADD"; ssum.location = (x + 480, y)
    nt.links.new(xsq.outputs[0], ssum.inputs[0]); nt.links.new(ysq.outputs[0], ssum.inputs[1])
    inv = nt.nodes.new("ShaderNodeMath"); inv.operation = "SUBTRACT"; inv.location = (x + 640, y)
    inv.inputs[0].default_value = 1.0; nt.links.new(ssum.outputs[0], inv.inputs[1])
    zsqrt = nt.nodes.new("ShaderNodeMath"); zsqrt.operation = "SQRT"; zsqrt.location = (x + 800, y)
    nt.links.new(inv.outputs[0], zsqrt.inputs[0])
    zr = nt.nodes.new("ShaderNodeMath"); zr.operation = "MULTIPLY_ADD"; zr.location = (x + 960, y)
    zr.inputs[1].default_value = 0.5; zr.inputs[2].default_value = 0.5
    nt.links.new(zsqrt.outputs[0], zr.inputs[0])
    # Normal Map node does its own *2-1 remap, so feed it raw R,G plus Z-in-[0,1].
    comb = nt.nodes.new("ShaderNodeCombineColor"); comb.location = (x + 1120, y)
    nt.links.new(sep.outputs[0], comb.inputs[0])
    nt.links.new(sep.outputs[1], comb.inputs[1])
    nt.links.new(zr.outputs[0], comb.inputs[2])
    return comb.outputs["Color"]


def build_material(spec: dict, pkg_dir: Path, opts: dict) -> "bpy.types.Material":
    key = spec["key"]
    mat = bpy.data.materials.new(name=key)
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")

    channels = spec.get("channels", {})

    # provenance
    mat["le_shaderset"] = spec.get("shaderset_hash", "")
    mat["le_material"] = spec.get("material_hash", "")
    for ch, data in channels.items():
        mat[f"le_tex_{ch}"] = data.get("texture", "")

    # base color
    bc = channels.get("base_color")
    base_in = _principled_input(bsdf, "Base Color")
    if bc and base_in:
        img = _load_image(pkg_dir, bc.get("file", ""), bc.get("colorspace", "sRGB"))
        if img:
            node = _tex_node(nt, img, bc.get("colorspace", "sRGB"), -600, 300)
            nt.links.new(node.outputs["Color"], base_in)
    if base_in and not (bc and bc.get("file")):
        base_in.default_value = tuple(spec.get("base_color_factor", [1, 1, 1, 1]))

    # roughness
    rg = channels.get("roughness")
    rough_in = _principled_input(bsdf, "Roughness")
    if rg and rough_in:
        img = _load_image(pkg_dir, rg.get("file", ""), "Non-Color")
        if img:
            node = _tex_node(nt, img, "Non-Color", -600, 0)
            nt.links.new(node.outputs["Color"], rough_in)

    # normal
    nm = channels.get("normal")
    norm_in = _principled_input(bsdf, "Normal")
    if nm and norm_in:
        img = _load_image(pkg_dir, nm.get("file", ""), "Non-Color")
        if img:
            tex = _tex_node(nt, img, "Non-Color", -1400, -300)
            src = _normal_chain(nt, tex, nm.get("reconstruct_z", False), -1200, -300)
            nmap = nt.nodes.new("ShaderNodeNormalMap"); nmap.location = (-300, -300)
            nt.links.new(src, nmap.inputs["Color"])
            nt.links.new(nmap.outputs["Normal"], norm_in)

    # opacity / alpha
    op = channels.get("opacity")
    alpha_in = _principled_input(bsdf, "Alpha")
    if op and alpha_in:
        img = _load_image(pkg_dir, op.get("file", ""), "Non-Color")
        if img:
            node = _tex_node(nt, img, "Non-Color", -600, -600)
            nt.links.new(node.outputs["Color"], alpha_in)
            try:
                mat.blend_method = "CLIP"
            except Exception:
                pass

    # emission
    em = channels.get("emission")
    em_col_in = _principled_input(bsdf, "Emission Color", "Emission")
    em_str_in = _principled_input(bsdf, "Emission Strength")
    if em and em_col_in:
        img = _load_image(pkg_dir, em.get("file", ""), em.get("colorspace", "sRGB"))
        if img:
            node = _tex_node(nt, img, em.get("colorspace", "sRGB"), -600, -900)
            nt.links.new(node.outputs["Color"], em_col_in)
        if em_str_in:
            em_str_in.default_value = float(spec.get("emissive_intensity", 1.0))
    elif em_col_in:
        ec = spec.get("emissive_color", [0, 0, 0])
        if any(ec):
            em_col_in.default_value = (ec[0], ec[1], ec[2], 1.0)
            if em_str_in:
                em_str_in.default_value = float(spec.get("emissive_intensity", 1.0))

    # two-sided
    try:
        mat.use_backface_culling = not spec.get("double_sided", False)
    except Exception:
        pass
    return mat
