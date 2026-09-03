# The 27 compound records — what they are, and five models ruled out

These types pass the size arithmetic (`data_size % count == 0`) but fail the
entry test: `+0` is not a constant component-system node hash and `+8` does not
resolve to an actor. Calling them "pooled records" was a **false positive**, and
this file is the attempt to decode them properly.

## What IS established

**The leading section is a genuine flat record.** Walking `CTagCR` as a
CR-style header confirms it:

| Level | File | data_size | count | stride |
|---|---|---|---|---|
| `mpl_combat_fission` | 424 | 24 | 1 | 24 |
| `mpl_lobby_b2` | 608 | 48 | 2 | 24 |
| `r14_glb_global_mp` | 1344 | 144 | 6 | 24 |

`data_size == count * 24` in every level, so the 56-byte envelope and the
24-byte entry array are real. **What follows the entry array is what is not
decoded.**

The entries are not `(node, actor)`. In `r14_glb_global_mp` they read:

    entry 0: (0, 8, 0)          entry 3: (0x150, 0, 0x100000000)
    entry 1: (0x100000000, 0, 1) entry 4: (0, 6, 6)
    entry 2: (1, 6, 0)          entry 5: (0, 0x210, 0)

Cloning such a file whole is why the engine reports
`Missing actor with nodeid 0x0000000000000000` — it reads `+8` of entry 0 as an
actor and gets 8.

## What they actually are: PARALLEL ARRAYS

The tail is not one pool. It is **several arrays, each with `count` elements of a
fixed size, each introduced by its own 56-byte header.**

Found by scanning for the header signature (`+0 == 0`, `+16 == 0`,
sentinel `+28 == 1`, `+40 == +48`) and keeping only sections whose size is
**exactly `K * count`** — in *every* level that ships the type. Since these types
appear with counts of 1, 2, 6 and more across levels, a size that tracks the
count exactly everywhere is structure, not coincidence.

**All 27 have such sections, and every one of them includes K = 24** — the entry
array the top-level header already declares. That is the control: the method
re-finds the array we independently know is there.

`CTagCR` worked through by hand agrees exactly. Its sections are 24, 56 and 88
bytes per element:

| Level | count | 24 x n | 56 x n | 88 x n |
|---|---|---|---|---|
| `mpl_combat_fission` | 1 | 24 | 56 | 88 |
| `mpl_lobby_b2` | 2 | 48 | 112 | 176 |
| `r14_glb_global_mp` | 6 | 144 | 336 | 528 |

And the 88-byte array is where the meaning lives: in fission it contains
`0x766573BEEF66C064` — **the payload actor** — next to `0xC8E8D0B1A884E3ED`, one
of the component-system hashes fission declares. So the record binds
(component, actor) pairs; the 24-byte "entries" are an index, not the data.

| Type | Entry stride | Element sizes found (K per count) |
|---|---|---|
%s

## The record format IS decoded

`CTagCR` in fission ends with a region that repeats with a period of **96 bytes**
(2 x 96 = 192, starting at offset 232). One period decodes cleanly, and it
contains an inline 56-byte header:

| Offset in record | Size | Content |
|---|---|---|
| `+0` | u64 | component-system hash — `0xC8E8D0B1A884E3ED` |
| `+8` | u64 | **the actor** — `0x766573BEEF66C064`, fission's payload |
| `+16` | u64 | `0x00000000FFFFFFFF` |
| `+24` | u64 | 0 |
| `+32` | 56 | a standard CR header: `+0`=0, `+8`=**8** (size), `+16`=0, sentinel `+28`=1, counts 1/1 |
| `+88` | 8 | that header's data — `0x2FC78E86527B568E` (the tag value) |

`32 + 56 + 8 = 96`, exactly the measured period. So the record is a 32-byte
prefix binding *(component, actor)* followed by an **inline** `[header][data]`
section — the same envelope used everywhere else, nested one level down.

This also explains the `(component, actor)` pairing seen in the 88-byte array:
that array is these records without the trailing 8-byte payload.

## What is still open

The **record format is decoded** and the **element sizes** are established. The
**byte-exact tiling of the whole file is not**. Summing
headers plus arrays overshoots the file for `CTagCR`, so either some detected
headers are false positives of a deliberately loose signature, or sections
overlap in a way this model does not capture.

An exhaustive grammar search settles what the file is **not**. Walking it as a
sequence where each step either consumes a 56-byte header (pushing its size) or
consumes a pending size as data, explored with backtracking over every choice:

| Signature | Order | Result |
|---|---|---|
| strict | FIFO | no parse |
| strict | LIFO | no parse |
| relaxed | FIFO | no parse |
| relaxed | LIFO | no parse |

