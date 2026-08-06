"""Specular / F0: the RAD BRDF, its two samplers, and the Blender mapping.

A10. Supersedes the A7 "specular is not representable" verdict, which was
overturned by measurement, not by argument. Everything here is pure Python and
runs without Blender; the parts that need a renderer live in
`tests/blender_material_probe.py` and docs/MATERIALS.md.

Evidence labels: `shader-confirmed` (matches the arithmetic the engine's own
shaders perform) · `stream-confirmed` (decoded shipped archive bytes) ·
`engine-confirmed` (Blender RNA read-back / measured render) · `inferred`.

THE ENGINE SIDE (`shader-confirmed`)
------------------------------------
`layers[i].specalbedo[0]` is the Schlick F0 term::

    Fresnel(specalbedo, v, h, l)
        = specalbedo + (1 - specalbedo) * (1 - saturate(dot(l,h)))^5
        * saturate(dot(specalbedo, 333))                # <0.1% albedo cutoff

    GGX_BRDF(p)
        fresnel = Fresnel(p.specalbedo, v, h, p.l)
        diffuse = p.diffusealbedo * (1 - Fresnel(p.specintensity, v, h, p.l)) / pi
        spec    = GGX_Specular(p.m, p.n, h, v, p.l) * fresnel
        out     = (diffuse + spec) * saturate(dot(n,l)) * lightcolor

    GGX_Specular(m, n, h, v, l)
        m2    = m * m
        d     = m2 / (pi * ((n.h)^2 * (m2 - 1) + 1)^2)
        alpha = (0.5 + m/2)^2                # Burley visibility remap
        vis   = GGX_V1(alpha, n.l) * GGX_V1(alpha, n.v)

    p.m = sqrtroughness * sqrtroughness

Two samplers feed the one `specalbedo` slot, with DIFFERENT scales::

    composite_specular : specalbedo = .xyz * .w ; specintensity = .w
    specular_map       : specalbedo = k_enable_specular * speculartint *
                                      specular_map.xyz * k_fresnel
                         specintensity = k_fresnel

`k_fresnel` is authored 0.010 in the engine's ubermaterial.

THE BLENDER SIDE (`engine-confirmed`, 5.1.1, Cycles and EEVEE Next)
-------------------------------------------------------------------
Principled's dielectric normal-incidence reflectance is

    F0 = F0(IOR) * 2 * `Specular IOR Level` * `Specular Tint`

linear and UNCLAMPED. `Specular IOR Level` is `hard_max = 1.0` but
`Specular Tint` is `hard_max = FLT_MAX` (soft_max 1.0), so the reachable F0 is
NOT capped at 0.08. Measured against a Glossy BSDF whose Colour IS the target
F0, at normal incidence, orthographic camera, unit parallel sun, 32-bit linear
EXR: 0.00% error at every F0 in {0.01, 0.03, 0.04, 0.08, 0.16, 0.345, 0.5, 0.75,
0.85, 1.0} and for IOR in {1.33, 1.5, 2.0}.
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import types
from pathlib import Path

from le_mesh import materials as mat

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
MB_PATH = BLENDER_TOOL / "addon" / "lone_echo_import" / "material_builder.py"
FIXTURE_DIRS = (BLENDER_TOOL / "exports" / "fixtures_mat3",
                BLENDER_TOOL / "exports" / "fixtures_mat")

_MB = None


def _mb():
    """Load material_builder with a stub `bpy` (never imported at module scope)."""
    global _MB
    if _MB is not None:
        return _MB
    if "bpy" not in sys.modules:
        stub = types.ModuleType("bpy")
        stub.data = types.SimpleNamespace(materials=None, images=None)
        stub.context = types.SimpleNamespace(scene=None)
        stub.types = types.SimpleNamespace(Material=object)
        sys.modules["bpy"] = stub
    spec = importlib.util.spec_from_file_location("_le_material_builder_a10", MB_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MB = mod
    return mod


def _fixtures():
    for d in FIXTURE_DIRS:
        if d.is_dir():
            return d
    return None


# ---------------------------------------------------------------------------
# 1. Reference implementation of the RAD direct-light BRDF
# ---------------------------------------------------------------------------

def _sat(x):
    return max(0.0, min(1.0, x))


def rad_fresnel3(f0, ldoth):
    """The engine's `Fresnel` (float3 overload)."""
    p = (1.0 - _sat(ldoth)) ** 5.0
    cut = _sat(sum(f0) * 333.0)
    return [(c + (1.0 - c) * p) * cut for c in f0]


