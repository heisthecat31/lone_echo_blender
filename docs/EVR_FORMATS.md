# Echo VR resource formats — what is known

Reference for the flat extract at `H:\pcvr-extracted` (live PCVR / Win10 build).
Everything here is measured against shipped data; each claim carries the
evidence that pins it. Where something is a guess it says so.

**Corpus, measured:**

| | |
|---|---:|
| resource type directories | 235 |
| files | 69,694 |
| total | 15.01 GB |
| levels (actor data AND scene resource) | 32 |
| archives | 298 (281 Layout A, 17 Layout B) |
| types with a decoder in this project | 25 of 235 |
| types whose name is unknown | **0** |

Every type name resolves via `quest_combat_port/data/hash_lookup.json`. There
are no anonymous types — only undecoded ones.

---

## 1. Extract layout and type identity

```
<root>/<type_hash>/<resource_hash>
```

Both hashes are `CSymbol64` — a CRC-64 seeded at all-ones — of a **name**.
`le_mesh.material_scalars.symbol64` computes it, and it is case-insensitive
(`cgtextureresourceWin10` and `CGTextureResourceWin10` hash identically).

Consequences that matter:

* A resource hash is the hash of its asset *name*, so **two games that name an
  asset the same thing collide**. `prim_sphere`, `missing`, `common_white`,
  `common_black`, `Linear` exist in both Echo VR and Lone Echo 2 with identical
  hashes and *different contents*.
* A type has one hash per platform:
  `CSymbol64(<bare> + "Win10" | "Win7" | "Android" | ... + ["GPU"])`.
  `evr_resource_types.verify_win7_hashes()` re-derives the whole Win7 table
  from the Win10 names and reports 0 disagreements over 23 types.

### 1.1 Leading zeros are stripped on disk

`CGInstancedModelResourceWin7GPU` is `039a43c1af5440f9`, and extracts write the
directory as `39a43c1af5440f9` — 15 characters. A plain `root / hash` test
reports the type missing. `resolve_type_dir` tries the canonical spelling and
the zero-stripped one.

### 1.2 GPU sidecars

Descriptor and payload are separate types sharing one resource hash:

| descriptor | payload |
|---|---|
| `CGMeshListResourceWin10` | `CGMeshListResourceWin10GPU` |
| `CGInstancedModelResourceWin10` | `CGInstancedModelResourceWin10GPU` |
| `cgtextureresourceWin10` | `CGTextureResourceWin10GPU` (a complete `DDS ` file) |
| `CGLightMapResourceWin10` | `CGLightMapResourceWin10GPU` |
| `CGStaticInstanceResourceWin10` | `CGStaticInstanceResourceWin10GPU` |

**The closure does not list the GPU sidecars separately.** Copying only what an
archive names ships descriptors with no vertices and no pixels.

---

## 2. `CArchiveResource` — the load driver

`2a41cf1c1d9e5d32` (Win10) / `e5bd8207135b8887` (Win7).

This is the engine's own list of what a level loads. A resource absent from it
is never loaded; a resource present that the engine cannot serialize fails the
level load outright. It is the only authoritative answer to "what is in this
level".

### 2.1 Layout A — per-level closure (281 of 298)

```
+0x00  u32  0
+0x04  u32  count
+0x08  count × 16B   (type_hash:u64, resource_hash:u64)
       Table 2       (see 2.3)
EOF-8  u64  tail
```

### 2.2 Layout B — aggregator (17 of 298) — **newly decoded**

Previously documented as "NOT decoded here" in the only reader that existed, so
every master / menu / global archive read as empty. **The Summer lobby is
Layout B**, which is why its closure appeared absent.

```
+0x00        u32  child_count N
+0x04        N × u64  child archive hashes
+0x04 + 8N   u32  record_count
+0x08 + 8N   record_count × 16B  (type_hash, resource_hash)
```

*Evidence:* on the Summer build's 79 archives, **69 parse with a 100%
type-check** — every record's column A is a real type directory. `mpl_lobby_b2`
declares 8 children and the 8 following u64s are all real archive files
(probability of one random u64 hitting a 79-file set: 4×10⁻¹⁸). The other 10
are Layout A or stubs.

Children must be walked **transitively**: the lobby pulls in
`r14_glb_global_mp` (89 KB) among others, and its resources are part of the
level.

### 2.3 Table 2 is misframed by four bytes — **correction**

`quest_combat_port/tools/resource_io/carchiveresource.py` frames Table 2 as
`N × (field1:u32, type_hash:u64, tag:u32)`. That round-trips, but it splits a
field. The real layout is a u32 count followed by 16-byte records:

```
[N:u32]  [type:u64][version:u64]  [type:u64][version:u64] ...  [role:u32]
```

