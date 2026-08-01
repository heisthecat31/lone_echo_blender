"""`CGLightMapResourceWin7` -> baked-lightmap texture sets, and the mesh binding.

Pure stdlib. No Oodle, no archive, no `bpy` — so this is unit-testable outside
Blender and importable unchanged inside it.

What this module covers
-----------------------
1. `SLightMapTextureNames` (stride `0x28`) -> the five texture name-hashes of one
   lightmap set.
2. The on-disk container: the resource slice is a **compact** `[u32 count][count x
   0x28]` array at slice offset 0 — not a `CTable` memory image and not
   CResource-prefixed. Holds on **98/98** shipped resources across two archives.
3. `CGMeshData.lightmapindex@0x6c` / `lmsliceindex@0x70` / `numlobes@0x74` ->
   a **direct row index** into that table, a **lightmap page** (texture-array
   slice), and a lobe count.
4. The **join**: which `CGLightMapResourceWin7` a given mesh indexes into. See
   `lightmap_resource_name_for_scene` / `dynamic_lightmapsid` and "The join".
5. A Blender-facing spec dict (`build_lightmap_spec`) that names, per role, the
   texture file, the expected DXGI format and the colour space to load it in.

⚠ Status: this decode is complete and tested, but the importer does **not** yet
wire a lightmap onto a mesh end to end — see `docs/LIGHTING.md`.

What was measured
-----------------
Measured on the shipped corpus (bridge `0703fd2acd5803e9`, station_front
`942c829457a04a62`):

* **Container.** `size == 4 + count * 0x28` holds **exactly** for all 81 bridge
  + all 17 station_front resources — **98/98, zero failures**.  Observed shapes:
  `[u32 1]` (44 B) x 96, `[u32 7]` (284 B) x 1 (the bridge master), `[u32 10]`
  (404 B) x 1 (the station_front master).
* **Row layout.** `lightmapid@0, ao0id@8, ao1id@0x10, dlocclusionid@0x18,
  poocclusionid@0x20` — five `CSymbol64`.  Every populated slot resolves to a
  real in-archive `CGTextureResource` whose DXGI format matches its role, on
  **41 populated rows** across the two archives (113 rows read in total).
* **The station_front master row 1** (the only populated row of its 10):
  `lightmapid 0178fa39b1b95d2f` DXGI 95 `BC6H_UF16` 1024^2 **arraysize 65**;
  `ao0id 81a8fcf99b655a42` / `ao1id …a43` DXGI 83 `BC5_UNORM` 1024^2
  **arraysize 13**; `dlocclusionid bd2f79f78fb557f1` and
  `poocclusionid bd2f79f78fb543f1` DXGI 80 `BC4_UNORM` 1024^2 **arraysize 13**.
  ⛔ `dlocclusionid` and `poocclusionid` are **different hashes**; an earlier
  note that both slots held one texture was reading a truncated hash.

The join
--------
`CGScene` owns *five* sibling resource instances — meshlist, visresource,
reflectionresource, **lightmapresource**, staticinstresource — and `CGSceneData`
carries **no** id field for any of them. On disk the siblings are addressed **by
the scene's own resource name hash**:

* bridge: 54 `CGSceneResourceWin7` / 54 `CGMeshListResourceWin7` / 54
  `CGStaticInstanceResourceWin7` share one name-hash set, and **54 of the 81**
  `CGLightMapResourceWin7` carry a name from that same set (1:1).
* the remaining **27** are named *explicitly*: `SGDynamicInstancesData` ends with
  `lightmapsid` (`CSymbol64`), which lands in the **last 8 bytes** of a
  `CGDynamicInstanceResourceWin7` slice.  All 33 bridge dynamic instances were
  read: **27 name an in-archive lightmap resource, 6 hold the 0xff..ff sentinel,
  0 garbage** — and those 27 are *exactly* the 27 lightmap resources not co-named
  with a mesh-list.  54 + 27 = 81, disjoint, no leftovers.
* the co-name reading is what predicts populated-ness: of the 51 bridge
  mesh-lists that parse, the **9** with lightmapped meshes have a populated
  co-named table and the **42** without have an all-null one — **51/51, no
  exceptions**.  (36 populated = 9 co-named + 27 dynamic-named, exactly.)

`lightmapindex` is a DIRECT row index
-------------------------------------
The station_front master table has 10 rows of which **only row 1** is populated,
and every one of the **1049** lightmapped meshes bound to it carries
`lightmapindex == 1`.  An index over *populated rows only* would require 0.
Across 64 parsed mesh-lists + the one populated static-instance inline
mesh-list (**1221 meshes** = 121 + 50 + 1050),
`lightmapindex >= len(co-named table)` happened **0 times**.

`lmsliceindex` is the lightmap PAGE
----------------------------------
Station_front's 1050 static meshes use `lmsliceindex` values **0..12, all 13
present** — exactly the `arraysize` of the ao0/ao1/dlocclusion/poocclusion
arrays (13).  The shipped shaders' reflection data binds
`k_ambient_lightmap_ao0/ao1` with `Dimension == 5 == TEXTURE2DARRAY`, so the
slice index is the array index.

The colour (`lightmapid`) array is **65 = 13 x 5** slices: 5 per page, laid out
**page-major**, `colour_slice = lmsliceindex * 5 + k`.  Validated on the shipped
68 MB DDS: grouping every slice with its 4 run-neighbours beats every out-of-run
slice **65/65** by background-block overlap, while the lobe-major alternative
(stride 13) scores **0/65**; colour page *p* also matches AO slice *p* on 9 of 13
pages (the other 4 are inconclusive, not contradictory).  See
`colour_slice_indices`.

Unresolved
----------
* **What the 5th colour slice per page is.**  `CGMeshData.numlobes@0x74` reads
  **4 on every shipped mesh measured (1221/1221, both archives)**, so 5 is
  `numlobes + 1`.  The slot `lightmapid` fills is a **lobe basis**, and the
  engine's basis enum offers `eSH4Basis=0, eSH9Basis=1, eSG5Basis=2, …` — so
  "4 lobes + 1 extra" and "a 5-lobe SG bake whose `numlobes` field means
  something else" both fit.  Nothing measured separates them: within each run of
  5 no slice is structurally the odd one out (the largest-background-count slice
  is index 0 in only 6 of 13 runs).
* The four AO channels' semantics.  The runtime slot names are known —
  `btextures, ao0, ao1, dirlightocclusion, punctualocclusion` — but what the two
  BC5s encode is still not decoded.  Resolving it needs the ambient-lightmap
  sampling function, not more bytes.
* No colour/lobe-basis sampler name appears in the shipped shaders' reflection
  data (only `k_ambient_lightmap_ao0/ao1`), so how the lobe-basis array reaches
  the shader is unresolved.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

# --- SLightMapTextureNames ---------------------------------------------------
STRIDE = 0x28                 # 40 B = 5 x CSymbol64
F_LIGHTMAPID = 0x00
F_AO0 = 0x08
F_AO1 = 0x10
F_DLOC = 0x18
F_POOCC = 0x20

#: field order on disk; also the key order of `LightMapSet.textures`
ROLES = ("lightmapid", "ao0", "ao1", "dloc", "poocc")
_ROLE_OFFSETS = {
    "lightmapid": F_LIGHTMAPID,
    "ao0": F_AO0,
    "ao1": F_AO1,
    "dloc": F_DLOC,
    "poocc": F_POOCC,
}

#: our short role key -> the engine's own `SLightMapTextureNames` field name.
#: Our keys are kept for API stability; these are the real names and are what any
#: future doc should quote.
ROLE_STRUCT_FIELD = {
    "lightmapid": "lightmapid",
    "ao0": "ao0id",
    "ao1": "ao1id",
    "dloc": "dlocclusionid",
    "poocc": "poocclusionid",
}

#: the same five slots as the *runtime* struct names them: `btextures, ao0, ao1,
#: dirlightocclusion, punctualocclusion`, whose resource-side counterparts are
#: `lobebasis, ao0, ao1, dlocclusion, pocclusion`.  Note that slot 0 — the one
#: this module's `lightmapid` names, and the only HDR one — is a **lobe basis**,
#: not a plain colour map.
ROLE_RUNTIME_NAME = {
    "lightmapid": "lobebasis",
    "ao0": "ao0",
    "ao1": "ao1",
    "dloc": "dirlightocclusion",
    "poocc": "punctualocclusion",
}

#: an unset CSymbol64 slot is all-ones (observed on 9 of the 10 master rows)
NULL_HASH = 0xFFFFFFFFFFFFFFFF

#: `CGMeshData.lightmapindex` sentinel for "this mesh is not lightmapped"
LIGHTMAP_INDEX_NONE = 0xFFFFFFFF
#: `CGMeshData.lmsliceindex` sentinel
LM_SLICE_NONE = 0xFFFFFFFF

#: guard against a mis-pointed blob decoding as a giant table
MAX_TABLE_ENTRIES = 4096

# --- the three CGMeshData fields this module consumes ------------------------
# All three offsets are read out of shipped bytes: 1221 shipped meshes decode to
# in-range values at these spots. `le_mesh.meshlist` reads all three.
M_LIGHTMAPINDEX = 0x6C      # u32  row index into the bound lightmap table
M_LMSLICEINDEX = 0x70       # u32  lightmap PAGE == texture-array slice
M_NUMLOBES = 0x74           # u32  lobe count; 4 on every shipped mesh measured

#: The engine's lighting-basis enum. Recorded because the `numlobes == 4` /
#: `5 colour slices per page` mismatch has to be read against it, not against a
#: guess.
EBASIS_TYPE = {
    0: "eSH4Basis", 1: "eSH9Basis", 2: "eSG5Basis",
    3: "eSG6Basis", 4: "eSG9Basis", 5: "eSG12Basis",
    6: "eMaxBasis", 7: "eBasisNone",
}

#: `numlobes` on every shipped mesh measured — 1221 of 1221 across the bridge and
#: station_front.  A different value in some other archive would be a real
#: finding, so callers should report rather than assume it.
OBSERVED_NUMLOBES = 4

# --- DXGI formats the roles are expected to carry ----------------------------
DXGI_BC4_UNORM = 80
DXGI_BC5_UNORM = 83
DXGI_BC6H_UF16 = 95

#: role -> the DXGI format observed on the one fully-resolved shipped row
#: (station_front).  Treated as an *expectation*, not an assertion —
#: `build_lightmap_spec` reports a mismatch instead of failing.
ROLE_EXPECTED_DXGI = {
    "lightmapid": DXGI_BC6H_UF16,
    "ao0": DXGI_BC5_UNORM,
    "ao1": DXGI_BC5_UNORM,
    "dloc": DXGI_BC4_UNORM,
    "poocc": DXGI_BC4_UNORM,
}

# --- colour management -------------------------------------------------------
# Measured in Blender 5.1.1 by RNA probe + texel sample + EEVEE render
# (`tests/blender_lightmap_probe.py`):
#   * Blender 5.1.1 loads a DXGI-95 BC6H_UF16 DDS natively, as a FLOAT image, and
#     its DDS loader auto-assigns colour space 'Linear Rec.709'.
#   * 'Linear Rec.709' and 'Non-Color' are numerically IDENTICAL under the stock
#     OCIO config (both return the exact on-disk decoded texel, 0.500488 for the
#     probe's synthetic block, through `image.pixels` AND through an EEVEE render).
#   * 'sRGB' returns 0.214478 for the same texel — the silent double-gamma this
#     module exists to prevent.
# We pick 'Linear Rec.709' over 'Non-Color' for the HDR colour map because the
# lightmap really is linear light in Rec.709/sRGB primaries: under a non-default
# OCIO config (e.g. ACES) 'Linear Rec.709' still converts correctly, whereas
# 'Non-Color' would be left raw in the wrong primaries.  It is also what the
# loader picks by itself, so we are confirming the loader rather than fighting it.
COLORSPACE_LIGHTMAP = "Linear Rec.709"
#: the AO / occlusion maps are data, not colour.
COLORSPACE_DATA = "Non-Color"
#: fallback if a Blender build does not expose 'Linear Rec.709'
COLORSPACE_LIGHTMAP_FALLBACK = "Non-Color"

ROLE_COLORSPACE = {
    "lightmapid": COLORSPACE_LIGHTMAP,
    "ao0": COLORSPACE_DATA,
    "ao1": COLORSPACE_DATA,
    "dloc": COLORSPACE_DATA,
    "poocc": COLORSPACE_DATA,
}

#: the vertex UV set the lightmap is sampled with.  Measured across a 121-object
#: fixture export: every mesh that carries a non-null lightmapindex also carries
#: `uv1`, and its uv1 occupies a sub-rectangle of [0,1] (u 0..0.999, v 0..0.467)
#: while uv0 spans the full range — the signature of an atlas slot.
UV_LAYER = "uv1"


def colorspace_for_role(role: str) -> str:
    """Blender `image.colorspace_settings.name` for a lightmap-set role."""
    return ROLE_COLORSPACE.get(role, COLORSPACE_DATA)


# =============================================================================
# table decode
# =============================================================================

@dataclass
class LightMapSet:
    """One `SLightMapTextureNames` row: the five textures of one lightmap set."""
    index: int
    lightmapid: int
    ao0: int
    ao1: int
    dloc: int
    poocc: int

    @property
    def is_null(self) -> bool:
        """True when every slot is the all-ones sentinel (an unused row)."""
        return all(getattr(self, r) == NULL_HASH for r in ROLES)

    @property
    def has_color(self) -> bool:
        """True when the HDR colour map — the only load-bearing slot — is set."""
        return self.lightmapid != NULL_HASH

    @property
    def textures(self) -> dict:
        """{role -> 16-hex hash} for the populated slots only, in field order."""
        out = {}
        for r in ROLES:
            v = getattr(self, r)
            if v != NULL_HASH:
                out[r] = f"{v:016x}"
        return out

    def as_dict(self) -> dict:
        return {
            "index": self.index,
            "is_null": self.is_null,
            "textures": self.textures,
        }


def decode_texture_names(blob: bytes, off: int = 0, index: int = 0) -> LightMapSet:
    """Decode ONE 0x28-byte `SLightMapTextureNames` record at `blob[off:]`."""
    if len(blob) - off < STRIDE:
        raise ValueError(
            f"SLightMapTextureNames needs {STRIDE} bytes at off {off}, "
            f"have {len(blob) - off}")
    vals = struct.unpack_from("<5Q", blob, off)
    return LightMapSet(index, *vals)


def parse_lightmap_table(blob: bytes, off: int = 0, *, strict: bool = True) -> list:
    """Decode a `CGLightMapResourceWin7` slice -> list[LightMapSet].

    On-disk form: `[u32 count][count x 0x28]` at slice offset 0.  There is no
    CResource header and no `CTable` memory image.

    `strict=False` clamps `count` to what the buffer actually holds instead of
    raising — for probing a slice whose boundaries are not yet certain.
    """
    if len(blob) - off < 4:
        raise ValueError("lightmap resource slice is shorter than its count prefix")
    count = struct.unpack_from("<I", blob, off)[0]
    avail = (len(blob) - off - 4) // STRIDE
    if count > MAX_TABLE_ENTRIES:
        if strict:
            raise ValueError(
                f"implausible lightmap table count {count} "
                f"(max {MAX_TABLE_ENTRIES}) — wrong slice offset?")
        count = min(count, MAX_TABLE_ENTRIES)
    if count > avail:
        if strict:
            raise ValueError(
                f"lightmap table declares {count} entries but only {avail} fit "
                f"in {len(blob) - off} bytes")
        count = avail
    return [decode_texture_names(blob, off + 4 + i * STRIDE, i) for i in range(count)]


def table_size(count: int) -> int:
    """Bytes a `count`-entry lightmap table occupies on disk (`4 + count*0x28`)."""
    return 4 + count * STRIDE


# =============================================================================
# mesh binding
# =============================================================================

@dataclass
class LightMapBinding:
    """A mesh's resolved lightmap: which set, which texture-array slice."""
    lightmap_index: int
    slice_index: int
    texture_set: LightMapSet

    @property
    def has_color(self) -> bool:
        return self.texture_set.has_color

    def as_dict(self) -> dict:
        return {
            "lightmap_index": self.lightmap_index,
            "slice_index": self.slice_index,
            "texture_set": self.texture_set.as_dict(),
        }


