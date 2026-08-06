"""le_extract — offline Lone Echo archive -> .lemesh package extractor.

Stage 1 of the Blender tool. MUST run under Windows Python so `le_oodle` can load
the game Oodle DLL:

    python.exe blender_tool/extractor/le_extract.py --archive 0703fd2acd5803e9 --mesh 8f76d470b7ca990f
    python.exe blender_tool/extractor/le_extract.py --archive 0703fd2acd5803e9 --all
    python.exe blender_tool/extractor/le_extract.py --archive 0703fd2acd5803e9 --list

Reuses the PROVEN decode stack from this repository's `scripts/` (le_oodle,
le_archive_decode, le_meshlist_decode) to reach
decompressed primary/GPU subresource slices, then hands the bytes to the
pure-stdlib le_mesh core to decode every vertex attribute and write the package.

Milestones:
  M1 (this stage): full-attribute geometry + draws + flags + LOD metadata.   [DONE]
  M2: material role/texture bindings + SGMaterialData scalars + DDS texture
      extraction (incl. cross-archive homes).                                 [DONE]
      Roles are resolved LIVE FROM THE ARCHIVE by default, gated on the
      corpus-wide texture index (see `load_global_texture_index`). The old
      precomputed-TSV path is still available via --tsv-materials, but it
      covers only 1 of 102 mesh-bearing archives (51 of 1,240 mesh-lists, 4.1%).
      Flags: --textures (extract DDS), --tsv-materials (legacy TSV resolution).
"""

from __future__ import annotations

import argparse
import csv
import os
import struct
import sys
from pathlib import Path

# --- path wiring: this file is <repo>/blender_tool/extractor/… ---
THIS = Path(__file__).resolve()
EXTRACTOR = THIS.parent
BLENDER_TOOL = THIS.parents[1]
REPO_ROOT = THIS.parents[2]
SCRIPTS = REPO_ROOT / "scripts"
# EXTRACTOR is on sys.path implicitly when this file runs as __main__, but NOT
# when another module imports it (scripts/le_scene_materials.py does, to reuse
# `build_specs_for_pairs`). Add it explicitly so `import le_textures` resolves
# either way.
for p in (str(BLENDER_TOOL), str(SCRIPTS), str(EXTRACTOR)):
    if p not in sys.path:
        sys.path.insert(0, p)

# proven decode stack (Windows-python + Oodle)
from le_oodle import load_decompressed                       # noqa: E402
from le_archive_decode import (                    # noqa: E402
    ARCHIVE_GPU, ARCHIVE_PRIMARY, DEFAULT_HASH_LOOKUP,
    archive_offsets, entry_at, load_hash_lookup, parse_header, resource_entries,
)
from le_meshlist_decode import parse_candidate   # noqa: E402
# proven material / texture / binding decoders (reused, not reimplemented)
import le_scene_binding as sb                # noqa: E402
import le_shaderset_scan as sts            # noqa: E402
import le_material_slice as msp              # noqa: E402
from le_texture_extract import collect_resource_map   # noqa: E402

# pure-stdlib core + extractor helpers
from le_mesh import meshlist as ml                           # noqa: E402
from le_mesh import materials as mat                         # noqa: E402
from le_mesh import material_scalars as msc                  # noqa: E402
from le_mesh import package as pkg                           # noqa: E402
from le_mesh import lightmap as lmp                          # noqa: E402
from le_mesh import reflection_probe as rpm                  # noqa: E402
from le_mesh import dxbc                                     # noqa: E402
from le_mesh import role_index as ridx                       # noqa: E402
import le_textures                                           # noqa: E402
from le_streaming_texture import (         # noqa: E402
    parse_texture_primary,
)

# Optional precomputed scan inputs, all under `LONE_ECHO_SCAN_ROOT`. You generate
# these from your own game install; none of them ship with this repository.
SCAN_ROOT = Path(os.environ.get("LONE_ECHO_SCAN_ROOT",
                                str(REPO_ROOT / "scan_inputs")))
BINDING_TSV = SCAN_ROOT / "scene_binding_parse.tsv"
SCAN_TSV = SCAN_ROOT / "combined_shader_scan.tsv"
TEX_MANIFESTS = [SCAN_ROOT / "texture_manifest.tsv"]

# Corpus-wide texture home index: tex_hash -> the archive that owns the texture.
# Built by scripts/le_texture_archive_index.py over all 1,244 archives
# (7,274 textures across the 343 that contain any). ~180 KB, static, cached.
#
# This index is what makes cross-archive role resolution work at all. Lone Echo
# shadersets overwhelmingly bind textures homed in OTHER archives, so gating the
# SShaderInputData scan on archive-local texture hashes finds almost nothing:
# 88 of 115 bindings are external on the reference archive 0703fd2acd5803e9, and
# 31 of 31 on 4a405738bee7a74b (which resolved 0 roles for exactly this reason).
# `stream-confirmed`
TEX_INDEX_TSV = SCAN_ROOT / "texture_archive_index.tsv"

_GLOBAL_TEX_INDEX: dict[int, str] | None = None


def load_global_texture_index(path: Path = TEX_INDEX_TSV) -> dict[int, str]:
    """tex_hash(int) -> home archive hash. Cached; {} when the index is absent."""
    global _GLOBAL_TEX_INDEX
    if _GLOBAL_TEX_INDEX is None:
        idx: dict[int, str] = {}
        p = Path(path)
        if p.exists():
            with p.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh, delimiter="\t"):
                    try:
                        idx[int(row["tex_hash"], 16)] = row["archive_hash"]
                    except (KeyError, ValueError):
                        continue
        _GLOBAL_TEX_INDEX = idx
    return _GLOBAL_TEX_INDEX


# Corpus-wide material home index: material_hash -> the archive that owns it.
# Built by scripts/le_material_archive_index.py (1,418 materials over 1,244
# archives). Materials are only ~19% resident in the archive that BINDS them
# (127 bindings, 24 resident on 0703fd2acd5803e9) while shadersets are 100%
# resident -- so without this, every non-resident material silently falls back to
# SGMaterialData defaults and reads downstream as an ordinary opaque material.
# `stream-confirmed` / `export-validated`
MAT_INDEX_TSV = SCAN_ROOT / "material_archive_index.tsv"

_GLOBAL_MAT_INDEX: dict[str, str] | None = None


def load_global_material_index(path: Path = MAT_INDEX_TSV) -> dict[str, str]:
    """material_hash(16-hex) -> home archive hash. Cached; {} when absent."""
    global _GLOBAL_MAT_INDEX
    if _GLOBAL_MAT_INDEX is None:
        idx: dict[str, str] = {}
        p = Path(path)
        if p.exists():
            with p.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh, delimiter="\t"):
                    try:
                        idx[row["material_hash"].lower()] = row["archive_hash"]
                    except KeyError:
                        continue
        _GLOBAL_MAT_INDEX = idx
    return _GLOBAL_MAT_INDEX


