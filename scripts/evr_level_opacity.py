"""Make every static instance in a level see-through.

    python scripts/evr_level_opacity.py --level <hash> --opacity 0.3
                                        --dir <extract> --out <input-pcvr>

## Why this and not the materials

The obvious place is the material's `k_alpha`, and it is a dead end here: NONE
of the arena's 160 shipped materials carries that property at all (0 of 160),
so there is no value to turn -- it would have to be *added*, growing two tables
inside `CGMaterialResourceWin10` whose framing does not match the documented
Win7 layout (`material_scalars` reads counts of 0 and its size arithmetic misses
by 3,328 bytes on a real file). That is a decode job, not a tweak. Materials are
also GLOBAL assets shared between levels, so editing one changes every map that
binds it.

`CStaticInstanceModelCR`'s `mid["colors"]` is a per-instance **RGBA float** and
reads `(1.0, 1.0, 1.0, 1.0)` on all 732 arena instances -- one distinct value
across the whole level, which is what an untouched per-instance tint looks like.
Writing the fourth component is one file, one field, per instance, and nothing
outside the level sees it.

⚠ What this cannot promise: whether the engine BLENDS on that alpha depends on
the material's blend mode, and most arena materials are opaque. An opaque
surface may ignore the instance alpha entirely, in which case the level looks
unchanged -- that is a real possible outcome, not a failure of the write. The
write itself is verifiable (and is verified below); the shading is not, from
here.

Reverting is a copy: nothing else in the file is touched.
"""

from __future__ import annotations

