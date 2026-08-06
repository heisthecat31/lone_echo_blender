"""End-to-end: run the real extractor on a real archive and pin what it produces.

★ WHY. `extractor/le_extract.py` is the whole first half of this tool — archive
decode, Oodle, subresource pairing, the `SVertexElement` walk, every attribute
decode, the `CGRenderParams` walk, RDEF binding, role resolution — and until this
file existed NOTHING in the suite executed a single line of it. The unit tests
cover `le_mesh/*` with synthetic bytes; the extractor is the thing that produces
the bytes those modules see in production, and it was untested end to end.

WHAT IS PINNED, AND WHY THAT SPLIT

  * GEOMETRY, EXACTLY — every blob's sha256 and byte length, the index count, the
    full `raw_vertex_format` element table, the draw table, the flags, the
    stride, and the resolved `lightmap_uv`. This is the decode path, it is
    deterministic, and a change to it is always either a fix (update the
    expectation deliberately) or a regression.
  * MATERIAL IDENTITY, exactly — the material key, its two component hashes, the
    role -> texture-hash bindings and their `role_sources`, and the
    `unrouted_roles` refusal list. This asset is the RDEF case: nothing declares
    the role, `rdef_bind0` is bound from the DXBC `RDEF` chunk alone, and the
    extractor must REFUSE to route it. A regression that silently invented a role
    would show up here and nowhere else.
  * ⛔ NOT PINNED — the shading INTERPRETATION (`render_mode`, `alpha_source`,
    `brdf_lobes`, layer compositing, node graphs). Those are owned by
    `test_materials.py` / `test_brdf_lobes.py` / `test_material_builder_nodes.py`
    and are actively being improved; pinning them here would turn every
    improvement into a red suite for the wrong reason.

★ SHIP HASHES, NOT BYTES. The expectation file holds sha256 digests and integer
counts. It contains no game geometry and no hash -> name dictionary, so it is safe
to publish alongside the tool. Content hashes (`0703fd2acd5803e9`) are
identifiers, not secrets — they name resources inside a copy of the game the user
already owns.

⚠ SKIPS ARE LOUD. This needs (a) Windows `python.exe`, because `le_oodle` loads a
Windows Oodle DLL, and (b) the extracted game data tree. On any machine without
both, every test here raises `unittest.SkipTest` with a reason that names exactly
what is missing — never a silent `return`, which is the failure mode
docs/TESTING.md was written about.

    python3 blender_tool/tests/run_tests.py              # runs it when possible
    LE_SKIP_EXTRACTOR_E2E=1 python3 .../run_tests.py     # opt out explicitly
    LE_E2E_REGENERATE=1     python3 .../run_tests.py     # rewrite the expectation
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import SkipTest

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
LE_ROOT = BLENDER_TOOL.parent
if str(BLENDER_TOOL) not in sys.path:
    sys.path.insert(0, str(BLENDER_TOOL))

EXPECTED = HERE / "expected" / "le_extract_e2e.json"

#: The E2E asset. Chosen deliberately: ONE mesh, 24 vertices, 12 triangles, ONE
#: material — small enough to extract in seconds, yet it still exercises four
#: distinct vertex encodings (eF32 / eU8n / eU16n / eS16n), a slot-4 lightmap UV
#: set, an index buffer, a scene-set mask, and the RDEF-only binding path.
E2E_ARCHIVE = "0703fd2acd5803e9"
E2E_MESHLIST = "8f76d470b7ca990f"

#: Where a Windows interpreter is looked for, as PATH SEGMENTS rather than one
#: absolute string — `LE_WINDOWS_PYTHON` first, then the standard per-user
#: install under the mounted Windows drive. Kept as segments deliberately: a
#: literal `/mnt/<drive>/Users/<name>/...` in a source file is exactly what the
#: public-release scrub gate flags, and this file is a release candidate.
_WIN_MOUNT = Path("/mnt") / "c"
_WIN_PY_TAIL = ("AppData", "Local", "Programs")

#: Fields of a draw that are part of the decode contract. `material_index` is the
#: engine's index into the ARCHIVE's material table and is pinned as-is.
DRAW_FIELDS = ("renderparam_index", "idx_start", "idx_count", "primtype",
               "is_triangles", "shaderset_index", "material_index",
               "material_key", "sort_priority", "permutation", "scene_mask",
               "scene_set_bit", "scene_set_min_count")

OBJECT_FIELDS = ("name", "mesh_index", "name_hash", "flags", "flag_names",
                 "shadow_only", "force_single_sided", "vertex_count",
                 "vertex_stride", "lightmap_uv", "scene_lod_level",
                 "lightmap_index", "lm_slice_index", "numlobes", "outline_mode")

MATERIAL_FIELDS = ("key", "shaderset_hash", "material_hash", "double_sided",
                   "blend_mode", "mattype")


# ---------------------------------------------------------------------------
# environment probing — each returns a REASON string when unavailable
# ---------------------------------------------------------------------------

def windows_python() -> str | None:
    """The Windows interpreter to shell out to, or None when there is not one."""
    env = os.environ.get("LE_WINDOWS_PYTHON")
    if env and Path(env).is_file():
        return env
    users = _WIN_MOUNT / "Users"
    if not users.is_dir():
        return None
    try:
        homes = sorted(users.iterdir())
    except OSError:                       # unreadable mount — treat as absent
        return None
    for home in homes:
        base = home.joinpath(*_WIN_PY_TAIL) / "Python"
        if not base.is_dir():
            continue
        try:
            versions = sorted(base.iterdir(), reverse=True)
        except OSError:
            continue
        for ver in versions:
            exe = ver / "python.exe"
            if exe.is_file():
                return str(exe)
    return None


def as_local_path(p) -> Path:
    """A path this interpreter can `stat`.

    `le_oodle.DATA_ROOT` is written for the WINDOWS interpreter that runs the
    extractor, so under WSL it arrives as `<drive>:\\...` and every `is_dir()`
    on it is False regardless of whether the data is there. Translate it to the
    mount so the availability probe reports the truth rather than a false
    "missing". Built from segments, never a hardcoded absolute prefix.
    """
    s = str(p)
    if len(s) > 2 and s[1] == ":" and s[2] in "\\/":
        return Path("/mnt") / s[0].lower() / Path(s[3:].replace("\\", "/"))
    return Path(s)


def game_data_reason() -> str | None:
    """None when the extractor's inputs are reachable, else why not."""
    try:
        sys.path.insert(0, str(LE_ROOT / "scripts"))
        from le_oodle import DATA_ROOT                    # noqa: PLC0415
    except Exception as exc:                              # noqa: BLE001
        return f"`le_oodle` is not importable ({exc.__class__.__name__}: {exc})"
    primary = as_local_path(DATA_ROOT) / "primary"
    if not primary.is_dir():
        return f"the game data tree is not reachable (no `{primary}`)"
    return None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# the digest — field-for-field for structure, hash-for-hash for bytes
