"""The exterior vista's shading, read off the shipped pixel shaders.

`shader-confirmed`.  Every constant in this module was disassembled out of a
shipped `CGShaderSetResourceWin7` of `min_itc_master` (`4c47d84c1e52447a`).
Nothing here is fitted, guessed or eyeballed — where a number could not be
recovered from the shipped bytes it is absent, and the one per-FRAME constant the
vista depends on (`k_world_ambient`) is named as an explicit free parameter
rather than folded into a literal.

⛔ THIS MODULE IS NOT REPRODUCIBLE FROM THIS REPOSITORY
=======================================================
The shaderset disassembler (`le_shaderset_disasm.py`) is **not** part of this
public tree, so every constant below is a **transcribed** shipped-shader literal
rather than a value this repository can derive.  `tests/test_vista_shader.py`
re-types each one from the disassembly independently, which catches a typo but
cannot re-prove a reading.  Take the module on trust or redo it with your own
disassembler.  It is the only module in the project carrying that caveat, and
`README.md` and `CHANGELOG.md` 0.5.0 say so in as many words.

WHY THIS MODULE EXISTS
======================
Measured against the engine's own reflection-probe capture of Saturn, our render
was 6.9-7.6x too bright on one side of a terminator the engine does not have, and
3x too dim on the other.  The obvious reading — that the plate is "pre-lit" —
left roughly 100x unaccounted for.

The pixel shader answers all of it, and the answer is not a gain:

  * Saturn's albedo is **not** `base_color_factor`.  `6f67762bf83d59fd`'s
    pixel shader declares **no material constant buffer at all** (`cb0` perframe,
    `cb1` perview, `cb6`/`cb7` reflection), so the material record's `bakecolor`
    -- which is what the importer surfaces as `base_color_factor` -- cannot
    reach this shader.  Every coefficient is a compile-time literal.
  * The plate's own coefficient is `0.434154`, not `0.10035`.
  * The whole diffuse term is multiplied by `(1 + sum(saturate(BLEND.xyz)))`,
    and `BLEND` is vertex **colour set 1**, which reads `1.0` on 3703/3703 of
    Saturn's vertices -- so that factor is exactly **4.0**, and the importer
    drops the stream.
  * A further global `0.822` multiplies the diffuse.
  * The directional term is a WRAPPED diffuse, `saturate((N.L + 0.25) * 0.8)^2`,
    which is identically zero for `N.L <= -0.25`.  Both of the level's shipped
    directional lights put Saturn's sub-observer point at `N.L ~ -0.595`, so the
    engine's own sun contributes EXACTLY ZERO to the visible disc.  That is why
    the probe shows no terminator, and it is a shader fact, not an inference.

`SATURN_PLATE_SCALE / 0.10035 = 14.2` is therefore the shader-confirmed part of
the missing brightness; the remainder is `k_world_ambient` and the three detail
plates (below), neither of which is a scalar anyone can read off the level.

⛔ WHAT IS **NOT** IN HERE
=========================
* `k_world_ambient` (`SGPerFrameConstants` +20).  It multiplies the SG5 ambient
  sum in every shipped lit shader measured (Saturn, the debris rock, the moons),
  it is a per-FRAME value, and it is not in any level resource.  Callers pass it;
  the default is 1.0 because a matched-rock control puts our baked render
  within 0.93-1.18 of the engine's own capture without it.  That is a MEASURED
  bound on the product `k_world_ambient x (our rock albedo / the engine's)`, not
  a measurement of `k_world_ambient` alone -- state it that way.
* anything about the final frame's exposure or tonemap.  These shaders write
  scene-linear radiance; `k_exposure` and the Hable/ACES terms live in the post
  chain and are not modelled here.
"""
from __future__ import annotations

import math

# =============================================================================
# The SG5 lightmap basis -- shader-confirmed twice over
# =============================================================================
#: `kLobeDirsSG5`, and INDEPENDENTLY the same five literals appear inline in the
#: shipped pixel shaders of `6f67762bf83d59fd` (Saturn), `44538616b0138eb3` (a
#: debris rock) and `a1e53ff754dd1443` (the moons).  A hemispherical spiral: the
#: z components are 0.1 / 0.3 / 0.5 / 0.7 / 0.9 and all five are unit vectors.
SG5_DIRS = (
    (0.839526355, -0.534037054, 0.100000001),
    (-0.247647554, 0.921233237, 0.300000042),
    (-0.399156392, -0.768553317, 0.500000000),
    (0.670809269, 0.244979382, 0.700000107),
    (-0.402912945, 0.166315958, 0.900000095),
)
SG5_LAMBDA = 3.62780595          # kLambdaSG5
SG5_SCALE = 0.5                  # kSG5Scale
SG5_LOBES = 5

#: `2 / kLambdaSG5 * kSG5Scale`.  ★ The shipped shaders carry this PRE-MULTIPLIED
#: as the literal `0.275649` -- five times in Saturn's pixel shader, five more in
#: the rock's.  That the derived constant and the shipped literal agree to 6 decimal
#: places is what pins the whole reconstruction, and `tests/test_vista_shader.py`
#: asserts it against a verbatim copy of the disassembly.
SG5_K = 2.0 / SG5_LAMBDA * SG5_SCALE

#: The literal as it appears in the shipped instruction stream, kept separately
#: so the test compares two independently-sourced numbers rather than one number
#: with itself.
SG5_K_SHIPPED_LITERAL = 0.275649

#: `colour_slice = lm_slice_index * 5 + lobe`.  `le_mesh.lightmap` derives this
#: from the two array sizes (`export-validated`); the shader states it outright:
#: `mad rN.xyz, v6.xyzx, l(1,1,5), l(0,0,k)` then `sample t1` for k = 0..4, with
#: `v6 = LIGHTUV` carrying `(u, v, page)`.  `shader-confirmed`.
SG5_SLICES_PER_PAGE = 5


def sg5_weights(normal_ts=(0.0, 0.0, 1.0)):
    """The per-lobe scalar the shader applies, for a tangent-space normal.

    `max(0, dot(d_k, n)) * SG5_K`, then the shader clamps the dot to 1.0 first
    (`max r, 0` then `min r, 1`), which only bites for a non-unit normal.
    """
    out = []
    for d in SG5_DIRS:
        w = sum(a * b for a, b in zip(d, normal_ts))
        w = min(max(w, 0.0), 1.0)
        out.append(w * SG5_K)
    return out


#: The weights for an unperturbed surface (`n_ts == (0,0,1)`).  All five lobe
#: directions have positive z, so no lobe drops out.
SG5_WEIGHTS_FLAT = tuple(sg5_weights())


def sg5_ambient(lobes, normal_ts=(0.0, 0.0, 1.0), world_ambient=(1.0, 1.0, 1.0)):
    """`k_world_ambient * SUM_k saturate(dot(d_k, n)) * SG5_K * lobe_k`.

    `lobes` is five RGB triples in the page's own slice order.  This is the whole
    ambient term for a `k_ambient_lightmaps` surface -- there is nothing else.
    """
    if len(lobes) != SG5_LOBES:
        raise ValueError(f"SG5 needs exactly {SG5_LOBES} lobes, got {len(lobes)}")
    w = sg5_weights(normal_ts)
    acc = [0.0, 0.0, 0.0]
    for wi, lobe in zip(w, lobes):
        for c in range(3):
            acc[c] += max(0.0, wi * lobe[c])
    return tuple(acc[c] * world_ambient[c] for c in range(3))


# =============================================================================
# The level's directional lights, as the vista's shader consumes them
# =============================================================================
#: `6f67762bf83d59fd`'s pixel shader, the `k_level_dir_lights` branch:
#:     add    r5.w, r4.w, l(0.250000)        ; r4.w = dot(L, N)
#:     mul_sat r5.w, r5.w, l(0.800000)
#:     mul    r5.w, r5.w, r5.w
#:     mul    r9.xyz, r4.xyzx, r5.wwww       ; x albedo
#:     mul    r9.xyz, r9.xyzx, l(0.261651)
#: i.e. a WRAPPED Lambert with the terminator pushed to `N.L == -0.25`.
WRAP_OFFSET = 0.25
WRAP_SLOPE = 0.8
#: ⛔ CORRECTION: an earlier note here called this `SG5_SCALE * 2 / SG5_LAMBDA *
#: 0.949...` and said it was "not derived".  It IS derived, exactly, and two
#: independent readings of two different shaders agree:
#:     0.261651 == SATURN_DIFFUSE_GLOBAL / pi == 0.822 / 3.14159265 == 0.2616507
#: i.e. it is the ordinary Lambert `1/pi` with Saturn's own global folded in.
#: The moons (`a1e53ff754dd1443`) and the debris rock (`44538616b0138eb3`) carry
#: the UNFOLDED `0.318310` instead -- and, notably, NEITHER of them uses the
#: wrapped diffuse below (0 hits for `0.261651` and `l(0.800000)` in either).
#: ⇒ the wrap is SATURN-SPECIFIC; every other body gets a hard Lambert with its
#: terminator at `N.L == 0`, so the moons DO receive real direct light.
DIRLIGHT_DIFFUSE_K = 0.261651
LAMBERT_INV_PI = 0.318310


