"""Echo VR Scene Extractor -> a renderable `.lescatter` package.

Uses the `lone_echo_blender` data models to extract flat Echo VR files from
`H:\\pcvr-extracted` into `.lescatter` / `.lemesh` outputs that the Blender
add-on imports identically to Lone Echo.

## Materials (rewritten)

Materials and textures now come from `evr_materials`, which resolves real roles
out of `CGMaterialResourceWin10` and emits a **v2** sidecar the add-on hands to
`material_builder.build_material` verbatim -- full PBR, not base colour + normal.

Two things this replaced, both of which produced plausible-looking but wrong
output:

* **The texture "bindings" array did not exist.**  `parse_model_texture_mapping`
  read `CGTextureStreamingResourceWin10` as `[8B header][u32 count][hashes][bindings]`
  and looked for bindings at `12 + count*8`.  The verified layout puts
  `layouts_count` there, followed by 192-byte mip tables, so the "bindings" were
  mip offsets and sizes.  See `evr_texture_streaming` for the real layout.
* **`slot_idx // 4` was not a material index.**  The `% 4 -> base/normal/ORM/emissive`
  grouping built on those non-bindings assigned textures to roles by position in
  a table that has no roles in it.  Roles now come from `SShaderInputData`
  inputname hashes resolved through Lone Echo's own `role_for_inputname`.

`--legacy-materials` restores the old path for comparison; `--probe` reports what
the new one resolved without writing a package.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import os
import shutil
import math
from collections import Counter
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── MESH DECODER ──────────────────────────────────────────────────────────────
# `rad-archive-viewer/app.py` -- which loads these levels correctly -- imports a
# DIFFERENT decoder than this script did:
#
#     sys.path.insert(0, 'j:/EchoVR-Tools-Launcher/evr-mesh-importer/evr_mesh_importer')
#     import decode
#     import primary
#
# This script used `evr_mesh_importer_core.decode` from `FreshEVR/evrFileTools`
# instead.  Same function name, different implementation -- which is why passing
# the primary descriptor correctly still produced mangled geometry: the argument
# was right, the library was not.
#
# Prefer the viewer's decoder; fall back to the old one so the script still runs
# where that checkout is absent, and SAY which one is in use.
EVR_IMPORTER = str(Path(r"J:\EchoVR-Tools-Launcher\evr-mesh-importer\evr_mesh_importer"))
EVR_TOOLS = str(Path(r"C:\Users\lucas\Desktop\FreshEVR\evrFileTools"))
for _p in (EVR_IMPORTER, EVR_TOOLS):
    if _p not in sys.path:
        sys.path.append(_p)

# Add pyoodle for le_shaderset_scan
PYOODLE = str(Path(r"J:\EchoVR-Tools-Launcher\EchoVR-Cosmetics-Editor\pyoodle-main"))
if PYOODLE not in sys.path:
    sys.path.append(PYOODLE)

from le_scene_extract import SceneMesh, SceneInstance, write_package, LIGHTMAP_NONE
import evr_mesh_importer_core.level_reader as level_reader

# The decoder app.py uses, with `primary` alongside it. `primary._find_primary_data`
# has five fallback strategies for locating a primary; reimplementing only the
# hash-pair one (as an earlier version of this script did) misses the rest, so
# call the real thing when it is available.
try:
    import decode                    # noqa: F401  (viewer's implementation)
    import primary as evr_primary
    _DECODER = f"evr-mesh-importer ({EVR_IMPORTER})"
except ImportError:                  # pragma: no cover - environment dependent
    import evr_mesh_importer_core.decode as decode
    evr_primary = None
    _DECODER = f"evr_mesh_importer_core ({EVR_TOOLS}) -- FALLBACK"

import evr_level_reader
import evr_materials
import evr_model_materials
import evr_texture_streaming
from evr_resource_types import (
    find_mesh_and_primary,
    ACTOR_DATA as DIR_ACTOR_DATA,
    BVH_RESOURCE as DIR_BVH_RESOURCE,
    INSTANCE_MODEL_CR as DIR_INSTANCE_MODEL_CR,
    MODEL_CR as DIR_MODEL_CR,
    RAW_TEXTURE_PACK as DIR_RAW_TEX_PACK,
    SCENE_RESOURCE as DIR_SCENE_RESOURCE,
    STATIC_MODEL_CR as DIR_STATIC_MODEL_CR,
    STATIC_RESOURCE as DIR_STATIC_RESOURCE,
    TRANSFORM_CR as DIR_TRANSFORM_CR,
    TEXTURE_RESOURCE as DIR_TEX_RESOURCE,
    TEXTURE_STREAMING as DIR_TEX_STREAMING,
    MESH_DIRS,
    normalise_hash,
)

# Same four directories, same order, as the original list -- only the COMMENTS
# were wrong, and they are corrected in `evr_resource_types`. The real
# `CGMeshListResourceWin10` is deliberately NOT in here: `decode.extract_mesh`
# wants the raw vertex/index payload, not the stream-0 descriptor. The material
# resolver reads that descriptor separately.
MESH_DIRS = list(MESH_DIRS)

# This module's progress output uses non-ASCII marks, and a Windows console
# defaults to cp1252 -- which does not merely mangle them, it raises, killing an
# extraction after the expensive decode work is already done. Widen stdout/stderr
# to UTF-8 and never let a status line abort a run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Texture cache directories (pre-decoded PNGs from the EchoVR-Cosmetics-Editor)
TEXTURE_CACHE_DIRS = [
    Path(r"J:\EchoVR-Tools-Launcher\EchoVR-Cosmetics-Editor\Settings\texture_cache"),
    Path(r"J:\EchoVR-Tools-Launcher\Tools\Settings\texture_cache"),
    Path(r"C:\Oculus\Games\Software\Software\ready-at-dawn-echo-arena\bin\win10\Tools\Tools\Settings\texture_cache"),
]


#: `{level_hash: name}` merged from every `data/level_names_*.json`.
#:
#: A hash IS the CSymbol64 of the authored name, so these are recovered
#: preimages, not labels invented here -- `CSymbol64("mpl_arena_a")` really is
#: `576ed3f8428ebc4b`. See `scripts/evr_level_names.py` for how they are mined
#: out of the shipped executables and script DLLs.
def _load_level_names() -> dict:
    import json
    names: dict = {}
    data_dir = _ROOT / "data"
    if not data_dir.is_dir():
        return names
    for path in sorted(data_dir.glob("level_names_*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for level_hash, name in (payload.get("levels") or {}).items():
            if name:
                names.setdefault(normalise_hash(level_hash), name)
    return names


LEVEL_NAMES: dict = _load_level_names()


def level_label(level_hash) -> str:
    """`"mpl_arena_a (576ed3f8428ebc4b)"`, or just the hash when unknown."""
    canonical = normalise_hash(level_hash)
    name = LEVEL_NAMES.get(canonical)
    return f"{name} ({canonical})" if name else canonical


def resolve_level(token: str) -> str:
    """Accept a NAME or a hash on the command line; return the hash.

    Lets `--hash mpl_arena_a` work, which is the point of having the table.
    """
    text = (token or "").strip()
    canonical = normalise_hash(text)
    if canonical and all(c in "0123456789abcdef" for c in canonical):
        if canonical in LEVEL_NAMES or len(text.replace("0x", "")) >= 8:
            return canonical
    for level_hash, name in LEVEL_NAMES.items():
        if name == text:
            return level_hash
    return canonical


def sublevels_of(level_hash: str) -> list:
    """Every level that belongs with `level_hash`, itself first.

    A level's sublevels are not discoverable from its `CArchiveResource`
    closure -- checked: the lobby's closure is 2958 (type, resource) pairs and
    references exactly ONE level, itself. The relationship is carried in the
    NAMES instead, which is why `data/level_names_*.json` matters beyond
    labelling:

        mpl_lobby_b2 / mpl_lobby_b_arena / mpl_lobby_b_combat
        mpl_combat_fission{,_cargobay,_climax,_pantheon,_prologue}
        zon_fhb_hba_{exterior,commons,tram,security}

    Grouping rule: drop the last `_`-segment (and any trailing digits) to get a
    stem, then take every named level sharing it. An unnamed level has no
    discoverable siblings and is returned alone, which is honest rather than
    guessy.
    """
    import re
    canonical = normalise_hash(level_hash)
    name = LEVEL_NAMES.get(canonical)
    if not name:
        return [canonical]

    # Only two stems are safe. Anything shorter over-groups: dropping the last
    # segment of `mpl_combat_fission` gives `mpl_combat`, which swallows dyson,
    # gauss, combustion and the celebration rooms -- 14 unrelated levels.
    #   1. the name itself  -> `mpl_combat_fission` + `mpl_combat_fission_*`
    #   2. the name with a trailing number stripped, which is how a "main"
    #      level is spelled beside its parts (`mpl_lobby_b2` sits with
    #      `mpl_lobby_b_arena` / `mpl_lobby_b_combat`).
    stems = {name}
    trimmed = re.sub(r"\d+$", "", name).rstrip("_")
    if trimmed and trimmed != name and trimmed.count("_") >= 2:
        stems.add(trimmed)

    group = {canonical}
    for other_hash, other_name in LEVEL_NAMES.items():
        if not other_name:
            continue
        for stem in stems:
            if other_name == stem or other_name.startswith(stem + "_"):
                group.add(normalise_hash(other_hash))
                break

    root = Path(_LAST_ROOT[0] or ".")
    ordered = [canonical] + sorted(g for g in group if g != canonical)
    present = [g for g in ordered if (root / DIR_ACTOR_DATA / g).exists()]
    return present or [canonical]


#: Extract root of the run in progress, so `sublevels_of` can check existence.
_LAST_ROOT = [None]


def find_texture_cache() -> Path | None:
    """Find the first valid texture_cache directory."""
    for d in TEXTURE_CACHE_DIRS:
        if d.exists() and any(d.glob("*.png")):
            return d
    return None


def parse_model_texture_mapping(pcvr_dir: Path, model_hash: str) -> dict | None:
    """Per-model texture inventory from CGTextureStreamingResourceWin10.

    ⚠ **This no longer returns a `bindings` key, because the file has none.**
    The previous implementation synthesised one by reading (u32, f32) pairs at
    `12 + tex_count * 8` -- an offset that the verified layout says holds
    `layouts_count` followed by 192-byte `STextureStreamData` mip tables.  Those
    "bindings" were mip byte-offsets and sizes reinterpreted as slot indices.

    Callers that want role information must use `evr_materials`, which reads it
    from `CGMaterialResourceWin10`'s `SShaderInputData` binds.  See
    `evr_texture_streaming` for the layout and its provenance.

    Returns `{"textures": [...], "packfile": hash, "streamed": [...]}` or None.
    """
    try:
        resource = evr_texture_streaming.load_for_model(pcvr_dir, model_hash)
    except evr_texture_streaming.StreamingParseError as exc:
        print(f"  WARN streaming resource for {model_hash} did not parse: {exc}")
        return None
    if resource is None:
        return None
    return {
        "textures": list(resource.textures),
        "packfile": resource.packfilename,
        "streamed": resource.streamed_textures(),
    }


def reconstruct_dds(tex_hash: str, pcvr_dir: Path, out_path: Path) -> bool:
    """Reconstruct a proper DDS file from cgtextureresourceWin10 + RawTexturePackfileWin10.
    
    The cgtextureresourceWin10 file contains:
      0x00-0x3F: padding (0xFFFFFFFF)
      0x40-0xFF: high-quality RawTexturePackfileWin10 hashes (8 bytes each, terminated by 0xFFFFFFFF)
      0x100+:    DDS header + low-quality mipmap pixel data
    
    The high-res data in RawTexturePackfileWin10 is prepended to build the full DDS.
    """
    tex_res = pcvr_dir / DIR_TEX_RESOURCE / tex_hash
    if not tex_res.exists():
        return False
    
    low_data = tex_res.read_bytes()
    if len(low_data) < 256 + 128:
        return False
    
    # Extract high quality hashes from header (offset 0x40)
    high_hashes = []
    for i in range(0x40, 0x100, 8):
        chunk = low_data[i:i+8]
        if chunk == b'\xff' * 8:
            break
        h = struct.unpack('<Q', chunk)[0]
        high_hashes.append(f"{h:016x}")
    
    # Read DDS header (try DX10 148 bytes first, then standard 128)
    dds_header = bytearray(low_data[256:256+148])
    if dds_header[:4] != b'DDS ':
        dds_header = bytearray(low_data[256:256+128])
        if dds_header[:4] != b'DDS ':
            return False
    
    header_len = len(dds_header)
    
    orig_height = struct.unpack_from('<I', dds_header, 12)[0]
    orig_width = struct.unpack_from('<I', dds_header, 16)[0]
    orig_mips = struct.unpack_from('<I', dds_header, 28)[0]
    
    # Read high quality payloads (stored smallest to largest, prepend largest first)
    raw_dir = pcvr_dir / DIR_RAW_TEX_PACK
    high_payloads = []
    for h in reversed(high_hashes):
        hp = raw_dir / h
        if hp.exists():
            high_payloads.append(hp.read_bytes())
    
    # Calculate new dimensions
    num_extra_mips = len(high_payloads)
    new_width = orig_width * (2 ** num_extra_mips)
    new_height = orig_height * (2 ** num_extra_mips)
    new_mips = orig_mips + num_extra_mips
    
    # Update DDS header with new dimensions
    struct.pack_into('<I', dds_header, 12, new_height)
    struct.pack_into('<I', dds_header, 16, new_width)
    struct.pack_into('<I', dds_header, 28, new_mips)
    
    # Handle array textures - patch to single slice
    if len(dds_header) >= 148 and dds_header[84:88] == b'DX10':
        array_size = struct.unpack_from('<I', dds_header, 140)[0]
        if array_size > 1:
            struct.pack_into('<I', dds_header, 140, 1)
    
    # Write reconstructed DDS
    with open(out_path, 'wb') as f:
        f.write(dds_header)
        for hp in high_payloads:
            f.write(hp)
        f.write(low_data[256 + header_len:])
    
    return True


def resolve_and_copy_texture(tex_hash: str, texture_cache: Path | None,
                             pcvr_dir: Path, out_tex_dir: Path) -> str | None:
    """Resolve a texture hash to a file, copying/reconstructing it into out_tex_dir.
    
    Priority:
    1. Pre-decoded PNG in texture_cache -> copy as .png
    2. Reconstruct DDS from cgtextureresourceWin10 + RawTexturePackfileWin10 -> .dds
    """
    # Check if we already wrote this texture
    for ext in [".png", ".dds"]:
        existing = out_tex_dir / f"{tex_hash}{ext}"
        if existing.exists() and existing.stat().st_size > 100:
            return f"textures/{tex_hash}{ext}"
    
    # Check texture cache first (pre-decoded PNGs)
    if texture_cache:
        png_src = texture_cache / f"{tex_hash}.png"
        if png_src.exists():
            dst = out_tex_dir / f"{tex_hash}.png"
            shutil.copy2(str(png_src), str(dst))
            return f"textures/{tex_hash}.png"
    
    # Reconstruct DDS from engine format
    dds_dst = out_tex_dir / f"{tex_hash}.dds"
    if reconstruct_dds(tex_hash, pcvr_dir, dds_dst):
        return f"textures/{tex_hash}.dds"
    
    return None


def build_materials_for_model(model_hash: str, mapping: dict,
                              texture_cache: Path | None, pcvr_dir: Path,
                              mat_idx_start: int, out_tex_dir: Path) -> list[dict]:
    """⚠ LEGACY -- v1 entries from a texture grouping that was never real.

    Kept only behind `--legacy-materials`, for A/B comparison against the new
    path.  Do not extend it.

    The grouping it implements (`g_idx = slot_idx // 4`, then
    `slot % 4 -> base/normal/ORM/emissive`) rests on a `bindings` array that
    `parse_model_texture_mapping` used to synthesise out of the mip-layout
    table.  There is no slot numbering in that data, so the base-colour/normal
    assignment it produces is positional coincidence.  It also emits v1, which
    the add-on explicitly documents as losing alpha, render mode, emission,
    specular, roughness and blend masks.

    `mapping` must now supply a `bindings` key explicitly; the corrected
    `parse_model_texture_mapping` no longer invents one, so this function
    degrades to sequential ordering, which is what it always effectively was.
    """
    if not mapping or not mapping.get("textures"):
        return []
        
    textures = mapping["textures"]
    bindings = mapping.get("bindings", [])
    
    # If bindings are missing, fallback to sequential
    if not bindings:
        for i in range(len(textures)):
            bindings.append({"slot_idx": i, "texture_idx": i})
            
    # Group textures by material (g_idx)
    grouped = {}
    for bind in bindings:
        tex_idx = bind["texture_idx"]
        if 0 <= tex_idx < len(textures):
            g_idx = bind["slot_idx"] // 4
            slot = bind["slot_idx"] % 4
            grouped.setdefault(g_idx, {})[slot] = textures[tex_idx]
            
    materials = []
    # Sort by g_idx to ensure deterministic output
    for g_idx, slots in sorted(grouped.items()):
        mat_idx = mat_idx_start + g_idx
        
        base_tex = slots.get(0)
        normal_tex = slots.get(1)
        
        # Build the material entry in Lone Echo v1 format
        entry = {
            "matidx": mat_idx,
            "shdidx": 0,
            "material_hash": f"{model_hash}_{g_idx}",
            "mattype": 1,
            "base_color": [0.5, 0.5, 0.5],
            "double_sided": False,
        }
        
        # Base color texture
        if base_tex:
            rel_path = resolve_and_copy_texture(base_tex, texture_cache, pcvr_dir, out_tex_dir)
            if rel_path:
                entry["basecolor_texture"] = base_tex
                entry["basecolor_dds"] = rel_path
                entry["basecolor_role"] = "layer0_diffuse_map"
            else:
                entry["basecolor_texture"] = None
                entry["basecolor_dds"] = None
                entry["basecolor_role"] = None
        else:
            entry["basecolor_texture"] = None
            entry["basecolor_dds"] = None
            entry["basecolor_role"] = None
        
        # Normal texture  
        if normal_tex:
            rel_path = resolve_and_copy_texture(normal_tex, texture_cache, pcvr_dir, out_tex_dir)
            if rel_path:
                entry["normal_texture"] = normal_tex
            else:
                entry["normal_texture"] = None
        else:
            entry["normal_texture"] = None
        
        entry["owning_archive"] = None
        
        materials.append(entry)
    
    return materials


#: Which decode path each model took. `primary_described` is the good one;
#: `heuristic` means the vertex format was GUESSED and the mesh is probably
#: mangled. Printed at the end of every run.
_DECODE_PATHS: dict = {}
_NO_PRIMARY: set = set()
#: Models whose decode failed, so it is attempted once rather than per instance.
_FAILED_MODELS: set = set()
#: Global LOD-group id counter (SceneInstance.lod_group). Reset per scene
#: export so ids stay small and stable run to run.
_NEXT_LOD_GROUP = [0]

#: How many submeshes had their UV0 replaced with the UNORM16 set.
_UV_REPAIRED = [0]

#: Extra submeshes created by splitting merged draw sections.
_DRAWS_SPLIT = [0]

#: Corpus material hashes, set once by the caller so the decode
#: cache can resolve draw records without re-scanning the extract.
_MATERIAL_HASHES = [set()]

#: `{model_hash -> {submesh index, ...}}` for submeshes produced by splitting a
#: merged draw. These are SECTIONS of one draw sequence -- the engine draws them
#: together -- so they must never be LOD-grouped against each other. They share a
#: bounding box (they are parts of one object), which is exactly what the bbox
#: clustering would mistake for a LOD chain, hiding all but one at LOD 0.
_SPLIT_PIECES: dict = {}
#: Parallel to `_SPLIT_PIECES`: each piece's (lo, hi) VERTEX range inside the
#: submesh it was split out of. Needed to slice anything the engine stores per
#: unsplit mesh, such as the per-instance lightmap UV run.
_SPLIT_RANGES: dict = {}

#: Models re-decoded from their own tables (see `evr_structural_decode`).
_STRUCTURAL_USED = [0]
#: Off by default -- `--structural` opts in until it is proven on both levels.
_STRUCTURAL_FALLBACK = [False]
#: `--max-texture`, applied when textures are written.
_MAX_TEXTURE = [0]
#: `--texture-divisor`: reduce every texture by this factor relative to its own
#: native size (2 = half resolution), unlike the absolute `--max-texture` cap.
_TEXTURE_DIVISOR = [1]


def _submesh_bbox(verts):
    if not verts:
        return None
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    return ((min(xs), min(ys), min(zs)),
            (max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs)))


def _bbox_close(a, b, *, rel_tol=0.05, abs_tol=0.01):
    (amin, asize), (bmin, bsize) = a, b
    for k in range(3):
        scale = max(abs(asize[k]), abs(bsize[k]), abs_tol)
        if abs(amin[k] - bmin[k]) > rel_tol * scale + abs_tol:
            return False
        if abs(asize[k] - bsize[k]) > rel_tol * scale + abs_tol:
            return False
    return True


#: A UV0 whose coordinates run beyond this is not a base-map channel.
#: `13a91654991729e4` submesh 0 reads `u[-47.4, 3.0]` from UV0 -- 50 tiles across
#: a 31-unit surface -- while its UNORM16 set reads a clean `u[0.001, 0.999]`.
UV_SANE_LIMIT = 8.0

#: `SVertexElement.type` for a 16-bit normalised pair. NOT a half float: read as
#: half it yields +-60000 garbage; as `u16 / 65535` it yields exactly `[0, 1]`.
VTYPE_UNORM16 = 3
VTYPE_FLOAT32 = 8
SEM_TEXCOORD = 4

#: Field offsets inside the 336-byte vertex-buffer record (from the decoder's
#: own `_extract_metadata_meshes`).
VB_STRIDE = 336
VB_BASE_OFFSET = 0x128
VB_STREAM0_SIZE = 0x130
VB_VERTEX_COUNT = 0x13C


def _vertex_buffer_records(primary: bytes, vertex_counts: list) -> list:
    """`[(base_offset, stream0_size, vertex_count, attr_bytes), ...]`.

    Located by requiring the records' vertex-count column to equal the decoded
    submeshes' counts in order -- the same anchor the draw-record lookup uses,
    because a header walk does not predict where these arrays sit.
    """
    n = len(vertex_counts)
    if not n:
        return []
    for base in range(0, max(0, len(primary) - VB_STRIDE * n) + 1, 4):
        try:
            counts = [struct.unpack_from("<I", primary,
                                         base + k * VB_STRIDE + VB_VERTEX_COUNT)[0]
                      for k in range(n)]
        except struct.error:
            break
        if counts != vertex_counts:
            continue
        out = []
        for k in range(n):
            record = base + k * VB_STRIDE
            out.append((
                struct.unpack_from("<I", primary, record + VB_BASE_OFFSET)[0],
                struct.unpack_from("<I", primary, record + VB_STREAM0_SIZE)[0],
                counts[k],
                primary[record:record + 288],
            ))
        return out
    return []


def _unorm16_texcoord_offset(attr: bytes) -> int | None:
    """Byte offset of the UNORM16 TEXCOORD element, or None.

    Elements are 8 bytes: `semantic, offset, type, count, uvindex, size, stream`.
    """
    for k in range(36):
        element = attr[k * 8:(k + 1) * 8]
        if len(element) < 7:
            break
        semantic, offset, vtype, count = element[0], element[1], element[2], element[3]
        if semantic == 0xFF:
            break
        if (semantic == SEM_TEXCOORD and vtype == VTYPE_UNORM16
                and count == 2):
            return offset
    return None


def _repair_uvs(pcvr_dir: Path, model_hash: str, results: list) -> int:
    """Replace UV0 with the UNORM16 set on submeshes whose UV0 is not a base map.

    `decode.extract_mesh` always takes UV as float2 at +8. That is right for most
    meshes -- `ff5afb4e96897159` reads `u[0, 2]` there and renders correctly --
    but a mesh whose UV0 carries tiling coordinates then maps its base texture
    across ~50 repeats. Those meshes declare a second, UNORM16 TEXCOORD element
    which is the actual base-map channel.

    Only submeshes that FAIL the sanity check are touched, so meshes that are
    already correct keep the exact coordinates they have today.
    """
    counts = [len(rg[0]) for rg in results if rg]
    if len(counts) != len(results):
        return 0
    gpu_path, primary_path = find_mesh_and_primary(pcvr_dir, model_hash)
    if gpu_path is None or primary_path is None:
        return 0
    try:
        gpu = gpu_path.read_bytes()
        primary = primary_path.read_bytes()
    except OSError:
        return 0

    records = _vertex_buffer_records(primary, counts)
    if len(records) != len(results):
        return 0

    repaired = 0
    for index, (result, record) in enumerate(zip(results, records)):
        uvs = result[2] if len(result) > 2 else None
        if not uvs:
            continue
        extreme = max(max(abs(u), abs(v)) for u, v in uvs)
        if extreme <= UV_SANE_LIMIT:
            continue
        base_offset, stream0_size, vertex_count, attr = record
        if not vertex_count or stream0_size % vertex_count:
            continue
        stride = stream0_size // vertex_count
        offset = _unorm16_texcoord_offset(attr)
        if offset is None or offset + 4 > stride:
            continue
        replacement = []
        for j in range(vertex_count):
            at = base_offset + j * stride + offset
            if at + 4 > len(gpu):
                replacement = []
                break
            u, v = struct.unpack_from("<HH", gpu, at)
            replacement.append((u / 65535.0, v / 65535.0))
        if len(replacement) != len(uvs):
            continue
        # The decoder hands back tuples, so rebuild rather than assign in place
        # (item assignment raises and the caller's guard swallowed it silently).
        results[index] = (result[0], result[1], replacement) + tuple(result[3:])
        repaired += 1
    return repaired


def _split_submesh_draws(result, run):
    """Split one decoded submesh into its constituent DRAWS.

    `decode.extract_mesh` concatenates consecutive draws that share a vertex
    buffer, so one "submesh" can be several draw sections with DIFFERENT
    materials. `b4bf0b8ba02fbcbd` decodes 9 draws as 4 submeshes because
    `167+22+727+98+369 == 1383` -- five sections, five materials, collapsed onto
    one mesh that can only carry a single material. That is why LOD 0 renders
    wrong while lower LODs (single-draw) render right.

    Draws occupy CONSECUTIVE vertex ranges, so the split is a partition: assign
    each face to the range holding its vertices and rebase the indices. A face
    straddling a boundary would mean the ranges are not really draws, so it
    aborts rather than guessing.

    Returns `[(verts, faces, uvs, *rest), ...]` or `[]` to leave the submesh be.
    """
    verts, faces = result[0], result[1]
    uvs = result[2] if len(result) > 2 else None
    tail = tuple(result[3:])

    bounds = []
    start = 0
    for count, _material in run:
        bounds.append((start, start + count))
        start += count
    if start != len(verts):
        return []

    buckets = [[] for _ in bounds]
    for face in faces:
        placed = False
        for slot, (lo, hi) in enumerate(bounds):
            if all(lo <= i < hi for i in face):
                buckets[slot].append(face)
                placed = True
                break
        if not placed:
            return []          # a face spanning two ranges => not draw bounds

    out = []
    for slot, (lo, hi) in enumerate(bounds):
        piece_faces = [tuple(i - lo for i in f) for f in buckets[slot]]
        piece_uvs = uvs[lo:hi] if uvs else None
        out.append((verts[lo:hi], piece_faces, piece_uvs) + tail)
    return out


def _group_submeshes_by_lod(results, origins=None):
    """Cluster a model's decoded submeshes into LOD groups by bounding box.

    `decode.extract_mesh` returns every LOD level of a part as its own
    submesh, indistinguishable from a genuinely separate part -- its own
    comment says so outright ("LOD trimming is handled later by the
    importer"). Without this, every LOD level of every part renders
    simultaneously, stacked, at every placement of the model.

    Submeshes whose bounding box matches (same physical part, just fewer
    triangles) cluster together and are ordered by face count, most faces
    (finest) first -> level 0. A part with no LOD siblings is its own
    singleton cluster, reported as `cluster_size == 1` so the caller can map
    it to `lod_group = -1` (ungrouped: always rendered, matching the v1/v2
    no-LOD behaviour for parts that genuinely have only one representation).

    Verified on `ff5afb4e96897159` (576ed3f8428ebc4b instance i277's model):
    9 submeshes cluster into a 4-level main body (1968/614/328/184 faces,
    matching bbox to within ~2.6%), a 4-level small part (176/48/14/14
    faces), and one 2-triangle outlier (likely an impostor/collision card).

    `origins[i]` is the index of the submesh piece `i` was split out of (see
    `_split_submesh_draws`). Pieces sharing an origin are draw sections of ONE
    submesh -- they are drawn together and are not LODs of each other, so they
    are never clustered together. Pieces from DIFFERENT origins still cluster
    normally, which is what lets a split LOD 0 pair up with its split LOD 1.
    Treating every split piece as its own singleton instead (the previous rule)
    left both levels permanently visible, stacked, at every placement.

    Returns `[(cluster_index, level, cluster_size), ...]`, one entry per
    submesh, in `results` order. `cluster_index` is LOCAL to this call (the
    caller assigns global `lod_group` ids).
    """
    entries = []
    for res_group in results:
        if not res_group:
            entries.append(None)
            continue
        verts, faces = res_group[0], res_group[1]
        entries.append((_submesh_bbox(verts), len(faces)))

    # No split => every piece is its own submesh, so no two share an origin and
    # the constraint below is vacuous.
    if not origins or len(origins) != len(results):
        origins = list(range(len(results)))

    clusters: list = []       # list of [submesh_index, ...]
    cluster_bbox: list = []   # representative bbox per cluster, parallel to clusters
    for i, entry in enumerate(entries):
        if entry is None:
            clusters.append([i])
            cluster_bbox.append(None)
            continue
        bbox, _nfaces = entry
        placed = False
        for c, rep in enumerate(cluster_bbox):
            if rep is None or not _bbox_close(bbox, rep):
                continue
            if any(origins[m] == origins[i] for m in clusters[c]):
                continue          # a sibling draw section, not a coarser LOD
            clusters[c].append(i)
            placed = True
            break
        if not placed:
            clusters.append([i])
            cluster_bbox.append(bbox)

    out = [None] * len(results)
    for c, members in enumerate(clusters):
        ordered = sorted(members, key=lambda i: -(entries[i][1] if entries[i] else 0))
        for level, i in enumerate(ordered):
            out[i] = (c, level, len(members))
    return out


def _load_primary(mesh_file, primary_file):
    """Primary bytes for a GPU blob, preferring the viewer's own finder.

    `primary._find_primary_data` implements five strategies (GPU/Primary folder
    convention, flat hash sibling, nested sibling, recursive walk, known pcvr
    roots).  `find_mesh_and_primary` in `evr_resource_types` implements only the
    flat hash-pair one, which is right for `H:\\pcvr-extracted` but not for the
    archive layouts the viewer also handles.  Use the real thing when present.
    """
    if evr_primary is not None:
        try:
            data = evr_primary._find_primary_data(str(mesh_file))
            if data:
                return data
        except Exception:
            pass
    if primary_file is not None:
        return primary_file.read_bytes()
    return None


def _warn_no_primary(model_hash: str) -> None:
    """Say, once per model, that no primary descriptor was found for it."""
    if model_hash in _NO_PRIMARY:
        return
    _NO_PRIMARY.add(model_hash)
    if len(_NO_PRIMARY) <= 8:
        print(f"  NOTE {model_hash}: no primary descriptor -- vertex format "
              f"will be guessed, geometry may be wrong")
    elif len(_NO_PRIMARY) == 9:
        print("  NOTE ... further missing-primary notes suppressed")


#: Models already reported as falling back to positional material ordering.
#: Deduplicated so a large level does not bury the real warnings in repeats.
_ORDERED_MATERIAL_MODELS: set = set()


def _warn_ordered_materials(model_hash: str) -> None:
    """Say, once per model, that its draws were matched to materials by ORDER.

    This is worth a line each time it happens: it is the difference between
    "the mesh record told us" and "we assumed draw i uses material i". The
    latter is right for single-material models and a guess for the rest.
    """
    if model_hash in _ORDERED_MATERIAL_MODELS:
        return
    _ORDERED_MATERIAL_MODELS.add(model_hash)
    if len(_ORDERED_MATERIAL_MODELS) <= 10:
        print(f"  NOTE {model_hash}: draws matched to materials by order "
              f"(no CGMeshData link)")
    elif len(_ORDERED_MATERIAL_MODELS) == 11:
        print("  NOTE ... further positional-ordering notes suppressed")


#: `{model_hash -> (results, path_label) | None}`. The materials phase now
#: needs a real decode too (LOD grouping, see below), and the geometry phase
#: decodes the same models again later -- this is what stops that from being
#: two full decodes per model. `None` caches a failed decode.
_DECODE_CACHE: dict = {}


def _decode_model_cached(pcvr_dir: Path, mhash: str):
    """Decode one model's geometry, memoized across the whole scene export.

    Returns `(results, path_label)` or `(None, None)` when the model has no
    GPU blob, no primary, or decodes to nothing. Populates `_NO_PRIMARY` /
    `_DECODE_PATHS` exactly as the old inline geometry-phase code did, so
    calling this from the materials phase does not change those diagnostics.
    """
    if mhash in _DECODE_CACHE:
        return _DECODE_CACHE[mhash]

    mesh_file, primary_file = find_mesh_and_primary(pcvr_dir, mhash)
    if not mesh_file:
        _DECODE_CACHE[mhash] = (None, None)
        return None, None

    primary_data = _load_primary(mesh_file, primary_file)
    if primary_data is None:
        _warn_no_primary(mhash)

    results_tuple = decode.extract_mesh(
        str(mesh_file), primary_data=primary_data, auto_find_primary=False)
    if not results_tuple:
        _DECODE_CACHE[mhash] = (None, None)
        return None, None

    results, path_label = results_tuple[0], (
        results_tuple[1] if len(results_tuple) > 1 else "?")
    _DECODE_PATHS[path_label] = _DECODE_PATHS.get(path_label, 0) + 1
    if not results:
        _DECODE_CACHE[mhash] = (None, None)
        return None, None

    # STRUCTURAL FALLBACK: when the scanner's output contradicts the model's
    # own declared tables, decode from those tables instead.
    #
    # `decode.extract_mesh` finds meshes by pattern-scanning the GPU blob, which
    # fails silently on some models: `c48412e86560721e` declares 10 meshes and
    # the scanner returned 2, one of them 7453 vertices with 32 faces (the rest
    # orphaned). `evr_structural_decode` reads the vertex/index buffer tables
    # directly and recovers all 10.
    #
    # Only fires when the scan is DEMONSTRABLY wrong -- fewer submeshes than the
    # declared mesh count, or a submesh whose face count cannot describe its
    # vertices -- so models the scanner handles correctly are left exactly as
    # they are. `576ed3f8428ebc4b` is verified 1:1 on the scan path and must not
    # move.
    if _STRUCTURAL_FALLBACK[0]:
        try:
            import evr_structural_decode as structural
            declared = structural.table_counts(
                (structural.resource_path(pcvr_dir,
                                          structural.INSTANCED_MODEL_RESOURCE, mhash)
                 or structural.resource_path(pcvr_dir,
                                             structural.MESH_LIST_RESOURCE, mhash)
                 ).read_bytes())
            n_meshes = declared[structural.HDR_VERTEXBUFFERS] if len(declared) > 2 else 0
            broken = (not results
                      or (n_meshes and len(results) < n_meshes)
                      or any(rg and len(rg[1]) * 6 < len(rg[0]) for rg in results))
            if broken and n_meshes:
                rebuilt, note = structural.decode(pcvr_dir, mhash)
                if rebuilt and len(rebuilt) >= len(results or []):
                    results = [(v, f, uv) for v, f, uv in rebuilt]
                    path_label = "structural"
                    _STRUCTURAL_USED[0] += 1
        except Exception:
            pass

    # SPLIT MERGED DRAWS before anything else sees `results`.
    #
    # The decoder concatenates consecutive draws sharing a vertex buffer, so a
    # single "submesh" can be several sections with different materials. Split
    # them here, once, so LOD grouping and material assignment both operate on
    # REAL draws. Requires an exact vertex-count partition; anything that does
    # not partition cleanly is left untouched.
    if results:
        try:
            counts = [len(r[0]) for r in results if r]
            if len(counts) == len(results):
                draws = evr_model_materials.draw_records(
                    pcvr_dir, mhash, _MATERIAL_HASHES[0], counts)
                runs = (evr_model_materials.split_runs(draws, counts)
                        if draws else [])
                if runs and any(len(run) > 1 for run in runs):
                    # Record each piece's ORIGIN submesh as it is appended.
                    # Deriving it afterwards from `len(run)` desynchronises the
                    # moment `_split_submesh_draws` declines a split (it returns
                    # [] and one entry is appended, not `len(run)`), which walks
                    # the origins off the end and mislabels every later piece.
                    rebuilt: list = []
                    origins: list = []
                    ranges: list = []
                    for source, (result, run) in enumerate(zip(results, runs)):
                        pieces = (_split_submesh_draws(result, run)
                                  if len(run) > 1 else [])
                        if not pieces:
                            pieces = [result]
                        # A piece's VERTEX RANGE inside the submesh it came
                        # from. Anything keyed to the engine's own mesh index --
                        # the per-instance lightmap UV run especially -- is
                        # indexed by the unsplit submesh, so a piece has to say
                        # both which submesh it belongs to and which slice of it
                        # it is.
                        cursor = 0
                        for piece in pieces:
                            span = len(piece[0])
                            ranges.append((cursor, cursor + span))
                            cursor += span
                        rebuilt.extend(pieces)
                        origins.extend([source] * len(pieces))
                    if len(rebuilt) > len(results):
                        _DRAWS_SPLIT[0] += len(rebuilt) - len(results)
                        _SPLIT_PIECES[mhash] = origins
                        _SPLIT_RANGES[mhash] = ranges
                        results = rebuilt
        except Exception:
            pass

    # ⛔ Do NOT swap UV0 for the UNORM16 TEXCOORD set (`_repair_uvs`, kept below
    # but DISABLED). The reasoning looked airtight -- `13a91654991729e4`
    # submesh 0 reads `u[-47.4, 3.0]` from UV0 while its UNORM16 element reads a
    # clean `u[0.001, 0.999]`, and the sibling submesh's `v` matches between the
    # two channels -- but swapping it made 30 submeshes visibly WORSE
    # (user-verified) and fixed nothing.
    #
    # The reason: that UNORM16 set is the LIGHTMAP/atlas channel (UV1). It
    # ALWAYS spans [0,1] because every face is packed into the atlas
    # individually, which is exactly what makes it look "correct" by a range
    # test and exactly what makes it wrong as a base map -- the render comes out
    # as per-face shards. A [0,1] range is evidence of atlas packing, not of
    # being the right channel.
    #
    # So UV0 at +8 IS the base map, and `u[-47.4, 3.0]` is a genuine 50-tile
    # coordinate set. Whatever is wrong with that mesh is NOT the UV channel.
    _DECODE_CACHE[mhash] = (results, path_label)
    return results, path_label


def extract_evr_scene(scene_hash, pcvr_dir: Path, out_dir: Path,
                      *, legacy_materials: bool = False,
                      hash_lookup: Path | None = None,
                      probe_only: bool = False,
                      where_only: bool = False,
                      geo_only: bool = False):
    # Reset ONCE per scene, at the top -- not between the materials and
    # geometry phases. The materials phase now decodes geometry too (LOD
    # grouping, see the LOD-aware `material_rank` below), and `_DECODE_CACHE`
    # is what stops the geometry phase from decoding the same models again;
    # clearing between phases would both drop that cache and undercount
    # `_DECODE_PATHS` for every model whose only decode happened early.
    _DECODE_CACHE.clear()
    _DECODE_PATHS.clear()
    _NO_PRIMARY.clear()
    _FAILED_MODELS.clear()
    _NEXT_LOD_GROUP[0] = 0
    _UV_REPAIRED[0] = 0
    _DRAWS_SPLIT[0] = 0
    _SPLIT_PIECES.clear()
    _SPLIT_RANGES.clear()
    _STRUCTURAL_USED[0] = 0

    # A scene may be SEVERAL levels merged (`--full`): a main level plus its
    # sublevels, which the engine loads together but which ship as separate
    # resources. Everything below accumulates across the group, so the rest of
    # the pipeline sees one scene and emits one package.
    scene_group = [normalise_hash(h) for h in (
        scene_hash if isinstance(scene_hash, (list, tuple)) else [scene_hash])]
    scene_hash = scene_group[0]
    if len(scene_group) > 1:
        print(f"Merging {len(scene_group)} levels: "
              + ", ".join(level_label(h) for h in scene_group))

    actors = []
    nodeid_set = set()
    found_any = False
    for member in scene_group:
        actor_path = None
        for ext in ["", ".bin"]:
            cand = pcvr_dir / DIR_ACTOR_DATA / (member + ext)
            if cand.exists():
                actor_path = cand
                break
        if not actor_path:
            print(f"  ⚠ no actor data for {level_label(member)} -- skipped")
            continue
        found_any = True
        with open(actor_path, 'rb') as f:
            member_info = level_reader.parse_actor_data(f.read())
        member_actors = member_info.get('actors', [])
        actors.extend(member_actors)
        nodeid_set.update(a['nodeid'] for a in member_actors)

    if not found_any:
        print(f"No scene data found for level {scene_hash}")
        return False
    
    # `CStaticInstanceModelCR` is a THIRD per-actor component that names a
    # model, distinct from CModelCR/CInstanceModelCR above -- and also
    # distinct from the bulk `CGStaticInstanceResourceWin10` scatter system
    # this script already reads via `evr_level_reader` (same type hash,
    # unrelated data: that system's packed instance records, this one's
    # per-nodeid model binding). `engine.js` checks exactly these three CR
    # types in this order when resolving an actor's mesh. Without this one,
    # 732 of this level's 1459 actors (69% -> only 31% coverage) had no model
    # hash at all and were silently dropped -- most of a level's static
    # architecture lives here, not in CModelCR/CInstanceModelCR.
    actor_map = {}
    for member, h in ((m, t) for m in scene_group
                      for t in (DIR_MODEL_CR, DIR_INSTANCE_MODEL_CR,
                                DIR_STATIC_MODEL_CR)):
        p = pcvr_dir / h / member
        if not p.exists():
            p = p.with_suffix(".bin")
        if p.exists():
            with open(p, 'rb') as f:
                if h == DIR_MODEL_CR:
                    func, type_name = level_reader.parse_model_cr, h
                elif h == DIR_STATIC_MODEL_CR:
                    func, type_name = level_reader.parse_instance_model_cr, "CStaticInstanceModelCR"
                else:
                    func, type_name = level_reader.parse_instance_model_cr, h
                actor_map.update(func(f.read(), nodeid_set, type_name).get('actors', {}))

    # ─── PARSE STATIC INSTANCES (per member, merged) ─────────────────────────
    #
    # `static_instances[i].model_index` indexes THAT member's `static_models`,
    # so merging requires rebasing the index by however many slots are already
    # in the merged list. Without the rebase every sublevel's scatter would
    # resolve against the first level's model table.
    static_models = []
    static_instances = []

    for member in scene_group:
        def _res(kind):
            q = pcvr_dir / kind / member
            return q if q.exists() else q.with_suffix(".bin")

        p_smodel, p_transform = (_res(DIR_STATIC_MODEL_CR),
                                 _res(DIR_TRANSFORM_CR))
        if not (p_smodel.exists() and p_transform.exists()):
            continue

        # CSIMCR gives (entity, model) per instance; CTransformCR turns each
        # entity into a world TRS. Nothing here is quantized, so the level's
        # BVH bounds are not consulted at all.
        pairs = evr_level_reader.parse_static_instance_models(
            p_smodel.read_bytes())
        raw_sinst = evr_level_reader.parse_static_instances(
            p_smodel.read_bytes(), p_transform.read_bytes())

        base = len(static_models)
        static_models.extend(model for _entity, model in pairs)
        for inst in raw_sinst:
            inst.model_index += base
            inst.level = member
            static_instances.append(inst)
        missing = len(pairs) - len(raw_sinst)
        print(f"Static [{level_label(member)}]: {len(pairs)} instances, "
              f"{len(raw_sinst)} placed"
              + (f", {missing} dropped (no transform row)" if missing else ""))

    # Output directory structure: J:\EchoVRModels\scenes\<level name>\
    # `--full` merges a level with its sublevels, so it gets its own tree --
    # a merged scene is not the same artefact as the bare level and must not
    # overwrite it.
    #
    # Directories are named by the level's AUTHORED name, not its hash. The
    # hash IS the CSymbol64 of that name, so the name is the real identifier
    # and the hash is what you fall back to when the preimage was never
    # recovered (see evr_level_names.py) -- `mpl_arena_a`, not
    # `576ed3f8428ebc4b`.
    label = LEVEL_NAMES.get(scene_hash) or scene_hash
    if len(scene_group) > 1:
        scene_out_dir = out_dir / "Scenes_Full" / label
    else:
        scene_out_dir = out_dir / "scenes" / label
    scene_out_dir.mkdir(exist_ok=True, parents=True)
    tex_out_dir = scene_out_dir / "textures"
    tex_out_dir.mkdir(exist_ok=True)
    
    # Find texture cache
    texture_cache = find_texture_cache()
    if texture_cache:
        print(f"Using texture cache: {texture_cache}")
    else:
        print("WARNING: No texture_cache found. Textures will not be resolved to PNGs.")
    
    # Track meshes and materials
    all_meshes = []
    all_instances = []
    mesh_idx_map = {}
    
    # Build materials per unique model hash
    global_materials = []
    model_mat_map = {}  # model_hash -> (mat_idx_start, num_materials)
    
    # First pass: collect unique model hashes
    unique_models = set()
    for actor in actors:
        nid_str = str(actor['nodeid'])
        if nid_str not in actor_map:
            continue
        mhash = actor_map[nid_str]['model_hash'].replace('0x', '').replace('0X', '').lower().rjust(16, '0')
        unique_models.add(mhash)
        
    for inst in static_instances:
        mhash = static_models[inst.model_index]
        if mhash:
            unique_models.add(mhash)

    # The level's OWN base geometry (walls/floor/architecture) is a model in
    # its own right, keyed by the SCENE's hash -- not an actor and not a
    # static instance, so the two loops above never see it. `app.py`'s
    # `engine.js` loads it explicitly (`meshesToLoad.push({hash: level.hash,
    # name: "Map Geometry", ...})`, identity transform) alongside every actor
    # and static instance; without it here, only scattered props export and
    # "most of the geometry is missing" in the viewport. `find_mesh_and_primary`
    # is the same GPU-blob lookup every prop model goes through, so this is
    # only added when the scene genuinely has its own decodable geometry.
    # EVERY member of the group contributes its own map geometry, not just the
    # one named on the command line. A merged scene that took base geometry
    # from `scene_hash` alone silently dropped each sublevel's level mesh --
    # the actors and scatter came through, so it looked populated while the
    # rooms they sit in were missing.
    base_geometry_levels = []
    for member in scene_group:
        member_mesh, _member_primary = find_mesh_and_primary(pcvr_dir, member)
        if member_mesh:
            base_geometry_levels.append(member)
            unique_models.add(member)
    has_base_geometry = bool(base_geometry_levels)

    print(f"Found {len(unique_models)} unique models in scene {scene_hash} "
          f"(base map geometry: {len(base_geometry_levels)}/{len(scene_group)} "
          f"level(s))")

    if where_only:
        # Census: which resource types actually hold each model, and what each
        # model's own file references. Run this when a link probe finds nothing.
        mat_set = evr_materials.evr_mat.all_material_hashes(pcvr_dir)
        shd_set = evr_materials.evr_shaderset.all_shaderset_hashes(pcvr_dir)
        print(f"\ncorpus: {len(mat_set)} materials, {len(shd_set)} shader sets\n")

        # The scene's OWN resources first -- a level may carry one global
        # material/shaderset table rather than per-model references.
        print("=== scene-level resources ===")
        scene_mats = evr_materials.scan_all_files(pcvr_dir, scene_hash, mat_set)
        scene_shds = evr_materials.scan_all_files(pcvr_dir, scene_hash, shd_set)
        for label, values in sorted(scene_mats.items()):
            print(f"  materials  in {label}: {len(values)}  {values[:4]}")
        for label, values in sorted(scene_shds.items()):
            print(f"  shadersets in {label}: {len(values)}  {values[:4]}")
        if not scene_mats and not scene_shds:
            print("  none")

        print("\n=== per-model (every file scanned, not just the first) ===")
        type_tally = {}
        carrier_tally = {}
        linked = 0
        for mhash in sorted(unique_models)[:20]:
            found = evr_materials.locate_model(pcvr_dir, mhash)
            for label in found:
                type_tally[label] = type_tally.get(label, 0) + 1

            mats = evr_materials.scan_all_files(pcvr_dir, mhash, mat_set)
            shds = evr_materials.scan_all_files(pcvr_dir, mhash, shd_set)
            for label in set(mats) | set(shds):
                carrier_tally[label] = carrier_tally.get(label, 0) + 1
            if mats or shds:
                linked += 1

            print(f"{mhash}   types: {', '.join(found) or 'NONE'}")
            for label, values in sorted(mats.items()):
                print(f"   MATERIALS  in {label}: {len(values)}  {values[:3]}")
            for label, values in sorted(shds.items()):
                print(f"   SHADERSETS in {label}: {len(values)}  {values[:3]}")
            if not mats and not shds:
                print("   no material or shaderset references in any file")

        print(f"\ntype histogram: {type_tally}")
        print(f"reference carriers: {carrier_tally}")
        print(f"{linked} of {min(20, len(unique_models))} sampled models "
              f"reference at least one material or shader set")
        return True

    # ─── MATERIALS ────────────────────────────────────────────────────────────
    # `mat_ctx` / `mat_table` are the v2 path; `global_materials` / `model_mat_map`
    # stay populated either way so the mesh loop below is version-agnostic.
    mat_ctx = None
    mat_table = None
    models_with_textures = 0
    #: model_hash -> [matidx per draw], when the mesh->material link resolved.
    model_draw_materials: dict[str, list] = {}
    #: model -> the `x` (LOD-level) ordinal of each draw section,
    #: parallel to `model_draw_materials[model]`.
    model_section_x: dict[str, list] = {}
    #: models whose materials came from texture overlap rather than a real link.
    inferred_models: set = set()

    if legacy_materials:
        print("⚠ --legacy-materials: using the superseded slot//4 grouping")
        for mhash in sorted(unique_models):
            mapping = parse_model_texture_mapping(pcvr_dir, mhash)
            if mapping:
                mat_idx_start = len(global_materials)
                mats = build_materials_for_model(
                    mhash, mapping, texture_cache, pcvr_dir,
                    mat_idx_start, tex_out_dir)
                if mats:
                    global_materials.extend(mats)
                    model_mat_map[mhash] = (mat_idx_start, len(mats))
                    models_with_textures += 1
    else:
        mat_ctx = evr_materials.build_context(
            pcvr_dir, sorted(unique_models), hash_lookup=hash_lookup)
        mat_ctx.max_texture = _MAX_TEXTURE[0]
        mat_ctx.texture_divisor = _TEXTURE_DIVISOR[0]
        mat_table = evr_materials.MaterialTable()
        # The decode cache splits merged draws, which needs the corpus material
        # set to read draw records; publish it before any model is decoded.
        _MATERIAL_HASHES[0] = mat_ctx.material_hashes
        _DECODE_CACHE.clear()
        for warning in mat_ctx.warnings:
            print(f"  WARN {warning}")

        for mhash in sorted(unique_models):
            mapping = parse_model_texture_mapping(pcvr_dir, mhash) or {}
            model_textures = mapping.get("textures") or ()
            packfile = mapping.get("packfile")

            # Preferred: the material AND shader set hashes read from each
            # draw's own CGMeshData. The shader set is what carries the texture
            # roles, so a draw without one renders untextured.
            # Vertex counts anchor the draw-record lookup for instanced
            # models, whose arrays are not where a header walk predicts.
            _dres, _dpath = _decode_model_cached(pcvr_dir, mhash)
            model_vertex_counts = [len(rg[0]) for rg in (_dres or []) if rg]

            draw_materials = evr_materials.materials_for_model(
                pcvr_dir, mhash, mat_ctx.material_hashes,
                mat_ctx.mesh_material_offset,
                vertex_counts=model_vertex_counts)
            draw_shadersets = evr_materials.materials_for_model(
                pcvr_dir, mhash, mat_ctx.shaderset_hashes,
                mat_ctx.mesh_shaderset_offset)

            linked = bool([m for m in draw_materials if m])

            # Fallback: no usable mesh link, so rank materials by how many of
            # this model's textures they bind. Inferred, not read -- but it is
            # the difference between textured output and none at all.
            if not linked:
                ranked = evr_materials.materials_by_texture_overlap(
                    mat_ctx, model_textures)
                draw_materials = [h for h, _score in ranked]
                draw_shadersets = []
                if draw_materials:
                    inferred_models.add(mhash)

            # This model's draws, clustered into LOD groups by bounding box --
            # the SAME clustering the geometry phase uses (_group_submeshes_by_lod),
            # computed here too (via the shared _decode_model_cached, so this
            # is not a second decode) because materials need it for two things:
            #
            # 1. `CONFIRMED_MATERIAL_ROLES` propagation: a material confirmed
            #    against the real game applies to every LOD level of the SAME
            #    physical part automatically (evr_materials.
            #    propagate_confirmed_roles_to_lod_siblings), not just the one
            #    draw it was checked on.
            # 2. LOD-aware `rank`: materials sharing an LOD group get the SAME
            #    rank, so even an UNCONFIRMED part's DXGI-family guess (see
            #    roles_from_texture_list) stays consistent across its own LOD
            #    levels instead of drifting per level -- still a guess, but no
            #    longer one that visibly changes as an instance's LOD swaps.
            #
            # Falls back to the old "every distinct material gets its own
            # rank" scheme when geometry does not decode (draw_materials and
            # submesh index i then have nothing to align against).
            material_lod_group: dict = {}
            materials_by_group: dict = {}
            results, _path_label = _decode_model_cached(pcvr_dir, mhash)
            if results:
                # `_group_submeshes_by_lod` returns a real cluster index for
                # EVERY submesh, singletons included (it has no -1 sentinel --
                # that conversion is the geometry phase's, for its globally-
                # unique ids). A singleton has no sibling to propagate to or
                # rank alongside, so skip cluster_size <= 1 here explicitly
                # rather than relying on a "!= -1" check that would never
                # actually be false.
                for i, (group, _level, cluster_size) in enumerate(
                        _group_submeshes_by_lod(
                            results,
                            origins=_SPLIT_PIECES.get(mhash))):
                    if i >= len(draw_materials):
                        break
                    h = draw_materials[i]
                    if h and cluster_size > 1:
                        material_lod_group.setdefault(h, group)
                        materials_by_group.setdefault(group, [])
                        if h not in materials_by_group[group]:
                            materials_by_group[group].append(h)

            confirmed_roles = dict(evr_materials.CONFIRMED_MATERIAL_ROLES)
            confirmed_roles.update(evr_materials.propagate_confirmed_roles_to_lod_siblings(
                mhash, material_lod_group, materials_by_group))

            # This model's DISTINCT materials, 0/1/2/... in first-seen order,
            # except materials sharing an LOD group collapse onto ONE rank
            # (the group's first-seen order) -- see above.
            material_rank: dict = {}
            group_rank: dict = {}
            next_rank = 0
            for h in draw_materials:
                if not h or h in material_rank:
                    continue
                group = material_lod_group.get(h, -1)
                if group != -1:
                    if group not in group_rank:
                        group_rank[group] = next_rank
                        next_rank += 1
                    material_rank[h] = group_rank[group]
                else:
                    material_rank[h] = next_rank
                    next_rank += 1

            indices = []
            for i, material_hash in enumerate(draw_materials):
                if not material_hash:
                    indices.append(None)
                    continue
                # Counts routinely differ -- a model may list 5 materials and
                # 1 shader set, because one shader set serves every draw. Index
                # pairing would then misalign, so clamp: use the matching entry
                # when there is one, otherwise the FIRST, which is the common
                # single-shaderset case and never worse than binding nothing.
                if i < len(draw_shadersets):
                    shaderset = draw_shadersets[i]
                elif draw_shadersets:
                    shaderset = draw_shadersets[0]
                else:
                    shaderset = None
                indices.append(mat_table.intern(
                    mat_ctx, material_hash, shaderset_hash=shaderset,
                    model_textures=model_textures,
                    out_dir=tex_out_dir, packfile_hash=packfile,
                    rank=material_rank.get(material_hash, 0),
                    confirmed_roles=confirmed_roles))

            model_section_x[mhash] = evr_model_materials.section_levels(
                pcvr_dir, mhash, mat_ctx.material_hashes)

            resolved = [i for i in indices if i is not None]
            if resolved:
                # Only trust per-draw indexing when the link was READ. When it
                # was inferred, order is meaningless, so fall through to the
                # positional path rather than pretending draw i maps to rank i.
                if linked:
                    model_draw_materials[mhash] = indices
                model_mat_map[mhash] = (resolved[0], len(resolved))
                models_with_textures += 1

        global_materials = mat_table.entries
        if inferred_models:
            note = (f"{len(inferred_models)} model(s) had no CGMeshData "
                    f"material link; their materials were INFERRED from "
                    f"texture overlap and draw order is not authoritative")
            print(f"  WARN {note}")
            mat_ctx.warnings.append(note)

    print(f"Built {len(global_materials)} materials for "
          f"{models_with_textures}/{len(unique_models)} models")

    if geo_only:
        # Geometry sanity, independent of materials. The failure modes that
        # produce radiating triangle fans in the viewport all show up here:
        # indices past the vertex count, index counts that are not a multiple
        # of 3, and degenerate or absurd vertex extents.
        print("\n=== geometry audit ===")
        print(f"decoder: {_DECODER}")
        ok = bad = empty = 0
        problems = []
        paths = {}
        no_primary = 0
        for mhash in sorted(unique_models):
            mesh_file, primary_file = find_mesh_and_primary(pcvr_dir, mhash)

            if not mesh_file:
                empty += 1
                problems.append(f"{mhash}: no GPU blob in any of {MESH_DIRS}")
                continue

            primary_data = _load_primary(mesh_file, primary_file)
            if primary_data is None:
                no_primary += 1

            try:
                results_tuple = decode.extract_mesh(
                    str(mesh_file), primary_data=primary_data,
                    auto_find_primary=False)
            except Exception as exc:
                bad += 1
                problems.append(f"{mhash}: decode raised {type(exc).__name__}: {exc}")
                continue

            if not results_tuple or not results_tuple[0]:
                empty += 1
                problems.append(f"{mhash}: decode returned nothing "
                                f"(gpu={mesh_file.parent.name}, "
                                f"primary={'yes' if primary_data else 'NO'})")
                continue

            label = results_tuple[1] if len(results_tuple) > 1 else "?"
            paths[label] = paths.get(label, 0) + 1

            for i, res_group in enumerate(results_tuple[0]):
                if not res_group:
                    continue
                verts, faces, uvs, *_rest = res_group
                nv = len(verts)
                flat_idx = [x for f in faces for x in f]
                ni = len(flat_idx)
                hi = max(flat_idx) if flat_idx else -1

                issues = []
                if nv == 0 or ni == 0:
                    issues.append("empty")
                if ni % 3:
                    issues.append(f"index count {ni} not a multiple of 3")
                if hi >= nv:
                    issues.append(f"max index {hi} >= vertex count {nv}")
                if uvs is not None and uvs and len(uvs) != nv:
                    issues.append(f"uv count {len(uvs)} != vertex count {nv}")
                if verts:
                    xs = [v[0] for v in verts]
                    span = max(xs) - min(xs)
                    if span > 1e5:
                        issues.append(f"x-extent {span:.1f} implausible")

                if issues:
                    bad += 1
                    if len(problems) < 40:
                        problems.append(
                            f"{mhash}[{i}] v={nv} i={ni}: {'; '.join(issues)}")
                else:
                    ok += 1

        print(f"\nsubmeshes clean       {ok}")
        print(f"submeshes suspect     {bad}")
        print(f"models empty/missing  {empty}")
        print(f"models w/o primary    {no_primary}")
        print(f"decode paths          {paths}")
        print("  ('primary_described' is correct; 'heuristic' means the vertex "
              "format was guessed and the mesh is probably mangled)")
        print("\nfirst problems:")
        for line in problems[:30]:
            print(f"  {line}")
        return True

    if probe_only:
        if mat_ctx is not None and mat_table is not None:
            sidecar = mat_table.to_sidecar(scene_hash, mat_ctx)
            print()
            print(evr_materials.summarise(sidecar))
        print("\n--probe: stopping before geometry extraction")
        return True

    # (state reset happens once, at the top of this function -- see there)
    print(f"Exporting EVR scene {level_label(scene_hash)} with {len(actor_map)} actors and {len(static_instances)} static instances...")
    
    # Unify instances
    instances_to_process = []

    # The level's own base geometry, once, at identity transform -- exactly
    # how app.py's engine.js places "Map Geometry". Must run through the same
    # decode/material path as every other model, so it goes in
    # instances_to_process rather than being special-cased later.
    #
    # 'rot' is a DICT, not a list, deliberately: actor transforms below always
    # carry rotation as {"x","y","z","w"} (level_reader.parse_actor_data), and
    # the consumer's list branch assumes (w,x,y,z) order -- a mismatch against
    # unpack_rotation's actual (x,y,z,w) return that only static instances hit
    # (see docs/ECHO_VR.md §7.4). The dict branch is unambiguous, so use it.
    for member in base_geometry_levels:
        instances_to_process.append({
            'mhash': member,
            'pos': {'x': 0.0, 'y': 0.0, 'z': 0.0},
            'rot': {'x': 0.0, 'y': 0.0, 'z': 0.0, 'w': 1.0},
            'scale': {'x': 1.0, 'y': 1.0, 'z': 1.0},
        })

    for actor in actors:
        nid_str = str(actor['nodeid'])
        if nid_str not in actor_map:
            continue
            
        mhash = actor_map[nid_str]['model_hash'].replace('0x', '').replace('0X', '').lower().rjust(16, '0')
        t = actor.get('transform')
        if not t:
            continue
            
        instances_to_process.append({
            'mhash': mhash,
            'pos': t.get('position', [0, 0, 0]),
            'rot': t.get('rotation', [0, 0, 0, 1]),
            'scale': t.get('scale', [1, 1, 1])
        })
        
    for inst in static_instances:
        mhash = inst.model_hash or static_models[inst.model_index]
        if not mhash or mhash == "0" * 16:
            continue
        # Position, rotation and scale all come from the instance's
        # `CTransformCR` row as plain world-space floats. `rot` goes as a DICT
        # for the same reason the base-geometry instance above does: the row
        # stores (x, y, z, w) but the consumer's list branch reads a list as
        # (w, x, y, z), so a list here would silently mis-orient every prop.
        x, y, z, w = inst.rotation
        instances_to_process.append({
            'mhash': mhash,
            'pos': inst.position,
            'rot': {'x': x, 'y': y, 'z': z, 'w': w},
            'scale': inst.scale,
            # Carried so the lighting pass can reach this instance's lightmap
            # page and its OWN atlas UVs, both of which are keyed by entity.
            'entity': inst.entity,
            'level': inst.level,
        })
        
    # Parallel to `all_instances`: [entity_hex, level_hash, submesh] or None.
    static_entity_map: list = []

    for inst_data in instances_to_process:
        mhash = inst_data['mhash']
            
        if mhash in _FAILED_MODELS:
            continue
        if mhash not in mesh_idx_map:
            # Decoded already in the materials phase (LOD grouping needs it
            # there too) -- this is a cache hit, not a second decode. Caches
            # the failure case too: without that the same broken model is
            # re-decoded once per instance, which is why `decode paths`
            # totalled 131 for 63 models and made `heuristic` look far more
            # widespread than it is.
            results, path_label = _decode_model_cached(pcvr_dir, mhash)
            if not results:
                _FAILED_MODELS.add(mhash)
                continue

            # Cluster this model's submeshes into LOD groups by bounding box
            # (see _group_submeshes_by_lod). Recomputed here (cheap -- pure
            # bbox math over already-decoded results) rather than cached
            # alongside `results`, so the global group ids stay assigned in
            # the order this phase actually visits models.
            submesh_clusters = _group_submeshes_by_lod(
                results, origins=_SPLIT_PIECES.get(mhash))
            submesh_lod: list = []
            local_to_global: dict = {}
            for cluster_index, level, cluster_size in submesh_clusters:
                if cluster_size <= 1:
                    submesh_lod.append((-1, 0, 1))
                    continue
                if cluster_index not in local_to_global:
                    local_to_global[cluster_index] = _NEXT_LOD_GROUP[0]
                    _NEXT_LOD_GROUP[0] += 1
                submesh_lod.append(
                    (local_to_global[cluster_index], level, cluster_size))

            # Submesh -> material.
            #
            # ⚠ This is the WEAKEST remaining link (§7.5). `draws[i]` is not a
            # real per-draw binding: the CGMeshData material probe never
            # resolves (coverage 0.0), so `materials_for_model` falls back to
            # `scan_model_references`, which returns whatever material hashes
            # appear anywhere in the model file, in scan order. Pairing that
            # order with submesh order is a guess, and `i` counts EVERY
            # submesh including each LOD copy.
            #
            # ⛔ Do NOT "fix" this by collapsing each LOD group to one material
            # by majority vote. It was tried: the invariant is real (LODs of one
            # part must share a material) but it is enforced through the BBOX
            # CLUSTERING, which is itself a heuristic, so it forces one material
            # onto parts the heuristic merged wrongly. Measured on
            # d09afd15b1c75c04: materials 216 -> 146 and loaded images 383 ->
            # 221, i.e. it destroys correct assignments faster than it fixes
            # wrong ones. Fix the draw->material link itself first.
            # ⛔ Seven mappings tried here and all reverted (§7.5/§7.5c):
            # palette-by-index, sections-by-index, LOD-group majority vote,
            # `x`-as-LOD-level, degenerate-aware alignment, part-major
            # permutation, and one-material-per-part swept over LOD levels
            # (`EVR_PART_MATERIAL_LEVEL` = last/1/2 -- none correct at LOD 0).
            # Two facts are established and should anchor the next attempt:
            #   * all LODs of a part MUST share one material
            #     (`87ac33e558c5c2e3`: one part, two LODs, two materials)
            #   * a fixed LOD level is NOT the selector (the sweep refutes it)
            draws_for_model = model_draw_materials.get(mhash)
            raw_mat_idx: list = []
            for i in range(len(results)):
                value = 0
                if (draws_for_model is not None and i < len(draws_for_model)
                        and draws_for_model[i] is not None):
                    value = draws_for_model[i]
                elif mhash in model_mat_map:
                    mat_idx_for_mesh, num_mats = model_mat_map[mhash]
                    value = mat_idx_for_mesh + (i if i < num_mats else 0)
                    if i == 0 and draws_for_model is None:
                        _warn_ordered_materials(mhash)
                raw_mat_idx.append(value)


            base_idx = len(all_meshes)
            scene_meshes = []
            
            # results is a list of submeshes. Each result is usually [(verts, faces, uvs, bone_data)]
            for i, res_group in enumerate(results):
                if not res_group: continue
                # results is a list of submeshes
                verts, faces, uvs, *rest = res_group
                
                # Flatten arrays for le_scatter SceneMesh
                flat_pos = []
                for v in verts: flat_pos.extend(v)
                
                flat_idx = []
                for f in faces: flat_idx.extend(f)
                
                flat_uv = None
                if uvs:
                    flat_uv = []
                    for u in uvs: flat_uv.extend(u)
                
                # Submesh -> material.
                #
                # Preferred: `model_draw_materials[mhash][i]` is the matidx read
                # from THIS draw's own CGMeshData record, so submesh order and
                # material order cannot drift apart.
                #
                # Fallback (probe not confident, or legacy path): the old
                # `mat_idx_start + i` ordering, clamped. That assumes draw i uses
                # material i, which holds for single-material models and is a
                # guess for everything else -- so it is logged once per model.
                submesh_mat_idx = raw_mat_idx[i] if i < len(raw_mat_idx) else 0

                sm = SceneMesh(
                    index=base_idx + i,
                    name_hash=int(mhash, 16),
                    matidx=submesh_mat_idx,
                    shdidx=0,
                    aabb_min=(0,0,0),
                    aabb_max=(0,0,0),
                    instance_offset=len(all_instances),
                    instance_count=0,
                    positions=flat_pos,
                    indices=flat_idx,
                    normals=None,
                    uv0=flat_uv,
                    uv1=None,
                    draws=[{
                        "matidx": submesh_mat_idx,
                        "shdidx": 0,
                        "idx_start": 0,
                        "idx_count": len(flat_idx)
                    }]
                )
                scene_meshes.append(sm)
                all_meshes.append(sm)
                
            mesh_idx_map[mhash] = (base_idx, scene_meshes, submesh_lod)

        base_idx, scene_meshes, submesh_lod = mesh_idx_map[mhash]
        
        # Extract transforms from unified inst_data
        pos = inst_data['pos']
        rot = inst_data['rot']
        scale = inst_data['scale']
        
        # Handle dict format (from actors) or list/tuple format (from static instances)
        tx = pos['x'] if isinstance(pos, dict) else pos[0]
        ty = pos['y'] if isinstance(pos, dict) else pos[1]
        tz = pos['z'] if isinstance(pos, dict) else pos[2]
        
        rx = rot['x'] if isinstance(rot, dict) else rot[1] # quaternion from unpack_quat is (w,x,y,z)
        ry = rot['y'] if isinstance(rot, dict) else rot[2]
        rz = rot['z'] if isinstance(rot, dict) else rot[3]
        rw = rot['w'] if isinstance(rot, dict) else rot[0]
        
        sx = scale['x'] if isinstance(scale, dict) else scale[0]
        sy = scale['y'] if isinstance(scale, dict) else scale[1]
        sz = scale['z'] if isinstance(scale, dict) else scale[2]
        
        # Add instances for all submeshes. lod_group/-level/-group_levels come
        # from the bbox clustering above -- without them every LOD level of
        # every part draws simultaneously, stacked, at this placement.
        for submesh_i, (sm, (lod_group, lod_level, lod_group_levels)) in enumerate(
                zip(scene_meshes, submesh_lod)):
            inst = SceneInstance(
                mesh_index=sm.index,
                translation=(tx, ty, tz),
                rotation=(rx, ry, rz, rw),
                scale=(sx, sy, sz),
                lod_group=lod_group,
                lod_level=lod_level,
                lod_group_levels=lod_group_levels,
            )
            # Record which static-instance entity (and which of ITS submeshes)
            # produced this package instance. A static instance's lightmap page
            # and its atlas UVs are both keyed by entity, and nothing else in
            # the package preserves that link once instances are flattened.
            entity = inst_data.get('entity')
            if not entity:
                static_entity_map.append(None)
            else:
                origins = _SPLIT_PIECES.get(mhash)
                ranges = _SPLIT_RANGES.get(mhash)
                engine_mesh = (origins[submesh_i]
                               if origins and submesh_i < len(origins)
                               else submesh_i)
                lo, hi = (ranges[submesh_i]
                          if ranges and submesh_i < len(ranges) else (0, 0))
                static_entity_map.append(
                    [f"{entity:016x}", inst_data.get('level', ''),
                     engine_mesh, lo, hi])
            all_instances.append(inst)
            sm.instance_count += 1
            
    if all_meshes:
        out_pkg = scene_out_dir

        if global_materials:
            if mat_ctx is not None and mat_table is not None:
                # v2: the add-on hands `spec` to material_builder verbatim, so
                # this is the line that turns a base-colour+normal import into a
                # full PBR one. `to_sidecar` also emits the v1 flat fields,
                # DERIVED from the same spec, for older add-on builds.
                materials_json = mat_table.to_sidecar(scene_hash, mat_ctx)
            else:
                materials_json = {"master": scene_hash,
                                  "materials": global_materials,
                                  "version": 1}

            with (out_pkg / "materials.json").open("w", encoding="utf-8") as mf:
                json.dump(materials_json, mf, indent=1)

            # ⛔ Do NOT also write `<master>_materials.json` next to `out_pkg`'s
            # PARENT (`out_pkg.parent / f"{scene_hash}_materials.json"`), even
            # though `scatter_import.import_lescatter`'s auto-discovery checks
            # that location FIRST and it looks like harmless redundancy.
            # `channels[*]["file"]` above is written relative to `out_pkg`
            # itself ("textures/xxx.dds"). Lone Echo's own `le_scene_materials.py`
            # writes that OUTER location's sidecar with paths relative to ITS
            # parent instead -- a different, incompatible convention -- and
            # `import_lescatter` computes `tex_base` assuming whichever
            # location it found the sidecar AT sets the base. Writing the same
            # `out_pkg`-relative paths at `out_pkg.parent` makes auto-discovery
            # find a sidecar whose paths are off by one directory level for
            # EVERY texture, and it wins over the correct inner copy because it
            # is checked first: measured, this made every material import with
            # zero image texture nodes -- 0 of 179 -- even though `materials.json`
            # inside `out_pkg` (the file written just above) is completely
            # correct and loads perfectly on its own. The inner file alone is
            # sufficient: it's exactly `import_lescatter`'s SECOND
            # auto-discovery candidate, `pkg.dir / "materials.json"`.

            tex_count = len(list(tex_out_dir.glob("*.png")))
            dds_count = len(list(tex_out_dir.glob("*.dds")))
            version = materials_json.get("version", 1)
            print(f"Wrote {len(global_materials)} materials (v{version}), "
                  f"{tex_count} PNG + {dds_count} DDS textures")
            if mat_ctx is not None:
                print()
                print(evr_materials.summarise(materials_json))
                if mat_ctx.warnings:
                    print(f"\n{len(mat_ctx.warnings)} warning(s); "
                          f"see diagnostics.warnings in materials.json")

        # Which decode path each model took. From `decode.extract_mesh`, in the
        # order it tries them:
        #
        #   primary_described  CIMR models -- CGInstancedModelResource ONLY
        #   cgml               mesh-list models -- the CORRECT path for that
        #                      family, not a fallback
        #   crossref_ib / hero tried when neither of the above matched
        #   heuristic          no branch matched; the vertex stride is GUESSED
        #
        # An earlier version of this summary treated everything but
        # `primary_described` as broken, which flagged 37 correctly-decoded
        # mesh-list models as suspect. Only `heuristic` is unambiguously bad.
        print(f"\ndecode paths   {_DECODE_PATHS or 'none'}")
        if _NO_PRIMARY:
            print(f"no primary     {len(_NO_PRIMARY)} model(s) -- vertex format "
                  f"guessed for these")

        good = sum(v for k, v in _DECODE_PATHS.items()
                   if k in ("primary_described", "cgml"))
        weak = sum(v for k, v in _DECODE_PATHS.items()
                   if k in ("hero", "crossref_ib"))
        guessed = _DECODE_PATHS.get("heuristic", 0)
        print(f"               {good} on a described path, {weak} on a "
              f"reconstructed one, {guessed} guessed")
        if guessed:
            print(f"⛔ {guessed} model(s) fell through every described path -- "
                  f"the vertex format was guessed and the geometry is probably "
                  f"wrong. These are the models to chase.")
        if _FAILED_MODELS:
            print(f"⛔ {len(_FAILED_MODELS)} model(s) produced no geometry at "
                  f"all: {sorted(_FAILED_MODELS)[:6]}")

        print(f"\nWriting {len(all_instances)} instances across {len(all_meshes)} meshes to {out_pkg}...")
        write_package(out_pkg, scene_hash, all_meshes, all_instances)
        # Sidecar, not manifest: this is an EVR-only join key and the manifest
        # contract is shared with the Lone Echo path.
        if any(static_entity_map):
            (out_pkg / "static_entities.json").write_text(json.dumps({
                "format": "evr_static_entities",
                "version": 1,
                "note": ("Parallel to instances.bin: [entity, level, submesh] "
                         "or null for a non-static instance. The entity is the "
                         "key into CGStaticInstanceResource for this instance's "
                         "lightmap page and its own atlas UVs."),
                "instances": static_entity_map,
            }, indent=1), encoding="utf-8")
        print("Done!")
        return True

    print("No meshes found to export!")
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("hash", help="Hash of the CActorDataResourceWin10")
    parser.add_argument("--dir", type=Path, default=Path(r"H:\pcvr-extracted"),
                        help="flat Echo VR extract root")
    parser.add_argument("--out", type=Path, default=Path(r"J:\EchoVRModels"))
    parser.add_argument("--hash-lookup", type=Path, default=None,
                        help="hash_lookup.json of cracked CSymbol64 names. "
                             "Improves inputname->role resolution; without it "
                             "unnamed binds fall back to unknown_s{slot}.")
    parser.add_argument("--legacy-materials", action="store_true",
                        help="use the superseded slot//4 grouping and emit v1. "
                             "For A/B comparison only.")
    parser.add_argument("--probe", action="store_true",
                        help="resolve materials and report, then stop before "
                             "geometry extraction. Use this first.")
    parser.add_argument("--full", action="store_true",
                        help="also load this level's SUBLEVELS and emit ONE "
                             "merged scene under <out>/Scenes_Full/<name>/ "
                             "(mpl_lobby_b2 pulls in mpl_lobby_b_arena and "
                             "mpl_lobby_b_combat)")
    parser.add_argument("--include", nargs="*", default=[],
                        help="extra level names/hashes to merge in, for groups "
                             "the naming rule cannot infer")
    parser.add_argument("--max-texture", type=int, default=0,
                        help="cap written textures to this many pixels per side "
                             "(e.g. 512). Drops top mips -- exact, no resampling. "
                             "Large levels otherwise crash Blender's material preview.")
    parser.add_argument("--texture-divisor", type=int, default=1,
                        help="reduce EVERY texture by this power-of-two factor "
                             "relative to its own native size (2 = half "
                             "resolution). Unlike --max-texture this also "
                             "shrinks already-small textures. Exact mip "
                             "selection, no resampling.")
    parser.add_argument("--structural", action="store_true",
                        help="re-decode models whose scanned geometry contradicts "
                             "their own declared tables (see evr_structural_decode)")
    parser.add_argument("--geo", action="store_true",
                        help="audit GEOMETRY only: which models decode, and "
                             "whether their indices/UVs are self-consistent. "
                             "Use this when the viewport looks wrong.")
    parser.add_argument("--where", action="store_true",
                        help="census: which resource types hold each model and "
                             "what each model's file references. Use this when "
                             "--probe reports zero materials.")
    args = parser.parse_args()

    _STRUCTURAL_FALLBACK[0] = bool(args.structural)
    _MAX_TEXTURE[0] = int(args.max_texture or 0)
    _TEXTURE_DIVISOR[0] = max(1, int(args.texture_divisor or 1))
    _LAST_ROOT[0] = str(args.dir)
    target = resolve_level(args.hash)
    group = sublevels_of(target) if args.full else [target]
    for extra in args.include:
        extra_hash = resolve_level(extra)
        if extra_hash not in group:
            group.append(extra_hash)

    ok = extract_evr_scene(
        group if len(group) > 1 else target, args.dir, args.out,
        legacy_materials=args.legacy_materials,
        hash_lookup=args.hash_lookup,
        probe_only=args.probe,
        where_only=args.where,
        geo_only=args.geo,
    )
    raise SystemExit(0 if ok else 1)