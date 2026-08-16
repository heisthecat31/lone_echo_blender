# Echo VR support

Adds full **Echo VR** and **Lone Echo 2** level extraction alongside the existing
Lone Echo 1 pipeline, plus a desktop app that drives all three without a command
line.

Echo VR runs on the same Ready At Dawn engine as Lone Echo, but ships its assets
through a different container family, so almost none of the original importer
applied directly. Everything below is decoded from the shipped data — each claim
here is backed by a measurement or a cross-check, not by inference.

---

## Highlights

### Levels come out placed, textured and named

Extract a level by **name**, not by hash:

```bash
python scripts/evr_scene_extract.py mpl_arena_a --dir <extract> --out <output>
```

A hash *is* the CSymbol64 of its authored name, so the lookup tables are
recovered preimages rather than labels invented here — `CSymbol64("mpl_arena_a")`
really is `576ed3f8428ebc4b`.

| game | levels named |
|---|---|
| Echo VR | **32 / 34** |
| Lone Echo 2 | **156 / 302** |

Unnamed levels are written as `null` and displayed as hashes marked *(unnamed)*,
so the data states what is unknown instead of hiding it.

### Static-instance placement, from the engine's own join

Every scattered prop in a level is placed by joining two component resources:

```
CStaticInstanceModelCR   [dir] n × 24B   {marker, ENTITY @+8}
                         [recs] n × 88B  {marker, MODEL @+0x20}
CTransformCR             176B rows       {KEY @+0x30 == ENTITY,
                                          rotation @+0x48, translation @+0x58,
                                          scale @+0x64}
```

Plain world-space floats — nothing quantized. The join is total:

```
mpl_lobby_b2      465 / 465 placed
mpl_arena_a       732 / 732
mpl_lobby_b_arena 191 / 191
mpl_lobby_b_combat 859 / 859
```

**Verified against the level's own geometry.** Distance from each prop to the
nearest built surface, against a uniform-random control:

```
random control   median 165.92
decoded props    median   2.63     -> 63x closer to real geometry than chance
                                      100% of props inside the level
```

`mpl_arena_a`'s placements also come out symmetric about the field centre to the
decimal (x ±16.0, z ±78.1), as the real arena is.

### Sublevels merged into one scene

A map like `mpl_combat_fission` ships as a parent plus four sublevels. `--full`
merges the set into a single package:

```bash
python scripts/evr_scene_extract.py mpl_lobby_b2 --dir <extract> --full
```

```
mpl_lobby_b2        3 levels   (b2 + b_arena + b_combat)
mpl_combat_fission  5 levels   (+ cargobay, climax, pantheon, prologue)
mpl_combat_gauss    5 levels   (+ section01a/01b/02/03)
```

The relationship is not in the archive closure — a level's closure references
only itself — so grouping is derived from the authored names.

### Materials and textures

* **Per-material texture tables** — `SGMaterialData`'s sixth container is a
  `CMap<CSymbol64 slot, CSymbol64 texture>`: the binding on disk, no inference.
* **Per-draw materials** — `CGRenderParams` (112 B/draw, `matidx @+32`) gives
  each draw its own material instead of assuming draw *i* uses material *i*.
* **Shader-set fallback** — when a mesh record's shader-set field does not
  resolve, the material→shader-set index is consulted before falling back to
  format guessing. On `mpl_combat_dyson` this rescued **36 materials** that were
  previously guessed.
* **Honest confidence** — a binding inferred from DXGI format is marked
  `tentative` with `binding_guessed: true`, never `confirmed`.

### Baked lighting

Per-level lightmaps are decoded from `CGLightMapResourceWin10` and collapsed out
of the engine's own SG5 / SH4 basis using the shader's weights:

```
irradiance/Pi = Σ z_i · (2 / kLambdaSG5) · kSG5Scale · slice[page·5 + i]
```

`lobes = ambient.arraysize / occlusion.arraysize` is exactly 5 or 4 on every
shipped level, selecting the branch.

Placed lights come from `CGSceneResource` section 1 (`SGLightParams`) with type,
colour, intensity, range and direction. `mpl_arena_a` carries **138** — 2
directional, 26 spot, 110 point — and the map's warm/cool team split is right
there in the two directional lights: `(1.000, 0.583, 0.431)` against
`(0.584, 0.820, 1.000)`.

### Texture size control

Large levels otherwise exhaust VRAM, because Blender uploads textures
**decompressed** — a 2048 BC1 that is 2.7 MB on disk costs ~16 MB in VRAM.

```bash
--max-texture 1024      # absolute ceiling
--texture-divisor 2     # half every texture, relative to its own size
```

Both drop leading mips — exact selection, never resampling.

---

## Desktop app

```bash
python app/echo_extractor.py        # or: Echo Extractor.bat
```

* Installs / updates the Blender add-on on launch, across every Blender install.
* Auto-discovers each game's data folder from the install root
  (`_data/<build>/[radNN/]win10|win7`) and Lone Echo's `bin/win7` Oodle DLL.
* Bundles the external extractors into `app/extract/` so a working install is
  self-contained.
* Levels are shown as **coloured bundles** — click a bundle for the merged
  scene, or a level inside it for that one alone.
* Free-space check, live progress, and a Cancel that terminates the whole
  process tree.
* Remembers every path per game.

Each title's extraction genuinely differs, and each is verified end to end:

| game | method | test result |
|---|---|---|
| Echo VR | `evrtools -mode extract -package <hash>` | 43 files, exit 0 |
| Lone Echo 2 | `evrtools -mode extract -package <hash>` | 382 files, exit 0 |
| Lone Echo 1 | pyoodle + `le_extract.py` per archive | 3 meshes / 59 files, 0 failed |

---

## Blender add-on

* Imports `.lescatter` packages with LOD selection, instancing and materials.
* Auto-loads `lightmaps.json` beside the manifest — no extra file to pick — and
  builds the level's lights.
* Emissive maps are split by material shape: a material binding an emissive map
  and **no albedo** has nothing to occlude, so it emits; one with a base colour
  keeps the ambient-occlusion wiring.

---

## Known limitations

Stated plainly, because they are the next things to fix:

* **A level's own base geometry collapses onto one material.** Props resolve
  per-draw correctly (196 and 144 distinct materials on arena and dyson), but
  the level shell does not — `CGRenderParams.matidx` reads a constant for
  mesh-list models. The correct palette *is* present in the companion
  `CGSceneResource` (dyson: 94 materials, 32 sections, 30 distinct matidx); only
  the submesh→section join remains. This is why large surfaces, skyboxes
  included, can wear the wrong texture.
* **Skybox animation is not applied.** The sky's motion is a shader UV
  transform (`uvtransform.scrolldir`) the importer does not reproduce.
* **Single-mesh names are not recoverable.** Of 2,521 Echo VR mesh resources,
  the shipped binaries name 5, and the engine authoring tree adds none — mesh
  names are referenced only by hash at runtime.
* **Lone Echo 1 level names are not bound.** The 171 authored scene names are
  recovered from the install's `sourcedb`, but match 0 of 1,244 archive names
  and 0 resource hashes inside them (full sweep, 0 failures). They are
  script-level identifiers reaching resources through an indirection that does
  not ship.
* Some models ship as empty stubs and produce no geometry.
* `blender_tool/tests/test_evr_materials.py` has 28 failures against drifted
  constants and predates the current modules; the rest of the suite is 1,082
  passing.
