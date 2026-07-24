"""scene.json (`lescene`) reader for the Lone Echo add-on — pure stdlib.

Consumes the M4 scene-placement file emitted by `scripts/le_scene.py` so imported
`.lemesh` meshes land at their level WORLD positions instead of stacking at the
origin. This module only *locates* and *parses* the JSON and exposes the
row-major -> rows mapping; the actual mathutils.Matrix build + placement lives in
the operator (`__init__.py`) because it needs `mathutils` / `bpy`.

Schema (see scripts/le_scene.py):
    {"format":"lescene","version":1,"archive":..,"coordinate_system":"rad_engine",
     "placements": {<meshlist_hash>: [
         {"actornodeid","world_xf":[16 floats row-major],"parent_type",
          "parent_type_name","scale","start_visible","resolved","reason"?}, ...]}}
"""

from __future__ import annotations

import json
from pathlib import Path

SCENE_FORMAT = "lescene"


def find_scene_json(pkg_path):
    """Auto-detect a `scene.json` beside the chosen package/manifest.

    Searches the package directory and its parent (the archive export dir). Returns
    the first existing `scene.json` as a Path, else None.
    """
    p = Path(pkg_path)
    if p.name == "manifest.json":
        p = p.parent
    if p.is_dir():
        candidates = [p / "scene.json", p.parent / "scene.json"]
    else:
        candidates = [p.parent / "scene.json"]
    for c in candidates:
        if c.is_file():
            return c
    return None


def load_scene(path) -> dict:
    """Load + validate a `scene.json`. Raises ValueError on the wrong format."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("format") != SCENE_FORMAT:
        raise ValueError(
            f"{path}: not a {SCENE_FORMAT} scene (format={data.get('format')!r})")
    return data


def placements_for(scene: dict, meshlist_hash: str) -> list:
    """All placements for a meshlist / model-asset hash (or [] if none)."""
    return list(scene.get("placements", {}).get(meshlist_hash, []))


def world_xf_rows(world_xf):
    """Row-major 16-float `world_xf` -> the 4 ROW tuples for `mathutils.Matrix`.

    `world_xf` is row-major with the translation in indices 3, 7, 11 (the last
    column), so the rows are [0:4], [4:8], [8:12], [12:16]. `mathutils.Matrix(rows)`
    is row-major and reads its translation from column 3, hence
    `Matrix(world_xf_rows(x)).translation == (x[3], x[7], x[11])`. This mirrors the
    `_mat()` / `_build_armature` idiom already in __init__.py.
    """
    r = [float(v) for v in world_xf]
    return (tuple(r[0:4]), tuple(r[4:8]), tuple(r[8:12]), tuple(r[12:16]))
