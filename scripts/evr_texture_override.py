"""`CTextureOverrideCR` -- per-actor texture swaps the engine applies at runtime.

## What it is

A level can replace a texture on a specific actor without authoring a second
material.  `mpl_arena_a` carries 64 such records.  They are what puts the live
score on a scoreboard panel and the clock on a timer: the material ships a
placeholder, and this component names what actually goes there.

That is the answer to "why is this panel wearing a UV test grid" -- it is not a
decode failure, the shipped material genuinely binds a developer checker and the
real surface is supplied here.

## Layout (verified, exact)

The usual CR component framing, no surprises:

    header   +0x08  u32  table byte size   (2048 on `mpl_arena_a`)
             +0x28  u32  record count      (64)
    record   base `len(file) - size` = 0x38, stride 32
             +0x00  u64  the OVERRIDE texture (CGTextureResource)
             +0x08  u64  the actor it applies to
             +0x10  u32  0xFFFFFFFF on every record seen
             +0x14  u32  1, except 0 on a single record
             +0x18  u64  0

`count * stride == size` exactly, and every `+0x08` is a real actor nodeid.

## What the records actually say

Of the arena's 64 records:

* **35 are IDENTITY** -- the override names the texture the material already
  binds.  Applying them changes nothing; they are the rest state written down.
* **18 name a texture that is NOT IN THE EXTRACT.**  Those are runtime render
  targets -- the live scoreboard, the match clock -- generated per frame and
  never shipped as an asset.  Nothing can be applied for them; the useful thing
  is to SAY SO, so a consumer can label the object instead of leaving it wearing
  a placeholder with no explanation.
* **11 name a shipped texture that differs from the material's.**  Those are the
  only ones with anything to apply.

## ⛔ Why the slot is not guessed

A record says WHICH texture, never which slot.  For a material that binds a
single texture that is unambiguous.  For anything else it is not, and the
obvious tie-breakers are wrong:

    model 7f8ceaa023e3462b, override 27425a1f1d6aadb0 (256x256 BC1_UNORM,
    a score readout reading "00  0" with the team icons)

    layer0_albedo_map              16x16   BC1_UNORM_SRGB
    layer0_specular_map            16x16   BC7_UNORM
    layer0_composite_components    16x16   BC1_UNORM   <- exact format match

Matching on DXGI format picks `composite_components`, i.e. it would route a
scoreboard image into the roughness/metalness socket.  Matching on "replace
whichever texture is a stub" is no better -- three of the four are 16x16 stubs.

So an override is applied ONLY when the target material binds exactly one
texture.  Otherwise it is recorded on the spec and left unapplied, with the
reason attached.  A wrong socket is worse than an untouched one.
"""

from __future__ import annotations

import struct
from pathlib import Path

#: `CTextureOverrideCRWin10`.
TEXTURE_OVERRIDE_CR = "4127ff2ffe6be26a"

#: `CModelCRWin10` / `CInstanceModelCRWin10` -- actor -> model, needed to know
#: WHICH material an override lands on.
MODEL_CR = "ea51a0d76eb90142"
INSTANCE_MODEL_CR = "2464c4ed290f3268"

#: Offset of the model CSymbol64 inside a `CModelCR`/`CInstanceModelCR` record.
MODEL_HASH_OFFSET = 24

R_TEXTURE = 0x00
R_ACTOR = 0x08


def _normalise(value) -> str:
    return "%016x" % (int(value) & 0xFFFFFFFFFFFFFFFF)