# Corpus-wide role index: tex_hash -> the role(s) any SShaderInputData array
# anywhere in the corpus gives it. Built by scripts/le_role_index.py over
# the 149 archives that can hold a CGShaderSetResourceWin7 (25,694 binds, 2,194
# distinct textures).
#
# This is the corpus-scale version of `Archive._ensure_role_by_tex`: the role
# lives ONLY in the array, some shadersets ship no array at all, and the
# archive-local propagation that covers them scales with the archive's shaderset
# count (9/9 roles in a 259-shaderset archive, 4/15 in a 17-shaderset one).
# Policy for applying it — and the measured conflict rate that justifies it —
# lives in `le_mesh.role_index`, deliberately outside this Oodle-bound module so
# it is unit-testable. `stream-confirmed`
ROLE_INDEX_TSV = SCAN_ROOT / "role_index.tsv"

_GLOBAL_ROLE_INDEX: "ridx.RoleIndex | None" = None


def load_global_role_index(path: Path = ROLE_INDEX_TSV) -> "ridx.RoleIndex":
    """Corpus `tex_hash -> role` index. Cached; EMPTY (not an error) when absent."""
    global _GLOBAL_ROLE_INDEX
    if _GLOBAL_ROLE_INDEX is None:
        _GLOBAL_ROLE_INDEX = ridx.load_role_index(path)
    return _GLOBAL_ROLE_INDEX


def _primary_offsets(primary: bytes) -> tuple[int, int]:
    """(data_off, header_off) from a decompressed primary alone (no GPU needed).

    The primary's two header blocks enumerate both primary and GPU entries, so a
    foreign archive can be probed for material slices without decompressing its
    (much larger) GPU file. Same arithmetic as le_texture_archive_index.
    """
    primary_size = struct.unpack_from("<Q", primary, 0)[0]
    extra_skip = struct.unpack_from("<Q", primary, 0x18)[0]
    data_off = 0x20 + extra_skip
    return data_off, data_off + primary_size


_FOREIGN_SCALAR_CACHE: dict[str, dict] = {}


def foreign_material_scalars(wanted: dict[str, str], *, verbose: bool = False) -> dict:
    """{material_hash -> scalars} for materials living in OTHER archives.

    `wanted` maps material_hash -> home archive hash. Grouped by home and loaded
    ONE ARCHIVE AT A TIME, dropping each decompressed primary before opening the
    next, so peak memory stays at a single archive rather than the union.

    Results are cached process-wide: `--all` resolves materials per mesh-list and
    the same foreign material is bound by many of them, so without the cache a
    51-mesh-list run would re-decompress the same home archives 51 times.
    """
    by_home: dict[str, set[str]] = {}
    out: dict[str, dict] = {}
    for mh, home in wanted.items():
        hit = _FOREIGN_SCALAR_CACHE.get(mh)
        if hit is not None:
            out[mh] = hit
        else:
            by_home.setdefault(home, set()).add(mh)
    if not by_home:
        return out

    for home, hashes in sorted(by_home.items()):
        path = ARCHIVE_PRIMARY / home
        if not path.exists() or _compressed_stub(path):
            continue
        try:
            primary = load_decompressed(path)
            data_off, header_off = _primary_offsets(primary)
            mmap_ = collect_resource_map(primary, header_off, msp.MATERIAL_TYPE)
            for mh in hashes:
                ent = mmap_.get(int(mh, 16))
                if ent is None:
                    continue
                _idx, pos, size = ent
                sc = msc.decode_material_scalars(
                    primary[data_off + pos: data_off + pos + size])
                out[mh] = _FOREIGN_SCALAR_CACHE[mh] = sc
            if verbose:
                print(f"    foreign materials from {home}: "
                      f"{sum(1 for h in hashes if h in out)}/{len(hashes)}")
        except Exception as exc:   # noqa: BLE001
            print(f"    WARN foreign material archive {home}: {exc}")
        finally:
            primary = None   # noqa: F841  drop before opening the next archive
    return out


def _compressed_stub(path: Path) -> bool:
    return path.exists() and path.stat().st_size in (44, 57)


