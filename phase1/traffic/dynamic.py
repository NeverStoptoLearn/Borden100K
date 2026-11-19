"""Opt-in same-process SUMO background demand and observed routing costs."""
import copy, math

def validate_config(c,request,edges,max_time):
 required={'format','scenario_id','scenario_version','seed','network_sha256','background','background_max_speed_m_s','sample_interval_s','routing_interval_s','cooldown_s','delay_threshold_s'}
 if not isinstance(c,dict) or set(c)!=required or c['format']!='dynamic-traffic-config-v1':raise ValueError('Invalid dynamic config fields/format')
 for k in ('scenario_id','scenario_version','seed'):
  if c[k]!=request[k]:raise ValueError('Dynamic identity mismatch: '+k)
 if c['network_sha256']!=request['payload']['network']['network_sha256']:raise ValueError('Dynamic network SHA mismatch')
 for k in ('background_max_speed_m_s','sample_interval_s','routing_interval_s','cooldown_s','delay_threshold_s'):
  if type(c[k]) not in (int,float) or not math.isfinite(c[k]) or c[k]<=0:raise ValueError('Invalid dynamic value '+k)
 if not 0<c['background_max_speed_m_s']<=40 or any(c[k]!=int(c[k]) for k in ('sample_interval_s','routing_interval_s','cooldown_s')):raise ValueError('Invalid dynamic bounds')
 if not isinstance(c['background'],list) or len(c['background'])>200:raise ValueError('Limit background demand to 200 in focus acceptance')
 ids=set();routes=set();emergency={d['vehicle_id'] for d in request['payload']['dispatches']}
 for d in c['background']:
  if set(d)!={'vehicle_id','route_id','depart_s','from_edge','to_edge'}:raise ValueError('Invalid background OD')
  if not d['vehicle_id'].startswith('bg-') or not d['route_id'].startswith('bg-route-') or d['vehicle_id'] in ids|emergency or d['route_id'] in routes:raise ValueError('Background namespace/identity collision')
  if type(d['depart_s']) not in (int,float) or d['depart_s']!=int(d['depart_s']) or not 0<=d['depart_s']<max_time or d['from_edge'] not in edges or d['to_edge'] not in edges:raise ValueError('Invalid background time/edge')
  ids.add(d['vehicle_id']);routes.add(d['route_id'])
 return copy.deepcopy(c)

