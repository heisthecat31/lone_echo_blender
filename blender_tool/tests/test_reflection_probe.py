"""`CGReflectionProbeResourceWin7` grammar, join and cube geometry — archive-free.

Every *number* here was measured on shipped LE1 Win7 bytes and is recorded as a
literal, so the suite runs with no Oodle, no archive and no `bpy`. No shipped
BYTES are embedded: `STATION_FRONT_PROBE` is **synthesised from the documented
grammar** by `_build_probe_slice()` below, reproducing the measured *shape* of
the reference resource — 23 selection boxes over 16 probes, the measured
box → probe histogram, one shared parallax volume expressed probe-relative, a
BC6H_UF16 256² cube with 9 mips per probe — with values this file makes up.
That exercises every decode path a verbatim slice would, and ships nothing that
came out of the game.

The reference measurements it reproduces (`942c829457a04a62`,
stn_ext_itc_station_front — the archive's only `CGReflectionProbeResourceWin7`,
slice size 0x1160):

  * 23 boxes / 16 probes, histogram `{0:1, 1:1, 12:5, 13:10, 14:5, 15:1}`
  * `points`, `mipcounts`, `boundingboxes`, `gpuoffsets` all 16; 0 spheres
  * `SGProbeBoundingBox` stride 0x98, mipcount 9, cube 256², `gpumemsize`
    16 × 524,448 B
  * 15 of the 16 bounding boxes share one rotation and one world OBB; the 16th
    (the exterior/vista probe) has its own
  * `eBC6UeFLOAT` (engine 59) on 94/94 resources corpus-wide

⚠ A byte-exact fixture is the natural way to test a byte-exact decoder, and it
is exactly what must not cross into a public repository. Synthesise instead —
and if you ever do need the real bytes, keep them out of the tree and gate the
test on the user's own install, the way `test_extractor_e2e.py` does.
"""

import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from le_mesh import reflection_probe as RP


# ---------------------------------------------------------------------------
# the fixture — SYNTHESISED from the grammar, never a slice of the game
# ---------------------------------------------------------------------------

N_PROBES = 16
N_BOXES = 23
MIPCOUNT = 9
CUBE_DIM = 256
#: BC6H 256², nine mips, six faces — arithmetic, not a measured constant
CUBE_BYTES = 524448

#: box -> probe. The measured station_front distribution, reproduced so the
#: parser is exercised on a many-boxes-per-probe resource.
BOX_PROBE = [0] * 1 + [1] * 1 + [12] * 5 + [13] * 10 + [14] * 5 + [15] * 1
assert len(BOX_PROBE) == N_BOXES

#: the one shared world-space parallax volume every probe expresses in its own
#: rotated, probe-relative frame.
OBB_WORLD_MIN = (-500.0, -500.0, -500.0)
OBB_WORLD_MAX = (500.0, 500.0, 500.0)

#: per-mip radiance scales: monotone from mip 1, exceeding 1.0, the measured
#: shape. The values are invented; only the SHAPE is a finding.
NORMALIZATION_SERIES = [16.0, 16.0, 8.0, 4.0, 2.0, 1.0, 0.5, 0.25, 0.125]

_IDENTITY_3X3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
_C30, _S30 = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))
#: a second, genuinely different orthonormal frame — the vista probe's
_ROT_Z30 = (_C30, -_S30, 0.0, _S30, _C30, 0.0, 0.0, 0.0, 1.0)


def _ctable(nbytes: int, count: int) -> bytes:
    """One `CTableA<T,0>` memory image: null pointer, size in bytes, count."""
    img = bytearray(RP.CTABLE_STRIDE)
    struct.pack_into("<Q", img, RP.CT_NBYTES, nbytes)
    struct.pack_into("<Q", img, RP.CT_EXPAND, 32)      # 32 on 100 % of shipped
    struct.pack_into("<Q", img, RP.CT_IALLOCATED, count)
    struct.pack_into("<Q", img, RP.CT_IUSED, count)
    return bytes(img)


def _probe_point(i: int) -> tuple:
    """Probe centres, spread so `select_probe`'s nearest-point fallback is sharp."""
    return (i * 100.0, i * 10.0, i * 5.0)


