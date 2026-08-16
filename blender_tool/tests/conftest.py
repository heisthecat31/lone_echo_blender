"""pytest bootstrap: make `le_mesh` and `scripts/` importable.

`scripts/` is on the path so the archive-free Echo VR decoder tests
(`test_evr_*.py`) can import `evr_material_resource` and friends. Those modules
are pure-Python binary parsers with no Oodle and no `bpy` dependency, so they run
under plain `python3` with the rest of the core suite.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # .../blender_tool
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCRIPTS = ROOT.parent / "scripts"
if SCRIPTS.is_dir() and str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
