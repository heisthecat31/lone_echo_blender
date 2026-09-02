"""Per-submesh vertex TINT -- the colour a surface is multiplied by.

## The problem this solves

`mpl_combat_combustion`'s water (`42670f2bed45703c` submeshes 0, 1, 5-9, which
the importer names i38 / i34 / i9 among others) imports WHITE and unlit, while
in game it is blue and glowing. Its material explains neither: both emissive
maps are greyscale facet patterns (mean R=G=B 0.37 and 0.35), the material
resource has NO property words at all, and `bakecolor` is `[1, 1, 1, 1]`.

The colour is in the GEOMETRY. Stream 0 carries a 4-byte lane at `+0` that the
decoder skips on its way to the UVs (`uv_stream_offset`: +8 at stride 20, +4 at
stride 16). Read as **BGRA** it is a flat tint:

    0xff0000ff  ->  R   0 G   0 B 255   the blue water          21 submeshes
    0xffffbf3f  ->  R 255 G 191 B  63   the orange combustion    21 submeshes
    0xffffffff  ->  white, i.e. untinted                         71 submeshes

⭐ BGRA rather than RGBA is settled by the level itself, not by preference:
read as RGBA those two would be a RED water and a PALE BLUE lava, which is the
opposite of what `mpl_combat_combustion` looks like.

161 of the model's 190 submeshes carry a CONSTANT value in that lane, which is
what makes it a per-submesh tint rather than per-vertex shading.

## What this returns, and what it refuses to

`submesh_tints()` reports a submesh ONLY when every vertex agrees. The other 29
vary per vertex; averaging them would invent a colour that is in the data
nowhere, so they are left out and the caller applies no tint -- exactly the
behaviour they have today. Per-vertex colour would need a real colour attribute
plumbed through the manifest and is deliberately not attempted here.

⚠ White (`0xffffffff`) is reported like any other value. It is the identity for
a multiply, so a consumer may skip it, but this module does not decide that.
"""

from __future__ import annotations

import struct
from pathlib import Path

#: Descriptor marker pair that opens a stream record (same as the decoder's).
_DESC_MAGIC = 0xFFFFFF0C
_DESC_SENTINEL = 0xFFFFFFFF

#: Fallback tint offset, used only when the model states no element table.
#:
#: ⛔ NOT the answer on its own. `+0` is where `mpl_combat_combustion`'s water
#: keeps its colour, and taking it as a constant read the WRONG LANE almost
#: everywhere else: stream 0 carries TWO colour attributes, and the model's own
#: `SVertexElement` table says which is which. On 137 of `mpl_arena_a`'s 140
#: models the layout is
#:
#:     usage COLOUR  usage_index 1  ->  +0     (the per-layer blend GATE)
#:     usage COLOUR  usage_index 0  ->  +4     (the vertex COLOUR)
#:
#: so `+0` read the gate. That is the whole reason "188 of 244 constant tints
#: are BLACK (77%)" -- the gate is 0 on most surfaces, and black was then
#: refused as an unset value. It also tinted the sky's 239-unit emissive shell
#: (mesh 7) BLUE from a gate of `[255,0,0]` when its authored colour lane reads
#: `[255,101,127]`, PINK. Only 3 of the 140 put color0 at +0, which is why the
#: combustion case worked and hid this.
TINT_OFFSET = 0
#: `SVertexElement.usage` for a colour attribute, and the usage_index that is
#: the COLOUR rather than the blend gate.
_USAGE_COLOUR = 1
_COLOUR_INDEX = 0
#: Below this stride there is no room for a tint and a UV pair.
MIN_STRIDE = 12
#: Cap on vertices sampled per submesh when checking for constancy.
_SAMPLE_LIMIT = 4096


