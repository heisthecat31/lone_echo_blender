"""Solve the inline-table layout of every POOLED component record -- structurally.

A pooled CR is  56-byte header | count x stride entries | ordered pool.

Searching for "offsets whose sizes sum to the pool" is AMBIGUOUS: a header whose
size is zero in every entry can be included or dropped without changing the sum,
so many offset sets satisfy it. So headers are identified by their SHAPE first,
and the sum is then used as the check rather than the search:

    an inline table header at offset o inside an entry has
        u64 @ o+0   == 0        (no offset field -- the pool is ordered)
        u64 @ o+16  == 0
        u64 @ o+8   == byte size of this table's data

Take every offset that satisfies that in EVERY entry of EVERY level, then
require

    sum over entries, over headers, of size  ==  pool length      (exactly)

Writes docs/decoded/_pools.json.
"""
import json, pathlib, struct, sys

sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor")
from echo_editor.assemble.authoring import AuthoringBackend
A = AuthoringBackend(); A.activate()
U = lambda s: A.rad_hash(s) & 0xFFFFFFFFFFFFFFFF

DOCS  = pathlib.Path(r"J:\EchoVR-Tools-Launcher\lone_echo_blender\docs")
CLEAN = pathlib.Path(r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor\echovr_clean_extract\48037dc70b0ecab2")
EXTR  = pathlib.Path(r"H:\pcvr-extracted")
idx = json.loads((DOCS / "evr_level_index.json").read_text(encoding="utf-8"))
LEVELS = {h: v.get("name", h) for h, v in idx.items()}


def read(td, lvhash, lvname):
    f = CLEAN / td / (lvname + ".bin")
    if f.is_file():
        return f.read_bytes()
    f = EXTR / ("%016x" % U(td)) / lvhash
    return f.read_bytes() if f.is_file() else None


S = json.loads((DOCS / "decoded" / "_all.json").read_text(encoding="utf-8"))
out = {}
for td, v in sorted(S.items()):
    if "pooled-cr" not in v["shapes"]:
        continue
    files = []
    for h, nm in LEVELS.items():
        r = v["per_level"].get(nm)
        if not r or r.get("shape") != "pooled-cr":
            continue
        b = read(td, h, nm)
        if b is not None:
            files.append((nm, b, r["count"], r["stride"], r["pool_bytes"]))
    if not files:
        continue
    stride = files[0][3]

    # --- 1. structural candidates: +0 and +16 of the header are always zero ---
    cand = []
    for o in range(0, stride - 24 + 1, 8):
        ok = True
        for _nm, b, n, st, _p in files:
            for i in range(n):
                base = 56 + i * st + o
                if struct.unpack_from("<Q", b, base)[0] != 0 or \
                   struct.unpack_from("<Q", b, base + 16)[0] != 0:
                    ok = False; break
            if not ok:
                break
        if ok:
            cand.append(o)

    # --- 2. keep those whose size field is ever non-zero, then check the sum ---
    def total(offs):
        t = 0
        for _nm, b, n, st, _p in files:
            for i in range(n):
                base = 56 + i * st
                for o in offs:
                    t += struct.unpack_from("<Q", b, base + o + 8)[0]
        return t

    pool_total = sum(f[4] for f in files)
    live = [o for o in cand if total([o]) > 0]
    solved = total(cand) == pool_total
    solved_live = total(live) == pool_total
    out[td] = {"stride": stride, "levels": [f[0] for f in files],
               "pools": {f[0]: f[4] for f in files},
               "header_candidates": cand, "live_headers": live,
               "tables": len(live),
               "solved": bool(solved or solved_live),
               "sum_matches_pool": solved_live or solved}

(DOCS / "decoded" / "_pools.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
ok = [t for t, v in out.items() if v["solved"]]
print("pooled types: %d ; layout SOLVED (headers found + sum exact): %d" % (len(out), len(ok)))
for t in sorted(ok):
    v = out[t]
    print("   %-42s stride %-4d %2d live table(s) at %s"
          % (t, v["stride"], v["tables"],
             ",".join("+%d" % o for o in v["live_headers"][:6]) or "-"))
un = sorted(t for t in out if not out[t]["solved"])
print("\nunsolved (%d): %s" % (len(un), ", ".join(un[:14])))
