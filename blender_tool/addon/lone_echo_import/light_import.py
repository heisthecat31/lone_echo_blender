"""Import a `lights.json` sidecar (`le_lights`) as Blender lamps.

⛔ READ THIS BEFORE ENABLING IT ⛔  (`stream-confirmed`)
-----------------------------------------------------------------------------
Lone Echo is a **hybrid**, not a baked-only game, and it is not a realtime-only
game either. 86 of the 87 shipped "lit surface" shaders bind BOTH the baked
ambient path (`k_ambient_lightmap_ao0/ao1`, `k_ambient_spec_cubemaps`) AND the
realtime clustered path (`k_clustered_lights`, `k_light_clusters`). Of 118
decoded level lights only **49 set `eEnableDiffuse`** (station_front 15 of 47,
bridge_night 28 of 65) while 112 set `eEnableSpecular` — i.e. **most shipped
lights are specular-only**. They exist to put moving highlights on surfaces
whose *diffuse* response is already baked into the lightmap and the irradiance
volumes.

Blender has neither a specular-only lamp nor the baked diffuse underneath, so
importing every light **double-lights the scene**. Therefore:

  * this importer is **OFF BY DEFAULT** (`opts["import_lights"]` / the operator
    checkbox), and
  * when on it imports **only the `eEnableDiffuse` subset** by default
    (`light_set="diffuse"`); `light_set="all"` is an explicit opt-in that
    returns a `warnings` entry and parks the specular-only lamps in a hidden
    child collection unless `hide_specular_only=False`.

The lights are also **not sufficient** for visual fidelity on their own — the
bake is 101.8 MB of irradiance SH + HDR lightmaps against 108 KB of light
records (936x). See docs/LIGHTING.md §0 and the lightmap importer.

-----------------------------------------------------------------------------
Headless use (no operator, like `import_lemesh` / `import_lescatter`):

    import lone_echo_import
    lone_echo_import.import_lights("path/to/lights.json", bpy.context,
                                   {"light_set": "diffuse"})

`import_lights` also accepts an already-parsed sidecar dict instead of a path.

-----------------------------------------------------------------------------
WHY THIS MODULE DOES NOT `import bpy` AT MODULE SCOPE
-----------------------------------------------------------------------------
Everything except `import_lights` itself is pure stdlib, so the conversion math,
the selection policy and the axis transform are unit-tested under plain
`python3` (`tests/test_light_import.py`) exactly like `scatter_reader`. `bpy`
and `mathutils` are imported inside the functions that need them.

The unit math is duplicated from `le_mesh.lights` on purpose: the add-on must be
self-contained when zipped. `tests/test_light_import.py` pins this module's
arithmetic against `le_mesh.lights` AND against the `blender` block of a real
extractor sidecar, so the two can never drift.

-----------------------------------------------------------------------------
UNITS — `shader-confirmed` against the engine's own lighting shaders
-----------------------------------------------------------------------------
    primarycolor  linear HDR RGB, intensity PRE-MULTIPLIED (no intensity float)
    color        = primarycolor / max(primarycolor)          both sides linear
    energy       = 4*pi * max(primarycolor)   POINT / SPOT   (watts)
    energy       =        max(primarycolor)   SUN            (W/m^2, no d term)
    spot_size    = fovy                                      identical
    spot_blend   = 1 - acos(penumbra.x)/acos(penumbra.y)     approximate ramp
    cutoff_dist  = attenuation.z
    attenmethod  = the exponent m in 1/d^m; 2 is Blender-native, 1 needs a
                   Cycles Light-Falloff node -> flagged LOSSY

⚠ KNOWN, QUANTIFIED DIVERGENCE. The engine subtracts a runtime `faderangeoffset`
so attenuation reaches exactly 0 at the range; Blender has no such term. At
`d = range/2` with `attenmethod == 2` the game is at `3/R^2` and Blender at
`4/R^2`, so an imported light is **33 % brighter than the game there** (the game
is 25 % dimmer than pure inverse-square). `use_custom_distance` +
`cutoff_distance = range` clips the tail but does not fix the shape.

⛔ NOT DERIVABLE FROM DISK — never fabricated here, carried as inert `le_*`
custom properties instead: light radius / `shadow_soft_size` (there is NO
source-size field; `filtersize` is a shadow-map PCF width in texels), the cone
`falloff` exponent, `faderangeoffset`, `lightmask` / `scenemask` / `visindex` /
`qualitylevel` receiver gating, and absolute exposure (the game auto-exposes and
tonemaps, so only ratios are meaningful).
"""

from __future__ import annotations

