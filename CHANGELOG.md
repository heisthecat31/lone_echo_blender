# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.1.0] - 2026-07-24

### Added

- Full-attribute mesh import: positions, normals, tangents, vertex colors, all UV
  sets, and skin weights.
- Per-draw PBR materials: base-color plus normal, including BC5 normal
  reconstruction, built as a Principled BSDF node graph.
- Skeleton / armature import, with meshes skinned to the reconstructed rest pose.
- Whole scatter-level import, placing every instance at its own per-instance
  transform while sharing one mesh datablock per unique mesh.
- Multi-material meshes: one material slot per draw, with each face assigned to its
  covering draw.
- A headless render harness supporting both the Workbench and EEVEE engines for a
  full PBR render.
- The `pyoodle`-backed offline extractor, with environment-configurable game-data
  and Oodle-runtime paths, that writes portable `.lemesh` and `.lescatter` packages.
