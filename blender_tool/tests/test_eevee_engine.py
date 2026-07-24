"""Unit tests for the bpy-free `resolve_render_engine` helper.

Archive-free / Blender-free: runs under plain `python3` via run_tests.py.
"""

from render_engine_util import resolve_render_engine


def test_eevee_prefers_next_when_both_present():
    ids = ["BLENDER_WORKBENCH", "BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "CYCLES"]
    assert resolve_render_engine("eevee", ids) == "BLENDER_EEVEE_NEXT"


def test_eevee_falls_back_to_plain_eevee():
    ids = ["BLENDER_WORKBENCH", "BLENDER_EEVEE", "CYCLES"]
    assert resolve_render_engine("eevee", ids) == "BLENDER_EEVEE"


def test_eevee_mode_is_case_insensitive():
    ids = ["BLENDER_WORKBENCH", "BLENDER_EEVEE_NEXT"]
    assert resolve_render_engine("EEVEE", ids) == "BLENDER_EEVEE_NEXT"


def test_workbench_none_and_empty_map_to_workbench():
    ids = ["BLENDER_WORKBENCH", "BLENDER_EEVEE_NEXT"]
    assert resolve_render_engine("workbench", ids) == "BLENDER_WORKBENCH"
    assert resolve_render_engine(None, ids) == "BLENDER_WORKBENCH"
    assert resolve_render_engine("", ids) == "BLENDER_WORKBENCH"


def test_eevee_with_neither_available_raises():
    ids = ["BLENDER_WORKBENCH", "CYCLES"]
    try:
        resolve_render_engine("eevee", ids)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError when no EEVEE engine available")


def test_unknown_mode_raises():
    ids = ["BLENDER_WORKBENCH", "BLENDER_EEVEE_NEXT"]
    try:
        resolve_render_engine("cycles", ids)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for unknown engine mode")
