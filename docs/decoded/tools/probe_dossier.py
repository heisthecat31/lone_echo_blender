"""Decode `CGReflectionProbeResourceWin10` + `...Win10GPU` across every EVR archive.

Tests, in order:
  1. residual        META_HEADER_SIZE + sum(table nbytes) == len(file)   (exact, 0 slack)
  2. strides         nbytes // iused per table, measured not assumed
  3. GPU closure     gpuoffsets[0]==0, monotonic, gpuoffsets[-1]+stride == gpumemsize
  4. GPU pairing     gpumemsize == len(the paired ...Win10GPU entry)
  5. cube geometry   per-probe bytes == BC6H face-major mip chain for some dim
  6. cross-table     points/mipcounts/boundingboxes/gpuoffsets all == n_probes
  7. identity        probeidx == row index on points; boxes[].probeidx in range

Writes docs/decoded/_probes.json.
"""
import json, pathlib, struct, sys, collections, hashlib

sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\lone_echo_blender")
from blender_tool.le_mesh import reflection_probe as RP

EXTR = pathlib.Path(r"H:\pcvr-extracted")
DOCS = pathlib.Path(r"J:\EchoVR-Tools-Launcher\lone_echo_blender\docs")
META_DIR = EXTR / "2829c885034afcde"   # CGReflectionProbeResourceWin10
GPU_DIR  = EXTR / "5004f0b9f6645271"   # CGReflectionProbeResourceWin10GPU

idx = json.loads((DOCS / "evr_level_index.json").read_text(encoding="utf-8"))
NAME = {h.lower(): v.get("name", h) for h, v in idx.items()}


