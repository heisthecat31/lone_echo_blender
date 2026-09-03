# Combat CR types — measured layouts

Every type the level index marks `no_decoder` that turns out to be a **flat
component record** (the 56-byte CR envelope, then `count` fixed-size entries and
nothing else). Measured directly from the shipped bytes of all six combat levels.

**Stride is not guessed.** For each type, `stride = data_size / count`, and the
value agreed across **every level that ships the type** — all 61 of them, no
exceptions. That agreement is the check: a wrong stride would disagree between a
map with 12 entries and one with 700.

**"Binds actors" = confirmed.** A flat CR is confirmed when (a) every entry's
`+0` holds the *same* value — the component-system node hash — and (b) every
entry's `+8` is an actor that really exists in that level's `CActorDataResource`.
55 of 61 pass both. The 6 that do not are marked `no`: their rows reference
actors from outside the level (pooled/instanced actors), so the `+8` test cannot
confirm them, though the stride still agrees everywhere.

Counts are entries per level.

| Type | CS node hash (+0) | Stride | Binds actors | Type hash | fission | gauss | dyson | combustion | war_room | lobby_b_combat |
|---|---|---|---|---|---|---|---|---|---|---|
| `CActorLODCRWin10` | 0x37EE80183BF38B62 | 48 | yes | 0x424FB75EFEE13BA6 | 4 | 4 | 4 | 4 | - | 25 |
| `CActorRegionLODCRWin10` | 0x3322D914A642A24E | 32 | yes | 0x409758926E4728C4 | 6 | 6 | 6 | 6 | - | 8 |
| `CAmbientSoundCRWin10` | 0x6089664C2C9EDD3C | 280 | yes | 0x4047315F08901C70 | - | 14 | - | - | - | - |
| `CBillboardCRWin10` | 0x974A2D380950622C | 80 | yes | 0x615E262159C487E0 | 23 | 25 | 22 | 14 | - | 39 |
| `CBoundingSphereCRWin10` | 0x5430D599D93889C7 | 48 | yes | 0x22F9FCB2D5E52E3C | 629 | 644 | 1098 | 741 | - | 606 |
| `CComponentRegionLODCRWin10` | 0xCCE4A1D216A3B10C | 88 | yes | 0x22CCEC2D7D8B0FFA | 1 | 1 | 1 | 1 | - | 4 |
| `CDecalCRWin10` | 0xE32DC7DEDC8C45BC | 296 | yes | 0x3B5DB8AF43546D40 | 11 | 9 | 361 | 82 | - | 77 |
| `CDynamicLODRegionTargetCRWin10` | 0xFFF35F2A0CA1E3A0 | 32 | yes | 0x9A613982DD36EC92 | - | - | - | - | - | 6 |
| `CFrustumCullCRWin10` | 0x0EFF81786EE23F1A | 32 | yes | 0xCA5A03D5A497238C | 45 | 44 | 1185 | 865 | - | 964 |
| `CInputCRWin10` | 0x38EE951E27EF9172 | 32 | yes | 0xEEC3FCAE33F68500 | 1 | 1 | 1 | 1 | - | - |
| `CLevelAABBCRWin10` | 0x3A4FC347109D8206 | 56 | yes | 0xB76203B6E5EAFF80 | - | 1 | 736 | 749 | - | 859 |
| `CMaterialTypeCRWin10` | 0xF4675E412BBF816F | 32 | yes | 0x3B87EF2FC94A9B14 | 1 | 1 | 1 | 1 | - | 1 |
| `COcclusionCullCRWin10` | 0xF9A77C39836AAAAA | 32 | yes | 0x142026F469321D54 | 45 | 44 | 1185 | 865 | - | 964 |
| `CParticleEffectCRWin10` | 0x3D6E1DDBC3795FD4 | 152 | no | 0x21A09C2016D8F3E6 | 11 | 16 | 11 | 11 | - | 11 |
| `CPhysicsCRWin10` | 0x5B8CC538E22AD937 | 48 | yes | 0xF6BAB7207D923478 | 42 | 70 | 144 | 49 | - | 70 |
| `CR15BounceCRWin10` | 0x7A8046009A26458D | 48 | yes | 0x74EDE05B09640CEA | 7 | 7 | 7 | 7 | - | 11 |
| `CR15ButtonInteractCRWin10` | 0x23A0EF8FAFD233EE | 296 | no | 0xE9B24EA816DECE48 | 517 | 516 | 518 | 516 | - | 486 |
| `CR15CollisionCRWin10` | 0x9EEFFF46EF778E3A | 56 | yes | 0x17FAF18DA79D0E78 | 7 | 7 | 7 | 7 | - | 7 |
| `CR15DampenerCRWin10` | 0x85F1EF96072808DA | 64 | no | 0x2B292830E51C4FD2 | 3 | 3 | 3 | 3 | - | 7 |
| `CR15FrisbeeCRWin10` | 0x84C65832F0B8B181 | 32 | yes | 0x1875FEBBEAB23F44 | 4 | 4 | 4 | 4 | - | 4 |
| `CR15ImpulseToHandTouchTypeCRWin10` | 0x82A3EB45E149376D | 32 | yes | 0x7A7E782B928652BC | 8 | 10 | - | - | - | - |
| `CR15InteractOutputCRWin10` | 0x9A6F1E833B500BCA | 32 | yes | 0x4E0D508C8911AB6A | 9 | 10 | - | - | - | - |
| `CR15LinearConstraintTouchInteractCRWin10` | 0x9A6F1E833B500BCA | 288 | yes | 0x10E2D7FF635E6162 | 9 | 10 | - | - | - | - |
| `CR15LinearPositionConstraintCRWin10` | 0x9A6F1E833B500BCA | 136 | yes | 0x68C32C04284FB022 | 9 | 10 | - | - | - | - |
| `CR15NetActorCRWin10` | 0xCFB89F03903773DA | 32 | yes | 0x24C73CB2EBE38BFC | 17 | 17 | 17 | 17 | - | 17 |
| `CR15NetAimAssistCRWin10` | 0xCC9E89D811BBC952 | 48 | yes | 0xBD8575CC9885E590 | 15 | 15 | 15 | 15 | - | 15 |
| `CR15NetAutoTargetCRWin10` | 0x48CF074C47891A54 | 104 | yes | 0x975113E786B2C0A4 | 1 | 1 | 1 | 1 | - | 1 |
| `CR15NetBulletCRWin10` | 0x48037CDE0E07C1B2 | 136 | yes | 0x4FD65C8D2B63D470 | 9 | 9 | 9 | 9 | - | 9 |
| `CR15NetDamageableCRWin10` | 0xC9DC08CFC2F5920F | 56 | yes | 0xBD1868F576836696 | 8 | 17 | 21 | 8 | - | 32 |
| `CR15NetDebugDrawCRWin10` | 0x46AC22D23C690593 | 32 | yes | 0x079586E19869A090 | 129 | 129 | 129 | 129 | - | - |
| `CR15NetDynamicCoverCRWin10` | 0xFD0FD89845DAED36 | 32 | yes | 0x42D101B2307BBC16 | 1 | 1 | 1 | 1 | - | 1 |
| `CR15NetDynamicPosterCRWin10` | 0x5915C96109D229E0 | 72 | yes | 0x267366A86FEEC098 | - | - | - | - | - | 9 |
| `CR15NetExplosionCRWin10` | 0x17EB03EFFA1EDAAE | 96 | no | 0x4AF215B19B297C34 | 9 | 9 | 9 | 9 | - | 9 |
| `CR15NetFollowPlayerCRWin10` | 0x718D4EF01CC3E962 | 40 | yes | 0xA7BCBF4B204A8916 | 1 | 1 | 1 | 1 | - | 1 |
| `CR15NetGhostLODCRWin10` | 0x794F256175FF883C | 88 | yes | 0x012B9CC38A29755A | 556 | 556 | 556 | 556 | - | - |
| `CR15NetGunCRWin10` | 0x7A804A0A9B2F5386 | 160 | yes | 0xDBAED509C67B8884 | 120 | 120 | 120 | 120 | - | 120 |
| `CR15NetIdCRWin10` | 0x24BCC1192CEB8D62 | 32 | yes | 0xC29715D62E039402 | 229 | 352 | 173 | 153 | - | 155 |
| `CR15NetKillTickerCRWin10` | 0x9E205E044C41A8F2 | 32 | yes | 0xF289249001A4BED6 | 1 | 1 | 1 | 1 | - | - |
| `CR15NetMagazineCRWin10` | 0xD5B9A165827D6823 | 40 | yes | 0x5B723F040A3778EE | 7 | 7 | 7 | 7 | - | 7 |
| `CR15NetPayloadCRWin10` | 0x9C80EAD4300AA0D6 | 72 | yes | 0x3850C0283892236E | 1 | 1 | - | - | - | - |
| `CR15NetPhysicsCRWin10` | 0x8FEE6557F995E4A3 | 40 | yes | 0x71131E0E3B8AA17C | 12 | 12 | 11 | 11 | - | 12 |
| `CR15NetPooledActorCRWin10` | 0x105121C3E1A26CE8 | 40 | yes | 0xC28A49E8E5639D32 | 1 | 1 | 1 | 1 | - | 1 |
| `CR15NetPunchableCRWin10` | 0x8DBBE870DFF5DEB5 | 48 | yes | 0xC0C407DB04436F3C | - | - | - | - | - | 13 |
| `CR15NetSensorTargetCRWin10` | 0xA7F862E8FBAA0D46 | 32 | yes | 0xE51718C1E4669474 | 7 | 7 | 7 | 7 | - | 20 |
| `CR15NetTouchInteractCRWin10` | 0xD669C4681BA186D0 | 40 | yes | 0xEEC5CC16EA02F8FC | 11 | 12 | 16 | 14 | - | 13 |
| `CR15NetTrackMoverCRWin10` | 0x81CB05E013A56E1A | 48 | yes | 0xE826937E7F7D0994 | 1 | 1 | - | - | - | - |
| `CR15NetVarCRWin10` | 0x2019BD260E641B4B | 32 | yes | 0xAC05E44816844A00 | 1 | 1 | 1 | 1 | - | - |
| `CR15OrientationConstraintCRWin10` | 0x9A6F1E833B500BCA | 112 | yes | 0x4E285CA5CA255902 | 9 | 10 | - | - | - | - |
| `CR15ParentedConstraintCRWin10` | 0x70340DF290571652 | 32 | yes | 0xF2676C58C07BAB0C | 11 | 12 | 2 | 2 | - | 2 |
| `CR15RigidAttachConstraintCRWin10` | 0x1D9528636D78586C | 40 | yes | 0xDDE8FFFC5B4D7AD4 | 2 | 2 | 2 | 2 | - | 2 |
| `CR15SpawnPointCRWin10` | 0x03534276F16B8C3C | 48 | yes | 0xCB75EEE100D282A8 | 5 | 5 | 5 | 5 | - | - |
| `CR15StickySurfaceCRWin10` | 0x7C949DFD5DC13065 | 88 | yes | 0xEC4547A5BAF4B6B2 | - | - | - | - | - | 5 |
| `CR15TouchInteractCRWin10` | 0x2E40131422F7B1FC | 64 | yes | 0xC2BF1B5284855E4A | 31 | 34 | 89 | 21 | - | 20 |
| `CR15TrackPointCRWin10` | 0x0620ED979771A3AA | 56 | yes | 0x451830F92DDC5CC6 | 69 | 181 | - | - | - | - |
| `CR15TriggerCRWin10` | 0x85D45832E4BDB196 | 72 | yes | 0x39284E7992BEFC92 | 1 | 1 | 1 | 1 | - | 1 |
| `CSharedCanvasUICRWin10` | 0xA772411051A1BF53 | 72 | yes | 0xDAB7DCE1DF894EF6 | 5 | 5 | 24 | 34 | - | - |
| `CSoundCRWin10` | 0x38EE950426EA8A62 | 80 | yes | 0x04E7C2E6A7EBD80E | 311 | 323 | 342 | 300 | - | - |
| `CStaticLODRegionTargetCRWin10` | 0x187089486A57023E | 32 | yes | 0x02D2BE13EA8FB8AE | 7 | 7 | 7 | 7 | - | - |
| `CStaticRaycastCRWin10` | 0x53C70747EF67B6D2 | 32 | yes | 0xD649A90FFF322C12 | 1 | 1 | 1 | 1 | - | 1 |
| `CTextureOverrideCRWin10` | 0x1A26A23198B0E761 | 32 | no | 0x4127FF2FFE6BE26A | 358 | 358 | 361 | 361 | - | 316 |
| `CTextureStreamingCRWin10` | 0x0101A4756C2124AE | 40 | no | 0x96832B652460ECDE | 726 | 744 | 792 | 764 | - | 640 |
