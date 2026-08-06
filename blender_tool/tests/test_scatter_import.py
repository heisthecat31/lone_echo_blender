"""Archive-free core tests for the `.lescatter` reader + placement math + the
material-sidecar consumer (no Blender: `bpy` and `mathutils` are stubbed).

Covers:
  * `scatter_reader` — manifest + instances.bin parsing (on a real synthetic pkg).
  * `compose_instance_matrix` — the B @ (T @ R @ S) placement transform, asserting
    known instances map to the expected Blender world positions.
  * `scatter_import.get_material` — the v2 full-spec PASSTHROUGH and the frozen v1
    flat-field adapter, both driven through the real `import_lescatter`.
  * `uv0`/`uv1` layer construction and the lightmap-id custom properties.

Runs under `python3 blender_tool/tests/run_tests.py` and unchanged under pytest.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import types
from pathlib import Path

# The pure reader lives inside the addon package; import it standalone (WITHOUT
# triggering the package __init__, which imports bpy).
_ADDON = Path(__file__).resolve().parents[1] / "addon" / "lone_echo_import"
if str(_ADDON) not in sys.path:
    sys.path.insert(0, str(_ADDON))

import scatter_reader  # noqa: E402
from scatter_reader import (  # noqa: E402
    ScatterPackage, read_instances, compose_instance_matrix, basis_matrix,
    transform_point, quat_to_matrix,
)
import make_synthetic_scatter  # noqa: E402  (tests dir is on sys.path via run_tests)

TOL = 1e-5


def _vclose(a, b, tol=TOL):
    return all(abs(a[i] - b[i]) <= tol for i in range(3))


def _axis_quat(axis, deg):
    h = math.radians(deg) * 0.5
    s = math.sin(h)
    return (axis[0] * s, axis[1] * s, axis[2] * s, math.cos(h))


# --- reader ------------------------------------------------------------------

def test_instance_stride_is_44():
    assert scatter_reader.INSTANCE_STRIDE == 44


def test_reader_manifest_and_meshes(tmp_path):
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    pkg = ScatterPackage(pkg_dir)
    assert pkg.manifest["format"] == "le_scatter"
    assert pkg.num_meshes == 3
    assert pkg.num_instances == 8
    # contiguous per-mesh runs: offsets are the prefix sum of counts
    offs = [m["instance_offset"] for m in pkg.meshes]
    cnts = [m["instance_count"] for m in pkg.meshes]
    assert cnts == [3, 3, 2]
    assert offs == [0, 3, 6]
    # optional-key coverage: tri has no normals, tall box has no uv0
    by_idx = {m["index"]: m for m in pkg.meshes}
    assert "normals" in by_idx[0] and "uv0" in by_idx[0]
    assert "normals" not in by_idx[1]           # tri
    assert "uv0" not in by_idx[2]               # tall box


def test_reader_geometry_blobs(tmp_path):
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    pkg = ScatterPackage(pkg_dir)
    cube = pkg.meshes[0]
    pos = pkg.positions(cube)
    idx = pkg.indices(cube)
    assert len(pos) == cube["nverts"] * 3 == 24 * 3
    assert len(idx) == cube["nindices"] == 36     # 12 tris
    assert pkg.normals(cube) is not None
    assert pkg.uv0(cube) is not None
    # tri has no normals blob -> None
    assert pkg.normals(pkg.meshes[1]) is None
    # tall box has no uv0 blob -> None
    assert pkg.uv0(pkg.meshes[2]) is None


def test_reader_instances_roundtrip(tmp_path):
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    pkg = ScatterPackage(pkg_dir)
    recs = read_instances(pkg)
    assert len(recs) == 8
    assert [r.index for r in recs] == list(range(8))
    # instance 0: cube at origin, identity, unit scale
    r0 = recs[0]
    assert r0.mesh_index == 0
    assert _vclose(r0.translation, (0.0, 0.0, 0.0))
    assert _vclose(r0.scale, (1.0, 1.0, 1.0))
    # instance 1: cube at (4,0,0), 45deg about game-Y
    r1 = recs[1]
    assert _vclose(r1.translation, (4.0, 0.0, 0.0))
    exp_q = _axis_quat((0, 1, 0), 45)
    assert all(abs(r1.rotation[i] - exp_q[i]) <= 1e-5 for i in range(4))
    # instance 6: tall box elevated in game-Y
    assert recs[6].mesh_index == 2
    assert _vclose(recs[6].translation, (0.0, 3.0, -4.0))


# --- placement math (B @ T @ R @ S) ------------------------------------------

def test_basis_maps_yup_to_zup():
    # game (x,y,z) -> Blender (x,-z,y); pure rotation, determinant +1
    B = basis_matrix(True)
    assert _vclose(transform_point(B, (1, 2, 3)), (1.0, -3.0, 2.0))
    r = [row[:3] for row in B[:3]]
    det = (r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
           - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
           + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0]))
    assert abs(det - 1.0) < 1e-9


def test_basis_identity_when_disabled():
    B = basis_matrix(False)
    assert _vclose(transform_point(B, (1, 2, 3)), (1.0, 2.0, 3.0))


def test_translation_only_instance():
    # mesh origin of a translated, unrotated, unit-scale instance lands at B@T
    M = compose_instance_matrix((2.0, 3.0, 5.0), (0, 0, 0, 1), (1, 1, 1))
    assert _vclose(transform_point(M, (0, 0, 0)), (2.0, -5.0, 3.0))


def test_rotation_maps_point():
    # 90deg about game-Y sends local +X -> game (0,0,-1); B then -> Blender (0,1,0)
    M = compose_instance_matrix((0, 0, 0), _axis_quat((0, 1, 0), 90), (1, 1, 1))
    assert _vclose(transform_point(M, (1, 0, 0)), (0.0, 1.0, 0.0))


def test_nonuniform_scale():
    # scale (2,3,1): local (1,0,0)->(2,0,0)->B(2,0,0); local (0,1,0)->(0,3,0)->B(0,0,3)
    M = compose_instance_matrix((0, 0, 0), (0, 0, 0, 1), (2, 3, 1))
    assert _vclose(transform_point(M, (1, 0, 0)), (2.0, 0.0, 0.0))
    assert _vclose(transform_point(M, (0, 1, 0)), (0.0, 0.0, 3.0))


def test_combined_trs_known_point():
    # rotate local +X by 90deg-Y, scale x2, translate (10,0,0):
    #   S: (1,0,0)->(2,0,0);  R(Y90): ->(0,0,-2);  T: ->(10,0,-2);  B: ->(10,2,0)
    M = compose_instance_matrix((10.0, 0.0, 0.0), _axis_quat((0, 1, 0), 90), (2, 2, 2))
    assert _vclose(transform_point(M, (1, 0, 0)), (10.0, 2.0, 0.0))


def test_quat_identity_is_identity_rotation():
    R = quat_to_matrix(0, 0, 0, 1)
    assert _vclose(transform_point(R, (1, 2, 3)), (1.0, 2.0, 3.0))


# =============================================================================
# scatter_import consumer tests — `bpy` / `mathutils` stubbed, `material_builder`
# replaced by a RECORDER so "the spec reached build_material unmodified" is an
# identity assertion, not a re-derivation. `scatter_reader` is the REAL module.
# =============================================================================

class _Props(dict):
    """Blender ID custom-property behaviour: `x["k"] = v`, `x.keys()`."""

    def __init__(self):
        dict.__init__(self)


class _FakeMaterial(_Props):
    def __init__(self, name):
        _Props.__init__(self)
        self.name = name
        self.diffuse_color = (1.0, 1.0, 1.0, 1.0)
        self.use_nodes = False
        self.node_tree = types.SimpleNamespace(nodes=[], links=[])

    def copy(self):
        """Blender's `ID.copy()`: a new datablock carrying the same custom props."""
        out = _FakeMaterial(self.name)
        out.update(self)
        out.diffuse_color = self.diffuse_color
        out.use_nodes = self.use_nodes
        return out


