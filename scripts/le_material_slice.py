"""P1 — probe CGMaterialResourceWin7 archive subresource slices.

Disk format (derived from CMemStream::Attach + CPointerFixupInspector::Inspect<SGMaterialData>):

  [0x000..0x160)  SGMaterialData header — direct memory image
      +0x000  u64   materialfx           (CSymbol64 hash of companion CGMaterialFXResourceWin7)
      +0x008  4×f32 bakecolor            (RGBA base-color multiplier)
      +0x018  4×f32 bakeemissivecolor    (RGBA emissive; all-zero = no emission)
      +0x028  u16   blendmode
      +0x02a  u16   mattype
      +0x02c  u32   flags                (SGMaterialData::EFlags)
      +0x030  f32   shadowfadedist
      +0x034  u32   pad
      +0x038  CTable<uint32_t> materialprops  (0x38 bytes)
                  iused at +0x038+0x028 = +0x060
      +0x070  CMap<CSymbol64,uint32_t> materialpropoffsets  (0x40 bytes)
                  iused at +0x070+0x028 = +0x098
      +0x0b0  CTable<CSymbol64> uvsets  (0x38 bytes)
                  iused at +0x0b0+0x028 = +0x0d8
      +0x0e8  CMap<uint32_t,CSymbol64> permutations  (0x40 bytes)
                  iused at +0x0e8+0x028 = +0x110
      +0x128  CTable<SShaderInputData> auxillaryinputs  (0x38 bytes)
                  iused at +0x128+0x028 = +0x150

  [0x160..0x160+A)  materialprops array  (n_props × 4 bytes)
  [..+B)            materialpropoffsets entries  (n_propoffsets × 16 bytes)
  [..+C)            uvsets array  (n_uvsets × 8 bytes)
  [..+D)            permutations entries  (n_perms × 16 bytes)
  [..+E)            auxillaryinputs array  (n_inputs × 0x20 bytes each):
      +0x00  u64  inputname     (CSymbol64 shader input name — e.g. 'albedo', 'normal')
      +0x08  u64  textureassetid (CSymbol64 hash of CGTextureResourceWin7)
      +0x10  u16  type
      +0x12  u16  layer
      +0x14  u16  engineresource
      +0x16  u16  slot
      +0x18  f32  uscale
      +0x1c  f32  vscale

SGMaterialData::EFlags bits:
    eDoubleSided              = 0x001
    eCastShadows              = 0x002
    eGIOccluder               = 0x004
    eGIReceiver               = 0x008
    eUseAmbientSpecular       = 0x010
    eUseVertexLighting        = 0x020
    eUseFoliageAnimation      = 0x040
    eEyeMaterial              = 0x080
    eOutputTransparentVelocity = 0x100

Run with Windows Python so le_oodle can load the game Oodle DLL:
    python.exe scripts/le_material_slice.py
    python.exe scripts/le_material_slice.py --hash 0703fd2acd5803e9
    python.exe scripts/le_material_slice.py --all
"""

from __future__ import annotations

import argparse
import csv
import json
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path

from le_oodle import load_decompressed
from le_archive_decode import (
    ARCHIVE_GPU,
    ARCHIVE_PRIMARY,
    DEFAULT_HASH_LOOKUP,
    archive_offsets,
    entry_at,
    load_hash_lookup,
    parse_header,
)
import sys

# Windows consoles default to cp1252 and argparse echoes this module's docstring
# on --help, so any non-ASCII in it raises UnicodeEncodeError the moment stdout
# is not a console (a pipe, a redirect, CI). Force UTF-8 on the streams we own.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):   # already-wrapped or non-reconfigurable
        pass


MATERIAL_TYPE    = 0x117d2b6509c8ff79
MATERIAL_FX_TYPE = 0x525e5a64a59eb745

SGMAT_HEADER_SIZE = 0x160   # sizeof(SGMaterialData) runtime layout

