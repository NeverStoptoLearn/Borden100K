# Borden100K Task Release v1.0-rc1

## Project Overview

Borden100K provides reproducible evaluation interfaces for groundwater source
identification, groundwater forward modelling, emergency resource scheduling and
traffic-aware routing. The physical baseline is the existing NumPy/SciPy Darcy
solver and three-dimensional finite-volume transport model.

This release is a synthetic, uncalibrated research task toolkit. Its 100 km ×
100 km extent describes the road/resource service area; groundwater is evaluated
on a separate local aquifer domain. It is not a validated regional digital twin.

## Features

- A task CLI with explicit scene identity, seeds, inputs and indexed results.
- Separate Borden reference and procedurally generated synthetic scenes.
- The original source objective and discrete adjoint, actual SUMO routing, and
  the existing deterministic resource decoder.
- A FastAPI job/artifact API and Vue/deck.gl interface for scenes and replay.
- Reproducible bounded baselines and small regression tests.

## Supported Tasks

| Task | Release scope |
|---|---|
| `source_identification` | Bounded objective/gradient evaluator and fixed-prior baseline |
| `groundwater_forward` | No-pump Darcy/FV smoke evaluator |
| `resource_scheduling` | Deterministic feasibility and schedule decoder |
| `vehicle_routing` | Native SUMO normal/closure evaluation on the synthetic scene |
| `monitoring_well_optimization` | Experimental |
| `emergency_response_optimization` | Experimental |
| `hydro_traffic_coupled_optimization` | Experimental |

Supported evaluators do not constitute a completed optimization campaign. Full
optimization tasks are not available through the supported task CLI. See
[TASKS.md](TASKS.md) for decisions, objectives, constraints and contracts.

## Scenarios

| Scene ID | Coordinates and origin | Available evaluation |
|---|---|---|
| `borden_reference_100k` | EPSG:32617 service frame; public Government of Canada locator metadata | Local synthetic/uncalibrated hydro baseline; no target-road traffic |
| `synthetic_multiterrain_100k` | Procedural local metres, 0–100000; no WGS84 claim | Local hydro baseline, synthetic roads, SUMO and resource examples |

Both released scenes use version 1001 and independent configurations and result
identities. The Borden locator does not register the numerical model to field
measurements. No Borden road network, observed contamination dataset or Google
imagery is bundled. The local fan-shaped hydro domain and original 21900-day
horizon remain separate from the service frame. See [SCENARIOS.md](SCENARIOS.md).

## Project Structure

```text
borden_tasks/          Task registry, CLI, evaluation adapters and result contracts
phase1/hydro_adapter/ Existing scientific solver adapter
phase1/traffic/       Native SUMO routing and synthetic network
phase1/resources/     Resource scheduling decoder
phase1/backend/       FastAPI jobs, artifacts and replay API
phase1/frontend/      Vue/deck.gl scene and result interface
phase1/scenarios/     Separate scene manifests and configuration
schemas/              Frozen engine and task-result schemas
data/baselines/      Portable numerical baseline summaries
tests/               Small release regression tests
```

Root `borden3d_*.py` and the chapter-7 source set remain distinct scientific
implementations; do not mix their same-named imports.

## Requirements

- Scientific smoke profile: Python **3.10.19**, NumPy **2.2.6**, SciPy **1.15.3**;
  remaining pinned dependencies are in `requirements.txt`.
- Backend: Python **3.11** with `requirements-backend.txt`. A separate scientific
  interpreter can be selected with `BORDEN_SCIENCE_PYTHON`.
- Traffic: SUMO **1.28.0** plus `requirements-traffic.txt`; configure `SUMO_HOME`
  or `SUMO_BINARY` when SUMO is not on PATH.
- Frontend: Node.js/npm compatible with Vite 6; exact JavaScript dependencies are
  recorded in `phase1/frontend/package-lock.json`.

No imagery API key is required. Google Maps 3D is optional and disabled by default.

## Installation

Use a source checkout or source archive and run commands from its root. Reuse a
compatible environment, or create one if needed:

```sh
python -m venv .venv
# Activate .venv using your shell's standard activation command.
python -m pip install -r requirements.txt
python -m borden_tasks list
```

The supported distribution is a source checkout/archive. Editable installation
is optional; a standalone wheel without the accompanying scene/configuration
assets is not a supported deployment.

## Quick Start

Generate a small synthetic observation set, then evaluate the fixed-prior source
baseline and the local no-pump forward example:

```sh
python -m borden_tasks prepare --scenario borden_reference_100k --output artifacts/observations
python -m borden_tasks run --task source_identification --scenario borden_reference_100k --observations artifacts/observations/observations.npz --output artifacts/source-baseline --seed 42
python -m borden_tasks run --task groundwater_forward --scenario synthetic_multiterrain_100k --output artifacts/forward --seed 42
```

Every output directory must be new. Observation preparation and evaluation are
separate. The source baseline uses the midpoint of the public prior bounds, not
the hidden reference parameters. The inverse evaluator retains the original
smooth source approximation and discrete adjoint; it is not identical to the
prescribed-concentration observation generator.

With SUMO and the traffic dependencies available:

```sh
python -m pip install -r requirements-traffic.txt
python -m borden_tasks run --task vehicle_routing --scenario synthetic_multiterrain_100k --output artifacts/traffic
python -m borden_tasks run --task vehicle_routing --scenario synthetic_multiterrain_100k --traffic-variant closure --output artifacts/traffic-closure
python -m borden_tasks run --task resource_scheduling --scenario synthetic_multiterrain_100k --traffic-result artifacts/traffic/result.json --output artifacts/schedule
```

Without `--traffic-result`, resource scheduling uses an explicitly labelled
`fixture=true` example. It checks the decoder contract and is not a SUMO or hydro
result. Supplying a real traffic result verifies its artifact bytes and clock.

## Task Evaluation and Baselines

`python -m borden_tasks run` is the evaluation interface. For source scoring,
`--candidate` accepts three values: local x (m), local y (m), and initial
concentration (mg/L), within the original configured bounds. Omitting it selects
the fixed-prior baseline. The default evaluation seed is 42.

Results include `input.json`, `result.json` and `artifact-index.json`, recording
the task/scene identity, engine envelope, units and source digests. Scheduling
can return `feasible=false` without being an execution failure.

[EVALUATION.md](EVALUATION.md) defines objectives, units and acceptance boundaries.
[BASELINES.md](BASELINES.md) describes the fixed-prior, no-pump, native SUMO and
priority-decoder baselines. `data/baselines/final-fronts.json.gz` contains
historical numerical comparisons; they are not certified v1 campaign scores.

## Backend and Frontend

Install backend requirements into the backend environment and set
`BORDEN_SCIENCE_PYTHON` to a compatible scientific interpreter. Use the same
runtime directory for the API and worker. For example, in PowerShell:

```powershell
python -m pip install -r requirements-backend.txt
$env:BORDEN_BACKEND_HOME = "$PWD/artifacts/backend-runtime"
python -m uvicorn phase1.backend.app:app --host 127.0.0.1 --port 8000
```

In another terminal, configure the same `BORDEN_BACKEND_HOME` and scientific
interpreter, then start the worker for bounded example jobs:

```powershell
$env:BORDEN_BACKEND_HOME = "$PWD/artifacts/backend-runtime"
$env:BORDEN_RELEASE_MAINTENANCE_SMOKE = "1"
python -m phase1.backend.worker --home "$env:BORDEN_BACKEND_HOME"
```

In a frontend terminal:

```powershell
cd phase1/frontend
npm ci
$env:BORDEN_API_PROXY = "http://127.0.0.1:8000"
npm run dev
```

The interface displays separate scenes, resources, jobs and compatible indexed
results. The worker is a separate process; starting only the API does not run a
scientific job. A successful UI launch does not establish acceptance of the
Experimental optimization tasks.

## Testing

Run the small release suite without launching a full scientific campaign:

```sh
python -B -m unittest discover -s tests -p 'test*.py' -v
```

With the frontend dependencies installed, run its unit tests:

```sh
cd phase1/frontend
npm test -- --run
```

See [VALIDATION.md](VALIDATION.md) for the scope of existing evidence. Tests that
were not run in a particular environment must not be reported as passing.

## Documentation

- [Tasks and interfaces](TASKS.md)
- [Scenes, coordinates and provenance](SCENARIOS.md)
- [Evaluation protocol](EVALUATION.md)
- [Baseline configurations](BASELINES.md)
- [Environment, seeds and reproducibility](REPRODUCIBILITY.md)
- [Data, generation and checksums](DATA.md)
- [Scientific source provenance](SOURCE_PROVENANCE.json)

## License and Citation

Project-owned source code is licensed under [Apache-2.0](LICENSE). Third-party
rights and attribution are listed in [NOTICE](NOTICE); this license does not
relicense external road or observational data.

When citing the toolkit, identify **Borden100K Task Release**, the release version,
and the source commit used. No paper citation or archival DOI is assigned here.
