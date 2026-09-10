"""Grow a level's per-level tables so a NEW static instance can be added.

Each function takes the resource bytes and returns new bytes. They are the
table-growth half of adding a model; `evr_add_model` drives them together with
the asset, collision and archive work.

    CActorDataResource        evr_actor_table.add_actor   (the registry)
    CTransformCR              add_transform_row           (where it sits)
    CStaticInstanceModelCR    add_instance                (that it is drawn)
    CGStaticInstanceResource  add_instance_bindings       (what it is drawn with)
    CArchiveResource          add_manifest_entries        (that it is loaded)

EVERY GROWER CLONES A DONOR
---------------------------
The same discipline as the actor table: a new row is stamped from an existing
row of the same kind and only the fields whose meaning is established are
changed. Several of these records carry per-instance state nobody has decoded
(`CSIMCR`'s 88-byte record body, its 16-byte colour block, the 32-byte
`CTransformCR` component header), and cloning sidesteps every one of them.

WHAT EACH ONE HAD TO GET RIGHT
------------------------------
* **CTransformCR** is a flat component table: a 56-byte envelope then 176-byte
  rows at `56 + 176k`. The row's own layout is node hash `+0x00`, actor `+0x08`,
  quaternion `+0x20`, position `+0x30`, scale `+0x3c`. Growth bumps `data_size`
  at `+8` and `count` at `+40`.

* **CStaticInstanceModelCR** is the awkward one -- seven regions that all have to
  move together, and three of the header's count fields mirror each other
  (`@0x28`, `@0x30`, `@0x70`), with `@0x08 = 24n` and `@0xb8 = 2n` besides. The
  model table `[arr]` is SIGNED-i64 sorted and `[u16]` indexes into it, so
  adding a model whose hash sorts before an existing one **renumbers every
  later index** -- those have to be rewritten, not just appended to. `@0xa8` is
  the count the loader actually reads to find `[u16]`; a stale one is a known
  crash.

* **CGStaticInstanceResource** tiles six row tables after a 0x178 header, and
  carries `@0x170 == 4 * sum(meshdata uvcount)` as an internal check. The header
  is otherwise carried verbatim, so every size/count field a grown table touches
  has to be patched by hand before the codec will accept it back.

* **CArchiveResource** is the level's load manifest, `[u32 0][u32 count]` then
  `(u64 type, u64 name)` pairs. A resource that is not listed is not loaded.
"""
from __future__ import annotations

import os
import struct
import sys
from pathlib import Path

_RIO = r"J:\EchoVR-Tools-Launcher\quest_combat_port\tools"
if _RIO not in sys.path:
    sys.path.insert(0, _RIO)
from resource_io import cstaticinstancemodelcr as CSIMCR     # noqa: E402
from resource_io import cgstaticinstanceresource as CGSI     # noqa: E402

XF_STRIDE = 176
XF_ACTOR = 0x08
XF_ROT = 0x20
XF_POS = 0x30
XF_SCALE = 0x3C
CR_DATASIZE = 8
CR_COUNT = 40
#: ⛔ A SECOND count, and the one the loader trusts. `CBinaryStreamInspector`
#: sizes the attach from THIS field, so bumping only `CR_COUNT` ships a level
#: that loads the right number of rows and then dies with
#: "Attach size (367368) doesn't match stream size (369656)" before the level
#: ever appears. `CStaticInstanceModelCR`, `CActorDataResource` and
#: `CGStaticInstanceResource` all write both; only the transform grower did not.
CR_COUNT2 = 48
CR_BODY = 56


def _i64(v: int) -> int:
    return v - (1 << 64) if v >= (1 << 63) else v


