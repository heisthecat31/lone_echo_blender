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

## The channel region IS uniform -- it is a versioned tagged block

The earlier reading of this region as a flat array, with an unexplained
alternation of 36 and 56, was wrong.  Each `channel_offset` points at a block
with a self-describing header, and the blocks differ in size because they
differ in VERSION, not because descriptors are interleaved with entries:

    +0x00  u32  version          1 on 6102 of 6537 blocks; also 3, 4, 6, 11, 18
    +0x04  u64  name             per-block, unaligned (the struct is packed)
    +0x0c  u64  owner            THE ANIMSET'S OWN HASH -- 6102 of 6537
    +0x14  u64  X                never resolves to a resource; unidentified
    +0x1c  u64  Y                entry count; 0 means header-only

Measured over all 1423 LE2 animsets (6537 blocks):

  * `owner` equals the file's own name on **6102 of 6537** blocks.  A field
    that reproduces the filename on 93% of samples is not a coincidence, and
    it is what identifies the header as a header.
  * `version == 1 and Y == 0` spans **exactly 36 bytes on 4595 of 4595**.  No
    exceptions.  That is where "always a multiple of 36" came from -- 36 is
    the bare header, not an array stride.
  * `version == 1 and Y == 1` spans 468 bytes on **1261 of 1262**, a 432-byte
    body that is 16 records of 24 bytes, almost all `[-1][0][0]`.  A table of
    fixed slots with a null id, so this block is a BINDING/SLOT descriptor --
    it is not pose data and does not contain any.

So `channel_offset` / `channel_count` can now be used to slice, and this
module does (`channel_blocks`).

## What is not resolved

⛔ **The pose data is not in the channel region at all -- it is after it, and
this module does not describe that region.**  `0x30 + 136*A + B` leaves a
trailing region that the layout above never accounts for: **1.63 GB across the
1423 LE2 animsets**, up to 65 MB in a single file (`b3489c36717b6e4c`, 29
animations, `B` only 1044).  Any future pose decode starts there, not in the
channel region.

What is known about it, from `b3489c36717b6e4c`'s 65 MB:

  * It is NOT block-compressed -- 4.05 bits/byte, 41.6% zero.  A packed or
    quantised layout, not zlib/LZ4, so it can be read in place.
  * It does contain genuine `[unit quat][vec3][f32 1.0]` 32-byte transforms --
    e.g. at trailing+156, `(0.17505, -0.69294, 0.21955, 0.66406)` with a norm
    of 1.00002 -- but only in ONES AND TWOS.

⛔ **The rotations are not stored as raw float quaternions.**  This CONFIRMS
the original finding rather than overturning it, now on LE2 as well as EchoVR
and by an exhaustive rather than a sampled scan: sweeping all 65 MB at every
4-byte phase finds **zero** runs of 16 or more consecutive unit quaternions at
stride 16, and the longest run at any phase is 3 (stride 16) or 5 (stride 32).
Bulk rotation is quantised.  Decoding it needs a visual oracle (a known pose),
not a numeric one -- a wrong reading of quantised curve data still produces
plausible-looking numbers.

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


#: A channel block's self-describing header. See the module docstring.
B_VERSION, B_NAME, B_OWNER, B_X, B_COUNT = 0x00, 0x04, 0x0c, 0x14, 0x1c
BLOCK_HEADER = 0x24                       # 36 bytes


@dataclass
class ChannelBlock:
    """One block of the channel region, as framed by its own header."""
    animation: int
    offset: int               # byte offset into the channel region
    span: int                 # to the next block, or to the region's end
    version: int
    name_hash: str
    owner_hash: str
    x: int
    count: int                # Y -- 0 means header-only

    @property
    def header_only(self) -> bool:
        return self.span == BLOCK_HEADER


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


