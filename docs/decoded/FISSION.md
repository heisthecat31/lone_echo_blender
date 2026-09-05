# mpl_combat_fission — complete dossier

`0xDF5CA7B7DFA383D4`. The reference payload map: everything it ships, and
everything that links to it.

| | |
|---|---|
| Resources | **97** |
| Total size | **8.5 MB** |
| Actors | **1539** |
| Component systems declared | **76** |
| Script entries / distinct scripts | **418 / 81** |
| Sub-levels declared (sec12) | 6 |
| Parent level | `0x08A1AF9E108DEF0B` (`mpl_combat_war_room`) |

## What links TO fission — and why it kept appearing

Four levels name fission in **both** `CGameLevelInfoResource` (`+0`) and
`CGameLevelResource` (`+16`) — i.e. **fission is their parent**:

| Hash | Level | Parented to fission |
|---|---|---|
| `0x836C5B14CCC58201` | mpl_combat_fission_cargobay | **yes** |
| `0x906C4707CBC28C16` | mpl_combat_fission_pantheon | **yes** |
| `0x907F461FCCC0961D` | mpl_combat_fission_prologue | **yes** |
| `0xF919F210BBDA872C` | mpl_combat_fission_climax | **yes** |

**These are fission's own map regions**, not shared content:
`..._cargobay`, `..._pantheon`, `..._prologue`, `..._climax`. Loading any one of
them pulls its parent, which is fission.

⚠ **This corrects a substantial earlier mistake of mine.** I had identified six
levels that stock combat streams as "the war-room UI levels carrying the
terminal UI and the deploy hologram", and tried to stream them into our map.
Named, they are:

| Hash | What it actually is |
|---|---|
| `0x836C5B14CCC58201` | `mpl_combat_fission_cargobay` — a fission map region |
| `0x906C4707CBC28C16` | `mpl_combat_fission_pantheon` — a fission map region |
| `0x907F461FCCC0961D` | `mpl_combat_fission_prologue` — a fission map region |
| `0xF919F210BBDA872C` | `mpl_combat_fission_climax` — a fission map region |
| `0x61B0162ADBD446FF` | `mpl_combat_celebration_room_orange` |
| `0xE1A3AE700140D9D5` | `mpl_combat_celebration_room_blue` |

**None of them is a terminal or a hologram.** Four are pieces of fission itself
and two are the end-of-match celebration rooms. Streaming them into another map
was never going to produce terminals — it could only drag fission in, which is
exactly what happened, and the duplicate `CR15NetBalanceSettings` then hung the
load. The clone machinery worked perfectly; the target was wrong.

Two further identifications I had wrong, from the same guesswork:

* `0x3F9915D3001DC28E` is **`r14_glb_global_mp`**, the global multiplayer
  gamespace — not a "terminal panel prefab".
* `0x4D82118C7C91B6BB` and `0xAC360E41E4EDE056` are **`mnu_master_mp_ingame`**
  and **`mnu_master`** — menu levels, not the room you stand in.
* `mpl_combat_war_room` has **675 records** in the level index. My earlier claim
  that it "ships zero resources" was an artifact of this extract not containing
  them, not a fact about the game.

## Sub-levels fission declares (sec12)

Offsets are the world position each is placed at. Note these are **not** the
levels the game streams during a match.

| Sub-level | Name | Offset |
|---|---|---|
| `0xB490BB722958C1A8` | (not in this extract) | 25.91, -10.43, 7.36 |
| `0xF1F1734847D74B28` | (not in this extract) | -27.43, -3.97, 12.18 |
| `0xFF2EBE34FE81CDDA` | (not in this extract) | 49.14, -10.44, 18.89 |
| `0x0F9E2949BB1B1645` | (not in this extract) | 51.63, -10.43, 23.23 |
| `0x32D7D56E27A93157` | (not in this extract) | 22.33, -10.42, 10.91 |
| `0x7244FD32CBE25A07` | (not in this extract) | -30.98, -3.96, 15.7 |

## Scripts

**418 entries binding 81 distinct scripts.** The heaviest by actor count:

| Script | Actors |
|---|---|
| `0x31791321CEB2B274` | 120 |
| `0xD56C95E9F5C64131` | 21 |
| `0x10A27372E2E5C944` | 20 |
| `0xCFF4557B7D2CE426` | 15 |
| `0xCFF74A696F2DED20` | 15 |
| `0xA9DB8988989BB09E` | 15 |
| `0x6DAE7E9A52BE5F0E` | 15 |
| `0x6DAE619456BB4F17` | 15 |