class _FakePolys(list):
    def foreach_set(self, attr, values):
        for p, v in zip(self, values):
            setattr(p, attr, v)

    def foreach_get(self, attr, out):
        for i, p in enumerate(self):
            out[i] = getattr(p, attr)


class _FakeUVLayer:
    def __init__(self, name, n_loops):
        self.name = name
        self.uv = [0.0] * (n_loops * 2)
        self.data = types.SimpleNamespace(foreach_set=self._set)

    def _set(self, attr, values):
        assert attr == "uv"
        self.uv = list(values)


class _FakeUVLayers(list):
    def __init__(self, mesh):
        list.__init__(self)
        self._mesh = mesh

    def new(self, name="UVMap"):
        layer = _FakeUVLayer(name, len(self._mesh.loops))
        self.append(layer)
        return layer

    def get(self, name):
        return next((l for l in self if l.name == name), None)


class _FakeMesh(_Props):
    def __init__(self, name):
        _Props.__init__(self)
        self.name = name
        self.vertices = []
        self.polygons = _FakePolys()
        self.loops = _FakePolys()
        self.materials = []
        self.uv_layers = _FakeUVLayers(self)
        self.split_normals = None

    def from_pydata(self, verts, edges, faces):
        self.vertices = list(verts)
        self.polygons = _FakePolys(
            types.SimpleNamespace(use_smooth=False, material_index=0) for _ in faces)
        self.loops = _FakePolys()
        for f in faces:
            for vi in f:
                self.loops.append(types.SimpleNamespace(vertex_index=vi))

    def update(self):
        pass

    def normals_split_custom_set_from_vertices(self, vn):
        self.split_normals = list(vn)

    def copy(self):
        """Blender's `Mesh.copy()`: geometry is duplicated, and so are the UV
        layers — a copy must be able to carry DIFFERENT per-loop UVs, which is the
        whole point of the per-instance lightmap path. Material slots are copied
        as a list (the materials themselves are shared until reassigned), and the
        copy is registered in `bpy.data.meshes` exactly as Blender does."""
        out = _FakeMesh(self.name)
        dict.update(out, self)      # `update` is shadowed by Mesh.update()
        out.vertices = self.vertices
        out.polygons = self.polygons
        out.loops = self.loops
        out.materials = list(self.materials)
        out.split_normals = self.split_normals
        for layer in self.uv_layers:
            new = out.uv_layers.new(name=layer.name)
            new.uv = list(layer.uv)
        store = sys.modules["bpy"].data.meshes
        store._by_name[f"{self.name}#copy{len(store._by_name)}"] = out
        return out


class _FakeObject(_Props):
    def __init__(self, name, data):
        _Props.__init__(self)
        self.name = name
        self.data = data
        self.type = "MESH"
        self.matrix_world = None


class _FakeCollection:
    def __init__(self, name):
        self.name = name
        self.objects = _FakeLinkList()
        self.children = _FakeLinkList()


class _FakeLinkList(list):
    def link(self, item):
        self.append(item)


def _install_bpy_stub():
    """Minimal `bpy` + `mathutils` good enough to exercise scatter_import.

    Other test modules install their own thin `bpy` stub for `material_builder`'s
    pure layer, and whichever runs first wins `sys.modules["bpy"]`. So AUGMENT the
    existing module object in place rather than replacing it — replacing it would
    leave an already-imported `material_builder` bound to a different `bpy`.
    """
    if "mathutils" not in sys.modules:
        mu = types.ModuleType("mathutils")
        mu.Matrix = lambda rows: [list(r) for r in rows]
        mu.Vector = lambda v: list(v)
        sys.modules["mathutils"] = mu
    stub = sys.modules.get("bpy") or types.ModuleType("bpy")
    if getattr(stub, "_le_scatter_stub", False):
        return stub
    stub._le_scatter_stub = True
    stub.data = types.SimpleNamespace(
        materials=_FakeIDStore(_FakeMaterial),
        meshes=_FakeIDStore(_FakeMesh),
        objects=_FakeIDStore(_FakeObject, two_args=True),
        collections=_FakeIDStore(_FakeCollection),
        images=_FakeIDStore(_FakeMaterial),
    )
    stub.context = types.SimpleNamespace(
        scene=types.SimpleNamespace(collection=_FakeCollection("Scene Collection")))
    prev_types = getattr(stub, "types", None)
    stub.types = types.SimpleNamespace(
        Operator=type("Operator", (), {}),
        Material=getattr(prev_types, "Material", object))
    props = types.ModuleType("bpy.props")
    for n in ("BoolProperty", "EnumProperty", "IntProperty", "StringProperty",
              "FloatProperty", "PointerProperty"):
        setattr(props, n, lambda *a, **k: None)
    stub.props = props
    sys.modules["bpy"] = stub
    sys.modules["bpy.props"] = props
    bx = types.ModuleType("bpy_extras")
    io_utils = types.ModuleType("bpy_extras.io_utils")
    io_utils.ImportHelper = type("ImportHelper", (), {})
    bx.io_utils = io_utils
    sys.modules["bpy_extras"] = bx
    sys.modules["bpy_extras.io_utils"] = io_utils
    return stub


class _FakeIDStore:
    def __init__(self, cls, two_args=False):
        self._cls = cls
        self._two = two_args
        self._by_name = {}

    def new(self, name, data=None):
        obj = self._cls(name, data) if self._two else self._cls(name)
        self._by_name.setdefault(name, obj)
        return obj

    def get(self, name):
        return self._by_name.get(name)


