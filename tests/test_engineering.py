"""Compatibility checks for the engineering boundary, without engine mocks as results."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from borden_tasks.__main__ import build_parser, main
from phase1 import contracts
from phase1.backend import core
from phase1.scenarios import registry


class EngineeringTests(unittest.TestCase):
    def test_backend_reexports_keep_exception_and_validator_identity(self):
        for name in (
            'ROOT',
            'Problem',
            'dump',
            'sha',
            'safe_path',
            'write',
            'validate',
            'SCHEMA',
            'VALIDATOR',
        ):
            self.assertIs(getattr(core, name), getattr(contracts, name))

    def test_scene_import_does_not_load_backend(self):
        command = (
            'import sys; import phase1.scenarios.registry; '
            'assert not any(k.startswith("phase1.backend") for k in sys.modules)'
        )
        result = subprocess.run(
            [sys.executable, '-B', '-c', command],
            cwd=contracts.ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_artifact_paths_and_atomic_json_keep_original_contract(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('', '../x', 'a/../x', '/x', 'C:/x', 'a\\x', 'a//x', './x'):
                with self.subTest(name=name), self.assertRaises(contracts.Problem) as raised:
                    contracts.safe_path(root, name)
                self.assertEqual(raised.exception.error['code'], 'ARTIFACT_INVALID')
            target = contracts.safe_path(root, 'nested/value.json')
            contracts.write(target, {'z': 2, 'a': 1})
            self.assertEqual(target.read_text(), '{"a":1,"z":2}')
            self.assertFalse(target.with_suffix('.json.tmp').exists())
            with self.assertRaises(ValueError):
                contracts.dump({'x': float('nan')})

    def test_scene_asset_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / 'input.txt'
            asset.write_bytes(b'original')
            ref = dict(path='input.txt', sha256=contracts.sha(asset), media_type='text/plain')
            with patch.object(registry, 'ROOT', root):
                self.assertEqual(registry.read_asset(ref), b'original')
                asset.write_bytes(b'changed')
                with self.assertRaises(contracts.Problem) as raised:
                    registry.read_asset(ref)
                self.assertEqual(raised.exception.http, 409)
                self.assertEqual(raised.exception.error['code'], 'ARTIFACT_INVALID')

    def test_cli_arguments_and_machine_readable_failure_are_compatible(self):
        args = build_parser().parse_args(
            [
                'run',
                '--task',
                'vehicle_routing',
                '--scenario',
                'synthetic_multiterrain_100k',
                '--output',
                'unused',
            ]
        )
        self.assertEqual(
            (args.seed, args.mode, args.traffic_variant), (42, 'maintenance_smoke', 'normal')
        )
        with patch(
            'borden_tasks.__main__.run', side_effect=contracts.Problem('EXPECTED', 'reason')
        ):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(
                    ['run', '--task', args.task, '--scenario', args.scenario, '--output', 'unused']
                )
        self.assertEqual(code, 2)
        self.assertEqual(
            json.loads(output.getvalue()), dict(status='failed', code='ERROR', error='reason')
        )

    def test_api_error_envelope_and_invalid_snapshot_do_not_change(self):
        from fastapi.testclient import TestClient

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch.dict(os.environ, {'BORDEN_BACKEND_HOME': temporary}),
        ):
            from phase1.backend.app import create_app

            with TestClient(create_app(temporary)) as client:
                response = client.get('/api/v1/not-a-collection')
                self.assertEqual(response.status_code, 404)
                self.assertEqual(
                    response.json(),
                    {
                        'error': {
                            'code': 'INVALID_INPUT',
                            'message': 'Unknown collection',
                            'retryable': False,
                        }
                    },
                )
                response = client.post('/api/v1/parameters', json={'unexpected': True})
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()['error']['code'], 'INVALID_INPUT')

    def test_portable_integrity_command(self):
        result = subprocess.run(
            [sys.executable, '-B', 'tools/check_integrity.py'],
            cwd=contracts.ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(
            (report['model_files'], report['schema_files'], len(report['scenes'])), (2, 2, 2)
        )


if __name__ == '__main__':
    unittest.main()
