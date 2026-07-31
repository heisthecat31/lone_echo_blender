# Level of detail

Lone Echo ships **two independent LOD systems**, and both are populated in retail
data. Both are decoded, carried through the package formats, and selectable at
import; the default on both import paths is **LOD 0 (highest detail)**.

Conflating them is the trap — they live in different structures, encode a level
differently, and are selected differently.

| | **A. static-instance LOD** | **B. mesh-list LOD chain** |
|---|---|---|
| structure | `SGStaticInstancesData.lod` (`SGStaticInstanceLODData`) + `lodfadelookup` | `CGRenderParams.lodprimsetidx` / `lodchildrenstart` / `lodchildrencount` + `CGMeshListData.lodchildindices` |
| a level is | **a different mesh**, with its own instances at the same world position | **a later index range of the same index buffer** of one mesh |
| scope | static scatter (levels and their props) | any mesh-list |
| how common | 5,779 LOD groups in one level (`station_front`) alone | **11 of 1,240** mesh-lists — rare, but real |
| decoded by | `le_mesh.static_lod` | `le_mesh.meshlist.assign_lod_levels` |
| selected by | `scatter_reader.filter_by_lod` (`.lescatter`) | `package_reader.select_lod_draws` (`.lemesh`) |

A third variant exists for *dynamic* instances
(`SGDynamicInstancesData.lods : CTable<SGDynamicInstanceLODData>`, a 20-byte record
`{u16 meshindex, u16 nummeshes, f32 nearfadestart, nearfadeend, farfadestart,
farfadeend}`). It has been decoded but **this tool does not consume it** — see
[What is not here](#what-is-not-here).

---

## Why this matters

Before 0.2.0 the scatter importer placed **every** instance, which meant every LOD
level of every prop was stacked on top of every other, in the same space:

| `station_front` | instances | triangles | vs. all levels |
|---|---:|---:|---:|
| all levels (what the tool used to do) | 21,394 | 6,296,171 | 100 % |
| **LOD 0 (the new default)** | **8,288** | **3,670,469** | 58.3 % |
| LOD 1 | 7,968 | 2,249,049 | 35.7 % |
| LOD 2 | 7,381 | 1,746,084 | 27.7 % |
| LOD 3 | 6,263 | 1,404,138 | 22.3 % |
| LOD 4 / coarsest | 6,235 | 1,289,415 | 20.5 % |

**61.3 % of `station_front`'s instances were lower-LOD duplicates.** Another level
is worse: only 26.5 % of its 8,616 instances are LOD 0.

Selecting a level never *removes* a placement. Rendering the same package at LOD 0
(8,288 objects), at coarsest (6,235) and at all levels (21,394) yields
`distinct_positions = 5,092` in all three: the extra instances are exact
co-locations of their LOD 0 siblings. That number comes from Blender world
matrices — a completely different code path from the disk decode.

The same is true at a smaller scale on the `.lemesh` path for the 11 mesh-lists
that carry a system-B chain: one of them imported 83,994 triangles before and
imports 43,629 at LOD 0.

`All levels (stacked)` reproduces the pre-0.2.0 behaviour exactly.

---

## System A — static-instance LOD

### Disk grammar

`SGStaticInstancesData`, read out of shipped bytes and validated on **all 62
populated static-instance masters** of a 102-archive corpus (the other 40 archives
bake no populated master):

```
lod.nodes             CTableA<CReal4,16>   u32 count, 12 B pad, count*16   vec4, w == 0
lod.lodfadeslopeoffs  CTableA<CReal4,16>   u32 count, 12 B pad, count*16   one per LOD
lod.hierlods          CTable<SHierLOD>     u32 count, count*12
                                           {u32 parent, u32 firstchild, u32 numchildren}
lod.nodelookup        CTable<u16>          u32 count, count*2    LOD -> node
lod.totalnumlods      u64                  8-aligned
meshlist              CGMeshListData       inline
instancescount        CTable<u32>          num_meshes
instanceoffsets       CTable<u32>          num_meshes   prefix sum of instancescount
irrsamplelocs         CTable<C3Vector>     num_meshes
dirlightmasks         CTable<u8>           num_instances
visstrlookup          CTable<u16>          num_instances
lodfadelookup         CTable<u32>          num_instances   instance -> LOD index
ditherfadeflags       CTable<u32>          ceil(num_meshes/32)   a per-MESH BITSET
u32 totalinstances, numvisentries, instancedataoffset, instancedatasize,
    instancetypedataoffset, instancetypedatasize
```

Three things bite anyone re-deriving this:

1. **Tables are byte-packed with no inter-table padding.** A `CTable<u16>` header
   can therefore land on an odd offset — `station_front`'s `visstrlookup` header
   sits at 1,033,002. Only the two `CTableA<T,16>` internal 12-byte pads and the
   `u64` 8-align exist. A walker that 4-aligns between tables derails silently.
2. **`ditherfadeflags` is a bitset, not one entry per element** — its count is
   `ceil(num_meshes/32)` (33 words for 1,050 meshes). Reading it as per-instance
   loses the walk.
3. **`totalnumlods` is authoritative and can be smaller than the table counts.**
   One master stores 8,276 `nodelookup` / `lodfadeslopeoffs` rows but
   `totalnumlods == 8,274`; the two slack rows repeat node 0 with stale values and
   no instance references them. This is near-universal — **41 of 62 masters**,
   always 1–3 slack rows. Trim to `totalnumlods` or a contiguity check reports a
   false defect.

`decode_static_lod` walks the tail **backwards** from the fixed 24-byte scalar tail
and re-reads every table's own `u32` count header as an assertion, so a layout
drift raises rather than mis-parsing. On all 62 masters the inline
`CGMeshListData`, walked *forward* from `meshlist_offset`, ends on the exact byte
where the *backward* tail walk puts the `instancescount` header — zero padding,
zero slack.

### The model

* A **node** (`nodes[i]`, a world position with `w == 0`) is one **LOD group** —
  one placed prop.
* A group's LOD levels are a contiguous run in the LOD array; `nodelookup` maps LOD
  index → node, so `level = lod_index − first_lod_index_of(node)`, **rebased so the
  group's finest *drawn* level is 0** (see below).
* Instance `i` draws at `level_of_instance[i]` of `group_of_instance[i]`.
* A level may own **more than one instance**: LOD 0 of a multi-part prop is several
  meshes and coarser levels collapse them. One `station_front` group: L0 = meshes
  2+3+4 (1,856 tris), L1 = 5+6+7 (1,028), L2 = 8+9+10 (712), L3 = mesh 11 (412),
  L4 = mesh 12 (32).
* **Every LOD level is a different mesh.** No group reuses one mesh across levels.

Requesting a level clamps per group, so a two-level prop asked for LOD 3 still
contributes its LOD 1 rather than vanishing.

#### Rebasing: why the finest level is not always index 0

A group's *first* LOD entry can be unreferenced by any instance. Raw levels then
come out `1, 2, 3…`, a request for LOD 0 matches nothing, and the prop disappears —
at the importer's default. This affects **21 groups / 94 instances across 6 of the
62 masters**. `_derive_levels` rebases each group so its finest drawn level is 0 and
emits a warning; after the fix, 0 groups vanish on 62/62. Regression test:
`test_group_with_unreferenced_first_lod_is_rebased`.

### `hierlods` — a hierarchical-LOD cluster table

`SHierLOD` is `{u32 parent, u32 firstchild, u32 numchildren}`. `parent` is an index
into the **LOD array**, and it is *not* always the record index: on one master,
records 9/10/11 all carry `parent == 9` (on the other 13 masters that carry any,
`parent == index`).

Measured over the **14 of 62** masters that carry any (476 records):

* **No instance ever references a parent LOD entry** — 0, on every master. The
  front of the LOD array is pure proxy entries.
* The parent's node sits at the **centroid of its children's nodes** — median offset
  0.35 units, while the children spread a median of 2.49.
* Parent nodes never reappear as leaf nodes. A parent node typically owns two LOD
  entries, each with its own child range.
* Child ranges tile the leaf array almost contiguously.

So it is a hierarchical-LOD *cluster* grouping over the per-prop chains, not a
zone or region index. It is **not needed for level selection**.

### `visstrlookup`

Per instance, the index of its **LOD group's** visibility entry — *not* the identity
permutation over instances (`station_front` first diverges at instance 16). On
62/62 masters it is constant across every instance of a group, a bijection
group ↔ value, its values are exactly `0 … numgroups−1`, and the tail scalar
`numvisentries` equals its distinct count. `decode_static_lod` returns both and
warns on a mismatch.

---

## System B — the mesh-list LOD chain

Here the coarser levels are extra `CGRenderParams` covering later slices of the
**same** index buffer of **one** mesh. One shipped mesh-list, mesh 0, over a single
34,518-index buffer:

| draw | index range | tris | `lodprimsetidx` | `lodchildrencount` | level |
|---|---|---:|---|---|---|
| rp0 | [0, 17262) | 5,754 | `0xFFFFFFFF` | 2 → children `[1, 2]` | **0** |
| rp1 | [17262, 28824) | 3,854 | 0 | 0 | **1** |
| rp2 | [28824, 34518) | 1,898 | 0 | 0 | **2** |

* **`lodchildrencount != 0` is the only reliable root predicate.**
  `lodchildrenstart` is a **running cursor** that stays non-zero on children too
  (corpus-wide: 142 draws carry a non-zero start, only 51 are roots), and
  `lodprimsetidx != 0xFFFFFFFF` marks **children**, not parents. Before 0.2.0
  `is_lod_parent` OR-ed all three and so called every child a parent.
* `lodchildindices[start : start+count]` are **mesh-local** renderparam indices
  (relative to `CGMeshData.renderparamidx`), in level order.

Corpus scale, over 102 archives · 1,240 mesh-lists · 9,328 renderparams: **78
`lodchildindices` entries in 11 mesh-lists, 78 child draws, 51 root draws.** And
`lodchildindices.count == 0` in all 62 mesh-lists inlined into a scatter master —
system B never appears inside a scatter master.

---

## Caveats — read before quoting a number

### Triangle counts are *usually*, not *always*, non-increasing

**"Total triangles never increase with level" is a strong tendency, not an
invariant.** Widened from two masters to all 62: **196 of 72,004 multi-level groups
increase at some level, on 21 of 62 masters**; 32 groups exceed their own LOD 0;
197 groups increase in *instance count*. It really is 0 exceptions on the two
masters it was first derived from (0/4,921 and 0/2,065), which is exactly why it
read as absolute.

The dominant shape is a coarsest level that **merges the parts**:

| group | triangles by level | instances by level |
|---|---|---|
| A | 3,899 → 3,179 → 2,740 → **2,899** | 7 · 7 · 7 · **1** |
| B | 4,854 → 2,702 → 1,672 → **1,674** | 1 · 1 · 1 · **6** |
| C | 10 → **30** → 6 | 1 · 1 · 1 |

Group A is the clean case: L2 is 7 meshes / 2,740 triangles, L3 is **one** mesh of
2,899. The LOD saves **draw calls**, not triangles. The same violating sequences
recur verbatim across archives, i.e. a handful of shared props rather than
scattered noise.

Do not build a validator that asserts monotonicity.

### `nodelookup` is not always monotonic

It is monotonic non-decreasing on **61 of 62** masters. One stores
`nodelookup[0:12] = 0,1,2,3,4,5,6,7,8,9,10,9` — node 9 owns LOD entries 9 **and** 11
with node 10's entry between them. The descent sits inside the `hierlods` parent
region and no instance references it, so no derived level is affected — but a
walker that *asserts* monotonicity will refuse a shipped master.

### `lodfadeslopeoffs` semantics are unresolved

Its four floats per LOD entry are carried through verbatim as audit metadata.
**They are not switch distances and must not be presented as such.**

What *is* pinned, from disassembling the shipped instanced vertex/pixel shader
pair: the vertex shader reads the packed instance record's `lodfadeidx` (the same
value as `lodfadelookup[i]`), indexes a per-view `Buffer<float4>` of fade amounts at
`view*stride + lodfadeidx`, takes **only `.x`**, and passes it to the pixel shader,
which turns it into an **alpha-to-coverage dither mask** (this is also the consumer
of `ditherfadeflags`). So what the GPU consumes per LOD entry is a per-frame,
CPU-computed **scalar in [0,1]**; `lodfadeslopeoffs` is that scalar's CPU-side input
and never reaches the GPU as a vec4.