Zero valid parses on both `mpl_combat_fission` and `mpl_lobby_b2`. The file is
therefore **not** a flat sequence of header/data pairs under any queue
discipline — the records carry untagged prefix bytes (the 32 bytes above), which
no such grammar can express. The periodic suffix is only periodic in fission,
where both records happen to tag the same actor; in `lobby_b2` and
`r14_glb_global_mp` the records differ, so there is no repeating suffix to find. Element sizes listed for types
present in only one level should be treated as weaker — with a single count
there is nothing for the proportionality to be tested against.

## Five models tested and refuted

Each was run against all 39 unsolved types (or all 27 where noted), and each
failed on evidence rather than on judgement.

**1. Inline-table headers.** Look for offsets where `+0` and `+16` are zero in
every entry (the shape that solves `CScriptCR`, `CListCR` and 14 others), then
require the sizes to sum to the tail. **Zero candidate offsets** in the stride-24
family — a 24-byte entry cannot hold a 56-byte header.

**2. Direct pool addressing.** For stride 24 the only free field is `+16`, so
test `sum u64@16`, `sum u32@16`, `sum u32@20`, and those scaled by 4, 8 and 16
against the tail length. **0 of 24 match.**

**3. Nested block sequence.** Walk the tail as repeated
`[56-byte header][data_size bytes]` and require it to land exactly on the end.
**0 of 39 tile.**

**4. Fixed per-entry payload.** Fit `tail = base + b * count` with a robust
estimator (choose `b` minimising the number of distinct per-file bases). Nine
types fit *arithmetically* — `CTagCR` gives `b = 160`, `base` in {168, 184, 200},
exact across counts 1, 2 and 6.

**But it is refuted structurally.** Slicing `r14_glb_global_mp` into
`184 + 6 x 160` and dumping each block shows the boundaries landing mid-value —
blocks begin `0000000000000000 0000000000000008`, then
`0000000000000001 0000000000000000`, then `b113997837c29003 00000000000fffff`.
A real per-entry record would repeat its shape. The arithmetic fit is a
coincidence of these particular counts, which is exactly why it was checked
against the bytes instead of being believed.

**5. Concatenated sections.** Walk the whole file as back-to-back CR sections,
each with its own 56-byte header. Section 0 always parses (it is the real entry
array), and the walk then **stops immediately**: there is no header at offset 80
in fission, 104 in lobby_b2, or 200 in global_mp.

## Where that leaves it

The trailing region is 8-byte aligned (the gcd of per-level tails is 8 for most
of these types), is not a fixed block size, is not proportional to the entry
count, and is not introduced by a header. It therefore needs a length or offset
field that is not in any of the positions tested — most likely inside the
24-byte entries, whose three u64s look like small sizes and counts
(`8`, `6`, `0x210`, `0x150`) rather than hashes.

That was the state before the parallel-array model above, which supersedes it:
the sections and their element sizes are now measured. What remains open is the
exact tiling, not the shape.

## The list

The 27, with the level count each was measured across:

* `CAnimationCRWin10` — stride 24, 17 level(s)
* `CCharacterAnimationCRWin10` — stride 24, 5 level(s)
* `CCheckpointCRWin10` — stride 24, 6 level(s)
* `CEventCRWin10` — stride 24, 26 level(s)
* `CGReflectionProbeResourceWin10` — stride 56, 15 level(s)
* `CJsonConfigCRWin10` — stride 24, 3 level(s)
* `CMaterialTypesBVHResourceWin10` — stride 16, 25 level(s)
* `CModelLODCRWin10` — stride 24, 1 level(s)
* `CModelSwapperCRWin10` — stride 24, 2 level(s)
* `CPlatformCRWin10` — stride 24, 6 level(s)
* `CPluginCRWin10` — stride 24, 2 level(s)
* `CPositionSelectionCRWin10` — stride 24, 1 level(s)
* `CR15KillVolCRWin10` — stride 24, 2 level(s)
* `CR15NetBitFieldCRWin10` — stride 24, 5 level(s)
* `CR15NetPlayerModelSwapperCRWin10` — stride 24, 2 level(s)
* `CR15NetUISettingsCRWin10` — stride 24, 2 level(s)
* `CR15PlatformCRWin10` — stride 24, 4 level(s)
* `CR15PropInputCRWin10` — stride 24, 1 level(s)
* `CR15SyncGrabCRWin10` — stride 24, 14 level(s)
* `CR15TagCRWin10` — stride 24, 2 level(s)
* `CR15TeamCRWin10` — stride 24, 8 level(s)
* `CR15UISettingsCRWin10` — stride 24, 2 level(s)
* `CSVOResourceWin10` — stride 2032, 1 level(s)
* `CSettingsTranslatorCRWin10` — stride 24, 2 level(s)
* `CSettingsUICRWin10` — stride 24, 2 level(s)
* `CTagCRWin10` — stride 24, 12 level(s)
* `CTeamCRWin10` — stride 24, 7 level(s)
