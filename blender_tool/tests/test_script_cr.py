"""`CScriptCR` decode: actor -> script, anchored on real nodeids.

The component's record block is NOT aligned to `len(file) - size` -- measured
across four levels the actor column lands at `+0x2b8`, `+0x0c0`, `+0x148` and
`+0x1e0` inside that block. These build synthetic files at deliberately awkward
alignments and check the decoder still finds the records, and refuses when it
cannot.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from evr_script import (  # noqa: E402
    RECORD_STRIDE, R_ACTOR, R_SCRIPT, SCRIPT_CR, read_scripts, scripts_for,
    script_names,
)

ACTORS = [0x1111111111111111, 0x2222222222222222, 0x3333333333333333]
SCRIPTS = [0xAAAAAAAAAAAAAAAA, 0xBBBBBBBBBBBBBBBB, 0xAAAAAAAAAAAAAAAA]


def _build(tmp_path, level, actor_offset, count=3):
    """A CScriptCR file with records on a lattice at phase `actor_offset`.

    Mirrors the real files: the header declares `count * 720` bytes, but the
    records are laid on a lattice whose phase the header never states, so the
    file is longer than the declared block and the first record does not begin
    where `len - size` points.
    """
    blob = bytearray(actor_offset + RECORD_STRIDE * count + 64)
    struct.pack_into("<Q", blob, 0x08, RECORD_STRIDE * count)
    struct.pack_into("<Q", blob, 0x28, count)
    struct.pack_into("<Q", blob, 0x30, count)
    for i in range(count):
        record = actor_offset + i * RECORD_STRIDE
        struct.pack_into("<Q", blob, record + R_ACTOR, ACTORS[i])
        struct.pack_into("<Q", blob, record + R_SCRIPT, SCRIPTS[i])
    directory = tmp_path / SCRIPT_CR
    directory.mkdir(parents=True, exist_ok=True)
    (directory / level).write_bytes(bytes(blob))
    return tmp_path


def test_reads_actor_and_script_at_an_awkward_alignment(tmp_path):
    """`+0x2b8` is a real measured offset (mpl_lobby_b_combat)."""
    root = _build(tmp_path, "lvl", actor_offset=0x2B8)
    rows = read_scripts(root, ["lvl"], set(ACTORS))
    assert len(rows) == 3
    assert [r["actor"] for r in rows] == ["%016x" % a for a in ACTORS]
    assert [r["script"] for r in rows] == ["%016x" % s for s in SCRIPTS]


def test_alignment_is_discovered_not_assumed(tmp_path):
    """The same records must decode from a different block alignment."""
    for offset in (0x000, 0x0C0, 0x148, 0x1E0, 0x2B8):
        root = _build(tmp_path / f"a{offset:03x}", "lvl", actor_offset=offset)
        rows = read_scripts(root, ["lvl"], set(ACTORS))
        assert [r["actor"] for r in rows] == ["%016x" % a for a in ACTORS], offset


def test_rejects_when_no_column_looks_like_actors(tmp_path):
    """Wrong actor table => no anchor => nothing returned, not garbage."""
    root = _build(tmp_path, "lvl", actor_offset=0x2B8)
    assert read_scripts(root, ["lvl"], {0xDEADBEEF}) == []


def test_rejects_when_size_is_not_count_times_stride(tmp_path):
    root = _build(tmp_path, "lvl", actor_offset=0x2B8)
    path = root / SCRIPT_CR / "lvl"
    blob = bytearray(path.read_bytes())
    struct.pack_into("<Q", blob, 0x08, RECORD_STRIDE * 3 - 8)   # not a multiple
    path.write_bytes(bytes(blob))
    assert read_scripts(root, ["lvl"], set(ACTORS)) == []


def test_scripts_for_groups_by_actor_without_duplicates(tmp_path):
    root = _build(tmp_path, "lvl", actor_offset=0x2B8)
    mapping = scripts_for(root, ["lvl"], set(ACTORS))
    assert mapping["%016x" % ACTORS[0]] == ["%016x" % SCRIPTS[0]]
    # two actors share one script; each still lists it once
    assert mapping["%016x" % ACTORS[2]] == ["%016x" % SCRIPTS[2]]


def test_zero_and_sentinel_scripts_are_skipped(tmp_path):
    root = _build(tmp_path, "lvl", actor_offset=0x2B8)
    path = root / SCRIPT_CR / "lvl"
    blob = bytearray(path.read_bytes())
    struct.pack_into("<Q", blob, 0x2B8 + R_SCRIPT, 0)
    struct.pack_into("<Q", blob, 0x2B8 + RECORD_STRIDE + R_SCRIPT,
                     0xFFFFFFFFFFFFFFFF)
    path.write_bytes(bytes(blob))
    rows = read_scripts(root, ["lvl"], set(ACTORS))
    assert len(rows) == 1
    assert rows[0]["actor"] == "%016x" % ACTORS[2]


def test_known_script_names_are_loadable_and_plausible():
    """The cracked preimages must hash back onto their own keys."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "blender_tool"))
    from le_mesh.material_scalars import symbol64

    names = script_names()
    assert names, "data/script_names.json is empty"
    for hash_hex, name in names.items():
        assert "%016x" % symbol64(name) == hash_hex, (hash_hex, name)
    # the eight that came from the independent dictionary, not from generation
    assert names.get("a0815e6bca5b8bfc") == "tut_movement"
    assert names.get("c516aee653563ee0") == "tut_boost"


def test_binding_names_filters_boilerplate(tmp_path):
    """`setup_bindings` literals identify a script; shared noise is dropped."""
    from evr_script import binding_names, SCRIPT_DLL_SUBPATH

    directory = tmp_path / SCRIPT_DLL_SUBPATH
    directory.mkdir(parents=True)
    body = b"\x00".join([
        b"setup_bindings", b"varname", b"gamespace",        # boilerplate
        b"evt_catapult_launched", b"launcher_at_base",      # real bindings
        b"ring_bright_mul", b"MixedCase", b"d:/projects2/rad/dev",
    ])
    (directory / "abc123.dll").write_bytes(body)
    names = binding_names(tmp_path, "ABC123")               # case-insensitive
    assert names == ["evt_catapult_launched", "launcher_at_base",
                     "ring_bright_mul"]


def test_binding_names_missing_dll_is_empty(tmp_path):
    from evr_script import binding_names

    assert binding_names(tmp_path, "deadbeefdeadbeef") == []
