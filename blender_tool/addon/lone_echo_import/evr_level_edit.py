"""Edit a stock Echo VR level in Blender and export it as a NEW level.

Adds an **EVR Level** tab to the 3D view's N panel. Import a level with the
`.lescatter` importer, move / rotate / scale / duplicate its objects, then
export the whole thing under a new name straight into the packer's staging
folder.

WHAT THE EXPORT ACTUALLY DOES
-----------------------------
1. Clones every one of the level's ~90 per-level resources under
   `rad_hash(new name)`, repointing the four files that carry a self-reference
   (`evr_clone_level`).
2. For every object whose transform you changed, applies that same transform to
   its **render placement, its player collision and its raycast geometry**
   together (`evr_geo_move`), rebuilding the per-edge features and the kDOP
   tree so the collision matches what you see.

   A ROTATE also turns the body's `+400` collision direction table. Those are
   unit face normals: a move leaves them alone, but a turn has to carry them
   with the geometry or the collision keeps its old headings while the vertices
   sit somewhere else. An entry a STATIC triangle also produces has to keep
   serving that triangle, so it is left behind and reported instead -- see
   `evr_geo_move` for why the table cannot simply grow.

Nothing else is touched, so tunnels, launchers, goals, UI, scripts, audio and
lighting behave exactly as they do in the stock map.

HOW A BLENDER OBJECT MAPS BACK TO THE LEVEL
-------------------------------------------
The importer stamps `le_instance_index` on every object it creates -- the row
index into the package's `blobs/instances.bin`. `static_entities.json` turns
that into the level's static-instance ENTITY, which is the join key into
`CTransformCR`. The baseline placement comes from the same record, so the edit
is measured as `current` against `stock` rather than assumed.

Placement math is the importer's own: `world = B @ (T @ R @ S)` with `B` the
Y-up -> Z-up basis, so the game-space matrix is `B_inv @ object.matrix_world`
and the delta that has to reach the level is
`game_now @ game_stock_inverse`.

MIRRORING
---------
The arena is symmetric under a 180-degree turn about y (`x, z -> -x, -z`), and
that is the symmetry used to keep both ends identical: edit one end, press
**Sync Mirror** (or leave **Auto** on), and the matching object at the other end
gets the mirrored transform `P M P` with `P = diag(-1, 1, -1)`. Partners are
found from the stock placement -- an instance at `(-x, y, -z)` built from the
same mesh, or from that mesh's opposite-half twin, because the cook ships a
separate model per half of the level.

WHICH COLLISION MOVES WITH AN OBJECT
------------------------------------
Not "whatever fits a box". A box gets this wrong both ways: instance 1207 has a
32 x 17 m render AABB, so containment swept up 28 components that belong to the
props standing in front of it, while a hull that hugs its own object but
overhangs the render bounds by 4 cm was rejected as "straddling" and left
behind.

The exporter instead asks who OWNS each component -- which entity's stock render
surface actually runs along it (`evr_geo_move.Owners`) -- and moves it when the
edited entities are the only owners, however far it reaches past the box.
Ownership is judged per ENTITY, not per instance, because the cook ships one
logical object as several models: instances 2777..2781 are all entity
`daa8136c`, and the hull they share is covered 0.77 by 2781 alone but 1.00 by
the entity.

The signal is close to binary in practice -- a component is typically covered
0.8-1.0 by its owner and 0.0-0.29 by everything else -- so the 50% threshold is
not doing delicate work. Two cases still hold back:

* a component **two entities both own** moves only when both are in the edit,
  which is the cluster rule and keeps a partial selection from tearing a shared
  mesh; select the whole object and it moves.
* a component **nobody claims** -- small blockers in body 2 with no render
  surface against them -- falls back to the old "entirely inside the box" test,
  the conservative reading when ownership is unknown.

Both are counted and reported rather than passed over silently.

ADDING NEW OBJECTS -- NOT YET
-----------------------------
Duplicating an object, or bringing in a new mesh, needs more than a transform.
The level has to gain a `CTransformCR` row (176 bytes at `56 + 176k`, entity at
`+0x08`, quat `+0x20`, position `+0x30`, scale `+0x3c` -- straightforward
growth), a `CStaticInstanceModelCR` record, an entry in
`CGStaticInstanceResource`, and a collision body (the map editor's
`build_collision_body` + `append_body_to_cphysics` is a proven path, and
`cbvhresource.graft_triangles` covers the raycast side).

The link that is NOT established is the actor id: a new instance needs one, and
`CActorDataResource` is a keyed structure -- 1,553 pairs, 167 groups, a 5,836
word prefab payload and four bitmaps -- whose growth has not been worked out.
Shipping an instance whose actor the level does not know is how you get a map
that will not load, so the exporter REPORTS new objects and exports everything
else rather than guessing. **Fail on New Objects** turns that warning into a
hard stop if you would rather not ship a partial edit by accident.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty,
                       PointerProperty, StringProperty)
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Matrix

try:
    from . import scatter_reader
except ImportError:                                   # pragma: no cover
    import scatter_reader

#: default location of the repo's `scripts/` folder, which holds the level
#: tools the export drives. Overridable in the panel.
DEFAULT_TOOLS = r"J:\EchoVR-Tools-Launcher\lone_echo_blender\scripts"
DEFAULT_EXTRACT = r"H:\pcvr-extracted"
DEFAULT_EXPORT = (r"C:\Oculus\Games\Software\Software\ready-at-dawn-echo-arena"
                  r"\bin\win10\Tools\Tools\Settings\input-pcvr")

#: a transform counts as edited past this much movement / this much change in
#: any matrix element. Blender round-trips floats, so an exact compare would
#: flag every object in the scene.
EPS_POS = 1e-4
EPS_LIN = 1e-5


# ── tool loading ────────────────────────────────────────────────────────────
def _load_tools(tools_dir: str):
    """Import the level tools, adding `tools_dir` to `sys.path` first."""
    d = str(Path(bpy.path.abspath(tools_dir)))
    if d not in sys.path:
        sys.path.insert(0, d)
    import importlib
    mods = {}
    for name in ("evr_clone_level", "evr_geo_move"):
        m = importlib.import_module(name)
        importlib.reload(m)
        mods[name] = m
    return mods["evr_clone_level"], mods["evr_geo_move"]


# ── package access ──────────────────────────────────────────────────────────
class Baseline:
    """The stock placement of every static instance in the imported package."""

    def __init__(self, package: Path):
        self.dir = Path(package)
        man = json.loads((self.dir / "manifest.json").read_text())
        self.master = man.get("master", "")
        blob = (self.dir / "blobs" / "instances.bin").read_bytes()
        ents = json.loads((self.dir / "static_entities.json").read_text())["instances"]
        self.mesh_of = {m["index"]: m["name_hash"] for m in man["meshes"]}
        self.mesh_aabb = {m["index"]: (m.get("aabb_min"), m.get("aabb_max"))
                          for m in man["meshes"]}
        self.mesh_rec = {m["index"]: m for m in man["meshes"]}
        self._mesh_cache = {}
        self.rec = {}
        self.entity_of = {}
        for i in range(man["num_instances"]):
            r = struct.unpack_from("<I10f", blob, i * 44)
            self.rec[i] = {"mesh": r[0], "pos": list(r[1:4]),
                           "rot": list(r[4:8]), "scale": list(r[8:11])}
            e = ents[i]
            if e:
                self.entity_of[i] = e[0]

    def mesh_positions(self, mesh_index: int):
        """A mesh's local vertices, straight from the package blob.

        Read from `blobs/m<n>_pos.bin` rather than from the Blender datablock,
        so ownership does not depend on what happens to be imported: a capped
        or partly hidden import would otherwise leave instances out of the
        index and make their collision look unclaimed.
        """
        got = self._mesh_cache.get(mesh_index)
        if got is None:
            import numpy as np
            m = self.mesh_rec.get(mesh_index)
            if m is None or not m.get("positions"):
                got = None
            else:
                p = self.dir / m["positions"]
                got = (np.frombuffer(p.read_bytes(), dtype="<f4").reshape(-1, 3)
                       .astype("float64") if p.is_file() else None)
            self._mesh_cache[mesh_index] = got
        return got

    def stock_world_verts(self, i: int):
        """Instance `i`'s render vertices at their STOCK placement, game space --
        the geometry the level's collision was cooked against."""
        import numpy as np
        v = self.mesh_positions(self.rec[i]["mesh"])
        if v is None or not len(v):
            return None
        m = self.game_matrix(i)
        R = np.array([[m[r][c] for c in range(3)] for r in range(3)])
        t = np.array([m[0][3], m[1][3], m[2][3]])
        return (R @ v.T).T + t

    def owner_items(self):
        """`(entity, stock world vertices)` for every static instance."""
        out = []
        for i, ent in self.entity_of.items():
            w = self.stock_world_verts(i)
            if w is not None and len(w):
                out.append((ent, w))
        return out

    def game_matrix(self, i: int) -> Matrix:
        """`T @ R @ S` for instance `i`, in game space.

        `basis_matrix(False)` is the importer's own identity passthrough, so
        this is exactly the placement the addon composes minus the Y-up -> Z-up
        change -- the same tested math, not a re-derivation.
        """
        r = self.rec[i]
        rows = scatter_reader.compose_instance_matrix(
            r["pos"], r["rot"], r["scale"], basis=scatter_reader.basis_matrix(False))
        return Matrix(rows)

    def world_aabb(self, i: int, objects=None):
        """Stock world-space AABB of instance `i`, in game space.

        Bounds come from the imported MESH DATA, not from the manifest: this
        package's per-mesh `aabb_min`/`aabb_max` are all zero (431 of 431), so
        trusting them collapses the box to a point and the collision search
        finds nothing. The mesh datablock is stored in game space -- the
        importer applies the basis on the object matrix, never on the mesh --
        so the local corners only need the stock instance matrix.
        """
        pts = []
        m = self.game_matrix(i)
        for ob in (objects or ()):
            data = getattr(ob, "data", None)
            if data is None or not getattr(data, "vertices", None):
                continue
            xs = [v.co for v in data.vertices]
            lo = [min(c[k] for c in xs) for k in range(3)]
            hi = [max(c[k] for c in xs) for k in range(3)]
            for k in range(8):
                pts.append(m @ _vec(((lo[0] if k & 1 else hi[0]),
                                     (lo[1] if k & 2 else hi[1]),
                                     (lo[2] if k & 4 else hi[2]))))
        if not pts:                                   # fall back to the manifest
            lo, hi = self.mesh_aabb.get(self.rec[i]["mesh"], (None, None))
            if not lo or not hi or (lo == hi):
                return None
            for k in range(8):
                pts.append(m @ _vec(((lo[0] if k & 1 else hi[0]),
                                     (lo[1] if k & 2 else hi[1]),
                                     (lo[2] if k & 4 else hi[2]))))
        return ([min(p[k] for p in pts) for k in range(3)],
                [max(p[k] for p in pts) for k in range(3)])


