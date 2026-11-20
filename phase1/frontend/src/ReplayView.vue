<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { demo } from './demo';
import { loadApiReplay } from './apiReplay';
import { groupTrajectories, replayGroups, checkGeoref, localToWgs, modelDay, serviceBoundary, sumoToWgs } from './spatial';
import { concentrationFrame } from './artifacts';
import { taskState } from './roadView';
const props=defineProps({initialData:Object});
const data=ref(props.initialData??null),loadError=ref(''), seconds=ref(0),playing=ref(false),speed=ref(30),mapState=ref('未启用'),mapError=ref(''),host=ref(),localOnly=ref(false),showRoutes=ref(true),showConcentration=ref(true),selected=ref('');
let scene,timer,readyTimer;let alive=true;
const groups=computed(()=>data.value?groupTrajectories(data.value.samples):[]);
const replay=computed(()=>replayGroups(groups.value,seconds.value));
const svgPoints=samples=>samples.map(p=>`${p.sumo_position_m.x_m},${p.sumo_position_m.y_m}`).join(' ');
const actual=computed(()=>data.value?.source==='api');
const tasks=computed(()=>data.value?.tasks??[]);
const metrics=computed(()=>data.value?.hydro?.payload.metrics??null);
const depots=computed(()=>data.value?.parameters?.data.resources?.inventory??[]);
const earthDownload=profile=>`/api/v1/runs/${encodeURIComponent(data.value.scenario.run_id)}/exports/earth.kmz?profile=${profile}`;
const duration=computed(()=>actual.value?Math.max(1,...data.value.samples.map(p=>p.event_time_s),...(data.value.concentration?.times_day??[]).map(t=>(t-axis.value.reference_model_day)*86400)):600);
function bounds(points){if(!points.length)return '0 0 100 100';let x=Infinity,y=Infinity,xx=-Infinity,yy=-Infinity;for(const p of points){x=Math.min(x,p[0]);y=Math.min(y,p[1]);xx=Math.max(xx,p[0]);yy=Math.max(yy,p[1]);}const w=Math.max(1,xx-x),h=Math.max(1,yy-y),pad=Math.max(w,h)*.1;return `${x-pad} ${y-pad} ${w+2*pad} ${h+2*pad}`;}
const trafficBounds=computed(()=>actual.value?bounds([...(data.value.roads??[]).flatMap(r=>r.points),...data.value.samples.map(p=>[p.sumo_position_m.x_m,p.sumo_position_m.y_m])]):'600 600 900 1000');
const concentrationBounds=computed(()=>actual.value?bounds(data.value.concentration?.cell_xyz_m??[]):'0 0 1380 1260');
const markerRadius=computed(()=>actual.value?Math.max(...trafficBounds.value.split(' ').slice(2).map(Number))*.015:30);
const cellSize=computed(()=>actual.value?Math.max(...concentrationBounds.value.split(' ').slice(2).map(Number))/Math.max(10,Math.sqrt(data.value.concentration?.cell_ids.length??1)):150);
const axis=computed(()=>data.value?.scenario.payload.time_axis);
const day=computed(()=>axis.value?modelDay(axis.value,seconds.value):0);
const frame=computed(()=>data.value?concentrationFrame(data.value.concentration,day.value):null);
const utc=computed(()=>axis.value?new Date(Date.parse(axis.value.scenario_origin_utc)+seconds.value*1000).toISOString():'');
const scenario=computed(()=>{if(!data.value)return null;const s=structuredClone(JSON.parse(JSON.stringify(data.value.scenario)));if(localOnly.value){s.payload.georeference={status:'uncalibrated',projected_crs:null,local_to_projected:null,anchor_wgs84:null};s.data_provenance.calibration='none';}return s;});
const geo=computed(()=>scenario.value&&checkGeoref(scenario.value));
function seek(value){const n=Number(value);if(value!==''&&Number.isFinite(n)&&n>=0&&n<=duration.value)seconds.value=n;}
function render(){
  if(!scene||!data.value)return;
  const d=data.value,s=scenario.value;
  if(!geo.value){scene.draw({boundary:[],markers:[],paths:[]});return;}
  const markers=[...s.payload.wells.map(w=>({...localToWgs(s,w.position),label:`SYNTHETIC · ${w.well_id}`})),...d.depots.map(p=>({...sumoToWgs(d.network,p.sumo_position_m),label:`SYNTHETIC · ${p.depot_id}`}))];
  for(const g of replay.value)if(g.position)markers.push({...sumoToWgs(d.network,g.position.sumo_position_m),label:`SYNTHETIC · ${g.vehicle_id} / ${g.route_id}`});
  const paths=showRoutes.value?replay.value.flatMap(g=>[{points:g.samples.map(p=>sumoToWgs(d.network,p.sumo_position_m)),color:'#6d8fa4'},{points:g.trail.map(p=>sumoToWgs(d.network,p.sumo_position_m)),color:'#ffd276'}]):[];
  // Concentrations stay in the local panel: these are subsurface cells, not terrain surface values.
  scene.draw({boundary:serviceBoundary(s),markers,paths});
}
async function startMap(){
  mapState.value='加载中';mapError.value='';
  try{
    if(!data.value?.scenario.payload.georeference.anchor_wgs84)throw Error('GEOREFERENCE_UNAVAILABLE：未配准结果禁止Google加载。');
    const {createScene,loadGoogle}=await import('./google3d');
    const api=await loadGoogle({key:import.meta.env.VITE_GOOGLE_MAPS_API_KEY,enabled:import.meta.env.VITE_GOOGLE_MAPS_ENABLED==='true',onError:e=>{mapState.value='授权失败';mapError.value=e.message;clearTimeout(readyTimer);}});
    if(!alive)return;
    scene?.dispose();
    scene=createScene(api,host.value,data.value.scenario.payload.georeference.anchor_wgs84,()=>{mapState.value='场景已稳定';clearTimeout(readyTimer);},()=>{mapState.value='加载失败';mapError.value='Google 场景报告错误，请检查服务设置。';});
    mapState.value='等待三维影像';
    readyTimer=setTimeout(()=>{if(mapState.value==='等待三维影像'){mapState.value='尚未验证';mapError.value='三维影像未就绪，检查 WebGL、覆盖与网络。';}},30000);
    render();
  }catch(e){mapState.value='未连接';mapError.value=e.message;}
}
function focus(){if(scene&&geo.value)scene.focus(localToWgs(scenario.value,scenario.value.payload.wells[0].position));}
watch([seconds,localOnly,showRoutes],render);
onMounted(async()=>{try{const query=new URLSearchParams(location.search);if(!props.initialData)data.value=query.has('run_id')?await loadApiReplay(query.get('run_id')):await demo({twoVehicles:query.get('fixture')==='two-vehicles'});timer=setInterval(()=>{if(playing.value){seconds.value=Math.min(duration.value,seconds.value+speed.value/10);if(seconds.value===duration.value)playing.value=false;}},100);}catch(e){loadError.value=e.message;}});
onUnmounted(()=>{alive=false;clearInterval(timer);clearTimeout(readyTimer);scene?.dispose();});
</script>

