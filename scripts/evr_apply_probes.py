"""Add an Echo VR level's reflection probes to an extracted package.

Writes `<pkg>/probes/` (one BC6H cubemap DDS per probe) and the
`reflection_probes` section of `<pkg>/manifest.json`, plus a `probe_index` on
every mesh -- which is all `lone_echo_import.probe_builder` needs. That builder
already existed and was Lone Echo only, because nothing wrote the Echo VR half.

    python scripts/evr_apply_probes.py <package> <level> [--dir <extract>]

Why this works at all: `CGReflectionProbeResourceWin10` is Lone Echo's
`CGReflectionProbeResourceWin7` grammar UNCHANGED -- the same six `CTableA`
images and the same offsets parse every Win10 file with zero residual bytes on
all 32 archives. So `le_mesh.reflection_probe` is reused verbatim rather than
reimplemented. See docs/decoded/REFLECTION_PROBES.md.

Echo VR's probes are rigidly uniform where Lone Echo's varied: every one of the
379 shipped probes is 256^2, 9 mips, BC6H_UF16, 524,448 bytes, and `gpuoffsets`
is exactly `i * 524448` on all 25 populated levels.

⚠ `probe_index` is DERIVED here, not read. The engine stores it per mesh at
`CGMeshData.probeidx` (struct +0x50, record +0x68 in Echo VR), but the package
manifest's meshes carry no field that maps back to a mesh-list record. So this
applies the selection rule those shipped indices were measured to follow --
nearest probe point, overridden by a containing `SGProbeBox` -- which reproduces
the shipped value on 97.9% of 1,580 meshes (96.8% nearest, plus 18 of the
remaining 51 explained by a box). Each mesh records which rule fired.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from le_mesh import reflection_probe as RP           # noqa: E402

PROBE_TYPE = "CGReflectionProbeResourceWin10"
PROBE_GPU_TYPE = "CGReflectionProbeResourceWin10GPU"


def _hash(name: str) -> int:
    sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor")
    from echo_editor.assemble.authoring import AuthoringBackend
    backend = AuthoringBackend()
    backend.activate()
    return backend.rad_hash(name) & 0xFFFFFFFFFFFFFFFF


def _quat_rotate(q, v):
    x, y, z, w = q
    ux, uy, uz = x, y, z
    dot = ux * v[0] + uy * v[1] + uz * v[2]
    uu = ux * ux + uy * uy + uz * uz
    cx = uy * v[2] - uz * v[1]
    cy = uz * v[0] - ux * v[2]
    cz = ux * v[1] - uy * v[0]
    k = w * w - uu
    return (2 * dot * ux + k * v[0] + 2 * w * cx,
            2 * dot * uy + k * v[1] + 2 * w * cy,
            2 * dot * uz + k * v[2] + 2 * w * cz)


def select_probe(resource, point):
    """`(probe_index, rule)` for a world point.

    The measured rule: nearest `SGProbePoint`, unless the point falls inside an
    `SGProbeBox`, in which case that box names the probe. Smallest containing
    box wins, which is the usual priority for nested volumes.
    """
    pts = resource.points
    if not pts:
        return RP.PROBE_INDEX_NONE, "no probes"
    best, bestd = 0, None
    for i, p in enumerate(pts):
        d = sum((point[k] - p.point[k]) ** 2 for k in range(3))
        if bestd is None or d < bestd:
            best, bestd = i, d
    hits = []
    for box in resource.boxes:
        rel = tuple(point[k] - box.pos[k] for k in range(3))
        local = _quat_rotate(box.inv_rot, rel)
        if all(box.local_min[k] - 1e-3 <= local[k] <= box.local_max[k] + 1e-3
               for k in range(3)):
            vol = 1.0
            for k in range(3):
                vol *= max(1e-6, box.local_max[k] - box.local_min[k])
            hits.append((vol, box.probe_index))
    if hits:
        pick = min(hits)[1]
        if pick != best:
            return pick, "box"
    return best, "nearest"


def apply(pkg: Path, level: str, root: Path) -> dict:
    lv = level if len(level) == 16 and all(c in "0123456789abcdef" for c in level.lower()) \
        else "%016x" % _hash(level)
    meta = root / ("%016x" % _hash(PROBE_TYPE)) / lv
    gpu = root / ("%016x" % _hash(PROBE_GPU_TYPE)) / lv
    if not meta.is_file():
        raise SystemExit("no %s for %s" % (PROBE_TYPE, level))
    res = RP.parse_probe_resource(meta.read_bytes())
    if not res.n_probes:
        return {"probes": 0, "reason": "level ships the empty 344-byte stub"}

    payload = gpu.read_bytes() if gpu.is_file() else b""
    if len(payload) != res.gpumemsize:
        raise SystemExit("GPU payload is %d B, resource says %d"
                         % (len(payload), res.gpumemsize))

    outdir = pkg / RP.PROBE_DIR
    outdir.mkdir(parents=True, exist_ok=True)
    files = {}
    for i in range(res.n_probes):
        start, end = res.probe_gpu_range(i)
        dim = res.cube_dim(i)
        mips = res.mipcount(i)
        cube = RP.probe_file_name(i)
        # BOTH forms. The cube DDS is the faithful artefact; the STRIP is what
        # Blender can actually open, because `bpy.data.images.load` on a DX10
        # cubemap yields only a dim x 6*dim vertical strip of mip 0 -- so
        # `probe_builder` consumes the strip and the cube is the reference.
        strip = RP.probe_file_name(i, strip=True)
        (outdir / cube).write_bytes(
            RP.cube_dds_bytes(payload[start:end], dim, mips,
                              engine_format=res.texture_format))
        (outdir / strip).write_bytes(
            RP.cube_strip_bytes(payload[start:end], dim, mips,
                                engine_format=res.texture_format))
        # THE WHOLE CHAIN, one strip per level. The engine samples the mip
        # chain by roughness; exporting only mip 0 is why the wired reflection
        # was always the sharp one on every surface. All 9 levels are on disk.
        mip_files = []
        for m in range(mips):
            d = max(1, dim >> m)
            if d < 4:                       # below one BC block, nothing to see
                break
            nm = "%s_mip%d_strip.dds" % (cube[:-4], m)
            (outdir / nm).write_bytes(
                RP.cube_strip_bytes(payload[start:end], dim, mips,
                                    engine_format=res.texture_format, mip=m))
            mip_files.append("%s/%s" % (RP.PROBE_DIR, nm))
        files[i] = {"cube": "%s/%s" % (RP.PROBE_DIR, cube),
                    "strip": "%s/%s" % (RP.PROBE_DIR, strip),
                    "mips": mip_files}

    manifest_path = pkg / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[RP.MANIFEST_KEY] = RP.manifest_probe_section(
        res, files, resource_name=int(lv, 16), gpu_present=bool(payload))

    # ⛔ NOT from `mesh["aabb_min"]`. Every mesh in an Echo VR package manifest
    # carries an all-zero AABB, so binding off it put EVERY mesh on probe 0.
    # The meshes are instanced and the world position lives in the instance
    # blob: 11 floats per record -- [u32 tag, pos xyz, quat xyzw, scale xyz] --
    # whose translation columns span the arena exactly (x +-16, y -14..9,
    # z +-78) and share the probes' own space.
    rules = {}
    blob = pkg / (manifest.get("instances_blob") or "blobs/instances.bin")
    n_inst = int(manifest.get("num_instances") or 0)
    inst_probe = []
    if blob.is_file() and n_inst:
        raw = blob.read_bytes()
        stride = len(raw) // n_inst
        for i in range(n_inst):
            x, y, z = struct.unpack_from("<3f", raw, i * stride + 4)
            idx, rule = select_probe(res, (x, y, z))
            inst_probe.append(int(idx))
            rules[rule] = rules.get(rule, 0) + 1
    manifest[RP.MANIFEST_KEY]["instance_probe_index"] = inst_probe

    # Each mesh takes the probe most of its own instances chose, so the
    # per-object path the builder already has stays usable.
    for mesh in manifest.get("meshes") or []:
        off = int(mesh.get("instance_offset") or 0)
        cnt = int(mesh.get("instance_count") or 0)
        mine = inst_probe[off:off + cnt]
        if not mine:
            mesh["probe_index"] = RP.PROBE_INDEX_NONE
            continue
        counts = {}
        for v in mine:
            counts[v] = counts.get(v, 0) + 1
        mesh["probe_index"] = int(max(counts.items(), key=lambda kv: kv[1])[0])
        mesh["probe_instances"] = len(set(mine))
    manifest[RP.MANIFEST_KEY]["probe_index_source"] = (
        "derived: nearest SGProbePoint with SGProbeBox override "
        "(97.9% agreement with the shipped CGMeshData.probeidx)")
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return {"probes": res.n_probes, "boxes": len(res.boxes),
            "dim": res.cube_dim(0), "mips": res.mipcount(0),
            "format": res.format_name, "bytes": len(payload),
            "meshes": sum(rules.values()), "rules": rules}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("package")
    ap.add_argument("level")
    ap.add_argument("--dir", default=r"H:\pcvr-extracted")
    args = ap.parse_args(argv)
    out = apply(Path(args.package), args.level, Path(args.dir))
    if not out.get("probes"):
        print("  %s" % out.get("reason", "no probes"))
        return 0
    print("  %d probes, %d selection boxes, %d^2 x %d mips %s, %.1f MB of cubes"
          % (out["probes"], out["boxes"], out["dim"], out["mips"],
             out["format"], out["bytes"] / 1048576))
    print("  meshes bound: %d  %s" % (out["meshes"], out["rules"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
