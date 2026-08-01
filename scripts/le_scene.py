"""M4 scene / level placement extractor for the Lone Echo Blender tool.

Emits per-asset WORLD placements so imported `.lemesh` meshes land at their
level positions instead of stacking at the origin. This is the offline half of
scene placement; the Blender addon consumes `scene.json`.

Run offline (no game archive, no Oodle, no bpy) against the pre-baked join
manifest:

    python3 scripts/le_scene.py \
        --manifest generic_rebuilds/placed_model_manifest_0703fd2acd5803e9.tsv \
        --archive 0703fd2acd5803e9 \
        --out /path/to/scene.json

The pure matrix/resolve functions are importable (used by
blender_tool/tests/test_scene.py) without touching any archive.

------------------------------------------------------------------------------
LAYOUT
------------------------------------------------------------------------------
Each placed model routes through a `CTransformCR` row (STransformCD::SProperties,
a flat POD CTable whose in-memory offsets equal the on-disk offsets):

  * actornodeid   @+0x08   -- join key (one actor node == one placement identity)
  * initialxf     CTransfQ: r@+0x20 (quat x,y,z,w), t@+0x30 (vec3), s@+0x3C (uniform)
  * parenttype    u32 @+0x40  -- EParentType
  * parentxf.actor.nodeid @+0x48
  * jointorrefpointname   @+0x78
  * transformoffset @+0x80: type@0x80, rotation@+0x88 (quat), offset@+0x98 (vec3),
                            NO scale -> M_offset built with s = 1.0

EParentType:  eNone=0  eAuto=1  eTransform=2  eJoint=3  eRefPoint=4

World formula, per parent type:
  * eNone (0)      : world = M_init . M_offset                         [fully resolved]
  * eTransform (2) : world = parentWorld . M_init . M_offset           [chain-resolved]
                     parent = the CTransformCR row whose actornodeid == parentxf.actor.nodeid
  * eJoint (3)     : world = parentWorld . objectjoints[jointIdx] . M_offset
                     (rest-pose attach; needs the parent actor's skeleton -> UNRESOLVED here)
  * eAuto (1)      : runtime-selected parent                           [UNRESOLVED]
  * eRefPoint (4)  : shapelists.transformlist                          [UNRESOLVED, unimplemented]

`M_init = _mat_from_transfq(initialxf.r, initialxf.t, initialxf.s)` and
`M_offset = _mat_from_transfq(offset.rotation, offset.offset, 1.0)`, both reused
verbatim from `le_mesh.skinning` (row-major 4x4, translation in the last column).

NOTE on transformoffset: the placed-model TSV OMITS `transformoffset`, so it is
loaded from the *transform* manifest via `load_offsets` (supplied as
`--transform-manifest`, keyed by (resource_hash, row_index)). Corpus check (2026-07-23):
46 rows across 4 archives carry a NON-identity offset (up to a 180deg flip) which `le_scene`
composes correctly (`M_init . M_offset`) ONLY when the transform manifest is supplied -- so
`--transform-manifest` is LOAD-BEARING, not optional; without it M_offset defaults to identity
(correct only for all-identity-offset archives, which the reference archive happened to be).

Static instances are NOT covered here: `CTransformCR`
recovers only the ~hundreds of hero/actor placements. Every geometry-bearing level
ALSO bakes one populated `CGStaticInstanceResourceWin7` master (SGStaticInstancesData)
holding BULK environment scatter that `CTransformCR` does not enumerate:
totalinstances = 21394 across 1050 meshes in stn_ext_itc_station_front (8616 / 194 in
min_itc_master), bound as contiguous per-mesh runs (instancescount/instanceoffsets).
`scripts/le_static_scatter.py` decodes that structure; the per-instance world
transforms are packed in the GPU sibling `instancedata` region (undecoded follow-up).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

# --- reuse the verified matrix helpers from the skinning decoder (do not rewrite)
_LE_ROOT = Path(__file__).resolve().parent.parent
_BLENDER_TOOL = _LE_ROOT / "blender_tool"
if str(_BLENDER_TOOL) not in sys.path:
    sys.path.insert(0, str(_BLENDER_TOOL))

from le_mesh.skinning import _IDENT4, _mat_from_transfq, _mat_mul  # noqa: E402


# --- EParentType (VERIFIED) --------------------------------------------------
E_NONE = 0
E_AUTO = 1
E_TRANSFORM = 2
E_JOINT = 3
E_REFPOINT = 4

PARENT_TYPE_NAME = {
    E_NONE: "eNone",
    E_AUTO: "eAuto",
    E_TRANSFORM: "eTransform",
    E_JOINT: "eJoint",
    E_REFPOINT: "eRefPoint",
}

# Hashes that mean "no reference" on disk.
NULL_HASHES = {"", "0000000000000000", "ffffffffffffffff"}

SCENE_FORMAT = "lescene"
SCENE_VERSION = 1
COORDINATE_SYSTEM = "rad_engine"

# Reference archive default (the jck_arm / stn_int_itc_bridge corpus).
DEFAULT_MANIFEST = ("generic_rebuilds/"
                    "placed_model_manifest_0703fd2acd5803e9.tsv")
DEFAULT_ARCHIVE = "0703fd2acd5803e9"


# --- pure matrix composition (testable without archives) ---------------------

def transform_matrix(pos, rot, scale) -> tuple:
    """M_init from a CTransfQ initialxf (rot quat x,y,z,w; pos vec3; uniform scale)."""
    return _mat_from_transfq(rot, pos, scale)


def offset_matrix(offset_rot=(0.0, 0.0, 0.0, 1.0),
                  offset_vec=(0.0, 0.0, 0.0)) -> tuple:
    """M_offset from transformoffset (rotation quat + offset vec3, NO scale -> s=1.0).

    Identity when both are zero -- which is the case for every manifest-sourced
    placement, since the pre-baked TSV does not carry transformoffset."""
    return _mat_from_transfq(offset_rot, offset_vec, 1.0)


def local_matrix(pos, rot, scale,
                 offset_rot=(0.0, 0.0, 0.0, 1.0),
                 offset_vec=(0.0, 0.0, 0.0)) -> tuple:
    """The object's own placement, parent-relative: M_init . M_offset."""
    return _mat_mul(transform_matrix(pos, rot, scale),
                    offset_matrix(offset_rot, offset_vec))


