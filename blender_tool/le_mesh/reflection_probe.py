"""`CGReflectionProbeResourceWin7` -> the ambient SPECULAR term (probes + IBL cubes).

Pure stdlib.  No Oodle, no archive, no `bpy` — unit-testable outside Blender and
importable unchanged inside it.  Sibling of `le_mesh.lightmap`, which does the
same job for the ambient DIFFUSE term.

What this module covers
-----------------------
1. `SReflectionProbeMetaData` on disk: **the unpatched CTable memory image**,
   six `CTableA<T,0>` records of `0x38` bytes followed by two `u32`, then every
   table's payload back-to-back in declaration order.  Residual **0**.
2. The five populated sub-tables: `SGProbeBox` (selection volumes),
   `SGProbePoint` (one per probe), `mipcounts`, `SGProbeBoundingBox` (the GPU
   structured-buffer record) and `gpuoffsets` (byte offset of each probe's
   cubemap inside the paired GPU slice).
3. The GPU payload: a BC6H_UF16 cube array — `gpumemsize` bytes, one cube per
   probe, `gpuoffsets[i]` bytes in, face-major with a full mip chain per face.
4. `CGMeshData.probeidx@0x50` -> which probe a mesh reflects.
5. DDS writers so the cube payload leaves the archive as an inspectable file,
   and a Blender-facing spec dict.

Full write-up with every count: docs/LIGHTING.md.

The on-disk grammar  (`stream-confirmed`)
-------------------------------------------------
⚠ Disk offsets below are **disk** offsets.  They coincide with the engine's own declarations's runtime
offsets for the head of the struct only because the resource is serialised as a
raw memory image whose pointer slots are left null until `CResource` patches
them (`CResource::ePointersPatched`); the *payloads* are appended after the
struct and have no runtime offset at all.

    +0x000  CTableA<SGProbeBox,0>          boxes
    +0x038  CTableA<SGProbeSphere,0>       spheres
    +0x070  CTableA<SGProbePoint,0>        points
    +0x0a8  CTableA<unsigned int,0>        mipcounts
    +0x0e0  CTableA<SGProbeBoundingBox,0>  boundingboxes
    +0x118  CTableA<unsigned int,0>        gpuoffsets
    +0x150  u32                            gpumemsize
    +0x154  u32                            textureformat   (engine ETextureFormat)
    +0x158  payloads, in declaration order, no padding

Each `CTableA<T,0>` image is a table (the field names are the engine's own,
`name-only`; every field below is `stream-confirmed`):

    +0x00  u64 ptr        ALWAYS 0 on disk — the unpatched data pointer
    +0x08  u64 nbytes     payload size in BYTES  (== iused * sizeof(T))
    +0x10  u64            0
    +0x18  u64 expand     32 on 100 % of shipped tables
    +0x20  u64 iallocated
    +0x28  u64 iused      the element count
    +0x30  u32 flags / u32 padding

`nbytes // iused` is therefore a **measured** element stride, not an assumed
one — which is how `SGProbeBoundingBox` was pinned at `0x98` (see below).

⛔ **`boxes` is not the probe count.**  On station_front `942c829457a04a62` the
resource holds **23 boxes but 16 probes**; `points`, `mipcounts`,
`boundingboxes` and `gpuoffsets` all read 16 and the GPU slice is 16 cubes.
The "23 boxes" figure recorded in docs/SCENES.md is the
count of *selection volumes*; several boxes share one probe (histogram on
station_front: probe 12 x5, probe 13 x10, probe 14 x5, probes 0/1/15 x1).

Record layouts
--------------
`SGProbeBox` — stride `0x38`, names `name-only` (the engine's own, from the same
neighbourhood of declarations),
offsets `stream-confirmed`:

    +0x00  CQuaternion invrot   (x, y, z, w); identity on 22/23 station_front boxes
    +0x10  C3Vector    pos      world centre of the volume
    +0x1c  CBox        box      local min (0x1c) / max (0x28), symmetric about pos
    +0x34  u32         probeidx index into points/mipcounts/boundingboxes/gpuoffsets

`SGProbeSphere` — stride `0x14` (`CSphere` 0x10 + u32).  **0 shipped anywhere in
the corpus**, so its stride is `name-only`, never stream-confirmed.

`SGProbePoint` — stride `0x10`: `C3Vector point` + `u32 probeidx`.  One per
probe, and `probeidx == row index` on every shipped resource measured.

`SGProbeBoundingBox` — stride **`0x98` measured** (`nbytes // iused`).  The
engine's own declaration reads `C4Vector[80] normalizations`, which would make the struct 0x548; the
shipped stride says the `[80]` is a **byte** length, i.e. 20 floats:

    +0x00  C33Matrix rotation        row-major, orthonormal (rowlen 1.0000)
    +0x24  C3Vector  probepos        == points[i].point
    +0x30  C3Vector  min             OBB corner, in the probe's rotated frame,
    +0x3c  C3Vector  max             RELATIVE to probepos  (see below)
    +0x48  float[20] normalizations  per-mip scalar at index 2*mip

`gpuoffsets` — `u32` byte offsets into the paired `CGReflectionProbeResourceWin7GPU`
slice.  Uniform stride on every shipped resource measured, and
`gpuoffsets[-1] + stride == gpumemsize == the GPU entry's size`.

The OBB is one shared volume (`measured`)
-----------------------------------------
`min[i] + R*probepos[i]` is **constant across probes** on station_front (15 of
the 16 share one `R` and one constant; the 16th, the exterior/vista probe, has
its own).  So `min`/`max` are one world-space oriented box expressed in each
probe's own rotated, probe-relative frame — the classic box-projection
(parallax) volume.  `measured` on `942c829457a04a62`; the runtime use is
`inferred`, no shader has been read for it.

`normalizations` (`measured` layout, `inferred` meaning)
--------------------------------------------------------
20 floats.  On every shipped row measured, exactly `mipcounts[i]` of them are
non-zero and they sit at **even** indices `0, 2, 4, … 2*(mipcount-1)`; index 1
duplicates index 0 and every other odd slot is 0.  Values fall monotonically
from mip 1 onward and exceed 1.0 often (16.0 observed) — the shape of a per-mip
radiance scale for a pre-normalised BC6H prefilter.  ⚠ **No shader has been read
that consumes them**, so `mip_normalizations()` is offered and NOT applied.

The join (sibling-by-name)
--------------------------
`CGScene` (the engine's own type names) owns `CResourceInstanceT<CGReflectionProbeResource>
reflectionresource` at runtime `+0x790` in this build, and `CGSceneData` stores
no id for it: the resource is addressed by the scene's **own name hash**, the
same convention `le_mesh.lightmap.lightmap_resource_name_for_scene` names for
the lightmap sibling.  `stream-confirmed`:
`CGameLevelResourceWin7 == CGReflectionProbeResourceWin7` in 90/90 level
archives (docs/SCENES.md).

Coverage
--------
`census` over `generic_rebuilds/archive_census/archive_type_counts.tsv`
(all 1,244 archives of the LE1 Win7 retail corpus): `CGReflectionProbeResourceWin7`
**94 resources / 90 archives**, and `…Win7GPU` the same 94/90.  Four archives
carry 2.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

# =============================================================================
# grammar constants
# =============================================================================

#: `CTableA<T,0>` memory image size.
CTABLE_STRIDE = 0x38
#: field offsets inside one `CTableA<T,0>` image
CT_PTR = 0x00
CT_NBYTES = 0x08
CT_EXPAND = 0x18
CT_IALLOCATED = 0x20
CT_IUSED = 0x28
CT_FLAGS = 0x30

#: the six tables of `SReflectionProbeMetaData`, in declaration order.
TABLE_NAMES = ("boxes", "spheres", "points", "mipcounts", "boundingboxes", "gpuoffsets")

#: `SReflectionProbeMetaData` head size: 6 tables + gpumemsize + textureformat.
META_HEADER_SIZE = 0x158
OFF_GPUMEMSIZE = 0x150
OFF_TEXTUREFORMAT = 0x154

#: element strides.  `stream-confirmed` except `SGProbeSphere`, which never ships.
STRIDE_BOX = 0x38
STRIDE_SPHERE = 0x14          # name-only: CSphere(0x10) + u32.  0 shipped.
STRIDE_POINT = 0x10
STRIDE_U32 = 0x04
STRIDE_BOUNDINGBOX = 0x98     # measured from nbytes//iused, NOT from the engine's own declarations

#: `SGProbeBox` field offsets
B_INVROT = 0x00
B_POS = 0x10
B_MIN = 0x1C
B_MAX = 0x28
B_PROBEIDX = 0x34

#: `SGProbeBoundingBox` field offsets
BB_ROTATION = 0x00
BB_PROBEPOS = 0x24
BB_MIN = 0x30
BB_MAX = 0x3C
BB_NORMALIZATIONS = 0x48
BB_NORMALIZATION_FLOATS = 20

#: `CGMeshData.probeidx` — declared in `le_mesh.meshlist` as `M_PROBEIDX` and
#: read by nothing there.  Re-declared here so this module can read it off a
#: mesh table without `le_mesh.meshlist` having to change.
M_PROBEIDX = 0x50
MESH_STRIDE = 0x80
#: "this mesh has no probe".  `stream-confirmed` — see
#: docs/LIGHTING.md §4 for the measured distribution.
PROBE_INDEX_NONE = 0xFFFFFFFF

#: guard against a mis-pointed slice decoding as a giant table
MAX_TABLE_ENTRIES = 65536

# =============================================================================
# formats
# =============================================================================

#: `NRadEngine::ETextureFormat`.  `name-only` — the names are the engine's own.
#: Only the block-compressed tail is listed — that is all a probe can carry.
#: ⚠ This is the ENGINE enum, NOT DXGI: `CGTextureResourceData.format` is DXGI
#: and the two must never be mixed (docs/SCENES.md).
ETEXTUREFORMAT = {
    49: "eBC1eUNORM", 50: "eBC1eSRGB", 51: "eBC2eUNORM", 52: "eBC2eSRGB",
    53: "eBC3eUNORM", 54: "eBC3eSRGB", 55: "eBC4eUNORM", 56: "eBC4eSNORM",
    57: "eBC5eUNORM", 58: "eBC5eSNORM", 59: "eBC6UeFLOAT", 60: "eBC6SeFLOAT",
    61: "eBC7eUNORM", 62: "eBC7eSRGB",
}

#: the one format shipped by every probe measured.
ETEXTUREFORMAT_BC6H_UF16 = 59

#: engine `ETextureFormat` -> the DXGI format a DDS must declare.
ETEXTUREFORMAT_TO_DXGI = {
    49: 71, 50: 72, 51: 74, 52: 75, 53: 77, 54: 78, 55: 80, 56: 81,
    57: 83, 58: 84, 59: 95, 60: 96, 61: 98, 62: 99,
}
DXGI_BC6H_UF16 = 95

#: bytes per 4x4 block, by engine format.
_BLOCK_BYTES = {49: 8, 50: 8, 55: 8, 56: 8}     # BC1/BC4 are 8; everything else 16
BLOCK_DIM = 4

#: the HDR cube is linear light, exactly like the lightmap colour array.
#: `engine-confirmed (Blender 5.1.1)` for the colour-space pair — see
#: `le_mesh.lightmap.COLORSPACE_LIGHTMAP` for the measurement that established it.
COLORSPACE_PROBE = "Linear Rec.709"
COLORSPACE_PROBE_FALLBACK = "Non-Color"

#: resource TYPE hashes (`hash_lookup.json`).  Kept in the pure-stdlib core for
#: the same reason `le_mesh.lightmap` keeps `LIGHTMAP_TYPE_WIN7`.
REFLECTION_PROBE_TYPE_WIN7 = 0x8BA398B946658761
REFLECTION_PROBE_TYPE_WIN7_GPU = 0xCA26A533F9BACD85

#: the manifest key the addon's `probe_builder` reads.
MANIFEST_KEY = "reflection_probes"
#: package sub-directory the cube DDS files are written to.
PROBE_DIR = "probes"


def block_bytes(engine_format: int) -> int:
    """Bytes per 4x4 block for an engine `ETextureFormat`."""
    return _BLOCK_BYTES.get(engine_format, 16)


def format_name(engine_format: int) -> str:
    return ETEXTUREFORMAT.get(engine_format, f"<{engine_format}>")


# =============================================================================
# records
# =============================================================================

@dataclass
class ProbeBox:
    """`SGProbeBox`: an oriented volume that selects one probe."""
    index: int
    inv_rot: tuple            # quaternion (x, y, z, w)
    pos: tuple                # world centre
    local_min: tuple
    local_max: tuple
    probe_index: int

    @property
    def is_identity_rotation(self) -> bool:
        x, y, z, w = self.inv_rot
        return abs(x) < 1e-6 and abs(y) < 1e-6 and abs(z) < 1e-6 and abs(abs(w) - 1.0) < 1e-6

    def contains(self, world_pos) -> bool:
        """Is `world_pos` inside this volume?  `inferred` — see `select_probe`."""
        d = [world_pos[i] - self.pos[i] for i in range(3)]
        lx, ly, lz = _quat_rotate(self.inv_rot, d)
        return (self.local_min[0] <= lx <= self.local_max[0]
                and self.local_min[1] <= ly <= self.local_max[1]
                and self.local_min[2] <= lz <= self.local_max[2])

    @property
    def half_extent(self) -> tuple:
        return tuple((self.local_max[i] - self.local_min[i]) * 0.5 for i in range(3))

    @property
    def volume(self) -> float:
        h = self.half_extent
        return 8.0 * h[0] * h[1] * h[2]

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "probe": self.probe_index,
            "pos": list(self.pos),
            "inv_rot": list(self.inv_rot),
            "min": list(self.local_min),
            "max": list(self.local_max),
        }


@dataclass
class ProbeSphere:
    """`SGProbeSphere`.  **Zero shipped in the corpus** — decoded for completeness."""
    index: int
    center: tuple
    radius: float
    probe_index: int

    def as_dict(self) -> dict:
        return {"index": self.index, "probe": self.probe_index,
                "center": list(self.center), "radius": self.radius}


@dataclass
class ProbePoint:
    """`SGProbePoint`: the probe's own capture position."""
    index: int
    point: tuple
    probe_index: int

    def as_dict(self) -> dict:
        return {"index": self.index, "probe": self.probe_index, "point": list(self.point)}


