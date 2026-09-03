# Echo Combat formats

> Archive-wide coverage of **all 203** undecoded types across all 32 levels
> lives in [../decoded/](../decoded/README.md). This folder is the combat-specific
> view: the container format, the activation laws, scripts, and the gameplay
> records.

What the level index (`docs/evr_level_index.json`) marks `no_decoder` for the
six combat levels, decoded as far as the shipped bytes allow.

Scope: `mpl_combat_fission`, `mpl_combat_gauss`, `mpl_combat_dyson`,
`mpl_combat_combustion`, `mpl_combat_war_room`, `mpl_lobby_b_combat`.
Of 117 types with decoders, **90 more had none**; those 90 are covered here.

| Document | Covers |
|---|---|
| **CR_FORMAT.md** | the 56-byte component-record envelope; flat vs ordered-pool records; how stride is derived and checked |
| **CR_TYPES.md** | all 61 flat CR types — CS node hash, stride, per-level entry counts, whether confirmed |
| **RESOURCES.md** | the 27 types that are not component records, with per-level sizes |
| **COMPONENT_SPACE.md** | `CComponentSpaceResource` and the two component-system activation laws |
| **SCRIPTS.md** | script resources, the `CScriptCR` binding, reading script failures, and why a script cannot be cloned |
| **GAMEPLAY.md** | payload, track points, track mover, spawn points, trigger, net id, sub-levels, the war room |
| **PROVEN.md** | the six tests and exactly what each one establishes — including the 8 byte-identical types and the route-order proof |
| `_survey.json`, `_constants.json`, `_fields.json` | the raw measurements everything above is generated from |

The 90 split 61 flat CRs + 27 resources + 2 fully-decoded small types
(`CGameLevelInfoResource`, `COccluderMeshResource` — both in GAMEPLAY.md).

## How these were decoded

No guessing was accepted where a check was available:

1. **Split CR from resource** by the header: `data_size == len - 56` means a
   component record. That splits the 90 into 61 + 27 (+2 oddities).
2. **Derive stride** as `data_size / count`, then require the value to agree
   across *every* level that ships the type. All 61 agree — a wrong stride would
   diverge between a 12-entry map and a 700-entry one.
3. **Confirm the entry head** by requiring `+0` constant within a file (the
   component-system node hash) and `+8` to resolve in that level's
   `CActorDataResource`. 55 of 61 pass both; the 6 that do not are marked, and
   fail only because their rows address actors outside the level.
4. **Prove pooled layouts** by summing every inline table size across every
   entry and requiring the total to equal the pool length exactly. It does, in
   all four combat maps, for all three pooled types.

## Confidence

Stated plainly per claim rather than globally:

* **Measured** — strides, counts, node hashes, pool arithmetic, the payload
  tuning floats, spawn ids, the war room shipping no resources.
* **Inferred** — the meaning of the track-point flag bits, and the role of the
  symbol hash at track-point `+32`. Marked as such in GAMEPLAY.md.
* **Not decoded** — the per-script header signature (SCRIPTS.md), and the
  internal formats of most entries in RESOURCES.md.

## Reproducing

    python docs/combat/tools/decode_combat.py     # rewrites _survey.json
    python docs/combat/tools/gen_combat_docs.py    # regenerates CR_TYPES + RESOURCES
    python docs/combat/tools/prove_constants.py    # invariance + byte-identity  -> _constants.json
    python docs/combat/tools/prove_fields.py       # correlation vs actor truth  -> _fields.json

Sources read: the clean extract
(`EchoVR-Map-Editor/echovr_clean_extract/48037dc70b0ecab2`, friendly type-dir
names) for the four combat maps, and `H:/pcvr-extracted` (hash-named) for the
war room and lobby. Analysis only — nothing here writes to a build.