## Every resource fission ships

Shape is from the envelope test (`../combat/CR_FORMAT.md`); stride is
`data_size / count`.

| Type | Type hash | Shape | Count | Stride | Bytes |
|---|---|---|---|---|---|
| `e642bfb1abcf76df` | 0xE642BFB1ABCF76DF | resource | 44326893869977600 | - | 2,972,740 |
| `CBVHResourceWin10` | 0x358B53C17825D154 | resource | 262592 | - | 2,097,680 |
| `CTransformCRWin10` | 0x92ABD3E1432BF5E8 | flat-cr | 4128 | 176 | 726,584 |
| `5004f0b9f6645271` | 0x5004F0B9F6645271 | resource | 5270498306774269952 | - | 524,448 |
| `CGSceneResourceWin10` | 0xA388EA69E5108F4C | resource | 4764808406823337984 | - | 489,324 |
| `CModelCRWin10` | 0xEA51A0D76EB90142 | pooled-cr | 726 | 24 | 426,024 |
| `CScriptCRWin10` | 0xD99F6BBD8009C92C | pooled-cr | 418 | 720 | 383,848 |
| `CMaterialTypesBVHResourceWin10` | 0x4230B4E0957B5462 | pooled-cr | 19019 | 16 | 304,368 |
| `CActorDataResourceWin10` | 0x347869CE492DC7DA | pooled-cr | 1593 | 16 | 165,608 |
| `CR15ButtonInteractCRWin10` | 0xE9B24EA816DECE48 | flat-cr | 517 | 296 | 153,088 |
| `CAnimationCRWin10` | 0xDB098C12AD9B9844 | pooled-cr | 313 | 24 | 151,280 |
| `CGameLevelResourceWin10` | 0xE8E38D7781A338A6 | resource | 4 | - | 82,528 |
| `CComponentLODCRWin10` | 0x7F49ABAE39AAF2AA | pooled-cr | 339 | 96 | 78,200 |
| `12b9cc38a29755a` | 0x12B9CC38A29755A | flat-cr | 556 | 88 | 48,984 |
| `CCanvasUICRWin10` | 0x822FD4CCB42E8A3C | flat-cr | 410 | 88 | 36,136 |
| `carchiveresourceWin10` | 0x2A41CF1C1D9E5D32 | resource | 10057083141392679454 | - | 34,608 |
| `CBoundingSphereCRWin10` | 0x22F9FCB2D5E52E3C | flat-cr | 629 | 48 | 30,248 |
| `CR15NetBitFieldCRWin10` | 0xF460DAE1C4C8071C | pooled-cr | 120 | 24 | 30,008 |
| `CTextureStreamingCRWin10` | 0x96832B652460ECDE | flat-cr | 726 | 40 | 29,096 |
| `4e7c2e6a7ebd80e` | 0x4E7C2E6A7EBD80E | flat-cr | 311 | 80 | 24,936 |
| `CR15SyncGrabCRWin10` | 0xC8EDC00BF1D93EFE | pooled-cr | 7 | 24 | 24,320 |
| `CR15NetGunCRWin10` | 0xDBAED509C67B8884 | flat-cr | 120 | 160 | 19,256 |
| `CGMeshListResourceWin10` | 0x4E426F88C1B5D7AC | resource | 4294967295 | - | 18,656 |
| `CEventCRWin10` | 0x547B31427E1CBD8C | pooled-cr | 29 | 24 | 15,864 |
| `CTextureOverrideCRWin10` | 0x4127FF2FFE6BE26A | flat-cr | 358 | 32 | 11,512 |
| `CInstanceModelCRWin10` | 0x2464C4ED290F3268 | pooled-cr | 54 | 24 | 7,680 |
| `CR15NetIdCRWin10` | 0xC29715D62E039402 | flat-cr | 229 | 32 | 7,384 |
| `CR15PlatformCRWin10` | 0x6DCACF3BE89109A0 | pooled-cr | 6 | 24 | 4,952 |
| `CGVisibilityResourceWin10` | 0x73D312A620DA3824 | resource | 17179604975 | - | 4,380 |
| `79586e19869a090` | 0x79586E19869A090 | flat-cr | 129 | 32 | 4,184 |
| `CR15TrackPointCRWin10` | 0x451830F92DDC5CC6 | flat-cr | 69 | 56 | 3,920 |
| `dbc9c94837220ee` | 0xDBC9C94837220EE | pooled-cr | 1 | 200 | 3,720 |
| `CDecalCRWin10` | 0x3B5DB8AF43546D40 | flat-cr | 11 | 296 | 3,312 |
| `f0fb3116ec3f644` | 0xF0FB3116EC3F644 | pooled-cr | 6 | 440 | 2,864 |
| `CR15LinearConstraintTouchInteractCRWin10` | 0x10E2D7FF635E6162 | flat-cr | 9 | 288 | 2,648 |
| `CLegacyCameraDataCRWin10` | 0x38F8036A376F5F64 | pooled-cr | 1 | 192 | 2,312 |
| `CGTextureStreamingResourceWin10` | 0xC2434C5A99E139CE | resource | 15150362026338664807 | - | 2,168 |
| `CPhysicsCRWin10` | 0xF6BAB7207D923478 | flat-cr | 42 | 48 | 2,072 |
| `CR15TouchInteractCRWin10` | 0xC2BF1B5284855E4A | flat-cr | 31 | 64 | 2,040 |
| `CBillboardCRWin10` | 0x615E262159C487E0 | flat-cr | 23 | 80 | 1,896 |
| `CParticleEffectCRWin10` | 0x21A09C2016D8F3E6 | flat-cr | 11 | 152 | 1,728 |
| `COcclusionCullCRWin10` | 0x142026F469321D54 | flat-cr | 45 | 32 | 1,496 |
| `CFrustumCullCRWin10` | 0xCA5A03D5A497238C | flat-cr | 45 | 32 | 1,496 |
| `CGFSEffectsResourceWin10` | 0x7D687BA03866061E | resource | 4575657222473777152 | - | 1,416 |
| `CR15NetBulletCRWin10` | 0x4FD65C8D2B63D470 | flat-cr | 9 | 136 | 1,280 |
| `CR15LinearPositionConstraintCRWin10` | 0x68C32C04284FB022 | flat-cr | 9 | 136 | 1,280 |
| `CComponentSpaceResourceWin10` | 0x6901365C8BB8BF50 | resource | 32 | - | 1,280 |
| `CGReflectionProbeResourceWin10` | 0x2829C885034AFCDE | pooled-cr | 12 | 56 | 1,192 |
| `CTeamCRWin10` | 0xB9A46F348CBF3BC6 | pooled-cr | 9 | 24 | 1,176 |
| `CR15TeamCRWin10` | 0x991DF4582160DDC0 | pooled-cr | 8 | 24 | 1,072 |
| `CR15OrientationConstraintCRWin10` | 0x4E285CA5CA255902 | flat-cr | 9 | 112 | 1,064 |
| `CR15NetExplosionCRWin10` | 0x4AF215B19B297C34 | flat-cr | 9 | 96 | 920 |
| `CR15NetAimAssistCRWin10` | 0xBD8575CC9885E590 | flat-cr | 15 | 48 | 776 |
| `CR15NetActorCRWin10` | 0x24C73CB2EBE38BFC | flat-cr | 17 | 32 | 600 |
| `CGLightMapResourceWin10` | 0x230554BC3BECA38C | resource | 18446744073709551615 | - | 588 |
| `CR15NetPhysicsCRWin10` | 0x71131E0E3B8AA17C | flat-cr | 12 | 40 | 536 |
| `CR15NetDamageableCRWin10` | 0xBD1868F576836696 | flat-cr | 8 | 56 | 504 |
| `CR15NetTouchInteractCRWin10` | 0xEEC5CC16EA02F8FC | flat-cr | 11 | 40 | 496 |
| `fb620e994d00128` | 0xFB620E994D00128 | pooled-cr | 4 | 88 | 472 |
| `CR15CollisionCRWin10` | 0x17FAF18DA79D0E78 | flat-cr | 7 | 56 | 448 |
| `CTagCRWin10` | 0x1C718652028E0984 | pooled-cr | 1 | 24 | 424 |
| `CSharedCanvasUICRWin10` | 0xDAB7DCE1DF894EF6 | flat-cr | 5 | 72 | 416 |
| `CR15ParentedConstraintCRWin10` | 0xF2676C58C07BAB0C | flat-cr | 11 | 32 | 408 |
| `CR15BounceCRWin10` | 0x74EDE05B09640CEA | flat-cr | 7 | 48 | 392 |
| `CGStaticInstanceResourceWin10` | 0x77C0BF257CA92AA0 | resource | 0 | - | 376 |
| `CR15InteractOutputCRWin10` | 0x4E0D508C8911AB6A | flat-cr | 9 | 32 | 344 |
| `CR15NetMagazineCRWin10` | 0x5B723F040A3778EE | flat-cr | 7 | 40 | 336 |
| `CR15UIPageCRWin10` | 0xD282D4778B4EDDB2 | pooled-cr | 1 | 264 | 336 |
| `CR15ImpulseToHandTouchTypeCRWin10` | 0x7A7E782B928652BC | flat-cr | 8 | 32 | 312 |
| `CR15SpawnPointCRWin10` | 0xCB75EEE100D282A8 | flat-cr | 5 | 48 | 296 |
| `2d2be13ea8fb8ae` | 0x2D2BE13EA8FB8AE | flat-cr | 7 | 32 | 280 |
| `CR15NetSensorTargetCRWin10` | 0xE51718C1E4669474 | flat-cr | 7 | 32 | 280 |
| `CR15DampenerCRWin10` | 0x2B292830E51C4FD2 | flat-cr | 3 | 64 | 248 |
| `CActorRegionLODCRWin10` | 0x409758926E4728C4 | flat-cr | 6 | 32 | 248 |
| `CActorLODCRWin10` | 0x424FB75EFEE13BA6 | flat-cr | 4 | 48 | 248 |
| `CComponentRegionLODCRWin10` | 0x22CCEC2D7D8B0FFA | pooled-cr | 1 | 88 | 240 |
| `CComponentLODZoneCRWin10` | 0x39C0EE986726DFD2 | pooled-cr | 1 | 144 | 240 |
| `CR15UILayoutCRWin10` | 0x8B91EFD51747C21E | pooled-cr | 1 | 96 | 200 |
| `CR15FrisbeeCRWin10` | 0x1875FEBBEAB23F44 | flat-cr | 4 | 32 | 184 |
| `CR15NetAutoTargetCRWin10` | 0x975113E786B2C0A4 | flat-cr | 1 | 104 | 160 |
| `CR15RigidAttachConstraintCRWin10` | 0xDDE8FFFC5B4D7AD4 | flat-cr | 2 | 40 | 136 |
| `CR15NetPayloadCRWin10` | 0x3850C0283892236E | flat-cr | 1 | 72 | 128 |
| `CR15TriggerCRWin10` | 0x39284E7992BEFC92 | flat-cr | 1 | 72 | 128 |
| `CR15NetTrackMoverCRWin10` | 0xE826937E7F7D0994 | flat-cr | 1 | 48 | 104 |
| `CR15NetFollowPlayerCRWin10` | 0xA7BCBF4B204A8916 | flat-cr | 1 | 40 | 96 |
| `CR15NetPooledActorCRWin10` | 0xC28A49E8E5639D32 | flat-cr | 1 | 40 | 96 |
| `CMaterialTypeCRWin10` | 0x3B87EF2FC94A9B14 | flat-cr | 1 | 32 | 88 |
| `CR15NetDynamicCoverCRWin10` | 0x42D101B2307BBC16 | flat-cr | 1 | 32 | 88 |
| `CR15NetVarCRWin10` | 0xAC05E44816844A00 | flat-cr | 1 | 32 | 88 |
| `CStaticRaycastCRWin10` | 0xD649A90FFF322C12 | flat-cr | 1 | 32 | 88 |
| `CInputCRWin10` | 0xEEC3FCAE33F68500 | flat-cr | 1 | 32 | 88 |
| `CR15NetKillTickerCRWin10` | 0xF289249001A4BED6 | flat-cr | 1 | 32 | 88 |
| `CPhysicsResourceWin10` | 0xB7D338793FA37832 | resource | 0 | - | 72 |
| `COccluderMeshResourceWin10` | 0xB5CBB950CB55A92C | empty-cr | 0 | - | 56 |
| `CGameLevelInfoResourceWin10` | 0x858055BBD5655F64 | - | - | - | 16 |
| `7b9a4c9fd03f37eb` | 0x7B9A4C9FD03F37EB | - | - | - | 0 |
| `dd3ff9850e4eed35` | 0xDD3FF9850E4EED35 | - | - | - | 0 |

## Gameplay records

Decoded in detail in [../combat/GAMEPLAY.md](../combat/GAMEPLAY.md): the payload
objective and its tuning floats, the 69 track points (entry order proven to be
route order), the track mover, the five band-2 spawns at ids 200-204, and the
combat trigger.