def rad_fresnel1(f0, ldoth):
    """The engine's `Fresnel` (float overload, used for `specintensity`)."""
    p = (1.0 - _sat(ldoth)) ** 5.0
    return (f0 + (1.0 - f0) * p) * _sat(f0 * 1000.0)


def rad_ggx_specular(m, ndoth, ndotl, ndotv):
    """The engine's `GGX_Specular`. `m` is `roughness` == `sqrtroughness ** 2`."""
    def v1(a2, nx):
        return 1.0 / (nx + math.sqrt(a2 + (1.0 - a2) * nx * nx))
    m2 = m * m
    t = ndoth * ndoth * (m2 - 1.0) + 1.0
    d = m2 / (math.pi * t * t)
    alpha = (0.5 + m / 2.0) ** 2
    return d * v1(alpha, ndotl) * v1(alpha, ndotv)


def rad_geometry(phi_deg, theta_deg):
    """n = +Z; view tilted +phi about +Y; light tilted -theta about +Y."""
    p, t = math.radians(phi_deg), math.radians(theta_deg)
    v = (math.sin(p), 0.0, math.cos(p))
    l = (-math.sin(t), 0.0, math.cos(t))
    hx, hz = l[0] + v[0], l[2] + v[2]
    hn = math.hypot(hx, hz) or 1.0
    h = (hx / hn, 0.0, hz / hn)
    return {"ndotl": l[2], "ndotv": v[2], "ndoth": h[2],
            "ldoth": l[0] * h[0] + l[2] * h[2]}


def rad_radiance(dif, spec, sint, sqrtrough, phi=0.0, theta=0.0):
    """Outgoing radiance under a unit white parallel light. The engine's `GGX_BRDF`."""
    g = rad_geometry(phi, theta)
    if g["ndotl"] <= 0.0 or g["ndotv"] <= 0.0:
        return [0.0, 0.0, 0.0]
    m = sqrtrough * sqrtrough
    f = rad_fresnel3(spec, g["ldoth"])
    fi = rad_fresnel1(sint, g["ldoth"])
    s = rad_ggx_specular(m, g["ndoth"], g["ndotl"], g["ndotv"])
    nl = _sat(g["ndotl"])
    return [(dif[i] * (1.0 - fi) / math.pi + s * f[i]) * nl for i in range(3)]


def test_rad_fresnel_is_schlick_with_specalbedo_as_f0():
    # at normal incidence (l.h = 1) Schlick collapses to F0 exactly
    assert abs(rad_fresnel3([0.345] * 3, 1.0)[0] - 0.345) < 1e-9
    assert abs(rad_fresnel3([1.0] * 3, 1.0)[0] - 1.0) < 1e-9
    # and ramps toward 1 as l.h -> 0
    assert abs(rad_fresnel3([0.04] * 3, 0.0)[0] - 1.0) < 1e-9
    # the <0.1%-albedo cutoff kills spec entirely below sum(f0) * 333 < 1
    assert rad_fresnel3([0.0005] * 3, 0.0)[0] < 0.51
    assert rad_fresnel3([0.0] * 3, 0.0)[0] == 0.0


def test_rad_ggx_peak_matches_closed_form():
    """At n=h=l=v the NDF peak is `1 / (pi * m^2)` and the visibility is 1/4."""
    m = 0.30 ** 2
    got = rad_ggx_specular(m, 1.0, 1.0, 1.0)
    want = (1.0 / (math.pi * m * m)) * 0.25
    assert abs(got - want) / want < 1e-12
    assert abs(got - 9.8244) < 1e-3        # the value the Blender probe matched


def test_rad_radiance_reference_values():
    """Regression lock on the reference the Blender measurements were scored
    against (`engine-confirmed` counterparts in docs/MATERIALS.md."""
    # pure Lambert, albedo 1, head-on -> 1/pi
    assert abs(rad_radiance((1, 1, 1), (0, 0, 0), 0.0, 0.3)[0] - 1.0 / math.pi) < 1e-9
    # shipped-p50-like F0 = 0.345
    assert abs(rad_radiance((0.2,) * 3, (0.345,) * 3, 0.35, 0.40)[0] - 1.113811) < 1e-5
    # shipped-p90-like F0 = 0.85
    assert abs(rad_radiance((0.1,) * 3, (0.85,) * 3, 0.85, 0.30)[0] - 8.355497) < 1e-5
    # the specular_map panels: F0 = specular_map(=1) * k_fresnel(=0.01)
    assert abs(rad_radiance((0,) * 3, (0.01,) * 3, 0.01, 0.45)[0] - 0.019406) < 1e-5


