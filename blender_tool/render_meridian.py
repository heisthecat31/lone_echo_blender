"""Render orientation views of a built arena, and dump its layout numerically.

    blender -b file.blend --python blender_tool/render_meridian.py -- --out DIR

Cameras are AIMED with an explicit up-hint rather than given Euler angles --
hand-written angles are guesswork and quietly point at a wall. The plan and
elevation views roll the camera so the map's LENGTH runs across the frame, which
is the only way a 156 m map is legible in one image; they also render at a wide
aspect for the same reason.

Views looking in from outside hide the hull, because the arena is a closed tube.
"""
from __future__ import annotations

import argparse
import sys

import bpy
from mathutils import Matrix, Vector

HULL = ("hull",)

#  name,      camera,          look at,        proj,       aspect,  hide
VIEWS = [
    ("plan",       (0, 0, 200),    (0, 0, 0),   ("ortho", 168), (2600, 700), HULL),
    ("elevation",  (200, 0, 0),    (0, 0, 0),   ("ortho", 168), (2600, 700), HULL),
    ("section",    (0, 0, 200),    (0, 0, 0),   ("ortho", 168), (2600, 700), ()),
    ("approach",   (0, -150, 2),   (0, 40, 0),  ("lens", 42),   (1600, 1000), ()),
    ("midfield",   (0, -46, 3),    (0, 30, 0),  ("lens", 26),   (1600, 1000), ()),
    ("drum",       (13, -22, 7),   (0, 2, 0),   ("lens", 32),   (1600, 1000), ()),
    ("vanes",      (10, 12, 5),    (-4, 42, 1), ("lens", 30),   (1600, 1000), ()),
    ("cowl",       (0, 44, 3),     (0, 74, 0),  ("lens", 34),   (1600, 1000), ()),
    ("goalmouth",  (7, 62, 4),     (0, 73, 0),  ("lens", 40),   (1600, 1000), ()),
]


def look(cam, loc, target, up_hint=(0, 0, 1)):
    """Point the camera at `target`. Camera looks down its own -Z, up is +Y."""
    loc = Vector(loc)
    fwd = (Vector(target) - loc)
    fwd = fwd.normalized() if fwd.length > 1e-9 else Vector((0, 1, 0))
    up_hint = Vector(up_hint)
    if abs(fwd.dot(up_hint.normalized())) > 0.999:
        up_hint = Vector((1, 0, 0))
    right = fwd.cross(up_hint).normalized()
    up = right.cross(fwd).normalized()
    cam.location = loc
    cam.matrix_world = Matrix((
        (right.x, up.x, -fwd.x, loc.x),
        (right.y, up.y, -fwd.y, loc.y),
        (right.z, up.z, -fwd.z, loc.z),
        (0, 0, 0, 1)))


def layout_dump():
    print("\n--- layout: world bounds by collection ---")
    for coll in bpy.data.collections:
        obs = [o for o in coll.objects if o.type == "MESH"]
        if not obs:
            continue
        pts = [o.matrix_world @ Vector(c) for o in obs for c in o.bound_box]
        lo = [min(p[i] for p in pts) for i in range(3)]
        hi = [max(p[i] for p in pts) for i in range(3)]
        print("  %-22s %3d obj   x %7.2f..%7.2f  y %7.2f..%7.2f  z %7.2f..%7.2f"
              % (coll.name, len(obs), lo[0], hi[0], lo[1], hi[1], lo[2], hi[2]))


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="")
    a = ap.parse_args(argv)
    layout_dump()

    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sh = sc.display.shading
    sh.light = "STUDIO"
    sh.color_type = "MATERIAL"
    sh.show_shadows = True
    sh.show_cavity = True
    sh.cavity_type = "BOTH"
    sh.curvature_ridge_factor = 1.0
    sh.curvature_valley_factor = 1.0
    sc.render.image_settings.file_format = "PNG"
    try:
        sc.view_settings.view_transform = "Standard"
    except Exception:                                    # noqa: BLE001
        pass

    cd = bpy.data.cameras.new("cam")
    cam = bpy.data.objects.new("cam", cd)
    sc.collection.objects.link(cam)
    sc.camera = cam

    want = set(a.only.split(",")) if a.only else None
    for name, loc, tgt, (kind, val), (rw, rh), hide in VIEWS:
        if want and name not in want:
            continue
        hidden = []
        for h in hide:
            for ob in bpy.data.objects:
                if ob.name == h or ob.name.startswith(h + "_m"):
                    ob.hide_render = True
                    hidden.append(ob)
        cd.type = "ORTHO" if kind == "ortho" else "PERSP"
        if kind == "ortho":
            cd.ortho_scale = val
        else:
            cd.lens = val
        sc.render.resolution_x, sc.render.resolution_y = rw, rh
        # plan and elevation: roll so the map's length runs across the frame
        up = (1, 0, 0) if name in ("plan", "section") else (0, 0, 1)
        if name == "elevation":
            up = (0, 0, 1)
        look(cam, loc, tgt, up)
        if name in ("plan", "section", "elevation"):
            cam.matrix_world = cam.matrix_world @ Matrix.Rotation(
                __import__("math").radians(90), 4, "Z")
        sc.render.filepath = "%s/%s.png" % (a.out.rstrip("/"), name)
        bpy.ops.render.render(write_still=True)
        print("rendered", name)
        for ob in hidden:
            ob.hide_render = False
    return 0


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    sys.exit(main(argv))
