"""Clone a whole Echo VR level under a new name.

    python scripts/evr_clone_level.py mpl_arena_a mpl_arena_v2_a [--out <dir>]

Writes `<out>/<type hash>/<new level hash>` for every per-level resource, with
every self-reference repointed at the new name. `--out` defaults to the packer's
`input-pcvr` staging folder, so the result is ready to pack as-is.

WHY A LEVEL CAN BE CLONED BY RENAMING
-------------------------------------
Levels are keyed by `rad_hash(level name)`. `mpl_arena_a` is
`576ed3f8428ebc4b`, and 90 type directories hold a file under that name --
111.8 MB, everything from the 60 MB collision BVH down to a 16-byte info stub.
*Asset* resources (meshes, textures, materials, shadersets, sound banks) are
NOT among them: they are content-hashed and global, so the clone references the
same ones rather than duplicating them.

Two independent scans establish that the set is closed:

* `CArchiveResource` -- the level's own load manifest, `[u32 0][u32 count]` then
  `count` x `(u64 type, u64 name)` -- lists 2,076 resources of 99 types, of
  which exactly **85 name the level itself**. Those 85 are exactly the 85
  per-level CPU resources on disk. The other five files are the three GPU
  companions (`...Win10GPU`, paired implicitly by name), the empty lightmap GPU
  blob, and the manifest itself.
* Scanning all **69,694** extracted files for the arena's 8-byte name hash finds
  it outside the level in only three: one `CRxMaterialFXResource` and two
  compiled `StreamingScript` DLLs, where it appears inside x86-64
  `mov rax, imm64` instructions. Those are hardcoded call sites, not a registry
  the clone has to join -- nothing indexes levels by name that a new level must
  be added to. (The game reaches a new level through
  `sourcedb/rad15/json/r14/config/matchsettings_config.json`, which is plain
  JSON in the install and is not packed.)

THE FOUR FILES THAT NEED PATCHING
---------------------------------
86 of the 90 files carry no self-reference at all and are copied byte-for-byte.
Four do, and each was checked structurally rather than pattern-matched:

| file | self-refs | what they are |
|---|--:|---|
| `CArchiveResource` | 85 | the `name` half of a manifest entry |
| `CComponentSpaceResource` | 69 | `+0` owner, then the level field of all 68 `(component, level)` rows |
| `CGameLevelResource` | 1 | `+104` |
| `COccluderMeshCR` | 1 | `+88` |

`CGameLevelResource+104`, `CComponentSpaceResource+0` and `COccluderMeshCR+88`
each hold the owning level's own hash on **every** level that ships the type
(9/9 checked: arena, lobby_b2, lobby_b_arena, tutorial_arena, tutorial_lobby,
fission, dyson, gauss, combustion), so these are fields, not coincidences.

`CGameLevelResource+16` is the PARENT level (`r14_glb_global_mp` for the arena)
and is deliberately left alone -- a clone keeps its parent.

Every patch is a same-width u64 overwrite, so no file changes length.
"""
from __future__ import annotations

import argparse
import json
import shutil
import struct
import sys
from pathlib import Path

DEFAULT_EXTRACT = Path(r"H:\pcvr-extracted")
DEFAULT_OUT = Path(r"C:\Oculus\Games\Software\Software\ready-at-dawn-echo-arena"
                   r"\bin\win10\Tools\Tools\Settings\input-pcvr")

#: type hash -> the byte offsets of that file's own level-name field. `None`
#: means "structured": the patcher walks the file instead of using fixed offsets.
ARCHIVE_TYPE = "2a41cf1c1d9e5d32"          # CArchiveResourceWin10
COMPONENT_SPACE_TYPE = "6901365c8bb8bf50"  # CComponentSpaceResourceWin10
FIXED_SELF_REF = {
    "e8e38d7781a338a6": (104,),            # CGameLevelResourceWin10
    "85d28ce20f6a83f8": (88,),             # COccluderMeshCRWin10
}


#: RAD Engine CRC-64, polynomial 0x95AC9329AC4BC9B5, case-insensitive. Inlined
#: rather than imported from the map editor so this runs anywhere -- including
#: inside Blender, where that package is not on the path. Verified against the
#: shipped names: mpl_arena_a -> 576ed3f8428ebc4b, cgsceneresourceWin10 ->
#: a388ea69e5108f4c.
_POLY = 0x95AC9329AC4BC9B5
_MASK64 = 0xFFFFFFFFFFFFFFFF


def _crc_table():
    table = []
    for i in range(256):
        r = 0
        for bit in range(7, -1, -1):
            if (i >> bit) & 1:
                r ^= _POLY
            r = (r << 1) & _MASK64
        table.append(r)
    return table


_TABLE = _crc_table()


def rad_hash(name: str) -> str:
    """`%016x` of the engine's CSymbol64 for an authored name."""
    h = _MASK64
    for ch in name:
        c = ord(ch)
        if 0x41 <= c <= 0x5A:
            c += 0x20
        if c >= 0x80:
            c |= ~0xFF & _MASK64
        h = (c ^ _TABLE[(h >> 56) & 0xFF]) ^ ((h << 8) & _MASK64)
    return "%016x" % (h & _MASK64)


