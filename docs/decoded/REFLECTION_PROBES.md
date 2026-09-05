# Reflection probes — `CGReflectionProbeResourceWin10`

**Solved.** This is the single largest thing in an Echo VR level: **189.6 MB of
baked HDR cubemaps across 379 probes**, more than every other level resource
combined. It shipped as one of the `no_decoder` rows in
[ALL_TYPES.md](ALL_TYPES.md); it is now decoded end to end, metadata and GPU
payload, with **zero residual bytes on all 32 archives**.

Two entries per level, keyed by the level hash:

| Type | Hash | Holds |
|---|---|---|
| `CGReflectionProbeResourceWin10` | `0x2829C885034AFCDE` | `SReflectionProbeMetaData` — where the probes are, which one a surface uses |
| `CGReflectionProbeResourceWin10GPU` | `0x5004F0B9F6645271` | the cubemaps themselves — BC6H_UF16, 6 faces, 9 mips |

## The headline

**Echo VR's probe resource is Lone Echo's, unchanged.** The
`CGReflectionProbeResourceWin7` grammar already decoded in
[../LIGHTING.md](../LIGHTING.md#reflection-probes) parses every Win10 file
byte-exactly — same six tables, same offsets, same record strides. Nothing in
this format was re-authored between the two titles.

What *did* change is the content, and it changed in one direction: **Echo VR is
rigidly uniform where Lone Echo was variable.**

| | Lone Echo (Win7) | Echo VR (Win10) |
|---|---|---|
| Cube size | varies per probe | **256², 9 mips, always** — 379 of 379 |
| Bytes per cube | varies | **524,448, always** |
| `gpuoffsets` | genuine offsets | `i × 524448` on 25 of 25 — fully derivable |
| `normalizations` | per-mip values | **zero on 378 of 379 probes** |
| `spheres` | 0 shipped | 0 shipped |

That uniformity is the practical finding: an Echo VR probe slice is just
`n_probes` fixed-size cubes end to end, and the offset table that indexes them
carries no information you cannot recompute.

## On-disk grammar

The header is a fixed 344 bytes — six `CTableA<T,0>` memory images of `0x38`,
then two `u32`. Payloads follow in declaration order, no padding, no alignment.

```
+0x000  CTableA<SGProbeBox,0>          boxes           selection volumes (optional)
+0x038  CTableA<SGProbeSphere,0>       spheres         0 shipped anywhere
+0x070  CTableA<SGProbePoint,0>        points          ONE PER PROBE - this is the probe count
+0x0a8  CTableA<unsigned int,0>        mipcounts       one per probe; 9 on every probe shipped
+0x0e0  CTableA<SGProbeBoundingBox,0>  boundingboxes   one per probe
+0x118  CTableA<unsigned int,0>        gpuoffsets      one per probe
+0x150  u32                            gpumemsize      == the GPU entry's byte length, exactly
+0x154  u32                            textureformat   59 = eBC6UeFLOAT on all 32
+0x158  payloads, declaration order
```

Each `CTableA<T,0>` image:

```
+0x00  u64 ptr          0 on disk - unpatched until CResource::ePointersPatched
+0x08  u64 nbytes       payload size in BYTES
+0x10  u64              0
+0x18  u64 expand       32
+0x20  u64 iallocated
+0x28  u64 iused        the element count
+0x30  u32 flags / u32 pad
```

Element strides are **measured** as `nbytes // iused`, never assumed:
`boxes` 56, `points` 16, `mipcounts` 4, `boundingboxes` 152, `gpuoffsets` 4 —
agreeing in every level that ships each table.

⛔ **`boxes` is not the probe count**, and the envelope-shaped `count` a generic
component-record reader picks up at `+0x28` *is* `boxes.iused` — which is why
this type sat mis-shaped as a pooled record in ALL_TYPES.md. `mpl_combat_dyson`
reads 17 there and has **29 probes**; ten populated levels ship **zero** boxes.
The probe count is `points.iused`.

### Records

`SGProbeBox` — stride `0x38`. A selection volume, not a probe.

```
+0x00  CQuaternion invrot     identity on only 40% of boxes - Echo VR does rotate them
+0x10  C3Vector    pos        world centre
+0x1c  CBox        min / max  local, symmetric about pos (min == -max on 116/116)
+0x34  u32         probeidx
```

`SGProbePoint` — stride `0x10`: `C3Vector point` + `u32 probeidx`, and
`probeidx == row index` on all 379.

`SGProbeBoundingBox` — stride `0x98`.

```
+0x00  C33Matrix rotation        orthonormal to 2.7e-07; ONE shared rotation in 23 of 25 levels
+0x24  C3Vector  probepos        == points[i].point EXACTLY (max delta 0 across the corpus)
+0x30  C3Vector  min             OBB corner, in the probe's own rotated, probe-relative frame
+0x3c  C3Vector  max
+0x48  float[20] normalizations  ZERO on 378 of 379 probes
```

`min + R·probepos` is **constant across probes** in 19 of 25 levels: the OBB is
a single world-space box that every probe re-expresses in its own frame, not 379
separate boxes. Its extent is the level — `mpl_arena_a` reads `20 × 36 × 160`
with a 90° rotation, the Echo Arena playfield.

The `normalizations` array is effectively dead in Echo VR. The one exception is
`mpl_combat_fission` probe 0, carrying a decaying sequence
(`0.0248, 0.0248, 0.0026, 0.0013, 0.0009, …`) consistent with a per-mip
normalization the other 378 bakes simply do not populate.

## The GPU payload

`gpumemsize == len(the paired ...Win10GPU entry)` on **32 of 32** archives — the
pairing is exact, not approximate.

Each cube is **face-major with a complete mip chain per face**:

```
face f, mip m  at  f * 87408 + sum(CHAIN[0..m))

CHAIN = 65536, 16384, 4096, 1024, 256, 64, 16, 16, 16
        sum = 87408 per face    x6 = 524448 per cube
```

Note the tail: mips 6, 7 and 8 are all **16 bytes**, because a BC6H mip never
drops below one 4×4 block. A naive `(dim/4)²` chain under-counts by 32 bytes per
face and will not close on the file.

Ordering was decided against the alternative (mip-major: all six faces of mip 0,
then all six of mip 1) by correlating each mip against a box-downsample of its
predecessor, over **all 379 probes × 6 faces × 3 mip pairs**:

| Hypothesis | r | fraction r > 0.9 |
|---|--:|--:|
| **face-major** | **+0.8375** | **0.375** |
| mip-major | +0.1569 | 0.016 |

The chain is **not** a box mip chain. Correlation climbs monotonically with mip
level (0.80 → 0.96), and each level needs a further ~3–4 texel low-pass on top of
the downsample to match — the signature of a **roughness-prefiltered radiance
chain**, one mip per roughness step, which is what a specular IBL cube is for.
8-bit decode clipping is not the cause: only 0.7% of mip-0 texels clip.

## Which probe a surface uses

`CGMeshData.probeidx` lives at struct `+0x50`, and Echo VR's mesh record is a
24-byte cook identity prefix followed by the 128-byte `CGMeshData` — so it reads
at **record `+0x68`** in `CGMeshListResourceWin10`. Confirmed semantically, not
by range: across 1,580 meshes in 25 levels, the value at `+0x68`

* is in `[0, n_probes)` or `0xFFFFFFFF` — with **zero** out-of-range values;
* names the **nearest** probe point 1,529 times (96.8%), mean rank **0.10**;
* is explained by a containing `SGProbeBox` in 18 of the remaining 51.

Rival offsets sit at chance: `+0x84`, `+0x88` and `+0x38` name the nearest probe
12–16% of the time, mean rank ~11.

So selection is **nearest-point by default, box override where authored** —
97.9% of meshes accounted for. The boxes are a real override and not redundant:
a box's own centre picks a different probe than nearest-point on 22% of boxes,
and the probe a box names sits *outside* that box 64 of 116 times.

The 2.1% residual is unexplained. The bake evidently keys off something other
than a bounding centre for those; the AABB centre and the bounding-sphere centre
(`+0x54`) give byte-identical results, so it is not the choice of centre.

⚠ 23 meshes across the corpus sit **exactly** equidistant from two probes. Any
audit of this field must tie-break stably, or it will disagree with itself by
~1.5% between runs.

## Per level

| Level | metadata | probes | boxes | GPU cube slice |
|---|--:|--:|--:|--:|
| `mpl_lobby_b_combat` | 16968 | 90 | 14 | 45.01 MB |
| `mpl_combat_dyson` | 6400 | 29 | 17 | 14.50 MB |
| `mpl_tutorial_lobby` | 4848 | 24 | 5 | 12.00 MB |
| `mpl_lobby_b2` | 4848 | 24 | 5 | 12.00 MB |
| `mpl_combat_combustion` | 4504 | 23 | 2 | 11.50 MB |
| `mpl_tutorial_arena` | 4384 | 22 | 3 | 11.00 MB |
| `mpl_arena_a` | 4384 | 22 | 3 | 11.00 MB |
| `mpl_lobby_b_arena` | 4720 | 22 | 9 | 11.00 MB |
| `mpl_combat_fission_climax` | 3920 | 20 | 1 | 10.00 MB |
| `mpl_combat_gauss_section02` | 3336 | 17 | 0 | 8.50 MB |
| `mpl_combat_fission_cargobay` | 3488 | 15 | 9 | 7.50 MB |
| `mpl_combat_gauss_section01b` | 2632 | 13 | 0 | 6.50 MB |
| `mpl_combat_fission_pantheon` | 3128 | 12 | 12 | 6.00 MB |
| `mpl_combat_gauss_section01a` | 1928 | 9 | 0 | 4.50 MB |
| `mpl_combat_gauss_section03` | 1576 | 7 | 0 | 3.50 MB |
| `mpl_combat_fission_prologue` | 1560 | 5 | 6 | 2.50 MB |
| `mpl_tutorial_boost` | 1048 | 4 | 0 | 2.00 MB |
| `8a1af9e108def0b` (unnamed) | 1048 | 4 | 0 | 2.00 MB |
| `mpl_combat_celebration_room_blue` | 1832 | 4 | 14 | 2.00 MB |
| `mpl_tutorial_movement` | 872 | 3 | 0 | 1.50 MB |
| `mpl_combat_celebration_room_orange` | 1096 | 3 | 4 | 1.50 MB |
| `mpl_tutorial_micro_thrusters` | 872 | 3 | 0 | 1.50 MB |
| `mpl_tutorial_air_brake` | 696 | 2 | 0 | 1.00 MB |
| `mpl_tutorial_hands` | 520 | 1 | 0 | 0.50 MB |
| `mpl_combat_fission` | 1192 | 1 | 12 | 0.50 MB |
| `r14_glb_global_mp` | 344 | 0 | 0 | -- |
| `r14_glb_global_root` | 344 | 0 | 0 | -- |
| `mpl_combat_gauss` | 344 | 0 | 0 | -- |
| `mnu_master_mp_ingame` | 344 | 0 | 0 | -- |
| `mpl_tutorial_master` | 344 | 0 | 0 | -- |
| `mnu_master` | 344 | 0 | 0 | -- |
| `r14_glb_global_tutorial` | 344 | 0 | 0 | -- |

Seven archives ship the empty 344-byte stub — the parents and menu roots, which
own no geometry.

`mpl_combat_gauss` is worth a note: the parent carries **no** probes while its
streamed sections carry 13, 9, 17 and 7. Probes belong to the archive that owns
the geometry, never to the parent. The same holds for `mpl_combat_fission`,
which keeps 1 probe and 12 selection boxes for itself and leaves the rest to its
four regions.

## Cross-checks against an independent decoder

`quest_combat_port` (a separate PC→Quest port effort in this tree) reverse-
engineered this type from `libr15.so` disassembly and explicitly records its
interior grammar as **OPAQUE** — "no record decoder, VERIFIED offsets only". Its
independently measured facts all land on the decode above:

* `@0x150 u32` = GPU byte size, `@0x154 u32` = format scalar 59 — matching
  `gpumemsize` and `textureformat`.
* "head `[0:0x150]` preserved" — exactly the six table images.
* "the trailing GPU byte-offset table, a `k × step` linear ramp" — `gpuoffsets`,
  with `step` = 524,448.
* Its low-spec decimation of `mpl_arena_a` moves "`+0x08` count 168 → 112".
  `+0x08` is `boxes.nbytes`; arena ships **3** boxes (3 × 56 = 168) and the
  decimated build ships **2** (112). Independent confirmation of both the field
  and the stride.
* Its low-spec GPU sizes divide by 524,448 exactly — arena 8,915,616 = 17 cubes,
  lobby 12,586,752 = 24 cubes.

It also reports that the Quest hop rewrites the format scalar 59 → **87**
(ASTC_8x8) and rescales every GPU byte value by exactly 152/607.

## Reproducing

```
python docs/decoded/tools/probe_dossier.py            # container, all 32 archives -> _probes.json
python docs/decoded/tools/probe_dossier.py --proofs   # cube ordering + the mesh binding
```

The first pass prints `no failures` when residual, count agreement, GPU pairing,
offset closure, cube geometry and the `probeidx == row` identity all hold. The
proofs pass needs `numpy` and `texture2ddecoder`.

## Still open

* **The specular sampler itself** — how the shader picks a mip from roughness,
  and whether `boundingboxes` drives a parallax correction on the cube lookup.
  The OBB is present and the geometry is right for box-projected reflections,
  but nothing here proves the shader uses it.
* **The 2.1% of meshes** whose `probeidx` is neither nearest-point nor
  box-covered.
* **`normalizations`** — the single populated instance is not enough to pin what
  the 20 floats meant, only that Echo VR stopped writing them.
