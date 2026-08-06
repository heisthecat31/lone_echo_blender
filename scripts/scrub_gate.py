#!/usr/bin/env python3
"""scrub_gate — release gate: this repository must leak nothing private.

Scans **every git-tracked file** (text and binary alike — PNG metadata chunks and
zip members are just bytes) for things that must never reach a public,
MIT-licensed mirror of an otherwise private reverse-engineering project:

  * usernames and absolute home paths (`C:\\Users\\…`, `/mnt/c/Users/…`,
    `/home/<user>`, `/Users/<user>`)
  * any private literal supplied at run time -- a username, a private repository
    name, a private data-tree name (see PRIVATE LITERALS below)
  * private-tree artefacts: debug-symbol paths and `.pdb` / `.exe` / `.dll`
    filenames
  * references to the private research log (session numbers, ledger entries)
  * credentials: API keys, tokens, private keys, account ids, and routable IPs
  * committed game bytes: anything the `.gitignore` says must never be committed
    (`exports/`, `dist/`, `*.lemesh/`, `*.lescatter/`, `*.dll`) that is
    nevertheless tracked
  * game bytes **pasted inside a source file** — a base64 / hex / `\\x..` blob
    large enough to be a real asset slice (see EMBEDDED BLOBS below)

Exit code is 0 when clean and 1 when anything is found; every finding is printed
as `path:line: <rule> — <evidence>` so it can be fixed directly.

    python3 scripts/scrub_gate.py           # gate the tracked tree
    python3 scripts/scrub_gate.py --self-test
    python3 scripts/scrub_gate.py --paths a.py b.md     # gate specific files

PRIVATE LITERALS -- SUPPLIED, NEVER STORED
-----------------------------------------
This file deliberately contains **no** username and **no** private repository or
data-tree name. A gate that embeds the literal it hunts for becomes the leak the
moment it is published, so the sensitive strings are supplied at run time:

    SCRUB_PRIVATE_LITERALS="myname,my_private_repo,my_data_tree"   # env
    .scrub_private                                                 # gitignored file

With none configured the gate still catches every absolute home path, debug-symbol
artefact, session reference, credential and forbidden binary -- it simply cannot
catch a bare mention of a name it was never told. Use `--require-literals` (CI) to
make configuring them mandatory.

WHAT IS DELIBERATELY ALLOWED
----------------------------
Game-asset **content hashes** (16 lowercase hex characters, e.g.
`0703fd2acd5803e9`) are identifiers, not secrets. They name resources inside a
copy of the game the user already owns, they carry no key material, and they have
been published since 0.1.0. The gate must not flag them, which is why the
credential rules below are anchored to key/token *prefixes* and to `key=`-style
assignments rather than to bare hex runs.

EMBEDDED BLOBS -- THE RULE THE PATH RULES CANNOT SEE
----------------------------------------------------
Every other defence against shipping game bytes is a **path** rule: `.gitignore`,
`FORBIDDEN_PATHS` (`exports/`, `dist/`, `*.lemesh/`, `*.lescatter/`), and
`check_paths()` running before any content scan. A slice of a shipped asset
pasted **inside a `.py` file** -- as a `base64.b64decode(...)` decode fixture,
say -- is on none of those paths, so none of them look at it. That is exactly the
natural thing for a decode author to do (a byte-exact fixture is how you test a
byte-exact decoder) and exactly what must not cross into a public MIT mirror.

`scan_embedded_blobs()` therefore measures the **decoded size** of every literal
in a shipped source file and flags anything at or above `BLOB_THRESHOLD` bytes:
`base64.b64decode` / `binascii.a2b_base64` payloads, `bytes.fromhex` /
`unhexlify` payloads, bare `b"..."` byte literals, and long base64/hex runs in
non-Python text. A blob that is genuinely ours (a synthesised fixture, a public
spec vector) is kept with an explicit per-site allow carrying a stated reason:

    # scrub-allow: embedded-bytes — synthesised by tests/synthetic.py, no game bytes

The marker must appear within two lines of the literal, and the reason may not be
empty. Silence is never an allow.
"""
from __future__ import annotations

import argparse
import ast
import base64
import os
import re
import subprocess
import sys
from pathlib import Path

