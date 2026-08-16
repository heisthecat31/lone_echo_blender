"""The REAL per-material texture table: `SGMaterialData`'s slot -> texture CMap.

## What this is, and why it replaces the guessing

Every route this pipeline used before -- shader-set overlap, DXGI family
clustering, hand spot-confirmation -- existed because the model -> texture link
was believed to be absent from the data.  It is not.  `CGMaterialResourceWin10`
carries it directly, in the LAST of its six containers:

    CMap<CSymbol64, CSymbol64>   slot-name CSymbol64  ->  texture CSymbol64

`layer0_basecolor_map -> <texture>`, per material, on disk, no inference.

## Why it was missed for so long

`evr_material_resource.py` models the payload as a header of six UNIFORM
56-byte descriptors starting at offset 56.  The real `SGMaterialData` is a fixed
struct IMAGE with six containers at FIXED, NON-UNIFORM offsets, because a
`CMap` header is 64 bytes (56 + an 8-byte slot) while a `CTable` header is 56:

    +0x28   CTable<unsigned char>            stride 1
    +0x60   CMap<CSymbol64, SMatPropHandle>  stride 16   material properties
    +0xA0   CTable<CSymbol64>                stride 8    slot refs
    +0xD8   CMap<unsigned int, unsigned int> stride 8    permutations
    +0x118  CTable<SShaderInputData>         stride 32   decal inputs
    +0x150  CMap<CSymbol64, CSymbol64>       stride 16   ⟵ THE TEXTURE TABLE

so the image is 0x190 bytes, not the 0x188 the uniform-stride model computes.
Two further rules, both of which the old reader gets wrong:

* the on-disk array holds **`iused`** (LIVE) elements, not **`count`** (the
  runtime hash CAPACITY).  For a `CMap`, `count > iused` -- reading `count`
  elements walks off the end of the live data into the next array.
* the image size is per-file: `image_size = filesize - Σ(iused * stride)`.

Under the old model the sixth container was read at the wrong offset and the
wrong length, which is exactly why it was dismissed in `docs/ECHO_VR.md` as a
"shared boilerplate table" -- the bytes being fingerprinted were not the table.

The grammar here is transcribed from `quest_combat_port/tools/resource_io/
cgmaterialresource.py`, an independent reverse-engineering project that derived
it from `SGMaterialData::Inspect` disassembly and round-trips it byte-exact on
492/492 Quest and 1713/1713 PC materials.  It reads all **1727/1727** materials
in this extract with zero failures.

## Defaults vs real bindings

Every material declares all ~84 slots; the unused ones point at shared engine
stub textures (a black texel, a flat normal, a white texel).  Those are not
this material's textures and must not be routed.  They are separated by how
many DISTINCT materials bind them, which is sharply bimodal across the corpus:

    bound by >= 100 materials :    14 textures   ⟵ engine defaults / stubs
    bound by >=  50 materials :    15 textures
    bound by ==   1 material  :  5736 textures   ⟵ real, per-material art

`DEFAULT_SHARE_THRESHOLD` sits in that gap.  It is a corpus measurement, not a
hard-coded hash list, so a build with different stub assets still classifies
correctly.

## Slot roles

A slot is named when its CSymbol64 preimage is known (`role_for_inputname`,
plus the `basecolor_map` spelling Echo VR uses where Lone Echo says
`albedo_map`).  The heaviest-used slots have no recovered preimage, so their
role is established from evidence instead, and `SLOT_ROLE` records which:

* `06470a0dd842f5d0` -> normal.  **Two independent lines agree**: BC5_UNORM on
  422/422 corpus bindings (BC5 is the two-channel normal format), and 288
  `SShaderInputData` binds in real shader sets name it `layer{0,1}_normal_map`.
* the rest are format-derived over 600+ bindings each at 96-100% single-format
  agreement -- see the table below.

The whole thing is corroborated end to end by the one material a human checked
against the running game: `1e070bb9873c1e45` binds all three of the
hand-confirmed textures, each in the role this module assigns it.
"""

from __future__ import annotations

import struct
import sys
from collections import Counter
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _path in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from evr_resource_types import MATERIAL_RESOURCE, normalise_hash, resource_path

#: (struct offset, element stride) for the six `SGMaterialData` containers, in
#: member order.  Non-uniform on purpose -- see the module docstring.
CONTAINERS: tuple[tuple[int, int], ...] = (
    (0x28, 1),     # CTable<unsigned char>
    (0x60, 16),    # CMap<CSymbol64, SMatPropHandle>   material properties
    (0xA0, 8),     # CTable<CSymbol64>                 slot refs
    (0xD8, 8),     # CMap<unsigned int, unsigned int>  permutations
    (0x118, 32),   # CTable<SShaderInputData>          decal inputs
    (0x150, 16),   # CMap<CSymbol64, CSymbol64>        THE TEXTURE TABLE
)

