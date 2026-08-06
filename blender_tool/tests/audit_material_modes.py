"""audit_material_modes — corpus audit of SGMaterialData transparency/emissive state.

Decodes EVERY CGMaterialResourceWin7 in one archive and reports:
  * the (mattype, blendmode) joint histogram      -> which EMaterialType values ship
  * SGMaterialData::EFlags histogram
  * materialprop names resolved through the authoring-source vocabulary
    (k_alpha / k_alpha_threshold / k_emissive_scale / k_refractive_index / ...)
  * every material whose alpha, emissive or blend state is non-default

`--archive` MUST run under Windows Python (Oodle DLL) from the
the repository root:

    python.exe blender_tool/tests/audit_material_modes.py --archive 0703fd2acd5803e9
    python.exe blender_tool/tests/audit_material_modes.py --archive 0703fd2acd5803e9 --tsv out.tsv

Memory: loads ONE archive's decompressed primary stream and frees it before
returning. Do not run two archives concurrently.

`--fixtures` audits already-exported `.lemesh` packages instead. It is pure
stdlib, loads no archive, and is therefore safe to run any time:

    python3 blender_tool/tests/audit_material_modes.py \
        --fixtures blender_tool/exports/fixtures_mat

The name vocabulary is recovered from the engine's own ubermaterial and its
material asset schema and lives in `le_mesh.material_scalars`, so this tool
needs nothing but the archive. Every
name is a VERIFIED CSymbol64 preimage: a candidate is only ever accepted for a
hash when symbol64(name) reproduces that hash exactly.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

THIS = Path(__file__).resolve()
BLENDER_TOOL = THIS.parents[1]
LE_ROOT = THIS.parents[2]
for _p in (str(BLENDER_TOOL), str(LE_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from le_mesh import material_scalars as msc   # noqa: E402

# The enum tables and the authored-parameter vocabulary now live in
# `le_mesh.material_scalars` so the decoder itself can emit `mattype_name` /
# `blend_mode_name` / `named_scalars_resolved`.  Re-exported here so this audit
# tool (and the tests that read `amm.MATTYPE_NAMES`) keep working unchanged.
MATTYPE_NAMES = msc.MATTYPE_NAMES          # `name-confirmed`: CGMaterial::EMaterialType
BLENDMODE_NAMES = msc.BLENDMODE_NAMES      # `name-confirmed`: NRadEngine::EBlendMode

# --- authored parameter vocabulary (ubermaterial + material asset schema) ---
# Re-exported from le_mesh.material_scalars, where the name-derived lists now
# live (they were regenerated straight out of the engine's own declarations,
# which added the non-layer `[PREFIXPROPERTY]` groups pom_/blood_/scorch_/
# cutting_ — the axis that recovered `pom_height_map`).
GLOBAL_PARAMS = msc.GLOBAL_PARAMS
LAYER_PARAMS = msc.LAYER_PARAMS
GROUP_PARAMS = msc.GROUP_PARAMS
SUFFIXED = msc.SUFFIXED


def build_name_table(max_layer: int = 8) -> dict[int, str]:
    """hash -> authored parameter name (every entry a verified preimage)."""
    return msc.build_name_table(max_layer)


# ---------------------------------------------------------------------------

def audit_archive(archive_hash: str, verbose: bool = False) -> list[dict]:
    """Decode every material in one archive. Frees the archive before returning."""
    from le_oodle import load_decompressed
    from le_archive_decode import (
        ARCHIVE_PRIMARY, archive_offsets, load_hash_lookup)
    from le_texture_extract import collect_resource_map
    import le_material_slice as msp
    from le_archive_decode import ARCHIVE_GPU

    names = load_hash_lookup(Path("hash_lookup.json"))
    if verbose:
        print(f"hash_lookup: {len(names)} entries")

    primary = load_decompressed(ARCHIVE_PRIMARY / archive_hash)
    gpu_path = ARCHIVE_GPU / archive_hash
    gpu = load_decompressed(gpu_path) if gpu_path.exists() else b""
    try:
        _, _, data_off, header_off = archive_offsets(primary, gpu)
        del gpu
        mats = collect_resource_map(primary, header_off, msp.MATERIAL_TYPE)
        rows = []
        for name_hash, (_idx, pos, size) in sorted(mats.items()):
            slc = primary[data_off + pos: data_off + pos + size]
            sc = msc.decode_material_scalars(slc)
            sc["hash"] = f"{name_hash:016x}"
            sc["slice_size"] = size
            rows.append(sc)
        return rows
    finally:
        del primary


def report(rows: list[dict], name_table: dict[int, str]) -> None:
    print(f"\nmaterials decoded: {len(rows)}")

    joint = collections.Counter((r["mattype"], r["blend_mode"]) for r in rows)
    print("\n(mattype, blendmode) joint histogram")
    print(f"  {'n':>5}  {'mattype':>3} {'name':<24} {'blend':>3} {'name'}")
    for (mt, bm), n in joint.most_common():
        print(f"  {n:5d}  {mt:>3} {MATTYPE_NAMES.get(mt, '?'):<24} "
              f"{bm:>3} {BLENDMODE_NAMES.get(bm, '?')}")

    flags = collections.Counter()
    for r in rows:
        for f in r["flag_names"]:
            flags[f] += 1
    print("\nEFlags histogram")
    for f, n in flags.most_common():
        print(f"  {n:5d}  {f}")

    props = collections.Counter()
    unknown = collections.Counter()
    for r in rows:
        for hhex in r["named_scalars"]:
            h = int(hhex, 16)
            nm = name_table.get(h)
            if nm:
                props[nm] += 1
            else:
                unknown[hhex] += 1
    print(f"\nmaterialprop names resolved ({len(props)} distinct)")
    for nm, n in props.most_common(40):
        print(f"  {n:5d}  {nm}")
    if unknown:
        print(f"\nUNRESOLVED materialprop hashes ({len(unknown)} distinct)")
        for h, n in unknown.most_common(20):
            print(f"  {n:5d}  {h}")

    # per-layer emissive knobs + the layer-selection error (B4)
    print("\nper-layer emissive_intensity distribution")
    per_layer = collections.defaultdict(collections.Counter)
    for r in rows:
        for lay in r.get("layers", ()):
            if lay["emissive_intensity"] is not None:
                per_layer[lay["index"]][lay["emissive_intensity"]] += 1
    for L in sorted(per_layer):
        tot = sum(per_layer[L].values())
        vals = ", ".join(f"{v:g}x{n}" for v, n in sorted(per_layer[L].items()))
        print(f"  layer{L}: {tot:3d} materials   {vals}")

    print("\nauthored-default fallbacks (absent => authored default)")
    fell = collections.Counter()
    for r in rows:
        for nm in r.get("scalar_defaults_applied", ()):
            fell[nm] += 1
    for nm, n in fell.most_common():
        print(f"  {n:5d} of {len(rows)}  {nm} -> {msc.AUTHORED_DEFAULTS_GLOBAL[nm]:g}")

    # non-default transparency / emissive
    interesting = [r for r in rows
                   if r["alpha"] != 1.0 or r["is_emissive"] or r["blend_mode"] != 0
                   or r["mattype"] not in (0, 1)]
    print(f"\nnon-opaque / emissive materials: {len(interesting)} of {len(rows)}")
    for r in sorted(interesting, key=lambda x: (x["mattype"], x["blend_mode"]))[:40]:
        em = ",".join(f"{v:.3f}" for v in r["emissive_color"])
        inten = ",".join("-" if lay["emissive_intensity"] is None
                         else f"{lay['emissive_intensity']:g}"
                         for lay in r.get("layers", ()))
        print(f"  {r['hash']}  mattype={r['mattype']:>2} "
              f"{r.get('mattype_name', '?'):<22} "
              f"blend={r['blend_mode']:>2} {r.get('blend_mode_name', '?'):<22} "
              f"alpha={r['alpha']:.3f} bakeA={r['base_color_factor'][3]:.3f} "
              f"emissive=[{em}] layerI=[{inten}] ds={int(r['double_sided'])}")


def audit_fixture_packages(root: Path) -> list[dict]:
    """Corpus audit straight off exported `.lemesh` manifests — NO archive load.

    Safe to run anywhere (pure stdlib, no oodle, no multi-GB read). One row per
    DISTINCT material_hash; rows that carry `named_scalars` were decoded by
    `decode_material_scalars` at extract time, so the manifest already holds the
    materialprop table.
    """
    import json

    by_hash: dict[str, dict] = {}
    for man in sorted(Path(root).glob("*.lemesh/manifest.json")):
        for mat in json.loads(man.read_text(encoding="utf-8")).get("materials", []):
            h = mat.get("material_hash", "")
            prev = by_hash.get(h)
            if prev is None or ("named_scalars" in mat and "named_scalars" not in prev):
                by_hash[h] = mat
    return [m for _h, m in sorted(by_hash.items())]


def report_fixtures(mats: list[dict], name_table: dict[int, str]) -> None:
    decoded = [m for m in mats if "named_scalars" in m]
    print(f"\ndistinct materials: {len(mats)}   with decoded materialprops: {len(decoded)}")

    joint = collections.Counter((m.get("mattype"), m.get("blend_mode")) for m in decoded)
    print("\n(mattype, blendmode) joint histogram")
    for (mt, bm), n in joint.most_common():
        print(f"  {n:5d}  {mt:>3} {MATTYPE_NAMES.get(mt, '?'):<24} "
              f"{bm:>3} {BLENDMODE_NAMES.get(bm, '?')}")

    byname: dict[str, list[float]] = collections.defaultdict(list)
    for m in decoded:
        for hh, v in m["named_scalars"].items():
            byname[name_table.get(int(hh, 16), hh)].append(v)
    print(f"\nmaterialprops carried, by resolved name ({len(byname)} distinct)")
    for nm in sorted(byname, key=lambda k: (-len(byname[k]), k)):
        vals = sorted({round(v, 6) for v in byname[nm]})
        print(f"  {len(byname[nm]):3d}  {nm:34s} {vals}")

    # the layer-selection error, measured
    print("\nB4 layer-selection check (routed emissive map vs flat emissive_intensity)")
    mismatch = 0
    checked = 0
    for m in decoded:
        role = (m.get("channels", {}).get("emission") or {}).get("role_key", "")
        if not role.startswith("layer"):
            continue
        L = int(role[5])
        want = None
        for hh, v in m["named_scalars"].items():
            if int(hh, 16) == msc.HASH_EMISSIVE_INTENSITY.get(L):
                want = v
        if want is None:
            continue
        checked += 1
        flat = m.get("emissive_intensity")
        if abs(want - flat) > 1e-6:
            mismatch += 1
            print(f"  {m['material_hash']}  {role}: correct={want:g} flat={flat:g} "
                  f"({want / flat if flat else float('inf'):.3g}x too dim)")
    print(f"  {mismatch} of {checked} materials with a routed emissive map are wrong today")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", default="0703fd2acd5803e9")
    ap.add_argument("--fixtures", type=Path, default=None,
                    help="audit exported .lemesh packages instead of an archive "
                         "(pure stdlib, no oodle, no archive load)")
    ap.add_argument("--tsv", type=Path, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    name_table = build_name_table()
    print(f"name table: {len(name_table)} verified preimages")

    if args.fixtures:
        report_fixtures(audit_fixture_packages(args.fixtures), name_table)
        return 0

    rows = audit_archive(args.archive, verbose=args.verbose)
    report(rows, name_table)

    if args.tsv:
        cols = ["hash", "slice_size", "mattype", "blend_mode", "alpha",
                "emissive_intensity", "double_sided", "flags", "is_emissive"]
        with args.tsv.open("w", encoding="utf-8", newline="") as fh:
            fh.write("\t".join(cols + ["mattype_name", "blendmode_name",
                                       "named_props"]) + "\n")
            for r in rows:
                props = ";".join(
                    f"{name_table.get(int(h, 16), h)}={v:g}"
                    for h, v in sorted(r["named_scalars"].items()))
                fh.write("\t".join(str(r[c]) for c in cols) +
                         f"\t{MATTYPE_NAMES.get(r['mattype'], '?')}"
                         f"\t{BLENDMODE_NAMES.get(r['blend_mode'], '?')}"
                         f"\t{props}\n")
        print(f"\nwrote {args.tsv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