import json
import math
from pathlib import Path

SIDECAR_FORMAT = "le_lights"

# --- ELightOptions (`name-confirmed`) -- only the bits this module acts on --
eEnableDiffuse = 1 << 0
eEnableSpecular = 1 << 1
eCastShadows = 1 << 2
eCastLevelShadows = 1 << 3
eLightEnabled = 1 << 8
ePrimaryDirLight = 1 << 20

# ELightType (`name-confirmed`) -> Blender lamp type
BLENDER_TYPE = {0: "POINT", 1: "SPOT", 2: "SUN"}
TYPE_NAME_TO_ENUM = {"ePointLight": 0, "eSpotLight": 1, "eDirectionalLight": 2}

# ELightOptions bit -> name, for rebuilding `options_raw` from a v1 name list
OPTION_BITS = {
    "eEnableDiffuse": 1 << 0, "eEnableSpecular": 1 << 1, "eCastShadows": 1 << 2,
    "eCastLevelShadows": 1 << 3, "eCastActorShadows": 1 << 4,
    "eLightTransparents": 1 << 5, "eLightOpaques": 1 << 6,
    "eLightParticles": 1 << 7, "eLightEnabled": 1 << 8,
    "eUseLightShaft": 1 << 9, "eUseLightShaftShadows": 1 << 10, "eUseFog": 1 << 11,
    "eBakeDirect": 1 << 12, "eBakeIndirect": 1 << 13, "eUseNonUniformFog": 1 << 14,
    "eCastOpaqueShadows": 1 << 15, "eCastAlphaTestShadows": 1 << 16,
    "eCastTransparentShadows": 1 << 17, "eBakeOnlyIrradiance": 1 << 18,
    "eDontBakeIrradiance": 1 << 19, "ePrimaryDirLight": 1 << 20,
    "eEyesOnlyLight": 1 << 21, "eBakeShadow": 1 << 22,
    "eLightVolumetrics": 1 << 23, "eCastAllLevelShadows": 1 << 24,
}

# --- import policy ----------------------------------------------------------
LIGHT_SET_DIFFUSE = "diffuse"     # DEFAULT: eEnableDiffuse only (49/118 corpus)
LIGHT_SET_ALL = "all"             # opt-in; DOUBLE-LIGHTS, warns
LIGHT_SET_ENABLED = "enabled"     # alias of "all" for readability at call sites
LIGHT_SETS = (LIGHT_SET_DIFFUSE, LIGHT_SET_ALL, LIGHT_SET_ENABLED)

DEFAULT_OPTS = {
    "import_lights": False,        # OFF BY DEFAULT -- see the module header
    "light_set": LIGHT_SET_DIFFUSE,
    "skip_disabled": True,         # drop records with eLightEnabled clear
    "hide_specular_only": True,    # light_set="all": park them hidden
    "y_up_to_z_up": True,          # THE basis, same one the meshes use
    "use_custom_distance": True,   # clip at attenuation.z
    "cycles_falloff_nodes": True,  # Light Falloff node when attenmethod != 2
    "exposure_scale": 1.0,         # USER calibration only; 1.0 == the raw
                                   # shader-confirmed conversion. Not a fudge
                                   # factor -- the game auto-exposes, we cannot.
    "scene_filter": None,          # scene_hash or scene_name; None = all scenes
    "collection_name": None,
}


# ===========================================================================
# sidecar reading  (pure stdlib)
# ===========================================================================

def load_lights(source):
    """Accept a `lights.json` path, a directory holding one, or a parsed dict."""
    if isinstance(source, dict):
        doc = source
    else:
        p = Path(source)
        if p.is_dir():
            cands = sorted(p.glob("*lights*.json"))
            if not cands:
                raise FileNotFoundError(f"no *lights*.json in {p}")
            p = cands[0]
        doc = json.loads(p.read_text(encoding="utf-8"))
    fmt = doc.get("format")
    if fmt != SIDECAR_FORMAT:
        raise ValueError(f"not a {SIDECAR_FORMAT} sidecar (format={fmt!r})")
    return doc


def iter_lights(doc, scene_filter=None):
    """Flatten `doc` to `[(scene_hash, scene_name, light_dict), ...]`.

    `scene_filter` keeps only the scene whose hash or name matches (v1 and v2
    sidecars both carry every scene in the archive, most of them empty).
    """
    out = []
    for s in doc.get("scenes", []):
        h, n = s.get("scene_hash", ""), s.get("scene_name", "")
        if scene_filter and scene_filter not in (h, n):
            continue
        for i, L in enumerate(s.get("lights", [])):
            L = dict(L)
            L.setdefault("index", i)
            out.append((h, n, L))
    return out


