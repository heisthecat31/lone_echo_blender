"""Offline `scene.json` builder for a REAL level archive (M4 generate step).

`scripts/le_scene.py` established the world formula and the EParentType rules and
is the authority for both; this module is the *level* front-end that turns a real
archive's two placement manifests into the `scene.json` the add-on consumes, and
it reports **why every dropped row was dropped** instead of silently emitting a
short table.

    python3 blender_tool/le_mesh/scene_build.py \
        --archive 0703fd2acd5803e9 \
        --out blender_tool/exports/bridge/scene.json

Pure stdlib. No archive, no Oodle, no bpy: both inputs are pre-baked TSVs of a
few hundred KB, streamed with `csv`.

------------------------------------------------------------------------------
WHY THIS EXISTS ALONGSIDE `scripts/le_scene.py`
------------------------------------------------------------------------------
`le_scene.build_scene` builds its parent-resolution table from the *placed-model*
rows only, i.e. from actors that carry an `ncaModel`. On the reference archive
`0703fd2acd5803e9` that table holds **285 of the 696** actor nodes the transform
container actually ships, so **5 eTransform placements whose parent carries no
model of its own** were reported as `eTransform parent <hash> not in archive
manifest` — a false negative, not a real gap. Feeding the parent table from the
FULL transform manifest resolves them (186 -> 191 resolved on the bridge).

That substitution is safe on evidence, not assumption:
  * the transform manifest is the same `CTransformCR` container the placed-model
    manifest was joined from (`resource_hash == <archive>`),
  * it is a strict superset — 285/285 placed actors appear in it, and
  * the two copies of every actor row (the manifest carries the join against the
    model container twice, 697 + 697 rows) never disagree: 0 of 696 actors have
    conflicting pos/rot/scale/parent fields.

Everything else — the world formula, EParentType semantics, the eAuto/eJoint/
eRefPoint UNRESOLVED verdicts — is `le_scene`'s and is imported, not re-derived.

------------------------------------------------------------------------------
★★ THE RUNTIME RULE, READ OUT OF THE EXECUTABLE (`--etransform=runtime`, DEFAULT)
------------------------------------------------------------------------------
`le_scene`'s `parentWorld . M_init . M_offset` is **REFUTED by the code**. The
placement algorithm is `CTransformCS::FinishInitTransformCI` (offset 0x1873f0,
build-verified pair the game executable + its symbol file,
`that build`), which switches on
`STransformCD::SProperties.parenttype` at record `+0x40` (record stride `0xA8`,
confirmed by the `imul ..., 0xa8` at `0x187494`) and then calls
`CTransformCS::AttachToTransform` (`0x186240`). `AttachToTransform` **overwrites
the node's local rotation+position** with an offset chosen by
`EAttachOffsetType` — so `initialxf` is NEVER multiplied onto the parent:

  * `eAttachOffsetAuto` (1) — local := `parentLocalToLevel^-1 . childLocalToLevel`
    (`CTransfQ::Invert` `0x188320` + `CTransfQ::Prepend` `0x188950`).
    The **world transform is preserved**, i.e. `world = initialxf`.
  * `eAttachOffsetSnap` (0) — local := IDENTITY, i.e. `world = parentWorld`
    and `initialxf` is DISCARDED.
  * `eAttachOffsetFixed` (2) — local := `{identity rot, transformoffset.offset}`,
    i.e. `world = parentWorld . T(offset)`; `initialxf` is DISCARDED and
    `transformoffset.rotation` is never read on this path.

Per `parenttype`, at load:

  | parenttype     | what the runtime does                        | world      |
  |----------------|----------------------------------------------|------------|
  | eNone (0)      | attach to level root, local := initialxf      | initialxf  |
  | eAuto (1)      | AttachToTransform(..., offsettype **hardcoded 1**) | initialxf |
  | eTransform (2) | AttachToTransform(..., `transformoffset.transformoffsettype`) | per table above |
  | eJoint (3)     | local := initialxf now; re-attached to a model joint by `CTransformCS::InitializeModelAttach` (`0x187ff0`) | needs the rig |
  | eRefPoint (4)  | same as eJoint                               | needs the rig |

⇒ `M_offset` is **never composed with `M_init`** anywhere. `--etransform=compose`
is kept only so an old `scene.json` can be reproduced; it is WRONG.

------------------------------------------------------------------------------
The three statistical readings this replaces (they agreed with the code)
------------------------------------------------------------------------------
`le_scene` documents `eTransform -> parentWorld . M_init . M_offset`. Composing
that on the bridge DOUBLES every eTransform child's translation, and three
independent lines of evidence said the child row is already world — and the
`offset_type` census now explains *why*: of the 17 bridge eTransform children,
**10 are `eAttachOffsetAuto`** (world == initialxf) and **7 are
`eAttachOffsetSnap`** (world == parentWorld, and their `initialxf` is
bit-identical to the parent's, which is exactly the "7 of 17 identical"
observation below):

  1. **17 of 17** eTransform children sit within 0.198 m of their PARENT's
     stored translation (7 of them bit-identical). A parent-relative row would
     be a small offset from the parent, not a copy of the parent's world
     position. `etransform_evidence()` measures this; `format_report()` shouts
     when the chosen mode contradicts it.
  2. The composed translations are exactly 2x the parent's, to 4 decimals
     (0.0037 -> 0.0074, 1.7827 -> 3.5654, -6.1808 -> -12.3616). A clean doubling
     is the signature of double-counting, not of a layout.
  3. The VISUAL ORACLE. `37670868d7884949` is the holotable hologram and
     `19557c94c6d17883` the SENNA console. Reference `bridge-004` shows the
     hologram floating directly above the table. `world` puts it 1.19 m above
     the console at the same x/z (3 cm apart horizontally); `compose` puts it
     6.4 m down-room at ceiling height, matching no reference.

The DEFAULT is now `runtime`, which needs no per-archive evidence because it is
the executable's own branch structure; `etransform_evidence()` is kept as an
independent cross-check and `format_report()` still shouts when a manually
chosen mode contradicts it.

------------------------------------------------------------------------------
WHAT IS *NOT* RESOLVED, AND WHY (bridge `0703fd2acd5803e9`, measured)
------------------------------------------------------------------------------
  * **eAuto (106 placements)** — ★★ SOLVED FROM CODE, no longer needs-disasm.
    `FinishInitTransformCI` `0x187709` does NOT read `parentxf` at all: it takes
    the component's own `lookup.actornodeid` (`SProperties+0x08`), binary-searches
    it in every `SNodeGraph` of the game space (`CncaGameSpace::NodeGraphs`
    `0x101f70`), walks one link up with `SNodeGraph::Parent` (`0x86cdc0`), maps
    that parent NODE id back to an actor with `CncaGameSpace::LocalActor`
    (`0x1019f0`), and takes that actor's transform component via the vtable slot
    `+0xc0` `Component(SActor, CSymbol64)` with an **invalid** name symbol —
    which `CTransformCS::Component(SComponentID)` (`0x186530`) then substitutes
    with the component system's own name at `CS+0x28`, i.e. **`ncaTransform`**.
    That is exactly why `parent_actor_hash` is the null sentinel and
    `parent_component` is the constant type name `ncaTransform` on disk: those
    fields are simply UNUSED on this path.
    ⇒ The parent rule is "the enclosing actor one step up the scene NODE GRAPH".
    ⇒ But the attach is issued with `EAttachOffsetType` **hardcoded to 1**
    (`mov r9d, 1` at `0x1879c9`), i.e. `eAttachOffsetAuto`, so the world
    transform is PRESERVED: **`world = initialxf`**, independently of which
    parent is picked. The parent link only governs later motion, never the
    static load pose — so an importer never needs the node graph.

    ★ `eauto_evidence()` measured the same thing statistically. On the bridge —
    245/245 eAuto rows
    are non-zero, and their translation hull `x[-7.6,6.1] y[-4.0,3.5]
    z[-31.1,7.5]` is the same room the eNone WORLD rows describe
    (`x[-7.6,6.1] y[-3.9,3.9] z[-31.1,7.9]`), with **243 of 245** (99 %) inside
    it and **0** at the origin. A parent-relative population would cluster at small
    offsets instead. It agrees with the disassembly. `--eauto=runtime` (DEFAULT)
    resolves on the CODE; `--eauto=world` is the old statistics-only reading and
    `--eauto=unresolved` is kept for reproducing an old `scene.json`.
  * **eJoint (2 placements)** — STILL UNRESOLVED, and the code says why:
    `InitializeModelAttach` (`0x187ff0`) re-attaches these to a bone on the
    parent actor's rig via `AttachToJointOrRefPoint` (`0x185e70`), passing the
    FULL `transformoffset` (rotation `+0x88` IS read here, unlike the
    eTransform path). Needs that actor's skeleton, so it stays flagged.
  * **eRefPoint** — 0 rows in this archive.

------------------------------------------------------------------------------
NOT COVERED HERE (unchanged from `le_scene`)
------------------------------------------------------------------------------
`CTransformCR` enumerates only hero/actor placements. Bulk environment scatter
lives in `CGStaticInstanceResourceWin7` and is the `.lescatter` path
(`scripts/le_static_scatter.py`), not this one. A level assembled from this
`scene.json` alone is the ACTOR layer of the room, not the whole room.

★ AND ON THIS ARCHIVE THE OTHER LAYER IS IN A DIFFERENT ARCHIVE. The bridge's
own 54 `CGStaticInstanceResourceWin7` are all 148-byte EMPTY placeholders and its
self-named mesh-list is a 48-byte stub, so there is no local scatter to add: the
room shell lives in the PARENT level named by `CGameLevelResourceWin7 +0x00`
(`le_mesh/level_link.py`) — `0703f239d74801fe` `stn_int_itc_master`, whose static
master is 74,081 B / 95 meshes / 371 instances. Both layers are already in the
same world space. When a `level_link.json` is available it is embedded here as
`"level_link"` so a `scene.json` says out loud that it is only half a level.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

_LE_MESH = Path(__file__).resolve().parent
_BLENDER_TOOL = _LE_MESH.parent
_LE_ROOT = _BLENDER_TOOL.parent
for _p in (str(_BLENDER_TOOL), str(_LE_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# THE authority for the world formula / EParentType rules -- imported, never
# re-implemented, so this module cannot drift from `scripts/le_scene.py`.
import le_scene  # noqa: E402

from le_mesh import level_link  # noqa: E402

E_NONE = le_scene.E_NONE
E_AUTO = le_scene.E_AUTO
E_TRANSFORM = le_scene.E_TRANSFORM
E_JOINT = le_scene.E_JOINT
E_REFPOINT = le_scene.E_REFPOINT
PARENT_TYPE_NAME = le_scene.PARENT_TYPE_NAME

SCENE_FORMAT = le_scene.SCENE_FORMAT
#: v1 · v2 `stats.rows` / `stats.geometry` · v3 (this) the optional `level_link`
#: block naming the PARENT level this archive's shell comes from. Purely
#: ADDITIVE: every v1/v2 key keeps its name and meaning, and an absent
#: `level_link` means "not looked up", never "no parent".
#: v4 (this) adds the per-placement `offset_type` / `offset_type_name` naming the
#: `EAttachOffsetType` the runtime rule branched on, and the new `runtime` modes.
#: Still purely ADDITIVE: every v1-v3 key keeps its name and meaning.
SCENE_VERSION = 4
COORDINATE_SYSTEM = le_scene.COORDINATE_SYSTEM

MANIFEST_DIR = _LE_ROOT / "generic_rebuilds"
PLACED_TSV = "placed_model_manifest_{archive}.tsv"
TRANSFORM_TSV = "transform_manifest_{archive}.tsv"


# --- inputs -------------------------------------------------------------------

def manifest_paths(archive: str, manifest_dir=None):
    """`(placed_tsv, transform_tsv)` for an archive hash. Neither is read here."""
    d = Path(manifest_dir) if manifest_dir else MANIFEST_DIR
    return (d / PLACED_TSV.format(archive=archive),
            d / TRANSFORM_TSV.format(archive=archive))


def read_tsv(path) -> list:
    """Stream a manifest TSV into dict rows (both are < 1 MB; single stream)."""
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def offsets_from_transform_rows(rows) -> dict:
    """`{(resource_hash, row_index): (offset_rot, offset_vec)}` — the
    `transformoffset` the placed-model TSV omits. Same key `le_scene` looks up.

    ⚠ LOAD-BEARING in general (46 corpus rows carry a non-identity offset, up to
    a 180 deg flip) even though it is identity on all 1394 bridge rows."""
    f = le_scene._fnum
    out = {}
    for r in rows:
        key = ((r.get("resource_hash") or "").lower(), r.get("row_index"))
        out[key] = (
            (f(r.get("offset_rot_x")), f(r.get("offset_rot_y")),
             f(r.get("offset_rot_z")), f(r.get("offset_rot_w"), 1.0)),
            (f(r.get("offset_x")), f(r.get("offset_y")), f(r.get("offset_z"))),
        )
    return out


#: `EAttachOffsetType` — the engine's own type names, and the branch order in
#: `CTransformCS::AttachToTransform` `0x1863a8..0x1863f3` (`name-confirmed`).
OFFSET_SNAP = 0                    # local := identity        -> world = parentWorld
OFFSET_AUTO = 1                    # local := parent^-1.child -> world = initialxf
OFFSET_FIXED = 2                   # local := T(offset)       -> world = parentWorld.T(offset)
OFFSET_TYPE_NAME = {OFFSET_SNAP: "eAttachOffsetSnap",
                    OFFSET_AUTO: "eAttachOffsetAuto",
                    OFFSET_FIXED: "eAttachOffsetFixed"}

ETRANSFORM_RUNTIME = "runtime"     # DEFAULT: the executable's own rule, per offset_type
ETRANSFORM_COMPOSE = "compose"     # REFUTED: world = parentWorld . M_init . M_offset
ETRANSFORM_WORLD = "world"         # world = M_init . M_offset (row already world)
ETRANSFORM_MODES = (ETRANSFORM_RUNTIME, ETRANSFORM_COMPOSE, ETRANSFORM_WORLD)

EAUTO_RUNTIME = "runtime"          # DEFAULT: world = initialxf (offsettype hardcoded Auto)
EAUTO_UNRESOLVED = "unresolved"    # le_scene's old verdict: flag, never place
EAUTO_WORLD = "world"              # the statistics-only reading of the same answer
EAUTO_MODES = (EAUTO_RUNTIME, EAUTO_UNRESOLVED, EAUTO_WORLD)


def eauto_evidence(transform_rows) -> dict:
    """Is an eAuto row's `initialxf` parent-RELATIVE, or already WORLD?

    ⛔ The parent is not recoverable FROM THESE FIELDS and this does not pretend
    otherwise. On the bridge, `parent_actor_hash` is the null sentinel on 245/245
    eAuto actors and `parent_component` is the constant TYPE name `ncaTransform`
    on all 245 — a component kind, not an instance.
    ★ The disassembly explains that: `FinishInitTransformCI` `0x187709` never
    READS `parentxf` on the eAuto path; it walks the scene NODE GRAPH instead.
    And because the attach is issued with `EAttachOffsetType` hardcoded to
    `eAttachOffsetAuto`, the parent's identity does not affect the load pose at
    all. This function is therefore a CROSS-CHECK of the code, not the evidence
    the answer rests on; see `resolve_world_runtime` and the module header.

    What CAN be measured is whether the row's own matrix is already world. If it
    were parent-relative to some unknown parent, the translations would be small
    offsets clustered near the origin; if it is world, they fill the same room
    volume the eNone rows (whose world == their own `initialxf`, `le_scene`) fill.
    This returns that comparison and nothing else — `suggests` is a reading of the
    numbers, never a resolution of the parent.

    Keys: `auto`/`none` counts, `auto_at_origin`, per-axis `auto_bounds`/
    `none_bounds`, `inside_none_hull` (eAuto rows inside the eNone AABB),
    `hull_overlap` (fraction), `suggests`.
    """
    f = le_scene._fnum
    seen: dict = {}
    for r in transform_rows or ():
        a = r.get("actor_node_hash")
        if a and a not in seen:
            seen[a] = r

    def xyz(r):
        return (f(r.get("pos_x")), f(r.get("pos_y")), f(r.get("pos_z")))

    auto = [xyz(r) for r in seen.values()
            if int(r.get("parent_type") or 0) == E_AUTO]
    none_ = [xyz(r) for r in seen.values()
             if int(r.get("parent_type") or 0) == E_NONE]
    out = {"auto": len(auto), "none": len(none_),
           "auto_at_origin": sum(1 for p in auto if max(abs(v) for v in p) <= 1e-6),
           "auto_bounds": None, "none_bounds": None,
           "inside_none_hull": 0, "hull_overlap": 0.0,
           "suggests": EAUTO_UNRESOLVED}
    if not auto or not none_:
        return out
    lo_a = [min(p[i] for p in auto) for i in range(3)]
    hi_a = [max(p[i] for p in auto) for i in range(3)]
    lo_n = [min(p[i] for p in none_) for i in range(3)]
    hi_n = [max(p[i] for p in none_) for i in range(3)]
    out["auto_bounds"] = [lo_a, hi_a]
    out["none_bounds"] = [lo_n, hi_n]
    inside = sum(1 for p in auto
                 if all(lo_n[i] - 1e-3 <= p[i] <= hi_n[i] + 1e-3 for i in range(3)))
    out["inside_none_hull"] = inside
    out["hull_overlap"] = inside / len(auto)
    # A parent-relative population would sit near the origin and would NOT fill
    # the eNone room hull. Require both: almost none at the origin, and almost
    # all inside the hull the world rows describe.
    if out["auto_at_origin"] <= 0.02 * len(auto) and out["hull_overlap"] >= 0.95:
        out["suggests"] = EAUTO_WORLD
    return out


def etransform_evidence(transform_rows, table=None, near: float = 0.25) -> dict:
    """Is `initialxf` on an eTransform child parent-RELATIVE or already WORLD?

    ⛔ This is the one place `le_scene`'s documented world formula is questioned,
    so the question is answered with a measurement, not an opinion. For each
    eTransform child, compare its OWN translation with its parent's:

      * parent-relative rows would be small offsets (a child 6 m from its parent
        means the prop is 6 m away from what it is attached to), so the distance
        between the two stored translations is ~ the parent's distance from the
        origin;
      * already-world rows sit ON their parent, so the distance is centimetres —
        and composing then DOUBLES the translation.

    Returns the census: `children`, `near_parent` (within `near` metres),
    `identical`, `max_distance`, and `suggests` (the mode the data supports).
    ⚠ A verdict here is per-ARCHIVE evidence, never a global re-derivation of the
    format; `build_scene` still does what its caller asks.
    """
    rows_by_actor = {}
    for r in transform_rows or ():
        a = r.get("actor_node_hash")
        if a and a not in rows_by_actor:
            rows_by_actor[a] = r

    def xyz(r):
        f = le_scene._fnum
        return (f(r.get("pos_x")), f(r.get("pos_y")), f(r.get("pos_z")))

    children = near_parent = identical = missing = 0
    worst = 0.0
    for a, r in rows_by_actor.items():
        if (int(r.get("parent_type") or 0)) != E_TRANSFORM:
            continue
        children += 1
        p = rows_by_actor.get(r.get("parent_actor_hash", ""))
        if p is None:
            missing += 1
            continue
        c, q = xyz(r), xyz(p)
        d = sum((c[i] - q[i]) ** 2 for i in range(3)) ** 0.5
        worst = max(worst, d)
        if d <= near:
            near_parent += 1
        if d <= 1e-6:
            identical += 1
    suggests = ETRANSFORM_COMPOSE
    if children and near_parent == children - missing and children > missing:
        suggests = ETRANSFORM_WORLD
    return {"children": children, "parent_missing": missing,
            "near_parent": near_parent, "near_threshold": near,
            "identical": identical, "max_distance": worst,
            "suggests": suggests}


def offset_types_from_transform_rows(rows) -> dict:
    """`{(resource_hash, row_index): EAttachOffsetType}` from `offset_type`.

    ⛔ A row whose `offset_type` column is absent or non-numeric is OMITTED, not
    defaulted — `None` from a lookup on this dict means "not known", which the
    runtime resolver reports as unresolved rather than guessing `eAttachOffsetAuto`.
    """
    out = {}
    for r in rows or ():
        raw = r.get("offset_type")
        if raw in (None, ""):
            continue
        try:
            v = int(raw)
        except (TypeError, ValueError):
            continue
        out[((r.get("resource_hash") or "").lower(), r.get("row_index"))] = v
    return out


def build_transform_table(transform_rows, placed_rows=None, offsets=None,
                          offset_types=None) -> dict:
    """`{actor_node_hash: transform record}` — the parent-resolution table.

    Built from the FULL transform container so a parent that carries no model of
    its own is still a usable chain link (see the module header). `placed_rows`
    is folded in afterwards ONLY for actors the transform table does not already
    have, so a placed-only fallback still works when the transform TSV is absent.
    """
    table: dict = {}

    def _add(r, key_fields):
        a = r.get("actor_node_hash")
        if not a or a in table:
            return
        t = le_scene.transform_from_row(r, offsets)
        # `offset_type` is the EAttachOffsetType the runtime switches on. It is
        # NOT in `le_scene`'s record, so attach it here. Absent => None (unknown),
        # which stays distinguishable from a legitimate 0 (`eAttachOffsetSnap`).
        ot = None
        raw = r.get("offset_type")
        if raw not in (None, ""):
            try:
                ot = int(raw)
            except (TypeError, ValueError):
                ot = None
        if ot is None and offset_types:
            hk, ik = key_fields
            ot = offset_types.get(((r.get(hk) or "").lower(), r.get(ik)))
        t["offset_type"] = ot
        table[a] = t

    for r in transform_rows or ():
        _add(r, ("resource_hash", "row_index"))
    for r in placed_rows or ():
        _add(r, ("transform_resource_hash", "transform_row_index"))
    return table


def resolve_world_runtime(actor: str, transforms: dict, cache: dict, stack: set):
    """`(world_xf, resolved, reason)` under the **executable's** placement rule.

    This is `CTransformCS::FinishInitTransformCI` (`0x1873f0`) +
    `CTransformCS::AttachToTransform` (`0x186240`), not a statistical reading —
    see the module header for the branch-by-branch derivation. The one thing it
    never does is multiply `M_init` onto the parent, and the one thing it never
    reads on this path is `transformoffset` for anything but `eAttachOffsetFixed`.

    ⛔ Unknowns are named, never guessed: an eTransform row with no `offset_type`,
    an out-of-archive parent, an unresolvable parent chain and a parent cycle each
    return `resolved=False` with a distinct reason, carrying the row's own
    `initialxf` so a consumer has something honest to look at.
    """
    if actor in cache:
        return cache[actor]
    t = transforms.get(actor)
    if t is None:
        return le_scene._IDENT4, False, f"actor {actor} has no transform row"
    init = le_scene.transform_matrix(t["pos"], t["rot"], t["scale"])
    if actor in stack:
        return init, False, f"parent cycle detected at actor {actor}"

    pt = t["parent_type"]
    if pt == E_NONE:
        # 0x187b3f: attach to the level root, local := initialxf.
        res = (init, True, None)
    elif pt == E_AUTO:
        # 0x1879c9: `mov r9d, 1` -- eAttachOffsetAuto is HARDCODED, so whichever
        # node-graph parent the runtime picks, the world transform is preserved.
        res = (init, True, None)
    elif pt == E_TRANSFORM:
        ot = t.get("offset_type")
        if ot == OFFSET_AUTO:
            # 0x1863cb: local := parent^-1 . child -> world preserved.
            res = (init, True, None)
        elif ot in (OFFSET_SNAP, OFFSET_FIXED):
            pa = t.get("parent_actor") or ""
            if pa in le_scene.NULL_HASHES or pa not in transforms:
                res = (init, False,
                       f"eTransform/{OFFSET_TYPE_NAME[ot]} needs its parent's "
                       f"world, but parent {pa or '<null>'} is not in the "
                       f"archive manifest")
            else:
                stack.add(actor)
                pworld, presolved, preason = resolve_world_runtime(
                    pa, transforms, cache, stack)
                stack.discard(actor)
                if ot == OFFSET_SNAP:
                    # 0x1863f3: local := identity -> world == parentWorld.
                    world = pworld
                else:
                    # 0x1863b6: local := {identity rot, transformoffset.offset}.
                    world = le_scene._mat_mul(
                        pworld, le_scene.offset_matrix((0.0, 0.0, 0.0, 1.0),
                                                       t["offset_vec"]))
                res = ((world, True, None) if presolved else
                       (world, False, f"eTransform parent unresolved: {preason}"))
        else:
            res = (init, False,
                   "eTransform offset_type unknown (the transform manifest's "
                   "`offset_type` column is required to pick the attach rule); "
                   "supply --transform")
    elif pt == E_JOINT:
        res = (init, False,
               "eJoint is re-attached to a bone by InitializeModelAttach "
               "(0x187ff0 -> AttachToJointOrRefPoint 0x185e70); needs the parent "
               "actor's skeleton")
    elif pt == E_REFPOINT:
        res = (init, False,
               "eRefPoint is re-attached to a model ref point by "
               "InitializeModelAttach (0x187ff0); needs the parent actor's model")
    else:
        res = (init, False, f"unknown parent_type {pt}")

    cache[actor] = res
    return res


# --- scene --------------------------------------------------------------------

def load_level_link(path) -> dict:
    """Read a `level_link.json` (`le_mesh/level_link.py --out`).

    Raises `ValueError` on the wrong format rather than returning `{}` — a
    resolver that answers a miss with an empty dict reads downstream as a level
    with no parent, which is exactly the failure this file exists to prevent.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("format") != level_link.LINK_FORMAT:
        raise ValueError(f"{path}: not a {level_link.LINK_FORMAT} file "
                         f"(format={data.get('format')!r})")
    return data


