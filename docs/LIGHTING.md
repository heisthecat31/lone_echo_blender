# Lighting

**Status (0.4.0): the light importer ships, OFF BY DEFAULT, and imports only the
`eEnableDiffuse` subset. The resource-level baked lightmap wires automatically
once you supply the level atlas. The per-instance bake and the reflection probes
are decoded and are both opt-in.**

Lone Echo's lighting is three separate systems and this page covers all three, in
the order they matter: the **runtime lights**, the **baked ambient diffuse** (the
lightmap and the irradiance volumes), and the **baked ambient specular** (the
[reflection probes](#reflection-probes)). ⚠ Every lit surface uses all of them at
once — read [Importing these lights naively is wrong](#naive-import) before
turning anything on.

Evidence tags used below — `stream-confirmed`, `corpus-confirmed`,
`shader-confirmed`, `name-confirmed` / `name-only`, `engine-confirmed`,
`inferred` — are defined in [FORMATS.md](FORMATS.md#evidence-vocabulary).

`le_mesh/lights.py` decodes the `SGLightParams` table out of a level's scene
resource and converts each record into Blender light parameters, with unit tests.
`blender_tool/extractor/le_lights.py` writes those records to a `lights.json`
sidecar without decompressing a whole archive. `addon/lone_echo_import/
light_import.py` imports that sidecar as Blender lamps — but you have to ask for
it, and by default it drops the specular-only majority. Read the next section
before turning it on.

---

<a id="naive-import"></a>
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

⚠ **The lightmap wires only when you supply the level atlas**, because the atlas
is a *level* asset and not part of a `.lemesh` package. With no bake underneath,
a naive light import makes results *less* faithful, not more — which is why the
light importer is off by default and, when on, defaults to the diffuse subset
only. The two options are designed to be read together: `lightmap_mode="baked"`
(the default) zeroes the BSDF's own diffuse and specular, so scene lights change
nothing; `lightmap_mode="ambient"` leaves the BSDF intact so real lights still
light the surface, and ⛔ **double-counts unless only the `eEnableDiffuse` subset
is imported.**

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

Nothing undecodable is invented: `filtersize`, the cone `falloff` exponent,
`lightmask`/`scenemask`/`visindex`/`qualitylevel` and the shipped `attenuation`
vector all ride along as inert `le_*` custom properties. `attenuation.w` is no
longer among the *undecoded* ones — it is
[`maxfadedistance`](#attenuation-w) and it now drives
`le_faderangeoffset_runtime`, which is reported rather than applied because
Blender has no such term.

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
  page-major**: `slice = page * 5 + i`. The lightmap therefore does not hold
  irradiance directly — each page holds five radiance lobes in **tangent** space,
  and the engine's diffuse term (`DiffuseTermSG` over `kLobeDirsSG5`,
  `kLambdaSG5`, `kSG5Scale`) is
  `Σᵢ saturate(dot(kLobeDirsSG5[i], n_ts)) × (2 / kLambdaSG5) × kSG5Scale × lobeᵢ`
  (`shader-confirmed`). ⚠ With no normal map the tangent-space normal is
  `(0,0,1)`, which collapses the lobe weights to five constants — which is also
  why the [shipped tangent basis](MATERIALS.md#tangent-basis) is a prerequisite
  for getting this term right rather than flat.
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

**What IS automatic at 0.4.0:** the extractor writes a `lightmap` section into the
`.lemesh` manifest, the importer resolves the level atlas **once** per package
(not once per mesh — it is a 68 MB texture array), and `material_builder` builds a
per-page material variant wired by `lightmap_builder.wire_lightmap`.
`lightmap_mode` defaults to `"baked"`.

★ The wiring is split per **(material, page)** pair because the two axes genuinely
cross: the page comes from the per-**mesh** `CGMeshData.lmsliceindex` while
materials are shared per material key, and in one shipped pair `obj001` (page 3)
and `obj002` (page 6) share the material key
`ae4aa9ff9320fcb1__6eac75dad7fc016d` (`stream-confirmed`). Wiring that material
once would put one mesh on the other's bake. Only pages a mesh actually names are
materialised, so the cost is `keys × pages_used`, not `keys × 13`.

⚠ **The whole block is inert unless an atlas resolves.** The atlas is a *level*
asset — one 1024², `arraySize 65`, BC6H_UF16 DDS of about 68 MB per scene — and is
not part of a `.lemesh` package. Point at it with `lightmap_texture` /
`lightmap_dir`, or extract with `--lightmap-textures` to copy it into the package.
Nothing guesses: with no atlas resolved the path is a no-op and the summary's
`lightmap.reason` says why. `lightmap_mode="none"` returns the material completely
unchanged — no copy, no nodes, no custom properties.

⚠ With only one file available the basis falls back to `"single"` — lobe 0 alone,
visibly darker and directionally wrong, but a **defined, reported** fallback
rather than a lie.

⛔ `lightmap_use_ao` is **off** by default, and deliberately: the engine does not
multiply `ao0.R` into a lightmapped surface.

⚠ **What is still unresolved:** what the 5th colour slice per page is.
`CGMeshData.numlobes` reads 4 on every shipped mesh measured (1221/1221), so 5 is
`numlobes + 1`; the slot the colour map fills is a *lobe basis* and the engine's
basis enum offers both a 4-lobe and a 5-lobe spherical-gaussian option. Nothing
measured separates them. The semantics of the two BC5 AO maps are also not
decoded.

### The lightmap UV set is a SLOT, not a name

⛔ **`"uv1"` is the wrong answer and used to be hardcoded.** The lightmap UV set
is texcoord **semantic slot 4** — the engine's vertex shader reads
`vb_texcoord4` into `vsinput.lightmapuv`, specifically that slot
(`shader-confirmed`). On disk the slot is
`CGVertexFormat::SVertexElement`'s `uint8 slot` field, and it is decoded into
every `.lemesh` manifest's `raw_vertex_format`.

The `uvN` names in a package are **appearance order**, not slot numbers. `uv1`
means "the second texcoord element present"; it coincides with the lightmap set
only when an object's texcoord slots happen to be `(0, 4)`. Over the 913 objects
in the reference exports (`corpus-confirmed`):

| resolved lightmap attribute | objects |
|---|---:|
| `uv1` | 861 |
| **`uv2`** | **64** |
| **`uv3`** | **29** |

⛔ The hardcoded literal was wrong on **93 of 913 objects** — on those it sampled
the material's *second texture* UV set as if it were the bake. Resolution now runs
in this order, and `lightmap_builder._lightmap_uv_of` mirrors
`le_mesh.package.lightmap_uv_for_manifest_object` exactly:

1. `obj["lightmap_uv"]`, written by the current extractor;
2. otherwise resolved from `obj["raw_vertex_format"]`, which **every `.lemesh`
   manifest ever written** carries — so old packages need no re-extraction;
3. otherwise `""`.

⚠ `""` is also the answer for a mesh with **no slot-4 texcoord at all**. Such a
mesh has no lightmap UV set, and substituting another one is precisely the bug.
Corroborating: the slot-4 element is `eU16n` on 504/504 corpus objects that carry
texcoords — recorded, never primary; the slot is what the engine indexes. Pinned
by `blender_tool/tests/test_lightmap_uv_slot.py`.

<a id="instance-lightmap"></a>
### Instanced statics are baked per INSTANCE, not per mesh

★ A `.lescatter` level's lightmap does **not** live on the shared mesh. Each
static-instance record is a fixed 44-byte `SGPackedInstanceData` header followed
by the instance's **own** per-vertex lightmap UVs, so the record stride is
`44 + 8·nverts` (`stream-confirmed`):

```
+0x00 pos          C3Vector     3× f32, world
+0x0C orientation  C4VectorS16N 4× int16 snorm (x,y,z,w) unit quat
+0x14 scale        C3HVector    3× f16
+0x1A lightmapidx  uint16       <- the PER-INSTANCE lightmap PAGE
+0x1C probeidx_lmask_dlmask     uint32
+0x20 color        C4HVector    4× f16
+0x28 lodfadeidx   uint32
+0x2C lightmapuvs  C2Vector[nverts]   8 B/vertex, per-instance per-vertex
```

The stride arithmetic is what proves it: on `942c829457a04a62` (station_front)
meshes 0/1/468 have strides 900/636/844, each exactly `44 + 8·nverts`, and
`44·C + 8·Σ(count·nverts)` reproduces `instancedatasize` with **residual exactly
0** on that level and on `4c47d84c1e52447a` as well.

⛔ **Instancing cannot survive a bake.** Two consequences, both measured:

* **1046 of one level's 1050 per-mesh `uv1` blobs are entirely zero** — the engine
  overrides that slot per instance, so on this path `uv1` is simply not the
  lightmap UV set;
* instances of the same mesh carry **different** UVs, so a lightmapped static
  instance cannot ride a shared Blender mesh datablock;
* the per-instance page and the per-mesh `CGMeshData.lmsliceindex` **disagree for
  13,909 of station_front's 21,394 instances (65.1 %)**. ⛔ The instance wins.

Extracted only behind `--instance-lightmap` (~52 MB on station_front) as
`.lescatter` package version 5 — four fixed blobs parallel to
`blobs/instances.bin`; see [FORMATS.md](FORMATS.md). The section is written as
`{"present": false, "reason": …}` when the flag is off, so a consumer can always
tell "not extracted" from "not available".

⚠ **Import is opt-in too** (`instance_lightmap`, default off), and turning it on
**spends the instancing**: every lightmapped instance gets its own mesh datablock
and its own lightmap UV layer. That is a design call, not an oversight — Blender
cannot express "one shared mesh plus a per-instance UV *set*", because a UV set is
per-loop mesh data and every object sharing a datablock sees the same loops. The
only per-object channel a shader can read is a custom property, which the
`Attribute` node exposes as **one** value per object — enough for a scalar or a
vector, not for `nverts` distinct UV pairs. So the choice is between copying the
datablock and getting the bake wrong. With the option off the path is
byte-identical to the pre-existing one: no atlas resolved, no datablock copied, no
material varied.

⚠ A per-object **affine** (scale + offset) attribute *would* be enough if every
instance's UV set were the same chart placed at a different atlas rect. Sampled
deltas of 12 and 16 texels say the strips are not all the same width, so at best
it is scale-plus-offset and at worst it is neither. That remains `inferred` on the
real stream and nothing here assumes it.

⛔ **Do not port the later Echo revision's model here.** That era packs a 48-byte
record and a 4-byte `C2VectorU16N` UV held in a separate offsets-indexed buffer
(`k_instancelightuvs` / `lmuvoffsetidx`), which does not exist in this one. Never
carry one era's formula to the other.

---

<a id="reflection-probes"></a>
## Reflection probes — the ambient specular term

`CGReflectionProbeResourceWin7` is the lightmap's sibling: the lightmap bakes the
ambient **diffuse**, the probes bake the ambient **specular**.
`blender_tool/le_mesh/reflection_probe.py` decodes it (pure stdlib, no archive, no
`bpy`) and `addon/lone_echo_import/probe_builder.py` does the Blender half.

**Coverage:** `CGReflectionProbeResourceWin7` appears **94 times across 90
archives**, and its `…Win7GPU` sibling the same 94/90; four archives carry two
(`corpus-confirmed`). The resource is addressed by the scene's **own name hash** —
`CGSceneData` stores no id for it — the same sibling-by-name convention the
lightmap uses; `CGameLevelResourceWin7 == CGReflectionProbeResourceWin7` in 90 of
90 level archives.

### What is on disk

`SReflectionProbeMetaData` is the **unpatched `CTable` memory image**: six
`CTableA<T,0>` records of `0x38` bytes, then two `u32`, then every table's payload
back-to-back in declaration order. Residual **0**.

```
+0x000  CTableA<SGProbeBox,0>          boxes
+0x038  CTableA<SGProbeSphere,0>       spheres
+0x070  CTableA<SGProbePoint,0>        points
+0x0a8  CTableA<unsigned int,0>        mipcounts
+0x0e0  CTableA<SGProbeBoundingBox,0>  boundingboxes
+0x118  CTableA<unsigned int,0>        gpuoffsets
+0x150  u32                            gpumemsize
+0x154  u32                            textureformat   (NRadEngine::ETextureFormat)
+0x158  payloads, in declaration order, no padding
```

Each `CTableA<T,0>` image carries a null data pointer (unpatched on disk), an
`nbytes` payload size and an `iused` element count, so `nbytes // iused` is a
**measured** element stride rather than an assumed one — which is how
`SGProbeBoundingBox` was pinned at `0x98` (the engine's declaration prints
`C4Vector[80] normalizations`, which would make the struct `0x548`; the shipped
stride says the `[80]` is a **byte** length, i.e. 20 floats).

| table | stride | contents |
|---|---|---|
| `SGProbeBox` | `0x38` | `CQuaternion invrot`, `C3Vector pos`, local `CBox` symmetric about `pos`, `u32 probeidx` |
| `SGProbeSphere` | `0x14` | ⚠ **0 shipped anywhere in the corpus** — `name-only`, never `stream-confirmed` |
| `SGProbePoint` | `0x10` | `C3Vector point` + `u32 probeidx`; one per probe, and `probeidx == row index` on every shipped resource measured |
| `SGProbeBoundingBox` | **`0x98` measured** | `C33Matrix rotation` (orthonormal, rowlen 1.0000), `C3Vector probepos` (`== points[i].point`), OBB `min`/`max` relative to `probepos`, `float[20] normalizations` |
| `gpuoffsets` | `0x04` | byte offset of each probe's cube inside the paired GPU slice; `gpuoffsets[-1] + stride == gpumemsize == the GPU entry's size` |

⛔ **`boxes` is not the probe count.** On station_front `942c829457a04a62` the
resource holds **23 boxes over 16 probes**; `points`, `mipcounts`,
`boundingboxes` and `gpuoffsets` all read 16 and the GPU slice is 16 cubes.
Several boxes share one probe (probe 12 ×5, probe 13 ×10, probe 14 ×5, probes
0/1/15 ×1). "23 probe boxes" is a count of **selection volumes**.

`CGMeshData.probeidx` at `+0x50` says which probe a mesh reflects;
`0xffffffff` means none.

**The OBB is one shared volume.** `min[i] + R·probepos[i]` is *constant across
probes* on station_front — 15 of the 16 share one `R` and one constant, and the
16th (the exterior/vista probe) has its own. So `min`/`max` are a single
world-space oriented box expressed in each probe's own rotated, probe-relative
frame: the classic box-projection (parallax) volume. The measurement is
`stream-confirmed`; the runtime *use* is `inferred` — the decoder reports the box
and the importer does not apply it.

**`normalizations`** is 20 floats of which exactly `mipcounts[i]` are non-zero, at
**even** indices `0, 2, 4, … 2·(mipcount−1)`; index 1 duplicates index 0 and every
other odd slot is 0. Values fall monotonically from mip 1 onward and exceed 1.0
often (16.0 observed) — the shape of a per-mip radiance scale for a pre-normalised
BC6H prefilter. ⚠ The layout is measured; the meaning is `inferred`, so
`mip_normalizations()` is **offered and not applied**.

### The GPU payload, and what Blender can do with it

The paired GPU resource is a **BC6H_UF16 cube array**: `gpumemsize` bytes, one
cube per probe, `gpuoffsets[i]` bytes in, face-major with a full mip chain per
face. The shipped cube is **256² with 9 mips** (`mipcounts == 9` on 60/60 shipped
probe sets).

⛔ **Blender has no cube-texture image type.** `bpy.data.images.load` on a DX10
cubemap DDS yields a `dim × 6·dim` vertical strip of the six faces' **mip 0** and
nothing else of the cube is reachable (`engine-confirmed`, Blender 5.1.1) — and
byte-for-byte the same pixels `reflection_probe.cube_strip_bytes` produces by
hand, which is what pins the face/mip arithmetic. So the importer resamples that
strip to an equirectangular float image and drives a `ShaderNodeTexEnvironment`
from `Texture Coordinate → Reflection`, added as an emission weighted by
`gloss² × Fresnel × intensity` so a `roughness == 1` surface is provably
unchanged.

⚠ Blender's DDS reader returns the image with row 0 at the **bottom**, so a cube
DDS is exposed as face 5 at the bottom of the buffer with each face's rows
reversed (`engine-confirmed`). Flip that and every reflection is upside down *and*
face-swapped.

The honest limits, none of them worked around:

* ⛔ **No roughness-dependent prefilter.** The engine samples the mip chain and
  scales it by the per-mip `normalizations`; only mip 0 reaches a material, so the
  wired reflection is always the sharp one and dims with roughness only through
  the `gloss²` weight. **Mips 1–8 are on disk, decoded, written to DDS, and
  unwired** — that is the whole reason the probe exists, and it is the largest
  open gap on this page.
* ⛔ **No box projection / parallax.** The shared OBB is decoded and reported as
  `obb_min_world` / `obb_max_world`, and not applied.
* ⛔ **No F0 from the material.** The Fresnel factor is a plain dielectric Schlick
  term at IOR 1.45, not the material's own specular colour.

| option | default | meaning |
|---|---|---|
| `probe_mode` | **`"off"`** | `"specular"` opts in. Off by default, like every other new light path. |
| `probe_equirect_width` / `_height` | `512` / `256` | the equirect the 256²×6 strip is resampled to — deliberately no upsampling |

Provenance lands on the material as `le_probe_index`, `le_probe_mode` and
`le_probe_file`. Pinned by `blender_tool/tests/test_reflection_probe.py` and
`blender_tool/tests/test_probe_builder.py`; verified in Blender by
`blender_tool/tests/blender_probe_probe.py`.

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
| reflection-probe resource | 94 resources / 90 archives | ⛔ boxes are selection volumes, **not** probes: 23 boxes over **16** probes on one level. See [Reflection probes](#reflection-probes). |

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
| 0x02c | 4×f32 | `attenuation` | `(1.0, midpoint, range, maxfadedistance)` — all four components are accounted for; see [`attenuation` in full](#attenuation-w) |
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
5. `attenuation.w == attenuation.z` on **107/118**; the **11** exceptions differ in
   both directions, which is what proves `.w` is a separate quantity — see
   [`attenuation` in full](#attenuation-w). It is `maxfadedistance`, **not** a
   second cull radius; that guess is retired.
6. `2 · acos(penumbra.y) == fovy` on **106/106** spots; `penumbra == (-1,-1)` on
   12/12 non-spots.
7. `secondarycolor == (1,1,1)`, `quantizer` null, `goboassetid` null,
   `cachedjointidx` and `jointoffsetidx` both `0xffffffff`, `pad@0x154 == 0`.

<a id="attenuation-w"></a>
### `attenuation` in full — all four components

★ **All four components are accounted for** (`shader-confirmed`).

| component | meaning |
|---|---|
| `.x` | authored inner stop of the volume-light ramp — hence the `== 1.0` invariant |
| `.y` | its midpoint, `(.x + .z)/2` — derived, not authored |
| `.z` | **the range**: a hard cull (a receiver past it is skipped outright) and the light-offset normaliser. `== farp` on 118/118, which is why it is also the shadow far plane |
| `.w` | **`maxfadedistance`**: the argument the attenuation curve's zero-offset is computed from |

The engine's own attenuation function takes the distance, the decay exponent and
`maxfadedistance` — and is passed `attenuation.w`, not `.z`:

```
offset = 1 / pow(abs(maxfadedistance), attenmethod)
atten  = 1 / pow(distance, attenmethod) − offset
if attenmethod == 0:  atten = saturate(1 − distance / maxfadedistance)
return min(atten, 10000)
```

The runtime agrees structurally: `SGForwardLight` carries `MaxRange` and
`FadeRangeOffset` as two **independent** fp16 values packed into one word, so the
range and the offset source were never the same field.

⚠ **Era caveat.** The shader corpus that settles this is the later Echo dev build,
not Lone Echo's own. Lone Echo **corroborates rather than proves** it: `.w == .z`
on 107/118 shipped records, which is exactly what an artist leaving the fade at
the range produces, and **the 11 that differ** (e.g. `.z = 1000`, `.w = 5000`)
are the ones the distinction exists for. Those 11 are the only shape in the
shipped data that can tell the two fields apart.

`.x`/`.y`/`.z` are additionally the three stops of the engine's volume-light ramp,
which is what makes invariants 3 and 4 the invariants they are — an authored inner
stop and its midpoint, not padding.

The importer keeps `cutoff_distance` on `.z` (the cull radius) and derives
`le_faderangeoffset_runtime = 1 / maxfadedistance^attenmethod` from `.w`. See
[the range offset](#the-range-offset) for why the offset is reported and not
applied.

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
# faderangeoffset = 1 / maxfadedistance**attenmethod   <- attenuation.w, NOT .z
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
| `attenuation.z` (the range) | `light.cutoff_distance` (+ `use_custom_distance`) | direct |
| `attenuation.w` (`maxfadedistance`) | *(no equivalent)* | carried as `le_attenuation_maxfadedistance`; feeds `le_faderangeoffset_runtime` |
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

<a id="the-range-offset"></a>
### The one systematic error: the range offset

The engine subtracts a constant so the attenuation curve reaches exactly 0 at
`maxfadedistance`. The constant itself is computed at runtime, but its **argument
is on disk** — it is `attenuation.w` — so the importer can and does derive
`faderangeoffset = 1 / maxfadedistance^attenmethod` and carries it as
`le_faderangeoffset_runtime`.

⛔ **Deriving it does not let it be applied.** Blender's lamp has no such term, so
a Blender light stays too bright in the outer half of its range: at
`d = range/2` the game is already 25 % dimmer than pure inverse-square, and as
`d → range` the game reaches 0 while Blender is still at `1/range²`. Setting
`use_custom_distance` + `cutoff_distance = range` clips the tail but does not fix
the shape. The divergence is reported per lamp as
`le_brightness_vs_game_at_half_range` (1.333 in the physical case, 1.0 for SUN)
rather than being silently absorbed.

⚠ On the **11 of 118** lights where `.w != .z`, the fade and the cull are at
different distances, so the offset must be computed from `.w` while the clip stays
on `.z`. Using one field for both is the bug this distinction exists to prevent.

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
* **The range-offset *term*** — its argument `attenuation.w` is on disk and the
  offset is derived from it, but Blender's lamp has nowhere to apply it. See
  [the range offset](#the-range-offset).
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
* **The reflection probes' roughness prefilter and box projection** — decoded,
  written to DDS, and unwireable: Blender has no cube-texture image type. See
  [Reflection probes](#reflection-probes).
* **The per-instance bake on instanced statics** — extractable behind
  `--instance-lightmap`, but ⛔ instances of one mesh carry different UVs, so it
  cannot ride a shared Blender mesh datablock. See [above](#instance-lightmap).

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
axis and the add-on's duplicated arithmetic). The rest of this page is pinned by
`test_lightmap.py` / `test_lightmap_resource.py` / `test_lightmap_wiring.py`
(the bake), `test_lightmap_uv_slot.py` (the slot-4 rule),
`test_instance_lightmap_extract.py` / `test_instance_lightmap_import.py` (the
per-instance stream) and `test_reflection_probe.py` / `test_probe_builder.py`
(the probes).

Every one of them is archive-free and their fixtures are **constructed**, not
extracted — this repository ships no game bytes — so they lock the decoders'
offset tables and the arithmetic, and nothing more. ⚠ A synthetic fixture
exercises the parse; it is **not** evidence about the real bake. The corpus
measurements quoted above live here, in the documentation, not in the test data.
Set `LONE_ECHO_LIGHTS_JSON` to a sidecar you extracted yourself and
`test_light_import.py` re-runs its invariants against your real data too.
