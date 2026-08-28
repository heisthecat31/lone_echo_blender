"""Quest materials: which material each submesh draws with, and its textures.

## The container grammar is shared, so the PC reader is REUSED

`CGMaterialResourceAndroid` is the same `SGMaterialData` object as the PC
`CGMaterialResourceWin10`: same six containers at 0x28/0x60/0xA0/0xD8/0x118/
0x150, same descriptor shape. Measured, not assumed -- `evr_material_textures.
read_containers` parses **877 of 877** shipped Quest materials with no changes,
830 of them carrying a non-empty slot->texture table over 103 distinct slots.

So this module does NOT fork the material reader. It imports
`evr_material_textures` for `slot_textures` / `role_for_slot` and adds only the
two things that genuinely differ on Quest: where a submesh's material comes
from, and the fact that the textures are ASTC rather than BCn.

## Where a submesh's material comes from

`CGRenderParams` -- the 112-byte per-draw record every mesh resource carries --
holds the material PALETTE INDEX at **+0x28**, and the model's own tables hold
the palette. The binding is therefore a table lookup, not an inference:

    material(submesh i) = palette[ renderparams[i].u32 @ +0x28 ]

The palette is NOT in submesh order. On 20 of 225 models `+0x28` is a genuine
PERMUTATION of `0..n-1` (e.g. `[5,2,6,8,7,9,0,3,4,1]`), so reading `palette[i]`
for submesh `i` hands those models each other's materials. The two readings
agree on the other 145, which is exactly why that mistake survives a casual
check.

`+0x28` was identified against a LABELLED set rather than guessed: on models
whose palette count equals their submesh count the answer is already known, and
`+0x28` is the ONLY field in the 112 bytes that determines the material in 29
of 29 such models. `quest_combat_port` carries this record verbatim as opaque
bytes, so nothing downstream depended on an older reading of it.

Measured over all 225 shipped instanced models: 225 resolve, and 561 of 561
submesh slots bind -- against 209/225 and 468/479 before.

A MESH LIST (`CGMeshListResourceAndroid`) carries render params but no palette
of its own. Its palette lives in the LEVEL's `CGSceneResourceAndroid`, as a u32
count followed by that many `CSymbol64`s, and `+0x28` indexes it exactly as it
does a model's own table.

⚠ The palette is NOT 4-byte aligned. The arena's sits at scene offset
5,245,198, which is only 2-byte aligned, so a scan stepping 4 bytes from zero
walks straight past it and reports the level as having no materials at all.
`scene_material_palette` steps 2.

Located by the count prefix and the requirement that EVERY entry be a material
extracted from this build -- not by offset, and not by a "looks like a hash"
test, which runs of IEEE floats pass.

## Textures

Quest ships ASTC, never BCn, so nothing here goes through the DDS path. The
`dxgi` field in the emitted spec is a STAND-IN: `le_mesh.materials` uses it only
to decide colour space, so an sRGB ASTC format is reported as the DXGI code for
an sRGB block format and a linear one as its UNORM twin. It is not a claim that
the texel data is BCn -- see `DXGI_FOR_SRGB`.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from evr_quest import mesh as qmesh                       # noqa: E402
from evr_quest import texture as qtexture                 # noqa: E402
from evr_quest import types as qtypes                     # noqa: E402

#: A `RadArrayDescriptor` table can only hold the binding if its stride is a
#: whole number of u64s -- the hash is one.
HASH_ALIGN = 8

#: Stand-in DXGI codes. `le_mesh.materials` reads `dxgi` ONLY to pick a colour
#: space, so these carry the sRGB bit across and nothing else. Quest texels are
#: ASTC; no BCn decoder is ever handed these.
DXGI_FOR_SRGB = 99          # BC7_UNORM_SRGB
DXGI_FOR_LINEAR = 98        # BC7_UNORM


def _normalise(value) -> str:
    if isinstance(value, int):
        return f"{value:016x}"
    return str(value).lower()


def material_hashes(root) -> set:
    """Every material in the extract, in both hash spellings."""
    directory = qtypes.type_dir(root, qtypes.MATERIAL)
    if directory is None:
        return set()
    out = set()
    for path in directory.iterdir():
        name = path.name.lower()
        out.add(name)
        out.add(name.lstrip("0") or "0")
    return out


def _is_material(value: int, known: set) -> bool:
    if not value:
        return False
    name = f"{value:016x}"
    return name in known or (name.lstrip("0") or "0") in known


#: `CGRenderParams` is 112 bytes; the material palette index is a u32 here.
RENDERPARAM_MATIDX = 0x28

#: Scene palettes are only 2-byte aligned -- see the module docstring.
SCENE_SCAN_STEP = 2
#: A palette may carry entries the hull never indexes, so accept a count this
#: much above the highest index in use. Beyond that a match is coincidence.
SCENE_PALETTE_SLACK = 64


def scene_material_palette(root, level, least: int, known: set) -> list:
    """A material palette of at least `least` entries in a level's scene, or `[]`.

    A level hull's materials are not in its mesh list; they are here, as a u32
    count followed by that many hashes. `least` is `max(index) + 1`, NOT the
    number of distinct indices -- a hull that skips an entry still indexes past
    it, and asking for the distinct count finds nothing (the arena wants 13 and
    uses 12).

    Every entry must be a real material in this extract, the same rule
    `_palettes` applies. A "looks like a hash" test is not enough: runs of
    IEEE floats read as perfectly plausible u64s, and the LOD-distance tables
    are full of them.
    """
    if least <= 0:
        return []
    path = qtypes.resource(root, qtypes.SCENE_RESOURCE, level)
    if path is None:
        return []
    try:
        blob = path.read_bytes()
    except OSError:
        return []
    best = None
    for off in range(0, max(len(blob) - 4, 0), SCENE_SCAN_STEP):
        count = struct.unpack_from("<I", blob, off)[0]
        if not least <= count <= least + SCENE_PALETTE_SLACK:
            continue
        if best is not None and count >= best[0]:
            continue
        base = off + 4
        if base + count * 8 > len(blob):
            continue
        values = [struct.unpack_from("<Q", blob, base + i * 8)[0]
                  for i in range(count)]
        named = sum(1 for v in values if _is_material(v, known))
        if named and named + values.count(0) == count:
            best = (count, [f"{v:016x}" if _is_material(v, known) else None
                            for v in values])
    return best[1] if best else []


def _palettes(blob: bytes, tables, base, known: set) -> list:
    """Every table that is a run of material hashes: `[(count, values), ...]`.

    A table qualifies only when EVERY slot is a real material in this extract
    or an explicit null. That is what rejects the LOD-distance float tables,
    which otherwise read as perfectly plausible u64s.
    """
    out = []
    at = base
    for size, count in tables:
        stride = size // count if count else 0
        if size and stride and stride % HASH_ALIGN == 0:
            for off in range(0, stride - HASH_ALIGN + 1, HASH_ALIGN):
                values = [struct.unpack_from("<Q", blob, at + i * stride + off)[0]
                          for i in range(count)]
                named = sum(1 for v in values if _is_material(v, known))
                if named and named + values.count(0) == count:
                    out.append((count, [f"{v:016x}" if _is_material(v, known)
                                        else None for v in values]))
        at += size
    return out


#: `CGMeshData`: which render-param SECTIONS this submesh owns.
M_FIRST_SECTION = 0x34
M_SECTION_COUNT = 0x38


def _sections(blob: bytes, mesh_at: int, submeshes: int) -> list:
    """`[first render-param index, ...]`, one per submesh.

    ⛔ NOT `range(submeshes)`. Render params are per DRAW SECTION, not per
    submesh, and a submesh routinely owns several: `34918b365c4b7940` has 6
    submeshes and **14** render params. Taking `renderparams[i]` for submesh `i`
    therefore reads a neighbour's material from the second submesh onward, which
    is exactly what put banner atlases on the arena hull.

    ⭐ `CGMeshData` states the mapping: `+0x34` is the first section and `+0x38`
    the count. They tile the render-param array EXACTLY -- `sum(counts) == nrp`
    and each `first == sum(counts[:i])` -- on **475 of 475** mesh lists and
    **225 of 225** instanced models, with no remainder anywhere. A wrong field
    pair does not tile 700 files.
    """
    out = []
    for i in range(submeshes):
        at = mesh_at + i * qmesh.MESH_REC
        if at + qmesh.MESH_REC > len(blob):
            return []
        out.append(struct.unpack_from("<I", blob, at + M_FIRST_SECTION)[0])
    return out


def _render_param_indices(blob: bytes, submeshes: int):
    """`([palette index per submesh], tables, base)` for either on-disk form.

    A `CGInstancedModelResource` declares its tables through
    `RadArrayDescriptor`s; a `CGMeshListResource` uses the compact
    `[u32 count][records]` form and has no descriptors at all. Both carry the
    same 112-byte `CGRenderParams`, so this finds them either way -- without
    which a level hull silently resolves to nothing.
    """
    def _pick(rp_at, n_rp, mesh_at):
        firsts = _sections(blob, mesh_at, submeshes)
        if not firsts or max(firsts) >= n_rp:
            return None
        return [struct.unpack_from(
            "<I", blob,
            rp_at + first * qmesh.RENDERPARAM_REC + RENDERPARAM_MATIDX)[0]
            for first in firsts]

    tables = qmesh._descriptors(blob)
    if tables:
        base = qmesh._data_base(blob, tables)
        if base is not None:
            at = base
            mesh_at = rp_at = None
            n_rp = 0
            for size, count in tables:
                stride = size // count if count else 0
                if stride == qmesh.MESH_REC and mesh_at is None and size:
                    mesh_at = at
                if stride == qmesh.RENDERPARAM_REC and rp_at is None and size:
                    rp_at, n_rp = at, count
                at += size
            if mesh_at is not None and rp_at is not None:
                index = _pick(rp_at, n_rp, mesh_at)
                if index is not None:
                    return index, tables, base

    # Compact form: [u32 nmesh][nmesh x 152][u32 nrp][nrp x 112]...
    if len(blob) < 4:
        return None, tables, None
    count = struct.unpack_from("<I", blob, 0)[0]
    if count != submeshes:
        return None, tables, None
    mesh_at = 4
    at = 4 + count * qmesh.MESH_REC
    if at + 4 > len(blob):
        return None, tables, None
    n_rp = struct.unpack_from("<I", blob, at)[0]
    at += 4
    if at + n_rp * qmesh.RENDERPARAM_REC > len(blob):
        return None, tables, None
    index = _pick(at, n_rp, mesh_at)
    return (index, tables, None) if index is not None else (None, tables, None)


def submesh_materials(blob: bytes, submeshes: int, known: set,
                      root=None, level=None) -> list:
    """`[material_hash | None, ...]` per submesh, or `[]`.

    `palette[ renderparams[i] @ +0x28 ]`. The palette is the model's own table
    where it has one, and the LEVEL's scene palette where it does not -- which
    is the case for every level hull. See the module docstring.
    """
    if submeshes <= 0:
        return []
    index, tables, base = _render_param_indices(blob, submeshes)
    if not index:
        return []

    # The smallest palette that covers every index. A larger table may happen
    # to contain them too, but the model's own palette is the tightest fit and
    # is what the engine indexes.
    covering = ([t for t in _palettes(blob, tables, base, known)
                 if max(index) < t[0]] if base is not None else [])
    if covering:
        values = min(covering, key=lambda t: t[0])[1]
        return [values[j] for j in index]

    # No table in the file covers the indices -- a level hull. Its palette is
    # in the scene resource, sized to exactly the distinct indices used.
    if root is None or level is None:
        return []
    values = scene_material_palette(root, level, max(index) + 1, known)
    if not values:
        return []
    return [values[j] for j in index]


def model_materials(root, model_hash, submeshes: int, known: set | None = None) -> list:
    """`submesh_materials` for a model in a Quest extract."""
    known = material_hashes(root) if known is None else known
    path = qtypes.resource(root, qtypes.INSTANCED_MODEL, model_hash)
    if path is None:
        path = qtypes.resource(root, qtypes.MESH_LIST, model_hash)
    if path is None:
        return []
    try:
        return submesh_materials(path.read_bytes(), submeshes, known,
                                 root=root, level=model_hash)
    except (OSError, struct.error):
        return []


def _material_textures():
    """The PC slot->texture reader, imported lazily so this module can load
    without `blender_tool` on the path."""
    blender_tool = _SCRIPTS.parent / "blender_tool"
    if str(blender_tool) not in sys.path:
        sys.path.insert(0, str(blender_tool))
    import evr_material_textures                          # noqa: PLC0415
    return evr_material_textures


#: A texture bound by at least this many DISTINCT materials is shared engine
#: infrastructure -- a default/stub -- not this material's own art.
#:
#: Measured over the 877 shipped Quest materials the split is unambiguous: 14
#: textures are bound by 290-830 materials each, the next most-shared is bound
#: by 57, and 2993 are bound by exactly one. Any threshold in 58..289 selects
#: the same 14, so this sits in an empty band rather than on a boundary.
DEFAULT_SHARE_THRESHOLD = 100

_DEFAULTS_CACHE: dict = {}


def default_textures(root, threshold: int = DEFAULT_SHARE_THRESHOLD) -> set:
    """The shared engine stub textures, counted rather than listed.

    One pass over every material, counting how many DISTINCT materials bind
    each texture. Without this an 8x8 grey stub lands in roughness, emission,
    opacity and blend_mask on most materials, which imports as a surface
    covered in meaningless 8x8 maps.
    """
    key = (str(root), threshold)
    if key in _DEFAULTS_CACHE:
        return _DEFAULTS_CACHE[key]
    directory = qtypes.type_dir(root, qtypes.MATERIAL)
    if directory is None:
        _DEFAULTS_CACHE[key] = set()
        return _DEFAULTS_CACHE[key]
    emt = _material_textures()
    counts: dict = {}
    for path in directory.iterdir():
        try:
            table = emt.slot_textures(path.read_bytes())
        except Exception:                                 # noqa: BLE001
            continue
        for texture in set(table.values()):
            counts[texture] = counts.get(texture, 0) + 1
    out = {t for t, n in counts.items() if n >= threshold}
    _DEFAULTS_CACHE[key] = out
    return out


def roles(root, material_hash, names: dict | None = None,
          defaults: set | None = None) -> dict:
    """`{role_key -> texture_hash}` for one Quest material.

    Straight through the PC reader: the container layout is identical, so
    forking it would be duplicating code that already parses 877/877 of these.
    """
    path = qtypes.resource(root, qtypes.MATERIAL, material_hash)
    if path is None:
        return {}
    emt = _material_textures()
    try:
        table = emt.slot_textures(path.read_bytes())
    except Exception:                                     # noqa: BLE001
        return {}
    if defaults is None:
        defaults = default_textures(root)
    table = emt.real_slot_textures(table, defaults)
    return emt.roles_from_material_table(table, names or {})


def texture_files(root, out_dir, wanted, cap: int = 0,
                  dropped_alpha: list | None = None,
                  keep_alpha: set | None = None) -> dict:
    """Decode the wanted Quest textures to PNG. `{texture_hash -> rel path}`.

    ASTC, not BCn. Each texture is written at the LARGEST level it can supply,
    which usually is not the one resident in the leaf: most of this build is
    streamed, so the leaf keeps a 128px thumbnail and the real image sits in
    `RawTexturePackfile` pages. Decoding the resident level is what made the
    first export look washed out -- 128 of 137 arena textures were undersized,
    one of them 4096x2048 served as 128x64.

    `cap` selects a mip rather than resampling one, so a capped export is still
    exactly the engine's own image data.

    A texture that cannot be decoded is OMITTED rather than written as a
    placeholder, so a missing file means "not decodable", not "black".
    """
    out_dir = Path(out_dir)
    textures = out_dir / "textures"
    textures.mkdir(parents=True, exist_ok=True)
    written = {}
    dropped = [] if dropped_alpha is None else dropped_alpha
    for raw in wanted:
        name = _normalise(raw)
        if not name or name in written:
            continue
        primary = qtypes.resource(root, qtypes.TEXTURE, name)
        if primary is None:
            continue
        info = qtexture.read(primary)
        if info is None:
            continue
        gpu = qtypes.resource(root, qtypes.TEXTURE_GPU, name)
        level = qtexture.pick_level(info, cap)
        decoded = None
        if level is not None:
            try:
                decoded = qtexture.decode_level(primary, gpu, root, info, level)
            except Exception:                             # noqa: BLE001
                decoded = None
        if not decoded:
            # A streamed page can be absent from a partial extract; the
            # resident level still decodes and is better than nothing.
            try:
                decoded = qtexture.decode(primary, gpu, info)
            except Exception:                             # noqa: BLE001
                continue
        if not decoded:
            continue
        rgba, width, height = decoded
        rel = f"textures/{name}.png"
        # The alpha channel is dropped unless it reads as opacity: see
        # `qtexture.alpha_is_opacity`. Writing it unconditionally is what made
        # every textured surface render black.
        # A material that draws through the transparent pipeline NEEDS its
        # alpha, however sparse it looks -- the heuristic is only a fallback
        # for textures no transparent material claims.
        forced = bool(keep_alpha and name in keep_alpha)
        opaque_alpha = forced or qtexture.alpha_is_opacity(rgba, width, height)
        if qtexture.write_png(out_dir / rel, rgba, width, height,
                              keep_alpha=opaque_alpha):
            written[name] = (rel, info)
            if not opaque_alpha:
                dropped.append(name)
    return written


def srgb_map(written: dict) -> dict:
    """`{texture_hash -> stand-in DXGI}` from each texture's ASTC sRGB flag."""
    out = {}
    for name, (_rel, info) in written.items():
        is_srgb = bool(getattr(info, "srgb", False))
        out[name] = DXGI_FOR_SRGB if is_srgb else DXGI_FOR_LINEAR
    return out


