"""Strict phase1-v1 inputs -> portable, deterministic offline presentation files."""
import copy
import csv
from datetime import datetime, timedelta
import hashlib
import html
import io
import json
import math
from pathlib import Path
import struct
import stat
import zipfile
import zlib
import xml.etree.ElementTree as ET

import numpy as np
from jsonschema import Draft202012Validator, FormatChecker

ROOT = Path(__file__).resolve().parents[2]
VERSION = 'earth-export-v1'
KML = 'http://www.opengis.net/kml/2.2'
GX = 'http://www.google.com/kml/ext/2.2'
ET.register_namespace('', KML)
ET.register_namespace('gx', GX)
SCHEMA = json.loads((ROOT/'schemas/phase1-v1.schema.json').read_text('utf-8'))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
IDENTITY = ('contract_version','experiment_id','run_id','scenario_id','scenario_version','parameter_version','seed','units')
MAX_BYTES = 128*1024*1024
MAX_ROWS = 2_000_000


class ExportError(ValueError):
    pass


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (ValueError, TypeError) as exc:
        raise ExportError('Non-finite or non-JSON input') from exc


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_file(root, name):
    if not isinstance(name,str) or not name or ':' in name or '\\' in name or name.startswith('/') or any(x in ('','.','..') for x in name.split('/')):
        raise ExportError('Unsafe artifact path')
    root=Path(root).resolve();path=root/name
    reject_links(path)
    for p in (path,*path.parents):
        if p==root:break
        if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise ExportError('Linked artifact')
    if not path.resolve().is_relative_to(root) or not path.is_file():raise ExportError('Missing/escaped artifact')
    if path.stat().st_size>MAX_BYTES:raise ExportError('Artifact size limit')
    return path


def reject_links(path):
    """Reject junctions as well as symbolic links on Python 3.10/3.11."""
    path=Path(path).absolute()
    for p in (path,*path.parents):
        try:attrs=p.lstat()
        except FileNotFoundError:continue
        if stat.S_ISLNK(attrs.st_mode) or getattr(attrs,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400):raise ExportError('Linked/reparse path prohibited')


def message(value, kind, status, allow_fixture):
    canonical(value)
    try:VALIDATOR.validate(value)
    except Exception as exc:raise ExportError('Contract validation: '+str(exc)) from exc
    if value['kind']!=kind:raise ExportError('Wrong envelope kind')
    if value['status']!=status:raise ExportError('Only succeeded results / queued scenarios supported')
    if value['data_provenance']['fixture'] and not allow_fixture:raise ExportError('Fixture prohibited in production export')


def same(a,b):
    if any(a[k]!=b[k] for k in IDENTITY):raise ExportError('Cross-run/version identity mismatch')


def csv_bytes(fields, rows):
    f=io.StringIO(newline='');w=csv.writer(f);w.writerow(fields)
    for row in rows:
        # User text must not become a spreadsheet formula. Numeric values stay raw.
        w.writerow(["'"+x if isinstance(x,str) and x.lstrip().startswith(('=','+','-','@','\t','\r')) else x for x in row])
    return f.getvalue().encode('utf-8')


