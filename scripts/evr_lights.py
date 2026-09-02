"""Echo VR scene lights: `CGSceneResource` section 1, `SGLightParams`.

## Correcting an earlier reading

An earlier version of this module read **section 10** (56-byte records), which
`rad-archive-viewer/app.py` labels "lighting". Those records decode cleanly --
100% unit quaternions, positions inside the level -- but they carry no colour
and no type, which led to the wrong conclusion that Echo VR's lights are
colourless and everything coloured must be baked.

They are not. The real light array is **section 1**, the FIRST section in the
stream, stride 360. It has colour, type, intensity, range and direction.

## Where the field map comes from

`rad-archive-viewer/ORIGINAL FILES/scene_lights.py`, which RE'd the engine's
scene-light-prepare state machine (`sub_1405A5900`, reached from CGScene render
init `sub_14058FD20` case 6->7) in order to AUTHOR lights into custom levels.
Its map, confirmed here against the shipped files:

    +0    u32    flags -- the runtime light-pipeline gate word, not an id
    +4    u32    TYPE   0 = point, 1 = spot, 2 = directional
    +8    u64    name symbol
    +20   3xf32  position
    +28   3xf32  COLOR (linear rgb)
    +40   f32    intensity
    +44   2xf32  range
    +84   3xf32  DIRECTION
    +344  u64    owner entity (all-ones = none)

Verification on `mpl_arena_a`: 138 lights, **2 directional / 26 spot / 110
point**, and the two directional lights read

    warm  color (1.000, 0.583, 0.431)  direction (0, -0.707, -0.707)
    cool  color (0.584, 0.820, 1.000)  direction (0, -0.766,  0.643)

matching that document's independently-recorded values exactly. That warm-key /
cool-fill pair is the arena's orange-vs-blue split.

Only TYPE >= 2 lights reach the runtime list that shades DYNAMIC objects
(the engine gates on `light_type >= 2`); point and spot lights are for the
static bake. `dynamic_only()` applies that gate.
"""

from __future__ import annotations

import math
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evr_resource_types import (SCENE_RESOURCE, normalise_hash, resolve_type_dir,
                                resource_path)

#: `SGLightParams` stride, and the offsets within it.
LIGHT_STRIDE = 360
L_FLAGS = 0x00
L_TYPE = 0x04
L_NAME = 0x08
#: ⚠ `scene_lights.py` records position at +20, but +20 and its own "+28 COLOR"
#: overlap by 4 bytes, so one of the two is off by a field. COLOR at +28 is
#: certainly right (the arena's two directional colours reproduce exactly), so
#: position must END where colour begins -- +0x10. Measured: at +0x10 all 138
#: arena lights land inside the level's geometry extent; at +0x14 none of them
#: do, and the third component collapses to [0,1] because it is really the red
#: channel. The corrected block is contiguous: 0x10+12 = 0x1C, 0x1C+12 = 0x28.
L_POSITION = 0x10
L_COLOR = 0x1C
L_INTENSITY = 0x28
L_RANGE = 0x2C
L_DIRECTION = 0x54
L_OWNER = 0x158

#: The SPOT cone, stored three ways in the same record. `+0x44` is the FULL
#: angle in radians; `+0x60` and `+0x64` are the cosines of the HALF inner and
#: outer angles, i.e. what the shader actually multiplies against.
#:
#: ⭐ The identity `2 * acos(+0x64) == +0x44` holds for **77 of 77** spots
#: across mpl_arena_a, mpl_combat_dyson, mpl_tutorial_lobby and
#: d09afd15b1c75c04 -- 0 disagreements -- and the angles come out on round
#: degrees (59, 75, 81 outer; 25 inner), which is what an authored value looks
#: like. All three are constant across POINT records and vary only for spots.
L_SPOT_ANGLE = 0x44
L_SPOT_COS_INNER = 0x60
L_SPOT_COS_OUTER = 0x64

POINT, SPOT, DIRECTIONAL = 0, 1, 2
TYPE_NAMES = {POINT: "POINT", SPOT: "SPOT", DIRECTIONAL: "SUN"}

NULL_SYMBOL = 0xFFFFFFFFFFFFFFFF


