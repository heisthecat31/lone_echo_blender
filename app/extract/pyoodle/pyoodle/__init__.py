"""pyoodle — decompress RAD/Oodle ``COMPRESS``-framed containers.

A small, dependency-free wrapper around RAD Game Tools' Oodle runtime for reading
the ``COMPRESS``-wrapped, chunked Oodle containers used by the NRadEngine (Lone
Echo / Echo VR) asset packages. It exposes:

* :func:`decompress` — decode a whole ``COMPRESS`` container.
* :func:`decompress_range` — decode only the chunks overlapping an uncompressed
  ``[lo, hi)`` window (OOM-safe: peak memory is the compressed file plus the
  touched chunks, never the full uncompressed buffer).
* :func:`chunk_table` — walk a container's chunk records WITHOUT decompressing,
  so a caller can learn the uncompressed size / locate chunks first.
* :func:`load_decompressed` — read a file and fully decompress it.
* :func:`hexdump` — a tiny debug helper.

Oodle itself is a **proprietary, Windows** library that is NOT bundled here. Point
pyoodle at your own copy of the Oodle DLL either via the ``PYOODLE_DLL`` environment
variable or :func:`set_dll_path` before the first decompress call.

    export PYOODLE_DLL='C:\\path\\to\\oodle_11_win64.dll'   # or set_dll_path(...)

The container format (``COMPRESS`` magic, 8-byte little-endian sizes, 256 KiB
chunking) is an on-disk framing, decoded here with plain ``struct``; only the inner
block decode calls into Oodle.
"""

from __future__ import annotations

import ctypes
import os
import struct
from pathlib import Path

__all__ = [
    "CHUNK_SIZE", "OODLE_DLL", "set_dll_path", "init_oodle", "decompress",
    "load_decompressed", "chunk_table", "decompress_range", "hexdump",
    "OodleError",
]

__version__ = "0.1.0"

CHUNK_SIZE = 262144

# Path to the proprietary Oodle DLL. Not bundled — set via PYOODLE_DLL or
# set_dll_path(). Kept as a module global so callers/shims can point at it.
OODLE_DLL: Path | None = Path(os.environ["PYOODLE_DLL"]) if os.environ.get("PYOODLE_DLL") else None

_oodle = None
_fn = None


class OodleError(RuntimeError):
    """Raised when the Oodle DLL is missing/unloadable or a decode fails."""


def set_dll_path(path) -> None:
    """Point pyoodle at the Oodle DLL to load on the next :func:`init_oodle`."""
    global OODLE_DLL, _oodle, _fn
    OODLE_DLL = Path(path)
    _oodle = _fn = None            # force a reload against the new path


def init_oodle() -> None:
    """Load the Oodle DLL and bind ``OodleLZ_Decompress`` (idempotent)."""
    global _oodle, _fn
    if _fn is not None:
        return
    if OODLE_DLL is None:
        raise OodleError(
            "Oodle DLL location is not set. Set the PYOODLE_DLL environment "
            "variable or call pyoodle.set_dll_path(...) with your own copy of "
            "the Oodle runtime (oodle_*_win64.dll).")
    if not Path(OODLE_DLL).is_file():
        raise OodleError(f"Oodle DLL not found: {OODLE_DLL}")
    if not hasattr(ctypes, "WinDLL"):
        raise OodleError("Oodle is a Windows library; run under Windows Python "
                         "(ctypes.WinDLL is unavailable on this platform).")

    _oodle = ctypes.WinDLL(str(OODLE_DLL))

    try:
        get_clib_vtable = _oodle.OodleMalloc_GetVTable_Clib
        get_clib_vtable.restype = ctypes.c_void_p
        _oodle.OodleMalloc_InstallVTable(ctypes.c_void_p(get_clib_vtable()))
    except AttributeError:
        pass

    try:
        _oodle.Oodle_Init_Default.restype = ctypes.c_int
        _oodle.Oodle_Init_Default()
    except AttributeError:
        pass

    _fn = _oodle.OodleLZ_Decompress
    _fn.restype = ctypes.c_longlong
    _fn.argtypes = [
        ctypes.c_void_p, ctypes.c_longlong, ctypes.c_void_p, ctypes.c_longlong,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
        ctypes.c_longlong, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_longlong, ctypes.c_int,
    ]


def _oodle_decompress(payload: bytes, uncomp_size: int) -> bytes | None:
    init_oodle()
    out = ctypes.create_string_buffer(uncomp_size)
    result = _fn(
        ctypes.cast(ctypes.c_char_p(payload), ctypes.c_void_p), len(payload),
        ctypes.cast(out, ctypes.c_void_p), uncomp_size,
        1, 0, 0, ctypes.c_void_p(0), 0, ctypes.c_void_p(0),
        ctypes.c_void_p(0), ctypes.c_void_p(0), 0, 3,
    )
    if result != uncomp_size:
        return None
    return bytes(out[:uncomp_size])


