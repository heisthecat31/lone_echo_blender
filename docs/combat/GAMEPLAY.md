# Combat gameplay records — measured layouts

All offsets are within an entry (after the 56-byte CR header). Values quoted
from `mpl_combat_fission` unless noted.

## CR15NetPayloadCR — the payload objective

One entry, stride 72. CS node `0x9C80EAD4300AA0D6`.

    +0   u64   node hash
    +8   u64   the payload actor
    +32  10 x f32   tuning

Fission and gauss carry **byte-identical tuning**:

    1, 1, 6, 1, 1.2, 1.3, 1.35, 1, 0.1, 0.2

`1.2 / 1.3 / 1.35` are exactly `payload_speed_multi_attacker_2/3/4` from the
balance JSON, which is what identifies this block as tuning rather than a route.
**The route is not in this CR.**

## CR15TrackPointCR — the route

The route is a set of actors, one per point, not a path in a buffer. Stride 56,
CS node `0x0620ED979771A3AA`; fission ships 69, gauss 69.

    +0   u64   node hash
    +8   u64   the track-point actor (its position comes from CActorData)
    +16  u32   0xFFFFFFFF
    +24  u32   flags
    +32  u64   a symbol hash, or 0xFFFFFFFFFFFFFFFF when unset

**Entry order is route order — proven.** Comparing the gap between consecutive
entries' actor positions against the gap between arbitrary pairs:

| Level | Points | Consecutive (median) | Arbitrary pair (median) | Route length |
|---|---|---|---|---|
| fission | 69 | 1.34 m | 57.49 m | 181.8 m |
| gauss | 181 | 0.66 m | 53.75 m | 206.8 m |

Consecutive entries are 43x and 81x closer than arbitrary pairs, so the table is
a path in order, not an authoring list. (Gauss ships 181 points, not 69.)

Flags at `+24` are set on only four of the 69 entries:

| Index | Flag |
|---|---|
| 31 | `0x60` |
| 32 | `0x20` |
| 37 | `0x60` |
| 68 | `0x10` |

Index 68 is the last point and carries a flag of its own, consistent with an
end marker; the `0x20`/`0x60` entries sit mid-route, consistent with
checkpoints. **Inferred, not proven** — the bit meanings are not confirmed.

`+32` is set on 24 entries and unset (all-ones) on 45. Those values are **not**
actors and **not** other track points, so they are name/symbol hashes rather
than links; 20 entries share a single value, and 4 are unique.

## CR15NetTrackMoverCR — what rides the route

One entry, stride 48. This is the component that moves the payload along the
points; the payload snaps to point 0 on load.

## CR15SpawnPointCR — where players enter

Stride 48, CS node `0x03534276F16B8C3C`.

    +0   u64   node hash
    +8   u64   the spawn actor (position comes from CActorData)
    +40  u32   spawn id

Fission's five ids are `200..204` — **band 2 only**. This is the combat
convention: a combat map does not own team spawns, it owns the deploy
destinations. Ids `0..` and `100..` are arena team bands and belong to the war
room.

Positions live on the actor, not in the entry, so a spawn's location is read
from `CActorDataResource` via `+8`.

## CR15TriggerCR — the combat event volume

One entry, stride 72. Across all four combat maps the fission file is
**byte-identical**, on the same fixed actor `0x26B939FCAC9FBFF8`. Fission also
binds a script to that actor (`0x3A678375DF604B4F`), which on load asks for two
co-located trigger volumes (`0x8A133114A604FF08`, `0x0671C5E4E1797488`) sitting
+/-0.06 m either side of it.

## CTextureStreamingCR — which asset's textures to stream

Stride 40. `+32` is a **model-asset hash**, not a texture id: resolved against
every type directory in the archive, 604 of fission's 726 entries name a
resource that exists, under the asset's own hash (the other 122 are assets
absent from this extract).

Confirmed independently in-game: a payload whose model pointed at a re-keyed
asset while this field still named the stock one loaded fine and **drew
nothing** — the streamer was fetching the wrong asset's textures. Re-keying this
field fixed it.

