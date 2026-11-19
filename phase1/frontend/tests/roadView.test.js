import {describe,it,expect} from 'vitest';
import {scheduledTasks,taskState,parseRoadNetwork} from '../src/roadView';
import resourceExample from '../../../schemas/examples/resource_result.json';
import trafficExample from '../../../schemas/examples/traffic_result.json';

describe('Resource presentation follows the frozen Traffic timeline',()=>{
  it('uses exact boundaries and never shows a pump before installation',()=>{
    const resource=structuredClone(resourceExample),traffic=structuredClone(trafficExample);
    const task=resource.payload.tasks[0],route=traffic.payload.routes[0];
    route.arrival_time_s=task.vehicle_arrival_s;
    expect(scheduledTasks(resource,traffic)).toEqual([task]);
    expect(taskState(task,task.vehicle_arrival_s-1)).toBe('运输中');
    expect(taskState(task,task.vehicle_arrival_s)).toBe('卸载中');
    expect(taskState(task,task.unload_complete_s)).toBe('安装中');
    expect(taskState(task,task.pump_start_s)).toBe('抽水中');
    expect(taskState(task,task.pump_stop_s)).toBe('已停泵');
    expect(scheduledTasks(null,null)).toEqual([]);
  });
  it('rejects infeasible schedules, vehicle identity and nonmonotonic times',()=>{
    const resource=structuredClone(resourceExample),traffic=structuredClone(trafficExample);
    traffic.payload.routes[0].arrival_time_s=resource.payload.tasks[0].vehicle_arrival_s;
    resource.payload.feasible=false;expect(()=>scheduledTasks(resource,traffic)).toThrow('infeasible');
    resource.payload.feasible=true;resource.payload.tasks[0].vehicle_id='wrong';expect(()=>scheduledTasks(resource,traffic)).toThrow('mismatch');
    resource.payload.tasks[0].vehicle_id=traffic.payload.routes[0].vehicle_id;
    resource.payload.tasks[0].pump_start_s=resource.payload.tasks[0].vehicle_arrival_s-1;
    expect(()=>scheduledTasks(resource,traffic)).toThrow('mismatch');
  });
  it('rejects external entity declarations before invoking any XML parser',()=>{
    expect(()=>parseRoadNetwork('<!DOCTYPE net SYSTEM "https://example.invalid/x"><net/>',{})).toThrow('Invalid road XML');
  });
});
