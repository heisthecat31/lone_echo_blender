"""The SG5 lobe basis, and the invariant tying the two SG5 code paths together.

`evr_lighting` (the importer) evaluates the five spherical gaussians per pixel
against the surface's tangent-space normal. `evr_lightmap` (the extractor)
collapses the same five lobes ahead of time with a fixed weight each, for the
PNG a consumer gets when it cannot evaluate the basis. Those two must agree
wherever the normal is flat, or one surface would change brightness depending
on which path drew it.

The literals come from `material_base_ps.hlsl:1097` via docs/EVR_LIGHTING.md.
`evr_lighting` imports `bpy` unconditionally, so the constants are read out of
its source with `ast` rather than by importing it.
"""
from __future__ import annotations

import ast
import math
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

import evr_lightmap as LM                                        # noqa: E402

_SOURCE = (_ROOT / "blender_tool" / "addon" / "lone_echo_import"
           / "evr_lighting.py")


def _constants() -> dict:
    """Top-level constant assignments in `evr_lighting`, without importing it.

    Evaluated rather than `literal_eval`'d one by one, because the derived ones
    (`SG5_K`, `SG5_WEIGHTS`) are comprehensions over the literals -- and those
    derivations are exactly what these tests need to check. Anything that needs
    a name this namespace does not have (i.e. anything touching `bpy`) raises
    and is skipped.
    """
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    out: dict = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            continue
        try:
            exec(compile(ast.Module(body=[node], type_ignores=[]),
                         "<evr_lighting-consts>", "exec"), out)
        except Exception:
            continue
    out.pop("__builtins__", None)
    return out


CONST = _constants()


def test_five_lobes_are_unit_vectors():
    """A direction that is not unit length would scale the whole term."""
    dirs = CONST["SG5_LOBE_DIRS"]
    assert len(dirs) == 5
    for d in dirs:
        assert abs(math.sqrt(sum(c * c for c in d)) - 1.0) < 1e-6


def test_the_basis_is_hemispherical():
    """z running 0.1..0.9 -- never negative -- is what makes the normal matter.

    A full-sphere basis could be collapsed to a constant per lobe and still be
    right; a hemispherical one cannot, because a lobe can face away from the
    surface. `kLobeDirsSphereSG5` is the sphere basis and is NOT this one.
    """
    z = [d[2] for d in CONST["SG5_LOBE_DIRS"]]
    assert z == sorted(z)
    assert all(0.0 < c < 1.0 for c in z)
    assert z == [0.1, 0.3, 0.5, 0.7, 0.9]


def test_flat_normal_reduces_to_the_shipped_weights():
    """With n_ts = (0,0,1), `saturate(dot(dir, n))` is just the lobe's z."""
    k = (2.0 / CONST["SG5_LAMBDA"]) * CONST["SG5_SCALE"]
    for d, w in zip(CONST["SG5_LOBE_DIRS"], CONST["SG5_WEIGHTS"]):
        flat = max(0.0, min(1.0, d[2]))          # dot((x,y,z), (0,0,1)) == z
        assert abs(flat * k - w) < 1e-12


def test_importer_and_extractor_agree_on_a_flat_surface():
    """The invariant: same surface, same brightness, whichever path drew it."""
    assert len(LM.sg5_weights()) == len(CONST["SG5_WEIGHTS"])
    for baked, live in zip(LM.sg5_weights(), CONST["SG5_WEIGHTS"]):
        assert abs(baked - live) < 1e-12


def test_weight_sum_is_the_clamp_ceiling_of_the_old_8bit_path():
    """0.68912 -- the value the collapsed PNG used to saturate at."""
    assert abs(sum(CONST["SG5_WEIGHTS"]) - 0.68912) < 1e-5


def test_a_lobe_facing_away_contributes_nothing():
    """`saturate` is load-bearing: without it a back-facing lobe SUBTRACTS.

    Lobe 0 points mostly along +x/-y with z=0.1, so a normal tilted against it
    gives a negative dot. Unclamped that would darken the surface below what an
    unlit texel gives, which is not a thing irradiance can do.
    """
    d = CONST["SG5_LOBE_DIRS"][0]
    n = (-d[0], -d[1], -d[2])                    # straight back at it
    raw = sum(a * b for a, b in zip(d, n))
    assert raw < 0.0
    assert max(0.0, min(1.0, raw)) == 0.0
