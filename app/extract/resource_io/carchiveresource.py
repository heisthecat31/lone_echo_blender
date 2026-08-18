"""CArchiveResource (v3) — the level/section CLOSURE / load driver.

THE LOAD DRIVER (s144): the dedicated server/client loads exactly the resources
listed here; a resource absent from the closure is never loaded.

Two on-disk layouts exist (s61 corpus survey, stream-confirmed):

  Layout A  (word0 == 0)  — the per-level / per-section closure.  281/298 files,
                            incl. every standalone map (mpl_arena_a, gauss, …).
  Layout B  (word0 != 0)  — the master/global/menu AGGREGATOR archives (mnu_master,
                            r14_glb_global_*, mpl_tutorial_master, …). 17/298 files.
                            Distinct header (word0 = child count, word1 = 0xfa.. magic,
                            child-archive refs, then by-type edges). NOT decoded here.

This module structures **Layout A** and RAISES on Layout B (→ n/a-by-shape in the
round-trip harness; honest "not yet decoded", not a false byte-echo).

Layout A (stream-confirmed across 281 files):

    +0x00  u32  word0 = 0
    +0x04  u32  cnt                         number of Table-1 records
    +0x08  Table 1 : cnt x 16B  (type_key:u64, resource_hash:u64)
                                            grouped into contiguous same-type_key
                                            SECTIONS; type_key in col A, resource in
                                            col B. (Archive-tree edges instead key
                                            col B = parent map hash — same 16B grid.)
    ...    Table 2 : N x 16B  (field1:u32, type_hash:u64, tag:u32)
                                            one entry per distinct Table-1 section,
                                            SAME order as the sections; type_hash ==
                                            the section's col-A key; tag is a per-type
                                            constant (corpus-wide, 0 exceptions);
                                            field1[0] == N, field1[i>0] = loader index
                                            field (OPEN — derive via loader disasm).
    EOF-8  u64  tail                        role flag ∈ {0,1,2} (sub-archive / level /
                                            engine_assets). Not derivable from content.

NOTE the historical map-authoring finding framed Table-1 as
`(resource_hash@+0, type_symbol@+0x08)` starting at 0x10 with a `first_hash@0x08`.
That framing is OFF-BY-8 and column-SWAPPED; it round-trips only as a byte echo. The
canonical structure is the one above (matches map-conversion/findings/closure-v3-
format.md). The two framings happen to place the resource_hash field at the same
absolute offset, which is why the s144/s24 closure-repoint offsets remain correct.
"""
import struct

STRUCTURED = True
HDR = 8
REC = 16
T2REC = 16
TAIL = 8


class NotLayoutA(ValueError):
    """File is a Layout-B aggregator (or malformed); not a Layout-A closure."""


def read(buf):
    if len(buf) < HDR + TAIL:
        raise NotLayoutA("too short for a Layout-A closure")
    word0, cnt = struct.unpack_from("<II", buf, 0)
    if word0 != 0:
        raise NotLayoutA(f"word0=0x{word0:x} != 0 (Layout-B aggregator)")
    t1end = HDR + REC * cnt
    if t1end + TAIL > len(buf):
        raise NotLayoutA(f"cnt={cnt} overruns file ({len(buf)} bytes)")
    rem = len(buf) - t1end
    if (rem - TAIL) % T2REC != 0:
        raise NotLayoutA(f"trailer {rem} != 16*N + 8")

    table1 = [struct.unpack_from("<QQ", buf, HDR + REC * i) for i in range(cnt)]
    n2 = (rem - TAIL) // T2REC
    table2 = [struct.unpack_from("<IQI", buf, t1end + T2REC * k) for k in range(n2)]
    tail = struct.unpack_from("<Q", buf, len(buf) - TAIL)[0]
    return dict(word0=word0, count=cnt, table1=table1, table2=table2, tail=tail)


def write(obj):
    out = bytearray()
    out += struct.pack("<II", obj["word0"], obj["count"])
    for k, r in obj["table1"]:
        out += struct.pack("<QQ", k, r)
    for f1, h, tag in obj["table2"]:
        out += struct.pack("<IQI", f1, h, tag)
    out += struct.pack("<Q", obj["tail"])
    return bytes(out)


def roundtrip(buf):
    return write(read(buf))


# ---- structural helpers (not needed for round-trip; used by the generator) ----

def sections(table1):
    """Contiguous same-col-A runs: list of (type_key, [resource_hash, ...])."""
    out = []
    i = 0
    n = len(table1)
    while i < n:
        k = table1[i][0]
        j = i
        while j < n and table1[j][0] == k:
            j += 1
        out.append((k, [table1[x][1] for x in range(i, j)]))
        i = j
    return out


def validate(obj):
    """Cross-check the Table-2 index against Table-1 sections. Returns a dict of
    booleans; the generator/tests use it. Does not raise."""
    secs = sections(obj["table1"])
    t2 = obj["table2"]
    keys_match = [s[0] for s in secs] == [e[1] for e in t2]
    n_match = len(secs) == len(t2)
    f1_head = (t2[0][0] == len(t2)) if t2 else False
    return dict(n_match=n_match, keys_match=keys_match, f1_head_is_N=f1_head,
                n_sections=len(secs), n_table2=len(t2))
