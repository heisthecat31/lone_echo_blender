"""R1 in-Blender probe: did the SHIPPED tangent basis actually reach the shader?

    blender.exe --background --factory-startup --python blender_tangent_probe.py \
        [-- [pkg=<...>.lemesh] [json=<out.json>]]

Deliberately NOT named `test_*`: `tests/run_tests.py` runs under plain python3 and
must never import `bpy`. The pure half — the opt, the four `.w` states, and the
corpus laws behind them — is `tests/test_shipped_tangent.py`.

⛔ WHY A PROBE AND NOT A UNIT TEST. `material_builder` can be exercised with a
stub `bpy`, but a stub will happily accept `links.new(a, b)` for a link Blender
would refuse. The brief for R1 was explicit — *verify it takes effect rather than
assuming the socket accepted it* — and only Blender can answer that. Three
sections, each printing its own checks:

  1. **SOCKET FACTS.** `ShaderNodeMix`'s VECTOR socket indices and
     `ShaderNodeVectorMath`'s input layout, read back off real nodes instead of
     assumed. `material_builder.MIX_VECTOR_SOCKETS` is asserted against them.
  2. **THE GRAPH, AS BUILT.** Import a real character with `shipped_tangent` on
     and off, and walk the links backwards from the Principled `Normal` input.
     On: it must reach the `le_tangent` Attribute node. Off: it must reach a
     `ShaderNodeNormalMap` and NOT the attribute.
  3. **THE DIVERGENCE, RE-MEASURED AGAINST MIKKTSPACE.** An earlier measurement
     compared the shipped basis against a *naive area-weighted* UV tangent and
     flagged it as an upper bound rather than Blender's error.
     Here `mesh.calc_tangents()` gives Blender's real per-loop mikktspace tangent
     and the comparison is exact. `loop.bitangent_sign` vs `sign(le_tangent_w)`
     additionally settles whether the `flip_v` import inverts the green channel.
"""

import json
import math
import sys
from pathlib import Path

import bpy   # type: ignore

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
for p in (str(BLENDER_TOOL), str(BLENDER_TOOL / "addon"), str(HERE)):
    if p not in sys.path:
        sys.path.insert(0, p)

import lone_echo_import                                    # noqa: E402
from lone_echo_import import material_builder as MB        # noqa: E402

DEFAULT_PKG = (BLENDER_TOOL / "exports" / "chars"
               / "c6bc8607972268c9_64b4b5b2a0153f7e.lemesh")

FAILURES = []
CHECKS = [0]


def check(label, cond, detail=""):
    CHECKS[0] += 1
    if cond:
        print(f"  ok   {label}" + (f"  [{detail}]" if detail else ""))
    else:
        print(f"  FAIL {label}" + (f"  [{detail}]" if detail else ""))
        FAILURES.append(label)
    return bool(cond)


def argv_opts():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    out = {}
    for a in argv:
        if "=" in a:
            k, v = a.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def clear_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)


# ---------------------------------------------------------------------------
# 1. socket facts
# ---------------------------------------------------------------------------

def section_socket_facts():
    print("\n[1] socket facts, read back off real nodes")
    mat = bpy.data.materials.new("le_probe_sockets")
    mat.use_nodes = True
    nt = mat.node_tree
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = "VECTOR"
    names = [(i, s.name, s.type) for i, s in enumerate(mix.inputs)]
    print("      ShaderNodeMix inputs:", ", ".join(f"{i}:{n}({t})"
                                                   for i, n, t in names))
    print("      ShaderNodeMix outputs:", ", ".join(
        f"{i}:{s.name}" for i, s in enumerate(mix.outputs)))
    fi, ai, bi, ri = MB.MIX_VECTOR_SOCKETS
    check("MIX_VECTOR_SOCKETS factor index is a float Factor",
          mix.inputs[fi].type == "VALUE", mix.inputs[fi].name)
    check("MIX_VECTOR_SOCKETS A/B are VECTOR sockets",
          mix.inputs[ai].type == "VECTOR" and mix.inputs[bi].type == "VECTOR",
          f"{mix.inputs[ai].name} / {mix.inputs[bi].name}")
    check("MIX_VECTOR_SOCKETS result is a VECTOR output",
          mix.outputs[ri].type == "VECTOR", mix.outputs[ri].name)

    vm = nt.nodes.new("ShaderNodeVectorMath")
    vm.operation = "SCALE"
    print("      ShaderNodeVectorMath inputs:", ", ".join(
        f"{i}:{s.name}({s.type})" for i, s in enumerate(vm.inputs)))
    check("VECMATH_A is a vector input", vm.inputs[MB.VECMATH_A].type == "VECTOR")
    check("VECMATH_SCALE is the float Scale input",
          vm.inputs[MB.VECMATH_SCALE].type == "VALUE",
          vm.inputs[MB.VECMATH_SCALE].name)
    xf = nt.nodes.new("ShaderNodeVectorTransform")
    check("ShaderNodeVectorTransform can convert OBJECT -> WORLD",
          hasattr(xf, "convert_from") and hasattr(xf, "convert_to"))