class _MBRecorder(types.ModuleType):
    """Stand-in `material_builder` that records the EXACT spec object it was given."""

    def __init__(self):
        types.ModuleType.__init__(self, "_le_mb_recorder")
        self.calls = []
        self.lm_calls = []

    def build_material(self, spec, pkg_dir, opts=None):
        self.calls.append({"spec": spec, "pkg_dir": pkg_dir, "opts": opts})
        return _FakeMaterial(str(spec.get("key", "mat")))

    def lightmap_variant(self, mat, lm_spec, opts=None, ctx=None):
        """Mirror of the real per-(material, page) variant cache.

        Deliberately faithful on the three things this front's tests assert: the
        variant is keyed on `<mat>__lm<page>` and cached by that name, the PAGE
        comes from `lm_spec["slice_index"]`, and the UV LAYER the graph would
        sample comes from `lm_spec["uv_layer"]`. `wire_lightmap` itself is not
        re-implemented — it is D3's, tested by `test_lightmap_wiring.py`, and
        needs a real `bpy`."""
        self.lm_calls.append({"mat": getattr(mat, "name", None), "spec": lm_spec,
                              "opts": opts, "ctx": ctx})
        if mat is None or not lm_spec:
            return mat
        page = lm_spec.get("slice_index")
        if not isinstance(page, int) or page < 0 or page == 0xFFFFFFFF:
            return mat
        if mat.get("le_lightmap_page") == page:
            return mat
        name = f"{mat.name}__lm{page}"
        store = sys.modules["bpy"].data.materials
        existing = store.get(name)
        if existing is not None:
            return existing
        var = mat.copy()
        var.name = name
        var["le_lightmap_page"] = page
        var["le_lightmap_wired"] = True
        var["le_lightmap_spec_uv_layer"] = lm_spec.get("uv_layer", "")
        store._by_name[name] = var
        return var


_SI_CACHE = {}


def _scatter_import():
    """Load `scatter_import` as a submodule of a synthetic package so its
    `from . import scatter_reader / material_builder` resolve — the real reader,
    a recorder for the builder."""
    if _SI_CACHE:
        return _SI_CACHE["mod"], _SI_CACHE["mb"]
    _install_bpy_stub()
    pkg = types.ModuleType("_le_addon")
    pkg.__path__ = [str(_ADDON)]
    sys.modules["_le_addon"] = pkg

    sr_spec = importlib.util.spec_from_file_location(
        "_le_addon.scatter_reader", _ADDON / "scatter_reader.py")
    sr = importlib.util.module_from_spec(sr_spec)
    sys.modules["_le_addon.scatter_reader"] = sr
    sr_spec.loader.exec_module(sr)
    pkg.scatter_reader = sr

    mb = _MBRecorder()
    sys.modules["_le_addon.material_builder"] = mb
    pkg.material_builder = mb

    # The REAL `lightmap_builder` — it is bpy-tolerant by construction (`bpy =
    # None` fallback at its imports) and this front consumes its resolver +
    # spec builder, so stubbing it would test a copy of the contract rather than
    # the contract. Only `wire_lightmap`, which needs a live bpy, is kept out of
    # reach (it sits behind `_MBRecorder.lightmap_variant`).
    lb_spec = importlib.util.spec_from_file_location(
        "_le_addon.lightmap_builder", _ADDON / "lightmap_builder.py")
    lb = importlib.util.module_from_spec(lb_spec)
    sys.modules["_le_addon.lightmap_builder"] = lb
    lb_spec.loader.exec_module(lb)
    pkg.lightmap_builder = lb

    si_spec = importlib.util.spec_from_file_location(
        "_le_addon.scatter_import", _ADDON / "scatter_import.py")
    si = importlib.util.module_from_spec(si_spec)
    sys.modules["_le_addon.scatter_import"] = si
    si_spec.loader.exec_module(si)
    _SI_CACHE["mod"] = si
    _SI_CACHE["mb"] = mb
    return si, mb


def _fresh_context():
    """Blender-like `bpy.data` is a global; wipe it between imports so datablock
    names (`scatter_m0_...`) do not carry over from a previous test."""
    bpy = _install_bpy_stub()
    bpy.context.scene.collection = _FakeCollection("Scene Collection")
    for store in (bpy.data.materials, bpy.data.meshes, bpy.data.objects,
                  bpy.data.collections, bpy.data.images):
        store._by_name.clear()
    return bpy.context


# --- the real .lemesh spec used as the v2 payload ----------------------------
# Lifted VERBATIM from a shipped manifest so the fixture is genuine extractor
# output, not a hand-invented shape (`export-validated`).

_REAL_SPEC = {
    "key": "090e3789378a55ab__e4b04c7873d7a3f7",
    "shaderset_hash": "090e3789378a55ab",
    "material_hash": "e4b04c7873d7a3f7",
    "double_sided": False,
    "blend_mode": 0,
    "base_color_factor": [0.04916, 0.04380, 0.04413, 0.24982],
    "emissive_color": [0.0, 0.0, 0.0],
    "emissive_intensity": 1.0,
    "alpha": 1.0,
    "channels": {
        "base_color": {"texture": "f92d355531d3c2ba",
                       "role_key": "layer0_composite_diffuse", "dxgi": 78,
                       "colorspace": "sRGB", "alpha_mode": "CHANNEL_PACKED",
                       "alpha_channel": "A", "layer": 0,
                       "file": "textures/f92d355531d3c2ba.dds"},
        "normal": {"texture": "f8dda3b191b89787",
                   "role_key": "layer0_composite_normals", "dxgi": 83,
                   "colorspace": "Non-Color", "reconstruct_z": True, "layer": 0,
                   "alpha_mode": "NONE", "file": "textures/f8dda3b191b89787.dds"},
        "roughness": {"texture": "4857e9abdb251b02",
                      "role_key": "layer0_composite_components", "dxgi": 71,
                      "colorspace": "Non-Color", "roughness_is_sqrt": True,
                      "ao_channel": "G", "layer": 0,
                      "file": "textures/4857e9abdb251b02.dds"},
        "specular": {"texture": "4be30071f595bcf4",
                     "role_key": "layer0_composite_specular", "dxgi": 78,
                     "colorspace": "sRGB", "spec_albedo_scaled_by": "A", "layer": 0,
                     "file": "textures/4be30071f595bcf4.dds"},
        "emission": {"texture": "30358209b85ad391",
                     "role_key": "layer0_emissive_map", "dxgi": 72,
                     "colorspace": "sRGB", "layer": 0,
                     "file": "textures/30358209b85ad391.dds"},
        "alpha": {"texture": "f92d355531d3c2ba",
                  "role_key": "layer0_composite_diffuse", "dxgi": 78,
                  "colorspace": "sRGB", "alpha_channel": "A", "layer": 0,
                  "from_channel": "base_color",
                  "file": "textures/f92d355531d3c2ba.dds"},
    },
    "render_mode": "CLIP",
    "alpha_source": "BASE_COLOR_ALPHA",
    "alpha_threshold": 0.5,
    "emissive_layer": 0,
    "emissive_scale": 1.0,
    "ior": 1.0,
    "roughness_is_sqrt": True,
    "ao_channel": "G",
    "mattype": 9,
    "mattype_name": "eMTAlphaTested",
    "blend_mode_name": "eBlendOpaque",
    "flags": 24,
    "flag_names": ["eGIReceiver", "eUseAmbientSpecular"],
    "is_emissive": False,
    "named_scalars_resolved": {"layer1_blend_mask_scale": 0.0},
}

