"""CGShaderSetResourceWin10 -- where Echo VR's texture ROLES actually live.

## Why this module exists (and why the previous answer was wrong)

`evr_material_resource` was written on the assumption that
`CGMaterialResourceWin10.auxillaryinputs` held the per-texture role bindings.
It does not.  Measured across 1727 shipped materials::

    materials with auxillaryinputs binds : 337
    total binds                          : 674
    distinct inputnames                  : 2
        54cc9d89af0bf18a  cutting_cut_decal
        b20feace74483152  cutting_scorch_decal

Exactly two names, two per material.  `auxillaryinputs` is a cutting/scorch
decal slot, not the surface texture table -- and Lone Echo's own
`material_scalars.AUX_INPUT_NAMES` says so in as many words.  That table was a
dead end, and the material resource contributes SCALARS (`k_alpha`, blend mode,
mattype, bakecolor) and nothing else.

The roles are in the **shader set**, exactly as they are in Lone Echo, whose
`scripts/le_shaderset_scan.py` scans `CGShaderSetResourceWin7` slices for the
same `SShaderInputData` struct.  Echo VR's equivalent is
`CGShaderSetResourceWin10` (`4984e2bbb2ddb256`), and in a flat extract it is
already decompressed -- so this is strictly easier than the Lone Echo path,
which has to go through Oodle first.

## `SShaderInputData` (0x20 bytes) -- unchanged between the two games

    +0x00  u64  inputname       CSymbol64 of the slot name -- THE ROLE
    +0x08  u64  textureassetid  CSymbol64 of the texture resource
    +0x10  u16  type
    +0x12  u16  layer
    +0x14  u16  engineresource
    +0x16  u16  slot
    +0x18  f32  uscale
    +0x1c  f32  vscale

## The anchored byte-scan was replaced (2026-08) -- it was inventing binds

The original version of this module found binds by sliding an anchored,
plausibility-checked window over the raw file looking for a known texture
hash at `+8`.  That is a coincidence detector, not a structure reader: a
shader set's real `SShaderInputData` table lives inside a specific,
count-prefixed, alignment-driven element layout, and roughly half of it is
legitimately empty (no bound texture in that table at all -- the material
gets its texture some other way).  The anchored scan could not tell "this
8-byte value happens to equal a real texture hash, somewhere in the DXBC
bytecode or an unrelated subtable" apart from "this is a genuine bound
texture" -- both look identical to a floor/plausibility check that never
verifies it is reading inside the real element array.

Spot-check against the real structure (below) on a random sample of 40 shader
sets: **all 40 decode byte-exact to EOF**, but only 18/40 (45%) have *any*
bound texture in the real table.  Two shader sets the anchored scan had
confidently reported real textures for -- `d46780386d6debab` and
`ea8a7fee4ce240f9`, both used by materials in the `576ed3f8428ebc4b` level --
turned out to have **zero** bound texture records in the real table.  The
scan's reported textures for those materials were coincidental matches
elsewhere in the file, indistinguishable from a real bind by the old method's
own checks.

## The real structure

`CGShaderSetResourceWin10` is `NRadEngine`'s standard shader-set container --
the same on-disk grammar this session confirmed (byte-exact, EOF-consuming)
against a disassembly-driven decoder from a separate, independent
reverse-engineering project (`quest_combat_port`, `tools/convert/shaderset_wall/
ssbind_true.py`, citing disassembly of `NRadEngine::CGShaderSetResource`'s
serializer and MATBIND_RE finding n93). `_parse_structured` below is a port of
that decoder: a 96-byte head, then `count` 1072-byte elements (shader
variants), each holding five `CTable<SShaderInputData>` sub-tables (one per
pipeline stage: VS, HS, DS, GS, PS) at fixed offsets, each `SShaderInputData`
32 bytes:

    +0x00  u64  inputname       CSymbol64 of the slot name -- THE ROLE
    +0x08  u64  textureassetid  CSymbol64 of the texture resource (0 or
                                 0xFFFFFFFFFFFFFFFF = unbound)
    +0x10  u16  type            0x9/0xA = SSBO, not a texture (see below)
    +0x12  u16  layer
    +0x14  u16  engineresource
    +0x16  u16  slot
    +0x18  f32  uscale
    +0x1c  f32  vscale

Parsing is table-driven (count-prefix + alignment, matching
`ssbind_true.parse_shaderset`) and rejects the whole file (returns `None`) on
any structural mismatch -- there is no plausibility-check fallback, because a
genuine element table either accounts for every byte to EOF or it does not.

Two guards beyond "not unbound": a `type` of `0x9`/`0xA` is an SSBO row, not a
texture slot -- `ssbind_true`'s own finding (n93, the "`c947d039` mirage") is
that treating an SSBO row's fields as a texture bind fabricates one that was
never there, so those rows are skipped outright regardless of what their `+8`
field holds.  `type == 0xFFFF` is `SShaderInputData`'s own end-of-table
sentinel and is skipped the same way.
"""