def test_current_unwired_state_was_not_neutral():
    """Leaving the channel unwired is not "no specular": Principled's default
    `Specular IOR Level` 0.5 pins F0 at 0.04 for every material.

    Head-on radiance ratios vs the RAD reference (`engine-confirmed` in Cycles,
    reproduced here on the reference side):
      F0 0.345 -> 6.0x too DARK · F0 0.85 -> 19.7x too DARK · F0 0.01 -> 4.0x too BRIGHT
    """
    def head_on(f0, dif):
        return rad_radiance(dif, (f0,) * 3, f0, 0.30)[0]
    blender_default = 0.04
    for f0, direction in ((0.345, "dark"), (0.85, "dark"), (0.01, "bright")):
        want = head_on(f0, (0.0,) * 3)
        got = head_on(blender_default, (0.0,) * 3)
        ratio = want / got
        if direction == "dark":
            assert ratio > 5.0
        else:
            assert ratio < 0.3


# ---------------------------------------------------------------------------
# 2. The Blender mapping (pure decision layer)
# ---------------------------------------------------------------------------

def test_f0_from_ior():
    mb = _mb()
    assert abs(mb.f0_from_ior(1.5) - 0.04) < 1e-9
    assert abs(mb.f0_from_ior(1.0)) < 1e-12
    assert abs(mb.f0_from_ior(2.0) - (1.0 / 3.0) ** 2) < 1e-9
    assert mb.f0_from_ior("nonsense") == 0.04


def test_specular_tint_scale_composite_and_specular_map():
    mb = _mb()
    comp = mat.classify_roles({"layer0_composite_specular": "cs"}, {"cs": 78})["specular"]
    smap = mat.classify_roles({"layer0_specular_map": "sm"}, {"sm": 72})["specular"]
    # composite: `.w` supplies the scale, applied as a node -> constant is 1
    assert mb.specular_scales_by_alpha(comp) is True
    assert mb.specular_albedo_scale({}, comp) == 1.0
    assert abs(mb.specular_tint_scale({}, comp, 1.5) - 25.0) < 1e-9
    # specular_map: `k_fresnel` supplies the scale, authored 0.010
    assert mb.specular_scales_by_alpha(smap) is False
    assert abs(mb.specular_albedo_scale({}, smap) - 0.01) < 1e-12
    assert abs(mb.specular_tint_scale({}, smap, 1.5) - 0.25) < 1e-9
    # a serialised override wins over the authored default
    spec = {"named_scalars_resolved": {"layer0_fresnel": 0.5}}
    assert abs(mb.specular_fresnel_scalar(spec, smap) - 0.5) < 1e-12
    assert abs(mb.specular_tint_scale(spec, smap, 1.5) - 12.5) < 1e-9
    # IOR 1.0 must not divide by zero
    assert mb.specular_tint_scale({}, comp, 1.0) > 0.0


def test_specular_tint_scale_round_trips_to_f0():
    """`Specular Tint = F0 / F0(IOR)` and `F0 = F0(IOR) * tint` must invert, at
    every IOR the builder can actually set."""
    mb = _mb()
    comp = mat.classify_roles({"layer0_composite_specular": "cs"}, {"cs": 78})["specular"]
    for ior in (1.33, 1.45, 1.5, 2.0):
        scale = mb.specular_tint_scale({}, comp, ior)
        for f0 in (0.01, 0.04, 0.345, 0.85, 1.0):
            tint = f0 * scale                      # what the node graph produces
            assert abs(mb.f0_from_ior(ior) * tint - f0) < 1e-9


def test_wire_specular_option_defaults_on():
    mb = _mb()
    assert mb.wire_specular_enabled(None) is True
    assert mb.wire_specular_enabled({}) is True
    assert mb.wire_specular_enabled({"wire_specular": True}) is True
    assert mb.wire_specular_enabled({"wire_specular": False}) is False
    assert mb.SPECULAR_IOR_LEVEL_NEUTRAL == 0.5
    assert mb.SPEC_MAP_FRESNEL_DEFAULT == mat.SPEC_MAP_FRESNEL_DEFAULT == 0.01


