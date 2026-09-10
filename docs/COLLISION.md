# Collision — full-detail, multi-body, no decimation

How Echo VR's player collision is actually stored, and how to author a whole map's
worth of it without throwing geometry away.

Every number here is **measured** — from 3,808 collision bodies across 1,675
shipped `CPhysicsResource` files in `H:\pcvr-extracted`, and from the working
`mpl_reactor.kfclvl` build. Where something is inferred rather than measured it
says so.

The decoder/encoder these facts come from is `scripts/evr_geo_move.py`, which
moves collision with geometry and therefore has to agree with the format
exactly.

---

## 1. The three rules

1. **Never decimate.** The format has no size pressure that decimation relieves.
   The one hard ceiling is *per body*, and you get as many bodies as you want.
2. **Split at the edge ceiling: 32,767 edges per body.** That is ~21,000
   triangles. Fill a body to the budget, start another.
3. **Collision is single-sided.** One triangle per surface, wound outward. Do not
   emit back-faces.

Everything below is why.

---

## 2. The hard ceiling

**32,767 edges per body. Never exceeded, in any shipped body.**

```
scanned 1,675 level physics resources, 3,808 bodies
  MAX EDGES per body : 32,767      <- 2^15 - 1
  MAX VERTS per body : 13,317
  MAX TRIS  per body : 21,451
  bodies over 32,767 edges: NONE
```

`32767 = 2^15 - 1`, i.e. a **signed 16-bit** index. The triangle record stores its
three edge references as `u32` (measured, see §5), so the ceiling is *not* imposed
by that field — it comes from something narrower inside the engine's mid-phase.
**The mechanism is inferred; the ceiling is measured**, and 3,808 bodies is enough
evidence to build against.

Vertices and triangles have **no** comparable ceiling — 13,317 and 21,451 are just
the largest that happened to fit under the edge budget, not round numbers.

### The distribution proves the strategy

```
edge count per body        bodies
      0 -  4,095            3,751     <- props, doors, small dynamic bodies
  4,096 - 28,671               26
 28,672 - 32,767              31      <- level geometry, packed to the ceiling
```

Bimodal, with nothing between. Level geometry is not "as big as it happened to
be" — it is **packed against the ceiling and then cut**.

## 3. The planning budget

Across the 174 bodies with more than 500 triangles:

| ratio | median | mean | range |
|---|---|---|---|
| edges / triangles | **1.558** | 1.570 | 1.488 – 2.000 |
| verts / triangles | 0.570 | 0.585 | — |

So:

```
32,767 edges / 1.558 edges-per-triangle  =  ~21,000 triangles per body
```

Confirmed against the 30 bodies already at the ceiling: **19,878 – 21,451
triangles**, median 20,810.

> **Budget 20,000 triangles per body and you will never hit the wall.** Push to
> 21,000 only if you compute real edge counts as you pack.

The ratio rises toward 2.0 for open sheets (every triangle contributing its own
edges) and falls toward 1.5 for closed manifolds where edges are shared. If you
are packing thin, unwelded, sheet-like geometry, use the pessimistic 2.0 and
budget ~16,000 triangles.

## 4. How the split is actually made — *not* spatially

This is the part that is easy to get wrong by assuming.

**Bodies are not spatial chunks.** Measured on both a stock level and a working
custom one, every pair of body AABBs overlaps:

```
mpl_arena_a (stock)   9 bodies   AABB overlaps: 36 of 36 pairs
mpl_reactor (custom)  8 bodies   AABB overlaps: 28 of 28 pairs
```

**Bodies are not single connected shells either.** A body is a bag of whatever
components fell into it:

```
mpl_arena_a  body 0:  21,451 tris,  297 welded components
mpl_reactor  body 0:  20,169 tris,  181 welded components
mpl_reactor  body 1:  19,914 tris,  366 welded components
```

So the packer does not need a BVH, an octree, or a spatial sort. **Emit geometry
in whatever order you have it, count edges as you go, and cut a new body when the
next component would cross 32,767.** That is what the shipped data looks like and
it is what a rebuild should do.

Keep whole **components** together in one body, though — see §6.

## 5. On-disk layout

`CPhysicsResource`, type hash `b7d338793fa37832`.

```
+32   u32   body count
+36         first body
```

Bodies are **walked, not indexed** — each body's length is derived from its own
array counts, so you must parse body *n* to find body *n+1*. A body:

```
+0    u64   1                (geo-body marker)
+8          SPhGeoFull header, 648 bytes
+656        the arrays, back to back, in declaration order
            then an 880-byte tail
```

Each array's element count lives at a fixed offset in the header; the data
follows the header contiguously in `ARRAY_ORDER`. The ones that matter:

