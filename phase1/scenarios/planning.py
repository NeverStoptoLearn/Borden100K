"""SHA-bound optional planning metadata; no changes to frozen messages."""
import json
import math
import re
from .registry import get_scene, read_asset
from phase1.contracts import Problem


def context_for(ident, version):
    scene=get_scene(ident,version);manifest=scene['manifest']
    sources=[s for s in manifest['sources'] if s['source_id']=='dispatch-context-v1']
    if len(sources)!=1:raise Problem('INVALID_INPUT','Planning context unavailable',404)
    expected=f'phase1/traffic/scenarios/{ident}/v{version}/scenario_context.json'
    match=re.fullmatch(re.escape(expected)+r'#sha256=([0-9a-f]{64})',sources[0]['reference'])
    if not match:raise Problem('ARTIFACT_INVALID','Invalid planning context reference',409)
    digest=match.group(1)
    try:value=json.loads(read_asset(dict(path=expected,sha256=digest,media_type='application/json')))
    except (ValueError,OSError) as exc:raise Problem('ARTIFACT_INVALID','Invalid planning context',409) from exc
    if value.get('format')!='scenario-context-v1' or any(value.get(k)!=manifest[k] for k in ('scenario_id','scenario_version','seed')):
        raise Problem('ARTIFACT_INVALID','Planning context identity mismatch',409)
    if value.get('network_sha256')!=(manifest['assets']['network'] or {}).get('sha256'):
        raise Problem('ARTIFACT_INVALID','Planning context network mismatch',409)
    def finite(v):
        if isinstance(v,float) and not math.isfinite(v):raise Problem('ARTIFACT_INVALID','Nonfinite planning value',409)
        if isinstance(v,dict):
            for item in v.values():finite(item)
        elif isinstance(v,list):
            for item in v:finite(item)
    finite(value)
    templates=json.loads(read_asset(manifest['assets']['parameters_template']))
    resources=(templates.get('normal') or {}).get('resources')
    if not resources or value.get('task_catalog')!=resources['tasks']:
        raise Problem('ARTIFACT_INVALID','Planning candidate tasks differ from immutable template',409)
    access=value.get('facility_access',{})
    for name,key,rows in [('depots','depot_id',resources['inventory']),('vehicles','vehicle_id',resources['vehicles']),('tasks','task_id',resources['tasks'])]:
        ids=[item.get(key) for item in access.get(name,[])]
        if len(ids)!=len(set(ids)) or set(ids)!={row[key] for row in rows}:
            raise Problem('ARTIFACT_INVALID','Planning access identity mismatch: '+name,409)
    return dict(scene=scene['scene'],manifest_sha256=scene['scene']['manifest_sha256'],context_sha256=digest,context_asset=dict(path=expected,sha256=digest,media_type='application/json'),
                context=value,resources=resources,model_time_axis=json.loads(read_asset(manifest['assets']['scenario_template']))['payload']['time_axis'],defaults=dict(risk_profile='control',traffic_profile='emergency_only'))


def dynamic_for(metadata):
    """Only read the published optional policy, never a client-selected path."""
    context=metadata['context'];reference=context.get('background_demand')
    expected=f"phase1/traffic/scenarios/{context['scenario_id']}/v{context['scenario_version']}/dynamic-config.json"
    if not isinstance(reference,dict) or reference.get('path')!=expected:
        raise Problem('INVALID_INPUT','DYNAMIC_TRAFFIC_NOT_AVAILABLE',409)
    try:policy=json.loads(read_asset(reference))
    except (ValueError,OSError) as exc:raise Problem('ARTIFACT_INVALID','Dynamic policy invalid',409) from exc
    if policy.get('format')!='dynamic-traffic-config-v1' or any(policy.get(k)!=context[k] for k in ('scenario_id','scenario_version','seed','network_sha256')):
        raise Problem('ARTIFACT_INVALID','Dynamic policy identity mismatch',409)
    return reference,policy
