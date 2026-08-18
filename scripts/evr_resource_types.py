"""Echo VR resource-type directory hashes.

A flat Echo VR extract (e.g. `H:\\pcvr-extracted`) is one directory per resource
TYPE, named by the type's CSymbol64, containing one file per resource named by
that resource's own CSymbol64:

    <root>/<type_hash>/<resource_hash>

Every constant below is transcribed from `rad-archive-viewer/type_map.json`,
which maps `win10_name <-> type hash` for the whole registry.  They are NOT
guesses and they are NOT re-derived by hashing the name here on purpose: the
type map is the artefact that was validated against a real extract, so it is the
thing worth quoting.  `verify_against_type_map()` re-checks them if you have the
map to hand.

## Corrections this module carries

`scripts/evr_scene_extract.py` shipped three mislabelled comments.  They were
comments only -- the hashes it used were right for what it did -- but two of the
labels named the wrong resource, which makes the search order look deliberate
when it is not:

* `37102e4b27955a14` is **CGInstancedModelResourceWin10**, not
  `CGMeshListResourceWin10` as the comment claimed.
* `e642bfb1abcf76df` is the raw stream-1 GPU vertex/index bucket, not
  `CGInstancedModelResourceWin10`.
* the real `CGMeshListResourceWin10` is `4e426f88c1b5d7ac`, which the old
  `MESH_DIRS` never searched at all.

## The GPU buckets are named after all

Three hashes here were carried with "not in `type_map.json` under any
`win10_name`" comments and no name.  `type_map.json` is simply incomplete (the
copy in this tree is 37KB of zeroes, which is its own problem);
`quest_combat_port/data/hash_lookup.json` names all three --
`CGMeshListResourceWin10GPU`, `CGInstancedModelResourceWin10GPU`,
`CGTextureResourceWin10GPU`.  That mattered, because "this type has no name"
was also the reason nobody derived its `Win7` twin, and without those twins a
Win7 extract has no geometry and no texture pixels at all.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Scene / actor graph
# ---------------------------------------------------------------------------
ACTOR_DATA = "347869ce492dc7da"          # CActorDataResourceWin10
SCENE_RESOURCE = "a388ea69e5108f4c"      # CGSceneResourceWin10
MODEL_CR = "ea51a0d76eb90142"            # CModelCRWin10
INSTANCE_MODEL_CR = "2464c4ed290f3268"   # CInstanceModelCRWin10
STATIC_MODEL_CR = "263584544abbd56c"     # CStaticInstanceModelCRWin10
STATIC_RESOURCE = "77c0bf257ca92aa0"     # CGStaticInstanceResourceWin10
BVH_RESOURCE = "358b53c17825d154"        # CBVHResourceWin10
#: Component transform table -- the ONLY source of static-instance placement.
#: `CGStaticInstanceResourceWin10` holds asset/lighting bindings and no
#: transform, so a static instance is placed by joining its CSIMCR entity here.
TRANSFORM_CR = "92abd3e1432bf5e8"        # CTransformCRWin10

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
MESH_LIST_RESOURCE = "4e426f88c1b5d7ac"      # CGMeshListResourceWin10  (stream 0)
INSTANCED_MODEL_RESOURCE = "37102e4b27955a14"  # CGInstancedModelResourceWin10
MESH_GPU_BUCKET = "e642bfb1abcf76df"         # CGMeshListResourceWin10GPU
#: The GPU vertex/index blob for an instanced model.
INSTANCED_MODEL_GPU = "e7a8ab5ceaef49cb"     # CGInstancedModelResourceWin10GPU
UNKNOWN_MESH_BUCKET = INSTANCED_MODEL_GPU      # legacy alias

# ---------------------------------------------------------------------------
# Materials and textures
# ---------------------------------------------------------------------------
MATERIAL_RESOURCE = "e3e0266f1911dafa"   # CGMaterialResourceWin10
#: Where the real per-texture ROLE bindings live.
#:
#: ⚠ NOT `CGMaterialResourceWin10.auxillaryinputs`.  That table was the obvious
#: candidate and it is a dead end: across 1727 shipped materials it holds
#: exactly two inputnames, `cutting_cut_decal` and `cutting_scorch_decal`, on
#: 337 materials, two each.  It is a cutting/scorch decal slot, not the surface
#: texture table.  Lone Echo binds through the shader set for the same reason.
SHADER_SET_RESOURCE = "4984e2bbb2ddb256"     # CGShaderSetResourceWin10
STANDALONE_SHADER = "635f6220ae85fd46"       # CGStandaloneShaderResourceWin10
TEXTURE_RESOURCE = "4a4c32c49300b8a0"    # cgtextureresourceWin10 (lowercase in registry)
TEXTURE_STREAMING = "c2434c5a99e139ce"   # CGTextureStreamingResourceWin10
RAW_TEXTURE_PACK = "ae49fad43254367a"    # RawTexturePackfileWin10
#: The texture's actual pixel payload, as a COMPLETE, ready-to-use DDS file
#: (starts with the literal "DDS " magic), addressed by the SAME hash as
#: `TEXTURE_RESOURCE`. Not in `type_map.json` under any `win10_name` -- carried
#: by hash, same as `INSTANCED_MODEL_GPU` below -- but independently confirmed
#: as `TypeDDSTexture` / "cgtexture pixel payloads" / "the raw DDS" across the
#: Go extractor (`pkg/naming/type_mapper.go`) and multiple rad-archive-viewer
#: mod-tool scripts. Present for essentially every texture (12287 of 12293 in
#: one flat extract) -- this is THE pixel source, not a fallback: prefer it
#: over both `_rebuild_from_layout` and `_rebuild_legacy` in
#: `evr_texture_resource.rebuild_dds`, which reconstruct from partial/
#: sentinel-filled data this sidecar makes unnecessary. It is what
#: `streamingdisabled == 1` textures -- whose `cgtextureresourceWin10` header
#: carries no inline pixels AND an all-`0xFFFFFFFF` sentinel packfilelayout --
#: turn out to always have, which is why they are recoverable at all.
TEXTURE_DDS_SIDECAR = "beac1969cb7b8861"      # CGTextureResourceWin10GPU

# ---------------------------------------------------------------------------
# Rigging and baked lighting
#
# These three were declared privately in the modules that use them, which is
# why they had no Win7 twin: `resolve_type_dir` can only translate a hash it
# has been told about.  They live here now so every type the pipeline opens is
# translated from one table.
# ---------------------------------------------------------------------------
#: The level's CLOSURE / load driver -- the authoritative list of every
#: `(type, resource)` the engine loads for a level. Decoded by
#: `quest_combat_port/tools/resource_io/carchiveresource.py`.
ARCHIVE_RESOURCE = "2a41cf1c1d9e5d32"    # CArchiveResourceWin10
#: Per-model animation set: the animation TABLE (see `evr_animset`).
ANIM_SET_RESOURCE = "e9e7d2e25d8e2252"   # CAnimSetResourceWin10
SKELETON_RESOURCE = "46adff5980245670"   # CSkeletonResourceWin10
LIGHTMAP_RESOURCE = "230554bc3beca38c"   # CGLightMapResourceWin10
STATIC_RESOURCE_GPU = "dd3ff9850e4eed35"  # CGStaticInstanceResourceWin10GPU

# ---------------------------------------------------------------------------
# UI
#
# Echo VR does not build its UI out of meshes the way Lone Echo's holotable
# does.  A screen is a CANVAS: a pixel-sized rectangle of elements, each one a
# sub-rectangle of a shared texture atlas, placed into the world by a per-level
# component table that names an actor node and a pixels-per-metre scale.  That
# is why none of it shows up in a mesh export -- there is no mesh to find.
# ---------------------------------------------------------------------------
#: The canvas itself: size in pixels, then its element table.
UI_CANVAS_RESOURCE = "59a9bd6e4525ecc4"  # CUICanvasResourceWin10
#: Per-LEVEL component table that PLACES canvases on actor nodes.
CANVAS_UI_CR = "822fd4ccb42e8a3c"        # CCanvasUICRWin10
SHARED_CANVAS_UI_CR = "dab7dce1df894ef6"  # CSharedCanvasUICRWin10

#: name -> hash, for `verify_against_type_map`.
TYPE_NAMES: dict[str, str] = {
    "CActorDataResourceWin10": ACTOR_DATA,
    "CGSceneResourceWin10": SCENE_RESOURCE,
    "CModelCRWin10": MODEL_CR,
    "CInstanceModelCRWin10": INSTANCE_MODEL_CR,
    "CStaticInstanceModelCRWin10": STATIC_MODEL_CR,
    "CGStaticInstanceResourceWin10": STATIC_RESOURCE,
    "CBVHResourceWin10": BVH_RESOURCE,
    "CGMeshListResourceWin10": MESH_LIST_RESOURCE,
    "CGInstancedModelResourceWin10": INSTANCED_MODEL_RESOURCE,
    "CGMaterialResourceWin10": MATERIAL_RESOURCE,
    "CGShaderSetResourceWin10": SHADER_SET_RESOURCE,
    "CGStandaloneShaderResourceWin10": STANDALONE_SHADER,
    "cgtextureresourceWin10": TEXTURE_RESOURCE,
    "CGTextureStreamingResourceWin10": TEXTURE_STREAMING,
    "RawTexturePackfileWin10": RAW_TEXTURE_PACK,
    "CGMeshListResourceWin10GPU": MESH_GPU_BUCKET,
    "CGInstancedModelResourceWin10GPU": INSTANCED_MODEL_GPU,
    "CGTextureResourceWin10GPU": TEXTURE_DDS_SIDECAR,
    "CSkeletonResourceWin10": SKELETON_RESOURCE,
    "CGLightMapResourceWin10": LIGHTMAP_RESOURCE,
    "CGStaticInstanceResourceWin10GPU": STATIC_RESOURCE_GPU,
    "CTransformCRWin10": TRANSFORM_CR,
    "CArchiveResourceWin10": ARCHIVE_RESOURCE,
    "CAnimSetResourceWin10": ANIM_SET_RESOURCE,
    "CUICanvasResourceWin10": UI_CANVAS_RESOURCE,
    "CCanvasUICRWin10": CANVAS_UI_CR,
    "CSharedCanvasUICRWin10": SHARED_CANVAS_UI_CR,
}

# ---------------------------------------------------------------------------
# Win7 equivalents (older builds, e.g. the Summer lobby build)
#
# The hash of `CActorDataResourceWin7` is NOT `ACTOR_DATA`; every type has a
# different hash for its Win7 variant.  The mapping below lets the pipeline
# locate the correct directory in either format automatically.
#
# Every value is `CSymbol64(<the Win10 name with "Win10" replaced by "Win7">)`,
# and `verify_win7_hashes()` re-derives the whole table to prove it.  The names
# come from `quest_combat_port/data/hash_lookup.json`, which is where the three
# types the old table was missing -- the two GPU geometry buckets and the DDS
# sidecar -- turn out to be named after all.  They were carried here as bare
# hashes on the belief that they had no name, so nobody thought to look for a
# Win7 twin; on the Summer build that is exactly what made every model export
# as "no meshes found".
# ---------------------------------------------------------------------------
_WIN10_TO_WIN7: dict[str, str] = {
    ACTOR_DATA:              "c165fbf2e77f973d",  # CActorDataResourceWin7
    SCENE_RESOURCE:          "86f4cd162e7da857",  # CGSceneResourceWin7
    MODEL_CR:                "3de813820d0b4719",  # CModelCRWin7
    INSTANCE_MODEL_CR:       "9ccac823a34d5d61",  # CInstanceModelCRWin7
    STATIC_MODEL_CR:         "d612ab89f07e9ee1",  # CStaticInstanceModelCRWin7
    STATIC_RESOURCE:         "e83cf7faaec4cab5",  # CGStaticInstanceResourceWin7
    BVH_RESOURCE:            "3ae74682a3963d31",  # CBVHResourceWin7
    TRANSFORM_CR:            "5c06dd89d54954c9",  # CTransformCRWin7
    MESH_LIST_RESOURCE:      "366b22153d894fe1",  # CGMeshListResourceWin7
    INSTANCED_MODEL_RESOURCE: "1a8ef93542db7fd7", # CGInstancedModelResourceWin7
    MATERIAL_RESOURCE:       "117d2b6509c8ff79",  # CGMaterialResourceWin7
    SHADER_SET_RESOURCE:     "5fa019d27a511a3b",  # CGShaderSetResourceWin7
    STANDALONE_SHADER:       "f717bc83bcd0c537",  # CGStandaloneShaderResourceWin7
    TEXTURE_RESOURCE:        "e8017b774f2b6327",  # cgtextureresourceWin7
    TEXTURE_STREAMING:       "23d48cecc462abe7",  # CGTextureStreamingResourceWin7
    RAW_TEXTURE_PACK:        "51e6cb2d64c65e4f",  # RawTexturePackfileWin7
    # -- the entries whose absence broke the Summer lobby ------------------
    MESH_GPU_BUCKET:         "617076c759935957",  # CGMeshListResourceWin7GPU
    INSTANCED_MODEL_GPU:     "039a43c1af5440f9",  # CGInstancedModelResourceWin7GPU
    TEXTURE_DDS_SIDECAR:     "e2f9e022d8519ca9",  # CGTextureResourceWin7GPU
    SKELETON_RESOURCE:       "202d89353292d63d",  # CSkeletonResourceWin7
    LIGHTMAP_RESOURCE:       "6665bedfeadf8b79",  # CGLightMapResourceWin7
    STATIC_RESOURCE_GPU:     "20b61b33e84bab85",  # CGStaticInstanceResourceWin7GPU
    ARCHIVE_RESOURCE:        "e5bd8207135b8887",  # CArchiveResourceWin7
    ANIM_SET_RESOURCE:       "9576db2165a5f779",  # CAnimSetResourceWin7
    UI_CANVAS_RESOURCE:      "d2c0532987135e95",  # CUICanvasResourceWin7
    CANVAS_UI_CR:            "deec78ebf244b725",  # CCanvasUICRWin7
    SHARED_CANVAS_UI_CR:     "4f86b88915b537f1",  # CSharedCanvasUICRWin7
}

#: Win7 hash -> the Win10 hash it stands in for.  Lets a caller that found a
#: directory by iteration label it with the type it actually is.
_WIN7_TO_WIN10: dict[str, str] = {v: k for k, v in _WIN10_TO_WIN7.items()}


def _dir_name_variants(type_hash: str):
    """Every spelling a type directory may carry on disk, in search order.

    ⚠ `CGInstancedModelResourceWin7GPU` is `039a43c1af5440f9` and the Summer
    extract writes that directory as `39a43c1af5440f9` -- 15 characters, the
    leading zero stripped.  `resource_path` already tolerated that for RESOURCE
    file names; the type directory did not, and one dropped nibble is enough to
    lose every instanced model in the build.
    """
    canonical = normalise_hash(type_hash)
    stripped = canonical.lstrip("0") or "0"
    return dict.fromkeys((str(type_hash), canonical, stripped))


def resolve_type_dir(root: Path, win10_hash: str) -> Path:
    """Return the existing resource-type directory, checking Win10 then Win7.

    Falls back to the Win10 path (even if it doesn't exist) so callers that
    only care about existence keep working.
    """
    root = Path(root)
    canonical = normalise_hash(win10_hash)
    for type_hash in (win10_hash, _WIN10_TO_WIN7.get(canonical)):
        if not type_hash:
            continue
        for name in _dir_name_variants(type_hash):
            directory = root / name
            if directory.is_dir():
                return directory
    return root / str(win10_hash)  # fallback: caller will see it doesn't exist


#: How the `CGMeshData` table that opens a `CGMeshListResource` is framed:
#: `(count_offset, table_offset, record_stride)`.
#:
#: ⛔ The two formats do NOT share a layout, and reading a Win7 file with the
#: Win10 frame is worse than reading nothing.  Win7 puts a u64 in front of the
#: count, so `u32@0` is a 0/1 flag: on the Summer build that flag is 1 for 80
#: of 439 mesh lists, and each of those was read as "one record starting at
#: byte 4" -- 80 records of pure misalignment that the caller had no way to
#: tell from real ones.
#:
#: Measured on the 303 models that ship in BOTH the Win7 (Summer) and Win10
#: (Summer2) builds: the counts agree model-for-model, and each Win7 record is
#: the Win10 record minus two u64s after the mesh id and one u64 further in --
#: 24 bytes -- with the trailing 96 bytes byte-identical.  So a field at Win10
#: offset `n >= 0x38` sits at `n - 24` in Win7.
MESH_TABLE_WIN10 = (0, 4, 152)
MESH_TABLE_WIN7 = (8, 12, 128)


def mesh_table_layout(path: Path) -> tuple[int, int, int]:
    """`(count_offset, table_offset, stride)` for a mesh-list file.

    Decided by the type directory the file was found in, which is the only
    thing that actually states the format; sniffing the bytes cannot, because
    a Win7 flag of 1 is indistinguishable from a Win10 count of 1.
    """
    parent = normalise_hash(Path(path).parent.name)
    if parent == normalise_hash(_WIN10_TO_WIN7[MESH_LIST_RESOURCE]):
        return MESH_TABLE_WIN7
    return MESH_TABLE_WIN10


def win7_type_hash(win10_hash) -> str | None:
    """The Win7 twin of a Win10 type hash, or None when there is no entry."""
    return _WIN10_TO_WIN7.get(normalise_hash(win10_hash))


def canonical_type_hash(type_hash) -> str:
    """A Win7 OR Win10 type hash -> the Win10 hash the pipeline names it by.

    Directory-iterating callers (`evr_materials.locate_model`,
    `scan_all_files`) label what they find with `TYPE_NAMES`, which is keyed by
    Win10 hashes only.  Without this, every Win7 directory came back labelled
    with a raw hash, and `scan_model_references` -- which prefers the geometry
    resources BY NAME -- silently fell through to "whatever file matched".
    """
    canonical = normalise_hash(type_hash)
    return _WIN7_TO_WIN10.get(canonical, canonical)


def verify_win7_hashes() -> list[str]:
    """Re-derive every Win7 hash from its Win10 name; report disagreements.

    `CSymbol64` is available in-tree (`le_mesh.material_scalars.symbol64`), so
    the Win7 table does not have to be trusted -- it can be recomputed.
    Returns [] when the whole table checks out.
    """
    from le_mesh.material_scalars import symbol64

    by_hash = {normalise_hash(v): k for k, v in TYPE_NAMES.items()}
    problems: list[str] = []
    for win10_hash, win7_hash in _WIN10_TO_WIN7.items():
        name = by_hash.get(normalise_hash(win10_hash))
        if name is None:
            problems.append(f"{win10_hash}: no name in TYPE_NAMES to re-derive from")
            continue
        expected = normalise_hash(symbol64(name.replace("Win10", "Win7")))
        if expected != normalise_hash(win7_hash):
            problems.append(f"{name}Win7: table says {win7_hash}, "
                            f"CSymbol64 says {expected}")
    return problems

#: GPU blob -> its PRIMARY descriptor.  Transcribed from
#: `evr-mesh-importer/primary.py::_find_primary_path`, the implementation that
#: loads this level correctly.
#:
#: ⛔ This pairing is why geometry was mangled.  `decode.extract_mesh` takes the
#: GPU blob AND the primary that describes its vertex format; called with the
#: blob alone it falls back to a `heuristic` path that guesses the stride, which
#: renders as triangle fans radiating from a point.  It also explains the models
#: that "returned nothing": without a primary there was nothing to describe them.
#:
#: The old `MESH_DIRS` compounded it by listing `37102e4b27955a14` -- a PRIMARY --
#: as a place to look for geometry, and `ae49fad43254367a`, a texture pack.
GPU_TO_PRIMARY: dict[str, str] = {
    MESH_GPU_BUCKET: MESH_LIST_RESOURCE,            # e642... -> 4e42...
    INSTANCED_MODEL_GPU: INSTANCED_MODEL_RESOURCE,  # e7a8... -> 3710...
}

#: The GPU blobs `decode.extract_mesh` is pointed at -- and ONLY those.
#:
#: Each must be paired with its primary from `GPU_TO_PRIMARY`.  The old list had
#: four entries and two were wrong in kind: `37102e4b27955a14` is a primary
#: descriptor, and `ae49fad43254367a` is `RawTexturePackfileWin10`.  Handing
#: either to the mesh decoder produces garbage or nothing.
MESH_DIRS: tuple[str, ...] = (
    MESH_GPU_BUCKET,        # e642... paired with CGMeshListResourceWin10
    INSTANCED_MODEL_GPU,    # e7a8... paired with CGInstancedModelResourceWin10
)


def find_mesh_and_primary(root, resource_hash) -> tuple:
    """`(gpu_path, primary_path)` for a model, or `(None, None)`.

    `decode.extract_mesh` needs both: the blob holds the vertex and index bytes,
    the primary holds the format that says how to read them.
    """
    for gpu_dir in MESH_DIRS:
        gpu_path = resource_path(root, gpu_dir, resource_hash)
        if gpu_path is None:
            continue
        primary_dir = GPU_TO_PRIMARY.get(gpu_dir)
        primary_path = (resource_path(root, primary_dir, resource_hash)
                        if primary_dir else None)
        return gpu_path, primary_path
    return None, None


def normalise_hash(value) -> str:
    """Any spelling of a CSymbol64 -> canonical lowercase 16-hex-digit form.

    Accepts `int`, `"0x1234"`, `"1234"`, `"1234ABCD"`, and already-normalised
    input.  Returns `""` for None/empty so callers can test falsiness rather
    than catching.
    """
    if value is None:
        return ""
    if isinstance(value, int):
        return f"{value & 0xFFFFFFFFFFFFFFFF:016x}"
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    if not text:
        return ""
    return text.rjust(16, "0")


def resource_path(root: Path, type_hash: str, resource_hash) -> Path | None:
    """Locate `<root>/<type_hash>/<resource_hash>`, tolerating name variants.

    Real extracts differ on two axes -- a `.bin` suffix, and whether leading
    zeroes were stripped when the file was written.  Both are tried; the first
    existing path wins.  Returns None when nothing matches.
    """
    directory = resolve_type_dir(Path(root), type_hash)
    if not directory.is_dir():
        return None
    canonical = normalise_hash(resource_hash)
    if not canonical:
        return None
    stripped = canonical.lstrip("0") or "0"
    for stem in dict.fromkeys((canonical, stripped)):
        for suffix in ("", ".bin"):
            candidate = directory / f"{stem}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def verify_against_type_map(type_map_path: Path) -> list[str]:
    """Re-check every constant against a `type_map.json`.

    Returns a list of human-readable disagreements; empty means everything in
    `TYPE_NAMES` matched.  Names absent from the map are reported rather than
    silently passed, because a silent pass on a missing key is exactly how a
    stale constant survives.
    """
    import json

    raw = json.loads(Path(type_map_path).read_text(encoding="utf-8"))
    by_name: dict[str, str] = {}
    for type_hash, record in raw.items():
        name = (record or {}).get("win10_name")
        if name:
            by_name[name] = normalise_hash(type_hash)

    problems: list[str] = []
    for name, expected in TYPE_NAMES.items():
        actual = by_name.get(name)
        if actual is None:
            problems.append(f"{name}: absent from type map (expected {expected})")
        elif actual != normalise_hash(expected):
            problems.append(f"{name}: type map says {actual}, constant says {expected}")
    return problems


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("usage: python evr_resource_types.py <type_map.json>")
        raise SystemExit(2)
    issues = verify_against_type_map(Path(sys.argv[1]))
    if not issues:
        print(f"OK - all {len(TYPE_NAMES)} type hashes match the map")
    else:
        for line in issues:
            print(f"MISMATCH {line}")
        raise SystemExit(1)
