# Local release-candidate acceptance

Status: **local RC**, not a public release or a completed optimization benchmark.
Project-wide capacity acceptance is **BLOCKED** above 200,000,000,000 bytes.

| Check | Actual result |
|---|---|
| Independent source export, all manifest file SHA256 values | PASS |
| `python -B -m unittest discover -s tests -p test_task_release.py -v` | PASS, 10 tests in an independent source directory |
| `node node_modules/vitest/vitest.mjs run` from `phase1/frontend` | PASS, 79 tests / 10 files |
| `node node_modules/vite/bin/vite.js build` | PASS |
| Browser/API scene and task catalog | PASS, no browser page/HTTP errors, no Google requests |
| Fresh API scene import, repeat import, actual SciPy worker, artifact SHA and SQLite reopen | PASS, non-fixture smoke |
| Frozen root and separate chapter scientific bytes | PASS against source provenance |
| Inverse evaluator at prior midpoint `[280,355,500]` | PASS; objective `0.0007390396635860738`, finite original adjoint |
| Native SUMO normal / closure | PASS; two vehicles arrived at 1249/1117 s and 2358/2159 s respectively |
| Scheduling from actual SUMO output | PASS, `fixture=false`, feasible |
| Default official execution admission | PASS: refused before scientific execution |
| Full optimizer / multiseed Pareto campaign | NOT_RUN; Experimental |
| Field calibration / Borden regional-road acceptance | NOT_RUN |
| Large historical file retirement / XML migration / rollback retirement | NOT_RUN; not authorized |
| Public push / release / history rewrite | NOT_RUN |

The accepted numerical tuple is Python 3.10.19, NumPy 2.2.6, SciPy 1.15.3.
Backend tests used the separately pinned Python 3.11 environment. SUMO was
1.28.0. Existing environments were reused; no large simulation was run.
UUID-bearing output files naturally differ between runs; numerical and source
checks have separate meanings. Resource fixture tests are explicitly labeled.

The independent API test also rejects public CRUD attempts to jump to an
unregistered version. A fresh installation may import only the exact v1001
catalog genesis anchored by SCENARIO_PROVENANCE.json; existing immutable
snapshot version ordering remains enforced. It does not import or rewrite
historical research databases.

Machine-specific audit logs and cleanup manifests remain local and are excluded
from the public source payload. RELEASE_FILES.json lists source assets and their
byte identities. Original repository ancestry contains historical large objects;
the small standalone repository is the prepared publication candidate.
