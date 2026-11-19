import {validateCustomTask} from './customTasks';
import { envelope,sameRun } from './contract';
import { artifactBytes } from './artifacts';
import { loadReplay } from './replay';
import { parseRoadNetwork,scheduledTasks } from './roadView';

function canonical(value){return JSON.stringify(value===null||typeof value!=='object'?value:Array.isArray(value)?value.map(v=>JSON.parse(canonical(v))):Object.fromEntries(Object.keys(value).sort().map(k=>[k,JSON.parse(canonical(value[k]))])));}

// Read-only same-origin API: never submit runs or fall back to fixtures.
export async function loadApiReplay(runId,{fetcher=fetch,signal,expectedScene}={}){
  const rawFetch=fetcher;fetcher=(url,options={})=>rawFetch(url,{...options,signal});
  if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(runId))throw Error('Invalid run_id');
  const json=async path=>{const response=await fetcher(path);if(!response.ok)throw Error(`Replay HTTP ${response.status}`);return response.json();};
  const status=await json(`/api/v1/runs/${encodeURIComponent(runId)}`);
  envelope(status,'run_status');
  if(status.run_id!==runId||status.status!=='succeeded')throw Error(`Replay run is not succeeded: ${status.status}`);
  if(expectedScene&&(status.scenario_id!==expectedScene.scenario_id||status.scenario_version!==expectedScene.scenario_version))throw Error('Selected scene identity mismatch');
  let sceneManifest=null,sceneDisplay=null,sceneBinding=null;
  const managed=status.artifacts.some(a=>a.artifact_id==='scene-manifest');
  if(expectedScene&&!managed)throw Error('Missing scene binding artifacts');
  if(managed){
    const decode=async id=>JSON.parse(new TextDecoder().decode(await artifactBytes(status,id,fetcher)));
    sceneManifest=await decode('scene-manifest');sceneDisplay=await decode('scene-display');sceneBinding=await decode('scene-binding');
    for(const key of ['scenario_id','scenario_version','seed'])if(sceneManifest[key]!==status[key])throw Error('Scene manifest identity mismatch');
    for(const key of ['scenario_id','scenario_version','parameter_version','seed','run_id'])if(sceneBinding[key]!==status[key])throw Error('Scene binding identity mismatch');
    const manifestSHA=status.artifacts.find(a=>a.artifact_id==='scene-manifest').sha256;
    if(sceneBinding.manifest_sha256!==manifestSHA||expectedScene?.manifest_sha256&&expectedScene.manifest_sha256!==manifestSHA)throw Error('Scene manifest SHA mismatch');
    if(sceneDisplay.scenario_id!==status.scenario_id||sceneDisplay.scenario_version!==status.scenario_version||sceneDisplay.display_version!=='dual-display-v1')throw Error('Scene display identity mismatch');
    if(sceneManifest.assets.display.sha256!==status.artifacts.find(a=>a.artifact_id==='scene-display').sha256)throw Error('Scene display binding mismatch');
  }
  const scenario=await json(`/api/v1/scenarios/${encodeURIComponent(status.scenario_id)}/versions/${status.scenario_version}`);
  envelope(scenario,'scenario');
  const params=await json(`/api/v1/parameters/${encodeURIComponent(status.parameter_version)}/versions/1`);
  if(params.id!==status.parameter_version||params.version!==1||params.data?.scenario?.id!==status.scenario_id||params.data.scenario.version!==status.scenario_version)throw Error('Parameter snapshot identity mismatch');
  if(managed){
    for(const [snapshot,current] of [[sceneBinding.parameters,params],[sceneBinding.scenario,scenario],[sceneBinding.manifest,sceneManifest],[sceneBinding.display,sceneDisplay]]){
      if(!snapshot||canonical(snapshot)!==canonical(current))throw Error('Published scene configuration snapshot mismatch');
    }
  }
  // Immutable scenario payload stays byte-equivalent; execution identity is run-specific.
  for(const key of ['run_id','parameter_version','seed'])scenario[key]=status[key];
  sameRun(scenario,status);
  const result=async(name,required)=>{
    const id=`${name}-result`,exists=status.artifacts.some(a=>a.artifact_id===id);
    if(!exists){if(required)throw Error(`Missing ${name} result`);return null;}
    const message=JSON.parse(new TextDecoder().decode(await artifactBytes(status,id,fetcher)));
    envelope(message,name==='resources'?'resource_result':`${name}_result`);sameRun(status,message);
    if(message.status!=='succeeded')throw Error(`${name} result is not succeeded`);
    return message;
  };
  const traffic=await result('traffic',!!params.data.traffic),hydro=await result('hydro',true),resource=await result('resources',!!params.data.resources);
  let planning=null;
  if(sceneBinding?.planning){
    const decoded=JSON.parse(new TextDecoder().decode(await artifactBytes(status,'scene-context',fetcher)));
    planning=JSON.parse(new TextDecoder().decode(await artifactBytes(status,'scene-planning',fetcher)));
    if(canonical(planning)!==canonical(sceneBinding.planning)||canonical(decoded)!==canonical(planning.context))throw Error('Published planning snapshot mismatch');
    const refs=sceneManifest.sources.filter(s=>s.source_id==='dispatch-context-v1');
    if(refs.length!==1||!refs[0].reference.endsWith('#sha256='+planning.context_sha256)||status.artifacts.find(a=>a.artifact_id==='scene-context').sha256!==planning.context_sha256)throw Error('Planning context SHA mismatch');
    for(const k of ['scenario_id','scenario_version','seed'])if(decoded[k]!==status[k])throw Error('Planning context identity mismatch');
    if(decoded.network_sha256!==sceneManifest.assets.network?.sha256||planning.configuration.manifest_sha256!==sceneBinding.manifest_sha256)throw Error('Planning network/manifest mismatch');
  }else if(sceneManifest?.sources?.some(s=>s.source_id==='dispatch-context-v1'))throw Error('Missing published planning binding');
  for(const entry of planning?.custom_tasks??[])await validateCustomTask(entry,{context:planning.context,context_sha256:planning.context_sha256,manifest_sha256:sceneBinding.manifest_sha256},params);
  if(planning){
    const actual=(planning.custom_tasks??[]).map(e=>e.record.task_ref).sort((a,b)=>a.id.localeCompare(b.id));
    const expected=[...(planning.configuration.custom_task_refs??[])].sort((a,b)=>a.id.localeCompare(b.id));
    if(canonical(actual)!==canonical(expected))throw Error('Selected custom task refs mismatch');
  }
  const network=params.data.traffic?.network??null;
  const optionalTrafficJSON=async id=>traffic?.artifacts.some(a=>a.artifact_id===id)
    ?JSON.parse(new TextDecoder().decode(await artifactBytes(traffic,id,fetcher))):null;
  const executedTrafficRequest=await optionalTrafficJSON('traffic-request');
  if(executedTrafficRequest){
    envelope(executedTrafficRequest,'traffic_request');sameRun(status,executedTrafficRequest);
    if(canonical(executedTrafficRequest.payload.network)!==canonical(network))throw Error('Executed traffic network mismatch');
  }
  const routePlans=await optionalTrafficJSON('traffic-route-plans');
  if(routePlans){
    if(routePlans.format!=='route-plans-v1')throw Error('Unsupported route plan format');
    for(const key of ['scenario_id','scenario_version','parameter_version','run_id','seed'])if(routePlans[key]!==status[key])throw Error('Route plan identity mismatch');
    if(!network||routePlans.network_id!==network.network_id||routePlans.network_sha256!==network.network_sha256)throw Error('Route plan network mismatch');
  }
  let dynamic=null;
  if(planning?.dynamic_config){
    const ref=planning.dynamic_config_ref;
    const policy=JSON.parse(new TextDecoder().decode(await artifactBytes(status,'scene-dynamic-config',fetcher)));
    if(!ref||status.artifacts.find(a=>a.artifact_id==='scene-dynamic-config')?.sha256!==ref.sha256||planning.configuration.dynamic_config_sha256!==ref.sha256||canonical(policy)!==canonical(planning.dynamic_config))throw Error('Dynamic policy SHA/binding mismatch');
    const decode=async id=>JSON.parse(new TextDecoder().decode(await artifactBytes(traffic,id,fetcher)));
    dynamic={state:await decode('traffic-traffic-state'),background:await decode('traffic-background-trajectories'),decisions:await decode('traffic-routing-decisions')};
    if(traffic.artifacts.some(a=>a.artifact_id==='traffic-dynamic-config')&&canonical(await decode('traffic-dynamic-config'))!==canonical(policy))throw Error('Executed dynamic policy mismatch');
  }else if(traffic?.artifacts.some(a=>a.artifact_id==='traffic-traffic-state'))throw Error('Unexpected unbound dynamic artifacts');
  const replay=await loadReplay({scenario,traffic,network,hydro,status},{fixture:false,fetcher});
  const candidates=traffic?.artifacts.filter(a=>a.path.endsWith('/network.net.xml')||a.path==='network.net.xml')??[];
  if(candidates.length>1)throw Error('Ambiguous road network artifact');
  let roads=[];
  if(candidates.length){
    const item=candidates[0];
    if(item.sha256!==network.network_sha256)throw Error('Network SHA differs from pinned parameter');
    roads=parseRoadNetwork(new TextDecoder().decode(await artifactBytes(traffic,item.artifact_id,fetcher)),network);
  }
  if(signal?.aborted)throw new DOMException('Aborted','AbortError');
  return {...replay,dynamic,planning,sceneManifest,sceneDisplay,sceneBinding,executedTrafficRequest,routePlans,hydro,resource,tasks:scheduledTasks(resource,traffic),roads,depots:[],parameters:params,source:'api'};
}
