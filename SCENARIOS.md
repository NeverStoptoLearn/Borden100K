# Independent scenarios

| Identity | Service frame | Origin and interpretation | Current scope |
|---|---|---|---|
| borden_reference_100k, version 1001 | EPSG:32617, 100 km × 100 km descriptive service area | public Government of Canada locator metadata; local groundwater baseline remains synthetic/uncalibrated | no_pump/physical evaluation; target-road traffic blocked |
| synthetic_multiterrain_100k, version 1001 | synthetic local metres, 0..100000 in each horizontal direction; no WGS84 claim | fixed-seed procedural roads and display layers; synthetic resources and hydro baseline | released SUMO baseline and local physical evaluation |

Configurations, manifests, source attribution, coordinates and results are
separate. Each scene has its own `hydro-config.json`. Their certified hydro smoke
parameters currently have identical bytes: this is shared baseline physics, not
evidence for two independently calibrated aquifers. Both preserve the original
local fan-shaped domain and 21900-day horizon. The 100 km service area is not a
100 km aquifer grid.

The Borden locator does not register the local numerical model to the field site.
No road network, observed contamination measurements or Google imagery is
included for that reference context. Traffic availability remains explicitly
blocked instead of borrowing the synthetic network.

Historical version 1 inputs remain in the original research repository. New
version 1001 removes private interpreter descriptions, changes asset paths and
removes XML comments. The synthetic network attribute tree is verified unchanged;
new derived files receive new SHA256 without editing historical evidence. See
`SCENARIO_PROVENANCE.json` and `phase1/scenarios/catalog.json`.
