"""Write a package's `skeleton.json` + weight blob, when its model has a rig.

`CSkeletonResourceWin10` is same-named as the model's mesh list, so a model
either has a skeleton at its own hash or has none -- no join table needed.

    python scripts/evr_apply_skeleton.py <package_dir> <model_hash> [--dir root]

The sidecar sits beside `manifest.json`, exactly like `lightmaps.json`, so the
importer finds it without the user selecting anything.

## What is decoded

**Bind pose** -- one 32-byte record per bone: quaternion (16) + translation
(12) + uniform scale (4). Located by anchoring on the hierarchy table
(`locate_tables`), or, on a Lone Echo 2 skeleton, as the first clean window of
the header's DECLARED bone count. The longest-run-of-unit-quaternions scan is
the last resort only -- it is what returned 191 bones for a 189-bone rig and
cost that skeleton its entire hierarchy.

**Two container layouts.** Echo VR skeletons carry `CTable` headers (six of them
on the reference chassis) and `locate_tables` reads the counts and sizes from
those. Lone Echo 2 skeletons carry NONE -- the model hash sits at +0x00 followed
by plain u32 counts, with the bone count at +0x20 and mirrored at +0x3c. See
`declared_bone_count`; getting the count right is what makes `find_hierarchy`
succeed, since it validates a table of exactly `count` records.

**Hierarchy** -- a parallel 24-byte table:

    +0x00  u32  ordering
    +0x04  u64  name hash (CSymbol64, unaligned)
    +0x0c  u32  parent          0xFFFFFFFF = root
    +0x10  u32  first_child
    +0x14  u32  next_sibling

Located by a check that cannot pass by accident: walking `first_child` /
`next_sibling` must reconstruct the `parent` column EXACTLY. An earlier search
missed this table by requiring a single root -- these rigs have FOUR (a 125-bone
character root plus three helper roots), so the predicate rejected the real
answer. Verified across skeletons of 8, 21, 36 and 125 bones at four different
table offsets.

**World bind pose** -- the stored translations are parent-relative, so each
bone's model-space transform is `world[parent] @ local`. Cross-checked against
a completely independent estimate (the weighted centroid of each bone's own
skinned vertices): the two agree to a **median of 2.0 cm** over 76 bones on
`dac6537a23226325`, with mirrored pairs landing symmetric to three decimals
(bone 8 at x -0.114, bone 9 at +0.114, identical y/z). The residual is expected
-- a centroid sits in the middle of the influenced flesh, not on the joint --
so the composed transform is what is emitted.

Bones with no weighted vertices (helpers/IK) are kept now that the hierarchy is
known: dropping them would break the parent chain of the bones below them.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evr_resource_types import SKELETON_RESOURCE, resource_path

BONE_STRIDE = 32

#: The parallel hierarchy table. `parent` is the field that matters; the child
#: and sibling links are what make the table identifiable.
HIER_STRIDE = 24
H_NAME = 0x04
H_PARENT = 0x0c
H_FIRST_CHILD = 0x10
H_NEXT_SIBLING = 0x14
#: `parent`/`first_child`/`next_sibling` sentinel.
NO_BONE = 0xFFFFFFFF

#: CSymbol64 -> authored bone name, recovered in `data/bone_names.json`.
BONE_NAMES_FILE = _ROOT / "data" / "bone_names.json"
_BONE_NAMES = None


def bone_name_table() -> dict:
    """`{hash: name}` for every bone name recovered so far.

    A hash IS `symbol64(name)`, so each entry is a verified preimage. Every
    bone hash on every skeleton extracted so far now resolves, but a caller
    must still handle `None`: a rig this table has never seen may carry
    cosmetic-specific joints that are not in it.
    """
    global _BONE_NAMES
    if _BONE_NAMES is None:
        try:
            raw = json.loads(BONE_NAMES_FILE.read_text(encoding="utf-8"))
            _BONE_NAMES = {int(k, 16): v for k, v in (raw.get("names") or {}).items()}
        except (OSError, ValueError):
            _BONE_NAMES = {}
    return _BONE_NAMES

SIDECAR_NAME = "skeleton.json"
SIDECAR_FORMAT = "evr_skeleton"
WEIGHT_BLOB = "skeleton_weights.bin"


def has_skeleton(root: Path, model_hash: str) -> bool:
    return resource_path(root, SKELETON_RESOURCE, model_hash) is not None


#: A `CTable` header: 32 at +0x20, then mirrored counts, and `size == n*stride`.
TABLE_SCAN_LIMIT = 0x600
TABLE_STRIDES = (4, 8, 12, 16, 24, 32, 48, 64, 96, 120, 128)


def _tables(blob: bytes) -> list:
    """`[(header_off, size, count, stride), ...]` in declaration order."""
    out = []
    for cursor in range(0, TABLE_SCAN_LIMIT, 8):
        if cursor + 0x38 > len(blob):
            break
        size = struct.unpack_from("<Q", blob, cursor + 8)[0]
        mark, total, used = struct.unpack_from("<QQQ", blob, cursor + 0x20)
        if mark != 32 or not used or total != used or used > 100000:
            continue
        if not size or size % used:
            continue
        stride = size // used
        if stride in TABLE_STRIDES:
            out.append((cursor, size, used, stride))
    return out


def _hierarchy_offset(blob: bytes, count: int):
    span = count * HIER_STRIDE
    for base in range(0, len(blob) - span + 1, 4):
        if find_hierarchy(blob[base:base + span], count) is not None:
            return base
    return None


def locate_tables(blob: bytes):
    """`(bone_count, bind_offset, hierarchy_offset)` or None.

    The bind pose is found by ANCHORING on the hierarchy table rather than by
    scanning for quaternions. A skeleton file holds more than one 32-byte pose
    table -- the second is a shared non-bind pose, identically asymmetric in
    every file that has one -- so "the longest run of unit quaternions" picks
    the wrong table whenever the real one is not the longest, and can also
    start mid-table (it put `b6121f7fb73af91f` at 0xc3c instead of 0xb3c).

    The hierarchy table IS uniquely identifiable, so its offset plus the
    declared table sizes give the bind pose exactly.
    """
    tables = _tables(blob)
    bind = next(((i, t) for i, t in enumerate(tables) if t[3] == BONE_STRIDE), None)
    if bind is None:
        return None
    count = bind[1][2]
    hier = next(((i, t) for i, t in enumerate(tables)
                 if t[3] == HIER_STRIDE and t[2] == count), None)
    if hier is None:
        return None
    hier_off = _hierarchy_offset(blob, count)
    if hier_off is None:
        return None
    bind_off = hier_off - sum(tables[k][1] for k in range(bind[0], hier[0]))
    if bind_off < 0 or bind_off + count * BONE_STRIDE > len(blob):
        return None
    return count, _snap_bind(blob, bind_off, count), hier_off


#: How far either side of the derived offset a phase correction may look.
BIND_SNAP = (0, 4, -4, 8, -8)


def _bind_window_is_clean(blob: bytes, off: int, count: int) -> bool:
    """Does every record here hold a unit quaternion and a scale of exactly 1?

    Both hold for a real bind pose -- the scale column reads `0x3F800000` on
    every bone of every skeleton in the corpus -- and a window read at the wrong
    PHASE fails the scale test immediately, which is what makes this usable as a
    phase check rather than just a sanity check.
    """
    for i in range(count):
        base = off + i * BONE_STRIDE
        if base < 0 or base + BONE_STRIDE > len(blob):
            return False
        quat = struct.unpack_from("<4f", blob, base)
        if not all(math.isfinite(c) for c in quat):
            return False
        if abs(math.sqrt(sum(c * c for c in quat)) - 1.0) > 1e-3:
            return False
        if abs(struct.unpack_from("<f", blob, base + 28)[0] - 1.0) > 1e-3:
            return False
    return True


def _snap_bind(blob: bytes, bind_off: int, count: int) -> int:
    """Correct a 4-byte PHASE error in the derived bind-pose offset.

    ⛔ `bind_off` is derived by subtracting declared table sizes from the
    hierarchy offset, and `_hierarchy_offset` returns the first base at which
    the tree reads consistently. Both the true table start and the one four
    bytes ahead of it satisfy that, because the record's leading u32 is the
    PREVIOUS row's trailing column -- so the hierarchy is read correctly either
    way, and the error is invisible there.

    It is not invisible in the bind pose, which inherits the same phase: the
    window then straddles records and the scale column stops reading 1.0.
    Measured over the corpus, 88 files land clean and **20 need +4**; on
    `de5882fe4cf82580` the unsnapped window gives scale 0.0 and translation
    (1, 0, 0) for a bone whose real rest is the origin, and on
    `58173136d23722e4` -- the 66-bone rig `docs/EVR_SKELETONS.md` was written
    against -- it is wrong too.

    ⚠ Five files satisfy no clean window at any of these phases; four of them
    have a single bone. Those keep the derived offset rather than a guess.
    """
    for delta in BIND_SNAP:
        if _bind_window_is_clean(blob, bind_off + delta, count):
            return bind_off + delta
    return bind_off


#: Where a Lone Echo 2 skeleton declares its bone count -- twice, which is what
#: makes it safe to trust: a stray integer is not mirrored 0x1c bytes later.
LE2_COUNT_OFFSETS = (0x20, 0x3C)


def declared_bone_count(blob: bytes):
    """The bone count a Lone Echo 2 skeleton states in its header, or None.

    That layout has no `CTable` headers at all -- `_tables` finds SIX in an Echo
    VR skeleton and ZERO here -- so `locate_tables` returns None and everything
    downstream falls back to guessing. The count is stated plainly instead: the
    model hash at +0x00, then plain u32 counts, with the bone count at +0x20 and
    mirrored at +0x3c.

    Getting it from a scan instead is what broke the Delta unit: the
    longest-run-of-unit-quaternions fallback returned 191 where the header says
    189, and `find_hierarchy` -- which validates a table of exactly `count`
    records -- then rejected the real hierarchy. Every bone became a root, world
    collapsed to local, and 98 of the bones landed on the origin.
    """
    if len(blob) < max(LE2_COUNT_OFFSETS) + 4:
        return None
    values = {struct.unpack_from("<I", blob, off)[0] for off in LE2_COUNT_OFFSETS}
    if len(values) != 1:
        return None
    count = values.pop()
    if not 1 <= count <= 100000:
        return None
    return count


def first_clean_bind(blob: bytes, count: int):
    """The EARLIEST offset holding a clean `count`-record bind pose, or None.

    Clean windows come in runs 32 apart -- a real table is still "clean" when
    read starting one bone late -- so the earliest of a run is the table's true
    start. A file also holds a SECOND 32-byte pose table (a shared non-bind
    pose); the bind pose is the first, which is the same ordering
    `locate_tables` documents.

    On the Delta unit the candidates score 0.027, 0.616, 1.225 and 5.074 as the
    median distance from each bone to its own skinned-vertex centroid, so the
    earliest is right by a wide margin and the rule is checkable, not a guess.
    """
    limit = len(blob) - count * BONE_STRIDE
    for off in range(0, limit + 1, 4):
        if _bind_window_is_clean(blob, off, count):
            return off
    return None


def read_bind_pose(root: Path, model_hash: str) -> list:
    """`[(quat xyzw, translation, scale), ...]`, or `[]`."""
    path = resource_path(root, SKELETON_RESOURCE, model_hash)
    if path is None:
        return []
    blob = path.read_bytes()

    located = locate_tables(blob)
    declared = declared_bone_count(blob)
    declared_off = first_clean_bind(blob, declared) if declared else None
    if located is not None:
        count, best_off, _hier = located
        best_n = count
    elif declared is not None and declared_off is not None:
        # Lone Echo 2: no CTable headers, but the count is stated in the header
        # and the bind pose is the first clean window of that many records.
        # Believing the scan instead cost this rig its whole hierarchy -- see
        # `declared_bone_count`.
        best_n, best_off = declared, declared_off
    else:
        # Fallback for a file whose header block does not parse: the longest
        # run of unit quaternions. Less reliable -- see `locate_tables`.
        def unit(off):
            if off + 16 > len(blob):
                return False
            q = struct.unpack_from("<4f", blob, off)
            return (all(math.isfinite(c) for c in q)
                    and abs(math.sqrt(sum(c * c for c in q)) - 1.0) < 0.01)

        best_n = best_off = 0
        off = 0
        while off + BONE_STRIDE * 8 <= len(blob):
            n = 0
            while unit(off + n * BONE_STRIDE):
                n += 1
            if n > best_n:
                best_n, best_off = n, off
            off += max(1, n) * BONE_STRIDE if n else 4

    out = []
    for i in range(best_n):
        base = best_off + i * BONE_STRIDE
        out.append((list(struct.unpack_from("<4f", blob, base)),
                    list(struct.unpack_from("<3f", blob, base + 16)),
                    struct.unpack_from("<f", blob, base + 28)[0]))
    return out


def find_hierarchy(blob: bytes, count: int):
    """`(parent, first_child, next_sibling)` lists, or None.

    The table is found by self-consistency rather than by a fixed offset:
    walking `first_child` then `next_sibling` from every bone must rebuild the
    `parent` column exactly. A coincidental run of in-range integers does not
    survive that.
    """
    if count <= 0:
        return None
    span = count * HIER_STRIDE
    for base in range(0, len(blob) - span + 1, 4):
        parent, child, sibling = [], [], []
        ok = True
        for i in range(count):
            off = base + i * HIER_STRIDE
            p = struct.unpack_from("<I", blob, off + H_PARENT)[0]
            c = struct.unpack_from("<I", blob, off + H_FIRST_CHILD)[0]
            n = struct.unpack_from("<I", blob, off + H_NEXT_SIBLING)[0]
            if not all(v < count or v == NO_BONE for v in (p, c, n)):
                ok = False
                break
            parent.append(p)
            child.append(c)
            sibling.append(n)
        if not ok or not any(p == NO_BONE for p in parent):
            continue
        derived = [NO_BONE] * count
        bad = False
        for i in range(count):
            cursor, guard = child[i], 0
            while cursor != NO_BONE:
                if derived[cursor] != NO_BONE or guard > count:
                    bad = True
                    break
                derived[cursor] = i
                cursor = sibling[cursor]
                guard += 1
            if bad:
                break
        if bad or derived != parent:
            continue
        return parent, child, sibling
    return None


# ⛔ Two things in the recovered hierarchy LOOK like bugs and are not. Now that
# every bone hash resolves (data/bone_names.json), the parent column can be
# audited against the NAMES, and 288 name-implied links check out. The
# exceptions are conventions, not damage:
#
#   * A piston's two ends are SIBLINGS, not a chain. EXP_?1_ThumbPiston2 hangs
#     off Hand1 alongside ThumbPiston1, because both ends of a piston anchor to
#     the same parent -- that is what lets them slide against each other. So the
#     usual "Foo2 is a child of Foo1" rule (correct for Index2, Arm2, Spine2)
#     does NOT hold for Piston pairs. It reads identically on all 11 skeletons.
#   * The rig is not symmetric. The Lone Echo 2 body has EXP_L1_Arm3 and no
#     EXP_R1_Arm3, sitting at exactly the left shoulder with Clav1 as parent --
#     an unused socket, like EXP_C1_SynchHelmet1 coinciding with the head joint.
#
# Do not "repair" either one. The numeric test still governs: composed bone
# midpoints sit a median 0.025 m from their own skin centroids (0.035 m on the
# head package), against 1.15 m for no hierarchy at all.
#
# ⛔ A parent-column-only fallback was tried here and REMOVED. On a skeleton
# whose sibling column does not validate it is tempting to accept the parent
# column alone, since that is all `world_transforms` needs -- but on the Lone
# Echo 2 Delta unit (`25bcc4282bf0807f`, 191 bones) no column in the blob
# composes correctly, and the best one scores a median 0.90 against the bones'
# own SKIN CENTROIDS where having no hierarchy at all scores 1.15. It would have
# shipped a wrong rig that merely looks plausible.
#
# The real gap is upstream: `locate_tables` returns None for these skeletons, so
# `read_bind_pose` falls back to the longest-run-of-unit-quaternions scan and the
# BIND POSE itself is not trustworthy either. Fix the table location for this
# layout before revisiting the hierarchy.
#
# The skin centroid is the test to use: `build` already computes a weighted
# centroid per bone, and a correct rig must compose to within a few centimetres
# of it.


#: A piston is TWO bones parented to DIFFERENT segments, each aiming at the
#: other, plus a marker bone ~1 cm away sharing its end's parent. The marker is
#: named `<end>Up` on the leg/spine/forearm pistons and `<end>Aim` on the bicep
#: pistons, but it is the same thing either way: on this rig every marker offset
#: is PERPENDICULAR to the piston axis (measured, dot product < 1e-3), so it is
#: an up-vector, not a target.
#:
#: None of this is guessable from the geometry alone -- the ends are unweighted
#: leaves, so nothing in the mesh says which two belong together. It only became
#: readable once the CSymbol64 names were cracked.
PISTON_MARKERS = ("Up", "Aim")


def _piston_key(name: str):
    """The key two ends of the SAME piston share.

    Upper/Lower pairs key on the shared stem. The rest pair across a trailing
    index (ForearmPiston1/2, ThumbPiston1/2) or across the limb segment they
    hang off (Arm1BicepPiston1 with Arm2BicepPiston1), so those get the trailing
    digits stripped and the segment index normalised.
    """
    for half in ("Upper1", "Lower1"):
        if name.endswith(half):
            return name[: -len(half)]
    key = re.sub(r"\d+$", "", name)
    return re.sub(r"(EXP_[CLR]\d_)(Arm|Leg)\d", "\\g<1>\\g<2>#", key)


def piston_pairs(names) -> list:
    """`[{a, b, a_up, b_up}]` of bone INDICES, from the authored names.

    A pair is only emitted when exactly two ends share a key -- a group of one
    or of three is reported by the caller rather than guessed at.
    """
    ends, marker = {}, {}
    for i, n in enumerate(names):
        if not n or "Piston" not in n:
            continue
        for suffix in PISTON_MARKERS:
            if n.endswith(suffix) and n[: -len(suffix)] in names:
                marker[n[: -len(suffix)]] = i
                break
        else:
            ends.setdefault(_piston_key(n), []).append((n, i))
    pairs, odd = [], []
    for key, members in sorted(ends.items()):
        if len(members) != 2:
            odd.append((key, [m[0] for m in members]))
            continue
        (na, ia), (nb, ib) = sorted(members)
        pairs.append({"a": ia, "b": ib,
                      "a_up": marker.get(na), "b_up": marker.get(nb)})
    return pairs, odd


def bone_names(blob: bytes, count: int, base_hint=None) -> list:
    """The per-bone CSymbol64 name hashes, in bone order."""
    base = base_hint if base_hint is not None else _hierarchy_offset(blob, count)
    if base is None:
        return [0] * count
    return [struct.unpack_from("<Q", blob, base + i * HIER_STRIDE + H_NAME)[0]
            for i in range(count)]


def _compose(quat, translation, scale):
    """Local TRS -> a 4x4 as flat row-major lists (no numpy dependency)."""
    x, y, z, w = quat
    rot = ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
           (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
           (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))
    return [[rot[r][c] * scale for c in range(3)] + [translation[r]]
            for r in range(3)] + [[0.0, 0.0, 0.0, 1.0]]


def _matmul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)]
            for r in range(4)]


def world_transforms(bones, parent) -> list:
    """Model-space 4x4 per bone: `world[parent] @ local`."""
    world = [None] * len(bones)

    def solve(i, guard=0):
        if world[i] is not None:
            return world[i]
        if guard > len(bones):
            world[i] = _compose(*bones[i])
            return world[i]
        local = _compose(*bones[i])
        p = parent[i]
        world[i] = local if p == NO_BONE else _matmul(solve(p, guard + 1), local)
        return world[i]

    for i in range(len(bones)):
        solve(i)
    return world


def build(package: Path, root: Path, model_hash: str) -> dict | None:
    """Write the sidecar for one package. Returns a summary, or None."""
    import evr_scene_extract as extractor

    bones = read_bind_pose(root, model_hash)
    if not bones:
        return None

    results, _label = extractor._decode_model_cached(root, model_hash)
    if not results:
        return None
    lod = extractor._group_submeshes_by_lod(results)
    keep = [i for i, (_g, level, _n) in enumerate(lod) if level == 0]

    # Per-bone weighted centroid, and the per-vertex weights themselves.
    acc = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    blob = bytearray()
    meshes = []
    for out_index, source in enumerate(keep):
        result = results[source]
        verts = result[0]
        skin = result[3] if len(result) > 3 else None
        entry = {"mesh": out_index, "nverts": len(verts), "offset": len(blob)}
        if skin:
            for vi, item in enumerate(skin):
                try:
                    idxs, wts = item
                except (TypeError, ValueError):
                    idxs, wts = (0, 0, 0, 0), (0, 0, 0, 0)
                # The record holds FOUR influences. Every eSkinWeights vertex
                # format seen so far is 4-wide and already sums to 255, so this
                # is lossless -- but a wider source must not be truncated in
                # silence: dropping an influence without renormalising leaves
                # the weights summing under 255, and Blender reads the shortfall
                # as partial weighting and drags the vertex toward the origin.
                if len(wts) > 4 and any(wts[4:]):
                    raise ValueError(
                        "vertex %d of submesh %d carries %d skin influences; "
                        "the sidecar record holds 4. Widen the record (and "
                        "renormalise) rather than dropping the extras."
                        % (vi, source, sum(1 for w in wts if w)))
                blob += struct.pack("<4H4B", *(list(idxs)[:4] + [0] * 4)[:4],
                                    *(list(wts)[:4] + [0] * 4)[:4])
                v = verts[vi]
                for bi, w in zip(idxs, wts):
                    if not w:
                        continue
                    a = acc[bi]
                    a[0] += v[0] * w
                    a[1] += v[1] * w
                    a[2] += v[2] * w
                    a[3] += w
        entry["weighted"] = bool(skin)
        meshes.append(entry)

    centroid = {b: (a[0] / a[3], a[1] / a[3], a[2] / a[3])
                for b, a in acc.items() if a[3]}

    blob_bytes = resource_path(root, SKELETON_RESOURCE, model_hash).read_bytes()
    hierarchy = find_hierarchy(blob_bytes, len(bones))
    if hierarchy is None:
        parent = [NO_BONE] * len(bones)
        names = [0] * len(bones)
        world = [_compose(*b) for b in bones]
    else:
        parent, _child, _sibling = hierarchy
        names = bone_names(blob_bytes, len(bones))
        world = world_transforms(bones, parent)

    table = bone_name_table()
    records = []
    for i, (quat, translation, scale) in enumerate(bones):
        matrix = world[i]
        records.append({
            "index": i,
            "name_hash": "%016x" % names[i],
            # None when the preimage is not recovered -- the consumer falls
            # back to the index rather than inventing a name.
            "name": table.get(names[i]),
            "parent": None if parent[i] == NO_BONE else int(parent[i]),
            # Model space, composed down the parent chain -- NOT the stored
            # translation, which is parent-relative.
            "position": [round(matrix[r][3], 6) for r in range(3)],
            "matrix": [[round(matrix[r][c], 6) for c in range(4)] for r in range(3)],
            "rotation": [round(v, 6) for v in quat],
            "scale": round(scale, 6),
            "local_translation": [round(v, 6) for v in translation],
            "weighted": i in centroid,
        })

    (package / WEIGHT_BLOB).write_bytes(bytes(blob))
    # Piston pairs, read off the authored names. Their ends are unweighted
    # leaves, so nothing in the mesh says which two belong together -- without
    # the names this block could not be built at all.
    pairs, unpaired = piston_pairs([r["name"] for r in records])
    piston_records = [
        {"a": records[p["a"]]["name"], "b": records[p["b"]]["name"],
         "a_up": records[p["a_up"]]["name"] if p["a_up"] is not None else None,
         "b_up": records[p["b_up"]]["name"] if p["b_up"] is not None else None}
        for p in pairs]
    doc = {
        "format": SIDECAR_FORMAT,
        "model": model_hash,
        "bone_count": len(bones),
        "hierarchy": hierarchy is not None,
        "roots": [i for i, x in enumerate(parent) if x == NO_BONE],
        "bones": records,
        "weights_blob": WEIGHT_BLOB,
        "weight_record": "4x u16 bone index + 4x u8 weight (0-255), per vertex",
        "meshes": meshes,
        "pistons": piston_records,
        "_note": ("`position` is MODEL space, composed as world[parent] @ local "
                  "-- the stored translation is parent-relative and is kept as "
                  "`local_translation`. `parent` is null for a root; these rigs "
                  "have several. Bone names are CSymbol64 hashes whose "
                  "preimages are recovered for most anatomical joints (see "
                  "data/bone_names.json); `name` is null where it is not. "
                  "`pistons` pairs the two ends of each piston with their "
                  "up-vector markers, so an importer can aim them at each "
                  "other -- the ends carry no skin weights, so the pairing is "
                  "readable only from the names."),
    }
    (package / SIDECAR_NAME).write_text(json.dumps(doc, indent=1),
                                        encoding="utf-8")
    return {"bones": len(records), "of": len(bones), "meshes": len(meshes),
            "weight_bytes": len(blob), "hierarchy": hierarchy is not None,
            "roots": len(doc["roots"]),
            "named": sum(1 for r in records if r["name"]),
            "pistons": len(piston_records),
            "pistons_unpaired": len(unpaired)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("package")
    ap.add_argument("model_hash")
    ap.add_argument("--dir", default=None)
    args = ap.parse_args(argv)
    import evr_paths
    args.dir = evr_paths.require_extract(args.dir)

    package = Path(args.package)
    summary = build(package, Path(args.dir), args.model_hash)
    if summary is None:
        print(f"{args.model_hash}: no skeleton resource -- nothing written")
        return 0
    print(f"{args.model_hash}: {summary['bones']}/{summary['of']} bones, "
          f"hierarchy {'YES' if summary['hierarchy'] else 'no'} "
          f"({summary['roots']} root(s)), {summary['named']} named, "
          f"{summary['meshes']} mesh(es), "
          f"{summary['weight_bytes']:,} B of weights -> {package / SIDECAR_NAME}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