def wrap_diffuse(ndotl: float) -> float:
    """`saturate((N.L + 0.25) * 0.8)^2` -- the vista's only diffuse response to a
    directional light.  Zero for `ndotl <= -0.25`, one for `ndotl >= 1.0`."""
    w = min(max((ndotl + WRAP_OFFSET) * WRAP_SLOPE, 0.0), 1.0)
    return w * w


def dirlight_is_dark(ndotl: float) -> bool:
    """True when the wrapped term is identically zero, i.e. the surface receives
    NOTHING from that directional light.  This is the predicate that settles
    whether the engine's own sun reaches Saturn's visible disc at all."""
    return wrap_diffuse(ndotl) <= 0.0


# =============================================================================
# 6f67762bf83d59fd -- vst_saturn_planet  (obj030)
# =============================================================================
#: The plate's coefficient in the albedo accumulator.  `mad r4.xyz, r6.xyzx,
#: l(0.434154, ...), r4.xyzx` where `r6 = sample(vst_saturn_planet_hdr)`.
SATURN_PLATE_COEFF = 0.434154

#: The global diffuse coefficient.  `mad r0.xzw, r1.yyzw, l(0.822000, ...),
#: r0.xxzw`, applied to `albedo * ambient * blend_multiplier`.  ★ It is
#: SATURN-SPECIFIC: the debris-rock shader `44538616b0138eb3` has no such factor.
SATURN_DIFFUSE_GLOBAL = 0.822

#: The three detail plates and their per-layer tints and blend weights.  Each is
#: `sample(t) * tint * weight * BLEND.<channel>`; the role names are the
#: importer's own and every one is `symbol64`-confirmed against the shader's own
#: RDEF name (5/5 exact -- see `docs/MATERIALS.md`, binding provenance).
SATURN_DETAIL = (
    # (role,               shipped name,                          tint,                               weight, blend channel)
    ("layer1_albedo_map", "vst_saturn_planet_detail_spots_clr",
     (0.671031, 0.610880, 0.306828), 0.3, 0),
    ("layer2_albedo_map", "vst_saturn_planet_detail_wind_clr",
     (0.389517, 0.340856, 0.061280), 1.0, 1),
    ("layer3_albedo_map", "vst_saturn_planet_detail_clouds_clr",
     (0.153506, 0.140215, 0.051804), 0.6, 2),
)

#: `layer0_flowmap_map -> vst_saturn_planet_detail_nml` is NOT a shading normal:
#: the shader samples it twice with time-scrolled UVs, maps `*2-1`, and adds the
#: result to the detail layers' UVs at 0.11 / 0.2347 / 0.3912 / 0.75.  It is a
#: UV WARP, and the importer's `flowmap` role label is therefore correct.
SATURN_FLOWMAP_ROLE = "layer0_flowmap_map"

#: The atmospheric / rim tint, `l(0.016033, 0.018544, 0.079322)` -- strongly
#: BLUE, applied to `(0.05 + 0.95 * plate)`.
#: ★★ WHAT IT ACTUALLY IS: the specular **F0**, and the Fresnel mixes it toward
#: WHITE at the limb.  So the disc centre is the blue end and the limb is the
#: neutral, bright end -- the rim's hue is the hue of the incident RADIANCE, not
#: of this tint.  Limb/centre runs **25x-480x** depending on the plate value,
#: which is why omitting this stack removes the bright limb entirely.
SATURN_ATMOSPHERE_TINT = (0.016033, 0.018544, 0.079322)
SATURN_ATMOSPHERE_PLATE_BIAS = 0.05
SATURN_ATMOSPHERE_PLATE_SCALE = 0.95

#: `saturate(dot(A, (333,333,333)))` gates the atmospheric term.  ★ It is
#: provably identically **1**: the minimum of `sum(F0)` over the plate's range is
#: 0.005695, and `0.005695 x 333 = 1.896 > 1`.  Recorded so nobody re-derives it.
SATURN_ATMOSPHERE_GATE_IS_ONE = True

#: Schlick, exponent 5, on `saturate(N.V)` -- confirmed TWICE in the stream
#: (`dot(R, H)`, which is algebraically `N.V` because `H = normalize(V + R) = N`,
#: and again as `N.V` directly in the cube branch).
FRESNEL_EXPONENT = 5.0

#: The height-correlated Smith visibility the shader inlines:
#: `Vt(x) = 1 / (x + sqrt(0.317010 x^2 + 0.682990))`, and the specular core
#: multiplies by `2.346142 * Vt(c)^2`.  Those two literals ARE the shipped
#: roughness: `alpha^2 = 0.426231` for material roughness 0.808.
SATURN_VIS_A = 0.317010
SATURN_VIS_B = 0.682990
SATURN_VIS_K = 2.346142
SATURN_ROUGHNESS = 0.808

#: The cube branch's own Fresnel is CAPPED rather than mixed to white:
#: `saturate(A' + 0.383792 * (1 - A') * f5)`, sampled at `sample_l` LOD 7.017880.
#: ⚠ Note the asymmetry: the cube branch uses `A'` WITHOUT the blend multiplier
#: inside the Fresnel and applies the multiplier afterwards; the SG branch bakes
#: it into `F0` BEFORE the Fresnel.
SATURN_CUBE_FRESNEL_CAP = 0.383792
SATURN_CUBE_LOD = 7.017880

#: The four flowmap taps and the four per-layer UV chains.  ⛔ The previous note
#: here said the flowmap is sampled TWICE and warps "the detail layers" -- both
#: wrong.  It is sampled FOUR times with four different time scrolls, and the
#: 0.11 warp is applied to the **base plate**, not to a detail layer.
#: Each entry: (warp, uv_set, tile, tile_offset, uv_scroll, flow_scroll).
#: `uv_scroll`/`flow_scroll` are per-second rates multiplied by `k_time0_x`, so
#: at t = 0 (a still frame) every scroll term vanishes and the three `uv0` taps
#: collapse onto one sample.
SATURN_UV_CHAINS = {
    "plate":  {"warp": 0.11,    "uv": 0, "tile": (1.0, 1.0),  "tile_offset": (0.0, 0.0),
               "uv_scroll": (-0.0015, 0.0), "flow_scroll": (0.0, 0.007)},
    "spots":  {"warp": 0.23469, "uv": 0, "tile": (9.0, 12.0), "tile_offset": (0.0, 0.0),
               "uv_scroll": (-0.0100, 0.0), "flow_scroll": (-0.010, 0.0)},
    "wind":   {"warp": 0.3912,  "uv": 0, "tile": (3.0, 3.0),  "tile_offset": (0.0, -0.246),
               "uv_scroll": (-0.0080, 0.0), "flow_scroll": (-0.005, 0.0)},
    "clouds": {"warp": 0.75,    "uv": 1, "tile": (1.0, 1.0),  "tile_offset": (0.0, 0.0),
               "uv_scroll": (-0.0100, 0.0), "flow_scroll": (-0.015, 0.0)},
}

#: Which chain drives which importer role.  ★ `shader-confirmed`, and no longer
#: `inferred (RDEF order only)`: the RDEF bind order `t5 nml / t6 spots / t7 wind
#: / t8 clouds / t9 hdr` joins to the package's own role table hash-for-hash --
#:     layer0_flowmap_map a2fb5094dfde9416 == t5 vst_saturn_planet_detail_nml
#:     layer1_albedo_map  26e6ff80da39a3a8 == t6 ..._detail_spots_clr
#:     layer2_albedo_map  3d90c1543be196ee == t7 ..._detail_wind_clr
#:     layer3_albedo_map  524e4af17809c034 == t8 ..._detail_clouds_clr
#:     layer0_albedo_map  5ac9f126a8a79928 == t9 vst_saturn_planet_hdr
#: so the plate role, which the role table alone left unverifiable, really is
#: `vst_saturn_planet_hdr`, bound last.
SATURN_LAYER_ROLE = {
    "plate": "layer0_albedo_map",
    "spots": "layer1_albedo_map",
    "wind": "layer2_albedo_map",
    "clouds": "layer3_albedo_map",
}
SATURN_FLOWMAP_TEX_ROLE = "layer0_flowmap_map"


def saturn_flow_uv(layer, uv0, uv1, time=0.0):
    """The UV at which `vst_saturn_planet_detail_nml` is tapped for `layer`.

    `sample(t5, base + time * flow_scroll)`, in the shader's own DX UV space.
    ★ At `time == 0` the `plate`/`spots`/`wind` taps collapse onto ONE `uv0`
    sample and `clouds` onto one `uv1` sample -- which is why a still frame needs
    two texture fetches, not four.
    """
    ch = SATURN_UV_CHAINS[layer]
    base = uv0 if ch["uv"] == 0 else uv1
    s = ch["flow_scroll"]
    return (base[0] + s[0] * float(time), base[1] + s[1] * float(time))


