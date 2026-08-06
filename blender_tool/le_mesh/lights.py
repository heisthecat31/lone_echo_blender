"""le_mesh.lights — CGSceneData light table decode + game->Blender unit conversion.

Pure stdlib. No Oodle, no bpy, no numpy. Decodes the `lights` member of a
Lone Echo (Win7) `CGSceneResourceWin7` payload and converts each record into
Blender light parameters.

==============================================================================
WHERE THE LIGHTS LIVE  (`stream-confirmed`)
==============================================================================
`CGSceneData` (`name-confirmed`) begins

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
level scenes and lands byte-exactly on the `actors` table, whose rows join the
transform/model manifests.

NOTE (Echo VR divergence): the later engine revision's `SGLightParams` is
**360 B**, not 352. Do not reuse that stride here.

==============================================================================
RECORD LAYOUT — SGLightParams, 352 B  (`name-confirmed` + `stream-confirmed`)
==============================================================================
    +0x000 u32    options            CFlagsT<u32>, ELightOptions (see below)
    +0x004 u32    lighttype          ELightType 0=point 1=spot 2=directional
    +0x008 3xf32  pos                WORLD position (scene space == scatter space)
    +0x014 3xf32  primarycolor       LINEAR HDR RGB, intensity PRE-MULTIPLIED IN
    +0x020 3xf32  secondarycolor     (1,1,1) on 118/118 shipped records
    +0x02c 4xf32  attenuation        (volume-inner, volume-mid, RANGE/cull,
                                     MAXFADEDISTANCE) -- see notes
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

★ ALL FOUR COMPONENTS ARE NOW ACCOUNTED FOR (`shader-confirmed`, Echo r15
shader corpus `2799580733489822`; see `LightRecord.maxfadedistance` for the
quotes and the cross-era caveat):
  * `.z` is the RANGE, used by the offline irradiance baker as a hard cull
    (`dist > attenuation.z -> continue`) and as the light-offset normaliser. It
    equals `farp` on 118/118, which is why it is also the shadow far plane.
  * `.w` is `maxfadedistance` -- the argument the attenuation curve's zero-offset
    is computed from, at both of the baker's `LightAttenuation` call sites. It is
    NOT a second cull radius; that guess is now RETIRED.
  * `.x`/`.y`/`.z` are additionally the three stops of the VOLUME-light ramp
    `VolumeLightAttenutation(dist, atten, c0, c1)`, which is what
    makes `.x == 1.0` and `.y == (.x + .z)/2` the invariants they are -- they are
    an authored inner stop and its midpoint, not padding.

==============================================================================
UNITS  (`shader-confirmed` against the RAD engine's own HLSL; see
docs/LIGHTING.md)
==============================================================================
The engine's lighting shader (RAD Engine 3.0) computes:

    float LightAttenuation(float distance, float attenuation,
                           float faderangeoffset, bool pointlight) {
        // 1 / d^x (maya supports 0, 1, 2, 3)
        float atten = rcp(pow(distance, attenuation)) - faderangeoffset;
        if (attenuation == 0.0f && pointlight)
            atten = saturate(1.0f - distance / faderangeoffset);
        return min(atten, 10000.0f);
    }
    ...
    params.lightcolor = PrimaryColor(light) * atten * visibility;   // spot
    params.lightcolor = PrimaryColor(light);                        // directional

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

STRIDE = 0x160          # sizeof(SGLightParams), Win7 / Lone Echo (r14)
STRIDE_R15 = 0x168      # Echo VR / Quest (r15) -- NOT used here, documented to avoid mix-ups

# ELightType (`name-confirmed`)
ePointLight = 0
eSpotLight = 1
eDirectionalLight = 2
LIGHT_TYPE_NAME = {0: "ePointLight", 1: "eSpotLight", 2: "eDirectionalLight"}

# ELightAttenuation (`name-confirmed`) -- `attenmethod` is this enum stored
# as a FLOAT
eNoLightAttenuation = 0
eLinearLightAttenuation = 1
eQuadraticLightAttenuation = 2
eCubicLightAttenuation = 3

# ELightOptions (`name-confirmed`)
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
# `mesh_builder._axis_matrix` (AXIS_CALIBRATION.md); `tests/test_lights.py`
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
        """The light's reach = attenuation.z (== farp on 118/118 shipped records).

        This is the CULL radius: the offline irradiance baker skips a receiver
        outright once `dist > attenuation.z` (`shader-confirmed`) and normalises
        the light-offset ramp by it. It is NOT the value the attenuation curve
        is offset by -- that is `maxfadedistance`, below."""
        return self.attenuation[2]

    @property
    def maxfadedistance(self) -> float:
        """attenuation.w -- the distance the 1/d^m curve is offset to reach 0 at.

        ★ `shader-confirmed`, and it closes what this file used to carry as
        `unresolved`.  The offline irradiance baker declares the identical
        `float4 attenuation` and passes `.w`, not `.z`, as `maxfadedistance`:

            float atten = LightAttenuation(dist, light.attenmethod,
                                           light.attenuation.w);

            float LightAttenuation(float distance, float attenuation,
                                   float maxfadedistance) {
                float offset = 1.0f / pow(abs(maxfadedistance), attenuation);
                float atten  = (1.0f / pow(distance, attenuation)) - offset;
                if(attenuation == 0.0f)
                    atten = saturate(1.0f - distance / maxfadedistance);
                return min(atten, 10000.0f);
            }

        The runtime agrees structurally: `SGForwardLight` carries `MaxRange` and
        `FadeRangeOffset` as two INDEPENDENT fp16 values packed in one word
        (`shader-confirmed`), so the range and the offset source were never the
        same field.

        ⚠ ERA: the shader corpus is the Echo r15 dev build `2799580733489822`,
        not Lone Echo's own r14.  Lone Echo corroborates rather than proves it —
        `.w == .z` on 107/118 shipped LE lights, which is exactly what an artist
        leaving the fade at the range produces, and the 11 that differ (e.g.
        `.z = 1000`, `.w = 5000`) are the ones this distinction is for."""
        return self.attenuation[3]

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


# The three 4-byte holes the 352 B grid leaves unnamed. All zero on the shipped
# records we hold, so `encode_light` writes zeros there and the round-trip is
# byte-exact (tests/test_lights.py::test_encode_light_is_byte_exact_on_real_records).
PAD_OFFSETS = (0xA4, 0xCC, 0x154)


def encode_light(rec: LightRecord) -> bytes:
    """Exact inverse of `decode_light` — 352 bytes.

    Exists so fixtures and tests can be built from verified field values without
    touching an archive (and so `decode(encode(r)) == r` pins the field grid).
    The three unnamed pad words (`PAD_OFFSETS`) are written as zero; they are
    zero on every shipped record inspected, so a re-encode of real bytes is
    byte-exact. `stream-confirmed` for the grid; the pads are
    `stream-confirmed` zero on the records we hold and `inferred` in general.
    """
    buf = bytearray(STRIDE)
    p = struct.pack_into
    p("<II", buf, 0x00, rec.options & 0xFFFFFFFF, rec.lighttype & 0xFFFFFFFF)
    p("<3f", buf, 0x08, *rec.pos)
    p("<3f", buf, 0x14, *rec.primarycolor)
    p("<3f", buf, 0x20, *rec.secondarycolor)
    p("<4f", buf, 0x2C, *rec.attenuation)
    p("<4f", buf, 0x3C, *rec.orientation)
    p("<f", buf, 0x4C, rec.fovy)
    p("<f", buf, 0x50, rec.nearp)
    p("<f", buf, 0x54, rec.farp)
    p("<f", buf, 0x58, rec.filtersize)
    p("<3f", buf, 0x5C, *rec.direction)
    p("<2f", buf, 0x68, *rec.penumbra)
    p("<f", buf, 0x70, rec.falloff)
    p("<f", buf, 0x74, rec.attenmethod)
    p("<f", buf, 0x78, rec.bias)
    p("<f", buf, 0x7C, rec.shadowfadestart)
    p("<f", buf, 0x80, rec.shadowfadeend)
    p("<f", buf, 0x84, rec.shadowthrottledist)
    f = rec.fade
    p("<6f", buf, 0x88, f["proximityend"], f["proximitystart"], f["proximityintensity"],
      f["distantstart"], f["distantend"], f["distantintensity"])
    p("<I", buf, 0xA0, int(f["fadetype"]) & 0xFFFFFFFF)
    p("<f", buf, 0xA8, rec.shadowresolution)
    p("<f", buf, 0xAC, rec.shadowoffsetscale)
    p("<f", buf, 0xB0, rec.lightoffsetstart)
    p("<f", buf, 0xB4, rec.lightoffsetdist)
    ls = rec.lightshaft
    p("<4f", buf, 0xB8, ls["intensity"], ls["startoffset"], ls["fadeinlen"], ls["offset"])
    p("<i", buf, 0xC8, int(ls["slices"]))
    p("<Q", buf, 0xD0, int(ls["goboassetid"]) & 0xFFFFFFFFFFFFFFFF)
    p("<f", buf, 0xD8, rec.airlightminradius)
    p("<III", buf, 0xDC, rec.lightmask & 0xFFFFFFFF, rec.visindex & 0xFFFFFFFF,
      rec.qualitylevel & 0xFFFFFFFF)
    p("<Q", buf, 0xE8, rec.quantizer & 0xFFFFFFFFFFFFFFFF)
    p("<ii", buf, 0xF0, int(rec.signal[0]), int(rec.signal[1]))
    p("<8f", buf, 0xF8, *[float(v) for v in rec.signal[2:10]])
    p("<2f", buf, 0x118, *rec.shadowangularfade)
    p("<Q", buf, 0x120, rec.name & 0xFFFFFFFFFFFFFFFF)
    mask = bytes(rec.scenemask)
    if len(mask) != 0x28:
        raise ValueError(f"scenemask must be 0x28 B, got {len(mask)}")
    buf[0x128:0x150] = mask
    p("<I", buf, 0x150, rec.shadowqualitylevel & 0xFFFFFFFF)
    p("<I", buf, 0x158, rec.cachedjointidx & 0xFFFFFFFF)
    p("<I", buf, 0x15C, rec.jointoffsetidx & 0xFFFFFFFF)
    return bytes(buf)


def record_from_fields(d: dict, index: int = 0) -> LightRecord:
    """Build a `LightRecord` from a plain field dict (an already-decoded dump or
    a `lights.json` entry). Round-trips through `encode_light`/`decode_light`, so
    a fixture built this way is guaranteed consistent with the 352 B grid.

    Accepts either `options`/`lighttype` (raw) or the sidecar's `options_raw`,
    and hex strings for the two CSymbol64 fields.
    """
    def sym(v):
        return int(v, 16) if isinstance(v, str) else int(v)

    ls = dict(d.get("lightshaft", {}))
    ls.setdefault("intensity", 0.0)
    ls.setdefault("startoffset", 0.0)
    ls.setdefault("fadeinlen", 0.0)
    ls.setdefault("offset", 0.0)
    ls.setdefault("slices", 0)
    ls["goboassetid"] = sym(ls.get("goboassetid", NULL_SYMBOL))
    fade = dict(d.get("fade", {}))
    for k in ("proximityend", "proximitystart", "proximityintensity",
              "distantstart", "distantend", "distantintensity"):
        fade.setdefault(k, 0.0)
    fade["fadetype"] = int(fade.get("fadetype", 0))
    sig = tuple(d.get("signal", (0, 0) + (0.0,) * 8))
    mask = d.get("scenemask", b"\x00" * 0x28)
    if isinstance(mask, str):
        mask = bytes.fromhex(mask)

    # `options` may be the raw word or the decoded name list (sidecar form)
    opts = d.get("options_raw", d.get("options", 0))
    if isinstance(opts, (list, tuple)):
        names = set(opts)
        opts = sum(b for b, n in OPTION_NAMES if n in names)
    elif isinstance(opts, str):
        names = set(opts.split("|"))
        opts = sum(b for b, n in OPTION_NAMES if n in names)

    # `lighttype` may be the raw enum or its name (`type` / `lighttype_name`)
    lt = d.get("lighttype")
    if lt is None:
        tn = d.get("lighttype_name") or d.get("type") or ""
        lt = next((k for k, v in LIGHT_TYPE_NAME.items() if v == tn), 0)

    rec = LightRecord(
        index=int(d.get("index", index)),
        options=int(opts),
        lighttype=int(lt),
        pos=tuple(d["pos"]), primarycolor=tuple(d["primarycolor"]),
        secondarycolor=tuple(d.get("secondarycolor", (1.0, 1.0, 1.0))),
        attenuation=tuple(d["attenuation"]), orientation=tuple(d["orientation"]),
        fovy=float(d.get("fovy", 0.0)), nearp=float(d.get("nearp", 0.0)),
        farp=float(d.get("farp", 0.0)), filtersize=float(d.get("filtersize", 0.0)),
        direction=tuple(d["direction"]), penumbra=tuple(d.get("penumbra", (-1.0, -1.0))),
        falloff=float(d.get("falloff", 0.0)),
        attenmethod=float(d.get("attenmethod", 2.0)),
        bias=float(d.get("bias", 0.0)),
        shadowfadestart=float(d.get("shadowfadestart", 0.0)),
        shadowfadeend=float(d.get("shadowfadeend", 0.0)),
        shadowthrottledist=float(d.get("shadowthrottledist", 0.0)),
        fade=fade,
        shadowresolution=float(d.get("shadowresolution", 0.0)),
        shadowoffsetscale=float(d.get("shadowoffsetscale", 0.0)),
        lightoffsetstart=float(d.get("lightoffsetstart", 0.0)),
        lightoffsetdist=float(d.get("lightoffsetdist", 0.0)),
        lightshaft=ls,
        airlightminradius=float(d.get("airlightminradius", 0.0)),
        lightmask=int(d.get("lightmask", 0)), visindex=int(d.get("visindex", NULL_U32)),
        qualitylevel=int(d.get("qualitylevel", 0)),
        quantizer=sym(d.get("quantizer", NULL_SYMBOL)),
        signal=sig, shadowangularfade=tuple(d.get("shadowangularfade", (0.0, 0.0))),
        name=sym(d.get("name", 0)), scenemask=bytes(mask),
        shadowqualitylevel=int(d.get("shadowqualitylevel", 0)),
        cachedjointidx=int(d.get("cachedjointidx", NULL_U32)),
        jointoffsetidx=int(d.get("jointoffsetidx", NULL_U32)),
    )
    # normalise float precision through the on-disk grid so the fixture is
    # exactly what a real decode would have produced
    return decode_light(encode_light(rec), rec.index)


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
    as the static-instance scatter (export-validated: station_front's 47 lights
    all fall inside the 21,394-instance scatter bbox), so there is no
    CTransformCR join and no parent chain to resolve."""
    return to_blender_vec(rec.pos)