## CR15NetIdCR — network identity

Stride 32. Present on actors that the netgame must register. Its absence is not
a level-load error: the level loads clean and the **netgame** rejects it
afterwards.

    [LEVELLOAD] Finished loading level '0x...'
    [NETGAME] Failed to load level
    [NETGAME] NetGame switching state (from loading level, to load failed)

That distinction — level loader OK, netgame refuses — is the signature of an
actor-registration problem rather than a resource problem.

## Sub-levels: CGSceneResource sec12

`sec12` opens with a hull-1 table of 16-byte `(sub_level_hash, position_index)`
rows, then later a positions array of 3 floats each.

    +0   u32   hull-1 row count
    +4        rows, 16 bytes each, ascending by key

Fission declares 6 sub-levels, gauss 8. **These rows do not cause a level to
stream.** They supply the world OFFSET the engine applies to a sub-level that is
loaded by other means — the log line `Level 'H' offset by (x, y, z)` reads from
here. Which level streams is compiled into the StreamingScript (see SCRIPTS.md).

## The war room and its parent chain

A combat map's parent is set in two places — `CGameLevelInfoResource` at `+0`
and `CGameLevelResource` at `+16` — and the shipped rule is absolute:

    mpl_combat_combustion / dyson / fission / gauss  ->  mpl_combat_war_room
    mpl_arena_a, the lobbies, the tutorials          ->  r14_glb_global_mp

⚠ **Correction.** An earlier version of this file said `mpl_combat_war_room`
"ships zero resources and is an empty parent gamespace", and identified the room
you stand in as `0x4D82118C7C91B6BB` / `0xAC360E41E4EDE056` with
`0x3F9915D3001DC28E` as a terminal panel prefab. All of that was wrong:

| Hash | What it actually is |
|---|---|
| `0x08A1AF9E108DEF0B` | `mpl_combat_war_room` — **675 records** in the level index |
| `0x3F9915D3001DC28E` | `r14_glb_global_mp` — the global multiplayer gamespace |
| `0x4D82118C7C91B6BB` | `mnu_master_mp_ingame` — a menu level |
| `0xAC360E41E4EDE056` | `mnu_master` — a menu level |

The war room is a real level with real content; this extract simply does not
contain its resources, which is what the "zero resources" reading came from.

### CGameLevelInfoResource — fully decoded

Exactly 16 bytes, no header:

    +0   u64   parent level hash
    +8   u64   0xFFFFFFFFFFFFFFFF

All four combat maps read `0x08A1AF9E108DEF0B` (`mpl_combat_war_room`) at `+0`
and all-ones at `+8`. This is the smallest and most consequential resource in a
combat map: the parent gamespace is what loads the gun, ordnance and tac-mod.

### COccluderMeshResource — an empty CR

56 bytes in every combat level: the CR envelope with `data_size = 0`,
`count = 0`, sentinel `1`. Present so its component system has a file to find
(see COMPONENT_SPACE.md), carrying no entries.

## The levels a match streams

Stock combat streams six further levels once a match starts. Named, they are:

| Hash | Level |
|---|---|
| `0x836C5B14CCC58201` | `mpl_combat_fission_cargobay` |
| `0x906C4707CBC28C16` | `mpl_combat_fission_pantheon` |
| `0x907F461FCCC0961D` | `mpl_combat_fission_prologue` |
| `0xF919F210BBDA872C` | `mpl_combat_fission_climax` |
| `0x61B0162ADBD446FF` | `mpl_combat_celebration_room_orange` |
| `0xE1A3AE700140D9D5` | `mpl_combat_celebration_room_blue` |

⚠ **These are not "war-room UI levels" and they carry no terminal UI or deploy
hologram** — an earlier claim here, now withdrawn. Four are regions of fission
itself (each names fission as its parent in both parent fields), and two are the
end-of-match celebration rooms. Streaming them into another map can only drag
fission in with them. See [../decoded/FISSION.md](../decoded/FISSION.md).
