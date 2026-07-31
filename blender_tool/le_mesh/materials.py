"""Material resolution: shaderset/material -> texture roles -> Principled BSDF spec.

Pure stdlib. Produces the `materials` list embedded in a `.lemesh` manifest; the
Blender addon's material_builder wires these onto a Principled BSDF.

Resolution chain:
  CGRenderParams.shadersetidx -> scene shaderset table -> CGShaderSetResource
    -> SShaderInputData rows {inputname(CSymbol64), textureassetid(CSymbol64), ...}
    -> CGTextureResource -> DDS  (DXGI format decides colorspace)
  CGRenderParams.materialidx -> CGSceneData.materials -> SGMaterialData
    -> scalar params: bakecolor, bakeemissivecolor, blendmode, EFlags(eDoubleSided),
       materialprops(k_alpha, layerN_emissive_intensity, uv offsets)

For the prototype the shaderset->texture join is read from the proven precomputed
scan TSVs; a direct-from-archive
resolver is a later hardening step. The role/colorspace/Principled tables below
are durable format knowledge and independent of that source.
"""

from __future__ import annotations

import csv
from pathlib import Path


# --- inputname CSymbol64 hash -> role key -----------------------------------
# confidence: "confirmed" (preimage cracked) vs "tentative" (DXGI-format inferred)
INPUTNAME_ROLE: dict[str, tuple[str, str]] = {
    # confirmed
    "2249a2ab88ae66f0": ("layer0_specular_map", "confirmed"),
    "e61f1a40b0f64878": ("layer0_normal_map", "confirmed"),
    "a0790a952a361b16": ("layer0_opacity_map", "confirmed"),
    "6dd500693d77b342": ("layer0_albedo_map", "confirmed"),
    "36edc221250ba1a0": ("layer0_emissive_map", "confirmed"),
    "dcfcc0a30933479e": ("layer1_emissive_map", "confirmed"),
    "b188cecfb9c75902": ("layer2_emissive_map", "confirmed"),
    "63942a40279db62a": ("layer1_opacity_map", "confirmed"),
    "f340cfaa0e533ab5": ("layer1_blend_mask", "confirmed"),
    "18405b9104db1997": ("layer2_blend_mask", "confirmed"),
    "bebfd787fd5cf889": ("layer3_blend_mask", "confirmed"),
    "174d6978fb021e30": ("layer0_flowmap_map", "confirmed"),
    "d4a049adf6a9b30c": ("layer1_flowmap_map", "confirmed"),
    # ⛔ These ten were labelled "tentative (DDS-format inferred)" before 0.2.0 and
    # were INVENTED names — none of them hashed to its own key. Every one below is
    # now the exact recovered preimage: `material_scalars.symbol64(name)` reproduces
    # the key it is filed under (locked by tests/test_transparency.py).
    # Two of the fakes were wrong about MEANING, not just spelling:
    #   * there is NO glass-specific role — "layer1_glass_*" is just layer 1
    #   * "layer1_mask_b" (wired to Roughness) is really layer1_alpha_map = OPACITY
    # See docs/MATERIALS.md.
    "e342db88d8e9d701": ("layer0_composite_normals", "confirmed"),
    "96ac91cb13fe5be7": ("layer1_composite_normals", "confirmed"),
    "33d1823268b0a40c": ("layer0_composite_specular", "confirmed"),
    "e348dd9cd3fdc817": ("layer0_composite_diffuse", "confirmed"),
    "96a697df18ea44f1": ("layer1_composite_diffuse", "confirmed"),
    "5359456ffb9a1dae": ("layer1_composite_specular", "confirmed"),
    "39d68102257d6d24": ("layer0_back_lighting_map", "confirmed"),
    "228838c1c7770d21": ("layer1_composite_components", "confirmed"),
    "d000069cc9204803": ("layer0_composite_components", "confirmed"),
    "8ed4ab4792aaf806": ("layer1_alpha_map", "confirmed"),
}