# The v1 sidecar entry for the SAME material — every flat field
# `scripts/le_scene_materials.py` emits today.
_V1_ENTRY = {
    "matidx": 0, "shdidx": 0,
    "material_hash": "e4b04c7873d7a3f7",
    "mattype": 9,
    "base_color": [0.04916, 0.04380, 0.04413],
    "basecolor_texture": "f92d355531d3c2ba",
    "basecolor_dds": "0000000000ca77e5_textures/f92d355531d3c2ba.dds",
    "basecolor_role": "layer0_composite_diffuse",
    "normal_texture": "f8dda3b191b89787",
    "owning_archive": "88e3475bb562f454",
    "double_sided": False,
}


def _write_sidecar(tmp_path, payload, name="mats.json"):
    p = Path(tmp_path) / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return str(p)


def _import(tmp_path, materials_json=None, **extra):
    si, mb = _scatter_import()
    mb.calls.clear()
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    opts = {"flip_v": True, "y_up_to_z_up": True, "import_proxy": False,
            "lod_level": -1}
    if materials_json:
        opts["materials_json"] = materials_json
    opts.update(extra)
    summary = si.import_lescatter(pkg_dir, _fresh_context(), opts)
    return si, mb, pkg_dir, summary


# --- T1: v2 full-spec passthrough --------------------------------------------

def test_v2_sidecar_passes_the_spec_through_byte_for_byte(tmp_path):
    """The spec object handed to build_material must be the SAME dict, untouched."""
    mj = _write_sidecar(tmp_path, {
        "version": 2, "master": "0000000000ca77e5",
        "textures_subdir": "0000000000ca77e5_textures",
        "materials": [{"matidx": 0, "shdidx": 0, "spec": _REAL_SPEC}]})
    si, mb, _pkg, summary = _import(tmp_path, mj)

    assert summary["materials_sidecar_version"] == 2
    assert len(mb.calls) == 1, "exactly one (0,0) material should be built"
    got = mb.calls[0]["spec"]
    # Byte-for-byte against what is ON DISK: no key added, dropped, renamed or
    # re-derived between the sidecar and material_builder.
    on_disk = json.loads(Path(mj).read_text())["materials"][0]["spec"]
    assert got == on_disk
    assert json.dumps(got, sort_keys=True) == json.dumps(_REAL_SPEC, sort_keys=True)
    # every rich field the v1 adapter used to drop is present at the builder
    for key in ("mattype", "blend_mode", "render_mode", "alpha", "alpha_threshold",
                "alpha_source", "emissive_intensity", "emissive_scale",
                "roughness_is_sqrt", "ao_channel", "ior", "flags",
                "named_scalars_resolved"):
        assert key in got, key
    assert set(got["channels"]) == {"base_color", "normal", "roughness",
                                    "specular", "emission", "alpha"}


def test_v2_texture_base_is_the_sidecar_directory(tmp_path):
    mj = _write_sidecar(tmp_path, {
        "version": 2, "master": "0000000000ca77e5",
        "materials": [{"matidx": 0, "shdidx": 0, "spec": _REAL_SPEC}]})
    si, mb, _pkg, _s = _import(tmp_path, mj)
    assert Path(mb.calls[0]["pkg_dir"]) == Path(tmp_path)


def test_v2_records_provenance_on_the_material(tmp_path):
    mj = _write_sidecar(tmp_path, {
        "version": 2, "master": "0000000000ca77e5",
        "materials": [{"matidx": 0, "shdidx": 0, "spec": _REAL_SPEC}]})
    si, mb, pkg_dir, _s = _import(tmp_path, mj)
    mat = si.bpy.data.meshes.get("scatter_m0_00000000cube0001").materials[0]
    assert mat["le_matidx"] == 0 and mat["le_shdidx"] == 0
    assert mat["le_sidecar_version"] == 2


def test_v2_higher_versions_also_pass_through(tmp_path):
    """`version >= 2`, not `== 2` — a v3 sidecar must not silently fall back."""
    mj = _write_sidecar(tmp_path, {
        "version": 7, "master": "0000000000ca77e5",
        "materials": [{"matidx": 0, "shdidx": 0, "spec": _REAL_SPEC}]})
    si, mb, _pkg, summary = _import(tmp_path, mj)
    assert summary["materials_sidecar_version"] == 7
    assert mb.calls[0]["spec"] == json.loads(json.dumps(_REAL_SPEC))
    assert "mattype" in mb.calls[0]["spec"]      # not the 6-key v1 adapter


def test_v2_entry_without_a_spec_falls_back_to_the_v1_adapter(tmp_path):
    """Defensive: a malformed v2 row must not crash the whole level import."""
    entry = dict(_V1_ENTRY)
    mj = _write_sidecar(tmp_path, {
        "version": 2, "master": "0000000000ca77e5", "materials": [entry]})
    si, mb, _pkg, _s = _import(tmp_path, mj)
    assert len(mb.calls) == 1
    assert mb.calls[0]["spec"]["key"] == "scatter_mat_0_0"


# --- T1: v1 back-compat (frozen) ---------------------------------------------

def test_v1_sidecar_builds_exactly_the_legacy_six_key_spec(tmp_path):
    mj = _write_sidecar(tmp_path, {
        "master": "0000000000ca77e5", "materials": [_V1_ENTRY]})
    si, mb, _pkg, summary = _import(tmp_path, mj)
    assert summary["materials_sidecar_version"] == 1
    spec = mb.calls[0]["spec"]
    assert set(spec) == {"key", "material_hash", "shaderset_hash", "channels",
                         "base_color_factor", "double_sided"}
    assert spec["key"] == "scatter_mat_0_0"
    assert spec["material_hash"] == "e4b04c7873d7a3f7"
    assert set(spec["channels"]) == {"base_color", "normal"}
    assert spec["channels"]["base_color"] == {
        "file": "0000000000ca77e5_textures/f92d355531d3c2ba.dds",
        "colorspace": "sRGB"}
    assert spec["channels"]["normal"] == {
        "file": "0000000000ca77e5_textures/f8dda3b191b89787.dds",
        "colorspace": "Non-Color", "reconstruct_z": True}
    assert spec["base_color_factor"] == [0.04916, 0.04380, 0.04413, 1.0]
    assert spec["double_sided"] is False