from __future__ import annotations

import json
import struct
import sys
from collections import Counter
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _path in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import evr_material_resource as evr_mat
from evr_resource_types import (
    SHADER_SET_RESOURCE,
    STANDALONE_SHADER,
    normalise_hash,
    resource_path,
)


def all_shaderset_hashes(root: Path) -> set:
    """Every CGShaderSetResourceWin10 present in the extract."""
    directory = Path(root) / SHADER_SET_RESOURCE
    if not directory.is_dir():
        return set()
    return {
        normalise_hash(p.stem if p.suffix == ".bin" else p.name)
        for p in directory.iterdir() if p.is_file()
    }


_SS_HEAD_SIZE = 96
_SS_ELEM_SIZE = 1072
#: (offset-within-element, record-size, alignment-floor) for the 5 per-stage
#: `CTable<SShaderInputData>` sub-tables (VS,HS,DS,GS,PS) + 5 sampler tables +
#: 1 trailing `CTable<u32>`.  Ported from `ssbind_true.parse_shaderset`
#: (`quest_combat_port/tools/convert/shaderset_wall/ssbind_true.py`), itself
#: vendored from the community EchoVR-Map-Editor's
#: `echomod.resources.cgshaderset_resource.parse_shaderset`, EOF-exact on
#: 3692/3692 stock shader sets -- the same 3692 this extract carries.
_SS_SUBTABLES = (
    tuple((128 + 56 * k, 32, 8) for k in range(5))       # 5x CTable<SShaderInputData>
    + tuple((408 + 56 * k, 32, 8) for k in range(5))     # 5x CTable<SSamplerData>
    + ((1016, 4, 4),)                                    # CTable<u32>
)
#: `SShaderInputData.type` values that are not a texture slot at all.
_SS_TYPE_SSBO = (0x0009, 0x000A)
_SS_TYPE_SENTINEL = 0xFFFF
_UNBOUND = (0, 0xFFFFFFFFFFFFFFFF)


def _align_up(x: int, a: int) -> int:
    return x + ((-x) % a) if a > 1 else x


