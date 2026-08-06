"""Read the REAL Blender node graph back and diff it against the manifest spec.

    "$BLENDER" --background --factory-startup \
        --python <ABS WINDOWS PATH>\\blender_character_material_audit.py -- \
        pkg=<ABS WINDOWS .lemesh>  [out=<ABS WINDOWS .json>] [strict=1] [opts...]

★ WHY THIS EXISTS. A manifest that says `role = layer0_base_color` proves nothing
about Blender: the routing table, the image loader, the socket names and the
blend-method mapping all sit between the manifest and the picture, and every one
of them has been wrong at least once in this tree. This harness reads the graph
that was actually built — every `ShaderNodeTexImage`'s file, `colorspace_settings`
and `alpha_mode`, and the real link path from each Principled socket back to its
terminal image node — and then asserts the manifest's OWN claims against it.

What it checks, and where each rule comes from:

  R1  every `channels[*]` texture is loaded, from the file the manifest names
  R2  `image.colorspace_settings.name` == `channels[*].colorspace`
  R3  `image.alpha_mode` == `channels[*].alpha_mode`
  R4  DXGI `_SRGB` => sRGB, everything else => Non-Color
      (`le_mesh.materials.SRGB_DXGI`, `colorspace_for`)
  R5  a normal map is Non-Color WHATEVER its format
  R6  BC5 (82..84) => the Z-reconstruction chain is in the graph
      (`material_builder._normal_chain`: Separate Color -> POWER x2 -> ADD ->
       SUBTRACT -> SQRT -> MULTIPLY_ADD -> Combine Color)
  R7  each channel reaches the Principled socket the builder promises
  R8  `surface_render_method` agrees with `render_mode_for(mattype, blend_mode)`
  R9  `use_backface_culling == not double_sided`
  R10 roughness is NOT squared: no POWER/MULTIPLY node between the components
      texture and the Roughness socket other than the documented lobe mix
  R11 every DDS in `<pkg>/textures/` either reaches an image node or is
      accounted for as an `unrouted_roles` bind

Output is JSON (machine-readable, one record per material) plus a human table on
stdout. `strict=1` makes any R-rule violation a non-zero exit.
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

import bpy                                                    # type: ignore

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import lone_echo_import                                       # noqa: E402
from lone_echo_import import material_builder as mb           # noqa: E402
from le_mesh import materials as lemat                        # noqa: E402

# channel name -> the Principled socket the builder promises to drive.
# `None` means "no Principled socket by design" — the reason is in the table.
CHANNEL_SOCKET = {
    "base_color": "Base Color",
    "roughness": "Roughness",
    "normal": "Normal",
    "specular": "Specular Tint",
    "alpha": "Alpha",
    "emission": "Emission Color",
    "skin_thickness": "Subsurface Weight",
    # `opacity_map` is a dual-source ADD of a tinted background, wired as
    # Add Shader(surface, Transparent BSDF) — never a Principled socket.
    "transmission": None,
    "opacity": None,                 # deprecated mirror of `transmission`
    "blend_mask": None,              # the per-layer compositing weight
    "translucency": None,            # `-dot(N,L)` wrap lobe, deliberately unrouted
    "flowmap": None,
    "secondary_emission": None,
}

PRINCIPLED_SOCKETS = [
    "Base Color", "Metallic", "Roughness", "IOR", "Alpha", "Normal",
    "Specular IOR Level", "Specular Tint", "Emission Color", "Emission Strength",
    "Transmission Weight", "Subsurface Weight", "Subsurface Radius",
    "Coat Weight", "Sheen Weight", "Anisotropic",
]

# `material_builder._normal_chain` emits exactly these ops between the BC5
# texture and the Normal Map node.
Z_RECONSTRUCT_OPS = {"POWER", "ADD", "SUBTRACT", "SQRT", "MULTIPLY_ADD"}


# ---------------------------------------------------------------------------
# graph walking
# ---------------------------------------------------------------------------

def image_record(img) -> dict:
    if img is None:
        return {"file": None}
    return {
        "file": Path(img.filepath).name,
        "colorspace": img.colorspace_settings.name,
        "alpha_mode": img.alpha_mode,
        "size": [int(img.size[0]), int(img.size[1])],
        "write_failed": img.get("le_alpha_mode_write_failed", None),
    }


def _node_id(n) -> str:
    return f"{n.label or n.name}[{n.type}]"


def trace(sock, depth=0, seen=None):
    """Every terminal Image Texture upstream of `sock`, with the node path taken.

    Returns `{"const": value}` when nothing is linked, else
    `{"path": [...], "images": [{"file":..., "out": "Color"|"Alpha", "via": [...]}]}`.
    """
    seen = seen if seen is not None else set()
    if not sock.links:
        try:
            v = sock.default_value
            try:
                return {"const": [round(float(x), 6) for x in v]}
            except TypeError:
                return {"const": round(float(v), 6)}
        except Exception:
            return {"const": None}
    out = {"images": [], "path": []}
    stack = [(sock.links[0].from_node, sock.links[0].from_socket, [])]
    while stack:
        node, from_sock, via = stack.pop()
        if (node.as_pointer(), from_sock.name) in seen or len(via) > 12:
            continue
        seen.add((node.as_pointer(), from_sock.name))
        step = _node_id(node) + (f".{node.operation}" if node.type == "MATH" else "")
        out["path"].append(".".join(via + [step]) + f" -> {from_sock.name}")
        if node.type == "TEX_IMAGE":
            rec = image_record(node.image)
            rec["out"] = from_sock.name
            rec["via"] = list(via)
            rec["label"] = node.label or node.name
            out["images"].append(rec)
            continue
        for inp in node.inputs:
            for lk in inp.links:
                stack.append((lk.from_node, lk.from_socket, via + [step]))
    return out


def upstream_ops(sock, stop_at_image=True) -> list:
    """Every MATH/MIX operation between `sock` and the images that feed it."""
    ops, stack, seen = [], [], set()
    for lk in sock.links:
        stack.append(lk.from_node)
    while stack:
        n = stack.pop()
        if n.as_pointer() in seen:
            continue
        seen.add(n.as_pointer())
        if n.type == "TEX_IMAGE":
            if stop_at_image:
                continue
        if n.type == "MATH":
            ops.append(f"MATH.{n.operation}:{n.label or ''}")
        elif n.type == "MIX":
            ops.append(f"MIX.{n.data_type}.{n.blend_type}:{n.label or ''}")
        elif n.type in ("SEPARATE_COLOR", "COMBINE_COLOR", "NORMAL_MAP"):
            ops.append(f"{n.type}:{n.label or ''}")
        for inp in n.inputs:
            for lk in inp.links:
                stack.append(lk.from_node)
    return ops


def dds_dxgi(path: Path):
    """DXGI format int from a DX10 DDS header, else None."""
    try:
        head = path.open("rb").read(148)
    except Exception:
        return None
    if len(head) < 148 or head[:4] != b"DDS ":
        return None
    if head[84:88] != b"DX10":
        return None
    return struct.unpack_from("<I", head, 128)[0]


# ---------------------------------------------------------------------------
# per-material audit
# ---------------------------------------------------------------------------

def audit_material(mat, spec: dict, pkg_dir: Path) -> dict:
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    outn = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
    channels = (spec or {}).get("channels", {}) or {}

    rec = {
        "key": mat.name,
        "shaderset_hash": (spec or {}).get("shaderset_hash", ""),
        "material_hash": (spec or {}).get("material_hash", ""),
        "mattype": (spec or {}).get("mattype"),
        "mattype_name": (spec or {}).get("mattype_name", ""),
        "blend_mode": (spec or {}).get("blend_mode"),
        "blend_mode_name": (spec or {}).get("blend_mode_name", ""),
        "manifest_render_mode": (spec or {}).get("render_mode", ""),
        "double_sided": bool((spec or {}).get("double_sided", False)),
        "alpha_source": (spec or {}).get("alpha_source", ""),
        "alpha_terms": (spec or {}).get("alpha_terms", []),
        "unrouted_roles": list((spec or {}).get("unrouted_roles") or []),
        "role_sources": dict((spec or {}).get("role_sources") or {}),
        "role_textures": dict((spec or {}).get("role_textures") or {}),
        "brdf_lobes": bool((spec or {}).get("brdf_lobes")),
        "composite_path": bool((spec or {}).get("composite_path")),
        "specular_f0_when_absent": (spec or {}).get("specular_f0_when_absent"),
        "le_props": {k: (list(mat[k]) if hasattr(mat[k], "__len__")
                         and not isinstance(mat[k], str) else mat[k])
                     for k in mat.keys() if str(k).startswith("le_")},
        "surface_render_method": getattr(mat, "surface_render_method", None),
        "blend_method": getattr(mat, "blend_method", None),
        "use_backface_culling": bool(getattr(mat, "use_backface_culling", False)),
        "use_transparent_shadow": bool(getattr(mat, "use_transparent_shadow", True)),
        "images": [], "sockets": {}, "channels": {}, "violations": [],
    }

    for n in nt.nodes:
        if n.type == "TEX_IMAGE":
            r = image_record(n.image)
            r["label"] = n.label or n.name
            r["linked"] = bool(any(o.links for o in n.outputs))
            rec["images"].append(r)

    if bsdf is not None:
        for s in PRINCIPLED_SOCKETS:
            if s in bsdf.inputs:
                rec["sockets"][s] = trace(bsdf.inputs[s])
    if outn is not None:
        rec["surface"] = trace(outn.inputs["Surface"])
    rec["extra_shader_nodes"] = sorted(
        {n.type for n in nt.nodes
         if n.type in ("ADD_SHADER", "MIX_SHADER", "BSDF_TRANSPARENT", "EMISSION")})

    files_in_graph = {i["file"] for i in rec["images"] if i.get("file")}

    def bad(rule, msg):
        rec["violations"].append({"rule": rule, "message": msg})

    # --- R1..R7 per manifest channel ---------------------------------------
    for name, ch in sorted(channels.items()):
        want_file = Path(str(ch.get("file") or "")).name
        node = next((i for i in rec["images"] if i.get("file") == want_file), None)
        socket = CHANNEL_SOCKET.get(name, "?")
        entry = {
            "role_key": ch.get("role_key", ""),
            "texture": ch.get("texture", ""),
            "file": want_file,
            "manifest_colorspace": ch.get("colorspace"),
            "manifest_alpha_mode": ch.get("alpha_mode"),
            "manifest_dxgi": ch.get("dxgi"),
            "component": ch.get("component"),
            "reconstruct_z": bool(ch.get("reconstruct_z")),
            "expected_socket": socket,
            "in_graph": node is not None,
            "graph_colorspace": node.get("colorspace") if node else None,
            "graph_alpha_mode": node.get("alpha_mode") if node else None,
            "reaches_socket": None,
            # `audit_only` from the manifest OR from the routing table itself:
            # a channel with no Principled socket by design is audit-only even
            # when the manifest predates `materials.AUDIT_ONLY_SUFFIXES`.
            "audit_only": bool(ch.get("audit_only")) or socket is None,
            "blend_layer": ch.get("blend_layer"),
        }
        on_disk = (pkg_dir / str(ch.get("file") or "")).exists() if ch.get("file") else False
        entry["file_on_disk"] = on_disk
        # ⚠ A channel whose LAYER is parked at its animated OFF extreme
        # (`layerN_blend_mask_offset == -1` => `saturate(mask*scale + offset) == 0`
        # for every texel) is deliberately not wired, and `material_builder`
        # stamps the reason. That is `suppressed_at_rest`, not a dropped channel,
        # and must not read as a defect.
        suppressed = bool(mat.get(f"le_layer_blend_{name}_suppressed"))
        if name == "specular" and mat.get("le_specular_unwired"):
            suppressed = True
        entry["suppressed_at_rest"] = suppressed
        if suppressed:
            entry["suppressed_reason"] = (
                str(mat.get("le_specular_unwired") or "")
                or f"layer {mat.get('le_layer_blend_' + name)} blend amount is 0 "
                   f"(mask_offset {mat.get('le_layer_blend_mask_offset')})")
        if node is None:
            if on_disk and not entry["audit_only"] and socket is not None \
                    and not suppressed:
                bad("R1", f"channel {name}: {want_file} is on disk but reaches no "
                          f"image node")
        else:
            if entry["graph_colorspace"] != ch.get("colorspace"):
                bad("R2", f"channel {name}: colorspace {entry['graph_colorspace']!r} "
                          f"!= manifest {ch.get('colorspace')!r}")
            if entry["graph_alpha_mode"] != ch.get("alpha_mode"):
                bad("R3", f"channel {name}: alpha_mode {entry['graph_alpha_mode']!r} "
                          f"!= manifest {ch.get('alpha_mode')!r}")
            dxgi = ch.get("dxgi")
            if isinstance(dxgi, int):
                want_cs = "sRGB" if dxgi in lemat.SRGB_DXGI else "Non-Color"
                if name == "normal" or "composite_normals" in str(ch.get("role_key")) \
                        or "normal_map" in str(ch.get("role_key")):
                    want_cs = "Non-Color"
                    if entry["graph_colorspace"] != "Non-Color":
                        bad("R5", f"channel {name}: a NORMAL map is "
                                  f"{entry['graph_colorspace']!r}, must be Non-Color")
                elif entry["graph_colorspace"] != want_cs:
                    bad("R4", f"channel {name}: dxgi {dxgi} implies {want_cs}, "
                              f"graph has {entry['graph_colorspace']!r}")
                if dxgi in lemat.BC5_DXGI and name == "normal":
                    ops = set()
                    if bsdf is not None and "Normal" in bsdf.inputs:
                        ops = {o.split(":")[0].replace("MATH.", "")
                               for o in upstream_ops(bsdf.inputs["Normal"])}
                    if not (Z_RECONSTRUCT_OPS & ops):
                        bad("R6", f"channel {name}: BC5 ({dxgi}) but no Z-reconstruct "
                                  f"chain upstream of Normal (ops={sorted(ops)})")
        if socket and bsdf is not None and socket in bsdf.inputs:
            got = rec["sockets"].get(socket) or {}
            files = {i["file"] for i in (got.get("images") or [])}
            entry["reaches_socket"] = want_file in files
            if node is not None and not entry["reaches_socket"] \
                    and not entry["audit_only"] and not entry["suppressed_at_rest"]:
                bad("R7", f"channel {name}: {want_file} is loaded but does not "
                          f"reach {socket!r} (socket = {got.get('const', 'linked')})")
        rec["channels"][name] = entry

    # --- R8 / R9 render state ----------------------------------------------
    if not spec:
        rec["violations"].append({"rule": "R0",
                                  "message": "no manifest spec for this material "
                                             "(built from a fallback)"})
        rec["files_in_graph"] = sorted(files_in_graph)
        return rec
    want_mode, _lossy = lemat.render_mode_for(int(rec["mattype"] or 0),
                                              int(rec["blend_mode"] or 0))
    rec["expected_render_mode"] = want_mode
    got_method = rec["surface_render_method"]
    # `material_builder._set_render_mode` maps CLIP/BLEND -> 'BLENDED' and the
    # cutout is a node op; OPAQUE -> 'DITHERED'.
    want_method = mb.surface_render_method_for(want_mode)
    # a transmission tint or an additive blend force BLEND after the fact
    forced = bool(mat.get("le_transmission_tint") or mat.get("le_additive_blend"))
    if got_method is not None and got_method != want_method and not forced:
        bad("R8", f"surface_render_method {got_method!r} != {want_method!r} "
                  f"(mattype {rec['mattype_name']}, blend {rec['blend_mode_name']})")
    if spec and rec["use_backface_culling"] == rec["double_sided"]:
        bad("R9", f"use_backface_culling {rec['use_backface_culling']} but "
                  f"double_sided {rec['double_sided']}")

    # --- R10 roughness must not be squared ---------------------------------
    if bsdf is not None and "Roughness" in bsdf.inputs:
        ops = upstream_ops(bsdf.inputs["Roughness"])
        rec["roughness_ops"] = ops
        for o in ops:
            if o.startswith("MATH.POWER") or o.startswith("MATH.MULTIPLY:") and "x skin" not in o:
                bad("R10", f"Roughness has a {o} upstream — RAD's sqrtroughness is "
                           f"used RAW docs/MATERIALS.md 6)")

    rec["files_in_graph"] = sorted(files_in_graph)
    return rec


def main() -> int:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts = {}
    for a in argv:
        if "=" in a:
            k, v = a.split("=", 1)
            opts[k.strip()] = v.strip()
    pkg = opts.pop("pkg", "")
    if not pkg:
        raise SystemExit("pkg=<.lemesh> is required")
    out_path = opts.pop("out", "")
    strict = opts.pop("strict", "0") in ("1", "true", "yes")

    print("[addon]", lone_echo_import.__file__, flush=True)
    if "blender_tool" not in lone_echo_import.__file__.replace("\\", "/"):
        raise SystemExit("STALE INSTALLED ADDON -- aborting")

    mopts = {"import_materials": True, "flip_v": True, "y_up_to_z_up": True}
    for k, v in opts.items():
        if v in ("0", "1"):
            mopts[k] = bool(int(v))
        else:
            try:
                mopts[k] = float(v)
            except ValueError:
                mopts[k] = v
    mopts.setdefault("lod_level", 0)
    print("[opts]", mopts, flush=True)

    pkg_dir = Path(pkg)
    manifest = json.loads((pkg_dir / "manifest.json").read_text(encoding="utf-8"))
    specs = {s["key"]: s for s in manifest.get("materials", [])}

    # ⚠ `--factory-startup` ships a Cube with a material called "Material"; left
    # in, it is audited as a real material and reports a spurious R9.
    for ob in list(bpy.data.objects):
        bpy.data.objects.remove(ob, do_unlink=True)
    for m in list(bpy.data.materials):
        bpy.data.materials.remove(m, do_unlink=True)

    res = lone_echo_import.import_lemesh(pkg, bpy.context, mopts)
    objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    print(f"[import] {res['objects']} objects, {res['vertices']} verts, "
          f"{res['triangles']} tris, {res['materials']} materials", flush=True)

    doc = {
        "package": str(pkg_dir),
        "archive": (manifest.get("source") or {}).get("archive", ""),
        "meshlist": (manifest.get("source") or {}).get("meshlist", ""),
        "opts": {k: str(v) for k, v in mopts.items()},
        "import": {k: res[k] for k in ("objects", "vertices", "triangles", "materials")
                   if k in res},
        "manifest_objects": len(manifest.get("objects", [])),
        "manifest_materials": len(specs),
        "objects": [],
        "materials": [],
        "violations": 0,
    }
    for o in sorted(objs, key=lambda x: x.name):
        doc["objects"].append({
            "name": o.name,
            "verts": len(o.data.vertices),
            "tris": len(o.data.loop_triangles) or sum(
                len(p.vertices) - 2 for p in o.data.polygons),
            "materials": [s.material.name if s.material else None
                          for s in o.material_slots],
            "uv_layers": [l.name for l in o.data.uv_layers],
            "color_attrs": [c.name for c in getattr(o.data, "color_attributes", [])],
            "le_lod_level": o.get("le_lod_level"),
            "le_scene_lod_level": o.get("le_scene_lod_level"),
        })

    seen = set()
    for o in sorted(objs, key=lambda x: x.name):
        for slot in o.material_slots:
            m = slot.material
            if m is None or m.name in seen:
                continue
            seen.add(m.name)
            spec = specs.get(m.name) or specs.get(str(m.get("le_shaderset", "")) + "__"
                                                  + str(m.get("le_material", "")))
            rec = audit_material(m, spec or {}, pkg_dir)
            rec["spec_found"] = spec is not None
            doc["materials"].append(rec)
            doc["violations"] += len(rec["violations"])

    # --- R11: every extracted DDS accounted for ----------------------------
    tex_dir = pkg_dir / "textures"
    on_disk = sorted(p.name for p in tex_dir.glob("*")) if tex_dir.is_dir() else []
    in_graph = {f for r in doc["materials"] for f in r["files_in_graph"]}
    # ⛔ Every orphan must NAME ITS CONTAINER: which material declares it, under
    # which role, and why that role never reached a node. "Unexplained" is then a
    # real residue, not a bookkeeping gap.
    built = {r["key"] for r in doc["materials"]}
    suppressed_tex = set()
    for r in doc["materials"]:
        for ch in r["channels"].values():
            if ch.get("suppressed_at_rest") and ch.get("file"):
                suppressed_tex.add(ch["file"])
        for k, v in r["le_props"].items():
            if k.endswith("_mask") and isinstance(v, str) and v:
                suppressed_tex.add(f"{v}.dds")     # a gate mask that never fired
    owner = {}
    for key, s in specs.items():
        rt = s.get("role_textures") or {}
        for role, tex in rt.items():
            if tex:
                owner.setdefault(f"{tex}.dds", []).append(
                    {"material": key, "role": role, "built": key in built,
                     "unrouted": role in (s.get("unrouted_roles") or [])})
    orphans = [f for f in on_disk if f not in in_graph]
    classes = {"unrouted_bind": [], "audit_only_role": [], "material_not_built": [],
               "layer_suppressed_at_rest": [], "unexplained": []}
    detail = {}
    for f in orphans:
        rows = owner.get(f, [])
        detail[f] = rows
        if f in suppressed_tex:
            classes["layer_suppressed_at_rest"].append(f)
        elif any(r["unrouted"] for r in rows):
            classes["unrouted_bind"].append(f)
        elif rows and not any(r["built"] for r in rows):
            classes["material_not_built"].append(f)
        elif any(ch.get("audit_only") or CHANNEL_SOCKET.get(cname, "?") is None
                 for s in specs.values()
                 for cname, ch in (s.get("channels") or {}).items()
                 if Path(str(ch.get("file") or "")).name == f):
            classes["audit_only_role"].append(f)
        else:
            classes["unexplained"].append(f)
    doc["textures"] = {
        "on_disk": len(on_disk),
        "reached_by_a_node": len(in_graph),
        "orphans": orphans,
        "orphan_owner": detail,
        **{f"orphans_{k}": sorted(v) for k, v in classes.items()},
    }

    # --- report ------------------------------------------------------------
    print("=" * 100)
    print(f"{'material':40s} {'mattype':22s} {'render':9s} {'bound':>5s} "
          f"{'const':>5s} {'unrouted':>8s} viol")
    for r in doc["materials"]:
        bound = sum(1 for c in r["channels"].values() if c["in_graph"])
        const = sum(1 for s, v in r["sockets"].items() if "const" in v)
        print(f"{r['key'][:40]:40s} {r['mattype_name'][:22]:22s} "
              f"{str(r['surface_render_method'])[:9]:9s} {bound:5d} {const:5d} "
              f"{len(r['unrouted_roles']):8d} {len(r['violations'])}")
        for name, c in sorted(r["channels"].items()):
            src = r["role_sources"].get(c["role_key"], "?")
            print(f"     {name:18s} {c['role_key'][:30]:30s} src={src:11s} "
                  f"dxgi={str(c['manifest_dxgi']):>4s} cs={str(c['graph_colorspace'])[:9]:9s} "
                  f"a={str(c['graph_alpha_mode'])[:14]:14s} -> "
                  f"{str(c['expected_socket']):18s} reaches={c['reaches_socket']}")
        for role in r["unrouted_roles"]:
            print(f"     {'UNROUTED':18s} {role[:30]:30s} "
                  f"tex={r['role_textures'].get(role, '')}")
        for v in r["violations"]:
            print(f"    !! {v['rule']}  {v['message']}")
    print("-" * 100)
    t = doc["textures"]
    print(f"textures on disk {t['on_disk']}, reached {t['reached_by_a_node']}, "
          f"orphans {len(t['orphans'])} = "
          f"{len(t['orphans_unrouted_bind'])} unrouted bind "
          f"+ {len(t['orphans_audit_only_role'])} audit-only role "
          f"+ {len(t['orphans_material_not_built'])} material not built "
          f"+ {len(t['orphans_layer_suppressed_at_rest'])} layer suppressed "
          f"+ {len(t['orphans_unexplained'])} UNEXPLAINED")
    for f in t["orphans_unexplained"]:
        print(f"    ?? orphan {f}  owner={t['orphan_owner'].get(f)}")
    print(f"TOTAL VIOLATIONS: {doc['violations']}")

    if out_path:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_text(json.dumps(doc, indent=1), encoding="utf-8")
        print(f"[json] {out_path}")
    return 1 if (strict and doc["violations"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
