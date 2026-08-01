# Lighting

**Status (0.3.0): the light importer ships, OFF BY DEFAULT, and imports only the
`eEnableDiffuse` subset. The baked lightmap is decoded but not yet auto-wired.**

`le_mesh/lights.py` decodes the `SGLightParams` table out of a level's scene
resource and converts each record into Blender light parameters, with unit tests.
`blender_tool/extractor/le_lights.py` writes those records to a `lights.json`
sidecar without decompressing a whole archive. `addon/lone_echo_import/
light_import.py` imports that sidecar as Blender lamps — but you have to ask for
it, and by default it drops the specular-only majority. Read the next section
before turning it on.

---

## ⚠ Importing these lights naively is wrong

Lone Echo is a **hybrid**, not a baked-only game and not a realtime-only one.
Importing the lights alone will not reproduce its look; importing the lightmaps
alone will not either. Every lit surface is lit by **both at once**.

Three measurements make the point:

* Of the shipped shaders, **86 of the 87 "lit surface" shaders bind both paths** —
  the baked ambient path (lightmap + AO + ambient specular cubemaps) *and* the
  realtime clustered-light path (clustered lights, light clusters, shadow map).
  **Zero** shaders are baked-only; one is clustered-only.
* Of 118 decoded light records, **only 49 (42 %) carry `eEnableDiffuse`, while 112
  (95 %) carry `eEnableSpecular`.** On one 47-light level only **15** carry
  `eEnableDiffuse`. The shader gates on exactly these bits.
* The bake dwarfs the lights on disk: 309 light records (about 108 KB) across 28
  shipped scenes, against 51 irradiance volumes carrying **101.8 MB** of spherical
  harmonic sample data — roughly **936× the bytes** — plus a BC6H HDR lightmap in
  every level archive.

**So slightly over half of Lone Echo's shipped level lights are specular-only.**
They exist to put moving highlights and eye-lights on surfaces whose *diffuse*
response is already baked into the lightmap and the irradiance volumes.

Blender has neither a "specular only" light nor that baked diffuse underneath. Add
these lights to a Blender scene and you **double-light** it: the baked diffuse the
lights were authored to sit on top of is simply absent, and the specular-only
lights start contributing diffuse they were never meant to contribute.

Measured: importing every light rather than the `eEnableDiffuse` subset is
**7.06× brighter** on identical receivers.

**The lightmap is not auto-wired yet.** Until it is, a naive light import makes
results *less* faithful, not more — which is why the importer is off by default
and, when on, defaults to the diffuse subset only.

---

## Using the light importer

**File > Import > "Lone Echo Lights (.json)"**, or headless:

```python
import lone_echo_import
lone_echo_import.import_lights("path/to/lights.json", bpy.context,
                               {"light_set": "diffuse"})
```

Options (`DEFAULT_OPTS` in `light_import.py`):

| option | default | meaning |
|---|---|---|
| `import_lights` | **`False`** | the whole importer is opt-in |
| `light_set` | `"diffuse"` | `eEnableDiffuse` only. `"all"` is an explicit opt-in that returns a warning and parks the specular-only lamps in a hidden child collection |
| `skip_disabled` | `True` | drop records with `eLightEnabled` clear |
| `hide_specular_only` | `True` | with `light_set="all"`, hide them rather than lighting with them |
| `y_up_to_z_up` | `True` | **the** axis basis — the same one the meshes use |
| `use_custom_distance` | `True` | clip at `attenuation.z` |
| `cycles_falloff_nodes` | `True` | build a Light Falloff node when `attenmethod != 2` (Cycles only; lossy in EEVEE) |
| `exposure_scale` | `1.0` | USER calibration. 1.0 is the raw unit conversion — not a fudge factor |
| `scene_filter` | `None` | import one scene by hash or name |

The lamp's world matrix is `A · T(pos) · R(orientation) · Rx(180°)`, where `A` is
the same Y-up→Z-up basis the meshes use and `Rx(180°)` is the lamp-forward flip
(the engine's light forward is local **+Z**; a Blender lamp emits along local
**−Z**). The alignment invariant the tests assert is
`M[:3,:3] · (0,0,−1) == blender_direction(rec)`.

Nothing undecodable is invented: `filtersize`, the cone `falloff` exponent, the
runtime range offset, `lightmask`/`scenemask`/`visindex`/`qualitylevel` and
`attenuation.w` all ride along as inert `le_*` custom properties.

---

## The baked lightmap

