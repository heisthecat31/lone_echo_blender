"""Quest baked lighting: the atlas, and the UVs that address it.

## Why the arena hull has no material

It is not missing one. A `CGMeshListResourceAndroid` level hull carries render
params but no material palette, and an exhaustive scan of all 22,741 files in
the shipped tree finds NO run of 13 material hashes anywhere -- 13 being what
the arena hull's `CGRenderParams +0x28` indices (0..12) would need. The hull is
lit by its BAKED LIGHTMAP, which is where its colour comes from; there is no
per-mesh material to find.

## The atlas

The level's `CGLightMapResourceAndroid` cites exactly one texture, and that
texture is an ordinary `CGTextureResourceAndroid`. The arena's is
`B10G11R11_UFLOAT` (HDR), 1024x220, fully resident with 901,120 bytes of texel
data.

⚠ Do NOT look for it in `CGLightMapResourceAndroidGPU`. All 520 of those files
in the shipped tree are ZERO bytes; the atlas deliberately lives in the texture
namespace, which is why searching the lightmap GPU sibling finds nothing and
reports a level as unlit.

## The UVs

**Static-instanced geometry** carries its lightmap UV per INSTANCE, in the CGSI
GPU sibling, because instances of one mesh sit in different regions of the
atlas. The PC layout holds unchanged on Quest and is pinned three ways:
`4 * sum(meshdata.uvcount)` equals the header word at +0x170 AND the GPU file
size exactly (3744 on the arena), and the row key
`MakeInstancedMeshBakeID(entity, "mesh-<i>")` resolves 16 of 16 rows with none
unaddressed. The resulting charts are small, non-overlapping and packed along
one atlas row, and each chart's length equals its submesh's vertex count
exactly (44<->44, 10<->10, 148<->148, 32<->32).

⛔ **Hull / mesh-list geometry: the source is NOT known.** Vertex texcoord
usage 4 / usage_index 4 was believed to hold it, read as unsigned `u16 / 65535`
because "every value lands inside 0..1". That reasoning is CIRCULAR -- any u16
divided by 65535 lands inside 0..1 by construction, so the check proves
nothing.

Tested against ground truth it is simply wrong. For the 16 submeshes whose
lightmap UVs the CGSI states outright, set 4 disagrees on **16 of 16**, with a
maximum per-vertex difference of 0.66 to 0.98. It is also IDENTICAL across
every instance of a model (sub 0 of one model reads u[0.0052,0.9619] on all
five of its placements) while the true charts differ per instance -- so it is a
per-model attribute, not a per-instance lightmap chart.

`mesh_uvs` is kept because it correctly reads that slot, but it must not be
treated as a lightmap UV until something validates it. Multiplying albedo by it
shreds a texture that renders perfectly on its own, which is how this was
caught.

## Re-tested, and it still fails

Set 4 is seductive: across the arena hull its 29 charts pack into BANDS that
fill U and V exactly 0..1, with small meshes sitting side by side in one V slab
-- textbook atlas packing. Three further tests were run against it anyway,
because 84% of the atlas is lit while the CGSI charts address only 0.12% of it,
so something clearly does own the rest.

1. **Colour.** Sampling the atlas at set 4 ANTI-correlates with the level's
   own vertex bake, r = -0.34, where the real chart must agree.
2. **Orientation.** All eight swap/flip conventions were tried; the best
   scores +0.05, i.e. nothing.
3. **Texel density.** `area3D / areaUV` spread is 1.47 against the plain
   texture UV's 0.58 -- set 4 is LESS uniform than a texture UV, inverting the
   single property a lightmap parameterization exists to provide.

⚠ A channel-order swap was considered as the explanation for (1) and REJECTED:
matching the atlas against the level's two authored light colours, the
as-decoded order puts 6.5% / 5.7% of texels near the warm/cool hues while a
swap puts 0.00% near the cool one. The `B10G11R11` unpack is correct.

So the hull's lightmap chart is still unknown, and the hull's baked lighting is
read from the VERTEX COLOURS instead -- see `evr_quest.scene.baked_vertex_light`,
which is validated rather than assumed.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from evr_quest import mesh as qmesh                       # noqa: E402
from evr_quest import texture as qtexture                 # noqa: E402
from evr_quest import types as qtypes                     # noqa: E402

#: `SVertexElement.usage` for a texture coordinate.
USAGE_TEXCOORD = 4
#: The texcoord slot the lightmap UV lives in.
LIGHTMAP_UV_INDEX = 4
#: Lightmap UVs are u16 normalised by this. Unsigned -- see the module docstring.
UV_SCALE = 65535.0

#: `CGLightMapResourceAndroid` / its (always empty) GPU sibling.
LIGHTMAP = "CGLightMapResourceAndroid"


def atlas(root, level) -> str | None:
    """The lightmap atlas TEXTURE hash for a level, or None.

    The lightmap primary is a short record whose only texture citation is the
    atlas, so it is read as "the one hash in here that names a texture" rather
    than from a fixed offset.
    """
    path = qtypes.resource(root, LIGHTMAP, level)
    if path is None:
        return None
    try:
        blob = path.read_bytes()
    except OSError:
        return None
    directory = qtypes.type_dir(root, qtypes.TEXTURE)
    if directory is None:
        return None
    known = set()
    for entry in directory.iterdir():
        name = entry.name.lower()
        known.add(name)
        known.add(name.lstrip("0") or "0")
    for off in range(0, len(blob) - 8, 4):
        value = struct.unpack_from("<Q", blob, off)[0]
        if not value:
            continue
        name = f"{value:016x}"
        if name in known or (name.lstrip("0") or "0") in known:
            return name
    return None


def export_atlas(root, out_dir, level) -> dict:
    """Decode the level's atlas to a Radiance `.hdr`. Returns a manifest dict.

    HDR, not PNG. The atlas is a packed-float surface whose median luma is
    ~0.056 against a peak near 3.7, so writing it through the 8-bit path
    quantises the whole image into about three of 255 levels -- see
    `texture.decode_hdr`.
    """
    name = atlas(root, level)
    if name is None:
        return {}
    primary = qtypes.resource(root, qtypes.TEXTURE, name)
    if primary is None:
        return {"atlas": name, "file": "", "reason": "texture resource absent"}
    info = qtexture.read(primary)
    if info is None:
        return {"atlas": name, "file": "", "reason": "header unreadable"}
    gpu = qtypes.resource(root, qtypes.TEXTURE_GPU, name)

    out_dir = Path(out_dir)
    result = {"atlas": name, "format": info.format_name, "uv": "uv1",
              "width": info.width, "height": info.height}
    hdr = qtexture.decode_hdr(primary, gpu, info)
    if hdr is not None:
        rgb, width, height = hdr
        rel = f"textures/{name}.hdr"
        if qtexture.write_hdr(out_dir / rel, rgb, width, height):
            result["file"] = rel
            result["hdr"] = True
            result["peak"] = round(float(rgb.max()), 4)
            return result

    # Not a packed-float atlas (or the HDR write failed): fall back to the
    # ordinary 8-bit path rather than emitting nothing.
    decoded = qtexture.decode(primary, gpu, info)
    if not decoded:
        return {"atlas": name, "file": "", "reason": "decode failed"}
    rgba, width, height = decoded
    rel = f"textures/{name}.png"
    if not qtexture.write_png(out_dir / rel, rgba, width, height):
        return {"atlas": name, "file": "", "reason": "write failed"}
    result["file"] = rel
    result["hdr"] = False
    return result


def mesh_uvs(gpu: bytes, mesh) -> list:
    """`[(u, v), ...]` from vertex texcoord slot 4, or `[]`.

    ⛔ NOT the lightmap UV -- see the module docstring. Ground truth disagrees
    with it on 16 of 16 submeshes. Kept for inspection only; nothing should
    feed this to a lightmap.
    """
    element = next((e for e in mesh.elements
                    if e.usage == USAGE_TEXCOORD
                    and e.usage_index == LIGHTMAP_UV_INDEX), None)
    if element is None:
        return []
    block, stride = qmesh.stream_block(gpu, mesh, element.stream)
    if block is None:
        return []
    out = []
    for i in range(mesh.count):
        at = i * stride + element.offset
        if at + 4 > len(block):
            return []
        u, v = struct.unpack_from("<HH", block, at)
        out.append((u / UV_SCALE, v / UV_SCALE))
    return out


def looks_like_uv(uvs, tolerance: float = 0.001) -> bool:
    """Do these values sit inside 0..1?

    ⚠ This is NOT evidence of anything for u16 data: `u16 / 65535` is inside
    0..1 by construction, so the test passes for a packed direction, a weight,
    or noise. It was used to justify treating slot 4 as a lightmap UV and that
    conclusion was wrong. Retained only as a cheap range guard.
    """
    if not uvs:
        return False
    lo = -tolerance
    hi = 1.0 + tolerance
    return all(lo <= u <= hi and lo <= v <= hi for u, v in uvs)
