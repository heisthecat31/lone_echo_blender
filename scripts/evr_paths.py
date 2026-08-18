"""Every path the project needs, resolved without hard-coding a machine.

## The rule

No module may contain an absolute path to a developer's disk.  Anything the
project needs is either

  * **vendored** under `app/extract/` and found relative to the repo root, or
  * **supplied by the user** on the command line, or
  * **overridden by an environment variable**, or
  * **discovered** from a known install layout.

Resolution order for every lookup is: explicit argument -> environment
variable -> vendored/in-repo copy -> discovery -> `None`.  Nothing raises at
import time; a missing optional dependency is reported where it is used.

## Vendored dependencies

| under `app/extract/` | why |
|---|---|
| `evr_mesh_importer` | the mesh decoder (`decode`, `primary`) -- MIT |
| `resource_io` | `CArchiveResource` closure decoder |
| `pyoodle` | Oodle decompression for shader-set scans |

⚠ `evr_mesh_importer/bin/` is deliberately NOT vendored: it is 7.2 MB of DLLs
(`libsquish`, `texconv`) used only for texture *encoding*, which no extraction
path touches.  `decode.py` and `primary.py` import nothing but `struct`, `math`
and `os`.

## Environment variables

| variable | meaning |
|---|---|
| `EVR_EXTRACT_DIR` | default flat game extract for `--dir` |
| `EVR_OUT_DIR` | default output root for `--out` |
| `EVR_HASH_LOOKUP` | hash -> name table |
| `EVR_TEXTURE_CACHE` | Echo VR texture cache directory |
| `EVR_BLENDER` | `blender.exe` for headless rendering |
"""

from __future__ import annotations

import os
from pathlib import Path

#: The repository root -- this file lives in `<root>/scripts/`.
ROOT = Path(__file__).resolve().parent.parent

#: Where vendored third-party code lives.
EXTRACT = ROOT / "app" / "extract"
DATA = ROOT / "data"

#: Vendored import roots, in the order they should go on `sys.path`.
VENDORED_IMPORT_ROOTS = (
    EXTRACT / "evr_mesh_importer",          # decode / primary
    EXTRACT / "resource_io",                # carchiveresource
    EXTRACT / "pyoodle",
    ROOT / "blender_tool",
    ROOT / "scripts",
)


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    p = Path(value).expanduser()
    return p if p.exists() else None


def install_import_paths() -> list:
    """Put the vendored roots on `sys.path`. Returns those that exist.

    Also honours `EVR_EXTRA_PYTHONPATH` (os.pathsep-separated) so a developer
    can point at a working checkout without editing code.
    """
    import sys

    added = []
    roots = list(VENDORED_IMPORT_ROOTS)
    extra = os.environ.get("EVR_EXTRA_PYTHONPATH", "")
    roots = [Path(p) for p in extra.split(os.pathsep) if p] + roots
    for root in roots:
        if root.is_dir():
            s = str(root)
            if s not in sys.path:
                sys.path.insert(0, s)
            added.append(root)
    return added


def hash_lookup() -> Path | None:
    """`hash_lookup.json` -- hash -> name. Optional; only affects labelling."""
    return (_env_path("EVR_HASH_LOOKUP")
            or (DATA / "hash_lookup.json" if (DATA / "hash_lookup.json").is_file()
                else None))


def extract_dir(explicit=None) -> Path | None:
    """The flat game extract. There is deliberately NO built-in default."""
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.exists() else None
    return _env_path("EVR_EXTRACT_DIR")


def out_dir(explicit=None, fallback: str = "out") -> Path:
    """Where packages are written. Defaults inside the repo, not on J:."""
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("EVR_OUT_DIR")
    return Path(env).expanduser() if env else ROOT / fallback


def texture_cache() -> Path | None:
    """Echo VR's texture cache, if this machine has the game installed.

    Discovery only -- never required. Checks the env var, then the standard
    Oculus install layout on every fixed drive.
    """
    found = _env_path("EVR_TEXTURE_CACHE")
    if found:
        return found
    tail = Path("Software/Software/ready-at-dawn-echo-arena/bin/win10/Tools")
    candidates = []
    for drive in ("C:/", "D:/", "E:/", "F:/", "G:/", "H:/", "J:/"):
        base = Path(drive) / "Oculus/Games" / tail
        candidates += [base / "Tools/Settings/texture_cache",
                       base / "Settings/texture_cache"]
    for c in candidates:
        if c.is_dir():
            return c
    return None


def blender() -> Path | None:
    """`blender.exe`, for the headless render helpers."""
    found = _env_path("EVR_BLENDER")
    if found:
        return found
    import shutil
    which = shutil.which("blender")
    if which:
        return Path(which)
    for drive in ("C:/", "D:/"):
        for version in ("5.0", "4.5", "4.2", "4.1", "4.0"):
            p = Path(drive) / f"Program Files/Blender Foundation/Blender {version}/blender.exe"
            if p.is_file():
                return p
    return None


def require_extract(explicit=None) -> Path:
    """`extract_dir` or a clear error naming both ways to supply it."""
    p = extract_dir(explicit)
    if p is None:
        raise SystemExit(
            "no game extract given: pass --dir <path> or set EVR_EXTRACT_DIR")
    return p


def describe() -> dict:
    """What resolved to what -- for `--paths` diagnostics."""
    return {
        "root": str(ROOT),
        "vendored": {str(p.relative_to(ROOT)): p.is_dir()
                     for p in VENDORED_IMPORT_ROOTS},
        "hash_lookup": str(hash_lookup() or ""),
        "extract_dir": str(extract_dir() or "(unset)"),
        "out_dir": str(out_dir()),
        "texture_cache": str(texture_cache() or "(not found)"),
        "blender": str(blender() or "(not found)"),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(describe(), indent=1))
