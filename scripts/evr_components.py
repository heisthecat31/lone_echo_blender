"""Echo VR level COMPONENT tables (`*CR` resources) -- a self-describing decoder.

## What this is

A level is a graph of actors; each `*CRWin10` resource is one COMPONENT TABLE
saying which actors carry that component. `evr_scene_extract` reads four of them
(model / instance-model / static-instance-model / transform) and ignores the
rest -- of the 53 component types `mpl_combat_war_room` ships, 38 were read by
no tool in this repo.

## The framing is IN THE FILE -- no per-type stride table is needed

Every `*CR` table opens with the engine's standard `CTable` header, the same one
`resource_io/cgstaticinstanceresource.py` documents for CGSI ("size u64 @+0x08
and count u64 @+0x28, mirrored @+0x30"):

    +0x08  u32   record-array size IN BYTES
    +0x28  u32   record count
    +0x30  u32   the same count again (mirror)
    0x38   ----  the records begin here (HDR = 56)
                 stride = size / count
    then         an optional trailing HEAP

So a reader derives the stride per file and never guesses. Measured across the
whole shipped extract: the header parses on every file of every type tried,
gives ONE stride per component type, and those strides reproduce the ones
recovered independently (billboard 80, decal 296, transform 176, sound 80).

Two earlier methods that are WRONG, recorded so they are not retried:

* "`+0x00` is a constant known name" -- rejects six types that are fine. A table
  may hold several component variants, so `+0x00` legitimately varies;
  `CDecalCR` does it in 13 of its 19 files.
* "STRIDE = GCD of file-size differences, HDR = size % stride" -- gets the
  stride right but the HEADER wrong whenever a type has a trailing heap,
  inventing headers of 0/16/24. HDR is 56 for every type. The bogus header
  shifts the records and quietly costs accuracy: `CTextureOverrideCR` read 99%
  under it and reads 100% with HDR 56.

## Validation

Decode `+0x08` of every record and ask how many are real actor nodeids in that
level's own `CActorDataResource`. A wrong framing walks that field through
arbitrary bytes and the rate collapses. Corpus-wide it is 100% for every
component below except `CStaticInstanceModelCR` (93%) and `CEventCR` (0%).

### `CEventCR`'s 0% -- resolved: it is a multi-table CONTAINER

`CEventCR` is not a single table, so `read()` mis-frames it and the bytes it
calls "records" are other CTable DESCRIPTORS -- 288, 12, 0x100000000 and so on.
Events were never being read at all; they are not "pointing somewhere else".

Its descriptors sit contiguously from offset 0 at +0x000 / +0x038 / +0x078 /
+0x0b0 (gaps 56, 64, 56 -- the 64 is a small table whose 8-byte body is inlined
after its descriptor), and that is unlike its apparent 24-byte record stride.

⚠ DISCRIMINATOR, because a naive "count the descriptors" scan lies. A record
whose bytes happen to hold 0x100000000 at +0x18 with a consistent size/count
triple looks exactly like a descriptor. The tell is the GAP: if the spacing
between "descriptors" equals the record STRIDE, they are records, not
descriptors. `CComponentLODCR` (gap 96 == stride 96) and `CListCR` (gap 440 ==
stride 440) are false positives and are genuinely single tables -- their 100%
nodeid rate already said so.

`CScriptCR` (bases 0x0/0x68), `CLegacyCameraDataCR` (0x0/0xb8) and
`CStaticInstanceModelCR` (0x0/0x38) each carry a SECOND descriptor whose gap is
not their stride, so `read()` returns only their FIRST table. That first table
validates at 100% / 100% / 93% on the nodeid test, so it is real and useful --
but it is not the whole file.

## The record shape

    +0x00  u64  component TYPE-NAME hash  ("billboard", "decal", ...)
    +0x08  u64  nodeid -- the actor carrying this component

`+0x08` is what makes these usable without decoding the payload: it binds
straight to what `evr_actor_data` already returns.

### `CTextureOverrideCR` -- decoded further (stride 32)

    +0x00  u64  the OVERRIDE TEXTURE   1988/2092 resolve to a real
                                       CGTextureResource (95%)
    +0x08  u64  nodeid
    +0x10  u64  slot/flags             0x00000001ffffffff on live rows
    +0x18  u64  0

A PER-ACTOR TEXTURE SWAP: how two instances of one model render differently
with no extra material. Nothing in the pipeline reads it.

Past `+0x00`/`+0x08` the payload is NOT decoded; raw records are returned
rather than inventing field names.
"""
from __future__ import annotations

import struct
from pathlib import Path

HDR = 56
SIZE_OFF = 0x08
COUNT_OFF = 0x28
COUNT_MIRROR_OFF = 0x30
TYPE_NAME_OFF = 0x00
NODEID_OFF = 0x08

TEXTURE_OVERRIDE = "4127ff2ffe6be26a"