@dataclass
class Light:
    """One `SGLightParams` record."""

    index: int = 0
    kind: int = POINT
    position: list = field(default_factory=lambda: [0.0, 0.0, 0.0])
    direction: list = field(default_factory=lambda: [0.0, -1.0, 0.0])
    color: list = field(default_factory=lambda: [1.0, 1.0, 1.0])
    intensity: float = 1.0
    #: `(x, y)` -- the engine writes both, normally equal.
    range: list = field(default_factory=lambda: [150.0, 150.0])
    name: int = 0
    flags: int = 0
    #: Owning entity, or None when the light is entity-free.
    owner: str | None = None
    #: SPOT only: the full cone angle in radians, and the full INNER angle
    #: inside which the light is at full strength. `None` on other types.
    cone: float | None = None
    cone_inner: float | None = None

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.kind, "POINT")

    @property
    def shades_dynamic(self) -> bool:
        """Whether the engine puts this light in the DYNAMIC shading list."""
        return self.kind >= DIRECTIONAL


def _cone_from_cos(value: float) -> float | None:
    """`2 * acos(cos_half)` -- the full angle, in radians, or None if unusable."""
    if not isinstance(value, float) or value != value:
        return None
    if not -1.0 <= value <= 1.0:
        return None
    return 2.0 * math.acos(value)


def parse_scene_lights(data: bytes) -> list:
    """Section 1 -> `[Light, ...]`.

    Section 1 opens the stream, so the array needs no walk: a `u32` count then
    `count` x 360 bytes. Returns `[]` if that does not fit the file, or if the
    types are not all in 0..2 -- the cheapest check that the stride is right.
    """
    if len(data) < 4:
        return []
    count = struct.unpack_from("<I", data, 0)[0]
    if not count or 4 + count * LIGHT_STRIDE > len(data):
        return []

    out = []
    for i in range(count):
        base = 4 + i * LIGHT_STRIDE
        kind = struct.unpack_from("<I", data, base + L_TYPE)[0]
        if kind > DIRECTIONAL:
            return []
        owner = struct.unpack_from("<Q", data, base + L_OWNER)[0]
        out.append(Light(
            index=i,
            kind=kind,
            position=list(struct.unpack_from("<3f", data, base + L_POSITION)),
            direction=list(struct.unpack_from("<3f", data, base + L_DIRECTION)),
            color=list(struct.unpack_from("<3f", data, base + L_COLOR)),
            intensity=struct.unpack_from("<f", data, base + L_INTENSITY)[0],
            range=list(struct.unpack_from("<2f", data, base + L_RANGE)),
            name=struct.unpack_from("<Q", data, base + L_NAME)[0],
            flags=struct.unpack_from("<I", data, base + L_FLAGS)[0],
            owner=(None if owner == NULL_SYMBOL else f"{owner:016x}"),
            cone=(struct.unpack_from("<f", data, base + L_SPOT_ANGLE)[0]
                  if kind == SPOT else None),
            cone_inner=(_cone_from_cos(
                struct.unpack_from("<f", data, base + L_SPOT_COS_INNER)[0])
                if kind == SPOT else None),
        ))
    return out


def dynamic_only(lights) -> list:
    """Just the lights that shade dynamic objects (the engine's own gate)."""
    return [light for light in lights if light.shades_dynamic]


def level_lights(root: Path, level_hash: str) -> list:
    """`[Light, ...]` for a level, or `[]`."""
    # `resource_path` tolerates BOTH on-disk spellings (zero-padded and
    # leading-zero-stripped) and the optional `.bin` suffix. A raw join sees
    # only the padded name, so a level such as `mpl_combat_war_room`
    # (`08a1af9e108def0b`, stored as `8a1af9e108def0b`) silently returned
    # nothing at all.
    path = resource_path(root, SCENE_RESOURCE, level_hash)
    if path is None:
        return []
    return parse_scene_lights(path.read_bytes())


