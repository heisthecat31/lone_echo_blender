"""le_mesh — pure-stdlib NRadEngine (Lone Echo) mesh decode core.

No Oodle, no bpy, no numpy. Imported by both the offline extractor and the
Blender addon; unit tested with plain python3.

Modules:
  vertex_format  SVertexElement / EUsage / EType decode of every vertex attribute
  meshlist       CGMeshListData -> MeshObject model (attrs, draws, flags, LOD, AABB)
  materials      shaderset/material -> texture-role -> Principled BSDF spec
  package        the .lemesh package contract (writer + stdlib reader)
  static_lod     SGStaticInstanceLODData -> per-instance LOD group/level
  lights         SGLightParams -> decoded lights + Blender unit conversion
  lightmap       CGLightMapResourceWin7 -> baked-lightmap texture sets + binding
  material_scalars  SGMaterialData scalar params + the CSymbol64 name hash
"""

from . import vertex_format, meshlist, materials, package, static_lod  # noqa: F401

__all__ = ["vertex_format", "meshlist", "materials", "package", "static_lod"]
__version__ = "0.3.0"
