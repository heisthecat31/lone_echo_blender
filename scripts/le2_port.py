"""Port a Lone Echo 2 level into Echo VR's flat `<type>/<resource>` layout.

## Why this is a copy and not a conversion

Lone Echo 2 and Echo VR are the same engine family and **Lone Echo 2 already
ships Win10 resources with the SAME type hashes** -- `CGMeshListResourceWin10`
is `4e426f88c1b5d7ac` in both games, and all 25 types this repo knows are
present in the LE2 extract.  So there is no Win7->Win10 archaeology to do here
(unlike the Summer lobby, where `CGMeshData` is stride 128 vs 152 and the UI
canvas element table shifts by 0x44).  `evr_scene_extract` decodes LE2 geometry
unmodified, on the described `cgml` path.

The port is therefore: take the level's CLOSURE, copy those resources across,
and re-emit an archive that lists only what the target engine can serialize.

## The closure is the load driver

`CArchiveResourceWin10` (`2a41cf1c1d9e5d32`) is the list of every
`(type, resource)` the engine loads for a level -- a resource absent from it is
never loaded, and a resource PRESENT in it that the engine cannot serialize
fails the level load outright.  That is the failure behind:

    Failed to serialize resource (type: '0x403B7B8867A17F09', name: '0x...')
    version mismatch ... Expected Version: 9472387077
                         Archive Version: 16369959984720466089

⚠ Note `0xE32DC7DFD49F50A9` -- the "Archive Version" in that log -- is
`CSymbol64("Empty")`.  That is not a version at all: it is a placeholder a cook
wrote into a section's version stamp.

## Table 2 is misframed by 4 bytes, and that hid the version

`carchiveresource.py` frames Table 2 as `N x (field1:u32, type_hash:u64,
tag:u32)`.  That round-trips, but it splits a field.  The real layout is a u32
COUNT followed by 16-byte `(type_hash:u64, version:u64)` records:

    [N:u32]  [type:u64][version:u64]  [type:u64][version:u64] ...  [role:u32]

so under the old framing a section's `version` appears as its own `tag` (the low
32 bits) plus the NEXT record's `field1` (the high 32).  Three independent
checks, all against the target's own 281 Layout-A archives:

  * `(next.field1 << 32) | tag` is constant per type -- **0 of 163 types vary**,
    where `field1` alone varies 0/1/3/5 for a fixed tag.
  * it reproduces the engine's demanded value exactly, for both types that have
    failed a load here: `CScriptResourceWin10` -> 9472387077 and
    `CCanvasUICRWin10` -> 6289902299.
  * the LAST section has no next record, and its high word turns out to live in
    the tail's low 32 bits: rebuilding it that way matches the type's known
    version in **265 archives with 0 mismatches**, leaving the tail's top 32
    bits 0 in all 280.

This is why stamping only the `tag` still failed: it fixed the low half and left
Lone Echo 2's high half in place.

## The version is the TARGET's to supply

Carrying Lone Echo 2's `tag` across verbatim is wrong, and fails like this:

    version mismatch for assets in archive 0x1581F6362104F69E
        Asset Type: 0x822FD4CCB42E8A3C      (CCanvasUICRWin10)
        Expected Version: 6289902299
        Archive Version:  6320295576

`6289902299 - 1994935003 == 4294967296 == 2**32` EXACTLY, where 1994935003 is
the tag Echo VR's own stock archives carry for that type.  So the on-disk `tag`
is a **u32** (Table-2 is `<IQI`) and the engine's registered version is a u64
whose low 32 bits are that tag -- the same bit-32 relationship the Quest port
documented for `CComponentLODZoneCR`.  6320295576 is simply Lone Echo 2's tag
seen the same way.

⇒ each surviving section must be stamped with the TARGET game's tag.
`build_tag_registry` derives them from the target's own 281 Layout-A archives,
where the tag is **invariant per type across every archive (0 variance)** --
which is what makes stamping them safe rather than a guess.  `field1` is NOT
stamped: it varies per archive for 54 of 164 types, so the source value is kept.
A section whose type has no tag in the target registry is DROPPED, because there
is no honest value to stamp and a wrong one fails the same check.

## What cannot come across

Measured on `zon_fhb_hba_exterior`: of 6073 closure records, **130 (2.1%) are
of 63 types that exist in Lone Echo 2 and NOT in Echo VR** -- `CLanguageTable`
and `CTTFont` by name, plus 61 unnamed R16-era types.  Those sections are
DROPPED, because including a type the target engine does not register is
precisely what makes a level fail to load.  Dropping is the same remedy the
Quest combat port reached for its 18 device-unversioned sections.

## ⛔ NEVER overwrite a stock asset

A resource hash is `CSymbol64` of the asset NAME, so any asset both games named
the same thing collides: `prim_sphere`, `missing`, `common_white`,
`common_black`, `Linear`, ... Writing Lone Echo 2's copy over Echo VR's breaks
the STOCK game, instantly, before any level loads -- the observed failure was

    Offset is past the end of the stream            (cmemstream.cpp:40)
    Unable to create resource for 0x29EDBD196A3C7457 asset '0x171B71164CAC8315'
    ... failed load in archive 0xAC360E41E4EDE056

which is `CGSceneResource:prim_sphere` failing inside `mnu_master`, the frontend
menu.  Stock `prim_sphere` is 704 bytes; Lone Echo 2's is 404, so the engine read
a 704-byte structure's offsets out of a 404-byte file.  149 of this level's files
collide that way.

Colliding resources are therefore SKIPPED, not written.  They stay in the
closure, so the level still resolves them -- to the stock Echo VR asset, which
is what a shared primitive should resolve to anyway.

⛔ This ports the level's DATA.  It does not port behaviour: scripts, animation
and particles are carried as bytes only if their type survives, and nothing
here validates that Echo VR's R15 engine can interpret an R16 resource whose
type hash merely happens to match.  A clean load is the test, not this script.

    python scripts/le2_port.py <level> --src <le2_extract> \\
        --target <echovr_extract> --out <output_tree>
"""

