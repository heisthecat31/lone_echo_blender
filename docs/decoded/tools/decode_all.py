"""Run the combat decode pipeline over EVERY level and EVERY undecoded type.

Same tests as docs/combat (which this supersedes in scope):
  1. envelope split      data_size == len-56  ->  component record, else resource
  2. stride agreement    stride = data_size/count, required equal in every level
  3. entry head          +0 constant in a file, +8 resolves in CActorDataResource
  4. invariance          which offsets never vary across every entry of every level
  5. byte identity       sha1 of the file, per level

Writes docs/decoded/_all.json.
"""
import json, pathlib, struct, sys, collections, hashlib

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
TYPES = sorted({t for v in idx.values() for t in (v.get("no_decoder") or [])})
print("levels %d, undecoded types %d" % (len(LEVELS), len(TYPES)))


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

out = {}
for td in TYPES:
    per, strides, vals, nent = {}, set(), collections.defaultdict(set), 0
    for h, nm in LEVELS.items():
        b = read(td, h, nm)
        if b is None:
            continue
        rec = {"bytes": len(b), "sha1": hashlib.sha1(b).hexdigest()[:10]}
        if len(b) >= 56:
            dsz = struct.unpack_from("<Q", b, 8)[0]
            n = struct.unpack_from("<Q", b, 40)[0]
            rec["count"] = n
            if dsz != len(b) - 56:
                # POOLED CR, not a resource: entries occupy data_size and an
                # ordered pool follows. Recognised when data_size divides evenly
                # by count and leaves a positive tail.
                if n and dsz and dsz % n == 0 and 56 + dsz < len(b):
                    st = dsz // n
                    rec["shape"] = "pooled-cr"; rec["stride"] = st; strides.add(st)
                    rec["pool_bytes"] = len(b) - 56 - dsz
                    node = {struct.unpack_from("<Q", b, 56 + i * st)[0] for i in range(n)}
                    ent = [struct.unpack_from("<Q", b, 56 + i * st + 8)[0] for i in range(n)]
                    aset = ACT.get(h, set())
                    rec["node_constant"] = len(node) == 1
                    rec["node"] = "0x%016X" % min(node)
                    rec["entity_resolves"] = (sum(1 for e in ent if e in aset), len(ent))
                else:
                    rec["shape"] = "resource"
            elif n == 0:
                rec["shape"] = "empty-cr"
            elif dsz % n == 0:
                st = dsz // n
                rec["shape"] = "flat-cr"; rec["stride"] = st; strides.add(st)
                node = {struct.unpack_from("<Q", b, 56 + i * st)[0] for i in range(n)}
                ent = [struct.unpack_from("<Q", b, 56 + i * st + 8)[0] for i in range(n)]
                aset = ACT.get(h, set())
                rec["node_constant"] = len(node) == 1
                rec["node"] = "0x%016X" % min(node)
                rec["entity_resolves"] = (sum(1 for e in ent if e in aset), len(ent))
                for i in range(n):
                    nent += 1
                    for off in range(0, st - 3, 4):
                        vals[off].add(struct.unpack_from("<I", b, 56 + i * st + off)[0])
            else:
                rec["shape"] = "pooled-cr"
        else:
            rec["shape"] = "tiny"
        per[nm] = rec
    if not per:
        continue
    shapes = {r["shape"] for r in per.values()}
    inv = {off: sorted(v)[0] for off, v in vals.items() if len(v) == 1}
    e = {"type_hash": "0x%016X" % U(td), "levels": len(per),
         "shapes": sorted(shapes), "strides": sorted(strides),
         "stride_agrees": len(strides) <= 1,
         "entries_examined": nent,
         "invariant_offsets": sorted(inv), "varying_offsets": sorted(o for o in vals if len(vals[o]) > 1),
         "sha1_identical": len({r["sha1"] for r in per.values()}) == 1 and len(per) > 1,
         "per_level": per}
    if "flat-cr" in shapes or "pooled-cr" in shapes:
        ok = [r for r in per.values() if r.get("shape") in ("flat-cr", "pooled-cr")]
        e["node_constant_everywhere"] = all(r.get("node_constant") for r in ok)
        e["entity_resolves_everywhere"] = all(r["entity_resolves"][0] == r["entity_resolves"][1]
                                              for r in ok if r.get("entity_resolves"))
    out[td] = e

p = DOCS / "decoded" / "_all.json"
p.write_text(json.dumps(out, indent=1), encoding="utf-8")
sh = collections.Counter(tuple(v["shapes"]) for v in out.values())
print("types surveyed: %d" % len(out))
for k, c in sh.most_common(8):
    print("   %-28s %d" % ("+".join(k), c))
flat = [t for t, v in out.items() if "flat-cr" in v["shapes"]]
print("flat: %d ; stride agrees in all levels: %d"
      % (len(flat), sum(1 for t in flat if out[t]["stride_agrees"])))
print("confirmed (node constant + every entity resolves): %d"
      % sum(1 for t in flat if out[t].get("node_constant_everywhere") and out[t].get("entity_resolves_everywhere")))
print("byte-identical across every level shipping them: %d"
      % sum(1 for v in out.values() if v["sha1_identical"]))
print("total entries examined: %d" % sum(v["entries_examined"] for v in out.values()))
