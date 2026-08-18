"""Universal level-name cracker for Echo VR, Lone Echo 2, and variant builds.

Resolves the CSymbol64 preimage for every level hash in a flat extract by
combining every available name source:

  1. **Dictionary merge** — merges all known-name JSON files AND the
     quest_combat_port hash_lookup (13k entries) into a single lookup.
  2. **Binary harvesting** — ASCII runs from executables/DLLs, expanded at
     ``_`` boundaries (the same technique as ``evr_level_names.py``).
  3. **Suffix generation** — appends ``_lowspec``, ``_highspec``, ``_quest``
     etc. to every *already-resolved* name and hashes the result.  This is
     the strategy that cracked Summer2's 16 lowspec variants — the original
     ``evr_level_names.py`` has no equivalent.
  4. **Zone recombination** — ``zon_<hub>_<zone>_<tail>`` generation carried
     over from ``evr_level_names.py`` for Lone Echo 2.

Game-agnostic: works on any extract without knowing which title it came from.

    python scripts/evr_name_crack.py --dir J:\\Summer2 --out data/level_names_summer2.json
    python scripts/evr_name_crack.py --dir <extract> --out data/level_names_echovr.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evr_resource_types import (ACTOR_DATA, SCENE_RESOURCE, normalise_hash)

# Win7 resource type hashes for older builds (e.g. Summer lobby build).
# CSymbol64("CActorDataResourceWin7") and CSymbol64("CGSceneResourceWin7").
_ACTOR_DATA_WIN7 = "c165fbf2e77f973d"
_SCENE_RESOURCE_WIN7 = "86f4cd162e7da857"

_RESOURCE_PAIRS = [
    (ACTOR_DATA, SCENE_RESOURCE),
    (_ACTOR_DATA_WIN7, _SCENE_RESOURCE_WIN7),
]

_ASCII = re.compile(rb"[A-Za-z0-9_./\-]{4,90}")
_MASK = (1 << 64) - 1

# ── Known dictionaries ──────────────────────────────────────────────────
#
# Merged in order; first hit wins so game-specific files take precedence over
# the big hash_lookup which occasionally has shortened names.

_DATA = _ROOT / "data"

#: Every known-name JSON file shipped with this repo.
_GAME_NAME_FILES = [
    _DATA / "level_names_echovr.json",
    _DATA / "level_names_loneecho2.json",
    _DATA / "level_names_loneecho1.json",
    _DATA / "level_names_summer2.json",
    _DATA / "level_names.json",
]

#: The quest_combat_port CSymbol64 dictionary, when present.
_HASH_LOOKUP = Path(
    r"J:\EchoVR-Tools-Launcher\quest_combat_port\data\hash_lookup.json")

#: Suffixes that variant builds append to the base level name.  The Summer
#: build ships every original level plus a ``_lowspec`` twin; future builds may
#: use ``_highspec`` or ``_quest``.  This list is deliberately kept short —
#: each suffix multiplies the candidate count by the number of already-resolved
#: names, so adding junk here costs time without benefit.
VARIANT_SUFFIXES = (
    "_lowspec",
    "_highspec",
    "_quest",
    "_mobile",
    "_lite",
)


# ── CSymbol64 ───────────────────────────────────────────────────────────

def _hash(name: str) -> str:
    from le_mesh.material_scalars import symbol64
    return normalise_hash(symbol64(name) & _MASK)


# ── Level enumeration ───────────────────────────────────────────────────

def levels_in(root: Path) -> set:
    """Hashes that are LEVELS: actor data AND a scene resource.

    Checks both Win10 and Win7 resource type directories so older builds
    (e.g. the Summer lobby build) are handled automatically.
    """
    actors: set = set()
    scenes: set = set()
    for actor_hash, scene_hash in _RESOURCE_PAIRS:
        actor_dir = root / actor_hash
        scene_dir = root / scene_hash
        if actor_dir.is_dir():
            actors |= {p.name for p in actor_dir.iterdir()}
        if scene_dir.is_dir():
            scenes |= {p.name for p in scene_dir.iterdir()}
    return {normalise_hash(h) for h in (actors & scenes)}


# ── Dictionary loading ──────────────────────────────────────────────────

def _load_game_names() -> dict:
    """Merge all game-specific name JSONs into {hash: name}."""
    merged: dict = {}
    for path in _GAME_NAME_FILES:
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # Handle both {"levels": {...}} and {"games": {"x": {"levels": {...}}}}
        if "levels" in raw:
            for k, v in raw["levels"].items():
                if v and normalise_hash(k) not in merged:
                    merged[normalise_hash(k)] = v
        if "games" in raw:
            for game in raw["games"].values():
                for k, v in (game.get("levels") or {}).items():
                    if v and normalise_hash(k) not in merged:
                        merged[normalise_hash(k)] = v
    return merged


def _load_hash_lookup(path: Path | None = None) -> dict:
    """Load the quest_combat_port hash_lookup dictionary."""
    p = Path(path) if path else _HASH_LOOKUP
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            continue
        # The lookup keys can be "0x1234" or plain hex.
        h = normalise_hash(key.replace("0x", ""))
        if h:
            out[h] = value
        # Also hash the value to catch names stored as values.
        h2 = _hash(value)
        if h2:
            out.setdefault(h2, value)
    return out


# ── Binary string harvesting ────────────────────────────────────────────

def harvest_strings(paths) -> set:
    """Every ASCII run ≥ 4 chars from the given binaries."""
    found: set = set()
    for path in paths:
        try:
            blob = Path(path).read_bytes()
        except OSError:
            continue
        for match in _ASCII.findall(blob):
            found.add(match.decode("ascii", "ignore"))
    return found


def expand(strings) -> set:
    """Candidate names: each string plus its ``_``-delimited prefixes."""
    out: set = set()
    for text in strings:
        out.add(text)
        if "/" in text or "." in text:
            out.add(text.split("/")[-1].split(".")[0])
        parts = text.split("_")
        for i in range(2, len(parts)):
            out.add("_".join(parts[:i]))
    return out


# ── Zone recombination (Lone Echo 2 strategy) ───────────────────────────

def _zone_candidates(names: dict, harvested: set) -> set:
    """Generate ``zon_<zone>_<word>`` and ``gpr_<chapter>_<zone>_<tail>``."""
    vocabulary = set()
    for text in harvested:
        for word in text.split("_"):
            if 2 <= len(word) <= 18 and re.fullmatch(r"[a-z0-9]+", word):
                vocabulary.add(word)

    zone_codes = {name.split("_")[1]
                  for name in names.values()
                  if name and name.startswith(("zon_", "gpr_")) and "_" in name[4:]}
    zone_codes |= {name.split("_")[2]
                   for name in names.values()
                   if name and name.startswith("gpr_") and name.count("_") >= 3}

    tails = set()
    for text in harvested:
        parts = text.split("_")
        for i in range(len(parts)):
            for width in (1, 2, 3, 4):
                if i + width <= len(parts):
                    tail = "_".join(parts[i:i + width])
                    if re.fullmatch(r"[a-z0-9_]{3,45}", tail):
                        tails.add(tail)

    codes = {z for z in zone_codes if 2 <= len(z) <= 4}
    chapters = {name.split("_")[1] for name in names.values()
                if name and name.startswith("gpr_")}

    candidates: set = set()
    for zone in codes:
        candidates.add(f"zon_{zone}")
        for word in vocabulary:
            candidates.add(f"zon_{zone}_{word}")
            candidates.add(f"zon_{word}_{zone}")
        for tail in tails:
            candidates.add(f"zon_{zone}_{tail}")
        for chapter in chapters:
            candidates.add(f"gpr_{chapter}_{zone}")
            for tail in tails:
                candidates.add(f"gpr_{chapter}_{zone}_{tail}")
    # Two-level zones: zon_<hub>_<zone>_<tail>.
    for hub in codes:
        for zone in codes:
            candidates.add(f"zon_{hub}_{zone}")
            for tail in tails:
                candidates.add(f"zon_{hub}_{zone}_{tail}")
    return candidates


# ── Suffix generation (the new trick) ───────────────────────────────────

def _suffix_candidates(names: dict) -> set:
    """Append variant suffixes to every already-resolved name.

    This is what cracked Summer2's 16 ``_lowspec`` levels that no binary
    string harvesting could reach — the preimage is not present in any
    shipped binary; it is a build-time concatenation of a known base name
    and a variant tag.
    """
    out: set = set()
    for name in names.values():
        if not name:
            continue
        for suffix in VARIANT_SUFFIXES:
            out.add(name + suffix)
    return out


# ── Main resolver ───────────────────────────────────────────────────────

def resolve(root: Path, binaries=None, dictionary: Path | None = None) -> dict:
    """`{level_hash: name or None}` for every level in the extract.

    Runs every available strategy in order.  Each successive pass only
    targets hashes that are still unnamed, so a dictionary hit is never
    overwritten by a weaker source.
    """
    levels = levels_in(root)
    names: dict = {h: None for h in levels}

    def offer(candidate: str) -> None:
        h = _hash(candidate)
        if h in names and names[h] is None:
            names[h] = candidate

    # Pass 1: game-specific name JSONs
    for h, name in _load_game_names().items():
        if h in names and names[h] is None:
            names[h] = name

    # Pass 2: quest_combat_port hash_lookup
    for h, name in _load_hash_lookup(dictionary).items():
        if h in names and names[h] is None:
            names[h] = name

    # Pass 3: suffix generation on everything resolved so far
    for candidate in _suffix_candidates(names):
        offer(candidate)

    # Pass 4: binary harvesting + expansion
    files = []
    # Always check the extract root for executables (Summer2 has them there).
    for ext in ("*.exe", "*.dll"):
        files.extend(sorted(root.glob(ext)))
    for entry in binaries or ():
        entry = Path(entry)
        if entry.is_dir():
            files.extend(sorted(entry.glob("*.exe")))
            files.extend(sorted(entry.glob("*.dll")))
            files.extend(sorted((entry / "scripts").glob("*.dll")))
        elif entry.is_file():
            files.append(entry)

    harvested: set = set()
    if files:
        harvested = harvest_strings(files)
        for candidate in expand(harvested):
            offer(candidate)

    # Pass 5: suffix generation again (now including binary-harvested names)
    for candidate in _suffix_candidates(names):
        offer(candidate)

    # Pass 6: zone recombination (effective for Lone Echo 2)
    if harvested:
        for candidate in _zone_candidates(names, harvested):
            offer(candidate)

    return names


def resolve_quick(level_hashes: set, existing_names: dict | None = None) -> dict:
    """Fast resolution using dictionaries + suffix generation only (no binary).

    Designed for inline use in the app's ``discover_levels()`` — runs in
    milliseconds, not seconds.

    Parameters
    ----------
    level_hashes : set
        The 16-hex-digit hashes to resolve.
    existing_names : dict, optional
        Already-known ``{hash: name}`` mappings (merged into the search).

    Returns
    -------
    dict
        ``{hash: name}`` for every hash that was cracked.  Hashes that
        remain unknown are not included.
    """
    targets = {normalise_hash(h) for h in level_hashes}
    names: dict = {}

    # Seed with existing names (so suffix generation has material).
    if existing_names:
        names.update({normalise_hash(k): v for k, v in existing_names.items()
                      if v is not None})

    cracked: dict = {}

    def offer(candidate: str) -> None:
        h = _hash(candidate)
        if h in targets and h not in cracked:
            cracked[h] = candidate
            names[h] = candidate  # feed back into suffix generation

    # Dictionary sources
    for h, name in _load_game_names().items():
        if h in targets:
            cracked.setdefault(h, name)
            names[h] = name
    for h, name in _load_hash_lookup().items():
        if h in targets:
            cracked.setdefault(h, name)
            names[h] = name

    # Suffix generation
    for candidate in _suffix_candidates(names):
        offer(candidate)

    return cracked


# ── CLI ─────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="flat extract root")
    ap.add_argument("--binaries", nargs="*", default=[],
                    help="game exe / script dirs to mine for name strings")
    ap.add_argument("--dictionary", default=None,
                    help="path to quest_combat_port hash_lookup.json")
    ap.add_argument("--out", required=True, help="output JSON path")
    args = ap.parse_args()

    root = Path(args.dir)
    dictionary = Path(args.dictionary) if args.dictionary else None
    names = resolve(root, args.binaries, dictionary)

    known = sum(1 for v in names.values() if v)
    payload = {
        "levels": dict(sorted(names.items(),
                               key=lambda kv: (kv[1] is None, kv[1] or kv[0]))),
        "_note": (f"{known} of {len(names)} levels named. A hash is the CSymbol64 "
                  f"of the authored name; unnamed levels are ones whose preimage "
                  f"was not recovered. null means unknown, not absent."),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"{known} / {len(names)} levels named -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