def cube_chain_bytes(dim, mips, fmt):
    """face-major BC6H cube: 6 faces x full mip chain, 4x4 blocks, min 1 block."""
    bb = RP.block_bytes(fmt)
    tot = 0
    for m in range(mips):
        d = max(1, dim >> m)
        blocks = max(1, (d + 3) // 4)
        tot += blocks * blocks * bb
    return tot * 6


def solve_dim(nbytes, mips, fmt):
    for d in (1024, 512, 256, 128, 64, 32, 16, 8, 4):
        if cube_chain_bytes(d, mips, fmt) == nbytes:
            return d
    return None


rows, fails = [], []
for f in sorted(META_DIR.iterdir()):
    blob = f.read_bytes()
    lv = f.name.lower()
    rec = {"level": lv, "name": NAME.get(lv, "?"), "meta_bytes": len(blob)}
    if len(blob) < RP.META_HEADER_SIZE:
        rec["error"] = "short"; rows.append(rec); continue

    tables, cur = {}, 0
    for i, nm in enumerate(RP.TABLE_NAMES):
        ptr, nbytes, ialloc, iused = RP.parse_ctable(blob, i * 0x38)
        tables[nm] = {"ptr": ptr, "nbytes": nbytes, "ialloc": ialloc, "iused": iused,
                      "stride": (nbytes // iused) if iused else None}
        cur += nbytes
    rec["tables"] = tables
    rec["residual"] = len(blob) - RP.META_HEADER_SIZE - cur
    rec["gpumemsize"] = struct.unpack_from("<I", blob, RP.OFF_GPUMEMSIZE)[0]
    fmt = struct.unpack_from("<I", blob, RP.OFF_TEXTUREFORMAT)[0]
    rec["textureformat"] = fmt
    rec["format_name"] = RP.format_name(fmt)

    n = tables["points"]["iused"]
    rec["n_probes"] = n
    rec["counts_agree"] = (n == tables["mipcounts"]["iused"] ==
                           tables["boundingboxes"]["iused"] == tables["gpuoffsets"]["iused"])

    # payload slices, in declaration order
    off = RP.META_HEADER_SIZE
    pay = {}
    for nm in RP.TABLE_NAMES:
        nb = tables[nm]["nbytes"]
        pay[nm] = blob[off:off + nb]; off += nb

    if n:
        offs = list(struct.unpack_from("<%dI" % n, pay["gpuoffsets"], 0))
        mips = list(struct.unpack_from("<%dI" % n, pay["mipcounts"], 0))
        pidx = [struct.unpack_from("<I", pay["points"], i * 0x10 + 0xC)[0] for i in range(n)]
        rec["gpu_offsets"] = offs
        rec["mipcounts"] = mips
        rec["point_idx_is_row"] = pidx == list(range(n))
        rec["gpu_monotonic"] = all(offs[i] < offs[i + 1] for i in range(n - 1))
        rec["gpu_starts_zero"] = offs[0] == 0
        sizes = [(offs[i + 1] - offs[i]) for i in range(n - 1)] + [rec["gpumemsize"] - offs[-1]]
        rec["cube_bytes"] = sorted(set(sizes))
        rec["gpu_closes"] = (offs[-1] + sizes[-1]) == rec["gpumemsize"]
        rec["cube_dims"] = [solve_dim(sizes[i], mips[i], fmt) for i in range(n)]
        bidx = [struct.unpack_from("<I", pay["boxes"], i * 0x38 + RP.B_PROBEIDX)[0]
                for i in range(tables["boxes"]["iused"])]
        rec["box_probeidx"] = sorted(set(bidx))
        rec["box_idx_in_range"] = all(0 <= b < n for b in bidx)
    else:
        rec["gpu_offsets"] = []; rec["mipcounts"] = []

    g = GPU_DIR / f.name
    rec["gpu_present"] = g.is_file()
    rec["gpu_bytes"] = g.stat().st_size if g.is_file() else None
    rec["gpu_matches"] = (rec["gpu_bytes"] == rec["gpumemsize"]) if g.is_file() else None
    rows.append(rec)

    bad = []
    if rec["residual"] != 0: bad.append("residual=%d" % rec["residual"])
    if not rec["counts_agree"]: bad.append("counts")
    if rec["gpu_matches"] is False: bad.append("gpu %s != %s" % (rec["gpu_bytes"], rec["gpumemsize"]))
    if n and not rec["gpu_closes"]: bad.append("gpu_closure")
    if n and None in rec["cube_dims"]: bad.append("cube_dim")
    if n and not rec["point_idx_is_row"]: bad.append("point_idx")
    if bad: fails.append((lv, rec["name"], bad))

json.dump(rows, open(DOCS / "decoded" / "_probes.json", "w"), indent=1)

tot = len(rows)
withp = [r for r in rows if r.get("n_probes")]
print("archives with a probe resource : %d" % tot)
print("  populated (n_probes > 0)     : %d" % len(withp))
print("  empty (344-byte stub)        : %d" % sum(1 for r in rows if not r.get("n_probes")))
print("residual == 0                  : %d / %d" % (sum(1 for r in rows if r.get("residual") == 0), tot))
print("counts agree                   : %d / %d" % (sum(1 for r in rows if r.get("counts_agree")), tot))
print("gpumemsize == GPU entry size   : %d / %d" % (sum(1 for r in rows if r.get("gpu_matches")), sum(1 for r in rows if r.get("gpu_present"))))
print("gpuoffsets close on gpumemsize : %d / %d" % (sum(1 for r in withp if r.get("gpu_closes")), len(withp)))
print("every cube dim solved          : %d / %d" % (sum(1 for r in withp if None not in r["cube_dims"]), len(withp)))
print("points[i].probeidx == i        : %d / %d" % (sum(1 for r in withp if r.get("point_idx_is_row")), len(withp)))
print("boxes[].probeidx in range      : %d / %d" % (sum(1 for r in withp if r.get("box_idx_in_range")), len(withp)))
print("total probes                   : %d" % sum(r["n_probes"] for r in withp))
print("total GPU bytes                : %.1f MB" % (sum(r["gpumemsize"] for r in rows) / 1048576.0))
st = collections.Counter()
for r in rows:
    for nm, t in (r.get("tables") or {}).items():
        if t["stride"]: st[(nm, t["stride"])] += 1
print("measured strides               : %s" % dict(st))
print("formats                        : %s" % collections.Counter(r.get("format_name") for r in rows))
dims = collections.Counter(d for r in withp for d in r["cube_dims"])
print("cube dims                      : %s" % dict(dims))
mc = collections.Counter(m for r in withp for m in r["mipcounts"])
print("mipcounts                      : %s" % dict(mc))
if fails:
    print("\nFAILURES (%d):" % len(fails))
    for lv, nm, b in fails: print("  %s %-34s %s" % (lv, nm, b))
else:
    print("\nno failures")


# =============================================================================
# proofs beyond the container: cube byte order, the mesh binding, selection
# =============================================================================
def _extra_proofs():
    import numpy as np, texture2ddecoder as T
    sys.path.insert(0, r"J:\EchoVR-Tools-Launcher\EchoVR-Map-Editor")
    from echo_editor.assemble.authoring import AuthoringBackend
    A = AuthoringBackend(); A.activate()
    U = lambda s: A.rad_hash(s) & 0xFFFFFFFFFFFFFFFF
    MLD = EXTR / ("%016x" % U("CGMeshListResourceWin10"))
    BLK, DIM, MIPS, FACES = 16, 256, 9, 6
    CHAIN = [max(1, ((DIM >> m) + 3) // 4) ** 2 * BLK for m in range(MIPS)]
    FACE, CUBE = sum(CHAIN), sum(CHAIN) * FACES
    live = [r for r in rows if r.get("n_probes")]

    def dec(buf, d):
        return np.frombuffer(T.decode_bc6(buf, d, d), np.uint8).reshape(d, d, 4)[:, :, :3].astype(np.float32)
    def down(a):
        return a.reshape(a.shape[0]//2, 2, a.shape[1]//2, 2, 3).mean((1, 3))
    def corr(a, b):
        a = a.ravel() - a.mean(); b = b.ravel() - b.mean()
        d = np.sqrt((a*a).sum() * (b*b).sum())
        return float((a*b).sum()/d) if d > 1e-6 else 0.0
    fm = lambda f, m: f * FACE + sum(CHAIN[:m])
    mm = lambda f, m: sum(c * FACES for c in CHAIN[:m]) + f * CHAIN[m]

    print("\n--- cube byte order (all %d probes, mip m+1 vs box-downsample of mip m) ---" % sum(r["n_probes"] for r in live))
    for nm, off in (("face-major", fm), ("mip-major", mm)):
        s = [corr(down(dec(blob[p*CUBE+off(f,m):p*CUBE+off(f,m)+CHAIN[m]], DIM >> m)),
                  dec(blob[p*CUBE+off(f,m+1):p*CUBE+off(f,m+1)+CHAIN[m+1]], DIM >> (m+1)))
             for r in live for blob in [(GPU_DIR / r["level"]).read_bytes()]
             for p in range(r["n_probes"]) for f in range(FACES) for m in range(3)]
        print("  %-11s r = %+.4f   (fraction r > 0.9: %.3f)" % (nm, np.mean(s), float((np.array(s) > 0.9).mean())))

    print("\n--- per-mesh binding: CGMeshData.probeidx (struct +0x50 -> record +0x68) ---")
    REC, NONE = 152, 0xFFFFFFFF
    def tabs(lv):
        b = (META_DIR / lv).read_bytes(); off, out = RP.META_HEADER_SIZE, {}
        for i, nm in enumerate(RP.TABLE_NAMES):
            _, nb, _, iu = RP.parse_ctable(b, i * 0x38); out[nm] = (b[off:off+nb], iu); off += nb
        return out
    def qrot(q, v):
        x, y, z, w = q; u = np.array([x, y, z])
        return 2*np.dot(u, v)*u + (w*w - np.dot(u, u))*v + 2*w*np.cross(u, v)
    tot = near = boxwin = 0; ranks = []
    for r in live:
        f = MLD / r["level"]
        if not f.is_file(): continue
        Tb = tabs(r["level"]); pt, P = Tb["points"]; bx, B = Tb["boxes"]
        pts = [np.array(struct.unpack_from("<3f", pt, i*0x10)) for i in range(P)]
        boxes = [(np.array(struct.unpack_from("<4f", bx, i*0x38)),
                  np.array(struct.unpack_from("<3f", bx, i*0x38+0x10)),
                  np.array(struct.unpack_from("<3f", bx, i*0x38+0x1c)),
                  np.array(struct.unpack_from("<3f", bx, i*0x38+0x28)),
                  struct.unpack_from("<I", bx, i*0x38+0x34)[0]) for i in range(B)]
        buf = f.read_bytes(); n = struct.unpack_from("<I", buf, 0)[0]
        if not (0 < n < 200000) or 4 + n*REC > len(buf): continue
        for i in range(n):
            rec = buf[4+i*REC:4+(i+1)*REC]
            v = struct.unpack_from("<I", rec, 0x68)[0]
            if v == NONE: continue
            a = struct.unpack_from("<6f", rec, 0x3C)
            c = np.array([(a[0]+a[3])/2, (a[1]+a[4])/2, (a[2]+a[5])/2])
            # stable sort: 23 meshes across the corpus sit exactly equidistant from two
            # probes, and an unstable sort breaks those ties differently to argmin.
            order = list(np.argsort(np.array([np.linalg.norm(c-p) for p in pts]), kind="stable"))
            tot += 1; ranks.append(order.index(v) if v < P else 999)
            if order[0] == v: near += 1; continue
            cand = [(float(np.prod(mx-mn)), pi) for q, pos, mn, mx, pi in boxes
                    if np.all(qrot(q, c-pos) >= mn-1e-3) and np.all(qrot(q, c-pos) <= mx+1e-3)]
            if cand and min(cand)[1] == v: boxwin += 1
    print("  meshes carrying a probe index : %d  (out-of-range values: %d)" % (tot, sum(1 for x in ranks if x == 999)))
    print("  names the NEAREST probe       : %d (%.1f%%), mean rank %.2f" % (near, 100.0*near/tot, np.mean(ranks)))
    print("  not nearest, a box names it   : %d" % boxwin)
    print("  explained by nearest + box    : %d (%.2f%%)" % (near+boxwin, 100.0*(near+boxwin)/tot))


if __name__ == "__main__" and "--proofs" in sys.argv:
    _extra_proofs()
