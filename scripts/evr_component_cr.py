"""Component-record readers: model bindings and BVH bounds.

## Why this is in-repo

These were the last three things the project imported from `evrFileTools`, a
28 MB vendored tree with **no LICENSE file** that credits third parties -- so it
could not be redistributed, and `.gitignore` correctly excluded it.  That made a
fresh clone non-functional: `evr_paths` resolved the import root and found
nothing there.

Reimplementing these drops that dependency entirely.  Everything the project now
vendors is MIT under the same copyright as the project itself.

Together with `evr_actor_data` (the actor table) and `evr_level_reader` (static
instances), the component-record surface is fully covered in-repo.

## `CInstanceModelCR` -- actor -> model

Scanned rather than walked, because the record base is not where a header walk
predicts.  A record is accepted only when four independent fields agree:

    +0x00  type hash
    +0x08  selector      -- must be a real actor nodeid
    +0x10  terminator    -- must be 0xFFFFFFFF
    +0x18  slot id       -- must be <= 0x20
    +0x20  model hash
    +0x28  sentinel      -- must be 0xFFFFFFFFFFFFFFFF

Four constraints on an 8-byte-aligned scan is a strong filter; a false positive
needs all four to coincide.

## `CStaticInstanceModelCR` -- two parallel groups

Positional, not scanned:

    count    = u32 @ 0x28
    group A  = 0x2A8,                 stride 24, entity  @ +0x08
    group B  = file_size - count*88,  stride 88, model   @ +0x20

Group B is located from the END of the file, which is why the tail is exact.

## `CBVHResource` -- root bounds only

    u32 buffer size @ +0x08, nodes from +64, stride 32,
    each node = 6 x f32 (min xyz, max xyz) + 8 bytes

⚠ Bounds only.  The tree itself is not decoded, and on a level whose BVH
includes skybox geometry the root bound is huge and not a useful proxy for the
playable space (`mpl_combat_fission` reports +/-5880 m).
"""

from __future__ import annotations

import math
import struct

#: Component types that bind a model to an actor in `CModelCR`.
#:
#: Transcribed from the reader this replaces.  The set is not exhaustive on its
#: own -- the `record_id == 0x1C and flags == 0x000FFFFF` fallback in
#: `evr_scene_extract._model_cr_bindings` catches the rest.  Measured on
#: `mpl_combat_fission`: of 726 records citing a real model on a real actor,
#: exactly ONE fails both tests.
CMODEL_COMPONENT_TYPES = frozenset({
    0x38EE951A26FB816A,      # ncaModel
    0x741299B67D142A8F,      # ncaModel (backpack variant)
    0x0FAD20BE1B6FD25A3,     # model companion
    0x329A11436EEA2156,      # orphan A
    0xBAA43BE4F38C7B63,      # orphan B
})

#: `CInstanceModelCR` record fields, and the constants they must hold.
_ICR_STRIDE = 8             # scan alignment, not record size
_ICR_SPAN = 0x48
_TERMINATOR = 0xFFFFFFFF
_SENTINEL = 0xFFFFFFFFFFFFFFFF
_MAX_SLOT = 0x20

#: `CStaticInstanceModelCR` group framing.
_SICR_COUNT_OFFSET = 0x28
_SICR_GROUP_A = 0x2A8
_SICR_A_STRIDE, _SICR_A_ENTITY = 24, 0x08
_SICR_B_STRIDE, _SICR_B_MODEL = 88, 0x20

#: `CBVHResource` framing.
_BVH_SIZE_OFFSET = 0x08
_BVH_NODES = 64
_BVH_STRIDE = 32


def _finite(value: float) -> float:
    return 0.0 if (math.isnan(value) or math.isinf(value)) else value


