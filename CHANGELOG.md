# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.0] - 2026-08-01

Materials reach the renderer on the `.lemesh` path, cross-archive resolution stops
silently losing most of them, and the scene lights get an importer — off by
default, because turning it on naively is wrong.

### Added

- **Cross-archive material and texture resolution.** Neither textures nor
  materials live in the archive that binds them: shadersets are 100 % resident,
  but 88 of 115 texture bindings are external on one reference archive (31 of 31
  on another, which used to resolve *zero* texture roles), and materials are only
  **~19 %** resident. A local-only resolver fails **silently** — a missed texture
  is not extracted, a missed material falls back to `SGMaterialData` defaults and
  an `eMTForwardTransparent` material reads as plain opaque.
  - Two corpus-wide indexes fix it, generated from **your own** game install into
    `$LONE_ECHO_SCAN_ROOT` by new `scripts/le_texture_archive_index.py` and
    `scripts/le_material_archive_index.py`. **No index data is shipped.**
  - With the texture index the archive's local hashes are *unioned* with it to
    form the `SShaderInputData` needle set: 60 bindings found locally vs **212**
    globally on the reference archive, exactly reproducing a precomputed scan TSV.
    ⛔ Dropping the gate entirely was measured and rejected: 1,884 bindings at
    89 % false positives.
  - Foreign materials are loaded from their home archive one archive at a time,
    each decompressed primary dropped before the next is opened, and cached
    process-wide so an `--all` run does not reopen the same homes per mesh-list.
  - **Absence is loud, never silent:** `le_extract.py` prints exactly what is lost
    when either index is missing. Silent local-only degradation is the bug they
    exist to fix.
- **A light importer** (`addon/lone_echo_import/light_import.py`, plus
  **File > Import > "Lone Echo Lights (.json)"**). **OFF BY DEFAULT**, and when
  enabled it imports only the `eEnableDiffuse` subset: importing every light is
  **7.06× brighter** on identical receivers, because the specular-only majority
  (112 of 118 records set `eEnableSpecular`, only 49 set `eEnableDiffuse`) sits on
  top of a baked diffuse Blender does not have. `light_set="all"` is an explicit
  opt-in that warns and parks the specular-only lamps in a hidden collection.
  Nothing undecodable is invented — `filtersize` (a shadow PCF width, *not* a
  radius), the cone `falloff` exponent, the runtime range offset and the
  receiver-gating masks all ride along as inert `le_*` custom properties.
- **Baked-lightmap decode** — new `le_mesh/lightmap.py`
  (`CGLightMapResourceWin7` → five-texture sets, the mesh binding, the
  scene-sibling and dynamic-instance joins) and
  `addon/lone_echo_import/lightmap_builder.py` (the node graph). The bake is an
  **SG5 array**: 13 pages × 5 spherical-gaussian lobes, page-major,
  `slice = page*5 + i`. Blender exposes only slice 0 of an array DDS, so the
  importer splits the slices itself; colour space is `Linear Rec.709`, never sRGB
  (that is a silent ×2.31–2.50 double-gamma).
- `le_mesh/lights.py` gains `encode_light` (byte-exact inverse of the decoder),
  `record_from_fields`, `blender_matrix_rows`, `select_lights`, `not_derivable`
  and `range_offset_divergence`; the `lights.json` sidecar goes to **version 2**
  (v1 still loads) and `le_lights.py` gains an archive-free `--from-json` rebuild.
- `audit_material_modes.py --fixtures` audits already-exported `.lemesh` packages
  with no archive load at all.
- 256 new tests (398 total, all archive-free).

### Fixed

Five of the nine documented breaks in the decoder → manifest → builder → EEVEE
chain (see `docs/MATERIALS.md`); two were fixed in 0.2.0.

- **`k_alpha` was never applied**, so a material with `k_alpha = 0.25` and no
  opacity map rendered fully opaque. It is now the last term of the alpha chain,
  and a material carrying it with no other transparency evidence is upgraded to a
  blended pass so it is visible at all.
- **`blend_method` is a dead alias on Blender 4.2+** — `OPAQUE`, `CLIP` and
  `HASHED` all read back as `HASHED` and collapse to `DITHERED`; only `BLEND`
  gives `BLENDED`. `mat.blend_method = "CLIP"` could therefore never clip. The
  pass is now driven from `surface_render_method` and read back, and a cutout is a
  `Math(GREATER_THAN)` node.
- **`mattype` and `blend_mode` were carried in the spec and never read.** The pass
  and the blend equation now both come off disk.
- **Emissive intensity was read from layer 0 unconditionally** while the emissive
  *map* often sits on another layer — 2.0 instead of 25.0 on one shipped bridge
  material, **12.5× too dim**. It now comes from the layer whose map was routed.
- **`layerN_opacity_map` was wired to Blender's Alpha socket.** It is a float3
  *transmission tint* (`color.rgb += background * opacity`), so coloured glass came
  out uniformly see-through instead of tinting what is behind it. It now becomes an
  added Transparent BSDF, and `channels["opacity"]` survives only as a flagged
  deprecated mirror of `channels["transmission"]`.
- **`layerN_composite_specular` and `layerN_specular_map` were routed to Base
  Color.** Both are F0. They now drive Principled's `Specular Tint` with the
  `Specular IOR Level` left at its 0.5 neutral point — that path is linear and
  **unclamped**, and matched a Glossy BSDF of the target F0 to 0.00 % at every F0
  in {0.01 … 1.0}. Leaving it unwired was not neutral: Principled's default pins
  F0 at 0.04, measured 6×–20× too dark on shipped composite data and 4× too bright
  on the `specular_map` panels.