# --- transform record + world resolution -------------------------------------

def _fnum(s, default=0.0) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def transform_from_row(row: dict, offsets: dict | None = None) -> dict:
    """Build a parent-resolvable transform record from a placed-model manifest row.

    Actor-invariant fields only (pos/rot/scale/parent linkage); the model/meshlist
    is attached later at placement time. The placed-model TSV omits `transformoffset`,
    so it is looked up from `offsets` (built from the transform manifest, which carries
    it) keyed by (transform_resource_hash, transform_row_index); identity if absent.
    """
    offset_rot, offset_vec = (0.0, 0.0, 0.0, 1.0), (0.0, 0.0, 0.0)
    if offsets:
        key = ((row.get("transform_resource_hash") or "").lower(),
               row.get("transform_row_index"))
        if key in offsets:
            offset_rot, offset_vec = offsets[key]
    return {
        "actor": row["actor_node_hash"],
        "pos": (_fnum(row["pos_x"]), _fnum(row["pos_y"]), _fnum(row["pos_z"])),
        "rot": (_fnum(row["rot_x"]), _fnum(row["rot_y"]),
                _fnum(row["rot_z"]), _fnum(row["rot_w"], 1.0)),
        "scale": _fnum(row["scale"], 1.0),
        "parent_type": int(row["parent_type"]) if row["parent_type"] else E_NONE,
        "parent_actor": row.get("parent_actor_hash", ""),
        "offset_rot": offset_rot,
        "offset_vec": offset_vec,
    }


def _local_of(t: dict) -> tuple:
    return local_matrix(t["pos"], t["rot"], t["scale"],
                        t["offset_rot"], t["offset_vec"])


