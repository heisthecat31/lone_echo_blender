"""Prove per-field meaning in every flat combat CR by CORRELATION, not by eye.

Ground truth is each level's CActorDataResource: every CR entry names its actor
at +8, and that actor has a known position / rotation / scale. So for each
4-byte offset in an entry we can ASK a question with a yes/no answer:

    does the float at this offset equal the owning actor's position.x
    for EVERY entry, in EVERY level that ships this type?

A field that matches 100% across thousands of entries and several maps is
decoded, not guessed. Anything below 100% is reported with its rate and left
unclaimed. Writes docs/combat/_fields.json.
"""
import json, pathlib, struct, sys, collections

sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor")
from echo_editor.assemble.authoring import AuthoringBackend
A = AuthoringBackend(); A.activate()
U = lambda s: A.rad_hash(s) & 0xFFFFFFFFFFFFFFFF
ADR = A.actor_data_mod.ActorDataResource

CLEAN = pathlib.Path(r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor\echovr_clean_extract\48037dc70b0ecab2")
EXTR  = pathlib.Path(r"H:\pcvr-extracted")
LEVELS = ("mpl_combat_fission", "mpl_combat_gauss", "mpl_combat_dyson",
          "mpl_combat_combustion", "mpl_combat_war_room", "mpl_lobby_b_combat")
EPS = 1e-6
MIN_N = 8          # too few entries to call anything proven


def read(td, lv):
    f = CLEAN / td / (lv + ".bin")
    if f.is_file():
        return f.read_bytes()
    f = EXTR / ("%016x" % U(td)) / ("%016x" % U(lv))
    return f.read_bytes() if f.is_file() else None


# ---- ground truth: actor -> transform ------------------------------------
TRUTH, ACTORSET = {}, {}
for lv in LEVELS:
    b = read("CActorDataResourceWin10", lv)
    if not b:
        TRUTH[lv], ACTORSET[lv] = {}, set()
        continue
    ad = ADR.from_bytes(b)
    ids = list(ad.nodeids)
    t = {}
    for i, h in enumerate(ids):
        tr = ad.transforms[i]
        t[h] = {
            "pos.x": tr.position_x, "pos.y": tr.position_y, "pos.z": tr.position_z,
            "rot.x": getattr(tr, "rotation_x", None), "rot.y": getattr(tr, "rotation_y", None),
            "rot.z": getattr(tr, "rotation_z", None), "rot.w": getattr(tr, "rotation_w", None),
            "scale.x": getattr(tr, "scale_x", None), "scale.y": getattr(tr, "scale_y", None),
            "scale.z": getattr(tr, "scale_z", None),
        }
    TRUTH[lv], ACTORSET[lv] = t, set(ids)

survey = json.loads((pathlib.Path(r"J:\EchoVR-Tools-Launcher\lone_echo_blender\docs\combat")
                     / "_survey.json").read_text(encoding="utf-8"))

out = {}
for tname, tv in survey.items():
    flat = {lv: p for lv, p in tv["per_level"].items() if p.get("shape") == "flat-cr"}
    if not flat:
        continue
    stride = next(iter(flat.values()))["stride"]
    # tally[offset][hypothesis] = [hits, total]
    tally = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    varfield = collections.defaultdict(set)     # distinct raw u32 seen per offset
    vartruth = collections.defaultdict(set)     # distinct truth values per hypothesis
    # (both filled below; used to reject degenerate 0 == 0 "matches")
    for lv, p in flat.items():
        b = read(tname, lv)
        if b is None:
            continue
        n, truth, aset = p["count"], TRUTH[lv], ACTORSET[lv]
        for i in range(n):
            base = 56 + i * stride
            owner = struct.unpack_from("<Q", b, base + 8)[0]
            tr = truth.get(owner)
            for off in range(0, stride - 3, 4):
                f = struct.unpack_from("<f", b, base + off)[0]
                u = struct.unpack_from("<I", b, base + off)[0]
                if tr:
                    for k, v in tr.items():
                        if v is None:
                            continue
                        vartruth["actor " + k].add(round(v, 6))
                        cell = tally[off]["actor " + k]
                        cell[1] += 1
                        if abs(f - v) <= EPS * max(1.0, abs(v)):
                            cell[0] += 1
                varfield[off].add(u)
                c = tally[off]["entry index"]; c[1] += 1; c[0] += (u == i)
                if off <= stride - 8:
                    q = struct.unpack_from("<Q", b, base + off)[0]
                    c = tally[off]["actor ref"]; c[1] += 1; c[0] += (q in aset)
    proven, partial = {}, {}
    for off, hyps in sorted(tally.items()):
        for h, (hit, tot) in hyps.items():
            if not tot or tot < MIN_N:
                continue
            # DISCRIMINATION. A field of zeros "matches" a rotation of zeros in
            # every entry and proves nothing, and +8 is the owner by definition.
            # A claim only counts when BOTH sides actually vary.
            if h == "actor ref" and off == 8:
                continue
            if h.startswith("actor ") and h != "actor ref":
                if len(varfield[off]) < 2 or len(vartruth[h]) < 2:
                    continue
            if h == "entry index" and len(varfield[off]) < 2:
                continue
            rate = hit / tot
            if rate == 1.0:
                proven.setdefault(off, []).append({"means": h, "n": tot})
            elif rate >= 0.90:
                partial.setdefault(off, []).append({"means": h, "rate": round(rate, 4), "n": tot})
    if proven or partial:
        out[tname] = {"stride": stride, "levels": sorted(flat),
                      "proven": proven, "partial": partial}

p = pathlib.Path(r"J:\EchoVR-Tools-Launcher\lone_echo_blender\docs\combat\_fields.json")
p.write_text(json.dumps(out, indent=1), encoding="utf-8")
nproven = sum(1 for v in out.values() if v["proven"])
print("types with at least one 100%% field: %d / %d" % (nproven, len(out)))
for t, v in sorted(out.items()):
    if v["proven"]:
        bits = []
        for off, hs in sorted(v["proven"].items()):
            bits.append("+%s=%s" % (off, "/".join(h["means"] for h in hs)))
        print("   %-40s %s" % (t, ", ".join(bits[:5])))
