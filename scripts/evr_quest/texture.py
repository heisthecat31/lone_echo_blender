"""Quest textures: `CGTextureResourceAndroid` -> PNG.

## The descriptor

248 bytes on `android_vulkan` (PC carries two more words), no DDS wrapper -- the
payload is raw compressed blocks. Field map from `quest_combat_port`'s
`resource_io/cgtextureresource.py`, which names them from RAD's own
`STextureStreamData::Serialize`:

    +0x40  u64[8]   RawTexturePackfile citation slots (the streamed-out top mips)
    +0x80  u32[16]  `reversedmipsizes`, ASCENDING (index 0 is the 1x1 level),
                    padded with 0xFFFFFFFF
    +0xC0  u32      topology: 1 = resident, 0 = payload inline in Primary
    +0xC4  u32[3]   full width, full height, full mip count
    +0xD0  u32      layers
    +0xD8  u32      `NRadEngine::ETextureFormat`   (NOT DXGI)
    +0xE8  u32[4]   resident width, height, mips, datasize

⭐ Two laws hold across the whole shipped tree and are re-checked on every read:

  * `width == full_width >> (full_mips - mips)` and the same for height --
    **6464 of 6464**;
  * the payload is at least the chain it declares -- for the 4637 inline
    textures `sum(level_table[:mips]) * layers`, for the 1827 resident ones the
    GPU sibling's size equals `datasize` exactly.

⚠ THE PAYLOAD RUNS LARGEST MIP FIRST while `reversedmipsizes` runs smallest
first. Reading the table in payload order gives a 1x1 image for every texture.

## Formats

Only five appear: ASTC 4x4 and 8x8 (UNORM and SRGB) and one uncompressed HDR
packed format. There is no BCn on Quest, so `texture2ddecoder.decode_bc*` -- the
whole PC texture path -- is inapplicable; `decode_astc` is what this uses.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from evr_quest import types as qtypes

HEADER_SIZE = 248

OFF_CITATIONS, CITATION_SLOTS = 0x40, 8
OFF_LEVEL_TABLE, LEVEL_SLOTS = 0x80, 16
LEVEL_SENTINEL = 0xFFFFFFFF
OFF_RESIDENT = 0xC0
OFF_FULL_WIDTH, OFF_FULL_HEIGHT, OFF_FULL_MIPS = 0xC4, 0xC8, 0xCC
OFF_LAYERS = 0xD0
OFF_FORMAT = 0xD8
OFF_WIDTH, OFF_HEIGHT, OFF_MIPS, OFF_DATASIZE = 0xE8, 0xEC, 0xF0, 0xF4

#: `ETextureFormat` -> (block width, block height, bytes per block, is sRGB).
#: Transcribed from `quest_combat_port/data/etextureformat_table.tsv`, which
#: marks every row `disasm-confirmed`. Only the five the corpus uses are here;
#: an unlisted format is REFUSED rather than guessed at.
FORMATS = {
    73: ("ASTC_4x4_UNORM", 4, 4, 16, False),
    74: ("ASTC_4x4_SRGB", 4, 4, 16, True),
    87: ("ASTC_8x8_UNORM", 8, 8, 16, False),
    88: ("ASTC_8x8_SRGB", 8, 8, 16, True),
    47: ("B10G11R11_UFLOAT", 1, 1, 4, False),
}

ASTC_FORMATS = frozenset({73, 74, 87, 88})
PACKED_FLOAT = 47


@dataclass
class Texture:
    hash: str
    format: int
    format_name: str
    width: int
    height: int
    mips: int
    full_width: int
    full_height: int
    full_mips: int
    layers: int
    resident: bool
    datasize: int
    level_table: list = field(default_factory=list)
    citations: list = field(default_factory=list)

    @property
    def srgb(self) -> bool:
        return FORMATS.get(self.format, ("", 0, 0, 0, False))[4]

    @property
    def streamed_out(self) -> int:
        """Top-of-chain mips that live in packfile pages, not in this leaf."""
        return max(0, self.full_mips - self.mips)


def mip0_bytes(texture: Texture) -> int:
    """Byte size of the largest RESIDENT mip."""
    entry = FORMATS.get(texture.format)
    if entry is None:
        return 0
    _name, bw, bh, bpb, _srgb = entry
    return (((texture.width + bw - 1) // bw)
            * ((texture.height + bh - 1) // bh) * bpb)


def read(path) -> Texture | None:
    """Parse one descriptor, or None when it is not one."""
    blob = Path(path).read_bytes()
    if len(blob) < HEADER_SIZE:
        return None
    resident = struct.unpack_from("<I", blob, OFF_RESIDENT)[0]
    if resident not in (0, 1):
        return None
    full_width, full_height, full_mips = struct.unpack_from(
        "<III", blob, OFF_FULL_WIDTH)
    width, height, mips, datasize = struct.unpack_from("<IIII", blob, OFF_WIDTH)
    if not width or not height:
        return None
    # The dimension law: a leaf's resident size is the full chain shifted by
    # however many top mips were streamed out.
    shift = max(0, full_mips - mips)
    if (max(1, full_width >> shift) != width
            or max(1, full_height >> shift) != height):
        return None
    fmt = struct.unpack_from("<I", blob, OFF_FORMAT)[0]
    table = [v for v in struct.unpack_from(
        f"<{LEVEL_SLOTS}I", blob, OFF_LEVEL_TABLE) if v != LEVEL_SENTINEL]
    citations = [v for v in struct.unpack_from(
        f"<{CITATION_SLOTS}Q", blob, OFF_CITATIONS)
        if v not in (0, 0xFFFFFFFFFFFFFFFF)]
    return Texture(
        hash=Path(path).name, format=fmt,
        format_name=FORMATS.get(fmt, (f"format_{fmt}",))[0],
        width=width, height=height, mips=mips,
        full_width=full_width, full_height=full_height, full_mips=full_mips,
        layers=struct.unpack_from("<I", blob, OFF_LAYERS)[0],
        resident=bool(resident), datasize=datasize,
        level_table=table, citations=citations)


def block_bytes(texture: Texture, width: int, height: int) -> int:
    """Compressed size of one `width` x `height` image in this format."""
    entry = FORMATS.get(texture.format)
    if entry is None:
        return 0
    _name, bw, bh, bpb, _srgb = entry
    return (((width + bw - 1) // bw) * ((height + bh - 1) // bh) * bpb)


def levels(texture: Texture) -> list:
    """Every mip this texture can supply, LARGEST FIRST.

    `[(width, height, size, citation | None), ...]`; `citation` is None for a
    level that is resident in the leaf and a `RawTexturePackfile` hash for one
    that was streamed out.

    The mapping is exact, not a guess: `reversedmipsizes` ascends, the leaf
    holds its first `mips` entries, and citation `i` continues the SAME table
    at index `mips + i`. Checked by size -- every citation's packfile is
    byte-for-byte the entry it lands on.

    Without this a 4096x2048 texture decodes as the 128x64 the leaf happens to
    keep resident, which is what made the first arena export look washed out.
    """
    out = []
    for i in range(texture.full_mips):
        shift = texture.full_mips - 1 - i
        width = max(1, texture.full_width >> shift)
        height = max(1, texture.full_height >> shift)
        size = (texture.level_table[i] if i < len(texture.level_table)
                else block_bytes(texture, width, height))
        if i < texture.mips:
            out.append((width, height, size, None))
        elif i - texture.mips < len(texture.citations):
            out.append((width, height, size,
                        texture.citations[i - texture.mips]))
    out.reverse()
    return out


def pick_level(texture: Texture, cap: int = 0):
    """The largest level fitting `cap` pixels per edge, or the largest there is.

    When even the smallest level is over the cap it wins anyway -- returning
    nothing would drop the texture entirely, and one oversized image beats a
    missing one.
    """
    chain = levels(texture)
    if not chain:
        return None
    if not cap:
        return chain[0]
    for entry in chain:
        if entry[0] <= cap and entry[1] <= cap:
            return entry
    return chain[-1]


def level_payload(primary_path, gpu_path, root, texture: Texture, level) -> bytes:
    """The block bytes for one level from `levels`, resident or streamed."""
    _width, _height, size, citation = level
    if citation is None:
        data = payload(primary_path, gpu_path, texture)
        return data[:size] if len(data) >= size else b""
    path = qtypes.resource(root, qtypes.RAW_TEXTURE_PACKFILE,
                           f"{citation:016x}")
    if path is None:
        return b""
    return Path(path).read_bytes()


def decode_level(primary_path, gpu_path, root, texture: Texture, level,
                 slice_index: int = 0):
    """`(rgba, width, height)` for one level, or None."""
    width, height, size, _citation = level
    data = level_payload(primary_path, gpu_path, root, texture, level)
    need = block_bytes(texture, width, height)
    layers = max(1, texture.layers)
    if layers > 1 and len(data) >= need * layers:
        # An array texture stacks its slices; slice 0 is not "the" texture.
        data = data[need * slice_index:need * (slice_index + 1)]
    if not need or len(data) < need:
        return None
    block = data[:need]
    if texture.format in ASTC_FORMATS:
        import texture2ddecoder
        _name, bw, bh, _bpb, _srgb = FORMATS[texture.format]
        return texture2ddecoder.decode_astc(block, width, height, bw, bh), width, height
    if texture.format == PACKED_FLOAT:
        return _decode_b10g11r11(block, width, height)
    return None


def payload(primary_path, gpu_path, texture: Texture) -> bytes:
    """The block bytes for this leaf, LARGEST MIP FIRST."""
    if texture.resident:
        if gpu_path is None or not Path(gpu_path).is_file():
            return b""
        return Path(gpu_path).read_bytes()
    blob = Path(primary_path).read_bytes()
    return blob[HEADER_SIZE:]


def decode(primary_path, gpu_path, texture: Texture):
    """`(rgba bytes, width, height)` for mip 0, or None when unsupported."""
    data = payload(primary_path, gpu_path, texture)
    need = mip0_bytes(texture)
    if not need or len(data) < need:
        return None
    block = data[:need]

    if texture.format in ASTC_FORMATS:
        import texture2ddecoder
        _name, bw, bh, _bpb, _srgb = FORMATS[texture.format]
        raw = texture2ddecoder.decode_astc(
            block, texture.width, texture.height, bw, bh)
        return raw, texture.width, texture.height
    if texture.format == PACKED_FLOAT:
        return _decode_b10g11r11(block, texture.width, texture.height)
    return None


def _decode_b10g11r11(block: bytes, width: int, height: int):
    """`VK_FORMAT_B10G11R11_UFLOAT_PACK32` -> 8-bit BGRA, tone-mapped by /4."""
    import numpy as np

    words = np.frombuffer(block, dtype="<u4")[:width * height]
    if words.size < width * height:
        return None
    red = _ufloat(words & 0x7FF, 6)
    green = _ufloat((words >> 11) & 0x7FF, 6)
    blue = _ufloat((words >> 22) & 0x3FF, 5)
    stack = np.stack([blue, green, red,
                      np.ones_like(red)], axis=-1)
    scaled = np.clip(stack * 0.25, 0.0, 1.0)
    return (scaled * 255.0 + 0.5).astype(np.uint8).tobytes(), width, height


def _ufloat(bits, mantissa_bits):
    """Unsigned 10/11-bit float (5-bit exponent, no sign) as float32."""
    import numpy as np

    exponent = (bits >> mantissa_bits).astype(np.int32)
    mantissa = (bits & ((1 << mantissa_bits) - 1)).astype(np.float32)
    scale = np.float32(1 << mantissa_bits)
    normal = (1.0 + mantissa / scale) * np.power(
        np.float32(2.0), (exponent - 15).astype(np.float32))
    subnormal = mantissa / scale * np.float32(2.0 ** -14)
    return np.where(exponent == 0, subnormal, normal).astype(np.float32)


def decode_hdr(primary_path, gpu_path, texture: Texture):
    """`(float32 HxWx3 array, width, height)` for a packed-float texture.

    The 8-bit path is lossy in a way that MATTERS for lightmaps: a baked atlas
    runs to a median luma of about 0.056 against a peak near 3.7, so the `/4`
    tone-map plus 8-bit quantisation lands the whole image inside roughly three
    of 255 levels. Everything downstream then amplifies those three levels and
    the banding reads as noise. This keeps the float values.
    """
    if texture.format != PACKED_FLOAT:
        return None
    import numpy as np

    data = payload(primary_path, gpu_path, texture)
    need = texture.width * texture.height
    words = np.frombuffer(data, dtype="<u4")
    if words.size < need:
        return None
    words = words[:need]
    red = _ufloat(words & 0x7FF, 6)
    green = _ufloat((words >> 11) & 0x7FF, 6)
    blue = _ufloat((words >> 22) & 0x3FF, 5)
    rgb = np.stack([red, green, blue], axis=-1)
    return rgb.reshape(texture.height, texture.width, 3), texture.width, texture.height


#: Below this a Radiance pixel is encoded as the zero exponent.
HDR_EPSILON = 1e-32


def write_hdr(out_path, rgb, width: int, height: int) -> bool:
    """Write a Radiance RGBE (`.hdr`) file. Blender loads these natively.

    RGBE rather than EXR on purpose: it needs no third-party encoder, it is
    four bytes per pixel like the source, and a shared 8-bit exponent covers
    this data's range (peak 3.7, median 0.056) with no visible loss -- unlike
    the 8-bit LDR path it replaces.
    """
    import numpy as np

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    values = np.asarray(rgb, dtype=np.float32).reshape(height, width, 3)
    peak = values.max(axis=-1)

    mantissa, exponent = np.frexp(np.maximum(peak, 0.0))
    # frexp gives peak = mantissa * 2**exponent with 0.5 <= mantissa < 1, so
    # mantissa * 256 / peak is the scale that puts the largest channel in
    # 128..255 -- the standard RGBE normalisation.
    with np.errstate(divide="ignore", invalid="ignore"):
        scale = np.where(peak > HDR_EPSILON, mantissa * 256.0 / peak, 0.0)
    encoded = np.clip(values * scale[..., None], 0, 255).astype(np.uint8)
    exp_byte = np.where(peak > HDR_EPSILON,
                        np.clip(exponent + 128, 0, 255), 0).astype(np.uint8)
    pixels = np.concatenate([encoded, exp_byte[..., None]], axis=-1)

    header = ("#?RADIANCE\n"
              "FORMAT=32-bit_rle_rgbe\n"
              "\n"
              f"-Y {height} +X {width}\n").encode("ascii")
    try:
        with out_path.open("wb") as handle:
            handle.write(header)
            # Flat (un-RLE) scanlines: every reader accepts them and the file
            # is small enough that compression buys nothing.
            handle.write(pixels.tobytes())
    except OSError:
        return False
    return True


#: A texture whose alpha is this dark is not an opacity mask -- see `write_png`.
OPACITY_ALPHA_FLOOR = 0.25


def alpha_is_opacity(rgba: bytes, width: int, height: int) -> bool:
    """Could this texture's alpha channel plausibly BE opacity?

    ⛔ On this build it usually cannot. `f83135be7daa0f90` -- the arena hull's
    own base map, on a mesh that is solid metal -- is **90.8% alpha == 0**, and
    `a4f6a5c04a2e95f6` is 48.5%. Treating that as opacity deletes the arena.

    ⚠ It is also not merely unused: writing it into the PNG's alpha channel is
    what turned every textured surface BLACK. Blender's image `Color` output
    comes back black wherever alpha is 0, so a 90%-transparent PNG renders as a
    90%-black one even when nothing reads the alpha socket. Dropping the channel
    restored the hull's full panel plating with the SAME UVs -- which is how the
    UVs were cleared of a bug they never had.

    Corroborating: all 877 shipped Quest materials decode to `blend_mode 0`
    (`eBlendOpaque`), one single signature across the entire corpus.
    """
    import numpy as np

    pixels = np.frombuffer(rgba, dtype=np.uint8).reshape(height, width, 4)
    return float((pixels[:, :, 3] > 0).mean()) >= OPACITY_ALPHA_FLOOR


def write_png(out_path, rgba: bytes, width: int, height: int,
              keep_alpha: bool | None = None) -> bool:
    """`decode_astc` returns BGRA; PNG wants RGBA.

    The alpha channel is DROPPED unless it plausibly carries opacity -- see
    `alpha_is_opacity`. Returns whether the file was written.
    """
    try:
        from PIL import Image
    except ImportError:
        return False
    import numpy as np

    pixels = np.frombuffer(rgba, dtype=np.uint8).reshape(height, width, 4)
    if keep_alpha is None:
        keep_alpha = alpha_is_opacity(rgba, width, height)
    if keep_alpha:
        Image.fromarray(pixels[:, :, [2, 1, 0, 3]]).save(out_path)
    else:
        Image.fromarray(pixels[:, :, [2, 1, 0]]).save(out_path)
    return True
