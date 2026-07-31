# Package formats

The extractor (Stage 1) writes two portable package formats, and the Blender add-on
(Stage 2) reads them. Both are plain folders — human-inspectable JSON plus raw
little-endian binary blobs — so a package is self-contained and durable long after
extraction.

All numeric blobs are **little-endian**. All geometry is stored in **native game
space**; the add-on applies any coordinate conversion (see [Coordinate
space](#coordinate-space)) at import time, never baking it into the stored data.

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
clamped per mesh: a mesh whose chain stops at level 1 still emits its level 1 when
level 3 is requested. This chain is populated in only 11 of 1,240 shipped
mesh-lists, so for most packages selection is a no-op. See [LOD.md](LOD.md).

### Versions

- **Version 2** adds `draws[].lod.level` and `.is_lod_child`. Purely additive: a
  version-1 package reads as all-level-0, which the selector passes through
  unchanged, so version-1 packages import exactly as before.
- **Version 1** has no `level` key.

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
| `version` | `1`, `2` or `3` (all load; see [Versions](#versions-1)). |
| `master` | The level's identifier (hex string). |
| `axis` | `"native"` — geometry is stored in native game space. |
| `num_meshes` | Number of unique meshes. |
| `num_instances` | Number of placed instances. |
| `meshes` | Array of per-mesh entries (below). |
| `instances_blob` | Relative path to the instance table (`blobs/instances.bin`). |
| `lod` | (**Version 3**) the per-instance LOD binding (below). |

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

### Versions

- **Version 3** adds the `lod` block and `blobs/instance_lod.bin`.
- **Version 2** carries a `draws` list on every mesh (full multi-material support).
- **Version 1** has no `draws` key. The reader treats such a mesh as a single draw
  spanning its whole index buffer with the mesh's top-level `(matidx, shdidx)`, so
  version-1 packages continue to load unchanged.

A version-1 or version-2 package has no `lod` block; the reader then reports one
level for every instance, so LOD filtering degrades to "keep everything" and such
packages import exactly as they did before.

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