class DynamicTraffic:
 def __init__(self,c,request,axis,conn,edges):
  self.c=c;self.conn=conn;self.edges=edges;self.pending={d['vehicle_id']:d for d in c['background']};self.active=set();self.rows={};self.samples=[];self.stats=[];self.decisions=[];self.last={}
  self.header={k:request[k] for k in ('scenario_id','scenario_version','parameter_version','run_id','seed')};self.header.update(network_id=request['payload']['network']['network_id'],network_sha256=c['network_sha256'],time_axis=axis,demand_synthetic=True,source='actual_engine')
  conn.vehicletype.copy('DEFAULT_VEHTYPE','dyn-background');conn.vehicletype.setMaxSpeed('dyn-background',c['background_max_speed_m_s'])
  self.header.update(statistics_scope='observed_routes_and_demand_edges',congestion_definition='occupied edge mean speed below half freeflow; derived indicator',cost_hold_policy='latest observed travel time held until next update; empty edges freeflow',coordinate_frame='synthetic_local_m' if request['payload']['network']['proj_parameter']=='!' else 'wgs84_projected');self.focus={d[k] for d in c['background'] for k in ('from_edge','to_edge')};self.costs={};self.free={e:conn.lane.getLength(e+'_0')/conn.lane.getMaxSpeed(e+'_0') for e in edges}
 def busy(self):return bool(self.pending or self.active)
 def inject(self,t,plan):
  for vid,d in list(self.pending.items()):
   if d['depart_s']>t:continue
   path=plan(d['from_edge'],d['to_edge'])
   if not path:
    self.rows[vid]=dict(d,status='unreachable',actual_depart_s=None,arrival_time_s=None);del self.pending[vid];continue
   self.conn.route.add(d['route_id'],path);self.conn.vehicle.add(vid,d['route_id'],typeID='dyn-background',depart=str(t));self.active.add(vid);del self.pending[vid]
   self.rows[vid]=dict(d,status='inserted',actual_depart_s=None,arrival_time_s=None)
 def apply_closure(self,closed,t):
  for vid in list(self.active):
   row=self.rows[vid]
   if row['actual_depart_s'] is None and row['from_edge'] in closed:
    self.conn.vehicle.remove(vid);self.active.remove(vid);row.update(status='unreachable_before_insertion_due_closure',arrival_time_s=None)
 def observe(self,t):
  present=set(self.conn.vehicle.getIDList())
  for vid in self.conn.simulation.getDepartedIDList():
   if vid in self.rows:self.rows[vid]['actual_depart_s']=t
  for vid in self.conn.simulation.getArrivedIDList():
   if vid in self.active:self.active.remove(vid);self.rows[vid].update(status='arrived',arrival_time_s=t)
  if t%self.c['sample_interval_s']:return
  for vid in sorted(self.active&present):
   x,y=self.conn.vehicle.getPosition(vid);wgs=None
   if self.conn.simulation.getNetBoundary() and self.header.get('coordinate_frame')=='wgs84_projected':
    lon,lat=self.conn.simulation.convertGeo(x,y);wgs=dict(lon=lon,lat=lat)
   self.samples.append(dict(vehicle_id=vid,route_id=self.rows[vid]['route_id'],event_time_s=t,sumo_position_m=dict(x_m=x,y_m=y),wgs84=wgs))
  for edge in sorted(self.focus):
   n=self.conn.edge.getLastStepVehicleNumber(edge);length=self.conn.lane.getLength(edge+'_0');lanes=self.conn.edge.getLaneNumber(edge);speed=self.conn.edge.getLastStepMeanSpeed(edge) if n else None
   travel=self.conn.edge.getTraveltime(edge) if n else self.free[edge]
   if travel<=0 or not math.isfinite(travel):travel=self.free[edge]
   self.costs[edge]=travel;self.conn.edge.adaptTraveltime(edge,travel,begin=t,end=2147483647)
   self.stats.append(dict(edge_id=edge,event_time_s=t,mean_speed_m_s=speed,vehicle_count=n,density_veh_km_lane=n/(length/1000*lanes),travel_time_s=travel if n else None,congested=bool(n and speed<length/self.free[edge]*.5),quality='observed' if n else 'empty_no_travel_observation'))
 def eta(self,path,t):return t+sum(self.costs.get(e,self.free[e]) for e in path)
 def record(self,vid,rid,t,old,new,reason):
  self.decisions.append(dict(vehicle_id=vid,route_id=rid,decision_s=t,old_edge_ids=list(old),new_edge_ids=list(new),old_eta_s=self.eta(old,t),new_eta_s=self.eta(new,t),cost_source='same_run_edge_travel_time_with_empty_edge_freeflow',eta_semantics='absolute_s_remaining_edge_sum_includes_whole_current_edge',reason=reason,applied=True));self.last[vid]=t
 def replan(self,t,running,routes,plan,route_plans,closed):
  if t%self.c['routing_interval_s']:return
  for vid in sorted(running&set(self.conn.vehicle.getIDList())):
   if t-self.last.get(vid,-1e9)<self.c['cooldown_s']:continue
   edge=self.conn.vehicle.getRoadID(vid)
   if edge.startswith(':') or edge in closed:continue
   old=self.conn.vehicle.getRoute(vid)[self.conn.vehicle.getRouteIndex(vid):];new=plan(edge,old[-1])
   if new and list(old)!=new and self.eta(old,t)-self.eta(new,t)>=self.c['delay_threshold_s']:
    self.conn.vehicle.setRoute(vid,new);rid=routes[vid]['route_id'];self.record(vid,rid,t,old,new,'congestion_delay');route_plans['events'].append(dict(vehicle_id=vid,route_id=rid,event_time_s=t,kind='replanned',edge_ids=new,reason='congestion_delay'))
 def finalize(self,trips):
  for vid,row in self.rows.items():
   if row['status']=='arrived':
    trip=trips[vid];row['actual_depart_s']=float(trip['depart']);row['arrival_time_s']=float(trip['arrival'])
 def outputs(self):
  return {'traffic-state.json':dict(format='traffic-state-v1',**self.header,edge_samples=self.stats),'background-trajectories.json':dict(format='background-trajectories-v1',**self.header,samples=self.samples,vehicles=list(self.rows.values())),'routing-decisions.json':dict(format='routing-decisions-v1',**self.header,decisions=self.decisions)}
