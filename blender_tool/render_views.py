"""Render a standard view set for any arena .blend -- stock or new.

    blender -b file.blend --python blender_tool/render_views.py -- \
        --out DIR [--tag name] [--only plan,cowl]

Plan and elevation are SECTION CUTS: the camera sits far outside and
`clip_start` is pushed just past the near half, so the near wall and ceiling are
sliced away and the interior is visible. That is the only way to read a closed
156 m tube in one image.

Camera orientation is given as an explicit basis, not Euler angles or a
look-at-plus-roll -- both of those guess, and the roll in particular silently
rotates about the wrong axis, which is how the first pass ended up rendering the
map on its side.
"""
from __future__ import annotations

import argparse
import sys

import bpy
from mathutils import Matrix, Vector

# camera basis columns: (screen right, screen up, backward = -view direction)
PLAN = ((0, 1, 0), (-1, 0, 0), (0, 0, 1))        # look down -Z, length across
ELEV = ((0, 1, 0), (0, 0, 1), (1, 0, 0))         # look along -X, length across

#  name,       basis|None,  location,        target,       proj,       res,        clip
VIEWS = [
    ("plan",      PLAN, (0, 0, 200),   None,          ("ortho", 168), (2600, 760), 192.0),
    ("elevation", ELEV, (200, 0, 0),   None,          ("ortho", 168), (2600, 760), 192.0),
    ("approach",  None, (0, -120, 1),  (0, 40, 0),    ("lens", 38),   (1700, 1000), 0.1),
    ("midfield",  None, (11, -30, 4),  (-2, 20, 0),   ("lens", 26),   (1700, 1000), 0.1),
    ("centre",    None, (0, -14, 2),   (0, 30, 0),    ("lens", 24),   (1700, 1000), 0.1),
    ("goalend",   None, (0, 48, 2),    (0, 78, 0),    ("lens", 32),   (1700, 1000), 0.1),
    ("goalmouth", None, (6, 60, 3),    (0, 74, 0),    ("lens", 40),   (1700, 1000), 0.1),
    ("corner",    None, (13, 34, 7),   (-6, 60, -3),  ("lens", 28),   (1700, 1000), 0.1),
]


def set_basis(cam, basis, loc):
    r, u, b = (Vector(v) for v in basis)
    cam.matrix_world = Matrix(((r.x, u.x, b.x, loc[0]),
                               (r.y, u.y, b.y, loc[1]),
                               (r.z, u.z, b.z, loc[2]),
                               (0, 0, 0, 1)))


def look(cam, loc, target, up=(0, 0, 1)):
    loc = Vector(loc)
    f = (Vector(target) - loc)
    f = f.normalized() if f.length > 1e-9 else Vector((0, 1, 0))
    up = Vector(up)
    if abs(f.dot(up.normalized())) > 0.999:
        up = Vector((1, 0, 0))
    r = f.cross(up).normalized()
    u = r.cross(f).normalized()
    set_basis(cam, (r, u, -f), loc)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--only", default="")
    a = ap.parse_args(argv)

    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sh = sc.display.shading
    sh.light = "STUDIO"
    sh.color_type = "MATERIAL" if bpy.data.materials else "SINGLE"
    sh.single_color = (0.55, 0.57, 0.60)
    sh.show_shadows = True
    sh.show_cavity = True
    sh.cavity_type = "BOTH"
    sh.curvature_ridge_factor = 1.2
    sh.curvature_valley_factor = 1.2
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
    tag = (a.tag + "_") if a.tag else ""
    for name, basis, loc, tgt, (kind, val), (rw, rh), clip in VIEWS:
        if want and name not in want:
            continue
        cd.type = "ORTHO" if kind == "ortho" else "PERSP"
        if kind == "ortho":
            cd.ortho_scale = val
        else:
            cd.lens = val
        cd.clip_start = clip
        cd.clip_end = 900.0
        if basis:
            set_basis(cam, basis, loc)
        else:
            look(cam, loc, tgt)
        sc.render.resolution_x, sc.render.resolution_y = rw, rh
        sc.render.filepath = "%s/%s%s.png" % (a.out.rstrip("/"), tag, name)
        bpy.ops.render.render(write_still=True)
        print("rendered", tag + name)
    return 0


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    sys.exit(main(argv))
