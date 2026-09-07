"""Index a Lone Echo 1 install: which archives are SCENES, which carry meshes.

    python scripts/le_scene_index.py --out app/le1_scene_index.json

Lone Echo 1 has no level list. Its 1,244 archives are undifferentiated hashes
with no shipped names (`data/level_names_loneecho1.json` records that as an
exhausted negative), so the only way to know what a given archive is, is to
look inside it. This writes that answer once so the app does not pay for it
again.

WHAT AN ARCHIVE CAN BE
----------------------
* **a compressed stub** -- a 44- or 57-byte primary or GPU stream, a
  placeholder for content compressed away. `le_extract.Archive` refuses to open
  one. 1,046 of 1,244 on build 3.17.4, so 84% of the corpus.
* **a scene** -- it holds a POPULATED `CGStaticInstanceResourceWin7` master, the
  bulk environment scatter. `le_scene_extract` turns one into a `.lescatter`
  package, which is what the add-on consumes. 62 of them.
* **mesh-bearing** -- no scatter master, but paired `CGMeshListResource`s that
  `le_extract` can pull individual models out of.

COST
----
Detection reads the archive's compressed bytes and decompresses only the TAIL
(the resource header region) plus a 64-byte prelude -- never the payload. That
is ~0.32 s per archive against ~2.2 s for a full `Archive()` load, so a whole
install indexes in about a minute rather than seven.

The master is identified by its TYPE-HASH constant, so no `hash_lookup.json` is
needed; `EMPTY_MASTER_MAX` separates a populated master from the 148-byte empty
placeholder that geometry-light interiors bake.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

STUB_SIZES = (44, 57)


def _pair_dirs(data_root: Path):
    """`(primary, gpu)` for the content archive set under a win7 data root.

    An install ships several `primary/<id>/<version>` holders and most are not
    archive sets. The content set is the one whose file names appear identically
    under `GPU/<id>/<version>` -- an archive is split across a primary and a GPU
    stream and both halves carry the same name.
    """
    def sets(base):
        out = []
        if base.is_dir():
            for holder in sorted(p for p in base.iterdir() if p.is_dir()):
                for version in sorted(p for p in holder.iterdir() if p.is_dir()):
                    names = frozenset(p.name for p in version.iterdir()
                                      if p.is_file())
                    if names:
                        out.append((version, names))
        return out

    gpu_sets = sets(data_root / "GPU")
    best = None
    for version, names in sets(data_root / "primary"):
        if any(names == g for _d, g in gpu_sets):
            if best is None or len(names) > len(best[1]):
                best = (version, names)
    if best is None:
        return None, None
    gdir = next(d for d, g in gpu_sets if g == best[1])
    return best[0], gdir


def scene_master(path: Path):
    """`(master_name_hash, size)` of a populated static master, or None.

    Mirrors the cheap half of `le_static_scatter.load_master_blob`: it stops
    once the resource table says whether a populated master is present, without
    fetching the master itself.
    """
    from le_oodle import chunk_table, decompress_range
    from le_archive_decode import parse_header, entry_at
    import le_static_scatter as scatter

    raw = path.read_bytes()
    uncomp_total, _ = chunk_table(raw)
    prelude = decompress_range(raw, 0, 64)
    primary_size = struct.unpack_from("<Q", prelude, 0)[0]
    extra_skip = struct.unpack_from("<Q", prelude, 24)[0]
    tail = decompress_range(raw, 32 + extra_skip + primary_size, uncomp_total)
    head = parse_header(tail, 0)
    best = None
    for i in range(head.contents.count):
        th, name_hash, val = struct.unpack_from("<QQQ", tail,
                                                head.contents.off + i * 24)
        if th != scatter.STATIC_TYPE_HASH or val >= head.entries.count:
            continue
        _pos, size = entry_at(tail, head, val)
        if size > scatter.EMPTY_MASTER_MAX and (best is None or size > best[1]):
            best = (name_hash, size)
    return best


def build_index(data_root, progress=None) -> dict:
    """Classify every archive. `progress(done, total, name)` if given."""
    data_root = Path(data_root)
    primary, gpu = _pair_dirs(data_root)
    if primary is None:
        raise SystemExit("no paired primary/GPU archive set under %s" % data_root)

    names = sorted(p.name for p in primary.iterdir() if p.is_file())
    stubs, candidates = [], []
    for n in names:
        g = gpu / n
        if ((primary / n).stat().st_size in STUB_SIZES
                or (g.is_file() and g.stat().st_size in STUB_SIZES)):
            stubs.append(n)
        else:
            candidates.append(n)

    scenes, meshy, failed = {}, [], {}
    t0 = time.time()
    for i, n in enumerate(candidates, 1):
        try:
            found = scene_master(primary / n)
        except Exception as exc:                            # noqa: BLE001
            failed[n] = str(exc)[:120]
            found = None
        if found:
            scenes[n] = {"master": "%016x" % found[0], "size": found[1]}
        else:
            meshy.append(n)
        if progress:
            progress(i, len(candidates), n)
    return {
        "format": "le1_scene_index",
        "version": 1,
        "data_root": str(data_root),
        "archive_dir": str(primary),
        "gpu_dir": str(gpu),
        "counts": {"archives": len(names), "stubs": len(stubs),
                   "scenes": len(scenes), "meshes": len(meshy),
                   "failed": len(failed)},
        "seconds": round(time.time() - t0, 1),
        "stubs": stubs,
        "scenes": dict(sorted(scenes.items())),
        "meshes": sorted(meshy),
        "failed": failed,
        "_note": ("Archives holding a populated CGStaticInstanceResourceWin7 "
                  "master are SCENES (le_scene_extract -> .lescatter); the rest "
                  "of the non-stub archives carry meshes only (le_extract). "
                  "Stubs cannot be opened at all."),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-root", default="",
                    help="the win7 data directory (default: LONE_ECHO_DATA_ROOT)")
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args(argv)

    import os
    root = a.data_root or os.environ.get("LONE_ECHO_DATA_ROOT", "")
    if not root:
        ap.error("pass --data-root or set LONE_ECHO_DATA_ROOT")

    def progress(done, total, name):
        # one line per archive so a caller can drive a progress bar by parsing
        print("scan %d/%d %s" % (done, total, name), flush=True)

    index = build_index(root, progress)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(index, indent=1), encoding="utf-8")
    c = index["counts"]
    print("indexed %d archive(s) in %.1fs: %d scene(s), %d mesh archive(s), "
          "%d stub(s), %d failed -> %s"
          % (c["archives"], index["seconds"], c["scenes"], c["meshes"],
             c["stubs"], c["failed"], a.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
