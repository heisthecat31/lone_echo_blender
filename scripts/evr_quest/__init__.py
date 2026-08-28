"""Echo VR **Quest** (Android / Vulkan) asset decoding.

Separate from the PC path on purpose. The PC decoders in `scripts/evr_*.py`
target `win10_dx12` resource types and DDS/BCn payloads; the Quest build ships a
different type-hash namespace (`*Android`, `*AndroidGPU`), a different texture
descriptor (248 bytes, no DDS wrapper, `ETextureFormat` instead of DXGI), a
`RawTexturePackfile` texel store that PC has no equivalent of, and `_lowspec`
cooks of the same maps. Nothing here modifies or is imported by the PC path.

What IS shared -- deliberately, because it was measured rather than assumed --
is the engine's container grammar. `evr_actor_data`, `evr_lights` and
`evr_lightmap.read_cgsi` read Quest files unchanged; see `docs/EVR_QUEST.md` for
the evidence. Those modules are imported as-is rather than forked.

Reference: `J:/EchoVR-Tools-Launcher/quest_combat_port` (MIT), whose
`tools/resource_io` codecs and `docs/format/resource-type-encyclopedia.md`
established most of the Quest-side grammar this package builds on.
"""

from __future__ import annotations

__all__ = ["types", "mesh"]