# Windows consoles default to cp1252 and argparse echoes this module's docstring
# on --help, so any non-ASCII in it raises UnicodeEncodeError the moment stdout
# is not a console (a pipe, a redirect, CI). Force UTF-8 on the streams we own.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):   # already-wrapped or non-reconfigurable
        pass

REPO = Path(__file__).resolve().parents[1]

# Files the gate is allowed to skip when looking for *its own* rule strings: this
# file necessarily contains every pattern it searches for.
SELF = "scripts/scrub_gate.py"

# --- rules -------------------------------------------------------------------
# (rule name, compiled pattern, human explanation). Patterns are matched against
# each line decoded as latin-1, so they work on binary files too.

def _rx(p: str) -> re.Pattern:
    return re.compile(p, re.IGNORECASE)


RULES: list[tuple[str, re.Pattern, str]] = [
    # -- usernames and absolute home paths ----------------------------------
    # NOTE: there is deliberately no hard-coded username or private-tree name
    # here. A gate that embeds the literal it hunts for *is itself the leak* once
    # published. The structural rules below catch ANY username in an absolute
    # path (which is how these strings actually appear), and bare occurrences are
    # covered by the environment-supplied literals in `private_literal_rules()`.
    ("windows-home", _rx(r"[a-z]:[\\/]+users[\\/]+[^\\/\s\"'<>|]+"),
     "absolute Windows user path"),
    ("wsl-home", _rx(r"/mnt/[a-z]/users/[^/\s\"']+"),
     "absolute WSL path into a Windows user directory"),
    ("posix-home", _rx(r"/(?:home|users)/(?!<|\$|user\b|username\b|you\b)[a-z0-9_.-]+/"),
     "absolute POSIX home path"),

    # -- private-tree reverse-engineering artefacts --------------------------
    ("debug-symbols", _rx(r"pdb_work|archive_debug|r14\.pdb|libr15\.so"),
     "private debug-symbol working directory or binary"),
    # Any .pdb/.exe/.dll filename EXCEPT the three the project legitimately has to
    # name: the interpreters you run the extractor and the render harness with, and
    # the Oodle runtime the user must supply themselves (never shipped — see the
    # `proprietary-binary` path rule, which is what actually stops one being
    # committed). `.pdb` has no exemption at all.
    ("binary-filename",
     _rx(r"[\w./\\-]*(?<!\bpython)(?<!\bblender)(?<=\w)\.exe\b"
         r"|[\w./\\-]*(?<!\boodle_11_win64)(?<=\w)\.dll\b"
         r"|[\w./\\-]*(?<=\w)\.pdb\b"),
     "a .pdb / .exe / .dll filename (no proprietary binary may be named or shipped)"),

    # -- the private research log --------------------------------------------
    ("session-note", _rx(r"\bsessions?[ _-]?\d+|session-n\d+|\bn\d{2,3}\b(?=\s+(?:handoff|result))"),
     "reference to a numbered private research session"),
    ("ledger", _rx(r"do-not-relitigate|generic_rebuilds/session"),
     "reference to the private research ledger"),
    # A git commit id from the private history is the same class as a session
    # number: it names a revision of a tree nobody outside can read, so it is a
    # dangling citation publicly and a pointer to private work privately.
    # Anchored to backticks and required to mix letters and digits so that
    # neither a decimal count (`1048576`) nor a binary literal (`0b00011`) nor a
    # 16-hex game-asset id can match.
    ("vcs-ref",
     _rx(r"`(?=[0-9a-f]{7,10}`)(?=[0-9a-f]*[a-f])(?=[0-9a-f]*[0-9])"
         r"(?!0[bx])[0-9a-f]{7,10}`"),
     "a git commit id from the private history"),

    # -- the game's own source tree, debug headers and shader disassembly -----
    # ★ THE RULE THE 0.4.0 AUDIT ADDED, and why it is a rule rather than a
    # one-off fix. Published 0.1.0-0.3.0 contain ZERO citations of this class:
    # they name the engine by its own SYMBOL names (`kLambdaSG5`,
    # `CGVertexFormat::EUsage`, `DiffuseTermSG`) — which is a fact about the
    # shipped bytes and is what this repository is for — and never by a path or
    # line number into a source tree, a debug header or a disassembly listing,
    # which is a fact about how the reverse engineering was done. The 0.4.0
    # candidate arrived carrying 172 `*.hlsl:NNN` citations, 25 `sourcedb/…`
    # paths and 3 `<header>.h@<build-id>:<line>` references; every existing rule
    # passed them, because every existing rule was looking for the *private*
    # tree's artefacts and these name the *game's*.
    ("engine-source",
     _rx(r"sourcedb[/\\]"
         r"|[\w./\\-]*\.(?:hlsl|radmat|usf|fx)\b"
         r"|\b\w+\.h@\d+"
         r"|\b[pvcghd]s_5_\d\b"
         r"|\bdcl_(?:constantbuffer|resource_texture|temps|input_ps|output)\b"),
     "a path, line number or disassembly listing from the game's own source tree, "
     "debug headers or compiled shaders (cite the engine's symbol names instead)"),

    # -- credentials ----------------------------------------------------------
    ("private-key", _rx(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
     "private key material"),
    ("token", _rx(r"\b(?:ghp|gho|ghs|ghu|github_pat|sk-|xox[abprs]-|AKIA|ASIA)[A-Za-z0-9_-]{8,}"),
     "an API key or access token"),
    ("secret-assignment",
     _rx(r"\b(?:api[_-]?key|secret|password|passwd|access[_-]?token|auth[_-]?token|"
         r"client[_-]?secret|oculus[_-]?id|account[_-]?id)\s*[=:]\s*[\"']?[A-Za-z0-9_\-]{8,}"),
     "a credential or account id assigned in source"),
    ("ip-address", _rx(r"(?<![\w.])(?!0\.0\.0\.0|127\.0\.0\.1|255\.255\.255\.255)"
                       r"(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"),
     "a routable IP address"),
]

# `bytes` filters applied before the per-line pass, purely for speed on big blobs.
# `bytes` prefilter, purely for speed on big blobs. Generic substrings only -- no
# private literal appears here either; when private literals are configured the
# prefilter is skipped so nothing can slip past it.
NEEDLES = [b"users", b"Users", b"pdb", b"exe", b"dll", b"session", b"Session",
           b"relitigate", b"BEGIN", b"key", b"KEY", b"secret", b"token",
           b"password", b"home", b".",
           # `engine-source` and `vcs-ref` match tokens that need not contain a
           # dot (`ps_5_0`, `dcl_temps`, a backticked commit id), so the
           # prefilter has to know about them or it silently suppresses both.
           b"_5_", b"dcl_", b"sourcedb", b"hlsl", b"radmat", b"`"]

# --- files that must never be tracked at all --------------------------------
FORBIDDEN_PATHS = [
    ("game-bytes", re.compile(r"(^|/)(exports|dist)/"),
     "generated extractor output / build artefacts are gitignored and must not be committed"),
    ("game-bytes", re.compile(r"\.(lemesh|lescatter)/"),
     "an extracted game-data package must not be committed"),
    ("proprietary-binary", re.compile(r"\.(dll|exe|pdb|so|dylib)$", re.IGNORECASE),
     "no compiled or proprietary binary may be committed"),
]


# --- environment-supplied private literals -----------------------------------
# The strings that are actually sensitive for a given developer -- their username,
# the names of their private repositories and data trees -- are supplied at RUN
# TIME and never stored in this file. Provide them either way:
#
#   SCRUB_PRIVATE_LITERALS="myname,my_private_repo,my_data_tree"   (env, comma/newline)
#   .scrub_private                                                 (gitignored file,
#                                                                   one literal per line,
#                                                                   `#` comments allowed)
#
# With none configured the gate still runs and still catches every absolute home
# path, debug-symbol artefact, session reference, credential and forbidden binary
# -- it just cannot catch a bare mention of a name it was never told about. CI
# should set the variable; `--require-literals` makes that mandatory.
PRIVATE_LITERALS_FILE = REPO / ".scrub_private"


def private_literals() -> list[str]:
    """Sensitive literals from the environment and the gitignored local file."""
    raw = os.environ.get("SCRUB_PRIVATE_LITERALS", "")
    items = [t.strip() for t in re.split(r"[,\n]", raw)]
    if PRIVATE_LITERALS_FILE.is_file():
        for ln in PRIVATE_LITERALS_FILE.read_text(encoding="utf-8").splitlines():
            ln = ln.split("#", 1)[0].strip()
            if ln:
                items.append(ln)
    return [t for t in items if t]


def private_literal_rules(literals: list[str]) -> list[tuple[str, re.Pattern, str]]:
    """One rule matching any configured private literal (never stored in source)."""
    if not literals:
        return []
    pat = "|".join(re.escape(t) for t in literals)
    return [("private-literal", _rx(pat),
             "a configured private literal (username / private repo / data tree)")]


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files"],
                         capture_output=True, text=True, check=True)
    return [p for p in out.stdout.splitlines() if p]


