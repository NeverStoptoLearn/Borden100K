# Data, provenance and redistribution

The repository includes only small project-generated scene inputs, schemas,
explicit fixtures and the unchanged scientific configuration. It does not include
large historical ZIP/NPZ/XML/GZ outputs, old model checkpoints, installed software,
private databases, map tiles or third-party road databases.

- **Borden reference locator:** Government of Canada federal contaminated-sites
  registry, site 11022032; [Open Government Licence – Canada](https://open.canada.ca/en/open-government-licence-canada).
  This is descriptive location metadata, not observed hydro data or a survey.
- **Synthetic service network:** project-generated fixed-seed network and display
  layers, with source attribution and new version 1001 hashes in the manifests.
  XML comment removal changes byte hashes but not the network attribute tree.
- **Hydro inputs:** original synthetic, uncalibrated local model. The two scene
  configuration files have separate paths and identities and currently identical
  certified physical parameters.
- **Observations:** generate locally with `borden_tasks prepare`. The original
  generator writes observations, grid metadata and an evaluation-only reference.
  These are not downloaded field measurements and are not checked into Git.
- **Resource fixture:** explicitly `fixture=true`; it tests decoder structure
  only. For non-fixture scheduling supply a verified actual traffic result.

Run the repository unit tests to validate bundled asset SHA values. For SUMO,
install version 1.28.0 from the official distribution and configure its executable
in your own environment; Python bindings are pinned in requirements-traffic.txt.
Third-party software retains its own license. Apache-2.0 for project-owned source
does not relicense external observations, roads, imagery or binaries.

There is no default download of proprietary map resources. Google Maps 3D is an
optional independently enabled renderer; its imagery is not redistributed or
treated as a road-data license. Historical external datasets and legacy study
outputs are not prerequisites for the minimum release examples.