# Fixed-offset fields within the 0x160-byte header
OFF_MATERIALFX          = 0x000   # u64
OFF_BAKECOLOR           = 0x008   # 4×f32
OFF_BAKEEMISSIVECOLOR   = 0x018   # 4×f32
OFF_BLENDMODE           = 0x028   # u16
OFF_MATTYPE             = 0x02a   # u16
OFF_FLAGS               = 0x02c   # u32
OFF_SHADOWFADEDIST      = 0x030   # f32
# CTable/CMap iused counts (each at fieldbase + 0x028 within the 0x38-byte CTable)
OFF_MATERIALPROPS_IUSED = 0x038 + 0x028   # = 0x060
OFF_PROPOFFSETS_IUSED   = 0x070 + 0x028   # = 0x098
OFF_UVSETS_IUSED        = 0x0b0 + 0x028   # = 0x0d8
OFF_PERMS_IUSED         = 0x0e8 + 0x028   # = 0x110
OFF_AUXINPUTS_IUSED     = 0x128 + 0x028   # = 0x150

SIZEOF_SHADERINPUTDATA  = 0x20   # sizeof(SShaderInputData)

# SGMaterialData::EFlags
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

DEFAULT_ARCHIVE  = "0703fd2acd5803e9"
DEFAULT_OUT_TSV  = Path("generic_rebuilds/material_slice_probe.tsv")
DEFAULT_OUT_JSON = Path("generic_rebuilds/material_slice_summary.json")


def u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def f32(data: bytes, off: int) -> float:
    return struct.unpack_from("<f", data, off)[0]


def f32x4(data: bytes, off: int) -> tuple[float, float, float, float]:
    return struct.unpack_from("<4f", data, off)


def hex64(v: int) -> str:
    return f"{v:016x}"


def flag_names(flags: int) -> str:
    names = [k for k, v in EFLAGS.items() if flags & v]
    return "|".join(names) if names else "none"


@dataclass
class ShaderInput:
    inputname: str
    textureassetid: str
    type: int
    layer: int
    engineresource: int
    slot: int
    uscale: float
    vscale: float


@dataclass
class MaterialRow:
    archive_hash: str
    resource_hash: str
    slice_size: int
    # Fixed header fields
    materialfx: str
    bakecolor_r: float
    bakecolor_g: float
    bakecolor_b: float
    bakecolor_a: float
    bakeemissive_r: float
    bakeemissive_g: float
    bakeemissive_b: float
    bakeemissive_a: float
    blendmode: int
    mattype: int
    flags: str
    flags_named: str
    shadowfadedist: float
    # Table counts
    n_materialprops: int
    n_propoffsets: int
    n_uvsets: int
    n_permutations: int
    n_auxinputs: int
    # Computed/derived
    is_emissive: bool
    array_data_offset: int
    parse_ok: bool
    parse_error: str
    # Texture bindings (serialized as JSON)
    texture_inputs: str    # JSON list of {inputname, textureassetid, ...}
    # Raw header hex for debugging
    header_hex: str