| header offset | stride | what |
|---|---|---|
| `+8` | 12 | **vertices**, `float32[3]` |
| `+24` | 52 | **triangles** |
| `+40` | 48 | **edges** |
| `+176` | 84 | mid-phase kDOP tree nodes |
| `+208` | 12 | rest-verts (`v - centroid`); empty on level bodies |
| `+400` | 12 | unit direction table (see §7) |

### The triangle record (52 B, 13 × u32) — measured

```
+0  +4  +8    vertex indices
+12 +16 +20   adjacent triangle indices (0xFFFFFFFF = boundary)
+24 +28 +32   EDGE indices
+36 +40       0xFFFFFFFF
+44 +48       0
```

No plane equation and no normal — nothing in the triangle record is invalidated
by moving the mesh.

### The edge record (48 B) — measured, and the one that bites

```
+0   u32   va, vb, oppA, oppB, triA, triB, facecount
+28  f32   LENGTH          == |v[vb] - v[va]|
+32  f32   DIHEDRAL        == pi - acos(nA . nB)   (+inf on a boundary edge)
+36  f32   OPP SEPARATION  == |v[oppB] - v[oppA]|
+40  u32   slotA, slotB
```

Those three floats are **precomputed and never recomputed at load**. Verified
against the stock arena's body 0: 32,765 / 32,765 lengths, 31,588 / 31,588
dihedrals and opposite-separations exact, with `+inf` on all 1,177 boundary
edges. If you author or move geometry and leave them stale, the surface behaves
as its old shape.

`+inf` at `+32` is a **sentinel for a one-faced boundary edge**, not a stale
angle. Preserve it; do not "fix" it to a number.

### The mid-phase tree (84 B/node)

Three packed `(count << 16 | start)` range words over verts / tris / edges, then
18 floats: `min[9]` then `max[9]` of the projections onto the nine axes
`x, y, z, x+y, x+z, y+z, x-y, x-z, y-z`.

Recomputing those from `verts[start : start+count]` reproduces **699 of 699**
stock arena nodes to within 4e-6 (float32 rounding). **The engine reads this tree
verbatim and never rebuilds it** — a stale node makes the body sleep where the
geometry used to be.

### Header sentinels — do not "fill these in"

| field | on level bodies | meaning |
|---|---|---|
| `+76` kDOP (18 f32) | ±inf | not computed |
| `+152` radius | `0.0` | not computed — arena body 0 reads 0.0 against a real 78.7 m extent |
| `+156` centroid | `0,0,0` | not computed |

These are **sentinels the shipped level bodies actually carry**. Writing a real
value where the game ships a sentinel is inventing a bound the engine never had.
If you edit an existing body, shift the centroid by the measured change and only
ever *grow* a radius that was already non-zero.

## 6. Single-sided, and what that means for authoring

Measured, zero exceptions:

```
mpl_reactor  body 0:  20,169 tris,  0 with an opposite-wound twin
mpl_arena_a  body 0:  21,451 tris,  0 with an opposite-wound twin
```

One triangle per surface. A wall you can touch from both sides is still **one**
triangle — the engine does not need a back-face, and emitting one doubles your
edge count for nothing, which is exactly the budget you cannot afford.

Practical consequences when building from a render mesh:

* **Weld first** (1e-3 is what the decoder uses to find components). Unwelded
  duplicate vertices inflate the edge count without adding surface.
* **Drop degenerates** before counting — a zero-area triangle still costs edges.
* **Keep a component whole in one body.** The edge record stores a precomputed
  LENGTH; splitting a component across two bodies means an edge whose endpoints
  live in different vertex arrays, which the format cannot express.

## 7. The `+400` direction table

A small per-body table of **unit face normals** — 52 entries / 18 distinct on
arena body 0, 24 / 6 on body 8. It is invariant under translation but **must be
turned with the geometry under a rotate or a non-uniform scale**; leaving it
stale is why rotating a model used to leave its collision pointing the old way.

Nothing indexes it per primitive, so entries are attributed by direction: turn
one only when a triangle the edit moved produces it and no triangle left behind
does. `evr_geo_move._turn_plane_dirs` implements that. It is also not a faithful
face list — 6 of arena body 0's 17 directions match no triangle in the body — so
it cannot be regenerated from scratch.

## 8. Case study — `mpl_reactor.kfclvl`

A working custom build (level `0x577dc4fc4f9b8c58`, gametype hint `Social_2.0`,
341 resources, 567 MB overlay). Its `CPhysicsResource` is **39,073,056 bytes**,
2.8× the stock arena's 13,962,648.

