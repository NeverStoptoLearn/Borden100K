import hashlib
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / 'schemas/phase1-v1.schema.json').read_text('utf-8'))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
IDENTITY = ('contract_version', 'experiment_id', 'run_id', 'scenario_id', 'scenario_version', 'parameter_version', 'seed', 'units')
TERMINAL = {'succeeded', 'failed', 'cancelled'}


class Problem(Exception):
    def __init__(self, code, message, http=422):
        self.error = dict(code=code, message=str(message), retryable=False)
        self.http = http
        super().__init__(str(message))


def dump(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(path):
    with Path(path).open('rb') as f:
        h = hashlib.sha256()
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
        return h.hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def validate(value, definition=None):
    try:
        dump(value)
        validator = VALIDATOR if definition is None else Draft202012Validator(
            {'$ref': '#/$defs/' + definition, '$defs': SCHEMA['$defs']}, format_checker=FormatChecker())
        validator.validate(value)
    except Exception as exc:
        raise Problem('INVALID_INPUT', str(exc)) from exc


def safe_path(root, name):
    if not name or ':' in name or '\\' in name or name.startswith('/') or any(p in ('', '.', '..') for p in name.split('/')):
        raise Problem('ARTIFACT_INVALID', 'Unsafe relative path')
    root = Path(root).resolve()
    path = root / name
    # Reject links and junctions, including links pointing inside the root.
    for parent in (path, *path.parents):
        if parent == root:
            break
        if parent.is_symlink() or (hasattr(parent, 'is_junction') and parent.is_junction()):
            raise Problem('ARTIFACT_INVALID', 'Linked artifact path')
    if not path.resolve().is_relative_to(root):
        raise Problem('ARTIFACT_INVALID', 'Path escaped run root')
    return path


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(dump(value), 'utf-8')
    os.replace(tmp, path)


def build():
    return dict(engine_id='orchestrator', engine_version='API-002', code_sha256=sha(__file__),
                config_sha256=sha(ROOT / 'borden3d_config.json'))


def status(request, state, previous=None, error=None, artifacts=None, progress=0):
    result = dict(request, kind='run_status', status=state, error=error, artifacts=artifacts or [],
                  model_build=build(), payload=dict(previous_status=previous, updated_at_utc=now(), progress_fraction=progress))
    validate(result)
    return result
