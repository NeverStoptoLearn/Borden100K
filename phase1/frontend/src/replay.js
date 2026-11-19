import { envelope, sameRun, trajectory } from './contract';
import { checkGeoref, sumoToWgs } from './spatial';
import { artifactBytes, concentrationNPZ } from './artifacts';
// Future backend seam. No POST, engine execution, NPZ conversion service or API integration here.
// Depot positions are view metadata (not extra fields added to a frozen envelope).
export async function loadReplay({scenario,traffic,network,hydro=null,status=null}, {fixture=false,fetcher=fetch}={}) {
  envelope(scenario,'scenario',{fixture});checkGeoref(scenario);
  if(traffic){
    envelope(traffic,'traffic_result',{fixture});sameRun(scenario,traffic);
    if(network?.network_id!==traffic.payload.network_id)throw Error('Network identity mismatch');
  }else if(!hydro)throw Error('Replay requires traffic or hydro');
  if(status){envelope(status,'run_status',{fixture});sameRun(scenario,status);}
  const routes=new Set(), samples=[];
  for(const r of traffic?.payload.routes??[]){
    if(routes.has(r.route_id))throw Error('Duplicate route ID');routes.add(r.route_id);
    if(!r.reachable)continue;
    if(r.arrival_time_s<r.depart_s)throw Error('Arrival before departure');
    const a=traffic.artifacts.find(a=>a.artifact_id===r.trajectory_artifact_id);
    if(a?.role!=='trajectory')throw Error('Trajectory artifact role mismatch');
    const data=JSON.parse(new TextDecoder().decode(await artifactBytes(traffic,r.trajectory_artifact_id,fetcher)));
    const vehicleSamples=data.filter(s=>s.route_id===r.route_id&&s.vehicle_id===r.vehicle_id);
    trajectory(vehicleSamples,r);
    // Never derive coordinates from ! networks. Upstream WGS84, when present, must agree.
    if(network.proj_parameter!=='!') for(const s of vehicleSamples){
      const w=sumoToWgs(network,s.sumo_position_m);
      if(s.wgs84&&(Math.abs(w.lon-s.wgs84.lon)>1e-5||Math.abs(w.lat-s.wgs84.lat)>1e-5))throw Error('Trajectory WGS84 / projection mismatch');
    }
    samples.push(...vehicleSamples);
  }
  let concentration=null;
  if(hydro){
    envelope(hydro,'hydro_result',{fixture});sameRun(scenario,hydro);
    if(hydro.payload.risk_window_stop_day>scenario.payload.time_axis.model_end_day || hydro.payload.risk_window_start_day>=hydro.payload.risk_window_stop_day)throw Error('Invalid risk window');
    if(hydro.artifacts.find(a=>a.artifact_id===hydro.payload.concentration_artifact_id)?.role!=='concentration')throw Error('Concentration artifact role mismatch');
    concentration=concentrationNPZ(await artifactBytes(hydro,hydro.payload.concentration_artifact_id,fetcher),hydro);
  }
  return {scenario,traffic,network,status,samples,concentration};
}
