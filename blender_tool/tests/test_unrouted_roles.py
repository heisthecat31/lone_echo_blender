"""E3 — the `unrouted_roles` residue, and the `k_ambient_lightmaps` SRV.

Two fronts, both read-only on already-extracted data (no archive is opened here
and none may be — decoding one costs enough memory to matter).

**Task A.** docs/MATERIALS.md §7 left "62 role bindings across the
67 specs are `unrouted_roles` … a per-suffix breakdown was not done" open. The
breakdown says all 62 are `unknown_s{slot}` — an inputname-RESOLUTION gap, not a
routing-table gap — so the fix is to widen the NAME space (forward-hashed sampler
grid + the repo's existing generated authored-name table) while leaving routing
gated by the curated `CHANNEL_ROLE_SUFFIXES`.

**Task B.** docs/LIGHTING.md §4.3's third open item ("no lobe-basis
SRV name appears anywhere in the reference DXBC RDEF corpus").

Every literal below is also embedded as a module constant, so the assertions
still run on a clean checkout where `blender_tool/exports/` (gitignored) is absent.
Full write-up: docs/MATERIALS.md.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from le_mesh import materials as mat
from le_mesh.material_scalars import symbol64

ROOT = Path(__file__).resolve().parents[2]
EXPORTS = ROOT / "blender_tool" / "exports"
REBUILDS = ROOT / "generic_rebuilds"
SCRATCH = ROOT / "scratchpad"

# --- measured literals -------------------------------------------------------
# station_front `942c829457a04a62`, v2 sidecar (`export-validated`).
STATION_SPECS = 67
STATION_UNROUTED_BINDINGS = 62
STATION_UNROUTED_SPECS = 21
# every distinct unrouted role, and how many bindings carry it
STATION_UNROUTED_HIST = {
    "unknown_s11": 9, "unknown_s13": 7, "unknown_s14": 8, "unknown_s15": 11,
    "unknown_s16": 7, "unknown_s17": 9, "unknown_s18": 2, "unknown_s19": 1,
    "unknown_s20": 3, "unknown_s21": 4, "unknown_s22": 1,
}
# archive `0703fd2acd5803e9`, exports/fixtures_mat3, 100 unique materials
BRIDGE_UNROUTED_HIST = {"unknown_s23": 1}
# `stream-confirmed`, generic_rebuilds/combined_shader_scan.tsv:
# the ONLY uncracked inputname in the mesh corpus that is not a scanner artefact.
BRIDGE_S23_INPUTNAME = "602e82b525713c1c"

# named_scalars (Task A's smaller sibling)
STATION_SPECS_WITH_NAMED_SCALARS = 2
STATION_NAMED_SCALARS_RESOLVED = {"layer1_blend_mask_offset": 0.0,
                                  "layer0_emissive_intensity": 10.0}
BRIDGE_SPECS_WITH_NAMED_SCALARS = 65        # of 100 unique fixtures_mat3 materials

# --- Task B literals ---------------------------------------------------------
# generic_rebuilds/dxbc_summary.json (`stream-confirmed`)
RDEF_ARCHIVE = "0703fd2acd5803e9"
RDEF_SHADERSETS = 152
RDEF_DXBC_CONTAINERS = 304
# SRV census over dxbc_rdef_resources.tsv (`stream-confirmed`).
# Names carry a `_decl` suffix in the shipped RDEF.
RDEF_SRV_COUNTS = {
    "k_ambient_lightmap_ao0_decl": 86,      # SGAOTextures      -> present
    "k_ambient_lightmap_ao1_decl": 85,      # SGAOTextures      -> present
    "k_ambient_lightmaps_decl": 0,          # SGLightMapTextures -> ABSENT
    "k_dirlight_occlusion_map_decl": 0,     # SGLightMapTextures -> ABSENT
    "k_punctual_occlusion_map_decl": 0,     # SGLightMapTextures -> ABSENT
}
# blender_tool/exports/lightmap_probe/a8_*.json (`export-validated`).
# (rows, populated, lobe-basis, ao0, dlocclusion, poocclusion)
A8_BRIDGE_ROWS = (87, 36, 0, 36, 0, 0)
A8_STATION_ROWS = (26, 5, 1, 5, 1, 1)
LM_SENTINEL = "ffffffffffffffff"


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _station_specs():
    doc = _load_json(EXPORTS / "942c829457a04a62_materials.json")
    if not doc or doc.get("version") != 2:
        return []
    return [e["spec"] for e in doc["materials"]]


def _fixture_specs(sub="fixtures_mat3"):
    d = EXPORTS / sub
    if not d.is_dir():
        return []
    out = {}
    for pkg in sorted(d.iterdir()):
        mf = pkg / "manifest.json"
        if not mf.is_file():
            continue
        for spec in json.loads(mf.read_text(encoding="utf-8")).get("materials", []):
            out[spec.get("key")] = spec
    return list(out.values())


# ---------------------------------------------------------------------------
# 1. The forward-hashed grid is a preimage table, not a name invention
# ---------------------------------------------------------------------------

def test_every_generated_role_name_hashes_to_its_own_key():
    """The one invariant that makes forward-hashing safe.

    `symbol64(name) == key` by construction for every generated entry, exactly as
    `material_scalars.build_name_table()` already guarantees for materialprops.
    A generated entry can therefore only ever fire on a shipped hash that really
    IS that name; an entry the cook never emits is inert.
    """
    bad = [(h, n) for h, (n, _c) in mat.GENERATED_LAYER_INPUTNAME.items()
           if "%016x" % symbol64(n) != h]
    assert not bad, bad
    bad = [(h, n) for h, (n, _c) in mat.UNROUTABLE_INPUTNAME.items()
           if "%016x" % symbol64(n) != h]
    assert not bad, bad
    bad = [(h, n) for h, (n, _c) in mat.ROLE_BY_INPUTNAME.items()
           if "%016x" % symbol64(n) != h]
    assert not bad, bad


def test_generated_grid_shape_matches_the_source_layer_count():
    """The engine's ubermaterial instantiates `UberMaterialLayer` 4x."""
    assert mat.UBERMATERIAL_LAYER_COUNT == 4
    suffixes = mat.CONFIRMED_LAYER_SUFFIXES + mat.UNROUTABLE_LAYER_SUFFIXES
    assert len(mat.GENERATED_LAYER_INPUTNAME) == 4 * len(suffixes)
    # every confirmed suffix really is a cracked one, at some layer
    cracked = {mat.split_role(r)[1] for r in mat.INPUTNAME_ROLE_CONF}
    assert set(mat.CONFIRMED_LAYER_SUFFIXES) == cracked
    # ...and the grid covers the cells the crackers had actually seen
    for name in mat.INPUTNAME_ROLE_CONF:
        layer, suffix = mat.split_role(name)
        if suffix in cracked and layer < mat.UBERMATERIAL_LAYER_COUNT:
            assert "%016x" % symbol64(name) in mat.GENERATED_LAYER_INPUTNAME


