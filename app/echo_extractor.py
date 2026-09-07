"""Echo Extractor -- a desktop front end for the Lone Echo / Echo VR pipeline.

One window that takes someone from "I own the game" to "it is open in Blender":

    addon check -> pick a game -> point at (or produce) an extract
      -> choose scenes / models -> texture size -> extract

Everything it drives already exists as a script in this repo; this is the layer
that makes the pipeline usable without a command line.

Levels are shown GROUPED. A map like `mpl_combat_fission` ships as a parent plus
four sublevels, and the interesting artefact is nearly always the whole set
merged into one package -- so a group is one click, and its members are
individually clickable underneath for when you want just one. Selecting a whole
group extracts it merged (`--full`); selecting members extracts them separately.

Run it with any Python 3.9+ (tkinter only, no third-party packages):

    python app/echo_extractor.py
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_ROOT = Path(__file__).resolve().parent
REPO = APP_ROOT.parent
SCRIPTS = REPO / "scripts"
DATA = REPO / "data"
ADDON_SRC = REPO / "blender_tool" / "addon" / "lone_echo_import"

# Resource-type directories that identify an extract: CSymbol64s of the
# engine's own type names, so nothing else produces this pair.
#
# Older builds (e.g. the Summer lobby build) use Win7 resource types whose
# hashes differ from the Win10 ones the later builds shipped.  Both pairs are
# checked so the app works on any extract regardless of era.
ACTOR_DATA_WIN10 = "347869ce492dc7da"     # CActorDataResourceWin10
SCENE_RESOURCE_WIN10 = "a388ea69e5108f4c" # CGSceneResourceWin10
ACTOR_DATA_WIN7 = "c165fbf2e77f973d"      # CActorDataResourceWin7
SCENE_RESOURCE_WIN7 = "86f4cd162e7da857"  # CGSceneResourceWin7

# Back-compat aliases used by the rest of the codebase (always the Win10 pair).
ACTOR_DATA = ACTOR_DATA_WIN10
SCENE_RESOURCE = SCENE_RESOURCE_WIN10

# All (actor, scene) pairs to try, in order of preference.
_RESOURCE_PAIRS = [
    (ACTOR_DATA_WIN10, SCENE_RESOURCE_WIN10),
    (ACTOR_DATA_WIN7, SCENE_RESOURCE_WIN7),
]


def _find_level_dirs(root: Path):
    """Return `(actors_dir, scenes_dir)` for whichever format exists, or `(None, None)`."""
    for actor_hash, scene_hash in _RESOURCE_PAIRS:
        actors, scenes = root / actor_hash, root / scene_hash
        if actors.is_dir() and scenes.is_dir():
            return actors, scenes
    return None, None

#: The app keeps its own copy of the external extractors, so a working install
#: is self-contained and does not depend on where they happened to be cloned.
TOOLS_DIR = APP_ROOT / "extract"
EVRTOOLS_HOME = TOOLS_DIR / "evrFileTools"
PYOODLE_HOME = TOOLS_DIR / "pyoodle"

#: Where to look for a tool that is not bundled yet, in order. First hit is
#: copied into `app/extract/`; after that the bundled copy is always used.
TOOL_SOURCES = {
    "evrFileTools": [
        Path(r"C:\Users\lucas\Desktop\FreshEVR\evrFileTools"),
        Path(r"C:\Users\lucas\Desktop\evrFileTools"),
    ],
    "pyoodle": [
        Path(r"J:\EchoVR-Tools-Launcher\EchoVR-Cosmetics-Editor\pyoodle-main"),
        Path(r"C:\Users\lucas\Desktop\pyoodle-main"),
    ],
}

#: Remembered paths and options, so nothing has to be re-typed between runs.
SETTINGS_FILE = APP_ROOT / "settings.json"

#: Lone Echo 1's archive classification, built once per install by
#: `scripts/le_scene_index.py`. Machine-derived (it describes YOUR game folder),
#: so it sits beside settings.json rather than in the repo's data/.
LE1_INDEX_FILE = APP_ROOT / "le1_scene_index.json"


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_settings(data) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def evrtools_exe() -> Path:
    return EVRTOOLS_HOME / "cmd" / "evrtools" / "evrtools.exe"


def find_data_dir(game_root):
    """The platform data directory inside a game install, or None.

    All three titles put it under `_data/<build>/` -- Echo VR and Lone Echo 2
    behind a `radNN` folder (`rad15/win10`, `rad16/win10`), Lone Echo 1 directly
    (`win7`). So the user picks the GAME folder and this finds the rest; asking
    them to navigate five levels down to a numbered build directory is exactly
    the kind of step that gets it wrong.

    Accepts being handed the data directory itself, so a saved path still works.
    """
    root = Path(game_root)
    if not root.is_dir():
        return None
    if root.name.lower() in ("win10", "win7"):
        return root
    data = root / "_data"
    if not data.is_dir():
        data = root
    best = None
    for build in sorted(data.iterdir()) if data.is_dir() else []:
        if not build.is_dir():
            continue
        for candidate in (build, *sorted(p for p in build.iterdir()
                                         if p.is_dir())):
            for leaf in ("win10", "win7"):
                found = candidate / leaf
                # Prefer the one that actually holds shipped content.
                if found.is_dir() and any(found.iterdir()):
                    if best is None or leaf == "win10":
                        best = found
    return best


def find_oodle_dll(game_root):
    """Lone Echo's Oodle DLL. Always `bin/win7/` inside the game folder."""
    root = Path(game_root)
    for base in (root, root.parent, root.parent.parent):
        dll = base / "bin" / "win7" / "oodle_11_win64.dll"
        if dll.is_file():
            return dll
    hits = sorted(root.glob("**/oodle_*_win64.dll"))
    return hits[0] if hits else None


def find_le1_archive_dir(data_dir):
    """Lone Echo 1's archive directory: `primary/<id>/<version>` under win7.

    An install ships SEVERAL primary holders -- eight on 3.17.4 -- and most are
    not archive sets. The content set is the one whose file names appear
    identically under `GPU/<id>/<version>`: an archive is split across a primary
    and a GPU stream, and both halves carry the same name. On 3.17.4 exactly
    four holders pair up and the largest is `e5bd8207135b8887` with 1,244
    archives, which is the set `le_archive_decode` pins as its constant.

    Taking the biggest folder instead lands on `51e6cb2d64c65e4f` -- 2,888
    files, no GPU counterpart, not what the extractor opens.

    Discovered rather than hard-coded so a different build still works, but it
    agrees with the pinned constants on this one.
    """
    root = Path(data_dir)
    primary, gpu = root / "primary", root / "GPU"
    if not primary.is_dir():
        return None

    def sets(base):
        out = []
        if not base.is_dir():
            return out
        for holder in sorted(p for p in base.iterdir() if p.is_dir()):
            for version in sorted(p for p in holder.iterdir() if p.is_dir()):
                names = frozenset(p.name for p in version.iterdir() if p.is_file())
                if names:
                    out.append((version, names))
        return out

    gpu_sets = [names for _d, names in sets(gpu)]
    best = None
    for version, names in sets(primary):
        if any(names == g for g in gpu_sets):
            if best is None or len(names) > len(best[1]):
                best = (version, names)
    if best:
        return best[0]
    # No GPU tree to pair against: fall back to the largest primary set rather
    # than finding nothing at all.
    allsets = sets(primary)
    return max(allsets, key=lambda kv: len(kv[1]))[0] if allsets else None


def stub_levels(root, levels) -> set:
    """Levels with nothing to extract -- an empty or missing scene/actor stream.

    The Echo VR and Lone Echo 2 analogue of a Lone Echo 1 compressed stub. It
    matches nothing in either extract checked (0 of 32, 0 of 36), which is the
    honest answer for those trees rather than a reason to leave the control off
    them: the same checkbox should mean the same thing on every title.
    """
    out = set()
    actors, scenes = _find_level_dirs(Path(root or ""))
    if actors is None:
        return out
    for h, _name in levels:
        for d in (actors, scenes):
            # extracts drop a leading zero on some names, so try both spellings
            for cand in (h, h.lstrip("0")):
                p = d / cand
                if p.is_file():
                    if p.stat().st_size == 0:
                        out.add(h)
                    break
    return out


def looks_like_game_install(game_root):
    """Validate a Lone Echo 1 install. `(ok, message, data_dir, dll, archives)`.

    Everything the pyoodle path needs lives inside the one folder the user
    picks, so all three are resolved here rather than asked for separately.
    """
    root = Path(game_root or "")
    if not root.is_dir():
        return False, "That folder does not exist.", None, None, None
    data = find_data_dir(root)
    if data is None:
        return (False, "No _data/<build>/win7 directory here. Pick the game's "
                "install folder \u2014 the one containing _data.",
                None, None, None)
    archives = find_le1_archive_dir(data)
    if archives is None:
        return (False, f"Found {data} but no primary/<id>/<version> archives "
                "under it.", data, None, None)
    dll = find_oodle_dll(root)
    n = sum(1 for p in archives.iterdir() if p.is_file())
    if dll is None:
        return (False, f"Found {n} archive(s), but the game's own "
                "bin\\win7\\oodle_11_win64.dll is missing \u2014 the archives are "
                "Oodle-compressed and cannot be read without it.",
                data, None, archives)
    return True, f"Lone Echo install \u2014 {n} archive(s) found.", data, dll, archives


def install_tools() -> list:
    """Copy any missing extractor into `app/extract/`. Returns what it did.

    Bundling matters beyond tidiness: the Lone Echo 1 path puts pyoodle on
    PYTHONPATH, so a moved or deleted clone silently breaks decompression long
    after the fact. A copy the app owns cannot drift.
    """
    report = []
    wanted = {"evrFileTools": EVRTOOLS_HOME, "pyoodle": PYOODLE_HOME}
    for name, dest in wanted.items():
        if dest.is_dir() and any(dest.iterdir()):
            report.append((name, "bundled"))
            continue
        source = next((s for s in TOOL_SOURCES[name] if s.is_dir()), None)
        if source is None:
            report.append((name, "not found"))
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, dest, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(
                                "__pycache__", "*.pyc", ".git", "test_extract*"))
            report.append((name, "installed"))
        except OSError as exc:
            report.append((name, f"failed: {exc}"))
    return report

# -------------------------------------------------------------- palette
BG = "#0e1015"
BG_PANEL = "#161922"
BG_CARD = "#1c2029"
BG_HOVER = "#242936"
LINE = "#272c38"
FG = "#eceff5"
FG_MID = "#9aa3b8"
FG_DIM = "#6b7488"
ACCENT = "#5aa9ff"
GOOD = "#4ade80"
WARN = "#fbbf24"
BAD = "#f87171"

#: Fixed row heights, in pixels. The level list is VIRTUALISED -- only the
#: rows on screen exist as widgets -- and that needs a height it can compute
#: without building anything, so each row is given its height rather than
#: measured. Lone Echo 1 lists 1,244 archives; at ~6 widgets and 12 bindings
#: per row, building them all was ~7,500 widgets in one pass, which froze the
#: window and left the canvas drawing rows over each other.
ROW_GROUP = 46
ROW_MEMBER = 30

#: One hue per group box, cycled. Chosen to stay legible on the dark panel and
#: to read as distinct at a glance rather than as a gradient.
GROUP_HUES = ["#5aa9ff", "#a78bfa", "#4ade80", "#fbbf24",
              "#f472b6", "#22d3ee", "#fb923c", "#94a3b8"]