class Archive:
    """Loaded + decompressed archive with resolved mesh-list resource tables."""

    def __init__(self, archive_hash: str, names: dict):
        self.hash = archive_hash
        self.names = names
        primary_path = ARCHIVE_PRIMARY / archive_hash
        gpu_path = ARCHIVE_GPU / archive_hash
        if _compressed_stub(primary_path) or _compressed_stub(gpu_path):
            raise RuntimeError(f"archive {archive_hash} is a compressed stub")
        self.primary = load_decompressed(primary_path)
        self.gpu = load_decompressed(gpu_path)
        _, _, self.data_off, self.header_off = archive_offsets(self.primary, self.gpu)
        header0 = parse_header(self.primary, self.header_off)
        header1 = parse_header(self.primary, header0.end)
        self.primary_rows = resource_entries(self.primary, header0, names, "CGMeshListResourceWin7")
        self.gpu_rows = resource_entries(self.primary, header1, names, "CGMeshListResourceWin7GPU")
        # lazy caches for material resolution (built on first use)
        self._scene_map = None
        self._material_set = None
        self._shaderset_set = None
        self._material_map = None
        self._shaderset_map = None
        self._texture_hashes = None
        self._pbr = None
        self._role_by_tex = None
        #: tex_hash -> exact texture name recovered from a DXBC RDEF chunk.
        #: Accumulated as shadersets are resolved; harvestable into hash_lookup.
        self.rdef_names: dict[str, str] = {}
        #: shaderset_hash -> {role_key -> le_mesh.role_index.SOURCE_*}. Provenance
        #: for every binding this archive resolved, so "the array said so" and
        #: "the corpus voted" never look the same downstream.
        self.role_sources: dict[str, dict[str, str]] = {}
        #: shaderset_hash -> {tex_hash -> {role: votes}} for every bind the corpus
        #: index DISAGREED about — both the ones applied (layer ambiguity) and the
        #: ones refused (suffix conflict). Recorded, never silently resolved.
        self.role_ambiguity: dict[str, dict[str, dict[str, int]]] = {}

    def meshlist_hashes(self) -> list[str]:
        return sorted(f"{h:016x}" for h in self.primary_rows if h in self.gpu_rows)

    # --- material resolution directly from THIS archive's loaded bytes -------

    def _ensure_maps(self) -> None:
        if self._scene_map is not None:
            return
        self._scene_map = sb.collect_type_map(self.primary, self.header_off, sb.SCENE_TYPE)
        self._material_set = sb.collect_type_set(self.primary, self.header_off, sb.MATERIAL_TYPE)
        self._shaderset_set = sb.collect_type_set(self.primary, self.header_off, sb.SHADERSET_TYPE)
        self._material_map = collect_resource_map(self.primary, self.header_off, msp.MATERIAL_TYPE)

    def binding(self, meshlist_hash: str) -> tuple[list[str], list[str]]:
        """(material_hashes[], shaderset_hashes[]) index-ordered for this meshlist.

        Parsed live from the companion CGSceneResourceWin7 (works for any archive);
        materialidx / shadersetidx select directly into these lists. ([],[]) if the
        scene binding is absent or unparseable.
        """
        self._ensure_maps()
        ent = self._scene_map.get(int(meshlist_hash, 16))
        if ent is None:
            return [], []
        pos, size = ent
        ok, _note, res = sb.parse_binding_table(
            self.primary, self.data_off + pos, size, self._material_set, self._shaderset_set)
        if not ok:
            return [], []
        return ([f"{h:016x}" for h in res["mat_hashes"]],
                [f"{h:016x}" for h in res["shd_hashes"]])

    def material_scalars_for(self, material_hash: str) -> dict:
        """decode_material_scalars for a local material; {} if not in this archive."""
        if not material_hash:
            return {}
        self._ensure_maps()
        ent = self._material_map.get(int(material_hash, 16))
        if ent is None:
            return {}
        _idx, pos, size = ent
        return msc.decode_material_scalars(self.primary[self.data_off + pos: self.data_off + pos + size])

    def _ensure_shader_scan(self) -> None:
        if self._shaderset_map is not None:
            return
        smap: dict[int, tuple[int, int]] = {}
        header = None
        for hi in range(2):
            header = parse_header(self.primary, self.header_off if hi == 0 else header.end)
            for i in range(header.contents.count):
                th, nh, val = struct.unpack_from("<QQQ", self.primary, header.contents.off + i * 24)
                if th == sts.SHADERSET_TYPE and val < header.entries.count:
                    smap[nh] = entry_at(self.primary, header, val)
        self._shaderset_map = smap
        # Archive-local textures alone are NOT a sufficient needle set: shadersets
        # overwhelmingly bind textures homed in other archives (88/115 external on
        # 0703fd2acd5803e9; 31/31 on 4a405738bee7a74b). Union with the corpus-wide
        # index so cross-archive binds are seen at all. Keeping the union rather
        # than replacing preserves any local texture missing from the index.
        # ⛔ Dropping the gate entirely was measured and rejected: 1,884 bindings,
        # 89% false positives — struct validation alone is not selective enough.
        self._texture_hashes = sts.collect_type_hashes(
            self.primary, self.data_off, self.header_off, sts.TEXTURE_TYPE)
        self._texture_hashes |= set(load_global_texture_index())
        self._pbr = sts.build_pbr_hash_table(self.names)

    #: inputname CSymbol64 -> role key, for anchoring an SShaderInputData row on
    #: its NAME rather than on its texture. Needle-free: unlike the texture gate
    #: this sees a row whose texture is absent from the corpus index.
    _ROLE_ANCHOR = {int(k, 16): v[0] for k, v in mat.ROLE_BY_INPUTNAME.items()}

    def _shaderset_slice(self, shaderset_hash: str) -> bytes | None:
        self._ensure_shader_scan()
        ent = self._shaderset_map.get(int(shaderset_hash, 16))
        if ent is None:
            return None
        pos, size = ent
        return self.primary[self.data_off + pos: self.data_off + pos + size]

    def _ensure_role_by_tex(self) -> None:
        """tex_hash -> role, learned from every shaderset in THIS archive.

        The role only ever exists in the `SShaderInputData` array, and some
        shadersets ship no array (see `le_mesh.dxbc`). A texture's role is stable
        wherever it IS declared — measured on `2fd6839161785e9c`, 5 textures bound
        by two shadersets each, 5/5 identical roles — so an array-less shaderset
        can borrow the role from a sibling that binds the same texture.

        Conflicts are recorded and the FIRST role wins, so a disagreement can
        never silently flip an already-correct binding.
        """
        if self._role_by_tex is not None:
            return
        self._ensure_shader_scan()
        table: dict[str, str] = {}
        self.role_conflicts: dict[str, set[str]] = {}
        for _nh, (pos, size) in self._shaderset_map.items():
            start = self.data_off + pos
            for off in range(start, start + size - 0x20 + 1, 8):
                role = self._ROLE_ANCHOR.get(
                    struct.unpack_from("<Q", self.primary, off)[0])
                if role is None:
                    continue
                tex = struct.unpack_from("<Q", self.primary, off + 0x08)[0]
                if tex in (0, 0xFFFFFFFFFFFFFFFF):
                    continue          # engine-supplied input, never a material bind
                th = f"{tex:016x}"
                if th not in table:
                    table[th] = role
                elif table[th] != role:
                    self.role_conflicts.setdefault(th, {table[th]}).add(role)
        self._role_by_tex = table

    def shaderset_roles_direct(self, shaderset_hash: str) -> dict[str, str]:
        """{role_key -> tex_hash} scanned live from this archive's shaderset slices.

        FOUR sources, in strict precedence — a later one may only fill a gap the
        earlier ones left, never overwrite what they established:

        1. the shaderset's own `SShaderInputData` array — authoritative, because
           it is the only place the ROLE is recorded. Cross-archive: the needle
           set is this archive's textures UNIONED with the corpus-wide texture
           index, so a shaderset binding a texture homed elsewhere still
           resolves. Measured on 0703fd2acd5803e9: 60 bindings with local needles
           vs **212** with global ones, exactly reproducing the precomputed
           scan TSV. `stream-confirmed`

        2. archive-local propagation (`_ensure_role_by_tex`) — a texture's role
           borrowed from a sibling shaderset in THIS archive that declares it.

        3. the CORPUS role index (`le_mesh.role_index`, 149 archives) — the same
           propagation at corpus scale, for the textures no sibling here declares.
           ★ Applied only when the corpus agrees on the role SUFFIX; a
           suffix-conflicted texture (16 corpus-wide, all reusable greyscale
           utility maps) is REFUSED and falls through to (4). Disagreements —
           applied or refused — are recorded in `self.role_ambiguity`.

        The binds themselves come from the shaderset's own DXBC **RDEF** chunk
        (`le_mesh.dxbc`) — every material texture it binds, by exact name, needing
        no needle set at all. That is the only bind source for the shadersets that
        ship NO array: 4 of 17 on `2fd6839161785e9c`, carrying Liv's two largest
        meshes.

        4. the TEXTURE FORMAT (`mat.composite_roles_from_format`) — for a
           `generated_composite_*` atlas that NO array declares anywhere, the
           DXGI format partitions the composite role set with no measured
           exception: BC5 ⇒ `composite_normals` (52/52), non-sRGB ⇒
           `composite_components` (52/52), and inside one resolution group the
           unique BC1_UNORM_SRGB / BC3_UNORM_SRGB are diffuse / specular.
           Held out against the shipped arrays (461 shadersets, hide the array,
           run the rule): **830 fired, 494 refused, 0 wrong — 0.000 %.**
           ⛔ It emits a suffix only; the layer is the lowest this shaderset has
           not already claimed, which is sound exactly when one unresolved
           resolution group remains — and more than one is refused.

        5. `rdef_bind{n}` — RDEF knew the texture, nothing knew the role. A key no
           Principled channel consumes, so it lands in the spec's `unrouted_roles`
           rather than being guessed into a socket. ⛔ Never invent a role.

        Provenance for every binding is recorded in `self.role_sources`.
        """
        if not shaderset_hash:
            return {}
        slab = self._shaderset_slice(shaderset_hash)
        if slab is None:
            return {}
        pos, size = self._shaderset_map[int(shaderset_hash, 16)]
        rows = sts.scan_shaderset_slice(
            self.primary, self.data_off + pos, size, self.hash, shaderset_hash,
            self._texture_hashes, self.names, self._pbr)
        roles = mat.roles_from_input_rows(rows, self.names)
        sources = {r: ridx.SOURCE_ARRAY for r in roles}
        self.role_sources[shaderset_hash] = sources

        binds = dxbc.material_texture_binds(slab)
        if not binds:
            return roles
        self._ensure_role_by_tex()
        corpus = load_global_role_index()
        ambiguity = self.role_ambiguity.setdefault(shaderset_hash, {})
        already = set(roles.values())
        pending: dict[int, str] = {}       # composite binds nothing could name
        for bind, name in sorted(binds.items()):
            th = f"{msc.symbol64(name):016x}"
            self.rdef_names.setdefault(th, name)
            if th in already:
                continue                      # the array already named its role
            role = self._role_by_tex.get(th)
            source = ridx.SOURCE_ARCHIVE
            if role is None:
                role, status = corpus.resolve(th)
                source = ridx.SOURCE_CORPUS
                if status != ridx.STATUS_ABSENT and status != ridx.STATUS_UNANIMOUS:
                    ambiguity[th] = corpus.roles_for(th)
            # A corpus role that COLLIDES with one this shaderset already carries
            # is not applied either: two textures cannot both be the layer-1
            # diffuse, and the array/archive answer wins by precedence.
            if role is None or role in roles:
                if name.startswith(mat.COMPOSITE_NAME_PREFIX):
                    pending[bind] = th        # -> step 4, the format rule
                    already.add(th)
                    continue
                role = f"rdef_bind{bind}"
                source = ridx.SOURCE_RDEF
            roles[role] = th
            sources[role] = source
            already.add(th)

        if pending:
            meta = texture_meta_for(self, sorted(set(pending.values())))
            claimed = {mat.split_role(r)[0] for r in roles
                       if mat.split_role(r)[1].startswith("composite_")}
            out = mat.composite_roles_from_format(
                pending, meta, claimed_layers=claimed, taken_roles=roles.keys())
            for bind, role in sorted(out["roles"].items()):
                roles[role] = pending[bind]
                sources[role] = ridx.SOURCE_FORMAT
            for bind in sorted(out["refused"]):
                roles[f"rdef_bind{bind}"] = pending[bind]
                sources[f"rdef_bind{bind}"] = ridx.SOURCE_RDEF
        if not ambiguity:
            self.role_ambiguity.pop(shaderset_hash, None)
        return roles

    def build(self, meshlist_hash: str) -> list[ml.MeshObject]:
        mi = int(meshlist_hash, 16)
        if mi not in self.primary_rows or mi not in self.gpu_rows:
            raise KeyError(f"{meshlist_hash} not a paired CGMeshListResource in {self.hash}")
        _pe, pos, _size = self.primary_rows[mi]
        _ge, gpu_pos, gpu_size = self.gpu_rows[mi]
        start = self.data_off + pos
        # Empty mesh-list resource: all eight loader-order table counts are 0
        # (48-byte all-zero primary, gpudatasize 0, paired to a 16-byte GPU stub).
        # These are legitimately empty (the the reference baseline excludes them too;
        # 106 exist corpus-wide) — return no objects rather than a false FAIL.
        if not any(struct.unpack_from("<8I", self.primary, start)):
            return []
        parsed = parse_candidate(self.primary, start, gpu_size)
        if parsed is None:
            raise RuntimeError(f"parse_candidate failed for {meshlist_hash}")

        def tbl(name):
            t = parsed[name]
            return ml.Table(t.count, t.data_off)

        objects = ml.build_objects(
            self.primary, self.gpu, gpu_pos,
            meshes=tbl("meshes"), renderparams=tbl("renderparams"),
            vertexbuffers=tbl("vertexbuffers"), indexbuffers=tbl("indexbuffers"),
            # populated in only 11 of the corpus's 1,240 mesh-lists, but where it
            # IS populated the coarser LODs are extra draws over later slices of
            # the same index buffer — import them all and the levels overlap.
            lodchildindices=tbl("lodchildindices"),
        )
        # ADDITIVE (R2): `CGMeshData.probeidx@0x50` — declared in
        # `le_mesh.meshlist` as `M_PROBEIDX` and read by nothing there. Stamped
        # onto each object here so the package carries it without that module
        # changing. `le_mesh.package` emits it as `probe_index`.
        mtab = parsed["meshes"]
        probe_idx = rpm.read_mesh_probe_indices(self.primary, mtab.data_off, mtab.count)
        for o in objects:
            if 0 <= o.mesh_index < len(probe_idx):
                o.probe_index = probe_idx[o.mesh_index]
        return objects