def test_generated_names_do_not_collide_with_a_different_harvested_name():
    """A generated hash must never contradict a name harvested from real data."""
    hl = _load_json(ROOT / "hash_lookup.json") or {}
    by_hash = {k.lower().replace("0x", "").zfill(16): v for k, v in hl.items()}
    clashes = [(h, n, by_hash[h]) for h, (n, _c) in mat.ROLE_BY_INPUTNAME.items()
               if h in by_hash and by_hash[h] != n]
    assert not clashes, clashes


def test_the_curated_cracked_table_did_not_move():
    """Widening the NAME space must not widen `KNOWN_ROLES`.

    `KNOWN_ROLES` means "roles the router routes"; `test_material_routing`
    asserts a KNOWN_ROLE is never left unrouted, and `test_material_routing`
    pins `TRANSMISSION_ROLES` by exact list equality. Both would break if the
    generated grid leaked into `INPUTNAME_ROLE`.
    """
    assert len(mat.INPUTNAME_ROLE) == 25
    assert mat.TRANSMISSION_ROLES == ["layer0_opacity_map", "layer1_opacity_map"]
    assert "layer2_composite_diffuse" not in mat.KNOWN_ROLES
    assert "pom_height_map" not in mat.KNOWN_ROLES
    assert "layer0_composite_data0" not in mat.KNOWN_ROLES
    # the generated names are still resolvable, just not "routable by contract"
    assert mat.role_for_inputname("%016x" % symbol64("layer2_composite_diffuse")) \
        == "layer2_composite_diffuse"