def falloff_is_physical(rec: LightRecord) -> bool:
    """True when attenmethod == 2 (inverse-square) -- i.e. Blender's native
    falloff reproduces the engine exactly (up to the range offset). Anything
    else (1 = Maya linear decay, 0 = none) needs a Cycles Light Falloff node."""
    return abs(rec.attenmethod - 2.0) < 1e-6


def range_offset(rec: LightRecord) -> float:
    """The shader's `faderangeoffset`: `1 / maxfadedistance^attenmethod`.

    ★ The argument is `attenuation.w`, NOT `.z`.  This used to read `.z` while
    `.w` sat in `not_derivable()` labelled `unresolved`; the offline irradiance
    baker settles it — its `LightAttenuation` definition and both of its call
    sites take `.w` (see `LightRecord.maxfadedistance`).  On the 107/118 shipped
    LE lights where `.w == .z` nothing changes; on the 11 that differ it does —
    e.g. a spot with `.z = 1000`, `.w = 5000`, `attenmethod = 1` moves the
    subtracted offset from 1e-3 to 2e-4, and the light no longer reaches exactly
    zero at `.z` (the baker CULLS it there instead, which is why the two fields
    are separate at all).

    Still `inferred` in one respect: the runtime value is packed by
    `SGForwardLight::SetFromLight` (`name-only`, not in the shader corpus), so the
    formula is read off the baker rather than off the runtime packer."""
    r = rec.maxfadedistance
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


