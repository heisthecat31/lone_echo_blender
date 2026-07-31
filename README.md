# Lone Echo Blender Importer

Import Lone Echo / NRadEngine meshes **and** whole scatter levels into Blender with
full vertex attributes, skeletons, per-draw materials, and **level-of-detail
selection**.

## Showcase

A whole static-scatter level, extracted to a portable package and imported into
Blender — 21,000+ placed instances over 1,000+ unique meshes, with per-material
base-color and normal maps:

![Textured scatter level in the Blender viewport](blender_tool/fixtures/scatter_station_front_textured.png)

The same level rendered through Blender's EEVEE engine, where the reconstructed
normal maps contribute surface detail:

![EEVEE PBR render of the scatter level](blender_tool/fixtures/scatter_station_front_eevee.png)

### LOD selection

Lone Echo stores every level of detail of a prop as a **separate mesh with its own
instances**. Importing all of them stacks every level on top of every other. Here is
the same level with all levels placed (left, 21,394 instances / 6.30 M triangles)
and at LOD 0 alone (right, 8,288 instances / 3.67 M triangles) — the silhouette is
identical, because **61.3 % of those instances were lower-LOD duplicates**:

| all levels stacked | LOD 0 (the default) |
| --- | --- |
| ![All LOD levels placed at once](blender_tool/fixtures/lodfull_all.png) | ![Only LOD 0 placed](blender_tool/fixtures/lodfull_l0.png) |

Both import operators expose a **LOD Level** option; see [docs/LOD.md](docs/LOD.md).

## Status — what works, and what does not

Be clear-eyed about this before you start: the value here is the tooling and the
dead ends already ruled out, not a claim that everything works.

| | state |
| --- | --- |
| Geometry, UVs, vertex colors, tangents, skin weights | **works** |
| Skeletons / armatures | **works** |
| Whole-level scatter placement (linked duplicates) | **works** |
| Multi-material meshes (one slot per draw) | **works** |
| **LOD selection**, both systems | **works** — new in 0.2.0 |
| Base color + normal maps (incl. BC5 normal reconstruction) | **works** |
| **Transparency and emission** | ⚠ **do not reach the renderer at all** |
| Baked lightmaps | not imported |
| Scene lights | **decoded, deliberately not imported** |

Two of those deserve to be spelled out.

**Materials are only partly wired.** Base colour and normal maps work. Transparency
and emission do **not**: an end-to-end audit found every exported `.lemesh` manifest
carries `"materials": []`, and there are **nine breaks** in the
decoder → manifest → builder → EEVEE chain. Two are fixed in 0.2.0; the other seven
are documented, with file-level detail, in [docs/MATERIALS.md](docs/MATERIALS.md).
Do not read "PBR" here as "full PBR".

**Lights are decoded but importing them naively is wrong.** Most Lone Echo level
lights are **specular-only** (of 118 decoded records only 49 carry `eEnableDiffuse`;
on one 47-light level only 15 do) and they sit on top of a **baked** lightmap this
tool does not yet import — 86 of the 87 lit shaders bind both paths. Blender has
neither, so importing them **double-lights the scene**. The decoder ships; a light
importer deliberately does not. See [docs/LIGHTING.md](docs/LIGHTING.md).

## How it works

The tool is split into two stages so that the part you install into Blender stays
tiny and portable, while the tricky data decode happens once, offline.

1. **Stage 1 — the extractor** reads *your own* copy of the game data and writes a
   self-describing, portable package:
   - a **`.lemesh`** package for a single mesh or model, or
   - a **`.lescatter`** package for a whole scatter level (every unique mesh plus
     every per-instance placement transform).

2. **Stage 2 — the Blender add-on** (`lone_echo_import`) imports those packages.
   It reads only the package — decoded geometry, materials, and transforms — so it
   never touches the game archives or any proprietary runtime.

