import {describe,it,expect} from 'vitest';
import {selectionBody,validatePlanning} from '../src/planning';
const scene={scenario_id:'borden_reference_100k',scenario_version:3,seed:42,manifest_sha256:'a'.repeat(64)};
describe('explicit planning boundary',()=>{
 it('preserves five selected tasks rather than quietly truncating to vehicles',()=>{const selected=['1','2','3','4','5'];const b=selectionBody(scene,{mode:'normal',selected,assignments:{}});expect(b.selected_task_ids).toEqual(selected);expect(b.assignments).toHaveLength(5);expect(b.risk_profile).toBe('control');expect(b.closure_case_id).toBeNull();});
 it('history is explicit and no_pump carries no hidden dispatch',()=>{const b=selectionBody(scene,{mode:'no_pump',selected:['1'],assignments:{1:'v'},risk:'history_validation'});expect(b.risk_profile).toBe('history_validation');expect(b.selected_task_ids).toEqual([]);expect(b.assignments).toEqual([]);});
 it('rejects metadata from another scene/version/seed and missing hash',()=>{const value={manifest_sha256:scene.manifest_sha256,context_sha256:'b'.repeat(64),resources:{tasks:[]},context:{format:'scenario-context-v1',...scene,facility_access:{}}};expect(validatePlanning(value,scene)).toBe(value);for(const key of ['scenario_id','scenario_version','seed']){const v=structuredClone(value);v.context[key]='wrong';expect(()=>validatePlanning(v,scene)).toThrow('identity');}expect(()=>validatePlanning({...value,context_sha256:'bad'},scene)).toThrow('SHA');});
});