#: Stride of the SECOND table in the scene resource's `lead` group.
VOLUME_STRIDE = 296
#: Offsets inside it. Position and colour land on the same offsets as the
#: 360-byte `SGLightParams`, but the rest of the record does NOT match it --
#: `L_TYPE` reads as a distinct 32-bit value per record and `L_RANGE` goes
#: negative -- so this is a DIFFERENT struct that happens to share a prefix.
V_POSITION = 0x10
V_COLOR = 0x1C
V_MAGNITUDE = 0x28
#: A 3x3 rotation*scale, then a translation at `V_TRANSLATION`.
#:
#: ⭐ The three rows are ORTHOGONAL -- every pairwise dot product is 0 to within
#: 1e-4 on every record checked -- so this is an oriented box, and each row's
#: NORM is that axis's half-extent while the normalised row is its direction.
#: Row norms on `mpl_combat_war_room` record 0 are `(1.482, 0.041, 0.472)`,
#: identical across its first six records.
#:
#: ⛔ Do NOT read the extent off the DIAGONAL. That gives `(0.404, 0.029,
#: 0.225)` here -- both the wrong size and axis-aligned, which renders every
#: volume as a flat plate in the wrong orientation.
V_TRANSFORM = 0x2C
#: The 3x3's translation. Superseded for SHAPE purposes by `V_CORNERS` below --
#: it is kept because it is a real field, not because anything should build
#: geometry from it.
V_TRANSLATION = 0x50
#: ★ THE SHAPE. Eight `C3Vector` corners, stride 12, in WORLD space.
#:
#: Recovered from the engine, not guessed. `CGVolumeHexahedronLight::Initialize`
#: (@0x18ec9fc) opens with `memcpy(this + 0x1500, params, 0x128)` -- the whole
#: 296-byte record -- so a runtime offset minus 0x1500 IS the params offset.
#: `CGVolumeHexahedronLight::Update(const C44Matrix&)` (@0x18ecfa4) then
#: transforms exactly EIGHT points at `this+0x1550 .. +0x15a4` (stride 12)
#: through a point-by-matrix helper, writing them to `this+0x1658..`. Eight
#: corners is a hexahedron, and 0x1550 - 0x1500 = 0x50.
#:
#: They are a FRUSTUM, not a box: on `mpl_combat_war_room` record 0 the
#: `c0..c3` face measures 0.313 across against 5.632 for `c4..c7` -- narrow at
#: one end, flared at the other, i.e. a light shaft. `mpl_combat_dyson`'s are
#: box-like (both faces parallel), so the same record serves both. No box built
#: from `V_TRANSFORM`'s axes can express the flared case, which is why every
#: extent reading failed at every scale.
V_CORNERS = 0x50
V_CORNER_COUNT = 8
#: `c0..c3` are one quad face and `c4..c7` the opposite one, in matching winding
#: order (verified on dyson, whose faces are axis-aligned at x=28.050/-9.180).
HEXAHEDRON_FACES = ((0, 1, 2, 3), (7, 6, 5, 4), (0, 1, 5, 4),
                    (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7))


