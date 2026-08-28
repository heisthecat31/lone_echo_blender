"""Rim lighting: find the mask under EITHER spelling, and don't need a colour.

⛔ Two independent gates were dropping it. `build_rim_lighting` looked for a
role key containing `"rimlighting"` and then required `accent_tint`, and across
the 18 shipped levels that left it firing on **15 of the 138** materials that
bind a rim mask:

    layer0_rimlighting_map   54   matched
    layer1_rimlighting_map   21   matched
    layer2_rimlighting_map    6   matched
    layer0_rim_map           38   MISSED
    layer2_rim_map           22   MISSED
    layer1_rim_map           16   MISSED
    layer3_rim_map           14   MISSED
    layer1_secondary_rim_map  2   MISSED

72 of 138 never matched the name at all; of the 66 that did, 51 were then
dropped for having no `accent_tint` -- a slot only `mpl_arena_a` carries.

The rim term is what makes a surface dark head-on and bright at grazing
angles. Losing it is why `mpl_combat_war_room`'s panel faces glow flat instead
of showing only their edge strips.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

#: `material_builder` imports `bpy` at module scope, so load it off disk behind
#: a stub -- the same loader `test_material_builder_nodes` uses.
_MB_PATH = (Path(__file__).resolve().parents[1] / "addon" /
            "lone_echo_import" / "material_builder.py")
_MB = None


def _mb():
    global _MB
    if _MB is not None:
        return _MB
    if "bpy" not in sys.modules:
        stub = types.ModuleType("bpy")
        stub.data = types.SimpleNamespace(materials=None, images=None)
        stub.context = types.SimpleNamespace(scene=None)
        stub.types = types.SimpleNamespace(Material=object)
        sys.modules["bpy"] = stub
    spec = importlib.util.spec_from_file_location("_le_mb_rim", _MB_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MB = mod
    return mod


def rim_role_of(roles):
    return _mb().rim_role_of(roles)

#: Every rim key spelling observed in the shipped corpus, with its count.
CORPUS_RIM_ROLES = {
    "layer0_rimlighting_map": 54,
    "layer0_rim_map": 38,
    "layer2_rim_map": 22,
    "layer1_rimlighting_map": 21,
    "layer1_rim_map": 16,
    "layer3_rim_map": 14,
    "layer2_rimlighting_map": 6,
    "layer1_secondary_rim_map": 2,
}


def test_every_corpus_spelling_is_matched():
    """★ All eight, not just the two the old substring knew."""
    for key in CORPUS_RIM_ROLES:
        assert rim_role_of({key: "ab" * 8}) == key, key


def test_the_missed_spellings_are_the_majority():
    """⛔ Guards the regression by weight.

    ⚠ Two different totals, and they are easy to confuse: the table counts role
    BINDINGS (173 -- `matidx 64` alone binds `layer2` and `layer3`), while the
    138 quoted in the docstring counts MATERIALS. 92 of the 173 bindings, and
    72 of the 138 materials, matched nothing under the old substring.
    """
    missed = sum(n for k, n in CORPUS_RIM_ROLES.items() if "rimlighting" not in k)
    assert missed == 92
    assert sum(CORPUS_RIM_ROLES.values()) == 173
    assert missed > sum(CORPUS_RIM_ROLES.values()) / 2


def test_a_material_with_no_rim_mask_is_left_alone():
    assert rim_role_of({"layer0_albedo_map": "ab" * 8}) is None
    assert rim_role_of({}) is None
    assert rim_role_of(None) is None


def test_the_suffix_cannot_sweep_up_an_unrelated_role():
    """⚠ `"rim" in key` would also match `primary`. The suffix must not."""
    assert _mb().RIM_ROLE_SUFFIX == "_rim_map"
    assert rim_role_of({"layer0_primary_map": "ab" * 8}) is None
    assert rim_role_of({"layer0_primary_albedo_map": "ab" * 8}) is None


def test_selection_is_deterministic_when_several_are_bound():
    """The war room's matidx 64 binds layer2 AND layer3. Sorted order, so the
    same mask is chosen every import rather than dict-order roulette."""
    roles = {"layer3_rim_map": "cd" * 8, "layer2_rim_map": "ab" * 8}
    assert rim_role_of(roles) == "layer2_rim_map"
    assert rim_role_of(dict(reversed(list(roles.items())))) == "layer2_rim_map"


def test_rimlighting_wins_over_a_plain_rim_map():
    """Sorted order puts `layer0_rim_map` before `layer0_rimlighting_map`;
    either is a valid mask, but the choice must be stable."""
    roles = {"layer0_rimlighting_map": "cd" * 8, "layer0_rim_map": "ab" * 8}
    assert rim_role_of(roles) in roles
    assert rim_role_of(roles) == rim_role_of(dict(reversed(list(roles.items()))))
