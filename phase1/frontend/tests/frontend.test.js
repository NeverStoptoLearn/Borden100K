import { describe,it,expect,vi } from 'vitest';
import proj4 from 'proj4';
import { zipSync } from 'fflate';
import { demo } from '../src/demo';
import { envelope,sameRun,trajectory,statusUpdate } from '../src/contract';
import { checkGeoref,localToWgs,sumoToWgs,serviceBoundary,atTime,groupTrajectories,replayGroups,modelDay,DEMO_CRS } from '../src/spatial';
import { artifactBytes,concentrationNPZ,concentrationFrame,digest } from '../src/artifacts';
import { loadGoogle,createScene } from '../src/google3d';
import statusExample from '../../../schemas/examples/run_status.json';
import { loadReplay } from '../src/replay';
import { loadApiReplay } from '../src/apiReplay';
import { loopbackProxy } from '../vite.config';
import hydroExample from '../../../schemas/examples/hydro_result.json';

describe('contract and time',()=>{
  it('validates the synthetic demo against frozen schema',async()=>{const d=await demo();expect(envelope(d.scenario,'scenario',{fixture:true})).toBeTruthy();expect(()=>envelope(d.traffic,'traffic_result')).toThrow('Fixture');});
  it('rejects unknown fields, bad UUID, nonfinite and identity mismatch',async()=>{const d=await demo();expect(()=>envelope({...d.scenario,extra:1},'scenario',{fixture:true})).toThrow();expect(()=>envelope({...d.scenario,run_id:'bad'},'scenario',{fixture:true})).toThrow();expect(()=>envelope({...d.scenario,seed:Infinity},'scenario',{fixture:true})).toThrow();expect(()=>sameRun(d.scenario,{...d.traffic,scenario_version:2})).toThrow();});
  it('keeps day reference and interpolates vehicle at scenario seconds',async()=>{const d=await demo();expect(modelDay(d.scenario.payload.time_axis,86400)).toBe(14601);expect(atTime(d.samples,-1)).toBeNull();expect(atTime(d.samples,75).sumo_position_m.x_m).toBe(750);expect(atTime(d.samples,900).sumo_position_m.y_m).toBe(1400);});
  it('rejects unordered trajectory',async()=>{const d=await demo();expect(()=>trajectory([...d.samples].reverse(),d.traffic.payload.routes[0])).toThrow();});
  it('never promotes failed/cancelled runs or skips queued→running',()=>{const p=structuredClone(statusExample);p.status='queued';const n=structuredClone(p);n.status='succeeded';n.payload.previous_status='queued';expect(()=>statusUpdate(p,n,{fixture:true})).toThrow();p.status='failed';expect(()=>statusUpdate(p,n,{fixture:true})).toThrow();p.status='cancelled';expect(()=>statusUpdate(p,n,{fixture:true})).toThrow();});
});
describe('multi-vehicle replay (synthetic samples)',()=>{
  const point=(vehicle_id,route_id,event_time_s,x_m)=>({vehicle_id,route_id,event_time_s,sumo_position_m:{x_m,y_m:0},wgs84:null});
  it('interpolates each interleaved vehicle independently, never across cars',()=>{
    const samples=[point('a','r1',0,0),point('b','r2',5,1000),point('a','r1',10,10),point('b','r2',15,1100)];
    const groups=groupTrajectories(samples),at7=replayGroups(groups,7);
    expect(at7.map(g=>[g.position.vehicle_id,g.position.sumo_position_m.x_m])).toEqual([['a',7],['b',1020]]);
    expect(at7.map(g=>g.trail.map(p=>p.vehicle_id))).toEqual([['a','a'],['b','b']]);
    expect(samples).toHaveLength(4);
  });
  it('separates same-vehicle routes and defines empty/before/after sample policy',()=>{
    const groups=groupTrajectories([point('a','outbound',10,1),point('a','return',30,90)]);
    expect(groupTrajectories([])).toEqual([]);expect(replayGroups([],5)).toEqual([]);
    expect(replayGroups(groups,5).map(g=>g.position)).toEqual([null,null]);
    expect(replayGroups(groups,20).map(g=>g.position?.sumo_position_m.x_m??null)).toEqual([1,null]);
    expect(replayGroups(groups,40).map(g=>g.position.sumo_position_m.x_m)).toEqual([1,90]);
    expect(replayGroups(groups,40).map(g=>g.trail.length)).toEqual([1,1]);
  });
  it('rejects duplicate, reversed and nonfinite group timestamps instead of silently sorting',()=>{
    for(const t of [10,9,NaN])expect(()=>groupTrajectories([point('a','r1',10,0),point('b','r2',0,100),point('a','r1',t,10)])).toThrow();
    expect(()=>replayGroups([],NaN)).toThrow();
  });
  it('keeps original single-car demo and validates opt-in two-car fixture',async()=>{
    expect(groupTrajectories((await demo()).samples)).toHaveLength(1);
    const d=await demo({twoVehicles:true});expect(groupTrajectories(d.samples)).toHaveLength(2);
    expect(envelope(d.traffic,'traffic_result',{fixture:true})).toBeTruthy();
    const r=await loadReplay({scenario:d.scenario,traffic:d.traffic,network:d.network},{fixture:true,fetcher:async()=>new Response(JSON.stringify(d.samples))});
    expect(groupTrajectories(r.samples).map(g=>g.vehicle_id)).toEqual(['truck-1','truck-2']);
  });
});
describe('three coordinate spaces',()=>{
  it('applies local affine, subtracts SUMO offset and checks round trip',async()=>{const d=await demo();const w=localToWgs(d.scenario,{x_local_m:300,y_local_m:400});const s=sumoToWgs(d.network,{x_m:1300,y_m:1400});expect(s.lon).toBeCloseTo(w.lon,8);expect(s.lat).toBeCloseTo(w.lat,8);const xy=proj4('WGS84',DEMO_CRS,[w.lon,w.lat]);expect(xy[0]).toBeCloseTo(551300,4);expect(xy[1]).toBeCloseTo(4181400,4);});
  it('creates a 100000 m square in projected space',async()=>{const d=await demo();const b=serviceBoundary(d.scenario).map(p=>proj4('WGS84',DEMO_CRS,[p.lon,p.lat]));expect(b[1][0]-b[0][0]).toBeCloseTo(100000,3);expect(b[2][1]-b[1][1]).toBeCloseTo(100000,3);});
  it('blocks uncalibrated geographic overlay and ! SUMO conversion',async()=>{const d=await demo();d.scenario.payload.georeference={status:'uncalibrated',projected_crs:null,local_to_projected:null,anchor_wgs84:null};expect(localToWgs(d.scenario,{})).toBeNull();expect(serviceBoundary(d.scenario)).toEqual([]);expect(()=>sumoToWgs({...d.network,proj_parameter:'!'},{x_m:0,y_m:0})).toThrow();});
  it('rejects incomplete calibrated metadata and inconsistent synthetic labels',async()=>{const d=await demo();d.scenario.payload.georeference.status='calibrated';expect(()=>checkGeoref(d.scenario)).toThrow();d.scenario.payload.georeference.status='synthetic_anchor';d.scenario.data_provenance.calibration='none';expect(()=>checkGeoref(d.scenario)).toThrow();});
});
function npy(data,shape,dtype='<f8') { const header=`{'descr': '${dtype}', 'fortran_order': False, 'shape': (${shape.join(',')},), }\n`;const text=new TextEncoder().encode(header);const b=new Uint8Array(10+text.length+data.length*8);b.set([147,78,85,77,80,89,1,0]);new DataView(b.buffer).setUint16(8,text.length,true);b.set(text,10);const v=new DataView(b.buffer);data.forEach((n,i)=>dtype==='<f8'?v.setFloat64(10+text.length+i*8,n,true):v.setBigInt64(10+text.length+i*8,BigInt(n),true));return b; }
function npz(values=[1,2]){return zipSync({'times_day.npy':npy([14600,14601],[2]),'C.npy':npy(values,[2,1]),'cell_xyz_m.npy':npy([3,4,5],[1,3]),'cell_ids.npy':npy([9],[1],'<i8')});}
describe('artifacts and NPZ',()=>{
  it('verifies sha256 and uses ID endpoints',async()=>{const d=await demo();const bytes=new TextEncoder().encode('[]');d.traffic.artifacts[0].sha256=await digest(bytes);const f=vi.fn(async()=>new Response(bytes));await artifactBytes(d.traffic,'trajectory-1',f);expect(f.mock.calls[0][0]).toContain('/artifacts/trajectory-1');d.traffic.artifacts[0].sha256='0'.repeat(64);await expect(artifactBytes(d.traffic,'trajectory-1',f)).rejects.toThrow('SHA256');});
  it('rejects traversal before fetch',async()=>{const d=await demo();d.traffic.artifacts[0].path='../secret';const f=vi.fn();await expect(artifactBytes(d.traffic,'trajectory-1',f)).rejects.toThrow();expect(f).not.toHaveBeenCalled();});
  it('parses real zipped NPY arrays and selects previous sampled model day',()=>{const d=concentrationNPZ(npz(),{payload:{times_day:[14600,14601],model_signature:'test'}});expect(d.cell_ids).toEqual([9]);expect(concentrationFrame(d,14600.5).values).toEqual([1]);expect(concentrationFrame(d,14599)).toBeNull();});
  it('rejects negative and nonfinite concentration and metadata disagreement',()=>{expect(()=>concentrationNPZ(npz([-1,2]),{payload:{times_day:[14600,14601]}})).toThrow();expect(()=>concentrationNPZ(npz([NaN,2]),{payload:{times_day:[14600,14601]}})).toThrow();expect(()=>concentrationNPZ(npz(),{payload:{times_day:[0,1]}})).toThrow();});
});
describe('read-only actual API replay',()=>{
  async function setup(){
    const d=await demo(),status=structuredClone(d.status),scenario=structuredClone(d.scenario),hydro=structuredClone(hydroExample);
    for(const value of [status,scenario,hydro])value.data_provenance.fixture=false;
    status.status='succeeded';status.payload.previous_status='running';status.payload.progress_fraction=1;status.error=null;
    for(const k of ['run_id','scenario_id','scenario_version','experiment_id','parameter_version','seed'])hydro[k]=status[k];
    hydro.payload.times_day=[14600,14601];hydro.artifacts[0].sha256=await digest(npz());
    const raw=new TextEncoder().encode(JSON.stringify(hydro));
    status.artifacts=[{artifact_id:'hydro-result',path:'published/hydro/result.json',role:'summary',media_type:'application/json',units:'see units',sha256:await digest(raw)}];
    const fetcher=vi.fn(async path=>{
      if(path.endsWith('/artifacts/hydro-result'))return new Response(raw);
      if(path.includes('/artifacts/'))return new Response(npz());
      if(path.startsWith('/api/v1/runs/'))return Response.json(status);
      if(path.startsWith('/api/v1/scenarios/'))return Response.json(scenario);
      return Response.json({id:status.parameter_version,version:1,data:{scenario:{id:status.scenario_id,version:status.scenario_version},traffic:null}});
    });
    return {status,scenario,fetcher};
  }
  it('loads hydro-only no-pump actual artifacts, binds execution identity and preserves payload',async()=>{
    const {status,scenario,fetcher}=await setup(),before=structuredClone(scenario.payload);
    scenario.run_id='00000000-0000-4000-8000-000000000001';scenario.parameter_version='old';scenario.seed=100;
    const r=await loadApiReplay(status.run_id,{fetcher});expect(r.source).toBe('api');expect(r.samples).toEqual([]);expect(r.concentration.C).toEqual([[1],[2]]);expect(r.scenario.payload).toEqual(before);expect(r.scenario.run_id).toBe(status.run_id);expect(r.scenario.seed).toBe(status.seed);
  });
  it('rejects failed/fixture runs and API errors without fixture fallback',async()=>{
    const {status,fetcher}=await setup();status.status='failed';status.error={code:'INVALID_INPUT',message:'failure',retryable:false};
    await expect(loadApiReplay(status.run_id,{fetcher})).rejects.toThrow('not succeeded');expect(fetcher).toHaveBeenCalledTimes(1);
    status.status='succeeded';status.error=null;status.data_provenance.fixture=true;
    await expect(loadApiReplay(status.run_id,{fetcher})).rejects.toThrow('Fixture');
    await expect(loadApiReplay(status.run_id,{fetcher:async()=>new Response('',{status:503})})).rejects.toThrow('HTTP 503');
  });
  it('requires selected scenario/version and immutable scene binding for managed replay',async()=>{
    const {status,fetcher}=await setup();
    await expect(loadApiReplay(status.run_id,{fetcher,expectedScene:{scenario_id:'other',scenario_version:status.scenario_version}})).rejects.toThrow('Selected scene identity');
    await expect(loadApiReplay(status.run_id,{fetcher,expectedScene:status})).rejects.toThrow('Missing scene binding');
  });
  it('loads published scene bytes and rejects cross-run binding or manifest SHA',async()=>{
    const {status,scenario,fetcher}=await setup(),raws={};
    const display={display_version:'dual-display-v1',scenario_id:status.scenario_id,scenario_version:status.scenario_version};
    async function add(id,value){const raw=new TextEncoder().encode(JSON.stringify(value));raws[id]=raw;const a={artifact_id:id,path:'published/'+id+'.json',sha256:await digest(raw),role:'config',media_type:'application/json',units:'see units'};const i=status.artifacts.findIndex(x=>x.artifact_id===id);if(i<0)status.artifacts.push(a);else status.artifacts[i]=a;return a.sha256;}
    const displaySHA=await add('scene-display',display);
    const manifest={scenario_id:status.scenario_id,scenario_version:status.scenario_version,seed:status.seed,assets:{display:{sha256:displaySHA}}};const manifestSHA=await add('scene-manifest',manifest);
    const binding={...Object.fromEntries(['scenario_id','scenario_version','parameter_version','seed','run_id'].map(k=>[k,status[k]])),manifest_sha256:manifestSHA,manifest,display,scenario:structuredClone(scenario),parameters:await (await fetcher('/api/v1/parameters/'+status.parameter_version+'/versions/1')).json()};await add('scene-binding',binding);
    const managedFetch=(url,opts)=>raws[url.split('/').at(-1)]?Promise.resolve(new Response(raws[url.split('/').at(-1)])):fetcher(url,opts);
    const r=await loadApiReplay(status.run_id,{fetcher:managedFetch,expectedScene:status});expect(r.sceneDisplay).toEqual(display);
    await expect(loadApiReplay(status.run_id,{fetcher:managedFetch,expectedScene:{...status,manifest_sha256:'0'.repeat(64)}})).rejects.toThrow('manifest SHA');
    await add('scene-binding',{...binding,parameters:{...binding.parameters,data:{...binding.parameters.data,risk_window_start_day:123}}});
    await expect(loadApiReplay(status.run_id,{fetcher:managedFetch,expectedScene:status})).rejects.toThrow('snapshot mismatch');
    await add('scene-binding',{...binding,run_id:'00000000-0000-4000-8000-000000000000'});
    await expect(loadApiReplay(status.run_id,{fetcher:managedFetch,expectedScene:status})).rejects.toThrow('binding identity');
  });
  it('rejects summary corruption and non-loopback proxy configuration',async()=>{
    const {status,fetcher}=await setup();status.artifacts[0].sha256='0'.repeat(64);
    await expect(loadApiReplay(status.run_id,{fetcher})).rejects.toThrow('SHA256');
    expect(loopbackProxy()).toBeUndefined();expect(loopbackProxy('http://127.0.0.1:5290')['/api'].target).toBe('http://127.0.0.1:5290');
    for(const target of ['https://example.com','http://user:pass@localhost:5290','http://localhost:5290/api'])expect(()=>loopbackProxy(target)).toThrow();
  });
});
describe('Google adapter (SDK doubles only)',()=>{
  it('makes no request without enabled credentials',async()=>{const doc={head:{append:vi.fn()}};await expect(loadGoogle({enabled:false,key:''},{},doc)).rejects.toThrow();expect(doc.head.append).not.toHaveBeenCalled();});
  it('constructs Google map and draws marker/line then disposes',()=>{class Element{constructor(options){this.options=options;}addEventListener(){}removeEventListener(){}append=vi.fn();remove=vi.fn();}const host={replaceChildren:vi.fn()};const s=createScene({Map3DElement:Element,Marker3DElement:Element,Polyline3DElement:Element},host,{lat:1,lon:2},()=>{},()=>{});s.draw({boundary:[{lat:1,lon:2},{lat:2,lon:3}],markers:[{lat:1,lon:2,label:'synthetic'}],paths:[]});const map=host.replaceChildren.mock.calls[0][0];expect(map.options.mode).toBe('SATELLITE');expect(map.append).toHaveBeenCalledTimes(2);s.dispose();expect(map.remove).toHaveBeenCalled();});
});

