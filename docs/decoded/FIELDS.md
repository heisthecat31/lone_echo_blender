# Field map — every offset of every record type

Semantics can rarely be *proven* (see OPEN.md), but the **value domain** of every
field can be measured, and that is what this file records. Profiled over
**181 record types** and **73679 sampled entries** across all levels.

How to read a row:

| Kind | Meaning |
|---|---|
| `zero` | every observed value is 0 |
| `0xFFFFFFFF` | every observed value is all-ones (the engine's "unset") |
| `constant` | one value everywhere — a node hash, a type tag, or a fixed flag |
| `actor ref` | the u64 here resolves in the level's `CActorDataResource` for >=99% of entries |
| `float32` | every value is a finite float in a sane range, and they vary |
| `small int` | all values below 65536 — counts, indices, ids, flags |
| `hash-like` | high-entropy 64-bit values, mostly distinct — names or symbols |
| `mixed` | varies without fitting any of the above |

`+0` is the component-system node hash and `+8` is the owning actor; both are
part of the envelope (`../combat/CR_FORMAT.md`) and appear here for completeness.

**Offsets overlap.** A field is profiled at every 4-byte position, and a u64 at
`+16` is also visible as two u32s at `+16` and `+20`. Read neighbouring rows
together rather than as separate fields.

## Cross-references between actors

13 types carry an actor reference at an offset **other than** `+8` — that is a
record pointing at a second actor, which is structural information rather than a
value domain:


* `CAnimationCRWin10` -> `+0` (1570/1695 resolve)
* `CEventCRWin10` -> `+16` (264/378 resolve)
* `CPlatformCRWin10` -> `+0` (180/216 resolve)
* `CR15KillVolCRWin10` -> `+0` (18/30 resolve)
* `CR15LinearConstraintTouchInteractCRWin10` -> `+96` (32/38 resolve), `+120` (32/38 resolve)
* `CR15LinearPositionConstraintCRWin10` -> `+64` (32/44 resolve), `+88` (32/44 resolve)
* `CR15NetBitFieldCRWin10` -> `+16` (565/600 resolve)
* `CR15NetGunCRWin10` -> `+80` (600/600 resolve), `+104` (600/600 resolve)
* `CR15NetTrackMoverCRWin10` -> `+32` (2/2 resolve)
* `CR15SliderCRWin10` -> `+144` (2/2 resolve)
* `CR15StandardPOICRWin10` -> `+200` (41/41 resolve), `+216` (41/41 resolve), `+232` (41/41 resolve), `+248` (41/41 resolve)
* `CR15TrackPointCRWin10` -> `+40` (248/250 resolve)
* `CSharedCanvasUICRWin10` -> `+48` (104/108 resolve)

## Per-type field maps

### `CActorAliasCRWin10`

0x05D48844E3733C5E — stride **48**, 1 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x4214C649 (1108657737) |
| `+4` | constant | 0x99BD0B5C (2579303260) |
| `+8` | constant | 0x4E2D2402 (1311581186) |
| `+12` | constant | 0x7AE6881A (2061928474) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x2E37FF19 (775421721) |
| `+36` | constant | 0x118D5373 (294474611) |
| `+40` | zero |  |
| `+44` | zero |  |

### `CActorJugglerCRWin10`

0x975B4C59D4B2F418 — stride **144**, 1 entries sampled, 1 level(s), pooled, 2 inline table(s) at +32, +88

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x86407890 (2252372112) |
| `+4` | constant | 0xD37A6C15 (3548015637) |
| `+8` | constant | 0x4E2D2402 (1311581186) |
| `+12` | constant | 0x7AE6881A (2061928474) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | constant | 0x00000020 (32) |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | constant | 0x00000002 (2) |
| `+76` | zero |  |
| `+80` | constant | 0x00000002 (2) |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | constant | 0x000000C0 (192) |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | constant | 0x00000001 (1) |
| `+120` | constant | 0x00000020 (32) |
| `+124` | zero |  |
| `+128` | constant | 0x0000000C (12) |
| `+132` | zero |  |
| `+136` | constant | 0x0000000C (12) |
| `+140` | zero |  |

### `CActorLODCRWin10`

0x424FB75EFEE13BA6 — stride **48**, 237 entries sampled, 12 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x3BF38B62 (1005816674) |
| `+4` | constant | 0x37EE8018 (938377240) |
| `+8` | actor ref | 237/237 resolve |
| `+12` | mixed | 111 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 0.000 |
| `+36` | float32 | range 0.000 .. 0.000 |
| `+40` | zero |  |
| `+44` | float32 | range 0.000 .. 50.000 |

### `CActorRegionLODCRWin10`

0x409758926E4728C4 — stride **32**, 540 entries sampled, 10 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xA642A24E (2789384782) |
| `+4` | constant | 0x3322D914 (857921812) |
| `+8` | actor ref | 540/540 resolve |
| `+12` | hash-like | 300 distinct of 540 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |

### `CAmbientSoundCRWin10`

0x4047315F08901C70 — stride **280**, 61 entries sampled, 6 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x2C9EDD3C (748608828) |
| `+4` | constant | 0x6089664C (1619617356) |
| `+8` | actor ref | 61/61 resolve |
| `+12` | hash-like | 55 distinct of 61 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | constant | 0x00000010 (16) |
| `+28` | zero |  |
| `+32` | mixed | 25 distinct |
| `+36` | float32 | range 14.000 .. 30.000 |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | zero |  |
| `+64` | zero |  |
| `+68` | constant | 0x00000001 (1) |
| `+72` | zero |  |
| `+76` | zero |  |
| `+80` | zero |  |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | zero |  |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | zero |  |
| `+120` | zero |  |
| `+124` | constant | 0x00000001 (1) |
| `+128` | zero |  |
| `+132` | zero |  |
| `+136` | zero |  |
| `+140` | zero |  |
| `+144` | zero |  |
| `+148` | zero |  |
| `+152` | zero |  |
| `+156` | zero |  |
| `+160` | zero |  |
| `+164` | zero |  |
| `+168` | zero |  |
| `+172` | zero |  |
| `+176` | zero |  |
| `+180` | constant | 0x00000001 (1) |
| `+184` | zero |  |
| `+188` | zero |  |
| `+192` | zero |  |
| `+196` | zero |  |
| `+200` | zero |  |
| `+204` | zero |  |
| `+208` | zero |  |
| `+212` | zero |  |
| `+216` | zero |  |
| `+220` | zero |  |
| `+224` | zero |  |
| `+228` | zero |  |
| `+232` | zero |  |
| `+236` | constant | 0x00000001 (1) |
| `+240` | zero |  |
| `+244` | zero |  |
| `+248` | zero |  |
| `+252` | zero |  |
| `+256` | zero |  |
| `+260` | zero |  |
| `+264` | zero |  |
| `+268` | zero |  |
| `+272` | zero |  |
| `+276` | zero |  |

### `CAnimationCRWin10`

0xDB098C12AD9B9844 — stride **24**, 1695 entries sampled, 17 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | actor ref (partial) | 1570/1695 resolve |
| `+4` | mixed | 449 distinct |
| `+8` | mixed | 26 distinct |
| `+12` | float32 | range 0.000 .. 0.000 |
| `+16` | mixed | 31 distinct |
| `+20` | mixed | 11 distinct |

### `CBillboardCRWin10`

0x615E262159C487E0 — stride **80**, 247 entries sampled, 17 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range 0.000 .. 0.006 |
| `+4` | hash-like | 197 distinct of 247 |
| `+8` | actor ref | 247/247 resolve |
| `+12` | hash-like | 178 distinct of 247 |
| `+16` | mixed | 4 distinct |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | mixed | 4 distinct |
| `+36` | mixed | 4 distinct |
| `+40` | 0xFFFFFFFF |  |
| `+44` | 0xFFFFFFFF |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | 0xFFFFFFFF |  |
| `+60` | 0xFFFFFFFF |  |
| `+64` | zero |  |
| `+68` | float32 | range -1.000 .. 1.000 |
| `+72` | zero |  |
| `+76` | zero |  |

### `CBlackboardCRWin10`

0xD6E7A7F4813D3656 — stride **592**, 20 entries sampled, 4 level(s), pooled, 6 inline table(s) at +32, +88, +144, +200, +480, +536

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x2DEE9F10 (770613008) |
| `+4` | constant | 0x0891313B (143733051) |
| `+8` | actor ref | 20/20 resolve |
| `+12` | hash-like | 20 distinct of 20 |
| `+16` | mixed | 2 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | float32 | range 0.000 .. 0.000 |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | float32 | range 0.000 .. 0.000 |
| `+76` | zero |  |
| `+80` | float32 | range 0.000 .. 0.000 |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | float32 | range 0.000 .. 0.000 |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | constant | 0x00000001 (1) |
| `+120` | constant | 0x00000020 (32) |
| `+124` | zero |  |
| `+128` | float32 | range 0.000 .. 0.000 |
| `+132` | zero |  |
| `+136` | float32 | range 0.000 .. 0.000 |
| `+140` | zero |  |
| `+144` | zero |  |
| `+148` | zero |  |
| `+152` | float32 | range 0.000 .. 0.000 |
| `+156` | zero |  |
| `+160` | zero |  |
| `+164` | zero |  |
| `+168` | zero |  |
| `+172` | constant | 0x00000001 (1) |
| `+176` | constant | 0x00000020 (32) |
| `+180` | zero |  |
| `+184` | float32 | range 0.000 .. 0.000 |
| `+188` | zero |  |
| `+192` | float32 | range 0.000 .. 0.000 |
| `+196` | zero |  |
| `+200` | zero |  |
| `+204` | zero |  |
| `+208` | float32 | range 0.000 .. 0.000 |
| `+212` | zero |  |
| `+216` | zero |  |
| `+220` | zero |  |
| `+224` | zero |  |
| `+228` | constant | 0x00000001 (1) |
| `+232` | constant | 0x00000020 (32) |
| `+236` | zero |  |
| `+240` | float32 | range 0.000 .. 0.000 |
| `+244` | zero |  |
| `+248` | float32 | range 0.000 .. 0.000 |
| `+252` | zero |  |
| `+256` | zero |  |
| `+260` | zero |  |
| `+264` | zero |  |
| `+268` | zero |  |
| `+272` | zero |  |
| `+276` | zero |  |
| `+280` | zero |  |
| `+284` | constant | 0x00000001 (1) |
| `+288` | constant | 0x00000020 (32) |
| `+292` | zero |  |
| `+296` | zero |  |
| `+300` | zero |  |
| `+304` | zero |  |
| `+308` | zero |  |
| `+312` | zero |  |
| `+316` | zero |  |
| `+320` | zero |  |
| `+324` | zero |  |
| `+328` | zero |  |
| `+332` | zero |  |
| `+336` | zero |  |
| `+340` | constant | 0x00000001 (1) |
| `+344` | constant | 0x00000020 (32) |
| `+348` | zero |  |
| `+352` | zero |  |
| `+356` | zero |  |
| `+360` | zero |  |
| `+364` | zero |  |
| `+368` | zero |  |
| `+372` | zero |  |
| `+376` | zero |  |
| `+380` | zero |  |
| `+384` | zero |  |
| `+388` | zero |  |
| `+392` | zero |  |
| `+396` | constant | 0x00000001 (1) |
| `+400` | constant | 0x00000020 (32) |
| `+404` | zero |  |
| `+408` | zero |  |
| `+412` | zero |  |
| `+416` | zero |  |
| `+420` | zero |  |
| `+424` | zero |  |
| `+428` | zero |  |
| `+432` | zero |  |
| `+436` | zero |  |
| `+440` | zero |  |
| `+444` | zero |  |
| `+448` | zero |  |
| `+452` | constant | 0x00000001 (1) |
| `+456` | constant | 0x00000020 (32) |
| `+460` | zero |  |
| `+464` | zero |  |
| `+468` | zero |  |
| `+472` | zero |  |
| `+476` | zero |  |
| `+480` | zero |  |
| `+484` | zero |  |
| `+488` | float32 | range 0.000 .. 0.000 |
| `+492` | zero |  |
| `+496` | zero |  |
| `+500` | zero |  |
| `+504` | zero |  |
| `+508` | constant | 0x00000001 (1) |
| `+512` | constant | 0x00000020 (32) |
| `+516` | zero |  |
| `+520` | float32 | range 0.000 .. 0.000 |
| `+524` | zero |  |
| `+528` | float32 | range 0.000 .. 0.000 |
| `+532` | zero |  |
| `+536` | zero |  |
| `+540` | zero |  |
| `+544` | float32 | range 0.000 .. 0.000 |
| `+548` | zero |  |
| `+552` | zero |  |
| `+556` | zero |  |
| `+560` | zero |  |
| `+564` | constant | 0x00000001 (1) |
| `+568` | constant | 0x00000020 (32) |
| `+572` | zero |  |
| `+576` | float32 | range 0.000 .. 0.000 |
| `+580` | zero |  |
| `+584` | float32 | range 0.000 .. 0.000 |
| `+588` | zero |  |

### `CBoundingSphereCRWin10`

0x22F9FCB2D5E52E3C — stride **48**, 5742 entries sampled, 30 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 16 distinct |
| `+4` | hash-like | 3783 distinct of 5742 |
| `+8` | actor ref | 5742/5742 resolve |
| `+12` | hash-like | 3560 distinct of 5742 |
| `+16` | mixed | 10 distinct |
| `+20` | constant | 0x00000001 (1) |
| `+24` | constant | 0x00000004 (4) |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | zero |  |
| `+44` | zero |  |

### `CCharacterAnimationCRWin10`

0x871EEF513970CFF6 — stride **24**, 6 entries sampled, 5 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | zero |  |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | zero |  |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | zero |  |

### `CCheckpointCRWin10`

0x97C5A1DF329BAF40 — stride **24**, 11 entries sampled, 6 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range -0.000 .. 0.000 |
| `+4` | float32 | range -0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | zero |  |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | float32 | range 0.000 .. 0.000 |

### `CComponentLODCRWin10`

0x7F49ABAE39AAF2AA — stride **96**, 2318 entries sampled, 18 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 6 distinct |
| `+4` | mixed | 6 distinct |
| `+8` | actor ref | 2318/2318 resolve |
| `+12` | mixed | 1044 distinct |
| `+16` | mixed | 3 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x8D260515 (2368079125) |
| `+36` | constant | 0xABFE651C (2885575964) |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | float32 | range 0.000 .. 0.000 |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | zero |  |
| `+64` | zero |  |
| `+68` | constant | 0x00000001 (1) |
| `+72` | constant | 0x00000020 (32) |
| `+76` | zero |  |
| `+80` | float32 | range 0.000 .. 0.000 |
| `+84` | zero |  |
| `+88` | float32 | range 0.000 .. 0.000 |
| `+92` | zero |  |

### `CComponentLODZoneCRWin10`

0x39C0EE986726DFD2 — stride **144**, 5 entries sampled, 5 level(s), pooled, 2 inline table(s) at +32, +88

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x7CDEA969 (2094967145) |
| `+4` | constant | 0x1AC33FF5 (449003509) |
| `+8` | actor ref | 5/5 resolve |
| `+12` | hash-like | 5 distinct of 5 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | constant | 0x00000008 (8) |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | constant | 0x00000001 (1) |
| `+76` | zero |  |
| `+80` | constant | 0x00000001 (1) |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | constant | 0x00000020 (32) |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | constant | 0x00000001 (1) |
| `+120` | constant | 0x00000020 (32) |
| `+124` | zero |  |
| `+128` | constant | 0x00000002 (2) |
| `+132` | zero |  |
| `+136` | constant | 0x00000002 (2) |
| `+140` | zero |  |

### `CComponentRegionLODCRWin10`

0x22CCEC2D7D8B0FFA — stride **88**, 42 entries sampled, 10 level(s), pooled, 1 inline table(s) at +32

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x16A3B10C (379826444) |
| `+4` | constant | 0xCCE4A1D2 (3437535698) |
| `+8` | actor ref | 42/42 resolve |
| `+12` | hash-like | 42 distinct of 42 |
| `+16` | mixed | 3 distinct |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | float32 | range 0.000 .. 0.000 |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | float32 | range 0.000 .. 0.000 |
| `+76` | zero |  |
| `+80` | float32 | range 0.000 .. 0.000 |
| `+84` | zero |  |

### `CDecalCRWin10`

0x3B5DB8AF43546D40 — stride **296**, 1341 entries sampled, 18 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 11 distinct |
| `+4` | hash-like | 1300 distinct of 1341 |
| `+8` | actor ref | 1341/1341 resolve |
| `+12` | hash-like | 1276 distinct of 1341 |
| `+16` | mixed | 7 distinct |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | mixed | 8 distinct |
| `+36` | mixed | 8 distinct |
| `+40` | mixed | 4 distinct |
| `+44` | mixed | 4 distinct |
| `+48` | mixed | 4 distinct |
| `+52` | mixed | 4 distinct |
| `+56` | mixed | 8 distinct |
| `+60` | mixed | 8 distinct |
| `+64` | mixed | 8 distinct |
| `+68` | mixed | 8 distinct |
| `+72` | float32 | range 0.098 .. 1.000 |
| `+76` | float32 | range 0.061 .. 1.000 |
| `+80` | float32 | range 0.008 .. 1.000 |
| `+84` | float32 | range 0.000 .. 12.000 |
| `+88` | constant | 0x3F800000 (1065353216) |
| `+92` | float32 | range 0.000 .. 1.000 |
| `+96` | float32 | range 0.000 .. 1.000 |
| `+100` | float32 | range 0.000 .. 1.000 |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | float32 | range 0.500 .. 1.000 |
| `+116` | float32 | range 0.200 .. 1.000 |
| `+120` | float32 | range 0.000 .. 0.500 |
| `+124` | float32 | range 0.000 .. 0.665 |
| `+128` | zero |  |
| `+132` | zero |  |
| `+136` | float32 | range 0.500 .. 1.000 |
| `+140` | float32 | range 0.200 .. 1.000 |
| `+144` | float32 | range 0.000 .. 0.500 |
| `+148` | float32 | range 0.000 .. 0.665 |
| `+152` | zero |  |
| `+156` | zero |  |
| `+160` | float32 | range 0.500 .. 1.000 |
| `+164` | float32 | range 0.200 .. 1.000 |
| `+168` | float32 | range 0.000 .. 0.500 |
| `+172` | float32 | range 0.000 .. 0.665 |
| `+176` | zero |  |
| `+180` | zero |  |
| `+184` | float32 | range 0.380 .. 1.000 |
| `+188` | float32 | range 0.170 .. 1.000 |
| `+192` | float32 | range 0.000 .. 0.532 |
| `+196` | float32 | range -0.010 .. 0.680 |
| `+200` | zero |  |
| `+204` | zero |  |
| `+208` | float32 | range 0.380 .. 1.000 |
| `+212` | float32 | range 0.170 .. 1.000 |
| `+216` | float32 | range 0.000 .. 0.532 |
| `+220` | float32 | range -0.010 .. 0.680 |
| `+224` | constant | 0x3F800000 (1065353216) |
| `+228` | constant | 0x3F800000 (1065353216) |
| `+232` | constant | 0x3F800000 (1065353216) |
| `+236` | constant | 0x3F800000 (1065353216) |
| `+240` | float32 | range 0.000 .. 0.000 |
| `+244` | constant | 0x43800000 (1132462080) |
| `+248` | constant | 0x3E000000 (1040187392) |
| `+252` | float32 | range 0.000 .. 0.000 |
| `+256` | constant | 0x3F800000 (1065353216) |
| `+260` | constant | 0x3F800000 (1065353216) |
| `+264` | float32 | range 0.100 .. 1.000 |
| `+268` | zero |  |
| `+272` | constant | 0x3F800000 (1065353216) |
| `+276` | constant | 0x3F800000 (1065353216) |
| `+280` | zero |  |
| `+284` | zero |  |
| `+288` | 0xFFFFFFFF |  |
| `+292` | 0xFFFFFFFF |  |

### `CDialogue2CRWin10`

0xD3C86A196705C13C — stride **176**, 17 entries sampled, 11 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x054524EA (88417514) |
| `+4` | constant | 0x056C0235 (90964533) |
| `+8` | actor ref | 17/17 resolve |
| `+12` | hash-like | 15 distinct of 17 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | float32 | range -0.000 .. 0.000 |
| `+36` | mixed | 3 distinct |
| `+40` | mixed | 2 distinct |
| `+44` | mixed | 2 distinct |
| `+48` | mixed | 3 distinct |
| `+52` | mixed | 3 distinct |
| `+56` | float32 | range 0.000 .. 0.000 |
| `+60` | zero |  |
| `+64` | 0xFFFFFFFF |  |
| `+68` | 0xFFFFFFFF |  |
| `+72` | constant | 0x1E30130C (506467084) |
| `+76` | constant | 0x41D2D53C (1104336188) |
| `+80` | 0xFFFFFFFF |  |
| `+84` | 0xFFFFFFFF |  |
| `+88` | 0xFFFFFFFF |  |
| `+92` | 0xFFFFFFFF |  |
| `+96` | constant | 0x9877EEAD (2557996717) |
| `+100` | constant | 0x89423FB4 (2302820276) |
| `+104` | constant | 0x3F800000 (1065353216) |
| `+108` | constant | 0x3F800000 (1065353216) |
| `+112` | constant | 0x3F800000 (1065353216) |
| `+116` | zero |  |
| `+120` | zero |  |
| `+124` | zero |  |
| `+128` | zero |  |
| `+132` | zero |  |
| `+136` | zero |  |
| `+140` | zero |  |
| `+144` | zero |  |
| `+148` | constant | 0x00000001 (1) |
| `+152` | constant | 0x00000020 (32) |
| `+156` | zero |  |
| `+160` | zero |  |
| `+164` | zero |  |
| `+168` | zero |  |
| `+172` | zero |  |

### `CDialogueSceneCRWin10`

0x9DDA14546287C580 — stride **32**, 3 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xE9CE8751 (3922626385) |
| `+4` | constant | 0x0CD0CA62 (215009890) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CDynamicLODRegionTargetCRWin10`

0x9A613982DD36EC92 — stride **32**, 57 entries sampled, 6 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x0CA1E3A0 (211936160) |
| `+4` | constant | 0xFFF35F2A (4294139690) |
| `+8` | actor ref | 57/57 resolve |
| `+12` | hash-like | 57 distinct of 57 |
| `+16` | mixed | 3 distinct |
| `+20` | constant | 0x00000001 (1) |
| `+24` | zero |  |
| `+28` | zero |  |

### `CEventCRWin10`

0x547B31427E1CBD8C — stride **24**, 378 entries sampled, 26 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 28 distinct |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | mixed | 55 distinct |
| `+12` | mixed | 23 distinct |
| `+16` | actor ref (partial) | 264/378 resolve |
| `+20` | mixed | 117 distinct |

### `CFrustumCullCRWin10`

0xCA5A03D5A497238C — stride **32**, 8187 entries sampled, 27 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 16 distinct |
| `+4` | hash-like | 7557 distinct of 8187 |
| `+8` | actor ref | 8187/8187 resolve |
| `+12` | hash-like | 7472 distinct of 8187 |
| `+16` | mixed | 9 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |

### `CGReflectionProbeResourceWin10`

0x2829C885034AFCDE — stride **56**, 116 entries sampled, 31 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range 0.000 .. 9.215 |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | float32 | range -0.707 .. 0.733 |
| `+16` | mixed | 2 distinct |
| `+20` | float32 | range 0.000 .. 1.000 |
| `+24` | float32 | range -163.647 .. 194.977 |
| `+28` | float32 | range -43.246 .. 23.983 |
| `+32` | float32 | range -203.799 .. 203.725 |
| `+36` | float32 | range -8.145 .. 0.000 |
| `+40` | float32 | range -8.602 .. 0.000 |
| `+44` | float32 | range -9.215 .. 0.000 |
| `+48` | float32 | range 0.000 .. 8.145 |
| `+52` | float32 | range 0.000 .. 8.602 |

### `CInputCRWin10`

0xEEC3FCAE33F68500 — stride **32**, 13 entries sampled, 11 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x27EF9172 (670011762) |
| `+4` | constant | 0x38EE951E (955159838) |
| `+8` | actor ref | 13/13 resolve |
| `+12` | hash-like | 9 distinct of 13 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | zero |  |
| `+28` | zero |  |

### `CJsonConfigCRWin10`

0xCB3478C2A8CD87CC — stride **24**, 22 entries sampled, 3 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | hash-like | 19 distinct of 22 |
| `+4` | hash-like | 19 distinct of 22 |
| `+8` | actor ref (partial) | 15/22 resolve |
| `+12` | float32 | range -1.647 .. 0.000 |
| `+16` | mixed | 4 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |

### `CLODRegionCRWin10`

0x1258CF094C80C4D2 — stride **200**, 44 entries sampled, 5 level(s), pooled, 3 inline table(s) at +32, +88, +144

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x16958F0A (378900234) |
| `+4` | constant | 0x305DD44E (811455566) |
| `+8` | actor ref | 44/44 resolve |
| `+12` | hash-like | 29 distinct of 44 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | constant | 0x00000010 (16) |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | constant | 0x00000001 (1) |
| `+76` | zero |  |
| `+80` | constant | 0x00000001 (1) |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | float32 | range 0.000 .. 0.000 |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | constant | 0x00000001 (1) |
| `+120` | constant | 0x00000020 (32) |
| `+124` | zero |  |
| `+128` | float32 | range 0.000 .. 0.000 |
| `+132` | zero |  |
| `+136` | float32 | range 0.000 .. 0.000 |
| `+140` | zero |  |
| `+144` | zero |  |
| `+148` | zero |  |
| `+152` | float32 | range 0.000 .. 0.000 |
| `+156` | zero |  |
| `+160` | zero |  |
| `+164` | zero |  |
| `+168` | zero |  |
| `+172` | constant | 0x00000001 (1) |
| `+176` | constant | 0x00000020 (32) |
| `+180` | zero |  |
| `+184` | float32 | range 0.000 .. 0.000 |
| `+188` | zero |  |
| `+192` | float32 | range 0.000 .. 0.000 |
| `+196` | zero |  |

### `CLegacyCameraDataCRWin10`

0x38F8036A376F5F64 — stride **192**, 23 entries sampled, 23 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xD0DA61C5 (3503972805) |
| `+4` | constant | 0xDD857FAD (3716513709) |
| `+8` | actor ref | 23/23 resolve |
| `+12` | hash-like | 23 distinct of 23 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | float32 | range 0.000 .. 0.000 |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | zero |  |
| `+68` | zero |  |
| `+72` | zero |  |
| `+76` | zero |  |
| `+80` | zero |  |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | constant | 0x00000001 (1) |
| `+96` | zero |  |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | zero |  |
| `+120` | zero |  |
| `+124` | zero |  |
| `+128` | zero |  |
| `+132` | zero |  |
| `+136` | constant | 0x00000010 (16) |
| `+140` | zero |  |
| `+144` | zero |  |
| `+148` | zero |  |
| `+152` | zero |  |
| `+156` | constant | 0x00000001 (1) |
| `+160` | zero |  |
| `+164` | zero |  |
| `+168` | constant | 0x00000001 (1) |
| `+172` | zero |  |
| `+176` | constant | 0x00000001 (1) |
| `+180` | zero |  |
| `+184` | zero |  |
| `+188` | zero |  |

### `CLevelAABBCRWin10`

0xB76203B6E5EAFF80 — stride **56**, 8276 entries sampled, 24 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x109D8206 (278757894) |
| `+4` | constant | 0x3A4FC347 (978305863) |
| `+8` | actor ref | 8276/8276 resolve |
| `+12` | hash-like | 7078 distinct of 8276 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | constant | 0x00000001 (1) |
| `+24` | constant | 0x00000004 (4) |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |

### `CListCRWin10`

0x0F0FB3116EC3F644 — stride **440**, 73 entries sampled, 8 level(s), pooled, 3 inline table(s) at +48, +104, +384

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 17 distinct |
| `+4` | hash-like | 41 distinct of 73 |
| `+8` | actor ref | 73/73 resolve |
| `+12` | mixed | 28 distinct |
| `+16` | mixed | 3 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 0.000 |
| `+36` | zero |  |
| `+40` | mixed | 3 distinct |
| `+44` | mixed | 3 distinct |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | float32 | range 0.000 .. 0.000 |
| `+60` | zero |  |
| `+64` | zero |  |
| `+68` | zero |  |
| `+72` | zero |  |
| `+76` | constant | 0x00000001 (1) |
| `+80` | constant | 0x00000020 (32) |
| `+84` | zero |  |
| `+88` | float32 | range 0.000 .. 0.000 |
| `+92` | zero |  |
| `+96` | float32 | range 0.000 .. 0.000 |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | float32 | range 0.000 .. 0.000 |
| `+116` | zero |  |
| `+120` | zero |  |
| `+124` | zero |  |
| `+128` | zero |  |
| `+132` | constant | 0x00000001 (1) |
| `+136` | constant | 0x00000020 (32) |
| `+140` | zero |  |
| `+144` | float32 | range 0.000 .. 0.000 |
| `+148` | zero |  |
| `+152` | float32 | range 0.000 .. 0.000 |
| `+156` | zero |  |
| `+160` | zero |  |
| `+164` | zero |  |
| `+168` | zero |  |
| `+172` | zero |  |
| `+176` | zero |  |
| `+180` | zero |  |
| `+184` | zero |  |
| `+188` | constant | 0x00000001 (1) |
| `+192` | constant | 0x00000020 (32) |
| `+196` | zero |  |
| `+200` | zero |  |
| `+204` | zero |  |
| `+208` | zero |  |
| `+212` | zero |  |
| `+216` | zero |  |
| `+220` | zero |  |
| `+224` | zero |  |
| `+228` | zero |  |
| `+232` | zero |  |
| `+236` | zero |  |
| `+240` | zero |  |
| `+244` | constant | 0x00000001 (1) |
| `+248` | constant | 0x00000020 (32) |
| `+252` | zero |  |
| `+256` | zero |  |
| `+260` | zero |  |
| `+264` | zero |  |
| `+268` | zero |  |
| `+272` | zero |  |
| `+276` | zero |  |
| `+280` | zero |  |
| `+284` | zero |  |
| `+288` | zero |  |
| `+292` | zero |  |
| `+296` | zero |  |
| `+300` | constant | 0x00000001 (1) |
| `+304` | constant | 0x00000020 (32) |
| `+308` | zero |  |
| `+312` | zero |  |
| `+316` | zero |  |
| `+320` | zero |  |
| `+324` | zero |  |
| `+328` | zero |  |
| `+332` | zero |  |
| `+336` | zero |  |
| `+340` | zero |  |
| `+344` | zero |  |
| `+348` | zero |  |
| `+352` | zero |  |
| `+356` | constant | 0x00000001 (1) |
| `+360` | constant | 0x00000020 (32) |
| `+364` | zero |  |
| `+368` | zero |  |
| `+372` | zero |  |
| `+376` | zero |  |
| `+380` | zero |  |
| `+384` | zero |  |
| `+388` | zero |  |
| `+392` | float32 | range 0.000 .. 0.000 |
| `+396` | zero |  |
| `+400` | zero |  |
| `+404` | zero |  |
| `+408` | zero |  |
| `+412` | constant | 0x00000001 (1) |
| `+416` | constant | 0x00000020 (32) |
| `+420` | zero |  |
| `+424` | float32 | range 0.000 .. 0.000 |
| `+428` | zero |  |
| `+432` | float32 | range 0.000 .. 0.000 |
| `+436` | zero |  |

### `CMaterialTypeCRWin10`

0x3B87EF2FC94A9B14 — stride **32**, 31 entries sampled, 31 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x2BBF816F (733970799) |
| `+4` | constant | 0xF4675E41 (4100415041) |
| `+8` | actor ref | 31/31 resolve |
| `+12` | hash-like | 31 distinct of 31 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CMaterialTypesBVHResourceWin10`

0x4230B4E0957B5462 — stride **16**, 10045 entries sampled, 31 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 5 distinct |
| `+4` | mixed | 5 distinct |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | float32 | range 0.000 .. 0.000 |

### `CModelLODCRWin10`

0x0B5DA9ABD34D392E — stride **24**, 4 entries sampled, 1 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range 0.000 .. 0.000 |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | zero |  |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | float32 | range 0.000 .. 0.000 |

### `CModelSwapperCRWin10`

0x915FD67110D60292 — stride **24**, 16 entries sampled, 2 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range 0.000 .. 0.000 |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | float32 | range 0.000 .. 0.000 |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | float32 | range 0.000 .. 0.000 |

### `COccluderMeshCRWin10`

0x85D28CE20F6A83F8 — stride **48**, 22 entries sampled, 4 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x62A41BA2 (1654922146) |
| `+4` | constant | 0xCF1E371D (3474863901) |
| `+8` | actor ref | 22/22 resolve |
| `+12` | mixed | 11 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | float32 | range -0.000 .. 71.368 |
| `+36` | hash-like | 22 distinct of 22 |
| `+40` | float32 | range 0.000 .. 0.000 |
| `+44` | zero |  |

### `COccluderMeshResourceWin10`

0xB5CBB950CB55A92C — stride **136**, 22 entries sampled, 31 level(s), pooled, 2 inline table(s) at +0, +56

| Offset | Kind | Detail |
|---|---|---|
| `+0` | zero |  |
| `+4` | zero |  |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | zero |  |
| `+16` | zero |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | constant | 0x00000001 (1) |
| `+32` | constant | 0x00000020 (32) |
| `+36` | zero |  |
| `+40` | float32 | range 0.000 .. 0.000 |
| `+44` | zero |  |
| `+48` | float32 | range 0.000 .. 0.000 |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | zero |  |
| `+64` | float32 | range 0.000 .. 0.000 |
| `+68` | zero |  |
| `+72` | zero |  |
| `+76` | zero |  |
| `+80` | zero |  |
| `+84` | constant | 0x00000001 (1) |
| `+88` | constant | 0x00000020 (32) |
| `+92` | zero |  |
| `+96` | float32 | range 0.000 .. 0.000 |
| `+100` | zero |  |
| `+104` | float32 | range 0.000 .. 0.000 |
| `+108` | zero |  |
| `+112` | float32 | range -43.730 .. 38.587 |
| `+116` | float32 | range -1.917 .. 9.332 |
| `+120` | float32 | range -33.521 .. 22.755 |
| `+124` | float32 | range 8.158 .. 81.464 |
| `+128` | float32 | range 0.000 .. 0.000 |
| `+132` | zero |  |

### `COcclusionCullCRWin10`

0x142026F469321D54 — stride **32**, 8165 entries sampled, 27 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 16 distinct |
| `+4` | hash-like | 7546 distinct of 8165 |
| `+8` | actor ref | 8165/8165 resolve |
| `+12` | hash-like | 7461 distinct of 8165 |
| `+16` | mixed | 9 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |

### `CParticleEffectCRWin10`

0x21A09C2016D8F3E6 — stride **152**, 134 entries sampled, 9 level(s), pooled, 1 inline table(s) at +96

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 19 distinct |
| `+4` | mixed | 17 distinct |
| `+8` | actor ref | 134/134 resolve |
| `+12` | mixed | 34 distinct |
| `+16` | mixed | 6 distinct |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | mixed | 20 distinct |
| `+36` | mixed | 20 distinct |
| `+40` | mixed | 3 distinct |
| `+44` | mixed | 3 distinct |
| `+48` | mixed | 9 distinct |
| `+52` | mixed | 8 distinct |
| `+56` | float32 | range 0.000 .. 0.000 |
| `+60` | float32 | range -1.474 .. 0.089 |
| `+64` | float32 | range -0.715 .. 0.033 |
| `+68` | float32 | range -1.415 .. 1.415 |
| `+72` | float32 | range -90.000 .. 0.000 |
| `+76` | float32 | range -90.000 .. 0.000 |
| `+80` | zero |  |
| `+84` | float32 | range 0.200 .. 1.500 |
| `+88` | float32 | range 0.200 .. 1.500 |
| `+92` | float32 | range 0.200 .. 1.500 |
| `+96` | zero |  |
| `+100` | zero |  |
| `+104` | float32 | range 0.000 .. 0.000 |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | zero |  |
| `+120` | zero |  |
| `+124` | constant | 0x00000001 (1) |
| `+128` | constant | 0x00000020 (32) |
| `+132` | zero |  |
| `+136` | float32 | range 0.000 .. 0.000 |
| `+140` | zero |  |
| `+144` | float32 | range 0.000 .. 0.000 |
| `+148` | zero |  |

### `CPhysicsCRWin10`

0xF6BAB7207D923478 — stride **48**, 863 entries sampled, 23 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xE22AD937 (3794458935) |
| `+4` | constant | 0x5B8CC538 (1535952184) |
| `+8` | actor ref | 863/863 resolve |
| `+12` | hash-like | 570 distinct of 863 |
| `+16` | mixed | 4 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | constant | 0x3F800000 (1065353216) |
| `+40` | 0xFFFFFFFF |  |
| `+44` | 0xFFFFFFFF |  |

### `CPlatformCRWin10`

0x40861B479CAC8CD8 — stride **24**, 216 entries sampled, 6 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | actor ref (partial) | 180/216 resolve |
| `+4` | mixed | 57 distinct |
| `+8` | mixed | 7 distinct |
| `+12` | float32 | range 0.000 .. 0.000 |
| `+16` | float32 | range -0.000 .. 0.000 |
| `+20` | mixed | 6 distinct |

### `CPluginCRWin10`

0xF5B9A3786E2C020C — stride **24**, 3 entries sampled, 2 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | zero |  |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | zero |  |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | zero |  |

### `CPositionSelectionCRWin10`

0xEF9D3D97F6A3F71C — stride **24**, 1 entries sampled, 1 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | zero |  |
| `+4` | zero |  |
| `+8` | constant | 0x00000008 (8) |
| `+12` | zero |  |
| `+16` | zero |  |
| `+20` | zero |  |

### `CR15ArmComputerSystemCRWin10`

0xF952BA5AB225E648 — stride **88**, 1 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xB799DBB5 (3080313781) |
| `+4` | constant | 0x0DABF147 (229372231) |
| `+8` | constant | 0x4E070923 (1309083939) |
| `+12` | constant | 0x6C1F6FF0 (1813999600) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x28A0FF05 (681639685) |
| `+36` | constant | 0x0E3444A5 (238306469) |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | constant | 0xB2BAB6C4 (2998580932) |
| `+52` | constant | 0x6AEA99EE (1793759726) |
| `+56` | constant | 0x89DF5B7C (2313116540) |
| `+60` | constant | 0x8A5DCEE3 (2321403619) |
| `+64` | zero |  |
| `+68` | zero |  |
| `+72` | constant | 0xB2BAB6C4 (2998580932) |
| `+76` | constant | 0x6AEA99EE (1793759726) |
| `+80` | constant | 0x00000010 (16) |
| `+84` | zero |  |

### `CR15AudioListenerCRWin10`

0x05E8CC8D044039A8 — stride **32**, 2 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x7011C8C4 (1880213700) |
| `+4` | constant | 0x1996445E (429278302) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | float32 | range -249.401 .. -1.647 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15BatteryPackCRWin10`

0x098DB0CB749C99B4 — stride **56**, 4 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x9C3D77E5 (2621274085) |
| `+4` | constant | 0x45896146 (1166631238) |
| `+8` | actor ref | 4/4 resolve |
| `+12` | hash-like | 4 distinct of 4 |
| `+16` | mixed | 2 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x42C80000 (1120403456) |
| `+36` | constant | 0x41400000 (1094713344) |
| `+40` | constant | 0x3F59999A (1062836634) |
| `+44` | constant | 0x3F800000 (1065353216) |
| `+48` | constant | 0x3F800000 (1065353216) |
| `+52` | zero |  |

### `CR15BodyCRWin10`

0xC203E475283313A4 — stride **312**, 12 entries sampled, 4 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 2 distinct |
| `+4` | hash-like | 12 distinct of 12 |
| `+8` | actor ref | 12/12 resolve |
| `+12` | hash-like | 9 distinct of 12 |
| `+16` | mixed | 3 distinct |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | mixed | 2 distinct |
| `+36` | mixed | 2 distinct |
| `+40` | mixed | 4 distinct |
| `+44` | mixed | 4 distinct |
| `+48` | constant | 0x43A491A1 (1134858657) |
| `+52` | constant | 0x00EC6519 (15492377) |
| `+56` | constant | 0x80A78294 (2158461588) |
| `+60` | constant | 0x104B470D (273368845) |
| `+64` | constant | 0xC1D435D5 (3251910101) |
| `+68` | constant | 0x4609C833 (1175046195) |
| `+72` | mixed | 2 distinct |
| `+76` | mixed | 2 distinct |
| `+80` | mixed | 2 distinct |
| `+84` | float32 | range 0.000 .. 8818.050 |
| `+88` | mixed | 2 distinct |
| `+92` | float32 | range -0.000 .. 0.000 |
| `+96` | mixed | 2 distinct |
| `+100` | float32 | range 0.000 .. 8818.050 |
| `+104` | mixed | 2 distinct |
| `+108` | float32 | range -0.000 .. 0.000 |
| `+112` | mixed | 2 distinct |
| `+116` | float32 | range 0.000 .. 8818.050 |
| `+120` | mixed | 2 distinct |
| `+124` | float32 | range -0.000 .. 0.000 |
| `+128` | mixed | 2 distinct |
| `+132` | float32 | range 0.000 .. 8818.050 |
| `+136` | mixed | 2 distinct |
| `+140` | float32 | range -0.000 .. 0.000 |
| `+144` | mixed | 2 distinct |
| `+148` | float32 | range 0.000 .. 8818.050 |
| `+152` | mixed | 2 distinct |
| `+156` | float32 | range -0.000 .. 0.000 |
| `+160` | mixed | 2 distinct |
| `+164` | float32 | range 0.000 .. 8818.050 |
| `+168` | mixed | 2 distinct |
| `+172` | float32 | range -0.000 .. 0.000 |
| `+176` | mixed | 2 distinct |
| `+180` | float32 | range 0.000 .. 8818.050 |
| `+184` | mixed | 2 distinct |
| `+188` | float32 | range -0.000 .. 0.000 |
| `+192` | mixed | 2 distinct |
| `+196` | float32 | range 0.000 .. 8818.050 |
| `+200` | mixed | 2 distinct |
| `+204` | float32 | range -0.000 .. 0.000 |
| `+208` | mixed | 2 distinct |
| `+212` | float32 | range 0.000 .. 8818.050 |
| `+216` | mixed | 2 distinct |
| `+220` | mixed | 2 distinct |
| `+224` | mixed | 2 distinct |
| `+228` | float32 | range 0.000 .. 8818.050 |
| `+232` | mixed | 2 distinct |
| `+236` | mixed | 2 distinct |
| `+240` | mixed | 2 distinct |
| `+244` | float32 | range 0.000 .. 8818.050 |
| `+248` | mixed | 2 distinct |
| `+252` | mixed | 2 distinct |
| `+256` | mixed | 2 distinct |
| `+260` | float32 | range 0.000 .. 8818.050 |
| `+264` | mixed | 2 distinct |
| `+268` | mixed | 2 distinct |
| `+272` | mixed | 2 distinct |
| `+276` | float32 | range 0.000 .. 8818.050 |
| `+280` | mixed | 2 distinct |
| `+284` | mixed | 2 distinct |
| `+288` | mixed | 2 distinct |
| `+292` | float32 | range 0.000 .. 8818.050 |
| `+296` | mixed | 2 distinct |
| `+300` | mixed | 2 distinct |
| `+304` | mixed | 2 distinct |
| `+308` | float32 | range 0.000 .. 8818.050 |

### `CR15BounceCRWin10`

0x74EDE05B09640CEA — stride **48**, 51 entries sampled, 10 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x9A26458D (2586199437) |
| `+4` | constant | 0x7A804600 (2055226880) |
| `+8` | actor ref | 51/51 resolve |
| `+12` | mixed | 21 distinct |
| `+16` | mixed | 2 distinct |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range 0.250 .. 0.600 |
| `+36` | float32 | range 0.500 .. 1.000 |
| `+40` | float32 | range 0.500 .. 1.000 |
| `+44` | constant | 0x3F800000 (1065353216) |

### `CR15ButtonInteractCRWin10`

0xE9B24EA816DECE48 — stride **296**, 2705 entries sampled, 17 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 35 distinct |
| `+4` | mixed | 18 distinct |
| `+8` | actor ref | 2705/2705 resolve |
| `+12` | mixed | 517 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 100.000 |
| `+36` | float32 | range 0.000 .. 1000.000 |
| `+40` | float32 | range 0.000 .. 0.035 |
| `+44` | float32 | range 0.000 .. 0.035 |
| `+48` | float32 | range 0.000 .. 1.222 |
| `+52` | zero |  |
| `+56` | mixed | 3 distinct |
| `+60` | mixed | 3 distinct |
| `+64` | mixed | 2 distinct |
| `+68` | mixed | 2 distinct |
| `+72` | mixed | 3 distinct |
| `+76` | mixed | 3 distinct |
| `+80` | float32 | range -0.110 .. -0.004 |
| `+84` | float32 | range -0.066 .. -0.001 |
| `+88` | float32 | range -0.066 .. -0.005 |
| `+92` | float32 | range 0.004 .. 0.060 |
| `+96` | float32 | range 0.008 .. 0.066 |
| `+100` | float32 | range 0.001 .. 0.066 |
| `+104` | float32 | range 0.000 .. 1.000 |
| `+108` | float32 | range 0.000 .. 1.000 |
| `+112` | float32 | range 0.000 .. 1.000 |
| `+116` | float32 | range -0.016 .. 0.000 |
| `+120` | zero |  |
| `+124` | zero |  |
| `+128` | float32 | range -313.000 .. 188.100 |
| `+132` | float32 | range -57.479 .. 82.816 |
| `+136` | float32 | range -254.627 .. 41.143 |
| `+140` | float32 | range -0.165 .. 0.707 |
| `+144` | float32 | range -0.924 .. 1.000 |
| `+148` | float32 | range -0.396 .. 0.431 |
| `+152` | float32 | range -1.000 .. 1.000 |
| `+156` | float32 | range 0.000 .. 1.000 |
| `+160` | float32 | range 0.000 .. 1.000 |
| `+164` | float32 | range 0.000 .. 1.000 |
| `+168` | float32 | range -0.707 .. 0.304 |
| `+172` | float32 | range -0.707 .. 0.638 |
| `+176` | float32 | range 0.000 .. 0.638 |
| `+180` | float32 | range 0.304 .. 1.000 |
| `+184` | float32 | range -0.047 .. 0.154 |
| `+188` | float32 | range -0.153 .. 1.631 |
| `+192` | float32 | range -0.008 .. 0.935 |
| `+196` | float32 | range 0.010 .. 1.000 |
| `+200` | 0xFFFFFFFF |  |
| `+204` | 0xFFFFFFFF |  |
| `+208` | mixed | 6 distinct |
| `+212` | mixed | 6 distinct |
| `+216` | 0xFFFFFFFF |  |
| `+220` | 0xFFFFFFFF |  |
| `+224` | 0xFFFFFFFF |  |
| `+228` | 0xFFFFFFFF |  |
| `+232` | float32 | range 0.000 .. 1.000 |
| `+236` | float32 | range 0.000 .. 1.000 |
| `+240` | float32 | range 0.000 .. 1.000 |
| `+244` | float32 | range 0.050 .. 0.500 |
| `+248` | mixed | 72 distinct |
| `+252` | mixed | 72 distinct |
| `+256` | zero |  |
| `+260` | zero |  |
| `+264` | 0xFFFFFFFF |  |
| `+268` | 0xFFFFFFFF |  |
| `+272` | 0xFFFFFFFF |  |
| `+276` | 0xFFFFFFFF |  |
| `+280` | 0xFFFFFFFF |  |
| `+284` | 0xFFFFFFFF |  |
| `+288` | 0xFFFFFFFF |  |
| `+292` | 0xFFFFFFFF |  |

### `CR15CollisionCRWin10`

0x17FAF18DA79D0E78 — stride **56**, 35 entries sampled, 5 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xEF778E3A (4017589818) |
| `+4` | constant | 0x9EEFFF46 (2666528582) |
| `+8` | actor ref | 35/35 resolve |
| `+12` | mixed | 5 distinct |
| `+16` | constant | 0x000FFFFF (1048575) |
| `+20` | zero |  |
| `+24` | constant | 0x00000001 (1) |
| `+28` | zero |  |
| `+32` | 0xFFFFFFFF |  |
| `+36` | 0xFFFFFFFF |  |
| `+40` | 0xFFFFFFFF |  |
| `+44` | 0xFFFFFFFF |  |
| `+48` | constant | 0x3E4CCCCD (1045220557) |
| `+52` | zero |  |

### `CR15ContactTrackerCRWin10`

0x1472A245AA22E262 — stride **32**, 1 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xFDCE223C (4258144828) |
| `+4` | constant | 0xA20DBFCF (2718810063) |
| `+8` | constant | 0xBDA1D334 (3181499188) |
| `+12` | constant | 0xC6B1C21F (3333538335) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15CreditsUICRWin10`

0x20761564F11F2E70 — stride **32**, 1 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xE86D943D (3899495485) |
| `+4` | constant | 0x83E6F746 (2212951878) |
| `+8` | constant | 0xF5F01C0E (4126153742) |
| `+12` | constant | 0x1272C01E (309510174) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | constant | 0x00000001 (1) |
| `+28` | zero |  |

### `CR15DampenerCRWin10`

0x2B292830E51C4FD2 — stride **64**, 46 entries sampled, 8 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range -0.000 .. 0.000 |
| `+4` | hash-like | 34 distinct of 46 |
| `+8` | actor ref | 46/46 resolve |
| `+12` | hash-like | 33 distinct of 46 |
| `+16` | mixed | 2 distinct |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 10000.000 |
| `+36` | float32 | range 0.000 .. 10000.000 |
| `+40` | constant | 0x40A00000 (1084227584) |
| `+44` | constant | 0x41200000 (1092616192) |
| `+48` | constant | 0x3F800000 (1065353216) |
| `+52` | constant | 0x3F800000 (1065353216) |
| `+56` | constant | 0x3F800000 (1065353216) |
| `+60` | constant | 0x3F800000 (1065353216) |

### `CR15DialoguePOICRWin10`

0x7F103187AA42673A — stride **464**, 45 entries sampled, 7 level(s), pooled, 4 inline table(s) at +176, +232, +296, +352

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x003B2EC3 (3878595) |
| `+4` | constant | 0xB6F96D2E (3069799726) |
| `+8` | actor ref | 45/45 resolve |
| `+12` | hash-like | 30 distinct of 45 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | mixed | 14 distinct |
| `+36` | mixed | 16 distinct |
| `+40` | mixed | 15 distinct |
| `+44` | mixed | 12 distinct |
| `+48` | mixed | 6 distinct |
| `+52` | float32 | range 0.000 .. 0.000 |
| `+56` | float32 | range 0.000 .. 0.003 |
| `+60` | zero |  |
| `+64` | zero |  |
| `+68` | zero |  |
| `+72` | zero |  |
| `+76` | zero |  |
| `+80` | zero |  |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | zero |  |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | zero |  |
| `+120` | zero |  |
| `+124` | zero |  |
| `+128` | zero |  |
| `+132` | zero |  |
| `+136` | zero |  |
| `+140` | zero |  |
| `+144` | zero |  |
| `+148` | zero |  |
| `+152` | zero |  |
| `+156` | zero |  |
| `+160` | float32 | range 0.000 .. 9.000 |
| `+164` | mixed | 10 distinct |
| `+168` | mixed | 10 distinct |
| `+172` | zero |  |
| `+176` | zero |  |
| `+180` | zero |  |
| `+184` | float32 | range 0.000 .. 0.000 |
| `+188` | zero |  |
| `+192` | zero |  |
| `+196` | zero |  |
| `+200` | zero |  |
| `+204` | constant | 0x00000001 (1) |
| `+208` | constant | 0x00000020 (32) |
| `+212` | zero |  |
| `+216` | float32 | range 0.000 .. 0.000 |
| `+220` | zero |  |
| `+224` | float32 | range 0.000 .. 0.000 |
| `+228` | zero |  |
| `+232` | zero |  |
| `+236` | zero |  |
| `+240` | float32 | range 0.000 .. 0.000 |
| `+244` | zero |  |
| `+248` | zero |  |
| `+252` | zero |  |
| `+256` | zero |  |
| `+260` | constant | 0x00000001 (1) |
| `+264` | constant | 0x00000020 (32) |
| `+268` | zero |  |
| `+272` | float32 | range 0.000 .. 0.000 |
| `+276` | zero |  |
| `+280` | float32 | range 0.000 .. 0.000 |
| `+284` | zero |  |
| `+288` | mixed | 3 distinct |
| `+292` | float32 | range -399858.250 .. 26.351 |
| `+296` | zero |  |
| `+300` | zero |  |
| `+304` | float32 | range 0.000 .. 0.000 |
| `+308` | zero |  |
| `+312` | zero |  |
| `+316` | zero |  |
| `+320` | zero |  |
| `+324` | constant | 0x00000001 (1) |
| `+328` | constant | 0x00000020 (32) |
| `+332` | zero |  |
| `+336` | float32 | range 0.000 .. 0.000 |
| `+340` | zero |  |
| `+344` | float32 | range 0.000 .. 0.000 |
| `+348` | zero |  |
| `+352` | zero |  |
| `+356` | zero |  |
| `+360` | float32 | range 0.000 .. 0.000 |
| `+364` | zero |  |
| `+368` | zero |  |
| `+372` | zero |  |
| `+376` | zero |  |
| `+380` | constant | 0x00000001 (1) |
| `+384` | constant | 0x00000020 (32) |
| `+388` | zero |  |
| `+392` | float32 | range 0.000 .. 0.000 |
| `+396` | zero |  |
| `+400` | float32 | range 0.000 .. 0.000 |
| `+404` | zero |  |
| `+408` | zero |  |
| `+412` | zero |  |
| `+416` | zero |  |
| `+420` | zero |  |
| `+424` | zero |  |
| `+428` | zero |  |
| `+432` | zero |  |
| `+436` | constant | 0x00000001 (1) |
| `+440` | constant | 0x00000020 (32) |
| `+444` | zero |  |
| `+448` | zero |  |
| `+452` | zero |  |
| `+456` | zero |  |
| `+460` | zero |  |

### `CR15DockToViewCRWin10`

0x6D63AE74BBA190F0 — stride **88**, 1 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x6AC04BB9 (1790987193) |
| `+4` | constant | 0x0B33165C (187897436) |
| `+8` | constant | 0x27887D7E (663256446) |
| `+12` | constant | 0xC23FAC3C (3258952764) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x41F00000 (1106247680) |
| `+36` | constant | 0x3F400000 (1061158912) |
| `+40` | constant | 0x41F00000 (1106247680) |
| `+44` | constant | 0x42B40000 (1119092736) |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x87DB68D6 (2279303382) |
| `+68` | constant | 0x94087F0E (2483584782) |
| `+72` | zero |  |
| `+76` | zero |  |
| `+80` | constant | 0x8D260515 (2368079125) |
| `+84` | constant | 0xABFE651C (2885575964) |

### `CR15EchoPathingCRWin10`

0x0A92D2E2FD761398 — stride **32**, 1 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x8B48D273 (2336805491) |
| `+4` | constant | 0xFD34E2D1 (4248101585) |
| `+8` | constant | 0x1860D487 (408999047) |
| `+12` | constant | 0x7E1E9B7C (2115935100) |
| `+16` | constant | 0x000AFFFF (720895) |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15EchoUnitCRWin10`

0xCEC6B3333D1F0182 — stride **48**, 5 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xDEBAE6B6 (3736790710) |
| `+4` | constant | 0xEFE7A55E (4024935774) |
| `+8` | actor ref | 5/5 resolve |
| `+12` | hash-like | 5 distinct of 5 |
| `+16` | mixed | 2 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 0.000 |
| `+36` | hash-like | 5 distinct of 5 |
| `+40` | hash-like | 3 distinct of 5 |
| `+44` | mixed | 3 distinct |

### `CR15FacingCRWin10`

0x202DF2DFCF55DED8 — stride **32**, 7 entries sampled, 4 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x8C21488F (2350991503) |
| `+4` | constant | 0x7A81420E (2055291406) |
| `+8` | actor ref | 7/7 resolve |
| `+12` | hash-like | 7 distinct of 7 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15FlagBaseCRWin10`

0x8B5329AB0AC01796 — stride **216**, 9 entries sampled, 3 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x91226FCD (2434953165) |
| `+4` | constant | 0xC7B18A05 (3350301189) |
| `+8` | actor ref | 9/9 resolve |
| `+12` | hash-like | 9 distinct of 9 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 0.000 |
| `+36` | zero |  |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | zero |  |
| `+64` | constant | 0x3F800000 (1065353216) |
| `+68` | zero |  |
| `+72` | zero |  |
| `+76` | zero |  |
| `+80` | zero |  |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | zero |  |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | float32 | range -53.540 .. 0.000 |
| `+116` | float32 | range 0.000 .. 1.482 |
| `+120` | float32 | range -36.071 .. 36.042 |
| `+124` | float32 | range 0.000 .. 2.300 |
| `+128` | float32 | range -53.540 .. 0.000 |
| `+132` | float32 | range -0.269 .. 8.660 |
| `+136` | float32 | range -36.071 .. 36.042 |
| `+140` | float32 | range -0.099 .. 0.383 |
| `+144` | float32 | range -0.240 .. 0.924 |
| `+148` | float32 | range 0.000 .. 0.383 |
| `+152` | float32 | range 0.000 .. 0.924 |
| `+156` | float32 | range 0.066 .. 1.108 |
| `+160` | float32 | range 0.066 .. 1.103 |
| `+164` | float32 | range 0.005 .. 0.042 |
| `+168` | float32 | range -53.540 .. 0.000 |
| `+172` | float32 | range 0.000 .. 1.482 |
| `+176` | float32 | range -36.737 .. 36.708 |
| `+180` | float32 | range 0.000 .. 8.000 |
| `+184` | float32 | range -53.540 .. 0.000 |
| `+188` | float32 | range 0.000 .. 1.482 |
| `+192` | float32 | range -36.071 .. 36.042 |
| `+196` | float32 | range 0.000 .. 250.000 |
| `+200` | zero |  |
| `+204` | zero |  |
| `+208` | zero |  |
| `+212` | zero |  |

### `CR15FlagCRWin10`

0x3DC5C3DD185587B2 — stride **56**, 8 entries sampled, 5 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x5878568D (1484281485) |
| `+4` | constant | 0x2FC5DECB (801496779) |
| `+8` | actor ref | 8/8 resolve |
| `+12` | hash-like | 8 distinct of 8 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x0000FFFF (65535) |
| `+36` | zero |  |
| `+40` | 0xFFFFFFFF |  |
| `+44` | 0xFFFFFFFF |  |
| `+48` | zero |  |
| `+52` | zero |  |

### `CR15FrisbeeCRWin10`

0x1875FEBBEAB23F44 — stride **32**, 28 entries sampled, 10 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xF0B8B181 (4038635905) |
| `+4` | constant | 0x84C65832 (2227591218) |
| `+8` | actor ref | 28/28 resolve |
| `+12` | mixed | 11 distinct |
| `+16` | mixed | 2 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15GestureTargetCRWin10`

0xAD445A2A573C4D0A — stride **40**, 9 entries sampled, 8 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xF4285BFC (4096285692) |
| `+4` | constant | 0x60FA1B42 (1627003714) |
| `+8` | actor ref | 9/9 resolve |
| `+12` | hash-like | 9 distinct of 9 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | float32 | range -0.000 .. -0.000 |
| `+36` | mixed | 2 distinct |

### `CR15HandAnimatorCRWin10`

0xDB5B3CFAE8A705D2 — stride **48**, 3 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xFB38C71A (4214802202) |
| `+4` | constant | 0xDD4FD63F (3712996927) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0xCB512B41 (3411094337) |
| `+36` | constant | 0xC3F09EE9 (3287326441) |
| `+40` | constant | 0xFAE7EDC1 (4209503681) |
| `+44` | constant | 0x428E729E (1116631710) |

### `CR15HeadlookCRWin10`

0xB5C9447587FF7EF8 — stride **392**, 1 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x9F2C73C3 (2670490563) |
| `+4` | constant | 0xC9B88A06 (3384314374) |
| `+8` | constant | 0xF2CA89C8 (4073359816) |
| `+12` | constant | 0xF1CA42A6 (4056564390) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x9F2C73C3 (2670490563) |
| `+36` | constant | 0xC9B88A06 (3384314374) |
| `+40` | constant | 0x32761FB4 (846602164) |
| `+44` | constant | 0xC8C33E48 (3368238664) |
| `+48` | constant | 0x6C0A841B (1812628507) |
| `+52` | constant | 0x2429F0B6 (606728374) |
| `+56` | constant | 0x9F2A86EB (2670364395) |
| `+60` | constant | 0x6D87E5BE (1837622718) |
| `+64` | constant | 0x7F9D07AA (2140997546) |
| `+68` | constant | 0x4FAE8119 (1336836377) |
| `+72` | constant | 0x7F9D1EBC (2141003452) |
| `+76` | constant | 0x4FAE8119 (1336836377) |
| `+80` | constant | 0x27FA876D (670730093) |
| `+84` | constant | 0x3EE89513 (1055429907) |
| `+88` | constant | 0x41F00000 (1106247680) |
| `+92` | constant | 0x3EA3D70A (1050924810) |
| `+96` | constant | 0x41400000 (1094713344) |
| `+100` | constant | 0x41800000 (1098907648) |
| `+104` | constant | 0x40600000 (1080033280) |
| `+108` | zero |  |
| `+112` | constant | 0x3141D8EA (826398954) |
| `+116` | constant | 0x387FC835 (947898421) |
| `+120` | constant | 0xBF9C61A6 (3214696870) |
| `+124` | constant | 0x3F9C61A6 (1067213222) |
| `+128` | constant | 0x3E24FA48 (1042610760) |
| `+132` | constant | 0x3F56C15D (1062650205) |
| `+136` | constant | 0x3EB2B8C7 (1051900103) |
| `+140` | constant | 0x4032B8D0 (1077065936) |
| `+144` | constant | 0x3F451EB8 (1061494456) |
| `+148` | constant | 0xBF451EB8 (3208978104) |
| `+152` | constant | 0x5FB938C6 (1605974214) |
| `+156` | constant | 0xF7E6FAB2 (4159109810) |
| `+160` | 0xFFFFFFFF |  |
| `+164` | 0xFFFFFFFF |  |
| `+168` | 0xFFFFFFFF |  |
| `+172` | 0xFFFFFFFF |  |
| `+176` | 0xFFFFFFFF |  |
| `+180` | 0xFFFFFFFF |  |
| `+184` | 0xFFFFFFFF |  |
| `+188` | 0xFFFFFFFF |  |
| `+192` | constant | 0x46E8C257 (1189659223) |
| `+196` | constant | 0x5EEEC230 (1592705584) |
| `+200` | constant | 0x40000000 (1073741824) |
| `+204` | constant | 0xBFC8F5C3 (3217618371) |
| `+208` | constant | 0x3FC8F5C3 (1070134723) |
| `+212` | constant | 0x3F800000 (1065353216) |
| `+216` | zero |  |
| `+220` | constant | 0x3E32B8C7 (1043511495) |
| `+224` | constant | 0x403DE450 (1077797968) |
| `+228` | constant | 0x3F800000 (1065353216) |
| `+232` | constant | 0xBF800000 (3212836864) |
| `+236` | zero |  |
| `+240` | 0xFFFFFFFF |  |
| `+244` | 0xFFFFFFFF |  |
| `+248` | 0xFFFFFFFF |  |
| `+252` | 0xFFFFFFFF |  |
| `+256` | 0xFFFFFFFF |  |
| `+260` | 0xFFFFFFFF |  |
| `+264` | constant | 0xDD06EF3C (3708219196) |
| `+268` | constant | 0xEF4FBBC9 (4014980041) |
| `+272` | constant | 0x3F000000 (1056964608) |
| `+276` | constant | 0x3F800000 (1065353216) |
| `+280` | 0xFFFFFFFF |  |
| `+284` | 0xFFFFFFFF |  |
| `+288` | 0xFFFFFFFF |  |
| `+292` | 0xFFFFFFFF |  |
| `+296` | constant | 0x1A78B248 (444117576) |
| `+300` | constant | 0x9127C976 (2435303798) |
| `+304` | 0xFFFFFFFF |  |
| `+308` | 0xFFFFFFFF |  |
| `+312` | zero |  |
| `+316` | zero |  |
| `+320` | zero |  |
| `+324` | zero |  |
| `+328` | zero |  |
| `+332` | zero |  |
| `+336` | zero |  |
| `+340` | constant | 0x00000001 (1) |
| `+344` | constant | 0x00000020 (32) |
| `+348` | zero |  |
| `+352` | zero |  |
| `+356` | zero |  |
| `+360` | zero |  |
| `+364` | zero |  |
| `+368` | 0xFFFFFFFF |  |
| `+372` | 0xFFFFFFFF |  |
| `+376` | constant | 0x579C4468 (1469858920) |
| `+380` | constant | 0x2B132AD6 (722676438) |
| `+384` | constant | 0x4061DB23 (1080154915) |
| `+388` | constant | 0x40B0ED91 (1085336977) |

### `CR15HeraldryCRWin10`

0x6946E9356647EC70 — stride **112**, 16 entries sampled, 5 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xC7B0FDBB (3350265275) |
| `+4` | constant | 0xE2E1BF50 (3806445392) |
| `+8` | actor ref | 16/16 resolve |
| `+12` | hash-like | 12 distinct of 16 |
| `+16` | mixed | 3 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | mixed | 2 distinct |
| `+36` | mixed | 2 distinct |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | mixed | 2 distinct |
| `+52` | mixed | 2 distinct |
| `+56` | mixed | 3 distinct |
| `+60` | mixed | 3 distinct |
| `+64` | mixed | 3 distinct |
| `+68` | mixed | 3 distinct |
| `+72` | mixed | 3 distinct |
| `+76` | mixed | 3 distinct |
| `+80` | mixed | 3 distinct |
| `+84` | mixed | 3 distinct |
| `+88` | mixed | 3 distinct |
| `+92` | mixed | 3 distinct |
| `+96` | mixed | 3 distinct |
| `+100` | mixed | 3 distinct |
| `+104` | mixed | 4 distinct |
| `+108` | mixed | 4 distinct |

### `CR15ImpulseToHandTouchTypeCRWin10`

0x7A7E782B928652BC — stride **32**, 38 entries sampled, 9 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xE149376D (3779671917) |
| `+4` | constant | 0x82A3EB45 (2191780677) |
| `+8` | actor ref | 38/38 resolve |
| `+12` | hash-like | 34 distinct of 38 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | constant | 0x00000001 (1) |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |

### `CR15InputPointerCRWin10`

0x0B23AA6E4497C21E — stride **32**, 3 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x1550937A (357602170) |
| `+4` | constant | 0xA64CDA33 (2790054451) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15InteractOutputCRWin10`

0x4E0D508C8911AB6A — stride **32**, 52 entries sampled, 11 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 6 distinct |
| `+4` | hash-like | 52 distinct of 52 |
| `+8` | actor ref | 52/52 resolve |
| `+12` | hash-like | 39 distinct of 52 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | constant | 0x00000001 (1) |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15KillVolCRWin10`

0xDC08BBD0E7AD56F4 — stride **24**, 30 entries sampled, 2 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | actor ref (partial) | 18/30 resolve |
| `+4` | mixed | 3 distinct |
| `+8` | mixed | 5 distinct |
| `+12` | float32 | range 0.000 .. 0.000 |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | float32 | range -41.701 .. 0.000 |

### `CR15LevelTouchInteractCRWin10`

0xBEF368F4F866CFAC — stride **32**, 100 entries sampled, 2 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range -0.000 .. -0.000 |
| `+4` | float32 | range 0.000 .. 966.047 |
| `+8` | constant | 0xCA666177 (3395707255) |
| `+12` | constant | 0xB42C6CE4 (3022810340) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | constant | 0x00000001 (1) |
| `+28` | zero |  |

### `CR15LinearConstraintTouchInteractCRWin10`

0x10E2D7FF635E6162 — stride **288**, 38 entries sampled, 8 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x3B500BCA (995101642) |
| `+4` | constant | 0x9A6F1E83 (2590973571) |
| `+8` | actor ref | 38/38 resolve |
| `+12` | hash-like | 34 distinct of 38 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x3B500BCA (995101642) |
| `+36` | constant | 0x9A6F1E83 (2590973571) |
| `+40` | 0xFFFFFFFF |  |
| `+44` | 0xFFFFFFFF |  |
| `+48` | 0xFFFFFFFF |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | zero |  |
| `+64` | float32 | range 0.000 .. 0.000 |
| `+68` | zero |  |
| `+72` | float32 | range -1.588 .. 1.588 |
| `+76` | float32 | range 0.000 .. 2.125 |
| `+80` | float32 | range 0.000 .. 18.368 |
| `+84` | float32 | range -0.138 .. 0.138 |
| `+88` | float32 | range 0.000 .. 2.125 |
| `+92` | float32 | range 0.000 .. 18.368 |
| `+96` | actor ref (partial) | 32/38 resolve |
| `+100` | hash-like | 33 distinct of 38 |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | mixed | 2 distinct |
| `+116` | hash-like | 33 distinct of 38 |
| `+120` | actor ref (partial) | 32/38 resolve |
| `+124` | hash-like | 33 distinct of 38 |
| `+128` | zero |  |
| `+132` | zero |  |
| `+136` | mixed | 2 distinct |
| `+140` | mixed | 2 distinct |
| `+144` | zero |  |
| `+148` | constant | 0x3F000000 (1056964608) |
| `+152` | constant | 0x3F733333 (1064514355) |
| `+156` | zero |  |
| `+160` | zero |  |
| `+164` | zero |  |
| `+168` | 0xFFFFFFFF |  |
| `+172` | 0xFFFFFFFF |  |
| `+176` | 0xFFFFFFFF |  |
| `+180` | 0xFFFFFFFF |  |
| `+184` | 0xFFFFFFFF |  |
| `+188` | zero |  |
| `+192` | constant | 0x00000020 (32) |
| `+196` | zero |  |
| `+200` | zero |  |
| `+204` | zero |  |
| `+208` | zero |  |
| `+212` | zero |  |
| `+216` | zero |  |
| `+220` | zero |  |
| `+224` | zero |  |
| `+228` | zero |  |
| `+232` | zero |  |
| `+236` | zero |  |
| `+240` | zero |  |
| `+244` | constant | 0x3F800000 (1065353216) |
| `+248` | zero |  |
| `+252` | zero |  |
| `+256` | zero |  |
| `+260` | constant | 0x3F800000 (1065353216) |
| `+264` | zero |  |
| `+268` | zero |  |
| `+272` | zero |  |
| `+276` | zero |  |
| `+280` | float32 | range 0.000 .. 0.000 |
| `+284` | zero |  |

### `CR15LinearPositionConstraintCRWin10`

0x68C32C04284FB022 — stride **136**, 44 entries sampled, 10 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 3 distinct |
| `+4` | hash-like | 44 distinct of 44 |
| `+8` | actor ref | 44/44 resolve |
| `+12` | hash-like | 37 distinct of 44 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 0.000 |
| `+36` | zero |  |
| `+40` | float32 | range -1.588 .. 1.588 |
| `+44` | float32 | range 0.000 .. 2.125 |
| `+48` | float32 | range 0.000 .. 18.368 |
| `+52` | float32 | range -0.138 .. 0.138 |
| `+56` | float32 | range 0.000 .. 2.125 |
| `+60` | float32 | range 0.000 .. 18.368 |
| `+64` | actor ref (partial) | 32/44 resolve |
| `+68` | hash-like | 33 distinct of 44 |
| `+72` | zero |  |
| `+76` | zero |  |
| `+80` | mixed | 2 distinct |
| `+84` | hash-like | 33 distinct of 44 |
| `+88` | actor ref (partial) | 32/44 resolve |
| `+92` | hash-like | 33 distinct of 44 |
| `+96` | zero |  |
| `+100` | zero |  |
| `+104` | mixed | 2 distinct |
| `+108` | mixed | 2 distinct |
| `+112` | zero |  |
| `+116` | constant | 0x3F000000 (1056964608) |
| `+120` | constant | 0x3F733333 (1064514355) |
| `+124` | zero |  |
| `+128` | zero |  |
| `+132` | zero |  |

### `CR15MenuPlayerCRWin10`

0x509CAE81E47525E2 — stride **104**, 2 entries sampled, 2 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x132A9850 (321558608) |
| `+4` | constant | 0xAA8A3EA7 (2861186727) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | mixed | 2 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | constant | 0x00000160 (352) |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | constant | 0x00000004 (4) |
| `+76` | zero |  |
| `+80` | constant | 0x00000004 (4) |
| `+84` | zero |  |
| `+88` | constant | 0x00000001 (1) |
| `+92` | zero |  |
| `+96` | zero |  |
| `+100` | constant | 0x3F800000 (1065353216) |

### `CR15MenuTooltipCRWin10`

0x0E27ECC8FEE165FA — stride **40**, 3 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xEAA6CFAA (3936800682) |
| `+4` | constant | 0xE709B5F0 (3876173296) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x2E7606A4 (779486884) |
| `+36` | constant | 0xC8C33E48 (3368238664) |

### `CR15MirrorPlayerCRWin10`

0x61E3C722C53615DE — stride **184**, 1 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x2D603EFC (761282300) |
| `+4` | constant | 0x4A8D54BE (1250776254) |
| `+8` | constant | 0x2AA1190D (715200781) |
| `+12` | constant | 0x2385B762 (595965794) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x65135F29 (1695768361) |
| `+36` | constant | 0x6E11D3A4 (1846662052) |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | constant | 0x8D260515 (2368079125) |
| `+52` | constant | 0xABFE651C (2885575964) |
| `+56` | constant | 0x3E4CCCCD (1045220557) |
| `+60` | constant | 0x00000001 (1) |
| `+64` | zero |  |
| `+68` | zero |  |
| `+72` | zero |  |
| `+76` | zero |  |
| `+80` | zero |  |
| `+84` | zero |  |
| `+88` | constant | 0x3F800000 (1065353216) |
| `+92` | zero |  |
| `+96` | zero |  |
| `+100` | zero |  |
| `+104` | constant | 0xC1C03DBC (3250601404) |
| `+108` | constant | 0x3FAD3AFC (1068317436) |
| `+112` | constant | 0x415E2FDE (1096691678) |
| `+116` | constant | 0x3D2E1EF7 (1026432759) |
| `+120` | zero |  |
| `+124` | constant | 0x43200000 (1126170624) |
| `+128` | constant | 0xC1C03DBC (3250601404) |
| `+132` | constant | 0xBD28D190 (3173568912) |
| `+136` | constant | 0x415E2FDE (1096691678) |
| `+140` | constant | 0x3FF5D945 (1073076549) |
| `+144` | constant | 0x1530024D (355467853) |
| `+148` | constant | 0x41D2C62D (1104332333) |
| `+152` | constant | 0xC1D435D5 (3251910101) |
| `+156` | constant | 0x4609C833 (1175046195) |
| `+160` | 0xFFFFFFFF |  |
| `+164` | 0xFFFFFFFF |  |
| `+168` | constant | 0x400CCCCD (1074580685) |
| `+172` | zero |  |
| `+176` | constant | 0xBF000000 (3204448256) |
| `+180` | zero |  |

### `CR15NetAIThrowCRWin10`

0x5BE0BFEE054DB944 — stride **32**, 1 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x3417AEC5 (873967301) |
| `+4` | constant | 0x9C91E2D9 (2626806489) |
| `+8` | constant | 0x1860D487 (408999047) |
| `+12` | constant | 0x7E1E9B7C (2115935100) |
| `+16` | constant | 0x000AFFFF (720895) |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetAIWaypointCRWin10`

0xC0F1EE3AC3B0C412 — stride **96**, 118 entries sampled, 1 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x751CF10C (1964830988) |
| `+4` | constant | 0x09FEC4BF (167691455) |
| `+8` | actor ref | 118/118 resolve |
| `+12` | mixed | 51 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 0.000 |
| `+36` | zero |  |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | float32 | range 0.000 .. 0.000 |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | zero |  |
| `+64` | zero |  |
| `+68` | constant | 0x00000001 (1) |
| `+72` | constant | 0x00000020 (32) |
| `+76` | zero |  |
| `+80` | float32 | range 0.000 .. 0.000 |
| `+84` | zero |  |
| `+88` | float32 | range 0.000 .. 0.000 |
| `+92` | zero |  |

### `CR15NetActorCRWin10`

0x24C73CB2EBE38BFC — stride **32**, 96 entries sampled, 10 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x903773DA (2419553242) |
| `+4` | constant | 0xCFB89F03 (3484983043) |
| `+8` | actor ref | 96/96 resolve |
| `+12` | mixed | 24 distinct |
| `+16` | mixed | 10 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetAimAssistCRWin10`

0xBD8575CC9885E590 — stride **48**, 75 entries sampled, 5 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x11BBC952 (297519442) |
| `+4` | constant | 0xCC9E89D8 (3432942040) |
| `+8` | actor ref | 75/75 resolve |
| `+12` | mixed | 15 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | constant | 0x00000001 (1) |
| `+28` | zero |  |
| `+32` | constant | 0x41200000 (1092616192) |
| `+36` | constant | 0x3E800000 (1048576000) |
| `+40` | constant | 0x41A00000 (1101004800) |
| `+44` | constant | 0x3E800000 (1048576000) |

### `CR15NetAutoTargetCRWin10`

0x975113E786B2C0A4 — stride **104**, 5 entries sampled, 5 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x47891A54 (1200167508) |
| `+4` | constant | 0x48CF074C (1221527372) |
| `+8` | constant | 0xA9BF5A13 (2847889939) |
| `+12` | constant | 0x10E40287 (283378311) |
| `+16` | constant | 0x000FFFFF (1048575) |
| `+20` | zero |  |
| `+24` | constant | 0x00000001 (1) |
| `+28` | zero |  |
| `+32` | constant | 0x41A00000 (1101004800) |
| `+36` | constant | 0x3E99999A (1050253722) |
| `+40` | constant | 0x40000000 (1073741824) |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x3F800000 (1065353216) |
| `+64` | constant | 0xC03FDFE6 (3225411558) |
| `+68` | constant | 0x3D954D3A (1033194810) |
| `+72` | constant | 0x40093229 (1074344489) |
| `+76` | constant | 0x3F800000 (1065353216) |
| `+80` | constant | 0xD40B806E (3557523566) |
| `+84` | constant | 0xA8F84E7D (2834845309) |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | 0xFFFFFFFF |  |
| `+100` | 0xFFFFFFFF |  |

### `CR15NetBalanceSettingsCRWin10`

0x0FB620E994D00128 — stride **88**, 25 entries sampled, 7 level(s), pooled, 1 inline table(s) at +32

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x631643B7 (1662403511) |
| `+4` | constant | 0x79682648 (2036868680) |
| `+8` | actor ref | 25/25 resolve |
| `+12` | mixed | 9 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | float32 | range 0.000 .. 0.000 |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | float32 | range 0.000 .. 0.000 |
| `+76` | zero |  |
| `+80` | float32 | range 0.000 .. 0.000 |
| `+84` | zero |  |

### `CR15NetBitFieldCRWin10`

0xF460DAE1C4C8071C — stride **24**, 600 entries sampled, 5 level(s), **byte-identical across levels**, pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 5 distinct |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | mixed | 5 distinct |
| `+12` | mixed | 2 distinct |
| `+16` | actor ref (partial) | 565/600 resolve |
| `+20` | mixed | 115 distinct |

### `CR15NetBoosterCRWin10`

0xFB92FB2F6F6D1F58 — stride **32**, 6 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x2F11A4C0 (789685440) |
| `+4` | constant | 0x9C92E4C2 (2626872514) |
| `+8` | actor ref | 6/6 resolve |
| `+12` | hash-like | 6 distinct of 6 |
| `+16` | mixed | 3 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetBotAnimCRWin10`

0x5304C307D5B08742 — stride **32**, 2 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x3D0BA8DF (1024174303) |
| `+4` | constant | 0x9C92E4D9 (2626872537) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | mixed | 2 distinct |
| `+16` | constant | 0x000AFFFF (720895) |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetBotCRWin10`

0x12BD2C7D6BF71FDC — stride **32**, 2 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x9B2A499C (2603239836) |
| `+4` | constant | 0x7A804A0A (2055227914) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | mixed | 2 distinct |
| `+16` | constant | 0x000AFFFF (720895) |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetBulletCRWin10`

0x4FD65C8D2B63D470 — stride **136**, 45 entries sampled, 5 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x0E07C1B2 (235389362) |
| `+4` | constant | 0x48037CDE (1208188126) |
| `+8` | actor ref | 45/45 resolve |
| `+12` | mixed | 9 distinct |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range 60.000 .. 155.000 |
| `+36` | float32 | range 1.000 .. 5.000 |
| `+40` | float32 | range 3.000 .. 5.000 |
| `+44` | float32 | range 0.000 .. 1.000 |
| `+48` | float32 | range 0.080 .. 1.500 |
| `+52` | float32 | range 1.000 .. 3.000 |
| `+56` | float32 | range 0.000 .. 0.200 |
| `+60` | float32 | range 1.000 .. 20.000 |
| `+64` | float32 | range 10.000 .. 60.000 |
| `+68` | zero |  |
| `+72` | constant | 0x00000001 (1) |
| `+76` | zero |  |
| `+80` | mixed | 4 distinct |
| `+84` | mixed | 4 distinct |
| `+88` | mixed | 2 distinct |
| `+92` | mixed | 2 distinct |
| `+96` | mixed | 3 distinct |
| `+100` | mixed | 3 distinct |
| `+104` | mixed | 3 distinct |
| `+108` | mixed | 3 distinct |
| `+112` | 0xFFFFFFFF |  |
| `+116` | 0xFFFFFFFF |  |
| `+120` | mixed | 2 distinct |
| `+124` | mixed | 2 distinct |
| `+128` | mixed | 2 distinct |
| `+132` | mixed | 2 distinct |

### `CR15NetCaptureVolumeCRWin10`

0x93A1C9EAAAA00B44 — stride **96**, 2 entries sampled, 2 level(s), pooled, 1 inline table(s) at +32

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xBCF99B89 (3170474889) |
| `+4` | constant | 0x53300AE1 (1395657441) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | mixed | 2 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | float32 | range 0.000 .. 0.000 |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | float32 | range 0.000 .. 0.000 |
| `+76` | zero |  |
| `+80` | float32 | range 0.000 .. 0.000 |
| `+84` | zero |  |
| `+88` | float32 | range -0.000 .. 55116.934 |
| `+92` | float32 | range -0.000 .. 0.000 |

### `CR15NetCustomizationCRWin10`

0x6E03CA8E7F468AAA — stride **128**, 18 entries sampled, 7 level(s), pooled, 1 inline table(s) at +72

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x0AC4407E (180633726) |
| `+4` | constant | 0x2DD53481 (768947329) |
| `+8` | actor ref | 18/18 resolve |
| `+12` | hash-like | 14 distinct of 18 |
| `+16` | mixed | 3 distinct |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | mixed | 2 distinct |
| `+36` | mixed | 2 distinct |
| `+40` | mixed | 2 distinct |
| `+44` | mixed | 2 distinct |
| `+48` | mixed | 2 distinct |
| `+52` | mixed | 2 distinct |
| `+56` | mixed | 2 distinct |
| `+60` | mixed | 2 distinct |
| `+64` | float32 | range 0.000 .. 0.000 |
| `+68` | float32 | range 0.000 .. 1.250 |
| `+72` | zero |  |
| `+76` | zero |  |
| `+80` | float32 | range 0.000 .. 0.000 |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | zero |  |
| `+100` | constant | 0x00000001 (1) |
| `+104` | constant | 0x00000020 (32) |
| `+108` | zero |  |
| `+112` | float32 | range 0.000 .. 0.000 |
| `+116` | zero |  |
| `+120` | float32 | range 0.000 .. 0.000 |
| `+124` | zero |  |

### `CR15NetDamageableCRWin10`

0xBD1868F576836696 — stride **56**, 97 entries sampled, 7 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xC2F5920F (3270873615) |
| `+4` | constant | 0xC9DC08CF (3386640591) |
| `+8` | actor ref | 97/97 resolve |
| `+12` | hash-like | 63 distinct of 97 |
| `+16` | mixed | 4 distinct |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 112.000 |
| `+36` | float32 | range 7.000 .. 9999.000 |
| `+40` | float32 | range 0.000 .. 170000.000 |
| `+44` | float32 | range 0.000 .. 999.000 |
| `+48` | mixed | 10 distinct |
| `+52` | mixed | 7 distinct |

### `CR15NetDebugDrawCRWin10`

0x079586E19869A090 — stride **32**, 516 entries sampled, 4 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x3C690593 (1013515667) |
| `+4` | constant | 0x46AC22D2 (1185686226) |
| `+8` | actor ref | 516/516 resolve |
| `+12` | mixed | 129 distinct |
| `+16` | mixed | 7 distinct |
| `+20` | constant | 0x00000001 (1) |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetDebugInputRecorderCRWin10`

0x1FC097A004E12724 — stride **32**, 1 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x6CB29C7C (1823644796) |
| `+4` | constant | 0x4C5486E5 (1280607973) |
| `+8` | constant | 0x0DDE0312 (232653586) |
| `+12` | constant | 0xF1E70ACD (4058450637) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetDynamicCoverCRWin10`

0x42D101B2307BBC16 — stride **32**, 5 entries sampled, 5 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x45DAED36 (1171975478) |
| `+4` | constant | 0xFD0FD898 (4245674136) |
| `+8` | constant | 0xD5292926 (3576244518) |
| `+12` | constant | 0x3D8B8CA4 (1032555684) |
| `+16` | constant | 0x001FFFFF (2097151) |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetDynamicPosterCRWin10`

0x267366A86FEEC098 — stride **72**, 59 entries sampled, 4 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x09D229E0 (164768224) |
| `+4` | constant | 0x5915C961 (1494600033) |
| `+8` | actor ref | 59/59 resolve |
| `+12` | hash-like | 36 distinct of 59 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0xB2F74B01 (3002551041) |
| `+36` | constant | 0xEB899520 (3951662368) |
| `+40` | constant | 0x051B2F23 (85667619) |
| `+44` | float32 | range -0.000 .. -0.000 |
| `+48` | float32 | range 0.000 .. 0.000 |
| `+52` | zero |  |
| `+56` | 0xFFFFFFFF |  |
| `+60` | 0xFFFFFFFF |  |
| `+64` | constant | 0x2E3D410E (775766286) |
| `+68` | constant | 0x80011E8D (2147557005) |

### `CR15NetExplosionCRWin10`

0x4AF215B19B297C34 — stride **96**, 48 entries sampled, 6 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 7 distinct |
| `+4` | mixed | 7 distinct |
| `+8` | actor ref | 48/48 resolve |
| `+12` | mixed | 10 distinct |
| `+16` | mixed | 5 distinct |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range 0.500 .. 3.000 |
| `+36` | float32 | range 0.000 .. 3.000 |
| `+40` | float32 | range 0.000 .. 40.000 |
| `+44` | float32 | range 0.000 .. 40.000 |
| `+48` | float32 | range 0.000 .. 1.100 |
| `+52` | float32 | range 0.500 .. 1.000 |
| `+56` | zero |  |
| `+60` | float32 | range 0.000 .. 1.800 |
| `+64` | float32 | range 0.000 .. 1.800 |
| `+68` | float32 | range 0.000 .. 5.000 |
| `+72` | float32 | range 0.000 .. 3.000 |
| `+76` | float32 | range 0.000 .. 1.000 |
| `+80` | mixed | 9 distinct |
| `+84` | mixed | 7 distinct |
| `+88` | mixed | 2 distinct |
| `+92` | mixed | 2 distinct |

### `CR15NetFollowPlayerCRWin10`

0xA7BCBF4B204A8916 — stride **40**, 5 entries sampled, 5 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x1CC3E962 (482601314) |
| `+4` | constant | 0x718D4EF0 (1905086192) |
| `+8` | constant | 0xA9BF5A13 (2847889939) |
| `+12` | constant | 0x10E40287 (283378311) |
| `+16` | constant | 0x000FFFFF (1048575) |
| `+20` | zero |  |
| `+24` | constant | 0x00000001 (1) |
| `+28` | zero |  |
| `+32` | constant | 0x3FC00000 (1069547520) |
| `+36` | constant | 0x40200000 (1075838976) |

### `CR15NetFrisbeeTrailCRWin10`

0xD6FE68E19923FE44 — stride **48**, 2 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xFABB3322 (4206572322) |
| `+4` | constant | 0xF78683DE (4152787934) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | float32 | range -0.000 .. 0.000 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x6D14F68A (1830090378) |
| `+36` | constant | 0x8EBF34AC (2394895532) |
| `+40` | constant | 0xB276ED03 (2994138371) |
| `+44` | constant | 0xDC009ABA (3691027130) |

### `CR15NetGhostLODCRWin10`

0x012B9CC38A29755A — stride **88**, 2238 entries sampled, 6 level(s), pooled, 1 inline table(s) at +32

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x75FF883C (1979680828) |
| `+4` | constant | 0x794F2561 (2035230049) |
| `+8` | actor ref | 2238/2238 resolve |
| `+12` | mixed | 568 distinct |
| `+16` | mixed | 10 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | float32 | range 0.000 .. 0.000 |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | float32 | range 0.000 .. 0.000 |
| `+76` | zero |  |
| `+80` | float32 | range 0.000 .. 0.000 |
| `+84` | zero |  |

### `CR15NetGunCRWin10`

0xDBAED509C67B8884 — stride **160**, 600 entries sampled, 5 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x9B2F5386 (2603570054) |
| `+4` | constant | 0x7A804A0A (2055227914) |
| `+8` | actor ref | 600/600 resolve |
| `+12` | mixed | 120 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 0.000 |
| `+36` | zero |  |
| `+40` | constant | 0x5BC85873 (1539856499) |
| `+44` | constant | 0xEB587BB2 (3948444594) |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x3F800000 (1065353216) |
| `+64` | constant | 0xC0400000 (3225419776) |
| `+68` | constant | 0xC2B40000 (3266576384) |
| `+72` | mixed | 8 distinct |
| `+76` | mixed | 8 distinct |
| `+80` | actor ref | 600/600 resolve |
| `+84` | mixed | 120 distinct |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | constant | 0x8D260515 (2368079125) |
| `+100` | constant | 0xABFE651C (2885575964) |
| `+104` | actor ref | 600/600 resolve |
| `+108` | mixed | 8 distinct |
| `+112` | zero |  |
| `+116` | zero |  |
| `+120` | 0xFFFFFFFF |  |
| `+124` | 0xFFFFFFFF |  |
| `+128` | zero |  |
| `+132` | float32 | range 0.500 .. 1.000 |
| `+136` | 0xFFFFFFFF |  |
| `+140` | 0xFFFFFFFF |  |
| `+144` | float32 | range 0.500 .. 10.000 |
| `+148` | float32 | range 0.050 .. 0.150 |
| `+152` | constant | 0x41200000 (1092616192) |
| `+156` | constant | 0x00000001 (1) |

### `CR15NetHealBubbleCRWin10`

0x1AD01A2A91709A1A — stride **72**, 3 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x3F3DEBD1 (1061022673) |
| `+4` | constant | 0xB1FDA2A8 (2986189480) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | hash-like | 3 distinct of 3 |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x41100000 (1091567616) |
| `+36` | constant | 0x41000000 (1090519040) |
| `+40` | constant | 0x42D60000 (1121320960) |
| `+44` | constant | 0x3F800000 (1065353216) |
| `+48` | 0xFFFFFFFF |  |
| `+52` | 0xFFFFFFFF |  |
| `+56` | zero |  |
| `+60` | zero |  |
| `+64` | zero |  |
| `+68` | zero |  |

### `CR15NetHealBubbleTargetCRWin10`

0xFA06459149D98D30 — stride **32**, 3 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x55E0F3F8 (1440805880) |
| `+4` | constant | 0xFE890DF4 (4270394868) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | hash-like | 3 distinct of 3 |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetHoloBitCRWin10`

0xB3DE608077189596 — stride **32**, 52 entries sampled, 2 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x3307A8C6 (856139974) |
| `+4` | constant | 0x9C98E4C1 (2627265729) |
| `+8` | actor ref | 52/52 resolve |
| `+12` | mixed | 26 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetIdCRWin10`

0xC29715D62E039402 — stride **32**, 1139 entries sampled, 11 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x2CEB8D62 (753634658) |
| `+4` | constant | 0x24BCC119 (616349977) |
| `+8` | actor ref | 1139/1139 resolve |
| `+12` | hash-like | 594 distinct of 1139 |
| `+16` | mixed | 3 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetKillTickerCRWin10`

0xF289249001A4BED6 — stride **32**, 4 entries sampled, 4 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x4C41A8F2 (1279371506) |
| `+4` | constant | 0x9E205E04 (2652921348) |
| `+8` | constant | 0x86155095 (2249543829) |
| `+12` | constant | 0xA8ADFF57 (2829975383) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetMagazineCRWin10`

0x5B723F040A3778EE — stride **40**, 35 entries sampled, 5 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x827D6823 (2189256739) |
| `+4` | constant | 0xD5B9A165 (3585712485) |
| `+8` | actor ref | 35/35 resolve |
| `+12` | mixed | 5 distinct |
| `+16` | constant | 0x000FFFFF (1048575) |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | constant | 0x41F00000 (1106247680) |
| `+36` | constant | 0x461C3C00 (1176255488) |

### `CR15NetMagazinePouchCRWin10`

0x2D90EE7BD8175EC2 — stride **96**, 3 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x931AC558 (2468005208) |
| `+4` | constant | 0x5635E33F (1446372159) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | hash-like | 3 distinct of 3 |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x00000001 (1) |
| `+36` | zero |  |
| `+40` | constant | 0x00000001 (1) |
| `+44` | zero |  |
| `+48` | float32 | range 0.000 .. 0.320 |
| `+52` | float32 | range 0.000 .. 0.550 |
| `+56` | float32 | range 0.000 .. 0.600 |
| `+60` | float32 | range 0.000 .. 100.000 |
| `+64` | float32 | range 0.000 .. 0.320 |
| `+68` | float32 | range 0.000 .. 0.300 |
| `+72` | constant | 0x4AE084D9 (1256228057) |
| `+76` | constant | 0xC946F722 (3376871202) |
| `+80` | mixed | 2 distinct |
| `+84` | mixed | 2 distinct |
| `+88` | mixed | 2 distinct |
| `+92` | mixed | 2 distinct |

### `CR15NetMetricsCRWin10`

0x635114B61DE7E264 — stride **32**, 2 entries sampled, 2 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x2E0CA2C1 (772580033) |
| `+4` | constant | 0x9C9DEED9 (2627595993) |
| `+8` | constant | 0x74EF3A32 (1961835058) |
| `+12` | constant | 0x7299EC72 (1922690162) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetPackageDownloadCRWin10`

0x9C61645215480A4A — stride **40**, 3 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x17B22C9C (397552796) |
| `+4` | constant | 0x70E1FF2F (1893859119) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | float32 | range -0.000 .. 0.000 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x591C60FF (1495032063) |
| `+36` | constant | 0x80603893 (2153789587) |

### `CR15NetPayloadCRWin10`

0x3850C0283892236E — stride **72**, 2 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x300AA0D6 (806002902) |
| `+4` | constant | 0x9C80EAD4 (2625694420) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | mixed | 2 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | constant | 0x00000020 (32) |
| `+28` | zero |  |
| `+32` | constant | 0x3F800000 (1065353216) |
| `+36` | constant | 0x3F800000 (1065353216) |
| `+40` | constant | 0x40C00000 (1086324736) |
| `+44` | constant | 0x3F800000 (1065353216) |
| `+48` | constant | 0x3F99999A (1067030938) |
| `+52` | constant | 0x3FA66666 (1067869798) |
| `+56` | constant | 0x3FACCCCD (1068289229) |
| `+60` | constant | 0x3F800000 (1065353216) |
| `+64` | constant | 0x3DCCCCCD (1036831949) |
| `+68` | constant | 0x3E4CCCCD (1045220557) |

### `CR15NetPhysicsCRWin10`

0x71131E0E3B8AA17C — stride **40**, 93 entries sampled, 10 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xF995E4A3 (4187350179) |
| `+4` | constant | 0x8FEE6557 (2414765399) |
| `+8` | actor ref | 93/93 resolve |
| `+12` | hash-like | 59 distinct of 93 |
| `+16` | mixed | 4 distinct |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | float32 | range -0.150 .. 0.000 |

### `CR15NetPlayerEquipmentCRWin10`

0x59105E9CAA27E82A — stride **64**, 3 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x84B03F6E (2226143086) |
| `+4` | constant | 0xCA14940C (3390346252) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | hash-like | 3 distinct of 3 |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x5F615B9E (1600215966) |
| `+36` | constant | 0x2FD69C8C (802593932) |
| `+40` | constant | 0x32761FBC (846602172) |
| `+44` | constant | 0xC8C33E48 (3368238664) |
| `+48` | constant | 0xA894E7FE (2828331006) |
| `+52` | constant | 0xC8E8D0B1 (3370700977) |
| `+56` | constant | 0x00000001 (1) |
| `+60` | zero |  |

### `CR15NetPlayerModelSwapperCRWin10`

0x8895458CDD7DA876 — stride **24**, 7 entries sampled, 2 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range 0.000 .. 0.000 |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | zero |  |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | float32 | range 0.000 .. 0.000 |

### `CR15NetPooledActorCRWin10`

0xC28A49E8E5639D32 — stride **40**, 10 entries sampled, 10 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xE1A26CE8 (3785518312) |
| `+4` | constant | 0x105121C3 (273752515) |
| `+8` | actor ref | 10/10 resolve |
| `+12` | mixed | 4 distinct |
| `+16` | mixed | 3 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 0.000 |
| `+36` | zero |  |

### `CR15NetPropertyModifiersCRWin10`

0xADBA2E358D0CE5C4 — stride **40**, 4 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xD5636AD5 (3580062421) |
| `+4` | constant | 0x81BD4A38 (2176666168) |
| `+8` | actor ref | 4/4 resolve |
| `+12` | hash-like | 4 distinct of 4 |
| `+16` | hash-like | 3 distinct of 4 |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x00000010 (16) |
| `+36` | zero |  |

### `CR15NetPunchCRWin10`

0x8EB18A4D55D0F590 — stride **96**, 2 entries sampled, 1 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x862D7FC0 (2251128768) |
| `+4` | constant | 0xCFB89F12 (3484983058) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | mixed | 2 distinct |
| `+16` | mixed | 2 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x00000001 (1) |
| `+36` | zero |  |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | constant | 0x00000420 (1056) |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | zero |  |
| `+64` | zero |  |
| `+68` | constant | 0x00000001 (1) |
| `+72` | constant | 0x00000020 (32) |
| `+76` | zero |  |
| `+80` | constant | 0x0000000C (12) |
| `+84` | zero |  |
| `+88` | constant | 0x0000000C (12) |
| `+92` | zero |  |

### `CR15NetPunchableCRWin10`

0xC0C407DB04436F3C — stride **48**, 30 entries sampled, 4 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xDFF5DEB5 (3757432501) |
| `+4` | constant | 0x8DBBE870 (2377902192) |
| `+8` | actor ref | 30/30 resolve |
| `+12` | hash-like | 26 distinct of 30 |
| `+16` | mixed | 3 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x40400000 (1077936128) |
| `+36` | zero |  |
| `+40` | mixed | 2 distinct |
| `+44` | mixed | 2 distinct |

### `CR15NetRemoteVolumeQueryCRWin10`

0x967DF5A2E169F9F0 — stride **88**, 4 entries sampled, 3 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xE345FFB5 (3813015477) |
| `+4` | constant | 0x56E237CF (1457665999) |
| `+8` | actor ref | 4/4 resolve |
| `+12` | hash-like | 3 distinct of 4 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 26.546 |
| `+36` | float32 | range -2.141 .. 0.000 |
| `+40` | float32 | range 0.000 .. 14.142 |
| `+44` | float32 | range 0.000 .. 7.500 |
| `+48` | zero |  |
| `+52` | float32 | range 0.000 .. 1.783 |
| `+56` | float32 | range -31.996 .. 0.000 |
| `+60` | zero |  |
| `+64` | zero |  |
| `+68` | zero |  |
| `+72` | constant | 0x3F800000 (1065353216) |
| `+76` | float32 | range 0.000 .. 18.000 |
| `+80` | float32 | range 0.000 .. 11.000 |
| `+84` | float32 | range 0.000 .. 40.000 |

### `CR15NetRewardItemCRWin10`

0x32F30FE361939DEE — stride **664**, 471 entries sampled, 1 level(s), pooled, 2 inline table(s) at +432, +488

| Offset | Kind | Detail |
|---|---|---|
| `+0` | hash-like | 471 distinct of 471 |
| `+4` | hash-like | 266 distinct of 471 |
| `+8` | constant | 0x066E6641 (107898433) |
| `+12` | constant | 0x4B333CE6 (1261649126) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | hash-like | 471 distinct of 471 |
| `+36` | hash-like | 266 distinct of 471 |
| `+40` | mixed | 7 distinct |
| `+44` | mixed | 66 distinct |
| `+48` | hash-like | 364 distinct of 471 |
| `+52` | hash-like | 307 distinct of 471 |
| `+56` | mixed | 147 distinct |
| `+60` | mixed | 71 distinct |
| `+64` | mixed | 38 distinct |
| `+68` | float32 | range 0.000 .. 0.000 |
| `+72` | zero |  |
| `+76` | zero |  |
| `+80` | zero |  |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | zero |  |
| `+100` | zero |  |
| `+104` | mixed | 18 distinct |
| `+108` | mixed | 17 distinct |
| `+112` | mixed | 7 distinct |
| `+116` | float32 | range -399858.250 .. 26.354 |
| `+120` | mixed | 2 distinct |
| `+124` | hash-like | 374 distinct of 471 |
| `+128` | hash-like | 404 distinct of 471 |
| `+132` | hash-like | 404 distinct of 471 |
| `+136` | 0xFFFFFFFF |  |
| `+140` | 0xFFFFFFFF |  |
| `+144` | mixed | 5 distinct |
| `+148` | mixed | 5 distinct |
| `+152` | mixed | 5 distinct |
| `+156` | mixed | 5 distinct |
| `+160` | mixed | 2 distinct |
| `+164` | hash-like | 322 distinct of 471 |
| `+168` | hash-like | 373 distinct of 471 |
| `+172` | hash-like | 319 distinct of 471 |
| `+176` | mixed | 122 distinct |
| `+180` | mixed | 36 distinct |
| `+184` | mixed | 10 distinct |
| `+188` | float32 | range 0.000 .. 0.000 |
| `+192` | zero |  |
| `+196` | zero |  |
| `+200` | zero |  |
| `+204` | zero |  |
| `+208` | zero |  |
| `+212` | zero |  |
| `+216` | zero |  |
| `+220` | zero |  |
| `+224` | zero |  |
| `+228` | zero |  |
| `+232` | mixed | 4 distinct |
| `+236` | mixed | 4 distinct |
| `+240` | mixed | 4 distinct |
| `+244` | mixed | 4 distinct |
| `+248` | mixed | 3 distinct |
| `+252` | mixed | 3 distinct |
| `+256` | float32 | range 0.000 .. 0.000 |
| `+260` | float32 | range 0.000 .. 0.000 |
| `+264` | mixed | 2 distinct |
| `+268` | zero |  |
| `+272` | zero |  |
| `+276` | zero |  |
| `+280` | zero |  |
| `+284` | zero |  |
| `+288` | zero |  |
| `+292` | zero |  |
| `+296` | mixed | 9 distinct |
| `+300` | mixed | 9 distinct |
| `+304` | float32 | range 0.000 .. 0.000 |
| `+308` | constant | 0x0000000F (15) |
| `+312` | zero |  |
| `+316` | float32 | range 0.000 .. 260.000 |
| `+320` | mixed | 127 distinct |
| `+324` | mixed | 219 distinct |
| `+328` | mixed | 17 distinct |
| `+332` | mixed | 18 distinct |
| `+336` | mixed | 15 distinct |
| `+340` | mixed | 7 distinct |
| `+344` | zero |  |
| `+348` | zero |  |
| `+352` | zero |  |
| `+356` | zero |  |
| `+360` | zero |  |
| `+364` | zero |  |
| `+368` | zero |  |
| `+372` | zero |  |
| `+376` | zero |  |
| `+380` | zero |  |
| `+384` | zero |  |
| `+388` | zero |  |
| `+392` | mixed | 3 distinct |
| `+396` | mixed | 3 distinct |
| `+400` | float32 | range 0.000 .. 1170.000 |
| `+404` | float32 | range 0.000 .. 2340.000 |
| `+408` | float32 | range 0.000 .. 2048.000 |
| `+412` | float32 | range 0.000 .. 220.000 |
| `+416` | float32 | range 0.000 .. 1360.000 |
| `+420` | float32 | range 0.000 .. 3975.000 |
| `+424` | float32 | range 0.000 .. 1024.000 |
| `+428` | float32 | range 0.000 .. 82.000 |
| `+432` | zero |  |
| `+436` | zero |  |
| `+440` | float32 | range 0.000 .. 0.000 |
| `+444` | zero |  |
| `+448` | zero |  |
| `+452` | zero |  |
| `+456` | zero |  |
| `+460` | constant | 0x00000001 (1) |
| `+464` | constant | 0x00000020 (32) |
| `+468` | zero |  |
| `+472` | float32 | range 0.000 .. 0.000 |
| `+476` | zero |  |
| `+480` | float32 | range 0.000 .. 0.000 |
| `+484` | zero |  |
| `+488` | zero |  |
| `+492` | zero |  |
| `+496` | float32 | range 0.000 .. 0.000 |
| `+500` | zero |  |
| `+504` | zero |  |
| `+508` | zero |  |
| `+512` | zero |  |
| `+516` | constant | 0x00000001 (1) |
| `+520` | constant | 0x00000020 (32) |
| `+524` | zero |  |
| `+528` | float32 | range 0.000 .. 0.000 |
| `+532` | zero |  |
| `+536` | float32 | range 0.000 .. 0.000 |
| `+540` | zero |  |
| `+544` | 0xFFFFFFFF |  |
| `+548` | 0xFFFFFFFF |  |
| `+552` | 0xFFFFFFFF |  |
| `+556` | 0xFFFFFFFF |  |
| `+560` | 0xFFFFFFFF |  |
| `+564` | 0xFFFFFFFF |  |
| `+568` | 0xFFFFFFFF |  |
| `+572` | 0xFFFFFFFF |  |
| `+576` | mixed | 44 distinct |
| `+580` | mixed | 44 distinct |
| `+584` | mixed | 44 distinct |
| `+588` | mixed | 44 distinct |
| `+592` | 0xFFFFFFFF |  |
| `+596` | 0xFFFFFFFF |  |
| `+600` | mixed | 28 distinct |
| `+604` | mixed | 28 distinct |
| `+608` | mixed | 28 distinct |
| `+612` | mixed | 28 distinct |
| `+616` | 0xFFFFFFFF |  |
| `+620` | 0xFFFFFFFF |  |
| `+624` | mixed | 77 distinct |
| `+628` | mixed | 84 distinct |
| `+632` | mixed | 77 distinct |
| `+636` | mixed | 84 distinct |
| `+640` | 0xFFFFFFFF |  |
| `+644` | 0xFFFFFFFF |  |
| `+648` | mixed | 28 distinct |
| `+652` | mixed | 28 distinct |
| `+656` | mixed | 28 distinct |
| `+660` | mixed | 28 distinct |

### `CR15NetRewardItemSorterCRWin10`

0xD629140F02C8DE38 — stride **112**, 21 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xE67FD224 (3867136548) |
| `+4` | constant | 0x4CB01426 (1286607910) |
| `+8` | actor ref | 21/21 resolve |
| `+12` | hash-like | 21 distinct of 21 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x00000001 (1) |
| `+36` | zero |  |
| `+40` | mixed | 2 distinct |
| `+44` | mixed | 2 distinct |
| `+48` | 0xFFFFFFFF |  |
| `+52` | 0xFFFFFFFF |  |
| `+56` | 0xFFFFFFFF |  |
| `+60` | 0xFFFFFFFF |  |
| `+64` | 0xFFFFFFFF |  |
| `+68` | 0xFFFFFFFF |  |
| `+72` | 0xFFFFFFFF |  |
| `+76` | 0xFFFFFFFF |  |
| `+80` | 0xFFFFFFFF |  |
| `+84` | 0xFFFFFFFF |  |
| `+88` | 0xFFFFFFFF |  |
| `+92` | 0xFFFFFFFF |  |
| `+96` | 0xFFFFFFFF |  |
| `+100` | 0xFFFFFFFF |  |
| `+104` | float32 | range 0.000 .. 0.000 |
| `+108` | zero |  |

### `CR15NetSensorCRWin10`

0x63D75F570B7C7B08 — stride **48**, 3 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x0C18CBB4 (202951604) |
| `+4` | constant | 0x48036DCE (1208184270) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | hash-like | 3 distinct of 3 |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | constant | 0x42480000 (1112014848) |
| `+44` | float32 | range 4.000 .. 4.500 |

### `CR15NetSensorTargetCRWin10`

0xE51718C1E4669474 — stride **32**, 51 entries sampled, 6 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xFBAA0D46 (4222225734) |
| `+4` | constant | 0xA7F862E8 (2818073320) |
| `+8` | actor ref | 51/51 resolve |
| `+12` | mixed | 21 distinct |
| `+16` | mixed | 3 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetSocialInteractCRWin10`

0x0BD005335676F466 — stride **32**, 6 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xD4F6EB74 (3572951924) |
| `+4` | constant | 0x0CAE2906 (212740358) |
| `+8` | actor ref | 6/6 resolve |
| `+12` | hash-like | 6 distinct of 6 |
| `+16` | mixed | 3 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetSpectatorCameraCRWin10`

0x0DBC9C94837220EE — stride **200**, 6 entries sampled, 6 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xA79233BD (2811376573) |
| `+4` | constant | 0x66845D16 (1719950614) |
| `+8` | actor ref | 6/6 resolve |
| `+12` | mixed | 3 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | constant | 0x00000B58 (2904) |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | constant | 0x00000021 (33) |
| `+76` | zero |  |
| `+80` | constant | 0x00000021 (33) |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | float32 | range 0.000 .. 0.000 |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | constant | 0x00000001 (1) |
| `+120` | constant | 0x00000020 (32) |
| `+124` | zero |  |
| `+128` | float32 | range 0.000 .. 0.000 |
| `+132` | zero |  |
| `+136` | float32 | range 0.000 .. 0.000 |
| `+140` | zero |  |
| `+144` | zero |  |
| `+148` | zero |  |
| `+152` | float32 | range 0.000 .. 0.000 |
| `+156` | zero |  |
| `+160` | zero |  |
| `+164` | zero |  |
| `+168` | zero |  |
| `+172` | constant | 0x00000001 (1) |
| `+176` | constant | 0x00000020 (32) |
| `+180` | zero |  |
| `+184` | float32 | range 0.000 .. 0.000 |
| `+188` | zero |  |
| `+192` | float32 | range 0.000 .. 0.000 |
| `+196` | zero |  |

### `CR15NetTouchInteractCRWin10`

0xEEC5CC16EA02F8FC — stride **40**, 117 entries sampled, 9 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x1BA186D0 (463570640) |
| `+4` | constant | 0xD669C468 (3597255784) |
| `+8` | actor ref | 117/117 resolve |
| `+12` | hash-like | 87 distinct of 117 |
| `+16` | mixed | 2 distinct |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | constant | 0x43960000 (1133903872) |
| `+36` | zero |  |

### `CR15NetTrackMoverCRWin10`

0xE826937E7F7D0994 — stride **48**, 2 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x13A56E1A (329608730) |
| `+4` | constant | 0x81CB05E0 (2177566176) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | mixed | 2 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | actor ref | 2/2 resolve |
| `+36` | mixed | 2 distinct |
| `+40` | zero |  |
| `+44` | zero |  |

### `CR15NetUISettingsCRWin10`

0x515BF2B6E2687C68 — stride **24**, 2 entries sampled, 2 level(s), **byte-identical across levels**, pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | zero |  |
| `+4` | zero |  |
| `+8` | constant | 0x00000008 (8) |
| `+12` | zero |  |
| `+16` | zero |  |
| `+20` | zero |  |

### `CR15NetUpdateExpressionDataCRWin10`

0x883938ED52F689E4 — stride **32**, 1 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xEBD13D07 (3956358407) |
| `+4` | constant | 0x00A50CC9 (10816713) |
| `+8` | constant | 0xB32075CC (3005248972) |
| `+12` | constant | 0x02F6C49F (49726623) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15NetVarCRWin10`

0xAC05E44816844A00 — stride **32**, 10 entries sampled, 8 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range -0.000 .. 0.000 |
| `+4` | mixed | 2 distinct |
| `+8` | actor ref | 10/10 resolve |
| `+12` | mixed | 2 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | constant | 0x00000010 (16) |
| `+28` | zero |  |

### `CR15NetVoipBroadcasterCRWin10`

0x4F8B589766FDDC68 — stride **88**, 4 entries sampled, 3 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x793D333A (2034053946) |
| `+4` | constant | 0x089A8585 (144344453) |
| `+8` | actor ref | 4/4 resolve |
| `+12` | hash-like | 4 distinct of 4 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | constant | 0x00000058 (88) |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | constant | 0x00000001 (1) |
| `+76` | zero |  |
| `+80` | constant | 0x00000001 (1) |
| `+84` | zero |  |

### `CR15NetVoipReceiverCRWin10`

0x7120AB790B3FB83A — stride **32**, 4 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x27234EB0 (656625328) |
| `+4` | constant | 0x7A06E9E8 (2047273448) |
| `+8` | actor ref | 4/4 resolve |
| `+12` | hash-like | 4 distinct of 4 |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15ObjectiveTrackerCRWin10`

0x671B4C160B346FF0 — stride **128**, 3 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x4293296E (1116940654) |
| `+4` | constant | 0xF32E9DB3 (4079918515) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0x04DBFC7F (81525887) |
| `+36` | constant | 0xA120698E (2703255950) |
| `+40` | constant | 0x373D5406 (926766086) |
| `+44` | constant | 0x1577D9F9 (360176121) |
| `+48` | constant | 0x0EDAAAB3 (249211571) |
| `+52` | constant | 0x9ADD6701 (2598201089) |
| `+56` | constant | 0x3E4CCCCD (1045220557) |
| `+60` | constant | 0x3DB851EC (1035489772) |
| `+64` | constant | 0xBD4CCCCD (3175926989) |
| `+68` | constant | 0xC03F1CDB (3225361627) |
| `+72` | constant | 0xBFAF2EDB (3215929051) |
| `+76` | constant | 0x403F1BB6 (1077877686) |
| `+80` | constant | 0x9E68FC01 (2657680385) |
| `+84` | constant | 0x4F82A5BB (1333962171) |
| `+88` | constant | 0x373D4A06 (926763526) |
| `+92` | constant | 0x1577D9F9 (360176121) |
| `+96` | constant | 0x739EB7B1 (1939781553) |
| `+100` | constant | 0xF9F343F9 (4193469433) |
| `+104` | constant | 0xBD4CCCCD (3175926989) |
| `+108` | constant | 0xBE19999A (3189348762) |
| `+112` | constant | 0x3D4CCCCD (1028443341) |
| `+116` | constant | 0xBF060A92 (3204844178) |
| `+120` | constant | 0xBE860A92 (3196455570) |
| `+124` | constant | 0x3FEA927F (1072337535) |

### `CR15OrientationConstraintCRWin10`

0x4E285CA5CA255902 — stride **112**, 46 entries sampled, 11 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range -0.000 .. 0.003 |
| `+4` | hash-like | 46 distinct of 46 |
| `+8` | actor ref | 46/46 resolve |
| `+12` | hash-like | 39 distinct of 46 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 180.000 |
| `+36` | float32 | range 0.000 .. 180.000 |
| `+40` | float32 | range 0.000 .. 0.500 |
| `+44` | float32 | range 0.000 .. 360.000 |
| `+48` | float32 | range 0.000 .. 360.000 |
| `+52` | float32 | range 0.000 .. 360.000 |
| `+56` | float32 | range 0.000 .. 0.000 |
| `+60` | zero |  |
| `+64` | zero |  |
| `+68` | zero |  |
| `+72` | zero |  |
| `+76` | constant | 0x3F800000 (1065353216) |
| `+80` | zero |  |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | constant | 0x3F800000 (1065353216) |
| `+96` | zero |  |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |

### `CR15ParentedConstraintCRWin10`

0xF2676C58C07BAB0C — stride **32**, 62 entries sampled, 14 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x90571652 (2421626450) |
| `+4` | constant | 0x70340DF2 (1882459634) |
| `+8` | actor ref | 62/62 resolve |
| `+12` | hash-like | 50 distinct of 62 |
| `+16` | mixed | 3 distinct |
| `+20` | constant | 0x00000001 (1) |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15PlatformCRWin10`

0x6DCACF3BE89109A0 — stride **24**, 34 entries sampled, 4 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range -0.000 .. 0.000 |
| `+4` | mixed | 3 distinct |
| `+8` | mixed | 8 distinct |
| `+12` | float32 | range 0.000 .. 0.000 |
| `+16` | mixed | 9 distinct |
| `+20` | mixed | 4 distinct |

### `CR15PlayerBroadcasterCRWin10`

0x0E796139581EF81E — stride **32**, 4 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xE59A20D4 (3852083412) |
| `+4` | constant | 0xA0F1C26F (2700198511) |
| `+8` | actor ref | 4/4 resolve |
| `+12` | hash-like | 4 distinct of 4 |
| `+16` | mixed | 2 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15PlayerNavCRWin10`

0x15A4D439EB6A519E — stride **208**, 3 entries sampled, 2 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x8EB1033C (2393965372) |
| `+4` | constant | 0x559BE58A (1436280202) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | constant | 0x00000C08 (3080) |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | constant | 0x00000023 (35) |
| `+76` | zero |  |
| `+80` | constant | 0x00000023 (35) |
| `+84` | zero |  |
| `+88` | constant | 0xF2BC016A (4072407402) |
| `+92` | constant | 0x881C7A82 (2283567746) |
| `+96` | constant | 0x73783415 (1937257493) |
| `+100` | constant | 0x088F2BBA (143600570) |
| `+104` | constant | 0x9BFDDBF6 (2617105398) |
| `+108` | constant | 0x0B9CA429 (194815017) |
| `+112` | constant | 0xDBC9C99E (3687434654) |
| `+116` | constant | 0xBB8671F4 (3146150388) |
| `+120` | constant | 0xCEAA3572 (3467261298) |
| `+124` | constant | 0xF0F4D380 (4042576768) |
| `+128` | constant | 0x54855710 (1418024720) |
| `+132` | constant | 0x50BF92EA (1354732266) |
| `+136` | constant | 0xC74FDFE0 (3343900640) |
| `+140` | constant | 0xCB52AB38 (3411192632) |
| `+144` | constant | 0xB84B4A27 (3091941927) |
| `+148` | constant | 0x5D162DD2 (1561734610) |
| `+152` | constant | 0xE187B967 (3783768423) |
| `+156` | constant | 0xDAEC198B (3672906123) |
| `+160` | constant | 0x00CF9A13 (13605395) |
| `+164` | constant | 0x6789CDBC (1737084348) |
| `+168` | constant | 0x0A015601 (167859713) |
| `+172` | constant | 0x5C943EB2 (1553219250) |
| `+176` | zero |  |
| `+180` | zero |  |
| `+184` | zero |  |
| `+188` | constant | 0x3F800000 (1065353216) |
| `+192` | zero |  |
| `+196` | zero |  |
| `+200` | zero |  |
| `+204` | constant | 0x3F800000 (1065353216) |

### `CR15PointerCRWin10`

0xBF101810836F65D8 — stride **48**, 5 entries sampled, 4 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xEDAEB196 (3987648918) |
| `+4` | constant | 0x85D04532 (2245018930) |
| `+8` | actor ref | 5/5 resolve |
| `+12` | hash-like | 5 distinct of 5 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 1.000 |
| `+36` | float32 | range 0.000 .. 1.000 |
| `+40` | float32 | range 4.000 .. 8.000 |
| `+44` | zero |  |

### `CR15PointerInterfaceCRWin10`

0xE4A9B494CE3BA6E0 — stride **32**, 139 entries sampled, 3 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x9A53B91F (2589178143) |
| `+4` | constant | 0xAC82D08D (2894254221) |
| `+8` | actor ref | 139/139 resolve |
| `+12` | hash-like | 139 distinct of 139 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15PointerMeshCRWin10`

0x2972B798AA1CF688 — stride **80**, 139 entries sampled, 3 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x372BE098 (925622424) |
| `+4` | constant | 0x7E6245AB (2120369579) |
| `+8` | actor ref | 139/139 resolve |
| `+12` | hash-like | 139 distinct of 139 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | float32 | range 0.220 .. 5.000 |
| `+36` | float32 | range 0.140 .. 4.000 |
| `+40` | float32 | range 1.000 .. 120.000 |
| `+44` | float32 | range 0.000 .. 0.000 |
| `+48` | float32 | range 0.000 .. 0.000 |
| `+52` | float32 | range 0.000 .. 0.500 |
| `+56` | float32 | range 0.000 .. 1.000 |
| `+60` | zero |  |
| `+64` | constant | 0xCBEF78A3 (3421468835) |
| `+68` | constant | 0xCB52BB44 (3411196740) |
| `+72` | constant | 0xE4424F76 (3829550966) |
| `+76` | constant | 0x34DFBE67 (887078503) |

### `CR15PositionConstraintCRWin10`

0x1A113B501F2133E4 — stride **40**, 3 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range -0.000 .. -0.000 |
| `+4` | hash-like | 3 distinct of 3 |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 0.000 |
| `+36` | zero |  |

### `CR15PropInputCRWin10`

0xEAC9FB460629C57E — stride **24**, 3 entries sampled, 1 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range 0.000 .. 0.000 |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | zero |  |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | zero |  |

### `CR15RadiationShieldsCRWin10`

0x3F96729C19DDC8C8 — stride **64**, 3 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xDD8811AB (3716682155) |
| `+4` | constant | 0x7726554A (1999000906) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0xEC00A245 (3959464517) |
| `+36` | constant | 0x3594F9F3 (898955763) |
| `+40` | constant | 0xEE008960 (3993012576) |
| `+44` | constant | 0xD4D3EB2C (3570658092) |
| `+48` | constant | 0xF6907D80 (4136664448) |
| `+52` | constant | 0x3ADCC96C (987548012) |
| `+56` | constant | 0xB6971B03 (3063356163) |
| `+60` | constant | 0xFD9CCD6E (4254911854) |

### `CR15RemotePlayerCRWin10`

0xAE135E5AB55304E2 — stride **32**, 2 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x6B01B67E (1795274366) |
| `+4` | constant | 0x999D680B (2577229835) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | float32 | range -0.000 .. -0.000 |
| `+16` | constant | 0x000FFFFF (1048575) |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15RigidAttachConstraintCRWin10`

0xDDE8FFFC5B4D7AD4 — stride **40**, 20 entries sampled, 7 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 4 distinct |
| `+4` | hash-like | 12 distinct of 20 |
| `+8` | actor ref | 20/20 resolve |
| `+12` | mixed | 10 distinct |
| `+16` | mixed | 3 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range 0.000 .. 0.000 |
| `+36` | zero |  |

### `CR15RotationConstraintCRWin10`

0xD34BDE625004DF5C — stride **176**, 2 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x8AA8B1E2 (2326311394) |
| `+4` | constant | 0x9094BB08 (2425666312) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | float32 | range -0.000 .. 0.000 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | constant | 0x8AA8B1E2 (2326311394) |
| `+36` | constant | 0x9094BB08 (2425666312) |
| `+40` | 0xFFFFFFFF |  |
| `+44` | 0xFFFFFFFF |  |
| `+48` | 0xFFFFFFFF |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | zero |  |
| `+64` | constant | 0x43340000 (1127481344) |
| `+68` | constant | 0x43340000 (1127481344) |
| `+72` | float32 | range 0.000 .. 0.500 |
| `+76` | constant | 0x43B40000 (1135869952) |
| `+80` | constant | 0x43B40000 (1135869952) |
| `+84` | constant | 0x43B40000 (1135869952) |
| `+88` | constant | 0x00000001 (1) |
| `+92` | zero |  |
| `+96` | zero |  |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | constant | 0x3F800000 (1065353216) |
| `+112` | zero |  |
| `+116` | zero |  |
| `+120` | zero |  |
| `+124` | constant | 0x3F800000 (1065353216) |
| `+128` | zero |  |
| `+132` | zero |  |
| `+136` | zero |  |
| `+140` | zero |  |
| `+144` | constant | 0x8AA8B1E2 (2326311394) |
| `+148` | constant | 0x9094BB08 (2425666312) |
| `+152` | 0xFFFFFFFF |  |
| `+156` | 0xFFFFFFFF |  |
| `+160` | 0xFFFFFFFF |  |
| `+164` | zero |  |
| `+168` | constant | 0x00000020 (32) |
| `+172` | zero |  |

### `CR15RumbleCRWin10`

0xAE44A8C5686B89A8 — stride **32**, 3 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x822A4A8D (2183809677) |
| `+4` | constant | 0x7A80561A (2055231002) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15SettingsTranslatorCRWin10`

0xB0D8B0AF33FDA95A — stride **32**, 2 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x69D1E470 (1775363184) |
| `+4` | constant | 0x93F4A5A2 (2482283938) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | mixed | 2 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15SliderCRWin10`

0xFC3815C51F35467A — stride **184**, 2 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x862C439A (2251047834) |
| `+4` | constant | 0x7A805703 (2055231235) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | mixed | 2 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | float32 | range -0.016 .. 0.016 |
| `+40` | float32 | range -0.023 .. 0.021 |
| `+44` | float32 | range -0.036 .. 0.038 |
| `+48` | float32 | range -0.165 .. 0.000 |
| `+52` | float32 | range 0.000 .. 0.986 |
| `+56` | float32 | range -0.167 .. 0.000 |
| `+60` | float32 | range -0.000 .. 0.986 |
| `+64` | constant | 0x3C23D70A (1008981770) |
| `+68` | constant | 0x3C23D70A (1008981770) |
| `+72` | float32 | range 0.020 .. 0.020 |
| `+76` | float32 | range -0.165 .. 0.000 |
| `+80` | float32 | range 0.000 .. 0.986 |
| `+84` | float32 | range -0.167 .. 0.000 |
| `+88` | float32 | range -0.000 .. 0.986 |
| `+92` | float32 | range -0.016 .. 0.016 |
| `+96` | float32 | range -0.024 .. 0.024 |
| `+100` | float32 | range -0.047 .. 0.048 |
| `+104` | constant | 0x3D4CCCCD (1028443341) |
| `+108` | float32 | range -0.165 .. 0.000 |
| `+112` | float32 | range 0.000 .. 0.986 |
| `+116` | float32 | range -0.167 .. 0.000 |
| `+120` | float32 | range -0.000 .. 0.986 |
| `+124` | float32 | range -0.086 .. 0.086 |
| `+128` | float32 | range -0.024 .. 0.024 |
| `+132` | float32 | range -0.047 .. 0.048 |
| `+136` | constant | 0x3D4CCCCD (1028443341) |
| `+140` | zero |  |
| `+144` | actor ref | 2/2 resolve |
| `+148` | float32 | range 0.000 .. 0.000 |
| `+152` | zero |  |
| `+156` | zero |  |
| `+160` | constant | 0x763D0417 (1983710231) |
| `+164` | constant | 0x6F4D989E (1867356318) |
| `+168` | constant | 0x00000002 (2) |
| `+172` | zero |  |
| `+176` | zero |  |
| `+180` | zero |  |

### `CR15SmoothAttacherCRWin10`

0x58AB627A32613BB2 — stride **96**, 39 entries sampled, 4 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x1C800834 (478152756) |
| `+4` | constant | 0xD76D94C0 (3614282944) |
| `+8` | actor ref | 39/39 resolve |
| `+12` | hash-like | 25 distinct of 39 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | constant | 0x00000001 (1) |
| `+28` | zero |  |
| `+32` | constant | 0x00000001 (1) |
| `+36` | constant | 0x00000002 (2) |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | constant | 0x3ECCCCCD (1053609165) |
| `+56` | constant | 0x8D260515 (2368079125) |
| `+60` | constant | 0xABFE651C (2885575964) |
| `+64` | constant | 0xE22AD937 (3794458935) |
| `+68` | constant | 0x5B8CC538 (1535952184) |
| `+72` | constant | 0x8D260515 (2368079125) |
| `+76` | constant | 0xABFE651C (2885575964) |
| `+80` | 0xFFFFFFFF |  |
| `+84` | 0xFFFFFFFF |  |
| `+88` | zero |  |
| `+92` | zero |  |

### `CR15SpawnPointCRWin10`

0xCB75EEE100D282A8 — stride **48**, 85 entries sampled, 13 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xF16B8C3C (4050357308) |
| `+4` | constant | 0x03534276 (55788150) |
| `+8` | actor ref | 85/85 resolve |
| `+12` | hash-like | 60 distinct of 85 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | mixed | 2 distinct |
| `+36` | mixed | 2 distinct |
| `+40` | float32 | range 0.000 .. 0.000 |
| `+44` | zero |  |

### `CR15StandardPOICRWin10`

0xD64551CD9A4DD92A — stride **280**, 41 entries sampled, 4 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x7629A705 (1982441221) |
| `+4` | constant | 0x48792E38 (1215901240) |
| `+8` | actor ref | 41/41 resolve |
| `+12` | hash-like | 26 distinct of 41 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | constant | 0x8D260515 (2368079125) |
| `+36` | constant | 0xABFE651C (2885575964) |
| `+40` | constant | 0xE22AD937 (3794458935) |
| `+44` | constant | 0x5B8CC538 (1535952184) |
| `+48` | constant | 0x22F7B1FC (586658300) |
| `+52` | constant | 0x2E401314 (775951124) |
| `+56` | mixed | 4 distinct |
| `+60` | mixed | 3 distinct |
| `+64` | zero |  |
| `+68` | zero |  |
| `+72` | 0xFFFFFFFF |  |
| `+76` | 0xFFFFFFFF |  |
| `+80` | zero |  |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | zero |  |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | constant | 0x00000001 (1) |
| `+112` | constant | 0x00000020 (32) |
| `+116` | zero |  |
| `+120` | zero |  |
| `+124` | zero |  |
| `+128` | zero |  |
| `+132` | zero |  |
| `+136` | zero |  |
| `+140` | zero |  |
| `+144` | zero |  |
| `+148` | zero |  |
| `+152` | zero |  |
| `+156` | zero |  |
| `+160` | zero |  |
| `+164` | zero |  |
| `+168` | zero |  |
| `+172` | zero |  |
| `+176` | zero |  |
| `+180` | zero |  |
| `+184` | zero |  |
| `+188` | zero |  |
| `+192` | zero |  |
| `+196` | zero |  |
| `+200` | actor ref | 41/41 resolve |
| `+204` | hash-like | 26 distinct of 41 |
| `+208` | zero |  |
| `+212` | zero |  |
| `+216` | actor ref | 41/41 resolve |
| `+220` | hash-like | 26 distinct of 41 |
| `+224` | zero |  |
| `+228` | zero |  |
| `+232` | actor ref | 41/41 resolve |
| `+236` | hash-like | 26 distinct of 41 |
| `+240` | zero |  |
| `+244` | zero |  |
| `+248` | actor ref | 41/41 resolve |
| `+252` | hash-like | 26 distinct of 41 |
| `+256` | zero |  |
| `+260` | zero |  |
| `+264` | 0xFFFFFFFF |  |
| `+268` | 0xFFFFFFFF |  |
| `+272` | zero |  |
| `+276` | zero |  |

### `CR15StaticArtTouchInteractCRWin10`

0x7DF22C27E143C2EA — stride **32**, 6 entries sampled, 2 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 3 distinct |
| `+4` | mixed | 3 distinct |
| `+8` | constant | 0xCA666177 (3395707255) |
| `+12` | constant | 0xB42C6CE4 (3022810340) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CR15StickyCRWin10`

0x4A63EDB14DC42414 — stride **136**, 6 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x862B4D91 (2250984849) |
| `+4` | constant | 0x7A80571B (2055231259) |
| `+8` | actor ref | 6/6 resolve |
| `+12` | hash-like | 6 distinct of 6 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | float32 | range -0.001 .. 0.002 |
| `+36` | float32 | range -0.127 .. 0.000 |
| `+40` | float32 | range -0.004 .. 0.054 |
| `+44` | float32 | range -0.000 .. 0.000 |
| `+48` | float32 | range -0.000 .. 0.000 |
| `+52` | float32 | range -0.000 .. 0.000 |
| `+56` | float32 | range 1.000 .. 1.000 |
| `+60` | float32 | range 0.014 .. 0.066 |
| `+64` | float32 | range 0.008 .. 0.014 |
| `+68` | float32 | range 0.014 .. 0.068 |
| `+72` | float32 | range -1.000 .. 1.000 |
| `+76` | float32 | range -0.707 .. 0.707 |
| `+80` | float32 | range -0.707 .. 0.707 |
| `+84` | float32 | range -0.000 .. 0.000 |
| `+88` | float32 | range 0.000 .. 0.000 |
| `+92` | float32 | range -0.124 .. 0.000 |
| `+96` | float32 | range 0.000 .. 0.058 |
| `+100` | float32 | range 1.000 .. 1.000 |
| `+104` | float32 | range -0.000 .. 0.707 |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | constant | 0x00000038 (56) |
| `+120` | 0xFFFFFFFF |  |
| `+124` | 0xFFFFFFFF |  |
| `+128` | zero |  |
| `+132` | zero |  |

### `CR15StickySurfaceCRWin10`

0xEC4547A5BAF4B6B2 — stride **88**, 17 entries sampled, 4 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x5DC13065 (1572941925) |
| `+4` | constant | 0x7C949DFD (2090114557) |
| `+8` | actor ref | 17/17 resolve |
| `+12` | hash-like | 13 distinct of 17 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | float32 | range -20.267 .. 20.266 |
| `+36` | float32 | range -7.006 .. 0.000 |
| `+40` | float32 | range -15.782 .. 25.821 |
| `+44` | zero |  |
| `+48` | float32 | range -0.866 .. 0.866 |
| `+52` | zero |  |
| `+56` | float32 | range 0.500 .. 1.000 |
| `+60` | zero |  |
| `+64` | zero |  |
| `+68` | zero |  |
| `+72` | 0xFFFFFFFF |  |
| `+76` | 0xFFFFFFFF |  |
| `+80` | zero |  |
| `+84` | constant | 0x01000000 (16777216) |

### `CR15SyncGrabCRWin10`

0xC8EDC00BF1D93EFE — stride **24**, 48 entries sampled, 14 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range 0.000 .. 0.000 |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | zero |  |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | float32 | range 0.000 .. 0.000 |

### `CR15TagCRWin10`

0x33B05DC2277E7E6C — stride **24**, 4 entries sampled, 2 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | zero |  |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | zero |  |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | zero |  |

### `CR15TeamCRWin10`

0x991DF4582160DDC0 — stride **24**, 55 entries sampled, 8 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range 0.000 .. 0.000 |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 3954081.750 |
| `+12` | float32 | range 0.000 .. 0.000 |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | float32 | range 0.000 .. 0.000 |

### `CR15ThereminCRWin10`

0xD2DA34C25E28F4A2 — stride **112**, 3 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xCEB9E6AC (3468289708) |
| `+4` | constant | 0xFEECA843 (4276922435) |
| `+8` | actor ref | 3/3 resolve |
| `+12` | hash-like | 3 distinct of 3 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | zero |  |
| `+44` | constant | 0x3F800000 (1065353216) |
| `+48` | constant | 0xC1C04189 (3250602377) |
| `+52` | constant | 0xBF88F5C2 (3213424066) |
| `+56` | constant | 0x415E0000 (1096679424) |
| `+60` | constant | 0x3F800000 (1065353216) |
| `+64` | constant | 0x6A76A7BA (1786161082) |
| `+68` | hash-like | 3 distinct of 3 |
| `+72` | constant | 0x15EAE214 (367714836) |
| `+76` | constant | 0x7DD89308 (2111345416) |
| `+80` | constant | 0x26294964 (640239972) |
| `+84` | constant | 0xD774D3B8 (3614757816) |
| `+88` | constant | 0x7A57195D (2052528477) |
| `+92` | constant | 0x3F800000 (1065353216) |
| `+96` | constant | 0x40400000 (1077936128) |
| `+100` | constant | 0x40600000 (1080033280) |
| `+104` | constant | 0x408E147B (1083053179) |
| `+108` | zero |  |

### `CR15TouchAnimBlendersCRWin10`

0x4FEDA95613A2D0BA — stride **48**, 8 entries sampled, 4 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 2 distinct |
| `+4` | hash-like | 8 distinct of 8 |
| `+8` | actor ref | 8/8 resolve |
| `+12` | hash-like | 5 distinct of 8 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | mixed | 2 distinct |
| `+36` | mixed | 2 distinct |
| `+40` | mixed | 2 distinct |
| `+44` | mixed | 2 distinct |

### `CR15TouchInteractCRWin10`

0xC2BF1B5284855E4A — stride **64**, 348 entries sampled, 17 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x22F7B1FC (586658300) |
| `+4` | constant | 0x2E401314 (775951124) |
| `+8` | actor ref | 348/348 resolve |
| `+12` | hash-like | 282 distinct of 348 |
| `+16` | mixed | 4 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | mixed | 5 distinct |
| `+36` | mixed | 5 distinct |
| `+40` | mixed | 3 distinct |
| `+44` | mixed | 3 distinct |
| `+48` | float32 | range 0.000 .. 0.000 |
| `+52` | float32 | range 0.000 .. 0.200 |
| `+56` | float32 | range 1.000 .. 100.000 |
| `+60` | constant | 0x43200000 (1126170624) |

### `CR15TrackPointCRWin10`

0x451830F92DDC5CC6 — stride **56**, 250 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x9771A3AA (2540807082) |
| `+4` | constant | 0x0620ED97 (102821271) |
| `+8` | actor ref | 250/250 resolve |
| `+12` | hash-like | 250 distinct of 250 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | mixed | 15 distinct |
| `+36` | hash-like | 250 distinct of 250 |
| `+40` | actor ref | 248/250 resolve |
| `+44` | hash-like | 249 distinct of 250 |
| `+48` | zero |  |
| `+52` | zero |  |

### `CR15TriggerCRWin10`

0x39284E7992BEFC92 — stride **72**, 5 entries sampled, 5 level(s), **byte-identical across levels**

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xE4BDB196 (3837637014) |
| `+4` | constant | 0x85D45832 (2245285938) |
| `+8` | constant | 0xAC9FBFF8 (2896150520) |
| `+12` | constant | 0x26B939FC (649673212) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | constant | 0x00000001 (1) |
| `+28` | zero |  |
| `+32` | constant | 0xE1797488 (3782833288) |
| `+36` | constant | 0x0671C5E4 (108119524) |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | constant | 0xA604FF08 (2785345288) |
| `+52` | constant | 0x8A133114 (2316513556) |
| `+56` | zero |  |
| `+60` | zero |  |
| `+64` | constant | 0x3F333333 (1060320051) |
| `+68` | zero |  |

### `CR15UILayoutCRWin10`

0x8B91EFD51747C21E — stride **96**, 34 entries sampled, 12 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xD2BBFAB6 (3535534774) |
| `+4` | constant | 0xFFEDA150 (4293763408) |
| `+8` | actor ref | 34/34 resolve |
| `+12` | hash-like | 34 distinct of 34 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | hash-like | 18 distinct of 34 |
| `+36` | hash-like | 18 distinct of 34 |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | float32 | range 0.000 .. 0.000 |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | zero |  |
| `+64` | zero |  |
| `+68` | constant | 0x00000001 (1) |
| `+72` | constant | 0x00000020 (32) |
| `+76` | zero |  |
| `+80` | float32 | range 0.000 .. 0.000 |
| `+84` | zero |  |
| `+88` | float32 | range 0.000 .. 0.000 |
| `+92` | zero |  |

### `CR15UIPage2CRWin10`

0x4A3FE67C1FF9CCC4 — stride **328**, 137 entries sampled, 3 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xE2BDB1D6 (3804082646) |
| `+4` | constant | 0x85D5432B (2245346091) |
| `+8` | actor ref | 137/137 resolve |
| `+12` | hash-like | 135 distinct of 137 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | float32 | range 0.000 .. 0.000 |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | float32 | range 0.000 .. 0.000 |
| `+76` | zero |  |
| `+80` | float32 | range 0.000 .. 0.000 |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | float32 | range 0.000 .. 0.000 |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | constant | 0x00000001 (1) |
| `+120` | constant | 0x00000020 (32) |
| `+124` | zero |  |
| `+128` | float32 | range 0.000 .. 0.000 |
| `+132` | zero |  |
| `+136` | float32 | range 0.000 .. 0.000 |
| `+140` | zero |  |
| `+144` | zero |  |
| `+148` | zero |  |
| `+152` | zero |  |
| `+156` | zero |  |
| `+160` | zero |  |
| `+164` | zero |  |
| `+168` | zero |  |
| `+172` | constant | 0x00000001 (1) |
| `+176` | constant | 0x00000020 (32) |
| `+180` | zero |  |
| `+184` | zero |  |
| `+188` | zero |  |
| `+192` | zero |  |
| `+196` | zero |  |
| `+200` | zero |  |
| `+204` | zero |  |
| `+208` | float32 | range 0.000 .. 0.000 |
| `+212` | zero |  |
| `+216` | zero |  |
| `+220` | zero |  |
| `+224` | zero |  |
| `+228` | constant | 0x00000001 (1) |
| `+232` | constant | 0x00000020 (32) |
| `+236` | zero |  |
| `+240` | float32 | range 0.000 .. 0.000 |
| `+244` | zero |  |
| `+248` | float32 | range 0.000 .. 0.000 |
| `+252` | zero |  |
| `+256` | mixed | 2 distinct |
| `+260` | mixed | 2 distinct |
| `+264` | zero |  |
| `+268` | zero |  |
| `+272` | zero |  |
| `+276` | zero |  |
| `+280` | float32 | range 0.000 .. 0.000 |
| `+284` | zero |  |
| `+288` | zero |  |
| `+292` | zero |  |
| `+296` | zero |  |
| `+300` | constant | 0x00000001 (1) |
| `+304` | constant | 0x00000020 (32) |
| `+308` | zero |  |
| `+312` | float32 | range 0.000 .. 0.000 |
| `+316` | zero |  |
| `+320` | float32 | range 0.000 .. 0.000 |
| `+324` | zero |  |

### `CR15UIPage2ElementCRWin10`

0xC1B1FCA890787B30 — stride **96**, 140 entries sampled, 3 level(s), pooled, 1 inline table(s) at +32

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x68E40658 (1759774296) |
| `+4` | constant | 0x84A940C5 (2225684677) |
| `+8` | actor ref | 140/140 resolve |
| `+12` | hash-like | 140 distinct of 140 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | float32 | range 0.000 .. 0.000 |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | float32 | range 0.000 .. 0.000 |
| `+76` | zero |  |
| `+80` | float32 | range 0.000 .. 0.000 |
| `+84` | zero |  |
| `+88` | hash-like | 91 distinct of 140 |
| `+92` | mixed | 78 distinct |

### `CR15UIPageCRWin10`

0xD282D4778B4EDDB2 — stride **264**, 34 entries sampled, 11 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x9F29418D (2670281101) |
| `+4` | constant | 0x7A815106 (2055295238) |
| `+8` | actor ref | 34/34 resolve |
| `+12` | hash-like | 34 distinct of 34 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | float32 | range 0.000 .. 0.000 |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | float32 | range 0.000 .. 0.000 |
| `+76` | zero |  |
| `+80` | float32 | range 0.000 .. 0.000 |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | zero |  |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | constant | 0x00000001 (1) |
| `+120` | constant | 0x00000020 (32) |
| `+124` | zero |  |
| `+128` | zero |  |
| `+132` | zero |  |
| `+136` | zero |  |
| `+140` | zero |  |
| `+144` | mixed | 2 distinct |
| `+148` | mixed | 2 distinct |
| `+152` | zero |  |
| `+156` | zero |  |
| `+160` | float32 | range 0.000 .. 0.000 |
| `+164` | zero |  |
| `+168` | zero |  |
| `+172` | zero |  |
| `+176` | zero |  |
| `+180` | constant | 0x00000001 (1) |
| `+184` | constant | 0x00000020 (32) |
| `+188` | zero |  |
| `+192` | float32 | range 0.000 .. 0.000 |
| `+196` | zero |  |
| `+200` | float32 | range 0.000 .. 0.000 |
| `+204` | zero |  |
| `+208` | zero |  |
| `+212` | zero |  |
| `+216` | float32 | range 0.000 .. 0.000 |
| `+220` | zero |  |
| `+224` | zero |  |
| `+228` | zero |  |
| `+232` | zero |  |
| `+236` | constant | 0x00000001 (1) |
| `+240` | constant | 0x00000020 (32) |
| `+244` | zero |  |
| `+248` | float32 | range 0.000 .. 0.000 |
| `+252` | zero |  |
| `+256` | float32 | range 0.000 .. 0.000 |
| `+260` | zero |  |

### `CR15UISettingsCRWin10`

0x04DA13B8BF4FB47E — stride **24**, 2 entries sampled, 2 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | zero |  |
| `+4` | zero |  |
| `+8` | constant | 0x00000008 (8) |
| `+12` | zero |  |
| `+16` | zero |  |
| `+20` | zero |  |

### `CR15VRMenuWidgetCRWin10`

0xFB854915B12A5108 — stride **96**, 69 entries sampled, 3 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 21 distinct |
| `+4` | hash-like | 68 distinct of 69 |
| `+8` | actor ref | 69/69 resolve |
| `+12` | hash-like | 36 distinct of 69 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | float32 | range -2.988 .. 2.997 |
| `+36` | float32 | range -1.888 .. 1.479 |
| `+40` | float32 | range 0.000 .. 6.200 |
| `+44` | float32 | range -0.005 .. 0.005 |
| `+48` | float32 | range -0.221 .. 1.000 |
| `+52` | float32 | range 0.000 .. 0.147 |
| `+56` | float32 | range -0.261 .. 1.000 |
| `+60` | float32 | range 0.000 .. 1.308 |
| `+64` | float32 | range 0.000 .. 1.704 |
| `+68` | float32 | range 0.000 .. 0.150 |
| `+72` | mixed | 12 distinct |
| `+76` | mixed | 12 distinct |
| `+80` | mixed | 12 distinct |
| `+84` | mixed | 12 distinct |
| `+88` | mixed | 12 distinct |
| `+92` | mixed | 12 distinct |

### `CResourceLoaderCRWin10`

0xBB0A36E2054C45C0 — stride **32**, 18 entries sampled, 7 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x115BDCC8 (291232968) |
| `+4` | constant | 0x6EA0EB30 (1856039728) |
| `+8` | actor ref | 18/18 resolve |
| `+12` | hash-like | 14 distinct of 18 |
| `+16` | mixed | 3 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | zero |  |
| `+28` | zero |  |

### `CRxAICRWin10`

0x69F6426374C30ADE — stride **144**, 1 entries sampled, 1 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x286B1FB9 (678109113) |
| `+4` | constant | 0xC8C33E48 (3368238664) |
| `+8` | constant | 0x1860D487 (408999047) |
| `+12` | constant | 0x7E1E9B7C (2115935100) |
| `+16` | constant | 0x000AFFFF (720895) |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | constant | 0x00000288 (648) |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | zero |  |
| `+60` | constant | 0x00000001 (1) |
| `+64` | constant | 0x00000020 (32) |
| `+68` | zero |  |
| `+72` | constant | 0x00000003 (3) |
| `+76` | zero |  |
| `+80` | constant | 0x00000003 (3) |
| `+84` | zero |  |
| `+88` | zero |  |
| `+92` | zero |  |
| `+96` | constant | 0x00000948 (2376) |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | constant | 0x00000001 (1) |
| `+120` | constant | 0x00000020 (32) |
| `+124` | zero |  |
| `+128` | constant | 0x00000021 (33) |
| `+132` | zero |  |
| `+136` | constant | 0x00000021 (33) |
| `+140` | zero |  |

### `CRxMaterialFXCRWin10`

0x9D7C9773CF1D74E2 — stride **56**, 2 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range -0.000 .. -0.000 |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | constant | 0x81E2E0F4 (2179129588) |
| `+12` | constant | 0x81F13E66 (2180071014) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | constant | 0xD89B42A8 (3634053800) |
| `+36` | constant | 0xE32DC7D7 (3811428311) |
| `+40` | float32 | range -0.422 .. 0.000 |
| `+44` | mixed | 2 distinct |
| `+48` | constant | 0x00000001 (1) |
| `+52` | zero |  |

### `CSVOPathPlannerCRWin10`

0xC953B3E9E48AB9AC — stride **32**, 2 entries sampled, 2 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xF22841EA (4062724586) |
| `+4` | constant | 0x51256090 (1361404048) |
| `+8` | actor ref | 2/2 resolve |
| `+12` | mixed | 2 distinct |
| `+16` | mixed | 2 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CSVOVolumeCRWin10`

0x7A40DBFDC05807FC — stride **232**, 1 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xDBAAD8E7 (3685406951) |
| `+4` | constant | 0x16827453 (377648211) |
| `+8` | constant | 0x4E76B141 (1316401473) |
| `+12` | constant | 0xB74D5602 (3075298818) |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | zero |  |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | constant | 0x3F800000 (1065353216) |
| `+60` | constant | 0x42700000 (1114636288) |
| `+64` | constant | 0x42700000 (1114636288) |
| `+68` | constant | 0x42700000 (1114636288) |
| `+72` | zero |  |
| `+76` | constant | 0x412130B4 (1092694196) |
| `+80` | zero |  |
| `+84` | constant | 0x3E4CCCCD (1045220557) |
| `+88` | constant | 0x3F800000 (1065353216) |
| `+92` | constant | 0x00000006 (6) |
| `+96` | constant | 0xC2700000 (3262119936) |
| `+100` | constant | 0xC2700000 (3262119936) |
| `+104` | constant | 0xC2700000 (3262119936) |
| `+108` | constant | 0x42700000 (1114636288) |
| `+112` | constant | 0x42700000 (1114636288) |
| `+116` | constant | 0x42700000 (1114636288) |
| `+120` | zero |  |
| `+124` | zero |  |
| `+128` | zero |  |
| `+132` | zero |  |
| `+136` | zero |  |
| `+140` | zero |  |
| `+144` | zero |  |
| `+148` | constant | 0x00000001 (1) |
| `+152` | constant | 0x00000020 (32) |
| `+156` | zero |  |
| `+160` | zero |  |
| `+164` | zero |  |
| `+168` | zero |  |
| `+172` | zero |  |
| `+176` | zero |  |
| `+180` | zero |  |
| `+184` | zero |  |
| `+188` | zero |  |
| `+192` | zero |  |
| `+196` | zero |  |
| `+200` | zero |  |
| `+204` | constant | 0x00000001 (1) |
| `+208` | constant | 0x00000020 (32) |
| `+212` | zero |  |
| `+216` | zero |  |
| `+220` | zero |  |
| `+224` | zero |  |
| `+228` | zero |  |

### `CScriptCRWin10`

0xD99F6BBD8009C92C — stride **720**, 4076 entries sampled, 31 level(s), pooled, 9 inline table(s) at +48, +160, +272, +328, +384, +440, +496, +552, +608

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 210 distinct |
| `+4` | hash-like | 2625 distinct of 4076 |
| `+8` | actor ref | 4076/4076 resolve |
| `+12` | mixed | 1864 distinct |
| `+16` | mixed | 10 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | mixed | 565 distinct |
| `+36` | mixed | 556 distinct |
| `+40` | zero |  |
| `+44` | zero |  |
| `+48` | zero |  |
| `+52` | zero |  |
| `+56` | float32 | range 0.000 .. 0.000 |
| `+60` | zero |  |
| `+64` | zero |  |
| `+68` | zero |  |
| `+72` | zero |  |
| `+76` | constant | 0x00000001 (1) |
| `+80` | constant | 0x00000020 (32) |
| `+84` | zero |  |
| `+88` | float32 | range 0.000 .. 0.000 |
| `+92` | zero |  |
| `+96` | float32 | range 0.000 .. 0.000 |
| `+100` | zero |  |
| `+104` | zero |  |
| `+108` | zero |  |
| `+112` | zero |  |
| `+116` | zero |  |
| `+120` | zero |  |
| `+124` | zero |  |
| `+128` | zero |  |
| `+132` | constant | 0x00000001 (1) |
| `+136` | constant | 0x00000020 (32) |
| `+140` | zero |  |
| `+144` | zero |  |
| `+148` | zero |  |
| `+152` | zero |  |
| `+156` | zero |  |
| `+160` | zero |  |
| `+164` | zero |  |
| `+168` | float32 | range 0.000 .. 0.000 |
| `+172` | zero |  |
| `+176` | zero |  |
| `+180` | zero |  |
| `+184` | zero |  |
| `+188` | constant | 0x00000001 (1) |
| `+192` | constant | 0x00000020 (32) |
| `+196` | zero |  |
| `+200` | float32 | range 0.000 .. 0.000 |
| `+204` | zero |  |
| `+208` | float32 | range 0.000 .. 0.000 |
| `+212` | zero |  |
| `+216` | zero |  |
| `+220` | zero |  |
| `+224` | zero |  |
| `+228` | zero |  |
| `+232` | zero |  |
| `+236` | zero |  |
| `+240` | zero |  |
| `+244` | constant | 0x00000001 (1) |
| `+248` | constant | 0x00000020 (32) |
| `+252` | zero |  |
| `+256` | zero |  |
| `+260` | zero |  |
| `+264` | zero |  |
| `+268` | zero |  |
| `+272` | zero |  |
| `+276` | zero |  |
| `+280` | float32 | range 0.000 .. 0.000 |
| `+284` | zero |  |
| `+288` | zero |  |
| `+292` | zero |  |
| `+296` | zero |  |
| `+300` | constant | 0x00000001 (1) |
| `+304` | constant | 0x00000020 (32) |
| `+308` | zero |  |
| `+312` | float32 | range 0.000 .. 0.000 |
| `+316` | zero |  |
| `+320` | float32 | range 0.000 .. 0.000 |
| `+324` | zero |  |
| `+328` | zero |  |
| `+332` | zero |  |
| `+336` | float32 | range 0.000 .. 0.000 |
| `+340` | zero |  |
| `+344` | zero |  |
| `+348` | zero |  |
| `+352` | zero |  |
| `+356` | constant | 0x00000001 (1) |
| `+360` | constant | 0x00000020 (32) |
| `+364` | zero |  |
| `+368` | float32 | range 0.000 .. 0.000 |
| `+372` | zero |  |
| `+376` | float32 | range 0.000 .. 0.000 |
| `+380` | zero |  |
| `+384` | zero |  |
| `+388` | zero |  |
| `+392` | float32 | range 0.000 .. 0.000 |
| `+396` | zero |  |
| `+400` | zero |  |
| `+404` | zero |  |
| `+408` | zero |  |
| `+412` | constant | 0x00000001 (1) |
| `+416` | constant | 0x00000020 (32) |
| `+420` | zero |  |
| `+424` | float32 | range 0.000 .. 0.000 |
| `+428` | zero |  |
| `+432` | float32 | range 0.000 .. 0.000 |
| `+436` | zero |  |
| `+440` | zero |  |
| `+444` | zero |  |
| `+448` | float32 | range 0.000 .. 0.000 |
| `+452` | zero |  |
| `+456` | zero |  |
| `+460` | zero |  |
| `+464` | zero |  |
| `+468` | constant | 0x00000001 (1) |
| `+472` | constant | 0x00000020 (32) |
| `+476` | zero |  |
| `+480` | float32 | range 0.000 .. 0.000 |
| `+484` | zero |  |
| `+488` | float32 | range 0.000 .. 0.000 |
| `+492` | zero |  |
| `+496` | zero |  |
| `+500` | zero |  |
| `+504` | float32 | range 0.000 .. 0.000 |
| `+508` | zero |  |
| `+512` | zero |  |
| `+516` | zero |  |
| `+520` | zero |  |
| `+524` | constant | 0x00000001 (1) |
| `+528` | constant | 0x00000020 (32) |
| `+532` | zero |  |
| `+536` | float32 | range 0.000 .. 0.000 |
| `+540` | zero |  |
| `+544` | float32 | range 0.000 .. 0.000 |
| `+548` | zero |  |
| `+552` | zero |  |
| `+556` | zero |  |
| `+560` | float32 | range 0.000 .. 0.000 |
| `+564` | zero |  |
| `+568` | zero |  |
| `+572` | zero |  |
| `+576` | zero |  |
| `+580` | constant | 0x00000001 (1) |
| `+584` | constant | 0x00000020 (32) |
| `+588` | zero |  |
| `+592` | float32 | range 0.000 .. 0.000 |
| `+596` | zero |  |
| `+600` | float32 | range 0.000 .. 0.000 |
| `+604` | zero |  |
| `+608` | zero |  |
| `+612` | zero |  |
| `+616` | float32 | range 0.000 .. 0.000 |
| `+620` | zero |  |
| `+624` | zero |  |
| `+628` | zero |  |
| `+632` | zero |  |
| `+636` | constant | 0x00000001 (1) |
| `+640` | constant | 0x00000020 (32) |
| `+644` | zero |  |
| `+648` | float32 | range 0.000 .. 0.000 |
| `+652` | zero |  |
| `+656` | float32 | range 0.000 .. 0.000 |
| `+660` | zero |  |
| `+664` | zero |  |
| `+668` | zero |  |
| `+672` | zero |  |
| `+676` | zero |  |
| `+680` | zero |  |
| `+684` | zero |  |
| `+688` | zero |  |
| `+692` | constant | 0x00000001 (1) |
| `+696` | constant | 0x00000020 (32) |
| `+700` | zero |  |
| `+704` | zero |  |
| `+708` | zero |  |
| `+712` | zero |  |
| `+716` | zero |  |

### `CScriptStateStackCRWin10`

0x5EBB0A68CD6CC76A — stride **32**, 2 entries sampled, 1 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 2 distinct |
| `+4` | mixed | 2 distinct |
| `+8` | actor ref | 2/2 resolve |
| `+12` | mixed | 2 distinct |
| `+16` | 0xFFFFFFFF |  |
| `+20` | constant | 0x00000001 (1) |
| `+24` | zero |  |
| `+28` | zero |  |

### `CSettingsTranslatorCRWin10`

0xFF5BAD1FCF4C464C — stride **24**, 2 entries sampled, 2 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | zero |  |
| `+4` | zero |  |
| `+8` | constant | 0x00000008 (8) |
| `+12` | zero |  |
| `+16` | zero |  |
| `+20` | zero |  |

### `CSettingsUICRWin10`

0x130C1B8FCC7D1DCE — stride **24**, 4 entries sampled, 2 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | zero |  |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | zero |  |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | zero |  |

### `CSharedCanvasUICRWin10`

0xDAB7DCE1DF894EF6 — stride **72**, 108 entries sampled, 7 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 2 distinct |
| `+4` | hash-like | 99 distinct of 108 |
| `+8` | actor ref | 108/108 resolve |
| `+12` | hash-like | 93 distinct of 108 |
| `+16` | mixed | 3 distinct |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |
| `+32` | mixed | 5 distinct |
| `+36` | mixed | 5 distinct |
| `+40` | mixed | 8 distinct |
| `+44` | mixed | 8 distinct |
| `+48` | actor ref (partial) | 104/108 resolve |
| `+52` | mixed | 20 distinct |
| `+56` | zero |  |
| `+60` | zero |  |
| `+64` | mixed | 3 distinct |
| `+68` | mixed | 3 distinct |

### `CSoundCRWin10`

0x04E7C2E6A7EBD80E — stride **80**, 1511 entries sampled, 12 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x26EA8A62 (652905058) |
| `+4` | constant | 0x38EE9504 (955159812) |
| `+8` | actor ref | 1511/1511 resolve |
| `+12` | mixed | 686 distinct |
| `+16` | mixed | 5 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | float32 | range 0.000 .. 0.000 |
| `+28` | zero |  |
| `+32` | zero |  |
| `+36` | float32 | range -0.383 .. 0.924 |
| `+40` | zero |  |
| `+44` | float32 | range 0.383 .. 1.000 |
| `+48` | float32 | range -1.025 .. 0.608 |
| `+52` | float32 | range -0.100 .. 1.309 |
| `+56` | float32 | range -1.001 .. 0.644 |
| `+60` | constant | 0x3F800000 (1065353216) |
| `+64` | mixed | 2 distinct |
| `+68` | mixed | 2 distinct |
| `+72` | float32 | range 0.000 .. 0.000 |
| `+76` | zero |  |

### `CStaticLODRegionTargetCRWin10`

0x02D2BE13EA8FB8AE — stride **32**, 423 entries sampled, 8 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x6A57023E (1784087102) |
| `+4` | constant | 0x18708948 (410028360) |
| `+8` | actor ref | 423/423 resolve |
| `+12` | hash-like | 273 distinct of 423 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | constant | 0x00000001 (1) |
| `+24` | zero |  |
| `+28` | zero |  |

### `CStaticRaycastCRWin10`

0xD649A90FFF322C12 — stride **32**, 31 entries sampled, 31 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0xEF67B6D2 (4016551634) |
| `+4` | constant | 0x53C70747 (1405552455) |
| `+8` | actor ref | 31/31 resolve |
| `+12` | hash-like | 31 distinct of 31 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CSyncCRWin10`

0xEFFEC49E45650654 — stride **32**, 17 entries sampled, 10 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | constant | 0x4D6D5989 (1299011977) |
| `+4` | constant | 0x2FD98C9E (802786462) |
| `+8` | actor ref | 17/17 resolve |
| `+12` | hash-like | 17 distinct of 17 |
| `+16` | 0xFFFFFFFF |  |
| `+20` | zero |  |
| `+24` | zero |  |
| `+28` | zero |  |

### `CTagCRWin10`

0x1C718652028E0984 — stride **24**, 18 entries sampled, 12 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | float32 | range 0.000 .. 0.000 |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | zero |  |
| `+16` | float32 | range 0.000 .. 0.000 |
| `+20` | float32 | range 0.000 .. 0.000 |

### `CTeamCRWin10`

0xB9A46F348CBF3BC6 — stride **24**, 97 entries sampled, 7 level(s), pooled, layout unsolved

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 8 distinct |
| `+4` | float32 | range 0.000 .. 0.000 |
| `+8` | float32 | range 0.000 .. 0.000 |
| `+12` | float32 | range -399858.250 .. 0.000 |
| `+16` | mixed | 25 distinct |
| `+20` | mixed | 23 distinct |

### `CTextureOverrideCRWin10`

0x4127FF2FFE6BE26A — stride **32**, 2089 entries sampled, 18 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 44 distinct |
| `+4` | mixed | 44 distinct |
| `+8` | actor ref | 2089/2089 resolve |
| `+12` | mixed | 832 distinct |
| `+16` | mixed | 3 distinct |
| `+20` | float32 | range 0.000 .. 0.000 |
| `+24` | zero |  |
| `+28` | zero |  |

### `CTextureStreamingCRWin10`

0x96832B652460ECDE — stride **40**, 4508 entries sampled, 28 level(s)

| Offset | Kind | Detail |
|---|---|---|
| `+0` | mixed | 65 distinct |
| `+4` | mixed | 61 distinct |
| `+8` | actor ref | 4508/4508 resolve |
| `+12` | mixed | 1699 distinct |
| `+16` | mixed | 4 distinct |
| `+20` | constant | 0x00000001 (1) |
| `+24` | constant | 0x00000004 (4) |
| `+28` | zero |  |
| `+32` | mixed | 315 distinct |
| `+36` | mixed | 327 distinct |