# =============================================================================
# the join:  which CGLightMapResourceWin7 does this mesh index into?
# =============================================================================
# See the module docstring's "The join". Two mechanisms, disjoint, and together
# they account for every shipped lightmap resource in the bridge (54 + 27 == 81,
# no leftovers):
#
#   scene-owned geometry  ->  SIBLING-BY-NAME.  `CGScene` holds its lightmap
#       resource next to its meshlist / staticinstresource / reflectionresource
#       siblings, and `CGSceneData` stores no id for any of them: they are all
#       addressed by the scene's own resource name hash.
#
#   dynamic instances     ->  EXPLICIT.  `SGDynamicInstancesData` ends with
#       `CSymbol64 lightmapsid`, which serialises as the LAST 8 BYTES of a
#       `CGDynamicInstanceResourceWin7` slice.

#: `SGDynamicInstancesData.lightmapsid` is the struct's final field, so on disk
#: it is the last 8 bytes of the slice.
DYNAMIC_LIGHTMAPSID_TAIL = 8


def lightmap_resource_name_for_scene(scene_name_hash: int) -> int:
    """The `CGLightMapResourceWin7` name hash a scene/mesh-list binds to.

    It is the scene's *own* resource name hash — mesh-list, scene, static-instance
    and lightmap resources of one scene all share it.  Measured: 54/54 in the
    bridge, 16/16 in station_front; and of the 51 bridge mesh-lists that parse,
    the 9 with lightmapped meshes are exactly the 9 whose co-named lightmap table
    is populated (42/42 unlit ones are all-null).

    This is deliberately an identity function: it exists so callers name the
    mechanism instead of open-coding "the same hash" and losing the reasoning.
    """
    return int(scene_name_hash)


