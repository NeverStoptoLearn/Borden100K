# Code style

Use Python 3.10-compatible syntax, Vue 3 with JavaScript modules, and npm with the
checked-in lockfile. `.editorconfig` supplies UTF-8, spaces and ordinary LF endings.

## Python

Use snake_case for modules/functions/variables, PascalCase for classes and
UPPER_SNAKE_CASE for constants. Add types to new public helpers. Keep engine imports
at the execution boundary when their environment is optional. Include units in
scientific field names; preserve contract names and model variable meanings.
Keep exception context (`raise ... from exc`). Catch broadly only at a documented
process/CLI boundary that records or returns the failure; do not silently succeed.
Backend run diagnostics continue to use `Worker.log` and run-local event files.

Install the pinned Ruff from `requirements-dev.txt`, then run:

```sh
ruff check .
ruff format --check .
```

`pyproject.toml` limits this initial check/format scope to `borden_tasks`, shared
contracts, backend core, tests and the integrity command. It enables import-order,
undefined/unused-name and basic error checks. Format only an explicitly reviewed
module, for example `ruff format borden_tasks/__main__.py`. Extend the scope in a
separate reviewed change when bringing another existing module into compliance.

## Frontend

Use PascalCase Vue component files and camelCase JavaScript functions/modules.
Use the existing API helpers and separate replay preparation from rendering.
Cancel outstanding requests on scene changes/unmount, reject stale completions,
and dispose timers, sockets, observers and deck.gl resources.

From `phase1/frontend`:

```sh
npm run lint
npm run format:check
```

ESLint checks every source JavaScript/Vue file and Vitest test using the JavaScript
recommended and Vue essential rules. Browser globals apply to source, Node globals
to tool configuration/tests. Prettier checks the explicitly reviewed files listed
in `.prettierignore`: shared comparison/replay/request helpers, two small components,
their focused tests and tool configuration. `npm run format` writes only this set.
Expand that set module by module rather than reformatting every component at once.

## Byte-sensitive assets

Keep `borden3d_common.py`, `borden3d_config.json`, `MODEL_SHA256.json`, provenance,
schemas, scene JSON and network assets outside automatic rewriting. The existing
`.gitattributes` byte rules remain in effect. Ruff uses `force-exclude`; Prettier's
allowlist is entirely within the frontend source/configuration scope. Verify the
model and scene references with `python -B tools/check_integrity.py` after changes.

Tool references: [Ruff configuration](https://docs.astral.sh/ruff/configuration/),
[Vue ESLint guide](https://eslint.vuejs.org/user-guide/),
[Prettier ignore rules](https://prettier.io/docs/ignore.html).
