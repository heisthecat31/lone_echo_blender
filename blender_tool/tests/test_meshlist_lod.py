"""Core tests for the MESH-LIST LOD chain (`assign_lod_levels` + `select_lod_draws`).

Distinct from the static-scatter system (`test_static_lod.py`): here the coarser
levels are extra `CGRenderParams` covering LATER slices of the SAME index buffer of
ONE mesh, and `CGMeshListData.lodchildindices` names them by mesh-local renderparam
index. Modelled on the stream-confirmed shape of `4a405738bee7a74b` /
`001e3b0be3b357af` (root rp0 [0,17262) 5,754 tris; children [1, 2] = rp1
[17262,28824) 3,854 tris and rp2 [28824,34518) 1,898 tris).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "addon" / "lone_echo_import"))
from le_mesh.meshlist import Draw, assign_lod_levels  # noqa: E402
from package_reader import select_lod_draws        # noqa: E402

NO_PRIMSET = 0xFFFFFFFF


def _draw(rp, start, count, *, primset=NO_PRIMSET, cstart=0, ccount=0):
    return Draw(renderparam_index=rp, idx_start=start, idx_count=count, primtype=4,
                shaderset_index=8, material_index=6, permutation=0, sort_priority=0,
                lod_primset_idx=primset, lod_children_start=cstart,
                lod_children_count=ccount)


def _chain(rp_base=0):
    """One mesh: root + two children, the real 001e3b0be3b357af mesh-0 shape."""
    return [
        _draw(rp_base + 0, 0, 17262, cstart=0, ccount=2),
        _draw(rp_base + 1, 17262, 11562, primset=0, cstart=2),
        _draw(rp_base + 2, 28824, 5694, primset=0, cstart=2),
    ]


def test_root_and_child_predicates():
    root, c1, c2 = _chain()
    assert root.is_lod_parent and not root.is_lod_child
    # a child carries a NON-ZERO children_start (a running cursor) but is not a root
    assert c1.lod_children_start == 2 and not c1.is_lod_parent
    assert c1.is_lod_child and c2.is_lod_child


def test_levels_from_lodchildindices():
    draws = _chain()
    assign_lod_levels(draws, [1, 2], rp_base=0)
    assert [d.lod_level for d in draws] == [0, 1, 2]
    # triangle counts shrink with level (the invariant the chain encodes)
    tris = [d.idx_count // 3 for d in draws]
    assert tris == [5754, 3854, 1898]
    assert all(tris[i] > tris[i + 1] for i in range(len(tris) - 1))


def test_levels_are_mesh_local_not_global():
    """A second mesh's children are also `[1, 2]` — mesh-LOCAL renderparam
    indices, resolved against that mesh's own `renderparamidx` base."""
    draws = _chain(rp_base=3)
    assign_lod_levels(draws, [1, 2, 1, 2], rp_base=3)
    assert [d.lod_level for d in draws] == [0, 1, 2]


def test_mesh_without_a_chain_is_untouched():
    draws = [_draw(0, 0, 300), _draw(1, 300, 600)]     # two material splits
    assign_lod_levels(draws, [], rp_base=0)
    assert [d.lod_level for d in draws] == [0, 0]
    entries = [{"lod": {"level": d.lod_level}} for d in draws]
    assert select_lod_draws(entries, 0) == entries
    assert select_lod_draws(entries, 3) == entries      # nothing to clamp to


def test_select_clamps_and_stacks():
    draws = _chain()
    assign_lod_levels(draws, [1, 2], rp_base=0)
    entries = [{"lod": {"level": d.lod_level}, "idx_start": d.idx_start} for d in draws]

    assert [e["lod"]["level"] for e in select_lod_draws(entries, 0)] == [0]
    assert [e["lod"]["level"] for e in select_lod_draws(entries, 1)] == [1]
    assert [e["lod"]["level"] for e in select_lod_draws(entries, 2)] == [2]
    # past the end clamps to the mesh's coarsest level, never to nothing
    assert [e["lod"]["level"] for e in select_lod_draws(entries, 9)] == [2]
    # negative = every level stacked (the pre-LOD behaviour)
    assert select_lod_draws(entries, -1) == entries


def test_multi_material_mesh_with_two_chains():
    """Two materials x three levels: each root owns its own child pair."""
    draws = [
        _draw(0, 0, 300, cstart=0, ccount=2),
        _draw(1, 300, 150, primset=0, cstart=2),
        _draw(2, 450, 90, primset=0, cstart=2),
        _draw(3, 540, 600, cstart=2, ccount=2),
        _draw(4, 1140, 300, primset=3, cstart=4),
        _draw(5, 1440, 150, primset=3, cstart=4),
    ]
    assign_lod_levels(draws, [1, 2, 4, 5], rp_base=0)
    assert [d.lod_level for d in draws] == [0, 1, 2, 0, 1, 2]
    entries = [{"lod": {"level": d.lod_level}, "rp": d.renderparam_index} for d in draws]
    assert [e["rp"] for e in select_lod_draws(entries, 0)] == [0, 3]
    assert [e["rp"] for e in select_lod_draws(entries, 2)] == [2, 5]
