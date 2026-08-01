# Materials

**Status (0.3.0): the `.lemesh` path is wired end to end. Base colour, normal,
roughness/AO, specular/F0, alpha, transmission tint, emission, layer blend masks
and the render pass all reach EEVEE. The `.lescatter` path still does not — its
sidecar carries only base colour and normal.**

Read [What actually reaches EEVEE](#what-actually-reaches-eevee) for the exact
split before assuming any import is full PBR.

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
breaks**. Seven are fixed in 0.3.0; the two that remain are both on the
`.lescatter` (whole-level scatter) path.

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

⚠ One thing 0.3.0 does *not* claim: an end-to-end run against real archives. The
extractor changes below are covered by unit tests and by reasoning about measured
counts, not by a re-export of the fixture corpus.

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
*is* `saturate()` — and feeds it to a `ShaderNodeMix` per gated channel. The
**vertex-blend** term is deliberately NOT built: it is component (i−1) of the
second vertex colour stream, which `mesh_builder` does not import today, and
whether it is sampled at all is a shader permutation bit that is not on disk.

⚠ **Every shipped `layerN_blend_mask_offset` in the corpus is `-1.0`**, which makes
`saturate(mask.R × 1 + (−1))` zero for every possible texel: the layer contributes
nothing at rest. That parameter is animatable with a soft range of [−1, 1] and the
two region maps are weighted masks with animated per-slice weights, so this is a
**runtime state we cannot reproduce, not a decode bug**. The decode reports it
(`suppressed_at_rest`) rather than editorialising. Pass
`opts["layer_blend_mask_offset"] = 0.0` to see those layers at their authored-on
state — an override of a runtime-animated value, not a fudge factor.

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
| `eMTForwardTransparent` + `eBlendTranslucent` | `'BLENDED'` + transmission tint: mix a Transparent BSDF coloured by `opacity_map × opacity_tint_color`. Do **not** use Principled Transmission — that is refraction, not this dual-source add. |
| additive / linear dodge | EEVEE has no additive blend; approximate with Emission + `'BLENDED'` + Alpha ≈ luminance. **Lossy — flag it.** |
| `eMTRefraction` | Principled `Transmission Weight = 1`, `IOR = k_refractive_index`, raytraced refraction |
| `eMTSkirt` | no Blender equivalent; import opaque and tag |
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

0.3.0 fixes this with two corpus-wide indexes. They are **your data**, generated
once from your own game install into `$LONE_ECHO_SCAN_ROOT`:

```bat
python.exe scripts\le_texture_archive_index.py     :: texture_archive_index.tsv
python.exe scripts\le_material_archive_index.py    :: material_archive_index.tsv
```

With the texture index in place, the archive's own texture hashes are **unioned**
with it to form the needle set for the `SShaderInputData` scan: 60 bindings found
with local needles versus **212** with global ones on the reference archive,
exactly reproducing a precomputed scan TSV. With the material index in place,
non-resident materials are loaded from their home archive — grouped by home, one
archive at a time, each decompressed primary dropped before the next is opened,
and cached process-wide so an `--all` run does not re-open the same home archives
once per mesh-list.

⛔ Dropping the texture gate entirely was measured and rejected: 1,884 bindings at
**89 % false positives**. Struct validation alone is not selective enough.

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
