"""Build a Blender mesh object from a .lemesh manifest object entry.

Targets Blender 4.1+ / 5.x. Version-sensitive calls (custom normals) are guarded
and validated by the headless smoke test.
"""

from __future__ import annotations

import math
import re

import bpy   # type: ignore

from . import lightmap_builder, material_builder
from .package_reader import select_lod_draws

# `uv0`, `uv1`, `uv2`, ... — the exporter's name for a texcoord SLOT
# (`le_mesh.vertex_format.attribute_key`). UV sets differ by `slot`, not usage.
_UV_KEY_RE = re.compile(r"^uv(\d+)$")


def _positions(pkg, obj):
    flat, comps = pkg.attribute(obj, "position")
    if flat is None:
        return [], 0
    verts = [(flat[i], flat[i + 1], flat[i + 2]) for i in range(0, len(flat), comps)]
    return verts, len(verts)


def _faces_and_material_indices(pkg, obj, mat_slot_of_key, reverse_winding=False,
                                lod_level=0):
    """Build triangle faces from draws; return (faces, per_face_material_index).

    `reverse_winding` (diagnostic; default off) swaps b<->c per triangle. The
    on-disk winding already agrees with the stored outward normals under the
    default pure-rotation axis transform, so reversal is NOT needed and only
    exists so tests/blender_axis_probe.py can render the inside-out counter-
    example. See AXIS_CALIBRATION.md.

    `lod_level` picks one level of the mesh's LOD chain (see `select_lod_draws`);
    pass a negative value to emit every level.
    """
    indices = pkg.indices(obj)
    faces = []
    face_mat = []
    if indices is None:
        return faces, face_mat
    n_verts = obj["vertex_count"]
    for draw in select_lod_draws(obj.get("draws", []), lod_level):
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
    """One manifest object entry -> a Blender mesh object.

    ⚠ The axis conversion is applied to `matrix_basis` (see below).  Callers
    that read `matrix_world` — camera framing, bounding boxes, world-space
    export — must call `bpy.context.view_layer.update()` first, or they will
    silently get the IDENTITY matrix and aim at the origin.  `object.bound_box`
    is stale in exactly the same way.  Both cost A11 two full render passes of
    plausible-looking pure-black frames.
    """
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
        reverse_winding=opts.get("reverse_winding", False),
        lod_level=opts.get("lod_level", 0))

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    # material slots.
    # `eDiffuseVertexColor` (CGMeshData 0x2000) is a per-MESH flag while materials are
    # shared per material-key, so the per-vertex albedo tint cannot live in the shared
    # material -- meshes without the flag would be tinted too. Flagged meshes get a
    # lazily-created `<mat>__vcol` variant instead (material_builder.vertex_color_variant),
    # which splices Color Attribute("color0") -> Mix(MULTIPLY) -> Base Color. Engine
    # side: `diffusealbedo = k_composite_diffuse.xyz * params.albedovertex.xyz`
    # (`shader-confirmed`).
    want_vcol = (material_builder.wants_vertex_color_diffuse(obj)
                 and opts.get("vertex_color_diffuse", True))
    # The baked lightmap is per-MESH too, and for the same reason: the PAGE
    # comes from `CGMeshData.lmsliceindex`, not from the material. Two meshes
    # sharing one material key can sit on different pages (they do in the
    # shipped station_front pair), so the page cannot be baked into the shared
    # material either -- `material_builder.lightmap_variant` splits per
    # (material, page) and composes with the vertex-colour split above.
    # `{}` when the package carries no lightmap binding, when no atlas was
    # resolved, or when this mesh has no page.
    lm_spec = lightmap_builder.lightmap_spec_for_object(
        opts.get("lightmap_context"), obj, opts)
    for k in ordered_keys:
        m = get_material(k)
        if want_vcol:
            m = material_builder.vertex_color_variant(m, "color0")
        if lm_spec:
            m = material_builder.lightmap_variant(
                m, lm_spec, opts, opts.get("lightmap_context"))
        mesh.materials.append(m)
    if face_mat:
        mesh.polygons.foreach_set("material_index", face_mat)

    # smooth shading
    for p in mesh.polygons:
        p.use_smooth = True

    # --- UVs (per loop) ------------------------------------------------------
    loop_vidx = [0] * len(mesh.loops)
    mesh.loops.foreach_get("vertex_index", loop_vidx)
    # uv0/uv1 are the historical pair. ALSO import this object's RESOLVED
    # lightmap UV set: on a (0, 1, 4) object that is `uv2`, and it would
    # otherwise never reach Blender at all -- which makes the slot-4 resolution
    # in `lightmap_builder` inert, because the UV Map node would point at a layer
    # that does not exist docs/LIGHTING.md §4.3).
    # Appended, never inserted -- uv0 must stay layer 0 (the render UV).
    #
    # ★ 2026-08-05: import EVERY decoded `uvN`, not just the historical pair plus
    # the lightmap set. A corpus census over `blender_tool/exports` (286 packages /
    # 913 objects) finds `uv2` on **91 objects (10.0 %)** and `uv3` on **29 (3.2 %)**;
    # before this, any texcoord slot that was neither uv0/uv1 nor the resolved
    # lightmap set was decoded into `blobs/`, written into the manifest, and then
    # silently dropped on import. Ordering is still append-only and deterministic
    # (numeric by slot), so uv0 stays layer 0 and the lightmap set keeps whatever
    # index it already had.
    uv_keys = ["uv0", "uv1"]
    _lm_uv = lightmap_builder._lightmap_uv_of(obj)
    if _lm_uv and _lm_uv not in uv_keys:
        uv_keys.append(_lm_uv)
    for extra in sorted((k for k in (obj.get("attributes") or {})
                         if _UV_KEY_RE.match(k) and k not in uv_keys),
                        key=lambda k: int(k[2:])):
        uv_keys.append(extra)
    for uv_key in uv_keys:
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
    # ★ 2026-08-05: `color1` too. It is not decoration — it is the engine's
    # per-vertex LAYER-BLEND WEIGHT: `vertblend = blend[i-1]` where `blend` is the
    # `float4 blend : COLOR1` vertex stream (`shader-confirmed` in the engine's
    # ubershader), and the layer
    # composite multiplies the blend mask by it. It ships on **523 of 913 objects
    # (57.3 %)** in `blender_tool/exports` and reached Blender on none of them,
    # which is why layer compositing here has only ever been mask-driven.
    # ⚠ Whether the shader samples it is the `use_vertex_blend_` permutation bit,
    # which is NOT on disk — so this IMPORTS the data and asserts nothing about
    # its use. `le_mesh.materials` already records that as `vertex_blend_applied`.
    for _cattr in ("color0", "color1"):
        flat, comps = pkg.attribute(obj, _cattr)
        if flat is None or not n_verts:
            continue
        try:
            ca = mesh.color_attributes.new(_cattr, "FLOAT_COLOR", "POINT")
            rgba = [0.0] * (n_verts * 4)
            for vi in range(n_verts):
                for c in range(4):
                    rgba[vi * 4 + c] = flat[vi * comps + c] if c < comps else 1.0
            ca.data.foreach_set("color", rgba)
        except Exception:
            # ⚠ recorded, not swallowed: a silent miss here also silently disables
            # `vertex_color_variant`'s tint, which reads as an authoring choice.
            ob_note = f"le_vertex_color_failed_{_cattr}"
            mesh[ob_note] = True

    # --- tangents -------------------------------------------------------------
    # ★ 2026-08-05: `tangent` (EUsage 3, `s16n` x4) is decoded on **913 of 913**
    # objects in `blender_tool/exports`.
    #
    # ⛔ CORRECTION, 2026-08-05 (R1). This comment used to read "`.w` is the
    # bitangent handedness (+/-1)". IT IS NOT +/-1. `.w` takes exactly FOUR
    # values — -1.0, -0.5, +0.5, +1.0 — over 509,266 vertices
    # (`tests/test_vertex_streams.py::test_tangent_w_is_a_FOUR_state_field_not_a_
    # handedness_bit`), i.e. a sign AND a magnitude. Both halves are now measured
    # and both are documented on `material_builder.tangent_w_meaning`:
    #   * SIGN     = the bitangent handedness. 397,082/397,082 vertices agree with
    #                the UV-derived handedness (100.00 %), in all four states.
    #   * MAGNITUDE = a FRONT/BACK tag on a duplicated shell: |w| = 0.5 marks a
    #                second copy of every vertex, position-identical to its
    #                |w| = 1.0 partner (109,400/109,400 = 100.00 %) with an
    #                exactly negated NORMAL (99.92 %). ⚠ its TANGENT is negated
    #                on only 65.67 % — the back shell carries its own frame,
    #                which is exactly why the shader reads `sign(w)` per vertex.
    # A FIFTH value is refused loudly rather than rounded — see below.
    #
    # ⚠ Blender will not accept an authored per-loop tangent — `mesh.loops[].tangent`
    # is read-only and computed from the active UV layer — so this deliberately
    # STORES the shipped basis as generic attributes rather than fighting the API.
    # `material_builder._shipped_tangent_normal` then rebuilds the TBN in shader
    # nodes, which is the only route by which an authored basis reaches a Blender
    # shader. (Until 0.4.0 there were three writers here and zero readers, so
    # every normal map ran on Blender's UV-derived tangent instead.)
    # It is also the prerequisite for the world-space SG5 normal-mapped lightmap
    # sum, which is decoded but not yet wired.
    flat, comps = pkg.attribute(obj, "tangent")
    if flat is not None and n_verts and comps >= 3:
        try:
            ta = mesh.attributes.new("le_tangent", "FLOAT_VECTOR", "POINT")
            ta.data.foreach_set("vector", [flat[vi * comps + c]
                                           for vi in range(n_verts) for c in range(3)])
            if comps >= 4:
                ha = mesh.attributes.new("le_tangent_w", "FLOAT", "POINT")
                ha.data.foreach_set("value", [flat[vi * comps + 3]
                                              for vi in range(n_verts)])
                # ⛔ REFUSE LOUDLY, DO NOT ROUND. Every state this importer knows
                # how to interpret is one of the four; an unknown one means the
                # 2-bit reading is wrong for this asset, and the shader's
                # `sign(w)` would then be a guess. Recorded on the mesh so a
                # render can be audited without re-reading the blob, and counted
                # so "which states does this mesh carry" is answerable offline.
                # ⚠ classify the DISTINCT values (there are four), not 48,450
                # vertices one at a time: this runs on every object of every
                # import.
                seen = {}
                for vi in range(n_verts):
                    k = round(float(flat[vi * comps + 3]), 3)
                    seen[k] = seen.get(k, 0) + 1
                states, unknown = {}, []
                for k, cnt in seen.items():
                    m = material_builder.tangent_w_meaning(k)
                    if not m["known"]:
                        if len(unknown) < 8:
                            unknown.append(k)
                        continue
                    key = f"{m['w']:+.1f}"
                    states[key] = states.get(key, 0) + cnt
                mesh["le_tangent_w_states"] = ", ".join(
                    f"{k}:{v}" for k, v in sorted(states.items()))
                mesh["le_tangent_w_has_back_shell"] = any(
                    k in ("-0.5", "+0.5") for k in states)
                if unknown:
                    mesh["le_tangent_w_unexpected"] = ", ".join(str(u) for u in unknown)
        except Exception:
            mesh["le_tangent_failed"] = True

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
    # The PAGE, not just the table row. `lm_slice_index` is the ONLY thing that
    # selects which of the 13 lightmap pages this mesh samples; the SG5 colour
    # slices are `page*5 .. page*5+4` (`shader-confirmed`, page-major).
    # It was previously read from the manifest and discarded, so anything wiring
    # lightmaps AFTER import silently fell back to page 0.
    ob["le_lm_slice_index"] = _int_prop(obj.get("lm_slice_index", 0xFFFFFFFF))
    ob["le_lightmap_numlobes"] = _int_prop(obj.get("numlobes", 0))
    # What the import ACTUALLY did with the two fields above, so a .blend can be
    # audited without re-running the resolver. `le_lightmap_page` is absent (not
    # 0) when nothing was wired -- page 0 is a real page and never means "none".
    if lm_spec:
        page = lightmap_builder._page_of(lm_spec.get("slice_index"))
        if page is not None:
            ob["le_lightmap_page"] = page
    ob["le_lightmap_wired"] = bool(
        lm_spec and any(m is not None and m.get("le_lightmap_wired")
                        for m in mesh.materials))
    draws = obj.get("draws", [])
    ob["le_lod_parent"] = any(d.get("lod", {}).get("is_lod_parent") for d in draws)
    ob["le_lod_levels"] = max(
        (int(d.get("lod", {}).get("level", 0) or 0) for d in draws), default=0) + 1
    ob["le_lod_level"] = opts.get("lod_level", 0)
    ob["le_vertex_color_diffuse"] = bool(want_vcol)
    return ob
