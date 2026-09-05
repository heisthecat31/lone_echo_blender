# New-model chain test

```bash
python scripts/tests/test_add_model.py
```

Clones `mpl_arena_a`, adds a 1 m box as a brand-new model, and checks all nine
links. Prints `FAILURES: 0 []` on success (27 checks).

It verifies the things that are easy to get silently wrong:

* the actor table still round-trips and the new actor resolves to its index
* the placement row lands at the requested world position
* `CSIMCR` re-parses and the new instance names the right model
* the `CGSI` GPU-size chain (`@0x170 == 4 * sum(uvcount)`) still matches the
  sidecar file's real length
* **every physics body still walks** — the first run appended a whole
  `CPhysicsResource` instead of just its body, which put a `CPhData` header
  where the level's 36-byte trailer was and made body 10 unreadable
* the grafted BVH triangles carry a fresh geomID and sit at the world position
* all 1,890 manifest entries still resolve to a real file