# --- Principled channel priorities (first present wins) ----------------------
# ⚠ Re-derived after the fabricated-name correction above. The old lists routed by
# the invented names and so mis-assigned three channels: the two `*_composite_specular`
# maps were treated as BASE COLOUR (they are specular/roughness data),
# `layer1_alpha_map` was treated as ROUGHNESS (it is opacity), and
# `layer0_back_lighting_map` was treated as EMISSION (it is translucency).
BASE_COLOR_ROLES = [
    "layer0_specular_map", "layer0_albedo_map",
    "layer0_composite_diffuse", "layer1_composite_diffuse",
]
NORMAL_ROLES = ["layer0_normal_map", "layer0_composite_normals",
                "layer1_composite_normals"]
ROUGHNESS_ROLES = ["layer0_composite_components", "layer1_composite_components",
                   "layer0_composite_specular", "layer1_composite_specular"]
OPACITY_ROLES = ["layer0_opacity_map", "layer1_opacity_map", "layer1_alpha_map"]
EMISSION_ROLES = ["layer0_emissive_map", "layer1_emissive_map", "layer2_emissive_map"]
# Translucency/back-lighting, NOT emission — kept out of EMISSION_ROLES on purpose.
# No Principled channel is a faithful target; carried for audit only.
TRANSLUCENCY_ROLES = ["layer0_back_lighting_map"]

# --- DXGI format -> colorspace ----------------------------------------------
# Standard DXGI enum values. sRGB set is authoritative for base color / emission;
# normals & masks are ALWAYS linear regardless of format.
SRGB_DXGI = frozenset({72, 78, 99})   # BC1_SRGB, BC3_SRGB, BC7_SRGB
# BC5 (83) is two-channel XY normal -> reconstruct Z.
BC5_DXGI = frozenset({83})


def colorspace_for(dxgi: int | None, role_key: str) -> str:
    """Blender Image colorspace: 'sRGB' or 'Non-Color'."""
    if role_key in NORMAL_ROLES or "mask" in role_key or "opacity" in role_key \
            or "alpha" in role_key or "components" in role_key \
            or "specular" in role_key or "flowmap" in role_key:
        return "Non-Color"
    if dxgi is not None and dxgi in SRGB_DXGI:
        return "sRGB"
    # Colour-ish roles default to sRGB even if the format code is unknown.
    if role_key in BASE_COLOR_ROLES or role_key in EMISSION_ROLES:
        return "sRGB"
    return "Non-Color"


def _first_present(roles: list[str], role_textures: dict[str, str]) -> str | None:
    for r in roles:
        if role_textures.get(r):
            return r
    return None


def _channel(role_key: str, tex_hash: str, dxgi_by_tex: dict[str, int]) -> dict:
    dxgi = dxgi_by_tex.get(tex_hash)
    conf = INPUTNAME_ROLE_CONF.get(role_key, "tentative")
    return {
        "texture": tex_hash,
        "role_key": role_key,
        "dxgi": dxgi,
        "colorspace": colorspace_for(dxgi, role_key),
        "reconstruct_z": bool(dxgi in BC5_DXGI) or role_key in NORMAL_ROLES,
        "confidence": conf,
    }


# role_key -> confidence (derived from INPUTNAME_ROLE)
INPUTNAME_ROLE_CONF = {v[0]: v[1] for v in INPUTNAME_ROLE.values()}


