"""Inspect CArchiveResourceWin7 runtime stream layout.

CArchive::Load consumes the primary stream as:

  u64 datasize[0], u64 datasize[1]
  skip 8
  u64 extra_skip, skip extra_skip
  attach archive.data[0] from the current primary offset for datasize[0] bytes
  attach archive.data[1] from GPU stream offset 0 for datasize[1] bytes
  read two CArchiveHeaderData records from the primary stream

Run with Windows Python so le_oodle can load the game Oodle DLL:
    python.exe scripts/le_archive_decode.py
"""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass
from pathlib import Path

from le_oodle import DATA_ROOT, load_decompressed
from le_meshlist_decode import parse_candidate, vertex_stride


ARCHIVE_PRIMARY = DATA_ROOT / "primary" / "e5bd8207135b8887" / "v13363680368"
ARCHIVE_GPU = DATA_ROOT / "GPU" / "005a5579fb36b249" / "v13363680368"
DEFAULT_HASH = "455295a65f8dbb6d"
DEFAULT_HASH_LOOKUP = Path("hash_lookup.json")


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


@dataclass
class Blob:
    name: str
    count: int
    off: int
    elem_size: int

    @property
    def end(self) -> int:
        return self.off + self.count * self.elem_size


@dataclass
class Header:
    start: int
    end: int
    parents: Blob
    entries: Blob
    contents: Blob
    contents_seed: int
    versions: Blob
    versions_seed: int
    hashes: Blob
    ptrpatches: Blob


def read_counted_blob(data: bytes, off: int, name: str, elem_size: int) -> tuple[Blob, int]:
    if off + 4 > len(data):
        raise ValueError(f"{name}: count at {off:#x} is past EOF")
    count = u32(data, off)
    blob = Blob(name, count, off + 4, elem_size)
    if blob.end > len(data):
        raise ValueError(
            f"{name}: count={count} elem_size={elem_size} end={blob.end:#x} "
            f"past EOF {len(data):#x}"
        )
    return blob, blob.end


def read_map(data: bytes, off: int, name: str, elem_size: int) -> tuple[Blob, int, int]:
    blob, off = read_counted_blob(data, off, name, elem_size)
    if off + 4 > len(data):
        raise ValueError(f"{name}: seed/status at {off:#x} is past EOF")
    seed = u32(data, off)
    return blob, seed, off + 4


def parse_header(data: bytes, off: int) -> Header:
    start = off
    off += 0xB0  # SLanguageSelection, copied as one fixed-size blob.
    parents, off = read_counted_blob(data, off, "parents", 8)
    entries, off = read_counted_blob(data, off, "entries", 8)
    contents, contents_seed, off = read_map(data, off, "contents", 24)
    versions, versions_seed, off = read_map(data, off, "versions", 16)
    hashes, off = read_counted_blob(data, off, "hashes", 16)
    ptrpatches, off = read_counted_blob(data, off, "ptrpatches", 32)
    return Header(
        start=start,
        end=off,
        parents=parents,
        entries=entries,
        contents=contents,
        contents_seed=contents_seed,
        versions=versions,
        versions_seed=versions_seed,
        hashes=hashes,
        ptrpatches=ptrpatches,
    )