def material_key(si: int, shd_hash: str, mat_hash: str) -> str:
    """The unique key for one (shaderset, material) binding.

    THE single definition of the `.lemesh` material key. The level/scatter path
    (`scripts/le_scene_materials.py`) imports this rather than re-deriving it so
    a level sidecar's `spec["key"]` is formatted identically to a `.lemesh` one.
    """
    if shd_hash and mat_hash:
        return f"{shd_hash}__{mat_hash}"
    if shd_hash:
        return shd_hash
    if mat_hash:
        return mat_hash
    return f"shd{si}"


_material_key = material_key       # legacy private alias


def build_specs_for_pairs(pairs: dict, *, role_by_key: dict, dxgi_by_tex: dict,
                          scalars_by_hash: dict, texture_files: dict,
                          role_sources_by_key: dict | None = None,
                          role_ambiguity_by_key: dict | None = None,
                          texture_names: dict | None = None) -> list[dict]:
    """[material spec] for `pairs` = {key -> (shaderset_hash, material_hash)}.

    The tail of `_resolve_materials`, factored out so BOTH the single-mesh
    `.lemesh` path and the level `.lescatter` sidecar path go through one call
    into `le_mesh.materials.build_material_spec`. Any future field added to the
    spec therefore reaches the level path for free — the divergence this exists
    to prevent is exactly what left `<master>_materials.json` v1 at 11 flat
    fields while `.lemesh` grew to 35.

    `texture_files` maps tex_hash -> a path relative to whatever directory the
    consumer will resolve against (`textures/<hash>.dds` inside a `.lemesh`;
    `<master>_textures/<hash>.dds` beside a level sidecar).
    """
    specs: dict[str, dict] = {}
    for key, (shd_hash, mat_hash) in pairs.items():
        specs[key] = mat.build_material_spec(
            key, shaderset_hash=shd_hash, material_hash=mat_hash,
            role_textures=role_by_key.get(key, {}), dxgi_by_tex=dxgi_by_tex,
            scalars=scalars_by_hash.get(mat_hash, {}), texture_files=texture_files,
            role_sources=(role_sources_by_key or {}).get(key),
            role_ambiguity=(role_ambiguity_by_key or {}).get(key),
            texture_names=texture_names)
    return list(specs.values())


