"""Add a brand-new model to a level: asset, placement, registry and collision.

    import evr_add_model as AM
    stage = AM.add_model(extract, level_hash, out_dir,
                         name="coriolis_gate_ring",
                         positions=[...], indices=[...],
                         transform=dict(pos=(0,0,24)))

This is the chain that new geometry needs. Each link was the reason the previous
attempt stopped at "re-place stock models only":

| link | resource | what it says |
|---|---|---|
| 1 | `CActorDataResource` | the actor exists |
| 2 | `CGInstancedModelResource` + GPU | what the model looks like |
| 3 | `CGVisibilityResource`, `CGTextureStreamingResource` | the per-asset sidecars an instanced model needs |
| 4 | `CTransformCR` | where it sits |
| 5 | `CStaticInstanceModelCR` | that it is drawn, and from which model |
| 6 | `CGStaticInstanceResource` | the asset binding the renderer resolves |
| 7 | `CPhysicsResource` | what the player collides with |
| 8 | `CBVHResource` | what the disc and lasers hit |
| 9 | `CArchiveResource` | that any of it is loaded at all |

Link 1 was the blocker and is solved in `evr_actor_table`; links 4-6 and 9 are
in `evr_level_grow`; 2, 3 and 7 come from the map editor's proven authoring
backend; 8 is `cbvhresource.graft_triangles`.

DONOR-STAMPED, NOT INVENTED
---------------------------
Every record this adds is cloned from an existing one of the same kind, with
only the fields whose meaning is established overwritten. Several of these
carry per-instance state nobody has decoded -- the 88-byte `CSIMCR` record body,
the 16-byte colour block, the `CTransformCR` component header, four per-actor
words in the actor table -- and cloning means none of it has to be guessed.

TWO THINGS DELIBERATELY LEFT ALONE
----------------------------------
* **No `meshdata` row.** Its key is not the entity but
  `MakeInstancedMeshBakeID(entity, "mesh-i")`, which is not decoded. That row
  only carries per-instance lightmap UVs, so skipping it costs the new model its
  baked GI and nothing else -- and writing a wrong key would corrupt the
  scatter for the instances that do have one.
* **No lightmap page.** Same reason. A new model is lit by the scene's dynamic
  lights, not by the bake.

BVH GRAFT SIZE
--------------
`graft_triangles` puts each graft under a single leaf, and a leaf addresses at
most 0x1f blocks -- 124 triangles. Bigger meshes are grafted in chunks, each
chunk becoming its own leaf under a new root.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
for _p in (str(_SCRIPTS), r"J:\EchoVR-Tools-Launcher\quest_combat_port\tools",
           r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evr_actor_table as ACTOR                        # noqa: E402
import evr_clone_level as CLONE                        # noqa: E402
import evr_level_grow as GROW                          # noqa: E402
import evr_level_reader as LR                          # noqa: E402
from resource_io import cbvhresource as BVHMOD         # noqa: E402

#: type directories, by the name the engine knows them under.
T = {
    "actor":      "347869ce492dc7da",   # CActorDataResourceWin10
    "transform":  "92abd3e1432bf5e8",   # CTransformCRWin10
    "csimcr":     "263584544abbd56c",   # CStaticInstanceModelCRWin10
    "cgsi":       "77c0bf257ca92aa0",   # CGStaticInstanceResourceWin10
    "cgsi_gpu":   "dd3ff9850e4eed35",   # CGStaticInstanceResourceWin10GPU
    "physics":    "b7d338793fa37832",   # CPhysicsResourceWin10
    "bvh":        "358b53c17825d154",   # CBVHResourceWin10
    "archive":    "2a41cf1c1d9e5d32",   # CArchiveResourceWin10
    "model":      "37102e4b27955a14",   # CGInstancedModelResourceWin10
    "visibility": "73d312a620da3824",   # CGVisibilityResourceWin10
    "texstream":  "c2434c5a99e139ce",   # CGTextureStreamingResourceWin10
}
#: a leaf addresses at most 0x1f Triangle4 blocks; two triangles per block.
BVH_CHUNK = 124
#: the CPhData header that `encode_cphysics_v2` prefixes to the body it makes.
CPHDATA_HEADER = 36


def _backend():
    from echo_editor.assemble.authoring import AuthoringBackend
    b = AuthoringBackend()
    b.activate()
    return b


def _read(root: Path, type_hash: str, level: str) -> bytes:
    for t in (type_hash, type_hash.lstrip("0")):
        for n in (level, level.lstrip("0")):
            p = root / t / n
            if p.is_file():
                return p.read_bytes()
    raise FileNotFoundError("%s/%s" % (type_hash, level))


def _write(out: Path, type_hash: str, name: str, blob: bytes) -> None:
    d = out / type_hash
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(blob)


def build_geometry(backend, positions, indices, normals=None, uvs=None):
    """A `MeshGeometry` from plain arrays, with flat normals when none given."""
    # `mesh_geometry_cls` is a property holding the CLASS, so it is not called
    MG = backend.mesh_geometry_cls
    g = MG()
    for i, p in enumerate(positions):
        n = normals[i] if normals else (0.0, 1.0, 0.0)
        uv = uvs[i] if uvs else (0.0, 0.0)
        g.add_vertex(p, n, (1.0, 0.0, 0.0, 1.0), uv)
    for k in range(0, len(indices), 3):
        g.add_tri(indices[k], indices[k + 1], indices[k + 2])
    return g


def world_triangles(positions, indices, xf):
    """`[(v0, v1, v2)]` in world space, for the collision and raycast bakes."""
    import numpy as np
    P = np.asarray(positions, dtype=np.float64)
    M = np.asarray(xf["rot3"], dtype=np.float64) if "rot3" in xf else np.eye(3)
    S = np.asarray(xf.get("scale", (1.0, 1.0, 1.0)), dtype=np.float64)
    t = np.asarray(xf.get("pos", (0.0, 0.0, 0.0)), dtype=np.float64)
    W = (M @ np.diag(S) @ P.T).T + t
    return [tuple(map(tuple, W[[indices[k], indices[k + 1], indices[k + 2]]]))
            for k in range(0, len(indices), 3)], W


def add_model(extract: Path, level: str, out: Path, *, name: str,
              positions, indices, transform, material_hash: int | None = None,
              shaderset_hash: int | None = None, normals=None, uvs=None,
              collision: bool = True, verbose: bool = True) -> dict:
    """Add one new model and one placement of it. Returns a report."""
    extract, out = Path(extract), Path(out)
    backend = _backend()
    rh = CLONE.rad_hash
    model_hash = int(rh(name), 16)
    entity = int(rh(name + "@actor"), 16)
    log = {"name": name, "model": "%016x" % model_hash, "entity": "%016x" % entity}

    # --- donors: an existing static instance of this level
    csim = _read(extract, T["csimcr"], level)
    pairs = LR.parse_static_instance_models(csim)
    if not pairs:
        raise SystemExit("level has no static instances to stamp from")
    donor_entity, donor_model_hex = pairs[0]
    donor_model = int(donor_model_hex, 16)
    log["donor_entity"] = "%016x" % donor_entity
    log["donor_model"] = donor_model_hex

    # --- 1. the actor registry
    actor_blob = _read(extract, T["actor"], level)
    at = ACTOR.ActorTable(actor_blob)
    di = at.pick_donor([e for e, _m in pairs])
    if di is None:
        raise SystemExit("no donor actor found")
    log["actor_index"] = at.add(entity, di, prefab=model_hash)
    _write(out, T["actor"], level, at.to_bytes())

    # --- 2/3. the model asset and its per-asset sidecars
    g = build_geometry(backend, positions, indices, normals, uvs)
    mat = material_hash if material_hash is not None else 0
    shd = shaderset_hash if shaderset_hash is not None else 0
    _write(out, T["model"], "%016x" % model_hash,
           backend.build_instanced_model_header(g, mat, shd))
    gpu_dir = "%016x" % (int(rh("CGInstancedModelResourceWin10GPU"), 16))
    _write(out, gpu_dir, "%016x" % model_hash, backend.encode_gpu_sidecar(g))
    _write(out, T["visibility"], "%016x" % model_hash,
           backend.build_mesh_asset_cgvisibility())
    _write(out, T["texstream"], "%016x" % model_hash,
           backend.build_mesh_asset_cgtexturestreaming())
    log["model_gpu_dir"] = gpu_dir
    log["verts"], log["tris"] = g.nv, g.ni // 3

    # --- 4. where it sits
    xf_blob = _read(extract, T["transform"], level)
    _write(out, T["transform"], level,
           GROW.add_transform_row(xf_blob, entity, donor_entity,
                                  pos=transform.get("pos", (0.0, 0.0, 0.0)),
                                  rot=transform.get("rot", (0.0, 0.0, 0.0, 1.0)),
                                  scale=transform.get("scale", (1.0, 1.0, 1.0))))

    # --- 5. that it is drawn
    _write(out, T["csimcr"], level,
           GROW.add_instance(csim, entity, model_hash, donor_entity))

    # --- 6. the asset binding the renderer resolves
    cgsi = _read(extract, T["cgsi"], level)
    grown, extra = GROW.add_instance_bindings(cgsi, entity, model_hash,
                                              donor_entity, donor_model,
                                              uvcount=None)
    _write(out, T["cgsi"], level, grown)
    gpu = _read(extract, T["cgsi_gpu"], level)
    _write(out, T["cgsi_gpu"], level, gpu + b"\x00" * extra)

    # --- 7/8. collision and raycast
    tris, world = world_triangles(positions, indices, transform)
    if collision:
        cm = backend.build_mesh_collision([tuple(p) for p in world], list(indices))
        encoded, _meta = backend.encode_cphysics_v2(cm)
        # `encode_cphysics_v2` returns a COMPLETE CPhysicsResource -- the 36-byte
        # CPhData header (bonecount, fromlevel, zone, bodies=1) and then the body.
        # `append_body_to_cphysics` wants the body alone; appending the whole
        # thing writes that header over the level's 36-byte trailer and the next
        # body walk then reads a geo flag of 0x1_00000000 instead of 1.
        body = encoded[CPHDATA_HEADER:]
        from echo_editor.assemble import edited_collision as EC
        phys = _read(extract, T["physics"], level)
        grown_phys = EC.append_body_to_cphysics(phys, body)
        if grown_phys is None:
            raise SystemExit("append_body_to_cphysics declined the body")
        _write(out, T["physics"], level, grown_phys)
        log["collision_bodies"] = "+1"
        log["collision_bytes"] = len(grown_phys) - len(phys)

        bvh = BVHMOD.read(_read(extract, T["bvh"], level))
        gid = max((int(t[3]) for p in bvh.primitives for t in p.triangles()
                   if t[3] != BVHMOD.LANE_INVALID), default=0) + 1
        for k in range(0, len(tris), BVH_CHUNK):
            BVHMOD.graft_triangles(bvh, tris[k:k + BVH_CHUNK], gid)
        _write(out, T["bvh"], level, BVHMOD.write(bvh))
        log["bvh_geom_id"] = gid
        log["bvh_grafts"] = (len(tris) + BVH_CHUNK - 1) // BVH_CHUNK

    # --- 9. that any of it is loaded
    arc = _read(extract, T["archive"], level)
    _write(out, T["archive"], level, GROW.add_manifest_entries(arc, [
        (int(T["model"], 16), model_hash),
        (int(T["visibility"], 16), model_hash),
        (int(T["texstream"], 16), model_hash)]))
    log["manifest_added"] = 3

    if verbose:
        for k, v in log.items():
            print("  %-18s %s" % (k, v))
    return log