def dynamic_lightmapsid(slice_bytes: bytes):
    """`SGDynamicInstancesData.lightmapsid` -> lightmap resource name, or None.

    Reads the last 8 bytes of a `CGDynamicInstanceResourceWin7` primary slice.
    Returns None for the `0xff..ff` "no lightmap" sentinel and for a slice too
    short to hold the field.

    Measured over all 33 dynamic-instance resources in the bridge: 27 yield a
    hash that is an in-archive `CGLightMapResourceWin7` name and 6 yield the
    sentinel — 33/33 accounted for, 0 garbage.  Those 27 are exactly the 27
    lightmap resources that are *not* co-named with a mesh-list.
    """
    if slice_bytes is None or len(slice_bytes) < DYNAMIC_LIGHTMAPSID_TAIL:
        return None
    v = struct.unpack_from("<Q", slice_bytes, len(slice_bytes) - DYNAMIC_LIGHTMAPSID_TAIL)[0]
    return None if v == NULL_HASH else v


# =============================================================================
# lmsliceindex -> texture-array slices
# =============================================================================

def colour_slices_per_page(colour_arraysize: int, ao_arraysize: int) -> int:
    """How many `lightmapid` array slices belong to one lightmap page.

    Derived from the two `CGTextureResourceData.arraysize@0xd0` values rather
    than hard-coded: station_front ships 65 colour slices against 13 AO slices,
    so 5.  Raises when the division is not exact — a non-integer ratio would
    mean this whole model is wrong, and that must be loud.
    """
    if ao_arraysize <= 0:
        raise ValueError(f"ao arraysize must be positive, got {ao_arraysize}")
    if colour_arraysize % ao_arraysize:
        raise ValueError(
            f"colour arraysize {colour_arraysize} is not a whole multiple of the "
            f"page count {ao_arraysize} — the page model does not hold here")
    return colour_arraysize // ao_arraysize


