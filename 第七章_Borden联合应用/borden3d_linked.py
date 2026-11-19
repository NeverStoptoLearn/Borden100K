"""Linked chapters 3--6 on one versioned Borden uncertainty model.

Each stage requires signed upstream artifacts. Outputs are isolated from the
20261022 snapshots and historical results. Truth is used only to generate or
score synthetic observations, never to select wells or initialize inference.
"""
from pathlib import Path
import hashlib, json, os
import numpy as np
from borden3d_common import ROOT, json_write
from borden3d_joint import JointModel
from borden3d_evolution import MITree3D, canonical, select, nondominated, pool_coordinates, decode
from borden3d_sources import observation_fingerprint,read_source_estimates,export_source_estimates,posterior_dataset_path


def settings():
    path=Path(os.environ.get('BORDEN3D_UNCERTAINTY_CONFIG',str(ROOT/'borden3d_uncertainty_config.json')))
    return json.loads(path.read_text(encoding='utf-8'))


def location(profile,chapter,variant=None):
    root=Path(os.environ.get('BORDEN3D_LINKED_ROOT',str(ROOT/'Borden3D_linked_runs')))
    out=root/profile/('chapter%d'%chapter)
    if variant:out=out/variant
    out.mkdir(parents=True,exist_ok=True)
    return out


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def metadata(spec,**values):
    return {'schema':'borden-linked-v2','model_signature':spec.signature,'profile':spec.profile,
            'field_model':'finite-rank anisotropic squared-exponential log-Gaussian conductivity',
            'options':spec.options,'posterior_calibrated':False,'field_calibrated':False,**values}


def pool(spec,count):
    valid=np.flatnonzero(~spec.base.chd)
    return valid[np.unique(np.linspace(0,len(valid)-1,min(len(valid),count),dtype=int))]


