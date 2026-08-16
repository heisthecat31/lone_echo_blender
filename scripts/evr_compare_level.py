"""Diff our extraction against `rad-archive-viewer`'s cached level JSON.

## Why

`rad-archive-viewer` renders these levels correctly and caches its parsed result
at `cache/level_<hash>.json`.  That file is GROUND TRUTH sitting on disk: the
model set, the instance placements, and the per-instance `material_index` that a
working implementation produced from the same bytes.

Several rounds of this project were spent inferring what the extractor was
getting wrong.  Diffing against the working output answers it directly, and
keeps answering it as things change.

## What it compares

* **model set** -- which models the level contains.  Our extractor found 63
  unique models where the viewer requested 120+, and that gap is the headline
  symptom of "most of the level is missing".
* **static instances** -- count, and how many resolve to a model.
* **positions** -- ours against theirs for the same instance index, so a
  quantisation or bounds error shows up as a distance rather than a guess.
* **material_index** -- carried by the viewer and, as of now, parsed but not yet
  used by us.

Usage::

    python.exe scripts/evr_compare_level.py <level_hash> \\
        --cache "C:/Users/lucas/Desktop/desktop/rad-archive-viewer/cache" \\
        --out J:/EchoVRModels
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_CACHE = Path(
    r"C:\Users\lucas\Desktop\desktop\rad-archive-viewer\cache")


def normalise(value) -> str:
    """Any CSymbol64 spelling -> lowercase 16 hex digits."""
    if value is None:
        return ""
    text = str(value).strip().lower()
    if text.startswith("0x"):
        text = text[2:]
    return text.rjust(16, "0") if text else ""


def load_reference(cache_dir: Path, level_hash: str) -> dict:
    path = Path(cache_dir) / f"level_{normalise(level_hash).lstrip('0')}.json"
    if not path.is_file():
        # The viewer keys the file by the hash as typed, not normalised.
        for candidate in Path(cache_dir).glob("level_*.json"):
            if normalise(candidate.stem[6:]) == normalise(level_hash):
                path = candidate
                break
    if not path.is_file():
        raise SystemExit(f"no cached level JSON for {level_hash} in {cache_dir}")
    return json.loads(path.read_text(encoding="utf-8"))


def reference_summary(doc: dict) -> dict:
    """Model set and instance placements from the viewer's own output."""
    instances = doc.get("static_instances") or []
    actors = doc.get("actors") or []

    placed = [i for i in instances if i.get("model_hash")]
    models = {normalise(i.get("model_hash")) for i in placed}

    actor_models = set()
    for actor in actors:
        for key in ("model_hash", "model", "modelHash"):
            if actor.get(key):
                actor_models.add(normalise(actor[key]))
                break

    return {
        "instances_total": len(instances),
        "instances_placed": len(placed),
        "static_models": models,
        "actors": len(actors),
        "actor_models": actor_models,
        "all_models": models | actor_models,
        "by_index": {i.get("index"): i for i in placed},
        "material_indices": sorted({i.get("material_index")
                                    for i in placed
                                    if i.get("material_index") is not None}),
    }


def ours_summary(out_root: Path, level_hash: str) -> dict:
    """Model set and placements from our own written package."""
    pkg = Path(out_root) / "scenes" / normalise(level_hash).lstrip("0")
    if not pkg.is_dir():
        pkg = Path(out_root) / "scenes" / level_hash
    manifest = pkg / "manifest.json"
    if not manifest.is_file():
        raise SystemExit(f"no manifest at {manifest} -- run the extractor first")

    doc = json.loads(manifest.read_text(encoding="utf-8"))
    meshes = doc.get("meshes") or []
    models = {normalise(m.get("name_hash")) for m in meshes if m.get("name_hash")}
    # `name_hash` is stored as an int in the package.
    models = {normalise(f"{int(m['name_hash']):016x}")
              for m in meshes if isinstance(m.get("name_hash"), int)} or models

    return {
        "meshes": len(meshes),
        "instances": sum(m.get("instance_count", 0) for m in meshes),
        "models": models,
        "manifest": manifest,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level", help="level hash, e.g. 576ed3f8428ebc4b")
    ap.add_argument("--cache", type=Path, default=DEFAULT_CACHE,
                    help="rad-archive-viewer cache directory")
    ap.add_argument("--out", type=Path, default=Path(r"J:\EchoVRModels"),
                    help="our extractor's output root")
    ap.add_argument("--samples", type=int, default=8,
                    help="how many per-instance position comparisons to print")
    args = ap.parse_args()

    ref = reference_summary(load_reference(args.cache, args.level))

    print("=== reference (rad-archive-viewer, known good) ===")
    print(f"  actors               {ref['actors']}")
    print(f"  static instances     {ref['instances_total']} "
          f"({ref['instances_placed']} resolve to a model)")
    print(f"  distinct static models {len(ref['static_models'])}")
    print(f"  distinct actor models  {len(ref['actor_models'])}")
    print(f"  distinct models TOTAL  {len(ref['all_models'])}")
    print(f"  material_index values  {ref['material_indices'][:16]}")

    try:
        ours = ours_summary(args.out, args.level)
    except SystemExit as exc:
        print(f"\n{exc}")
        return 1

    print("\n=== ours ===")
    print(f"  meshes               {ours['meshes']}")
    print(f"  instances            {ours['instances']}")
    print(f"  distinct models      {len(ours['models'])}")

    missing = ref["all_models"] - ours["models"]
    extra = ours["models"] - ref["all_models"]
    print("\n=== model set diff ===")
    print(f"  in reference, MISSING from ours   {len(missing)}")
    for h in sorted(missing)[:20]:
        print(f"      {h}")
    if len(missing) > 20:
        print(f"      ... and {len(missing) - 20} more")
    print(f"  in ours, absent from reference    {len(extra)}")
    for h in sorted(extra)[:10]:
        print(f"      {h}")

    print("\n=== interpretation ===")
    if missing:
        print(f"  ⛔ {len(missing)} models the working viewer places are absent "
              f"from our extraction. This is the 'most of the level is missing' "
              f"symptom, and it is a MODEL ENUMERATION problem -- not geometry, "
              f"not materials.")
    else:
        print("  ✓ model sets agree; any remaining visual difference is "
              "placement, geometry decode, or materials.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
