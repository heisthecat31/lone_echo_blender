# Package formats

The extractor (Stage 1) writes two portable package formats, and the Blender add-on
(Stage 2) reads them. Both are plain folders — human-inspectable JSON plus raw
little-endian binary blobs — so a package is self-contained and durable long after
extraction.

All numeric blobs are **little-endian**. All geometry is stored in **native game
space**; the add-on applies any coordinate conversion (see [Coordinate
space](#coordinate-space)) at import time, never baking it into the stored data.

★ **Both formats are purely additive at every step.** Every key that has ever
shipped keeps its name, its type and its byte layout, so an older reader loads a
newer package unchanged and a newer reader loads an older one. An absent key means
**"not extracted"** — never a different value. Only the `format` field is validated
when a package is opened; every version is accepted.

---

<a id="evidence-vocabulary"></a>
## Evidence tags

Claims in these documents and in the source carry a provenance tag. This is the
whole vocabulary, and each tag asserts exactly what its row says — no more:

| tag | what it asserts |
| --- | --- |
| `stream-confirmed` | Read out of the bytes of shipped resources on disk. The site states its **container** and its **coverage**. |
| `corpus-confirmed` | The same, measured across the whole extracted corpus rather than one resource. |
| `shader-confirmed` | Matches the arithmetic the engine's own shaders perform. |
| `name-confirmed` / `name-only` | Matches the engine's own type, field or enum names. `-only` means the name is known but **no shipped resource exercises it**. |
| `engine-confirmed` | Verified inside Blender, by building it and reading the result back. "Engine" here is the **render** engine, not the game. |
| `export-validated` | Checked against a package this repository's own extractor wrote. ⚠ Weaker than `stream-confirmed`: it proves the tool is self-consistent, not that the reading of the shipped bytes is right. |
| `inferred` | Stated as an inference, not a measurement. |

The convention they serve: **this repository cites the engine by its own symbol
names — `kLambdaSG5`, `CGVertexFormat::EUsage`, `NRadEngine::EBlendMode` — and
never by a path or line number into a source tree, a debug header or a disassembly
listing.** A symbol name is a fact about the shipped bytes, which is what this
repository is for; a file and line is a fact about how the reverse engineering was
done. ⛔ `scripts/scrub_gate.py` enforces that mechanically, as rule
`engine-source`.

---

## `.lemesh` — a single mesh or model

A `.lemesh` package is a **directory**:

```
<name>.lemesh/
  manifest.json          fully self-describing: objects, attributes, draws, materials
  blobs/*.bin            raw flat little-endian arrays (float32 / uint32 / int32)
  textures/*.dds         (optional) source textures referenced by the materials
  skeleton.json          (optional) joint hierarchy and rest pose
```

- **`manifest.json`** describes everything needed to rebuild the model: the format
  tag and version, provenance, one entry per mesh object (its vertex attributes,
  index buffer, draw ranges, bounds, and flags), and the material list. Each vertex
  attribute is decoded to a canonical array and points at its own blob, so the
  reader never has to interpret a packed vertex layout.
- **`blobs/`** holds the raw per-attribute and index arrays as flat little-endian
  values (float32 for positions/normals/UVs/colors, integer types for indices and
  skin data). Storing geometry pre-decoded keeps the importer trivial.
- **`textures/`** (optional) holds the DDS textures the materials reference, present
  only when textures were extracted.
- **`skeleton.json`** (optional) holds the joint hierarchy and rest pose used to
  build an armature and to name skin vertex groups.

### Vertex attributes, and the semantic slot

An object's `attributes` map holds one entry per decoded attribute, keyed by a
canonical transport name — `position`, `normal`, `tangent`, `uv0 … uvN`,
`color0`/`color1`, `skin_indices`, `skin_weights` — each carrying its `usage`,
`slot`, `comps`, `encoding`, and its own blob.

⚠ **`uvN` and `colorN` are appearance order in the element table, not semantic
slot**, and on real data the two disagree. Three keys exist so a consumer never has
to guess:

| Key | Where | Meaning |
| --- | --- | --- |
| `slot` | per attribute | `CGVertexFormat`'s `SVertexElement.slot` — the semantic index the `uvN`/`colorN` suffix throws away. |
| `lightmap_uv` | per object | The **resolved** attribute name of the texcoord set on semantic slot 4, or `null` when the mesh carries no lightmap UV set. |
| `raw_vertex_format` | per object | The raw `SVertexElement` table, verbatim, so the package stays auditable even though geometry is stored decoded. |

The baked lightmap is texcoord **slot 4** specifically (`shader-confirmed`), and
the corpus has **64 objects on `uv2` and 29 on `uv3`** — the literal `"uv1"` is
wrong on every one of them. Resolution order is `lightmap_uv`, then slot 4 out of
`raw_vertex_format`, then `null`. Because `raw_vertex_format` is present on **every
`.lemesh` manifest ever written**, a package written before `slot` and
`lightmap_uv` existed resolves exactly as a freshly written one, with no
re-extraction. ⛔ `null` means the mesh has *no* slot-4 set; substituting another UV
set there is the defect this resolution order exists to prevent.

Two attributes are worth their own note:

| Attribute | Notes |
| --- | --- |
| `tangent` | `CGVertexFormat::EUsage::eTangent`, `s16n × 4`. Decoded on **913 of 913** objects (container: one full local export under `blender_tool/exports`). ⛔ `.w` is **not** a ±1 handedness bit. It takes exactly **four** values — `-1.0`, `-0.5`, `+0.5`, `+1.0` — over 509,266 vertices. Its **sign** is the bitangent handedness (397,082 / 397,082 vertices agree with the UV-derived handedness, 100.00 %); its **magnitude** tags a duplicated back shell (magnitude `0.5` on 109,400 / 109,400 position-identical partners, normal exactly negated on 99.92 %, tangent negated on only 65.67 %). A fifth value is refused loudly rather than rounded. |
| `color1` | The engine's per-vertex **layer-blend weight**, not decoration: layer *i* reads component *i−1* of the `COLOR1` vertex stream and the layer composite multiplies the blend mask by it (`shader-confirmed`). Ships on **523 of 913 objects (57.3 %)**. ⚠ whether a given shader samples it is a permutation bit that is **not on disk**, so the package carries the data and asserts nothing about its use. |

Blender will not accept an authored per-loop tangent — `mesh.loops[].tangent` is
read-only and computed from the active UV layer — so the importer stores the
shipped basis as the generic attributes `le_tangent` and `le_tangent_w` and
rebuilds the TBN in shader nodes. See [MATERIALS.md](MATERIALS.md).

### Lightmap fields on an object

Each object entry carries three fields the baked lightmap needs. They are decoded
and written but nothing consumes them automatically yet — see
[LIGHTING.md](LIGHTING.md).

| Field | Meaning |
| --- | --- |
| `lightmap_index` | Row index into the lightmap resource the object's scene binds. `0xffffffff` = not lightmapped. It is a **direct** row index, not an index over the populated rows only. |
| `lm_slice_index` | The lightmap **page**, i.e. the texture-array slice of the AO/occlusion maps. The colour array holds five SG lobes per page, so its slices are `page*5 .. page*5+4`, page-major. |
| `numlobes` | The bake's spherical-gaussian lobe count. Reads `4` on every shipped mesh measured (1221 of 1221), while the colour array carries 5 slices per page — that mismatch is unresolved. |

`numlobes` is additive: an older package simply has no such key and reads as `0`.
The importer copies all three onto the object as `le_lightmap_index`,
`le_lm_slice_index` and `le_lightmap_numlobes`.

### Scene-set gating, per draw and per object

Every draw carries the leading `SSceneSetMask` of its `CGRenderParams` — 32 bytes
of bits followed by a min-count. This is the **third** LOD system, the one
characters use, and it is *not always an LOD ladder*: see [LOD.md](LOD.md) and
[CHARACTERS.md](CHARACTERS.md) §2.

| Field | Where | Meaning |
| --- | --- | --- |
| `scene_mask` | per draw | The draw's scene-set bits as an integer. **`0` = no scene set gates this draw**, so it always draws — which is what every level mesh in this corpus carries. Only models with a `ComponentLOD` set bits here. |
| `scene_set_bit` | per draw | Index of the **lowest** set bit, or `-1` when the mask is `0`. |
| `scene_set_min_count` | per draw | The mask's trailing min-count field, carried verbatim. |
| `scene_lod_level` | per object | **What a consumer selects on**, written only when at least two distinct non-zero masks occur across the mesh-list. `null` = this mesh-list has no scene-set LOD and every mesh always draws; otherwise `0` is the highest detail. `package_reader.select_lod_objects` filters on it. |

⚠ "Bit index == LOD level" is `inferred`, not measured: the bit → set-**name**
mapping lives in the model's own `CGSceneSetsData`, not in the mesh-list. What is
`stream-confirmed` is the **partition** — on one character head the bits split its
19 meshes 10 / 8 / 1 with monotonically falling vertex counts, and its
`ComponentLOD` names exactly `lod0`/`lod1`/`lod2`.

### Per-draw LOD (version 2)

Each entry of an object's `draws` array carries a `lod` block:

| Field | Meaning |
| --- | --- |
| `level` | **This is what a consumer selects on.** `0` = highest detail. |
| `is_lod_parent` | `true` when this draw is a level-0 root that owns coarser draws. |
| `is_lod_child` | `true` when this draw is a coarser level of another draw. |
| `primset_idx`, `children_start`, `children_count` | The raw on-disk chain fields, carried verbatim as audit metadata. |

A mesh's coarser LOD levels are **extra draws covering later slices of the same
index buffer**, so emitting every draw stacks the levels on top of each other. The
add-on selects one level (default `0`) with `package_reader.select_lod_draws`,
which snaps the request into the ladder the mesh actually carries: a mesh whose
chain stops at level 1 still emits its level 1 when level 3 is requested. This
chain is populated in only 11 of 1,240 shipped mesh-lists, so for most packages
selection is a no-op. See [LOD.md](LOD.md).

### Material bindings carry their provenance

Every material spec records **where each channel's binding came from**, alongside
the bindings themselves, so a corpus-**voted** role can never be read as an
array-**declared** one:

| Field | Meaning |
| --- | --- |
| `role_textures` | role key → texture hash. The raw binding, kept for audit. |
| `role_sources` | role key → how that role was resolved: `array` (this shaderset's own `SShaderInputData` row), `archive` (propagated from a sibling in the same archive), `corpus` (the corpus role index), `format` (the DXGI `FORMAT` alone), `rdef` (`RDEF` knew the texture, nothing knew the role). |
| `role_ambiguity` | texture hash → `{role: votes}`, for every bind the corpus index disagreed with itself about. |
| `texture_names` | texture hash → the exact `RDEF` texture name. Audit only — nothing routes on it. |

`role_sources`, `role_ambiguity` and `unrouted_role_notes` are **always present**,
often `{}`, so the `.lemesh` and level specs keep identical key sets. See
[MATERIALS.md](MATERIALS.md) for the ladder itself.

### `reflection_probes` — a level section

`reflection_probes` is **level-scoped, not per-object**: one probe set serves every
mesh-list of a scene, exactly like its ambient-diffuse sibling `lightmap` (see
[LIGHTING.md](LIGHTING.md)). It holds `resource`, `count`, `box_count`,
`sphere_count`, `texture_format` / `texture_format_name`, `gpumemsize`,
`gpu_present`, `colorspace`, and one `probes` entry per probe. 94 resources across
90 archives; the shipped cube is BC6H_UF16 **256² with 9 mips**.

⛔ **Selection boxes are not probes** — one shipped level has **23 boxes over 16
probes**. Do not read `box_count` as a probe count.

The per-object binding stays on each object's `probe_index` (`CGMeshData.probeidx`):
`null` = the extractor did not read it, `4294967295` = the shipped "no probe"
sentinel, which is **not** probe 0.

Both level sections are **omitted, never guessed**, when the extractor cannot
resolve the scene's resource; the add-on then falls back exactly as before.

### Versions

- **Version 2** adds `draws[].lod.level` and `.is_lod_child`. Purely additive: a
  version-1 package reads as all-level-0, which the selector passes through
  unchanged, so version-1 packages import exactly as before.
- **Version 1** has no `level` key.

⚠ The `.lemesh` keys added since — `attributes[].slot`, `lightmap_uv`,
`scene_mask` / `scene_set_bit` / `scene_set_min_count`, `scene_lod_level`,
`probe_index`, `numlobes`, and the `lightmap` and `reflection_probes` sections —
are carried **without a version bump**, deliberately. Each is self-describing —
present means a newer writer produced it — and a manifest without them behaves
exactly as it did before: the lightmap UV set still resolves out of
`raw_vertex_format`, and an absent `scene_mask` / `scene_lod_level` makes
`select_lod_objects` pass the package through untouched. The version number would
therefore distinguish nothing a reader can act on.

---

## `.lescatter` — a whole scatter level

A `.lescatter` package is a **directory**:

```
<name>.lescatter/
  manifest.json          level description: meshes + per-instance placement
  blobs/                 geometry blobs + the instance table
```

Every unique mesh is stored once; the instances reference meshes by index and each
carries its own placement transform.

### `manifest.json`

Top-level fields:

| Field | Meaning |
| --- | --- |
| `format` | Always `"le_scatter"`. This is the only field validated on load. |
| `version` | `1` … `5` (all load; see [Versions](#versions-1)). |
| `master` | The level's identifier (hex string). |
| `axis` | `"native"` — geometry is stored in native game space. |
| `num_meshes` | Number of unique meshes. |
| `num_instances` | Number of placed instances. |
| `meshes` | Array of per-mesh entries (below). |
| `instances_blob` | Relative path to the instance table (`blobs/instances.bin`). |
| `lod` | (**Version 3**) the per-instance LOD binding (below). |
| `lightmap` | (**Version 4**) the master's lightmap **resource binding**. Omitted entirely when the extractor cannot see it, rather than guessed. |
| `lightmap_stats` | (**Version 4**) a summary readout of the per-mesh lightmap ids: `meshes_lightmapped`, `meshes_unlit`, `meshes_with_uv1`, `slice_indices`, `numlobes_values`. |
| `instance_lightmap` | (**Version 5**) the per-instance baked lightmap stream (below). |

### Per-mesh entry

Each element of `meshes` describes one unique mesh:

| Field | Meaning |
| --- | --- |
| `index` | The mesh's own index. Instances reference this value (it is **not** the array position — a subset package may be sparse). |
| `name_hash` | The mesh's name identifier (16-hex string). |
| `matidx`, `shdidx` | Top-level material and shaderset indices (mirror `draws[0]`). |
| `aabb_min`, `aabb_max` | Axis-aligned bounds `[x, y, z]`. |
| `instance_offset`, `instance_count` | This mesh's run within the level's instances. |
| `nverts`, `nindices` | Vertex and index counts. |
| `positions` | Path to the position blob (`float32 × 3 × nverts`). |
| `normals` | (Optional) path to the normal blob (`float32 × 3 × nverts`); key absent when the mesh has no normals. |
| `uv0` | (Optional) path to the first UV set (`float32 × 2 × nverts`); key absent when the mesh has none. |
| `indices` | Path to the index blob (`uint32 × nindices`). |
| `proxy` | `true` when the mesh is a collision/LOD proxy stand-in. |
| `draws` | (**Version 2**) the mesh's draw list (below). |
| `uv1` | (**Version 4**, optional) path to the mesh's lightmap UV set (`float32 × 2 × nverts`), same naming and layout convention as `uv0` so a reader streams it with the identical code path. Key absent when the mesh has none. |
| `lightmap_index`, `lm_slice_index`, `numlobes` | (**Version 4**) the three `CGMeshData` lightmap ids, always present; `0xFFFFFFFF` = not lightmapped. Same meanings as the [`.lemesh` fields](#lightmap-fields-on-an-object). |

**`draws`** (version 2) is the list of draws that make up the mesh, in order. Each
entry is:

| Field | Meaning |
| --- | --- |
| `matidx` | Material index for this draw. |
| `shdidx` | Shaderset index for this draw. |
| `idx_start` | Start position of this draw's triangles, **relative to this mesh's own index buffer**. |
| `idx_count` | Number of indices in this draw. |

`draws[0]` always mirrors the mesh's top-level `matidx`/`shdidx`. The add-on turns
each distinct `(matidx, shdidx)` pair into a Blender material slot and assigns every
face to its covering draw.

### `blobs/instances.bin`

A flat array of **N records** in global order (`i = 0 … N-1`), each **44 bytes**,
little-endian:

| Offset | Field | Type |
| --- | --- | --- |
| 0 | `mesh_index` | `uint32` |
| 4 | `translation` | `float32 × 3` |
| 16 | `rotation` | `float32 × 4` (quaternion, `x, y, z, w`) |
| 32 | `scale` | `float32 × 3` |

Each record places one instance of the mesh named by `mesh_index` at the given
translation, rotation, and (possibly non-uniform) scale.

### `lod` and `blobs/instance_lod.bin` (version 3)

**Every LOD level of a prop is a separate mesh with its own instances**, so placing
all N instances stacks every level of every prop on top of each other — 61.3 % of
one shipped level's 21,394 instances are lower-LOD duplicates. Version 3 carries the
grouping so an importer can select one level.

The `lod` manifest block:

| Field | Meaning |
| --- | --- |
| `blob` | Relative path to the LOD table (`blobs/instance_lod.bin`). |
| `record` | The record layout, as a string, for self-description. |
| `num_groups` | Number of distinct LOD groups (one per placed prop). |
| `max_level` | Coarsest level present anywhere in the level. |
| `levels_histogram` | `{"<level>": instance count}` — a cheap sanity readout. |

`blobs/instance_lod.bin` is **N records parallel to `instances.bin`** (same order,
same count), each **12 bytes**, little-endian:

| Offset | Field | Type |
| --- | --- | --- |
| 0 | `lod_group` | `uint32` (`0xFFFFFFFF` = this instance has no LOD group) |
| 4 | `lod_level` | `uint32` (`0` = highest detail) |
| 8 | `lod_group_levels` | `uint32` (how many levels this instance's group has, ≥ 1) |

It is a separate blob precisely so the 44-byte `instances.bin` contract stays
byte-identical and version-1/2 readers keep working.

`lod_group_levels` is carried per instance so a consumer can **clamp without a
group table**: a two-level prop asked for LOD 3 still contributes its LOD 1 rather
than vanishing. See [LOD.md](LOD.md).

### `instance_lightmap` (version 5)

The **per-instance** baked lightmap stream, read out of `SGPackedInstanceData`
(page = `u16` at record `+0x1a`, `C2Vector lightmapuvs[nverts]` at `+0x2c`, record
stride `44 + 8·nverts`).

⛔ **For instanced statics *this* is the level's lightmap UV input, not the
per-mesh `uv1` blob.** 1046 of one level's 1050 per-mesh `uv1` blobs are **entirely
zero**, because the engine overrides that slot per instance — so instancing cannot
survive a bake. See [LIGHTING.md](LIGHTING.md).

It is written as four blobs with fixed names — fixed rather than per-mesh, because
the stream is one flat array in global instance order:

| Blob | Contents |
| --- | --- |
| `blobs/instance_lm_uv.bin` | `float32` `u,v` pairs, every instance's UVs concatenated in global order. |
| `blobs/instance_lm_uvoff.bin` | `uint32` start of each instance's run. |
| `blobs/instance_lm_count.bin` | `uint32` length of each instance's run. |
| `blobs/instance_lm_page.bin` | `uint32` lightmap page per instance. |

⚠ `offsets` and `counts` are in **UV PAIRS, not bytes**, and all three index arrays
are **parallel to `blobs/instances.bin`** — index `i` is the same instance the
instance table's record `i` is.

The manifest section is self-describing (`count`, `uv_blob`, `offsets_blob`,
`counts_blob`, `page_blob`, `total_uv_pairs`, `order`, `uv_dtype`, `uv_record`,
`index_dtype`, `source`, `uv_bytes`, `page_histogram`, `warnings`), so a consumer
needs no other document. Two further fields are the arithmetic self-check:
`predicted_instancedatasize` is `44·C + 8·Σcounts`, and `instancedata_residual` is
its difference from the master's own `instancedatasize` — **a non-zero residual
means a mis-strided read**.

`flip_v_applied` is always **`false`**: the UVs are copied verbatim off disk,
exactly as `uv0`/`uv1` are, and flipping V is the consumer's job.

The section is **opt-in** behind `--instance-lightmap`, because it is ~52 MB on one
shipped level and it breaks mesh-datablock sharing. When it was not extracted the
section is still written, as `{"present": false, "reason": …}`, so a consumer can
tell **"not extracted" from "not available"**.

### Versions

- **Version 5** adds the `instance_lightmap` section and its four blobs.
- **Version 4** adds the per-mesh `uv1` blob, the three `CGMeshData` lightmap ids
  (`lightmap_index`, `lm_slice_index`, `numlobes`), the master's `lightmap`
  resource binding and the `lightmap_stats` readout.
- **Version 3** adds the `lod` block and `blobs/instance_lod.bin`.
- **Version 2** carries a `draws` list on every mesh (full multi-material support).
- **Version 1** has no `draws` key. The reader treats such a mesh as a single draw
  spanning its whole index buffer with the mesh's top-level `(matidx, shdidx)`, so
  version-1 packages continue to load unchanged.

A version-1 or version-2 package has no `lod` block; the reader then reports one
level for every instance, so LOD filtering degrades to "keep everything" and such
packages import exactly as they did before. A package below version 4 has no
lightmap ids, which reads as "no lightmap UV / no lightmap id", never as a
different value.

Only the `format` field is validated when a package is opened; all versions are
accepted.

---

## Coordinate space

Geometry in both formats is stored in **native game space** (Y-up), exactly as
decoded. The Blender add-on applies a Y-up → Z-up basis change at import time:

- For a **`.lemesh`** model, the basis is applied to the object's transform.
- For a **`.lescatter`** level, the basis is applied per instance, on top of that
  instance's translation/rotation/scale, and is never baked into the shared mesh
  data.

Applying the basis at import — rather than rewriting the stored vertices — keeps the
blobs faithful to what was extracted and lets the conversion be toggled off for a
native-space import.