Ruled out by measurement, so nobody has to re-run these:

* **Not** two `(slope, offset)` ramps in either pairing — every candidate pairing
  puts the ramp's zero crossing at a *negative* distance.
* **Not** a partition of unity under `saturate(a·d+b)`, `saturate(min(…))` or
  `saturate(a·d+b)·saturate(c·d+e)`, for `d` or `d²`: only 1,887 of 4,940 groups fit,
  and those are degenerate all-0 / all-1 rows.
* **Not** a sliding window over one per-group threshold list: `fades[i][1:] ==
  fades[i+1][:3]` on **0 of 15,319** row pairs.
* Rows are **not** consistently ascending (6,649 of 15,320), so it is not simply
  `{nearfadestart, nearfadeend, farfadestart, farfadeend}`.

The hard constraint any future model must explain: every row is **all-positive or
all-negative** — only 59 mixed-sign rows in 262,132 corpus-wide — and the
positive/negative split is within ±1 of exactly half on 62/62 masters (exactly half
on 38). `(±100000, ±100000, ±100000, ±100000)` is by far the most common row, and
`100000` is this engine's "never" LOD sentinel elsewhere.

### `nodes[i].w`

`w == 0.0` in every row of every one of the 62 masters. Whether it is padding or an
unused radius is **undetermined**; nothing on disk distinguishes them.