def channel_blocks(root: Path, model_hash) -> list:
    """`[ChannelBlock, ...]` for one animation set, in region order.

    Frames the channel region by each block's own header rather than by a
    guessed stride -- see the module docstring for why the flat-array reading
    was wrong and what pins this one.
    """
    path = resource_path(root, ANIM_SET_RESOURCE, model_hash)
    if path is None:
        return []
    data = path.read_bytes()
    if len(data) < RECORD_BASE:
        return []
    count = struct.unpack_from("<Q", data, H_COUNT)[0]
    channel_bytes = struct.unpack_from("<Q", data, H_CHANNEL_BYTES)[0]
    if not count or RECORD_BASE + count * RECORD_STRIDE > len(data):
        return []
    base = RECORD_BASE + count * RECORD_STRIDE
    if base + channel_bytes > len(data):
        return []

    starts = []
    for i in range(count):
        offset = struct.unpack_from(
            "<I", data, RECORD_BASE + i * RECORD_STRIDE + R_CHANNEL_OFFSET)[0]
        if offset <= channel_bytes:
            starts.append((offset, i))
    starts.sort()
    bounds = [o for o, _ in starts] + [channel_bytes]

    out = []
    for k, (offset, animation) in enumerate(starts):
        at = base + offset
        if at + BLOCK_HEADER > len(data):
            continue
        out.append(ChannelBlock(
            animation=animation, offset=offset, span=bounds[k + 1] - offset,
            version=struct.unpack_from("<I", data, at + B_VERSION)[0],
            name_hash=normalise_hash(
                struct.unpack_from("<Q", data, at + B_NAME)[0]),
            owner_hash=normalise_hash(
                struct.unpack_from("<Q", data, at + B_OWNER)[0]),
            x=struct.unpack_from("<Q", data, at + B_X)[0],
            count=struct.unpack_from("<Q", data, at + B_COUNT)[0]))
    return out


def payload_region(root: Path, model_hash):
    """`(offset, size)` of the region AFTER the channel region, or None.

    This is where the pose data lives and it is not decoded -- see the module
    docstring. Exposed so a caller can find it without re-deriving the header
    arithmetic, and so its size is visible rather than silently ignored.
    """
    path = resource_path(root, ANIM_SET_RESOURCE, model_hash)
    if path is None:
        return None
    size = path.stat().st_size
    with path.open("rb") as handle:
        data = handle.read(RECORD_BASE)
    if len(data) < RECORD_BASE:
        return None
    count = struct.unpack_from("<Q", data, H_COUNT)[0]
    channel_bytes = struct.unpack_from("<Q", data, H_CHANNEL_BYTES)[0]
    if not count:
        return None
    start = RECORD_BASE + count * RECORD_STRIDE + channel_bytes
    if start > size:
        return None
    return start, size - start


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


# ── the channel region ──────────────────────────────────────────────────

#: Per-bone channel mask, in the high nibble of the table row's flag byte.
TRA_CONST = 0x1        # one vec3, in the constant block
TRA_ANIM = 0x2         # one vec3 per frame
ROT_CONST = 0x4        # one quaternion (xyzw), in the constant block
ROT_ANIM = 0x8         # one quaternion per frame

ROT_BYTES = 16
TRA_BYTES = 12

#: Trailer geometry. Each payload ends with
#:
#:   [bone table: 6*n][gap][slot map: 2*n u16][pad][model hash: 8]
#:
#: Neither the gap nor the pad is a function of `n`. The gap is two zero bytes
#: followed by enough `0xFF` to land the slot map on an 8-byte boundary -- 10
#: bytes on a 20-bone set, 12 on a 21-bone one -- and the pad is whatever it
#: then takes to land the hash 8-aligned (0 and 6 on those same two). Both are
#: therefore SEARCHED, and the candidate is accepted only if the table it
#: implies reads as a table.
TRAILER_ROW = 6
TRAILER_GAP = range(8, 20)
MAX_TRAILER_PAD = 8

#: A payload's frame data is followed by padding before the table.
MAX_FRAME_PAD = 16

#: How far a stored quaternion may be from unit length before the decode is
#: refused. These are raw f32, so the tolerance is numerical, not lossy-codec.
QUAT_TOLERANCE = 5e-3


@dataclass
class BoneChannel:
    """What one bone contributes to a pose."""
    slot: int
    bone: int              # index into the model's skeleton
    mask: int
    frame_offset: int      # byte offset of its per-frame data inside a frame
    const_offset: int      # byte offset of its constant data


@dataclass
class Channels:
    """One animation's decoded pose data."""
    index: int
    name: str
    name_hash: str
    bones: int
    frames: int
    frame_bytes: int
    const_bytes: int
    start: int             # file offset of the constant block
    pad: int
    channels: list

    def pose(self, blob: bytes, frame: int = 0) -> list:
        """`[(quat | None, translation | None), ...]`, one entry per slot."""
        const = self.start
        anim = self.start + self.const_bytes + frame * self.frame_bytes
        out = []
        for channel in self.channels:
            rot = tra = None
            if channel.mask & ROT_CONST:
                rot = struct.unpack_from("<4f", blob, const)
                const += ROT_BYTES
            if channel.mask & TRA_CONST:
                tra = struct.unpack_from("<3f", blob, const)
                const += TRA_BYTES
            if channel.mask & ROT_ANIM:
                rot = struct.unpack_from("<4f", blob, anim)
                anim += ROT_BYTES
            if channel.mask & TRA_ANIM:
                tra = struct.unpack_from("<3f", blob, anim)
                anim += TRA_BYTES
            out.append((rot, tra))
        return out


