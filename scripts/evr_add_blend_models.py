"""Graft Blender geometry, textures and lights into a shipped Echo VR level.

The non-Blender half of the add-on's "Add Models to Level" button. It takes the
geometry the add-on exported (`evr_add_models.export_selection`) and grows the
level's tables so the engine loads and draws it.

    python scripts/evr_add_blend_models.py --geo <dir> --level <hash>
                                           --dir <extract> --out <input-pcvr>

## What the engine demands, and what it costs to get wrong

Everything below was paid for once, in load failures that all looked identical:
`Offset is past the end of the stream` on `CStaticInstanceModelCR`. None of them
is checkable from inside the file -- every one still walks end to end, still
round-trips byte-identically, still has every count and index in range.

* **A cloned donor row carries fields that must be recomputed.** Six of them:
  the transform CR's twin row count at +48 (it sizes the attach); the asset's
  `(start, count)` run into `shadersetoverrides` (the 98 shipped runs PARTITION
  that section exactly, and no shipped asset has count 0, so a new asset needs a
  real override of its own); the instance slot index (a permutation of
  `0..n-1`); the CSIMCR record's entity at +8; and the CSIMCR's `gap` and `pad`.
  All fixed in `evr_level_grow`; this module only has to call it.
* **`assetdata` / `instancedata` / `meshdata` are signed-i64 sorted and BINARY
  SEARCHED.** Append instead of insert and the engine reports
  `Level static instance data has no info for instanced model asset <hash>`.
* **`gap` and `pad` are derived.** `gap = ceil(n/64)*8` is a per-instance bitmap;
  `pad = (-idx_end) % 8` realigns `[mid]` and `[recs]`. `[u16]` is 2 bytes per
  instance, so the PARITY of `n` decides the alignment -- one added instance
  flips a level with `pad = 0` to `pad = 6`. Both rules hold on 25 of 25 shipped
  files.
* **Three more CRs carry a row per static instance** and the model chain grows
  none of them: `b76203b6e5eaff80` (exactly one row per instance),
  `ca5a03d5a497238c` and `142026f469321d54`. Found generically here rather than
  hard-coded -- a CR is `size@+8 == n*stride` with matching counts at +40/+48,
  and the entity column is whichever 8-byte column holds the donor's entity.
* **POINT lights are capped at 110 per level.** Four shipped levels sit at
  exactly 110 while their spot and sun counts differ, and none exceeds it. The
  arena is already at the cap, so extra fill lights have to be SPOT.
* **Only `type >= 2` (SUN) shades dynamic geometry.** POINT and SPOT feed the
  static bake, and grafted geometry has no lightmap, so a SUN is what actually
  lights it.

## What is still a workaround

⚠ `--visibility counted` replaces the level's precomputed visibility set with
the counted-92 form. Without it the new instances are culled everywhere and
never draw -- but it also throws away the level's own culling, so the map stops
going black when you leave it. The real fix is to author the new instances into
the scene resource's sec25 boxtree and into a real PVS; nothing here does that.

⚠ `--lod-scale` writes a `loddistancescales` no shipped level uses (they are all
1.0). It holds grafted geometry at full detail from any distance, which it needs
only because it is outside the boxtree. Leave it at 1.0 once the boxtree is
authored properly.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parent
for _p in (str(_SCRIPTS), str(_ROOT), str(_ROOT / "blender_tool"),
           r"J:\EchoVR-Tools-Launcher\quest_combat_port\tools",
           r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import evr_paths                                              # noqa: E402
from le_symbol_names import symbol64                          # noqa: E402

T_TEX, T_TEX_GPU = "4a4c32c49300b8a0", "beac1969cb7b8861"
T_SCENE, T_MAT = "a388ea69e5108f4c", "e3e0266f1911dafa"
T_CSIMCR, T_CGSI = "263584544abbd56c", "77c0bf257ca92aa0"
T_VIS = "73d312a620da3824"
#: Everything the model chain reads or grows.
LEVEL_TYPES = ("347869ce492dc7da", "92abd3e1432bf5e8", T_CSIMCR, T_CGSI,
               "dd3ff9850e4eed35", "2a41cf1c1d9e5d32", "b7d338793fa37832",
               "358b53c17825d154", T_SCENE)
#: CRs the chain already grows -- `grow_entity_crs` must not add a second row.
ALREADY_GROWN = set(LEVEL_TYPES)

#: `cgtextureresourceWin10` stream-0 field offsets (`evr_texture_resource`).
TEX_F = dict(streamingdisabled=0xC0, maxwidth=0xC4, maxheight=0xC8,
             maxmipcount=0xCC, arraysize=0xD0, cubemap=0xD4, format=0xD8,
             srgb=0xDC, createasarray=0xE0, volume=0xE4, width=0xE8, height=0xEC,
             mipcount=0xF0, resmemsize=0xF4, pitch=0xF8, padding=0xFC)

#: `SGLightParams` (`evr_lights`).
L_STRIDE = 360
L = dict(type=0x04, name=0x08, position=0x10, color=0x1C, intensity=0x28,
         range=0x2C, spot_angle=0x44, direction=0x54, cos_inner=0x60,
         cos_outer=0x64, owner=0x158)
POINT, SPOT, SUN = 0, 1, 2
NULL = 0xFFFFFFFFFFFFFFFF
#: No shipped level exceeds this many POINT lights, and four sit exactly on it.
POINT_CAP = 110


# ---------------------------------------------------------------------------
def _read_bin(path: Path, fmt: str, group: int = 1):
    raw = path.read_bytes()
    flat = struct.unpack("<%d%s" % (len(raw) // 4, fmt), raw)
    if group == 1:
        return list(flat)
    return [flat[i:i + group] for i in range(0, len(flat), group)]


U16_LIMIT = 65535


def split_u16(pos, nrm, uv, idx, limit=60000):
    """`[(pos, nrm, uv, idx), ...]`, each addressing at most `limit` vertices.

    The index buffer is u16, so one model cannot reach past 65,535 vertices; the
    shipped pipeline splits at exactly that into CGMLMesh buckets. Split per
    triangle so no face is ever cut.
    """
    if len(pos) <= U16_LIMIT:
        return [(pos, nrm, uv, idx)]
    parts, cur, remap = [], ([], [], [], []), {}
    for k in range(0, len(idx), 3):
        if len(cur[0]) + 3 > limit:
            parts.append(cur)
            cur, remap = ([], [], [], []), {}
        for v in idx[k:k + 3]:
            if v not in remap:
                remap[v] = len(cur[0])
                cur[0].append(pos[v]); cur[1].append(nrm[v]); cur[2].append(uv[v])
            cur[3].append(remap[v])
    if cur[0]:
        parts.append(cur)
    return parts


# ---------------------------------------------------------------------------
def _texture_donor(*roots):
    """A 256-byte `streamingdisabled == 1` descriptor to stamp from.

    Looked for in the staging directory first and then in the EXTRACT, because
    a first run has nothing staged yet -- taking the donor only from `out` left
    a fresh input-pcvr with zero textures and no error.
    """
    for root in roots:
        d = Path(root) / T_TEX
        if not d.is_dir():
            continue
        for p in sorted(d.iterdir()):
            try:
                b = p.read_bytes()
            except OSError:
                continue
            if (len(b) == 256
                    and struct.unpack_from("<I", b, TEX_F["streamingdisabled"])[0] == 1):
                return b
    return None


def stage_textures(geo_dir: Path, out: Path, prefix: str, texconv: Path | None,
                   extract: Path | None = None):
    """Every exported image -> a raw `cgtextureresource` + GPU pair.

    "Raw" is the shipped staging form: a 256-byte descriptor with
    `streamingdisabled = 1`, and the DDS verbatim in the GPU sidecar. No
    `RawTexturePackfile` stream. The descriptor is donor-stamped so the 192-byte
    `packfilelayout` it opens with comes from a real one.
    """
    images = json.loads((geo_dir / "images.json").read_text(encoding="utf-8"))
    if not images:
        return {}
    (out / T_TEX).mkdir(parents=True, exist_ok=True)
    (out / T_TEX_GPU).mkdir(parents=True, exist_ok=True)
    donor = _texture_donor(out, extract) if extract else _texture_donor(out)
    if donor is None:
        print("  no 256-byte descriptor to stamp from -- skipping textures")
        return {}
    dds_dir = geo_dir / "dds"
    dds_dir.mkdir(exist_ok=True)
    made = {}
    for img in images:
        src = geo_dir / img["file"]
        srgb = 1 if str(img.get("colorspace", "sRGB")).lower() == "srgb" else 0
        dst = dds_dir / (Path(img["file"]).stem + ".dds")
        if not dst.is_file():
            if texconv is None:
                continue
            subprocess.run([str(texconv), "-nologo", "-y", "-f",
                            "BC7_UNORM_SRGB" if srgb else "BC7_UNORM",
                            "-m", "0", "-o", str(dds_dir), str(src)],
                           capture_output=True, text=True)
            if not dst.is_file():
                print("  texconv failed: %s" % img["file"])
                continue
        blob = dst.read_bytes()
        h, w = struct.unpack_from("<II", blob, 12)
        pitch = struct.unpack_from("<I", blob, 20)[0]
        mips = struct.unpack_from("<I", blob, 28)[0] or 1
        dxgi = struct.unpack_from("<I", blob, 128)[0] if blob[84:88] == b"DX10" else 0
        desc = bytearray(donor[:256])
        for key, value in (("streamingdisabled", 1), ("maxwidth", w),
                           ("maxheight", h), ("maxmipcount", mips),
                           ("arraysize", 1), ("cubemap", 0), ("format", dxgi),
                           ("srgb", srgb), ("createasarray", 0), ("volume", 0),
                           ("width", w), ("height", h), ("mipcount", mips),
                           ("resmemsize", len(blob)), ("pitch", pitch),
                           ("padding", 0)):
            struct.pack_into("<I", desc, TEX_F[key], int(value))
        name = prefix + Path(img["file"]).stem.lower()
        hh = symbol64(name)
        (out / T_TEX / hh).write_bytes(bytes(desc))
        (out / T_TEX_GPU / hh).write_bytes(blob)
        made[img["name"]] = {"name": name, "hash": hh, "size": [w, h],
                             "mips": mips, "bytes": len(blob)}
    print("  textures: %d" % len(made))
    return made


# ---------------------------------------------------------------------------
def _light_counts(scene: bytes):
    n = struct.unpack_from("<I", scene, 0)[0]
    kinds = [struct.unpack_from("<I", scene, 4 + i * L_STRIDE + L["type"])[0]
             for i in range(n)]
    return n, kinds


def _donor_light(scene: bytes, kind: int):
    n, kinds = _light_counts(scene)
    for i, k in enumerate(kinds):
        if k == kind:
            return scene[4 + i * L_STRIDE: 4 + (i + 1) * L_STRIDE]
    return None


def add_lights(work: Path, level: str, lights) -> int:
    """Append `SGLightParams` records, stamped from a shipped one of the type.

    `lights` are dicts of `{type, position, direction, color, intensity, range,
    cone, cone_inner, name}`. A POINT that would take the level past
    `POINT_CAP` is re-typed to SPOT with a full-hemisphere cone: the cap is
    real, and going over it drops geometry out of the render.
    """
    path = work / T_SCENE / level
    scene = path.read_bytes()
    n, kinds = _light_counts(scene)
    points = sum(1 for k in kinds if k == POINT)
    blob, made = b"", 0
    for spec in lights:
        kind = {"POINT": POINT, "SPOT": SPOT, "SUN": SUN}[spec["type"].upper()]
        if kind == POINT and points >= POINT_CAP:
            kind = SPOT
            spec = dict(spec, cone=math.radians(179.0),
                        cone_inner=math.radians(143.0))
        donor = _donor_light(scene, kind)
        if donor is None:
            print("  no donor light of type %d -- skipped" % kind)
            continue
        rec = bytearray(donor)
        struct.pack_into("<I", rec, L["type"], kind)
        struct.pack_into("<Q", rec, L["name"], int(symbol64(spec["name"]), 16))
        struct.pack_into("<3f", rec, L["position"], *spec.get("position", (0, 0, 0)))
        struct.pack_into("<3f", rec, L["direction"], *spec.get("direction", (0, -1, 0)))
        struct.pack_into("<3f", rec, L["color"], *spec.get("color", (1, 1, 1))[:3])
        struct.pack_into("<f", rec, L["intensity"], float(spec.get("intensity", 1.0)))
        reach = float(spec.get("range", 0.0))
        struct.pack_into("<2f", rec, L["range"], reach, reach)
        if kind == SPOT and spec.get("cone"):
            cone = float(spec["cone"])
            inner = float(spec.get("cone_inner") or cone)
            struct.pack_into("<f", rec, L["spot_angle"], cone)
            struct.pack_into("<f", rec, L["cos_outer"], math.cos(cone / 2.0))
            struct.pack_into("<f", rec, L["cos_inner"], math.cos(inner / 2.0))
        struct.pack_into("<Q", rec, L["owner"], NULL)
        blob += bytes(rec)
        made += 1
        if kind == POINT:
            points += 1
    if not made:
        return n
    out = bytearray(scene)
    end = 4 + n * L_STRIDE
    out[end:end] = blob
    struct.pack_into("<I", out, 0, n + made)
    path.write_bytes(bytes(out))
    print("  lights %d -> %d  (POINT now %d / %d)" % (n, n + made, points, POINT_CAP))
    return n + made


# ---------------------------------------------------------------------------
def grow_entity_crs(extract: Path, work: Path, level: str, entities, donor_entity):
    """Add a row to every level CR that carries one per static instance."""
    grown = []
    for d in sorted(extract.iterdir()):
        if not d.is_dir() or d.name in ALREADY_GROWN:
            continue
        src = d / level
        if not src.is_file():
            continue
        b = src.read_bytes()
        if len(b) < 64:
            continue
        try:
            size = struct.unpack_from("<Q", b, 8)[0]
            c1 = struct.unpack_from("<Q", b, 40)[0]
            c2 = struct.unpack_from("<Q", b, 48)[0]
        except struct.error:
            continue
        if not (c1 and c1 == c2 and size == len(b) - 56 and size % c1 == 0):
            continue
        stride = size // c1
        if not (8 <= stride <= 4096):
            continue
        col = di = None
        for c in range(0, min(stride, 64), 8):
            for i in range(c1):
                if struct.unpack_from("<Q", b, 56 + i * stride + c)[0] == donor_entity:
                    col, di = c, i
                    break
            if col is not None:
                break
        if col is None:
            continue
        out = bytearray(b)
        row = bytearray(b[56 + di * stride: 56 + (di + 1) * stride])
        end = 56 + c1 * stride
        for k, ent in enumerate(entities):
            r = bytearray(row)
            struct.pack_into("<Q", r, col, ent)
            out[end + k * stride: end + k * stride] = r
        n = c1 + len(entities)
        struct.pack_into("<Q", out, 8, n * stride)
        struct.pack_into("<Q", out, 40, n)
        struct.pack_into("<Q", out, 48, n)
        (work / d.name).mkdir(parents=True, exist_ok=True)
        (work / d.name / level).write_bytes(bytes(out))
        grown.append(d.name)
        print("  entity CR %s  %d -> %d rows (stride %d, entity col +%d)"
              % (d.name, c1, n, stride, col))
    return grown


def counted_cgvisibility(meshes: int, lights: int) -> bytes:
    """The counted-92 `CGVisibilityResource`: 7 counts, 7 cumulative pairs."""
    cats = [meshes, lights, 0, 0, 0, 0, 0]
    head = struct.pack("<7I", 0, 0, 0, 0, 0, 0, 0)
    tail, first = b"", 0
    for c in cats:
        tail += struct.pack("<II", first, c)
        first += c
    tail += struct.pack("<II", (sum(cats) + 31) // 32, 0)
    blob = head + tail
    assert len(blob) == 92, len(blob)
    return blob


# ---------------------------------------------------------------------------
def existing_levels(extract: Path) -> dict:
    """`{hash: name-or-""}` for every level that already exists.

    A level "exists" if the extract holds its `CStaticInstanceModelCR` -- that
    is the table this tool grows, so it is exactly the set it must refuse. The
    friendly names come from `data/level_names_echovr.json` where they are
    known, purely so the error can say `mpl_arena_a` instead of a hash.
    """
    names = {}
    table = evr_paths.DATA / "level_names_echovr.json"
    if table.is_file():
        try:
            names = {k.lower(): v for k, v in
                     json.loads(table.read_text(encoding="utf-8"))
                     .get("levels", {}).items()}
        except (OSError, ValueError):
            names = {}
    out = {}
    d = extract / T_CSIMCR
    if d.is_dir():
        for p in d.iterdir():
            h = (p.stem if p.suffix else p.name).lower().rjust(16, "0")
            out[h] = names.get(h, names.get(h.lstrip("0"), ""))
    return out


def assert_new_level(level: str, extract: Path) -> None:
    """Refuse to touch a level that already exists.

    ⛔ NEW LEVELS ONLY. Grafting into a shipped level edits the base game for
    everyone who loads it: the resources are keyed by name hash and are GLOBAL,
    so a modified `mpl_arena_a` is THE `mpl_arena_a` until the files are put
    back. It also makes the change impossible to distribute -- there is nothing
    to ship but a diff against someone's install.

    The supported route is `--clone-from`: `evr_clone_level` copies the level's
    ~90 files under a new name hash (only four of them carry a self-reference,
    and nothing outside the level indexes it), and the graft then lands on the
    copy. The stock level is never opened for writing.
    """
    have = existing_levels(extract)
    if level.lower() in have:
        label = have[level.lower()] or level
        raise SystemExit(
            "refusing to modify the existing level '%s' (%s).\n"
            "This tool only makes NEW levels -- a shipped level's resources are\n"
            "global, so editing one changes the base game and cannot be shipped.\n"
            "Clone it first and graft into the copy:\n"
            "    --level <new_name> --clone-from %s" % (label, level, label))


def clone_level(src: str, dst: str, extract: Path, out: Path) -> str:
    """Copy a level under a new name hash, and return that hash."""
    import evr_clone_level as CL
    dst_hex = CL.resolve(dst)
    src_hex = CL.resolve(src)
    if dst_hex in existing_levels(extract):
        raise SystemExit("the new level name '%s' (%s) already exists -- "
                         "pick another" % (dst, dst_hex))
    CL.clone(src, dst, extract, out, verbose=False)
    print("  cloned %s (%s) -> %s (%s)" % (src, src_hex, dst, dst_hex))
    return dst_hex


def run(geo_dir: Path, level: str, extract: Path, out: Path, *,
        prefix="blend_", collision=False, visibility="counted",
        lod_scale=1000.0, texconv=None, work=None, level_root=None) -> dict:
    """Graft into `level`, which must NOT already exist -- see `assert_new_level`.

    `level_root` is where the level's own files are read from; it defaults to
    `extract` and is the CLONE directory when `--clone-from` made one. `extract`
    stays the real game extract either way, because the donors this needs
    (a texture descriptor, a light of each type) come from shipped data.
    """
    import evr_add_model as AM
    import evr_level_reader as LR

    assert_new_level(level, extract)
    level_root = Path(level_root or extract)
    work = Path(work or (out.parent / ("_work_" + level)))
    if work.exists():
        shutil.rmtree(work)
    for t in LEVEL_TYPES:
        src = level_root / t / level
        if src.is_file():
            (work / t).mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, work / t / level)

    textures = stage_textures(geo_dir, out, prefix, texconv, extract)
    geo = json.loads((geo_dir / "geo.json").read_text(encoding="utf-8"))
    backend = AM._backend()
    (out / T_MAT).mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("EVR_LOD_SCALE", str(lod_scale))

    added = []
    for row in geo:
        stem = row["stem"]
        pos = _read_bin(geo_dir / (stem + "_pos.bin"), "f", 3)
        nrm = _read_bin(geo_dir / (stem + "_nrm.bin"), "f", 3)
        uv = _read_bin(geo_dir / (stem + "_uv.bin"), "f", 2)
        idx = _read_bin(geo_dir / (stem + "_idx.bin"), "I")
        albedo = next((textures[i["image"]] for i in row.get("images", [])
                       if i["image"] in textures
                       and str(i.get("colorspace", "")).lower() == "srgb"), None)
        mat_hash = 0
        if albedo:
            mat_hash = int(symbol64(prefix + row["material"].lower()), 16)
            try:
                (out / T_MAT / ("%016x" % mat_hash)).write_bytes(
                    backend.build_lit_world_material(int(albedo["hash"], 16)))
            except Exception as exc:                          # noqa: BLE001
                print("  material for %s failed: %s" % (row["material"], exc))
                mat_hash = 0
        base = prefix + row["material"].lower()
        parts = split_u16(pos, nrm, uv, idx)
        for k, (p, n, u, i) in enumerate(parts):
            name = base if len(parts) == 1 else "%s_%d" % (base, k)
            try:
                log = AM.add_model(work, level, work, name=name, positions=p,
                                   indices=i, normals=n, uvs=u,
                                   transform={"pos": tuple(row.get("offset", (0, 0, 0)))},
                                   material_hash=mat_hash or None,
                                   collision=collision, verbose=False)
                added.append(log)
                print("  + %-34s %6d verts %6d tris" % (name, len(p), len(i) // 3))
            except Exception as exc:                          # noqa: BLE001
                print("  ! %s: %r" % (name, exc))

    lights = json.loads((geo_dir / "lights.json").read_text(encoding="utf-8")) \
        if (geo_dir / "lights.json").is_file() else []
    n_lights = add_lights(work, level, lights) if lights else \
        _light_counts((work / T_SCENE / level).read_bytes())[0]

    extra = []
    if added:
        pairs = LR.parse_static_instance_models(
            (level_root / T_CSIMCR / level).read_bytes())
        extra = grow_entity_crs(level_root, work, level,
                                [int(a["entity"], 16) for a in added], pairs[0][0])

    copied = 0
    for t in list(LEVEL_TYPES) + list(extra):
        src = work / t / level
        if src.is_file():
            (out / t).mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, out / t / level)
            copied += 1
    for d in work.iterdir():
        if not d.is_dir():
            continue
        for f in d.iterdir():
            if f.name == level:
                continue
            (out / d.name).mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, out / d.name / f.name)
            copied += 1

    if visibility == "counted":
        import evr_add_model as _AM
        inst = _AM.GROW.CGSI.read((out / T_CGSI / level).read_bytes())
        n_inst = len(inst["sections"]["instancedata"])
        (out / T_VIS).mkdir(parents=True, exist_ok=True)
        (out / T_VIS / level).write_bytes(counted_cgvisibility(n_inst, n_lights))
        print("  CGVisibility -> counted-92  cat0=%d cat1=%d" % (n_inst, n_lights))
    print("copied %d file(s) into %s" % (copied, out))
    return {"models": added, "textures": textures, "lights": n_lights}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--geo", required=True, help="the add-on's export directory")
    ap.add_argument("--level", required=True,
                    help="the NEW level to build: a name or a hash that does "
                         "not already exist. Existing levels are refused")
    ap.add_argument("--clone-from", default=None,
                    help="clone this shipped level to --level first, then graft "
                         "into the copy. Required unless --level already names "
                         "a clone you made earlier")
    ap.add_argument("--dir", default=None, help="flat extract (or EVR_EXTRACT_DIR)")
    ap.add_argument("--out", required=True, help="input-pcvr staging directory")
    ap.add_argument("--prefix", default="blend_", help="name prefix for new assets")
    ap.add_argument("--collision", action="store_true",
                    help="also author CPhysics + CBVH (default: no collision)")
    ap.add_argument("--visibility", choices=("counted", "keep"), default="counted",
                    help="counted: replace the PVS so the new geometry is never "
                         "culled (loses the level's own culling); keep: leave it")
    ap.add_argument("--lod-scale", type=float, default=1000.0,
                    help="loddistancescales for the new instances (stock is 1.0)")
    ap.add_argument("--texconv", default=None, help="texconv.exe for the images")
    args = ap.parse_args(argv)

    extract = evr_paths.extract_dir(args.dir)
    if extract is None:
        ap.error("no game extract given: pass --dir <path> or set EVR_EXTRACT_DIR")
    import evr_clone_level as CL
    level = CL.resolve(args.level).lower()
    out_dir = Path(args.out)
    level_root = extract
    if args.clone_from:
        # Clone into `out` -- those files ARE the new level and have to ship --
        # and read the level's own tables back from there, while donors keep
        # coming from the real extract.
        level = clone_level(args.clone_from, args.level, extract, out_dir).lower()
        level_root = out_dir
    run(Path(args.geo), level, extract, out_dir, level_root=level_root,
        prefix=args.prefix, collision=args.collision, visibility=args.visibility,
        lod_scale=args.lod_scale,
        texconv=Path(args.texconv) if args.texconv else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
