"""Write a package's `skeleton.json` + weight blob, when its model has a rig.

`CSkeletonResourceWin10` is same-named as the model's mesh list, so a model
either has a skeleton at its own hash or has none -- no join table needed.

    python scripts/evr_apply_skeleton.py <package_dir> <model_hash> [--dir root]

The sidecar sits beside `manifest.json`, exactly like `lightmaps.json`, so the
importer finds it without the user selecting anything.

## What is decoded, and what is not

**Bind pose** -- solid. `CSkeletonResource` holds one 32-byte record per bone:
quaternion (16) + translation (12) + uniform scale (4). Located by the longest
run of unit-length quaternions at stride 32, which nothing else in the file
produces.

**Bone positions** -- the bind translations are PARENT-RELATIVE, and the
hierarchy is not decoded, so they cannot be composed into model space. A bone's
position is instead the weighted centroid of the vertices it influences, which
needs no hierarchy. Verified on `dac6537a23226325`: bones 8 and 9 land at
x = -0.124 / +0.123 at identical height (a mirrored L/R pair) and the set spans
x +/-0.62 against a mesh of +/-0.63.

**Hierarchy** -- NOT decoded. Bones are emitted parentless. Skinning is correct
and the rest pose is correct; posing a parent will not carry its children.
Bones with no weighted vertices (helpers/IK, 47 of 125 on the reference model)
are omitted rather than piled on the origin.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from collections import defaultdict
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evr_resource_types import SKELETON_RESOURCE, resource_path

BONE_STRIDE = 32

SIDECAR_NAME = "skeleton.json"
SIDECAR_FORMAT = "evr_skeleton"
WEIGHT_BLOB = "skeleton_weights.bin"


def has_skeleton(root: Path, model_hash: str) -> bool:
    return resource_path(root, SKELETON_RESOURCE, model_hash) is not None


def read_bind_pose(root: Path, model_hash: str) -> list:
    """`[(quat xyzw, translation, scale), ...]`, or `[]`."""
    path = resource_path(root, SKELETON_RESOURCE, model_hash)
    if path is None:
        return []
    blob = path.read_bytes()

    def unit(off):
        if off + 16 > len(blob):
            return False
        q = struct.unpack_from("<4f", blob, off)
        return (all(math.isfinite(c) for c in q)
                and abs(math.sqrt(sum(c * c for c in q)) - 1.0) < 0.01)

    best_n = best_off = 0
    off = 0
    while off + BONE_STRIDE * 8 <= len(blob):
        n = 0
        while unit(off + n * BONE_STRIDE):
            n += 1
        if n > best_n:
            best_n, best_off = n, off
        off += max(1, n) * BONE_STRIDE if n else 4

    out = []
    for i in range(best_n):
        base = best_off + i * BONE_STRIDE
        out.append((list(struct.unpack_from("<4f", blob, base)),
                    list(struct.unpack_from("<3f", blob, base + 16)),
                    struct.unpack_from("<f", blob, base + 28)[0]))
    return out


def build(package: Path, root: Path, model_hash: str) -> dict | None:
    """Write the sidecar for one package. Returns a summary, or None."""
    import evr_scene_extract as extractor

    bones = read_bind_pose(root, model_hash)
    if not bones:
        return None

    results, _label = extractor._decode_model_cached(root, model_hash)
    if not results:
        return None
    lod = extractor._group_submeshes_by_lod(results)
    keep = [i for i, (_g, level, _n) in enumerate(lod) if level == 0]

    # Per-bone weighted centroid, and the per-vertex weights themselves.
    acc = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    blob = bytearray()
    meshes = []
    for out_index, source in enumerate(keep):
        result = results[source]
        verts = result[0]
        skin = result[3] if len(result) > 3 else None
        entry = {"mesh": out_index, "nverts": len(verts), "offset": len(blob)}
        if skin:
            for vi, item in enumerate(skin):
                try:
                    idxs, wts = item
                except (TypeError, ValueError):
                    idxs, wts = (0, 0, 0, 0), (0, 0, 0, 0)
                blob += struct.pack("<4H4B", *(list(idxs)[:4] + [0] * 4)[:4],
                                    *(list(wts)[:4] + [0] * 4)[:4])
                v = verts[vi]
                for bi, w in zip(idxs, wts):
                    if not w:
                        continue
                    a = acc[bi]
                    a[0] += v[0] * w
                    a[1] += v[1] * w
                    a[2] += v[2] * w
                    a[3] += w
        entry["weighted"] = bool(skin)
        meshes.append(entry)

    centroid = {b: (a[0] / a[3], a[1] / a[3], a[2] / a[3])
                for b, a in acc.items() if a[3]}

    records = []
    for i, (quat, translation, scale) in enumerate(bones):
        if i not in centroid:
            continue          # no weighted vertices -> no position to place it
        records.append({
            "index": i,
            "position": list(centroid[i]),
            "rotation": quat,
            "scale": scale,
            "local_translation": translation,
        })

    (package / WEIGHT_BLOB).write_bytes(bytes(blob))
    doc = {
        "format": SIDECAR_FORMAT,
        "model": model_hash,
        "bone_count": len(bones),
        "bones": records,
        "weights_blob": WEIGHT_BLOB,
        "weight_record": "4x u16 bone index + 4x u8 weight (0-255), per vertex",
        "meshes": meshes,
        "_note": ("Bones are PARENTLESS: the bind translations are "
                  "parent-relative and the hierarchy is not decoded, so each "
                  "bone is placed at the weighted centroid of the vertices it "
                  "influences. Skinning and rest pose are correct; posing a "
                  "parent will not carry its children. Bones with no weighted "
                  "vertices are omitted."),
    }
    (package / SIDECAR_NAME).write_text(json.dumps(doc, indent=1),
                                        encoding="utf-8")
    return {"bones": len(records), "of": len(bones), "meshes": len(meshes),
            "weight_bytes": len(blob)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("package")
    ap.add_argument("model_hash")
    ap.add_argument("--dir", default="H:/pcvr-extracted")
    args = ap.parse_args(argv)

    package = Path(args.package)
    summary = build(package, Path(args.dir), args.model_hash)
    if summary is None:
        print(f"{args.model_hash}: no skeleton resource -- nothing written")
        return 0
    print(f"{args.model_hash}: {summary['bones']}/{summary['of']} bones placed, "
          f"{summary['meshes']} mesh(es), "
          f"{summary['weight_bytes']:,} B of weights -> {package / SIDECAR_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