#: Measured framing, kept as DOCUMENTATION and a cross-check -- `read()` does
#: not consult it. `component -> (type hex, stride, records, nodeid hit-rate)`.
#: 25 of these are single tables validated at 100%. Rows marked "first table
#: only" are multi-descriptor containers where `read()` returns table 0.
#: `CEventCR` is DELIBERATELY ABSENT: it is a container and has no single
#: framing (see above).
MEASURED = {
    "CTransformCR":             ("92abd3e1432bf5e8", 176, 36850, "100%"),
    "CFrustumCullCR":           ("ca5a03d5a497238c",  32, 11699, "100%"),
    "COcclusionCullCR":         ("142026f469321d54",  32, 11677, "100%"),
    "CStaticInstanceModelCR":   ("263584544abbd56c",  24,  9201,  "93%"),   # first table only
    "CScriptCR_note":           ("d99f6bbd8009c92c", 720,  4122, "100%"),   # first table only
    "CLegacyCameraDataCR_note": ("38f8036a376f5f64", 192,    24, "100%"),   # first table only
    "CLevelAABBCR":             ("b76203b6e5eaff80",  56,  9201, "100%"),
    "CBoundingSphereCR":        ("22f9fcb2d5e52e3c",  48,  6388, "100%"),
    "CTextureStreamingCR":      ("96832b652460ecde",  40,  4541, "100%"),
    "CScriptCR":                ("d99f6bbd8009c92c", 720,  4122, "100%"),
    "CR15ButtonInteractCR":     ("e9b24ea816dece48", 296,  2706, "100%"),
    "CCanvasUICR":              ("822fd4ccb42e8a3c",  88,  2570, "100%"),
    "CComponentLODCR":          ("7f49abae39aaf2aa",  96,  2340, "100%"),
    "CTextureOverrideCR":       (TEXTURE_OVERRIDE,    32,  2092, "100%"),
    "CSoundCR":                 ("04e7c2e6a7ebd80e",  80,  1874, "100%"),
    "CDecalCR":                 ("3b5db8af43546d40", 296,  1405, "100%"),
    "CPhysicsCR":               ("f6bab7207d923478",  48,   885, "100%"),
    "CR15NetDebugDrawCR":       ("079586e19869a090",  32,   670, "100%"),
    "CActorRegionLODCR":        ("409758926e4728c4",  32,   562, "100%"),
    "CStaticLODRegionTargetCR": ("02d2be13ea8fb8ae",  32,   547, "100%"),
    "CBillboardCR":             ("615e262159c487e0",  80,   277, "100%"),
    "CActorLODCR":              ("424fb75efee13ba6",  48,   249, "100%"),
    "CSharedCanvasUICR":        ("dab7dce1df894ef6",  72,   116, "100%"),
    "CR15NetDamageableCR":      ("bd1868f576836696",  56,   109, "100%"),
    "CR15SpawnPointCR":         ("cb75eee100d282a8",  48,    95, "100%"),
    "CListCR":                  ("0f0fb3116ec3f644", 440,    94, "100%"),
    "CAmbientSoundCR":          ("4047315f08901c70", 280,    65, "100%"),
    "CR15NetSensorTargetCR":    ("e51718c1e4669474",  32,    63, "100%"),
    "CR15NetPunchableCR":       ("c0c407db04436f3c",  48,    42, "100%"),
    "CMaterialTypeCR":          ("3b87ef2fc94a9b14",  32,    32, "100%"),
    "CStaticRaycastCR":         ("d649a90fff322c12",  32,    32, "100%"),
    "CLegacyCameraDataCR":      ("38f8036a376f5f64", 192,    24, "100%"),
}


def read(blob: bytes) -> dict | None:
    """Frame a component table from its own header. `None` if it is not one."""
    if len(blob) < HDR:
        return None
    size = struct.unpack_from("<I", blob, SIZE_OFF)[0]
    count = struct.unpack_from("<I", blob, COUNT_OFF)[0]
    mirror = struct.unpack_from("<I", blob, COUNT_MIRROR_OFF)[0]
    if not count or count != mirror or not size or size % count:
        return None
    stride = size // count
    if HDR + size > len(blob):
        return None
    recs = [blob[HDR + i * stride: HDR + (i + 1) * stride] for i in range(count)]
    return {
        "count": count, "stride": stride, "record_bytes": size,
        "heap": blob[HDR + size:],
        "type_names": sorted({struct.unpack_from("<Q", r, TYPE_NAME_OFF)[0] for r in recs}),
        "nodeids": [struct.unpack_from("<Q", r, NODEID_OFF)[0] for r in recs],
        "records": recs,
    }


def load(root, level_hash: str, type_hash: str) -> dict | None:
    from evr_resource_types import resource_path
    p = resource_path(Path(root), type_hash, level_hash)
    return read(p.read_bytes()) if p is not None else None


def actors_with(root, level_hash: str, type_hash: str) -> list[int]:
    """Nodeids of the actors carrying this component in this level."""
    doc = load(root, level_hash, type_hash)
    return doc["nodeids"] if doc else []


def texture_overrides(root, level_hash: str) -> list[dict]:
    """`[{nodeid, texture}]` -- the per-actor texture swaps for a level."""
    doc = load(root, level_hash, TEXTURE_OVERRIDE)
    if not doc:
        return []
    out = []
    for r in doc["records"]:
        tex, nid = struct.unpack_from("<QQ", r, 0)
        out.append({"nodeid": nid, "texture": "%016x" % tex})
    return out
