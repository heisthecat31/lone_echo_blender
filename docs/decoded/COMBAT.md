# Everything linked to combat

All 17 combat-related levels, their relationships, and what each ships.
Companion to [FISSION.md](FISSION.md), which covers the reference map in depth.

## The family tree

Parents come from `CGameLevelInfoResource` `+0`, confirmed against
`CGameLevelResource` `+16`:

    r14_glb_global_root
      └── r14_glb_global_mp                     the global multiplayer gamespace
            ├── mpl_lobby_b_combat              the combat lobby
            ├── mpl_combat_celebration_room_orange
            ├── mpl_combat_celebration_room_blue
            └── (arena maps)

    mpl_combat_war_room                         the combat gamespace
      ├── mpl_combat_fission
      │     ├── mpl_combat_fission_prologue
      │     ├── mpl_combat_fission_cargobay
      │     ├── mpl_combat_fission_pantheon
      │     └── mpl_combat_fission_climax
      ├── mpl_combat_gauss
      │     ├── mpl_combat_gauss_section01a
      │     ├── mpl_combat_gauss_section01b
      │     ├── mpl_combat_gauss_section02
      │     └── mpl_combat_gauss_section03
      ├── mpl_combat_dyson                      (no child regions)
      └── mpl_combat_combustion                 (no child regions)

Two structural facts fall out of this:

* **The two payload maps are built in pieces; the two capture maps are not.**
  Fission has four regions and gauss four sections, each naming its map as
  parent. Dyson and combustion have none — they ship as single levels.
* **A region is a thin level.** Fission's prologue has 165 actors, 16 component
  systems and 7 script entries against the parent map's 1,539 / 76 / 418. They
  carry geometry, not gameplay.

## The extract gap

`mpl_combat_war_room` reports **675 records and 39.7 MB in the level index**,
but **none of its resources are in this extract** — no actor table, no component
space, no scripts. Every measurement for it below is blank for that reason, not
because the level is empty. An earlier note in these docs claimed it "ships zero
resources"; that was an artifact of this gap and is withdrawn.

`mpl_lobby_b_combat` (3,662 records) IS present and is substantial: 2,339
actors, 65 component systems, 375 script entries. Between the two, they are the
open candidates for where the EQUIPMENT STATION terminal UI actually lives.

## Every combat level

| Level | Hash | Records | Actors | CS | Script entries | Distinct scripts | Parent |
|---|---|---|---|---|---|---|---|
| `mpl_combat_celebration_room_blue` | 0xE1A3AE700140D9D5 | 179 | 58 | 12 | 1 | 1 | r14_glb_global_mp |
| `mpl_combat_celebration_room_orange` | 0x61B0162ADBD446FF | 173 | 100 | 12 | 1 | 1 | r14_glb_global_mp |
| `mpl_combat_combustion` | 0x42670F2BED45703C | 4039 | 2306 | 70 | 428 | 81 | mpl_combat_war_room |
| `mpl_combat_dyson` | 0x43E2DA7914642604 | 3423 | 2692 | 70 | 451 | 82 | mpl_combat_war_room |
| `mpl_combat_fission` | 0xDF5CA7B7DFA383D4 | 2055 | 1539 | 76 | 418 | 81 | mpl_combat_war_room |
| `mpl_combat_fission_cargobay` | 0x836C5B14CCC58201 | 1129 | 949 | 17 | 9 | 5 | mpl_combat_fission |
| `mpl_combat_fission_climax` | 0xF919F210BBDA872C | 1017 | 611 | 26 | 17 | 6 | mpl_combat_fission |
| `mpl_combat_fission_pantheon` | 0x906C4707CBC28C16 | 761 | 291 | 17 | 10 | 5 | mpl_combat_fission |
| `mpl_combat_fission_prologue` | 0x907F461FCCC0961D | 528 | 165 | 16 | 7 | 4 | mpl_combat_fission |
| `mpl_combat_gauss` | 0x43E2DA7A0C623A19 | 2455 | 1717 | 79 | 471 | 85 | mpl_combat_war_room |
| `mpl_combat_gauss_section01a` | 0xBE0FF249E3C43783 | 764 | 431 | 15 | 1 | 1 | mpl_combat_gauss |
| `mpl_combat_gauss_section01b` | 0xBE0FF249E3C43780 | 1296 | 709 | 14 | 1 | 1 | mpl_combat_gauss |
| `mpl_combat_gauss_section02` | 0x555BE9BCB4759006 | 949 | 428 | 13 | 1 | 1 | mpl_combat_gauss |
| `mpl_combat_gauss_section03` | 0x555BE9BCB4759007 | 1096 | 639 | 14 | 2 | 2 | mpl_combat_gauss |
| `mpl_combat_war_room` | 0x08A1AF9E108DEF0B | 675 | - | - | - | - | - |
| `mpl_lobby_b_combat` | 0xCB9977F7FC2B4526 | 3662 | 2339 | 65 | 375 | 55 | r14_glb_global_mp |
| `r14_glb_global_mp` | 0x3F9915D3001DC28E | 1875 | 69 | 99 | 166 | 91 | r14_glb_global_root |

