import {describe,it,expect} from 'vitest';
import example from '../../../schemas/examples/scenario.json';
import {prepareMapReplay,mapFrame,assertComparable} from '../src/mapReplay';
import {createScenarioClock} from '../src/mapClock';
function fixture(){
 const scenario=structuredClone(example),network={network_id:'net',network_sha256:'a'.repeat(64),proj_parameter:'!',net_offset_m:[100,200]};
 const task={task_id:'t1',well_id:'well-1',vehicle_id:'v1',vehicle_arrival_s:11,unload_complete_s:12,install_complete_s:13,pump_start_s:14,pump_stop_s:20};
 return {source:'fixture',scenario,network,traffic:{...scenario,payload:{network_id:'net',routes:[{vehicle_id:'v1',route_id:'r1',task_id:'t1',reachable:true,depart_s:0,arrival_time_s:11}]}},resource:{...scenario,payload:{feasible:true,violations:[],tasks:[task]}},samples:[{vehicle_id:'v1',route_id:'r1',event_time_s:1,sumo_position_m:{x_m:100,y_m:200},wgs84:null},{vehicle_id:'v1',route_id:'r1',event_time_s:11,sumo_position_m:{x_m:110,y_m:200},wgs84:null}],roads:[{edge_id:'e',points:[[100,200],[110,200]]}],concentration:{times_day:[14600,14600+10/86400],C:[[1,-1e-8],[2,0]],cell_ids:[1,2],cell_xyz_m:[[1,2,3],[4,5,6]]},sceneDisplay:{scenario_id:scenario.scenario_id,scenario_version:scenario.scenario_version,coordinate_frame:'synthetic_local_m',roads:[],task_locations:[{task_id:'t1',position_m:{x_m:101,y_m:202}}],hydro_placement:{view_only:true,synthetic:true,rotation_rad:0,translation_m:[100,200]}}};
}
const model=()=>prepareMapReplay(fixture(),{allowFixture:true});
describe('Map replay',()=>{
 it('requires explicit fixture opt-in',()=>expect(()=>prepareMapReplay(fixture())).toThrow('explicit fixture'));
 it('does not invent endpoints and applies offset once',()=>{const m=model();expect(m.roads[0].points_m).toEqual([[0,0],[10,0]]);expect(mapFrame(m,0).vehicles).toEqual([]);expect(mapFrame(m,6).vehicles[0]).toMatchObject({position_m:[5,0],phase:'interpolated'});expect(mapFrame(m,12).vehicles[0]).toMatchObject({position_m:[10,0],phase:'held_last_recorded'});});
 it('isolates interleaved vehicle routes',()=>{const d=fixture();d.traffic.payload.routes.push({vehicle_id:'v2',route_id:'r2',reachable:true,depart_s:0,arrival_time_s:2});d.samples.splice(1,0,{vehicle_id:'v2',route_id:'r2',event_time_s:2,sumo_position_m:{x_m:200,y_m:200},wgs84:null});const m=prepareMapReplay(d,{allowFixture:true});expect(mapFrame(m,6).vehicles.map(v=>v.position_m)).toEqual([[5,0],[100,0]]);});
 it('uses exact task boundaries and includes pump stop',()=>{const m=model();for(const [t,state] of [[10,'transport'],[11,'unloading'],[12,'installing'],[13,'waiting_pump'],[14,'pumping'],[20,'stopped']])expect(mapFrame(m,t).taskStates[0].state).toBe(state);expect(m.bounds.traffic_s[1]).toBe(20);expect(mapFrame(m,1).taskEvents).toHaveLength(5);});
 it('preserves raw C, previous true frame and seconds/day boundary',()=>{const m=model();expect(mapFrame(m,9).concentration.frame_index).toBe(0);expect(mapFrame(m,9).concentration.cells[1].concentration_mg_L).toBe(-1e-8);expect(mapFrame(m,10).concentration.frame_index).toBe(1);expect(mapFrame(m,10).model_day).toBe(14600+10/86400);expect(mapFrame(m,10).concentration.cells[0].position_m).toEqual([101,202]);expect(mapFrame(m,10).concentration.cells[0].local_xyz_m).toEqual([1,2,3]);});
 it('keeps Borden hydro local and does not invent missing features',()=>{const d=fixture();d.sceneDisplay.coordinate_frame='wgs84_projected';const f=mapFrame(prepareMapReplay(d,{allowFixture:true}),6);expect(f.concentration.placement).toBe('local_only');expect(f.concentration.cells[0].position_m).toBeNull();expect(f.paths.planned).toEqual([]);expect(f.capabilities.replanned_routes.status).toBe('NOT_RUN');});
 it('validates same-run plans and applies replan time',()=>{const d=fixture();d.routePlans={format:'route-plans-v1',...Object.fromEntries(['scenario_id','scenario_version','parameter_version','run_id','seed'].map(k=>[k,d.scenario[k]])),network_id:'net',network_sha256:'a'.repeat(64),events:[{vehicle_id:'v1',route_id:'r1',event_time_s:0,kind:'planned',edge_ids:['e']},{vehicle_id:'v1',route_id:'r1',event_time_s:5,kind:'replanned',edge_ids:['e']}]};const m=prepareMapReplay(d,{allowFixture:true});expect(mapFrame(m,4).paths.replanned).toHaveLength(0);expect(mapFrame(m,5).paths.replanned).toHaveLength(1);d.routePlans.network_sha256='b'.repeat(64);expect(()=>prepareMapReplay(d,{allowFixture:true})).toThrow('network mismatch');});
 it('uses executed closures half-open boundaries',()=>{const m=model();m._request={payload:{closures:[{edge_id:'e',start_s:5,stop_s:10}]}};expect(mapFrame(m,5).closures[0].active).toBe(true);expect(mapFrame(m,10).closures[0].active).toBe(false);});
 it('compares explicit two run identities only on equal scene/version/network',()=>{const a=model(),b=model();b.identity={...b.identity,run_id:'another'};expect(assertComparable(a,b).right_run_id).toBe('another');b.identity.scenario_version++;expect(()=>assertComparable(a,b)).toThrow('Incompatible');});
 it('accepts no-pump null network without vehicles or tasks',()=>{const d=fixture();Object.assign(d,{network:null,traffic:null,resource:null,samples:[],roads:[]});const m=prepareMapReplay(d,{allowFixture:true});expect(m.identity.network_sha256).toBeNull();expect(mapFrame(m,1).vehicles).toEqual([]);expect(mapFrame(m,1).taskEvents).toEqual([]);});
});
describe('Single wall-clock',()=>{
 it('uses elapsed wall seconds and common speed, clears pause/resume gap',()=>{const c=createScenarioClock({max_s:100,speed:2});c.play(1000);expect(c.tick(2500).scenario_time_s).toBe(3);c.pause();c.tick(20000);c.play(30000);expect(c.tick(31000).scenario_time_s).toBe(5);});
 it('clears seek and speed accumulators, stops end, disposes',()=>{const c=createScenarioClock({max_s:10});c.play(0);c.tick(1000);c.seek(5);expect(c.tick(9000).scenario_time_s).toBe(5);c.setSpeed(2);c.tick(10000);expect(c.tick(13000)).toMatchObject({scenario_time_s:10,playing:false});c.dispose();expect(()=>c.play()).toThrow('disposed');});
 it('rejects invalid speed/seek and permits explicitly historical signed range',()=>{expect(()=>createScenarioClock({speed:0})).toThrow();const c=createScenarioClock({min_s:-86400,max_s:86400,initial_s:0});expect(c.seek(-86400).scenario_time_s).toBe(-86400);expect(()=>c.seek(-86401)).toThrow();});
});
import {readFileSync} from 'node:fs';
import {dirname,join} from 'node:path';
import {concentrationNPZ} from '../src/artifacts';
if(process.env.BORDEN_MAP_REAL_REPORT)it('reads actual stored NPZ samples without moving model days or altering cells',()=>{
 const reportPath=process.env.BORDEN_MAP_REAL_REPORT,root=dirname(reportPath),report=JSON.parse(readFileSync(reportPath,'utf8'));
 for(const item of report.cases){
  const base=join(root,'home','runs',item.run_id),status=JSON.parse(readFileSync(join(root,item.scenario_id+'-'+item.mode,'status.json'),'utf8'));
  const loaded=JSON.parse(readFileSync(join(root,item.scenario_id+'-loaded.json'),'utf8'));
  const scenario={...loaded.scenario};for(const k of ['run_id','parameter_version','seed'])scenario[k]=status[k];
  const hydro=JSON.parse(readFileSync(join(base,'published/hydro/result.json'),'utf8'));
  const concentration=concentrationNPZ(new Uint8Array(readFileSync(join(root,item.scenario_id+'-'+item.mode,'concentration.npz'))),hydro);
  const traffic=item.mode==='no_pump'?null:JSON.parse(readFileSync(join(base,'published/traffic/result.json'),'utf8'));
  const resource=item.mode==='no_pump'?null:JSON.parse(readFileSync(join(base,'published/resources/result.json'),'utf8'));
  const network=loaded.parameters[item.mode].data.traffic?.network??null;
  const samples=traffic?.payload.routes.flatMap(r=>JSON.parse(readFileSync(join(base,traffic.artifacts.find(a=>a.artifact_id===r.trajectory_artifact_id).path),'utf8')))??[];
  const data={source:'api',scenario,status,hydro,traffic,resource,network,samples,sceneDisplay:loaded.display,concentration};
  const m=prepareMapReplay(data);for(let i=0;i<concentration.times_day.length;i++){
   const t=(concentration.times_day[i]-scenario.payload.time_axis.reference_model_day)*86400;
   const frame=mapFrame(m,t);expect(frame.concentration.frame_index).toBe(i);expect(frame.concentration.cells.map(c=>c.concentration_mg_L)).toEqual(concentration.C[i]);expect(frame.concentration.cells.map(c=>c.local_xyz_m)).toEqual(concentration.cell_xyz_m);
  }
  expect(m.bounds.physical_s[1]).toBe(630720000);expect(mapFrame(m,0).concentration.sampled_model_day).toBe(14600);
 }
});