from __future__ import annotations

import argparse
import json
import re
import pathlib
import shutil
import sys
from collections import Counter
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool"),
           str(_ROOT / "app" / "extract" / "resource_io")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import carchiveresource as car          # noqa: E402  Layout-A closure decoder
from evr_resource_types import normalise_hash  # noqa: E402

#: `CArchiveResourceWin10` -- the closure / load driver.
ARCHIVE_RESOURCE = "2a41cf1c1d9e5d32"

#: Names loaded only to make the report readable.
_HASH_LOOKUP = None       # resolved via evr_paths.hash_lookup()


def load_names() -> dict:
    import evr_paths
    path = _HASH_LOOKUP or evr_paths.hash_lookup()
    if path is None:
        return {}
    try:
        raw = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, AttributeError):
        return {}
    return {k.lower().replace("0x", "").rjust(16, "0"): v
            for k, v in raw.items() if isinstance(v, str)}


def type_dirs(root: Path) -> dict:
    """`{canonical 16-hex type hash -> actual directory}`.

    ⚠ Extracts strip leading zeroes from directory names (`1cd04cc3ce48d12` for
    `01cd04cc3ce48d12`), so a plain `root / hash` membership test silently
    reports a type as missing and would drop a section that is really there.
    """
    out = {}
    for entry in root.iterdir():
        if entry.is_dir():
            out[normalise_hash(entry.name)] = entry
    return out


def resource_file(directory: Path, resource_hash: str) -> Path | None:
    stripped = resource_hash.lstrip("0") or "0"
    for stem in dict.fromkeys((resource_hash, stripped)):
        for suffix in ("", ".bin"):
            candidate = directory / f"{stem}{suffix}"
            if candidate.is_file():
                return candidate
    return None


def gpu_pairs(names: dict) -> dict:
    """`{type hash -> its ...GPU sidecar type hash}`.

    Geometry and texture payloads live in a parallel `<name>GPU` type that the
    closure does NOT list separately, so a copy that follows only the closure
    ships descriptors with no pixels and no vertices.
    """
    by_name = {name: h for h, name in names.items()}
    out = {}
    for h, name in names.items():
        sidecar = by_name.get(name + "GPU")
        if sidecar:
            out[h] = sidecar
    return out


