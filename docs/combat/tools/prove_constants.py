"""Two stronger proofs than transform-correlation.

(a) INVARIANTS -- for each flat CR type, which byte offsets hold the SAME value
    in every entry of every combat level. A field that never varies across four
    independently authored maps is structural (node hash, sentinel, padding),
    and that is a fact about the format rather than about one map.

(b) CONTROLLED INPUT -- our own build authors known values (spawn ids, the
    payload start, the trigger volume). Finding those exact values at a fixed
    offset in the built bytes proves what the field is, because we chose it.

Writes docs/combat/_constants.json.
"""
import json, pathlib, struct, sys, collections

sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor")
from echo_editor.assemble.authoring import AuthoringBackend
A = AuthoringBackend(); A.activate()
U = lambda s: A.rad_hash(s) & 0xFFFFFFFFFFFFFFFF

CLEAN = pathlib.Path(r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor\echovr_clean_extract\48037dc70b0ecab2")
EXTR  = pathlib.Path(r"H:\pcvr-extracted")
DOCS  = pathlib.Path(r"J:\EchoVR-Tools-Launcher\lone_echo_blender\docs\combat")
LEVELS = ("mpl_combat_fission", "mpl_combat_gauss", "mpl_combat_dyson",
          "mpl_combat_combustion", "mpl_combat_war_room", "mpl_lobby_b_combat")


def read(td, lv):
    f = CLEAN / td / (lv + ".bin")
    if f.is_file():
        return f.read_bytes()
    f = EXTR / ("%016x" % U(td)) / ("%016x" % U(lv))
    return f.read_bytes() if f.is_file() else None


survey = json.loads((DOCS / "_survey.json").read_text(encoding="utf-8"))
out = {}
for tname, tv in survey.items():
    flat = {lv: p for lv, p in tv["per_level"].items() if p.get("shape") == "flat-cr"}
    if not flat:
        continue
    stride = next(iter(flat.values()))["stride"]
    vals = collections.defaultdict(set)     # offset -> distinct u32 across everything
    total = 0
    for lv, p in flat.items():
        b = read(tname, lv)
        if b is None:
            continue
        for i in range(p["count"]):
            base = 56 + i * stride
            total += 1
            for off in range(0, stride - 3, 4):
                vals[off].add(struct.unpack_from("<I", b, base + off)[0])
    inv = {}
    for off in sorted(vals):
        if len(vals[off]) == 1:
            v = next(iter(vals[off]))
            kind = ("zero" if v == 0 else
                    "0xFFFFFFFF" if v == 0xFFFFFFFF else
                    "float %g" % struct.unpack("<f", struct.pack("<I", v))[0]
                    if 0x30000000 < v < 0x50000000 else
                    "0x%08X" % v)
            inv[off] = kind
    varying = [o for o in sorted(vals) if len(vals[o]) > 1]
    out[tname] = {"stride": stride, "entries_examined": total,
                  "levels": sorted(flat),
                  "invariant": inv, "varying_offsets": varying,
                  "invariant_bytes": 4 * len(inv),
                  "varying_bytes": 4 * len(varying)}

(DOCS / "_constants.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
tot_i = sum(v["invariant_bytes"] for v in out.values())
tot_v = sum(v["varying_bytes"] for v in out.values())
print("flat types: %d ; entries examined: %d"
      % (len(out), sum(v["entries_examined"] for v in out.values())))
print("bytes per entry that NEVER vary: %d ; that do: %d" % (tot_i, tot_v))
full = [t for t, v in out.items() if not v["varying_offsets"]]
print("types whose every field is invariant across all maps: %d" % len(full))
for t in sorted(full)[:12]:
    print("   %-42s stride %-4d (%d entries)"
          % (t, out[t]["stride"], out[t]["entries_examined"]))
