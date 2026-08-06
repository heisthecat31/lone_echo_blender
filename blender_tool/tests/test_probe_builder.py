"""Unit tests for the pure-python layer of the addon's `probe_builder`.

`probe_builder` guards its `import bpy`, so it loads unchanged outside Blender
with `PB.bpy is None` — but it lives inside the `lone_echo_import` package whose
`__init__` does NOT guard, so it is loaded straight off disk here, the same way
`test_material_builder_nodes` loads `material_builder`.

The Blender half — DDS row order, the node graph, the before/after render — is
asserted by `tests/blender_probe_probe.py` and `tests/blender_probe_render.py`,
which must run inside Blender.
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
if str(BLENDER_TOOL) not in sys.path:
    sys.path.insert(0, str(BLENDER_TOOL))
PB_PATH = BLENDER_TOOL / "addon" / "lone_echo_import" / "probe_builder.py"

from le_mesh import reflection_probe as RP    # noqa: E402

_PB = None


def _pb():
    global _PB
    if _PB is not None:
        return _PB
    spec = importlib.util.spec_from_file_location("_le_probe_builder", PB_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _PB = mod
    return mod


DIM = 8


def _strip(fn, flipped):
    """A `DIM x 6*DIM` RGBA buffer whose FILE row order is top-down.

    `fn(face, x, y) -> (r, g, b)` with `y == 0` the face's first stored row.
    """
    px = [0.0] * (DIM * DIM * 6 * 4)
    for f in range(6):
        for y in range(DIM):
            for x in range(DIM):
                row = ((5 - f) * DIM + (DIM - 1 - y)) if flipped else (f * DIM + y)
                i = (row * DIM + x) * 4
                r, g, b = fn(f, x, y)
                px[i], px[i + 1], px[i + 2], px[i + 3] = r, g, b, 1.0
    return px


# ---------------------------------------------------------------------------

def test_module_loads_outside_blender():
    """It must import with no real Blender.

    ⚠ `bpy` may be a STUB here rather than absent: `test_material_builder_nodes`
    injects one into `sys.modules` and the suite shares a process.  What matters
    is that `probe_builder` loads and that nothing real got imported.
    """
    PB = _pb()
    assert PB.bpy is None or not hasattr(PB.bpy, "app")
    assert PB.MANIFEST_KEY == RP.MANIFEST_KEY == "reflection_probes"


def test_mode_defaults_to_off():
    PB = _pb()
    assert PB.DEFAULT_MODE == PB.MODE_OFF == "off"
    assert PB.resolved_mode(None) == "off"
    assert PB.resolved_mode({}) == "off"
    assert PB.resolved_mode({"probe_mode": "specular"}) == "specular"
    assert PB.resolved_mode({"probe_mode": "nonsense"}) == "off"
    assert PB.resolved_mode({"probe_mode": None}) == "off"


def test_variant_name_carries_the_probe():
    PB = _pb()
    assert PB.variant_name("wall", 7) == "wall__probe7"
    # composes with the lightmap page suffix, because it is built from mat.name
    assert PB.variant_name("wall__lm3", 7) == "wall__lm3__probe7"


def test_strip_texel_reads_the_flipped_buffer_blender_hands_back():
    PB = _pb()
    px = _strip(lambda f, x, y: (float(f), float(y), float(x)), flipped=True)
    for f in range(6):
        for y in (0, DIM - 1):
            for x in (0, DIM - 1):
                assert PB.strip_texel(px, DIM, f, x, y) == (float(f), float(y), float(x))


def test_strip_texel_unflipped_is_a_different_mapping():
    PB = _pb()
    px = _strip(lambda f, x, y: (float(f), float(y), float(x)), flipped=False)
    assert PB.strip_texel(px, DIM, 0, 0, 0, flipped=False) == (0.0, 0.0, 0.0)
    # reading the same buffer with the wrong convention lands on face 5
    assert PB.strip_texel(px, DIM, 0, 0, 0, flipped=True)[0] == 5.0


def test_sampler_clamps_out_of_range_uv():
    PB = _pb()
    px = _strip(lambda f, x, y: (float(f), float(y), float(x)), flipped=True)
    s = PB.make_strip_sampler(px, DIM)
    assert s(2, -5.0, -5.0) == (2.0, 0.0, 0.0)
    assert s(2, 5.0, 5.0) == (2.0, float(DIM - 1), float(DIM - 1))


def test_equirect_from_strip_places_the_faces_the_convention_says():
    PB = _pb()
    px = _strip(lambda f, x, y: (float(f), 0.0, 0.0), flipped=True)
    w, h = 8, 4
    out = PB.equirect_pixels_from_strip(px, DIM, w, h)
    assert len(out) == w * h * 4

    def face_at(ix, iy):
        return int(round(out[(iy * w + ix) * 4]))

    assert face_at(w // 2, h - 1) == 2      # Blender +Z (up)  -> game +Y
    assert face_at(w // 2, 0) == 3          # Blender -Z (down) -> game -Y
    assert face_at(w // 2, h // 2) == 0     # Blender +X       -> game +X


def test_seam_scorer_prefers_the_convention_the_buffer_was_written_with():
    """A cube that is a SMOOTH function of direction has no seams — unless the
    reader's face/row convention is wrong.  The scorer must say so."""
    PB = _pb()

    def smooth(face, x, y):
        u = (x + 0.5) / DIM
        v = (y + 0.5) / DIM
        d = RP.face_uv_to_direction(face, u, v)
        n = math.sqrt(sum(c * c for c in d))
        val = 1.0 + 0.9 * (d[0] / n)        # strictly positive, direction-only
        return (val, val, val)

    px = _strip(smooth, flipped=True)
    pairs = PB._edge_direction_pairs(DIM, samples=32)
    assert pairs, "the edge sampler must find cross-seam pairs"
    right = PB.cube_seam_error(px, DIM, flipped=True, pairs=pairs)
    wrong = PB.cube_seam_error(px, DIM, flipped=False, pairs=pairs)
    # DIM is deliberately tiny (8), so nearest-neighbour quantisation leaves a
    # residual even for the right convention; what discriminates is the RATIO.
    # Measured here 0.096 vs 0.914 (9.5x); on the shipped 256^2 station_front
    # cube `blender_probe_probe.py` measures 0.1115 vs 1.6045 (14.4x).
    assert right < 0.15, right
    assert wrong > right * 5, (right, wrong)


