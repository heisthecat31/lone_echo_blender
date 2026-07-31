"""le_mesh.lights — CGSceneData light table decode + game->Blender unit conversion.

Pure stdlib. No Oodle, no bpy, no numpy. Decodes the `lights` member of a
Lone Echo (Win7) `CGSceneResourceWin7` payload and converts each record into
Blender light parameters.

⚠ IMPORTING THESE LIGHTS NAIVELY IS WRONG. Most Lone Echo level lights are
SPECULAR-ONLY and sit on top of a BAKED lightmap this tool does not yet import,
so adding them to a Blender scene double-lights it. Read `docs/LIGHTING.md`
before wiring this into an importer. The decoder ships; a light importer does not.

==============================================================================
WHERE THE LIGHTS LIVE
==============================================================================
`CGSceneData` begins

    +0x000 SBVHTreeData                bvhtreedata
    +0x040 CTable<SGLightParams>       lights          <-- THIS MODULE
    +0x078 CTable<SGVolumetricLightParams> vlights
    +0x0b0 CTable<SGAtmosphericVolumeParams> avolumes
    +0x0e8 CTable<C3Vector>            dirlightdirections
    +0x120 CTable<unsigned int>        dirlightindices
    +0x158 CGIrradianceVolumesData     ivdata
    ...

and its serializer walks the members in declaration order, so on disk the scene
payload is

    [SBVHTreeData]
    [u32 count][count * 352 B SGLightParams]   <-- the lights table
    [u32 count][count * 272 B SGVolumetricLightParams]
    ...

`SGLightParams` stride 0x160 == 352 B is confirmed out of shipped bytes: the
member walk continues past the lights table with that stride across 28 shipped
level scenes and lands byte-exactly on the `actors` table.

NOTE (Echo VR divergence): the later engine revision's `SGLightParams` is
**360 B**, not 352. Do not reuse that stride here.

==============================================================================
RECORD LAYOUT — SGLightParams, 352 B
==============================================================================
    +0x000 u32    options            CFlagsT<u32>, ELightOptions (see below)
    +0x004 u32    lighttype          ELightType 0=point 1=spot 2=directional
    +0x008 3xf32  pos                WORLD position (scene space == scatter space)
    +0x014 3xf32  primarycolor       LINEAR HDR RGB, intensity PRE-MULTIPLIED IN
    +0x020 3xf32  secondarycolor     (1,1,1) on 118/118 shipped records
    +0x02c 4xf32  attenuation        (1.0, mean, range, maxrange) -- see notes
    +0x03c 4xf32  orientation        quaternion (x,y,z,w); light forward = R*+Z
    +0x04c f32    fovy               FULL spot cone angle, radians
    +0x050 f32    nearp              shadow-map near plane
    +0x054 f32    farp               shadow-map far plane == attenuation.z
    +0x058 f32    filtersize         shadow PCF filter size (NOT a light radius)
    +0x05c 3xf32  direction          == R(orientation) * (0,0,1), normalised
    +0x068 2xf32  penumbra           (cos inner half-angle, cos outer half-angle);
                                     (-1,-1) for point/directional
    +0x070 f32    falloff            extra pow(cos a, falloff) cone weighting
    +0x074 f32    attenmethod        FLOAT exponent m in 1/d^m (Maya decay rate)
    +0x078 f32    bias               shadow depth bias
    +0x07c f32    shadowfadestart
    +0x080 f32    shadowfadeend
    +0x084 f32    shadowthrottledist
    +0x088 SFadeParams (0x20)        proximityend/start/intensity,
                                     distantstart/end/intensity, u32 fadetype, pad
    +0x0a8 f32    shadowresolution
    +0x0ac f32    shadowoffsetscale
    +0x0b0 f32    lightoffsetstart   shader LightOffset(dist, start, dist)
    +0x0b4 f32    lightoffsetdist
    +0x0b8 SLightShaftProps (0x20)   intensity/startoffset/fadeinlen/offset,
                                     i32 slices, u32 pad, u64 goboassetid
    +0x0d8 f32    airlightminradius
    +0x0dc u32    lightmask          16-bit set mask ANDed with the receiver mask
    +0x0e0 u32    visindex           visibility-system index (0xffffffff = none)
    +0x0e4 u32    qualitylevel       EGfxLightQualityLevel gate
    +0x0e8 u64    quantizer          CSymbol64 (0xffff.. = none on all 118)
    +0x0f0 CSignalTransform (0x28)   mode,timebased,range(2f),initial(2f),
                                     cps,cyclelimit,input,result
    +0x118 2xf32  shadowangularfade
    +0x120 u64    name               CSymbol64 light name
    +0x128 SSceneSetMask (0x28)      scene-set visibility mask (verbatim)
    +0x150 u32    shadowqualitylevel
    +0x154 SPad<4>                   zero on 118/118
    +0x158 u32    cachedjointidx     0xffffffff on 118/118 (level lights)
    +0x15c u32    jointoffsetidx     0xffffffff on 118/118

Corpus invariants measured on 118 shipped lights (station_front 47, bridge_night
65, gpr_020 3, mnu_master 3) -- all 118/118 unless noted:
  * direction == R(orientation) * (0,0,1)          (max err 2.5e-07)
  * farp == attenuation.z
  * attenuation.x == 1.0
  * attenuation.y == (attenuation.x + attenuation.z) / 2   (derived, not authored)
  * attenuation.w == attenuation.z                 (107/118; the other 11 differ)
  * 2*acos(penumbra.y) == fovy                     (106/106 spots)
  * penumbra == (-1,-1)                            (12/12 non-spots)
  * pad@0x154 == 0
So `attenuation.z` is THE light range (== the shader's MaxRange); `.y` is a
derived midpoint; `.w` is `unresolved` (a separate cull radius, most likely).

==============================================================================
UNITS  (see `docs/LIGHTING.md`)
==============================================================================
The engine's distance attenuation, restated as pseudocode:

    atten(d) = clamp(1 / d**attenmethod - faderangeoffset, 0, 10000)
    # attenmethod is a Maya-style decay exponent (0, 1, 2 or 3) stored as a float;
    # faderangeoffset is a runtime constant chosen so atten(range) == 0.
    # point / spot:  lightcolor = primarycolor * atten(d) * visibility
    # directional:   lightcolor = primarycolor            (no distance term)

So the irradiance a surface receives is `primarycolor / d^attenmethod` (minus the
range offset), and `primarycolor` is a LINEAR HDR radiometric scale with the
authored intensity already folded in -- there is NO separate intensity float in
the record. Blender's point/spot lamps use `radiance = P*C / (4*pi*d^2)`, hence

    energy_W       = 4*pi * max(primarycolor)      (POINT and SPOT)
    energy_W_per_m2= max(primarycolor)             (SUN -- no distance term)
    color          = primarycolor / max(primarycolor)

Both sides are LINEAR, so no sRGB transform is applied anywhere.
"""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

