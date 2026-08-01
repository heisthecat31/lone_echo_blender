"""Extract a level's `CGSceneData.lights` table into a `lights.json` sidecar.

Archive-side half of `le_mesh.lights` (which owns all decode + unit math and is
archive-free / unit tested). This module only locates the scene payload inside a
`CArchiveResourceWin7` primary and hands the lights table to the decoder.

⚠ Decoding lights is NOT the same as importing them. Most Lone Echo level lights
are specular-only and sit on top of a baked lightmap; adding them all to a
Blender scene double-lights it. The add-on's light importer is off by default and
imports only the `eEnableDiffuse` subset. See `docs/LIGHTING.md`.

OOM-SAFE BY CONSTRUCTION: it never decompresses a whole archive. It walks the
compressed chunk table (`le_oodle.decompress_range`) and touches only
  * the 40 B archive prelude,
  * the header tables at the end of the primary,
  * four 4-byte probes inside the scene (BVH sizes + the lights count),
  * the lights table itself (count * 352 B).
A 380 MB-uncompressed level primary costs ~5 chunk decompressions.

Run from the repository root with Windows Python (the Oodle runtime is a Windows
binary), with a RELATIVE --hash-lookup (an absolute path makes `load_hash_lookup`
return {}):

    python.exe blender_tool\\extractor\\le_lights.py <archive-hash> ^
        --out blender_tool\\exports\\<name>_lights.json

There is also an ARCHIVE-FREE path that runs under plain `python3` and never
opens a primary — it re-serialises an already-decoded dump (a `lights.json` of
any version, or an ad-hoc probe dump) into the current sidecar schema, pushing
every record back through `encode_light`/`decode_light` so the result is
byte-consistent with the 352 B grid:

    python3 blender_tool/extractor/le_lights.py \\
        --from-json <decoded-dump.json> --scene <scene-name> \\
        --out blender_tool/exports/<name>_lights.json

The member walk is read out of shipped bytes: the same scene prefix parses on 28
shipped level scenes and lands byte-exactly on the following `actors` table.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]          # repository root
for _p in (str(_ROOT / "scripts"), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from le_mesh import lights as le_lights  # noqa: E402

# Windows consoles default to cp1252 and argparse echoes this module's docstring
# on --help, so any non-ASCII in it raises UnicodeEncodeError the moment stdout
# is not a console (a pipe, a redirect, CI). Force UTF-8 on the streams we own.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):   # already-wrapped or non-reconfigurable
        pass

SCENE_TYPE = "CGSceneResourceWin7"


def _align16(v: int) -> int:
    return (v + 15) & ~15


def _parse_header(buf: bytes, off: int):
    """`le_archive_decode.parse_header`, but against a buffer whose
    index 0 is the header region's absolute base (so the caller can decompress
    only the tail). Returns (header_dict, next_offset)."""
    off += 0xB0                                   # SLanguageSelection blob

    def counted(o, esz):
        c = struct.unpack_from("<I", buf, o)[0]
        return (c, o + 4, esz), o + 4 + c * esz

    parents, off = counted(off, 8)
    entries, off = counted(off, 8)
    contents, off = counted(off, 24)
    off += 4                                      # contents seed
    versions, off = counted(off, 16)
    off += 4                                      # versions seed
    hashes, off = counted(off, 16)
    ptrpatches, off = counted(off, 32)
    return {"parents": parents, "entries": entries, "contents": contents,
            "versions": versions, "hashes": hashes, "ptrpatches": ptrpatches}, off


def extract_lights(archive_hash: str, hash_lookup: Path = Path("hash_lookup.json")):
    """[{scene_hash, scene_name, lights:[...] }] for every scene in the archive."""
    from le_oodle import chunk_table, decompress_range
    from le_archive_decode import ARCHIVE_PRIMARY, load_hash_lookup
    from le_symbol_names import symbol64

    names = load_hash_lookup(Path(hash_lookup))
    scene_type_hash = int(symbol64(SCENE_TYPE), 16)

    raw = (ARCHIVE_PRIMARY / archive_hash).read_bytes()
    total, _ = chunk_table(raw)

    pre = decompress_range(raw, 0, 40)
    primary_size = struct.unpack_from("<Q", pre, 0)[0]
    extra_skip = struct.unpack_from("<Q", pre, 24)[0]
    data_off = 32 + extra_skip
    header0_off = data_off + primary_size

    tail = decompress_range(raw, header0_off, total)
    hdr0, _ = _parse_header(tail, 0)
    ecount, eoff, _ = hdr0["entries"]
    ccount, coff, _ = hdr0["contents"]

    scenes = []
    for i in range(ccount):
        type_hash, name_hash, value = struct.unpack_from("<QQQ", tail, coff + i * 24)
        if type_hash != scene_type_hash or value >= ecount:
            continue
        pos, size = struct.unpack_from("<II", tail, eoff + value * 8)
        scene_abs = data_off + pos

        def u32_at(o):
            return struct.unpack_from("<I", decompress_range(raw, o, o + 4), 0)[0]

        # SBVHTreeData: [u32 tribytes][align16][tris][u32 nodebytes][align16][nodes]
        #               [u32 root][align16]
        tri = u32_at(scene_abs)
        o = _align16(scene_abs + 4) + tri
        nodes = u32_at(o)
        o = _align16(o + 4) + nodes
        o = _align16(o + 4)                       # past root

        count = u32_at(o)
        recs = []
        if count:
            stride = le_lights.STRIDE
            blob = decompress_range(raw, o, o + 4 + count * stride)
            recs, end = le_lights.decode_lights_table(blob, 0)
            assert end == len(blob), (end, len(blob))
        scenes.append({
            "scene_hash": f"{name_hash:016x}",
            "scene_name": names.get(name_hash, ""),
            "scene_size": size,
            "bvh_triangle_bytes": tri,
            "bvh_node_bytes": nodes,
            "num_lights": count,
            "lights": recs,
        })
    del raw, tail
    return scenes


SIDECAR_FORMAT = "le_lights"
SIDECAR_VERSION = 2


def summarise(scenes) -> dict:
    """Corpus counts the importer surfaces before it imports anything.

    `specular_only` is the number that would DOUBLE-LIGHT a Blender scene if
    imported naively (see `docs/LIGHTING.md`).
    """
    out = {"scenes": len(scenes), "scenes_with_lights": 0, "lights": 0,
           "by_type": {}, "enabled": 0, "diffuse_enabled": 0,
           "specular_enabled": 0, "specular_only": 0, "cast_shadows": 0,
           "by_attenmethod": {}, "lossy_falloff": 0}
    for s in scenes:
        if s["num_lights"]:
            out["scenes_with_lights"] += 1
        for r in s["lights"]:
            out["lights"] += 1
            out["by_type"][r.type_name] = out["by_type"].get(r.type_name, 0) + 1
            k = f"{r.attenmethod:g}"
            out["by_attenmethod"][k] = out["by_attenmethod"].get(k, 0) + 1
            out["enabled"] += 1 if r.enabled else 0
            out["diffuse_enabled"] += 1 if r.affects_diffuse else 0
            out["specular_enabled"] += 1 if r.affects_specular else 0
            out["specular_only"] += 1 if (r.affects_specular and not r.affects_diffuse) else 0
            out["cast_shadows"] += 1 if (r.options & (le_lights.eCastShadows
                                                      | le_lights.eCastLevelShadows)) else 0
            out["lossy_falloff"] += 0 if le_lights.falloff_is_physical(r) else 1
    return out


def write_lights_json(scenes, out_path: Path, archive_hash: str,
                      source: str = "archive") -> Path:
    """Serialise to the `le_lights` sidecar contract (pure data, no archive).

    ==========================================================================
    SIDECAR SCHEMA -- `le_lights` v2  (consumed by
    `addon/lone_echo_import/light_import.py`)
    ==========================================================================
    {
      "format": "le_lights", "version": 2,
      "archive": "<hex16>",              # source archive hash
      "source":  "archive" | "<provenance>",   # how the records were obtained
      "axis":    "native",               # GAME space (Y-up); the addon applies
                                         # the +90degX basis at import time
      "record":  "SGLightParams/352",    # the Lone Echo stride -- NOT the 360 B
                                         # one of the later engine revision
      "summary": { ... }                 # see `summarise()`
      "scenes": [ { "scene_hash", "scene_name", "num_lights",
                    "lights": [ <light> ] } ]
    }

    <light> carries the WHOLE decoded record verbatim in game space
    (authoritative), plus a derived `blender` block. Everything in `blender` is
    recomputed by the addon from the raw fields -- the block is a convenience and
    a cross-check, never the source of truth
    (tests/test_light_import.py::test_addon_math_matches_extractor_sidecar).

    `blender.matrix` is the 4x4 row-major WORLD matrix
    `A @ T(pos) @ R(orientation) @ Rx(180deg)`; `blender.not_derivable` holds the
    fields with no Blender equivalent, which the addon carries onto the object as
    inert custom properties (`le_*`) and never converts.

    v1 (no `summary`, fewer raw fields, no `matrix`) still loads in the addon.
    """
    doc = {
        "format": SIDECAR_FORMAT,
        "version": SIDECAR_VERSION,
        "archive": archive_hash,
        "source": source,
        "axis": "native",                          # game Y-up; convert at import
        "record": "SGLightParams/352",
        "summary": summarise(scenes),
        "scenes": [],
    }
    for s in scenes:
        doc["scenes"].append({
            "scene_hash": s["scene_hash"],
            "scene_name": s["scene_name"],
            "num_lights": s["num_lights"],
            "lights": [_light_json(r) for r in s["lights"]],
        })
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return out_path


def _light_json(r) -> dict:
    b = le_lights.to_blender(r)
    return {
        # --- raw game-space record (authoritative, complete) ---
        "index": r.index,
        "name": f"{r.name:016x}",
        "type": r.type_name,
        "lighttype": r.lighttype,
        "options": r.option_names,
        "options_raw": r.options,
        "pos": list(r.pos),
        "primarycolor": list(r.primarycolor),
        "secondarycolor": list(r.secondarycolor),
        "attenuation": list(r.attenuation),
        "orientation": list(r.orientation),
        "direction": list(r.direction),
        "fovy": r.fovy,
        "penumbra": list(r.penumbra),
        "falloff": r.falloff,
        "attenmethod": r.attenmethod,
        "nearp": r.nearp,
        "farp": r.farp,
        "filtersize": r.filtersize,
        "bias": r.bias,
        "shadowfadestart": r.shadowfadestart,
        "shadowfadeend": r.shadowfadeend,
        "shadowthrottledist": r.shadowthrottledist,
        "shadowresolution": r.shadowresolution,
        "shadowoffsetscale": r.shadowoffsetscale,
        "lightoffsetstart": r.lightoffsetstart,
        "lightoffsetdist": r.lightoffsetdist,
        "airlightminradius": r.airlightminradius,
        "lightmask": r.lightmask,
        "visindex": r.visindex,
        "qualitylevel": r.qualitylevel,
        "shadowqualitylevel": r.shadowqualitylevel,
        "quantizer": f"{r.quantizer:016x}",
        "signal": list(r.signal),
        "shadowangularfade": list(r.shadowangularfade),
        "cachedjointidx": r.cachedjointidx,
        "jointoffsetidx": r.jointoffsetidx,
        "fade": r.fade,
        "lightshaft": {**r.lightshaft, "goboassetid": f"{r.lightshaft['goboassetid']:016x}"},
        "scenemask": r.scenemask.hex(),
        # --- derived Blender view (see le_mesh.lights for the arithmetic) ---
        "blender": {
            "type": b["type"], "location": list(b["location"]),
            "direction": list(b["direction"]),
            "matrix": [list(row) for row in b["matrix"]],
            "color": list(b["color"]),
            "energy": b["energy"], "peak_radiance": b["peak_radiance"],
            "spot_size": b["spot_size"],
            "spot_blend": b["spot_blend"], "cutoff_distance": b["cutoff_distance"],
            "shadow_soft_size": b["shadow_soft_size"],
            "use_shadow": b["use_shadow"],
            "physical_falloff": b["physical_falloff"],
            "attenmethod": b["attenmethod"],
            "enabled": b["enabled"],
            "affects_diffuse": b["affects_diffuse"],
            "affects_specular": b["affects_specular"],
            "not_derivable": b["not_derivable"],
        },
    }


# ---------------------------------------------------------------------------
# Archive-free rebuild (fixtures, regression corpora, re-serialising old dumps)
# ---------------------------------------------------------------------------

def scenes_from_json(obj):
    """Rebuild the `scenes` structure from ANY already-decoded JSON dump.

    Accepts a `lights.json` of either version, or an ad-hoc probe dump: anything
    containing dicts with a `lights` list of decoded field dicts. Each light dict
    is pushed back through `le_mesh.lights.record_from_fields`, which re-encodes
    and re-decodes it against the 352 B grid -- so a fixture built this way is
    guaranteed consistent with a real decode.

    NO ARCHIVE IS TOUCHED.
    """
    scenes = []

    def walk(node):
        if isinstance(node, list):
            for v in node:
                walk(v)
            return
        if not isinstance(node, dict):
            return
        lts = node.get("lights")
        if isinstance(lts, list) and all(isinstance(x, dict) for x in lts):
            recs = [le_lights.record_from_fields(d, i) for i, d in enumerate(lts)]
            scenes.append({
                "scene_hash": node.get("scene_hash", ""),
                "scene_name": node.get("scene_name", ""),
                "scene_size": node.get("scene_size", 0),
                "bvh_triangle_bytes": node.get("bvh_tri", node.get("bvh_triangle_bytes", 0)),
                "bvh_node_bytes": node.get("bvh_nodes", node.get("bvh_node_bytes", 0)),
                "num_lights": len(recs),
                "lights": recs,
            })
            return
        for v in node.values():
            walk(v)

    walk(obj)
    return scenes


def rebuild_from_json(in_path: Path):
    """`scenes_from_json` over a file. Returns (scenes, archive_hash_or_empty)."""
    obj = json.loads(Path(in_path).read_text(encoding="utf-8"))
    archive = ""
    if isinstance(obj, dict):
        archive = obj.get("archive", "")
    elif isinstance(obj, list) and obj and isinstance(obj[0], dict):
        archive = obj[0].get("archive", "")
    return scenes_from_json(obj), archive


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hash", nargs="?", default=None,
                    help="archive hash (16 hex chars); omit with --from-json")
    ap.add_argument("--out", type=Path, default=None, help="lights.json output path")
    ap.add_argument("--hash-lookup", type=Path, default=Path("hash_lookup.json"),
                    help="RELATIVE path (absolute silently yields {})")
    ap.add_argument("--from-json", type=Path, default=None,
                    help="ARCHIVE-FREE: re-serialise an already-decoded dump "
                         "(a lights.json of any version, or a probe dump) into "
                         "the current sidecar schema. Runs under plain python3.")
    ap.add_argument("--scene", action="append", default=None,
                    help="keep only scenes whose hash or name matches (repeatable)")
    args = ap.parse_args(argv)

    if args.from_json is not None:
        scenes, archive = rebuild_from_json(args.from_json)
        source = f"rebuild:{Path(args.from_json).name}"
    else:
        if not args.hash:
            ap.error("give an archive hash, or --from-json for the archive-free path")
        scenes = extract_lights(args.hash, args.hash_lookup)
        archive = args.hash
        source = "archive"
    if args.scene:
        want = set(args.scene)
        scenes = [s for s in scenes
                  if s["scene_hash"] in want or s["scene_name"] in want]
    lit = [s for s in scenes if s["num_lights"]]
    print(f"archive {archive or '<none>'} [{source}]: "
          f"{len(scenes)} scenes, {len(lit)} with lights")
    for s in lit:
        types = {}
        diffuse = 0
        for r in s["lights"]:
            types[r.type_name] = types.get(r.type_name, 0) + 1
            diffuse += 1 if r.affects_diffuse else 0
        print(f"  {s['scene_hash']} {s['scene_name'] or '<unnamed>':34s} "
              f"lights={s['num_lights']:4d} {types} diffuse-enabled={diffuse}")
    if args.out:
        p = write_lights_json(scenes, args.out, archive, source=source)
        print(f"wrote {p} ({p.stat().st_size} bytes)")
        print("  summary: " + json.dumps(summarise(scenes)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