@dataclass
class Game:
    key: str
    title: str
    subtitle: str
    names_file: str
    tool_name: str
    tool_hint: str
    #: How raw assets are produced. The three titles genuinely differ:
    #:   "packages"  evrtools -mode extract, once per package in _data
    #:   "loneecho"  evrtools -mode loneecho, one pass over the win7 dir
    #:   "pyoodle"   le_extract.py per archive, with pyoodle on PYTHONPATH and
    #:               the game's own Oodle DLL -- it reads the shipped archives
    #:               directly, so there is no flat-tree step at all
    mode: str = "packages"
    #: Where packages/archives live under the chosen data directory. A fallback
    #: only -- `find_le1_archive_dir` discovers the real one, so a different
    #: build of the game still works.
    archive_glob: str = ""
    #: True when the game is read IN PLACE and there is no flat extract at all.
    #: Lone Echo 1 is the only one: `le_extract.py` opens the shipped archives
    #: through pyoodle and the game's own Oodle DLL, so the install folder IS
    #: the source and there is nothing to pre-extract or browse to afterwards.
    install_only: bool = False


GAMES = [
    Game("echovr", "Echo VR", "Zero-g arena \u00b7 all versions",
         "level_names_echovr.json", "evrFileTools",
         "evrtools -mode extract, run once per package in the game's _data "
         "folder (\u2026/_data/<id>/rad15/win10).", "packages"),
    # Lone Echo 2 ships the SAME manifests/packages layout as Echo VR under
    # win10, so it takes the same per-package extract -- verified by running it
    # (package 5f7991e1f1909a1f -> 382 files). `-mode loneecho` is for the win7
    # layout, which is Lone Echo 1, not this.
    Game("loneecho2", "Lone Echo 2", "Story campaign \u00b7 302 levels",
         "level_names_loneecho2.json", "evrFileTools",
         "evrtools -mode extract, run once per package in the game's _data "
         "folder (\u2026/_data/<id>/rad16/win10).", "packages"),
    Game("loneecho1", "Lone Echo", "The original \u00b7 archive pipeline",
         "level_names_loneecho1.json", "pyoodle",
         "Reads the shipped archives directly through pyoodle and the game's "
         "own oodle_11_win64.dll \u2014 no flat-tree step.", "pyoodle",
         "primary/e5bd8207135b8887/v13363680368", install_only=True),
]

#: Lone Echo 1 needs these three on the environment, exactly as the reference
#: PowerShell script sets them. pyoodle resolves to the app's bundled copy.

#: Blender uploads textures DECOMPRESSED, so a 2048 BC1 that is 2.7 MB on disk
#: costs ~16 MB of VRAM. That, not disk size, is why capping matters.
TEXTURE_CHOICES = [
    (512, "512 px", "4 GB VRAM or less \u00b7 safest for large levels"),
    (1024, "1024 px", "6 GB VRAM \u00b7 good balance"),
    (2048, "2048 px", "8-12 GB VRAM \u00b7 near-native detail"),
    (0, "Native", "12 GB+ VRAM \u00b7 full shipped resolution"),
]

EST_SCENE_BYTES = 420 * 1024 * 1024
EST_MODEL_BYTES = 6 * 1024 * 1024


# ------------------------------------------------------------------ addon
def blender_addon_dirs() -> list:
    roots = []
    appdata = Path.home() / "AppData" / "Roaming" / "Blender Foundation" / "Blender"
    if appdata.is_dir():
        for version in sorted(appdata.iterdir()):
            if version.is_dir():
                roots.append(version / "scripts" / "addons")
    return roots


def addon_status() -> tuple:
    if not ADDON_SRC.is_dir():
        return False, []
    source = {p.name: p.stat().st_mtime for p in ADDON_SRC.glob("*.py")}
    report = []
    for target in blender_addon_dirs():
        dest = target / ADDON_SRC.name
        if not dest.is_dir():
            report.append((dest, "missing"))
            continue
        stale = any(not (dest / n).is_file()
                    or (dest / n).stat().st_mtime < t - 1
                    for n, t in source.items())
        report.append((dest, "stale" if stale else "current"))
    return all(s == "current" for _d, s in report) and bool(report), report


def install_addon() -> list:
    changed = []
    for target in blender_addon_dirs():
        dest = target / ADDON_SRC.name
        try:
            target.mkdir(parents=True, exist_ok=True)
            if dest.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(ADDON_SRC, dest,
                            ignore=shutil.ignore_patterns("__pycache__"))
            changed.append(dest)
        except OSError:
            continue
    return changed


# ----------------------------------------------------------- discovery
def looks_like_extract(path) -> tuple:
    p = Path(path)
    if not p.is_dir():
        return False, "That folder does not exist."
    actors, scenes = _find_level_dirs(p)
    if actors is not None:
        n = len({q.name for q in actors.iterdir()} & {q.name for q in scenes.iterdir()})
        return True, f"Valid extract \u2014 {n} level(s) found."
    hexish = sum(1 for q in p.iterdir()
                 if q.is_dir() and 12 <= len(q.name) <= 16
                 and all(c in "0123456789abcdef" for c in q.name.lower()))
    if hexish > 20:
        return True, (f"Looks like an extract ({hexish} resource folders) but the "
                      f"level directories are missing \u2014 it may be partial.")
    return False, ("No resource folders here. Pick the folder that CONTAINS the "
                   "16-character hex directories.")


def norm_hash(name) -> str:
    """`4d82118c7c91b6bb` from any spelling of it.

    Extracts drop a leading zero on some resource names, so `8a1af9e108def0b`
    and `08a1af9e108def0b` are the same level. Comparing them unpadded is how
    `mpl_combat_war_room` used to read as an unnamed hash.
    """
    stem = str(name or "").split(".")[0].lower()
    return stem.rjust(16, "0") if len(stem) <= 16 else stem


def discover_levels(root, names_file) -> list:
    """`[(hash, name_or_None), ...]` for every level present, named first.

    Loads the game-specific names file AND merges every other known-name
    source (all per-game JSONs, the quest_combat_port hash_lookup, and the
    Summer2 names) so the full dictionary is available regardless of which
    extract the user pointed at.  Any still-unnamed hashes are auto-cracked
    via suffix generation and the result is written back so the cost is
    paid once.
    """
    p = Path(root)
    actors, scenes = _find_level_dirs(p)
    if actors is None:
        return []

    # Zero-pad to 16: some extracts drop a hash's leading zero, and an unpadded
    # key misses the name table entirely -- which is how `mpl_combat_war_room`
    # (08a1...) showed up as an unnamed hash.
    norm = norm_hash

    present = ({norm(q.name) for q in actors.iterdir()}
               & {norm(q.name) for q in scenes.iterdir()})

    # Start with the game-specific names file.
    names = {}
    src = DATA / names_file
    if src.is_file():
        try:
            names = json.loads(src.read_text(encoding="utf-8")).get("levels", {})
        except (OSError, ValueError):
            names = {}

    # Merge every OTHER known-name JSON so the full dictionary is always
    # available.  A level present in Summer2 is named by the EchoVR file and
    # vice versa — the user should never see an unnamed hash that we have a
    # name for in any file.
    _ALL_NAME_FILES = [
        "level_names_echovr.json",
        "level_names_loneecho2.json",
        "level_names_loneecho1.json",
        "level_names_summer2.json",
        "level_names.json",
    ]
    for other_file in _ALL_NAME_FILES:
        other = DATA / other_file
        if not other.is_file() or other == src:
            continue
        try:
            raw = json.loads(other.read_text(encoding="utf-8"))
            for section in (raw.get("levels", {}),):
                for k, v in section.items():
                    if v and k not in names:
                        names[k] = v
            # Handle the multi-game format (level_names.json)
            for game in (raw.get("games") or {}).values():
                for k, v in (game.get("levels") or {}).items():
                    if v and k not in names:
                        names[k] = v
        except (OSError, ValueError):
            pass

    # Auto-crack: resolve any unnamed hashes via dictionary + suffix generation.
    unnamed = {h for h in present if not names.get(h)}
    if unnamed:
        try:
            if str(SCRIPTS) not in sys.path:
                sys.path.insert(0, str(SCRIPTS))
            from evr_name_crack import resolve_quick
            cracked = resolve_quick(unnamed, names)
            if cracked:
                names.update(cracked)
                # Persist so this is a one-time cost.
                _save_cracked_names(src, names, present)
        except Exception:                              # noqa: BLE001
            pass  # cracker unavailable or failed — degrade gracefully

    out = [(h, names.get(h)) for h in present]
    out.sort(key=lambda kv: (kv[1] is None, (kv[1] or kv[0]).lower()))
    return out