`le_mesh/lightmap.py` decodes `CGLightMapResourceWin7` — a compact
`[u32 count][count × 0x28]` array of five-`CSymbol64` rows (HDR colour, two AO
maps, two occlusion maps) — plus the join that says *which* lightmap resource a
given mesh indexes into, and `CGMeshData.lightmapindex` / `lmsliceindex` /
`numlobes`. `addon/lone_echo_import/lightmap_builder.py` can wire it onto a
material node graph.

Three things a user has to know:

* **The bake is SG5, not a single colour map.** The colour texture is a DX10
  texture ARRAY: `arraySize 65` against AO siblings of `arraySize 13`, and
  65 = 13 × 5. The engine indexes it `lightmapuv.z = lightmapuv.z * 5 + i`
  (i = 0..4), so the array is **13 lightmap pages × 5 spherical-gaussian lobes,
  page-major**: `slice = page * 5 + i`. Each page holds five radiance lobes in
  TANGENT space and the diffuse term is
  `Σ saturate(dot(lobe_dir_i, n_ts)) × (2/λ) × scale × lobe_i`.
* **Blender exposes only slice 0 of an array DDS**, measured on 5.1.1: the array
  file's pixels are bit-identical to split slice 0 and differ from every other
  slice. A mesh on page 7 would therefore silently render page 0's lobe 0. The
  importer splits the array itself — pure stdlib, one file per slice, cached next
  to the source.
* **Colour space is `Linear Rec.709`.** Blender 5.1.1 loads the DXGI-95
  `BC6H_UF16` DDS natively as a float image and its loader auto-assigns exactly
  that. `Linear Rec.709` and `Non-Color` are numerically identical under the stock
  OCIO config; `sRGB` is a silent double-gamma (the shipped brightest texel
  `(1.900, 2.014, 1.688)` comes back as `(4.397, 5.033, 3.339)`, ×2.31–2.50). We
  set it explicitly rather than trusting the default, and prefer `Linear Rec.709`
  over `Non-Color` so a non-default OCIO config still converts correctly.

⚠ **What is NOT wired:** the resource → mesh join is decoded and tested, and
`lightmap_builder` can build the node graph, but `material_builder` does not call
it automatically and the extractor does not yet write a lightmap block into the
`.lemesh` manifest. Driving it today means calling `wire_lightmap` yourself (the
in-Blender probes `tests/blender_lightmap_probe.py` and
`tests/blender_lightmap_render.py` do exactly that).

⚠ **What is still unresolved:** what the 5th colour slice per page is.
`CGMeshData.numlobes` reads 4 on every shipped mesh measured (1221/1221), so 5 is
`numlobes + 1`; the slot the colour map fills is a *lobe basis* and the engine's
basis enum offers both a 4-lobe and a 5-lobe spherical-gaussian option. Nothing
measured separates them. The semantics of the two BC5 AO maps are also not
decoded.

---

## Where the lights live

`CGSceneData` begins with a BVH tree, then the lights table, and its serializer
walks members in declaration order:

```
+0x000 SBVHTreeData                      bvhtreedata
+0x040 CTable<SGLightParams>             lights            <-- 352 B records
+0x078 CTable<SGVolumetricLightParams>   vlights           <-- 272 B records
+0x0b0 CTable<SGAtmosphericVolumeParams> avolumes
+0x0e8 CTable<C3Vector>                  dirlightdirections
+0x120 CTable<unsigned int>              dirlightindices
+0x158 CGIrradianceVolumesData           ivdata
```

so on disk the scene payload starts `[SBVHTreeData][u32 count][count × 352 B]…`.
The 352-byte stride is confirmed out of shipped bytes: the same walk continues past
the lights table on 28 shipped level scenes and lands byte-exactly on the following
`actors` table.

> ⚠ **Stride divergence.** The later Echo VR revision of `SGLightParams` is **360**
> bytes, not 352. `le_mesh.lights` exposes both as `STRIDE` and `STRIDE_R15` and a
> test asserts they differ, precisely so the two can never be confused.

There is **no per-actor dynamic-light component** in Lone Echo — the type that
would carry one appears zero times in every archive probed. Do not go looking
for it.

### Shipped inventory (28 scenes)