STRIDE = 0x160          # sizeof(SGLightParams), Lone Echo (Win7 build)
STRIDE_R15 = 0x168      # the later Echo VR revision -- NOT used here; kept so the
                        # two strides can never be confused

# ELightType
ePointLight = 0
eSpotLight = 1
eDirectionalLight = 2
LIGHT_TYPE_NAME = {0: "ePointLight", 1: "eSpotLight", 2: "eDirectionalLight"}

# ELightAttenuation -- `attenmethod` is this enum stored as a FLOAT
eNoLightAttenuation = 0
eLinearLightAttenuation = 1
eQuadraticLightAttenuation = 2
eCubicLightAttenuation = 3

# ELightOptions
eEnableDiffuse = 1 << 0
eEnableSpecular = 1 << 1
eCastShadows = 1 << 2
eCastLevelShadows = 1 << 3
eCastActorShadows = 1 << 4
eLightTransparents = 1 << 5
eLightOpaques = 1 << 6
eLightParticles = 1 << 7
eLightEnabled = 1 << 8
eUseLightShaft = 1 << 9
eUseLightShaftShadows = 1 << 10
eUseFog = 1 << 11
eBakeDirect = 1 << 12
eBakeIndirect = 1 << 13
eUseNonUniformFog = 1 << 14
eCastOpaqueShadows = 1 << 15
eCastAlphaTestShadows = 1 << 16
eCastTransparentShadows = 1 << 17
eBakeOnlyIrradiance = 1 << 18
eDontBakeIrradiance = 1 << 19
ePrimaryDirLight = 1 << 20
eEyesOnlyLight = 1 << 21
eBakeShadow = 1 << 22
eLightVolumetrics = 1 << 23
eCastAllLevelShadows = 1 << 24

