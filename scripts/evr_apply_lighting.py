"""Add baked lightmaps and placed lights to an extracted Echo VR package.

Writes `<pkg>/lightmaps/` (one diffuse-irradiance PNG per lightmap page) and
`<pkg>/lightmaps.json` binding meshes to pages and listing every placed light,
so the importer needs no knowledge of the resource formats.

    python scripts/evr_apply_lighting.py <package> <level> [--dir <extract>]

See `docs/EVR_LIGHTING.md`. The lights are the real `SGLightParams` records
(colour, type, intensity, range, direction) -- see `evr_lights.py`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evr_lightmap as evr_lm
import evr_lights as evr_li
import evr_scene_extract as extractor
from evr_resource_types import (INSTANCED_MODEL_RESOURCE, MESH_LIST_RESOURCE,
                                resource_path)


def apply(pkg: Path, level: str, root: Path) -> dict:
    extractor._LAST_ROOT[0] = str(root)
    group = extractor.sublevels_of(extractor.resolve_level(level))

    manifest = json.loads((pkg / "manifest.json").read_text(encoding="utf-8"))
    by_hash = defaultdict(list)
    for mesh in manifest["meshes"]:
        by_hash[mesh["name_hash"]].append(mesh["index"])

    out_dir = pkg / "lightmaps"
    out_dir.mkdir(exist_ok=True)

    images: dict = {}
    gains: dict = {}
    bindings: dict = {}
    lights: list = []

    for member in group:
        label = extractor.level_label(member).split(" (")[0]

        for light in evr_li.level_lights(root, member):
            lights.append({
                "level": label,
                "type": light.type_name,
                "position": [round(v, 5) for v in light.position],
                "direction": [round(v, 6) for v in light.direction],
                "color": [round(v, 6) for v in light.color],
                "intensity": round(light.intensity, 5),
                "range": [round(v, 4) for v in light.range],
                "shades_dynamic": light.shades_dynamic,
                "owner": light.owner,
            })

        info = evr_lm.level_lightmap(root, member)
        if not info:
            print(f"  {label}: no lightmap")
            continue

        # EVERY model in the package can be lightmapped, not just the level's
        # own mesh: mpl_arena_a's base mesh is 9 unlit submeshes because the
        # arena is built almost entirely from actor-placed prop models, each
        # carrying its own CGMeshData. So walk the whole model set, and take
        # only those whose lightmapindex matches THIS level's bound row --
        # a prop shared between levels would otherwise be lit from the wrong
        # atlas.
        slices = None
        lit_total = model_count = bound = 0
        for model_hash, indices in sorted(by_hash.items()):
            primary = resource_path(root, MESH_LIST_RESOURCE, model_hash) \
                or resource_path(root, INSTANCED_MODEL_RESOURCE, model_hash)
            if primary is None:
                continue
            results, _note = extractor._decode_model_cached(root, model_hash)
            submeshes = len(results or [])
            if not submeshes:
                continue
            binds = evr_lm.mesh_lightmap_bindings(primary.read_bytes(), submeshes)
            lit = [(i, row, page) for i, (row, page) in enumerate(binds)
                   if page is not None and row == info["row"]]
            if not lit:
                continue
            model_count += 1
            lit_total += len(lit)

            if slices is None:
                slices, width, height = evr_lm.decode_ambient(root, info["ambient"])
            for _i, _row, page in lit:
                key = f"{member}_p{page}"
                if key not in images and page < info["pages"]:
                    images[key] = _write_page(out_dir, key, slices, page, info,
                                              width, height, gains)
            for submesh, _row, page in lit:
                key = f"{member}_p{page}"
                if submesh < len(indices) and key in images:
                    bindings[str(indices[submesh])] = {
                        "image": images[key], "level": label, "page": page,
                    }
                    bound += 1
        print(f"  {label}: {info['basis']} row {info['row']}, {info['pages']} "
              f"page(s), {lit_total} lit submeshes across {model_count} "
              f"model(s), {bound} bound")

    # --- static instances -------------------------------------------------
    # These do NOT bind through CGMeshData (mpl_arena_a's prop models are all
    # unlit there). Their lightmap ROW is per model and their PAGE is per
    # instance, both in CGSI -- and because instances of one mesh occupy
    # DIFFERENT atlas regions, their UVs are per instance too, in the CGSI GPU
    # sibling rather than the vertex stream.
    sh_pages: dict = {}
    instance_uv, instance_pages = _static_instances(pkg, root, images, out_dir,
                                                    gains, sh_pages)

    payload = {
        "format": "evr_lighting",
        "version": 1,
        "note": ("lightmaps: diffuse irradiance per page, already collapsed "
                 "from the SG5/SH4 basis with the shader's own weights -- "
                 "multiply base colour by it, sampled with the lightmap UV. "
                 "lights: SGLightParams from CGSceneResource section 1 -- "
                 "type, colour, intensity, range and direction. Only SUN "
                 "(type 2) lights shade dynamic objects in-engine; POINT/SPOT "
                 "are the static-bake rig."),
        "dir": "lightmaps",
        "images": images,
        # Divisor applied to each atlas so it fits 8-bit: the physical
        # irradiance is `stored * gain`. Exposure, not decoded data.
        "gains": gains,
        "meshes": bindings,
        # SH4 pages kept as RAW coefficients: {page_key: {basis, slices[4]}}.
        # slice 0 is the DC term; 1-3 are packed to [0,1] and must be unpacked
        # as `c*2-1` then rescaled by `dc*2` before evaluating irradiance with
        # the surface normal (core/shaders/materials/material_base_ps.hlsl:1129).
        "sh_pages": sh_pages,
        "lights": lights,
        # Per-instance lightmap: {package instance index: {"image", "uv_offset",
        # "uv_count"}} into `instance_uv_blob` (float32 u,v pairs).
        "instances": instance_pages,
        "instance_uv_blob": "lightmaps/instance_uvs.bin" if instance_pages else None,
    }
    if instance_pages:
        (out_dir / "instance_uvs.bin").write_bytes(instance_uv)
    (pkg / "lightmaps.json").write_text(json.dumps(payload, indent=1),
                                        encoding="utf-8")
    print(f"\n{len(images)} atlas(es), {len(bindings)} meshes bound, "
          f"{len(lights)} lights -> {pkg / 'lightmaps.json'}")
    return payload


def _static_instances(pkg: Path, root: Path, images: dict, out_dir: Path,
                      gains: dict, sh_pages: dict) -> tuple:
    """`(uv_blob, {instance_index: binding})` for static-instanced geometry.

    Reads `static_entities.json` -- written by the extractor because flattening
    instances into the package otherwise loses the entity, which is the key to
    everything here.
    """
    import struct

    from evr_resource_types import STATIC_RESOURCE

    sidecar = pkg / "static_entities.json"
    if not sidecar.is_file():
        return b"", {}
    entries = json.loads(sidecar.read_text(encoding="utf-8"))["instances"]

    cache: dict = {}
    blob = bytearray()
    out: dict = {}
    for index, entry in enumerate(entries):
        if not entry:
            continue
        entity_hex, level, submesh = entry[0], entry[1], entry[2]
        # (lo, hi) is this package mesh's vertex slice of the engine mesh. The
        # engine stores ONE UV run per unsplit mesh, so a draw-split piece must
        # take its own slice or it reads another piece's atlas region -- which
        # renders as streaked garbage rather than as nothing.
        vert_lo, vert_hi = (entry[3], entry[4]) if len(entry) >= 5 else (0, 0)
        if level not in cache:
            cgsi_path = resource_path(root, STATIC_RESOURCE, level)
            gpu_path = resource_path(root, evr_lm.CGSI_GPU, level)
            cgsi = cgsi_path.read_bytes() if cgsi_path else None
            gpu = gpu_path.read_bytes() if gpu_path else None
            pages = evr_lm.static_instance_lightmaps(cgsi)[1] if cgsi else {}
            cache[level] = (cgsi, gpu, evr_lm.level_lightmap(root, level),
                            pages, [None, None])
        cgsi, gpu, info, page_by_entity, decoded = cache[level]
        if not cgsi or not gpu or not info:
            continue
        entity = int(entity_hex, 16)
        page = page_by_entity.get(entity)
        if page is None or page >= info["pages"]:
            continue
        key = f"{level}_p{page}"
        if key not in images:
            if decoded[0] is None:
                decoded[0] = evr_lm.decode_ambient(root, info["ambient"])
            slices, width, height = decoded[0]
            images[key] = _write_page(out_dir, key, slices, page, info,
                                      width, height, gains)
            # SH4 additionally keeps its four RAW coefficient slices, because
            # world-space SH cannot be collapsed ahead of shading.
            if info["basis"] == "SH4":
                if decoded[1] is None:
                    decoded[1] = evr_lm.ambient_dds(root, info["ambient"])[0]
                names = _write_sh4_page(out_dir, key, decoded[1], page,
                                        width, height)
                if len(names) == 4:
                    sh_pages[key] = {"basis": "SH4", "slices": names}
        uvs = evr_lm.static_instance_uvs(cgsi, gpu, entity, submesh)
        if not uvs:
            continue
        if vert_hi > vert_lo:
            if vert_hi > len(uvs):
                continue          # the slice is not inside this UV run
            uvs = uvs[vert_lo:vert_hi]
        # A UV run that is ALL (0,0) is the engine's "no bake" marker, not a
        # chart at the atlas origin. Wiring it anyway tints the whole object
        # with whatever single texel sits at (0,0). Four instances on
        # `mpl_arena_a`, two of them 43.7 m across.
        if not any(u or v for u, v in uvs):
            continue

        offset = len(blob) // 8
        for u, v in uvs:
            blob += struct.pack("<2f", u, v)
        out[str(index)] = {"image": images[key], "page": page,
                           "page_key": key,
                           "uv_offset": offset, "uv_count": len(uvs)}
    if out:
        print(f"  static instances: {len(out)} lit, "
              f"{len(blob) // 8} UV pairs")
    return bytes(blob), out


#: Slice order inside one SH4 page, from `core/textures/ambient_lightmap_sh*.radtex`
#: and confirmed by content: slice 0 is the DC term (mean 16/255, HDR), slices
#: 1-3 sit at 128/255 on lit texels -- exactly the zero point of the shader's
#: `*2-1` unpack.
SH4_SLICE_NAMES = ("sh0", "sh1", "sh2", "sh3")


def _write_sh4_page(out_dir, key, raw_dds, page, width, height) -> list:
    """Write one SH4 page as FOUR raw coefficient images.

    ⚠ Deliberately NOT collapsed to a single irradiance page. SH4 is baked in
    WORLD space (`material_base_ps.hlsl:1129`), so irradiance depends on the
    surface normal and cannot be resolved until shading time. The previous
    behaviour kept the DC term alone (`weights = [1, 0, 0, 0]`) and dropped all
    three directional coefficients, which is why lit surfaces came out flat and
    unlike the game.

    Stored raw and linear -- no exposure divisor, no sRGB curve. These are
    coefficients, not a picture; the consumer unpacks and evaluates them.
    """
    names = []
    for i in range(4):
        index = page * 4 + i
        chunk = evr_lm.single_slice_dds(raw_dds, index)
        if chunk is None:
            break
        name = f"{key}_{SH4_SLICE_NAMES[i]}.dds"
        (out_dir / name).write_bytes(chunk)
        names.append(name)
    return names


def _write_page(out_dir, key, slices, page, info, width, height,
                gains: dict = None) -> str:
    import numpy as np
    from PIL import Image

    img = evr_lm.page_irradiance(slices, page, info["basis"], width, height)

    # EXPOSURE, not decoding. These are irradiance/Pi values -- physically dark
    # (a lit arena texel averages ~0.03 linear) because the engine tonemaps and
    # exposes downstream. Multiplied into base colour raw, a DCC render comes
    # out near-black. So normalise the page onto [0,1] by its 99.5th percentile
    # and RECORD the divisor, which keeps the stored image viewable and the
    # original value recoverable as `stored * gain`.
    lit = img[img.max(axis=2) > 1e-4]
    peak = float(np.percentile(lit, 99.5)) if lit.size else 0.0
    gain = peak if peak > 1e-6 else 1.0
    if gains is not None:
        gains[key] = round(gain, 6)

    # TRUE sRGB transfer, not a 2.2 power. Blender decodes an image tagged
    # "sRGB" with the piecewise sRGB EOTF; encoding with a plain 2.2 gamma is
    # close but not its inverse, and the mismatch is worst in the darks, which
    # is most of a lightmap. Using the real curve makes the round-trip exact.
    norm = np.clip(img / gain, 0.0, 1.0)
    srgb = np.where(norm <= 0.0031308,
                    norm * 12.92,
                    1.055 * np.power(norm, 1.0 / 2.4) - 0.055)
    name = f"{key}.png"
    Image.fromarray((srgb * 255.0 + 0.5).astype(np.uint8)).save(out_dir / name)
    return name


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("package")
    ap.add_argument("level")
    ap.add_argument("--dir", default=None,
                    help="flat game extract (or set EVR_EXTRACT_DIR)")
    args = ap.parse_args()
    # `Path(None)` raises TypeError, which is what happened every time the app
    # ran this step without --dir: the crash was swallowed by its capture_output
    # and every package it produced came out unlit. Resolve through evr_paths so
    # EVR_EXTRACT_DIR works and a missing root reports itself.
    import evr_paths
    root = evr_paths.require_extract(args.dir)
    apply(Path(args.package), args.level, root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
