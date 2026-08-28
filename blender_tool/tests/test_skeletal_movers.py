"""The two mover sources `mpl_combat_dyson` needed and did not have.

That level reported ZERO movers while carrying visibly moving geometry, because
it uses neither of the two components the scan knew:

* its straight-line platforms are `CR15PlatformCR`, the R15 rewrite of
  `CPlatformCR` -- same record, one word wider;
* its fire fixtures do not translate at all. They are actors `CAnimationCR`
  marks as animated whose model owns a `CSkeletonResource` and a
  `CAnimSetResource`, so they deform a rig.

Also here: the bind-pose PHASE correction in `evr_apply_skeleton`, which those
fixtures exposed.

These build synthetic resources rather than reading the extract, so they check
the DECODE and the join rules, not the shipped data.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import evr_movers as MV                                      # noqa: E402
import evr_apply_skeleton as AS                              # noqa: E402


# ── CAnimationCR: which actors animate ──────────────────────────────────

def _anim_index(actors, junk=b"") -> bytes:
    """A `CAnimationCR` index: 24-byte rows of class, actor, 0xFFFFFFFF, 0."""
    out = bytearray(junk)
    for actor in actors:
        out += struct.pack("<QQII", MV.ANIMATION_CLASS, actor, 0xFFFFFFFF, 0)
    return bytes(out)


def test_animated_actors_reads_the_index_rows():
    blob = _anim_index([0x1111111111111111, 0x2222222222222222])
    assert MV.animated_actors(blob) == {0x1111111111111111, 0x2222222222222222}


def test_the_index_is_found_at_any_offset():
    """The rows are located by their four-field signature, not by a header."""
    blob = _anim_index([0xABCDEF0123456789], junk=b"\x7f" * 0x2B7)
    assert MV.animated_actors(blob) == {0xABCDEF0123456789}


def test_a_row_whose_tail_fields_disagree_is_not_an_index_row():
    """The class symbol alone is not enough -- it also appears in the payload."""
    blob = struct.pack("<QQII", MV.ANIMATION_CLASS, 0x4444444444444444, 0x20, 1)
    assert MV.animated_actors(blob) == set()


def test_sentinel_actors_are_not_actors():
    blob = _anim_index([0, 0xFFFFFFFFFFFFFFFF, 0x5555555555555555])
    assert MV.animated_actors(blob) == {0x5555555555555555}


# ── CSkeletonResource: the bind-pose PHASE ─────────────────────

def _bind_records(count, scale=1.0) -> bytes:
    """`count` bind-pose records: identity quaternion, a translation, scale."""
    out = bytearray()
    for i in range(count):
        out += struct.pack("<4f", 0.0, 0.0, 0.0, 1.0)
        out += struct.pack("<3f", float(i), 2.0 * i, -float(i))
        out += struct.pack("<f", scale)
    return bytes(out)


def test_a_clean_bind_window_is_left_alone():
    blob = bytes(16) + _bind_records(6)
    assert AS._snap_bind(blob, 16, 6) == 16


def test_a_four_byte_phase_error_is_corrected():
    """The real failure: 20 of the corpus's 109 skeletons land four bytes early.

    The hierarchy reads correctly at either phase -- the record's leading u32 is
    the previous row's trailing column -- so nothing upstream notices, and the
    bind pose silently comes out straddling records.
    """
    blob = bytes(12) + _bind_records(6)
    assert AS._snap_bind(blob, 12 - 4, 6) == 12
    assert AS._snap_bind(blob, 12 + 4, 6) == 12


def test_the_scale_column_is_what_detects_the_phase():
    """A shifted window still holds unit quaternions; only the scale gives it away."""
    blob = bytes(16) + _bind_records(6)
    assert AS._bind_window_is_clean(blob, 16, 6)
    assert not AS._bind_window_is_clean(blob, 12, 6)


def test_an_offset_with_no_clean_phase_is_kept_rather_than_guessed():
    blob = bytes([0x11]) * 512
    assert AS._snap_bind(blob, 64, 6) == 64


# ── CR15PlatformCR: the fourth straight-line source ─────────────────────

ACTOR = 0x7777777777777777
REST = (94.32272, 16.51450, -0.00090)
FAR = (91.44151, 15.61827, -0.01878)


def _r15_platform_blob() -> bytes:
    """Index region, a wide gap, then two payload records -- out leg and return.

    The gap is what `_platform_records` anchors on, and the return leg is the
    duplicate the reader has to drop, so both are here rather than a single
    tidy record.
    """
    blob = bytearray(0x1330 + 2 * MV.R15_PLATFORM_STRIDE)
    struct.pack_into("<Q", blob, 0x128, ACTOR)              # index region A
    struct.pack_into("<Q", blob, 0x140, ACTOR)
    for i, (a, b) in enumerate(((REST, FAR), (FAR, REST))):
        rec = 0x1330 + i * MV.R15_PLATFORM_STRIDE
        struct.pack_into("<Q", blob, rec, ACTOR)
        struct.pack_into("<3f", blob, rec + MV.R15_P_POINT_A, *a)
        struct.pack_into("<3f", blob, rec + MV.R15_P_POINT_B, *b)
    return bytes(blob)


def test_r15_platform_reads_the_shifted_endpoints(tmp_path):
    directory = tmp_path / MV.R15_PLATFORM_CR
    directory.mkdir(parents=True)
    (directory / "lvl").write_bytes(_r15_platform_blob())

    got = MV.r15_platform_movers(tmp_path, ["lvl"], {ACTOR: REST})
    assert list(got) == [str(ACTOR)]
    rec = got[str(ACTOR)]
    assert rec["source"] == "CR15PlatformCR"
    # ordered rest -> far end using the actor's own transform
    assert rec["rest"] == [round(v, 5) for v in REST]
    assert abs(rec["distance"] - 3.017) < 0.01


def test_r15_platform_offsets_are_not_the_older_component_s(tmp_path):
    """Reading a 392-byte record at the 384-byte layout must NOT work."""
    assert MV.R15_PLATFORM_STRIDE != MV.PLATFORM_STRIDE
    directory = tmp_path / MV.PLATFORM_CR
    directory.mkdir(parents=True)
    (directory / "lvl").write_bytes(_r15_platform_blob())
    got = MV.platform_movers(tmp_path, ["lvl"], {ACTOR: REST})
    assert got == {} or got[str(ACTOR)]["rest"] != [round(v, 5) for v in REST]


# ── the sidecar contract ────────────────────────────────────────────────

def test_skeletal_movers_are_a_separate_section_from_travel_movers():
    """A rig-driven mover has no `travel`, so it must not reach that path."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addon"
                          / "lone_echo_import"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_evr_movers_addon",
        Path(__file__).resolve().parents[1] / "addon" / "lone_echo_import"
        / "evr_movers.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("bpy", type(sys)("bpy"))
    spec.loader.exec_module(module)

    doc = {"format": "evr_movers", "instances": {},
           "skeletal": {"7": {"model": "de5882fe4cf82580", "bones": 20,
                              "animations": [{"name": "open", "hash": "x"},
                                             {"name": "", "hash": "y"}],
                              "level": "lvl", "source": "test"}}}
    assert module.summarize(doc)["skeletal"] == 1

    class _Obj(dict):
        pass

    obj = _Obj()
    out = module.apply(doc, {7: [obj]})
    assert out["skeletal_tagged"] == 1
    assert obj["evr_mover_kind"] == "skeletal"
    assert obj["evr_animations"] == ["open", "y"]
    # tagged, NOT keyframed: the pose curves are not decoded
    assert "evr_mover_travel" not in obj
    assert "not decoded" in obj["evr_mover_motion_is_not_decoded"]
