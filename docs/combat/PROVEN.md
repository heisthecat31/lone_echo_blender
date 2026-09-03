# What is proven, and by what test

Four independent tests were run against the shipped bytes. Each either passes
outright or is reported with its rate — nothing here rests on reading hex by eye.

## Test 1 — stride agreement across maps

`stride = data_size / count`, computed separately per level and required to
agree everywhere the type ships.

**Result: 61 of 61 flat CR types agree in every level.** A wrong stride would
diverge between a 12-entry map and a 700-entry one, so agreement across four
independently authored maps is the check.

## Test 2 — entry head resolves

`+0` must be constant within a file (the component-system node hash) and `+8`
must name an actor in that level's `CActorDataResource`.

**Result: 55 of 61 pass both.** The 6 that fail do so only on `+8`, because
their rows address pooled/instanced actors that live outside the level table.

## Test 3 — invariance across four maps

For every 4-byte offset, collect the distinct values across **28,543 entries**
spanning all six levels. An offset with exactly one value never varies.

**Result: 2,956 bytes per entry (summed over types) never vary; 1,468 do.**

And **8 types have no varying field at all**. That is not a coincidence of
authoring — they are byte-identical files, sha1-verified across every map that
ships them:

| Type | Stride | sha1 (first 10) | Ships in |
|---|---|---|---|
| `CInputCRWin10` | 32 | `44de797672` | the 4 combat maps |
| `CR15NetAutoTargetCRWin10` | 104 | `2cda5cd43e` | 4 combat maps + lobby |
| `CR15NetDynamicCoverCRWin10` | 32 | `c2da113bdb` | 4 combat maps + lobby |
| `CR15NetFollowPlayerCRWin10` | 40 | `4e4e9c0b78` | 4 combat maps + lobby |
| `CR15NetKillTickerCRWin10` | 32 | `d339719769` | the 4 combat maps |
| `CR15NetPooledActorCRWin10` | 40 | `f5a400d4aa` | 4 combat maps + lobby |
| `CR15NetVarCRWin10` | 32 | `3ee65badf4` | the 4 combat maps |
| `CR15TriggerCRWin10` | 72 | `6be1deab22` | 4 combat maps + lobby |

These are fixed templates on fixed actors — `CR15TriggerCR` is always the single
combat trigger `0x26B939FCAC9FBFF8`. They can be carried verbatim.

## Test 4 — correlation against ground truth

Every entry names its actor at `+8`, and that actor has a known position,
rotation and scale. So each 4-byte offset can be asked whether it equals one of
those, in every entry of every level.

Degenerate matches are rejected: a field of zeros "matches" a rotation of zeros
and proves nothing, so a claim counts only when **both** the field and the truth
value actually vary, with at least 8 entries. `+8` is excluded from "actor ref"
because it is the owner by definition.

**Result: 5 types carry a proven second reference.**

| Type | Offset | Proven meaning |
|---|---|---|
| `CR15LinearConstraintTouchInteractCRWin10` | `+96`, `+120` | actor references |
| `CR15LinearPositionConstraintCRWin10` | `+64`, `+88` | actor references |
| `CR15NetGunCRWin10` | `+80`, `+104` | actor references |
| `CR15RigidAttachConstraintCRWin10` | `+32` | entry index |
| `CSharedCanvasUICRWin10` | `+48` | actor reference |

Under the strict rule, most fields are *not* copies of a transform — which is
the honest finding, and why the loose version of this test (which claimed all 61)
was discarded.

## Test 5 — route ordering

Are `CR15TrackPointCR` entries in route order, or just authoring order? Compare
the gap between consecutive entries' actor positions against the gap between
arbitrary pairs.

| Level | Points | Consecutive gap (median) | Arbitrary pair (median) | Route length |
|---|---|---|---|---|
| fission | 69 | 1.34 m | 57.49 m | 181.8 m |
| gauss | 181 | 0.66 m | 53.75 m | 206.8 m |

**Consecutive entries are ~43x and ~81x closer than arbitrary pairs.** Entry
order is route order, and the totals (182 m, 207 m) are sane payload routes.

## Test 6 — what a field points at

`CTextureStreamingCR` `+32` (stride 40) was resolved against every type
directory in the archive: **604 of 726 fission entries name a resource that
exists**, all under the asset's own hash. It is a model-asset reference, not a
texture id.

The remaining 122 name assets absent from this extract. Independently confirmed
in-game: re-keying this field is what fixed a payload that loaded but drew
nothing, because the streamer was fetching a different asset's textures than the
model used.