def blender_matrix_rows(rec: LightRecord, y_up_to_z_up: bool = True):
    """The light object's WORLD matrix, 4x4 row-major nested tuples.

        M = A @ Translation(pos) @ R(orientation) @ Rx(180 deg)

    * `A` is the ONE axis basis (`mesh_builder._axis_matrix`, +90 deg X, det +1,
      game (x,y,z) -> (x,-z,y)); passing `y_up_to_z_up=False` makes it identity.
      Using the same A the meshes use is what keeps the light rig aligned with
      the geometry -- see AXIS_CALIBRATION.md.
    * `R(orientation)` is the record's own quaternion (x,y,z,w). `pos` and
      `orientation` are already WORLD (no CTransformCR join, no parent chain).
    * `Rx(180 deg)` is the lamp-forward flip: the engine's light forward is its
      local **+Z** (`direction == R(q)*(0,0,1)`, 118/118) while a Blender lamp
      emits along its local **-Z**. Rx(pi) maps -Z to +Z, det +1, no mirror.

    Consequence, and the alignment invariant the tests assert:
      `M[:3,:3] @ (0,0,-1) == blender_direction(rec)`.
    Roll about the axis is unconstrained by the engine (no shipped light has a
    gobo), but taking it from the quaternion keeps the result deterministic.
    """
    x, y, z, w = rec.orientation
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n > 1e-12:
        x, y, z, w = x / n, y / n, z / n, w / n
    # column-major-free: R[r][c]
    R = (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )
    # R @ Rx(pi): negate columns 1 and 2
    RF = tuple(tuple((R[r][c] if c == 0 else -R[r][c]) for c in range(3)) for r in range(3))
    A = ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)) if y_up_to_z_up else \
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    M = tuple(tuple(sum(A[r][k] * RF[k][c] for k in range(3)) for c in range(3))
              for r in range(3))
    t = to_blender_vec(rec.pos) if y_up_to_z_up else tuple(rec.pos)
    return (M[0] + (t[0],), M[1] + (t[1],), M[2] + (t[2],),
            (0.0, 0.0, 0.0, 1.0))