def saturn_layer_uv(layer, uv0, uv1, flow, time=0.0):
    """The UV at which `layer`'s own plate is sampled, in DX UV space.

        uv = (flow*2 - 1) * warp + (base * tile + tile_offset) + time * uv_scroll

    `flow` is the RAW `t5` sample (before the `*2-1` decode), so pass the texture
    value, not the decoded one.
    """
    ch = SATURN_UV_CHAINS[layer]
    base = uv0 if ch["uv"] == 0 else uv1
    tile, off, sc = ch["tile"], ch["tile_offset"], ch["uv_scroll"]
    w = ch["warp"]
    return tuple((float(flow[c]) * 2.0 - 1.0) * w
                 + base[c] * tile[c] + off[c]
                 + sc[c] * float(time) for c in range(2))


def dx_uv_to_blender(uv):
    """`v_bl = 1 - v_dx` -- the importer's `flip_v`, as arithmetic.

    ⚠ The whole UV chain above is in the shader's DX space; a Blender image
    lookup at `(u, v_bl)` equals a DX lookup at `(u, 1 - v_bl)`.  So the honest
    node graph converts INTO DX space, does the shader's arithmetic verbatim, and
    converts back -- rather than trying to fold the flip into each tile/offset
    (where `tile.y = 12` happens to survive the flip mod 1 and `tile_offset.y =
    -0.246` does NOT).
    """
    return (uv[0], 1.0 - uv[1])


def schlick5(f0, cosine: float):
    """`F0 + (1 - F0) * (1 - c)^5`, per channel."""
    c = min(max(float(cosine), 0.0), 1.0)
    f = (1.0 - c) ** FRESNEL_EXPONENT
    return tuple(v + (1.0 - v) * f for v in tuple(f0)[:3])


def smith_vis(cosine: float) -> float:
    """`2.346142 * Vt(c)^2` with `Vt(x) = 1/(x + sqrt(0.317010 x^2 + 0.682990))`.

    Runs 0.5865 at `c = 1` to 3.4351 at `c = 0` -- a 5.86x limb boost on top of
    the Fresnel's own `1/F0` swing.
    """
    c = min(max(float(cosine), 0.0), 1.0)
    vt = 1.0 / (c + math.sqrt(SATURN_VIS_A * c * c + SATURN_VIS_B))
    return SATURN_VIS_K * vt * vt


def saturn_atmosphere_f0(plate, color1=None):
    """`A' = tint * (0.05 + 0.95 * plate)`, and `F0 = A' * (1 + sum(BLEND))`.

    Returns `(a_prime, f0)`.  Pass `color1 = None` for the un-multiplied `A'`
    the CUBE branch uses inside its Fresnel.
    """
    p = (plate, plate, plate) if isinstance(plate, (int, float)) else tuple(plate)[:3]
    a = tuple(SATURN_ATMOSPHERE_TINT[c]
              * (SATURN_ATMOSPHERE_PLATE_BIAS
                 + SATURN_ATMOSPHERE_PLATE_SCALE * p[c]) for c in range(3))
    if color1 is None:
        return a, a
    m = blend_multiplier(color1)
    return a, tuple(v * m for v in a)


def saturn_rim(plate, cosine, radiance, color1=(1.0, 1.0, 1.0), gain=1.0):
    """The SG-lightmap branch's rim, `F * vis * L_spec * gain`.

    ⚠ `radiance` (`L_spec`) is the incident specular radiance.  The shipped term
    is an ANISOTROPIC sum over the same five SG5 lobes the diffuse uses; the lobe
    geometry and its `norm * pi * exp(...)` weighting are NOT reproduced here.
    Passing the isotropic SG5 lobe sum is a **structural substitution** -- same
    five lobes, same texture, different weighting -- and `gain` folds the
    per-FRAME `k_sgopts.z/.w * k_world_ambient_spec` that is not in the bytes.
    ⛔ `gain` is therefore UNFITTED and defaults to 1.0.  Do not fit it to art.
    """
    _, f0 = saturn_atmosphere_f0(plate, color1)
    f = schlick5(f0, cosine)
    vis = smith_vis(cosine)
    r = (radiance, radiance, radiance) if isinstance(radiance, (int, float)) \
        else tuple(radiance)[:3]
    return tuple(f[c] * vis * r[c] * float(gain) for c in range(3))

#: What the vertex `BLEND` attribute is: **eColor slot 1**
#: (`mov o2.xyzw, v1.xyzw` in the vertex shader, whose input signature names
#: `COLOR 1`).
SATURN_BLEND_ATTRIBUTE = "color1"

#: The shaderset binds `k_ambient_lightmaps`, `k_ambient_spec_cubemaps`,
#: `k_dirlight_occlusion_map`, `k_level_dir_lights` and `k_fog_ramp`, and it does
#: NOT bind `k_clustered_lights` / `k_light_clusters` / `k_shadow_map`.  ⇒ the
#: level's two POINT lights cannot reach Saturn at all -- the shaderset has no
#: code path for them.  `shader-confirmed`.
SATURN_BINDS_CLUSTERED_LIGHTS = False

SATURN_SHADERSET = "6f67762bf83d59fd"


def blend_multiplier(color1=(1.0, 1.0, 1.0)) -> float:
    """`1 + saturate(BLEND.x) + saturate(BLEND.y) + saturate(BLEND.z)`.

    `add r2.w, r9.x, l(1.0)` / `add r2.w, r9.y, r2.w` / `add r2.w, r9.z, r2.w`
    with `r9 = mov_sat v2.xyzx`.  On Saturn's shipped mesh every component is
    1.0 (min 0.9961 over 3703 vertices), so this is **4.0** there.
    """
    return 1.0 + sum(min(max(float(c), 0.0), 1.0) for c in tuple(color1)[:3])


def saturn_plate_scale(color1=(1.0, 1.0, 1.0)) -> float:
    """The total scalar the shader applies to `vst_saturn_planet_hdr` before the
    ambient multiply: `0.822 * 0.434154 * (1 + sum(saturate(BLEND)))`.

    For the shipped mesh (`BLEND == (1,1,1)`) this is **1.42750**.
    """
    return SATURN_DIFFUSE_GLOBAL * SATURN_PLATE_COEFF * blend_multiplier(color1)


def saturn_albedo(plate, detail=None, color1=(1.0, 1.0, 1.0)):
    """The shader's albedo accumulator `r4`, per channel.

    `plate` is the `vst_saturn_planet_hdr` sample (a scalar or an RGB triple).
    `detail` maps a role key from `SATURN_DETAIL` to its own sample; a role that
    is absent contributes nothing, which is exactly what the importer does today
    -- so calling this with `detail=None` reproduces the importer's current
    coverage while still applying the coefficients the shader actually uses.
    """
    p = (plate, plate, plate) if isinstance(plate, (int, float)) else tuple(plate)[:3]
    b = [min(max(float(c), 0.0), 1.0) for c in tuple(color1)[:3]]
    acc = [SATURN_PLATE_COEFF * p[c] for c in range(3)]
    for role, _name, tint, weight, chan in SATURN_DETAIL:
        s = (detail or {}).get(role)
        if s is None:
            continue
        s = (s, s, s) if isinstance(s, (int, float)) else tuple(s)[:3]
        k = weight * b[chan]
        for c in range(3):
            acc[c] += s[c] * tint[c] * k
    return tuple(acc)


def saturn_diffuse(plate, lobes, detail=None, color1=(1.0, 1.0, 1.0),
                   normal_ts=(0.0, 0.0, 1.0), world_ambient=(1.0, 1.0, 1.0)):
    """The shader's whole diffuse output for Saturn:

        0.822 * albedo * (1 + sum(saturate(BLEND))) * k_world_ambient
              * SUM_k saturate(dot(d_k, n)) * 0.275649 * lobe_k

    ⛔ The specular, ambient-specular-cube, Fresnel-rim and fog terms are NOT in
    here.  This is the diffuse path, which is what the probe measures on the disc
    (the probe shows no view-dependent highlight there and the shader's own A/B
    ladder agrees).
    """
    alb = saturn_albedo(plate, detail, color1)
    amb = sg5_ambient(lobes, normal_ts, world_ambient)
    m = SATURN_DIFFUSE_GLOBAL * blend_multiplier(color1)
    return tuple(alb[c] * amb[c] * m for c in range(3))


# =============================================================================
# 35a8c5ad5fb8d894 -- vst_sun  (obj002, the sun card)
# =============================================================================
#: `mul r0.yzw, r0.yyzw, l(0, 0.2, 0.2, 0.2)`.  ★ The card's colour is
#: `vst_sun.rgb * vertexcolour.rgb * 0.2`.  The importer applies neither the 0.2
#: nor the vertex colour, which is 5x of the measured 12.4-15.4x over-brightness.
SUN_CARD_RGB_SCALE = 0.2

#: `mul r1.x, r1.x, l(0.999924)` on the `vst_sun_hdr_opc` sample.  A 1-LSB-of-8
#: normaliser, kept verbatim.
SUN_CARD_OPC_SCALE = 0.999924

#: `log/mul 2.2/exp` on `saturate(vst_sun.a)` -- the plate's ALPHA is decoded
#: through a 2.2 gamma before it becomes opacity.
SUN_CARD_ALPHA_GAMMA = 2.2

#: `ge r0.x, l(0.0001), r1.w` + `discard_nz` -- an alpha test, not a blend edge.
SUN_CARD_ALPHA_DISCARD = 1e-4

