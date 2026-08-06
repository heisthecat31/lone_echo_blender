"""Head/body assembly across the WHOLE character roster, not just Liv.

`le_mesh.attach.component_attach_matrix` was written for, and verified on, the
219-joint humans: Liv's body carries a joint whose `name_hash` equals the
14-joint `liv_head` rig's root (`dc009aa0b878fd03` == symbol64("EXP_C1_Head1")),
and the seat is `M = body.object_bind[j] @ head.inverse_bind[root]`.

★ The 188-joint androids are a DIFFERENT SHAPE and this pins it: the head/helmet
asset `916a82bd119c330f` is not a small component rig at all — it ships the whole
188-joint body rig, name-hash-identical to `64b4b5b2a0153f7e`'s (188 of 188).
So there is nothing to seat: the two assets are already in one object space and
the correct offset is exactly zero.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
if str(BLENDER_TOOL) not in sys.path:
    sys.path.insert(0, str(BLENDER_TOOL))

from le_mesh.attach import IDENTITY4, component_attach_matrix   # noqa: E402
from le_mesh.material_scalars import symbol64                   # noqa: E402

EXPORTS = BLENDER_TOOL / "exports"
JACK_BODY = EXPORTS / "c6bc8607972268c9_64b4b5b2a0153f7e.lemesh" / "skeleton.json"
ANDROID_HEAD = EXPORTS / "chars" / "6113bd53bd411194_916a82bd119c330f.lemesh" / "skeleton.json"
LIV_VARIANT = EXPORTS / "chars" / "2fd6839161785e9c_3a80cdb80b7e60c0.lemesh" / "skeleton.json"

#: the head-rig root every 219-joint body seats `liv_head` on.
EXP_C1_HEAD1 = "%016x" % symbol64("EXP_C1_Head1")


def _skel(p):
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def test_exp_c1_head1_hashes_to_the_shipped_key():
    """The preimage the 219 attach joins on. `symbol64(name) == key`, always."""
    assert EXP_C1_HEAD1 == "dc009aa0b878fd03"


def test_the_shared_joint_namespace_spans_both_rig_families():
    """`EXP_C1_Head1` is present on the 188 rig AND the 219 rig."""
    for p in (JACK_BODY, ANDROID_HEAD, LIV_VARIANT):
        s = _skel(p)
        if s is None:
            return
        hashes = {j["name_hash"] for j in s["joints"]}
        assert EXP_C1_HEAD1 in hashes, p.parent.name


def test_the_188_head_asset_carries_the_whole_body_rig():
    body, head = _skel(JACK_BODY), _skel(ANDROID_HEAD)
    if body is None or head is None:
        return
    got = component_attach_matrix(body, head)
    assert got["same_rig"] is True
    assert got["shared_joints"] == got["part_joints"] == 188
    assert got["confidence"] == "stream-confirmed"
    for a, b in zip(got["matrix"], IDENTITY4):
        assert abs(a - b) < 1e-6, got["matrix"]


def test_the_character_rigs_are_forests_and_the_count_is_reported():
    """⚠ 'the root' is index-order dependent on a multi-root rig — say so."""
    for p in (JACK_BODY, ANDROID_HEAD, LIV_VARIANT):
        s = _skel(p)
        if s is None:
            return
        roots = sum(1 for j in s["joints"] if int(j.get("parent", -1)) < 0)
        assert roots > 1, (p.parent.name, roots)
    got = component_attach_matrix(_skel(JACK_BODY), _skel(ANDROID_HEAD))
    assert got["host_roots"] > 1 and got["part_roots"] > 1


def test_same_rig_is_false_for_a_genuine_component_rig():
    host = {"joints": [{"index": 0, "name_hash": "aaaa", "parent": -1,
                        "object_bind": IDENTITY4, "inverse_bind": IDENTITY4},
                       {"index": 1, "name_hash": "bbbb", "parent": 0,
                        "object_bind": IDENTITY4, "inverse_bind": IDENTITY4}]}
    part = {"joints": [{"index": 0, "name_hash": "bbbb", "parent": -1,
                        "object_bind": IDENTITY4, "inverse_bind": IDENTITY4}]}
    got = component_attach_matrix(host, part)
    assert got["same_rig"] is False
    assert got["shared_joints"] == 1 and got["part_joints"] == 1
