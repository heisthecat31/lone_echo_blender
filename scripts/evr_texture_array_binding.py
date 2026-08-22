"""`CTextureArrayBindingCR` -- which ARRAY SLICE an actor uses.

## The problem this solves

Some art ships as a texture ARRAY and every object bound to it gets slice 0.
`mpl_lobby_b2`'s poster boards are the visible case: `a240a4bc051b2f23` is 37
slices of 2048x1024 BC1_SRGB, each a different poster -- the movement
tutorials, the Atlas Intelligence board, the report notice, the social links --
and all the boards imported showing the same image.

The material does not name the array at all, so no amount of material decoding
finds it. The mapping lives in its own component, one record per placed board:

    header   +0x08  u32  table byte size
             +0x28  u32  record count
    record   base 0x38, stride 72
             +0x00  u64  class symbol (constant across the table)
             +0x08  u64  the ACTOR this applies to
             +0x20  u64  base texture (what the material binds anyway)
             +0x28  u64  the texture ARRAY
             +0x30  u32  SLICE INDEX
             +0x38  u64  0xFFFF...
             +0x40  u64  constant

`count * stride == size` exactly, every `+0x08` is a real actor nodeid, and on
`mpl_lobby_b2` the 23 records land on 23 distinct poster instances (i543..i572)
carrying slices 0..20 -- consecutive runs per board cluster, which is what a
hand-placed poster wall looks like.

⚠ The type name is a GUESS. `267366a86feec098` did not fall out of forward
hashing the authoring identifiers, so the name here describes what the records
do, not what the engine calls them.

## What a consumer needs

The slice files themselves come from `evr_materials.write_array_slices`, which
re-headers each slice as a standalone DDS. This module supplies the other half:
which slice belongs to which actor.
"""

from __future__ import annotations

import struct
from pathlib import Path

#: The component holding actor -> (array, slice).
TEXTURE_ARRAY_BINDING_CR = "267366a86feec098"

RECORD_STRIDE = 72
R_ACTOR = 0x08
R_BASE_TEXTURE = 0x20
R_ARRAY = 0x28
R_SLICE = 0x30

#: A slice index beyond this is not a slice; it is a misread field.
MAX_SLICE = 4096


def _normalise(value) -> str:
    return "%016x" % (int(value) & 0xFFFFFFFFFFFFFFFF)


def read_bindings(root: Path, members, known_actors=None) -> list:
    """`[{actor, array, base_texture, slice, level}, ...]` for a scene group."""
    out: list = []
    for member in members:
        path = Path(root) / TEXTURE_ARRAY_BINDING_CR / member
        if not path.exists():
            path = path.with_suffix(".bin")
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        if len(blob) < 0x38:
            continue
        size = struct.unpack_from("<I", blob, 0x08)[0]
        count = struct.unpack_from("<I", blob, 0x28)[0]
        if not count or not size or size % count:
            continue
        stride = size // count
        base = len(blob) - size
        if base < 0 or stride < R_SLICE + 4:
            continue
        for i in range(count):
            rec = base + i * stride
            actor = struct.unpack_from("<Q", blob, rec + R_ACTOR)[0]
            if known_actors is not None and actor not in known_actors:
                continue
            index = struct.unpack_from("<I", blob, rec + R_SLICE)[0]
            if index > MAX_SLICE:
                continue
            array = _normalise(struct.unpack_from("<Q", blob, rec + R_ARRAY)[0])
            if array in ("0000000000000000", "ffffffffffffffff"):
                continue
            out.append({
                "actor": _normalise(actor),
                "array": array,
                "base_texture": _normalise(
                    struct.unpack_from("<Q", blob, rec + R_BASE_TEXTURE)[0]),
                "slice": int(index),
                "level": member,
            })
    return out


def arrays_used(bindings) -> set:
    """Just the array texture hashes, for the extractor to slice."""
    return {b["array"] for b in bindings if b.get("array")}


def alternate_slices(bindings, slice_files, texture_dir) -> dict:
    """`{(array, slice) -> alternate slice}` for boards that SWITCH.

    ⭐ Some boards cycle between two posters at runtime. The binding table
    records only the FIRST, and the second is left unbound -- which is why the
    array has gaps: on `mpl_lobby_b2` slices 8, 11 and 24 are referenced by no
    binding at all, and each one immediately follows a bound slice.

    Reading them as pairs is confirmed by the art. Slice 7 is "REPORT PLAYERS
    VIOLATING THE CODE OF CONDUCT" and slice 8 is "HAVING ISSUES? CONTACT
    PLAYER SUPPORT"; slice 10 is "LET'S GET SOCIAL" (discord/twitter) and
    slice 11 is "NEWS - PATCH NOTES" (the blog); slice 23 is "HOLD & PRESS TO
    LAUNCH" and slice 24 is "BE AWARE OF THE 3 POINT ZONE". Three thematic
    pairs, each a bound slice followed by an unbound one.

    ⚠ HEURISTIC. Candidates, not conclusions -- nothing in the data states the
    pairing or the switch interval; this reads the GAPS, it does not decode an
    instruction. Slice 33 -> 34 also fires and is a FALSE POSITIVE: 34 is blank
    backing art.

    ⛔ Do NOT try to filter those out by "how much detail is in the slice".
    Tried, on distinct compressed blocks in the top mip, and it does not
    separate anything: the blank slice 34 scores 8784 where the genuine pairs
    score 1530-2183. A subtle gradient compresses to plenty of variety. The
    caller should present these as candidates and let a human look.
    """
    used = {}
    for b in bindings:
        used.setdefault(b["array"], set()).add(int(b["slice"]))

    out = {}
    for array, taken in used.items():
        count = len(slice_files.get(array) or ())
        for index in sorted(taken):
            nxt = index + 1
            if nxt >= count or nxt in taken:
                continue
            if (Path(texture_dir) / ("%s.s%02d.dds" % (array, nxt))).is_file():
                out[(array, index)] = nxt
    return out


def main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Decode which texture-array slice each actor uses.")
    parser.add_argument("level", help="level hash")
    parser.add_argument("--dir", required=True, help="flat extract root")
    args = parser.parse_args(argv)

    rows = read_bindings(Path(args.dir), [args.level.lower()])
    print("%d binding(s) over %d array(s)" % (len(rows), len(arrays_used(rows))))
    for row in rows:
        print("   actor %s  array %s  slice %d"
              % (row["actor"], row["array"], row["slice"]))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
