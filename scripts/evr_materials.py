"""Echo VR materials -> the v2 `_materials.json` sidecar the add-on already reads.

## The point of this module

`blender_tool/addon/lone_echo_import/scatter_import.py` accepts two sidecar
versions.  Its own docstring is explicit about the difference:

* **v1** -- flat fields (`basecolor_dds`, `normal_texture`, ...).  The importer
  hand-rolls a spec from them and, in its words, loses "alpha, render mode,
  emission, specular, roughness, blend masks".
* **v2** -- `{"matidx":i, "shdidx":j, "spec":{...}}`, where `spec` is
  *byte-for-byte the same dict a `.lemesh` manifest carries* and is handed to
  `material_builder.build_material` **verbatim**.

`scripts/evr_scene_extract.py` emits v1.  That single fact -- not any missing
add-on feature -- is why Echo VR scatter imports are base colour plus normal
only.  The README's "⚠ `.lescatter` imports are still base colour + normal only"
describes the v1 sidecar, and it stops being true the moment the extractor emits
v2.

So this module does not add a rendering feature.  It stops throwing away one
that already exists on both sides.

## The chain

    CGTextureStreamingResourceWin10   ordered texture inventory for a model
              |                        (evr_texture_streaming)
              v
    CGMaterialResourceWin10           SShaderInputData binds: inputname -> texture
              |                        (evr_material_resource)
              v
    le_mesh.materials.role_for_inputname
              |                        inputname hash -> `layer0_diffuse_map` etc.
              v
    cgtextureresourceWin10            DXGI format per texture
              |                        (evr_texture_resource)
              v
    le_mesh.materials.build_material_spec
              |                        the SAME spec builder the Lone Echo path uses
              v
    materials.json  {"version": 2, "materials": [{"matidx", "shdidx", "spec"}]}

Every role name, channel routing rule, alpha chain, blend-mask composite and
colourspace decision is Lone Echo's existing, tested code.  Nothing about
material semantics is re-implemented here; this module only feeds it Echo VR
inputs.

## What is honestly weak

⛔ **The model -> material link is a heuristic.**  `CGMeshData` is a 152-byte
record whose material `CSymbol64` field offset has not been reversed
(`rad-archive-viewer/echomod/resources/cgmeshlist_resource.py` keeps the whole
mesh table as an opaque blob).  `resolve_model_materials` finds it by probing:
for each 8-byte-aligned offset in the record, count how many records hold a u64
that is a real `CGMaterialResourceWin10` in the extract, and take the offset
that scores highest across the whole corpus.  That is the same technique the
Lone Echo shaderset scanner uses, and it is self-checking -- `probe_mesh_material_offset`
reports the score so a weak winner is visible rather than assumed.  But it is a
probe, and a model whose materials are all absent from the extract will score
zero and fall back to per-model ordering.
"""

from __future__ import annotations

import json
import struct
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _path in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import evr_material_resource as evr_mat
import evr_material_textures as evr_mat_tex
import evr_model_materials
import evr_shaderset
import evr_texture_resource as evr_tex
import evr_texture_streaming as evr_stream
from evr_resource_types import (
    MESH_LIST_RESOURCE,
    normalise_hash,
    resource_path,
)

from le_mesh import materials as le_materials
from le_mesh import role_index

#: `CGMeshData` stride, from the reference mesh-list parser.
MESH_STRIDE = 152

#: The sidecar version the add-on treats as "hand `spec` to the builder verbatim".
SIDECAR_VERSION = 2

#: Role suffixes that reach a Principled socket at all. A material with none
#: of these has no visible texture, however many bindings it has.
_ROUTABLE_FOR_BASE = frozenset((
    "albedo_map", "composite_diffuse", "normal_map", "composite_normals",
    "composite_components", "composite_specular", "specular_map",
    "emissive_map", "secondary_emissive_map", "alpha_map", "opacity_map",
    "blend_mask", "back_lighting_map",
))


# ---------------------------------------------------------------------------
# hash_lookup.json -- cracked names for inputname hashes
# ---------------------------------------------------------------------------

def load_hash_lookup(path: Path | None) -> dict:
    """`{hash_int -> name}` from a `hash_lookup.json`.

    Same tolerant shape as `le_archive_decode.load_hash_lookup`: keys may be
    `"0x..."` or bare hex, non-string values are skipped.  Returns `{}` for a
    missing file, because the role resolver degrades to `unknown_s{slot}`
    rather than failing.
    """
    if path is None or not Path(path).exists():
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out: dict = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            continue
        text = str(key).lower()
        if text.startswith("0x"):
            text = text[2:]
        try:
            out[int(text, 16)] = value
        except ValueError:
            continue
    return out


# ---------------------------------------------------------------------------
# Model -> material link
# ---------------------------------------------------------------------------

def model_file(root: Path, model_hash) -> Path | None:
    """The first resource file that exists for a model hash, any type.

    Searched in order: the mesh-list descriptor, then every directory the
    geometry loader uses.  A model does not reliably have a
    `CGMeshListResourceWin10` -- on a real scene, none of 63 models did -- so
    assuming that type is how the material link probe found nothing to look at.
    """
    from evr_resource_types import MESH_DIRS, MESH_LIST_RESOURCE

    for type_hash in (MESH_LIST_RESOURCE, *MESH_DIRS):
        path = resource_path(root, type_hash, model_hash)
        if path is not None:
            return path
    return None


def locate_model(root: Path, model_hash) -> dict:
    """Every resource type directory that holds a file for this hash.

    A census, not a guess: it walks the extract's top-level directories and
    reports which ones contain the hash, with the type name where known.  This
    is the diagnostic to reach for when a link probe comes back empty.
    """
    from evr_resource_types import TYPE_NAMES

    by_hash = {v: k for k, v in TYPE_NAMES.items()}
    found = {}
    root = Path(root)
    if not root.is_dir():
        return found
    for directory in root.iterdir():
        if not directory.is_dir():
            continue
        path = resource_path(root, directory.name, model_hash)
        if path is not None:
            label = by_hash.get(normalise_hash(directory.name), directory.name)
            found[label] = path.stat().st_size
    return found


def scan_all_files(root: Path, resource_hash, wanted: set) -> dict:
    """`{type_label -> [hashes found]}` across EVERY file for this hash.

    A model owns four to eight resources -- geometry, visibility, physics,
    lightmap, texture streaming -- and the material reference need not be in
    whichever one a single-file search happens to pick first.  Scanning only the
    first is how `scan_model_references` reported "0 references" for all 20
    sampled models while the answer may simply have been in a sibling file.
    """
    from evr_resource_types import TYPE_NAMES

    by_hash = {v: k for k, v in TYPE_NAMES.items()}
    out: dict = {}
    root = Path(root)
    if not root.is_dir() or not wanted:
        return out

    for directory in sorted(root.iterdir()):
        if not directory.is_dir():
            continue
        path = resource_path(root, directory.name, resource_hash)
        if path is None:
            continue
        data = path.read_bytes()
        seen: list = []
        for offset in range(0, len(data) - 8 + 1, 4):
            value = normalise_hash(
                int.from_bytes(data[offset:offset + 8], "little"))
            if value in wanted and value not in seen:
                seen.append(value)
        if seen:
            label = by_hash.get(normalise_hash(directory.name), directory.name)
            out[label] = seen
    return out


def scan_model_references(root: Path, model_hash, wanted: set) -> list:
    """CSymbol64s from `wanted` that appear in a model's own resource file.

    Returns them in FILE ORDER, deduplicated.  This replaces the per-record
    `CGMeshData` probe for the common case where the record layout is unknown or
    the model has no mesh-list at all: a model's file names the materials and
    shader sets its draws use, wherever in the file those references sit.

    ⚠ Order here is file order, not necessarily DRAW order.  It is good enough to
    resolve WHICH materials a model uses -- which is what makes it textured
    rather than untextured -- but a multi-draw model may pair them with the
    wrong submesh.  `materials_for_model` still prefers a real per-record read
    when the offset probe succeeds.
    """
    hits = scan_all_files(root, model_hash, wanted)
    if not hits:
        return []
    # Prefer the geometry resources, which is where a draw's material belongs;
    # fall back to whatever file did carry references.
    for preferred in ("CGMeshListResourceWin10",
                      "CGInstancedModelResourceWin10"):
        if preferred in hits:
            return hits[preferred]
    merged: list = []
    for values in hits.values():
        for value in values:
            if value not in merged:
                merged.append(value)
    return merged


