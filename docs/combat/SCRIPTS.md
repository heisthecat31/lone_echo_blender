# Scripts — bindings, resources, and why they cannot be cloned

## A script is two archive resources, not a loose DLL

`bin/win10/scripts/<hash>.dll` is a cache. The script itself ships as two
resources, both keyed by the **script hash**:

| Type | Type hash | Size | Content |
|---|---|---|---|
| `StreamingScriptWin10` | `0x06EACE9F64204FD0` | ~200 KB | 20-byte header, then an embedded PE |
| `CScriptResourceWin10` | `0x991421B41DE58370` | 4 bytes | companion marker |

The archive ships 567 scripts under each type.

### The 20-byte header — DECODED

    +0   u64   build id, high half
    +8   u64   build id, low half
    +16  u32   size of the DLL payload
    +20  ...   the DLL (PE32+)
    +20+size   the PDB (Microsoft C/C++ MSF 7.00)

The resource is **DLL + PDB**, not just a DLL. For the streaming script the DLL
is 32,768 bytes and the PDB accounts for the remaining ~167 KB.

**The two 64-bit values are not a content hash — they are the script's BUILD
IDENTITY, and they are written in plain text inside the DLL.** The PE's CodeView
record names its PDB:

    d:\projects2
ad\dev\_data932408047
ad15\win10\multi\script_compile\output    3e1b045d874eb5c935eb275a67bb1601.pdb

`3e1b045d874eb5c9` is `+0` and `35eb275a67bb1601` is `+8`, concatenated.

**Verified on all 549 scripts in the archive: 549 of 549 match, 0 mismatches.**

So nothing has to be recomputed to clone a script. The pair is an identifier the
build system assigned, not a checksum over the bytes — and it can be chosen
freely, as long as the DLL's embedded PDB path is rewritten to agree (it is 32
hex characters, so any replacement is the same width).

⚠ **This corrects an earlier conclusion in this file.** The header was described
as "a content signature, so ANY edit to the PE invalidates the resource". That
was wrong. Edits to the PE do not invalidate anything, and the values are
copyable. The clone attempt failed for a different reason.

### Cloning a script — the working recipe

A script is **three** things, and all three must be present together. This was
the whole obstacle: two of them were shipped many times, never all three.

1. `StreamingScriptWin10` under the new hash — header + DLL + PDB
2. `CScriptResourceWin10` under the new hash — the 4-byte companion
3. **`bin/win10/scripts/<new hash>.dll`** — the loose DLL on disk

Each omission names itself:

| Missing | Error |
|---|---|
| (2) | `Cannot find resource 0x991421B41DE58370:0x...` |
| (3) | `Failed initializing resource 0x30A4750069F52E7F:0x...` |

`0x30A4750069F52E7F` is `rad_hash("CScriptResource")` — the **unsuffixed** name.
The engine logs types unsuffixed, which is why it reads like a fourth unknown
type rather than the resource you just shipped.

**Proven by isolation.** A byte-identical copy of the stock streaming script,
under a new hash, *failed* without the loose DLL and *loaded clean* with it —
streaming its target level, zero resource errors. So no edit to the PE was ever
the problem.

Steps, in full:

1. re-key the baked level hash (3 sites for the streaming script);
2. rewrite the export-directory module name `<old>.dll` -> `<new>.dll`
   (16 hex + `.dll`, same width);
3. pick a fresh build id, write it to `+0`/`+8`, and rewrite every occurrence of
   the embedded `<id>.pdb` filename to match (32 hex, same width);
4. emit both resources under the new hash;
5. register **both** types in the level's carchive `sec1`;
6. write the loose `scripts/<new hash>.dll` — the DLL payload, `blob[20:20+size]`.

The export is a generic `setup_bindings`, not a hash-derived symbol, so renaming
the module does not break lookup.

### What this unblocked, and what it did not

Cloned streaming scripts **do** bring in the war-room levels carrying the
terminal UI and deploy hologram — five of them reach "Finished loading" with our
map loaded alongside. But streaming them starts the real combat flow, and that
flow deploys into a combat map of its own choosing: it loads
`mpl_combat_fission`, not ours, and the duplicate `CR15NetBalanceSettings` hangs
the load (`cr15netbalancesettingscs.cpp:316`). Making the flow deploy into our
map is the open problem; the streaming itself is solved.
