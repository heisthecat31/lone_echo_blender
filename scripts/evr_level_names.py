"""Recover human-readable LEVEL names for a flat extract, and cache them as JSON.

## Why this works at all

A resource hash IS the CSymbol64 of its authored name -- verified, not assumed::

    CSymbol64("mpl_arena_a")  == 576ed3f8428ebc4b
    CSymbol64("mpl_lobby_b2") == d09afd15b1c75c04

So naming a level is a preimage problem, and the preimages are sitting in the
shipped binaries: the game executable and its script DLLs reference levels by
name, and those strings survive in the .text/.rdata sections.

## Method

1. Enumerate levels as `CActorDataResourceWin10` ∩ `CGSceneResourceWin10` --
   a level has both; a prop or sub-scene does not.
2. Harvest every ASCII run from the executable and script DLLs.
3. Expand each string at `_` boundaries. Level names are frequently PREFIXES of
   longer identifiers (`gpr_010_hba_010_intro` appears inside dialogue and POI
   symbols), so prefix expansion recovers far more than literal matches: on
   Lone Echo 2 it took the hit rate from 1 to 77.
4. Hash every candidate and keep the ones that land on a real level hash.
5. Merge a supplied dictionary (`quest_combat_port/data/hash_lookup.json`,
   13009 entries) which already names most of Echo VR.

## Coverage, honestly

    Echo VR       31 / 34 levels
    Lone Echo 2   77 / 302 levels

The rest are genuinely not present as strings in the shipped binaries -- they
are hashed at build time and the preimage is gone. Combinatorial generation
over the observed vocabulary (1.6M candidates from real zone codes, chapter
numbers and tails) recovered **zero** additional names, so this is not a matter
of trying harder with the same material; it needs a new source (a symbol dump,
a PDB, or authoring data).

Unnamed levels are written as `null` rather than omitted, so the file states
what is unknown instead of hiding it.

    python scripts/evr_level_names.py --dir H:/pcvr-extracted \
        --binaries "C:/.../ready-at-dawn-echo-arena/bin/win10" \
        --out data/level_names_echovr.json
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

from evr_resource_types import ACTOR_DATA, SCENE_RESOURCE, normalise_hash

#: `quest_combat_port`'s CSymbol64 dictionary, when present.
DEFAULT_DICTIONARY = Path(
    r"J:\EchoVR-Tools-Launcher\quest_combat_port\data\hash_lookup.json")

_ASCII = re.compile(rb"[A-Za-z0-9_./\-]{4,90}")
_MASK = (1 << 64) - 1


def _hash(name: str) -> str:
    from le_mesh.material_scalars import symbol64
    return normalise_hash(symbol64(name) & _MASK)


def levels_in(root: Path) -> set:
    """Hashes that are LEVELS: actor data AND a scene resource."""
    actors = {p.name for p in (root / ACTOR_DATA).iterdir()} \
        if (root / ACTOR_DATA).is_dir() else set()
    scenes = {p.name for p in (root / SCENE_RESOURCE).iterdir()} \
        if (root / SCENE_RESOURCE).is_dir() else set()
    return {normalise_hash(h) for h in (actors & scenes)}


def harvest_strings(paths) -> set:
    """Every ASCII run in the given binaries."""
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
    """Candidate names: each string plus its `_`-delimited prefixes.

    The prefix step is what makes this effective -- a level name usually appears
    only INSIDE a longer symbol, never on its own.
    """
    out: set = set()
    for text in strings:
        out.add(text)
        if "/" in text or "." in text:
            out.add(text.split("/")[-1].split(".")[0])
        parts = text.split("_")
        for i in range(2, len(parts)):
            out.add("_".join(parts[:i]))
    return out


def resolve(root: Path, binaries, dictionary: Path | None) -> dict:
    """`{level_hash: name or None}` for every level in the extract."""
    levels = levels_in(root)
    names: dict = {h: None for h in levels}

    if dictionary and Path(dictionary).exists():
        raw = json.loads(Path(dictionary).read_text(encoding="utf-8"))
        for key, value in raw.items():
            if not isinstance(value, str):
                continue
            for candidate in (_hash(value), normalise_hash(key.replace("0x", ""))):
                if candidate in names and names[candidate] is None:
                    names[candidate] = value

    files = []
    for entry in binaries or ():
        entry = Path(entry)
        if entry.is_dir():
            files.extend(sorted(entry.glob("*.exe")))
            files.extend(sorted(entry.glob("*.dll")))
            files.extend(sorted((entry / "scripts").glob("*.dll")))
        elif entry.is_file():
            files.append(entry)

    if files:
        harvested = harvest_strings(files)
        for candidate in expand(harvested):
            h = _hash(candidate)
            if h in names and names[h] is None:
                names[h] = candidate

        # Targeted generation for the `zon_<zone>_<word>` form.
        #
        # Zone resources hold a chapter's GEOMETRY while the `gpr_*` sublevels
        # hold only its actors, so these are the names worth having -- and they
        # rarely appear as literal strings. Recombining the real zone codes with
        # the binaries' own vocabulary recovered 16 that prefix expansion alone
        # missed (`zon_ps4_hive`, `zon_cns_bootup`, ...).
        #
        # NOTE this is the ONLY generation that pays: recombining chapter
        # numbers and tails into `gpr_*` forms produced 1.6M candidates and zero
        # hits, so it is deliberately not attempted.
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
        # Multi-word tails as they ACTUALLY occur, not a cross product: take
        # every 1..4-word window out of the harvested symbols. This is what
        # recovers names like `zon_fhb_spp_cargo_security_unpowered`, which no
        # single-word generation could reach.
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

        def offer(candidate):
            h = _hash(candidate)
            if h in names and names[h] is None:
                names[h] = candidate

        for zone in codes:
            offer(f"zon_{zone}")
            for word in vocabulary:
                offer(f"zon_{zone}_{word}")
                offer(f"zon_{word}_{zone}")
            for tail in tails:
                offer(f"zon_{zone}_{tail}")
            for chapter in chapters:
                offer(f"gpr_{chapter}_{zone}")
                for tail in tails:
                    offer(f"gpr_{chapter}_{zone}_{tail}")

        # TWO-LEVEL zones: `zon_<hub>_<zone>_<tail>`. Lone Echo 2 nests a hub
        # code before the area code -- `zon_fhb_hba_habitat_a` is the geometry
        # behind every `gpr_010_hba_*` sublevel. Missing this pattern is why the
        # habitat zone stayed unnamed through two earlier passes.
        for hub in codes:
            for zone in codes:
                offer(f"zon_{hub}_{zone}")
                for tail in tails:
                    offer(f"zon_{hub}_{zone}_{tail}")
    return names


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="flat extract root")
    ap.add_argument("--binaries", nargs="*", default=[],
                    help="game exe / script dirs to mine for name strings")
    ap.add_argument("--dictionary", default=str(DEFAULT_DICTIONARY))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    names = resolve(Path(args.dir), args.binaries, Path(args.dictionary))
    known = sum(1 for v in names.values() if v)
    payload = {
        "levels": dict(sorted(names.items(),
                              key=lambda kv: (kv[1] is None, kv[1] or kv[0]))),
        "_note": (f"{known} of {len(names)} levels named. A hash is the CSymbol64 "
                  f"of the authored name; unnamed levels are ones whose preimage "
                  f"does not appear in the shipped binaries. null means unknown, "
                  f"not absent."),
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(f"{known} / {len(names)} levels named -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