def _mesh_records(root: Path, model_hash) -> list:
    """The `CGMeshData` records for a model, or `[]`.

    Reads the count-prefixed mesh table that opens a `CGMeshListResourceWin10`.
    Stub files (56 zero bytes) yield `[]`.
    """
    path = resource_path(root, MESH_LIST_RESOURCE, model_hash)
    if path is None:
        return []
    data = path.read_bytes()
    if len(data) < 4 or len(data) == 56:
        return []
    count = int.from_bytes(data[:4], "little")
    if count == 0 or 4 + count * MESH_STRIDE > len(data):
        return []
    return [data[4 + i * MESH_STRIDE: 4 + (i + 1) * MESH_STRIDE]
            for i in range(count)]


def probe_mesh_field_offset(root: Path, model_hashes, wanted: set, *,
                            sample: int = 400, label: str = "field") -> dict:
    """Find which offset inside `CGMeshData` holds a CSymbol64 from `wanted`.

    Used twice: once for the material hash, once for the shader set hash.  Both
    are 8-byte-aligned u64s somewhere in the 152-byte record, and neither field's
    position has been reversed -- so score every offset by how many records hold
    a value that is a real resource of that type, and take the winner.
    """
    scores: Counter = Counter()
    total = 0

    for model_hash in list(model_hashes)[:sample]:
        for record in _mesh_records(root, model_hash):
            total += 1
            for offset in range(0, MESH_STRIDE - 8 + 1, 8):
                value = int.from_bytes(record[offset:offset + 8], "little")
                if normalise_hash(value) in wanted:
                    scores[offset] += 1

    ranked = scores.most_common()
    best = ranked[0] if ranked else (None, 0)
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    coverage = (best[1] / total) if total else 0.0

    return {
        "label": label,
        "records_examined": total,
        "offset": best[0],
        "hits": best[1],
        "coverage": round(coverage, 4),
        "runner_up_hits": runner_up,
        "confident": bool(total and coverage >= 0.60
                          and best[1] >= 2 * max(runner_up, 1)),
        "scoreboard": ranked[:8],
    }


def probe_mesh_material_offset(root: Path, model_hashes,
                               material_hashes: set, *,
                               sample: int = 400) -> dict:
    """Find which offset inside `CGMeshData` holds the material `CSymbol64`.

    Scores every 8-byte-aligned offset by how many mesh records hold a u64 there
    that is a real material in the extract.  Returns the full scoreboard so a
    marginal winner is visible.

    The result is only trustworthy when the winner's score is both high in
    absolute terms and clearly ahead of the runner-up; `confident` encodes that
    as "covers at least 60% of records and at least double the runner-up".
    """
    scores: Counter = Counter()
    total = 0

    for model_hash in list(model_hashes)[:sample]:
        for record in _mesh_records(root, model_hash):
            total += 1
            for offset in range(0, MESH_STRIDE - 8 + 1, 8):
                value = int.from_bytes(record[offset:offset + 8], "little")
                if normalise_hash(value) in material_hashes:
                    scores[offset] += 1

    ranked = scores.most_common()
    best = ranked[0] if ranked else (None, 0)
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    coverage = (best[1] / total) if total else 0.0

    return {
        "records_examined": total,
        "offset": best[0],
        "hits": best[1],
        "coverage": round(coverage, 4),
        "runner_up_hits": runner_up,
        "confident": bool(total and coverage >= 0.60 and best[1] >= 2 * max(runner_up, 1)),
        "scoreboard": ranked[:8],
    }


def materials_for_model(root: Path, model_hash, material_hashes: set,
                        offset: int | None = None, vertex_counts=None) -> list:
    """Per-draw material hashes for a model, in mesh-record order.

    Returns `[]` when the probe found no offset or the model has no mesh table;
    the caller then falls back to ordering, and should say so in its output
    rather than pretending the link was resolved.
    """
    # THE per-draw material link: `CGRenderParams.matidx` (u32 at +32),
    # resolved through the model's own palette. A direct read of what the file
    # says draw k uses -- not a correspondence, not an ordering assumption.
    #
    # This is what seven earlier attempts were groping for. The mapping is a
    # PERMUTATION the file states outright (`ff5afb4e96897159`:
    # [5, 0, 1, 2, 6, 3, 7, 4, 7]), which is precisely why every positional
    # rule -- submesh index, palette index, part-major, LOD level -- failed:
    # there is no order to recover, only a table to read.
    per_draw = evr_model_materials.draw_material_indices(
        root, model_hash, material_hashes, vertex_counts)
    if per_draw:
        return per_draw

    if offset is not None:
        out = []
        for record in _mesh_records(root, model_hash):
            value = int.from_bytes(record[offset:offset + 8], "little")
            canonical = normalise_hash(value)
            out.append(canonical if canonical in material_hashes else "")
        if out:
            return out

    # No usable per-record read: fall back to scanning the model's own file for
    # references. Less precise about draw ORDER, but it is the difference
    # between a textured import and an untextured one.
    return scan_model_references(root, model_hash, material_hashes)


def build_texture_owner_index(ctx: "MaterialContext") -> dict:
    """`{texture_hash -> [material_hash, ...]}` by decoding every material once.

    This is the fallback for when the `CGMeshData` probe cannot find the
    model->material link.  A material that BINDS one of a model's textures is
    very probably that model's material -- not certainly, which is why this is
    a fallback and why `materials_by_texture_overlap` ranks rather than picks.

    Cost is one decode per material in the extract, done once and cached on the
    context.  It is skipped entirely when the probe was confident.
    """
    if ctx.texture_owner_index is not None:
        return ctx.texture_owner_index

    index: dict = {}
    for material_hash in sorted(ctx.material_hashes):
        try:
            material = evr_mat.load(ctx.root, material_hash,
                                    known_textures=ctx.texture_hashes)
        except Exception:
            continue
        if material is None:
            continue
        for bind in material.binds:
            index.setdefault(bind.textureassetid_hash, []).append(material_hash)

    ctx.texture_owner_index = index
    return index


def materials_by_texture_overlap(ctx: "MaterialContext", model_textures,
                                 *, limit: int = 8) -> list:
    """Materials ranked by how many of a model's textures they bind.

    Returns `[(material_hash, overlap_count), ...]`, best first.  Used only when
    the mesh-record link is unavailable; the caller should record that these
    bindings are INFERRED, because a shared texture atlas will legitimately tie
    one material to many models.
    """
    wanted = {normalise_hash(t) for t in (model_textures or ()) if t}
    if not wanted:
        return []

    index = build_texture_owner_index(ctx)
    scores: Counter = Counter()
    for texture in wanted:
        for material_hash in index.get(texture, ()):
            scores[material_hash] += 1
    return scores.most_common(limit)


# ---------------------------------------------------------------------------
# Spec construction
# ---------------------------------------------------------------------------

