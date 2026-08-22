"""`CComponentLODCR` -- which COMPONENTS an actor drops at distance.

## Layout (verified, byte-exact on every level tried)

    header:
        +0x08  u32   table byte size          (== count * 96)
        +0x28  u32   record count
    records:  start at 0x38, stride 96
        +0x08  u64   the actor this component set belongs to
        +0x10  u64   0xFFFFFFFFFFFFFFFF terminator
        +0x20  u64   class symbol (constant across every record)
        +0x30  u32   entry byte size          (== entry count * 120)
        +0x50  u32   entry count
        +0x58  u32   entry capacity           (== count on disk)
    entries:  immediately after the record table, 120 bytes each,
              laid out in record order
        +0x10  u64   component TYPE hash
        +0x18  u64   a second type hash (constant across entries)
        +0x2c  u32   small count

The framing is self-checking three ways, which is what makes it trustworthy:
`+0x30 == count*120` holds for **every** record in both levels; the record
count matches the header exactly (205 / 339); and the payload length comes out
exact -- `mpl_arena_a` 413 entries * 120 = 49560 bytes, `mpl_combat_fission`
380 * 120 = 45600, with nothing left over.

⚠ The base is **0x38, not 0x40**. Reading it at 0x40 puts the nodeid at +0x00
and leaves the payload 8 bytes short; the 8-byte shortfall in both files is
what identifies the real base. Same convention as
`CR15LinearPositionConstraintCR`, whose nodeid also sits at +0x08.

## What it is NOT

⛔ **This is not submesh LOD.** Its entries name COMPONENT TYPE hashes -- the
values already in `evr_component_cr.CMODEL_COMPONENT_TYPES` -- so it describes
which components of an actor survive at each LOD step, not which submesh of a
model to draw.

The stacked-geometry problem this was investigated for is submesh-level: one
model's draw list contains several detail levels of the same part
(`dac6537a23236325` on `mpl_arena_a` is 53841 / 33624 / 23082 / 10143 / 1448
vertices). That structure lives in the model's own render params, not here --
`CGRenderParams +0x00` carries it, and on that model the field steps 18/20/24,
42/36/34, 74/68/66, 138/132/130, 266/260/258 across the five levels, i.e. a bit
per level from bit 4 up. The encoding is NOT uniform across models
(`570677a85028cfa9` reads 1/1/1/2/2 for five slices that are not LODs at all),
so it is characterised but not solved, and `evr_scene_extract` still infers
submesh LOD from bounding boxes constrained by mattype.
"""

from __future__ import annotations

import struct
from pathlib import Path

#: `CComponentLODCRWin10`.
COMPONENT_LOD_CR = "7f49abae39aaf2aa"

RECORD_BASE = 0x38
RECORD_STRIDE = 96
SIZE_OFFSET = 0x08
COUNT_OFFSET = 0x28

R_ACTOR = 0x08
R_ENTRY_BYTES = 0x30
R_ENTRY_COUNT = 0x50

ENTRY_STRIDE = 120
E_COMPONENT_TYPE = 0x10


def parse(blob: bytes) -> dict | None:
    """`{"actors": {nodeid: [component_type, ...]}, ...}` or None.

    Returns None rather than guessing when the three framing checks disagree.
    """
    if len(blob) < RECORD_BASE + 4:
        return None
    size = struct.unpack_from("<I", blob, SIZE_OFFSET)[0]
    count = struct.unpack_from("<I", blob, COUNT_OFFSET)[0]
    if not count or size != count * RECORD_STRIDE:
        return None
    if RECORD_BASE + size > len(blob):
        return None

    records = []
    total_entries = 0
    for i in range(count):
        off = RECORD_BASE + i * RECORD_STRIDE
        actor = struct.unpack_from("<Q", blob, off + R_ACTOR)[0]
        n = struct.unpack_from("<I", blob, off + R_ENTRY_COUNT)[0]
        nbytes = struct.unpack_from("<I", blob, off + R_ENTRY_BYTES)[0]
        if nbytes != n * ENTRY_STRIDE:
            return None                 # framing broken -- refuse
        records.append((actor, n))
        total_entries += n

    payload = RECORD_BASE + size
    if payload + total_entries * ENTRY_STRIDE != len(blob):
        return None                     # the exactness check

    actors: dict = {}
    cursor = payload
    for actor, n in records:
        types = []
        for _ in range(n):
            types.append("%016x" % struct.unpack_from(
                "<Q", blob, cursor + E_COMPONENT_TYPE)[0])
            cursor += ENTRY_STRIDE
        actors.setdefault(actor, []).extend(types)
    return {
        "records": count,
        "entries": total_entries,
        "actors": actors,
    }


def read(root: Path, level_hash) -> dict | None:
    from evr_resource_types import normalise_hash, resolve_type_dir

    directory = resolve_type_dir(root, COMPONENT_LOD_CR)
    path = directory / normalise_hash(level_hash)
    if not path.exists():
        path = path.with_suffix(".bin")
    if not path.is_file():
        return None
    return parse(path.read_bytes())


def main(argv=None) -> int:
    import argparse
    import json
    import sys
    from collections import Counter

    here = Path(__file__).resolve().parent
    for extra in (str(here), str(here.parent / "blender_tool")):
        if extra not in sys.path:
            sys.path.insert(0, extra)
    import evr_paths
    evr_paths.install_import_paths()

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("level")
    ap.add_argument("--dir", default=None)
    args = ap.parse_args(argv)
    parsed = read(evr_paths.require_extract(args.dir), args.level)
    if parsed is None:
        print("no CComponentLODCR for that level, or the framing did not check out")
        return 1
    print("records %d, entries %d, actors %d"
          % (parsed["records"], parsed["entries"], len(parsed["actors"])))
    per = Counter(len(v) for v in parsed["actors"].values())
    print("components per actor: %s" % sorted(per.items()))
    types = Counter(t for v in parsed["actors"].values() for t in v)
    print("component types referenced:")
    for t, n in types.most_common(10):
        print("   %s  x%d" % (t, n))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
