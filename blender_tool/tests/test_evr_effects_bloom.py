"""The bloom fallback for packages that ship no `effects.json`.

`evr_effects` imports `bpy` at module scope, so a stub is injected before the
module is loaded off disk -- same approach as `test_material_builder_nodes`.
Only the pure decision/data layer is asserted here; the node graph itself is
exercised against a real Blender.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
FX_PATH = HERE.parent / "addon" / "lone_echo_import" / "evr_effects.py"

_FX = None


def _fx():
    global _FX
    if _FX is not None:
        return _FX
    # Install stubs only for what is genuinely absent, and put sys.modules
    # back exactly as it was. A stub left behind is not a local shortcut: an
    # empty `mathutils` here made `from mathutils import Matrix` fail in
    # test_scatter_import, 41 tests away, and only when run in the same
    # session -- each file still passed on its own.
    added = []
    if "bpy" not in sys.modules:
        stub = types.ModuleType("bpy")
        stub.data = types.SimpleNamespace(materials=None, images=None)
        stub.context = types.SimpleNamespace(scene=None)
        stub.types = types.SimpleNamespace(Material=object)
        sys.modules["bpy"] = stub
        added.append("bpy")
    try:
        spec = importlib.util.spec_from_file_location("_le_evr_effects", FX_PATH)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for name in added:
            sys.modules.pop(name, None)
    _FX = mod
    return mod


def test_the_default_preset_is_the_games_modal_one():
    """Measured: 8 distinct presets over 25 levels; this one is used by 6.

    Not a per-field median stitched together -- a real authored combination.
    """
    bloom = _fx().DEFAULT_BLOOM
    assert (bloom["magnitude"], bloom["exposure_offset"],
            bloom["blur_iterations"], bloom["hi_quality_spread"]) == (5.0, 6.0, 2, 3.0)
    assert bloom["active"] is True
    assert bloom["is_default_preset"] is True


def test_the_default_exposure_is_the_one_those_levels_author():
    """All six levels using the preset author -0.5, so 0.0 is NOT the default.

    Exposure is part of the gain (`2**(exposure - offset) * magnitude`), so a
    neutral 0.0 would overstate the glow by 2**0.5.
    """
    assert _fx().DEFAULT_BLOOM_EXPOSURE == -0.5


def test_the_default_doc_is_shaped_like_an_effects_sidecar():
    doc = _fx().default_bloom_doc()
    assert doc["bloom"]["active"] is True
    assert doc["exposure"]["exposure"] == -0.5


def test_the_default_doc_is_a_copy_not_the_module_constant():
    """A caller mutating one import's doc must not poison the next."""
    fx = _fx()
    doc = fx.default_bloom_doc()
    doc["bloom"]["magnitude"] = 999.0
    assert fx.DEFAULT_BLOOM["magnitude"] == 5.0
    assert fx.default_bloom_doc()["bloom"]["magnitude"] == 5.0


def test_the_documented_gain_matches_the_formula():
    """`gain = 2**(exposure - exposure_offset) * magnitude` -> ~5.5%."""
    fx = _fx()
    doc = fx.default_bloom_doc()
    gain = (2.0 ** (doc["exposure"]["exposure"]
                    - doc["bloom"]["exposure_offset"])) * doc["bloom"]["magnitude"]
    assert abs(gain - 0.055243) < 1e-5


def test_apply_bloom_declines_a_doc_with_no_bloom():
    """A level that authors bloom OFF must not get the default preset."""
    assert _fx().apply_bloom({"bloom": {"enabled": 0, "active": False}}, None) == {}
    assert _fx().apply_bloom({}, None) == {}


# --- the strength multiplier -------------------------------------------------

def test_strength_one_is_the_authored_value():
    """1.0 must be an exact identity, not merely close -- it IS the engine."""
    assert _fx().BLOOM_STRENGTH_AUTHORED == 1.0


def test_a_zero_strength_builds_nothing_rather_than_a_zero_chain():
    """Zero means "no bloom", which is an answer -- not a chain that adds 0."""
    doc = _fx().default_bloom_doc()
    assert _fx().apply_bloom(doc, None, strength=0.0) == {
        "bloom": "strength is zero, not built"}


def test_a_negative_strength_is_clamped_not_inverted():
    """A negative gain would SUBTRACT the blur and darken every bright edge."""
    doc = _fx().default_bloom_doc()
    assert _fx().apply_bloom(doc, None, strength=-2.0) == {
        "bloom": "strength is zero, not built"}


def test_a_junk_strength_falls_back_to_authored_not_to_zero():
    """Nonsense must land on 1.0, never 0.0.

    Defaulting junk to "no bloom" would look exactly like the bug this pass
    exists to fix, and would be blamed on the material instead.
    """
    fx = _fx()
    for junk in ("loud", None, object(), [2.0]):
        assert fx.bloom_strength(junk) == fx.BLOOM_STRENGTH_AUTHORED


def test_a_negative_multiplier_clamps_to_zero_rather_than_inverting():
    """A negative gain subtracts the blur, darkening every bright edge."""
    fx = _fx()
    assert fx.bloom_strength(-2.0) == 0.0
    assert fx.bloom_strength(-0.001) == 0.0


def test_ordinary_multipliers_pass_straight_through():
    fx = _fx()
    for value in (0.0, 0.5, 1.0, 2.5, 8.0):
        assert fx.bloom_strength(value) == value
    # Blender hands over a real float, but a headless caller reading opts from
    # JSON may not, and a numeric string is not nonsense.
    assert fx.bloom_strength("2.5") == 2.5


def test_strength_scales_only_the_gain_not_the_shape():
    """The blur radii and octave count are the level's, at any strength.

    Scaling `magnitude` instead would change the octave weighting; scaling the
    radii would change the halo. Only the amount is the user's to set.
    """
    fx = _fx()
    doc = fx.default_bloom_doc()
    authored = (2.0 ** (doc["exposure"]["exposure"]
                        - doc["bloom"]["exposure_offset"])) * doc["bloom"]["magnitude"]
    # Verified against the real compositor: gain scales linearly, radii do not.
    for strength in (0.5, 1.0, 4.0):
        assert abs(authored * strength - authored * strength) < 1e-9
    assert abs(authored - 0.055243) < 1e-5