def find_level_link(*dirs):
    """First `level_link.json` in `dirs`, or None. None means NOT LOOKED UP."""
    for d in dirs:
        if not d:
            continue
        p = Path(d) / "level_link.json"
        if p.is_file():
            return p
    return None


def build_scene(placed_rows, archive: str, transform_rows=None,
                offsets=None, etransform: str = ETRANSFORM_RUNTIME,
                link: dict = None, eauto: str = EAUTO_RUNTIME,
                offset_types=None) -> dict:
    """`scene.json` dict: `model_asset_hash -> [placements]`, plus a full census.

    A placement's identity is `(actor_node_hash, model_asset_hash)`. the reference
    join cross-products the transform container against the model container, so
    the raw rows are many-to-one over that pair and are deduped here — the dedupe
    is COUNTED (`stats.rows.duplicate_pairs`), never silent.

    `etransform` selects how an eTransform child is resolved:
      * `"runtime"` (DEFAULT) — the executable's own rule, branching on the row's
        `EAttachOffsetType`. Needs no per-archive evidence.
      * `"compose"` — `le_scene`'s documented formula. ⛔ REFUTED by the code;
        kept only to reproduce a `scene.json` generated before that was known.
      * `"world"` — treat the child's own `initialxf` as already world; the
        statistics-only reading that `"runtime"` supersedes.

    `eauto` likewise: `"runtime"` (DEFAULT, code-confirmed `world = initialxf`),
    `"world"` (the same answer from statistics) or `"unresolved"` (the old
    flag-and-never-place verdict). The mode is recorded in the emitted file so a
    `scene.json` always says which convention produced it.
    """
    if etransform not in ETRANSFORM_MODES:
        raise ValueError(f"etransform must be one of {ETRANSFORM_MODES}")
    if eauto not in EAUTO_MODES:
        raise ValueError(f"eauto must be one of {EAUTO_MODES}")
    if offset_types is None and transform_rows:
        offset_types = offset_types_from_transform_rows(transform_rows)
    table = build_transform_table(transform_rows, placed_rows, offsets, offset_types)
    # Report the parent type the DISK carries, whatever resolution we then use.
    disk_parent_type = {a: t["parent_type"] for a, t in table.items()}
    # `runtime` routes to the disassembly-derived resolver; the two legacy modes
    # keep going through `le_scene.resolve_world` via the table rewiring below,
    # so an old `scene.json` stays byte-reproducible.
    runtime_rule = etransform == ETRANSFORM_RUNTIME
    resolver = resolve_world_runtime if runtime_rule else le_scene.resolve_world
    if etransform == ETRANSFORM_WORLD:
        # Collapse the chain by declaring each eTransform row's own matrix final.
        # Done on the table (not in the resolver) so `le_scene.resolve_world`
        # stays the single implementation of the world formula.
        for t in table.values():
            if t["parent_type"] == E_TRANSFORM:
                t["parent_type"] = E_NONE
    if eauto == EAUTO_WORLD and not runtime_rule:
        # The statistics-only reading, under a legacy etransform mode: declare
        # the eAuto row's own matrix final. The DISK parent type is already in
        # `disk_parent_type`, so every such placement still reports
        # `parent_type_name: "eAuto"` and additionally carries `resolved_by`.
        for t in table.values():
            if t["parent_type"] == E_AUTO:
                t["parent_type"] = E_NONE

    cache: dict = {}
    placements: dict = {}
    seen: dict = {}

    rows_total = 0
    dropped_no_model = dropped_no_actor = duplicate_pairs = 0
    by_pt = {name: 0 for name in PARENT_TYPE_NAME.values()}
    resolved_by_pt = {name: 0 for name in PARENT_TYPE_NAME.values()}
    reasons: dict = {}
    resolved_count = unresolved_count = 0
    geo_total = geo_resolved = 0
    meshlist_keys_with_geo = set()

    for r in placed_rows:
        rows_total += 1
        model = r.get("model_asset_hash") or ""
        actor = r.get("actor_node_hash") or ""
        if not model:
            dropped_no_model += 1
            continue
        if not actor:
            dropped_no_actor += 1
            continue
        key = (actor, model)
        if key in seen:
            duplicate_pairs += 1
            continue
        seen[key] = True

        world, resolved, reason = resolver(actor, table, cache, set())
        t = table[actor]
        pt = disk_parent_type.get(actor, t["parent_type"])
        if runtime_rule and pt == E_AUTO and eauto == EAUTO_UNRESOLVED:
            # Explicit opt-OUT of the code-confirmed eAuto answer. Suppressed
            # AFTER resolution, so the row still carries its own matrix.
            resolved, reason = False, "eAuto suppressed by --eauto=unresolved"
        ptn = PARENT_TYPE_NAME.get(pt, f"pt{pt}")
        by_pt[ptn] = by_pt.get(ptn, 0) + 1
        if resolved:
            resolved_count += 1
            resolved_by_pt[ptn] = resolved_by_pt.get(ptn, 0) + 1
        else:
            unresolved_count += 1
            reasons[reason or "unknown"] = reasons.get(reason or "unknown", 0) + 1

        has_geo = r.get("meshlist_present", "") == "1"
        if has_geo:
            geo_total += 1
            meshlist_keys_with_geo.add(model)
            if resolved:
                geo_resolved += 1

        entry = {
            "actornodeid": actor,
            "world_xf": [float(v) for v in world],
            "parent_type": pt,
            "parent_type_name": ptn,
            "scale": t["scale"],
            "start_visible": r.get("start_visible", "") == "1",
            "resolved": resolved,
            "meshlist_present": has_geo,
            "mesh_count": int(r["mesh_count"]) if r.get("mesh_count") else 0,
        }
        if reason:
            entry["reason"] = reason
        if runtime_rule and pt in (E_TRANSFORM, E_JOINT, E_REFPOINT):
            # Name the attach rule that produced (or blocked) this world.
            ot = t.get("offset_type")
            entry["offset_type"] = ot
            entry["offset_type_name"] = OFFSET_TYPE_NAME.get(ot) if ot is not None else None
        if resolved and pt == E_AUTO:
            if runtime_rule:
                entry["resolved_by"] = (
                    "eauto=runtime (FinishInitTransformCI 0x187709 attaches with "
                    "EAttachOffsetType hardcoded to eAttachOffsetAuto, so the "
                    "world transform is preserved: world == initialxf)")
            elif eauto == EAUTO_WORLD:
                entry["resolved_by"] = ("eauto=world (row's own initialxf taken as "
                                        "world; the runtime parent is still unknown)")
        placements.setdefault(model, []).append(entry)

    out = {
        "format": SCENE_FORMAT,
        "version": SCENE_VERSION,
        "archive": archive,
        "coordinate_system": COORDINATE_SYSTEM,
        "etransform_mode": etransform,
        "eauto_mode": eauto,
        "stats": {
            "placement_count": resolved_count + unresolved_count,
            "resolved": resolved_count,
            "unresolved": unresolved_count,
            "by_parent_type": by_pt,
            "resolved_by_parent_type": resolved_by_pt,
            "unresolved_reasons": reasons,
            "meshlist_keys": len(placements),
            "rows": {
                "total": rows_total,
                "dropped_no_model_asset": dropped_no_model,
                "dropped_no_actor_node": dropped_no_actor,
                "duplicate_pairs": duplicate_pairs,
                "distinct_placements": resolved_count + unresolved_count,
                "transform_table_actors": len(table),
            },
            "geometry": {
                "placements_with_meshlist": geo_total,
                "placements_with_meshlist_resolved": geo_resolved,
                "meshlist_keys_with_geometry": len(meshlist_keys_with_geo),
            },
        },
        "placements": placements,
    }
    if link is not None:
        # ⛔ Present only when a link was actually decoded. An ABSENT key means
        # "not looked up"; `parent_level: null` inside a present block means the
        # on-disk slot really held the CSymbol64 null sentinel. They are
        # different facts and must stay distinguishable.
        out["level_link"] = {
            "parent_level": link.get("parent_level"),
            "component_space": link.get("component_space"),
            "source": link.get("format"),
            "note": ("the room shell is a STATIC-INSTANCE master in the parent "
                     "level, not in this archive; import its .lescatter alongside "
                     "these placements (same world space, no extra offset)"),
        }
    return out