#: `min o0.xyz, r0.xyzx, l(11000, 11000, 11000, 0)` -- the shader's own output
#: clamp.  Nothing on this card can exceed 11000 linear.
SUN_CARD_OUTPUT_CLAMP = 11000.0

SUN_CARD_SHADERSET = "35a8c5ad5fb8d894"


def sun_card_alpha(opc_r: float, sun_a: float, vcol_a: float = 1.0) -> float:
    """`saturate(vst_sun_hdr_opc.r * 0.999924 * pow(saturate(vst_sun.a), 2.2)
    * vertexcolour.a)`.

    ★ This is Q4's answer: `vst_sun_hdr_opc` IS the opacity, it is read from the
    RED channel only, and it multiplies a gamma-2.2-decoded plate alpha.
    """
    a = min(max(float(sun_a), 0.0), 1.0) ** SUN_CARD_ALPHA_GAMMA
    return min(max(float(opc_r) * SUN_CARD_OPC_SCALE * a * float(vcol_a), 0.0), 1.0)


def sun_card_rgb(sun_rgb, vcol_rgb=(1.0, 1.0, 1.0)):
    """`vst_sun.rgb * vertexcolour.rgb * 0.2`, then the shader's 11000 clamp."""
    s = (sun_rgb, sun_rgb, sun_rgb) if isinstance(sun_rgb, (int, float)) \
        else tuple(sun_rgb)[:3]
    v = tuple(vcol_rgb)[:3]
    return tuple(min(s[c] * v[c] * SUN_CARD_RGB_SCALE, SUN_CARD_OUTPUT_CLAMP)
                 for c in range(3))


# =============================================================================
# a849eddeb321dcc7 -- vst_starfield_nebula_clr  (obj018, the SKYDOME)
# =============================================================================
#: ★★ P1 IS SETTLED, AND THE ANSWER OVERTURNS THE PUBLISHED DEFAULT.
#: The renderer's skydome case used to rest on an explicitly UNRESOLVED choice
#: between "pool 19 is a background fill" (`composite`) and "the dome is ordinary
#: depth-tested geometry" (`depth`), and only a disassembly of this shaderset
#: could decide it.  It decides it:
#:
#:   * the view matrix's TRANSLATION row is applied (`add r0.xyz, r2.xyzx,
#:     cb1[r1.y+24].xyzx`) -- the dome does NOT follow the camera;
#:   * `mov o0.xyzw, r2.xyzw` passes the full projection to `SV_Position` and
#:     `o0` is never rewritten -- there is NO reversed-Z far-plane pin (`z = 0`);
#:   * the ps declares `SV_Target 0/1` only -- no `SV_Depth`, no `discard`.
#:
#: ⇒ the dome is drawn at its own true projected depth and OVERWRITES anything
#: farther than the shell.  `depth` is the engine's reading; `composite` is not.
#: (`DEPTH_WRITE_MASK` itself is pass state and not in DXBC -- but the occlusion
#: verdict follows from the depth TEST plus the pool order alone.)
SKYDOME_SHADERSET = "a849eddeb321dcc7"

#: `mad r0.xyz, r0.xyzx, l(0.099382, 0.114076, 0.192477), l(0.000488, 0.000595,
#: 0.000717)` then `max o0.xyz, r0.xyzx, l(0,0,0,0)`; `mov o0.w, l(1.0)`.
#: A two-colour remap of the plate, blue-dominant, and the ONLY colour maths in
#: the shader.  `o0.a` is the literal 1.0 -- the dome is OPAQUE in-engine, so the
#: plate's all-zero alpha is irrelevant (the sample is masked to `.xyz`).
SKYDOME_TINT = (0.099382, 0.114076, 0.192477)
SKYDOME_FLOOR = (0.000488, 0.000595, 0.000717)

#: `mul r0.xy, v2.xyxx, l(1.0, 2.0, 0, 0)` -- the V axis is sampled at 2x.
#: ⚠ That is a PACKING convention, not a tiling: `measured` on the shipped mesh,
#: `obj018`'s `uv0.v` spans exactly **[0.5, 1.0]**, so `2v` spans [1, 2] and
#: covers the plate exactly once under wrap.  (The competing CLAMP reading is
#: degenerate -- it would smear one texel row over the whole dome.)
#: In Blender, after the importer's `flip_v` (`v_bl = 1 - v_dx`), the equivalent
#: is `v_bl' = 2 * v_bl` with `v_bl` spanning [0, 0.5]: Scale Y = 2, Location
#: Y = 0, and no wrap is even reached.
SKYDOME_UV_V_SCALE = 2.0
SKYDOME_UV_V_RANGE = (0.5, 1.0)

#: The ps binds `sampler_0` + the plate + `perviewcb` and **not** `perframecb`,
#: which is where `k_exposure`, `k_world_ambient*` and `k_fog_*` live.  ⇒ the
#: starfield is emitted at a fixed authored brightness with no exposure, no
#: ambient scale and no fog.  Purely emissive.
SKYDOME_BINDS_PERFRAME = False


def skydome_rgb(plate_rgb):
    """`max(0, plate.rgb * K + B)` -- the whole skydome colour path."""
    t = tuple(plate_rgb)[:3]
    return tuple(max(0.0, t[c] * SKYDOME_TINT[c] + SKYDOME_FLOOR[c])
                 for c in range(3))


# =============================================================================
# 340f6ff7265f0077 -- vst_saturn_rings_horizon_haze_clr  (obj003/obj004)
# =============================================================================
#: `eBlendLinearDodge` (additive) cards.  The ps binds **zero** constant buffers
#: and zero light resources: no Fresnel, no normals (the vertex format has none),
#: no time scroll, no fog.  Purely emissive, and the whole shader is:
#:     a = saturate(vcol.a); discard if a <= 1e-4
#:     o0.rgb = clamp(plate.rgb * pow(saturate(vcol.rgb), 2.2) * C, 0, 11000)
#:     o0.a   = a
HAZE_SHADERSET = "340f6ff7265f0077"
HAZE_TINT = (0.127469, 0.167655, 0.227420)

#: `mov_sat / log / mul 2.2 / exp` in the VS -- the vertex colour is decoded
#: through a 2.2 gamma before it modulates the plate.  ⚠ Apply it only to the
#: RAW stored attribute; a colour layer Blender has already linearised must not
#: be decoded twice.
HAZE_VCOL_GAMMA = 2.2
HAZE_ALPHA_DISCARD = 1e-4
HAZE_OUTPUT_CLAMP = 11000.0

#: `measured` on the shipped mesh: both cards carry vertex ALPHA 1.0 on every
#: vertex, so the discard never fires and `o0.a == 1`.  ★ That also makes
#: `eBlendLinearDodge`'s unrecovered src factor MOOT on this level -- `(ONE,ONE)`
#: and `(SRC_ALPHA,ONE)` are identical at a == 1.  `obj003`'s vertex rgb is 1.0;
#: `obj004`'s median is 0.796, i.e. a real per-card modulation of 0.796^2.2.
HAZE_VCOL_MEASURED = {"obj003": (1.0, 1.0, 1.0, 1.0),
                      "obj004": (0.796, 0.796, 0.796, 1.0)}


def haze_rgb(plate_rgb, vcol_rgb=(1.0, 1.0, 1.0)):
    """`clamp(plate.rgb * pow(saturate(vcol.rgb), 2.2) * C, 0, 11000)`."""
    t = tuple(plate_rgb)[:3]
    v = tuple(vcol_rgb)[:3]
    return tuple(min(max(0.0, t[c] * min(max(v[c], 0.0), 1.0) ** HAZE_VCOL_GAMMA
                         * HAZE_TINT[c]), HAZE_OUTPUT_CLAMP)
                 for c in range(3))


# =============================================================================
# ba863c7b2cb61616 -- vst_saturn_rings  (obj034-obj038, the ring sheet)
# =============================================================================
#: ★★ Q5 IS CLOSED, AND THE CAUSE WAS NOT IN THIS MATERIAL AT ALL.
#: The engine's sheet reads cold blue-white; ours read dark red-brown, and the
#: two candidates were "the plate is red" or "the skydome shows through the
#: alpha".  ⛔ The first is FALSIFIED by measurement: `vst_saturn_rings_clr` is
#: NEUTRAL GREY (linear median (0.27468, 0.28744, 0.26225), R/B = 1.047, green
#: the largest channel), and no plate value can redden this material -- even a
#: pure-red plate leaves the mix blue.  ✅ The second is confirmed: the sheet
#: passes ~92 % of the sky through its alpha, and OUR sky was the raw
#: red-brown `vst_starfield_nebula_clr` because the harness wired that plate
#: with no tint.  ⇒ the ring hue fix is `SKYDOME_TINT`, one material over.
RING_SHADERSET = "ba863c7b2cb61616"

#: `mad_sat r?.w, msk.R, l(0.419), l(0.890)` -- a narrow brightness modulator in
#: [0.890, 1.0], NOT a blend weight, then `M = m * saturate(color1.R)`.
#: `measured`: the mask's median is 0 and only 13.9 % of texels reach >= 0.2625,
#: and `color1` is (1,1,1,1) on 3638/3638 ring vertices -- so `M` sits at 0.890
#: over most of the sheet and hits 1.0 on 13.9 % of it, where the material goes
#: nearly black.  That is shipped behaviour, not a defect.
RING_MASK_SCALE = 0.419
RING_MASK_BIAS = 0.890

