# Combat resource types — not component records

These 27 types do **not** use the CR envelope: their `+8` field is not
`len - 56`, so they are standalone resources (geometry, physics, audio, UI,
scripts), each with its own container format. Sizes in bytes per level, which is
the honest state of knowledge for most of them.

Two are decoded in detail elsewhere in this folder:

* `CScriptResourceWin10` / `StreamingScriptWin10` — see **SCRIPTS.md**
* `CComponentSpaceResourceWin10` — see **COMPONENT_SPACE.md**

| Type | Type hash | fission | gauss | dyson | combustion | war_room | lobby_b_combat |
|---|---|---|---|---|---|---|---|
| `CAnimationCRWin10` | 0xDB098C12AD9B9844 | 151280 | 151736 | 150856 | 150432 | - | 135592 |
| `CComponentLODCRWin10` | 0x7F49ABAE39AAF2AA | 78200 | 78536 | 79208 | 78536 | - | 70248 |
| `CComponentLODZoneCRWin10` | 0x39C0EE986726DFD2 | 240 | 240 | 240 | 240 | - | 240 |
| `CComponentSpaceResourceWin10` | 0x6901365C8BB8BF50 | 1280 | 1328 | 1184 | 1184 | - | 1104 |
| `CEventCRWin10` | 0x547B31427E1CBD8C | 15864 | 25512 | 34560 | 20688 | - | 18232 |
| `CGFSEffectsResourceWin10` | 0x7D687BA03866061E | 1416 | 1416 | 1416 | 1416 | - | 1416 |
| `CGReflectionProbeResourceWin10` | 0x2829C885034AFCDE | 1192 | 344 | 6400 | 4504 | - | 16968 |
| `CGVisibilityResourceWin10` | 0x73D312A620DA3824 | 4380 | 4592 | 86672 | 59724 | - | 265300 |
| `CGameLevelResourceWin10` | 0xE8E38D7781A338A6 | 82528 | 81680 | 85368 | 84432 | - | 86976 |
| `CLODRegionCRWin10` | 0x1258CF094C80C4D2 | - | - | - | - | - | 1592 |
| `CLegacyCameraDataCRWin10` | 0x38F8036A376F5F64 | 2312 | 2312 | 4360 | 2312 | - | 2312 |
| `CListCRWin10` | 0x0F0FB3116EC3F644 | 2864 | 2864 | 2864 | 2864 | - | - |
| `CMaterialTypesBVHResourceWin10` | 0x4230B4E0957B5462 | 304368 | 223024 | 6416544 | 6393184 | - | 6504736 |
| `CPhysicsResourceWin10` | 0xB7D338793FA37832 | 72 | 72 | 10102600 | 9429784 | - | 11715952 |
| `CPlatformCRWin10` | 0x40861B479CAC8CD8 | - | - | - | - | - | 3336 |
| `CR15NetBalanceSettingsCRWin10` | 0x0FB620E994D00128 | 472 | 472 | 472 | 472 | - | - |
| `CR15NetBitFieldCRWin10` | 0xF460DAE1C4C8071C | 30008 | 30008 | 30008 | 30008 | - | 30008 |
| `CR15NetCaptureVolumeCRWin10` | 0x93A1C9EAAAA00B44 | - | - | 168 | 264 | - | - |
| `CR15NetSpectatorCameraCRWin10` | 0x0DBC9C94837220EE | 3720 | 3720 | 3720 | 3720 | - | - |
| `CR15PlatformCRWin10` | 0x6DCACF3BE89109A0 | 4952 | 6504 | 9608 | 6504 | - | - |
| `CR15SyncGrabCRWin10` | 0xC8EDC00BF1D93EFE | 24320 | 24320 | 24320 | 24320 | - | 24320 |
| `CR15TeamCRWin10` | 0x991DF4582160DDC0 | 1072 | 1072 | 1072 | 1072 | - | 1072 |
| `CR15UILayoutCRWin10` | 0x8B91EFD51747C21E | 200 | 200 | 200 | 200 | - | - |
| `CR15UIPageCRWin10` | 0xD282D4778B4EDDB2 | 336 | 336 | 336 | 336 | - | - |
| `CScriptCRWin10` | 0xD99F6BBD8009C92C | 383848 | 426128 | 412392 | 388912 | - | 326968 |
| `CTagCRWin10` | 0x1C718652028E0984 | 424 | 424 | - | - | - | - |
| `CTeamCRWin10` | 0xB9A46F348CBF3BC6 | 1176 | 1176 | 1176 | 1176 | - | 1176 |