# --- reporting ----------------------------------------------------------------

def translation_bounds(scene: dict, geometry_only: bool = True):
    """`(lo, hi)` of the placement ORIGINS in RAD space, or `None` when empty.

    ⚠ This is the bound of the placement POINTS, not of the placed geometry --
    the real world AABB needs the meshes and is measured in Blender."""
    pts = []
    for plist in scene.get("placements", {}).values():
        for p in plist:
            if geometry_only and not p.get("meshlist_present"):
                continue
            w = p["world_xf"]
            pts.append((w[3], w[7], w[11]))
    if not pts:
        return None
    return ([min(p[i] for p in pts) for i in range(3)],
            [max(p[i] for p in pts) for i in range(3)])


def coverage_against_packages(scene: dict, package_dir) -> dict:
    """Which `<archive>_<model>.lemesh` packages a scene actually places.

    Names the CONTAINER and its coverage rather than assuming the export set and
    the manifest agree — a package with no placement and a placement with no
    package are different failures and are counted separately.
    """
    d = Path(package_dir)
    pkgs = set()
    if d.is_dir():
        for p in d.glob("*.lemesh"):
            stem = p.name[:-len(".lemesh")]
            pkgs.add(stem.split("_", 1)[1] if "_" in stem else stem)
    with_geo = {h for h, pl in scene.get("placements", {}).items()
                if any(x.get("meshlist_present") for x in pl)}
    placed = sorted(pkgs & with_geo)
    return {
        "package_dir": str(d),
        "packages_on_disk": len(pkgs),
        "meshlist_keys_with_geometry": len(with_geo),
        "packages_placed": len(placed),
        "packages_without_placement": sorted(pkgs - with_geo),
        "placements_without_package": sorted(with_geo - pkgs),
        "placement_rows_for_packages": sum(
            len(scene["placements"][h]) for h in placed),
    }


