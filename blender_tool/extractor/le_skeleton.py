"""le_skeleton -- offline CSkeletonResource -> skeleton.json extractor.

Standalone Stage-1 companion to le_extract.py (does NOT modify it). Finds
`CSkeletonResourceWin7` subresources in an archive, decodes them with the
pure-stdlib le_mesh.skinning core, and writes `skeleton.json` INTO an existing
`.lemesh` package directory (append-only) or to a plain output dir.

MUST run under Windows Python so le_oodle can load the game Oodle DLL:

    # list skeletons in an archive
    python.exe blender_tool/extractor/le_skeleton.py --archive 0703fd2acd5803e9 --list

    # decode one skeleton into an existing package (append skeleton.json)
    python.exe blender_tool/extractor/le_skeleton.py --archive 0703fd2acd5803e9 \
        --skeleton 892cca9de00b30a6 --into blender_tool/fixtures/0703fd2acd5803e9_892cca9de00b30a6.lemesh

    # auto-match: pick the skeleton whose name hash == the package's meshlist hash
    python.exe blender_tool/extractor/le_skeleton.py --archive 0703fd2acd5803e9 \
        --into blender_tool/fixtures/0703fd2acd5803e9_892cca9de00b30a6.lemesh

    # write a standalone skeleton.json
    python.exe blender_tool/extractor/le_skeleton.py --archive 0703fd2acd5803e9 \
        --skeleton 19557c94c6d17883 --out blender_tool/exports/skel

NOTE on the mesh<->skeleton link (investigated against the engine's own type names):
  * CGMeshListData (the mesh resource) carries NO skeleton reference -- it is pure
    geometry (meshes/renderparams/vertex+index buffers/LOD/cbuffers). Skin indices
    are bare integers with no embedded skeleton id.
  * The AUTHORITATIVE runtime binding lives in CAnimController.skeletonname
    (CSymbol64 @+0x00) + skelresource (CResourceInstanceT<CSkeletonResource> @+0x08).
    The anim controller and the model (SModelCD) are sibling components on the same
    actor/entity; the pairing is scene/actor-level data, NOT in the mesh archive.
    Recovering it authoritatively is needs-disasm (scene/SncaComponentData graph).
  * PROXY THAT WORKS: the asset pipeline co-names a skinned mesh and its skeleton --
    they share the exact CSymbol64 asset hash. Measured on archive 0703fd2acd5803e9:
    34/34 CSkeletonResourceWin7 have a same-hash CGMeshListResourceWin7 (the other
    20 meshlists are simply non-skinned). So `--into` without `--skeleton` matches by
    that shared name hash; it is a reliable pipeline convention here, and the match is
    corroborated by confirming the hash also names a mesh resource.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

THIS = Path(__file__).resolve()
BLENDER_TOOL = THIS.parents[1]
LE_ROOT = THIS.parents[2]
SCRIPTS = LE_ROOT / "scripts"
for p in (str(BLENDER_TOOL), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from le_oodle import load_decompressed                        # noqa: E402
from le_archive_decode import (                     # noqa: E402
    ARCHIVE_GPU, ARCHIVE_PRIMARY, DEFAULT_HASH_LOOKUP,
    archive_offsets, load_hash_lookup, parse_header, resource_entries,
)
from le_mesh import skinning                                  # noqa: E402
from le_model_link import classify_binding, model_referenced_assets  # noqa: E402

SKELETON_TYPE = "CSkeletonResourceWin7"


class SkeletonArchive:
    """Decompressed archive with resolved CSkeletonResource slices."""

    def __init__(self, archive_hash: str, names: dict):
        self.hash = archive_hash
        primary_path = ARCHIVE_PRIMARY / archive_hash
        gpu_path = ARCHIVE_GPU / archive_hash
        self.primary = load_decompressed(primary_path)
        gpu = load_decompressed(gpu_path)
        _, _, self.data_off, header0_off = archive_offsets(self.primary, gpu)
        header0 = parse_header(self.primary, header0_off)
        # skeletons live in the primary (header0). GPU stream not needed.
        self.rows = resource_entries(self.primary, header0, names, SKELETON_TYPE)
        # co-named meshlists -> corroborate the name-hash mesh<->skeleton link.
        self.meshlist_hashes = set(
            resource_entries(self.primary, header0, names, "CGMeshListResourceWin7"))
        del gpu

    def hashes(self) -> list[str]:
        return sorted(f"{h:016x}" for h in self.rows)

    def slice_for(self, skeleton_hash: int) -> bytes:
        entry = self.rows[skeleton_hash]
        _idx, pos, size = entry
        start = self.data_off + pos
        return self.primary[start:start + size]

    def decode(self, skeleton_hash: int) -> "skinning.Skeleton":
        return skinning.decode_skeleton(self.slice_for(skeleton_hash))


def _write_skeleton_json(out_dir: Path, archive_hash: str, skel, binding=None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        "format": "le_skeleton",
        "version": 1,
        "source": {"game": "lone_echo", "archive": archive_hash,
                   "skeleton": f"{skel.name_hash:016x}", "tool_version": "0.1.0"},
        **skel.as_dict(),
    }
    if binding is not None:
        doc["binding"] = binding
    path = out_dir / "skeleton.json"
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return path


def _package_meshlist_hash(pkg_dir: Path) -> str | None:
    manifest = pkg_dir / "manifest.json"
    if not manifest.exists():
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return (data.get("source", {}) or {}).get("meshlist")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", default="0703fd2acd5803e9", help="archive name hash")
    ap.add_argument("--skeleton", help="skeleton resource name hash")
    ap.add_argument("--list", action="store_true", help="list skeletons and exit")
    ap.add_argument("--into", type=Path,
                    help="existing .lemesh package dir to append skeleton.json into")
    ap.add_argument("--out", type=Path,
                    help="plain output dir for a standalone skeleton.json")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    names = load_hash_lookup(DEFAULT_HASH_LOOKUP)
    print(f"loading archive {args.archive} ...")
    arc = SkeletonArchive(args.archive, names)
    all_hashes = arc.hashes()
    print(f"  {len(all_hashes)} {SKELETON_TYPE}(s)")

    if args.list:
        for h in all_hashes:
            skel = arc.decode(int(h, 16))
            tag = []
            if skel.has_hierarchy:
                tag.append("tree")
            if skel.has_names:
                tag.append("names")
            if skel.has_bindpose:
                tag.append("bindpose")
            if skel.has_inverse_bind:
                tag.append("invbind")
            mesh = "mesh" if int(h, 16) in arc.meshlist_hashes else "no-mesh"
            print(f"  {h}  joints={skel.joint_count:<4} "
                  f"[{','.join(tag) or 'name-only'}] {mesh}")
        return 0

    # resolve which skeleton
    target = None
    if args.skeleton:
        target = args.skeleton.lower()
        if int(target, 16) not in arc.rows:
            ap.error(f"{target} is not a {SKELETON_TYPE} in {args.archive}")
    elif args.into is not None:
        pkg_hash = _package_meshlist_hash(args.into)
        if pkg_hash and int(pkg_hash, 16) in arc.rows:
            target = pkg_hash.lower()
            # corroborate: the shared hash should ALSO name a mesh resource (the
            # co-named mesh<->skeleton pipeline pairing, reliable in this corpus).
            co_named = int(target, 16) in arc.meshlist_hashes
            print(f"  auto-matched skeleton {target} == package meshlist hash "
                  f"(name-hash link; co-named mesh resource present: {co_named})")
        else:
            ap.error(
                f"no skeleton in {args.archive} matches package meshlist "
                f"{pkg_hash!r}; pass --skeleton explicitly. Available: "
                f"{', '.join(all_hashes[:8])}...")
    else:
        ap.error("nothing to do: pass --list, or --skeleton/--into to decode")

    skel = arc.decode(int(target, 16))
    print(f"  decoded {target}: {skel.joint_count} joints "
          f"(hierarchy={skel.has_hierarchy} names={skel.has_names} "
          f"bindpose={skel.has_bindpose} inverse_bind={skel.has_inverse_bind} "
          f"nonuniform_scale={skel.has_nonuniform_scale} is_tree={skel.is_tree()})")
    for note in skel.notes:
        print(f"    note: {note}")

    # Authoritative binding provenance: scope the mesh<->skeleton link to assets a
    # CModelCR actually references (SModelCD.modelassets[]); the shared-hash co-naming
    # stays as a safe fallback. [le_model_link; stream-confirmed read path]
    th = int(target, 16)
    owners = model_referenced_assets(args.archive, names)
    binding = classify_binding(th, owners, arc.meshlist_hashes)
    if binding["method"] == "modelassets":
        print(f"  binding: model-scoped (CModelCR.modelassets) -- "
              f"{len(binding['actornodeids'])} actornode(s); "
              f"co-named meshlist: {binding['co_named_meshlist']}")
    elif binding["method"] == "shared_hash_fallback":
        print("  binding: WARNING co-named pair not referenced by any CModelCR "
              "(orphan/anim-only) -- shared-hash fallback")
    else:
        print("  binding: WARNING skeleton neither model-referenced nor co-named "
              "with a meshlist -- unverified")

    dest = args.into if args.into is not None else args.out
    if dest is None:
        ap.error("pass --into <package.lemesh> or --out <dir> to write skeleton.json")
    path = _write_skeleton_json(dest, args.archive, skel, binding)
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