## Gameplay records per level

Counts are entries in that level's CR. This is the cleanest statement of what
makes a map a payload map or a capture map:

| Level | Payload | Capture volume | Track points | Spawns |
|---|---|---|---|---|
| `mpl_combat_combustion` | - | 1 | - | 5 |
| `mpl_combat_dyson` | - | 1 | - | 5 |
| `mpl_combat_fission` | 1 | - | 69 | 5 |
| `mpl_combat_gauss` | 1 | - | 181 | 5 |

Fission and gauss carry `CR15NetPayloadCR` and a track; dyson and combustion
carry `CR15NetCaptureVolumeCR` and none. Every combat map ships exactly five
spawns, ids 200-204 — band 2 only.

## Declared sub-levels (CGSceneResource sec12)

These are the world-offset declarations, **not** what the game streams at match
time:

| Level | Declares |
|---|---|
| `mpl_combat_combustion` | `0xA01FD494E8EECF7D`, `0xBB4700FC7F02D52D`, `0xCD4D1B83E1F513B1`, `0xF946144F555E0692`, `0x007485B2CF86708E`, `0x332241821D394EDC`, `0x44FF640480396839`, `0x6F083FB6D41F45B4` |
| `mpl_combat_dyson` | `0x832F006C141FE3E4`, `0x8A2A8C7D6A3FD6A8`, `0x8EDB3EAE1BE4589E`, `0xA01E7C964B634C73`, `0xB245FC7084A19FF5`, `0xCCE519206C761DB1`, `0xE5F259C00CF73F6F`, `0xE78865A028564B0D`, `0xEA20D8D726A369DF`, `0xED4D318237FBC13B`, `0xF1E81BE420E320C9`, `0xFB2D4FC63F4EAAFF`, `0xFC40A6932E16021B`, `0x1005F8207E6E2D98`, `0x1C64A784EB7E958E`, `0x328A432190BA40DC`, `0x4A2176246784D4A1`, `0x4DFD98887B9A22E1` |
| `mpl_combat_fission` | `0xB490BB722958C1A8`, `0xF1F1734847D74B28`, `0xFF2EBE34FE81CDDA`, `0x0F9E2949BB1B1645`, `0x32D7D56E27A93157`, `0x7244FD32CBE25A07` |
| `mpl_combat_gauss` | `0x8CEB004A9FA99F0B`, `0x91B7169D4ADFBAAD`, `0xC160F9592132153D`, `0xD2A9C765E744FDFC`, `0xDA3BF4A5AE622A76`, `0x5B7FFE49EE798364`, `0x6E0FC12DEF505656`, `0x703117CA40F3E697` |
| `mpl_lobby_b_combat` | `0x84669F8E122011CE`, `0x8D299E64CEB8DE53`, `0xC01E6A81BBFFFDEB`, `0xF8B51CA0D8615DCE` |

## Cross-references between combat levels

Which level's resources contain another combat level's hash anywhere:

| Level | References |
|---|---|
| `mpl_combat_celebration_room_blue` | `r14_glb_global_mp` |
| `mpl_combat_celebration_room_orange` | `r14_glb_global_mp` |
| `mpl_combat_combustion` | `mpl_combat_war_room` |
| `mpl_combat_dyson` | `mpl_combat_war_room` |
| `mpl_combat_fission` | `mpl_combat_war_room` |
| `mpl_combat_fission_cargobay` | `mpl_combat_fission` |
| `mpl_combat_fission_climax` | `mpl_combat_fission` |
| `mpl_combat_fission_pantheon` | `mpl_combat_fission` |
| `mpl_combat_fission_prologue` | `mpl_combat_fission` |
| `mpl_combat_gauss` | `mpl_combat_war_room` |
| `mpl_combat_gauss_section01a` | `mpl_combat_gauss` |
| `mpl_combat_gauss_section01b` | `mpl_combat_gauss` |
| `mpl_combat_gauss_section02` | `mpl_combat_gauss` |
| `mpl_combat_gauss_section03` | `mpl_combat_gauss` |
| `mpl_lobby_b_combat` | `r14_glb_global_mp` |
