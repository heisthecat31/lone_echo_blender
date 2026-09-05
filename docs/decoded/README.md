# Every undecoded type in the archive

`docs/combat/` covers the six combat levels. This folder covers **everything**:
all 203 types the level index marks `no_decoder`, across all 32 levels.

## Result

| | |
|---|---|
| Types marked `no_decoder` | 203 |
| Found in the extracted data and measured | **188** |
| Not present in this extract | 15 (global asset resources) |
| Component records (flat or pooled) | **153** |
| Mis-classified as pooled (false positives) | 27 — see POOLED.md |
| Genuine standalone resources | 5 |
| Entries examined | **59,408** |
| Stride agrees in every level | **132 of 132** record types with a stride |
| Confirmed (node constant + every actor resolves) | **108** |
| Byte-identical templates | **17** |
| Pooled layouts solved | 16 of 28 real pooled types |

The headline: **almost nothing here is a bespoke format.** 153 of 188 types are
the same component-record container with a different stride, and the container
itself is fully decoded (see `../combat/CR_FORMAT.md`). A further 27 look like
records by size arithmetic alone but fail the entry test — POOLED.md says how
that false positive was caught and why it matters.

| Document | Covers |
|---|---|
| **ALL_TYPES.md** | every measured type — hash, shape, stride, confirmation, entry counts |
| **POOLED.md** | inline-table layouts of pooled records, and how the ambiguity was removed |
| **REFLECTION_PROBES.md** | the biggest thing in a level -- 379 baked HDR cubemaps, 189.6 MB: the metadata grammar, the cube byte order, and which probe a mesh uses |
| **TERMINAL_UI.md** | where the EQUIPMENT STATION terminals live, and why they were never visible |
| **COMBAT.md** | all 17 combat-related levels, the parent family tree, and per-level gameplay records |
| **FISSION.md** | complete dossier on the reference payload map, and what links to it |
| **FIELDS.md** | a field map for every offset of all 181 record types — value domains, actor cross-references |
| **RESOURCES.md** | the standalone resources: `CGameLevelResource`'s parent field, `CPhysicsResource`'s zone block, `CGFSEffectsResource`'s float table |
| **OPEN.md** | what still needs decoding, measured and prioritised |
| **COMPOUND.md** | the 27 that only look like records — the leading array is real, the tail is not decoded, five models ruled out |
| **IDENTICAL.md** | the 17 byte-for-byte template records, sha1-verified |
| `_all.json`, `_pools.json`, `_probes.json` | raw measurements |

For the container format, the component-space laws, scripts and the combat
gameplay records, see [../combat/](../combat/README.md).

## Method

Same tests as the combat pass, applied archive-wide:

1. **Envelope split** — `data_size == len - 56` is a flat record; a smaller
   `data_size` that divides evenly by `count` is a **pooled** record with the
   remainder as its pool. (An earlier pass called pooled records "resources";
   fixing that moved 47 types out of the unknown column.)
2. **Stride agreement** — `data_size / count`, required equal in every level
   that ships the type. 132 of 132.
3. **Entry head** — `+0` constant within a file, `+8` resolving in that level's
   `CActorDataResource`. 108 of 180 pass both.
4. **Invariance and identity** — which offsets never vary, and whether the whole
   file is byte-identical across levels.
5. **Pool solving** — headers found by shape (`+0` and `+16` zero), then the
   size sum checked against the pool exactly.

## Honesty

* **Measured** — every number above, and every stride, node hash and entry count
  in ALL_TYPES.md.
* **Not decoded** — per-field *meaning* for most types. Knowing that
  `CR15FlagCRWin10` is a 48-byte record on a real actor is not the same as
  knowing what its fields mean, and this folder does not pretend otherwise.
  Field-level meaning is proven only where a test could establish it: see
  `../combat/PROVEN.md`.
* **Unsolved** — 39 pooled layouts, the 15 absent resource types, and the 5
  genuine resources' internals.

## Reproducing

    python docs/decoded/tools/decode_all.py     # -> _all.json
    python docs/decoded/tools/prove_pools.py    # -> _pools.json
    python docs/decoded/tools/gen_docs.py       # -> the three tables