describe('frontend input seam (fixture fetch, no backend)',()=>{
  async function setup(){const d=await demo();return {d,input:{scenario:d.scenario,traffic:d.traffic,network:d.network,status:d.status},options:{fixture:true,fetcher:async()=>new Response(JSON.stringify(d.samples))}};}
  it('loads hashed contract trajectory for the same run',async()=>{const {d,input,options}=await setup();const r=await loadReplay(input,options);expect(r.samples).toEqual(d.samples);expect(r.concentration).toBeNull();});
  it('rejects different run/version and network',async()=>{const {input,options}=await setup();input.traffic.scenario_version=2;await expect(loadReplay(input,options)).rejects.toThrow('identity');input.traffic.scenario_version=1;input.network.network_id='wrong';await expect(loadReplay(input,options)).rejects.toThrow('Network');});
  it('rejects arrival before departure and duplicate routes',async()=>{const {input,options}=await setup();input.traffic.payload.routes[0].depart_s=700;await expect(loadReplay(input,options)).rejects.toThrow('Arrival');input.traffic.payload.routes[0].depart_s=0;input.traffic.payload.routes.push(input.traffic.payload.routes[0]);await expect(loadReplay(input,options)).rejects.toThrow('Duplicate');});
  it('rejects projection and WGS84 disagreement',async()=>{const {d,input,options}=await setup();d.samples[0].wgs84={lon:0,lat:0};input.traffic.artifacts[0].sha256=await digest(new TextEncoder().encode(JSON.stringify(d.samples)));await expect(loadReplay(input,options)).rejects.toThrow('projection mismatch');});
});


