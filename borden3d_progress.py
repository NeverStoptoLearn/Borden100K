"""Optional durable progress/checkpoints; disabled for existing entry points."""
from pathlib import Path
import os,json,time
import numpy as np


def progress(stage, **values):
    path=os.environ.get('BORDEN3D_PROGRESS_FILE')
    if not path:return
    record={'stage':stage,'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
            'pid':os.getpid(),**values}
    out=Path(path);out.parent.mkdir(parents=True,exist_ok=True)
    temp=out.with_suffix('.tmp');temp.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    # Windows readers may briefly deny FILE_SHARE_DELETE. Progress reporting
    # must not abort a scientific run because a monitor is reading the file.
    for attempt in range(50):
        try:
            os.replace(str(temp),str(out));break
        except PermissionError:
            time.sleep(.02)
    else:
        record['progress_write_warning']='Windows sharing conflict; latest JSON was not replaced'
    print(json.dumps(record,ensure_ascii=False),flush=True)


def checkpoint_torch(name, payload):
    folder=os.environ.get('BORDEN3D_CHECKPOINT_DIR')
    if not folder:return
    import torch
    out=Path(folder);out.mkdir(parents=True,exist_ok=True)
    target=out/(name+'.pt');temp=out/(name+'.tmp')
    torch.save(payload,str(temp));os.replace(str(temp),str(target))


def checkpoint_population(pop,F,V,history,rng):
    folder=os.environ.get('BORDEN3D_CHECKPOINT_DIR')
    if not folder:return
    out=Path(folder);out.mkdir(parents=True,exist_ok=True)
    temp=out/'evolution.tmp.npz';target=out/'evolution.npz'
    np.savez_compressed(temp,population=pop,F=F,V=V,
        history_json=json.dumps(history),random_state_json=json.dumps(rng.bit_generator.state))
    os.replace(str(temp),str(target))
