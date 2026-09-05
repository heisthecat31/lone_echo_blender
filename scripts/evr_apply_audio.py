"""Audio: the WWise banks, the streams inside them, and where a level plays them.

    python scripts/evr_apply_audio.py <package> <level> [--dir <extract>] [--no-streams]

Writes `<pkg>/audio.json`, `<pkg>/audio/wem/` and `<pkg>/audio/wav/`.

THE BANKS
---------
`CWWiseSoundBankResourceWin10` is a stock WWise bank behind a 16-byte wrapper:
a `u32` size at +0 then the bank at +16, where `BKHD` starts. All 41 banks in
the corpus parse into BKHD / DIDX / DATA / HIRC.

`DIDX` is 12 bytes per stream -- `u32 id, u32 offset, u32 size` -- indexing into
`DATA`. That yields **2,721 streams, 83.3 MB**, every one a valid RIFF.

PLAYABILITY, STATED HONESTLY
----------------------------
* 55 streams are `0xFFFE`: real 16-bit PCM (`bps == rate * channels * 2`
  checks out) wearing a WWise container ffmpeg will not open. Rewriting a
  canonical 44-byte PCM header around the `data` chunk makes them play --
  verified through ffmpeg, with sane durations.
* 2,666 streams are `0xFFFF`, WWise Vorbis. Those need the bank's codebooks
  (ww2ogg / vgmstream); nothing here can decode them, so they are written as
  `.wem` and left as `.wem`. They are NOT silently renamed to something that
  looks playable.

WHERE A LEVEL PLAYS THEM
------------------------
`CAmbientSoundCR` is the level's own placement record:

    +0x08  u64  actor
    +0x20  u32  WWise EVENT id
    +0x24  f32  radius

The event id is not a guess: all 20 ambient records across arena, gauss,
lobby_b_arena and tutorial_arena resolve to a HIRC **type 4 (Event)** object in
the banks -- 20 of 20. Radii read 17.0 (arena), 20.0 (gauss), 14.0 (lobby).
"""
from __future__ import annotations

import argparse
import collections
import json
import struct
import sys
from pathlib import Path

BANK_TYPE = "CWWiseSoundBankResourceWin10"
AMBIENT_TYPE = "CAmbientSoundCRWin10"
A_ACTOR, A_EVENT, A_RADIUS = 0x08, 0x20, 0x24
#: WWise HIRC object types worth naming
HIRC_NAMES = {1: "Settings", 2: "Sound", 3: "Action", 4: "Event",
              5: "RandomSequence", 6: "SwitchContainer", 7: "ActorMixer",
              8: "Bus", 9: "LayerContainer", 10: "MusicSegment",
              11: "MusicTrack", 12: "MusicSwitch", 13: "MusicRanSeq",
              14: "Attenuation", 16: "Effect", 17: "AuxBus",
              18: "LFO", 19: "Envelope", 21: "AudioDevice"}


def _backend():
    sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor")
    from echo_editor.assemble.authoring import AuthoringBackend
    b = AuthoringBackend()
    b.activate()
    return b


def bank_chunks(b: bytes) -> dict:
    """`{tag: (offset, size)}` for a wrapped WWise bank, or `{}`."""
    i = b.find(b"BKHD")
    if i < 0:
        return {}
    out, o = {}, i
    while o + 8 <= len(b):
        tag = b[o:o + 4]
        sz = struct.unpack_from("<I", b, o + 4)[0]
        if not tag.isalnum() or o + 8 + sz > len(b):
            break
        out[tag.decode()] = (o + 8, sz)
        o += 8 + sz
    return out


def riff_chunks(w: bytes) -> dict:
    out, o = {}, 12
    while o + 8 <= len(w):
        tag = w[o:o + 4]
        sz = struct.unpack_from("<I", w, o + 4)[0]
        out[tag.decode("ascii", "ignore")] = (o + 8, sz)
        o += 8 + sz + (sz & 1)
    return out


def to_wav(w: bytes):
    """A canonical PCM WAV, or None when the stream is not plain PCM."""
    c = riff_chunks(w)
    if "fmt " not in c or "data" not in c:
        return None
    fo, _ = c["fmt "]
    tag, ch, rate, bps, align, bits = struct.unpack_from("<HHIIHH", w, fo)
    if bits not in (8, 16, 24, 32) or not ch or not rate:
        return None
    if bps != rate * ch * bits // 8:          # not linear PCM after all
        return None
    do, dsz = c["data"]
    pcm = w[do:do + dsz]
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, ch, rate, rate * ch * bits // 8,
                        ch * bits // 8, bits) +
            b"data" + struct.pack("<I", len(pcm)) + pcm)