import argparse
import shutil
import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
for _p in (str(_SCRIPTS), str(_SCRIPTS.parent),
           r"J:\EchoVR-Tools-Launcher\quest_combat_port\tools",
           r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json                                                   # noqa: E402
import evr_paths                                              # noqa: E402

T_CSIMCR = "263584544abbd56c"
T_MAT = "e3e0266f1911dafa"

#: `CGMaterialResourceWin10` header, from `evr_material_resource`.
MAT_BLENDMODE, MAT_MATTYPE = 0x18, 0x1A
#: `eMTForwardTransparent` / `eBlendTransparent`. ⛔ `mattype` is the PASS, and
#: it is the field that decides: `eMTDeferredOpaque` (0) and `eMTForwardOpaque`
#: (1) are drawn in the opaque pass and can never blend whatever `blendmode`
#: says. 173 of the arena's 241 materials are ForwardOpaque, which is why
#: setting a per-instance alpha alone changed nothing.
MATTYPE_FORWARD_TRANSPARENT, BLENDMODE_TRANSPARENT = 2, 7
#: Every mattype already drawn on the transparent pass.
TRANSPARENT_MATTYPES = {2, 3, 4, 10, 16}


def transparent_budget(package: Path):
    """`[(material_hash, emissions)]`, biggest first, and the stock load.

    The forward-transparent pass is sorted and has a budget. The arena SHIPS
    488 of its 4,290 scene emissions on that pass (11.4%); moving every material
    onto it asks for all 4,290, and the engine drops what does not fit -- whole
    chunks of the map stop drawing. So the choice of WHICH materials to convert
    has to be made against a number, not `all`.

    Ranked by triangles per emission -- the most visible surface per draw slot.
    See the comment on `ranked` for why instance count is the wrong metric.
    """
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    specs = {e["matidx"]: e["spec"] for e in
             json.loads((package / "materials.json").read_text(encoding="utf-8"))["materials"]}
    blob = (package / (manifest.get("instances_blob") or "blobs/instances.bin")).read_bytes()
    n = min(int(manifest.get("num_instances") or 0), len(blob) // 44)
    per_mesh = {}
    for mi, mesh in enumerate(manifest.get("meshes") or []):
        sp = specs.get(mesh.get("matidx"))
        per_mesh[mi] = (sp or {}).get("material_hash"), (sp or {}).get("mattype")
    tris = {mi: (mesh.get("nindices") or 0) // 3
            for mi, mesh in enumerate(manifest.get("meshes") or [])}
    counts, area, stock, opaque = {}, {}, 0, set()
    for i in range(n):
        mi = struct.unpack_from("<I", blob, i * 44)[0]
        h, mt = per_mesh.get(mi, (None, None))
        if h is None:
            continue
        counts[h] = counts.get(h, 0) + 1
        area[h] = area.get(h, 0) + tris.get(mi, 0)
        if mt in TRANSPARENT_MATTYPES:
            stock += 1
        else:
            opaque.add(h)
    # ⛔ Rank by TRIANGLES PER EMISSION, not by instance count. The budget is
    # spent in emissions (draw slots) but what you SEE is geometry, and the two
    # pull in opposite directions: the arena's biggest surface is 274,632
    # triangles over 8 instances (34,329 tris per slot), while the material with
    # the most instances is 400 small props totalling 125,520 (314 per slot).
    # Ranking by instance count spends the whole budget on repeated bolts and
    # changes nothing you can see -- which is exactly what happened at
    # `--budget 1500`: two materials converted, nothing looked transparent.
    ranked = sorted(counts.items(),
                    key=lambda kv: -(area.get(kv[0], 0) / max(kv[1], 1)))
    return ranked, stock, n, opaque


def patch_materials(hashes, extract: Path, out: Path, backup_dir: Path) -> dict:
    """Move each material to the forward-transparent pass, in place.

    Two u16 writes per file and no size change, so nothing downstream shifts.

    ⚠ Materials are GLOBAL assets: the same hash is bound by other levels, so
    this changes them everywhere until the originals are put back. Every file
    touched is copied to `backup_dir` first.

    ⚠ Being on the transparent pass does not by itself make a surface
    see-through -- it only makes the alpha MATTER. Where that alpha comes from
    (the per-instance tint, `k_alpha`, or the base-colour map's alpha channel)
    is not settled, and a material whose only alpha source is an opaque texture
    will still draw solid.
    """
    backup_dir.mkdir(parents=True, exist_ok=True)
    (out / T_MAT).mkdir(parents=True, exist_ok=True)
    changed = already = missing = 0
    for h in hashes:
        src = out / T_MAT / h
        if not src.is_file():
            src = extract / T_MAT / h
        if not src.is_file():
            missing += 1
            continue
        blob = bytearray(src.read_bytes())
        if len(blob) < 0x20:
            missing += 1
            continue
        bm = struct.unpack_from("<H", blob, MAT_BLENDMODE)[0]
        mt = struct.unpack_from("<H", blob, MAT_MATTYPE)[0]
        if mt == MATTYPE_FORWARD_TRANSPARENT and bm == BLENDMODE_TRANSPARENT:
            already += 1
            continue
        bak = backup_dir / h
        if not bak.is_file():
            bak.write_bytes(bytes(blob))
        struct.pack_into("<H", blob, MAT_BLENDMODE, BLENDMODE_TRANSPARENT)
        struct.pack_into("<H", blob, MAT_MATTYPE, MATTYPE_FORWARD_TRANSPARENT)
        (out / T_MAT / h).write_bytes(bytes(blob))
        changed += 1
    return {"changed": changed, "already": already, "missing": missing}


def set_instance_opacity(blob: bytes, opacity: float, tint=(1.0, 1.0, 1.0)):
    """Rewrite every instance's RGBA tint. Returns `(bytes, count, before)`."""
    from resource_io import cstaticinstancemodelcr as CSIMCR
    o = CSIMCR.read(blob)
    n = o["n"]
    buf = bytearray(o["mid"]["colors"])
    before = struct.unpack_from("<4f", buf, 0) if n else None
    for i in range(n):
        struct.pack_into("<4f", buf, i * 16, tint[0], tint[1], tint[2], opacity)
    o["mid"] = dict(o["mid"], colors=bytes(buf))
    return CSIMCR.write(o), n, before


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--level", required=True, help="level hash")
    ap.add_argument("--opacity", type=float, default=0.3,
                    help="0 = invisible, 1 = solid (default 0.3 = 70%% "
                         "see-through)")
    ap.add_argument("--tint", default="1,1,1", help="r,g,b multiplier")
    ap.add_argument("--dir", default=None, help="flat extract (or EVR_EXTRACT_DIR)")
    ap.add_argument("--out", required=True, help="input-pcvr staging directory")
    ap.add_argument("--materials", default=None,
                    help="also move every material named in this scene "
                         "package's materials.json to the forward-transparent "
                         "pass (mattype 2 / blendmode 7)")
    ap.add_argument("--budget", type=int, default=0,
                    help="how many scene emissions may be on the transparent "
                         "pass. 0 = the level's own stock load, which is the "
                         "number the engine demonstrably handles")
    ap.add_argument("--from-staged", action="store_true",
                    help="patch the CSIMCR already in --out (keeps any models "
                         "grafted into it) instead of the pristine extract")
    args = ap.parse_args(argv)

    extract = evr_paths.extract_dir(args.dir)
    if extract is None and not args.from_staged:
        ap.error("no game extract given: pass --dir <path> or set EVR_EXTRACT_DIR")
    out = Path(args.out)
    level = args.level.lower()
    src = (out / T_CSIMCR / level) if args.from_staged else (extract / T_CSIMCR / level)
    if not src.is_file():
        ap.error("no CStaticInstanceModelCR for %s at %s" % (level, src))
    tint = tuple(float(v) for v in args.tint.split(","))

    blob = src.read_bytes()
    backup = out.parent / ("%s_csimcr_before_opacity.bin" % level)
    backup.parent.mkdir(parents=True, exist_ok=True)
    if not backup.is_file():
        shutil.copy2(src, backup)
    new, n, before = set_instance_opacity(blob, args.opacity, tint)
    (out / T_CSIMCR).mkdir(parents=True, exist_ok=True)
    (out / T_CSIMCR / level).write_bytes(new)

    # read it back rather than trusting the write
    check, _n, after = set_instance_opacity(new, args.opacity, tint)
    print("  instances: %d" % n)
    print("  tint was %s -> now (%.3f, %.3f, %.3f, %.3f)"
          % (tuple(round(v, 3) for v in before) if before else None,
             tint[0], tint[1], tint[2], args.opacity))
    print("  size %d -> %d (unchanged: %s)" % (len(blob), len(new),
                                               len(blob) == len(new)))
    print("  wrote %s" % (out / T_CSIMCR / level))
    print("  original kept at %s" % backup)

    if args.materials:
        import json as _json
        doc = _json.loads((Path(args.materials) / "materials.json")
                          .read_text(encoding="utf-8"))
        ranked, stock, emissions, opaque = transparent_budget(Path(args.materials))
        # ⛔ The cap is NOT measured. What IS measured: the arena ships `stock`
        # emissions on the transparent pass and renders them fine, and asking
        # for all 4,290 drops whole chunks of the map. The real ceiling is
        # somewhere between, and the only way to find it is to raise `--budget`
        # until geometry starts disappearing again. The default spends twice
        # the stock load, which is a step into the unknown region rather than a
        # number anything has verified.
        budget = args.budget if args.budget > 0 else stock * 2
        hashes, used = [], stock
        for h, cnt in ranked:
            if h not in opaque:
                continue                  # already on the pass, nothing to do
            if used + cnt > budget:
                continue
            hashes.append(h)
            used += cnt
        bdir = out.parent / ("%s_materials_before_opacity" % level)
        # put back anything a previous, greedier run converted
        restored = 0
        if bdir.is_dir():
            for p in bdir.iterdir():
                if p.name not in hashes:
                    shutil.copy2(p, out / T_MAT / p.name)
                    restored += 1
        r = patch_materials(hashes, extract, out, bdir)
        print("  emissions: %d total, %d already transparent in stock" % (emissions, stock))
        print("  budget %d emission(s) -> %d material(s) converted, "
              "%d/%d emission(s) now transparent"
              % (budget, r["changed"], used, emissions))
        print("  converted (tris/emission, biggest first):")
        look = {h: (c, a) for h, c, a in
                [(h, cnt, 0) for h, cnt in ranked]}
        for h in hashes[:8]:
            print("     %s" % h)
        if restored:
            print("  restored %d material(s) a previous run had converted" % restored)
        print("  originals kept in %s" % bdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