Under the old framing a section's `version` appears as its own `tag` (low 32)
plus the **next** record's `field1` (high 32) — which is why that field was
documented as "loader index (OPEN — derive via loader disasm)". It is not a
loader index.

*Evidence, all against the live build's 281 Layout-A archives:*

1. `(next.field1 << 32) | tag` is **constant per type — 0 of 163 types vary**,
   while `field1` alone takes 0/1/3/5 for a fixed tag.
2. It reproduces the values the engine demands exactly:
   `CScriptResourceWin10` → 9472387077, `CCanvasUICRWin10` → 6289902299.
3. The last section has no next record; its high word lives in the **tail's low
   32 bits**. Rebuilding it that way matches the type's known version in **265
   archives with 0 mismatches**, and the tail's top 32 bits are 0 in all 280.

The version is a property of the **target game**, not of the data. Registry for
the live PCVR build: `data/evr_pcvr_archive_tags.json` (165 types).

---

## 3. Geometry

### 3.1 Primary + GPU

A model's geometry is a descriptor (`CGMeshListResource` **or**
`CGInstancedModelResource`) plus its GPU sidecar. `find_mesh_and_primary`
resolves the pair.

### 3.2 `CGMeshListResource` — count-prefixed tables

```
u32 meshes_cnt            ; × 152 B   CGMeshData
u32 renderparams_cnt      ; × 112 B   CGRenderParams
u32 vertexbuffers_cnt     ; × 336 B   CGVertexBufferData
u32 morphbuffers_cnt      ; × 336 B
u32 morphindexbuffers_cnt ; ×  16 B
u32 indexbuffers_cnt      ; ×  16 B
u32 lodchildindices_cnt   ; ×   4 B
```

*Evidence:* the walk is self-checking — a wrong stride overruns the file or
yields an absurd count — and it parses **359 of 359** non-stub mesh lists in
the live extract with zero failures.

### 3.3 `CGMeshData` stride differs by platform — **Win7 vs Win10**

| | count @ | table @ | stride |
|---|---:|---:|---:|
| Win10 | 0 | 4 | **152** |
| Win7 | 8 | 12 | **128** |

Win7 puts a u64 ahead of the count, so `u32@0` is a 0/1 flag. Reading a Win7
file with the Win10 frame yields "one record starting at byte 4" — on the
Summer build that flag is 1 for 80 of 439 mesh lists, i.e. 80 records of pure
misalignment indistinguishable from real ones.

*Evidence:* measured on the 303 models shipping in **both** the Win7 (Summer)
and Win10 (Summer2) builds — counts agree model-for-model, and each Win7 record
is the Win10 record minus two u64s after the mesh id and one further in (24
bytes), with the trailing 96 bytes byte-identical. A field at Win10 offset
`n ≥ 0x38` sits at `n − 24` in Win7.

### 3.4 Vertex layout

```
stream0 (uv / colour / lightmap uv)  at base_offset,           stride = stream0_size / vertex_count
stream1 (POSITIONS, f32×3)           at base_offset + stream0_size, stride 28
indices                              at indexbuffers.offset,   2 or 4 bytes each
```

Position stride 28 is confirmed by exclusion — every other stride tested
(12/16/20/24/32/36/40) produces non-finite garbage, on both well-formed and
suspect models.

⚠ **Not settled:** some models decode to implausible extents — `ce303d6bfc8fd138`
is 15,764 vertices inside 0.18 m, with four LODs at byte-identical extents.
Stride 28 being "the only one that isn't garbage" does **not** prove it right
for these; a quantized format with an unapplied scale would also yield small
finite floats. Treat sub-metre models with thousands of vertices as suspect.

---

## 4. Model discovery — how a level's models are found

This is where geometry goes missing, and two independent bugs lived here.

### 4.1 `CModelCR` — actor → model

Records are 0x28-strided; the parser accepts one whose `component_type` is in a
known set, or whose `record_id == 0x1C` with `flags == 0x000FFFFF`. That rule
is **not** the weak link: over `mpl_combat_fission`'s 726 records citing a real
model on a real actor, exactly **one** fails it.

**Bug 1 — last-write-wins.** `level_reader.parse_model_cr` returns
`{str(nodeid): {...}}`, so an actor binding several models keeps only the last.
On fission, **102 of 584 model-bound actors bind more than one model, and 18
models are lost outright**. Fixed by `evr_scene_extract._model_cr_bindings`,
which returns a list per actor.

### 4.2 The model table — **newly decoded**

`CModelCR` ends with one contiguous run of u64s that are all real model hashes
(87 entries on fission). It is a model TABLE, not a record array, so the
0x28-strided walk reads it as noise — the same hash lands in the
`component_type`, `selector` and `flags` slots at successive offsets.

Empty 56-byte stub models sit inside a repeating group with the meshes that
carry their geometry:

