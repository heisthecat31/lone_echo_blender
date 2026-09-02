"""The two per-vertex RGBA8 colours in an Echo VR model's stream 0.

⛔ These are NOT the tint mask. That is what they were extracted for, and the
render settles it: shading the whole chassis by this attribute leaves the entire
body BLACK, lighting only the helmet (red) and the hands (blue). It is a
body-part id, not a body/accent split -- so driving the tint from it colours the
helmet and hands and nothing else, which is exactly the wrong-parts symptom.

Layout (`evr_mesh_importer/patch_colors.py` is the authority): a vertex begins
with colour 0 at +0 and colour 1 at +4, then the UV pair at +8.

⚠ A vertex therefore starts 8 bytes BEFORE its UV pair. Probing UV offset 0
first "succeeds" -- the UVs still line up at stride intervals -- but reports a
base one whole colour late, so every colour read comes from the next vertex.
`_UV_OFFSETS` is pinned to (8,) for that reason.

Measured on the samurai chassis (`c2e85be6ffce4563`, stream-0 stride 44):

    colour 0                (0,0,0,255) on EVERY vertex of every mesh
    mesh 1 (body, 13047v)   (0,0,0,255) x11275   (255,0,0,255) x1772
                            -- and the 1772 are x +/-0.14, y 1.53..1.94: the HELMET
    mesh 4 (visor, 80v)     (255,0,0,255) throughout
    mesh 3 (head, 2110v)    (0,255,0,255) throughout
    mesh 2 (18978v)         (0,0,255,255) x9499  (0,0,127,255) x9479
    mesh 0 (490v)           (0,0,255,255) x245   (0,0,127,255) x245

Meshes 0 and 2 split ~50/50 across an IDENTICAL bounding box, so those two
values interleave within one shell rather than marking regions.

⚠ The decoder that produces the package (`decode.extract_mesh`) reads positions,
UVs and bone weights out of these streams but never the colours, and it does not
report where stream 0 begins. Rather than fork it -- the standalone viewer shares
that module -- stream 0 is re-located here from the UVs the package already
has, which is exact: a stride and offset that reproduce hundreds of UV pairs
verbatim cannot be the wrong buffer.
"""

from __future__ import annotations

import struct
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
import sys
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from evr_resource_types import MESH_DIRS, resource_path

#: Strides seen in the corpus; stream 0 is a fixed-size interleaved vertex.
_STRIDES = tuple(range(12, 81, 4))
#: UV pair position within the vertex. The decoder reads UVs at +8 for every
#: stride >= 16, and `evr_mesh_importer/patch_colors.py` reads the two vertex
#: COLOURS at +0 and +4 -- so a vertex begins 8 bytes BEFORE its UV pair.
#: Probing +0 first finds the UV but reports a base 8 bytes late, which shifts
#: every colour read by one whole vertex.
_UV_OFFSETS = (8,)
#: UV pairs that must match before a (stride, offset) is accepted.
_CONFIRM = 150


def gpu_blob(root: Path, model_hash: str) -> bytes | None:
    for directory in MESH_DIRS:
        path = resource_path(Path(root), directory, model_hash)
        if path and path.exists():
            return path.read_bytes()
    return None


def locate_stream0(gpu: bytes, uvs, count: int):
    """`(offset, stride, uv_offset)` for the stream 0 holding `uvs`, or None."""
    if not gpu or not uvs or count <= 0:
        return None
    needle = struct.pack("<ff", float(uvs[0][0]), float(uvs[0][1]))
    confirm = min(count, _CONFIRM)
    start = 0
    while True:
        hit = gpu.find(needle, start)
        if hit < 0 or hit % 4:
            if hit < 0:
                return None
            start = hit + 1
            continue
        for uv_offset in _UV_OFFSETS:
            base = hit - uv_offset
            if base < 0:
                continue
            for stride in _STRIDES:
                if base + count * stride > len(gpu):
                    continue
                ok = True
                for k in range(confirm):
                    at = base + k * stride + uv_offset
                    if struct.unpack_from("<ff", gpu, at) != (
                            float(uvs[k][0]), float(uvs[k][1])):
                        ok = False
                        break
                if ok:
                    return (base, stride, uv_offset)
        start = hit + 1


def zone_colors(gpu: bytes, base: int, stride: int, count: int,
                which: int = 1) -> list:
    """Vertex colour 0 (+0) or 1 (+4) per vertex, as floats in 0..1.

    Colour 0 measures constant (0,0,0,255) on every mesh of the samurai chassis,
    so colour 1 is the only per-vertex signal.
    """
    out = []
    for j in range(count):
        at = base + j * stride + (0 if which == 0 else 4)
        if at + 4 > len(gpu):
            out.append((0.0, 0.0, 0.0, 1.0))
            continue
        r, g, b, a = gpu[at:at + 4]
        out.append((r / 255.0, g / 255.0, b / 255.0, a / 255.0))
    return out


def for_meshes(root: Path, model_hash: str, uv_per_mesh) -> list:
    """`[colors_or_None, ...]`, one entry per mesh, in the given order."""
    gpu = gpu_blob(root, model_hash)
    if not gpu:
        return [None] * len(uv_per_mesh)
    out = []
    for uvs in uv_per_mesh:
        if not uvs:
            out.append(None)
            continue
        found = locate_stream0(gpu, uvs, len(uvs))
        if found is None:
            out.append(None)
            continue
        base, stride, _uv_offset = found
        out.append(zone_colors(gpu, base, stride, len(uvs)))
    return out


def distinct(colors) -> dict:
    """`{rgba: count}` -- for reporting how a mesh actually splits."""
    tally = {}
    for c in colors or ():
        key = tuple(round(v, 3) for v in c)
        tally[key] = tally.get(key, 0) + 1
    return tally
