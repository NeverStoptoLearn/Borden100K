"""Backend status envelopes; shared contract imports remain compatible."""

from datetime import datetime, timezone

from phase1.contracts import (
    ROOT as ROOT,
    SCHEMA as SCHEMA,
    VALIDATOR as VALIDATOR,
    Problem as Problem,
    dump as dump,
    safe_path as safe_path,
    sha as sha,
    validate as validate,
    write as write,
)

IDENTITY = (
    'contract_version',
    'experiment_id',
    'run_id',
    'scenario_id',
    'scenario_version',
    'parameter_version',
    'seed',
    'units',
)
TERMINAL = {'succeeded', 'failed', 'cancelled'}


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def build() -> dict:
    return dict(
        engine_id='orchestrator',
        engine_version='API-002',
        code_sha256=sha(__file__),
        config_sha256=sha(ROOT / 'borden3d_config.json'),
    )


def status(request, state, previous=None, error=None, artifacts=None, progress=0):
    result = dict(
        request,
        kind='run_status',
        status=state,
        error=error,
        artifacts=artifacts or [],
        model_build=build(),
        payload=dict(previous_status=previous, updated_at_utc=now(), progress_fraction=progress),
    )
    validate(result)
    return result