# ---------------------------------------------------------------------------
# 2. Resolution order and the two named residue hashes
# ---------------------------------------------------------------------------

def test_role_for_inputname_provenance_order():
    cracked = "e348dd9cd3fdc817"                    # layer0_composite_diffuse
    assert mat.role_for_inputname(cracked, 10) == "layer0_composite_diffuse"
    assert mat.role_confidence("layer0_composite_diffuse") == "confirmed"

    gen = "%016x" % symbol64("layer3_composite_specular")
    assert mat.role_for_inputname(gen, 21) == "layer3_composite_specular"
    assert mat.role_confidence("layer3_composite_specular") == "forward-hashed"

    # caller-supplied harvested names still win over the wide generated table
    assert mat.role_for_inputname("dead0000beef0000", 7,
                                  {0xdead0000beef0000: "harvested_name"}) \
        == "harvested_name"

    # and a hash nobody can name keeps the SLOT, which is not a name
    assert mat.role_for_inputname("0123456789abcdef", 5) == "unknown_s5"
    assert mat.role_for_inputname("0123456789abcdef") == "unknown_sx"


def test_pom_height_map_is_resolved_and_deliberately_unrouted():
    """`602e82b525713c1c` — the fixtures corpus's only non-artefact residue.

    Cracked (and already locked by `test_transparency.test_pom_height_map_preimage`)
    but never wired into *inputname* resolution, which is why archive
    `0703fd2acd5803e9` still reported it as `unknown_s23`.
    """
    assert "%016x" % symbol64("pom_height_map") == BRIDGE_S23_INPUTNAME
    assert mat.role_for_inputname(BRIDGE_S23_INPUTNAME, 23) == "pom_height_map"
    note = mat.explain_unrouted("pom_height_map")
    assert note["classification"] == "deliberately unrouted"
    assert note["named"] is True
    assert "parallax" in note["reason"].lower()


def test_composite_data0_is_the_second_specular_lobe():
    """`a820334883657dcc` == symbol64("layer0_composite_data0").

    docs/MATERIALS.md §5 lists this hash as uncracked
    (4 rows on archive `4a405738bee7a74b`, slots 18/19/22). It is the 5th
    `compositesampler` of the engine's `UberMaterialLayer` and it drives
    `specintensity[1]` / `specalbedo[1]` — RAD's SECOND specular lobe
    (`shader-confirmed`, the same packing `composite_specular` uses for lobe
    [0]). Blender's Principled
    BSDF has one specular lobe, so this is deliberately unrouted, not missing.
    """
    assert "%016x" % symbol64("layer0_composite_data0") == "a820334883657dcc"
    assert mat.role_for_inputname("a820334883657dcc", 18) == "layer0_composite_data0"
    note = mat.explain_unrouted("layer0_composite_data0")
    assert note["classification"] == "deliberately unrouted"
    assert "SECOND SPECULAR LOBE" in note["reason"]
    # lobe [1]'s roughness rides on composite_components `.w`, which the router
    # already reads for lobe [0] from `.x` — so no channel is silently lost.
    ch = mat.classify_roles_layered({"layer0_composite_components": "t"},
                                    {"t": 71})["channels"]["roughness"]
    assert (ch["roughness_channel"], ch["roughness_is_sqrt"]) == ("R", True)


def test_scanner_artefact_hashes_never_acquire_a_name():
    """The two `inputname == own shaderset hash` rows stay dropped and unnamed."""
    for shd in ("05575a94091f1839", "80a6642707ce0367"):
        assert mat.is_scanner_artefact_row(shd, shd)
        assert shd not in mat.ROLE_BY_INPUTNAME
        assert mat.role_for_inputname(shd, 0) == "unknown_s0"


# ---------------------------------------------------------------------------
# 3. Routing: a widened name space must not widen what gets routed
# ---------------------------------------------------------------------------

def _quartet(layer: int, base: str) -> dict[str, str]:
    return {f"layer{layer}_composite_diffuse": base + "0",
            f"layer{layer}_composite_normals": base + "1",
            f"layer{layer}_composite_specular": base + "2",
            f"layer{layer}_composite_components": base + "3"}