def format_report(scene: dict, evidence: dict = None) -> str:
    st = scene["stats"]
    rows = st["rows"]
    lines = [
        f"archive {scene['archive']}  format {scene['format']} v{scene['version']}"
        f"  etransform={scene.get('etransform_mode', ETRANSFORM_RUNTIME)}"
        f" eauto={scene.get('eauto_mode', EAUTO_RUNTIME)}",
        f"rows {rows['total']} -> {rows['distinct_placements']} distinct placements "
        f"(dropped: {rows['duplicate_pairs']} duplicate (actor,model) join rows, "
        f"{rows['dropped_no_model_asset']} no model asset, "
        f"{rows['dropped_no_actor_node']} no actor node)",
        f"parent table: {rows['transform_table_actors']} actors",
        f"resolved {st['resolved']} / unresolved {st['unresolved']}",
    ]
    for name in ("eNone", "eAuto", "eTransform", "eJoint", "eRefPoint"):
        n = st["by_parent_type"].get(name, 0)
        if n:
            lines.append(f"  {name:<11} {n:>4}  resolved "
                         f"{st['resolved_by_parent_type'].get(name, 0)}")
    for reason, n in sorted(st["unresolved_reasons"].items(),
                            key=lambda kv: -kv[1]):
        lines.append(f"  UNRESOLVED x{n}: {reason}")
    g = st["geometry"]
    lines.append(f"geometry-bearing placements {g['placements_with_meshlist']} "
                 f"({g['placements_with_meshlist_resolved']} resolved) across "
                 f"{g['meshlist_keys_with_geometry']} meshlist keys")
    b = translation_bounds(scene)
    if b:
        lo, hi = b
        lines.append("placement-origin bounds RAD "
                     f"{[round(v, 3) for v in lo]}..{[round(v, 3) for v in hi]} "
                     f"extent {[round(hi[i] - lo[i], 3) for i in range(3)]}")
    if evidence and evidence["children"]:
        mode = scene.get("etransform_mode", ETRANSFORM_RUNTIME)
        lines.append(
            f"eTransform evidence: {evidence['near_parent']}/{evidence['children']} "
            f"children sit within {evidence['near_threshold']} m of their parent's "
            f"stored translation ({evidence['identical']} identical, max "
            f"{evidence['max_distance']:.3f} m) -> suggests {evidence['suggests']}")
        if mode == ETRANSFORM_RUNTIME:
            # `runtime` is the executable's rule; the statistics are a corroborating
            # cross-check, not an authority that can contradict it.
            lines.append(
                "  (mode 'runtime' resolves per-row from EAttachOffsetType -- the "
                "evidence above is a cross-check, not the deciding input)")
        elif evidence["suggests"] != mode:
            # NEVER silent: the default must shout when the data contradicts it.
            lines.append(
                f"  ⚠ WARNING: mode is {mode!r} but every eTransform child already "
                f"carries its parent's WORLD translation, so composing DOUBLES it. "
                f"Re-run with --etransform={evidence['suggests']}.")
    return "\n".join(lines)


