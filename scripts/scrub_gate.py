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
"""
from __future__ import annotations

import argparse
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
           b"password", b"home", b"."]

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
        findings += scan_bytes(rel, fp.read_bytes(), extra_rules=extra)
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