| | total | notes |
|---|---:|---|
| `lights` (`SGLightParams`) | **309** | in 20 of 28 scenes; up to 65 in one level |
| `vlights` (volumetric) | **1,048** | god-ray volumes, not lights |
| `dirlightdirections` | 16 | one primary sun proxy per outdoor/lit level |
| `ivdata` irradiance volumes | 51 | in 18 of 28 scenes; 101.8 MB of SH samples |
| `avolumes` (atmospheric) | 0 | never populated in the shipped corpus |
| lightmap resource | present in every level archive | BC6H HDR colour + BC5 AO pair + BC4 occlusion |
| reflection-probe resource | 1 per level archive | 23 probe boxes in one level, BC6H cubemap |

Over the 118 records decoded in full: **106 spot, 10 point, 2 directional.** Lone
Echo lights almost entirely with spots.

---

## `SGLightParams` — the 352-byte record

The whole grid decodes to sane values on 118/118 shipped records, the `SPad<4>` at
`0x154` is zero on 118/118, and the cross-field invariants below hold on 118/118.

| off | type | field | meaning |
|---|---|---|---|
| 0x000 | u32 | `options` | `ELightOptions` bitfield, see below |
| 0x004 | u32 | `lighttype` | 0 point, 1 spot, 2 directional |
| 0x008 | 3×f32 | `pos` | **world position** |
| 0x014 | 3×f32 | `primarycolor` | **linear HDR RGB with intensity pre-multiplied in** — there is no separate intensity float |
| 0x020 | 3×f32 | `secondarycolor` | `(1,1,1)` on 118/118 — unused by the forward path |
| 0x02c | 4×f32 | `attenuation` | `(1.0, midpoint, range, ?)` — `.z` is *the* range |
| 0x03c | 4×f32 | `orientation` | quaternion `(x,y,z,w)`; forward is local **+Z** |
| 0x04c | f32 | `fovy` | **full** spot cone angle, radians |
| 0x050 | f32 | `nearp` | shadow-map near plane |
| 0x054 | f32 | `farp` | shadow-map far plane, `== attenuation.z` on 118/118 |
| 0x058 | f32 | `filtersize` | shadow PCF filter width — **not** a light radius |
| 0x05c | 3×f32 | `direction` | normalised forward, redundant with `orientation` |
| 0x068 | 2×f32 | `penumbra` | `(cos θ_inner, cos θ_outer)`; `(-1,-1)` for non-spots |
| 0x070 | f32 | `falloff` | extra `pow(cos a, falloff)` cone weighting; 0 on 106/118 |
| 0x074 | f32 | `attenmethod` | **the exponent m in `1/dᵐ`** — a Maya-style decay rate (0/1/2/3) stored as a float |
| 0x078 | f32 | `bias` | shadow depth bias |
| 0x07c–0x084 | 3×f32 | `shadowfadestart` / `shadowfadeend` / `shadowthrottledist` | shadow LOD distances |
| 0x088 | 0x20 | `SFadeParams` | proximity/distant camera fade + `u32 fadetype` |
| 0x0a8–0x0b4 | 4×f32 | `shadowresolution`, `shadowoffsetscale`, `lightoffsetstart`, `lightoffsetdist` | the last two feed the shader's near-fade |
| 0x0b8 | 0x20 | `SLightShaftProps` | god-ray params + `u64 goboassetid` (**null on 118/118**) |
| 0x0d8 | f32 | `airlightminradius` | 0 on 118/118 |
| 0x0dc | u32 | `lightmask` | 16-bit set mask ANDed with the receiver's mask |
| 0x0e0 | u32 | `visindex` | visibility-system index; `0xffffffff` = none |
| 0x0e4 | u32 | `qualitylevel` | quality gate |
| 0x0e8 | u64 | `quantizer` | intensity-quantizer asset id; **null on 118/118** |
| 0x0f0 | 0x28 | `CSignalTransform` | animated-intensity signal; identity on 118/118 |
| 0x118 | 2×f32 | `shadowangularfade` | |
| 0x120 | u64 | `name` | light name hash |
| 0x128 | 0x28 | `SSceneSetMask` | scene-set visibility |
| 0x150 | u32 | `shadowqualitylevel` | |
| 0x154 | 4 B | `SPad<4>` | **zero on 118/118 — the layout alignment check** |
| 0x158 | u32 | `cachedjointidx` | `0xffffffff` on 118/118 (level lights are not joint-attached) |
| 0x15c | u32 | `jointoffsetidx` | `0xffffffff` on 118/118 |

### `ELightOptions`