def propagate_lod_sibling_roles(pairs: dict, role_by_key: dict,
                                role_sources_by_key: dict) -> dict:
    """Fill `rdef_bind{n}` binds from a SAME-MATERIAL shaderset that named them.

    `pairs` is `{key -> (shaderset_hash, material_hash)}`; `role_by_key` and
    `role_sources_by_key` are mutated in place. Returns `{key -> {role: tex}}`
    for what each key gained, so the caller can log and re-collect textures.

    The rule, and every guard on it, is documented on
    `le_mesh.role_index.SOURCE_LOD_SIBLING`. In short: same `material_hash`,
    same texture hash, donor role came from the donor's OWN array, and the role
    is not already carried by the recipient. A texture bound under two different
    array-declared roles by two siblings is a CONTRADICTION and is refused
    outright rather than resolved by a tie-break.
    """
    # donor table: material_hash -> {tex_hash -> role}, array-sourced only
    donors: dict[str, dict[str, str]] = {}
    conflicted: dict[str, set] = {}
    for key, (_shd, mat_hash) in pairs.items():
        if not mat_hash:
            continue
        src = role_sources_by_key.get(key) or {}
        table = donors.setdefault(mat_hash, {})
        for role, tex in (role_by_key.get(key) or {}).items():
            # `unknown_s{slot}` is a SLOT, not a role -- it carries no channel
            # information and the two shadersets need not share a register
            # layout, so it is never a donor.
            if (src.get(role) != ridx.SOURCE_ARRAY
                    or role.startswith("rdef_bind") or role.startswith("unknown_s")):
                continue
            if table.get(tex, role) != role:
                conflicted.setdefault(mat_hash, set()).add(tex)
            table[tex] = role
    for mat_hash, bad in conflicted.items():
        for tex in bad:
            donors.get(mat_hash, {}).pop(tex, None)

    gained: dict[str, dict] = {}
    for key, (_shd, mat_hash) in pairs.items():
        table = donors.get(mat_hash) or {}
        if not table:
            continue
        roles = role_by_key.get(key) or {}
        srcs = role_sources_by_key.setdefault(key, {})
        for role in [r for r in roles if r.startswith("rdef_bind")]:
            tex = roles[role]
            want = table.get(tex)
            if not want or want in roles:
                continue
            roles.pop(role, None)
            srcs.pop(role, None)
            roles[want] = tex
            srcs[want] = ridx.SOURCE_LOD_SIBLING
            gained.setdefault(key, {})[want] = tex
    return gained


def _resolve_materials(arc: "Archive", meshlist_hash: str, objects: list[ml.MeshObject],
                       *, out_dir: Path, shd_tex: dict, dxgi: dict, tex_home: dict,
                       names: dict, direct: bool = False, want_textures: bool = False,
                       verbose: bool = False) -> list[dict]:
    """Resolve per-draw materials into unique specs.

    Binds each draw's shadersetidx/materialidx -> shaderset+material hash, resolves
    texture roles (from the archive live when `direct`, else the scan TSV), decodes
    SGMaterialData scalars, optionally extracts the referenced DDS into
    `<out_dir>/textures/`, and stamps each draw's material_key.
    """
    mats, shds = arc.binding(meshlist_hash)

    # --- per-draw hash assignment ---
    pairs: dict[str, tuple[str, str]] = {}
    for obj in objects:
        for draw in obj.draws:
            si, midx = draw.shaderset_index, draw.material_index
            shd_hash = shds[si] if 0 <= si < len(shds) else ""
            mat_hash = mats[midx] if 0 <= midx < len(mats) else ""
            key = material_key(si, shd_hash, mat_hash)
            draw.material_key = key
            pairs.setdefault(key, (shd_hash, mat_hash))

    # --- role textures per unique material key ---
    role_by_key: dict[str, dict[str, str]] = {}
    role_sources_by_key: dict[str, dict[str, str]] = {}
    role_ambiguity_by_key: dict[str, dict[str, dict[str, int]]] = {}
    dxgi_local = dict(dxgi)                 # merge real extracted formats into a copy
    needed_tex: dict[str, str] = {}        # tex_hash -> home archive
    for key, (shd_hash, mat_hash) in pairs.items():
        roles = arc.shaderset_roles_direct(shd_hash) if direct else dict(shd_tex.get(shd_hash, {}))
        role_by_key[key] = roles
        if direct:
            # Provenance travels with the bindings: a corpus-voted role and an
            # array-declared one must never be indistinguishable downstream.
            if arc.role_sources.get(shd_hash):
                role_sources_by_key[key] = dict(arc.role_sources[shd_hash])
            if arc.role_ambiguity.get(shd_hash):
                role_ambiguity_by_key[key] = {
                    t: dict(v) for t, v in arc.role_ambiguity[shd_hash].items()}
        for tex_hash in roles.values():
            # Never assume the texture is local. `le_textures.extract_by_hashes`
            # silently skips a hash that is not in the archive it is handed, so
            # assuming `arc.hash` in direct mode made DDS extraction no-op for
            # 88/115 of the reference archive's textures and 31/31 of
            # 4a405738bee7a74b's. `stream-confirmed`
            home = tex_home.get(tex_hash, arc.hash)
            needed_tex.setdefault(tex_hash, home)

    # --- LOD-SIBLING role propagation --------------------------------------
    # See `le_mesh.role_index.SOURCE_LOD_SIBLING`. Two shadersets that share a
    # `material_hash` are the SAME authored material at two detail levels; a role
    # one of them declares in its own array applies to the same texture hash
    # bound by the other. Runs before texture extraction so a newly-named
    # texture is still pulled.
    propagated = propagate_lod_sibling_roles(pairs, role_by_key, role_sources_by_key)
    if propagated and verbose:
        for key, gained in sorted(propagated.items()):
            print(f"      lod-sibling roles -> {key}: {sorted(gained)}")
    for key in propagated:
        for tex_hash in role_by_key[key].values():
            needed_tex.setdefault(tex_hash, tex_home.get(tex_hash, arc.hash))

    # --- texture extraction (grouped by home archive) ---
    texture_files: dict[str, str] = {}
    if want_textures and needed_tex:
        textures_dir = Path(out_dir) / "textures"
        by_home: dict[str, set[str]] = {}
        for tex_hash, home in needed_tex.items():
            by_home.setdefault(home, set()).add(tex_hash)
        for home, hashes in sorted(by_home.items()):
            meta = le_textures.extract_by_hashes(home, hashes, textures_dir, verbose=verbose)
            for tex_hash, m in meta.items():
                texture_files[tex_hash] = f"textures/{tex_hash}.dds"
                if m.get("dxgi_format"):
                    dxgi_local[tex_hash] = int(m["dxgi_format"])

    # --- material scalars, local first then cross-archive -------------------
    # Only ~19% of bound materials are resident in the binding archive. A miss
    # here is SILENT: the spec falls back to SGMaterialData defaults, so an
    # eMTForwardTransparent material reads as mattype 0 / eBlendOpaque and
    # renders opaque. Resolve the rest from their home archive.
    scalars_by_hash: dict[str, dict] = {}
    unresolved: dict[str, str] = {}
    mat_index = load_global_material_index()
    for _key, (_shd, mat_hash) in pairs.items():
        if not mat_hash or mat_hash in scalars_by_hash or mat_hash in unresolved:
            continue
        local = arc.material_scalars_for(mat_hash)
        if local:
            scalars_by_hash[mat_hash] = local
        else:
            home = mat_index.get(mat_hash.lower())
            if home and home != arc.hash:
                unresolved[mat_hash] = home
    if unresolved:
        found = foreign_material_scalars(unresolved, verbose=verbose)
        scalars_by_hash.update(found)
        if verbose or len(found) < len(unresolved):
            print(f"    material scalars: {len(scalars_by_hash)} resolved "
                  f"({len(found)}/{len(unresolved)} cross-archive)")

    # --- build the specs (shared with the level/scatter sidecar path) ---
    return build_specs_for_pairs(
        pairs, role_by_key=role_by_key, dxgi_by_tex=dxgi_local,
        scalars_by_hash=scalars_by_hash, texture_files=texture_files,
        role_sources_by_key=role_sources_by_key,
        role_ambiguity_by_key=role_ambiguity_by_key,
        # Accumulated by `Archive.shaderset_roles_direct` as each shaderset's RDEF
        # chunk is read: `symbol64(name) == tex_hash` for every bind. Carrying it
        # into the manifest is what makes an UNROUTED bind legible -- `rdef_bind1`
        # alone cannot say whether the cook generated the atlas or an artist named
        # it, and those two states need different follow-up.
        texture_names=dict(arc.rdef_names))