def _vec(p):
    from mathutils import Vector
    return Vector(p)


def _basis_inv() -> Matrix:
    return Matrix(scatter_reader.basis_matrix(True)).inverted()


# ── scene state ─────────────────────────────────────────────────────────────
def _collection(context):
    """The imported scatter collection, preferring the active one."""
    act = context.view_layer.active_layer_collection
    if act and act.collection.name.startswith("lescatter_"):
        return act.collection
    for c in bpy.data.collections:
        if c.name.startswith("lescatter_"):
            return c
    return None


def _package_dir(context, props) -> Path | None:
    if props.package_dir:
        return Path(bpy.path.abspath(props.package_dir))
    coll = _collection(context)
    if coll is not None and coll.get("le_package_dir"):
        return Path(coll["le_package_dir"])
    return None


def _instance_objects(coll):
    """`{instance index: [objects]}` for everything the importer placed."""
    out = {}
    if coll is None:
        return out
    for ob in coll.all_objects:
        idx = ob.get("le_instance_index")
        if idx is None:
            continue
        out.setdefault(int(idx), []).append(ob)
    return out


def _new_objects(coll):
    return [ob for ob in coll.all_objects
            if ob.type == "MESH" and ob.get("le_instance_index") is None] if coll else []


def collect_edits(context, props):
    """`(edits, added, problems)` -- what changed since import."""
    coll = _collection(context)
    pkg = _package_dir(context, props)
    if coll is None:
        return [], [], ["no `lescatter_*` collection in the scene -- import a level first"]
    if pkg is None or not (pkg / "manifest.json").is_file():
        return [], [], ["set Package to the imported .lescatter folder"]
    base = Baseline(pkg)
    binv = _basis_inv()
    edits, problems = [], []
    for idx, obs in _instance_objects(coll).items():
        ent = base.entity_of.get(idx)
        if ent is None:
            continue                                  # not a static instance
        stock = base.game_matrix(idx)
        now = binv @ obs[0].matrix_world
        d = now @ stock.inverted()
        moved = (abs(d.translation.length) > EPS_POS or
                 max(abs(d[r][c] - (1.0 if r == c else 0.0))
                     for r in range(3) for c in range(3)) > EPS_LIN)
        if not moved:
            continue
        box = base.world_aabb(idx, obs)
        if box is None:
            problems.append("instance %d (%s) has no mesh bounds; skipped" % (idx, ent))
            continue
        edits.append({"index": idx, "entity": ent, "delta": d, "box": box,
                      "objects": obs})
    return edits, _new_objects(coll), problems


