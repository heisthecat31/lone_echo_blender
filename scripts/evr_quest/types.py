"""Android resource-type hashes, and how to find a resource in a Quest extract.

A Quest extract has the same shape as a PC one -- one directory per resource
TYPE, named by the type's CSymbol64, holding one file per resource -- but the
type namespace is the `*Android` / `*AndroidGPU` half of the registry, which
shares no hashes with `*Win10`.

## Leading zeros are not consistent

⚠ The extractor that produced these trees writes a hash WITHOUT its leading
zeros in some places and with them in others: `CModelCRAndroid` appears as BOTH
`042c7bbb8da4211c` (1 file) and `42c7bbb8da4211c` (17 files) in the same root,
and the same happens to resource file names inside a directory. Every lookup
here therefore tries both spellings, and `type_dir` returns EVERY directory a
type resolves to so a caller cannot silently read one half of a split type.

The names come from `data/hash_lookup.json`, which covers all 227 type
directories in the shipped Quest tree with none left unnamed.
"""

from __future__ import annotations

import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_HASH_LOOKUP = _ROOT / "data" / "hash_lookup.json"

# ── the types this pipeline actually reads ─────────────────────────────
# Named constants for the ones with call sites; everything else resolves by
# name through `type_dir`.
ACTOR_DATA = "9ccc05b17c970f8a"          # CActorDataResourceAndroid
SCENE_RESOURCE = "3ef2335265daea50"      # CGSceneResourceAndroid
STATIC_RESOURCE = "20a01c39ad43b294"     # CGStaticInstanceResourceAndroid
STATIC_RESOURCE_GPU = "3073a5a0ddb97777"  # CGStaticInstanceResourceAndroidGPU
MESH_LIST = "616671cd1c9b4046"           # CGMeshListResourceAndroid
MESH_LIST_GPU = "e3478176e0b181df"       # CGMeshListResourceAndroidGPU
INSTANCED_MODEL = "38c44cbea5c59e8"      # CGInstancedModelResourceAndroid
INSTANCED_MODEL_GPU = "ff0f08277e8c1735"  # CGInstancedModelResourceAndroidGPU
TEXTURE = "e2efe7289d5985b8"             # CGTextureResourceAndroid
TEXTURE_GPU = "489bb35d53ca50e9"         # CGTextureResourceAndroidGPU
RAW_TEXTURE_PACKFILE = "b3a48859dea50b62"  # RawTexturePackfileAndroid
MATERIAL = "685a78f51339749c"            # CGMaterialResourceAndroid
MODEL_CR = "42c7bbb8da4211c"             # CModelCRAndroid  (also 042c…)
INSTANCE_MODEL_CR = "15ada71435037ff0"   # CInstanceModelCRAndroid
STATIC_MODEL_CR = "37d791a976c753e6"     # CStaticInstanceModelCRAndroid
TRANSFORM_CR = "dc6d950e3793520c"        # CTransformCRAndroid
SKELETON = "cc9d9374cb572064"            # CSkeletonResourceAndroid
ANIM_SET = "c08b58705528de72"            # CAnimSetResourceAndroid
ANIMATION_CR = "dc23db19af2436ce"        # CAnimationCRAndroid
GAME_LEVEL = "275adc4c85e7ba74"          # CGameLevelResourceAndroid
TEXTURE_OVERRIDE_CR = "2cb8da6f40bb67c0"  # CTextureOverrideCRAndroid
SCRIPT_CR = "f31aed40bf478d4e"           # CScriptCRAndroid
PLATFORM_CR = "311ebc6087c00544"         # CPlatformCRAndroid
#: The per-level ASSET CLOSURE, and the one type the shipped tree left unnamed.
#: `symbol64("CArchiveResourceAndroid")` is an exact match, and the file's own
#: shape confirms it: `{u32 0}{u32 record_count}` then `record_count` x 16-byte
#: `{type_hash, target_hash}` front-index records -- the layout
#: `quest_combat_port/data/source_extension_resource_type_map.tsv` gives for
#: `radarc`. The arena's archive is 1166 records: 330 textures, 99 materials,
#: 82 texture-streaming, 77 shader sets, 54 instanced models.
#:
#: Useful because it states exactly WHICH assets belong to a level, rather than
#: leaving it to be inferred from placements.
ARCHIVE = "04c5273be3eab58"              # CArchiveResourceAndroid


