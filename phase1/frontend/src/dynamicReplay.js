import {groupTrajectories,atTime} from './spatial';
const numeric=x=>typeof x==='number'&&Number.isFinite(x);
export function prepareDynamic(data,roads){
 const d=data.dynamic;if(!d)return null;
 const network=data.network,s=data.scenario,roadMap=new Map(roads.map(r=>[r.edge_id,r]));
 for(const [name,format] of [['state','traffic-state-v1'],['background','background-trajectories-v1'],['decisions','routing-decisions-v1']]){
  const x=d[name];if(!x||x.format!==format||x.source!=='actual_engine'||x.demand_synthetic!==true)throw Error('Invalid dynamic producer/source');
  for(const k of ['scenario_id','scenario_version','run_id','parameter_version','seed'])if(x[k]!==s[k])throw Error('Dynamic run identity mismatch');
  if(!network||x.network_id!==network.network_id||x.network_sha256!==network.network_sha256||JSON.stringify(Object.keys(x.time_axis).sort().map(k=>[k,x.time_axis[k]]))!==JSON.stringify(Object.keys(s.payload.time_axis).sort().map(k=>[k,s.payload.time_axis[k]])))throw Error('Dynamic network/time mismatch');
 }
 const metadata=new Map();for(const v of d.background.vehicles){const key=JSON.stringify([v.vehicle_id,v.route_id]);if(metadata.has(key)||!v.vehicle_id.startsWith('bg-')||!numeric(v.depart_s)||v.depart_s<0||(v.actual_depart_s!==null&&!numeric(v.actual_depart_s))||(v.arrival_time_s!==null&&!numeric(v.arrival_time_s)))throw Error('Invalid background vehicle');metadata.set(key,v);}
 const groups=groupTrajectories(d.background.samples);
 for(const g of groups){const v=metadata.get(g.key);if(!v||v.actual_depart_s===null||g.samples.some(s=>s.event_time_s<v.depart_s||s.event_time_s<v.actual_depart_s||(v.arrival_time_s!==null&&s.event_time_s>v.arrival_time_s)))throw Error('Background samples outside real insertion/arrival');}
 const edgeGroups=new Map();for(const e of d.state.edge_samples){if(!roadMap.has(e.edge_id)||!numeric(e.event_time_s)||e.event_time_s<0||!['observed','empty_no_travel_observation'].includes(e.quality))throw Error('Invalid dynamic edge state');for(const k of ['mean_speed_m_s','travel_time_s','vehicle_count','density_veh_km_lane'])if(e[k]!==null&&(!numeric(e[k])||e[k]<0))throw Error('Invalid dynamic metric');const rows=edgeGroups.get(e.edge_id)??[];if(rows.length&&e.event_time_s<=rows.at(-1).event_time_s)throw Error('Dynamic edge ordering');rows.push(e);edgeGroups.set(e.edge_id,rows);}
 for(const e of d.decisions.decisions){if(!numeric(e.decision_s)||!numeric(e.old_eta_s)||!numeric(e.new_eta_s)||e.applied!==true||[...e.old_edge_ids,...e.new_edge_ids].some(id=>!roadMap.has(id)))throw Error('Invalid routing decision');}
 return {groups,metadata,edgeGroups,decisions:d.decisions.decisions,roadMap,offset:network.net_offset_m,scope:d.state.statistics_scope};
}
export function dynamicFrame(model,time){
 if(!model)return {backgroundVehicles:[],edgeStates:[],routingDecisions:[]};
 const backgroundVehicles=[];
 for(const g of model.groups){const v=model.metadata.get(g.key);if(time<g.samples[0].event_time_s||(v.arrival_time_s!==null&&time>=v.arrival_time_s))continue;const sample=atTime(g.samples,time);backgroundVehicles.push({key:g.key,vehicle_id:g.vehicle_id,route_id:g.route_id,position_m:[sample.sumo_position_m.x_m-model.offset[0],sample.sumo_position_m.y_m-model.offset[1]],phase:time>g.samples.at(-1).event_time_s?'held_last_recorded':g.samples.some(s=>s.event_time_s===time)?'sampled':'interpolated',source:'actual_engine',demand_synthetic:true});}
 const edgeStates=[];for(const [edge_id,rows] of model.edgeGroups){const e=rows.findLast(e=>e.event_time_s<=time);if(e)edgeStates.push({...e,points_m:model.roadMap.get(edge_id).points_m,kind:'edge-state',id:edge_id});}
 const routingDecisions=model.decisions.filter(e=>e.decision_s<=time).map((e,i)=>({...e,id:'decision-'+i,kind:'routing-decision',eta_delta_s:e.new_eta_s-e.old_eta_s,old_paths:e.old_edge_ids.map(id=>model.roadMap.get(id).points_m),new_paths:e.new_edge_ids.map(id=>model.roadMap.get(id).points_m)}));
 const rows=[...model.metadata.values()];
 const backgroundSummary={requested:rows.length,actually_departed:rows.filter(v=>numeric(v.actual_depart_s)).length,arrived:rows.filter(v=>v.status==='arrived'&&numeric(v.arrival_time_s)).length,unreachable:rows.filter(v=>v.status.startsWith('unreachable')).length,with_samples:model.groups.length,scope:'final_run_statistics_not_playback_time'};
 return {backgroundVehicles,edgeStates,routingDecisions,backgroundSummary};
}
export function backgroundCapability(model){return model?.groups.length?{status:'PASS',reason:'Actual SUMO background samples; synthetic demand'}:{status:'NOT_RUN',reason:model?'All background OD unreachable/no actual background traces (empty demand also possible)':'No ordinary-vehicle engine trajectories'};}
