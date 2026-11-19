"""Scoped immutable deployment tasks at existing, validated operation points."""
import copy,hashlib,json
from uuid import uuid4
from pydantic import BaseModel,ConfigDict,Field
from .core import Problem,dump,validate

class CreateTask(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)
    manifest_sha256:str=Field(pattern=r'^[0-9a-f]{64}$')
    well_id:str
    depot_id:str
    priority:int=Field(default=1,ge=0)
    earliest_start_s:float=Field(default=0,ge=0)
    latest_stop_s:float=Field(default=86400,gt=0)
    unload_duration_s:float=Field(default=30,ge=0)
    install_duration_s:float=Field(default=60,ge=0)
    pump_duration_s:float=Field(default=3600,gt=0)
    extraction_rate_m3_day:float=Field(default=1,gt=0)

def digest(value):return hashlib.sha256(dump(value).encode()).hexdigest()

def check(entry,metadata):
    record=entry['record'];snapshot=entry['snapshot'];ref=record['task_ref'];ctx=metadata['context']
    if any(record.get(k)!=ctx[k] for k in ('scenario_id','scenario_version','seed','network_sha256')) or record['manifest_sha256']!=metadata['manifest_sha256'] or record['context_sha256']!=metadata['context_sha256']:
        raise Problem('ARTIFACT_INVALID','Custom task belongs to another scene snapshot',409)
    if ref!={'id':snapshot['id'],'version':snapshot['version'],'sha256':digest(snapshot)} or entry['canonical_snapshot']!=dump(snapshot):
        raise Problem('ARTIFACT_INVALID','Custom task snapshot SHA mismatch',409)
    task=snapshot['data'];validate(task,'task_request')
    if any(task[k]!=expected for k,expected in dict(required_item_id='pump',required_quantity=1,required_skill='pump-install').items()):
        raise Problem('ARTIFACT_INVALID','Unsupported custom deployment equipment/skill',409)
    if task['task_id']!=snapshot['id'] or snapshot['version']!=1 or not snapshot['source']['synthetic']:
        raise Problem('ARTIFACT_INVALID','Invalid custom task identity/source',409)
    operations=[o for o in ctx['facility_access']['tasks'] if o['well_id']==task['well_id']]
    if len(operations)!=1 or task['depot_id'] not in {d['depot_id'] for d in ctx['facility_access']['depots']}:
        raise Problem('ARTIFACT_INVALID','Unknown operation/depot for custom task',409)
    access=copy.deepcopy(operations[0]);access.update(task_id=task['task_id'],source_operation_task_id=operations[0]['task_id'])
    if record['access']!=access:raise Problem('ARTIFACT_INVALID','Custom operation mapping changed',409)
    return entry

def list_tasks(store,metadata):
    with store.connect() as db:rows=db.execute("SELECT body FROM snapshots WHERE kind='scenario_task' AND version=1 AND deleted=0 ORDER BY id").fetchall()
    entries=[]
    for row in rows:
        record=json.loads(row['body'])
        if record['scenario_id']!=metadata['context']['scenario_id'] or record['scenario_version']!=metadata['context']['scenario_version'] or record['manifest_sha256']!=metadata['manifest_sha256']:continue
        snapshot=store.get('tasks',record['task_ref']['id'],1)
        entries.append(check(dict(record=record,snapshot=snapshot,canonical_snapshot=dump(snapshot)),metadata))
    return entries

def create(store,metadata,body,create_snapshot):
    if body.manifest_sha256!=metadata['manifest_sha256']:raise Problem('VERSION_CONFLICT','Selected scene changed',409)
    ctx=metadata['context'];operations=[o for o in ctx['facility_access']['tasks'] if o['well_id']==body.well_id]
    if len(operations)!=1 or body.depot_id not in {d['depot_id'] for d in ctx['facility_access']['depots']}:
        raise Problem('INVALID_INPUT','Choose a published operation well and depot',409)
    axis=metadata['model_time_axis'];remaining=(axis['model_end_day']-axis['reference_model_day'])*86400
    if body.latest_stop_s<=body.earliest_start_s or body.latest_stop_s>remaining:
        raise Problem('INVALID_INPUT','Invalid task time window',409)
    ident='custom-'+uuid4().hex
    task=body.model_dump(exclude={'manifest_sha256'});task.update(task_id=ident,required_item_id='pump',required_quantity=1,required_skill='pump-install')
    validate(task,'task_request')
    source=dict(source_id='operator-task-v1',reference='Synthetic deployment at published operation; manifest '+metadata['manifest_sha256'],license='project synthetic task configuration',synthetic=True)
    snapshot=dict(id=ident,version=1,source=source,data=task)
    create_snapshot('tasks',snapshot)
    ref=dict(id=ident,version=1,sha256=digest(snapshot));access=copy.deepcopy(operations[0]);access.update(task_id=ident,source_operation_task_id=operations[0]['task_id'])
    record={k:ctx[k] for k in ('scenario_id','scenario_version','seed','network_sha256')}
    record.update(manifest_sha256=metadata['manifest_sha256'],context_sha256=metadata['context_sha256'],task_ref=ref,access=access)
    store.put('scenario_task',ident,1,record)
    return check(dict(record=record,snapshot=snapshot,canonical_snapshot=dump(snapshot)),metadata)