def colour_slice_indices(lm_slice_index, per_page: int) -> list:
    """Lightmap page -> the `lightmapid` array slices that make up that page.

    **Page-major**: page `p` owns slices `[p*per_page, (p+1)*per_page)`.
    Validated on the shipped 68 MB `0178fa39b1b95d2f.dds` (BC6H_UF16, 1024^2,
    arraysize 65): each slice's four run-neighbours overlap it more than any
    out-of-run slice does, **65/65**; the lobe-major alternative (stride 13)
    scores **0/65**.

    Returns `[]` when the mesh has no page (`0xffffffff`).
    """
    if lm_slice_index is None:
        return []
    try:
        p = int(lm_slice_index)
    except (TypeError, ValueError):
        return []
    if p == LM_SLICE_NONE or p < 0 or per_page <= 0:
        return []
    return [p * per_page + k for k in range(per_page)]


def is_lightmapped(lightmap_index) -> bool:
    """True when `CGMeshData.lightmapindex` names a lightmap set at all.

    Shipped meshes carry 0xffffffff for "none" (106 of the 121 objects in one
    fixture export; the other 15 carry 0).
    """
    if lightmap_index is None:
        return False
    try:
        v = int(lightmap_index)
    except (TypeError, ValueError):
        return False
    return v != LIGHTMAP_INDEX_NONE and v >= 0


