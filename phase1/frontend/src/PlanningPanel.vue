<script setup>
import {ref,computed,onMounted,onUnmounted,watch} from 'vue';
import CreateTaskForm from './CreateTaskForm.vue';
import {validateCustomTask} from './customTasks';
import {requestJSON} from './sceneClient';
import {validatePlanning,selectionBody} from './planning';
const props=defineProps({scene:Object,mode:String,replay:Object,frame:Object});
const emit=defineEmits(['configured','invalidated','update:mode','metadata']);
const metadata=ref(null),selected=ref([]),assignments=ref({}),risk=ref('control'),traffic=ref('emergency_only'),closure=ref(''),error=ref(''),busy=ref(false),compiled=ref(null);
const controller=new AbortController();let disposed=false,generation=0;
const base=()=>`/api/v1/scene-catalog/${props.scene.scenario_id}/versions/${props.scene.scenario_version}`;
const resources=computed(()=>metadata.value?.resources);
const access=computed(()=>metadata.value?.context.facility_access);
const origin=id=>access.value?.vehicles.find(v=>v.vehicle_id===id);
const dispatch=id=>props.replay?.executedTrafficRequest?.payload.dispatches.find(d=>d.task_id===id);
const operation=id=>access.value?.tasks.find(t=>t.task_id===id)??metadata.value?.custom_tasks?.find(e=>e.snapshot.id===id)?.record.access;
const taskGroups=computed(()=>[{title:'原始五项任务池',tasks:resources.value?.tasks??[]},{title:'本版本新增任务（创建后未自动选中）',tasks:metadata.value?.custom_tasks?.map(e=>e.snapshot.data)??[]}]);
async function refresh(){const value=validatePlanning(await requestJSON(base()+'/planning',{signal:controller.signal}),props.scene);for(const entry of value.custom_tasks??[])await validateCustomTask(entry,value);if(!disposed){metadata.value=value;emit('metadata',value);}return value;}
const eligible=task=>resources.value?.vehicles.filter(v=>origin(v.vehicle_id)?.depot_id===task.depot_id)??[];
async function taskCreated(){try{await refresh();}catch(e){if(!disposed)error.value=e.message;}}
function invalidate(){generation++;compiled.value=null;emit('invalidated');}
watch(()=>props.mode,invalidate);watch([selected,assignments,risk,closure,traffic],invalidate,{deep:true});
async function configure(){const ticket=generation;busy.value=true;error.value='';try{
 const body=selectionBody(props.scene,{mode:props.mode,selected:selected.value,assignments:assignments.value,closure:closure.value,risk:risk.value,traffic:traffic.value});
 const result=await requestJSON(base()+'/configurations',{body,signal:controller.signal});
 if(result.scenario.scenario_id!==props.scene.scenario_id||result.scenario.scenario_version!==props.scene.scenario_version||result.configuration.manifest_sha256!==props.scene.manifest_sha256||result.parameter.data.scenario.id!==props.scene.scenario_id||result.parameter.data.scenario.version!==props.scene.scenario_version)throw Error('Compiled scene identity mismatch');
 if(!disposed&&ticket===generation){compiled.value=result;emit('configured',result);}
 }catch(e){if(!disposed)error.value=e.message;}finally{if(!disposed)busy.value=false;}}