def _spec_builder():
    """`le_mesh.materials.build_material_spec`, imported lazily."""
    blender_tool = _SCRIPTS.parent / "blender_tool"
    if str(blender_tool) not in sys.path:
        sys.path.insert(0, str(blender_tool))
    from le_mesh import materials as le_materials          # noqa: PLC0415
    from le_mesh import role_index                         # noqa: PLC0415
    return le_materials, role_index


#: `mattype` values that draw through the transparent pipeline.
TRANSPARENT_MATTYPES = frozenset({2})
#: `blendmode` values that are not plain opaque.
OPAQUE_BLENDMODE = 0


def render_state(root, material_hash) -> dict:
    """`{mattype, blendmode, flags, transparent}` for one material, or `{}`.

    ⭐ Read, not inferred. `evr_material_resource.parse_header` finds the real
    block on Quest: `775ae052eb922921` reads `mattype 1 / blendmode 0`
    (`eMTForwardOpaque` / `eBlendOpaque`) while `677bc7cc44e623c7` reads
    `mattype 2 / blendmode 7` (`eMTForwardTransparent` / `eBlendTransparent`).
    Across the 877 shipped materials the split is real -- 632 opaque, and 69
    `eBlendTranslucent`, 36 additive, 28 linear-dodge, 25 transparent.

    ⛔ Do NOT use `le_mesh.material_scalars.decode_material_scalars` for this.
    It returns `mattype 0 / blendmode 0 / flags 0` for EVERY Quest material --
    one single signature across all 877 -- which reads as "this build authored
    no render state" when in fact the header carries `flags 6670` and a varied
    blend mode. That false uniformity is what made an earlier pass conclude the
    data was unrecoverable and reach for the texture's alpha content instead.
    """
    path = qtypes.resource(root, qtypes.MATERIAL, material_hash)
    if path is None:
        return {}
    import evr_material_resource                          # noqa: PLC0415
    try:
        header = evr_material_resource.parse_header(path.read_bytes())
    except Exception:                                     # noqa: BLE001
        return {}
    if header is None:
        return {}
    mattype = int(getattr(header, "mattype", 0) or 0)
    blendmode = int(getattr(header, "blendmode", 0) or 0)
    return {
        "mattype": mattype,
        "blend_mode": blendmode,
        "flags": int(getattr(header, "flags", 0) or 0),
        "transparent": (mattype in TRANSPARENT_MATTYPES
                        or blendmode != OPAQUE_BLENDMODE),
    }


