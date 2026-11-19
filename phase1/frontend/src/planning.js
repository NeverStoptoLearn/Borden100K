export function validatePlanning(value,scene){
 const c=value?.context;
 if(!c||c.format!=='scenario-context-v1'||value.manifest_sha256!==scene.manifest_sha256||!/^[a-f0-9]{64}$/.test(value.context_sha256))throw Error('Planning SHA/format mismatch');
 for(const k of ['scenario_id','scenario_version','seed'])if(c[k]!==scene[k])throw Error('Planning scene identity mismatch');
 if(!Array.isArray(value.resources?.tasks)||!c.facility_access)throw Error('Missing planning candidate resources');
 return value;
}
export function selectionBody(scene,{mode,selected,assignments,closure,risk='control',traffic='emergency_only'}){
 return {manifest_sha256:scene.manifest_sha256,mode,selected_task_ids:mode==='no_pump'?[]:[...selected],assignments:mode==='no_pump'?[]:selected.map(task_id=>({task_id,vehicle_id:assignments[task_id]??''})),closure_case_id:mode==='closure'?closure:null,risk_profile:risk,traffic_profile:mode==='no_pump'?'emergency_only':traffic};
}
