"""Echo VR UI canvases -> a package Blender can import.

## Why nothing UI-shaped ever came out of the mesh exporters

Lone Echo's holotable hologram is a MESH (`37670868d7884949`, the visual oracle
`le_mesh.scene_build` calibrates placement against), so it falls out of a scene
export for free.  Echo VR's UI is not built that way and has no mesh anywhere in
the extract, which is why every model- and scene-level export came back without
it.  A screen here is a CANVAS:

    CUICanvasResourceWin10          the screen itself: a WxH PIXEL rectangle
              |                     plus a table of elements, each one a
              |                     sub-rectangle of a shared texture atlas
              v
    CCanvasUICRWin10                per-LEVEL component table: which ACTOR NODE
              |                     each canvas hangs on, its scale, and the
              |                     pixels-per-metre that turns pixels into
              v                     world size
    CActorDataResourceWin10         the node's world transform -- the same
                                    actor table the mesh path already reads

So a canvas is a textured quad whose size the engine computes, not authored
geometry.  This module walks that chain and emits the quads.

## What is measured, and how

Everything below was probed against the shipped corpus rather than assumed, in
the same style as `evr_materials.probe_mesh_field_offset`:

* **`CCanvasUICR` is per level.**  36 of 36 files are level hashes (`CModelCR`
  is 44 of 44), so it indexes exactly like the model component table.
* **88-byte placement records.**  Anchored on "+0x20 is a real
  `CUICanvasResource` and +0x18 is a small record id", the walk yields 2996
  placements over 36 levels, and **every one of the 410 placements on the
  busiest level resolves to a real actor node -- 410/410**.  A wrong stride
  does not score like that.
* **Pixels-per-metre at +0x30.**  2850 of 2996 placements say exactly 150.0,
  the rest are round numbers (512, 200, 300, 192, 50).  Scale at +0x28 is
  (1.0, 1.0) on 2388 of them.  Sizes that come out of it are sane: the lobby's
  biggest screen is a 2048x1024 canvas at 150 -> 13.65 m x 6.83 m, the median
  emitted quad is 1.2 m across, and nothing degenerates.
* **It holds on both formats.**  The Summer (Win7) lobby yields 499 placements
  across the level and its `_summer` sublevel, 1942 quads, and **0 unplaced** --
  every nodeid resolved.  Viewed from above the quads are edge-on lines with
  two radial clusters, which is what a room of wall panels plus two rings of
  displays should look like; the atlases they bind decode to the Echo VR icon
  sheet (mute/mic/players/lock) and the BSL / UNAERO / LEED / ATLAS / HATHOR
  in-fiction logos.
* **Element UV rect and canvas-pixel rect.**  See `CANVAS_LAYOUT_WIN10` --
  including why the obvious "is the rect in bounds and correctly ordered?"
  probe picks a field that is in bounds, correctly ordered, and wrong.

## What is NOT resolved

⚠ **The canvas PLANE is a convention, not a measurement.**  The quad is built
in the actor's local X/Y facing +Z, with pixel Y running downward from the top
left, because that is what UI canvases conventionally are -- but nothing here
proves Echo VR agrees.  If screens come in edge-on or upside down, that is this
assumption, and `--plane` / `--flip-v` change it without touching anything that
was actually measured.

⚠ **Element `z`/layer ordering is not decoded.**  Overlapping elements are
coplanar, so a viewer may z-fight.  `--separation` nudges them apart by draw
order as a workaround; it is a rendering aid, not a decoded value.

    python scripts/evr_ui_extract.py --dir J:/Summer2 --list
    python scripts/evr_ui_extract.py 43e2da7914642604 --dir J:/Summer2 --out ui/
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT / "blender_tool")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
#: Where `evr_scene_extract` finds `evr_mesh_importer_core`, in ITS order.
#:
#: ⚠ There are two copies of `evrFileTools` and they are NOT interchangeable:
#: the bundled `app/extract/evrFileTools` copy cannot read a Win7
#: `CActorDataResource` (it walks the whole file and then runs 2 bytes past the
#: end), while the `FreshEVR` checkout can -- 5210 actors on the Summer lobby.
#: Import the same one the mesh path imports, or UI placement silently works on
#: Win10 and dies on Win7.
for _p in (str(Path(r"C:\Users\lucas\Desktop\FreshEVR\evrFileTools")),
           str(_ROOT / "app" / "extract" / "evrFileTools")):
    if _p not in sys.path:
        sys.path.append(_p)

from evr_resource_types import (ACTOR_DATA, CANVAS_UI_CR, TEXTURE_RESOURCE,
                                UI_CANVAS_RESOURCE, normalise_hash,
                                resolve_type_dir, resource_path)

# ---------------------------------------------------------------------------
# CCanvasUICR -- the placement table
# ---------------------------------------------------------------------------
#: Placement record stride.  See the module docstring for how it was pinned.
PLACEMENT_STRIDE = 88
#: Where the first record can start: the file opens with 56-byte CTable headers.
PLACEMENT_BASE = 0x38
#: Field offsets inside a placement record.
P_NODEID = 0x08
P_RECORD_ID = 0x18
P_CANVAS = 0x20
#: ⛔ NOT (scale_x, scale_y).  The engine schema
#: (`core/types/libs/components/canvasui.radattr`) declares these as
#: `:scalemin` and `:scalemax` -- the two ends of a RANGE the runtime picks a
#: uniform scale from, not per-axis scales.  Reading them as (sx, sy) is
#: harmless on the 2388 of 2996 placements where both are 1.0 and badly wrong
#: everywhere else: (0.5, 15.0) stretched a canvas 30:1 vertically.
P_SCALE_MIN = 0x28
P_SCALE_MAX = 0x2c
#: `:pixels`, whose schema DEFAULT is 150.0 -- which is exactly why 2850 of
#: 2996 shipped placements read 150.0. Independent confirmation of this field.
P_PIXELS_PER_UNIT = 0x30
#: `:transform` and `:model` are component references (`ncaTransform`,
#: `ncaModel`, a joint name), not assets -- 483 and 485 of 499 are unset.
P_TRANSFORM = 0x38
P_MODEL = 0x40
#: `:texture` -- a per-placement TextureAssetRef that overrides what the canvas
#: draws with. Set on 370 of the lobby's 499 placements, so ignoring it means
#: most canvases render with the wrong texture.
P_TEXTURE = 0x48

# ---------------------------------------------------------------------------
# CUICanvasResource -- the canvas itself
# ---------------------------------------------------------------------------
#: 2x u32: the canvas size IN PIXELS that element rectangles are expressed in.
#:
#: ⚠ There are TWO size pairs in the header, +0x0c and +0x14, and they are equal
#: on most canvases -- which is exactly why picking the wrong one looks fine
#: until it doesn't.  Where they differ, +0x14 is the larger, and it is the one
#: the element rects live in: 93% of rects fall inside +0x14 on BOTH formats,
#: against 77% (Win10) and 91% (Win7) for +0x0c.
C_SIZE = 0x14
C_ELEMENT_COUNT = 0x28    # u32

#: Element-table framing: `(base, stride, texture, uv, rect)`.
#:
#: The HEADER is identical in both formats -- same magic, same size fields,
#: same element count -- so nothing about the file announces which one you are
#: holding, and the Win10 framing reads a Win7 canvas as zero elements rather
#: than failing.
#:
#: ⛔ The rect offset is NOT safe to pick by "is it inside the canvas and
#: correctly ordered".  A rect of a few pixels passes that test trivially, and
#: an earlier reading (+0xd0 / +0x78) scored 95% and 92% on it while being
#: degenerate -- every quad it produced was under 2.3 cm across, on canvases
#: that are metres wide.  The offsets below were chosen two other ways instead,
#: which agree:
#:
#:  * **Area.** At +0x74/+0x30 the median element covers 4-16% of its canvas
#:    and the largest covers all of it (max area fraction 1.015). The rejected
#:    offsets have a median of 0.0000 and a MAXIMUM of 0.001.
#:  * **Cross-build.** Over the 850 element pairs that align across the 129
#:    canvases shipping in both builds, `win10 = win7 + 0x44` holds for this
#:    whole block, 675 of 850 values matching to 1e-3 -- so the two formats are
#:    being read at the same field, not at two independently plausible ones.
#:
#: The UV offsets survive their own probe: in 0..1 and correctly ordered for
#: 2993/3000 (Win10) and 1270/1275 (Win7) elements, beating the runner-up
#: ordering roughly 3:1.
CANVAS_LAYOUT_WIN10 = (568, 232, 0x00, 0x10, 0x74)
CANVAS_LAYOUT_WIN7 = (488, 144, 0x00, 0x08, 0x30)


def canvas_layout(path: Path):
    """`(base, stride, texture, uv, rect)` for a canvas file."""
    from evr_resource_types import win7_type_hash
    win7 = win7_type_hash(UI_CANVAS_RESOURCE) or ""
    parent = normalise_hash(Path(path).parent.name)
    if parent == normalise_hash(win7):
        return CANVAS_LAYOUT_WIN7
    return CANVAS_LAYOUT_WIN10


@dataclass
class Element:
    """One sub-rectangle of a canvas."""
    texture: str
    rect: tuple           # (x0, y0, x1, y1) canvas pixels
    uv: tuple             # (u0, v0, u1, v1)


@dataclass
class Canvas:
    """A `CUICanvasResource`: a pixel rectangle and the elements on it."""
    hash: str
    width: int
    height: int
    elements: list = field(default_factory=list)


@dataclass
class Placement:
    """One canvas hung on one actor node."""
    nodeid: int
    canvas: str
    scale: tuple                  # (scalemin, scalemax) -- a RANGE, see P_SCALE_MIN
    pixels_per_unit: float
    record_id: int
    texture: str = ""             # `:texture` override, "" when unset


def _u64(data: bytes, offset: int) -> int:
    return struct.unpack_from("<Q", data, offset)[0]


def canvas_hashes(root: Path) -> set:
    """Every `CUICanvasResource` in the extract."""
    directory = resolve_type_dir(Path(root), UI_CANVAS_RESOURCE)
    if not directory.is_dir():
        return set()
    return {normalise_hash(p.stem if p.suffix == ".bin" else p.name)
            for p in directory.iterdir() if p.is_file()}


def levels_with_ui(root: Path) -> list:
    """Level hashes that have a `CCanvasUICR`."""
    directory = resolve_type_dir(Path(root), CANVAS_UI_CR)
    if not directory.is_dir():
        return []
    return sorted(normalise_hash(p.stem if p.suffix == ".bin" else p.name)
                  for p in directory.iterdir() if p.is_file())


def placements(root: Path, level_hash, known_canvases: set | None = None) -> list:
    """`[Placement, ...]` for a level, in file order.

    The walk is anchored, not strided blindly: a record is accepted only when
    its `+0x20` is a canvas that really exists in this extract AND its
    `+0x18` is a small record id.  On a miss the cursor advances 8 bytes rather
    than a whole record, so one unexpected field cannot desynchronise the rest
    of the table.
    """
    path = resource_path(root, CANVAS_UI_CR, level_hash)
    if path is None:
        return []
    known = known_canvases if known_canvases is not None else canvas_hashes(root)
    data = path.read_bytes()

    out: list = []
    cursor = PLACEMENT_BASE
    while cursor + PLACEMENT_STRIDE <= len(data):
        canvas = normalise_hash(_u64(data, cursor + P_CANVAS))
        record_id = _u64(data, cursor + P_RECORD_ID)
        if canvas in known and record_id < 16:
            scale = (struct.unpack_from("<f", data, cursor + P_SCALE_MIN)[0],
                     struct.unpack_from("<f", data, cursor + P_SCALE_MAX)[0])
            override = _u64(data, cursor + P_TEXTURE)
            texture = "" if override == 0xFFFFFFFFFFFFFFFF else normalise_hash(override)
            ppu = struct.unpack_from("<f", data, cursor + P_PIXELS_PER_UNIT)[0]
            out.append(Placement(nodeid=_u64(data, cursor + P_NODEID),
                                 canvas=canvas, scale=scale, texture=texture,
                                 pixels_per_unit=ppu, record_id=record_id))
            cursor += PLACEMENT_STRIDE
        else:
            cursor += 8
    return out


def read_canvas(root: Path, canvas_hash, known_textures: set | None = None) -> Canvas | None:
    """Decode one `CUICanvasResource`, or None."""
    path = resource_path(root, UI_CANVAS_RESOURCE, canvas_hash)
    if path is None:
        return None
    data = path.read_bytes()
    element_base, stride, e_texture, e_uv, e_rect = canvas_layout(path)
    if len(data) < element_base:
        return None
    width, height = struct.unpack_from("<2I", data, C_SIZE)
    if not (0 < width < 1 << 16 and 0 < height < 1 << 16):
        return None
    canvas = Canvas(hash=normalise_hash(canvas_hash), width=width, height=height)

    declared = struct.unpack_from("<I", data, C_ELEMENT_COUNT)[0]
    available = (len(data) - element_base) // stride
    for i in range(min(declared, available)):
        base = element_base + i * stride
        texture = normalise_hash(_u64(data, base + e_texture))
        if known_textures is not None and texture not in known_textures:
            continue
        uv = struct.unpack_from("<4f", data, base + e_uv)
        rect = struct.unpack_from("<4f", data, base + e_rect)
        # An element whose rect is degenerate or inverted carries no area; the
        # 5% the probe could not read this way land here rather than becoming
        # zero-size or back-facing quads.
        if not (rect[0] < rect[2] and rect[1] < rect[3]):
            continue
        canvas.elements.append(Element(texture=texture, rect=rect, uv=uv))
    return canvas


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
#: Which local axes the canvas plane spans.  `xy` puts the screen in the
#: actor's X/Y plane facing +Z. See the docstring: this is a convention.
PLANES = {
    "xy": lambda u, v: (u, v, 0.0),
    "xz": lambda u, v: (u, 0.0, v),
    "zy": lambda u, v: (0.0, v, u),
}


def element_quad(canvas: Canvas, element: Element, placement: Placement, *,
                 plane: str = "xy", flip_v: bool = False, depth: float = 0.0):
    """`(positions, indices, uvs)` for one element, in the actor's local frame.

    The canvas is centred on the actor node, pixel Y running DOWN from the top
    edge, and scaled by `pixels_per_unit` into metres.
    """
    ppu = placement.pixels_per_unit or 1.0
    # `scalemin`/`scalemax` bound a UNIFORM runtime scale. With no runtime
    # state to pick from, use scalemin -- the resting size -- for both axes.
    # Never one per axis: that is what stretched canvases 30:1.
    uniform = (placement.scale or (1.0,))[0] or 1.0
    sx = sy = uniform
    x0, y0, x1, y1 = element.rect
    to_local = PLANES[plane]

    # Canvas pixel space -> metres, origin at the canvas centre.
    def px(x, y):
        u = (x - canvas.width / 2.0) / ppu * sx
        v = (canvas.height / 2.0 - y) / ppu * sy
        return to_local(u, v)

    corners = [px(x0, y1), px(x1, y1), px(x1, y0), px(x0, y0)]
    if depth:
        axis = {"xy": 2, "xz": 1, "zy": 0}[plane]
        corners = [tuple(c + depth if i == axis else c for i, c in enumerate(p))
                   for p in corners]

    u0, v0, u1, v1 = element.uv
    if flip_v:
        v0, v1 = 1.0 - v0, 1.0 - v1
    uvs = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]

    positions = [c for corner in corners for c in corner]
    flat_uv = [c for uv in uvs for c in uv]
    return positions, [0, 1, 2, 0, 2, 3], flat_uv


# ---------------------------------------------------------------------------
# Materials
# ---------------------------------------------------------------------------
def ui_material_spec(texture: str) -> dict:
    """The v2 material spec for one canvas atlas.

    ⚠ This is the `le_mesh.materials` spec contract, which
    `material_builder.build_material` consumes VERBATIM -- it is not a
    convenience dict.  It needs `key` (it is indexed, not `.get`-ed) and it
    reads `channels[*]["file"]` relative to the sidecar's own directory.

    `render_mode` is BLEND on purpose and is the whole point: a UI atlas is
    mostly transparent, so an opaque quad renders as a black rectangle with an
    icon somewhere in it rather than as an icon.  `alpha` reuses the base
    colour image, which is where a canvas atlas keeps its cut-out.
    """
    image = f"textures/{texture}.dds"
    return {
        "key": f"evr_ui_{texture}",
        "material_hash": texture,
        "shaderset_hash": "",
        "channels": {
            "base_color": {"file": image, "colorspace": "sRGB",
                           "texture": texture},
            "alpha": {"file": image, "colorspace": "Non-Color",
                      "texture": texture, "channel": "A"},
        },
        "base_color_factor": [1.0, 1.0, 1.0, 1.0],
        "render_mode": "BLEND",
        "double_sided": True,
        # Canvases are screens: they emit rather than reflect, and with no
        # lights bound to them a lit-only surface reads as black in a render.
        "emissive": {"file": image, "colorspace": "sRGB", "texture": texture},
        "emissive_intensity": 1.0,
    }


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def _actor_transforms(root: Path, level_hash) -> dict:
    """`{nodeid: transform}` from the level's actor table."""
    import evr_mesh_importer_core.level_reader as level_reader

    path = resource_path(root, ACTOR_DATA, level_hash)
    if path is None:
        return {}
    info = level_reader.parse_actor_data(path.read_bytes())
    return {a["nodeid"]: a.get("transform") or {} for a in info.get("actors", [])}


