# Scenes, levels and placement

Where a mesh goes in the world, how a level names its parent, and how the
skydome is fitted. New in 0.4.0.

## 1. Placement

`le_mesh/scene_build.py` turns a level archive's placement manifests into the
`scene.json` the add-on's **Apply Scene Placement** option consumes.

The world transform of a node is **not** `parentWorld · M_init · M_offset`. That
formula is refuted by the shipped data. The runtime instead switches on
`STransformCD::SProperties.parenttype` (record stride `0xA8`) and, for an
attached node, **overwrites the node's local rotation and position** with an
offset chosen by the parent type. `scene_build.py` reproduces that switch rather
than the composed-matrix guess, and it **reports why every dropped row was
dropped** instead of silently emitting a short table.

A placement's identity is `(actor_node_hash, model_asset_hash)`. The join
cross-products two containers, so the dedupe is counted and reported — a join
that silently collapses rows is indistinguishable from a join that finds fewer.

## 2. The parent-level edge

`le_mesh/level_link.py` reads the **upward** edge only: a level names its parent
gamespace, and the runtime combines the two at load.

Three rows establish the shape, and the third is the control: a level with its
**own** populated static master still parents to the **global** archive. Work on
the later engine generation independently puts the same upward parent-gamespace
edge at `CGameLevelResource +0x10`. Same edge, different offset — a corroborated
reading, not an imported one.

⛔ **This is the upward edge only. It does not enumerate children.** Walking
*down* the tree means scanning every level root for a matching `parent_level`,
which is a corpus job and not this module's. Nothing here claims the format
stores a child list.

Practical consequence: a level that looks empty may be a **parent** whose
content is in its children, or a **child** whose surroundings are in its parent.
Both cases are reported rather than papered over.

## 3. Vista fitting

`le_mesh/vista_fit.py` fits the pieces of an exterior scene from their own
geometry — nothing is assumed from a note:

- **skydome** — a sphere fit over the position blob, plus the count of vertices
  that lie *outside* the fitted shell. That number is what forces the skydome
  special case in any depth-sorted renderer.
- **rings** — a plane fit plus annulus inner/outer radius per ring object.
- **planetary body** — an **oblate** spheroid fit; a sphere fit is visibly wrong
  on the shipped geometry.
- **moons and sun card** — centroid, radius, and the card's facing direction.

`tests/vista_measure.py` is the report generator over an extracted package. It is
pure stdlib, reads only `.lemesh` / `.lescatter` packages, and touches no
archive — so it is safe to run anywhere. The maths it uses is `vista_fit.py`,
which is unit-tested in `tests/test_vista_fit.py`.

One claim worth restating because it is falsifiable: **the ring plane passes
through the play area**. That is a statement about the shipped geometry, not a
composition preference, and `vista_measure.py` recomputes it every run.

## 4. Camera framing

`le_mesh/framing.py` solves a camera placement that frames a given bounding
volume at a given sensor and focal length. It is used by the render probes; it
is not part of the import path.

## 5. Baked lightmaps at level scale

A level's baked lighting reaches instanced statics through the **per-instance**
stream, not through the per-mesh `uv1` blob:

- `SGPackedInstanceData` carries a per-instance `lightmapidx` **page** at `+0x1a`
  and a `C2Vector lightmapuvs[nverts]` array at `+0x2c`, so the record stride is
  `44 + 8·nverts`;
- **1046 of one level's 1050 per-mesh `uv1` blobs are entirely zero**, because
  the engine overrides that slot per instance.

⚠ The consequence for Blender is structural: per-instance UVs mean per-instance
mesh data, so **instancing cannot survive a bake**. The stream is therefore
extracted only on request (`--instance-lightmap`, ~52 MB on one level) and is
not auto-wired.

See [LIGHTING.md](LIGHTING.md) for the lightmap resource itself.

## 6. Axis convention

Everything in a package is stored in the game's native axis. The importer applies
the Y-up → Z-up basis **per instance**, never baked into a shared mesh datablock,
so the same mesh can be shared across instances without duplicating it.
