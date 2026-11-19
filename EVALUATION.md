# Evaluation protocol

Use the stated scene version, original kernel/config SHA, explicit seed and a new
output directory. Keep Borden and synthetic identities separate. Record the
scenario-generation seed independently from the evaluation seed (default 42).

## Source identification

`prepare` uses the existing exact-cell prescribed-concentration forward model to
generate synthetic observations. The reference field is marked evaluation-only.
The original inverse evaluator is a **smooth source penalty approximation**:
`0.5 * mean(((prediction - observation) / 500)^2)`. This approximation is part of
the existing scientific implementation; the release does not equate it with the
forward boundary condition or claim perfect truth recovery. Its gradient is the
existing discrete adjoint. Report objective, x/y/C, gradient, source SHA, model
signature and bounds. The baseline uses prior midpoints, not truth parameters.

## Routing and scheduling

Report actual SUMO routes, reachability, arrival and travel time, and whether
closures changed the path/time. Unreachable or operationally infeasible results
must remain visible; a decoder query can succeed while `feasible=false`.
Scheduling consumes verified upstream artifacts and the same clock. Do not
substitute estimated travel time for actual SUMO execution.

Road/resource timestamps are seconds from the recorded UTC scenario origin.
`model_day = reference_model_day + scenario_seconds / 86400`. Hydro uses days,
nonnegative extraction in m3/day, concentration mg/L and mass kg. The original
source switch at day 14600 and final day 21900 are preserved; no implicit time
reset or new health-risk interpretation is introduced.

## Acceptance boundaries

Validate `schemas/task-release-v1.schema.json`, then the embedded frozen
phase1-v1 engine envelope where present. Check all artifact SHA values. Compare
scientific values rather than UUID-bearing file hashes across independent runs.
Keep environment versions and validation scope explicit. Smoke, fixture tests,
API tests, native traffic tests and browser E2E are different evidence classes.
Full inverse training, multiseed optimizer/Pareto studies and field calibration
are not established by the small release acceptance suite.
