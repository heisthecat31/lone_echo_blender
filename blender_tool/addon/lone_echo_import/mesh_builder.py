"""Build a Blender mesh object from a .lemesh manifest object entry.

Targets Blender 4.1+ / 5.x. Version-sensitive calls (custom normals) are guarded
and validated by the headless smoke test.
"""

from __future__ import annotations

import math

import bpy   # type: ignore


def _positions(pkg, obj):
    flat, comps = pkg.attribute(obj, "position")
    if flat is None:
        return [], 0
    verts = [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat), comps)]
    return verts, len(verts)


def _faces_and_material_indices(pkg, obj, mat_slot_of_key, reverse_winding=False):
    """Build triangle faces from draws; return (faces, per_face_material_index).

    `reverse_winding` (diagnostic; default off) swaps b<->c per triangle. The
    on-disk winding already agrees with the stored outward normals under the
    default pure-rotation axis transform, so reversal is NOT needed and only
    exists so tests/blender_axis_probe.py can render the inside-out counter-
    example. See AXIS_CALIBRATION.md.
    """
    indices = pkg.indices(obj)
    faces = []
    face_mat = []
    if indices is None:
        return faces, face_mat
    n_verts = obj["vertex_count"]
    for draw in obj.get("draws", []):
        if not draw.get("is_triangles"):
            continue
        slot = mat_slot_of_key.get(draw.get("material_key", ""), 0)
        start = draw["idx_start"]
        count = draw["idx_count"]
        end = start + count
        for i in range(start, end - 2, 3):
            a, b, c = int(indices[i]), int(indices[i + 1]), int(indices[i + 2])
            # primitive-restart / degenerate guard
            if a == b or b == c or a == c:
                continue
            if a >= n_verts or b >= n_verts or c >= n_verts:
                continue
            faces.append((a, c, b) if reverse_winding else (a, b, c))
            face_mat.append(slot)
    return faces, face_mat


def _axis_matrix(opts):
    """Object-level axis-conversion matrix (mathutils), non-destructive.

    Default (y_up_to_z_up): convert RAD/NRadEngine Y-up to Blender Z-up as a
    +90 deg rotation about X, i.e. game (x, y, z) -> Blender (x, -z, y). This is
    a PURE rotation (determinant +1): it stands the mesh upright, does NOT mirror
    it, and preserves the winding<->normal relationship already on disk (so faces
    stay front-facing / outward in Blender). Applied to the object matrix only,
    so the decoded vertex blobs remain byte-faithful to disk. Evidence and the
    rejected alternatives are documented in AXIS_CALIBRATION.md.

    Diagnostic-only toggle (default off; used by tests/blender_axis_probe.py to
    render the rejected counter-examples):
      * mirror_axis in {"X","Y","Z"}: post-multiply a reflection (det -1). This
        mirrors the model and is the WRONG convention -- it only exists to prove,
        visually, that a handedness flip reverses left/right (e.g. reverses the
        text baked on the shoulder plate).
    """
    from mathutils import Matrix  # type: ignore
    m = Matrix.Identity(4)
    if opts.get("y_up_to_z_up", True):
        m = Matrix.Rotation(math.radians(90.0), 4, "X") @ m
    axis = opts.get("mirror_axis")
    if axis in ("X", "Y", "Z"):
        s = [1.0, 1.0, 1.0]
        s["XYZ".index(axis)] = -1.0
        m = Matrix.Diagonal((s[0], s[1], s[2], 1.0)) @ m
    return m


def _joint_group_name(joint_names, idx):
    """Group/bone name: joint name from skeleton.json if present, else joint_<idx>."""
    if joint_names is not None:
        n = joint_names.get(idx)
        if n:
            return n
    return f"joint_{idx}"


def _apply_skinning(ob, pkg, obj, opts):
    """Create vertex groups from skin_indices/skin_weights and assign weights.

    skin_indices index into the skeleton's jointhierarchy; group names come from
    skeleton.json (opts['skeleton_joint_names']) when available, else joint_<idx>.
    No-op when the mesh carries no skin attributes.
    """
    sidx, si_comps = pkg.attribute(obj, "skin_indices")
    swgt, sw_comps = pkg.attribute(obj, "skin_weights")
    if sidx is None or swgt is None:
        return 0
    comps = min(si_comps or 0, sw_comps or 0)
    if comps <= 0:
        return 0
    n_verts = obj.get("vertex_count", 0)
    joint_names = opts.get("skeleton_joint_names")

    groups = {}   # joint_idx -> vertex_group

    def group_for(jidx):
        vg = groups.get(jidx)
        if vg is None:
            vg = ob.vertex_groups.new(name=_joint_group_name(joint_names, jidx))
            groups[jidx] = vg
        return vg

    for vi in range(n_verts):
        base = vi * comps
        for c in range(comps):
            k = base + c
            if k >= len(sidx) or k >= len(swgt):
                break
            w = float(swgt[k])
            if w <= 0.0:
                continue
            jidx = int(sidx[k])
            group_for(jidx).add([vi], w, "REPLACE")
    ob["le_skin_joint_count"] = len(groups)
    return len(groups)


