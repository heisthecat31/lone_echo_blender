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

import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evr_resource_types import SCENE_RESOURCE, normalise_hash, resolve_type_dir

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

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.kind, "POINT")

    @property
    def shades_dynamic(self) -> bool:
        """Whether the engine puts this light in the DYNAMIC shading list."""
        return self.kind >= DIRECTIONAL


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
        ))
    return out


def dynamic_only(lights) -> list:
    """Just the lights that shade dynamic objects (the engine's own gate)."""
    return [light for light in lights if light.shades_dynamic]


def level_lights(root: Path, level_hash: str) -> list:
    """`[Light, ...]` for a level, or `[]`."""
    path = resolve_type_dir(root, SCENE_RESOURCE) / normalise_hash(level_hash)
    if not path.exists():
        path = path.with_suffix(".bin")
    if not path.exists():
        return []
    return parse_scene_lights(path.read_bytes())


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
