"""Separate, explicit test-only illustration. Does NOT execute any engine."""
import copy
import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4
import numpy as np
import xml.etree.ElementTree as ET
from .exporter import ROOT, canonical, digest


def build_fixture(output_dir):
    root=Path(output_dir).resolve();root.mkdir(parents=True,exist_ok=False)
    def example(kind):return json.loads((ROOT/'schemas/examples'/ (kind+'.json')).read_text('utf-8'))
    scenario,status,traffic,resource,hydro=(example(k) for k in ('scenario','run_status','traffic_result','resource_result','hydro_result'))
    ident=str(uuid4())
    for e in (scenario,status,traffic,resource,hydro):
        e['run_id']=ident;e['data_provenance']['calibration']='synthetic_anchor';e['data_provenance']['fixture']=True
        e['data_provenance']['sources'][0]['reference']='Hand-authored KML compatibility fixture; no SUMO/Hydro execution; NOT BORDEN GEOREFERENCE'
    scenario['payload']['georeference']=dict(status='synthetic_anchor',projected_crs='EPSG:32610',anchor_wgs84=dict(lon=-122.4325,lat=37.767),local_to_projected=dict(matrix_2x3=[[1,0,550000],[0,1,4180000]],z_offset_m=0,transform_version='explicit-synthetic-v1',validation_artifact=None))
    scenario['payload']['wells'][0]['cell_id']=0
    w=copy.deepcopy(scenario['payload']['wells'][0]);w.update(well_id='well-2',cell_id=1,position=dict(x_local_m=500,y_local_m=500,z_local_m=210));scenario['payload']['wells'].append(w)
    status.update(status='succeeded',error=None,artifacts=[])
    status['payload']['previous_status']='running'
    artifacts=[]
    def artifact(name,data,role,media,units):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        a=dict(artifact_id=name.replace('/','-').replace('.','-'),path=name,sha256=digest(data),role=role,media_type=media,units=units)
        artifacts.append(a);return a
    routes=[];traffic['artifacts']=[]
    for i in range(2):
        route=copy.deepcopy(traffic['payload']['routes'][0]);route.update(task_id=f'install-{i+1}',vehicle_id=f'truck-{i+1}',route_id=f'route-{i}',depart_s=i*10,arrival_time_s=600+i*10,edge_ids=[f'e{i*2}',f'e{i*2+1}'])
        samples=[dict(vehicle_id=route['vehicle_id'],route_id=route['route_id'],event_time_s=s+i*10,sumo_position_m=dict(x_m=300+s,y_m=400+i*100),wgs84=None) for s in (0,300,600)]
        a=artifact(f'trajectory_{i}.json',canonical(samples),'trajectory','application/json','m,s');traffic['artifacts'].append(a);route['trajectory_artifact_id']=a['artifact_id'];routes.append(route)
    traffic['payload']['routes']=routes
    net=ET.Element('net');ET.SubElement(net,'location',projParameter='EPSG:32610',netOffset='-550000,-4180000')
    for i in range(4):
        edge=ET.SubElement(net,'edge',id=f'e{i}');x=300+(i%2)*300;y=400+(i//2)*100
        ET.SubElement(edge,'lane',id=f'e{i}_0',index='0',shape=f'{x},{y} {x+300},{y}')
    net_raw=ET.tostring(net,encoding='utf-8',xml_declaration=True)
    a=artifact('network.net.xml',net_raw,'config','text/xml','m');a['artifact_id']='traffic-network-net';traffic['artifacts'].append(a)
    resource['payload']['tasks'][0]['vehicle_arrival_s']=600
    task=copy.deepcopy(resource['payload']['tasks'][0]);task.update(task_id='install-2',well_id='well-2',vehicle_id='truck-2',vehicle_arrival_s=610,unload_complete_s=910,install_complete_s=1810,pump_start_s=1810,pump_stop_s=86410);resource['payload']['tasks'].append(task)
    b=io.BytesIO();np.savez_compressed(b,times_day=np.array([0.,14600.,21900.]),C=np.array([[0.,0.],[1.,2.],[.5,1.]]),cell_xyz_m=np.array([[300.,400.,210.],[500.,500.,210.]]),cell_ids=np.array([0,1],dtype=np.int64))
    a=artifact('concentration.npz',b.getvalue(),'concentration','application/octet-stream','mg/L');hydro['artifacts']=[a];hydro['payload']['concentration_artifact_id']=a['artifact_id']
    for name,e in [('traffic',traffic),('resources',resource),('hydro',hydro)]:
        a=artifact(name+'_result.json',canonical(e),'summary','application/json','see units');a['artifact_id']=name+'-result'
    status['artifacts']=artifacts
    base=example('resource_request')['payload'];base={k:v for k,v in base.items() if k not in ('traffic_run_id','routes')}
    base['inventory'][0]['quantity']=2
    for collection,key,value in [('vehicles','vehicle_id','truck-2'),('crews','crew_id','crew-2'),('equipment','equipment_id','pump-2'),('tasks','task_id','install-2')]:
        item=copy.deepcopy(base[collection][0]);item[key]=value
        if collection=='tasks':item['well_id']='well-2'
        base[collection].append(item)
    params=dict(id=status['parameter_version'],version=1,source=scenario['data_provenance']['sources'][0],data=dict(scenario=dict(id=scenario['scenario_id'],version=scenario['scenario_version']),risk_window_start_day=0.,risk_window_stop_day=21900.,traffic=dict(network=dict(network_id=traffic['payload']['network_id'],source_id='demo',network_sha256=digest(net_raw),proj_parameter='EPSG:32610',net_offset_m=[-550000,-4180000])),resources=base))
    view={k:status[k] for k in ('run_id','scenario_id','scenario_version','parameter_version','seed')}
    view.update(view_version='export-view-v1',locations=[dict(kind='depot',id='depot-1',space='model_local',position=dict(x_local_m=0,y_local_m=0,z_local_m=210),source_ref='explicit test fixture, not a real facility',synthetic=True)])
    result=dict(scenario=scenario,run_status=status,parameters=params,artifact_root=str(root),view_metadata=view)
    (root/'bundle.json').write_bytes(canonical(result))
    return result
