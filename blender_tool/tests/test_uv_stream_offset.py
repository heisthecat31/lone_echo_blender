"""`uv_stream_offset` must not accept a HALF-DEAD slot.

⛔ The bug: the offset was validated with `(span_u + span_v) > 1e-6` -- a SUM.
On a stride-16 vertex, +8 lands half a vertex late: `u` comes back holding the
real `v`, and `v` reads the dead slot that follows. `span_v` is then 0 while
`span_u` is healthy, the sum is comfortably positive, and the wrong offset wins
over the right one at +4. The UV set that results has V collapsed to a
constant, so every vertex samples ONE texture row and the surface renders as a
flat wash of colour.

The war room hull proved it by duplication -- records 3-7 and 8-11 are the same
panels twice, and the twins that failed at +8 fell back to +4 and decoded
correctly while their siblings returned `u` equal to the twin's `v`, exactly one
field late.

Measured after the fix: `mpl_combat_war_room` 52 collapsed submeshes -> 0
(241 meshes byte-identical, 54 corrected), `mpl_combat_dyson` 106 -> 0.
"""
from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "evr_decode_for_test",
    str(Path(__file__).resolve().parents[2] / "app" / "extract" /
        "evr_mesh_importer" / "decode.py"))
decode = importlib.util.module_from_spec(_SPEC)
sys.modules["evr_decode_for_test"] = decode
_SPEC.loader.exec_module(decode)

STRIDE = 16
COUNT = 32


def _stream(dead=0.0):
    """Stride-16 stream-0 whose REAL uv pair sits at +4.

    Laid out so that reading at +8 -- half a vertex late -- yields
    `(v, dead)`: u varies, v does not. That is the shape that fooled the sum.
    """
    buf = bytearray()
    for j in range(COUNT):
        u = 0.10 + j * 0.02
        v = 0.30 + j * 0.01
        buf += struct.pack("<f", 7.0)        # +0  some other field
        buf += struct.pack("<ff", u, v)      # +4  the real UV
        buf += struct.pack("<f", dead)       # +12 dead slot
    return bytes(buf)


def test_the_offset_where_both_components_vary_wins():
    """★ The fix. +8 reads (v, dead) and must lose to +4's (u, v)."""
    assert decode.uv_stream_offset(_stream(), 0, COUNT, STRIDE) == 4


def test_the_half_dead_slot_would_have_passed_the_old_sum_rule():
    """⛔ Guards the exact regression: at +8, u varies and v is constant, so
    `span_u + span_v` is positive. Anything that reintroduces a sum test here
    picks 8 and collapses V again."""
    data = _stream()
    us, vs = [], []
    for j in range(COUNT):
        u, v = struct.unpack_from("<ff", data, j * STRIDE + 8)
        us.append(u)
        vs.append(v)
    span_u = max(us) - min(us)
    span_v = max(vs) - min(vs)
    assert span_v == 0.0                      # half dead
    assert span_u + span_v > 1e-6             # ...yet the old rule accepted it
    assert decode.uv_stream_offset(data, 0, COUNT, STRIDE) != 8


def test_a_genuinely_one_axis_uv_set_is_still_accepted():
    """⚠ A UV set constant in one axis is legitimate (a strip mapped along U),
    so pass 2 keeps the old rule and nothing that worked before is lost.

    The earlier slots hold out-of-range values here so they are rejected on
    their own merits -- otherwise this would test slot ORDER, not the rule.
    """
    buf = bytearray()
    for j in range(COUNT):
        buf += struct.pack("<ff", 1e30, 1e30)              # +0 implausible
        buf += struct.pack("<ff", 0.10 + j * 0.02, 0.5)    # +8 u varies, v flat
    assert decode.uv_stream_offset(bytes(buf), 0, COUNT, STRIDE) == 8


def test_the_preferred_offset_is_kept_when_it_is_fully_live():
    """313 of the arena's 324 submeshes are fine at +8 and must not move."""
    buf = bytearray()
    for j in range(COUNT):
        buf += struct.pack("<ff", 0.0, 0.0)
        buf += struct.pack("<ff", 0.10 + j * 0.02, 0.30 + j * 0.01)  # +8
    assert decode.uv_stream_offset(bytes(buf), 0, COUNT, STRIDE) == 8


def test_a_dead_span_threshold_exists_and_is_small():
    assert 0 < decode.UV_MIN_SPAN <= 1e-3
    assert decode.UV_PREFERRED_OFFSET == 8