def test_v1_normal_subdir_comes_from_master_when_unspecified(tmp_path):
    mj = _write_sidecar(tmp_path, {"master": "feedface0badc0de",
                                   "materials": [_V1_ENTRY]})
    si, mb, _pkg, _s = _import(tmp_path, mj)
    assert mb.calls[0]["spec"]["channels"]["normal"]["file"].startswith(
        "feedface0badc0de_textures/")


def test_no_sidecar_uses_the_distinct_colour_placeholder(tmp_path):
    si, mb, _pkg, summary = _import(tmp_path)
    assert mb.calls == []                     # material_builder never invoked
    assert summary["materials_sidecar_version"] == 0
    assert summary["materials"] == 3          # 3 distinct (matidx, shdidx) pairs
    mesh = si.bpy.data.meshes.get("scatter_m0_00000000cube0001")
    assert mesh.materials[0].name == "__le_scatter_mat_0_0"


def test_uncovered_pairs_still_get_the_placeholder_under_v2(tmp_path):
    """Only (0,0) is in the sidecar; (1,1) and (2,2) must not vanish."""
    mj = _write_sidecar(tmp_path, {
        "version": 2, "master": "0000000000ca77e5",
        "materials": [{"matidx": 0, "shdidx": 0, "spec": _REAL_SPEC}]})
    si, mb, _pkg, summary = _import(tmp_path, mj)
    assert summary["materials"] == 3
    assert summary["materials_from_sidecar"] == 1
    assert len(mb.calls) == 1
    assert si.bpy.data.meshes.get("scatter_m1_00000000tri00002") \
        .materials[0].name == "__le_scatter_mat_1_1"


# --- T1: multi-material slots must not regress -------------------------------

def test_multi_material_slots_survive_the_v2_path(tmp_path):
    si, mb = _scatter_import()
    mb.calls.clear()
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    manifest = json.loads((pkg_dir / "manifest.json").read_text())
    # split the cube's 36 indices into two draws with DIFFERENT bindings
    manifest["version"] = 2
    manifest["meshes"][0]["draws"] = [
        {"matidx": 0, "shdidx": 0, "idx_start": 0, "idx_count": 18},
        {"matidx": 7, "shdidx": 9, "idx_start": 18, "idx_count": 18},
    ]
    (pkg_dir / "manifest.json").write_text(json.dumps(manifest))
    mj = _write_sidecar(tmp_path, {
        "version": 2, "master": "0000000000ca77e5",
        "materials": [{"matidx": 0, "shdidx": 0, "spec": _REAL_SPEC},
                      {"matidx": 7, "shdidx": 9, "spec": _REAL_SPEC}]})
    si.import_lescatter(pkg_dir, _fresh_context(),
                        {"flip_v": True, "lod_level": -1, "materials_json": mj})
    mesh = si.bpy.data.meshes.get("scatter_m0_00000000cube0001")
    assert len(mesh.materials) == 2
    slots = [p.material_index for p in mesh.polygons]
    assert slots == [0] * 6 + [1] * 6        # 12 tris, 6 per draw
    assert len(mb.calls) == 2                 # one build per distinct pair


# --- T2: uv1 -----------------------------------------------------------------

def _add_uv1(pkg_dir, mesh_index, uv_flat, version=4):
    """Write a uv1 blob + key onto an existing package (what the extractor will do)."""
    import array as _array
    manifest = json.loads((Path(pkg_dir) / "manifest.json").read_text())
    manifest["version"] = version
    rel = f"blobs/m{mesh_index}_uv1.bin"
    _array.array("f", uv_flat).tofile(open(Path(pkg_dir) / rel, "wb"))
    for m in manifest["meshes"]:
        if m["index"] == mesh_index:
            m["uv1"] = rel
    (Path(pkg_dir) / "manifest.json").write_text(json.dumps(manifest))
    return manifest


def test_uv1_absent_imports_exactly_as_today(tmp_path):
    si, mb, pkg_dir, _s = _import(tmp_path)
    pkg = ScatterPackage(pkg_dir)
    assert pkg.uv1(pkg.meshes[0]) is None
    mesh = si.bpy.data.meshes.get("scatter_m0_00000000cube0001")
    assert [l.name for l in mesh.uv_layers] == ["uv0"]
    # the uv0-less mesh (tall box) still gets no UV layer at all
    assert [l.name for l in
            si.bpy.data.meshes.get("scatter_m2_0000000tallbox03").uv_layers] == []


def test_uv1_is_imported_as_a_second_layer_with_the_same_flip(tmp_path):
    si, mb = _scatter_import()
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    # 24 verts; make uv1 trivially distinguishable from uv0
    uv1 = []
    for vi in range(24):
        uv1 += [vi / 24.0, 0.25]
    _add_uv1(pkg_dir, 0, uv1)

    si.import_lescatter(pkg_dir, _fresh_context(), {"flip_v": True, "lod_level": -1})
    mesh = si.bpy.data.meshes.get("scatter_m0_00000000cube0001")
    assert [l.name for l in mesh.uv_layers] == ["uv0", "uv1"]
    layer = mesh.uv_layers.get("uv1")
    loop_v = [l.vertex_index for l in mesh.loops]
    # flip_v applies to uv1 exactly as it does to uv0: v -> 1 - v
    for li, vi in enumerate(loop_v):
        assert abs(layer.uv[li * 2] - vi / 24.0) < 1e-6
        assert abs(layer.uv[li * 2 + 1] - 0.75) < 1e-6


def test_uv1_flip_off_is_honoured_too(tmp_path):
    si, mb = _scatter_import()
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    _add_uv1(pkg_dir, 0, [0.5, 0.25] * 24)
    si.import_lescatter(pkg_dir, _fresh_context(), {"flip_v": False, "lod_level": -1})
    layer = si.bpy.data.meshes.get("scatter_m0_00000000cube0001").uv_layers.get("uv1")
    assert abs(layer.uv[1] - 0.25) < 1e-6


def test_short_uv1_blob_is_skipped_not_crashed(tmp_path):
    si, mb = _scatter_import()
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    _add_uv1(pkg_dir, 0, [0.5, 0.5] * 4)          # 4 verts of UV for a 24-vert mesh
    si.import_lescatter(pkg_dir, _fresh_context(), {"flip_v": True, "lod_level": -1})
    mesh = si.bpy.data.meshes.get("scatter_m0_00000000cube0001")
    assert [l.name for l in mesh.uv_layers] == ["uv0"]