@dataclass
class MaterialContext:
    """Everything resolved once and reused across every model in a scene."""

    root: Path
    material_hashes: set = field(default_factory=set)
    texture_hashes: set = field(default_factory=set)
    #: CGShaderSetResourceWin10 hashes -- where texture ROLES live.
    shaderset_hashes: set = field(default_factory=set)
    names: dict = field(default_factory=dict)
    mesh_material_offset: int | None = None
    #: Offset in CGMeshData holding the shader set hash.
    mesh_shaderset_offset: int | None = None
    probe: dict = field(default_factory=dict)
    shaderset_probe: dict = field(default_factory=dict)
    #: `{texture_hash -> DXGI int}`, filled lazily as textures are touched.
    dxgi_by_tex: dict = field(default_factory=dict)
    #: `{texture_hash -> package-relative path}` for textures actually written.
    texture_files: dict = field(default_factory=dict)
    #: `{texture_hash -> [material_hash]}`, built lazily and only when the
    #: CGMeshData probe failed. None means "not built yet".
    texture_owner_index: dict | None = None
    #: `{shaderset_hash -> {role -> texture}}`. Shader sets are shared across
    #: many materials, so scanning each one once matters.
    shaderset_roles: dict = field(default_factory=dict)
    #: `{material_hash -> [shaderset_hash]}`. The join runs shader set ->
    #: material, so this is built by inverting, once, in `build_context`.
    #: ⚠ SPARSE -- only ~21% of materials are named by any shader set.
    shaderset_by_material: dict = field(default_factory=dict)
    #: `{texture_hash -> [shaderset_hash]}`. The route that actually carries a
    #: level: a model's texture list overlapped against what shader sets bind.
    shaderset_by_texture: dict = field(default_factory=dict)
    #: How each material found its shader set -- audit, printed by `summarise`.
    role_route_tally: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    #: How many textures the resolution cap actually shrank.
    textures_capped: int = 0
    #: The shared engine stub textures (a black texel, a flat normal, ...) that
    #: every material's slot table points unused slots at. Measured from the
    #: corpus by `evr_material_textures.build_default_textures`, not listed.
    default_textures: set = field(default_factory=set)
    #: Largest texture edge to WRITE (0 = uncapped). Blender's material
    #: preview uploads textures DECOMPRESSED, so a level with thousands of
    #: 2048-4096px maps blows past VRAM and crashes it.
    max_texture: int = 0
    #: Reduce every texture by this power-of-two factor relative to its own
    #: native size (2 = half resolution). Applied before `max_texture`.
    texture_divisor: int = 1


def build_context(root: Path, model_hashes, *, hash_lookup: Path | None = None,
                  probe_sample: int = 400) -> MaterialContext:
    """Resolve the corpus-wide facts a scene export needs."""
    root = Path(root)
    ctx = MaterialContext(
        root=root,
        material_hashes=evr_mat.all_material_hashes(root),
        texture_hashes=evr_mat.all_texture_hashes(root),
        shaderset_hashes=evr_shaderset.all_shaderset_hashes(root),
        names=load_hash_lookup(hash_lookup),
    )

    if not ctx.material_hashes:
        ctx.warnings.append(
            f"no CGMaterialResourceWin10 directory under {root} -- every "
            f"material will fall back to defaults and read as plain opaque"
        )
    if not ctx.texture_hashes:
        ctx.warnings.append(
            f"no cgtextureresourceWin10 directory under {root} -- the bind scan "
            f"has no anchor and will find nothing"
        )
    if not ctx.shaderset_hashes:
        ctx.warnings.append(
            f"no CGShaderSetResourceWin10 directory under {root} -- texture "
            f"ROLES live there, so without it nothing binds a texture to a "
            f"Principled socket and every material is untextured"
        )

    ctx.probe = probe_mesh_field_offset(
        root, model_hashes, ctx.material_hashes,
        sample=probe_sample, label="material")
    if ctx.probe["confident"]:
        ctx.mesh_material_offset = ctx.probe["offset"]
    else:
        ctx.warnings.append(
            f"CGMeshData material-offset probe was not confident "
            f"(offset={ctx.probe['offset']} coverage={ctx.probe['coverage']:.1%} "
            f"runner_up={ctx.probe['runner_up_hits']}) -- falling back to "
            f"per-model material ordering, so multi-material models may bind "
            f"the wrong draw"
        )

    ctx.shaderset_probe = probe_mesh_field_offset(
        root, model_hashes, ctx.shaderset_hashes,
        sample=probe_sample, label="shaderset")
    if ctx.shaderset_probe["confident"]:
        ctx.mesh_shaderset_offset = ctx.shaderset_probe["offset"]

    # The material -> shader set join, built by inversion. Measured: 0 of 400
    # materials reference a shader set, while 69 of 200 shader sets name a
    # material -- so there is no forward lookup and this index is the only way
    # to give a material its roles.
    if ctx.shaderset_hashes:
        print(f"  indexing {len(ctx.shaderset_hashes)} shader sets (one-time)...")
        ctx.shaderset_by_material, ctx.shaderset_by_texture = (
            evr_shaderset.build_indexes(
                root, ctx.material_hashes, ctx.texture_hashes))
        covered = len(ctx.shaderset_by_material)
        if ctx.material_hashes:
            print(f"  material route: {covered} of {len(ctx.material_hashes)} "
                  f"materials name a shader set "
                  f"({covered / len(ctx.material_hashes):.1%})")
        print(f"  texture route:  {len(ctx.shaderset_by_texture)} textures are "
              f"bound by some shader set")
        if not ctx.shaderset_by_texture:
            ctx.warnings.append(
                "no shader set bound any known texture, so no roles can be "
                "resolved and every material renders untextured"
            )

    # The material's OWN slot -> texture table is the authoritative route (see
    # `evr_material_textures`), but telling a real binding from the shared
    # engine stub every unused slot points at is a whole-corpus measurement, so
    # it happens once here rather than per material.
    if ctx.material_hashes:
        print(f"  measuring shared/default textures over "
              f"{len(ctx.material_hashes)} materials (one-time)...")
        ctx.default_textures = evr_mat_tex.build_default_textures(root)
        print(f"  {len(ctx.default_textures)} shared engine textures identified")
    return ctx


#: DXGI-classified roles, named so `le_mesh.materials` routes them to the right
#: Principled socket. These specific strings matter -- `layer0_diffuse_map` is
#: NOT routable, only `composite_diffuse` and `albedo_map` are.
DXGI_ROLE_BASE_COLOR = "layer0_albedo_map"
DXGI_ROLE_NORMAL = "layer0_normal_map"
DXGI_ROLE_COMPOSITE = "layer0_composite_components"

#: Minimum shared hex-prefix OR hex-suffix length (of a 16-hex-char CSymbol64)
#: for two texture hashes to cluster as the same "set" in `_texture_families`.
#: 8 hex chars = 32 bits; see that function's docstring for the two confirmed
#: examples this was measured against.
FAMILY_MIN_COMMON_HEX = 8


def _texture_families(tex_hashes) -> list[list[str]]:
    """Cluster texture hashes into self-consistent SETS by shared hash prefix
    OR suffix, first-appearance order.

    Verified on `ff5afb4e96897159` (576ed3f8428ebc4b instance i277's model --
    confirmed correct by eye against the real game, `docs/ECHO_VR.md` §7.1):
    its 34-texture `CGTextureStreamingResourceWin10` list is actually AT LEAST
    SEVEN unrelated texture sets bundled into one flat list. The confirmed
    base/normal/roughness triple for the model's main body --
    `c29a7d30d8154550` / `c29a7d30d818444e` / `c29a7d30d81e4e56` -- share the
    10-hex-char PREFIX `c29a7d30d8`. A DIFFERENT, wrong-for-that-material set
    -- `e42a132ddab1e343` / `e42a132edab1e343` / `e42a132fdab1e343` -- instead
    shares an 8-hex-char SUFFIX `dab1e343`, differing only in one prefix
    nibble (almost certainly a layer/variant index). Flat per-DXGI-class
    pooling (`roles_from_texture_list`'s old behaviour) mixed channels across
    these unrelated sets -- e.g. base colour from one set, normal from a
    completely different one -- which is what made textures look outright
    WRONG rather than merely guessed. Clustering first, then classifying
    within one cluster by DXGI format, keeps every channel of a chosen
    material's guess pointing at the same physical surface.

    A model's texture list is tens, not thousands, of entries, so a 32-bit
    prefix/suffix collision between genuinely unrelated hashes is not a
    realistic risk at `FAMILY_MIN_COMMON_HEX`; both confirmed examples above
    clear it with no margin to spare (10 and 8 chars respectively), so do not
    raise the threshold without re-checking against them.
    """
    def common_prefix(a, b):
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n

    def common_suffix(a, b):
        return common_prefix(a[::-1], b[::-1])

    clusters: list = []
    for h in tex_hashes:
        placed = False
        for cluster in clusters:
            if any(common_prefix(h, m) >= FAMILY_MIN_COMMON_HEX
                   or common_suffix(h, m) >= FAMILY_MIN_COMMON_HEX
                   for m in cluster):
                cluster.append(h)
                placed = True
                break
        if not placed:
            clusters.append([h])
    return clusters


