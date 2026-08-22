# Echo VR baked lighting — the shipped shading model

Recovered from the engine's own HLSL (`core/shaders`), then confirmed against
the shipped resources. Nothing here is inferred from appearance.

## Status in our extractor

**SH4 is implemented and verified.** `evr_apply_lighting` exports each page's
four raw coefficient slices, and the add-on evaluates them per pixel against the
world normal via the `EVR_SH4_Irradiance` node group. That group was checked
against a direct transcription of the HLSL below over six cases spanning zero
DC, strong +/- directional terms and real arena values: **worst absolute error
2.4e-07**, i.e. float precision.

**SG5 is NOT implemented.** SG5 levels still take the old collapse, which sums
the five lobes with fixed weights and never transforms them by
`tangenttoworld`. That is wrong for the same reason the SH4 collapse was wrong
-- the basis is directional and hemispherical, so it cannot be resolved ahead of
shading. Affected: `mpl_lobby_b2`, `mpl_tutorial_lobby`, the `mpl_combat_*`
maps. `mpl_arena_a` and the tutorial maps are SH4 and are correct.

### What the SH4 path still approximates

| gap | cost |
|---|---|
| BC6H decoded at 8 bit (`texture2ddecoder.decode_bc6` returns BGRA8) | HDR above 1.0 is clamped; measured **0.0052%-0.69% of texels per page** |
| `EvalSH4PrefilteredSpecular` not implemented | baked specular missing; diffuse only |
| `k_punctual_occlusion_map` / `k_dirlight_occlusion_map` decoded but unused | baked shadowing of dynamic lights not applied |
| geometry with no bake at all | see below |

### Geometry the bake does not cover

