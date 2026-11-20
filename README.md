# Borden100K

Borden100K is a groundwater and emergency-planning application. It combines a
Darcy flow and three-dimensional contaminant transport solver, source-parameter
evaluation, SUMO vehicle routing, resource scheduling and an interactive map UI.

The road/resource service area spans 100 km × 100 km. The groundwater solver uses
its own local aquifer domain, configured independently of the service area.

## What you can run

| Task | Input | Output |
|---|---|---|
| Groundwater simulation | Scene and physical parameters | Concentration fields, flow and mass metrics |
| Source identification | Observations and source location/concentration | Objective value, adjoint gradient and predicted observations |
| Vehicle routing | Synthetic network, dispatches and road closures | Routes, arrival times and trajectories from SUMO |
| Resource scheduling | Traffic results, crews, equipment and inventory | Feasibility and deployment schedule |

Two scene configurations are included:

- **Borden reference** (`borden_reference_100k`): a regional reference based on
  Government of Canada location metadata, with a separately configured local
  groundwater model.
- **Synthetic terrain** (`synthetic_multiterrain_100k`): generated terrain,
  road-network and resource inputs in a local 100 km service frame.

## Install

Use **Python 3.10.19** for the groundwater examples. The pinned numerical
requirements are NumPy 2.2.6 and SciPy 1.15.3.

```powershell
git clone https://github.com/NeverStoptoLearn/Borden100K.git
cd Borden100K
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

On Linux/macOS activate the environment with `source .venv/bin/activate`.

## Run a first example

Generate model observations and evaluate the default source candidate:

```sh
python -m borden_tasks list
python -m borden_tasks prepare --scenario borden_reference_100k --output artifacts/observations
python -m borden_tasks run --task source_identification --scenario borden_reference_100k --observations artifacts/observations/observations.npz --output artifacts/source
```

Run the groundwater forward model:

```sh
python -m borden_tasks run --task groundwater_forward --scenario synthetic_multiterrain_100k --output artifacts/groundwater
```

Each output directory must be new. Results are written to `result.json` and
`artifact-index.json`; `input.json` records the scene, seed and source identities.
The default source candidate is the midpoint of the configured prior bounds.
Use `--candidate x y C0` to evaluate another location (m) and concentration (mg/L).

## Routing and scheduling

Install SUMO 1.28.0 and its Python bindings, then set `SUMO_HOME` or `SUMO_BINARY`
if its executable is outside PATH:

```sh
python -m pip install -r requirements-traffic.txt
python -m borden_tasks run --task vehicle_routing --scenario synthetic_multiterrain_100k --output artifacts/traffic
python -m borden_tasks run --task vehicle_routing --scenario synthetic_multiterrain_100k --traffic-variant closure --output artifacts/traffic-closure
python -m borden_tasks run --task resource_scheduling --scenario synthetic_multiterrain_100k --traffic-result artifacts/traffic/result.json --output artifacts/schedule
```

## Web interface

The web application provides scene selection, dispatch planning, simulation
jobs, a shared time axis and result replay. Start the API, worker and frontend
as described in [docs/usage.md](docs/usage.md).

## Project layout

```text
borden_tasks/         Command-line tasks and evaluation
phase1/hydro_adapter/ Groundwater solver adapter
phase1/traffic/       SUMO integration
phase1/resources/     Scheduling
phase1/backend/       API and job worker
phase1/frontend/      Vue/deck.gl web interface
phase1/scenarios/     Scene inputs and configuration
schemas/             Input/output contracts
tests/               Runtime checks
borden3d_common.py    Darcy/FV numerical implementation
borden3d_config.json  Local groundwater model parameters
```

## Tests and documentation

For development checks, install `requirements-dev.txt` in a compatible environment:

```sh
python -m pip install -r requirements-dev.txt
python -B tools/check_integrity.py
python -B -m unittest discover -s tests -v
```

The development requirements include the API test dependencies. With frontend dependencies installed,
run `npm test -- --run` from `phase1/frontend`.

See [docs/usage.md](docs/usage.md) for web startup, inputs, units, outputs and
troubleshooting. For development, see [architecture](docs/architecture.md),
[interfaces](docs/development/interfaces.md), [testing](docs/development/testing.md)
and [contributing](CONTRIBUTING.md). Source code is licensed under [Apache-2.0](LICENSE);
third-party attribution is in [NOTICE](NOTICE).