#: Hand-confirmed `{material_hash -> {role_key: texture_hash}}` overrides,
#: checked BEFORE the shader-set and DXGI-family routes in `build_spec`. Each
#: entry here is a material a human visually confirmed against the real game
#: (not derived from any binding table -- an exhaustive search of
#: `CGShaderSetResourceWin10`, the model's own primary/GPU data, and
#: `CGMaterialResourceWin10`'s `trailing` table found no computable source for
#: this data; see docs/ECHO_VR.md §7.1's wrong-turn entries). Growing this
#: table by spot-confirming more materials is the intended workflow -- see
#: `propagate_confirmed_roles_to_lod_siblings` for how one confirmation
#: reaches every LOD level of the same physical part automatically.
#: ⛔ EMPTIED, deliberately. These were hand-confirmed against the running game
#: back when no computable source for texture roles was known: a human looked at
#: model `ff5afb4e96897159`, said which render was right, and the answer was
#: pinned onto materials `a96dba2cbec4a581` / `2993ccd5a8e33846` -- the materials
#: the old scan-order route happened to assign to that model's submeshes.
#:
#: `evr_material_textures` + `evr_model_materials` later showed BOTH halves of
#: that were misattributed. The texture the human confirmed
#: (`c29a7d30d8154550`) really belongs to `1e070bb9873c1e45`, which is section 5
#: of that model's OWN palette and whose slot table binds it outright. The two
#: pinned materials bind something else (`2993ccd5a8e33846`) or are all-defaults
#: (`a96dba2cbec4a581`, i.e. the engine draws it untextured).
#:
#: The observation was right; the attribution was wrong, because at the time
#: there was no way to tell which material a submesh actually drew. Keeping
#: these entries now would override real, structured data with that guess and
#: put the texture on two materials the game never gives it to. The override
#: mechanism is kept for a future genuine confirmation -- the DATA is dropped.
CONFIRMED_MATERIAL_ROLES: dict[str, dict[str, str]] = {}

CONFIRMED_MATERIAL_MODEL: dict[str, str] = {}


def propagate_confirmed_roles_to_lod_siblings(
        model_hash: str,
        material_lod_group: dict[str, int],
        materials_by_group: dict[int, list[str]]) -> dict[str, dict[str, str]]:
    """Extend `CONFIRMED_MATERIAL_ROLES` to every LOD sibling of a confirmed one.

    A confirmed material's LOD siblings (same physical part, same bounding
    box, fewer triangles -- see `evr_scene_extract._group_submeshes_by_lod`)
    are, with overwhelming likelihood, the SAME surface and therefore the SAME
    textures: nothing about simplifying a mesh changes what it's made of.
    `material_lod_group` is `{material_hash -> lod_group_id}` and
    `materials_by_group` its inverse, both built once per model in the
    materials phase, for `model_hash` specifically. Returns a dict to MERGE on
    top of (not replace) `CONFIRMED_MATERIAL_ROLES` for this model's
    materials -- callers should check the merged result, not the module-level
    table alone, so a confirmation made on one LOD level is honoured for its
    siblings without having to list every one of them by hand.

    Only propagates when `model_hash` is the model `CONFIRMED_MATERIAL_MODEL`
    says the confirmation was actually made on -- see that table's docstring
    for why a raw hash match against `material_lod_group` alone is not enough.
    """
    extended: dict[str, dict[str, str]] = {}
    for material_hash, roles in CONFIRMED_MATERIAL_ROLES.items():
        if CONFIRMED_MATERIAL_MODEL.get(material_hash) != model_hash:
            continue
        group = material_lod_group.get(material_hash)
        if group is None:
            continue
        for sibling in materials_by_group.get(group, ()):
            if sibling not in CONFIRMED_MATERIAL_ROLES:
                extended[sibling] = roles
    return extended


def roles_from_texture_list(ctx: "MaterialContext", model_textures,
                            *, rank: int = 0) -> dict:
    """`{role -> texture}` from a model's OWN texture list, classified by format.

    The last-resort route, and the only one that needs no shader set at all.
    It rests on things that are measured rather than inferred: the ordered
    per-model list in `CGTextureStreamingResourceWin10`, the DXGI format in
    each `cgtextureresourceWin10` header, and -- new -- clustering that list
    into self-consistent texture SETS before classifying (`_texture_families`;
    see its docstring for why: a model's texture list routinely bundles
    several unrelated materials' textures, and classifying the whole list
    flat mixes channels across them).

    Within the chosen family, classification is in priority order per slot:

    * **BC5** (82-84) -> normal map.  Two-channel tangent-space normals are
      unambiguous; nothing else ships in BC5.
    * **an `_SRGB` format** -> base colour.  A colour-space view means the
      texture is authored as colour, which is exactly what base colour is.
    * **anything else, linear** -> `composite_components` (roughness/AO).

    ⚠ This is a HEURISTIC and the output labels it `SOURCE_FORMAT`, never
    `SOURCE_ARRAY`.  It recovers base colour, normal and a roughness/AO channel.
    It cannot recover emission, blend masks, specular tint or layer structure --
    those exist only in the shader set's named binds.  It is the difference
    between a textured import and an untextured one, not between a partial
    import and a complete one.

    ⭐ **`rank` selects WHICH FAMILY** -- the material's position among its
    model's DISTINCT materials (0, 1, 2, ...), same convention as before, just
    now indexing families instead of a flat per-class pool. Sibling materials
    on one model round-robin onto DIFFERENT texture SETS instead of colliding
    on the first (`docs/ECHO_VR.md` §7.1 -- "why several models wear the same
    texture"). Still a guess as to WHICH set is right -- see
    `CONFIRMED_MATERIAL_ROLES` for the only verified answers that exist -- but
    every channel it picks now points at the same physical surface.

    ⚠ **Ranking is restricted to families that have a base-colour candidate,
    when any exist.** A model's texture list routinely bundles small,
    single-purpose families too (one standalone normal map, one shared utility
    roughness texture with no colour counterpart -- see `4ae6b4f073d88ae9` in
    `_texture_families`'s worked example, which is exactly this). Ranking over
    EVERY family blind let `rank` land on one of those and return ONLY a
    normal or ONLY a roughness channel -- no base colour, so the mesh rendered
    as a flat, factor-only grey instead of textured at all. Measured on
    `576ed3f8428ebc4b`: 82 of 161 LOD0 meshes had no base-colour file before
    this filter. A family missing normal/roughness still looks like a real,
    if incomplete, material; a family missing base colour looks broken.
    """
    tex_hashes: list = []
    seen: set = set()
    for raw in (model_textures or ()):
        tex_hash = normalise_hash(raw)
        if tex_hash and tex_hash not in seen:
            seen.add(tex_hash)
            tex_hashes.append(tex_hash)
    if not tex_hashes:
        return {}

    def dxgi_of(tex_hash):
        dxgi = ctx.dxgi_by_tex.get(tex_hash)
        if dxgi is None:
            resource = evr_tex.load(ctx.root, tex_hash)
            if resource is None:
                return None
            dxgi = resource.format
            ctx.dxgi_by_tex[tex_hash] = dxgi
        return dxgi

    families = _texture_families(tex_hashes)

    def classify(family):
        normal: list = []
        base: list = []
        base_fallback: list = []
        composite: list = []
        for tex_hash in family:
            dxgi = dxgi_of(tex_hash)
            if dxgi is None:
                continue
            if dxgi in evr_tex.BC5_FORMATS:
                normal.append(tex_hash)
            elif dxgi in evr_tex.SRGB_FORMATS:
                base.append(tex_hash)
            elif dxgi in evr_tex.COLOR_CAPABLE_UNORM_FORMATS:
                # Same block format as a real base-colour texture, just not
                # flagged `_SRGB` on this asset -- only trusted as base colour
                # when the family has no explicit `_SRGB` member (see
                # COLOR_CAPABLE_UNORM_FORMATS's docstring). When a real `_SRGB`
                # texture IS present, this one is unclaimed rather than base
                # colour -- fold it into composite instead of dropping it.
                base_fallback.append(tex_hash)
            else:
                composite.append(tex_hash)
        if base:
            composite = composite + base_fallback
        else:
            base = base_fallback
        return normal, base, composite

    classified = [classify(f) for f in families]
    with_base = [i for i, (_n, b, _c) in enumerate(classified) if b]
    pool = with_base or list(range(len(families)))
    normal_candidates, base_color_candidates, composite_candidates = (
        classified[pool[rank % len(pool)]])

    roles: dict = {}
    if normal_candidates:
        roles[DXGI_ROLE_NORMAL] = normal_candidates[0]
    if base_color_candidates:
        roles[DXGI_ROLE_BASE_COLOR] = base_color_candidates[0]
    if composite_candidates:
        roles[DXGI_ROLE_COMPOSITE] = composite_candidates[0]
    return roles


