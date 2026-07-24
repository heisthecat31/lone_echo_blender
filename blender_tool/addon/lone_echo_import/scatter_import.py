"""Import a `.lescatter` static-scatter package and place its instances.

Consumer for the pinned `le_scatter` contract (see `scatter_reader`). Builds each
UNIQUE mesh once as a `bpy.data.meshes` datablock in NATIVE game space, then links
one lightweight object per instance sharing that datablock (linked duplicates), so
even 21k instances stay memory-cheap. Each instance object's `matrix_world` is
`B @ (T @ R @ S)` from `scatter_reader.compose_instance_matrix` — the axis basis B
is applied on the object matrix only, never baked into the mesh (no double-apply).

Headless use:

    import lone_echo_import
    lone_echo_import.import_lescatter("path/to/foo.lescatter", bpy.context,
                                      {"max_instances": 2000})
"""

from __future__ import annotations

import json
from pathlib import Path

import bpy   # type: ignore
from bpy.props import BoolProperty, IntProperty, StringProperty   # type: ignore
from bpy_extras.io_utils import ImportHelper                       # type: ignore

from . import scatter_reader
from . import material_builder


# Distinct viewport colors so different meshes/material bindings are visually
# separable in a Workbench MATERIAL-color render (placement proof).
_PALETTE = [
    (0.85, 0.30, 0.25, 1.0), (0.25, 0.55, 0.85, 1.0), (0.35, 0.75, 0.40, 1.0),
    (0.90, 0.70, 0.20, 1.0), (0.65, 0.40, 0.80, 1.0), (0.30, 0.75, 0.78, 1.0),
    (0.88, 0.50, 0.68, 1.0), (0.60, 0.60, 0.62, 1.0),
]


def _scatter_material(matidx: int, shdidx: int):
    """A cached, distinctly-colored Principled material keyed by (matidx, shdidx).

    Scatter packages carry only material/shaderset *indices* (no channel data), so
    this stands in with a viewport color that makes distinct bindings legible.
    """
    key = f"__le_scatter_mat_{matidx}_{shdidx}"
    mat = bpy.data.materials.get(key)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name=key)
    mat.use_nodes = True
    col = _PALETTE[(matidx if matidx >= 0 else 0) % len(_PALETTE)]
    mat.diffuse_color = col   # viewport / Workbench MATERIAL color
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None and "Base Color" in bsdf.inputs:
        bsdf.inputs["Base Color"].default_value = col
    mat["le_matidx"] = matidx
    mat["le_shdidx"] = shdidx
    return mat


def build_scatter_mesh(pkg, mesh_entry, get_material, opts) -> "bpy.types.Mesh":
    """Build ONE `bpy.data.meshes` datablock in native game space (shared source).

    No axis conversion is baked in — the Y-up->Z-up basis is applied per instance
    on the object matrix. `flip_v` converts DX top-left UVs to Blender bottom-left,
    exactly as the .lemesh importer does.

    Multi-material (v2): one Blender material slot per distinct (matidx, shdidx) in
    the mesh's `draws`, with each face's `material_index` set from the covering
    draw (`scatter_reader.assign_face_materials`). Single-draw meshes collapse to
    one slot with every face at material_index 0 — identical to the v1 behaviour.
    `get_material(matidx, shdidx)` resolves a cached material for any pair.
    """
    m = mesh_entry["index"]
    name = f"scatter_m{m}_{mesh_entry.get('name_hash', '')}"

    pos = pkg.positions(mesh_entry)
    n_verts = len(pos) // 3
    verts = [(pos[i], pos[i + 1], pos[i + 2]) for i in range(0, n_verts * 3, 3)]

    idx = pkg.indices(mesh_entry)
    faces, face_slot, slot_keys = scatter_reader.assign_face_materials(
        idx, pkg.draws(mesh_entry), n_verts)

    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    # one material slot per draw pair (first-seen order); fall back to the mesh's
    # top-level pair if a package somehow carries no draws.
    if slot_keys:
        for matidx, shdidx in slot_keys:
            mesh.materials.append(get_material(matidx, shdidx))
    else:
        mesh.materials.append(get_material(
            int(mesh_entry.get("matidx", -1)), int(mesh_entry.get("shdidx", -1))))
    if face_slot:
        mesh.polygons.foreach_set("material_index", face_slot)

    # --- normals (optional): crisp faces via custom split normals when present,
    #     else flat shading so untextured boxes still read cleanly -------------
    nrm = pkg.normals(mesh_entry)
    has_normals = nrm is not None and n_verts and len(nrm) >= n_verts * 3
    for p in mesh.polygons:
        p.use_smooth = bool(has_normals)
    if has_normals:
        import math
        vn = []
        for vi in range(n_verts):
            nx, ny, nz = nrm[vi * 3], nrm[vi * 3 + 1], nrm[vi * 3 + 2]
            ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            vn.append((nx / ln, ny / ln, nz / ln))
        try:
            mesh.normals_split_custom_set_from_vertices(vn)
        except Exception:
            pass

    # --- uv0 (optional; per loop, flip_v like the .lemesh path) --------------
    uv = pkg.uv0(mesh_entry)
    if uv is not None and len(mesh.loops):
        loop_vidx = [0] * len(mesh.loops)
        mesh.loops.foreach_get("vertex_index", loop_vidx)
        layer = mesh.uv_layers.new(name="uv0")
        flip = opts.get("flip_v", True)
        uv_flat = [0.0] * (len(mesh.loops) * 2)
        for li, vi in enumerate(loop_vidx):
            u = uv[vi * 2]
            v = uv[vi * 2 + 1]
            uv_flat[li * 2] = u
            uv_flat[li * 2 + 1] = (1.0 - v) if flip else v
        layer.data.foreach_set("uv", uv_flat)

    mesh.update()

    mesh["le_name_hash"] = mesh_entry.get("name_hash", "")
    mesh["le_matidx"] = int(mesh_entry.get("matidx", -1))
    mesh["le_shdidx"] = int(mesh_entry.get("shdidx", -1))
    mesh["le_proxy"] = bool(mesh_entry.get("proxy", False))
    return mesh