def _trs(transform: dict):
    pos = transform.get("position") or {}
    rot = transform.get("rotation") or {}
    scl = transform.get("scale") or {}
    return ((pos.get("x", 0.0), pos.get("y", 0.0), pos.get("z", 0.0)),
            (rot.get("x", 0.0), rot.get("y", 0.0), rot.get("z", 0.0),
             rot.get("w", 1.0)),
            (scl.get("x", 1.0), scl.get("y", 1.0), scl.get("z", 1.0)))


def level_group(root: Path, level_hash) -> list:
    """`[level, ...]` -- the level and its sublevels, itself first.

    The mesh path merges sublevels (the lobby is `mpl_lobby_b2` PLUS
    `mpl_lobby_b2_summer`), and each sublevel carries its own `CCanvasUICR`.
    Extracting only the named level drops a whole build's worth of screens --
    233 of the Summer lobby's 499 placements live on the sublevel -- and the UI
    then fails to line up with the scene export it is meant to sit inside.
    """
    try:
        import evr_scene_extract as scene
        # `sublevels_of` keeps only members that actually have actor data, and
        # it looks for them under `_LAST_ROOT` -- which defaults to ".". Leave
        # it unset and every group silently collapses to one level.
        scene._LAST_ROOT[0] = str(root)
        return [normalise_hash(h)
                for h in scene.sublevels_of(scene.resolve_level(str(level_hash)))]
    except Exception:                                        # noqa: BLE001
        return [normalise_hash(level_hash)]


