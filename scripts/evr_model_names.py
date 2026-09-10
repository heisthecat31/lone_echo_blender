"""Echo VR cosmetic MODEL names -- chassis, bracers and boosters.

## The short version

Every model in an Echo VR extract is addressed by a bare `CSymbol64`, and the
authored strings are not in the archives.  They ARE in the game install, in a
file nothing had read:

    <install>/sourcedb/rad15/json/r14/multiplayer/customization_models.json

521 distinct authored model names over five slots (`backpacks`,
`bodies.firstperson`, `bodies.thirdperson`, `bracers.left`, `bracers.right`).
Hash each one and **249** land on a resource that ships in the PC extract -- a
verified preimage every time, not a label invented here.  The rest are
Quest-only builds (`_quest_rig`) and unshipped variants.

The grammar those names follow is regular enough to generate from:

    [pty_]cst_(pack|body|bracer)_<theme>[_a][_rift|_quest|_fp][_<variant>]
                                        [_left|_right][_rig]

Generating it over the themes and variants the file itself uses -- plus the
themes only the catalogue knows -- and keeping only candidates whose hash is a
resource on disk adds **114 more** names the file does not list: the plain
`_rift` bracer/pack forms, and the `s11_retro` / `s11_flamey` season kit.
**363 names total.**  Nothing here is guessed: a name is kept only when
`symbol64(name)` equals a hash that exists.

## What it is bound to

`43934c379cf1e366` (type `32f30fe361939dee`) is the cosmetics catalogue: 713
records of 664 bytes.  Relative to a record's `rwd_*` id string,

| offset | field |
|---|---|
| +0    | the item id (`rwd_chassis_trex_s1_a`) |
| +128  | display label (`Meteor Rex`) |
| +192  | description |
| +88   | the item's icon texture |
| +528..+624 | the MODEL slots -- up to 12, and which ones are filled says what kind of item it is |

⚠ The record does not START at the id.  An earlier reading anchored records at
`id - 96` and got a self-consistent-looking table in which every item's model
was also the *next* item's model, because a 664-byte window anchored there
straddles two records and reads the same block at two offsets.  Anchoring on
the id string and taking the model block at +528..+624 makes each item's models
its own -- and the slot then means something stable:

| slot | chassis | bracer | booster |
|---|---|---|---|
| +536 | | `_fp_right` | |
| +544 | `_a_fp` | `_rift_right` | |
| +560 | | `_fp_left` | |
| +568 | | `_rift_left` | |
| +584 | | `_right` | |
| +592 | `_a` | `_rift_right` | `cst_pack_*_a_rift` |
| +608 | | `_left` | |
| +616 | | `_rift_left` | |

## Coverage

| | items | models | named |
|---|---|---|---|
| chassis | 32 | 64 | **64** |
| bracer | 56 | 116 | 110 |
| booster | 60 | 60 | 57 |

The nine that are missing are three themes -- `aurum`, `bee` (Bumblebee) and
`arcade_var` (Golden Age) -- whose models are not in `customization_models.json`
and do not fall out of the grammar under any spelling tried, including a
free-tail search over the whole authored vocabulary of the shipped binaries.
They are almost certainly named off a word that appears nowhere on this disk.

    python scripts/evr_model_names.py --dir <extract> [--install <game dir>]
    python scripts/evr_model_names.py --dir <extract> --write
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evr_paths                                             # noqa: E402
from le_symbol_names import symbol64                         # noqa: E402

#: The cosmetics catalogue: type dir, resource, record stride.
COSMETICS_TYPE = "32f30fe361939dee"
COSMETICS_RESOURCE = "43934c379cf1e366"
RECORD_STRIDE = 664
#: Field offsets RELATIVE TO THE ID STRING -- see the module docstring for why
#: the record start is not a safe anchor.
F_LABEL = 128
F_DESC = 192
F_ICON = 88
MODEL_SLOTS = range(528, 632, 8)

#: `CGMeshListResourceWin10` -- what a cosmetic model actually is.
MESHLIST_TYPE = "4e426f88c1b5d7ac"

#: Where the authored names live inside a game install.
MODELS_JSON = Path("sourcedb/rad15/json/r14/multiplayer/customization_models.json")

#: The name grammar, as observed across all 524 authored names.
KINDS = ("cst_pack_", "cst_body_", "cst_bracer_",
         "pty_cst_pack_", "pty_cst_bracer_")
PLATFORMS = ("", "_rift", "_quest", "_fp")
SIDES = ("", "_left", "_right")
RIGS = ("", "_rig")
LETTERS = ("_a", "")

CATEGORIES = ("chassis", "bracer", "booster")


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------
def find_install(explicit=None) -> Path | None:
    """The Echo VR install, or None.  Discovery only -- never required."""
    if explicit:
        p = Path(explicit).expanduser()
        return p if (p / MODELS_JSON).is_file() else None
    tail = Path("Software/Software/ready-at-dawn-echo-arena")
    for drive in ("C:/", "D:/", "E:/", "F:/", "G:/", "H:/", "J:/"):
        base = Path(drive) / "Oculus/Games" / tail
        if (base / MODELS_JSON).is_file():
            return base
    return None


def authored_names(install: Path) -> dict:
    """`{name: slot}` from `customization_models.json`."""
    data = json.loads((install / MODELS_JSON).read_text(encoding="utf-8"))
    out = {}
    for slot, value in data.items():
        if isinstance(value, list):
            for name in value:
                out.setdefault(name, slot)
        elif isinstance(value, dict):
            for sub, names in value.items():
                for name in names:
                    out.setdefault(name, f"{slot}.{sub}")
    out.pop("none", None)
    return out


def resource_hashes(root: Path, type_dir: str | None = None) -> set:
    """Every resource hash on disk, or just one type's."""
    out = set()
    dirs = [root / type_dir] if type_dir else [d for d in root.iterdir() if d.is_dir()]
    for d in dirs:
        if not d.is_dir():
            continue
        for f in d.iterdir():
            try:
                out.add("%016x" % int(f.stem if f.suffix else f.name, 16))
            except ValueError:
                pass
    return out


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
_SUFFIX = re.compile(r"(_rig|_fp|_left|_right|_rift|_quest)+$")


