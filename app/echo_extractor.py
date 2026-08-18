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
    #: Where packages/archives live under the chosen data directory.
    archive_glob: str = ""


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
         "level_names.json", "pyoodle",
         "Reads the shipped archives directly through pyoodle and the game's "
         "own oodle_11_win64.dll \u2014 no flat-tree step.", "pyoodle",
         "primary/e5bd8207135b8887/v13363680368"),
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
    def norm(name):
        stem = name.split(".")[0].lower()
        return stem.rjust(16, "0") if len(stem) <= 16 else stem

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
        self.sel_groups: set = set()      # stems selected whole -> merged
        self.sel_levels: set = set()      # individual level hashes
        self.want_models = tk.BooleanVar(value=False)
        self.models: list = []
        self.sel_models: set = set()
        self.rigged_only = tk.BooleanVar(value=True)
        self.game_data = tk.StringVar()
        self.oodle_dll = tk.StringVar()

        self._queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._cancel = threading.Event()
        self._proc = None                 # the live child, for a real cancel
        self._proc_lock = threading.Lock()

        self.settings = load_settings()
        self.texture.set(int(self.settings.get("texture") or 1024))
        self.oodle_dll.set(self.settings.get("oodle_dll") or "")

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
        archive_dir = Path(data_dir) / self.game.archive_glob
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
        if not looks_like_extract(self.source.get() or ".")[0]:
            self._validate_source()
            return
        if not self.groups or not self.levels:
            self.levels = discover_levels(self.source.get(), self.game.names_file)
            self.groups = group_levels(self.levels)
        self._clear()
        self.h_title.configure(text="Choose levels")
        bundles = sum(1 for g in self.groups if len(g["members"]) > 1)
        self.h_sub.configure(
            text=f"{len(self.levels)} level(s) \u00b7 {bundles} bundle(s). "
                 f"Click a bundle to take it merged, or pick levels inside it.")

        bar = ttk.Frame(self.body)
        bar.pack(fill="x", pady=(0, 10))
        self.search = tk.StringVar()
        ttk.Entry(bar, textvariable=self.search).pack(side="left", fill="x",
                                                      expand=True)
        ttk.Button(bar, text="Select all", style="Ghost.TButton",
                   command=self._select_all).pack(side="left", padx=(10, 0))
        ttk.Button(bar, text="Clear", style="Ghost.TButton",
                   command=self._clear_sel).pack(side="left", padx=(8, 0))

        wrap = tk.Frame(self.body, bg=BG_PANEL)
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg=BG_PANEL, highlightthickness=0)
        sb = ttk.Scrollbar(wrap, orient="vertical", command=self.canvas.yview)
        self.list_frame = tk.Frame(self.canvas, bg=BG_PANEL)
        self.list_frame.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.list_frame, anchor="nw",
                                  tags="inner")
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure("inner", width=e.width))
        self.canvas.configure(yscrollcommand=sb.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.canvas.bind_all(
            "<MouseWheel>",
            lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))

        self.search.trace_add("write", lambda *_a: self._paint())
        self._paint()

        models = tk.Frame(self.body, bg=BG_PANEL)
        models.pack(fill="x", pady=(12, 0))
        ttk.Checkbutton(models, style="Panel.TCheckbutton",
                        variable=self.want_models,
                        text="  Also extract standalone models for the selected "
                             "levels").pack(anchor="w", padx=20, pady=12)

        ttk.Button(models, text="Single models\u2026", style="Ghost.TButton",
                   command=self._page_models).pack(side="right", padx=20, pady=8)

        self.count_label = ttk.Label(self.body, style="Dim.TLabel")
        self.count_label.pack(anchor="w", pady=(10, 0))
        self._update_count()
        self._nav(back=self._page_source, forward=self._page_options,
                  forward_text="Options \u2192")

    def _paint(self):
        """Rebuild the list. Only for a filter change -- NOT for selection.

        Selecting used to call this, which destroyed and recreated every row:
        the whole list visibly flashed and the scroll position jumped. Toggles
        now restyle the existing widgets instead, so nothing is rebuilt.
        """
        for w in self.list_frame.winfo_children():
            w.destroy()
        self._gw = {}          # stem -> the group's header widgets
        self._mw = {}          # level hash -> that member's row widgets
        needle = self.search.get().strip().lower()
        for i, group in enumerate(self.groups):
            hue = GROUP_HUES[i % len(GROUP_HUES)]
            members = group["members"]
            if needle:
                hit = (needle in group["label"].lower()
                       or any(needle in (n or h).lower() for h, n in members))
                if not hit:
                    continue
            self._paint_group(group, hue)

    def _paint_group(self, group, hue):
        members = group["members"]
        multi = len(members) > 1

        box = tk.Frame(self.list_frame, bg=BG_PANEL)
        box.pack(fill="x", padx=14, pady=(8, 0))

        head = tk.Frame(box, bg=BG_CARD, highlightthickness=1,
                        highlightbackground=LINE)
        head.pack(fill="x")
        tk.Frame(head, bg=hue, width=4).pack(side="left", fill="y")

        title = tk.Label(head, text=group["label"], bg=BG_CARD,
                         font=("Segoe UI Semibold", 11) if multi
                         else ("Segoe UI", 10))
        title.pack(side="left", padx=(14, 0), pady=10)
        badge = tk.Label(head, bg=BG_CARD, fg=hue, font=("Segoe UI", 9),
                         text=(f"bundle \u00b7 {len(members)} levels" if multi
                               else "single level"))
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
            w.bind("<Enter>", lambda _e, s=group["stem"]: self._hover_group(s, True))
            w.bind("<Leave>", lambda _e, s=group["stem"]: self._hover_group(s, False))
        self._style_group(group["stem"])

        if multi:
            for h, name in members:
                self._paint_member(box, h, name, hue, group)

    def _paint_member(self, box, h, name, hue, group):
        row = tk.Frame(box, bg=BG_PANEL)
        row.pack(fill="x", padx=(22, 0))
        edge = tk.Frame(row, bg=LINE, width=2)
        edge.pack(side="left", fill="y")
        label = tk.Label(row, text=name or f"{h}  (unnamed)", bg=BG_PANEL,
                         font=("Segoe UI", 10) if name else ("Consolas", 9),
                         anchor="w")
        label.pack(side="left", fill="x", expand=True, padx=(12, 0), pady=6)

        self._mw[h] = {"row": row, "edge": edge, "label": label, "hue": hue,
                       "stem": group["stem"]}
        for w in (row, label):
            w.configure(cursor="hand2")
            w.bind("<Button-1>", lambda _e, hh=h: self._toggle_level(hh))
            w.bind("<Enter>", lambda _e, hh=h: self._hover_member(hh, True))
            w.bind("<Leave>", lambda _e, hh=h: self._hover_member(hh, False))
        self._style_member(h)

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
            self.outdir.set(str(Path(self.source.get()).parent / self.game.key))
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
                    env=(getattr(self, "_tool_environ", None) if raw_tool
                         else None),
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
            done += 1
            put(("tick", done))
        if raw_tool:
            self.levels, self.groups = [], []
        put(("finish", done))

    def _command(self, job) -> list:
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

    def _light(self, job, put):
        if job.kind == "model":
            return                    # lighting is a level concept
        pkg = Path(self.outdir.get())
        for cand in (pkg / "Scenes_Full" / job.label, pkg / "scenes" / job.label,
                     pkg / "scenes" / job.level):
            if (cand / "manifest.json").is_file():
                put(("log", f"  lighting \u2192 {cand.name}\n"))
                try:
                    proc = subprocess.run(
                        [sys.executable, str(SCRIPTS / "evr_apply_lighting.py"),
                         str(cand), job.level],
                        cwd=str(REPO), capture_output=True, text=True,
                        encoding="utf-8", errors="replace",
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    for ln in [x for x in (proc.stdout or "").splitlines()
                               if "atlas" in x or "lights" in x][-2:]:
                        put(("log", f"  {ln.strip()}\n"))
                except OSError:
                    pass
                return

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
