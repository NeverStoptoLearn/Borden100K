import proj4 from 'proj4';
import { finite } from './contract';
export const DEMO_CRS = '+proj=utm +zone=10 +datum=WGS84 +units=m +no_defs';
export function checkGeoref(scenario) {
  const g = scenario.payload.georeference;
  finite(g);
  if (g.status === 'uncalibrated') {
    if ([g.projected_crs, g.local_to_projected, g.anchor_wgs84].some(v => v !== null)) throw Error('GEOREFERENCE_UNAVAILABLE: inconsistent uncalibrated metadata');
    return false;
  }
  if (!g.projected_crs || !g.local_to_projected || !g.anchor_wgs84) throw Error('GEOREFERENCE_UNAVAILABLE');
  if (g.status === 'calibrated' && (!g.local_to_projected.validation_artifact || scenario.data_provenance.calibration !== 'field_verified')) throw Error('GEOREFERENCE_UNAVAILABLE: validation evidence required');
  if (g.status === 'synthetic_anchor' && scenario.data_provenance.calibration !== 'synthetic_anchor') throw Error('Synthetic provenance mismatch');
  const [[a,b],[d,e]] = g.local_to_projected.matrix_2x3;
  if (Math.abs(a*e-b*d) < 1e-12) throw Error('Singular local transform');
  return true;
}
export function projectedToWgs(crs, xy) {
  if (crs === '!') throw Error('Unprojected synthetic SUMO network');
  finite(xy);
  const [lon,lat] = proj4(crs, 'WGS84', xy);
  finite([lon,lat]);
  if (Math.abs(lon)>180 || Math.abs(lat)>90) throw Error('Invalid WGS84');
  return { lon, lat };
}
export function localToWgs(scenario, p) {
  if (!checkGeoref(scenario)) return null;
  const g=scenario.payload.georeference;
  const xy=g.local_to_projected.matrix_2x3.map(([a,b,c])=>a*p.x_local_m+b*p.y_local_m+c);
  return projectedToWgs(g.projected_crs,xy);
}
export function sumoToWgs(network, p) {
  return projectedToWgs(network.proj_parameter,[p.x_m-network.net_offset_m[0],p.y_m-network.net_offset_m[1]]);
}
export function serviceBoundary(scenario) {
  if (!checkGeoref(scenario)) return [];
  const g=scenario.payload.georeference;
  const [e,n]=proj4('WGS84',g.projected_crs,[g.anchor_wgs84.lon,g.anchor_wgs84.lat]);
  const [w,h]=scenario.payload.service_extent_m;
  return [[-w/2,-h/2],[w/2,-h/2],[w/2,h/2],[-w/2,h/2],[-w/2,-h/2]].map(([x,y])=>projectedToWgs(g.projected_crs,[e+x,n+y]));
}
export function atTime(samples, seconds) {
  if (!samples.length || seconds < samples[0].event_time_s) return null;
  const a = samples.findLast(s=>s.event_time_s<=seconds);
  const b = samples.find(s=>s.event_time_s>seconds);
  if (!b) return a;
  const f=(seconds-a.event_time_s)/(b.event_time_s-a.event_time_s);
  return { ...a, event_time_s: seconds, sumo_position_m: { x_m: a.sumo_position_m.x_m+(b.sumo_position_m.x_m-a.sumo_position_m.x_m)*f, y_m: a.sumo_position_m.y_m+(b.sumo_position_m.y_m-a.sumo_position_m.y_m)*f }, wgs84: null };
}
// Flat artifact samples may interleave vehicles. Preserve each group's producer
// order and reject invalid timelines rather than sorting away evidence.
export function groupTrajectories(samples) {
  const groups=new Map();
  for(const sample of samples){
    const {vehicle_id,route_id,event_time_s,sumo_position_m}=sample;
    if(typeof vehicle_id!=='string'||!vehicle_id||typeof route_id!=='string'||!route_id||!sumo_position_m)throw Error('Invalid trajectory identity/position');
    if([event_time_s,sumo_position_m.x_m,sumo_position_m.y_m].some(n=>typeof n!=='number'))throw Error('Invalid trajectory time/position');
    finite([event_time_s,sumo_position_m.x_m,sumo_position_m.y_m]);
    const key=JSON.stringify([vehicle_id,route_id]);
    if(!groups.has(key))groups.set(key,{key,vehicle_id,route_id,samples:[]});
    const group=groups.get(key);
    if(group.samples.length&&event_time_s<=group.samples.at(-1).event_time_s)throw Error('Trajectory group time must strictly increase');
    group.samples.push(sample);
  }
  return [...groups.values()];
}
export function replayGroups(groups,seconds) {
  if(typeof seconds!=='number')throw Error('Invalid replay time');
  finite(seconds);
  return groups.map(group=>{
    // Before first actual sample: hidden. After last: hold last recorded point.
    // No inferred departure/arrival endpoint or movement between separate routes.
    const position=atTime(group.samples,seconds);
    const trail=group.samples.filter(p=>p.event_time_s<=seconds);
    if(position&&trail.at(-1)?.event_time_s!==position.event_time_s)trail.push(position);
    return {...group,position,trail};
  });
}
export function modelDay(axis, seconds) { return axis.reference_model_day + seconds / 86400; }
