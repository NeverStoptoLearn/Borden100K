# Contributing

Start from a source checkout and follow the [running guide](docs/usage.md).
Reuse a compatible environment. Install `requirements-dev.txt` for Python
development; use `npm ci` in `phase1/frontend` for the JavaScript toolchain.
The numerical examples use the Python/NumPy/SciPy versions in the running guide.

Create a focused branch from `main`. Keep changes small and describe the user
behavior or maintenance problem in the commit and pull request. Include the
checks you ran and explain any checks requiring an external engine.

Before submitting:

```sh
ruff check .
ruff format --check .
python -B tools/check_integrity.py
python -B -m unittest discover -s tests -v
```

For frontend changes, run from `phase1/frontend`:

```sh
npm run lint
npm run format:check
npm test -- --run
npm run build
```

The lint/format scope grows incrementally; see [code style](docs/development/code-style.md).
Include a focused regression test for changes to validation, execution or replay.
Maintain command-line options, API errors, scene identities and artifact formats.
Update the [interface guide](docs/development/interfaces.md) and usage documentation
when an interface changes. Document units and coordinate frames explicitly.

The model files, scientific checksums, schemas and referenced scenario assets are
byte-sensitive. Keep them outside formatting passes. Changes to scientific methods
or frozen contracts need a separate review with their own validation.

Do not commit environments, generated runs, local databases, credentials or build
outputs. Keep attribution in `NOTICE` and include the source/license of added data.
