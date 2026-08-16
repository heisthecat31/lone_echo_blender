"""CGTextureStreamingResourceWin10 -- the per-model texture list.

## Why this module exists

`scripts/evr_scene_extract.py` parsed this file as::

    0x00  8 bytes   header
    0x08  u32       tex_count
    0x0C  8*N       texture hashes
    ...             "bindings": (u32 slot, f32 scale) pairs

The first three lines are accidentally right; the fourth is not.  The verified
layout (`rad-archive-viewer/echomod/resources/cgtexture_streaming_resource.py`,
byte-identical round-trip on 2518/2518 samples) is::

    0    u64        packfilename          (CSymbol64)
    8    u32        textures_count
    12   u64 * N    textures              (CSymbol64 hashes)
    .    u32        layouts_count
    .    192 * N    packfilelayouts       (STextureStreamData)
    .    u32        objecttsdata_count
    .    8 * N      objecttsdata          (u32 texture_index, f32 max_texel_ratio)
    .    u32        sectorobbs_count
    .    40 * N     sectorobbs            (COBB)
    .    u32        sectortsdata_count    (OPTIONAL -- 28-byte files omit it)
    .    per entry: u32 reqs_count, then reqs_count * 8 bytes

So the old code's `rem_offset = 12 + tex_count * 8` landed on `layouts_count`,
and it then read (slot, texture_idx) pairs out of the **192-byte mip-offset
tables**.  Those pairs are mip byte offsets, not bindings.  The
`slot_idx // 4 -> material`, `slot_idx % 4 -> base/normal/ORM/emissive` grouping
built on top of them was therefore noise that happened to be stable per file.

## What this file can and cannot tell you

It gives an **ordered texture list** for a model and, via `objecttsdata`, which
of those textures an object streams and at what texel ratio.  It carries **no
role information at all** -- nothing here says "texture 3 is the normal map".
Roles come from `CGMaterialResourceWin10`'s `SShaderInputData` binds; see
`evr_material_resource`.  Treat this module as the texture *inventory* and the
material resource as the *wiring*.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from evr_resource_types import TEXTURE_STREAMING, normalise_hash, resource_path

STREAM_DATA_SIZE = 192   # STextureStreamData: three u32[16] arrays
OBJECT_TS_SIZE = 8       # SObjectTSData: u32 texture + f32 maxtexelratio
SECTOR_OBB_SIZE = 40     # COBB: 3f center + 4f quat + 3f halfdims
SECTOR_REQ_SIZE = 8      # SSectorTextureRequirement (size solved empirically)

#: A model referencing more textures than this means we are parsing garbage.
#: The shipped maximum across the reference corpus is comfortably under 1000.
MAX_REASONABLE_TEXTURES = 4096


class StreamingParseError(ValueError):
    """The bytes did not match the verified layout.

    Raised rather than returning None so the caller cannot silently treat a
    misparse as "this model has no textures" -- which is precisely the failure
    mode that let the old heuristic run unnoticed.
    """


@dataclass
class ObjectTSData:
    """One object's streaming requirement: which texture, at what texel ratio."""
    texture_index: int = 0
    max_texel_ratio: float = 0.0


@dataclass
class TextureStreamingResource:
    """Parsed CGTextureStreamingResourceWin10."""

    packfilename: str = ""
    #: Ordered texture CSymbol64s, canonical lowercase hex.  Order is meaningful:
    #: `objecttsdata` indexes into it.
    textures: list[str] = field(default_factory=list)
    #: Raw 192-byte mip tables, one per texture, kept undecoded.  Only the
    #: caller that wants mip sizes needs them.
    packfilelayouts: list[bytes] = field(default_factory=list)
    objecttsdata: list[ObjectTSData] = field(default_factory=list)
    sectorobbs: list[bytes] = field(default_factory=list)
    sectortsdata: list[bytes] = field(default_factory=list)
    #: True when the optional trailing sector block was present.
    had_sectortsdata: bool = False

    def texture_at(self, index: int) -> str | None:
        """Bounds-checked index into `textures`."""
        if 0 <= index < len(self.textures):
            return self.textures[index]
        return None

    def streamed_textures(self) -> list[str]:
        """Textures actually referenced by `objecttsdata`, in first-seen order.

        This is a strict subset of `textures` in the general case.  Useful as a
        relevance filter, but do NOT use it as a binding list -- it says nothing
        about roles.
        """
        seen: list[str] = []
        for entry in self.objecttsdata:
            tex = self.texture_at(entry.texture_index)
            if tex and tex not in seen:
                seen.append(tex)
        return seen


class _Reader:
    """Minimal bounds-checked cursor. Every read validates before it consumes."""

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def _need(self, count: int, what: str) -> None:
        if self.pos + count > len(self.data):
            raise StreamingParseError(
                f"{what}: need {count} bytes at offset {self.pos}, "
                f"only {len(self.data) - self.pos} remain (file {len(self.data)}B)"
            )

    def u32(self, what: str) -> int:
        self._need(4, what)
        value = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return value

    def u64(self, what: str) -> int:
        self._need(8, what)
        value = struct.unpack_from("<Q", self.data, self.pos)[0]
        self.pos += 8
        return value

    def f32(self, what: str) -> float:
        self._need(4, what)
        value = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return value

    def blob(self, count: int, what: str) -> bytes:
        self._need(count, what)
        chunk = self.data[self.pos:self.pos + count]
        self.pos += count
        return chunk

    def count(self, what: str, *, stride: int) -> int:
        """Read a count and reject it if the payload cannot possibly fit.

        Catching an absurd count here turns a misparse into an exception at the
        field that caused it, instead of a MemoryError several fields later.
        """
        value = self.u32(what)
        if stride and value > (len(self.data) - self.pos) // stride + 1:
            raise StreamingParseError(
                f"{what}: count {value} needs {value * stride} bytes but only "
                f"{len(self.data) - self.pos} remain -- layout mismatch"
            )
        return value

    @property
    def remaining(self) -> int:
        return len(self.data) - self.pos


