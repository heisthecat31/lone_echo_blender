"""Map EVERYTHING a level loads -- the complete transitive closure.

## Why this exists

`CArchiveResource` is the engine's own load list: the resources it names are
exactly what gets loaded, and anything absent is never loaded.  So the honest
answer to "what is in this level?" is its archive closure -- not what a mesh
exporter happened to collect.

Two on-disk layouts exist, and only one was previously readable.

    Layout A   word0 == 0     per-level / per-section closure
    Layout B   word0 != 0     AGGREGATOR: pulls in child archives

`quest_combat_port/tools/resource_io/carchiveresource.py` decodes Layout A and
raises `NotLayoutA` on B, documenting it as "NOT decoded here" -- 17 of its 298
archives, including every master / menu / global one.  **The Summer lobby is
Layout B**, which is why its closure looked absent: the reader refused it and
the caller saw nothing.

## Layout B, decoded

    +0x00        u32  child_count N
    +0x04        N x u64  child archive hashes
    +0x04 + 8N   u32  record_count
    +0x08 + 8N   record_count x 16B  (type_hash:u64, resource_hash:u64)

Evidence, on the Summer build's 79 archives:

  * **69 parse with a 100% type check** -- every record's column A is a real
    resource-type directory in the extract.  A wrong framing does not do that.
  * the child count is exact: `mpl_lobby_b2` declares 8, and the 8 u64s that
    follow are all real archive files (chance of one random u64 hitting a
    79-file set is 4e-18).
  * the remaining 10 are Layout A or stubs.

## What "everything" means here

The map walks the closure TRANSITIVELY through child archives, so a level that
aggregates `r14_glb_global_mp` gets that archive's resources too.  For every
`(type, resource)` pair it records the type name, the file size, and whether
this project has a decoder for it.

⚠ Types with no decoder are NOT dropped.  They are reported with their counts,
byte totals and example hashes, under `unknown`.  A level map that silently
omits what it cannot parse is how "the floor is missing" goes unnoticed for a
long time.

    python scripts/evr_level_map.py <level> --dir <extract>
    python scripts/evr_level_map.py <level> --dir <extract> --json map.json
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evr_resource_types import (ARCHIVE_RESOURCE, TYPE_NAMES, _WIN10_TO_WIN7,
                                normalise_hash, resolve_type_dir, resource_path)

#: Types this project can actually decode, and the module that does it.
DECODERS = {
    "CGMeshListResourceWin10": "evr_scene_extract / evr_structural_decode",
    "CGInstancedModelResourceWin10": "evr_scene_extract",
    "CGMeshListResourceWin10GPU": "(payload for the above)",
    "CGInstancedModelResourceWin10GPU": "(payload for the above)",
    "CGMaterialResourceWin10": "evr_material_resource",
    "CGShaderSetResourceWin10": "evr_shaderset",
    "cgtextureresourceWin10": "evr_texture_resource",
    "CGTextureResourceWin10GPU": "evr_texture_resource (DDS payload)",
    "CGTextureStreamingResourceWin10": "evr_texture_streaming",
    "RawTexturePackfileWin10": "evr_texture_resource",
    "CActorDataResourceWin10": "level_reader.parse_actor_data",
    "CModelCRWin10": "evr_scene_extract._model_cr_bindings",
    "CInstanceModelCRWin10": "level_reader.parse_instance_model_cr",
    "CStaticInstanceModelCRWin10": "evr_level_reader.parse_static_instances",
    "CTransformCRWin10": "evr_level_reader",
    "CGSceneResourceWin10": "evr_lights.parse_scene_lights (lights only)",
    "CGLightMapResourceWin10": "evr_lightmap",
    "CGStaticInstanceResourceWin10": "evr_lightmap.static_instance_lightmaps",
    "CGStaticInstanceResourceWin10GPU": "evr_lightmap (LMUV stream)",
    "CSkeletonResourceWin10": "evr_apply_skeleton (bind pose only)",
    "CAnimSetResourceWin10": "evr_animset (TABLE only -- no poses)",
    "CUICanvasResourceWin10": "evr_ui_extract",
    "CCanvasUICRWin10": "evr_ui_extract",
    "CBVHResourceWin10": "level_reader.parse_bvh_resource (bounds only)",
    "CArchiveResourceWin10": "this module",
}


def read_archive(data: bytes):
    """`(children, records)` for either layout.

    `children` is `[archive hash, ...]` (empty for Layout A); `records` is
    `[(type hash, resource hash), ...]`.
    """
    if len(data) < 12:
        return [], []
    word0 = struct.unpack_from("<I", data, 0)[0]
    if word0 == 0:
        # Layout A: u32 0, u32 count, then 16B (type, resource) records.
        count = struct.unpack_from("<I", data, 4)[0]
        if 8 + count * 16 > len(data):
            return [], []
        return [], [struct.unpack_from("<QQ", data, 8 + i * 16)
                    for i in range(count)]
    # Layout B: aggregator.
    if word0 > 4096 or 4 + word0 * 8 + 4 > len(data):
        return [], []
    children = [struct.unpack_from("<Q", data, 4 + i * 8)[0]
                for i in range(word0)]
    base = 4 + word0 * 8
    count = struct.unpack_from("<I", data, base)[0]
    body = base + 4
    if count == 0 or body + count * 16 > len(data):
        return children, []
    return children, [struct.unpack_from("<QQ", data, body + i * 16)
                      for i in range(count)]


def load_names() -> dict:
    out = {}
    import evr_paths
    for candidate in (evr_paths.hash_lookup(),):
        if candidate.is_file():
            try:
                raw = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.update({k.lower().replace("0x", "").rjust(16, "0"): v
                        for k, v in raw.items() if isinstance(v, str)})
    for name in ("level_names.json", "level_names_echovr.json",
                 "level_names_loneecho2.json"):
        p = _ROOT / "data" / name
        if not p.is_file():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k, v in (raw.get("levels") or {}).items():
            if v:
                out.setdefault(normalise_hash(k), v)
        for g in (raw.get("games") or {}).values():
            for k, v in (g.get("levels") or {}).items():
                if v:
                    out.setdefault(normalise_hash(k), v)
    return out


#: Case-insensitive decoder lookup: the registry spells one type
#: `cgtextureresourceWin10` in lower case while `hash_lookup` gives
#: `CGTextureResourceWin7`.
_DECODERS_CI = {k.lower(): v for k, v in DECODERS.items()}


def _canonical_type_name(name: str) -> str:
    """A `...Win7` / `...Android` type name -> its Win10 spelling."""
    for suffix in ("Win7GPU", "Win7", "AndroidGPU", "Android"):
        if name.endswith(suffix):
            bare = name[:-len(suffix)]
            return bare + ("Win10GPU" if suffix.endswith("GPU") else "Win10")
    return name


def _win10_name_for(type_hash: str, names: dict) -> str:
    """Type name, resolving a Win7 directory hash back to its Win10 name."""
    canonical = normalise_hash(type_hash)
    direct = names.get(canonical)
    if direct:
        return direct
    for win10, win7 in _WIN10_TO_WIN7.items():
        if normalise_hash(win7) == canonical:
            for name, h in TYPE_NAMES.items():
                if normalise_hash(h) == normalise_hash(win10):
                    return name
    return ""


def build(root: Path, level: str, *, recurse: bool = True) -> dict:
    """The full transitive map of everything the level loads."""
    names = load_names()
    root = Path(root)
    arc_dir = resolve_type_dir(root, ARCHIVE_RESOURCE)

    seen_archives: list = []
    queue = [normalise_hash(level)]
    records: list = []
    missing_archives: list = []
    while queue:
        current = queue.pop(0)
        if current in seen_archives:
            continue
        seen_archives.append(current)
        path = resource_path(root, ARCHIVE_RESOURCE, current)
        if path is None:
            missing_archives.append(current)
            continue
        children, recs = read_archive(path.read_bytes())
        records.extend(recs)
        if recurse:
            for c in children:
                h = normalise_hash(c)
                if h not in seen_archives:
                    queue.append(h)

    # --- group by type ------------------------------------------------------
    by_type: dict = defaultdict(list)
    for t, r in records:
        by_type[normalise_hash(t)].append(normalise_hash(r))

    type_dirs = {normalise_hash(d.name): d for d in root.iterdir() if d.is_dir()}
    known, unknown = [], []
    total_bytes = 0
    for type_hash, resources in sorted(by_type.items(),
                                       key=lambda kv: -len(kv[1])):
        uniq = list(dict.fromkeys(resources))
        directory = type_dirs.get(type_hash)
        size = 0
        present = 0
        for r in uniq:
            p = resource_path(root, type_hash, r) if directory else None
            if p:
                present += 1
                size += p.stat().st_size
        total_bytes += size
        name = _win10_name_for(type_hash, names)
        # The decoder table is keyed by the Win10 name. A Win7 extract resolves
        # its own `...Win7` spelling first, so normalise before the lookup --
        # otherwise every type on a Win7 build reports "no decoder".
        canonical_name = _canonical_type_name(name)
        entry = {
            "type": type_hash,
            "name": name or "",
            "records": len(resources),
            "unique": len(uniq),
            "present_on_disk": present,
            "bytes": size,
            "decoder": _DECODERS_CI.get(canonical_name.lower(), ""),
            "canonical_name": canonical_name,
            "examples": uniq[:5],
        }
        (known if name else unknown).append(entry)

    named_resources = {}
    for t, rs in by_type.items():
        for r in rs:
            n = names.get(normalise_hash(r))
            if n:
                named_resources[normalise_hash(r)] = n

    return {
        "level": normalise_hash(level),
        "level_name": names.get(normalise_hash(level), ""),
        "archives_walked": seen_archives,
        "archives_missing": missing_archives,
        "closure_records": len(records),
        "distinct_types": len(by_type),
        "total_bytes": total_bytes,
        "types_known": known,
        "types_unknown": unknown,
        "named_resources": named_resources,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level", help="level hash")
    ap.add_argument("--dir", required=True, help="flat extract root")
    ap.add_argument("--json", help="also write the full map here")
    ap.add_argument("--no-recurse", action="store_true",
                    help="do NOT follow child archives (Layout B only)")
    args = ap.parse_args(argv)

    m = build(Path(args.dir), args.level, recurse=not args.no_recurse)
    print(f"  level            {m['level']}  {m['level_name']}")
    print(f"  archives walked  {len(m['archives_walked'])}"
          + (f"  (missing {len(m['archives_missing'])})"
             if m["archives_missing"] else ""))
    print(f"  closure records  {m['closure_records']:,}")
    print(f"  distinct types   {m['distinct_types']}")
    print(f"  bytes on disk    {m['total_bytes'] / 1e6:,.1f} MB")

    print(f"\n  {'type':18s} {'name':40s} {'uniq':>6s} {'MB':>8s}  decoder")
    for e in m["types_known"]:
        print(f"  {e['type']} {e['name'][:40]:40s} {e['unique']:6d} "
              f"{e['bytes']/1e6:8.2f}  {e['decoder'] or '-- NO DECODER --'}")
    if m["types_unknown"]:
        tot = sum(e["bytes"] for e in m["types_unknown"])
        print(f"\n  UNKNOWN TYPES ({len(m['types_unknown'])}, "
              f"{tot/1e6:.2f} MB) -- recorded, not dropped:")
        for e in m["types_unknown"]:
            print(f"  {e['type']} {'(unnamed)':40s} {e['unique']:6d} "
                  f"{e['bytes']/1e6:8.2f}  e.g. {e['examples'][:2]}")
    if args.json:
        Path(args.json).write_text(json.dumps(m, indent=1), encoding="utf-8")
        print(f"\n  wrote {args.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