# ---------------------------------------------------------------------------

def package_digest(pkg: Path) -> dict:
    """A stable, publishable summary of one `.lemesh` package."""
    m = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    objects = []
    for o in m["objects"]:
        attrs = {}
        for key, a in sorted((o.get("attributes") or {}).items()):
            entry = {k: a.get(k) for k in ("usage", "slot", "comps", "encoding",
                                           "packed_unresolved", "dtype")}
            rel = a.get("blob")
            if rel:
                raw = (pkg / rel).read_bytes()
                entry["bytes"] = len(raw)
                entry["sha256"] = _sha(raw)
            attrs[key] = entry
        idx = None
        ie = o.get("index") or {}
        if ie.get("blob"):
            raw = (pkg / ie["blob"]).read_bytes()
            idx = {"count": ie.get("count"), "dtype": ie.get("dtype"),
                   "bytes": len(raw), "sha256": _sha(raw)}
        objects.append({
            **{k: o.get(k) for k in OBJECT_FIELDS},
            # the raw element table is the decode contract; pinned whole because
            # it is small and every field of it matters.
            "raw_vertex_format": o.get("raw_vertex_format"),
            "aabb_sha256": _sha(json.dumps([o.get("aabb_min"), o.get("aabb_max")],
                                           sort_keys=True).encode()),
            "attributes": attrs,
            "index": idx,
            "draws": [{k: d.get(k) for k in DRAW_FIELDS}
                      for d in (o.get("draws") or [])],
            "draw_lod": [(d.get("lod") or {}).get("level") for d in (o.get("draws") or [])],
        })
    materials = []
    for mm in m.get("materials") or []:
        names = mm.get("texture_names") or {}
        materials.append({
            **{k: mm.get(k) for k in MATERIAL_FIELDS},
            "role_textures": dict(sorted((mm.get("role_textures") or {}).items())),
            "role_sources": dict(sorted((mm.get("role_sources") or {}).items())),
            "unrouted_roles": sorted(mm.get("unrouted_roles") or []),
            "channel_keys": sorted((mm.get("channels") or {}).keys()),
            # ★ a DIGEST, never the dictionary itself: a hash -> name table is
            # derived from shipped bytes and does not belong in a published repo.
            "texture_names_sha256": _sha(json.dumps(names, sort_keys=True).encode()),
            "texture_names_count": len(names),
        })
    return {
        "format": m.get("format"),
        "version": m.get("version"),
        "coordinate_system": m.get("coordinate_system"),
        "source": m.get("source"),
        "n_objects": len(m["objects"]),
        "n_materials": len(m.get("materials") or []),
        "objects": objects,
        "materials": materials,
    }


