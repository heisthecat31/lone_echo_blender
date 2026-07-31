# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - 2026-07-31

Level-of-detail selection, a texture-role correctness fix, and a light decoder —
plus documentation that is honest about what still does not work.

### Added

- **LOD selection.** Lone Echo has **two** independent LOD systems and both are
  populated in retail data. Both are now decoded and selectable at import, with
  **LOD 0 (highest detail) as the default** on both import paths:
  - *Static-instance LOD* (`SGStaticInstanceLODData`), where every level of a prop
    is a separate mesh with its own instances. New module `le_mesh/static_lod.py`,
    validated against all 62 populated static-instance masters of a 102-archive
    corpus. Selected at import by `scatter_reader.filter_by_lod`.
  - *Mesh-list LOD chain* (`CGRenderParams.lodchildrenstart` / `lodchildrencount`
    plus `CGMeshListData.lodchildindices`), where the coarser levels are extra
    draws over later slices of the same index buffer. New
    `le_mesh.meshlist.assign_lod_levels`; selected by
    `package_reader.select_lod_draws`.
  - Both operators gain a **LOD Level** dropdown, including `Coarsest` and
    `All levels (stacked)` — the latter reproduces the pre-0.2.0 behaviour exactly.
  - The scatter render harness takes `lod=N` after `--`.
  - Placed objects are tagged with `le_lod_group`, `le_lod_level` and
    `le_lod_group_levels` custom properties.

  On one shipped level this drops the import from 21,394 instances / 6.30 M
  triangles to 8,288 / 3.67 M with an identical silhouette: **61.3 % of its
  instances were lower-LOD duplicates.**
- **Light decoding.** New `le_mesh/lights.py` decodes the 352-byte `SGLightParams`
  record out of a level's scene resource and converts it to Blender light
  parameters (type, world placement, linear HDR colour, watts, spot cone), and new
  `blender_tool/extractor/le_lights.py` writes a `lights.json` sidecar without
  decompressing a whole archive. **No light importer is shipped** — see *Known
  limitations*.
- **New documentation:** `docs/LOD.md`, `docs/MATERIALS.md`, `docs/LIGHTING.md`.
- **`scripts/scrub_gate.py`** — a release gate that scans every tracked file
  (binaries included) for absolute home paths, debug-symbol artefacts,
  research-log references, credentials, and committed game bytes. It stores **no
  private literal of its own**: usernames and private repository / data-tree names
  are supplied at run time via `SCRUB_PRIVATE_LITERALS` or a gitignored
  `.scrub_private`, because a gate that embeds the string it hunts for becomes the
  leak once published. `--self-test` covers false positives, known-bad strings, and
  the supplied-literal mechanism; `--require-literals` makes configuration
  mandatory in CI.
- Three read-only corpus audits: `blender_tool/tests/audit_static_lod_corpus.py`,
  `audit_lod_fields.py` and `audit_material_modes.py`.

### Fixed

- **Ten fabricated texture-role names.** Ten entries in
  `le_mesh/materials.py::INPUTNAME_ROLE` were labelled `"tentative"` and were
  **invented** — none hashed to the key it was filed under. All ten are now exact
  recovered preimages, verified by `symbol64(name) == key`. This changed real
  channel routing:
  - `layer1_alpha_map` (was `layer1_mask_b`) is **opacity** and was driving
    **Roughness**;
  - `layer0_back_lighting_map` (was `layer0_emissive_rgba`) is **translucency** and
    was driving **Emission** — it now has its own `TRANSLUCENCY_ROLES` list;
  - the two `*_composite_specular` maps were treated as **base colour** under their
    fabricated `*_rgba` names.

  There is **no glass-specific texture role**; the four `layer1_glass_*` names were
  invented. Guarded by three regression tests.
- **`Draw.is_lod_parent` called every LOD child a parent.** It OR-ed three fields;
  `lodchildrencount != 0` is the only reliable root predicate, because
  `lodchildrenstart` is a running cursor that stays non-zero on children and
  `lodprimsetidx` marks children rather than parents. A new `is_lod_child` predicate
  covers the other side.
- **21 LOD groups vanished at the importer's default level.** A group's first LOD
  entry can be unreferenced by any instance, making its raw levels start at 1, so a
  request for LOD 0 matched nothing and the prop disappeared (94 instances across 6
  masters). Levels are now rebased per group so the finest *drawn* level is 0.
- `blender_tool/extractor/le_extract.py` and
  `blender_tool/tests/probe_vertex_types.py` referenced a private repository path in
  comments; the optional scan-input location is now environment-configurable
  (`LONE_ECHO_SCAN_ROOT`).

### Changed

- **`.lescatter` package format → version 3**: adds a `lod` manifest block and a
  `blobs/instance_lod.bin` table of 12-byte records parallel to `instances.bin`.
  Purely additive — `instances.bin` stays byte-identical at 44 bytes per record, and
  version 1 and 2 packages load and import exactly as before.
- **`.lemesh` package format → version 2**: each draw's `lod` block gains `level`
  and `is_lod_child`. Also additive — a version 1 package reads as all-level-0.
- Add-on version is now `0.2.0`.
- `README.md` now states plainly what does and does not reach the renderer, instead
  of implying full PBR.

### Known limitations

Stated here because they are the honest status, not because they are new:

- **Transparency and emission do not reach the renderer at all.** Every exported
  `.lemesh` manifest carries `"materials": []`, and there are **nine breaks** in the
  decoder → manifest → builder → EEVEE chain. Two are fixed above; the remaining
  seven are documented file-by-file in `docs/MATERIALS.md`. Base colour and normal
  maps do work.
- **Lights are decoded, not imported, and importing them naively is wrong.** Most
  Lone Echo level lights are **specular-only** (of 118 decoded records only 49 carry
  `eEnableDiffuse`; on one 47-light level only 15 do) and sit on top of a **baked**
  lightmap this tool does not import — 86 of the 87 lit shaders bind both paths.
  Blender has neither, so adding these lights double-lights the scene. See
  `docs/LIGHTING.md`.
- **LOD triangle-count monotonicity is a tendency, not an invariant.** 196 of 72,004
  multi-level groups increase at some level, across 21 of 62 masters: the coarsest
  level often *merges* the parts, saving draw calls rather than triangles. Do not
  build a validator that asserts it.
- **`lodfadeslopeoffs` semantics remain unresolved.** Its four floats per LOD entry
  are carried verbatim as audit metadata and are **not** switch distances. What has
  been ruled out is listed in `docs/LOD.md` so it need not be re-tried.
- The **dynamic**-instance LOD system is decoded but its decoder is not part of this
  repository, because nothing in the `.lemesh` / `.lescatter` pipeline consumes it.
  Its record layout and measured properties are documented in `docs/LOD.md`.

## [0.1.0] - 2026-07-24

### Added

- Full-attribute mesh import: positions, normals, tangents, vertex colors, all UV
  sets, and skin weights.
- Per-draw PBR materials: base-color plus normal, including BC5 normal
  reconstruction, built as a Principled BSDF node graph.
- Skeleton / armature import, with meshes skinned to the reconstructed rest pose.
- Whole scatter-level import, placing every instance at its own per-instance
  transform while sharing one mesh datablock per unique mesh.
- Multi-material meshes: one material slot per draw, with each face assigned to its
  covering draw.
- A headless render harness supporting both the Workbench and EEVEE engines for a
  full PBR render.
- The `pyoodle`-backed offline extractor, with environment-configurable game-data
  and Oodle-runtime paths, that writes portable `.lemesh` and `.lescatter` packages.