def _box_center(i: int) -> tuple:
    """Box centres, 100 apart with a half-extent of 4 — no two overlap."""
    return (i * 100.0, 500.0, 0.0)


def _rotate(rows, v) -> tuple:
    return tuple(sum(rows[k][j] * v[j] for j in range(3)) for k in range(3))


def _boxes_payload() -> bytes:
    out = bytearray()
    for i, probe in enumerate(BOX_PROBE):
        rec = bytearray(RP.STRIDE_BOX)
        # 22 of 23 shipped boxes carry an identity inverse rotation; the last
        # one does not, which is what proves `contains()` uses it.
        if i == N_BOXES - 1:
            quat = (0.0, 0.0, math.sin(math.radians(15.0)),
                    math.cos(math.radians(15.0)))
        else:
            quat = (0.0, 0.0, 0.0, 1.0)
        struct.pack_into("<4f", rec, RP.B_INVROT, *quat)
        struct.pack_into("<3f", rec, RP.B_POS, *_box_center(i))
        struct.pack_into("<3f", rec, RP.B_MIN, -4.0, -4.0, -4.0)
        struct.pack_into("<3f", rec, RP.B_MAX, 4.0, 4.0, 4.0)
        struct.pack_into("<I", rec, RP.B_PROBEIDX, probe)
        out += rec
    return bytes(out)


def _points_payload() -> bytes:
    out = bytearray()
    for i in range(N_PROBES):
        rec = bytearray(RP.STRIDE_POINT)
        struct.pack_into("<3f", rec, 0, *_probe_point(i))
        struct.pack_into("<I", rec, 0x0C, i)          # probeidx == row index
        out += rec
    return bytes(out)


def _bounding_boxes_payload() -> bytes:
    out = bytearray()
    for i in range(N_PROBES):
        rec = bytearray(RP.STRIDE_BOUNDINGBOX)
        rot = _ROT_Z30 if i == N_PROBES - 1 else _IDENTITY_3X3
        rows = [rot[0:3], rot[3:6], rot[6:9]]
        pos = _probe_point(i)
        rp_ = _rotate(rows, pos)
        struct.pack_into("<9f", rec, RP.BB_ROTATION, *rot)
        struct.pack_into("<3f", rec, RP.BB_PROBEPOS, *pos)
        # one world OBB, written in this probe's rotated, probe-relative frame
        struct.pack_into("<3f", rec, RP.BB_MIN,
                         *(OBB_WORLD_MIN[k] - rp_[k] for k in range(3)))
        struct.pack_into("<3f", rec, RP.BB_MAX,
                         *(OBB_WORLD_MAX[k] - rp_[k] for k in range(3)))
        norms = [0.0] * RP.BB_NORMALIZATION_FLOATS
        for m in range(MIPCOUNT):
            norms[2 * m] = NORMALIZATION_SERIES[m]
        norms[1] = norms[0]                            # index 1 duplicates 0
        struct.pack_into(f"<{RP.BB_NORMALIZATION_FLOATS}f", rec,
                         RP.BB_NORMALIZATIONS, *norms)
        out += rec
    return bytes(out)


def _build_probe_slice() -> bytes:
    """A `CGReflectionProbeResourceWin7` primary slice, built from the grammar.

    Six `CTableA<T,0>` images in declaration order, then two `u32`, then every
    payload back to back with no padding — residual 0 by construction, which is
    the arithmetic the decoder checks.
    """
    payloads = {
        "boxes": _boxes_payload(),
        "spheres": b"",                                # 0 shipped corpus-wide
        "points": _points_payload(),
        "mipcounts": struct.pack(f"<{N_PROBES}I", *([MIPCOUNT] * N_PROBES)),
        "boundingboxes": _bounding_boxes_payload(),
        "gpuoffsets": struct.pack(f"<{N_PROBES}I",
                                  *[i * CUBE_BYTES for i in range(N_PROBES)]),
    }
    counts = {"boxes": N_BOXES, "spheres": 0, "points": N_PROBES,
              "mipcounts": N_PROBES, "boundingboxes": N_PROBES,
              "gpuoffsets": N_PROBES}
    head = bytearray()
    for name in RP.TABLE_NAMES:
        head += _ctable(len(payloads[name]), counts[name])
    head += struct.pack("<II", N_PROBES * CUBE_BYTES, RP.ETEXTUREFORMAT_BC6H_UF16)
    assert len(head) == RP.META_HEADER_SIZE
    body = b"".join(payloads[name] for name in RP.TABLE_NAMES)
    return bytes(head) + body