def parse_scene_volume_lights(data: bytes) -> list:
    """The scene resource's SECOND `lead` table -- coloured light VOLUMES.

    ## Why this exists

    `parse_scene_lights` reads `lead[0]` and nothing read `lead[1]`, so a level
    reported only its placed-light count: `mpl_combat_dyson` 4, while this table
    holds **180** more. Across levels: war room 763, arena 216, tutorial_lobby
    218, combustion 62.

    ## What is established

    * **Colour is a colour.** All three components lie in 0..1 on 1439 of 1439
      records across five levels.
    * **They are the TEAM colours.** Dyson's 180 records use 14 distinct values,
      ~63 blue (`0.35,0.892,1.0`) and ~113 warm (`1.0,0.567,0.35`) -- the same
      families the placed lights already carry.
    * **They are placed in mirrored pairs.** Dyson record 0 is
      `(28.05,6.47,-2.81)` blue and record 1 `(-27.94,6.47,-2.81)` orange:
      same height and depth, mirrored across x, opposite ends of a symmetric
      map.
    * Positions fall inside each level's own bounds, on every level checked.

    ## What the ENGINE does with them (`libr15.so`, symbols intact)

    `lead[1]` is `CTable<SGVolumetricLightParams>` -- ORG's disassembly-confirmed
    `CGSceneData::Serialize` walk names member 2 `vlights`, and the symbol
    `CTable<SGVolumetricLightParams>::Serialize` @0x190c778 confirms it.

    ⭐ The geometry class is **`CGVolumeHexahedronLight`**
    (`Initialize(const SGVolumetricLightParams&)` @0x18ec9fc). A HEXAHEDRON --
    six faces, eight corners -- i.e. a FRUSTUM, not a box. That is why every
    box-shaped attempt failed structurally rather than by scale: a frustum has a
    different cross-section at each end and no scale factor turns a box into
    one. An extent sweep (x0.1/0.25/0.5/1.0) left the shape wrong at every size,
    which is the signature of a wrong primitive, not a wrong scale.

    Its `Update` takes a **C44Matrix** (@0x18ecfa4, reads +0x00..+0x28 of it),
    so the volume is placed by a full 4x4, not by a scale triple.

    Other confirmed behaviour, all from the symbol table:
      * `CGVolumetricLightInstance` is the runtime object; it FADES
        (`StartFade(u32, float)`), is intensity-scaled at runtime
        (`ApplyIntensityMultiplier(float)`) and can hang off a skeleton
        (`SetParentJoint(u32)`).
      * `CGScene::LevelVolumeLightVPMask` / `ActorVolumeLightVPMask` -- per
        VIEWPORT masks, so which volumes draw is view-dependent.
      * `CGRenderConfig::CanEnableVolumetrics()` / `EnableVolumetrics()` -- the
        whole feature is gated and may simply be off on a given profile.
      * `SGVolumetricLight::SetFromLight(const CGLightInstance*, const
        C44Matrix&)` -- a volumetric can also be derived from a placed light.

    ⚠ The FIELD LAYOUT is still not recovered. `Initialize` reads only +0x0c4
    directly and delegates the rest through calls that were not followed, so the
    offsets below are measured, not read out of the engine.

    ## What is INFERRED, and why they are not imported as point lights

    ⚠ The record carries a 3x4 TRANSFORM, not a radius -- a scale and a
    position, i.e. a box. So these are volumes, and turning one into a point
    light at its centre is a guess about falloff and extent that the data does
    not state. There is no type field (the `L_TYPE` slot is not an enum here)
    and no separate intensity beyond `V_MAGNITUDE` (2.5..12 on dyson).

    They are therefore REPORTED, not lit with. A consumer that wants to
    approximate them can take `position` + `color` + `magnitude`; one that wants
    to be faithful needs the volume semantics decoded first.
    """
    try:
        import cgsceneresource as scene_reader
    except ImportError:
        return []
    try:
        obj = scene_reader.read(data)
    except Exception:                                       # noqa: BLE001
        return []
    lead = obj.get("lead") or []
    if len(lead) < 2 or not isinstance(lead[1], tuple) or len(lead[1]) < 2:
        return []
    count, raw = lead[1][0], lead[1][1]
    if not count or not raw or len(raw) // count != VOLUME_STRIDE:
        return []

    out = []
    for i in range(count):
        base = i * VOLUME_STRIDE
        colour = list(struct.unpack_from("<3f", raw, base + V_COLOR))
        if not all(0.0 <= c <= 1.0 for c in colour):
            return []                    # the colour test is what identifies it
        m = struct.unpack_from("<9f", raw, base + V_TRANSFORM)
        rows = (m[0:3], m[3:6], m[6:9])
        # Half-extent is the row NORM; direction is the normalised row.
        extent = [math.sqrt(sum(c * c for c in r)) for r in rows]
        basis = [[round(c / e, 6) for c in r] if e > 1e-9 else [0.0, 0.0, 0.0]
                 for r, e in zip(rows, extent)]
        out.append({
            "index": i,
            "position": [round(v, 5) for v in
                         struct.unpack_from("<3f", raw, base + V_POSITION)],
            "color": [round(c, 6) for c in colour],
            "magnitude": round(struct.unpack_from(
                "<f", raw, base + V_MAGNITUDE)[0], 5),
            "extent": [round(e, 5) for e in extent],
            "basis": basis,
            # THE SHAPE -- eight world-space corners. See `V_CORNERS`.
            "corners": [[round(v, 5) for v in
                         struct.unpack_from("<3f", raw,
                                            base + V_CORNERS + k * 12)]
                        for k in range(V_CORNER_COUNT)],
            "translation": [round(v, 5) for v in
                            struct.unpack_from("<3f", raw,
                                               base + V_TRANSLATION)],
        })
    return out


def level_volume_lights(root: Path, level_hash: str) -> list:
    """`parse_scene_volume_lights` for a level, or `[]`."""
    path = resource_path(root, SCENE_RESOURCE, level_hash)
    if path is None:
        return []
    return parse_scene_volume_lights(path.read_bytes())


def main(argv) -> int:
    from collections import Counter

    import evr_paths
    root = Path(argv[0]) if argv else evr_paths.require_extract(None)
    for level in argv[1:] or ["576ed3f8428ebc4b"]:
        lights = level_lights(root, level)
        kinds = Counter(light.type_name for light in lights)
        print(f"{level}: {len(lights)} lights {dict(kinds)}")
        for light in lights:
            if light.kind == DIRECTIONAL:
                print(f"   SUN color={[round(c, 3) for c in light.color]} "
                      f"dir={[round(v, 3) for v in light.direction]} "
                      f"intensity={light.intensity:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