@dataclass
class ProbeBoundingBox:
    """`SGProbeBoundingBox`: the per-probe GPU structured-buffer record."""
    index: int
    rotation: tuple           # 9 floats, row-major
    probe_pos: tuple
    obb_min: tuple            # in the probe's rotated, probe-relative frame
    obb_max: tuple
    normalizations: tuple     # 20 raw floats

    @property
    def rotation_rows(self) -> list:
        r = self.rotation
        return [r[0:3], r[3:6], r[6:9]]

    @property
    def rotation_row_lengths(self) -> list:
        return [sum(c * c for c in row) ** 0.5 for row in self.rotation_rows]

    @property
    def is_orthonormal(self) -> bool:
        return all(abs(l - 1.0) < 1e-3 for l in self.rotation_row_lengths)

    def mip_normalizations(self, mipcount: int) -> list:
        """The `mipcount` per-mip scalars: `normalizations[2*mip]`.

        `measured` layout, `inferred` meaning.  Not applied by anything in this
        tree — see the module docstring.
        """
        n = self.normalizations
        return [n[2 * m] for m in range(min(mipcount, BB_NORMALIZATION_FLOATS // 2))]

    def normalizations_well_formed(self, mipcount: int | None = None) -> bool:
        """The measured shape: `[1] == [0]`, every other odd slot 0.

        A row that fails this is a loud signal that the 20-float reading of the
        engine's own `C4Vector[80]` is wrong for that build.
        """
        n = self.normalizations
        if abs(n[1] - n[0]) > 1e-6:
            return False
        if any(abs(n[2 * k + 1]) > 0.0 for k in range(1, BB_NORMALIZATION_FLOATS // 2)):
            return False
        if mipcount is not None:
            tail = [n[2 * k] for k in range(mipcount, BB_NORMALIZATION_FLOATS // 2)]
            if any(abs(v) > 0.0 for v in tail):
                return False
        return True

    def world_obb_corner(self, which: str = "min") -> tuple:
        """`min`/`max` mapped back to world: `probepos + R^T * corner`."""
        c = self.obb_min if which == "min" else self.obb_max
        rows = self.rotation_rows
        return tuple(self.probe_pos[i] + sum(rows[k][i] * c[k] for k in range(3))
                     for i in range(3))

    def as_dict(self, mipcount: int | None = None) -> dict:
        d = {
            "index": self.index,
            "probe_pos": list(self.probe_pos),
            "rotation": list(self.rotation),
            "obb_min": list(self.obb_min),
            "obb_max": list(self.obb_max),
        }
        if mipcount is not None:
            d["mip_normalizations"] = self.mip_normalizations(mipcount)
        return d


@dataclass
class ReflectionProbeResource:
    """A decoded `CGReflectionProbeResourceWin7` primary slice."""
    boxes: list = field(default_factory=list)
    spheres: list = field(default_factory=list)
    points: list = field(default_factory=list)
    mipcounts: list = field(default_factory=list)
    bounding_boxes: list = field(default_factory=list)
    gpu_offsets: list = field(default_factory=list)
    gpumemsize: int = 0
    texture_format: int = 0
    #: bytes of the slice not consumed by the header + the six payloads.
    #: **0 on every shipped resource measured** — the arithmetic that proves the
    #: grammar, the same discipline the instance-lightmap record was proved with.
    residual: int = 0
    #: measured element stride of `boundingboxes` (`nbytes // iused`)
    bb_stride: int = 0
    #: raw table descriptors, for auditing: {name: (ptr, nbytes, iallocated, iused)}
    tables: dict = field(default_factory=dict)

    # --- counts -------------------------------------------------------------
    @property
    def n_probes(self) -> int:
        """The PROBE count — `points`, not `boxes`.

        ⛔ `len(self.boxes)` is the count of selection VOLUMES and is larger
        (23 vs 16 on station_front).
        """
        return len(self.points)

    @property
    def n_spheres(self) -> int:
        return len(self.spheres)

    @property
    def format_name(self) -> str:
        return format_name(self.texture_format)

    # --- GPU payload --------------------------------------------------------
    def per_probe_bytes(self):
        """Bytes per cube, from the offsets (or from `gpumemsize` when n == 1)."""
        if not self.gpu_offsets:
            return None
        if len(self.gpu_offsets) == 1:
            return self.gpumemsize - self.gpu_offsets[0]
        return self.gpu_offsets[1] - self.gpu_offsets[0]

    def gpu_offsets_uniform(self) -> bool:
        """Are the cube offsets a uniform stride that closes on `gpumemsize`?"""
        if len(self.gpu_offsets) < 2:
            return bool(self.gpu_offsets) and self.gpu_offsets[0] == 0
        d = {self.gpu_offsets[i + 1] - self.gpu_offsets[i]
             for i in range(len(self.gpu_offsets) - 1)}
        if len(d) != 1:
            return False
        stride = d.pop()
        return (self.gpu_offsets[0] == 0
                and self.gpu_offsets[-1] + stride == self.gpumemsize)

    def probe_gpu_range(self, probe: int):
        """`(start, end)` of probe `probe`'s cube inside the paired GPU slice."""
        if probe < 0 or probe >= len(self.gpu_offsets):
            raise IndexError(f"probe {probe} out of range 0..{len(self.gpu_offsets)}")
        start = self.gpu_offsets[probe]
        if probe + 1 < len(self.gpu_offsets):
            return start, self.gpu_offsets[probe + 1]
        return start, self.gpumemsize

    def mipcount(self, probe: int) -> int:
        return self.mipcounts[probe] if probe < len(self.mipcounts) else 0

    def cube_dim(self, probe: int = 0):
        """Cube face dimension implied by this probe's byte budget + mipcount."""
        per = self.per_probe_bytes()
        if not per:
            return None
        return cube_dim_for(per, self.mipcount(probe), block_bytes(self.texture_format))

    # --- selection ----------------------------------------------------------
    def select_probe(self, world_pos):
        """Which probe covers `world_pos`?  `inferred` — a fallback, not a law.

        The engine's own selection rule has not been reverse-engineered; what IS
        known is that every mesh already carries the answer in
        `CGMeshData.probeidx@0x50`.  This exists for geometry that has no mesh
        record (e.g. an instance placed by the importer) and for cross-checking.

        Smallest containing box wins; failing that, the nearest `SGProbePoint`.
        """
        best = None
        for b in self.boxes:
            if b.contains(world_pos):
                v = b.volume
                if best is None or v < best[0]:
                    best = (v, b.probe_index)
        if best is not None:
            return best[1]
        if not self.points:
            return None
        return min(self.points,
                   key=lambda p: sum((p.point[i] - world_pos[i]) ** 2 for i in range(3))
                   ).probe_index

    def boxes_for_probe(self, probe: int) -> list:
        return [b for b in self.boxes if b.probe_index == probe]

    def as_dict(self) -> dict:
        return {
            "probes": self.n_probes,
            "boxes": len(self.boxes),
            "spheres": self.n_spheres,
            "gpumemsize": self.gpumemsize,
            "texture_format": self.texture_format,
            "texture_format_name": self.format_name,
            "mipcounts": list(self.mipcounts),
            "gpu_offsets": list(self.gpu_offsets),
            "residual": self.residual,
        }


# =============================================================================
# decode
# =============================================================================

def _quat_rotate(q, v):
    """Rotate `v` by quaternion `q = (x, y, z, w)`."""
    x, y, z, w = q
    # t = 2 * cross(q.xyz, v);  v' = v + w*t + cross(q.xyz, t)
    tx = 2.0 * (y * v[2] - z * v[1])
    ty = 2.0 * (z * v[0] - x * v[2])
    tz = 2.0 * (x * v[1] - y * v[0])
    return (v[0] + w * tx + (y * tz - z * ty),
            v[1] + w * ty + (z * tx - x * tz),
            v[2] + w * tz + (x * ty - y * tx))


def parse_ctable(blob: bytes, off: int) -> tuple:
    """One `CTableA<T,0>` memory image -> `(ptr, nbytes, iallocated, iused)`."""
    if len(blob) - off < CTABLE_STRIDE:
        raise ValueError(f"CTable image needs {CTABLE_STRIDE} bytes at {off:#x}")
    ptr = struct.unpack_from("<Q", blob, off + CT_PTR)[0]
    nbytes = struct.unpack_from("<Q", blob, off + CT_NBYTES)[0]
    ialloc = struct.unpack_from("<Q", blob, off + CT_IALLOCATED)[0]
    iused = struct.unpack_from("<Q", blob, off + CT_IUSED)[0]
    return ptr, nbytes, ialloc, iused


def parse_probe_resource(blob: bytes, off: int = 0, *, strict: bool = True):
    """Decode a `CGReflectionProbeResourceWin7` primary slice.

    `strict=True` raises on anything that would silently produce a plausible-
    looking but wrong parse: a non-null table pointer (the slice is not an
    unpatched image), an implausible count, or payloads that overrun the slice.
    `strict=False` clamps instead, for probing a slice whose bounds are unsure.
    """
    n = len(blob) - off
    if n < META_HEADER_SIZE:
        raise ValueError(
            f"reflection-probe slice is {n} bytes, shorter than the "
            f"{META_HEADER_SIZE}-byte SReflectionProbeMetaData head")

    tables = {}
    for i, name in enumerate(TABLE_NAMES):
        ptr, nbytes, ialloc, iused = parse_ctable(blob, off + i * CTABLE_STRIDE)
        if ptr != 0 and strict:
            raise ValueError(
                f"{name}: data pointer is {ptr:#x}, not 0 — this slice is not an "
                f"unpatched CTable image (CResource::ePointersPatched)")
        if iused > MAX_TABLE_ENTRIES:
            if strict:
                raise ValueError(f"{name}: implausible count {iused}")
            iused = 0
            nbytes = 0
        tables[name] = (ptr, nbytes, ialloc, iused)

    gpumemsize, texfmt = struct.unpack_from("<II", blob, off + OFF_GPUMEMSIZE)

    cursor = off + META_HEADER_SIZE
    payload = {}
    for name in TABLE_NAMES:
        _, nbytes, _, iused = tables[name]
        if cursor + nbytes > len(blob):
            if strict:
                raise ValueError(
                    f"{name}: payload [{cursor:#x}, {cursor + nbytes:#x}) overruns the "
                    f"{len(blob)}-byte slice")
            nbytes = max(0, len(blob) - cursor)
            iused = 0
        payload[name] = (cursor, nbytes, iused)
        cursor += nbytes
    residual = len(blob) - cursor

    def stride_of(name, expected):
        _, nbytes, iused = payload[name]
        if not iused:
            return expected
        return nbytes // iused

    res = ReflectionProbeResource(
        gpumemsize=gpumemsize, texture_format=texfmt,
        residual=residual, tables=tables)

    # boxes
    o, _, cnt = payload["boxes"]
    st = stride_of("boxes", STRIDE_BOX)
    for i in range(cnt):
        b = o + i * st
        res.boxes.append(ProbeBox(
            index=i,
            inv_rot=struct.unpack_from("<4f", blob, b + B_INVROT),
            pos=struct.unpack_from("<3f", blob, b + B_POS),
            local_min=struct.unpack_from("<3f", blob, b + B_MIN),
            local_max=struct.unpack_from("<3f", blob, b + B_MAX),
            probe_index=struct.unpack_from("<I", blob, b + B_PROBEIDX)[0]))

    # spheres
    o, _, cnt = payload["spheres"]
    st = stride_of("spheres", STRIDE_SPHERE)
    for i in range(cnt):
        b = o + i * st
        cx, cy, cz, r, pi = struct.unpack_from("<4fI", blob, b)
        res.spheres.append(ProbeSphere(i, (cx, cy, cz), r, pi))

    # points
    o, _, cnt = payload["points"]
    st = stride_of("points", STRIDE_POINT)
    for i in range(cnt):
        b = o + i * st
        x, y, z, pi = struct.unpack_from("<3fI", blob, b)
        res.points.append(ProbePoint(i, (x, y, z), pi))

    # mipcounts
    o, _, cnt = payload["mipcounts"]
    res.mipcounts = list(struct.unpack_from(f"<{cnt}I", blob, o)) if cnt else []

    # bounding boxes
    o, _, cnt = payload["boundingboxes"]
    st = stride_of("boundingboxes", STRIDE_BOUNDINGBOX)
    res.bb_stride = st
    for i in range(cnt):
        b = o + i * st
        res.bounding_boxes.append(ProbeBoundingBox(
            index=i,
            rotation=struct.unpack_from("<9f", blob, b + BB_ROTATION),
            probe_pos=struct.unpack_from("<3f", blob, b + BB_PROBEPOS),
            obb_min=struct.unpack_from("<3f", blob, b + BB_MIN),
            obb_max=struct.unpack_from("<3f", blob, b + BB_MAX),
            normalizations=struct.unpack_from(
                f"<{BB_NORMALIZATION_FLOATS}f", blob, b + BB_NORMALIZATIONS)))

    # gpu offsets
    o, _, cnt = payload["gpuoffsets"]
    res.gpu_offsets = list(struct.unpack_from(f"<{cnt}I", blob, o)) if cnt else []
    return res


# =============================================================================
# cube geometry
# =============================================================================

def cube_bytes(dim: int, mips: int, bpb: int = 16, faces: int = 6) -> int:
    """Bytes a block-compressed cube of `dim` with `mips` levels occupies."""
    total = 0
    for m in range(mips):
        w = max(1, dim >> m)
        blocks = ((w + BLOCK_DIM - 1) // BLOCK_DIM) ** 2
        total += blocks * bpb
    return total * faces


def face_mip_offsets(dim: int, mips: int, bpb: int = 16) -> list:
    """`[(offset, nbytes, mip_dim), …]` for ONE face's mip chain."""
    out, off = [], 0
    for m in range(mips):
        w = max(1, dim >> m)
        nb = ((w + BLOCK_DIM - 1) // BLOCK_DIM) ** 2 * bpb
        out.append((off, nb, w))
        off += nb
    return out


def face_bytes(dim: int, mips: int, bpb: int = 16) -> int:
    return cube_bytes(dim, mips, bpb, faces=1)


def cube_dim_for(nbytes: int, mips: int, bpb: int = 16, faces: int = 6):
    """The face dimension whose `mips`-level cube is exactly `nbytes`, or None."""
    if mips <= 0 or nbytes <= 0:
        return None
    d = 1
    while d <= 8192:
        if cube_bytes(d, mips, bpb, faces) == nbytes:
            return d
        d *= 2
    return None


def cube_dims_for(nbytes: int, bpb: int = 16, faces: int = 6):
    """`(dim, mips)` for a byte budget, preferring a FULL mip chain. None on miss."""
    d = 1
    while d <= 8192:
        full = d.bit_length()          # 256 -> 9
        for mips in range(full, 0, -1):
            if cube_bytes(d, mips, bpb, faces) == nbytes:
                return d, mips
        d *= 2
    return None, None


# =============================================================================
# DDS writers
# =============================================================================

DDS_MAGIC = b"DDS "
DDS_FOURCC_DX10 = b"DX10"
_DDSD = 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000          # CAPS|HEIGHT|WIDTH|PIXELFORMAT|LINEARSIZE
_DDSD_MIPMAPCOUNT = 0x20000
_DDSCAPS_TEXTURE = 0x1000
_DDSCAPS_COMPLEX = 0x8
_DDSCAPS_MIPMAP = 0x400000
_DDSCAPS2_CUBEMAP_ALLFACES = 0xFE00                 # CUBEMAP | +X..-Z
_D3D10_RESOURCE_DIMENSION_TEXTURE2D = 3
_DDS_RESOURCE_MISC_TEXTURECUBE = 0x4


def _dds_header(width: int, height: int, mips: int, dxgi: int, *,
                linear_size: int, caps2: int = 0, misc: int = 0,
                array_size: int = 1) -> bytes:
    hdr = bytearray(128)
    hdr[0:4] = DDS_MAGIC
    struct.pack_into("<I", hdr, 4, 124)
    flags = _DDSD | (_DDSD_MIPMAPCOUNT if mips > 1 else 0)
    struct.pack_into("<I", hdr, 8, flags)
    struct.pack_into("<I", hdr, 12, height)
    struct.pack_into("<I", hdr, 16, width)
    struct.pack_into("<I", hdr, 20, linear_size)
    struct.pack_into("<I", hdr, 28, max(1, mips))
    struct.pack_into("<I", hdr, 76, 32)             # DDS_PIXELFORMAT.dwSize
    struct.pack_into("<I", hdr, 80, 0x4)            # DDPF_FOURCC
    hdr[84:88] = DDS_FOURCC_DX10
    caps = _DDSCAPS_TEXTURE
    if mips > 1:
        caps |= _DDSCAPS_COMPLEX | _DDSCAPS_MIPMAP
    if caps2:
        caps |= _DDSCAPS_COMPLEX
    struct.pack_into("<I", hdr, 108, caps)
    struct.pack_into("<I", hdr, 112, caps2)
    dx10 = struct.pack("<IIIII", dxgi, _D3D10_RESOURCE_DIMENSION_TEXTURE2D,
                       misc, max(1, array_size), 0)
    return bytes(hdr) + dx10


def cube_dds_bytes(payload: bytes, dim: int, mips: int, *,
                   engine_format: int = ETEXTUREFORMAT_BC6H_UF16) -> bytes:
    """One probe's cube payload -> a DX10 **cubemap** DDS, byte-for-byte.

    The on-disk order is already DDS/D3D order — face-major with a full mip
    chain inside each face — so the payload is copied unmodified.  ⚠ The
    face-major reading is `inferred` from the D3D11 subresource rule
    (`sub = mip + face * mipcount`); nothing in this tree has read the loader.
    `export-validated` only insofar as the resulting image decodes coherently.
    """
    bpb = block_bytes(engine_format)
    want = cube_bytes(dim, mips, bpb)
    if len(payload) != want:
        raise ValueError(f"cube payload is {len(payload)} B, expected {want} for "
                         f"{dim}^2 x {mips} mips x 6 faces")
    dxgi = ETEXTUREFORMAT_TO_DXGI.get(engine_format, DXGI_BC6H_UF16)
    linear = ((dim + BLOCK_DIM - 1) // BLOCK_DIM) ** 2 * bpb
    hdr = _dds_header(dim, dim, mips, dxgi, linear_size=linear,
                      caps2=_DDSCAPS2_CUBEMAP_ALLFACES,
                      misc=_DDS_RESOURCE_MISC_TEXTURECUBE, array_size=1)
    return hdr + payload


def cube_strip_bytes(payload: bytes, dim: int, mips: int, *,
                     engine_format: int = ETEXTUREFORMAT_BC6H_UF16) -> bytes:
    """The six faces' MIP-0 blocks, concatenated: a `dim` x `6*dim` block image.

    Why this exists: **Blender has no cube-texture image type**, so a DX10
    cubemap DDS is not loadable in the shader graph.  A block-compressed image
    is stored 4x4-block-row-major, and each face's mip 0 is exactly `dim/4`
    contiguous block rows — so stacking the six faces vertically is a pure
    concatenation, no re-tiling.  The result is a legal single-mip 2D DDS that
    Blender decodes with its own BC6H decoder; `probe_builder` then resamples it
    to an equirectangular environment image.

    Face order is the DDS/D3D order: +X, -X, +Y, -Y, +Z, -Z.
    """
    bpb = block_bytes(engine_format)
    fb = face_bytes(dim, mips, bpb)
    mip0 = face_mip_offsets(dim, mips, bpb)[0][1]
    if len(payload) != fb * 6:
        raise ValueError(f"cube payload is {len(payload)} B, expected {fb * 6}")
    out = b"".join(payload[f * fb: f * fb + mip0] for f in range(6))
    dxgi = ETEXTUREFORMAT_TO_DXGI.get(engine_format, DXGI_BC6H_UF16)
    hdr = _dds_header(dim, dim * 6, 1, dxgi, linear_size=mip0 * 6)
    return hdr + out


CUBE_FACE_ORDER = ("+X", "-X", "+Y", "-Y", "+Z", "-Z")


# =============================================================================
# cube <-> direction <-> equirectangular
# =============================================================================
# Why any of this is here: Blender has **no cube-texture image type**, so the
# only way to put a shipped IBL cube into a shader graph is to resample it into
# an equirectangular environment image.  Keeping the maths in the pure core
# means it is unit-tested on synthetic faces instead of eyeballed in Blender.
#
# `direction_to_face_uv` is the D3D/DDS cube convention (D3D11 spec,
# `TextureCube` addressing).  `v == 0` is the TOP row of a face **as stored in
# the file**; a consumer whose pixel buffer is bottom-up has to say so.

def direction_to_face_uv(d):
    """Direction (game space) -> `(face, u, v)` in the D3D/DDS cube convention.

    `u`, `v` are in [0, 1]; `v == 0` is the face's first stored row.
    """
    x, y, z = d
    ax, ay, az = abs(x), abs(y), abs(z)
    if ax >= ay and ax >= az:
        if x > 0:
            face, sc, tc, ma = 0, -z, -y, ax
        else:
            face, sc, tc, ma = 1, z, -y, ax
    elif ay >= az:
        if y > 0:
            face, sc, tc, ma = 2, x, z, ay
        else:
            face, sc, tc, ma = 3, x, -z, ay
    else:
        if z > 0:
            face, sc, tc, ma = 4, x, -y, az
        else:
            face, sc, tc, ma = 5, -x, -y, az
    if ma == 0.0:
        return face, 0.5, 0.5
    return face, 0.5 * (sc / ma + 1.0), 0.5 * (tc / ma + 1.0)


def face_uv_to_direction(face: int, u: float, v: float):
    """Inverse of `direction_to_face_uv` (not normalised)."""
    sc = 2.0 * u - 1.0
    tc = 2.0 * v - 1.0
    return {
        0: (1.0, -tc, -sc),
        1: (-1.0, -tc, sc),
        2: (sc, 1.0, tc),
        3: (sc, -1.0, -tc),
        4: (sc, -tc, 1.0),
        5: (-sc, -tc, -1.0),
    }[face]


def equirect_to_direction(u: float, v: float):
    """Cycles' equirectangular mapping, in BLENDER world space (Z up).

    Matches `equirectangular_to_direction` in Cycles' `kernel/geom/…`:
    `phi = pi*(1 - 2u)`, `theta = pi*(v - 0.5)`,
    `d = (cos t * cos p, cos t * sin p, sin t)`.  `engine-confirmed` only to the
    extent that the round-trip through `ShaderNodeTexEnvironment` is verified by
    `tests/blender_probe_probe.py`; the formula itself is from the renderer.
    """
    import math
    phi = math.pi * (1.0 - 2.0 * u)
    theta = math.pi * (v - 0.5)
    ct = math.cos(theta)
    return (ct * math.cos(phi), ct * math.sin(phi), math.sin(theta))


#: game `(x, y, z)` -> Blender `(x, -z, y)`; a pure +90 deg X rotation, det +1.
#: `AXIS_CALIBRATION.md` — this module must never introduce a second convention.
def rad_to_blender(v):
    return (v[0], -v[2], v[1])


def blender_to_rad(v):
    """Inverse of `rad_to_blender`: Blender `(X, Y, Z)` -> game `(X, Z, -Y)`."""
    return (v[0], v[2], -v[1])


def resample_cube_to_equirect(sample, width: int, height: int, *,
                              components: int = 4, alpha: float = 1.0) -> list:
    """Cube -> a flat, row-major RGBA float list for a Blender image.

    `sample(face, u, v) -> (r, g, b)` where `u`/`v` are in [0, 1] with `v == 0`
    the face's first stored row.  Nearest-neighbour by construction: the caller's
    `sample` decides how a texel is fetched, so filtering is its business.

    Row 0 of the output is the BOTTOM row, which is Blender's `image.pixels`
    order.
    """
    out = [0.0] * (width * height * components)
    i = 0
    for py in range(height):
        v = (py + 0.5) / height          # 0 = bottom, matches Blender pixel order
        for px in range(width):
            u = (px + 0.5) / width
            d = blender_to_rad(equirect_to_direction(u, v))
            face, fu, fv = direction_to_face_uv(d)
            r, g, b = sample(face, fu, fv)
            out[i] = r
            out[i + 1] = g
            out[i + 2] = b
            if components > 3:
                out[i + 3] = alpha
            i += components
    return out


# =============================================================================
# CGMeshData.probeidx
# =============================================================================

def read_mesh_probe_index(primary: bytes, meshes_data_off: int, mesh_index: int) -> int:
    """`CGMeshData.probeidx@0x50` for one mesh."""
    return struct.unpack_from(
        "<I", primary, meshes_data_off + mesh_index * MESH_STRIDE + M_PROBEIDX)[0]


def read_mesh_probe_indices(primary: bytes, meshes_data_off: int, count: int) -> list:
    """`CGMeshData.probeidx@0x50` for a whole mesh table."""
    return [read_mesh_probe_index(primary, meshes_data_off, i) for i in range(count)]


# --- the STATIC-INSTANCE side -----------------------------------------------
# `SGPackedInstanceData` (the field names are the engine's own, `name-only`)
# packs the per-INSTANCE equivalent of `CGMeshData.probeidx` into one u32
# at +0x1c, named `probeidx_lmask_dlmask`.  `le_static_scatter` already decodes
# the rest of that 44-byte record; this is the field it left alone.
#
# ★ Bit layout `measured` on **960 sampled station_front instances** across the
# 40 busiest of its 1,050 mesh-types (a local working file,
# archive `942c829457a04a62`):
#     bits  0.. 7  probeidx        observed {0, 1, 3, 4, 6}; 960/960 < 16, the
#                                  probe count of that archive's probe set
#     bits  8..15  lightmask       observed 1 on 960/960
#     bits 16..23  dirlightmask    observed 1 on 960/960
#     bits 24..31  0 on 960/960
# The upper 24 bits read a constant `0x101`, i.e. exactly bit 8 and bit 16 set —
# which is what pins the two masks to byte boundaries and therefore the probe
# field to 8 bits.  ⚠ Only mask VALUE 1 was observed, so the widths are pinned
# by those two bit positions, not by a spread of values.
INSTANCE_PROBEFIELD_OFF = 0x1C
INSTANCE_PROBE_MASK = 0xFF
INSTANCE_LIGHTMASK_SHIFT = 8
INSTANCE_DIRLIGHTMASK_SHIFT = 16


def unpack_instance_probe_field(v: int) -> dict:
    """`SGPackedInstanceData.probeidx_lmask_dlmask` -> its three fields."""
    v = int(v)
    return {
        "probe_index": v & INSTANCE_PROBE_MASK,
        "light_mask": (v >> INSTANCE_LIGHTMASK_SHIFT) & 0xFF,
        "dirlight_mask": (v >> INSTANCE_DIRLIGHTMASK_SHIFT) & 0xFF,
        "spare": (v >> 24) & 0xFF,
    }


def instance_probe_index(record: bytes, off: int = 0) -> int:
    """The probe index of one `SGPackedInstanceData` record at `record[off:]`."""
    v = struct.unpack_from("<I", record, off + INSTANCE_PROBEFIELD_OFF)[0]
    return v & INSTANCE_PROBE_MASK


def has_probe(probe_index) -> bool:
    """True when a mesh's `probeidx` names a probe at all."""
    if probe_index is None:
        return False
    try:
        v = int(probe_index)
    except (TypeError, ValueError):
        return False
    return v != PROBE_INDEX_NONE and v >= 0


def resolve_mesh_probe(resource, probe_index):
    """`(resource, mesh probeidx)` -> the `ProbePoint`, or None.

    None for the null sentinel and for an index the resource does not hold —
    an out-of-range index is a real finding and must not be silently clamped.
    """
    if resource is None or not has_probe(probe_index):
        return None
    i = int(probe_index)
    if i >= resource.n_probes:
        return None
    return resource.points[i]


# =============================================================================
# Blender-facing spec / manifest
# =============================================================================

def probe_file_name(probe: int, *, strip: bool = False) -> str:
    return f"probe_{probe:02d}{'_strip' if strip else ''}.dds"


def build_probe_spec(resource, probe: int, files: dict | None = None) -> dict:
    """One probe -> the dict `probe_builder.wire_probe` consumes.

    `files` : {probe index -> {"cube": path, "strip": path}}, package-relative.
    A probe with no extracted bytes still gets an entry with empty paths, so a
    missing extraction is visible rather than silent.
    """
    if resource is None or probe < 0 or probe >= resource.n_probes:
        return {}
    f = (files or {}).get(probe, {})
    mip = resource.mipcount(probe)
    dim = resource.cube_dim(probe)
    bb = resource.bounding_boxes[probe] if probe < len(resource.bounding_boxes) else None
    start, end = resource.probe_gpu_range(probe)
    return {
        "index": probe,
        "position": list(resource.points[probe].point),
        "mipcount": mip,
        "cube_dim": dim,
        "texture_format": resource.texture_format,
        "texture_format_name": resource.format_name,
        "colorspace": COLORSPACE_PROBE,
        "gpu_offset": start,
        "gpu_bytes": end - start,
        "cube_file": f.get("cube", ""),
        "strip_file": f.get("strip", ""),
        "mip_normalizations": bb.mip_normalizations(mip) if bb else [],
        "obb_min_world": list(bb.world_obb_corner("min")) if bb else [],
        "obb_max_world": list(bb.world_obb_corner("max")) if bb else [],
        "boxes": [b.as_dict() for b in resource.boxes_for_probe(probe)],
    }


def manifest_probe_section(resource, files: dict | None = None, *,
                           resource_name=None, gpu_present: bool = False) -> dict:
    """The `manifest["reflection_probes"]` section for ONE package.

    LEVEL-scoped, not per-object — one probe set serves every mesh-list of a
    scene, exactly like `le_mesh.lightmap.manifest_lightmap_section`.  The
    per-object binding stays on each object's `probe_index`.

    `{}` for `resource is None`, so a caller can `or {}` without a branch.
    """
    if resource is None:
        return {}
    return {
        "resource": (f"{int(resource_name):016x}" if resource_name is not None else None),
        "count": resource.n_probes,
        "box_count": len(resource.boxes),
        "sphere_count": resource.n_spheres,
        "texture_format": resource.texture_format,
        "texture_format_name": resource.format_name,
        "gpumemsize": resource.gpumemsize,
        "gpu_present": bool(gpu_present),
        "colorspace": COLORSPACE_PROBE,
        "probes": [build_probe_spec(resource, i, files) for i in range(resource.n_probes)],
    }
