"""Import a `.lescatter` static-scatter package and place its instances.

Consumer for the pinned `le_scatter` contract (see `scatter_reader`). Builds each
UNIQUE mesh once as a `bpy.data.meshes` datablock in NATIVE game space, then links
one lightweight object per instance sharing that datablock (linked duplicates), so
even 21k instances stay memory-cheap. Each instance object's `matrix_world` is
`B @ (T @ R @ S)` from `scatter_reader.compose_instance_matrix` — the axis basis B
is applied on the object matrix only, never baked into the mesh (no double-apply).

MATERIALS. `opts["materials_json"]` points at the resolver sidecar
`<master>_materials.json`. Two versions are accepted:

  * **v2+** (`{"version":2, "master":..., "textures_subdir":...,
    "materials":[{"matidx":i,"shdidx":j,"spec":{...}}]}`) — `spec` is byte-for-byte
    the same dict a `.lemesh` `manifest.json["materials"][i]` carries, and it is
    handed to `material_builder.build_material` **verbatim**. The LEVEL path then
    renders with the identical 35-field treatment as a single-mesh import.
  * **v1** (no `"version"` key) — the legacy flat-field adapter, kept working
    unchanged. It can express only `base_color` + `normal`, which is why a level
    import used to lose alpha, render mode, emission, specular, roughness, blend
    masks, AO and `image.alpha_mode`.

PER-INSTANCE LIGHTMAP (`opts["instance_lightmap"]`, **default OFF**). A static
instance takes its baked-light UV from the INSTANCE record, not from the vertex
stream, and instances of the same mesh carry DIFFERENT UVs — each owns its own
strip of the atlas (`stream-confirmed`, docs/LIGHTING.md §8.2).
Honouring that therefore **breaks instancing**: a lightmapped instance needs its
own `bpy.data.meshes` datablock. On station_front that is up to 21,394 datablocks
instead of 1,050, which is why it is opt-in rather than a silent change. See
`_InstanceLightmapper` for the full design note.

Headless use:

    import lone_echo_import
    lone_echo_import.import_lescatter("path/to/foo.lescatter", bpy.context,
                                      {"max_instances": 2000,
                                       "materials_json": ".../942c..._materials.json"})
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import bpy   # type: ignore
from bpy.props import (   # type: ignore
    BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty,
)
from bpy_extras.io_utils import ImportHelper                       # type: ignore

from . import scatter_reader
from . import material_builder

try:
    from . import evr_lighting
    from . import evr_movers
    from . import evr_effects
    from . import evr_texture_arrays
    from . import evr_skeleton
except ImportError:          # optional: a package without EVR lighting still imports
    evr_lighting = None
    evr_movers = None
    evr_effects = None
    evr_texture_arrays = None
    evr_skeleton = None

#: UV layer the per-instance lightmap UVs are written to on the per-instance mesh
#: COPY. Deliberately NOT `uv1`: `uv1` is the (all-zero, dead) vertex-stream set
#: and it is kept intact on the copy so the two are never confused in a shipped
#: `.blend`. It is also not `uv2`, which is a real albedo set on some meshes
#: docs/LIGHTING.md.
INSTANCE_LM_UV_LAYER = "lm_inst"

#: `instance_lightmap_uv_source` values.
UV_SOURCE_INSTANCE = "instance"     # the v5 per-instance stream — the correct one
#: ⛔ DIAGNOSTIC ONLY. Selecting this renders the DOCUMENTED FAILURE MODE and is
#: never reachable from the operator. It is a NAMED CONSTANT, not a literal at the
#: assignment site, on purpose: `test_lightmap_uv_slot.py`'s guard forbids a
#: hardcoded `"uv1"` from *selecting* the lightmap UV set, and that guard is
#: right — the one place this string is allowed to appear is the definition of
#: "the wrong answer we can render".
UV_SOURCE_UV1 = "uv1"
UV_SOURCES = (UV_SOURCE_INSTANCE, UV_SOURCE_UV1)


# Distinct viewport colors so different meshes/material bindings are visually
# separable in a Workbench MATERIAL-color render (placement proof).
_PALETTE = [
    (0.85, 0.30, 0.25, 1.0), (0.25, 0.55, 0.85, 1.0), (0.35, 0.75, 0.40, 1.0),
    (0.90, 0.70, 0.20, 1.0), (0.65, 0.40, 0.80, 1.0), (0.30, 0.75, 0.78, 1.0),
    (0.88, 0.50, 0.68, 1.0), (0.60, 0.60, 0.62, 1.0),
]


def _int_prop(v):
    """Blender ID int properties are 32-bit SIGNED; RAD stores these as uint32.

    `lm_slice_index == 0xFFFFFFFF` ("no lightmap") overflows a C int and raises
    `OverflowError` on assignment, so out-of-range ints are stored as STRINGS.
    Verbatim copy of `mesh_builder.py:248-249` -- both importers must stringify
    identically or a consumer has to special-case the source. **Any consumer must
    accept `"4294967295"` as well as `4294967295`.**
    """
    return str(v) if isinstance(v, int) and not (-(2 ** 31) <= v < 2 ** 31) else v


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

    Two UV layers are built when the package carries them: `uv0` (the texture UV
    set) and `uv1` (the LIGHTMAP UV set), both with the same `flip_v`. Packages
    written before `uv1` existed simply have no key and build `uv0` alone.
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

    # --- uv0 / uv1 (both optional; per loop, flip_v like the .lemesh path) ---
    # uv1 is the LIGHTMAP UV set. ⚠ It gets the SAME `flip_v` as uv0, deliberately.
    # Settled on the `.lemesh` path both numerically and pictorially
    # (docs/LIGHTING.md 4.4 + 9.3: Blender's DDS loader flips rows
    # relative to the file, so a D3D-authored UV must be flipped to address the
    # same texel; flip-off renders the same station beams nearly unlit). The V flip
    # is a property of the API SAMPLER ORIGIN, not of the UV set -- do not "fix"
    # it by exempting uv1.
    if len(mesh.loops):
        loop_vidx = [0] * len(mesh.loops)
        mesh.loops.foreach_get("vertex_index", loop_vidx)
        flip = opts.get("flip_v", True)
        for uv_name, uv in (("uv0", pkg.uv0(mesh_entry)),
                            ("uv1", pkg.uv1(mesh_entry))):
            if uv is None or len(uv) < n_verts * 2:
                continue                   # absent key (pre-uv1 package) or short blob
            layer = mesh.uv_layers.new(name=uv_name)
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
    # lightmap ids -- SAME property names + SAME uint32 stringification as the
    # .lemesh path (`mesh_builder.py:256-263`), so one consumer reads both.
    lm_index, lm_slice, lm_lobes = scatter_reader.ScatterPackage.lightmap_ids(mesh_entry)
    mesh["le_lightmap_index"] = _int_prop(lm_index)
    mesh["le_lm_slice_index"] = _int_prop(lm_slice)
    mesh["le_lightmap_numlobes"] = _int_prop(lm_lobes)
    return mesh


# ---------------------------------------------------------------------------
# Per-instance lightmap (default OFF)
# ---------------------------------------------------------------------------

class _InstanceLightmapper:
    """Give each lightmapped instance its OWN mesh datablock + lightmap UV layer.

    ★ THE DESIGN CALL, AND WHY THE CHEAP ALTERNATIVE IS NOT AVAILABLE.

    The obvious way to keep instancing is "one shared mesh + a per-instance UV
    *attribute*". Blender cannot express that: a UV set is per-LOOP mesh data
    (`Mesh.uv_layers`), and every object sharing a datablock sees the same loops.
    The only per-OBJECT channels a shader can read are custom properties, which
    the `Attribute` node exposes as ONE value per object — enough for a scalar or
    a vector, not for `nverts` distinct UV pairs. So there is no per-instance UV
    attribute to reach for; the choice is between copying the datablock and
    getting the bake wrong.

    ⚠ A per-object **affine** (scale + offset) attribute WOULD be enough IF every
    instance's UV set were the same chart placed at a different atlas rect. The
    two sampled deltas in findings 8.2 are 12 and 16 texels, i.e. the strips are
    NOT all the same width, so at best it is scale+offset and at worst it is
    neither. It is `unresolved` on the real stream and this front does not assume
    it: `uv_affine_fit_error()` measures it on whatever package is to hand, so
    the question is decidable the moment a real v5 package exists.

    So: one `bpy.data.meshes` copy per lightmapped instance. Materials do NOT
    need copying per instance — the lightmap node graph addresses the UV set BY
    NAME, and every copy names it `INSTANCE_LM_UV_LAYER` — so D3's per-(material,
    page) variant cache serves the whole level unchanged, bounded by
    `material_keys x pages_used`.

    Identical UV sets are shared rather than re-copied (keyed on a blake2b of the
    raw UV bytes + the mesh + the page). On the shipped data that is expected to
    hit almost never (findings 8.2 says the sets differ), so it is a MEASUREMENT
    of how much instancing actually survives, not an optimisation being relied
    on. `instance_lightmap_dedup=False` turns it off.
    """

    def __init__(self, pkg, mesh_entries, opts, ctx, lightmap_builder,
                 uv_source=UV_SOURCE_INSTANCE):
        self.pkg = pkg
        self.entries = mesh_entries                 # {mesh_index: manifest entry}
        self.opts = opts
        self.ctx = ctx or {}
        self.lb = lightmap_builder
        self.stream = pkg.instance_lightmap
        self.uv_source = uv_source if uv_source in UV_SOURCES else UV_SOURCE_INSTANCE
        if self.uv_source == UV_SOURCE_UV1:
            # ⛔ DIAGNOSTIC. `uv1` is the vertex-stream set and 1046/1050 of the
            # shipped blobs are ENTIRELY ZERO, so this renders atlas texel (0,0)
            # for 99.6 % of the level, on the per-MESH page rather than the
            # instance's. It exists so that failure can be RENDERED on demand
            # instead of only described — the same class of switch as D3's
            # `lightmap_force_page`. Never reachable from the operator UI.
            self.uv_layer = UV_SOURCE_UV1
        else:
            self.uv_layer = (opts.get("instance_lightmap_uv_layer")
                             or INSTANCE_LM_UV_LAYER)
        self.dedup = bool(opts.get("instance_lightmap_dedup", True))
        # flip ONCE and only once: the stream says whether the extractor already
        # did it, and `flip_v` is the same importer-wide switch `uv0`/`uv1` use.
        self.flip_v = bool(opts.get("flip_v", True)) and not self.stream.flip_v_applied
        self._db_cache = {}          # (mesh_index, page, uv_digest) -> mesh datablock
        self._spec_cache = {}        # page -> lm_spec
        self._loop_cache = {}        # mesh_index -> [loop vertex index]
        self.stats = {
            "wired": 0, "created": 0, "shared": 0, "pages": {},
            "skipped_unlit_mesh": 0, "skipped_no_uv": 0, "skipped_no_page": 0,
            "skipped_vertex_count_mismatch": 0, "variants": set(),
            "variant_uv_layer_conflicts": 0, "meshes_wired": 0,
        }

    # --- availability --------------------------------------------------------

    @property
    def usable(self):
        """Whether `datablock_for` can do anything.

        The `uv1` diagnostic needs no per-instance stream (that is the point of
        it), so it is 'usable' on any v4 package.
        """
        if self.uv_source == UV_SOURCE_UV1:
            return False        # handled once per MESH, never per instance
        return bool(self.stream.present)

    def _lm_spec(self, page):
        """The `lm_spec` for a PAGE, cached. Built by `lightmap_builder` from a
        synthetic manifest-object dict — the same shape the `.lemesh` path feeds
        it, so there is exactly one spec builder for both importers."""
        if page in self._spec_cache:
            return self._spec_cache[page]
        obj = {"lightmap_index": 1, "lm_slice_index": page,
               "lightmap_uv": self.uv_layer}
        spec = self.lb.lightmap_spec_for_object(self.ctx, obj, self.opts)
        self._spec_cache[page] = spec
        return spec

    def _loop_vidx(self, mesh_index, db):
        cached = self._loop_cache.get(mesh_index)
        if cached is None:
            cached = [0] * len(db.loops)
            db.loops.foreach_get("vertex_index", cached)
            self._loop_cache[mesh_index] = cached
        return cached

    def _variant_materials(self, db, page):
        """Swap each material slot for its (material, page) lightmap variant."""
        spec = self._lm_spec(page)
        if not spec:
            return
        for i, mat in enumerate(list(db.materials)):
            if mat is None:
                continue
            var = material_builder.lightmap_variant(mat, spec, self.opts, self.ctx)
            if var is not None and var is not mat:
                db.materials[i] = var
                self.stats["variants"].add(var.name)
                # ⚠ D3 keys the variant datablock on (material, PAGE) only, so a
                # single Blender session that wires the same (material, page)
                # through two different UV layers — e.g. the `uv1` diagnostic and
                # then the real path — reuses the FIRST variant and silently
                # samples the wrong set. Stamp the layer and count disagreements
                # rather than letting that be invisible. (One import only ever
                # uses one source, so this stays 0 in normal use.)
                prev = var.get("le_lightmap_uv_layer")
                if prev and prev != self.uv_layer:
                    self.stats["variant_uv_layer_conflicts"] += 1
                var["le_lightmap_uv_layer"] = self.uv_layer

    # --- ⛔ the NAIVE path, for pictures only --------------------------------

    def wire_shared_meshes(self, mesh_datablocks):
        """⛔ THE DOCUMENTED FAILURE MODE: wire `uv1` on the SHARED datablocks.

        This is what a naive `.lescatter` lightmap consumer would write — reuse
        the `.lemesh` model, take the UV from the vertex stream (`uv1`) and the
        page from the per-mesh `lm_slice_index`. It is wrong twice over on this
        path: 1046 of 1050 `uv1` blobs are entirely ZERO (so 99.6 % of the level
        samples atlas texel (0,0)) and the per-mesh page disagrees with the
        instance's for 65.1 % of instances. Reachable ONLY through
        `instance_lightmap_uv_source == "uv1"`, which the operator never sets.

        Costs nothing in memory — no datablock is copied — which is exactly why
        it is tempting.
        """
        if self.uv_source != UV_SOURCE_UV1:
            return
        for mesh_index, db in mesh_datablocks.items():
            entry = self.entries.get(mesh_index)
            if entry is None:
                continue
            lm_index, lm_slice, _lobes = \
                scatter_reader.ScatterPackage.lightmap_ids(entry)
            if not self.lb.is_lightmapped(lm_index):
                self.stats["skipped_unlit_mesh"] += 1
                continue
            page = self.lb._page_of(lm_slice)
            if page is None:
                self.stats["skipped_no_page"] += 1
                continue
            if db.uv_layers.get(self.uv_layer) is None:
                self.stats["skipped_no_uv"] += 1
                continue
            db["le_lightmap_page"] = page
            db["le_lightmap_uv_layer"] = self.uv_layer
            db["le_lightmap_uv_source"] = UV_SOURCE_UV1
            self._variant_materials(db, page)
            self.stats["meshes_wired"] += 1
            self.stats["pages"][page] = self.stats["pages"].get(page, 0) + 1

    # --- the per-instance work ----------------------------------------------

    def datablock_for(self, rec, base_db):
        """-> (datablock, page). `(base_db, None)` whenever nothing was wired.

        Returning the BASE datablock on every skip is what keeps the option a
        pure addition: an instance the stream cannot describe renders exactly as
        it does with the mode off.
        """
        if not self.usable:
            return base_db, None
        entry = self.entries.get(rec.mesh_index)
        if entry is None:
            return base_db, None
        lm_index, _slice, _lobes = scatter_reader.ScatterPackage.lightmap_ids(entry)
        if not self.lb.is_lightmapped(lm_index):
            # `lightmapindex == 0xffffffff` — 5 of station_front's 1050 meshes.
            self.stats["skipped_unlit_mesh"] += 1
            return base_db, None
        page = self.stream.page(rec.index)
        if page is None:
            self.stats["skipped_no_page"] += 1
            return base_db, None
        uv = self.stream.uv(rec.index)
        if uv is None:
            self.stats["skipped_no_uv"] += 1
            return base_db, None
        n_verts = len(base_db.vertices)
        if len(uv) != n_verts * 2:
            # The record's pair count must equal the mesh's nverts (findings 8.1:
            # stride == 44 + 8*nverts). A mismatch means the instance and the mesh
            # disagree, and guessing which is right would silently misplace the
            # whole chart, so refuse this instance and count it.
            self.stats["skipped_vertex_count_mismatch"] += 1
            return base_db, None

        digest = b""
        if self.dedup:
            digest = hashlib.blake2b(memoryview(uv).cast("B").tobytes(),
                                     digest_size=16).digest()
            key = (rec.mesh_index, page, digest)
            hit = self._db_cache.get(key)
            if hit is not None:
                self.stats["shared"] += 1
                self.stats["wired"] += 1
                self.stats["pages"][page] = self.stats["pages"].get(page, 0) + 1
                return hit, page

        db = base_db.copy()
        db.name = f"{base_db.name}_lm_i{rec.index}"
        loop_vidx = self._loop_vidx(rec.mesh_index, base_db)
        layer = db.uv_layers.get(self.uv_layer) or db.uv_layers.new(name=self.uv_layer)
        flat = [0.0] * (len(loop_vidx) * 2)
        flip = self.flip_v
        for li, vi in enumerate(loop_vidx):
            u = uv[vi * 2]
            v = uv[vi * 2 + 1]
            flat[li * 2] = u
            flat[li * 2 + 1] = (1.0 - v) if flip else v
        layer.data.foreach_set("uv", flat)
        db["le_lightmap_page"] = page
        db["le_lightmap_uv_layer"] = self.uv_layer
        db["le_lightmap_uv_source"] = UV_SOURCE_INSTANCE
        db["le_instance_index"] = rec.index
        self._variant_materials(db, page)

        if self.dedup:
            self._db_cache[(rec.mesh_index, page, digest)] = db
        self.stats["created"] += 1
        self.stats["wired"] += 1
        self.stats["pages"][page] = self.stats["pages"].get(page, 0) + 1
        return db, page

    def summary(self):
        s = self.stats
        return {
            "enabled": True,
            "uv_source": self.uv_source,
            "uv_layer": self.uv_layer,
            "meshes_wired_shared": s["meshes_wired"],
            "variant_uv_layer_conflicts": s["variant_uv_layer_conflicts"],
            "stream_present": bool(self.stream.present),
            "stream_reason": self.stream.reason,
            "stream_count": self.stream.count,
            "stream_flip_v_applied": self.stream.flip_v_applied,
            "flip_v_applied_by_importer": self.flip_v,
            "atlas_available": bool(self.ctx.get("available")),
            "atlas_reason": self.ctx.get("reason", ""),
            "atlas_source": self.ctx.get("source", ""),
            "atlas_texture": self.ctx.get("color_file", ""),
            "instances_wired": s["wired"],
            "datablocks_created": s["created"],
            "datablocks_shared": s["shared"],
            "material_variants": len(s["variants"]),
            "pages": dict(sorted(s["pages"].items())),
            "skipped_unlit_mesh": s["skipped_unlit_mesh"],
            "skipped_no_uv": s["skipped_no_uv"],
            "skipped_no_page": s["skipped_no_page"],
            "skipped_vertex_count_mismatch": s["skipped_vertex_count_mismatch"],
        }


def uv_affine_fit_error(pkg, mesh_index, instance_a, instance_b):
    """Max abs residual of fitting instance B's UVs as `A * scale + offset`.

    ⚠ MEASUREMENT HELPER, not part of the import path. It answers the one
    question that would let a future front collapse 21,394 mesh copies back into
    one shared mesh + four per-object floats: *is each instance's lightmap chart
    the same chart, rigidly scaled and translated into a different atlas rect?*
    Returns `None` when either instance has no usable stream record.

    A near-zero result on many pairs of the REAL stream would make a
    shared-mesh + `Attribute(OBJECT)` -> `Mapping` design correct AND O(1) in
    memory. A large one closes the idea. Today it is `unresolved`: findings 8.2
    reports strip widths of 12 and 16 texels, which already rules out a pure
    translation.
    """
    ilm = pkg.instance_lightmap
    a = ilm.uv(instance_a)
    b = ilm.uv(instance_b)
    if a is None or b is None or len(a) != len(b) or len(a) < 4:
        return None
    err = 0.0
    for comp in (0, 1):
        xs = list(a[comp::2])
        ys = list(b[comp::2])
        lo, hi = min(xs), max(xs)
        ylo, yhi = min(ys), max(ys)
        if hi - lo < 1e-9:
            fits = [(1.0, (sum(ys) / len(ys)) - (sum(xs) / len(xs)))]
        else:
            # The bounding box maps to the bounding box under ANY affine map, so
            # matching the two extents recovers (scale, offset) EXACTLY when the
            # relation really is affine — which makes a large residual a genuine
            # REFUTATION, not a fitting artefact. Both orientations are tried so
            # a mirrored chart is not mistaken for a non-affine one.
            s = (yhi - ylo) / (hi - lo)
            fits = [(s, ylo - lo * s), (-s, yhi + lo * s)]
        best = min(max(abs(x * s + o - y) for x, y in zip(xs, ys)) for s, o in fits)
        err = max(err, best)
    return err


def _place_instances(context, coll, pkg, mesh_datablocks, records, opts,
                     lods=None, inst_lm=None) -> dict:
    """Link one object per instance sharing its mesh datablock, at `B @ T @ R @ S`.

    `B` (the Y-up->Z-up basis) is computed once and passed into
    `compose_instance_matrix`, so every instance uses the exact same tested math.
    `max_instances` caps how many are placed for a fast first render (0/None = all).
    `lods` (indexed by GLOBAL instance index) tags each object with its LOD group
    and level as custom properties; pass None to skip the tagging.

    `inst_lm` (an `_InstanceLightmapper`, or None = today's behaviour) may hand
    back a PER-INSTANCE mesh copy instead of the shared datablock — the only way
    to honour a per-instance lightmap UV set. With `inst_lm=None` every object
    shares its mesh exactly as before.
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
        lm_page = None
        if inst_lm is not None:
            db, lm_page = inst_lm.datablock_for(rec, db)
        ob = bpy.data.objects.new(f"{coll.name}_i{rec.index}", db)
        rows = scatter_reader.compose_instance_matrix(
            rec.translation, rec.rotation, rec.scale, basis=basis)
        ob.matrix_world = Matrix(rows)
        ob["le_instance_index"] = rec.index
        ob["le_mesh_index"] = rec.mesh_index
        # Mirror the mesh's lightmap ids onto the OBJECT as well. The .lemesh path
        # puts them on the object (`mesh_builder.py:256-263`) because that is where
        # a lightmap consumer looks; here the datablock is shared by every instance
        # of a mesh, so the ids are per-mesh but must still be readable per-object.
        for prop in ("le_lightmap_index", "le_lm_slice_index", "le_lightmap_numlobes"):
            if prop in db.keys():
                ob[prop] = db[prop]
        # D3 §6's object contract: `le_lightmap_page` is ABSENT (never 0) when
        # nothing was wired, because page 0 is a real page and must never double
        # as "none".
        if lm_page is not None:
            ob["le_lightmap_page"] = lm_page
            ob["le_lightmap_wired"] = True
            ob["le_lightmap_uv_layer"] = db.get("le_lightmap_uv_layer", "")
        lod = lods[rec.index] if lods and rec.index < len(lods) else None
        if lod is not None:
            ob["le_lod_group"] = lod.group
            ob["le_lod_level"] = lod.level
            ob["le_lod_group_levels"] = lod.group_levels
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
    # Optional per-material data from a resolver sidecar
    # (`scripts/le_scene_materials.py` -> `<master>_materials.json`), keyed by
    # (matidx, shdidx). When absent, fall back to the distinct-viewport-color
    # placeholder material.
    sidecar = {}
    tex_base = None
    tex_subdir = f"{pkg.master}_textures"
    sidecar_version = 1
    mj = opts.get("materials_json")
    if not mj and opts.get("auto_materials", True):
        # The extractor writes the sidecar NEXT TO the package directory, named
        # `<master>_materials.json` (`scripts/le_scene_materials.py`). Finding it
        # automatically is what makes the resolved-material path the DEFAULT rather
        # than an option the caller has to know exists -- without this, an import
        # silently falls back to flat viewport-colour placeholders and looks like a
        # material bug. Pass `auto_materials=False` to force the placeholder path.
        for cand in (pkg.dir.parent / f"{pkg.master}_materials.json",
                     pkg.dir / "materials.json"):
            if cand.is_file():
                mj = str(cand)
                break
    if mj:
        with open(mj, "r", encoding="utf-8") as fh:
            _md = json.load(fh)
        try:
            sidecar_version = int(_md.get("version", 1))
        except (TypeError, ValueError):
            sidecar_version = 1
        for e in _md.get("materials", []):
            sidecar[(int(e["matidx"]), int(e["shdidx"]))] = e
        tex_base = Path(opts.get("textures_base") or Path(mj).parent)
        tex_subdir = _md.get("textures_subdir") or \
            f"{_md.get('master', pkg.master)}_textures"

    def _spec_from_v1_entry(entry, matidx, shdidx) -> dict:
        """The LEGACY 6-key adapter. Frozen: v1 sidecars must import as they did.

        This hand-rolls a spec from the flat fields a v1 `_materials.json` carries
        and is exactly the integration gap the v2 path closes -- `channels` can
        only ever hold `base_color` + `normal`, so a v1 sidecar can express no
        alpha, no render mode, no emission, no specular, no roughness, no blend
        mask, no AO and no `image.alpha_mode` hint, however rich the underlying
        material actually is. Do NOT extend it; extend the sidecar to v2 instead.
        """
        channels = {}
        if entry.get("basecolor_dds"):
            channels["base_color"] = {"file": entry["basecolor_dds"], "colorspace": "sRGB"}
        nt = entry.get("normal_texture")
        if nt:
            channels["normal"] = {"file": f"{tex_subdir}/{nt}.dds",
                                  "colorspace": "Non-Color", "reconstruct_z": True}
        bc = (list(entry.get("base_color") or [1.0, 1.0, 1.0])[:3] + [1.0])
        return {"key": f"scatter_mat_{matidx}_{shdidx}",
                "material_hash": entry.get("material_hash", ""),
                "shaderset_hash": "",
                "channels": channels,
                "base_color_factor": bc,
                "double_sided": bool(entry.get("double_sided", False))}

    def get_material(matidx, shdidx):
        """Resolve (cache) a material for an ARBITRARY (matidx, shdidx) pair — one
        per draw slot, not only the mesh's top-level pair.

        **v2+ sidecar: `entry["spec"]` is handed to `material_builder` VERBATIM.**
        It is byte-for-byte the same dict shape as a `.lemesh`
        `manifest.json["materials"][i]`, so the scatter (LEVEL) path gets the same
        35-field treatment as a single-mesh import: alpha chain + render mode,
        emissive layer/tint/intensity, specular F0, sqrt-roughness, AO channel,
        layer blend masks, `mattype`/`blendmode` and every `image.alpha_mode`.
        Re-deriving any of it here is precisely the bug this replaced -- so
        nothing in the spec is touched, not even normalised.
        """
        matidx = int(matidx)
        shdidx = int(shdidx)
        key = (matidx, shdidx)
        mat = mat_cache.get(key)
        if mat is not None:
            return mat
        entry = sidecar.get(key)
        spec = None
        if entry is not None:
            if sidecar_version >= 2 and isinstance(entry.get("spec"), dict):
                spec = entry["spec"]           # verbatim passthrough — do not adapt
            else:
                spec = _spec_from_v1_entry(entry, matidx, shdidx)
        if spec is not None:
            # `channels[*]["file"]` is relative to the sidecar's own directory.
            mat = material_builder.build_material(spec, tex_base, opts)
            mat["le_matidx"] = matidx
            mat["le_shdidx"] = shdidx
            mat["le_sidecar_version"] = sidecar_version
            try:                       # viewport color for Workbench SINGLE/MATERIAL
                bc = list(spec.get("base_color_factor") or [1.0, 1.0, 1.0, 1.0])
                mat.diffuse_color = (float(bc[0]), float(bc[1]), float(bc[2]), 1.0)
            except Exception:
                pass
        else:
            mat = _scatter_material(matidx, shdidx)
        mat_cache[key] = mat
        return mat

    coll_name = f"lescatter_{pkg.master or Path(pkg.dir).stem}"
    coll = bpy.data.collections.new(coll_name)
    context.scene.collection.children.link(coll)
    # `collections.new` RENAMES on collision -- a second import of the same
    # level becomes `...001`. Reporting the requested name instead of the real
    # one sent the lighting pass to the PREVIOUS import's collection: the new
    # objects got no lightmap, the old ones got 1020 materials copied for
    # nothing, and the operator said "loaded but NOT wired -- no reason
    # recorded" because zero objects matched.
    coll_name = coll.name

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

    # --- per-instance lightmap (DEFAULT OFF) --------------------------------
    # OFF is byte-identical to the pre-existing path: `inst_lm` stays None, no
    # atlas is resolved, no datablock is copied and no material is varied.
    inst_lm = None
    lm_summary = {"enabled": False,
                  "reason": "instance_lightmap option is off (default)"}
    if opts.get("instance_lightmap"):
        from . import lightmap_builder      # lazy: keeps the option's cost at 0
        uv_source = str(opts.get("instance_lightmap_uv_source")
                        or UV_SOURCE_INSTANCE).lower()
        lm_ctx = lightmap_builder.resolve_lightmap_context(
            pkg.dir, pkg.manifest, opts)
        inst_lm = _InstanceLightmapper(pkg, {m["index"]: m for m in pkg.meshes},
                                       opts, lm_ctx, lightmap_builder,
                                       uv_source=uv_source)
        if inst_lm.uv_source == UV_SOURCE_UV1:
            inst_lm.wire_shared_meshes(mesh_datablocks)

    records = scatter_reader.read_instances(pkg)
    lods = scatter_reader.read_instance_lod(pkg)
    # LOD selection. Every LOD level of a prop is a separate mesh with its own
    # instances, so without this every level is placed at once and they overlap.
    # `lod_level` defaults to 0 (highest detail); pass LOD_ALL for the old
    # all-levels-stacked behaviour.
    lod_level = opts.get("lod_level", 0)
    selected = scatter_reader.filter_by_lod(records, lods, lod_level)
    place = _place_instances(context, coll, pkg, mesh_datablocks, selected, opts,
                             lods=lods, inst_lm=inst_lm)
    if inst_lm is not None:
        lm_summary = inst_lm.summary()
        # ★ the number the option exists to make visible: how much instancing
        # actually survived the bake.
        lm_summary["base_datablocks"] = len(mesh_datablocks)
        lm_summary["datablocks_total"] = (len(mesh_datablocks)
                                          + lm_summary.get("datablocks_created", 0))
        lm_summary["instances_sharing_base"] = (
            place["placed"] - lm_summary.get("instances_wired", 0))

    backfaces_shown = 0
    if opts.get("show_backfaces"):
        seen_mats = set()
        for ob in coll.objects:
            for slot in ob.material_slots:
                m = slot.material
                if m is None or m.name in seen_mats:
                    continue
                seen_mats.add(m.name)
                if getattr(m, "use_backface_culling", False):
                    m.use_backface_culling = False
                    backfaces_shown += 1

    return {
        "collection": coll_name,
        "backfaces_shown": backfaces_shown,
        "master": pkg.master,
        "meshes_total": pkg.num_meshes,
        "meshes_built": len(mesh_datablocks),
        "meshes_skipped_proxy": skipped_proxy,
        "instances_total": pkg.num_instances,
        "instances_selected": len(selected),
        "instances_placed": place["placed"],
        "instances_skipped_missing_mesh": place["skipped_missing_mesh"],
        "lod_level": lod_level,
        "lod_max_level": pkg.max_lod_level,
        "lod_groups": int(pkg.lod.get("num_groups", 0)),
        "triangles_unique": n_tris,
        "materials": len(mat_cache),
        "materials_sidecar_version": sidecar_version if sidecar else 0,
        "materials_from_sidecar": sum(
            1 for k in mat_cache if k in sidecar),
        "instance_lightmap": lm_summary,
    }


class IMPORT_OT_lescatter(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.lescatter"
    bl_label = "Import Lone Echo Scatter (.lescatter)"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})   # type: ignore
    #: ⚠ `filter_glob` is an EXTENSION filter, not a name filter: Blender
    #: matches it with `BLI_path_extension_check_glob`, so only `*.ext`
    #: patterns work. Setting it to "manifest.json" hides EVERY file, which is
    #: why picking the right sidecar is fixed in `execute` instead --
    #: see `package_reader.resolve_package_file`.

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
    lod_level: EnumProperty(
        name="LOD Level",
        description="Which level of detail to place. Every LOD level of a prop is a "
                    "separate mesh with its own instances, so 'All levels' stacks them "
                    "on top of each other. A level is clamped per group, so props with "
                    "fewer levels still contribute their coarsest one",
        items=[
            ("0", "LOD 0 (highest detail)", "Place each prop's most detailed level"),
            ("1", "LOD 1", "One step coarser where available"),
            ("2", "LOD 2", "Two steps coarser where available"),
            ("3", "LOD 3", "Three steps coarser where available"),
            ("4", "LOD 4", "Four steps coarser where available"),
            ("-2", "Coarsest", "Each prop's cheapest level"),
            ("-1", "All levels (stacked)", "Place every instance — levels overlap"),
        ],
        default="0")   # type: ignore
    auto_materials: BoolProperty(
        name="Auto-find Materials",
        default=True,
        description="Look for `<master>_materials.json` next to the package and use it. "
                    "Without a sidecar every mesh gets a flat placeholder colour, which "
                    "reads as a material bug rather than a missing file")   # type: ignore
    materials_json: StringProperty(
        name="Materials JSON",
        default="",
        subtype="FILE_PATH",
        description="Override the resolved-material sidecar. Leave blank to auto-find. "
                    "A v2 sidecar carries the full material spec (alpha, emissive, "
                    "specular, roughness, blend masks); a v1 sidecar carries only "
                    "base colour and normal")   # type: ignore
    textures_base: StringProperty(
        name="Textures Base",
        default="",
        subtype="DIR_PATH",
        description="Directory the sidecar's texture paths are relative to. Blank = the "
                    "sidecar's own directory")   # type: ignore
    evr_armature: BoolProperty(
        name="Armature",
        default=True,
        description="Build the model's skeleton and bind skin weights when the "
                    "package has a skeleton.json (written by "
                    "scripts/evr_apply_skeleton.py). Bones are parentless -- "
                    "rest pose and skinning are correct, but posing a bone does "
                    "not carry its children")   # type: ignore
    world_ambient: FloatProperty(
        name="World Ambient",
        description=("Strength of Blender's world background. The engine has NO "
                     "constant ambient -- its ambient is the baked SH4/SG "
                     "lighting -- so the faithful value is 0. Blender's default "
                     "grey world is what makes the skymap render white and the "
                     "level look washed out. Raise it only to see geometry while "
                     "baked lighting is off"),
        default=0.0, min=0.0, max=10.0, precision=3)

    uv_scroll_rate: FloatProperty(
        name="UV Scroll Speed",
        description=("Speed of the sky/background UV scroll, in UV units per "
                     "second. The materials say THAT they scroll but not how "
                     "fast -- no rate is on disk -- so this is a tunable, not a "
                     "recovered value. 0 disables it"),
        default=0.02, min=0.0, max=1.0, precision=4)

    evr_effects: BoolProperty(
        name="Fog and Exposure",
        default=True,
        description="Apply the level's authored fog, exposure and tonemap "
                    "coefficients from CGFSEffectsResource. 24 of 32 levels "
                    "author fog. Exposure maps exactly; the tonemap is the "
                    "engine's Hable curve, which Blender has no equivalent "
                    "for, so its five coefficients are recorded on the scene "
                    "as evr_tonemap_* and the nearest view transform is "
                    "selected instead")   # type: ignore
    evr_particles: BoolProperty(
        name="Particle Emitters (markers)",
        default=True,
        description="Add an Empty for each particle emitter placement, named "
                    "for the effect asset it plays. PLACEMENTS ONLY -- the "
                    "effect definitions are not decoded, so these mark where "
                    "something emits and nothing more")   # type: ignore
    evr_movers: BoolProperty(
        name="Animate Movers",
        default=True,
        description="Keyframe the level geometry that MOVES. Echo VR has no "
                    "animation curves for level geometry -- a moving platform "
                    "is an R15 linear constraint between two anchor actors, "
                    "and the travel is the vector between them. Start and end "
                    "are authored; the TIMING is not (the trigger and duration "
                    "live in CScriptCR, which is not decoded), so the frame "
                    "spacing is a placeholder. Does nothing on a level with no "
                    "movers")   # type: ignore
    evr_mover_frames: IntProperty(
        name="Mover Frames",
        default=48, min=1, max=1000,
        description="Frames for one leg of the placeholder there-and-back "
                    "cycle. Not authored data -- see Animate Movers")   # type: ignore
    evr_lighting: BoolProperty(
        name="Echo VR Lighting",
        default=True,
        description="Load lightmaps.json if the package has one (written by "
                    "scripts/evr_apply_lighting.py): the level's placed lights, "
                    "and the baked irradiance atlases where the geometry carries "
                    "a lightmap UV. This is unrelated to Per-Instance Lightmap "
                    "below, which is the Lone Echo path")   # type: ignore
    evr_lightmaps: BoolProperty(
        name="Baked Lightmaps (UNFINISHED)",
        default=False,
        description="Wire the baked lightmap atlas as EMITTED RADIANCE "
                    "(albedo x baked irradiance) rather than into base colour. "
                    "The atlas holds light that has ALREADY arrived at the "
                    "surface, so feeding it to base colour leaves the renderer "
                    "to light it a second time -- and Echo VR bakes nearly "
                    "everything (mpl_arena_a keeps 2 of 138 lights as dynamic), "
                    "so that came out near-black. Per-instance chart UVs "
                    "measure a median 2.6-texel edge, i.e. well-formed.\n\n"
                    "⚠ UNFINISHED, and OFF by default. What works: SH4 levels "
                    "(mpl_arena_a and the tutorial maps), evaluated against the "
                    "engine's own Geomerics fit. What does NOT: the 12 SG5 "
                    "levels still use a fixed-weight collapse with no "
                    "tangent-space transform; baked specular is not "
                    "implemented; and the two occlusion maps are decoded and "
                    "never applied. Turn it on to experiment, not to trust")   # type: ignore
    show_backfaces: BoolProperty(
        name="Show Backfaces (ignore single-sided)",
        default=True,
        description="Turn OFF backface culling on every imported material. "
                    "80%% of this level's materials are authored single-sided "
                    "(eDoubleSided clear), and the geometry winds correctly "
                    "(verified: 26 of 26 closed meshes have positive signed "
                    "volume), so Material Preview and Rendered hide their back "
                    "faces exactly as the engine does -- while Blender's SOLID "
                    "mode ignores culling and shows them. That mismatch is why "
                    "surfaces look like they vanish when you switch shading "
                    "mode. Enable this to see every surface from both sides; "
                    "it makes the viewport diverge from the game on purpose")   # type: ignore
    evr_dynamic_lights_only: BoolProperty(
        name="Dynamic Lights Only",
        default=False,
        description="Import only the lights the engine puts in its DYNAMIC "
                    "shading list (SGLightParams type 2 / SUN). POINT and SPOT "
                    "lights are the static-bake rig -- their contribution is "
                    "already in the lightmap, so importing both double-counts "
                    "it")   # type: ignore
    instance_lightmap: BoolProperty(
        name="Per-Instance Lightmap",
        default=False,
        description="Honour each instance's OWN baked-lightmap UVs and atlas page "
                    "(package v5). ⚠ Instances of the same mesh carry DIFFERENT "
                    "lightmap UVs, so this breaks instancing: every lightmapped "
                    "instance gets its own mesh datablock (up to 21,394 instead of "
                    "1,050 on station_front). Off = today's behaviour, unchanged")   # type: ignore
    lightmap_texture: StringProperty(
        name="Lightmap Atlas",
        default="",
        subtype="FILE_PATH",
        description="The level's BC6H_UF16 lobe-basis DDS. Blank = search "
                    "'Lightmap Dir' and then the package directory")   # type: ignore
    lightmap_dir: StringProperty(
        name="Lightmap Dir",
        default="",
        subtype="DIR_PATH",
        description="Directory searched for the atlas (identified by DXGI format, "
                    "not by name)")   # type: ignore
    lightmap_intensity: FloatProperty(
        name="Lightmap Intensity", default=1.0, min=0.0,
        description="Scales the baked lighting term (exposure aid). Applied on "
                    "top of each atlas page's recorded `gain`, which is "
                    "restored automatically")   # type: ignore

    def draw(self, context):
        layout = self.layout
        for prop in ("lod_level", "flip_v", "y_up_to_z_up", "import_proxy",
                     "max_instances"):
            layout.prop(self, prop)
        box = layout.box()
        box.label(text="Materials")
        box.prop(self, "auto_materials")
        sub = box.column()
        sub.enabled = not self.auto_materials or bool(self.materials_json)
        sub.prop(self, "materials_json")
        sub.prop(self, "textures_base")
        box = layout.box()
        box.label(text="Echo VR Lighting")
        box.prop(self, "evr_lighting")
        box.prop(self, "evr_movers")
        box.prop(self, "uv_scroll_rate")
        box.prop(self, "world_ambient")
        box.prop(self, "evr_effects")
        box.prop(self, "evr_particles")
        sub_mv = box.column()
        sub_mv.enabled = self.evr_movers
        sub_mv.prop(self, "evr_mover_frames")
        box.prop(self, "evr_armature")
        box.prop(self, "show_backfaces")
        sub = box.column()
        sub.enabled = self.evr_lighting
        sub.prop(self, "evr_dynamic_lights_only")
        sub.prop(self, "evr_lightmaps")
        box = layout.box()
        box.label(text="Lightmap (per instance)")
        box.prop(self, "instance_lightmap")
        sub = box.column()
        sub.enabled = self.instance_lightmap
        sub.prop(self, "lightmap_texture")
        sub.prop(self, "lightmap_dir")
        sub.prop(self, "lightmap_intensity")

    def execute(self, context):
        # Any file inside the package identifies it, so a mis-picked
        # sidecar (materials.json, movers.json, ...) resolves to manifest.json
        # instead of failing. `filter_glob` cannot do this -- it filters by
        # EXTENSION only.
        from . import package_reader
        self.filepath = package_reader.resolve_package_file(
            self.filepath, "manifest.json")
        opts = {
            "flip_v": self.flip_v,
            "y_up_to_z_up": self.y_up_to_z_up,
            "import_proxy": self.import_proxy,
            "max_instances": self.max_instances,
            "uv_scroll_rate": self.uv_scroll_rate,
            "lod_level": int(self.lod_level),
            "auto_materials": self.auto_materials,
            "materials_json": self.materials_json or None,
            "textures_base": self.textures_base or None,
            "instance_lightmap": self.instance_lightmap,
            "lightmap_texture": self.lightmap_texture or None,
            "lightmap_dir": self.lightmap_dir or None,
            "lightmap_intensity": self.lightmap_intensity,
            # ⛔ `instance_lightmap_uv_source` is deliberately NOT exposed: its
            # only other value renders the documented failure mode.
        }
        opts["show_backfaces"] = self.show_backfaces
        try:
            summary = import_lescatter(self.filepath, context, opts)
        except Exception as exc:   # noqa: BLE001
            self.report({"ERROR"}, f"lescatter import failed: {exc}")
            return {"CANCELLED"}

        # Echo VR lighting rides alongside the package rather than inside the
        # manifest, so it is picked up here from the file the user already
        # chose -- there is nothing extra to select.
        if self.evr_lighting:
            self._import_evr_lighting(context, summary)
        if self.evr_movers:
            self._import_evr_movers(context, summary)
        if self.evr_effects or self.evr_particles:
            self._import_evr_effects(context, summary)
        self._tag_texture_overrides(context, summary)
        self._apply_texture_arrays(context, summary)
        if self.evr_armature:
            self._import_evr_armature(context, summary)
        self.report({"INFO"},
                    "Scatter: placed {instances_placed}/{instances_total} instances "
                    "over {meshes_built} meshes ({triangles_unique} unique tris), "
                    "LOD {lod_level} of 0..{lod_max_level}".format(**summary))
        # Surface the material provenance. A silent fall-back to placeholder colours
        # is the single most confusing failure on this path -- it looks like broken
        # materials, not a missing sidecar -- so say which path was taken.
        if not summary.get("materials_from_sidecar"):
            self.report({"WARNING"},
                        "No material sidecar found - every mesh got a flat placeholder "
                        "colour. Point 'Materials JSON' at <master>_materials.json.")
        elif int(summary.get("materials_sidecar_version", 1)) < 2:
            self.report({"WARNING"},
                        "v1 material sidecar: base colour + normal only. Re-run "
                        "scripts/le_scene_materials.py for the v2 full spec (alpha, "
                        "emissive, specular, roughness, blend masks).")
        # Per-instance lightmap: say what it cost and, when it did nothing, WHY.
        # Silence here is the same failure as the material sidecar's — it looks
        # like a broken bake rather than a package without one.
        lm = summary.get("instance_lightmap") or {}
        if lm.get("enabled"):
            if not lm.get("stream_present"):
                # Echo VR packages never carry this section -- it is the Lone
                # Echo `SGPackedInstanceData` stream, and telling an EVR user to
                # "re-export with v5" sends them after something that does not
                # exist for their game. Their baked lighting is lightmaps.json.
                if evr_lighting is not None and evr_lighting.load(self.filepath):
                    self.report({"INFO"},
                                "Per-instance lightmap is a Lone Echo feature and "
                                "this is an Echo VR package -- its baked lighting "
                                "came from lightmaps.json instead.")
                else:
                    self.report({"WARNING"},
                                "Per-instance lightmap: %s. Re-export the package "
                                "with the instance_lightmap section (v5)."
                                % lm.get("stream_reason", "no per-instance UV stream"))
            elif not lm.get("atlas_available"):
                self.report({"WARNING"},
                            "Per-instance lightmap UVs imported but NOT wired: %s"
                            % lm.get("atlas_reason", "no atlas"))
            else:
                self.report({"INFO"},
                            "Per-instance lightmap: {instances_wired} instances wired, "
                            "{datablocks_created} mesh datablocks created "
                            "(+{datablocks_shared} shared), {material_variants} "
                            "material variants, pages {pages}".format(**lm))
        return {"FINISHED"}

    def _import_evr_armature(self, context, summary):
        """Build the armature and bind weights, if the package has a rig."""
        if evr_skeleton is None:
            return
        doc = evr_skeleton.load(self.filepath)
        if doc is None:
            return
        counts = evr_skeleton.summarize(doc)
        armature, names = evr_skeleton.build_armature(
            doc, context, y_up_to_z_up=self.y_up_to_z_up)
        if armature is None:
            return
        # ⛔ NOT `summary["objects_by_mesh"]` -- the summary carries no such
        # key, so asking for it silently bound nothing. Objects already carry
        # `le_mesh_index` (set in `_place_instances`), so read the map back off
        # the collection, exactly as the lightmap path does.
        objects_by_mesh: dict = {}
        coll = bpy.data.collections.get(summary.get("collection") or "")
        for obj in (coll.all_objects if coll else ()):
            index = obj.get("le_mesh_index")
            if index is not None:
                objects_by_mesh.setdefault(int(index), []).append(obj)
        bound = evr_skeleton.bind_weights(
            doc, self.filepath, armature, names, objects_by_mesh)
        parented = bool(doc.get("hierarchy"))
        self.report({"INFO"},
                    "Armature: %d bones, %d mesh(es) skinned, %s."
                    % (counts["bones"], bound["bound"],
                       "parented (%d root%s)"
                       % (len(doc.get("roots") or []),
                          "" if len(doc.get("roots") or []) == 1 else "s")
                       if parented else
                       "PARENTLESS -- hierarchy table not found in this "
                       "skeleton, so posing a bone will not carry its children"))
        hidden = int(armature.get("evr_hidden_sockets") or 0)
        if hidden:
            # Say so rather than letting the bone count silently disagree with
            # what is on screen -- they are hidden, not missing.
            self.report({"INFO"},
                        "Armature: %d unweighted attachment socket%s on the "
                        "model origin hidden (zeroJoint/synchJoint/weapon "
                        "mounts). Unhide from the Armature tab."
                        % (hidden, "" if hidden == 1 else "s"))
        if bound.get("skipped_vertex_mismatch"):
            self.report({"WARNING"},
                        "Armature: %d mesh(es) skipped -- vertex count differs "
                        "from when skeleton.json was written (LOD or split "
                        "mismatch). Re-run evr_apply_skeleton.py."
                        % bound["skipped_vertex_mismatch"])

    def _apply_texture_arrays(self, context, summary):
        """Give each object bound to a texture ARRAY its own slice.

        Without this every object sharing an array material shows slice 0 --
        `mpl_lobby_b2`'s poster boards all displayed the same poster. The
        material never names the array; the mapping comes from
        `texture_arrays.json`. See `evr_texture_arrays` for why the swap is
        linked to the OBJECT rather than the mesh.
        """
        if evr_texture_arrays is None:
            return
        doc = evr_texture_arrays.load(self.filepath)
        if doc is None:
            return

        coll = bpy.data.collections.get(summary.get("collection") or "")
        by_instance = {}
        for obj in (coll.all_objects if coll else ()):
            index = obj.get("le_instance_index")
            if index is not None:
                by_instance[int(index)] = obj
        if not by_instance:
            return

        result = evr_texture_arrays.apply_slices(doc, self.filepath, by_instance)
        if result.get("applied"):
            note = ("Texture arrays: %d object(s) given their own slice over %d "
                    "material variant(s)" % (result["applied"], result["variants"]))
            skipped = [k for k in ("missing_file", "no_base_node", "no_object")
                       if result.get(k)]
            if skipped:
                note += " (skipped: %s)" % ", ".join(
                    "%s=%d" % (k, result[k]) for k in skipped)
            self.report({"INFO"}, note)

    def _tag_texture_overrides(self, context, summary):
        """Mark objects whose texture the engine replaces at runtime.

        `CTextureOverrideCR` (see `scripts/evr_texture_override.py`) says which
        actors get a different texture than their material binds. Most records
        are the rest state and change nothing, but the interesting ones name a
        RENDER TARGET that is not shipped -- a live scoreboard, a match clock.
        Those objects legitimately wear a placeholder (often a UV test grid) and
        there is nothing to apply.

        Left untagged that reads as a broken import, which is exactly how it was
        reported. So the object gets `le_runtime_texture` and a note saying the
        surface is drawn per frame by the engine; nothing about its material is
        changed, because there is no texture to change it to.
        """
        path = Path(self.filepath)
        if path.is_file():
            path = path.parent
        sidecar = path / "texture_overrides.json"
        if not sidecar.is_file():
            return
        try:
            doc = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if doc.get("format") != "evr_texture_overrides":
            return
        models = doc.get("models") or {}
        if not models:
            return

        pkg = scatter_reader.ScatterPackage(Path(self.filepath))
        try:
            meshes = pkg.manifest.get("meshes") or []
        except Exception:                                    # noqa: BLE001
            return
        by_index = {}
        for mesh in meshes:
            entry = models.get(str(mesh.get("name_hash") or "").lower())
            if entry:
                by_index[int(mesh["index"])] = entry

        coll = bpy.data.collections.get(summary.get("collection") or "")
        tagged = runtime = 0
        for obj in (coll.all_objects if coll else ()):
            index = obj.get("le_mesh_index")
            if index is None:
                continue
            entry = by_index.get(int(index))
            if entry is None:
                continue
            obj["le_texture_override"] = entry.get("texture", "")
            obj["le_texture_override_action"] = entry.get("action", "")
            tagged += 1
            if entry.get("action") == "runtime":
                obj["le_runtime_texture"] = True
                obj["le_runtime_texture_note"] = (
                    "the engine draws this surface every frame (score, clock, "
                    "team panel). Its texture is a render target that is not "
                    "shipped, so the placeholder you see is what the package "
                    "contains -- not a failed import.")
                runtime += 1
        if tagged:
            self.report({"INFO"},
                        "Texture overrides: %d object(s) tagged, %d of them "
                        "runtime-drawn surfaces (placeholder is expected)"
                        % (tagged, runtime))

    def _import_evr_effects(self, context, summary):
        """Fog, exposure and particle-emitter markers, when the level has them."""
        if evr_effects is None:
            return
        doc = evr_effects.load(self.filepath)
        if doc is None:
            return
        notes = []
        if self.evr_effects:
            exposure = evr_effects.apply_exposure(doc, context.scene)
            # Fog BEFORE the tonemap: the engine fogs the HDR colour and
            # tonemaps the result, and `apply_tonemap` chains onto the fog mix
            # when it finds one.
            world = evr_effects.apply_world_ambient(
                context.scene, self.world_ambient)
            if world.get("world"):
                notes.append("world %s (the engine has no constant ambient; "
                             "raise World Ambient to see geometry while baked "
                             "lighting is off)" % world["world"])
            fog = evr_effects.apply_fog(doc, context.scene)
            tonemap = evr_effects.apply_tonemap(doc, context.scene)
            if tonemap.get("tonemap", "").startswith("built"):
                # `apply_tonemap` takes exposure over from the view settings so
                # it lands BEFORE the curve, so do not report it twice.
                notes.append("exposure %+.2f EV and the engine's own Hable "
                             "curve, built in the compositor (white point %g)"
                             % (tonemap["exposure_stops"],
                                tonemap["coefficients"]["white"]))
            else:
                if "exposure" in exposure:
                    notes.append("exposure %+.2f EV" % exposure["exposure"])
                if exposure.get("view_transform"):
                    notes.append("view transform %s (nearest to the engine's "
                                 "Hable curve, not a match)"
                                 % exposure["view_transform"])
                if tonemap.get("tonemap"):
                    notes.append("tonemap %s" % tonemap["tonemap"])
            if fog.get("fog") == "built":
                notes.append("fog %s over %.0f-%.0f m at %.2f (compositor "
                             "depth ramp, matching the engine's model; the "
                             "height band is not applied)"
                             % (fog["color"], fog["band"][0], fog["band"][1],
                                fog.get("intensity", 0.0)))
            elif fog.get("fog"):
                notes.append("fog %s" % fog["fog"])
        if self.evr_particles:
            parts = evr_effects.apply_particles(
                doc, context, y_up_to_z_up=self.y_up_to_z_up)
            if parts.get("emitters"):
                notes.append("%d particle emitter marker(s) over %d effect(s) "
                             "-- PLACEMENTS ONLY, the effects are not decoded"
                             % (parts["emitters"], parts["effects"]))
        if notes:
            self.report({"INFO"}, "Echo VR effects: " + "; ".join(notes))

    def _import_evr_movers(self, context, summary):
        """Keyframe the level's moving geometry, when it has any."""
        if evr_movers is None:
            return
        doc = evr_movers.load(self.filepath)
        if doc is None:
            return
        objects_by_instance: dict = {}
        coll = bpy.data.collections.get(summary.get("collection") or "")
        for obj in (coll.all_objects if coll else ()):
            index = obj.get("le_instance_index")
            if index is not None:
                objects_by_instance.setdefault(int(index), []).append(obj)
        result = evr_movers.apply(
            doc, objects_by_instance,
            y_up_to_z_up=self.y_up_to_z_up,
            frames=self.evr_mover_frames,
            scene=context.scene)
        counts = evr_movers.summarize(doc)
        if result.get("animated"):
            self.report(
                {"INFO"},
                "Echo VR movers: %d object(s) keyframed, %d motion(s), travel %s m "
                "-- start/end authored, TIMING IS A PLACEHOLDER (trigger lives "
                "in CScriptCR, not decoded)%s"
                % (result["animated"], counts["movers"],
                   ", ".join(str(d) for d in (result.get("distances") or [])),
                   " -- %d mover(s) had no imported object (LOD filtered?)"
                   % result["no_object"] if result.get("no_object") else ""))
        elif counts["movers"]:
            self.report({"WARNING"},
                        "Echo VR movers: %d in the level but none matched an "
                        "imported object%s"
                        % (counts["movers"],
                           " (%s)" % result["reason"] if result.get("reason") else ""))

    def _import_evr_lighting(self, context, summary):
        """Load the package's `lightmaps.json`, if it has one."""
        if evr_lighting is None:
            return
        doc = evr_lighting.load(self.filepath)
        if doc is None:
            return
        counts = evr_lighting.summarize(doc)

        lights = evr_lighting.import_lights(
            doc, context, y_up_to_z_up=self.y_up_to_z_up,
            dynamic_only=self.evr_dynamic_lights_only)
        if lights.get("created"):
            self.report({"INFO"},
                        "Echo VR lights: %d built from SGLightParams (type, "
                        "colour, intensity, range)%s"
                        % (lights["created"],
                           " -- %d static-bake lights skipped, they are already "
                           "in the lightmap" % lights["skipped_static"]
                           if lights.get("skipped_static") else ""))

        if not counts["atlases"] or not self.evr_lightmaps:
            return
        # Objects already carry `le_mesh_index` / `le_instance_index` (set in
        # `_place_instances`), so both maps are read back off the collection
        # rather than threaded through the summary.
        objects_by_mesh: dict = {}
        objects_by_instance: dict = {}
        coll = bpy.data.collections.get(summary.get("collection") or "")
        for obj in (coll.all_objects if coll else ()):
            index = obj.get("le_mesh_index")
            if index is not None:
                objects_by_mesh.setdefault(int(index), []).append(obj)
            index = obj.get("le_instance_index")
            if index is not None:
                objects_by_instance.setdefault(int(index), []).append(obj)

        total = 0
        notes = []
        # Per-instance first: static-instanced geometry needs its OWN UVs, and
        # a mesh-level wire would put the wrong atlas region on it.
        if counts["bound_instances"]:
            result = evr_lighting.wire_instance_lightmaps(
                doc, self.filepath, objects_by_instance,
                intensity=self.lightmap_intensity,
                y_up_to_z_up=self.y_up_to_z_up)
            total += result.get("wired", 0)
            if result.get("reason"):
                notes.append(result["reason"])
            if result.get("mismatched"):
                notes.append("%d instance(s) skipped on vertex-count mismatch"
                             % result["mismatched"])
        if counts["bound_meshes"]:
            result = evr_lighting.wire_lightmaps(
                doc, self.filepath, objects_by_mesh,
                intensity=self.lightmap_intensity)
            total += result.get("wired", 0)
            if result.get("reason"):
                notes.append(result["reason"])

        if total:
            self.report({"INFO"},
                        "Echo VR lightmaps: %d material(s) wired from %d "
                        "atlas(es)%s" % (total, counts["atlases"],
                                         " -- " + "; ".join(notes) if notes else ""))
        else:
            self.report({"WARNING"},
                        "Echo VR lightmaps: %d atlas(es) loaded but NOT wired -- %s"
                        % (counts["atlases"],
                           "; ".join(notes) or "no reason recorded"))


def menu_func(self, context):
    self.layout.operator(IMPORT_OT_lescatter.bl_idname, text="Lone Echo Scatter (.lescatter)")
