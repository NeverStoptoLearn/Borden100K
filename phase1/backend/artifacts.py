import json
import numpy as np
from .core import safe_path, sha, Problem, validate, IDENTITY


def check_result(result, request, root):
    validate(result)
    if any(result[k] != request[k] for k in IDENTITY) or result['data_provenance']['fixture']:
        raise Problem('ARTIFACT_INVALID', 'Result identity mismatch or fixture output')
    if result['status'] != 'succeeded':
        exc = Problem(**{k: result['error'][k] for k in ('code', 'message')})
        exc.error = result['error']
        raise exc
    ids = set()
    for a in result['artifacts']:
        if a['artifact_id'] in ids:
            raise Problem('ARTIFACT_INVALID', 'Duplicate artifact ID')
        ids.add(a['artifact_id'])
        path = safe_path(root, a['path'])
        if not path.is_file() or sha(path) != a['sha256']:
            raise Problem('ARTIFACT_INVALID', 'Artifact bytes/hash mismatch')
        if a['role'] == 'concentration':
            with np.load(path, allow_pickle=False) as data:
                t, c, xyz, cells = (data[k] for k in ('times_day', 'C', 'cell_xyz_m', 'cell_ids'))
                if (t.dtype != np.float64 or c.dtype != np.float64 or xyz.dtype != np.float64 or
                    t.ndim != 1 or len(t) < 2 or cells.ndim != 1 or cells.dtype.kind not in 'iu' or
                    c.shape != (len(t), len(cells)) or xyz.shape != (len(cells), 3) or
                    not np.array_equal(cells, np.arange(len(cells))) or not c.flags.c_contiguous or
                    not all(np.isfinite(x).all() for x in (t, c, xyz)) or c.min() < -1e-7 or
                    not np.all(np.diff(t) > 0) or t.tolist() != result['payload']['times_day']):
                    raise Problem('ARTIFACT_INVALID', 'Invalid concentration arrays')
        if a['role'] == 'trajectory':
            samples = json.loads(path.read_text('utf-8'))
            previous = {}
            for sample in samples:
                validate(sample, 'trajectory_sample')
                key = (sample['vehicle_id'], sample['route_id'])
                if sample['event_time_s'] <= previous.get(key, -1):
                    raise Problem('ARTIFACT_INVALID', 'Nonmonotonic trajectory')
                previous[key] = sample['event_time_s']
    if result['kind'] == 'hydro_result':
        if any(result['model_build'][k] != request['model_build'][k] for k in ('code_sha256','config_sha256')):
            raise Problem('ARTIFACT_INVALID', 'Scientific build hash mismatch')
        ref = result['payload']['concentration_artifact_id']
        if not any(a['artifact_id'] == ref and a['role'] == 'concentration' for a in result['artifacts']):
            raise Problem('ARTIFACT_INVALID', 'Missing concentration reference')
        for k in ('risk_window_start_day', 'risk_window_stop_day', 'profile'):
            if result['payload'][k] != request['payload'][k]:
                raise Problem('ARTIFACT_INVALID', 'Risk window/profile changed')
    if result['kind'] == 'traffic_result':
        for route in result['payload']['routes']:
            if route['reachable']:
                if route['arrival_time_s'] is None or route['arrival_time_s'] < route['depart_s']:
                    raise Problem('ARTIFACT_INVALID', 'Invalid reachable arrival')
                if route['trajectory_artifact_id'] not in ids:
                    raise Problem('ARTIFACT_INVALID', 'Missing trajectory reference')
            elif route['arrival_time_s'] is not None or route['edge_ids'] or route['trajectory_artifact_id'] is not None or not route['reason']:
                raise Problem('ARTIFACT_INVALID', 'Inconsistent unreachable route')
    return result
