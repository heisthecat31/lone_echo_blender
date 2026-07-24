# Lone Echo Blender Importer

Import Lone Echo / NRadEngine meshes **and** whole scatter levels into Blender with
full vertex attributes, per-draw PBR materials, and skeletons.

## Showcase

A whole static-scatter level, extracted to a portable package and imported into
Blender — 21,000+ placed instances over 1,000+ unique meshes, with per-material
base-color and normal maps:

![Textured scatter level in the Blender viewport](blender_tool/fixtures/scatter_station_front_textured.png)

The same level rendered through Blender's EEVEE engine, where the reconstructed
normal maps contribute surface detail:

![EEVEE PBR render of the scatter level](blender_tool/fixtures/scatter_station_front_eevee.png)

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

- **Import Materials** — build full PBR materials for each draw.
- **Include Shadow-Only Meshes** — also import meshes flagged shadow-only.
- **Flip UV V** — convert top-left UV origin to Blender's bottom-left.
- **Y-up to Z-up** — stand the model upright for Blender's Z-up world.
- **Import Armature** — build a skeleton from the package (when present) and skin
  the meshes to it.
- **Apply Scene Placement** — position the imported meshes at their level world
  transforms using an accompanying scene description.

### Lone Echo Scatter (.lescatter) — whole levels

**File > Import > "Lone Echo Scatter (.lescatter)"**. Options:

- **Flip UV V** — as above.
- **Y-up to Z-up** — apply the upright basis per instance (never baked into the
  shared meshes).
- **Include Proxy Meshes** — also build meshes flagged as collision/LOD proxies.
- **Max Instances** — cap how many instances are placed, for a fast first preview
  (0 = place all).

Each unique mesh is built once and shared across all of its instances as a linked
duplicate, so even tens of thousands of instances stay memory-light.

**Materials.** Meshes with more than one draw get **one material slot per draw**,
with each face assigned to its covering draw. Materials are full **PBR**: base-color
plus normal, with **BC5 normal reconstruction** wired into the shader graph.

**Rendering.** A headless render harness,
`blender_tool/tests/blender_scatter_render.py`, can render a package to an image
with either the Workbench engine or, with `engine=eevee`, a full PBR EEVEE render.

## Tests

Run the archive-free core test suite (no game data required):

```bash
python3 blender_tool/tests/run_tests.py
```

## License

Released under the [MIT License](LICENSE).

## Disclaimer

This project is not affiliated with, endorsed by, or associated with Ready At Dawn
or Meta. It ships **no game assets** and includes **no proprietary Oodle runtime**.
It is a tool for working with data from **your own legally obtained copy** of the
game, for personal, research, and interoperability purposes. You are responsible for
your use of it.