#: Season tokens in an item id that the MODEL name drops:
#: `rwd_booster_trex_s1_a` wears `cst_pack_trex_a_rift`.
_SEASON = re.compile(r"s\d$")


def vocabulary(names, items=()) -> tuple:
    """`(themes, variants)` -- everything the grammar can be spelled from.

    A theme is what sits between the kind prefix and the `_a`; a variant is
    the WHOLE remainder after it, platform and side included (`_rift_daydream`,
    `_quest_left_hydro`). Keeping the remainder whole rather than splitting it
    into orthogonal axes is what reaches `cst_bracer_fume_a_rift_daydream`,
    where the variant word sits between the platform and the side.

    `items` are the catalogue rows, and they matter: `s11_retro` and
    `s11_flamey` ship no entry in `customization_models.json`, so their themes
    exist only in the item id (`rwd_chassis_s11_retro_a`) and in the display
    label ("S11 Flamey" -> `s11_flamey`, which is not the id's word `fire`).
    """
    themes, variants = set(), set()
    for name in names:
        m = re.match(r"(?:pty_)?cst_(?:pack|body|bracer)_(.+)$", name)
        if not m:
            continue
        core = m.group(1)
        parts = core.split("_")
        if "a" in parts:
            i = parts.index("a")
            themes.add("_".join(parts[:i]))
            if parts[i + 1:]:
                variants.add("_".join(parts[i + 1:]))
        else:
            themes.add(_SUFFIX.sub("", core))
    for row in items:
        parts = row["id"].split("_")
        if len(parts) < 3 or parts[1] not in CATEGORIES:
            continue
        core = [p for p in parts[2:] if not _SEASON.match(p) and p not in ("a", "b")]
        for n in range(1, len(core) + 1):
            themes.add("_".join(core[:n]))
        label = re.sub(r"[^a-z0-9]+", "_", (row.get("label") or "").lower()).strip("_")
        if label:
            themes.add(label)
    themes.discard("")
    return themes, variants


def generate(themes, variants):
    """Every name the grammar can spell over this vocabulary."""
    var = [""] + ["_" + v for v in sorted(variants)]
    for kind in KINDS:
        for theme in sorted(themes):
            for letter in LETTERS:
                stem = kind + theme + letter
                for plat in PLATFORMS:
                    for v in var:
                        for side in SIDES:
                            for rig in RIGS:
                                yield stem + plat + v + side + rig
                                if side:
                                    yield stem + plat + side + v + rig


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------
def _cstr(blob: bytes, off: int, cap: int = 80) -> str:
    end = blob.find(b"\0", off, off + cap)
    return blob[off:end if end >= 0 else off + cap].decode("ascii", "replace")


def catalogue(root: Path, meshlist: set) -> list:
    """`[{id, label, desc, models: [(slot, hash)]}, ...]`, one per item."""
    path = root / COSMETICS_TYPE / COSMETICS_RESOURCE
    if not path.is_file():
        return []
    blob = path.read_bytes()
    rows, seen = [], set()
    for m in re.finditer(rb"rwd_[a-z0-9_]{2,60}\x00", blob):
        ident = m.group().decode().rstrip("\0")
        if ident in seen:
            continue
        seen.add(ident)
        off = m.start()
        models = []
        for slot in MODEL_SLOTS:
            if off + slot + 8 > len(blob):
                break
            value = "%016x" % struct.unpack_from("<Q", blob, off + slot)[0]
            if value in meshlist:
                models.append((slot, value))
        rows.append({"id": ident, "label": _cstr(blob, off + F_LABEL),
                     "description": _cstr(blob, off + F_DESC),
                     "icon": "%016x" % struct.unpack_from("<Q", blob, off + F_ICON)[0],
                     "models": models})
    return rows