def _descriptors(meta: bytes) -> list:
    """`[(base_offset, stream0_size, vertex_count), ...]`, in storage order."""
    out: list = []
    n = len(meta)

    def u32(offset: int) -> int:
        if offset < 0 or offset + 4 > n:
            return 0
        return struct.unpack_from("<I", meta, offset)[0]

    for doff in range(0, max(0, n - 0x40), 4):
        vals = [u32(doff + i * 4) for i in range(14)]
        if vals[0] != _DESC_MAGIC or vals[1] != _DESC_SENTINEL:
            continue
        if vals[2] not in (0x0B, 0x0D) or vals[3] != 0:
            continue
        count = vals[9]
        if count == 0 or vals[10] != count:
            continue
        out.append((vals[4], vals[6], count))
    out.sort()
    return out


def _bgra(word: int) -> tuple:
    """A stream-0 tint word as linear `(r, g, b, a)` in 0..1.

    The bytes are B, G, R, A in memory order -- see the module docstring for
    why that reading is the one the level supports.
    """
    blue = word & 0xFF
    green = (word >> 8) & 0xFF
    red = (word >> 16) & 0xFF
    alpha = (word >> 24) & 0xFF
    return (red / 255.0, green / 255.0, blue / 255.0, alpha / 255.0)


def tint_offset(primary: bytes) -> int:
    """Where THIS model keeps its vertex COLOUR, from its own element table.

    Falls back to `TINT_OFFSET` when the table cannot be read, which keeps a
    model the scanner cannot describe behaving exactly as it did.
    """
    try:
        import evr_vertex_gate                            # noqa: PLC0415
    except ImportError:
        return TINT_OFFSET
    for usage, off, _fmt, _comps, uidx, _size, stream in             evr_vertex_gate.elements(primary):
        if usage == _USAGE_COLOUR and uidx == _COLOUR_INDEX and stream == 0:
            return int(off)
    return TINT_OFFSET


def submesh_tints(gpu: bytes, primary: bytes) -> dict:
    """`{submesh index -> (r, g, b, a)}` for submeshes with a CONSTANT tint.

    Submeshes whose tint lane varies per vertex are omitted rather than
    averaged; see the module docstring.
    """
    out: dict = {}
    lane = tint_offset(primary)
    for index, (base, stream0_size, count) in enumerate(_descriptors(primary)):
        if not count:
            continue
        stride = stream0_size // count
        if stride < MIN_STRIDE:
            continue
        if base + count * stride > len(gpu):
            continue
        step = max(1, count // _SAMPLE_LIMIT)
        first = None
        constant = True
        for i in range(0, count, step):
            if lane + 4 > stride:
                constant = False
                break
            word = struct.unpack_from("<I", gpu, base + i * stride + lane)[0]
            if first is None:
                first = word
            elif word != first:
                constant = False
                break
        if constant and first is not None:
            out[index] = _bgra(first)
    return out


def tints_for_model(root: Path, model_hash: str) -> dict:
    """`submesh_tints` for a model in a flat extract, or `{}`."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import evr_scene_extract as se                            # noqa: E402

    gpu_path, primary_path = se.find_mesh_and_primary(Path(root), model_hash)
    if not gpu_path or not primary_path:
        return {}
    try:
        return submesh_tints(gpu_path.read_bytes(), primary_path.read_bytes())
    except OSError:
        return {}


def main(argv=None) -> int:
    import argparse
    import collections

    parser = argparse.ArgumentParser(
        description="Decode per-submesh vertex tints for a model.")
    parser.add_argument("model", help="model hash")
    parser.add_argument("--dir", required=True, help="flat extract root")
    args = parser.parse_args(argv)

    tints = tints_for_model(Path(args.dir), args.model.lower())
    print("%d submesh(es) carry a constant tint" % len(tints))
    tally = collections.Counter(tints.values())
    for colour, n in tally.most_common():
        print("   R=%3d G=%3d B=%3d A=%3d   %d submesh(es)"
              % (round(colour[0] * 255), round(colour[1] * 255),
                 round(colour[2] * 255), round(colour[3] * 255), n))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