def resolve(name_or_hash: str) -> str:
    """A level's 16-hex key, whether given as a name or already as the key."""
    s = name_or_hash.strip()
    if len(s) == 16 and all(c in "0123456789abcdefABCDEF" for c in s):
        return s.lower()
    return rad_hash(s)


def patch_archive(blob: bytes, src: int, dst: int) -> tuple[bytes, int]:
    """Repoint the manifest entries that name the level. Structured, not a scan.

    `[u32 pad][u32 count]` then `count` x `(u64 type, u64 name)`; only the
    `name` half of an entry is ever touched, so a type hash that happened to
    equal the level hash could not be corrupted.
    """
    buf = bytearray(blob)
    count = struct.unpack_from("<I", buf, 4)[0]
    hit = 0
    for i in range(count):
        off = 8 + 16 * i + 8
        if struct.unpack_from("<Q", buf, off)[0] == src:
            struct.pack_into("<Q", buf, off, dst)
            hit += 1
    return bytes(buf), hit


def patch_component_space(blob: bytes, src: int, dst: int) -> tuple[bytes, int]:
    """`+0` owner plus the level half of every `(component, level)` row.

    64-byte header with the row count at `+48`, then 16-byte rows. Only the
    second u64 of a row moves, so a component hash is never rewritten.
    """
    buf = bytearray(blob)
    hit = 0
    if struct.unpack_from("<Q", buf, 0)[0] == src:
        struct.pack_into("<Q", buf, 0, dst)
        hit += 1
    count = struct.unpack_from("<I", buf, 48)[0]
    for i in range(count):
        off = 64 + 16 * i + 8
        if struct.unpack_from("<Q", buf, off)[0] == src:
            struct.pack_into("<Q", buf, off, dst)
            hit += 1
    return bytes(buf), hit


def patch_fixed(blob: bytes, offsets, src: int, dst: int) -> tuple[bytes, int]:
    buf = bytearray(blob)
    hit = 0
    for off in offsets:
        if struct.unpack_from("<Q", buf, off)[0] == src:
            struct.pack_into("<Q", buf, off, dst)
            hit += 1
    return bytes(buf), hit


def clone(src_name: str, dst_name: str, root: Path, out: Path,
          verbose: bool = True) -> dict:
    src_hex = resolve(src_name)
    dst_hex = resolve(dst_name)
    if src_hex == dst_hex:
        raise SystemExit("source and destination hash to the same name")
    src_u64 = int(src_hex, 16)
    dst_u64 = int(dst_hex, 16)
    src_le = bytes.fromhex(src_hex)[::-1]

    files, patched, total = [], {}, 0
    for tdir in sorted(root.iterdir()):
        if not tdir.is_dir():
            continue
        srcf = tdir / src_hex
        if not srcf.is_file():
            continue
        blob = srcf.read_bytes()
        before = blob.count(src_le)
        if tdir.name == ARCHIVE_TYPE:
            blob, hit = patch_archive(blob, src_u64, dst_u64)
        elif tdir.name == COMPONENT_SPACE_TYPE:
            blob, hit = patch_component_space(blob, src_u64, dst_u64)
        elif tdir.name in FIXED_SELF_REF:
            blob, hit = patch_fixed(blob, FIXED_SELF_REF[tdir.name], src_u64, dst_u64)
        else:
            hit = 0
        # every self-reference in this file must have been claimed by a
        # structured patch -- an unpatched one would leave the clone pointing
        # at the stock level, so it is an error rather than a warning.
        left = blob.count(src_le)
        if left:
            raise SystemExit("%s/%s: %d self-reference(s) not patched "
                             "(matched %d of %d)" % (tdir.name, src_hex, left, hit, before))
        if len(blob) != srcf.stat().st_size:
            raise SystemExit("%s: patch changed the file length" % tdir.name)
        dstd = out / tdir.name
        dstd.mkdir(parents=True, exist_ok=True)
        (dstd / dst_hex).write_bytes(blob)
        files.append(tdir.name)
        total += len(blob)
        if hit:
            patched[tdir.name] = hit
    if verbose:
        print("  %s  %s" % (src_name, src_hex))
        print("  %s  %s" % (dst_name, dst_hex))
        print("  files written : %d   (%.1f MB)" % (len(files), total / 1e6))
        print("  patched       : %d file(s)" % len(patched))
        for k, v in sorted(patched.items(), key=lambda kv: -kv[1]):
            print("      %s  %d self-reference(s)" % (k, v))
        print("  -> %s" % out)
    return {"src": src_hex, "dst": dst_hex, "files": files,
            "patched": patched, "bytes": total, "out": str(out)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source")
    ap.add_argument("dest")
    ap.add_argument("--dir", default=str(DEFAULT_EXTRACT))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    a = ap.parse_args(argv)
    clone(a.source, a.dest, Path(a.dir), Path(a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
