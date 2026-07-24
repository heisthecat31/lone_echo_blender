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
| `version` | `1` or `2` (both load; see [Versions](#versions)). |
| `master` | The level's identifier (hex string). |
| `axis` | `"native"` — geometry is stored in native game space. |
| `num_meshes` | Number of unique meshes. |
| `num_instances` | Number of placed instances. |
| `meshes` | Array of per-mesh entries (below). |
| `instances_blob` | Relative path to the instance table (`blobs/instances.bin`). |

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

### Versions

- **Version 2** carries a `draws` list on every mesh (full multi-material support).
- **Version 1** has no `draws` key. The reader treats such a mesh as a single draw
  spanning its whole index buffer with the mesh's top-level `(matidx, shdidx)`, so
  version-1 packages continue to load unchanged.

Only the `format` field is validated when a package is opened; both versions are
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