def resolve_world(actor: str, transforms: dict, cache: dict, stack: set):
    """Resolve an actor node's WORLD 4x4 (row-major 16-float), following the
    EParentType chain. Returns (world_xf, resolved: bool, reason: str | None).

    * eNone       -> M_init . M_offset                        (resolved)
    * eTransform  -> parentWorld . M_init . M_offset          (resolved iff the
                     whole parent chain resolves and is in-archive; cycle-guarded)
    * eJoint      -> M_init . M_offset, resolved=False        (needs parent skeleton)
    * eAuto       -> M_init . M_offset, resolved=False        (runtime-selected parent)
    * eRefPoint   -> M_init . M_offset, resolved=False        (shapelists.transformlist)

    For every UNRESOLVED case we still emit the object's own local matrix
    (M_init . M_offset) rather than fabricating a full world -- it is at least the
    node's own placement and is clearly flagged `resolved:false` with a reason, so a
    consumer never mistakes it for a resolved world.
    """
    if actor in cache:
        return cache[actor]
    if actor in stack:
        # a parent cycle -- bail without caching (result is stack-context dependent)
        t = transforms.get(actor)
        loc = _local_of(t) if t else _IDENT4
        return loc, False, f"parent cycle detected at actor {actor}"

    t = transforms.get(actor)
    if t is None:
        return _IDENT4, False, f"actor {actor} has no transform row"

    local = _local_of(t)
    pt = t["parent_type"]

    if pt == E_NONE:
        res = (local, True, None)
    elif pt == E_TRANSFORM:
        pa = t["parent_actor"]
        if pa in NULL_HASHES or pa not in transforms:
            res = (local, False,
                   f"eTransform parent {pa or '<null>'} not in archive manifest")
        else:
            stack.add(actor)
            pworld, presolved, preason = resolve_world(pa, transforms, cache, stack)
            stack.discard(actor)
            world = _mat_mul(pworld, local)
            if presolved:
                res = (world, True, None)
            else:
                res = (world, False, f"eTransform parent unresolved: {preason}")
    elif pt == E_JOINT:
        res = (local, False,
               "eJoint rest-pose needs parent skeleton "
               "(jointorrefpointname attaches to a bone on the parent actor's rig)")
    elif pt == E_AUTO:
        res = (local, False, "eAuto = runtime-selected parent, not decoded")
    elif pt == E_REFPOINT:
        res = (local, False, "eRefPoint = shapelists.transformlist, unimplemented")
    else:
        res = (local, False, f"unknown parent_type {pt}")

    cache[actor] = res
    return res


# --- manifest -> scene --------------------------------------------------------

