import {prepareDynamic,dynamicFrame,backgroundCapability} from './dynamicReplay';
import {groupTrajectories} from './spatial';
import {sameRun,trajectory} from './contract';
import {CONCENTRATION_NEGATIVE_TOLERANCE_MG_L} from './artifacts';
import {scheduledTasks} from './roadView';

const finite=(x)=>{if(typeof x!=='number'||!Number.isFinite(x))throw Error('Nonfinite map time/value');return x;};
const upper=(rows,t,get=x=>x.event_time_s)=>{let a=0,b=rows.length;while(a<b){const m=(a+b)>>1;if(get(rows[m])<=t)a=m+1;else b=m;}return a;};
const key=(v,r)=>JSON.stringify([v,r]);
const unavailable=reason=>({status:'NOT_RUN',reason});
export function prepareMapReplay(data,{display=data.sceneDisplay,trafficRequest=data.executedTrafficRequest??null,routePlans=data.routePlans??null,allowFixture=false}={}){
  const s=data.scenario;if(!s)throw Error('Missing scenario');
  const fixture=s.data_provenance?.fixture===true;
  if((fixture||data.source!=='api')&&!allowFixture)throw Error('Map requires verified actual replay or explicit fixture');
  if(data.status&&data.status.status!=='succeeded')throw Error('Map run is not succeeded');
  for(const message of [data.traffic,data.hydro,data.resource,data.status,trafficRequest])if(message)sameRun(s,message);
  for(const value of [display,data.sceneManifest,data.sceneBinding])if(value){
    for(const name of ['scenario_id','scenario_version'])if(value[name]!==s[name])throw Error('Map scene identity mismatch');
  }
  const manifestSHA=data.status?.artifacts?.find(a=>a.artifact_id==='scene-manifest')?.sha256??null;
  if(data.sceneBinding){
    for(const name of ['run_id','parameter_version','seed'])if(data.sceneBinding[name]!==s[name])throw Error('Map binding mismatch');
    if(data.sceneBinding.manifest_sha256!==manifestSHA)throw Error('Map manifest SHA mismatch');
  }
  if(data.sceneManifest&&data.sceneManifest.seed!==s.seed)throw Error('Map manifest seed mismatch');
  if(data.parameters){
    if(data.parameters.id!==s.parameter_version||data.parameters.version!==1||data.parameters.data?.scenario?.id!==s.scenario_id||data.parameters.data.scenario.version!==s.scenario_version)throw Error('Map parameter snapshot mismatch');
    if(data.sceneBinding?.parameters&&JSON.stringify(data.sceneBinding.parameters)!==JSON.stringify(data.parameters)){
      const canonical=v=>v&&typeof v==='object'?(Array.isArray(v)?v.map(canonical):Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])]))):v;
      if(JSON.stringify(canonical(data.sceneBinding.parameters))!==JSON.stringify(canonical(data.parameters)))throw Error('Map bound parameter content mismatch');
    }
  }
  const network=data.network??null;
  if(data.parameters){
    const pinned=data.parameters.data.traffic?.network??null;
    if(Boolean(pinned)!==Boolean(network)||pinned&&['network_id','network_sha256','proj_parameter'].some(k=>pinned[k]!==network[k]))throw Error('Map parameter network mismatch');
    if(pinned&&JSON.stringify(pinned.net_offset_m)!==JSON.stringify(network.net_offset_m))throw Error('Map parameter offset mismatch');
  }
  if(data.traffic){
    if(!network||network.network_id!==data.traffic.payload.network_id||!/^([0-9a-f]{64})$/.test(network.network_sha256))throw Error('Map network identity mismatch');
    if(data.sceneManifest?.assets.network?.sha256&&data.sceneManifest.assets.network.sha256!==network.network_sha256)throw Error('Map network SHA mismatch');
  }else if(network||trafficRequest||routePlans)throw Error('No-pump replay has unexpected traffic input');
  if(trafficRequest&&JSON.stringify(trafficRequest.payload.network)!==JSON.stringify(network)){
    for(const k of ['network_id','network_sha256','proj_parameter'])if(trafficRequest.payload.network[k]!==network[k])throw Error('Executed traffic network mismatch');
    if(JSON.stringify(trafficRequest.payload.network.net_offset_m)!==JSON.stringify(network.net_offset_m))throw Error('Executed traffic offset mismatch');
  }
  const offset=network?.net_offset_m??[0,0];if(offset.length!==2)throw Error('Invalid network offset');offset.forEach(finite);
  const service=p=>[finite(p.x_m)-offset[0],finite(p.y_m)-offset[1]];
  const roadSource=data.roads?.length?data.roads.map(r=>({...r,points_m:r.points.map(p=>[finite(p[0])-offset[0],finite(p[1])-offset[1]])})):(display?.roads??[]).map(r=>({...r,points_m:r.points}));
  const roads=roadSource.map(r=>({edge_id:r.edge_id,points_m:r.points_m,road_class:r.road_class??'sumo_road'}));
  const roadMap=new Map(roads.map(r=>[r.edge_id,r]));if(roadMap.size!==roads.length)throw Error('Duplicate map road ID');
  const groups=groupTrajectories(data.samples??[]).map(g=>({...g,points:g.samples.map(p=>service(p.sumo_position_m))}));
  if(!data.traffic&&groups.length)throw Error('No-pump replay has vehicle samples');
  const routes=new Map((data.traffic?.payload.routes??[]).map(r=>[key(r.vehicle_id,r.route_id),r]));
  if(routes.size!==(data.traffic?.payload.routes??[]).length)throw Error('Duplicate route identity');
  const byGroup=new Map(groups.map(g=>[g.key,g]));
  for(const g of groups)if(!routes.has(g.key))throw Error('Trajectory does not belong to run routes');
  for(const [k,r] of routes){
    const rows=byGroup.get(k)?.samples??[];
    if(!r.reachable){if(rows.length)throw Error('Unreachable route has samples');continue;}
    finite(r.depart_s);finite(r.arrival_time_s);
    if(r.depart_s<0||r.arrival_time_s<r.depart_s)throw Error('Invalid route window');
    trajectory(rows,r);
  }
  if(routePlans){
    if(routePlans.format!=='route-plans-v1')throw Error('Unknown route plan format');
    for(const name of ['scenario_id','scenario_version','parameter_version','run_id','seed'])if(routePlans[name]!==s[name])throw Error('Route plan identity mismatch');
    if(routePlans.network_id!==network.network_id||routePlans.network_sha256!==network.network_sha256)throw Error('Route plan network mismatch');
    const previous=new Map();
    for(const e of routePlans.events){
      const k=key(e.vehicle_id,e.route_id);finite(e.event_time_s);
      if(e.event_time_s<0||!routes.has(k)||!['planned','replanned'].includes(e.kind)||!e.edge_ids?.length||e.edge_ids.some(id=>!roadMap.has(id)))throw Error('Invalid route plan event');
      if(previous.has(k)&&e.event_time_s<previous.get(k))throw Error('Route plan event time reversed');previous.set(k,e.event_time_s);
    }
  }
  const tasks=scheduledTasks(data.resource??null,data.traffic??null);
  const kinds=[['arrival','vehicle_arrival_s'],['unload_complete','unload_complete_s'],['install_complete','install_complete_s'],['pump_start','pump_start_s'],['pump_stop','pump_stop_s']];
  const axis=s.payload.time_axis;finite(axis.reference_model_day);
  const taskEvents=tasks.flatMap(t=>kinds.map(([kind,name])=>({event_id:`${t.task_id}:${kind}`,task_id:t.task_id,well_id:t.well_id,vehicle_id:t.vehicle_id,kind,event_time_s:t[name],model_day:axis.reference_model_day+t[name]/86400})));
  const times=[0,...groups.flatMap(g=>[g.samples[0].event_time_s,g.samples.at(-1).event_time_s]),...taskEvents.map(e=>e.event_time_s)];
  const concentration=data.concentration??null;
  if(concentration){
    if(!concentration.times_day.length||!concentration.cell_ids.length||concentration.times_day.length!==concentration.C.length||concentration.cell_ids.length!==concentration.cell_xyz_m.length)throw Error('Map concentration dimensions');
    concentration.times_day.forEach((v,i)=>{finite(v);if(i&&v<=concentration.times_day[i-1])throw Error('Map concentration times');});
    for(const row of concentration.C){if(row.length!==concentration.cell_ids.length)throw Error('Map concentration dimensions');row.forEach(v=>{finite(v);if(v < -CONCENTRATION_NEGATIVE_TOLERANCE_MG_L)throw Error('Invalid concentration values');});}
    if(new Set(concentration.cell_ids).size!==concentration.cell_ids.length||concentration.cell_ids.some(id=>!Number.isSafeInteger(id)||id<0))throw Error('Invalid concentration cell IDs');
    if(data.hydro&&(concentration.model_signature!==data.hydro.payload.model_signature||JSON.stringify(concentration.times_day)!==JSON.stringify(data.hydro.payload.times_day)))throw Error('Map Hydro sample identity mismatch');
    for(const p of concentration.cell_xyz_m){if(p.length!==3)throw Error('Map concentration position');p.forEach(finite);}
  }
  const placement=display?.coordinate_frame==='synthetic_local_m'?display?.hydro_placement:null;
  if(placement&&(placement.view_only!==true||placement.synthetic!==true||placement.translation_m?.length!==2))throw Error('Invalid synthetic placement');
  if(placement){finite(placement.rotation_rad);placement.translation_m.forEach(finite);}
  const identity={scenario_id:s.scenario_id,scenario_version:s.scenario_version,run_id:s.run_id,parameter_version:s.parameter_version,seed:s.seed,manifest_sha256:manifestSHA,road_network_id:network?.network_id??null,network_sha256:network?.network_sha256??null};
  const physical=concentration?.times_day.map(d=>(d-axis.reference_model_day)*86400)??times;
  return {_dynamic:prepareDynamic(data,roads),identity,coordinate_frame:display?.coordinate_frame??(network?.proj_parameter==='!'?'synthetic_local_m':'wgs84_projected'),display,roads,bounds:{traffic_s:[0,Math.max(1,...times)],physical_s:[Math.min(...physical),Math.max(...physical)]},_data:data,_groups:groups,_roadMap:roadMap,_plans:routePlans,_request:trafficRequest,_tasks:tasks,_events:taskEvents,_placement:placement,_fixture:fixture};
}
export function mapFrame(model,scenario_time_s,{threshold_mg_L=1}={}){
  finite(scenario_time_s);finite(threshold_mg_L);if(threshold_mg_L<0)throw Error('Negative visual threshold');
  const {identity}=model,day=model._data.scenario.payload.time_axis.reference_model_day+scenario_time_s/86400;
  const vehicles=[],actual=[];
  for(const g of model._groups){
    const end=upper(g.samples,scenario_time_s);if(!end)continue;const i=end-1,a=g.samples[i],b=g.samples[i+1];
    let position=g.points[i],phase=scenario_time_s>a.event_time_s?(b?'interpolated':'held_last_recorded'):'sampled';
    if(b&&phase==='interpolated'){const f=(scenario_time_s-a.event_time_s)/(b.event_time_s-a.event_time_s);position=position.map((v,j)=>v+(g.points[i+1][j]-v)*f);}
    const p=g.points[Math.max(0,i-1)],q=g.points[b?i+1:i];
    vehicles.push({key:g.key,vehicle_id:g.vehicle_id,route_id:g.route_id,position_m:position,heading_rad:Math.atan2(q[1]-p[1],q[0]-p[0]),phase,source:model._fixture?'fixture':'actual_engine'});
    const trail=g.points.slice(0,end);if(phase==='interpolated')trail.push(position);
    actual.push({key:g.key,vehicle_id:g.vehicle_id,route_id:g.route_id,points_m:trail,edge_ids:[],event_time_s:scenario_time_s,source:model._fixture?'fixture':'actual_samples'});
  }
  const planned=[],replanned=[];const chosen=new Map();
  for(const e of model._plans?.events??[])if(e.event_time_s<=scenario_time_s)chosen.set(key(e.vehicle_id,e.route_id)+':'+e.kind,e);
  for(const e of chosen.values())e.edge_ids.forEach((id,i)=>{const road=model._roadMap.get(id);(e.kind==='planned'?planned:replanned).push({key:`${key(e.vehicle_id,e.route_id)}:${e.kind}:${i}`,vehicle_id:e.vehicle_id,route_id:e.route_id,points_m:road.points_m,edge_ids:[id],event_time_s:e.event_time_s,source:model._fixture?'fixture':'sumo_route_plan'});});
  const closures=(model._request?.payload.closures??[]).map(c=>({...c,active:c.start_s<=scenario_time_s&&scenario_time_s<c.stop_s,points_m:model._roadMap.get(c.edge_id)?.points_m??[]}));
  const positions=new Map((model.display?.task_locations??[]).map(t=>[t.task_id,[t.position_m.x_m,t.position_m.y_m]]));
  for(const entry of model._data.planning?.custom_tasks??[]){const well=model.display?.wells?.find(w=>w.well_id===entry.snapshot.data.well_id);if(well)positions.set(entry.snapshot.id,[well.position_m.x_m,well.position_m.y_m]);else positions.set(entry.snapshot.id,entry.record.access.position_m);}
  const taskStates=model._tasks.map(t=>({task_id:t.task_id,well_id:t.well_id,vehicle_id:t.vehicle_id,state:scenario_time_s<t.vehicle_arrival_s?'transport':scenario_time_s<t.unload_complete_s?'unloading':scenario_time_s<t.install_complete_s?'installing':scenario_time_s<t.pump_start_s?'waiting_pump':scenario_time_s<t.pump_stop_s?'pumping':'stopped',position_m:positions.get(t.task_id)??null}));
  const c=model._data.concentration;let concentration=null;
  if(c){const i=upper(c.times_day,day,v=>v)-1;if(i>=0){const p=model._placement;concentration={frame_index:i,sampled_model_day:c.times_day[i],sampled_scenario_time_s:(c.times_day[i]-model._data.scenario.payload.time_axis.reference_model_day)*86400,frame_kind:model._fixture?'fixture':'actual_npz',placement:p?'synthetic_view_only':'local_only',threshold_mg_L,threshold_kind:'visual_comparison_not_health_standard',cells:c.cell_ids.map((id,j)=>{const xyz=c.cell_xyz_m[j],value=c.C[i][j];return {cell_id:id,local_xyz_m:xyz,position_m:p?[Math.cos(p.rotation_rad)*xyz[0]-Math.sin(p.rotation_rad)*xyz[1]+p.translation_m[0],Math.sin(p.rotation_rad)*xyz[0]+Math.cos(p.rotation_rad)*xyz[1]+p.translation_m[1]]:null,concentration_mg_L:value,exceeds_threshold:value>=threshold_mg_L};})};}}
  const capability=(kind)=>model._plans?.events.some(e=>e.kind===kind)?{status:'PASS',reason:model._fixture?'Explicit fixture; not engine acceptance':'Verified SUMO route-plan artifact'}:unavailable('No recorded same-run SUMO route decision');
  return {...dynamicFrame(model._dynamic,scenario_time_s),identity,scenario_time_s,model_day:day,vehicles,paths:{planned,actual,replanned},closures,taskEvents:model._events,taskStates,concentration,capabilities:{planned_routes:capability('planned'),replanned_routes:capability('replanned'),congestion:model._dynamic?{status:'PASS',reason:'Same-run observed edges only; unknown edges grey'}:unavailable('No measured congestion series'),dynamic_speed_limits:unavailable('No time-varying speed-limit series'),live_vehicle_stream:unavailable('Backend WS reports job status only'),ordinary_traffic:backgroundCapability(model._dynamic)}};
}
export function assertComparable(a,b){
  for(const name of ['scenario_id','scenario_version','road_network_id','network_sha256'])if(a.identity[name]!==b.identity[name])throw Error('Incompatible map run comparison');
  if(a.identity.run_id===b.identity.run_id)throw Error('Comparison needs distinct run IDs');
  return {left_run_id:a.identity.run_id,right_run_id:b.identity.run_id};
}
