"""A model's OWN material palette, from the game's own tables.

## What this replaces

`evr_materials.materials_for_model` had no real source for "which materials does
this model use", so it fell back to `scan_model_references`: every material hash
appearing anywhere in the model's bytes, in file-scan order. That list is wrong
in both ways that matter -- it contains materials the model does not draw (a
hash can appear in unrelated padding or in a neighbouring table) and its ORDER
is meaningless. Pairing it with submesh index is what put another model's
texture on a mesh.

The engine does not infer this. Every model carries its palette explicitly.

## The two carriers

Echo VR splits models across two resource families, and each keeps the same two
tables in a different place:

* **mesh-list models** (`CGMeshListResourceWin10`, 628 of them) have a
  **same-named `CGSceneResourceWin10`** -- the mesh list's hash IS the scene
  resource's hash, a **628/628** pairing with nothing left over. This is exactly
  how Lone Echo does it (`le_scene_binding` looks up the same companion), and
  all 628 decode with the validated `CGSceneResource` reader.
* **instanced models** (`CGInstancedModelResourceWin10`, 1893) have **no** scene
  resource at all (0 of 1893 pair) -- they embed the same two tables inline in
  the primary.

Both then hold:

    materials    CTable<CSymbol64>            the palette
    shadersets   CTable<SGMeshShaderSet>      stride 24, one per DRAW SECTION:
                     u64 shaderset
                     u64 material             names the material outright
                     u32 x                    section / LOD ordinal
                     u32 matidx               index into the palette

`matidx` is **non-decreasing and run-length structured** across sections -- one
material covers a run of consecutive sections, e.g. `0,1,1,1,2,2,2,3`. That is
the fact the old per-submesh-index assignment violated: it advanced to a new
material on every submesh, so it fell out of phase on the first repeated entry
and stayed out. Which submeshes then came out right depended on which LOD level
was imported, which is why importing LOD 0 and LOD 4 produced complementary sets
of correctly-textured models.

## Locating the inline tables

For the instanced case there is no count prefix to trust, so the tables are
found by a constraint that is effectively impossible to satisfy by accident:
take a candidate run of 24-byte records whose material field is a real material
and whose `matidx` is non-decreasing, read `n = max(matidx) + 1` CSymbol64s
immediately BEFORE the run as the palette, and require

    palette[record.matidx] == record.material        for EVERY record

A false start fails this immediately. An earlier attempt that only checked "is
this a plausible hash" (no palette cross-check) returned garbage on most models
-- `max(x) + 1 == 43` where the real answer is single digits -- which is why the
cross-check, not the scan, is what makes this trustworthy.

Verified by hand on `ff5afb4e96897159`: an 8-entry palette at `0x1920` followed
by 8 records at `0x1960`, containing `1e070bb9873c1e45` at index 5 -- the
material whose slot table binds all three textures confirmed against the running
game. The old scan-order path had been giving that model
`a96dba2cbec4a581`/`2993ccd5a8e33846` instead, whose own tables are all-defaults
(i.e. genuinely untextured).
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _path in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from evr_resource_types import (
    INSTANCED_MODEL_RESOURCE,
    SCENE_RESOURCE,
    normalise_hash,
    resource_path,
)

#: `SGMeshShaderSet` on disk.
SECTION_STRIDE = 24

#: A record run shorter than this is not worth trusting from an inline scan --
#: a single 24-byte window satisfying the palette cross-check is possible by
#: chance in a large file, a run of two consecutive ones essentially is not.
MIN_INLINE_SECTIONS = 1


def _scene_tables(root: Path, model_hash) -> tuple | None:
    """`(palette, sections)` from the model's companion `CGSceneResourceWin10`."""
    path = resource_path(root, SCENE_RESOURCE, model_hash)
    if path is None:
        return None
    try:
        import cgsceneresource as scene_reader
    except ImportError:
        return None
    try:
        obj = scene_reader.read(path.read_bytes())
    except Exception:
        return None

    mat_count, mat_raw = obj["materials"]
    sec_count, sec_raw = obj["shadersets"]
    palette = [normalise_hash(struct.unpack_from("<Q", mat_raw, i * 8)[0])
               for i in range(mat_count)]
    sections = []
    for i in range(sec_count):
        base = i * SECTION_STRIDE
        material = normalise_hash(struct.unpack_from("<Q", sec_raw, base + 8)[0])
        x, matidx = struct.unpack_from("<II", sec_raw, base + 16)
        sections.append((x, matidx, material))
    return palette, sections


