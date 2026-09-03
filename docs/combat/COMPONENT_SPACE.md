# CComponentSpaceResource — which component systems a level runs

Not a CR: its header is 64 bytes and it holds no `data_size` in the CR sense.

    layout:  64-byte header | count x 16-byte rows
    count at +48
    row:     +0  u64  component-system node hash
             +8  u64  resource name hash (always this level's own hash)

Verified: `64 + 16 * count == len(file)` for every combat level, and every row's
second field is the level's own hash.

| Level | Bytes | Declared systems |
|---|---|---|
| mpl_combat_fission | 1280 | 76 |
| mpl_combat_gauss | 1328 | 79 |
| mpl_combat_dyson | 1184 | 70 |
| mpl_combat_combustion | 1184 | 70 |
| mpl_lobby_b_combat | 1104 | 65 |

(`mpl_combat_war_room` ships **no** resources at all — see GAMEPLAY.md.)

## The two activation laws

**LAW 1 — a component system runs if and only if its node hash is listed here.**
A CR file that exists but whose CS is undeclared never activates. Conversely a
hash that is not a component system aborts the load outright:

    Couldn't find resource load callback for 0x... component data
                                              cncagamespace.cpp:2783

That is what happens if you list a *script* hash here: a script is bound through
a `CScriptCR` entry, and only `ncaScript` belongs in the component space.

**LAW 2 — a declared system usually needs its level-named CR file.**

    Cannot find resource 0x...:0x...        cresourcemanager.cpp:1040

The qualifier matters: of fission's 76 declared systems, **28 have no
level-named CR at all**. Those are code-only systems, so LAW 2 is not absolute —
declaring a system with no data is normal, and the failure mode above only
applies to systems that do expect a file. What is *not* safe is declaring a
system and shipping an empty or malformed CR for it: the engine reads the file
it finds, and a 256-byte record naming an actor you never created fails the load
with `Missing actor with nodeid ...`.
