# Public Task Release: v1.0-rc1

Repository: https://github.com/NeverStoptoLearn/Borden100K

This is the first public source release candidate of the bounded task toolkit.
The source is published under Apache-2.0. It is not a completed multiseed
optimization benchmark or a calibrated regional digital twin.

## Supported scope

- Source identification: existing objective/adjoint evaluator and fixed-prior baseline.
- Groundwater forward modelling: original local no-pump smoke evaluator.
- Resource scheduling: deterministic feasibility/schedule decoder.
- Vehicle routing: native SUMO evaluation on the synthetic scenario.

Monitoring-well, emergency-response and Hydro–Traffic coupled optimization
remain Experimental. The Borden reference locator and synthetic service scene
have separate identities; the service extent is not the local aquifer extent.

## Publication verification

On 2026-11-20 the source distribution was independently exported and checked:

| Check | Result |
|---|---|
| Distribution-member SHA256, including frozen scientific sources | PASS |
| Release regression suite | PASS, 12 tests |
| Isolated safety and Git-hook suite | PASS, 11 tests |
| CLI registry and documented argument interfaces | PASS |
| No runtime datasets, installed environments or private operational records in the source payload | PASS |
| Complete optimization campaigns or fresh large physical simulations | NOT_RUN; outside this publication |

The verification uses small fixtures and existing evidence; it does not rerun a
historical scientific campaign. The structural regression checks use an existing
Python 3.11 environment. The accepted numerical smoke profile is Python 3.10.19,
NumPy 2.2.6 and SciPy 1.15.3. Earlier scientific smoke and browser results are
recorded in [VALIDATION.md](VALIDATION.md), not newly claimed as publication runs.

## Reproduce

Use the source checkout, the pinned requirements, the two bundled scene manifests
and the commands in [README.md](README.md). Verify task/scene identity, generation
and evaluation seeds, configuration/source SHA and artifact-index hashes.

`RELEASE_FILES.json` enumerates distribution members and their byte identities;
it excludes itself. `SOURCE_PROVENANCE.json` and `SHA256_MANIFEST.json` preserve
the original scientific identities. No frozen scientific source or historical
checksum is rewritten for publication.

The numerical exports in `data/baselines/final-fronts.json.gz` are historical
comparisons with original source identities, not certified scores for a new v1
campaign. Original campaign budgets, individual physical trajectories, local
databases and private machine state are not bundled or required by the minimum
examples. Their absence is not a claim that full historical replay is supported.
