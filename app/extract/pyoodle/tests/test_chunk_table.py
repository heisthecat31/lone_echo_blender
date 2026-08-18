"""Container-framing tests for pyoodle — no Oodle DLL required.

These exercise the pure `struct`-based half (COMPRESS header parsing, the chunk
table, non-COMPRESS passthrough, and the DLL-missing error path). Round-trip decode
against real Oodle-compressed data needs the proprietary runtime and is out of scope.

Run standalone (`python3 tests/test_chunk_table.py`) or under pytest.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pyoodle  # noqa: E402

CHUNK = pyoodle.CHUNK_SIZE  # 262144


def _single_chunk_container(uncomp_total: int, comp_len: int) -> bytes:
    """A COMPRESS container whose single chunk covers the whole file."""
    body = bytes(comp_len)
    file_size = 40 + comp_len
    hdr = struct.pack("<8sQQQQ", b"COMPRESS", uncomp_total, file_size, 0, comp_len)
    return hdr + body


def _two_chunk_container(comp0: int, comp1: int) -> bytes:
    """A COMPRESS container with two 256 KiB-emit chunks + the 16 B mid record."""
    uncomp_total = CHUNK * 2
    # header (first_comp = comp0) + chunk0 + [start,u64][comp1,u64] + chunk1
    payload = bytes(comp0) + struct.pack("<QQ", CHUNK, comp1) + bytes(comp1)
    file_size = 40 + len(payload)
    hdr = struct.pack("<8sQQQQ", b"COMPRESS", uncomp_total, file_size, 0, comp0)
    return hdr + payload


def test_non_compress_passthrough():
    raw = b"not a compress container, just bytes"
    total, chunks = pyoodle.chunk_table(raw)
    assert total == len(raw)
    assert chunks == [(0, 0, len(raw), len(raw))]
    # decompress() returns a non-COMPRESS buffer unchanged
    assert pyoodle.decompress(raw) == raw


def test_single_chunk_table():
    raw = _single_chunk_container(uncomp_total=1000, comp_len=120)
    total, chunks = pyoodle.chunk_table(raw)
    assert total == 1000
    assert chunks == [(0, 40, 120, 1000)]


def test_two_chunk_table():
    raw = _two_chunk_container(comp0=100, comp1=80)
    total, chunks = pyoodle.chunk_table(raw)
    assert total == CHUNK * 2
    # (uncomp_start, comp_off, comp_len, emit_len)
    assert chunks[0] == (0, 40, 100, CHUNK)
    assert chunks[1] == (CHUNK, 40 + 100 + 16, 80, CHUNK)
    assert len(chunks) == 2


def test_bad_header_len_mismatch():
    raw = _single_chunk_container(1000, 120)[:-1]  # truncate -> file_size != len
    try:
        pyoodle.chunk_table(raw)
    except ValueError:
        return
    raise AssertionError("expected ValueError on file_size/len mismatch")


def test_decompress_range_empty_window():
    raw = _single_chunk_container(1000, 120)
    assert pyoodle.decompress_range(raw, 500, 500) == b""   # hi <= lo, no Oodle touched
    assert pyoodle.decompress_range(raw, 900, 100) == b""   # inverted window


def test_missing_dll_raises_oodle_error():
    saved = pyoodle.OODLE_DLL
    try:
        pyoodle.set_dll_path("this_dll_does_not_exist_12345.dll")
        try:
            pyoodle.init_oodle()
        except pyoodle.OodleError:
            pass
        else:
            raise AssertionError("expected OodleError for a missing DLL")
    finally:
        # restore module state so other tests/callers are unaffected
        pyoodle.OODLE_DLL = saved
        pyoodle._oodle = pyoodle._fn = None


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        fn()
        passed += 1
        print(f"  PASS {fn.__name__}")
    print(f"\n{passed} passed, 0 failed")
