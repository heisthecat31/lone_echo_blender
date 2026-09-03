# Every undecoded type in the archive — measured

All 203 types the level index marks `no_decoder`, across all 32 levels.
188 were found in the extracted data and are measured here; 15 ship in no level
this extract contains (listed at the bottom).

Columns:

* **Shape** — `flat-cr` (entries only), `pooled-cr` (entries + an ordered pool),
  `resource` (not a component record), `empty-cr`, `tiny`. A type showing two
  shapes is flat in levels where its pool happens to be empty.
* **Stride** — `data_size / count`, and it agreed in **every** level for all
  182 record types. That cross-map agreement is the check.
* **Confirmed** — `+0` constant within each file (the CS node hash) *and* every
  `+8` resolving in that level's `CActorDataResource`.
* **Tables** — inline tables per entry, for pooled records whose layout is
  solved (see POOLED.md).
* **Identical** — the file is byte-for-byte the same in every level shipping it
  (see IDENTICAL.md).

| Type | Type hash | Shape | Stride | Confirmed | Levels | Entries | Tables | |
|---|---|---|---|---|---|---|---|---|
| `CActorAliasCRWin10` | 0x05D48844E3733C5E | flat-cr | 48 | yes | 1 | 1 |  |  |
| `CActorJugglerCRWin10` | 0x975B4C59D4B2F418 | pooled-cr | 144 | yes | 1 | 0 | 2 |  |
| `CActorLODCRWin10` | 0x424FB75EFEE13BA6 | flat-cr | 48 | yes | 12 | 237 |  |  |
| `CActorRegionLODCRWin10` | 0x409758926E4728C4 | flat-cr | 32 | yes | 10 | 540 |  |  |
| `CAmbientSoundCRWin10` | 0x4047315F08901C70 | flat-cr | 280 | yes | 6 | 61 |  |  |
| `CAnimationCRWin10` | 0xDB098C12AD9B9844 | pooled-cr | 24 | no | 17 | 0 | ? |  |
| `CBillboardCRWin10` | 0x615E262159C487E0 | flat-cr | 80 | no | 17 | 247 |  |  |
| `CBlackboardCRWin10` | 0xD6E7A7F4813D3656 | pooled-cr | 592 | yes | 4 | 0 | 6 |  |
| `CBoundingSphereCRWin10` | 0x22F9FCB2D5E52E3C | flat-cr | 48 | no | 30 | 6291 |  |  |
| `CCharacterAnimationCRWin10` | 0x871EEF513970CFF6 | pooled-cr | 24 | no | 5 | 0 | ? |  |
| `CCheckpointCRWin10` | 0x97C5A1DF329BAF40 | pooled-cr | 24 | no | 6 | 0 | ? |  |
| `CComponentLODCRWin10` | 0x7F49ABAE39AAF2AA | pooled-cr | 96 | no | 18 | 0 | ? |  |
| `CComponentLODZoneCRWin10` | 0x39C0EE986726DFD2 | pooled-cr | 144 | yes | 5 | 0 | 2 |  |
| `CComponentRegionLODCRWin10` | 0x22CCEC2D7D8B0FFA | flat-cr+pooled-cr | 88 | yes | 10 | 11 | 1 |  |
| `CComponentSpaceResourceWin10` | 0x6901365C8BB8BF50 | resource | - | n/a | 31 | 0 |  |  |
| `CDecalCRWin10` | 0x3B5DB8AF43546D40 | flat-cr | 296 | no | 18 | 1341 |  |  |
| `CDialogue2CRWin10` | 0xD3C86A196705C13C | flat-cr | 176 | yes | 11 | 17 |  |  |
| `CDialogueSceneCRWin10` | 0x9DDA14546287C580 | flat-cr | 32 | yes | 2 | 3 |  |  |
| `CDynamicLODRegionTargetCRWin10` | 0x9A613982DD36EC92 | flat-cr | 32 | yes | 6 | 57 |  |  |
| `CEventCRWin10` | 0x547B31427E1CBD8C | pooled-cr | 24 | no | 26 | 0 | ? |  |
| `CFrustumCullCRWin10` | 0xCA5A03D5A497238C | flat-cr | 32 | no | 27 | 11139 |  |  |
| `CGFSEffectsResourceWin10` | 0x7D687BA03866061E | resource | - | n/a | 31 | 0 |  |  |
| `CGReflectionProbeResourceWin10` | 0x2829C885034AFCDE | pooled-cr+resource | 56 | no | 31 | 0 | ? |  |
| `CGVisibilityResourceWin10` | 0x73D312A620DA3824 | resource | - | n/a | 31 | 0 |  |  |
| `CGameLevelInfoResourceWin10` | 0x858055BBD5655F64 | tiny | - | n/a | 31 | 0 |  |  |
| `CGameLevelResourceWin10` | 0xE8E38D7781A338A6 | resource | - | n/a | 31 | 0 |  |  |
| `CInputCRWin10` | 0xEEC3FCAE33F68500 | flat-cr | 32 | yes | 11 | 13 |  |  |
| `CJsonConfigCRWin10` | 0xCB3478C2A8CD87CC | pooled-cr | 24 | no | 3 | 0 | ? |  |
| `CLODRegionCRWin10` | 0x1258CF094C80C4D2 | pooled-cr | 200 | yes | 5 | 0 | 3 |  |
| `CLegacyCameraDataCRWin10` | 0x38F8036A376F5F64 | pooled-cr | 192 | yes | 23 | 0 | ? |  |
| `CLevelAABBCRWin10` | 0xB76203B6E5EAFF80 | flat-cr | 56 | yes | 24 | 8705 |  |  |
| `CListCRWin10` | 0x0F0FB3116EC3F644 | pooled-cr | 440 | no | 8 | 0 | 3 |  |
| `CMaterialTypeCRWin10` | 0x3B87EF2FC94A9B14 | flat-cr | 32 | yes | 31 | 31 |  |  |
| `CMaterialTypesBVHResourceWin10` | 0x4230B4E0957B5462 | pooled-cr+resource | 16 | no | 31 | 0 | ? |  |
| `CModelLODCRWin10` | 0x0B5DA9ABD34D392E | pooled-cr | 24 | no | 1 | 0 | ? |  |
| `CModelSwapperCRWin10` | 0x915FD67110D60292 | pooled-cr | 24 | no | 2 | 0 | ? |  |
| `COccluderMeshCRWin10` | 0x85D28CE20F6A83F8 | flat-cr | 48 | yes | 4 | 22 |  |  |
| `COccluderMeshResourceWin10` | 0xB5CBB950CB55A92C | empty-cr+pooled-cr | 136 | no | 31 | 0 | 2 |  |
| `COcclusionCullCRWin10` | 0x142026F469321D54 | flat-cr | 32 | no | 27 | 11117 |  |  |
| `CParticleEffectCRWin10` | 0x21A09C2016D8F3E6 | flat-cr+pooled-cr | 152 | no | 9 | 64 | 1 |  |
| `CPhysicsCRWin10` | 0xF6BAB7207D923478 | flat-cr | 48 | yes | 23 | 863 |  |  |
| `CPhysicsResourceWin10` | 0xB7D338793FA37832 | resource | - | n/a | 31 | 0 |  |  |
| `CPlatformCRWin10` | 0x40861B479CAC8CD8 | pooled-cr | 24 | no | 6 | 0 | ? |  |
| `CPluginCRWin10` | 0xF5B9A3786E2C020C | pooled-cr | 24 | no | 2 | 0 | ? |  |
| `CPositionSelectionCRWin10` | 0xEF9D3D97F6A3F71C | pooled-cr | 24 | no | 1 | 0 | ? |  |
| `CR15ArmComputerSystemCRWin10` | 0xF952BA5AB225E648 | flat-cr | 88 | yes | 1 | 1 |  |  |
| `CR15AudioListenerCRWin10` | 0x05E8CC8D044039A8 | flat-cr | 32 | yes | 2 | 2 |  |  |
| `CR15BatteryPackCRWin10` | 0x098DB0CB749C99B4 | flat-cr | 56 | yes | 2 | 4 |  |  |
| `CR15BodyCRWin10` | 0xC203E475283313A4 | flat-cr | 312 | no | 4 | 12 |  |  |
| `CR15BounceCRWin10` | 0x74EDE05B09640CEA | flat-cr | 48 | yes | 10 | 51 |  |  |
| `CR15ButtonInteractCRWin10` | 0xE9B24EA816DECE48 | flat-cr | 296 | no | 17 | 2705 |  |  |
| `CR15CollisionCRWin10` | 0x17FAF18DA79D0E78 | flat-cr | 56 | yes | 5 | 35 |  | **identical** |
| `CR15ContactTrackerCRWin10` | 0x1472A245AA22E262 | flat-cr | 32 | yes | 1 | 1 |  |  |
| `CR15CreditsUICRWin10` | 0x20761564F11F2E70 | flat-cr | 32 | yes | 1 | 1 |  |  |
| `CR15DampenerCRWin10` | 0x2B292830E51C4FD2 | flat-cr | 64 | no | 8 | 46 |  |  |
| `CR15DialoguePOICRWin10` | 0x7F103187AA42673A | pooled-cr | 464 | yes | 7 | 0 | 4 |  |
| `CR15DockToViewCRWin10` | 0x6D63AE74BBA190F0 | flat-cr | 88 | yes | 1 | 1 |  |  |
| `CR15EchoPathingCRWin10` | 0x0A92D2E2FD761398 | flat-cr | 32 | yes | 1 | 1 |  |  |
| `CR15EchoUnitCRWin10` | 0xCEC6B3333D1F0182 | flat-cr | 48 | yes | 2 | 5 |  |  |
| `CR15FacingCRWin10` | 0x202DF2DFCF55DED8 | flat-cr | 32 | yes | 4 | 7 |  |  |
| `CR15FlagBaseCRWin10` | 0x8B5329AB0AC01796 | flat-cr | 216 | yes | 3 | 9 |  |  |
| `CR15FlagCRWin10` | 0x3DC5C3DD185587B2 | flat-cr | 56 | yes | 5 | 8 |  |  |
| `CR15FrisbeeCRWin10` | 0x1875FEBBEAB23F44 | flat-cr | 32 | yes | 10 | 28 |  |  |
| `CR15GestureTargetCRWin10` | 0xAD445A2A573C4D0A | flat-cr | 40 | yes | 8 | 9 |  |  |
| `CR15HandAnimatorCRWin10` | 0xDB5B3CFAE8A705D2 | flat-cr | 48 | yes | 2 | 3 |  |  |
| `CR15HeadlookCRWin10` | 0xB5C9447587FF7EF8 | flat-cr | 392 | yes | 1 | 1 |  |  |
| `CR15HeraldryCRWin10` | 0x6946E9356647EC70 | flat-cr | 112 | yes | 5 | 16 |  |  |
| `CR15ImpulseToHandTouchTypeCRWin10` | 0x7A7E782B928652BC | flat-cr | 32 | yes | 9 | 38 |  |  |
| `CR15InputPointerCRWin10` | 0x0B23AA6E4497C21E | flat-cr | 32 | yes | 2 | 3 |  |  |
| `CR15InteractOutputCRWin10` | 0x4E0D508C8911AB6A | flat-cr | 32 | no | 11 | 52 |  |  |
| `CR15KillVolCRWin10` | 0xDC08BBD0E7AD56F4 | pooled-cr | 24 | no | 2 | 0 | ? |  |
| `CR15LevelTouchInteractCRWin10` | 0xBEF368F4F866CFAC | flat-cr | 32 | no | 2 | 100 |  | **identical** |
| `CR15LinearConstraintTouchInteractCRWin10` | 0x10E2D7FF635E6162 | flat-cr | 288 | yes | 8 | 38 |  |  |
| `CR15LinearPositionConstraintCRWin10` | 0x68C32C04284FB022 | flat-cr | 136 | no | 10 | 44 |  |  |
| `CR15MenuPlayerCRWin10` | 0x509CAE81E47525E2 | pooled-cr | 104 | yes | 2 | 0 | ? |  |
| `CR15MenuTooltipCRWin10` | 0x0E27ECC8FEE165FA | flat-cr | 40 | yes | 2 | 3 |  |  |
| `CR15MirrorPlayerCRWin10` | 0x61E3C722C53615DE | flat-cr | 184 | yes | 1 | 1 |  |  |
| `CR15NetAIThrowCRWin10` | 0x5BE0BFEE054DB944 | flat-cr | 32 | yes | 1 | 1 |  |  |
| `CR15NetAIWaypointCRWin10` | 0xC0F1EE3AC3B0C412 | pooled-cr | 96 | yes | 1 | 0 | ? |  |
| `CR15NetActorCRWin10` | 0x24C73CB2EBE38BFC | flat-cr | 32 | yes | 10 | 96 |  |  |
| `CR15NetAimAssistCRWin10` | 0xBD8575CC9885E590 | flat-cr | 48 | yes | 5 | 75 |  | **identical** |
| `CR15NetAutoTargetCRWin10` | 0x975113E786B2C0A4 | flat-cr | 104 | yes | 5 | 5 |  | **identical** |
| `CR15NetBalanceSettingsCRWin10` | 0x0FB620E994D00128 | pooled-cr | 88 | yes | 7 | 0 | 1 |  |
| `CR15NetBitFieldCRWin10` | 0xF460DAE1C4C8071C | pooled-cr | 24 | no | 5 | 0 | ? | **identical** |
| `CR15NetBoosterCRWin10` | 0xFB92FB2F6F6D1F58 | flat-cr | 32 | yes | 1 | 6 |  |  |
| `CR15NetBotAnimCRWin10` | 0x5304C307D5B08742 | flat-cr | 32 | yes | 1 | 2 |  |  |
| `CR15NetBotCRWin10` | 0x12BD2C7D6BF71FDC | flat-cr | 32 | yes | 1 | 2 |  |  |
| `CR15NetBulletCRWin10` | 0x4FD65C8D2B63D470 | flat-cr | 136 | yes | 5 | 45 |  | **identical** |
| `CR15NetCaptureVolumeCRWin10` | 0x93A1C9EAAAA00B44 | pooled-cr | 96 | yes | 2 | 0 | 1 |  |
| `CR15NetCustomizationCRWin10` | 0x6E03CA8E7F468AAA | flat-cr+pooled-cr | 128 | yes | 7 | 2 | 1 |  |
| `CR15NetDamageableCRWin10` | 0xBD1868F576836696 | flat-cr | 56 | yes | 7 | 97 |  |  |
| `CR15NetDebugDrawCRWin10` | 0x079586E19869A090 | flat-cr | 32 | yes | 4 | 516 |  | **identical** |
| `CR15NetDebugInputRecorderCRWin10` | 0x1FC097A004E12724 | flat-cr | 32 | yes | 1 | 1 |  |  |
| `CR15NetDynamicCoverCRWin10` | 0x42D101B2307BBC16 | flat-cr | 32 | yes | 5 | 5 |  | **identical** |
| `CR15NetDynamicPosterCRWin10` | 0x267366A86FEEC098 | flat-cr | 72 | yes | 4 | 59 |  |  |
| `CR15NetExplosionCRWin10` | 0x4AF215B19B297C34 | flat-cr | 96 | no | 6 | 48 |  |  |
| `CR15NetFollowPlayerCRWin10` | 0xA7BCBF4B204A8916 | flat-cr | 40 | yes | 5 | 5 |  | **identical** |
| `CR15NetFrisbeeTrailCRWin10` | 0xD6FE68E19923FE44 | flat-cr | 48 | yes | 2 | 2 |  |  |
| `CR15NetGhostLODCRWin10` | 0x012B9CC38A29755A | flat-cr+pooled-cr | 88 | yes | 6 | 2224 | 1 |  |
| `CR15NetGunCRWin10` | 0xDBAED509C67B8884 | flat-cr | 160 | yes | 5 | 600 |  | **identical** |
| `CR15NetHealBubbleCRWin10` | 0x1AD01A2A91709A1A | flat-cr | 72 | yes | 1 | 3 |  |  |
| `CR15NetHealBubbleTargetCRWin10` | 0xFA06459149D98D30 | flat-cr | 32 | yes | 1 | 3 |  |  |
| `CR15NetHoloBitCRWin10` | 0xB3DE608077189596 | flat-cr | 32 | yes | 2 | 52 |  | **identical** |
| `CR15NetIdCRWin10` | 0xC29715D62E039402 | flat-cr | 32 | yes | 11 | 1139 |  |  |
| `CR15NetKillTickerCRWin10` | 0xF289249001A4BED6 | flat-cr | 32 | yes | 4 | 4 |  | **identical** |
| `CR15NetMagazineCRWin10` | 0x5B723F040A3778EE | flat-cr | 40 | yes | 5 | 35 |  | **identical** |
| `CR15NetMagazinePouchCRWin10` | 0x2D90EE7BD8175EC2 | flat-cr | 96 | yes | 1 | 3 |  |  |
| `CR15NetMetricsCRWin10` | 0x635114B61DE7E264 | flat-cr | 32 | yes | 2 | 2 |  | **identical** |
| `CR15NetPackageDownloadCRWin10` | 0x9C61645215480A4A | flat-cr | 40 | yes | 2 | 3 |  |  |
| `CR15NetPayloadCRWin10` | 0x3850C0283892236E | flat-cr | 72 | yes | 2 | 2 |  |  |
| `CR15NetPhysicsCRWin10` | 0x71131E0E3B8AA17C | flat-cr | 40 | yes | 10 | 93 |  |  |
| `CR15NetPlayerEquipmentCRWin10` | 0x59105E9CAA27E82A | flat-cr | 64 | yes | 1 | 3 |  |  |
| `CR15NetPlayerModelSwapperCRWin10` | 0x8895458CDD7DA876 | pooled-cr | 24 | no | 2 | 0 | ? |  |
| `CR15NetPooledActorCRWin10` | 0xC28A49E8E5639D32 | flat-cr | 40 | yes | 10 | 10 |  |  |
| `CR15NetPropertyModifiersCRWin10` | 0xADBA2E358D0CE5C4 | flat-cr | 40 | yes | 1 | 4 |  |  |
| `CR15NetPunchCRWin10` | 0x8EB18A4D55D0F590 | pooled-cr | 96 | yes | 1 | 0 | ? |  |
| `CR15NetPunchableCRWin10` | 0xC0C407DB04436F3C | flat-cr | 48 | yes | 4 | 30 |  |  |
| `CR15NetRemoteVolumeQueryCRWin10` | 0x967DF5A2E169F9F0 | flat-cr | 88 | yes | 3 | 4 |  |  |
| `CR15NetRewardItemCRWin10` | 0x32F30FE361939DEE | pooled-cr | 664 | no | 1 | 0 | 2 |  |
| `CR15NetRewardItemSorterCRWin10` | 0xD629140F02C8DE38 | flat-cr | 112 | yes | 1 | 21 |  |  |
| `CR15NetSensorCRWin10` | 0x63D75F570B7C7B08 | flat-cr | 48 | yes | 1 | 3 |  |  |
| `CR15NetSensorTargetCRWin10` | 0xE51718C1E4669474 | flat-cr | 32 | yes | 6 | 51 |  |  |
| `CR15NetSocialInteractCRWin10` | 0x0BD005335676F466 | flat-cr | 32 | yes | 1 | 6 |  |  |
| `CR15NetSpectatorCameraCRWin10` | 0x0DBC9C94837220EE | pooled-cr | 200 | yes | 6 | 0 | ? |  |
| `CR15NetTouchInteractCRWin10` | 0xEEC5CC16EA02F8FC | flat-cr | 40 | yes | 9 | 117 |  |  |
| `CR15NetTrackMoverCRWin10` | 0xE826937E7F7D0994 | flat-cr | 48 | yes | 2 | 2 |  |  |
| `CR15NetUISettingsCRWin10` | 0x515BF2B6E2687C68 | pooled-cr | 24 | no | 2 | 0 | ? | **identical** |
| `CR15NetUpdateExpressionDataCRWin10` | 0x883938ED52F689E4 | flat-cr | 32 | yes | 1 | 1 |  |  |
| `CR15NetVarCRWin10` | 0xAC05E44816844A00 | flat-cr | 32 | no | 8 | 10 |  |  |
| `CR15NetVoipBroadcasterCRWin10` | 0x4F8B589766FDDC68 | pooled-cr | 88 | yes | 3 | 0 | ? |  |
| `CR15NetVoipReceiverCRWin10` | 0x7120AB790B3FB83A | flat-cr | 32 | yes | 1 | 4 |  |  |
| `CR15ObjectiveTrackerCRWin10` | 0x671B4C160B346FF0 | flat-cr | 128 | yes | 2 | 3 |  |  |
| `CR15OrientationConstraintCRWin10` | 0x4E285CA5CA255902 | flat-cr | 112 | no | 11 | 46 |  |  |
| `CR15ParentedConstraintCRWin10` | 0xF2676C58C07BAB0C | flat-cr | 32 | yes | 14 | 62 |  |  |
| `CR15PlatformCRWin10` | 0x6DCACF3BE89109A0 | pooled-cr | 24 | no | 4 | 0 | ? |  |
| `CR15PlayerBroadcasterCRWin10` | 0x0E796139581EF81E | flat-cr | 32 | yes | 1 | 4 |  |  |
| `CR15PlayerNavCRWin10` | 0x15A4D439EB6A519E | pooled-cr | 208 | yes | 2 | 0 | ? |  |
| `CR15PointerCRWin10` | 0xBF101810836F65D8 | flat-cr | 48 | yes | 4 | 5 |  |  |
| `CR15PointerInterfaceCRWin10` | 0xE4A9B494CE3BA6E0 | flat-cr | 32 | yes | 3 | 139 |  |  |
| `CR15PointerMeshCRWin10` | 0x2972B798AA1CF688 | flat-cr | 80 | yes | 3 | 139 |  |  |
| `CR15PositionConstraintCRWin10` | 0x1A113B501F2133E4 | flat-cr | 40 | yes | 2 | 3 |  |  |
| `CR15PropInputCRWin10` | 0xEAC9FB460629C57E | pooled-cr | 24 | no | 1 | 0 | ? |  |
| `CR15RadiationShieldsCRWin10` | 0x3F96729C19DDC8C8 | flat-cr | 64 | yes | 2 | 3 |  |  |
| `CR15RemotePlayerCRWin10` | 0xAE135E5AB55304E2 | flat-cr | 32 | yes | 1 | 2 |  |  |
| `CR15RigidAttachConstraintCRWin10` | 0xDDE8FFFC5B4D7AD4 | flat-cr | 40 | no | 7 | 20 |  |  |
| `CR15RotationConstraintCRWin10` | 0xD34BDE625004DF5C | flat-cr | 176 | yes | 1 | 2 |  |  |
| `CR15RumbleCRWin10` | 0xAE44A8C5686B89A8 | flat-cr | 32 | yes | 2 | 3 |  |  |
| `CR15SettingsTranslatorCRWin10` | 0xB0D8B0AF33FDA95A | flat-cr | 32 | yes | 2 | 2 |  |  |
| `CR15SliderCRWin10` | 0xFC3815C51F35467A | flat-cr | 184 | yes | 1 | 2 |  |  |
| `CR15SmoothAttacherCRWin10` | 0x58AB627A32613BB2 | flat-cr | 96 | yes | 4 | 39 |  |  |
| `CR15SpawnPointCRWin10` | 0xCB75EEE100D282A8 | flat-cr | 48 | yes | 13 | 85 |  |  |
| `CR15StandardPOICRWin10` | 0xD64551CD9A4DD92A | flat-cr | 280 | yes | 4 | 41 |  |  |
| `CR15StaticArtTouchInteractCRWin10` | 0x7DF22C27E143C2EA | flat-cr | 32 | no | 2 | 6 |  | **identical** |
| `CR15StickyCRWin10` | 0x4A63EDB14DC42414 | flat-cr | 136 | yes | 1 | 6 |  |  |
| `CR15StickySurfaceCRWin10` | 0xEC4547A5BAF4B6B2 | flat-cr | 88 | yes | 4 | 17 |  |  |
| `CR15SyncGrabCRWin10` | 0xC8EDC00BF1D93EFE | pooled-cr | 24 | no | 14 | 0 | ? |  |
| `CR15TagCRWin10` | 0x33B05DC2277E7E6C | pooled-cr | 24 | no | 2 | 0 | ? |  |
| `CR15TeamCRWin10` | 0x991DF4582160DDC0 | pooled-cr | 24 | no | 8 | 0 | ? |  |
| `CR15ThereminCRWin10` | 0xD2DA34C25E28F4A2 | flat-cr | 112 | yes | 1 | 3 |  |  |
| `CR15TouchAnimBlendersCRWin10` | 0x4FEDA95613A2D0BA | flat-cr | 48 | no | 4 | 8 |  |  |
| `CR15TouchInteractCRWin10` | 0xC2BF1B5284855E4A | flat-cr | 64 | yes | 17 | 348 |  |  |
| `CR15TrackPointCRWin10` | 0x451830F92DDC5CC6 | flat-cr | 56 | yes | 2 | 250 |  |  |
| `CR15TriggerCRWin10` | 0x39284E7992BEFC92 | flat-cr | 72 | yes | 5 | 5 |  | **identical** |
| `CR15UILayoutCRWin10` | 0x8B91EFD51747C21E | pooled-cr | 96 | yes | 12 | 0 | ? |  |
| `CR15UIPage2CRWin10` | 0x4A3FE67C1FF9CCC4 | pooled-cr | 328 | yes | 3 | 0 | ? |  |
| `CR15UIPage2ElementCRWin10` | 0xC1B1FCA890787B30 | pooled-cr | 96 | yes | 3 | 0 | 1 |  |
| `CR15UIPageCRWin10` | 0xD282D4778B4EDDB2 | pooled-cr | 264 | yes | 11 | 0 | ? |  |
| `CR15UISettingsCRWin10` | 0x04DA13B8BF4FB47E | pooled-cr | 24 | no | 2 | 0 | ? |  |
| `CR15VRMenuWidgetCRWin10` | 0xFB854915B12A5108 | flat-cr | 96 | no | 3 | 69 |  |  |
| `CResourceLoaderCRWin10` | 0xBB0A36E2054C45C0 | flat-cr | 32 | yes | 7 | 18 |  |  |
| `CRxAICRWin10` | 0x69F6426374C30ADE | pooled-cr | 144 | yes | 1 | 0 | ? |  |
| `CRxMaterialFXCRWin10` | 0x9D7C9773CF1D74E2 | flat-cr | 56 | no | 1 | 2 |  |  |
| `CSVOPathPlannerCRWin10` | 0xC953B3E9E48AB9AC | flat-cr | 32 | yes | 2 | 2 |  |  |
| `CSVOResourceWin10` | 0xBCE9C410B354B078 | pooled-cr | 2032 | no | 1 | 0 | ? |  |
| `CSVOVolumeCRWin10` | 0x7A40DBFDC05807FC | flat-cr | 232 | yes | 1 | 1 |  |  |
| `CScriptCRWin10` | 0xD99F6BBD8009C92C | flat-cr+pooled-cr | 720 | no | 31 | 3 | 9 |  |
| `CScriptStateStackCRWin10` | 0x5EBB0A68CD6CC76A | flat-cr | 32 | no | 1 | 2 |  |  |
| `CSettingsTranslatorCRWin10` | 0xFF5BAD1FCF4C464C | pooled-cr | 24 | no | 2 | 0 | ? |  |
| `CSettingsUICRWin10` | 0x130C1B8FCC7D1DCE | pooled-cr | 24 | no | 2 | 0 | ? |  |
| `CSharedCanvasUICRWin10` | 0xDAB7DCE1DF894EF6 | flat-cr | 72 | yes | 7 | 108 |  |  |
| `CSoundCRWin10` | 0x04E7C2E6A7EBD80E | flat-cr | 80 | yes | 12 | 1511 |  |  |
| `CStaticLODRegionTargetCRWin10` | 0x02D2BE13EA8FB8AE | flat-cr | 32 | yes | 8 | 423 |  |  |
| `CStaticRaycastCRWin10` | 0xD649A90FFF322C12 | flat-cr | 32 | yes | 31 | 31 |  |  |
| `CSyncCRWin10` | 0xEFFEC49E45650654 | flat-cr | 32 | yes | 10 | 17 |  |  |
| `CTagCRWin10` | 0x1C718652028E0984 | pooled-cr | 24 | no | 12 | 0 | ? |  |
| `CTeamCRWin10` | 0xB9A46F348CBF3BC6 | pooled-cr | 24 | no | 7 | 0 | ? |  |
| `CTextureOverrideCRWin10` | 0x4127FF2FFE6BE26A | flat-cr | 32 | no | 18 | 2089 |  |  |
| `CTextureStreamingCRWin10` | 0x96832B652460ECDE | flat-cr | 40 | no | 28 | 4508 |  |  |

## Not present in this extract (15)

These are named by levels in the index but ship in packages this extract does
not contain — mostly global asset resources rather than level records.

* `CGFSEffectsVolumeResourceWin10`
* `CGParticleEffectResourceWin10`
* `CGParticleGraphResourceWin10`
* `CGStandaloneShaderResourceWin10`
* `CJsonResourceWin10`
* `CLanguageTableResourceWin10`
* `CLensConfigResourceWin10`
* `CLensResourceWin10`
* `CQuantizerResourceWin10`
* `CRadSourceResourceWin10`
* `CRxMaterialFXResourceWin10`
* `CScriptResourceWin10`
* `CSettingsUIResourceWin10`
* `CTTFontResourceWin10`
* `CWWiseSoundBankResourceWin10`
