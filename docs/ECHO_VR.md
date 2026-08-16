# Echo VR support — handoff

Everything learned while making this repository extract Echo VR levels, written
for whoever picks it up next. **Read the "Wrong turns" section before writing
code** — most of a long session went into dead ends that look plausible from the
outside, and each one is recorded here so it is not walked again.

Goal: full extraction of Echo VR models and scenes **with textures**, importing
through the existing Lone Echo Blender add-on.

---

## 1. Current state

Measured on level `576ed3f8428ebc4b`:

| | |
| --- | --- |
| unique models in scene | **127** (was 63 — §"actor coverage" fix below found `CStaticInstanceModelCR`, a third actor-component type the extractor was not reading) |
| models that decode | **119** (8 fail — see §7.3) |
| decode quality | 107 on a described path, 12 reconstructed, 8 guessed |
| actors exported | **1178 of 1459** (was 446/1459, 31%→81%) |
| static instances placed | **659** — matches the reference viewer exactly |
| instances placed (incl. actors) | **5030** across 293 meshes |
| materials written | 406, schema **v2** |
| textures written | 314 DDS on disk (was 51 at session start) |
| channels | `base_color=391 alpha=106 normal=94 roughness=50 emission=15` |
| LOD0 meshes with a real base-colour texture | **152 of 161 (94%)** (was 79/161, 49%, before §7.1/§7.1b) |

**Model enumeration is correct and verified.** `scripts/evr_compare_level.py`
diffs against the reference viewer's own cached output: *zero* models missing,
instance counts identical. Do not go looking for a model-enumeration bug; there
isn't one.

⭐ **Texture binding is SOLVED (§7.6).** The material's own sixth container is a
`CMap<slot_name, texture>` — the real binding, on disk. It supplies **376 of 406**
materials here (92.6%) and **463 of 490** on `d09afd15b1c75c04` (94.5%); DXGI
guessing fell 380 → 22, textures extracted 120 → 460 (and 128 → 760), warnings
251 → 22. Verified through the real add-on import on both levels, 0 broken nodes,
with **332 and 383 image datablocks actually loaded** (was 89). The stats row
above predates this and understates the result.

What is *not* right yet, in priority order: ~30 materials still fall through to
a DXGI guess, and `rimlighting`/`transmittance` are named but have no Principled
socket (§7.6); the draw→material assignment is still inferred (§7.5) and is now
the weakest link, since the material→texture half is exact; 12 models decode
through a lower-fidelity path (§7.2); 8 produce no geometry (§7.3).

---

## 2. Where everything lives

| what | path |
| --- | --- |
| game data (flat extract) | `H:\pcvr-extracted` |
| our output | `J:\EchoVRModels\scenes\<hash>\` |
| **reference viewer (ground truth)** | `C:\Users\lucas\Desktop\desktop\rad-archive-viewer` |
| **its cached correct output** | `rad-archive-viewer\cache\level_<hash>.json` |
| mesh decoder app.py uses | `J:\EchoVR-Tools-Launcher\evr-mesh-importer\evr_mesh_importer` |
| `level_reader` (actors only) | `C:\Users\lucas\Desktop\FreshEVR\evrFileTools` |
| cracked CSymbol64 names | `C:\Users\lucas\Desktop\desktop\turbo\hash_lookup.json` |
| resource type map | `rad-archive-viewer\type_map.json` |

⭐ **`rad-archive-viewer` renders these levels correctly.** It is the single most
useful artefact in this project. When something is wrong, read `app.py` first —
it is a working implementation of the same problem, and its `cache/level_*.json`
files are ground truth you can diff against without running anything.

---

## 3. Modules added (all in `scripts/`)

| module | what it does |
| --- | --- |
| `evr_resource_types.py` | Type-hash constants, GPU↔primary pairing, `find_mesh_and_primary` |
| `evr_material_resource.py` | `CGMaterialResourceWin10` — fully decoded |
| `evr_shaderset.py` | `CGShaderSetResourceWin10` — texture roles live here |
| `evr_texture_resource.py` | `cgtextureresourceWin10` — DXGI formats, DDS rebuild |
| `evr_texture_streaming.py` | `CGTextureStreamingResourceWin10` — per-model texture list |
| `evr_level_reader.py` | Static instance placement, ported from `app.py` |
| `evr_materials.py` | Ties it together, emits the v2 sidecar |
| `evr_compare_level.py` | **Diffs our output against the reference** |
| `evr_scene_extract.py` | The entry point (pre-existing, heavily reworked) |

Tests: `blender_tool/tests/test_evr_*.py`, ~100 assertions, archive-free.
Each module's docstring carries its own findings and caveats.

---

## 4. Verified format knowledge

### 4.1 Resource type hashes

```
347869ce492dc7da  CActorDataResourceWin10
a388ea69e5108f4c  CGSceneResourceWin10
ea51a0d76eb90142  CModelCRWin10
2464c4ed290f3268  CInstanceModelCRWin10
263584544abbd56c  CStaticInstanceModelCRWin10
77c0bf257ca92aa0  CGStaticInstanceResourceWin10
358b53c17825d154  CBVHResourceWin10
4e426f88c1b5d7ac  CGMeshListResourceWin10        <- PRIMARY
37102e4b27955a14  CGInstancedModelResourceWin10  <- PRIMARY
e642bfb1abcf76df  (GPU blob for mesh lists)
e7a8ab5ceaef49cb  (GPU blob for instanced models)
e3e0266f1911dafa  CGMaterialResourceWin10
4984e2bbb2ddb256  CGShaderSetResourceWin10
4a4c32c49300b8a0  cgtextureresourceWin10
c2434c5a99e139ce  CGTextureStreamingResourceWin10
ae49fad43254367a  RawTexturePackfileWin10
230554bc3beca38c  CGLightMapResourceWin10
73d312a620da3824  CGVisibilityResourceWin10
b7d338793fa37832  CPhysicsResourceWin10
46adff5980245670  CSkeletonResourceWin10
2a41cf1c1d9e5d32  carchiveresourceWin10   (archive manifest, NOT a binding table)
92abd3e1432bf5e8  CTransformCRWin10
```

**GPU → primary pairing** (from `evr-mesh-importer/primary.py`):

```
e642bfb1abcf76df  ->  4e426f88c1b5d7ac
e7a8ab5ceaef49cb  ->  37102e4b27955a14
```

`decode.extract_mesh` needs **both**: the GPU blob holds vertex/index bytes, the
primary holds the vertex format.

### 4.2 `CGMaterialResourceWin10` — fully decoded (300/300 files)

```
0x00  u64      materialfx (CSymbol64, -1 = none)
0x08  f32[4]   bakecolor
0x18  u16      blendmode
0x1A  u16      mattype
0x1C  u32      flags            (bit 0 = eDoubleSided)
0x20  f32      shadowfadedist
0x24  u32      pad
0x28  ...      SIX descriptors, 352 bytes
0x188          == 392, end of descriptor block
+ 32 bytes     PREAMBLE (four u64s, purpose unknown)
then           tables, EACH 8-BYTE ALIGNED
```

Descriptor order and stride: `materialprops`(56), `materialpropoffsets`(64),
`uvsets`(56), `permutations`(64), `auxillaryinputs`(56), `trailing`(56).

Descriptor fields: `pData@0, dataByteSize@8, pAllocator@16, pad@24, flags@28,
pBase@32, capacity@40, count@48`.

**Three things that took four attempts each — do not re-derive:**

1. **`dataByteSize` = `capacity × element_size`** — the ALLOCATED buffer, not
   what is stored. Bytes on disk are `count × element_size`.
2. **Element size is self-describing**: `dataByteSize / capacity`. Derive it per
   file; do not hard-code. This works for `permutations` and `trailing` whose
   element types are unreversed.
3. **`materialprops` is a `CTable<u8>`** — its count is in **BYTES**, not f32
   words. Scalars are 4 bytes at a byte offset given by `materialpropoffsets`
   (`key u64@0, byteoffset u32@8`).

Difference from Lone Echo: LE has a 56-byte header (includes
`bakeemissivecolor`), 5 descriptors, payload at 352. **Echo VR has no
`bakeemissivecolor`** — flat emissive colour genuinely does not exist in the
data, so emission comes only from an emissive map × per-layer intensity.

⛔ **`auxillaryinputs` is NOT the texture table.** Across 1727 materials it holds
exactly two inputnames — `cutting_cut_decal` and `cutting_scorch_decal` — on 337
materials, two each. It is a decal slot.

⚠⚠ **The descriptor model above is WRONG, and it hid the texture table for the
whole project.** The real payload is `SGMaterialData`: six containers at FIXED,
NON-uniform offsets (`0x28/0x60/0xA0/0xD8/0x118/0x150`), because a `CMap` header
is 64 bytes and a `CTable` header is 56 — not six uniform 56-byte descriptors
from offset 56. The on-disk array length is **`iused`**, not `count`. Under the
uniform model the SIXTH container is read at the wrong offset and length, which
is why it was written off as "shared boilerplate" — it is in fact
`CMap<CSymbol64 slot, CSymbol64 texture>`, **the per-material texture binding**.
See §7.6 and `scripts/evr_material_textures.py`, which reads 1727/1727 files.
This section's model still describes what `evr_material_resource.py` does for
SCALARS (which works), so it is kept — but never use it to reach a container.

### 4.3 `CGShaderSetResourceWin10` — where texture roles live

Read via `evr_shaderset._parse_structured`, a table-driven decode of the real
element/sub-table layout (96-byte head, `count` 1072-byte variant elements,
five `CTable<SShaderInputData>` per variant) — not a byte scan. Ported from an
independent, disassembly-validated decoder (`quest_combat_port/tools/convert/
shaderset_wall/ssbind_true.py`, EOF-exact on 3692/3692 stock shader sets); see
§7.6 for how that was found and cross-checked. Records with `type` `0x9`/`0xA`
(SSBO) or `0xFFFF` (sentinel) are not texture binds and are skipped.

`SShaderInputData`, 0x20 bytes:

```
+0x00  u64  inputname       CSymbol64 of the slot name -- THE ROLE
+0x08  u64  textureassetid  CSymbol64 of the texture
+0x10  u16  type
+0x12  u16  layer
+0x14  u16  engineresource
+0x16  u16  slot
+0x18  f32  uscale
+0x1c  f32  vscale
```

Measured: **6231 binds, 55 distinct inputnames, 95.4 % nameable** via Lone
Echo's existing `role_for_inputname` plus `hash_lookup.json`. Roles found span
`layer0..3` of `composite_diffuse`, `composite_specular`, `composite_components`,
`composite_normals`, `emissive_map`, `detail_normal_map`, `blend_mask`,
`albedo_map`, `normal_map`, `specular_map`, `rim_map`, `flowmap_map`,
`opacity_map`, `back_lighting_map`, `secondary_emissive_map`.

Two unresolved inputnames, both single-slot (7 and 8) while surface textures sit
at 21–31 — almost certainly engine-bound resources, safe to ignore:
`8f8bb15ea1f33ff6`, `bf307575be385a5a`.

⚠ **`layer0_diffuse_map` is not a routable role.**
`materials.CHANNEL_ROLE_SUFFIXES["base_color"]` is exactly
`("composite_diffuse", "albedo_map")`. The original code wrote
`"basecolor_role": "layer0_diffuse_map"`, which routes nowhere.

### 4.4 `cgtextureresourceWin10`

256-byte header; **DXGI format at 0xD8**; mip tables occupy 0–191; inline
DDS-prefixed pixels at 256. `streamingdisabled == 1` → file is exactly 256 bytes.

### 4.5 `CGTextureStreamingResourceWin10`

```
u64 packfilename | u32 count | u64[] textures
u32 layouts_count      | 192 * N   STextureStreamData
u32 objecttsdata_count | 8 * N     (u32 tex_idx, f32 texel_ratio)
u32 sectorobbs_count   | 40 * N
u32 sectortsdata_count (OPTIONAL — 28-byte files omit it)
```

⛔ **There is no bindings array in this file.** It is a texture *inventory*. The
original code read "bindings" at `12 + count*8`, which is `layouts_count`
followed by 192-byte mip tables.

### 4.6 Static instance placement (ported from `app.py`)

**Instance record — 24 bytes, and there is NO SCALE:**

```
u32  model index (u0)
u32  unk1
u32  unk2
u32  unk3
u32  packed_pos
u32  packed_rot
```

* position: 9 bits per axis, `min + (q / 511) * (max - min)` within the level's
  **BVH bounds** (`CBVHResourceWin10`: scan nodes from offset 64, stride 32, take
  global min/max).
* ⭐ **`material_index = (packed_pos >> 27) & 0x1F`** — the per-instance material
  selector, 0..31. **Parsed but NOT YET USED.** This is the highest-value
  outstanding task (§7.1).

`CStaticInstanceModelCR`: `cnt@48 u64`, `hash_count@168 u64`, hash array at
`568 + cnt*24 + 208`, then the per-instance `u16` index array (8-aligned).
Filter instances on the bounds check **alone**.

### 4.7 `decode.extract_mesh` branch order

```
if primary_data:
    primary_described  <- CIMR (CGInstancedModelResource) ONLY
    cgml               <- mesh-list models; the CORRECT path for that family
    crossref_ib / hero <- reconstructed fallbacks
