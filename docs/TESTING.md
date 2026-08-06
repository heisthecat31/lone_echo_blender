# What the suite covers, and what it does not

A green test run is a claim. This page says exactly what that claim is worth
here, because at one point it was worth less than it looked.

## 1. Run it

```bash
python3 blender_tool/tests/run_tests.py            # the whole suite
python3 blender_tool/tests/run_tests.py --quiet    # summary, failures and skips
python3 blender_tool/tests/run_tests.py --list-probes
```

Nothing in the default run needs Blender, an archive, Oodle, or game data.

On a clean checkout: **905 passed, 0 failed, 57 skipped**.
With a full local export: **915 passed, 0 failed, 47 skipped**.
962 tests over 52 modules, either way.

## 2. The blind spot this page exists for

Two things used to make a green run mean less than it looked like.

**A test that could not reach its data just `return`ed, and counted as PASSED.**
`840 passed` with eleven silent no-ops is not the same result as
`829 passed, 11 skipped`, and that difference is how a real LOD defect survived
a whole cycle of green suites: the tests that would have caught it could not see
any package, returned, and were counted as passes.

Tests now raise `unittest.SkipTest` instead. Skips are counted separately and
**every skip reason is reprinted at the end of the run**, most of them shouting in
capitals about what is *not* being checked while the skip is active. If you take
one thing from this page: **read the skip block, not just the count.**

★ **0.4.0 converted 34 more of these, across 11 modules — 27 tests that had been
reporting PASS while executing no assertion at all.** The pass count on a clean
checkout therefore *fell*, from 942 to 905, with **nothing removed and nothing
broken**: the total is 962 either way and the failures are still zero. A release
where the green number goes down because the suite stopped lying is a better
release than one where it goes up.

⚠ The remaining audit is written down rather than assumed clean. The detector is:

```bash
# a bare `return` in a test body (not a nested helper) within four lines of a
# data-availability guard — the shape that silently converts a skip into a pass
python3 - <<'PY'
import ast, pathlib
KEYS = ("is_dir", "exists()", "is_file", "glob", "sys.modules")
for f in sorted(pathlib.Path("blender_tool/tests").glob("test_*.py")):
    src = f.read_text(encoding="utf-8").splitlines()
    for node in ast.walk(ast.parse("\n".join(src))):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test")):
            continue
        nested = set()
        for d in ast.walk(node):
            if isinstance(d, ast.FunctionDef) and d is not node:
                nested |= {id(x) for x in ast.walk(d)}
        for sub in ast.walk(node):
            if id(sub) in nested:
                continue
            if isinstance(sub, ast.Return) and not getattr(sub.value, "value", sub.value):
                ctx = " | ".join(l.strip() for l in src[max(0, sub.lineno - 4):sub.lineno])
                if any(k in ctx for k in KEYS):
                    print(f"{f.name}::{node.name}:{sub.lineno}")
PY
```

What it still reports is the `except ValueError: return` idiom in tests that
assert a call *must* raise — genuine control flow, not a silent skip.

**The runner never mentioned the scripts it does not run.** `tests/` also holds
`blender_*.py` render probes and `audit_*.py` corpus audits — real verification
work that needs Blender, the game archives, or both. The runner has never
executed them, and used to say nothing, so their coverage was silently credited
to the suite. They are now **inventoried on every run**, with what each one needs,
under a heading that says their coverage is not included in the counts.

## 3. Defects the suite guards

### 3.1 D13 — a sparse LOD ladder selected nothing. **FIXED in 0.4.0.**

★ This was the open defect the previous release shipped with, and it is closed.

A ladder on disk is neither dense nor zero-based, and both facts cost a defect
apiece. **D2**: `3cee9f282bf0807f` partitions its gated meshes into levels
`{3, 4}`, so the default `level = 0` asked for a rung nothing carries and the
importer produced nothing. **D13**: `2fd6839161785e9c_ff91757c910ea7b6` (Liv's
body) partitions its six meshes into `{0, 3}`, so levels 1 and 2 fell in a HOLE
*between* the rungs, which a floor-and-ceiling clamp does not cover — asking for
LOD 1 imported **nothing at all**.

`package_reader.snap_to_ladder` is now the whole rule, in one expression shared
by `select_lod_objects` and `select_lod_draws` so the hole cannot land twice:
**snap DOWN to the greatest present rung `<= level`, and snap UP to the finest
rung only when the request is below the whole ladder.** Measured:
`2fd6839161785e9c_ff91757c910ea7b6` levels 1–2 now select **5 of 6** meshes,
**was 0 of 6**.

⚠ The one-line fix this page used to propose — *nearest rung by distance* — is
the **wrong** rule: on a `{0, 3}` ladder it answers level 2 with rung 3, a
*coarser* model than was asked for. Snapping down is the bias the module already
commits to (over-draw is visible and reversible, a missing limb is silent), it is
what a threshold ladder does, and it stays monotone so an LOD sweep still reads
as a ladder.

Pinned two ways: `tests/test_lod_ladder_hole.py` proves the rule on constructed
ladders with no game data at all, and `tests/test_real_package_invariants.py`
keeps the corpus half — that no package on disk selects nothing. ⚠ That second
pin was **deliberately inverted** when the fix landed: its old formulation passed
*because* the defect was live, so it would have gone silent rather than red.
`KNOWN_SPARSE_LOD_LADDERS` survives with a stated change of meaning — packages
whose ladder is sparse (a fact about the data), not packages the importer
deletes.