# --- import policy: which lights are safe to import -------------------------
#
# `stream-confirmed`: only 49 of 118 shipped level lights set
# `eEnableDiffuse` (station_front 15/47, bridge_night 28/65); 112/118 set
# `eEnableSpecular`. The specular-only majority exists to put highlights on
# surfaces whose DIFFUSE response is already baked into the lightmap + irradiance
# volumes (86 of 87 lit-surface shaders bind BOTH paths). Blender has neither a
# specular-only lamp nor the baked diffuse underneath, so importing all of them
# DOUBLE-LIGHTS the scene. Default to the diffuse subset; "all" is opt-in.
LIGHT_SET_DIFFUSE = "diffuse"     # DEFAULT -- eEnableDiffuse only
LIGHT_SET_ALL = "all"             # opt-in, double-lights (warns)
LIGHT_SET_ENABLED = "enabled"     # every runtime light, diffuse or not
LIGHT_SETS = (LIGHT_SET_DIFFUSE, LIGHT_SET_ALL, LIGHT_SET_ENABLED)


def select_lights(records, light_set: str = LIGHT_SET_DIFFUSE,
                  skip_disabled: bool = True):
    """Apply the import policy. Returns (kept, stats).

    `stats` reports every reason a record was dropped so the caller can surface
    an honest count instead of silently importing fewer lights than the level has.
    """
    if light_set not in LIGHT_SETS:
        raise ValueError(f"light_set must be one of {LIGHT_SETS}, got {light_set!r}")
    kept = []
    stats = {"total": 0, "kept": 0, "skipped_disabled": 0,
             "skipped_specular_only": 0, "specular_only_kept": 0,
             "diffuse_enabled": 0, "specular_enabled": 0, "light_set": light_set}
    for r in records:
        stats["total"] += 1
        stats["diffuse_enabled"] += 1 if r.affects_diffuse else 0
        stats["specular_enabled"] += 1 if r.affects_specular else 0
        if skip_disabled and not r.enabled:
            stats["skipped_disabled"] += 1
            continue
        if light_set == LIGHT_SET_DIFFUSE and not r.affects_diffuse:
            stats["skipped_specular_only"] += 1
            continue
        if not r.affects_diffuse:
            stats["specular_only_kept"] += 1
        kept.append(r)
    stats["kept"] = len(kept)
    return kept, stats


