"""Verification gallery — render every material of a .lemesh package for visual judging.

    blender.exe --background --factory-startup --python blender_verify_gallery.py \
        -- <PKG.lemesh> <OUT.png> [--mode gallery|beauty] [--max N]

Two modes:

  gallery  (default) one UV sphere per unique material, laid out in a grid in front
           of a checker backdrop and a bright emissive strip. The backdrop is what
           makes alpha/transmission legible: an opaque material hides it, a blended
           one shows it through, a cutout punches holes in it.

  beauty   import the package's real geometry and render it lit.

Colour management is pinned to **'Standard'**, never AgX: Blender 4.0+ defaults the
view transform to AgX, which heavily desaturates highlights, and comparing an
emissive panel or an HDR lightmap against a game reference under AgX makes correct
values look wrong (the project notes 4e).

EEVEE Next is used when available (raytracing on, so Transmission Weight is not
flat/black), else Workbench with a printed warning.
"""

import json
import math
import sys
from pathlib import Path

import bpy         # type: ignore
from mathutils import Vector   # type: ignore

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from render_engine_util import resolve_render_engine   # noqa: E402


def _argv():
    rest = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    pkg = rest[0] if rest else ""
    out = rest[1] if len(rest) > 1 else "/tmp/le_gallery.png"
    mode = "gallery"
    limit = 24
    for i, a in enumerate(rest):
        if a == "--mode" and i + 1 < len(rest):
            mode = rest[i + 1]
        if a == "--max" and i + 1 < len(rest):
            limit = int(rest[i + 1])
    return pkg, out, mode, limit


def _set_engine(scene):
    ids = [i.identifier for i in scene.render.bl_rna.properties["engine"].enum_items]
    try:
        scene.render.engine = resolve_render_engine("eevee", ids)
    except ValueError as exc:
        print(f"WARNING: {exc}; falling back to Workbench")
        scene.render.engine = "BLENDER_WORKBENCH"
        return False
    # EEVEE Next needs raytracing on or Transmission Weight renders flat/black.
    try:
        scene.eevee.use_raytracing = True
    except Exception:
        pass
    return True


def _pin_colour_management(scene):
    """'Standard', never AgX -- see module docstring."""
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0.0
    scene.view_settings.gamma = 1.0
    print(f"view_transform readback: {scene.view_settings.view_transform!r}")


def _backdrop():
    """A checkerboard plane behind the spheres so transparency is legible."""
    bpy.ops.mesh.primitive_plane_add(size=40.0, location=(0.0, 6.0, 0.0))
    plane = bpy.context.active_object
    plane.rotation_euler = (math.radians(90.0), 0.0, 0.0)
    mat = bpy.data.materials.new("__backdrop")
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
    checker = nt.nodes.new("ShaderNodeTexChecker")
    checker.location = (-400, 0)
    checker.inputs["Scale"].default_value = 24.0
    checker.inputs["Color1"].default_value = (0.85, 0.25, 0.12, 1.0)
    checker.inputs["Color2"].default_value = (0.10, 0.35, 0.85, 1.0)
    nt.links.new(checker.outputs["Color"], bsdf.inputs["Base Color"])
    plane.data.materials.append(mat)
    return plane


def _lighting(scene):
    world = bpy.data.worlds.new("w")
    world.use_nodes = True
    bg = world.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.05, 0.05, 0.06, 1.0)
        bg.inputs[1].default_value = 1.0
    scene.world = world

    key = bpy.data.lights.new("key", "AREA")
    key.energy = 900.0
    key.size = 6.0
    ob = bpy.data.objects.new("key", key)
    ob.location = (-5.0, -7.0, 7.0)
    ob.rotation_euler = (Vector((0, 0, 0)) - Vector(ob.location)).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(ob)

    fill = bpy.data.lights.new("fill", "AREA")
    fill.energy = 250.0
    fill.size = 8.0
    ob2 = bpy.data.objects.new("fill", fill)
    ob2.location = (6.0, -6.0, 2.0)
    ob2.rotation_euler = (Vector((0, 0, 0)) - Vector(ob2.location)).to_track_quat("-Z", "Y").to_euler()
    scene.collection.objects.link(ob2)