def test_resolve_probe_context_on_a_manifest_without_the_section():
    PB = _pb()
    ctx = PB.resolve_probe_context(None, {"objects": []})
    assert ctx["count"] == 0 and ctx["source"] == "absent"
    assert ctx["files"] == {}
    assert any("no `reflection_probes` section" in n for n in ctx["notes"])


def test_resolve_probe_context_says_when_no_cube_was_extracted():
    PB = _pb()
    manifest = {"reflection_probes": {
        "count": 2, "resource": "dead", "colorspace": RP.COLORSPACE_PROBE,
        "probes": [{"index": 0, "cube_file": ""}, {"index": 1, "cube_file": ""}]}}
    ctx = PB.resolve_probe_context(None, manifest)
    assert ctx["count"] == 2 and ctx["files"] == {}
    assert any("--probe-textures" in n for n in ctx["notes"])


def test_resolve_probe_context_finds_a_written_cube(tmp_path):
    PB = _pb()
    (tmp_path / "probes").mkdir()
    (tmp_path / "probes" / "probe_00.dds").write_bytes(b"DDS ")
    manifest = {"reflection_probes": {
        "count": 1, "probes": [{"index": 0, "cube_file": "probes/probe_00.dds"}]}}
    ctx = PB.resolve_probe_context(tmp_path, manifest)
    assert list(ctx["files"]) == [0]
    assert ctx["files"][0].endswith("probe_00.dds")
    assert ctx["notes"] == []