def png(points, values=None):
    """Exact local sample markers, no interpolation/smoothing or geographic overlay."""
    image=np.full((384,384,3),24,dtype=np.uint8)
    if len(points):
        xy=np.asarray(points,dtype=float)[:,:2];lo=xy.min(axis=0);span=np.maximum(xy.max(axis=0)-lo,1)
        pixels=(20+(xy-lo)/span*343).astype(int)
        if values is None:colors=np.tile([50,220,190],(len(xy),1))
        else:
            v=np.asarray(values);den=max(float(v.max()-v.min()),1e-30);f=(v-v.min())/den
            colors=np.stack((50+205*f,180-110*f,220-190*f),axis=1).astype(np.uint8)
        for (x,y),color in zip(pixels,colors):image[max(0,382-y):min(384,387-y),max(0,x-2):min(384,x+3)]=color
    raw=b''.join(b'\0'+row.tobytes() for row in image)
    def chunk(tag,data):return struct.pack('>I',len(data))+tag+data+struct.pack('>I',zlib.crc32(tag+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',384,384,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(raw))+chunk(b'IEND',b'')


def sub(parent, tag, text=None, gx=False):
    node=ET.SubElement(parent,'{'+(GX if gx else KML)+'}'+tag)
    if text is not None:node.text=str(text)
    return node


def extended(parent, values):
    ext=sub(parent,'ExtendedData')
    for key,value in values.items():
        data=sub(ext,'Data');data.set('name',str(key));sub(data,'value',html.escape(str(value),quote=True))


def export_run(*,scenario,run_status,parameters,artifact_root,output_dir,view_metadata=None,profile='earth_web',allow_fixture=False):
    if profile not in ('earth_web','earth_pro'):raise ExportError('Unknown export profile')
    scenario,run_status,parameters,view_metadata=copy.deepcopy((scenario,run_status,parameters,view_metadata))
    message(scenario,'scenario','queued',allow_fixture);message(run_status,'run_status','succeeded',allow_fixture);same(scenario,run_status)
    canonical(parameters)
    if parameters.get('id')!=run_status['parameter_version'] or parameters.get('version')!=1 or parameters.get('data',{}).get('scenario')!={'id':scenario['scenario_id'],'version':scenario['scenario_version']}:
        raise ExportError('Parameter snapshot mismatch')
    reject_links(output_dir);reject_links(artifact_root)
    out=Path(output_dir).resolve();root=Path(artifact_root).resolve()
    if out==root or out.is_relative_to(root):raise ExportError('Export output must be outside immutable run directory')
    if out.exists() and any(out.iterdir()):raise ExportError('Output directory must be new or empty')
    files={};inputs={};indexed={};total=0
    for i,a in enumerate(run_status['artifacts']):
        if a['artifact_id'] in indexed:raise ExportError('Duplicate artifact ID')
        data=safe_file(root,a['path']).read_bytes();total+=len(data)
        if total>MAX_BYTES:raise ExportError('Total input size limit')
        if digest(data)!=a['sha256']:raise ExportError('Artifact SHA256 mismatch')
        indexed[a['artifact_id']]=(a,data)
        name=f'data/original_{i:04d}.bin'
        files[name]=data;inputs[a['artifact_id']]={'path':a['path'],'sha256':a['sha256'],'archive_path':name}
    stages={}
    for name in ('traffic','resources','hydro'):
        if name+'-result' in indexed:
            data=indexed[name+'-result'][1]
            try:result=json.loads(data)
            except Exception as exc:raise ExportError('Invalid result JSON') from exc
            message(result,{'resources':'resource_result'}.get(name,name+'_result'),'succeeded',allow_fixture);same(run_status,result)
            for a in result['artifacts']:
                if a['artifact_id'] not in indexed or indexed[a['artifact_id']][0]!=a:raise ExportError('Summary artifact/index mismatch')
            stages[name]=result;files['data/'+name+'_result.json']=data
    if 'hydro' not in stages:raise ExportError('Missing published hydro-result')
    h=stages['hydro'];npzid=h['payload']['concentration_artifact_id']
    if npzid not in indexed or indexed[npzid][0]['role']!='concentration':raise ExportError('Missing concentration artifact')
    npz=indexed[npzid][1]
    try:
        with zipfile.ZipFile(io.BytesIO(npz)) as z:
            if len(z.infolist())>16 or sum(i.file_size for i in z.infolist())>MAX_BYTES:raise ExportError('NPZ decompression limit')
        with np.load(io.BytesIO(npz),allow_pickle=False) as z:t,c,xyz,ids=(z[k] for k in ('times_day','C','cell_xyz_m','cell_ids'))
        if (t.dtype!=np.float64 or c.dtype!=np.float64 or xyz.dtype!=np.float64 or t.ndim!=1 or len(t)<2 or ids.ndim!=1 or ids.dtype.kind not in 'iu' or c.shape!=(len(t),len(ids)) or xyz.shape!=(len(ids),3) or len(set(ids.tolist()))!=len(ids) or not len(ids) or not all(np.isfinite(x).all() for x in (t,c,xyz)) or not np.all(np.diff(t)>0) or t.tolist()!=h['payload']['times_day'] or c.min() < -1e-7):raise ExportError('Invalid concentration arrays')
        if not np.array_equal(ids,np.arange(len(ids))) or t[0]<0 or t[-1]>scenario['payload']['time_axis']['model_end_day']:raise ExportError('Invalid cell IDs/model time domain')
        for well in scenario['payload']['wells']:
            cell=well['cell_id'];position=[well['position'][k] for k in ('x_local_m','y_local_m','z_local_m')]
            if cell>=len(ids) or not np.allclose(xyz[cell],position,rtol=0,atol=1e-6):raise ExportError('Well cell/local position mismatch')
        if c.size>MAX_ROWS:raise ExportError('Concentration CSV row limit')
    except ExportError:raise
    except Exception as exc:raise ExportError('Invalid concentration NPZ') from exc
    files['data/concentration.npz']=npz
    axis=scenario['payload']['time_axis'];origin=datetime.fromisoformat(axis['scenario_origin_utc'].replace('Z','+00:00'))
    def utc(seconds):
        try:return (origin+timedelta(seconds=float(seconds))).isoformat(timespec='microseconds').replace('+00:00','Z')
        except Exception as exc:raise ExportError('UTC date overflow') from exc
    files['data/concentrations.csv']=csv_bytes(['model_day','utc','historical_model_frame','cell_id','x_local_m','y_local_m','z_local_m','concentration_mg_L'],([float(day),utc((day-axis['reference_model_day'])*86400),bool(day<axis['reference_model_day']),int(ids[j]),*xyz[j].tolist(),float(c[i,j])] for i,day in enumerate(t) for j in range(len(ids))))
    # One exact horizontal elevation slice; never collapse different depths.
    slice_z=float(xyz[0,2]);mask=xyz[:,2]==slice_z;slice_info={'z_local_m':slice_z,'selection':'exact z of first cell; no depth aggregation','cells':ids[mask].tolist(),'frames':[]}
    for i,day in enumerate(t):
        name=f'files/concentration_{i:04d}.png';v=c[i,mask];files[name]=png(xyz[mask],v)
        slice_info['frames'].append(dict(model_day=float(day),utc=utc((day-axis['reference_model_day'])*86400),min_mg_L=float(v.min()),max_mg_L=float(v.max()),image=name))
    samples={};routes=stages.get('traffic',{}).get('payload',{}).get('routes',[])
    network=(parameters['data'].get('traffic') or {}).get('network')
    route_edges={};route_rows=[];network_hash=None
    candidates=[a for a in stages.get('traffic',{}).get('artifacts',[]) if Path(a['path']).name=='network.net.xml']
    if len(candidates)>1:raise ExportError('Ambiguous indexed road network')
    if candidates:
        a=candidates[0];raw=indexed[a['artifact_id']][1]
        if not network or digest(raw)!=network['network_sha256']:raise ExportError('Network SHA/metadata mismatch')
        if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ExportError('Network XML entities prohibited')
        try:
            net=ET.fromstring(raw);loc=net.find('location')
            if loc is None or loc.get('projParameter')!=network['proj_parameter'] or [float(x) for x in loc.get('netOffset','').split(',')]!=network['net_offset_m']:raise ExportError('Network location metadata mismatch')
            edges={e.get('id'):e for e in net.findall('edge')}
            for route in routes:
                shapes=[]
                for seq,edge_id in enumerate(route['edge_ids']):
                    if edge_id not in edges:raise ExportError('Unknown route edge')
                    lane=next((l for l in edges[edge_id].findall('lane') if l.get('index')=='0'),None)
                    if lane is None:raise ExportError('Route edge has no lane0 centerline')
                    shape=[tuple(float(v) for v in pair.split(',')) for pair in lane.get('shape','').split()]
                    if len(shape)<2 or any(len(p)!=2 or not all(math.isfinite(v) for v in p) for p in shape):raise ExportError('Invalid road edge shape')
                    shapes.append(shape)
                    route_rows.extend([route['vehicle_id'],route['route_id'],seq,edge_id,lane.get('id'),j,x,y,'verified_network_lane0'] for j,(x,y) in enumerate(shape))
                route_edges[(route['vehicle_id'],route['route_id'])]=shapes
            network_hash=digest(raw)
        except ExportError:raise
        except Exception as exc:raise ExportError('Invalid indexed network XML') from exc
    for route in routes:
        if not route['reachable']:continue
        ident=route['trajectory_artifact_id']
        if ident not in indexed or indexed[ident][0]['role']!='trajectory':raise ExportError('Missing trajectory')
        raw=indexed[ident][1]
        try:arr=json.loads(raw)
        except Exception as exc:raise ExportError('Invalid trajectory JSON') from exc
        if not isinstance(arr,list) or len(arr)>MAX_ROWS:raise ExportError('Invalid trajectory size')
        last=-math.inf
        for s in arr:
            if not isinstance(s,dict):raise ExportError('Trajectory sample must be object')
            try:Draft202012Validator({'$ref':'#/$defs/trajectory_sample','$defs':SCHEMA['$defs']},format_checker=FormatChecker()).validate(s)
            except Exception as exc:raise ExportError('Invalid trajectory sample') from exc
            canonical(s)
        selected=[s for s in arr if s.get('vehicle_id')==route['vehicle_id'] and s.get('route_id')==route['route_id']]
        if not selected:raise ExportError('Empty reachable trajectory')
        for s in selected:
            seconds=s['event_time_s']
            if seconds<=last or not route['depart_s']<=seconds<=route['arrival_time_s']:raise ExportError('Trajectory time order/range')
            last=seconds
        key=(route['vehicle_id'],route['route_id'])
        if key in samples:raise ExportError('Duplicate vehicle/route')
        samples[key]=selected;files[f'data/trajectory_{len(samples):04d}.json']=raw
    files['data/trajectories.csv']=csv_bytes(['vehicle_id','route_id','scenario_seconds','utc','x_m','y_m'],([key[0],key[1],s['event_time_s'],utc(s['event_time_s']),s['sumo_position_m']['x_m'],s['sumo_position_m']['y_m']] for key,arr in samples.items() for s in arr))
    if not candidates:
        route_rows=[[key[0],key[1],'','','',i,s['sumo_position_m']['x_m'],s['sumo_position_m']['y_m'],'sampled_path_fallback'] for key,arr in samples.items() for i,s in enumerate(arr)]
    if len(route_rows)>MAX_ROWS:raise ExportError('Route vertex limit')
    files['data/routes.csv']=csv_bytes(['vehicle_id','route_id','edge_sequence','edge_id','lane_id','vertex_index','x_m','y_m','geometry_source'],route_rows)
    files['files/local_traffic.png']=png([[s['sumo_position_m']['x_m'],s['sumo_position_m']['y_m']] for arr in samples.values() for s in arr])
    wells={w['well_id']:w for w in scenario['payload']['wells']}
    tasks=stages.get('resources',{}).get('payload',{}).get('tasks',[])
    if 'resources' in stages and not stages['resources']['payload']['feasible']:raise ExportError('Infeasible resource result')
    route_by_task={r['task_id']:r for r in routes}
    resource_inputs=parameters['data'].get('resources') or {}
    task_inputs={q['task_id']:q for q in resource_inputs.get('tasks',[])}
    for task in tasks:
        route=route_by_task.get(task['task_id'])
        chain=[task[k] for k in ('vehicle_arrival_s','unload_complete_s','install_complete_s','pump_start_s','pump_stop_s')]
        if task['well_id'] not in wells or not route or not route['reachable'] or route['vehicle_id']!=task['vehicle_id'] or route['arrival_time_s']!=task['vehicle_arrival_s'] or chain!=sorted(chain) or chain[-2]>=chain[-1] or axis['reference_model_day']+chain[-1]/86400>axis['model_end_day']:raise ExportError('Invalid task/route/well timeline')
        if task['task_id'] not in task_inputs or task_inputs[task['task_id']]['well_id']!=task['well_id']:raise ExportError('Task does not match immutable resource inputs')
    fields=['task_id','well_id','vehicle_id','vehicle_arrival_s','unload_complete_s','install_complete_s','pump_start_s','pump_stop_s','extraction_rate_m3_day']
    files['data/tasks.csv']=csv_bytes(fields,([task[k] for k in fields] for task in tasks))
    files['data/pump_events.csv']=csv_bytes(['task_id','well_id','state','scenario_seconds','model_day','utc','extraction_rate_m3_day'],([q['task_id'],q['well_id'],state,q[key],axis['reference_model_day']+q[key]/86400,utc(q[key]),q['extraction_rate_m3_day'] if state=='pumping' else 0] for q in tasks for state,key in [('pumping','pump_start_s'),('stopped','pump_stop_s')]))
    objects=[]
    for w in wells.values():objects.append(dict(kind='well',id=w['well_id'],space='model_local',position=w['position'],source_ref='scenario well position',synthetic=w['synthetic']))
    for task in tasks:objects.append(dict(kind='task',id=task['task_id'],space='model_local',position=wells[task['well_id']]['position'],source_ref='derived_from_well:'+task['well_id'],synthetic=True))
    for (vehicle,route),arr in samples.items():objects.append(dict(kind='vehicle',id=vehicle,space='sumo',position=arr[0]['sumo_position_m'],source_ref='first actual sample, not depot/departure endpoint',synthetic=True))
    known={'well':set(wells),'task':set(route_by_task),'vehicle':{r['vehicle_id'] for r in routes},'depot':{s['depot_id'] for s in (parameters['data'].get('resources') or {}).get('inventory',[])}}
    for depot in sorted(known['depot']):objects.append(dict(kind='depot',id=depot,space='missing',position={},source_ref='position_missing',synthetic=True))
    if view_metadata is not None:
        allowed={'view_version','run_id','scenario_id','scenario_version','parameter_version','seed','locations','service_center_projected_m','georef_validation'}
        if set(view_metadata)-allowed or view_metadata.get('view_version')!='export-view-v1':raise ExportError('Invalid view metadata fields/version')
        if any(view_metadata.get(k)!=run_status[k] for k in ('run_id','scenario_id','scenario_version','parameter_version','seed')):raise ExportError('Sidecar identity mismatch')
        locations=view_metadata.get('locations',[])
        if not isinstance(locations,list) or len(locations)>10000:raise ExportError('Sidecar locations limit')
        seen=set()
        for loc in locations:
            if set(loc)!={'kind','id','space','position','source_ref','synthetic'} or loc['kind'] not in known or loc['id'] not in known[loc['kind']] or (loc['kind'],loc['id']) in seen:raise ExportError('Invalid sidecar object reference')
            seen.add((loc['kind'],loc['id']))
            expected={'model_local':{'x_local_m','y_local_m','z_local_m'},'sumo':{'x_m','y_m'},'wgs84':{'lon','lat'}}.get(loc['space'])
            if expected is None or set(loc['position'])!=expected or not all(isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x) for x in loc['position'].values()):raise ExportError('Invalid sidecar position')
            if not isinstance(loc['source_ref'],str) or len(loc['source_ref'])>2048 or any(x in loc['source_ref'].lower() for x in ('://','javascript:','file:')) or loc['synthetic'] is not True:raise ExportError('Invalid sidecar provenance')
            if loc['space']=='wgs84' and (abs(loc['position']['lon'])>180 or abs(loc['position']['lat'])>90):raise ExportError('Invalid WGS84 range')
            objects=[o for o in objects if (o['kind'],o['id'])!=(loc['kind'],loc['id'])];objects.append(loc)
        files['data/view_metadata.json']=canonical(view_metadata)
    files['data/objects.csv']=csv_bytes(['kind','id','space','position','source_ref','synthetic'],([o['kind'],o['id'],o['space'],json.dumps(o['position'],sort_keys=True),o['source_ref'],o['synthetic']] for o in objects))
    for name,value in [('scenario',scenario),('run_status',run_status),('parameters',parameters)]:files['data/'+name+'.json']=canonical(value)
    g=scenario['payload']['georeference'];warnings=[];mode='metadata_only';transform=None
    if g['status']=='uncalibrated':
        if any(g[k] is not None for k in ('projected_crs','local_to_projected','anchor_wgs84')):raise ExportError('Inconsistent uncalibrated metadata')
        warnings.append('GEOREFERENCE_UNAVAILABLE: no geographic geometry; coordinates are local only')
    elif g['status']=='calibrated':warnings.append('GEOREFERENCE_UNAVAILABLE: trusted approval registry not installed; labels/sidecar are not approval')
    elif g['status']=='synthetic_anchor':
        if not allow_fixture or not all(x['data_provenance']['fixture'] for x in (scenario,run_status,*stages.values())) or scenario['data_provenance']['calibration']!='synthetic_anchor':raise ExportError('Synthetic anchor requires separate explicit fixture channel')
        try:
            from pyproj import CRS,Transformer
        except ImportError as exc:raise ExportError('Optional pyproj required for explicitly requested fixture geometry') from exc
        try:
            crs=CRS.from_user_input(g['projected_crs'])
            if not crs.is_projected or any(a.unit_name not in ('metre','meter') for a in crs.axis_info):raise ExportError('Projected metric CRS required')
            affine=np.asarray(g['local_to_projected']['matrix_2x3'],dtype=float)
            if not np.isfinite(affine).all() or abs(np.linalg.det(affine[:,:2]))<1e-12:raise ExportError('Singular local affine')
            forward=Transformer.from_crs(crs,'EPSG:4326',always_xy=True);back=Transformer.from_crs('EPSG:4326',crs,always_xy=True)
            def transform(x,y):
                lon,lat=forward.transform(x,y);rx,ry=back.transform(lon,lat)
                if not all(math.isfinite(v) for v in (lon,lat)) or abs(lon)>180 or abs(lat)>90 or math.hypot(rx-x,ry-y)>0.01:raise ExportError('Projection round-trip/range failed')
                return lon,lat
            mode='synthetic_fixture';warnings.append('SYNTHETIC FIXTURE — NOT BORDEN GEOREFERENCE')
        except ExportError:raise
        except Exception as exc:raise ExportError('Invalid geographic transform') from exc
    kml=ET.Element('{'+KML+'}kml');doc=sub(kml,'Document');sub(doc,'name',('SYNTHETIC FIXTURE — NOT BORDEN GEOREFERENCE — ' if allow_fixture else '')+run_status['run_id']+' '+profile)
    sub(doc,'description','; '.join(warnings)+'; Synthetic uncalibrated model indicators, not health risk. Subsurface concentration remains in local plots/data. No map tiles bundled.')
    extended(doc,{k:json.dumps(run_status[k],sort_keys=True) for k in ('run_id','scenario_id','scenario_version','parameter_version','seed','units','model_build','data_provenance')})
    for state,color in [('installing','ff00ffff'),('pumping','ff00ff00'),('stopped','ff0000ff')]:
        style=sub(doc,'Style');style.set('id','pump-'+state)
        icon=sub(style,'IconStyle');sub(icon,'color',color);sub(icon,'scale','0.9')
        doc.remove(style);doc.insert(next(i for i,e in enumerate(doc) if e.tag=='{'+KML+'}ExtendedData'),style)
    count=0
    def placemark(folder,name,values=None,position=None):
        nonlocal count
        p=sub(folder,'Placemark');sub(p,'name',('SYNTHETIC FIXTURE | ' if mode=='synthetic_fixture' else '')+name)
        if values:extended(p,values)
        if position is not None:
            pt=sub(p,'Point');sub(pt,'altitudeMode','clampToGround');sub(pt,'coordinates',f'{position[0]:.12f},{position[1]:.12f},0');count+=1
        return p
    def local_point(position):return transform(*(affine@np.array([position['x_local_m'],position['y_local_m'],1.])))
    def sumo_point(position):
        if not network or network['proj_parameter'] in ('!','-','.'):raise ExportError('Unprojected SUMO network cannot become WGS84')
        road=Transformer.from_crs(network['proj_parameter'],'EPSG:4326',always_xy=True)
        value=road.transform(position['x_m']-network['net_offset_m'][0],position['y_m']-network['net_offset_m'][1])
        if not all(math.isfinite(v) for v in value) or abs(value[0])>180 or abs(value[1])>90:raise ExportError('SUMO projection invalid')
        return value
    if transform:
        center=(view_metadata or {}).get('service_center_projected_m')
        if center is None:center=back.transform(g['anchor_wgs84']['lon'],g['anchor_wgs84']['lat'])
        width,height=scenario['payload']['service_extent_m'];corners=[transform(center[0]+x*width/2,center[1]+y*height/2) for x,y in [(-1,-1),(1,-1),(1,1),(-1,1),(-1,-1)]]
        if max(p[0] for p in corners)-min(p[0] for p in corners)>180:raise ExportError('Dateline crossing unsupported')
        p=placemark(doc,'100km service area (not aquifer)');poly=sub(p,'Polygon');sub(poly,'altitudeMode','clampToGround');ring=sub(sub(poly,'outerBoundaryIs'),'LinearRing');sub(ring,'coordinates',' '.join(f'{x:.12f},{y:.12f},0' for x,y in corners));count+=1
    folder=sub(doc,'Folder');sub(folder,'name','Objects / positions')
    for o in objects:
        point=None
        if transform and o['space']!='missing':
            point=local_point(o['position']) if o['space']=='model_local' else sumo_point(o['position']) if o['space']=='sumo' else (o['position']['lon'],o['position']['lat'])
        placemark(folder,o['kind']+' '+o['id'],{'space':o['space'],'position':json.dumps(o['position']),'source':o['source_ref'],'synthetic':o['synthetic']},point)
    folder=sub(doc,'Folder');sub(folder,'name','SUMO road-edge geometry and sampled trajectories (separate)')
    for key,arr in samples.items():
        if key in route_edges:
            full=placemark(folder,'SUMO road-edge lane0 centerlines '+key[0]+' / '+key[1],{'network_sha256':network_hash,'selection':'indexed road edges in route order; lane index0; internal junction connectors not inferred'})
            if transform:
                multi=sub(full,'MultiGeometry')
                for shape in route_edges[key]:
                    line=sub(multi,'LineString');sub(line,'altitudeMode','clampToGround');sub(line,'coordinates',' '.join(f'{x:.12f},{y:.12f},0' for x,y in [sumo_point(dict(x_m=x,y_m=y)) for x,y in shape]))
                count+=1
        p=placemark(folder,'sampled_path '+key[0]+' / '+key[1],{'first_sample_utc':utc(arr[0]['event_time_s']),'last_sample_utc':utc(arr[-1]['event_time_s'])})
        if transform:
            coords=[sumo_point(s['sumo_position_m']) for s in arr]
            line=sub(p,'LineString');sub(line,'altitudeMode','clampToGround');sub(line,'coordinates',' '.join(f'{x:.12f},{y:.12f},0' for x,y in coords));count+=1
            if profile=='earth_pro':
                q=placemark(folder,'Timed vehicle '+key[0]+' / '+key[1]);track=sub(q,'Track',gx=True);sub(track,'altitudeMode','clampToGround')
                for s in arr:sub(track,'when',utc(s['event_time_s']))
                for x,y in coords:sub(track,'coord',f'{x:.12f} {y:.12f} 0',gx=True)
                count+=1
    folder=sub(doc,'Folder');sub(folder,'name','Installation and pump states [start, stop)')
    for task in tasks:
        point=local_point(wells[task['well_id']]['position']) if transform else None
        for state,start,stop in [('installing',task['unload_complete_s'],task['install_complete_s']),('pumping',task['pump_start_s'],task['pump_stop_s']),('stopped',task['pump_stop_s'],None)]:
            p=placemark(folder,task['well_id']+' '+state,{'task_id':task['task_id'],'state':state,'start_utc':utc(start),'start_s':start,'stop_s':stop,'extraction_rate_m3_day':task['extraction_rate_m3_day'] if state=='pumping' else 0},point)
            style=ET.Element('{'+KML+'}styleUrl');style.text='#pump-'+state;p.insert(1,style)
            if profile=='earth_pro':
                time=ET.Element('{'+KML+'}'+('TimeStamp' if stop is None else 'TimeSpan'));p.insert(1,time);sub(time,'when' if stop is None else 'begin',utc(start))
                if stop is not None:sub(time,'end',utc(stop))
    folder=sub(doc,'Folder');sub(folder,'name','Subsurface LOCAL concentration; never ground pollution')
    for i,frame in enumerate(slice_info['frames']):
        p=placemark(folder,f'Local z={slice_z:.6f} m / model day={frame["model_day"]:.6f}',{'model_day':frame['model_day'],'z_local_m':slice_z,'units':'mg/L','min':frame['min_mg_L'],'max':frame['max_mg_L']})
        description=ET.Element('{'+KML+'}description');description.text=f'<p>LOCAL exact elevation samples; not georeferenced; not surface or health risk. z={slice_z:.6f} m, day={frame["model_day"]:.6f}. Color range {frame["min_mg_L"]:.9g}..{frame["max_mg_L"]:.9g} mg/L; x right, y up. All raw data retained.</p><img src="{frame["image"]}" width="384" height="384"/>';p.insert(1,description)
    width,height=scenario['payload']['service_extent_m'];files['data/service_area.csv']=csv_bytes(['width_m','height_m','location_status'],[[width,height,'geographic' if transform else 'unlocated dimensions only; not centered on model']])
    files['files/service_area_schematic.png']=png([[0,0],[width,0],[width,height],[0,height],[0,0]])
    files['doc.kml']=ET.tostring(kml,encoding='utf-8',xml_declaration=True)
    files['README.txt']=('Borden export '+VERSION+'\n'+mode+'\n'+'\n'.join(warnings)+'\nNo Google Maps API or tiles. Google Earth client basemap may require network.\nConcentration images are exact local z samples, not Earth surface pollution. Full arrays retained.\n').encode('utf-8')
    manifest={'export_format_version':VERSION,'run_id':run_status['run_id'],'profile':profile,'geometry_mode':mode,'warnings':warnings,'geographic_feature_count':count,'suppressed_layers':[] if transform else ['service_area','objects','routes','timed_tracks','pump_geography'],'inputs':inputs,'input_snapshots_sha256':{name:digest(canonical(value)) for name,value in [('scenario_execution_view',scenario),('run_status',run_status),('parameters',parameters),('view_metadata',view_metadata)]},'identity':{k:run_status[k] for k in IDENTITY},'model_builds':{name:value['model_build'] for name,value in stages.items()},'concentration_slice':slice_info,'compatibility':{'earth_pro':'NOT_RUN: requires actual client test','earth_web':'NOT_RUN: static profile; animation not promised'},'members':{name:{'sha256':digest(data),'bytes':len(data)} for name,data in sorted(files.items())}}
    manifest['route_geometry']={'mode':'verified_network_lane0' if candidates else 'sampled_path_fallback','network_sha256':network_hash,'vertex_count':len(route_rows),'selection':'lane0 per indexed road edge in route order; junction connectors not fabricated'}
    files['manifest.json']=canonical(manifest)
    if sum(len(data) for data in files.values())>256*1024*1024:raise ExportError('Export size limit')
    out.mkdir(parents=True,exist_ok=True)
    for name,data in sorted(files.items()):
        path=out/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    name=('SYNTHETIC_FIXTURE-' if mode=='synthetic_fixture' else 'borden-')+run_status['run_id']+'-'+profile+'.kmz'
    kmz=out/name
    with zipfile.ZipFile(kmz,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for member in ['doc.kml']+sorted(n for n in files if n!='doc.kml'):
            info=zipfile.ZipInfo(member,(2000,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100644<<16;z.writestr(info,files[member])
    return {'export_format_version':VERSION,'run_id':run_status['run_id'],'profile':profile,'geometry_mode':mode,'warnings':warnings,'suppressed_layers':manifest['suppressed_layers'],'geographic_feature_count':count,'kml_path':str(out/'doc.kml'),'kmz_path':str(kmz),'manifest_path':str(out/'manifest.json'),'kmz_sha256':digest(kmz.read_bytes()),'kml_sha256':digest(files['doc.kml']),'manifest_sha256':digest(files['manifest.json'])}