def parse_material_slice(data: bytes, start: int, size: int, archive_hash: str, resource_hash: str) -> MaterialRow:
    """Parse a CGMaterialResourceWin7 subresource slice."""
    if start + SGMAT_HEADER_SIZE > len(data):
        return MaterialRow(
            archive_hash=archive_hash, resource_hash=resource_hash,
            slice_size=size, materialfx="", bakecolor_r=0, bakecolor_g=0, bakecolor_b=0, bakecolor_a=0,
            bakeemissive_r=0, bakeemissive_g=0, bakeemissive_b=0, bakeemissive_a=0,
            blendmode=0, mattype=0, flags="", flags_named="", shadowfadedist=0,
            n_materialprops=0, n_propoffsets=0, n_uvsets=0, n_permutations=0, n_auxinputs=0,
            is_emissive=False, array_data_offset=0, parse_ok=False,
            parse_error=f"slice too short: start={start:#x} size={size:#x}",
            texture_inputs="[]", header_hex="",
        )

    h = data[start:start + SGMAT_HEADER_SIZE]

    materialfx      = hex64(u64(h, OFF_MATERIALFX))
    bakecolor       = f32x4(h, OFF_BAKECOLOR)
    bakeemissive    = f32x4(h, OFF_BAKEEMISSIVECOLOR)
    blendmode       = u16(h, OFF_BLENDMODE)
    mattype         = u16(h, OFF_MATTYPE)
    flags_val       = u32(h, OFF_FLAGS)
    shadowfadedist  = f32(h, OFF_SHADOWFADEDIST)

    n_props         = u64(h, OFF_MATERIALPROPS_IUSED)
    n_propoffsets   = u64(h, OFF_PROPOFFSETS_IUSED)
    n_uvsets        = u64(h, OFF_UVSETS_IUSED)
    n_perms         = u64(h, OFF_PERMS_IUSED)
    n_inputs        = u64(h, OFF_AUXINPUTS_IUSED)

    # Sanity-check counts (>10000 suggests wrong parse)
    MAX_REASONABLE = 10_000
    if any(v > MAX_REASONABLE for v in (n_props, n_propoffsets, n_uvsets, n_perms, n_inputs)):
        return MaterialRow(
            archive_hash=archive_hash, resource_hash=resource_hash,
            slice_size=size, materialfx=materialfx,
            bakecolor_r=bakecolor[0], bakecolor_g=bakecolor[1], bakecolor_b=bakecolor[2], bakecolor_a=bakecolor[3],
            bakeemissive_r=bakeemissive[0], bakeemissive_g=bakeemissive[1], bakeemissive_b=bakeemissive[2], bakeemissive_a=bakeemissive[3],
            blendmode=blendmode, mattype=mattype, flags=f"{flags_val:#010x}", flags_named=flag_names(flags_val),
            shadowfadedist=shadowfadedist,
            n_materialprops=int(n_props), n_propoffsets=int(n_propoffsets), n_uvsets=int(n_uvsets),
            n_permutations=int(n_perms), n_auxinputs=int(n_inputs),
            is_emissive=any(v != 0.0 for v in bakeemissive[:3]),
            array_data_offset=SGMAT_HEADER_SIZE,
            parse_ok=False, parse_error=f"implausible counts: props={n_props} propoff={n_propoffsets} uvsets={n_uvsets} perms={n_perms} inputs={n_inputs}",
            texture_inputs="[]", header_hex=h.hex(),
        )

    # Compute sequential array data offsets
    off = SGMAT_HEADER_SIZE
    off += n_props * 4            # materialprops: u32 each
    off += n_propoffsets * 16     # materialpropoffsets entries: CSimpleKey<u32,CSymbol64> = 16 bytes
    off += n_uvsets * 8           # uvsets: CSymbol64 = 8 bytes
    off += n_perms * 16           # permutations entries: CSimpleKey<CSymbol64,u32> = 16 bytes

    # Now parse auxillaryinputs
    inputs: list[ShaderInput] = []
    parse_ok = True
    parse_error = ""
    abs_inputs_start = start + off

    if abs_inputs_start + n_inputs * SIZEOF_SHADERINPUTDATA > len(data):
        parse_ok = False
        parse_error = f"auxinputs array out of bounds: abs={abs_inputs_start:#x} count={n_inputs}"
    else:
        for i in range(n_inputs):
            e = abs_inputs_start + i * SIZEOF_SHADERINPUTDATA
            inputs.append(ShaderInput(
                inputname      = hex64(u64(data, e + 0x00)),
                textureassetid = hex64(u64(data, e + 0x08)),
                type           = u16(data, e + 0x10),
                layer          = u16(data, e + 0x12),
                engineresource = u16(data, e + 0x14),
                slot           = u16(data, e + 0x16),
                uscale         = f32(data, e + 0x18),
                vscale         = f32(data, e + 0x1c),
            ))

    is_emissive = any(v != 0.0 for v in bakeemissive[:3])  # only check RGB, not alpha

    return MaterialRow(
        archive_hash=archive_hash,
        resource_hash=resource_hash,
        slice_size=size,
        materialfx=materialfx,
        bakecolor_r=bakecolor[0], bakecolor_g=bakecolor[1], bakecolor_b=bakecolor[2], bakecolor_a=bakecolor[3],
        bakeemissive_r=bakeemissive[0], bakeemissive_g=bakeemissive[1], bakeemissive_b=bakeemissive[2], bakeemissive_a=bakeemissive[3],
        blendmode=blendmode,
        mattype=mattype,
        flags=f"{flags_val:#010x}",
        flags_named=flag_names(flags_val),
        shadowfadedist=shadowfadedist,
        n_materialprops=int(n_props),
        n_propoffsets=int(n_propoffsets),
        n_uvsets=int(n_uvsets),
        n_permutations=int(n_perms),
        n_auxinputs=int(n_inputs),
        is_emissive=is_emissive,
        array_data_offset=SGMAT_HEADER_SIZE,
        parse_ok=parse_ok,
        parse_error=parse_error,
        texture_inputs=json.dumps([asdict(inp) for inp in inputs]),
        header_hex=h.hex(),
    )