def classify_roles(role_textures: dict[str, str], dxgi_by_tex: dict[str, int]) -> dict:
    """Map a shaderset's {role_key -> tex_hash} to Principled channels.

    Includes the DXGI fallback for unknown `unknown_s{slot}` roles: BC5 -> normal,
    BC1/BC3/BC4 -> base color.
    """
    channels: dict[str, dict] = {}

    bc = _first_present(BASE_COLOR_ROLES, role_textures)
    if bc:
        channels["base_color"] = _channel(bc, role_textures[bc], dxgi_by_tex)
    nm = _first_present(NORMAL_ROLES, role_textures)
    if nm:
        channels["normal"] = _channel(nm, role_textures[nm], dxgi_by_tex)
    rg = _first_present(ROUGHNESS_ROLES, role_textures)
    if rg:
        channels["roughness"] = _channel(rg, role_textures[rg], dxgi_by_tex)
    op = _first_present(OPACITY_ROLES, role_textures)
    if op:
        channels["opacity"] = _channel(op, role_textures[op], dxgi_by_tex)
    em = _first_present(EMISSION_ROLES, role_textures)
    if em:
        channels["emission"] = _channel(em, role_textures[em], dxgi_by_tex)

    # DXGI fallback for any still-unassigned unknown_s{slot} textures.
    assigned = {c["texture"] for c in channels.values()}
    for role_key, tex in role_textures.items():
        if not role_key.startswith("unknown_s") or tex in assigned:
            continue
        dxgi = dxgi_by_tex.get(tex, 0)
        if dxgi in BC5_DXGI and "normal" not in channels:
            channels["normal"] = _channel("layer0_normal_map", tex, dxgi_by_tex)
        elif "base_color" not in channels:
            channels["base_color"] = _channel("layer0_diffuse_map", tex, dxgi_by_tex)
        assigned.add(tex)
    return channels


def build_material_spec(key: str, *, shaderset_hash: str = "", material_hash: str = "",
                        role_textures: dict[str, str] | None = None,
                        dxgi_by_tex: dict[str, int] | None = None,
                        scalars: dict | None = None,
                        texture_files: dict[str, str] | None = None) -> dict:
    """Produce one `materials[]` entry for the manifest.

    `scalars` may carry: base_color_factor[4], emissive_color[3],
    emissive_intensity, alpha, blend_mode(int), double_sided(bool) plus optional
    audit extras (mattype, flags, flag_names, materialfx, is_emissive,
    named_scalars) — see le_mesh.material_scalars.decode_material_scalars.
    `texture_files` maps tex_hash -> package-relative file path (e.g. textures/<hash>.dds).
    """
    role_textures = role_textures or {}
    dxgi_by_tex = dxgi_by_tex or {}
    scalars = scalars or {}
    texture_files = texture_files or {}

    channels = classify_roles(role_textures, dxgi_by_tex)
    for ch in channels.values():
        ch["file"] = texture_files.get(ch["texture"], "")

    spec = {
        "key": key,
        "shaderset_hash": shaderset_hash,
        "material_hash": material_hash,
        "double_sided": bool(scalars.get("double_sided", False)),
        "blend_mode": int(scalars.get("blend_mode", 0)),
        "base_color_factor": list(scalars.get("base_color_factor", [1.0, 1.0, 1.0, 1.0])),
        "emissive_color": list(scalars.get("emissive_color", [0.0, 0.0, 0.0])),
        "emissive_intensity": float(scalars.get("emissive_intensity", 1.0)),
        "alpha": float(scalars.get("alpha", 1.0)),
        "channels": channels,
        "role_textures": role_textures,   # keep raw for audit
    }
    # carry through the material-scalar audit extras when present
    for extra in ("mattype", "flags", "flag_names", "materialfx", "is_emissive",
                  "named_scalars"):
        if extra in scalars:
            spec[extra] = scalars[extra]
    return spec


# --- proven-TSV resolver ----------------------------------------------------

def load_binding_table(path: Path) -> dict[str, list[str]]:
    """meshlist_hash -> ordered [shaderset_hash] (only parse_ok rows)."""
    table: dict[str, list[str]] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row.get("parse_ok") != "True":
                continue
            ml = row["meshlist_hash"].lower()
            shd = [h.strip().lower() for h in (row.get("shaderset_hashes") or "").split(";") if h.strip()]
            table[ml] = shd
    return table


