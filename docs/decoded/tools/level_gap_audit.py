"""What a level SHIPS vs what we DECODE vs what reaches Blender.

Three columns, because they are three different questions and the gaps between
them are where the work is:

  SHIPS    the type is in the level's archive at all (evr_level_index)
  DECODED  something in this tree parses it -- a scripts/ module, le_mesh, or
           the map editor's importers
  EXPORTED it reaches the Blender package: a manifest key, a sidecar json, or
           a blob directory

A type can be decoded and never exported (we understand it, Blender never sees
it) or exported and barely decoded (we ship bytes we do not understand). Both
are counted separately rather than collapsed into "done".

Writes docs/decoded/LEVEL_GAPS.md.
"""
import json, pathlib, re, sys, collections

ROOT = pathlib.Path(r"J:\EchoVR-Tools-Launcher\lone_echo_blender")
DOCS = ROOT / "docs"
PKG = pathlib.Path(r"J:\EchoVRModels_half\scenes\mpl_arena_a")
EDITOR = pathlib.Path(r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor")

idx = json.loads((DOCS / "evr_level_index.json").read_text(encoding="utf-8"))
arena = next(v for v in idx.values() if v.get("name") == "mpl_arena_a")
ships = sorted(set(arena["known"]))
no_decoder = set(arena["no_decoder"])

# ---- DECODED: does any source file in the tree name the type?
sources = []
for base, pat in ((ROOT / "scripts", "*.py"), (ROOT / "blender_tool", "*.py"),
                  (EDITOR / "echo_editor", "*.py"),
                  (pathlib.Path(r"J:\EchoVR-Tools-Launcher\quest_combat_port\tools\resource_io"), "*.py")):
    if base.is_dir():
        sources += [p for p in base.rglob(pat) if "__pycache__" not in str(p)]
blob = {}
for p in sources:
    try:
        blob[p] = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        pass
print("scanned %d source files" % len(blob))


#: A DEDICATED module whose filename IS the type. This is the honest test.
#: "some file mentions the string" scored 99 of 99, because the level index,
#: the docs and the survey tools all name every type they enumerate --
#: mentioning a type is not parsing it.
MODULES = {}
for _p in blob:
    MODULES.setdefault(_p.stem.lower().replace("_", ""), []).append(_p)


def decoders_for(t):
    """Modules that exist to parse THIS type, matched by filename."""
    keys = {t.lower(), t.replace("Win10", "").lower()}
    hits = []
    for k in keys:
        hits += MODULES.get(k.replace("_", ""), [])
    return sorted({p.name for p in hits})


def mentions(t):
    """How many source files merely NAME the type -- context, not evidence."""
    bare = t.replace("Win10", "")
    n = 0
    for _p, s in blob.items():
        if t in s or (len(bare) > 8 and re.search(r"\b%s\b" % re.escape(bare), s)):
            n += 1
    return n


# ---- EXPORTED: what the Blender package actually carries
pkg_files = sorted(p.name for p in PKG.iterdir()) if PKG.is_dir() else []
manifest = json.loads((PKG / "manifest.json").read_text(encoding="utf-8")) if (PKG / "manifest.json").is_file() else {}
man_keys = sorted(manifest)
# type -> what carries it into the package (hand-mapped, stated not guessed)
EXPORTS = {
    "CGMeshListResourceWin10": "manifest.meshes + blobs/*_pos/idx/uv0",
    "CGInstancedModelResourceWin10": "manifest.meshes + blobs/instances.bin",
    "CGMaterialResourceWin10": "materials.json",
    "CGTextureResourceWin10": "textures/",
    "cgtextureresourceWin10": "textures/",
    "CGShaderSetResourceWin10": "materials.json (shaderset_hash only)",
    "CGSceneResourceWin10": "lightmaps.json lights + volume_lights",
    "CGLightMapResourceWin10": "lightmaps/ + lightmaps.json",
    "CGReflectionProbeResourceWin10": "probes/ + manifest.reflection_probes",
    "CGFSEffectsResourceWin10": "effects.json",
    "CTextureOverrideCRWin10": "texture_overrides.json",
    "CScriptCRWin10": "scripts.json",
    "CStaticInstanceModelCRWin10": "static_entities.json",
    "CGStaticInstanceResourceWin10": "static_entities.json",
    "CTransformCRWin10": "blobs/instances.bin",
    "CParticleEffectCRWin10": "effects.json (emitters)",
    "CGTextureStreamingResourceWin10": "textures/ (mip selection)",
    "CActorDataResourceWin10": "scripts.json / static_entities.json (actor ids)",
}

rows = []
for t in ships:
    d = decoders_for(t)
    rows.append({"type": t, "decoded": bool(d), "decoders": d[:4],
                 "mentions": mentions(t),
                 "exported": EXPORTS.get(t, ""), "flagged_no_decoder": t in no_decoder})

dec = [r for r in rows if r["decoded"]]
exp = [r for r in rows if r["exported"]]
both = [r for r in rows if r["decoded"] and r["exported"]]
dec_only = [r for r in rows if r["decoded"] and not r["exported"]]
neither = [r for r in rows if not r["decoded"] and not r["exported"]]

out = []
w = out.append
w("# What we have from a level, and what we do not\n")
w("Audit of `mpl_arena_a` -- the most complete package -- across three")
w("questions that are usually collapsed into one.\n")
w("| | count |")
w("|---|--:|")
w("| Resource types the level ships | **%d** |" % len(rows))
w("| Something in this tree parses | %d |" % len(dec))
w("| Reaches the Blender package | %d |" % len(exp))
w("| Both parsed AND exported | **%d** |" % len(both))
w("| Parsed but never exported | %d |" % len(dec_only))
w("| Neither | %d |" % len(neither))
w("")
w("The middle row is the real number: %d of %d types are understood well enough"
  % (len(both), len(rows)))
w("to reach Blender. The rest are the gap.\n")
w("## Exported (the level's visible half)\n")
w("| type | carried by |")
w("|---|---|")
for r in sorted(exp, key=lambda r: r["type"]):
    w("| `%s` | %s |" % (r["type"], r["exported"]))
w("")
w("## Parsed but NOT exported -- understood, invisible to Blender\n")
w("| type | parsed by |")
w("|---|---|")
for r in sorted(dec_only, key=lambda r: r["type"]):
    w("| `%s` | %s |" % (r["type"], ", ".join(r["decoders"]) or "-"))
w("")
w("## Neither parsed nor exported\n")
for r in sorted(neither, key=lambda r: -r["mentions"]):
    w("* `%s` -- named in %d source file(s), parsed by none"
      % (r["type"], r["mentions"]))
w("")
w("## Package contents\n")
w("Files: %s\n" % ", ".join("`%s`" % f for f in pkg_files if not f.endswith(".bak")))
w("`manifest.json` keys: %s\n" % ", ".join("`%s`" % k for k in man_keys))

(DOCS / "decoded" / "LEVEL_GAPS.md").write_text("\n".join(out), encoding="utf-8")
print("types shipped        : %d" % len(rows))
print("  parsed somewhere   : %d" % len(dec))
print("  exported to Blender: %d" % len(exp))
print("  BOTH               : %d" % len(both))
print("  parsed, not exported: %d" % len(dec_only))
print("  neither            : %d" % len(neither))
print("\nwrote docs/decoded/LEVEL_GAPS.md")