def _read(root: Path, type_hash: str, member: str) -> bytes:
    path = Path(root) / type_hash / member
    if not path.exists():
        path = path.with_suffix(".bin")
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _payload_span(blob: bytes, known_actors, stride: int):
    """`(base, count)` of the record run, anchored on real actor nodeids.

    These files hold a small index table first and the payload after a gap, and
    neither offset is in the header.  Anchoring on ids that are real actors is
    what keeps a wrong stride from yielding plausible garbage.
    """
    hits = [off for off in range(0, len(blob) - 8, 4)
            if struct.unpack_from("<Q", blob, off)[0] in known_actors]
    if len(hits) < 2:
        return None, 0
    jump = max(range(len(hits) - 1), key=lambda i: hits[i + 1] - hits[i])
    if hits[jump + 1] - hits[jump] < 1000:
        return None, 0
    base = hits[jump + 1]
    return base, min(len(hits) - jump - 1, (len(blob) - base) // stride)


def actor_models(root: Path, members, known_actors) -> dict:
    """`{actor_nodeid: model_hash}` across both model components."""
    out: dict = {}
    for type_hash, stride in ((MODEL_CR, 296), (INSTANCE_MODEL_CR, 72)):
        for member in members:
            blob = _read(root, type_hash, member)
            if not blob:
                continue
            base, count = _payload_span(blob, known_actors, stride)
            if base is None:
                continue
            for i in range(count):
                rec = base + i * stride
                actor = struct.unpack_from("<Q", blob, rec)[0]
                if actor in known_actors:
                    out[actor] = _normalise(
                        struct.unpack_from("<Q", blob, rec + MODEL_HASH_OFFSET)[0])
    return out


def read_overrides(root: Path, members, known_actors) -> dict:
    """`{model_hash: {"texture", "actors", "records", "conflicting"}}`.

    Keyed by MODEL because that is what a material hangs off; `conflicting` is
    set when one model receives more than one distinct override, which happens
    for a multi-state UI surface and means no single texture is "the" answer.
    """
    models = actor_models(root, members, known_actors)
    per: dict = {}
    for member in members:
        blob = _read(root, TEXTURE_OVERRIDE_CR, member)
        if len(blob) < 0x38:
            continue
        size = struct.unpack_from("<I", blob, 0x08)[0]
        count = struct.unpack_from("<I", blob, 0x28)[0]
        if not count or not size or size % count:
            continue
        stride = size // count
        base = len(blob) - size
        if base < 0:
            continue
        for i in range(count):
            rec = base + i * stride
            actor = struct.unpack_from("<Q", blob, rec + R_ACTOR)[0]
            model = models.get(actor)
            if model is None:
                continue
            texture = _normalise(struct.unpack_from("<Q", blob, rec + R_TEXTURE)[0])
            if texture in ("0000000000000000", "ffffffffffffffff"):
                continue
            entry = per.setdefault(model, {"texture": texture, "actors": set(),
                                           "records": 0, "conflicting": False,
                                           "level": member})
            if entry["texture"] != texture:
                entry["conflicting"] = True
            entry["actors"].add(actor)
            entry["records"] += 1
    for entry in per.values():
        entry["actors"] = len(entry["actors"])
    return per


def resolve(entry: dict, role_textures: dict, texture_exists) -> dict:
    """Decide what to do with one model's override.

    `texture_exists(hash) -> bool` says whether the override is a shipped asset;
    a miss means a runtime render target.  Returns a record for the spec with an
    explicit `action`, never a silent guess -- see the module docstring for why
    the slot is not inferred when the material binds more than one texture.
    """
    texture = entry["texture"]
    shipped = bool(texture_exists(texture))
    current = dict(role_textures or {})
    out = {"texture": texture, "shipped": shipped,
           "actors": entry.get("actors", 0), "records": entry.get("records", 0)}

    if not shipped:
        out["action"] = "runtime"
        out["note"] = ("names a texture that is not in the extract -- a runtime "
                       "render target (live score, clock). The material's own "
                       "texture is a placeholder and the real surface is drawn "
                       "per frame, so nothing can be applied.")
        return out
    if entry.get("conflicting"):
        out["action"] = "ambiguous"
        out["note"] = "this model receives more than one override; no single texture applies"
        return out
    if texture in set(current.values()):
        out["action"] = "identity"
        out["note"] = "the material already binds this texture; nothing to change"
        return out
    if len(current) == 1:
        out["action"] = "applied"
        out["role"] = next(iter(current))
        out["replaced"] = current[out["role"]]
        return out
    out["action"] = "ambiguous"
    out["note"] = ("the material binds %d textures and the record names no slot; "
                   "matching on DXGI format or on 'replace the stub' both pick "
                   "the wrong socket here, so it is left unapplied"
                   % len(current))
    return out
