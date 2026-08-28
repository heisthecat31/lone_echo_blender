"""`CScriptCR` -- which SCRIPT each actor runs.

## What it is

`CScriptCRWin10` = `d99f6bbd8009c92c`, confirmed by forward-hashing:
`CSymbol64("CScriptCR" + "Win10")` lands exactly on the directory name. Its
sibling `CScriptResourceWin10` = `991421b41de58370` holds the scripts
themselves -- or rather, does not: all 567 of them are **4 bytes of zeros**.

⭐ The bodies live in the GAME INSTALL, not the asset extract. Each script is a
compiled native DLL at

    <install>/bin/win10/scripts/<script hash>.dll

and **546 of the 567 script resources (96%) have one**, 20 KB to 971 KB each.
That overlap is the strongest confirmation of this decode: the `+0x18` field
was identified purely from the asset side, and 96% of the values it produces
turn out to name a real compiled artefact on the other side of the game.

So a script's identity is its hash, and that hash is the `CSymbol64` of its
authored NAME. Anyone chasing what a script actually DOES -- mover timing, for
instance -- has the machine code available at the path above.

## Framing

Standard CR framing, with one trap:

    +0x08  u64  table byte size
    +0x28  u64  record count       (mirrored at +0x30)
    stride      720 bytes

⛔ The record block is NOT aligned to `len(file) - size`. Measured across four
levels, the actor field lands at a DIFFERENT offset in that block every time
(`+0x2b8` on mpl_lobby_b_combat, `+0x0c0` on mpl_lobby_b_arena, `+0x148` on
mpl_arena_a, `+0x1e0` on mpl_lobby_b2), so a fixed base yields garbage. What is
constant is the record's internal layout. The give-away that the layout really
is fixed: whatever offset the actor lands on, the script hash is always exactly
`+0x18` after it.

    record  +0x00  u64  ACTOR nodeid
            +0x18  u64  CScriptResource hash

So records are located as a LATTICE (see `_anchor`) rather than from a base
pointer. That distinction is not cosmetic: reading from `len - size` decoded
391 of `mpl_lobby_b2`'s 477 records, because the `+0x18` script field pushes
the last record past the declared block. On the lattice every level resolves
its full declared count -- 477/477, 375/375, 97/97 and 289/289 on the four
levels above.

## Names

`data/script_names.json` carries **93 of the 567** preimages (16%), from four
independent sources, which is why they can be trusted:

  1. the existing `hash_lookup` dictionary -- `tut_movement`, `tut_boost`,
     `tut_air_brake`, `tut_micro_thrusters`, `mp_combat_ability_barrier`,
     `mp_gbl_player_lock_manager`, `mp_combat_server_state`, `Empty`;
  2. `<install>/bin/win10/db/symbols.json`, the game's own 8064-entry name
     table -- it covers only 2 script hashes, but agrees with both and
     **conflicts with none** of the names cracked another way;
  3. identifiers harvested from the 546 script DLLs themselves, which yielded
     `mpl_mm_podium`, `ui_mp_helmet_meter_combat`, `mp_match_statistics`,
     `mp_spectator_minimap_functions` and others;
  4. iterating `prefix_token[_token]` generation over that vocabulary until it
     stopped finding anything -- `mp_combat_grenade_{arc,core,dot,stun}`,
     `mp_combat_level_{combustion,dyson,fission,gauss}`, `mp_door_timer`,
     `mp_frisbee_{light,outline,respawn,sounds}`, `mpl_sim_hera`,
     `ui_ar_objective_marker`, `mp_arena_{disc,goal,logic,music}` ...

⚠ `echovr.exe` itself is NOT a useful source: harvesting all six binaries in
`bin/win10` yields 181,883 strings and cracks **zero** script hashes -- only 1
of the 27 names known at the time appears in them at all. The names are hashed
at compile time, so the vocabulary has to come from the script DLLs.

A test asserts every stored name hashes back onto its own key, so a bad entry
cannot sit in the file unnoticed.

## The record body: 12 empty binding slots

Beyond `+0x00` and `+0x18` the 720-byte record is a fixed array of **12 slots
of 56 bytes** (`0x38`), each holding `count = 1` and `size = 0x20` and nothing
else -- the first slots start at `+0x40`, `+0x78`, `+0x b0`, `+0xe8`, ... The
non-zero mask is IDENTICAL across all 10 records of `mpl_arena_a`'s catapult
script, so the record carries **no per-instance parameter values at all**. A
script's tunables are compiled into its DLL, not authored per placement.

That is what rules out reading mover timing from here (see below).

## Reading what a script DOES

`setup_bindings` is every script DLL's only export, and its string literals are
the script's binding names -- lowercase, underscore-separated, sitting in
`.rdata`. `binding_names()` returns them, and they identify a script's job even
when its own name is unrecovered:

    cea83dc626035076  evt_catapult_launched, evt_catapult_returned,
                      evt_launcher_btn_pressed, launcher_at_base,
                      ring_bright_mul, orange_team, blue_team, team_index
                      -> mpl_arena_a's CATAPULT / launcher

    39978d1add3719db  outer_door_open, launch_btn, btn_update_on/off
    9a69cb25110dbd29  enable_slave_timers, disable_slave_timers
    31166977834d8960  evt_timer_countdown, timer_text, is_visible, duration
                      -> `mp_door_timer`, a door plus its countdown display

⚠ A DLL never contains its OWN name: checked all 93 names known at the time,
in underscore, stripped and raw forms -- **0 of 93** appear anywhere in their
own binary. The export is always literally `setup_bindings`, and the PDB path
carries a hash, not a name. So the DLLs give vocabulary and semantics, never
the name directly.

## ⛔ What is NOT in here

**No art, and no timing.** Every u64 in every record was checked against the
full corpus of model, material and shaderset hashes: over 1141 records x 90
fields (~103,000 slots) on `mpl_lobby_b2`, `mpl_lobby_b_combat` and
`mpl_arena_a`, the ONLY things that ever resolve are actor nodeids and script
resources. Zero models, zero materials, zero shader sets.

So `CScriptCR` does NOT answer either of the questions it was suspected of:

  * it does not carry the placeholder -> dressed-art link
    (`evr_materials.dressed_geometry_twin` remains a geometry reconstruction);
  * it names no mover schedule, and cannot: 30 of `mpl_arena_a`'s 50 movers
    DO run a script (11 of them `mp_door_timer`, 10 the catapult), so the
    association is real -- but the records hold no values, only empty slots.
    The schedule lives in the script DLL's machine code, so `evr_movers`'
    "timing is a placeholder" caveat still stands and would need
    disassembly, not another component, to lift.

What it DOES give is behaviour attribution: which actor runs which named
script. Records average ~2.7 actor references each, so beyond `+0x00` a record
also points at other actors -- presumably the script's own parameters. Those
extra slots are deliberately left undecoded rather than guessed at.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

#: `CScriptCRWin10` -- the per-actor script binding.
SCRIPT_CR = "d99f6bbd8009c92c"
#: `CScriptResourceWin10` -- 4-byte stubs; the hash is the whole payload.
SCRIPT_RESOURCE = "991421b41de58370"

RECORD_STRIDE = 720
#: Offsets WITHIN a record, once it has been anchored on the actor column.
R_ACTOR = 0x00
R_SCRIPT = 0x18

_NAMES: dict = {}


def _normalise(value) -> str:
    return "%016x" % (int(value) & 0xFFFFFFFFFFFFFFFF)


def script_names() -> dict:
    """`{script hash -> authored name}` for the preimages recovered so far."""
    if not _NAMES:
        path = Path(__file__).resolve().parent.parent / "data" / "script_names.json"
        try:
            _NAMES.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            _NAMES["__empty__"] = ""
    return {k: v for k, v in _NAMES.items() if k != "__empty__"}


def _anchor(blob: bytes, count: int, known_actors) -> int | None:
    """Byte offset of the FIRST record, or None.

    ⭐ Records are found as a LATTICE, not from a base pointer. The block is
    not aligned to `len - size` (see the module docstring), and because the
    script field sits `+0x18` after the actor, records straddle whatever
    boundary that base implies -- anchoring on the base drops the last record
    and depends on an offset the file never states.

    So this scores each of the 90 possible phases `offset % 720` over the WHOLE
    file by how many actor nodeids land on it, takes the winner, and walks that
    lattice from its first position. A phase that resolves on fewer than a
    fifth of `count` is not the actor column, and the file is rejected rather
    than read as noise.
    """
    best_phase, best_hits = None, 0
    for phase in range(0, RECORD_STRIDE, 8):
        hits = 0
        position = phase
        while position + 8 <= len(blob):
            if struct.unpack_from("<Q", blob, position)[0] in known_actors:
                hits += 1
            position += RECORD_STRIDE
        if hits > best_hits:
            best_phase, best_hits = phase, hits
    if best_phase is None or best_hits < max(1, count // 5):
        return None
    return best_phase


def read_scripts(root: Path, members, known_actors) -> list:
    """`[{actor, script, name, level}, ...]` for a scene group.

    `known_actors` is required, not optional: it is what locates the record
    block (see `_anchor`), so there is no way to read this component without
    the level's actor table.
    """
    out: list = []
    for member in members:
        path = Path(root) / SCRIPT_CR / member
        if not path.exists():
            path = path.with_suffix(".bin")
        try:
            blob = path.read_bytes()
        except OSError:
            continue
        if len(blob) < 0x38:
            continue
        size = struct.unpack_from("<Q", blob, 0x08)[0]
        count = struct.unpack_from("<Q", blob, 0x28)[0]
        if not count or not size or size != count * RECORD_STRIDE:
            continue
        if len(blob) - size < 0:
            continue
        base = _anchor(blob, count, known_actors)
        if base is None:
            continue
        names = script_names()
        for i in range(count + 2):          # the lattice bounds itself below
            record = base + i * RECORD_STRIDE
            if record + R_SCRIPT + 8 > len(blob):
                break
            actor = struct.unpack_from("<Q", blob, record + R_ACTOR)[0]
            if actor not in known_actors:
                continue
            script = _normalise(
                struct.unpack_from("<Q", blob, record + R_SCRIPT)[0])
            if script in ("0000000000000000", "ffffffffffffffff"):
                continue
            out.append({
                "actor": _normalise(actor),
                "script": script,
                "name": names.get(script),
                "level": member,
            })
    return out


def scripts_for(root: Path, scene_group, known_actors) -> dict:
    """`{actor -> [script, ...]}`, merged across a scene group."""
    result: dict = {}
    for row in read_scripts(root, scene_group, known_actors):
        slot = result.setdefault(row["actor"], [])
        if row["script"] not in slot:
            slot.append(row["script"])
    return result


#: Where the compiled script bodies live, relative to the game install root.
SCRIPT_DLL_SUBPATH = "bin/win10/scripts"

#: Binding-table boilerplate every script exports; never script-specific.
_BINDING_NOISE = frozenset({
    "setup_bindings", "varname", "value", "value_out", "loglevel", "message",
    "userdata", "self", "componentname", "itemname", "name", "actor",
    "component", "levelname", "output", "output2", "enable", "type",
    "gamespace", "colorspace", "temp", "invalid", "server", "peer",
})


def binding_names(install_dir, script_hash) -> list:
    """The binding identifiers a script's DLL declares, or `[]`.

    `install_dir` is the game install root (the folder holding `bin/win10`).
    Boilerplate shared by every script is filtered out, so what comes back
    describes THIS script -- see the module docstring for worked examples.
    """
    import re

    path = (Path(install_dir) / SCRIPT_DLL_SUBPATH
            / (str(script_hash).lower() + ".dll"))
    try:
        blob = path.read_bytes()
    except OSError:
        return []
    runs = {m.group().decode("ascii", "replace")
            for m in re.finditer(rb"[ -~]{3,120}", blob)}
    return sorted(r for r in runs
                  if re.fullmatch(r"[a-z0-9_]{3,40}", r)
                  and r not in _BINDING_NOISE)


def main(argv=None) -> int:
    import argparse
    import collections
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    import evr_actor_data                                     # noqa: E402
    import evr_scene_extract as se                            # noqa: E402

    parser = argparse.ArgumentParser(
        description="Decode CScriptCR: which script each actor runs.")
    parser.add_argument("level", help="level hash")
    parser.add_argument("--dir", required=True, help="flat extract root")
    args = parser.parse_args(argv)

    root = Path(args.dir)
    actor_path = se.resource_path(root, se.DIR_ACTOR_DATA, args.level)
    if actor_path is None:
        raise SystemExit(f"no actor data for level {args.level} under {root}")
    actors = evr_actor_data.parse(actor_path.read_bytes())["actors"]
    known = {a["nodeid"] for a in actors}

    rows = read_scripts(root, [args.level.lower()], known)
    tally = collections.Counter(r["name"] or r["script"] for r in rows)
    print("%d script binding(s) over %d actor(s), %d distinct script(s)"
          % (len(rows), len({r["actor"] for r in rows}),
             len({r["script"] for r in rows})))
    named = sum(1 for r in rows if r["name"])
    print("%d binding(s) resolve to an authored name" % named)
    for label, n in tally.most_common(40):
        print("   %-40s %d actor(s)" % (label, n))
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
