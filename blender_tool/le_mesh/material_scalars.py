"""Decode SGMaterialData scalar parameters from a material primary slice.

Pure stdlib. Importable without oodle or bpy (the CSymbol64 hash is inlined so
this module has no dependency on the `scripts/` decode stack). The extractor
hands this an already-decompressed CGMaterialResourceWin7 primary slice; it
returns the durable scalar knobs the Blender addon needs:

    {
      base_color_factor: [r,g,b,a],   # SGMaterialData.bakecolor  @0x08
      emissive_color:    [r,g,b],     # SGMaterialData.bakeemissivecolor @0x18 (RGB)
      emissive_intensity: float,      # materialprop layer0_emissive_intensity (fallback max layerN)
      alpha:             float,       # materialprop k_alpha (fallback 1.0)
      blend_mode:        int,         # SGMaterialData.blendmode  @0x28 (u16)
      double_sided:      bool,        # flags & eDoubleSided
      # extras (audit / consumers that want them):
      mattype, flags, flag_names, materialfx, is_emissive, named_scalars
    }

Disk layout:

  [0x000 .. 0x160)  SGMaterialData header (direct memory image)
     +0x000 u64   materialfx (CSymbol64)
     +0x008 4×f32 bakecolor (RGBA base-color multiplier)
     +0x018 4×f32 bakeemissivecolor (RGBA; RGB != 0 => emissive)
     +0x028 u16   blendmode
     +0x02a u16   mattype
     +0x02c u32   flags (EFlags)
     +0x030 f32   shadowfadedist
     +0x060 u64   materialprops.iused         (CTable<u32> count)
     +0x098 u64   materialpropoffsets.iused   (CMap<CSymbol64,u32> count)
     +0x0d8 u64   uvsets.iused                (CTable<CSymbol64> count)
     +0x110 u64   permutations.iused          (CMap count)
     +0x150 u64   auxillaryinputs.iused       (CTable<SShaderInputData> count)
  [0x160 ..)  trailing arrays, in order:
     materialprops        n_props     × 4      (u32 words; decode as float32)
     materialpropoffsets  n_propoff   × 16     (key u64 @0, byteoffset u32 @8, pad @12)
     uvsets               n_uvsets    × 8
     permutations         n_perms     × 16
     auxillaryinputs      n_inputs    × 0x20

A materialpropoffsets entry maps a property-name hash -> a byte offset into the
materialprops word array (offset/4 = word index).  The pointed-at u32 word
reinterpreted as a float32 is the scalar value.
"""

from __future__ import annotations

import struct

# --- SGMaterialData::EFlags -------------------------------------------------
EFLAGS = {
    "eDoubleSided":               0x001,
    "eCastShadows":               0x002,
    "eGIOccluder":                0x004,
    "eGIReceiver":                0x008,
    "eUseAmbientSpecular":        0x010,
    "eUseVertexLighting":         0x020,
    "eUseFoliageAnimation":       0x040,
    "eEyeMaterial":               0x080,
    "eOutputTransparentVelocity": 0x100,
}
E_DOUBLE_SIDED = 0x001

HEADER_SIZE = 0x160
OFF_MATERIALFX          = 0x000
OFF_BAKECOLOR           = 0x008
OFF_BAKEEMISSIVECOLOR   = 0x018
OFF_BLENDMODE           = 0x028
OFF_MATTYPE             = 0x02A
OFF_FLAGS               = 0x02C
OFF_SHADOWFADEDIST      = 0x030
OFF_MATERIALPROPS_IUSED = 0x060
OFF_PROPOFFSETS_IUSED   = 0x098
OFF_UVSETS_IUSED        = 0x0D8
OFF_PERMS_IUSED         = 0x110
OFF_AUXINPUTS_IUSED     = 0x150

SIZEOF_SHADERINPUTDATA  = 0x20
MAX_REASONABLE          = 10_000


# --- CSymbol64 hash (inlined; matches scripts/le_symbol_names) -----
_MASK = 0x95AC9329AC4BC9B5


def _init_seeds() -> list[int]:
    seeds: list[int] = []
    for i in range(256):
        value = 0x2B5926535897936A if (i & 0x80) else 0
        if i & 0x40:
            value ^= _MASK
        shift = 0x20
        while shift:
            value = (2 * value) & 0xFFFFFFFFFFFFFFFF
            if i & shift:
                value ^= _MASK
            shift >>= 1
        seeds.append((2 * value) & 0xFFFFFFFFFFFFFFFF)
    return seeds


_SEEDS = _init_seeds()


def symbol64(text: str) -> int:
    """CSymbol64 hash of an ASCII name (case-insensitive), as an int."""
    result = 0xFFFFFFFFFFFFFFFF
    for byte in text.encode("utf-8", "ignore"):
        if 0x41 <= byte <= 0x5A:      # to-lower
            byte += 0x20
        result = (((result << 8) & 0xFFFFFFFFFFFFFFFF) ^ _SEEDS[(result >> 56) & 0xFF] ^ byte)
    return result & 0xFFFFFFFFFFFFFFFF