# ── CTransformCR ────────────────────────────────────────────────────────────
def transform_rows(blob: bytes):
    """`(count, stride)` for a flat component table."""
    ds = struct.unpack_from("<Q", blob, CR_DATASIZE)[0]
    n = struct.unpack_from("<Q", blob, CR_COUNT)[0]
    return n, (ds // n if n else 0)


def find_transform_row(blob: bytes, actor: int) -> int | None:
    n, st = transform_rows(blob)
    for i in range(n):
        if struct.unpack_from("<Q", blob, CR_BODY + i * st + XF_ACTOR)[0] == actor:
            return i
    return None


def add_transform_row(blob: bytes, actor: int, donor_actor: int,
                      pos, rot=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0)) -> bytes:
    """Append one placement row, stamped from `donor_actor`'s row.

    The 32-byte component header and the trailing body are copied verbatim, so
    the new row carries whatever per-component state the donor had; only the
    actor and the TRS are written.
    """
    n, st = transform_rows(blob)
    if st != XF_STRIDE:
        raise ValueError("unexpected CTransformCR stride %d" % st)
    di = find_transform_row(blob, donor_actor)
    if di is None:
        raise ValueError("donor actor %016x has no transform row" % donor_actor)
    row = bytearray(blob[CR_BODY + di * st: CR_BODY + (di + 1) * st])
    struct.pack_into("<Q", row, XF_ACTOR, actor)
    struct.pack_into("<4f", row, XF_ROT, *rot)
    struct.pack_into("<3f", row, XF_POS, *pos)
    struct.pack_into("<3f", row, XF_SCALE, *scale)
    out = bytearray(blob)
    out[CR_BODY + n * st: CR_BODY + n * st] = row
    struct.pack_into("<Q", out, CR_DATASIZE, (n + 1) * st)
    struct.pack_into("<Q", out, CR_COUNT, n + 1)
    struct.pack_into("<Q", out, CR_COUNT2, n + 1)
    return bytes(out)