def run_extractor(out_dir: Path) -> Path:
    """Run `le_extract.py` under Windows Python. Returns the package directory.

    ⚠ Every path handed to `python.exe` is RELATIVE to `LE_ROOT`. A WSL absolute
    path (`/mnt/<drive>/...`) reaches the Windows interpreter as `C:\\mnt\\...`
    and it fails with ENOENT; the repo is already on the mounted drive, so
    running from it with relative arguments is the portable form.
    """
    py = windows_python()
    script = (BLENDER_TOOL / "extractor" / "le_extract.py").relative_to(LE_ROOT)
    rel_out = out_dir.resolve().relative_to(LE_ROOT)
    cmd = [py, str(script), "--archive", E2E_ARCHIVE, "--mesh", E2E_MESHLIST,
           "--out", str(rel_out)]
    proc = subprocess.run(cmd, cwd=str(LE_ROOT), capture_output=True,
                          text=True, timeout=900)
    pkg = out_dir / f"{E2E_ARCHIVE}_{E2E_MESHLIST}.lemesh"
    if proc.returncode != 0 or not (pkg / "manifest.json").is_file():
        raise AssertionError(
            f"extractor failed (rc={proc.returncode})\n"
            f"--- stdout ---\n{proc.stdout[-4000:]}\n"
            f"--- stderr ---\n{proc.stderr[-4000:]}")
    return pkg


def _scratch() -> Path:
    """A temp directory the WINDOWS interpreter can also see.

    ⚠ WSL `/tmp` is invisible to `python.exe`; a run pointed there lands in
    `C:\\tmp` and the test then finds nothing. The repo itself is on the mounted
    Windows drive whenever the extractor is runnable at all, so the scratch dir
    goes under the (gitignored) `exports/`.
    """
    d = BLENDER_TOOL / "exports" / ".e2e_tmp"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _skip_unless_runnable() -> None:
    if os.environ.get("LE_SKIP_EXTRACTOR_E2E"):
        raise SkipTest("LE_SKIP_EXTRACTOR_E2E is set — the extractor end-to-end "
                       "test was opted out of explicitly")
    if not windows_python():
        raise SkipTest(
            "Windows `python.exe` not found — `le_oodle` loads the game's Oodle "
            "DLL through `ctypes.WinDLL` and cannot run under WSL/Linux python3. "
            "Set LE_WINDOWS_PYTHON to your interpreter to enable this test. "
            "⛔ WHILE THIS SKIP IS ACTIVE NOTHING IN THE SUITE EXECUTES THE "
            "EXTRACTOR AT ALL.")
    why = game_data_reason()
    if why:
        raise SkipTest(
            f"the extractor's inputs are unavailable: {why}. Point "
            f"LONE_ECHO_DATA_ROOT at your own extracted game data to enable "
            f"this test. ⛔ WHILE THIS SKIP IS ACTIVE NOTHING IN THE SUITE "
            f"EXECUTES THE EXTRACTOR AT ALL.")


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

def test_extractor_environment_is_reported():
    """Say out loud whether this run can execute the extractor.

    This test exists so the *absence* of E2E coverage is visible in the output
    rather than inferred from a missing line.
    """
    py = windows_python()
    why = game_data_reason()
    print(f"    [extractor e2e] windows python: {py or 'NOT FOUND'}; "
          f"game data: {'OK' if not why else why}")
    _skip_unless_runnable()