def build_object(pkg, obj, get_material, opts) -> "bpy.types.Object":
    name = obj["name"]
    verts, n_verts = _positions(pkg, obj)

    # material slots for this object, in first-seen order
    mat_slot_of_key = {}
    ordered_keys = []
    for draw in obj.get("draws", []):
        k = draw.get("material_key", "")
        if k and k not in mat_slot_of_key:
            mat_slot_of_key[k] = len(ordered_keys)
            ordered_keys.append(k)

    faces, face_mat = _faces_and_material_indices(
        pkg, obj, mat_slot_of_key,
        reverse_winding=opts.get("reverse_winding", False))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    # material slots
    for k in ordered_keys:
        mesh.materials.append(get_material(k))
    if face_mat:
        mesh.polygons.foreach_set("material_index", face_mat)

    # smooth shading
    for p in mesh.polygons:
        p.use_smooth = True

    # --- UVs (per loop) ------------------------------------------------------
    loop_vidx = [0] * len(mesh.loops)
    mesh.loops.foreach_get("vertex_index", loop_vidx)
    for uv_key in ("uv0", "uv1"):
        flat, comps = pkg.attribute(obj, uv_key)
        if flat is None:
            continue
        layer = mesh.uv_layers.new(name=uv_key)
        uv_flat = [0.0] * (len(mesh.loops) * 2)
        flip = opts.get("flip_v", True)
        for li, vi in enumerate(loop_vidx):
            u = flat[vi * comps]
            v = flat[vi * comps + 1]
            uv_flat[li * 2] = u
            uv_flat[li * 2 + 1] = (1.0 - v) if flip else v
        layer.data.foreach_set("uv", uv_flat)

    # --- vertex colors (POINT float) ----------------------------------------
    flat, comps = pkg.attribute(obj, "color0")
    if flat is not None and n_verts:
        try:
            ca = mesh.color_attributes.new("color0", "FLOAT_COLOR", "POINT")
            rgba = [0.0] * (n_verts * 4)
            for vi in range(n_verts):
                for c in range(4):
                    rgba[vi * 4 + c] = flat[vi * comps + c] if c < comps else 1.0
            ca.data.foreach_set("color", rgba)
        except Exception:
            pass

    # --- custom split normals ------------------------------------------------
    flat, comps = pkg.attribute(obj, "normal")
    if flat is not None and n_verts:
        vnormals = []
        for vi in range(n_verts):
            nx = flat[vi * comps]
            ny = flat[vi * comps + 1]
            nz = flat[vi * comps + 2] if comps >= 3 else 0.0
            ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            vnormals.append((nx / ln, ny / ln, nz / ln))
        try:
            mesh.normals_split_custom_set_from_vertices(vnormals)
        except Exception:
            pass

    mesh.update()

    ob = bpy.data.objects.new(name, mesh)

    # skinning: vertex groups from skin_indices/skin_weights (no-op if absent)
    _apply_skinning(ob, pkg, obj, opts)

    # axis conversion: RAD is Y-up; Blender is Z-up. Clean object-level matrix
    # (non-destructive: vertex blobs stay byte-faithful to disk). The default is
    # a pure +90 deg-X rotation -> upright, un-mirrored, outward normals.
    ob.matrix_basis = _axis_matrix(opts) @ ob.matrix_basis

    # --- provenance / flags as custom properties -----------------------------
    # Blender ID int properties are 32-bit signed; RAD stores these as uint32
    # (e.g. lightmap_index 0xFFFFFFFF == "none"), which overflows a C int. Store
    # such values as strings so any uint32 round-trips without OverflowError.
    def _int_prop(v):
        return str(v) if isinstance(v, int) and not (-(2 ** 31) <= v < 2 ** 31) else v

    ob["le_name_hash"] = obj.get("name_hash", "")
    ob["le_flags"] = _int_prop(obj.get("flags", 0))
    ob["le_flag_names"] = ", ".join(obj.get("flag_names", []))
    ob["le_shadow_only"] = obj.get("shadow_only", False)
    ob["le_mesh_index"] = _int_prop(obj.get("mesh_index", -1))
    ob["le_lightmap_index"] = _int_prop(obj.get("lightmap_index", 0))
    lod_parent = any(d.get("lod", {}).get("is_lod_parent") for d in obj.get("draws", []))
    ob["le_lod_parent"] = lod_parent
    return ob
