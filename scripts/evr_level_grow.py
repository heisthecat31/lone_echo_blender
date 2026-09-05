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
        mid[name] = buf + buf[di * w:(di + 1) * w]
    o["mid"] = mid
    o["n"] = n + 1

    # the header is carried verbatim, so every self-describing field it holds
    # has to be brought forward by hand or `read()` rejects the result.
    h = bytearray(o["header"])
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
        asset.append((model,) + tuple(da[1:]))

    di = next((r for r in inst if r[0] == donor_entity), None)
    if di is None:
        raise ValueError("donor entity %016x has no instancedata row" % donor_entity)
    inst.append((entity,) + tuple(di[1:]))

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
