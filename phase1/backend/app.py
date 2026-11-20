import asyncio
import json
import os
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4
from fastapi import FastAPI, Header, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ConfigDict, Field
from .core import ROOT, Problem, validate, dump, safe_path, sha, write, build
from .store import Store
from .processes import execute
from .worker import SCIENCE


class Snapshot(BaseModel):
    """Internal CRUD DTO, not a new contract envelope kind."""
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$')
    version: int = Field(ge=1)
    source: dict[str, Any]
    data: dict[str, Any]


class Reference(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str
    version: int = Field(ge=1)


class Parameters(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    scenario: Reference
    risk_window_start_day: float = Field(ge=0)
    risk_window_stop_day: float = Field(gt=0)
    network_path: str | None = None
    traffic: dict[str, Any] | None = None
    resources: dict[str, Any] | None = None


RESOURCE_TYPES = {'vehicles': ('vehicle', 'vehicle_id'), 'crews': ('crew', 'crew_id'),
                  'equipment': ('equipment', 'equipment_id'), 'tasks': ('task_request', 'task_id'), 'inventory': ('stock', None)}


def create_app(home=None, science=SCIENCE):
    store = Store(home or os.environ.get('BORDEN_BACKEND_HOME', str(ROOT / 'phase1/backend/runtime')))
    app = FastAPI(title='Borden100K', version='1.0.0rc1', description='Groundwater, traffic and emergency resource planning API.')
    app.state.store = store

    @app.get('/api/v1/task-release/tasks')
    def task_release_catalog():
        from borden_tasks.core import TASKS
        return dict(version='1.0.0rc1',tasks=TASKS,project_hard_limit_bytes=200000000000,
                    official_admission='BLOCKED_PENDING_CAPACITY_ACCEPTANCE',default_mode='maintenance_smoke')

    @app.exception_handler(Problem)
    async def problem_handler(request: Request, exc: Problem):
        return JSONResponse(status_code=exc.http, content={'error': exc.error})

    @app.exception_handler(RequestValidationError)
    async def invalid_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={'error': dict(code='INVALID_INPUT', message=str(exc), retryable=False)})

    def envelope(value, kind):
        validate(value)
        if value['kind'] != kind or value['status'] != 'queued' or value['artifacts'] or value['data_provenance']['fixture']:
            raise Problem('INVALID_INPUT', 'Expected non-fixture queued ' + kind)

    @app.get('/api/v1/health')
    def health():
        return {'status': 'ok', 'model_build': build(), 'worker': 'separate process', 'fixture': False}

    def _create_scenario(value, registered=False):
        envelope(value, 'scenario')
        initial_version=1
        if registered and value['scenario_version']==1001:
            # A new release installation has no historical snapshots. Import
            # only the exact certified catalog genesis, never an arbitrary jump.
            from phase1.scenarios.registry import get_scene,read_asset
            from uuid import uuid5,NAMESPACE_URL
            scene=get_scene(value['scenario_id'],1001)
            fingerprint=scene['scene']['manifest_sha256']
            anchors=json.loads((ROOT/'SCENARIO_PROVENANCE.json').read_text('utf8'))
            if not any(a['scenario_id']==value['scenario_id'] and a['release_version']==1001 and a['release_manifest_sha256']==fingerprint for a in anchors):
                raise Problem('VERSION_CONFLICT','Release genesis provenance mismatch',409)
            expected=json.loads(read_asset(scene['manifest']['assets']['scenario_template']))
            expected['parameter_version']=f"{value['scenario_id']}.v1001.no_pump.{fingerprint[:12]}"
            expected['run_id']=str(uuid5(NAMESPACE_URL,f"dual:{value['scenario_id']}:1001:{fingerprint}"))
            if dump(value)!=dump(expected):raise Problem('VERSION_CONFLICT','Release genesis content mismatch',409)
            initial_version=1001
        p = value['payload']
        wells = p['wells']
        if len({w['well_id'] for w in wells}) != len(wells):
            raise Problem('INVALID_INPUT', 'Duplicate well ID')
        geo = p['georeference']
        if geo['status'] == 'uncalibrated':
            if any(geo[k] is not None for k in ('projected_crs', 'local_to_projected', 'anchor_wgs84')):
                raise Problem('GEOREFERENCE_UNAVAILABLE', 'Local-coordinate scenes require an empty geographic transform')
        else:
            # Calibration verification/approval is an integration gate, not inferred from user JSON.
            raise Problem('GEOREFERENCE_UNAVAILABLE', 'Use local layers or configure a registered geographic transform')
        if value['data_provenance']['calibration'] != 'none' or value['data_provenance']['classification'] == 'observed':
            raise Problem('INVALID_INPUT', 'Input provenance must match the selected model configuration')
        folder = store.home / 'preflight' / str(uuid4())
        write(folder / 'input.json', dict(request=value, context={}))
        try:
            code = execute([science, '-B', '-m', 'phase1.backend.bridge', 'preflight', str(folder / 'input.json'), str(folder / 'output.json')], folder, 60)
        except Exception as exc:
            raise Problem('ENGINE_FAILED', 'Scientific preflight unavailable: ' + str(exc)) from exc
        if code or not (folder / 'output.json').is_file():
            raise Problem('INVALID_INPUT', 'Scientific preflight rejected: ' + (folder / 'stderr.log').read_text('utf-8')[-4000:])
        preflight = json.loads((folder / 'output.json').read_text('utf-8'))
        store.put('scenario', value['scenario_id'], value['scenario_version'], dict(envelope=value, preflight=preflight),initial_version=initial_version)
        return value

    @app.post('/api/v1/scenarios', status_code=202)
    def create_scenario(value: dict[str, Any]):
        return _create_scenario(value)

    @app.get('/api/v1/scenarios/{ident}/versions/{version}')
    def get_scenario(ident: str, version: int):
        return store.get('scenario', ident, version)['envelope']

    from .scenes import install, guard
    install(app, store, lambda value:_create_scenario(value,registered=True), lambda kind, value: create_snapshot(kind, Snapshot.model_validate(value)))

    @app.get('/api/v1/{kind}')
    def list_snapshots(kind: str):
        if kind not in {*RESOURCE_TYPES, 'parameters', 'scenarios'}:
            raise Problem('INVALID_INPUT', 'Unknown collection', 404)
        key = 'scenario' if kind == 'scenarios' else kind
        with store.connect() as db:
            rows = db.execute('SELECT s.* FROM snapshots s WHERE kind=? AND version=(SELECT max(version) FROM snapshots x WHERE x.kind=s.kind AND x.id=s.id) ORDER BY id', (key,)).fetchall()
        return [json.loads(r['body']) for r in rows if not r['deleted']]

    @app.post('/api/v1/{kind}', status_code=202)
    def create_snapshot(kind: str, value: Snapshot):
        data = value.model_dump()
        validate(data['source'], 'source')
        if kind in RESOURCE_TYPES:
            definition, field = RESOURCE_TYPES[kind]
            validate(data['data'], definition)
            item = data['data']
            expected = item[field] if field else item['depot_id'] + '.' + item['item_id']
            if expected != data['id']:
                raise Problem('INVALID_INPUT', 'CRUD id differs from resource identity')
            if 'available_until_s' in item and item['available_until_s'] <= item['available_from_s']:
                raise Problem('INVALID_INPUT', 'Invalid availability interval')
            if kind == 'tasks' and (item['pump_duration_s'] <= 0 or item['earliest_start_s'] >= item['latest_stop_s']):
                raise Problem('INVALID_INPUT', 'Invalid task interval/duration')
        elif kind == 'parameters':
            if data['version'] != 1:
                raise Problem('INVALID_INPUT', 'parameter_version is an immutable ID; use a new ID for each parameter revision')
            try:
                params = Parameters.model_validate(data['data']).model_dump()
            except Exception as exc:
                raise Problem('INVALID_INPUT', str(exc)) from exc
            scenario = store.get('scenario', params['scenario']['id'], params['scenario']['version'])
            if not 0 <= params['risk_window_start_day'] < params['risk_window_stop_day'] <= scenario['envelope']['payload']['time_axis']['model_end_day']:
                raise Problem('INVALID_INPUT', 'Invalid immutable risk window')
            if params['network_path'] is not None:
                path = safe_path(ROOT / 'phase1/traffic', params['network_path'])
                params['network_path'] = path.relative_to(ROOT).as_posix()
                if not path.is_file():
                    raise Problem('INVALID_INPUT', 'Network file missing')
                validate(params['traffic'], 'traffic_request')
                if sha(path) != params['traffic']['network']['network_sha256']:
                    raise Problem('INVALID_INPUT', 'Network SHA mismatch')
            if params['resources'] is not None:
                # Resource inputs are pinned immutable CRUD references, expanded at parameter creation.
                params['resource_references'] = params['resources']
                resources = {}
                for collection in RESOURCE_TYPES:
                    refs = params['resources'].get(collection, [])
                    try:
                        parsed = [Reference.model_validate(r) for r in refs]
                    except Exception as exc:
                        raise Problem('INVALID_INPUT', str(exc)) from exc
                    resources[collection] = [store.get(collection, r.id, r.version)['data'] for r in parsed]
                if set(params['resources']) - set(RESOURCE_TYPES):
                    raise Problem('INVALID_INPUT', 'Unknown resource snapshot collection')
                params['resources'] = resources
            data['data'] = params
        else:
            raise Problem('INVALID_INPUT', 'Unknown collection', 404)
        return store.put(kind, data['id'], data['version'], data)

    @app.get('/api/v1/{kind}/{ident}/versions/{version}')
    def get_snapshot(kind: str, ident: str, version: int):
        if kind not in {*RESOURCE_TYPES, 'parameters'}:
            raise Problem('INVALID_INPUT', 'Unknown collection', 404)
        return store.get(kind, ident, version)

    @app.delete('/api/v1/{kind}/{ident}/versions/{version}', status_code=202)
    def delete_snapshot(kind: str, ident: str, version: int):
        key = 'scenario' if kind == 'scenarios' else kind
        if key not in {*RESOURCE_TYPES, 'parameters', 'scenario'}:
            raise Problem('INVALID_INPUT', 'Unknown collection', 404)
        old = store.get(key, ident, version)
        # Append tombstone; historical snapshots stay readable for reproducibility.
        store.put(key, ident, version + 1, old, deleted=True)
        return {'id': ident, 'version': version + 1, 'deleted': True}

    @app.post('/api/v1/runs', status_code=202)
    def submit(value: dict[str, Any], idempotency_key: str = Header(alias='Idempotency-Key')):
        envelope(value, 'run_request')
        if idempotency_key != value['payload']['idempotency_key']:
            raise Problem('INVALID_INPUT', 'Header/payload Idempotency-Key mismatch')
        scene_binding = guard(store, value)
        with store.connect() as db:
            existing = db.execute('SELECT request,body FROM jobs WHERE key=? OR id=?', (idempotency_key,value['run_id'])).fetchone()
        if existing:
            if existing['request'] != dump(value):
                raise Problem('VERSION_CONFLICT', 'Idempotency key or run_id conflict', 409)
            return json.loads(existing['body'])
        scenario = store.get('scenario', value['scenario_id'], value['scenario_version'])
        params = store.get('parameters', value['parameter_version'], 1)
        s = scenario['envelope']
        if params['data']['scenario'] != dict(id=value['scenario_id'], version=value['scenario_version']):
            raise Problem('VERSION_CONFLICT', 'Parameter set belongs to another scenario version', 409)
        if any(value[k] != s[k] for k in ('experiment_id', 'units', 'data_provenance')):
            raise Problem('INVALID_INPUT', 'Scenario identity/provenance mismatch')
        for k in ('code_sha256', 'config_sha256'):
            if value['model_build'][k] != build()[k]:
                raise Problem('INVALID_INPUT', 'Run must use current orchestrator build from /health')
        if value['payload']['mode'] != 'no_pump' and any(params['data'][k] is None for k in ('network_path', 'traffic', 'resources')):
            raise Problem('INVALID_INPUT', 'Dispatch mode requires traffic/network/resource snapshots')
        return store.submit(value, dict(scenario=s, preflight=scenario['preflight'], parameters=params, scene_binding=scene_binding))

    @app.get('/api/v1/runs/{run_id}')
    def get_run(run_id: str):
        return json.loads(store.job(run_id)['body'])

    @app.get('/api/v1/runs/{run_id}/stages')
    def stages(run_id: str):
        store.job(run_id)
        with store.connect() as db:
            return [dict(stage=r['stage'], state=r['state'], envelope=json.loads(r['body'])) for r in db.execute('SELECT * FROM stages WHERE run_id=? ORDER BY rowid', (run_id,))]

    @app.post('/api/v1/runs/{run_id}/cancel')
    def cancel(run_id: str):
        return store.cancel(run_id)

    @app.get('/api/v1/runs/{run_id}/artifacts/{artifact_id}')
    def download(run_id: str, artifact_id: str):
        row = store.job(run_id)
        with store.connect() as db:
            a = db.execute('SELECT body FROM artifacts WHERE run_id=? AND id=?', (run_id, artifact_id)).fetchone()
        if a is None:
            raise Problem('INVALID_INPUT', 'Artifact not published', 404)
        a = json.loads(a['body'])
        path = safe_path(store.home / 'runs' / run_id, a['path'])
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise Problem('ARTIFACT_INVALID', str(exc)) from exc
        import hashlib
        if hashlib.sha256(content).hexdigest() != a['sha256']:
            raise Problem('ARTIFACT_INVALID', 'Download SHA mismatch')
        return Response(content, media_type=a['media_type'], headers={'ETag': '"' + a['sha256'] + '"', 'X-Content-Type-Options': 'nosniff'})

    @app.get('/api/v1/runs/{run_id}/exports/earth.kmz')
    def earth_kmz(run_id: str, profile: Literal['earth_web', 'earth_pro'] = 'earth_web'):
        from .earth import download
        return download(store, run_id, profile, 'kmz')

    @app.get('/api/v1/runs/{run_id}/exports/earth.kml')
    def earth_kml(run_id: str, profile: Literal['earth_web', 'earth_pro'] = 'earth_web'):
        from .earth import download
        return download(store, run_id, profile, 'kml')

    @app.websocket('/api/v1/runs/{run_id}/events')
    async def events(websocket: WebSocket, run_id: str, after: int = 0):
        try:
            store.job(run_id)
        except Problem:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        try:
            while True:
                for event in store.events(run_id, after):
                    await websocket.send_json(event)
                    after = event['sequence']
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=.1)
                except asyncio.TimeoutError:
                    pass
        except WebSocketDisconnect:
            pass

    # Literal collections (/runs, /scenarios) precede internal generic CRUD routes.
    app.router.routes.sort(key=lambda route: route.path.count('{'))
    return app


app = create_app()
