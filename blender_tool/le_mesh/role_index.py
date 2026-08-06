"""Corpus-wide `texture_hash -> role` index and the policy for applying it.

Pure stdlib, so the POLICY is unit-testable without a game file. The producer is
`scripts/le_role_index.py` (149 archives, Windows Python + Oodle); this
module only reads its TSV and decides what may be believed.

★ WHY A CORPUS INDEX AT ALL.

The role of a texture bind exists in exactly one place — the `inputname` field of
an `SShaderInputData` row. Some shadersets ship no such array (4 of 17 in Liv's
archive, docs/MATERIALS.md §1) and are read
through their DXBC `RDEF` chunk instead, which names the texture but not the
role. `Archive._ensure_role_by_tex` recovers the role by propagating it from a
sibling shaderset that DOES declare the same texture — but only inside one
archive, which is why it reaches 9/9 roles in a 259-shaderset archive and 4/15 in
a 17-shaderset one. This index is that same propagation over all 149 archives
that can hold a shaderset.

★ WHY IT IS ONLY *MOSTLY* SOUND — measured, not assumed.

`generic_rebuilds/role_index.tsv`, 25,694 binds / 138 archives / 4,507
shadersets, 2,194 distinct textures (`stream-confirmed`, 2026-08-04):

| | count | of the 1,665 textures declared by >1 shaderset |
|---|---:|---:|
| textures carrying more than one role | 90 | 5.41 % |
| ... disagreeing only in the LAYER INDEX | 74 | 4.44 % |
| ... disagreeing in the SUFFIX | **16** | **0.96 %** |

The two classes are not equally dangerous and are not the same phenomenon:

* **layer-index conflicts are benign and expected.** 50 of the 74 are
  `generated_composite_*` — the cook's per-material composite atlases — used as
  `layer0_composite_diffuse` by one material and `layer1_composite_diffuse` by
  another. The Principled channel is chosen by the SUFFIX
  (`materials.CHANNEL_ROLE_SUFFIXES`), so the texture reaches the right socket
  either way; only the layer-compositing weight can be misattributed.
* **suffix conflicts are real authored ambiguity, not decode error.** All 16 are
  named, and every one is a reusable single-channel utility map — e.g.
  `fx_cmn_scrolling_noise_swirls_liquid_clr` bound as albedo / alpha / blend
  mask / emissive by 38 different shadersets, `mfx_water_runoff_sheet_b_nml` as
  normal / flowmap / `pom_height_map` by 24. A greyscale noise plate genuinely IS
  a different thing in each material, so no amount of extra evidence resolves it.
  ★ **Zero of the 16 is a `generated_composite_*`** — the class that carries the
  binds we are actually trying to recover is exactly the class that never
  disagrees on its suffix.

Hence the policy below: **the suffix must be unanimous, or nothing is applied.**
"""

from __future__ import annotations

import csv
from pathlib import Path

from .materials import split_role

#: what produced a role key, recorded per binding so provenance stays auditable
SOURCE_ARRAY = "array"        # this shaderset's own SShaderInputData row
SOURCE_ARCHIVE = "archive"    # propagated from a sibling in the SAME archive
SOURCE_CORPUS = "corpus"      # propagated from the corpus index (this module)
SOURCE_FORMAT = "format"      # no array names it anywhere; the composite atlas's
                              # DXGI format + resolution group does
                              # (`materials.composite_roles_from_format`)
SOURCE_RDEF = "rdef"          # RDEF knew the texture, nothing knew the role
SOURCE_LOD_SIBLING = "lod_sibling"
"""Propagated from a shaderset that is the SAME MATERIAL at another LOD.

★ 2026-08-05. A character ships each LOD as its own mesh with its own
SHADERSET, but the two share one `material_hash` -- they are one authored
material compiled twice. `liv_head` is the case that exposed it: the LOD-1 skin
shaderset `b149f66575443907` ships an `SShaderInputData` array declaring
`layer0_thickness_mask` and `layer0_detail_normal_map`, and the LOD-0 skin
shaderset `c8deda534cc6f28b` -- the one every render actually draws -- ships NO
array at all, so eight of its binds (`liv_head_thk`, `liv_head_tertiary_nml`,
`liv_head_wm{1,2,3}_{msk,nml}`) landed as `rdef_bind23..30`.

The join is TIGHT, which is why this is not the corpus-wide propagation that
docs/MATERIALS.md closed as a negative for Liv's body: it fires
only when (a) the two shadersets carry the SAME `material_hash`, (b) the
DONOR's role came from its own array, and (c) the same TEXTURE HASH is bound by
both. Same material, same texture, one array -- there is nothing left to vote on.
Anything the donor did not declare stays `rdef_bind{n}`."""