def _label(text, location):
    try:
        cu = bpy.data.curves.new(type="FONT", name="lbl")
        cu.body = text
        cu.size = 0.16
        ob = bpy.data.objects.new("lbl", cu)
        ob.location = location
        ob.rotation_euler = (math.radians(90.0), 0.0, 0.0)
        bpy.context.scene.collection.objects.link(ob)
        m = bpy.data.materials.new("__lbl")
        m.use_nodes = True
        b = next(n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
        b.inputs["Base Color"].default_value = (1, 1, 1, 1)
        for s in ("Emission Color", "Emission"):
            if s in b.inputs:
                b.inputs[s].default_value = (1, 1, 1, 1)
                break
        if "Emission Strength" in b.inputs:
            b.inputs["Emission Strength"].default_value = 2.0
        cu.materials.append(m)
        return ob
    except Exception as exc:
        print(f"label skipped: {exc}")
        return None


def _build_materials(pkg_dir, limit):
    """Build every manifest material through the addon's material_builder."""
    from lone_echo_import import material_builder   # noqa: E402

    manifest = json.loads((Path(pkg_dir) / "manifest.json").read_text(encoding="utf-8"))
    specs = manifest.get("materials", [])[:limit]
    built = []
    for spec in specs:
        try:
            mat = material_builder.build_material(spec, Path(pkg_dir), {})
            built.append((spec, mat))
        except Exception as exc:
            print(f"  BUILD FAIL {spec.get('key','?')}: {exc}")
    return manifest, built


def run_gallery(pkg_dir, out_png, limit):
    scene = bpy.context.scene
    _set_engine(scene)
    _pin_colour_management(scene)
    _backdrop()
    _lighting(scene)

    manifest, built = _build_materials(pkg_dir, limit)
    print(f"materials built: {len(built)}/{len(manifest.get('materials', []))}")

    cols = max(1, int(math.ceil(math.sqrt(len(built) or 1))))
    spacing = 2.4
    for i, (spec, mat) in enumerate(built):
        cx = (i % cols - (cols - 1) / 2.0) * spacing
        cz = ((cols - 1) / 2.0 - i // cols) * spacing
        bpy.ops.mesh.primitive_uv_sphere_add(radius=0.9, segments=48, ring_count=24,
                                             location=(cx, 0.0, cz))
        ob = bpy.context.active_object
        bpy.ops.object.shade_smooth()
        ob.data.materials.append(mat)
        rm = getattr(mat, "surface_render_method", "?")
        print(f"  [{i}] {spec.get('key','?')[:34]} "
              f"render_mode={spec.get('render_mode','-')} srm={rm} "
              f"mattype={spec.get('mattype_name', spec.get('mattype','-'))} "
              f"channels={sorted(spec.get('channels', {}))}")
        _label(f"{i}", (cx - 0.85, -1.1, cz + 0.95))

    span = cols * spacing
    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0.0, -span * 1.35 - 3.0, 0.0)
    cam.rotation_euler = (math.radians(90.0), 0.0, 0.0)
    scene.camera = cam

    scene.render.resolution_x = 1280
    scene.render.resolution_y = 1280
    scene.render.filepath = out_png
    bpy.ops.render.render(write_still=True)
    print(f"RENDERED: {out_png}")


def run_beauty(pkg_dir, out_png):
    import lone_echo_import   # noqa: E402

    scene = bpy.context.scene
    _set_engine(scene)
    _pin_colour_management(scene)
    _lighting(scene)

    summary = lone_echo_import.import_lemesh(
        pkg_dir, bpy.context,
        {"import_materials": True, "flip_v": True, "y_up_to_z_up": True})
    print(f"import: {summary}")

    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if not meshes:
        print("no meshes")
        return
    mn = Vector((1e9, 1e9, 1e9))
    mx = Vector((-1e9, -1e9, -1e9))
    for o in meshes:
        for corner in o.bound_box:
            wc = o.matrix_world @ Vector(corner)
            mn = Vector(map(min, mn, wc))
            mx = Vector(map(max, mx, wc))
    center = (mn + mx) * 0.5
    size = (mx - mn).length or 1.0

    cam_data = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    direction = Vector((1.0, -1.2, 0.55)).normalized()
    cam.location = center + direction * size * 1.5
    cam.rotation_euler = (center - cam.location).to_track_quat("-Z", "Y").to_euler()
    scene.camera = cam

    scene.render.resolution_x = 1280
    scene.render.resolution_y = 960
    scene.render.filepath = out_png
    bpy.ops.render.render(write_still=True)
    print(f"RENDERED: {out_png}")


def main():
    pkg, out, mode, limit = _argv()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if mode == "beauty":
        run_beauty(pkg, out)
    else:
        run_gallery(pkg, out, limit)


main()
