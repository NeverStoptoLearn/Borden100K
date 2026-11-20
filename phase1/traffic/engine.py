"""SUMO subprocess + TraCI adapter. No estimated arrival is returned as simulation."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
import traceback
import uuid
import xml.etree.ElementTree as ET

from jsonschema import Draft202012Validator, FormatChecker

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCHEMA = json.loads((ROOT / 'schemas/phase1-v1.schema.json').read_text('utf-8'))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')
    temp.replace(path)


def finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('Nonfinite number')
    if isinstance(value, dict):
        for v in value.values():
            finite(v)
    elif isinstance(value, list):
        for v in value:
            finite(v)


def binary(name):
    override = os.environ.get(name.upper() + '_BINARY')
    candidates = [override, shutil.which(name),
                  str(HERE / '.deps/sumo/bin' / (name + ('.exe' if os.name == 'nt' else '')))]
    if os.environ.get('SUMO_HOME'):
        candidates.append(str(Path(os.environ['SUMO_HOME']) / 'bin' / (name + ('.exe' if os.name == 'nt' else ''))))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise RuntimeError(f'{name} unavailable; install phase1/traffic/requirements.txt')


def command(args, log, timeout=120):
    try:
        result = subprocess.run([str(a) for a in args], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        Path(log).write_text(f'Launch failed: {exc}\n',encoding='utf-8')
        raise
    Path(log).write_text('$ ' + subprocess.list2cmdline([str(a) for a in args]) + '\n' +
                         result.stdout + result.stderr + f'\nexit_code={result.returncode}\n',
                         encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'Engine exit {result.returncode}: {log}')
    return result.stdout


def network_metadata(path, network_id, source_id):
    loc = ET.parse(path).getroot().find('location')
    if loc is None:
        raise ValueError('Network requires location metadata')
    return dict(network_id=network_id, source_id=source_id, network_sha256=digest(path),
                proj_parameter=loc.attrib['projParameter'],
                net_offset_m=[float(x) for x in loc.attrib['netOffset'].split(',')])


def convert_position(network, x, y, *, to_wgs84=True):
    """Explicit SUMO offset convention; never georeference an unprojected grid."""
    from pyproj import CRS, Transformer
    if network['proj_parameter'] in ('!', '-', '.'):
        raise ValueError('GEOREFERENCE_UNAVAILABLE: unprojected network')
    projection = CRS.from_user_input(network['proj_parameter'])
    offset_x, offset_y = network['net_offset_m']
    if to_wgs84:
        return Transformer.from_crs(projection, 'EPSG:4326', always_xy=True).transform(x-offset_x, y-offset_y)
    east, north = Transformer.from_crs('EPSG:4326', projection, always_xy=True).transform(x,y)
    return east+offset_x, north+offset_y


def import_osm(osm, output, source):
    """Import independently acquired OSM; canonical IDs preserve v1 ID grammar."""
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = copy.deepcopy(source)
    for k in ('source_id', 'reference', 'license', 'synthetic', 'acquired_at_utc'):
        if k not in source:
            raise ValueError(f'Missing provenance {k}')
    if 'openstreetmap' not in source['reference'].lower() and not source['synthetic']:
        raise ValueError('OSM source reference required')
    shutil.copyfile(osm, output / 'source.osm.xml')
    args = [binary('netconvert'), '--osm-files', output / 'source.osm.xml',
            '--output-file', output / 'network.net.xml', '--no-turnarounds', 'true',
            '--tls.guess', 'false', '--seed', '42']
    command(args, output / 'netconvert.log')
    tree = ET.parse(output / 'network.net.xml')
    root = tree.getroot()
    names = {}
    edge_map = {}
    for edge in root.findall('edge'):
        if edge.get('function') == 'internal':
            continue
        old = edge.attrib['id']
        new = 'osm_' + hashlib.sha256(old.encode()).hexdigest()[:20]
        edge_map[new] = old
        names[old] = new
        for lane in edge.findall('lane'):
            names[lane.attrib['id']] = new + '_' + lane.attrib['index']
    # Rewrite exact IDs and whitespace-separated lane references only.
    for element in root.iter():
        for key, value in list(element.attrib.items()):
            if key in ('id', 'from', 'to', 'via', 'incLanes', 'intLanes', 'lane'):
                element.set(key, ' '.join(names.get(v, v) for v in value.split()))
    tree.write(output / 'network.net.xml', encoding='utf-8', xml_declaration=True)
    write_json(output / 'edge_map.json', edge_map)
    source.update(osm_sha256=digest(output / 'source.osm.xml'),
                  network_sha256=digest(output / 'network.net.xml'),
                  netconvert_version=command([binary('netconvert'), '--version'], output / 'version.log').splitlines()[0],
                  assumptions='SUMO default speeds/lanes for missing OSM tags; no measured demand; no guessed TLS')
    write_json(output / 'source.json', source)
    return network_metadata(output / 'network.net.xml', 'osm-small-network', source['source_id'])


def validate_request(request, net):
    finite(request)
    VALIDATOR.validate(request)
    if request['kind'] != 'traffic_request' or request['status'] != 'queued':
        raise ValueError('Expected queued traffic_request')
    if request['data_provenance']['fixture']:
        raise ValueError('Contract fixtures cannot run as production results')
    provenance = request['data_provenance']
    synthetic = [s['synthetic'] for s in provenance['sources']]
    expected = 'synthetic' if all(synthetic) else 'mixed' if any(synthetic) else 'observed'
    if provenance['classification'] != expected:
        raise ValueError('Inconsistent source provenance classification')
    payload = request['payload']
    metadata = payload['network']
    if metadata != network_metadata(net, metadata['network_id'], metadata['source_id']):
        raise ValueError('Network SHA256/projection/netOffset mismatch')
    if metadata['source_id'] not in {s['source_id'] for s in request['data_provenance']['sources']}:
        raise ValueError('Missing network provenance source')
    edges = {e.attrib['id'] for e in ET.parse(net).getroot().findall('edge') if e.get('function') != 'internal'}
    for field in ('vehicle_id', 'task_id'):
        ids = [d[field] for d in payload['dispatches']]
        if len(ids) != len(set(ids)):
            raise ValueError(f'Duplicate {field}; use one dispatch per vehicle per run')
    for dispatch in payload['dispatches']:
        if dispatch['from_edge'] not in edges or dispatch['to_edge'] not in edges:
            raise ValueError('Unknown dispatch edge')
    for closure in payload['closures']:
        if closure['start_s'] >= closure['stop_s'] or closure['edge_id'] not in edges:
            raise ValueError('Invalid closure interval or edge')


def run(request, network_path, output_root, *, time_axis, max_time_s=3600, wall_timeout_s=120, dynamic_config=None):
    """Run in an exclusive UUID directory. Raises on failure; writes failed run_status.

    time_axis is explicit integration context (not an extra frozen payload field).
    Closure/departure events must align to the 1-second SUMO step.
    """
    import traci
    network_path = Path(network_path).resolve()
    validate_request(request, network_path)
    finite(time_axis)
    Draft202012Validator({'$ref': '#/$defs/time_axis', '$defs': SCHEMA['$defs']},
                        format_checker=FormatChecker()).validate(time_axis)
    if not math.isfinite(max_time_s) or max_time_s <= 0 or not math.isfinite(wall_timeout_s) or wall_timeout_s <= 0:
        raise ValueError('Invalid run limits')
    if time_axis['reference_model_day'] + max_time_s / 86400 > time_axis['model_end_day']:
        raise ValueError('Simulation exceeds model horizon')
    payload = request['payload']
    times = [d['depart_s'] for d in payload['dispatches']]
    times += [c[k] for c in payload['closures'] for k in ('start_s', 'stop_s')]
    if any(t != int(t) or t >= max_time_s for t in times):
        raise ValueError('Events must be integer seconds strictly below max_time_s')
    dynamic_input=None
    if dynamic_config is not None:
        from .dynamic import validate_config
        dynamic_input=validate_config(dynamic_config,request,{e.get('id') for e in ET.parse(network_path).getroot().findall('edge') if e.get('function')!='internal'},max_time_s)
    out = Path(output_root).resolve() / request['run_id']
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(network_path, out / 'network.net.xml')
    sidecars = []
    for name in ('source.json','edge_map.json','source.osm.xml'):
        source_path = network_path.parent / name
        if source_path.is_file():
            shutil.copyfile(source_path, out / name)
            sidecars.append(name)
    write_json(out / 'request.json', request)
    write_json(out / 'context.json', dict(time_axis=time_axis, max_time_s=max_time_s,
                                         wall_timeout_s=wall_timeout_s, step_length_s=1))
    log_path = out / 'events.jsonl'
    def log(t, message, level='INFO'):
        with log_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(dict(run_id=request['run_id'], scenario_version=request['scenario_version'],
                                    event_time_s=t, level=level, module='traffic', message=message)) + '\n')
    def artifact(path, role, units):
        path = Path(path)
        resolved = (out / path).resolve()
        resolved.relative_to(out)
        return dict(artifact_id=path.stem.replace('.', '-'), path=path.as_posix(),
                    sha256=digest(resolved), media_type='application/json' if path.suffix == '.json' else 'text/plain',
                    role=role, units=units)
    result = copy.deepcopy(request)
    result['model_build'] = dict(engine_id='sumo', engine_version='NOT_STARTED',
                                code_sha256=digest(__file__), config_sha256=digest(out / 'context.json'))
    # Include payload and network digest in a reproducible config signature.
    result['model_build']['config_sha256'] = hashlib.sha256(
        (out.joinpath('context.json').read_bytes() + json.dumps(payload, sort_keys=True).encode())).hexdigest()
    conn = None
    t = 0
    stdout = (out / 'sumo.log').open('w', encoding='utf-8')
    try:
        log(0, 'queued -> running')
        version = command([binary('sumo'), '--version'], out / 'version.log').splitlines()[0]
        result['model_build']['engine_version'] = version
        label = str(uuid.uuid4())
        args = [binary('sumo'), '-n', str(out / 'network.net.xml'), '--seed', str(request['seed']),
                '--step-length', '1', '--no-step-log', 'true', '--time-to-teleport', '-1',
                '--tripinfo-output', str(out / 'tripinfo.xml'), '--tripinfo-output.write-unfinished', 'true']
        write_json(out / 'command.json', args)
        traci.start(args, label=label, stdout=stdout, numRetries=10, doSwitch=False)
        conn = traci.getConnection(label)
        conn._socket.settimeout(min(wall_timeout_s, 30))
        dynamic=None
        if dynamic_input is not None:
            from .dynamic import DynamicTraffic
            dynamic=DynamicTraffic(dynamic_input,request,time_axis,conn,{e.get('id') for e in ET.parse(network_path).getroot().findall('edge') if e.get('function')!='internal'})
            write_json(out/'dynamic-config.json',dynamic_input)
            result['model_build']['config_sha256']=hashlib.sha256((out/'context.json').read_bytes()+json.dumps(payload,sort_keys=True).encode()+json.dumps(dynamic_input,sort_keys=True).encode()).hexdigest()
        original = {lane: conn.lane.getAllowed(lane) for lane in conn.lane.getIDList()}
        active_closed = set()
        routes, samples, pending, running, visited = {}, {}, {}, set(), {}
        # Observations only: visited edges in traffic_result remain unchanged.
        route_plans = dict(format='route-plans-v1',
            **{key: request[key] for key in ('scenario_id', 'scenario_version', 'parameter_version', 'run_id', 'seed')},
            network_id=payload['network']['network_id'], network_sha256=payload['network']['network_sha256'], events=[])
        started = time.monotonic()
        for i, dispatch in enumerate(payload['dispatches']):
            rid = f'route-{i}'
            routes[dispatch['vehicle_id']] = dict(task_id=dispatch['task_id'], vehicle_id=dispatch['vehicle_id'],
                route_id=rid, reachable=False, depart_s=dispatch['depart_s'], arrival_time_s=None,
                edge_ids=[], trajectory_artifact_id=None, reason='UNREACHABLE')
            samples[dispatch['vehicle_id']] = []
            pending[dispatch['vehicle_id']] = dispatch
            visited[dispatch['vehicle_id']] = []
        def plan(a, b):
            if a in active_closed or b in active_closed:
                return []
            try:
                stage = conn.simulation.findRoute(a, b, depart=t) if dynamic else conn.simulation.findRoute(a, b)
            except traci.exceptions.TraCIException as exc:
                if 'No connection' in str(exc):
                    return []
                raise
            if dynamic:dynamic.focus.update(stage.edges)
            return list(stage.edges)
        def reroute(vid):
            edge = conn.vehicle.getRoadID(vid)
            if edge.startswith(':') or edge in active_closed:
                return  # cannot undo entry; leave current edge before rerouting
            target = conn.vehicle.getRoute(vid)[-1]
            path = plan(edge, target)
            if not path:
                conn.vehicle.remove(vid)
                running.remove(vid)
                routes[vid]['reason'] = 'UNREACHABLE after closure'
                log(t, f'{vid}: UNREACHABLE after closure')
            else:
                old=conn.vehicle.getRoute(vid)[conn.vehicle.getRouteIndex(vid):]
                conn.vehicle.setRoute(vid, path)
                if dynamic:dynamic.record(vid,routes[vid]['route_id'],t,old,path,'road_closure')
                route_plans['events'].append(dict(vehicle_id=vid, route_id=routes[vid]['route_id'],
                    event_time_s=t, kind='replanned', edge_ids=list(path), reason='closure_reroute'))
        while pending or running or (dynamic and dynamic.busy()):
            if t >= max_time_s or time.monotonic() - started > wall_timeout_s:
                raise TimeoutError('Traffic simulation limit reached before completion')
            closed = {c['edge_id'] for c in payload['closures'] if c['start_s'] <= t < c['stop_s']}
            changed = closed != active_closed
            if changed:
                if dynamic:dynamic.apply_closure(closed,t)
                for edge in closed | active_closed:
                    for lane in original:
                        if conn.lane.getEdgeID(lane) == edge:
                            conn.lane.setAllowed(lane, ['authority'] if edge in closed else original[lane])
                active_closed = closed
                log(t, f'closures={sorted(closed)}')
            # Reroute at closure boundaries, including vehicles already in motion.
            if changed:
                for vid in sorted(running & set(conn.vehicle.getIDList())):
                    edge = conn.vehicle.getRoadID(vid)
                    if edge.startswith(':'):
                        continue  # handled below every step until out of junction
                    reroute(vid)
            for vid, dispatch in list(pending.items()):
                if dispatch['depart_s'] > t:
                    continue
                path = plan(dispatch['from_edge'], dispatch['to_edge'])
                del pending[vid]
                if not path:
                    log(t, f'{vid}: UNREACHABLE at dispatch')
                    continue
                conn.route.add(routes[vid]['route_id'], path)
                conn.vehicle.add(vid, routes[vid]['route_id'], depart=str(t))
                running.add(vid)
                route_plans['events'].append(dict(vehicle_id=vid, route_id=routes[vid]['route_id'],
                    event_time_s=t, kind='planned', edge_ids=list(path), reason='dispatch'))
            for vid in sorted(running & set(conn.vehicle.getIDList())):
                edge = conn.vehicle.getRoadID(vid)
                if active_closed and not edge.startswith(':'):
                    remaining = conn.vehicle.getRoute(vid)[conn.vehicle.getRouteIndex(vid) + 1:]
                    if any(e in active_closed for e in remaining):
                        reroute(vid)
            if dynamic:
                dynamic.inject(t,plan)
                dynamic.replan(t,running,routes,plan,route_plans,active_closed)
            conn.simulationStep()
            t = conn.simulation.getTime()
            if dynamic:dynamic.observe(t)
            if conn.simulation.getStartingTeleportIDList():
                raise RuntimeError('Teleport detected')
            for vid in sorted(running & set(conn.vehicle.getIDList())):
                edge = conn.vehicle.getRoadID(vid)
                if not edge.startswith(':') and (not visited[vid] or visited[vid][-1] != edge):
                    visited[vid].append(edge)
                x, y = conn.vehicle.getPosition(vid)
                wgs = None
                if payload['network']['proj_parameter'] != '!':
                    lon, lat = conn.simulation.convertGeo(x, y)
                    wgs = dict(lon=lon, lat=lat)
                samples[vid].append(dict(vehicle_id=vid, route_id=routes[vid]['route_id'], event_time_s=t,
                    sumo_position_m=dict(x_m=x, y_m=y), wgs84=wgs))
            for vid in conn.simulation.getArrivedIDList():
                if vid not in running:continue
                running.remove(vid)
                routes[vid].update(reachable=True, arrival_time_s=t, edge_ids=visited[vid], reason=None)
                log(t, f'{vid}: arrived')
        conn.close()
        if conn._process.returncode != 0:
            raise RuntimeError(f'SUMO exited {conn._process.returncode}')
        conn = None
        stdout.flush()
        # tripinfo reports engine arrival, independent of the sampling step boundary.
        trips = {v.attrib['id']: v.attrib for v in ET.parse(out / 'tripinfo.xml').getroot().findall('tripinfo')}
        if dynamic:dynamic.finalize(trips)
        artifacts = []
        sample_validator = Draft202012Validator({'$ref': '#/$defs/trajectory_sample', '$defs': SCHEMA['$defs']})
        for vid, route in routes.items():
            if not route['reachable']:
                continue
            trip = trips[vid]
            arrival = float(trip['arrival'])
            if arrival < route['depart_s'] or not samples[vid] or not route['edge_ids']:
                raise RuntimeError('Invalid trip/trajectory output')
            route['arrival_time_s'] = arrival
            for sample in samples[vid]:
                finite(sample)
                sample_validator.validate(sample)
            path = route['route_id'] + '.json'
            write_json(out / path, samples[vid])
            a = artifact(path, 'trajectory', 's,m,degree')
            route['trajectory_artifact_id'] = a['artifact_id']
            artifacts.append(a)
        result.update(kind='traffic_result', status='succeeded', error=None,
                      payload=dict(network_id=payload['network']['network_id'], routes=list(routes.values())))
        log(t, 'running -> succeeded; SUMO exit_code=0; outputs validated')
        write_json(out / 'route-plans.json', route_plans)
        if dynamic:
            for filename,content in dynamic.outputs().items():
                write_json(out/filename,content);artifacts.append(artifact(filename,'summary','s,m,vehicle/km/lane'))
            artifacts.append(artifact('dynamic-config.json','config','synthetic demand'))
        for path, role, units in [('request.json','config','contract-v1'), ('context.json','config','s,day'),
                ('route-plans.json','summary','s,edge_ids'),
                ('network.net.xml','config','m'), ('command.json','config','SUMO CLI'),
                ('tripinfo.xml','summary','s,m'), ('sumo.log','log','text'),
                ('version.log','log','text'), ('events.jsonl','log','s')]:
            artifacts.append(artifact(path, role, units))
        for name in sidecars:
            artifacts.append(artifact(name, 'config', 'OSM provenance'))
        result['artifacts'] = artifacts
        finite(result)
        VALIDATOR.validate(result)
        write_json(out / 'result.json', result)
        return result
    except Exception as exc:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                if conn._process:
                    conn._process.kill()
                    conn._process.wait()
        log(t, str(exc), 'ERROR')
        (out / 'traceback.log').write_text(traceback.format_exc(), encoding='utf-8')
        result.update(kind='run_status', status='failed', artifacts=[],
                      error=dict(code='TIMEOUT' if isinstance(exc, TimeoutError) else 'ENGINE_FAILED',
                                 message=str(exc), retryable=False),
                      payload=dict(previous_status='running', updated_at_utc=__import__('datetime').datetime.now(
                          __import__('datetime').timezone.utc).isoformat().replace('+00:00', 'Z'), progress_fraction=0))
        VALIDATOR.validate(result)
        write_json(out / 'result.json', result)
        raise
    finally:
        stdout.close()