# --- T2: lightmap ids --------------------------------------------------------

def test_lightmap_ids_default_to_the_lemesh_sentinels():
    ids = ScatterPackage.lightmap_ids({"index": 0})
    assert ids == (0, 0xFFFFFFFF, 0)


def test_lightmap_ids_read_from_the_mesh_entry():
    ids = ScatterPackage.lightmap_ids(
        {"lightmap_index": 3, "lm_slice_index": 10, "numlobes": 2})
    assert ids == (3, 10, 2)


def test_lightmap_props_on_mesh_and_instances(tmp_path):
    si, mb = _scatter_import()
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    manifest = json.loads((pkg_dir / "manifest.json").read_text())
    manifest["version"] = 4
    manifest["meshes"][0].update(
        {"lightmap_index": 4, "lm_slice_index": 3, "numlobes": 2})
    (pkg_dir / "manifest.json").write_text(json.dumps(manifest))

    summary = si.import_lescatter(pkg_dir, _fresh_context(),
                                  {"flip_v": True, "lod_level": -1})
    mesh = si.bpy.data.meshes.get("scatter_m0_00000000cube0001")
    assert mesh["le_lightmap_index"] == 4
    assert mesh["le_lm_slice_index"] == 3
    assert mesh["le_lightmap_numlobes"] == 2
    coll = si.bpy.context.scene.collection.children[0]
    cube_objs = [o for o in coll.objects if o["le_mesh_index"] == 0]
    assert len(cube_objs) == 3
    for o in cube_objs:
        assert o["le_lm_slice_index"] == 3 and o["le_lightmap_index"] == 4
    assert summary["instances_placed"] == 8


def test_lm_slice_sentinel_is_stringified_not_overflowed(tmp_path):
    """0xFFFFFFFF does not fit a Blender signed-32-bit ID property; the .lemesh
    path stores such values as strings and a consumer must accept "4294967295"."""
    si, mb = _scatter_import()
    assert si._int_prop(0xFFFFFFFF) == "4294967295"
    assert si._int_prop(3) == 3
    assert si._int_prop(-1) == -1
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    si.import_lescatter(pkg_dir, _fresh_context(), {"flip_v": True, "lod_level": -1})
    mesh = si.bpy.data.meshes.get("scatter_m0_00000000cube0001")
    assert mesh["le_lm_slice_index"] == "4294967295"
    coll = si.bpy.context.scene.collection.children[0]
    assert coll.objects[0]["le_lm_slice_index"] == "4294967295"


# --- T2: producer -> consumer round trip through the REAL v4 writer ----------
# `scripts/le_scene_extract.write_package` is the producer (not mine); this pins
# that its v4 keys and this consumer agree without either side guessing.

def test_v4_writer_uv1_and_lightmap_ids_reach_blender(tmp_path):
    _root = Path(__file__).resolve().parents[2]
    if str(_root / "scripts") not in sys.path:
        sys.path.insert(0, str(_root / "scripts"))
    import le_scene_extract as lse

    si, mb = _scatter_import()
    quad_pos = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0]
    quad_idx = [0, 1, 2, 0, 2, 3]
    common = dict(aabb_min=(0.0, 0.0, 0.0), aabb_max=(1.0, 1.0, 0.0),
                  instance_offset=0, instance_count=1,
                  positions=quad_pos, indices=quad_idx,
                  uv0=[0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0],
                  draws=[{"matidx": 1, "shdidx": 2, "idx_start": 0, "idx_count": 6}])
    lit = lse.SceneMesh(index=0, name_hash=0xAAAA0000BBBB0001, matidx=1, shdidx=2,
                        uv1=[0.0, 0.0, 0.5, 0.0, 0.5, 0.5, 0.0, 0.5],
                        lightmap_index=0, lm_slice_index=3, numlobes=4, **common)
    unlit = lse.SceneMesh(index=1, name_hash=0xAAAA0000BBBB0002, matidx=1, shdidx=2,
                          **common)          # no uv1, sentinel lightmap ids
    insts = [lse.SceneInstance(mesh_index=0, translation=(0.0, 0.0, 0.0),
                               rotation=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0)),
             lse.SceneInstance(mesh_index=1, translation=(4.0, 0.0, 0.0),
                               rotation=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0))]
    out = lse.write_package(Path(tmp_path) / "v4.lescatter", "cafebabe12345678",
                            [lit, unlit], insts)

    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["version"] >= 4
    pkg = ScatterPackage(out)
    assert pkg.uv1(pkg.meshes[0]) is not None
    assert pkg.uv1(pkg.meshes[1]) is None
    assert ScatterPackage.lightmap_ids(pkg.meshes[0]) == (0, 3, 4)
    assert ScatterPackage.lightmap_ids(pkg.meshes[1]) == (
        0xFFFFFFFF, 0xFFFFFFFF, 0)

    si.import_lescatter(out, _fresh_context(), {"flip_v": True, "lod_level": -1})
    lit_mesh = si.bpy.data.meshes.get("scatter_m0_aaaa0000bbbb0001")
    unlit_mesh = si.bpy.data.meshes.get("scatter_m1_aaaa0000bbbb0002")
    assert [l.name for l in lit_mesh.uv_layers] == ["uv0", "uv1"]
    assert [l.name for l in unlit_mesh.uv_layers] == ["uv0"]
    assert lit_mesh["le_lm_slice_index"] == 3
    assert lit_mesh["le_lightmap_numlobes"] == 4
    # the sentinel round-trips as the STRING form the .lemesh path also emits
    assert unlit_mesh["le_lm_slice_index"] == "4294967295"
    assert unlit_mesh["le_lightmap_index"] == "4294967295"


# =============================================================================
# Render fixture builder (NOT a test — consumed by tests/blender_scatter_render.py)
# =============================================================================
#
# Builds a MATCHED PAIR of sidecars over the same `.lescatter` package so the
# before/after renders differ in ONE variable: how much of the material spec the
# consumer forwards.
#
#   * `d2_before_v1_materials.json` — the flat fields
#     `scripts/le_scene_materials.py` emits today (material_hash, mattype,
#     base_color, basecolor_dds, normal_texture, double_sided).
#   * `d2_after_v2_materials.json`  — the SAME materials, each carrying its full
#     `.lemesh` spec verbatim under `"spec"`.
#
# The specs are real extractor output lifted from `exports/fixtures_mat3/*.lemesh`
# (`export-validated`). What is SYNTHETIC is only the (matidx, shdidx) -> spec
# assignment: those specs come from archive `0703fd2acd5803e9` while the scatter
# master is `942c829457a04a62`, so this fixture proves the CONSUMER, not the
# resolver. The resolver's own pairing is D1's `_materials.json` v2.