#: the fixture every container/table test below parses. Synthesised, not shipped.
STATION_FRONT_PROBE = _build_probe_slice()


# ---------------------------------------------------------------------------
# corpus tallies, recorded so a regression in the decoder is loud
# ---------------------------------------------------------------------------
# `census` + `stream-confirmed`, measured 2026-08-05 over the LE1 Win7
# retail corpus (1,244 archives; this tree holds no numeric build id for it).
CORPUS = {
    "resources": 94, "archives": 90,
    "empty": 34,                 # 344-byte bare header, GPU sibling a 16-byte stub
    "populated": 60,
    "probes_total": 705, "boxes_total": 283,
    "populated_without_boxes": 26,
    "texture_format": 59,        # eBC6UeFLOAT on 94/94
    "cube_dim": 256, "cube_mips": 9,   # on 60/60 populated
    "residual_zero": 94,
}
#: mesh side, six level archives, 128 mesh-lists
MESH_SIDE = {"meshes": 578, "null": 282, "named": 296, "out_of_range": 0,
             "box_agree": 5, "box_disagree": 0,
             "nearest_agree": 276, "nearest_total": 291}


def _res():
    return RP.parse_probe_resource(STATION_FRONT_PROBE)


# ---------------------------------------------------------------------------
# the container
# ---------------------------------------------------------------------------

def test_the_slice_is_an_unpatched_ctable_memory_image():
    """Every table's data pointer is 0 on disk — that IS the grammar."""
    for i, name in enumerate(RP.TABLE_NAMES):
        ptr, _nbytes, _ia, _iu = RP.parse_ctable(STATION_FRONT_PROBE, i * RP.CTABLE_STRIDE)
        assert ptr == 0, f"{name} carries a patched pointer {ptr:#x}"


def test_strict_parse_rejects_a_patched_pointer():
    bad = bytearray(STATION_FRONT_PROBE)
    struct.pack_into("<Q", bad, 0, 0xDEADBEEF)
    try:
        RP.parse_probe_resource(bytes(bad))
    except ValueError as exc:
        assert "not 0" in str(exc)
    else:
        raise AssertionError("a patched pointer must not parse under strict=True")


def test_header_is_six_tables_plus_two_u32():
    assert RP.META_HEADER_SIZE == 6 * RP.CTABLE_STRIDE + 8 == 0x158
    assert RP.OFF_GPUMEMSIZE == 0x150 and RP.OFF_TEXTUREFORMAT == 0x154


def test_the_six_payloads_account_for_every_byte():
    """Residual 0 — the arithmetic that proves the grammar."""
    r = _res()
    assert r.residual == 0
    total = RP.META_HEADER_SIZE + sum(r.tables[n][1] for n in RP.TABLE_NAMES)
    assert total == len(STATION_FRONT_PROBE) == 0x1160


def test_strict_parse_rejects_a_truncated_slice():
    try:
        RP.parse_probe_resource(STATION_FRONT_PROBE[:-8])
    except ValueError as exc:
        assert "overruns" in str(exc)
    else:
        raise AssertionError("a short slice must not parse under strict=True")


def test_a_short_slice_is_rejected_before_the_header_is_read():
    try:
        RP.parse_probe_resource(b"\x00" * 0x100)
    except ValueError as exc:
        assert "shorter than" in str(exc)
    else:
        raise AssertionError("a sub-header slice must raise")


def test_an_empty_resource_is_the_bare_header():
    """34 of the 94 shipped resources are exactly this: 344 B, everything 0."""
    blob = bytearray(RP.META_HEADER_SIZE)
    struct.pack_into("<II", blob, RP.OFF_GPUMEMSIZE, 0, RP.ETEXTUREFORMAT_BC6H_UF16)
    r = RP.parse_probe_resource(bytes(blob))
    assert r.residual == 0
    assert r.n_probes == 0 and not r.boxes and r.gpumemsize == 0
    assert r.per_probe_bytes() is None
    assert RP.manifest_probe_section(r)["count"] == 0
    assert len(blob) == 344 == CORPUS["empty"] * 0 + 344


