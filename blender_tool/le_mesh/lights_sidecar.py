"""Wrap a raw light PROBE dump into the `le_lights` sidecar the add-on reads.

The extractor writes a v2 `le_lights` sidecar directly; the standalone probes in
`scratchpad/` (`le_lights_probe.py`) write the decoded `SGLightParams` fields
straight out as a list of archive dicts, which
`lone_echo_import.light_import.load_lights` rejects (`format != "le_lights"`).
This module is the offline adapter, so a level whose lights were probed but never
re-extracted can still be lit without touching an archive:

    python3 blender_tool/le_mesh/lights_sidecar.py \
        --probe scratchpad/lights_bridge_night.json \
        --out blender_tool/exports/bridge/lights_night.json

⚠ It CONVERTS NOTHING. Every field is copied through verbatim; the only work is
re-shaping the two option fields into the pair the sidecar contract specifies
(`options` = the name list, `options_raw` = the raw word) and stamping the record
index. If a probe record is missing a field, the sidecar is missing it too — this
adapter never invents a default for a decoded value.

Pure stdlib. Both the probe dumps and the sidecars are a few hundred KB.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SIDECAR_FORMAT = "le_lights"
SIDECAR_VERSION = 2

# ELightOptions bit names, low bit first (the engine's own type names). Kept in this order so the
# emitted `options` list reads like the extractor's.
OPTION_NAMES = [
    "eEnableDiffuse", "eEnableSpecular", "eCastShadows", "eCastLevelShadows",
    "eCastActorShadows", "eLightTransparents", "eLightOpaques", "eLightParticles",
    "eLightEnabled", "eUseLightShaft", "eUseLightShaftShadows", "eUseFog",
    "eBakeDirect", "eBakeIndirect", "eUseNonUniformFog", "eCastOpaqueShadows",
    "eCastAlphaTestShadows", "eCastTransparentShadows", "eBakeOnlyIrradiance",
    "eDontBakeIrradiance", "ePrimaryDirLight", "eEyesOnlyLight", "eBakeShadow",
    "eLightVolumetrics", "eCastAllLevelShadows",
]
OPTION_BITS = {n: 1 << i for i, n in enumerate(OPTION_NAMES)}

eEnableDiffuse = OPTION_BITS["eEnableDiffuse"]
eEnableSpecular = OPTION_BITS["eEnableSpecular"]
eLightEnabled = OPTION_BITS["eLightEnabled"]

LIGHT_TYPE_NAME = {0: "ePointLight", 1: "eSpotLight", 2: "eDirectionalLight"}


def option_names(word: int) -> list:
    return [n for n, b in OPTION_BITS.items() if word & b]


def options_word(rec) -> int:
    """The raw option word from whichever of the probe's two spellings is present."""
    for key in ("options_raw", "options"):
        v = rec.get(key)
        if isinstance(v, int):
            return v
    names = rec.get("options_names") or rec.get("options") or []
    if isinstance(names, str):
        names = [n for n in names.split("|") if n]
    return sum(OPTION_BITS.get(n, 0) for n in names)


def normalize_record(rec: dict, index: int) -> dict:
    """One probe light -> one sidecar light. Copy-through plus the option pair."""
    out = dict(rec)
    word = options_word(rec)
    out.pop("options_names", None)
    out["options_raw"] = word
    out["options"] = option_names(word)
    out["index"] = int(rec.get("index", index))
    lt = rec.get("lighttype")
    out["type"] = rec.get("lighttype_name") or LIGHT_TYPE_NAME.get(lt, "")
    return out


def _iter_archives(doc):
    """The probe writes either one archive dict or a list of them."""
    return doc if isinstance(doc, list) else [doc]


def sidecar_from_probe(doc, archive: str = None, source: str = "probe") -> dict:
    """Build the `le_lights` v2 sidecar from a raw probe dump.

    `archive` selects one archive when the dump holds several; with a single
    archive it is optional and the dump's own hash is used.
    """
    archives = [a for a in _iter_archives(doc)
                if archive is None or a.get("archive") == archive]
    if not archives:
        have = [a.get("archive") for a in _iter_archives(doc)]
        raise ValueError(f"archive {archive!r} not in probe dump (have {have})")
    if len(archives) > 1:
        raise ValueError(f"probe dump holds {len(archives)} archives; pass --archive")
    arc = archives[0]

    scenes = []
    by_type: dict = {}
    n_lights = diffuse = specular = enabled = 0
    for s in arc.get("scenes", []):
        recs = [normalize_record(r, i) for i, r in enumerate(s.get("lights", []))]
        scenes.append({
            "scene_hash": s.get("scene_hash", ""),
            "scene_name": s.get("scene_name", ""),
            "num_lights": len(recs),
            "lights": recs,
        })
        for r in recs:
            n_lights += 1
            w = r["options_raw"]
            by_type[r["type"]] = by_type.get(r["type"], 0) + 1
            diffuse += 1 if w & eEnableDiffuse else 0
            specular += 1 if w & eEnableSpecular else 0
            enabled += 1 if w & eLightEnabled else 0

    return {
        "format": SIDECAR_FORMAT,
        "version": SIDECAR_VERSION,
        "archive": arc.get("archive", ""),
        "source": source,
        "axis": "native",
        "record": "SGLightParams/352",
        "summary": {
            "scenes": len(scenes),
            "scenes_with_lights": sum(1 for s in scenes if s["num_lights"]),
            "lights": n_lights,
            "by_type": by_type,
            "enabled": enabled,
            "diffuse_enabled": diffuse,
            "specular_enabled": specular,
            "specular_only": specular - sum(
                1 for s in scenes for r in s["lights"]
                if (r["options_raw"] & eEnableSpecular)
                and (r["options_raw"] & eEnableDiffuse)),
        },
        "scenes": scenes,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--probe", required=True, help="raw probe JSON dump")
    ap.add_argument("--archive", default=None, help="archive hash to take")
    ap.add_argument("--out", required=True, help="lights.json sidecar to write")
    args = ap.parse_args(argv)

    doc = json.loads(Path(args.probe).read_text(encoding="utf-8"))
    sc = sidecar_from_probe(doc, args.archive, source=f"probe:{Path(args.probe).name}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(sc, indent=1), encoding="utf-8")
    print(f"{sc['archive']}: {sc['summary']}")
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