heuristic              <- no branch matched; vertex stride is GUESSED
```

`cgml` is **not** a failure. Only `heuristic` is unambiguously bad.

### 4.8 Where models name their materials

* `CGInstancedModelResourceWin10` — 14 of 20 sampled models
* `CGSceneResourceWin10` — 3 of 20 (the mesh-list family)

Join direction, measured:

* material → shaderset: **0 of 400**. Does not exist. `materialfx` is never a
  shaderset.
* shaderset → material: **69 of 200**; corpus-wide 357/1727 materials (20.7 %).

---

## 5. Commands

```bat
:: extract a level
python.exe scripts\evr_scene_extract.py <hash> --hash-lookup <hash_lookup.json>

:: diagnostics
python.exe scripts\evr_scene_extract.py <hash> --probe   :: materials only
python.exe scripts\evr_scene_extract.py <hash> --geo     :: geometry only
python.exe scripts\evr_scene_extract.py <hash> --where   :: resource census

:: verify against the working viewer
python.exe scripts\evr_compare_level.py <hash>

:: format audits
python.exe scripts\evr_material_resource.py H:\pcvr-extracted --limit 300
python.exe scripts\evr_material_resource.py H:\pcvr-extracted --dump <mat_hash>
python.exe scripts\evr_shaderset.py H:\pcvr-extracted --hash-lookup <f> --crack
python.exe scripts\evr_shaderset.py H:\pcvr-extracted --link
python.exe scripts\evr_texture_resource.py H:\pcvr-extracted --sample 20

