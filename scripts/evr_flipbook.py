"""How many FRAMES a flipbook texture stacks, counted from the texture itself.

## Why this exists

`mpl_arena_a`'s sky is a flipbook: one horizontal band is on screen at a time
and the material steps through the rows. The texture stores all of them at once
-- `e36cdae9c6eaa43a` is 2048x2048 holding EIGHT bands of speckle, and
`bb7ef8ce6d29476c` the same eight as a soft glow -- so a mesh that maps its V
across the whole image shows all eight rows stacked. That is exactly what the
importer did, and it is why the sky read as a stack of stripes instead of one
moving band.

⭐ The count is READ, not assumed. The bands are separated by empty rows, so
the alpha channel's per-row mean is a comb: eight peaks with near-zero gaps
between them. Counting the peaks gives the frame count directly, and it agrees
with the geometry -- `mpl_arena_a`'s meshes 2 and 3 map V over exactly
0.445..0.555, a 0.110 slice of a 0.125 band.

⚠ The material's own property is spelled `layer0_albedo_map_uoffset`, i.e. a U
offset, even though the frames stack along V. The name is not the layout; the
texture is.

⛔ A texture with ONE band is not a flipbook and must report 1, so an ordinary
albedo is never sliced. The peak test needs a real gap between bands -- a
continuous image has no comb and falls through to 1.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

#: Rows are sampled at this height; enough to separate 16 bands cleanly.
SAMPLE_ROWS = 128
#: A row belongs to a band when its mean rises this far above the floor,
#: measured as a fraction of the profile's own range.
PEAK_LEVEL = 0.35
#: Above this many bands the reading is not trusted -- a noisy texture can comb.
MAX_FRAMES = 16


def _alpha_rows(path: Path, rows: int = SAMPLE_ROWS) -> list:
    """Per-row mean of the texture's ALPHA, top to bottom, or []."""
    try:
        raw = subprocess.run(
            ["ffmpeg", "-hide_banner", "-v", "error", "-i", str(path),
             "-vf", "scale=8:%d" % rows, "-f", "rawvideo", "-pix_fmt", "rgba",
             "-"], capture_output=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    if len(raw) < rows * 8 * 4:
        return []
    out = []
    for y in range(rows):
        base = y * 8 * 4
        out.append(sum(raw[base + 3 + x * 4] for x in range(8)) / 8.0)
    return out


def frame_count(path) -> int:
    """Bands stacked along V in this texture. 1 when it is not a flipbook."""
    profile = _alpha_rows(Path(path))
    if not profile:
        return 1
    lo, hi = min(profile), max(profile)
    if hi - lo < 8.0:                     # flat: no comb, not a flipbook
        return 1
    level = lo + (hi - lo) * PEAK_LEVEL
    bands, inside = 0, False
    for value in profile:
        if value >= level and not inside:
            bands += 1
            inside = True
        elif value < level:
            inside = False
    # A band split across the wrap (top and bottom both lit) is ONE band.
    if bands > 1 and profile[0] >= level and profile[-1] >= level:
        bands -= 1
    return bands if 1 < bands <= MAX_FRAMES else 1


def rows_for_material(spec: dict, pkg_dir) -> int:
    """The flipbook row count for a material's layer-0 albedo, or 1.

    Only asked of a material that says its UVs animate: the scalars name
    `..._uoffset` or `..._flipbook_offset`. A static material is never sliced.
    """
    scalars = spec.get("named_scalars") or {}
    if not any(("uoffset" in k or "flipbook" in k) for k in scalars):
        return 1
    channel = (spec.get("channels") or {}).get("base_color") or {}
    rel = channel.get("file")
    if not rel:
        return 1
    path = Path(pkg_dir) / rel
    return frame_count(path) if path.is_file() else 1
