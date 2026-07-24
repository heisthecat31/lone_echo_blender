"""Vertex-format decode: enums, element parse, stride, per-type decode."""

import math

from le_mesh import vertex_format as vf
from synthetic import STRIDE44_ELEMENTS, STRIDE44, make_vertex


def test_parse_elements_and_stride():
    # pack the stride-44 element table and read it back
    import struct
    buf = bytearray(0x130)
    for i, el in enumerate(STRIDE44_ELEMENTS):
        struct.pack_into("<8B", buf, i * 8, *el)
    elements = vf.parse_elements(bytes(buf), 0, len(STRIDE44_ELEMENTS))
    assert len(elements) == 6
    assert vf.compute_stride(elements) == STRIDE44
    # position element is float3
    pos = elements[0]
    assert pos.usage == vf.EUsage.ePosition
    assert pos.type == vf.EType.eF32
    assert pos.count == 3 and pos.size == 12
    # uv1 is a second eTexCoord, eU16n, on slot 1
    uv1 = elements[3]
    assert uv1.usage == vf.EUsage.eTexCoord and uv1.slot == 1
    assert uv1.type == vf.EType.eU16n


def test_decode_all_attributes():
    verts = [
        ((0.25, -0.5, 2.0), (1.0, 0.5, 0.0, 1.0), (0.1, 0.9), (0.25, 0.75),
         (0.0, 0.0, 1.0, 1.0), (1.0, 0.0, 0.0, -1.0)),
        ((3.0, 4.0, 5.0), (0.0, 0.25, 1.0, 0.5), (0.3, 0.7), (0.5, 0.5),
         (0.0, 1.0, 0.0, 1.0), (0.0, 0.0, 1.0, 1.0)),
    ]
    gpu = bytearray()
    for v in verts:
        gpu += make_vertex(*v)

    import struct
    vbrec = bytearray(0x130)
    for i, el in enumerate(STRIDE44_ELEMENTS):
        struct.pack_into("<8B", vbrec, i * 8, *el)
    struct.pack_into("<I", vbrec, 0x120, len(STRIDE44_ELEMENTS))
    struct.pack_into("<I", vbrec, 0x128, 0)
    struct.pack_into("<I", vbrec, 0x12C, len(verts))

    elements, stride, rel_gpu, count = vf.read_vertex_format(bytes(vbrec), 0)
    assert stride == STRIDE44 and rel_gpu == 0 and count == 2

    attrs = vf.decode_vertex_buffer(bytes(gpu), 0, rel_gpu, stride, count, elements)
    assert set(attrs) == {"position", "color0", "uv0", "uv1", "normal", "tangent"}

    # exact float positions (eF32 lossless)
    pos = attrs["position"].data
    assert pos[0:3] == [0.25, -0.5, 2.0]
    assert pos[3:6] == [3.0, 4.0, 5.0]

    # eU8n color within 1/255
    col = attrs["color0"].data
    assert math.isclose(col[0], 1.0, abs_tol=1 / 255)
    assert math.isclose(col[1], 0.5, abs_tol=1 / 255)

    # eF32 uv0 (0.1/0.9 not exactly representable in float32 -> tolerance)
    assert math.isclose(attrs["uv0"].data[0], 0.1, abs_tol=1e-6)
    assert math.isclose(attrs["uv0"].data[1], 0.9, abs_tol=1e-6)

    # eU16n uv1 within 1/65535
    assert math.isclose(attrs["uv1"].data[0], 0.25, abs_tol=1 / 65535)

    # eS16n normal within 1/32767
    nrm = attrs["normal"].data
    assert math.isclose(nrm[2], 1.0, abs_tol=1 / 32767)   # z of vertex 0
    assert math.isclose(nrm[5], 1.0, abs_tol=1 / 32767)   # y of vertex 1 (index 4+1)

    # tangent 4th component carries sign
    tan = attrs["tangent"].data
    assert math.isclose(tan[3], -1.0, abs_tol=1 / 32767)


def test_half_float_decode():
    import struct
    # single eF16 x2 element at offset 0, stride 4
    el = vf.VertexElement(usage=vf.EUsage.eTexCoord, offset=0, type=vf.EType.eF16,
                          count=2, slot=0, size=4, stream=0, instancerate=0)
    gpu = struct.pack("<2e", 0.5, -2.5)
    attrs = vf.decode_vertex_buffer(gpu, 0, 0, 4, 1, [el])
    assert attrs["uv0"].data == [0.5, -2.5]


def test_packed_type_marked_unresolved():
    # eCmp packed normal — decoder must not guess; mark packed_unresolved
    el = vf.VertexElement(usage=vf.EUsage.eNormal, offset=0, type=vf.EType.eCmp,
                          count=4, slot=0, size=4, stream=0, instancerate=0)
    attrs = vf.decode_vertex_buffer(b"\x00\x00\x00\x00", 0, 0, 4, 1, [el])
    assert attrs["normal"].packed_unresolved is True
    assert attrs["normal"].data == []


def test_skin_indices_are_integers():
    import struct
    el = vf.VertexElement(usage=vf.EUsage.eSkinIndices, offset=0, type=vf.EType.eU8,
                          count=4, slot=0, size=4, stream=0, instancerate=0)
    gpu = struct.pack("<4B", 3, 7, 12, 40)
    attrs = vf.decode_vertex_buffer(gpu, 0, 0, 4, 1, [el])
    a = attrs["skin_indices"]
    assert a.is_integer is True
    assert a.data == [3, 7, 12, 40]
