"""Quest: the material UV multiplier, and the baked per-vertex lighting.

Both are things the first Quest export dropped, and both are read from the
file rather than inferred:

* **UV multiplier** -- a sibling pair of floats in `materialprops`. The arena's
  panel material carries `(1.0, 2.0)` and its mesh's V spans only 0..0.49, so
  every panel sampled the TOP HALF of a 1024x512 sheet -- the grey prop art in
  the corners instead of the coloured triangles the panels are made of.

* **Baked vertex lighting** -- usage 1 at stream offset 4, sRGB-encoded. It is
  the level's light bake; dropping it is what made the first import render flat
  and blown out.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from evr_quest import material as QM                       # noqa: E402
from evr_quest import scene as QS                          # noqa: E402


# ── UV multiplier ───────────────────────────────────────────────────────

def test_identity_is_the_unauthored_default():
    assert QM.IDENTITY_UV_SCALE == (1.0, 1.0)


def test_a_missing_material_scales_by_one_rather_than_zero(tmp_path):
    """Absent data must be a no-op, never a collapse of the UV set."""
    assert QM.uv_scale(tmp_path, "de" * 8) == QM.IDENTITY_UV_SCALE


def test_the_two_halves_of_the_pair_are_distinct_hashes():
    """They differ by one byte; reading one for both would tile square."""
    assert QM.UV_SCALE_U_HASH != QM.UV_SCALE_V_HASH


def _package(tmp_path, uv0, uv_scales, color0=None):
    mesh = QS.QuestMesh(index=0, model="ab" * 8,
                        positions=[0.0] * (3 * (len(uv0) // 2)),
                        indices=[0, 1, 2], uv0=list(uv0),
                        color0=list(color0 or []))
    QS.write_package(tmp_path, "ab" * 8, [mesh], [], {0: 0}, None,
                     uv_scales=uv_scales)
    doc = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    return doc["meshes"][0], doc


def _floats(tmp_path, rel):
    raw = (tmp_path / rel).read_bytes()
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def test_the_multiplier_is_baked_into_the_exported_uvs(tmp_path):
    """`(1, 2)` doubles V and leaves U alone -- the arena panels' case."""
    entry, _doc = _package(tmp_path, [0.25, 0.5, 0.75, 0.25], {0: (1.0, 2.0)})
    assert entry["uv_scale"] == [1.0, 2.0]
    assert _floats(tmp_path, entry["uv0"]) == [0.25, 1.0, 0.75, 0.5]


def test_an_unscaled_mesh_is_written_through_untouched(tmp_path):
    entry, _doc = _package(tmp_path, [0.25, 0.5, 0.75, 0.25], {})
    assert "uv_scale" not in entry
    assert _floats(tmp_path, entry["uv0"]) == [0.25, 0.5, 0.75, 0.25]


def test_a_tiling_multiplier_is_allowed_past_one(tmp_path):
    """16x tiling is real in this corpus; clamping to 0..1 would destroy it."""
    entry, _doc = _package(tmp_path, [0.5, 0.5], {0: (16.0, 1.0)})
    assert _floats(tmp_path, entry["uv0"]) == [8.0, 0.5]


# ── baked vertex lighting ───────────────────────────────────────────────

def test_srgb_decode_matches_the_standard_curve():
    assert QS._srgb_to_linear(0.0) == 0.0
    assert abs(QS._srgb_to_linear(1.0) - 1.0) < 1e-9
    # below the knee the transfer is linear; above it is the 2.4 power leg
    assert abs(QS._srgb_to_linear(0.04) - 0.04 / 12.92) < 1e-12
    assert abs(QS._srgb_to_linear(0.5) - 0.21404114) < 1e-6


def test_the_decode_is_not_a_passthrough():
    """⛔ Raw/255 runs 3-4x hot at the low end against the level's own atlas."""
    assert QS._srgb_to_linear(0.25) < 0.25 / 3


def test_baked_colour_reaches_the_package_and_is_flagged(tmp_path):
    entry, doc = _package(tmp_path, [0.0, 0.0], {},
                          color0=[0.1, 0.2, 0.3])
    assert doc["baked_vertex_light"] is True
    got = _floats(tmp_path, entry["color0"])
    assert all(abs(a - b) < 1e-6 for a, b in zip(got, (0.1, 0.2, 0.3)))
    assert len(got) == 3


def test_a_package_without_a_bake_says_so(tmp_path):
    _entry, doc = _package(tmp_path, [0.0, 0.0], {})
    assert doc["baked_vertex_light"] is False


def test_the_bake_lane_is_the_second_colour_not_the_first():
    """⛔ Slot 0 (offset 0) reads flat black on every hull mesh measured."""
    assert QS.BAKE_COLOR_OFFSET == 4
    assert QS.USAGE_COLOR == 1
