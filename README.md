# Borden100K Task Release v1.0-rc1

Small, reproducible task evaluation interfaces for groundwater source identification,
emergency resource scheduling and traffic-aware routing. The physical baseline is
the existing NumPy/SciPy Darcy + three-dimensional finite-volume implementation.
This is a synthetic, uncalibrated research task release, not a validated regional
digital twin. The 100 km × 100 km extent belongs to the road/resource service area;
the aquifer is a separate local domain.

**Release status:** local RC. Basic evaluators and maintenance smoke examples are
supported; full optimization campaigns are Experimental. Official campaign
admission remains blocked until all locally owned Borden100K data are below
200,000,000,000 bytes and capacity acceptance is complete. A small Git archive does
not prove that the original research installation meets that limit.

## Quick start

Use a source checkout and Python **3.10.19**, NumPy 2.2.6 and SciPy 1.15.3 for the
certified numerical smoke profile. Reuse an existing compatible environment when
available. From the repository root:

```sh
python -m pip install -r requirements.txt
python -m borden_tasks list
python -m borden_tasks prepare --scenario borden_reference_100k --output artifacts/observations
python -m borden_tasks run --task source_identification --scenario borden_reference_100k --observations artifacts/observations/observations.npz --output artifacts/source-baseline
python -m borden_tasks run --task groundwater_forward --scenario synthetic_multiterrain_100k --output artifacts/forward
```

Each output path must be new. The source baseline uses the midpoint of the public
prior bounds; it does not select parameters using the hidden reference solution.
The inverse evaluator retains the original smooth source approximation and its
original discrete adjoint. It is not a newly calibrated inversion algorithm.

For actual traffic, install SUMO 1.28.0 separately, set `SUMO_HOME` or
`SUMO_BINARY`, and install `requirements-traffic.txt` into a compatible environment:

```sh
python -m borden_tasks run --task vehicle_routing --scenario synthetic_multiterrain_100k --output artifacts/traffic
python -m borden_tasks run --task vehicle_routing --scenario synthetic_multiterrain_100k --traffic-variant closure --output artifacts/traffic-closure
python -m borden_tasks run --task resource_scheduling --scenario synthetic_multiterrain_100k --traffic-result artifacts/traffic/result.json --output artifacts/schedule
```

Without `--traffic-result`, the resource example is explicitly `fixture=true` and
tests only the deterministic decoder contract. It is never described as a SUMO
or groundwater result. The real-source form verifies the upstream artifact bytes
and clock before running the unchanged scheduling policy.

## Browser platform

The retained Phase I API provides durable jobs, artifacts, scenarios and replay.
The Vue/deck.gl UI keeps the two scene identities separate. Google Maps 3D remains
optional and disabled by default; no imagery key is required for the basic UI.

```sh
# Backend environment: Python 3.11 and requirements-backend.txt.
# BORDEN_SCIENCE_PYTHON points to your compatible Python 3.10 interpreter.
python -m uvicorn phase1.backend.app:app --host 127.0.0.1 --port 8000
# In another terminal, from phase1/frontend:
npm ci
# Set BORDEN_API_PROXY=http://127.0.0.1:8000 in your shell, then:
npm run dev
```

The worker is a separate process. Default scientific job admission is blocked.
Only an operator-authorized bounded smoke session may set
`BORDEN_RELEASE_MAINTENANCE_SMOKE=1` and start
`python -m phase1.backend.worker --home <your-runtime-directory>`.
This flag does not authorize a full campaign or historical cleanup.

## Validation and distribution

```sh
python -m unittest discover -s tests -p test_task_release.py -v
```

Use the small standalone release repository/archive prepared from this branch.
The original repository history is retained locally and contains large historical
objects and local provenance; do not assume pushing this branch excludes those
objects. No remote push or public Release is performed by this preparation.

See [TASKS.md](TASKS.md), [SCENARIOS.md](SCENARIOS.md),
[EVALUATION.md](EVALUATION.md), [BASELINES.md](BASELINES.md),
[REPRODUCIBILITY.md](REPRODUCIBILITY.md) and [DATA.md](DATA.md).
Project-owned code: [Apache-2.0](LICENSE); third-party rights: [NOTICE](NOTICE).
Actual local acceptance and remaining gates: [VALIDATION.md](VALIDATION.md).
