# Materials

**Status: partly wired. Base colour and normal maps reach the renderer.
Transparency and emission do not reach it at all.**

That is not a caveat about quality — it is the literal state of the pipeline. Read
[What actually reaches EEVEE](#what-actually-reaches-eevee) before assuming a
`.lemesh` or `.lescatter` import is full PBR. It is not.

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

The importer does not yet split them this way — it binds
`composite_components` whole to Roughness. Correct would be Roughness = `.R²`,
AO = `.G`.

---

## What actually reaches EEVEE

An end-to-end audit of the decoder → manifest → builder → EEVEE chain found **nine
breaks**, and the observable consequence is blunt: **every exported `.lemesh`
manifest carries `"materials": []`**, and the `.lescatter` manifest has no
`materials` key at all. No transparent or emissive material has ever actually
reached the renderer through this pipeline. The EEVEE render harness renders
channels that are, today, never populated for transparency or emission.

| # | where | break |
|---|---|---|
| **B1** | `addon/lone_echo_import/material_builder.py` | **`spec["alpha"]` is never applied.** The Principled `Alpha` socket is touched only when an *opacity texture* exists, so a material with `k_alpha = 0.25` and no opacity map renders fully opaque. |
| **B2** | `material_builder.py` | `mat.blend_method = "CLIP"` silently resolves to dithered on Blender 4.2+. The code path can **never** produce true alpha blending, so transparent/translucent glass renders as stochastic dither. |
| **B3** | `material_builder.py` | `blend_mode` and `mattype` are carried in the spec and **never read**. No pass or blend decision is made at all. |
| **B4** | `le_mesh/material_scalars.py` | Emissive intensity is taken from `layer0` unconditionally, but the emissive *map* may be on another layer (see below). |
| **B5** | `scripts/le_scene_materials.py` | `classify_roles()` computes `opacity` and `emission` channels, then only `base_color` and `normal` are read; the sidecar has no `alpha`, `blend_mode`, `emissive_*` or opacity/emissive texture, and only base-colour + normal DDS are extracted. |
| **B6** | `addon/lone_echo_import/scatter_import.py` | The spec built from the sidecar carries only `base_color`, `normal`, `base_color_factor`, `double_sided`. Everything transparency- or emission-related is dropped before `build_material` is called. |
| **B7** | the `.lescatter` manifest | It has **no `materials` array**; meshes carry `matidx`/`shdidx` with nothing to resolve them against inside the package. The `_materials.json` sidecar is a separate, lossy schema. |
| **B8** | `le_mesh/materials.py` | The fabricated role names above — fixed in 0.2.0, listed here because it is one of the nine. |
| **B9** | consumers of the role table | Anything that resolved those ten hashes got a confident wrong answer — also fixed in 0.2.0. |

B8 and B9 are fixed. **B1–B7 are not.** Fixing them is the obvious next piece of
work and it is mostly plumbing: the decoders already produce the values, and the
schemas drop them.

### The emissive-layer bug (B4), with numbers

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

### Roles the table still misses or misroutes

| role | today | should be |
|---|---|---|
| `layerN_composite_diffuse` **.a** | base colour RGB only | base colour **+ Alpha from `.a`** |
| `layer0_alpha_map` | absent → `unknown_s{slot}` | Alpha (multiplier) |
| `layerN_opacity_map` | Alpha | transmission tint (a Transparent BSDF colour) |
| `layer0_secondary_emissive_map` | absent | second emissive multiplier |
| `layerN_composite_components` | Roughness (whole RGB) | Roughness = `.R²`, AO = `.G` |

---

## Emission arithmetic, for when B1–B7 are fixed

```
Emission Color    = layerN_emissive_map  ×  layerN_emissive_tint_color
Emission Strength = layerN_emissive_intensity  ×  k_emissive_scale
```

**There is no unit-conversion constant. The scale factor is 1.0.** Both sides are
linear radiance multipliers; any residual difference is the engine's exposure and
tonemap, which this tool does not reproduce. Inventing a fudge factor here would be
wrong.

### Target mapping

| on-disk case | Blender |
|---|---|
| opaque `mattype`, `blendmode 0` | `surface_render_method = 'DITHERED'`, Alpha = 1 |
| `eMTAlphaTested` | hard cutout: `Math(GREATER_THAN, alpha, k_alpha_threshold)` → Alpha, `'DITHERED'` (EEVEE Next has no `CLIP` mode) |
| transparent `mattype`, `eBlendTransparent` | `'BLENDED'`, Alpha = the alpha chain |
| `eMTForwardTransparent` + `eBlendTranslucent` | `'BLENDED'` + transmission tint: mix a Transparent BSDF coloured by `opacity_map × opacity_tint_color`. Do **not** use Principled Transmission — that is refraction, not this dual-source add. |
| additive / linear dodge | EEVEE has no additive blend; approximate with Emission + `'BLENDED'` + Alpha ≈ luminance. **Lossy — flag it.** |
| `eMTRefraction` | Principled `Transmission Weight = 1`, `IOR = k_refractive_index`, raytraced refraction |
| `eMTSkirt` | no Blender equivalent; import opaque and tag |
| `eDoubleSided` | `use_backface_culling = False`. The geometry is genuinely single-sided, so **the setting is enough — do not duplicate or flip faces.** For blended glass also set `show_transparent_back = False` unless the mesh really is a closed shell. |

Verified against Blender 5.1's RNA: `blend_method` still exists but is a **legacy
alias** — `OPAQUE`, `CLIP` and `HASHED` all collapse to
`surface_render_method = 'DITHERED'`; only `BLEND` maps to `'BLENDED'`.

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

## Auditing your own copy

```
python.exe blender_tool\tests\audit_material_modes.py --archive <hash>
python.exe blender_tool\tests\audit_material_modes.py --archive <hash> --tsv out.tsv
```

Decodes every material in one archive and reports the `(mattype, blendmode)` joint
histogram, the flags histogram, and every `materialprop` resolved through an
embedded table of verified name preimages — plus every material whose alpha,
emissive or blend state is non-default. It loads one archive's primary stream and
frees it before returning; do not run two concurrently.