**Why split it this way?** The add-on is pure Blender (only `bpy`, `mathutils`, and
the Python standard library), so it installs and runs anywhere Blender does, on any
platform, with nothing else to set up. All of the format-specific decoding — which
needs a Windows-only compression runtime — lives in the offline extractor and is
covered by an archive-free test suite. Once a package is written, it is a plain
folder of JSON and raw binary blobs that anyone can inspect, archive, or re-import.

## Requirements

**For the add-on (Stage 2):**

- Blender **4.1 or newer** (validated on 5.1).
- Nothing else. The add-on is self-contained — no extractor and no external
  packages are needed to import a `.lemesh` or `.lescatter` package.

**For the extractor (Stage 1):**

- **Windows Python** (`python.exe`). The compression runtime the game data uses is
  Windows-only, so extraction must run under Windows Python.
- Your own, legally obtained copy of the game data.
- Your own copy of the Oodle runtime DLL. It is **not** included in this repo.
- The **`pyoodle`** package (a separate, MIT-licensed sibling project). Either
  `pip install` it, or check it out next to this repository — the extractor finds a
  sibling checkout automatically.

## Install the add-on

Build the installable zip:

```bash
python3 blender_tool/build_addon_zip.py
```

This writes `blender_tool/dist/lone_echo_import-<version>.zip`, whose single
top-level folder is the `lone_echo_import/` add-on package.

Then, in Blender:

1. **Edit > Preferences > Add-ons > Install from Disk…**
2. Pick the zip you just built.
3. Enable **"Lone Echo Importer (.lemesh / .lescatter)"** in the add-on list.

That is all the add-on needs — no extractor, no `pyoodle`, no game data.

## Extract packages (Stage 1)

The extractor locates the game data and the Oodle runtime through environment
variables (set only what your setup needs):

| Variable | Meaning |
| --- | --- |
| `LONE_ECHO_DATA_ROOT` | Root of your extracted game-data tree. |
| `LONE_ECHO_OODLE_DLL` | Path to your own copy of the Oodle runtime DLL. |
| `PYOODLE_PATH` | Location of the `pyoodle` checkout (only if it is not `pip install`-ed). |

All extractor commands must run under **Windows Python** (`python.exe`).

### Extract a single mesh or model → `.lemesh`

```bat
python.exe blender_tool\extractor\le_extract.py ^
    --archive <hash> --mesh <hash> ^
    --out blender_tool\exports --textures --direct-materials
```

- `--textures` extracts the referenced textures alongside the package.
- `--direct-materials` resolves each material's textures directly from the data.
- `--all` extracts every mesh in the archive; `--list` prints the available meshes.

The package is written to `blender_tool\exports\<archive>_<mesh>.lemesh\`.

### Extract a whole scatter level → `.lescatter`

First write the geometry + placement package:

```bat
python.exe scripts\le_scene_extract.py <hash> ^
    --out blender_tool\exports\<hash>.lescatter
```

Then resolve and extract that level's materials and textures into sidecar files
(so the add-on can bind full PBR materials to the placed instances):

```bat
python.exe scripts\le_scene_materials.py <hash> ^
    --manifest     blender_tool\exports\<hash>.lescatter\manifest.json ^
    --out-textures blender_tool\exports\<hash>_textures ^
    --out-json     blender_tool\exports\<hash>_materials.json
