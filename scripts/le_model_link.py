"""le_model_link -- authoritative model->asset ownership from CModelCR.modelassets[].

Recovers, per archive, which asset hashes each `CModelCRWin7` component references
(`SModelCD.SInitData.modelassets[]`) and the `actornodeid` that owns them. This lets
the mesh<->skeleton binding be *scoped* to assets a model actually references, instead
of trusting any co-named pair in the archive (the shared-hash proxy, kept as a safe
fallback).

Reuses `le_model_scan.scan_archive` verbatim, so it inherits that decoder's
two on-disk shapes -- the `sresource_inittable` form and the `post_resource_properties`
fallback (the reference archive `0703fd2acd5803e9` uses ONLY the latter; a resolver that
read only the inittable form would see all-null assets and silently drop to shared-hash).

Verified end-to-end on `0703fd2acd5803e9`: all 34 co-named
skeleton/meshlist pairs are model-referenced (scoped == naive == 34, 0 dropped).

MUST run under Windows Python (le_oodle) -- `scan_archive` loads the archive.
"""

from __future__ import annotations

from collections import defaultdict

from le_model_scan import scan_archive

NULL_HASHES = {"", "0", "0000000000000000"}


def model_referenced_assets(archive_hash: str, names: dict) -> dict[int, list[str]]:
    """Return `{asset_hash_int: [actornodeid_hex, ...]}` across all CModelCRWin7 rows
    in one archive. Returns an empty dict on any scan failure so callers can fall back
    to the shared-hash link rather than error.
    """
    owners: dict[int, list[str]] = defaultdict(list)
    try:
        rows = scan_archive(archive_hash, names)
    except Exception:
        return {}
    for row in rows:
        for h in row.asset_hashes.split(","):
            h = h.strip().lower()
            if h in NULL_HASHES:
                continue
            try:
                owners[int(h, 16)].append(row.actor_node_hash)
            except ValueError:
                continue
    return dict(owners)


def classify_binding(target_hash: int, owners: dict[int, list[str]],
                     meshlist_hashes: set) -> dict:
    """Classify how a skeleton hash binds to its mesh, most-authoritative first:

      * ``modelassets``          -- a CModelCR references it; carries owning actornodeids.
      * ``shared_hash_fallback`` -- co-named with a meshlist but no CModelCR references it
                                    (orphan / anim-only); still safe to bind.
      * ``unreferenced``         -- neither; binding is unverified.
    """
    if target_hash in owners:
        return {"method": "modelassets",
                "actornodeids": sorted(set(owners[target_hash])),
                "co_named_meshlist": target_hash in meshlist_hashes}
    if target_hash in meshlist_hashes:
        return {"method": "shared_hash_fallback",
                "actornodeids": [], "co_named_meshlist": True}
    return {"method": "unreferenced",
            "actornodeids": [], "co_named_meshlist": False}
