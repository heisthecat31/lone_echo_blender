"""Load a stock `.lescatter` package into Blender so it can be looked at.

    blender -b --factory-startup --python blender_tool/view_arena.py -- \
        --pkg J:/EchoVRModels_half/scenes/mpl_arena_a --out DIR [--blend F]

Reads the package blobs directly (manifest + `m<n>_pos.bin` / `m<n>_idx.bin` +
`instances.bin`) rather than going through the addon, because all that is wanted
here is the shape of the map -- no materials, lightmaps or tint wiring.

Placement is `scatter_reader.compose_instance_matrix`, the importer's own maths,
so what comes out sits exactly where the addon would put it.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import bpy
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parent / "addon" / "lone_echo_import"))
import scatter_reader as SR                                   # noqa: E402


def load(pkg: Path, cap=0):
    man = json.loads((pkg / "manifest.json").read_text())
    meshes = {m["index"]: m for m in man["meshes"]}
    blob = (pkg / "blobs" / "instances.bin").read_bytes()
    basis = SR.basis_matrix(True)

    datablocks = {}
    for mi, m in meshes.items():
        pos = (pkg / m["positions"]).read_bytes()
        verts = [struct.unpack_from("<3f", pos, k * 12) for k in range(len(pos) // 12)]
        idx = (pkg / m["indices"]).read_bytes()
        n = m["nindices"]
        fmt = "<%dH" % n if len(idx) == n * 2 else "<%dI" % n
        ind = struct.unpack(fmt, idx[:struct.calcsize(fmt)])
        faces = [ind[k:k + 3] for k in range(0, n - 2, 3)]
        faces = [f for f in faces if len(set(f)) == 3]
        me = bpy.data.meshes.new("m%d" % mi)
        me.from_pydata(verts, [], faces)
        me.validate(verbose=False)
        me.update()
        datablocks[mi] = me

    coll = bpy.data.collections.new("arena")
    bpy.context.scene.collection.children.link(coll)
    n_inst = man["num_instances"] if not cap else min(cap, man["num_instances"])
    for i in range(n_inst):
        r = struct.unpack_from("<I10f", blob, i * 44)
        me = datablocks.get(r[0])
        if me is None:
            continue
        ob = bpy.data.objects.new("i%d" % i, me)
        ob.matrix_world = Matrix(SR.compose_instance_matrix(
            list(r[1:4]), list(r[4:8]), list(r[8:11]), basis=basis))
        coll.objects.link(ob)
    print("loaded %d meshes, %d instances" % (len(datablocks), n_inst))
    return coll


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--pkg", required=True)
    ap.add_argument("--blend", default="")
    ap.add_argument("--cap", type=int, default=0)
    a = ap.parse_args(argv)

    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    load(Path(a.pkg), a.cap)
    if a.blend:
        bpy.ops.wm.save_as_mainfile(filepath=a.blend)
        print("saved", a.blend)
    return 0


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    sys.exit(main(argv))
