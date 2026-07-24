"""Scan CModelCRWin7 component resources.

This follows the transform scan by resolving CModelCRWin7 resources and
extracting the model component lookup rows plus SModelCD::SInitData asset hashes.

Run with Windows Python so le_oodle can load the game Oodle DLL:
    python.exe scripts/le_model_scan.py --hash 455295a65f8dbb6d
    python.exe scripts/le_model_scan.py --all --quiet
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

from le_oodle import load_decompressed
from le_archive_decode import (
    ARCHIVE_GPU,
    ARCHIVE_PRIMARY,
    DEFAULT_HASH,
    DEFAULT_HASH_LOOKUP,
    archive_offsets,
    load_hash_lookup,
    parse_header,
    resource_entries,
)


MODEL_TYPE = "CModelCRWin7"
DEFAULT_MANIFEST = Path("generic_rebuilds/model_manifest.tsv")
DEFAULT_SUMMARY = Path("generic_rebuilds/model_summary.json")

SRESOURCE_SIZE = 0xE8
LOOKUP_SIZE = 0x18
INITDATA_SIZE = 0x90
PROPERTIES_SIZE = 0xB0
PROPERTIES_INIT_OFFSET = 0x20
NULL_HASHES = {"0000000000000000", "ffffffffffffffff"}


def u16(data: bytes, off: int) -> int:
    return struct.unpack_from("<H", data, off)[0]


def u32(data: bytes, off: int) -> int:
    return struct.unpack_from("<I", data, off)[0]


def u64(data: bytes, off: int) -> int:
    return struct.unpack_from("<Q", data, off)[0]


def hex64(value: int) -> str:
    return f"{value:016x}"


def resolved_name(names: dict[int, str], value: int) -> str:
    return names.get(value, "")


def align(value: int, amount: int) -> int:
    return (value + amount - 1) & ~(amount - 1)


@dataclass
class ModelRow:
    archive_hash: str
    resource_hash: str
    resource_name: str
    entry_index: int
    entry_abs: str
    entry_size: str
    row_index: int
    component_name_hash: str
    component_name: str
    actor_node_hash: str
    actor_node_name: str
    actor_id: int
    num_pooled: int
    flags: str
    start_visible: int
    unpickable: int
    asset_hashes: str
    asset_names: str
    attach_mode: int
    max_attachments: int
    transform_name_hash: str
    transform_name: str
    attach_model_hash: str
    attach_model_name: str
    attach_name_hash: str
    attach_name: str
    scene_set_config_count: int


def archive_hashes(explicit_hashes: list[str] | None, scan_all: bool, limit: int | None) -> list[str]:
    if explicit_hashes:
        hashes = [value.lower() for value in explicit_hashes]
    elif scan_all:
        hashes = sorted(
            path.name
            for path in ARCHIVE_PRIMARY.iterdir()
            if path.is_file() and (ARCHIVE_GPU / path.name).is_file()
        )
    else:
        hashes = [DEFAULT_HASH]
    if limit is not None:
        hashes = hashes[:limit]
    return hashes


def compressed_stub(path: Path) -> bool:
    return path.stat().st_size in (44, 57)


def bitmap_word_count(bit_count: int) -> int:
    return (bit_count + 63) // 64


def bit_is_set(words: list[int], index: int) -> int:
    word_index = index // 64
    bit_index = index % 64
    if word_index >= len(words):
        return 0
    return 1 if words[word_index] & (1 << bit_index) else 0


def read_bitmaps(primary: bytes, entry_abs: int) -> tuple[list[int], list[int], list[int], int]:
    disable_bits = u64(primary, entry_abs + 0x58)
    startvisible_bits = u64(primary, entry_abs + 0xB8)
    unpickable_bits = u64(primary, entry_abs + 0xE0)

    pos = entry_abs + SRESOURCE_SIZE
    disable_words = [u64(primary, pos + i * 8) for i in range(bitmap_word_count(disable_bits))]
    pos += len(disable_words) * 8
    startvisible_words = [u64(primary, pos + i * 8) for i in range(bitmap_word_count(startvisible_bits))]
    pos += len(startvisible_words) * 8
    unpickable_words = [u64(primary, pos + i * 8) for i in range(bitmap_word_count(unpickable_bits))]
    pos += len(unpickable_words) * 8
    return disable_words, startvisible_words, unpickable_words, pos


def plausible_lookup_run(primary: bytes, off: int, count: int, names: dict[int, str]) -> bool:
    if count <= 0:
        return False
    first_name = names.get(u64(primary, off), "").lower()
    if "model" not in first_name:
        return False
    resolved_component_names = 0
    repeated_names: dict[int, int] = {}
    for i in range(count):
        row = off + i * LOOKUP_SIZE
        component_name = u64(primary, row)
        actor_node = u64(primary, row + 0x08)
        pad = u32(primary, row + 0x14)
        repeated_names[component_name] = repeated_names.get(component_name, 0) + 1
        if names.get(component_name, "").lower().find("model") >= 0:
            resolved_component_names += 1
        if actor_node == 0 or pad != 0:
            return False

    most_common_name_hash, most_common = max(repeated_names.items(), key=lambda item: item[1])
    most_common_name = names.get(most_common_name_hash, "").lower()
    return (
        "model" in most_common_name
        and most_common >= max(1, math.ceil(count * 0.75))
        and resolved_component_names >= max(1, count // 2)
    )


def find_lookup_start(primary: bytes, entry_abs: int, entry_size: int, count: int, names: dict[int, str]) -> int:
    start = entry_abs + SRESOURCE_SIZE
    end = min(entry_abs + entry_size - count * LOOKUP_SIZE, entry_abs + 0x800)
    for off in range(align(start, 8), end + 1, 8):
        if plausible_lookup_run(primary, off, count, names):
            return off
    raise ValueError(f"could not locate CModel lookup rows near {entry_abs:#x}")


def plausible_init_run(primary: bytes, off: int, count: int, entry_end: int) -> bool:
    if count <= 0 or off + count * INITDATA_SIZE > entry_end:
        return False
    for i in range(count):
        row = off + i * INITDATA_SIZE
        asset_bytes = u64(primary, row + 0x10)
        asset_count = u64(primary, row + 0x28)
        asset_used = u64(primary, row + 0x30)
        max_attachments = u32(primary, row + 0x3C)
        if asset_bytes != asset_count * 8:
            return False
        if asset_count != asset_used or asset_count > 256:
            return False
        if max_attachments > 4096:
            return False
    return True


def find_init_start(primary: bytes, lookup_start: int, lookup_count: int, init_count: int, entry_end: int) -> int:
    start = align(lookup_start + lookup_count * LOOKUP_SIZE, 16)
    end = min(entry_end - init_count * INITDATA_SIZE, start + 0x200)
    for off in range(start, end + 1, 16):
        if plausible_init_run(primary, off, init_count, entry_end):
            return off
    raise ValueError(f"could not locate CModel init rows after {lookup_start:#x}")


def compact_asset_count(primary: bytes, init_off: int) -> int:
    """Read compact CTable<CAssetName> count used by serialized SProperties rows.

    Some archives carry a post-resource SProperties table.  In that stream the
    nested modelassets CTable omits the first runtime-sized qword compared with
    the SResource inittable form: byte size is at +0x08, counts at +0x20/+0x28,
    and flags at +0x30.
    """
    byte_size = u64(primary, init_off + 0x08)
    allocated = u64(primary, init_off + 0x20)
    used = u64(primary, init_off + 0x28)
    flags = u32(primary, init_off + 0x30)
    if byte_size % 8 != 0 or byte_size != used * 8:
        raise ValueError("bad compact modelassets byte size")
    if allocated < used or used > 256:
        raise ValueError("bad compact modelassets count")
    if flags > 0xFFFF:
        raise ValueError("bad compact modelassets flags")
    return used


def plausible_properties_run(
    primary: bytes,
    off: int,
    count: int,
    entry_end: int,
    names: dict[int, str],
) -> bool:
    if count <= 0 or off + count * PROPERTIES_SIZE > entry_end:
        return False
    first_name = names.get(u64(primary, off), "").lower()
    if "model" not in first_name:
        return False
    total_assets = 0
    resolved_component_names = 0
    for i in range(count):
        row = off + i * PROPERTIES_SIZE
        component_name = u64(primary, row)
        actor_node = u64(primary, row + 0x08)
        pad = u32(primary, row + 0x14)
        if names.get(component_name, "").lower().find("model") >= 0:
            resolved_component_names += 1
        if actor_node == 0 or pad != 0:
            return False
        try:
            total_assets += compact_asset_count(primary, row + PROPERTIES_INIT_OFFSET)
        except ValueError:
            return False
    if off + count * PROPERTIES_SIZE + total_assets * 8 > entry_end:
        return False
    return resolved_component_names >= max(1, count // 2)


def find_properties_start(
    primary: bytes,
    search_start: int,
    count: int,
    entry_end: int,
    names: dict[int, str],
) -> int | None:
    end = min(entry_end - count * PROPERTIES_SIZE, search_start + 0x4000)
    for off in range(align(search_start, 8), end + 1, 8):
        if plausible_properties_run(primary, off, count, entry_end, names):
            return off
    return None


def parse_model_rows(
    primary: bytes,
    archive_hash: str,
    resource_hash: int,
    resource_name: str,
    entry_index: int,
    entry_abs: int,
    entry_size: int,
    names: dict[int, str],
) -> list[ModelRow]:
    entry_end = entry_abs + entry_size
    lookup_bytes = u64(primary, entry_abs + 0x08)
    lookup_count = u64(primary, entry_abs + 0x28)
    init_bytes = u64(primary, entry_abs + 0x68)
    init_count = u64(primary, entry_abs + 0x88)
    if lookup_bytes != lookup_count * LOOKUP_SIZE:
        raise ValueError(f"{archive_hash}/{resource_hash:016x}: bad lookup table size")
    if init_bytes != init_count * INITDATA_SIZE:
        raise ValueError(f"{archive_hash}/{resource_hash:016x}: bad init table size")
    if lookup_count != init_count:
        raise ValueError(f"{archive_hash}/{resource_hash:016x}: lookup/init count mismatch")

    _disable_words, startvisible_words, unpickable_words, _bitmap_end = read_bitmaps(primary, entry_abs)
    lookup_start = find_lookup_start(primary, entry_abs, entry_size, lookup_count, names)
    init_start = find_init_start(primary, lookup_start, lookup_count, init_count, entry_end)
    asset_cursor = init_start + init_count * INITDATA_SIZE

    rows: list[ModelRow] = []
    decoded_asset_lists: list[list[int]] = []
    use_properties_rows = False
    for row_index in range(init_count):
        init_off = init_start + row_index * INITDATA_SIZE

        asset_count = u64(primary, init_off + 0x28)
        if asset_cursor + asset_count * 8 > entry_end:
            raise ValueError(
                f"{archive_hash}/{resource_hash:016x}: asset table for row {row_index} "
                f"runs past entry end"
            )
        asset_hashes = [u64(primary, asset_cursor + i * 8) for i in range(asset_count)]
        asset_cursor += asset_count * 8
        decoded_asset_lists.append(asset_hashes)

    if not any(value not in (0, 0xFFFFFFFFFFFFFFFF) for assets in decoded_asset_lists for value in assets):
        properties_start = find_properties_start(primary, asset_cursor, init_count, entry_end, names)
        if properties_start is not None:
            asset_cursor = properties_start + init_count * PROPERTIES_SIZE
            decoded_asset_lists = []
            for row_index in range(init_count):
                props_off = properties_start + row_index * PROPERTIES_SIZE
                init_off = props_off + PROPERTIES_INIT_OFFSET
                asset_count = compact_asset_count(primary, init_off)
                if asset_cursor + asset_count * 8 > entry_end:
                    raise ValueError(
                        f"{archive_hash}/{resource_hash:016x}: compact asset table for row {row_index} "
                        f"runs past entry end"
                    )
                asset_hashes = [u64(primary, asset_cursor + i * 8) for i in range(asset_count)]
                asset_cursor += asset_count * 8
                decoded_asset_lists.append(asset_hashes)

            lookup_start = properties_start
            init_start = properties_start + PROPERTIES_INIT_OFFSET
            use_properties_rows = True

    for row_index, asset_hashes in enumerate(decoded_asset_lists):
        if use_properties_rows:
            lookup_off = lookup_start + row_index * PROPERTIES_SIZE
            init_off = lookup_off + PROPERTIES_INIT_OFFSET
        else:
            lookup_off = lookup_start + row_index * LOOKUP_SIZE
            init_off = init_start + row_index * INITDATA_SIZE
        component_name_hash = u64(primary, lookup_off)
        actor_node_hash = u64(primary, lookup_off + 0x08)
        actor_id = u16(primary, lookup_off + 0x10)
        num_pooled = u16(primary, lookup_off + 0x12)

        attach_mode = u32(primary, init_off + 0x38)
        max_attachments = u32(primary, init_off + 0x3C)
        transform_name = u64(primary, init_off + 0x40)
        attach_model = u64(primary, init_off + 0x48)
        attach_name = u64(primary, init_off + 0x50)
        scene_set_config_count = u64(primary, init_off + 0x80)
        flags = 0

        rows.append(
            ModelRow(
                archive_hash=archive_hash,
                resource_hash=hex64(resource_hash),
                resource_name=resource_name,
                entry_index=entry_index,
                entry_abs=f"{entry_abs:#x}",
                entry_size=f"{entry_size:#x}",
                row_index=row_index,
                component_name_hash=hex64(component_name_hash),
                component_name=resolved_name(names, component_name_hash),
                actor_node_hash=hex64(actor_node_hash),
                actor_node_name=resolved_name(names, actor_node_hash),
                actor_id=actor_id,
                num_pooled=num_pooled,
                flags=f"{flags:#x}",
                start_visible=bit_is_set(startvisible_words, row_index),
                unpickable=bit_is_set(unpickable_words, row_index),
                asset_hashes=",".join(hex64(value) for value in asset_hashes),
                asset_names=",".join(resolved_name(names, value) for value in asset_hashes),
                attach_mode=attach_mode,
                max_attachments=max_attachments,
                transform_name_hash=hex64(transform_name),
                transform_name=resolved_name(names, transform_name),
                attach_model_hash=hex64(attach_model),
                attach_model_name=resolved_name(names, attach_model),
                attach_name_hash=hex64(attach_name),
                attach_name=resolved_name(names, attach_name),
                scene_set_config_count=scene_set_config_count,
            )
        )
    return rows


def scan_archive(archive_hash: str, names: dict[int, str]) -> list[ModelRow]:
    primary_path = ARCHIVE_PRIMARY / archive_hash
    gpu_path = ARCHIVE_GPU / archive_hash
    if compressed_stub(primary_path) or compressed_stub(gpu_path):
        return []

    primary = load_decompressed(primary_path)
    gpu = load_decompressed(gpu_path)
    _primary_size, _gpu_size, data_off, header0_off = archive_offsets(primary, gpu)
    header0 = parse_header(primary, header0_off)
    rows: list[ModelRow] = []
    for resource_hash, (entry_index, pos, size) in sorted(resource_entries(primary, header0, names, MODEL_TYPE).items()):
        rows.extend(
            parse_model_rows(
                primary,
                archive_hash,
                resource_hash,
                resolved_name(names, resource_hash),
                entry_index,
                data_off + pos,
                size,
                names,
            )
        )
    return rows


def write_manifest(path: Path, rows: list[ModelRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(ModelRow.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_summary(path: Path, scanned_archives: int, rows: list[ModelRow], errors: list[tuple[str, str]]) -> None:
    archives_with_rows = sorted({row.archive_hash for row in rows})
    resources = sorted({(row.archive_hash, row.resource_hash) for row in rows})
    asset_counts: dict[str, int] = {}
    named_assets = 0
    visible_rows = 0
    for row in rows:
        if row.start_visible:
            visible_rows += 1
        for asset in row.asset_hashes.split(","):
            if not asset or asset in NULL_HASHES:
                continue
            asset_counts[asset] = asset_counts.get(asset, 0) + 1
            if row.asset_names:
                named_assets += 1
    summary = {
        "scanned_archives": scanned_archives,
        "archives_with_model_rows": len(archives_with_rows),
        "model_resources": len(resources),
        "model_rows": len(rows),
        "start_visible_rows": visible_rows,
        "unique_non_null_asset_hashes": len(asset_counts),
        "top_asset_reference_counts": dict(sorted(asset_counts.items(), key=lambda item: item[1], reverse=True)[:50]),
        "errors": [{"archive_hash": archive_hash, "error": message} for archive_hash, message in errors],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hash", action="append", dest="hashes", help="Archive hash to scan")
    parser.add_argument("--all", action="store_true", help="Scan all paired archive files")
    parser.add_argument("--limit-archives", type=int, help="Limit scanned archives")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY)
    parser.add_argument("--hash-lookup", type=Path, default=DEFAULT_HASH_LOOKUP)
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    names = load_hash_lookup(args.hash_lookup)
    archives = archive_hashes(args.hashes, args.all, args.limit_archives)
    all_rows: list[ModelRow] = []
    errors: list[tuple[str, str]] = []
    for index, archive_hash in enumerate(archives, 1):
        try:
            rows = scan_archive(archive_hash, names)
            all_rows.extend(rows)
            if not args.quiet and (rows or index == len(archives) or index % args.progress_every == 0):
                print(f"[{index}/{len(archives)}] {archive_hash}: {len(rows)} model row(s)")
        except Exception as exc:  # noqa: BLE001 - diagnostic scanner should keep broad scans moving.
            message = str(exc)
            errors.append((archive_hash, message))
            if not args.quiet:
                print(f"[{index}/{len(archives)}] {archive_hash}: ERROR {message}")

    write_manifest(args.manifest, all_rows)
    write_summary(args.summary, len(archives), all_rows, errors)
    print(f"wrote {len(all_rows)} model row(s) from {len(archives)} archive(s) to {args.manifest}")
    if errors:
        print(f"errors={len(errors)}; see {args.summary}")


if __name__ == "__main__":
    main()
