# Characters

How a Lone Echo character is put together on disk, and what the importer does
with it. Everything here is measured on shipped bytes; where a claim is inferred
rather than measured, it says so.

## 1. A character is not a mesh-list

A prop is a mesh-list. A **character is an actor node plus NAMED components**,
and the mesh-list is only one of those components. Reading a character as "the
mesh-list at this hash" gets you a body part, not a body.

The assembly lives in `le_mesh/attach.py`:

- the **actor node** owns a component list, each entry naming a component by
  symbol;
- a component may be a model, a skeleton, a scene-set controller
  (`ComponentLOD`), or a transform;
- an attach relationship carries `attachmode`, `transformname`, `attachmodel`
  and `attachname`.

⛔ **On the shipped rigs inspected, every one of those attach fields is null and
`attachmode` is 0.** The components are placed by their own transforms, not by a
stored joint attachment. `attach.py` therefore reads the attach fields, reports
them, and does **not** invent a joint attachment when they are empty — the
identity transform is the measured answer, not a fallback.

## 2. Three LOD systems, not one

This is the part that has bitten hardest. Lone Echo has **three independent**
level-of-detail mechanisms, and they are not variants of each other.

| system | where it lives | what a "level" is |
| --- | --- | --- |
| **Static-instance LOD** | `SGStaticInstanceLODData` | a *different mesh* with its own instances |
| **Mesh-list LOD chain** | `CGRenderParams.lodchildrenstart/count` + `CGMeshListData.lodchildindices` | an *index range* over the same buffers |
| **Scene-set mask** | `SSceneSetMask` per draw + the model's `CGSceneSetsData` | a *bit* in a per-draw mask |

The first two are documented in [LOD.md](LOD.md). The third is the character one
and is new in 0.4.0.

### 2.1 The scene-set mask is not always an LOD ladder

The obvious reading — "bit N == LOD level N" — is **false on 4 of 12 roster
mesh-lists**. On those four the bits partition the body in **space**: one set is
the torso, another the arms, another the hands. Selecting "level 2" under the
LOD reading deletes a character's left arm and both hands, silently, with a
perfectly normal-looking import.

### 2.2 The refusal heuristic

`le_mesh/static_lod.py` therefore asks whether the sets form a **geometric
chain** — successively coarser, each a subset relationship consistent with a
ladder — before treating them as one. When they are not a chain it **draws
everything**.

That asymmetry is deliberate:

- over-draw is **visible and reversible** — you see too much and can hide it;
- a missing limb is **silent** — nothing in the scene says a hand is gone.

### 2.3 A ladder with a hole — fixed

When the sets *are* a chain but the levels are **sparse** — say {0, 3} — asking
for level 1 or 2 used to match no set and import **nothing at all**: no object,
no distinguishable warning. `2fd6839161785e9c_ff91757c910ea7b6` (Liv's body) is
exactly that shape, six meshes over levels {0, 3}, and levels 1 and 2 made the
entire character disappear.

`package_reader.snap_to_ladder` is the rule, in one expression shared by
`select_lod_objects` and `select_lod_draws`: **snap DOWN to the greatest present
rung `<= level`, and snap UP to the finest rung only when the request is below
the whole ladder.** Liv's body now selects **5 of 6** meshes at levels 1–2, was
**0 of 6**.

⚠ Snapping *down* is the same asymmetry §2.2 turns on. Snapping up would answer
"level 1" with rung 3 — a *coarser* model than was asked for, which no ladder
semantics produces — and drawing everything would stack all six of Liv's meshes,
re-creating the very proxy defect §2.4 exists to prevent. See
[TESTING.md](TESTING.md) §3.1.

### 2.4 Variant draws

Two draws with a **byte-identical index range** but different scene-set bits are
the same geometry authored into two sets. Emitting both puts tens of thousands
of co-planar duplicate triangles on a model — z-fighting that looks like a
normal or shading bug. The selector now emits one.

## 3. Skinning

Stride-52 vertex formats **are** skinned; the joint indices and weights are
present and decode normally. Rigs of 188 and 219 joints are the two common
shapes in the roster and are confirmed at rig level by their own shaders.

A 520-joint rig exists in the corpus and is **not** understood; the importer
decodes its vertices and skips its skeleton rather than guessing.

### 3.1 Characters ship a duplicated back-face shell

★ New in 0.4.0, and it is why the tangent stream had to be read rather than
recomputed. `tangent.w` takes four values, and its **magnitude** tags a second,
position-identical copy of a mesh: over 5 character packages / 63 objects
carrying a 4-component tangent, **109,400 of 109,400** `|w| = 0.5` vertices have
a `|w| = 1.0` partner at the same position, with an exactly negated **normal**
on 99.92 % of pairs — but a negated **tangent** on only **65.67 %**. The back
shell carries its own frame; it is not a sign flip of the front one. 26 objects
carry both magnitudes, 37 carry only `|w| = 1.0`, and **0 carry only 0.5** —
there is never a back shell without a front.

⚠ The buffer order is **not** part of the rule: 25 of the 26 lay it out
fronts-then-backs and one interleaves them. Read the tag, never the index.

⛔ Both shells are currently **drawn**, and nothing has yet decided whether they
should be. See [MATERIALS.md](MATERIALS.md) for how the shipped basis reaches the
shader.

## 4. Materials on characters

Characters use the same material path as everything else (see
[MATERIALS.md](MATERIALS.md)), with two shapes worth knowing:

- **`eMTSkirt` is a decal sheet.** Its cut-out alpha is load-bearing; discarding
  it fills in holes that are meant to be holes. It is the one material type whose
  resolved render mode may differ from the mode stored in the manifest.
- **`roughness == 0` is not a mirror.** Blender treats roughness 0 as a perfect
  mirror; the engine does not. Characters carry a lot of `roughness == 0`
  (measured 42–100 % of one rig's materials versus ~0 % of another's), so a naive
  import makes one character look chrome-plated and leaves the other alone.

## 5. What is not imported

- Facial rigs, blend shapes and any animation data.
- The 520-joint rig's skeleton.
- Cloth or hair simulation state.
