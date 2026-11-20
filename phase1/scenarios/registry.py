"""Internal dual-scene registry. Frozen v1 messages remain unchanged."""
import copy
import hashlib
import json
import math
import re
from pathlib import Path
from phase1.contracts import ROOT, Problem, safe_path, sha, dump

IDS = {'borden_reference_100k', 'synthetic_multiterrain_100k'}
VERSION = 'dual-scenes-v1'

def strict(value, keys):
    if not isinstance(value, dict) or set(value) != set(keys.split()):
        raise Problem('INVALID_INPUT', 'Invalid internal scene object fields')

def read_asset(ref):
    strict(ref, 'path sha256 media_type')
    if not isinstance(ref['sha256'],str) or not re.fullmatch('[0-9a-f]{64}',ref['sha256']):raise Problem('INVALID_INPUT','Invalid scene SHA')
    path = safe_path(ROOT, ref['path'])
    if not path.is_file():raise Problem('ARTIFACT_INVALID','Missing scene asset',409)
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise Problem('ARTIFACT_INVALID', 'Scene asset SHA mismatch: '+ref['path'], 409)
    return raw

def catalog():
    value=json.loads((ROOT/'phase1/scenarios/catalog.json').read_text('utf-8'))
    strict(value, 'catalog_version scenes')
    if value['catalog_version']!=VERSION:raise Problem('INVALID_INPUT','Unknown catalog')
    for row in value['scenes']:
        strict(row,'scenario_id scenario_version manifest summary')
        cached=row['summary'];strict(cached,'scenario_id scenario_version title manifest_sha256 seed coordinate_frame availability synthetic')
        if any(cached[k]!=row[k] for k in ('scenario_id','scenario_version')) or cached['manifest_sha256']!=row['manifest']['sha256']:raise Problem('INVALID_INPUT','Cached catalog summary binding mismatch')
    return value