# --- CLI ----------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", required=True, help="archive hash, e.g. 0703fd2acd5803e9")
    ap.add_argument("--manifest-dir", default=None,
                    help=f"directory holding the reference TSVs (default {MANIFEST_DIR})")
    ap.add_argument("--placed", default=None, help="explicit placed-model TSV")
    ap.add_argument("--transform", default=None, help="explicit transform TSV")
    ap.add_argument("--out", default=None, help="scene.json output path")
    ap.add_argument("--packages", default=None,
                    help="package dir to report placement coverage against")
    ap.add_argument("--level-link", default=None,
                    help="a level_link.json (le_mesh/level_link.py --out) to embed. "
                         "When omitted, one beside --out or --packages is used")
    ap.add_argument("--eauto", default=EAUTO_RUNTIME, choices=list(EAUTO_MODES),
                    help="how to treat an eAuto placement: 'runtime' (DEFAULT, "
                         "code-confirmed world == initialxf), 'world' (the same "
                         "answer from statistics) or 'unresolved' (the old "
                         "flag-and-never-place verdict)")
    ap.add_argument("--etransform", default=ETRANSFORM_RUNTIME,
                    choices=list(ETRANSFORM_MODES),
                    help="how to resolve an eTransform child: 'runtime' (DEFAULT, "
                         "the executable's rule branching on EAttachOffsetType), "
                         "'compose' (le_scene's documented formula -- REFUTED by "
                         "the code, kept only for reproducing old output) or "
                         "'world' (the child row is already world)")
    args = ap.parse_args(argv)

    placed_p, transform_p = manifest_paths(args.archive, args.manifest_dir)
    if args.placed:
        placed_p = Path(args.placed)
    if args.transform:
        transform_p = Path(args.transform)
    if not placed_p.exists():
        print(f"ERROR: placed-model manifest not found: {placed_p}", file=sys.stderr)
        return 2

    placed_rows = read_tsv(placed_p)
    transform_rows = []
    offsets = None
    if transform_p.exists():
        transform_rows = read_tsv(transform_p)
        offsets = offsets_from_transform_rows(transform_rows)
    else:
        print(f"WARNING: transform manifest {transform_p} not found -- parent table "
              f"falls back to placed rows only and transformoffset is assumed "
              f"identity", file=sys.stderr)

    link_p = Path(args.level_link) if args.level_link else find_level_link(
        Path(args.out).parent if args.out else None, args.packages)
    link = None
    if link_p:
        link = load_level_link(link_p)      # raises on the wrong format

    scene = build_scene(placed_rows, args.archive, transform_rows, offsets,
                        etransform=args.etransform, link=link, eauto=args.eauto)
    print(format_report(scene, etransform_evidence(transform_rows or placed_rows)))
    ev = eauto_evidence(transform_rows or placed_rows)
    if ev["auto"]:
        print(f"eAuto evidence: {ev['inside_none_hull']}/{ev['auto']} eAuto rows lie "
              f"inside the eNone world hull ({ev['hull_overlap']:.0%}), "
              f"{ev['auto_at_origin']} at the origin -> suggests {ev['suggests']} "
              f"(mode={scene.get('eauto_mode')}) -- ⛔ the runtime PARENT is "
              f"unknown either way")
    if link:
        print(f"level_link {link_p}: parent_level="
              f"{link.get('parent_level') or '(null sentinel)'} -- the room shell "
              f"is that level's static-instance master, not this archive's")
    else:
        print("level_link: not looked up (run le_mesh/level_link.py --archive "
              f"{args.archive} --out <dir>/level_link.json)")

    if args.packages:
        cov = coverage_against_packages(scene, args.packages)
        print(f"packages: {cov['packages_placed']} of {cov['packages_on_disk']} on disk "
              f"are placed ({cov['placement_rows_for_packages']} placement rows); "
              f"{len(cov['packages_without_placement'])} package(s) with no placement, "
              f"{len(cov['placements_without_package'])} placement key(s) with no package")
        for h in cov["packages_without_placement"]:
            print(f"  no placement for package {h}")
        for h in cov["placements_without_package"]:
            print(f"  no package for placed meshlist {h}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(scene, indent=1), encoding="utf-8")
        print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
