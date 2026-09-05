"""The UI tables `evr_ui_extract` does not carry, plus its missing textures.

`ui.json` already exports the canvases (element rect/uv) and the placements
that put them in the world. Four more UI record types ship in every level and
reach nothing:

    CSharedCanvasUICR   a canvas shared between actors, with a texture override
    CR15UIPageCR        UI pages
    CR15UILayoutCR      UI layouts
    CDialogue2CR        dialogue

    python scripts/evr_apply_ui_records.py <package> <level> [--dir <extract>]

Writes `<pkg>/ui/<level>/ui_records.json` and fills in any canvas texture that
is resident but was never written.

WHAT IS VERIFIED, AND WHAT IS NOT
---------------------------------
`CSharedCanvasUICR` is decoded: over arena's 27 records, `+0x08` resolves in
`CActorDataResource` 27/27, `+0x30` resolves as an actor 27/27, and `+0x28` is
a real `cgtextureresource` on 21 of 27. So a record binds an OWNER actor, a
TARGET actor and a texture.

The other three resolve only their actor at `+0x08` (5/5, 7/7, 1/1). Their
payload is an ordered pool of CSymbol64 references -- pages hold five (hash, 0)
pairs per entry, layouts four (hash, hash, 0) triples -- and those symbols
resolve to no canvas, texture or actor in this corpus. They are exported RAW
and labelled unresolved rather than guessed at.

⚠ A `texture_override` naming a texture that is not in the extract is a RUNTIME
RENDER TARGET -- the live scoreboard and match clock are generated per frame and
never ship as an asset. Those are labelled, so a consumer shows "dynamic"
instead of a missing-texture placeholder.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
for _p in (str(_SCRIPTS), str(_SCRIPTS.parent), str(_SCRIPTS.parent / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evr_texture_resource as evr_tex_res     # noqa: E402

#: (type, stride, flat?) for the four tables. Strides are MEASURED
#: (data_size // count) on arena, and agree on every level that ships them.
TABLES = (("CSharedCanvasUICRWin10", 72),
          ("CR15UIPageCRWin10", 264),
          ("CR15UILayoutCRWin10", 96),
          ("CDialogue2CRWin10", 176))

#: verified `CSharedCanvasUICR` fields
SC_ACTOR = 0x08
SC_TEXTURE = 0x28
SC_TARGET = 0x30


def _backend():
    sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor")
    from echo_editor.assemble.authoring import AuthoringBackend
    b = AuthoringBackend()
    b.activate()
    return b


def read_table(root: Path, backend, type_name: str, level: str, stride: int) -> dict:
    """Envelope-split one component record table. `{}` when the level lacks it."""
    h = "%016x" % (backend.rad_hash(type_name) & 0xFFFFFFFFFFFFFFFF)
    f = root / h / level
    if not f.is_file():
        return {}
    b = f.read_bytes()
    if len(b) < 56:
        return {}
    data_size = struct.unpack_from("<Q", b, 8)[0]
    count = struct.unpack_from("<Q", b, 40)[0]
    if not count:
        return {"type": type_name, "count": 0, "entries": []}
    measured = data_size // count
    entries = []
    for i in range(count):
        o = 56 + i * measured
        entries.append({"node": "%016x" % struct.unpack_from("<Q", b, o)[0],
                        "actor": "%016x" % struct.unpack_from("<Q", b, o + 8)[0],
                        "raw": b[o + 16:o + measured].hex()})
    return {"type": type_name, "count": count, "stride": measured,
            "stride_expected": stride, "pool_bytes": len(b) - 56 - data_size,
            "pool": b[56 + data_size:].hex(), "entries": entries}


def apply(pkg: Path, level: str, root: Path) -> dict:
    backend = _backend()
    H = lambda s: "%016x" % (backend.rad_hash(s) & 0xFFFFFFFFFFFFFFFF)
    lv = level if len(level) == 16 and all(c in "0123456789abcdef" for c in level.lower()) \
        else H(level)
    uidir = pkg / "ui" / lv
    if not uidir.is_dir():
        raise SystemExit("no UI sub-package at %s -- run evr_ui_extract first" % uidir)

    tex_dir = root / H("cgtextureresourceWin10")
    known_tex = {p.name for p in tex_dir.iterdir()} if tex_dir.is_dir() else set()
    actors = set()
    adr = root / H("CActorDataResourceWin10") / lv
    if adr.is_file():
        try:
            actors = {"%016x" % n for n in
                      backend.actor_data_mod.ActorDataResource.from_bytes(
                          adr.read_bytes()).nodeids}
        except Exception:                                     # noqa: BLE001
            actors = set()

    out = {"format": "evr_ui_records", "version": 1, "level": lv, "tables": {}}
    for name, stride in TABLES:
        t = read_table(root, backend, name, lv, stride)
        if not t:
            continue
        if name == "CSharedCanvasUICRWin10":
            b = (root / H(name) / lv).read_bytes()
            st = t["stride"]
            for i, e in enumerate(t["entries"]):
                o = 56 + i * st
                tex = "%016x" % struct.unpack_from("<Q", b, o + SC_TEXTURE)[0]
                tgt = "%016x" % struct.unpack_from("<Q", b, o + SC_TARGET)[0]
                e["texture"] = tex if tex in known_tex else ""
                e["texture_runtime"] = (tex not in known_tex
                                        and tex != "ffffffffffffffff")
                e["target_actor"] = tgt
                e["target_resolves"] = tgt in actors
        else:
            t["note"] = ("only `actor` (+0x08) is resolved; the pool is an "
                         "ordered CSymbol64 stream that matches no canvas, "
                         "texture or actor in this corpus")
        out["tables"][name] = t

    # --- fill in canvas textures that are resident but never written
    ui = json.loads((uidir / "ui.json").read_text(encoding="utf-8"))
    want = {e["texture"] for c in ui.get("canvases") or () for e in (c.get("elements") or ())}
    want |= {p["texture_override"] for p in ui.get("placements") or ()
             if p.get("texture_override")}
    tdir = uidir / "textures"
    tdir.mkdir(exist_ok=True)
    have = {p.stem for p in tdir.iterdir()}
    added, runtime = [], []
    # the rebuilder lives in evr_texture_resource, the same module
    # `evr_ui_extract` uses -- not a separate `evr_texture`.
    evr_tex = evr_tex_res
    for t in sorted(want - have):
        if t not in known_tex:
            runtime.append(t)
            continue
        if evr_tex is None:
            continue
        try:
            blob, _n = evr_tex.rebuild_dds(root, t)
        except Exception:                                     # noqa: BLE001
            blob = None
        if blob:
            (tdir / (t + ".dds")).write_bytes(blob)
            added.append(t)
    out["textures_added"] = added
    out["textures_runtime"] = runtime
    out["textures_runtime_note"] = (
        "named by a canvas or an override but absent from the extract: these "
        "are RUNTIME RENDER TARGETS (live scoreboard, match clock), generated "
        "per frame and never shipped -- label them, do not treat as missing")
    (uidir / "ui_records.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return {"tables": {k: v.get("count") for k, v in out["tables"].items()},
            "added": added, "runtime": runtime, "dir": str(uidir)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("package")
    ap.add_argument("level")
    ap.add_argument("--dir", default=r"H:\pcvr-extracted")
    a = ap.parse_args(argv)
    r = apply(Path(a.package), a.level, Path(a.dir))
    for k, v in r["tables"].items():
        print("  %-32s %s records" % (k, v))
    print("  textures written           : %d %s" % (len(r["added"]), r["added"] or ""))
    print("  runtime render targets     : %d %s" % (len(r["runtime"]), r["runtime"] or ""))
    print("  -> %s/ui_records.json" % r["dir"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
