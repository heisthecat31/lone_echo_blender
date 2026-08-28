# `CSkeletonResourceWin10` — armatures

The armature the user wants is a **separate resource** from what the mesh
decoder reports as "bones": that is the per-vertex skin weight/index pair riding
in the vertex stream (the weight paint). The hierarchy and rest pose live here.

    CSkeletonResourceWin10   46adff5980245670
      Echo VR       135 skeletons
      Lone Echo 2   160 skeletons

⚠ **This document was stale.** It described the hierarchy as unresolved long
after `scripts/evr_apply_skeleton.py` decoded it and started building the
importer's armature from it. The sections below are corrected to match that
module, which is the decoder — there is no second reader for this resource.

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
plus one coincidental match past the end). Every `+0x1c` reads 1.0
(`0x3F800000`).

⛔ That scan is NOT how the table is located, despite reading well on this
sample. A skeleton file holds more than one 32-byte pose table — the second is a
shared non-bind pose — and "the longest run" picks the wrong one on most files
(64 of 109 when tried), and can start mid-table. `locate_tables` anchors on the
hierarchy instead; see the phase note below for the one thing that goes wrong
with that. Rotations are small angles about X with many
identities; translations are metre-scale offsets (e.g. `(0, 1.1442, -0.0389)`).

## Hierarchy + names — DECODED

66 records of 24 bytes, as `evr_apply_skeleton` reads them:

    +0x00   u32   the PREVIOUS row's trailing column (see the phase note below)
    +0x04   u64   bone name hash (CSymbol64, unaligned in this framing)
    +0x0c   u32   PARENT        index, 0xFFFFFFFF = none
    +0x10   u32   FIRST CHILD   index, 0xFFFFFFFF = none
    +0x14   u32   NEXT SIBLING  index, 0xFFFFFFFF = none

It is a **first-child / next-sibling** tree, which is why the earlier search for
a child-count or a sequential index column found nothing, and why no single
column looked like "the tree".

`find_hierarchy` locates it by a check that cannot pass by accident: walking
`first_child` then `next_sibling` must reconstruct the `parent` column exactly.
An earlier search missed the table by requiring a SINGLE root — these rigs have
several (a character root plus helper roots; `de5882fe4cf82580` has three) — so
the predicate rejected the real answer.

Independently re-derived on `de5882fe4cf82580`, `mpl_combat_dyson`'s four-headed
fire fixture: a 20-bone rig small enough to read by eye. Its name column agrees
with `bone_names` on **109 of 109** skeletons that decode.

## ⚠ The four-byte phase

The record above is framed four bytes EARLY: what it calls `+0x00 ordering` is
the previous row's trailing column, and the true record starts at the name. Both
framings read the tree identically — every link column lands in the same place —
so nothing in the hierarchy notices.

The **bind pose** does notice, because `locate_tables` derives its offset by
subtracting declared table sizes from the hierarchy offset and inherits the same
phase. The window then straddles records: quaternions still read as unit (they
are mostly identities), but the scale column stops reading 1.0.

Measured over the corpus: **88 files land clean, 20 need +4.** On
`de5882fe4cf82580` the unsnapped window gives scale 0.0 and translation
`(1, 0, 0)` for a bone whose real rest is the origin; `58173136d23722e4`, the
66-bone rig this document was written against, is wrong too. `_snap_bind` now
corrects the phase by requiring a clean window — all quaternions unit AND every
scale exactly 1.0 — and leaves the derived offset alone when no phase gives one
(five files, four of them single-bone).

`0x358`'s 16-byte records read as `{u64 name_hash, u64 index}` — rows like
`(9829530555516637595, 52)` — a name->index map that cross-checks the above.

## Bone names

CSymbol64 preimages, kept in `data/bone_names.json` (132 names, 71% of slots).
The hash is **invertible**: given the state after a byte, the state before it is
recoverable, so names can be recovered EXACTLY by a meet-in-the-middle preimage
search rather than guessed from a word list — practical for about 8 free
characters either side of a known prefix.

Recovered that way for the fire fixture: `EXP_C1_Doors1` and all eight
`EXP_{C1,L1,R1}_Eye{Left,Right}{1,2}`, joining the `EXP_C1_Head1/2`,
`EXP_L1_Head1` and `EXP_R1_Head1` already in the table — 16 of its 20 bones. The
same walk named three of that model's four animations in `CAnimSetResource`:
`open`, `close` and `closed_reset`.

## Next steps

1. Name the remaining bones. The preimage walk covers about 8 free characters;
   longer names need a known prefix, and the `EXP_<C1|L1|R1>_<Part><N>` scheme
   gives one.
2. `CAnimSetResource`'s channel region — lossy-compressed fitted curves — is all
   that stands between a rest-pose armature and actual animation. `evr_animset`
   names the animations and locates their channels; nothing reads them, so a
   rigged mover is imported tagged and unposed.
