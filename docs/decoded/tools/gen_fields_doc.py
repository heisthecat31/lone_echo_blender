"""Emit docs/decoded/FIELDS.md -- a field map for every record type."""
import json, pathlib, collections

D = pathlib.Path(r"J:\EchoVR-Tools-Launcher\lone_echo_blender\docs\decoded")
FM = json.loads((D / "_fieldmap.json").read_text(encoding="utf-8"))
S = json.loads((D / "_all.json").read_text(encoding="utf-8"))
P = json.loads((D / "_pools.json").read_text(encoding="utf-8"))

# cross-references: actor refs at offsets other than +8
xref = {}
for t, v in FM.items():
    extra = [f for f in v["fields"]
             if f["kind"].startswith("actor ref") and f["offset"] != 8]
    if extra:
        xref[t] = extra

lines = ["""# Field map — every offset of every record type

Semantics can rarely be *proven* (see OPEN.md), but the **value domain** of every
field can be measured, and that is what this file records. Profiled over
**%d record types** and **%d sampled entries** across all levels.

How to read a row:

| Kind | Meaning |
|---|---|
| `zero` | every observed value is 0 |
| `0xFFFFFFFF` | every observed value is all-ones (the engine's "unset") |
| `constant` | one value everywhere — a node hash, a type tag, or a fixed flag |
| `actor ref` | the u64 here resolves in the level's `CActorDataResource` for >=99%% of entries |
| `float32` | every value is a finite float in a sane range, and they vary |
| `small int` | all values below 65536 — counts, indices, ids, flags |
| `hash-like` | high-entropy 64-bit values, mostly distinct — names or symbols |
| `mixed` | varies without fitting any of the above |

`+0` is the component-system node hash and `+8` is the owning actor; both are
part of the envelope (`../combat/CR_FORMAT.md`) and appear here for completeness.

**Offsets overlap.** A field is profiled at every 4-byte position, and a u64 at
`+16` is also visible as two u32s at `+16` and `+20`. Read neighbouring rows
together rather than as separate fields.

## Cross-references between actors

%d types carry an actor reference at an offset **other than** `+8` — that is a
record pointing at a second actor, which is structural information rather than a
value domain:

""" % (len(FM), sum(v["entries_sampled"] for v in FM.values()), len(xref))]

for t in sorted(xref):
    offs = ", ".join("`+%d` (%s)" % (f["offset"], f["note"]) for f in xref[t])
    lines.append("* `%s` -> %s" % (t, offs))

lines.append("\n## Per-type field maps\n")
for t in sorted(FM):
    v = FM[t]
    meta = S.get(t, {})
    pool = P.get(t)
    bits = ["stride **%d**" % v["stride"],
            "%d entries sampled" % v["entries_sampled"],
            "%d level(s)" % meta.get("levels", 0)]
    if meta.get("sha1_identical"):
        bits.append("**byte-identical across levels**")
    if pool and pool.get("solved"):
        bits.append("pooled, %d inline table(s) at %s"
                    % (pool["tables"], ", ".join("+%d" % o for o in pool["live_headers"])))
    elif pool:
        bits.append("pooled, layout unsolved")
    lines.append("### `%s`\n" % t)
    lines.append("%s — %s\n" % (meta.get("type_hash", ""), ", ".join(bits)))
    lines.append("| Offset | Kind | Detail |")
    lines.append("|---|---|---|")
    for f in v["fields"]:
        lines.append("| `+%d` | %s | %s |" % (f["offset"], f["kind"], f["note"] or ""))
    lines.append("")

(D / "FIELDS.md").write_text("\n".join(lines), encoding="utf-8")
print("FIELDS.md: %d types, %d offset rows, %d cross-reference types"
      % (len(FM), sum(len(v["fields"]) for v in FM.values()), len(xref)))