# ---------------------------------------------------------------------------
def build(root: Path, install: Path | None) -> dict:
    every = resource_hashes(root)
    meshlist = resource_hashes(root, MESHLIST_TYPE)
    names, sources = {}, {}

    authored = authored_names(install) if install else {}
    for name, slot in authored.items():
        h = symbol64(name)
        if h in every:
            names[h] = name
            sources[h] = "customization_models.json:" + slot

    rows = catalogue(root, meshlist)
    themes, variants = vocabulary(authored or list(names.values()), rows)
    generated = 0
    for name in generate(themes, variants):
        h = symbol64(name)
        if h in every and h not in names:
            names[h] = name
            sources[h] = "generated"
            generated += 1

    bound = {h for r in rows for _, h in r["models"]}
    return {
        "names": names, "sources": sources, "items": rows,
        "stats": {
            "authored_in_json": len(authored),
            "authored_present": sum(1 for v in sources.values()
                                    if v.startswith("customization")),
            "generated_present": generated,
            "named_total": len(names),
            "meshlist_resources": len(meshlist),
            "meshlist_named": len(meshlist & set(names)),
            "catalogue_items": len(rows),
            "catalogue_models": len(bound),
            "catalogue_models_named": len(bound & set(names)),
        },
    }


def report(result: dict) -> None:
    s = result["stats"]
    for k, v in s.items():
        print("  %-24s %s" % (k, v))
    print()
    rows, names = result["items"], result["names"]
    for cat in CATEGORIES:
        sel = [r for r in rows if r["id"].split("_")[1:2] == [cat]]
        models = {h for r in sel for _, h in r["models"]}
        named = models & set(names)
        print("  %-8s items=%-4d models=%-5d named=%-5d unnamed=%d"
              % (cat, len(sel), len(models), len(named), len(models) - len(named)))
    missing = sorted({h for r in rows for _, h in r["models"]
                      if r["id"].split("_")[1:2] and r["id"].split("_")[1] in CATEGORIES
                      and h not in names})
    if missing:
        print("\n  unnamed cosmetic models (%d):" % len(missing))
        for h in missing:
            who = sorted({r["id"] for r in rows for _, x in r["models"] if x == h})
            print("    %s  %s" % (h, ", ".join(who)))


def write(result: dict, path: Path) -> None:
    payload = {
        "format": "evr_model_names",
        "version": 1,
        "_note": (
            "CSymbol64 -> authored Echo VR MODEL name. Every entry is a "
            "VERIFIED PREIMAGE: symbol64(name) == the hash, checked at build "
            "time. Two sources, both exact. (1) The game install ships the "
            "authored strings in sourcedb/rad15/json/r14/multiplayer/"
            "customization_models.json -- 524 names over backpacks / bodies "
            "(first- and third-person) / bracers (left and right); 249 of them "
            "are resources in the PC extract and the remainder are Quest-only "
            "builds. (2) Those names follow a regular grammar -- "
            "[pty_]cst_(pack|body|bracer)_<theme>[_a][_rift|_quest|_fp]"
            "[_<variant>][_left|_right][_rig] -- and generating it over the "
            "themes and variants the file itself uses, then keeping only "
            "candidates whose hash is a resource on disk, adds 114 more: the "
            "plain _rift forms and the s11_retro / s11_flamey season kit. "
            "`items` is the binding read out of the cosmetics catalogue "
            "43934c379cf1e366 -- which model each rwd_* item wears, and in "
            "which slot. ANCHOR ON THE ID STRING, not on a record start: a "
            "664-byte window anchored at id-96 straddles two records and makes "
            "every item appear to share the next item's model. NOT NAMED: nine "
            "models across three themes (aurum, bee/Bumblebee, arcade_var/"
            "Golden Age) that are absent from customization_models.json and do "
            "not fall out of the grammar under any spelling tried, including a "
            "free-tail search over the authored vocabulary of every shipped "
            "binary."),
        "stats": result["stats"],
        "names": dict(sorted(result["names"].items())),
        "sources": dict(sorted(result["sources"].items())),
        "items": result["items"],
    }
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print("wrote %s (%d names, %d items)"
          % (path, len(payload["names"]), len(payload["items"])))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=None,
                    help="flat game extract (or set EVR_EXTRACT_DIR)")
    ap.add_argument("--install", default=None,
                    help="Echo VR install holding sourcedb/ (default: discover)")
    ap.add_argument("--write", action="store_true",
                    help="write data/model_names_echovr.json")
    ap.add_argument("--out", default=None, help="override the output path")
    args = ap.parse_args(argv)

    root = evr_paths.extract_dir(args.dir)
    if root is None:
        ap.error("no game extract given: pass --dir <path> or set EVR_EXTRACT_DIR")
    install = find_install(args.install)
    if install is None:
        print("no game install found -- only the grammar pass will run; pass "
              "--install <dir> for the authored names")
    else:
        print("install: %s" % install)
    result = build(root, install)
    report(result)
    if args.write:
        out = Path(args.out) if args.out else evr_paths.DATA / "model_names_echovr.json"
        write(result, out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