#: Index into `CONTAINERS` of the slot -> texture map.
TEXTURE_TABLE = 5

#: Smallest image a valid `SGMaterialData` can have (the six headers end here).
MIN_IMAGE = 0x190

#: A texture bound by at least this many DISTINCT materials is a shared engine
#: asset (default stub, common normal, ...), not this material's own art.  The
#: corpus histogram is bimodal with nothing between ~15 and ~5700, so any value
#: in the gap gives the same answer; see the module docstring.
DEFAULT_SHARE_THRESHOLD = 50


class MaterialTableError(ValueError):
    """The blob is not a well-formed `SGMaterialData`."""


def _header(data: bytes, off: int) -> dict:
    ptr, size, z10 = struct.unpack_from("<QQQ", data, off)
    z18, flags = struct.unpack_from("<II", data, off + 0x18)
    mark, count, iused = struct.unpack_from("<QQQ", data, off + 0x20)
    return {"ptr": ptr, "size": size, "z10": z10, "z18": z18,
            "flags": flags, "mark": mark, "count": count, "iused": iused}


def read_containers(data: bytes) -> dict:
    """Locate the six container arrays. Raises `MaterialTableError` if the blob
    is not a material.

    Every check here is a structural invariant, not a plausibility heuristic:
    the reserved words must be zero, `size` must equal `count * stride`, `iused`
    must not exceed `count`, and the arrays must consume the file exactly.  A
    blob that satisfies all of them at these fixed offsets is a material.
    """
    if len(data) < MIN_IMAGE:
        raise MaterialTableError(f"too short for SGMaterialData ({len(data)}B)")

    headers = []
    used = 0
    for off, stride in CONTAINERS:
        h = _header(data, off)
        if h["ptr"] or h["z10"] or h["z18"]:
            raise MaterialTableError(
                f"container @{off:#x}: reserved words nonzero -- not SGMaterialData")
        if h["count"] and h["size"] != h["count"] * stride:
            raise MaterialTableError(
                f"container @{off:#x}: size {h['size']} != count {h['count']} * {stride}")
        if h["iused"] > h["count"]:
            raise MaterialTableError(
                f"container @{off:#x}: iused {h['iused']} > count {h['count']}")
        h["stride"] = stride
        headers.append(h)
        used += h["iused"] * stride

    image_size = len(data) - used
    if image_size < MIN_IMAGE:
        raise MaterialTableError(
            f"computed image {image_size} < {MIN_IMAGE:#x} -- heap overflows the file")

    arrays = []
    cursor = image_size
    for h in headers:
        length = h["iused"] * h["stride"]
        arrays.append(data[cursor:cursor + length])
        cursor += length
    if cursor != len(data):
        raise MaterialTableError(f"arrays consumed {cursor} of {len(data)} bytes")

    return {"image_size": image_size, "headers": headers, "arrays": arrays}