def group_edits(edits, gap=0.15):
    """Merge edits whose stock boxes touch AND whose transforms agree.

    Several models usually make one physical object and share collision, so the
    group is what a component's owners are checked against: a component two
    entities own moves only when the group holds both. Grouping only instances
    that move the SAME way keeps that honest -- a group whose members disagree
    is left split, so their shared collision fails the ownership subset test and
    is reported rather than torn.
    """
    n = len(edits)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def same(a, b):
        da, db = edits[a]["delta"], edits[b]["delta"]
        return all(abs(da[r][c] - db[r][c]) < 1e-4 for r in range(4) for c in range(4))

    for i in range(n):
        for j in range(i + 1, n):
            (alo, ahi), (blo, bhi) = edits[i]["box"], edits[j]["box"]
            touch = all(alo[k] <= bhi[k] + gap and ahi[k] >= blo[k] - gap for k in range(3))
            if touch and same(i, j):
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(edits[i])
    out = []
    for members in groups.values():
        lo = [min(m["box"][0][k] for m in members) for k in range(3)]
        hi = [max(m["box"][1][k] for m in members) for k in range(3)]
        out.append({"members": members, "lo": lo, "hi": hi,
                    "delta": members[0]["delta"],
                    "entities": [m["entity"] for m in members]})
    return out


