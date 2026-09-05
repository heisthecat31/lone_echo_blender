"""`CTTFontResourceWin10` -> a glyph atlas Blender can actually draw text with.

    python scripts/evr_apply_fonts.py <package> [--dir <extract>]

Four fonts ship in the whole corpus and nothing read them, so every UI export so
far is textured quads with no text.

THE FORMAT (measured, not guessed)
----------------------------------
A 64-byte header, a variable gap, then a flat glyph table of 32-byte records.

    +0x00  u32   atlas width    512 on all four
    +0x04  u32   atlas height   512 on all four
    ...
    gap: carries the ATLAS TEXTURE hashes as CSymbol64 -- 9f118d37013acde8 is
         512x512 B8G8R8A8_UNORM, i.e. exactly the header's dimensions and an
         uncompressed alpha format, which is what a glyph sheet wants.

    glyph record, stride 32:
    +0x00  u32   codepoint
    +0x04  u16   atlas x        0..488, inside the 512 sheet
    +0x06  u16   atlas y        0..504
    +0x08  u16   width
    +0x0A  u16   height
    +0x0C  u16   width  again   (equals +0x08 on every glyph measured)
    +0x0E  u16   height again   (equals +0x0A)
    +0x10  u16   bearing x      0..4
    +0x12  u16   bearing y      0..14
    +0x14  u16   line height    10 on every glyph of every font
    +0x16  u16   0

The table is FOUND, not assumed: its start is the longest run of consecutive
codepoints at stride 32, which lands on printable ASCII 32..126 -- a 95-glyph
run -- on all four fonts. Reading on from there yields 303/303, 505/505,
303/303 and 303/303 codepoints in range, up to U+2014, so the run is the start
of a complete table rather than a lucky window.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

_S = Path(__file__).resolve().parent
for _p in (str(_S), str(_S.parent), str(_S.parent / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evr_texture_resource as evr_tex          # noqa: E402

FONT_TYPE = "CTTFontResourceWin10"
GLYPH_STRIDE = 32
OFF_ATLAS_W, OFF_ATLAS_H = 0x00, 0x04
G_CP, G_X, G_Y, G_W, G_H = 0x00, 0x04, 0x06, 0x08, 0x0A
G_BEARX, G_BEARY, G_LINE = 0x10, 0x12, 0x14
MAX_CODEPOINT = 0x11000


def _backend():
    sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor")
    from echo_editor.assemble.authoring import AuthoringBackend
    b = AuthoringBackend()
    b.activate()
    return b


def find_table(b: bytes) -> int:
    """Start of the glyph table: the longest run of consecutive codepoints."""
    best, at = 0, None
    for o in range(0, min(len(b), 4096), 4):
        run, prev, k = 0, None, o
        while k + 4 <= len(b):
            v = struct.unpack_from("<I", b, k)[0]
            if prev is None and 32 <= v <= 126:
                run = 1
            elif prev is not None and v == prev + 1 and 32 <= v <= 126:
                run += 1
            else:
                break
            prev, k = v, k + GLYPH_STRIDE
        if run > best:
            best, at = run, o
    return at if best >= 32 else None


def read_font(blob: bytes, known_textures: set) -> dict | None:
    if len(blob) < 128:
        return None
    start = find_table(blob)
    if start is None:
        return None
    aw = struct.unpack_from("<I", blob, OFF_ATLAS_W)[0]
    ah = struct.unpack_from("<I", blob, OFF_ATLAS_H)[0]
    atlases = []
    for o in range(0, start - 7):
        h = "%016x" % struct.unpack_from("<Q", blob, o)[0]
        if h in known_textures and h not in atlases:
            atlases.append(h)
    glyphs = []
    n = (len(blob) - start) // GLYPH_STRIDE
    for i in range(n):
        o = start + i * GLYPH_STRIDE
        cp = struct.unpack_from("<I", blob, o + G_CP)[0]
        if not 0 < cp < MAX_CODEPOINT:
            continue
        x, y, w, h_ = struct.unpack_from("<4H", blob, o + G_X)
        bx, by = struct.unpack_from("<2H", blob, o + G_BEARX)
        line = struct.unpack_from("<H", blob, o + G_LINE)[0]
        glyphs.append({"cp": cp, "char": chr(cp) if 32 <= cp < 127 else "",
                       "x": x, "y": y, "w": w, "h": h_,
                       "bearing_x": bx, "bearing_y": by, "line_height": line,
                       # uv for a shader, computed from the header's own atlas size
                       "uv": [x / aw, y / ah, (x + w) / aw, (y + h_) / ah]})
    return {"atlas_width": aw, "atlas_height": ah, "atlases": atlases,
            "table_offset": start, "glyph_count": len(glyphs),
            "capacity": n, "glyphs": glyphs}


def apply(pkg: Path, root: Path) -> dict:
    backend = _backend()
    H = lambda s: "%016x" % (backend.rad_hash(s) & 0xFFFFFFFFFFFFFFFF)
    fdir = root / H(FONT_TYPE)
    if not fdir.is_dir():
        raise SystemExit("no %s in the extract" % FONT_TYPE)
    tex_dir = root / H("cgtextureresourceWin10")
    known = {p.name for p in tex_dir.iterdir()} if tex_dir.is_dir() else set()

    out = {"format": "evr_fonts", "version": 1, "fonts": {}}
    outdir = pkg / "fonts"
    outdir.mkdir(parents=True, exist_ok=True)
    written = []
    for f in sorted(fdir.iterdir()):
        rec = read_font(f.read_bytes(), known)
        if rec is None:
            continue
        out["fonts"][f.name] = rec
        for a in rec["atlases"]:
            dest = outdir / (a + ".dds")
            if dest.is_file():
                continue
            try:
                blob, _n = evr_tex.rebuild_dds(root, a)
            except Exception:                                 # noqa: BLE001
                blob = None
            if blob:
                dest.write_bytes(blob)
                written.append(a)
    out["atlases_written"] = written
    (pkg / "fonts.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return {"fonts": len(out["fonts"]),
            "glyphs": {k: v["glyph_count"] for k, v in out["fonts"].items()},
            "atlases": written, "dir": str(outdir)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("package")
    ap.add_argument("--dir", default=r"H:\pcvr-extracted")
    a = ap.parse_args(argv)
    r = apply(Path(a.package), Path(a.dir))
    print("  fonts decoded : %d" % r["fonts"])
    for k, v in r["glyphs"].items():
        print("      %s  %d glyphs" % (k, v))
    print("  atlases written: %d %s" % (len(r["atlases"]), r["atlases"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