def extract(root: Path, level_hash, out_dir: Path, *, plane: str = "xy",
            flip_v: bool = False, separation: float = 0.0,
            max_texture: int = 0, texture_divisor: int = 1,
            merge_sublevels: bool = True) -> dict:
    """Write one level's UI package.  Returns a summary."""
    from le_scene_extract import SceneInstance, SceneMesh, write_package
    import evr_texture_resource as evr_tex

    root = Path(root)
    level_hash = normalise_hash(level_hash)
    known_canvases = canvas_hashes(root)
    tex_dir = resolve_type_dir(root, TEXTURE_RESOURCE)
    known_textures = ({normalise_hash(p.stem if p.suffix == ".bin" else p.name)
                       for p in tex_dir.iterdir() if p.is_file()}
                      if tex_dir.is_dir() else set())

    group = ([h for h in level_group(root, level_hash)
              if resource_path(root, CANVAS_UI_CR, h) is not None]
             if merge_sublevels else [level_hash])
    if level_hash not in group:
        group.insert(0, level_hash)

    placed, transforms = [], {}
    for member in group:
        placed.extend(placements(root, member, known_canvases))
        transforms.update(_actor_transforms(root, member))

    package = Path(out_dir) / level_hash
    textures = package / "textures"
    textures.mkdir(parents=True, exist_ok=True)

    canvases: dict = {}
    meshes, instances, specs = [], [], []
    spec_of_texture: dict = {}
    unplaced = 0

    for placement in placed:
        transform = transforms.get(placement.nodeid)
        if transform is None:
            unplaced += 1
            continue
        if placement.canvas not in canvases:
            canvases[placement.canvas] = read_canvas(root, placement.canvas,
                                                     known_textures)
        canvas = canvases[placement.canvas]
        if canvas is None or not canvas.elements:
            continue
        translation, rotation, scale = _trs(transform)

        for depth_index, element in enumerate(canvas.elements):
            # `:texture` on the PLACEMENT overrides what the canvas element
            # names -- the same canvas is reused across the level and re-skinned
            # per placement, so honouring it is the difference between every
            # copy drawing the same art and each drawing its own.
            texture = placement.texture or element.texture
            if texture not in spec_of_texture:
                spec_of_texture[texture] = len(specs)
                specs.append({
                    "matidx": len(specs), "shdidx": 0,
                    "spec": ui_material_spec(texture),
                })
            matidx = spec_of_texture[texture]
            positions, indices, uvs = element_quad(
                canvas, element, placement, plane=plane, flip_v=flip_v,
                depth=separation * depth_index)
            meshes.append(SceneMesh(
                index=len(meshes), name_hash=int(placement.canvas, 16),
                matidx=matidx, shdidx=0,
                aabb_min=(0, 0, 0), aabb_max=(0, 0, 0),
                instance_offset=len(instances), instance_count=1,
                positions=positions, indices=indices,
                normals=None, uv0=uvs, uv1=None,
                draws=[{"matidx": matidx, "shdidx": 0,
                        "idx_start": 0, "idx_count": len(indices)}]))
            instances.append(SceneInstance(
                mesh_index=len(meshes) - 1, translation=translation,
                rotation=rotation, scale=scale))

    if meshes:
        write_package(package, level_hash, meshes, instances)

    written = 0
    for texture in sorted(spec_of_texture):
        target = textures / f"{texture}.dds"
        if target.exists():
            continue
        try:
            blob, _note = evr_tex.rebuild_dds(root, texture)
        except Exception:                                    # noqa: BLE001
            blob = None
        if not blob:
            continue
        if texture_divisor > 1:
            blob, _n = evr_tex.scale_dds_resolution(blob, texture_divisor)
        if max_texture:
            blob, _n = evr_tex.cap_dds_resolution(blob, max_texture)
        target.write_bytes(blob)
        written += 1

    summary = {
        "level": level_hash,
        "merged": group,
        "placements": len(placed),
        "unplaced": unplaced,
        "canvases": len([c for c in canvases.values() if c]),
        "quads": len(meshes),
        "materials": len(specs),
        "textures": written,
        "package": str(package),
        "plane": plane,
        "flip_v": flip_v,
    }
    if specs:
        (package / "materials.json").write_text(json.dumps(
            {"format": "le_materials", "version": 2, "master": level_hash,
             "source": "evr_ui_extract", "textures_subdir": "textures",
             "materials": specs, "diagnostics": summary},
            indent=1), encoding="utf-8")
    (package / "ui.json").write_text(json.dumps(
        {"format": "evr_ui", "version": 1, "level": level_hash,
         "summary": summary,
         "canvases": [
             {"hash": c.hash, "width": c.width, "height": c.height,
              "elements": [{"texture": e.texture, "rect": list(e.rect),
                            "uv": list(e.uv)} for e in c.elements]}
             for c in canvases.values() if c],
         "placements": [
             {"nodeid": f"{p.nodeid:016x}", "canvas": p.canvas,
              "scalemin": p.scale[0], "scalemax": p.scale[1],
              "texture_override": p.texture, "pixels_per_unit": p.pixels_per_unit,
              "placed": p.nodeid in transforms}
             for p in placed]},
        indent=1), encoding="utf-8")
    return summary


