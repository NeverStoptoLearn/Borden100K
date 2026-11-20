# Running Borden100K

## Environments

| Component | Environment |
|---|---|
| Numerical examples and scientific subprocess | Python **3.10.19**, NumPy **2.2.6**, SciPy **1.15.3**; `requirements.txt` |
| API and worker host | Python **3.11** (existing integration checked with 3.11.9); `requirements-backend.txt` |
| Traffic subprocess | Numerical environment plus `requirements-traffic.txt`, SUMO executable **1.28.0** |
| Web frontend | Node.js **22.x**, npm, and the checked-in `package-lock.json`; Vue **3.5.22**, Vite **6.4.1** |

Run commands from the repository root unless another directory is shown. Reuse
compatible environments when available. The README creates `.venv` using Python
3.10.19; `python --version` must report that exact version before creating it.
Select the API interpreter separately. In Windows PowerShell, the setup commands
below use the Python launcher to select Python 3.11:

```powershell
py -3.11 -m venv .venvs/api
& ./.venvs/api/Scripts/python.exe -m pip install -r requirements-backend.txt
& ./.venv/Scripts/python.exe -m pip install -r requirements-traffic.txt
```

The second requirements file installs the Python bindings; install the SUMO 1.28.0
executable separately. If `sumo` is already on PATH, no override is needed.
Otherwise, replace the placeholder with the installation directory containing `bin`:

```powershell
$env:SUMO_HOME = "<path-to-sumo-1.28.0>"
& "$env:SUMO_HOME/bin/sumo.exe" --version
```

Alternatively set `SUMO_BINARY` to the full `sumo.exe` path. Keep the same SUMO
configuration in **both the API and worker terminals**: the API checks scene
availability, while the worker executes traffic. A separate traffic interpreter
can be selected with worker `--traffic-python`; install `requirements-traffic.txt`
in that interpreter instead.

On Linux/macOS, create the numerical environment with the installed Python 3.10.19
interpreter (`python3.10 --version` to check), use `python3.11 -m venv .venvs/api`
for the API, and replace `Scripts/python.exe` with `bin/python`. Environment
variables use `export NAME=value`. For SUMO use `export SUMO_HOME=/path/to/sumo-1.28.0`
or `export SUMO_BINARY=/full/path/to/sumo`, then check `"$SUMO_HOME/bin/sumo" --version`
when using SUMO_HOME.

## API and worker

In a PowerShell terminal at the repository root, configure SUMO as above when
using routing, then start the API:

```powershell
$env:BORDEN_BACKEND_HOME = "$PWD/artifacts/web-runtime"
$env:BORDEN_SCIENCE_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
& ./.venvs/api/Scripts/python.exe -m uvicorn phase1.backend.app:app --host 127.0.0.1 --port 8000
```

In another terminal at the same repository root, repeat the SUMO configuration
and start the separate worker:

```powershell
$env:BORDEN_BACKEND_HOME = "$PWD/artifacts/web-runtime"
$env:BORDEN_SCIENCE_PYTHON = (Resolve-Path .venv/Scripts/python.exe).Path
$env:BORDEN_RELEASE_MAINTENANCE_SMOKE = "1"
& ./.venvs/api/Scripts/python.exe -m phase1.backend.worker --home "$env:BORDEN_BACKEND_HOME"
```

`BORDEN_SCIENCE_PYTHON` selects the numerical Python environment, which contains
`requirements.txt` and, for traffic, `requirements-traffic.txt`. The API and worker
must use the same `BORDEN_BACKEND_HOME`. The worker's explicit smoke flag enables
the existing bounded example execution path.

For Linux/macOS, the equivalent API terminal is:

```sh
export BORDEN_BACKEND_HOME="$PWD/artifacts/web-runtime"
export BORDEN_SCIENCE_PYTHON="$PWD/.venv/bin/python"
.venvs/api/bin/python -m uvicorn phase1.backend.app:app --host 127.0.0.1 --port 8000
```

In the worker terminal, repeat those exports and any SUMO settings, then run:

```sh
export BORDEN_RELEASE_MAINTENANCE_SMOKE=1
.venvs/api/bin/python -m phase1.backend.worker --home "$BORDEN_BACKEND_HOME"
```

Open `http://127.0.0.1:8000/docs` for the API documentation and
`http://127.0.0.1:8000/api/v1/health` for its health response.

## Frontend

Check `node --version` and `npm --version`. Use Node.js 22.x with its bundled npm.
In another terminal:

```powershell
cd phase1/frontend
npm ci
$env:BORDEN_API_PROXY = "http://127.0.0.1:8000"
npm run dev
```

On Linux/macOS, replace the variable assignment with
`export BORDEN_API_PROXY=http://127.0.0.1:8000`. Set it before starting Vite.
Keep the API and worker running; open the local address printed by Vite
(usually `http://127.0.0.1:5173`). Select a scene, load its configuration, choose
an available run mode and start a job. Both bundled scenes provide `no_pump`;
the synthetic scene also provides the routing modes when SUMO is available.
Completed jobs can be replayed with roads, vehicles, resources and groundwater
concentration on the shared time axis. Google Maps 3D is an optional renderer;
the default view uses the bundled local scene layers.

For a production frontend build, run `npm run build` in `phase1/frontend`.
`npm run preview` serves the build locally for inspection; the API and worker
remain separate processes. The development proxy above is a Vite dev-server
setting; a production host must also forward `/api` and its WebSocket connections
to the API on the same origin.

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
