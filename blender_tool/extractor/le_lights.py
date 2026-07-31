"""Extract a level's `CGSceneData.lights` table into a `lights.json` sidecar.

Archive-side half of `le_mesh.lights` (which owns all decode + unit math and is
archive-free / unit tested). This module only locates the scene payload inside a
`CArchiveResourceWin7` primary and hands the lights table to the decoder.

⚠ Decoding lights is NOT the same as importing them. Most Lone Echo level lights
are specular-only and sit on top of a baked lightmap this tool does not yet
import; adding them to a Blender scene double-lights it. See `docs/LIGHTING.md`.

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


def write_lights_json(scenes, out_path: Path, archive_hash: str) -> Path:
    """Serialise to the `le_lights` sidecar contract (pure data, no archive)."""
    doc = {
        "format": "le_lights",
        "version": 1,
        "archive": archive_hash,
        "axis": "native",                          # game Y-up; convert at import
        "record": "SGLightParams/352",
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
        # --- raw game-space record (authoritative) ---
        "name": f"{r.name:016x}",
        "type": r.type_name,
        "options": r.option_names,
        "options_raw": r.options,
        "pos": list(r.pos),
        "primarycolor": list(r.primarycolor),
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
        "lightmask": r.lightmask,
        "visindex": r.visindex,
        "qualitylevel": r.qualitylevel,
        "shadowqualitylevel": r.shadowqualitylevel,
        "fade": r.fade,
        "lightshaft": {**r.lightshaft, "goboassetid": f"{r.lightshaft['goboassetid']:016x}"},
        "scenemask": r.scenemask.hex(),
        # --- derived Blender view (see le_mesh.lights for the arithmetic) ---
        "blender": {
            "type": b["type"], "location": list(b["location"]),
            "direction": list(b["direction"]), "color": list(b["color"]),
            "energy": b["energy"], "spot_size": b["spot_size"],
            "spot_blend": b["spot_blend"], "cutoff_distance": b["cutoff_distance"],
            "shadow_soft_size": b["shadow_soft_size"],
            "physical_falloff": b["physical_falloff"],
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("hash", help="archive hash (16 hex chars)")
    ap.add_argument("--out", type=Path, default=None, help="lights.json output path")
    ap.add_argument("--hash-lookup", type=Path, default=Path("hash_lookup.json"),
                    help="RELATIVE path (absolute silently yields {})")
    args = ap.parse_args(argv)

    scenes = extract_lights(args.hash, args.hash_lookup)
    lit = [s for s in scenes if s["num_lights"]]
    print(f"archive {args.hash}: {len(scenes)} scenes, {len(lit)} with lights")
    for s in lit:
        types = {}
        diffuse = 0
        for r in s["lights"]:
            types[r.type_name] = types.get(r.type_name, 0) + 1
            diffuse += 1 if r.affects_diffuse else 0
        print(f"  {s['scene_hash']} {s['scene_name'] or '<unnamed>':34s} "
              f"lights={s['num_lights']:4d} {types} diffuse-enabled={diffuse}")
    if args.out:
        p = write_lights_json(scenes, args.out, args.hash)
        print(f"wrote {p} ({p.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
