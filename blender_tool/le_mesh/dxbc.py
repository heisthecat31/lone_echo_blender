"""DXBC RDEF reader — the shaderset's own record of every texture it binds.

Pure stdlib (the whole `le_mesh` core must import inside Blender).

## Why this exists

Until now the only texture-binding source was the `SShaderInputData` array in a
`CGShaderSetResourceWin7` slice, found by scanning for a `textureassetid` that is
a known `CGTextureResourceWin7` name hash
(`le_shaderset_scan.scan_shaderset_slice`). That has two
failure modes, and the second one is fatal:

1. the needle set has to be corpus-complete or bindings are silently lost
   (docs/MATERIALS.md);
2. **some shadersets ship no `SShaderInputData` array at all.** Measured on
   `2fd6839161785e9c` (aln_liv): 4 of 17 shadersets — including the two carrying
   Liv's largest meshes, 13,168 v and 14,270 v — contain ZERO 8-aligned u64
   anywhere in 23-73 KB that matches any of 24,852 known CSymbol64 hashes. Not a
   predicate failure and not per-archive damage: the same assets are byte-identical
   in `6a993ea8dd6c3dfd`, and their `CGShaderSetResourceWin7GPU` sibling is a
   32-byte zero stub. `stream-confirmed`

But the compiled shader itself knows. A DX11 `DXBC` container carries an `RDEF`
(resource definition) chunk naming every constant buffer, SRV and sampler it
declares, and RAD's cook **rewrites each material sampler's name to the name of
the texture it bound**. So:

    ★ THE LAW: for a material bind, `RDEF resource name` minus the `_decl`
      suffix is the exact CSymbol64 preimage of that bind's `textureassetid`.

Measured over every shaderset in `2fd6839161785e9c` that ships an array:
**39 binds verified, 0 mismatched** — `symbol64(rdef_name[:-5]) == textureassetid`
for every one. `stream-confirmed` / `export-validated`

RDEF is therefore a strict superset of the array: it covers the shadersets that
ship no array, it needs no needle set, and it yields **exact asset names** for
free (`liv_evasuit_pack_a_detail_msk`, `liv_helmet_glass_nml`, ...) — names the
24,852-entry dictionary did not have.

## What RDEF does NOT give you

The **role** (`layer0_composite_diffuse`). The cook overwrote the HLSL sampler
name — which was the role — with the texture name, so the role survives only in
the `SShaderInputData` array. ⛔ Bind ORDER is not a substitute: measured across
the five arrays in `2fd6839161785e9c`, five different orderings appear, and two
1-layer/5-bind materials (`4122c9201430fb6f`, `99ba8b4b0e3613cb`) disagree. Role
recovery for an array-less shaderset is handled by the caller (per-texture
propagation from shadersets that DO ship an array); anything unresolved must land
in `unrouted_roles`, never guessed into a Principled channel.
"""

from __future__ import annotations

import struct
from typing import NamedTuple

#: `D3D_SHADER_INPUT_TYPE` values that denote a shader-resource view we care
#: about. 2 = D3D_SIT_TEXTURE; 5/7 are the structured/byte-address buffer SRVs
#: the engine binds its bone cache and light clusters through.
SRV_TYPES = frozenset((2, 5, 7))

#: The cook suffixes every rewritten resource name. Stripped to get the preimage.
DECL_SUFFIX = "_decl"

#: Engine-supplied inputs are `k_`-prefixed (`k_irradiance_0`, `k_shadow_map`,
#: `k_bone_cache_prev`, ...). They are bound by the renderer, not by the material,
#: and carry `textureassetid == -1` in the array.
ENGINE_PREFIX = "k_"


class BoundResource(NamedTuple):
    """One `RDEF` bound-resource record."""

    dxbc_index: int          # which DXBC blob in the slice (0 = vertex, 1 = pixel)
    bind: int                # register slot (t#)
    name: str                # as stored, including the `_decl` suffix
    input_type: int          # D3D_SHADER_INPUT_TYPE

    @property
    def preimage(self) -> str:
        """The resource name with the cook's `_decl` suffix removed."""
        return self.name[:-len(DECL_SUFFIX)] if self.name.endswith(DECL_SUFFIX) else self.name

    @property
    def is_engine_input(self) -> bool:
        return self.preimage.startswith(ENGINE_PREFIX)


def iter_dxbc_offsets(slab: bytes) -> list[int]:
    """Offsets of every `DXBC` container in a shaderset slice, in order.

    A shaderset carries one container per stage; the material samplers live in
    the pixel shader, which is the later/larger blob.
    """
    out: list[int] = []
    pos = slab.find(b"DXBC")
    while pos >= 0:
        # A container declares its own total size at +0x18 and chunk count at
        # +0x1c; require both to be sane so an incidental "DXBC" in bytecode or
        # in a string table cannot be mistaken for a header.
        if pos + 0x20 <= len(slab):
            total, nchunk = struct.unpack_from("<II", slab, pos + 0x18)
            if 0 < nchunk <= 32 and 0 < total <= len(slab) - pos:
                out.append(pos)
        pos = slab.find(b"DXBC", pos + 4)
    return out


def bound_resources(slab: bytes) -> list[BoundResource]:
    """Every SRV bound-resource record of every DXBC container in the slice."""
    out: list[BoundResource] = []
    for di, base in enumerate(iter_dxbc_offsets(slab)):
        nchunk = struct.unpack_from("<I", slab, base + 0x1c)[0]
        for c in range(nchunk):
            coff = struct.unpack_from("<I", slab, base + 0x20 + c * 4)[0]
            if slab[base + coff: base + coff + 4] != b"RDEF":
                continue
            csz = struct.unpack_from("<I", slab, base + coff + 4)[0]
            body = slab[base + coff + 8: base + coff + 8 + csz]
            if len(body) < 16:
                continue
            _cb_count, _cb_off, rb_count, rb_off = struct.unpack_from("<IIII", body, 0)
            for r in range(rb_count):
                rec = rb_off + r * 32
                if rec + 32 > len(body):
                    break
                name_off, input_type, _ret, _dim, _cnt, bind, _bcount, _flags = \
                    struct.unpack_from("<IIIIIIII", body, rec)
                if input_type not in SRV_TYPES or name_off >= len(body):
                    continue
                end = body.find(b"\x00", name_off)
                if end < 0:
                    continue
                out.append(BoundResource(
                    dxbc_index=di, bind=bind, input_type=input_type,
                    name=body[name_off:end].decode("ascii", "replace")))
    return out


def material_texture_binds(slab: bytes) -> dict[int, str]:
    """`{bind -> texture name preimage}` for the MATERIAL-supplied textures only.

    Engine `k_*` inputs are dropped: the renderer binds those, they are never a
    material's business, and they carry `textureassetid == -1` in the array.

    When two DXBC stages declare the same register, the later (pixel) stage wins —
    that is the stage the material samplers live in.
    """
    out: dict[int, str] = {}
    for res in bound_resources(slab):
        if res.is_engine_input:
            continue
        out[res.bind] = res.preimage
    return out