OPTION_NAMES = [
    (eEnableDiffuse, "eEnableDiffuse"), (eEnableSpecular, "eEnableSpecular"),
    (eCastShadows, "eCastShadows"), (eCastLevelShadows, "eCastLevelShadows"),
    (eCastActorShadows, "eCastActorShadows"), (eLightTransparents, "eLightTransparents"),
    (eLightOpaques, "eLightOpaques"), (eLightParticles, "eLightParticles"),
    (eLightEnabled, "eLightEnabled"), (eUseLightShaft, "eUseLightShaft"),
    (eUseLightShaftShadows, "eUseLightShaftShadows"), (eUseFog, "eUseFog"),
    (eBakeDirect, "eBakeDirect"), (eBakeIndirect, "eBakeIndirect"),
    (eUseNonUniformFog, "eUseNonUniformFog"), (eCastOpaqueShadows, "eCastOpaqueShadows"),
    (eCastAlphaTestShadows, "eCastAlphaTestShadows"),
    (eCastTransparentShadows, "eCastTransparentShadows"),
    (eBakeOnlyIrradiance, "eBakeOnlyIrradiance"), (eDontBakeIrradiance, "eDontBakeIrradiance"),
    (ePrimaryDirLight, "ePrimaryDirLight"), (eEyesOnlyLight, "eEyesOnlyLight"),
    (eBakeShadow, "eBakeShadow"), (eLightVolumetrics, "eLightVolumetrics"),
    (eCastAllLevelShadows, "eCastAllLevelShadows"),
]

NULL_SYMBOL = 0xFFFFFFFFFFFFFFFF
NULL_U32 = 0xFFFFFFFF

# The ONE Y-up -> Z-up basis: a pure +90 deg rotation about X, det +1,
# game (x, y, z) -> Blender (x, -z, y). Identical to
# `addon/lone_echo_import/scatter_reader.basis_matrix()` and to
# `mesh_builder._axis_matrix`; `tests/test_lights.py`
# asserts the equality against scatter_reader so the two can never drift.


def to_blender_vec(v):
    """Game (x, y, z) -> Blender (x, -z, y). Same basis as scatter_reader."""
    return (v[0], -v[2], v[1])


def option_names(options: int) -> list:
    """Decoded ELightOptions names, plus 'unk:0x..' for any leftover bits."""
    out = [n for b, n in OPTION_NAMES if options & b]
    rest = options & ~sum(b for b, _ in OPTION_NAMES)
    if rest:
        out.append(f"unk:0x{rest:x}")
    return out


