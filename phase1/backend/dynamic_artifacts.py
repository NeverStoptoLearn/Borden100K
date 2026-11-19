"""Additional identity checks for opt-in sidecars; flat trajectory checks stay strict."""
import json,math
from .core import Problem,validate

def check(result,request,context,root):
    files={a['artifact_id']:a for a in result['artifacts']}
    formats={'traffic-state':'traffic-state-v1','background-trajectories':'background-trajectories-v1','routing-decisions':'routing-decisions-v1'}
    config=context.get('dynamic_config')
    def require(test,message):
        if not test:raise Problem('ARTIFACT_INVALID','Dynamic sidecar: '+message)
    if config is None:
        require(not set(formats)&set(files),'unexpected unrequested background outputs');return
    packets={}
    for ident,format in formats.items():
        require(ident in files and files[ident]['role']=='summary','missing structured summary '+ident)
        value=json.loads((root/files[ident]['path']).read_text('utf-8'))
        require(value.get('format')==format and value.get('source')=='actual_engine' and value.get('demand_synthetic') is True,'producer type/source')
        require(all(value.get(k)==request[k] for k in ('scenario_id','scenario_version','parameter_version','run_id','seed')),'run identity')
        require(value.get('time_axis')==context['time_axis'],'time axis')
        require(all(value.get(k)==request['payload']['network'][k] for k in ('network_id','network_sha256')),'network identity')
        packets[ident]=value
    require('dynamic-config' in files,'missing input policy')
    require(json.loads((root/files['dynamic-config']['path']).read_text('utf-8'))==config,'input policy differs from bound configuration')
    demand={row['vehicle_id']:row for row in config['background']};rows={row['vehicle_id']:row for row in packets['background-trajectories']['vehicles']}
    require(len(rows)==len(packets['background-trajectories']['vehicles']) and set(rows)==set(demand),'demand accounting')
    for vid,row in rows.items():
        require(all(row[k]==demand[vid][k] for k in ('route_id','depart_s','from_edge','to_edge')),'background OD changed')
        require(row['status'] in ('arrived','unreachable','unreachable_before_insertion_due_closure'),'unfinished background demand')
        if row['status']=='arrived':require(row['actual_depart_s'] is not None and row['arrival_time_s'] is not None and row['arrival_time_s']>=row['actual_depart_s']>=row['depart_s'],'background actual time window')
        else:require(row['arrival_time_s'] is None,'fabricated arrival')
    previous={}
    for sample in packets['background-trajectories']['samples']:
        validate(sample,'trajectory_sample');vid=sample['vehicle_id'];key=(vid,sample['route_id'])
        require(vid in rows and rows[vid]['route_id']==sample['route_id'],'sample vehicle/route')
        row=rows[vid];t=sample['event_time_s']
        require(row['status']=='arrived' and row['actual_depart_s']<=t<=row['arrival_time_s'] and t>previous.get(key,-1),'background sample ordering/window')
        previous[key]=t
    def number(x):return type(x) in (int,float) and math.isfinite(x)
    for row in packets['traffic-state']['edge_samples']:
        require(number(row['event_time_s']) and row['event_time_s']>=0,'state sample time')
        require(type(row['vehicle_count']) is int and row['vehicle_count']>=0,'vehicle count')
        require(number(row['density_veh_km_lane']) and row['density_veh_km_lane']>=0,'density')
        require(type(row['congested']) is bool,'congestion flag')
        if not row['vehicle_count']:
            require(row['mean_speed_m_s'] is None and row['travel_time_s'] is None and row['congested'] is False and row['quality']=='empty_no_travel_observation','empty state fabricated as measurement')
        else:
            require(all(number(row[k]) and row[k]>=0 for k in ('mean_speed_m_s','travel_time_s')) and row['quality']=='observed','invalid occupied-edge measurement')
    for row in packets['routing-decisions']['decisions']:
        require(row['applied'] is True and all(number(row[k]) for k in ('decision_s','old_eta_s','new_eta_s')),'routing decision values')
        if row['reason']=='congestion_delay':require(row['old_edge_ids']!=row['new_edge_ids'] and row['old_eta_s']-row['new_eta_s']>=config['delay_threshold_s'],'unverified congestion route change')