def test_specintensity_is_not_double_counted():
    """`.w` has two engine roles and Principled covers both from ONE number.

    `specalbedo = .xyz * .w` (F0) and the diffuse lobe is scaled by
    `(1 - Fresnel(specintensity))` in the engine's `GGX_BRDF`. Principled attenuates
    its diffuse lobe by `(1 - F)` from the same F0, so setting F0 = specalbedo
    reproduces the diffuse term too whenever `.xyz` is white (specalbedo == .w).
    Feeding `.w` to `Specular IOR Level` AS WELL would square it.
    """
    mb = _mb()
    comp = mat.classify_roles({"layer0_composite_specular": "cs"}, {"cs": 78})["specular"]
    assert comp["spec_intensity_channel"] == "A"
    assert comp["spec_albedo_scaled_by"] == "A"
    # the builder puts the whole of F0 in the tint and leaves the level neutral
    src = MB_PATH.read_text(encoding="utf-8")
    assert "sp_level_in.default_value = SPECULAR_IOR_LEVEL_NEUTRAL" in src
    # engine check: with a white .xyz, specalbedo == specintensity
    w = 0.6
    specalbedo = [1.0 * w] * 3
    assert abs(specalbedo[0] - w) < 1e-12
    # diffuse attenuation reproduced from the same F0
    dif = rad_radiance((0.1,) * 3, specalbedo, w, 0.3)[0]
    spec_only = rad_radiance((0.0,) * 3, specalbedo, w, 0.3)[0]
    assert abs((dif - spec_only) - 0.1 * (1.0 - w) / math.pi) < 1e-9
    assert mb.specular_albedo_scale({}, comp) == 1.0


# ---------------------------------------------------------------------------
# 3. Corpus facts (skip themselves without the gitignored fixtures)
# ---------------------------------------------------------------------------

def _corpus_materials():
    root = _fixtures()
    if root is None:
        return None
    out = {}
    for mf in sorted(root.glob("*.lemesh/manifest.json")):
        for s in json.loads(mf.read_text(encoding="utf-8")).get("materials", []):
            out.setdefault(s["key"], (mf.parent, s))
    return out


def test_corpus_specular_roles_and_the_six_no_albedo_panels():
    """`stream-confirmed`, exports/fixtures_mat3 (51 packages, 100 materials):
    28 materials resolve a `specular` channel -- 19 `layer0_composite_specular`,
    1 `layer1_composite_specular`, 8 `layer0_specular_map`. SIX of the eight
    carry no `base_color` at all; every one of those six is `eMTForwardTransparent`
    and all six share the same texture `9cef9cbe9bc742ff`.
    """
    mats = _corpus_materials()
    if mats is None:
        return
    roles, no_albedo = {}, []
    for key, (_, s) in mats.items():
        ch = (s.get("channels") or {}).get("specular")
        if not ch:
            continue
        roles[ch["role_key"]] = roles.get(ch["role_key"], 0) + 1
        if "base_color" not in (s.get("channels") or {}):
            no_albedo.append((key, ch["texture"], s.get("mattype_name")))
    assert sum(roles.values()) == 28, roles
    assert roles.get("layer0_composite_specular") == 19
    assert roles.get("layer1_composite_specular") == 1
    assert roles.get("layer0_specular_map") == 8
    assert len(no_albedo) == 6, no_albedo
    assert {t for _, t, _ in no_albedo} == {"9cef9cbe9bc742ff"}
    assert {m for _, _, m in no_albedo} == {"eMTForwardTransparent"}


def test_corpus_never_overrides_the_specular_scalars():
    """No shipped material serialises `fresnel` / `specular_tint_color` /
    `specular_gloss` / `enable_specular`, so every `specular_map` material in the
    corpus runs on the authored defaults (`stream-confirmed`). That is what makes
    `k_fresnel = 0.010` the right constant to hard-wire.
    """
    mats = _corpus_materials()
    if mats is None:
        return
    banned = ("fresnel", "specular_tint_color", "specular_gloss", "enable_specular")
    for key, (_, s) in mats.items():
        for name in (s.get("named_scalars_resolved") or {}):
            base = name.split("_", 1)[1] if name.startswith("layer") else name
            assert base not in banned, (key, name)
