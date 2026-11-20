# Running Borden100K

## Environments

Numerical examples use Python 3.10.19, NumPy 2.2.6 and SciPy 1.15.3. Core
dependencies are pinned in `requirements.txt`. The API uses
`requirements-backend.txt`; its startup below uses a Python 3.11 environment
and a separately selected numerical interpreter. Traffic bindings are listed
in `requirements-traffic.txt`; install them in the interpreter used for traffic.
Reuse an existing compatible environment when available.

## API and worker

From the repository root, in the backend environment:

```powershell
python -m pip install -r requirements-backend.txt
$env:BORDEN_BACKEND_HOME = "$PWD/artifacts/web-runtime"
$env:BORDEN_SCIENCE_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
python -m uvicorn phase1.backend.app:app --host 127.0.0.1 --port 8000
```

Use another terminal with the same backend environment and variables for the worker:

```powershell
$env:BORDEN_BACKEND_HOME = "$PWD/artifacts/web-runtime"
$env:BORDEN_SCIENCE_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
$env:BORDEN_RELEASE_MAINTENANCE_SMOKE = "1"
python -m phase1.backend.worker --home "$env:BORDEN_BACKEND_HOME"
```

`BORDEN_SCIENCE_PYTHON` points to the numerical Python environment. It must contain
`requirements.txt`. For routing jobs, install the traffic requirements in that
environment too, or pass `--traffic-python` to the worker to select an existing
traffic environment. Configure `SUMO_HOME` or `SUMO_BINARY` in the worker terminal.

Open `http://127.0.0.1:8000/docs` for the API documentation and
`http://127.0.0.1:8000/api/v1/health` for its health response.

## Frontend

In another terminal:

```powershell
cd phase1/frontend
npm ci
$env:BORDEN_API_PROXY = "http://127.0.0.1:8000"
npm run dev
```

Open the local address printed by Vite. Select a scene, choose a run mode and
start a job. Completed jobs can be replayed with roads, vehicles, resources and
groundwater concentration on the shared time axis. Google Maps 3D is an optional
renderer; the default view uses the bundled local scene layers.

## Task inputs

- `source_identification`: use `prepare` to generate `observations.npz` and its
  `task-dataset.json`, then pass the NPZ with `--observations`. `--candidate x y C0`
  uses model-local metres and mg/L. The evaluator returns the original normalized
  least-squares objective and discrete-adjoint gradient.
- `groundwater_forward`: the configured no-pump example returns the concentration
  arrays and flow/mass metrics in an indexed engine directory.
- `vehicle_routing`: choose the synthetic scene and `--traffic-variant normal`
  or `closure`. The result contains executed SUMO routes and arrival times.
- `resource_scheduling`: supply `--traffic-result` from a routing task. The
  decoder checks crews, inventory, equipment, timing and stock. Omitting traffic
  selects an explicitly tagged fixture for testing the decoder.

The default seed is 42. Scene-generation seeds are recorded separately in the
scene manifests. New output folders keep task results separate.

## Coordinates, time and outputs

The Borden reference service frame uses EPSG:32617. Its location metadata comes
from the Government of Canada registry, site 11022032. The synthetic service
scene uses local metres from 0 to 100000; roads and resources are generated from
fixed seeds. Each scene has its own configuration and identity.

Groundwater positions use model-local metres, concentration uses mg/L, and
extraction uses nonnegative m3/day. Road/resource time is seconds from the scene
origin. Model time is days:

`model_day = reference_model_day + scenario_seconds / 86400`

The model configuration records the original 14600-day source switch and
21900-day horizon. `MODEL_SHA256.json` verifies the numerical implementation and
configuration. Scene inputs have their own hashes and versioned manifests.

A task creates `input.json`, `result.json` and `artifact-index.json`. The original
engine result is under `engine_result`. Inspect `feasible` in scheduling results
as well as task execution status. API jobs additionally store status and event
records in the selected backend runtime directory.

## Troubleshooting

- **Numerical profile error:** select Python 3.10.19 with NumPy 2.2.6 and SciPy 1.15.3.
- **SUMO executable missing:** set `SUMO_BINARY` to the executable or `SUMO_HOME`
  to its installation directory, and install `requirements-traffic.txt`.
- **Output already exists:** choose a new `--output` directory.
- **API reachable but job remains queued:** start the worker with the same
  `BORDEN_BACKEND_HOME` and configure its numerical/traffic interpreters.
- **Frontend API error:** set `BORDEN_API_PROXY` before starting Vite and check
  the API health endpoint.

## Development checks

```sh
python -B -m unittest discover -s tests -v
```

Frontend checks from `phase1/frontend`:

```sh
npm test -- --run
npm run build
```