:: tests (no game data needed)
python.exe blender_tool\tests\run_tests.py
```

Import in Blender: **File > Import > Lone Echo Scatter (.lescatter)** →
`J:\EchoVRModels\scenes\<hash>\manifest.json`. The sidecar auto-discovers.

---

## 6. ⛔ Wrong turns — do not repeat these

Recorded because each looked correct and cost real time.

1. **`auxillaryinputs` is not the texture table.** It is `SShaderInputData`, the
   right struct — but only ever holds two cutting-decal names. Roles are in the
   **shader set**. Lone Echo's own `AUX_INPUT_NAMES` says so.
2. **`capacity != count`** in a descriptor, and `dataByteSize` is the *allocated*
   size. Assuming either rejected 300/300 shipped materials.
3. **The 36-byte "preamble" is 32 bytes + per-table alignment.** Solving for the
   residual gives 32 *or* 36 and 36 is wrong; it shifts every scalar by one word
   while still looking plausible (both stay 4-byte aligned).
4. **`MESH_DIRS` originally listed two non-geometry directories** —
   `37102e4b27955a14` is a *primary*, `ae49fad43254367a` is a texture packfile.
   Feeding either to the mesh decoder yields garbage or nothing.
5. **`decode.extract_mesh` needs `primary_data`.** Without it the vertex stride
   is guessed and meshes render as triangle fans radiating from a point.
6. **There are two different `decode` modules.** `app.py` uses
   `J:\EchoVR-Tools-Launcher\evr-mesh-importer\evr_mesh_importer`. Using the
   FreshEVR one instead wastes time fixing arguments to the wrong library.
7. **Static instances have no scale.** Reading one from `level_reader` and
   applying it stretches meshes into spikes.
8. **Do not narrow the shaderset bind scan to a model's own texture list.** A
   shader set binds textures that need not appear in any one model's streaming
   list; intersecting the two lost 99 of 100 materials.
9. **A geometry audit that only checks index ranges cannot see a wrong stride.**
   132 submeshes passed "clean" while rendering as fans. Check the decode
   `path_label`, not just index sanity.
10. **Cache decode failures.** Without it a broken model is re-decoded once per
    instance and the path histogram reads 131 for 63 models.
11. **`layer0_diffuse_map` routes nowhere** — see §4.3.
12. **Read `app.py` first.** Several of the above were answered in one read of a
    working implementation that was available the whole time.
13. **The per-instance `material_index` is not consumed anywhere in the
    reference renderer, and `app.py`'s own `parse_materials_mapping` "bindings"
    array is a coincidence, not a verified structure.** Both looked like the
    missing piece for §7.1 and neither is — see §7.1 for the full case (engine.js
    never reads `inst.material_index`; the one place a "material index" drives
    texturing, `sub.material_index` in `api_mesh`, is dead code that is always
    0 because `decode.extract_mesh` never returns a 6th tuple element; and
    `parse_materials_mapping` reads 8-byte bindings out of what the
    byte-identical-verified `cgtexture_streaming_resource.py` says are 192-byte
    `STextureStreamData` mip tables). Don't re-open either lead without new
    evidence.

---

## 7. Open problems, in priority order

### 7.0 Every LOD level of every part was rendering simultaneously, stacked — FIXED

`decode.extract_mesh`'s own comment says it outright: *"LOD trimming is
handled later by the importer."* Every LOD level of a model part comes back
as its own submesh, indistinguishable from a genuinely separate part — and
nothing in `evr_scene_extract.py` ever did that trimming. Every placement of
a multi-LOD model rendered EVERY level of EVERY part on top of each other at
once. This, not (only) the texture-role guessing, is why geometry looked
broken/duplicated/low-detail: an instance that looked "wrong" might actually
be its own model's LOD3 poking through LOD0.

Confirmed on `ff5afb4e96897159` (the model behind level 576ed3f8428ebc4b's
instance i277, which is how this was found — its UVs "looked low", and they
were: i277 is that model's WORST LOD): `decode.extract_mesh` returns 9
submeshes for this one model. Their bounding boxes cluster into exactly 3
physical parts:

```
submesh  faces  bbox size (game units)      part
   0     1968   0.574 x 1.282 x 0.551       main body, LOD0 (finest)
   3      614   0.578 x 1.286 x 0.550       main body, LOD1
   5      328   0.586 x 1.290 x 0.554       main body, LOD2
   7      184   0.589 x 1.290 x 0.553       main body, LOD3 (coarsest) <- i277
   2      176   0.114 x 0.029 x 0.026       small part, LOD0
   4       48   0.114 x 0.029 x 0.025       small part, LOD1
   6       14   0.111 x 0.028 x 0.021       small part, LOD2
   8       14   0.111 x 0.028 x 0.021       small part, LOD3
   1        2   0.538 x 0.242 x 0.244       singleton (impostor/collision card?)
```

Bounding boxes agree to within ~2.6% across LOD levels of the same part and
are nowhere near each other across parts — clustering on bbox match (5%
relative + 0.01 absolute tolerance) separates them cleanly.

**Fix**: `_group_submeshes_by_lod` in `evr_scene_extract.py` clusters a
model's decoded submeshes by bounding box, ranks each cluster by face count
(most faces = level 0), and a singleton part (no LOD siblings) reports
`cluster_size == 1`. The main extraction loop turns that into
`SceneInstance.lod_group` / `lod_level` / `lod_group_levels` — fields the
package format and the add-on ALREADY had, built for Lone Echo's own static-LOD
system (`le_mesh/static_lod.py`) and already wired into
`scatter_import.py`'s default import (`lod_level=0`, i.e. finest-only). Echo VR
just never populated them, so every instance defaulted to `lod_group=-1`
(ungrouped == always shown == every level stacked). No add-on change needed.

⚠ This is a bounding-box heuristic, not a read of a real LOD table — Echo VR's
`CGMeshListResourceWin10`/`CGInstancedModelResourceWin10` carry no such table
(unlike Lone Echo's static-scatter master, see `le_mesh/static_lod.py`'s own
docstring for why that system had to be reverse-engineered from a different,
fully-populated source). It can misgroup two genuinely distinct parts that
happen to occupy near-identical bounding boxes, or fail to group a real LOD
pair whose silhouette drifts more than the tolerance. Re-run `--geo` after any
tolerance change and spot-check a model with `--probe`-adjacent tooling before
trusting a big tolerance change blindly.

### 7.1 Texture roles come from DXGI guessing ⭐ highest value — PARTIALLY FIXED

`role routes dxgi(1)=29, dxgi(2)=15, dxgi(3)=55` — 99 of 100 materials assign
textures by pixel format (BC5 → normal, `_SRGB` → base colour, else roughness).
Three layers of fix now sit on top of that fallback, weakest to strongest:

**Layer 1 — sibling materials stop colliding.** `roles_from_texture_list` takes
a `rank` (a material's position among its model's own DISTINCT materials,
0/1/2/…) so two materials on one model no longer grab the identical first
match of each DXGI class.

**Layer 2 — the pool is CLUSTERED before classifying, not flat.**
`_texture_families` groups a model's texture list into self-consistent SETS by
shared hash prefix OR suffix (≥8 hex chars either direction) before DXGI
classification runs, and `rank` now selects a FAMILY rather than a slot in one
flat pool. Why this had to happen: `ff5afb4e96897159` (576ed3f8428ebc4b
instance i277's model) bundles AT LEAST SEVEN unrelated texture sets in one
34-texture streaming list, and the old flat-pool fallback mixed channels
across them — base colour from one set, normal from a totally different one.
Two confirmed examples (below) show both clustering patterns: a 10-hex shared
PREFIX (`c29a7d30d8…`) and an 8-hex shared SUFFIX (`…dab1e343`, differing only
in one prefix nibble — almost certainly a layer/variant index). Still a
guess as to which FAMILY is right, but every channel it picks now points at
the same physical surface.

**Layer 2b — ranking prefers families that HAVE a base colour.** Clustering
alone introduced a new failure mode: `rank % len(families)` can land on a
small, single-purpose family (one standalone normal map, one shared utility
roughness texture) with no colour member at all, so the material renders as a
flat factor-only grey — measured on `576ed3f8428ebc4b`: 82 of 161 LOD0 meshes
had no base-colour file under family-clustering-without-this-bias.
`roles_from_texture_list` now ranks only over families that have an SRGB
member when at least one such family exists; `COLOR_CAPABLE_UNORM_FORMATS` in
`evr_texture_resource.py` widens "has a colour member" to the plain (non-
`_SRGB`-flagged) counterpart of each SRGB format (BC1/BC2/BC3/BC7 UNORM) as a
lower-priority fallback, for models whose entire texture list lacks an
explicit `_SRGB` asset. A family missing normal/roughness still reads as a
real material; a family missing base colour reads as broken, so this bias is
one-directional on purpose.

**Layer 3 — `CONFIRMED_MATERIAL_ROLES`: hand-verified answers, propagated
across LOD.** For `ff5afb4e96897159`'s main-body material, exhaustive search
(§ wrong-turns below) found no computable source for the real binding
anywhere in the extract — so a human rendered the candidates and confirmed
`c29a7d30d8154550` (base) / `c29a7d30d818444e` (normal) / `c29a7d30d81e4e56`
(roughness) against the real game. That answer is pinned in
`evr_materials.CONFIRMED_MATERIAL_ROLES`, checked FIRST in `build_spec` (ahead
of shader-set and DXGI routes), and **propagated automatically to every LOD
sibling of the same physical part** via
`propagate_confirmed_roles_to_lod_siblings` — the materials-phase loop in
`evr_scene_extract.py` now decodes each model once (`_decode_model_cached`,
shared with the geometry phase so this is not a second decode) purely to run
`_group_submeshes_by_lod` early enough to know which materials are LOD
siblings of a confirmed one. On `576ed3f8428ebc4b`: `role routes … confirmed=6
… lod_sibling=6` — 2 materials directly confirmed, propagated to their LOD1/
LOD2 siblings automatically, deduplicated onto one matidx each (`intern`'s
cache key drops shader-set/rank for a confirmed material, since its content
doesn't depend on either).

Tests: `test_roles_from_texture_list_gives_sibling_materials_different_textures`,
`..._wraps_rank_when_candidates_run_out`, `test_texture_families_clusters_by_shared_prefix_or_suffix`,
`test_intern_keys_on_rank_...` in `test_evr_materials.py`.

**Growing this further is a spot-confirm workflow, not a research problem
anymore:** render a model's candidate texture families (see the rendering
scripts pattern used to confirm `ff5afb4e96897159`: decode one submesh's raw
geometry, build one Blender material per candidate family via
`material_builder.build_material`, render, compare against the real game),
add the answer to `CONFIRMED_MATERIAL_ROLES`, re-extract. LOD propagation means
one confirmation covers a whole physical part, not just the one draw checked.

**Still open, and still real, for everything NOT in `CONFIRMED_MATERIAL_ROLES`:**
family selection is round-robin, not a link — it has no idea which family is
right, only that whichever it picks is internally consistent. The underlying
cause (only 20.7% of materials name a shader set directly, and the `CGMeshData`
material-offset probe is at 0% confidence on this level) is untouched, and per
the wrong-turns below, is not fixable from data in this extract at all.

⛔ **`material_index = (packed_pos >> 27) & 0x1F` is NOT the fix for this, and
do not spend more time trying to wire it in as one.** It looked promising —
parsed into `StaticInstance.material_index`, range 0–31, carried into
`instances_to_process` and then never consumed — but two independent checks
of the reference viewer say it is not what selects a texture:

1. **`rad-archive-viewer`'s own renderer never reads it.** `engine.js`'s
   `loadInstancedMesh`/`loadMesh` build `meshesToLoad` from `inst.model_hash`
   and `inst.position` only; `inst.material_index` is read nowhere in the
   render path. The only place `material_index` is read at all is the level
   EDITOR's static-instance patcher (`app.py` ~line 2812), which round-trips
   it unchanged while repacking a moved instance's position — it never uses
   the value to pick anything.
2. **The one place a "material_index" *does* drive texturing is a different,
   per-SUBMESH field** (`api_mesh`'s `sub.material_index`, `app.py` ~line
   2213), sourced from `decode.extract_mesh`'s result tuple. Checked against
   `evr-mesh-importer/decode.py`: every returned submesh tuple is
   `(verts, faces[, uvs[, bone_data]])`, 2–4 elements — index 5 never exists,
   so `sub.material_index` is `0` for every submesh, always. It is dead code
   in the reference viewer too, not a working example to copy.

Where the reference viewer's material GROUPING actually comes from
(`app.py`'s `parse_materials_mapping`, used by `/api/model_textures`) is its
own separate dead end, recorded here so it is not mistaken for ground truth:
it reads `CGTextureStreamingResourceWin10`'s `layouts_count` section as
`slot_count` + 8-byte `(texture_idx, scale)` bindings, chunked by 4 into
materials. But `rad-archive-viewer/echomod/resources/cgtexture_streaming_resource.py`
— the byte-identical-round-trip-verified parser `evr_texture_streaming.py`
here matches — says that exact same section is `layouts_count` × **192-byte**
`STextureStreamData` records. `parse_materials_mapping` is reading the leading
8 bytes of each 192-byte mip-offset table as if it were a small binding
record; its own `score_order()` (which tries both `int_float` and
`float_int` byte order and keeps whichever looks more plausible) is a tell
that this was always a heuristic, not a verified structure. This is the same
`slot_idx // 4` grouping `evr_scene_extract.build_materials_for_model`
already tried and rejected — independently arriving at the same dead end from
the other side confirms it, it does not undo the rejection.

