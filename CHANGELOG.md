# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.5.0] - 2026-08-07

The exterior vista's shading model, read off seven of the level's own pixel
shaders — and the one release in this project's history that ships a module you
cannot reproduce from this repository. That is stated plainly below, in the
README's status table and at the top of the module itself, because it reverses a
call 0.4.0 made and the honest version of a reversal names what it costs.

### ⛔ Read this first — what 0.5.0 ships that it cannot back up

0.4.0 deferred this work for four reasons. Two are now resolved and **two are
still true**:

| 0.4.0's reason | 0.5.0 |
|---|---|
| the constants are a verbatim second copy of a shipped shader's literals | ⛔ **still true.** They are *transcribed*, not derived here. |
| the light-side fixture was decoded game data checked into the repository | ✅ **gone.** The rig is now **constructed in code** and pushed through the public `le_mesh.lights` encode/decode round-trip; no decoded sidecar is shipped. |
| the module depends on a disassembler that is not public | ⛔ **still true.** `le_shaderset_disasm.py` is not part of this tree, so `le_mesh/vista_shader.py` ships **unreproducible from the public tree alone**. |
| the work itself was open and reported so | ✅ **closed** on the four questions this release answers, and the ones that remain open are first-class rows in the README status table. |

⚠ **What the tests do and do not prove.** `tests/test_vista_shader.py` (72 tests)
re-types every expected value from the disassembly independently of the module,
so a failure means the two disagree rather than that a constant was compared with
itself. That catches a **transcription error** and nothing else. It is a
consistency check over a transcription, not a reproduction of one, and it is the
only test module in this repository carrying that caveat.

