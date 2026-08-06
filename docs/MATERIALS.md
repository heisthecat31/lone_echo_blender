# Materials

**Status (0.4.0): the `.lemesh` path is wired end to end. Base colour, normal,
roughness/AO, specular/F0, alpha, transmission tint, emission, layer blend masks
and the render pass all reach EEVEE; normal maps now run on the *shipped* tangent
basis, and specular is reproduced with both of the engine's lobes. The
`.lescatter` path still does not — its sidecar carries only base colour and
normal.**

Read [What actually reaches EEVEE](#what-actually-reaches-eevee) for the exact
split before assuming any import is full PBR, and [the role
ladder](#role-ladder) for how a texture binding gets a meaning at all — through
0.3.0 that was a single lookup covering under half the corpus.

⛔ Two defects are **open** at 0.4.0. They are documented in the sections they
belong to rather than in a release note: [`eBlendTranslucent` is not
implemented](#blend-translucent), and [19 of 44 audited materials drop an
authored layer](#dropped-layers) — 18 provably invisible, **1 not**.

Evidence tags used below — `stream-confirmed`, `corpus-confirmed`,
`shader-confirmed`, `name-confirmed` / `name-only`, `engine-confirmed`,
`inferred` — are defined in [FORMATS.md](FORMATS.md#evidence-vocabulary).

---

<a id="role-ladder"></a>
## Where a texture role comes from — the ladder

A binding is a pair: a **texture** and a **role**. The texture is a hash and is
easy. The role — `layer0_composite_diffuse` and its forty-odd siblings — is what
decides which Principled socket that texture reaches, and it is *not* stored
beside the texture.

Through 0.3.0 there was exactly one source for it: the `inputname` field of an
`SShaderInputData` row in the shaderset. That covers under half the corpus.
**52.4 % of shadersets ship no `SShaderInputData` array at all**
(`corpus-confirmed`) — including the two carrying Liv's largest meshes, 13,168 v
and 14,270 v. Measured on `2fd6839161785e9c`: 4 of 17 shadersets contain **zero**
8-aligned `u64` anywhere in 23–73 KB that matches any of 24,852 known `CSymbol64`
hashes. Not a predicate failure and not per-archive damage — the same assets are
byte-identical in `6a993ea8dd6c3dfd`, and their `CGShaderSetResourceWin7GPU`
sibling is a 32-byte zero stub (`stream-confirmed`).

0.4.0 replaces the single lookup with a **ladder**, tried in this order, and
records **which rung answered** on every channel:

| # | rung | what it is | evidence |
|---|---|---|---|
| 1 | `array` | this shaderset's own `SShaderInputData` row — the authored `inputname` | `stream-confirmed` |
| 2 | `archive` | propagated from a sibling shaderset in the **same archive** that binds the same texture and *does* ship an array | `corpus-confirmed` |
| 3 | `corpus` | propagated from the corpus-wide `texture_hash -> role` index, under the unanimity policy below | `corpus-confirmed` |
| 4 | `format` | nothing names it anywhere: the composite atlas's DXGI `FORMAT` plus its resolution group implies the suffix | `corpus-confirmed` |

Rung 2 is why coverage is so uneven per archive on its own: it reaches 9/9 roles
in a 259-shaderset archive and 4/15 in a 17-shaderset one. Rung 3 is that same
propagation run over all 149 shaderset-bearing archives at once.

Two further sources are recorded alongside the four rungs:

* `lod_sibling` — a character ships each LOD as its own mesh with its own
  shaderset, but the two share one `material_hash`: they are one authored
  material compiled twice. The join is deliberately **tight** and fires only when
  (a) both shadersets carry the same `material_hash`, (b) the donor's role came
  from its own array, and (c) the same texture hash is bound by both. Same
  material, same texture, one array — there is nothing left to vote on. On
  `liv_head` the LOD-1 skin shaderset `b149f66575443907` ships an array declaring
  `layer0_thickness_mask` and `layer0_detail_normal_map` while the LOD-0
  shaderset `c8deda534cc6f28b` — the one every render actually draws — ships none,
  so eight of its binds used to land as `rdef_bind23..30`.
* `rdef` — RDEF knew the *texture* and nothing knew the *role*.

⛔ **Nothing unresolved is guessed into a Principled channel.** A bind the ladder
cannot name stays `rdef_bind{n}`, lands in `unrouted_roles`, and says *why*. The
provenance of each binding is written per channel as `role_sources`
(`array` / `archive` / `corpus` / `format` / `lod_sibling` / `rdef`) and each
disputed texture's vote counts as `role_ambiguity`, so a corpus-**voted** role can
never be mistaken for an array-**declared** one. Both are audit only — nothing in
the routing reads them.

### RDEF is the binding *and* the name source

A shaderset with no `SShaderInputData` array is read through its compiled
shader's DXBC **`RDEF`** (resource-definition) chunk, which names every constant
buffer, SRV and sampler the shader declares. RAD's cook **rewrites each material
sampler's name to the name of the texture it bound**, so:

★ **The law:** for a material bind, the `RDEF` resource name minus its `_decl`
suffix is the exact `CSymbol64` preimage of that bind's `textureassetid` —
`symbol64(rdef_name − "_decl") == textureassetid`, **74 verified / 0 mismatched**
(`stream-confirmed`).

RDEF is therefore a strict superset of the array: it needs no needle set, it
covers the array-less shadersets, and it yields **exact asset names**
(`liv_evasuit_pack_a_detail_msk`, `liv_helmet_glass_nml`, …) rather than only
hashes — names the 24,852-entry harvested dictionary did not have. Decoded by
`blender_tool/le_mesh/dxbc.py`, pure stdlib, pinned by
`blender_tool/tests/test_dxbc.py`.

Engine-supplied inputs are `k_`-prefixed (`k_irradiance_0`, `k_shadow_map`,
`k_bone_cache_prev`, …) and carry `textureassetid == -1`; they are bound by the
renderer, not by the material, and are skipped.

⛔ **RDEF does not give you the role.** The cook overwrote the HLSL sampler name
— which *was* the role — with the texture name, so the role survives only in the
array. ⛔ **Bind order is not a substitute either:** over 3,146
`(shaderset, layer)` groups binding the full four-role composite set, **all 24
register permutations occur, the modal one at 10.5 %** (`corpus-confirmed`).

### The corpus index, and the policy that refuses

Rung 3 is a corpus-wide `texture_hash -> role` index
(`blender_tool/le_mesh/role_index.py`). Measured over **25,694 binds / 138
archives / 4,507 shadersets / 2,194 distinct textures** (`corpus-confirmed`):

| | count | of the 1,665 textures declared by more than one shaderset |
|---|---:|---:|
| textures carrying more than one role | 90 | **5.41 %** |
| … disagreeing only in the LAYER INDEX | 74 | 4.44 % |
| … disagreeing in the SUFFIX | **16** | **0.96 %** |

The two classes are not equally dangerous and are not the same phenomenon:

* **Layer-index conflicts are benign and expected.** 50 of the 74 are
  `generated_composite_*` — the cook's per-material atlases — used as
  `layer0_composite_diffuse` by one material and `layer1_composite_diffuse` by
  another. The Principled channel is chosen by the **suffix**, so the texture
  still reaches the right socket; only the layer-compositing weight can be
  misattributed.
* **Suffix conflicts are real authored ambiguity, not decode error.** All 16 are
  named, and every one is a reusable single-channel utility plate —
  `fx_cmn_scrolling_noise_swirls_liquid_clr` is bound as albedo / alpha / blend
  mask / emissive by 38 different shadersets, `mfx_water_runoff_sheet_b_nml` as
  normal / flowmap / `pom_height_map` by 24. A greyscale noise plate genuinely
  *is* a different thing in each material, so no amount of extra evidence
  resolves it. ★ **Zero of the 16 is a `generated_composite_*`** — the class
  carrying the binds the ladder exists to recover is exactly the class that never
  disagrees on its suffix.

⛔ **The suffix must be unanimous, or nothing is applied.** A layer-index
disagreement is recorded and the suffix used; a suffix disagreement is refused
outright and the bind stays unrouted. The four outcomes are named —
`unanimous`, `layer_ambiguous`, `suffix_conflict`, `absent` — so a refusal is a
value, not a silence.

Generate the index from your own game install, exactly like the two archive
indexes in [Cross-archive resolution](#cross-archive-resolution--read-this-before-trusting-a-package):

```bat
python.exe scripts\le_role_index.py        :: role_index.tsv, 149 archives
```

⛔ **No index data ships with this repository.** It is derived from your files.

### The last rung: the DXGI `FORMAT`

A `generated_composite_*` atlas that **no array anywhere declares** has one
signal left. Every name- or identity-based route is a measured closed negative:

* corpus-wide propagation resolves **0 of Liv's 11**, over all 149
  shaderset-bearing archives (`corpus-confirmed`);
* the composite **name** carries no channel code —
  `generated_composite_<h1>_<h2>` has **2,241 distinct `h1` and 2,241 distinct
  `h2` over 2,241 distinct names**, i.e. both halves are per-texture identity
  hashes, and **0 of the 4,482 inner hashes** appear in the 27,995-entry
  harvested name dictionary;
* the **bind register** is refuted above (all 24 permutations occur).

What *is* a function of the data is the **format**. Over the 216
`generated_composite_*` textures carrying both a role and a measured DXGI format
(`corpus-confirmed`):

| format class | n | role suffix |
|---|---:|---|
| `BC5_UNORM` | 52 | `composite_normals`, 52/52 |
| non-sRGB, non-BC5 (`BC1`/`BC3`/`BC4_UNORM`) | 52 | `composite_components` |
| any `_SRGB` | 112 | diffuse / specular / data0 |

⛔ **The sRGB class is not separable by format alone** — `BC3_UNORM_SRGB` is
specular 51×, diffuse 11×, data0 4×. Within one resolution group the single
`BC1_UNORM_SRGB` is the diffuse and the single `BC3_UNORM_SRGB` is the specular;
**any group that is not that shape is refused outright**, and `composite_data0`
is never emitted this way.

⛔ **The format gives the suffix, never the layer.** The layer assigned is the
lowest index the shaderset has not already claimed, because layer is genuinely
not recoverable from the format: resolution-group and layer-group agree on only
**95.5 %** of shadersets, and layer 0 is the strictly largest group in just **5 of
19** multi-layer shadersets. That is sound exactly when one unresolved group
remains, which is why more than one is refused. Every refusal names itself:
`no_format`, `many_unresolved_resolution_groups`, `format_not_unique_in_group`,
`format_matches_no_composite_class`, `no_free_layer_index`,
`role_already_carried_by_this_shaderset`.

---

## The fabricated-name correction (0.2.0)

Ten entries in `le_mesh/materials.py::INPUTNAME_ROLE` were labelled
`"tentative (DDS-format inferred)"`. They were **invented labels that do not hash
to the key they were filed under** — the names were guessed from each texture's DDS
format rather than recovered.

Every texture-role key is a `CSymbol64` hash of the shader input's name, so a name
is either the exact preimage of its key or it is fiction. All ten have now been
cracked against the game's own authored material-parameter vocabulary, and every
replacement satisfies `symbol64(name) == key` exactly:

| hash | fabricated label (wrong) | real preimage | rows seen |
|---|---|---|---:|
| `e348dd9cd3fdc817` | `layer0_diffuse_map` | **`layer0_composite_diffuse`** | 47 |
| `33d1823268b0a40c` | `layer0_rgba_surface` | **`layer0_composite_specular`** | 44 |
| `e342db88d8e9d701` | `layer0_normal_map_alt` | **`layer0_composite_normals`** | 44 |
| `d000069cc9204803` | `layer0_linear_map` | **`layer0_composite_components`** | 44 |
| `8ed4ab4792aaf806` | `layer1_mask_b` | **`layer1_alpha_map`** | 11 |
| `96a697df18ea44f1` | `layer1_glass_diffuse` | **`layer1_composite_diffuse`** | 1 |
| `5359456ffb9a1dae` | `layer1_glass_rgba` | **`layer1_composite_specular`** | 1 |
| `96ac91cb13fe5be7` | `layer1_glass_normal` | **`layer1_composite_normals`** | 1 |
| `228838c1c7770d21` | `layer1_glass_mask` | **`layer1_composite_components`** | 1 |
| `39d68102257d6d24` | `layer0_emissive_rgba` | **`layer0_back_lighting_map`** | 2 |
| `9dba2dc44433be64` | *(was unlabelled)* | **`layer0_alpha_map`** | 9 |
| `571b8c6b2599c12a` | *(was unlabelled)* | **`layer0_secondary_emissive_map`** | 9 |

**There is no glass-specific texture role.** The four "glass" roles were invented;
`layer1_composite_*` is simply the layer-1 composite set.

### This changed real channel routing

Two of the fakes were wrong about *meaning*, not just spelling, and the Principled
channel priority lists were built from those names. Fixing the names fixed three
mis-assignments:

* `layer1_alpha_map` (the real name of `layer1_mask_b`) is **opacity**, and it was
  driving **Roughness**.
* `layer0_back_lighting_map` (the real name of `layer0_emissive_rgba`) is
  **translucency / back-lighting**, and it was driving **Emission**. There is no
  faithful Principled target for it, so it now lives in its own
  `TRANSLUCENCY_ROLES` list, carried for audit and deliberately kept out of
  `EMISSION_ROLES`.
* The two `*_composite_specular` maps were treated as **base colour** under their
  fabricated `*_rgba` names. They are specular/roughness data.

Three tests lock this so it cannot regress:
`test_transparency.test_cracked_inputname_preimages` (every name is its key's
preimage), `test_transparency.test_no_fabricated_role_names` (none of the ten fakes
hashes to the key it was filed under), and
`test_materials.test_every_role_name_hashes_to_its_own_key` (the whole table, plus
"no entry may still be labelled tentative").

### Composite channel packing

The composite textures are a build-generated packed set, and the channels are:

```
diffusealbedo    = composite_diffuse.xyz
alpha            = composite_diffuse.w * vertexcolor.w      <-- opacity lives here
sqrtroughness[0] = composite_components.x
ambientocclusion = composite_components.y
brdfblends.y     = composite_components.z
sqrtroughness[1] = composite_components.w
specintensity    = composite_specular.w
specalbedo       = composite_specular.xyz * composite_specular.w
roughness        = sqrtroughness * sqrtroughness
```

0.3.0 splits them: `composite_components.R` drives Roughness and `.G` is exposed
as AO. ⚠ **Roughness takes `.R` RAW, not squared.** The engine's GGX alpha is
`sqrtroughness²` while Blender's is `Roughness²`, so equating the two gives
`Roughness == sqrtroughness == composite_components.R`. Squaring it made Blender's
alpha `sqrtroughness⁴` and the peak highlight **2.4× (at 0.80) to 920× (at 0.15)
too bright**. `roughness_is_sqrt` in the manifest means "this texel is in sqrt
space" — it is not an instruction to convert.

AO is deliberately **left unconnected**. The engine multiplies `ambientocclusion`
into its *ambient/indirect* diffuse term only, and Principled has no occlusion
input; wiring it to Base Color would darken direct light too. The Separate Color
node is in the graph and `le_ao_channel` records which output, so a user can wire
it, and `opts["ao_to_base_color"]` opts in to the approximation.

---

## What actually reaches EEVEE

An end-to-end audit of the decoder → manifest → builder → EEVEE chain found **nine
breaks**. Six are fixed — four in 0.3.0 and two in 0.2.0 — and the **three** that
remain are all on the `.lescatter` (whole-level scatter) path.

| # | where | state |
|---|---|---|
| 1 | `material_builder.py` | **fixed 0.3.0** — `spec["alpha"]` (`k_alpha`) is applied, and a material with `k_alpha < 1` and no other transparency evidence is upgraded to a blended pass so it is actually visible. |
| 2 | `material_builder.py` | **fixed 0.3.0** — `blend_method` is a legacy alias on 4.2+ (`OPAQUE`/`CLIP`/`HASHED` all collapse to dithered) and is no longer used. The pass is driven from `surface_render_method`, and a cutout is a `Math(GREATER_THAN)` node. |
| 3 | `material_builder.py` | **fixed 0.3.0** — `mattype` (the pass) and `blend_mode` (the equation) are both read; see [Target mapping](#target-mapping). |
| 4 | `le_mesh/material_scalars.py` | **fixed 0.3.0** — emissive intensity comes from the layer whose emissive *map* was routed. See the worked example below. |
| 5 | `scripts/le_scene_materials.py` | **NOT fixed** — the `.lescatter` sidecar still computes `opacity`/`emission` and then writes only `base_color` and `normal`, and extracts only those two DDS. |
| 6 | `addon/lone_echo_import/scatter_import.py` | **NOT fixed** — the spec built from that sidecar carries only `base_color`, `normal`, `base_color_factor`, `double_sided`; everything else is dropped before `build_material` is called. |
| 7 | the `.lescatter` manifest | **NOT fixed** — no `materials` array; meshes carry `matidx`/`shdidx` with nothing inside the package to resolve them against. |
| 8 | `le_mesh/materials.py` | fixed 0.2.0 — the fabricated role names above. |
| 9 | consumers of the role table | fixed 0.2.0 — anything that resolved those ten hashes got a confident wrong answer. |

So: **`.lemesh` imports carry the full material chain; `.lescatter` imports still
carry base colour + normal only.** Closing 5–7 is mostly plumbing — the decoders
already produce the values and the scatter schema drops them.

⚠ 0.3.0 could not claim an end-to-end run against real archives; its extractor
changes rested on unit tests and on reasoning about measured counts. 0.4.0 adds
tests that open real packages and run the extractor end to end
(`blender_tool/tests/test_real_package_invariants.py`,
`blender_tool/tests/test_extractor_e2e.py`), and a runner that counts skips,
prints every skip reason and inventories the scripts under `tests/` it does *not*
execute — a green run full of silent no-ops is how a real defect survived a whole
cycle of green suites. See [TESTING.md](TESTING.md).

⛔ Two breaks in this chain are **not** on the `.lescatter` path and are open at
0.4.0: [`eBlendTranslucent`](#blend-translucent) is not built at all, and
[19 of 44 audited materials drop an authored layer](#dropped-layers).

### The emissive-layer bug, with numbers

One shipped transparent-translucent material stores:

```
layer0_emissive_intensity = 2
layer1_emissive_intensity = 25      <-- the layer that actually has the emissive map
layer2_emissive_intensity = 2
k_emissive_scale          = absent -> authored default 1.0

correct Emission Strength = 25 × 1.0 = 25.0
what the importer would emit =  2.0     (layer0 wins unconditionally)
                             -> 12.5× too dim
```

This is a *layer-selection* bug, not a units bug. The intensity must be read from
the same layer index as the emissive map that was selected — and in the corpus
`layer1_emissive_map` is more common than `layer0_emissive_map` (16 rows vs 13).

Also: `is_emissive` is computed from `bakeemissivecolor`, which is `(0,0,0)` for
**every** genuinely emissive material inspected. It under-reports emission and must
not be used as the gate.

---

## `SGMaterialData` — what is on disk

```
+0x000 u64    materialfx
+0x008 4×f32  bakecolor              <- authored "Bake Color"
+0x018 4×f32  bakeemissivecolor      <- authored bake emissive colour
+0x028 u16    blendmode              <- EBlendMode
+0x02a u16    mattype                <- EMaterialType
+0x02c u32    flags                  <- SGMaterialData::EFlags
+0x030 f32    shadowfadedist
+0x038 CTable<u32>              materialprops
+0x070 CMap<CSymbol64,u32>      materialpropoffsets
+0x0b0 CTable<CSymbol64>        uvsets
+0x0e8 CMap<u32,CSymbol64>      permutations
+0x128 CTable<SShaderInputData> auxillaryinputs
+0x160 trailing arrays, in that order
```

The trailing layout is confirmed by slice-size arithmetic: for the 21 materials of
one archive, the exact slice size is reproduced by
`0x160 + 4·n_props + 16·n_propoffs + 8·n_uvsets + 16·n_perms + 32·n_auxinputs` on
every row (424/444/464/544/584 …). Locked by
`test_transparency.test_material_slice_size_arithmetic`.

`le_mesh/material_scalars.py` decodes the header scalars and `materialprops`. It
ignores `uvsets` (always 1 in shipped rows), `permutations` (**always empty** — see
below) and `auxillaryinputs` (a damage-decal system, not opacity).

> `bakecolor` is **not** a runtime tint. It is the baker's flat albedo, authored as
> a "Bake Color" and never referenced by the ubershader. The runtime albedo tint is
> the per-layer `layerN_albedo_tint_color`. Using `bakecolor` as a base-colour
> fallback is defensible, but it must be labelled a fallback approximation.

### How transparency is selected

Three mechanisms, and **only two of them are on disk**.

**(a) `mattype` — the render pass.** 17 values; observed in shipped bytes:
`eMTForwardOpaque`, `eMTForwardTransparent`, `eMTAlphaTested`, `eMTSkirt`,
`eMTTransparentPostAA`. The rest (deferred opaque, particles, refraction, hair,
skydome, outline …) are defined but not yet seen.

**(b) `blendmode` — the blend equation.** 18 values; observed: `eBlendOpaque`,
`eBlendTranslucent`, `eBlendTransparent`, `eBlendLinearDodge`. The observed joint
distribution in one archive:

| n | mattype | blendmode |
|---|---|---|
| 9 | `eMTForwardOpaque` | `eBlendOpaque` |
| 7 | `eMTForwardTransparent` | `eBlendTranslucent` |
| 3 | `eMTAlphaTested` | `eBlendOpaque` |
| 1 | `eMTForwardTransparent` | `eBlendTransparent` |
| 1 | `eMTTransparentPostAA` | `eBlendLinearDodge` |

The pairing is consistent and non-redundant: **`mattype` picks the pass,
`blendmode` picks the equation.** Alpha-tested materials render in an opaque pass —
the cutout happens with a clip, not with blending.

**(c) compile options — the permutation — NOT on disk.** Whether an alpha-tested
material clips or *dithers*, whether a transparent material uses premultiplied
alpha or refraction, whether vertex colour or output alpha is enabled — these are
**shader permutation bits**, carried by the shaderset's permutation key, not by the
material. `SGMaterialData.permutations` ships **empty**, so it is not a second copy.

> **Blunt answer:** that information is **not recoverable from `SGMaterialData`**.
> It needs the shaderset's permutation key decoded against the option bit order, or
> disassembly. The full bit order is not pinned.

### `eMTSkirt` is a decal sheet

`eMTSkirt` / `eBlendSkirt` is the engine's **decal pass** (`eSkirts`), not an
opaque one, and through 0.3.0 this importer rendered it opaque. A decal sheet is a
quad whose **cut-out alpha is the whole content**: drawn opaque it is a solid
rectangle sitting over the surface it was authored to detail.

The pass and the equation are independent `u16`s, so either field alone is enough
to identify a skirt; in the shipped corpus the two always co-occur (11 of 11 rows,
`corpus-confirmed`).

⚠ **This is the one material type whose resolved render mode may legitimately
differ from the mode stored in the manifest.** Every `.lemesh` written before
0.4.0 records `render_mode: "OPAQUE"` for the decal pass, and re-cooking every
package to fix a picture is not a reasonable prerequisite — so
`resolve_render_mode` repairs a skirt that says `OPAQUE` to `BLEND` and records
`le_skirt_render_mode_repaired` on the material. It is the only place that
function overrides the decoder, and fresh manifests never take the branch because
`le_mesh.materials.render_mode_for` now agrees (asserted by
`blender_tool/tests/test_skirt_decal_alpha.py`). Every skirt is tagged `le_skirt`
so a scene's decals can be found. `opts["skirt_alpha"] = False` restores the
pre-fix opaque picture for an A/B.

<a id="blend-translucent"></a>
### ⛔ `eBlendTranslucent` is not implemented

`eBlendTranslucent` is the **second most common** equation in the joint
distribution above — 7 of the 21 materials in that archive — and **0.4.0 does not
build it**. [Target mapping](#target-mapping) records what it *should* become: a
`'BLENDED'` pass plus a Transparent BSDF coloured by
`opacity_map × opacity_tint_color`, a dual-source add and specifically **not**
Principled's `Transmission`, which is refraction. Until that exists, a material
declaring it falls back to the nearest supported pass, which is visibly wrong on
the surfaces that use it.

This is an open defect, not a documented approximation.

### The alpha chain

```
alpha = albedovertex.a * emissivevertex.a
      * albedomap.a * albedotint.a
      * emissivemap.a * emissivemap2.a * emissivetint.a
      * diffusemap.a * diffusetint.a
      * alphamap                       <- layerN_alpha_map (scalar)
      * k_alpha
alpha = saturate(alpha)
```

### `opacity_map` is not alpha

`opacity` is a **float3**, not a scalar: a **coloured transmission tint** — how much
of the background shows through, per channel — consumed either by the screen-space
refraction path or by dual-source blending. Mapping it onto Blender's `Alpha` (what
the role table does today) is **wrong**: it makes coloured glass uniformly
see-through instead of tinting what is behind it.

### Roles: what 0.3.0 changed

| role | before 0.3.0 | now |
|---|---|---|
| `layerN_composite_diffuse` **.a** | base colour RGB only | base colour **+ Alpha from `.a`**, when the format has a real alpha block and the material is not opaque |
| `layer0_alpha_map` | absent → `unknown_s{slot}` | `alpha` channel (the scalar multiplier) |
| `layerN_opacity_map` | Alpha | `transmission` — a Transparent BSDF colour added on, **not** the Alpha socket |
| `layer0_secondary_emissive_map` | absent | its own `secondary_emission` channel (carried, not yet wired to a socket) |
| `layerN_composite_components` | Roughness (whole RGB) | Roughness = `.R` raw, AO = `.G` |
| `layerN_composite_specular` | base colour (!) | `specular` — see below |
| `layerN_specular_map` | base colour (!) | `specular`, scaled by `fresnel` rather than by its own alpha |

Routing is now **layer-aware**: it is (suffix → channel) × (layer index), not a
flat first-present-wins list. A material carrying `layer0_emissive_map` and
`layer1_emissive_map` keeps both, on their own layers, instead of dropping one.

---

## Specular / F0

`layers[i].specalbedo[0]` **is** the Schlick F0 term, and two samplers feed it:

```
composite_specular : specalbedo = rgb * a  ;  specintensity = a
specular_map       : specalbedo = rgb * fresnel ; specintensity = fresnel
```

`fresnel` is authored 0.010 and no shipped material in the 51-package fixture
corpus overrides it (nor `specular_tint_color`, `specular_gloss` or
`enable_specular`), so the constant is safe to rely on.

An earlier pass concluded this was not representable in Blender, because
`Specular IOR Level` is `hard_max = 1.0`, which caps F0 at 0.08 — and shipped
`composite_specular` data reaches 1.0. That was only half the socket. `Specular
Tint` is `hard_max = FLT_MAX`, and Principled's dielectric reflectance is

```
F0 = F0(IOR) × 2 × `Specular IOR Level` × `Specular Tint`
```

**linear and unclamped**. Leaving the level at its 0.5 "no adjustment" point and
putting the whole of F0 into the tint (`Specular Tint = specalbedo / F0(IOR)`,
i.e. ×25 at IOR 1.5) matched a Glossy BSDF whose colour *is* the target F0 to
**0.00 %** at every F0 in {0.01 … 1.0} and for IOR in {1.33, 1.5, 2.0} in Cycles;
EEVEE Next tracks the same curve within 2 %.

Leaving the channel unwired is **not** neutral: Principled's default pins F0 at
0.04, which measured 6×–20× too dark on shipped `composite_specular` data and 4×
too bright on the `specular_map` panels. Opt out with `opts["wire_specular"] = False`.

**Residual, common to every Blender construction and not fixable by wiring:** the
engine's GGX visibility uses the Burley remap `alpha = ((m+1)/2)²` where Blender
uses Smith with `alpha = roughness²`. Equal at normal incidence; Blender is ~1.4×
brighter at 60° and ~9× at 85° in the mirror configuration.

### Two lobes, not one

★ RAD's composite path runs **two specular lobes** and weights them per texel.
0.3.0 rendered lobe [0] at full weight and dropped lobe [1] entirely
(`shader-confirmed`):

```
brdfblends       = (1 − composite_components.z, composite_components.z)
specintensity[0] = composite_specular.w ; specalbedo[0] = composite_specular.xyz * .w
specintensity[1] = composite_data0.w    ; specalbedo[1] = composite_data0.xyz    * .w
sqrtroughness[0] = composite_components.x
sqrtroughness[1] = composite_components.w
```

`layerN_composite_data0` is lobe [1]'s albedo — **the exact packing
`composite_specular` uses for lobe [0], one index up** — and its roughness comes
from `composite_components.w` rather than `.x`. When nothing is bound there the
engine sets `specalbedo[1] = specalbedo[0]`.

Blender's Principled BSDF has **one** specular lobe, so there is no faithful
target for the second: `composite_data0` is deliberately never routed to a
channel and appears in `unrouted_roles` with that reason attached. The faithful
single-lobe stand-in is the engine's *own* weighted combination, and 0.4.0 builds
it — weight `1 − components.B`, lobe [1]'s roughness from `components.A`, blended
into lobe [0]'s only when lobe [1] actually carries energy. A zero albedo
contributes no specular, so weighting its roughness in would be a fudge rather
than a decode.

Measured on Liv's orange gel-coat, dropping lobe [1] **over-drove F0 by 2.75× and
under-drove roughness by 6.9×** — the "wet vinyl" look.

⚠ Because `composite_data0` is (correctly) unrouted, a manifest carries no `dxgi`
for it; the builder reads the DXGI format out of the DDS header already in the
package (a 148-byte read, no archive opened). Reading an `_SRGB` specular map as
linear would inflate F0 — the exact class of error the two-lobe work fixes.

| option | default | meaning |
|---|---|---|
| `brdf_lobe_blend` | **`True`** | build the weighted two-lobe combination. `False` restores the pre-fix single-lobe-at-full-weight look for an A/B. |
| `brdf_lobe_zero_roughness_gate` | **`True`** | ⚠ keeps a `composite_components.x` of exactly 0 out of Blender's `Roughness`. Blender reads that as a **perfect mirror**; the engine's own GGX numerator is `sqrtroughness⁴`, so there it contributes nothing at all. |

A material predating the `brdf_lobes` manifest record still gets the fix: the
record is reconstructed from the manifest it does have — the components texture is
the `roughness` channel, the weight is its `.z`, and lobe [1]'s albedo hash is
`role_textures["layer{N}_composite_data0"]`, which the decoder records even though
it never routes it. Pinned by `blender_tool/tests/test_brdf_lobes.py`.

---

<a id="tangent-basis"></a>
## Normal maps — the shipped tangent basis

★ **Consuming the shipped tangent fixed a defect present in every render this
tool had ever made.**

`tangent` (`CGVertexFormat::EUsage` 3, `s16n` × 4) is decoded on **913 of 913**
objects (`corpus-confirmed`) and, until 0.4.0, was imported by **nothing** —
three writers, zero readers. Every normal map therefore ran on Blender's
UV-derived (mikktspace) tangent.

⛔ **That basis was not merely different, it was inverted.** The importer flips V
for Blender, which inverts the derived bitangent: Blender's own
`loop.bitangent_sign` agrees with the shipped `sign(w)` on **0.0–0.8 % of loops**
on 11 of the 13 measured meshes (and on exactly 50.0 % of the two back-shell
meshes, where the reversed winding flips it back on one shell).
**An inverted bitangent inverts the green channel of every tangent-space normal
map.** The shipped basis never consults the UV derivative, so it cannot inherit
that error.

Measured against Blender's *actual* tangent (`mesh.calc_tangents()`, i.e.
mikktspace) on `64b4b5b2a0153f7e` — 13 meshes, 277,336 loops, `engine-confirmed`
in Blender 5.1.1 by `blender_tool/tests/blender_tangent_probe.py`:

| mesh class | angle between the shipped and the derived basis |
|---|---|
| the 2 carrying a duplicated back-face shell | **median 93.1°, p90 179.8°, max 180.0°; 50.6 % of loops past 15°** |
| the 11 single-shell meshes | median 0.05–1.9°, p99 1.1–22.1°; 1–3 % of loops past 15° |

⚠ The defect is real but it is **concentrated in the back shell**, not spread
evenly across the body. Earlier estimates of 20–25 % past 15° were measured
against a naive area-weighted UV tangent and over-stated Blender's own error by an
order of magnitude.

### `tangent.w` is two fields, not a handedness bit

⛔ `.w` is **not** ±1. It takes exactly **four** values — **−1.0, −0.5, +0.5,
+1.0** — over **509,266** vertices (`corpus-confirmed`,
`blender_tool/tests/test_vertex_streams.py`). `s16n` maps `int16` onto [−1, 1], so
this is a deliberate 2-bit quantisation: a **sign** *and* a **magnitude**. Both
halves are now measured.

**Sign = bitangent handedness.** Over 5 character packages / 36 objects /
**397,082 vertices**, `sign(w)` agrees with the handedness derived from the
shipped UVs (`sign(dot(cross(N, T), B_uv))`, Lengyel accumulation, disk space, no
V flip) on **397,082 of 397,082 vertices — 100.00 %**, and at that rate inside
each of the four states separately (`stream-confirmed`). So
`B = cross(N, T) · sign(w)` is a *reconstruction*, not an assumption.

**Magnitude tags a duplicated back-face shell.** Over 5 character packages /
**63 objects** carrying a 4-component tangent: 26 carry both magnitudes, 37 carry
only `|w| = 1.0`, and **0 carry only 0.5** — there is never a back shell without a
front. In all 26 the two classes are exactly equal in size, and
(`stream-confirmed`):

* **109,400 of 109,400 (100.00 %)** `|w| = 0.5` vertices have a
  position-identical `|w| = 1.0` partner;
* **109,317 of 109,400 (99.92 %)** of those pairs have exactly negated normals;
* only **65.67 %** have exactly negated *tangents* — ★ **the back shell carries
  its own frame**, it is not a sign flip of the front one;
* every triangle appears twice, once per shell (7,302 × 2 on
  `64b4b5b2a0153f7e/obj000`, where the pair's tangents are 180.0° apart at
  p10 = median = p90).

⚠ **The buffer order is not part of the law.** 25 of the 26 lay it out
fronts-then-backs; one interleaves them. Read the tag, never the index.

⇒ **The shader needs the sign and nothing else.** The back shell's flipped frame
is already in that shell's own `normal` and `tangent` values, so a per-vertex
`sign(w)` reconstructs both sides correctly with no special case.

⛔ **A fifth value is refused loudly, not rounded.** An unknown state means the
2-bit reading is wrong for that asset and `sign(w)` would then be a guess. The
importer records the state histogram on the mesh as `le_tangent_w_states`, the
presence of a shell as `le_tangent_w_has_back_shell`, and any unknown value as
`le_tangent_w_unexpected`, then falls back to Blender's own tangent and says so.
⚠ It classifies the **distinct** values — there are four — not tens of thousands
of vertices one at a time; this runs on every object of every import.

### The TBN is rebuilt in shader nodes

⛔ **Blender will not accept an authored per-loop tangent anywhere.**
`mesh.loops[].tangent` is read-only and recomputed by mikktspace from the active
UV layer, `ShaderNodeNormalMap` has no tangent input, and `ShaderNodeTangent`
offers only radial axes and a UV map. So `mesh_builder` stores the shipped basis
as generic point attributes — `le_tangent` (`FLOAT_VECTOR`) and `le_tangent_w`
(`FLOAT`) — and `material_builder._shipped_tangent_normal` rebuilds the frame in
nodes, which is the only route by which an authored basis reaches a Blender
shader:

```
T   = normalize(object_to_world(le_tangent))
T'  = normalize(T − N · dot(N, T))            Gram-Schmidt against N
B   = cross(N, T') · sign(le_tangent_w)
n   = 2 · color − 1                           the same remap NormalMap does
out = normalize(T' · n.x + B · n.y + N · n.z)
```

`N` is `ShaderNodeNewGeometry`'s `Normal` — the **world-space** shading normal,
which is the custom split normal this importer set from the shipped `normal`
stream, so both legs of the frame come from the same source.

⚠ **The `OBJECT` → `WORLD` `ShaderNodeVectorTransform` on `T` is required and is
not decoration.** `le_tangent` is stored in mesh space — the vertex blobs stay
byte-faithful to disk and the Y-up → Z-up conversion lives on `ob.matrix_basis` —
while `Geometry.Normal` is world space. Without the transform the two legs sit in
different frames and the result is **silently wrong rather than visibly broken**.
The default axis matrix is a pure rotation (det +1) and carries the handedness
across unchanged; the diagnostic `mirror_axis` toggle is det −1 and *would* flip
it, which is exactly what that toggle already documents itself as.

⛔ **It degrades, it never blackens.** A mesh with no `le_tangent` reads
`(0, 0, 0)` from the Attribute node, and normalizing that yields a black surface.
The graph therefore keeps the `ShaderNodeNormalMap` leg as well and mixes to it
wherever `length(le_tangent) < 0.5` — it is a **mix, not a switch**. An object
that ships no tangent stream renders **exactly as it did before, per pixel**.

| option | default | meaning |
|---|---|---|
| `shipped_tangent` | **`True`** | wire the shipped basis. `False` restores Blender's mikktspace tangent through `ShaderNodeNormalMap` — what every render before 0.4.0 used. |

The material records which basis it received as `le_tangent_basis`
(`"shipped"` / `"mikktspace"`). Pinned by
`blender_tool/tests/test_shipped_tangent.py`; verified inside Blender, reading the
result back, by `blender_tool/tests/blender_tangent_probe.py`.

⚠ The shipped basis is also the prerequisite for the world-space SG5
normal-mapped lightmap sum described in [LIGHTING.md](LIGHTING.md), which is
decoded but not yet wired.

---

## Layer compositing

`layerN_blend_mask` gates every other channel of the same layer. Layer 0 is the
base and is never blended; layers 1..N are composited on in ascending order:

```
mask_amount = saturate(mask.R × blend_mask_scale + blend_mask_offset)
blend       = saturate((vertex_blend − height) / blend_fade) × mask_amount
result      = BlendValue(lower_layers, layer_i,
                         blend × <channel>_blend_alpha, blend_mode)
```

The authored `blend_mode` default is 6 (`eBlendTransparent`), which is a lerp
`(1 − m)·base + m·layer`. Every participating map defaults to the value that makes
its own term vanish, so with authored defaults the whole thing collapses to
`saturate(vertex_blend / fade) × saturate(mask.R × scale + offset)`.

The importer builds the mask term as `Math(MULTIPLY_ADD, use_clamp=True)` — which
*is* `saturate()` — and feeds it to a `ShaderNodeMix` per gated channel.

The **vertex-blend** term is still deliberately NOT built, but for one reason
now instead of two. `vertblend` is component (i−1) of the `float4 blend : COLOR1`
vertex stream (`shader-confirmed`). 0.4.0 **imports** that stream: `color1` is not
decoration, it is the engine's per-vertex layer-blend weight, it ships on **523 of
913 objects (57.3 %)** and it reached Blender on none of them — which is why layer
compositing here has only ever been mask-driven. It now arrives as a `color1`
point colour attribute. ⚠ What is still missing is permission to *use* it:
whether the shader samples it at all is the `use_vertex_blend_` permutation bit,
which is not on disk. So the data is imported and nothing is asserted about its
use; `le_mesh.materials` records that as `vertex_blend_applied`.

⚠ **Every shipped `layerN_blend_mask_offset` in the corpus is `-1.0`**, which makes
`saturate(mask.R × 1 + (−1))` zero for every possible texel: the layer contributes
nothing at rest. That parameter is animatable with a soft range of [−1, 1] and the
two region maps are weighted masks with animated per-slice weights, so this is a
**runtime state we cannot reproduce, not a decode bug**. The decode reports it
(`suppressed_at_rest`) rather than editorialising. Pass
`opts["layer_blend_mask_offset"] = 0.0` to see those layers at their authored-on
state — an override of a runtime-animated value, not a fudge factor.

<a id="dropped-layers"></a>
### ⛔ 19 of 44 audited materials drop an authored layer

An audit of 44 materials found **19 that drop a layer the artist authored**.

* **18 of the 19 are provably invisible** — the layer's blend mask is pinned at
  its OFF extreme by the `-1.0` offset above, so nothing that layer could
  contribute is capable of reaching the frame.
* **1 is not.** That one is a real, unexplained loss of authored content.

⛔ Do not read "18 provably invisible" as "19 harmless". The nineteenth is counted,
not explained, and it stays on this page until it is.

---

## `image.alpha_mode` — the silent albedo corruptor

Blender defaults an image to `alpha_mode = 'STRAIGHT'`, which **multiplies the RGB
by the alpha channel on load**. Measured on a `layer0_composite_diffuse` texel of
`6f51c495d957d59a.dds` (BC3_UNORM_SRGB): raw sRGB8 `(192,151,0,28)` came out of
the Image Texture `Color` socket as `(0.007499, 0.005182, 0)` under `'STRAIGHT'`
against a ground truth of `(0.527115, 0.309469, 0)` — **70× too dark**, and a texel
with `alpha == 0` came out pure black. `'CHANNEL_PACKED'` reproduced the DDS
bit-exactly.

Every texture in this data packs alpha as an independent signal, so the importer
sets `'CHANNEL_PACKED'` on all of them. The alpha channel is always **linear**,
even in an `_SRGB` format — an `_SRGB` view sRGB-decodes RGB only.

`'STRAIGHT'` is never emitted automatically: choosing it would need the
premultiplied-alpha / output-alpha shader permutation bits, which are not on disk.

---

## Emission arithmetic

```
Emission Color    = layerN_emissive_map  ×  layerN_emissive_tint_color
Emission Strength = layerN_emissive_intensity  ×  k_emissive_scale
```

**There is no unit-conversion constant. The scale factor is 1.0.** Both sides are
linear radiance multipliers; any residual difference is the engine's exposure and
tonemap, which this tool does not reproduce. Inventing a fudge factor here would be
wrong.

<a id="target-mapping"></a>
### Target mapping

| on-disk case | Blender |
|---|---|
| opaque `mattype`, `blendmode 0` | `surface_render_method = 'DITHERED'`, Alpha = 1 |
| `eMTAlphaTested` | hard cutout: `Math(GREATER_THAN, alpha, k_alpha_threshold)` → Alpha, `'DITHERED'` (EEVEE Next has no `CLIP` mode) |
| transparent `mattype`, `eBlendTransparent` | `'BLENDED'`, Alpha = the alpha chain |
| `eMTForwardTransparent` + `eBlendTranslucent` | ⛔ **NOT IMPLEMENTED** — see [above](#blend-translucent). The target is `'BLENDED'` + transmission tint: mix a Transparent BSDF coloured by `opacity_map × opacity_tint_color`. Do **not** use Principled Transmission — that is refraction, not this dual-source add. |
| additive / linear dodge | EEVEE has no additive blend; approximate with Emission + `'BLENDED'` + Alpha ≈ luminance. **Lossy — flag it.** |
| `eMTRefraction` | Principled `Transmission Weight = 1`, `IOR = k_refractive_index`, raytraced refraction |
| `eMTSkirt` / `eBlendSkirt` | the decal pass: `'BLENDED'` with the cut-out alpha, tagged `le_skirt`. ⚠ The **one** case where the resolved mode may legitimately differ from the manifest's — see [`eMTSkirt` is a decal sheet](#emtskirt-is-a-decal-sheet). |
| `eDoubleSided` | `use_backface_culling = False`. The geometry is genuinely single-sided, so **the setting is enough — do not duplicate or flip faces.** For blended glass also set `show_transparent_back = False` unless the mesh really is a closed shell. |

Verified against Blender 5.1's RNA: `blend_method` still exists but is a **legacy
alias** — `OPAQUE`, `CLIP` and `HASHED` all collapse to
`surface_render_method = 'DITHERED'`; only `BLEND` maps to `'BLENDED'`. The
importer therefore writes `surface_render_method` directly and **reads it back**,
recording the result on the material as `le_surface_render_method`. On Blender 4.1
and earlier, where only the alias exists, it falls back to it.

---

## Not derivable from disk

* Which compile options are active (alpha-test vs. dithered alpha-test,
  alpha-to-coverage, premultiplied alpha, output alpha, opacity, refraction, vertex
  colour, exposure-independent emissive) — all shaderset permutation bits.
* The runtime exposure value that exposure-independent emissives divide by.
* Distance and depth fades (view dependent).
* Whether an absent `k_*` prop means "default" or "not applicable" (assumed default).
* The sRGB-vs-linear treatment of the **alpha channel**: the engine gamma-converts
  every map alpha in one permutation, which Blender's Image Texture `Alpha` output
  does not. Matching it exactly needs a gamma node *and* knowing which permutation
  is in play.

---

<a id="cross-archive-resolution--read-this-before-trusting-a-package"></a>
## Cross-archive resolution — read this before trusting a package

Neither textures nor materials live in the archive that binds them:

* shadersets are **100 %** resident in the binding archive, but the **textures**
  they bind mostly are not — 88 of 115 bindings are external on archive
  `0703fd2acd5803e9`, and **31 of 31** on `4a405738bee7a74b`, which is exactly why
  that archive used to resolve zero texture roles;
* **materials** are only **~19 %** resident (24 of 127 bindings on
  `0703fd2acd5803e9`).

A resolver that assumes local fails **silently**: a missed texture is simply not
extracted, and a missed material falls back to `SGMaterialData` defaults, so an
`eMTForwardTransparent` material reads as `mattype 0` / `eBlendOpaque` and renders
opaque.

0.3.0 fixes this with two corpus-wide indexes, and 0.4.0 adds a third — the
[role index](#role-ladder). All three are **your data**, generated once from your
own game install into `$LONE_ECHO_SCAN_ROOT`:

```bat
python.exe scripts\le_texture_archive_index.py     :: texture_archive_index.tsv
python.exe scripts\le_material_archive_index.py    :: material_archive_index.tsv
python.exe scripts\le_role_index.py                :: role_index.tsv
```

With the texture index in place, the archive's own texture hashes are **unioned**
with it to form the needle set for the `SShaderInputData` scan: 60 bindings found
with local needles versus **212** with global ones on the reference archive,
exactly reproducing a precomputed scan TSV. With the material index in place,
non-resident materials are loaded from their home archive — grouped by home, one
archive at a time, each decompressed primary dropped before the next is opened,
and cached process-wide so an `--all` run does not re-open the same home archives
once per mesh-list.

With the role index in place, rung 3 of the [ladder](#role-ladder) can answer for
a texture no shaderset in the current archive names.

⛔ Dropping the texture gate entirely was measured and rejected: 1,884 bindings at
**89 % false positives**. Struct validation alone is not selective enough. ⚠ The
needle set matters much less than it used to — [RDEF](#rdef-is-the-binding-and-the-name-source)
names the bound textures outright and needs no needles — but the gate still
guards the `SShaderInputData` scan itself.

Without the indexes the extractor still runs, but it **says so, loudly**, and
names what is lost. Silent local-only degradation is the exact bug they exist to
fix.

Live (direct-from-archive) role resolution is now the **default**; on the
reference archive it reproduces a precomputed scan TSV exactly (212 bindings / 57
shadersets both ways) and, unlike a TSV, works on every archive without a prior
scan. `--tsv-materials` switches back to the TSV path.

---

## Auditing your own copy

```
python.exe blender_tool\tests\audit_material_modes.py --archive <hash>
python.exe blender_tool\tests\audit_material_modes.py --archive <hash> --tsv out.tsv
python3    blender_tool/tests/audit_material_modes.py --fixtures blender_tool/exports/fixtures_mat
```

`--fixtures` audits already-exported `.lemesh` packages instead of an archive: it
is pure stdlib, loads no archive and is safe to run any time.

Decodes every material in one archive and reports the `(mattype, blendmode)` joint
histogram, the flags histogram, and every `materialprop` resolved through an
embedded table of verified name preimages — plus every material whose alpha,
emissive or blend state is non-default. It loads one archive's primary stream and
frees it before returning; do not run two concurrently.
