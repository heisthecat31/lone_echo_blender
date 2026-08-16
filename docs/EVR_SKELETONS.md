# `CSkeletonResourceWin10` — armatures (in progress)

The armature the user wants is a **separate resource** from what the mesh
decoder reports as "bones": that is the per-vertex skin weight/index pair riding
in the vertex stream (the weight paint). The hierarchy and rest pose live here.

    CSkeletonResourceWin10   46adff5980245670
      Echo VR       135 skeletons
      Lone Echo 2   160 skeletons

Neither game's extraction touches them today.

## File layout

Leading block of `CTable`-shaped headers (`mark == 32`, `size == count * stride`)
at 0x040, 0x080, 0x0b8, 0x160, 0x208, 0x358, 0x488, 0x4c8, 0x508, 0x540. Sample
`58173136d23722e4` (9456 B, **66 bones**):

    hdr     count  stride  bytes   what
    0x040       4     120     480
    0x080       6       4      24
    0x0b8      66      32    2112   BIND POSE          <- decoded
    0x160      66      24    1584   hierarchy + names  <- partly decoded
    0x208       6      24     144
    0x358      66      16    1056   CMap<name -> index>
    0x488       6      16      96
    0x4c8       3      16      48
    0x508       3       8      24
    0x540       6       4      24

The data region does **not** start at `filesize - sum(sizes)`. It starts at
**0x6b8** here, confirmed because the bind-pose table then lands exactly at the
0x8b0 its header declares. Locate the arrays from a known-good anchor rather
than by assuming the tail packs flush.

## Bind pose — DECODED, validated

`0x0b8`, 66 records of 32 bytes:

    +0x00   4x f32   rotation quaternion (x, y, z, w)
    +0x10   3x f32   translation
    +0x1c   1x f32   uniform scale

Validation: a scan for the longest run of unit-length quaternions at any stride
and sub-offset returns **stride 32, sub-offset 0**, run length 67 (66 records
plus one coincidental match past the end) — no other candidate comes close. Every
`+0x1c` reads 1.0 (`0x3F800000`). Rotations are small angles about X with many
identities; translations are metre-scale offsets (e.g. `(0, 1.1442, -0.0389)`).

## Hierarchy + names — LOCATED, field roles unresolved

`0x160`, 66 records of 24 bytes. Established:

* It carries **66 distinct u64 name hashes**. Bone names are almost certainly
  CSymbol64 preimages, so the level-name mining approach
  (`scripts/evr_level_names.py`) should crack many of them.
* Adjacent records share the first half of a name hash while differing in the
  second — the signature of an `_L` / `_R` bone pair.
* Two u32 columns hold small in-range integers with `0xFFFFFFFF` sentinels, and
  they pair up as a tree: `(0,5) (0,7) (3,8) (3,9) (4,10) (5,13) (6,14)` — one
  column is the parent, the other a child or sibling link.

NOT yet pinned: which offset within the 24 bytes is the name, the parent, and
the index. Several field assignments were tried; none produced a column that is
simultaneously sequential 0..65 and a valid single-rooted tree, so the record is
not a plain `{name, parent, index}` triple and should not be guessed at. Read a
skeleton whose bone names are known (a character rig) and anchor on the name
hash first.

`0x358`'s 16-byte records read as `{u64 name_hash, u64 index}` — rows like
`(9829530555516637595, 52)` — i.e. a name->index map that will cross-check
whatever the 24-byte table turns out to say.

## Next steps

1. Crack bone names: hash candidate strings from the game binaries and the
   engine authoring tree against the 66 name hashes. Named bones make every
   remaining field obvious by inspection.
2. Pin the parent column against a known rig (spine -> chest -> shoulder -> arm).
3. Join skeleton -> model, then feed the existing `le_mesh/skinning.py` with the
   armature plus the skin weights the mesh decoder already reads.