def resolve(table: list, lightmap_index, lm_slice_index=LM_SLICE_NONE):
    """(table, mesh's lightmapindex, lmsliceindex) -> LightMapBinding or None.

    Returns None when the mesh is not lightmapped, when the index is out of
    range for the table, or when the row it names is a null row.

    The index is a **direct row index**, not an index over the populated rows
    only: the station_front master table has 10 rows with only row **1**
    populated, and all 1049 lightmapped meshes bound to it carry
    `lightmapindex == 1`; the populated-only reading would require 0.  Across
    1221 shipped meshes, `lightmapindex >= len(table)` never occurred.  `table`
    must be the table of the resource the mesh's scene binds to — see
    `lightmap_resource_name_for_scene` / `dynamic_lightmapsid`.
    """
    if not is_lightmapped(lightmap_index):
        return None
    idx = int(lightmap_index)
    if idx >= len(table):
        return None
    row = table[idx]
    if row.is_null:
        return None
    slice_index = LM_SLICE_NONE
    if lm_slice_index is not None:
        try:
            slice_index = int(lm_slice_index)
        except (TypeError, ValueError):
            slice_index = LM_SLICE_NONE
    return LightMapBinding(idx, slice_index, row)


# =============================================================================
# Blender-facing spec
# =============================================================================

