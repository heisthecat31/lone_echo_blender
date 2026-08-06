"""End-to-end LOD selection over the real `.lescatter` writer + reader (no bpy).

Writes a v3 package with `le_scene_extract.write_package`, reads it back with the
addon's pure `scatter_reader`, and checks that `filter_by_lod` keeps exactly one
level per LOD group with per-group clamping. This is the contract the Blender
operator delegates to, so it is tested archive-free here rather than in Blender.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_ADDON = _ROOT / "blender_tool" / "addon" / "lone_echo_import"
for _p in (str(_ROOT / "scripts"), str(_ROOT / "blender_tool"), str(_ADDON)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from le_scene_extract import (  # noqa: E402
    SceneInstance, SceneMesh, write_package,
)
from scatter_reader import (  # noqa: E402
    LOD_ALL, LOD_COARSEST, ScatterPackage, filter_by_lod, read_instance_lod,
    read_instances,
)


def _mesh(index):
    return SceneMesh(
        index=index, name_hash=0x1122334455667788 + index, matidx=1, shdidx=2,
        aabb_min=(0.0, 0.0, 0.0), aabb_max=(1.0, 1.0, 1.0),
        instance_offset=0, instance_count=1,
        positions=[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0],
        indices=[0, 1, 2],
        draws=[{"matidx": 1, "shdidx": 2, "idx_start": 0, "idx_count": 3}])


def _inst(mesh_index, group, level, group_levels):
    return SceneInstance(
        mesh_index=mesh_index, translation=(float(group), 0.0, float(level)),
        rotation=(0.0, 0.0, 0.0, 1.0), scale=(1.0, 1.0, 1.0),
        lod_group=group, lod_level=level, lod_group_levels=group_levels)


def _pkg(tmp_path):
    """3 groups: A has 3 levels, B has 2, C has 1 (a prop with no LOD chain)."""
    instances = [
        _inst(0, 10, 0, 3), _inst(1, 10, 1, 3), _inst(2, 10, 2, 3),
        _inst(0, 11, 0, 2), _inst(1, 11, 1, 2),
        _inst(0, 12, 0, 1),
    ]
    out = write_package(tmp_path / "lodsel.lescatter", "deadbeef",
                        [_mesh(0), _mesh(1), _mesh(2)], instances)
    return ScatterPackage(out)


def test_manifest_carries_the_lod_block(tmp_path):
    pkg = _pkg(tmp_path)
    manifest = json.loads((pkg.dir / "manifest.json").read_text())
    # The `lod` block landed in v3 and every later version is purely ADDITIVE, so
    # pin the floor, not the exact number — bumping PACKAGE_VERSION for an
    # unrelated additive key (v4 = uv1 + lightmap ids) must not fail this test.
    assert manifest["version"] >= 3
    lod = manifest["lod"]
    assert lod["blob"] == "blobs/instance_lod.bin"
    assert lod["num_groups"] == 3
    assert lod["max_level"] == 2
    assert lod["levels_histogram"] == {"0": 3, "1": 2, "2": 1}
    assert (pkg.dir / "blobs" / "instance_lod.bin").stat().st_size == 6 * 12
    # the 44-B instances.bin contract is unchanged by the v3 addition
    assert (pkg.dir / "blobs" / "instances.bin").stat().st_size == 6 * 44


def test_lod_records_roundtrip(tmp_path):
    pkg = _pkg(tmp_path)
    lods = read_instance_lod(pkg)
    assert [(l.group, l.level, l.group_levels) for l in lods] == [
        (10, 0, 3), (10, 1, 3), (10, 2, 3),
        (11, 0, 2), (11, 1, 2),
        (12, 0, 1),
    ]
    assert pkg.max_lod_level == 2


def test_filter_keeps_one_level_per_group(tmp_path):
    pkg = _pkg(tmp_path)
    recs = read_instances(pkg)
    lods = read_instance_lod(pkg)

    # LOD 0: the highest detail of every group, one instance each
    keep = filter_by_lod(recs, lods, 0)
    assert [r.index for r in keep] == [0, 3, 5]

    # LOD 1: group 12 has only one level, so it clamps to its level 0
    keep = filter_by_lod(recs, lods, 1)
    assert [r.index for r in keep] == [1, 4, 5]

    # LOD 5 (past the end): every group clamps to its coarsest
    keep = filter_by_lod(recs, lods, 5)
    assert [r.index for r in keep] == [2, 4, 5]
    assert filter_by_lod(recs, lods, LOD_COARSEST) == keep

    # every group is represented exactly once at any requested level
    for level in (0, 1, 2, 3, LOD_COARSEST):
        groups = [lods[r.index].group for r in filter_by_lod(recs, lods, level)]
        assert sorted(groups) == [10, 11, 12], (level, groups)

    # LOD_ALL is the pre-LOD behaviour: everything, levels stacked
    assert len(filter_by_lod(recs, lods, LOD_ALL)) == 6


def test_v2_package_without_lod_block_degrades_to_all(tmp_path):
    """A package with no `lod` block reports one level per instance, so filtering
    at any level keeps everything — old packages import exactly as before."""
    pkg = _pkg(tmp_path)
    manifest = json.loads((pkg.dir / "manifest.json").read_text())
    manifest.pop("lod")
    manifest["version"] = 2
    (pkg.dir / "manifest.json").write_text(json.dumps(manifest))
    (pkg.dir / "blobs" / "instance_lod.bin").unlink()

    pkg2 = ScatterPackage(pkg.dir)
    recs = read_instances(pkg2)
    lods = read_instance_lod(pkg2)
    assert all((l.group, l.level, l.group_levels) == (-1, 0, 1) for l in lods)
    assert len(filter_by_lod(recs, lods, 0)) == 6
    assert pkg2.max_lod_level == 0