def load_rows(manifest_path: Path) -> list:
    """Read the placed-model manifest TSV (offline, tiny -- single stream)."""
    with open(manifest_path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def load_offsets(transform_manifest_path: Path) -> dict:
    """{(resource_hash, row_index): (offset_rot, offset_vec)} from the transform
    manifest, which carries the `transformoffset` the placed-model TSV omits. Keyed to
    match a placed row's (transform_resource_hash, transform_row_index)."""
    out: dict = {}
    with open(transform_manifest_path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh, delimiter="\t"):
            key = ((r.get("resource_hash") or "").lower(), r.get("row_index"))
            orot = (_fnum(r.get("offset_rot_x")), _fnum(r.get("offset_rot_y")),
                    _fnum(r.get("offset_rot_z")), _fnum(r.get("offset_rot_w"), 1.0))
            ovec = (_fnum(r.get("offset_x")), _fnum(r.get("offset_y")),
                    _fnum(r.get("offset_z")))
            out[key] = (orot, ovec)
    return out


def build_scene(rows: list, archive: str, offsets: dict | None = None) -> dict:
    """Turn manifest rows into a `scene.json` dict: meshlist_hash -> [placements].

    A placement's identity is (actor_node_hash, model_asset_hash) -- the pre-baked
    join cross-products the two CTransformCR / CModelCR containers in this archive
    (`0703fd2acd5803e9` x `59aa439cc732fc1e`), so the raw rows are deduped to distinct
    actor-node placements. One meshlist may be placed by many actor nodes.

    `offsets` (optional, from `load_offsets`) supplies the real transformoffset.
    """
    # Every actor's transform (built from ALL rows, so parents that carry no model
    # are still resolvable as chain links).
    transforms: dict = {}
    for r in rows:
        a = r["actor_node_hash"]
        if a and a not in transforms:
            transforms[a] = transform_from_row(r, offsets)

    cache: dict = {}
    placements: dict = {}
    seen = set()

    stats = {name: 0 for name in PARENT_TYPE_NAME.values()}
    resolved_count = 0
    unresolved_count = 0

    for r in rows:
        model = r["model_asset_hash"]
        actor = r["actor_node_hash"]
        if not model or not actor:
            continue
        key = (actor, model)
        if key in seen:
            continue
        seen.add(key)

        world, resolved, reason = resolve_world(actor, transforms, cache, set())
        pt = transforms[actor]["parent_type"]
        stats[PARENT_TYPE_NAME.get(pt, f"pt{pt}")] = \
            stats.get(PARENT_TYPE_NAME.get(pt, f"pt{pt}"), 0) + 1
        if resolved:
            resolved_count += 1
        else:
            unresolved_count += 1

        entry = {
            "actornodeid": actor,
            "world_xf": [float(v) for v in world],
            "parent_type": pt,
            "parent_type_name": PARENT_TYPE_NAME.get(pt, f"pt{pt}"),
            "scale": transforms[actor]["scale"],
            "start_visible": r.get("start_visible", "") == "1",
            "resolved": resolved,
            "meshlist_present": r.get("meshlist_present", "") == "1",
            "mesh_count": int(r["mesh_count"]) if r.get("mesh_count") else 0,
        }
        if reason:
            entry["reason"] = reason
        placements.setdefault(model, []).append(entry)

    scene = {
        "format": SCENE_FORMAT,
        "version": SCENE_VERSION,
        "archive": archive,
        "coordinate_system": COORDINATE_SYSTEM,
        "stats": {
            "placement_count": resolved_count + unresolved_count,
            "resolved": resolved_count,
            "unresolved": unresolved_count,
            "by_parent_type": stats,
            "meshlist_keys": len(placements),
        },
        "placements": placements,
    }
    return scene


# --- verification helpers -----------------------------------------------------

def raw_parent_type_breakdown(rows: list) -> dict:
    out: dict = {}
    for r in rows:
        pt = int(r["parent_type"]) if r["parent_type"] else E_NONE
        out[pt] = out.get(pt, 0) + 1
    return out


def find_placement(scene: dict, meshlist_hash: str, actornodeid: str = None):
    for p in scene["placements"].get(meshlist_hash, []):
        if actornodeid is None or p["actornodeid"] == actornodeid:
            return p
    return None


# --- CLI ----------------------------------------------------------------------

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default=str(_LE_ROOT / DEFAULT_MANIFEST),
                    help="placed-model manifest TSV (default: reference archive)")
    ap.add_argument("--transform-manifest", default=None,
                    help="transform manifest TSV carrying transformoffset (default: "
                         "transform_manifest_<archive>.tsv beside --manifest)")
    ap.add_argument("--archive", default=DEFAULT_ARCHIVE,
                    help="archive hash label written into scene.json")
    ap.add_argument("--out", default=None,
                    help="scene.json output path (default: print summary only)")
    ap.add_argument("--arm-check", action="store_true",
                    help="assert the jck_arm anchor placement (reference archive)")
    args = ap.parse_args(argv)

    manifest = Path(args.manifest)
    if not manifest.exists():
        print(f"ERROR: manifest not found: {manifest}", file=sys.stderr)
        return 2

    tm = Path(args.transform_manifest) if args.transform_manifest else \
        manifest.parent / f"transform_manifest_{args.archive}.tsv"
    offsets = None
    if tm.exists():
        offsets = load_offsets(tm)
    else:
        print(f"WARNING: transform manifest {tm} not found -- transformoffset assumed "
              f"identity (correct only if this archive uses no offsets)", file=sys.stderr)

    rows = load_rows(manifest)
    scene = build_scene(rows, args.archive, offsets)

    raw = raw_parent_type_breakdown(rows)
    st = scene["stats"]
    print(f"manifest rows: {len(rows)}")
    print(f"raw parent_type breakdown (rows): "
          f"{ {k: raw[k] for k in sorted(raw)} }")
    print(f"distinct placements: {st['placement_count']}  "
          f"(resolved {st['resolved']} / unresolved {st['unresolved']})")
    print(f"placements by parent_type: {st['by_parent_type']}")
    print(f"meshlist keys: {st['meshlist_keys']}")

    # jck_arm anchor: model_asset 892cca9de00b30a6, actornode 040f0c3353d34458
    arm = find_placement(scene, "892cca9de00b30a6", "040f0c3353d34458")
    if arm is not None:
        wx = arm["world_xf"]
        tx, ty, tz = wx[3], wx[7], wx[11]
        print(f"jck_arm anchor: parent_type={arm['parent_type_name']} "
              f"scale={arm['scale']} resolved={arm['resolved']} "
              f"world translation=({tx:.3f}, {ty:.3f}, {tz:.3f})")
        if args.arm_check:
            assert arm["parent_type"] == E_NONE, arm["parent_type"]
            assert abs(arm["scale"] - 1.0) < 1e-6
            assert abs(tx - 0.01) < 1e-3 and abs(ty - 1.378) < 1e-3 \
                and abs(tz + 19.876) < 1e-3, (tx, ty, tz)
            print("  arm-check: PASS")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(scene, indent=1), encoding="utf-8")
        print(f"wrote {out}  ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