def test_probe_spec_for_object_honours_the_null_sentinel():
    PB = _pb()
    manifest = {"reflection_probes": {
        "count": 2, "probes": [{"index": 0}, {"index": 1}]}}
    ctx = PB.resolve_probe_context(None, manifest)
    assert PB.probe_spec_for_object(ctx, {"probe_index": 1})["index"] == 1
    # ⛔ the sentinel must NOT collapse onto probe 0
    assert PB.probe_spec_for_object(ctx, {"probe_index": 0xFFFFFFFF}) == {}
    assert PB.probe_spec_for_object(ctx, {"probe_index": None}) == {}
    assert PB.probe_spec_for_object(ctx, {}) == {}
    # out of range is refused, never clamped
    assert PB.probe_spec_for_object(ctx, {"probe_index": 9}) == {}


def test_wire_is_a_no_op_without_blender_and_says_why():
    PB = _pb()
    rep = PB.wire_ambient_specular(None, None, None, {}, None)
    assert rep["wired"] is False
    # every no-op names itself; which reason fires depends on whether a `bpy`
    # stub is present in this process (see the note in the test above).
    assert rep["reason"] in ("no bpy", "probe_mode=off")
    on = {"probe_mode": PB.MODE_SPECULAR}
    if PB.bpy is not None:
        assert PB.wire_ambient_specular(None, None, None, {}, None, on)["reason"] == \
            "mesh names no probe"
        assert PB.wire_ambient_specular(
            None, None, None, {"index": 0}, None, on)["reason"].startswith("no equirect")


def test_the_documented_defaults_are_the_conservative_ones():
    PB = _pb()
    assert PB.STRIP_IMAGE_IS_FLIPPED is True
    assert PB.DEFAULT_EQUIRECT_WIDTH == 512 and PB.DEFAULT_EQUIRECT_HEIGHT == 256
    assert PB.DEFAULT_FRESNEL_IOR == 1.45
    # a 512x256 equirect never upsamples a 256^2 x 6 cube
    assert PB.DEFAULT_EQUIRECT_WIDTH * PB.DEFAULT_EQUIRECT_HEIGHT < 256 * 256 * 6


def test_it_degrades_instead_of_exploding_without_le_mesh():
    """The shipped add-on zip contains only `lone_echo_import/`.

    `build_addon_zip.py --list` ships 10 files and **no `le_mesh`**, so the soft
    import can genuinely fail on a user's install.  Every entry point must then
    say `le_mesh unavailable` and wire nothing — a module-level `RP.X` would
    take the whole add-on down at registration instead.
    """
    PB = _pb()
    real = PB.RP
    try:
        PB.RP = None
        assert PB.available() is False
        ctx = PB.resolve_probe_context("/nowhere", {"reflection_probes": {"count": 3}})
        assert ctx["count"] == 0 and ctx["notes"] == ["le_mesh unavailable"]
        assert PB.probe_spec_for_object(ctx, {"probe_index": 0}) == {}
        assert PB.equirect_pixels_from_strip([], 4, 2, 2) == []
        assert PB._edge_direction_pairs(4) == []
        assert PB.cube_seam_error([], 4) == float("inf")
        assert PB._has_probe(0) is True
        assert PB._has_probe(0xFFFFFFFF) is False
    finally:
        PB.RP = real
    assert PB.available() is True


def test_module_level_constants_have_standalone_fallbacks():
    PB = _pb()
    assert PB.MANIFEST_KEY == "reflection_probes"
    assert PB.COLORSPACE_PROBE == "Linear Rec.709"
    assert PB.COLORSPACE_PROBE_FALLBACK == "Non-Color"
    assert PB.PROBE_INDEX_NONE == 0xFFFFFFFF