```

Replace every `<hash>` with the identifier for the archive or level you own.

## Import in Blender (Stage 2)

With the add-on enabled, use **File > Import** and pick the package's
`manifest.json`:

### Lone Echo (.lemesh) — meshes and models

**File > Import > "Lone Echo (.lemesh)"**. Options:

- **LOD Level** — which level of a mesh's LOD chain to emit. Default **LOD 0
  (highest detail)**; `All levels (stacked)` reproduces the pre-0.2.0 behaviour.
  Clamped per mesh, and a no-op for the vast majority of mesh-lists, which carry no
  chain.
- **Import Materials** — build materials for each draw (base colour + normal; see
  [Status](#status--what-works-and-what-does-not)).
- **Include Shadow-Only Meshes** — also import meshes flagged shadow-only.
- **Flip UV V** — convert top-left UV origin to Blender's bottom-left.
- **Y-up to Z-up** — stand the model upright for Blender's Z-up world.
- **Import Armature** — build a skeleton from the package (when present) and skin
  the meshes to it.
- **Apply Scene Placement** — position the imported meshes at their level world
  transforms using an accompanying scene description.

### Lone Echo Scatter (.lescatter) — whole levels

**File > Import > "Lone Echo Scatter (.lescatter)"**. Options:

- **LOD Level** — which level of detail to place: `LOD 0 … LOD 4`, `Coarsest`, or
  `All levels (stacked)`. Default **LOD 0**. Every LOD level of a prop is a separate
  mesh with its own instances, so `All levels` places them on top of each other. A
  level is clamped per group, so props with fewer levels still contribute their
  coarsest one. See [docs/LOD.md](docs/LOD.md).
- **Flip UV V** — as above.
- **Y-up to Z-up** — apply the upright basis per instance (never baked into the
  shared meshes).
- **Include Proxy Meshes** — also build meshes flagged as collision/LOD proxies.
- **Max Instances** — cap how many instances are placed, for a fast first preview
  (0 = place all).

Each unique mesh is built once and shared across all of its instances as a linked
duplicate, so even tens of thousands of instances stay memory-light.

**Materials.** Meshes with more than one draw get **one material slot per draw**,
with each face assigned to its covering draw. Each material is a Principled BSDF
carrying **base colour plus normal**, with **BC5 normal reconstruction** wired into
the shader graph. ⚠ That is the whole of it — **transparency and emission are not
wired through**, and neither is the baked lightmap. See
[docs/MATERIALS.md](docs/MATERIALS.md) for exactly where the chain breaks.

**Rendering.** A headless render harness,
`blender_tool/tests/blender_scatter_render.py`, can render a package to an image
with either the Workbench engine or, with `engine=eevee`, an EEVEE render. Pass
`lod=N` to choose the LOD level (`-1` = every level stacked, `-2` = coarsest).

## Tests

Run the archive-free core test suite (no game data required):

```bash
python3 blender_tool/tests/run_tests.py
```

142 tests, none of which need game data, an archive, Oodle, or Blender.

Two read-only **corpus audits** re-derive the LOD findings against your own copy of
the game data, and one does the same for material transparency/emissive state. They
need Windows Python and the Oodle runtime, load one archive at a time, and are
deliberately not named `test_*.py` so the unit suite never imports them:

```bat
python.exe blender_tool\tests\audit_static_lod_corpus.py
python.exe blender_tool\tests\audit_lod_fields.py
python.exe blender_tool\tests\audit_material_modes.py --archive <hash>
```

Before publishing any change, run the scrub gate over the tracked tree:

```bash
python3 scripts/scrub_gate.py
```

## Documentation

| | |
| --- | --- |
| [docs/FORMATS.md](docs/FORMATS.md) | The `.lemesh` and `.lescatter` package formats, field by field. |
| [docs/LOD.md](docs/LOD.md) | Both LOD systems, the numbers, and the caveats. |
| [docs/MATERIALS.md](docs/MATERIALS.md) | What materials carry, and exactly where the chain to the renderer breaks. |
| [docs/LIGHTING.md](docs/LIGHTING.md) | The light record, the unit conversion, and why a light importer is not shipped. |

## License

Released under the [MIT License](LICENSE).

## Disclaimer

This project is not affiliated with, endorsed by, or associated with Ready At Dawn
or Meta. It ships **no game assets** and includes **no proprietary Oodle runtime**.
It is a tool for working with data from **your own legally obtained copy** of the
game, for personal, research, and interoperability purposes. You are responsible for
your use of it.