def spellings(value) -> list:
    """Both hash spellings a Quest extract may use, longest first."""
    text = value if isinstance(value, str) else f"{int(value):x}"
    text = text.lower().removeprefix("0x")
    padded = text.rjust(16, "0")
    stripped = padded.lstrip("0") or "0"
    return [padded] if padded == stripped else [padded, stripped]


_NAMES: dict | None = None


def names() -> dict:
    """`{hash -> name}` restricted to the Android half of the registry."""
    global _NAMES
    if _NAMES is None:
        try:
            raw = json.loads(_HASH_LOOKUP.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        _NAMES = {k.lower().removeprefix("0x").rjust(16, "0"): v
                  for k, v in raw.items()
                  if isinstance(v, str) and "Android" in v}
    return _NAMES


_BY_NAME: dict | None = None


def type_hash(name: str) -> str | None:
    """`CGTextureResourceAndroid` -> its type hash, or None."""
    global _BY_NAME
    if _BY_NAME is None:
        _BY_NAME = {v: k for k, v in names().items()}
    return _BY_NAME.get(name)


def type_dirs(root, type_hash_or_name) -> list:
    """EVERY directory this type resolves to, in the order they should be read.

    A list rather than a single path because the leading-zero inconsistency
    genuinely splits one type across two directories.
    """
    root = Path(root)
    value = type_hash_or_name
    if isinstance(value, str) and not _is_hex(value):
        value = type_hash(value) or value
    out = []
    for spelling in spellings(value):
        candidate = root / spelling
        if candidate.is_dir():
            out.append(candidate)
    return out


def type_dir(root, type_hash_or_name) -> Path | None:
    """The first directory for a type, or None. Prefer `type_dirs`."""
    found = type_dirs(root, type_hash_or_name)
    return found[0] if found else None


def resource(root, type_hash_or_name, resource_hash) -> Path | None:
    """Path to one resource, trying both spellings of both hashes."""
    for directory in type_dirs(root, type_hash_or_name):
        for spelling in spellings(resource_hash):
            candidate = directory / spelling
            if candidate.is_file():
                return candidate
    return None


def resources(root, type_hash_or_name) -> dict:
    """`{padded hash -> path}` for every resource of a type."""
    out = {}
    for directory in type_dirs(root, type_hash_or_name):
        for path in directory.iterdir():
            if path.is_file():
                out.setdefault(spellings(path.name)[0], path)
    return out


def is_quest_extract(root) -> bool:
    """Does this look like a Quest extract? Actor tables AND scenes, Android."""
    root = Path(root)
    return bool(type_dirs(root, ACTOR_DATA) and type_dirs(root, SCENE_RESOURCE))


def levels(root) -> list:
    """Padded hashes that are LEVELS: an actor table AND a scene resource."""
    actors = set(resources(root, ACTOR_DATA))
    scenes = set(resources(root, SCENE_RESOURCE))
    return sorted(actors & scenes)


def _is_hex(text: str) -> bool:
    stripped = text.lower().removeprefix("0x")
    return bool(stripped) and all(c in "0123456789abcdef" for c in stripped)


def survey(root) -> list:
    """`[(hash, name, files, bytes), ...]` for every type directory present."""
    root = Path(root)
    table = names()
    out = []
    for path in sorted(root.iterdir()):
        if not path.is_dir():
            continue
        files = [p for p in path.iterdir() if p.is_file()]
        padded = spellings(path.name)[0]
        out.append((path.name, table.get(padded, "?"), len(files),
                    sum(p.stat().st_size for p in files)))
    return out
