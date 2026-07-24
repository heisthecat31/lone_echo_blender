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