# ── mirroring ───────────────────────────────────────────────────────────────
MIRROR_MATRIX = {
    "POINT": Matrix(((-1, 0, 0, 0), (0, 1, 0, 0), (0, 0, -1, 0), (0, 0, 0, 1))),
    "X": Matrix(((-1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 1, 0), (0, 0, 0, 1))),
    "Z": Matrix(((1, 0, 0, 0), (0, 1, 0, 0), (0, 0, -1, 0), (0, 0, 0, 1))),
}


def partner_map(base: Baseline, mode: str, tol=0.08):
    """`{instance index: partner index}` under the chosen symmetry.

    Matched on the stock placement, and only between instances built from the
    same mesh or from that mesh's opposite-half twin -- the cook ships a
    separate model for each half of the level, so a `z` mirror never matches on
    mesh identity alone.
    """
    P = MIRROR_MATRIX[mode]
    idx = [i for i in base.rec if i in base.entity_of]
    pos = {i: base.rec[i]["pos"] for i in idx}
    mesh = {i: base.mesh_of.get(base.rec[i]["mesh"]) for i in idx}
    # mesh twin: two meshes whose instance sets are exact z-mirrors
    by_mesh = {}
    for i in idx:
        by_mesh.setdefault(mesh[i], []).append(i)
    twin = {}
    for a, ia in by_mesh.items():
        za = [pos[i][2] for i in ia]
        for b, ib in by_mesh.items():
            if b == a or len(ib) != len(ia):
                continue
            zb = [pos[i][2] for i in ib]
            if abs(min(za) + max(zb)) < 0.05 and abs(max(za) + min(zb)) < 0.05:
                twin[a] = b
    out = {}
    for i in idx:
        p = P @ _vec(pos[i])
        want = {mesh[i]} | ({twin[mesh[i]]} if mesh[i] in twin else set())
        best, bd = None, tol
        for j in idx:
            if j == i or mesh[j] not in want:
                continue
            q = pos[j]
            d = max(abs(p[0] - q[0]), abs(p[1] - q[1]), abs(p[2] - q[2]))
            if d < bd:
                best, bd = j, d
        if best is not None:
            out[i] = best
    return out


