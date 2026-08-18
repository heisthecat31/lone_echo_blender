"""Echo VR baked lightmaps: resource -> per-page diffuse irradiance.

See `docs/EVR_LIGHTING.md` for how this was recovered. The short version:

* One `CGLightMapResourceWin10` per level is a table of `SLightMapTextureNames`
  rows (stride 0x28 == five CSymbol64), framed `12 + n*0x28 + 16` bytes.
* `CGStaticInstanceResource.assetdata.lightmapidx` selects the ROW; the row's
  five hashes are ambient radiance (BC6H, HDR), two AO maps (BC5) and two
  occlusion maps (BC4), identified by their DXGI format, not by position.
* `lobes = ambient.arraysize / occlusion.arraysize` is exactly 5 or 4 on every
  shipped level, selecting the SG5 or SH4 branch of `material_base_ps.hlsl`.

## The reduction this module performs

A faithful evaluation needs the shading normal, so it can only happen in a
shader. What a DCC tool can use is the irradiance for the UNPERTURBED normal,
which for SG5 is exact rather than approximate, because the lobes are stored in
TANGENT space: with `n = (0,0,1)`, `dot(lobe.mean, n)` collapses to the lobe's
own z component, which is a constant of the basis. So

    irradiance/Pi = SUM_i  z_i * (2 / kLambdaSG5) * kSG5Scale * slice[page*5+i]

is a fixed weighted sum of the five slices -- no per-texel geometry needed. The
weights fall out of `DiffuseTermSG` (which deliberately omits Pi, returning
irradiance/Pi for direct use as diffuse reflectance).

For SH4 the coefficients are baked in WORLD space, so the same collapse is not
available; the DC term alone is used, which is the standard ambient reduction.

The result multiplies base colour, exactly as the low-spec path does:

    radiance = lightmap.rgb * diffusealbedo
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evr_resource_types import (LIGHTMAP_RESOURCE, STATIC_RESOURCE_GPU,
                                TEXTURE_RESOURCE, normalise_hash,
                                resolve_type_dir)
import evr_texture_resource as evr_tex

#: `SLightMapTextureNames`: five CSymbol64 per row.
ROW_STRIDE = 0x28
ROW_SLOTS = 5
#: The table is framed by a 12-byte lead (count @+8) and a 16-byte trailer.
TABLE_LEAD = 12
TABLE_TRAIL = 16

#: `constants.hlsl`. The z components ARE the dot products against the
#: unperturbed tangent-space normal, which is what makes the reduction exact.
SG5_LOBE_Z = (0.1, 0.3, 0.5, 0.7, 0.9)
SG5_LAMBDA = 3.62780595
SG5_SCALE = 0.5

BC6H_FORMATS = {94, 95, 96}
BC4_FORMATS = {79, 80, 81}
BC5_FORMATS = {82, 83, 84}


def sg5_weights() -> list:
    """Per-slice weight of the SG5 -> irradiance/Pi collapse."""
    return [z * (2.0 / SG5_LAMBDA) * SG5_SCALE for z in SG5_LOBE_Z]


def table_rows(blob: bytes) -> list:
    """`[[hash, hash, hash, hash, hash], ...]` -- one entry per table row."""
    if len(blob) < TABLE_LEAD + TABLE_TRAIL:
        return []
    count = struct.unpack_from("<I", blob, 8)[0]
    if TABLE_LEAD + count * ROW_STRIDE + TABLE_TRAIL != len(blob):
        return []                      # not this layout; say so rather than guess
    rows = []
    for r in range(count):
        base = TABLE_LEAD + r * ROW_STRIDE
        rows.append([f"{struct.unpack_from('<Q', blob, base + k * 8)[0]:016x}"
                     for k in range(ROW_SLOTS)])
    return rows


#: EVR `CGMeshData`, as it appears in a level's `CGMeshListResourceWin10`.
#:
#: The EVR struct is NOT the Lone Echo one (`le_mesh.meshlist` uses stride 0x80
#: with the fields at 0x6C/0x70/0x74) and the file does not open with the
#: CTable header block that `evr_structural_decode` walks on prop models, so
#: neither existing reader finds it. These offsets are pinned by a run test:
#: reading `lightmapindex` here yields ONLY the level's own lightmap row or the
#: unlit sentinel -- never a third value -- across every level checked, the page
#: always falls inside that level's page count, `numlobes` is uniformly 4, and
#: the number of valid records equals the decoder's submesh count exactly
#: (77/20/116/115/45 on five levels).
MESHDATA_BASE = 0x28
MESHDATA_STRIDE = 0x98
MD_LIGHTMAPINDEX = 0x60
MD_LMSLICEINDEX = 0x64
MD_NUMLOBES = 0x68

#: `CGMeshData.lightmapindex` / `.lmsliceindex` "not lightmapped".
LIGHTMAP_NONE = 0xFFFFFFFF


#: Sanity ceiling for a lightmap row / page index.
MAX_LIGHTMAP_INDEX = 64


def _read_triples(primary: bytes, field: int, count: int) -> list | None:
    """The `(row, page, numlobes)` triples at absolute offset `field`, or None.

    Rejects the offset unless EVERY record is well-formed: `numlobes` is the
    shipped value 4, and the row is either one single non-sentinel value shared
    by the whole model (a model binds ONE lightmap) or the unlit sentinel.
    """
    triples = []
    rows = set()
    for i in range(count):
        base = field + i * MESHDATA_STRIDE
        if base + 12 > len(primary):
            return None
        row, page, lobes = struct.unpack_from("<3I", primary, base)
        if lobes != 4:
            return None
        if row != LIGHTMAP_NONE:
            if row >= MAX_LIGHTMAP_INDEX or page >= MAX_LIGHTMAP_INDEX:
                return None
            rows.add(row)
        triples.append((row, page))
    if len(rows) > 1:
        return None
    return triples


def mesh_lightmap_bindings(primary: bytes, count: int) -> list:
    """`[(lightmapindex, page), ...]` for a model's submeshes, in submesh order.

    `count` is the number of submeshes the geometry decode produced; the record
    array is parallel to it. Entries are `(None, None)` where the mesh is unlit,
    and the whole list is unlit if the array cannot be located confidently.

    The array's file offset is NOT fixed: level mesh-lists put the first
    record's `lightmapindex` at 0x88 while prop models put it at 0x3FC, so it
    is located by scanning for a run of `count` well-formed records rather than
    assumed. The stride (0x98) is constant.
    """
    unlit = [(None, None)] * count
    if count <= 0:
        return unlit
    limit = len(primary) - MESHDATA_STRIDE * count
    if limit < 0:
        return unlit

    for field in range(0, limit + 1, 4):
        triples = _read_triples(primary, field, count)
        if triples is None:
            continue
        return [(None, None) if row == LIGHTMAP_NONE or page == LIGHTMAP_NONE
                else (row, page) for row, page in triples]
    return unlit


#: `CGStaticInstanceResource` (`SGStaticInstancesData`) descriptor bases. The
#: spacing is NOT uniform -- 0x40 for the first four, 0x38 for the last two --
#: which is why a fixed-stride header walk finds only four of the six.
CGSI_HEADER = 0x178
CGSI_SECTIONS = (
    (0x000, "assetdata", "<QIIII", 24),          # {model, ssoffset, sscount,
    (0x040, "instancedata", "<QIHHII", 24),      #  lightmapidx, pad}
    (0x080, "meshdata", "<QII", 16),             # {entity, lmsliceidx, probeidx,
    (0x0C0, "shadersetoverrides", "<QQ", 16),    #  dirlightmask, visstridx, pad}
    (0x0F8, "brokennodes", "<Q", 8),
    (0x130, "brokenassets", "<QQ", 16),
)


def read_cgsi(blob: bytes) -> dict | None:
    """Decode `CGStaticInstanceResource` into its six tables, or None.

    Validates that every section's `size == count * stride` and that the six
    tile the file exactly from 0x178, so a file that is not this layout is
    rejected instead of yielding plausible rows.
    """
    if len(blob) < CGSI_HEADER:
        return None
    sizes, counts = [], []
    for base, _name, _fmt, stride in CGSI_SECTIONS:
        size = struct.unpack_from("<Q", blob, base + 0x08)[0]
        count = struct.unpack_from("<Q", blob, base + 0x28)[0]
        if (count != struct.unpack_from("<Q", blob, base + 0x30)[0]
                or size != count * stride):
            return None
        sizes.append(size)
        counts.append(count)
    if CGSI_HEADER + sum(sizes) != len(blob):
        return None

    out, cursor = {}, CGSI_HEADER
    for (_base, name, fmt, stride), size, count in zip(CGSI_SECTIONS, sizes, counts):
        out[name] = [struct.unpack_from(fmt, blob, cursor + stride * i)
                     for i in range(count)]
        cursor += size
    return out


def static_instance_lightmaps(cgsi: bytes) -> tuple:
    """`(row_by_model, page_by_entity)` from a `CGStaticInstanceResource`.

    Static-instanced geometry does NOT take its lightmap binding from
    `CGMeshData` -- `mpl_arena_a`'s prop models are all unlit there. It comes
    from CGSI instead: the lightmap ROW is per MODEL (`assetdata.lightmapidx`)
    and the PAGE is per INSTANCE (`instancedata.lmsliceidx`), keyed by entity.
    On the arena that is row 10 for 64 of 98 models and pages 0..4 on 556 of
    732 instances.
    """
    tables = read_cgsi(cgsi)
    if not tables:
        return {}, {}
    row_by_model = {f"{r[0]:016x}": r[3] for r in tables["assetdata"]
                    if r[3] != LIGHTMAP_NONE}
    page_by_entity = {r[0]: r[1] for r in tables["instancedata"]
                      if r[1] != LIGHTMAP_NONE}
    return row_by_model, page_by_entity


#: `CGStaticInstanceResourceWin10GPU` -- the LMUV scatter buffer.
#: Defined in `evr_resource_types` so `resolve_type_dir` can translate it on a
#: Win7 extract; re-exported here under its historical name.
CGSI_GPU = STATIC_RESOURCE_GPU
#: One lightmap UV: two u16 UNORM. `meshdata.uvcount` counts these, and the
#: header's `@0x170 == 4 * sum(uvcount) == GPU file size` pins the stride.
UV_STRIDE = 4


def instanced_mesh_bake_id(entity: int, submesh: int) -> int:
    """`MakeInstancedMeshBakeID(entity, "mesh-<i>")`.

    CSymbol64 is a CRC-64 that seeds at all-ones; this is the same walk SEEDED
    WITH THE ENTITY instead, over the string `mesh-<i>`. That reading resolves
    **100%** of `meshdata` keys on every level tried (1872/1872 arena,
    1262/1262 lobby, 2208/2208 lobby_b_combat), which is what identifies it.
    """
    from le_mesh.material_scalars import _SEEDS

    mask = (1 << 64) - 1
    result = entity & mask
    for byte in f"mesh-{submesh}".encode("ascii"):
        if 0x41 <= byte <= 0x5A:
            byte += 0x20
        result = ((result << 8) & mask) ^ _SEEDS[(result >> 56) & 0xFF] ^ byte
    return result & mask


def static_instance_uvs(cgsi: bytes, gpu: bytes, entity: int,
                        submesh: int) -> list | None:
    """Per-instance lightmap UVs for one instance's submesh, or None.

    Static-instanced geometry does not carry its lightmap UV in the vertex
    stream -- instances of one mesh sit in DIFFERENT atlas regions, so the UVs
    are per instance and live in the CGSI GPU sibling: `meshdata.uvoffset` /
    `.uvcount`, 4 bytes each. The header's `@0x170 == 4 * sum(uvcount)` chain
    holds exactly and equals the GPU file size, which is what validates the
    stride.
    """
    tables = read_cgsi(cgsi)
    if not tables:
        return None
    key = instanced_mesh_bake_id(entity, submesh)
    for row_key, offset, count in tables["meshdata"]:
        if row_key != key:
            continue
        # `uvoffset` counts UVs, NOT bytes. The highest offset in the arena is
        # 949,571 against a total of 949,601 UVs -- it indexes the item, and
        # the byte position is 4x that. Reading it as a byte offset lands 4x
        # too deep and hands each instance a chart that spans ~58% of the
        # atlas instead of ~1%, which renders as one stretched lightmap.
        base = offset * UV_STRIDE
        if base + count * UV_STRIDE > len(gpu):
            return None
        return [(struct.unpack_from("<H", gpu, base + i * UV_STRIDE)[0] / 65535.0,
                 struct.unpack_from("<H", gpu, base + i * UV_STRIDE + 2)[0] / 65535.0)
                for i in range(count)]
    return None


def _dds_info(blob: bytes) -> tuple:
    """`(width, height, dxgi, arraysize)` for a DX10 DDS."""
    height, width = struct.unpack_from("<II", blob, 12)
    dxgi = struct.unpack_from("<I", blob, 128)[0]
    arraysize = struct.unpack_from("<I", blob, 140)[0] or 1
    return width, height, dxgi, arraysize


def level_lightmap(root: Path, level_hash: str, row_index: int | None = None) -> dict | None:
    """Resolve a level's lightmap binding.

    Returns `{"ambient", "occlusion", "ao", "pages", "lobes", "basis",
    "width", "height"}` or None. `row_index` comes from
    `assetdata.lightmapidx`; when omitted the single populated row is used,
    which is what every shipped level has.
    """
    path = resolve_type_dir(root, LIGHTMAP_RESOURCE) / normalise_hash(level_hash)
    if not path.exists():
        path = path.with_suffix(".bin")
    if not path.exists():
        return None
    rows = table_rows(path.read_bytes())
    if not rows:
        return None

    tex_dir = resolve_type_dir(root, TEXTURE_RESOURCE)
    known = {p.name for p in tex_dir.iterdir()} if tex_dir.is_dir() else set()
    candidates = ([(row_index, rows[row_index])]
                  if row_index is not None and row_index < len(rows)
                  else [(i, r) for i, r in enumerate(rows)
                        if any(h in known for h in r)])
    for row_number, row in candidates:
        ambient = occlusion = None
        ao = []
        width = height = 0
        for h in row:
            if h not in known:
                continue
            blob, _note = evr_tex.rebuild_dds(root, h)
            if not blob or len(blob) < 148:
                continue
            w, ht, dxgi, arr = _dds_info(blob)
            if dxgi in BC6H_FORMATS:
                ambient, width, height = (h, arr), w, ht
            elif dxgi in BC4_FORMATS:
                occlusion = (h, arr)
            elif dxgi in BC5_FORMATS:
                ao.append((h, arr))
        if not ambient or not occlusion or not occlusion[1]:
            continue
        pages = occlusion[1]
        lobes = ambient[1] / pages
        if lobes not in (4.0, 5.0):
            continue          # neither shipped basis -- refuse rather than guess
        return {
            "ambient": ambient[0], "occlusion": occlusion[0],
            "ao": [h for h, _ in ao], "pages": pages, "lobes": int(lobes),
            "basis": "SG5" if lobes == 5.0 else "SH4",
            "width": width, "height": height,
            # The table ROW this level binds. A prop model's `lightmapindex`
            # must match it, or the prop belongs to a different level's atlas.
            "row": row_number,
        }
    return None


def decode_ambient(root: Path, tex_hash: str) -> tuple:
    """`(slices, width, height)` -- every array slice as a float RGB list."""
    import texture2ddecoder

    blob, note = evr_tex.rebuild_dds(root, tex_hash)
    if not blob:
        raise ValueError(f"{tex_hash}: {note}")
    width, height, dxgi, arraysize = _dds_info(blob)
    if dxgi not in BC6H_FORMATS:
        raise ValueError(f"{tex_hash}: dxgi {dxgi} is not BC6H")

    pixels = blob[148:]
    per_slice = max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * 16
    out = []
    for s in range(arraysize):
        chunk = pixels[s * per_slice:(s + 1) * per_slice]
        if len(chunk) < per_slice:
            break
        # decode_bc6 returns BGRA float32; unsigned variant for BC6H_UF16.
        raw = texture2ddecoder.decode_bc6(chunk, width, height)
        out.append(raw)
    return out, width, height


def page_irradiance(slices, page: int, basis: str, width: int, height: int):
    """Combine one page's slices into a linear RGB irradiance image.

    Returns a flat `[r, g, b, r, g, b, ...]` float list, row-major.
    """
    import numpy as np

    lobes = 5 if basis == "SG5" else 4
    weights = sg5_weights() if basis == "SG5" else [1.0, 0.0, 0.0, 0.0]
    acc = np.zeros((height, width, 3), dtype=np.float32)

    for i in range(lobes):
        index = page * lobes + i
        if index >= len(slices) or weights[i] == 0.0:
            continue
        # ⚠ `texture2ddecoder.decode_bc6` returns BGRA **8-bit**, not float: the
        # source is BC6H (HDR) and anything above 1.0 is clamped on the way out.
        # The lobe weights sum to 0.69, so a fully-lit texel lands mid-range and
        # the clamp mostly bites on bright emitters. Replacing this with a real
        # half-float BC6H decode is the way to recover the highlights.
        src = np.frombuffer(slices[index], dtype=np.uint8).reshape(height, width, 4)
        acc += src[:, :, [2, 1, 0]].astype(np.float32) * (weights[i] / 255.0)
    return acc