def not_derivable(rec: LightRecord) -> dict:
    """Fields that exist on disk but have NO faithful Blender equivalent, plus
    the ones that are not on disk at all. An importer should carry these onto the
    object as inert custom properties and MUST NOT invent values for them.

    * `filtersize` is a shadow-map PCF filter width in texels -- it is NOT a
      light radius. `SGLightParams` carries no source-size field whatsoever, so
      `shadow_soft_size` is imported as 0 (a true point source). `inferred`.
    * `falloff` is the extra `pow(cos a, falloff)` cone weighting -- Blender's
      spot has no such exponent. `shader-confirmed`, non-zero on 12/118.
    * `faderangeoffset` is computed at runtime by `SGForwardLight::SetFromLight`
      and never stored; see `range_offset()`. `inferred`.
    * `lightmask` / `scenemask` / `visindex` / `qualitylevel` are per-receiver
      and per-scene-set gating with no Blender analogue -- a light with
      `lightmask == 2` lights only receivers carrying bit 1, so importing every
      light over-lights. `shader-confirmed` (lightmask), `name-only` (the rest).
    * `attenuation.w` is RESOLVED -- it is `maxfadedistance` and it now drives
      `range_offset()`. It stays in this dict only as the raw operand of a
      derived value, under its real name.
    * absolute exposure: the game auto-exposes and tonemaps these HDR values, so
      only RATIOS are meaningful. Calibrate Film Exposure once per level.
    """
    return {
        "filtersize_pcf": rec.filtersize,
        "cone_falloff_exponent": rec.falloff,
        "faderangeoffset_runtime": range_offset(rec),
        "lightmask": rec.lightmask,
        "scenemask": rec.scenemask.hex(),
        "visindex": rec.visindex,
        "qualitylevel": rec.qualitylevel,
        "shadowqualitylevel": rec.shadowqualitylevel,
        "attenuation_maxfadedistance": rec.maxfadedistance,
        "affects_diffuse": rec.affects_diffuse,
        "affects_specular": rec.affects_specular,
        "lightshaft_intensity": rec.lightshaft["intensity"],
        "airlightminradius": rec.airlightminradius,
    }