def _save_cracked_names(path: Path, all_names: dict, present: set) -> None:
    """Write auto-cracked names back to the JSON so they persist.

    Only writes hashes that are present in THIS extract, so the per-game
    file stays scoped to what's actually on disk.
    """
    try:
        scoped = {h: all_names.get(h) for h in present}
        known = sum(1 for v in scoped.values() if v)
        payload = {
            "levels": dict(sorted(scoped.items(),
                                   key=lambda kv: (kv[1] is None, kv[1] or kv[0]))),
            "_note": (f"{known} of {len(scoped)} levels named. "
                      f"Auto-enriched by the universal name cracker."),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    except OSError:
        pass


def group_levels(levels) -> list:
    """Bundle each level with its sublevels.

    A map ships as a parent plus siblings that extend its name --
    `mpl_combat_fission` + `_cargobay` / `_climax` / `_pantheon` / `_prologue`.
    A group's stem is an actual level name, so the parent is always real rather
    than invented; `mpl_lobby_b2` additionally matches `mpl_lobby_b_*` via a
    trailing-digit strip.

    The stem must keep at least two underscores. Dropping that guard collapses
    `mpl_combat_fission` to `mpl_combat`, which swallows dyson, gauss and the
    celebration rooms into one bogus group.

    Returns `[{"stem", "label", "parent", "members": [(hash, name)]}, ...]`,
    biggest groups first, then singletons.
    """
    named = [(h, n) for h, n in levels if n]
    unnamed = [(h, n) for h, n in levels if not n]

    candidates = []
    for h, name in named:
        stems = {name}
        stripped = name.rstrip("0123456789")
        if stripped != name and stripped.count("_") >= 2:
            stems.add(stripped.rstrip("_"))
        for stem in stems:
            members = [(hh, nn) for hh, nn in named
                       if nn == stem or nn.startswith(stem + "_")
                       or (nn == name)]
            members = list(dict.fromkeys(members))
            if len(members) > 1:
                candidates.append((len(members), stem, h, members))

    candidates.sort(key=lambda c: (-c[0], c[1]))
    claimed, groups = set(), []
    for _size, stem, parent, members in candidates:
        members = [m for m in members if m[0] not in claimed]
        if len(members) < 2:
            continue
        claimed.update(m[0] for m in members)
        parent_name = next((n for h, n in members if h == parent), stem)
        groups.append({"stem": stem, "label": parent_name, "parent": parent,
                       "members": sorted(members, key=lambda m: m[1] or "")})

    for h, name in named + unnamed:
        if h in claimed:
            continue
        groups.append({"stem": name or h, "label": name or f"{h}  (unnamed)",
                       "parent": h, "members": [(h, name)]})
    groups.sort(key=lambda g: (len(g["members"]) < 2, g["label"].lower()))
    return groups


def human(n) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def kill_tree(proc):
    """Kill a child AND its descendants.

    `terminate()` alone leaves the extractor's own subprocesses running, so a
    cancelled batch would keep writing files. On Windows only taskkill /T
    reliably takes the whole tree down.
    """
    if proc is None or proc.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


@dataclass
class Job:
    kind: str                     # "scene" | "group" | "models" | "tool"
    level: str
    label: str
    args: list = field(default_factory=list)


class EchoExtractor(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Echo Extractor")
        self.geometry("1140x800")
        self.minsize(980, 700)
        self.configure(bg=BG)

        self.game: Game | None = None
        self.source = tk.StringVar()
        self.outdir = tk.StringVar()
        self.use_default_out = tk.BooleanVar(value=True)
        self.texture = tk.IntVar(value=1024)
        self.levels: list = []
        self.groups: list = []
        #: the unfiltered discovery result, and the entries the stub filter
        #: removes from it -- kept apart so the checkbox can toggle without
        #: re-reading the disk.
        self.all_levels: list = []
        self.stubs: set = set()
        self.sel_groups: set = set()      # stems selected whole -> merged
        self.sel_levels: set = set()      # individual level hashes
        self.want_models = tk.BooleanVar(value=False)
        self.models: list = []
        self.sel_models: set = set()
        self.rigged_only = tk.BooleanVar(value=True)
        self.game_data = tk.StringVar()
        self.oodle_dll = tk.StringVar()
        #: Hide entries that cannot be extracted at all. On by default: 1,046
        #: of Lone Echo 1's 1,244 archives are compressed stubs, so leaving
        #: them in means a list that is 84% guaranteed failures. The remembered
        #: value is applied once `settings` is loaded, below.
        self.hide_stubs = tk.BooleanVar(value=True)
        #: Lone Echo 1 only. "scenes" lists the archives holding a populated
        #: static-scatter master -- the levels, extracted to `.lescatter` by
        #: `le_scene_extract`, which is what the add-on consumes. "meshes"
        #: lists every openable archive for `le_extract`, which pulls out
        #: individual models instead. They are different products, not two
        #: views of one.
        self.le1_mode = tk.StringVar(value="scenes")
        #: The per-instance baked lightmap stream (page + per-vertex UVs).
        #: OFF by default and deliberately so: it is tens of MB per scene and
        #: forces per-instance mesh copies downstream, which has to be the
        #: user's choice rather than a default they discover from disk usage.
        self.le1_instance_lm = tk.BooleanVar(value=False)

        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._proc = None                 # the live child, for a real cancel
        self._proc_lock = threading.Lock()

        self.settings = load_settings()
        self.texture.set(int(self.settings.get("texture") or 1024))
        self.oodle_dll.set(self.settings.get("oodle_dll") or "")
        self.hide_stubs.set(bool(self.settings.get("hide_stubs", True)))

        self._style()
        self._build()
        self._check_addon()
        self._check_tools()
        self.protocol("WM_DELETE_WINDOW", self._quit)
        self.after(80, self._drain)

    # ---------------------------------------------------------------- style
    def _style(self):
        s = ttk.Style(self)
        try:
            s.theme_use("clam")
        except tk.TclError:
            pass
        s.configure(".", background=BG, foreground=FG, borderwidth=0)
        s.configure("TFrame", background=BG)
        s.configure("Panel.TFrame", background=BG_PANEL)
        s.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        s.configure("Dim.TLabel", background=BG, foreground=FG_DIM,
                    font=("Segoe UI", 9))
        s.configure("H1.TLabel", background=BG, foreground=FG,
                    font=("Segoe UI Semibold", 24))
        s.configure("H2.TLabel", background=BG, foreground=FG,
                    font=("Segoe UI Semibold", 13))
        s.configure("Accent.TButton", background=ACCENT, foreground="#06121f",
                    font=("Segoe UI Semibold", 10), padding=(18, 10))
        s.map("Accent.TButton", background=[("active", "#7cbcff"),
                                            ("disabled", "#2b5f96")])
        s.configure("Ghost.TButton", background=BG_CARD, foreground=FG,
                    font=("Segoe UI", 10), padding=(15, 9))
        s.map("Ghost.TButton", background=[("active", BG_HOVER)])
        s.configure("TRadiobutton", background=BG_PANEL, foreground=FG,
                    font=("Segoe UI", 10))
        s.map("TRadiobutton", background=[("active", BG_PANEL)])
        s.configure("Panel.TCheckbutton", background=BG_PANEL, foreground=FG,
                    font=("Segoe UI", 10))
        s.map("Panel.TCheckbutton", background=[("active", BG_PANEL)])
        s.configure("Bar.TCheckbutton", background=BG, foreground=FG_MID,
                    font=("Segoe UI", 9))
        s.map("Bar.TCheckbutton", background=[("active", BG)],
              foreground=[("active", FG)])
        s.configure("Bar.TRadiobutton", background=BG, foreground=FG_MID,
                    font=("Segoe UI", 9))
        s.map("Bar.TRadiobutton", background=[("active", BG)],
              foreground=[("active", FG)])
        s.configure("TEntry", fieldbackground=BG_CARD, foreground=FG,
                    insertcolor=FG, padding=9)
        s.configure("Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=BG_CARD, thickness=8)
        s.configure("Vertical.TScrollbar", background=BG_CARD, troughcolor=BG,
                    arrowcolor=FG_DIM)

    # ---------------------------------------------------------------- build
    def _build(self):
        header = ttk.Frame(self, padding=(34, 26, 34, 8))
        header.pack(fill="x")
        self.h_title = ttk.Label(header, text="Echo Extractor", style="H1.TLabel")
        self.h_title.pack(anchor="w")
        self.h_sub = ttk.Label(header, style="Dim.TLabel",
                               text="Lone Echo and Echo VR assets, into Blender.")
        self.h_sub.pack(anchor="w", pady=(3, 0))
        tk.Frame(self, bg=LINE, height=1).pack(fill="x", padx=34, pady=(14, 0))

        self.body = ttk.Frame(self, padding=(34, 16, 34, 10))
        self.body.pack(fill="both", expand=True)

        footer = ttk.Frame(self, padding=(34, 4, 34, 20))
        footer.pack(fill="x")
        self.status = ttk.Label(footer, text="", style="Dim.TLabel")
        self.status.pack(anchor="w")
        self.progress = ttk.Progressbar(footer, mode="determinate",
                                        style="Horizontal.TProgressbar")
        self._page_games()

    def _clear(self):
        for w in self.body.winfo_children():
            w.destroy()

    def _set_status(self, text, tone=FG_DIM):
        self.status.configure(text=text, foreground=tone)

    def _nav(self, back=None, forward=None, forward_text="Continue \u2192",
             extra=None):
        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(20, 0))
        if back:
            ttk.Button(bar, text="\u2190 Back", style="Ghost.TButton",
                       command=back).pack(side="left")
        if forward:
            btn = ttk.Button(bar, text=forward_text, style="Accent.TButton",
                             command=forward)
            btn.pack(side="right")
        if extra:
            ttk.Button(bar, text=extra[0], style="Ghost.TButton",
                       command=extra[1]).pack(side="right", padx=(0, 10))
        return bar

    # ------------------------------------------------------------ addon
    def _check_addon(self):
        ok, report = addon_status()
        if not report:
            self._set_status("Blender not found \u2014 the add-on installs once "
                             "Blender has been run at least once.", WARN)
            return
        if ok:
            self._set_status(f"Blender add-on up to date "
                             f"({len(report)} install(s)).", GOOD)
            return
        changed = install_addon()
        if changed:
            self._set_status("Blender add-on installed automatically \u2014 "
                             "restart Blender to pick it up.", GOOD)
            messagebox.showinfo(
                "Add-on installed",
                "The Lone Echo importer was installed into Blender:\n\n"
                + "\n".join(str(p) for p in changed)
                + "\n\nEnable it under Edit > Preferences > Add-ons if it is not "
                  "already on, then restart Blender.")
        else:
            self._set_status("Could not install the Blender add-on \u2014 check "
                             "folder permissions.", BAD)

    # ------------------------------------------------------- page: games
    def _page_games(self):
        self._clear()
        self.h_title.configure(text="Choose a game")
        self.h_sub.configure(text="Each title has its own asset pipeline.")

        row = ttk.Frame(self.body)
        row.pack(fill="both", expand=True)
        for i, game in enumerate(GAMES):
            hue = GROUP_HUES[i]
            card = tk.Frame(row, bg=BG_CARD, highlightthickness=1,
                            highlightbackground=LINE)
            card.grid(row=0, column=i, sticky="nsew",
                      padx=(0 if i == 0 else 16, 0))
            row.columnconfigure(i, weight=1, uniform="g")
            row.rowconfigure(0, weight=1)

            tk.Frame(card, bg=hue, height=3).pack(fill="x")
            inner = tk.Frame(card, bg=BG_CARD)
            inner.pack(fill="both", expand=True, padx=24, pady=(22, 24))
            tk.Label(inner, text=game.title, bg=BG_CARD, fg=FG,
                     font=("Segoe UI Semibold", 17)).pack(anchor="w")
            tk.Label(inner, text=game.subtitle, bg=BG_CARD, fg=hue,
                     font=("Segoe UI", 10)).pack(anchor="w", pady=(3, 0))
            tk.Label(inner, text=game.tool_hint, bg=BG_CARD, fg=FG_DIM,
                     font=("Segoe UI", 9), wraplength=250,
                     justify="left").pack(anchor="w", pady=(18, 0))
            tk.Label(inner, text=f"Needs {game.tool_name}", bg=BG_CARD,
                     fg=FG_MID, font=("Segoe UI", 9)).pack(anchor="w", pady=(12, 18))
            ttk.Button(inner, text="Select", style="Accent.TButton",
                       command=lambda g=game: self._pick_game(g)).pack(anchor="w")

            def enter(_e, c=card, w=inner):
                c.configure(bg=BG_HOVER)
                w.configure(bg=BG_HOVER)
                for ch in w.winfo_children():
                    if isinstance(ch, tk.Label):
                        ch.configure(bg=BG_HOVER)

            def leave(_e, c=card, w=inner):
                c.configure(bg=BG_CARD)
                w.configure(bg=BG_CARD)
                for ch in w.winfo_children():
                    if isinstance(ch, tk.Label):
                        ch.configure(bg=BG_CARD)

            for widget in (card, inner, *inner.winfo_children()):
                widget.bind("<Enter>", enter)
                widget.bind("<Leave>", leave)

    def _pick_game(self, game):
        self.game = game
        self.sel_groups.clear()
        self.sel_levels.clear()
        self.levels, self.groups = [], []
        self.all_levels, self.stubs = [], set()
        remembered = (self.settings.get("paths") or {}).get(game.key) or {}
        self.source.set(remembered.get("source") or "")
        saved_out = remembered.get("out")
        self.use_default_out.set(not saved_out)
        self.outdir.set(saved_out or "")
        self._page_source()

    def _remember(self):
        """Persist every path the user chose, keyed by game."""
        paths = self.settings.setdefault("paths", {})
        if self.game:
            entry = paths.setdefault(self.game.key, {})
            if self.source.get():
                entry["source"] = self.source.get()
            entry["out"] = ("" if self.use_default_out.get()
                            else self.outdir.get())
            self.settings["last_game"] = self.game.key
        self.settings["texture"] = int(self.texture.get())
        self.settings["hide_stubs"] = bool(self.hide_stubs.get())
        if self.oodle_dll.get():
            self.settings["oodle_dll"] = self.oodle_dll.get()
        save_settings(self.settings)

    def _check_tools(self):
        report = install_tools()
        installed = [n for n, s in report if s == "installed"]
        missing = [n for n, s in report if s == "not found"]
        if installed:
            messagebox.showinfo(
                "Extractors installed",
                "Copied into the app so they cannot go missing later:\n\n"
                + "\n".join(f"  {n}  →  {TOOLS_DIR / n}"
                            for n in installed))
        if missing:
            self._set_status(
                "Could not find " + " or ".join(missing)
                + " to install — point at an already-extracted folder "
                  "instead, or install the tool and restart.", WARN)

    # ------------------------------------------------------ page: source
    def _page_source(self):
        self._clear()
        if self.game.install_only:
            self._page_install()
            return
        self.h_title.configure(text=self.game.title)
        self.h_sub.configure(text="Where are the extracted game assets?")

        card = tk.Frame(self.body, bg=BG_PANEL)
        card.pack(fill="x")
        tk.Label(card, text="Already extracted?", bg=BG_PANEL, fg=FG,
                 font=("Segoe UI Semibold", 12)).pack(anchor="w", padx=24,
                                                      pady=(22, 2))
        tk.Label(card, bg=BG_PANEL, fg=FG_DIM, font=("Segoe UI", 9),
                 justify="left", wraplength=940,
                 text="Point at the folder holding the 16-character resource "
                      "directories. It is checked before anything runs."
                 ).pack(anchor="w", padx=24)

        pick = ttk.Frame(card, style="Panel.TFrame")
        pick.pack(fill="x", padx=24, pady=(16, 4))
        ttk.Entry(pick, textvariable=self.source).pack(side="left", fill="x",
                                                       expand=True)
        ttk.Button(pick, text="Browse", style="Ghost.TButton",
                   command=self._browse_source).pack(side="left", padx=(10, 0))
        ttk.Button(pick, text="Check", style="Ghost.TButton",
                   command=self._validate_source).pack(side="left", padx=(8, 0))

        self.src_note = tk.Label(card, text="", bg=BG_PANEL, fg=FG_DIM,
                                 font=("Segoe UI", 9), justify="left",
                                 wraplength=940)
        self.src_note.pack(anchor="w", padx=24, pady=(8, 22))

        tools = tk.Frame(self.body, bg=BG_PANEL)
        tools.pack(fill="x", pady=(16, 0))
        found = self._tool_present()
        tk.Label(tools, text=f"Not extracted yet? \u00b7 {self.game.tool_name}",
                 bg=BG_PANEL, fg=FG, font=("Segoe UI Semibold", 12)).pack(
                     anchor="w", padx=24, pady=(22, 4))
        tk.Label(tools, bg=BG_PANEL, fg=GOOD if found else WARN,
                 font=("Segoe UI", 9), justify="left", wraplength=940,
                 text=(f"Found: {self._tool_path()}" if found else
                       f"{self.game.tool_name} was not found. "
                       + f"Expected at {self._tool_path()}")
                 ).pack(anchor="w", padx=24)
        ttk.Button(tools, text=f"Run {self.game.tool_name}",
                   style="Accent.TButton" if found else "Ghost.TButton",
                   command=self._run_tool).pack(anchor="w", padx=24, pady=(16, 22))

        self._nav(back=self._page_games, forward=self._page_pick)
        self.btn_next = [w for w in self.body.winfo_children()[-1].winfo_children()
                         if isinstance(w, ttk.Button)][-1]
        self.btn_next.state(["disabled"])
        if self.source.get():
            self._validate_source()

    def _page_install(self):
        """Lone Echo 1: point at the GAME, not at an extract.

        There is no "already extracted?" step here and no tool to run first.
        `le_extract.py` opens the shipped archives in place through pyoodle and
        the game's own Oodle DLL, so the install folder is the only thing to
        ask for -- `_data/<build>/win7`, the archive directory and the DLL are
        all found inside it. Asking for a flat extract that this pipeline never
        produces is what made this page a dead end.
        """
        self.h_title.configure(text=self.game.title)
        self.h_sub.configure(text="Where is the game installed?")

        card = tk.Frame(self.body, bg=BG_PANEL)
        card.pack(fill="x")
        tk.Label(card, text="Lone Echo install folder", bg=BG_PANEL, fg=FG,
                 font=("Segoe UI Semibold", 12)).pack(anchor="w", padx=24,
                                                      pady=(22, 2))
        tk.Label(card, bg=BG_PANEL, fg=FG_DIM, font=("Segoe UI", 9),
                 justify="left", wraplength=940,
                 text="The folder that contains _data. Lone Echo 1 is read "
                      "straight out of its own archives \u2014 there is no "
                      "extract step and nothing to run first."
                 ).pack(anchor="w", padx=24)

        pick = ttk.Frame(card, style="Panel.TFrame")
        pick.pack(fill="x", padx=24, pady=(16, 4))
        ttk.Entry(pick, textvariable=self.source).pack(side="left", fill="x",
                                                       expand=True)
        ttk.Button(pick, text="Browse", style="Ghost.TButton",
                   command=self._browse_install).pack(side="left", padx=(10, 0))
        ttk.Button(pick, text="Check", style="Ghost.TButton",
                   command=self._validate_source).pack(side="left", padx=(8, 0))

        self.src_note = tk.Label(card, text="", bg=BG_PANEL, fg=FG_DIM,
                                 font=("Segoe UI", 9), justify="left",
                                 wraplength=940)
        self.src_note.pack(anchor="w", padx=24, pady=(8, 22))

        found = tk.Frame(self.body, bg=BG_PANEL)
        found.pack(fill="x", pady=(16, 0))
        tk.Label(found, text="Found inside it", bg=BG_PANEL, fg=FG,
                 font=("Segoe UI Semibold", 12)).pack(anchor="w", padx=24,
                                                      pady=(22, 6))
        self.install_note = tk.Label(
            found, text="", bg=BG_PANEL, fg=FG_DIM, font=("Consolas", 9),
            justify="left", wraplength=940)
        self.install_note.pack(anchor="w", padx=24, pady=(0, 22))

        self._nav(back=self._page_games, forward=self._page_pick)
        self.btn_next = [w for w in self.body.winfo_children()[-1].winfo_children()
                         if isinstance(w, ttk.Button)][-1]
        self.btn_next.state(["disabled"])
        if self.source.get():
            self._validate_source()

    def _browse_install(self):
        chosen = filedialog.askdirectory(
            title=f"{self.game.title} game folder (the one containing _data)")
        if chosen:
            self.source.set(chosen)
            self._validate_source()

    def _validate_install(self) -> bool:
        """Resolve and report _data, the archives and the Oodle DLL."""
        ok, message, data, dll, archives = looks_like_game_install(
            self.source.get() or "")
        if ok:
            self.game_data.set(str(data))
            self.oodle_dll.set(str(dll))
            self._tool_environ = self._tool_env(data)
            self._archive_dir = str(archives)
            self._remember()
        else:
            self._archive_dir = str(archives) if archives else ""
        note = getattr(self, "src_note", None)
        if note is not None and note.winfo_exists():
            note.configure(text=message, fg=GOOD if ok else BAD)
        detail = getattr(self, "install_note", None)
        if detail is not None and detail.winfo_exists():
            rows = [("game data", data), ("archives", archives),
                    ("oodle dll", dll)]
            detail.configure(
                text="\n".join("%-10s %s" % (k, v if v else "\u2014 not found")
                                for k, v in rows),
                fg=FG_DIM if ok else WARN)
        btn = getattr(self, "btn_next", None)
        if btn is not None and btn.winfo_exists():
            btn.state(["!disabled"] if ok else ["disabled"])
        return ok

    def _tool_path(self):
        if self.game.mode == "pyoodle":
            return PYOODLE_HOME
        exe = evrtools_exe()
        return exe if exe.is_file() else EVRTOOLS_HOME

    def _tool_present(self) -> bool:
        return Path(self._tool_path()).exists()

    def _tool_jobs(self, data_dir, target) -> list:
        """The real command(s) that turn a game install into raw assets."""
        exe = str(evrtools_exe())
        data, out = str(data_dir), str(target)

        if self.game.mode == "loneecho":
            # One pass over the whole win7 directory.
            return [Job("tool", "", "evrtools -mode loneecho",
                        [exe, "-mode", "loneecho", "-data", data,
                         "-output", out, "-force"])]

        if self.game.mode == "packages":
            # Echo VR extracts per package, so enumerate them from _data.
            # Packages are NOT in the win10 root: that holds `manifests/` and
            # `packages/`, the latter split into `<hash>_0`, `<hash>_1`, ...
            # chunks. One manifest == one package, so the manifest directory is
            # the authoritative list; `packages/` is the fallback with the
            # chunk suffix stripped. A `.bak` manifest is not a package.
            def is_package(name):
                stem = name.split(".")[0].split("_")[0].lower()
                return (len(stem) == 16
                        and all(c in "0123456789abcdef" for c in stem)), stem

            root = Path(data_dir)
            names = set()
            for folder in (root / "manifests", root / "packages", root):
                if not folder.is_dir():
                    continue
                for entry in folder.iterdir():
                    if entry.name.endswith(".bak"):
                        continue
                    ok, stem = is_package(entry.name)
                    if ok:
                        names.add(stem)
                if names:
                    break
            packages = sorted(names)
            return [Job("tool", pkg, f"package {pkg}",
                        [exe, "-mode", "extract", "-data", data,
                         "-package", pkg, "-output", out, "-force"])
                    for pkg in packages]

        # Lone Echo 1: one le_extract.py call per archive, mirroring the
        # reference script. Archives are the file names under the versioned
        # primary directory, not the data root itself.
        archive_dir = (find_le1_archive_dir(data_dir)
                       or Path(data_dir) / self.game.archive_glob)
        archives = sorted(p.name for p in archive_dir.iterdir()
                          if p.is_file()) if archive_dir.is_dir() else []
        extractor = str(REPO / "blender_tool" / "extractor" / "le_extract.py")
        return [Job("tool", a, f"archive {a}",
                    [sys.executable, extractor, "--archive", a, "--all",
                     "--out", str(Path(out) / "meshes"), "--textures",
                     "--direct-materials"])
                for a in archives]

    def _tool_env(self, data_dir):
        """Environment for the tool run. Only Lone Echo 1 needs one."""
        if self.game.mode != "pyoodle":
            return None
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PYOODLE_HOME)
        env["LONE_ECHO_DATA_ROOT"] = str(data_dir)
        dll = self.oodle_dll.get()
        if dll:
            env["LONE_ECHO_OODLE_DLL"] = dll
        return env

    def _browse_source(self):
        chosen = filedialog.askdirectory(
            title="Folder containing the extracted assets")
        if chosen:
            self.source.set(chosen)
            self._validate_source()

    def _validate_source(self) -> bool:
        """Check the source folder. Safe to call when the page is gone.

        The note widget belongs to the source page; touching it after that page
        was destroyed is what used to make Back look like it did nothing.
        """
        if self.game.install_only:
            return self._validate_install()
        ok, message = looks_like_extract(self.source.get() or ".")
        if ok:
            self._remember()
        note = getattr(self, "src_note", None)
        if note is not None and note.winfo_exists():
            note.configure(text=message, fg=GOOD if ok else BAD)
        btn = getattr(self, "btn_next", None)
        if btn is not None and btn.winfo_exists():
            btn.state(["!disabled"] if ok else ["disabled"])
        return ok

    def _run_tool(self):
        if not self._tool_present():
            messagebox.showwarning(
                self.game.tool_name,
                f"{self.game.tool_name} was not found.\n\n"
                f"Expected: {self._tool_path()}")
            return

        picked = filedialog.askdirectory(
            title=f"{self.game.title} game folder (the one containing _data)")
        if not picked:
            return
        data_dir = find_data_dir(picked)
        if data_dir is None:
            messagebox.showerror(
                "No _data folder",
                f"Could not find a win10/win7 data directory under:\n{picked}"
                f"\n\nPick the game's install folder — the one holding _data.")
            return
        self.game_data.set(str(data_dir))

        if self.game.mode == "pyoodle":
            dll = find_oodle_dll(picked)
            if dll is None:
                messagebox.showerror(
                    "Oodle DLL not found",
                    "Lone Echo archives are Oodle-compressed and the game's own "
                    "bin\\win7\\oodle_11_win64.dll was not found under:\n"
                    f"{picked}")
                return
            self.oodle_dll.set(str(dll))

        target = filedialog.askdirectory(title="Where should the raw assets go?")
        if not target:
            return

        jobs = self._tool_jobs(data_dir, target)
        if not jobs:
            messagebox.showerror(
                "Nothing to extract",
                f"No packages or archives were found under:\n{data_dir}\n\n"
                f"Check that this is the right folder for {self.game.title}.")
            return
        self.source.set(target)
        self._remember()
        self._tool_environ = self._tool_env(data_dir)
        self._start(jobs, raw_tool=True)

    # -------------------------------------------------------- page: pick
    def _page_pick(self):
        if self.game.install_only:
            if not looks_like_game_install(self.source.get() or "")[0]:
                self._page_source()
                return
        elif not looks_like_extract(self.source.get() or ".")[0]:
            self._validate_source()
            return
        if self.game.install_only and self._le1_index() is None:
            # Lone Echo 1 ships no level list, so what each archive IS has to be
            # read out of the archives themselves. Done once per install.
            if not messagebox.askyesno(
                    "Index the archives",
                    "Lone Echo 1 has no level list, so the app has to look "
                    "inside the archives once to find which of them are "
                    "scenes.\n\nThis takes about a minute and is saved, so it "
                    "only happens again if you point at a different install."
                    "\n\nRun it now?"):
                return
            self._tool_environ = self._tool_env(self.game_data.get())
            self._start([self._le1_scan_job()], raw_tool=True)
            return
        if not self.groups or not self.levels:
            if self.game.install_only:
                self.all_levels, self.stubs = self._le1_levels(self._le1_index())
            else:
                self.all_levels = discover_levels(self.source.get(),
                                                  self.game.names_file)
                self.stubs = stub_levels(self.source.get(), self.all_levels)
            self._apply_stub_filter()
        self._clear()
        self.h_title.configure(text="Choose levels")
        # Archive hashes cannot be named from anything Lone Echo 1 ships, so
        # the header says that once rather than leaving 1,200 bare hashes
        # unexplained; it also reports how many stubs are being hidden.
        self._pick_header()

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(0, 10))
        self.search = tk.StringVar()
        ttk.Entry(bar, textvariable=self.search).pack(side="left", fill="x",
                                                      expand=True)
        ttk.Button(bar, text="Select all", style="Ghost.TButton",
                   command=self._select_all).pack(side="left", padx=(10, 0))
        ttk.Button(bar, text="Clear", style="Ghost.TButton",
                   command=self._clear_sel).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(bar, style="Bar.TCheckbutton", variable=self.hide_stubs,
                        command=self._toggle_stub_filter,
                        text="  Hide compressed stubs").pack(side="left",
                                                             padx=(14, 0))
        if self.game.install_only:
            for value, label in (("scenes", "Scenes"), ("meshes", "Meshes")):
                ttk.Radiobutton(bar, style="Bar.TRadiobutton", value=value,
                                variable=self.le1_mode, text="  " + label,
                                command=self._toggle_le1_mode).pack(
                                    side="left", padx=(10, 0))

        wrap = tk.Frame(self.body, bg=BG_PANEL)
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg=BG_PANEL, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self._yview)
        self.vbar = sb
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind("<Configure>", lambda _e: self._render_window(force=True))
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)

        #: index -> (canvas item, widget) for the rows currently materialised
        self._placed = {}
        self._rows = []
        self.search.trace_add("write", lambda *_a: self._paint())
        self._paint()
        self._page_pick_footer()

    def _le1_index(self):
        """The archive classification for the CURRENT install, or None.

        Rejected when it describes a different archive directory, so pointing
        the app at another copy of the game re-scans instead of showing the
        previous one's contents.
        """
        try:
            idx = json.loads(LE1_INDEX_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if idx.get("format") != "le1_scene_index":
            return None
        want = str(getattr(self, "_archive_dir", "") or "")
        if want and Path(idx.get("archive_dir", "")) != Path(want):
            return None
        return idx

    def _le1_scan_job(self):
        """The one-off indexing pass, as a job the normal runner can drive."""
        return Job("tool", "index", "classifying archives",
                   [sys.executable, str(SCRIPTS / "le_scene_index.py"),
                    "--data-root", self.game_data.get(),
                    "--out", str(LE1_INDEX_FILE)])

    def _le1_names(self) -> dict:
        """`{archive hash: authored name}` for Lone Echo 1.

        An archive hash IS the CSymbol64 of its authored name -- verified 8/8
        against the names already in `data/hash_lookup.json` -- so these are
        recovered preimages, not labels invented here. Two sources, merged:
        the shared 13k `hash_lookup` table and `level_names_loneecho1.json`,
        which holds what the generator cracked on top of it.

        Note this is a DIFFERENT namespace from `data/le1_scene_names.json`.
        Those 171 sourcedb identifiers bind to nothing and that is settled;
        archive names are their own thing and do crack.
        """
        if getattr(self, "_le1_name_cache", None) is not None:
            return self._le1_name_cache
        out = {}
        for path, key in ((DATA / "hash_lookup.json", None),
                          (DATA / "level_names_loneecho1.json", "levels")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            table = (raw.get(key) or {}) if key else raw
            for h, name in table.items():
                if isinstance(name, str) and name:
                    out[norm_hash(h.replace("0x", ""))] = name
        self._le1_name_cache = out
        return out

    def _le1_pkg_dir(self, job) -> Path:
        """Where a Lone Echo 1 scene package goes.

        Named by the level's AUTHORED name, falling back to the hash only when
        the preimage was never recovered -- the same rule `evr_scene_extract`
        applies for Echo VR (`LEVEL_NAMES.get(hash) or hash`). Taken from the
        name table rather than `job.label`, because a singleton group's label
        carries a "  (unnamed)" suffix that has no business in a path.
        """
        name = self._le1_names().get(norm_hash(job.level)) or job.level
        return Path(self.outdir.get()) / "scenes" / name

    def _le1_levels(self, idx):
        """`(entries, stubs)` for the current Lone Echo 1 mode."""
        scenes = sorted(idx.get("scenes") or {})
        meshes = sorted(idx.get("meshes") or [])
        stubs = set(idx.get("stubs") or [])
        names = self._le1_names()
        if self.le1_mode.get() == "scenes":
            # A scene archive is never a stub, so nothing is hidden here.
            return [(h, names.get(h)) for h in scenes], set()
        # Mesh mode offers every archive `le_extract` can open -- scene
        # archives carry meshlists too -- and the stubs behind the checkbox.
        openable = sorted(set(scenes) | set(meshes))
        return ([(h, names.get(h)) for h in openable + sorted(stubs)], stubs)

    def _toggle_le1_mode(self):
        idx = self._le1_index()
        if idx is None:
            return
        self.all_levels, self.stubs = self._le1_levels(idx)
        self.sel_groups.clear()
        self.sel_levels.clear()
        self._apply_stub_filter()
        self._pick_header()
        self._paint()
        self._update_count()

    def _apply_stub_filter(self):
        """Derive `levels`/`groups` from the full list and the stub set.

        Filtering BEFORE grouping, rather than hiding rows at paint time, is
        what keeps everything else honest: `Select all`, the job plan and the
        selection counter all read `groups`, so a hidden stub can never be
        queued for an extraction that is certain to fail.
        """
        stubs = getattr(self, "stubs", set()) or set()
        full = getattr(self, "all_levels", []) or []
        self.levels = ([lv for lv in full if lv[0] not in stubs]
                       if self.hide_stubs.get() else list(full))
        keep = {h for h, _n in self.levels}
        self.sel_levels &= keep
        self.groups = group_levels(self.levels)
        self.sel_groups &= {g["stem"] for g in self.groups}

    def _toggle_stub_filter(self):
        """Re-plan the list when the checkbox moves."""
        self._apply_stub_filter()
        self._pick_header()
        self._paint()
        self._update_count()

    def _pick_header(self):
        """The line under the title -- it has to restate the counts."""
        hidden = len(getattr(self, "all_levels", [])) - len(self.levels)
        note = (" \u00b7 %d hidden" % hidden) if hidden else ""
        if self.game.install_only:
            what = ("scene(s) \u2014 archives holding a static-scatter master"
                    if self.le1_mode.get() == "scenes"
                    else "archive(s) with meshes to pull out")
            text = ("%d %s%s. Lone Echo 1's hashes have no shipped names "
                    "\u2014 search by hash, or take them all."
                    % (len(self.levels), what, note))
        else:
            bundles = sum(1 for g in self.groups if len(g["members"]) > 1)
            text = ("%d level(s) \u00b7 %d bundle(s)%s. Click a bundle to take it "
                    "merged, or pick levels inside it."
                    % (len(self.levels), bundles, note))
        if getattr(self, "h_sub", None) is not None and self.h_sub.winfo_exists():
            self.h_sub.configure(text=text)

    # -- virtual list ------------------------------------------------------
    def _yview(self, *args):
        self.canvas.yview(*args)
        self._render_window()

    def _on_wheel(self, e):
        if not getattr(self, "canvas", None) or not self.canvas.winfo_exists():
            return
        self.canvas.yview_scroll(int(-e.delta / 120), "units")
        self._render_window()

    def _page_pick_footer(self):
        """The options strip below the list.

        Its own method so it cannot drift back into a scroll handler: it once
        did, which meant the footer was never built on page load and was packed
        again on EVERY wheel event -- the duplicated "Also extract standalone
        models" blocks piling up under the nav bar.
        """
        models = tk.Frame(self.body, bg=BG_PANEL)
        models.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(models, style="Panel.TCheckbutton",
                        variable=self.want_models,
                        text="  Also extract standalone models for the selected "
                             "levels").pack(anchor="w", padx=20, pady=12)

        ttk.Button(models, text="Single models\u2026", style="Ghost.TButton",
                   command=self._page_models).pack(side="right", padx=20, pady=8)
        if self.game.install_only:
            ttk.Checkbutton(
                models, style="Panel.TCheckbutton",
                variable=self.le1_instance_lm,
                text="  Per-instance baked lightmap UVs \u2014 tens of MB per "
                     "scene, and each instance gets its own mesh copy"
            ).pack(anchor="w", padx=20, pady=(0, 12))

        self.count_label = ttk.Label(self.body, style="Dim.TLabel")
        self.count_label.pack(anchor="w", pady=(10, 0))
        self._update_count()
        self._nav(back=self._page_source, forward=self._page_options,
                  forward_text="Options \u2192")

    def _paint(self):
        """Re-plan the list. Only for a filter change -- NOT for selection.

        Selecting used to call this, which destroyed and recreated every row:
        the whole list visibly flashed and the scroll position jumped. Toggles
        restyle the existing widgets instead, so nothing is rebuilt.

        This builds a PLAN -- one entry per visible row, with its height -- and
        leaves the widgets to `_render_window`, which makes only the rows on
        screen. Building all of them is what made Lone Echo 1's 1,244 archives
        freeze the window and draw over themselves.
        """
        self._clear_rows()
        self._gw = {}          # stem -> the group's header widgets
        self._mw = {}          # level hash -> that member's row widgets
        needle = self.search.get().strip().lower()
        rows, y = [], 0
        for i, group in enumerate(self.groups):
            hue = GROUP_HUES[i % len(GROUP_HUES)]
            members = group["members"]
            if needle:
                hit = (needle in group["label"].lower()
                       or any(needle in (n or h).lower() for h, n in members))
                if not hit:
                    continue
            rows.append({"kind": "group", "group": group, "hue": hue,
                         "y": y, "h": ROW_GROUP})
            y += ROW_GROUP
            if len(members) > 1:
                for h, name in members:
                    rows.append({"kind": "member", "hash": h, "name": name,
                                 "hue": hue, "group": group,
                                 "y": y, "h": ROW_MEMBER})
                    y += ROW_MEMBER
        self._rows = rows
        self.canvas.configure(scrollregion=(0, 0, 1, max(y, 1)))
        self.canvas.yview_moveto(0.0)
        self._render_window(force=True)

    def _clear_rows(self):
        for item, widget in getattr(self, "_placed", {}).values():
            self.canvas.delete(item)
            widget.destroy()
        self._placed = {}

    def _render_window(self, force=False):
        """Materialise only the rows inside the viewport (plus a little slack).

        Rows leaving the window are destroyed and their entries dropped from
        `_gw` / `_mw`, so the restyle maps only ever hold live widgets.
        """
        if not getattr(self, "_rows", None) or not self.canvas.winfo_exists():
            if force and getattr(self, "_placed", None):
                self._clear_rows()
            return
        top = self.canvas.canvasy(0)
        height = self.canvas.winfo_height() or 600
        width = self.canvas.winfo_width() or 900
        pad = ROW_GROUP * 3                       # a little either side
        lo, hi = top - pad, top + height + pad

        want = {i for i, r in enumerate(self._rows)
                if r["y"] + r["h"] >= lo and r["y"] <= hi}
        for i in [i for i in self._placed if i not in want]:
            item, widget = self._placed.pop(i)
            self.canvas.delete(item)
            row = self._rows[i]
            if row["kind"] == "group":
                self._gw.pop(row["group"]["stem"], None)
            else:
                self._mw.pop(row["hash"], None)
            widget.destroy()
        for i in sorted(want):
            row = self._rows[i]
            if i in self._placed:
                if force:
                    self.canvas.itemconfigure(self._placed[i][0], width=width)
                continue
            widget = (self._make_group_row(row) if row["kind"] == "group"
                      else self._make_member_row(row))
            item = self.canvas.create_window(0, row["y"], anchor="nw",
                                             window=widget, width=width,
                                             height=row["h"])
            self._placed[i] = (item, widget)

    def _make_group_row(self, row):
        """One group header, parented to the canvas rather than packed.

        Positioning is the canvas's job (`_render_window` places it at the row's
        own `y`), so nothing here packs into a shared column -- which is what
        let thousands of rows fight over the same geometry and overlap.
        """
        group, hue = row["group"], row["hue"]
        multi = len(group["members"]) > 1

        box = tk.Frame(self.canvas, bg=BG_PANEL)
        head = tk.Frame(box, bg=BG_CARD, highlightthickness=1,
                        highlightbackground=LINE)
        head.pack(fill="both", expand=True, padx=14, pady=(8, 0))
        tk.Frame(head, bg=hue, width=4).pack(side="left", fill="y")

        title = tk.Label(head, text=group["label"], bg=BG_CARD,
                         font=("Segoe UI Semibold", 11) if multi
                         else ("Segoe UI", 10))
        title.pack(side="left", padx=(14, 0))
        badge = tk.Label(head, bg=BG_CARD, fg=hue, font=("Segoe UI", 9),
                         text=(f"bundle \u00b7 {len(group['members'])} levels"
                               if multi else "single level"))
        badge.pack(side="left", padx=(12, 0))
        mark = tk.Label(head, bg=BG_CARD, fg=hue,
                        font=("Segoe UI Semibold", 11), text="")
        mark.pack(side="right", padx=14)

        self._gw[group["stem"]] = {"group": group, "hue": hue, "multi": multi,
                                   "head": head, "title": title,
                                   "badge": badge, "mark": mark}
        for w in (head, title, badge, mark):
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda _e, g=group: self._toggle_group(g))
            w.bind("<Enter>", lambda _e, st=group["stem"]: self._hover_group(st, True))
            w.bind("<Leave>", lambda _e, st=group["stem"]: self._hover_group(st, False))
        self._style_group(group["stem"])
        return box

    def _make_member_row(self, row):
        h, name, hue, group = row["hash"], row["name"], row["hue"], row["group"]
        outer = tk.Frame(self.canvas, bg=BG_PANEL)
        inner = tk.Frame(outer, bg=BG_PANEL)
        inner.pack(fill="both", expand=True, padx=(36, 14))
        edge = tk.Frame(inner, bg=LINE, width=2)
        edge.pack(side="left", fill="y")
        label = tk.Label(inner, text=name or f"{h}  (unnamed)", bg=BG_PANEL,
                         font=("Segoe UI", 10) if name else ("Consolas", 9),
                         anchor="w")
        label.pack(side="left", fill="both", expand=True, padx=(12, 0))

        self._mw[h] = {"row": inner, "edge": edge, "label": label, "hue": hue,
                       "stem": group["stem"]}
        for w in (inner, label):
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda _e, hh=h: self._toggle_level(hh))
            w.bind("<Enter>", lambda _e, hh=h: self._hover_member(hh, True))
            w.bind("<Leave>", lambda _e, hh=h: self._hover_member(hh, False))
        self._style_member(h)
        return outer

    # -- restyling: touches colours only, never the widget tree ------------
    def _style_group(self, stem, hover=False):
        w = self._gw.get(stem)
        if not w or not w["head"].winfo_exists():
            return
        chosen = stem in self.sel_groups
        hue, multi = w["hue"], w["multi"]
        bg = self._tint(hue) if chosen else (BG_HOVER if hover else BG_CARD)
        w["head"].configure(bg=bg, highlightbackground=hue if chosen else LINE)
        w["title"].configure(bg=bg, fg=FG if (chosen or multi) else FG_MID)
        w["badge"].configure(bg=bg)
        w["mark"].configure(bg=bg, text="\u2713 merged" if chosen else "")

    def _style_member(self, h, hover=False):
        w = self._mw.get(h)
        if not w or not w["row"].winfo_exists():
            return
        dimmed = w["stem"] in self.sel_groups
        picked = h in self.sel_levels and not dimmed
        bg = self._tint(w["hue"]) if picked else (BG_HOVER if hover else BG_PANEL)
        w["row"].configure(bg=bg)
        w["edge"].configure(bg=w["hue"] if picked else LINE)
        w["label"].configure(bg=bg,
                             fg=FG if picked else (FG_DIM if dimmed else FG_MID))

    def _hover_group(self, stem, on):
        if stem not in self.sel_groups:
            self._style_group(stem, hover=on)

    def _hover_member(self, h, on):
        self._style_member(h, hover=on)

    @staticmethod
    def _tint(hue):
        """A dark, desaturated version of a hue, for a selected row."""
        r, g, b = (int(hue[i:i + 2], 16) for i in (1, 3, 5))
        mix = lambda c: int(c * 0.22 + 0x1c * 0.78)          # noqa: E731
        return f"#{mix(r):02x}{mix(g):02x}{mix(b):02x}"

    def _toggle_group(self, group):
        stem = group["stem"]
        if stem in self.sel_groups:
            self.sel_groups.discard(stem)
        else:
            self.sel_groups.add(stem)
            # Taking the whole bundle supersedes any individual picks in it.
            for h, _n in group["members"]:
                self.sel_levels.discard(h)
        self._style_group(stem)
        for h, _n in group["members"]:
            self._style_member(h)
        self._update_count()

    def _toggle_level(self, h):
        touched = set()
        if h in self.sel_levels:
            self.sel_levels.discard(h)
        else:
            self.sel_levels.add(h)
            for g in self.groups:
                if any(m[0] == h for m in g["members"]):
                    if g["stem"] in self.sel_groups:
                        self.sel_groups.discard(g["stem"])
                        touched.add(g["stem"])
        self._style_member(h)
        for stem in touched:
            self._style_group(stem)
            for hh, _n in next(g for g in self.groups
                               if g["stem"] == stem)["members"]:
                self._style_member(hh)
        self._update_count()

    def _restyle_all(self):
        for stem in self._gw:
            self._style_group(stem)
        for h in self._mw:
            self._style_member(h)

    def _select_all(self):
        """Toggle: a second press clears, rather than doing nothing."""
        everything = {g["stem"] for g in self.groups}
        if self.sel_groups == everything and not self.sel_levels:
            self.sel_groups.clear()
        else:
            self.sel_levels.clear()
            self.sel_groups = set(everything)
        self._restyle_all()
        self._update_count()

    def _clear_sel(self):
        self.sel_groups.clear()
        self.sel_levels.clear()
        self._restyle_all()
        self._update_count()

    def _update_count(self):
        jobs = self._plan()
        if hasattr(self, "count_label") and self.count_label.winfo_exists():
            self.count_label.configure(
                text=f"{len(jobs)} package(s) selected.")

    # ------------------------------------------------------ page: models
    def _page_models(self):
        """Pick individual models by hash.

        Rigged models sort first: a model either has a `CSkeletonResource` at
        its own hash or has none, and the rigged ones are the characters and
        props worth extracting on their own.
        """
        self._clear()
        self.h_title.configure(text="Single models")

        if not self.models:
            try:
                if str(SCRIPTS) not in sys.path:
                    sys.path.insert(0, str(SCRIPTS))
                import evr_model_extract as ME
                root = Path(self.source.get())
                rigged = set(ME.list_models(root, only_skeleton=True))
                self.models = sorted(
                    ((h, h in rigged) for h in ME.list_models(root)),
                    key=lambda kv: (not kv[1], kv[0]))
            except Exception as exc:                        # noqa: BLE001
                self._set_status(f"Could not list models: {exc}", BAD)
                self.models = []

        rigged_n = sum(1 for _h, r in self.models if r)
        self.h_sub.configure(
            text=f"{len(self.models)} models \u00b7 {rigged_n} with an armature. "
                 f"Mesh names are not recoverable from the shipped data, so "
                 f"these are hashes.")

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(0, 10))
        self.model_search = tk.StringVar()
        ttk.Entry(bar, textvariable=self.model_search).pack(
            side="left", fill="x", expand=True)
        ttk.Checkbutton(bar, text="Rigged only", variable=self.rigged_only,
                        command=self._paint_models).pack(side="left", padx=(10, 0))
        ttk.Button(bar, text="Clear", style="Ghost.TButton",
                   command=self._clear_models).pack(side="left", padx=(8, 0))

        wrap = tk.Frame(self.body, bg=BG_PANEL)
        wrap.pack(fill="both", expand=True)
        self.mcanvas = tk.Canvas(wrap, bg=BG_PANEL, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.mcanvas.yview)
        self.mframe = tk.Frame(self.mcanvas, bg=BG_PANEL)
        self.mframe.bind("<Configure>", lambda _e: self.mcanvas.configure(
            scrollregion=self.mcanvas.bbox("all")))
        self.mcanvas.create_window((0, 0), window=self.mframe, anchor="nw",
                                   tags="minner")
        self.mcanvas.bind("<Configure>", lambda e: self.mcanvas.itemconfigure(
            "minner", width=e.width))
        self.mcanvas.configure(yscrollcommand=sb.set)
        self.mcanvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.model_search.trace_add("write", lambda *_a: self._paint_models())
        self._paint_models()

        self.mcount = ttk.Label(self.body, style="Dim.TLabel")
        self.mcount.pack(anchor="w", pady=(10, 0))
        self._update_model_count()
        self._nav(back=self._page_pick, forward=self._page_options,
                  forward_text="Options \u2192")

    #: Rows built per repaint. 2,521 models would make 2,521 widget sets and
    #: freeze the window, so the list is capped and the search box narrows it.
    MODEL_ROW_LIMIT = 400

    def _paint_models(self):
        for w in self.mframe.winfo_children():
            w.destroy()
        needle = self.model_search.get().strip().lower()
        shown = 0
        for h, rigged in self.models:
            if self.rigged_only.get() and not rigged:
                continue
            if needle and needle not in h:
                continue
            if shown >= self.MODEL_ROW_LIMIT:
                break
            picked = h in self.sel_models
            hue = GROUP_HUES[2] if rigged else GROUP_HUES[7]
            bg = self._tint(hue) if picked else BG_PANEL
            row = tk.Frame(self.mframe, bg=bg)
            row.pack(fill="x", padx=14, pady=1)
            tk.Frame(row, bg=hue if picked else LINE, width=3).pack(
                side="left", fill="y")
            tk.Label(row, text=h, bg=bg, fg=FG if picked else FG_MID,
                     font=("Consolas", 10)).pack(side="left", padx=(12, 0), pady=5)
            if rigged:
                tk.Label(row, text="armature", bg=bg, fg=hue,
                         font=("Segoe UI", 9)).pack(side="left", padx=(12, 0))
            for w in (row, *row.winfo_children()):
                w.configure(cursor="hand2")
                w.bind("<Button-1>", lambda _e, hh=h: self._toggle_model(hh))
            shown += 1
        if shown >= self.MODEL_ROW_LIMIT:
            tk.Label(self.mframe, bg=BG_PANEL, fg=FG_DIM, font=("Segoe UI", 9),
                     text=f"showing the first {self.MODEL_ROW_LIMIT} "
                          f"\u2014 use the search box to narrow").pack(
                              anchor="w", padx=20, pady=8)

    def _toggle_model(self, h):
        if h in self.sel_models:
            self.sel_models.discard(h)
        else:
            self.sel_models.add(h)
        self._paint_models()
        self._update_model_count()

    def _clear_models(self):
        self.sel_models.clear()
        self._paint_models()
        self._update_model_count()

    def _update_model_count(self):
        if hasattr(self, "mcount") and self.mcount.winfo_exists():
            self.mcount.configure(text=f"{len(self.sel_models)} model(s) selected.")

    # ----------------------------------------------------- page: options
    def _page_options(self):
        self._clear()
        self.h_title.configure(text="Output options")
        self.h_sub.configure(text="Lights and textures are always included.")

        tex = tk.Frame(self.body, bg=BG_PANEL)
        tex.pack(fill="x")
        tk.Label(tex, text="Texture size", bg=BG_PANEL, fg=FG,
                 font=("Segoe UI Semibold", 12)).pack(anchor="w", padx=24,
                                                      pady=(22, 2))
        tk.Label(tex, bg=BG_PANEL, fg=FG_DIM, font=("Segoe UI", 9),
                 wraplength=940, justify="left",
                 text="Blender uploads textures DECOMPRESSED, so a 2048 map that "
                      "is 2.7 MB on disk costs about 16 MB of VRAM. Capping drops "
                      "the top of the mip chain \u2014 exact, never resampled."
                 ).pack(anchor="w", padx=24, pady=(0, 8))
        for value, label, hint in TEXTURE_CHOICES:
            line = tk.Frame(tex, bg=BG_PANEL)
            line.pack(fill="x", padx=24, pady=1)
            ttk.Radiobutton(line, text=label, value=value,
                            variable=self.texture).pack(side="left")
            tk.Label(line, text=hint, bg=BG_PANEL, fg=FG_DIM,
                     font=("Segoe UI", 9)).pack(side="left", padx=(14, 0))
        tk.Frame(tex, bg=BG_PANEL, height=16).pack()

        out = tk.Frame(self.body, bg=BG_PANEL)
        out.pack(fill="x", pady=(16, 0))
        tk.Label(out, text="Where should it go?", bg=BG_PANEL, fg=FG,
                 font=("Segoe UI Semibold", 12)).pack(anchor="w", padx=24,
                                                      pady=(22, 8))
        ttk.Checkbutton(out, style="Panel.TCheckbutton",
                        text=f"  Default \u2014 beside the source, in "
                             f"\\{self.game.key}\\",
                        variable=self.use_default_out,
                        command=self._sync_out).pack(anchor="w", padx=22)
        pick = ttk.Frame(out, style="Panel.TFrame")
        pick.pack(fill="x", padx=24, pady=(12, 22))
        self.out_entry = ttk.Entry(pick, textvariable=self.outdir)
        self.out_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(pick, text="Browse", style="Ghost.TButton",
                   command=self._browse_out).pack(side="left", padx=(10, 0))
        self._sync_out()

        self.est = tk.Label(self.body, bg=BG, fg=FG_DIM, font=("Segoe UI", 10),
                            justify="left")
        self.est.pack(anchor="w", pady=(18, 0))
        self._estimate()
        self._nav(back=self._page_pick, forward=self._begin,
                  forward_text="Start extraction \u2192")

    def _sync_out(self):
        if self.use_default_out.get():
            # For Lone Echo 1 `source` is the GAME folder, so the default must
            # not land beside a game install -- put it next to the app instead.
            base = (APP_ROOT if self.game.install_only
                    else Path(self.source.get()).parent)
            self.outdir.set(str(Path(base) / self.game.key))
            self.out_entry.state(["disabled"])
        else:
            self.out_entry.state(["!disabled"])

    def _browse_out(self):
        chosen = filedialog.askdirectory(title="Export folder")
        if chosen:
            self.use_default_out.set(False)
            self.outdir.set(chosen)
            self._sync_out()

    def _plan(self) -> list:
        jobs = []
        by_stem = {g["stem"]: g for g in self.groups}
        for stem in sorted(self.sel_groups):
            g = by_stem.get(stem)
            if not g:
                continue
            multi = len(g["members"]) > 1
            jobs.append(Job("group" if multi else "scene", g["parent"], g["label"]))
        names = dict(self.levels)
        for h in sorted(self.sel_levels):
            jobs.append(Job("scene", h, names.get(h) or h))
        if self.want_models.get():
            for job in list(jobs):
                jobs.append(Job("models", job.level, job.label))
        for h in sorted(self.sel_models):
            jobs.append(Job("model", h, h))
        return jobs

    def _estimate(self):
        jobs = self._plan()
        cap = self.texture.get()
        scale = {512: 0.12, 1024: 0.3, 2048: 0.62}.get(cap, 1.0) if cap else 1.0
        total = sum((EST_MODEL_BYTES if j.kind == "models" else EST_SCENE_BYTES)
                    * scale for j in jobs)
        try:
            free = shutil.disk_usage(Path(self.outdir.get()).anchor).free
        except (OSError, ValueError):
            free = 0
        ok = free > total * 1.15
        if hasattr(self, "est") and self.est.winfo_exists():
            self.est.configure(
                text=(f"{len(jobs)} package(s) \u00b7 estimated {human(total)} "
                      f"\u00b7 {human(free)} free"
                      + ("" if ok else "   \u2014 NOT ENOUGH SPACE")),
                fg=FG_DIM if ok else BAD)
        return total, free, ok

    # ---------------------------------------------------------- extraction
    def _begin(self):
        jobs = self._plan()
        if not jobs:
            messagebox.showwarning("Nothing selected",
                                   "Pick at least one bundle or level first.")
            return
        total, free, ok = self._estimate()
        if not ok and not messagebox.askyesno(
                "Not enough space",
                f"This needs roughly {human(total)} but only {human(free)} is free "
                f"on that drive.\n\nStart anyway?"):
            return
        Path(self.outdir.get()).mkdir(parents=True, exist_ok=True)
        self._remember()
        self._start(jobs)

    def _start(self, jobs, raw_tool=False):
        self._clear()
        self.h_title.configure(text="Extracting")
        self.h_sub.configure(text=f"{len(jobs)} package(s) to go.")
        self.progress.pack(fill="x", pady=(6, 4))
        self.progress.configure(maximum=max(len(jobs), 1), value=0)

        self.job_label = ttk.Label(self.body, text="Starting\u2026",
                                   style="H2.TLabel")
        self.job_label.pack(anchor="w", pady=(2, 10))
        self.log = tk.Text(self.body, bg=BG_PANEL, fg=FG_MID, height=18,
                           font=("Consolas", 9), relief="flat", wrap="none",
                           insertbackground=FG, padx=14, pady=10)
        self.log.pack(fill="both", expand=True)

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(16, 0))
        self.btn_cancel = ttk.Button(bar, text="Cancel", style="Ghost.TButton",
                                     command=self._do_cancel)
        self.btn_cancel.pack(side="left")
        self.btn_done = ttk.Button(bar, text="Done", style="Accent.TButton",
                                   command=self._page_pick)
        self.btn_done.pack(side="right")
        self.btn_done.state(["disabled"])

        self._cancel.clear()
        self._worker = threading.Thread(target=self._run, args=(jobs, raw_tool),
                                        daemon=True)
        self._worker.start()

    def _do_cancel(self):
        """Stop for real: flag the loop AND kill the running child tree."""
        self._cancel.set()
        self.btn_cancel.state(["disabled"])
        self.job_label.configure(text="Cancelling\u2026")
        with self._proc_lock:
            kill_tree(self._proc)
        self._queue.put(("log", "\n-- cancelled by user --\n"))

    def _run(self, jobs, raw_tool):
        put = self._queue.put
        done = 0
        for job in jobs:
            if self._cancel.is_set():
                break
            put(("job", f"{job.label}   \u00b7   {job.kind}"))
            cmd = job.args if raw_tool else self._command(job)
            put(("log", f"\n$ {' '.join(str(c) for c in cmd)}\n"))
            try:
                proc = subprocess.Popen(
                    cmd, cwd=str(REPO), stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                    errors="replace", bufsize=1,
                    # Lone Echo 1 needs PYTHONPATH/pyoodle and the Oodle DLL
                    # on every job, not only the bulk tool run -- its per-archive
                    # extraction is the same le_extract.py call.
                    env=(getattr(self, "_tool_environ", None)
                         if (raw_tool or self.game.install_only) else None),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError as exc:
                put(("log", f"  failed to start: {exc}\n"))
                done += 1
                put(("tick", done))
                continue
            with self._proc_lock:
                self._proc = proc
            for line in proc.stdout:
                if line.strip():
                    put(("log", line))
            proc.wait()
            with self._proc_lock:
                self._proc = None
            if self._cancel.is_set():
                break
            if proc.returncode:
                put(("log", f"  exited with code {proc.returncode}\n"))
            elif not raw_tool:
                self._light(job, put)
                self._ui(job, put)
                self._le1_post(job, put)
            done += 1
            put(("tick", done))
        if raw_tool:
            self.levels, self.groups = [], []
        self.all_levels, self.stubs = [], set()
        put(("finish", done))

    def _command(self, job) -> list:
        if self.game.install_only:
            # Lone Echo 1 has no flat tree for `evr_scene_extract` to read, and
            # its two products come from DIFFERENT tools: a scene is a
            # `.lescatter` package built from the static-scatter master, a mesh
            # extraction is individual models out of an archive. Both run with
            # pyoodle and the game DLL on the environment.
            out = Path(self.outdir.get())
            if self.le1_mode.get() == "scenes":
                cmd = [sys.executable, str(SCRIPTS / "le_scene_extract.py"),
                       job.level, "--out", str(self._le1_pkg_dir(job)),
                       "--lightmap-textures"]
                if self.le1_instance_lm.get():
                    cmd.append("--instance-lightmap")
                return cmd
            return [sys.executable,
                    str(REPO / "blender_tool" / "extractor" / "le_extract.py"),
                    "--archive", job.level, "--all",
                    "--out", str(out / "meshes"),
                    "--textures", "--direct-materials"]
        if job.kind == "model":
            # A standalone model is its own extractor: same package format,
            # plus the skeleton sidecar when the model carries an armature.
            cmd = [sys.executable, str(SCRIPTS / "evr_model_extract.py"),
                   job.level, "--dir", self.source.get(),
                   "--out", str(Path(self.outdir.get()) / "models")]
            cap = self.texture.get()
            if cap:
                cmd += ["--max-texture", str(cap)]
            return cmd
        cmd = [sys.executable, str(SCRIPTS / "evr_scene_extract.py"), job.level,
               "--dir", self.source.get(), "--out", self.outdir.get()]
        if job.kind == "group":
            cmd.append("--full")
        if job.kind == "models":
            cmd.append("--geo")
        cap = self.texture.get()
        if cap:
            cmd += ["--max-texture", str(cap)]
        return cmd

    def _package_for(self, job):
        """The package THIS job just wrote, or None.

        `evr_scene_extract` writes `<out>/Scenes_Full/<label>` when a level
        merges sublevels and `<out>/scenes/<label>` otherwise, naming the folder
        from its OWN table -- so the app cannot just assume one path.

        Taking the first candidate that merely EXISTS is what went wrong before:
        once both trees hold a package for the same level, a single-level job
        would light the MERGED package and leave its own unlit. Candidates are
        therefore ordered by what this job asked for, kept only when their
        `manifest.json` names this level as `master`, and the most recently
        written of those wins -- the extractor finished seconds ago.
        """
        out = Path(self.outdir.get())
        # The tree this job asked for decides, and only falls through when it
        # holds nothing: `--full` on a level that turns out to have no
        # sublevels still lands in `scenes/`. mtime breaks ties WITHIN a tree
        # (name vs hash), never across them -- letting it decide across trees
        # would send a group job to the single-level package.
        trees = (["Scenes_Full", "scenes"] if job.kind == "group"
                 else ["scenes", "Scenes_Full"])
        for tree in trees:
            best = None
            for leaf in (job.label, job.level):
                man = out / tree / leaf / "manifest.json"
                if not man.is_file():
                    continue
                try:
                    master = json.loads(man.read_text(encoding="utf-8")).get("master")
                except (OSError, ValueError):
                    continue
                if norm_hash(master) != norm_hash(job.level):
                    continue
                stamp = man.stat().st_mtime
                if best is None or stamp > best[0]:
                    best = (stamp, man.parent)
            if best:
                return best[1]
        return None

    def _ui(self, job, put):
        """Extract the level's UI canvases into the package.

        A level's screens -- scoreboards, the arena's big display, the panels on
        the tunnel mouths -- are `CUICanvasResource` quads placed by
        `CCanvasUICR`, and they live in NEITHER the scatter geometry nor the
        materials. `mpl_arena_a` has 108 placements over 23 canvases and none of
        it came across, because this step did not exist.

        Written INTO the package next to `manifest.json`, so the add-on finds it
        beside everything else rather than in a second location.
        """
        if self.game.install_only:
            return                    # Echo VR resource types; Lone Echo 1 has none
        if job.kind == "model":
            return                    # UI canvases are a level concept
        cand = self._package_for(job)
        if cand is not None:
            put(("log", f"  ui \u2192 {cand.name}\n"))
            cmd = [sys.executable, str(SCRIPTS / "evr_ui_extract.py"),
                   job.level, "--dir", self.source.get(),
                   "--out", str(cand / "ui")]
            cap = self.texture.get()
            if cap:
                cmd += ["--max-texture", str(cap)]
            try:
                proc = subprocess.run(
                    cmd, cwd=str(REPO), capture_output=True, text=True,
                    encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                for ln in [x for x in (proc.stdout or "").splitlines()
                           if "quad" in x or "canvas" in x][-2:]:
                    put(("log", f"  {ln.strip()}\n"))
                if proc.returncode != 0:
                    tail = [x for x in (proc.stderr or "").splitlines() if x.strip()]
                    put(("log", "  ui FAILED: %s\n"
                         % (tail[-1].strip() if tail else
                            "exit %d" % proc.returncode)))
            except OSError as exc:
                put(("log", f"  ui could not start: {exc}\n"))
            return

    def _le1_post(self, job, put):
        """Textures for a Lone Echo 1 scene -- the stage after the geometry.

        `le_scene_extract` writes geometry and the lightmap binding only. The
        per-material base-colour and normal DDS come from `le_scene_materials`,
        which resolves the scatter's `matidx`/`shdidx` pairs against the
        binding table and pulls the textures out of the master's PARENT
        archives -- 87 of them on the scene checked, because a scatter's
        textures almost never live in its own archive.

        Without this the package renders untextured, which is what "the
        textures did not extract" was: not a decode failure, a stage that was
        never run.
        """
        if not self.game.install_only or self.le1_mode.get() != "scenes":
            return
        pkg = self._le1_pkg_dir(job)
        if not (pkg / "manifest.json").is_file():
            put(("log", "  materials SKIPPED: no package at %s\n" % pkg))
            return
        put(("log", "  materials \u2192 %s\n" % pkg.name))
        cmd = [sys.executable, str(SCRIPTS / "le_scene_materials.py"), job.level,
               "--manifest", str(pkg / "manifest.json"),
               "--out-textures", str(pkg / "textures"),
               "--out-json", str(pkg / "materials.json")]
        try:
            proc = subprocess.run(
                cmd, cwd=str(REPO), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                env=getattr(self, "_tool_environ", None),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            put(("log", f"  materials could not start: {exc}\n"))
            return
        for ln in [x.strip() for x in (proc.stdout or "").splitlines()
                   if "extracted" in x or "pairs:" in x][-2:]:
            put(("log", f"  {ln}\n"))
        if proc.returncode != 0:
            tail = [x for x in (proc.stderr or "").splitlines() if x.strip()]
            put(("log", "  materials FAILED: %s\n"
                 % (tail[-1].strip() if tail else "exit %d" % proc.returncode)))

    def _light(self, job, put):
        """Baked lightmaps + placed lights, on EVERY level extraction.

        Runs for every scene and group job, not as a separate pass -- the
        package is only complete once its lighting is in it.
        """
        if self.game.install_only:
            return                    # Echo VR resource types; Lone Echo 1 has none
        if job.kind == "model":
            return                    # lighting is a level concept
        cand = self._package_for(job)
        if cand is None:
            put(("log", "  lighting SKIPPED: no package for %s under %s\n"
                 % (job.label, self.outdir.get())))
            return
        put(("log", "  lighting \u2192 %s/%s\n" % (cand.parent.name, cand.name)))
        try:
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "evr_apply_lighting.py"),
                 str(cand), job.level, "--dir", self.source.get()],
                cwd=str(REPO), capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            put(("log", f"  lighting could not start: {exc}\n"))
            return
        # The script's last line is its summary; show it whatever it says.
        lines = [x.strip() for x in (proc.stdout or "").splitlines() if x.strip()]
        summary = next((x for x in reversed(lines) if "atlas" in x), "")
        if summary:
            put(("log", f"  {summary}\n"))
        # Report failures. This step used to fail silently on EVERY run
        # (no --dir -> TypeError), so packages shipped unlit and nothing said so.
        if proc.returncode != 0:
            tail = [x for x in (proc.stderr or "").splitlines() if x.strip()]
            put(("log", "  lighting FAILED: %s\n"
                 % (tail[-1].strip() if tail else "exit %d" % proc.returncode)))
        elif summary.startswith("0 atlas"):
            # Not a failure: roughly a quarter of Echo VR levels ship an EMPTY
            # CGStaticInstanceResourceWin10GPU (8 of 32 in pcvr-extracted, 12 of
            # 36 in Summer, and four of them in BOTH), so there is no bake to
            # extract. The placed lights in the same line are still real. Say
            # which it is, because "0 atlas(es)" alone reads like a failure.
            put(("log", "  (no baked lightmaps for this level -- its "
                        "static-instance GPU resource is empty in the shipped "
                        "data; placed lights above are still applied)\n"))

    def _drain(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "log":
                    self.log.insert("end", payload)
                    self.log.see("end")
                elif kind == "job":
                    self.job_label.configure(text=payload)
                elif kind == "tick":
                    self.progress.configure(value=payload)
                elif kind == "finish":
                    stopped = self._cancel.is_set()
                    self.job_label.configure(
                        text=(f"Cancelled after {payload} package(s)." if stopped
                              else f"Finished \u2014 {payload} package(s)."))
                    self.btn_done.state(["!disabled"])
                    self.btn_cancel.state(["disabled"])
                    self._set_status(f"Output: {self.outdir.get()}",
                                     WARN if stopped else GOOD)
        except queue.Empty:
            pass
        self.after(80, self._drain)

    def _quit(self):
        self._remember()
        self._cancel.set()
        with self._proc_lock:
            kill_tree(self._proc)
        self.destroy()


def main() -> int:
    if not SCRIPTS.is_dir():
        print(f"Cannot find the pipeline scripts at {SCRIPTS}", file=sys.stderr)
        return 1
    EchoExtractor().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
