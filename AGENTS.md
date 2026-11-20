# Borden100K development rules

1. Check branch, status and existing user edits before working. Change only the requested scope and preserve user work.
2. Keep the Darcy/FV equations, objective, adjoint, constraints, units and original model/configuration bytes unchanged unless a scientific change is explicitly requested and validated.
3. Record seeds, configuration, dependencies, source revision and input/output hashes for reproducible runs. Keep the Borden reference and generated service scenes distinct.
4. Reuse existing environments and data. Start with static/unit checks and bounded examples; large simulations or searches require explicit authorization.
5. Estimate output size and physical headroom before execution. Use new output directories; preserve required scientific results and avoid redundant logs/checkpoints.
6. Remove only verified unused rebuildable caches within the authorized scope. Historical scientific retirement requires an exact approved manifest. Never delete by extension, FAILED status or directory name; never traverse junctions during deletion or override ACLs.
7. Preserve historical protocols, databases, scientific checksums and original results. Changes in the public runtime selection do not authorize edits to original research evidence.
8. Do not reset/clean user work, rewrite history, force-push or publish without authorization. Keep runtime output, environments, local databases and credentials out of Git.
9. Reuse existing modules and preserve interfaces. Update callers/tests when an interface changes; avoid unnecessary dependencies or full environment rebuilds.
10. Report changed files, exact checks, PASS/FAIL/BLOCKED/NOT_RUN, scientific impact, storage changes and Git status. Instructions supplement program checks; they do not provide OS isolation.

## Working paths

- `borden_tasks/`: public CLI and task evaluators.
- `phase1/`: groundwater adapter, traffic, resources, API, UI and scenes.
- `borden3d_common.py` / `borden3d_config.json`: original numerical solver/configuration, verified by `MODEL_SHA256.json`.
- `tests/`: focused contract and integration checks. Run `python -B -m unittest discover -s tests -v`.

Preserve reference coordinates, local model coordinates and generated service coordinates. Road time uses seconds; model time uses days with an explicit reference day. Describe actual supported operations directly and keep source classifications accurate.