def build_lightmap_spec(binding, texture_files: dict | None = None, *,
                        texture_meta: dict | None = None,
                        uv_layer: str = UV_LAYER) -> dict:
    """A `LightMapBinding` -> the dict `lightmap_builder.wire_lightmap` consumes.

    `texture_files` : {texture-hash -> package-relative file path}. A role whose
        hash has no file is still reported, with `"file": ""`, so a missing
        extraction is visible rather than silent.
    `texture_meta`  : optional {texture-hash -> {"dxgi": int, "width": int,
        "height": int, "arraysize": int}}. When a role's DXGI format is present
        and disagrees with `ROLE_EXPECTED_DXGI`, the entry gets
        `"dxgi_unexpected": True` — a loud signal that the role mapping is wrong.

    Shape:
        {"lightmap_index", "slice_index", "uv_layer",
         "color": <entry or None>,       # the BC6H_UF16 HDR map
         "ao0", "ao1", "dloc", "poocc": <entry or None>,
         "roles": {role: entry}}
    where an entry is
        {"role", "hash", "file", "colorspace", "expected_dxgi", "dxgi",
         "dxgi_unexpected", "width", "height", "arraysize"}
    """
    if binding is None:
        return {}
    files = texture_files or {}
    meta = texture_meta or {}

    def entry(role: str):
        h = binding.texture_set.textures.get(role)
        if h is None:
            return None
        m = meta.get(h, {})
        dxgi = m.get("dxgi")
        exp = ROLE_EXPECTED_DXGI.get(role)
        return {
            "role": role,
            "hash": h,
            "file": files.get(h, ""),
            "colorspace": colorspace_for_role(role),
            "expected_dxgi": exp,
            "dxgi": dxgi,
            "dxgi_unexpected": bool(dxgi is not None and exp is not None and dxgi != exp),
            "width": m.get("width"),
            "height": m.get("height"),
            "arraysize": m.get("arraysize"),
        }

    roles = {r: entry(r) for r in ROLES}
    spec = {
        "lightmap_index": binding.lightmap_index,
        "slice_index": binding.slice_index,
        "uv_layer": uv_layer,
        "roles": roles,
        "color": roles["lightmapid"],
    }
    for r in ("ao0", "ao1", "dloc", "poocc"):
        spec[r] = roles[r]

    # `lmsliceindex` is the lightmap PAGE.  For the AO/occlusion arrays the page
    # IS the array slice (station_front: pages 0..12 vs arraysize 13, exact).
    # The colour/lobe-basis array carries several slices per page, page-major.
    spec["page_index"] = binding.slice_index
    spec["slices_per_page"] = None
    spec["color_slices"] = []
    colour, ao = roles["lightmapid"], roles["ao0"]
    if colour and ao and colour.get("arraysize") and ao.get("arraysize"):
        try:
            per = colour_slices_per_page(colour["arraysize"], ao["arraysize"])
        except ValueError:
            spec["slices_per_page"] = "unresolved"     # loud, never silent
        else:
            spec["slices_per_page"] = per
            spec["color_slices"] = colour_slice_indices(binding.slice_index, per)
    return spec


