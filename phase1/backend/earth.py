"""Read-only derived Earth downloads. Never run engines or alter job artifacts."""
import copy
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from .core import ROOT, Problem, dump, safe_path, sha, validate


def fingerprint(store, run_id, profile):
    try:
        UUID(run_id)
    except (ValueError, TypeError):
        raise Problem('INVALID_INPUT', 'Invalid run_id', 422)
    row = store.job(run_id)
    status = json.loads(row['body'])
    validate(status)
    if row['state'] != 'succeeded' or status['status'] != 'succeeded':
        raise Problem('INVALID_INPUT', 'Only succeeded runs can be exported', 409)
    if status['run_id'] != run_id or status['data_provenance']['fixture']:
        raise Problem('INVALID_INPUT', 'Production export requires a matching non-fixture run', 422)
    context = json.loads(row['context'])
    original = context['scenario']
    scenario = copy.deepcopy(original)
    for key in ('run_id', 'parameter_version', 'seed'):
        scenario[key] = status[key]
    base = safe_path(store.home, 'runs/' + run_id)
    inputs = []
    seen = set()
    for artifact in status['artifacts']:
        if artifact['artifact_id'] in seen:
            raise Problem('ARTIFACT_INVALID', 'Duplicate source artifact ID')
        seen.add(artifact['artifact_id'])
        path = safe_path(base, artifact['path'])
        if not path.is_file() or sha(path) != artifact['sha256']:
            raise Problem('ARTIFACT_INVALID', 'Source artifact missing or SHA mismatch')
        inputs.append(dict(artifact_id=artifact['artifact_id'], path=artifact['path'], sha256=artifact['sha256']))
    code = {p.relative_to(ROOT).as_posix(): sha(p) for p in sorted((ROOT/'phase1/earth_export').rglob('*.py'))}
    source = dict(profile=profile, exporter_code=code, scenario_snapshot_sha256=hashlib.sha256(dump(original).encode()).hexdigest(),
                  execution_scenario_sha256=hashlib.sha256(dump(scenario).encode()).hexdigest(),
                  context_sha256=hashlib.sha256(row['context'].encode()).hexdigest(),
                  status_sha256=hashlib.sha256(row['body'].encode()).hexdigest(), artifacts=inputs,
                  view_metadata=None, export_contract='earth-export-v1')
    digest = hashlib.sha256(dump(source).encode()).hexdigest()
    return scenario, status, context['parameters'], base, source, digest


def download(store, run_id, profile='earth_web', extension='kmz'):
    if profile not in ('earth_web', 'earth_pro') or extension not in ('kml', 'kmz'):
        raise Problem('INVALID_INPUT', 'Unsupported Earth export profile or extension')
    try:
        scenario, status, parameters, base, source, key = fingerprint(store, run_id, profile)
    except Problem:
        raise
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise Problem('ARTIFACT_INVALID', 'Invalid export source: ' + str(exc)) from exc
    folder = safe_path(store.home, f'exports/{run_id}/{profile}')
    folder.mkdir(parents=True, exist_ok=True)
    cache = safe_path(store.home, f'exports/{run_id}/{profile}/{key}')
    if not cache.exists():
        temp = safe_path(store.home, f'exports/{run_id}/{profile}/tmp-{uuid4().hex}')
        try:
            from phase1.earth_export import export_run
            report = export_run(scenario=scenario, run_status=status, parameters=parameters,
                                artifact_root=base, output_dir=temp, profile=profile,
                                view_metadata=None, allow_fixture=False)
            outputs = {}
            for kind in ('kml', 'kmz', 'manifest'):
                path = Path(report[kind + '_path'])
                relative = path.resolve().relative_to(temp.resolve()).as_posix()
                checked = safe_path(temp, relative)
                if not checked.is_file():
                    raise Problem('ARTIFACT_INVALID', 'Exporter output missing')
                outputs[kind] = dict(path=relative, sha256=sha(checked))
            metadata = dict(source=source, digest=key, outputs=outputs,
                            geometry_mode=report['geometry_mode'], warnings=report['warnings'])
            (temp/'backend-export.json').write_text(dump(metadata), encoding='utf-8')
            # Concurrent producers publish complete directories. Never overwrite a winner.
            try:
                os.rename(temp, cache)
            except OSError:
                if not cache.is_dir():
                    raise
        except Problem:
            raise
        except (ValueError, KeyError, TypeError, OSError, ImportError) as exc:
            raise Problem('ARTIFACT_INVALID', 'Earth export rejected: ' + str(exc)) from exc
    try:
        metadata_path = safe_path(cache, 'backend-export.json')
        metadata = json.loads(metadata_path.read_text('utf-8'))
        if not isinstance(metadata, dict) or not isinstance(metadata.get('outputs'), dict) or set(metadata['outputs']) != {'kml','kmz','manifest'}:
            raise ValueError('Invalid cache metadata structure')
        if metadata['digest'] != key or metadata['source'] != source:
            raise ValueError('Cache source identity mismatch')
        for item in metadata['outputs'].values():
            if not isinstance(item, dict) or set(item) != {'path','sha256'}:
                raise ValueError('Invalid cache output structure')
            path = safe_path(cache, item['path'])
            if not path.is_file() or sha(path) != item['sha256']:
                raise ValueError('Cache output SHA mismatch')
        item = metadata['outputs'][extension]
        content = safe_path(cache, item['path']).read_bytes()
        if hashlib.sha256(content).hexdigest() != item['sha256']:
            raise ValueError('Cache changed during read')
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise Problem('ARTIFACT_INVALID', 'Invalid Earth export cache: ' + str(exc)) from exc
    from fastapi.responses import Response
    return Response(content, media_type='application/vnd.google-earth.' + ('kmz' if extension == 'kmz' else 'kml+xml'),
                    headers={'Content-Disposition': f'attachment; filename="borden-{run_id}-{profile}.{extension}"',
                             'ETag': '"' + item['sha256'] + '"', 'X-Content-Type-Options': 'nosniff',
                             'X-Borden-Georeference': metadata['geometry_mode'].replace('_', '-')})