`CGStaticInstanceResource` binds lightmaps **only to static-instanced
entities**. On `mpl_arena_a` that is 732 entities -> 2598 package instances, of
which 2100 are lit. Everything else -- actor-placed props and the level's own
9-submesh base mesh -- has no baked data in the level at all, and per-mesh
`CGMeshData` bindings are empty here (only 7 of 123 models carry any, all at
rows 0/1, not this level's row 10). Those objects are lit in a DCC only by
importing the authored rig (untick *Dynamic Lights Only*, 136 static lights).

## The resource

One `CGLightMapResourceWin10` per level, keyed by the level hash, 28–748 bytes.
It is a binding table, not pixels: a run of `0xFFFFFFFF` slots with **five
texture hashes** embedded. Those five are exactly the shader's two binding
structs (`shaders/common/engineparams.hlsl:730`):

    struct SGLightMapTextures {           struct SGAOTextures {
        Texture2DArray   k_ambient_lightmaps;     Texture2DArray k_ambient_lightmap_ao0;
        Texture2DArray<float> k_dirlight_occlusion_map;  Texture2DArray k_ambient_lightmap_ao1;
        Texture2DArray<float> k_punctual_occlusion_map;
    };                                    };

and the shipped DXGI formats agree, which is what identifies which hash is which:

| slot | format | dxgi | array size |
|---|---|---|---|
| ambient radiance | BC6H_UF16 (HDR) | 95 | `pages * lobes` |
| ao0, ao1 | BC5_UNORM (2ch) | 83 | `pages` |
| dirlight / punctual occlusion | BC4_UNORM (1ch) | 80 | `pages` |

All are 1024x1024, `mips = 1`.

## Which basis a level uses

`lobes = ambient.arraysize / occlusion.arraysize`. Measured over every level
that ships a lightmap, this is **exactly 5.00 or 4.00** — never anything else,
so the classification is not a judgement call:

    SG5 (12)  mpl_lobby_b2, mpl_tutorial_lobby, mpl_combat_fission + prologue/
              pantheon/climax, mpl_combat_gauss + section01a/01b/03,
              mpl_combat_celebration_room_blue/_orange
    SH4  (8)  mpl_arena_a, mpl_lobby_b_arena, mpl_tutorial_movement/hands/
              arena/boost/air_brake/micro_thrusters

The arena and tutorial maps bake SH4; the lobby and combat maps bake SG5. Both
paths exist in one shader as permutation branches (`kSG5LightMaps` /
`kSH4LightMaps`), so a consumer must read the ratio per level rather than
assume one.

## SG5 — `material_base_ps.hlsl:1097`

Radiance as five spherical gaussians, **in TANGENT SPACE**, one array slice each:

    for i in 0..4:
        slice        = page * 5 + i
        sg.mean      = mul(kLobeDirsSG5[i], tangenttoworld)
        sg.sharpness = kLambdaSG5
        sg.color     = ambient.Sample(uv, slice).rgb * kSG5Scale

    kLobeDirsSG5[5] = { 0.839526355, -0.534037054, 0.1},
                      {-0.247647554,  0.921233237, 0.3},
                      {-0.399156392, -0.768553317, 0.5},
                      { 0.670809269,  0.244979382, 0.7},
                      {-0.402912945,  0.166315958, 0.9}
    kLambdaSG5 = 3.62780595
    kSG5Scale  = 1.0 / 2.0

The z components run 0.1 .. 0.9 — a HEMISPHERICAL basis, which is why the
transform by `tangenttoworld` is mandatory. Do not confuse it with
`kLobeDirsSphereSG5` (`kLambdaSphereSG5 = 2.27315331`), a different full-sphere
basis used for irradiance volumes.

Evaluation (`sg.hlsl`):

    SGEvalDirection(sg, n) = sg.color * exp((saturate(dot(sg.mean, n)) - 1) * sg.sharpness)
    DiffuseTermSG(sg, n)   = max(saturate(dot(sg.mean, n)) * 2 * rcp(sg.sharpness) * sg.color, 0)

`DiffuseTermSG` deliberately omits the Pi term: it returns irradiance/Pi for
direct use as diffuse reflectance.

## SH4 — `material_base_ps.hlsl:1129`

L1 spherical harmonics, **baked in WORLD space**, four slices per page. The
encoding is not raw coefficients — slices 1..3 are packed and scaled by the DC:

    for i in 0..3:
        texel = ambient.Sample(uv, page * 4 + i).rgb
        sh[i] = texel                                  # i == 0: DC, stored as-is
        if i > 0:
            sh[i] = sh[i] * 2 - 1                      # unpack from [0,1]
            sh[i] = sh[i] * (sh[0] * 2)                # rescale by the DC

    diffuse  = EvalSH4IrradianceGeomerics(n, shr, shg, shb) * albedo * (1/Pi)
    specular = EvalSH4PrefilteredSpecular(n, v, sqrtroughness, specalbedo, shr, shg, shb) * cavity

Getting the `*2-1` unpack or the DC rescale wrong yields plausible-looking but
wrong lighting, so both are load-bearing.

## Low-spec path

`LowShaderSpec_` ignores both: a single slice holding **irradiance / Pi**,
applied as `sample.rgb * diffusealbedo`. This is the cheap approximation to
reach for if a consumer cannot evaluate the directional bases.

## Occlusion, always applied

Independent of basis, sampled at the page slice (no lobe offset):

    localshadow    = punctual_occlusion.Sample(lightmapuv)
    dirlightshadow = dirlight_occlusion.Sample(lightmapuv)   # only if lightmapuv != 0

## Per-instance UVs

Static-instanced geometry does not carry its lightmap UV in the vertex stream:
instances of one mesh sit in DIFFERENT atlas regions, so the UVs are per
instance, in the CGSI GPU sibling (`meshdata.uvoffset` / `.uvcount`, two UNORM16
each). `uvoffset` counts UVs, not bytes.

Measured on `mpl_arena_a` after import: median UV edge **2.6 atlas texels**, and
only 2 of 1024 lit objects exceed 60 -- i.e. the charts are continuous and
well-formed. A large UV *bounding box* is not evidence of a broken chart, since
one chart legitimately spans several islands; per-edge continuity is the test
that distinguishes them.

Four instances carry an all-zero UV run. That is the engine's "no bake" marker,
not a chart at the atlas origin, and wiring it tints the whole object with texel
(0,0) -- two of the four are 43.7 m across. They are skipped at export.
