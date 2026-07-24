"""le_textures — extract referenced DDS textures out of an archive by hash.

Stage-1 helper for the Blender tool's M2 material pipeline. Thin wrapper over the
texture extractors: given a set of CGTextureResource name
hashes, pull each one's DDS out of an archive and write `<out_dir>/<hash>.dds`.

Delegates to `le_cross_archive_texture.extract_from_archive`, which
handles BOTH storage forms the engine uses:
  * inline  — the GPU slice is a whole DDS after a zero prefix, and
  * streaming — the GPU slice is a <=16-byte stub and the real mip data lives in
    the global streaming packfile; the DDS is rebuilt from STextureStreamData +
    a synthesized header.

Most shared character/prop textures are streaming, so the inline-only
path alone yields empty stubs — hence the streaming delegation.

MUST run under Windows Python (Oodle is a Windows DLL). It self-loads each named
archive's primary stream (a few MB; GPU only if an inline texture needs it).
"""

from __future__ import annotations

import sys
from pathlib import Path

# --- path wiring (mirrors le_extract) so this is importable standalone ------
_THIS = Path(__file__).resolve()
_BLENDER_TOOL = _THIS.parents[1]
_LE_ROOT = _THIS.parents[2]
_SCRIPTS = _LE_ROOT / "scripts"
for _p in (str(_BLENDER_TOOL), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import le_cross_archive_texture as _s67    # noqa: E402


def extract_by_hashes(
    archive_hash: str,
    tex_hashes: set[str],
    out_dir: Path,
    *,
    verbose: bool = False,
) -> dict[str, dict]:
    """Extract the given texture hashes from one archive into `out_dir`.

    `tex_hashes` : iterable of 16-hex-char CGTextureResource name hashes.
    Writes `<out_dir>/<hash>.dds` for each texture that resolves in this archive
    (inline or streaming). Returns {tex_hash -> meta} for the ones written:
        {dxgi_format, width, height, mip_count, note, file, home_archive}
    Hashes not present in this archive (cross-archive / missing / packfile-less)
    are skipped silently — the returned dict simply omits them.
    """
    out_dir = Path(out_dir)
    if not tex_hashes:
        return {}

    tex_ints: list[int] = []
    for th in tex_hashes:
        try:
            tex_ints.append(int(th, 16))
        except (ValueError, TypeError):
            continue
    if not tex_ints:
        return {}

    results = _s67.extract_from_archive(archive_hash, tex_ints, out_dir, verbose=verbose)

    out: dict[str, dict] = {}
    for tex_int, meta in results.items():
        if not meta.get("ok"):
            continue
        th = f"{tex_int:016x}"
        out[th] = {
            "dxgi_format": int(meta.get("dxgi_format", 0) or 0),
            "width": int(meta.get("width", 0) or 0),
            "height": int(meta.get("height", 0) or 0),
            "mip_count": int(meta.get("mip_count", 0) or 0),
            "note": meta.get("note", ""),
            "file": f"{th}.dds",
            "home_archive": archive_hash,
        }
    return out