describe('batch-one real producer compatibility',()=>{
  it('accepts bounded SUMO samples without fabricated departure/arrival endpoints',async()=>{
    const d=await demo(), route=d.traffic.payload.routes[0], sampled=d.samples.slice(1,-1);
    expect(trajectory(sampled,route)).toEqual(sampled);
    expect(()=>trajectory([],route)).toThrow('Empty reachable trajectory');
    expect(()=>trajectory([{...sampled[0],event_time_s:route.depart_s-1}],route)).toThrow();
    expect(()=>trajectory([{...sampled[0],event_time_s:route.arrival_time_s+1}],route)).toThrow();
  });
  it('loads a hashed trajectory whose endpoint times are not sampled',async()=>{
    const d=await demo(), samples=d.samples.slice(1,-1), bytes=new TextEncoder().encode(JSON.stringify(samples));
    d.traffic.artifacts[0].sha256=await digest(bytes);
    const r=await loadReplay({scenario:d.scenario,traffic:d.traffic,network:d.network,status:d.status},{fixture:true,fetcher:async()=>new Response(bytes)});
    expect(r.samples).toEqual(samples);
  });
  it('retains physical concentration roundoff without truncation and rejects beyond tolerance',()=>{
    const data=concentrationNPZ(npz([-1e-8,2]),{payload:{times_day:[14600,14601]}});
    expect(data.C[0][0]).toBe(-1e-8);
    expect(()=>concentrationNPZ(npz([-1.0001e-7,2]),{payload:{times_day:[14600,14601]}})).toThrow();
  });
  it('does not accept run_status in place of a successful hydro_result',async()=>{
    const d=await demo();
    await expect(loadReplay({scenario:d.scenario,traffic:d.traffic,network:d.network,hydro:d.status},{fixture:true,fetcher:async()=>new Response(JSON.stringify(d.samples))})).rejects.toThrow('Expected hydro_result');
  });
});