# ── CStaticInstanceModelCR ──────────────────────────────────────────────────
def add_instance(blob: bytes, entity: int, model: int, donor_entity: int) -> bytes:
    """Append one drawn instance of `model` on `entity`, cloned from a donor.

    `[arr]` is signed-i64 sorted and `[u16]` indexes into it, so inserting a
    model hash that sorts before an existing one renumbers the later entries --
    every existing index is rewritten to match rather than assumed stable.
    """
    o = CSIMCR.read(blob)
    n = o["n"]
    di = next((i for i, d in enumerate(o["dir_entries"])
               if struct.unpack_from("<Q", d, 8)[0] == donor_entity), None)
    if di is None:
        raise ValueError("donor entity %016x is not in the CSIMCR" % donor_entity)

    d = bytearray(o["dir_entries"][di])
    struct.pack_into("<Q", d, 8, entity)
    o["dir_entries"] = list(o["dir_entries"]) + [bytes(d)]

    r = bytearray(o["recs"][di])
    struct.pack_into("<Q", r, CSIMCR.MODEL_OFF, model)
    # ⛔ The 88-byte record carries the ENTITY at +8 as well, and it is unique
    # across all 732 shipped records -- it is exactly the dir entry's entity,
    # 732 of 732. Cloning the donor's record without rewriting it leaves the new
    # instance answering to the DONOR's entity: the entity appears twice in the
    # recs array and the new one not at all, and resolving it lands out of
    # range -- "Offset is past the end of the stream" on this very resource.
    struct.pack_into("<Q", r, CSIMCR_ENTITY_OFF, entity)
    o["recs"] = list(o["recs"]) + [bytes(r)]

    arr = list(o["arr"])
    idx = list(o["idx"])
    if model in arr:
        slot = arr.index(model)
    else:
        slot = len(arr)
        for i, v in enumerate(arr):
            if _i64(v) > _i64(model):
                slot = i
                break
        arr.insert(slot, model)
        idx = [v + 1 if v >= slot else v for v in idx]
    o["arr"] = arr
    o["idx"] = idx + [slot]
    o["uniq"] = len(arr)

    mid = dict(o["mid"])
    for name, w in CSIMCR.MID_FIELDS:
        buf = mid[name]
        row = buf[di * w:(di + 1) * w]
        if name == "loddistancescales":
            # Every shipped instance carries 1.0f here, but every shipped
            # instance is also INSIDE the level's boxtree and its authored
            # visibility set. A grafted-on model is in neither, so it is only
            # drawn while the camera is close and fades out as you pull away.
            # Scaling its LOD distance keeps it at full detail from anywhere.
            # ⚠ This is the one field written to a value no shipped level uses;
            # `EVR_LOD_SCALE=1` restores the stock 1.0.
            row = struct.pack("<f", float(os.environ.get("EVR_LOD_SCALE", "1000")))
        mid[name] = buf + row
    o["mid"] = mid
    o["n"] = n + 1

    # ⛔ `gap` and `pad` are DERIVED, not carried. Both rules hold on 25 of 25
    # shipped CSIMCRs with no exceptions:
    #
    #   gap = ceil(n / 64) * 8   -- a per-instance BITMAP, one bit each, rounded
    #                               up to whole 8-byte words (it is all-zero in
    #                               every shipped level, which is why a reader
    #                               that only checks "the gap is zeros" passes)
    #   pad = (-idx_end) % 8     -- realigns [mid] and [recs] to 8 bytes, where
    #                               idx_end = 680 + 24n + gap + 8*uniq + 2n
    #
    # `[u16]` is 2 bytes per instance, so the parity of `n` decides the
    # alignment: the arena ships n = 732 with pad = 0, and one added instance
    # makes n odd, `idx_end % 8` become 2 and pad have to become 6. Carrying the
    # donor's 0 leaves [mid] and [recs] six bytes early -- the file still walks
    # end-to-end (which is why a size check and a round-trip both pass) but the
    # engine reads them at aligned offsets and runs off the tail:
    # "Offset is past the end of the stream" on this very resource.
    o["gap"] = -(-o["n"] // 64) * 8
    idx_end = 680 + 24 * o["n"] + o["gap"] + 8 * o["uniq"] + 2 * o["n"]
    o["pad"] = (-idx_end) % 8

    # the header is carried verbatim, so every self-describing field it holds
    # has to be brought forward by hand or `read()` rejects the result.
    h = bytearray(o["header"])
    struct.pack_into("<Q", h, 0x40, o["gap"])
    struct.pack_into("<Q", h, 0x08, 24 * (n + 1))
    for off in (0x28, 0x30, 0x70):
        struct.pack_into("<Q", h, off, n + 1)
    struct.pack_into("<Q", h, 0xB8, 2 * (n + 1))
    cap = struct.unpack_from("<Q", h, 0xA0)[0]
    struct.pack_into("<Q", h, 0xA8, len(arr))
    if cap < len(arr):
        struct.pack_into("<Q", h, 0xA0, len(arr))
    for off in (0xD8, 0xE0, 0x110, 0x118, 0x148, 0x150, 0x180, 0x188,
                0x1B8, 0x1C0, 0x1F0, 0x1F8, 0x260, 0x268, 0x298, 0x2A0):
        if struct.unpack_from("<Q", h, off)[0] == n:
            struct.pack_into("<Q", h, off, n + 1)
    for off, per in ((0xF0, 8), (0x128, 16), (0x160, 2), (0x198, 2),
                     (0x1D0, 4), (0x240, 2), (0xB8, 2)):
        if struct.unpack_from("<Q", h, off)[0] == per * n:
            struct.pack_into("<Q", h, off, per * (n + 1))
    if struct.unpack_from("<Q", h, 0x278)[0] == 88 * n:
        struct.pack_into("<Q", h, 0x278, 88 * (n + 1))
    o["header"] = bytes(h)
    return CSIMCR.write(o)


# ── CGStaticInstanceResource ────────────────────────────────────────────────
#: The 88-byte CSIMCR record's entity column (verified: equals the dir
#: entry's entity on 732 of 732 shipped records).
CSIMCR_ENTITY_OFF = 8


#: The "no limit / not in a group" spelling the shipped tables use.
NO_LIMIT = 0xFFFFFFFF


def _skey(value: int) -> int:
    """The engine's sort key: these u64 columns are compared SIGNED."""
    return value - (1 << 64) if value >= (1 << 63) else value


def _insert_sorted(rows: list, row) -> int:
    """Insert `row` at its signed-i64 sorted position on column 0.

    ⛔ NOT an append. `assetdata`, `instancedata` and `meshdata` are each
    signed-i64 sorted by their key column and the engine BINARY-SEARCHES them.
    Appending a hash that sorts earlier leaves the array unsorted, the search
    then misses, and the level dies -- either with
    "Level static instance data has no info for instanced model asset <hash>"
    (cbaseinstancemodelcs.cpp) or, once a grown CStaticInstanceModelCR turns the
    failed lookup into an index, with "Offset is past the end of the stream".
    This is the same trap `add_instance` already handles for the CSIMCR's
    `[arr]`; nothing was doing it for the CGSI.
    """
    import bisect
    pos = bisect.bisect_left([_skey(r[0]) for r in rows], _skey(row[0]))
    rows.insert(pos, row)
    return pos


def add_instance_bindings(blob: bytes, entity: int, model: int,
                          donor_entity: int, donor_model: int,
                          uvcount: int | None = None):
    """Add the asset / instance / mesh rows the renderer needs, plus GPU growth.

    Returns `(new bytes, extra GPU bytes)`. The level refuses to load an
    instance whose model has no `assetdata` entry, so a genuinely new model
    gets one; re-placing an existing model only adds `instancedata`.
    """
    o = CGSI.read(blob)
    S = o["sections"]
    asset = list(S["assetdata"])
    inst = list(S["instancedata"])
    mesh = list(S["meshdata"])

    da = next((r for r in asset if r[0] == donor_model), None)
    if da is None:
        raise ValueError("donor model %016x has no assetdata row" % donor_model)
    if not any(r[0] == model for r in asset):
        # ⛔ NOT a straight clone of the donor's tail, and NOT an empty run.
        #
        # `assetdata[1:3]` is a `(start, count)` run into `shadersetoverrides`,
        # and across the 98 shipped rows those runs PARTITION that section
        # exactly: all 260 slots covered once, sum(count) == count. Cloning the
        # donor's run makes the new asset claim slots the donor already owns and
        # the implied total runs off the end.
        #
        # An EMPTY run does not fix it either: no shipped asset has count 0 --
        # the counts are 1,2,3,4,5,7,8,10 over all 98 -- so the engine reads
        # `overrides[start]` unconditionally, and a run parked at `len(overrides)`
        # dereferences exactly one past the end. Both spellings die the same way,
        # on the CStaticInstanceModelCR with "Offset is past the end of the
        # stream".
        #
        # So the new asset gets a REAL override of its own, appended to the
        # section and stamped from the donor's first: `(shaderset, slot)` where
        # the shaderset is one that exists on disk. The partition stays exact
        # and every asset keeps count >= 1.
        overrides = S["shadersetoverrides"]
        start = len(overrides)
        overrides.append(overrides[da[1]] if da[2] else overrides[0])
        # `assetdata[3]` takes exactly two shipped values: 10 on 64 of the 98
        # arena assets and 0xFFFFFFFF on the other 34. A new model is not part
        # of the level's authored LOD/fade scheme, so it takes the SENTINEL --
        # a spelling the game already ships rather than an invented number.
        # With the donor's 10 the geometry fades out as the camera pulls away
        # and pops back in on approach.
        _insert_sorted(asset, (model, start, 1, NO_LIMIT, da[4]))

    di = next((r for r in inst if r[0] == donor_entity), None)
    if di is None:
        raise ValueError("donor entity %016x has no instancedata row" % donor_entity)
    # ⛔ NOT a straight clone. `instancedata[4]` is a SLOT INDEX and across the
    # 732 shipped rows it is a PERMUTATION of 0..n-1 -- 732 distinct values, no
    # gaps. Cloning the donor's leaves every new instance sitting on the donor's
    # slot (13 copies of 0) while 732..744 are never claimed, and the level then
    # dies on the CStaticInstanceModelCR with "Offset is past the end of the
    # stream". Each new instance takes the next free slot instead.
    row = list(di)
    row[0], row[4] = entity, len(inst)
    # `instancedata[1]` is 0xFFFFFFFF on 176 of the 732 shipped rows and a small
    # 0..4 index on the rest -- a group the new instance does not belong to, so
    # it takes the sentinel too.
    row[1] = NO_LIMIT
    _insert_sorted(inst, tuple(row))

    extra = 0
    if uvcount:
        uvoff = sum(r[2] for r in mesh)
        mesh.append((entity, uvoff, uvcount))
        extra = 4 * uvcount

    S["assetdata"], S["instancedata"], S["meshdata"] = asset, inst, mesh
    h = bytearray(o["header"])
    for base, name, _fmt, stride in CGSI.SECTIONS:
        c = len(S[name])
        struct.pack_into("<Q", h, base + 0x08, c * stride)
        struct.pack_into("<Q", h, base + 0x28, c)
        struct.pack_into("<Q", h, base + 0x30, c)
    struct.pack_into("<Q", h, 0x170, 4 * sum(r[2] for r in mesh))
    o["header"] = bytes(h)
    return CGSI.write(o), extra


# ── CArchiveResource ────────────────────────────────────────────────────────
def add_manifest_entries(blob: bytes, entries) -> bytes:
    """Add `(type hash, name hash)` pairs to the level's load manifest.

    A resource the manifest does not name is never loaded, so every new asset
    the level gains has to appear here. Pairs already present are skipped.
    """
    count = struct.unpack_from("<I", blob, 4)[0]
    have = {struct.unpack_from("<QQ", blob, 8 + 16 * i) for i in range(count)}
    body = bytearray(blob[:8 + 16 * count])
    tail = blob[8 + 16 * count:]
    added = 0
    for t, nm in entries:
        if (t, nm) in have:
            continue
        body += struct.pack("<QQ", t, nm)
        have.add((t, nm))
        added += 1
    struct.pack_into("<I", body, 4, count + added)
    return bytes(body) + tail