def _texture_file_name(tex_hash: str) -> str:
    return f"textures/{tex_hash}.dds"


def extract_textures(ctx: MaterialContext, tex_hashes, out_dir: Path,
                     *, packfile_hash: str | None = None) -> dict:
    """Write each texture's DDS into `out_dir`; return `{hash -> rel path}`.

    Records the DXGI format on `ctx` as a side effect, because every texture we
    write is one whose format the spec builder is about to need.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: dict = {}

    for raw in tex_hashes:
        tex_hash = normalise_hash(raw)
        if not tex_hash or tex_hash in written:
            continue

        if tex_hash in ctx.texture_files:
            written[tex_hash] = ctx.texture_files[tex_hash]
            continue

        resource = evr_tex.load(ctx.root, tex_hash)
        if resource is None:
            continue
        ctx.dxgi_by_tex[tex_hash] = resource.format

        destination = out_dir / f"{tex_hash}.dds"
        if not destination.exists() or destination.stat().st_size <= 128:
            blob, note = evr_tex.rebuild_dds(
                ctx.root, tex_hash, packfile_hash=packfile_hash)
            if not blob:
                ctx.warnings.append(f"texture {tex_hash}: {note}")
                continue
            # Relative scaling first, then the absolute ceiling -- so the two
            # can be combined and the ceiling always wins.
            if ctx.texture_divisor > 1:
                blob, scale_note = evr_tex.scale_dds_resolution(
                    blob, ctx.texture_divisor)
                if scale_note.startswith("capped"):
                    ctx.textures_capped += 1
            if ctx.max_texture:
                blob, cap_note = evr_tex.cap_dds_resolution(blob, ctx.max_texture)
                if cap_note.startswith("capped"):
                    ctx.textures_capped += 1
            destination.write_bytes(blob)

        relative = _texture_file_name(tex_hash)
        written[tex_hash] = relative
        ctx.texture_files[tex_hash] = relative

    return written


def build_spec(ctx: MaterialContext, material_hash: str, *,
               shaderset_hash=None, model_textures=None,
               out_dir: Path | None = None,
               packfile_hash: str | None = None,
               rank: int = 0,
               confirmed_roles: dict | None = None) -> dict | None:
    """Produce the v2 `spec` dict from a material AND its shader set.

    The split matters and is not arbitrary:

    * **`CGShaderSetResourceWin10` -> texture ROLES.**  Measured: 6231 binds
      over 55 distinct inputnames, 95.4%% of them nameable -- the full PBR set
      (`composite_diffuse`, `composite_specular`, `composite_components`,
      `composite_normals`, `emissive`, `detail_normal`, `blend_mask`, `rim`,
      `flowmap`, `opacity`, `back_lighting`) across layers 0..3.
    * **`CGMaterialResourceWin10` -> SCALARS.**  `k_alpha`, `mattype`,
      `blendmode`, `bakecolor`, per-layer emissive knobs.

    ⛔ The material's `auxillaryinputs` is NOT a source of roles.  Across 1727
    shipped materials it carries exactly two inputnames, `cutting_cut_decal`
    and `cutting_scorch_decal`.  An earlier version of this function read roles
    from there and got two per material; it is a decal slot, not the surface
    texture table.
    """
    material = evr_mat.load(ctx.root, material_hash, known_textures=())
    if material is None:
        return None

    for warning in material.warnings:
        ctx.warnings.append(f"material {normalise_hash(material_hash)}: {warning}")

    # A hand-confirmed answer beats every other route -- there is no
    # computable source for this data (see CONFIRMED_MATERIAL_ROLES's
    # docstring), so once a material is spot-confirmed against the real game,
    # trust it over a shader-set scan or a DXGI guess. `confirmed_roles` is
    # the per-model dict the caller merged from the base table plus LOD-sibling
    # propagation; fall back to the bare module table when the caller has no
    # LOD info to propagate through (e.g. a direct build_spec call in tests).
    overrides = confirmed_roles if confirmed_roles is not None else CONFIRMED_MATERIAL_ROLES
    confirmed = overrides.get(normalise_hash(material_hash))
    if confirmed:
        role_textures = dict(confirmed)
        referenced = [t for t in role_textures.values() if t]
        if out_dir is not None and referenced:
            extract_textures(ctx, referenced, out_dir, packfile_hash=packfile_hash)
        else:
            for tex in referenced:
                if tex not in ctx.dxgi_by_tex:
                    resource = evr_tex.load(ctx.root, tex)
                    if resource is not None:
                        ctx.dxgi_by_tex[tex] = resource.format
        scalars = _scalars_from_material(material)
        # Distinguish the material a human actually looked at from one that
        # inherited the answer via LOD-sibling propagation -- both are in
        # `overrides` (the caller already merged them), only the base module
        # table is the direct confirmation.
        source = (role_index.SOURCE_CONFIRMED
                  if normalise_hash(material_hash) in CONFIRMED_MATERIAL_ROLES
                  else role_index.SOURCE_LOD_SIBLING)
        role_sources = {role: source for role in role_textures}
        ctx.role_route_tally[source] = ctx.role_route_tally.get(source, 0) + 1
        return le_materials.build_material_spec(
            f"{normalise_hash(material_hash)}__{normalise_hash(shaderset_hash)}",
            shaderset_hash=normalise_hash(shaderset_hash),
            material_hash=normalise_hash(material_hash),
            role_textures=role_textures,
            dxgi_by_tex=ctx.dxgi_by_tex,
            scalars=scalars,
            texture_files=ctx.texture_files,
            role_sources=role_sources,
            texture_names={})

    # ── ROUTE 0: the material's OWN slot -> texture table ────────────────────
    # `CGMaterialResourceWin10`'s sixth container is a
    # `CMap<CSymbol64 slot, CSymbol64 texture>` -- the per-material texture
    # binding, on disk, no inference (see `evr_material_textures`). It outranks
    # every route below because those all exist to RECONSTRUCT this fact:
    # the shader-set routes guess WHICH shader set a material uses, and the
    # DXGI route guesses which of a model's textures fills which socket. This
    # says so directly.
    #
    # Validated end to end against the one material a human checked against the
    # running game: `1e070bb9873c1e45` binds all three hand-confirmed textures,
    # each in the role this route assigns it.
    raw_table = evr_mat_tex.load_slot_textures(ctx.root, material_hash)
    table = evr_mat_tex.real_slot_textures(raw_table, ctx.default_textures)

    # ⛔ An all-defaults table does NOT mean "draw untextured" -- tested and
    # reverted. The reasoning looked sound (a material pointing every slot at
    # an engine stub should render flat, so falling through to a guess puts a
    # texture where the game has none) and it did fix the specific case it was
    # derived from. But it made the scene visibly WORSE overall (user-verified):
    # large surfaces that should be textured went flat.
    #
    # The reason is the layering. A material's slot table holds its OVERRIDES;
    # where it overrides nothing, the texture comes from the SHADER SET, not
    # from nowhere. `ff5afb4e96897159`'s records all name shaderset
    # `d46780386d6debab`, and 1344 of 3692 shader sets carry real binds. So an
    # all-defaults table has to keep falling through to the shader-set route
    # below -- only the final DXGI guess is truly a guess.
    if table:
        role_textures = evr_mat_tex.roles_from_material_table(table, ctx.names)

        # ⛔ Do NOT promote an unroutable role to base colour. Tried and
        # reverted. `a2630eab179ee4a2`'s only binding is a
        # `layer0_rimlighting_map`; promoting it put a rim/fresnel pattern on
        # props as BOTH base colour and alpha -- visibly wrong (user-verified
        # on lobby instance i299) and partly transparent as a bonus.
        #
        # A rim map is not albedo. The engine draws these surfaces from
        # `bakecolor` plus a rim term the Principled BSDF cannot express, so a
        # flat colour with NO texture is the faithful result. "This prop looks
        # untextured" is the correct outcome here, not a bug to paper over.
        if role_textures:
            referenced = [t for t in role_textures.values() if t]
            if out_dir is not None and referenced:
                extract_textures(ctx, referenced, out_dir,
                                 packfile_hash=packfile_hash)
            else:
                for tex in referenced:
                    if tex not in ctx.dxgi_by_tex:
                        resource = evr_tex.load(ctx.root, tex)
                        if resource is not None:
                            ctx.dxgi_by_tex[tex] = resource.format
            ctx.role_route_tally["material_table"] = (
                ctx.role_route_tally.get("material_table", 0) + 1)
            return le_materials.build_material_spec(
                f"{normalise_hash(material_hash)}__{normalise_hash(shaderset_hash)}",
                shaderset_hash=normalise_hash(shaderset_hash),
                material_hash=normalise_hash(material_hash),
                role_textures=role_textures,
                dxgi_by_tex=ctx.dxgi_by_tex,
                scalars=_scalars_from_material(material),
                texture_files=ctx.texture_files,
                role_sources={r: role_index.SOURCE_ARRAY for r in role_textures},
                texture_names={})

    # Roles come from the shader set's own structured element/sub-table layout
    # (`evr_shaderset._parse_structured`, EOF-exact decode) -- not an anchored
    # byte scan. No anchor set is needed: a structural decode either accounts
    # for the whole file or it returns nothing, so there is nothing left for an
    # anchor to validate against. About half of shader sets legitimately have
    # zero bound textures in this table (the material's texture comes from
    # elsewhere), which is why the routes below still fall through to
    # texture-overlap / DXGI rather than treating "no binds" as an error.
    #
    # Resolve the shader set, best route first.
    #   1. whatever the caller read from the mesh record
    #   2. the material -> shader set index (direct, but only ~21% coverage)
    #   3. texture overlap: rank shader sets by how many of THIS MODEL's
    #      textures they bind. Indirect, but it rests on the per-model texture
    #      inventory, so it carries the levels the direct edge cannot.
    role_source = "mesh_record" if shaderset_hash else "none"
    if not shaderset_hash:
        candidates = ctx.shaderset_by_material.get(normalise_hash(material_hash))
        if candidates:
            shaderset_hash = candidates[0]
            role_source = "material_index"
    if not shaderset_hash and model_textures:
        ranked = evr_shaderset.shadersets_for_textures(
            ctx.shaderset_by_texture, model_textures)
        if ranked:
            shaderset_hash = ranked[0][0]
            role_source = f"texture_overlap({ranked[0][1]})"

    role_textures: dict = {}
    if shaderset_hash:
        key = normalise_hash(shaderset_hash)
        if key in ctx.shaderset_roles:
            role_textures = ctx.shaderset_roles[key]
        else:
            role_textures = evr_shaderset.role_textures(
                ctx.root, shaderset_hash, ctx.names)
            ctx.shaderset_roles[key] = role_textures
        if not role_textures:
            ctx.warnings.append(
                f"shaderset {normalise_hash(shaderset_hash)} yielded no texture "
                f"binds"
            )
            # The mesh record's shader-set field is not always a shader set --
            # `mpl_arena_a`'s sky material b149cb9cf8b7a9c3 arrives with its OWN
            # hash there, so the lookup finds nothing and the material drops to
            # the DXGI guess, which picked the sky's layer2 BLEND MASK as base
            # colour. That is the wrong-looking sky.
            #
            # The material -> shader set index still resolves it (7 candidates,
            # the first three agreeing on all binds), so consult that before
            # giving up. Guessing by texture format is the last resort, not the
            # second.
            for candidate in ctx.shaderset_by_material.get(
                    normalise_hash(material_hash)) or ():
                key = normalise_hash(candidate)
                found = ctx.shaderset_roles.get(key)
                if found is None:
                    found = evr_shaderset.role_textures(
                        ctx.root, candidate, ctx.names)
                    ctx.shaderset_roles[key] = found
                if found:
                    role_textures = found
                    shaderset_hash = candidate
                    role_source = "material_index_fallback"
                    break

    # Last resort: classify the model's own textures by DXGI format. No shader
    # set needed. Recovers base colour + normal + roughness, and nothing richer.
    # `rank` (this material's position among its model's distinct materials)
    # round-robins the pick so sibling materials on the same model stop wearing
    # the identical first-match texture -- see roles_from_texture_list.
    if not role_textures and model_textures:
        role_textures = roles_from_texture_list(ctx, model_textures, rank=rank)
        if role_textures:
            role_source = f"dxgi({len(role_textures)})"

    if not role_textures:
        ctx.warnings.append(
            f"material {normalise_hash(material_hash)}: no shader set and no "
            f"usable texture list -- renders untextured"
        )

    referenced = [t for t in role_textures.values() if t]

    if out_dir is not None and referenced:
        extract_textures(ctx, referenced, out_dir, packfile_hash=packfile_hash)
    else:
        for tex in referenced:
            if tex not in ctx.dxgi_by_tex:
                resource = evr_tex.load(ctx.root, tex)
                if resource is not None:
                    ctx.dxgi_by_tex[tex] = resource.format

    scalars = _scalars_from_material(material)

    # Provenance, honestly. A role we could NAME came from this material's own
    # SShaderInputData row (`SOURCE_ARRAY`). A role that fell back to
    # `unknown_s{slot}` did not: the bind is real, but the role is whatever the
    # DXGI fallback in `classify_roles_layered` decides, which is `SOURCE_FORMAT`.
    # Collapsing both to "array" would let a guessed role read as a declared one.
    # A DXGI-classified role is named, but it was GUESSED from a pixel format --
    # marking it SOURCE_ARRAY would let a guess read as a shader-set-declared
    # binding, which is the one thing the provenance field exists to prevent.
    # The shader set's UV scale (`SShaderInputData.uscale/vscale`), which this
    # pipeline parsed and discarded -- ~10% of binds are non-unit, and without
    # it those materials tile at the wrong density.
    uv_scale = (1.0, 1.0)
    if shaderset_hash:
        uv_scale = evr_shaderset.dominant_uv_scale(ctx.root, shaderset_hash)

    from_dxgi = role_source.startswith("dxgi")
    role_sources = {
        role: (role_index.SOURCE_FORMAT
               if from_dxgi or role.startswith("unknown_s")
               else role_index.SOURCE_ARRAY)
        for role in role_textures
    }
    ctx.role_route_tally[role_source] = ctx.role_route_tally.get(role_source, 0) + 1

    spec = le_materials.build_material_spec(
        f"{normalise_hash(material_hash)}__{normalise_hash(shaderset_hash)}",
        shaderset_hash=normalise_hash(shaderset_hash),
        material_hash=normalise_hash(material_hash),
        role_textures=role_textures,
        dxgi_by_tex=ctx.dxgi_by_tex,
        scalars=scalars,
        texture_files=ctx.texture_files,
        role_sources=role_sources,
        texture_names={},
    )
    if uv_scale != (1.0, 1.0):
        spec["uv_scale"] = [uv_scale[0], uv_scale[1]]
    return spec


def _scalars_from_material(material) -> dict:
    """Project a decoded Echo VR material onto the scalar dict the spec wants.

    `le_mesh.material_scalars.decode_material_scalars` cannot be called directly
    -- it hard-codes Lone Echo's 0x160 header and its `bakeemissivecolor`, which
    Echo VR does not have.  The named-scalar lookups it performs are all keyed by
    property-name hash, though, so those carry over unchanged.
    """
    from le_mesh import material_scalars as msc

    props = material.props
    words = material.words
    slots = material.slots

    def read(name_hash, arity=1):
        index = slots.get(name_hash)
        if index is None or index >= len(words):
            return None
        if arity <= 1:
            return float(words[index])
        return [float(x) for x in words[index:min(index + arity, len(words))]]

    header = material.header
    scalars = {
        "base_color_factor": list(header.bakecolor),
        # Echo VR's SGMaterialData carries NO bakeemissivecolor field. Emission
        # therefore comes only from an emissive MAP plus its per-layer intensity;
        # a flat authored emissive colour is genuinely not present in the data,
        # and inventing one here would be a fabrication, not a fallback.
        "emissive_color": [0.0, 0.0, 0.0],
        "bake_emissive_nonzero": False,
        "mattype": int(header.mattype),
        "blend_mode": int(header.blendmode),
        "flags": int(header.flags),
        "flag_names": msc.flag_names(header.flags),
        "materialfx": header.materialfx,
        "double_sided": bool(header.flags & msc.EFLAGS["eDoubleSided"]),
        "named_scalars": {},
        "named_scalars_resolved": {},
    }

    alpha = props.get(msc.HASH_K_ALPHA)
    scalars["alpha"] = float(
        alpha if alpha is not None
        else msc.AUTHORED_DEFAULTS_GLOBAL["k_alpha"])

    # `symbol64` is the CSymbol64 hash; `material_scalars` precomputes the three
    # globals we need, so use those constants rather than re-hashing the names.
    defaults_applied = []
    for key, name, name_hash in (
        ("emissive_scale", "k_emissive_scale", msc.HASH_K_EMISSIVE_SCALE),
        ("alpha_threshold", "k_alpha_threshold", msc.HASH_K_ALPHA_THRESHOLD),
        ("refractive_index", "k_refractive_index", msc.HASH_K_REFRACTIVE_INDEX),
    ):
        value = props.get(name_hash)
        if value is None:
            scalars[key] = msc.AUTHORED_DEFAULTS_GLOBAL[name]
            defaults_applied.append(name)
        else:
            scalars[key] = float(value)
    scalars["scalar_defaults_applied"] = defaults_applied

    # Every property we could put a name to, for audit. `build_name_table` is
    # the generated authored-name table; a hit is a verified preimage because
    # the key IS symbol64(name).
    name_table = msc.build_name_table()
    scalars["named_scalars"] = {
        name_table[h]: float(v) for h, v in props.items() if h in name_table
    }

    # Does this surface receive lighting at all?
    #
    # An EVR material carries `enablebakedlighting` / `enablesglighting` /
    # `enabledynamiclighting`. When ALL are 0 the surface is lit by nothing --
    # it is self-illuminated, which is what a light-fixture panel is.
    #
    # This is the only signal in the shipped data that separates a REAL
    # emissive map from one serving as ambient occlusion. Both use the same
    # `layerN_emissive_map` role, both are usually greyscale masks, and the
    # emissive tint that would otherwise distinguish them is one of the ~126
    # properties whose authored name is still uncracked. Without this flag a
    # consumer has to pick one treatment and be wrong for the other half.
    # ⚠ These flags are u32 INTEGERS sharing the float value blob. Read as a
    # float, `1` comes back as the denormal 1.401298464324817e-45, so a naive
    # `> 0.0` test happens to work while every printed value looks like noise.
    # Recover the integer instead of comparing garbage.
    def _flag(name):
        raw = scalars["named_scalars"].get(name)
        if raw is None:
            return None
        try:
            return struct.unpack("<I", struct.pack("<f", float(raw)))[0]
        except (struct.error, ValueError, OverflowError):
            return None

    lighting = {name: _flag(name) for name in msc.EVR_LIGHTING_FLAGS}
    scalars["lighting_flags"] = lighting
    known = [v for v in lighting.values() if v is not None]
    scalars["receives_lighting"] = (
        bool(any(v for v in known)) if known else None)
    # All lighting off => the surface is lit by nothing, i.e. self-illuminated.
    # On mpl_arena_a this holds for 10 of 64 materials that carry an emissive
    # map, against 3 of 139 that do not -- an 8x enrichment, so it is a real
    # signal. It is NOT sufficient on its own: the fixture at mesh 160 reads
    # fully lit yet glows in game, so some genuinely emissive surfaces are also
    # lit surfaces. Recovering `emissive_tint_color` (authored name confirmed in
    # `core/materials/base/ubermaterial.radmat`, hash still uncracked) is what
    # would settle it.
    scalars["self_illuminated"] = (
        bool(known) and not any(v for v in known))

    # Per-layer emissive/opacity knobs, keyed by the same hashes Lone Echo uses.
    layers = []
    emissive_layers = []
    for layer in range(msc.N_DECLARED_LAYERS):
        intensity = read(msc.HASH_EMISSIVE_INTENSITY[layer])
        tint = read(msc.HASH_EMISSIVE_TINT[layer], 4)
        opacity = read(msc.HASH_OPACITY_TINT[layer], 4)
        layers.append({
            "index": layer,
            "emissive_intensity": intensity,
            "emissive_tint": tint,
            "opacity_tint": opacity,
            "uv_offset": None,
            "uv_offsets": {},
        })
        if intensity:
            emissive_layers.append(layer)
    scalars["layers"] = layers
    scalars["emissive_layer_indices"] = emissive_layers
    scalars["emissive_intensity"] = float(
        layers[0]["emissive_intensity"] or 1.0) if layers else 1.0
    scalars["is_emissive"] = bool(emissive_layers)

    # Both tables are int-keyed dicts in `material_scalars`.
    scalars["mattype_name"] = msc.MATTYPE_NAMES.get(
        header.mattype, f"unknown_mattype_{header.mattype}")
    scalars["blend_mode_name"] = msc.BLENDMODE_NAMES.get(
        header.blendmode, f"unknown_blendmode_{header.blendmode}")

    return scalars


# ---------------------------------------------------------------------------
# Sidecar assembly
# ---------------------------------------------------------------------------

@dataclass
class MaterialTable:
    """Accumulates `matidx`-keyed entries across a scene export."""

    entries: list = field(default_factory=list)
    #: `(material_hash, shaderset_hash) -> matidx`, built once and reused.
    by_hash: dict = field(default_factory=dict)

    def intern(self, ctx: MaterialContext, material_hash: str, *,
               shaderset_hash=None, model_textures=None,
               out_dir: Path | None = None,
               packfile_hash: str | None = None,
               rank: int = 0,
               confirmed_roles: dict | None = None) -> int | None:
        """Return the `matidx` for a (material, shader set, rank) triple.

        Keyed on all three: the same material can be paired with different
        shader sets, and those are genuinely different materials to render --
        collapsing them on the material hash alone would give one of them the
        other's textures. `rank` joins the key for the same reason: it is what
        makes `roles_from_texture_list`'s round-robin DXGI fallback pick a
        different texture per sibling material (see `build_spec`), and caching
        across ranks would hand that fix's output right back to a single shared
        entry.

        Exception: a material with a `confirmed_roles` entry ignores shader set
        and rank in the cache key. Its content does not depend on either (the
        override wins over both), so the same material paired with two
        different DXGI-fallback shader-set guesses would otherwise write out
        the identical spec twice under two matidx values -- harmless, just
        wasteful, and it happens routinely because the same physical part's
        several LOD materials each get their own (usually wrong) shader-set
        guess from `build_spec`'s texture-overlap route.
        """
        canonical = normalise_hash(material_hash)
        if not canonical:
            return None
        confirmed = (confirmed_roles or {}).get(canonical)
        if confirmed:
            key = (canonical, "__confirmed__", 0)
        else:
            key = (canonical, normalise_hash(shaderset_hash), rank)
        if key in self.by_hash:
            return self.by_hash[key]

        spec = build_spec(ctx, canonical, shaderset_hash=shaderset_hash,
                          model_textures=model_textures,
                          out_dir=out_dir, packfile_hash=packfile_hash,
                          rank=rank, confirmed_roles=confirmed_roles)
        if spec is None:
            return None

        matidx = len(self.entries)
        self.entries.append({"matidx": matidx, "shdidx": 0, "spec": spec})
        self.by_hash[key] = matidx
        return matidx

    def to_sidecar(self, master: str, ctx: MaterialContext) -> dict:
        """The full `_materials.json` document."""
        return {
            "format": "le_scene_materials",
            "version": SIDECAR_VERSION,
            "master": normalise_hash(master),
            "source": "echo_vr",
            "materials": [dict(entry, **_v1_projection(entry, ctx))
                          for entry in self.entries],
            "diagnostics": {
                "mesh_material_offset": ctx.mesh_material_offset,
                "mesh_material_probe": ctx.probe,
                "mesh_shaderset_offset": ctx.mesh_shaderset_offset,
                "mesh_shaderset_probe": ctx.shaderset_probe,
                "materials_built": len(self.entries),
                "role_routes": dict(ctx.role_route_tally),
                "textures_written": len(ctx.texture_files),
                "warnings": ctx.warnings[:200],
                "warning_count": len(ctx.warnings),
            },
        }


def _v1_projection(entry: dict, ctx: MaterialContext) -> dict:
    """The legacy flat fields, DERIVED from the v2 spec so they cannot disagree.

    Mirrors `le_scene_materials._v1_entry`.  Emitted alongside `spec` purely so
    an older add-on build still gets base colour and normal instead of nothing.
    """
    spec = entry["spec"]
    channels = spec.get("channels") or {}
    base = channels.get("base_color") or {}
    normal = channels.get("normal") or {}
    factor = spec.get("base_color_factor", [1.0, 1.0, 1.0, 1.0])
    return {
        "material_hash": spec.get("material_hash") or None,
        "mattype": int(spec.get("mattype", 0)),
        "base_color": [float(x) for x in list(factor)[:3]],
        "basecolor_texture": base.get("texture"),
        "basecolor_dds": base.get("file") or None,
        "basecolor_role": base.get("role_key"),
        "normal_texture": normal.get("texture"),
        "owning_archive": None,
        "double_sided": bool(spec.get("double_sided", False)),
    }


def audit_inputnames(root: Path, hash_lookup: Path | None = None,
                     limit: int | None = None) -> dict:
    """Can we NAME Echo VR's `SShaderInputData` inputname hashes?

    This is the question the whole material pipeline turns on.  A named
    inputname becomes a real role (`layer0_albedo_map`) and routes to the right
    Principled socket.  An unnamed one becomes `unknown_s{slot}` and can only be
    rescued by the DXGI fallback, which distinguishes base colour from normal
    and nothing else -- no roughness, no specular, no emission.

    Reports every distinct inputname by frequency, and which resolver (if any)
    could name it.  The `unresolved` list, ordered by how many materials use it,
    is the wordlist-cracking worklist.
    """
    root = Path(root)
    names = load_hash_lookup(hash_lookup)
    textures = evr_mat.all_texture_hashes(root)
    material_hashes = sorted(evr_mat.all_material_hashes(root))
    if limit:
        material_hashes = material_hashes[:limit]

    by_hash: Counter = Counter()
    slots_for: dict = {}
    materials_with_binds = 0

    for material_hash in material_hashes:
        try:
            material = evr_mat.load(root, material_hash, known_textures=textures)
        except Exception:
            continue
        if material is None or not material.binds:
            continue
        materials_with_binds += 1
        for bind in material.binds:
            by_hash[bind.inputname_hash] += 1
            slots_for.setdefault(bind.inputname_hash, set()).add(bind.slot)

    resolved: dict = {}
    unresolved: list = []
    for inputname, count in by_hash.most_common():
        role = le_materials.role_for_inputname(inputname, None, names)
        if role.startswith("unknown_s"):
            unresolved.append({
                "inputname": inputname,
                "binds": count,
                "slots": sorted(slots_for.get(inputname, ())),
            })
        else:
            resolved[inputname] = {"role": role, "binds": count}

    total = sum(by_hash.values())
    named = sum(v["binds"] for v in resolved.values())
    return {
        "materials_scanned": len(material_hashes),
        "materials_with_binds": materials_with_binds,
        "total_binds": total,
        "distinct_inputnames": len(by_hash),
        "binds_named": named,
        "binds_unnamed": total - named,
        "hash_lookup_entries": len(names),
        "resolved": resolved,
        "unresolved": unresolved[:40],
    }


def crack_inputnames(unresolved, extra_words=()) -> dict:
    """Brute-force candidate parameter names against unresolved inputname hashes.

    Builds `layer{N}_{suffix}` over every suffix `le_mesh.materials` knows plus
    the Echo VR-plausible extras, hashes each with the engine's CSymbol64, and
    reports any that match.  A hit is a VERIFIED preimage, not a guess -- the
    key IS the hash of the name -- which is why this is worth trying before
    concluding the names are unknowable.
    """
    from le_mesh import material_scalars as msc

    wanted = {
        (u["inputname"] if isinstance(u, dict) else str(u)).lower()
        for u in unresolved
    }

    suffixes = set()
    for group in le_materials.CHANNEL_ROLE_SUFFIXES.values():
        suffixes.update(group)
    suffixes.update(extra_words)
    suffixes.update({
        "diffuse_map", "color_map", "colour_map", "basecolor_map",
        "base_color_map", "detail_map", "detail_normal_map", "mask_map",
        "ao_map", "occlusion_map", "metallic_map", "metalness_map",
        "gloss_map", "glossiness_map", "roughness_map", "orm_map",
        "composite_map", "lightmap", "light_map", "cube_map", "env_map",
        "reflection_map", "tint_map", "noise_map", "ramp_map",
    })

    found: dict = {}
    for layer in range(8):
        for suffix in suffixes:
            for template in (f"layer{layer}_{suffix}", suffix,
                             f"k_layer{layer}_{suffix}", f"k_{suffix}"):
                digest = f"{msc.symbol64(template):016x}"
                if digest in wanted:
                    found[digest] = template
    return found


def summarise(sidecar: dict) -> str:
    """A short human report -- what got built, and what is missing."""
    materials = sidecar.get("materials", [])
    diagnostics = sidecar.get("diagnostics", {})
    channel_counts: Counter = Counter()
    unrouted: Counter = Counter()

    for entry in materials:
        spec = entry.get("spec") or {}
        for name in (spec.get("channels") or {}):
            channel_counts[name] += 1
        for role in spec.get("unrouted_roles") or spec.get("unrouted") or ():
            unrouted[role] += 1

    lines = [
        f"materials      {len(materials)}",
        f"textures       {diagnostics.get('textures_written', 0)}",
        f"mesh offset    {diagnostics.get('mesh_material_offset')} "
        f"(coverage {diagnostics.get('mesh_material_probe', {}).get('coverage')})",
        "channels       " + (", ".join(
            f"{name}={count}" for name, count in channel_counts.most_common())
            or "none"),
        "role routes    " + (", ".join(
            f"{k}={v}" for k, v in
            sorted((diagnostics.get("role_routes") or {}).items()))
            or "none"),
    ]
    if unrouted:
        lines.append("unrouted roles " + ", ".join(
            f"{role}={count}" for role, count in unrouted.most_common(8)))
    warn_count = diagnostics.get("warning_count", 0)
    if warn_count:
        lines.append(f"warnings       {warn_count} (first few in diagnostics)")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="Echo VR material diagnostics.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="flat Echo VR extract root")
    ap.add_argument("--hash-lookup", type=Path, default=None,
                    help="hash_lookup.json of cracked CSymbol64 names")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap how many materials are scanned")
    ap.add_argument("--crack", action="store_true",
                    help="brute-force candidate names for unresolved hashes")
    args = ap.parse_args()

    report = audit_inputnames(args.root, args.hash_lookup, args.limit)

    if args.crack and report["unresolved"]:
        cracked = crack_inputnames(report["unresolved"])
        report["cracked_by_wordlist"] = cracked
        if cracked:
            still = [u for u in report["unresolved"]
                     if u["inputname"] not in cracked]
            report["unresolved"] = still

    print(json.dumps(report, indent=2))
