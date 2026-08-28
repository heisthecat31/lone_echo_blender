"""`brokenassets` recovery: the models a level lists but nothing places.

A level's `assetdata` names models that no CSIMCR pair and no actor places.
`mpl_combat_dyson` has 9, all listed in the CGSI's own `brokenassets` table, and
all 9 decode cleanly off disk -- so they were dropped for want of a transform,
not because they are corrupt.

`brokenassets` is a PAIR of asset hashes whose FIRST column is a model that IS
placed. What supports reading the second as "belongs at the first's transforms":

  * all 9 of dyson's partners are placed, and none of them is an entity;
  * those partners hold 32 instances between them, exactly matching the 32 rows
    of the sibling `brokennodes` table;
  * the placements come out as mirrored pairs, which is how the level is built.

⚠ `inferred` -- no decoded struct names the relationship, so this pins the
DECODE and the mapping, not the semantics.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import evr_lightmap as LM                                        # noqa: E402
import evr_scene_extract as SE                                   # noqa: E402

#: `CGStaticInstanceResource` section bases and their row strides, in order.
SECTIONS = [(0x000, 24), (0x040, 24), (0x080, 16),
            (0x0C0, 16), (0x0F8, 8), (0x130, 16)]
HEADER = 0x178


def _cgsi(rows_per_section) -> bytes:
    """A minimal CGSI whose six sections tile the file exactly."""
    body = b""
    head = bytearray(HEADER)
    for (base, stride), rows in zip(SECTIONS, rows_per_section):
        count = len(rows)
        struct.pack_into("<Q", head, base + 0x08, count * stride)
        struct.pack_into("<Q", head, base + 0x28, count)
        struct.pack_into("<Q", head, base + 0x30, count)
        for row in rows:
            body += row
    return bytes(head) + body


def _pair(a: int, b: int) -> bytes:
    return struct.pack("<QQ", a, b)


def test_pairs_map_partner_to_the_model_it_stands_in_for():
    blob = _cgsi([[], [], [], [], [], [_pair(0xAAAA, 0x1111),
                                       _pair(0xBBBB, 0x2222)]])
    assert LM.read_cgsi(blob) is not None, "the synthetic CGSI must parse"
    assert SE.broken_asset_map(blob) == {
        "000000000000aaaa": ["0000000000001111"],
        "000000000000bbbb": ["0000000000002222"],
    }


def test_one_partner_can_stand_in_for_several_models():
    """The map is a LIST per partner: two rows may share a first column."""
    blob = _cgsi([[], [], [], [], [], [_pair(0xAAAA, 0x1111),
                                       _pair(0xAAAA, 0x2222)]])
    assert SE.broken_asset_map(blob) == {
        "000000000000aaaa": ["0000000000001111", "0000000000002222"]}


def test_a_level_with_no_broken_assets_recovers_nothing():
    blob = _cgsi([[], [], [], [], [], []])
    assert SE.broken_asset_map(blob) == {}


def test_hashes_are_rendered_as_16_hex_digits():
    """The model list is keyed by the same 16-char form the manifest uses."""
    blob = _cgsi([[], [], [], [], [], [_pair(0x43E2DA7914642604,
                                             0xAA5A3485D9DE43F2)]])
    got = SE.broken_asset_map(blob)
    assert got == {"43e2da7914642604": ["aa5a3485d9de43f2"]}
    for key, vals in got.items():
        assert len(key) == 16 and all(c in "0123456789abcdef" for c in key)
        assert all(len(v) == 16 for v in vals)


def test_a_blob_that_is_not_a_cgsi_yields_nothing_rather_than_guessing():
    assert SE.broken_asset_map(b"") == {}
    assert SE.broken_asset_map(b"\x00" * 64) == {}
    assert SE.broken_asset_map(b"not a resource at all") == {}


def test_the_recovery_can_be_switched_off():
    """`--no-broken-assets`: the pairing is inferred, so it stays escapable."""
    assert SE._NO_BROKEN_ASSETS == [False]