#: The material's UV multiplier, as a sibling pair of float properties in the
#: `materialprops` block. The CSymbol64 preimages are NOT cracked -- neither
#: hash appears in any known-name source -- so they are addressed by hash.
UV_SCALE_U_HASH = 0xb038ef9606824f9f
UV_SCALE_V_HASH = 0xb038ec9606824f9f

#: `[u64 name hash][u32 offset][u32 size]`, offsets relative to `materialprops`.
MATERIAL_PROP_REC = 16

IDENTITY_UV_SCALE = (1.0, 1.0)


def material_props(root, material_hash):
    """`({name_hash: (offset, size)}, blob)`, or `{}` -- offsets absolute."""
    path = qtypes.resource(root, qtypes.MATERIAL, material_hash)
    if path is None:
        return {}
    import evr_material_resource                          # noqa: PLC0415
    try:
        blob = path.read_bytes()
        header = evr_material_resource.parse_header(blob)
    except Exception:                                     # noqa: BLE001
        return {}
    if header is None:
        return {}
    try:
        table = header.payload_offsets["materialpropoffsets"]
        base = header.payload_offsets["materialprops"]
        count = header.counts["materialpropoffsets"]
    except (AttributeError, KeyError, TypeError):
        return {}
    out = {}
    for i in range(count):
        at = table + i * MATERIAL_PROP_REC
        if at + MATERIAL_PROP_REC > len(blob):
            break
        name, offset, size = struct.unpack_from("<QII", blob, at)
        out[name] = (base + offset, size)
    return out, blob