# Channel weights, tuned to select specs that make the v1->v2 delta LEGIBLE
# rather than merely large: a base-colour map is required for the surface to read
# at all (the v1 path can carry one, so it is not itself the delta), and on top of
# it the routed channels the v1 path CANNOT carry are what the picture must show.
_CHANNEL_WEIGHT = {
    "base_color": 60,          # v1 can carry this — needed so the render is legible
    "normal": 5,               # v1 can carry this too
    "emission": 40, "secondary_emission": 30,
    "alpha": 30, "opacity": 15, "transmission": 20,
    "specular": 15, "roughness": 15, "blend_mask": 10, "flowmap": 0,
}


def _spec_richness(spec):
    ch = spec.get("channels") or {}
    score = sum(_CHANNEL_WEIGHT.get(c, 5) for c in ch)
    if spec.get("render_mode") in ("BLEND", "CLIP"):
        score += 25
    if spec.get("is_emissive"):
        score += 20
    score += min(int(float(spec.get("emissive_intensity", 1.0))), 25)
    return score


def _rebase_dds(node, src_dir, dst_dir, master):
    """Copy every `"file"` a spec references into `<master>_textures/` and rewrite
    the path to be relative to the SIDECAR directory (the v2 contract)."""
    import shutil
    if isinstance(node, dict):
        f = node.get("file")
        if isinstance(f, str) and f.endswith(".dds"):
            src = Path(src_dir) / f
            if src.exists():
                dst = Path(dst_dir) / f"{master}_textures" / src.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    shutil.copyfile(src, dst)
                node["file"] = f"{master}_textures/{src.name}"
            else:
                node.pop("file", None)
        for v in node.values():
            _rebase_dds(v, src_dir, dst_dir, master)
    elif isinstance(node, list):
        for v in node:
            _rebase_dds(v, src_dir, dst_dir, master)
    return node


def build_d2_render_fixture(out_dir, scatter_pkg, fixtures_mat3, master):
    """-> (v1_sidecar_path, v2_sidecar_path, stats dict)."""
    import copy
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads((Path(scatter_pkg) / "manifest.json").read_text())
    weight = {}
    for m in manifest["meshes"]:
        for d in ScatterPackage.draws(m):
            key = (int(d["matidx"]), int(d["shdidx"]))
            weight[key] = weight.get(key, 0) + int(m.get("instance_count", 0))
    pairs = sorted(weight, key=lambda k: (-weight[k], k))

    cands = []
    for pkg in sorted(Path(fixtures_mat3).glob("*.lemesh")):
        mf = json.loads((pkg / "manifest.json").read_text())
        for spec in mf.get("materials", []):
            if spec.get("channels"):
                cands.append((pkg, spec))
    cands.sort(key=lambda ps: (-_spec_richness(ps[1]), ps[1]["key"]))

    v1, v2 = [], []
    for i, pair in enumerate(pairs):
        if i >= len(cands):
            break
        src_pkg, spec = cands[i]
        spec = _rebase_dds(copy.deepcopy(spec), src_pkg, out_dir, master)
        matidx, shdidx = pair
        v2.append({"matidx": matidx, "shdidx": shdidx, "spec": spec})
        ch = spec.get("channels") or {}
        bc = ch.get("base_color") or {}
        nm = ch.get("normal") or {}
        v1.append({
            "matidx": matidx, "shdidx": shdidx,
            "material_hash": spec.get("material_hash"),
            "mattype": spec.get("mattype", 0),
            "base_color": list(spec.get("base_color_factor", [1, 1, 1, 1]))[:3],
            "basecolor_texture": bc.get("texture"),
            "basecolor_dds": bc.get("file"),
            "basecolor_role": bc.get("role_key"),
            "normal_texture": nm.get("texture"),
            "owning_archive": None,
            "double_sided": bool(spec.get("double_sided", False)),
        })

    p1 = out_dir / "d2_before_v1_materials.json"
    p2 = out_dir / "d2_after_v2_materials.json"
    p1.write_text(json.dumps({"master": master, "materials": v1}, indent=1))
    p2.write_text(json.dumps({"version": 2, "master": master,
                              "textures_subdir": f"{master}_textures",
                              "materials": v2}, indent=1))
    stats = {
        "pairs": len(v2),
        "blended": sum(1 for e in v2
                       if e["spec"].get("render_mode") == "BLEND"),
        "clipped": sum(1 for e in v2
                       if e["spec"].get("render_mode") == "CLIP"),
        "emissive_maps": sum(1 for e in v2
                             if "emission" in (e["spec"].get("channels") or {})),
        "with_alpha_channel": sum(1 for e in v2
                                  if "alpha" in (e["spec"].get("channels") or {})),
        "v1_with_basecolor_dds": sum(1 for e in v1 if e["basecolor_dds"]),
    }
    (out_dir / "d2_fixture_stats.json").write_text(json.dumps(stats, indent=1))
    return str(p1), str(p2), stats


def derive_v1_sidecar_from_v2(v2_path, out_path):
    """Reduce a REAL v2 sidecar to the v1 flat fields — the honest "before".

    Emits exactly the keys `scripts/le_scene_materials.py` wrote before the v2
    upgrade (material_hash, mattype, base_color, basecolor_*, normal_texture,
    double_sided) taken from each entry's own `spec`, so a before/after render
    pair over the same package differs in ONE variable: how much of the spec the
    consumer forwards. Same materials, same textures, same geometry, same camera.
    """
    src = json.loads(Path(v2_path).read_text())
    out = []
    for e in src.get("materials", []):
        spec = e.get("spec") or {}
        ch = spec.get("channels") or {}
        bc = ch.get("base_color") or {}
        nm = ch.get("normal") or {}
        out.append({
            "matidx": e["matidx"], "shdidx": e["shdidx"],
            "material_hash": spec.get("material_hash"),
            "mattype": spec.get("mattype", 0),
            "base_color": list(spec.get("base_color_factor", [1, 1, 1, 1]))[:3],
            "basecolor_texture": bc.get("texture"),
            "basecolor_dds": bc.get("file"),
            "basecolor_role": bc.get("role_key"),
            "normal_texture": nm.get("texture"),
            "owning_archive": None,
            "double_sided": bool(spec.get("double_sided", False)),
        })
    Path(out_path).write_text(json.dumps(
        {"master": src.get("master", ""), "materials": out}, indent=1))
    return str(out_path)


_REAL_V2 = (Path(__file__).resolve().parents[1] / "exports"
            / "942c829457a04a62_materials.json")


