"""Survey every combat CR/resource type the level index marks `no_decoder`.

Reads the REAL bytes of all six combat levels and classifies each type:
standard-CR vs resource, flat vs ordered-pool, entry stride, and per-field
roles (node hash / actor ref / float / small int / constant).
Writes docs/combat/_survey.json. Analysis only -- touches no build.
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


def read(type_dir, level):
    """type_dir is the FRIENDLY name; try the clean extract then the hash tree."""
    f = CLEAN / type_dir / (level + ".bin")
    if f.is_file():
        return f.read_bytes()
    f = EXTR / ("%016x" % U(type_dir)) / ("%016x" % U(level))
    return f.read_bytes() if f.is_file() else None


ACTORS = {}
for lv in LEVELS:
    b = read("CActorDataResourceWin10", lv)
    ACTORS[lv] = set(ADR.from_bytes(b).nodeids) if b else set()


def classify(b, actors):
    """Standard CR? flat or pooled? stride? Returns a dict."""
    out = {"bytes": len(b)}
    if len(b) < 56:
        out["shape"] = "tiny"; return out
    dsz = struct.unpack_from("<Q", b, 8)[0]
    n   = struct.unpack_from("<Q", b, 40)[0]
    n2  = struct.unpack_from("<Q", b, 48)[0]
    sen = struct.unpack_from("<I", b, 28)[0]
    out.update(data_size=dsz, count=n, count2=n2, sentinel=sen)
    if dsz != len(b) - 56:
        out["shape"] = "resource"       # not the 56-byte CR envelope
        return out
    if not n:
        out["shape"] = "empty-cr"; return out
    if dsz % n == 0:
        stride = dsz // n
        node = {struct.unpack_from("<Q", b, 56 + i * stride)[0] for i in range(n)}
        ent  = [struct.unpack_from("<Q", b, 56 + i * stride + 8)[0] for i in range(n)]
        hit  = sum(1 for e in ent if e in actors) if actors else 0
        out.update(shape="flat-cr", stride=stride, node_hashes=sorted(node)[:4],
                   node_constant=len(node) == 1,
                   entity_in_actordata="%d/%d" % (hit, len(ent)))
        if actors and hit == len(ent) and len(node) == 1:
            out["confidence"] = "confirmed"      # every row binds a real actor
        return out
    out["shape"] = "pooled-cr"          # entries + a trailing ordered pool
    return out


def fields(b, stride, n, actors):
    """Per-8-byte-offset roles across all entries of a flat CR."""
    roles = []
    for off in range(0, stride - 7, 4):
        vals = [struct.unpack_from("<Q", b, 56 + i * stride + off) [0] for i in range(n)]
        f32  = [struct.unpack_from("<f", b, 56 + i * stride + off)[0] for i in range(n)]
        u32  = [struct.unpack_from("<I", b, 56 + i * stride + off)[0] for i in range(n)]
        role = None
        if len(set(vals)) == 1 and vals[0] > 0xFFFFFFFF:
            role = "constant hash 0x%016X" % vals[0]
        elif actors and sum(1 for v in vals if v in actors) >= max(1, int(0.9 * n)):
            role = "ACTOR ref"
        elif all(v == 0 for v in vals):
            role = "zero"
        elif all(u == 0xFFFFFFFF for u in u32):
            role = "0xFFFFFFFF sentinel"
        elif all(abs(x) < 1e6 and (x == 0.0 or 1e-6 < abs(x)) for x in f32) and \
                any(x not in (0.0,) for x in f32):
            role = "float32 (e.g. %s)" % ", ".join("%.3f" % x for x in f32[:3])
        elif all(u < 4096 for u in u32):
            role = "small u32 (e.g. %s)" % ", ".join(str(u) for u in u32[:4])
        if role:
            roles.append({"offset": off, "role": role})
    return roles


TYPES = sys.argv[1:] or None
idx = json.loads((pathlib.Path(r"J:\EchoVR-Tools-Launcher\lone_echo_blender\docs")
                  / "evr_level_index.json").read_text(encoding="utf-8"))
want = collections.Counter()
for v in idx.values():
    if v.get("name") in LEVELS:
        for t in v.get("no_decoder", []) or []:
            want[t] += 1
targets = TYPES or sorted(want)

survey = {}
for t in targets:
    per = {}
    for lv in LEVELS:
        b = read(t, lv)
        if b is None:
            continue
        c = classify(b, ACTORS.get(lv, set()))
        if c.get("shape") == "flat-cr" and c["count"] <= 4096:
            c["fields"] = fields(b, c["stride"], c["count"], ACTORS.get(lv, set()))
        per[lv] = c
    if per:
        survey[t] = {"levels": len(per), "in_index_levels": want.get(t, 0), "per_level": per}

out = pathlib.Path(r"J:\EchoVR-Tools-Launcher\lone_echo_blender\docs\combat\_survey.json")
out.write_text(json.dumps(survey, indent=1), encoding="utf-8")
shapes = collections.Counter(next(iter(v["per_level"].values()))["shape"] for v in survey.values())
print("surveyed %d type(s) -> %s" % (len(survey), out))
for k, c in shapes.most_common():
    print("   %-12s %d" % (k, c))