def survey(root: Path) -> list:
    """`[{level, placements, canvases}, ...]` for every level with UI."""
    known = canvas_hashes(root)
    rows = []
    for level in levels_with_ui(root):
        placed = placements(root, level, known)
        rows.append({"level": level, "placements": len(placed),
                     "canvases": len({p.canvas for p in placed})})
    return sorted(rows, key=lambda r: -r["placements"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("level", nargs="?", help="level hash")
    ap.add_argument("--dir", required=True, help="flat extract root")
    ap.add_argument("--out", default="ui", help="output directory")
    ap.add_argument("--list", action="store_true",
                    help="survey every level that has UI and exit")
    ap.add_argument("--plane", choices=sorted(PLANES), default="xy",
                    help="local plane the canvas spans (default xy, facing +Z)")
    ap.add_argument("--flip-v", action="store_true",
                    help="flip texture V if screens come out upside down")
    ap.add_argument("--separation", type=float, default=0.0,
                    help="metres to push each successive element apart, to "
                         "stop coplanar elements z-fighting (default 0)")
    ap.add_argument("--no-merge-sublevels", action="store_true",
                    help="extract only the named level, not its sublevels")
    ap.add_argument("--max-texture", type=int, default=0,
                    help="cap texture edge length (0 = uncapped)")
    ap.add_argument("--texture-divisor", type=int, default=1)
    args = ap.parse_args(argv)

    root = Path(args.dir)
    if args.list:
        rows = survey(root)
        total = sum(r["placements"] for r in rows)
        print(f"{len(rows)} level(s) with UI, {total} canvas placements\n")
        print(f"{'level':18s} {'placements':>10s} {'canvases':>9s}")
        for row in rows:
            print(f"{row['level']:18s} {row['placements']:10d} "
                  f"{row['canvases']:9d}")
        return 0

    if not args.level:
        ap.error("pass a level hash, or --list")
    summary = extract(root, args.level, Path(args.out), plane=args.plane,
                      flip_v=args.flip_v, separation=args.separation,
                      max_texture=args.max_texture,
                      texture_divisor=args.texture_divisor,
                      merge_sublevels=not args.no_merge_sublevels)
    for key, value in summary.items():
        print(f"  {key:12s} {value}")
    if not summary["quads"]:
        print("\nNo UI quads produced.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