#: `Failed to serialize resource (type: '0x..', name: '0x..')` in an r14 log.
_FAILURE_RE = re.compile(
    r"Failed to serialize resource \(type: '0x([0-9A-Fa-f]+)', name: '0x([0-9A-Fa-f]+)'\)")


def failures_in_log(path: Path) -> set:
    """`{(type, resource)}` the engine could not serialize, from an r14 log.

    The iteration loop this enables: port -> launch -> feed the log back with
    `--exclude-failed` -> the offending resources leave the CLOSURE, so the
    engine stops trying to load them.  A resource absent from the closure is
    never loaded, which is the documented behaviour that makes this safe.

    ⚠ This REMOVES CONTENT.  It is a bisection tool for finding out how much of
    a level survives the R16 -> R15 format gap, not a fix for that gap.  What
    drops out is genuinely missing from the level.
    """
    out = set()
    try:
        text = Path(path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return out
    # ⚠ The engine names the PLATFORM-AGNOSTIC type -- `0x29EDBD196A3C7457` is
    # `CGSceneResource`, while the closure keys on `CGSceneResourceWin10`
    # (`a388ea69e5108f4c`). Matching the log's hash directly excludes nothing.
    names = load_names()
    try:
        from le_mesh.material_scalars import symbol64
    except ImportError:                                      # pragma: no cover
        symbol64 = None
    for type_hex, res_hex in _FAILURE_RE.findall(text):
        canonical = normalise_hash(type_hex)
        keys = {canonical}
        base = names.get(canonical)
        if base and symbol64 is not None:
            for suffix in ("Win10", "Win10GPU"):
                keys.add(normalise_hash(symbol64(base + suffix)
                                        & 0xFFFFFFFFFFFFFFFF))
        for key in keys:
            out.add((key, normalise_hash(res_hex)))
    return out


def build_tag_registry(root: Path) -> dict:
    """`{type hash -> u64 version}` from the TARGET's own archives.

    Reads the version with the CORRECT framing (see the module docstring): a
    section's version is `(next_record.field1 << 32) | tag`, and the last
    section's high word comes from the tail's low 32 bits.
    """
    counts: dict = {}
    directory = type_dirs(root).get(normalise_hash(ARCHIVE_RESOURCE))
    if directory is None:
        return {}
    for path in directory.iterdir():
        if not path.is_file():
            continue
        try:
            data = car.read(path.read_bytes())
        except Exception:                                    # noqa: BLE001
            continue      # Layout-B aggregator; no per-type sections to read
        table2 = data["table2"]
        for i, (_field1, type_hash, tag) in enumerate(table2):
            high = (table2[i + 1][0] if i + 1 < len(table2)
                    else data["tail"] & 0xFFFFFFFF)
            version = ((high & 0xFFFFFFFF) << 32) | (tag & 0xFFFFFFFF)
            counts.setdefault(normalise_hash(type_hash), Counter())[version] += 1
    return {k: c.most_common(1)[0][0] for k, c in counts.items()}


def live_index(root: Path) -> dict:
    """`{type hash -> {resource hash}}` for the LIVE game -- the overwrite guard."""
    out = {}
    for directory in root.iterdir():
        if directory.is_dir():
            out[normalise_hash(directory.name)] = {
                normalise_hash(f.name.split(".")[0])
                for f in directory.iterdir() if f.is_file()}
    return out


def port(level: str, src: Path, target: Path, out: Path, *,
         dry_run: bool = False, exclude: set | None = None,
         drop_types=None) -> dict:
    names = load_names()
    src_types = type_dirs(src)
    target_types = type_dirs(target)
    stock = live_index(target)
    tag_registry = build_tag_registry(target)
    sidecar_of = gpu_pairs(names)

    archive_dir = src_types.get(normalise_hash(ARCHIVE_RESOURCE))
    if archive_dir is None:
        raise SystemExit(f"no CArchiveResourceWin10 directory under {src}")
    archive_path = resource_file(archive_dir, normalise_hash(level))
    if archive_path is None:
        raise SystemExit(f"level {level} has no archive in {archive_dir}")

    closure = car.read(archive_path.read_bytes())
    table1 = closure["table1"]

    # --- decide what survives ------------------------------------------------
    droppable = set()
    unstampable = set()
    for type_key, _res in table1:
        key = normalise_hash(type_key)
        if key not in target_types:
            droppable.add(key)
        elif key not in tag_registry:
            # The target registers the type but no stock archive stamps it, so
            # there is no correct version to write. Dropping beats guessing.
            droppable.add(key)
            unstampable.add(key)

    exclude = exclude or set()
    for name in (drop_types or ()):
        key = normalise_hash(name) if all(c in "0123456789abcdefABCDEF"
                                          for c in name) and len(name) >= 8 else None
        if key is None:
            from le_mesh.material_scalars import symbol64
            key = normalise_hash(symbol64(name) & 0xFFFFFFFFFFFFFFFF)
        droppable.add(key)
    kept_t1 = [(k, r) for k, r in table1
               if normalise_hash(k) not in droppable
               and (normalise_hash(k), normalise_hash(r)) not in exclude]
    excluded_hit = sum(1 for k, r in table1
                       if (normalise_hash(k), normalise_hash(r)) in exclude)
    kept_keys = [k for k, _ in car.sections(kept_t1)]
    # Table 2 carries one entry per section, in section order, keyed by the
    # section's own type hash. Rebuild it by filtering the SOURCE entries so
    # every surviving section keeps its original `tag` -- the version stamp the
    # engine checks. Never synthesise one.
    t2_by_key = {}
    for field1, type_hash, tag in closure["table2"]:
        t2_by_key.setdefault(normalise_hash(type_hash), (field1, type_hash, tag))
    # Rebuild Table 2 with the TARGET's u64 version per section, laid back into
    # the on-disk interleave: tag[i] = version.low32, field1[i+1] = version.high32,
    # field1[0] = N, and the last section's high word into the tail's low 32.
    versions = [tag_registry[normalise_hash(k)] for k in kept_keys]
    kept_t2 = []
    for i, key in enumerate(kept_keys):
        high = (versions[i - 1] >> 32) & 0xFFFFFFFF if i else len(kept_keys)
        kept_t2.append((high, key, versions[i] & 0xFFFFFFFF))
    new_tail = ((versions[-1] >> 32) & 0xFFFFFFFF) if versions else closure["tail"]
    restamped = sum(1 for i, key in enumerate(kept_keys)
                    if t2_by_key.get(normalise_hash(key), (0, 0, None))[2]
                    != (versions[i] & 0xFFFFFFFF))

    rebuilt = dict(word0=closure["word0"], count=len(kept_t1),
                   table1=kept_t1, table2=kept_t2, tail=new_tail)
    checks = car.validate(rebuilt)
    if not (checks["n_match"] and checks["keys_match"] and checks["f1_head_is_N"]):
        raise SystemExit(f"rebuilt archive failed validation: {checks}")

    # --- copy ---------------------------------------------------------------
    copied = missing = sidecars = 0
    copied_bytes = 0
    collisions: list = []
    per_type = Counter()
    seen = set()
    for type_key, res in kept_t1:
        key = normalise_hash(type_key)
        rhash = normalise_hash(res)
        for tkey in (key, sidecar_of.get(key)):
            if not tkey:
                continue
            directory = src_types.get(normalise_hash(tkey))
            if directory is None:
                continue
            source = resource_file(directory, rhash)
            if source is None:
                if tkey == key:
                    missing += 1
                continue
            if (tkey, rhash) in seen:
                continue
            seen.add((tkey, rhash))
            # THE OVERWRITE GUARD. Never write over a stock asset.
            if rhash in stock.get(normalise_hash(tkey), ()):
                collisions.append({"type": normalise_hash(tkey),
                                   "resource": rhash,
                                   "name": names.get(rhash, "")})
                continue
            size = source.stat().st_size
            copied_bytes += size
            if tkey == key:
                copied += 1
                per_type[key] += 1
            else:
                sidecars += 1
            if not dry_run:
                dest_dir = out / normalise_hash(tkey)
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, dest_dir / rhash)

    if not dry_run:
        dest = out / normalise_hash(ARCHIVE_RESOURCE)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / normalise_hash(level)).write_bytes(car.write(rebuilt))

    report = {
        "level": normalise_hash(level),
        "source": str(src), "target": str(target), "out": str(out),
        "closure_records_in": len(table1),
        "closure_records_out": len(kept_t1),
        "sections_in": len(closure["table2"]),
        "sections_out": len(kept_t2),
        "dropped_records": len(table1) - len(kept_t1),
        "dropped_types": sorted(
            ({"type": t, "name": names.get(t, ""),
              "records": sum(1 for k, _ in table1 if normalise_hash(k) == t)}
             for t in droppable),
            key=lambda d: -d["records"]),
        "excluded_by_log": excluded_hit,
        "sections_restamped": restamped,
        "types_dropped_unstampable": sorted(unstampable),
        "collisions_skipped": len(collisions),
        "collisions": collisions,
        "resources_copied": copied,
        "gpu_sidecars_copied": sidecars,
        "closure_entries_with_no_file": missing,
        "bytes_copied": copied_bytes,
        "archive_validation": checks,
    }
    if not dry_run:
        (out / f"port_report_{normalise_hash(level)}.json").write_text(
            json.dumps(report, indent=1), encoding="utf-8")
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level", help="LE2 level hash")
    ap.add_argument("--src", required=True, help="LE2 flat extract")
    ap.add_argument("--target", required=True,
                    help="the LIVE Echo VR extract. Read for two things: which "
                         "types that engine registers, and which (type, "
                         "resource) pairs already exist so they are never "
                         "overwritten")
    ap.add_argument("--out", required=True, help="output flat tree")
    ap.add_argument("--exclude-failed", action="append", default=[],
                    metavar="LOG",
                    help="an r14 log; every resource it reports as failing to "
                         "serialize is removed from the closure. Repeatable. "
                         "⚠ removes content -- a bisection tool, not a fix")
    ap.add_argument("--drop-type", action="append", default=[], metavar="TYPE",
                    help="drop a whole type from the closure, by 16-hex hash or "
                         "by type NAME (e.g. CScriptResourceWin10). Repeatable. "
                         "⚠ removes content")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    exclude = set()
    for log in args.exclude_failed:
        found = failures_in_log(Path(log))
        print(f"  {len(found)} failing resource(s) from {log}")
        exclude |= found
    report = port(args.level, Path(args.src), Path(args.target), Path(args.out),
                  dry_run=args.dry_run, exclude=exclude,
                  drop_types=args.drop_type)
    print(f"  level              {report['level']}")
    print(f"  closure records    {report['closure_records_in']} -> "
          f"{report['closure_records_out']}  "
          f"({report['dropped_records']} dropped)")
    print(f"  sections           {report['sections_in']} -> {report['sections_out']}")
    print(f"  sections restamped {report['sections_restamped']}  "
          f"(target's version tag)")
    print(f"  excluded by log    {report['excluded_by_log']}")
    print(f"  resources copied   {report['resources_copied']}")
    print(f"  COLLISIONS skipped {report['collisions_skipped']}  "
          f"(stock assets left intact)")
    print(f"  GPU sidecars       {report['gpu_sidecars_copied']}")
    print(f"  bytes              {report['bytes_copied'] / 1e6:,.1f} MB")
    print(f"  no file on disk    {report['closure_entries_with_no_file']}")
    print(f"  archive validation {report['archive_validation']}")
    print(f"\n  dropped types ({len(report['dropped_types'])}):")
    for row in report["dropped_types"][:8]:
        print(f"    {row['type']} {row['name'] or '(unnamed)':40s} {row['records']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