⛔ **This is a boundary, not a precedent.** 0.4.0's rule — a module whose values
are a verbatim second copy of a shipped shader's literals does not ship — is
reversed for this module and stands for every other. See
[docs/TESTING.md](docs/TESTING.md#transcribed-constants).

### Added

- **`le_mesh/vista_shader.py`** — the exterior vista's shading, from the shipped
  pixel shaders of seven shadersets: the planetary body, the sun card, the
  skydome, the ring haze, the ring sheet, the moons and the dig-site FX cards,
  plus the debris rock's lightmap verdict. Pure stdlib, `bpy`-free, and every
  per-frame value it cannot decode is an explicit free parameter rather than a
  folded-in literal.
- **`base_color_factor` provably never reaches these shaders.** They declare
  **no material constant buffer** at all, so the material record's `bakecolor` —
  which is what the importer surfaces as `base_color_factor` — cannot arrive at
  the GPU. On the planet body the shader's own coefficients are **14.2×** the
  value the importer was using. `albedo_correction()` returns `(None, why)` for
  any shaderset that has not been disassembled, so an unmeasured material is left
  exactly as the importer built it.
- **The skydome is ordinary depth-tested geometry**, which settles a question the
  render harness had been carrying with both answers renderable. The dome's
  vertex shader applies the view matrix's translation row (no camera pin) and
  passes the full projection through with no reversed-Z pin; its pixel shader
  declares `SV_Target 0/1` only — no `SV_Depth`, no discard. The harness's dome
  mode default therefore moves from `composite` to the new **`skydome=engine`**.
  ⚠ `engine` also clears the dome's *non-camera* ray visibility, because a closed
  dome is light-tight to a path tracer and that is a fact about Cycles, not about
  the engine, which traces no light at all.
- **Which light lights an exterior: `ePrimaryDirLight`, not the brightest.** The
  exterior level ships four light records; exactly one carries `ePrimaryDirLight`
  (and `eBakeIndirect`) and it peaks at **10 W/m²** against the unflagged
  directional light's **80**. Both point lights sit inside the play area with a
  100-unit range against a body 54,862 units away, and the body's shaderset binds
  no clustered-light path they could arrive through at all.
- **Neither directional light reaches the planet's visible disc**, and it is
  arithmetic rather than an impression: the body's diffuse is a *wrapped* Lambert,
  `saturate((N·L + 0.25) × 0.8)²`, identically zero for `N·L ≤ −0.25`, and both
  shipped lights put the sub-observer point at `N·L ≈ −0.595`. ⚠ The wrap is
  body-specific — the moons and the debris rock take a plain `saturate(N·L)` and
  **do** receive real direct light from the same records.
- **The lightmap mode is a property of the SHADERSET, with three answers, not
  two**: `baked` (the shader's only light is the SG5 lightmap), `ambient` (it
  adds a live directional on top, with no double-count because
  `k_dirlight_occlusion_map` scales only the live term) and `neither` (it binds
  no colour lightmap at all). All three occur on one level, which is why one
  import-wide flag cannot serve it; the harness gains `mesh_lightmap=auto`.
- **The planet's bright limb is the specular F0, Fresnel-mixed toward white** —
  not a tint laid over the disc. The disc centre is the blue end and the limb the
  neutral bright end, so the rim's hue is the hue of the incident radiance. With
  the shader's own height-correlated Smith visibility on top, limb/centre runs
  **25×–480×**; omitting the stack removes the limb rather than dimming it.
- **The scene-fog epilogue**, verbatim in three of the exterior shadersets and
  the last thing they do. The ramp lookups are half-texel inset on a **256-texel**
  ramp (`255/256` and `1/512`), slice 0 is distance and slice 1 is height, and the
  fog alpha is lerped by the same height ramp as the colour. `measured` against
  the engine's own reflection probe over nine directions spanning 3.4×, the
  shipped planet disc carries only **0.150** (sd 0.008) of the radiance the
  unfogged terms compute — a hard pointwise bound of **≥ 85 % fog**, since
  `p = (1−f)·c + f·F` with `F ≥ 0` gives `1 − f ≤ p/c`.
- **`tests/test_vista_shader.py`** — 72 archive-free tests, and the level's own
  four-light rig **constructed in code**, round-tripped through the 352-byte
  `SGLightParams` grid, and self-checking: it asserts `direction ==
  R(orientation)·(0,0,1)` and `farp == attenuation.z` on every record, the
  invariants measured on 118/118 shipped ones.
- **`tests/blender_vista_render.py` is the full harness** (1,085 → 3,250 lines):
  the decoded `CGLight` rig (`sun=lights`), the shader-confirmed material
  overrides (`vista_shader=`, on by default), the planet's three detail plates and
  four flow-warped UV chains (`saturn_detail=`), the ring sheet's probe-cube
  ambient (`ring_env=`), additive haze and FX cards (`haze_additive=`,
  `fx_card_additive=`), the ring back-face gate, the scene fog (`fog=`, **off**
  by default), a `colourless_surface=transparent` default that stops a material
  resolving no colour channel from drawing a flat card, and strip rendering plus
  tile/denoise controls for frames too large for one host buffer.

### Changed

- `SHADERSET_TERMS` rows name **the shaderset and stage** their arithmetic came
  from (`"<hash> pixel shader"`) rather than a disassembly listing path, and the
  harness stamps that on each material it overrides as `le_vista_shader_source`.
- `docs/FORMATS.md` gains a **`measured`** row in the evidence-tag table. It was
  already in use in published 0.4.0 and was the one tag the table did not define;
  the `shader-confirmed` row now also says that a site transcribing shipped
  literals rather than deriving them has to say so.
- The suite is **1,034 tests over 53 modules**: 977 passed / 0 failed / 57
  skipped on a clean checkout, 987 / 0 / 47 with a local export.

### Known limitations

- ⛔ **`le_mesh/vista_shader.py` is not reproducible from this repository** — see
  the top of this entry. Every other module here can be re-derived from your own
  install with the code that ships.
- ⛔ **The vista's per-frame terms are not decodable from a level and nothing
  here is fitted to art.** `k_world_ambient`, `k_world_ambient_spec` (which is
  also `rim_gain`, one constant with two consumers) and every input to the fog
  epilogue live in `SGPerFrameConstants`. They default to 1.0, 1.0 and fog
  **off**, and any other value is the caller's stated choice.
- ⚠ **The probe cube's LOD cannot be reached from a node graph.** The ring
  sheet's ambient is a cube fetch at LOD 3.56 and `ShaderNodeTexEnvironment` has
  no LOD input, so the harness picks `round(lod)` from the probe's own on-disk mip
  chain and drops the fractional part. A stated approximation, not a fit.
- ⚠ **`eBlendLinearDodge`'s source factor is still unrecovered.** Moot on the
  haze cards (every vertex carries alpha 1.0, so `(ONE,ONE)` and `(SRC_ALPHA,ONE)`
  agree) and decisive on the FX cards, where both readings stay renderable
  (`fx_card_src_alpha=1|0`) rather than one being asserted away.
- Everything carried over from 0.4.0 is unchanged: `eBlendTranslucent` is not
  implemented, the duplicated back-face shell is still drawn, 19 of 44 audited
  materials drop an authored layer (1 unexplained), reflection-probe mips beyond
  0 do not reach a material, and `.lescatter` imports still carry base colour +
  normal only.

## [0.4.0] - 2026-08-05

The material binding is read from the shader's own reflection data, roles are
resolved corpus-wide with a policy that refuses rather than guesses, characters
import as characters, levels can be placed, normal maps finally run on the
tangent basis the game ships, and the suite now opens real packages instead of
only synthetic ones.

### Added

- **RDEF is the binding *and* the name source** (`le_mesh/dxbc.py`). A shaderset
  with no `SShaderInputData` array is read through its DXBC `RDEF` chunk:
  `symbol64(rdef_name − "_decl") == textureassetid`, **74 verified / 0
  mismatched**. This is what fixes array-less shadersets, and it recovers ASSET
  NAMES rather than only hashes.
- **A corpus-wide `texture_hash -> role` index** (`le_mesh/role_index.py`) with an
  explicit unanimity policy. Over 25,694 binds / 2,194 textures, 5.41 % of
  multiply-declared textures carry more than one role — 4.44 % differ only in the
  layer index (benign) and 0.96 % in the suffix (real authored ambiguity).
  **The suffix must be unanimous or nothing is applied.** Generate the index from
  your own install with the new `scripts/le_role_index.py`; nothing is shipped.
- **The role ladder** — array → archive → corpus → DXGI `FORMAT`, in that order,
  with the provenance of every binding recorded per channel. 52.4 % of shadersets
  ship no array at all, so the ladder is the common path, not the fallback.
- **Two-lobe specular**, reproduced rather than collapsed to one.
- **The shipped tangent basis is consumed** (`material_builder._shipped_tangent_normal`,
  option `shipped_tangent`, default ON). Blender will not accept an authored
  per-loop tangent — `mesh.loops[].tangent` is read-only and recomputed by
  mikktspace from the active UV layer — so the TBN is rebuilt in shader nodes:
  `T' = normalize(T − N·dot(N, T))`, `B = cross(N, T')·sign(w)`,
  `out = normalize(T'·n.x + B·n.y + N·n.z)`, with an `OBJECT→WORLD` transform on
  `T` because the stream is in mesh space and `Geometry.Normal` is world space.
  ⛔ It **degrades and never blackens**: the old `ShaderNodeNormalMap` leg is kept
  and mixed to wherever `length(le_tangent) < 0.5`, so an object shipping no
  tangent renders exactly as before, per pixel.
- **`tangent.w` is fully decoded.** It takes exactly four values (−1.0, −0.5,
  +0.5, +1.0) over 509,266 vertices — a sign *and* a magnitude. **Sign = the
  bitangent handedness**, agreeing with the shipped UVs on **397,082 of 397,082
  vertices (100.00 %)**. **Magnitude tags a duplicated back-face shell**:
  |w| = 0.5 marks a second copy of every vertex, position-identical to its
  |w| = 1.0 partner (109,400/109,400) with an exactly negated normal (99.92 %)
  but a negated tangent on only 65.67 % — the back shell carries its own frame.
  A fifth value is refused loudly rather than rounded.
- **`color1` import** on the mesh path.
- **Character assembly** (`le_mesh/attach.py`) — a character is an actor node plus
  NAMED components, not a mesh-list.
- **Scene placement and the parent-level edge** (`le_mesh/scene_build.py`,
  `le_mesh/level_link.py`), including the level → parent-level upward edge.
- **Vista fitting** (`le_mesh/vista_fit.py`) — skydome sphere, ring plane and
  annulus, oblate planetary body — plus `tests/vista_measure.py`, a pure-stdlib
  report generator over extracted packages.
- **A camera-framing solver** (`le_mesh/framing.py`).
- **A lights sidecar schema** (`le_mesh/lights_sidecar.py`).
- **Reflection probes** (`le_mesh/reflection_probe.py`,
  `addon/lone_echo_import/probe_builder.py`) — `CGReflectionProbeResourceWin7`
  decodes to selection boxes, probe points, per-probe BC6H_UF16 cube arrays and
  DDS writers. 94 resources across 90 archives; the shipped cube is 256² with
  9 mips. ⚠ Blender has no cube-texture image type, so the importer writes a
  face strip and an equirect resample rather than a native cubemap, and the mip
  chain beyond mip 0 is not surfaced.
- **`.lescatter` package version 5**: per-instance baked lightmap stream (page +
  per-vertex UVs) out of `SGPackedInstanceData`, opt-in behind
  `--instance-lightmap`. Purely additive — a v1–v4 package still loads.
- **`scripts/le_streaming_texture.py`** — the streaming-texture decode path the
  extractor needs for shared character/prop textures, which are stubs on the
  inline path.
- **Tests that open real packages and run the extractor end to end**
  (`tests/test_real_package_invariants.py`, `tests/test_extractor_e2e.py`), plus
  a runner that **counts skips, prints every skip reason, and inventories the 25
  scripts in `tests/` it does not execute**. A green run with silent no-ops is
  how a real defect survived a whole cycle of green suites.
- `tests/test_lod_ladder_hole.py` and `tests/test_shipped_tangent.py` pin the two
  headline fixes below, and `tests/blender_tangent_probe.py` verifies the tangent
  graph inside Blender by reading the socket layout back.

### Fixed

- ★ **34 tests that could not reach their data were `return`ing, which
  `unittest` counts as PASSED.** 27 of them executed no assertion at all on a
  clean checkout while reporting a pass. They now raise `SkipTest` naming the
  missing artefact and the command that produces it. ⚠ **The clean-checkout pass
  count therefore FELL, 942 → 905, with nothing removed and nothing broken** —
  962 tests either way, 0 failed either way. The suite stopped claiming coverage
  it did not have.

- **Character LOD was three systems, not one.** `SSceneSetMask` bit N == level N
  is false on 4 of 12 roster mesh-lists, where the bits partition the body in
  SPACE rather than by detail. Reading them as an LOD chain deleted a character's
  left arm and hands. A refusal heuristic now draws EVERYTHING when the sets are
  not a geometric chain — over-draw is visible and reversible; a missing limb is
  silent.
- ★ **Every normal map had an inverted green channel.** The importer flips V for
  Blender, which inverts Blender's UV-derived bitangent: `loop.bitangent_sign`
  agreed with the shipped `sign(tangent.w)` on **0.0–0.8 % of loops** on 11 of 13
  measured meshes. The shipped basis never consults the UV derivative, so
  consuming it removes the inversion at the source. Measured A/B on a character
  portrait: **18.837 % of pixels differ, 5.706 % by more than 8/255**, against a
  re-render noise floor of **0.0062 %**; a flat decal sheet moves **0.003 %**,
  which is at the floor — the asymmetry the fix predicts.
- ★ **A sparse LOD ladder selected nothing** (was listed as a known limitation of
  this release and is fixed in it). `2fd6839161785e9c_ff91757c910ea7b6` (Liv's
  body) partitions its six meshes into levels `{0, 3}`, so levels 1 and 2 fell in
  a HOLE *between* the rungs and imported nothing at all — the whole character
  disappeared. `package_reader.snap_to_ladder` is now the single rule, shared by
  `select_lod_objects` and `select_lod_draws`: **snap DOWN to the greatest present
  rung `<= level`, and snap UP to the finest rung only from below the ladder.**
  Levels 1–2 now select **5 of 6** meshes, was **0 of 6**.
  ⚠ The "nearest rung by distance" fix previously written down here was the
  **wrong** rule — on a `{0, 3}` ladder it answers level 2 with rung 3, a coarser
  model than was asked for.
- LOD selection returned the empty set when the finest scene set was not bit 0.
- A mesh in no scene set vanished at every level >= 1.
- Scene-set VARIANT draws (byte-identical index range, different set) were both
  emitted, putting tens of thousands of co-planar duplicate triangles on a model.
- **The lightmap UV set is resolved by semantic SLOT, not by the literal
  `"uv1"`** — the corpus has 64 objects on `uv2` and 29 on `uv3`, and the
  hardcoded name was wrong on every one of them.
- **`attenuation.w` is `maxfadedistance`, not a second cull radius.** The range
  stays `attenuation.z`; only the fade-offset term moves. The two differ on 11 of
  118 shipped lights, which is the only shape that can tell them apart.
- Materials, mesh and scatter builders no longer disagree about which draw owns
  which face on multi-material meshes with scene-set variants.

### Changed

- Add-on version is now `0.4.0`, and its menu entry covers `.lemesh`,
  `.lescatter` and `.json` lights.
- Optional scan inputs are consistently located through `LONE_ECHO_SCAN_ROOT`.
- The role tables, the material scalars vocabulary and the texture-role suffix
  list all move behind the role ladder; a role only reaches a Principled channel
  if its suffix is in the curated `CHANNEL_ROLE_SUFFIXES` list.
- ★ **The evidence vocabulary is defined, and reduced to seven tags.** This
  repository annotates claims with provenance tags, and until now none of them
  were defined anywhere public. [docs/FORMATS.md](docs/FORMATS.md) now defines
  exactly seven — `stream-confirmed`, `corpus-confirmed`, `shader-confirmed`,
  `name-confirmed` / `name-only`, `engine-confirmed`, `export-validated`,
  `inferred` — and every site in the tree conforms to them. Tags that named the
  private reverse engineering rather than the finding are gone; in particular
  `decl-confirmed` / `decl-only`, which no published release ever used, are
  replaced by `name-confirmed` / `name-only`, which say the same thing without
  naming where the declarations were read.
- ★ **The repository cites the engine by its own symbol names — `kLambdaSG5`,
  `CGVertexFormat::EUsage`, `NRadEngine::EBlendMode` — and never by a path or
  line number into a source tree, a debug header or a disassembly listing.**
  That was already the convention of 0.1.0–0.3.0, which contain zero citations of
  that class; it is now written down and enforced. `scripts/scrub_gate.py` gains
  two rules, `engine-source` and `vcs-ref`, both calibrated to **0 findings on
  published 0.3.0**.

### Deferred to 0.5.0

- **The exterior vista's shading model.** The skydome, ring plane and planetary
  body are fitted and import (`le_mesh/vista_fit.py`); reproducing what the
  engine's own shaders do to them is not finished, and the module that exists
  would ship a set of constants that are a verbatim second copy of a shipped
  shader's literals. Held, with its tests and its harness, rather than shipped
  half-ratified. See [docs/TESTING.md](docs/TESTING.md) §5.1.

### Known limitations

- **`eBlendTranslucent` is not implemented.**
- **A duplicated back-face shell is drawn.** Character meshes ship two shells and
  the importer emits both, because nothing has yet established whether the engine
  draws both or culls one. It is now *detectable* — every mesh records
  `le_tangent_w_states` and `le_tangent_w_has_back_shell` — rather than invisible.
- **19 of 44 audited materials drop an authored layer.** 18 are provably
  invisible; **1 is not**.
- **Reflection-probe mips.** Blender has no cube-texture image type, so only
  mip 0 of each face reaches a material; the roughness-varying prefilter the
  probe stores is decoded and written to DDS but not wired.
- **`.lescatter` imports still carry base colour + normal only** on the material
  path; the v5 per-instance lightmap stream is extracted but not auto-wired.
- The specular residual against the engine's Burley-remapped GGX visibility is
  unchanged from 0.3.0 and is not fixable by wiring.

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