#: The DIFFUSE albedo: `plate * S * (1 - 0.971 M) + 0.000589 * M`, all times
#: `diffGlobal = 1 - 0.560 * (1 - 0.081 M)`.
#: ⛔ `base_color_factor` (0.4564, 0.4865, 0.6877) provably never reaches this
#: shader -- it binds no material constant buffer.
RING_ALBEDO_TINT = (0.364327, 0.398072, 0.614965)
RING_ALBEDO_M_SLOPE = 0.971
RING_DIFF_GLOBAL_A = 0.560
RING_DIFF_GLOBAL_B = 0.081

#: `M * (0.000589, 0.010000, 0.434241)` -- the "pre-arrival" triple, whose three
#: components feed three DIFFERENT terms: .x the albedo floor, .y the specular
#: F0 floor, .z the roughness floor.
RING_PREARRIVAL = (0.000589, 0.010000, 0.434241)

#: ⛔ CORRECTION: the "inverted mix" `0.7574*(C - K*plate) + K*plate` recorded by
#: an earlier note as the ALBEDO is the specular **F0** -- the two registers were
#: read the other way round.  Expanded it is
#: `(0.197137, 0.298266, 0.487311) + plate * (0.050405, 0.068113, 0.096851)`,
#: all times `(1 - M)`.
RING_F0_CONST = (0.197137, 0.298266, 0.487311)
RING_F0_PLATE = (0.050405, 0.068113, 0.096851)
RING_F0_SCALAR = 0.643400
RING_ROUGHNESS_BASE = 0.592300

#: `pow(saturate(plate.a), 2.2)`, discarded at 1e-4 == raw alpha 0.015199.
#: ⚠ Our `alpha_source: BASE_COLOR_ALPHA` was **3.90x too OPAQUE** at the median
#: (0.3216 vs the engine's 0.0824), i.e. too LITTLE sky came through the sheet.
RING_ALPHA_GAMMA = 2.2
RING_ALPHA_DISCARD_RAW = 0.015199

#: ★ `layer2_normal_map` (`vst_saturn_rings_dtl_nml`) at `uv1 * (1, 40)` is the
#: ONLY normal that shades: the shading normal is
#: `lerp(N_base, N_detail, saturate(color1.G))` and `color1.G == 1.0` on every
#: shipped vertex.  The `layer0_normal_map` the importer wires (at `uv1*(1,10)`)
#: contributes NOTHING, and the role that "reaches nothing" is the real one.
RING_DETAIL_UV_SCALE = 40.0
RING_BASE_UV_SCALE = 10.0

#: ★★ Plain Lambert `saturate(N.L)` -- NOT Saturn's wrap.  No +0.25, no x0.8, no
#: square, no 0.261651.  And the ps has NO `SV_IsFrontFace` and never flips the
#: normal, so the anti-sun face receives EXACTLY ZERO direct light.  Cycles
#: flips it, and that -- not the material -- is the 255x anti-sun blowout that
#: made the decoded 80 W/m^2 key light unusable.
RING_DIFFUSE_IS_WRAPPED = False
RING_BACKFACE_DIRECT_IS_ZERO = True

#: ★★ THE RING'S ENTIRE AMBIENT, and we rendered it as ZERO.
#: `ba863c7b2cb61616` binds NO colour lightmap and never reads `k_world_ambient`
#: (`cb0[1]` is not referenced).  Its only ambient is a reflection-probe cubemap
#: fetched along the reflection vector `R`:
#:
#:     aR   = sqrt(saturate(roughness^2 - 0.010))
#:     lod  = max(0, 10*aR - 1)
#:     cube = k_ambient_spec_cubemaps.SampleLevel(R_boxcorrected, lod)
#:     envF = saturate(F0col + (1-F0col)*(1-saturate(N.V))^5 / (2*(aR+0.001)+1))
#:     ambientSpec = envF * specMask * cube * AO_R
#:     col        += ambientSpec * k_world_ambient_spec
#:
#: At the measured `M = 0.890` that is `roughness 0.46691 -> aR 0.456070 ->
#: lod 3.5607`, denominator `1.914140`.
RING_ENV_ROUGH_EPS = 0.010
RING_ENV_LOD_SCALE = 10.0
RING_ENV_LOD_BIAS = 1.0
RING_ENV_FRESNEL_EPS = 0.001

#: `saturate(dot(F0col, (333,333,333)))` -- the same "is this specular colour
#: black?" kill-switch Saturn carries.  ⚠ Unlike Saturn's it is NOT identically
#: one here: `F0col` is driven to (0,0,0) as `M -> 1`, so the 13.9 % of the sheet
#: the pre-arrival mask marks loses its environment term entirely.
RING_SPEC_MASK_K = 333.0


def ring_mask_m(mask_r, color1_r=1.0):
    """`M = saturate(msk.R * 0.419 + 0.890) * saturate(color1.R)`."""
    m = min(max(float(mask_r) * RING_MASK_SCALE + RING_MASK_BIAS, 0.0), 1.0)
    return m * min(max(float(color1_r), 0.0), 1.0)


def ring_roughness(m):
    """`roughness = 0.592300*(1 - 0.971 M) + 0.434241 M`."""
    m = float(m)
    return RING_ROUGHNESS_BASE * (1.0 - RING_ALBEDO_M_SLOPE * m) \
        + RING_PREARRIVAL[2] * m


def ring_f0col(plate, m):
    """`F0col = ((0.197137,0.298266,0.487311) + plate*(0.050405,0.068113,0.096851))
    * (1 - M)` -- the specular F0, NOT the albedo."""
    p = (plate, plate, plate) if isinstance(plate, (int, float)) else tuple(plate)[:3]
    return tuple((RING_F0_CONST[c] + p[c] * RING_F0_PLATE[c]) * (1.0 - float(m))
                 for c in range(3))


def ring_spec_mask(f0col):
    """`saturate(dot(F0col, 333))` -- 1 everywhere except the pre-arrival band."""
    return min(max(sum(tuple(f0col)[:3]) * RING_SPEC_MASK_K, 0.0), 1.0)


def ring_alpha_roughness(roughness):
    """`aR = sqrt(saturate(roughness^2 - 0.010))`."""
    r = float(roughness)
    return math.sqrt(min(max(r * r - RING_ENV_ROUGH_EPS, 0.0), 1.0))


def ring_env_lod(roughness):
    """`lod = max(0, 10*aR - 1)` -- the cube mip the shader fetches.

    ⚠ Blender's `ShaderNodeTexEnvironment` has no LOD input, so a node graph can
    only pick a PRE-FILTERED image.  The shipped probe resource carries its own
    mip chain on disk (one cube strip per mip, M = 0..6), and those mips are read
    AS STORED -- the probe resource's `normalizations` must NOT be applied to
    them.  So "sample mip round(lod)" is buildable and is what the harness does;
    the fractional part of the LOD is dropped.
    """
    return max(0.0, RING_ENV_LOD_SCALE * ring_alpha_roughness(roughness)
               - RING_ENV_LOD_BIAS)


def ring_env_fresnel(f0col, ndotv, roughness):
    """`saturate(F0col + (1-F0col) * (1-saturate(N.V))^5 / (2*(aR+0.001)+1))`.

    ★ Note the roughness-dependent DIVISOR: unlike Saturn's rim this Fresnel is
    damped rather than mixed all the way to white, so a rough ring never goes
    mirror-bright at grazing angles.  At `M = 0.890` the divisor is 1.914140.
    """
    f0 = tuple(f0col)[:3]
    c = min(max(float(ndotv), 0.0), 1.0)
    p5 = (1.0 - c) ** FRESNEL_EXPONENT
    d = 2.0 * (ring_alpha_roughness(roughness) + RING_ENV_FRESNEL_EPS) + 1.0
    return tuple(min(max(f0[i] + (1.0 - f0[i]) * p5 / d, 0.0), 1.0) for i in range(3))


def ring_ambient_spec(cube, f0col, ndotv, roughness, ao=1.0, world_ambient_spec=1.0):
    """`envF * specMask * cube * AO_R * k_world_ambient_spec`.

    ⛔ `k_world_ambient_spec` (`SGPerFrameConstants` +32, `cb0[2]`) is per-FRAME
    and is in NO level resource, exactly like `k_world_ambient`.  It defaults to
    1.0 here and is NOT fitted.  `AO_R` is one texel of an engine-CREATED default
    `k_ambient_lightmap_ao0/ao1` (the ring's `uv2` is `(0,0)` on all 3 638
    vertices and its page index is the `0xFFFFFFFF` sentinel), so 1.0 is
    `inferred`, not measured.
    """
    f = ring_env_fresnel(f0col, ndotv, roughness)
    mask = ring_spec_mask(f0col)
    c = (cube, cube, cube) if isinstance(cube, (int, float)) else tuple(cube)[:3]
    return tuple(f[i] * mask * c[i] * float(ao) * float(world_ambient_spec)
                 for i in range(3))


