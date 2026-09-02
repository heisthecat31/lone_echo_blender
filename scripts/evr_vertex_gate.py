"""The per-vertex gate an emissive LAYER names, read from the vertex data.

## Why this exists

`le_mesh.materials` records a layer's blend as `vertex_blend_attribute:
"color1"` with `vertex_blend_applied: False` -- the layer says which vertex
colour gates it, and nothing ever read that colour. Ungated, the layer
composited at `amount_constant` 1.0 as a LERP, i.e. it REPLACED the layer
beneath it.

⭐ `mpl_arena_a`'s catapult tunnel is the case that found it. Material
`492ec3cc59ce42ae` stacks a 128x128 hand decal (`caa826cc1a0e586d`) on layer 1
over the ring light on layer 0, and ungated the decal smeared across the rings.
Its `color1` reads `[0, 0, 0, 255]` on every one of that mesh's 865 vertices --
the gate is authored CLOSED, so the engine draws none of it.

⛔ Do NOT gate on the layer descriptor alone. The same shape -- full strength,
no mask, unapplied `color1` -- covers 202 upper layers across 48 materials in
18 packages, and it contains both outcomes:

    caa826cc1a0e586d  tube hand decal    gate CLOSED [0,0,0,255]  must not show
    76ee2e8cb2b336db  dyson lock art L1  gate OPEN [255,255,0]    MUST show
    fd5bd6bdcc84d5a1  dyson lock art L2  gate OPEN [255,255,0]    MUST show

Texture appearance does not separate them either: the war room fill (54.9% lit,
coverage 0.93) and dyson's L1 (45.3%, 1.00) sit on top of each other and need
opposite treatment. Only the gate itself decides.

Corpus: 108 open, 8 closed, 46 varying, of 162 read.

## The layout

Both `CGInstancedModelResourceWin10` and `CGMeshListResourceWin10` carry the
same `SVertexElement` table -- 8-byte records
`[usage][offset][format][components][usage_index][size][stream][pad]`. It does
NOT sit immediately after the `0c ff ff ff ff ff ff ff` terminator run, so it
is located by scanning for the longest valid run carrying a POSITION and a
TEXCOORD.

⚠ Only the stream BASE differs between the two shapes, and a mesh list's
submesh stride can be NARROWER than the table's: the table describes the full
layout, and a record without `color1` drops its leading 4 bytes and shifts
everything down. That same shift is why the stride-16 records in
`decode.uv_stream_offset` take their UV at +4 where the table says +8.
"""

from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

import numpy as np

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from evr_resource_types import find_mesh_and_primary   # noqa: E402

#: `[usage][offset][format][components][usage_index][size][stream][pad]`.
ELEMENT_REC = 8
USAGE_POSITION, USAGE_COLOUR, USAGE_TEXCOORD = 0, 1, 4
_USAGES = frozenset((0, 1, 2, 3, 4, 11))
_FORMATS = frozenset((1, 3, 5, 8))

#: array2 record fields, as `decode` reads them.
M_BASE, M_STREAM0_SIZE, M_VCOUNT = 0x128, 0x130, 0x13C
MESHLIST_A2_STRIDE = 0x150

ATTR_USAGE_INDEX = {"color0": 0, "color1": 1, "color2": 2, "color3": 3}

_DECODE = None


def _decode():
    """`app/extract/evr_mesh_importer/decode.py`, loaded off disk.

    Its package `__init__` imports `bpy`, which does not exist outside Blender,
    so the module is loaded directly rather than imported.
    """
    global _DECODE
    if _DECODE is None:
        spec = importlib.util.spec_from_file_location(
            "_evr_decode_for_gate",
            str(_SCRIPTS.parent / "app" / "extract" / "evr_mesh_importer" /
                "decode.py"))
        mod = importlib.util.module_from_spec(spec)
        sys.modules["_evr_decode_for_gate"] = mod
        spec.loader.exec_module(mod)
        _DECODE = mod
    return _DECODE


def _valid(rec) -> bool:
    usage, off, fmt, comps, uidx, size, stream, pad = rec
    return (usage in _USAGES and fmt in _FORMATS and 1 <= comps <= 4
            and stream <= 3 and pad == 0 and off <= 64
            and 1 <= size <= 16 and uidx <= 7)