def scan_bytes(path: str, data: bytes, *, skip_self: bool = True,
               extra_rules: list | None = None) -> list[str]:
    """Findings for one file's bytes, as `path:line: rule — evidence` strings."""
    if skip_self and path == SELF:
        return []
    rules = RULES + (extra_rules or [])
    findings: list[str] = []
    # the prefilter is a speed optimisation over the GENERIC rules only; if private
    # literals are configured we must scan every byte or one could slip past it.
    if not extra_rules and not any(n in data for n in NEEDLES):
        return findings
    for lineno, raw in enumerate(data.split(b"\n"), start=1):
        if len(raw) > 4096:                      # a minified/binary run: chunk it
            raw = raw[:4096]
        line = raw.decode("latin-1")
        for name, pattern, why in rules:
            m = pattern.search(line)
            if m:
                evidence = m.group(0)
                if len(evidence) > 80:
                    evidence = evidence[:77] + "..."
                findings.append(f"{path}:{lineno}: {name} — {why}: {evidence!r}")
    return findings


# --- embedded game bytes -----------------------------------------------------
# A blob inside a source file is on NO path rule. See the module docstring.

#: decoded size, in bytes, at or above which an embedded literal must justify
#: itself. 256 B is far larger than any legitimate magic number, sentinel or
#: spec test vector this project uses, and far smaller than any asset slice
#: worth pasting in as a decode fixture.
BLOB_THRESHOLD = 256