# ---------------------------------------------------------------------------
# LEVEL lightmap -> the `.lemesh` manifest `lightmap` section
# (a local working file §2: ergonomics, so the atlas resolves without
#  the operator naming one of the 68 MB DDS files by hand.)
# ---------------------------------------------------------------------------

#: package-relative directory the atlas is copied into when --lightmap-textures
#: is passed.  Chosen because the addon already searches it
#: (`lightmap_builder.PKG_SEARCH_DIRS`), so the copy resolves two ways.
LIGHTMAP_DIR = "lightmap"

_LM_TABLE_CACHE: dict = {}
_TEX_META_CACHE: dict = {}


def lightmap_table_for(arc: "Archive", name_hash: int):
    """The `CGLightMapResourceWin7` table a scene binds to, or None.

    SIBLING-BY-NAME (`stream-confirmed`, `le_mesh.lightmap`): a scene's
    mesh-list / scene / static-instance / lightmap resources all carry the same
    resource name hash, because `CGScene` owns them as sibling
    `CResourceInstanceT<>`s and `CGSceneData` stores no id for any of them. So a
    mesh-list's own hash IS its lightmap resource's name.  Returns None when no
    such resource exists in this archive — which is the common case and not an
    error (42 of the bridge's 51 mesh-lists have an all-null table).
    """
    key = (arc.hash, name_hash)
    if key in _LM_TABLE_CACHE:
        return _LM_TABLE_CACHE[key]
    table = None
    try:
        ent = collect_resource_map(
            arc.primary, arc.header_off, lmp.LIGHTMAP_TYPE_WIN7).get(name_hash)
        if ent is not None:
            _idx, pos, size = ent
            table = lmp.parse_lightmap_table(
                arc.primary[arc.data_off + pos: arc.data_off + pos + size])
    except Exception as exc:      # noqa: BLE001
        print(f"    WARN lightmap table {name_hash:016x}: {exc}")
    _LM_TABLE_CACHE[key] = table
    return table


def _texture_meta_from_primary(primary: bytes, data_off: int, header_off: int,
                               hashes) -> dict:
    """{tex_hash -> {dxgi,width,height,arraysize}} from `CGTextureResourceData`.

    Read from the PRIMARY slice only — no GPU bytes, no DDS extraction. That is
    what makes `arraysize` (the level's page count, and the only thing that makes
    `slices_per_page` derived rather than assumed) affordable when the 68 MB
    atlas is NOT being copied into the package.
    """
    out: dict = {}
    tmap = collect_resource_map(primary, header_off, lmp.TEXTURE_TYPE_WIN7)
    for h in hashes:
        ent = tmap.get(int(h, 16))
        if ent is None:
            continue
        _idx, pos, size = ent
        meta = parse_texture_primary(primary[data_off + pos: data_off + pos + size])
        if not meta:
            continue
        out[h] = {"dxgi": int(meta["dxgi_format"]), "width": int(meta["maxwidth"]),
                  "height": int(meta["maxheight"]),
                  "arraysize": int(meta["arraysize"]) or 1}
    return out


def texture_meta_for(arc: "Archive", hashes, *, verbose: bool = False) -> dict:
    """`{tex_hash -> {dxgi,width,height,arraysize}}`, local archive first.

    Cached process-wide and grouped by home archive, one primary at a time —
    same OOM discipline as `foreign_material_scalars`. PRIMARY slices only, so
    it is affordable for every texture a shaderset binds, not just the atlas.

    Two callers: the lightmap section (which needs `arraysize`) and
    `Archive.shaderset_roles_direct`'s composite-role recovery (which needs
    `dxgi` + `width` + `height`).
    """
    out, want = {}, []
    for h in hashes:
        if h in _TEX_META_CACHE:
            if _TEX_META_CACHE[h]:
                out[h] = _TEX_META_CACHE[h]
        else:
            want.append(h)
    if not want:
        return out

    local = _texture_meta_from_primary(arc.primary, arc.data_off, arc.header_off, want)
    out.update(local)
    _TEX_META_CACHE.update(local)

    missing = [h for h in want if h not in local]
    if missing:
        index = load_global_texture_index()
        by_home: dict[str, set] = {}
        for h in missing:
            home = index.get(int(h, 16))
            if home and home != arc.hash:
                by_home.setdefault(home, set()).add(h)
            else:
                _TEX_META_CACHE[h] = {}
        for home, hs in sorted(by_home.items()):
            path = ARCHIVE_PRIMARY / home
            if not path.exists() or _compressed_stub(path):
                for h in hs:
                    _TEX_META_CACHE[h] = {}
                continue
            primary = None
            try:
                primary = load_decompressed(path)
                d_off, h_off = _primary_offsets(primary)
                got = _texture_meta_from_primary(primary, d_off, h_off, hs)
                out.update(got)
                _TEX_META_CACHE.update(got)
                for h in hs:
                    _TEX_META_CACHE.setdefault(h, {})
                if verbose:
                    print(f"    lightmap texture meta from {home}: {len(got)}/{len(hs)}")
            except Exception as exc:      # noqa: BLE001
                print(f"    WARN lightmap texture archive {home}: {exc}")
            finally:
                primary = None            # noqa: F841
    return out


