"""Scene catalog endpoints and immutable snapshot assembly."""
import copy
import json
import threading
from uuid import uuid5,NAMESPACE_URL
from fastapi import Query
from pydantic import BaseModel,ConfigDict,Field
from phase1.scenarios.registry import IDS,VERSION,catalog,get_scene,read_asset,binding,verify_binding
from .core import Problem,dump

_LOCK=threading.RLock()
class LoadScene(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    manifest_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')

def install(app,store,create_scenario,create_snapshot):
    def put_same(kind,ident,version,value,callback):
        try:
            old=store.get(kind,ident,version)
        except Problem as exc:
            if exc.http!=404:raise
            try: callback()
            except Problem as race:
                if race.http!=409:raise
            old=store.get(kind,ident,version)
        expected=value if kind!='scenario' else value
        actual=old if kind!='scenario' else old['envelope']
        if dump(actual)!=dump(expected):raise Problem('VERSION_CONFLICT','Scene snapshot already has different content',409)
        return old

    @app.get('/api/v1/scene-catalog')
    def listing():
        summaries=[]
        for row in catalog()['scenes']:
            try:summary=get_scene(row['scenario_id'],row['scenario_version'])['scene']
            except (Problem,ValueError,TypeError,KeyError,OSError):
                summary=copy.deepcopy(row['summary'])
                summary['availability']={mode:dict(allowed=False,blockers=['ARTIFACT_INVALID: scene package unavailable']) for mode in ('normal','closure','no_pump')}
            summaries.append(summary)
        return dict(catalog_version=VERSION,scenes=summaries)

    @app.get('/api/v1/scene-catalog/{ident}/versions/{version}')
    def detail(ident:str,version:int):return get_scene(ident,version)

    @app.post('/api/v1/scene-catalog/{ident}/versions/{version}/load')
    def load(ident:str,version:int,body:LoadScene):
        with _LOCK:
            scene=get_scene(ident,version);m=scene['manifest'];fingerprint=scene['scene']['manifest_sha256']
            if body.manifest_sha256!=fingerprint:raise Problem('VERSION_CONFLICT','Selected scene changed',409)
            # A fresh installation must materialize registered predecessors;
            # Store deliberately forbids skipping immutable snapshot versions.
            first_registered=min(r['scenario_version'] for r in catalog()['scenes'] if r['scenario_id']==ident)
            if version>1 and not (version==1001 and first_registered==1001):
                try:store.get('scenario',ident,version)
                except Problem as exc:
                    if exc.http!=404:raise
                    previous=next((r for r in catalog()['scenes'] if r['scenario_id']==ident and r['scenario_version']==version-1),None)
                    if previous is None:raise Problem('VERSION_CONFLICT','Registered predecessor scene is required',409)
                    load(ident,version-1,LoadScene(manifest_sha256=previous['manifest']['sha256']))
            scenario=json.loads(read_asset(m['assets']['scenario_template']))
            template=json.loads(read_asset(m['assets']['parameters_template']))
            scenario['parameter_version']=f'{ident}.v{version}.no_pump.{fingerprint[:12]}'
            scenario['run_id']=str(uuid5(NAMESPACE_URL,f'dual:{ident}:{version}:{fingerprint}'))
            put_same('scenario',ident,version,scenario,lambda:create_scenario(scenario))
            parameters={}; pending_bindings=[]
            for mode in ('normal','closure','no_pump'):
                if not scene['scene']['availability'][mode]['allowed']:
                    parameters[mode]=None;continue
                data=copy.deepcopy(template[mode]);resources=data.get('resources')
                if resources is not None:
                    refs={}
                    for collection,items in resources.items():
                        refs[collection]=[]
                        for item in items:
                            field={'vehicles':'vehicle_id','crews':'crew_id','equipment':'equipment_id','tasks':'task_id'}.get(collection)
                            key=item[field] if field else item['depot_id']+'.'+item['item_id']
                            snapshot=dict(id=key,version=1,source=m['sources'][-1],data=item)
                            put_same(collection,key,1,snapshot,lambda c=collection,v=snapshot:create_snapshot(c,v))
                            refs[collection].append(dict(id=key,version=1))
                    data['resources']=refs
                param_id=f'{ident}.v{version}.{mode}.{fingerprint[:12]}'
                data['scenario']=dict(id=ident,version=version)
                snapshot=dict(id=param_id,version=1,source=m['sources'][-1],data=data)
                try:existing=store.get('parameters',param_id,1)
                except Problem as exc:
                    if exc.http!=404:raise
                    try:create_snapshot('parameters',snapshot)
                    except Problem as race:
                        if race.http!=409:raise
                        try:store.get('parameters',param_id,1)
                        except Problem:raise race
                    existing=store.get('parameters',param_id,1)
                from .app import Parameters
                expected_data=Parameters.model_validate(data).model_dump()
                if expected_data['network_path'] is not None: expected_data['network_path']='phase1/traffic/'+expected_data['network_path']
                if resources is not None:
                    expected_data['resource_references']=data['resources']
                    expected_data['resources']=resources
                expected_snapshot=dict(snapshot,data=expected_data)
                if dump(existing)!=dump(expected_snapshot):raise Problem('VERSION_CONFLICT','Parameter snapshot content differs',409)
                bind=dict(manifest_sha256=fingerprint,mode=mode,scenario=scenario,parameters=existing)
                pending_bindings.append((param_id,bind))
                parameters[mode]=existing
            with store.connect() as db:
                for key,value in pending_bindings:
                    old=db.execute("SELECT body,deleted FROM snapshots WHERE kind='scene_binding' AND id=? AND version=1",(key,)).fetchone()
                    if old and (old['deleted'] or old['body']!=dump(value)):raise Problem('VERSION_CONFLICT','Scene complete binding conflict',409)
                    if not old:db.execute('INSERT INTO snapshots VALUES(?,?,?,?,0)',('scene_binding',key,1,dump(value)))
            return dict(catalog_version=VERSION,scene=scene['scene'],scenario=scenario,parameters=parameters,display=scene['display'])

    @app.get('/api/v1/runs')
    def history(scenario_id:str=Query(...),scenario_version:int=Query(...,ge=1)):
        with store.connect() as db:rows=db.execute('SELECT body FROM jobs ORDER BY rowid DESC').fetchall()
        runs=[json.loads(r['body']) for r in rows]
        return dict(runs=[r for r in runs if r['scenario_id']==scenario_id and r['scenario_version']==scenario_version])

    from .planning import install as install_planning
    install_planning(app,store,load,create_snapshot,_LOCK)

def guard(store,request):
    if request['scenario_id'] not in IDS:return None
    scene=get_scene(request['scenario_id'],request['scenario_version'])
    gate=scene['scene']['availability'][request['payload']['mode']]
    if not gate['allowed']:raise Problem('INVALID_INPUT','SCENE_NOT_READY: '+'; '.join(gate['blockers']),409)
    try:b=store.get('scene_binding',request['parameter_version'],1)
    except Problem:raise Problem('INVALID_INPUT','Scene must be loaded before submission',409)
    if any(s['source_id']=='dispatch-context-v1' for s in scene['manifest']['sources']) and not b.get('planning'):
        raise Problem('INVALID_INPUT','CONFIGURATION_REQUIRED: explicitly select tasks, assignments and risk profile',409)
    if b['mode']!=request['payload']['mode'] or b['manifest_sha256']!=scene['scene']['manifest_sha256']:raise Problem('VERSION_CONFLICT','Scene parameter/mode binding mismatch',409)
    scenario=store.get('scenario',request['scenario_id'],request['scenario_version'])['envelope']
    params=store.get('parameters',request['parameter_version'],1)
    if b['scenario']!=scenario or b['parameters']!=params:raise Problem('VERSION_CONFLICT','Loaded snapshot binding changed',409)
    if request['seed']!=scene['manifest']['seed']:raise Problem('INVALID_INPUT','Scene seed differs',409)
    result=binding(request,scene,params);result['scenario']=scenario
    if b.get('planning'):result['planning']=copy.deepcopy(b['planning'])
    verify_binding(request,result);return result
