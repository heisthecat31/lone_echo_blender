"""Add actors to a level's `CActorDataResource` -- the table that gated new models.

    import evr_actor_table as AT
    blob, index = AT.add_actor(blob, actor_hash, donor=some_existing_actor_hash)

WHY THIS EXISTS
---------------
Everything else needed to add geometry to a level was already reachable: a
`CTransformCR` row, a `CStaticInstanceModelCR` record, a `CGStaticInstanceResource`
entry, a collision body and a BVH graft. The one missing link was the actor id.
An instance references an actor, and an actor the level does not know is a level
that will not load, so new models could not ship.

THE TABLE
---------
`CActorDataResource` is a 0x308-byte image region of container descriptors
followed by their payloads (see `resource_io/cactordataresource.py`, whose
grammar this builds on and whose round-trip is byte-exact on the arena file).
Two counts drive it:

    K = 1553   leader entries: {chain u64, hash u64}, ONE PER NAME
    N = 1459   actors: every per-actor array is N long

`K > N` because 94 hashes are aliases -- an actor known by a second name -- and
they simply repeat an existing `chain`. Measured on `mpl_arena_a`:

* the leader table is sorted **ascending as SIGNED int64** on `hash` (1553/1553);
  read unsigned it looks rotated, which is what "circularly sorted" was really
  seeing -- the rotation point is exactly where the sign flips
* `chain` is the **actor index**, in `[0, N)`; 1,552 entries land in range, 94 of
  them duplicating another entry's index (the aliases), and one entry carries
  the sentinel `chain == N`
* index 932 is claimed by no name at all, and `head` is 932 -- the header word
  names the one unowned slot
* every per-actor array is indexed by that `chain`: `prefablookup` (8 B),
  `dyn0`/`dyn1` (8 B), `dyn2` (48 B, a `CTransfQS`), `dyn3`/`dyn4` (2 B), plus
  `dyn5` and four `CBitMap`s carrying one bit per actor

All 732 of the arena's static instances resolve here, so this really is the
registry an added instance has to join.

CLONE A DONOR, DO NOT INVENT A ROW
----------------------------------
`dyn0`, `dyn1`, `dyn2` and `dyn4` vary per actor and their meaning is NOT
established -- `dyn2` is a transform but not the world placement (that stays in
`CTransformCR`; the two disagree on every actor checked). Rather than guess, a
new actor is stamped from a DONOR: an existing actor of the kind you are adding,
whose every per-actor field and every bit is copied verbatim. Only the identity
changes. The new actor is then, by construction, indistinguishable to the engine
from one that already works.

`pick_donor` looks for a plain one -- no `dyn0`, no `dyn1`, `dyn3`/`dyn4` unset,
and belonging to no group -- so nothing is inherited that ties the new actor to
another object.

GROWTH IS CHEAP
---------------
Appending at index `N` disturbs nothing: `chain` values are actor indices, not
leader positions, so inserting into the sorted leader table leaves every
existing actor's index intact. The bit arrays are sized in whole 8-byte words
(184 B = 1,472 bits), so the arena has 13 spare bits before any of them has to
grow; `add_actor` grows them when it must and says so.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

_RIO = r"J:\EchoVR-Tools-Launcher\quest_combat_port\tools"
if _RIO not in sys.path:
    sys.path.insert(0, _RIO)
from resource_io import cactordataresource as ADR    # noqa: E402

NONE64 = 0xFFFFFFFFFFFFFFFF
NONE16 = 0xFFFF
#: per-actor arrays, as (key into the decoded dict, element size in bytes)
PER_ACTOR = (("prefab_payload", 8), ("dyn0", 8), ("dyn1", 8),
             ("dyn2", 48), ("dyn3", 2), ("dyn4", 2))


def _s64(v: int) -> int:
    return v - (1 << 64) if v >> 63 else v


def _bytes_of(words) -> bytearray:
    return bytearray(struct.pack("<%dH" % len(words), *words))


def _words_of(buf: bytes):
    return list(struct.unpack_from("<%dH" % (len(buf) // 2), buf, 0))


class ActorTable:
    """The decoded table, with the per-actor payloads unpacked to bytes."""

    def __init__(self, blob: bytes):
        self.r = ADR.read(blob)
        self.N = self.r["N"]
        self.K = self.r["K"]
        self.arr = {"prefab_payload": _bytes_of(self.r["prefab_payload"])}
        for i, key in enumerate(("dyn0", "dyn1", "dyn2", "dyn3", "dyn4", "dyn5")):
            self.arr[key] = _bytes_of(self.r["dyn_payloads"][i])
        self.bits = list(self.r["bit_words"])
        self.words_per_bitmap = self.r["bmaps"][0][2]

    # ── lookup ───────────────────────────────────────────────────────────
    def index_of(self, actor: int) -> int | None:
        for chain, h in self.r["pairs"]:
            if h == actor and chain < self.N:
                return chain
        return None

    def field(self, index: int, key: str) -> int:
        _k, size = next(p for p in PER_ACTOR if p[0] == key)
        buf = self.arr[key]
        fmt = {8: "<Q", 2: "<H"}.get(size)
        return struct.unpack_from(fmt, buf, index * size)[0] if fmt else None

    def bit(self, bitmap: int, index: int) -> int:
        w = self.bits[bitmap * self.words_per_bitmap + index // 64]
        return (w >> (index % 64)) & 1

    def _set_bit(self, bitmap: int, index: int, value: int) -> None:
        i = bitmap * self.words_per_bitmap + index // 64
        mask = 1 << (index % 64)
        self.bits[i] = (self.bits[i] | mask) if value else (self.bits[i] & ~mask)

    def in_group(self, index: int) -> bool:
        return index in set(self.r["mem"])

    def pick_donor(self, candidates) -> int | None:
        """The plainest actor among `candidates` (actor hashes) to stamp from.

        Plain means nothing that would tie the new actor to another object:
        no `dyn0`, no `dyn1`, `dyn3`/`dyn4` unset, and not a member of any
        group. Falls back to the least-encumbered candidate if none is clean.
        """
        mem = set(self.r["mem"])
        best, best_score = None, None
        for a in candidates:
            i = self.index_of(a)
            if i is None:
                continue
            score = (self.field(i, "dyn0") != NONE64) + (self.field(i, "dyn1") != NONE64) \
                + (self.field(i, "dyn3") != NONE16) + (self.field(i, "dyn4") != NONE16) \
                + (i in mem)
            if best_score is None or score < best_score:
                best, best_score = i, score
            if score == 0:
                break
        return best

    # ── growth ───────────────────────────────────────────────────────────
    def add(self, actor: int, donor_index: int, prefab: int | None = None) -> int:
        """Append one actor stamped from `donor_index`. Returns its index."""
        if self.index_of(actor) is not None:
            raise ValueError("actor %016x is already in the table" % actor)
        new = self.N
        for key, size in PER_ACTOR:
            buf = self.arr[key]
            if len(buf) < (new + 1) * size:
                buf.extend(buf[donor_index * size:(donor_index + 1) * size])
            else:
                buf[new * size:(new + 1) * size] = buf[donor_index * size:
                                                       (donor_index + 1) * size]
        if prefab is not None:
            struct.pack_into("<Q", self.arr["prefab_payload"], new * 8, prefab)

        # bit arrays: one bit per actor, in whole 8-byte words. Grow only when
        # the new actor does not fit the words already there.
        need_words = (new + 1 + 63) // 64
        if need_words > self.words_per_bitmap:
            grown = []
            for b in range(4):
                seg = self.bits[b * self.words_per_bitmap:(b + 1) * self.words_per_bitmap]
                grown.extend(seg + [0] * (need_words - self.words_per_bitmap))
            self.bits = grown
            self.words_per_bitmap = need_words
            d5 = self.arr["dyn5"]
            d5.extend(b"\x00" * ((need_words * 8) - len(d5)))
        for b in range(4):
            self._set_bit(b, new, self.bit(b, donor_index))
        d5 = self.arr["dyn5"]
        wi = donor_index // 64
        src = struct.unpack_from("<Q", d5, wi * 8)[0] if (wi + 1) * 8 <= len(d5) else 0
        dbit = (src >> (donor_index % 64)) & 1
        wj = new // 64
        if (wj + 1) * 8 > len(d5):
            d5.extend(b"\x00" * ((wj + 1) * 8 - len(d5)))
        cur = struct.unpack_from("<Q", d5, wj * 8)[0]
        mask = 1 << (new % 64)
        struct.pack_into("<Q", d5, wj * 8, (cur | mask) if dbit else (cur & ~mask))

        # leader entry, keeping the signed-int64 ordering
        pairs = list(self.r["pairs"])
        at = len(pairs)
        for i, (_c, h) in enumerate(pairs):
            if _s64(h) > _s64(actor):
                at = i
                break
        pairs.insert(at, (new, actor))
        self.r["pairs"] = pairs
        self.K += 1
        self.N += 1
        return new

    # ── write ────────────────────────────────────────────────────────────
    def to_bytes(self) -> bytes:
        r = dict(self.r)
        r["N"] = self.N
        r["K"] = self.K
        r["p_bs"] = len(self.arr["prefab_payload"])
        r["prefab_payload"] = _words_of(bytes(self.arr["prefab_payload"]))
        r["dyn_payloads"] = [_words_of(bytes(self.arr[k]))
                             for k in ("dyn0", "dyn1", "dyn2", "dyn3", "dyn4", "dyn5")]
        counts = [self.N, self.N, self.N, self.N, self.N, self.words_per_bitmap]
        r["dyn"] = [(len(self.arr[k]), c) for k, c in
                    zip(("dyn0", "dyn1", "dyn2", "dyn3", "dyn4", "dyn5"), counts)]
        by = self.words_per_bitmap * 8
        r["bmaps"] = [(self.N, by, self.words_per_bitmap) for _ in range(4)]
        r["bit_words"] = self.bits
        return ADR.write(r)


def add_actor(blob: bytes, actor: int, donor: int, prefab: int | None = None):
    """Convenience: add one actor stamped from the actor hash `donor`."""
    t = ActorTable(blob)
    di = t.index_of(donor)
    if di is None:
        raise ValueError("donor %016x is not in the table" % donor)
    idx = t.add(actor, di, prefab)
    return t.to_bytes(), idx