---

## Using it

### In Blender

Both import operators expose a **LOD Level** dropdown, defaulting to *LOD 0
(highest detail)*:

* `.lescatter` — `LOD 0 … LOD 4`, `Coarsest`, `All levels (stacked)`.
* `.lemesh` — `LOD 0 … LOD 3`, `All levels (stacked)`.

Placed scatter objects are tagged with `le_lod_group`, `le_lod_level` and
`le_lod_group_levels` custom properties; imported `.lemesh` objects get
`le_lod_level` and `le_lod_levels`.

### From the render harness

```bash
blender --background --python blender_tool/tests/blender_scatter_render.py -- \
    pkg=<path>.lescatter out=render.png lod=0
```

`lod=-1` places every level (the pre-0.2.0 behaviour); `lod=-2` places each prop's
coarsest level.

### Corpus audits

Two read-only audit tools re-derive the numbers above against your own game data.
Both need Windows Python and the Oodle runtime, load one archive at a time, and are
deliberately not named `test_*.py` so the unit suite never imports them:

```
python.exe blender_tool/tests/audit_lod_fields.py           # system B, whole corpus
python.exe blender_tool/tests/audit_static_lod_corpus.py    # system A, 62 masters
```

---

## What is not here

The **dynamic**-instance LOD system (`SGDynamicInstanceLODData`) is decoded but its
decoder is not part of this repository, because nothing in the `.lemesh` /
`.lescatter` pipeline consumes it. For the record, so it need not be re-derived:
`SGDynamicInstancesData` walks forward from byte 0 as `meshlist` (inline
`CGMeshListData`), `materials : CTable<CSymbol64>`, `shadersets : CTable<CSymbol64>`,
`lods : CTable<SGDynamicInstanceLODData>` (20 B each), `lightmapsid : CSymbol64`, and
must consume the blob exactly. Measured over 360 populated resources / 667 LOD
records with zero decode failures:

* `[meshindex, meshindex+nummeshes)` tiles the inline mesh-list exactly, 360/360.
* `nearfadestart ≤ nearfadeend ≤ farfadestart ≤ farfadeend`, 667/667.
* `nearfadestart == nearfadeend` in 667/667 and `farfadestart == farfadeend` in
  666/667 — so in retail this is a **hard switch**; the cross-fade band the struct
  can express is authored to zero width.
* The floats are plain world-space distances (5, 6, 10, 15, 22, 33, 64, 75, 100,
  200 …) with **100000 as the "never" sentinel**.