def test_a_layer2_composite_quartet_now_routes_into_its_own_layer():
    """The shape station_front's unknown slots have (see docs/MATERIALS.md)."""
    roles = {**_quartet(0, "a"), **_quartet(2, "c"), "layer2_blend_mask": "m0"}
    dxgi = {"a0": 72, "a1": 83, "a2": 78, "a3": 71,
            "c0": 72, "c1": 83, "c2": 78, "c3": 71, "m0": 80}
    out = mat.classify_roles_layered(roles, dxgi)
    assert out["unrouted"] == []
    assert [e["index"] for e in out["layers"]] == [0, 2]
    l2 = {e["index"]: e for e in out["layers"]}[2]
    assert set(l2["channels"]) == {"base_color", "normal", "specular",
                                   "roughness", "blend_mask"}
    # merged view: the LOWEST layer still wins, so nothing existing moves
    assert out["channels"]["base_color"]["texture"] == "a0"
    assert out["channels"]["base_color"]["layer"] == 0


def test_a_widened_name_never_reaches_a_channel_on_its_own():
    """Routing stays gated by the curated suffix table."""
    roles = {"layer0_composite_data0": "t0", "pom_height_map": "t1",
             "layer1_detail_normal_map": "t2"}
    out = mat.classify_roles_layered(roles, {"t0": 80, "t1": 80, "t2": 83})
    # ⚠ t2 is BC5 and the DXGI fallback only ever promotes `unknown_s*`, so a
    # NAMED role with no channel rule must stay out of `channels` entirely.
    assert out["channels"] == {}
    assert out["unrouted"] == ["layer0_composite_data0", "layer1_detail_normal_map",
                               "pom_height_map"]
    kinds = {r: mat.explain_unrouted(r)["classification"] for r in out["unrouted"]}
    assert kinds["layer0_composite_data0"] == "deliberately unrouted"
    assert kinds["pom_height_map"] == "deliberately unrouted"
    assert kinds["layer1_detail_normal_map"] == "unresolved"


def test_unknown_slot_entries_are_classified_as_unresolved_not_as_roles():
    note = mat.explain_unrouted("unknown_s15")
    assert note["classification"] == "unresolved"
    assert note["named"] is False
    assert "slot" in note["reason"]


def test_a_beaten_suffix_is_classified_routable():
    """composite_diffuse outranks albedo_map on the same layer (:2240-2242)."""
    roles = {"layer0_composite_diffuse": "d0", "layer0_albedo_map": "a0"}
    out = mat.classify_roles_layered(roles, {"d0": 72, "a0": 72})
    assert out["channels"]["base_color"]["texture"] == "d0"
    assert out["unrouted"] == ["layer0_albedo_map"]
    assert mat.explain_unrouted("layer0_albedo_map")["classification"] == "routable"


def test_spec_carries_a_note_for_every_unrouted_role():
    spec = mat.build_material_spec(
        "k", role_textures={"layer0_composite_diffuse": "d0",
                            "layer0_composite_data0": "x0",
                            "unknown_s9": "u0"},
        dxgi_by_tex={"d0": 72, "x0": 80, "u0": 78})
    # `unknown_s9` is promoted by the DXGI fallback only when base_color is free;
    # here it is not, so it stays unrouted alongside composite_data0.
    assert set(spec["unrouted_role_notes"]) == set(spec["unrouted_roles"])
    assert spec["unrouted_role_notes"]["layer0_composite_data0"]["classification"] \
        == "deliberately unrouted"
    # the key is ALWAYS present so the level and .lemesh specs keep equal key sets
    empty = mat.build_material_spec("k2", role_textures={}, dxgi_by_tex={})
    assert empty["unrouted_role_notes"] == {}


# ---------------------------------------------------------------------------
# 4. The measured breakdown (real data; no-ops on a clean checkout)
# ---------------------------------------------------------------------------

