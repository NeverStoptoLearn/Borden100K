"""Independent durable worker: python -m phase1.backend.worker --home PATH."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time
import traceback
import psutil
from .core import ROOT, Problem, dump, write, sha, validate, TERMINAL
from .store import Store
from .processes import execute, alive, stop_tree
from .artifacts import check_result

# Release portability change; scientific worker and kernels are unchanged.
SCIENCE = os.environ.get('BORDEN_SCIENCE_PYTHON', sys.executable)


class Worker:
    def __init__(self, store, science=SCIENCE, traffic=None, timeout=300):
        self.store, self.science, self.traffic, self.timeout = store, science, traffic or science, timeout
        self.pid = os.getpid()
        self.birth = psutil.Process().create_time()

    def log(self, ident, message, level='INFO'):
        path = self.store.home / 'runs' / ident / 'events.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        row = self.store.job(ident)
        req = json.loads(row['request'])
        with path.open('a', encoding='utf-8') as f:
            f.write(dump(dict(run_id=ident, scenario_version=req['scenario_version'], event_time_s=0,
                              level=level, module='backend', message=message)) + '\n')

    def diagnostics(self, ident):
        base = self.store.home / 'runs' / ident
        result = []
        for path in sorted(base.rglob('*')):
            if path.is_file() and (path.suffix in ('.log', '.jsonl') or path.name == 'error.json' or path.name.endswith('-input.json')):
                relative = path.relative_to(base).as_posix()
                result.append(dict(artifact_id='diagnostic-' + str(len(result)), path=relative, sha256=sha(path),
                    role='log' if path.suffix in ('.log', '.jsonl') else 'config', media_type='text/plain' if path.suffix == '.log' else 'application/json', units='see units'))
        return result

    def tick(self, ident, process=None):
        with self.store.connect() as db:
            row = db.execute('SELECT cancel FROM jobs WHERE id=?', (ident,)).fetchone()
            processes = [] if process is None else [dict(pid=process.p.pid, birth=process.birth)]
            db.execute('UPDATE jobs SET heartbeat=?,processes=? WHERE id=?', (time.time(), dump(processes), ident))
        if row['cancel']:
            raise Problem('CANCELLED', 'cancel_requested; process tree cleanup required')

    def recover(self, stale_s=30):
        with self.store.connect() as db:
            candidates = [row['id'] for row in db.execute("SELECT id FROM jobs WHERE state='running'")]
        def expired(row):
            return not alive(row['owner'], row['owner_birth']) or time.time() - row['heartbeat'] > stale_s
        def cleanup(row):
            if alive(row['owner'], row['owner_birth']):
                stop_tree(row['owner'], row['owner_birth'])
            for p in json.loads(row['processes']):
                stop_tree(p['pid'], p['birth'])
        for ident in candidates:
            # Owner death closes its Windows jobs. Recheck while holding the
            # durable write transaction; concurrent reapers must not fail twice.
            self.store.recover_running(ident, expired, cleanup)

    def stage(self, ident, name, request, context):
        from phase1.scenarios.registry import verify_binding
        job = self.store.job(ident)
        verify_binding(json.loads(job['request']), json.loads(job['context']).get('scene_binding'), json.loads(job['context']))
        self.tick(ident)
        self.log(ident, 'Starting ' + name)
        validate(request)
        base = self.store.home / 'runs' / ident
        staging = base / 'staging' / name
        bundle = base / (name + '-input.json')
        write(bundle, dict(request=request, context=context))
        with self.store.connect() as db:
            db.execute('INSERT INTO stages VALUES(?,?,?,?)', (ident, name, 'running', dump(request)))
        py = self.science if name == 'hydro' else self.traffic if name == 'traffic' else sys.executable
        code = execute([py, '-B', '-m', 'phase1.backend.bridge', name, str(bundle), str(staging)],
                       base / 'process-logs' / name, self.timeout, lambda p: self.tick(ident, p))
        path = staging / ident / 'result.json'
        if not path.is_file():
            raise Problem('ENGINE_FAILED', f'{name} exited {code}; no result; see process logs')
        result = json.loads(path.read_text('utf-8'))
        check_result(result, request, staging / ident)
        if name=='traffic':
            from .dynamic_artifacts import check as check_dynamic
            check_dynamic(result,request,context,staging/ident)
        if code:
            raise Problem('ENGINE_FAILED', f'{name} exited {code}')
        with self.store.connect() as db:
            db.execute('UPDATE stages SET state=?,body=? WHERE run_id=? AND stage=?', ('succeeded', dump(result), ident, name))
            from .core import status
            body = status(request, 'running', 'running', progress={'traffic': .25, 'resources': .5, 'hydro': .9}[name])
            db.execute('UPDATE jobs SET body=? WHERE id=?', (dump(body), ident))
            db.execute('INSERT INTO events(run_id,body) VALUES(?,?)', (ident, dump(body)))
        return result

    def pipeline(self, ident, request, context):
        # Release admission guard only; no scientific calculation is changed.
        if os.environ.get('BORDEN_RELEASE_MAINTENANCE_SMOKE') != '1':
            raise Problem('STORAGE_CAPACITY_BLOCKED', 'Official release execution requires whole-project capacity acceptance below 200000000000 bytes', 409)
        if request.get('scenario_version') != 1001 or context.get('scenario', {}).get('payload', {}).get('profile') not in (None, 'smoke'):
            raise Problem('INVALID_INPUT', 'Maintenance execution is limited to the released smoke scenes', 409)
        from phase1.scenarios.registry import verify_binding
        verify_binding(request, context.get('scene_binding'), context)
        scenario, params = context['scenario'], context['parameters']['data']
        payload = scenario['payload']
        def message(kind, data):
            return dict(copy.deepcopy(request), kind=kind, payload=data, artifacts=[], status='queued', error=None)
        results = []
        events = []
        if request['payload']['mode'] != 'no_pump':
            traffic_payload = copy.deepcopy(params['traffic'])
            if request['payload']['mode'] == 'normal':
                traffic_payload['closures'] = []
            traffic_req = message('traffic_request', traffic_payload)
            traffic_context=dict(network_path=str(ROOT / params['network_path']),time_axis=payload['time_axis'])
            planning=(context.get('scene_binding') or {}).get('planning',{})
            if planning.get('dynamic_config') is not None:traffic_context['dynamic_config']=copy.deepcopy(planning['dynamic_config'])
            traffic = self.stage(ident, 'traffic', traffic_req, traffic_context)
            results.append(('traffic', traffic))
            resource_payload = copy.deepcopy(params['resources'])
            resource_payload.update(traffic_run_id=ident, routes=traffic['payload']['routes'])
            resource_req = message('resource_request', resource_payload)
            # Preserve the immutable scenario payload/snapshot; execution fields
            # belong to this run's parameter set and random seed.
            execution_scenario = copy.deepcopy(scenario)
            for key in ('run_id', 'parameter_version', 'seed'):
                execution_scenario[key] = request[key]
            control = context['preflight']['control']
            resource_context = dict(scenario=execution_scenario, traffic_result=traffic,
                traffic_time_axis=copy.deepcopy(payload['time_axis']),
                control_window_model_day=[control['start_day'], control['end_day']],
                traffic_artifact_root=str(self.store.home / 'runs' / ident / 'staging' / 'traffic' / ident))
            resource = self.stage(ident, 'resources', resource_req, resource_context)
            results.append(('resources', resource))
            r = resource['payload']
            if (not r['feasible'] and (r['tasks'] or not r['violations'])) or (r['feasible'] and r['violations']):
                raise Problem('INVALID_INPUT', 'Inconsistent resource feasibility')
            if not r['feasible']:
                raise Problem('UNREACHABLE', 'Resource infeasible: ' + '; '.join(r['violations']))
            wells = {w['well_id']: w for w in payload['wells']}
            tasks = {t['task_id']: t for t in resource_payload['tasks']}
            routes = {r['task_id']: r for r in traffic['payload']['routes']}
            seen = set()
            for t in r['tasks']:
                if t['task_id'] in seen or t['task_id'] not in tasks or t['well_id'] != tasks[t['task_id']]['well_id']:
                    raise Problem('INVALID_INPUT', 'Resource task reference mismatch')
                seen.add(t['task_id'])
                route = routes[t['task_id']]
                if not route['reachable'] or route['vehicle_id'] != t['vehicle_id'] or route['arrival_time_s'] != t['vehicle_arrival_s']:
                    raise Problem('INVALID_INPUT', 'Resource route reference mismatch')
                if not (t['vehicle_arrival_s'] <= t['unload_complete_s'] <= t['install_complete_s'] <= t['pump_start_s'] < t['pump_stop_s']):
                    raise Problem('INVALID_INPUT', 'Invalid resource time chain')
                events.append(dict(task_id=t['task_id'], well_id=t['well_id'], cell_id=wells[t['well_id']]['cell_id'],
                                   start_s=t['pump_start_s'], stop_s=t['pump_stop_s'], extraction_rate_m3_day=t['extraction_rate_m3_day']))
        hydro_req = message('hydro_request', dict(time_axis=payload['time_axis'], engine_copy='root_standalone',
            config_path=payload['hydro_config_path'], profile=payload['hydro_profile'], pump_events=events,
            risk_window_start_day=params['risk_window_start_day'], risk_window_stop_day=params['risk_window_stop_day']))
        hydro_req['model_build'] = context['preflight']['model_build']
        hydro = self.stage(ident, 'hydro', hydro_req, dict(timeout_s=self.timeout))
        if hydro['payload']['model_signature'] != context['preflight']['model_signature']:
            raise Problem('ARTIFACT_INVALID', 'Hydro model signature mismatch')
        results.append(('hydro', hydro))
        artifacts = []
        for name, result in results:
            # ID and path prefixes prevent collisions; source result remains byte-exact.
            published = copy.deepcopy(result)
            mapped = {}
            for a in result['artifacts']:
                item = dict(a, artifact_id=name + '-' + a['artifact_id'], path=f'staging/{name}/{ident}/' + a['path'])
                mapped[a['artifact_id']] = item['artifact_id']
                artifacts.append(item)
            published['artifacts'] = artifacts[-len(result['artifacts']):] if result['artifacts'] else []
            if name == 'hydro':
                published['payload']['concentration_artifact_id'] = mapped[published['payload']['concentration_artifact_id']]
            if name == 'traffic':
                for route in published['payload']['routes']:
                    if route['trajectory_artifact_id']:
                        route['trajectory_artifact_id'] = mapped[route['trajectory_artifact_id']]
            validate(published)
            path = self.store.home / 'runs' / ident / f'published/{name}/result.json'
            write(path, published)
            artifacts.append(dict(artifact_id=name + '-result', path=f'published/{name}/result.json', sha256=sha(path),
                                  media_type='application/json', role='summary', units='see units'))
        if context.get('scene_binding'):
            bound = context['scene_binding']
            publications=[('scene-manifest', bound['manifest']), ('scene-display', bound['display']), ('scene-binding', bound)]
            if bound.get('planning'):publications.extend([('scene-planning',bound['planning']),('scene-context',bound['planning']['context'])])
            if bound.get('planning',{}).get('dynamic_config') is not None:publications.append(('scene-dynamic-config',bound['planning']['dynamic_config']))
            for artifact_id, value in publications:
                path = self.store.home / 'runs' / ident / 'published' / (artifact_id+'.json')
                # manifest/display bytes remain identical to the published source snapshot.
                if artifact_id == 'scene-manifest':
                    from phase1.scenarios.registry import catalog, read_asset
                    ref=next(r['manifest'] for r in catalog()['scenes'] if r['scenario_id']==request['scenario_id'] and r['scenario_version']==request['scenario_version'])
                    raw=read_asset(ref)
                elif artifact_id == 'scene-display':
                    from phase1.scenarios.registry import read_asset
                    raw=read_asset(bound['manifest']['assets']['display'])
                elif artifact_id == 'scene-context':
                    from phase1.scenarios.planning import context_for
                    from phase1.scenarios.registry import read_asset
                    raw=read_asset(context_for(request['scenario_id'],request['scenario_version'])['context_asset'])
                elif artifact_id == 'scene-dynamic-config':
                    from phase1.scenarios.registry import read_asset
                    raw=read_asset(bound['planning']['dynamic_config_ref'])
                else: raw=dump(value).encode('utf-8')
                path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
                artifacts.append(dict(artifact_id=artifact_id,path=path.relative_to(self.store.home/'runs'/ident).as_posix(),sha256=sha(path),role='config',media_type='application/json',units='see units'))
        return artifacts

    def once(self):
        self.recover()
        ident = self.store.claim(self.pid, self.birth)
        if ident is None:
            return False
        row = self.store.job(ident)
        base = self.store.home / 'runs' / ident
        base.mkdir(parents=True, exist_ok=True)
        try:
            self.log(ident, 'queued -> running')
            self.tick(ident)
            artifacts = self.pipeline(ident, json.loads(row['request']), json.loads(row['context']))
            from phase1.scenarios.registry import verify_binding
            verify_binding(json.loads(row['request']),json.loads(row['context']).get('scene_binding'),json.loads(row['context']))
            self.tick(ident)
            for a in artifacts:
                if sha(base / a['path']) != a['sha256']:
                    raise Problem('ARTIFACT_INVALID', 'Final publication hash mismatch')
            self.log(ident, 'Output validation completed')
            artifacts += self.diagnostics(ident)
            write(base / 'artifact_index.json', artifacts)
            self.store.transition(ident, 'succeeded', artifacts=artifacts, progress=1)
        except Exception as exc:
            error = exc.error if isinstance(exc, Problem) else dict(code='ENGINE_FAILED', message=str(exc), retryable=False)
            state = 'cancelled' if error['code'] == 'CANCELLED' else 'failed'
            write(base / 'error.json', dict(error=error, traceback=traceback.format_exc()))
            self.log(ident, error['message'], 'ERROR')
            # Engine termination/confirmation has completed in execute.finally.
            self.store.transition(ident, state, error, artifacts=self.diagnostics(ident))
            with self.store.connect() as db:
                from .core import status
                pending = list(db.execute("SELECT stage,body FROM stages WHERE run_id=? AND state='running'", (ident,)))
                for stage in pending:
                    body = status(json.loads(stage['body']), state, 'running', error=error)
                    db.execute('UPDATE stages SET state=?,body=? WHERE run_id=? AND stage=?', (state, dump(body), ident, stage['stage']))
        return True


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--home', required=True)
    p.add_argument('--science-python', default=SCIENCE)
    p.add_argument('--traffic-python')
    p.add_argument('--timeout', type=float, default=300)
    p.add_argument('--once', action='store_true')
    a = p.parse_args()
    worker = Worker(Store(a.home), a.science_python, a.traffic_python, a.timeout)
    while True:
        worked = worker.once()
        if a.once:
            return
        if not worked:
            time.sleep(.25)


if __name__ == '__main__':
    main()