- **Roughness must take `composite_components.R` RAW.** The engine's GGX alpha is
  `sqrtroughness²` while Blender's is `Roughness²`, so squaring it made Blender's
  alpha `sqrtroughness⁴` and the peak highlight **2.4×–920× too bright**. AO
  (`.G`) is exposed but deliberately left unconnected — the engine applies it to
  the ambient term only, and Principled has no occlusion input.
- **`image.alpha_mode` defaulted to `STRAIGHT`**, which multiplies RGB by alpha on
  load: a measured albedo texel came out **70× too dark** and an `alpha == 0` texel
  came out black. Every image is now `CHANNEL_PACKED`, with a read-back guard.
- **`is_emissive` was gated on `bakeemissivecolor`**, which is `(0,0,0)` on every
  genuinely emissive material inspected — it under-reported emission on all of
  them. The gate is now the authored per-layer emissive state; the old bake-time
  signal survives as `bake_emissive_nonzero`.
- **Texture extraction assumed the texture was local** in direct mode, so
  `--textures` silently extracted nothing for 88/115 of one archive's textures.

### Changed

- **Material routing is layer-aware.** It is now (role suffix → channel) × (layer
  index) rather than a flat first-present-wins list, so a material carrying
  `layer0_emissive_map` *and* `layer1_emissive_map` keeps both instead of dropping
  one. The merged `channels` view is unchanged for consumers: lowest layer wins.
- **Layer blend-mask compositing** is decoded and built: `saturate(mask.R × scale
  + offset)` gates every other channel of the layer that owns the mask, per
  channel via `layerN_<channel>_blend_alpha`. ⚠ Every shipped
  `layerN_blend_mask_offset` in the corpus is `-1.0`, which pins the layer at its
  animated OFF extreme — a runtime state we cannot reproduce, reported as
  `suppressed_at_rest` rather than editorialised. `opts["layer_blend_mask_offset"]`
  overrides it. The **vertex-blend** term is deliberately not built: it needs a
  colour set `mesh_builder` does not import and a permutation bit that is not on
  disk.
- Live (direct-from-archive) role resolution is now the **default** in
  `le_extract.py`; it reproduces a precomputed scan TSV exactly on the reference
  archive (212 bindings / 57 shadersets both ways) and works on archives no TSV
  covers. `--tsv-materials` selects the old path; `--direct-materials` is a
  retained no-op alias.
- `.lemesh` manifests gain `numlobes` per object, and imported objects gain
  `le_lm_slice_index` / `le_lightmap_numlobes` custom properties — the lightmap
  PAGE was previously read from the manifest and discarded, so anything wiring
  lightmaps after import silently fell back to page 0.
- `le_mesh/material_scalars.py` now owns the enum tables and the authored
  parameter vocabulary (regenerated, and gaining the non-layer parameter GROUPS
  that recovered `pom_height_map`); `audit_material_modes.py` re-exports them.
- Add-on version is now `0.3.0`.
- The default scan-input directory is `scan_inputs/` (still overridden by
  `LONE_ECHO_SCAN_ROOT`).

### Known limitations

- **`.lescatter` imports still carry base colour + normal only.** Two of the nine
  chain breaks are unfixed and both are in the scatter path: the sidecar writer
  computes the other channels and then drops them, and the `.lescatter` manifest
  has no `materials` array at all.
- **The lightmap is decoded but not auto-wired.** The resource, the mesh join and
  the node graph exist and are tested, but `material_builder` does not call
  `lightmap_builder`, and the extractor writes no lightmap block into the
  manifest. Driving it today means calling `wire_lightmap` yourself.
- **What the 5th SG colour slice per page is remains unresolved.**
  `CGMeshData.numlobes` reads 4 on 1221 of 1221 shipped meshes, so 5 is
  `numlobes + 1`; both "4 lobes plus an extra" and "a 5-lobe bake whose `numlobes`
  means something else" fit, and nothing measured separates them. The two BC5 AO
  channels' semantics are likewise not decoded.
- **Vertex-colour albedo (`eDiffuseVertexColor`) is gated and unexercised.** The
  per-mesh flag builds a `<mat>__vcol` material variant, but no checked-in test or
  fixture reaches that path.
- **Shader permutation bits are not on disk** and are never guessed: clip vs
  dither, alpha-to-coverage, premultiplied alpha, output alpha, vertex-colour
  enable, `enable_specular`. Where one is needed it is assumed and the assumption
  is recorded.
- **The specular residual is not fixable by wiring.** The engine's GGX visibility
  uses the Burley remap `alpha = ((m+1)/2)²`, Blender uses Smith with
  `alpha = roughness²`. Equal head-on; Blender is ~1.4× brighter at 60° and ~9× at
  85° in the mirror configuration.
- The extractor changes **are** end-to-end verified against real archives: both
  corpus indexes were rebuilt from a full 1,244-archive scan and one archive was
  re-extracted with `--textures`. 78% of its textures and 70% of its materials
  resolved from foreign home archives, and with the indexes removed the same run
  degrades to 22/100 materials carrying a real `mattype` — the failure the
  indexes exist to prevent, and which `warn_missing_indexes()` now reports.
- Everything listed under 0.2.0's *Known limitations* that is not contradicted
  above still holds — in particular the LOD triangle-count non-monotonicity and
  the unresolved `lodfadeslopeoffs` semantics.

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
