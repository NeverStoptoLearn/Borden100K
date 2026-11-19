"""Executed in the selected module interpreter, never imported by ASGI."""
import json
import sys
from pathlib import Path
from .core import write, validate, Problem


def main():
    stage, bundle_path, output = sys.argv[1:]
    bundle = json.loads(Path(bundle_path).read_text('utf-8'))
    request, context = bundle['request'], bundle['context']
    if stage == 'preflight':
        from phase1.hydro_adapter.worker import capability, load_engine
        from phase1.hydro_adapter.adapter import ROOT, sha256
        build = capability(request['payload']['hydro_profile'])
        for key in ('code_sha256', 'config_sha256'):
            if request['model_build'][key] != build[key]:
                raise ValueError('Scenario scientific code/config hash mismatch')
        engine = load_engine(build)
        cfg = json.loads((ROOT / 'borden3d_config.json').read_text('utf-8'))
        scene = engine.BordenScene(profile=request['payload']['hydro_profile'], config=cfg)
        axis = request['payload']['time_axis']
        if not 0 <= axis['reference_model_day'] < axis['model_end_day'] == scene.src['end_day']:
            raise ValueError('Invalid original model time horizon')
        for well in request['payload']['wells']:
            cell = well['cell_id']
            xyz = [well['position'][k] for k in ('x_local_m', 'y_local_m', 'z_local_m')]
            import numpy as np
            if cell >= scene.n or scene.chd[cell] or not np.allclose(xyz, scene.xyz[cell], rtol=0, atol=1e-6):
                raise ValueError('Well mapping is not an active non-CHD cell at stated local position')
        write(output, dict(model_build=build, model_signature=scene.signature, control=cfg['control']))
        return
    if stage == 'traffic':
        from phase1.traffic.engine import run
        options=dict(time_axis=context['time_axis'])
        if context.get('dynamic_config') is not None:options['dynamic_config']=context['dynamic_config']
        result = run(request, context['network_path'], output, **options)
    elif stage == 'resources':
        from phase1.resources import schedule
        result = schedule(request, scenario_context=context)
        Path(output, request['run_id']).mkdir(parents=True)
        write(Path(output, request['run_id'], 'result.json'), result)
    elif stage == 'hydro':
        from phase1.hydro_adapter import run
        result = run(request, output, timeout_s=context['timeout_s'])
    else:
        raise ValueError('Unknown stage')
    validate(result)
    write(Path(output, request['run_id'], 'result.json'), result)
    if result['status'] != 'succeeded':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
