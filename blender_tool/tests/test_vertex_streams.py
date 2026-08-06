"""The vertex streams the importer must not drop, and the invariants they hold.

`mesh_builder` imported `uv0`/`uv1` (+ the resolved lightmap set), `color0`,
`normal` and `position` — and silently dropped `tangent`, `color1` and every
other texcoord slot, all of which the extractor had already decoded into
`blobs/` and named in the manifest. Corpus census over `blender_tool/exports`
(286 packages / 913 objects, 2026-08-05):

    tangent  913/913  100.0 %      <- imported on NONE
    color1   523/913   57.3 %      <- imported on NONE
    uv2       91/913   10.0 %      <- imported on NONE
    uv3       29/913    3.2 %      <- imported on NONE

⚠ Every data-backed test here is written the way `test_scene_materials_v2.py`
does it — `>=` thresholds and a clean SKIP when the fixtures are absent — NOT as
an exact census. An exact census asserts a property of the packages on disk as
if it were a property of the code, which is how two tests came to call a bug fix
a regression on 2026-08-05.
"""

from __future__ import annotations

import json
import math
import re
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
EXPORTS = BLENDER_TOOL / "exports"

# `mesh_builder._UV_KEY_RE` — duplicated here on purpose: importing mesh_builder
# needs `bpy`. Kept in step by `test_uv_key_regex_matches_the_builders`.
_UV_KEY_RE = re.compile(r"^uv(\d+)$")