def resolve_lightmap_section(arc: "Archive", meshlist_hash: str, objects, *,
                             out_dir: Path, want_textures: bool = False,
                             verbose: bool = False) -> dict:
    """The manifest `lightmap` section for one mesh-list, or `{}`.

    `{}` — the key is then simply absent — whenever the scene has no lightmapped
    object, no co-named `CGLightMapResourceWin7`, or a null row. Nothing here is
    guessed: an absent section means the extractor could not see the binding, and
    the addon's existing option/scan fallbacks take over.
    """
    rows = sorted({int(o.lightmap_index) for o in objects
                   if lmp.is_lightmapped(o.lightmap_index)})
    if not rows:
        return {}
    name_hash = lmp.lightmap_resource_name_for_scene(int(meshlist_hash, 16))
    table = lightmap_table_for(arc, name_hash)
    if not table:
        return {}

    binding = next((b for b in (lmp.resolve(table, r) for r in rows) if b), None)
    if binding is None:
        return {}

    hashes = list(binding.texture_set.textures.values())
    meta = texture_meta_for(arc, hashes, verbose=verbose)

    texture_files: dict = {}
    if want_textures and hashes:
        index = load_global_texture_index()
        by_home: dict[str, set] = {}
        for h in hashes:
            by_home.setdefault(index.get(int(h, 16), arc.hash), set()).add(h)
        dest = Path(out_dir) / LIGHTMAP_DIR
        for home, hs in sorted(by_home.items()):
            got = le_textures.extract_by_hashes(home, hs, dest, verbose=verbose)
            for th in got:
                texture_files[th] = f"{LIGHTMAP_DIR}/{th}.dds"

    section = lmp.manifest_lightmap_section(
        binding, texture_files, texture_meta=meta, resource_name=name_hash)
    if len(rows) > 1:
        section["rows_referenced"] = rows
    section["textures_copied"] = bool(texture_files)
    return section



# =============================================================================
# reflection probes (R2) — the ambient SPECULAR sibling of the lightmap block
# =============================================================================
#: package sub-directory the probe cube DDS files land in
PROBE_DIR = rpm.PROBE_DIR


def _probe_rows(arc: "Archive"):
    """Every `CGReflectionProbeResourceWin7` in the archive, decoded.

    `{name_hash: (resource, gpu_pos, gpu_size)}`.
    """
    h0 = parse_header(arc.primary, arc.header_off)
    h1 = parse_header(arc.primary, h0.end)
    prim = resource_entries(arc.primary, h0, arc.names, "CGReflectionProbeResourceWin7")
    gpu = resource_entries(arc.primary, h1, arc.names, "CGReflectionProbeResourceWin7GPU")
    out = {}
    for nh, (_e, pos, size) in prim.items():
        start = arc.data_off + pos
        try:
            res = rpm.parse_probe_resource(arc.primary[start:start + size])
        except ValueError as exc:
            print(f"    WARN reflection probe {nh:016x}: {exc}")
            continue
        g = gpu.get(nh)
        out[nh] = (res, g[1] if g else None, g[2] if g else None)
    return out


def probe_resource_for_scene(arc: "Archive", scene_name_hash: int):
    """`(resource, gpu_pos, gpu_size, rule)` for a scene's probes, or None.

    Two rules, in order, and the one that fired is reported — never merged:

    1. **sibling-by-name**, exactly like the lightmap: `CGScene` owns a
       `CResourceInstanceT<CGReflectionProbeResource>` and `CGSceneData` stores
       no id for it, so the probe resource carries the scene's OWN name hash
       (docs/SCENES.md §1a; `CGameLevelResourceWin7 ==
       CGReflectionProbeResourceWin7` in 90/90 level archives).
    2. **archive-unique** — used when rule 1 finds nothing or finds the 344-byte
       EMPTY resource.  `measured` over the whole corpus: an archive holds **at
       most one populated** probe resource (94 resources / 90 archives = 34
       empty + 60 populated, and in each of the 4 archives that carry two, the
       archive-named one is the empty one).  A sub-scene mesh-list therefore has
       exactly one candidate and no ambiguity to resolve.  ⚠ `inferred` that the
       sub-scene really shares the parent's probe set — it is what the per-mesh
       `probeidx` ranges are consistent with, not something read from the engine.
       If an archive ever ships two populated sets this returns None rather than
       pick one.
    """
    rows = _probe_rows(arc)
    hit = rows.get(scene_name_hash)
    if hit is not None and hit[0].n_probes:
        return hit[0], hit[1], hit[2], "sibling-by-name", scene_name_hash
    populated = [(nh, v) for nh, v in rows.items() if v[0].n_probes]
    if len(populated) == 1:
        nh, (res, gp, gs) = populated[0]
        return res, gp, gs, "archive-unique", nh
    if len(populated) > 1:
        print(f"    WARN {arc.hash}: {len(populated)} populated probe resources — "
              f"refusing to guess which one this scene binds")
        return None
    if hit is not None:
        return hit[0], hit[1], hit[2], "empty", scene_name_hash
    return None