def get_scene(ident, version):
    row=next((s for s in catalog()['scenes'] if s['scenario_id']==ident and s['scenario_version']==version),None)
    if row is None:raise Problem('INVALID_INPUT','Scene version not found',404)
    strict(row,'scenario_id scenario_version manifest summary')
    raw=read_asset(row['manifest']);m=json.loads(raw)
    strict(m,'manifest_version scenario_id scenario_version title seed synthetic coordinates availability sources science assets')
    if m['manifest_version']!=VERSION or m['scenario_id']!=ident or m['scenario_version']!=version:raise Problem('INVALID_INPUT','Scene identity mismatch')
    strict(m['coordinates'],'coordinate_frame projected_crs extent_m anchor_wgs84')
    strict(m['science'],'code config profile model_signature')
    if type(m['scenario_version']) is not int or m['scenario_version']<1 or type(m['seed']) is not int or not 0<=m['seed']<=2147483647 or type(m['synthetic']) is not bool:raise Problem('INVALID_INPUT','Scene version/seed/type invalid')
    if m['science']['profile']!='smoke' or not re.fullmatch('[0-9a-f]{64}',m['science']['model_signature']):raise Problem('INVALID_INPUT','Uncertified scientific profile/signature')
    frame=m['coordinates']['coordinate_frame']
    if frame not in ('wgs84_projected','synthetic_local_m'):raise Problem('INVALID_INPUT','Unknown coordinate frame')
    if frame=='wgs84_projected' and (not m['coordinates']['projected_crs'] or not isinstance(m['coordinates']['anchor_wgs84'],dict)):raise Problem('INVALID_INPUT','Missing projected reference')
    strict(m['availability'],'normal closure no_pump')
    for gate in m['availability'].values():
        strict(gate,'allowed blockers')
        if type(gate['allowed']) is not bool or not isinstance(gate['blockers'],list) or any(not isinstance(t,str) or not t for t in gate['blockers']) or gate['allowed'] != (not gate['blockers']):raise Problem('INVALID_INPUT','Invalid mode gate')
    from phase1.contracts import validate
    if not m['sources'] or len({r['source_id'] for r in m['sources']})!=len(m['sources']):raise Problem('INVALID_INPUT','Scene sources missing/duplicate')
    for source in m['sources']:validate(source,'source')
    if m['coordinates']['coordinate_frame']=='synthetic_local_m' and (m['coordinates']['projected_crs'] is not None or m['coordinates']['anchor_wgs84'] is not None):raise Problem('INVALID_INPUT','Synthetic scene cannot have WGS84')
    strict(m['assets'],'network scenario_template parameters_template display')
    for asset in [*m['assets'].values(),m['science']['code'],m['science']['config']]:
        if asset is not None:read_asset(asset)
    d=json.loads(read_asset(m['assets']['display']))
    strict(d,'display_version scenario_id scenario_version coordinate_frame extent_m terrain rivers bridges cities depots task_locations wells vehicles roads hydro_placement')
    if d['display_version']!='dual-display-v1' or d['scenario_id']!=ident or d['scenario_version']!=version or d['coordinate_frame']!=m['coordinates']['coordinate_frame'] or d['extent_m']!=m['coordinates']['extent_m']:raise Problem('INVALID_INPUT','Display identity mismatch')
    validate_display(d)
    if m['assets']['network'] is not None: validate_network(d,read_asset(m['assets']['network']))
    elif d['roads'] or d['bridges']:raise Problem('INVALID_INPUT','Road preview without actual network')
    scenario=json.loads(read_asset(m['assets']['scenario_template']))
    templates=json.loads(read_asset(m['assets']['parameters_template']))
    physical={w['well_id']:w for w in scenario['payload']['wells']}
    resource=(templates.get('normal') or {}).get('resources')
    if resource:
        vehicles={r['vehicle_id'] for r in resource['vehicles']};tasks={r['task_id']:r for r in resource['tasks']};depots={r['depot_id'] for r in resource['inventory']}
        if any(v['vehicle_id'] not in vehicles for v in d['vehicles']) or any(x['depot_id'] not in depots for x in d['depots']) or any(t['task_id'] not in tasks or tasks[t['task_id']]['well_id']!=t['well_id'] for t in d['task_locations']):raise Problem('INVALID_INPUT','Display resource references mismatch')
    if any(w['well_id'] not in physical for w in d['wells']):raise Problem('INVALID_INPUT','Display well mismatch')
    placement=d['hydro_placement']
    if placement:
        angle=placement['rotation_rad'];dx,dy=placement['translation_m']
        for well in d['wells']:
            xyz=physical[well['well_id']]['position'];x,y=xyz['x_local_m'],xyz['y_local_m'];expected=[math.cos(angle)*x-math.sin(angle)*y+dx,math.sin(angle)*x+math.cos(angle)*y+dy]
            if abs(expected[0]-well['position_m']['x_m'])>1e-6 or abs(expected[1]-well['position_m']['y_m'])>1e-6:raise Problem('INVALID_INPUT','Hydro view rigid placement mismatch')
    dump(d)
    summary={k:copy.deepcopy(m[k]) for k in ('scenario_id','scenario_version','title','seed','synthetic','availability')}
    summary.update(manifest_sha256=row['manifest']['sha256'],coordinate_frame=m['coordinates']['coordinate_frame'])
    if summary!=row['summary']:raise Problem('INVALID_INPUT','Cached summary differs from published manifest')
    for mode in ('normal','closure'):
        if summary['availability'][mode]['allowed']:
            try:
                from phase1.traffic.engine import binary
                binary('sumo')
            except RuntimeError:
                summary['availability'][mode]=dict(allowed=False,blockers=['ENGINE_UNAVAILABLE: sumo'])
    return dict(catalog_version=VERSION,scene=summary,manifest=m,display=d)

def binding(request, scene, parameter):
    return dict(scenario_id=request['scenario_id'],scenario_version=request['scenario_version'],manifest_sha256=scene['scene']['manifest_sha256'],parameter_version=request['parameter_version'],seed=request['seed'],run_id=request['run_id'],manifest=scene['manifest'],parameters=parameter,display=scene['display'])