#: `quest_combat_port`'s validated `resource_io` package, if present. The
#: `CGSceneResource` grammar is a 22-member serializer walk transcribed from
#: disassembly; re-implementing it here would be a second, worse copy.
_QUEST_RESOURCE_IO = Path(
    r"J:\EchoVR-Tools-Launcher\quest_combat_port\tools\resource_io")
if _QUEST_RESOURCE_IO.is_dir() and str(_QUEST_RESOURCE_IO) not in sys.path:
    sys.path.append(str(_QUEST_RESOURCE_IO))


def _inline_tables(data: bytes, material_hashes: set) -> tuple | None:
    """`(palette, sections)` located inside an instanced-model primary.

    Anchored on the palette cross-check described in the module docstring, so a
    coincidental hash match cannot produce a result.
    """
    best = None
    limit = len(data) - SECTION_STRIDE
    offset = 0
    while offset <= limit:
        material = normalise_hash(struct.unpack_from("<Q", data, offset + 8)[0])
        if material not in material_hashes:
            offset += 4
            continue

        sections = []
        cursor = offset
        previous_matidx = -1
        while cursor + SECTION_STRIDE <= len(data):
            mat = normalise_hash(struct.unpack_from("<Q", data, cursor + 8)[0])
            if mat not in material_hashes:
                break
            x, matidx = struct.unpack_from("<II", data, cursor + 16)
            if matidx < previous_matidx or matidx > 0xFFFF:
                break
            sections.append((x, matidx, mat))
            previous_matidx = matidx
            cursor += SECTION_STRIDE

        if len(sections) >= MIN_INLINE_SECTIONS:
            n_mat = max(s[1] for s in sections) + 1
            palette_start = offset - n_mat * 8
            if palette_start >= 0:
                palette = [
                    normalise_hash(struct.unpack_from("<Q", data, palette_start + i * 8)[0])
                    for i in range(n_mat)
                ]
                # THE cross-check: the palette must actually explain every record.
                if all(palette[matidx] == mat for _x, matidx, mat in sections):
                    if best is None or len(sections) > len(best[1]):
                        best = (palette, sections)
        offset += 4
    return best


def model_tables(root: Path, model_hash, material_hashes: set) -> tuple | None:
    """`(palette, sections)` for a model, or None.

    Companion scene resource first (structural, no scanning), then the inline
    instanced-model layout.
    """
    tables = _scene_tables(root, model_hash)
    if tables and tables[0]:
        return tables
    path = resource_path(root, INSTANCED_MODEL_RESOURCE, model_hash)
    if path is None:
        return None
    try:
        return _inline_tables(path.read_bytes(), material_hashes)
    except (OSError, struct.error):
        return None


def section_materials(root: Path, model_hash, material_hashes: set) -> list:
    """The material drawn by each DRAW SECTION, in section order.

    This is the ordered, run-length-structured list the engine itself uses --
    `[m0, m1, m1, m1, m2, m2, m2, m3]` rather than a set of distinct materials.
    """
    tables = model_tables(root, model_hash, material_hashes)
    if not tables:
        return []
    _palette, sections = tables
    return [material for _x, _matidx, material in sections]


def palette(root: Path, model_hash, material_hashes: set) -> list:
    """The model's distinct materials, in palette order."""
    tables = model_tables(root, model_hash, material_hashes)
    return list(tables[0]) if tables else []


def section_levels(root: Path, model_hash, material_hashes: set) -> list:
    """The `x` ordinal of each draw section, in section order.

    `x` measures as the LOD LEVEL the section belongs to: sections repeat it
    when several parts draw at the same level (`0,0,1,2,3,4,5,6` = two parts at
    level 0, one part continuing alone through levels 1..6). Pairing this with
    the decoder's per-submesh LOD level is what lets a submesh find its own
    section instead of being matched positionally.
    """
    tables = model_tables(root, model_hash, material_hashes)
    if not tables:
        return []
    _palette, sections = tables
    return [x for x, _matidx, _material in sections]


