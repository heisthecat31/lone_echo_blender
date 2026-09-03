"""Generate docs/decoded/*.md from _all.json + _pools.json."""
import json, pathlib, collections

D = pathlib.Path(r"J:\EchoVR-Tools-Launcher\lone_echo_blender\docs\decoded")
S = json.loads((D / "_all.json").read_text(encoding="utf-8"))
P = json.loads((D / "_pools.json").read_text(encoding="utf-8"))
idx = json.loads((D.parent / "evr_level_index.json").read_text(encoding="utf-8"))
ALLT = sorted({t for v in idx.values() for t in (v.get("no_decoder") or [])})
missing = [t for t in ALLT if t not in S]

rows = []
for t in sorted(S):
    v = S[t]
    shape = "+".join(v["shapes"])
    st = ",".join(str(x) for x in v["strides"]) or "-"
    conf = ("yes" if v.get("node_constant_everywhere") and v.get("entity_resolves_everywhere")
            else "no" if "flat-cr" in v["shapes"] or "pooled-cr" in v["shapes"] else "n/a")
    ident = "**identical**" if v["sha1_identical"] else ""
    pools = P.get(t)
    tbl = str(pools["tables"]) if pools and pools.get("solved") else ("?" if pools else "")
    rows.append("| `%s` | %s | %s | %s | %s | %d | %d | %s | %s |"
                % (t, v["type_hash"], shape, st, conf, v["levels"],
                   v["entries_examined"], tbl, ident))

(D / "ALL_TYPES.md").write_text("""# Every undecoded type in the archive — measured

All %d types the level index marks `no_decoder`, across all %d levels.
%d were found in the extracted data and are measured here; %d ship in no level
this extract contains (listed at the bottom).

Columns:

* **Shape** — `flat-cr` (entries only), `pooled-cr` (entries + an ordered pool),
  `resource` (not a component record), `empty-cr`, `tiny`. A type showing two
  shapes is flat in levels where its pool happens to be empty.
* **Stride** — `data_size / count`, and it agreed in **every** level for all
  %d record types. That cross-map agreement is the check.
* **Confirmed** — `+0` constant within each file (the CS node hash) *and* every
  `+8` resolving in that level's `CActorDataResource`.
* **Tables** — inline tables per entry, for pooled records whose layout is
  solved (see POOLED.md).
* **Identical** — the file is byte-for-byte the same in every level shipping it
  (see IDENTICAL.md).

| Type | Type hash | Shape | Stride | Confirmed | Levels | Entries | Tables | |
|---|---|---|---|---|---|---|---|---|
%s

## Not present in this extract (%d)

These are named by levels in the index but ship in packages this extract does
not contain — mostly global asset resources rather than level records.

%s
""" % (len(ALLT), len(idx), len(S), len(missing),
       sum(1 for v in S.values() if v["strides"]),
       "\n".join(rows), len(missing),
       "\n".join("* `%s`" % t for t in missing)), encoding="utf-8")

# ---- POOLED.md
solved = {t: v for t, v in P.items() if v.get("solved")}
uns = sorted(t for t in P if not P[t].get("solved"))
prows = []
for t in sorted(solved):
    v = solved[t]
    prows.append("| `%s` | %d | %d | %s |"
                 % (t, v["stride"], v["tables"],
                    ", ".join("+%d" % o for o in v["live_headers"]) or "-"))
(D / "POOLED.md").write_text("""# Pooled component records — inline table layouts

A pooled CR is:

    56-byte header | count x stride entries | ordered pool

Each entry carries inline table headers; a header's byte size is at `+8`. There
is **no offset field** — `+0` and `+16` of every header are zero, so the pool is
a plain ordered stream consumed entry by entry, table by table.

## How the headers were found

Searching for "a set of offsets whose sizes sum to the pool" is **ambiguous**: a
header that is zero in every entry can be added or dropped without changing the
sum, so many offset sets satisfy it. (An earlier pass did exactly this and
produced two different answers for the same type.)

So headers are identified by **shape** first — every offset where `+0` and `+16`
are zero in every entry of every level — and the sum is then used as the
*check*:

    sum over entries, over headers, of size  ==  pool length   (exactly)

That makes the answer canonical. It reproduces the layouts verified by hand:
`CR15NetBalanceSettingsCR` one table at `+32`, `CListCR` from `+48`,
`CScriptCR` from `+48` on a 56-byte pitch.

**%d of %d pooled types solved.** "Live" tables are those with a non-zero size
somewhere; the remaining candidate offsets are headers that are always empty.

| Type | Stride | Live tables | Header offsets |
|---|---|---|---|
%s

## Unsolved (%d)

Headers were not identified for these: either `+0`/`+16` are not zero (a
different header shape) or the pool is not a simple concatenation.

%s

## Why this matters

Because the pool is positional and offset-free, any **subset** of entries can be
sliced out exactly — take entries in donor order, each with its own pool slice,
and concatenate. No offset rewriting is possible or needed. Conversely, sorting
entries without carrying their slices alongside mis-assigns every block.
""" % (len(solved), len(P), "\n".join(prows), len(uns),
       "\n".join("* `%s` (stride %d)" % (t, P[t]["stride"]) for t in uns)),
    encoding="utf-8")

# ---- IDENTICAL.md
ident = {t: v for t, v in S.items() if v["sha1_identical"]}
irows = []
for t in sorted(ident):
    v = ident[t]
    sha = next(iter(v["per_level"].values()))["sha1"]
    lv = ", ".join(sorted(v["per_level"]))
    irows.append("| `%s` | %s | %s | %d | %s |"
                 % (t, ",".join(str(x) for x in v["strides"]) or "-", sha,
                    v["levels"], lv))
(D / "IDENTICAL.md").write_text("""# Byte-identical records — fixed templates

%d types are **byte-for-byte the same file** in every level that ships them,
sha1-verified. They are not authored per map; they are templates on fixed
actors.

The practical consequence: these can be carried verbatim into a new map. No
re-keying, no per-map authoring — the same bytes the shipped maps use.

Most of the combat weapon kit is in this list, which is why an "empty kit plus
CS declarations" approach works at all: the kit genuinely is constant.

| Type | Stride | sha1 (first 10) | Levels | Ships in |
|---|---|---|---|---|
%s
""" % (len(ident), "\n".join(irows)), encoding="utf-8")

print("ALL_TYPES.md  %d rows" % len(rows))
print("POOLED.md     %d solved / %d pooled" % (len(solved), len(P)))
print("IDENTICAL.md  %d byte-identical" % len(ident))