def range_offset_divergence(rec: LightRecord, fraction: float = 0.5):
    """Quantify the ONE systematic brightness error (docs/LIGHTING.md
    §4.5): the engine subtracts `faderangeoffset` so attenuation reaches exactly
    0 at the range; Blender has no such term.

    Returns `(game_over_blender, blender_over_game)` at `fraction * range`.
    For the physical case (`attenmethod == 2`) at half range the engine is at
    `3/R^2` where Blender is at `4/R^2`:
        game/blender  = 0.75  -> the game is 25 % DIMMER than the import
        blender/game  = 1.333 -> the import is 33 % BRIGHTER than the game
    `use_custom_distance` + `cutoff_distance = range` clips the tail but does not
    fix the shape. `shader-confirmed` formula, `inferred` offset.
    """
    if rec.lighttype == eDirectionalLight:
        return 1.0, 1.0
    d = rec.range * fraction
    if d <= 0.0:
        return 1.0, 1.0
    blender = 1.0 / (d ** rec.attenmethod) if rec.attenmethod else 1.0
    game = blender - range_offset(rec)
    if blender <= 0.0:
        return 1.0, 1.0
    ratio = max(0.0, game) / blender
    return ratio, (1.0 / ratio if ratio > 0.0 else float("inf"))


def to_blender(rec: LightRecord, y_up_to_z_up: bool = True) -> dict:
    """Everything an importer needs for one light, in Blender terms."""
    color, peak = normalized_color(rec.primarycolor)
    spot_size, spot_blend = blender_spot(rec)
    return {
        "index": rec.index,
        "name": f"{rec.name:016x}",
        "type": BLENDER_TYPE.get(rec.lighttype, "POINT"),
        "location": blender_position(rec),
        "direction": blender_direction(rec),
        "matrix": blender_matrix_rows(rec, y_up_to_z_up),
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
        "not_derivable": not_derivable(rec),
    }
