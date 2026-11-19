"""Explicit observation and source-estimate contracts for chapters 3, 4 and 6.

Point estimates are not posterior samples. Chapter 6 may use them as declared
scenarios alongside chapter-4 posterior scenarios, never as extra evidence from
the same observations. Chapters 4 and 5 independently consume chapter 3.
This module does not import chapter 5.
"""
from pathlib import Path
import hashlib, json
import numpy as np
from borden3d_common import json_write


def observation_fingerprint(obs):
    h=hashlib.sha256()
    for name in ('time_indices','cell_indices','values','sigma_mg_L'):
        value=np.ascontiguousarray(obs[name],dtype='<i8' if name.endswith('indices') else '<f8')
        h.update(name.encode());h.update(str(value.shape).encode());h.update(value.tobytes())
    return h.hexdigest()


def read_source_estimates(path,spec,network_sha256,observation_sha256=None):
    path=Path(path);data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema')!='borden-source-estimates-v1':
        raise ValueError('External source estimates require the borden-source-estimates-v1 contract')
    if data.get('model_signature')!=spec.signature or data.get('network_sha256')!=network_sha256:
        raise ValueError('Source estimates belong to a different model or monitoring network')
    if observation_sha256 is not None and data.get('observation_sha256')!=observation_sha256:
        raise ValueError('Source estimates belong to a different observation dataset')
    points=np.asarray(data.get('source_estimates'),float)
    if points.ndim!=2 or points.shape[1]!=3 or not len(points) or not np.all(np.isfinite(points)):
        raise ValueError('source_estimates must contain finite [x, y, C_first] rows')
    if np.any(points<=spec.lower) or np.any(points>=spec.upper):
        raise ValueError('Source estimates must lie strictly inside the inference bounds')
    hydro=data.get('hydro_estimates')
    if hydro is not None and data.get('hydro_columns')!=spec.columns[3:]:
        raise ValueError('Hydro column order must match the joint model')
    if hydro is None:
        if data.get('hydro_assumption')!='nominal':
            raise ValueError('Declare nominal hydro or provide hydro_estimates explicitly')
        hydro=np.zeros((len(points),spec.dimension-3))
    hydro=np.asarray(hydro,float)
    if hydro.shape!=(len(points),spec.dimension-3) or not np.all(np.isfinite(hydro)):
        raise ValueError('hydro_estimates must match each source row and the shared hydro columns')
    return points,np.concatenate([points,hydro],axis=1),data


def export_source_estimates(path,spec,points,network_sha256,observation_sha256,
                            hydro=None,producer='external point estimator',origin_chapter=None):
    """Adapter for future chapter-5 outputs; does not execute chapter 5."""
    data={'schema':'borden-source-estimates-v1','model_signature':spec.signature,
          'network_sha256':network_sha256,'observation_sha256':observation_sha256,
          'source_estimates':np.atleast_2d(points).tolist(),'producer':producer,
          'probability_distribution':False,'hydro_columns':spec.columns[3:],'origin_chapter':origin_chapter}
    if hydro is None:data['hydro_assumption']='nominal'
    else:data['hydro_estimates']=np.atleast_2d(hydro).tolist()
    json_write(path,data)
    read_source_estimates(path,spec,network_sha256,observation_sha256)
    return data


def posterior_dataset_path(root,dataset_id=None):
    """Never silently select the first noise level or repetition."""
    root=Path(root);catalog=root/'posterior_catalog.json'
    if not catalog.exists():
        if dataset_id is not None:raise FileNotFoundError('Missing posterior dataset catalog')
        return root/'posterior_joint_samples.npz'
    records=json.loads(catalog.read_text(encoding='utf-8'))['datasets']
    if dataset_id is None:
        if len(records)!=1:
            raise ValueError('Multiple posterior datasets; specify --dataset-id: '+', '.join(r['dataset_id'] for r in records))
        record=records[0]
    else:
        matches=[r for r in records if r['dataset_id']==dataset_id]
        if len(matches)!=1:raise ValueError('Unknown or duplicate posterior dataset id: '+str(dataset_id))
        record=matches[0]
    path=(root/record['relative_path']).resolve()
    if root.resolve() not in path.parents:raise ValueError('Posterior path must remain inside its dataset root')
    return path
