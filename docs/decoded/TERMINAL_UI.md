# The EQUIPMENT STATION terminal UI — located

`mpl_lobby_b_combat` (`0xCB9977F7FC2B4526`, 3,662 records, 2,339 actors,
65 component systems, 375 script entries). This is where the terminals live.

## What makes a terminal

The terminal is an actor tree whose leaves carry **two** components:

| Resource | Entries in the lobby | Role |
|---|---|---|
| `CCanvasUICRWin10` | **354** | the canvas — the screen itself |
| `CTextureOverrideCRWin10` | **316** | what the canvas draws |

The known terminal actors all appear in both:

| Actor | Role |
|---|---|
| `0x2D65EBA7F674D94A` | terminal root |
| `0xD62267852D1D5767` .. `0xD62267852D1D5763` | the five station markers |
| `0xC7C81AF14364688D` | canvas child |
| `0xF22E48B85B5C6ED0` | visual child |

Both component systems are declared in the lobby's `CComponentSpaceResource`
(`CanvasUI` `0x35EC9A0128EC916F`, `TextureOverride` `0xA621530B7AACA923`), so
they activate — the LAW-1 requirement is met.

## The 436 canvas errors are NOT the terminals

The chain the game log complains about —

    Cannot find canvas element 0x0EC5CE292F08ED27 on canvas 0x13361C4773E07B3E
    from component 0xE55AF932C88EC864 (actor 0x13983255EE1DBCBE)

— has **none** of those four hashes anywhere in `mpl_lobby_b_combat`. They belong
to `r14_glb_global_mp`, the global HUD. Stock `mpl_combat_fission` logs the
identical error with working terminals. Confirmed noise.

## Why the terminals are not visible — positions

Our equipment level `mpl_arena_combat` is a faithful clone of the lobby: the
canvas and texture-override CRs are byte-for-byte intact (354 / 316 entries, all
five station markers present in both), and its component space is **identical**
to the lobby's — 65 declarations, nothing dropped.

The problem is purely **where the terminals are**:

| | Terminal root position |
|---|---|
| stock `mpl_lobby_b_combat` | (40.95, 0.73, -23.64) |
| our clone `mpl_arena_combat` | (-128.60, 10.60, 87.50) — the combat spawn |
| **the player, standing in the war room** | **(-158.50, -7.95, 202.76)** |

Two separate consequences:

1. **Right now the stock lobby is what loads**, because TurboAHH is not in
   `bin/win10/plugins/` and so its `scriptpatch` never redirects the streaming
   script. Its terminals sit at (40.95, 0.73, -23.64), roughly 230 m from the
   player. They exist, they render, nobody can see them.
2. **Even with TurboAHH active**, our clone puts the equipment at the *combat*
   spawn, not in the war room. The terminals would only be reachable after
   deploying into the map — not while standing at the ring.

So "no terminals" has never been a missing-resource problem. The UI has been
shipped correctly the whole time; it has been placed in a room the player is not
standing in.

## What follows

* To see terminals **in the war room**: place the equipment near
  (-158.50, -7.95, 202.76) — `_equipment_level.py` takes the position as its
  `build(spawn)` argument, so this is one number.
* For our clone to load at all: `TurboAHH.dll` must be back in
  `bin/win10/plugins/` (it is currently in `bin/win10/`). That also restores the
  guns, which come from the same plugin.