def sync_mirror(context, props) -> tuple[int, list]:
    coll = _collection(context)
    pkg = _package_dir(context, props)
    if coll is None or pkg is None:
        return 0, ["no imported level to mirror"]
    base = Baseline(pkg)
    binv = _basis_inv()
    b = Matrix(scatter_reader.basis_matrix(True))
    P = MIRROR_MATRIX[props.mirror_mode]
    pm = partner_map(base, props.mirror_mode)
    objs = _instance_objects(coll)
    edits, _added, _p = collect_edits(context, props)
    changed = {e["index"] for e in edits}
    done, notes = 0, []
    for e in edits:
        j = pm.get(e["index"])
        if j is None:
            notes.append("instance %d has no mirror partner" % e["index"])
            continue
        if j in changed and j < e["index"]:
            continue                                  # already the source side
        targets = objs.get(j)
        if not targets:
            continue
        want_game = (P @ e["delta"] @ P) @ base.game_matrix(j)
        for ob in targets:
            ob.matrix_world = b @ want_game
        done += 1
    return done, notes


# ── properties ──────────────────────────────────────────────────────────────
class EVRLevelEditProps(PropertyGroup):
    tools_dir: StringProperty(name="Tools", subtype="DIR_PATH", default=DEFAULT_TOOLS,
                              description="The repo's scripts/ folder (evr_geo_move, evr_clone_level)")
    extract_dir: StringProperty(name="pcvr-extracted", subtype="DIR_PATH",
                                default=DEFAULT_EXTRACT,
                                description="Flat extract the stock level is read from")
    package_dir: StringProperty(name="Package", subtype="DIR_PATH",
                                description="The imported .lescatter folder; blank = "
                                            "the one recorded on the collection")
    export_dir: StringProperty(name="Export to", subtype="DIR_PATH", default=DEFAULT_EXPORT,
                               description="Where the new level is written, ready to pack")
    source_level: StringProperty(name="Source level", default="mpl_arena_a",
                                 description="Level being edited; a name or its 16-hex hash")
    new_level: StringProperty(name="New level", default="mpl_arena_v2_a",
                              description="Name for the level you are making")
    mirror: BoolProperty(name="Mirror", default=True,
                         description="Keep the other half of the map in step with your edits")
    mirror_auto: BoolProperty(name="Auto", default=False,
                              description="Mirror on every change instead of on demand")
    mirror_mode: EnumProperty(
        name="Symmetry", default="POINT",
        items=[("POINT", "180 about Y", "x,z -> -x,-z; the arena's own symmetry"),
               ("Z", "Mirror Z", "z -> -z"),
               ("X", "Mirror X", "x -> -x")])
    allow_new: BoolProperty(
        name="Fail on New Objects", default=False,
        description="Stop the export when the scene contains objects that were not "
                    "in the stock level, instead of exporting the rest and warning. "
                    "New objects cannot ship yet: an added instance needs an actor "
                    "id and CActorDataResource growth is not established")


# ── operators ───────────────────────────────────────────────────────────────
class EVR_OT_mirror_sync(Operator):
    bl_idname = "evr.level_mirror_sync"
    bl_label = "Sync Mirror"
    bl_description = "Copy your edits across to the other half of the map"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        props = context.scene.evr_level_edit
        n, notes = sync_mirror(context, props)
        for m in notes[:3]:
            self.report({"WARNING"}, m)
        self.report({"INFO"}, "mirrored %d object(s)" % n)
        return {"FINISHED"}


class EVR_OT_validate(Operator):
    bl_idname = "evr.level_validate"
    bl_label = "Check Edits"
    bl_description = "Report what would be exported, without writing anything"

    def execute(self, context):
        props = context.scene.evr_level_edit
        edits, added, problems = collect_edits(context, props)
        groups = group_edits(edits)
        for m in problems[:5]:
            self.report({"WARNING"}, m)
        self.report({"INFO"}, "%d instance(s) edited in %d group(s); %d new object(s)"
                    % (len(edits), len(groups), len(added)))
        return {"FINISHED"} if not problems else {"FINISHED"}


