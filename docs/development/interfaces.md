# Interface guide

## Contract authority

- `schemas/phase1-v1.schema.json`: existing versioned engine/request/result envelopes,
  sources, units, coordinates, resources and run status.
- `schemas/task-release-v1.schema.json`: CLI task-result wrapper around the original
  engine result.
- `schemas/examples/`: concrete Phase I envelope examples.
- `phase1/scenarios/catalog.json`, scene manifests and `SCENARIO_PROVENANCE.json`:
  scenario identities and referenced bytes.

Schema fields and version meaning remain authoritative. Python validation is in
`phase1.contracts` (also re-exported by `phase1.backend.core`); frontend envelope
validation is in `src/contract.js`. New wrappers must not extend a frozen engine
envelope in place.

## CLI

`python -m borden_tasks list|prepare|run` retains its existing arguments, four task
IDs, default seed 42, create-only output and `maintenance_smoke` mode. Parser errors
use argparse's exit 2. Execution failures return one JSON object with `status`,
`code`, `error` and exit 2. The code is the exception's `code` attribute when present,
otherwise `ERROR`. Existing official campaign admission checks remain in place.

Task results contain the task/release/scene/run identity plus `engine_result`;
the original engine envelope is validated independently. `artifact-index.json`
records relative paths, lengths and SHA256 values. Output files and error semantics
are described in the [running guide](../usage.md).

## HTTP and WebSocket

`phase1.backend.app:create_app` registers the API under `/api/v1`; `/docs` exposes
the actual OpenAPI operations. Main groups are scene catalog/load/configure,
versioned scenarios/resources/parameters, run submission/status/cancellation,
artifact download and geographic exports. A run submission uses the existing
`Idempotency-Key` binding. The run event socket is
`/api/v1/runs/{run_id}/events?after=0`; its ordered events contain a sequence and
the versioned status envelope.

Backend errors retain `{"error":{"code":"...","message":"...","retryable":false}}`
and their existing HTTP status. Pydantic request errors are 422 `INVALID_INPUT`.
Scene/artifact identity conflicts retain their existing conflict status.
Queued/running/terminal transitions are enforced by the store, worker and replay
validators; a failed run never supplies a successful result.

## Scene identity, units and source checks

Use both `scenario_id` and `scenario_version` plus the manifest SHA. Current scene
versions are 1001. Parameter IDs/versions, run UUID, seed and network SHA are bound
to each execution. Source provenance and availability gates remain separate from
runtime success. The Borden reference uses EPSG:32617 for its service frame; the
synthetic scene uses a separate local service frame. Model-local x/y/z positions
remain distinct from both road coordinates and WGS84.

Road/resource times are seconds from the scenario origin. Model time uses days:
`model_day = reference_model_day + scenario_seconds / 86400`.
Extraction rates are nonnegative m3/day; concentration is mg/L. Keep the existing
model horizon, physical units and scene identities when adapting an engine.

Scene reads verify every referenced asset. Backend publication and frontend replay
verify the run/scene/parameter binding and artifact SHA. The shared canonical JSON
helper compares parsed snapshots only; recorded digests continue to cover the
original byte streams. Required inputs never fall back to unlabelled fixtures.