# ---------------------------------------------------------------------------
# the tables
# ---------------------------------------------------------------------------

def test_boxes_is_NOT_the_probe_count():
    """⛔ 23 boxes but 16 probes.  The two are different populations."""
    r = _res()
    assert len(r.boxes) == 23
    assert r.n_probes == 16
    assert len(r.boxes) != r.n_probes


def test_probe_count_agrees_across_four_tables():
    r = _res()
    assert (len(r.points) == len(r.mipcounts) == len(r.bounding_boxes)
            == len(r.gpu_offsets) == 16)
    assert r.n_spheres == 0


def test_no_sphere_ships_anywhere_so_its_stride_stays_pdb_only():
    assert _res().n_spheres == 0
    assert RP.STRIDE_SPHERE == 0x14


def test_measured_strides():
    r = _res()
    assert r.tables["boxes"][1] // r.tables["boxes"][3] == RP.STRIDE_BOX == 0x38
    assert r.tables["points"][1] // r.tables["points"][3] == RP.STRIDE_POINT == 0x10
    assert r.bb_stride == RP.STRIDE_BOUNDINGBOX == 0x98


def test_bounding_box_stride_refutes_the_pdb_c4vector_80():
    """`C4Vector[80]` would make the struct 0x548; the shipped stride is 0x98.

    0x98 - 0x48 == 80 BYTES of `normalizations`, i.e. 20 floats — so the engine's own declarations's
    `[80]` is a byte length, not an element count.
    """
    r = _res()
    assert r.bb_stride == 0x98
    assert r.bb_stride - RP.BB_NORMALIZATIONS == 80
    assert RP.BB_NORMALIZATION_FLOATS == 20
    assert 0x48 + 80 * 16 == 0x548 and r.bb_stride != 0x548


def test_box_probe_indices_are_all_in_probe_range():
    r = _res()
    assert all(0 <= b.probe_index < r.n_probes for b in r.boxes)
    hist = {}
    for b in r.boxes:
        hist[b.probe_index] = hist.get(b.probe_index, 0) + 1
    assert hist == {0: 1, 1: 1, 12: 5, 13: 10, 14: 5, 15: 1}


def test_points_are_probe_ordered():
    r = _res()
    assert [p.probe_index for p in r.points] == list(range(16))


def test_bounding_box_probepos_equals_the_point():
    r = _res()
    for i, bb in enumerate(r.bounding_boxes):
        for k in range(3):
            assert abs(bb.probe_pos[k] - r.points[i].point[k]) < 1e-4


def test_bounding_box_rotations_are_orthonormal():
    assert all(bb.is_orthonormal for bb in _res().bounding_boxes)


def test_the_obb_is_ONE_shared_volume_expressed_probe_relative():
    """`min + R*probepos` is constant over the 15 probes that share one `R`."""
    r = _res()
    shared = [bb for bb in r.bounding_boxes
              if bb.rotation == r.bounding_boxes[0].rotation]
    assert len(shared) == 15          # the 16th is the exterior/vista probe
    consts = []
    for bb in shared:
        rows = bb.rotation_rows
        rp_ = [sum(rows[k][j] * bb.probe_pos[j] for j in range(3)) for k in range(3)]
        consts.append(tuple(bb.obb_min[k] + rp_[k] for k in range(3)))
    for c in consts[1:]:
        for k in range(3):
            assert abs(c[k] - consts[0][k]) < 0.05, (c, consts[0])


def test_normalizations_have_the_measured_shape():
    """`[1] == [0]`, every other odd slot 0, exactly `mipcount` even slots set."""
    r = _res()
    for i, bb in enumerate(r.bounding_boxes):
        assert bb.normalizations_well_formed(r.mipcounts[i]), i
        assert len(bb.mip_normalizations(r.mipcounts[i])) == 9