def compressed_stub(path: Path) -> bool:
    return path.stat().st_size in (44, 57)


def scan_archive(archive_hash: str, names: dict[int, str], type_filter: int = MATERIAL_TYPE) -> list[MaterialRow]:
    primary_path = ARCHIVE_PRIMARY / archive_hash
    gpu_path     = ARCHIVE_GPU / archive_hash
    if compressed_stub(primary_path) or compressed_stub(gpu_path):
        return []
    primary_bytes = load_decompressed(primary_path)
    gpu_bytes     = load_decompressed(gpu_path)
    prim_size, gpu_size, data_off, header_off = archive_offsets(primary_bytes, gpu_bytes)

    rows: list[MaterialRow] = []

    for hdr_idx in range(2):
        header = parse_header(primary_bytes, header_off if hdr_idx == 0 else header.end)
        for i in range(header.contents.count):
            type_hash, name_hash, value = struct.unpack_from(
                "<QQQ", primary_bytes, header.contents.off + i * 24
            )
            if type_hash != type_filter or value >= header.entries.count:
                continue

            pos, size = entry_at(primary_bytes, header, value)
            abs_start = data_off + pos

            row = parse_material_slice(
                primary_bytes, abs_start, size, archive_hash, hex64(name_hash)
            )
            rows.append(row)

    return rows


def write_tsv(path: Path, rows: list, fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hash", default=DEFAULT_ARCHIVE)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--materialfx", action="store_true", help="scan CGMaterialFXResourceWin7 instead")
    parser.add_argument("--out-tsv",  type=Path, default=DEFAULT_OUT_TSV)
    parser.add_argument("--out-json", type=Path, default=DEFAULT_OUT_JSON)
    args = parser.parse_args()

    names = load_hash_lookup(DEFAULT_HASH_LOOKUP)
    type_filter = MATERIAL_FX_TYPE if args.materialfx else MATERIAL_TYPE

    if args.all:
        import re
        lookup = json.loads(DEFAULT_HASH_LOOKUP.read_text()) if DEFAULT_HASH_LOOKUP.exists() else {}
        archive_hashes = sorted(set(
            k.lstrip("0x").lower() for k in lookup
            if re.fullmatch(r"0x[0-9a-fA-F]{16}", k)
        ))
    else:
        archive_hashes = [args.hash]

    all_rows: list[MaterialRow] = []
    for ahash in archive_hashes:
        print(f"scanning {ahash} ...", flush=True)
        try:
            rows = scan_archive(ahash, names, type_filter)
            print(f"  found {len(rows)} material slices")
            for r in rows:
                status = "OK" if r.parse_ok else f"ERROR: {r.parse_error}"
                emissive_tag = " [EMISSIVE]" if r.is_emissive else ""
                print(f"  {r.resource_hash} inputs={r.n_auxinputs} emissive={r.is_emissive}{emissive_tag} {status}")
                if r.parse_ok and r.n_auxinputs > 0:
                    inputs = json.loads(r.texture_inputs)
                    for inp in inputs:
                        print(f"    input '{inp['inputname']}' -> tex {inp['textureassetid']}")
            all_rows.extend(rows)
        except Exception as exc:
            print(f"  FAILED: {exc}")

    if all_rows:
        fieldnames = list(asdict(all_rows[0]).keys())
        write_tsv(args.out_tsv, all_rows, fieldnames)
        ok_count = sum(1 for r in all_rows if r.parse_ok)
        print(f"\nwrote {len(all_rows)} rows ({ok_count} parsed OK) -> {args.out_tsv}")

    summary = {
        "archive_count": len(archive_hashes),
        "material_count": len(all_rows),
        "parse_ok": sum(1 for r in all_rows if r.parse_ok),
        "emissive_count": sum(1 for r in all_rows if r.is_emissive),
        "total_texture_inputs": sum(r.n_auxinputs for r in all_rows if r.parse_ok),
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(summary, indent=2))
    print(f"wrote summary -> {args.out_json}")


if __name__ == "__main__":
    main()
