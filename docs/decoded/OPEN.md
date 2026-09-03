# What still needs decoding

Measured from `_all.json` / `_pools.json`, not estimated. Ordered by how much is
actually unknown.

## A. Field meaning inside records — the big one

The container is fully decoded; **what the fields mean is mostly not.**

| | Bytes per entry (summed over 132 record types) |
|---|---|
| Explained by the envelope (`+0` node hash, `+8` actor) | 2,112 |
| **No established meaning** | **8,424 — 80%** |

Only **5 types** have any field proven beyond `+0`/`+8`
(`CR15NetGunCR` `+80`/`+104`, `CR15LinearPositionConstraintCR` `+64`/`+88`,
`CR15LinearConstraintTouchInteractCR` `+96`/`+120`,
`CR15RigidAttachConstraintCR` `+32`, `CSharedCanvasUICR` `+48`), plus the
hand-decoded gameplay fields in `../combat/GAMEPLAY.md`.

**Correlation against actor transforms is exhausted** — it was tried against
every offset of every type and yields only those 5 once degenerate zero-matches
are excluded. Getting further needs a different instrument:

* **Controlled input.** The map editor authors many of these record types. Build
  a level with *known* values (a trigger of known half-extents, a spawn of known
  id and band, a light of known radius), read the bytes back, and the field is
  proven because the input was chosen. This is how the spawn id at `+40` and the
  payload tuning floats were established, and it is the only method here that
  scales.
* **Differential builds.** Change exactly one authored value, rebuild, diff the
  bytes. Whatever moved is that value.

## B. Pooled layouts still unsolved — 12

A relaxed header search was tried and its results **rejected**: dropping the
`+0`/`+16`-zero requirement produces "solutions" whose offset sets are dense runs
of consecutive 8-byte positions, which is a fitted artifact rather than a header
layout — the same over-fitting that produced two different answers for
`CR15NetBalanceSettingsCR` earlier. These stay unsolved rather than be claimed.


Their entries are the normal `(node, actor)` form, so only the tail is unknown.
Headers were not found by the `+0`/`+16`-zero signature.

`CComponentLODCR` (96), `CLegacyCameraDataCR` (192), `CR15MenuPlayerCR` (104),
`CR15NetAIWaypointCR` (96), `CR15NetPunchCR` (96),
`CR15NetSpectatorCameraCR` (200), `CR15NetVoipBroadcasterCR` (88),
`CR15PlayerNavCR` (208), `CR15UILayoutCR` (96), `CR15UIPageCR` (264),
`CR15UIPage2CR` (328), `CRxAICR` (144).

## C. Compound records — file tiling — 27

The 96-byte record is decoded (see COMPOUND.md): a 32-byte
`(component, actor)` prefix, an inline 56-byte header, then its data. What is
open is how records and arrays tile the whole file. The header/data grammar is
**eliminated** under all four disciplines tested; the next model to try is a
parser that allows a fixed-size prefix before each nested header, which is
exactly what those 32 bytes are.

## D. Standalone resources — partly decoded now

See RESOURCES.md. `CGameLevelResource`'s parent-level field (`+16`),
`CPhysicsResource`'s leading zone block, and `CGFSEffectsResource`'s fixed
354-float table are decoded. What remains: `CGameLevelResource`'s ~80 KB body,
`CPhysicsResource`'s collision geometry, and `CGVisibilityResource` entirely.

(`CComponentSpaceResource` is sometimes grouped with these but is **decoded** —
64-byte header, count at `+48`, 16-byte rows. See
`../combat/COMPONENT_SPACE.md`.)

## E. Types absent from this extract — 15

Named by levels in the index but shipping in packages this extract does not
contain — mostly global asset resources (`CTTFontResource`,
`CWWiseSoundBankResource`, `CLensResource`, `CJsonResource`, and others; the
full list is at the end of ALL_TYPES.md). These need a wider extract before any
decoding is possible.

## F. Named specifics

* **The script resource 20-byte header signature.** Two per-script 64-bit values
  ahead of the embedded PE, unique across all 567 scripts, not `rad_hash` of the
  name, module name or payload. **This is the one that blocks real work** — it
  is why a script cannot be cloned under a new hash, and therefore why the
  war-room levels cannot be brought in by duplicating the StreamingScript.
* **`CR15TrackPointCR` `+24` flag bits.** `0x10` on the last point, `0x20`/`0x60`
  mid-route. Consistent with end/checkpoint markers but **inferred, not proven**.
* **`CR15TrackPointCR` `+32` symbol hash.** Set on 24 of fission's 69 points,
  unset on 45. Not actors, not track points; 20 share one value.
* **`CScriptCR` inline table semantics.** Twelve tables per entry; sizes and the
  pool arithmetic are exact, but what each table *holds* is unread.
* **`CGSceneResource` sections other than sec12.** Only the sub-level table and
  its position array are decoded.

## What would move the needle fastest

1. **Controlled-input experiments through the editor** — turns section A from
   80% unknown into measured fact, one authored field at a time.
2. **The script header signature** — unblocks carrying scripts, which is the
   current hard stop on the Pebbles port.
3. **The compound tiling** with a prefix-aware parser — 27 types, and the record
   format is already in hand.