def read_hirc(b: bytes, chunks: dict) -> dict:
    """`{object id: type}` from the bank hierarchy."""
    if "HIRC" not in chunks:
        return {}
    ho, hsz = chunks["HIRC"]
    n = struct.unpack_from("<I", b, ho)[0]
    out, p = {}, ho + 4
    for _ in range(n):
        if p + 9 > ho + hsz:
            break
        typ = b[p]
        sz = struct.unpack_from("<I", b, p + 1)[0]
        out[struct.unpack_from("<I", b, p + 5)[0]] = typ
        p += 5 + sz
    return out


def apply(pkg: Path, level: str, root: Path, streams: bool = True) -> dict:
    backend = _backend()
    H = lambda s: "%016x" % (backend.rad_hash(s) & 0xFFFFFFFFFFFFFFFF)
    lv = level if len(level) == 16 and all(c in "0123456789abcdef" for c in level.lower()) \
        else H(level)

    outdir = pkg / "audio"
    wem_dir, wav_dir = outdir / "wem", outdir / "wav"
    if streams:
        wem_dir.mkdir(parents=True, exist_ok=True)
        wav_dir.mkdir(parents=True, exist_ok=True)
    else:
        outdir.mkdir(parents=True, exist_ok=True)

    hirc, banks = {}, {}
    n_wem = n_wav = 0
    codecs = collections.Counter()
    bdir = root / H(BANK_TYPE)
    for f in sorted(bdir.iterdir()) if bdir.is_dir() else []:
        b = f.read_bytes()
        ch = bank_chunks(b)
        if not ch:
            continue
        hirc.update(read_hirc(b, ch))
        ids = []
        if "DIDX" in ch and "DATA" in ch:
            do, dsz = ch["DIDX"]
            ao, _ = ch["DATA"]
            for k in range(dsz // 12):
                wid, off, size = struct.unpack_from("<3I", b, do + k * 12)
                ids.append(wid)
                w = b[ao + off:ao + off + size]
                j = w.find(b"fmt ")
                codecs["0x%04X" % struct.unpack_from("<H", w, j + 8)[0] if j > 0 else "?"] += 1
                if not streams:
                    continue
                (wem_dir / ("%d.wem" % wid)).write_bytes(w)
                n_wem += 1
                wav = to_wav(w)
                if wav:
                    (wav_dir / ("%d.wav" % wid)).write_bytes(wav)
                    n_wav += 1
        banks[f.name] = {"bytes": len(b), "chunks": {k: v[1] for k, v in ch.items()},
                         "streams": len(ids)}

    amb = []
    af = root / H(AMBIENT_TYPE) / lv
    if af.is_file():
        b = af.read_bytes()
        ds = struct.unpack_from("<Q", b, 8)[0]
        n = struct.unpack_from("<Q", b, 40)[0]
        st = ds // n if n else 0
        for i in range(n):
            o = 56 + i * st
            ev = struct.unpack_from("<I", b, o + A_EVENT)[0]
            amb.append({"actor": "%016x" % struct.unpack_from("<Q", b, o + A_ACTOR)[0],
                        "event": ev,
                        "radius": round(struct.unpack_from("<f", b, o + A_RADIUS)[0], 4),
                        "hirc_type": HIRC_NAMES.get(hirc.get(ev), None),
                        "resolves": ev in hirc})

    doc = {"format": "evr_audio", "version": 1, "level": lv,
           "banks": banks, "bank_count": len(banks),
           "streams_total": sum(v["streams"] for v in banks.values()),
           "codecs": dict(codecs),
           "hirc_objects": len(hirc),
           "hirc_by_type": {HIRC_NAMES.get(t, str(t)): c for t, c in
                            collections.Counter(hirc.values()).items()},
           "ambient_sounds": amb,
           "streams_written": n_wem, "wav_written": n_wav,
           "note": ("0xFFFF streams are WWise Vorbis and need ww2ogg/vgmstream "
                    "codebooks -- they are written as .wem and left that way. "
                    "0xFFFE streams are linear PCM and are rewritten as "
                    "playable .wav.")}
    (pkg / "audio.json").write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("package")
    ap.add_argument("level")
    ap.add_argument("--dir", default=r"H:\pcvr-extracted")
    ap.add_argument("--no-streams", action="store_true",
                    help="index only; do not write the 83 MB of audio")
    a = ap.parse_args(argv)
    d = apply(Path(a.package), a.level, Path(a.dir), streams=not a.no_streams)
    print("  banks            : %d" % d["bank_count"])
    print("  streams indexed  : %d   codecs %s" % (d["streams_total"], d["codecs"]))
    print("  HIRC objects     : %d" % d["hirc_objects"])
    print("  ambient sounds   : %d  (%d resolve to a WWise Event)"
          % (len(d["ambient_sounds"]),
             sum(1 for x in d["ambient_sounds"] if x["resolves"])))
    print("  written          : %d .wem, %d .wav" % (d["streams_written"], d["wav_written"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