def verify_binding(request, value, context=None):
    if request['scenario_id'] not in IDS:return
    if value is None:raise Problem('INVALID_INPUT','Managed scene binding missing',409)
    scene=get_scene(request['scenario_id'],request['scenario_version'])
    if request['seed']!=scene['manifest']['seed']:raise Problem('ARTIFACT_INVALID','Scene seed mismatch',409)
    if context is not None:
        if context.get('parameters')!=value['parameters'] or context.get('scenario')!=value.get('scenario') or context.get('preflight',{}).get('model_signature')!=scene['manifest']['science']['model_signature']:raise Problem('ARTIFACT_INVALID','Job context differs from frozen scene binding',409)
    for k in ('scenario_id','scenario_version','parameter_version','seed','run_id'):
        if value.get(k)!=request[k]:raise Problem('ARTIFACT_INVALID','Scene binding identity mismatch',409)
    if value['manifest_sha256']!=scene['scene']['manifest_sha256'] or value['manifest']!=scene['manifest'] or value['display']!=scene['display']:raise Problem('ARTIFACT_INVALID','Scene binding changed',409)
    if any(s['source_id']=='dispatch-context-v1' for s in scene['manifest']['sources']):
        from .planning import context_for
        proof=value.get('planning');metadata=context_for(request['scenario_id'],request['scenario_version'])
        if not proof or proof['context']!=metadata['context'] or proof['context_sha256']!=metadata['context_sha256']:
            raise Problem('ARTIFACT_INVALID','Planning context changed',409)
        selection=proof['configuration']
        if hashlib.sha256(dump(selection).encode()).hexdigest()!=proof['selection_sha256'] or selection['manifest_sha256']!=value['manifest_sha256'] or selection['context_sha256']!=proof['context_sha256'] or selection['mode']!=request['payload']['mode']:
            raise Problem('ARTIFACT_INVALID','Planning selection changed',409)
        if selection.get('traffic_profile')=='synthetic_background_small':
            from .planning import dynamic_for
            dynamic_ref,dynamic_config=dynamic_for(metadata)
            if proof.get('dynamic_config_ref')!=dynamic_ref or proof.get('dynamic_config')!=dynamic_config or selection.get('dynamic_config_sha256')!=dynamic_ref['sha256']:
                raise Problem('ARTIFACT_INVALID','Dynamic policy changed',409)
        elif proof.get('dynamic_config') is not None:raise Problem('ARTIFACT_INVALID','Unexpected dynamic policy',409)
        params=value['parameters']['data'];window=selection['risk_window_model_day']
        if proof.get('custom_tasks'):
            from phase1.backend.custom_tasks import check as check_custom
            custom=[check_custom(entry,metadata) for entry in proof['custom_tasks']]
            if [entry['record']['task_ref'] for entry in custom]!=selection.get('custom_task_refs'):
                raise Problem('ARTIFACT_INVALID','Selected custom task refs changed',409)
            expanded={t['task_id']:t for t in (params.get('resources') or {}).get('tasks',[])}
            if any(expanded.get(entry['snapshot']['id'])!=entry['snapshot']['data'] for entry in custom):
                raise Problem('ARTIFACT_INVALID','Expanded custom task differs from snapshot',409)
        elif selection.get('custom_task_refs'):raise Problem('ARTIFACT_INVALID','Missing selected custom snapshots',409)
        axis=value['scenario']['payload']['time_axis']
        expected=[axis['reference_model_day'],axis['model_end_day']] if selection['risk_profile']=='control' else [0.,axis['model_end_day']]
        if window!=expected or [params['risk_window_start_day'],params['risk_window_stop_day']]!=expected:
            raise Problem('ARTIFACT_INVALID','Explicit risk profile mismatch',409)
        if selection['mode']=='no_pump':
            if selection['selected_task_ids'] or any(params.get(k) is not None for k in ('traffic','resources','network_path')):raise Problem('ARTIFACT_INVALID','no_pump selection mismatch',409)
        else:
            selected=set(selection['selected_task_ids']);dispatches=params['traffic']['dispatches']
            assignments={a['task_id']:a['vehicle_id'] for a in selection['assignments']}
            if {t['task_id'] for t in params['resources']['tasks']}!=selected or {t['task_id']:t['vehicle_id'] for t in dispatches}!=assignments:
                raise Problem('ARTIFACT_INVALID','Dispatch/resource selection mismatch',409)
    gate=scene['scene']['availability'][request['payload']['mode']]
    if not gate['allowed']:raise Problem('INVALID_INPUT','SCENE_NOT_READY: '+'; '.join(gate['blockers']),409)


