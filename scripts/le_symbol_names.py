"""CSymbol64 name-hash algorithm.

Hashes a candidate string to the 64-bit value the engine uses for resource and
shader-input names, so a known name can be matched against an on-disk name hash
(e.g. to crack a shaderset input-name hash from a PBR wordlist). `symbol64` is
case-insensitive: ASCII letters are lower-cased before hashing.
"""

from __future__ import annotations

MASK = 0x95AC9329AC4BC9B5


def init_seeds() -> list[int]:
    """Build the 256-entry substitution table the hash steps through."""
    seeds: list[int] = []
    for i in range(256):
        value = 0x2B5926535897936A if (i & 0x80) else 0
        if i & 0x40:
            value ^= MASK
        shift = 0x20
        while shift:
            value = (2 * value) & 0xFFFFFFFFFFFFFFFF
            if i & shift:
                value ^= MASK
            shift >>= 1
        seeds.append((2 * value) & 0xFFFFFFFFFFFFFFFF)
    return seeds


SEEDS = init_seeds()


def symbol64(text: str) -> str:
    """Return the 16-hex-digit CSymbol64 hash of `text` (case-insensitive)."""
    result = 0xFFFFFFFFFFFFFFFF
    for byte in text.encode("utf-8", "ignore"):
        if 0x41 <= byte <= 0x5A:                 # upper-case ASCII -> lower
            byte += 0x20
        result = (((result << 8) & 0xFFFFFFFFFFFFFFFF) ^ SEEDS[(result >> 56) & 0xFF] ^ byte)
    return f"{result & 0xFFFFFFFFFFFFFFFF:016x}"
