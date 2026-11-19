# Baselines

| Baseline | Configuration | Status |
|---|---|---|
| fixed prior source candidate | midpoint of the unchanged x/y/C inverse bounds; one original objective/gradient evaluation; seed 42 | supported bounded baseline |
| no-pump Darcy/FV | original smoke grid/profile; original source switch and 21900-day horizon | supported physical evaluator |
| native SUMO normal/closure | synthetic version 1001 network; original dispatch policy; no closed edge in normal mode; original closures in closure mode | supported native baseline |
| priority resource decoder | original descending priority then task-ID ordering; unchanged one-use personnel/equipment and stock rules | supported feasibility baseline |
| historical MITree/NSGA-II/neural monitor/control methods | original code retained without algorithm edits | Experimental; not a portable accepted v1 campaign |

No new optimization algorithm is introduced. The source midpoint is a reference
candidate, not a trained estimator. To compare a new estimator, pass an explicit
candidate to the same frozen objective and preserve the same observation/scene
identity. Additional optimizer budgets, splits and statistical claims require a
separate experimental protocol and storage admission.