def test_station_front_unrouted_bindings_are_all_unknown_slots():
    """The Task A headline: not one of the 62 is a cracked role name."""
    specs = _station_specs()
    if not specs:
        return
    assert len(specs) == STATION_SPECS
    hist: dict[str, int] = {}
    carriers = 0
    for spec in specs:
        ur = spec.get("unrouted_roles") or []
        carriers += bool(ur)
        for r in ur:
            hist[r] = hist.get(r, 0) + 1
    assert sum(hist.values()) == STATION_UNROUTED_BINDINGS
    assert carriers == STATION_UNROUTED_SPECS
    assert hist == STATION_UNROUTED_HIST
    assert all(r.startswith("unknown_s") for r in hist)
    # ...and none of them is a surviving scanner artefact: that misparse always
    # lands at slot 0 (entry_offset 768), and station_front's lowest slot is 11.
    assert min(int(r[len("unknown_s"):]) for r in hist) == 11


def test_fixture_corpus_residue_is_exactly_one_pom_height_map():
    specs = _fixture_specs()
    if not specs:
        return
    assert len(specs) == 100
    hist: dict[str, int] = {}
    for spec in specs:
        for r in spec.get("unrouted_roles") or []:
            hist[r] = hist.get(r, 0) + 1
    assert hist == BRIDGE_UNROUTED_HIST
    # ...and its inputname hash is now nameable, so a re-extract resolves it
    rows = REBUILDS / "combined_shader_scan.tsv"
    if rows.is_file():
        with rows.open(encoding="utf-8", newline="") as fh:
            slot23 = {r["inputname_hash"].lower() for r in csv.DictReader(fh, delimiter="\t")
                      if r["slot"] == "23"
                      and r["inputname_hash"].lower() not in mat.INPUTNAME_ROLE}
        assert BRIDGE_S23_INPUTNAME in slot23
    assert mat.role_for_inputname(BRIDGE_S23_INPUTNAME, 23) == "pom_height_map"


def test_named_scalars_absence_on_station_front_is_the_shipped_data():
    """D1's `unresolved`: "station_front simply authors no materialprops".

    Verified, not assumed. The material slice grows by 20 B per authored prop
    (`424 + 20n` for the shipped n_uv=1 / n_aux=2 family; the general formula is
    locked by `test_transparency.test_material_slice_size_arithmetic`), so a
    424-byte slice HAS no props to decode. The independent raw-slice probe TSVs
    agree with the sidecar on props-present/absent for every material both cover.
    """
    specs = _station_specs()
    if not specs:
        return
    with_props = [s for s in specs if s.get("named_scalars")]
    assert len(with_props) == STATION_SPECS_WITH_NAMED_SCALARS
    resolved = {}
    for s in with_props:
        resolved.update(s.get("named_scalars_resolved") or {})
    assert resolved == STATION_NAMED_SCALARS_RESOLVED
    # the slices WERE decoded: every spec carries the off-disk keys, and every
    # one of the 67 carries an authored (non-unit) bake colour.
    assert all("flags" in s and "named_scalars" in s for s in specs)
    assert not [s for s in specs if s["base_color_factor"] == [1.0, 1.0, 1.0, 1.0]]

    tsv = SCRATCH / "stationfront_materials.tsv"
    if tsv.is_file():
        by_hash = {s["material_hash"]: s for s in specs}
        checked = 0
        with tsv.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                spec = by_hash.get(row["hash"])
                if spec is None:
                    continue
                checked += 1
                assert int(row["slice_size"]) == 424
                assert not row["named_props"].strip()
                assert not spec["named_scalars"]
        assert checked == 3


def test_bridge_named_scalars_agree_with_the_independent_slice_probe():
    """The same cross-check where the base rate is high (65 of 100 specs)."""
    specs = _fixture_specs()
    tsv = SCRATCH / "bridge_materials.tsv"
    if not specs or not tsv.is_file():
        return
    assert len([s for s in specs if s.get("named_scalars")]) \
        == BRIDGE_SPECS_WITH_NAMED_SCALARS
    by_hash = {s["material_hash"]: s for s in specs}
    checked = 0
    with tsv.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            spec = by_hash.get(row["hash"])
            if spec is None:
                continue
            checked += 1
            assert bool(row["named_props"].strip()) == bool(spec["named_scalars"])
            assert int(row["slice_size"]) == 424 + 20 * len(spec["named_scalars"])
    assert checked == 19


