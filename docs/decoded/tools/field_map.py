"""Profile EVERY offset of EVERY record type across every level.

Semantics mostly cannot be proven (see OPEN.md), but the VALUE DOMAIN of each
field can be measured exactly, and that is documentable: whether an offset is
always zero, a constant hash, an actor reference, a float, a small counter, or
high-entropy data. Writes docs/decoded/_fieldmap.json.
"""
import json, pathlib, struct, sys, collections, math

sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor")
from echo_editor.assemble.authoring import AuthoringBackend
A = AuthoringBackend(); A.activate()
U = lambda s: A.rad_hash(s) & 0xFFFFFFFFFFFFFFFF
ADR = A.actor_data_mod.ActorDataResource

DOCS  = pathlib.Path(r"J:\EchoVR-Tools-Launcher\lone_echo_blender\docs")
CLEAN = pathlib.Path(r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor\echovr_clean_extract\48037dc70b0ecab2")
EXTR  = pathlib.Path(r"H:\pcvr-extracted")
idx = json.loads((DOCS / "evr_level_index.json").read_text(encoding="utf-8"))
LEVELS = {h: v.get("name", h) for h, v in idx.items()}
S = json.loads((DOCS / "decoded" / "_all.json").read_text(encoding="utf-8"))


def read(td, lvhash, lvname):
    f = CLEAN / td / (lvname + ".bin")
    if f.is_file():
        return f.read_bytes()
    f = EXTR / ("%016x" % U(td)) / lvhash
    return f.read_bytes() if f.is_file() else None


ACT = {}
for h, nm in LEVELS.items():
    b = read("CActorDataResourceWin10", h, nm)
    try:
        ACT[h] = set(ADR.from_bytes(b).nodeids) if b else set()
    except Exception:
        ACT[h] = set()
ALLACT = set().union(*ACT.values()) if ACT else set()


def classify(u32s, u64s, actor_hits, n):
    """Describe one offset from its observed values."""
    d32 = set(u32s)
    if d32 == {0}:
        return "zero", ""
    if d32 == {0xFFFFFFFF}:
        return "0xFFFFFFFF", ""
    if len(d32) == 1:
        v = next(iter(d32))
        return "constant", "0x%08X (%d)" % (v, v)
    if u64s is not None and actor_hits is not None and n and actor_hits / n >= 0.99:
        return "actor ref", "%d/%d resolve" % (actor_hits, n)
    if u64s is not None and actor_hits and actor_hits / n >= 0.5:
        return "actor ref (partial)", "%d/%d resolve" % (actor_hits, n)
    fs = []
    ok = True
    for v in u32s:
        f = struct.unpack("<f", struct.pack("<I", v))[0]
        if math.isnan(f) or math.isinf(f) or abs(f) > 1e7:
            ok = False; break
        fs.append(f)
    if ok and len(set(fs)) > 1:
        return "float32", "range %.3f .. %.3f" % (min(fs), max(fs))
    if all(v < 65536 for v in u32s):
        return "small int", "range %d .. %d, %d distinct" % (min(u32s), max(u32s), len(d32))
    if u64s is not None and len(set(u64s)) > max(2, 0.5 * len(u64s)):
        return "hash-like", "%d distinct of %d" % (len(set(u64s)), len(u64s))
    return "mixed", "%d distinct" % len(d32)


out = {}
for td, v in sorted(S.items()):
    kinds = {r.get("shape") for r in v["per_level"].values()}
    if not ({"flat-cr", "pooled-cr"} & kinds):
        continue
    stride = v["strides"][0] if v["strides"] else None
    if not stride or stride > 1024:
        continue
    cols32 = collections.defaultdict(list)
    cols64 = collections.defaultdict(list)
    hits64 = collections.Counter()
    n = 0
    for h, nm in LEVELS.items():
        r = v["per_level"].get(nm)
        if not r or r.get("shape") not in ("flat-cr", "pooled-cr"):
            continue
        b = read(td, h, nm)
        if b is None:
            continue
        aset = ACT.get(h, set())
        cnt = r["count"]
        if cnt * stride + 56 > len(b):
            continue
        step = max(1, cnt // 400)          # sample very large tables
        for i in range(0, cnt, step):
            base = 56 + i * stride
            n += 1
            for off in range(0, stride - 3, 4):
                cols32[off].append(struct.unpack_from("<I", b, base + off)[0])
            for off in range(0, stride - 7, 4):
                q = struct.unpack_from("<Q", b, base + off)[0]
                cols64[off].append(q)
                if q in aset:
                    hits64[off] += 1
    if not n:
        continue
    fields = []
    for off in sorted(cols32):
        kind, note = classify(cols32[off], cols64.get(off), hits64.get(off), n)
        fields.append({"offset": off, "kind": kind, "note": note})
    out[td] = {"stride": stride, "entries_sampled": n, "fields": fields}

p = DOCS / "decoded" / "_fieldmap.json"
p.write_text(json.dumps(out, indent=1), encoding="utf-8")
kinds = collections.Counter(f["kind"] for v in out.values() for f in v["fields"])
print("profiled %d record types, %d entries sampled"
      % (len(out), sum(v["entries_sampled"] for v in out.values())))
for k, c in kinds.most_common():
    print("   %-22s %d offsets" % (k, c))