def elements(meta: bytes) -> list:
    """The `SVertexElement` table as `[(usage, off, fmt, comps, uidx, size,
    stream), ...]`, or `[]`."""
    best: list = []
    at, n = 0, len(meta)
    while at + ELEMENT_REC <= n:
        if not _valid(meta[at:at + ELEMENT_REC]):
            at += 4
            continue
        run, k = [], at
        while k + ELEMENT_REC <= n and _valid(meta[k:k + ELEMENT_REC]):
            usage, off, fmt, comps, uidx, size, stream, _p = meta[k:k + ELEMENT_REC]
            run.append((usage, off, fmt, comps, uidx, size, stream))
            k += ELEMENT_REC
        kinds = {e[0] for e in run}
        if (len(run) > len(best) and USAGE_POSITION in kinds
                and USAGE_TEXCOORD in kinds):
            best = run
        at = k if k > at else at + 4
    return best


def _stream0(meta, gpu, positions, nverts, table_stride, arrays):
    """`(base, stride)` for stream 0, for either resource shape."""
    if arrays and len(arrays) > 2 and arrays[2][2] == MESHLIST_A2_STRIDE:
        a2b, a2c, a2s = arrays[2]
        for i in range(a2c):
            rec = a2b + i * a2s
            base = struct.unpack_from("<I", meta, rec + M_BASE)[0]
            size = struct.unpack_from("<I", meta, rec + M_STREAM0_SIZE)[0]
            count = struct.unpack_from("<I", meta, rec + M_VCOUNT)[0]
            if count != nverts or not size:
                continue
            got = np.frombuffer(gpu[base + size:base + size + 12],
                                dtype=np.float32)
            if len(got) == 3 and np.allclose(got, positions[0], atol=1e-4):
                return base, size // count
        return None, None
    return 0, table_stride


def read_gate(root, name_hash, nverts, positions, uv0=None,
              attribute="color1"):
    """`(verdict, rgba_or_reason)` for one mesh's gate.

    Verdict is `"open"`, `"closed"`, `"varies"` or `"absent"`, with the RGBA
    when it is constant; or `None` and a reason when the stream could not be
    located or the check failed. Never guesses -- an unreadable stream reports
    itself rather than returning a value that would suppress a layer wrongly.
    """
    uidx = ATTR_USAGE_INDEX.get(str(attribute).lower())
    if uidx is None:
        return None, "unknown attribute %r" % (attribute,)
    gpu_path, primary_path = find_mesh_and_primary(Path(root), str(name_hash))
    if gpu_path is None or primary_path is None:
        return None, "no resource"
    try:
        meta = primary_path.read_bytes()
        gpu = gpu_path.read_bytes()
    except OSError:
        return None, "unreadable"

    els = elements(meta)
    if not els:
        return None, "no element table"
    want = [e for e in els if e[0] == USAGE_COLOUR and e[4] == uidx]
    if not want:
        return None, "no %s element" % (attribute,)
    stream = want[0][6]
    table_stride = max(o + s for u, o, f, c, ui, s, st in els if st == stream)

    arrays, _end = _decode()._meshlist_arrays(meta)
    positions = np.asarray(positions, dtype=np.float32).reshape(-1, 3)
    base, stride = _stream0(meta, gpu, positions, nverts, table_stride, arrays)
    if base is None or not stride or base + nverts * stride > len(gpu):
        return None, "stream not located"

    # ⚠ A record NARROWER than the table has dropped its leading colour, so the
    # attribute this layer names is not authored on this mesh at all.
    shift = table_stride - stride
    off = want[0][1]
    if shift > 0:
        if off < shift:
            return "absent", None
        off -= shift
    if off + 4 > stride:
        return "absent", None

    raw = np.frombuffer(gpu[base:base + nverts * stride],
                        dtype=np.uint8).reshape(nverts, stride)
    if uv0 is not None and len(uv0):
        uvel = [e for e in els
                if e[0] == USAGE_TEXCOORD and e[4] == 0 and e[6] == stream]
        if uvel:
            uv_off = uvel[0][1] - max(shift, 0)
            if 0 <= uv_off and uv_off + 8 <= stride:
                got = raw[:, uv_off:uv_off + 8].copy().view(np.float32)
                if not np.allclose(got[:, 0],
                                   np.asarray(uv0).reshape(-1, 2)[:, 0],
                                   atol=1e-4):
                    return None, "stream check failed"

    col = raw[:, off:off + 4]
    uniq = np.unique(col, axis=0)
    if len(uniq) > 1:
        return "varies", None
    rgba = [int(v) for v in uniq[0]]
    return ("closed" if rgba[0] == 0 else "open"), rgba