def _place_instances(context, coll, pkg, mesh_datablocks, records, opts) -> dict:
    """Link one object per instance sharing its mesh datablock, at `B @ T @ R @ S`.

    `B` (the Y-up->Z-up basis) is computed once and passed into
    `compose_instance_matrix`, so every instance uses the exact same tested math.
    `max_instances` caps how many are placed for a fast first render (0/None = all).
    """
    from mathutils import Matrix   # type: ignore

    basis = scatter_reader.basis_matrix(opts.get("y_up_to_z_up", True))
    cap = opts.get("max_instances")
    if not cap or cap <= 0:
        cap = None

    placed = skipped_missing = 0
    for rec in records:
        if cap is not None and placed >= cap:
            break
        db = mesh_datablocks.get(rec.mesh_index)
        if db is None:                    # e.g. a skipped proxy mesh
            skipped_missing += 1
            continue
        ob = bpy.data.objects.new(f"{coll.name}_i{rec.index}", db)
        rows = scatter_reader.compose_instance_matrix(
            rec.translation, rec.rotation, rec.scale, basis=basis)
        ob.matrix_world = Matrix(rows)
        ob["le_instance_index"] = rec.index
        ob["le_mesh_index"] = rec.mesh_index
        coll.objects.link(ob)
        placed += 1
    return {"placed": placed, "skipped_missing_mesh": skipped_missing}