def evolve(evaluate,initial,generations,rng,method='nsga2',repair=None,guide=None):
    if len(initial)<4 or generations<1:raise ValueError('population>=4, generations>=1 required')
    pop=initial.copy();history=[]
    for gen in range(generations):
        result=[evaluate(x) for x in pop];F=np.array([a for a,b in result]);V=np.array([b for a,b in result])
        if method in ('miteda','no_alignment'):
            elite,_,_=select(pop,F,V,max(4,len(pop)//2))
            model=MITree3D(pop.shape[1],use_alignment=method!='no_alignment');model.fit(elite)
            children=model.sample(len(pop),rng)+rng.normal(0,.035,pop.shape)
        elif method=='random':children=rng.random(pop.shape)
        else:
            children=[]
            for _ in pop:
                a,b=pop[rng.integers(len(pop),size=2)];u=rng.random(a.shape)
                beta=np.where(u<=.5,(2*u)**(1/16),(1/(2*(1-u)))**(1/16))
                child=.5*((1+beta)*a+(1-beta)*b)
                if guide is not None:child=guide(child,rng)
                children.append(child+rng.normal(0,.04,child.shape))
            children=np.array(children)
        children=np.clip(children,0,1)
        if repair:children=np.array([repair(x) for x in children])
        merged=np.concatenate([pop,children]);result=[evaluate(x) for x in merged]
        pop,F,V=select(merged,np.array([a for a,b in result]),np.array([b for a,b in result]),len(pop))
        history.append({'generation':gen+1,'feasible':int(np.sum(V<=1e-9))})
    ids=nondominated(F,V)
    if not len(ids):raise RuntimeError('No feasible front; inspect constraints or increase budget')
    return pop[ids],F[ids],V[ids],history


def run_monitor(profile='quick',population=24,generations=30,seed=42,algorithm='miteda',publish=True):
    cfg=settings();spec=JointModel(profile,cfg);base=spec.base
    out=location(profile,3,algorithm);candidates=pool(spec,int(spec.options['monitor_pool']))
    coords=pool_coordinates(base,candidates);slots=int(spec.options['monitor_count'])
    if slots<2 or slots>len(candidates):raise ValueError('Invalid monitor_count')
    bounds=np.asarray(base.cfg['inverse']['center_bounds_m'])
    centers=np.array([(x,y) for x in np.linspace(*bounds[0],3) for y in np.linspace(*bounds[1],3)])
    ti=np.searchsorted(base.times(),spec.observation_times)
    design_rows=spec.prior_rows(int(spec.options['design_scenarios']),spec.options['design_seed'])
    validation_rows=spec.prior_rows(int(spec.options['validation_scenarios']),spec.options['design_seed']+10000)
    def responses(rows):
        values=[]
        for row in rows:
            scene=spec.scene(row);block=[]
            for center in centers:
                sim=scene.simulate(center=center,end_day=spec.cutoff)
                block.append(sim['C'][ti][:,candidates].T)
            values.append(block)
        return np.asarray(values)
    R=responses(design_rows);sigma=cfg['monitor_noise_sd_mg_L']
    labels=np.tile(np.arange(len(centers)),len(R));pairs=np.where(np.triu(labels[:,None]!=labels[None,:],1))
    def evaluate(x):
        ids=decode(x,candidates,coords);chosen=np.unique(ids[x[:,-1]>.5]);cells=candidates[chosen]
        violation=max(0,2-len(cells))+int(np.sum(x[:,-1]>.5))-len(cells)
        if len(cells)>1:
            distance=np.linalg.norm(base.xyz[cells,None,:2]-base.xyz[cells,:2][None,:,:],axis=2)
            distance+=np.eye(len(cells))*1e20
            violation+=max(0,60-distance.min())/60.
        values=R[:,:,chosen,:].reshape(len(labels),-1)/sigma
        separation=np.min(np.linalg.norm(values[pairs[0]]-values[pairs[1]],axis=1)) if len(cells) else 0.
        cost=np.sum(1000+20*(base.cfg['geometry']['surface_m']-base.xyz[cells,2]))
        return np.array([-separation,cost]),float(violation)
    rng=np.random.default_rng(seed);initial=rng.random((population,slots,4));initial[:,:,-1]=1.
    # Deterministic geometric repair, using no concentration/truth information.
    def repair(x):
        x=canonical(x);ids=decode(x,candidates,coords);used=[]
        for j in range(slots):
            if x[j,-1]>.5 or len(used)<2:
                order=np.argsort(np.linalg.norm(coords-coords[ids[j]],axis=1))
                feasible=[i for i in order if all(np.linalg.norm(base.xyz[candidates[i],:2]-base.xyz[candidates[u],:2])>=60 for u in used)]
                if feasible:ids[j]=feasible[0];used.append(ids[j]);x[j,:3]=coords[ids[j]];x[j,-1]=1.
                else:x[j,-1]=0.
        return canonical(x)
    initial=np.array([repair(x) for x in initial])
    X,F,V,history=evolve(evaluate,initial,generations,rng,algorithm,repair)
    scaled=(F-F.min(axis=0))/np.maximum(np.ptp(F,axis=0),1e-12)
    best=int(np.argmin(np.sum(scaled**2,axis=1)))
    chosen=np.unique(decode(X[best],candidates,coords)[X[best,:,-1]>.5]);cells=candidates[chosen]
    network=out/'network.npz';np.savez_compressed(network,cells=cells,xyz=base.xyz[cells],X=X,F=F,V=V,candidate_cells=candidates)
    held=responses(validation_rows);a=held[:,:,chosen,:].reshape(len(validation_rows)*len(centers),-1)/sigma
    lab=np.tile(np.arange(len(centers)),len(validation_rows));i,j=np.where(np.triu(lab[:,None]!=lab[None,:],1))
    report=metadata(spec,method=algorithm,network_sha256=digest(network),network_path=str(network.resolve()),
        wells=len(cells),selected_cost=float(F[best,1]),selected_separation=float(-F[best,0]),
        validation_min_separation=float(np.min(np.linalg.norm(a[i]-a[j],axis=1))),seed=seed,history=history,
        observation_times_day=spec.observation_times.tolist(),selection_uses_truth=False,consumer_chapters=[4,5])
    json_write(out/'result.json',report)
    if publish:
        json_write(location(profile,3)/'selected_network.json',report)
        publish_observations(spec)
    return report


def load_network(spec,network=None):
    manifest=Path(network) if network else location(spec.profile,3)/'selected_network.json'
    if not manifest.exists():raise FileNotFoundError('Run chapter 3 first: missing '+str(manifest))
    meta=json.loads(manifest.read_text(encoding='utf-8'))
    if meta['model_signature']!=spec.signature:raise ValueError('Network model signature mismatch; rerun chapter 3')
    path=Path(meta['network_path'])
    if digest(path)!=meta['network_sha256']:raise ValueError('Network content hash mismatch')
    with np.load(path) as a:cells=a['cells'].copy();xyz=a['xyz'].copy()
    if len(np.unique(cells))!=len(cells) or np.any(cells<0) or np.any(cells>=spec.base.n):raise ValueError('Invalid network cells')
    if not np.allclose(spec.base.xyz[cells],xyz):raise ValueError('Network geometry mismatch')
    return cells,meta


def publish_observations(spec,network=None):
    """Chapter 3 publishes one signed, pre-decision observation bundle."""
    cells,meta=load_network(spec,network)
    row=spec.prior_rows(1,spec.options['truth_seed'])[0]
    truth=spec.scene(row);sim=truth.simulate(end_day=spec.cutoff)
    times=sim['times'];ti=np.searchsorted(times,spec.observation_times)
    tt,ww=np.meshgrid(ti,cells,indexing='ij');tt=tt.ravel();ww=ww.ravel()
    obs={'time_indices':tt,'cell_indices':ww,'values':sim['C'][tt,ww],'times':times,
         'network_sha256':np.array(meta['network_sha256']),'model_signature':np.array(spec.signature)}
    folder=location(spec.profile,3)/'observations'/meta['network_sha256'];folder.mkdir(parents=True,exist_ok=True)
    np.savez_compressed(folder/'clean.npz',**obs)
    np.savez_compressed(folder/'evaluation_only.npz',joint_truth=row)
    json_write(folder/'metadata.json',metadata(spec,network_sha256=meta['network_sha256'],
        observations_sha256=digest(folder/'clean.npz'),evaluation_sha256=digest(folder/'evaluation_only.npz'),
        observation_end_day=spec.cutoff,observation_times_day=spec.observation_times.tolist(),
        data_kind='synthetic observations at the optimized monitoring network'))
    return obs,row,meta


def clean_observations(spec,network=None):
    _,meta=load_network(spec,network)
    folder=location(spec.profile,3)/'observations'/meta['network_sha256']
    if not (folder/'metadata.json').exists():
        raise FileNotFoundError('Missing chapter-3 observation bundle; publish it by running chapter 3 first')
    saved=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    if saved['model_signature']!=spec.signature or saved['network_sha256']!=meta['network_sha256']:
        raise ValueError('Observation bundle model/network mismatch')
    if digest(folder/'clean.npz')!=saved['observations_sha256'] or digest(folder/'evaluation_only.npz')!=saved['evaluation_sha256']:
        raise ValueError('Observation bundle content hash mismatch')
    with np.load(folder/'clean.npz') as a:obs={k:a[k].copy() for k in a.files}
    with np.load(folder/'evaluation_only.npz') as a:row=a['joint_truth'].copy()
    if np.any(spec.base.times()[obs['time_indices']]>spec.cutoff):raise ValueError('Future observations in published bundle')
    return obs,row,meta


def noisy_observations(spec,clean,level,seed,fraction):
    from borden3d_uncertainty import noisy_observations as split_noise
    obs,held=split_noise(clean,level,seed,fraction)
    obs['network_sha256']=clean['network_sha256'];obs['model_signature']=clean['model_signature']
    if np.any(spec.base.times()[obs['time_indices']]>spec.cutoff):raise ValueError('Future observation leakage')
    return obs,held


def run_tsb(profile='quick',stage1=500,stage2=2000,width=48,device='cpu',seed=42,draws=128,repetitions=1,noise_levels=None,train_fraction=None,network=None,algorithm='tsbpinn'):
    import torch
    from borden3d_joint import train_joint_bayesian
    from borden3d_uncertainty import interval_summary
    from borden3d_bayesian import predict_draws
    cfg=settings();spec=JointModel(profile,cfg);clean,truth,netmeta=clean_observations(spec,network)
    out=location(profile,4,algorithm);levels=cfg['noise_levels'] if noise_levels is None else noise_levels
    fraction=cfg['observation_train_fraction'] if train_fraction is None else train_fraction
    if repetitions<1:raise ValueError('repetitions must be positive')
    records=[];catalog=[]
    for ni,level in enumerate(levels):
        for rep in range(repetitions):
            noise_seed=seed+1000*ni+rep
            obs,held=noisy_observations(spec,clean,level,noise_seed,fraction)
            folder=out/('noise_%g_rep_%02d'%(level,rep));folder.mkdir(parents=True,exist_ok=True)
            np.savez_compressed(folder/'observations.npz',**obs)
            fingerprint=observation_fingerprint(obs)
            model,guide,rows,history=train_joint_bayesian(spec,obs,0 if algorithm=='bpinn' else stage1,
                stage2,width,draws,device,seed+50000+rep,method=algorithm)
            path=folder/'posterior_joint_samples.npz'
            np.savez_compressed(path,joint=rows,theta=rows[:,:3],hydro=rows[:,3:],columns=np.array(spec.columns))
            meta=metadata(spec,columns=spec.columns,network_sha256=netmeta['network_sha256'],noise_seed=noise_seed,
                observations_sha256=digest(folder/'observations.npz'),observation_end_day=spec.cutoff,
                observation_sha256=fingerprint,dataset_id=folder.name,noise_level=float(level),repetition=rep,
                upstream_chapters=[3],chapter5_used=False,
                family='q(network) times full-covariance q(source and hydro); soft-physics generalized posterior',
                joint_source_hydro=True,algorithm=algorithm,bayesian_sampling=algorithm!='pinn',
                posterior_sha256=digest(path),
                stage1=0 if algorithm=='bpinn' else stage1,stage2=stage2,draws=draws)
            json_write(folder/'posterior_metadata.json',meta)
            export_source_estimates(folder/'source_estimates.json',spec,rows[:,:3].mean(axis=0),netmeta['network_sha256'],
                fingerprint,hydro=rows[:,3:].mean(axis=0),producer='chapter4 joint posterior mean; nominal scenario only',origin_chapter=4)
            catalog.append({'dataset_id':folder.name,'relative_path':str(path.relative_to(out)),
                            'noise_level':float(level),'repetition':rep,'observation_sha256':fingerprint})
            if not records and algorithm=='tsbpinn':
                np.savez_compressed(out/'posterior_joint_samples.npz',joint=rows,theta=rows[:,:3],hydro=rows[:,3:],columns=np.array(spec.columns))
                json_write(out/'posterior_metadata.json',{**meta,'posterior_sha256':digest(out/'posterior_joint_samples.npz')})
            torch.save({'network':model.state_dict(),'joint_guide':guide.state_dict(),'history':history,'metadata':meta},str(folder/'model.pt'))
            record={'noise_level':float(level),'repetition':rep,'history':history,
                    'joint_interval':interval_summary(rows,truth),'joint_mean':rows.mean(axis=0).tolist()}
            if len(held['values']):
                # Parameter posterior predictive uses each complete source-hydro row.
                pred=[]
                for row in rows:
                    sc=spec.scene(row);sim=sc.simulate(row[:2],row[2],smooth=True,end_day=spec.cutoff)
                    pred.append(sim['C'][held['time_indices'],held['cell_indices']])
                pred=np.asarray(pred);rng=np.random.default_rng(noise_seed+70000)
                predictive=pred+rng.normal(0,held['sigma_mg_L'],pred.shape)
                record['heldout_latent']=interval_summary(pred,held['clean_values'])
                record['heldout_noisy']=interval_summary(predictive,held['values'])
                np.savez_compressed(folder/'heldout_predictive.npz',latent=pred,predictive=predictive,**held)
            json_write(folder/'result.json',record);records.append(record)
    report=metadata(spec,method=algorithm,network_sha256=netmeta['network_sha256'],joint_source_hydro=True,
                    bayesian_sampling=algorithm!='pinn',results=records,environment_uncertainty=True,
                    upstream_chapters=[3],chapter5_used=False)
    json_write(out/'result.json',report)
    json_write(out/'posterior_catalog.json',{'model_signature':spec.signature,'datasets':catalog})
    return report


def load_posterior(spec,path=None,dataset_id=None,network=None):
    if path is not None and dataset_id is not None:raise ValueError('Select posterior path or dataset id, not both')
    path=Path(path) if path else posterior_dataset_path(location(spec.profile,4,'tsbpinn'),dataset_id)
    meta=json.loads(path.with_name('posterior_metadata.json').read_text(encoding='utf-8'))
    if digest(path)!=meta.get('posterior_sha256'):raise ValueError('Posterior content hash mismatch')
    if meta['model_signature']!=spec.signature or not meta.get('joint_source_hydro') or not meta.get('bayesian_sampling'):
        raise ValueError('A compatible joint source-hydro posterior is required')
    _,network_meta=load_network(spec,network)
    if meta['network_sha256']!=network_meta['network_sha256']:raise ValueError('Posterior uses a different monitoring network')
    with np.load(path) as a:
        rows=a['joint'].copy();columns=a['columns'].tolist()
    if columns!=spec.columns or rows.ndim!=2 or rows.shape[1]!=spec.dimension or not np.all(np.isfinite(rows)):
        raise ValueError('Invalid complete joint posterior rows')
    return rows,meta,path


def run_fpa(runner,profile='quick',iterations=30,inner_epochs=100,device='cpu',seed=42,repetitions=1,noise_levels=None,train_fraction=None,network=None,algorithm='fpapinn'):
    cfg=settings();spec=JointModel(profile,cfg);clean,truth,netmeta=clean_observations(spec,network)
    out=location(profile,5,algorithm);levels=cfg['noise_levels'] if noise_levels is None else noise_levels
    fraction=cfg['observation_train_fraction'] if train_fraction is None else train_fraction
    # A conditional point-inverse uses a declared nominal field, never hidden truth.
    nominal=truth.copy();nominal[3:]=0.;scene=spec.scene(nominal)
    records=[]
    for ni,level in enumerate(levels):
        for rep in range(repetitions):
            obs,held=noisy_observations(spec,clean,level,seed+1000*ni+rep,fraction)
            np.savez_compressed(out/('noise_%g_rep_%02d.npz'%(level,rep)),**obs)
            for fi,initial in enumerate(cfg['fpa_initial_fractions']):
                folder=out/('n%g_r%d_i%d'%(level,rep,fi))
                answer=runner(profile,iterations,inner_epochs,device,seed+60000,
                    observation_data=obs,output_dir=folder,initial_fraction=initial,
                    regularization=0. if algorithm=='no_regularization' else cfg['source_position_regularization'],
                    scene_override=scene,use_neural_gradient=algorithm!='discrete_adjoint',
                    use_variable_projection=algorithm!='no_vp',use_relaxation=algorithm!='no_km')
                err=np.linalg.norm((np.asarray(answer['source_estimate'])-truth[:3])/(spec.upper-spec.lower))
                records.append({'noise_level':float(level),'repetition':rep,'initial_fraction':initial,
                    'scaled_source_error':float(err),'source_estimate':answer['source_estimate'],
                    'reference_gradient_fallbacks':answer['reference_gradient_fallbacks'],
                    'neural_gradient_used':sum(int(h['neural_gradient_used']) for h in answer['history']),
                    'output':str(folder)})
    report=metadata(spec,method=algorithm,network_sha256=netmeta['network_sha256'],runs=records,
        environmental_model='same random-field truth and observations as chapter 4; nominal-field conditional point inverse',
        posterior_claimed=False,environment_uncertainty=True)
    json_write(out/'result.json',report);return report


def run_control(profile='quick',population=20,generations=20,seed=42,posterior=None,algorithm='hpnsgaii',
                source_mode='auto',source_estimates=None,dataset_id=None,network=None):
    requested_mode=source_mode
    if source_mode=='auto':source_mode='combined' if source_estimates is not None else 'posterior'
    if source_mode not in ('posterior','point','posterior_with_point_check','combined'):raise ValueError('Unknown source input mode')
    if (source_mode=='posterior') != (source_estimates is None):
        raise ValueError('Point, diagnostic or combined mode requires an explicit source-estimate artifact')
    cfg=settings();spec=JointModel(profile,cfg);rows,meta,path=load_posterior(spec,posterior,dataset_id,network)
    variant=algorithm+('' if source_mode=='posterior' else '__'+source_mode)+('' if dataset_id is None else '__'+dataset_id)
    out=location(profile,6,variant);opts=spec.options;base=spec.base;ctl=base.cfg['control']
    point_rows=None;point_meta=None
    if source_estimates:
        _,point_rows,point_meta=read_source_estimates(source_estimates,spec,meta['network_sha256'],meta['observation_sha256'])
        if source_mode=='point' and len(point_rows)!=1:raise ValueError('Select exactly one point for a nominal scenario')
        if source_mode=='combined' and point_meta.get('origin_chapter')!=5:
            raise ValueError('Combined control requires an explicitly identified chapter-5 result; chapter-4 means cannot substitute for it')
    nd=int(opts['posterior_design_draws']);nv=int(opts['posterior_validation_draws'])
    if min(nd,nv)<1 or len(rows)<nd+nv:raise ValueError('Insufficient disjoint joint posterior rows')
    rng=np.random.default_rng(seed+80000);ids=rng.permutation(len(rows));train=rows[ids[:nd]];test=rows[ids[nd:nd+nv]]
    # Nominal estimate uses training rows only, not validation rows.
    design=np.mean(train,axis=0,keepdims=True) if algorithm=='nominal' else train
    if source_mode=='point':design=point_rows.copy()
    scenes=[spec.scene(row) for row in design];held_scenes=[spec.scene(row) for row in test]
    point_scenes=[] if point_rows is None else [spec.scene(r) for r in point_rows]
    candidate=pool(spec,int(opts['control_pool']));coords=pool_coordinates(base,candidate)
    nw=int(opts['control_wells']);edges=np.array(ctl['period_edges_day']);periods=len(edges)-1
    constant=algorithm=='constant';genes=1 if constant else periods
    maxchange=float(opts['control_max_rate_change_fraction']);capacity=float(opts.get('control_capacity_m3_d',nw*ctl['max_rate_m3_d']))
    def rates(x):return np.repeat(x[:,3:],periods,axis=1) if constant else x[:,3:]
    def repair(x):
        x=np.clip(x,0,1);chosen=[];ix=decode(x,candidate,coords)
        for j in range(nw):
            ordered=np.argsort(np.linalg.norm(coords-coords[ix[j]],axis=1))
            feasible=[i for i in ordered if all(np.linalg.norm(base.xyz[candidate[i],:2]-base.xyz[candidate[k],:2])>=ctl['min_well_spacing_m'] for k in chosen)]
            if not feasible:raise ValueError('Candidate pool cannot accommodate well spacing')
            chosen.append(feasible[0]);x[j,:3]=coords[chosen[-1]]
        for t in range(1,genes):x[:,3+t]=np.clip(x[:,3+t],x[:,2+t]-maxchange,x[:,2+t]+maxchange)
        peak=np.max(rates(x).sum(axis=0))*ctl['max_rate_m3_d']
        if peak>capacity:x[:,3:]*=capacity/peak
        return x
    cache={}
    def evaluate_group(x,group,group_scenes):
        ix=decode(x,candidate,coords);q=rates(x)*ctl['max_rate_m3_d']
        cost=np.count_nonzero(np.max(q,axis=1)>1e-8)*ctl['construction_cost_cny']+float(np.sum(q*np.diff(edges)))*ctl['pumping_cost_cny_m3']
        schedule=[{'start_day':float(edges[t]),'end_day':float(edges[t+1]),
                   'pumps':[{'cell':int(candidate[i]),'rate':float(q[j,t])} for j,i in enumerate(ix)]} for t in range(periods)]
        mass=[];violations=[];draw=[];balance=[]
        for row,scene in zip(group,group_scenes):
            key=(tuple(row),tuple(ix),tuple(q.ravel()))
            if key not in cache:
                sim=scene.simulate(row[:2],row[2],pump_schedule=schedule)
                residual=float(np.dot(scene.water,sim['C'][-1])*1e-3)
                score=residual+sim['boundary_output_mass_kg']
                dd=sim['max_drawdown_m']
                err=abs(sim['net_source_input_mass_kg']-residual-sim['pumped_mass_kg']-sim['boundary_output_mass_kg'])
                cache[key]=(score,dd,err)
            score,dd,err=cache[key];mass.append(score);draw.append(dd);balance.append(err)
            violation=max(0.,dd-ctl['max_drawdown_m'])/ctl['max_drawdown_m']
            violation+=max(0.,q.sum(axis=0).max()-capacity)/max(capacity,1e-12)
            if periods>1:violation+=max(0.,np.abs(np.diff(q,axis=1)).max()/ctl['max_rate_m3_d']-maxchange)
            if len(ix)>1:
                distance=np.linalg.norm(base.xyz[candidate[ix],None,:2]-base.xyz[candidate[ix],:2][None,:,:],axis=2)+np.eye(nw)*1e20
                violation+=max(0.,ctl['min_well_spacing_m']-distance.min())/ctl['min_well_spacing_m']
            violations.append(violation)
        return cost,np.array(mass),np.array(violations),np.array(draw),np.array(balance)
    guide_rows=np.concatenate([design,point_rows]) if source_mode=='combined' else design
    guide_scenes=[spec.scene(r) for r in guide_rows]
    strength=[]
    for row,scene in zip(guide_rows,guide_scenes):
        strength.append(scene.simulate(row[:2],row[2],end_day=spec.cutoff)['C'][-1,candidate])
    strength=np.mean(strength,axis=0);weights=strength+max(float(strength.max())*.02,1e-8);weights/=weights.sum()
    rng=np.random.default_rng(seed);initial=rng.random((population,nw,3+genes));initial[:,:,3:]*=.15
    if algorithm not in ('nsga2','no_initialization'):
        for x in initial:x[:,:3]=coords[rng.choice(len(candidate),nw,replace=False,p=weights)]
    initial=np.array([repair(x) for x in initial])
    def guide(child,rng):
        ix=decode(child,candidate,coords)
        for j in range(nw):
            near=np.argsort(np.linalg.norm(coords-coords[ix[j]],axis=1))[:7]
            best=near[np.argmax(strength[near])]
            child[j,:3]+=.3*(coords[best]-child[j,:3])
            child[j,3:]+=.08*(strength[best]/max(strength.max(),1e-8)-child[j,3:])
        return child
    def evaluate(x):
        cost,mass,v,dd,bal=evaluate_group(x,design,scenes)
        risk=float(mass.mean()+cfg['control_variability_weight']*mass.std(ddof=0));violation=float(v.max())
        if source_mode=='combined':
            _,pm,pv,_,_=evaluate_group(x,point_rows,point_scenes)
            # Decision-level scenario safeguard, not posterior/data fusion:
            # both chapter-4 risk and chapter-5 point scenarios must be covered.
            risk=max(risk,float(pm.max()));violation=max(violation,float(pv.max()))
        return np.array([cost,risk]),violation
    X,F,V,history=evolve(evaluate,initial,generations,rng,'nsga2',repair,
        None if algorithm in ('nsga2','no_mutation') else guide)
    np.savez_compressed(out/'front.npz',X=X,F=F,V=V,pool=candidate,period_edges_day=edges)
    normalized=(F-F.min(axis=0))/np.maximum(np.ptp(F,axis=0),1e-12);best=int(np.argmin(np.sum(normalized**2,axis=1)))
    cost,mass,v,dd,balance=evaluate_group(X[best],test,held_scenes)
    point_check=None
    if source_mode in ('posterior_with_point_check','combined'):
        pc,pm,pv,pd,pb=evaluate_group(X[best],point_rows,point_scenes)
        point_check={'cost_cny':pc,'mass_kg':pm.tolist(),'constraint_violation':pv.tolist(),
                     'drawdown_m':pd.tolist(),'posterior_modified':False,'used_in_optimization':source_mode=='combined'}
    if source_mode=='combined':
        post_risk=[];fifth_mass=[];post_violation=[];fifth_violation=[]
        for x in X:
            _,m,pv,_,_=evaluate_group(x,design,scenes)
            _,pm,fv,_,_=evaluate_group(x,point_rows,point_scenes)
            post_risk.append(float(m.mean()+cfg['control_variability_weight']*m.std(ddof=0)))
            fifth_mass.append(float(pm.max()));post_violation.append(float(pv.max()));fifth_violation.append(float(fv.max()))
        np.savez_compressed(out/'combined_objective_components.npz',chapter4_risk=post_risk,
            chapter5_worst_mass=fifth_mass,chapter4_violation=post_violation,chapter5_violation=fifth_violation)
        np.savez_compressed(out/'chapter5_design_scenarios.npz',joint_scenarios=point_rows,columns=np.array(spec.columns))
    np.savez_compressed(out/'propagated_joint_samples.npz',design_joint=design,validation_joint=test,
        design_row_ids=ids[:nd] if source_mode!='point' and algorithm!='nominal' else np.array([],dtype=int),
        validation_row_ids=ids[nd:nd+nv],columns=np.array(spec.columns))
    np.savez_compressed(out/'selected_schedule.npz',cells=candidate[decode(X[best],candidate,coords)],
                        rates_m3_d=rates(X[best])*ctl['max_rate_m3_d'],period_edges_day=edges)
    report=metadata(spec,method=algorithm,history=history,posterior_path=str(path),posterior_sha256=digest(path),
        source_mode=source_mode,requested_source_mode=requested_mode,output_dir=str(out),dataset_id=meta.get('dataset_id'),
        required_upstream_chapters=[4,5],dual_inputs_used_in_optimization=source_mode=='combined',
        chapter5_status=('used_in_optimization' if source_mode=='combined' else 'diagnostic_only' if source_mode=='posterior_with_point_check'
                         else 'nominal_point_only' if source_mode=='point' else 'pending' if requested_mode=='auto' else 'not_requested_for_baseline'),
        workflow_complete=source_mode=='combined',
        dependency_graph={'3':[4,5],'4':[6],'5':[6]},
        combination_rule='max(chapter4 mean-plus-SD, chapter5 worst scenario); constraints enforced for both' if source_mode=='combined' else None,
        source_estimates_path=None if source_estimates is None else str(Path(source_estimates).resolve()),
        source_estimates_sha256=None if source_estimates is None else digest(source_estimates),
        source_estimate_producer=None if point_meta is None else point_meta.get('producer'),point_check=point_check,
        joint_rows_preserved=source_mode!='point' and algorithm!='nominal',
        source_posterior_fusion=False,extra_likelihood_from_point_estimate=False,
        cartesian_source_hydro_product=False,control_periods=periods,
        constant_rate_baseline=constant,selected_cost_cny=cost,heldout_mass_mean_kg=float(mass.mean()),
        heldout_mass_sd_kg=float(mass.std()),heldout_violation_fraction=float(np.mean(v>1e-9)),
        max_mass_balance_error_kg=float(balance.max()),heldout_max_drawdown_m=float(dd.max()),
        environmental_uncertainty=True,validation_scope='Disjoint samples of the same approximate posterior; not external field validation')
    json_write(out/'result.json',report);return report
