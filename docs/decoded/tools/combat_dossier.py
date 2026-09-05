"""Dossier for every combat-related level, plus the full link graph between them."""
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
NAME = {h.lower(): v.get("name", h) for h, v in idx.items()}
BYNAME = {v: k for k, v in NAME.items()}

COMBAT = [n for n in NAME.values()
          if "combat" in n or n in ("mpl_lobby_b_combat", "r14_glb_global_mp")]
TNAME = {"%016x" % U(d.name): d.name for d in CLEAN.iterdir() if d.is_dir()}
sm = A.scene_mod.CGSceneResource
out = {}

def rd(td_hash, lv):
    f = EXTR / td_hash / lv
    return f.read_bytes() if f.is_file() else None

for name in sorted(COMBAT):
    lv = BYNAME[name]
    meta = idx.get(lv) or idx.get(lv.upper()) or {}
    e = {"hash": "0x%s" % lv.upper(), "index_records": meta.get("records"),
         "index_types": meta.get("types"), "index_mb": round(meta.get("bytes", 0)/1048576.0, 1)}
    # resources actually present in this extract
    present = []
    for d in EXTR.iterdir():
        f = d / lv
        if f.is_file():
            present.append((TNAME.get(d.name, d.name), f.stat().st_size))
    e["extracted_resources"] = len(present)
    e["extracted_bytes"] = sum(s for _t, s in present)
    # actors
    b = rd("%016x" % U("CActorDataResourceWin10"), lv)
    try:
        e["actors"] = len(list(ADR.from_bytes(b).nodeids)) if b else None
    except Exception:
        e["actors"] = None
    # component systems
    b = rd("%016x" % U("CComponentSpaceResourceWin10"), lv)
    e["component_systems"] = struct.unpack_from("<Q", b, 48)[0] if b else None
    # scripts
    b = rd("%016x" % U("CScriptCRWin10"), lv)
    if b:
        n = struct.unpack_from("<Q", b, 40)[0]
        scripts = {struct.unpack_from("<Q", b, 56 + i*720 + 32)[0] for i in range(n)}
        e["script_entries"], e["distinct_scripts"] = n, len(scripts)
    # parent
    b = rd("%016x" % U("CGameLevelInfoResourceWin10"), lv)
    if b and len(b) >= 8:
        ph = "%016x" % struct.unpack_from("<Q", b, 0)[0]
        e["parent"] = NAME.get(ph, "0x%s" % ph.upper())
    # sub-levels
    b = rd("%016x" % U("CGSceneResourceWin10"), lv)
    if b:
        try:
            s12 = sm.from_bytes(b).sec12_raw
            k = struct.unpack_from("<I", s12, 0)[0]
            rows = [struct.unpack_from("<QQ", s12, 4 + i*16) for i in range(k)]
            e["sub_levels"] = [NAME.get("%016x" % h, "0x%016X" % h) for h, _i in rows]
        except Exception:
            e["sub_levels"] = None
    # gameplay markers
    for key, td in (("payload", "CR15NetPayloadCRWin10"),
                    ("capture_volume", "CR15NetCaptureVolumeCRWin10"),
                    ("track_points", "CR15TrackPointCRWin10"),
                    ("spawns", "CR15SpawnPointCRWin10")):
        b = rd("%016x" % U(td), lv)
        e[key] = struct.unpack_from("<Q", b, 40)[0] if b else 0
    out[name] = e

# ---- link graph: which combat level references which -----------------------
links = collections.defaultdict(set)
for src in sorted(COMBAT):
    s = BYNAME[src]
    blobs = []
    for d in EXTR.iterdir():
        f = d / s
        if f.is_file() and f.stat().st_size < 4_000_000:
            blobs.append(f.read_bytes())
    for dst in COMBAT:
        if dst == src:
            continue
        pat = struct.pack("<Q", int(BYNAME[dst], 16))
        if any(pat in b for b in blobs):
            links[src].add(dst)
out["_links"] = {k: sorted(v) for k, v in links.items()}

(DOCS / "decoded" / "_combat.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
print("combat levels: %d" % len(COMBAT))
for n in sorted(COMBAT):
    e = out[n]
    print("  %-36s %5s rec  %4s actors  %3s CS  %4s scripts  parent=%s"
          % (n, e.get("index_records"), e.get("actors"), e.get("component_systems"),
             e.get("script_entries"), e.get("parent")))
