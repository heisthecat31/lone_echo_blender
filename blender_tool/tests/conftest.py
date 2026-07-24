"""pytest bootstrap: make `le_mesh` importable from the blender_tool root."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]   # .../blender_tool
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