```
0x000001 eEnableDiffuse        0x000200 eUseLightShaft        0x040000 eBakeOnlyIrradiance
0x000002 eEnableSpecular       0x000400 eUseLightShaftShadows 0x080000 eDontBakeIrradiance
0x000004 eCastShadows          0x000800 eUseFog               0x100000 ePrimaryDirLight
0x000008 eCastLevelShadows     0x001000 eBakeDirect           0x200000 eEyesOnlyLight
0x000010 eCastActorShadows     0x002000 eBakeIndirect         0x400000 eBakeShadow
0x000020 eLightTransparents    0x004000 eUseNonUniformFog     0x800000 eLightVolumetrics
0x000040 eLightOpaques         0x008000 eCastOpaqueShadows   0x1000000 eCastAllLevelShadows
0x000080 eLightParticles       0x010000 eCastAlphaTestShadows
0x000100 eLightEnabled         0x020000 eCastTransparentShadows
```

Coverage over 118 records: `eLightEnabled` 98 %, `eEnableSpecular` 95 %,
`eEnableDiffuse` **42 %**, `eCastShadows` 18 %, `eBakeIndirect` 10 %, `eBakeDirect`
**0 %**. The 98 % `eLightEnabled` is what makes these *runtime* lights rather than
bake-time authoring data.

### Cross-field invariants (118/118 unless noted)

1. `direction == R(orientation) · (0,0,1)` — max error 2.5e-07. Forward is local +Z.
2. `farp == attenuation.z`.
3. `attenuation.x == 1.0`.
4. `attenuation.y == (attenuation.x + attenuation.z) / 2` — derived, not authored.
5. `attenuation.w == attenuation.z` on **107/118**; the 11 exceptions differ in both
   directions, so `.w` is a separate quantity and remains **unresolved** (most
   plausibly the cull radius the shader calls MaxRange).
6. `2 · acos(penumbra.y) == fovy` on **106/106** spots; `penumbra == (-1,-1)` on
   12/12 non-spots.
7. `secondarycolor == (1,1,1)`, `quantizer` null, `goboassetid` null,
   `cachedjointidx` and `jointoffsetidx` both `0xffffffff`, `pad@0x154 == 0`.

---

## Placement — no transform join

`pos` and `orientation` are already the **final world transform**, in the same world
space as the static-instance scatter. On one level, all 47 scene lights fall inside
the bounding box of that level's 21,394 scatter instances, laid out in regular
runs down its main corridor; the only outlier is the primary directional light,
whose position is meaningless because the shader uses only its direction.

Corroborating: `cachedjointidx` / `jointoffsetidx` are `0xffffffff` on 118/118, so
no shipped level light is joint-attached.

So a light importer needs **no** transform resolution and no parent chain — this is
strictly simpler than the mesh placement path. Apply the same single basis the rest
of the tool uses (a pure +90° rotation about X, `(x,y,z) → (x,−z,y)`);
`le_mesh.lights.to_blender_vec` *is* that map, and a test pins it against
`scatter_reader.basis_matrix()` so the two can never drift.

Blender lamps point along local **−Z** while the game's forward is **+Z**, so orient
with `Vector((0,0,-1)).rotation_difference(Vector(direction_blender))`. Roll is
unconstrained and irrelevant: no shipped light has a gobo.

---

## Units

The engine's distance attenuation, as pseudocode:

```
atten(d) = clamp(1 / d**attenmethod - faderangeoffset, 0, 10000)
# faderangeoffset is a runtime constant chosen so atten(range) == 0
point / spot:  lightcolor = primarycolor * atten(d) * cone(θ) * visibility
directional:   lightcolor = primarycolor                  # no distance term
```

`primarycolor` is a linear HDR radiometric scale with the authored intensity
already folded in. Blender's point/spot lamp of power *P* watts and normalised
colour *C* produces irradiance `E = P·C / (4π d²)`. Equating the two:

| game | Blender | formula |
|---|---|---|
| `lighttype` 0/1/2 | `light.type` POINT / SPOT / SUN | direct |
| `pos` | `object.location` | `(x, −z, y)` |
| `orientation` / `direction` | object rotation | `Vector((0,0,−1)).rotation_difference((dx,−dz,dy))` |
| `primarycolor` | `light.color` | `primarycolor / max(primarycolor)` — **both sides linear, no sRGB transform** |
| `primarycolor` | `light.energy` (POINT/SPOT, W) | **`4π · max(primarycolor)`** |
| `primarycolor` | `light.energy` (SUN, W/m²) | **`max(primarycolor)`** |
| `fovy` | `light.spot_size` | **identical** — both are the full cone angle in radians |
| `penumbra` | `light.spot_blend` | `1 − acos(penumbra.x)/acos(penumbra.y)` — **approximate** |
| `attenuation.z` | `light.cutoff_distance` (+ `use_custom_distance`) | direct |
| `attenmethod == 2` | Blender's native falloff | exact, modulo the range offset |
| `attenmethod == 1` | Cycles `Light Falloff → Linear` node | 12 of 118 lights; EEVEE cannot do it |
| `eEnableDiffuse` / `eEnableSpecular` | *(no equivalent)* | see the warning above |