@dataclass
class LightRecord:
    """One decoded SGLightParams. Vectors are tuples in NATIVE GAME SPACE."""
    index: int
    options: int
    lighttype: int
    pos: tuple
    primarycolor: tuple
    secondarycolor: tuple
    attenuation: tuple
    orientation: tuple           # quaternion (x, y, z, w)
    fovy: float
    nearp: float
    farp: float
    filtersize: float
    direction: tuple
    penumbra: tuple
    falloff: float
    attenmethod: float
    bias: float
    shadowfadestart: float
    shadowfadeend: float
    shadowthrottledist: float
    fade: dict
    shadowresolution: float
    shadowoffsetscale: float
    lightoffsetstart: float
    lightoffsetdist: float
    lightshaft: dict
    airlightminradius: float
    lightmask: int
    visindex: int
    qualitylevel: int
    quantizer: int
    signal: tuple
    shadowangularfade: tuple
    name: int
    scenemask: bytes
    shadowqualitylevel: int
    cachedjointidx: int
    jointoffsetidx: int

    # ---- semantic helpers (all derived, no extra bytes) --------------------
    @property
    def type_name(self) -> str:
        return LIGHT_TYPE_NAME.get(self.lighttype, f"?{self.lighttype}")

    @property
    def enabled(self) -> bool:
        return bool(self.options & eLightEnabled)

    @property
    def affects_diffuse(self) -> bool:
        return bool(self.options & eEnableDiffuse)

    @property
    def affects_specular(self) -> bool:
        return bool(self.options & eEnableSpecular)

    @property
    def bake_only(self) -> bool:
        """Authoring-only: contributes to a bake but never runs at runtime."""
        return (not self.enabled) and bool(self.options & (eBakeDirect | eBakeIndirect))

    @property
    def range(self) -> float:
        """The light's reach = attenuation.z (== farp on 118/118 shipped records)."""
        return self.attenuation[2]

    @property
    def option_names(self) -> list:
        return option_names(self.options)

    @property
    def is_joint_attached(self) -> bool:
        return self.cachedjointidx != NULL_U32 or self.jointoffsetidx != NULL_U32


def decode_light(rec: bytes, index: int = 0) -> LightRecord:
    """Decode one 352-byte SGLightParams record."""
    if len(rec) != STRIDE:
        raise ValueError(f"SGLightParams record must be {STRIDE} B, got {len(rec)}")
    u = struct.unpack_from
    options, lighttype = u("<II", rec, 0x00)
    fp = u("<6f", rec, 0x88)
    fadetype = u("<I", rec, 0xA0)[0]
    ls = u("<4f", rec, 0xB8)
    ls_slices = u("<i", rec, 0xC8)[0]
    ls_gobo = u("<Q", rec, 0xD0)[0]
    sig_mode, sig_timebased = u("<ii", rec, 0xF0)
    sig_rest = u("<8f", rec, 0xF8)
    return LightRecord(
        index=index,
        options=options,
        lighttype=lighttype,
        pos=u("<3f", rec, 0x08),
        primarycolor=u("<3f", rec, 0x14),
        secondarycolor=u("<3f", rec, 0x20),
        attenuation=u("<4f", rec, 0x2C),
        orientation=u("<4f", rec, 0x3C),
        fovy=u("<f", rec, 0x4C)[0],
        nearp=u("<f", rec, 0x50)[0],
        farp=u("<f", rec, 0x54)[0],
        filtersize=u("<f", rec, 0x58)[0],
        direction=u("<3f", rec, 0x5C),
        penumbra=u("<2f", rec, 0x68),
        falloff=u("<f", rec, 0x70)[0],
        attenmethod=u("<f", rec, 0x74)[0],
        bias=u("<f", rec, 0x78)[0],
        shadowfadestart=u("<f", rec, 0x7C)[0],
        shadowfadeend=u("<f", rec, 0x80)[0],
        shadowthrottledist=u("<f", rec, 0x84)[0],
        fade=dict(proximityend=fp[0], proximitystart=fp[1], proximityintensity=fp[2],
                  distantstart=fp[3], distantend=fp[4], distantintensity=fp[5],
                  fadetype=fadetype),
        shadowresolution=u("<f", rec, 0xA8)[0],
        shadowoffsetscale=u("<f", rec, 0xAC)[0],
        lightoffsetstart=u("<f", rec, 0xB0)[0],
        lightoffsetdist=u("<f", rec, 0xB4)[0],
        lightshaft=dict(intensity=ls[0], startoffset=ls[1], fadeinlen=ls[2],
                        offset=ls[3], slices=ls_slices, goboassetid=ls_gobo),
        airlightminradius=u("<f", rec, 0xD8)[0],
        lightmask=u("<I", rec, 0xDC)[0],
        visindex=u("<I", rec, 0xE0)[0],
        qualitylevel=u("<I", rec, 0xE4)[0],
        quantizer=u("<Q", rec, 0xE8)[0],
        signal=(sig_mode, sig_timebased) + sig_rest,
        shadowangularfade=u("<2f", rec, 0x118),
        name=u("<Q", rec, 0x120)[0],
        scenemask=bytes(rec[0x128:0x150]),
        shadowqualitylevel=u("<I", rec, 0x150)[0],
        cachedjointidx=u("<I", rec, 0x158)[0],
        jointoffsetidx=u("<I", rec, 0x15C)[0],
    )