# ---------------------------------------------------------------------------
# 5. Task B — what the reference RDEF corpus actually covers
# ---------------------------------------------------------------------------

def test_rdef_corpus_is_one_archive_and_it_is_not_the_lightmapped_one():
    summary = _load_json(REBUILDS / "dxbc_summary.json")
    if not summary:
        return
    assert summary["archive_hash"] == RDEF_ARCHIVE
    assert summary["shadersets_indexed"] == RDEF_SHADERSETS
    assert summary["dxbc_containers"] == RDEF_DXBC_CONTAINERS
    # ...and that archive is the BRIDGE, whose lightmap rows carry no lobe basis.
    assert RDEF_ARCHIVE != "942c829457a04a62"


def test_the_absent_srvs_are_exactly_the_sglightmaptextures_triple():
    """`k_ambient_lightmaps` is absent — with all three of its struct siblings.

    `SGLightMapTextures { k_ambient_lightmaps; k_dirlight_occlusion_map;
    k_punctual_occlusion_map; }` is declared as ONE `#if lightmap_` block
    (`name-confirmed`) and `SGAOTextures { ao0; ao1; }` under a different gate
    (`use_map_ao_`). The corpus shows the whole first group absent and the
    whole second group present — a compile-flag partition, not a decode hole.
    """
    tsv = REBUILDS / "dxbc_rdef_resources.tsv"
    if not tsv.is_file():
        return
    counts: dict[str, int] = {}
    names = set()
    with tsv.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            n = row["resource_name"]
            names.add(n)
            counts[n] = counts.get(n, 0) + 1
    for name, expected in RDEF_SRV_COUNTS.items():
        assert counts.get(name, 0) == expected, (name, counts.get(name, 0))
    # no lobe-basis SRV under ANY spelling, over all distinct resource names
    assert not [n for n in names
                if "lightmap" in n.lower() and "_ao" not in n.lower()]
    assert not [n for n in names if "occlusion" in n.lower()
                and n != "k_ambient_occlusion_decl"]


def test_a8_lightmap_rows_explain_the_absence_field_by_field():
    """The resource table and the shader reflection agree 5 fields out of 5."""
    bridge = _load_json(EXPORTS / "lightmap_probe" / "a8_bridge.json")
    if not bridge:
        return
    rows = pop = lobe = ao = dl = po = 0
    for res in bridge["lightmaps"]:
        for r in res.get("rows", []):
            rows += 1
            vals = [r["lightmapid"], r["ao0"], r["ao1"], r["dloc"], r["poocc"]]
            pop += any(v != LM_SENTINEL for v in vals)
            lobe += r["lightmapid"] != LM_SENTINEL
            ao += r["ao0"] != LM_SENTINEL
            dl += r["dloc"] != LM_SENTINEL
            po += r["poocc"] != LM_SENTINEL
    assert (rows, pop, lobe, ao, dl, po) == A8_BRIDGE_ROWS
    # the three SRVs absent from the RDEF corpus are exactly the three fields
    # that are the null sentinel on every bridge row.
    assert (lobe, dl, po) == (0, 0, 0)
    assert RDEF_SRV_COUNTS["k_ambient_lightmaps_decl"] == 0
    assert RDEF_SRV_COUNTS["k_ambient_lightmap_ao0_decl"] > 0 and ao > 0


def test_station_front_is_the_one_row_that_binds_the_whole_triple():
    station = _load_json(EXPORTS / "lightmap_probe" / "a8_station.json")
    if not station:
        return
    rows = pop = lobe = ao = dl = po = 0
    full = None
    for _name, table in station["lm_table"].items():
        for r in table:
            rows += 1
            pop += any(v != LM_SENTINEL for v in r)
            lobe += r[0] != LM_SENTINEL
            ao += r[1] != LM_SENTINEL
            dl += r[3] != LM_SENTINEL
            po += r[4] != LM_SENTINEL
            if all(v != LM_SENTINEL for v in r):
                full = r
    assert (rows, pop, lobe, ao, dl, po) == A8_STATION_ROWS
    # one complete row: lobe basis + AO pair + both occlusion maps
    assert full is not None and full[0] == "0178fa39b1b95d2f"