def _parse_structured(data: bytes):
    """Byte-complete decomposition of a shader set Primary, or `None` on ANY
    structural mismatch (a real element table always accounts for every byte
    to EOF, so a `None` here means this file is not laid out the way every
    other stock shader set is -- not "keep guessing at a lower confidence").
    Returns `[{"inputs": [[rec, ...] x5 stages]}, ...]`, one dict per variant.
    """
    try:
        if len(data) < _SS_HEAD_SIZE:
            return None
        count = int.from_bytes(data[48:56], "little")
        talign = int.from_bytes(data[24:28], "little")
        if count > 0xFFFF:
            return None
        cur = _SS_HEAD_SIZE
        elements = []
        if count:
            cur = _align_up(cur, max(talign, 8))
            if cur + _SS_ELEM_SIZE * count > len(data):
                return None
            elem_start = cur
            cur += _SS_ELEM_SIZE * count
            for i in range(count):
                base = elem_start + _SS_ELEM_SIZE * i
                inputs = [[] for _ in range(5)]
                for ti, (off, esize, floor) in enumerate(_SS_SUBTABLES):
                    c = int.from_bytes(data[base + off + 48:base + off + 56], "little")
                    al = int.from_bytes(data[base + off + 24:base + off + 28], "little")
                    if not c:
                        continue
                    cur = _align_up(cur, max(al, floor))
                    n = esize * c
                    if cur + n > len(data):
                        return None
                    chunk = data[cur:cur + n]
                    cur += n
                    if ti < 5:
                        for j in range(c):
                            o = 32 * j
                            name = int.from_bytes(chunk[o:o + 8], "little")
                            tex = int.from_bytes(chunk[o + 8:o + 16], "little")
                            typ = int.from_bytes(chunk[o + 16:o + 18], "little")
                            layer = int.from_bytes(chunk[o + 18:o + 20], "little")
                            engres = int.from_bytes(chunk[o + 20:o + 22], "little")
                            slot = int.from_bytes(chunk[o + 22:o + 24], "little")
                            uscale, vscale = struct.unpack_from("<ff", chunk, o + 24)
                            inputs[ti].append(dict(
                                name=name, texture=tex, type=typ, layer=layer,
                                engineresource=engres, slot=slot,
                                uscale=uscale, vscale=vscale))
                elements.append({"inputs": inputs})
        msz = int.from_bytes(data[64 + 8:64 + 16], "little")
        mal = int.from_bytes(data[64 + 24:64 + 28], "little")
        if msz:
            if mal:
                cur = _align_up(cur, mal)
            if cur + msz > len(data):
                return None
            cur += msz
        if cur != len(data):
            return None
        return elements
    except (IndexError, struct.error):
        return None


def _binds_from_data(data: bytes, shaderset_hash) -> list:
    """`_parse_structured(data)` -> real, bound `TextureBind` rows only."""
    elements = _parse_structured(data)
    if elements is None:
        return []

    hash_str = normalise_hash(shaderset_hash)
    binds: list = []
    for element in elements:
        for stage_recs in element["inputs"]:
            for r in stage_recs:
                if r["type"] in _SS_TYPE_SSBO or r["type"] == _SS_TYPE_SENTINEL:
                    continue
                if r["texture"] in _UNBOUND:
                    continue
                binds.append(evr_mat.TextureBind(
                    inputname_hash=normalise_hash(r["name"]),
                    textureassetid_hash=normalise_hash(r["texture"]),
                    type=r["type"], layer=r["layer"],
                    engineresource=r["engineresource"], slot=r["slot"],
                    uscale=r["uscale"], vscale=r["vscale"],
                    shaderset_hash=hash_str,
                ))
    return binds


def binds_for(root: Path, shaderset_hash, *_unused) -> list:
    """Every genuinely bound `SShaderInputData` in one shader set.

    Reads the real element/sub-table structure (see module docstring) rather
    than scanning for coincidental hash matches. `*_unused` absorbs the old
    `known_textures` anchor argument some callers may still pass; it is not
    needed -- a structural decode either accounts for the whole file or it
    doesn't, so there is nothing left to anchor against.
    """
    path = resource_path(root, SHADER_SET_RESOURCE, shaderset_hash)
    if path is None:
        return []
    return _binds_from_data(path.read_bytes(), shaderset_hash)


def role_textures(root: Path, shaderset_hash, names: dict | None = None) -> dict:
    """`{role_key -> texture_hash}` for one shader set."""
    from le_mesh import materials as le_materials

    binds = binds_for(root, shaderset_hash)
    return le_materials.roles_from_input_rows(binds, names or {})