# ---------------------------------------------------------------------------
# 2. the graph as built
# ---------------------------------------------------------------------------

def _upstream_nodes(socket, seen=None):
    """Every node reachable backwards from an input socket."""
    seen = seen if seen is not None else set()
    for link in socket.links:
        n = link.from_node
        if n.name in seen:
            continue
        seen.add(n.name)
        for inp in n.inputs:
            _upstream_nodes(inp, seen)
    return seen


def _normal_graph(mat):
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is None:
        return None, set(), []
    ni = bsdf.inputs.get("Normal")
    if ni is None or not ni.links:
        return ni, set(), []
    names = _upstream_nodes(ni)
    kinds = [nt.nodes[n].type for n in names]
    attrs = [nt.nodes[n].attribute_name for n in names
             if nt.nodes[n].type == "ATTRIBUTE"]
    return ni, set(kinds), attrs


def section_graph(pkg: Path):
    print(f"\n[2] the graph as built — {pkg.name}")
    results = {}
    for on in (True, False):
        clear_scene()
        res = lone_echo_import.import_lemesh(str(pkg), bpy.context, {
            "import_materials": True, "lod_level": 0, "flip_v": True,
            "y_up_to_z_up": True, "shipped_tangent": on,
        })
        wired = normal_wired = 0
        kinds_all, attrs_all = set(), set()
        basis = set()
        for m in bpy.data.materials:
            if not m.use_nodes or "le_tangent_basis" not in m.keys():
                continue
            basis.add(m["le_tangent_basis"])
            ni, kinds, attrs = _normal_graph(m)
            if ni is None or not ni.links:
                continue
            normal_wired += 1
            kinds_all |= kinds
            attrs_all |= set(attrs)
            if "le_tangent" in attrs:
                wired += 1
        results["on" if on else "off"] = {
            "materials": res["materials"], "normal_wired": normal_wired,
            "tangent_wired": wired, "basis": sorted(basis),
            "node_kinds": sorted(kinds_all), "attributes": sorted(attrs_all),
        }
        print(f"      shipped_tangent={int(on)}: {res['materials']} material(s), "
              f"{normal_wired} with Normal driven, {wired} reaching le_tangent; "
              f"basis={sorted(basis)}")
        print(f"        upstream node types: {sorted(kinds_all)}")
    on_r, off_r = results["on"], results["off"]
    check("ON: at least one material drives Normal", on_r["normal_wired"] > 0,
          str(on_r["normal_wired"]))
    check("ON: every normal-mapped material reaches the le_tangent attribute",
          on_r["normal_wired"] > 0 and on_r["tangent_wired"] == on_r["normal_wired"],
          f"{on_r['tangent_wired']}/{on_r['normal_wired']}")
    check("ON: the graph carries a VECTOR_TRANSFORM (object -> world)",
          "VECT_TRANSFORM" in on_r["node_kinds"], str(on_r["node_kinds"]))
    check("ON: le_tangent_w is read too",
          "le_tangent_w" in on_r["attributes"], str(on_r["attributes"]))
    check("ON: the mikktspace leg is KEPT as the fallback",
          "NORMAL_MAP" in on_r["node_kinds"])
    check("ON: materials record basis=shipped", on_r["basis"] == ["shipped"],
          str(on_r["basis"]))
    check("OFF: no material reaches le_tangent", off_r["tangent_wired"] == 0,
          f"{off_r['tangent_wired']}/{off_r['normal_wired']}")
    check("OFF: the normal map still drives Normal",
          off_r["normal_wired"] > 0 and "NORMAL_MAP" in off_r["node_kinds"])
    check("OFF: materials record basis=mikktspace", off_r["basis"] == ["mikktspace"],
          str(off_r["basis"]))
    return results