def options_word(rec) -> int:
    """`options_raw` when present (v2), else rebuilt from the v1 name list."""
    raw = rec.get("options_raw")
    if isinstance(raw, int):
        return raw
    names = rec.get("options") or []
    if isinstance(names, int):
        return names
    if isinstance(names, str):
        names = names.split("|")
    return sum(OPTION_BITS.get(n, 0) for n in names)


def light_type_enum(rec) -> int:
    lt = rec.get("lighttype")
    if isinstance(lt, int):
        return lt
    return TYPE_NAME_TO_ENUM.get(rec.get("type", ""), 0)


def affects_diffuse(rec) -> bool:
    return bool(options_word(rec) & eEnableDiffuse)


def affects_specular(rec) -> bool:
    return bool(options_word(rec) & eEnableSpecular)


def is_enabled(rec) -> bool:
    return bool(options_word(rec) & eLightEnabled)


def select(records, opts=None):
    """Apply the import policy. Returns `(kept, stats)`.

    `stats` names every reason a record was dropped, so the caller can report an
    honest "imported 15 of 47" instead of silently under-importing.
    """
    opts = merged_opts(opts)
    light_set = opts["light_set"]
    if light_set not in LIGHT_SETS:
        raise ValueError(f"light_set must be one of {LIGHT_SETS}, got {light_set!r}")
    skip_disabled = bool(opts["skip_disabled"])

    kept = []
    stats = {"light_set": light_set, "total": 0, "kept": 0,
             "skipped_disabled": 0, "skipped_specular_only": 0,
             "specular_only_kept": 0, "diffuse_enabled": 0,
             "specular_enabled": 0, "specular_only": 0}
    for r in records:
        stats["total"] += 1
        d, s = affects_diffuse(r), affects_specular(r)
        stats["diffuse_enabled"] += 1 if d else 0
        stats["specular_enabled"] += 1 if s else 0
        stats["specular_only"] += 1 if (s and not d) else 0
        if skip_disabled and not is_enabled(r):
            stats["skipped_disabled"] += 1
            continue
        if light_set == LIGHT_SET_DIFFUSE and not d:
            stats["skipped_specular_only"] += 1
            continue
        if not d:
            stats["specular_only_kept"] += 1
        kept.append(r)
    stats["kept"] = len(kept)
    return kept, stats


def merged_opts(opts=None) -> dict:
    o = dict(DEFAULT_OPTS)
    o.update(opts or {})
    return o


# ===========================================================================
# game -> Blender math  (pure stdlib; pinned against le_mesh.lights by tests)
# ===========================================================================

def axis_rows(y_up_to_z_up: bool = True):
    """THE Y-up -> Z-up basis as 3x3 row-major: game (x,y,z) -> (x,-z,y).

    A pure +90 deg rotation about X, determinant +1, NO mirror. Identical to
    `mesh_builder._axis_matrix` and `scatter_reader.basis_matrix()` — a light rig
    rotated relative to the geometry is a silent, expensive bug, so this must
    never be a second convention. See AXIS_CALIBRATION.md.
    """
    if not y_up_to_z_up:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    return ((1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0))


def to_blender_vec(v, y_up_to_z_up: bool = True):
    if not y_up_to_z_up:
        return (float(v[0]), float(v[1]), float(v[2]))
    return (float(v[0]), -float(v[2]), float(v[1]))