#: the per-site allow. The reason after the dash is mandatory.
ALLOW_MARKER = re.compile(
    r"scrub-allow:\s*embedded-bytes\s*[—:-]\s*(?P<reason>\S.*\S)")

#: decoders whose *argument* is the payload, with the divisor from encoded
#: characters to decoded bytes.
_DECODERS = {
    "b64decode": 4 / 3, "standard_b64decode": 4 / 3, "urlsafe_b64decode": 4 / 3,
    "a2b_base64": 4 / 3, "decodebytes": 4 / 3,
    "fromhex": 2.0, "unhexlify": 2.0, "a2b_hex": 2.0,
}

#: source suffixes worth parsing as Python; everything else gets the text sweep.
_PY_SUFFIXES = {".py", ".pyw"}

# long runs in non-Python text: base64 needs 4 chars per 3 bytes, hex 2 per 1.
_B64_RUN = re.compile(rb"[A-Za-z0-9+/]{%d,}={0,2}" % int(BLOB_THRESHOLD * 4 / 3))
_HEX_RUN = re.compile(rb"(?:[0-9a-fA-F]{2}){%d,}" % BLOB_THRESHOLD)
_ESC_RUN = re.compile(rb"(?:\\x[0-9a-fA-F]{2}){%d,}" % BLOB_THRESHOLD)


def _allowed_near(lines: list[str], lineno: int, end_lineno: int) -> bool:
    """True when an allow marker with a real reason sits by this literal."""
    lo = max(0, lineno - 3)
    hi = min(len(lines), end_lineno + 2)
    return any(ALLOW_MARKER.search(ln) for ln in lines[lo:hi])


