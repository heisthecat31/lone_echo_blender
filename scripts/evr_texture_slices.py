"""Write a texture ARRAY out as one DDS per slice.

## Why this is a separate tool

Some art ships as a texture array and nothing in the level data says which
slice an object uses. `a240a4bc051b2f23` is 37 slices of 2048x1024 BC1_SRGB and
every slice is a DIFFERENT lobby poster -- the movement tutorials, the Atlas
Intelligence board, the player-report notice, the social links. The lobby's
poster meshes all share one material that does not name the array at all, so
every poster imports showing the same image.

The array is bound by NO material and named in NO model's texture list, so the
scene extractor never reaches it: `extract_textures` only walks textures a
material asks for. Slicing therefore cannot be automatic here -- somebody has to
say which texture to slice. That is what this is for.

## What it does and does not do

Slicing is a RE-HEADER, not a decode (`evr_lightmap.single_slice_dds`): the
DX10 header is rewritten to `arraySize = 1` and the slice's mip chain is copied
through untouched, so Blender still gets compressed data and does its own
decode.

⚠ It does NOT assign slices to objects. Nothing on disk links a model to a
slice index -- the choice is made at runtime -- so the output is an inventory to
pick from, not a binding.

    python scripts/evr_texture_slices.py a240a4bc051b2f23 \
        --dir H:/pcvr-extracted \
        --out J:/EchoVRModels_half/Scenes_Full/mpl_lobby_b2/textures
"""

from __future__ import annotations

import sys
from pathlib import Path

import evr_paths

evr_paths.install_import_paths()

import evr_texture_resource as evr_tex           # noqa: E402
import evr_materials as evr_materials            # noqa: E402


def slice_texture(root: Path, texture_hash: str, out_dir: Path) -> dict:
    """Write every slice of `texture_hash` into `out_dir`."""
    resource = evr_tex.load(root, texture_hash)
    if resource is None:
        return {"error": "texture %s is not in the extract" % texture_hash}
    count = int(resource.arraysize or 1)
    if count <= 1:
        return {"error": "%s has arraysize %d -- not an array" % (texture_hash, count)}

    blob, note = evr_tex.rebuild_dds(root, texture_hash)
    if not blob:
        return {"error": "could not rebuild %s: %s" % (texture_hash, note)}

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    names = evr_materials.write_array_slices(root, texture_hash, blob, out_dir)
    return {
        "texture": texture_hash,
        "declared_slices": count,
        "written": len(names),
        "size": "%dx%d" % (resource.maxwidth or 0, resource.maxheight or 0),
        "format": resource.format_name,
        "files": names,
        "dir": str(out_dir),
    }


def main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Write a texture array out as one DDS per slice.")
    parser.add_argument("texture", help="texture hash")
    parser.add_argument("--dir", required=True, help="flat extract root")
    parser.add_argument("--out", required=True, help="where to write the slices")
    args = parser.parse_args(argv)

    result = slice_texture(Path(args.dir), args.texture.lower(), Path(args.out))
    if "error" in result:
        print(result["error"])
        return 1
    print("%s  %s %s  %d of %d slices -> %s"
          % (result["texture"], result["size"], result["format"],
             result["written"], result["declared_slices"], result["dir"]))
    for name in result["files"][:6]:
        print("   %s" % name)
    if len(result["files"]) > 6:
        print("   ... %d more" % (len(result["files"]) - 6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