def audit(root: Path, hash_lookup: Path | None = None,
          limit: int | None = None) -> dict:
    """Do Echo VR's shader sets carry nameable texture roles?

    The same question `evr_materials.audit_inputnames` asked of the material
    resource, pointed at the table that actually holds the answer.  Reports
    every distinct inputname by frequency and whether anything can name it.
    """
    from evr_materials import load_hash_lookup
    from le_mesh import materials as le_materials

    root = Path(root)
    names = load_hash_lookup(hash_lookup)
    textures = evr_mat.all_texture_hashes(root)

    if not textures:
        return {"error": f"no cgtextureresourceWin10 directory under {root} -- "
                         f"nothing here has ever shipped a texture"}

    shadersets = sorted(all_shaderset_hashes(root))
    if not shadersets:
        return {"error": f"no {SHADER_SET_RESOURCE} directory under {root}"}
    if limit:
        shadersets = shadersets[:limit]

    by_hash: Counter = Counter()
    slots_for: dict = {}
    with_binds = 0
    total = 0
    example: dict = {}

    for shaderset in shadersets:
        binds = binds_for(root, shaderset, textures)
        if not binds:
            continue
        with_binds += 1
        total += len(binds)
        for bind in binds:
            by_hash[bind.inputname_hash] += 1
            slots_for.setdefault(bind.inputname_hash, set()).add(bind.slot)
        if not example and len(binds) >= 2:
            example = {
                "shaderset": shaderset,
                "binds": [
                    {"slot": b.slot, "layer": b.layer,
                     "inputname": b.inputname_hash,
                     "role": le_materials.role_for_inputname(
                         b.inputname_hash, b.slot, names),
                     "texture": b.textureassetid_hash,
                     "uv": [round(b.uscale, 4), round(b.vscale, 4)]}
                    for b in binds[:12]
                ],
            }

    resolved: dict = {}
    unresolved: list = []
    for inputname, count in by_hash.most_common():
        role = le_materials.role_for_inputname(inputname, None, names)
        if role.startswith("unknown_s"):
            unresolved.append({"inputname": inputname, "binds": count,
                               "slots": sorted(slots_for.get(inputname, ()))})
        else:
            resolved[inputname] = {"role": role, "binds": count}

    named = sum(v["binds"] for v in resolved.values())
    return {
        "shadersets_scanned": len(shadersets),
        "shadersets_with_binds": with_binds,
        "total_binds": total,
        "distinct_inputnames": len(by_hash),
        "binds_named": named,
        "binds_unnamed": total - named,
        "hash_lookup_entries": len(names),
        "resolved": dict(list(resolved.items())[:40]),
        "unresolved": unresolved[:40],
        "example": example,
    }


def build_indexes(root: Path, materials: set, textures: set,
                  *, progress=None) -> tuple:
    """One pass over every shader set -> `(by_material, by_texture)`.

    Both indexes come from the same read because scanning 3692 files twice is
    wasteful and they answer the same question from different sides:

    * `by_material` -- `{material_hash -> [shaderset]}`, the direct join.  It is
      SPARSE: only ~21%% of materials are named by any shader set, and on a real
      level almost none of the scene's materials were, which is why it cannot be
      the only route.
    * `by_texture` -- `{texture_hash -> [shaderset]}`, the join that actually
      carries a level.  Every model has a reliable texture list from
      `CGTextureStreamingResourceWin10`, and a shader set binds textures, so
      overlap between the two identifies the shader set without needing the
      material edge at all.

    `by_texture` is built from `_binds_from_data`'s structured decode (real
    bound records only -- see the module docstring), not a byte scan, so it is
    only as complete as a shader set's real bind table: roughly half of shader
    sets legitimately bind nothing here, which the texture-overlap ranking
    that consumes this index already tolerates (it ranks whatever candidates
    exist rather than assuming full coverage). `textures` is accepted for
    call-site compatibility but no longer used -- the structured decode does
    not need an anchor set.
    """
    root = Path(root)
    by_material: dict = {}
    by_texture: dict = {}

    shadersets = sorted(all_shaderset_hashes(root))
    for i, shaderset in enumerate(shadersets):
        if progress and i and i % 1000 == 0:
            progress(i, len(shadersets))
        path = resource_path(root, SHADER_SET_RESOURCE, shaderset)
        if path is None:
            continue
        data = path.read_bytes()

        seen_mat: set = set()
        for offset in range(0, len(data) - 8 + 1, 4):
            value = normalise_hash(
                int.from_bytes(data[offset:offset + 8], "little"))
            if value in materials and value not in seen_mat:
                seen_mat.add(value)
                by_material.setdefault(value, []).append(shaderset)

        for bind in _binds_from_data(data, shaderset):
            by_texture.setdefault(bind.textureassetid_hash, []).append(shaderset)

    return by_material, by_texture