<template>
  <div class="shell" v-if="data" :data-source="actual?'api':'fixture'" :data-run-id="data.scenario.run_id" :data-frames="data.concentration?.times_day.length??0">
    <header><div class="brand">B<span>100K</span></div><div><h1>道路 · 资源 · 地下水</h1><p>BORDEN / PHASE I / SIMULATION PLATFORM</p></div><span class="badge">{{actual?"SYNTHETIC INPUT · ACTUAL ENGINE":"SYNTHETIC · FIXTURE"}}</span><span class="phase">{{actual?"只读运行结果":"独立前端演示"}}</span></header>
    <div class="notice">{{actual?"真实引擎产物 · 合成输入 · 未经现场校准 · SHA256已校验。":"合成演示 · 车辆与浓度为手工 fixture，未执行 SUMO / 水文求解。"}}Web展示无需地图Key；Google Earth为独立环境观察工具。</div>
    <main>
      <aside>
        <div class="eyebrow">SCENARIO 01</div><h2>服务范围与图层</h2><div class="metric">100 <span>×</span> 100 <small>km</small></div><p>道路 / 资源服务域<br>局部水文域独立展示</p>
        <div class="rule"></div><label><input type="checkbox" v-model="showRoutes"> 车辆路线与已行轨迹</label><label><input type="checkbox" v-model="showConcentration"> 局部浓度 · mg/L</label><label><input type="checkbox" v-model="localOnly"> 切换未配准场景</label>
        <p class="hint">{{geo?'地理参考：使用场景配置中的示意锚点。':'本地坐标：道路与地下水分别显示。'}}</p>
        <div class="rule"></div><div class="eyebrow">OBJECTS</div><button v-if="!actual" class="object" @click="selected='depot-1'"><i class="dot depot"></i><span>基地 depot-1<small>synthetic · 示例仓储</small></span></button><button v-if="!actual" class="object" @click="selected='well-1';focus()"><i class="dot well"></i><span>井场 well-1<small>synthetic · 局部 (300, 400) m</small></span></button><button v-for="g in replay" :key="g.key" class="object" @click="selected=g.vehicle_id"><i class="dot truck"></i><span>车辆 {{g.vehicle_id}} / {{g.route_id}}<small>{{!g.position?'尚无实际采样点':seconds>g.samples.at(-1).event_time_s?'保持最后采样点':'回放中 · 组内插值示意'}}</small></span></button>
        <button v-for="w in actual?data.scenario.payload.wells:[]" :key="w.well_id" class="object" @click="selected=w.well_id"><span>井场 {{w.well_id}}<small>模型 cell {{w.cell_id}} · 独立局部水文坐标</small></span></button><div class="selected">当前选择 <strong>{{selected}}</strong></div>
        <div class="run"><div class="eyebrow">REPLAY IDENTITY</div><code>{{data.scenario.run_id}}</code><p>scenario v{{data.scenario.scenario_version}} · seed {{data.scenario.seed}}<br>parameters: {{data.scenario.parameter_version}}<br>build: {{actual?data.status.model_build.engine_version:"planned / fixture"}}</p><span class="state">运行状态：{{data.status.status}}{{actual?"（真实API状态）":"（合同 fixture）"}}</span></div>
      </aside>
      <section class="workspace">
        <div v-if="actual" class="notice" aria-label="Google Earth 导出"><a :href="earthDownload('earth_web')" download>下载 Earth 网页版 KMZ</a> · <a :href="earthDownload('earth_pro')" download>下载 Earth Pro KMZ</a><br>无需 Maps API Key。{{geo?'以导出报告中的配准门禁为准。':'当前未配准：KMZ保留数据与局部图，不生成地理叠加。'}}</div>
        <section class="service-summary" aria-label="服务域与坐标说明"><strong>100 × 100 km 道路/资源服务域</strong><span>当前运行展示其已验证输入；地理参考不代表水文配准或100km实地全网验收。</span><span>{{geo?'SYNTHETIC ANCHOR · 示意坐标':'NO GEOREFERENCE · 道路与地下水分图展示'}} · Earth仅用于独立环境参考，非网页引擎。</span></section>
        <div class="local-grid">
          <section class="panel"><div class="panelhead"><h3>道路网络与车辆回放</h3><span>SUMO 坐标 / m · {{actual?"ACTUAL ARTIFACT":"FIXTURE"}}</span></div><svg :viewBox="trafficBounds" role="img" aria-label="车辆局部轨迹"><g class="road-network"><polyline v-for="road in data.roads??[]" :key="road.edge_id" :data-edge="road.edge_id" :points="road.points.map(p=>p.join(',')).join(' ')" class="road-edge"><title>{{road.edge_id}} · lane0代表中心线</title></polyline></g><g v-for="g in replay" :key="g.key" :data-route="g.route_id" :data-vehicle="g.vehicle_id"><polyline v-if="showRoutes" :points="svgPoints(g.samples)" class="route"/><polyline v-if="showRoutes" :points="svgPoints(g.trail)" class="trail"/><circle v-if="g.position" class="vehicle-marker" :cx="g.position.sumo_position_m.x_m" :cy="g.position.sumo_position_m.y_m" :r="markerRadius" fill="#ffd276"><title>{{g.vehicle_id}} / {{g.route_id}} · {{actual?"actual engine / synthetic input":"synthetic fixture"}}</title></circle></g><rect v-if="!actual" x="675" y="675" width="50" height="50" fill="#63e4d0"/><circle v-if="!actual" cx="1300" cy="1400" r="26" fill="#d7a8ff"/></svg><p>{{actual?`已索引路段 ${data.roads?.length??0} · 黄色车辆 · 路线分别绘制`:"青色 基地 / 紫色 井场 / 黄色 车辆"}}<br>{{actual?"底网为已校验net.xml的lane0几何，轨迹为实际采样；无底网时仅显示轨迹。":"路线为手工示意，不表示真实道路。"}}</p></section>
          <section class="panel"><div class="panelhead"><h3>局部污染浓度</h3><span>模型 x / y / z · m</span></div><svg :viewBox="concentrationBounds" role="img" aria-label="局部浓度图"><rect v-if="!actual" width="1380" height="1260" fill="#112e38"/><g v-if="showConcentration&&frame"><rect v-for="(p,i) in frame.cells" :key="i" :x="p[0]-cellSize/2" :y="actual?p[1]-cellSize/2:1260-p[1]-75" :width="cellSize" :height="cellSize" :fill="`rgba(255,170,80,${0.15+frame.values[i]/2})`"><title>{{frame.values[i]}} mg/L · z={{p[2]}} m</title></rect></g><circle v-if="!actual" cx="300" cy="860" r="22" fill="#d7a8ff"/></svg><p>{{actual?"真实NPZ · mg/L · 单元着色示意":"0 ░▒▓ 1.4 mg/L · 离散单元示意"}}<br>采样 day {{frame?.day.toFixed(6)}} · 非健康风险</p></section>
        </div>
        <section v-if="actual" class="panel resource-panel" aria-label="资源调度结果"><div class="panelhead"><h3>资源调度与启停时间线</h3><span>场景秒 / m3/day · {{data.resource?'已核验Resource产物':'本次无资源调度阶段'}}</span></div><div class="table-scroll"><table v-if="tasks.length"><thead><tr><th>任务 / 井 / 车辆</th><th>到达 / 卸载 / 安装</th><th>启泵 / 停泵</th><th>抽取强度</th><th>当前状态</th></tr></thead><tbody><tr v-for="task in tasks" :key="task.task_id"><td>{{task.task_id}} / {{task.well_id}} / {{task.vehicle_id}}</td><td>{{task.vehicle_arrival_s}} / {{task.unload_complete_s}} / {{task.install_complete_s}}</td><td>{{task.pump_start_s}} / {{task.pump_stop_s}}</td><td>{{task.extraction_rate_m3_day}}</td><td>{{taskState(task,seconds)}}</td></tr></tbody></table><p v-else>无启泵任务，不补造车辆或抽水事件。</p></div><p v-for="stock in depots" :key="stock.depot_id+'.'+stock.item_id">基地 {{stock.depot_id}} · {{stock.item_id}} 库存 {{stock.quantity}} · 位置未提供，不在道路图中猜测落点。</p></section>
        <section v-if="metrics" class="panel result-panel" aria-label="地下水运行指标"><h3>地下水运行指标 · 非健康风险</h3><div class="metric-grid"><span>域内残余质量 <strong>{{metrics.residual_mass_kg.toPrecision(6)}} kg</strong></span><span>抽出污染质量 <strong>{{metrics.pumped_mass_kg.toPrecision(6)}} kg</strong></span><span>最大浓度 <strong>{{metrics.max_concentration_mg_L.toPrecision(6)}} mg/L</strong></span><span>最大降深 <strong>{{metrics.max_drawdown_m.toPrecision(6)}} m</strong></span></div><p>评价窗口 day {{data.hydro.payload.risk_window_start_day}}–{{data.hydro.payload.risk_window_stop_day}}；保留原始signed结果及错误语义，不自动改窗。</p></section>
        <footer><div class="timeheading"><strong>场景回放 <span>{{seconds.toFixed(0)}} s / {{duration.toFixed(0)}} s</span></strong><span>{{utc}}</span></div><div class="controls"><button @click="seconds=0;playing=false">归零</button><button class="primary" @click="seconds===duration&&(seconds=0);playing=!playing">{{playing?'暂停':'播放'}}</button><input aria-label="场景秒" type="range" min="0" :max="duration" step="1" v-model.number="seconds"><input v-if="actual" aria-label="回放秒数" type="number" min="0" :max="duration" step="1" :value="seconds" @change="seek($event.target.value)" style="width:100px"><select aria-label="回放速度" v-model.number="speed"><option :value="1">1×</option><option :value="30">30×</option><option :value="120">120×</option></select></div><div class="timeheading"><span>起点 day {{axis.reference_model_day}} · 参考日保持原值</span><span>当前模型日 {{day.toFixed(6)}}</span></div></footer>
        <details class="optional-google"><summary>可选 Google Maps 3D · 不属于一期必需项</summary><p>Google Earth/Pro 是独立环境观察工具。以下仅保留原有可选 SDK，不影响默认运行；历史真实影像验收仍为 NOT_RUN。</p><div class="mapbar"><span>Google Maps 3D <small>{{mapState}}</small></span><div><button @click="startMap">加载 Google 三维</button><button @click="focus" :disabled="!geo||!data.scenario.payload.wells.length">查看井场</button></div></div><div class="mapwrap"><div ref="host" class="map" aria-label="Google 三维地理场景"></div><div v-if="mapState!=='场景已稳定'" class="empty"><h2>可选三维影像</h2><p>{{mapError||'未启用；默认道路、资源和结果展示不需要此服务。'}}</p></div><div class="mapcaption">{{geo?'SYNTHETIC ANCHOR · 仅示意配准':'NO GEOREFERENCE · 已清空地理叠加'}}</div></div></details>
      </section>
    </main>
  </div>
  <div v-else class="loading" role="status">{{loadError?"运行结果加载失败："+loadError:"正在校验并加载运行结果…"}}</div>
</template>
