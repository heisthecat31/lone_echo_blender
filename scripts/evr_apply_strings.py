"""Every string the archives actually carry, plus what the dialogue records say.

    python scripts/evr_apply_strings.py <package> [--dir <extract>]

Writes `<pkg>/strings.json`.

WHERE THE TEXT IS
-----------------
`CLanguageTableResourceWin10` (`ef3b782fa312db02`) is the string table, and it
is fully decoded:

    u32            count
    u64 * count    key -- the CSymbol64 a record references the string by
    u32            count AGAIN (this repeat is what validates the parse)
    u32 * count+1  offsets into the blob, ascending, last is the total length
    bytes          the blob, NUL-terminated UTF-8

Validated on 3 of 3 files: the count matches in both places, the offsets are
sorted, and `base + offsets[-1]` lands exactly inside the file. The `count+1`
end sentinel is the part that is easy to miss -- reading `count` offsets puts
the blob base 4 bytes early and every string comes out as its own tail
("rs.", "ck!") instead of its head.

Two more types carry text in a DIFFERENT, undecoded layout, so their strings
are SCRAPED and labelled as such rather than presented as structured:
`CR15UIPage2CRWin10` ("Resume game") and `CR15NetRewardItemCRWin10`.

⚠ WHAT IS NOT HERE. `CDialogue2CR` names `radstr` -> `StringTableEntry`
(recovered by hashing: `41d2d53c1e30130c` = "radstr",
`89423fb49877eead` = "StringTableEntry", `056c0235054524ea` = "ncaDialogue2"),
but NONE of its 25 symbols matches any of the 42 language-table keys. The
dialogue lines themselves are not in the archives.

Most user-facing game text is not in the archives at all -- it is plain JSON in
`<install>/sourcedb/rad15/json/r14/`, e.g. `loading_tips.json`. That is left
where it is rather than copied.
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path

LANG_TYPE = "ef3b782fa312db02"          # CLanguageTableResourceWin10
SCRAPE = {"4a3fe67c1ff9ccc4": "CR15UIPage2CRWin10",
          "32f30fe361939dee": "CR15NetRewardItemCRWin10"}
#: a scraped run must look like prose, not like a hash or a fourcc
SCRAPE_RE = re.compile(rb"[ -~]{6,}")
PRINTABLE = re.compile(r"^[A-Za-z0-9][\x20-\x7e]*$")


def parse_language_table(b: bytes):
    """`[(key, text)]`, or None when the file is not this layout."""
    if len(b) < 12:
        return None
    n = struct.unpack_from("<I", b, 0)[0]
    if not 0 < n < 100000 or 4 + 8 * n + 4 > len(b):
        return None
    o = 4 + 8 * n
    if struct.unpack_from("<I", b, o)[0] != n:      # the repeat is the check
        return None
    o += 4
    if o + 4 * (n + 1) > len(b):
        return None
    offs = [struct.unpack_from("<I", b, o + 4 * i)[0] for i in range(n + 1)]
    base = o + 4 * (n + 1)
    if offs != sorted(offs) or base + offs[-1] > len(b):
        return None
    keys = [struct.unpack_from("<Q", b, 4 + 8 * i)[0] for i in range(n)]
    out = []
    for i in range(n):
        raw = b[base + offs[i]:base + offs[i + 1]].split(b"\x00")[0]
        out.append(("%016x" % keys[i], raw.decode("utf-8", "replace")))
    return out


def scrape(b: bytes):
    seen, out = set(), []
    for m in SCRAPE_RE.finditer(b):
        s = m.group().decode("ascii", "ignore").strip()
        if len(s) < 6 or s in seen:
            continue
        if not PRINTABLE.match(s) or " " not in s and not s.islower() and not s.isupper():
            # a run with no space and mixed case is far more likely to be
            # packed binary than a sentence; keep words and SHOUTED labels
            if " " not in s and not re.fullmatch(r"[a-z0-9_]+", s):
                continue
        seen.add(s)
        out.append(s)
    return out


def apply(pkg: Path, root: Path) -> dict:
    out = {"format": "evr_strings", "version": 1,
           "language_tables": {}, "scraped": {}}
    total = 0
    d = root / LANG_TYPE
    if d.is_dir():
        for f in sorted(d.iterdir()):
            rows = parse_language_table(f.read_bytes())
            if rows is None:
                continue
            out["language_tables"][f.name] = [{"key": k, "text": t} for k, t in rows]
            total += len(rows)
    for h, name in SCRAPE.items():
        dd = root / h
        if not dd.is_dir():
            continue
        got = {}
        for f in sorted(dd.iterdir()):
            s = scrape(f.read_bytes())
            if s:
                got[f.name] = s
        if got:
            out["scraped"][name] = got
    out["note"] = (
        "language_tables are STRUCTURED (key -> text, layout validated by the "
        "repeated count and the offset sentinel). `scraped` is text pulled out "
        "of types whose layout is NOT decoded -- the strings are real, their "
        "keys and grouping are not recovered.")
    out["dialogue_note"] = (
        "CDialogue2CR references radstr/StringTableEntry but none of its 25 "
        "symbols matches any language-table key: the dialogue lines are not in "
        "the archives.")
    out["elsewhere"] = ("most user-facing text is plain JSON under "
                        "<install>/sourcedb/rad15/json/r14/ (loading_tips.json, "
                        "config/uisettings/*) and is not copied here")
    (pkg / "strings.json").write_text(json.dumps(out, indent=1, ensure_ascii=False),
                                      encoding="utf-8")
    return {"tables": len(out["language_tables"]), "strings": total,
            "scraped": {k: sum(len(v) for v in d.values())
                        for k, d in out["scraped"].items()}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("package")
    ap.add_argument("--dir", default=r"H:\pcvr-extracted")
    a = ap.parse_args(argv)
    r = apply(Path(a.package), Path(a.dir))
    print("  language tables : %d  (%d structured strings)" % (r["tables"], r["strings"]))
    for k, v in r["scraped"].items():
        print("  scraped %-28s %d strings" % (k, v))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