```
C* D D D   C* D D D            stub C, then its meshes
N  O  P*   N  O  P*   N O P*   stub P, beside N (1.3 MB) and O (2.2 MB)
```

⚠ **Heuristic.** The two observed groups differ in size (4 and 3) and in
whether the stub leads or trails, so `_stub_substitutes` uses a fixed ±2 window
rather than a decoded record. `--no-stub-substitute` disables it.

### 4.3 Empty stubs are real

A 56-byte descriptor with a 0-byte GPU blob and `meshcount = 0` is genuinely
empty — byte-identical across three independent extracts (live PCVR, Summer2
Win10, Summer Win7). Not an extraction failure.

### 4.4 Measured result on `mpl_combat_fission`

| | models with geometry collected | missing |
|---|---:|---:|
| original | 72 | 26 (5.3 MB) |
| + multi-binding | 89 | 8 (4.1 MB) |
| + stub expansion | **91 of 98** | 6 (0.32 MB) |

Base map geometry is complete independently (declared submesh count equals
decoded on all five sublevels: 30/30, 101/101, 44/44, 49/49, 92/92), and every
static instance places (756/756, 229/229, 109/109, 481/481).

---

## 5. Materials

### 5.1 `CGMeshData` carries no material reference

The offset probe scores every 8-byte-aligned field against the real material
corpus. Result on three separate levels (fission, Summer lobby, Summer2):
**0 hits over 498 records, coverage 0.0%, empty scoreboard.** These builds do
not put material refs in `CGMeshData`. The per-model fallback is the correct
path, not a degraded one.

### 5.2 The material index is in `CGRenderParams`

Each 112-byte draw record carries `matidx` at **+0x20** and `vertexcount` at
**+0x40**.

**Bug 2 — a dead import.** `_renderparams_from_meshlist` did:

```python
try:
    import cgmeshlistresource as meshlist_reader   # exists in NO checkout
except Exception:
    return None
```

so it returned `None` for every model, always, silently. Every
mesh-list-primary model skipped the draw-record route and fell through to
positional assignment — the `material_index_fallback` route in the role tally.
Fixed by reading the count-prefixed tables directly (§3.2):
**359 of 400 sampled models now yield renderparams — 3,575 draw records that
were previously 0.**

### 5.3 Roles

Texture roles come from the shader set, not the material. Across a level only
~21% of materials name a shader set, so role routing also uses the material's
own slot table and a DXGI-format fallback.

⚠ `CGMaterialResource.auxillaryinputs` is a dead end for surface textures: over
1727 shipped materials it holds exactly two inputnames, `cutting_cut_decal` and
`cutting_scorch_decal`.

### 5.4 Blend modes that read as "missing geometry"

`eBlendSkirt` (decal) and `eBlendAdditive` / `eBlendLinearDodge` render as
nothing in material preview when their alpha comes from the base colour or when
they bind no routed colour channel. An object that is present in solid mode and
absent in material preview is a material problem, not a geometry one.

---

## 6. Textures

`CGTextureResourceWin10GPU` is a **complete, ready-to-use DDS file** (starts
with `DDS `), addressed by the same hash as the descriptor. Present for
essentially every texture (12,275 of 12,275 in the live build). Prefer it over
reconstructing from `RawTexturePackfile` layouts.

`RawTexturePackfileWin10` is the single largest thing in the game — **9.67 GB
across 17,226 files**, 64% of the extract.

---

## 7. UI — canvases

Echo VR's UI is not meshes. A screen is a **canvas**: a pixel-sized rectangle
of elements, each a sub-rectangle of a shared texture atlas, placed on an actor
node with a pixels-per-metre scale. Nothing UI-shaped appears in a mesh export
because there is no mesh.

### 7.1 `CCanvasUICR` — placement, 88-byte records

Confirmed against the engine schema at
`core/types/libs/components/canvasui.radattr`:

| offset | schema field | note |
|---|---|---|
| +0x08 | actor nodeid | 410/410 resolve on the busiest level |
| +0x18 | record id | |
| +0x20 | `:uiasset` | the canvas |
| +0x28 / +0x2c | `:scalemin` / `:scalemax` | a **range**, not per-axis scale |
| +0x30 | `:pixels` | schema default 150.0 — and 2850 of 2996 placements read exactly 150.0 |
| +0x38 / +0x40 | `:transform` / `:model` | component refs, unset on 483–485 of 499 |
| +0x48 | `:texture` | per-placement override, set on 370 of 499 |

### 7.2 `CUICanvasResource`

Canvas size in pixels at **+0x14** (not +0x0c — the two pairs are equal on most
canvases, which is why the wrong one looks fine until it isn't; 93% of element
rects fall inside +0x14 on both formats vs 77%/91% for +0x0c). Element count at
+0x28.

