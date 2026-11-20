"""Portable, bounded adapters; frozen physics and algorithms are unchanged."""

import copy
import hashlib
import json
import shutil
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_RUN_BYTES = 64 * 1024**2
SCENES = ('borden_reference_100k', 'synthetic_multiterrain_100k')
TASKS = {
    'source_identification': dict(
        status='Supported evaluator',
        baseline='fixed prior midpoint',
        objective='existing smooth_objective: half mean squared residual / 500^2',
        constraints='configured x/y and concentration bounds',
    ),
    'resource_scheduling': dict(
        status='Supported decoder',
        baseline='unchanged priority/skill/equipment/inventory scheduler',
        objective='schedule feasibility and resource allocation',
        constraints='original crew/vehicle/equipment/stock/time constraints',
    ),
    'vehicle_routing': dict(
        status='Supported evaluator',
        baseline='unchanged SUMO routing and closure adapter',
        objective='actual arrival/travel time',
        constraints='network permissions, connectivity, closures, time horizon',
    ),
    'groundwater_forward': dict(
        status='Supported smoke evaluator',
        baseline='unchanged SciPy Darcy + 3D FV no-pump model',
        objective='original transport/mass/flow metrics',
        constraints='original physical model and contract',
    ),
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024**2), b''):
            h.update(block)
    return h.hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def write(path, value):
    with Path(path).open('x', encoding='utf8') as f:
        json.dump(value, f, indent=2, ensure_ascii=False, allow_nan=False)


def scene(ident):
    if ident not in SCENES:
        raise ValueError('Unknown scene')
    folder = ROOT / 'phase1/scenarios' / ident / 'v1001'
    m = read(folder / 'manifest.json')
    for ref in [*m['assets'].values(), m['science']['code'], m['science']['config']]:
        if ref and sha(ROOT / ref['path']) != ref['sha256']:
            raise ValueError('Scene integrity mismatch: ' + ref['path'])
    return m, folder


def engine(ident):
    m, folder = scene(ident)
    from phase1.hydro_adapter.worker import capability, load_engine

    build = capability('smoke')
    module = load_engine(build)
    # Dict input explicitly bypasses the legacy per-machine environment override.
    return module.BordenScene(
        profile='smoke', config=read(ROOT / m['science']['config']['path'])
    ), build


