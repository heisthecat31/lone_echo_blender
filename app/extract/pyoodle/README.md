# pyoodle

Decompress **RAD/Oodle `COMPRESS`-framed containers** — the chunked, Oodle-compressed
file format used by the NRadEngine (Lone Echo / Echo VR) asset packages.

pyoodle implements the *open* container framing (the `COMPRESS` magic, the 8-byte
little-endian size fields, and the 256 KiB chunk table) in pure Python, and calls a
copy of RAD Game Tools' Oodle runtime — **which you supply** — to decode the inner
blocks.

## Features

- `decompress(raw)` — decode a whole `COMPRESS` container (non-`COMPRESS` input is
  returned unchanged).
- `decompress_range(raw, lo, hi)` — decode **only** the chunks overlapping the
  uncompressed `[lo, hi)` window. Peak memory is the compressed file plus the touched
  chunks, never the full uncompressed buffer — so you can pull a small subresource out
  of a multi-hundred-megabyte archive without materialising all of it. Byte-identical
  to `decompress(raw)[lo:hi]`.
- `chunk_table(raw)` — walk the chunk records **without decompressing** (pure
  `struct`); learn the uncompressed size and locate chunks first.
- `load_decompressed(path)` — read a file and fully decompress it.

## The Oodle runtime is not included

Oodle is a **proprietary, Windows** compression library by RAD Game Tools (Epic
Games). It is **not bundled** with pyoodle and cannot be redistributed here. To decode
real data you must supply your own legally-obtained copy of the Oodle DLL and point
pyoodle at it — either with the `PYOODLE_DLL` environment variable or `set_dll_path()`:

```python
import pyoodle
pyoodle.set_dll_path(r"C:\path\to\oodle_11_win64.dll")   # or set PYOODLE_DLL
data = pyoodle.load_decompressed("some_archive")
```

Because it loads a Windows DLL via `ctypes.WinDLL`, **decompression runs on Windows
Python only.** The pure framing helpers (`chunk_table`, header parsing) work anywhere.

## Install

```bash
pip install -e .            # from the repo root
```

or just put the `pyoodle/` package on your `PYTHONPATH`.

## Usage

```python
import pyoodle

raw = open("archive_file", "rb").read()

# whole file
full = pyoodle.decompress(raw)

# just a window (OOM-safe on huge archives)
uncomp_total, chunks = pyoodle.chunk_table(raw)      # no Oodle needed
window = pyoodle.decompress_range(raw, 0, 4096)      # first 4 KiB, uncompressed
```

## Tests

```bash
python3 -m pytest            # or: python3 tests/test_chunk_table.py
```

The container-framing tests need no Oodle DLL. Round-trip decode tests require a real
Oodle runtime + input files and are not included.

## License

pyoodle is MIT-licensed (see `LICENSE`). This covers only pyoodle's own code — the
`COMPRESS` container framing and the ctypes bindings. It does **not** grant any rights
to the Oodle runtime (proprietary, RAD Game Tools / Epic) or to any game data you use
it on; supply and use those under their own terms.
