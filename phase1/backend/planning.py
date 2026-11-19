"""Explicit task selection into existing immutable parameter snapshots."""
import copy
import hashlib
import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from phase1.scenarios.planning import context_for,dynamic_for
from phase1.scenarios.registry import get_scene,read_asset
from .core import Problem,dump
from .custom_tasks import CreateTask,list_tasks,create as create_task


class Assignment(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    task_id:str
    vehicle_id:str


class Configuration(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    manifest_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    mode:Literal['normal','closure','no_pump']
    selected_task_ids:list[str]=Field(max_length=50)
    assignments:list[Assignment]=Field(max_length=50)
    closure_case_id:str|None=None
    risk_profile:Literal['control','history_validation']='control'
    traffic_profile:Literal['emergency_only','synthetic_background_small']='emergency_only'


def install(app,store,load_scene,create_snapshot,lock):
    @app.get('/api/v1/scene-catalog/{ident}/versions/{version}/planning')
    def planning(ident:str,version:int):
        metadata=context_for(ident,version);metadata['custom_tasks']=list_tasks(store,metadata);return metadata

    @app.post('/api/v1/scene-catalog/{ident}/versions/{version}/tasks',status_code=201)
    def new_task(ident:str,version:int,body:CreateTask):
        with lock:return create_task(store,context_for(ident,version),body,create_snapshot)

    @app.post('/api/v1/scene-catalog/{ident}/versions/{version}/configurations')
    def configure(ident:str,version:int,body:Configuration):
        with lock:
            metadata=context_for(ident,version);ctx=metadata['context'];resources=metadata['resources']
            if metadata['manifest_sha256']!=body.manifest_sha256:raise Problem('VERSION_CONFLICT','Selected scene changed',409)
            selected=body.selected_task_ids
            if len(selected)!=len(set(selected)):raise Problem('INVALID_INPUT','Duplicate task selection',409)
            tasks={t['task_id']:t for t in resources['tasks']}
            customs={e['snapshot']['id']:e for e in list_tasks(store,metadata)}
            tasks.update({key:entry['snapshot']['data'] for key,entry in customs.items()})
            if not set(selected)<=set(tasks):raise Problem('INVALID_INPUT','Unknown task in selected scene version',409)
            if len(selected)>len(resources['vehicles']):raise Problem('INVALID_INPUT','DISPATCH_CAPACITY_EXCEEDED: one dispatch per vehicle; choose at most '+str(len(resources['vehicles'])),409)
            assignments=[a.model_dump() for a in body.assignments]
            if len(assignments)!=len(selected) or {a['task_id'] for a in assignments}!=set(selected) or len({a['vehicle_id'] for a in assignments})!=len(assignments):
                raise Problem('INVALID_INPUT','Exactly one distinct vehicle per selected task is required',409)
            if body.mode=='no_pump':
                if selected or assignments or body.closure_case_id or body.traffic_profile!='emergency_only':raise Problem('INVALID_INPUT','no_pump requires empty traffic/resource selection',409)
            elif not selected:raise Problem('INVALID_INPUT','Select tasks explicitly',409)
            access=ctx['facility_access'];vehicles={v['vehicle_id']:v for v in access['vehicles']};operations={t['task_id']:t for t in access['tasks']}
            operations.update({key:entry['record']['access'] for key,entry in customs.items()})
            for item in assignments:
                vehicle=vehicles.get(item['vehicle_id']);task=tasks[item['task_id']]
                if vehicle is None or vehicle['depot_id']!=task['depot_id']:
                    raise Problem('INVALID_INPUT','DEPOT_ORIGIN_MISMATCH: assigned vehicle must start at task supply depot',409)
            cases={case['case_id']:case for case in ctx.get('closures',[])}
            if body.mode=='closure' and body.closure_case_id not in cases:raise Problem('INVALID_INPUT','Select a published closure case',409)
            if body.mode!='closure' and body.closure_case_id is not None:raise Problem('INVALID_INPUT','Closure case requires closure mode',409)
            dynamic_ref=dynamic_config=None
            if body.traffic_profile!='emergency_only':dynamic_ref,dynamic_config=dynamic_for(metadata)
            # Validate the whole selection before creating any snapshots or job.
            from .scenes import LoadScene
            loaded=load_scene(ident,version,LoadScene(manifest_sha256=body.manifest_sha256))
            manifest=get_scene(ident,version)['manifest']
            template=json.loads(read_asset(manifest['assets']['parameters_template']))
            data=copy.deepcopy(template[body.mode]);axis=loaded['scenario']['payload']['time_axis']
            window=[axis['reference_model_day'],axis['model_end_day']] if body.risk_profile=='control' else [0.,axis['model_end_day']]
            data.update(risk_window_start_day=float(window[0]),risk_window_stop_day=float(window[1]))
            selection=body.model_dump();selection['selected_task_ids']=sorted(selected);selection['assignments']=sorted(assignments,key=lambda a:a['task_id'])
            selection.update(scenario_id=ident,scenario_version=version,context_sha256=metadata['context_sha256'],network_sha256=ctx['network_sha256'],risk_window_model_day=window)
            selected_custom=[customs[key] for key in sorted(selected) if key in customs]
            if selected_custom:selection['custom_task_refs']=[entry['record']['task_ref'] for entry in selected_custom]
            if dynamic_ref:selection['dynamic_config_sha256']=dynamic_ref['sha256']
            digest=hashlib.sha256(dump(selection).encode()).hexdigest()
            if body.mode!='no_pump':
                data['traffic']['dispatches']=[dict(task_id=a['task_id'],vehicle_id=a['vehicle_id'],depart_s=i*10,
                    from_edge=vehicles[a['vehicle_id']]['edge_id'],to_edge=operations[a['task_id']]['edge_id']) for i,a in enumerate(selection['assignments'])]
                data['traffic']['closures']=copy.deepcopy(cases[body.closure_case_id]['closures']) if body.mode=='closure' else []
                # Reuse immutable resources created by the existing scene loader.
                expanded=loaded['parameters'][body.mode]['data']
                refs=copy.deepcopy(expanded['resource_references'])
                refs['tasks']=[ref for ref in refs['tasks'] if ref['id'] in selected]
                refs['tasks'].extend(dict(id=entry['snapshot']['id'],version=1) for entry in selected_custom)
                data['resources']=refs
            key=f'{ident}.v{version}.{body.mode}.{digest[:20]}'
            snapshot=dict(id=key,version=1,source=manifest['sources'][-1],data=data)
            try:parameter=store.get('parameters',key,1)
            except Problem as exc:
                if exc.http!=404:raise
                create_snapshot('parameters',snapshot);parameter=store.get('parameters',key,1)
            proof=dict(context=ctx,context_sha256=metadata['context_sha256'],configuration=selection,selection_sha256=digest)
            if selected_custom:proof['custom_tasks']=selected_custom
            if dynamic_ref:proof.update(dynamic_config=dynamic_config,dynamic_config_ref=dynamic_ref)
            bound=dict(manifest_sha256=body.manifest_sha256,mode=body.mode,scenario=loaded['scenario'],parameters=parameter,planning=proof)
            with store.connect() as db:
                old=db.execute("SELECT body,deleted FROM snapshots WHERE kind='scene_binding' AND id=? AND version=1",(key,)).fetchone()
                if old and (old['deleted'] or old['body']!=dump(bound)):raise Problem('VERSION_CONFLICT','Configuration binding conflict',409)
                if not old:db.execute('INSERT INTO snapshots VALUES(?,?,?,?,0)',('scene_binding',key,1,dump(bound)))
            return dict(scene=loaded['scene'],scenario=loaded['scenario'],parameter=parameter,configuration=selection,selection_sha256=digest)