def shadersets_for_textures(by_texture: dict, model_textures,
                            *, limit: int = 4) -> list:
    """Rank shader sets by how many of a model's textures they bind.

    Returns `[(shaderset, overlap), ...]`, best first.  A model's textures come
    from its own streaming resource, so a shader set that binds several of them
    is that model's shader set; one that binds a single shared texture (an atlas,
    a default normal) is not, which is what the ranking separates.
    """
    wanted = [normalise_hash(t) for t in (model_textures or ()) if t]
    if not wanted:
        return []
    scores: Counter = Counter()
    for texture in set(wanted):
        for shaderset in by_texture.get(texture, ()):
            scores[shaderset] += 1
    return scores.most_common(limit)


def build_material_shaderset_index(root: Path, materials: set | None = None,
                                   *, progress=None) -> dict:
    """`{material_hash -> [shaderset_hash, ...]}` by inverting the reference.

    ## Why this direction

    The join runs shader set -> material, not the other way.  Measured: of 400
    materials, **zero** reference a shader set anywhere in their bytes, and
    `materialfx` is never a shader set hash.  Of 200 shader sets, 69 name a
    material.  So there is no forward lookup to do -- the index has to be built
    by reading every shader set once and inverting.

    That also explains why the model-level census found materials but no shader
    sets on most models: the model names its materials, and the shader set finds
    the model's material rather than the reverse.

    A material may legitimately map to SEVERAL shader sets (different
    permutations of the same material), which is why the value is a list.
    """
    root = Path(root)
    materials = materials or evr_mat.all_material_hashes(root)
    if not materials:
        return {}

    index: dict = {}
    shadersets = sorted(all_shaderset_hashes(root))
    for i, shaderset in enumerate(shadersets):
        if progress and i % 500 == 0:
            progress(i, len(shadersets))
        path = resource_path(root, SHADER_SET_RESOURCE, shaderset)
        if path is None:
            continue
        data = path.read_bytes()
        seen: set = set()
        for offset in range(0, len(data) - 8 + 1, 4):
            value = normalise_hash(
                int.from_bytes(data[offset:offset + 8], "little"))
            if value in materials and value not in seen:
                seen.add(value)
                index.setdefault(value, []).append(shaderset)
    return index