def _validate_display(d):
    strict(d,'display_version scenario_id scenario_version coordinate_frame extent_m terrain rivers bridges cities depots task_locations wells vehicles roads hydro_placement')
    if d['display_version']!='dual-display-v1' or type(d['scenario_version']) is not int or d['scenario_version']<1 or not isinstance(d['scenario_id'],str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',d['scenario_id']) or d['coordinate_frame'] not in ('wgs84_projected','synthetic_local_m'):raise Problem('INVALID_INPUT','Invalid display identity/type')
    extent=d['extent_m']
    def number(x):return type(x) in (int,float) and math.isfinite(x)
    if len(extent)!=4 or not all(map(number,extent)) or extent[2]-extent[0]!=100000 or extent[3]-extent[1]!=100000:raise Problem('INVALID_INPUT','Invalid display extent')
    def valid_id(value):
        if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}',value):raise Problem('INVALID_INPUT','Invalid display ID')
    def point(p):
        if not isinstance(p,list) or len(p)!=2 or not all(map(number,p)) or not extent[0]<=p[0]<=extent[2] or not extent[1]<=p[1]<=extent[3]:raise Problem('INVALID_INPUT','Point outside scene extent')
    def polygon(points):
        if len(points)<4 or points[0]!=points[-1]:raise Problem('INVALID_INPUT','Unclosed scene polygon')
        for p in points:point(p)
    definitions={'rivers':('id synthetic polygon_m','id'),'bridges':('id synthetic edge_ids line_m','id'),'cities':('id label synthetic polygon_m','id'),'depots':('depot_id synthetic position_m','depot_id'),'task_locations':('task_id well_id synthetic position_m','task_id'),'wells':('well_id synthetic position_m','well_id'),'vehicles':('vehicle_id synthetic position_m','vehicle_id'),'roads':('edge_id points road_class allowed_vehicle_classes synthetic','edge_id')}
    for key,(fields,idkey) in definitions.items():
        rows=d[key]
        if not isinstance(rows,list) or len({r[idkey] for r in rows})!=len(rows):raise Problem('INVALID_INPUT','Duplicate scene display id')
        for r in rows:
            strict(r,fields)
            valid_id(r[idkey])
            if 'well_id' in r:valid_id(r['well_id'])
            if 'label' in r and (not isinstance(r['label'],str) or not r['label']):raise Problem('INVALID_INPUT','Invalid display label')
            if 'edge_ids' in r:
                if not isinstance(r['edge_ids'],list) or not r['edge_ids'] or len(set(r['edge_ids']))!=len(r['edge_ids']):raise Problem('INVALID_INPUT','Invalid bridge edge IDs')
                for edge in r['edge_ids']:valid_id(edge)
            if 'allowed_vehicle_classes' in r:
                classes=r['allowed_vehicle_classes']
                if not isinstance(classes,list) or not classes or any(not isinstance(v,str) or not re.fullmatch('[a-zA-Z_]+',v) for v in classes) or len(set(classes))!=len(classes):raise Problem('INVALID_INPUT','Invalid road vehicle classes')
                if not isinstance(r['road_class'],str) or not r['road_class']:raise Problem('INVALID_INPUT','Invalid road class')
            if type(r['synthetic']) is not bool or (d['coordinate_frame']=='synthetic_local_m' and not r['synthetic']):raise Problem('INVALID_INPUT','Synthetic layer label missing')
            if 'position_m' in r:
                strict(r['position_m'],'x_m y_m');point([r['position_m']['x_m'],r['position_m']['y_m']])
            if 'polygon_m' in r:polygon(r['polygon_m'])
            for k in ('points','line_m'):
                if k in r:
                    if len(r[k])<2:raise Problem('INVALID_INPUT','Invalid scene line')
                    for p in r[k]:point(p)
    t=d['terrain']
    if t is not None:
        strict(t,'synthetic visual_only grid regions');strict(t['grid'],'nx ny origin_m spacing_m elevation_m')
        if t['synthetic'] is not True or t['visual_only'] is not True:raise Problem('INVALID_INPUT','Terrain must be synthetic/view only')
        g=t['grid']
        if type(g['nx']) is not int or type(g['ny']) is not int or min(g['nx'],g['ny'])<2 or len(g['elevation_m'])!=g['ny'] or any(len(row)!=g['nx'] or not all(map(number,row)) for row in g['elevation_m']):raise Problem('INVALID_INPUT','Invalid terrain grid')
        point(g['origin_m'])
        if len(g['spacing_m'])!=2 or not all(number(x) and x>0 for x in g['spacing_m']):raise Problem('INVALID_INPUT','Invalid grid spacing')
        point([g['origin_m'][i]+g['spacing_m'][i]*(g[['nx','ny'][i]]-1) for i in range(2)])
        if {r['kind'] for r in t['regions']}!={'plain','hillside','mountain'}:raise Problem('INVALID_INPUT','Missing terrain region')
        for r in t['regions']:
            strict(r,'id kind synthetic polygon_m');valid_id(r['id']);polygon(r['polygon_m'])
            if r['synthetic'] is not True:raise Problem('INVALID_INPUT','Terrain region synthetic label missing')
    if d['hydro_placement'] is not None:
        p=d['hydro_placement'];strict(p,'view_only synthetic rotation_rad translation_m')
        if p['view_only'] is not True or p['synthetic'] is not True or not number(p['rotation_rad']) or len(p['translation_m'])!=2 or not all(map(number,p['translation_m'])):raise Problem('INVALID_INPUT','Invalid view placement')

        wells={r['well_id']:r['position_m'] for r in d['wells']}
        for task in d['task_locations']:
            if task['well_id'] not in wells or task['position_m']!=wells[task['well_id']]:raise Problem('INVALID_INPUT','Task/Well view placement differs')