Worked example, one shipped light with `primarycolor = (12.9113, 14.8227, 16.0000)`:

```
max = 16.0
C   = (0.8070, 0.9264, 1.0000)
P   = 4π × 16.0 = 201.06 W
check at d = 1 m: P·C/(4π·1²) = 16.0·C = (12.911, 14.823, 16.000) == primarycolor ✓
```

A spot uses the same formula: Blender treats a spot's Power as the equivalent
full-sphere point-light power, so the cone masks rather than concentrates.

### The one systematic error: the range offset

The engine subtracts a constant so the attenuation curve reaches exactly 0 at the
range. Blender has no such term, and the constant is **not on disk** — it is
computed at runtime. A Blender light is therefore too bright in the outer half of
its range: at `d = range/2` the game is already 25 % dimmer than pure inverse-square,
and as `d → range` the game reaches 0 while Blender is still at `1/range²`. Setting
`use_custom_distance` + `cutoff_distance = range` clips the tail but does not fix
the shape.

### The spot ramp is approximate

The cone *edges* match exactly (`spot_size == fovy`), but the engine ramps with a
smootherstep **in cosine space** between outer and inner while Blender uses its own
curve. Only the edges match, not the ramp between them.

---

## Not derivable from disk

* **Light radius / `shadow_soft_size`.** No source-size field exists.
  `filtersize` is a shadow-map filter width in texels, not a physical radius; using
  it as one would be fabrication. Import as 0 (point source, hard shadows). The
  engine *does* have an area-light parameter block with a radius, but the scene
  resource has **no area-light table** — Lone Echo ships no area lights.
* **The `falloff` cone exponent** (non-zero on 12 of 118) — no Blender equivalent.
* **The range offset** — runtime-derived, see above.
* **`lightmask` / `scenemask` / `visindex` / `qualitylevel`** — per-receiver and
  per-scene-set gating with no Blender analogue. A light with `lightmask == 2`
  illuminates only objects whose receiver mask includes bit 1; import them all and
  you over-light.
* **The `eEnableDiffuse` / `eEnableSpecular` split** — Blender lights are always both.
* **Absolute exposure.** The game runs auto-exposure and tonemapping over these HDR
  values, so only *relative* light values are meaningful; a Blender render needs its
  own film exposure calibrated once per level.
* **The 1,048 volumetric lights** — god-ray hull volumes, not lights. No Blender
  equivalent short of a volume shader per hull.
* **`attenuation.w`** — see invariant 5.

---

## Using the decoder

```bat
python.exe blender_tool\extractor\le_lights.py <archive-hash> ^
    --out blender_tool\exports\<name>_lights.json
```

The extractor never decompresses a whole archive: it walks the compressed chunk
table and touches only the archive prelude, the header tables, four 4-byte probes
inside the scene, and the lights table itself. A 380 MB-uncompressed level primary
costs about five chunk decompressions.

The sidecar carries each record's raw game-space fields *and* a derived `blender`
block (location, direction, colour, energy, spot size/blend, cutoff distance). The
raw fields are authoritative; the derived block is this document's arithmetic
applied for you.

There is also an **archive-free** path that runs under plain `python3` and never
opens a primary — it re-serialises an already-decoded dump into the current
sidecar schema, pushing every record back through `encode_light`/`decode_light` so
the result is byte-consistent with the 352-byte grid:

```bash
python3 blender_tool/extractor/le_lights.py \
    --from-json <decoded-dump.json> --scene <scene-name> \
    --out blender_tool/exports/<name>_lights.json
```

Unit tests: `blender_tool/tests/test_lights.py` (the decoder and the units) and
`blender_tool/tests/test_light_import.py` (the sidecar, the selection policy, the
axis and the add-on's duplicated arithmetic). Both are archive-free, and their
fixtures are **constructed**, not extracted — this repository ships no game bytes —
so they lock the decoder's offset table and the unit arithmetic. The corpus
measurements quoted above live here, in the documentation, not in the test data.
Set `LONE_ECHO_LIGHTS_JSON` to a sidecar you extracted yourself and
`test_light_import.py` re-runs its invariants against your real data too.
