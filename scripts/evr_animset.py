"""Echo VR animation sets -- the animation INVENTORY.

## What this reads, and what it does not

`CAnimSetResourceWin10` (`e9e7d2e25d8e2252`) is the per-model animation set:
53 files, 11.4 MB, 1.1 KB to 1.8 MB each.  This module decodes its **table** --
which animations exist, what they are called, and where each one's channel data
begins.

⛔ It does NOT decode poses.  The keyframes are lossy-compressed fitted curves,
not a keyframe array: `core/animsets/animcompresssettings.radattr` defines
per-joint error tolerances plus separate camera / footpredict / real channel
settings, and a scan of every file finds **no raw `f32x4` quaternion runs at
all**.  Producing animation in Blender needs the channel region decoded, which
is a separate and much less certain problem -- see "What is not resolved".

## Layout

    +0x00  (ptr=0, A:u64)     A = animation count (1..66 in the corpus)
    +0x10  (ptr=0, B:u64)     B = byte size of the channel region
    +0x20  (ptr=0, C:u64)     C ~ A (unexplained; usually A or A-1)
    +0x30  animation records, stride 136, A entries
    +0x30 + 136*A             channel region, B bytes

The `(ptr, count)` pairing is the engine's usual on-disk shape: the pointer word
is nulled and the count follows it.

## Why the 136 stride is believed

Three independent checks, not one:

  1. **Every file parses.**  `0x30 + k*136` yields exactly A DISTINCT non-null
     `CSymbol64`s in **53 of 53** files.  A wrong stride does not do that.
  2. **A blind probe agrees.**  Searching for any `(base, stride)` at which A
     distinct symbols appear -- with no assumption about either -- finds the
     channel region's base at `48 + 136*A` on every file where it resolves
     uniquely (A=4 -> 592, A=7 -> 1000, A=8 -> 1136, A=10 -> 1408, A=16 -> 2224,
     A=18 -> 2496).  That formula is only consistent if the record ahead of it
     is 136 bytes.
  3. **It explains the header.**  `+0x30` was previously an unexplained hash
     word; it is simply animation 0's name.

Names recovered include `idle`, `ready`, `boost`, `kick`, `grip`, `show`,
`look_pitch`, `look_yaw`, `look_roll`, `root_ik`, `ghost_ik`,
`hand_left_gestures`, `hand_right_gestures`, `hand_right_grip_plane`.  Only 23
of 669 resolve, because `hash_lookup.json` covers a small fraction of animation
names -- the other 646 are real entries whose preimage is simply unknown.

## The 136-byte record

Measured over all 669 records in the corpus:

    +0x00  name CSymbol64                       CONFIRMED
    +0x08  u32 flag, 0 on 665 / 1 on 4          probably `looping`
    +0x0c  u32 byte offset into channel region  CONFIRMED (always a multiple
                                                of 36; tiles B exactly on 23/53)
    +0x10  u32 channel count (1 on 635)         CONFIRMED by the same tiling
    +0x14..+0x84  sparse small ints             UNMAPPED
    +0x1c +0x28 +0x34 +0x38 +0x40 +0x48
    +0x50..+0x5c +0x70 +0x78 +0x80 +0x84        ZERO in all 669 records

⚠ There is **no duration and no joint count in this record**.  Both must live in
the channel region, which is consistent with the timing being part of the
compressed curve data.

## What is not resolved

⛔ **The channel region is not uniform.**  `(offset, count)` tiles it exactly on
23 of 53 files; on the other 30 the offsets step by an alternating 36 and 56.
56 is the engine's `CTable` descriptor size, so the region most likely
interleaves descriptors with entries rather than being one flat array.  Until
that framing is settled, `channel_offset` / `channel_count` are reported as read
and NOT used to slice anything.

⛔ **No round-trip check exists.**  Every other format decoded in this project
was pinned by exact agreement -- mesh counts, version tags byte-identical across
281 archives, closure diffs.  Compressed animation is lossy, so a wrong reading
of the curve data can still produce plausible-looking numbers.  Any future
channel decode needs a visual oracle (a known pose), not a numeric one.

    python scripts/evr_animset.py --dir <extract> --list
    python scripts/evr_animset.py <model> --dir <extract>
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evr_resource_types import normalise_hash, resolve_type_dir, resource_path

#: `CAnimSetResourceWin10`.
ANIM_SET_RESOURCE = "e9e7d2e25d8e2252"

#: Header: three `(ptr, count)` pairs, then the animation records.
H_COUNT = 0x08            # u64 A -- animation count
H_CHANNEL_BYTES = 0x18    # u64 B -- byte size of the channel region
H_C = 0x28                # u64 C -- ~A, unexplained
RECORD_BASE = 0x30
RECORD_STRIDE = 136

#: Field offsets inside a record. See the module docstring for the evidence.
R_NAME = 0x00
R_FLAG = 0x08
R_CHANNEL_OFFSET = 0x0c
R_CHANNEL_COUNT = 0x10


@dataclass
class Animation:
    """One entry of an animation set."""
    index: int
    name_hash: str
    name: str                 # "" when the preimage is unknown
    flag: int                 # probably `looping`
    channel_offset: int       # byte offset into the channel region (as read)
    channel_count: int


@dataclass
class AnimSet:
    """A `CAnimSetResourceWin10`."""
    hash: str
    size: int
    count: int                # A
    channel_bytes: int        # B
    c: int                    # C
    animations: list


def load_names(path: Path | None = None) -> dict:
    """`{hash -> name}` for labelling. Absent file is not an error."""
    import evr_paths
    candidates = [path] if path else [evr_paths.hash_lookup()]
    for candidate in candidates:
        if candidate and candidate.is_file():
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            return {k.lower().replace("0x", "").rjust(16, "0"): v
                    for k, v in raw.items() if isinstance(v, str)}
    return {}


def read(root: Path, model_hash, names: dict | None = None) -> AnimSet | None:
    """Decode one animation set, or None when the model has none."""
    path = resource_path(root, ANIM_SET_RESOURCE, model_hash)
    if path is None:
        return None
    data = path.read_bytes()
    if len(data) < RECORD_BASE:
        return None
    names = names if names is not None else load_names()

    count = struct.unpack_from("<Q", data, H_COUNT)[0]
    channel_bytes = struct.unpack_from("<Q", data, H_CHANNEL_BYTES)[0]
    c_value = struct.unpack_from("<Q", data, H_C)[0]
    if not count or RECORD_BASE + count * RECORD_STRIDE > len(data):
        return None

    animations = []
    for i in range(count):
        base = RECORD_BASE + i * RECORD_STRIDE
        name_hash = normalise_hash(
            struct.unpack_from("<Q", data, base + R_NAME)[0])
        animations.append(Animation(
            index=i,
            name_hash=name_hash,
            name=names.get(name_hash, ""),
            flag=struct.unpack_from("<I", data, base + R_FLAG)[0],
            channel_offset=struct.unpack_from(
                "<I", data, base + R_CHANNEL_OFFSET)[0],
            channel_count=struct.unpack_from(
                "<I", data, base + R_CHANNEL_COUNT)[0],
        ))
    return AnimSet(hash=normalise_hash(model_hash), size=len(data),
                   count=count, channel_bytes=channel_bytes, c=c_value,
                   animations=animations)


def all_sets(root: Path) -> list:
    """Every model hash that owns an animation set."""
    directory = resolve_type_dir(Path(root), ANIM_SET_RESOURCE)
    if not directory.is_dir():
        return []
    return sorted(normalise_hash(p.stem if p.suffix == ".bin" else p.name)
                  for p in directory.iterdir() if p.is_file())


def survey(root: Path) -> dict:
    """Corpus-wide inventory: every set, every animation."""
    names = load_names()
    sets = []
    named = total = 0
    for model in all_sets(root):
        aset = read(root, model, names)
        if aset is None:
            continue
        sets.append(aset)
        total += len(aset.animations)
        named += sum(1 for a in aset.animations if a.name)
    return {"sets": sets, "animations": total, "named": named}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("model", nargs="?", help="model hash owning the set")
    ap.add_argument("--dir", default=None,
                    help="flat extract (or set EVR_EXTRACT_DIR)")
    ap.add_argument("--list", action="store_true",
                    help="survey every animation set in the extract")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args(argv)
    import evr_paths
    args.dir = evr_paths.require_extract(args.dir)

    root = Path(args.dir)
    if args.list:
        result = survey(root)
        if args.json:
            print(json.dumps({
                "sets": [{"model": s.hash, "count": s.count,
                          "animations": [{"name": a.name,
                                          "hash": a.name_hash} for a in s.animations]}
                         for s in result["sets"]]}, indent=1))
            return 0
        print(f"{len(result['sets'])} animation set(s), "
              f"{result['animations']} animations, "
              f"{result['named']} with a known name\n")
        print(f"{'model':18s} {'anims':>5s} {'chanB':>8s}  names")
        for s in result["sets"]:
            named = [a.name for a in s.animations if a.name]
            print(f"{s.hash:18s} {s.count:5d} {s.channel_bytes:8d}  "
                  f"{', '.join(named[:6])}")
        return 0

    if not args.model:
        ap.error("pass a model hash, or --list")
    aset = read(root, args.model)
    if aset is None:
        print(f"{args.model}: no animation set")
        return 1
    print(f"  model          {aset.hash}")
    print(f"  file size      {aset.size:,} B")
    print(f"  animations (A) {aset.count}")
    print(f"  channel bytes  {aset.channel_bytes:,}")
    print(f"  C              {aset.c}")
    print(f"\n  {'#':>3s} {'name':38s} {'flag':>4s} {'chOff':>8s} {'chCnt':>6s}")
    for a in aset.animations:
        label = a.name or f"({a.name_hash})"
        print(f"  {a.index:3d} {label:38s} {a.flag:4d} "
              f"{a.channel_offset:8d} {a.channel_count:6d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
