# Byte-identical records — fixed templates

17 types are **byte-for-byte the same file** in every level that ships them,
sha1-verified. They are not authored per map; they are templates on fixed
actors.

The practical consequence: these can be carried verbatim into a new map. No
re-keying, no per-map authoring — the same bytes the shipped maps use.

Most of the combat weapon kit is in this list, which is why an "empty kit plus
CS declarations" approach works at all: the kit genuinely is constant.

| Type | Stride | sha1 (first 10) | Levels | Ships in |
|---|---|---|---|---|
| `CR15CollisionCRWin10` | 56 | 36ea207cd7 | 5 | mpl_combat_combustion, mpl_combat_dyson, mpl_combat_fission, mpl_combat_gauss, mpl_lobby_b_combat |
| `CR15LevelTouchInteractCRWin10` | 32 | 6a9ff0c079 | 2 | r14_glb_global_mp, r14_glb_global_tutorial |
| `CR15NetAimAssistCRWin10` | 48 | 3ec4fe8c9a | 5 | mpl_combat_combustion, mpl_combat_dyson, mpl_combat_fission, mpl_combat_gauss, mpl_lobby_b_combat |
| `CR15NetAutoTargetCRWin10` | 104 | 2cda5cd43e | 5 | mpl_combat_combustion, mpl_combat_dyson, mpl_combat_fission, mpl_combat_gauss, mpl_lobby_b_combat |
| `CR15NetBitFieldCRWin10` | 24 | cedbde4a81 | 5 | mpl_combat_combustion, mpl_combat_dyson, mpl_combat_fission, mpl_combat_gauss, mpl_lobby_b_combat |
| `CR15NetBulletCRWin10` | 136 | 50b8444cfa | 5 | mpl_combat_combustion, mpl_combat_dyson, mpl_combat_fission, mpl_combat_gauss, mpl_lobby_b_combat |
| `CR15NetDebugDrawCRWin10` | 32 | ccc57de54d | 4 | mpl_combat_combustion, mpl_combat_dyson, mpl_combat_fission, mpl_combat_gauss |
| `CR15NetDynamicCoverCRWin10` | 32 | c2da113bdb | 5 | mpl_combat_combustion, mpl_combat_dyson, mpl_combat_fission, mpl_combat_gauss, mpl_lobby_b_combat |
| `CR15NetFollowPlayerCRWin10` | 40 | 4e4e9c0b78 | 5 | mpl_combat_combustion, mpl_combat_dyson, mpl_combat_fission, mpl_combat_gauss, mpl_lobby_b_combat |
| `CR15NetGunCRWin10` | 160 | 9d6d5cbc6d | 5 | mpl_combat_combustion, mpl_combat_dyson, mpl_combat_fission, mpl_combat_gauss, mpl_lobby_b_combat |
| `CR15NetHoloBitCRWin10` | 32 | 74876692b5 | 2 | mpl_arena_a, mpl_tutorial_arena |
| `CR15NetKillTickerCRWin10` | 32 | d339719769 | 4 | mpl_combat_combustion, mpl_combat_dyson, mpl_combat_fission, mpl_combat_gauss |
| `CR15NetMagazineCRWin10` | 40 | 3644b085f4 | 5 | mpl_combat_combustion, mpl_combat_dyson, mpl_combat_fission, mpl_combat_gauss, mpl_lobby_b_combat |
| `CR15NetMetricsCRWin10` | 32 | f99ebe40ab | 2 | r14_glb_global_mp, r14_glb_global_tutorial |
| `CR15NetUISettingsCRWin10` | 24 | c4fdf67539 | 2 | mpl_arena_a, mpl_tutorial_arena |
| `CR15StaticArtTouchInteractCRWin10` | 32 | 5d62214a26 | 2 | r14_glb_global_mp, r14_glb_global_tutorial |
| `CR15TriggerCRWin10` | 72 | 6be1deab22 | 5 | mpl_combat_combustion, mpl_combat_dyson, mpl_combat_fission, mpl_combat_gauss, mpl_lobby_b_combat |