def audit_link(root: Path, limit: int | None = 400) -> dict:
    """How does a MATERIAL find its SHADER SET?

    Most models reference materials but no shader set, so the join has to exist
    between the two resources rather than on the model.  Three candidates, all
    tested here rather than argued about:

    1. `materialfx` -- the CSymbol64 in the material header.  If it is a shader
       set hash, the link is a single field and everything else is noise.
    2. Anywhere else in the material file (the `permutations` CMap is the
       obvious home -- a shader set IS a compiled permutation of a material).
    3. The reverse: the shader set file naming its material.

    Reports which direction actually carries the reference, and for the material
    side, WHICH table the hit lands in -- so the answer names a field rather
    than an offset.
    """
    root = Path(root)
    shadersets = all_shaderset_hashes(root)
    materials = evr_mat.all_material_hashes(root)
    if not shadersets or not materials:
        return {"error": "need both CGMaterialResourceWin10 and "
                         "CGShaderSetResourceWin10 directories"}

    sample = sorted(materials)[:limit or len(materials)]
    materialfx_hits = 0
    body_hits = 0
    by_table: Counter = Counter()
    examples: list = []

    for material_hash in sample:
        path = resource_path(root, evr_mat.MATERIAL_RESOURCE, material_hash)
        if path is None:
            continue
        data = path.read_bytes()
        try:
            header = evr_mat.parse_header(data)
        except evr_mat.MaterialParseError:
            continue

        if header.materialfx in shadersets:
            materialfx_hits += 1

        found: list = []
        for offset in range(0, len(data) - 8 + 1, 4):
            value = normalise_hash(
                int.from_bytes(data[offset:offset + 8], "little"))
            if value in shadersets:
                # Name the table this offset falls in, so the answer is a field.
                table = "header/preamble"
                for name, start in header.payload_offsets.items():
                    if start <= offset < start + header.used_sizes.get(name, 0):
                        table = name
                        break
                found.append((offset, value, table))

        if found:
            body_hits += 1
            for _off, _val, table in found:
                by_table[table] += 1
            if len(examples) < 5:
                examples.append({
                    "material": material_hash,
                    "materialfx": header.materialfx,
                    "materialfx_is_shaderset": header.materialfx in shadersets,
                    "hits": [{"offset": o, "shaderset": v, "table": t}
                             for o, v, t in found[:4]],
                })

    # The reverse direction, on a smaller sample.
    reverse_hits = 0
    reverse_sample = sorted(shadersets)[:200]
    for shaderset in reverse_sample:
        path = resource_path(root, SHADER_SET_RESOURCE, shaderset)
        if path is None:
            continue
        data = path.read_bytes()
        for offset in range(0, len(data) - 8 + 1, 4):
            value = normalise_hash(
                int.from_bytes(data[offset:offset + 8], "little"))
            if value in materials:
                reverse_hits += 1
                break

    return {
        "materials_sampled": len(sample),
        "materialfx_is_a_shaderset": materialfx_hits,
        "materials_referencing_a_shaderset_anywhere": body_hits,
        "hits_by_table": dict(by_table.most_common()),
        "shadersets_sampled": len(reverse_sample),
        "shadersets_referencing_a_material": reverse_hits,
        "examples": examples,
    }


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", type=Path, help="flat Echo VR extract root")
    ap.add_argument("--hash-lookup", type=Path, default=None,
                    help="hash_lookup.json of cracked CSymbol64 names")
    ap.add_argument("--limit", type=int, default=None,
                    help="cap how many shader sets are scanned")
    ap.add_argument("--crack", action="store_true",
                    help="brute-force candidate names for unresolved hashes")
    ap.add_argument("--link", action="store_true",
                    help="find how a MATERIAL references its SHADER SET")
    args = ap.parse_args()

    if args.link:
        print(json.dumps(audit_link(args.root, args.limit or 400), indent=2))
        raise SystemExit(0)

    report = audit(args.root, args.hash_lookup, args.limit)

    if args.crack and report.get("unresolved"):
        from evr_materials import crack_inputnames

        cracked = crack_inputnames(report["unresolved"])
        report["cracked_by_wordlist"] = cracked
        if cracked:
            report["unresolved"] = [u for u in report["unresolved"]
                                    if u["inputname"] not in cracked]

    print(json.dumps(report, indent=2))


def dominant_uv_scale(root: Path, shaderset_hash) -> tuple:
    """`(uscale, vscale)` this shader set applies to its texture reads.

    `SShaderInputData` carries a per-bind UV scale that this pipeline parsed and
    then discarded. Measured over 795 binds: 83 (~10%) are NOT (1,1) -- values
    like (1,2), (0.25,1), (0.35,1.4) -- so dropping it renders those materials
    at the wrong tiling density.

    A material's binds almost always agree, so the most common non-unit value
    wins; `(1.0, 1.0)` means "nothing to apply" and lets the caller skip the
    Mapping node entirely.
    """
    scales: Counter = Counter()
    for bind in binds_for(root, shaderset_hash):
        scales[(round(bind.uscale, 4), round(bind.vscale, 4))] += 1
    for (u, v), _count in scales.most_common():
        if (u, v) != (1.0, 1.0) and u > 0 and v > 0:
            return (u, v)
    return (1.0, 1.0)
