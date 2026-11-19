import scenarioExample from '../../../schemas/examples/scenario.json';
import trafficExample from '../../../schemas/examples/traffic_result.json';
import statusExample from '../../../schemas/examples/run_status.json';
import { DEMO_CRS, projectedToWgs } from './spatial';
import { envelope, trajectory } from './contract';
import { digest } from './artifacts';
export async function demo({twoVehicles=false}={}) {
  const scenario=structuredClone(scenarioExample), traffic=structuredClone(trafficExample), status=structuredClone(statusExample);
  // Explicit, invented registration near San Francisco for presentation only.
  scenario.payload.georeference={status:'synthetic_anchor', projected_crs:DEMO_CRS, anchor_wgs84:projectedToWgs(DEMO_CRS,[551000,4181000]), local_to_projected:{matrix_2x3:[[1,0,551000],[0,1,4181000]],z_offset_m:0,transform_version:'synthetic-display-v1',validation_artifact:null}};
  for (const m of [scenario,traffic,status]) { m.data_provenance.calibration='synthetic_anchor'; m.data_provenance.sources[0].reference='Hand-authored frontend fixture; invented positions near San Francisco; no SUMO/hydro execution or field registration'; }
  const network={network_id:'synthetic-grid',source_id:'demo',network_sha256:'0'.repeat(64),proj_parameter:DEMO_CRS,net_offset_m:[-550000,-4180000]};
  const points=[[0,700,700],[150,800,700],[300,800,1000],[450,1300,1000],[600,1300,1400]];
  const samples=points.map(([t,x,y])=>({vehicle_id:'truck-1',route_id:'r1',event_time_s:t,sumo_position_m:{x_m:x,y_m:y},wgs84:null}));
  if(twoVehicles){
    traffic.payload.routes.push({...traffic.payload.routes[0],task_id:'install-2',vehicle_id:'truck-2',route_id:'r2',depart_s:60});
    samples.push(...points.map(([t,x,y],i)=>({vehicle_id:'truck-2',route_id:'r2',event_time_s:i===4?600:t+60,sumo_position_m:{x_m:x+80,y_m:y-80},wgs84:null})));
    samples.sort((a,b)=>a.event_time_s-b.event_time_s);
  }
  const bytes=new TextEncoder().encode(JSON.stringify(samples));
  traffic.artifacts[0].sha256=await digest(bytes);
  envelope(scenario,'scenario',{fixture:true}); envelope(traffic,'traffic_result',{fixture:true});
  for(const route of traffic.payload.routes)trajectory(samples.filter(p=>p.vehicle_id===route.vehicle_id&&p.route_id===route.route_id),route);
  return {scenario,traffic,status,network,samples,depots:[{depot_id:'depot-1',synthetic:true,sumo_position_m:samples[0].sumo_position_m}], concentration:{times_day:[14600,14600+300/86400,14600+600/86400],C:[[0.2,0.8,1.4,0.5],[0.2,0.8,1.4,0.5],[0.2,0.8,1.4,0.5]],cell_xyz_m:[[200,300,210],[400,300,210],[200,500,210],[400,500,210]],cell_ids:[1,2,3,4],model_signature:'0'.repeat(64)}};
}