### 7.1b Texture PIXEL DATA was silently missing even when the ROLE was right — FIXED

Separate from §7.1 (which role a texture is assigned to): once role assignment
was fixed, a large fraction of `layer0_albedo_map`-correct materials STILL
rendered with no base colour, because the texture's actual pixel bytes could
not be reconstructed at all. Measured on `576ed3f8428ebc4b`'s LOD0 meshes: 66
of 75 remaining "no base colour" cases had the CORRECT role assigned
(`role_textures` said `layer0_albedo_map`) but `rebuild_dds` returned nothing.

**Root cause:** `cgtextureresourceWin10`'s own header carries `streamingdisabled`
— when `== 1` the file is EXACTLY 256 bytes (`cgtexture_streaming_resource.py`'s
own docstring: verified on the full 12261-file reference corpus) with no inline
pixels AND an all-`0xFFFFFFFF` sentinel `packfilelayout` (checked directly on
three failing textures, including a real 1024×512 asset — not just tiny
placeholders). Both of `evr_texture_resource.py`'s existing reconstruction
strategies (`_rebuild_from_layout`, `_rebuild_legacy`) require SOME inline DDS
to extend, which these textures structurally never have. There was no bug in
either strategy to fix — the data they need genuinely is not there.

**Fix:** a THIRD resource type, `beac1969cb7b8861` (`TEXTURE_DDS_SIDECAR` in
`evr_resource_types.py`), holds a COMPLETE, ready-to-use DDS file — literal
`"DDS "` magic, header dimensions/mipcount matching `cgtextureresourceWin10`
exactly — under the SAME hash as every texture. Confirmed from three
independent sources, none of which are in `type_map.json`: the Go extractor's
`pkg/naming/type_mapper.go` names it `TypeDDSTexture`; several
rad-archive-viewer mod-tool scripts call it "cgtexture pixel payloads" / "the
raw DDS" / "the DDS pixel sidecar"; and it is present for 12287 of 12293
textures in one flat extract — this is THE pixel source, not a fallback for
the broken-streaming case specifically. `_rebuild_from_sidecar` in
`evr_texture_resource.py` just reads it directly (no reconstruction) and
`rebuild_dds`'s `"auto"` order now tries it FIRST, ahead of `layout`/`legacy`/
`inline`.

Measured impact on `576ed3f8428ebc4b`: DDS files written 294→314, warnings
417→251 (166 fewer texture-extraction failures), LOD0 meshes with a real
base-colour file 79→152 of 161 (49%→94%). The remaining 9 have no
colour-capable texture anywhere in their model's own list at all (checked, not
assumed) — a §7.1-style "no candidate exists" case, not a §7.1b extraction
failure.

⛔ **Do not re-derive the legacy hash-scan's high-res chain from
`0x40..0x100`.** That range is `reversedcmpmipsizes`/`reversedmipsizes`/the
typed header fields under the verified layout, not a hash list — the existing
`⛔ high-resolution mip chain` warning at the top of `evr_texture_resource.py`
already covers this; `TEXTURE_DDS_SIDECAR` is the real fix, not a patch to that
strategy.

### 7.1c A redundant sidecar copy made EVERY texture fail to load in the real add-on — FIXED

Everything in §7.1/§7.1b was verified correct by *reading `materials.json`
directly* or by handing a spec straight to `material_builder.build_material`
with the right directory — never by running the actual
`File > Import > Lone Echo Scatter` operator end to end. The first time that
happened (after the stale-add-on fix), **zero of 179 materials got a single
image texture node**, despite `materials.json` being completely correct.

