"""Level FOG, TONEMAPPING and PARTICLE placements.

## `CGFSEffectsResource` -- the level's full-screen effect settings

1416 bytes, one per level, 32 in the extract. The engine's own schema names its
contents and their order (`core/types/asset/fullscreeneffects.radattr` ->
`FullScreenEffectSettings`): Ambient, DOFQuality, Bokeh, ToneMapping, Exposure,
Bloom, MotionBlur, SSAO, Fog, ScreenTint, Shadow, FXAA, ... then Alt* variants.

Three blocks are located and verified here. Each was pinned by a distinctive
schema default appearing at exactly ONE offset across all 32 files, with its
declared neighbours agreeing:

    ToneMapping   +0x84  ShoulderStrength   (12.0)
                  +0x88  LinearStrength     (10.0)
                  +0x8c  LinearAngle        (0.148)   <- unique in the file
                  +0x90  ToeStrength        (6.35)    <- unique in the file
                  +0x94  WhitePoint         (3.0)
    Exposure      +0x9c  Exposure
                  +0xa0  AutoExposureKeyValue (0.5)
                  +0xa4  AutoExposureMin      (-10.0)
                  +0xa8  AutoExposureMax      (10.0)
    Fog           +0xd0  Color RGBA
                  +0xe0  Intensity
                  +0xe4  StartDepth         (50.0)
                  +0xe8  EndDepth           (100.0)
                  +0xec  StartHeight        (0.0)
                  +0xf0  EndHeight          (50.0)

⭐ Those five ToneMapping fields are the **Hable / Uncharted-2 filmic curve**
(A, B, C, D, W). The engine tonemaps and exposes downstream of the lightmap,
which is why raw baked values look physically dark -- see `evr_apply_lighting`.

Measured: 24 of 32 levels author non-default fog (`mpl_tutorial_lobby` is pink
at 1.0/0.4/0.5, intensity 0.1, 35->77 m; `mpl_combat_combustion` runs 15->100 m
with EndHeight 200), and the tonemap varies per level too (`mpl_tutorial_arena`
is A=4.22 W=7.0 at exposure 2.5 against the 12/10/0.148/6.35/3.0 default).

    Bloom         +0xb8  (enable-like flag, see below)
                  +0xbc  Magnitude
                  +0xc0  ExposureOffset     (3.0)
                  +0xc4  BlurIterations     (u32, 2 or 4)
                  +0xc8  HiQualitySpread    (5.0)

⭐ **Bloom is located.** An earlier pass rejected this region because it read as
`0.21, 0.21, 0.21` and looked like a ScreenTint colour -- but `WhiteLuminance
:= 0.21` is itself a `BloomParams` default, so that reading was the clue, not
the refutation. The block is pinned by its field SHAPE across all 32 levels:
`Enabled` is 0/1 only, `BlurIterations` takes only 2 and 4, `ExposureOffset`
sits on its 3.0 default on 14 levels and `HiQualitySpread` on its 5.0 default
on 13, and the leading flag being 0 picks out exactly the
same 7 levels as `Magnitude == 0`.

⚠ That leading flag is **not a schema field**. `BloomParams` declares only
Magnitude, ExposureOffset, BlurIterations, HiQuality, HiQualitySpread and the
deprecated WhiteLuminance, and its `FXParams` base is empty. Bools are not
serialized at all -- `ExposureParams` maps onto exactly seven consecutive words
(Exposure, AutoExposureKeyValue, AutoExposureMin, AutoExposureMax,
AdaptationRate, ExposureDelay, ExposureOffset) with its three bools absent,
which is also why `HiQuality` is missing here. So the flag is either a
serialization-level "this block is overridden" word or an FXParams member the
2023 schema dump does not show. It is reported as `enabled` because that is
what it behaves like, and `active` combines it with the magnitude -- but a
consumer should treat `magnitude > 0` as the real test.

The shader (`fsfx` bloom + `ToneMap_Hable`) gives the algorithm outright:

    bloom = blur(CalcExposedColor(colour, exposure - ExposureOffset)) * Magnitude
    graded = ToneMap_Hable(colour + bloom)

i.e. the bright pass exposes with `exp2(exposure - ExposureOffset)`, and the
bloom is added BEFORE the filmic curve, not composited after it.

## The Alt* variants -- present, and unused

`FullScreenEffectSettings` ends with a parallel set: `AltDOF`, `AltExposure`,
`AltBloom`, `AltFog`, `AltScreenTint`, `AltLensFlare`, `AltLensDirt`,
`AltDistortion`, `AltVignette`, `AltFilmGrain`, `AltChromAber`, `AltFX`.

⛔ **There is no `AltToneMapping`** -- the alt list has no tonemap member. A
second block DOES match the tonemap signature (0x324 release / 0x330 summer),
but it reads `2.0, 2.0, 0.5, 45.0, 2.5` identically on all 80 files, and the
schema's `ShadowParams` ends `ShadowIntensity := 0.5, ShadowAngle := 45.0,
ShadowDistance := 2.5`. It is the SHADOW block's tail, matching by coincidence
because 0.5 dips below its neighbours the way `LinearAngle` does. This is why
the locator anchors on Bloom rather than taking a signature match.

⛔ **The Alt blocks are never authored.** `AltFog` is located the same way the
primary is (it is the second fog-shaped block, 0x368 release / 0x378 summer,
found on 80/80 files) and carries the `FogParams` DEFAULTS -- colour
`(1, 1, 1, 0)`, intensity 1.0, 50 -> 100 m, heights 0 / 50 -- on **every file in
both builds**, against 10 and 13 distinct authored value-sets for the primary
fog. The words preceding it are 0.0 with a `0xFFFFFFFF` sentinel. So the alt
region is reserved but unused, and nothing is emitted for it: a consumer would
otherwise get a full set of fog parameters that no level ever applies.

⚠ **These offsets are BUILD-SPECIFIC.** The struct grew between builds and not
uniformly: against the 1416-byte release layout, the 1448-byte summer build
shifts ToneMapping and Bloom by +4 but Fog by +8 (an extra word appears between
them, in MotionBlur/SSAO). So Bloom is located by SIGNATURE rather than by a
fixed offset -- see `find_bloom` -- and the fixed tonemap/fog offsets are keyed
on the resource size (`LAYOUTS`). An unrecognised size still yields bloom, and
reports `layout_known: false` so a consumer knows the rest may be shifted.

## `CParticleEffectCR` -- where the emitters are

Same framing as the other CR component tables:

    header  +0x08 u32 table byte size   (== count * 152)
            +0x28 u32 record count
    record  base 0x38, stride 152
            +0x08 u64 actor nodeid
            +0x20 u64 CGParticleEffectResource asset

`count * 152 == size` exactly on every level tried, and every asset hash
resolves to a real `CGParticleEffectResource`. `mpl_arena_a` has 31 placements
over 4 distinct effects (symmetric, +/-15.98 -- the corner emitters);
`mpl_combat_fission` has 11 over 10.

⚠ This is the PLACEMENT only. The effect definitions live in
`CGParticleEffectResource` (81) and `CGParticleGraphResource` (34), neither of
which is decoded -- so a consumer knows where an emitter is and which asset it
plays, and nothing about what it looks like.
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

#: `CGFSEffectsResourceWin10`.
FS_EFFECTS = "7d687ba03866061e"
#: `CParticleEffectCRWin10`.
PARTICLE_EFFECT_CR = "21a09c2016d8f3e6"

FS_SIZE = 1416

#: Every block is located RELATIVE TO BLOOM, which `find_bloom` finds by shape.
#:
#: ToneMapping and Exposure keep a fixed distance from Bloom across builds --
#: the three move together -- so once Bloom is known they follow exactly. Fog
#: does NOT: it is 0x18 after Bloom in the 1416 release layout and 0x1c in the
#: 1448 summer build, because an extra word appears between them. So Fog is
#: SEARCHED for after Bloom rather than offset from it.
BLOOM_TO_TONEMAP = 0x34
BLOOM_TO_EXPOSURE = 0x1c
#: Start the fog search past Bloom's own five words, so the block cannot match
#: itself.
FOG_SEARCH_FROM = 0x14

#: `BloomParams`, as a run of five words. Offsets are RELATIVE to the block.
BLOOM_FIELDS = (("enabled", 0x00, "I"), ("magnitude", 0x04, "f"),
                ("exposure_offset", 0x08, "f"), ("blur_iterations", 0x0c, "I"),
                ("hi_quality_spread", 0x10, "f"))
BLOOM_DEFAULTS = {"exposure_offset": 3.0, "hi_quality_spread": 5.0}
#: The engine ships only these; anything else is not a BloomParams block.
BLOOM_ITERATIONS = (1, 2, 4, 8)

#: All offsets below are RELATIVE to their block's located base.
TONEMAP_FIELDS = (("shoulder_strength", 0x00), ("linear_strength", 0x04),
                  ("linear_angle", 0x08), ("toe_strength", 0x0c),
                  ("white_point", 0x10))
EXPOSURE_FIELDS = (("exposure", 0x00), ("auto_key_value", 0x04),
                   ("auto_min", 0x08), ("auto_max", 0x0c))
FOG_COLOR = 0x00
FOG_FIELDS = (("intensity", 0x10), ("start_depth", 0x14), ("end_depth", 0x18),
              ("start_height", 0x1c), ("end_height", 0x20))

#: The schema's own defaults, so a consumer can tell "authored" from "untouched".
FOG_DEFAULTS = {"intensity": 1.0, "start_depth": 50.0, "end_depth": 100.0,
                "start_height": 0.0, "end_height": 50.0}

PARTICLE_BASE = 0x38
PARTICLE_STRIDE = 152
P_ACTOR = 0x08
P_EFFECT = 0x20


def _f(blob, off):
    return round(struct.unpack_from("<f", blob, off)[0], 6)


def _u(blob, off):
    return struct.unpack_from("<I", blob, off)[0]


def find_bloom(blob: bytes) -> int | None:
    """Offset of the `BloomParams` block, located by field shape.

    A fixed offset does not survive across builds (see the module docstring), so
    the block is found by the constraints its own fields impose:

        Enabled            u32, 0 or 1 -- and 0 forbids a non-zero Magnitude
        Magnitude          finite, 0..100
        ExposureOffset     finite, >0 .. 32
        BlurIterations     u32 in {1, 2, 4, 8}
        HiQualitySpread    finite, >0 .. 64

    The LAST match is taken. The one false positive this admits sits earlier in
    the struct and OVERLAPS the tonemap block -- its "spread" word is literally
    ShoulderStrength -- so position separates them cleanly. Verified: a single
    candidate at 0xbc on 48/48 summer files, and 0xb8 chosen on 32/32 release
    files.
    """
    best = None
    for off in range(0, len(blob) - 20, 4):
        enabled = _u(blob, off)
        if enabled > 1:
            continue
        if _u(blob, off + 0x0c) not in BLOOM_ITERATIONS:
            continue
        magnitude, exposure = struct.unpack_from("<ff", blob, off + 4)
        spread = struct.unpack_from("<f", blob, off + 0x10)[0]
        if not all(math.isfinite(v) for v in (magnitude, exposure, spread)):
            continue
        if not 0.0 <= magnitude <= 100.0:
            continue
        if not 0.0 < exposure <= 32.0:
            continue
        if not 0.0 < spread <= 64.0:
            continue
        if enabled == 0 and magnitude != 0.0:
            continue
        best = off
    return best


def _is_tonemap(blob: bytes, off: int) -> bool:
    """Does a 5-float run look like the Hable curve (A, B, C, D, W)?

    `LinearAngle` is the discriminator: it runs 0.12..0.20 across every shipped
    level while its neighbours are all >= 1, so an order-of-magnitude dip in the
    middle of the run is what identifies the block.
    """
    if off < 0 or off + 20 > len(blob):
        return False
    a, l, angle, toe, white = struct.unpack_from("<5f", blob, off)
    if not all(math.isfinite(v) for v in (a, l, angle, toe, white)):
        return False
    if not (1.0 <= a <= 64.0 and 1.0 <= l <= 64.0 and 1.0 <= toe <= 64.0):
        return False
    if not 0.05 <= angle <= 0.5 or not 0.0 <= white <= 64.0:
        return False
    return angle < a and angle < l and angle < toe


def _is_fog(blob: bytes, off: int) -> bool:
    """Colour RGBA in 0..1 followed by depths that actually describe a ramp."""
    if off < 0 or off + 36 > len(blob):
        return False
    colour = struct.unpack_from("<4f", blob, off)
    if not all(math.isfinite(c) and 0.0 <= c <= 1.0 for c in colour):
        return False
    inten, start, end, low, high = struct.unpack_from("<5f", blob, off + 16)
    if not all(math.isfinite(v) for v in (inten, start, end, low, high)):
        return False
    if not 0.0 <= inten <= 5.0:
        return False
    if not 1.0 <= start <= 200.0 or not start < end <= 2000.0:
        return False
    return -1000.0 <= low <= 100.0 and 10.0 <= high <= 1000.0


def find_blocks(blob: bytes) -> dict:
    """`{bloom, tonemap, exposure, fog}` offsets; a value is None if unlocated.

    Bloom anchors everything. ToneMapping and Exposure sit a fixed distance
    from it on every build; Fog does not, so it is searched for and validated.
    Each anchored block is checked against its own predicate before being
    accepted, so a shifted layout reports None rather than plausible garbage.
    """
    bloom = find_bloom(blob)
    if bloom is None:
        return {"bloom": None, "tonemap": None, "exposure": None, "fog": None}

    tonemap = bloom - BLOOM_TO_TONEMAP
    if not _is_tonemap(blob, tonemap):
        tonemap = None
    exposure = bloom - BLOOM_TO_EXPOSURE
    if exposure < 0 or exposure + 16 > len(blob):
        exposure = None

    fog = None
    for off in range(bloom + FOG_SEARCH_FROM, len(blob) - 36, 4):
        if _is_fog(blob, off):
            fog = off
            break
    return {"bloom": bloom, "tonemap": tonemap, "exposure": exposure, "fog": fog}


def read_bloom(blob: bytes) -> dict | None:
    """`BloomParams` for one level, or None when the block is not found."""
    base = find_bloom(blob)
    if base is None:
        return None
    out = {}
    for name, rel, kind in BLOOM_FIELDS:
        out[name] = _u(blob, base + rel) if kind == "I" else _f(blob, base + rel)
    out["enabled"] = bool(out["enabled"])
    # `Magnitude == 0` is how a level turns bloom off even with Enabled set --
    # 3 summer levels do exactly that, so a consumer must check the magnitude
    # rather than trust the flag alone.
    out["active"] = out["enabled"] and out["magnitude"] > 0.0
    out["is_default"] = all(abs(out[k] - v) < 1e-4
                            for k, v in BLOOM_DEFAULTS.items())
    out["offset"] = base
    return out


def _resource(root: Path, kind: str, level_hash) -> Path | None:
    """The resource file for a level, tolerating a stripped leading zero.

    Some extracts name a directory entry by the hash with leading zeros DROPPED
    (`8a1af9e108def0b` for `08a1af9e108def0b` -- mpl_combat_war_room), so a
    padded lookup misses it and the level silently loses its fog, tonemap and
    bloom. Try the canonical name first, then the stripped form, then `.bin`.
    """
    from evr_resource_types import normalise_hash, resolve_type_dir

    directory = resolve_type_dir(root, kind)
    canonical = normalise_hash(level_hash)
    for name in (canonical, canonical.lstrip("0"), str(level_hash)):
        for candidate in (directory / name, (directory / name).with_suffix(".bin")):
            if candidate.is_file():
                return candidate
    return None


def read_fx(root: Path, level_hash) -> dict | None:
    """Fog / tonemapping / exposure for one level, or None."""
    path = _resource(root, FS_EFFECTS, level_hash)
    if path is None:
        return None
    blob = path.read_bytes()
    if len(blob) < FS_SIZE:
        return None

    # Every block is LOCATED, never assumed: offsets move between builds and
    # not by a common amount, so a hardcoded table silently misreads any build
    # it was not written for.
    blocks = find_blocks(blob)

    out = {
        # Where each block was found, so a consumer can audit the decode rather
        # than trust it. A None here means that block was not located and is
        # absent from the payload entirely.
        "blocks": {k: (None if v is None else v) for k, v in blocks.items()},
        "resource_size": len(blob),
        "layout_known": all(v is not None for v in blocks.values()),
    }

    if blocks["fog"] is not None:
        base = blocks["fog"]
        fog = {name: _f(blob, base + off) for name, off in FOG_FIELDS}
        fog["color"] = [_f(blob, base + FOG_COLOR + 4 * i) for i in range(4)]
        fog["is_default"] = all(
            abs(fog[k] - v) < 1e-4 for k, v in FOG_DEFAULTS.items())
        out["fog"] = fog
    if blocks["tonemap"] is not None:
        base = blocks["tonemap"]
        out["tonemap"] = {name: _f(blob, base + off)
                          for name, off in TONEMAP_FIELDS}
    if blocks["exposure"] is not None:
        base = blocks["exposure"]
        out["exposure"] = {name: _f(blob, base + off)
                           for name, off in EXPOSURE_FIELDS}
    bloom = read_bloom(blob)
    if bloom is not None:
        out["bloom"] = bloom
    return out


def read_particles(root: Path, members) -> list:
    """`[{actor, effect, level}, ...]` across the scene group."""
    out = []
    for member in members:
        path = _resource(root, PARTICLE_EFFECT_CR, member)
        if path is None:
            continue
        blob = path.read_bytes()
        if len(blob) < PARTICLE_BASE + 4:
            continue
        size = struct.unpack_from("<I", blob, 0x08)[0]
        count = struct.unpack_from("<I", blob, 0x28)[0]
        if not count or size != count * PARTICLE_STRIDE:
            continue                    # framing did not check out -- skip
        if PARTICLE_BASE + size > len(blob):
            continue
        for i in range(count):
            off = PARTICLE_BASE + i * PARTICLE_STRIDE
            out.append({
                "actor": str(struct.unpack_from("<Q", blob, off + P_ACTOR)[0]),
                "effect": "%016x" % struct.unpack_from("<Q", blob, off + P_EFFECT)[0],
                "level": member,
            })
    return out


def sidecar(root: Path, level_hash, members, actor_positions: dict | None = None) -> dict:
    """The full `effects.json` payload."""
    fx = read_fx(root, level_hash) or {}
    placements = read_particles(root, members)
    if actor_positions:
        for p in placements:
            pos = actor_positions.get(int(p["actor"]))
            if pos:
                p["position"] = [round(v, 5) for v in pos]
    payload = {
        "format": "evr_effects",
        "version": 1,
        "note": ("fog / tonemap / exposure from CGFSEffectsResource, particle "
                 "emitter placements from CParticleEffectCR. Positions are in "
                 "GAME axes (Y up). Bloom is added BEFORE the filmic curve: "
                 "graded = ToneMap_Hable(colour + blur(colour * "
                 "2^-(exposure_offset)) * magnitude). ⚠ Particle entries "
                 "are PLACEMENTS only -- the effect definitions "
                 "(CGParticleEffectResource / CGParticleGraphResource) are not "
                 "decoded, so nothing here says what an emitter looks like."),
        "particles": placements,
    }
    payload.update(fx)
    return payload


def main(argv=None) -> int:
    import argparse
    import json
    import sys

    here = Path(__file__).resolve().parent
    for extra in (str(here), str(here.parent / "blender_tool")):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    import evr_paths
    evr_paths.install_import_paths()

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("level")
    ap.add_argument("--dir", default=None)
    ap.add_argument("--members", nargs="*", default=None)
    args = ap.parse_args(argv)
    root = evr_paths.require_extract(args.dir)
    members = args.members or [args.level]
    print(json.dumps(sidecar(root, args.level, members), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
