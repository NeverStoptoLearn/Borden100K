export const SCENE_IDS=['borden_reference_100k','synthetic_multiterrain_100k'];
export function identity(value,expected){
  if(!value||value.scenario_id!==expected.scenario_id||value.scenario_version!==expected.scenario_version)throw Error('Scene identity mismatch');
}
export function selectedPackage(value,expected){
  if(value.catalog_version!=='dual-scenes-v1')throw Error('Unknown scene catalog');
  identity(value.scene,expected);identity(value.display,expected);
  if(value.scene.manifest_sha256!==expected.manifest_sha256)throw Error('Scene manifest changed');
  if(value.display.display_version!=='dual-display-v1')throw Error('Unknown scene display');
  if(value.manifest){identity(value.manifest,expected);if(value.manifest.seed!==value.scene.seed)throw Error('Scene seed mismatch');}
  if(value.scenario){identity(value.scenario,expected);if(value.scenario.seed!==value.scene.seed)throw Error('Scene seed mismatch');
    for(const [mode,p] of Object.entries(value.parameters)){
      if(p&&(p.version!==1||p.data.scenario.id!==expected.scenario_id||p.data.scenario.version!==expected.scenario_version))throw Error('Scene parameter identity mismatch');
      if(p&&!value.scene.availability[mode]?.allowed)throw Error('Blocked scene parameters');
    }
  }
  return value;
}
// Token checks also reject old responses from fetchers that ignore AbortSignal.
export function latestRequests(){let generation=0,controller;
 return {begin(){controller?.abort();controller=new AbortController();const current=++generation;return {signal:controller.signal,current:()=>current===generation&&!controller.signal.aborted};},cancel(){generation++;controller?.abort();}};
}
export async function requestJSON(url,{signal,body,fetcher=fetch,headers={}}={}){
 const response=await fetcher(url,{signal,...(body!==undefined?{method:'POST',headers:{'Content-Type':'application/json',...headers},body:JSON.stringify(body)}:{})});
 const value=await response.json();if(!response.ok)throw Error(value.error?.message??`HTTP ${response.status}`);return value;
}