**Root cause:** `evr_scene_extract.py` wrote the resolved-material sidecar
TWICE — once at `<scene_dir>/materials.json` (paths relative to `<scene_dir>`,
e.g. `"textures/xxx.dds"`) and once more at `<scene_dir_parent>/<hash>_materials.json`,
with the IDENTICAL relative paths. `scatter_import.import_lescatter`'s
auto-discovery checks the OUTER location first (that's Lone Echo's own
`le_scene_materials.py` convention — it writes sidecars next to the package
directory with paths relative to THAT outer location, a different and
incompatible convention from Echo VR's). Finding the outer file, it computed
`tex_base` one directory level too high, so every single `channels[*]["file"]`
lookup silently missed and `material_builder` built flat placeholder materials
for all 179 — no error, just nothing.

Confirmed directly: `bpy.data.images` count after a full headless
`import_lescatter` went 0 → 89, and materials with a `TEX_IMAGE` node went
0/179 → 179/179, purely from deleting the outer copy — no other change.

**Fix:** stop writing the outer copy. The inner `<scene_dir>/materials.json`
is already `import_lescatter`'s SECOND auto-discovery candidate
(`pkg.dir / "materials.json"`) and is self-sufficient on its own.

⚠ **This is the kind of bug that direct-script verification cannot catch.**
Every confirmation earlier in this document (`CONFIRMED_MATERIAL_ROLES`, the
UV-orientation check, the base-colour-family fix) was validated by calling
`material_builder.build_material(spec, correct_dir, {})` directly or by
reading `materials.json`'s fields — both bypass `import_lescatter`'s
auto-discovery path entirely. **Whenever the question is "does this actually
work in Blender," run the real `import_scene.lescatter` operator (or
`import_lescatter` headlessly) end to end, not a hand-built reproduction of
one piece of it.**

### 7.2 12 models decode via `hero` rather than a described path

Lower-fidelity reconstruction. Worth checking whether the viewer renders them
better; if so, find why `primary_described`/`cgml` declined them.

### 7.3 8 models produce no geometry

`03e2d75d43a4e793 0c2c087329dd6e6b 3b2559d29b3abe9f 5edc941928ce9985
7bea9a3a4da7f2cb cbacc9c45697b39f f3339adbbf2f9667 fa14b2541e93e8b9`

⚠ **These may not be a bug.** All are mesh-list-family models, and the three
that appeared in the `--where` census had **no material and no shaderset
references either**. Models with no materials, no shadersets and no decodable
geometry are most likely collision hulls, occluders or visibility proxies — every
one ships beside a `CPhysicsResourceWin10` and a `CGVisibilityResourceWin10`.

**Check this before investing:** open one in the viewer. If `app.py` renders
nothing for it either, 55 of 63 is complete and there is nothing to fix.

### 7.4 `unpack_rotation` is unverified

The one function that could **not** be ported — `app.py` passes `packed_rot` to
its JavaScript frontend without expanding it in Python. The current
implementation assumes a 10/10/10 layout with the largest component
reconstructed. If positions look right but orientations do not, this is the
suspect. Reference value to test against: instance 0 of `576ed3f8428ebc4b` has
`packed_rot = 1355997671`. Check what the viewer's JS does with it.

### 7.5 Draw → material — FIXED. Every model carries its own palette ⭐

`scripts/evr_model_materials.py`. **119/119 and 156/156** models in the two test
levels now resolve a real, ordered draw-section list; the scan-order fallback is
no longer reached on either level.

**What was wrong.** `materials_for_model` had no real source, so it fell back to
`scan_model_references` — every material hash appearing anywhere in the model's
bytes, in file order. Wrong membership (hashes appear in padding and neighbouring
tables) AND meaningless order. Paired with submesh index, it advanced to a new
material on every submesh, while the truth is **run-length structured**:

    model 34918b2b464d6b58   TRUE sections → matidx:  0, 1, 1, 1, 2, 2, 2, 3
                             OLD assignment:          0, 1, 2, 3, …

so it fell out of phase on the first repeated entry and never recovered. Which
submeshes survived that depended on which LOD level was imported — which is
exactly why importing LOD 0 and LOD 4 produced **complementary** sets of
correctly-textured models (user-observed, and the clue that cracked it).

**The real source.** Two carriers, same two tables:

* mesh-list models: a **same-named `CGSceneResourceWin10`** — 628 mesh lists,
  628 scene resources, a perfect **628/628** pairing (exactly Lone Echo's
  companion lookup), all decoding cleanly.
* instanced models: **no** scene resource (0/1893) — the tables are inline.

Both hold `materials` (`CTable<CSymbol64>`, the palette) then `shadersets`
(`CTable<SGMeshShaderSet>`, stride 24) = `{u64 shaderset, u64 material, u32 x,
u32 matidx}`, one record per DRAW SECTION, `matidx` non-decreasing into the
palette.

Inline tables are located by a constraint that cannot be satisfied by accident:
read `n = max(matidx)+1` CSymbol64s immediately before a candidate record run and
require `palette[matidx] == record.material` for **every** record. An earlier
attempt that only checked "is this a plausible hash" returned garbage
(`max(x)+1 == 43`); the palette cross-check is what makes it trustworthy.

**It also corrected the one hand-confirmation.** `ff5afb4e96897159`'s palette
holds `1e070bb9873c1e45` at index 5 — the material whose slot table binds the
texture a human confirmed against the running game. The old route had pinned that
answer onto `a96dba2cbec4a581`/`2993ccd5a8e33846` instead (all-defaults, and a
different texture, respectively). `CONFIRMED_MATERIAL_ROLES` is therefore now
**empty**: the observation was right, the attribution was wrong, and keeping it
would override structured data with a guess. The mechanism is kept for a future
genuine confirmation.

Measured (`576ed3f8428ebc4b`): materials 406 → 215, textures 460 → 376, route
`material_table=200`, `confirmed`/`lod_sibling` gone. The drop is the point —
those were materials the models never drew. Real add-on import: **169/173
materials textured, 334 images, 0 broken** (4 untextured are all-default
palettes, i.e. the engine draws them untextured too).

⛔ **The pipeline uses the PALETTE (distinct materials, engine order), not the
per-section list — and this is deliberate.** Wiring the section list in and
pairing submesh *i* with section *i* was tried and made LOD 0 visibly WORSE
(user-verified). The two are different axes: `decode.extract_mesh` returns LOD
VARIANTS, while sections enumerate LOD DRAWS and therefore repeat
(`m0,m1,m1,m1,m2,m2,m2,m3`). At LOD 0 there is roughly one submesh per PART, so a
list of distinct materials is the closer match. The palette keeps the old
indexing semantics while fixing what was genuinely broken about the scan:
membership and order.

⚠ **This is still the open problem, and it is now precisely quantified.** Indexing
a palette by submesh index can only be correct when the two counts agree, and on
`d09afd15b1c75c04` they usually do not:

| | models |
|---|---|
| submeshes **==** palette size | **67** |
| submeshes **>** palette size (index clamps/repeats) | 42 |
| submeshes **<** palette size (palette entries never reachable) | 47 |

That last row is the damaging one and is exactly the `ff5afb4e96897159` shape: 9
submeshes decoded, 8 palette entries, and the material the human confirmed was
**index 5** — unreachable by any submesh-ordered walk that stops early or drifts.
89 of 156 models cannot be right under index pairing, which matches the
user-reported "wrong texture entirely" cases that survive.

**`x` is NOT the LOD level — tested against real geometry and REFUTED.** The
hypothesis fit `34918b2b464d6b58` well (`x=0,0,1,2,3,4,5,6` against exactly 2
parts present at level 0 and one part continuing alone through 1..4) so it was
implemented: group sections by `x`, and let a submesh at level L, being the p-th
part at that level, take the p-th section with `x == L`. It is refuted by
`ff5afb4e96897159`, whose `x` runs **0..7 sequentially** over 8 sections while the
model decodes 3 parts across ~4 levels — so `by_level[0]` holds a single section
and every part at level 0 collapses onto one material. Measured on
`d09afd15b1c75c04`: materials 178 → **111**, loaded images 336 → **142**. Reverted.

⇒ `x` means different things in the two carriers, or is not a level at all. Any
future attempt must explain BOTH shapes before being wired in.

**The fix still requires the submesh → section mapping**, i.e. understanding the record's
`x` ordinal. Evidence gathered so far, deliberately NOT acted on:
`ff5afb4e96897159` has `x = 0,1,2,3,4,5,6,7` (sequential, one section per
material); `34918b2b464d6b58` has `x = 0,0,1,2,3,4,5,6` with
`matidx = 0,1,1,1,2,2,2,3` (a repeat at `x=0`); `3eff95282bf0807f` has
`x = 0,1,2,3,4` with `matidx = 0,0,0,1,2`. A LOD-level reading fits the second
model well (2 draws at LOD0 = its 2 parts) and fails the third (1 record at `x=0`
but 2 parts at LOD 0). Resolve that contradiction against real geometry BEFORE
wiring anything — two assignment changes have now been reverted for being shipped
ahead of their evidence.

