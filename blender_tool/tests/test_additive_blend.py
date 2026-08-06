"""`eBlendAdditive` is a framebuffer ADD, and it used to render OPAQUE.

The engine's blend op for `eBlendAdditive` (1) / `eBlendLinearDodge` (8) is
`dst = dst + src`. Two properties follow and neither is optional:

  * the surface **never occludes** what is behind it — `dst` survives unscaled;
  * `src` is added, so a black texel contributes nothing.

The importer used to record `le_blend_lossy` and then build an ordinary
Principled BSDF with `Alpha = 1.0` under Blender's `BLENDED` surface method —
which is ALPHA blending. The result was a fully opaque card: the one thing an
additive surface can never be. 22 of the materials in `blender_tool/exports` are
additive and most bind no colour channel at all, so they drew as solid
`bakecolor` chips.

⛔ The colour those cards ADD is *not* recoverable here. Liv's obj001 binds one
texture, `liv_basesuit_fx_clr`, whose role no `SShaderInputData` array in the
corpus declares — and the name cannot supply it: `_clr` maps to **21 distinct
roles** over 1,136 corpus observations. So the default contributes the identity
of `+` (nothing) and `opts['additive_unrouted_color']` opts into the guess.

Pure python: the *values* and *predicates* are asserted here; the node graph they
drive is asserted in Blender by a local working file.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from unittest import SkipTest

from le_mesh import materials as mat

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
EXPORTS = BLENDER_TOOL / "exports"
MB_PATH = BLENDER_TOOL / "addon" / "lone_echo_import" / "material_builder.py"
LIV = EXPORTS / "chars" / "2fd6839161785e9c_ff91757c910ea7b6.lemesh"
LIV_FX_KEY = "462c46aee0986b67__197e5a945532dcf8"

_MB = None


def _mb():
    """`material_builder` with a stub `bpy` — same loader as test_brdf_lobes."""
    global _MB
    if _MB is not None:
        return _MB
    if "bpy" not in sys.modules:
        stub = types.ModuleType("bpy")
        stub.data = types.SimpleNamespace(materials=None, images=None)
        stub.context = types.SimpleNamespace(scene=None)
        stub.types = types.SimpleNamespace(Material=object)
        sys.modules["bpy"] = stub
    spec = importlib.util.spec_from_file_location("_le_material_builder_additive", MB_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MB = mod
    return mod


def _liv_spec(key):
    manifest = LIV / "manifest.json"
    if not manifest.is_file():
        return None
    data = json.loads(manifest.read_text(encoding="utf-8"))
    for spec in data.get("materials", []):
        if spec.get("key") == key:
            return spec
    return None


# ---------------------------------------------------------------------------
# 1. The predicate
# ---------------------------------------------------------------------------

def test_additive_blend_modes_are_exactly_the_two_add_ops():
    mb = _mb()
    assert mb.BLENDMODE_ADDITIVE == frozenset({1, 8})
    for bm in (1, 8):
        assert mb.is_additive_blend({"blend_mode": bm}) is True
    for bm in (0, 7, 11, 12, None, "1", True):
        assert mb.is_additive_blend({"blend_mode": bm}) is False, bm


def test_additive_blend_is_on_by_default_and_can_be_turned_off():
    mb = _mb()
    assert mb.additive_blend_enabled(None) is True
    assert mb.additive_blend_enabled({}) is True
    assert mb.additive_blend_enabled({"additive_blend": False}) is False


def test_the_unrouted_colour_guess_is_OFF_by_default():
    """⛔ It routes a bind whose role is unknown. Opt-in, never a default."""
    mb = _mb()
    assert mb.additive_unrouted_color_enabled(None) is False
    assert mb.additive_unrouted_color_enabled({}) is False
    assert mb.additive_unrouted_color_enabled({"additive_unrouted_color": True}) is True
    # and it must not be switched on as a side effect of the blend fix
    assert mb.additive_unrouted_color_enabled({"additive_blend": True}) is False


# ---------------------------------------------------------------------------
# 2. Which bind (if any) the opt-in would use
# ---------------------------------------------------------------------------

def test_unrouted_colour_candidate_needs_additive_no_colour_and_exactly_one_bind():
    mb = _mb()
    base = {"blend_mode": 1, "unrouted_roles": ["rdef_bind1"],
            "role_textures": {"rdef_bind1": "abc"}}
    assert mb.additive_unrouted_color_role(base, {}) == "rdef_bind1"
    # not additive -> nothing to add
    assert mb.additive_unrouted_color_role({**base, "blend_mode": 0}, {}) is None
    # a routed colour already exists -> use it, do not guess
    assert mb.additive_unrouted_color_role(base, {"base_color": {"file": "x"}}) is None
    assert mb.additive_unrouted_color_role(base, {"emission": {"file": "x"}}) is None
    # more than one candidate -> nothing to choose between them
    two = {**base, "unrouted_roles": ["rdef_bind0", "rdef_bind1"],
           "role_textures": {"rdef_bind0": "a", "rdef_bind1": "b"}}
    assert mb.additive_unrouted_color_role(two, {}) is None
    # a role with no texture behind it is not a candidate
    assert mb.additive_unrouted_color_role({**base, "role_textures": {}}, {}) is None


# ---------------------------------------------------------------------------
# 3. `explain_unrouted` separates "the cook generated it" from "an artist named it"
# ---------------------------------------------------------------------------

def test_a_generated_composite_bind_and_an_authored_bind_no_longer_read_alike():
    gen = mat.explain_unrouted(
        "rdef_bind18", "generated_composite_9a5bbf896bbbadbc_0834023822550807")
    art = mat.explain_unrouted("rdef_bind1", "liv_basesuit_fx_clr")
    assert gen["texture_name_is_generated"] is True
    assert art["texture_name_is_generated"] is False
    assert gen["reason"] != art["reason"]
    assert "composite_roles_from_format" in gen["reason"]
    assert gen["texture_name"].startswith(mat.COMPOSITE_NAME_PREFIX)
    assert art["texture_name"] == "liv_basesuit_fx_clr"
    for rec in (gen, art):
        assert rec["classification"] == "unresolved"


def test_explain_unrouted_still_works_with_no_name_and_keeps_its_old_keys():
    """The name is optional: an old caller must keep getting the old contract."""
    rec = mat.explain_unrouted("rdef_bind1")
    assert set(rec) >= {"role", "classification", "reason", "named"}
    assert "texture_name" not in rec
    assert mat.explain_unrouted("layer0_composite_data0")["classification"] == \
        "deliberately unrouted"
    assert mat.explain_unrouted("unknown_s23")["named"] is False


# ---------------------------------------------------------------------------
# 4. The manifest carries the names, and only this material's
# ---------------------------------------------------------------------------

def test_build_material_spec_carries_texture_names_for_its_own_binds_only():
    spec = mat.build_material_spec(
        "k", shaderset_hash="s",
        role_textures={"layer0_composite_diffuse": "aa", "rdef_bind1": "bb"},
        dxgi_by_tex={"aa": 72, "bb": 78},
        texture_names={"aa": "some_clr", "bb": "liv_basesuit_fx_clr",
                       "cc": "a texture this material does not bind"})
    assert spec["texture_names"] == {"aa": "some_clr", "bb": "liv_basesuit_fx_clr"}
    # and the note picked the name up
    note = spec["unrouted_role_notes"]["rdef_bind1"]
    assert note["texture_name"] == "liv_basesuit_fx_clr"


def test_texture_names_is_always_present_so_the_key_sets_stay_identical():
    """Same contract `role_sources` / `unrouted_role_notes` / `brdf_lobes` follow."""
    spec = mat.build_material_spec("k", shaderset_hash="s")
    assert spec["texture_names"] == {}


# ---------------------------------------------------------------------------
# 5. The shipped material this exists for (SKIP without the package)
# ---------------------------------------------------------------------------

def test_livs_fx_cards_are_additive_bind_one_named_texture_and_route_nothing():
    spec = _liv_spec(LIV_FX_KEY)
    if spec is None:
        raise SkipTest(
            f"material {LIV_FX_KEY} is not in {LIV.name} — `blender_tool/exports/` "
            f"holds gitignored extracted game data, so a clean checkout has "
            f"none. Re-extract with `python.exe "
            f"blender_tool/extractor/le_extract.py --archive 2fd6839161785e9c "
            f"--mesh ff91757c910ea7b6` to make this test able to run. ⛔ WHILE "
            f"THIS SKIP IS ACTIVE NOTHING VERIFIES THAT THE SHIPPED ADDITIVE "
            f"CARD IS ADDITIVE, BINDS ONE NAMED TEXTURE AND ROUTES NO CHANNEL.")
    mb = _mb()
    assert mb.is_additive_blend(spec) is True
    assert len(spec.get("channels") or {}) == 0
    assert len(spec.get("unrouted_roles") or []) == 1
    role = mb.additive_unrouted_color_role(spec, spec.get("channels") or {})
    assert role is not None
    tex = spec["role_textures"][role]
    name = (spec.get("texture_names") or {}).get(tex)
    if name is not None:                        # pre-`texture_names` manifest
        assert not name.startswith(mat.COMPOSITE_NAME_PREFIX)


def test_the_corpus_has_more_than_one_additive_material_so_this_is_not_a_liv_quirk():
    if not EXPORTS.is_dir():
        raise SkipTest(
            "no `blender_tool/exports/` directory in this checkout — it holds "
            "gitignored extracted game data, so a clean checkout has none. "
            "Re-extract with `python.exe blender_tool/extractor/le_extract.py "
            "--archive <hash> --all` to make this test able to run. ⛔ WHILE "
            "THIS SKIP IS ACTIVE NOTHING VERIFIES THAT ADDITIVE BLENDING IS A "
            "CORPUS-WIDE MODE RATHER THAN A LIV-ONLY QUIRK.")
    additive = 0
    for manifest in EXPORTS.glob("**/manifest.json"):
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for spec in data.get("materials", []):
            if spec.get("blend_mode") in (1, 8):
                additive += 1
    if additive == 0:
        raise SkipTest(
            "no `manifest.json` under `blender_tool/exports/` carries a "
            "material with `blend_mode` 1 (eBlendAdditive) or 8 "
            "(eBlendLinearDodge) — the packages in this checkout (if any) do "
            "not cover the additive corpus. Re-extract a character archive "
            "with `python.exe blender_tool/extractor/le_extract.py --archive "
            "2fd6839161785e9c --all` to make this test able to run. ⛔ WHILE "
            "THIS SKIP IS ACTIVE NOTHING VERIFIES THAT ADDITIVE BLENDING IS A "
            "CORPUS-WIDE MODE RATHER THAN A LIV-ONLY QUIRK.")
    assert additive >= 2