def _markers(blob: bytes, model_hash: int) -> list:
    out, at = [], 0
    needle = struct.pack("<Q", model_hash)
    while True:
        at = blob.find(needle, at)
        if at < 0:
            return out
        out.append(at)
        at += 8


def _slot_map(blob: bytes, hash_at: int):
    """`(offset, slots)` of the ascending `u16` slot map ending before the hash.

    Searched rather than computed: the pad between the map and the hash is 0 on
    a 20-bone set and 6 on a 21-bone one, so it is not a function of the width.
    """
    best = None
    for pad in range(MAX_TRAILER_PAD):
        end = hash_at - pad
        run = []
        at = end - 2
        while at >= 0:
            value = struct.unpack_from("<H", blob, at)[0]
            if value >= 4096 or (run and value >= run[0]):
                break
            run.insert(0, value)
            at -= 2
            if run[0] == 0:
                break
        if len(run) >= 2 and run[0] == 0 and (best is None or len(run) > len(best[1])):
            best = (end - 2 * len(run), run)
    return best


def _grow_table(blob: bytes, end: int, limit: int = 512):
    """Rows of the bone table, grown BACKWARDS from its end.

    The row count is not stated anywhere and is not the slot map's length -- one
    corpus file has a 10-row table against an 8-entry map -- so the lattice is
    walked back while the rows still read as rows. Trailing all-zero rows cost
    nothing: a bone with no channels contributes no bytes to either block, so
    over-reaching by a few of them cannot change the arithmetic that gates the
    decode, only the slot numbering.
    """
    rows = []
    at = end - TRAILER_ROW
    while at >= 0 and len(rows) < limit:
        frame_off, flag, const_off = struct.unpack_from("<HxBH", blob, at)
        if flag & 0x0F or frame_off >= 0xF000 or const_off >= 0xF000:
            break
        if rows and (frame_off > rows[0][0] or const_off > rows[0][2]):
            break
        rows.insert(0, (frame_off, flag, const_off))
        at -= TRAILER_ROW
    while rows and rows[0] == (0, 0, 0) and len(rows) > 1 and rows[1] == (0, 0, 0):
        # keep at most one leading empty row beyond the first live one
        if any(r[1] for r in rows[:2]):
            break
        rows.pop(0)
    return rows


def _trailer(blob: bytes, hash_at: int, bones: int):
    """`(table_base, slot_map)` for the payload ending at `hash_at`, or None.

    The slot map is `bones` ascending u16s. Its distance from the hash is NOT a
    function of `bones` -- measured across the corpus the pad is 0 on a 20-bone
    set and 6 on a 12-bone one -- so it is searched for rather than computed.
    """
    for pad in range(MAX_TRAILER_PAD):
        at = hash_at - pad - 2 * bones
        if at < 0:
            continue
        slots = struct.unpack_from("<%dH" % bones, blob, at)
        if list(slots) != sorted(set(slots)) or slots[0] != 0:
            continue
        if max(slots) >= 4096:
            continue
        for gap in TRAILER_GAP:
            base = at - gap - TRAILER_ROW * bones
            if base >= 0 and _table_reads_as_a_table(blob, base, bones):
                return base, list(slots)
    return None


def _table_reads_as_a_table(blob: bytes, base: int, bones: int) -> bool:
    """Are the `bones` rows at `base` a bone table, or a window into one?

    Three things a misaligned window does not survive: the flag byte's low
    nibble is zero on every row, and BOTH offset columns are non-decreasing
    down the table (they are running byte cursors into the constant block and
    into a frame).
    """
    last_frame = last_const = -1
    live = False
    for i in range(bones):
        if base + i * TRAILER_ROW + TRAILER_ROW > len(blob):
            return False
        frame_off, flag, const_off = struct.unpack_from(
            "<HxBH", blob, base + i * TRAILER_ROW)
        if flag & 0x0F:
            return False
        if frame_off < last_frame or const_off < last_const:
            return False
        last_frame, last_const = frame_off, const_off
        live = live or bool(flag)
    return live


def _read_table(blob: bytes, base: int, slots: list) -> list:
    out = []
    for i, bone in enumerate(slots):
        frame_off, flag, const_off = struct.unpack_from(
            "<HxBH", blob, base + i * TRAILER_ROW)
        mask = flag >> 4
        if flag & 0x0F:
            return []                       # low nibble is always zero
        out.append(BoneChannel(slot=i, bone=bone, mask=mask,
                               frame_offset=frame_off, const_offset=const_off))
    return out


