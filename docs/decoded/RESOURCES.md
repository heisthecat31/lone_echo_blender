# Standalone resources — what each one is

These do not use the component-record envelope. Each has its own container.
Measured across all 31 levels that ship them.

## CGameLevelResource — DECODED (the part that matters)

**`+16` is the PARENT LEVEL hash.** Confirmed across every combat map and an
arena map:

| Level | Bytes | `+16` |
|---|---|---|
| `mpl_combat_fission` | 82,528 | `mpl_combat_war_room` |
| `mpl_combat_gauss` | 81,680 | `mpl_combat_war_room` |
| `mpl_combat_dyson` | 85,368 | `mpl_combat_war_room` |
| `mpl_combat_combustion` | 84,432 | `mpl_combat_war_room` |
| `mpl_arena_a` | 18,944 | `r14_glb_global_mp` |

That is the field a combat map must carry, and it is why an arena-parented
combat map loads no gun, ordnance or tac mod. `+0` and `+8` are further hashes;
`+24` is all-ones. The remaining ~80 KB is not decoded.

## CPhysicsResource — leading zone block DECODED

Sizes run 72 B to 13.9 MB. Every file opens with the same block:

    +0   u64      0x0000000100000000   (the (0, 1) marker used across the format)
    +8   f32 x6   zone data

For `mpl_combat_fission` those six floats are `300, 300, 300, 3, 0.5, 0` — the
`SPhZoneData` the engine scan-converts collision against, where `3.0` is the
cell size named in the engine's own error text:

    Subbody in body of physics body is too large! ... with the given cell size
    of 1.500000 the mesh would scan convert into over a million cells
                                                        cphbody.cpp:3996

A 72-byte file is exactly this header and nothing else. Body geometry follows in
larger files and is not decoded.

## CGFSEffectsResource — fixed-size float table

**Exactly 1,416 bytes in every one of the 31 levels — 354 float32 values.**
17 distinct variants across those levels, so it is a per-level settings table
drawn from a fixed schema rather than authored geometry.

The first twelve floats of `mpl_combat_fission`:

    1, 1, 1, 0.75, 1, 1, 1, 2, 1, 1, 1, 1

Field meaning is not established, but the shape is: a flat array of 354 floats,
same length everywhere.

## CGVisibilityResource — not decoded

92 B to 265,300 B, opening with zeros. No structure established.

## CComponentSpaceResource — DECODED elsewhere

Listed as a "resource" by the envelope test because its header is 64 bytes
rather than 56, but it is fully decoded: 64-byte header, count at `+48`,
16-byte `(component hash, level hash)` rows. See
[../combat/COMPONENT_SPACE.md](../combat/COMPONENT_SPACE.md). Its `+0` is the
owning level hash, consistent with the record envelope.