| | base | stride | texture | UV | rect |
|---|---:|---:|---:|---:|---:|
| Win10 | 568 | 232 | +0x00 | +0x10 | +0x74 |
| Win7 | 488 | 144 | +0x00 | +0x08 | +0x30 |

⛔ The rect offset is **not** safe to pick by "is it in bounds and correctly
ordered" — a rect of a few pixels passes trivially. An earlier reading scored
95%/92% on that test while producing quads under 2.3 cm on canvases metres
wide. The offsets above were chosen by **area** (median element covers 4–16% of
its canvas, max 1.015) and **cross-build agreement** (`win10 = win7 + 0x44`
across 850 aligned element pairs).

---

## 8. Animations — table only

`CAnimSetResourceWin10` (`e9e7d2e25d8e2252`): 53 files, 11.4 MB.

```
+0x00  (ptr=0, A:u64)   A = animation count (1..66)
+0x10  (ptr=0, B:u64)   B = byte size of the channel region
+0x20  (ptr=0, C:u64)   C ≈ A
+0x30  animation records — stride 136, A entries
+0x30 + 136A            channel region, B bytes
```

*Evidence:* `0x30 + k*136` yields exactly A **distinct** non-null `CSymbol64`s
in **53 of 53** files; a blind `(base, stride)` probe independently locates the
channel region at `48 + 136A`; and the previously unexplained header word at
+0x30 is simply animation 0's name.

Record fields, measured over all 669 animations:

| offset | content |
|---|---|
| +0x00 | name `CSymbol64` — **confirmed** |
| +0x08 | flag 0/1 (665/4) — probably `looping` |
| +0x0c | byte offset into the channel region — always a multiple of 36 |
| +0x10 | channel count — tiles B exactly on 23/53 |
| 12 other fields | **zero in all 669 records** |

Names recovered: `idle`, `ready`, `boost`, `kick`, `grip`, `show`,
`look_pitch`, `look_yaw`, `look_roll`, `root_ik`, `ghost_ik`,
`hand_left_gestures`, `hand_right_gestures`, `hand_right_grip_plane`.

⛔ **No poses.** There is no duration and no joint count in the record — both
live in the channel region, which is lossy-compressed fitted curves
(`core/animsets/animcompresssettings.radattr` defines per-joint error
tolerances and separate camera / footpredict / real channels). No raw `f32×4`
quaternion runs exist in any file. On 30 of 53 files the channel offsets step
by an alternating 36 and 56 — 56 being the engine's `CTable` descriptor size —
so that region interleaves descriptors with entries and its framing is unsettled.

⚠ **No round-trip check is available here.** Every other format in this
document is pinned by exact agreement — mesh counts, version tags byte-identical
across 281 archives, closure diffs. Compressed animation is lossy, so a wrong
curve reading still produces plausible numbers. Decoding the channel region
needs a **visual** oracle (a known pose), not a numeric one.

---

## 9. What is NOT decoded

25 of 235 types have a decoder. By volume, the significant gaps:

| type | files | size | note |
|---|---:|---:|---|
| `RawTexturePackfileWin10` | 17,226 | 9.67 GB | superseded by the DDS sidecar for most uses |
| `CBVHResourceWin10` | 32 | 450 MB | bounds only via `parse_bvh_resource` |
| `CPhysicsResourceWin10` | 2,243 | 218 MB | collision |
| `StreamingScriptWin10` | 567 | 215 MB | level behaviour |
| `CGReflectionProbeResourceWin10GPU` | 32 | 199 MB | |
| `CMaterialTypesBVHResourceWin10` | 32 | 95 MB | |
| `CWWiseSoundBankResourceWin10` | 41 | 88 MB | audio |
| `CGParticleEffectResource` / `GraphResource` | 34+ | — | effects |
| `CGStandaloneShaderResourceWin10` | 358 | 7 MB | |
| ~100 component `CR` types | — | small | gameplay behaviour |

Partial decoders, stated honestly: `CGSceneResource` — lights only.
`CSkeletonResource` — bind pose only, bones parentless. `CAnimSetResource` —
table only. `CBVHResource` — root bounds only.

---

## 10. Tools in this repo

| script | does |
|---|---|
| `evr_level_map.py` | full transitive closure of a level, both archive layouts, every type with counts/sizes/decoder status |
| `evr_scene_extract.py` | level → `.lescatter` package (geometry, materials, textures, lighting) |
| `evr_model_extract.py` | one model → package |
| `evr_ui_extract.py` | UI canvases → textured quads |
| `evr_animset.py` | animation inventory |
| `evr_structural_decode.py` | decode a model from its own tables rather than by scanning |
| `evr_resource_types.py` | type hashes, Win7↔Win10 translation, `verify_win7_hashes()` |
| `le2_port.py` | Lone Echo 2 level → Echo VR flat layout |