def _sizes(channels: list) -> tuple:
    const = frame = 0
    for c in channels:
        const += ROT_BYTES if c.mask & ROT_CONST else 0
        const += TRA_BYTES if c.mask & TRA_CONST else 0
        frame += ROT_BYTES if c.mask & ROT_ANIM else 0
        frame += TRA_BYTES if c.mask & TRA_ANIM else 0
    return const, frame


def _quats_are_unit(blob: bytes, entry: Channels) -> bool:
    for frame in range(max(entry.frames, 1)):
        for rot, _tra in entry.pose(blob, frame):
            if rot is None:
                continue
            total = sum(c * c for c in rot)
            if not (abs(total - 1.0) < 2 * QUAT_TOLERANCE):
                return False
    return True


def _first_start(blob, table_base, channels, const, frame) -> int | None:
    """The first payload has no marker ahead of it, so its start is searched.

    Every later payload begins where the previous one's model hash ends; the
    first begins after the header blocks, whose size this module does not model.
    Candidates are 8-aligned and accepted only when the constant block they
    imply holds unit quaternions -- which a misaligned window does not.
    """
    best = None
    for pad in range(MAX_FRAME_PAD):
        end = table_base - pad
        frames_range = range(0, 4096) if frame else range(1)
        for frames in frames_range:
            start = end - const - frames * frame
            if start < 0:
                break
            if start % 8:
                continue
            if _const_block_is_sane(blob, start, channels):
                if best is None or start > best:
                    best = start
    return best


def _const_block_is_sane(blob: bytes, start: int, channels: list) -> bool:
    at = start
    seen = False
    for c in channels:
        if c.mask & ROT_CONST:
            if at + ROT_BYTES > len(blob):
                return False
            quat = struct.unpack_from("<4f", blob, at)
            if abs(sum(v * v for v in quat) - 1.0) > QUAT_TOLERANCE:
                return False
            seen = True
            at += ROT_BYTES
        if c.mask & TRA_CONST:
            at += TRA_BYTES
    return seen


#: Largest bone count a solved trailer may claim.
MAX_BONES = 512


def _solve_bones(blob: bytes, hash_at: int, hint: int = 0):
    """The slot count for this payload, largest first.

    ⭐ Deliberately NOT taken from the model's skeleton. Two thirds of the
    corpus's animation sets belong to a model with no readable
    `CSkeletonResource`, and the trailer states its own width: taking it from
    the file decodes those too, and where a skeleton IS readable the solved
    count agreeing with its bone count is a free cross-check.
    """
    order = ([hint] if hint else []) + list(range(MAX_BONES, 1, -1))
    for bones in order:
        found = _trailer(blob, hash_at, bones)
        if found is not None:
            return bones, found
    return 0, None


def channels(root: Path, model_hash, bones: int = 0,
             names: dict | None = None):
    """Decode a set's channel region: `(blob, [Channels, ...])`.

    `bones` is an optional hint (the model's skeleton bone count); the trailer
    states its own width, so it is solved when the hint is absent or wrong.

    Returns `(blob, [])` when the region does not check out, rather than a
    partial read -- see `_quats_are_unit` and the padding gate below.
    """
    path = resource_path(root, ANIM_SET_RESOURCE, model_hash)
    if path is None:
        return b"", []
    blob = path.read_bytes()
    aset = read(root, model_hash, names)

    payloads = []
    hint = bones
    for hash_at in _markers(blob, int(normalise_hash(model_hash), 16)):
        width, found = _solve_bones(blob, hash_at, hint)
        if found is None:
            continue
        hint = width
        base, slots = found
        table = _read_table(blob, base, slots)
        if not table or not any(c.mask for c in table):
            continue
        payloads.append((hash_at, base, table))

    out = []
    previous = None
    for i, (hash_at, base, table) in enumerate(payloads):
        const, frame = _sizes(table)
        if previous is None:
            start = _first_start(blob, base, table, const, frame)
            if start is None:
                previous = hash_at + 8
                continue
        else:
            start = previous
        available = base - start - const
        frames = (available // frame) if frame else 0
        pad = available - frames * frame
        previous = hash_at + 8
        if not (0 <= pad < MAX_FRAME_PAD):
            continue
        animation = aset.animations[i] if aset and i < len(aset.animations) else None
        entry = Channels(
            index=i,
            name=animation.name if animation else "",
            name_hash=animation.name_hash if animation else "",
            bones=len(table), frames=frames, frame_bytes=frame,
            const_bytes=const,
            start=start, pad=pad, channels=table)
        if not _quats_are_unit(blob, entry):
            continue
        out.append(entry)
    return blob, out


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
