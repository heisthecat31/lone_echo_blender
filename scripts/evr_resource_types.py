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
MESH_GPU_BUCKET = "e642bfb1abcf76df"         # raw stream-1 vertex/index blobs

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
TEXTURE_DDS_SIDECAR = "beac1969cb7b8861"

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
}

#: The GPU vertex/index blob for an instanced model.  Not in `type_map.json`
#: under any `win10_name`, so it is carried by hash.
INSTANCED_MODEL_GPU = "e7a8ab5ceaef49cb"
UNKNOWN_MESH_BUCKET = INSTANCED_MODEL_GPU      # legacy alias

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
    directory = Path(root) / type_hash
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