def load_shaderset_textures(scan_path: Path, names: dict[int, str]
                            ) -> dict[str, dict[str, str]]:
    """shaderset_hash -> {role_key -> tex_hash}, role from cracked inputname or unknown_s{slot}."""
    table: dict[str, dict[str, str]] = {}
    with Path(scan_path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            shd = row["shaderset_hash"].lower()
            ihex = row["inputname_hash"].lower().zfill(16)
            role, _conf = INPUTNAME_ROLE.get(ihex, (None, None))
            if role is None:
                try:
                    ih = int(ihex, 16)
                    role = names.get(ih)
                except ValueError:
                    role = None
            if role is None:
                role = f"unknown_s{row.get('slot', 'x')}"
            table.setdefault(shd, {})[role] = row["textureassetid_hash"].lower()
    return table


def load_dxgi_by_tex(*manifest_paths: Path) -> dict[str, int]:
    """tex_hash -> DXGI format int, from one or more texture-manifest TSVs."""
    out: dict[str, int] = {}
    for mp in manifest_paths:
        mp = Path(mp)
        if not mp.exists():
            continue
        with mp.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                th = (row.get("textureassetid") or row.get("tex_hash") or "").lower()
                fmt = row.get("dxgi_format", "")
                if th and fmt.isdigit():
                    out[th] = int(fmt)
    return out


def load_binding_full(path: Path) -> dict[str, dict[str, list[str]]]:
    """meshlist_hash -> {"materials": [hash], "shadersets": [hash]} (parse_ok rows).

    Both lists are index-ordered so a draw's materialidx / shadersetidx select
    directly into them (scene-binding schema).
    """
    table: dict[str, dict[str, list[str]]] = {}
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row.get("parse_ok") != "True":
                continue
            ml = row["meshlist_hash"].lower()
            table[ml] = {
                "materials": [h.strip().lower()
                              for h in (row.get("material_hashes") or "").split(";") if h.strip()],
                "shadersets": [h.strip().lower()
                               for h in (row.get("shaderset_hashes") or "").split(";") if h.strip()],
            }
    return table


def load_texture_homes(scan_path: Path | None, *manifest_paths: Path) -> dict[str, str]:
    """tex_hash -> home archive hash.

    Merges the shader-set scan TSV (`texture_archive_hash` column, per binding)
    and any texture-manifest TSVs (`source_archive` column). Lets the extractor
    pull a texture out of the archive it actually lives in, even when that is not
    the mesh's own archive (very common for shared character/prop textures).
    """
    out: dict[str, str] = {}
    if scan_path is not None and Path(scan_path).exists():
        with Path(scan_path).open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                th = (row.get("textureassetid_hash") or "").lower()
                home = (row.get("texture_archive_hash") or "").lower()
                if th and home:
                    out.setdefault(th, home)
    for mp in manifest_paths:
        mp = Path(mp)
        if not mp.exists():
            continue
        with mp.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                th = (row.get("tex_hash") or row.get("textureassetid") or "").lower()
                home = (row.get("source_archive") or "").lower()
                if th and home:
                    out.setdefault(th, home)
    return out


def roles_from_input_rows(rows, names: dict[int, str]) -> dict[str, str]:
    """{role_key -> tex_hash} from live SShaderInputData scan rows (direct mode).

    `rows` are le_shaderset_scan.ShaderTexRow objects (fields
    inputname_hash / textureassetid_hash / slot). Same role-cracking order as the
    TSV path: cracked INPUTNAME_ROLE, then hash_lookup name, then unknown_s{slot}.
    """
    table: dict[str, str] = {}
    for r in rows:
        ihex = str(r.inputname_hash).lower().zfill(16)
        role, _conf = INPUTNAME_ROLE.get(ihex, (None, None))
        if role is None:
            try:
                role = names.get(int(ihex, 16))
            except ValueError:
                role = None
        if role is None:
            role = f"unknown_s{getattr(r, 'slot', 'x')}"
        table[role] = str(r.textureassetid_hash).lower()
    return table