def parse_instance_model_cr(data: bytes, nodeid_set, type_name: str) -> dict:
    """`CInstanceModelCR` / `CStaticInstanceModelCR` -> `{nodeid: {model_hash}}`."""
    size = len(data)
    actors: dict = {}

    if str(type_name).startswith("CStaticInstanceModelCR"):
        if size >= _SICR_GROUP_A:
            count = struct.unpack_from("<I", data, _SICR_COUNT_OFFSET)[0]
            group_b = size - count * _SICR_B_STRIDE
            if (group_b >= 0
                    and _SICR_GROUP_A + count * _SICR_A_STRIDE <= size):
                for i in range(count):
                    entity = struct.unpack_from(
                        "<Q", data,
                        _SICR_GROUP_A + i * _SICR_A_STRIDE + _SICR_A_ENTITY)[0]
                    model = struct.unpack_from(
                        "<Q", data,
                        group_b + i * _SICR_B_STRIDE + _SICR_B_MODEL)[0]
                    if entity in nodeid_set:
                        actors[str(entity)] = {
                            "model_hash": f"0x{model:016X}",
                            "model_name": str(model),
                        }
    else:
        for offset in range(0, max(0, size - _ICR_SPAN + 1), _ICR_STRIDE):
            (_type_hash, selector, terminator,
             slot_id, model, sentinel) = struct.unpack_from("<6Q", data, offset)
            if terminator != _TERMINATOR or sentinel != _SENTINEL:
                continue
            if slot_id > _MAX_SLOT:
                continue
            if selector in nodeid_set:
                actors[str(selector)] = {
                    "model_hash": f"0x{model:016X}",
                    "model_name": str(model),
                }

    return {"count": len(actors), "file_size": size, "type_name": type_name,
            "is_cr": True, "matched_actors": len(actors), "actors": actors}


def parse_model_cr(data: bytes, nodeid_set, type_name: str) -> dict:
    """`CModelCR` -> one model per actor.

    ⚠ Kept only for callers that expect the old one-model-per-actor shape.
    `evr_scene_extract._model_cr_bindings` returns a LIST per actor and should
    be preferred: an actor binding several models keeps only the last here, and
    on `mpl_combat_fission` that silently loses 18 models outright.
    """
    size = len(data)
    actors: dict = {}
    for offset in range(0x20, max(0x20, size - 8), 8):
        model = struct.unpack_from("<Q", data, offset)[0]
        if model in (0, _SENTINEL):
            continue
        component_type = struct.unpack_from("<Q", data, offset - 0x20)[0]
        selector = struct.unpack_from("<Q", data, offset - 0x18)[0]
        record_id = struct.unpack_from("<Q", data, offset - 0x08)[0]
        valid = component_type in CMODEL_COMPONENT_TYPES
        if not valid and record_id == 0x1C:
            flags = struct.unpack_from("<Q", data, offset - 0x10)[0]
            valid = flags == 0x000FFFFF
        if valid and selector in nodeid_set:
            actors[str(selector)] = {"model_hash": f"0x{model:016X}",
                                     "model_name": str(model)}
    return {"count": len(actors), "file_size": size, "type_name": type_name,
            "is_cr": True, "matched_actors": len(actors), "actors": actors}


def parse_bvh_resource(data: bytes) -> dict | None:
    """Root spatial bounds of a `CBVHResource`, or None.

    ⚠ BOUNDS ONLY -- the tree is not decoded, and the root bound can include
    skybox geometry.
    """
    if len(data) < _BVH_NODES + 12:
        return None
    buffer_size = struct.unpack_from("<I", data, _BVH_SIZE_OFFSET)[0]
    if len(data) < _BVH_NODES + buffer_size:
        return None
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for i in range(buffer_size // _BVH_STRIDE):
        base = _BVH_NODES + i * _BVH_STRIDE
        if base + 24 > len(data):
            break
        values = struct.unpack_from("<6f", data, base)
        for axis in range(3):
            lo[axis] = min(lo[axis], _finite(values[axis]))
            hi[axis] = max(hi[axis], _finite(values[axis + 3]))
    if lo[0] == float("inf"):
        return None
    return {"min": lo, "max": hi}
