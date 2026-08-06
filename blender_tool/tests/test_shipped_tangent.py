"""R1 — the shipped tangent basis: the pure decision layer, and the DATA.

`mesh_builder` has written `le_tangent` / `le_tangent_w` on 913 of 913 objects
since 2026-08-05 and nothing read them — three writers, zero readers — so every
normal map ran on Blender's UV-derived (mikktspace) tangent. This file pins the
two pure facts the wiring rests on and the corpus evidence for both:

  1. **`sign(w)` is the bitangent handedness.** Re-derived here from positions,
     `uv0` and the index buffer (Lengyel accumulation) and compared against the
     shipped `.w` — the same computation as the full corpus sweep, bounded so
     it runs in the suite.
  2. **`|w|` tags a duplicated BACK-FACE shell**, not a second handedness: the
     `|w| = 0.5` half of the vertex buffer is a vertex-for-vertex copy with
     identical position and `uv0` and exactly negated normal and tangent.

⛔ The node graph itself is NOT asserted here — `material_builder` needs `bpy`,
and a stub cannot tell you whether Blender accepted a link. That read-back is
`tests/blender_tangent_probe.py`, which runs inside Blender.

Skips are loud when `exports/` is absent (it is gitignored extracted game data).
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import types
from array import array
from pathlib import Path
from unittest import SkipTest

HERE = Path(__file__).resolve().parent
BLENDER_TOOL = HERE.parent
MB_PATH = BLENDER_TOOL / "addon" / "lone_echo_import" / "material_builder.py"
CHARS = BLENDER_TOOL / "exports" / "chars"

_MB = None
_CODE = {"float32": "f", "uint32": "I", "int32": "i"}


def _mb():
    """Load material_builder with a stub `bpy` — the house loader."""
    global _MB
    if _MB is not None:
        return _MB
    if "bpy" not in sys.modules:
        stub = types.ModuleType("bpy")
        stub.data = types.SimpleNamespace(materials=None, images=None)
        stub.context = types.SimpleNamespace(scene=None)
        stub.types = types.SimpleNamespace(Material=object)
        sys.modules["bpy"] = stub
    spec = importlib.util.spec_from_file_location("_le_material_builder", MB_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _MB = mod
    return mod


# ---------------------------------------------------------------------------
# 1. the opt
# ---------------------------------------------------------------------------

def test_shipped_tangent_defaults_on_and_is_switchable():
    mb = _mb()
    assert mb.shipped_tangent_enabled(None) is True
    assert mb.shipped_tangent_enabled({}) is True
    assert mb.shipped_tangent_enabled({"shipped_tangent": False}) is False
    assert mb.shipped_tangent_enabled({"shipped_tangent": True}) is True


def test_the_vector_mix_sockets_are_distinct_from_the_float_and_rgba_ones():
    """A `ShaderNodeMix` exposes "A" four times, once per data type, so the wiring
    goes by INDEX. Getting VECTOR's indices wrong would silently drive the FLOAT
    socket and leave `Normal` unconnected."""
    mb = _mb()
    assert mb.MIX_VECTOR_SOCKETS == (0, 4, 5, 1)
    assert len({mb.MIX_FLOAT_SOCKETS[1], mb.MIX_RGBA_SOCKETS[1],
                mb.MIX_VECTOR_SOCKETS[1]}) == 3


# ---------------------------------------------------------------------------
# 2. the four states
# ---------------------------------------------------------------------------

def test_all_four_tangent_w_states_are_understood():
    mb = _mb()
    for w, sign, shell in ((-1.0, -1.0, "front"), (-0.5, -1.0, "back"),
                           (0.5, 1.0, "back"), (1.0, 1.0, "front")):
        m = mb.tangent_w_meaning(w)
        assert m["known"] is True, w
        assert m["sign"] == sign, w
        assert m["shell"] == shell, w


def test_a_fifth_value_is_REFUSED_not_rounded():
    """⛔ The importer may not invent an interpretation. An unknown `.w` means the
    2-bit reading is wrong for that asset and `sign(w)` would be a guess."""
    mb = _mb()
    for bad in (0.0, 0.25, -0.75, 2.0, None, "x"):
        assert mb.tangent_w_meaning(bad)["known"] is False, bad


def test_the_state_table_is_the_one_the_stream_test_measured():
    mb = _mb()
    assert tuple(sorted(mb.TANGENT_W_STATES)) == (-1.0, -0.5, 0.5, 1.0)


# ---------------------------------------------------------------------------
# 3. the corpus evidence
# ---------------------------------------------------------------------------

def _packages(limit: int = 3):
    out = []
    if not CHARS.is_dir():
        return out
    for mf in sorted(CHARS.glob("*.lemesh/manifest.json")):
        try:
            man = json.loads(mf.read_text(encoding="utf-8"))
        except Exception:                                        # noqa: BLE001
            continue
        if man.get("format") == "lemesh" and man.get("objects"):
            out.append((mf.parent, man))
        if len(out) >= limit:
            break
    return out


def _blob(pd: Path, entry: dict):
    a = array(_CODE[entry["dtype"]])
    a.frombytes((pd / entry["blob"]).read_bytes())
    return a, entry.get("comps", 1)


def _object_with_tangent(man, max_verts=12000):
    for o in man["objects"]:
        A = o.get("attributes") or {}
        if {"tangent", "normal", "position", "uv0"} <= set(A) and o.get("index") \
                and int(o["vertex_count"]) <= max_verts \
                and A["tangent"].get("comps", 0) >= 4:
            return o
    return None


def test_sign_of_tangent_w_is_the_bitangent_handedness_on_the_real_bytes():
    """★ The measurement the shader's `B = cross(N, T) * sign(w)` rests on.

    Full sweep (`exports/chars`, 5 packages /
    36 objects): **397,082 of 397,082 vertices agree, 100.00 %**, and at that rate
    inside each of the four states separately. Bounded here to keep the suite
    fast; the threshold is 99.5 % so a genuinely different asset can be reported
    rather than crash the suite.
    """
    pkgs = _packages()
    if not pkgs:
        raise SkipTest("no character packages under exports/chars (gitignored)")
    agree = total = 0
    for pd, man in pkgs:
        o = _object_with_tangent(man)
        if o is None:
            continue
        A = o["attributes"]
        t, tc = _blob(pd, A["tangent"])
        n, nc = _blob(pd, A["normal"])
        p, pc = _blob(pd, A["position"])
        uv, uc = _blob(pd, A["uv0"])
        idx, _ = _blob(pd, o["index"])
        nv = int(o["vertex_count"])
        bt = [[0.0, 0.0, 0.0] for _ in range(nv)]
        for i in range(0, len(idx) - 2, 3):
            a_, b_, c_ = int(idx[i]), int(idx[i + 1]), int(idx[i + 2])
            if max(a_, b_, c_) >= nv:
                continue
            e1 = [p[b_ * pc + k] - p[a_ * pc + k] for k in range(3)]
            e2 = [p[c_ * pc + k] - p[a_ * pc + k] for k in range(3)]
            du1, dv1 = uv[b_ * uc] - uv[a_ * uc], uv[b_ * uc + 1] - uv[a_ * uc + 1]
            du2, dv2 = uv[c_ * uc] - uv[a_ * uc], uv[c_ * uc + 1] - uv[a_ * uc + 1]
            det = du1 * dv2 - du2 * dv1
            if abs(det) < 1e-12:
                continue
            r = 1.0 / det
            bi = [(-du2 * e1[k] + du1 * e2[k]) * r for k in range(3)]
            for v in (a_, b_, c_):
                for k in range(3):
                    bt[v][k] += bi[k]
        for v in range(nv):
            tx, ty, tz, w = (t[v * tc], t[v * tc + 1], t[v * tc + 2], t[v * tc + 3])
            nx, ny, nz = n[v * nc], n[v * nc + 1], n[v * nc + 2]
            bx, by, bz = bt[v]
            d = ((ny * tz - nz * ty) * bx + (nz * tx - nx * tz) * by
                 + (nx * ty - ny * tx) * bz)
            if abs(d) < 1e-9 or math.sqrt(bx * bx + by * by + bz * bz) < 1e-9:
                continue
            total += 1
            agree += (d > 0) == (w > 0)
    if not total:
        raise SkipTest("no object with tangent + uv0 + indices small enough")
    frac = agree / total
    print(f"    [R1] sign(w) == UV-derived handedness on {agree}/{total} "
          f"vertices ({100.0 * frac:.2f} %)")
    assert frac >= 0.995, (
        f"sign(w) is NOT the bitangent handedness on this data: only "
        f"{100.0 * frac:.2f} % agree — the shader's `B = cross(N, T) * sign(w)` "
        f"would be wrong here")


def test_half_magnitude_w_marks_a_duplicated_BACK_FACE_shell():
    """★ What the MAGNITUDE selects — the question `test_vertex_streams.py` left
    open, answered from the bytes.

    Every `|w| = 0.5` vertex has a position-identical `|w| = 1.0` partner whose
    NORMAL is exactly negated: the mesh ships its own double-sided geometry and
    `|w|` tags which shell a vertex belongs to. Full sweep over `exports/chars`
    (63 objects with a 4-component tangent): 26 carry both magnitudes in exactly
    equal counts, 37 carry only 1.0, **0 carry only 0.5**; pairing 109,400/109,400
    = 100.00 %, negated normal 109,317/109,400 = 99.92 %.

    ⛔ TWO THINGS THAT ARE NOT THE LAW, both checked and both false:
      * the tangents are NOT simply negated — only 65.67 % of pairs are, because
        the back shell carries its own frame. That is exactly why the shader
        reads `sign(w)` per vertex instead of deriving a shell-wide flip;
      * the layout is NOT always fronts-then-backs — 25 of 26 objects order it
        that way and `2fd6839161785e9c_3a80cdb80b7e60c0/obj001` interleaves, so
        an implementation that split the buffer in half would be wrong on it.
    """
    pkgs = _packages(limit=5)
    if not pkgs:
        raise SkipTest("no character packages under exports/chars (gitignored)")
    checked = pairs = paired = negn = 0
    for pd, man in pkgs:
        for o in man["objects"]:
            A = o.get("attributes") or {}
            if "tangent" not in A or A["tangent"].get("comps", 0) < 4:
                continue
            if "normal" not in A or "position" not in A:
                continue
            nv = int(o["vertex_count"])
            if nv > 14000:
                continue
            t, tc = _blob(pd, A["tangent"])
            mags = {round(abs(t[v * tc + 3]), 2) for v in range(nv)}
            if mags != {0.5, 1.0}:
                continue
            n, nc = _blob(pd, A["normal"])
            p, pc = _blob(pd, A["position"])
            front, back = [], []
            for v in range(nv):
                (front if abs(t[v * tc + 3]) > 0.75 else back).append(v)
            assert len(front) == len(back), (
                f"{pd.name}/{o['name']}: the two shells differ in size "
                f"({len(front)} front, {len(back)} back)")
            by_pos: dict[tuple, int] = {}
            for v in front:
                by_pos.setdefault(
                    tuple(round(p[v * pc + k], 5) for k in range(3)), v)
            for v in back:
                pairs += 1
                u = by_pos.get(tuple(round(p[v * pc + k], 5) for k in range(3)))
                if u is None:
                    continue
                paired += 1
                negn += all(abs(n[v * nc + k] + n[u * nc + k]) < 2e-3
                            for k in range(3))
            checked += 1
            if checked >= 3:
                break
        if checked >= 3:
            break
    if not pairs:
        raise SkipTest("no object on disk carries both |w| magnitudes")
    print(f"    [R1] {checked} object(s): {paired}/{pairs} back-shell vertices "
          f"pair by position, {negn}/{paired} with a negated normal")
    assert paired == pairs, (
        f"{pairs - paired} `|w|=0.5` vertex(es) have no position-identical "
        f"`|w|=1.0` partner — `|w|` is not a front/back shell tag on this data")
    assert negn / paired >= 0.99, (
        f"only {100.0 * negn / paired:.2f} % of the pairs have negated normals")


def test_no_object_ships_a_back_shell_without_a_front():
    """The prediction that made the shell reading falsifiable: `|w| = 0.5` alone
    would be a back face with nothing in front of it. 0 of 63 objects."""
    pkgs = _packages(limit=5)
    if not pkgs:
        raise SkipTest("no character packages under exports/chars (gitignored)")
    only_back, both, only_front = [], 0, 0
    for pd, man in pkgs:
        for o in man["objects"]:
            A = o.get("attributes") or {}
            if "tangent" not in A or A["tangent"].get("comps", 0) < 4:
                continue
            t, tc = _blob(pd, A["tangent"])
            mags = {round(abs(t[v * tc + 3]), 2)
                    for v in range(int(o["vertex_count"]))}
            if mags == {0.5}:
                only_back.append(f"{pd.name}/{o['name']}")
            elif mags == {1.0}:
                only_front += 1
            else:
                both += 1
    print(f"    [R1] shells: {only_front} front-only, {both} both, "
          f"{len(only_back)} back-only")
    assert not only_back, f"a back shell with no front: {only_back}"