class EVR_OT_export(Operator):
    bl_idname = "evr.level_export"
    bl_label = "Export Level"
    bl_description = "Clone the level under the new name and apply every edit, " \
                     "moving render placement, collision and raycast geometry together"

    def execute(self, context):
        props = context.scene.evr_level_edit
        try:
            clone_mod, geo = _load_tools(props.tools_dir)
        except Exception as exc:                       # noqa: BLE001
            self.report({"ERROR"}, "cannot load the level tools from %s: %s"
                        % (props.tools_dir, exc))
            return {"CANCELLED"}
        if not props.new_level.strip():
            self.report({"ERROR"}, "give the new level a name")
            return {"CANCELLED"}

        edits, added, problems = collect_edits(context, props)
        if problems:
            for m in problems[:5]:
                self.report({"ERROR"}, m)
            return {"CANCELLED"}
        if added:
            # Say exactly what is missing rather than dropping them quietly. The
            # transform/collision half of adding an object is solved; the actor
            # id is not, and shipping an instance whose actor the level does not
            # know is how you get a map that will not load.
            self.report({"WARNING"} if not props.allow_new else {"ERROR"},
                        "%d new object(s) NOT exported: a new instance needs an actor "
                        "id, and CActorDataResource growth is not established "
                        "(1,553 keyed pairs, 167 groups, 4 bitmaps). Everything else "
                        "for it -- transform row, static-instance record, collision "
                        "body -- is ready." % len(added))
            if props.allow_new:
                return {"CANCELLED"}
        groups = group_edits(edits)

        src = props.source_level.strip()
        dst = props.new_level.strip()
        src_hex = clone_mod.resolve(src)
        dst_hex = clone_mod.resolve(dst)
        if src_hex == dst_hex:
            self.report({"ERROR"}, "the new level needs a different name")
            return {"CANCELLED"}
        root = Path(bpy.path.abspath(props.extract_dir))
        out = Path(bpy.path.abspath(props.export_dir))

        try:
            info = clone_mod.clone(src, dst, root, out, verbose=False)
        except SystemExit as exc:
            self.report({"ERROR"}, "clone refused: %s" % exc)
            return {"CANCELLED"}
        except Exception as exc:                       # noqa: BLE001
            self.report({"ERROR"}, "clone failed: %s" % exc)
            return {"CANCELLED"}

        # Which collision belongs to which object, measured on the stock level.
        # Without this the selection is just "what fits the box", which both
        # drags a neighbour's collision along when the edited object is large
        # and abandons the object's own hull wherever it overhangs.
        owners = None
        try:
            base = Baseline(_package_dir(context, props))
            owners = geo.Owners(base.owner_items())
            if not len(owners):
                owners = None
        except Exception as exc:                       # noqa: BLE001
            self.report({"WARNING"},
                        "could not index collision ownership (%s); falling back "
                        "to box selection, which may move a neighbour's "
                        "collision or leave part of yours behind" % exc)

        try:
            lv = geo.Level(root, src_hex)
            moved_i = moved_c = moved_t = 0
            turned_d = shared_d = 0
            foreign = overhang = unclaimed = 0
            missed = []
            for g in groups:
                import numpy as np
                D = g["delta"]
                M = np.array([[D[r][c] for c in range(3)] for r in range(3)])
                t = np.array([D[0][3], D[1][3], D[2][3]])
                lo = np.array(g["lo"]) - 0.30
                hi = np.array(g["hi"]) + 0.30
                r = lv.transform(box=(lo, hi), entities=g["entities"],
                                 rot=M, scale=(1.0, 1.0, 1.0),
                                 pivot=np.zeros(3), delta=t,
                                 label="edit", owners=owners)
                moved_i += r["instances"]
                moved_c += r["phys_components"]
                moved_t += r["bvh_triangles"]
                turned_d += r.get("plane_dirs", 0)
                shared_d += r.get("plane_dirs_shared", 0)
                foreign += r.get("phys_foreign", 0)
                overhang += r.get("phys_overhang", 0)
                unclaimed += r.get("phys_unclaimed", 0)
                if r["instances"] != len(g["entities"]):
                    missed.append("only %d of %d instances placed"
                                  % (r["instances"], len(g["entities"])))
                if r["phys_straddled"]:
                    missed.append("%d collision component(s) crossed the selection "
                                  "box with no owner to settle it, and were left "
                                  "alone" % r["phys_straddled"])
            # A turned object's `+400` directions follow it, but one a static
            # triangle also produces has to keep serving that triangle and is
            # left behind. Say so rather than letting it pass silently -- it is
            # the one part of a rotate that the exporter cannot make exact.
            if shared_d:
                missed.append("%d collision direction(s) kept their old heading: "
                              "static geometry still needs them (%d turned)"
                              % (shared_d, turned_d))
            lv.write(out, dst_hex)
        except Exception as exc:                       # noqa: BLE001
            self.report({"ERROR"}, "geometry export failed: %s" % exc)
            return {"CANCELLED"}

        for m in missed[:4]:
            self.report({"WARNING"}, m)
        if owners is not None:
            # Say what ownership decided, because both halves are things the
            # box rule used to get wrong silently.
            self.report({"INFO"},
                        "collision ownership: %d component(s) taken along past "
                        "the selection box, %d left with their own object, "
                        "%d unclaimed" % (overhang, foreign, unclaimed))
        self.report({"INFO"},
                    "%s -> %s: %d instances, %d collision components, "
                    "%d raycast tris, %d collision directions turned"
                    % (props.source_level, props.new_level, moved_i, moved_c,
                       moved_t, turned_d))
        return {"FINISHED"}