⚠ Previously (now superseded): submesh *i* is paired with section *i*. Section count and
submesh count are not always equal (a model may ship 8 sections and decode 6
submeshes), so the mapping from decoded submeshes onto sections — particularly
across LOD levels, where `x` is the section/LOD ordinal — is the remaining
inexactness. Materials now always come from the model's own palette, so an error
here is a wrong choice among that model's real materials, never another model's.

### 7.7 UVs are CORRECT — verified against an independent rip. CLOSED

**`u[-47.42, 3.03]` on `13a91654991729e4` submesh 0 is real, intended data.**
Three independent lines confirm it:

1. **The engine's own material source.** `C:\Users\lucas\Desktop\core` is the
   RAD Engine authoring tree (`.radmat` material sources, 176 HLSL shaders,
   `.radtex`, `.radscn`). `material_base_mobile_vs.hlsl::SelectUVSet` selects a
   UV set by index into `uv0..uv3`, and materials name it Maya-style
   (`:layeruvset := "map1"`). Our material's `uvsets` entry is
   `c8c33e4837720ee1` — and `CSymbol64("map1") == c8c33e4837720ee1` **exactly**.
   So the material samples UV set 0: the float2 at +8, the channel already read.
2. **An independent recreation of the same level.** `Demo-Viewer2/.../Arena V4`
   (mpl_arena_a, near 1:1) has a `full` mesh whose UVs run
   **`u[-47.42, 56.32]`** — the SAME `-47.42` minimum our extraction produces.
   Two unrelated rips agreeing on an unusual value is not coincidence.
3. **The render.** `scripts/evr_render.py` shows the arena with its goal ring,
   team walls and tiled panels intact.

DO NOT "fix" this by swapping UV0 for the UNORM16 TEXCOORD at +16. Tried and
reverted: that element is the LIGHTMAP/atlas channel, which ALWAYS spans [0,1]
because every face is packed into the atlas individually. A range test therefore
*guarantees* selecting it, which is exactly the trap — it made 30 submeshes
render as per-face shards. "Lands in [0,1]" is evidence of atlas packing, not of
being the base map.

### 7.8 The render loop — `scripts/evr_render.py`

Every wrong call in this project's history was shipped because the only signal
was COUNTS, which repeatedly went UP while the scene got worse. This renders the
REAL `import_lescatter` headlessly to a PNG that can be looked at.

    blender -b -noaudio --python scripts/evr_render.py -- \
        --manifest .../manifest.json --out shot.png --focus _i1045

Full imports OOM (`Malloc returns null ... total 1098623060`). Three levers:
`--max-instances` caps placements, `--focus` keeps one object then purges
orphans (so only its textures stay resident), `--max-texture` downscales images
in place. `--inside` puts the camera in the scene. Framing uses the MEDIAN
object centre and a 90th-percentile radius — plain min/max gave radius 1181 from
one stray instance and rendered an empty frame.

FBX import is broken in this Blender build (`CyclesLightSettings.cast_shadow`);
open the `.blend` directly instead.

### 7.5d SOLVED — `CGRenderParams.matidx` is the per-draw material ⭐

**There was never an order to recover. There is a table to read.**

`CGRenderParams` (112 bytes, one per draw) holds the material index at **+32**,
resolved through the model's own palette:

    u32 @ +0x20   matidx        index into the model's material palette
    u32 @ +0x40   vertexcount   ties the record to a decoded submesh

On `ff5afb4e96897159` that column reads **`[5, 0, 1, 2, 6, 3, 7, 4, 7]`** — nine
draws, an eight-entry palette, and **not monotonic**. That single fact explains
why every previous attempt failed: submesh index, palette index, section index,
part-major ordering, `x`-as-LOD-level and one-material-per-part were all trying
to *derive* a permutation the file simply states.

**Verification.** Draw 0 — the 1877-vertex main body — resolves to
`1e070bb9873c1e45`, base colour `c29a7d30d8154550`: the exact texture confirmed
against the running game at the very start of this work. Corpus-wide, 550 of 567
mesh-list models have every index inside their palette; the 17 exceptions are
single-material models reading 1, which clamps harmlessly.

**Two carriers, two ways to reach the records:**
* mesh-list models — `CGMeshListResourceWin10.renderparams`, read directly.
* instanced models — the primary uses 56-byte `CTable` headers (counts match the
  decoded submesh count exactly) but the arrays do NOT sit where a header walk
  predicts. The array is located by its `vertexcount` column matching the
  decoded submeshes' vertex counts in order — a constraint that cannot
  coincide. (`ff5afb`: headers imply `0x788`, the real base is `0x8D8`.)

**Records are then reordered into SUBMESH order by `vertexcount`.** File order
does not match decode order; `87ac33e558c5c2e3` is the case that proved it.

Measured: `d09afd15b1c75c04` 494→**226** materials, 764→**496** textures;
`576ed3f8428ebc4b` 410→**193**, 464→**355**; DXGI guessing down to **7** and
**8** respectively. The drops are the point — those were materials the draws
never referenced, inherited from `scan_model_references`' false positives.

### 7.5c PREVIOUSLY: the root cause — our GEOMETRY is not Echo's draws ⭐ START HERE

Four separate attempts to fix material assignment were made and all four
reverted (see the `⛔` block in `evr_materials.materials_for_model`). They all
failed for the same reason, and it is not in the material layer at all:

**Echo's exact draw order IS known.** The `SGMeshShaderSet` records are the draw
list -- one record per section, in order, each naming its material outright
(§7.5). That data is read correctly and is authoritative.

**Our geometry does not correspond to it.** `decode.extract_mesh`
(`evr-mesh-importer`) reconstructs "submeshes" by PATTERN-SCANNING the GPU blob
for vertex runs. What it returns are LOD variants in scan order -- not the
game's draw sections. So there is Echo's exact draw list on one side, a pile of
heuristically-recovered geometry on the other, and no reliable correspondence
between them. Every mapping tried (submesh index, palette index, section index,
`x`-as-LOD) was an invented correspondence, which is why each one moved textures
onto DIFFERENT wrong meshes rather than onto right ones.

Ground truth showing the shape (user-verified in-game, `d09afd15b1c75c04`):

| instance | model | symptom |
|---|---|---|
| `i1149` | `cb9963f9fd257b37` | CORRECT |
| `i66` | `ff5afb4e96897159` | wrong texture ORDER — 9 decoded submeshes vs 8 real sections, with a 4-vertex outlier at decode index 1 shifting everything after it |
| `i1404` | `d030384d10c69074` | wrong texture ORDER |
| `i330` | `596131001f4d625d` | NO textures — all 4 submeshes assigned the same material (`a2630eab179ee4a2`), which resolves to zero channels |

#### The fix: decode geometry FROM the draw records

`CGMeshListResource` ships `CGRenderParams` -- 112-byte per-draw records, one per
section, never read by this repo. Field layout, **confirmed** by cross-checking
against the resource's own explicit `indexbuffers` table (`10f32317dc7f0ac` and
`1226bad704968da8`, every record agreeing):

    u32[18]  vertex count
    u32[20]  index count      == indexbuffers[mesh].numindices, exactly
    u32[10]  mesh index       (into meshes[] / indexbuffers[])
    u32[21]  0x2008 / 0x2048  format flags
    u32[0]   variant / pass index

e.g. `10f32317dc7f0ac` rp[3]: vertex=849, index=2802 == `ib[3].numindices=2802`.

Split geometry by these ranges instead of by heuristic vertex-run scanning and
**submesh i IS section i by construction** -- the material assignment stops being
a mapping problem and becomes a direct read.

⚠ Two cautions before starting:
* `1226bad704968da8` has **47 renderparams for 20 meshes** -- several records per
  mesh sharing one index count (2754) but differing in `u32[0]` (19/21/25). So a
  mesh is drawn in multiple passes/variants, and "section" is not simply "mesh".
  Resolve what `u32[0]` selects before assuming a 1:1 submesh↔section identity.
* This replaces the decode path that currently yields working geometry for
  148/165 models. It must go behind a flag and be diffed model-by-model, with a
  WORKING RENDER LOOP -- counts (materials/images) proved actively misleading
  four times in a row and must not be the acceptance signal.

### 7.5b Previously: draw → material order is inferred