def uv_scale(root, material_hash) -> tuple:
    """The material's `(u, v)` UV multiplier -- `(1.0, 1.0)` when unauthored.

    ⭐ This is what put the arena's panel art in the wrong half of its atlas.
    `775ae052eb922921` carries `(1.0, 2.0)`, and its mesh's raw V spans
    0.0032..0.4936 -- the TOP HALF of a 1024x512 sheet, landing every panel on
    the grey prop art in the corners instead of the coloured triangles. Doubled,
    the charts trace the triangle borders exactly.

    The corpus says the same thing without reference to any one mesh. Grouping
    every mesh by its material's pair and taking the median raw UV span:

        (1.0, 1.0)  410 meshes   V span 0.990   -- already fills, no-op
        (1.0, 2.0)   11 meshes   V span 0.490   -- x2 -> 0.981
        (1.0, 4.0)    2 meshes   V span 0.215   -- x4 -> 0.862
        (16.0, 1.0)  40 meshes   U span 1.068   -- genuine 16x tiling

    So it is a plain multiplier: art authored into a fraction of the sheet
    scales up to fill it, and tiling materials repeat. Both are `uv * scale`.
    """
    found = material_props(root, material_hash)
    if not found:
        return IDENTITY_UV_SCALE
    props, blob = found
    out = []
    for name in (UV_SCALE_U_HASH, UV_SCALE_V_HASH):
        entry = props.get(name)
        if entry is None or entry[1] < 4 or entry[0] + 4 > len(blob):
            return IDENTITY_UV_SCALE
        out.append(struct.unpack_from("<f", blob, entry[0])[0])
    u, v = out
    if not (u and v) or u != u or v != v:                 # zero or NaN
        return IDENTITY_UV_SCALE
    return (float(u), float(v))


