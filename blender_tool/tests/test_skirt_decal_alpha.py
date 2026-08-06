"""`eMTSkirt` is the DECAL pass, and its diffuse alpha is the cut-out.

The defect these lock down docs/MATERIALS.md: `eBlendSkirt` was in
`OPAQUE_BLENDMODES`, so `render_mode_for(10, 10)` returned `OPAQUE`, which
suppressed `BASE_COLOR_ALPHA` in `build_material_spec` and left
`uses_base_color_alpha` at False in the addon. Jack's 411-triangle decal mesh
(`obj005_e34877edc800ed69`, material `d81e1db32ebca219__c7842ca1cefde379`)
therefore rendered its logo sheet with `Alpha = 1` — every patch a solid BLACK
CARD carrying its artwork.

Three groups:

* **pure** — the decision functions on both sides of the tree, and the fact that
  they now AGREE, so a fresh manifest never takes the addon's repair branch;
* **narrowness** — the repair fires for skirts only. A non-skirt that stored
  `alpha_source: "NONE"` is still obeyed verbatim, and no non-skirt material in
  any shipped package changes render mode;
* **shipped bytes** — the alpha plane of the decal sheet is decoded here, from
  the DDS on disk, with a 25-line BC3 alpha-block reader and no third-party
  module, because "the alpha is a cut-out mask" is the whole argument.

Everything that needs `exports/` raises `unittest.SkipTest` with a reason when
it is absent (it is gitignored) — never a silent pass.
"""

from __future__ import annotations

import importlib.util
import json
import struct
import sys
import types
from pathlib import Path
from unittest import SkipTest

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
if str(BLENDER_TOOL) not in sys.path:
    sys.path.insert(0, str(BLENDER_TOOL))

from le_mesh import materials as M                        # noqa: E402

MB_PATH = BLENDER_TOOL / "addon" / "lone_echo_import" / "material_builder.py"
EXPORTS = BLENDER_TOOL / "exports"
JACK = EXPORTS / "chars" / "c6bc8607972268c9_64b4b5b2a0153f7e.lemesh"
JACK_SKIRT_KEY = "d81e1db32ebca219__c7842ca1cefde379"
JACK_DECAL_SHEET = "8cd58ef1b6314b03"

_MB = None