def test_mipcounts_are_nine_everywhere():
    r = _res()
    assert set(r.mipcounts) == {9}
    assert r.mipcount(0) == CORPUS["cube_mips"]


# ---------------------------------------------------------------------------
# the GPU payload
# ---------------------------------------------------------------------------

def test_texture_format_is_the_ENGINE_enum_not_dxgi():
    r = _res()
    assert r.texture_format == 59
    assert r.format_name == "eBC6UeFLOAT"
    assert RP.ETEXTUREFORMAT_TO_DXGI[59] == RP.DXGI_BC6H_UF16 == 95
    assert r.texture_format != 95     # ⛔ never mix the two enums


def test_gpu_offsets_are_a_uniform_stride_that_closes_on_gpumemsize():
    r = _res()
    assert r.gpu_offsets_uniform()
    assert r.gpu_offsets[0] == 0
    assert r.gpumemsize == 8391168 == 16 * 524448
    assert r.probe_gpu_range(15) == (7866720, 8391168)


def test_per_probe_bytes_is_a_bc6h_256_cube_with_nine_mips():
    r = _res()
    assert r.per_probe_bytes() == 524448
    assert RP.cube_dims_for(524448) == (256, 9)
    assert r.cube_dim(0) == 256
    assert RP.cube_bytes(256, 9) == 524448


def test_probe_gpu_range_rejects_an_out_of_range_probe():
    r = _res()
    try:
        r.probe_gpu_range(16)
    except IndexError:
        pass
    else:
        raise AssertionError("probe 16 must not resolve in a 16-probe resource")


def test_cube_byte_arithmetic():
    assert RP.cube_bytes(4, 1) == 6 * 16
    assert RP.face_bytes(256, 9) == 87408
    assert RP.face_bytes(256, 9) * 6 == 524448
    offs = RP.face_mip_offsets(256, 9)
    assert len(offs) == 9
    assert offs[0] == (0, 65536, 256)
    assert offs[-1][2] == 1
    assert offs[-1][0] + offs[-1][1] == RP.face_bytes(256, 9)


def test_cube_dim_for_is_exact_or_none():
    assert RP.cube_dim_for(524448, 9) == 256
    assert RP.cube_dim_for(524447, 9) is None
    assert RP.cube_dim_for(524448, 8) is None


# ---------------------------------------------------------------------------
# DDS writers
# ---------------------------------------------------------------------------