def load_hash_lookup(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    result: dict[int, str] = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            continue
        text = key.lower()
        if text.startswith("0x"):
            text = text[2:]
        try:
            result[int(text, 16)] = value
        except ValueError:
            continue
    return result


def archive_offsets(primary: bytes, gpu: bytes) -> tuple[int, int, int, int]:
    primary_size = u64(primary, 0)
    gpu_size = u64(primary, 8)
    off = 16
    off += 8
    extra_skip = u64(primary, off)
    off += 8 + extra_skip
    if off + primary_size > len(primary):
        raise ValueError(
            f"primary data block end {off + primary_size:#x} past primary EOF {len(primary):#x}"
        )
    if gpu_size > len(gpu):
        raise ValueError(f"gpu data block size {gpu_size:#x} past GPU EOF {len(gpu):#x}")
    return primary_size, gpu_size, off, off + primary_size


def entry_at(primary: bytes, header: Header, index: int) -> tuple[int, int]:
    if index < 0 or index >= header.entries.count:
        raise IndexError(index)
    return struct.unpack_from("<II", primary, header.entries.off + index * 8)


def print_header(
    header: Header,
    primary: bytes,
    index: int,
    limit: int,
    names: dict[int, str],
) -> None:
    print(f"\nheader[{index}] start={header.start:#x} end={header.end:#x}")
    for blob in (
        header.parents,
        header.entries,
        header.contents,
        header.versions,
        header.hashes,
        header.ptrpatches,
    ):
        print(f"  {blob.name:<10} count={blob.count:<6} data={blob.off:#x} end={blob.end:#x}")
    print(f"  contents_seed={header.contents_seed:#x} versions_seed={header.versions_seed:#x}")

    shown = min(header.entries.count, limit)
    if shown:
        print("  first entries:")
    for i in range(shown):
        pos, size = struct.unpack_from("<II", primary, header.entries.off + i * 8)
        print(f"    [{i:04d}] pos={pos:#010x} size={size:#010x}")

    shown = min(header.contents.count, limit)
    if shown:
        print("  first contents:")
    for i in range(shown):
        k0, k1, value = struct.unpack_from("<QQQ", primary, header.contents.off + i * 24)
        k0_name = names.get(k0, "")
        k1_name = names.get(k1, "")
        suffix = ""
        if k0_name or k1_name:
            suffix = f"  {k0_name or '?'} / {k1_name or '?'}"
        print(f"    [{i:04d}] key=({k0:016x},{k1:016x}) value={value}{suffix}")


def print_known_resource_ranges(
    primary: bytes,
    header: Header,
    header_index: int,
    data_off: int,
    names: dict[int, str],
    wanted: str,
) -> None:
    rows: list[tuple[int, int, int, int, str]] = []
    for i in range(header.contents.count):
        type_hash, name_hash, value = struct.unpack_from("<QQQ", primary, header.contents.off + i * 24)
        if names.get(type_hash) != wanted:
            continue
        if value >= header.entries.count:
            continue
        pos, size = entry_at(primary, header, value)
        rows.append((value, pos, size, name_hash, names.get(name_hash, "")))

    if not rows:
        return

    print(f"\n{wanted} ranges from header[{header_index}]:")
    for value, pos, size, name_hash, name in rows:
        label = f" {name}" if name else ""
        print(
            f"  entry[{value:04d}] abs={data_off + pos:#x} rel={pos:#x} "
            f"size={size:#x} name={name_hash:016x}{label}"
        )


# Stable resource-type-name hashes for the types the extractor resolves. Using
# these constants lets a resource type be identified WITHOUT a hash_lookup name
# table, so the tool runs against a bare game-data tree; a supplied table stays an
# optional fallback (and covers any type not listed here).
KNOWN_TYPE_HASHES = {
    "CGMeshListResourceWin7":       0x366B22153D894FE1,
    "CGMeshListResourceWin7GPU":    0x617076C759935957,
    "CGStaticInstanceResourceWin7": 0xE83CF7FAAEC4CAB5,
    "CSkeletonResourceWin7":        0x202D89353292D63D,
    "CModelCRWin7":                 0x3DE813820D0B4719,
}


def resource_entries(
    primary: bytes,
    header: Header,
    names: dict[int, str],
    wanted: str,
) -> dict[int, tuple[int, int, int]]:
    rows: dict[int, tuple[int, int, int]] = {}
    wanted_hash = KNOWN_TYPE_HASHES.get(wanted)
    for i in range(header.contents.count):
        type_hash, name_hash, value = struct.unpack_from("<QQQ", primary, header.contents.off + i * 24)
        if value >= header.entries.count:
            continue
        # match by resolved name (if a table was supplied) OR the known constant
        if names.get(type_hash) != wanted and type_hash != wanted_hash:
            continue
        pos, size = entry_at(primary, header, value)
        rows[name_hash] = (value, pos, size)
    return rows


def dump_meshlist_resources(
    primary: bytes,
    gpu: bytes,
    header0: Header,
    header1: Header,
    data_off: int,
    names: dict[int, str],
    limit: int,
) -> None:
    primary_rows = resource_entries(primary, header0, names, "CGMeshListResourceWin7")
    gpu_rows = resource_entries(primary, header1, names, "CGMeshListResourceWin7GPU")
    if not primary_rows:
        return

    print("\nResolved CGMeshListResourceWin7 loader-order parses:")
    for shown, (name_hash, (entry_index, pos, size)) in enumerate(sorted(primary_rows.items())):
        if shown >= limit:
            remaining = len(primary_rows) - shown
            if remaining:
                print(f"  ... {remaining} more mesh-list resources omitted")
            break

        gpu_entry = gpu_rows.get(name_hash)
        if gpu_entry is None:
            print(f"  entry[{entry_index:04d}] name={name_hash:016x}: no paired GPU entry")
            continue

        gpu_index, gpu_pos, gpu_size = gpu_entry
        start = data_off + pos
        end = start + size
        candidate = parse_candidate(primary, start, gpu_size)
        resource_name = names.get(name_hash, "")
        label = f" {resource_name}" if resource_name else ""
        print(
            f"\n  primary entry[{entry_index:04d}] {name_hash:016x}{label} "
            f"abs={start:#x} size={size:#x}"
        )
        print(f"  gpu     entry[{gpu_index:04d}] abs={gpu_pos:#x} size={gpu_size:#x}")
        if candidate is None:
            print("  loader-order parse: FAIL")
            continue

        print(f"  loader-order parse: OK used={candidate['end'] - start:#x} entry_end={end:#x}")
        for table_name in ("meshes", "renderparams", "vertexbuffers", "indexbuffers", "cbufferidx"):
            table = candidate[table_name]
            print(
                f"    {table_name:<13} count={table.count:<4} "
                f"rel={table.data_off - start:#x} size={table.end - table.data_off:#x}"
            )
        print(
            f"    numcbuffers={candidate['numcbuffers']} "
            f"cbufferoffset={candidate['cbufferoffset']:#x} gpudatasize={candidate['gpudatasize']:#x}"
        )

        meshes = candidate["meshes"]
        renderparams = candidate["renderparams"]
        vertexbuffers = candidate["vertexbuffers"]
        indexbuffers = candidate["indexbuffers"]
        for mesh_index in range(min(meshes.count, 4)):
            mesh_off = meshes.data_off + mesh_index * 0x80
            mesh_name = u64(primary, mesh_off)
            vb_index = u32(primary, mesh_off + 0x08)
            ib_index = u32(primary, mesh_off + 0x0C)
            rp_index = u32(primary, mesh_off + 0x1C)
            rp_count = u32(primary, mesh_off + 0x20)
            aabb = struct.unpack_from("<6f", primary, mesh_off + 0x24)
            print(
                f"    mesh[{mesh_index}] name={mesh_name:016x} vb={vb_index} ib={ib_index} "
                f"rp={rp_index}+{rp_count} "
                f"aabb=({aabb[0]:.3g},{aabb[1]:.3g},{aabb[2]:.3g}).."
                f"({aabb[3]:.3g},{aabb[4]:.3g},{aabb[5]:.3g})"
            )

            if rp_index < renderparams.count:
                rp_off = renderparams.data_off + rp_index * 0x68
                print(
                    f"      rp0 primtype={u32(primary, rp_off + 0x40)} "
                    f"idxstart={u32(primary, rp_off + 0x44)} "
                    f"idxcount={u32(primary, rp_off + 0x48)}"
                )
            if vb_index < vertexbuffers.count:
                vb_off = vertexbuffers.data_off + vb_index * 0x130
                rel_gpu = u32(primary, vb_off + 0x128)
                nverts = u32(primary, vb_off + 0x12C)
                stride = vertex_stride(primary, vb_off)
                print(
                    f"      vb rel_gpu={rel_gpu:#x} abs_gpu={gpu_pos + rel_gpu:#x} "
                    f"verts={nverts} stride={stride}"
                )
            if ib_index < indexbuffers.count:
                ib_off = indexbuffers.data_off + ib_index * 0x10
                rel_gpu, nidx, index_size, pad = struct.unpack_from("<4I", primary, ib_off)
                print(
                    f"      ib rel_gpu={rel_gpu:#x} abs_gpu={gpu_pos + rel_gpu:#x} "
                    f"indices={nidx} size={index_size} pad={pad}"
                )
                if (
                    vb_index < vertexbuffers.count
                    and index_size in (2, 4)
                    and rel_gpu + nidx * index_size <= gpu_size
                ):
                    fmt = "H" if index_size == 2 else "I"
                    values = struct.unpack_from(f"<{nidx}{fmt}", gpu, gpu_pos + rel_gpu)
                    non_restart = [v for v in values if v != 0xFFFF]
                    if non_restart:
                        nverts = u32(primary, vertexbuffers.data_off + vb_index * 0x130 + 0x12C)
                        in_range = sum(1 for v in non_restart if v < nverts)
                        print(
                            f"      indices minmax={min(non_restart)}..{max(non_restart)} "
                            f"in_vb={in_range}/{len(non_restart)} "
                            f"restart={len(values) - len(non_restart)}"
                        )


def scan_entry_meshlists(primary: bytes, header: Header, data_off: int, gpu_size: int, limit: int) -> None:
    hits: list[tuple[int, int, int, int]] = []
    for i in range(header.entries.count):
        pos, size = struct.unpack_from("<II", primary, header.entries.off + i * 8)
        start = data_off + pos
        end = start + size
        if start < data_off or end > data_off + u64(primary, 0):
            continue
        cand = parse_candidate(primary, start, gpu_size)
        if cand is not None and cand["end"] <= end:
            hits.append((i, pos, size, cand["end"] - start))  # type: ignore[operator]
            if len(hits) >= limit:
                break

    print(f"\nloader-order CGMeshListData hits inside header entries: {len(hits)}")
    for idx, pos, size, used in hits:
        print(f"  entry[{idx}] pos={pos:#x} size={size:#x} used={used:#x}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("hash", nargs="?", default=DEFAULT_HASH)
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--hash-lookup", type=Path, default=DEFAULT_HASH_LOOKUP)
    parser.add_argument("--scan-meshlists", action="store_true")
    args = parser.parse_args()

    prim_path = ARCHIVE_PRIMARY / args.hash
    gpu_path = ARCHIVE_GPU / args.hash
    primary = load_decompressed(prim_path)
    gpu = load_decompressed(gpu_path)
    names = load_hash_lookup(args.hash_lookup)

    primary_size, gpu_size, data_off, header0_off = archive_offsets(primary, gpu)
    print(f"archive {args.hash}")
    print(f"  decompressed primary={len(primary):#x} gpu={len(gpu):#x}")
    print(f"  datasizes primary={primary_size:#x} gpu={gpu_size:#x}")
    print(f"  primary data block={data_off:#x}..{header0_off:#x}")
    print(f"  gpu data block=0x0..{gpu_size:#x}")
    print(f"  prelude qwords={[hex(u64(primary, i * 8)) for i in range(5)]}")

    header0 = parse_header(primary, header0_off)
    header1 = parse_header(primary, header0.end)
    print_header(header0, primary, 0, args.limit, names)
    print_header(header1, primary, 1, args.limit, names)
    print(f"\nparsed tail end={header1.end:#x} primary EOF={len(primary):#x}")

    print_known_resource_ranges(primary, header0, 0, data_off, names, "CGMeshListResourceWin7")
    print_known_resource_ranges(primary, header1, 1, 0, names, "CGMeshListResourceWin7GPU")
    dump_meshlist_resources(primary, gpu, header0, header1, data_off, names, args.limit)

    if args.scan_meshlists:
        scan_entry_meshlists(primary, header0, data_off, gpu_size, args.limit)
        scan_entry_meshlists(primary, header1, data_off, gpu_size, args.limit)


if __name__ == "__main__":
    main()
