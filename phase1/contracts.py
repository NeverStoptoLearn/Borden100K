"""Shared frozen-envelope validation, file integrity and safe artifact paths."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'schemas/phase1-v1.schema.json').read_text('utf-8'))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


class Problem(Exception):
    def __init__(self, code: str, message: object, http: int = 422) -> None:
        self.error = dict(code=code, message=str(message), retryable=False)
        self.http = http
        super().__init__(str(message))


def dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(path: str | Path) -> str:
    with Path(path).open('rb') as f:
        h = hashlib.sha256()
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
        return h.hexdigest()


def validate(value: Any, definition: str | None = None) -> None:
    try:
        dump(value)
        validator = (
            VALIDATOR
            if definition is None
            else Draft202012Validator(
                {'$ref': '#/$defs/' + definition, '$defs': SCHEMA['$defs']},
                format_checker=FormatChecker(),
            )
        )
        validator.validate(value)
    except Exception as exc:
        raise Problem('INVALID_INPUT', str(exc)) from exc


def safe_path(root: str | Path, name: str) -> Path:
    if (
        not name
        or ':' in name
        or '\\' in name
        or name.startswith('/')
        or any(p in ('', '.', '..') for p in name.split('/'))
    ):
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


def write(path: str | Path, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(dump(value), 'utf-8')
    os.replace(tmp, path)