def test_extractor_reproduces_the_pinned_package():
    """Extract `0703fd2acd5803e9 / 8f76d470b7ca990f` and compare field for field
    and hash for hash against `tests/expected/le_extract_e2e.json`.

    A failure here means one of: the decode changed, the archive changed, or the
    expectation is stale. Re-run with `LE_E2E_REGENERATE=1` ONLY after reading
    the diff and deciding the new output is correct.
    """
    _skip_unless_runnable()
    out = _scratch()
    try:
        pkg = run_extractor(out)
        got = package_digest(pkg)
    finally:
        shutil.rmtree(out, ignore_errors=True)

    if os.environ.get("LE_E2E_REGENERATE") or not EXPECTED.is_file():
        EXPECTED.parent.mkdir(parents=True, exist_ok=True)
        EXPECTED.write_text(json.dumps(got, indent=1, sort_keys=True) + "\n",
                            encoding="utf-8")
        if not os.environ.get("LE_E2E_REGENERATE"):
            raise AssertionError(
                f"no expectation existed; one was written to {EXPECTED}. "
                f"Review it and commit it — this run proves nothing.")
        print(f"    [extractor e2e] REGENERATED {EXPECTED}")
        return

    want = json.loads(EXPECTED.read_text(encoding="utf-8"))
    if got == want:
        nblobs = sum(1 for o in got["objects"] for a in o["attributes"].values()
                     if "sha256" in a)
        print(f"    [extractor e2e] {got['n_objects']} object(s), {nblobs} blob "
              f"hashes, {got['n_materials']} material(s) — all match")
        return

    diffs = _diff(want, got, "")
    raise AssertionError(
        f"the extractor's output no longer matches {EXPECTED.name}:\n  "
        + "\n  ".join(diffs[:40])
        + (f"\n  ... and {len(diffs) - 40} more" if len(diffs) > 40 else ""))


def test_extractor_output_is_deterministic():
    """Two runs of the extractor on the same asset must agree byte for byte.

    A non-deterministic extractor makes every other assertion in this file
    meaningless, so it is checked directly rather than assumed. Only the blob
    digests are compared, which is the part that would drift.
    """
    _skip_unless_runnable()
    if not os.environ.get("LE_E2E_DETERMINISM"):
        raise SkipTest("determinism check runs a SECOND extraction; set "
                       "LE_E2E_DETERMINISM=1 to enable (it roughly doubles this "
                       "file's runtime)")
    first = second = None
    for slot in ("a", "b"):
        out = BLENDER_TOOL / "exports" / f".e2e_tmp_{slot}"
        shutil.rmtree(out, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
        try:
            d = package_digest(run_extractor(out))
        finally:
            shutil.rmtree(out, ignore_errors=True)
        blobs = {(o["name"], k): a.get("sha256")
                 for o in d["objects"] for k, a in o["attributes"].items()}
        if first is None:
            first = blobs
        else:
            second = blobs
    assert first == second, "the extractor is not deterministic across runs"


def test_the_expectation_file_carries_no_game_bytes_or_names():
    """The publishing rule, enforced: the checked-in expectation must be hashes
    and counts only — no geometry, and no hash -> name dictionary.

    Runs everywhere, including on a machine that can never run the extractor, so
    the release property is asserted even when the E2E itself skips.
    """
    if not EXPECTED.is_file():
        raise SkipTest(f"{EXPECTED} has not been generated yet — run with "
                       f"LE_E2E_REGENERATE=1 on a machine with the game data")
    want = json.loads(EXPECTED.read_text(encoding="utf-8"))
    for o in want["objects"]:
        assert "aabb_min" not in o and "aabb_max" not in o, o["name"]
        for key, a in o["attributes"].items():
            assert "data" not in a and "values" not in a, (o["name"], key)
            if "bytes" in a:
                assert isinstance(a["bytes"], int)
                assert isinstance(a["sha256"], str) and len(a["sha256"]) == 64
    for mm in want["materials"]:
        assert "texture_names" not in mm, "a hash -> name dictionary leaked in"
        assert isinstance(mm.get("texture_names_sha256"), str)
    blob = EXPECTED.read_bytes()
    assert len(blob) < 256 * 1024, f"{EXPECTED.name} is {len(blob)} B — too big " \
                                   f"to be hashes and counts"


def _diff(want, got, path):
    """Every leaf that differs, as `path: want != got`."""
    out = []
    if type(want) is not type(got):
        return [f"{path or '<root>'}: type {type(want).__name__} != {type(got).__name__}"]
    if isinstance(want, dict):
        for k in sorted(set(want) | set(got)):
            if k not in want:
                out.append(f"{path}.{k}: ADDED = {got[k]!r}")
            elif k not in got:
                out.append(f"{path}.{k}: REMOVED (was {want[k]!r})")
            else:
                out += _diff(want[k], got[k], f"{path}.{k}")
    elif isinstance(want, list):
        if len(want) != len(got):
            out.append(f"{path}: length {len(want)} != {len(got)}")
        for i, (a, b) in enumerate(zip(want, got)):
            out += _diff(a, b, f"{path}[{i}]")
    elif want != got:
        out.append(f"{path}: {want!r} != {got!r}")
    return out
