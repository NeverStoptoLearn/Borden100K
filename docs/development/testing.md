# Testing

Run commands from a source checkout. Reuse compatible environments; dependency
files describe how to create one when needed. Keep test output under `artifacts/`
or an explicit temporary directory, separate from scene/reference assets.

| Check | Command | Dependencies and scope |
|---|---|---|
| Python lint | `ruff check .` | Pinned Ruff; reviewed incremental scope |
| Python format | `ruff format --check .` | Same scope; frozen files excluded |
| Model/scene/contracts | `python -B tools/check_integrity.py` | jsonschema; read-only hashes, both scene identities and schema examples |
| Unit/API contracts | `python -B -m unittest discover -s tests -v` | `requirements-dev.txt`; CLI, scene isolation, errors, paths, admission and resource fixture |
| Frontend lint | `npm run lint` | In `phase1/frontend`; all source JS/Vue and Vitest tests |
| Frontend format | `npm run format:check` | Reviewed set in `.prettierignore` |
| Frontend unit | `npm test -- --run` | Vitest; contracts, replay, geometry, clock, planning and status stream |
| Frontend build | `npm run build` | Vite/deck.gl |

The resource unit example is explicitly a fixture. Scientific results and SUMO
execution are checked separately with the actual engines, rather than inferred
from fixture tests.

## Bounded scientific regression and integration

Use Python 3.10.19, NumPy 2.2.6 and SciPy 1.15.3 for the numerical profile. Follow
the README's `prepare` and source-evaluation commands using new output directories.
The default Borden midpoint candidate has objective `0.0007390396635860738` in
this profile. Compare candidate, observations, gradient and prediction as well as
the objective when checking scientific equivalence. Compare array contents rather
than timestamps or compressed-container bytes. Keep scene/model SHA checks active.

The CLI groundwater example and the API no-pump path use the actual Darcy/FV engine.
For API integration, start the API and a separate worker as described in
`docs/usage.md`; load the certified scene, submit an idempotent run, inspect status
and SHA-bound artifacts, then reopen the store and replay the same identity.

Routing tests require the SUMO 1.28.0 executable and `requirements-traffic.txt` in
the chosen interpreter. Run both normal/closure CLI examples, then resource
scheduling with the actual traffic result. `SUMO_HOME` or `SUMO_BINARY` selects the
executable. These bounded integration checks are separate from ordinary CI.

## Browser and optional environments

Vitest checks data and lifecycle helpers; it does not prove browser rendering.
The existing `test:browser` command invokes Playwright, but this source snapshot
has no checked-in Playwright browser suite/configuration. Report browser acceptance
as NOT_RUN until a separately reviewed browser suite is available. Google Maps 3D
additionally needs its explicit configuration and real browser/network environment.

`BORDEN_MAP_REAL_REPORT` optionally enables the existing actual-NPZ frontend test
when supplied a compatible integration report. Without that evidence it is not
part of the default test count. Do not report optional checks as passed by default.

## Continuous integration

`.github/workflows/ci.yml` runs Python unit/API-contract and integrity checks on
Linux/Python 3.10 and Windows/Python 3.11. Its Node 22 job uses `npm ci`, ESLint,
Prettier, Vitest and Vite build. CI does not run long scientific searches, external
imagery or a full SUMO campaign. A local pass and a hosted Actions pass are distinct;
report the latter only after the workflow has actually run.
