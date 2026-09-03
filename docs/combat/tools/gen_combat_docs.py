"""Generate docs/combat/*.md from _survey.json (facts) + the decoded formats."""
import json, pathlib, sys, struct, collections

sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor")
from echo_editor.assemble.authoring import AuthoringBackend
A = AuthoringBackend(); A.activate()
U = lambda s: A.rad_hash(s) & 0xFFFFFFFFFFFFFFFF

DOCS = pathlib.Path(r"J:\EchoVR-Tools-Launcher\lone_echo_blender\docs\combat")
S = json.loads((DOCS / "_survey.json").read_text(encoding="utf-8"))
LEVELS = ("mpl_combat_fission", "mpl_combat_gauss", "mpl_combat_dyson",
          "mpl_combat_combustion", "mpl_combat_war_room", "mpl_lobby_b_combat")
SHORT = {l: l.replace("mpl_combat_", "").replace("mpl_", "") for l in LEVELS}

flat, res, other = {}, {}, {}
for t, v in S.items():
    shapes = {p["shape"] for p in v["per_level"].values()}
    if "flat-cr" in shapes:   flat[t] = v
    elif "resource" in shapes: res[t] = v
    else:                      other[t] = v


def one(v, key):
    for p in v["per_level"].values():
        if p.get(key) is not None:
            return p[key]
    return None


def counts_row(v):
    return " | ".join(str(v["per_level"][l]["count"]) if l in v["per_level"] and
                      v["per_level"][l].get("count") is not None else "-" for l in LEVELS)


# ---------------------------------------------------------------- CR_TYPES.md
rows = []
for t in sorted(flat):
    v = flat[t]
    st = one(v, "stride")
    nh = one(v, "node_hashes")
    node = "0x%016X" % nh[0] if nh else "-"
    conf = "yes" if any(p.get("confidence") == "confirmed"
                        for p in v["per_level"].values()) else "no"
    rows.append("| `%s` | %s | %d | %s | %s | %s |"
                % (t, node, st, conf, "0x%016X" % U(t), counts_row(v)))

hdr = " | ".join(SHORT[l] for l in LEVELS)
(DOCS / "CR_TYPES.md").write_text(f"""# Combat CR types — measured layouts

Every type the level index marks `no_decoder` that turns out to be a **flat
component record** (the 56-byte CR envelope, then `count` fixed-size entries and
nothing else). Measured directly from the shipped bytes of all six combat levels.

**Stride is not guessed.** For each type, `stride = data_size / count`, and the
value agreed across **every level that ships the type** — all 61 of them, no
exceptions. That agreement is the check: a wrong stride would disagree between a
map with 12 entries and one with 700.

**"Binds actors" = confirmed.** A flat CR is confirmed when (a) every entry's
`+0` holds the *same* value — the component-system node hash — and (b) every
entry's `+8` is an actor that really exists in that level's `CActorDataResource`.
55 of 61 pass both. The 6 that do not are marked `no`: their rows reference
actors from outside the level (pooled/instanced actors), so the `+8` test cannot
confirm them, though the stride still agrees everywhere.

Counts are entries per level.

| Type | CS node hash (+0) | Stride | Binds actors | Type hash | {hdr} |
|""" + "---|" * (5 + len(LEVELS)) + """
""" + "\n".join(rows) + "\n", encoding="utf-8")

# --------------------------------------------------------------- RESOURCES.md
rrows = []
for t in sorted(res):
    v = res[t]
    sizes = " | ".join(str(v["per_level"][l]["bytes"]) if l in v["per_level"] else "-"
                       for l in LEVELS)
    rrows.append("| `%s` | 0x%016X | %s |" % (t, U(t), sizes))
(DOCS / "RESOURCES.md").write_text(f"""# Combat resource types — not component records

These {len(res)} types do **not** use the CR envelope: their `+8` field is not
`len - 56`, so they are standalone resources (geometry, physics, audio, UI,
scripts), each with its own container format. Sizes in bytes per level, which is
the honest state of knowledge for most of them.

Two are decoded in detail elsewhere in this folder:

* `CScriptResourceWin10` / `StreamingScriptWin10` — see **SCRIPTS.md**
* `CComponentSpaceResourceWin10` — see **COMPONENT_SPACE.md**

| Type | Type hash | {hdr} |
|""" + "---|" * (2 + len(LEVELS)) + """
""" + "\n".join(rrows) + "\n", encoding="utf-8")

print("CR_TYPES.md   : %d flat types" % len(flat))
print("RESOURCES.md  : %d resource types" % len(res))
print("other         : %s" % ", ".join(sorted(other)))