def resolve_probe_section(arc: "Archive", meshlist_hash: str, objects, *,
                          out_dir: Path, want_textures: bool = False,
                          verbose: bool = False) -> dict:
    """The manifest `reflection_probes` section for one mesh-list, or `{}`.

    `{}` — the key is then simply absent — when the archive ships no co-named
    probe resource, or ships the 344-byte EMPTY one (34 of the corpus's 94).
    Nothing is guessed: an absent section means the extractor could not see a
    probe set, and the addon wires no ambient specular at all.
    """
    name_hash = int(meshlist_hash, 16)
    found = probe_resource_for_scene(arc, name_hash)
    if found is None:
        return {}
    res, gpu_pos, gpu_size, rule, resolved_name = found
    if res.n_probes == 0:
        return {}

    files: dict = {}
    if want_textures and gpu_pos is not None:
        dest = Path(out_dir) / PROBE_DIR
        dest.mkdir(parents=True, exist_ok=True)
        for i in range(res.n_probes):
            lo, hi = res.probe_gpu_range(i)
            payload = arc.gpu[gpu_pos + lo:gpu_pos + hi]
            dim, mips = res.cube_dim(i), res.mipcount(i)
            if dim is None or not mips:
                print(f"    WARN probe {i}: {hi - lo} B is not a whole "
                      f"{res.format_name} cube — not written")
                continue
            try:
                blob = rpm.cube_dds_bytes(payload, dim, mips,
                                          engine_format=res.texture_format)
            except ValueError as exc:
                print(f"    WARN probe {i}: {exc}")
                continue
            rel = f"{PROBE_DIR}/{rpm.probe_file_name(i)}"
            (Path(out_dir) / rel).write_bytes(blob)
            files[i] = {"cube": rel}
        if verbose:
            print(f"    probes: wrote {len(files)} cube DDS -> {PROBE_DIR}/")

    section = rpm.manifest_probe_section(
        res, files, resource_name=resolved_name, gpu_present=gpu_pos is not None)
    section["gpu_size"] = gpu_size
    #: which of `probe_resource_for_scene`'s two rules produced this set.
    #: "archive-unique" is the weaker one — see that function's docstring.
    section["resolution"] = rule
    named = sorted({int(getattr(o, "probe_index", rpm.PROBE_INDEX_NONE))
                    for o in objects
                    if rpm.has_probe(getattr(o, "probe_index", None))})
    section["probe_indices_used"] = named
    section["probe_indices_out_of_range"] = [i for i in named if i >= res.n_probes]
    section["textures_copied"] = bool(files)
    return section


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", default="0703fd2acd5803e9", help="archive name hash")
    ap.add_argument("--mesh", action="append", default=[], help="meshlist hash (repeatable)")
    ap.add_argument("--all", action="store_true", help="extract every meshlist in the archive")
    ap.add_argument("--list", action="store_true", help="list meshlist hashes and exit")
    ap.add_argument("--out", type=Path, default=BLENDER_TOOL / "exports",
                    help="output root for .lemesh packages")
    ap.add_argument("--no-materials", action="store_true", help="skip M2 material resolution")
    ap.add_argument("--textures", action="store_true",
                    help="extract referenced DDS textures into <pkg>/textures/ (incl. cross-archive homes)")
    ap.add_argument("--no-lightmap", action="store_true",
                    help="skip the manifest `lightmap` section (the level atlas binding)")
    ap.add_argument("--lightmap-textures", action="store_true",
                    help="ALSO copy the level lightmap atlas DDS into <pkg>/lightmap/. "
                         "OFF by default: the colour atlas alone is ~68 MB per level "
                         "and one atlas serves every mesh-list of that scene")
    ap.add_argument("--no-probes", action="store_true",
                    help="skip the manifest `reflection_probes` section")
    ap.add_argument("--probe-textures", action="store_true",
                    help="ALSO extract each reflection probe's BC6H cube as a DX10 "
                         "cubemap DDS into <pkg>/probes/. OFF by default: one level "
                         "ships up to 37 probes at 512 KiB each (station_front: 16 "
                         "probes = 8.0 MiB)")
    ap.add_argument("--tsv-materials", action="store_true",
                    help="resolve shaderset->texture from the precomputed scan TSVs "
                         "instead of live from the archive. LEGACY: the TSVs cover only "
                         "1 of 102 mesh-bearing archives (4.1%% of mesh-lists)")
    ap.add_argument("--direct-materials", action="store_true",
                    help=argparse.SUPPRESS)   # deprecated: direct is now the default
    ap.add_argument("--drop-shadow-only", action="store_true",
                    help="omit eShadowOnly meshes from the package")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    names = load_hash_lookup(DEFAULT_HASH_LOOKUP)
    print(f"hash_lookup: {len(names)} entries")
    print(f"loading archive {args.archive} ...")
    arc = Archive(args.archive, names)
    all_hashes = arc.meshlist_hashes()
    print(f"  {len(all_hashes)} paired CGMeshListResource(s)")

    if args.list:
        for h in all_hashes:
            print(f"  {h}")
        return 0

    # M2 inputs. Direct (live-from-archive) resolution is the DEFAULT: measured on
    # the reference archive it reproduces the scan TSV exactly (212 bindings / 57
    # shadersets both ways) and is the only option for the other 101 archives.
    # `--direct-materials` is retained as a no-op alias so old invocations work.
    direct = not args.tsv_materials
    shd_tex = dxgi = tex_home = {}
    if not args.no_materials:
        if not direct and SCAN_TSV.exists():
            shd_tex = mat.load_shaderset_textures(SCAN_TSV, names)
        # dxgi + homes are needed in BOTH modes: direct resolution still has to know
        # which archive to pull each DDS out of, and what format it is.
        dxgi = mat.load_dxgi_by_tex(*TEX_MANIFESTS)
        tex_home = mat.load_texture_homes(SCAN_TSV if SCAN_TSV.exists() else None,
                                          *TEX_MANIFESTS)
        for th, home in load_global_texture_index().items():
            tex_home.setdefault(f"{th:016x}", home)
        src = "direct-from-archive" if direct else f"{len(shd_tex)} shadersets (TSV)"
        print(f"  materials: {src}, {len(dxgi)} texture formats, "
              f"{len(tex_home)} texture homes")

    targets = all_hashes if args.all else [h.lower() for h in args.mesh]
    if not targets:
        ap.error("nothing to do: pass --mesh <hash> (repeatable), --all, or --list")

    ok = fail = skip = 0
    for h in targets:
        try:
            objects = arc.build(h)
            if not objects:
                print(f"  SKIP {h}: empty mesh-list (no geometry)")
                skip += 1
                continue
            out_dir = args.out / f"{args.archive}_{h}.lemesh"
            materials = ([] if args.no_materials else
                         _resolve_materials(arc, h, objects, out_dir=out_dir,
                                            shd_tex=shd_tex, dxgi=dxgi, tex_home=tex_home,
                                            names=names, direct=direct,
                                            want_textures=args.textures, verbose=args.verbose))
            lightmap_section = ({} if args.no_lightmap else
                                resolve_lightmap_section(
                                    arc, h, objects, out_dir=out_dir,
                                    want_textures=args.lightmap_textures,
                                    verbose=args.verbose))
            probe_section = ({} if args.no_probes else
                             resolve_probe_section(
                                 arc, h, objects, out_dir=out_dir,
                                 want_textures=args.probe_textures,
                                 verbose=args.verbose))
            pkg.write_package(
                out_dir,
                source={"game": "lone_echo", "archive": args.archive, "meshlist": h,
                        "tool_version": "0.1.0"},
                objects=objects, materials=materials,
                drop_shadow_only=args.drop_shadow_only,
                lightmap=lightmap_section,
                reflection_probes=probe_section,
            )
            if probe_section:
                print(f"      probes: resource {probe_section.get('resource')} "
                      f"{probe_section.get('count')} probes / "
                      f"{probe_section.get('box_count')} boxes "
                      f"{probe_section.get('texture_format_name')} "
                      f"used={probe_section.get('probe_indices_used')} "
                      f"oob={probe_section.get('probe_indices_out_of_range')} "
                      f"copied={probe_section.get('textures_copied')}")
            if lightmap_section:
                col = lightmap_section.get("color") or {}
                print(f"      lightmap: resource {lightmap_section.get('resource')} "
                      f"row {lightmap_section.get('row')} colour {col.get('hash')} "
                      f"arraysize {col.get('arraysize')} pages "
                      f"{lightmap_section.get('pages')} "
                      f"slices/page {lightmap_section.get('slices_per_page')} "
                      f"copied={lightmap_section.get('textures_copied')}")
            nverts = sum(o.vertex_count for o in objects)
            ntris = sum(sum(d.idx_count for d in o.draws if d.is_triangles) // 3
                        for o in objects)
            print(f"  OK  {h}: {len(objects)} meshes, {nverts} verts, {ntris} tris, "
                  f"{len(materials)} materials -> {out_dir.name}")
            ok += 1
        except Exception as exc:   # noqa: BLE001
            print(f"  FAIL {h}: {exc}")
            if args.verbose:
                import traceback
                traceback.print_exc()
            fail += 1

    print(f"\ndone: {ok} extracted, {skip} skipped (empty), {fail} failed -> {args.out}")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
