"""Extract every WWise stream to J:\\EchoAudio, foldered by the event that fires it.

    python scripts/evr_extract_audio_named.py [--out J:\\EchoAudio] [--dir H:\\pcvr-extracted]

HOW THE NAMES ARE GOT
---------------------
WWise identifies everything by the **FNV-1 32-bit hash of the lowercased name**
(not `rad_hash`). Hashing every string in the game's `sourcedb` and the engine
`core` tree recovered `play_boost` and `play_collision`, which pinned the hash
function; brute-forcing Echo VR vocabulary through it named 23 objects, 17 of
them Events -- including `vo_announcer_arena`, `vo_announcer_combat` and the
`music_arena_*` set.

⚠ That is 17 of 1,731 events. The rest keep their id: a folder is
`event_<id>` when the name is not recovered. Nothing is invented.

THE GRAPH, EACH HOP VALIDATED
-----------------------------
    Event(4) -> Action(3) -> Sound(2) | Container(5,6,7,9,10,12,13) -> ... -> Sound
    Sound -> source id -> DIDX -> the bytes in DATA

* events: 1,731 of 1,737 parse with EVERY action id resolving to a real object
* actions: 1,821 of 1,928 have a target that resolves
* containers: 1,253 of 1,800 yield a children list where EVERY child is a known
  object id and none repeats -- that all-resolve test is what makes the scan
  safe rather than a guess
* result: 1,598 events reach audio, covering 2,294 of the 2,350 DIDX streams

AUDIO, BEST SOURCE FIRST
------------------------
1. the WAV already produced by EchoVR-Audio-Editor (1,307 of 2,350 ids) --
   verified decodable, so it is preferred and simply copied
2. else, for linear-PCM streams, a rebuilt canonical WAV
3. else the raw `.wem` (WWise Vorbis needs codebooks nothing here has)
"""
from __future__ import annotations

import argparse
import collections
import json
import shutil
import struct
import sys
from pathlib import Path

CONV = Path(r"C:\Oculus\Games\Software\Software\ready-at-dawn-echo-arena"
            r"\bin\win10\Tools\Tools\AudioFiles11")
SCRATCH = Path(r"J:\TMP\claude\J--EchoVR-Tools-Launcher-lone-echo-blender"
               r"\f8941b92-e46f-46f7-9dc8-fcd3e0aa93a9\scratchpad")


def riff_chunks(w):
    out, o = {}, 12
    while o + 8 <= len(w):
        t = w[o:o + 4]
        sz = struct.unpack_from("<I", w, o + 4)[0]
        out[t.decode("ascii", "ignore")] = (o + 8, sz)
        o += 8 + sz + (sz & 1)
    return out


def to_wav(w):
    c = riff_chunks(w)
    if "fmt " not in c or "data" not in c:
        return None
    fo, _ = c["fmt "]
    tag, ch, rate, bps, align, bits = struct.unpack_from("<HHIIHH", w, fo)
    if bits not in (8, 16, 24, 32) or not ch or not rate:
        return None
    if bps != rate * ch * bits // 8:
        return None
    do, dsz = c["data"]
    pcm = w[do:do + dsz]
    return (b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " +
            struct.pack("<IHHIIHH", 16, 1, ch, rate, rate * ch * bits // 8,
                        ch * bits // 8, bits) +
            b"data" + struct.pack("<I", len(pcm)) + pcm)


def safe(s):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in s)[:80]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=r"J:\EchoAudio")
    ap.add_argument("--dir", default=r"H:\pcvr-extracted")
    a = ap.parse_args(argv)
    out = Path(a.out)
    root = Path(a.dir)

    hirc = json.loads((SCRATCH / "_hirc.json").read_text())
    graph = json.loads((SCRATCH / "_hirc_graph.json").read_text())
    names = {int(k): v for k, v in json.loads((SCRATCH / "_wwise_names.json").read_text()).items()}
    wbe = {int(k): v for k, v in graph["wem_by_event"].items()}

    # each stream is filed under ONE event: a NAMED one wins, else the lowest id,
    # so the layout is deterministic instead of dependent on dict order
    owner = {}
    for e in sorted(wbe, key=lambda e: (e not in names, e)):
        for w in wbe[e]:
            owner.setdefault(w, e)

    sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor")
    from echo_editor.assemble.authoring import AuthoringBackend
    bk = AuthoringBackend(); bk.activate()
    BKDIR = root / ("%016x" % (bk.rad_hash("CWWiseSoundBankResourceWin10") & 0xFFFFFFFFFFFFFFFF))

    conv = {}
    if CONV.is_dir():
        for d in CONV.iterdir():
            if d.name.endswith("_WAV"):
                for p in d.iterdir():
                    if p.suffix == ".wav":
                        conv.setdefault(p.stem, p)

    out.mkdir(parents=True, exist_ok=True)
    stat = collections.Counter()
    manifest = []
    seen = set()
    for f in sorted(BKDIR.iterdir()):
        b = f.read_bytes()
        i = b.find(b"BKHD")
        if i < 0:
            continue
        ch, o = {}, i
        while o + 8 <= len(b):
            t = b[o:o + 4]
            sz = struct.unpack_from("<I", b, o + 4)[0]
            if not t.isalnum() or o + 8 + sz > len(b):
                break
            ch[t.decode()] = (o + 8, sz)
            o += 8 + sz
        if "DIDX" not in ch or "DATA" not in ch:
            continue
        do, dsz = ch["DIDX"]
        ao, _ = ch["DATA"]
        for k in range(dsz // 12):
            wid, off, size = struct.unpack_from("<3I", b, do + k * 12)
            if wid in seen:
                stat["duplicate id across banks"] += 1
                continue
            seen.add(wid)
            w = b[ao + off:ao + off + size]
            ev = owner.get(wid)
            folder = (safe(names[ev]) if ev in names
                      else ("event_%d" % ev if ev is not None else "_unreferenced"))
            d = out / folder
            d.mkdir(parents=True, exist_ok=True)
            src = conv.get(str(wid))
            if src is not None:
                shutil.copyfile(src, d / ("%d.wav" % wid))
                kind = "wav (pre-converted)"
            else:
                wav = to_wav(w)
                if wav:
                    (d / ("%d.wav" % wid)).write_bytes(wav)
                    kind = "wav (rebuilt PCM)"
                else:
                    (d / ("%d.wem" % wid)).write_bytes(w)
                    kind = "wem (Vorbis, needs codebooks)"
            stat[kind] += 1
            manifest.append({"wem": wid, "event": ev, "event_name": names.get(ev),
                             "folder": folder, "bank": f.name, "bytes": size,
                             "written": kind})
    (out / "audio_manifest.json").write_text(json.dumps(
        {"format": "evr_audio_named", "version": 1,
         "streams": len(manifest), "named_events": len(names),
         "note": "folder = the event that fires the stream; event_<id> when the "
                 "FNV-1 name was not recovered",
         "entries": manifest}, indent=1), encoding="utf-8")
    print("streams written : %d" % len(manifest))
    for k, v in stat.most_common():
        print("   %-32s %d" % (k, v))
    print("folders         : %d" % len({m["folder"] for m in manifest}))
    print("named folders   : %d" % len({m["folder"] for m in manifest if m["event_name"]}))
    print("-> %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