def parse(data: bytes, *, strict: bool = True) -> TextureStreamingResource:
    """Parse the verified layout. Raises `StreamingParseError` on mismatch.

    `strict=False` tolerates trailing bytes instead of raising; the parse is
    otherwise identical.  Leave it on unless you are probing an unknown variant
    -- trailing bytes mean the layout assumption is wrong somewhere above.
    """
    reader = _Reader(data)
    res = TextureStreamingResource()

    res.packfilename = normalise_hash(reader.u64("packfilename"))

    n_textures = reader.count("textures_count", stride=8)
    if n_textures > MAX_REASONABLE_TEXTURES:
        raise StreamingParseError(
            f"textures_count {n_textures} exceeds the sane ceiling "
            f"{MAX_REASONABLE_TEXTURES} -- this is not a streaming resource"
        )
    res.textures = [normalise_hash(reader.u64("texture")) for _ in range(n_textures)]

    n_layouts = reader.count("layouts_count", stride=STREAM_DATA_SIZE)
    res.packfilelayouts = [
        reader.blob(STREAM_DATA_SIZE, "packfilelayout") for _ in range(n_layouts)
    ]

    n_objects = reader.count("objecttsdata_count", stride=OBJECT_TS_SIZE)
    for _ in range(n_objects):
        index = reader.u32("objecttsdata.texture")
        ratio = reader.f32("objecttsdata.maxtexelratio")
        res.objecttsdata.append(ObjectTSData(texture_index=index, max_texel_ratio=ratio))

    n_obbs = reader.count("sectorobbs_count", stride=SECTOR_OBB_SIZE)
    res.sectorobbs = [reader.blob(SECTOR_OBB_SIZE, "sectorobb") for _ in range(n_obbs)]

    # Minimal 28-byte files stop here and omit the outer count entirely.
    if reader.remaining >= 4:
        res.had_sectortsdata = True
        outer = reader.count("sectortsdata_count", stride=4)
        for _ in range(outer):
            reqs = reader.count("sectortsdata.reqs_count", stride=SECTOR_REQ_SIZE)
            res.sectortsdata.append(
                reader.blob(reqs * SECTOR_REQ_SIZE, "sectortsdata.requirements")
            )

    if strict and reader.remaining:
        raise StreamingParseError(
            f"{reader.remaining} trailing bytes at offset {reader.pos} -- the "
            f"layout does not fully explain this {len(data)}-byte file"
        )
    return res


def load_for_model(root: Path, model_hash) -> TextureStreamingResource | None:
    """Read and parse the streaming resource for one model hash.

    Returns None when the model simply has no streaming resource (common and
    not an error).  A file that EXISTS but fails to parse still raises, because
    that is a layout bug and must not be mistaken for "no textures".
    """
    path = resource_path(root, TEXTURE_STREAMING, model_hash)
    if path is None:
        return None
    return parse(path.read_bytes())


def texture_list(root: Path, model_hash) -> list[str]:
    """Just the ordered texture hashes for a model; `[]` when absent."""
    res = load_for_model(root, model_hash)
    return list(res.textures) if res else []


def audit(root: Path, limit: int | None = None) -> dict:
    """Parse every streaming resource under `root` and report the outcome.

    Read-only.  Use this to confirm the layout holds across YOUR extract before
    trusting any material output built on top of it -- it is the cheap version
    of the 2518-sample round-trip the reference parser was validated with.
    """
    directory = Path(root) / TEXTURE_STREAMING
    if not directory.is_dir():
        return {"error": f"no such directory: {directory}"}

    files = sorted(p for p in directory.iterdir() if p.is_file())
    if limit:
        files = files[:limit]

    ok = 0
    failures: list[str] = []
    total_textures = 0
    with_objects = 0
    for path in files:
        try:
            res = parse(path.read_bytes())
        except StreamingParseError as exc:
            failures.append(f"{path.name}: {exc}")
            continue
        except Exception as exc:  # pragma: no cover - diagnostic breadth
            failures.append(f"{path.name}: {type(exc).__name__}: {exc}")
            continue
        ok += 1
        total_textures += len(res.textures)
        if res.objecttsdata:
            with_objects += 1

    return {
        "files": len(files),
        "parsed": ok,
        "failed": len(failures),
        "total_texture_refs": total_textures,
        "files_with_objecttsdata": with_objects,
        "failures": failures[:20],
    }


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="flat Echo VR extract root")
    ap.add_argument("--model", help="dump one model's texture list")
    ap.add_argument("--limit", type=int, default=None, help="cap the audit")
    args = ap.parse_args()

    if args.model:
        resource = load_for_model(args.root, args.model)
        if resource is None:
            print(f"no streaming resource for {args.model}")
            raise SystemExit(1)
        print(f"packfile   {resource.packfilename}")
        print(f"textures   {len(resource.textures)}")
        for i, tex in enumerate(resource.textures):
            print(f"  [{i:3d}] {tex}")
        print(f"objecttsdata {len(resource.objecttsdata)}")
        for entry in resource.objecttsdata[:32]:
            name = resource.texture_at(entry.texture_index) or "<out of range>"
            print(f"  tex[{entry.texture_index:3d}] ratio={entry.max_texel_ratio:.4f}  {name}")
    else:
        print(json.dumps(audit(args.root, args.limit), indent=2))
