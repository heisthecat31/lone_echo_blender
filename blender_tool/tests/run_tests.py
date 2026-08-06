"""Zero-dependency test runner (works without pytest).

    python3 tests/run_tests.py
    python3 tests/run_tests.py --list-probes    # detail the scripts NOT run
    python3 tests/run_tests.py --quiet          # summary + failures + skips only

Discovers test_*.py in this directory, runs every top-level `test_*` function,
and supplies a `tmp_path` (pathlib.Path to a fresh temp dir) when requested.
The same files also run under `pytest` unchanged.

★ WHAT THIS RUNNER DOES NOT RUN, AND WHY IT NOW SAYS SO.

Two things used to make a green run mean less than it looked like:

  1. A test that could not reach its data just `return`ed, and counted as
     PASSED. Tests may now raise `unittest.SkipTest` instead; those are counted
     separately and every skip REASON is reprinted at the end. `840 passed` with
     eleven silent no-ops is not the same result as `829 passed, 11 skipped`,
     and that difference is how the LOD-proxy defect survived a whole session of
     green suites.
  2. This directory also holds `blender_*.py` and `audit_*.py` scripts — real
     verification work that needs Blender, the game archives, or both. The
     runner has never executed them and never mentioned them, so their coverage
     was silently credited to the suite. They are now INVENTORIED on every run.

⛔ The default run stays pure-python3 and needs neither Blender nor the game
data. Probes are listed, never launched; `--list-probes` prints what each one
needs and what it does.
"""

from __future__ import annotations

import importlib
import inspect
import sys
import tempfile
import traceback
from pathlib import Path
from unittest import SkipTest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for p in (str(ROOT), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

#: modules here that are library code for the tests, not un-run probes.
_SUPPORT = {"conftest", "synthetic", "render_engine_util", "run_tests"}


def _probe_kind(path: Path) -> tuple[str, str]:
    """(requirement, one-line purpose) for a script this runner does not run."""
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
    except OSError:
        head = ""
    doc = ""
    for line in head.splitlines():
        s = line.strip().strip('"').strip("'")
        if s and not s.startswith(("#", "from ", "import ", '"""', "'''")):
            doc = s
            break
    if path.stem.startswith("blender_"):
        need = "Blender, headless"
    elif "le_oodle" in head or "le_archive_decode" in head or "load_decompressed" in head:
        need = "Windows python + the game archives"
    elif "exports" in head or "fixtures" in head:
        need = "an extracted package corpus under blender_tool/exports/"
    else:
        need = "manual invocation"
    return need, doc[:100]


def probe_inventory() -> list[tuple[str, str, str]]:
    """Every .py here that is neither a `test_*` module nor test support."""
    out = []
    for f in sorted(HERE.glob("*.py")):
        if f.stem.startswith("test_") or f.stem in _SUPPORT:
            continue
        need, doc = _probe_kind(f)
        out.append((f.name, need, doc))
    return out


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    list_probes = "--list-probes" in argv
    quiet = "--quiet" in argv

    modules = sorted(f.stem for f in HERE.glob("test_*.py"))
    passed = failed = skipped = 0
    failures: list[tuple[str, Exception, str]] = []
    skips: list[tuple[str, str]] = []
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
                if not quiet:
                    print(f"  PASS {mod_name}.{name}")
            except SkipTest as exc:
                skipped += 1
                skips.append((f"{mod_name}.{name}", str(exc)))
                print(f"  SKIP {mod_name}.{name}: {exc}")
            except Exception as exc:   # noqa: BLE001
                failed += 1
                failures.append((f"{mod_name}.{name}", exc, traceback.format_exc()))
                print(f"  FAIL {mod_name}.{name}: {exc}")

    tail = f", {skipped} skipped" if skipped else ""
    print(f"\n{passed} passed, {failed} failed{tail}")

    for label, _exc, tb in failures:
        print(f"\n=== {label} ===\n{tb}")

    # ★ REPRINT EVERY SKIP. A skip that scrolled off the top of a 900-line run
    # is a silent skip with extra steps.
    if skips:
        print(f"\n=== {len(skips)} SKIPPED — these assertions did NOT run ===")
        for label, why in skips:
            print(f"  {label}\n      {why}")

    probes = probe_inventory()
    if probes:
        print(f"\n=== {len(probes)} script(s) in tests/ that this runner does NOT "
              f"execute ===")
        if list_probes:
            for fname, need, doc in probes:
                print(f"  {fname}\n      needs: {need}\n      {doc}")
        else:
            print("  " + ", ".join(f for f, _n, _d in probes))
            print("  (--list-probes for what each one needs and does)")
        print("  ⛔ Their coverage is NOT included in the counts above.")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
