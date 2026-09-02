"""Engine DEBUG textures must never be routed as art.

`34dfbe67e4424f76` is a UV TEST GRID -- a checkerboard captioned "(0, 0)" /
"(1, 1)" with U and V axis arrows -- and `5c4bbfab65b919dd` is a byte-identical
copy of it (verified pixel-for-pixel, max abs diff 0).

⛔ It is not art, and the corpus says so without anyone having to look at it:
across the seven shipped packages that bind these two hashes they appear 15
times and ONLY EVER as `layer0_emissive_map` -- 11 on the PC side over six
levels, 4 on the Quest side -- never in any other role. That is the shape of an
engine stand-in for an emissive slot a material declares but does not author,
the same pattern as the Quest white 8x8 stub.

Routed as art it reaches EMISSION at full strength, which is what put a glowing
green-and-yellow test grid on the arena scoreboards.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from le_mesh.materials import (PLACEHOLDER_TEXTURES,          # noqa: E402
                               build_material_spec,
                               drop_placeholder_roles)

GRID = "34dfbe67e4424f76"
COPY = "5c4bbfab65b919dd"
#: The NULL ALBEDO tile -- 64x64 with all 4096 texels exactly RGBA (0,0,0,0),
#: read back through Blender's own BC1 decoder, and bound by 44 materials over
#: 7 levels under three different albedo roles. Observed, not inferred from
#: appearance, which is the bar the test below states.
NULL_ALBEDO = "50988725d240e5fe"


def test_the_uv_test_grid_is_dropped_with_a_reason():
    kept, dropped = drop_placeholder_roles({"layer0_emissive_map": GRID})
    assert kept == {}
    assert GRID in dropped["layer0_emissive_map"]
    assert "uv test grid" in dropped["layer0_emissive_map"]


def test_the_byte_identical_copy_is_dropped_too():
    """⛔ Filtering only the first hash leaves the same picture glowing under
    the second -- one of the arena's four bindings uses the copy."""
    _kept, dropped = drop_placeholder_roles({"layer0_emissive_map": COPY})
    assert dropped


def test_real_textures_are_untouched():
    roles = {"layer0_albedo_map": "deadbeefcafe0001",
             "layer0_normal_map": "deadbeefcafe0002"}
    kept, dropped = drop_placeholder_roles(dict(roles))
    assert kept == roles and dropped == {}


def test_only_the_placeholder_role_is_lost_not_the_material():
    """A material keeps every other binding -- the drop is surgical."""
    kept, dropped = drop_placeholder_roles({
        "layer0_albedo_map": "deadbeefcafe0001",
        "layer0_emissive_map": GRID})
    assert kept == {"layer0_albedo_map": "deadbeefcafe0001"}
    assert list(dropped) == ["layer0_emissive_map"]


def test_a_path_form_is_matched_not_just_a_bare_hash():
    """Packages carry `textures/<hash>.dds`; matching only bare hashes would
    silently let every path-form binding through."""
    for form in (f"textures/{GRID}.dds", f"textures\\{GRID}.png", GRID.upper()):
        _kept, dropped = drop_placeholder_roles({"layer0_emissive_map": form})
        assert dropped, form


def test_the_spec_reports_what_it_dropped():
    """Silent removal would turn an unlit material into a mystery."""
    spec = build_material_spec("k", role_textures={"layer0_emissive_map": GRID})
    assert spec["placeholder_roles"]
    assert "layer0_emissive_map" not in (spec.get("role_textures") or {})


def test_a_clean_material_reports_no_placeholders():
    spec = build_material_spec("k", role_textures={"layer0_albedo_map": "ab" * 8})
    assert spec["placeholder_roles"] == {}


def test_the_placeholder_table_is_emissive_only_evidence():
    """★ The justification is the corpus, so keep the table small and pinned.
    Adding a hash here claims it was observed as an engine stub -- not a guess
    that a texture looks wrong.

    `NULL_ALBEDO` clears that bar the same way the grid does: measured content
    (4096/4096 texels RGBA 0,0,0,0) plus corpus reuse (44 materials, 7 levels,
    3 roles). It is NOT here because the sky looked wrong -- it is here because
    a fully transparent uniform tile bound that widely is a stub, and treating
    it as an opaque backdrop is what let it replace the real sky art."""
    assert set(PLACEHOLDER_TEXTURES) == {GRID, COPY, NULL_ALBEDO}
