"""`CGameLevelResourceWin7` — the level-to-level edge that makes a level whole.

A Lone Echo level archive is NOT self-contained, and the missing half is not a
decode gap. On the reference archive `0703fd2acd5803e9` (`stn_int_itc_bridge`):

  * all **54** of its `CGameLevelResourceWin7`-sibling `CGStaticInstanceResourceWin7`
    resources are **148-byte EMPTY placeholders** with 16-byte GPU stubs — the
    archive ships **zero** static instances, so there is no scatter to decode;
  * its **self-named** `CGMeshListResourceWin7` is a **48-byte stub** (16-byte GPU
    stub), i.e. the render root's own mesh-list is empty;
  * the room SHELL — hull, floor, ceiling, hatch rings — is in the **PARENT**
    level `0703f239d74801fe` (`stn_int_itc_master`), whose self-named static
    master is **74,081 B** over a **56,885,760 B** GPU blob (95 meshes, 371
    instances), and whose instances land in the SAME world space as the child's
    `scene.json` placements with no extra offset.

`stream-confirmed` on three archives (see `PARENT_LEVEL_OFF` below).

------------------------------------------------------------------------------
THE FIELD
------------------------------------------------------------------------------
`SGameLevelData` — the engine's own type, reached from
`CGameLevelResource` — declares
`{componentspace, radsources, parent, volumes, enterportals, exitportals,
patchlist}`. On disk the blob is 320–328 B and carries exactly two `CSymbol64`
values that resolve to known names:

    +0x00  a level symbol that is NEVER this archive        -> `parent_level`
    +0x50  this archive's own hash                          -> `component_space`

`+0x50` is the archive-self-named `CComponentSpaceResourceWin7`, which exists in
both decoded archives, so `+0x50` is `componentspace` and `+0x00` is therefore the
other symbol member, `parent`. Measured:

| archive | +0x00 | +0x50 |
|---|---|---|
| `0703fd2acd5803e9` `stn_int_itc_bridge` | `0703f239d74801fe` `stn_int_itc_master` | self |
| `0703f239d74801fe` `stn_int_itc_master` | `956a00b1a4b3c37e` `stn_int_itc_liv`    | self |
| `4c47d84c1e52447a` `min_itc_master`     | `6113bd53bd411194` `r14_glb_global`     | self |

★ The third row is the control: a level with its OWN populated static master
parents to the **global** archive, exactly as Echo VR's standalone maps parent to
`r14_glb_global_mp`. Work on the LATER engine generation independently puts the
same upward parent-gamespace edge at `CGameLevelResource +0x10`. Same edge,
different offset — so this is a *corroborated* reading, never an imported one.

⛔ **This is the UPWARD edge only.** It does not enumerate children. Echo VR's
child list is compiled script code, not data; nothing here claims LE1 stores
one either. Walking DOWN the tree means scanning
level roots for a matching `parent_level`, which is a corpus job, not this module.

------------------------------------------------------------------------------
A MISS MUST NOT LOOK LIKE A VALUE
------------------------------------------------------------------------------
`parent_level` / `component_space` are `None` **only** when the slot holds the
explicit `NULL_SYMBOL` sentinel. A blob too short to hold the field raises
`ValueError` — it never silently reports "no parent". `decode_game_level` is
archive-free and pure stdlib so it is unit-tested on synthetic bytes.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: The on-disk category half of the `(category, name)` archive key.
GAME_LEVEL_TYPE = "CGameLevelResourceWin7"
COMPONENT_SPACE_TYPE = "CComponentSpaceResourceWin7"

#: `SGameLevelData::parent` — the level this level hangs under. `stream-confirmed`
#: on `0703fd2acd5803e9`, `0703f239d74801fe`, `4c47d84c1e52447a` (see the header).
PARENT_LEVEL_OFF = 0x00
#: `SGameLevelData::componentspace` — always this archive's own hash on all three.
COMPONENT_SPACE_OFF = 0x50
#: `CSymbol64` null. A slot holding this is an ABSENT link, not a hash.
NULL_SYMBOL = 0xFFFFFFFFFFFFFFFF
#: Smallest blob that can carry both fields.
MIN_BLOB_BYTES = COMPONENT_SPACE_OFF + 8

LINK_FORMAT = "lelevellink"
LINK_VERSION = 1


def _sym(blob: bytes, off: int):
    """`CSymbol64` at `off` as a 16-hex string, or `None` for the null sentinel."""
    v = struct.unpack_from("<Q", blob, off)[0]
    return None if v == NULL_SYMBOL else f"{v:016x}"


@dataclass
class LevelLink:
    """The decoded level-to-level edge of one `CGameLevelResourceWin7`."""

    archive: str | None
    parent_level: str | None          # None ONLY for the NULL_SYMBOL sentinel
    component_space: str | None       # ditto
    blob_size: int
    warnings: list = field(default_factory=list)

    @property
    def has_parent(self) -> bool:
        """True when a parent hash is actually recorded (not the null sentinel)."""
        return self.parent_level is not None

    @property
    def is_self_parented(self) -> bool:
        """A level naming ITSELF as parent — never seen; treated as suspect."""
        return bool(self.archive) and self.parent_level == self.archive

    def to_dict(self) -> dict:
        return {
            "format": LINK_FORMAT,
            "version": LINK_VERSION,
            "archive": self.archive,
            "parent_level": self.parent_level,
            "component_space": self.component_space,
            "blob_size": self.blob_size,
            "warnings": list(self.warnings),
        }


def decode_game_level(blob: bytes, archive: str | None = None) -> LevelLink:
    """Decode one `CGameLevelResourceWin7` primary blob.

    Raises `ValueError` when the blob cannot hold the fields — a short read is a
    decode FAILURE and must never be reported as "this level has no parent".
    """
    n = len(blob)
    if n < MIN_BLOB_BYTES:
        raise ValueError(
            f"{GAME_LEVEL_TYPE} blob is {n} B, needs >= {MIN_BLOB_BYTES} B to hold "
            f"parent@{PARENT_LEVEL_OFF:#x} and componentspace@{COMPONENT_SPACE_OFF:#x}")

    parent = _sym(blob, PARENT_LEVEL_OFF)
    cspace = _sym(blob, COMPONENT_SPACE_OFF)
    warnings: list[str] = []

    key = (archive or "").lower() or None
    if key and cspace and cspace != key:
        # Every decoded level self-names its component space. A level that does not
        # is not necessarily wrong, but the offsets were derived from that identity,
        # so say so instead of quietly returning a different meaning.
        warnings.append(
            f"componentspace@{COMPONENT_SPACE_OFF:#x} is {cspace}, not the archive "
            f"hash {key} -- the field identity these offsets rest on does not hold here")
    if key and parent == key:
        warnings.append(f"parent@{PARENT_LEVEL_OFF:#x} names this archive itself")

    return LevelLink(archive=key, parent_level=parent, component_space=cspace,
                     blob_size=n, warnings=warnings)


# ---------------------------------------------------------------------------
# Archive front-end (needs Oodle + the game files; Windows Python)
# ---------------------------------------------------------------------------

def load_game_level_blob(archive: str, hash_lookup: Path | None = None):
    """Pull the archive's self-named `CGameLevelResourceWin7` primary blob.

    Returns `(name_hash_hex, blob)`, or `(None, None)` when the archive holds no
    game-level root at all (most archives are content, not levels). Import is
    deferred so this module stays pure-stdlib for callers that only decode bytes.
    """
    root = Path(__file__).resolve().parents[2]
    for p in (str(root / "scripts"),):
        if p not in sys.path:
            sys.path.insert(0, p)
    from le_oodle import chunk_table, decompress_range           # noqa: PLC0415
    from le_archive_decode import (                    # noqa: PLC0415
        ARCHIVE_PRIMARY, parse_header, entry_at, load_hash_lookup,
    )

    names = load_hash_lookup(Path(hash_lookup) if hash_lookup
                             else root / "hash_lookup.json")
    raw = (ARCHIVE_PRIMARY / archive).read_bytes()
    uncomp_total, _ = chunk_table(raw)
    prelude = decompress_range(raw, 0, 64)
    primary_size = struct.unpack_from("<Q", prelude, 0)[0]
    extra_skip = struct.unpack_from("<Q", prelude, 24)[0]
    data_off = 32 + extra_skip
    tail = decompress_range(raw, data_off + primary_size, uncomp_total)

    h0 = parse_header(tail, 0)
    hit = None
    for i in range(h0.contents.count):
        th, nh, val = struct.unpack_from("<QQQ", tail, h0.contents.off + i * 24)
        if names.get(th) != GAME_LEVEL_TYPE or val >= h0.entries.count:
            continue
        hit = (f"{nh:016x}", entry_at(tail, h0, val))
        break
    del tail
    if hit is None:
        del raw
        return None, None
    name_hex, (pos, size) = hit
    blob = decompress_range(raw, data_off + pos, data_off + pos + size)
    del raw
    return name_hex, blob


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", required=True, help="archive hash, e.g. 0703fd2acd5803e9")
    ap.add_argument("--hash-lookup", default=None)
    ap.add_argument("--out", default=None, help="write a level_link.json here")
    args = ap.parse_args(argv)

    name_hex, blob = load_game_level_blob(args.archive, args.hash_lookup)
    if blob is None:
        print(f"archive {args.archive}: no {GAME_LEVEL_TYPE} -- not a level archive")
        return 1
    link = decode_game_level(blob, args.archive)
    print(f"archive {args.archive}  {GAME_LEVEL_TYPE} {name_hex}  {link.blob_size} B")
    print(f"  parent_level    {link.parent_level or '(none - null sentinel)'}")
    print(f"  component_space {link.component_space or '(none - null sentinel)'}")
    for w in link.warnings:
        print(f"  WARN: {w}")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(link.to_dict(), indent=1), encoding="utf-8")
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