# =============================================================================
# a1e53ff754dd1443 -- vst_saturn_moons  (obj031-obj033)
# =============================================================================
#: `albedo = clr * 0.552011`, a further global `0.719` on the ambient term, and
#: the emissive plate ADDED through a strongly blue tint (blue ~5x red).
MOON_SHADERSET = "a1e53ff754dd1443"
MOON_ALBEDO_COEFF = 0.552011
MOON_AMBIENT_GLOBAL = 0.719
MOON_EMISSIVE_TINT = (0.403480, 0.425726, 2.000000)

#: The moons carry NO blue Fresnel rim (0 hits for `SATURN_ATMOSPHERE_TINT`) and
#: their roughness is a fixed 1.0, which makes their probe/AO branch dead as
#: shipped.  Their diffuse is the unfolded Lambert `LAMBERT_INV_PI`.
MOON_HAS_ATMOSPHERE_RIM = False
MOON_ROUGHNESS = 1.0


# =============================================================================
# b9588078adab3e49 -- the dig-site STEAM/DUST FX cards  (obj013-obj017)
# =============================================================================
#: ★★ THE PALE STRAIGHT-EDGED QUADS ACROSS SATURN, AND THE CAUSE IS ONE LINE.
#: `shader-confirmed`: `b9588078adab3e49`'s pixel shader (41 body lines) and its
#: vertex shader (56 body lines).  `eMTForwardTransparent` / `eBlendLinearDodge`.
#: The whole pixel shader is:
#:
#:     t   = k_time0_x                                             /* cb0[0].y */
#:     f   = t1.Sample(uv*0.5 + t*(-0.000647,-0.002415)).rg
#:     uvW = uv + 0.22*(2f - 1)
#:     aD  = t2.Sample(uvW*2.0 + t*(-0.000434,-0.002462)).a
#:     aS  = t0.Sample(uvW*0.5 + t*(-0.000244, 0.002789)).a
#:     o0.a   = min(1, pow(sat(aS*0.784314),2.2)
#:                   * pow(sat(aD*0.721569+0.082353),2.2)
#:                   * vcol.a^2)
#:     discard if o0.a <= 1e-4
#:     o0.rgb = clamp(pow(sat(vcol.rgb),2.2) * C, 0, 11000)
#:
#: ★★ **`o0.rgb` reads NO TEXTURE AT ALL.**  Every plate this material binds is
#: sampled for its ALPHA and nothing else -- `t2.xywz -> r0.z` and `t0.wxyz ->
#: r0.x` both take the `w` lane.  The colour is the vertex colour through a 2.2
#: gamma times one compile-time constant, and `measured` the vertex rgb is
#: **1.0 on all 208 vertices of obj013-obj017**, so the shipped card's colour is
#: a CONSTANT pale blue over its whole area.  All of its structure is in `o0.a`.
FX_CARD_SHADERSET = "b9588078adab3e49"
FX_CARD_TINT = (1.120846, 1.343614, 1.856059)

#: `mov_sat / log / mul 2.2 / exp` in the vertex shader on `v1.xyz` only; `o2.w`
#: is `mov v1.w`, i.e. the vertex ALPHA is passed through RAW.  ⚠ Apply the gamma
#: only to the stored attribute's rgb, never to its alpha.
FX_CARD_VCOL_GAMMA = 2.2
#: `mul r0.y, v2.w, v2.w` -- the vertex alpha enters SQUARED.
FX_CARD_VCOL_ALPHA_POWER = 2

#: The UV chain.  ⚠ ONE texcoord reaches the ps: the VS input signature is
#: `POSITION / COLOR0 / TEXCOORD0` and it does a plain `mov o3.xy, v2.xyxx`, so
#: the ps's `UV1` interpolator is TEXCOORD **0** (our `uv0`) with no packing.
#: The mesh's second texcoord (slot 4, `eU16n`) is the lightmap UV and this
#: shader never reads it.
FX_CARD_FLOW_UV_SCALE = 0.5
FX_CARD_FLOW_SCROLL = (-0.000647, -0.002415)
FX_CARD_WARP = 0.22
FX_CARD_DUST_UV_SCALE = 2.0
FX_CARD_DUST_SCROLL = (-0.000434, -0.002462)
FX_CARD_STEAM_UV_SCALE = 0.5
FX_CARD_STEAM_SCROLL = (-0.000244, 0.002789)

#: `mad_sat r0.z, r0.z, l(0.721569), l(0.082353)` -- 184/255 and 21/255.
FX_CARD_DUST_SCALE = 0.721569
FX_CARD_DUST_BIAS = 0.082353
#: `mul_sat r0.x, r0.x, l(0.784314)` -- 200/255.
FX_CARD_STEAM_SCALE = 0.784314
FX_CARD_ALPHA_GAMMA = 2.2
FX_CARD_ALPHA_DISCARD = 1e-4
FX_CARD_OUTPUT_CLAMP = 11000.0

#: Which bind is which, joined through the RDEF bind order (t0/t1/t2 there and
#: in the package's own role table agree hash-for-hash, 3/3):
#:     t0 pfx_steam_b_clr                            cd05c827b1bf210c
#:     t1 gfx_steam_scrolling_clr_nml                13670421efc5bc40
#:     t2 gfx_min_itt_sensor_array_scrolling_dust_clr 1e4b12c86c128c4e
#: ⚠ The role names the material record carries are `emissive`/`flowmap`/`albedo`
#: and NONE of them describes what the shader does with the texture: the
#: "emissive" and "albedo" plates are both pure OPACITY inputs here.
FX_CARD_ROLE = {"steam": "layer0_emissive_map",
                "flow": "layer0_flowmap_map",
                "dust": "layer0_albedo_map"}

#: ⚠⚠ `eBlendLinearDodge`'s SOURCE factor is STILL not recovered from any shipped
#: state block -- and unlike the haze cards, where every vertex carried alpha 1.0
#: and `(ONE,ONE) == (SRC_ALPHA,ONE)`, here it decides the entire picture.
#: `inferred`, from the shader itself and from the engine's own probe:
#:   * `o0.rgb` is CONSTANT over the card, so under `(ONE, ONE)` the shipped
#:     engine would draw flat `(1.12, 1.34, 1.86)` polygons hard-cut at the
#:     `a <= 1e-4` contour -- brighter than anything else in the frame.  The
#:     probe capture shows nothing of the kind where these cards sit (probe mean
#:     0.0032 over the directions they cover).
#:   * under `(SRC_ALPHA, ONE)` the elaborately warped, twice-gamma'd, three-times
#:     scrolled `o0.a` is what shapes the card and the `1e-4` discard is exactly
#:     the "this pixel adds nothing" early-out it looks like.
#: The harness exposes both (`fx_card_src_alpha=1|0`) so the alternative stays
#: renderable rather than asserted away.
FX_CARD_SRC_FACTOR_IS_SRC_ALPHA = True

#: `measured`, straight off the shipped `color0` blobs (208 vertices over the
#: five objects): rgb is EXACTLY 1.0 everywhere and the alpha is a 0->1 fade with
#: a low mean, which is the card's soft edge.
FX_CARD_VCOL_MEASURED = {
    "obj013": {"rgb": (1.0, 1.0, 1.0), "a_min": 0.0, "a_max": 1.0, "a_mean": 0.1620},
    "obj014": {"rgb": (1.0, 1.0, 1.0), "a_min": 0.0, "a_max": 0.6471, "a_mean": 0.1618},
    "obj015": {"rgb": (1.0, 1.0, 1.0), "a_min": 0.0, "a_max": 1.0, "a_mean": 0.1556},
    "obj016": {"rgb": (1.0, 1.0, 1.0), "a_min": 0.0, "a_max": 0.6471, "a_mean": 0.1618},
    "obj017": {"rgb": (1.0, 1.0, 1.0), "a_min": 0.0, "a_max": 1.0, "a_mean": 0.1745},
}

#: `measured`, off the plates' own BC3/BC5 mip 0:
#: dust `.A` median 0.4078, steam `.A` median 0.2275, and the flowmap's RG sit on
#: 0.4980 / 0.4902 -- i.e. the warp is ~0 at the median and reaches |0.10| / 0.18
#: of a UV at the extremes.
FX_CARD_PLATE_MEDIAN_ALPHA = {"dust": 0.4078, "steam": 0.2275}


def fx_card_rgb(vcol_rgb=(1.0, 1.0, 1.0)):
    """`clamp(pow(saturate(vcol.rgb), 2.2) * C, 0, 11000)` -- the WHOLE colour."""
    v = tuple(vcol_rgb)[:3]
    return tuple(min(max(min(max(v[c], 0.0), 1.0) ** FX_CARD_VCOL_GAMMA
                         * FX_CARD_TINT[c], 0.0), FX_CARD_OUTPUT_CLAMP)
                 for c in range(3))


def fx_card_alpha(dust_a, steam_a, vcol_a=1.0):
    """`min(1, pow(sat(aS*0.784314),2.2) * pow(sat(aD*0.721569+0.082353),2.2)
    * vcol.a^2)` -- the only spatially varying quantity this shader computes."""
    d = min(max(float(dust_a) * FX_CARD_DUST_SCALE + FX_CARD_DUST_BIAS, 0.0), 1.0)
    s = min(max(float(steam_a) * FX_CARD_STEAM_SCALE, 0.0), 1.0)
    return min(1.0, (s ** FX_CARD_ALPHA_GAMMA) * (d ** FX_CARD_ALPHA_GAMMA)
               * float(vcol_a) ** FX_CARD_VCOL_ALPHA_POWER)