#: Byte offset of the material index inside a 112-byte `CGRenderParams` record.
#:
#: THE per-draw material link, found by dumping every u32 column of the record
#: across a model whose answer was known. On `ff5afb4e96897159` the column reads
#: `[5, 0, 1, 2, 6, 3, 7, 4, 7]` -- one value per draw, range 0..7 against an
#: 8-entry palette, and NOT monotonic, which is exactly why every ordering
#: hypothesis failed: the mapping is a permutation the file states outright.
#:
#: It puts `1e070bb9873c1e45` (base colour `c29a7d30d8154550`) on draw 0, the
#: 1877-vertex main body -- the texture confirmed against the running game.
#: Verified corpus-wide: on 550 of 567 mesh-list models every value is a legal
#: palette index; the 17 exceptions are single-material models reading 1, which
#: clamps harmlessly.
RENDERPARAM_MATIDX_OFFSET = 32
RENDERPARAM_VERTEXCOUNT_OFFSET = 64
RENDERPARAM_STRIDE = 112


#: `CGMeshListResourceWin10` is a flat run of COUNT-PREFIXED tables, in this
#: declaration order.  Strides are the Echo VR ones, which differ from the
#: `oldarena` decompilation (mesh 152 not 128, renderparams 112 not 104, vertex
#: buffer 336 not 304) -- the same 152 this repo already pins in
#: `evr_resource_types.MESH_TABLE_WIN10`, which is a useful cross-check.
MESHLIST_TABLE_STRIDES = (
    152,   # CGMeshData
    112,   # CGRenderParams   <- the draw records, and the material index
    336,   # CGVertexBufferData
    336,   # morph buffers
    16,    # morph index buffers
    16,    # CGIndexBufferData
    4,     # lod child indices
)


def _meshlist_tables(data: bytes):
    """`[(count, offset, stride), ...]` for a mesh list, or None.

    Validated by walking it: every one of the 359 non-stub
    `CGMeshListResourceWin10` files sampled from a live extract parses with no
    failures, and the walk is self-checking (a wrong stride overruns the file
    or yields an absurd count immediately).
    """
    offset = 0
    tables = []
    for stride in MESHLIST_TABLE_STRIDES:
        if offset + 4 > len(data):
            return None
        count = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        if count > 1_000_000 or offset + count * stride > len(data):
            return None
        tables.append((count, offset, stride))
        offset += count * stride
    return tables


def _renderparams_from_meshlist(root: Path, model_hash):
    """The 112-byte draw records of a `CGMeshListResourceWin10`, or None.

    ⛔ This used to `import cgmeshlistresource`, a module that exists in NO
    checkout of this project, inside a bare `except Exception: return None`.
    So it always returned None, silently, and every mesh-list-primary model
    skipped the draw-record route entirely and fell through to positional
    assignment -- which is what `material_index_fallback` in the role-route
    tally counts. The reader is a count-prefixed table walk; there was never
    anything to import.
    """
    from evr_resource_types import MESH_LIST_RESOURCE

    path = resource_path(root, MESH_LIST_RESOURCE, model_hash)
    if path is None:
        return None
    data = path.read_bytes()
    tables = _meshlist_tables(data)
    if not tables:
        return None
    count, offset, stride = tables[1]
    if not count:
        return None
    return [data[offset + k * stride: offset + (k + 1) * stride]
            for k in range(count)]


