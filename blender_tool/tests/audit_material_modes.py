"""audit_material_modes — corpus audit of SGMaterialData transparency/emissive state.

Decodes EVERY CGMaterialResourceWin7 in one archive and reports:
  * the (mattype, blendmode) joint histogram      -> which EMaterialType values ship
  * SGMaterialData::EFlags histogram
  * materialprop names resolved through the authoring-source vocabulary
    (k_alpha / k_alpha_threshold / k_emissive_scale / k_refractive_index / ...)
  * every material whose alpha, emissive or blend state is non-default

MUST run under Windows Python (the Oodle runtime is a Windows binary) from the
repository root:

    python.exe blender_tool/tests/audit_material_modes.py --archive 0703fd2acd5803e9
    python.exe blender_tool/tests/audit_material_modes.py --archive 0703fd2acd5803e9 --tsv out.tsv

Memory: loads ONE archive's decompressed primary stream and frees it before
returning. Do not run two archives concurrently.

The name vocabulary is the game's own authored material-parameter vocabulary,
embedded here so the tool needs nothing but the archive. Every name below is a
VERIFIED CSymbol64 preimage: `symbol64(name)` reproduces the on-disk hash exactly
(locked by `tests/test_transparency.py`).
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

THIS = Path(__file__).resolve()
BLENDER_TOOL = THIS.parents[1]
REPO_ROOT = THIS.parents[2]
for _p in (str(BLENDER_TOOL), str(REPO_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from le_mesh import material_scalars as msc   # noqa: E402

# --- EMaterialType (NRadEngine::CGMaterial::EMaterialType) ------------------
MATTYPE_NAMES = {
    0: "eMTDeferredOpaque", 1: "eMTForwardOpaque", 2: "eMTForwardTransparent",
    3: "eMTLowResTransparent", 4: "eMTSolidTransparent", 5: "eMTFullScreenEffect",
    6: "eMTParticles", 7: "eMT2D", 8: "eMTDebug", 9: "eMTAlphaTested",
    10: "eMTSkirt", 11: "eMTRefraction", 12: "eMTHair", 13: "eMTSkydome",
    14: "eMTOutline", 15: "eMTOutlineDepthFail", 16: "eMTTransparentPostAA",
}

# --- EBlendMode (NRadEngine::EBlendMode) ------------------------------------
BLENDMODE_NAMES = {
    0: "eBlendOpaque", 1: "eBlendAdditive", 2: "eBlendSubtractive",
    3: "eBlendMultiply", 4: "eBlendDarken", 5: "eBlendLighten", 6: "eBlendScreen",
    7: "eBlendTransparent", 8: "eBlendLinearDodge", 9: "eBlendLinearBurn",
    10: "eBlendSkirt", 11: "eBlendPremultipledAlpha", 12: "eBlendTranslucent",
    13: "eBlendMin", 14: "eBlendMax", 15: "eBlendAlphaToCoverage",
    16: "eBlendNoColorWrites", 17: "eBlendReverseSubtractive",
}

# --- authored material parameter vocabulary ---------------------------------
GLOBAL_PARAMS = [
    "k_alpha", "k_alpha_threshold", "k_transparent_alpha_threshold",
    "k_emissive_scale", "k_refractive_index", "k_refraction_amount",
    "k_depth_fade_distance", "k_shadow_fade_distance", "k_skirt_normal_blend_amt",
    "k_hardware_color", "k_bake_emissive_color", "k_bake_emissive_intensity",
    "k_irradiance_diffuse_scale", "k_irradiance_spec_scale",
    "k_irradiance_sg_sharpness_scale", "k_ao_volume_dynlight_scale",
    "k_baked_occlusion_dynlight_scale", "k_temporal_aa_scale",
    "k_subsurface_curvature_scale", "k_damage_height_scale", "k_wind_strength",
    "k_height_deform_amp", "k_height_deform_cycle_speed", "k_edge_amp",
    "k_edge_cycle_speed", "k_updown_amp", "k_updown_cycle_speed",
]

LAYER_PARAMS = [
    # transparency / opacity
    "alpha_map", "opacity_map", "opacity_tint_color", "shadow_alpha",
    "transparency_blend_alpha", "rim_alpha_intensity", "rim_opacity_intensity",
    "rim_opacity_tint_color",
    # emissive
    "emissive_map", "secondary_emissive_map", "emissive_intensity",
    "emissive_tint_color", "glow_color",
    "arbitrary_emissive_near_fade_x", "arbitrary_emissive_near_fade_y",
    "arbitrary_emissive_near_fade_z", "arbitrary_emissive_far_fade_x",
    "arbitrary_emissive_far_fade_y", "arbitrary_emissive_far_fade_z",
    # surface / colour
    "albedo_map", "diffuse_map", "specular_map", "normal_map", "ao_map",
    "cavity_map", "metallic_roughness_map", "height_map", "displacement_map",
    "detail_albedo_map", "detail_normal_map", "detail_ao_map",
    "detail_height_map", "back_lighting_map", "back_lighting_intensity",
    "back_lighting_tint_color", "subsurface_map", "thickness_mask",
    "additive_thin_map", "specular_shift_map", "specular_noise_map",
    "rim_map", "secondary_rim_map", "rim_ramp", "skirt_normal_blend_map",
    "flow_map", "flowmap_map", "weights_map", "masks",
    # composite (build-generated packed set)
    "composite_diffuse", "composite_normals", "composite_specular",
    "composite_components",
    # blending
    "blend_mask", "blend_height", "blend_mask_scale", "blend_mask_offset",
    "blend_scale", "blend_offset", "blend_fade", "blend_height_scale",
    "blend_height_offset", "blend_fade_scale_offset_map",
    "blend_offset_regions_map", "blend_scale_regions_map",
    # scalars
    "albedo_tint_color", "diffuse_tint_color", "specular_tint_color",
    "reflection_tint_color", "reflection_intensity", "reflection_attenuation",
    "roughness", "spec_intensity", "specular_gloss", "specular_spread",
    "specular_spread2", "ambient_specular_spread", "anisotropy", "fresnel",
    "thickness", "normal_softness", "normal_bevel", "normal_fade",
    "normal_map_intensity", "ao_lighting_scale", "grid_opacity", "grid_color",
    "grid_line_width", "grid_unit_size", "height_scale", "parallax_scale",
    "subsurface_amount", "subsurface_intensity", "subsurface_falloff",
    "subsurface_small_scale", "subsurface_shadow_scatter",
    "subsurface_shadow_penumbra", "velvet_fresnel", "velvet_front_spec",
    "rim_intensity", "rim_light_intensity", "rim_min_intensity",
    "rim_max_intensity", "rim_pow", "rim_tint_color", "rim_albedo_intensity",
    "rim_albedo_tint_color", "wrinkle_map_intensity", "mip_fade_start",
    "mip_fade_end", "fade", "scale", "pooling",
    # uv transforms
    "uvoffsetu", "uvoffsetv", "uvscaleu", "uvscalev", "uvscalepivotu",
    "uvscalepivotv", "uvblendamount", "detail_uvoffsetu", "detail_uvoffsetv",
    "detail_uvscaleu", "detail_uvscalev",
    # blend-alpha weights
    "normal_blend_alpha", "diff_albedo_blend_alpha", "spec_albedo_blend_alpha",
    "roughness_blend_alpha", "lighting_blend_alpha", "subsurface_blend_alpha",
    "backlighting_blend_alpha", "brdf_blend_alpha",
    # flipbook / flow
    "flipbook_index", "flipbook_offset", "flipbook_speed",
    "flipbook_phaseoffsetu", "flipbook_phaseoffsetv",
    "flowmap_speed", "flowmap_begin", "flowmap_end", "flowmap_offset",
]

SUFFIXED = ["_uoffset", "_voffset", "_uscale", "_vscale", "_intensity", "_scale",
            "_offset"]


def build_name_table(max_layer: int = 8) -> dict[int, str]:
    """hash -> authored parameter name (every entry a verified preimage)."""
    table: dict[int, str] = {}

    def add(name: str) -> None:
        table.setdefault(msc.symbol64(name), name)

    for n in GLOBAL_PARAMS:
        add(n)
    for L in range(max_layer):
        for n in LAYER_PARAMS:
            add(f"layer{L}_{n}")
            for suf in SUFFIXED:
                add(f"layer{L}_{n}{suf}")
    # material-level aux inputs seen in shipped auxillaryinputs tables
    for n in ("cutting_cut_decal", "cutting_scorch_decal"):
        add(n)
    return table


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

    # non-default transparency / emissive
    interesting = [r for r in rows
                   if r["alpha"] != 1.0 or r["is_emissive"] or r["blend_mode"] != 0
                   or r["mattype"] not in (0, 1)]
    print(f"\nnon-opaque / emissive materials: {len(interesting)} of {len(rows)}")
    for r in sorted(interesting, key=lambda x: (x["mattype"], x["blend_mode"]))[:40]:
        em = ",".join(f"{v:.3f}" for v in r["emissive_color"])
        print(f"  {r['hash']}  mattype={r['mattype']:>2} "
              f"{MATTYPE_NAMES.get(r['mattype'], '?'):<22} "
              f"blend={r['blend_mode']:>2} {BLENDMODE_NAMES.get(r['blend_mode'], '?'):<22} "
              f"alpha={r['alpha']:.3f} bakeA={r['base_color_factor'][3]:.3f} "
              f"emissive=[{em}] ds={int(r['double_sided'])}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive", default="0703fd2acd5803e9")
    ap.add_argument("--tsv", type=Path, default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    name_table = build_name_table()
    print(f"name table: {len(name_table)} verified preimages")

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