def decode_lights_table(buf, off: int = 0, max_count: int = 1_000_000):
    """Decode a `CTable<SGLightParams>` at `off`: [u32 count][count * 352 B].

    Returns (records, end_offset). Raises on a count that cannot fit in `buf`.
    """
    count = struct.unpack_from("<I", buf, off)[0]
    if count > max_count:
        raise ValueError(f"lights count {count} exceeds sanity limit {max_count}")
    data = off + 4
    end = data + count * STRIDE
    if end > len(buf):
        raise ValueError(f"lights table end {end} past buffer {len(buf)}")
    return [decode_light(bytes(buf[data + i * STRIDE:data + (i + 1) * STRIDE]), i)
            for i in range(count)], end


# =============================================================================
# Game -> Blender conversion
# =============================================================================

BLENDER_TYPE = {ePointLight: "POINT", eSpotLight: "SPOT", eDirectionalLight: "SUN"}


def normalized_color(primarycolor):
    """(linear rgb normalised to peak 1.0, peak). Peak 0 -> black, peak 0."""
    peak = max(primarycolor)
    if peak <= 0.0:
        return (0.0, 0.0, 0.0), 0.0
    return tuple(c / peak for c in primarycolor), peak


def blender_energy(rec: LightRecord) -> float:
    """Blender light `energy`.

    POINT/SPOT -> watts:  Blender radiance = P*C/(4*pi*d^2); the engine's is
                          primarycolor/d^2, so P = 4*pi*peak.
    SUN        -> W/m^2:  the engine applies no distance term to a directional
                          light (`params.lightcolor = PrimaryColor(light)`), and
                          Blender's sun `energy` is irradiance, so P = peak.
    """
    _, peak = normalized_color(rec.primarycolor)
    if rec.lighttype == eDirectionalLight:
        return peak
    return 4.0 * math.pi * peak


def blender_spot(rec: LightRecord):
    """(spot_size, spot_blend) for a spot; (0.0, 0.0) for other types.

    spot_size  = fovy exactly (both are the FULL cone angle in radians; the
                 corpus confirms fovy == 2*acos(penumbra.y) on 106/106 spots).
    spot_blend = 1 - theta_inner/theta_outer, with theta = acos(penumbra.*).
                 APPROXIMATE: the engine blends with a Ken-Perlin smootherstep
                 in COS space between outer and inner, Blender with its own
                 curve, so only the cone edges match exactly, not the ramp.
    """
    if rec.lighttype != eSpotLight:
        return 0.0, 0.0
    ci, co = rec.penumbra[0], rec.penumbra[1]
    if not (-1.0 <= ci <= 1.0) or not (-1.0 <= co <= 1.0):
        return float(rec.fovy), 0.0
    ti, to = math.acos(max(-1.0, min(1.0, ci))), math.acos(max(-1.0, min(1.0, co)))
    blend = 0.0 if to <= 1e-9 else max(0.0, min(1.0, 1.0 - ti / to))
    return float(rec.fovy), blend