onMounted(async()=>{busy.value=true;try{const value=await refresh();if(!disposed)closure.value=value.context.closures?.[0]?.case_id??'';}catch(e){if(!disposed)error.value=e.message;}finally{if(!disposed)busy.value=false;}});
onUnmounted(()=>{disposed=true;controller.abort();});
</script>
<template><section class="planning-panel" aria-label="应急任务配置"><h3>应急资源与任务选择</h3>
 <p v-if="scene.scenario_id==='borden_reference_100k'">真实局部OSM道路＋SYNTHETIC应急资源与操作点。非100km道路验收；操作点→模型cell为演示关联，水文未现场配准。</p>
 <p v-else>程序生成地形、设施和需求 · SYNTHETIC；车辆轨迹仅在真实引擎成功后显示。</p>
 <p v-if="replay?.planning" aria-label="当前运行冻结评价窗">当前播放run {{replay.scenario.run_id}}：已冻结 {{replay.planning.configuration.risk_profile}} · 窗口 {{replay.planning.configuration.risk_window_model_day.join('–')}} 日；当前状态 {{replay.status.status}}。</p><p>以下为新配置草稿，尚未运行；修改草稿不会改变当前播放run的评价窗或验收状态。</p><p v-if="error" role="alert">{{error}}</p><p v-if="busy">正在校验配置…</p>
 <template v-if="resources"><p>基地 {{access.depots.length}} · 车辆 {{resources.vehicles.length}} · 人员组 {{resources.crews.length}} · 设备 {{resources.equipment.length}} · 候选任务 {{resources.tasks.length}}</p>
 <details><summary>基地、库存、人员与设备</summary><p v-for="d in access.depots" :key="d.depot_id">{{d.depot_id}} · 接入 {{d.edge_id}} · 合成基地</p><p v-for="s in resources.inventory" :key="s.depot_id+s.item_id">{{s.depot_id}} / {{s.item_id}} 库存 {{s.quantity}}</p><p v-for="c in resources.crews" :key="c.crew_id">{{c.crew_id}} · 技能 {{c.skills.join(', ')}} · {{c.available_from_s}}–{{c.available_until_s}} s</p><p v-for="e in resources.equipment" :key="e.equipment_id">{{e.equipment_id}} · {{e.type}} · {{e.mass_kg}} kg</p></details>
 <details><summary>车辆运力与道路起点</summary><p v-for="v in resources.vehicles" :key="v.vehicle_id">{{v.vehicle_id}} · {{v.capacity_kg}} kg · {{origin(v.vehicle_id)?.depot_id}} · {{origin(v.vehicle_id)?.edge_id}}</p></details>
 <p>每车每次运行一次 dispatch，无返程或续运。已选 {{selected.length}} / {{resources.tasks.length}}；不会自动丢弃第五项。</p>
 <CreateTaskForm :scene="scene" :metadata="metadata" @created="taskCreated"/><fieldset v-for="group in taskGroups" :key="group.title" :disabled="mode==='no_pump'||busy"><legend>{{group.title}} · 选择并指定同基地车辆</legend><div v-for="t in group.tasks" :key="t.task_id" class="task-choice"><label><input type="checkbox" :value="t.task_id" v-model="selected" :aria-label="'选择任务 '+t.task_id">{{t.task_id}} → {{t.well_id}}</label><p>{{t.depot_id}} · {{t.required_quantity}} {{t.required_item_id}} · 技能 {{t.required_skill}} · 时窗 {{t.earliest_start_s}}–{{t.latest_stop_s}} s</p><select :aria-label="'任务车辆 '+t.task_id" v-model="assignments[t.task_id]" :disabled="!selected.includes(t.task_id)"><option value="">请选择车辆</option><option v-for="v in eligible(t)" :key="v.vehicle_id" :value="v.vehicle_id">{{v.vehicle_id}} · {{v.capacity_kg}} kg</option></select><small>道路终点 {{operation(t.task_id)?.edge_id}} · 模型cell {{operation(t.task_id)?.cell_id}} · synthetic操作关联</small></div></fieldset>
 <label v-if="mode==='closure'">道路封闭方案 <select aria-label="道路封闭方案" v-model="closure"><option v-for="c in metadata.context.closures" :key="c.case_id" :value="c.case_id">{{c.label}}</option></select></label>
 <label>背景交通配置 <select aria-label="背景交通配置" v-model="traffic" :disabled="mode==='no_pump'"><option value="emergency_only">仅应急车辆</option><option value="synthetic_background_small" :disabled="!metadata.context.background_demand">真实SUMO小规模背景车（synthetic需求）</option></select></label><label>风险评价配置 <select aria-label="风险评价配置" v-model="risk"><option value="control">默认控制窗14600–21900日</option><option value="history_validation">显式历史验证0–21900日（非控制窗验收）</option></select></label><p v-if="risk==='control'">控制窗 signed 净源质量可能产生合同错误；失败保持 failed，不自动改窗。</p><p v-else>已显式选择历史验证窗；将生成独立参数版本，不作为控制窗通过。</p>
 <button @click="configure" :disabled="busy">冻结任务与分配</button><p v-if="compiled">已冻结 {{compiled.parameter.id}} · {{risk}}；可以启动当前工况。</p>
 </template>
 <details v-if="replay?.traffic"><summary>本次真实路线、ETA与任务状态</summary><p v-for="r in replay.traffic.payload.routes" :key="r.route_id">{{r.vehicle_id}} · {{r.task_id}} · {{dispatch(r.task_id)?.from_edge}} → {{dispatch(r.task_id)?.to_edge}} · {{r.reachable?'ETA '+r.arrival_time_s+' s':r.reason}} · {{frame?.taskStates.find(t=>t.task_id===r.task_id)?.state}}</p></details>
 </section></template>
