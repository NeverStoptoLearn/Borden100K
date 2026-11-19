// Presentation-only SUMO coordinates. No geocoding, basemap or model transform.
import { finite } from './contract';

export function parseRoadNetwork(xml,network,Parser=globalThis.DOMParser){
  if(typeof xml!=='string'||xml.length>32*1024*1024||/<!DOCTYPE|<!ENTITY/i.test(xml))throw Error('Invalid road XML');
  if(!Parser)throw Error('XML parser unavailable');
  const document=new Parser().parseFromString(xml,'application/xml');
  if(document.getElementsByTagName('parsererror').length||document.documentElement?.tagName!=='net')throw Error('Invalid SUMO network XML');
  const location=document.getElementsByTagName('location')[0];
  const offset=location?.getAttribute('netOffset')?.split(',').map(Number);
  if(location?.getAttribute('projParameter')!==network.proj_parameter||!offset||offset.length!==2||offset.some((n,i)=>!Number.isFinite(n)||n!==network.net_offset_m[i]))throw Error('Network coordinate metadata mismatch');
  const roads=[],seen=new Set();
  for(const edge of document.getElementsByTagName('edge')){
    if(edge.getAttribute('function')==='internal')continue;
    const id=edge.getAttribute('id');
    if(!id||seen.has(id))throw Error('Invalid/duplicate road edge');seen.add(id);
    const lane=Array.from(edge.getElementsByTagName('lane')).find(l=>l.getAttribute('index')==='0');
    if(!lane)throw Error('Road edge lacks lane0');
    const points=(lane.getAttribute('shape')||'').trim().split(/\s+/).map(pair=>pair.split(',').map(Number));
    if(points.length<2||points.some(p=>p.length!==2||p.some(v=>!Number.isFinite(v))))throw Error('Invalid road shape');
    finite(points);roads.push({edge_id:id,points,allow:lane.getAttribute('allow'),disallow:lane.getAttribute('disallow')});
    if(roads.length>100000)throw Error('Road display size limit');
  }
  return roads;
}

export function scheduledTasks(resource,traffic){
  if(!resource)return [];
  if(!resource.payload.feasible||resource.payload.violations.length)throw Error('Cannot replay infeasible resource schedule');
  return resource.payload.tasks.map(task=>{
    const route=traffic?.payload.routes.find(r=>r.task_id===task.task_id);
    const chain=['vehicle_arrival_s','unload_complete_s','install_complete_s','pump_start_s','pump_stop_s'].map(k=>task[k]);
    if(!route?.reachable||route.vehicle_id!==task.vehicle_id||route.arrival_time_s!==task.vehicle_arrival_s||chain.some((t,i)=>!Number.isFinite(t)||t<0||(i&&t<chain[i-1]))||chain[3]>=chain[4])throw Error('Resource/Traffic timeline mismatch');
    return task;
  });
}

export function taskState(task,seconds){
  if(seconds<task.vehicle_arrival_s)return '运输中';
  if(seconds<task.unload_complete_s)return '卸载中';
  if(seconds<task.install_complete_s)return '安装中';
  if(seconds<task.pump_start_s)return '待启泵';
  if(seconds<task.pump_stop_s)return '抽水中';
  return '已停泵';
}