def build_sidecar(root, out_dir, per_mesh: dict, names: dict | None = None,
                  cap: int = 0,
                  progress=None) -> tuple:
    """Write `materials.json` + `textures/` for a Quest package.

    `per_mesh` maps mesh index -> material hash (or None). Returns
    `(document, {mesh_index: matidx})`.

    The spec is built by `le_mesh.materials.build_material_spec` -- the same
    function the PC path uses -- so the add-on receives exactly the shape it
    already reads. Only the inputs are Quest-specific: roles come from the
    shared container reader, and `dxgi` is the sRGB stand-in documented at the
    top of this module.
    """
    le_materials, role_index = _spec_builder()

    # ⛔ Slot 0 is a deliberate PLACEHOLDER with no textures, and real
    # materials start at 1. `matidx` defaults to 0 for any mesh the binding
    # did not resolve, so without this an unbound mesh silently wears the
    # first real material in the level -- someone else's textures, which reads
    # as a decode bug rather than as missing data.
    order, matidx = [], {}
    unbound = [i for i in sorted(per_mesh) if not per_mesh[i]]
    for mesh_index in sorted(per_mesh):
        name = per_mesh[mesh_index]
        if not name:
            continue
        if name not in order:
            order.append(name)
        matidx[mesh_index] = order.index(name) + 1

    role_by_material = {}
    state_by_material = {}
    transparent_textures = set()
    default_only = set()
    wanted = set()
    defaults = default_textures(root)
    emt = _material_textures()
    if progress:
        progress(f"{len(defaults)} shared engine texture(s) filtered out")
    for name in order:
        found = roles(root, name, names, defaults)
        role_by_material[name] = found
        wanted.update(v for v in found.values() if v)
        state = render_state(root, name)
        state_by_material[name] = state
        if state.get("transparent"):
            transparent_textures.update(_normalise(v) for v in found.values() if v)
        # ⭐ "No textures" and "only engine defaults" are DIFFERENT states and
        # both import as flat white, which is what makes the FX shells around
        # `mpl_arena_a_lowspec`'s discs read as solid grey plastic. A material
        # like `e7e59cb17f419942` binds 84 slots over 7 textures and every one
        # of the 7 is a shared default -- it is an effect surface whose whole
        # appearance is render state.
        #
        # ⛔ That render state is NOT RECOVERABLE from this extract: there is no
        # shaderset resource type in the tree at all, and `shaderset_hash` is
        # empty on every Quest material. So the sidecar FLAGS these rather than
        # inventing a blend mode for them, and a consumer can hide them.
        if not found:
            path = qtypes.resource(root, qtypes.MATERIAL, name)
            try:
                bound = bool(emt.slot_textures(path.read_bytes())) if path else False
            except Exception:                             # noqa: BLE001
                bound = False
            if bound:
                default_only.add(name)
    if progress:
        progress(f"{len(order)} material(s), {len(wanted)} texture(s)")

    dropped_alpha: list = []
    written = texture_files(root, out_dir, sorted(wanted), cap=cap,
                            dropped_alpha=dropped_alpha,
                            keep_alpha=transparent_textures)
    dxgi = srgb_map(written)
    # A reference with no resource behind it is NOT a decode failure. This
    # build ships neither normal nor specular maps for its baked surfaces --
    # 19 of 19 arena materials that carry a composite reference both and have
    # neither, with no counter-example -- so counting them as losses reported
    # a third of the textures missing when nothing had gone wrong.
    absent = sorted(t for t in wanted
                    if _normalise(t) not in written
                    and qtypes.resource(root, qtypes.TEXTURE, _normalise(t)) is None)
    files = {name: rel for name, (rel, _info) in written.items()}

    entries = [{
        "matidx": 0, "shdidx": 0,
        "spec": le_materials.build_material_spec(
            "unbound__", material_hash="", role_textures={},
            dxgi_by_tex={}, texture_files={}, role_sources={}),
    }]
    for index, name in enumerate(order, start=1):
        found = role_by_material[name]
        # Every binding here was READ from the material's own slot table, so
        # the provenance is the array, not a guess.
        sources = {role: role_index.SOURCE_ARRAY for role in found}
        state = state_by_material.get(name) or {}
        scalars = {}
        if state:
            scalars = {"blend_mode": state["blend_mode"],
                       "mattype": state["mattype"],
                       "flags": state["flags"],
                       "alpha": 1.0}
        spec = le_materials.build_material_spec(
            f"{name}__",
            material_hash=name,
            role_textures=found,
            dxgi_by_tex=dxgi,
            texture_files=files,
            scalars=scalars,
            role_sources=sources)
        entry = {"matidx": index, "shdidx": 0, "spec": spec}
        if name in default_only:
            entry["engine_default_only"] = True
            entry["note"] = ("binds only shared engine default textures -- an "
                             "effect surface whose look is render state, and "
                             "this build ships no shaderset resource to read "
                             "it from")
        entries.append(entry)


    document = {
        "format": "le_scene_materials",
        "version": 2,
        "source": "evr_quest",
        "textures_subdir": "textures",
        "materials": entries,
        "diagnostics": {
            "materials_built": len(entries) - 1,
            "textures_written": len(written),
            "textures_wanted": len(wanted),
            "textures_not_shipped": len(absent),
            "texture_cap": cap,
            "meshes_bound": len(matidx),
            "meshes_unbound": len(unbound),
            "materials_engine_default_only": len(default_only),
            "textures_alpha_dropped": len(dropped_alpha),
            "materials_transparent": sum(
                1 for v in state_by_material.values() if v.get("transparent")),
            "placeholder_matidx": 0,
            "note": ("Quest textures are ASTC and are written as PNG; the "
                     "`dxgi` field is an sRGB stand-in, not a BCn claim. "
                     "Textures whose top mips live in RawTexturePackfile are "
                     "written at their RESIDENT size."),
        },
    }
    (Path(out_dir) / "materials.json").write_text(
        __import__("json").dumps(document, indent=1), encoding="utf-8")
    return document, matidx
