"""`SGStaticInstanceLODData` — the Lone Echo static-scatter LOD system.

Pure stdlib (no oodle, no bpy). Decodes the LOD block that sits at the FRONT of a
populated `CGStaticInstanceResourceWin7` master's primary bytes, plus the four
per-instance / per-mesh tail tables that bind it to instances, and derives the
one thing a consumer actually wants: **for every instance, which LOD level it is**.

Why this module exists
----------------------
The mesh-list LOD fields (`CGRenderParams.lodprimsetidx/lodchildrenstart/
lodchildrencount` and `CGMeshListData.lodchildindices`) are populated in only 11
of 1,240 retail mesh-lists (`blender_tool/tests/audit_lod_fields.py`) and in NONE
of the mesh-lists inlined into the scatter masters — `lodchildindices.count == 0`
in all 62 of them (`blender_tool/tests/audit_static_lod_corpus.py`). Static
scatter carries a *second*,
fully populated LOD system, and it is the one the game actually runs. Every LOD
level of a prop is a SEPARATE mesh with its OWN instances in the same master; the
grouping lives here. Import every instance and you stack all LOD levels of every
prop on top of each other (61.3 % of station_front's 21,394 instances are
lower-LOD duplicates).

Disk grammar (LE-Win7 stream-confirmed on **all 62 populated masters** in the
the reference manifest — 40 of its 102 archives bake no populated master;
`name-confirmed` names and order against the engine's own `SGStaticInstancesData`,
`SGStaticInstanceLODData` and `SHierLOD`)

    ---- SGStaticInstancesData.lod : SGStaticInstanceLODData ----------------
    nodes             CTableA<CReal4,16>  u32 count, 12 B pad, count*16   (vec4, w==0)
    lodfadeslopeoffs  CTableA<CReal4,16>  u32 count, 12 B pad, count*16   (per LOD)
    hierlods          CTable<SHierLOD>    u32 count, count*12
                                          {u32 parent, u32 firstchild, u32 numchildren}
    nodelookup        CTable<u16>         u32 count, count*2   (per LOD -> node)
    totalnumlods      u64                 (8-aligned)
    ---- then the rest of SGStaticInstancesData ---------------------------
    meshlist          CGMeshListData      inline (see le_meshlist_decode)
    instancescount    CTable<u32>   num_meshes
    instanceoffsets   CTable<u32>   num_meshes   (prefix sum of instancescount)
    irrsamplelocs     CTable<C3Vector>  num_meshes
    dirlightmasks     CTable<u8>    num_instances
    visstrlookup      CTable<u16>   num_instances
    lodfadelookup     CTable<u32>   num_instances   <- instance -> LOD index
    ditherfadeflags   CTable<u32>   ceil(num_meshes/32)   <- a per-MESH BITSET
    u32 totalinstances, numvisentries, instancedataoffset, instancedatasize,
        instancetypedataoffset, instancetypedatasize          (the fixed 24-B tail)

Tables are byte-packed back to back with **no inter-table padding** (a
`CTable<u16>` header can therefore land on an odd offset — station_front's
`visstrlookup` header sits at 1,033,002). Only the two `CTableA<T,16>` 12-byte
internal pads and the `u64` 8-align exist. Every table header is re-validated
against its expected count by `decode_static_lod`, so a layout drift raises rather
than silently mis-parsing.

The model
---------
* a **node** (`nodes[i]`, a world position) is one LOD GROUP — one placed prop.
* a group's LOD levels are a CONTIGUOUS run in the LOD array; `nodelookup` maps
  LOD index -> node, so `level = lod_index - first_lod_index_of(node)`.
* instance `i` draws at `level_of_instance[i]` of group `group_of_instance[i]`.
* a level may own MORE THAN ONE instance: LOD0 of a multi-part prop is several
  meshes, and coarser levels collapse them (station_front node 35: L0 = meshes
  2+3+4 = 1,856 tris, L1 = 5+6+7 = 1,028, L2 = 8+9+10 = 712, L3 = mesh 11 = 412,
  L4 = mesh 12 = 32).
* the FRONT of the LOD array is `hierlods` cluster proxies, not prop levels.
  `hierlods[i] = (parent, firstchild, numchildren)` where `parent` is a LOD index
  and `[firstchild, firstchild+numchildren)` is a run of LOD entries the proxy
  stands in for. Only 14 of 62 masters carry any. Corpus-measured: **no instance
  ever references a parent entry** (0 refs on all four masters checked), the
  parent's node sits at the CENTROID of its children's nodes (0.4-8.6 units off,
  children spread 1.9-21.8), and parent nodes never reappear as leaf nodes.
  `parent == record index` on 13 of the 14, but NOT on `3c157c98a146325a`, where
  three records share `parent == 9` — so `parent` is a real field, not the index.

Corpus caveats (measured over all 62 populated masters — do NOT re-derive these
from station_front + min_itc alone, which is how the first two got stated too
strongly):

* "total triangles never increase with level" is a STRONG TENDENCY, NOT an
  invariant: 196 of 72,004 multi-level groups increase at some level (21 of 62
  masters), and 32 groups exceed their own LOD 0. It is 0/4,921 on station_front
  and 0/2,065 on min_itc, which is why it read as absolute. The usual shape is a
  coarsest level that MERGES the parts (7 meshes / 2,740 tris -> 1 mesh /
  2,899 tris on `87e57e7feed4f12f` group 2071): fewer draws, slightly more
  triangles. Instance count increases somewhere in 197 groups likewise.
* `nodelookup` is monotonic non-decreasing on 61 of 62 masters, NOT all:
  `3c157c98a146325a` stores `nodelookup[0:12] = 0..10, 9`. The descent is inside
  the `hierlods` parent region and no instance references it.

`visstrlookup` (`stream-confirmed`, 62/62 masters): per instance, the index of
its LOD GROUP's visibility entry. It is CONSTANT across every instance of a
group, a BIJECTION group <-> value, its values are exactly `0..numgroups-1`, and
the tail scalar `numvisentries` equals its distinct count. It is NOT the identity
permutation over instances (station_front first diverges at instance 16).

`lodfadeslopeoffs` is carried through verbatim as audit metadata; its four floats
are labelled `inferred` — do NOT present them as switch distances. What IS now
pinned, DXBC-confirmed from the shipped instanced vertex shaders
(`0ba36df7805485cd` / `5109c3131191ad81` in archive `0703fd2acd5803e9`): the VS
reads `SGPackedInstanceData.lodfadeidx` (+0x28, == `lodfadelookup[i]`), indexes
`k_instlodfadeamounts` (`Buffer<float4>` t3) at `view*stride + lodfadeidx`, takes
ONLY `.x`, and passes it to the PS, which turns it into an alpha-to-coverage
DITHER mask (`oMask = (1 << (alpha*fade)/(1/(n+1))) - 1`, `discard_z`). So the
per-LOD value the GPU consumes is a per-frame CPU-computed SCALAR in [0,1], and
`lodfadeslopeoffs` is its CPU-side input — it never reaches the GPU as a vec4.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

__all__ = [
    "StaticLodData", "decode_static_lod", "select_lod_instances",
    "LOD_ALL", "LOD_COARSEST",
]

LOD_ALL = -1        # keep every instance (pre-LOD behaviour: all levels stacked)
LOD_COARSEST = -2   # keep each group's LAST level (the cheapest silhouette)

_TAIL = 24          # the six trailing u32 scalars of SGStaticInstancesData


@dataclass
class StaticLodData:
    """Decoded `SGStaticInstanceLODData` + its per-instance binding."""

    # --- raw tables ---------------------------------------------------------
    nodes: list                  # [(x, y, z, w)] world anchor per LOD group
    fades: list                  # [(f0, f1, f2, f3)] per LOD entry (semantics: inferred)
    hierlods: list               # [(parent, firstchild, numchildren)] per SHierLOD
    nodelookup: list             # [node index] per LOD entry
    totalnumlods: int            # engine's own count (may differ from len(nodelookup))
    lodfadelookup: list          # [LOD index] per instance
    instancescount: list         # per mesh
    instanceoffsets: list        # per mesh (prefix sum)
    ditherfadeflags: list        # ceil(num_meshes/32) u32 words — a per-MESH bitset
    visstrlookup: list = field(default_factory=list)   # per instance -> vis entry
    numvisentries: int = 0       # tail scalar; == len(set(visstrlookup))

    # --- derived binding ----------------------------------------------------
    group_of_instance: list = field(default_factory=list)   # instance -> node
    level_of_instance: list = field(default_factory=list)   # instance -> 0..k-1
    group_num_levels: dict = field(default_factory=dict)    # node -> k (levels in use)

    meshlist_offset: int = 0     # where the inline CGMeshListData begins
    warnings: list = field(default_factory=list)

    @property
    def num_groups(self) -> int:
        return len(self.group_num_levels)

    @property
    def max_level(self) -> int:
        return max(self.group_num_levels.values(), default=1) - 1

    def levels_of_instance(self, i: int) -> int:
        """How many LOD levels the group owning instance `i` has."""
        return self.group_num_levels.get(self.group_of_instance[i], 1)

    def fade_of_instance(self, i: int):
        """The raw `lodfadeslopeoffs` vec4 for instance `i` (semantics: inferred)."""
        li = self.lodfadelookup[i]
        return self.fades[li] if li < len(self.fades) else (0.0, 0.0, 0.0, 0.0)

    def dither_fade(self, mesh_index: int) -> bool:
        """`ditherfadeflags` bit for a mesh index."""
        w, b = divmod(mesh_index, 32)
        return bool(self.ditherfadeflags[w] >> b & 1) if w < len(self.ditherfadeflags) else False


def _tableA16(blob: bytes, off: int, elem: int = 16):
    """`CTableA<T,16>`: u32 count, 12 B pad, count*elem."""
    count = struct.unpack_from("<I", blob, off)[0]
    data = off + 16
    return count, data, data + count * elem


def _back(blob: bytes, end: int, count: int, stride: int, label: str):
    """Locate the table that ENDS at `end` and holds exactly `count` elements.

    Tables are byte-packed, so the header sits at `end - 4 - count*stride`; the
    header value is re-read and asserted, which is what makes the backwards walk
    self-validating instead of positional guesswork.
    """
    hdr = end - 4 - count * stride
    if hdr < 0:
        raise ValueError(f"{label}: table runs off the front of the blob")
    got = struct.unpack_from("<I", blob, hdr)[0]
    if got != count:
        raise ValueError(f"{label}: header@{hdr} = {got}, expected {count}")
    return hdr, hdr + 4


def decode_static_lod(blob: bytes, num_meshes: int, num_instances: int) -> StaticLodData:
    """Decode the LOD system of a populated static-scatter master.

    `num_meshes` / `num_instances` come from
    `le_static_scatter.decode_static_master` (they are derived from the fixed
    24-byte tail). Raises `ValueError` if any table header fails to validate.
    """
    warnings: list[str] = []
    n = len(blob)
    if n < _TAIL + 32:
        raise ValueError(f"blob too small ({n} B) for a static master")

    # --- front: SGStaticInstanceLODData ------------------------------------
    n_nodes, nodes_at, off = _tableA16(blob, 0)
    n_fade, fade_at, off = _tableA16(blob, off)
    n_hier = struct.unpack_from("<I", blob, off)[0]
    hier_at = off + 4
    off = hier_at + n_hier * 12
    n_look = struct.unpack_from("<I", blob, off)[0]
    look_at = off + 4
    off = look_at + n_look * 2
    if n_look != n_fade:
        warnings.append(f"nodelookup count {n_look} != lodfadeslopeoffs count {n_fade}")

    # `totalnumlods` is a u64; the stream 8-aligns it. Both known masters were
    # already aligned, so accept either and let the mesh-list header decide.
    ml_off = 0
    totalnumlods = 0
    for cand_u64 in (((off + 7) // 8) * 8, off):
        end_u64 = cand_u64 + 8
        if end_u64 + 4 > n:
            continue
        if struct.unpack_from("<I", blob, end_u64)[0] == num_meshes:
            totalnumlods = struct.unpack_from("<Q", blob, cand_u64)[0]
            ml_off = end_u64
            break
    if not ml_off:
        raise ValueError("could not locate the inline CGMeshListData after totalnumlods")

    nodes = [struct.unpack_from("<4f", blob, nodes_at + i * 16) for i in range(n_nodes)]
    fades = [struct.unpack_from("<4f", blob, fade_at + i * 16) for i in range(n_fade)]
    hierlods = [struct.unpack_from("<3I", blob, hier_at + i * 12) for i in range(n_hier)]
    nodelookup = list(struct.unpack_from("<%dH" % n_look, blob, look_at)) if n_look else []

    # `totalnumlods` is AUTHORITATIVE and can be SMALLER than the table counts:
    # min_itc_master (4c47d84c1e52447a) stores 8,276 nodelookup/lodfadeslopeoffs
    # entries but totalnumlods == 8,274, and the two slack entries carry stale
    # values (they repeat node 0, which would otherwise read as a non-contiguous
    # LOD run). No instance references them — max(lodfadelookup) == 8,273. Trim to
    # the live range so the derived binding sees only real LOD entries.
    if 0 < totalnumlods < len(nodelookup):
        slack = len(nodelookup) - totalnumlods
        nodelookup = nodelookup[:totalnumlods]
        fades = fades[:totalnumlods]
        warnings.append(
            f"trimmed {slack} LOD entries past totalnumlods={totalnumlods} (stale slack)")

    # --- back: the per-mesh / per-instance tail tables ---------------------
    end = n - _TAIL
    nwords = -(-num_meshes // 32)
    e6, d = _back(blob, end, nwords, 4, "ditherfadeflags")
    ditherfadeflags = list(struct.unpack_from("<%dI" % nwords, blob, d)) if nwords else []
    e5, d = _back(blob, e6, num_instances, 4, "lodfadelookup")
    lodfadelookup = list(struct.unpack_from("<%dI" % num_instances, blob, d)) if num_instances else []
    e4, d = _back(blob, e5, num_instances, 2, "visstrlookup")
    visstrlookup = list(struct.unpack_from("<%dH" % num_instances, blob, d)) if num_instances else []
    e3, _ = _back(blob, e4, num_instances, 1, "dirlightmasks")
    e2, _ = _back(blob, e3, num_meshes, 12, "irrsamplelocs")
    e1, d = _back(blob, e2, num_meshes, 4, "instanceoffsets")
    instanceoffsets = list(struct.unpack_from("<%dI" % num_meshes, blob, d)) if num_meshes else []
    _e0, d = _back(blob, e1, num_meshes, 4, "instancescount")
    instancescount = list(struct.unpack_from("<%dI" % num_meshes, blob, d)) if num_meshes else []

    numvisentries = struct.unpack_from("<I", blob, n - _TAIL + 4)[0]

    data = StaticLodData(
        nodes=nodes, fades=fades, hierlods=hierlods, nodelookup=nodelookup,
        totalnumlods=totalnumlods, lodfadelookup=lodfadelookup,
        instancescount=instancescount, instanceoffsets=instanceoffsets,
        ditherfadeflags=ditherfadeflags, visstrlookup=visstrlookup,
        numvisentries=numvisentries, meshlist_offset=ml_off, warnings=warnings)
    _derive_levels(data)
    if visstrlookup and numvisentries != len(set(visstrlookup)):
        warnings.append(
            f"numvisentries={numvisentries} != {len(set(visstrlookup))} distinct "
            f"visstrlookup values")
    return data


def _derive_levels(d: StaticLodData) -> None:
    """instance -> (group, level) from `lodfadelookup` + `nodelookup`.

    A node's LOD entries are (almost always) contiguous, so the level is the
    offset of the instance's LOD index from its node's first LOD index. Two
    corpus-measured wrinkles are handled here:

    * **The front of the LOD array is `hierlods` PARENT entries.** They carry no
      instances, and their `nodelookup` rows are not always monotonic —
      `3c157c98a146325a` stores `nodelookup[0:12] = 0..10, 9`, so node 9 owns LOD
      entries 9 AND 11 with node 10's entry between them. That is diagnosed, not
      fatal: nothing references those entries.
    * **A group's FIRST LOD entry can be unreferenced.** On 6 of 62 masters
      (`1454b12d3ce15e38`, `513c36c202cc3469`, `5fc785e98bfa8179`,
      `87180b1b9bf8b3af`, `c660009ad6521671`, `cca8d487dd923ada`) a node's run
      starts one or more entries before the first entry any instance points at,
      so raw levels come out `1,2,3` and a request for LOD 0 selected NOTHING —
      21 groups / 94 instances silently vanished. Levels are therefore rebased
      per group so the finest level an instance actually draws is 0.
    """
    # Single pass: record each node's first LOD index and diagnose contiguity —
    # a node must never reappear once a different node has started.
    first_lod: dict = {}
    closed = set()
    prev = None
    for li, node in enumerate(d.nodelookup):
        if node != prev:
            if node in closed:
                d.warnings.append(f"node {node}: LOD entries are not contiguous")
            if prev is not None:
                closed.add(prev)
            prev = node
        first_lod.setdefault(node, li)

    levels: list = []
    gof: list = []
    for li in d.lodfadelookup:
        if li >= len(d.nodelookup):
            d.warnings.append(f"lodfadelookup index {li} out of range")
            gof.append(-1)
            levels.append(0)
            continue
        node = d.nodelookup[li]
        gof.append(node)
        levels.append(li - first_lod[node])

    # Rebase: a group's smallest DRAWN level becomes 0.
    base: dict = {}
    for node, lvl in zip(gof, levels):
        if node >= 0 and lvl < base.get(node, 1 << 30):
            base[node] = lvl
    rebased = sum(1 for v in base.values() if v)
    if rebased:
        d.warnings.append(
            f"rebased {rebased} LOD groups whose first LOD entry is unreferenced")
        levels = [lvl - base[node] if node >= 0 else lvl
                  for node, lvl in zip(gof, levels)]

    groups: dict = {}
    for node, lvl in zip(gof, levels):
        if node >= 0 and lvl + 1 > groups.get(node, 0):
            groups[node] = lvl + 1
    d.group_of_instance = gof
    d.level_of_instance = levels
    d.group_num_levels = groups


def select_lod_instances(level: int, group_of_instance, level_of_instance,
                         group_num_levels) -> list:
    """Global instance indices to keep for a requested LOD `level`.

    * `level >= 0` — that level, CLAMPED per group to its coarsest available one
      (a 2-level prop asked for LOD 3 yields its LOD 1, never nothing).
    * `LOD_ALL` (-1) — every instance, i.e. all levels stacked (pre-LOD behaviour).
    * `LOD_COARSEST` (-2) — each group's last level.
    """
    n = len(level_of_instance)
    if level == LOD_ALL:
        return list(range(n))
    out = []
    for i in range(n):
        k = group_num_levels.get(group_of_instance[i], 1)
        want = k - 1 if level == LOD_COARSEST else min(level, k - 1)
        if level_of_instance[i] == want:
            out.append(i)
    return out