def spec_for_mesh(table: list, lightmap_index, lm_slice_index,
                  texture_files: dict | None = None, *,
                  texture_meta: dict | None = None,
                  uv_layer: str = UV_LAYER) -> dict:
    """`resolve` + `build_lightmap_spec` in one call. `{}` when not lightmapped."""
    return build_lightmap_spec(
        resolve(table, lightmap_index, lm_slice_index),
        texture_files, texture_meta=texture_meta, uv_layer=uv_layer)


# =============================================================================
# BC6H_UF16 synthetic stand-in  — TEST FIXTURE, NOT part of the on-disk decode
# =============================================================================
# This repository ships no game textures, so the tests cannot use a real lightmap
# DDS. These helpers build a *synthetic* BC6H_UF16 DDS with exactly-known texel
# values so the Blender load path, colour space and node graph can be tested end
# to end against ground truth. Point the same tests at a DDS extracted from your
# own game data to check real bytes.
#
# Only BC6H **mode 11** (5-bit mode field `0b00011`, one region, two 10-bit
# untransformed RGB endpoints, 63 index bits) is implemented, and only with both
# endpoints equal — which makes the block a constant colour regardless of the
# indices.  That is enough for a known-value fixture and nothing more.

_BC6H_MODE11 = 0b00011
BC6H_BLOCK_BYTES = 16
BC6H_BLOCK_DIM = 4
BC6H_ENDPOINT_BITS = 10


def bc6h_uf16_solid_block(qr: int, qg: int, qb: int) -> bytes:
    """A 16-byte BC6H_UF16 mode-11 block of one constant colour.

    `qr/qg/qb` are 10-bit quantised endpoint values; decode them with
    `bc6h_uf16_decode_endpoint` to get the float a decoder will produce.
    """
    for v in (qr, qg, qb):
        if not 0 <= v < (1 << BC6H_ENDPOINT_BITS):
            raise ValueError(f"BC6H endpoint {v} out of 10-bit range")
    bits = 0
    pos = 0

    def put(val: int, n: int) -> None:
        nonlocal bits, pos
        bits |= (val & ((1 << n) - 1)) << pos
        pos += n

    put(_BC6H_MODE11, 5)
    for v in (qr, qg, qb, qr, qg, qb):      # endpoint A then endpoint B, equal
        put(v, BC6H_ENDPOINT_BITS)
    put(0, 63)                              # all indices 0 -> endpoint A
    assert pos == 128
    return bits.to_bytes(BC6H_BLOCK_BYTES, "little")