def slot_textures(data: bytes) -> dict[str, str]:
    """`{slot_name_hash -> texture_hash}` straight off the disk, defaults included.

    Filtering defaults needs whole-corpus context, so it is `real_slot_textures`'
    job -- this stays the honest, unfiltered read of what the file says.
    """
    arrays = read_containers(data)["arrays"]
    table = arrays[TEXTURE_TABLE]
    out: dict[str, str] = {}
    for i in range(len(table) // 16):
        key, value = struct.unpack_from("<QQ", table, i * 16)
        out[f"{key:016x}"] = f"{value:016x}"
    return out


def load_slot_textures(root: Path, material_hash) -> dict[str, str]:
    """`slot_textures` for a material in a flat extract, or `{}` if unreadable."""
    path = resource_path(root, MATERIAL_RESOURCE, material_hash)
    if path is None:
        return {}
    try:
        return slot_textures(path.read_bytes())
    except (MaterialTableError, struct.error, OSError):
        return {}


def build_default_textures(root: Path, *, threshold: int = DEFAULT_SHARE_THRESHOLD,
                           progress=None) -> set[str]:
    """The shared engine stub textures, measured rather than listed.

    One pass over every material in the extract, counting how many DISTINCT
    materials bind each texture; anything at or above `threshold` is shared
    infrastructure.  Also returns the per-material tables it read, since the
    caller invariably wants them and re-reading 1727 files is wasteful.
    """
    directory = Path(root) / MATERIAL_RESOURCE
    if not directory.is_dir():
        return set()

    counts: Counter = Counter()
    paths = sorted(p for p in directory.iterdir() if p.is_file())
    for i, path in enumerate(paths):
        if progress and i and i % 500 == 0:
            progress(i, len(paths))
        try:
            table = slot_textures(path.read_bytes())
        except (MaterialTableError, struct.error, OSError):
            continue
        for texture in set(table.values()):
            counts[texture] += 1
    return {h for h, n in counts.items() if n >= threshold}


def real_slot_textures(data_or_table, default_textures: set[str]) -> dict[str, str]:
    """The slot -> texture bindings that are this material's OWN art.

    Accepts either raw material bytes or an already-read table.  Drops the
    shared engine stubs and the null/sentinel entries.
    """
    table = (data_or_table if isinstance(data_or_table, dict)
             else slot_textures(data_or_table))
    return {
        slot: texture for slot, texture in table.items()
        if texture not in default_textures
        and texture not in ("0000000000000000", "ffffffffffffffff")
    }


# ---------------------------------------------------------------------------
# Slot -> role
# ---------------------------------------------------------------------------

#: Slots whose CSymbol64 preimage has never been recovered, mapped to a role by
#: evidence.  `n` is corpus bindings; the share is the dominant DXGI format's.
#:
#:   slot              n    evidence                                  role
#:   06470a0dd842f5d0  466  BC5_UNORM 100% + 288 shader-set binds
#:                          naming it layer{0,1}_normal_map           normal
#:   760ba3c3f12eed10  710  BC5_UNORM 100%  (615 distinct textures)   normal
#:   01bc8bf07fbfc184  710  BC1_SRGB   97%  (683 distinct)            base colour
#:   2c714411035f192e  710  BC7_UNORM 100%  (621 distinct); matches
#:                          layer0_specular_map's own BC7_UNORM 96%   specular
#:   f55db9261547732a  710  BC6H_UF16  64% + BC1_SRGB 33% (670
#:                          distinct) -- HDR, so emissive             emissive
#:   b9d846db5f446d8c  381  BC1_UNORM  98%  (linear, not colour)      components
#:   7abbb0b3b865219b  653  BC1_UNORM  98%  (635 distinct)            components
#:   94ffd3d8117518b9  653  BC1_UNORM  96%  (632 distinct)            components
#:   1ea5d65d02ed2414  655  BC1_UNORM  98%  (585 distinct)            components
#:
#: BC5 carries two channels and is the normal-map format; an SRGB format is a
#: colour map by definition; BC6H is the HDR format, which only emissive uses.
#: The role keys are Lone Echo's routable suffixes (`CHANNEL_ROLE_SUFFIXES`),
#: NOT the Echo VR slot spelling -- `basecolor_map` reaches no Principled socket.
SLOT_ROLE: dict[str, str] = {
    "06470a0dd842f5d0": "layer0_normal_map",
    "760ba3c3f12eed10": "layer0_normal_map",
    "01bc8bf07fbfc184": "layer0_albedo_map",
    "2c714411035f192e": "layer0_specular_map",
    "f55db9261547732a": "layer0_emissive_map",
    "b9d846db5f446d8c": "layer0_composite_components",
    "7abbb0b3b865219b": "layer0_composite_components",
    "94ffd3d8117518b9": "layer0_composite_components",
    "1ea5d65d02ed2414": "layer0_composite_components",
}

#: Echo VR spells the base-colour slot `basecolor_map`; Lone Echo's router only
#: knows `albedo_map` and `composite_diffuse`, so the name must be translated or
#: the texture silently reaches no socket.
#: Suffixes `le_mesh.materials.CHANNEL_ROLE_SUFFIXES` can actually route.
_ROUTABLE_SUFFIXES = frozenset((
    "albedo_map", "composite_diffuse", "normal_map", "composite_normals",
    "composite_components", "composite_specular", "specular_map",
    "alpha_map", "emissive_map", "secondary_emissive_map", "opacity_map",
    "blend_mask", "flowmap_map", "detail_normal_map", "back_lighting_map",
))

_SUFFIX_TRANSLATION = {
    "basecolor_map": "albedo_map",
    "components_map": "composite_components",
}


def _translate(role: str) -> str:
    for layer_prefix in ("layer0_", "layer1_", "layer2_", "layer3_"):
        if role.startswith(layer_prefix):
            suffix = role[len(layer_prefix):]
            return layer_prefix + _SUFFIX_TRANSLATION.get(suffix, suffix)
    return role


#: Echo VR slot spellings, forward-hashed.
#:
#: `le_mesh.materials.ROLE_BY_INPUTNAME` is Lone Echo's table, and the two games
#: do NOT use the same slot names: Echo VR's most-bound slot of all is
#: `layer0_basecolor_map` (`0f1a9cf23c0e7268`, 511 corpus bindings, 404 distinct
#: textures), which appears nowhere in Lone Echo's set. Without this grid it
#: resolves to `unknown_s*`, `role_for_slot` returns None, and the BASE COLOUR
#: of most of a level is silently dropped -- measured: base_color fell to 56 of
#: 406 materials while normal/specular/roughness all sat near 270.
#:
#: Forward-hashing is safe here in a way that guessing a preimage is not: the
#: name is only accepted when `CSymbol64(name)` equals a slot hash the corpus
#: actually contains, so a wrong spelling matches nothing rather than
#: mislabelling something.
_ECHO_VR_SUFFIXES = (
    "basecolor_map", "normal_map", "components_map", "specular_map",
    "emissive_map", "opacity_map", "flowmap_map", "rimlighting_map",
    "subsurface_map", "transmittance_map", "alpha_map", "blend_mask",
    "albedo_map", "detail_normal_map", "secondary_emissive_map",
    "composite_diffuse", "composite_normals", "composite_components",
    "composite_specular",
)


def _build_echo_vr_slot_names() -> dict[str, str]:
    from le_mesh.material_scalars import symbol64

    grid: dict[str, str] = {}
    for layer in range(4):
        for suffix in _ECHO_VR_SUFFIXES:
            name = f"layer{layer}_{suffix}"
            key = normalise_hash(symbol64(name))
            grid.setdefault(key, _translate(name))
    return grid


ECHO_VR_SLOT_NAMES: dict[str, str] = _build_echo_vr_slot_names()


#: Slot/parameter names recovered from the RAD Engine authoring tree
#: (`C:\Users\lucas\Desktop\core` -- `.radmat` material sources, `.hlsl`
#: shaders, `.radattr`). Identifiers were harvested from those files, hashed with
#: CSymbol64 in both plain and `layerN_` forms, and kept ONLY where the hash
#: actually occurs in this corpus: 86 of 1316 corpus hashes, so it is a
#: supplement, not a replacement.
#:
#: Its real value is corroboration. `f55db9261547732a` resolves to `emissive_map`
#: -- a slot whose role this module had inferred from BC6H being the HDR format,
#: with no name to check it against. The authoring tree confirms that inference
#: independently. `CSymbol64("map1") == c8c33e4837720ee1` likewise confirmed,
#: from `SelectUVSet` + `:layeruvset`, that materials sample UV set 0 (§7.7).
def _load_core_names() -> dict:
    import json
    path = _ROOT / "data" / "core_names.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


CORE_NAMES: dict = _load_core_names()


def role_for_slot(slot_hash: str, names: dict | None = None) -> str | None:
    """The routable role key for a slot, or None when it cannot be established.

    Named preimage first (it carries the real layer index), then Echo VR's own
    slot-name grid, then the evidence table.  Returning None rather than a
    guess keeps an unroutable slot out of the spec instead of putting a texture
    on the wrong socket.
    """
    from le_mesh import materials as le_materials

    slot = normalise_hash(slot_hash)
    role = le_materials.role_for_inputname(slot, None, names or {})
    if not role.startswith("unknown_s"):
        return _translate(role)
    named = ECHO_VR_SLOT_NAMES.get(slot)
    if named:
        return named
    # Names recovered from the engine's own authoring tree, translated into
    # routable role keys the same way the forward-hashed grid is.
    from_core = CORE_NAMES.get(slot)
    if from_core:
        candidate = _translate(from_core if from_core.startswith("layer")
                               else f"layer0_{from_core}")
        for layer_prefix in ("layer0_", "layer1_", "layer2_", "layer3_"):
            if candidate.startswith(layer_prefix):
                suffix = candidate[len(layer_prefix):]
                if suffix in _ROUTABLE_SUFFIXES:
                    return candidate
    return SLOT_ROLE.get(slot)


def roles_from_material_table(table: dict[str, str],
                              names: dict | None = None) -> dict[str, str]:
    """`{role_key -> texture_hash}` from a material's real slot bindings.

    When two slots claim one role -- several `components` slots exist -- the
    first wins and the rest are dropped; a Principled BSDF has one socket per
    role, and inventing layer indices to keep the extras would misrepresent
    which layer the engine actually samples.
    """
    out: dict[str, str] = {}
    for slot, texture in sorted(table.items()):
        role = role_for_slot(slot, names)
        if role and role not in out:
            out[role] = texture
    return out