# ── panel ───────────────────────────────────────────────────────────────────
class EVR_PT_level_edit(Panel):
    bl_label = "EVR Level"
    bl_idname = "EVR_PT_level_edit"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "EVR Level"

    def draw(self, context):
        L = self.layout
        p = context.scene.evr_level_edit

        box = L.box()
        box.label(text="Paths", icon="FILE_FOLDER")
        box.prop(p, "extract_dir")
        box.prop(p, "package_dir")
        box.prop(p, "export_dir")
        box.prop(p, "tools_dir")

        box = L.box()
        box.label(text="Level", icon="WORLD")
        box.prop(p, "source_level")
        box.prop(p, "new_level")

        box = L.box()
        row = box.row(align=True)
        row.prop(p, "mirror", toggle=True, icon="MOD_MIRROR")
        sub = row.row(align=True)
        sub.enabled = p.mirror
        sub.prop(p, "mirror_auto", toggle=True)
        sub = box.column()
        sub.enabled = p.mirror
        sub.prop(p, "mirror_mode")
        sub.operator("evr.level_mirror_sync", icon="MOD_MIRROR")

        coll = _collection(context)
        box = L.box()
        box.label(text="Status", icon="INFO")
        if coll is None:
            box.label(text="no level imported", icon="ERROR")
        else:
            box.label(text=coll.name)
            n_new = len(_new_objects(coll))
            if n_new:
                box.label(text="%d new object(s) - not exportable yet" % n_new,
                          icon="ERROR")
        box.prop(p, "allow_new")

        col = L.column(align=True)
        col.operator("evr.level_validate", icon="CHECKMARK")
        col.scale_y = 1.3
        col.operator("evr.level_export", icon="EXPORT")


# ── auto mirror ─────────────────────────────────────────────────────────────
_BUSY = [False]


def _depsgraph_mirror(scene, depsgraph=None):
    p = getattr(scene, "evr_level_edit", None)
    if p is None or not (p.mirror and p.mirror_auto) or _BUSY[0]:
        return
    if not any(u.is_updated_transform for u in (depsgraph.updates if depsgraph else ())):
        return
    _BUSY[0] = True
    try:
        sync_mirror(bpy.context, p)
    except Exception:                                  # noqa: BLE001
        pass
    finally:
        _BUSY[0] = False


CLASSES = (EVRLevelEditProps, EVR_OT_mirror_sync, EVR_OT_validate,
           EVR_OT_export, EVR_PT_level_edit)


def register():
    for c in CLASSES:
        bpy.utils.register_class(c)
    bpy.types.Scene.evr_level_edit = PointerProperty(type=EVRLevelEditProps)
    if _depsgraph_mirror not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_depsgraph_mirror)


def unregister():
    if _depsgraph_mirror in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_depsgraph_mirror)
    del bpy.types.Scene.evr_level_edit
    for c in reversed(CLASSES):
        bpy.utils.unregister_class(c)
