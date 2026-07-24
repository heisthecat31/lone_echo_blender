"""le_extract — offline Lone Echo archive -> .lemesh package extractor.

Stage 1 of the Blender tool. MUST run under Windows Python so `le_oodle` can load
the game Oodle DLL:

    python.exe blender_tool/extractor/le_extract.py --archive 0703fd2acd5803e9 --mesh 8f76d470b7ca990f
    python.exe blender_tool/extractor/le_extract.py --archive 0703fd2acd5803e9 --all
    python.exe blender_tool/extractor/le_extract.py --archive 0703fd2acd5803e9 --list

Reuses the PROVEN decode stack from lone_echo_research/scripts (le_oodle,
le_archive_decode, le_meshlist_decode) to reach
decompressed primary/GPU subresource slices, then hands the bytes to the
pure-stdlib le_mesh core to decode every vertex attribute and write the package.

Milestones:
  M1 (this stage): full-attribute geometry + draws + flags + LOD metadata.   [DONE]
  M2: material role/texture bindings + SGMaterialData scalars + DDS texture
      extraction (incl. cross-archive homes). Roles come from the
      scan TSVs by default, or live from the archive with --direct-materials.  [DONE]
      Flags: --textures (extract DDS), --direct-materials (no TSVs).
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

# --- path wiring: this file is lone_echo_research/blender_tool/extractor/… ---
THIS = Path(__file__).resolve()
BLENDER_TOOL = THIS.parents[1]
LE_ROOT = THIS.parents[2]                 # lone_echo_research
SCRIPTS = LE_ROOT / "scripts"
for p in (str(BLENDER_TOOL), str(SCRIPTS)):
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
import le_textures                                           # noqa: E402

# scan inputs (relative to the lone_echo_research root)
BINDING_TSV = LE_ROOT / "generic_rebuilds" / "scene_binding_parse.tsv"
SCAN_TSV = LE_ROOT / "generic_rebuilds" / "combined_shader_scan.tsv"
TEX_MANIFESTS = [
    LE_ROOT / "generic_rebuilds" / "texture_manifest.tsv",
    LE_ROOT / "generic_rebuilds" / "texture_manifest.tsv",
]


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
        self._texture_hashes = sts.collect_type_hashes(
            self.primary, self.data_off, self.header_off, sts.TEXTURE_TYPE)
        self._pbr = sts.build_pbr_hash_table(self.names)

    def shaderset_roles_direct(self, shaderset_hash: str) -> dict[str, str]:
        """{role_key -> tex_hash} scanned live from this archive (intra-archive textures)."""
        if not shaderset_hash:
            return {}
        self._ensure_shader_scan()
        ent = self._shaderset_map.get(int(shaderset_hash, 16))
        if ent is None:
            return {}
        pos, size = ent
        rows = sts.scan_shaderset_slice(
            self.primary, self.data_off + pos, size, self.hash, shaderset_hash,
            self._texture_hashes, self.names, self._pbr)
        return mat.roles_from_input_rows(rows, self.names)

    def build(self, meshlist_hash: str) -> list[ml.MeshObject]:
        mi = int(meshlist_hash, 16)
        if mi not in self.primary_rows or mi not in self.gpu_rows:
            raise KeyError(f"{meshlist_hash} not a paired CGMeshListResource in {self.hash}")
        _pe, pos, _size = self.primary_rows[mi]
        _ge, gpu_pos, gpu_size = self.gpu_rows[mi]
        start = self.data_off + pos
        # Empty mesh-list resource: all eight loader-order table counts are 0
        # (48-byte all-zero primary, gpudatasize 0, paired to a 16-byte GPU stub).
        # These are legitimately empty (the recorded baseline excludes them too;
        # 106 exist corpus-wide) — return no objects rather than a false FAIL.
        if not any(struct.unpack_from("<8I", self.primary, start)):
            return []
        parsed = parse_candidate(self.primary, start, gpu_size)
        if parsed is None:
            raise RuntimeError(f"parse_candidate failed for {meshlist_hash}")

        def tbl(name):
            t = parsed[name]
            return ml.Table(t.count, t.data_off)

        return ml.build_objects(
            self.primary, self.gpu, gpu_pos,
            meshes=tbl("meshes"), renderparams=tbl("renderparams"),
            vertexbuffers=tbl("vertexbuffers"), indexbuffers=tbl("indexbuffers"),
        )


def _material_key(si: int, shd_hash: str, mat_hash: str) -> str:
    if shd_hash and mat_hash:
        return f"{shd_hash}__{mat_hash}"
    if shd_hash:
        return shd_hash
    if mat_hash:
        return mat_hash
    return f"shd{si}"


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
            key = _material_key(si, shd_hash, mat_hash)
            draw.material_key = key
            pairs.setdefault(key, (shd_hash, mat_hash))

    # --- role textures per unique material key ---
    role_by_key: dict[str, dict[str, str]] = {}
    dxgi_local = dict(dxgi)                 # merge real extracted formats into a copy
    needed_tex: dict[str, str] = {}        # tex_hash -> home archive
    for key, (shd_hash, mat_hash) in pairs.items():
        roles = arc.shaderset_roles_direct(shd_hash) if direct else dict(shd_tex.get(shd_hash, {}))
        role_by_key[key] = roles
        for tex_hash in roles.values():
            home = arc.hash if direct else tex_home.get(tex_hash, arc.hash)
            needed_tex.setdefault(tex_hash, home)

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

    # --- build the specs ---
    specs: dict[str, dict] = {}
    for key, (shd_hash, mat_hash) in pairs.items():
        specs[key] = mat.build_material_spec(
            key, shaderset_hash=shd_hash, material_hash=mat_hash,
            role_textures=role_by_key.get(key, {}), dxgi_by_tex=dxgi_local,
            scalars=arc.material_scalars_for(mat_hash), texture_files=texture_files)
    return list(specs.values())


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
    ap.add_argument("--direct-materials", action="store_true",
                    help="resolve shaderset->texture live from the archive instead of the scan TSVs")
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

    # M2 inputs (scan TSVs are optional; --direct-materials needs none of them)
    shd_tex = dxgi = tex_home = {}
    if not args.no_materials and not args.direct_materials:
        if SCAN_TSV.exists():
            shd_tex = mat.load_shaderset_textures(SCAN_TSV, names)
        dxgi = mat.load_dxgi_by_tex(*TEX_MANIFESTS)
        tex_home = mat.load_texture_homes(SCAN_TSV if SCAN_TSV.exists() else None, *TEX_MANIFESTS)
        print(f"  materials: {len(shd_tex)} shadersets, {len(dxgi)} texture formats, "
              f"{len(tex_home)} texture homes")
    elif args.direct_materials:
        print("  materials: direct-from-archive resolution (no TSVs)")

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
                                            names=names, direct=args.direct_materials,
                                            want_textures=args.textures, verbose=args.verbose))
            pkg.write_package(
                out_dir,
                source={"game": "lone_echo", "archive": args.archive, "meshlist": h,
                        "tool_version": "0.1.0"},
                objects=objects, materials=materials,
                drop_shadow_only=args.drop_shadow_only,
            )
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
