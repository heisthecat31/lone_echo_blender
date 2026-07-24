"""Pure, bpy-free resolver for the render harness's `engine=` option.

Kept separate from `blender_scatter_render.py` (which imports `bpy`) so the
engine-selection logic is unit-testable under plain `python3` with no Blender.

`resolve_render_engine(mode, available_ids)` maps a user-facing engine mode to a
concrete Blender render-engine identifier, checking it against the identifiers
the running Blender actually exposes (`available_ids`):

    mode == "eevee"      -> first present of ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE")
                            (Blender 5.1 renamed the engine to *_NEXT); raises
                            ValueError if neither is available so the caller can
                            fall back to Workbench with a printed warning.
    mode in ("workbench", None, "") -> "BLENDER_WORKBENCH"
    any other mode       -> raises ValueError.

stdlib-only; no third-party or Blender imports.
"""

from __future__ import annotations

# Preference order for the EEVEE mode: Blender 5.x uses BLENDER_EEVEE_NEXT,
# older builds use BLENDER_EEVEE.
_EEVEE_PREFERENCE = ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE")

WORKBENCH_ID = "BLENDER_WORKBENCH"


def resolve_render_engine(mode, available_ids):
    """Return the concrete Blender engine identifier for `mode`.

    Args:
        mode: user-facing engine mode. "eevee"; or "workbench"/None/"" for
            Workbench; anything else is invalid.
        available_ids: iterable of engine identifiers the running Blender
            exposes (from `scene.render.bl_rna.properties['engine'].enum_items`).

    Raises:
        ValueError: for the "eevee" mode when no EEVEE identifier is available,
            or for any unrecognized mode.
    """
    key = mode.lower() if isinstance(mode, str) else mode
    available = set(available_ids)

    if key in (None, "", "workbench"):
        return WORKBENCH_ID

    if key == "eevee":
        for candidate in _EEVEE_PREFERENCE:
            if candidate in available:
                return candidate
        raise ValueError(
            "no EEVEE engine available (looked for "
            f"{', '.join(_EEVEE_PREFERENCE)}; have {sorted(available)})")

    raise ValueError(f"unknown render engine mode: {mode!r}")
