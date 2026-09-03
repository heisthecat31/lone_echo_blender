# The CR envelope — how a component record is laid out

Every `*CR*Win10` resource in a level shares one container. Measured across all
six combat levels; the arithmetic below holds byte-exactly in every case.

## The 56-byte header

| Offset | Size | Meaning |
|---|---|---|
| `+0`  | u64 | owning level hash, or 0 |
| `+8`  | u64 | **data size** — bytes after the header |
| `+28` | u32 | sentinel, 1 on a live record |
| `+40` | u64 | **entry count** |
| `+48` | u64 | entry count again (kept equal in every shipped file) |

The single most useful test is at `+8`:

    data_size == len(file) - 56   ->  a component record (CR)
    data_size != len(file) - 56   ->  a standalone resource, own format

That test alone splits the 90 undecoded combat types into 61 CRs and 27
resources (plus 2 oddities). See **CR_TYPES.md** and **RESOURCES.md**.

## Flat records

Most CRs are flat: `data_size` divides evenly by `count`, and

    stride = data_size / count

Each entry then begins:

| Offset | Size | Meaning |
|---|---|---|
| `+0` | u64 | **component-system node hash** — the same value in every entry of the file |
| `+8` | u64 | **actor nodeid** — an entry in this level's `CActorDataResource` |

Both properties are checkable, and that is what makes a stride trustworthy
rather than guessed:

* the stride derived from one level must equal the stride derived from every
  other level that ships the type (it does, for all 61);
* `+0` must be constant within a file;
* `+8` must resolve in that level's actor table for every entry.

55 of 61 satisfy all three. The other 6 fail only the `+8` test because their
rows address pooled/instanced actors that are not in the level's own table.

`CComponentResource::Attach` binary-searches `+8` in the actor table and hard
fails on the first miss, so a carried CR whose rows name donor actors is a load
failure, not a silent no-op:

    Missing actor with nodeid 0x...        ccomponentresource.h:318

## Ordered-pool records

A few CRs are not flat: their entries are fixed-size but each carries several
**inline tables** whose bytes live in one pool after the entry array.

    layout:  56-byte header | count x stride entries | pool

Each inline table header inside an entry has its byte size at `header + 8`.
There is **no offset field** — `+0` and `+16` of every header are zero. The pool
is a plain ordered stream, consumed entry by entry, table by table, in order.

That is provable rather than assumed: summing every table size across every
entry equals the pool length exactly.

| Type | Stride | Table headers at | Verified pools (fission / gauss / dyson / combustion) |
|---|---|---|---|
| `CScriptCRWin10` | 720 | 48, 104, 160, 216, 272, 328, 384, 440, 496, 552, 608, 664 | 82832 / 86952 / 87616 / 80696 — all MATCH |
| `CListCRWin10` | 440 | 48, 104, 160, 216, 272, 328, 384 | 168 in all four — MATCH |
| `CR15NetBalanceSettingsCRWin10` | 88 | 32 | 64 in all four — MATCH |

The practical consequence: because the pool is positional and offset-free, any
**subset** of entries can be sliced out exactly — take the entries you want in
donor order, each with its own pool slice, and concatenate. No offset rewriting
is needed or possible. Conversely, sorting entries without carrying their pool
slices alongside mis-assigns every block.

## Records that are neither

`CTagCRWin10` looks flat but is not: in fission it is 424 bytes with
`data_size = 24` and `count = 1`, so `data_size != len - 56`. Cloning it whole
ships an entry the engine reads as a null actor:

    Missing actor with nodeid 0x0000000000000000