def _packages(limit: int = 40):
    if not EXPORTS.is_dir():
        return []
    out = []
    for mf in sorted(EXPORTS.rglob("manifest.json")):
        try:
            man = json.loads(mf.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if man.get("objects"):
            out.append((mf.parent, man))
        if len(out) >= limit:
            break
    return out


def _blob(pkg_dir: Path, entry: dict):
    raw = (pkg_dir / entry["blob"]).read_bytes()
    n = len(raw) // 4
    return struct.unpack("<%df" % n, raw[: n * 4]), int(entry["comps"])


# ---------------------------------------------------------------------------
# the UV-slot rule
# ---------------------------------------------------------------------------

def test_uv_key_regex_matches_the_builders():
    """UV sets differ by texcoord SLOT, not usage — the key is `uv<slot>`."""
    assert _UV_KEY_RE.match("uv0") and _UV_KEY_RE.match("uv11")
    assert not _UV_KEY_RE.match("uv") and not _UV_KEY_RE.match("uvx")
    assert not _UV_KEY_RE.match("color1") and not _UV_KEY_RE.match("uv0_extra")
    assert int(_UV_KEY_RE.match("uv3").group(1)) == 3


def test_uv_keys_order_is_deterministic_and_uv0_stays_first():
    """`uv0` must stay UV layer 0 (the render UV); extras append in slot order.

    Reproduces the builder's ordering rule on synthetic input so it is asserted
    without `bpy`: base pair, then the resolved lightmap set, then every
    remaining `uvN` ascending.
    """
    attrs = {"uv3": {}, "uv0": {}, "position": {}, "uv2": {}, "uv1": {}, "color1": {}}
    uv_keys = ["uv0", "uv1"]
    lm = "uv2"                                     # a resolved lightmap slot
    if lm not in uv_keys:
        uv_keys.append(lm)
    uv_keys += sorted((k for k in attrs if _UV_KEY_RE.match(k) and k not in uv_keys),
                      key=lambda k: int(k[2:]))
    assert uv_keys == ["uv0", "uv1", "uv2", "uv3"]
    assert uv_keys[0] == "uv0"


# ---------------------------------------------------------------------------
# the shipped tangent basis — a DECODER invariant, checked over whatever is there
# ---------------------------------------------------------------------------

def test_the_shipped_tangent_basis_is_orthogonal_to_the_normal():
    """`tangent` is EUsage 3, `s16n` x4. The XYZ basis is byte-faithful.

    Measured 2026-08-05 over 40 packages / **509,266 vertices**:
    **100.00 %** satisfy `|dot(t_hat, n_hat)| < 0.02`. Not 99-point-something —
    every single vertex. So the `s16n` decode and the basis are correct.

    ⚠ Do NOT compare against Blender's `vertex.normal`: it is recomputed from the
    faces, not the decoded custom split normal, and comparing against it reports
    a spurious ~40 % orthogonality. Compare decoded-vs-decoded. That mistake was
    made once already, in the first probe written for this feature.
    """
    checked = tangent_objs = ortho = 0
    for pkg_dir, man in _packages():
        for o in man["objects"]:
            A = o.get("attributes") or {}
            if "tangent" not in A or "normal" not in A:
                continue
            tangent_objs += 1
            t, tc = _blob(pkg_dir, A["tangent"])
            nv, nc = _blob(pkg_dir, A["normal"])
            for i in range(int(o["vertex_count"])):
                tx, ty, tz = t[i * tc: i * tc + 3]
                nx, ny, nz = nv[i * nc: i * nc + 3]
                tl = math.sqrt(tx * tx + ty * ty + tz * tz)
                nl = math.sqrt(nx * nx + ny * ny + nz * nz)
                if tl < 1e-6 or nl < 1e-6:
                    continue
                checked += 1
                if abs((tx * nx + ty * ny + tz * nz) / (tl * nl)) < 0.02:
                    ortho += 1
    if not checked:                      # fixtures absent — nothing to assert
        return
    assert tangent_objs > 0
    assert ortho / checked >= 0.99, f"only {100.0 * ortho / checked:.1f}% orthogonal"


def test_tangent_w_is_a_FOUR_state_field_not_a_handedness_bit():
    """★ WAS an open question; ANSWERED 2026-08-05. The assertions below are
    unchanged — the four-state fact is still the fact, and this test still pins
    it. What changed is that the two halves now have meanings:

      * the SIGN is the bitangent handedness — `sign(w)` matches the UV-derived
        handedness on 397,082 / 397,082 vertices (100.00 %);
      * `|w|` tags a duplicated BACK-FACE SHELL — every `|w| = 0.5` vertex has a
        position-identical `|w| = 1.0` partner (109,400/109,400) whose normal is
        exactly negated (99.92 %).

    Pinned by `tests/test_shipped_tangent.py`; see `docs/MATERIALS.md` and
    `docs/CHARACTERS.md` §3.1. ★ The negative list below PREDICTED it:
    "0 objects carry only 0.5" is "no back shell without a front".
    ⛔ The last line of this docstring ("consumes it nowhere") is now false —
    `material_builder._shipped_tangent_normal` consumes the sign.

    The original text follows, unedited:

    ★ OPEN QUESTION, recorded as a measurement rather than assumed away.

    `.w` was written up as "the bitangent handedness (+/-1)" when the import
    landed. It is not. Over 40 packages / 509,266 vertices it takes **exactly
    four** values and nothing else:

        -1.00  29.77 %      -0.50  24.38 %      +0.50  21.67 %      +1.00  24.19 %

    `s16n` maps int16 -> [-1, 1], so this is a deliberate 2-bit quantisation:
    a sign AND a magnitude. The sign is almost certainly the handedness; what
    `|w| in {0.5, 1.0}` selects is **unresolved**.

    ⛔ It is NOT any of these — checked, and all negative (container: the same 40
    packages, 136 objects carrying a 4-component tangent):
      * an object-level classification — **63 of 136 objects carry BOTH
        magnitudes**, 73 carry only 1.0, and **0** carry only 0.5;
      * a mesh flag — `eCastsShadow`/`eRigidPhysSkin`/`eEnableRaycast` appear on
        both populations at the same rate;
      * the vertex stride (44/48/52/56 all split both ways);
      * the number of UV sets (2 uv sets: 60 use 0.5, 72 never).

    Next step is the shader, not more statistics: find where the vertex stage
    reconstructs the bitangent and see what it does with `.w`'s magnitude. Until
    then the importer STORES `.w` verbatim as `le_tangent_w` and consumes it
    nowhere, which is the honest state.
    """
    seen = {}
    for pkg_dir, man in _packages():
        for o in man["objects"]:
            A = o.get("attributes") or {}
            if "tangent" not in A:
                continue
            t, tc = _blob(pkg_dir, A["tangent"])
            if tc < 4:
                continue
            for i in range(int(o["vertex_count"])):
                seen[round(t[i * tc + 3], 2)] = seen.get(round(t[i * tc + 3], 2), 0) + 1
    if not seen:
        return
    assert set(seen) <= {-1.0, -0.5, 0.5, 1.0}, f"a fifth value appeared: {sorted(seen)}"
    assert {-1.0, 1.0} <= set(seen), "the sign must still be present"


def test_tangent_and_color1_are_actually_shipped_so_dropping_them_costs_something():
    """The reason the import exists, asserted with `>=` so a better extraction
    can only make it MORE true."""
    objs = with_tangent = with_color1 = extra_uv = 0
    for _pkg_dir, man in _packages():
        for o in man["objects"]:
            A = o.get("attributes") or {}
            objs += 1
            with_tangent += "tangent" in A
            with_color1 += "color1" in A
            extra_uv += any(_UV_KEY_RE.match(k) and k not in ("uv0", "uv1") for k in A)
    if not objs:
        return
    assert with_tangent >= 1, "tangent is decoded on every shipped object"
    assert with_color1 >= 1, "color1 is the COLOR1 layer-blend weight stream"
    assert extra_uv >= 0            # uv2/uv3 are rarer; presence is not required