def bc6h_uf16_decode_endpoint(q: int, bits: int = BC6H_ENDPOINT_BITS) -> float:
    """Reference decode of a BC6H_UF16 endpoint -> the float a decoder yields.

    Mirrors the D3D BC6H unsigned path: `Unquantize` then `FinishUnquantize`,
    the result being a half-float bit pattern.  Exact for the constant blocks
    `bc6h_uf16_solid_block` writes (both endpoints equal, so interpolation is a
    no-op).  Blender 5.1.1's own BC6H decoder returns bit-identical values for
    these blocks.
    """
    if bits >= 15:
        unq = q
    elif q == 0:
        unq = 0
    elif q == (1 << bits) - 1:
        unq = 0xFFFF
    else:
        unq = ((q << 16) + 0x8000) >> bits
    half_bits = (unq * 31) >> 6
    return struct.unpack("<e", struct.pack("<H", half_bits))[0]


def bc6h_quantise_for(value: float, bits: int = BC6H_ENDPOINT_BITS) -> int:
    """The 10-bit endpoint whose `bc6h_uf16_decode_endpoint` is nearest `value`."""
    n = 1 << bits
    best, best_err = 0, float("inf")
    for q in range(n):
        err = abs(bc6h_uf16_decode_endpoint(q, bits) - value)
        if err < best_err:
            best, best_err = q, err
    return best


DDS_MAGIC = b"DDS "
DDS_FOURCC_DX10 = b"DX10"


def write_bc6h_dds(path, width: int, height: int, block_q, *, arraysize: int = 1):
    """Write a synthetic single-mip BC6H_UF16 (DXGI 95) DDS.

    `block_q` is either a `(qr, qg, qb)` triple (uniform image) or a callable
    `(block_x, block_y) -> (qr, qg, qb)` so a test can build a gradient whose
    every texel value is known in closed form.
    """
    if width % BC6H_BLOCK_DIM or height % BC6H_BLOCK_DIM:
        raise ValueError("BC6H dimensions must be multiples of 4")
    bx, by = width // BC6H_BLOCK_DIM, height // BC6H_BLOCK_DIM
    getq = block_q if callable(block_q) else (lambda x, y: block_q)

    payload = bytearray()
    for _ in range(max(1, arraysize)):
        for y in range(by):
            for x in range(bx):
                payload += bc6h_uf16_solid_block(*getq(x, y))

    hdr = bytearray(128)
    hdr[0:4] = DDS_MAGIC
    struct.pack_into("<I", hdr, 4, 124)                     # dwSize
    # CAPS | HEIGHT | WIDTH | PIXELFORMAT | LINEARSIZE
    struct.pack_into("<I", hdr, 8, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000)
    struct.pack_into("<I", hdr, 12, height)
    struct.pack_into("<I", hdr, 16, width)
    struct.pack_into("<I", hdr, 20, bx * by * BC6H_BLOCK_BYTES)   # pitchOrLinearSize
    struct.pack_into("<I", hdr, 28, 1)                      # mipMapCount
    struct.pack_into("<I", hdr, 76, 32)                     # pixelformat size
    struct.pack_into("<I", hdr, 80, 0x4)                    # DDPF_FOURCC
    hdr[84:88] = DDS_FOURCC_DX10
    struct.pack_into("<I", hdr, 108, 0x1000)                # DDSCAPS_TEXTURE
    dx10 = struct.pack("<IIIII", DXGI_BC6H_UF16, 3, 0, max(1, arraysize), 0)

    data = bytes(hdr) + dx10 + bytes(payload)
    try:
        path.write_bytes(data)                              # pathlib.Path
    except AttributeError:
        with open(path, "wb") as fh:                        # str
            fh.write(data)
    return path
