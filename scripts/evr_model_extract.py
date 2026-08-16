"""Extract ONE model as a `.lescatter` package -- geometry, materials, skeleton.

The scene extractor is keyed on a level, so a standalone model (a character, a
prop, a chassis) had no route out except a hand-written Blender script. This
produces the same package format every other extraction does, so the add-on
imports it with no special case:

    manifest.json  materials.json  blobs/  textures/  [skeleton.json + weights]

    python scripts/evr_model_extract.py dac6537a23226325 --out J:/out
    python scripts/evr_model_extract.py --list --has-skeleton

Only LOD 0 is written. `decode.extract_mesh` returns every LOD of a part as its
own submesh, so importing all of them stacks five copies of the same geometry --
on the reference chassis that is 90,256 vertices instead of 38,329.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from evr_resource_types import (INSTANCED_MODEL_RESOURCE, MESH_LIST_RESOURCE,
                                normalise_hash)
import evr_apply_skeleton
from le_scene_extract import SceneInstance, SceneMesh, write_package


def list_models(root: Path, only_skeleton: bool = False) -> list:
    """Every model hash in the extract, optionally only rigged ones."""
    found = set()
    for kind in (INSTANCED_MODEL_RESOURCE, MESH_LIST_RESOURCE):
        directory = root / kind
        if directory.is_dir():
            found |= {normalise_hash(p.name.split(".")[0])
                      for p in directory.iterdir() if p.is_file()}
    if only_skeleton:
        found = {h for h in found if evr_apply_skeleton.has_skeleton(root, h)}
    return sorted(found)


def extract(root: Path, model_hash: str, out_dir: Path, *,
            max_texture: int = 0, texture_divisor: int = 1,
            all_lods: bool = False) -> dict | None:
    """Write one model's package. Returns a summary, or None if it has no mesh."""
    import evr_materials as EM
    import evr_scene_extract as E
    import evr_texture_streaming as TS

    model_hash = normalise_hash(model_hash)
    results, path_label = E._decode_model_cached(root, model_hash)
    if not results:
        return None

    if not all_lods:
        lod = E._group_submeshes_by_lod(results)
        keep = [i for i, (_g, level, _n) in enumerate(lod) if level == 0]
        results = [results[i] for i in keep]

    package = out_dir / model_hash
    package.mkdir(parents=True, exist_ok=True)
    textures = package / "textures"
    textures.mkdir(exist_ok=True)

    # Materials: give the resolver the model's OWN texture inventory and pack
    # file, exactly as the scene path does. Without them it cannot see the
    # model's high-resolution maps at all.
    ctx = EM.build_context(root, [model_hash])
    E._MAX_TEXTURE[0] = int(max_texture or 0)
    ctx.max_texture = int(max_texture or 0)
    ctx.texture_divisor = max(1, int(texture_divisor or 1))
    stream = TS.load_for_model(root, model_hash)
    model_textures = [str(t).lower() for t in stream.textures] if stream else None
    packfile = stream.packfilename if stream else None

    counts = [len(r[0]) for r in results]
    palette = EM.materials_for_model(root, model_hash, ctx.material_hashes,
                                     vertex_counts=counts) or []
    specs = []
    for i, material_hash in enumerate(palette[:len(results)]):
        spec = EM.build_spec(ctx, material_hash, out_dir=textures, rank=i,
                             model_textures=model_textures,
                             packfile_hash=packfile)
        if spec:
            specs.append({"matidx": len(specs), "shdidx": 0, "spec": spec})

    meshes, instances = [], []
    for i, result in enumerate(results):
        verts, faces = result[0], result[1]
        uvs = result[2] if len(result) > 2 else None
        if not verts or not faces:
            continue
        flat_pos = [c for v in verts for c in v]
        flat_idx = [c for f in faces for c in f]
        flat_uv = [c for uv in uvs for c in uv] if uvs and len(uvs) == len(verts) else None
        matidx = min(i, max(len(specs) - 1, 0))
        meshes.append(SceneMesh(
            index=len(meshes), name_hash=int(model_hash, 16),
            matidx=matidx, shdidx=0,
            aabb_min=(0, 0, 0), aabb_max=(0, 0, 0),
            instance_offset=len(instances), instance_count=1,
            positions=flat_pos, indices=flat_idx,
            normals=None, uv0=flat_uv, uv1=None,
            draws=[{"matidx": matidx, "shdidx": 0,
                    "idx_start": 0, "idx_count": len(flat_idx)}]))
        instances.append(SceneInstance(
            mesh_index=len(meshes) - 1, translation=(0.0, 0.0, 0.0),
            rotation=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0)))

    if not meshes:
        return None
    write_package(package, model_hash, meshes, instances)

    if specs:
        (package / "materials.json").write_text(json.dumps(
            {"format": "le_materials", "version": 2, "master": model_hash,
             "source": "evr_model_extract", "textures_subdir": "textures",
             "materials": specs,
             "diagnostics": {"model": model_hash, "decode_path": path_label}},
            indent=1), encoding="utf-8")

    skeleton = evr_apply_skeleton.build(package, root, model_hash)
    return {"package": package, "meshes": len(meshes),
            "verts": sum(len(r[0]) for r in results),
            "materials": len(specs),
            "textures": len(list(textures.glob("*.dds"))),
            "skeleton": skeleton}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("model", nargs="?", help="model hash")
    ap.add_argument("--dir", default="H:/pcvr-extracted")
    ap.add_argument("--out", default="J:/EchoVRModels/models")
    ap.add_argument("--list", action="store_true", help="list model hashes")
    ap.add_argument("--has-skeleton", action="store_true",
                    help="with --list, only models that carry an armature")
    ap.add_argument("--max-texture", type=int, default=0)
    ap.add_argument("--texture-divisor", type=int, default=1)
    ap.add_argument("--all-lods", action="store_true")
    args = ap.parse_args(argv)

    root = Path(args.dir)
    if args.list:
        for h in list_models(root, args.has_skeleton):
            print(h)
        return 0
    if not args.model:
        ap.error("a model hash is required (or use --list)")

    summary = extract(root, args.model, Path(args.out),
                      max_texture=args.max_texture,
                      texture_divisor=args.texture_divisor,
                      all_lods=args.all_lods)
    if summary is None:
        print(f"{args.model}: no geometry decoded")
        return 1
    skel = summary["skeleton"]
    print(f"{args.model}: {summary['meshes']} mesh(es), {summary['verts']:,} verts, "
          f"{summary['materials']} material(s), {summary['textures']} texture(s)"
          + (f", {skel['bones']}/{skel['of']} bones" if skel else ", no skeleton"))
    print(f"-> {summary['package']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
