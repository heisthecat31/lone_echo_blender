"""Per-submesh vertex tint: BGRA decode, constancy, and what is applied.

`mpl_combat_combustion`'s water is greyscale emissive facets plus a flat blue
carried in stream 0. These pin the byte order (which the level's own appearance
decides), the refusal to average a varying lane, and the rule that WHITE and
BLACK are both refused -- the first as a no-op, the second because it cannot be
told apart from an unset lane.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from evr_vertex_color import _bgra, submesh_tints, TINT_OFFSET  # noqa: E402

#: The two constants that settle the byte order, measured on combustion.
WATER = 0xFF0000FF          # BGRA -> pure blue
LAVA = 0xFFFFBF3F           # BGRA -> R255 G191 B63, orange


def test_bgra_byte_order_matches_the_level():
    """RGBA would make the water red and the lava pale blue -- it is BGRA."""
    r, g, b, a = _bgra(WATER)
    assert (round(r * 255), round(g * 255), round(b * 255)) == (0, 0, 255)
    assert a == 1.0
    r, g, b, _a = _bgra(LAVA)
    assert (round(r * 255), round(g * 255), round(b * 255)) == (255, 191, 63)


def _model(descriptors):
    """`(gpu, primary)` for submeshes given as `(stride, [tint words])`."""
    gpu = bytearray()
    meta = bytearray()
    for stride, words in descriptors:
        base = len(gpu)
        for word in words:
            vertex = bytearray(stride)
            struct.pack_into("<I", vertex, TINT_OFFSET, word)
            gpu += vertex
        record = bytearray(14 * 4)
        struct.pack_into("<I", record, 0, 0xFFFFFF0C)
        struct.pack_into("<I", record, 4, 0xFFFFFFFF)
        struct.pack_into("<I", record, 8, 0x0B)
        struct.pack_into("<I", record, 12, 0)
        struct.pack_into("<I", record, 16, base)              # base offset
        struct.pack_into("<I", record, 24, stride * len(words))   # stream0 size
        struct.pack_into("<I", record, 36, len(words))        # vertex count
        struct.pack_into("<I", record, 40, len(words))        # mirror
        meta += record
    return bytes(gpu), bytes(meta) + b"\0" * 0x40


def test_constant_lane_is_reported():
    gpu, meta = _model([(20, [WATER] * 8), (16, [LAVA] * 6)])
    tints = submesh_tints(gpu, meta)
    assert len(tints) == 2
    assert round(tints[0][2] * 255) == 255          # blue
    assert round(tints[1][0] * 255) == 255          # orange, red channel


def test_varying_lane_is_omitted_not_averaged():
    """Averaging would invent a colour present in the data nowhere."""
    gpu, meta = _model([(20, [WATER] * 4 + [LAVA] * 4)])
    assert submesh_tints(gpu, meta) == {}


def test_white_is_reported_and_left_for_the_consumer_to_skip():
    gpu, meta = _model([(20, [0xFFFFFFFF] * 4)])
    tints = submesh_tints(gpu, meta)
    assert tints[0][:3] == (1.0, 1.0, 1.0)


# ── the importer-side gate (pure logic, no bpy) ──────────────────────────────

def _is_applicable(rgba):
    if not rgba or len(rgba) < 3:
        return False
    if all(abs(c - 1.0) <= 1e-6 for c in rgba[:3]):
        return False
    return not all(abs(c) <= 1e-6 for c in rgba[:3])


def _has_albedo(spec):
    channels = spec.get("channels") or {}
    keys = set(channels) if isinstance(channels, (dict, list, set)) else set()
    if keys & {"base_color", "albedo"}:
        return True
    return any("albedo" in role for role in (spec.get("role_textures") or {}))


def test_white_and_black_are_both_skipped():
    """Colour is believed; the two degenerate values are not.

    Black looked like art when the only case in view was `mpl_lobby_b2`'s
    skybox. Across a whole level it is not: 188 of `mpl_arena_a`'s 244 constant
    tints are black, ordinary lit architecture included, and a black multiply
    erases every one of them.
    """
    assert _is_applicable([0.0, 0.0, 1.0])          # the water
    assert _is_applicable([1.0, 0.75, 0.25])        # the combustion lava
    assert _is_applicable([0.0, 0.447, 1.0])        # the arena's blue team
    assert not _is_applicable([1.0, 1.0, 1.0])      # identity for a multiply
    assert not _is_applicable([0.0, 0.0, 0.0])      # erases the surface


def test_black_is_not_separable_by_alpha():
    """There is no in-band flag: an unset lane is 0xff000000, not 0x00000000."""
    assert _bgra(0xFF000000) == (0.0, 0.0, 0.0, 1.0)


def test_albedo_is_no_longer_a_gate():
    """Kept as a probe only -- the lava samples albedo AND is tinted."""
    water = {"channels": {"emission": {}, "flowmap": {}},
             "role_textures": {"layer0_emissive_map": "x",
                               "layer0_flowmap_map": "y"}}
    dressed = {"channels": {"base_color": {}},
               "role_textures": {"layer0_albedo_map": "z"}}
    assert not _has_albedo(water)
    assert _has_albedo(dressed)
    # both are tinted in game, so albedo must not decide it
    assert _is_applicable([1.0, 0.75, 0.25])