# Named scalar hashes we care about.
HASH_K_ALPHA = symbol64("k_alpha")
HASH_EMISSIVE_INTENSITY = {L: symbol64(f"layer{L}_emissive_intensity") for L in range(8)}


# --- binary helpers ---------------------------------------------------------

def _u16(d: bytes, o: int) -> int:
    return struct.unpack_from("<H", d, o)[0]


def _u32(d: bytes, o: int) -> int:
    return struct.unpack_from("<I", d, o)[0]


def _u64(d: bytes, o: int) -> int:
    return struct.unpack_from("<Q", d, o)[0]


def _f32(d: bytes, o: int) -> float:
    return struct.unpack_from("<f", d, o)[0]


def _f32_from_u32(word: int) -> float:
    return struct.unpack("<f", struct.pack("<I", word))[0]


def flag_names(flags: int) -> list[str]:
    return [k for k, v in EFLAGS.items() if flags & v]


def parse_material_props(slice_bytes: bytes) -> dict[int, float]:
    """Return {property_name_hash -> float value} from the materialprops table.

    Robust to short/malformed slices (returns {} rather than raising).
    """
    d = slice_bytes
    if len(d) < HEADER_SIZE:
        return {}
    n_props = _u64(d, OFF_MATERIALPROPS_IUSED)
    n_propoff = _u64(d, OFF_PROPOFFSETS_IUSED)
    if n_props > MAX_REASONABLE or n_propoff > MAX_REASONABLE:
        return {}

    props_off = HEADER_SIZE
    propoff_off = props_off + n_props * 4
    if propoff_off + n_propoff * 16 > len(d):
        return {}

    words = [_u32(d, props_off + i * 4) for i in range(n_props)]
    result: dict[int, float] = {}
    for i in range(n_propoff):
        e = propoff_off + i * 16
        key_hash = _u64(d, e)
        byteoffset = _u32(d, e + 8)
        # byteoffset is a byte offset into the u32 word array (offset/4 = index);
        # tolerate a raw index encoding as a fallback.
        if byteoffset % 4 == 0 and (byteoffset // 4) < len(words):
            idx = byteoffset // 4
        elif byteoffset < len(words):
            idx = byteoffset
        else:
            continue
        result[key_hash] = _f32_from_u32(words[idx])
    return result


def decode_material_scalars(slice_bytes: bytes) -> dict:
    """Decode SGMaterialData scalars from a material primary slice.

    Returns the durable scalar dict documented in the module docstring. Always
    returns a full dict with safe defaults; never raises on short/garbage input.
    """
    defaults = {
        "base_color_factor": [1.0, 1.0, 1.0, 1.0],
        "emissive_color": [0.0, 0.0, 0.0],
        "emissive_intensity": 1.0,
        "alpha": 1.0,
        "blend_mode": 0,
        "double_sided": False,
        "mattype": 0,
        "flags": 0,
        "flag_names": [],
        "materialfx": "",
        "is_emissive": False,
        "named_scalars": {},
    }
    d = slice_bytes
    if len(d) < HEADER_SIZE:
        return defaults

    bakecolor = list(struct.unpack_from("<4f", d, OFF_BAKECOLOR))
    emissive = list(struct.unpack_from("<4f", d, OFF_BAKEEMISSIVECOLOR))
    blendmode = _u16(d, OFF_BLENDMODE)
    mattype = _u16(d, OFF_MATTYPE)
    flags = _u32(d, OFF_FLAGS)
    materialfx = f"{_u64(d, OFF_MATERIALFX):016x}"

    props = parse_material_props(d)

    # alpha <- k_alpha materialprop (fallback default 1.0)
    alpha = float(props.get(HASH_K_ALPHA, 1.0))

    # emissive_intensity <- layer0_emissive_intensity, fallback max over layerN
    emissive_intensity = 1.0
    if HASH_EMISSIVE_INTENSITY[0] in props:
        emissive_intensity = float(props[HASH_EMISSIVE_INTENSITY[0]])
    else:
        layer_vals = [props[h] for L, h in HASH_EMISSIVE_INTENSITY.items() if h in props]
        if layer_vals:
            emissive_intensity = float(max(layer_vals))

    return {
        "base_color_factor": [float(x) for x in bakecolor],
        "emissive_color": [float(x) for x in emissive[:3]],
        "emissive_intensity": emissive_intensity,
        "alpha": alpha,
        "blend_mode": int(blendmode),
        "double_sided": bool(flags & E_DOUBLE_SIDED),
        "mattype": int(mattype),
        "flags": int(flags),
        "flag_names": flag_names(flags),
        "materialfx": materialfx,
        "is_emissive": any(v != 0.0 for v in emissive[:3]),
        "named_scalars": {f"{k:016x}": float(v) for k, v in props.items()},
    }
