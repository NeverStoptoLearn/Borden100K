"""Submit an explicit synthetic no-pump demonstration to a running API."""
import argparse
import copy
import json
import os
import subprocess
from uuid import uuid4
import httpx
from .core import ROOT
from .worker import SCIENCE


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',default='http://127.0.0.1:8000')
    parser.add_argument('--science-python',default=SCIENCE)
    parser.add_argument('--window',choices=['history','control'],default='history')
    args=parser.parse_args()
    code="from phase1.hydro_adapter.worker import capability,load_engine; from phase1.hydro_adapter.adapter import ROOT; import json; b=capability('smoke'); s=load_engine(b).BordenScene(profile='smoke',config=json.loads((ROOT/'borden3d_config.json').read_text('utf-8'))); print(json.dumps(dict(build=b,xyz=s.xyz[100].tolist())))"
    env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONIOENCODING='utf-8')
    env.pop('BORDEN3D_SCENE_CONFIG',None)
    probe=json.loads(subprocess.check_output([args.science_python,'-B','-c',code],cwd=ROOT,env=env,timeout=60))
    scenario=json.loads((ROOT/'schemas/examples/scenario.json').read_text('utf-8'))
    suffix=str(uuid4())
    ident='synthetic-'+suffix
    parameter='demo-parameters-'+suffix
    scenario.update(scenario_id=ident,parameter_version=parameter,run_id=str(uuid4()),model_build=probe['build'])
    scenario['data_provenance']=dict(classification='synthetic',calibration='none',fixture=False,sources=[dict(
        source_id='backend-demo',reference='Synthetic no-pump smoke; cell 100 position obtained from certified engine; uncalibrated',license='project synthetic benchmark',synthetic=True)])
    scenario['payload']['wells'][0]['position']=dict(zip(('x_local_m','y_local_m','z_local_m'),probe['xyz']))
    params=dict(id=parameter,version=1,source=scenario['data_provenance']['sources'][0],data=dict(
        scenario=dict(id=ident,version=1),risk_window_start_day=0.0 if args.window=='history' else 14600.0,risk_window_stop_day=21900.0))
    with httpx.Client(base_url=args.url,trust_env=False,timeout=90) as client:
        for path,data in (('/api/v1/scenarios',scenario),('/api/v1/parameters',params)):
            response=client.post(path,json=data)
            response.raise_for_status()
        request=dict(copy.deepcopy(scenario),kind='run_request',run_id=str(uuid4()),model_build=client.get('/api/v1/health').json()['model_build'],
                     payload=dict(mode='no_pump',idempotency_key='demo-'+str(uuid4())))
        response=client.post('/api/v1/runs',json=request,headers={'Idempotency-Key':request['payload']['idempotency_key']})
        response.raise_for_status()
        print(json.dumps(response.json(),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