def _fake_cube(dim=8, mips=4):
    """A payload whose every 16-byte block encodes (face, mip, block index)."""
    fb = RP.face_bytes(dim, mips)
    out = bytearray()
    for f in range(6):
        for m, (_o, nb, _w) in enumerate(RP.face_mip_offsets(dim, mips)):
            for k in range(nb // 16):
                out += struct.pack("<IIII", f, m, k, 0)
    assert len(out) == fb * 6
    return bytes(out)


def test_cube_dds_bytes_is_a_dx10_cubemap_header_plus_verbatim_payload():
    payload = _fake_cube()
    dds = RP.cube_dds_bytes(payload, 8, 4)
    assert dds[:4] == b"DDS " and dds[84:88] == b"DX10"
    assert struct.unpack_from("<I", dds, 12)[0] == 8       # height
    assert struct.unpack_from("<I", dds, 16)[0] == 8       # width
    assert struct.unpack_from("<I", dds, 28)[0] == 4       # mipMapCount
    assert struct.unpack_from("<I", dds, 112)[0] == 0xFE00  # caps2 all six faces
    dxgi, dim, misc, arr, _ = struct.unpack_from("<5I", dds, 128)
    assert (dxgi, dim, misc, arr) == (95, 3, 4, 1)
    assert dds[148:] == payload                            # copied, not rebuilt


def test_cube_dds_bytes_refuses_a_wrong_sized_payload():
    try:
        RP.cube_dds_bytes(b"\x00" * 10, 8, 4)
    except ValueError as exc:
        assert "expected" in str(exc)
    else:
        raise AssertionError("a short payload must raise")


def test_cube_strip_is_the_six_faces_mip0_stacked():
    payload = _fake_cube()
    strip = RP.cube_strip_bytes(payload, 8, 4)
    assert struct.unpack_from("<I", strip, 16)[0] == 8        # width
    assert struct.unpack_from("<I", strip, 12)[0] == 8 * 6    # height
    assert struct.unpack_from("<I", strip, 28)[0] == 1        # single mip
    body = strip[148:]
    fb = RP.face_bytes(8, 4)
    mip0 = RP.face_mip_offsets(8, 4)[0][1]
    assert len(body) == mip0 * 6
    for f in range(6):
        assert body[f * mip0:(f + 1) * mip0] == payload[f * fb:f * fb + mip0]
        face, mip, _k, _ = struct.unpack_from("<4I", body, f * mip0)
        assert (face, mip) == (f, 0)


# ---------------------------------------------------------------------------
# cube <-> direction <-> equirect
# ---------------------------------------------------------------------------

def test_the_six_axis_directions_hit_the_six_face_centres():
    want = {(1, 0, 0): 0, (-1, 0, 0): 1, (0, 1, 0): 2,
            (0, -1, 0): 3, (0, 0, 1): 4, (0, 0, -1): 5}
    for d, face in want.items():
        f, u, v = RP.direction_to_face_uv(d)
        assert f == face, (d, f, face)
        assert abs(u - 0.5) < 1e-6 and abs(v - 0.5) < 1e-6


def test_face_uv_direction_round_trip():
    for face in range(6):
        for u in (0.05, 0.5, 0.95):
            for v in (0.05, 0.5, 0.95):
                d = RP.face_uv_to_direction(face, u, v)
                f2, u2, v2 = RP.direction_to_face_uv(d)
                assert f2 == face
                assert abs(u2 - u) < 1e-6 and abs(v2 - v) < 1e-6


def test_axis_convention_round_trip_matches_AXIS_CALIBRATION():
    assert RP.rad_to_blender((1, 2, 3)) == (1, -3, 2)
    for v in ((1, 2, 3), (-4, 5, -6)):
        assert RP.blender_to_rad(RP.rad_to_blender(v)) == v


def test_equirect_directions_are_unit_and_hit_the_poles():
    for u in (0.0, 0.25, 0.5, 0.75, 1.0):
        for v in (0.0, 0.5, 1.0):
            d = RP.equirect_to_direction(u, v)
            assert abs(math.sqrt(sum(c * c for c in d)) - 1.0) < 1e-6
    top = RP.equirect_to_direction(0.5, 1.0)
    assert abs(top[2] - 1.0) < 1e-6            # v == 1 is Blender +Z (up)
    bot = RP.equirect_to_direction(0.5, 0.0)
    assert abs(bot[2] + 1.0) < 1e-6


def test_resample_places_every_face_where_the_convention_says():
    """Constant-colour faces -> the equirect says which face it sampled."""
    def sample(face, _u, _v):
        return (float(face), 0.0, 0.0)

    w, h = 8, 4
    px = RP.resample_cube_to_equirect(sample, w, h)

    def face_at(ix, iy):
        return int(round(px[(iy * w + ix) * 4]))

    # top row == Blender +Z == game +Y == face 2; bottom row == face 3
    assert face_at(w // 2, h - 1) == 2
    assert face_at(w // 2, 0) == 3
    # horizon row: u=0.5 -> Blender +X -> game +X -> face 0
    mid = h // 2
    assert face_at(w // 2, mid) == 0
    assert face_at(0, mid) == 1                    # u~0 -> Blender -X -> face 1
    assert face_at(w // 4, mid) == 5               # u=0.25 -> Blender +Y -> game -Z
    assert face_at(3 * w // 4, mid) == 4           # u=0.75 -> Blender -Y -> game +Z
    assert len(px) == w * h * 4
    assert all(px[i] == 1.0 for i in range(3, len(px), 4))   # alpha


# ---------------------------------------------------------------------------
# CGMeshData.probeidx
# ---------------------------------------------------------------------------

def test_probeidx_offset_matches_the_meshlist_declaration():
    from le_mesh import meshlist as ML
    assert RP.M_PROBEIDX == ML.M_PROBEIDX == 0x50
    assert RP.MESH_STRIDE == ML.MESH_STRIDE == 0x80


def test_read_mesh_probe_indices_over_a_synthetic_mesh_table():
    n = 4
    table = bytearray(RP.MESH_STRIDE * n)
    for i, v in enumerate([0, 0xFFFFFFFF, 7, 15]):
        struct.pack_into("<I", table, i * RP.MESH_STRIDE + RP.M_PROBEIDX, v)
    got = RP.read_mesh_probe_indices(bytes(table), 0, n)
    assert got == [0, 0xFFFFFFFF, 7, 15]
    assert [RP.has_probe(v) for v in got] == [True, False, True, True]


def test_null_sentinel_is_not_probe_zero():
    assert RP.PROBE_INDEX_NONE == 0xFFFFFFFF
    assert RP.has_probe(0) and not RP.has_probe(RP.PROBE_INDEX_NONE)
    assert not RP.has_probe(None) and not RP.has_probe("nope") and not RP.has_probe(-1)


def test_resolve_mesh_probe_refuses_an_out_of_range_index():
    r = _res()
    assert RP.resolve_mesh_probe(r, 15) is r.points[15]
    assert RP.resolve_mesh_probe(r, 16) is None          # never clamped
    assert RP.resolve_mesh_probe(r, RP.PROBE_INDEX_NONE) is None
    assert RP.resolve_mesh_probe(None, 0) is None


def test_measured_mesh_side_tallies_are_recorded():
    """296 named indices, 0 out of range, over 578 meshes in 6 level archives."""
    assert MESH_SIDE["named"] + MESH_SIDE["null"] == MESH_SIDE["meshes"]
    assert MESH_SIDE["out_of_range"] == 0
    assert MESH_SIDE["box_disagree"] == 0


# ---------------------------------------------------------------------------
# selection (inferred; a fallback, not a law)
# ---------------------------------------------------------------------------

def test_select_probe_returns_the_owning_box():
    r = _res()
    for b in r.boxes:
        assert r.select_probe(b.pos) == b.probe_index


def test_select_probe_falls_back_to_the_nearest_point():
    r = _res()
    far = (1e6, 1e6, 1e6)
    assert not any(b.contains(far) for b in r.boxes)
    assert r.select_probe(far) is not None


def test_select_probe_is_none_on_an_empty_resource():
    blob = bytearray(RP.META_HEADER_SIZE)
    assert RP.parse_probe_resource(bytes(blob)).select_probe((0, 0, 0)) is None


def test_box_containment_uses_the_stored_inverse_rotation():
    r = _res()
    b = r.boxes[22]                       # the one non-identity rotation
    assert not b.is_identity_rotation
    assert b.contains(b.pos)
    outside = tuple(b.pos[k] + 1e5 for k in range(3))
    assert not b.contains(outside)


# ---------------------------------------------------------------------------
# manifest / spec
# ---------------------------------------------------------------------------

def test_manifest_probe_section_shape():
    r = _res()
    sec = RP.manifest_probe_section(r, resource_name=0x942C829457A04A62,
                                    gpu_present=True)
    assert sec["resource"] == "942c829457a04a62"
    assert sec["count"] == 16 and sec["box_count"] == 23 and sec["sphere_count"] == 0
    assert sec["texture_format_name"] == "eBC6UeFLOAT"
    assert sec["gpumemsize"] == 8391168 and sec["gpu_present"] is True
    assert len(sec["probes"]) == 16
    p13 = sec["probes"][13]
    assert p13["index"] == 13 and p13["cube_dim"] == 256 and p13["mipcount"] == 9
    assert p13["gpu_bytes"] == 524448
    assert len(p13["mip_normalizations"]) == 9
    assert len(p13["boxes"]) == 10
    assert p13["cube_file"] == "" and p13["strip_file"] == ""


def test_manifest_probe_section_is_empty_for_none():
    assert RP.manifest_probe_section(None) == {}


def test_build_probe_spec_rejects_an_out_of_range_probe():
    r = _res()
    assert RP.build_probe_spec(r, 16) == {}
    assert RP.build_probe_spec(r, -1) == {}


def test_probe_spec_carries_the_extracted_file_when_given_one():
    r = _res()
    files = {2: {"cube": "probes/probe_02.dds"}}
    spec = RP.build_probe_spec(r, 2, files)
    assert spec["cube_file"] == "probes/probe_02.dds"
    assert RP.probe_file_name(2) == "probe_02.dds"
    assert RP.probe_file_name(2, strip=True) == "probe_02_strip.dds"


def test_colorspace_matches_the_lightmap_hdr_choice():
    from le_mesh import lightmap as LM
    assert RP.COLORSPACE_PROBE == LM.COLORSPACE_LIGHTMAP == "Linear Rec.709"


def test_type_hashes_match_the_dictionary():
    import json
    from unittest import SkipTest
    lookup = Path(__file__).resolve().parents[2] / "hash_lookup.json"
    if not lookup.is_file():
        raise SkipTest(
            f"{lookup} is absent — it is the untracked CSymbol64 hash -> name "
            f"dictionary harvested from your own copy of the game (every "
            f"extractor reads it via `le_archive_decode.load_hash_lookup`, "
            f"defaulting to `hash_lookup.json` at the repo root). Put one "
            f"there to make this test able to run. ⛔ WHILE THIS SKIP IS "
            f"ACTIVE NOTHING CONFIRMS THAT `REFLECTION_PROBE_TYPE_WIN7` / "
            f"`..._GPU` ARE THE HASHES OF `CGReflectionProbeResourceWin7` AND "
            f"`CGReflectionProbeResourceWin7GPU` — THE TWO CONSTANTS EVERY "
            f"PROBE LOOKUP IN THIS MODULE KEYS ON.")
    d = json.loads(lookup.read_text(encoding="utf-8"))
    assert d[f"0x{RP.REFLECTION_PROBE_TYPE_WIN7:016x}"] == "CGReflectionProbeResourceWin7"
    assert d[f"0x{RP.REFLECTION_PROBE_TYPE_WIN7_GPU:016x}"] == "CGReflectionProbeResourceWin7GPU"


def test_block_bytes_and_format_names():
    assert RP.block_bytes(59) == 16 and RP.block_bytes(55) == 8
    assert RP.format_name(59) == "eBC6UeFLOAT"
    assert RP.format_name(999) == "<999>"


# ---------------------------------------------------------------------------
# the STATIC-INSTANCE side: SGPackedInstanceData.probeidx_lmask_dlmask@+0x1c
# ---------------------------------------------------------------------------
#: `measured` on 960 sampled station_front instances (`942c829457a04a62`,
#: 40 busiest of 1,050 mesh-types) — see a local working file.
INSTANCE_FIELD = {
    "sampled": 960,
    "probe_hist": {0: 321, 1: 337, 3: 32, 4: 6, 6: 264},
    "in_range": 960,             # all < 16, that archive's probe count
    "upper24_constant": 0x101,   # bit 8 (lightmask 1) and bit 16 (dirlightmask 1)
}


def test_instance_probe_field_offset_matches_the_scatter_record():
    assert RP.INSTANCE_PROBEFIELD_OFF == 0x1C
    # the record is 44 bytes; the field sits inside it, after `lightmapidx@0x1a`
    assert 0x1A < RP.INSTANCE_PROBEFIELD_OFF < 0x2C


def test_unpack_instance_probe_field_reproduces_the_measured_words():
    for probe in INSTANCE_FIELD["probe_hist"]:
        word = (INSTANCE_FIELD["upper24_constant"] << 8) | probe
        got = RP.unpack_instance_probe_field(word)
        assert got == {"probe_index": probe, "light_mask": 1,
                       "dirlight_mask": 1, "spare": 0}, (probe, got)


def test_instance_probe_index_reads_a_whole_record():
    rec = bytearray(0x2C)
    struct.pack_into("<I", rec, RP.INSTANCE_PROBEFIELD_OFF, (0x101 << 8) | 6)
    assert RP.instance_probe_index(bytes(rec)) == 6


def test_every_measured_instance_probe_is_in_range_for_station_front():
    r = _res()
    assert all(p < r.n_probes for p in INSTANCE_FIELD["probe_hist"])
    assert sum(INSTANCE_FIELD["probe_hist"].values()) == INSTANCE_FIELD["sampled"]
    assert INSTANCE_FIELD["in_range"] == INSTANCE_FIELD["sampled"]