```
body   verts     tris    edges   tree   extent (m)
   0   12,794   20,169   32,767   250   21.8 x  6.9 x 22.1
   1   13,151   19,914   32,762   469   21.8 x  6.9 x 21.4
   2   13,612   19,668   32,760   559   25.7 x 10.2 x 25.4
   3   14,168   19,176   32,746   596   25.7 x  4.5 x 25.7
   4   13,961   19,856   32,755   920   16.6 x  6.7 x 16.8
   5   13,013   20,363   32,761   588   21.1 x  6.5 x 21.1
   6   13,447   19,962   32,755   680   17.7 x  7.5 x 19.1
   7    4,990    7,555   12,516    95   20.6 x 10.7 x 13.8
TOTAL  99,136  146,663  241,822
```

**Seven of eight bodies sit within 21 edges of the ceiling; body 0 is exactly
32,767.** Body 7 is the remainder. That is textbook bin-packing against the
budget, and it is why this map has full-detail collision — 146,663 triangles in a
~26 × 11 × 26 m space, 1.6× the whole arena's collision in 1/40th the volume.

It matches the stock envelope exactly: every body's triangle count (19,176 –
20,363) sits inside the 19,878 – 21,451 band the shipped ceiling-bodies occupy.

### Against the shipped levels

| level | bodies | verts | tris | edges | largest body |
|---|---|---|---|---|---|
| `mpl_arena_a` | 9 | 53,545 | 91,656 | 144,130 | 32,765 |
| `mpl_lobby_b2` | 6 | 51,735 | 90,688 | 141,261 | 32,764 |
| `mpl_combat_dyson` | 5 | 39,884 | 65,112 | 103,493 | **32,767** |
| `mpl_combat_combustion` | 4 | 38,147 | 60,056 | 96,845 | **32,767** |
| `mpl_combat_war_room` | 4 | 27,242 | 45,692 | 71,727 | 32,765 |
| **`mpl_reactor`** | **8** | **99,136** | **146,663** | **241,822** | **32,767** |

Combustion and dyson hit the ceiling *exactly*, same as reactor body 0. The
shipped game and this custom build use the identical strategy; reactor simply
needs more bodies because it keeps more geometry.

## 9. Recipe for rebuilding a whole map's collision

1. **Take the render mesh at full detail.** No decimation, no convex hulls, no
   proxy shapes.
2. **Weld at 1e-3**, drop degenerate triangles, and **discard back-faces** —
   collision is single-sided.
3. **Find connected components** (union-find over welded triangle corners). The
   component is the atom: it must not be split across bodies.
4. **Pack components into bodies**, accumulating a real edge count. Start a new
   body when the next component would cross **32,767** edges. Budget 20,000
   triangles per body if you are estimating rather than counting.
   *Order does not matter and the result need not be spatially coherent — the
   shipped data is not.*
5. **Per body, build**: vertices → triangles (3 verts, 3 adjacent tris, 3 edges)
   → edges with the three derived floats (`+inf` dihedral on boundary edges) →
   the kDOP tree, computed from each node's own vertex span over the nine axes.
6. **Leave the header sentinels alone** — ±inf kDOP, radius 0, centroid 0 are
   what shipped level bodies carry.
7. **Emit bodies back to back** after the `u32` count at `+32`; there is no
   offset table, so the walk has to work.

## 10. Traps

| trap | symptom |
|---|---|
| decimating to "fit" | nothing is fitting — the ceiling is per body, and bodies are free |
| splitting a component across bodies | an edge whose endpoints are in two vertex arrays; unrepresentable |
| emitting back-faces | edge count doubles, so you hit 32,767 at half the real geometry |
| stale edge floats after moving geometry | surface behaves as its old shape |
| "fixing" a `+inf` dihedral | it is the boundary-edge sentinel, not a stale angle |
| rebuilding the kDOP tree lazily | the engine never rebuilds it; the body sleeps where the geometry used to be |
| filling in radius / centroid / kDOP | those are "not computed" sentinels on level bodies |
| assuming a spatial split | shipped bodies all overlap; do not build an octree you do not need |

---

## Provenance

* Ceilings, ratios and distributions: 3,808 bodies over 1,675 shipped
  `CPhysicsResource` files, scanned 2026-09-09.
* Format, derived-float and kDOP verification: `scripts/evr_geo_move.py`, which
  reproduces 699/699 stock tree nodes and all three edge floats exactly.
* Case study: `C:\Oculus\...\bin\win10\maps\mpl_reactor.kfclvl`, a working build.
* Reverse-engineering context (channels, masks, physics materials, the goal as a
  trigger volume): `docs/reference/map-editor/_verify/COLLISION_RE/`.