def import_lescatter(pkg_path, context, opts: dict) -> dict:
    """Core routine: build unique meshes + place all (or `max_instances`) instances.

    Returns a summary dict. Usable without the operator.
    """
    opts = dict(opts or {})
    pkg = scatter_reader.ScatterPackage(pkg_path)

    mat_cache = {}
    # optional per-material base-color/normal textures from a resolver sidecar
    # (le_scene_materials.py `_materials.json`), keyed by (matidx, shdidx). When
    # absent, fall back to the distinct-viewport-color placeholder material.
    sidecar = {}
    tex_base = None
    tex_subdir = f"{pkg.master}_textures"
    mj = opts.get("materials_json")
    if mj:
        with open(mj, "r", encoding="utf-8") as fh:
            _md = json.load(fh)
        for e in _md.get("materials", []):
            sidecar[(int(e["matidx"]), int(e["shdidx"]))] = e
        tex_base = Path(opts.get("textures_base") or Path(mj).parent)
        tex_subdir = f"{_md.get('master', pkg.master)}_textures"

    def get_material(matidx, shdidx):
        """Resolve (cache) a material for an ARBITRARY (matidx, shdidx) pair — one
        per draw slot, not only the mesh's top-level pair."""
        matidx = int(matidx)
        shdidx = int(shdidx)
        key = (matidx, shdidx)
        mat = mat_cache.get(key)
        if mat is not None:
            return mat
        entry = sidecar.get(key)
        if entry is not None:
            channels = {}
            if entry.get("basecolor_dds"):
                channels["base_color"] = {"file": entry["basecolor_dds"], "colorspace": "sRGB"}
            nt = entry.get("normal_texture")
            if nt:
                channels["normal"] = {"file": f"{tex_subdir}/{nt}.dds",
                                      "colorspace": "Non-Color", "reconstruct_z": True}
            bc = (list(entry.get("base_color") or [1.0, 1.0, 1.0])[:3] + [1.0])
            spec = {"key": f"scatter_mat_{matidx}_{shdidx}",
                    "material_hash": entry.get("material_hash", ""),
                    "shaderset_hash": "",
                    "channels": channels,
                    "base_color_factor": bc,
                    "double_sided": bool(entry.get("double_sided", False))}
            mat = material_builder.build_material(spec, tex_base, opts)
            try:                       # viewport color for Workbench SINGLE/MATERIAL
                mat.diffuse_color = (bc[0], bc[1], bc[2], 1.0)
            except Exception:
                pass
        else:
            mat = _scatter_material(matidx, shdidx)
        mat_cache[key] = mat
        return mat

    coll_name = f"lescatter_{pkg.master or Path(pkg.dir).stem}"
    coll = bpy.data.collections.new(coll_name)
    context.scene.collection.children.link(coll)

    import_proxy = opts.get("import_proxy", False)
    mesh_datablocks = {}
    n_tris = 0
    skipped_proxy = 0
    for mesh_entry in pkg.meshes:
        if mesh_entry.get("proxy") and not import_proxy:
            skipped_proxy += 1
            continue
        db = build_scatter_mesh(pkg, mesh_entry, get_material, opts)
        mesh_datablocks[mesh_entry["index"]] = db
        n_tris += len(db.polygons)

    records = scatter_reader.read_instances(pkg)
    place = _place_instances(context, coll, pkg, mesh_datablocks, records, opts)

    return {
        "collection": coll_name,
        "master": pkg.master,
        "meshes_total": pkg.num_meshes,
        "meshes_built": len(mesh_datablocks),
        "meshes_skipped_proxy": skipped_proxy,
        "instances_total": pkg.num_instances,
        "instances_placed": place["placed"],
        "instances_skipped_missing_mesh": place["skipped_missing_mesh"],
        "triangles_unique": n_tris,
        "materials": len(mat_cache),
    }


class IMPORT_OT_lescatter(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.lescatter"
    bl_label = "Import Lone Echo Scatter (.lescatter)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})   # type: ignore

    flip_v: BoolProperty(name="Flip UV V", default=True,
                         description="Convert DX top-left UV origin to Blender bottom-left")   # type: ignore
    y_up_to_z_up: BoolProperty(name="Y-up to Z-up", default=True,
                               description="Apply the +90deg-X basis on the instance "
                                           "object matrix (not baked into meshes)")   # type: ignore
    import_proxy: BoolProperty(name="Include Proxy Meshes", default=False,
                               description="Also build meshes flagged proxy (collision/LOD proxies)")   # type: ignore
    max_instances: IntProperty(name="Max Instances", default=0, min=0,
                               description="Cap placed instances for a fast preview "
                                           "(0 = place all)")   # type: ignore

    def draw(self, context):
        layout = self.layout
        for prop in ("flip_v", "y_up_to_z_up", "import_proxy", "max_instances"):
            layout.prop(self, prop)

    def execute(self, context):
        opts = {
            "flip_v": self.flip_v,
            "y_up_to_z_up": self.y_up_to_z_up,
            "import_proxy": self.import_proxy,
            "max_instances": self.max_instances,
        }
        try:
            summary = import_lescatter(self.filepath, context, opts)
        except Exception as exc:   # noqa: BLE001
            self.report({"ERROR"}, f"lescatter import failed: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"},
                    "Scatter: placed {instances_placed}/{instances_total} instances "
                    "over {meshes_built} meshes ({triangles_unique} unique tris)".format(**summary))
        return {"FINISHED"}


def menu_func(self, context):
    self.layout.operator(IMPORT_OT_lescatter.bl_idname, text="Lone Echo Scatter (.lescatter)")
