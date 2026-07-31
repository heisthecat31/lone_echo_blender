"""Standalone .lemesh reader for the Blender addon.

No dependency on le_mesh / oodle — reads manifest.json + raw blobs directly so
the addon installs as a normal Blender add-on. Uses numpy (bundled in Blender)
for fast blob loads, with an `array` fallback.
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    import numpy as _np
except Exception:   # pragma: no cover - Blender always ships numpy
    _np = None

_NP_DTYPE = {"float32": "<f4", "uint32": "<u4", "int32": "<i4"}
_ARR_CODE = {"float32": "f", "uint32": "I", "int32": "i"}


class Package:
    def __init__(self, pkg_dir: Path):
        self.dir = Path(pkg_dir)
        self.manifest = json.loads((self.dir / "manifest.json").read_text(encoding="utf-8"))

    @property
    def objects(self):
        return self.manifest["objects"]

    @property
    def materials(self):
        return self.manifest.get("materials", [])

    @property
    def coordinate_system(self):
        return self.manifest.get("coordinate_system", "rad_engine")

    def load(self, rel_path: str, dtype: str):
        """Return a flat sequence (numpy array or array.array) for a blob."""
        data = (self.dir / rel_path).read_bytes()
        if _np is not None:
            return _np.frombuffer(data, dtype=_NP_DTYPE[dtype])
        from array import array
        a = array(_ARR_CODE[dtype])
        a.frombytes(data)
        return a

    def attribute(self, obj: dict, key: str):
        """Return (flat_values, comps) for an attribute, or (None, 0)."""
        entry = obj.get("attributes", {}).get(key)
        if not entry or "blob" not in entry:
            return None, 0
        return self.load(entry["blob"], entry["dtype"]), entry["comps"]

    def indices(self, obj: dict):
        entry = obj.get("index")
        if not entry:
            return None
        return self.load(entry["blob"], entry["dtype"])


def select_lod_draws(draws, level):
    """The subset of a mesh's draws to emit for LOD `level`, clamped per mesh.

    A mesh's coarser LODs are extra draws covering LATER slices of the SAME index
    buffer (the mesh-list LOD chain — see `le_mesh.meshlist.assign_lod_levels`),
    so emitting every draw stacks the levels on top of each other. Selection is
    per mesh and clamped: a mesh whose chain stops at level 1 still emits its
    level 1 when level 3 is asked for, and a mesh with no chain at all (every draw
    level 0 — the case for all but 11 of the corpus's 1,240 mesh-lists) is
    returned unchanged.

    `level < 0` keeps every draw (all levels stacked — the pre-LOD behaviour).
    A package written before the `lod.level` key existed reads as all-level-0, so
    it also passes through untouched.

    Pure / bpy-free so it is unit tested outside Blender.
    """
    if level is None or level < 0:
        return list(draws)
    levels = [int((d.get("lod") or {}).get("level", 0) or 0) for d in draws]
    if not levels or max(levels) == 0:
        return list(draws)
    want = min(level, max(levels))
    return [d for d, lv in zip(draws, levels) if lv == want]