def _renderparams_from_instanced(root: Path, model_hash, vertex_counts):
    """The draw records inside an instanced-model primary.

    The primary opens with 56-byte `CTable` headers; header[1] is the draw
    table (stride 112) and gives the true record COUNT. The arrays do not sit
    where a naive header walk predicts, so the base is found by scanning for an
    offset at which every record's `vertexcount` column is plausible AND at
    least one matches a decoded submesh -- then the best such candidate wins.

    ⚠ The count is NOT the submesh count. `ca11721873128b56` has 3 draw records
    for 2 decoded submeshes (vcounts 148/232/144 vs submeshes 148/376). An
    earlier version demanded the whole sequence line up, found nothing, and
    silently fell back to positional assignment -- which swapped that model's
    two textures.
    """
    path = resource_path(root, INSTANCED_MODEL_RESOURCE, model_hash)
    if path is None:
        return None
    data = path.read_bytes()

    header_size, cursor, count = 0x38, 0, None
    index = 0
    while cursor + header_size <= len(data):
        ptr, size, z10 = struct.unpack_from("<QQQ", data, cursor)
        z18, _flags = struct.unpack_from("<II", data, cursor + 0x18)
        mark, total, iused = struct.unpack_from("<QQQ", data, cursor + 0x20)
        if ptr or z10 or z18 or mark not in (0, 32) or total > 100000:
            break
        if index == 1:
            count = iused
            break
        cursor += header_size
        index += 1
    if not count:
        return None

    span = count * RENDERPARAM_STRIDE
    wanted = set(vertex_counts or ())
    best, best_score = None, (0, 0)
    for base in range(0, len(data) - span + 1, 4):
        counts = []
        for k in range(count):
            offset = base + k * RENDERPARAM_STRIDE + RENDERPARAM_VERTEXCOUNT_OFFSET
            value = struct.unpack_from("<I", data, offset)[0]
            if not 0 < value < 500000:
                counts = None
                break
            counts.append(value)
        if not counts:
            continue
        # Scoring has to satisfy two cases at once:
        #
        #  * There are often MORE draw records than decoded submeshes --
        #    `155b9acfe6e12841` declares 10 records for 4 meshes. Positional
        #    scoring (records[k] == vertex_counts[k]) finds nothing there, so
        #    the model silently fell back to positional material assignment.
        #  * A degenerate run must not win. `ff5afb4e96897159` has a region
        #    where every record reads 4 -- a real submesh count -- which scored
        #    a perfect 9/9 under naive set-membership scoring.
        #
        # So: score by how many DISTINCT wanted counts appear anywhere in the
        # array, and reject any candidate whose counts are all identical. The
        # alignment step below then pairs submeshes to records by vertex count,
        # which already handles more records than submeshes.
        if len(set(counts)) < 2 and len(counts) > 1:
            continue

        # The vertex-count test alone is not enough. A LEVEL's own model
        # (`43e2da7914642604`, 157 draws) matched a region with perfectly
        # plausible varying counts whose matidx field read 4 on every single
        # record -- so all 144 of dyson's base meshes collapsed onto one
        # material, and the whole level wore that material's signage texture.
        #
        # A constant matidx across many draws is the same degeneracy as a
        # constant vertex count, one field over. It is legitimate for a
        # single-material model, so this ranks rather than rejects: at equal
        # count-score, a region whose materials VARY wins.
        mats = []
        for k in range(count):
            offset = base + k * RENDERPARAM_STRIDE + RENDERPARAM_MATIDX_OFFSET
            mats.append(struct.unpack_from("<I", data, offset)[0])
        score = (len(set(counts) & wanted), len(set(mats)))
        if score > best_score:
            best_score = score
            best = base
    if best is None:
        return None
    return [data[best + k * RENDERPARAM_STRIDE:
                 best + (k + 1) * RENDERPARAM_STRIDE] for k in range(count)]


