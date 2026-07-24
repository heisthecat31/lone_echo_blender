"""Zero-dependency test runner (works without pytest).

    python3 tests/run_tests.py

Discovers test_*.py in this directory, runs every top-level `test_*` function,
and supplies a `tmp_path` (pathlib.Path to a fresh temp dir) when requested.
The same files also run under `pytest` unchanged.
"""

from __future__ import annotations

import importlib
import inspect
import sys
import tempfile
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)


def main() -> int:
    modules = sorted(f.stem for f in HERE.glob("test_*.py"))
    passed = failed = 0
    failures = []
    for mod_name in modules:
        mod = importlib.import_module(mod_name)
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if not name.startswith("test_") or fn.__module__ != mod_name:
                continue
            kwargs = {}
            if "tmp_path" in inspect.signature(fn).parameters:
                kwargs["tmp_path"] = Path(tempfile.mkdtemp(prefix="lemesh_test_"))
            try:
                fn(**kwargs)
                passed += 1
                print(f"  PASS {mod_name}.{name}")
            except Exception as exc:   # noqa: BLE001
                failed += 1
                failures.append((f"{mod_name}.{name}", exc, traceback.format_exc()))
                print(f"  FAIL {mod_name}.{name}: {exc}")

    print(f"\n{passed} passed, {failed} failed")
    for label, _exc, tb in failures:
        print(f"\n=== {label} ===\n{tb}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