def _validate_network(display, raw):
    import xml.etree.ElementTree as ET
    try: net=ET.fromstring(raw)
    except ET.ParseError as exc:raise Problem('INVALID_INPUT','Invalid road network') from exc
    location=net.find('location');offset=[float(v) for v in location.get('netOffset').split(',')]
    edges={e.get('id'):e for e in net.findall('edge') if e.get('function')!='internal'}
    roads={r['edge_id']:r for r in display['roads']}
    if set(roads)!=set(edges):raise Problem('INVALID_INPUT','Preview/actual edge IDs differ')
    declared_bridges=[e for b in display['bridges'] for e in b['edge_ids']]
    if len(set(declared_bridges))!=len(declared_bridges) or not set(declared_bridges)<=set(edges):raise Problem('INVALID_INPUT','Bridge edges missing or duplicated')
    for ident,e in edges.items():
        lane=next((l for l in e.findall('lane') if l.get('index')=='0'),None)
        if lane is None:raise Problem('INVALID_INPUT','Missing lane0')
        points=[[float(p.split(',')[0])-offset[0],float(p.split(',')[1])-offset[1]] for p in lane.get('shape').split()]
        actual=roads[ident]['points']
        if len(points)!=len(actual) or any(abs(a-b)>1e-6 for p,q in zip(points,actual) for a,b in zip(p,q)):raise Problem('INVALID_INPUT','Road preview differs from actual lane0')
        expected_type=('synt-v1-' if display['coordinate_frame']=='synthetic_local_m' else '')+roads[ident]['road_class']
        if e.get('type')!=expected_type:raise Problem('INVALID_INPUT','Road class differs from net type')
        allowed=lane.get('allow');disallowed=(lane.get('disallow') or '').split()
        for cls in roads[ident]['allowed_vehicle_classes']:
            if cls in disallowed or (allowed is not None and cls not in allowed.split()):raise Problem('INVALID_INPUT','Preview road class permission differs')
        water=any(segment_in_polygon(a,b,r['polygon_m']) for r in display['rivers'] for a,b in zip(points,points[1:]))
        if water and ident not in declared_bridges:raise Problem('INVALID_INPUT','River crossed outside declared bridge')
        if ident in declared_bridges:
            if not water:raise Problem('INVALID_INPUT','Declared bridge does not cross river')
            bridge=next(b for b in display['bridges'] if ident in b['edge_ids'])
            nodes={j.get('id'):[float(j.get('x'))-offset[0],float(j.get('y'))-offset[1]] for j in net.findall('junction')}
            pair=[bridge['line_m'][0],bridge['line_m'][-1]];expected=[nodes[e.get('from')],nodes[e.get('to')]]
            close=lambda x,y:all(abs(a-b)<1e-6 for a,b in zip(x,y))
            if not ((close(pair[0],expected[0]) and close(pair[1],expected[1])) or (close(pair[1],expected[0]) and close(pair[0],expected[1]))):raise Problem('INVALID_INPUT','Bridge preview differs from actual junction endpoints')

def segment_in_polygon(a,b,polygon):
    # Split a segment at all polygon-boundary intersections, then test open midpoints.
    def cross(x,y):return x[0]*y[1]-x[1]*y[0]
    direction=[b[i]-a[i] for i in range(2)];cuts=[0.,1.]
    for c,d in zip(polygon,polygon[1:]):
        side=[d[i]-c[i] for i in range(2)];delta=[c[i]-a[i] for i in range(2)];den=cross(direction,side)
        if abs(den)>1e-12:
            t=cross(delta,side)/den;u=cross(delta,direction)/den
            if 0<=t<=1 and 0<=u<=1:cuts.append(t)
    def inside(p):
        result=False
        for c,d in zip(polygon,polygon[1:]):
            if (c[1]>p[1])!=(d[1]>p[1]) and p[0]<(d[0]-c[0])*(p[1]-c[1])/(d[1]-c[1])+c[0]:result=not result
        return result
    cuts=sorted(set(cuts))
    return any(inside([a[i]+direction[i]*(lo+hi)/2 for i in range(2)]) for lo,hi in zip(cuts,cuts[1:]) if hi-lo>1e-12)



def validate_display(value):
    try:return _validate_display(value)
    except Problem:raise
    except (TypeError,ValueError,KeyError,IndexError,OverflowError) as exc:raise Problem('INVALID_INPUT','Invalid nested display data') from exc

def validate_network(display, raw):
    try:return _validate_network(display,raw)
    except Problem:raise
    except (TypeError,ValueError,KeyError,IndexError,AttributeError) as exc:raise Problem('INVALID_INPUT','Invalid network/display structure') from exc