def test_real_v2_sidecar_reaches_the_builder_intact(tmp_path):
    """Integration check against the REAL resolver output when it is on disk.

    Not a fixture: this is `scripts/le_scene_materials.py`'s own v2 sidecar for
    the station_front master. Skips loudly when it has not been generated.
    """
    from unittest import SkipTest
    if not _REAL_V2.exists():
        raise SkipTest(
            f"{_REAL_V2.name} is absent — `blender_tool/exports/` is gitignored "
            f"extracted game data, so a clean checkout has none. Write it with "
            f"`python.exe scripts/le_scene_materials.py 942c829457a04a62` to "
            f"make this test able to run. ⛔ WHILE THIS SKIP IS ACTIVE NOTHING "
            f"CHECKS THAT A REAL RESOLVER-PRODUCED v2 SIDECAR REACHES "
            f"`material_builder.build_material` UNADAPTED.")
    doc = json.loads(_REAL_V2.read_text())
    if int(doc.get("version", 1)) < 2:
        raise SkipTest(
            f"{_REAL_V2.name} is schema v{int(doc.get('version', 1))}, not v2 — "
            f"the v1 envelope carries no `spec`, so there is nothing to hand "
            f"the builder. Regenerate it with `python.exe "
            f"scripts/le_scene_materials.py 942c829457a04a62` to make this "
            f"test able to run. ⛔ WHILE THIS SKIP IS ACTIVE NOTHING CHECKS "
            f"THAT A REAL RESOLVER-PRODUCED v2 SIDECAR REACHES "
            f"`material_builder.build_material` UNADAPTED.")
    entries = {(e["matidx"], e["shdidx"]): e for e in doc["materials"]}
    si, mb = _scatter_import()
    mb.calls.clear()
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    # remap the synthetic package's pairs onto three real sidecar pairs
    real_pairs = sorted(entries)[:3]
    manifest = json.loads((pkg_dir / "manifest.json").read_text())
    for m, pair in zip(manifest["meshes"], real_pairs):
        m["matidx"], m["shdidx"] = pair
    (pkg_dir / "manifest.json").write_text(json.dumps(manifest))

    si.import_lescatter(pkg_dir, _fresh_context(),
                        {"flip_v": True, "lod_level": -1,
                         "materials_json": str(_REAL_V2)})
    assert len(mb.calls) == 3
    for call, pair in zip(mb.calls, real_pairs):
        assert call["spec"] == entries[pair]["spec"]
        assert Path(call["pkg_dir"]) == _REAL_V2.parent
    # the real sidecar carries what the v1 adapter provably cannot
    rich = [c["spec"] for c in mb.calls]
    assert any("mattype" in s for s in rich)
    assert any(len(s.get("channels") or {}) > 2 for s in rich)


# --- orchestrator: sidecar AUTO-DISCOVERY ------------------------------------
# The extractor writes `<master>_materials.json` NEXT TO the package directory.
# Before auto-discovery, an import that did not pass `materials_json` fell back to
# flat placeholder colours -- which reads as "the materials are broken", not as
# "you forgot a path". These lock the default in.

def test_sidecar_is_auto_discovered_next_to_the_package(tmp_path):
    """`<pkg>.lescatter/../<master>_materials.json` is found with no opts at all."""
    si, mb = _scatter_import()
    mb.calls.clear()
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    master = json.loads((pkg_dir / "manifest.json").read_text())["master"]
    _write_sidecar(tmp_path, {
        "version": 2, "master": master,
        "materials": [{"matidx": 0, "shdidx": 0, "spec": _REAL_SPEC}]},
        name=f"{master}_materials.json")

    summary = si.import_lescatter(pkg_dir, _fresh_context(),
                                  {"flip_v": True, "lod_level": -1})
    assert summary["materials_sidecar_version"] == 2
    assert summary["materials_from_sidecar"] == 1
    assert len(mb.calls) == 1
    assert mb.calls[0]["spec"] == _REAL_SPEC


def test_auto_materials_false_forces_the_placeholder_path(tmp_path):
    """The escape hatch must actually escape -- sidecar present, still ignored."""
    si, mb = _scatter_import()
    mb.calls.clear()
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    master = json.loads((pkg_dir / "manifest.json").read_text())["master"]
    _write_sidecar(tmp_path, {
        "version": 2, "master": master,
        "materials": [{"matidx": 0, "shdidx": 0, "spec": _REAL_SPEC}]},
        name=f"{master}_materials.json")

    summary = si.import_lescatter(pkg_dir, _fresh_context(),
                                  {"flip_v": True, "lod_level": -1,
                                   "auto_materials": False})
    assert mb.calls == []
    assert summary["materials_sidecar_version"] == 0


def test_explicit_materials_json_beats_auto_discovery(tmp_path):
    """An explicit path wins even when a differently-named auto candidate exists."""
    si, mb = _scatter_import()
    mb.calls.clear()
    pkg_dir = make_synthetic_scatter.write_synthetic_scatter(tmp_path)
    master = json.loads((pkg_dir / "manifest.json").read_text())["master"]
    # the auto candidate is v1 ...
    _write_sidecar(tmp_path, {
        "master": master,
        "materials": [{"matidx": 0, "shdidx": 0, "material_hash": "dead",
                       "base_color": [1.0, 0.0, 0.0]}]},
        name=f"{master}_materials.json")
    # ... the explicit one is v2
    mj = _write_sidecar(tmp_path, {
        "version": 2, "master": master,
        "materials": [{"matidx": 0, "shdidx": 0, "spec": _REAL_SPEC}]},
        name="explicit.json")

    summary = si.import_lescatter(pkg_dir, _fresh_context(),
                                  {"flip_v": True, "lod_level": -1,
                                   "materials_json": mj})
    assert summary["materials_sidecar_version"] == 2
    assert mb.calls[0]["spec"] == _REAL_SPEC


def test_auto_discovery_finds_the_real_station_front_sidecar():
    """Against the SHIPPED artifacts: the v2 sidecar sits where the probe looks."""
    from unittest import SkipTest
    pkg = Path(__file__).resolve().parent.parent / "exports" / "942c829457a04a62.lescatter"
    if not (pkg / "manifest.json").is_file():
        raise SkipTest(
            "no `.lescatter` export in this checkout — sidecar auto-discovery "
            "has nothing to look for. Extract one with "
            "`python.exe scripts/le_scene_extract.py <hash>`.")
    master = json.loads((pkg / "manifest.json").read_text())["master"]
    cand = pkg.parent / f"{master}_materials.json"
    if not cand.is_file():
        raise SkipTest(
            f"the level export is present but its materials sidecar is not "
            f"({cand.name}). Run `python.exe scripts/le_scene_materials.py "
            f"{master}` to write it. ⛔ WHILE THIS SKIP IS ACTIVE SIDECAR "
            "AUTO-DISCOVERY IS UNTESTED AGAINST A REAL EXPORT.")
    assert int(json.loads(cand.read_text()).get("version", 1)) >= 2
