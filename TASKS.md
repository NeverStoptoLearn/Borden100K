# Tasks and supported scope

The release exposes one CLI, `python -m borden_tasks`, and Python adapters in
`borden_tasks.core`. `list` is the executable task registry. Supported means the
stated bounded evaluator/decoder is implemented; it does not imply a complete
optimization study, calibrated field validity or city-scale acceptance.

| Task ID | v1 RC status | Decisions/input | Objective/output | Constraints |
|---|---|---|---|---|
| source_identification | Supported evaluator + fixed-prior baseline | local x, y (m), initial concentration (mg/L), observation vectors | existing smooth_objective and discrete-adjoint gradient; half mean squared normalized residual | original configured x/y/C bounds; finite, in-grid observation indices; matching dataset/scene SHA |
| resource_scheduling | Supported deterministic decoder | fixed dispatches, priorities, crews, equipment, stock, installation/pumping durations | original feasible flag, reason codes and schedule; no global optimality claim | original skill, availability, inventory, load, non-overlap and control-window checks |
| vehicle_routing | Supported synthetic evaluator + native SUMO baseline | dispatch origin/destination/time and original closure policy | actual arrival/travel time, paths and trajectories | permissions, connectivity, closures, simulation horizon; unavailable Borden roads are rejected |
| groundwater_forward | Supported smoke evaluator | original no-pump physical configuration | original concentration, mass/flow metrics and indexed NPZ | original local model, clock, boundary/source conditions and schema |
| monitoring_well_optimization | Experimental | legacy monitor decisions | legacy source retained; complete release protocol pending | not executable through supported task CLI |
| emergency_response_optimization | Experimental | rates, start/stop and deployment decisions | forward/policy components exist; complete search acceptance pending | not a claimed v1 optimization benchmark |
| hydro_traffic_coupled_optimization | Experimental | dispatch and hydro decisions | Phase I coupling components retained; search acceptance pending | no claim that prior research runs establish a portable release task |

## Uniform result contract

`input.json` records task/run/scene identity, generation and experiment seeds,
candidate and source digests. `result.json` uses `task-result-v1` and embeds the
unchanged original engine envelope in `engine_result`; the frozen phase1-v1
envelope is not extended. `artifact-index.json` records relative paths, sizes and
SHA256. Failures produce `error.json` and never a successful artifact index.

All released scenes have version **1001**. This is a distinct deployment version
derived from historical version 1, not a rewrite of its frozen manifest. See
SCENARIO_PROVENANCE.json. Original source bounds, scientific config and physical
kernel bytes are unchanged. Full legacy Torch/monitor/control source is retained
for provenance and future work; its old entry points are not v1 supported APIs.

Only `maintenance_smoke` is enabled, with a 64 MiB per-run output bound and no
batch optimizer. `--mode official` is refused before creating an output directory.
Source observation files are limited to 1 MiB/4096 observations. Independent
scientific campaign admission is a separate capacity and protocol gate.