def prepare(ident, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    model, build = engine(ident)
    from borden3d_common import prepare_dataset

    prepare_dataset(model, out)
    write(
        out / 'task-dataset.json',
        dict(
            task_release='1.0.0rc1',
            scenario_id=ident,
            scenario_version=1001,
            scientific_config_sha256=build['config_sha256'],
            model_signature=model.signature,
            observations_sha256=sha(out / 'observations.npz'),
            classification='synthetic',
            calibrated=False,
            truth_use='reference_evaluation_only.npz is for generation/scoring, never baseline selection',
        ),
    )
    return out


def evaluate_source(ident, observations, candidate):
    import numpy as np

    model, build = engine(ident)
    bounds = model.cfg['inverse']
    x = np.asarray(candidate, dtype=float)
    lo = np.array(
        [
            bounds['center_bounds_m'][0][0],
            bounds['center_bounds_m'][1][0],
            bounds['concentration_bounds_mg_L'][0],
        ]
    )
    hi = np.array(
        [
            bounds['center_bounds_m'][0][1],
            bounds['center_bounds_m'][1][1],
            bounds['concentration_bounds_mg_L'][1],
        ]
    )
    if x.shape != (3,) or not np.all(np.isfinite(x)) or np.any(x < lo) or np.any(x > hi):
        raise ValueError('Candidate violates original inverse bounds')
    observations = Path(observations)
    if observations.stat().st_size > 1024**2:
        raise ValueError('Release smoke observation limit is 1 MiB')
    descriptor = read(observations.parent / 'task-dataset.json')
    if (
        descriptor['scenario_id'] != ident
        or descriptor['scenario_version'] != 1001
        or descriptor['model_signature'] != model.signature
        or descriptor['observations_sha256'] != sha(observations)
    ):
        raise ValueError('Observation source/scene identity mismatch')
    with np.load(observations, allow_pickle=False) as z:
        obs = {k: z[k].copy() for k in ['time_indices', 'cell_indices', 'values']}
    if not (0 < len(obs['values']) <= 4096) or any(
        a.shape != obs['values'].shape for a in obs.values()
    ):
        raise ValueError('Invalid observation vectors')
    if any(not np.all(np.isfinite(a)) for a in obs.values()):
        raise ValueError('Nonfinite observation')
    if any(a.dtype.kind not in 'iu' for a in [obs['time_indices'], obs['cell_indices']]):
        raise ValueError('Integer observation indices required')
    if (
        np.any(obs['time_indices'] < 0)
        or np.any(obs['time_indices'] >= len(model.times()))
        or np.any(obs['cell_indices'] < 0)
        or np.any(obs['cell_indices'] >= model.n)
    ):
        raise ValueError('Observation index outside certified grid')
    value, gradient, prediction, _ = model.smooth_objective(x, obs, gradient=True)
    return dict(
        status='succeeded',
        objective=float(value),
        gradient=gradient.tolist(),
        prediction=prediction.tolist(),
        candidate=x.tolist(),
        observations_sha256=sha(observations),
        model_build=build,
        model_signature=model.signature,
        inverse_model='frozen smooth source penalty approximation',
        field_calibrated=False,
        fixture=False,
    )


def run(
    task,
    ident,
    out,
    *,
    seed=42,
    observations=None,
    candidate=None,
    mode='maintenance_smoke',
    traffic_variant='normal',
    traffic_result=None,
    traffic_root=None,
):
    if task not in TASKS or TASKS[task]['status'] == 'Experimental':
        raise ValueError('Task is not a supported release evaluator')
    if mode != 'maintenance_smoke':
        raise ValueError(
            'Official campaign admission BLOCKED: complete fresh ownership inventory below 200000000000 bytes and capacity acceptance required'
        )
    out = Path(out).resolve()
    if out.exists():
        raise FileExistsError('Create-only output required')
    ancestor = out.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free < 1024**3:
        raise RuntimeError('Insufficient physical headroom for bounded smoke')
    m, folder = scene(ident)
    out.mkdir(parents=True, exist_ok=False)
    if task == 'source_identification' and candidate is None:
        cfg = read(ROOT / m['science']['config']['path'])
        b = cfg['inverse']
        candidate = [sum(v) / 2 for v in [*b['center_bounds_m'], b['concentration_bounds_mg_L']]]
    upstream = None
    if traffic_result:
        doc = read(traffic_result)
        upstream = doc.get('engine_result', doc)
        if (
            upstream.get('kind') != 'traffic_result'
            or upstream.get('scenario_id') != ident
            or upstream.get('scenario_version') != 1001
            or upstream.get('seed') != seed
        ):
            raise ValueError('Traffic source identity differs from task request')
    run_id = upstream['run_id'] if upstream else str(uuid.uuid4())
    identity = dict(
        task_id=task,
        run_id=run_id,
        scenario_id=ident,
        scenario_version=1001,
        seed=seed,
        scenario_generation_seed=m['seed'],
        mode=mode,
        task_release='1.0.0rc1',
        scientific_config_sha256=m['science']['config']['sha256'],
    )
    write(
        out / 'input.json',
        dict(
            **identity,
            candidate=candidate,
            observation_sha256=sha(observations) if observations else None,
            traffic_result_sha256=sha(traffic_result) if traffic_result else None,
        ),
    )
    try:
        if task == 'source_identification':
            if observations is None:
                raise ValueError('Explicit observations required; run prepare first')
            result = evaluate_source(ident, observations, candidate)
        elif task == 'groundwater_forward':
            from phase1.hydro_adapter.adapter import run as hydro
            from phase1.hydro_adapter.examples import example_request

            req = example_request('no_pump')
            req.update(run_id=run_id, scenario_id=ident, scenario_version=1001, seed=seed)
            result = hydro(req, out / 'engine')
        elif task == 'vehicle_routing':
            if ident != 'synthetic_multiterrain_100k':
                raise ValueError('Borden target road acceptance is not available')
            from phase1.traffic.engine import digest, run as traffic

            if traffic_variant not in ['normal', 'closure']:
                raise ValueError('Unknown traffic variant')
            params = read(folder / 'parameters.template.json')[traffic_variant]
            req = read(ROOT / 'schemas/examples/traffic_request.json')
            req.update(run_id=run_id, scenario_id=ident, scenario_version=1001, seed=seed)
            req['payload'] = copy.deepcopy(params['traffic'])
            if traffic_variant == 'normal':
                req['payload']['closures'] = []
            req['data_provenance'] = dict(
                classification='synthetic',
                calibration='none',
                fixture=False,
                sources=copy.deepcopy(m['sources']),
            )
            req['model_build'].update(
                code_sha256=digest(__file__), config_sha256=m['assets']['network']['sha256']
            )
            axis = read(folder / 'scenario.template.json')['payload']['time_axis']
            result = traffic(
                req,
                ROOT / m['assets']['network']['path'],
                out / 'engine',
                time_axis=axis,
                max_time_s=14400,
            )
        else:
            from phase1.resources import schedule

            if upstream:
                req = copy.deepcopy(upstream)
                req.update(kind='resource_request', status='queued', artifacts=[], error=None)
                params = read(folder / 'parameters.template.json')['normal']
                req['payload'] = dict(
                    traffic_run_id=run_id,
                    routes=copy.deepcopy(upstream['payload']['routes']),
                    **params['resources'],
                )
                scenario = read(folder / 'scenario.template.json')
                for key in [
                    'experiment_id',
                    'run_id',
                    'scenario_id',
                    'scenario_version',
                    'parameter_version',
                    'seed',
                    'units',
                ]:
                    scenario[key] = copy.deepcopy(upstream[key])
                root = (
                    Path(traffic_root)
                    if traffic_root
                    else Path(traffic_result).parent / 'engine' / run_id
                )
                axis = read(root / 'context.json')['time_axis']
                if axis != scenario['payload']['time_axis']:
                    raise ValueError('Traffic source clock differs from scene')
                cfg = read(ROOT / m['science']['config']['path'])
                context = dict(
                    scenario=scenario,
                    traffic_result=upstream,
                    traffic_time_axis=axis,
                    control_window_model_day=[
                        cfg['control']['start_day'],
                        cfg['control']['end_day'],
                    ],
                    traffic_artifact_root=str(root.resolve()),
                )
            else:
                req = read(ROOT / 'phase1/resources/examples/request.fixture.json')
                context = read(ROOT / 'phase1/resources/examples/context.fixture.json')
                for env in [req, context['scenario'], context['traffic_result']]:
                    env.update(run_id=run_id, scenario_id=ident, scenario_version=1001, seed=seed)
                req['payload']['traffic_run_id'] = run_id
            result = schedule(req, scenario_context=context)
            write(out / ('source-context.json' if upstream else 'fixture-context.json'), context)
        if result.get('status') != 'succeeded':
            raise RuntimeError('Engine did not succeed: ' + str(result.get('error')))
        if 'kind' in result:
            from phase1.hydro_adapter.adapter import validate

            validate(result)  # the original frozen envelope is not extended
        result = dict(
            **identity, status='succeeded', result_schema='task-result-v1', engine_result=result
        )
        import jsonschema

        jsonschema.validate(result, read(ROOT / 'schemas/task-release-v1.schema.json'))
        write(out / 'result.json', result)
        artifacts = []
        total = 0
        for p in sorted(out.rglob('*')):
            if p.is_file():
                total += p.stat().st_size
                artifacts.append(
                    dict(path=p.relative_to(out).as_posix(), sha256=sha(p), bytes=p.stat().st_size)
                )
        if total > MAX_RUN_BYTES:
            raise RuntimeError('Output bound exceeded; retain outputs and stop')
        write(
            out / 'artifact-index.json',
            dict(**identity, status='succeeded', bytes=total, artifacts=artifacts),
        )
        return result
    except Exception as e:
        write(
            out / 'error.json',
            dict(**identity, status='failed', error=type(e).__name__ + ': ' + str(e)),
        )
        raise
