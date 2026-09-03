# Pooled component records — inline table layouts

A pooled CR is:

    56-byte header | count x stride entries | ordered pool

Each entry carries inline table headers; a header's byte size is at `+8`. There
is **no offset field** — `+0` and `+16` of every header are zero, so the pool is
a plain ordered stream consumed entry by entry, table by table.

## How the headers were found

Searching for "a set of offsets whose sizes sum to the pool" is **ambiguous**: a
header that is zero in every entry can be added or dropped without changing the
sum, so many offset sets satisfy it. (An earlier pass did exactly this and
produced two different answers for the same type.)

So headers are identified by **shape** first — every offset where `+0` and `+16`
are zero in every entry of every level — and the sum is then used as the
*check*:

    sum over entries, over headers, of size  ==  pool length   (exactly)

That makes the answer canonical. It reproduces the layouts verified by hand:
`CR15NetBalanceSettingsCR` one table at `+32`, `CListCR` from `+48`,
`CScriptCR` from `+48` on a 56-byte pitch.

**16 of 55 pooled types solved.** "Live" tables are those with a non-zero size
somewhere; the remaining candidate offsets are headers that are always empty.

| Type | Stride | Live tables | Header offsets |
|---|---|---|---|
| `CActorJugglerCRWin10` | 144 | 2 | +32, +88 |
| `CBlackboardCRWin10` | 592 | 6 | +32, +88, +144, +200, +480, +536 |
| `CComponentLODZoneCRWin10` | 144 | 2 | +32, +88 |
| `CComponentRegionLODCRWin10` | 88 | 1 | +32 |
| `CLODRegionCRWin10` | 200 | 3 | +32, +88, +144 |
| `CListCRWin10` | 440 | 3 | +48, +104, +384 |
| `COccluderMeshResourceWin10` | 136 | 2 | +0, +56 |
| `CParticleEffectCRWin10` | 152 | 1 | +96 |
| `CR15DialoguePOICRWin10` | 464 | 4 | +176, +232, +296, +352 |
| `CR15NetBalanceSettingsCRWin10` | 88 | 1 | +32 |
| `CR15NetCaptureVolumeCRWin10` | 96 | 1 | +32 |
| `CR15NetCustomizationCRWin10` | 128 | 1 | +72 |
| `CR15NetGhostLODCRWin10` | 88 | 1 | +32 |
| `CR15NetRewardItemCRWin10` | 664 | 2 | +432, +488 |
| `CR15UIPage2ElementCRWin10` | 96 | 1 | +32 |
| `CScriptCRWin10` | 720 | 9 | +48, +160, +272, +328, +384, +440, +496, +552, +608 |

## The other 39 — what they are not

Two hypotheses were tested against every one of them and **both refuted**:

* **a different header pitch** — searching (start, step, count) over pitches
  8..128 for a set of size fields summing to the tail. Rejected: it "solves"
  types ambiguously, because a header that is zero everywhere can be added or
  dropped without changing the sum. It gave two different answers for
  `CR15NetBalanceSettingsCR`, which is how the ambiguity was caught.
* **a nested block sequence** — walking the tail as repeated
  `[56-byte header][data_size bytes]` records and requiring it to land exactly
  on the end. **0 of 39** tile that way.

Tail lengths are 8-byte aligned (gcd of the per-level tails is 8 for most) but
are not a fixed block size and are not proportional to the entry count, so the
length must come from a per-entry field that is not in the inline-table shape.

### Split by whether the ENTRIES are even the normal form

The entry test — `+0` constant within a file, `+8` resolving in that level's
`CActorDataResource` — separates these cleanly.

**11 have proper (node, actor) entries. Only the tail is undecoded.**

* `CLegacyCameraDataCRWin10` (stride 192, 23 level(s))
* `CR15MenuPlayerCRWin10` (stride 104, 2 level(s))
* `CR15NetAIWaypointCRWin10` (stride 96, 1 level(s))
* `CR15NetPunchCRWin10` (stride 96, 1 level(s))
* `CR15NetSpectatorCameraCRWin10` (stride 200, 6 level(s))
* `CR15NetVoipBroadcasterCRWin10` (stride 88, 3 level(s))
* `CR15PlayerNavCRWin10` (stride 208, 2 level(s))
* `CR15UILayoutCRWin10` (stride 96, 12 level(s))
* `CR15UIPage2CRWin10` (stride 328, 3 level(s))
* `CR15UIPageCRWin10` (stride 264, 11 level(s))
* `CRxAICRWin10` (stride 144, 1 level(s))

**1 references real actors but with a varying `+0`**, so it may carry more than
one component system in a single file:

* `CComponentLODCRWin10` (stride 96, 18 level(s))

**27 fail the entry test entirely — for these, "pooled" is a FALSE POSITIVE.**
`data_size % count == 0` held by coincidence, not because the file is an entry
array. Most are stride 24, and a dump of `CTagCR` shows why: its "entry" reads
`(0, 8, 0)` — no node hash, no actor — which is exactly why cloning that file
whole yields `Missing actor with nodeid 0x0000000000000000`. These are a
different container and should not be read as component records.

* `CAnimationCRWin10` (stride 24, 17 level(s))
* `CCharacterAnimationCRWin10` (stride 24, 5 level(s))
* `CCheckpointCRWin10` (stride 24, 6 level(s))
* `CEventCRWin10` (stride 24, 26 level(s))
* `CGReflectionProbeResourceWin10` (stride 56, 15 level(s))
* `CJsonConfigCRWin10` (stride 24, 3 level(s))
* `CMaterialTypesBVHResourceWin10` (stride 16, 25 level(s))
* `CModelLODCRWin10` (stride 24, 1 level(s))
* `CModelSwapperCRWin10` (stride 24, 2 level(s))
* `CPlatformCRWin10` (stride 24, 6 level(s))
* `CPluginCRWin10` (stride 24, 2 level(s))
* `CPositionSelectionCRWin10` (stride 24, 1 level(s))
* `CR15KillVolCRWin10` (stride 24, 2 level(s))
* `CR15NetBitFieldCRWin10` (stride 24, 5 level(s))
* `CR15NetPlayerModelSwapperCRWin10` (stride 24, 2 level(s))
* `CR15NetUISettingsCRWin10` (stride 24, 2 level(s))
* `CR15PlatformCRWin10` (stride 24, 4 level(s))
* `CR15PropInputCRWin10` (stride 24, 1 level(s))
* `CR15SyncGrabCRWin10` (stride 24, 14 level(s))
* `CR15TagCRWin10` (stride 24, 2 level(s))
* `CR15TeamCRWin10` (stride 24, 8 level(s))
* `CR15UISettingsCRWin10` (stride 24, 2 level(s))
* `CSVOResourceWin10` (stride 2032, 1 level(s))
* `CSettingsTranslatorCRWin10` (stride 24, 2 level(s))
* `CSettingsUICRWin10` (stride 24, 2 level(s))
* `CTagCRWin10` (stride 24, 12 level(s))
* `CTeamCRWin10` (stride 24, 7 level(s))