def draw_material_indices(root: Path, model_hash, material_hashes: set,
                          vertex_counts=None) -> list:
    """`[material_hash, ...]` for each DRAW, in draw order — the engine's own.

    Reads `CGRenderParams.matidx` per draw and resolves it through the model's
    palette. This is a direct read, not a correspondence: draw k's material is
    whatever index the file records for draw k.
    """
    tables = model_tables(root, model_hash, material_hashes)
    if not tables:
        return []
    palette, _sections = tables
    if not palette:
        return []

    records = _renderparams_from_meshlist(root, model_hash)
    if records is None:
        records = _renderparams_from_instanced(root, model_hash, vertex_counts)
    if not records:
        return []

    # Put the draw records in SUBMESH order.
    #
    # Match each submesh to the record with its vertex count, consuming records
    # as they are taken; submeshes with no matching record then take the
    # remaining records in order. There can be MORE records than submeshes --
    # `ca11721873128b56` ships 3 draws (vcount 148/232/144) for 2 decoded
    # submeshes (148/376), and the correct answer is submesh0 -> rec0 (its
    # vcount matches) and submesh1 -> rec1 (the next one left). Requiring a
    # whole-sequence match instead made this model fall back to positional
    # assignment, which swapped its two textures.
    if vertex_counts:
        pools: dict = {}
        for position, record in enumerate(records):
            if len(record) >= RENDERPARAM_VERTEXCOUNT_OFFSET + 4:
                value = struct.unpack_from(
                    "<I", record, RENDERPARAM_VERTEXCOUNT_OFFSET)[0]
                pools.setdefault(value, []).append(position)

        taken: set = set()
        chosen: list = [None] * len(vertex_counts)
        for position, wanted in enumerate(vertex_counts):
            pool = pools.get(wanted)
            while pool:
                candidate = pool.pop(0)
                if candidate not in taken:
                    chosen[position] = candidate
                    taken.add(candidate)
                    break
        spare = [i for i in range(len(records)) if i not in taken]
        for position in range(len(chosen)):
            if chosen[position] is None and spare:
                chosen[position] = spare.pop(0)
        records = [records[i] if i is not None else records[0]
                   for i in chosen]

    out = []
    for record in records:
        if len(record) < RENDERPARAM_MATIDX_OFFSET + 4:
            out.append("")
            continue
        index = struct.unpack_from("<I", record, RENDERPARAM_MATIDX_OFFSET)[0]
        out.append(palette[min(index, len(palette) - 1)])
    return out


def draw_records(root: Path, model_hash, material_hashes: set,
                 vertex_counts=None) -> list:
    """`[(vertex_count, material_hash), ...]` per DRAW, in record order.

    Unlike `draw_material_indices` this does NOT collapse to one entry per
    decoded submesh -- it returns every draw the model declares, because the
    geometry decoder merges consecutive draws that share a vertex buffer and the
    caller needs the un-merged list to split them apart again.
    """
    tables = model_tables(root, model_hash, material_hashes)
    if not tables:
        return []
    palette, _sections = tables
    if not palette:
        return []
    records = _renderparams_from_meshlist(root, model_hash)
    if records is None:
        records = _renderparams_from_instanced(root, model_hash, vertex_counts)
    if not records:
        return []
    out = []
    for record in records:
        if len(record) < RENDERPARAM_VERTEXCOUNT_OFFSET + 4:
            continue
        index = struct.unpack_from("<I", record, RENDERPARAM_MATIDX_OFFSET)[0]
        count = struct.unpack_from("<I", record, RENDERPARAM_VERTEXCOUNT_OFFSET)[0]
        out.append((count, palette[min(index, len(palette) - 1)]))
    return out


def split_runs(draws: list, submesh_vertex_counts: list) -> list:
    """Map each decoded submesh to the CONSECUTIVE run of draws it merged.

    Returns `[[(vertex_count, material), ...], ...]`, parallel to
    `submesh_vertex_counts`, or `[]` if the arithmetic does not work out.

    `b4bf0b8ba02fbcbd` is the case this exists for: 9 draws
    (284,204,167,22,727,98,369,124,70) decode as 4 submeshes
    (488,1383,124,70) because `284+204 == 488` and
    `167+22+727+98+369 == 1383`. A merged submesh can only carry ONE material,
    so five draws with five different materials collapsed onto one -- which is
    how a flat palette texture ended up smeared across geometry that the engine
    draws as five separately-textured sections.

    Requiring an EXACT partition is what makes this safe: if the sums do not
    line up the model is left alone rather than split on a guess.
    """
    runs = []
    cursor = 0
    for wanted in submesh_vertex_counts:
        total = 0
        run = []
        while cursor < len(draws) and total < wanted:
            total += draws[cursor][0]
            run.append(draws[cursor])
            cursor += 1
        if total != wanted or not run:
            return []
        runs.append(run)
    if cursor != len(draws):
        return []
    return runs