def blender_direction(rec: LightRecord):
    """The light's forward axis in Blender space (Z-up).

    The stored `direction` is the light's local +Z in game space (corpus:
    118/118 == R(orientation)*(0,0,1)). Blender lamps point along their local
    -Z, so an importer should orient the object with
    `Vector((0,0,-1)).rotation_difference(Vector(blender_direction(rec)))`.
    Roll is unconstrained and irrelevant: no shipped light has a gobo
    (`lightshaft.goboassetid` is null on 118/118).
    """
    d = rec.direction
    n = math.sqrt(d[0] * d[0] + d[1] * d[1] + d[2] * d[2])
    if n <= 1e-12:
        return (0.0, 0.0, -1.0)
    return to_blender_vec((d[0] / n, d[1] / n, d[2] / n))


def blender_position(rec: LightRecord):
    """World position in Blender space. `pos` is already in the same world space
    as the static-instance scatter (cross-checked: station_front's 47 lights all
    fall inside the same level's 21,394-instance scatter bounding box), so there is
    no transform join and no parent chain to resolve."""
    return to_blender_vec(rec.pos)


def falloff_is_physical(rec: LightRecord) -> bool:
    """True when attenmethod == 2 (inverse-square) -- i.e. Blender's native
    falloff reproduces the engine exactly (up to the range offset). Anything
    else (1 = Maya linear decay, 0 = none) needs a Cycles Light Falloff node."""
    return abs(rec.attenmethod - 2.0) < 1e-6


def range_offset(rec: LightRecord) -> float:
    """The shader's `faderangeoffset`, subtracted so the curve reaches 0 at the
    range: 1/range^attenmethod. UNRESOLVED -- the value is computed by the engine
    at runtime and is NOT stored on disk; this formula is inferred from the shader's
    own description of the term and reproduces atten(range) == 0 exactly."""
    r = rec.range
    if r <= 0.0:
        return 0.0
    return 1.0 / (r ** rec.attenmethod) if rec.attenmethod != 0.0 else r


def engine_irradiance(rec: LightRecord, distance: float):
    """The engine's per-channel `params.lightcolor` at `distance` on the cone
    axis, ignoring shadows/lightoffset. Used by the tests and by anyone
    calibrating a Blender render against the game."""
    if rec.lighttype == eDirectionalLight:
        return tuple(rec.primarycolor)
    if distance <= 0.0:
        return (0.0, 0.0, 0.0)
    atten = 1.0 / (distance ** rec.attenmethod) - range_offset(rec)
    atten = max(0.0, min(10000.0, atten))
    return tuple(c * atten for c in rec.primarycolor)


def to_blender(rec: LightRecord) -> dict:
    """Everything an importer needs for one light, in Blender terms."""
    color, peak = normalized_color(rec.primarycolor)
    spot_size, spot_blend = blender_spot(rec)
    return {
        "index": rec.index,
        "name": f"{rec.name:016x}",
        "type": BLENDER_TYPE.get(rec.lighttype, "POINT"),
        "location": blender_position(rec),
        "direction": blender_direction(rec),
        "color": color,
        "energy": blender_energy(rec),
        "peak_radiance": peak,
        "spot_size": spot_size,
        "spot_blend": spot_blend,
        "shadow_soft_size": 0.0,          # NOT derivable -- no radius on disk
        "use_shadow": bool(rec.options & (eCastShadows | eCastLevelShadows)),
        "cutoff_distance": rec.range,
        "enabled": rec.enabled,
        "affects_diffuse": rec.affects_diffuse,
        "affects_specular": rec.affects_specular,
        "bake_only": rec.bake_only,
        "attenmethod": rec.attenmethod,
        "physical_falloff": falloff_is_physical(rec),
        "cone_falloff_exponent": rec.falloff,   # no Blender equivalent
        "lightmask": rec.lightmask,
        "options": rec.option_names,
    }