#: lookup outcomes
STATUS_UNANIMOUS = "unanimous"              # one role corpus-wide
STATUS_LAYER_AMBIGUOUS = "layer_ambiguous"  # one suffix, several layer indices
STATUS_SUFFIX_CONFLICT = "suffix_conflict"  # several suffixes — REFUSED
STATUS_ABSENT = "absent"                    # no array anywhere declares it

#: statuses whose role may be applied
APPLICABLE = frozenset({STATUS_UNANIMOUS, STATUS_LAYER_AMBIGUOUS})


class RoleIndex:
    """`tex_hash -> role`, with the disagreement kept rather than averaged away.

    `entries[tex_hash]` is `{role_key: n_declaring_shadersets}` — the vote is by
    DISTINCT shaderset, not by row, so one shaderset duplicated into 30 archives
    cannot outvote five genuinely different ones.
    """

    __slots__ = ("entries", "_resolved")

    def __init__(self, entries: dict[str, dict[str, int]] | None = None) -> None:
        self.entries: dict[str, dict[str, int]] = entries or {}
        self._resolved: dict[str, tuple[str | None, str]] = {}

    def __len__(self) -> int:
        return len(self.entries)

    def __contains__(self, tex_hash: str) -> bool:
        return (tex_hash or "").lower() in self.entries

    # -- the policy ---------------------------------------------------------
    def resolve(self, tex_hash: str) -> tuple[str | None, str]:
        """`(role_key_or_None, status)`.

        * one role corpus-wide                -> that role, `unanimous`
        * one SUFFIX, several layer indices   -> the majority layer,
          `layer_ambiguous` (the suffix is what picks the Principled channel, so
          the channel is right either way; the layer is a recorded best-of)
        * several suffixes                    -> **None**, `suffix_conflict`.
          ⛔ Never guess here: the 16 corpus cases are all reusable greyscale
          utility maps whose meaning is genuinely per-material.
        * unknown texture                     -> None, `absent`
        """
        th = (tex_hash or "").lower()
        hit = self._resolved.get(th)
        if hit is not None:
            return hit
        roles = self.entries.get(th)
        if not roles:
            out: tuple[str | None, str] = (None, STATUS_ABSENT)
        elif len(roles) == 1:
            out = (next(iter(roles)), STATUS_UNANIMOUS)
        elif len({split_role(r)[1] for r in roles}) > 1:
            out = (None, STATUS_SUFFIX_CONFLICT)
        else:
            # majority layer; ties break on the LOWEST layer index so the answer
            # is deterministic and does not depend on TSV row order.
            best = max(roles.items(), key=lambda kv: (kv[1], -split_role(kv[0])[0]))
            out = (best[0], STATUS_LAYER_AMBIGUOUS)
        self._resolved[th] = out
        return out

    def roles_for(self, tex_hash: str) -> dict[str, int]:
        """Every role the corpus ever gave this texture, with its vote count."""
        return dict(self.entries.get((tex_hash or "").lower(), {}))

    # -- soundness readout, used by the builder and by tests ----------------
    def stats(self) -> dict[str, int]:
        multi = [r for r in self.entries.values() if len(r) > 1]
        suffix = [r for r in multi if len({split_role(x)[1] for x in r}) > 1]
        return {
            "textures": len(self.entries),
            "pairs": sum(len(r) for r in self.entries.values()),
            "multi_role": len(multi),
            "suffix_conflict": len(suffix),
            "layer_only": len(multi) - len(suffix),
        }


def index_from_rows(rows) -> RoleIndex:
    """Build from an iterable of dicts with `tex_hash`, `role`, `shaderset_hash`.

    Votes are per distinct shaderset: the same shaderset resource is byte-shared
    across archives, so counting rows would weight it by how many archives happen
    to embed it.
    """
    seen: dict[str, dict[str, set[str]]] = {}
    for row in rows:
        tex = (row.get("tex_hash") or "").lower()
        role = row.get("role") or ""
        if not tex or not role:
            continue
        seen.setdefault(tex, {}).setdefault(role, set()).add(
            (row.get("shaderset_hash") or "").lower())
    return RoleIndex({t: {r: len(s) for r, s in by_role.items()}
                      for t, by_role in seen.items()})


def load_role_index(path: Path) -> RoleIndex:
    """Read `role_index.tsv`. An ABSENT file yields an empty index, never an error.

    Same contract as `le_extract.load_global_texture_index` /
    `load_global_material_index`: the extractor must keep working in a tree that
    has not built the artifact yet — it simply resolves fewer roles.
    """
    p = Path(path)
    if not p.exists():
        return RoleIndex()
    with p.open(encoding="utf-8", newline="") as fh:
        return index_from_rows(csv.DictReader(fh, delimiter="\t"))
