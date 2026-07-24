"""le_oodle — thin compatibility shim over the standalone `pyoodle` package.

Historically the decode stack imported `le_oodle` for both (a) Oodle/COMPRESS
decompression and (b) the game-corpus path constants. The decompressor now lives
in its own MIT-licensed project, **pyoodle**; this shim re-exports that API so the
`le_scene_*` / `le_static_scatter` modules keep importing `le_oodle`
unchanged, and it resolves the game-data locations from the environment (no machine-
specific path is hard-coded).

Configuration (all optional; set what your setup needs):

    LONE_ECHO_DATA_ROOT   root of the extracted `<...>/win7` game data tree
    LONE_ECHO_OODLE_DLL   path to your own copy of the Oodle runtime DLL
    LONE_ECHO_PACKAGE_ROOT parent that implies DATA_ROOT + the default DLL location
    PYOODLE_PATH          location of the pyoodle checkout (if not pip-installed)

pyoodle is expected to be `pip install`-ed, or checked out next to this repo
(e.g. `Documents/pyoodle` beside `Documents/lone_echo_blender`), which this shim
finds automatically.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# --- locate pyoodle: installed, on PYOODLE_PATH, or the sibling checkout ------
try:
    import pyoodle
except ImportError:                                    # pragma: no cover
    _cand = os.environ.get("PYOODLE_PATH") or str(
        Path(__file__).resolve().parents[2] / "pyoodle")
    if _cand not in sys.path:
        sys.path.insert(0, _cand)
    import pyoodle

# Re-export the decompressor API (drop-in for the historical le_oodle surface).
from pyoodle import (                                   # noqa: E402,F401
    CHUNK_SIZE, init_oodle, decompress, load_decompressed,
    chunk_table, decompress_range, hexdump,
)

# --- game-corpus locations (env-configured; no hard-coded personal path) -----
_pkg = os.environ.get("LONE_ECHO_PACKAGE_ROOT")
PACKAGE_ROOT = Path(_pkg) if _pkg else None

_default_data = (PACKAGE_ROOT / "_data" / "5828984418" / "win7") if PACKAGE_ROOT else Path("game_data/win7")
DATA_ROOT = Path(os.environ.get("LONE_ECHO_DATA_ROOT", str(_default_data)))

_env_dll = os.environ.get("LONE_ECHO_OODLE_DLL")
if _env_dll:
    OODLE_DLL: Path | None = Path(_env_dll)
elif PACKAGE_ROOT is not None:
    OODLE_DLL = PACKAGE_ROOT / "bin" / "win7" / "oodle_11_win64.dll"
else:
    OODLE_DLL = None

# Point pyoodle at the resolved DLL (if any) so the historical callers that only
# ever set le_oodle.OODLE_DLL still work.
if OODLE_DLL is not None:
    pyoodle.set_dll_path(OODLE_DLL)