# ---------------------------------------------------------------------------
# 3. the divergence, against Blender's own tangent
# ---------------------------------------------------------------------------

def _pct(vals, q):
    if not vals:
        return float("nan")
    i = min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))
    return vals[i]


def section_divergence(pkg: Path):
    print(f"\n[3] shipped vs mikktspace, per loop — {pkg.name}")
    clear_scene()
    lone_echo_import.import_lemesh(str(pkg), bpy.context, {
        "import_materials": True, "lod_level": 0, "flip_v": True,
        "y_up_to_z_up": True, "shipped_tangent": True,
    })
    rows = []
    for ob in sorted(bpy.data.objects, key=lambda o: o.name):
        if ob.type != "MESH":
            continue
        me = ob.data
        if "le_tangent" not in me.attributes or not me.uv_layers:
            continue
        try:
            me.calc_tangents()
        except Exception as exc:                                # noqa: BLE001
            print(f"      {ob.name}: calc_tangents refused ({exc})")
            continue
        ta = me.attributes["le_tangent"].data
        wa = me.attributes.get("le_tangent_w")
        angles = []
        sign_agree = sign_total = 0
        for lp in me.loops:
            v = lp.vertex_index
            t = ta[v].vector
            lt = lp.tangent
            la = math.sqrt(t[0] ** 2 + t[1] ** 2 + t[2] ** 2)
            lb = math.sqrt(lt[0] ** 2 + lt[1] ** 2 + lt[2] ** 2)
            if la < 1e-6 or lb < 1e-6:
                continue
            d = (t[0] * lt[0] + t[1] * lt[1] + t[2] * lt[2]) / (la * lb)
            angles.append(math.degrees(math.acos(max(-1.0, min(1.0, d)))))
            if wa is not None:
                w = wa.data[v].value
                sign_total += 1
                sign_agree += (lp.bitangent_sign > 0) == (w > 0)
        if not angles:
            continue
        angles.sort()
        row = {
            "object": ob.name, "loops": len(angles),
            "median": _pct(angles, 0.5), "p90": _pct(angles, 0.9),
            "p99": _pct(angles, 0.99), "max": angles[-1],
            "frac_gt_15": sum(1 for a in angles if a > 15.0) / len(angles),
            "bitangent_sign_agree": (sign_agree / sign_total) if sign_total else None,
        }
        rows.append(row)
        bs = row["bitangent_sign_agree"]
        bs_txt = "n/a" if bs is None else f"{100 * bs:.1f} %"
        print(f"      {ob.name}: n={row['loops']:6d} median {row['median']:6.2f}deg "
              f"p90 {row['p90']:6.2f} p99 {row['p99']:6.2f} max {row['max']:6.2f} "
              f">15deg {100 * row['frac_gt_15']:5.1f} %  "
              f"bitangent-sign agree {bs_txt}")
    check("at least one mesh carried le_tangent and a UV layer", bool(rows))
    if rows:
        worst = max(rows, key=lambda r: r["frac_gt_15"])
        check("the two bases genuinely differ on the body",
              worst["frac_gt_15"] > 0.05,
              f"{worst['object']} {100 * worst['frac_gt_15']:.1f} % > 15°")
        sg = [r["bitangent_sign_agree"] for r in rows
              if r["bitangent_sign_agree"] is not None]
        if sg:
            print(f"      bitangent-sign agreement over {len(sg)} mesh(es): "
                  f"min {100 * min(sg):.1f} % max {100 * max(sg):.1f} %")
    return rows


def main():
    opts = argv_opts()
    pkg = Path(opts.get("pkg", str(DEFAULT_PKG)))
    if not (pkg / "manifest.json").exists():
        print(f"PROBE_RESULT: SKIP (no package at {pkg})")
        return 0
    print(f"Blender {bpy.app.version_string}")
    section_socket_facts()
    graph = section_graph(pkg)
    rows = section_divergence(pkg)
    out = opts.get("json")
    if out:
        Path(out).write_text(json.dumps(
            {"blender": bpy.app.version_string, "package": pkg.name,
             "graph": graph, "divergence": rows}, indent=1), encoding="utf-8")
        print(f"\nwrote {out}")
    print(f"\n{CHECKS[0]} check(s), {len(FAILURES)} failure(s)")
    for f in FAILURES:
        print(f"  FAILED: {f}")
    print(f"PROBE_RESULT: {'FAIL' if FAILURES else 'PASS'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
