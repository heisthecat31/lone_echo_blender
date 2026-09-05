"""Everything about mpl_combat_fission, and everything that links to it."""
import json, pathlib, struct, sys, collections, hashlib

sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor")
from echo_editor.assemble.authoring import AuthoringBackend
A = AuthoringBackend(); A.activate()
U = lambda s: A.rad_hash(s) & 0xFFFFFFFFFFFFFFFF
ADR = A.actor_data_mod.ActorDataResource

DOCS  = pathlib.Path(r"J:\EchoVR-Tools-Launcher\lone_echo_blender\docs")
CLEAN = pathlib.Path(r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor\echovr_clean_extract\48037dc70b0ecab2")
EXTR  = pathlib.Path(r"H:\pcvr-extracted")
FIS   = U("mpl_combat_fission")
FISH  = "%016x" % FIS
idx = json.loads((DOCS / "evr_level_index.json").read_text(encoding="utf-8"))
LEVELS = {h: v.get("name", h) for h, v in idx.items()}

# friendly type names from the clean extract
TNAME = {}
for d in CLEAN.iterdir():
    if d.is_dir():
        TNAME["%016x" % U(d.name)] = d.name

out = {"level": "mpl_combat_fission", "hash": "0x%016X" % FIS}

# ---- 1. every resource fission ships -------------------------------------
res = []
for d in sorted(EXTR.iterdir()):
    f = d / FISH
    if not f.is_file():
        continue
    b = f.read_bytes()
    e = {"type": TNAME.get(d.name, d.name), "type_hash": "0x%s" % d.name.upper(),
         "bytes": len(b), "sha1": hashlib.sha1(b).hexdigest()[:10]}
    if len(b) >= 56:
        dsz = struct.unpack_from("<Q", b, 8)[0]
        n = struct.unpack_from("<Q", b, 40)[0]
        e["count"] = n
        if dsz == len(b) - 56:
            e["shape"] = "flat-cr" if n else "empty-cr"
            if n and dsz % n == 0:
                e["stride"] = dsz // n
        elif n and dsz and dsz % n == 0 and 56 + dsz < len(b):
            e["shape"] = "pooled-cr"; e["stride"] = dsz // n
            e["pool"] = len(b) - 56 - dsz
        else:
            e["shape"] = "resource"
    res.append(e)
out["resources"] = res
out["resource_count"] = len(res)
out["total_bytes"] = sum(r["bytes"] for r in res)

# ---- 2. actors ------------------------------------------------------------
ad = ADR.from_bytes((EXTR / ("%016x" % U("CActorDataResourceWin10")) / FISH).read_bytes())
ids = list(ad.nodeids)
out["actors"] = len(ids)

# ---- 3. component space ---------------------------------------------------
cs = (EXTR / ("%016x" % U("CComponentSpaceResourceWin10")) / FISH).read_bytes()
n = struct.unpack_from("<Q", cs, 48)[0]
csh = [struct.unpack_from("<Q", cs, 64 + i * 16)[0] for i in range(n)]
out["component_systems"] = n
out["cs_without_cr"] = sum(1 for h in csh
                           if not (EXTR / ("%016x" % h) / FISH).is_file())

# ---- 4. scripts -----------------------------------------------------------
sc = (EXTR / ("%016x" % U("CScriptCRWin10")) / FISH).read_bytes()
sn = struct.unpack_from("<Q", sc, 40)[0]
ent = [(struct.unpack_from("<Q", sc, 56 + i * 720 + 8)[0],
        struct.unpack_from("<Q", sc, 56 + i * 720 + 32)[0]) for i in range(sn)]
fan = collections.Counter(s for _a, s in ent)
out["script_entries"] = sn
out["distinct_scripts"] = len(fan)
out["top_scripts"] = [{"script": "0x%016X" % s, "actors": c} for s, c in fan.most_common(8)]

# ---- 5. sub-levels (sec12) ------------------------------------------------
sm = A.scene_mod.CGSceneResource
s12 = sm.from_bytes((EXTR / ("%016x" % U("CGSceneResourceWin10")) / FISH).read_bytes()).sec12_raw
k = struct.unpack_from("<I", s12, 0)[0]
rows = [struct.unpack_from("<QQ", s12, 4 + i * 16) for i in range(k)]
p = 4 + k * 16 + 4
h2 = struct.unpack_from("<I", s12, p)[0]; p += 4 + h2 * 16 + 4
dc = struct.unpack_from("<I", s12, p)[0]; ds = p + 4
pts = [struct.unpack_from("<fff", s12, ds + i * 12) for i in range(dc)]
out["sub_levels"] = [{"level": "0x%016X" % h,
                      "at": [round(x, 2) for x in pts[i]] if i < len(pts) else None}
                     for h, i in rows]

# ---- 6. parent ------------------------------------------------------------
gi = (EXTR / ("%016x" % U("CGameLevelInfoResourceWin10")) / FISH).read_bytes()
out["parent"] = "0x%016X" % struct.unpack_from("<Q", gi, 0)[0]

# ---- 7. WHAT LINKS TO FISSION --------------------------------------------
links = []
for h, nm in LEVELS.items():
    if h == FISH:
        continue
    hits = []
    for d in EXTR.iterdir():
        f = d / h
        if f.is_file() and struct.pack("<Q", FIS) in f.read_bytes():
            hits.append(TNAME.get(d.name, d.name))
    if hits:
        gi2 = EXTR / ("%016x" % U("CGameLevelInfoResourceWin10")) / h
        parent = None
        if gi2.is_file():
            parent = "0x%016X" % struct.unpack_from("<Q", gi2.read_bytes(), 0)[0]
        links.append({"level": nm, "hash": "0x%s" % h.upper(),
                      "in": sorted(hits), "parent": parent,
                      "parented_to_fission": parent == "0x%016X" % FIS})
out["linked_from"] = links

(DOCS / "decoded" / "_fission.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print("resources %d (%.1f MB), actors %d, CS %d (%d without a CR)"
      % (out["resource_count"], out["total_bytes"] / 1048576.0,
         out["actors"], out["component_systems"], out["cs_without_cr"]))
print("scripts %d entries / %d distinct ; sub-levels %d ; parent %s"
      % (sn, len(fan), len(rows), out["parent"]))
print("levels referencing fission: %d (of which parented to it: %d)"
      % (len(links), sum(1 for l in links if l["parented_to_fission"])))
for l in links:
    print("   %-28s parent=%s  %s" % (l["level"], l["parent"], ",".join(l["in"])[:60]))
