"""Quest movers: `CPlatformCRAndroid` carries the Win10 platform record.

The Quest path must not re-derive the record. It hands the blob to
`evr_movers.platform_movers_from_blob`, so the ordering, the rest -> far-end
orientation and the return-leg de-duplication stay in one place and cannot
drift between platforms.

These build a synthetic component rather than reading the extract, so they
check the DECODE and the instance-keying rules, not the shipped data.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import evr_movers as MV                                      # noqa: E402
from evr_quest import movers as QM                           # noqa: E402


def _platform_blob(records, gap=4096) -> bytes:
    """A `CPlatformCR` blob: a 24-byte index, a gap, then 384-byte payload.

    The payload base is found by the big gap after the index, which is how the
    real decoder locates it -- so the fixture has to reproduce that shape
    rather than just concatenating records.
    """
    out = bytearray(MV.PLATFORM_INDEX_BASE)
    for actor, _a, _b in records:
        out += struct.pack("<QQQ", actor, 0, 0)
    out += b"\x00" * gap
    for actor, point_a, point_b in records:
        rec = bytearray(MV.PLATFORM_STRIDE)
        struct.pack_into("<Q", rec, 0, actor)
        struct.pack_into("<3f", rec, MV.P_POINT_A, *point_a)
        struct.pack_into("<3f", rec, MV.P_POINT_B, *point_b)
        out += rec
    return bytes(out)


class _Instance:
    def __init__(self, entity):
        self.entity = entity


def test_endpoints_are_read_as_world_positions():
    actor = 0x1111222233334444
    rest, far = (1.0, 2.0, 3.0), (1.0, 2.0, 12.297)
    blob = _platform_blob([(actor, rest, far)])
    got = MV.platform_movers_from_blob(
        blob, {actor: rest}, level="lvl", label=QM.LABEL)
    assert list(got) == [str(actor)]
    assert got[str(actor)]["rest"] == [1.0, 2.0, 3.0]
    assert got[str(actor)]["travel"] == [0.0, 0.0, 9.297]
    assert got[str(actor)]["distance"] == 9.297
    assert got[str(actor)]["source"] == QM.LABEL


def test_the_pair_is_oriented_rest_first_whichever_way_it_is_stored():
    """The actor's own transform picks which endpoint is the rest pose."""
    actor = 0x55
    rest, far = (0.0, 0.0, 0.0), (2.0, 0.0, 0.0)
    # stored with the FAR end in slot A -- the record must still come back
    # anchored on the rest pose, with the travel pointing away from it.
    blob = _platform_blob([(actor, far, rest)])
    got = MV.platform_movers_from_blob(blob, {actor: rest}, level="lvl")
    assert got[str(actor)]["rest"] == [0.0, 0.0, 0.0]
    assert got[str(actor)]["travel"] == [2.0, 0.0, 0.0]


def test_the_return_leg_of_a_pair_is_dropped():
    """Each actor appears twice, A and B swapped. That is one motion."""
    actor = 0x77
    rest, far = (0.0, 0.0, 0.0), (0.0, 0.0, 2.2)
    blob = _platform_blob([(actor, rest, far), (actor, far, rest)])
    got = MV.platform_movers_from_blob(blob, {actor: rest}, level="lvl")
    assert len(got) == 1
    assert got[str(actor)]["distance"] == 2.2


def test_a_stationary_record_is_not_a_mover():
    actor = 0x99
    rest = (5.0, 5.0, 5.0)
    blob = _platform_blob([(actor, rest, rest)])
    assert MV.platform_movers_from_blob(blob, {actor: rest}, level="lvl") == {}


def test_rows_are_keyed_by_instance_index_not_by_actor():
    """The add-on resolves objects by instance index, so that is the key."""
    movers = {"4242": {"rest": [0, 0, 0], "travel": [1, 0, 0],
                       "distance": 1.0, "level": "lvl", "source": QM.LABEL}}
    instances = [_Instance(1), _Instance(4242), _Instance(7), _Instance(4242)]
    rows = QM.rows_for_instances(movers, instances)
    # both placements of the actor move -- one actor, two instances
    assert sorted(rows) == ["1", "3"]
    assert rows["1"]["travel"] == [1, 0, 0]


def test_an_actor_with_no_instance_yields_no_row():
    movers = {"1": {"rest": [0, 0, 0], "travel": [1, 0, 0],
                    "distance": 1.0, "level": "lvl", "source": QM.LABEL}}
    assert QM.rows_for_instances(movers, [_Instance(2)]) == {}


def test_the_sidecar_matches_the_format_the_addon_reads():
    doc = QM.document({"0": {"rest": [0, 0, 0], "travel": [0, 0, 1],
                             "distance": 1.0, "level": "lvl",
                             "source": QM.LABEL}})
    assert doc["format"] == "evr_movers"
    assert doc["instances"]["0"]["distance"] == 1.0
    # skeletal movers are NOT implemented on Quest; the key must still exist so
    # the add-on's lookup does not have to special-case the Quest sidecar
    assert doc["skeletal"] == {}
    assert "placeholder" in doc["note"]


def test_no_sidecar_is_written_for_a_level_with_no_movers(tmp_path):
    """An empty movers.json would read as 'decoded, found nothing'."""
    assert QM.write_sidecar(tmp_path, {}) is None
    assert not (tmp_path / QM.SIDECAR_NAME).exists()
