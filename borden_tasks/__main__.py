import argparse
import json
from pathlib import Path
from .core import TASKS,SCENES,prepare,run

def main():
    p=argparse.ArgumentParser(description='Borden100K Task Release bounded evaluation interfaces')
    sub=p.add_subparsers(dest='command',required=True);sub.add_parser('list')
    q=sub.add_parser('historical-source');q.add_argument('--path',type=Path,required=True);q.add_argument('--ledger',type=Path,required=True)
    q=sub.add_parser('prepare');q.add_argument('--scenario',choices=SCENES,required=True);q.add_argument('--output',type=Path,required=True)
    q=sub.add_parser('run');q.add_argument('--task',choices=TASKS,required=True);q.add_argument('--scenario',choices=SCENES,required=True)
    q.add_argument('--output',type=Path,required=True);q.add_argument('--seed',type=int,default=42)
    q.add_argument('--observations',type=Path);q.add_argument('--candidate',type=float,nargs=3);q.add_argument('--mode',default='maintenance_smoke')
    q.add_argument('--traffic-variant',choices=['normal','closure'],default='normal')
    q.add_argument('--traffic-result',type=Path);q.add_argument('--traffic-root',type=Path)
    a=p.parse_args()
    try:
        if a.command=='list':print(json.dumps(TASKS,indent=2));return 0
        if a.command=='historical-source':
            from .history import require_raw
            print(json.dumps(dict(status='available',path=str(require_raw(a.path,a.ledger)))));return 0
        if a.command=='prepare':print(json.dumps(dict(status='prepared',output=str(prepare(a.scenario,a.output)))));return 0
        r=run(a.task,a.scenario,a.output,seed=a.seed,observations=a.observations,candidate=a.candidate,mode=a.mode,traffic_variant=a.traffic_variant,traffic_result=a.traffic_result,traffic_root=a.traffic_root)
        print(json.dumps(dict(status=r['status'],task_id=a.task,run_id=r['run_id'],scenario_id=a.scenario)));return 0
    except Exception as e:print(json.dumps(dict(status='failed',code=getattr(e,'code','ERROR'),error=str(e))));return 2

if __name__=='__main__':raise SystemExit(main())