def fx_card_added_rgb(dust_a, steam_a, vcol_rgb=(1.0, 1.0, 1.0), vcol_a=1.0,
                      src_alpha=FX_CARD_SRC_FACTOR_IS_SRC_ALPHA):
    """What `eBlendLinearDodge` adds to the framebuffer at one pixel.

    `src_alpha=True` is `dst += rgb*a` (the default reading, see
    `FX_CARD_SRC_FACTOR_IS_SRC_ALPHA`); `False` is `dst += rgb`, which is the
    flat pale polygon.  Both honour the `1e-4` discard.
    """
    a = fx_card_alpha(dust_a, steam_a, vcol_a)
    if a <= FX_CARD_ALPHA_DISCARD:
        return (0.0, 0.0, 0.0)
    rgb = fx_card_rgb(vcol_rgb)
    k = a if src_alpha else 1.0
    return tuple(c * k for c in rgb)


def fx_card_flow_uv(uv, time=0.0):
    """`uv*0.5 + t*(-0.000647, -0.002415)` -- the flowmap tap, in DX UV space."""
    return tuple(uv[c] * FX_CARD_FLOW_UV_SCALE + FX_CARD_FLOW_SCROLL[c] * float(time)
                 for c in range(2))


def fx_card_warped_uv(uv, flow_rg):
    """`uv + 0.22*(2*flow - 1)` -- the shared warp both alpha taps start from."""
    return tuple(uv[c] + FX_CARD_WARP * (2.0 * flow_rg[c] - 1.0) for c in range(2))


def fx_card_plate_uv(which, warped_uv, time=0.0):
    """`warped*scale + t*scroll` for `which` in {'dust', 'steam'}, in DX space."""
    scale, scroll = {
        "dust": (FX_CARD_DUST_UV_SCALE, FX_CARD_DUST_SCROLL),
        "steam": (FX_CARD_STEAM_UV_SCALE, FX_CARD_STEAM_SCROLL),
    }[which]
    return tuple(warped_uv[c] * scale + scroll[c] * float(time) for c in range(2))


# =============================================================================
# The per-shaderset table the importer consults
# =============================================================================
#: Only shadersets whose pixel shader has actually been disassembled appear
#: here.
#: ⛔ Never add a row from a name, a role or a guess -- the whole point of the
#: table is that every number in it came out of the shipped instruction stream.
SHADERSET_TERMS = {
    SATURN_SHADERSET: {
        "name": "vst_saturn_planet",
        "objects": ("obj030",),
        "albedo_scale": None,          # filled below; depends on the mesh's BLEND
        "plate_role": "layer0_albedo_map",
        "plate_coeff": SATURN_PLATE_COEFF,
        "diffuse_global": SATURN_DIFFUSE_GLOBAL,
        "blend_attribute": SATURN_BLEND_ATTRIBUTE,
        "binds_clustered_lights": SATURN_BINDS_CLUSTERED_LIGHTS,
        "ambient": "sg5",
        "shader": "6f67762bf83d59fd pixel shader",
    },
    SUN_CARD_SHADERSET: {
        "name": "vst_sun",
        "objects": ("obj002",),
        "rgb_scale": SUN_CARD_RGB_SCALE,
        "opacity_role": "rdef_bind2",
        "opacity_channel": "R",
        "alpha_gamma": SUN_CARD_ALPHA_GAMMA,
        "output_clamp": SUN_CARD_OUTPUT_CLAMP,
        "binds_clustered_lights": False,
        "ambient": None,
        "shader": "35a8c5ad5fb8d894 pixel shader",
    },
    SKYDOME_SHADERSET: {
        "name": "vst_starfield_nebula",
        "objects": ("obj018",),
        "emissive_tint": SKYDOME_TINT,
        "emissive_floor": SKYDOME_FLOOR,
        "uv_v_scale": SKYDOME_UV_V_SCALE,
        "opaque": True,
        "binds_clustered_lights": False,
        "binds_perframe": SKYDOME_BINDS_PERFRAME,
        "ambient": None,
        "shader": "a849eddeb321dcc7 pixel shader",
    },
    HAZE_SHADERSET: {
        "name": "vst_saturn_rings_horizon_haze",
        "objects": ("obj003", "obj004"),
        "emissive_tint": HAZE_TINT,
        "vcol_gamma": HAZE_VCOL_GAMMA,
        "alpha_discard": HAZE_ALPHA_DISCARD,
        "output_clamp": HAZE_OUTPUT_CLAMP,
        "binds_clustered_lights": False,
        "ambient": None,
        "shader": "340f6ff7265f0077 pixel shader",
    },
    RING_SHADERSET: {
        "name": "vst_saturn_rings",
        "objects": ("obj034", "obj035", "obj036", "obj037", "obj038"),
        "plate_role": "layer0_albedo_map",
        "albedo_tint": RING_ALBEDO_TINT,
        "mask_role": "layer1_blend_mask",
        "normal_role": "layer2_normal_map",
        "alpha_gamma": RING_ALPHA_GAMMA,
        "diffuse_wrapped": RING_DIFFUSE_IS_WRAPPED,
        "binds_clustered_lights": True,
        "ambient": "probe",          # ⛔ NO colour lightmap: `baked` is impossible
        "shader": "ba863c7b2cb61616 pixel shader",
    },
    FX_CARD_SHADERSET: {
        "name": "gfx_min_itt_steam_dust_fx_card",
        "objects": ("obj013", "obj014", "obj015", "obj016", "obj017"),
        "emissive_tint": FX_CARD_TINT,
        "vcol_gamma": FX_CARD_VCOL_GAMMA,
        "vcol_alpha_power": FX_CARD_VCOL_ALPHA_POWER,
        "alpha_discard": FX_CARD_ALPHA_DISCARD,
        "output_clamp": FX_CARD_OUTPUT_CLAMP,
        "roles": FX_CARD_ROLE,
        "binds_clustered_lights": False,
        "binds_perframe": True,          # cb0 -- k_time0_x, and nothing else
        "ambient": None,
        "shader": "b9588078adab3e49 pixel shader",
    },
    MOON_SHADERSET: {
        "name": "vst_saturn_moons",
        "objects": ("obj031", "obj032", "obj033"),
        "plate_role": "layer0_albedo_map",
        "albedo_coeff": MOON_ALBEDO_COEFF,
        "ambient_global": MOON_AMBIENT_GLOBAL,
        "emissive_role": "layer0_emissive_map",
        "emissive_tint": MOON_EMISSIVE_TINT,
        "diffuse_wrapped": False,
        "binds_clustered_lights": False,
        "ambient": "sg5",
        "shader": "a1e53ff754dd1443 pixel shader",
    },
}

#: ★★ Q3 — the per-object lightmap mode is a property of the SHADERSET, not of
#: how bright the object's atlas page is.  Three answers, not two:
#:   `baked`   the shader's only light IS the SG5 colour lightmap
#:   `ambient` the shader ADDS a live light to that sum (proven: the debris rock
#:             adds unconditionally, and `k_dirlight_occlusion_map` scales only
#:             the LIVE light -- so there is NO double-count)
#:   `neither` the shader binds no colour lightmap at all
LIGHTMAP_MODE_BY_SHADERSET = {
    SATURN_SHADERSET: "baked",     # its wrapped diffuse is 0 at N.L = -0.595
    MOON_SHADERSET: "ambient",     # hard Lambert -> the moons DO get direct light
    RING_SHADERSET: "neither",     # k_ambient_lightmap_ao0/ao1 only
    SKYDOME_SHADERSET: "neither",
    HAZE_SHADERSET: "neither",
    SUN_CARD_SHADERSET: "neither",
    FX_CARD_SHADERSET: "neither",  # binds cb0 and three textures, no lightmap
}

#: `44538616b0138eb3`, the debris rock / dig-site class.  Not a vista body, so it
#: gets no SHADERSET_TERMS row -- but its Q3 answer is the load-bearing one.
DEBRIS_ROCK_SHADERSET = "44538616b0138eb3"
LIGHTMAP_MODE_BY_SHADERSET[DEBRIS_ROCK_SHADERSET] = "ambient"
SHADERSET_TERMS[SATURN_SHADERSET]["albedo_scale"] = saturn_plate_scale()