def _mb():
    """Load material_builder with a stub `bpy` (same loader as
    tests/test_material_builder_nodes.py)."""
    global _MB
    if _MB is not None:
        return _MB
    if "bpy" not in sys.modules:
        stub = types.ModuleType("bpy")
        stub.data = types.SimpleNamespace(materials=None, images=None)
        stub.context = types.SimpleNamespace(scene=None)
        stub.types = types.SimpleNamespace(Material=object)
        sys.modules["bpy"] = stub
    spec = importlib.util.spec_from_file_location("_le_material_builder_skirt", MB_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MB = mod
    return mod


def _skirt_scalars(**over):
    s = {"mattype": 10, "mattype_name": "eMTSkirt",
         "blend_mode": 10, "blend_mode_name": "eBlendSkirt",
         "alpha": 1.0, "double_sided": False}
    s.update(over)
    return s


def _skirt_spec(dxgi=78):
    """A skirt built the way the extractor builds one: BC3_SRGB composite diffuse."""
    return M.build_material_spec(
        "sk__m", shaderset_hash="sk", material_hash="m",
        role_textures={"layer0_composite_diffuse": "d",
                       "layer0_composite_normals": "n"},
        dxgi_by_tex={"d": dxgi, "n": 83},
        scalars=_skirt_scalars(),
        texture_files={"d": "textures/d.dds", "n": "textures/n.dds"})


# ---------------------------------------------------------------------------
# 1. the decoder: eMTSkirt / eBlendSkirt is not opaque
# ---------------------------------------------------------------------------

def test_render_mode_for_skirt_is_blend_not_opaque():
    assert M.render_mode_for(10, 10) == ("BLEND", False)
    assert M.render_mode_for(10, 0) == ("BLEND", False)    # pass alone is enough
    assert M.render_mode_for(1, 10) == ("BLEND", False)    # equation alone is enough


def test_skirt_left_the_opaque_blendmode_set():
    assert 10 not in M.OPAQUE_BLENDMODES
    assert M.OPAQUE_BLENDMODES == frozenset({0, 16})       # Opaque / NoColorWrites
    assert M.SKIRT_MATTYPES == frozenset({10})
    assert M.SKIRT_BLENDMODES == frozenset({10})


def test_the_other_modes_are_untouched():
    """The fix must move skirts and nothing else."""
    assert M.render_mode_for(1, 0) == ("OPAQUE", False)    # eMTForwardOpaque
    assert M.render_mode_for(0, 0) == ("OPAQUE", False)    # eMTDeferredOpaque
    assert M.render_mode_for(0, 16) == ("OPAQUE", False)   # eBlendNoColorWrites
    assert M.render_mode_for(9, 0) == ("CLIP", False)      # eMTAlphaTested
    assert M.render_mode_for(2, 12) == ("BLEND", False)    # eBlendTranslucent
    assert M.render_mode_for(16, 8) == ("BLEND", True)     # additive stays LOSSY


def test_skirt_spec_routes_the_diffuse_alpha():
    spec = _skirt_spec()
    assert spec["render_mode"] == "BLEND"
    assert spec["alpha_source"] == "BASE_COLOR_ALPHA"
    assert spec["alpha_terms"] == ["BASE_COLOR_ALPHA"]
    a = spec["channels"]["alpha"]
    assert a["from_channel"] == "base_color"
    assert a["component"] == "A"            # `k_composite_diffuse[i].w`
    assert a["texture"] == "d"
    assert a["alpha_plane"] is True         # BC3 has a real 8-bit alpha plane
    assert a["punchthrough"] is False       # ...unlike BC1's single bit


def test_skirt_with_a_bc1_diffuse_gets_a_punchthrough_alpha_not_none():
    """BC1 carries one bit of alpha; it is still alpha and must still be read."""
    spec = _skirt_spec(dxgi=72)
    assert spec["render_mode"] == "BLEND"
    assert spec["alpha_source"] == "BASE_COLOR_ALPHA"
    assert spec["channels"]["alpha"]["punchthrough"] is True


# ---------------------------------------------------------------------------
# 2. the addon agrees with the decoder, and repairs only stale skirts
# ---------------------------------------------------------------------------

def test_addon_and_decoder_agree_on_a_fresh_skirt():
    """A manifest written today must NOT need the repair branch."""
    mb = _mb()
    spec = _skirt_spec()
    assert mb.resolve_render_mode(spec) == spec["render_mode"] == "BLEND"
    assert mb.is_skirt(spec) is True
    assert mb.uses_base_color_alpha(spec, spec["channels"]) is True


def test_stale_skirt_manifest_is_repaired():
    """Every `.lemesh` on disk predates the fix: OPAQUE + alpha_source NONE."""
    mb = _mb()
    stale = _skirt_spec()
    stale["render_mode"] = "OPAQUE"          # what the old decoder wrote
    stale["alpha_source"] = "NONE"
    stale["alpha_terms"] = []
    del stale["channels"]["alpha"]           # ...and it wrote no alpha channel
    assert mb.resolve_render_mode(stale) == "BLEND"
    assert mb.uses_base_color_alpha(stale, stale["channels"]) is True


def test_a_stored_alpha_source_of_none_is_still_obeyed_off_the_skirt_pass():
    """The repair is narrow: only `is_skirt` re-runs the heuristic."""
    mb = _mb()
    opaque = M.build_material_spec(
        "op__m", role_textures={"layer0_composite_diffuse": "d"},
        dxgi_by_tex={"d": 78},
        scalars={"mattype": 1, "blend_mode": 0, "alpha": 1.0},
        texture_files={"d": "textures/d.dds"})
    assert opaque["alpha_source"] == "NONE"
    assert mb.is_skirt(opaque) is False
    assert mb.uses_base_color_alpha(opaque, opaque["channels"]) is False
    # ...and a BLEND material that genuinely stores NONE keeps its answer
    blended = dict(opaque, render_mode="BLEND", mattype=2, blend_mode=12)
    assert mb.uses_base_color_alpha(blended, blended["channels"]) is False


def test_skirt_alpha_is_not_multiplied_by_itself():
    """D9: the derived `alpha` channel and `bc_node.Alpha` are one sampler.

    `engine-confirmed` before the fix on `bcb9caff4b7a4d37__ed972c98f19abfdc`
    (a local working file): the Alpha socket listed `0917328f9ecabf70.dds`
    TWICE through a MULTIPLY, i.e. `alpha ** 2`.
    """
    mb = _mb()
    spec = _skirt_spec()
    ch = spec["channels"]
    assert mb.alpha_channel_is_base_color(ch["alpha"], ch["base_color"]) is True
    # a real, independent alpha map is NOT the base colour and must still be wired
    other = {"texture": "z", "role_key": "layer1_alpha_map", "component": "R"}
    assert mb.alpha_channel_is_base_color(other, ch["base_color"]) is False
    assert mb.alpha_channel_is_base_color(None, ch["base_color"]) is False
    assert mb.alpha_channel_is_base_color(ch["alpha"], None) is False


def test_the_ab_escape_hatch_defaults_on():
    """`opts['skirt_alpha']=0` reproduces the pre-fix picture, and only that."""
    mb = _mb()
    assert mb.skirt_alpha_enabled(None) is True
    assert mb.skirt_alpha_enabled({}) is True
    assert mb.skirt_alpha_enabled({"skirt_alpha": True}) is True
    assert mb.skirt_alpha_enabled({"skirt_alpha": False}) is False
    assert mb.skirt_alpha_enabled({"skirt_alpha": 0}) is False


def test_the_hero_harness_exposes_the_hatch():
    """The before/after pair must be a CLI flag, not an edit (harness comment)."""
    src = (BLENDER_TOOL / "tests" / "blender_hero_render.py").read_text(encoding="utf-8")
    assert '"skirt_alpha"' in src


# ---------------------------------------------------------------------------
# 3. shipped bytes
# ---------------------------------------------------------------------------

def _dds_dxgi(path: Path):
    b = path.read_bytes()[:148]
    if b[:4] != b"DDS " or b[84:88] != b"DX10":
        return None
    return struct.unpack_from("<I", b, 128)[0]


def _bc3_alpha_histogram(path: Path):
    """Alpha histogram of mip 0 of a BC3 (DXT5) DDS -- pure stdlib.

    A BC3 block is 16 bytes: 8 of alpha (two endpoints then 16 x 3-bit indices,
    little-endian), then 8 of BC1 colour. `a0 > a1` selects the 8-value ramp,
    otherwise 6 values plus explicit 0 and 255.
    """
    raw = path.read_bytes()
    h, w = struct.unpack_from("<II", raw, 12)          # dwHeight, dwWidth
    off = 148 if raw[84:88] == b"DX10" else 128
    bx, by = (w + 3) // 4, (h + 3) // 4
    hist = [0] * 256
    for i in range(bx * by):
        blk = raw[off + i * 16: off + i * 16 + 8]
        if len(blk) < 8:
            break
        a0, a1 = blk[0], blk[1]
        if a0 > a1:
            ramp = [a0, a1] + [((7 - k) * a0 + k * a1) // 7 for k in range(1, 7)]
        else:
            ramp = [a0, a1] + [((5 - k) * a0 + k * a1) // 5 for k in range(1, 5)] + [0, 255]
        bits = int.from_bytes(blk[2:8], "little")
        for t in range(16):
            hist[ramp[(bits >> (3 * t)) & 7]] += 1
    return hist, bx * by * 16


def test_the_decal_sheet_alpha_is_a_cutout_mask():
    """The whole argument, from the shipped DDS: 8cd58ef1b6314b03 is BC3 and its
    alpha plane is BIMODAL — a coverage mask, not a constant and not noise."""
    dds = JACK / "textures" / f"{JACK_DECAL_SHEET}.dds"
    if not dds.is_file():
        raise SkipTest(
            f"{dds} is absent — `blender_tool/exports/` is gitignored "
            f"extracted game data, so a clean checkout has neither the package "
            f"nor its DDS files. Re-extract with `python.exe "
            f"blender_tool/extractor/le_extract.py --archive c6bc8607972268c9 "
            f"--mesh 64b4b5b2a0153f7e --textures` to make this test able to "
            f"run. ⛔ WHILE THIS SKIP IS ACTIVE THE WHOLE ARGUMENT — THAT THE "
            f"DECAL SHEET'S ALPHA PLANE IS A BIMODAL CUT-OUT MASK AND NOT A "
            f"CONSTANT — IS NEVER READ OFF THE SHIPPED BYTES.")
    assert _dds_dxgi(dds) == 78                    # BC3_UNORM_SRGB
    hist, n = _bc3_alpha_histogram(dds)
    lo = sum(hist[:8]) / n
    hi = sum(hist[248:]) / n
    assert 0.60 < lo < 0.75, f"transparent fraction {lo:.3f}"
    assert 0.15 < hi < 0.30, f"opaque fraction {hi:.3f}"
    assert lo + hi > 0.85, "alpha is not bimodal -- it would not be a cut-out"


def test_jacks_decal_mesh_gets_its_alpha_back():
    man = JACK / "manifest.json"
    if not man.is_file():
        raise SkipTest(
            f"{JACK.name} (Jack, whose 411-triangle decal mesh is the defect) "
            f"is not extracted — `blender_tool/exports/` is gitignored "
            f"extracted game data, so a clean checkout has none. Re-extract "
            f"with `python.exe blender_tool/extractor/le_extract.py --archive "
            f"c6bc8607972268c9 --mesh 64b4b5b2a0153f7e` to make this test able "
            f"to run. ⛔ WHILE THIS SKIP IS ACTIVE NOTHING VERIFIES ON A "
            f"SHIPPED MANIFEST THAT THE eMTSkirt DECAL RESOLVES TO BLEND AND "
            f"GETS ITS BASE-COLOUR ALPHA BACK.")
    mb = _mb()
    doc = json.loads(man.read_text(encoding="utf-8"))
    spec = next(s for s in doc["materials"] if s["key"] == JACK_SKIRT_KEY)
    assert spec["mattype"] == 10 and spec["blend_mode"] == 10
    assert spec["channels"]["base_color"]["texture"] == JACK_DECAL_SHEET
    assert mb.resolve_render_mode(spec) == "BLEND"
    assert mb.uses_base_color_alpha(spec, spec["channels"]) is True


def test_no_non_skirt_material_changes_render_mode_anywhere():
    """The regression guard, over every package this tree holds.

    Container: `blender_tool/exports/**/manifest.json`. Coverage: every manifest
    that carries a `materials` list. The ONLY specs whose resolved render mode
    may differ from the stored one are skirts.
    """
    from unittest import SkipTest
    if not EXPORTS.is_dir():
        raise SkipTest("no `exports/` in this checkout — the regression guard "
                       "has no package to sweep.")
    mb = _mb()
    seen = moved = pkgs = 0
    for man in sorted(EXPORTS.rglob("manifest.json")):
        try:
            doc = json.loads(man.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if "materials" not in doc:
            continue
        pkgs += 1
        for spec in doc["materials"]:
            stored = spec.get("render_mode")
            if not isinstance(stored, str):
                continue
            seen += 1
            got = mb.resolve_render_mode(spec)
            if got == stored:
                continue
            moved += 1
            assert mb.is_skirt(spec), (
                f"{man.parent.name}/{spec['key']}: {stored} -> {got} "
                f"but mattype={spec.get('mattype')} blend={spec.get('blend_mode')}")
            assert (stored, got) == ("OPAQUE", "BLEND")
    if not (pkgs and seen):
        raise SkipTest(
            f"swept {pkgs} package(s) and {seen} stored render mode(s) — no "
            "manifest in `exports/` carries a `materials` list, so the "
            "regression guard proved nothing. ⛔ WHILE THIS SKIP IS ACTIVE A "
            "NON-SKIRT MATERIAL COULD CHANGE RENDER MODE UNNOTICED. Re-extract "
            "with `--direct-materials` to enable it.")