Since §7.6 made material→texture exact, this is where the remaining error lives:
a wrong material now means a wrong texture, visibly. User-reported accuracy after
§7.6: **~90-95%**, with two symptoms — some models untextured, some wearing
another model's texture.

`draws[i]` is **not** a real per-draw binding. The `CGMeshData` material probe
never resolves (coverage 0.0), so `materials_for_model` falls back to
`scan_model_references` — every material hash appearing anywhere in the model
file, in scan order — and that order is then paired with submesh order. `i` also
counts EVERY submesh including each LOD copy, so one part's LOD1 can take the
material meant for a different part. This is also why importing LOD 0 and LOD 4
of the same scene yields different (and differently-wrong) textures.

⛔ **Do not "fix" this by collapsing each LOD group to one material.** Tried and
reverted. The invariant is real — LODs of one part must share a material — but it
can only be applied through the bbox CLUSTERING, which is itself a heuristic, so
it forces a single material onto parts the heuristic merged wrongly. Measured on
`d09afd15b1c75c04`: materials 216 → 146, loaded images 383 → 221. It destroys
correct assignments faster than it fixes wrong ones. The draw→material link has
to be found for real first.

#### The per-model material PALETTE — found and verified (selector still open)

Each model carries its own material palette. Two carriers, same shape:

* **Mesh-list models (628):** a **same-named `CGSceneResource`** — the hash of the
  mesh list is the hash of the scene resource, a perfect **628/628** pairing, and
  exactly how Lone Echo does it (`le_scene_binding`'s companion lookup). All
  **628/628 decode** with `quest_combat_port`'s validated decoder, and their
  `materials` `CTable<CSymbol64>` entries resolve on disk **100%**.
* **Instanced models (`CGInstancedModelResourceWin10`, 1893):** have NO scene
  resource (0 overlap) — they embed the same two tables inline.

Both then carry `SGMeshShaderSet` records, stride 24:

    u64 shaderset   u64 material   u32 x   u32 materialidx

`materialidx` indexes the palette and runs sequentially; `material` names the
material outright, so the palette is not even needed to read a record.

**Verified by hand on `ff5afb4e96897159`** (the model behind the one
human-confirmed texture) — palette at `0x1920`, 8 entries, 8 records:

    [0] a96dba2cbec4a581   x=0     [4] d586dde39b113a8f  x=2
    [1] bf5f45506b8db032   x=1     [5] 1e070bb9873c1e45  x=3   ⟵ the confirmed one
    [2] d586dde39b113a8d   x=2     [6] 2993ccd5a8e33845  x=4
    [3] d586dde39b113a8e   x=2     [7] 2993ccd5a8e33846  x=4

⭐ This **proves the model owns the correct material** (`1e070bb9873c1e45`, the
one whose slot table binds all three hand-confirmed textures) and that the old
pipeline was picking the WRONG ENTRY FROM THE RIGHT PALETTE — it had been forcing
`a96dba2cbec4a581`/`2993ccd5a8e33846`, whose own tables are all-defaults.

`x` groups the records (0,1,2,2,2,3,4,4) and **looks like the LOD level**, which
would explain directly why importing LOD 0 vs LOD 4 yields different textures for
the same object: different LODs reference different materials. ⚠ UNVERIFIED — a
quick generic locator for these records returns garbage (`max(x)+1 == 43` on most
models), i.e. it latches onto false starts, so the hand-read layout has NOT been
generalised. Do not wire `x` in as a LOD or section selector until a real
structural parse of the CIMR primary locates these two tables by their count
prefixes rather than by scanning for plausible hashes.

**So the remaining work is narrow and well-posed:** parse the palette + record
tables properly (structurally, both carriers), then establish what selects a
record per submesh — `x`, or the `CGRenderParams` field below.

Where to look next (unexplored): `CGMeshListResource`'s **`CGRenderParams`**
table — 112-byte per-draw/section records sitting right after the 152-byte mesh
records, which this repo has never read (the old probe only ever scanned the mesh
records). `quest_combat_port/tools/resource_io/cgmeshlistresource.py` decodes the
whole container and parses **567/567 + 61 empty** Echo VR files with zero
failures. `CGRenderParams` holds no material/shaderset/texture CSymbol64 at any
offset (probed, 5697 records, zero hits) — so if the link is there it is an
INDEX, which fits: static instances already carry a 5-bit
`material_index = (packed_pos >> 27) & 0x1F`. A promising unidentified field is
u32[0] of each record (values like 1,1,2,2,1 across a 5-section model).

⚠ `lodchildindices` is empty on all 567 models, so it is NOT the LOD source;
Echo VR keeps LODs as separate model resources, which is why the bbox heuristic
exists at all.

### 7.5b Original note

The `CGMeshData` material-offset probe never resolved (`coverage 0.0%`), so
per-draw material assignment falls back to file order. Correct for
single-material models, a guess for multi-draw ones. Fixing §7.1 likely makes
this moot for static instances.

### 7.6 The real texture binding — FOUND. `SGMaterialData`'s sixth container ⭐

**The material tells you its textures directly. It always did.**
`CGMaterialResourceWin10`'s last container is

    CMap<CSymbol64 slot_name, CSymbol64 texture>     e.g. layer0_basecolor_map -> <tex>

per material, on disk. No shader-set hop, no DXGI inference, no spot-confirming.
`scripts/evr_material_textures.py` reads it; it is now ROUTE 0 in `build_spec`,
above every previous route, because all of those existed only to reconstruct
this fact.

**Why it was missed.** `evr_material_resource.py` models the payload as six
UNIFORM 56-byte descriptors from offset 56. The real `SGMaterialData` is a fixed
struct image with containers at fixed, NON-uniform offsets — a `CMap` header is
64 bytes (56 + an 8-byte slot), a `CTable` header is 56:

| offset | container | stride | what |
|---|---|---|---|
| `+0x28` | `CTable<unsigned char>` | 1 | |
| `+0x60` | `CMap<CSymbol64,SMatPropHandle>` | 16 | material properties |
| `+0xA0` | `CTable<CSymbol64>` | 8 | slot refs |
| `+0xD8` | `CMap<uint,uint>` | 8 | permutations |
| `+0x118` | `CTable<SShaderInputData>` | 32 | decal inputs (the `auxillaryinputs` dead end) |
| `+0x150` | `CMap<CSymbol64,CSymbol64>` | 16 | **the texture table** |

so the image is `0x190`, not the `0x188` the uniform model computes. Two more
rules the old reader gets wrong: the on-disk array holds **`iused`** (live), not
**`count`** (runtime hash capacity, and `count > iused` for every `CMap`); and
`image_size = filesize - Σ(iused*stride)`, per file. Under the old model the
sixth container was read at the wrong offset AND the wrong length — which is
exactly why §6 dismissed it as "shared boilerplate": the bytes being
fingerprinted were never the table. The grammar came from `quest_combat_port`'s
`tools/resource_io/cgmaterialresource.py` (derived from `SGMaterialData::Inspect`
disassembly); it reads **1727/1727** materials here with zero failures.

**Defaults.** Every material declares all ~84 slots and points unused ones at
shared engine stubs. Separated by how many DISTINCT materials bind a texture —
sharply bimodal, so the threshold is a measurement, not a hash list:

| bound by ≥ N materials | textures |
|---|---|
| ≥ 100 | 14 ← engine stubs |
| ≥ 50 | 15 |
| == 1 | 5736 ← real per-material art |

**Slot roles.** Named preimage first, then Echo VR's own forward-hashed slot
grid, then evidence. Two things mattered:
* Echo VR and Lone Echo **do not share slot names**. Echo VR's most-bound slot
  is `layer0_basecolor_map`, absent from Lone Echo's table; unmapped, it routes
  nowhere and the base colour of most of a level silently vanishes (measured:
  `base_color` 56/406 while normal/specular/roughness sat near 270). It must be
  translated to the routable `albedo_map`.
* The heaviest slots have no recovered preimage; their role comes from evidence
  (`SLOT_ROLE`). `06470a0dd842f5d0` is confirmed **twice over** — BC5_UNORM on
  422/422 bindings, and 288 real `SShaderInputData` binds naming it
  `layer{0,1}_normal_map`.

**Independent validation.** The single material a human checked against the
running game, `1e070bb9873c1e45`, binds all three hand-confirmed textures, each
in the role this route assigns — base colour, normal, components. It also proves
the old model→material assignment was wrong: those textures were being forced
onto `a96dba2cbec4a581`/`2993ccd5a8e33846`, whose real tables are all-defaults.

**Measured effect on `576ed3f8428ebc4b`:**

| | before | after |
|---|---|---|
| roles from the real table | — | **376 / 406 (92.6%)** |
| roles from DXGI guessing | 380 | **22** |
| `base_color` channels | 391 (guessed) | **323** (real) |
| textures extracted | 120 | **460** |
| warnings | 251 | **22** |

Same run on `d09afd15b1c75c04`: **463 / 490 (94.5%)** from the real table, DXGI
18, textures 128 → 760, warnings 310 → 19.

Verified in the REAL add-on path (§7.1c's rule — run `import_lescatter`, not a
reproduction), both levels, **0 broken image nodes**:

| | `576ed3f8428ebc4b` | `d09afd15b1c75c04` |
|---|---|---|
| materials with a texture node | 175 / 179 | 201 / 216 |
| **image datablocks actually loaded** | **332** (was 89) | **383** |

The image-datablock count is the number that answers "are textures missing" —
3.7× more real texture data reaches Blender. The materials with no texture are
not failures: their slot tables are all-defaults, i.e. the engine draws them
untextured too.

⚠ **Watch the `--out` path.** It defaults to `J:\EchoVRModels` and the script
appends `scenes/<hash>` itself; passing the full scene dir double-nests it. And
passing a `J:\...` path through the Bash tool silently eats the backslashes, so
an extraction can appear to succeed while writing somewhere else entirely and a
later "verification" then reads stale output. Quote it, or use forward slashes.

Still open: ~30 materials fall through to DXGI, `layerN_rimlighting_map` and
`layer0_transmittance_map` are named but unrouted (no Principled socket), and
the several BC1_UNORM `components`-class slots are not individually
disambiguated (first wins).

### 7.6b The search that preceded it — candidates ruled out with evidence

Prompted by a direct challenge (why guess at all if the engine itself has to
know): does Echo VR store an explicit, indexed binding table the way Lone
Echo's `CGSceneResourceWin7` does (`u32 n_materials, u64 mat_hash[]..., u32
n_shadersets, u64 shd_hash[]..., sentinel 0xFFFFFFFFFFFFFFFF` — literal array
indices, no guessing)? Checked every structurally plausible candidate against
real bytes, not assumption:

* **`CGSceneResourceWin10` (`a388ea69e5108f4c`)**, both as Lone Echo's exact
  4-byte-packed layout and an 8-byte-aligned variant (Win10 could plausibly
  pack differently) — exhaustive backward scan from every sentinel occurrence
  in two real level files (3254+1597 and 3048+1498 positions checked). Zero
  self-consistent tables; every hit was noise (`n_mat=1, n_shd=0`, hash not on
  disk).
* **An undocumented 92-byte per-model resource** (`73d312a620da3824`), keyed
  by the same hash as a confirmed model — right size to be a tiny companion
  table. Turned out to be a repeated scalar count field (1–7 across sampled
  models, no hashes at all).
* **`CGMeshListResourceWin10`'s real internal layout** — not a single flat
  table as `MESH_STRIDE=152` assumed, but ten chained count-prefixed tables
  (confirmed by reusing the exact table-chaining logic
  `evr-mesh-importer/evr_mesh_importer/decode.py::_extract_metadata_meshes`
  already uses successfully for geometry). Scanned every 8-byte offset across
  all ten tables, ~3600+ real submesh records (336-byte per-submesh record
  included) for a material/shaderset hash. Zero hits anywhere.
* **`CModelCRWin10`'s per-record residual heap and fixed record fields** —
  using a real, disassembly-validated decoder (see below), scanned the heap
  and every 8-byte offset of the `main`/`sprops` records. One weak, low-
  coverage signal (~5% of records) at a consistent offset — plausibly a rare
  per-instance material override, not the general mechanism.

⚠ The conclusion this section originally reached -- "the per-submesh binding
is not in the data" -- was WRONG, and §7.6 above supersedes it. The search below
was looking for the link on the MESH/SCENE side; it is on the MATERIAL side, in
a container this repo had already decoded at the wrong offset. Kept because the
ruled-out candidates are still ruled out.

**Also found: an independent, disassembly-level reverse-engineering
project** (`J:\EchoVR-Tools-Launcher\quest_combat_port`, a PC→Quest Echo VR
Combat port effort with byte-validated decoders built from real `libr15.so`
disassembly, not inference). Its resource-type encyclopedia
(`docs/format/resource-type-encyclopedia.md`) states `CGSceneResource` really
does hold "draw-node graph + **mat/shaderset slots**" — and its decoder
(`tools/resource_io/cgsceneresource.py`) parses our exact PC files **byte-
exact to EOF**, confirming the on-disk grammar is understood correctly.
Running it against our two levels: `materials = CTable<CSymbol64>`,
`shadersets = CTable<SGMeshShaderSet, 24B>` are real (8/9 and 12/12 resolve to
genuine `CGMaterialResourceWin10`/`CGShaderSetResourceWin10` files on disk) —
**but only 9–11 materials and 8–12 shadersets per level**, far short of the
400+ used across all props. This table is scoped to scene-level constructs
(particle FX, decals, lights), not the per-model mesh palette. So: the real
binding order genuinely does exist in Echo VR's data for *some* things — the
"no computable source" framing from earlier in this investigation was too
absolute — but not, as far as this search reached, for the specific
per-submesh problem this pipeline needs solved.

**Self-correction, recorded because it nearly became a false "fix":** the
same project ships `ssbind_true.py`, a disassembly-validated decoder for
`CGShaderSetResourceWin10`'s real `SShaderInputData` bind table (EOF-exact on
3692/3692 stock shader sets — the same 3692 this extract carries). Spot-
checking two shader sets our pipeline had used for two different draws of the
same material (`d46780386d6debab`, `ea8a7fee4ce240f9`) against it found zero
bound textures in the real table, which read as a confirmed bug in
`evr_shaderset.py`'s old anchored byte-scan (find a known texture hash at
`+8`, validate slot/layer/type plausibility — a coincidence detector, not a
structure reader). It wasn't one: re-checking the *specific* materials.json
entries that had looked contradictory showed their `role_sources` already
said `"format"` (DXGI fallback) — the anchored scan had already, correctly,
found nothing for those two shader sets and fallen through. Running the
comparison properly, across the **entire corpus** (all 3692 shader sets, not
two cherry-picked ones), found **zero disagreement**: both methods produce
exactly 6231 binds, same files, same values. The lesson worth keeping: a
2-file "proof" of a discrepancy is not a proof until it survives the full
corpus, especially when the two files were picked *because* they looked
wrong rather than sampled at random.

`evr_shaderset.py` was still switched over to the validated structural
decoder (`_parse_structured`, ported from `ssbind_true.parse_shaderset`) —
zero behaviour change (proven above), but now correct by construction against
disassembly rather than empirically observed to agree, and immune to any
*future* coincidental false positive the old anchored scan could in principle
have produced on data outside what was checked here.

---

## 8. Design notes worth preserving

* **The add-on already supports full PBR on the scatter path.**
  `scatter_import.py` hands a **v2** `spec` to `material_builder` verbatim. The
  original Echo VR extractor emitted v1, which the add-on documents as losing
  alpha, render mode, emission, specular, roughness and blend masks. Emitting v2
  is what unlocks it — no add-on change needed.
* **Everything routes through Lone Echo's own material code.**
  `le_mesh.materials.build_material_spec` does the channel routing, alpha chain,
  colourspace and BC5 reconstruction. Nothing about material semantics is
  re-implemented for Echo VR; the modules here only supply inputs.
* **Provenance is tracked and must stay honest.** DXGI-guessed roles are marked
  `SOURCE_FORMAT`, shaderset-declared ones `SOURCE_ARRAY`. Never let a guess
  read as a declared binding.
* The hash is CRC-64 poly `0x95AC9329AC4BC9B5`, seed `-1`, case-insensitive —
  identical in both games (`le_mesh.material_scalars.symbol64`).
