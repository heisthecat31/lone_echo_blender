"""M3.1 probe: histogram SVertexElement.type by usage across real archives.

Purpose: decide whether the packed vertex types eCmp(7)/eSphN(9)/eSphT(10) are
ever used on *renderable* usages (position/normal/tangent). If they are absent
or rare, decoding their (undocumented) bit layout is not
worth it and `vertex_format.py` keeps them `packed_unresolved`. If they ARE
common on renderable usages, that is the signal to invest in a full decode.

MUST run under Windows Python (the Oodle runtime is a Windows binary):

    python.exe blender_tool/tests/probe_vertex_types.py
    python.exe blender_tool/tests/probe_vertex_types.py <archive_hash> [<archive_hash> ...]

Bounded on purpose: processes one archive at a time and caps meshlists per
archive (multi-GB loads have exhausted memory — never load many at once).
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# --- path wiring: blender_tool/tests/ -> blender_tool + <repo>/scripts ---
THIS = Path(__file__).resolve()
BLENDER_TOOL = THIS.parents[1]
REPO_ROOT = THIS.parents[2]
SCRIPTS = REPO_ROOT / "scripts"
for p in (str(BLENDER_TOOL), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from le_oodle import load_decompressed                        # noqa: E402
from le_archive_decode import (                     # noqa: E402
    ARCHIVE_GPU, ARCHIVE_PRIMARY, DEFAULT_HASH_LOOKUP,
    archive_offsets, load_hash_lookup, parse_header, resource_entries,
)
from le_meshlist_decode import parse_candidate   # noqa: E402
from le_mesh import vertex_format as vf                        # noqa: E402

# A small, deterministic default set: the character archive (rich vertex-format
# variety incl. skinned meshes) plus several small meshlist-bearing archives.
DEFAULT_ARCHIVES = [
    "0703fd2acd5803e9",   # jck_* character archive (54 meshlists, skinned)
    "1e5fe1b1b7f055be", "6b8f43e9115ca433", "60e82ca2ac68eb62",
    "061a1ae202355ecb", "2166405af5fdc226", "11a9679ddd15b318",
    "ac360e41e4ede056", "4138d4998596be78",
]

MAX_MESHLISTS_PER_ARCHIVE = 24     # bound the walk
RENDERABLE = {vf.EUsage.ePosition, vf.EUsage.eNormal, vf.EUsage.eTangent}


def _iter_vertexbuffers(primary, gpu, names):
    """Yield element lists for each vertex buffer in each paired meshlist."""
    _, _, data_off, header0_off = archive_offsets(primary, gpu)
    header0 = parse_header(primary, header0_off)
    header1 = parse_header(primary, header0.end)
    primary_rows = resource_entries(primary, header0, names, "CGMeshListResourceWin7")
    gpu_rows = resource_entries(primary, header1, names, "CGMeshListResourceWin7GPU")
    paired = sorted(h for h in primary_rows if h in gpu_rows)
    for name_hash in paired[:MAX_MESHLISTS_PER_ARCHIVE]:
        _e, pos, _s = primary_rows[name_hash]
        _ge, _gp, gpu_size = gpu_rows[name_hash]
        parsed = parse_candidate(primary, data_off + pos, gpu_size)
        if parsed is None:
            continue
        vbt = parsed["vertexbuffers"]
        for vi in range(vbt.count):
            vb_off = vbt.data_off + vi * vf.VB_RECORD_STRIDE
            try:
                elements, _stride, _rel, _n = vf.read_vertex_format(primary, vb_off)
            except Exception:
                continue
            yield elements


def probe(archives):
    names = load_hash_lookup(DEFAULT_HASH_LOOKUP)
    by_usage_type = Counter()       # (usage_name, type_name) -> count of elements
    packed_on_renderable = Counter()  # (usage_name, type_name) -> count
    n_archives = n_vb = 0

    for h in archives:
        pp = ARCHIVE_PRIMARY / h
        gp = ARCHIVE_GPU / h
        if not pp.exists() or not gp.exists():
            print(f"  skip {h}: missing primary/GPU")
            continue
        try:
            primary = load_decompressed(pp)
            gpu = load_decompressed(gp)
        except Exception as exc:   # noqa: BLE001
            print(f"  skip {h}: {exc}")
            continue
        vb_here = 0
        for elements in _iter_vertexbuffers(primary, gpu, names):
            vb_here += 1
            for e in elements:
                key = (e.usage_name, e.type_name)
                by_usage_type[key] += 1
                if e.is_packed and e.usage in RENDERABLE:
                    packed_on_renderable[key] += 1
        n_vb += vb_here
        n_archives += 1
        print(f"  {h}: {vb_here} vertex buffers walked")
        # free before next archive (OOM-safe single-stream)
        del primary, gpu

    print(f"\n=== histogram: {n_archives} archives, {n_vb} vertex buffers ===")
    print(f"{'usage':<14}{'type':<8}{'count':>8}")
    for (usage, typ), c in sorted(by_usage_type.items()):
        flag = "  <-- PACKED" if typ in ("eCmp", "eSphN", "eSphT") else ""
        print(f"{usage:<14}{typ:<8}{c:>8}{flag}")

    print("\n=== packed types (7/9/10) on RENDERABLE usages "
          "(position/normal/tangent) ===")
    if packed_on_renderable:
        for (usage, typ), c in sorted(packed_on_renderable.items()):
            print(f"  {usage} {typ}: {c}")
        print("VERDICT: packed types ARE used on renderable usages -> "
              "byte-verify their bit layout (needs-disasm).")
    else:
        print("  (none)")
        print("VERDICT: packed types 7/9/10 are UNUSED on renderable usages in "
              "this sample -> keep `packed_unresolved`; disasm not warranted now.")
    return by_usage_type, packed_on_renderable


def main() -> int:
    archives = sys.argv[1:] or DEFAULT_ARCHIVES
    print(f"probing {len(archives)} archive(s), <= {MAX_MESHLISTS_PER_ARCHIVE} "
          f"meshlists each")
    probe(archives)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