### 3.2 `eBlendTranslucent`

Unimplemented. Materials declaring it fall back to the nearest supported pass.
There is no test that asserts the correct appearance, because there is no
correct appearance to assert yet.

### 3.3 Dropped authored layers

19 of 44 audited materials drop an authored layer. 18 are provably invisible —
the layer's blend mask pins it at its OFF extreme. **One is not**, and that one
is an unexplained loss of authored content, recorded rather than hidden.

## 4. What only runs with your own data

Four groups, all skipped loudly on a clean checkout — 57 skips over 16 modules:

| group | skips | needs | how to enable |
| --- | ---: | --- | --- |
| `test_real_package_invariants` | 13 | extracted `.lemesh` packages under `blender_tool/exports/` | `python.exe blender_tool/extractor/le_extract.py --archive <hash> --all` |
| generated sidecars (`test_scene_materials_v2`, `test_scene_build`, `test_lights_sidecar`, `test_level_link`) | 17 | a sidecar written by a `scripts/` or `le_mesh/` generator | run the generator each skip reason names |
| the corpus-shape tests (`test_instance_lightmap_import`, `test_lightmap_uv_slot`, `test_skirt_decal_alpha`, `test_shipped_tangent`, `test_lod_ladder_hole`, `test_scene_set_lod`, `test_additive_blend`, `test_scatter_import`, `test_reflection_probe`, `test_light_import`) | 24 | specific archives extracted | extract the archive each skip reason names |
| `test_extractor_e2e` | 3 | the game data tree + Oodle + `pyoodle` | set `LONE_ECHO_DATA_ROOT` and `LONE_ECHO_OODLE_DLL` |

The extractor E2E test is worth calling out: while it is skipped, **nothing in
the suite executes the extractor at all**. Everything else tests the pure-stdlib
decode core and the add-on's non-`bpy` half.

## 5. Fixtures carry no game bytes

Every fixture in this repository is **constructed**, not extracted:

- light records are packed field by field at literal offsets, independently of
  the decoder's own offset table, so the two must agree or the test fails;
- the reflection-probe slice is **synthesised from the documented grammar** —
  23 selection boxes over 16 probes, the measured box → probe histogram, one
  shared parallax volume, a BC6H_UF16 256² cube with 9 mips — with values this
  repository makes up;
- `tests/expected/le_extract_e2e.json` stores a `sha256` of the texture-name set
  and a count, never the names, and a test enforces that it carries no game
  bytes or names.

The *shapes* those fixtures encode are the measured ones; asserting them here
locks the decoder and the conversion arithmetic. It does **not** re-prove the
corpus measurement, which lives in the other documents.

`scripts/scrub_gate.py` enforces this mechanically. Beyond the path rules
(`exports/`, `dist/`, `*.lemesh/`, `*.lescatter/`), it measures the **decoded
size of every literal in every shipped source file** and rejects any
`base64.b64decode` / `bytes.fromhex` / raw-bytes / long hex or base64 run at or
above 256 bytes unless the site carries an explicit
`# scrub-allow: embedded-bytes — <reason>`. A blob pasted inside a `.py` file is
on none of the path rules, which is exactly why that check exists.

### 5.1 Three classes the gate cannot see, and the call made on each

★ Worth stating plainly, because each one was found by hand after an automated
check said the tree was clean.

**Decoded game data in a `.json`.** A sidecar of decoded light records is a file
of floats: no path rule sees it, and no blob rule sees it either, because it is
not a blob. The rule that governs it is *generate, don't dump* — the deriving
code ships and the reader regenerates against their own install. Two fixtures
were removed under it for 0.4.0 and rebuilt as constructed ones.

**Constants transcribed out of a shipped shader.** A float is a float; nothing
mechanical can tell a measured coefficient from a compile-time literal copied out
of a disassembly. ⛔ **0.4.0's call: a module whose values are a verbatim second
copy of a shipped shader's literals does not ship**, and the exterior-vista
shading module that motivated the question is **deferred to 0.5.0** for exactly
that reason. What *is* enforceable is the provenance trail such work leaves, and
that is now a rule — see below.

**Paths and line numbers into the game's own source.** Published 0.1.0–0.3.0
carry **zero** citations of this class; the 0.4.0 candidate arrived with **241**
— source-tree paths, shader and asset-schema line references, a debug header
keyed by a build id, and disassembly tokens. The gate's `engine-source`
rule now rejects all of them, and `vcs-ref` rejects a commit id from the private
history. Both calibrate to **0 findings on published 0.3.0**, so they refuse
exactly what the release was about to add and nothing the project already
shipped. The convention they enforce: **cite the engine by its own symbol
names — `kLambdaSG5`, `CGVertexFormat::EUsage`, `NRadEngine::EBlendMode` — never
by a coordinate into a tree the reader cannot open.**

## 6. Blender-side verification

The add-on's `bpy`-dependent half is not covered by the unit suite. It is
verified by installing the built zip into a **factory-startup** Blender and
importing real packages:

```bash
python3 blender_tool/build_addon_zip.py
# then, in Blender: Edit > Preferences > Add-ons > Install from Disk…
```

0.4.0 was verified this way on Blender **5.1.1**: install from the built zip,
enable, all three import operators and menu entries registered, one `.lemesh`
imported (4 objects / 5,377 vertices / 5 materials) and one `.lescatter` placed
(200 instances).