def decompress(raw: bytes) -> bytes | None:
    """Decompress a ``COMPRESS``-wrapped Oodle container.

    A file that does not start with ``COMPRESS`` is returned unchanged (already
    plain). Multi-chunk containers store a destination start offset per block;
    each block emits ``min(256 KiB, total - chunk_start)`` bytes.
    """
    if raw[:8] != b"COMPRESS":
        return raw

    uncomp_total = struct.unpack_from("<Q", raw, 8)[0]
    file_size = struct.unpack_from("<Q", raw, 16)[0]
    reserved = struct.unpack_from("<Q", raw, 24)[0]
    first_comp = struct.unpack_from("<Q", raw, 32)[0]
    if file_size != len(raw) or reserved != 0:
        raise ValueError(
            f"bad COMPRESS header: file_size={file_size} len={len(raw)} "
            f"reserved={reserved}")

    if first_comp == len(raw) - 40:
        return _oodle_decompress(bytes(raw[40:]), uncomp_total)

    result = bytearray(uncomp_total)
    pos = 40
    chunk_comp = first_comp
    chunk_start = 0
    while chunk_start < uncomp_total:
        to_emit = min(CHUNK_SIZE, uncomp_total - chunk_start)
        block = _oodle_decompress(bytes(raw[pos:pos + chunk_comp]), to_emit)
        if block is None:
            return None
        result[chunk_start:chunk_start + to_emit] = block
        pos += chunk_comp
        if chunk_start + CHUNK_SIZE >= uncomp_total:
            break
        if pos + 16 > len(raw):
            break
        chunk_start = struct.unpack_from("<Q", raw, pos)[0]
        chunk_comp = struct.unpack_from("<Q", raw, pos + 8)[0]
        pos += 16
    return bytes(result)


def load_decompressed(path) -> bytes:
    """Read ``path`` and fully decompress it (raises :class:`OodleError` on fail)."""
    raw = Path(path).read_bytes()
    data = decompress(raw)
    if data is None:
        raise OodleError(f"Oodle decompression failed: {path}")
    return data


def chunk_table(raw: bytes) -> tuple[int, list[tuple[int, int, int, int]]]:
    """Walk a ``COMPRESS`` container's chunk records WITHOUT decompressing.

    Returns ``(uncomp_total, chunks)`` where each chunk is
    ``(uncomp_start, comp_off, comp_len, emit_len)``. Pure ``struct`` parsing —
    does not touch Oodle. A non-``COMPRESS`` buffer reports one identity chunk.
    """
    if raw[:8] != b"COMPRESS":
        return len(raw), [(0, 0, len(raw), len(raw))]
    uncomp_total = struct.unpack_from("<Q", raw, 8)[0]
    file_size = struct.unpack_from("<Q", raw, 16)[0]
    first_comp = struct.unpack_from("<Q", raw, 32)[0]
    if file_size != len(raw):
        raise ValueError(f"bad COMPRESS header: file_size={file_size} len={len(raw)}")

    chunks: list[tuple[int, int, int, int]] = []
    if first_comp == len(raw) - 40:
        chunks.append((0, 40, first_comp, uncomp_total))
        return uncomp_total, chunks

    pos = 40
    chunk_comp = first_comp
    chunk_start = 0
    while chunk_start < uncomp_total:
        to_emit = min(CHUNK_SIZE, uncomp_total - chunk_start)
        chunks.append((chunk_start, pos, chunk_comp, to_emit))
        pos += chunk_comp
        if chunk_start + CHUNK_SIZE >= uncomp_total:
            break
        if pos + 16 > len(raw):
            break
        chunk_start = struct.unpack_from("<Q", raw, pos)[0]
        chunk_comp = struct.unpack_from("<Q", raw, pos + 8)[0]
        pos += 16
    return uncomp_total, chunks


def decompress_range(raw: bytes, lo: int, hi: int) -> bytes:
    """Decompress ONLY the chunks overlapping the uncompressed window ``[lo, hi)``.

    OOM-safe way to pull a small subresource out of a multi-hundred-MB container:
    peak resident is the compressed file plus the touched chunks, not the whole
    uncompressed buffer. Byte-identical to ``decompress(raw)[lo:hi]``.
    """
    uncomp_total, chunks = chunk_table(raw)
    lo = max(0, lo)
    hi = min(hi, uncomp_total)
    if hi <= lo:
        return b""
    out = bytearray(hi - lo)
    for cstart, coff, clen, emit in chunks:
        cend = cstart + emit
        if cend <= lo or cstart >= hi:
            continue
        block = _oodle_decompress(bytes(raw[coff:coff + clen]), emit)
        if block is None:
            raise OodleError(f"chunk decompress fail at uncomp {cstart}")
        s = max(lo, cstart)
        e = min(hi, cend)
        out[s - lo:e - lo] = block[s - cstart:e - cstart]
    return bytes(out)


def hexdump(data: bytes, base: int = 0, width: int = 16, rows: int = 8) -> None:
    """Print a small hex/ASCII dump of ``data`` (debug helper)."""
    for i in range(0, min(len(data), rows * width), width):
        row = data[i:i + width]
        hex_text = " ".join(f"{b:02x}" for b in row)
        ascii_text = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
        print(f"  {base + i:08x}  {hex_text:<{width * 3}}  {ascii_text}")