def quat_matrix_rows(q):
    """Row-major 3x3 R(q) for a normalised-on-the-fly quaternion (x, y, z, w)."""
    x, y, z, w = (float(c) for c in q)
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n > 1e-12:
        x, y, z, w = x / n, y / n, z / n, w / n
    return (
        (1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
        (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
        (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)),
    )


def light_matrix_rows(rec, y_up_to_z_up: bool = True):
    """The lamp's WORLD matrix, 4x4 row-major:

        M = A @ T(pos) @ R(orientation) @ Rx(180 deg)

    * `pos`/`orientation` are ALREADY world — there is no `CTransformCR` join and
      no parent chain for scene lights (`cachedjointidx`/`jointoffsetidx` are
      0xFFFFFFFF on 118/118), and they live in the same world space as the
      static-instance scatter (`export-validated`: station_front's 47 lights all
      fall inside the 21,394-instance scatter bbox).
    * `A` is the mesh basis (see `axis_rows`).
    * `Rx(180 deg)` is the lamp-forward flip: the engine's forward is the light's
      local **+Z** (`direction == R(q)*(0,0,1)`, 118/118, max err 1.95e-07) while
      a Blender lamp emits along its local **-Z**. det +1, no mirror.

    Invariant the tests assert:  `M[:3,:3] @ (0,0,-1) == A @ direction`.
    Roll about the axis is unconstrained by the engine (no shipped light has a
    gobo); taking it from the quaternion keeps the result deterministic.
    """
    R = quat_matrix_rows(rec.get("orientation", (0.0, 0.0, 0.0, 1.0)))
    # R @ Rx(pi)  ==  negate columns 1 and 2
    RF = tuple(tuple(R[r][c] if c == 0 else -R[r][c] for c in range(3))
               for r in range(3))
    A = axis_rows(y_up_to_z_up)
    M = tuple(tuple(sum(A[r][k] * RF[k][c] for k in range(3)) for c in range(3))
              for r in range(3))
    t = to_blender_vec(rec.get("pos", (0.0, 0.0, 0.0)), y_up_to_z_up)
    return (M[0] + (t[0],), M[1] + (t[1],), M[2] + (t[2],), (0.0, 0.0, 0.0, 1.0))


def normalized_color(primarycolor):
    """`(color normalised to peak 1.0, peak)`. Both sides linear — NO sRGB."""
    pri = [float(c) for c in primarycolor]
    peak = max(pri) if pri else 0.0
    if peak <= 0.0:
        return (0.0, 0.0, 0.0), 0.0
    return tuple(c / peak for c in pri), peak


def blender_energy(rec) -> float:
    """POINT/SPOT: `4*pi*peak` watts. SUN: `peak` W/m^2 (no distance term)."""
    _, peak = normalized_color(rec.get("primarycolor", (0.0, 0.0, 0.0)))
    if light_type_enum(rec) == 2:
        return peak
    return 4.0 * math.pi * peak


def blender_spot(rec):
    """`(spot_size, spot_blend)`; `(0,0)` for non-spots.

    `spot_size == fovy` exactly (both are the FULL cone angle in radians;
    `2*acos(penumbra.y) == fovy` on 106/106 shipped spots). `spot_blend` is
    APPROXIMATE: the engine ramps with a smootherstep in COS space between outer
    and inner, Blender with its own curve — the cone EDGES match, the ramp does not.
    """
    if light_type_enum(rec) != 1:
        return 0.0, 0.0
    fovy = float(rec.get("fovy", 0.0))
    pen = rec.get("penumbra", (-1.0, -1.0))
    ci, co = float(pen[0]), float(pen[1])
    if not (-1.0 <= ci <= 1.0) or not (-1.0 <= co <= 1.0):
        return fovy, 0.0
    ti, to = math.acos(ci), math.acos(co)
    blend = 0.0 if to <= 1e-9 else max(0.0, min(1.0, 1.0 - ti / to))
    return fovy, blend


def light_range(rec) -> float:
    """`attenuation.z` — THE range (== `farp` on 118/118), used as the hard CULL
    radius by the engine's irradiance-bake shader (`shader-confirmed`). Not the
    curve's offset."""
    a = rec.get("attenuation") or (1.0, 0.0, 0.0, 0.0)
    return float(a[2]) if len(a) > 2 else 0.0


def maxfadedistance(rec) -> float:
    """`attenuation.w` — the distance the attenuation curve is offset to reach
    zero at. `shader-confirmed`; see `le_mesh.lights.LightRecord.maxfadedistance`
    for the shader quotes and the r15/r14 era caveat. Falls back to the range
    when absent, which is what 107/118 shipped records carry anyway."""
    a = rec.get("attenuation") or (1.0, 0.0, 0.0, 0.0)
    if len(a) > 3 and float(a[3]) > 0.0:
        return float(a[3])
    return light_range(rec)


def attenmethod(rec) -> float:
    return float(rec.get("attenmethod", 2.0))


def falloff_is_physical(rec) -> bool:
    """True when `attenmethod == 2` — Blender's native inverse-square falloff
    reproduces the engine exactly (modulo the range offset). Anything else is
    LOSSY in EEVEE and needs a Cycles Light Falloff node."""
    return abs(attenmethod(rec) - 2.0) < 1e-6


def range_offset(rec) -> float:
    """The shader's runtime `faderangeoffset` = `1/maxfadedistance^attenmethod`.

    ★ The argument is `attenuation.w`, not `.z` — see
    `le_mesh.lights.range_offset`, which this MUST agree with
    (`tests/test_light_import.test_range_offset_matches_le_mesh_lights`)."""
    r = maxfadedistance(rec)
    if r <= 0.0:
        return 0.0
    m = attenmethod(rec)
    return 1.0 / (r ** m) if m != 0.0 else r


def brightness_divergence(rec, fraction: float = 0.5) -> float:
    """How much BRIGHTER than the game an imported lamp is at `fraction*range`.

    1.333 for the physical case at half range (the game is 25 % dimmer than pure
    inverse-square there). 1.0 for SUN. See the module header.
    """
    if light_type_enum(rec) == 2:
        return 1.0
    d = light_range(rec) * fraction
    if d <= 0.0:
        return 1.0
    m = attenmethod(rec)
    blender = 1.0 / (d ** m) if m else 1.0
    game = blender - range_offset(rec)
    if game <= 0.0 or blender <= 0.0:
        return float("inf")
    return blender / game


def blender_params(rec, opts=None) -> dict:
    """Everything needed to stamp one Blender lamp, plus its `le_*` custom props.

    Nothing here is invented: `shadow_soft_size` is 0 because `SGLightParams`
    carries no source-size field at all, and every field with no Blender
    equivalent lands in `custom` (inert) rather than being converted.
    """
    o = merged_opts(opts)
    color, peak = normalized_color(rec.get("primarycolor", (0.0, 0.0, 0.0)))
    size, blend = blender_spot(rec)
    ow = options_word(rec)
    scale = float(o["exposure_scale"])
    a = list(rec.get("attenuation") or (1.0, 0.0, 0.0, 0.0))
    ls = rec.get("lightshaft") or {}
    return {
        "name": light_name(rec),
        "type": BLENDER_TYPE.get(light_type_enum(rec), "POINT"),
        "matrix": light_matrix_rows(rec, bool(o["y_up_to_z_up"])),
        "color": color,
        "peak_radiance": peak,
        "energy": blender_energy(rec) * scale,
        "spot_size": size,
        "spot_blend": blend,
        # no source-size field exists on disk -> a true point source, hard shadows
        "shadow_soft_size": 0.0,
        "use_shadow": bool(ow & (eCastShadows | eCastLevelShadows)),
        "cutoff_distance": light_range(rec),
        "use_custom_distance": bool(o["use_custom_distance"]),
        "attenmethod": attenmethod(rec),
        "physical_falloff": falloff_is_physical(rec),
        "affects_diffuse": bool(ow & eEnableDiffuse),
        "affects_specular": bool(ow & eEnableSpecular),
        "enabled": bool(ow & eLightEnabled),
        "primary_dir_light": bool(ow & ePrimaryDirLight),
        # --- inert provenance / not-derivable, carried NOT converted ----------
        "custom": {
            "le_light_index": int(rec.get("index", 0)),
            "le_name_hash": str(rec.get("name", "")),
            "le_light_type": rec.get("type", ""),
            "le_options": ", ".join(rec.get("options", []))
            if isinstance(rec.get("options"), list) else str(rec.get("options", "")),
            "le_options_raw": _int_prop(ow),
            "le_primarycolor": [float(c) for c in rec.get("primarycolor", (0, 0, 0))],
            "le_attenuation": [float(c) for c in a],
            "le_attenuation_maxfadedistance": maxfadedistance(rec),
            "le_attenmethod": attenmethod(rec),
            "le_range": light_range(rec),
            # NOT DERIVABLE / no Blender equivalent -- never used by the importer
            "le_filtersize_pcf_not_a_radius": float(rec.get("filtersize", 0.0)),
            "le_cone_falloff_exponent": float(rec.get("falloff", 0.0)),
            "le_faderangeoffset_runtime": range_offset(rec),
            "le_lightmask": _int_prop(int(rec.get("lightmask", 0))),
            "le_scenemask": str(rec.get("scenemask", "")),
            "le_visindex": _int_prop(int(rec.get("visindex", 0))),
            "le_qualitylevel": int(rec.get("qualitylevel", 0)),
            "le_shadowqualitylevel": int(rec.get("shadowqualitylevel", 0)),
            "le_lightshaft_intensity": float(ls.get("intensity", 0.0)),
            "le_affects_diffuse": bool(ow & eEnableDiffuse),
            "le_affects_specular": bool(ow & eEnableSpecular),
            "le_brightness_vs_game_at_half_range": brightness_divergence(rec),
            "le_falloff_lossy": not falloff_is_physical(rec),
        },
    }


def light_name(rec) -> str:
    t = {0: "point", 1: "spot", 2: "sun"}.get(light_type_enum(rec), "light")
    tag = "D" if affects_diffuse(rec) else "S"      # Diffuse-enabled / Spec-only
    return f"lelight_{int(rec.get('index', 0)):03d}_{t}_{tag}_{rec.get('name', '')}"


def _int_prop(v):
    """Blender ID int properties are 32-bit SIGNED; RAD stores uint32 (e.g.
    `visindex == 0xFFFFFFFF`). Stringify anything that would overflow — same
    guard `mesh_builder` uses."""
    if isinstance(v, int) and not (-(2 ** 31) <= v < 2 ** 31):
        return str(v)
    return v


def summarize_doc(doc, opts=None) -> dict:
    """Dry-run: what WOULD be imported, without touching bpy. Handy in tests and
    for a UI preview."""
    o = merged_opts(opts)
    recs = [r for _, _, r in iter_lights(doc, o["scene_filter"])]
    _, stats = select(recs, o)
    return stats


# ===========================================================================
# Blender side (the ONLY place bpy is touched)
# ===========================================================================

def _apply_cycles_falloff(light_data, params):
    """`attenmethod != 2` has no native Blender equivalent — approximate it with
    a Cycles **Light Falloff** node and flag the import LOSSY.

    `attenmethod` is the exponent m in `1/d^m` (the Maya decay rate, stored as a
    float; `shader-confirmed`). Blender lamps are hard-wired to m == 2, so:
      m == 1  -> Light Falloff `Linear`   (12/118 shipped lights)
      m == 0  -> Light Falloff `Constant`
      m == 3  -> no equivalent at all; left native and reported as lossy.
    ⚠ Cycles only — EEVEE does not evaluate light node trees (`inferred`), so in
    EEVEE these lights keep an inverse-square falloff. Returns True if a node
    tree was built.
    """
    m = params["attenmethod"]
    socket = {1.0: "Linear", 0.0: "Constant"}.get(round(m, 6))
    if socket is None:
        return False
    if not hasattr(light_data, "use_nodes"):
        return False
    light_data.use_nodes = True
    nt = light_data.node_tree
    if nt is None:
        return False
    emit = next((n for n in nt.nodes if n.type == "EMISSION"), None)
    if emit is None:
        return False
    fall = nt.nodes.new("ShaderNodeLightFalloff")
    fall.location = (emit.location[0] - 220, emit.location[1])
    fall.inputs["Strength"].default_value = 1.0
    fall.inputs["Smooth"].default_value = 0.0
    nt.links.new(fall.outputs[socket], emit.inputs["Strength"])
    return True


def _build_light(params, opts) -> "bpy.types.Object":     # noqa: F821
    import bpy                                            # type: ignore
    from mathutils import Matrix                          # type: ignore

    data = bpy.data.lights.new(params["name"], type=params["type"])
    data.color = params["color"]
    data.energy = params["energy"]
    data.use_shadow = params["use_shadow"]
    if params["type"] == "SPOT":
        data.spot_size = params["spot_size"]
        data.spot_blend = params["spot_blend"]
    if hasattr(data, "shadow_soft_size"):
        # 0.0 == a true point source. There is NO source-size field on disk;
        # `filtersize` is a shadow-map PCF width, using it here would be fabrication.
        data.shadow_soft_size = params["shadow_soft_size"]
    if params["type"] == "SUN" and hasattr(data, "angle"):
        data.angle = 0.0                       # same reasoning as shadow_soft_size
    if hasattr(data, "use_custom_distance"):
        rng = params["cutoff_distance"]
        if params["type"] != "SUN" and params["use_custom_distance"] and rng > 0.0:
            data.use_custom_distance = True
            data.cutoff_distance = rng

    lossy_nodes = False
    if not params["physical_falloff"] and opts["cycles_falloff_nodes"]:
        lossy_nodes = _apply_cycles_falloff(data, params)

    ob = bpy.data.objects.new(params["name"], data)
    ob.matrix_world = Matrix(params["matrix"])
    for k, v in params["custom"].items():
        try:
            ob[k] = v
        except Exception:                       # noqa: BLE001 - never fail an import on a prop
            ob[k] = str(v)
    ob["le_falloff_node_built"] = lossy_nodes
    return ob


def import_lights(source, context, opts: dict = None) -> dict:
    """Core routine. `source` = a `lights.json` path (or its directory) or an
    already-parsed sidecar dict. Returns a summary dict. Usable without the
    operator, exactly like `import_lemesh` / `import_lescatter`.

    Defaults: `light_set="diffuse"` (only the `eEnableDiffuse` subset) and
    `skip_disabled=True`. The caller is responsible for the OFF-BY-DEFAULT gate
    (`opts["import_lights"]`); calling this function is itself the opt-in.
    """
    import bpy                                            # type: ignore

    o = merged_opts(opts)
    doc = load_lights(source)
    entries = iter_lights(doc, o["scene_filter"])
    records = [r for _, _, r in entries]
    kept, stats = select(records, o)

    archive = doc.get("archive", "lights")
    root_name = o["collection_name"] or f"lelights_{archive}"
    root = bpy.data.collections.new(root_name)
    context.scene.collection.children.link(root)

    spec_coll = None
    if o["light_set"] != LIGHT_SET_DIFFUSE and stats["specular_only_kept"]:
        spec_coll = bpy.data.collections.new(f"{root_name}_specular_only")
        root.children.link(spec_coll)

    by_type = {}
    lossy = nodes_built = 0
    for rec in kept:
        params = blender_params(rec, o)
        ob = _build_light(params, o)
        target = root
        if spec_coll is not None and not params["affects_diffuse"]:
            target = spec_coll
            if o["hide_specular_only"]:
                ob.hide_render = True
                ob.hide_viewport = True
        target.objects.link(ob)
        by_type[params["type"]] = by_type.get(params["type"], 0) + 1
        if not params["physical_falloff"]:
            lossy += 1
            nodes_built += 1 if ob.get("le_falloff_node_built") else 0

    warnings = []
    if o["light_set"] != LIGHT_SET_DIFFUSE and stats["specular_only_kept"]:
        warnings.append(
            f"DOUBLE-LIGHTING RISK: {stats['specular_only_kept']} of {stats['kept']} "
            "imported lights are specular-only (eEnableDiffuse clear). The game "
            "layers them over a BAKED diffuse (lightmap + irradiance volumes); "
            "Blender has no such underlay, so the scene will be over-lit."
            + (" They were placed hidden in a '_specular_only' collection."
               if o["hide_specular_only"] else ""))
    if lossy:
        warnings.append(
            f"{lossy} light(s) have attenmethod != 2 (1/d^m, not inverse-square). "
            f"{nodes_built} got a Cycles Light Falloff node; EEVEE cannot "
            "reproduce them at all. LOSSY.")
    warnings.append(
        "Lights alone do NOT reproduce Lone Echo's look: 86 of 87 lit-surface "
        "shaders bind BOTH the baked ambient path and the clustered realtime "
        "path. Wire the lightmap too.")
    warnings.append(
        "Blender has no range-offset term, so an imported light is ~33% brighter "
        "than the game at range/2 (the game is 25% dimmer than pure inverse-"
        "square there). Absolute exposure is not derivable; calibrate Film "
        "Exposure once per level.")

    return {
        "collection": root_name,
        "specular_collection": spec_coll.name if spec_coll else None,
        "sidecar_version": doc.get("version"),
        "archive": archive,
        "scenes": len({(h, n) for h, n, _ in entries}),
        "light_set": o["light_set"],
        "total": stats["total"],
        "imported": stats["kept"],
        "skipped_specular_only": stats["skipped_specular_only"],
        "skipped_disabled": stats["skipped_disabled"],
        "specular_only_imported": stats["specular_only_kept"],
        "diffuse_enabled": stats["diffuse_enabled"],
        "specular_enabled": stats["specular_enabled"],
        "by_type": by_type,
        "lossy_falloff": lossy,
        "falloff_nodes_built": nodes_built,
        "exposure_scale": o["exposure_scale"],
        "warnings": warnings,
    }


# ===========================================================================
# Operator + menu entry
#
# Defined only when bpy is importable, so this module stays importable (and
# unit-testable) under plain python3. `__init__.py` registers it:
#     from . import light_import
#     import_lights = light_import.import_lights
#     _CLASSES = (..., light_import.IMPORT_OT_lelights)
#     bpy.types.TOPBAR_MT_file_import.append(light_import.menu_func)
# ===========================================================================

try:                                                       # pragma: no cover
    import bpy                                             # type: ignore
    from bpy.props import (BoolProperty, EnumProperty,      # type: ignore
                           FloatProperty, StringProperty)
    from bpy_extras.io_utils import ImportHelper            # type: ignore
    _HAVE_BPY = True
except Exception:                                          # noqa: BLE001
    _HAVE_BPY = False
    IMPORT_OT_lelights = None
    menu_func = None


if _HAVE_BPY:                                              # pragma: no cover
    class IMPORT_OT_lelights(bpy.types.Operator, ImportHelper):   # noqa: F811
        """Import Lone Echo scene lights from a lights.json sidecar.

        OFF BY DEFAULT elsewhere in the add-on; running this operator is the
        explicit opt-in. Defaults to the eEnableDiffuse subset — see the module
        header for why importing all of them double-lights the scene.
        """
        bl_idname = "import_scene.lelights"
        bl_label = "Import Lone Echo Lights (lights.json)"
        bl_options = {"REGISTER", "UNDO"}

        filename_ext = ".json"
        filter_glob: StringProperty(default="*.json", options={"HIDDEN"})   # type: ignore

        light_set: EnumProperty(
            name="Light Set",
            description="Which shipped lights to import. Most Lone Echo level "
                        "lights are SPECULAR-ONLY (only 49 of 118 set "
                        "eEnableDiffuse) and sit on top of a baked lightmap, so "
                        "importing all of them double-lights the scene",
            items=[
                (LIGHT_SET_DIFFUSE, "Diffuse-enabled only (recommended)",
                 "Import only lights with eEnableDiffuse — the ones whose diffuse "
                 "contribution is NOT already baked"),
                (LIGHT_SET_ALL, "All lights (double-lights!)",
                 "Import specular-only lights too. Blender has no specular-only "
                 "lamp and no baked diffuse underneath — the scene WILL be over-lit"),
            ],
            default=LIGHT_SET_DIFFUSE)   # type: ignore
        hide_specular_only: BoolProperty(
            name="Hide Specular-Only", default=True,
            description="With 'All lights': park the specular-only lamps in a "
                        "hidden '_specular_only' child collection")   # type: ignore
        skip_disabled: BoolProperty(
            name="Skip Disabled Lights", default=True,
            description="Drop records with eLightEnabled clear (authoring-only)")   # type: ignore
        y_up_to_z_up: BoolProperty(
            name="Y-up to Z-up", default=True,
            description="Apply the same +90deg-X basis the meshes use")   # type: ignore
        use_custom_distance: BoolProperty(
            name="Clip at Range", default=True,
            description="Set use_custom_distance + cutoff_distance = attenuation.z. "
                        "Clips the tail; does not fix the missing range-offset shape")   # type: ignore
        cycles_falloff_nodes: BoolProperty(
            name="Cycles Falloff Nodes", default=True,
            description="Build a Light Falloff node for lights whose attenmethod "
                        "is not 2 (1/d^m). Cycles only — LOSSY in EEVEE")   # type: ignore
        exposure_scale: FloatProperty(
            name="Exposure Scale", default=1.0, min=0.0, soft_max=10.0,
            description="USER calibration multiplied onto every energy. 1.0 is the "
                        "raw shader-confirmed conversion — the game auto-exposes and "
                        "tonemaps, which we do not reproduce")   # type: ignore
        scene_filter: StringProperty(
            name="Scene", default="",
            description="Import only this scene_hash or scene_name (blank = all)")   # type: ignore

        def draw(self, context):
            layout = self.layout
            layout.prop(self, "light_set")
            if self.light_set != LIGHT_SET_DIFFUSE:
                box = layout.box()
                box.label(text="Double-lighting risk", icon="ERROR")
                box.label(text="Specular-only lights sit on a BAKED diffuse.")
                box.prop(self, "hide_specular_only")
            for p in ("skip_disabled", "y_up_to_z_up", "use_custom_distance",
                      "cycles_falloff_nodes", "exposure_scale", "scene_filter"):
                layout.prop(self, p)

        def execute(self, context):
            opts = {
                "light_set": self.light_set,
                "hide_specular_only": self.hide_specular_only,
                "skip_disabled": self.skip_disabled,
                "y_up_to_z_up": self.y_up_to_z_up,
                "use_custom_distance": self.use_custom_distance,
                "cycles_falloff_nodes": self.cycles_falloff_nodes,
                "exposure_scale": self.exposure_scale,
                "scene_filter": self.scene_filter or None,
            }
            try:
                summary = import_lights(self.filepath, context, opts)
            except Exception as exc:                       # noqa: BLE001
                self.report({"ERROR"}, f"lights import failed: {exc}")
                return {"CANCELLED"}
            self.report(
                {"INFO"},
                "Lights: imported {imported}/{total} ({skipped_specular_only} "
                "specular-only skipped, {skipped_disabled} disabled), "
                "{lossy_falloff} lossy falloff".format(**summary))
            for w in summary["warnings"][:2]:
                self.report({"WARNING"}, w)
            return {"FINISHED"}

    def menu_func(self, context):                          # noqa: F811
        self.layout.operator(IMPORT_OT_lelights.bl_idname,
                             text="Lone Echo Lights (lights.json)")