describe('Adapter input boundary rejects corruption rather than trusting caller flags',()=>{
 it('rejects unreachable, before-departure, after-arrival and empty reachable traces',()=>{
  for(const mutate of [d=>d.traffic.payload.routes[0].reachable=false,d=>d.traffic.payload.routes[0].depart_s=2,d=>d.traffic.payload.routes[0].arrival_time_s=10,d=>d.samples=[]]){
   const d=fixture();mutate(d);expect(()=>prepareMapReplay(d,{allowFixture:true})).toThrow();
  }
 });
 it('rejects duplicate cells, invalid IDs and concentration below loader tolerance',()=>{
  for(const mutate of [d=>d.concentration.cell_ids=[1,1],d=>d.concentration.cell_ids=[1,1.5],d=>d.concentration.C[0][0]=-2e-7,d=>d.concentration.C[0][0]=NaN]){
   const d=fixture();mutate(d);expect(()=>prepareMapReplay(d,{allowFixture:true})).toThrow();
  }
  const d=fixture();d.concentration.C[0][0]=-1e-7;expect(mapFrame(prepareMapReplay(d,{allowFixture:true}),0).concentration.cells[0].concentration_mg_L).toBe(-1e-7);
 });
 it('rejects wrong parameter snapshot and preserves explicit historic physical bounds',()=>{
  const d=fixture();d.parameters={id:'wrong',version:1,data:{scenario:{id:d.scenario.scenario_id,version:1}}};expect(()=>prepareMapReplay(d,{allowFixture:true})).toThrow('parameter snapshot');
  delete d.parameters;d.concentration.times_day[0]=0;const m=prepareMapReplay(d,{allowFixture:true});expect(m.bounds.traffic_s[0]).toBe(0);expect(m.bounds.physical_s[0]).toBe(-14600*86400);
 });
});

describe('selected custom task position (explicit unit fixture)',()=>{it('uses bound run task/well, not current GET metadata',()=>{const d=fixture();d.planning={custom_tasks:[{snapshot:{id:'t1',data:{well_id:'well-1'}},record:{access:{position_m:[900,900]}}}]};d.sceneDisplay.wells=[{well_id:'well-1',position_m:{x_m:120,y_m:240}}];const m=prepareMapReplay(d,{allowFixture:true});expect(mapFrame(m,14).taskStates[0].position_m).toEqual([120,240]);});});