def _decoded_size(node: ast.AST) -> int:
    """Bytes this literal expands to, or 0 when it is not a sized literal."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bytes):
            return len(node.value)
        if isinstance(node.value, str):
            return len(node.value)
    return 0


def _scan_python_blobs(path: str, text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []                     # not our file to judge; the text sweep still runs
    lines = text.splitlines()
    findings: list[str] = []
    flagged: set[tuple[int, int]] = set()

    def report(node: ast.AST, size: int, what: str) -> None:
        lineno = getattr(node, "lineno", 0)
        end = getattr(node, "end_lineno", lineno) or lineno
        if (lineno, end) in flagged:
            return
        if _allowed_near(lines, lineno, end):
            return
        flagged.add((lineno, end))
        findings.append(
            f"{path}:{lineno}: embedded-blob — a {size} B literal is embedded in "
            f"source ({what}); game bytes must never be pasted into a shipped "
            f"file. Regenerate it, hash it, or add "
            f"'# scrub-allow: embedded-bytes — <reason>'")

    for node in ast.walk(tree):
        # 1. a decoder applied to a literal payload
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else (
                fn.id if isinstance(fn, ast.Name) else "")
            div = _DECODERS.get(name)
            if div and node.args:
                arg = node.args[0]
                enc = _decoded_size(arg)
                size = int(enc / div)
                if size >= BLOB_THRESHOLD:
                    report(arg, size, f"{name}() payload")
                    continue
        # 2. a bare literal big enough to be an asset slice
        if isinstance(node, ast.Constant) and isinstance(node.value, (bytes, str)):
            size = _decoded_size(node)
            if isinstance(node.value, str):
                # only flag strings that LOOK like packed data -- prose and
                # docstrings of any length are fine.
                s = node.value.strip()
                if not s or len(s) < BLOB_THRESHOLD:
                    continue
                packed = re.fullmatch(r"[A-Za-z0-9+/=\s]+", s) and (
                    max((len(w) for w in s.split()), default=0) >= 64)
                if not packed:
                    continue
                size = int(len(re.sub(r"\s", "", s)) * 3 / 4)
            if size >= BLOB_THRESHOLD:
                kind = "bytes literal" if isinstance(node.value, bytes) else \
                       "packed text literal"
                report(node, size, kind)
    return findings


def scan_embedded_blobs(path: str, data: bytes) -> list[str]:
    """Findings for game bytes pasted INSIDE a shipped source file."""
    if path == SELF:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []                      # a real binary; the path rules own those
    findings: list[str] = []
    if Path(path).suffix.lower() in _PY_SUFFIXES:
        findings += _scan_python_blobs(path, text)
    lines = text.splitlines()
    for rx, div, what in ((_B64_RUN, 4 / 3, "base64 run"),
                          (_HEX_RUN, 2.0, "hex run"),
                          (_ESC_RUN, 4.0, "\\x escape run")):
        for m in rx.finditer(data):
            lineno = data.count(b"\n", 0, m.start()) + 1
            if _allowed_near(lines, lineno, lineno):
                continue
            size = int(len(m.group(0)) / div)
            if any(f.startswith(f"{path}:{lineno}:") for f in findings):
                continue
            findings.append(
                f"{path}:{lineno}: embedded-blob — a {size} B {what} is embedded "
                f"in source; game bytes must never be pasted into a shipped file. "
                f"Regenerate it, hash it, or add "
                f"'# scrub-allow: embedded-bytes — <reason>'")
    return findings


def check_paths(paths: list[str]) -> list[str]:
    findings = []
    for p in paths:
        for name, pattern, why in FORBIDDEN_PATHS:
            if pattern.search(p):
                findings.append(f"{p}:0: {name} — {why}")
    return findings


def gate(paths: list[str] | None = None) -> list[str]:
    files = paths if paths else tracked_files()
    extra = private_literal_rules(private_literals())
    findings = check_paths(files)
    for rel in files:
        fp = REPO / rel
        if not fp.is_file():
            continue
        raw = fp.read_bytes()
        findings += scan_bytes(rel, raw, extra_rules=extra)
        findings += scan_embedded_blobs(rel, raw)
    return findings


# --- self-test ---------------------------------------------------------------

_GOOD = [
    "content hash 0703fd2acd5803e9 and 942c829457a04a62 are fine",
    "set LONE_ECHO_DATA_ROOT to your own game data root",
    "python3 blender_tool/tests/run_tests.py",
    "run it with python.exe (Windows Python) from the repository root",
    "the render harness shells out to blender.exe in headless mode",
    "supply your own copy of oodle_11_win64.dll via LONE_ECHO_OODLE_DLL",
    "*.dll",
    "the add-on imports only bpy, mathutils and the standard library",
    "127.0.0.1 is a loopback placeholder",
    "see docs/FORMATS.md and docs/LOD.md",
    "blobs/instance_lod.bin holds lod_group:u32,lod_level:u32,lod_group_levels:u32",
    # `engine-source` must name the engine's SYMBOLS freely -- that is the whole
    # published convention -- and only refuse paths, line numbers and listings.
    "SG5_LAMBDA = 3.62780595  # kLambdaSG5, and kSG5Scale = 0.5",
    "the engine's DiffuseTermSG is saturate(dot(mean, n)) * 2 / sharpness * color",
    "CGVertexFormat::SVertexElement +0x04 is a uint8 slot",
    "NRadEngine::EBlendMode has 18 members; eBlendTranslucent is not implemented",
    # `vcs-ref` must not fire on counts, binary literals or game-asset ids.
    "1048576 bytes, mask `0b00011`, package `2fd6839161785e9c_ff91757c910ea7b6`",
    "the shipped resource `0703fd2acd5803e9` has 5377 vertices",
]

# Fixtures use PLACEHOLDER names on purpose -- a self-test that pastes the real
# username or the real private repo names back in would defeat the whole point.
_BAD = [
    (r"C:\Users\somedev\Documents\thing", {"windows-home"}),
    ("/mnt/c/Users/someone/Documents/x", {"wsl-home"}),
    ("/home/alice/projects/x", {"posix-home"}),
    ("symbols from r14.pdb", {"debug-symbols", "binary-filename"}),
    ("patched libr15.so at 0x18dc9d4", {"debug-symbols"}),
    ("load D3DCompiler_47.dll to disassemble", {"binary-filename"}),
    ("run tool.exe on the dump", {"binary-filename"}),
    ("pdb_work/dump.txt", {"debug-symbols"}),
    ("confirmed in session 39", {"session-note"}),
    ("as session42 showed", {"session-note"}),
    ("see reference/do-not-relitigate/foo.md", {"ledger"}),
    ("generic_rebuilds/session40_manifest.tsv", {"ledger", "session-note"}),
    ("token = ghp_abcdefghij0123456789", {"token"}),
    ("api_key: 0123456789abcdef", {"secret-assignment"}),
    ("-----BEGIN RSA PRIVATE KEY-----", {"private-key"}),
    ("server at 203.0.113.42", {"ip-address"}),
    # -- the game's own source tree / debug headers / shader disassembly ------
    ("sourcedb/engine/core/shaders/common/constants.hlsl:179-184", {"engine-source"}),
    ("as ubershader_common.hlsl:2263 shows", {"engine-source"}),
    ("types/asset/material.radmat:282-288", {"engine-source"}),
    ("SGameLevelData (`LoneEcho.h@1721930137835372:90343`)", {"engine-source"}),
    ("disassembled out of the shipped ps_5_0", {"engine-source"}),
    ("dcl_constantbuffer CB0[26], immediateIndexed", {"engine-source"}),
    # -- a private commit id --------------------------------------------------
    ("re-creates the `b792c21` proxy defect", {"vcs-ref"}),
    ("`af11457` D1 measured the defect", {"vcs-ref"}),
]


def self_test() -> int:
    failures = []

    for text in _GOOD:
        hits = scan_bytes("selftest", text.encode(), skip_self=False)
        if hits:
            failures.append(f"FALSE POSITIVE on {text!r}: {hits}")

    for text, expect in _BAD:
        hits = scan_bytes("selftest", text.encode(), skip_self=False)
        got = {h.split(": ", 1)[1].split(" — ")[0] for h in hits}
        if not expect & got:
            failures.append(f"MISSED {text!r}: expected one of {sorted(expect)}, got {sorted(got)}")

    for bad_path, rule in (("blender_tool/exports/x.bin", "game-bytes"),
                           ("blender_tool/dist/a-0.2.0.zip", "game-bytes"),
                           ("fixtures/a.lemesh/manifest.json", "game-bytes"),
                           ("bin/oodle.dll", "proprietary-binary")):
        hits = check_paths([bad_path])
        if not any(rule in h for h in hits):
            failures.append(f"MISSED forbidden path {bad_path!r} ({rule}): {hits}")
    for ok_path in ("blender_tool/le_mesh/package.py", "docs/LOD.md",
                    "blender_tool/fixtures/lodfull_l0.png"):
        if check_paths([ok_path]):
            failures.append(f"FALSE POSITIVE on path {ok_path!r}")

    # -- embedded blobs: the rule no PATH check can stand in for --------------
    _blob = base64.b64encode(bytes(range(256)) * 8).decode()
    _hexblob = (bytes(range(256)) * 2).hex()
    _blob_bad = [
        (f'SLICE = base64.b64decode(\n    "{_blob}"\n)\n',
         "a base64 decode fixture"),
        (f'SLICE = bytes.fromhex("{_hexblob}")\n', "a hex decode fixture"),
        (f'SLICE = b"{"\\x41" * 400}"\n', "a raw bytes literal"),
    ]
    for src, what in _blob_bad:
        if not scan_embedded_blobs("t.py", src.encode()):
            failures.append(f"MISSED embedded blob: {what}")
    # the same three, each with an explicit allow carrying a reason
    for src, what in _blob_bad:
        allowed = ("# scrub-allow: embedded-bytes — synthesised in-test, "
                   "no game bytes\n") + src
        if scan_embedded_blobs("t.py", allowed.encode()):
            failures.append(f"allow marker did not exempt {what}")
    # ...and an allow with no reason must NOT exempt anything
    no_reason = "# scrub-allow: embedded-bytes\n" + _blob_bad[0][0]
    if not scan_embedded_blobs("t.py", no_reason.encode()):
        failures.append("a reasonless allow marker exempted an embedded blob")
    # a blob in a non-Python shipped file is caught by the text sweep
    if not scan_embedded_blobs("docs/X.md", f"payload: {_blob}\n".encode()):
        failures.append("MISSED embedded blob in a non-Python file")
    # and the things that must NOT trip it
    _blob_ok = [
        ("blender_tool/le_mesh/package.py",
         b'MAGIC = b"\\x89PNG\\r\\n\\x1a\\n"\nHASH = "0703fd2acd5803e9"\n'),
        ("blender_tool/tests/expected/x.json",
         b'{"texture_names_sha256": "'
         + b"ab" * 32 + b'", "count": 212}\n'),
        ("docs/LOD.md", ("prose about LOD levels. " * 200).encode()),
        ("blender_tool/le_mesh/materials.py",
         b'DOC = """a long docstring about roles and layers.\n' + b"word " * 400
         + b'\n"""\n'),
    ]
    for p, src in _blob_ok:
        hits = scan_embedded_blobs(p, src)
        if hits:
            failures.append(f"FALSE POSITIVE embedded-blob on {p}: {hits}")

    for line in failures:
        print(f"SELF-TEST FAIL: {line}")
    if failures:
        print(f"\nself-test: {len(failures)} failure(s)")
        return 1
    # the supplied-literal mechanism must fire on a name the gate was never told
    # about at authoring time, and must NOT fire when nothing is configured.
    probe = "zzq-private-name-probe"
    rules = private_literal_rules([probe])
    hit = scan_bytes("t.md", f"see {probe}/docs".encode(), skip_self=False,
                     extra_rules=rules)
    if not any("private-literal" in h for h in hit):
        print("SELF-TEST FAIL: supplied private literal was not caught")
        return 1
    if scan_bytes("t.md", f"see {probe}/docs".encode(), skip_self=False):
        print("SELF-TEST FAIL: probe matched a built-in rule (fixture is not neutral)")
        return 1
    if private_literal_rules([]):
        print("SELF-TEST FAIL: empty literal list must yield no rule")
        return 1

    n = len(private_literals())
    print(f"self-test: OK — {len(_GOOD)} clean strings passed, "
          f"{len(_BAD)} known-bad strings caught, path rules verified, "
          f"supplied-literal rule verified ({n} literal(s) configured)")
    if not n:
        print("  note: no private literals configured — set SCRUB_PRIVATE_LITERALS "
              "or .scrub_private to also catch bare private names")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--self-test", action="store_true",
                    help="verify the gate itself against known-good/known-bad strings")
    ap.add_argument("--require-literals", action="store_true",
                    help="fail unless private literals are configured (for CI)")
    ap.add_argument("--paths", nargs="*", default=None,
                    help="scan these paths instead of the whole tracked tree")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()

    if self_test() != 0:
        print("REFUSING to gate with a broken self-test")
        return 1

    if args.require_literals and not private_literals():
        print("SCRUB GATE: FAIL — --require-literals set but none configured "
              "(set SCRUB_PRIVATE_LITERALS or create .scrub_private)")
        return 1

    findings = gate(args.paths)
    if findings:
        print(f"\nSCRUB GATE: FAIL — {len(findings)} finding(s)\n")
        for f in findings:
            print(f)
        return 1
    scanned = len(args.paths) if args.paths else len(tracked_files())
    print(f"\nSCRUB GATE: PASS — {scanned} tracked file(s) clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