# =============================================================================
# ★★ THE SCENE-FOG EPILOGUE -- the last thing three of these shaders do
# =============================================================================
#: `shader-confirmed`.  The tail of `6f67762bf83d59fd`'s pixel shader, and the
#: SAME block verbatim in `ba863c7b2cb61616` and `35a8c5ad5fb8d894`.  It is the
#: shader's final statement before `max o0.xyz, ..., 0`:
#:
#:     d   = length(cameraPos - POSITIONWS)
#:     td  = saturate((d - k_fog_depth.x) / (k_fog_depth.y - k_fog_depth.x))
#:     Ad  = k_fog_ramp.SampleLevel((td*0.996094 + 0.001953, 0), SLICE 0, 0)
#:     th  = saturate((POSITIONWS.y - k_fog_depth.z) / (k_fog_depth.w - k_fog_depth.z))
#:     Ah  = k_fog_ramp.SampleLevel((th*0.996094 + 0.001953, 0), SLICE 1, 0)
#:     C   = lerp(k_fog_low_color, k_fog_hi_color, Ah)
#:     f   = C.a * k_fog_color.a * Ad
#:     o0.rgb = lerp(colour, C.rgb * k_fog_color.rgb, f)
#:
#: ★★ WHY IT MATTERS HERE.  This is the whole of the residual-brightness anomaly
#: against the engine's own probe.  Saturn is 19-38 kilo-units from the probe eye
#: and its disc spans +/-31,600 units of world HEIGHT, so both ramps are fully
#: engaged on it -- and `measured` against the engine's own reflection probe, the
#: shipped disc carries only **0.150** of the radiance our unfogged render
#: computes, over nine directions spanning a 3.4x dynamic range.
#:
#: ⛔ NOTHING here is decodable from a level resource.  `k_fog_depth`,
#: `k_fog_color`, `k_fog_low_color`, `k_fog_hi_color` are `SGPerFrameConstants`
#: (cb0[12], cb0[11], cb0[13], cb0[14]) and `k_fog_ramp` is an engine-bound
#: texture array.  The FORM is `shader-confirmed`; every VALUE is per-FRAME.
FOG_RAMP_U_SCALE = 0.996094          # `mad r2.x, r1.x, l(0.996094), l(0.001953)`
FOG_RAMP_U_BIAS = 0.001953           # == 255/256 and 1/512: a 256-texel ramp
FOG_RAMP_SLICE_DISTANCE = 0          # `mov r2.yz, l(0,0,0,0)`
FOG_RAMP_SLICE_HEIGHT = 1            # `mov r2.yz, l(0,0,1.000000,0)`

#: Which shadersets carry the epilogue.  `measured` by searching every
#: disassembled shader for `k_fog_ramp` -- and the split is a discriminator,
#: not a detail: the two bodies that are NOT fogged are exactly the two the
#: harness already matched against the probe.
SHADERSET_BINDS_FOG = frozenset({SATURN_SHADERSET, RING_SHADERSET,
                                 SUN_CARD_SHADERSET})
SHADERSET_NO_FOG = frozenset({SKYDOME_SHADERSET, HAZE_SHADERSET,
                              FX_CARD_SHADERSET, MOON_SHADERSET,
                              DEBRIS_ROCK_SHADERSET})

#: ★ `measured` (probe 15, by equirect comparison against the shipped probe
#: capture): the ratio
#: render/probe on the SUNWARD half of Saturn's disc, where the fog COLOUR term
#: is indistinguishable from zero, over nine directions whose own values span
#: 3.4x -- 0.1372, 0.1476, 0.1535, 0.1455, 0.1500, 0.1524, 0.1493, 0.1447,
#: 0.1658.  Mean 0.1496, sd 0.008.
#:
#: Since `p = (1-f)*c + f*F` and `F >= 0`, `1 - f <= p/c` **pointwise and without
#: any further assumption**, so this is a hard bound: `f >= 0.85` there.
#: ⚠ It bounds the PRODUCT `k_world_ambient * (1 - f)`.  The two are separated by
#: the debris rock (`44538616b0138eb3`), which multiplies the same `cb0[1].yzw`
#: `k_world_ambient` and binds NO fog: the matched-rock control put that at
#: 0.93-1.18 with `k_world_ambient = 1.0`.  ⇒ the 0.150 is the fog's `(1 - f)`.
SATURN_PROBE_UNFOGGED_RESIDUAL = 0.1496
SATURN_PROBE_UNFOGGED_RESIDUAL_SD = 0.008


def fog_ramp_u(t: float) -> float:
    """`saturate(t) * 0.996094 + 0.001953` -- the half-texel-inset ramp lookup.

    Both ramps use it, and both are 256 texels wide (255/256 and 1/512).
    """
    return min(max(float(t), 0.0), 1.0) * FOG_RAMP_U_SCALE + FOG_RAMP_U_BIAS


def fog_ramp_t(x: float, lo: float, hi: float) -> float:
    """`saturate((x - lo) / (hi - lo))` -- `div_sat` in the stream.

    `(lo, hi)` is `k_fog_depth.xy` for the DISTANCE ramp and `k_fog_depth.zw` for
    the HEIGHT ramp; `x` is `length(eye - P)` and `POSITIONWS.y` respectively.
    """
    d = float(hi) - float(lo)
    if d == 0.0:
        return 0.0
    return min(max((float(x) - float(lo)) / d, 0.0), 1.0)


def fog_colour_and_factor(a_dist, a_height, low_color, hi_color, fog_color):
    """`(C.rgb * k_fog_color.rgb, C.a * k_fog_color.a * Ad)`.

    `a_dist` / `a_height` are the two `k_fog_ramp` samples; the four colours are
    per-FRAME `SGPerFrameConstants` values the caller has to supply.
    """
    lo = tuple(low_color)[:4]
    hi = tuple(hi_color)[:4]
    h = float(a_height)
    c = tuple(lo[i] + (hi[i] - lo[i]) * h for i in range(4))
    k = tuple(fog_color)[:4]
    rgb = tuple(c[i] * k[i] for i in range(3))
    return rgb, c[3] * k[3] * float(a_dist)


def scene_fog(colour, fog_rgb, f: float):
    """`lerp(colour, fog_rgb, f)`, then `max(0)`.

    ⚠ It is applied to `o0.rgb` ONLY; `o0.a` is untouched, so on the two BLENDED
    fog consumers (the ring sheet and the sun card) the fog must not be allowed
    to change how much of the sky comes through.
    """
    c = tuple(colour)[:3]
    g = tuple(fog_rgb)[:3]
    t = float(f)
    return tuple(max(0.0, c[i] + t * (g[i] - c[i])) for i in range(3))


def albedo_correction(shaderset: str, base_color_factor, color1=(1.0, 1.0, 1.0)):
    """The per-channel factor an importer must apply to a material it built from
    `base_color_factor`, to land on what the shipped shader computes.

    Returns `(scale_rgb, why)`; `(None, why)` when the shaderset is not in the
    disassembled table -- in which case the caller must change nothing, because
    an unmeasured shaderset has no shader-confirmed answer.

    ⚠ `base_color_factor` is the material record's `bakecolor`.  For
    `6f67762bf83d59fd` the shader has NO material constant buffer, so that value
    provably never reaches the GPU: the correction below is not a tweak of a
    nearly-right number, it is the replacement of a number that was never the
    right kind of thing.
    """
    row = SHADERSET_TERMS.get(shaderset)
    if row is None:
        return None, f"shaderset {shaderset} has not been disassembled"
    if "albedo_scale" not in row or row["albedo_scale"] is None:
        return None, f"shaderset {shaderset} carries no albedo term"
    target = row["diffuse_global"] * row["plate_coeff"] * blend_multiplier(color1)
    bcf = tuple(base_color_factor)[:3]
    out = []
    for c in range(3):
        b = float(bcf[c])
        if b <= 0.0:
            return None, (f"base_color_factor[{c}] is {b}; a multiplicative "
                          f"correction cannot recover from zero")
        out.append(target / b)
    why = (f"{row['name']}: plate_coeff {row['plate_coeff']} x diffuse_global "
           f"{row['diffuse_global']} x blend {blend_multiplier(color1)} = "
           f"{target:.5f} against base_color_factor "
           f"({bcf[0]:.5f}, {bcf[1]:.5f}, {bcf[2]:.5f})")
    return tuple(out), why


# =============================================================================
# The level's own lights, as geometry
# =============================================================================

def _norm(v):
    n = math.sqrt(sum(c * c for c in v))
    if n <= 0.0:
        raise ValueError("zero-length direction")
    return tuple(c / n for c in v)


def ndotl_at(surface_normal, light_direction):
    """`dot(N, L)` for a DIRECTIONAL light, where `light_direction` is the
    record's own `direction` field -- the direction the light TRAVELS, so `L`,
    the vector toward the source, is its negation (`le_mesh.lights`: the stored
    direction is the light's local +Z, confirmed `R(orientation)*(0,0,1)` on
    118/118 shipped records)."""
    n = _norm(surface_normal)
    L = _norm(tuple(-c for c in light_direction))
    return sum(a * b for a, b in zip(n, L))


def sub_observer_normal(body_direction):
    """The surface normal at the point of a distant body that faces a viewer at
    the origin: the negation of the direction to the body."""
    return tuple(-c for c in _norm(body_direction))


def body_is_sunlit(body_direction, light_directions):
    """`(lit, rows)` for a distant body seen from the origin.

    `rows` is one `(ndotl, wrap, dark)` per directional light, so a caller can
    report the arithmetic rather than the verdict.  `lit` is True when ANY light
    puts a non-zero wrapped term on the sub-observer point.
    """
    n = sub_observer_normal(body_direction)
    rows = []
    for d in light_directions:
        nl = ndotl_at(n, d)
        w = wrap_diffuse(nl)
        rows.append((nl, w, w <= 0.0))
    return (any(not r[2] for r in rows), rows)
