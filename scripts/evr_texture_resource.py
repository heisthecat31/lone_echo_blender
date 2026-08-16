"""cgtextureresourceWin10 -- texture headers, DXGI formats, and DDS rebuild.

## Why the DXGI format matters more than it looks

`le_mesh.materials` needs `dxgi_by_tex` for four separate decisions, and gets
all four wrong without it:

* **colorspace** -- sRGB vs Non-Color.  A base colour loaded as Non-Color is
  visibly washed out; a normal map loaded as sRGB is subtly, persistently wrong.
* **BC5 normal reconstruction** -- a two-channel BC5 normal needs Z rebuilt as
  `sqrt(1 - x^2 - y^2)`.  Without the format there is nothing to trigger on.
* **alpha capability** -- whether the base colour's `.a` can carry alpha at all,
  which gates the entire alpha chain.
* **composite role classification** -- `composite_roles_from_format` resolves
  ambiguous binds by format.

So this module is not an optimisation.  It is a prerequisite for the material
specs being correct at all.

## Verified layout (stream 0)

From `rad-archive-viewer/echomod/resources/cgtexture_resource.py`, byte-identical
round-trip on 12261/12261 samples::

    0x00  192  STextureStreamData  packfilelayout   (3 x u32[16])
    0xC0  u32  streamingdisabled
    0xC4  u32  maxwidth
    0xC8  u32  maxheight
    0xCC  u32  maxmipcount
    0xD0  u32  arraysize
    0xD4  u32  cubemap
    0xD8  u32  format             <- DXGI_FORMAT
    0xDC  u32  srgb_or_tilemode
    0xE0  u32  createasarray
    0xE4  u32  volume
    0xE8  u32  width
    0xEC  u32  height
    0xF0  u32  mipcount
    0xF4  u32  resmemsize
    0xF8  u32  pitchorlinearsize
    0xFC  u32  padding
    0x100 ...  inline_pixels      (DDS-prefixed; only when streamingdisabled==0)

File-size rule across that corpus: `streamingdisabled == 1` -> exactly 256 bytes;
`streamingdisabled == 0` -> `256 + resmemsize`.

## The high-resolution mip chain -- read this before trusting output

`evr_scene_extract.reconstruct_dds` locates high-res mips by reading u64s from
`0x40..0x100` and stopping at an all-`0xFF` word, treating each as a
`RawTexturePackfileWin10` file name.  Under the verified layout that byte range
is **`reversedcmpmipsizes` (0x40), `reversedmipsizes` (0x80), and the typed
fields (0xC0)** -- not a hash list.  The values it pulls out are mip sizes and
dimensions reinterpreted as hashes, so the lookups overwhelmingly miss and the
function silently falls through to the inline low-resolution DDS.

That is why it "works" but looks soft: you have been getting the streamed-out
tail of the mip chain, not the top of it.

`packfilelayout` is the field that actually addresses the high-res data --
`reversedmipoffsets[i]` / `reversedcmpmipsizes[i]` are offsets and sizes into a
pack file.  Both strategies are implemented below and
`rebuild_dds(..., strategy="auto")` tries the layout-driven one first, falling
back to the legacy hash scan.  `diagnose()` reports which one fired and why, per
texture.

⛔ **The layout-driven path is written from the field semantics, not from a
verified extraction.**  I could not run it against real data in the session that
wrote it.  Run `diagnose()` on a handful of textures and check `strategy_used`
and the resulting dimensions before trusting a full scene export.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from evr_resource_types import (
    RAW_TEXTURE_PACK,
    TEXTURE_DDS_SIDECAR,
    TEXTURE_RESOURCE,
    normalise_hash,
    resource_path,
)

HEADER_SIZE = 256
MIP_TABLE_ENTRIES = 16

OFF_REVERSED_MIP_OFFSETS = 0x00
OFF_REVERSED_CMP_MIP_SIZES = 0x40
OFF_REVERSED_MIP_SIZES = 0x80
OFF_STREAMINGDISABLED = 0xC0
OFF_MAXWIDTH = 0xC4
OFF_MAXHEIGHT = 0xC8
OFF_MAXMIPCOUNT = 0xCC
OFF_ARRAYSIZE = 0xD0
OFF_CUBEMAP = 0xD4
OFF_FORMAT = 0xD8
OFF_SRGB_OR_TILEMODE = 0xDC
OFF_CREATEASARRAY = 0xE0
OFF_VOLUME = 0xE4
OFF_WIDTH = 0xE8
OFF_HEIGHT = 0xEC
OFF_MIPCOUNT = 0xF0
OFF_RESMEMSIZE = 0xF4
OFF_PITCHORLINEARSIZE = 0xF8

# DDS header field offsets, from the start of the 'DDS ' magic.
DDS_MAGIC = b"DDS "
DDS_OFF_HEIGHT = 12
DDS_OFF_WIDTH = 16
DDS_OFF_MIPCOUNT = 28
DDS_OFF_FOURCC = 84
DDS_OFF_DX10_ARRAYSIZE = 140
DDS_HEADER_LEN = 128
DDS_HEADER_LEN_DX10 = 148

#: DXGI formats that carry a two-channel tangent-space normal.  Kept here so the
#: extractor can flag normals without importing the Blender-side module.
BC5_FORMATS = frozenset({82, 83, 84})          # BC5_TYPELESS/UNORM/SNORM
#: DXGI formats whose name ends `_SRGB`.
#: ⚠ Must stay identical to `le_mesh.materials.SRGB_DXGI` -- that set is what
#: actually decides `colorspace`, and a disagreement here would report one thing
#: in `diagnose()` while the material did another.
#: `test_evr_texture_resource.py` pins the two together.
SRGB_FORMATS = frozenset({
    29,    # R8G8B8A8_UNORM_SRGB
    72,    # BC1_UNORM_SRGB
    75,    # BC2_UNORM_SRGB
    78,    # BC3_UNORM_SRGB
    91,    # B8G8R8A8_UNORM_SRGB
    93,    # B8G8R8X8_UNORM_SRGB
    99,    # BC7_UNORM_SRGB
})

#: The plain (non-`_SRGB`) counterpart of each format in `SRGB_FORMATS`, one
#: for one. BC1/BC2/BC3/BC7 are the RGB(A)-carrying block formats -- the ones
#: an engine authors colour into -- whether or not the specific asset was
#: flagged `_SRGB`; BC4/BC5 (grayscale/normal) never appear here. Used as a
#: FALLBACK base-colour signal only (`evr_materials.roles_from_texture_list`)
#: when a texture family has no `_SRGB`-flagged member at all: measured on
#: `576ed3f8428ebc4b`, several models' entire texture list is BC1_UNORM/
#: BC3_UNORM with no `_SRGB` variant present anywhere, and those materials
#: rendered with no base colour at all under the SRGB-only rule.
COLOR_CAPABLE_UNORM_FORMATS = frozenset({
    28,    # R8G8B8A8_UNORM
    71,    # BC1_UNORM
    74,    # BC2_UNORM
    77,    # BC3_UNORM
    87,    # B8G8R8A8_UNORM
    98,    # BC7_UNORM
})

DXGI_NAMES = {
    28: "R8G8B8A8_UNORM", 29: "R8G8B8A8_UNORM_SRGB",
    71: "BC1_UNORM", 72: "BC1_UNORM_SRGB",
    74: "BC2_UNORM", 75: "BC2_UNORM_SRGB",
    77: "BC3_UNORM", 78: "BC3_UNORM_SRGB",
    80: "BC4_UNORM", 81: "BC4_SNORM",
    83: "BC5_UNORM", 84: "BC5_SNORM",
    87: "B8G8R8A8_UNORM", 91: "B8G8R8A8_UNORM_SRGB",
    95: "BC6H_UF16", 96: "BC6H_SF16",
    98: "BC7_UNORM", 99: "BC7_UNORM_SRGB",
}


class TextureParseError(ValueError):
    """The bytes do not match the cgtextureresourceWin10 layout."""


def _u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def _u32_table(data: bytes, offset: int) -> list:
    return list(struct.unpack_from(f"<{MIP_TABLE_ENTRIES}I", data, offset))


@dataclass
class TextureResource:
    """Parsed cgtextureresourceWin10 header plus any inline pixel blob."""

    texture_hash: str = ""
    reversed_mip_offsets: list = field(default_factory=list)
    reversed_cmp_mip_sizes: list = field(default_factory=list)
    reversed_mip_sizes: list = field(default_factory=list)
    streamingdisabled: int = 1
    maxwidth: int = 0
    maxheight: int = 0
    maxmipcount: int = 0
    arraysize: int = 0
    cubemap: int = 0
    format: int = 0
    srgb_or_tilemode: int = 0
    createasarray: int = 0
    volume: int = 0
    width: int = 0
    height: int = 0
    mipcount: int = 0
    resmemsize: int = 0
    pitchorlinearsize: int = 0
    inline_pixels: bytes = b""

    @property
    def format_name(self) -> str:
        return DXGI_NAMES.get(self.format, f"DXGI_{self.format}")

    @property
    def is_normal_format(self) -> bool:
        """BC5 -- two channels, Z must be reconstructed."""
        return self.format in BC5_FORMATS

    @property
    def is_srgb(self) -> bool:
        return self.format in SRGB_FORMATS

    @property
    def has_inline_dds(self) -> bool:
        return self.inline_pixels[:4] == DDS_MAGIC

    def inline_dds(self) -> bytes:
        """The inline DDS blob, or `b""` when this texture is fully streamed."""
        return self.inline_pixels if self.has_inline_dds else b""

    def streamed_mips(self) -> list:
        """`[(offset, compressed_size, uncompressed_size), ...]`, largest first.

        The tables are stored *reversed* (the engine's own naming), so entry 0
        is the LARGEST mip.  Entries with zero size are unused slots and are
        dropped -- a texture declares 16 slots regardless of how many it uses.
        """
        out = []
        for i in range(MIP_TABLE_ENTRIES):
            cmp_size = self.reversed_cmp_mip_sizes[i] if i < len(self.reversed_cmp_mip_sizes) else 0
            raw_size = self.reversed_mip_sizes[i] if i < len(self.reversed_mip_sizes) else 0
            offset = self.reversed_mip_offsets[i] if i < len(self.reversed_mip_offsets) else 0
            if not cmp_size or cmp_size == 0xFFFFFFFF or offset == 0xFFFFFFFF:
                continue
            out.append((offset, cmp_size, raw_size))
        return out


def parse(data: bytes, *, texture_hash="") -> TextureResource:
    """Parse a cgtextureresourceWin10 blob."""
    if len(data) < HEADER_SIZE:
        raise TextureParseError(
            f"file is {len(data)}B, need at least {HEADER_SIZE}B"
        )
    res = TextureResource(
        texture_hash=normalise_hash(texture_hash),
        reversed_mip_offsets=_u32_table(data, OFF_REVERSED_MIP_OFFSETS),
        reversed_cmp_mip_sizes=_u32_table(data, OFF_REVERSED_CMP_MIP_SIZES),
        reversed_mip_sizes=_u32_table(data, OFF_REVERSED_MIP_SIZES),
        streamingdisabled=_u32(data, OFF_STREAMINGDISABLED),
        maxwidth=_u32(data, OFF_MAXWIDTH),
        maxheight=_u32(data, OFF_MAXHEIGHT),
        maxmipcount=_u32(data, OFF_MAXMIPCOUNT),
        arraysize=_u32(data, OFF_ARRAYSIZE),
        cubemap=_u32(data, OFF_CUBEMAP),
        format=_u32(data, OFF_FORMAT),
        srgb_or_tilemode=_u32(data, OFF_SRGB_OR_TILEMODE),
        createasarray=_u32(data, OFF_CREATEASARRAY),
        volume=_u32(data, OFF_VOLUME),
        width=_u32(data, OFF_WIDTH),
        height=_u32(data, OFF_HEIGHT),
        mipcount=_u32(data, OFF_MIPCOUNT),
        resmemsize=_u32(data, OFF_RESMEMSIZE),
        pitchorlinearsize=_u32(data, OFF_PITCHORLINEARSIZE),
        inline_pixels=data[HEADER_SIZE:],
    )
    return res


def load(root: Path, texture_hash) -> TextureResource | None:
    """Read one texture resource from a flat extract; None when absent."""
    path = resource_path(root, TEXTURE_RESOURCE, texture_hash)
    if path is None:
        return None
    return parse(path.read_bytes(), texture_hash=texture_hash)


def dxgi_map(root: Path, texture_hashes) -> dict:
    """`{texture_hash -> DXGI format int}` for a set of textures.

    Missing textures are simply absent from the result; `le_mesh.materials`
    already treats an absent entry as "format unknown" and degrades cleanly.
    """
    out: dict = {}
    for tex in texture_hashes:
        canonical = normalise_hash(tex)
        if not canonical or canonical in out:
            continue
        try:
            res = load(root, canonical)
        except TextureParseError:
            continue
        if res is not None:
            out[canonical] = res.format
    return out


# ---------------------------------------------------------------------------
# DDS reconstruction
# ---------------------------------------------------------------------------

def _dds_header_length(blob: bytes) -> int:
    """128, or 148 when the DX10 extension header is present."""
    if len(blob) >= DDS_HEADER_LEN_DX10 and blob[DDS_OFF_FOURCC:DDS_OFF_FOURCC + 4] == b"DX10":
        return DDS_HEADER_LEN_DX10
    return DDS_HEADER_LEN


def _patch_dds_header(header: bytearray, *, width: int, height: int,
                      mipcount: int) -> bytearray:
    """Rewrite dimensions/mip count, and collapse an array texture to one slice.

    Blender has no DDS array-texture concept, so a slice count above 1 makes the
    file unreadable rather than merely wrong.
    """
    struct.pack_into("<I", header, DDS_OFF_HEIGHT, height)
    struct.pack_into("<I", header, DDS_OFF_WIDTH, width)
    struct.pack_into("<I", header, DDS_OFF_MIPCOUNT, mipcount)
    if len(header) >= DDS_HEADER_LEN_DX10 and header[DDS_OFF_FOURCC:DDS_OFF_FOURCC + 4] == b"DX10":
        if _u32(bytes(header), DDS_OFF_DX10_ARRAYSIZE) > 1:
            struct.pack_into("<I", header, DDS_OFF_DX10_ARRAYSIZE, 1)
    return header


def _legacy_high_res_hashes(data: bytes) -> list:
    """The original `reconstruct_dds` hash scan, preserved as a fallback.

    ⚠ Reads `0x40..0x100`, which the verified layout says is the mip-size tables
    and the typed fields.  Retained ONLY because it is what currently ships and
    it costs nothing to try after the layout-driven path declines.  Every hash it
    returns is validated against the pack directory before use, so a garbage
    reading yields an empty list rather than corrupt output.
    """
    hashes = []
    for offset in range(0x40, 0x100, 8):
        chunk = data[offset:offset + 8]
        if len(chunk) < 8 or chunk == b"\xff" * 8:
            break
        hashes.append(normalise_hash(struct.unpack("<Q", chunk)[0]))
    return hashes


def _rebuild_legacy(res: TextureResource, data: bytes, root: Path) -> tuple:
    """Legacy strategy: prepend whole `RawTexturePackfileWin10` files."""
    inline = res.inline_dds()
    if not inline:
        return None, "no inline DDS to extend"

    pack_dir = Path(root) / RAW_TEXTURE_PACK
    payloads = []
    for tex_hash in reversed(_legacy_high_res_hashes(data)):
        candidate = resource_path(root, RAW_TEXTURE_PACK, tex_hash)
        if candidate is not None:
            payloads.append(candidate.read_bytes())

    if not payloads:
        return None, "no high-res pack files matched the legacy hash scan"

    header_len = _dds_header_length(inline)
    header = bytearray(inline[:header_len])
    extra = len(payloads)
    header = _patch_dds_header(
        header,
        width=res.width * (2 ** extra),
        height=res.height * (2 ** extra),
        mipcount=res.mipcount + extra,
    )
    blob = bytes(header) + b"".join(payloads) + inline[header_len:]
    return blob, f"legacy: prepended {extra} pack file(s)"


def _rebuild_from_sidecar(root: Path, texture_hash) -> tuple:
    """The real fix for `streamingdisabled == 1` textures: `TEXTURE_DDS_SIDECAR`
    holds a COMPLETE, ready-to-use DDS file under the SAME hash, for
    essentially every texture in the corpus -- not a reconstruction, just a
    direct read. See `TEXTURE_DDS_SIDECAR`'s docstring in `evr_resource_types`
    for how this was confirmed (byte dumps starting with the literal "DDS "
    magic whose header dimensions/mipcount matched `cgtextureresourceWin10`
    exactly, plus independent naming confirmation from the Go extractor and
    several rad-archive-viewer mod-tool scripts).

    Tried FIRST in `rebuild_dds`: a complete file beats reconstructing from
    the other two strategies' partial data, and it is what recovers the
    textures `_rebuild_from_layout` and `_rebuild_legacy` cannot -- both
    require SOME inline low-res DDS to extend, which `streamingdisabled == 1`
    textures never have by construction (0/12261-corpus counter-examples).
    """
    path = resource_path(root, TEXTURE_DDS_SIDECAR, texture_hash)
    if path is None:
        return None, "no TEXTURE_DDS_SIDECAR file for this hash"
    blob = path.read_bytes()
    if blob[:4] != b"DDS ":
        return None, f"sidecar file present but not DDS-magic-prefixed ({len(blob)}B)"
    return blob, f"sidecar: read {len(blob)}B directly, no reconstruction needed"


def _rebuild_from_layout(res: TextureResource, root: Path,
                         packfile_hash: str | None) -> tuple:
    """Layout-driven strategy: slice mips out of the named pack file.

    Uses `packfilelayout`'s `reversedmipoffsets` / `reversedcmpmipsizes`, which
    are what actually address the streamed mip chain.  Requires the pack file
    name, which lives in the model's `CGTextureStreamingResourceWin10`.
    """
    inline = res.inline_dds()
    if not inline:
        return None, "no inline DDS to extend"
    if not packfile_hash:
        return None, "no packfile name supplied (needs the streaming resource)"

    pack_path = resource_path(root, RAW_TEXTURE_PACK, packfile_hash)
    if pack_path is None:
        return None, f"pack file {packfile_hash} not present"

    mips = res.streamed_mips()
    if not mips:
        return None, "packfilelayout declares no streamed mips"

    pack = pack_path.read_bytes()
    payloads = []
    for offset, cmp_size, _raw in mips:
        end = offset + cmp_size
        if end > len(pack):
            return None, (f"mip at {offset}+{cmp_size} runs past the "
                          f"{len(pack)}B pack file -- layout mismatch")
        payloads.append(pack[offset:end])

    if not payloads:
        return None, "no mip slices resolved"

    header_len = _dds_header_length(inline)
    header = bytearray(inline[:header_len])
    extra = len(payloads)
    header = _patch_dds_header(
        header,
        width=max(res.maxwidth, res.width),
        height=max(res.maxheight, res.height),
        mipcount=max(res.maxmipcount, res.mipcount + extra),
    )
    blob = bytes(header) + b"".join(payloads) + inline[header_len:]
    return blob, f"layout: sliced {extra} mip(s) from {packfile_hash}"


def rebuild_dds(root: Path, texture_hash, *, packfile_hash=None,
                strategy: str = "auto") -> tuple:
    """Rebuild the fullest DDS available for a texture.

    Returns `(bytes | None, note)`.  `note` always explains the outcome,
    including on success, so a caller can log which strategy produced the file.

    Strategies: `"sidecar"` (read `TEXTURE_DDS_SIDECAR` directly -- a complete
    file, not a reconstruction; see `_rebuild_from_sidecar`), `"layout"`
    (packfilelayout-driven), `"legacy"` (the shipped hash scan), `"inline"`
    (no reconstruction -- just the resident low-res DDS), or `"auto"` to try
    them in that order. `sidecar` goes first in `"auto"`: it is present for
    essentially every texture and needs no inline DDS to extend, unlike
    `layout`/`legacy`, so it is what recovers `streamingdisabled == 1`
    textures that the other two strategies structurally cannot.
    """
    path = resource_path(root, TEXTURE_RESOURCE, texture_hash)
    if path is None:
        return None, f"no texture resource {normalise_hash(texture_hash)}"

    data = path.read_bytes()
    try:
        res = parse(data, texture_hash=texture_hash)
    except TextureParseError as exc:
        return None, str(exc)

    notes = []
    order = ({"sidecar": ("sidecar",), "layout": ("layout",),
              "legacy": ("legacy",), "inline": ("inline",)}.get(strategy)
             or ("sidecar", "layout", "legacy", "inline"))

    for name in order:
        if name == "sidecar":
            blob, note = _rebuild_from_sidecar(root, texture_hash)
        elif name == "layout":
            blob, note = _rebuild_from_layout(res, root, packfile_hash)
        elif name == "legacy":
            blob, note = _rebuild_legacy(res, data, root)
        else:
            blob = res.inline_dds() or None
            note = "inline: resident low-resolution DDS only" if blob else \
                   "inline: texture is fully streamed, nothing resident"
        if blob:
            return blob, "; ".join(notes + [note])
        notes.append(f"{name} declined ({note})")

    return None, "; ".join(notes)


def diagnose(root: Path, texture_hash, packfile_hash=None) -> dict:
    """Per-texture report: format, dimensions, and which rebuild path fires.

    Run this on a sample before trusting a full export -- it is the cheapest way
    to find out whether the high-resolution chain is actually being reached.
    """
    res = load(root, texture_hash)
    if res is None:
        return {"texture": normalise_hash(texture_hash), "present": False}

    blob, note = rebuild_dds(root, texture_hash, packfile_hash=packfile_hash)
    report = {
        "texture": normalise_hash(texture_hash),
        "present": True,
        "format": res.format,
        "format_name": res.format_name,
        "is_normal_format": res.is_normal_format,
        "is_srgb": res.is_srgb,
        "resident": [res.width, res.height],
        "max": [res.maxwidth, res.maxheight],
        "mipcount": res.mipcount,
        "maxmipcount": res.maxmipcount,
        "streamingdisabled": res.streamingdisabled,
        "streamed_mip_slots": len(res.streamed_mips()),
        "has_inline_dds": res.has_inline_dds,
        "rebuilt_bytes": len(blob) if blob else 0,
        "note": note,
    }
    # The tell that reconstruction is NOT reaching the top of the chain.
    if res.maxwidth > res.width and blob and len(blob) <= len(res.inline_dds()) + 256:
        report["warning"] = (
            f"resident is {res.width}x{res.height} but the texture declares "
            f"{res.maxwidth}x{res.maxheight} -- high-res mips were not recovered"
        )
    return report


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="flat Echo VR extract root")
    ap.add_argument("--texture", help="diagnose one texture hash")
    ap.add_argument("--packfile", help="pack file hash from the streaming resource")
    ap.add_argument("--sample", type=int, default=0,
                    help="diagnose the first N textures in the extract")
    args = ap.parse_args()

    if args.texture:
        print(json.dumps(diagnose(args.root, args.texture, args.packfile), indent=2))
    elif args.sample:
        directory = args.root / TEXTURE_RESOURCE
        files = sorted(p for p in directory.iterdir() if p.is_file())[:args.sample]
        for path in files:
            stem = path.stem if path.suffix == ".bin" else path.name
            print(json.dumps(diagnose(args.root, stem), indent=None))
    else:
        ap.error("pass --texture or --sample")


# ---------------------------------------------------------------------------
# Resolution cap
# ---------------------------------------------------------------------------

#: DXGI formats stored as 8-byte 4x4 blocks; everything else block-compressed
#: here is 16.  (BC1/BC4 vs BC2/BC3/BC5/BC6H/BC7.)
_BC_8BYTE = frozenset({70, 71, 72, 79, 80, 81})
_BC_16BYTE = frozenset({73, 74, 75, 76, 77, 78, 82, 83, 84, 94, 95, 96, 97, 98, 99})


def _mip_bytes(width: int, height: int, dxgi: int) -> int:
    """Size of one mip level, block-aware."""
    if dxgi in _BC_8BYTE or dxgi in _BC_16BYTE:
        block = 8 if dxgi in _BC_8BYTE else 16
        return max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * block
    # Uncompressed 32bpp is the only non-BC case this pipeline emits.
    return width * height * 4


#: Position of the streaming-TIER digit inside a texture CSymbol64.
#:
#: Echo VR ships one texture as a family of fixed-resolution tiers that differ
#: in exactly one hex digit, and a LOWER digit is a HIGHER resolution:
#:
#:     d0185a41bf37c1fe   1024x1024      <- tier 1, the best that exists
#:     d0185a42bf37c1fe    512x512
#:     d0185a43bf37c1fe    256x256
#:     d0185a44bf37c1fe    128x128
#:
#: Comparing those three hashes character by character, index 7 is the only one
#: that varies. (An earlier attempt at this rewrote index 6 -- the `4` of `a4`
#: -- and silently upgraded nothing.)
TIER_DIGIT = 7
_TIER_ORDER = "0123456789abcdef"


def best_tier(root: Path, texture_hash: str) -> str:
    """The highest-resolution tier of a texture that exists, or the input.

    A material frequently names a lower tier than the game ships -- binding
    `d0185a42...` (512) where `d0185a41...` (1024) is present -- so every
    consumer would otherwise get a half-resolution texture with no indication
    anything better existed.

    Only upgrades to a candidate that is BOTH present and at least as large as
    the original, so a coincidental hash collision cannot downgrade a texture.
    """
    h = normalise_hash(texture_hash)
    if len(h) != 16:
        return texture_hash
    current = load(root, h)
    current_area = (current.width * current.height) if current else 0
    for digit in _TIER_ORDER:
        if digit == h[TIER_DIGIT]:
            break                       # reached our own tier: nothing better
        candidate = h[:TIER_DIGIT] + digit + h[TIER_DIGIT + 1:]
        found = load(root, candidate)
        if found is None:
            continue
        if current_area and found.width * found.height < current_area:
            continue
        return candidate
    return h


def scale_dds_resolution(blob: bytes, divisor: int) -> tuple:
    """Halve (or quarter, ...) a texture RELATIVE to its own native size.

    `cap_dds_resolution` takes an absolute ceiling, which leaves already-small
    textures untouched. This instead reduces every texture by the same factor,
    which is what "half resolution" means: a 4096 becomes 2048 and a 256
    becomes 128.

    `divisor` must be a power of two -- each step down the mip chain halves both
    sides, so this is still exact mip selection, never resampling.
    """
    if divisor <= 1:
        return blob, "no scaling requested"
    if len(blob) < 148 or blob[:4] != b"DDS " or blob[84:88] != b"DX10":
        return blob, "not a DX10 DDS"
    height, width = struct.unpack_from("<II", blob, 12)
    target = max(1, max(width, height) // divisor)
    return cap_dds_resolution(blob, target)


def cap_dds_resolution(blob: bytes, max_dim: int) -> tuple:
    """Drop leading mips until the top level is <= `max_dim`.

    Returns `(blob, note)`; the blob is unchanged when no cap is needed or the
    file cannot be parsed confidently.

    Why this and not a resampler: a DDS already CONTAINS the smaller versions.
    Discarding the top of the chain is exact -- no resampling, no quality loss
    below the cap -- and it is what actually fixes the crash, because Blender's
    material preview uploads textures DECOMPRESSED: a 2048x2048 BC1 is 2.7MB on
    disk but 16MB in VRAM, so a level with 2836 textures (2.11GB on disk) needs
    well over 10GB and Blender dies. Capping at 512 cuts that by ~16x.
    """
    if len(blob) < 148 or blob[:4] != b"DDS " or blob[84:88] != b"DX10":
        return blob, "not a DX10 DDS"
    height, width = struct.unpack_from("<II", blob, 12)
    mip_count = struct.unpack_from("<I", blob, 28)[0] or 1
    dxgi = struct.unpack_from("<I", blob, 128)[0]
    if max(width, height) <= max_dim:
        return blob, "already within cap"

    header, pixels = blob[:148], blob[148:]
    offset = 0
    w, h, dropped = width, height, 0
    while max(w, h) > max_dim and dropped < mip_count - 1:
        step = _mip_bytes(w, h, dxgi)
        if offset + step > len(pixels):
            return blob, "mip chain shorter than the header claims"
        offset += step
        w = max(1, w // 2)
        h = max(1, h // 2)
        dropped += 1
    if not dropped:
        return blob, "no mip small enough"

    out = bytearray(header)
    struct.pack_into("<II", out, 12, h, w)
    struct.pack_into("<I", out, 28, mip_count - dropped)
    struct.pack_into("<I", out, 20, _mip_bytes(w, h, dxgi))   # pitchOrLinearSize
    return bytes(out) + pixels[offset:], (
        f"capped {width}x{height} -> {w}x{h} (dropped {dropped} mip(s))")
