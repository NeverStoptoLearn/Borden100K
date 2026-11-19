"""Uncertainty experiments shared by the four Borden applications.

Hydro factors are deliberately finite stress tests, not field probabilities.
Observation noise is a specified synthetic Gaussian measurement model.
"""
from pathlib import Path
import copy,json,hashlib,os
import numpy as np
from borden3d_common import BordenScene,ROOT,read_config,prepare_dataset,load_observations,json_write
from borden3d_evolution import MITree3D,canonical,select,decode,pool_coordinates,nondominated
from borden3d_progress import progress,checkpoint_population

FOLDERS=['实验代码_01_MITEDA_三维监测优化','实验代码_02_TSBPINN_三维两阶段反演',
         '实验代码_03_FPAPINN_三维稳定反演','实验代码_04_HPNSGAII_三维应急调控']


def settings():
    cfg=json.loads(Path(os.environ.get('BORDEN3D_UNCERTAINTY_CONFIG',str(ROOT/'borden3d_uncertainty_config.json'))).read_text(encoding='utf-8'))
    if cfg.get('enabled') is not True:raise ValueError('Uncertainty configuration is disabled')
    return cfg


def output(index,profile):
    root=Path(os.environ.get('BORDEN3D_OUTPUT_ROOT',str(ROOT)))
    out=root/FOLDERS[index]/'results_uncertainty'/profile;out.mkdir(parents=True,exist_ok=True)
    return out


def provenance(scene,cfg):
    return {'scene_signature':scene.signature,'uncertainty_settings':cfg,
            'uncertainty_code_signature':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'code_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                ['borden3d_common.py','borden3d_uncertainty.py','borden3d_bayesian.py','borden3d_pinn.py','borden3d_evolution.py']},
            'profile':scene.profile,'scope':'Synthetic benchmark; full-grid accuracy and field calibration not asserted.'}


def conditional_scene(profile,factors):
    cfg=read_config();h=cfg['hydro'];k,r=map(float,factors)
    if k<=0 or r<=0:raise ValueError('Positive hydro factors required')
    # Uniformly scaling all K components can cancel from steady flux-driven
    # transport with equal CHD heads. Vary horizontal K while holding Kz fixed
    # so that vertical routing/anisotropy changes rather than only head scale.
    for key in ['kx_m_d','ky_m_d']:h[key]*=k
    for key in ['recharge_m_d','enhanced_recharge_m_d']:h[key]*=r
    # Each conditional solve remains deterministic; uncertainty lives in the
    # explicit ensemble and its evaluator, not in the PDE solver's random seed.
    return BordenScene(profile,cfg)


def clean_observations(scene):
    path=ROOT/'Borden3D_共享数据'/scene.profile
    try:return load_observations(path,scene)
    except (FileNotFoundError,ValueError,KeyError):
        prepare_dataset(scene,path);return load_observations(path,scene)


def noisy_observations(clean,level,seed,train_fraction=1.):
    if level<=0 or not 0<train_fraction<=1:raise ValueError('Positive noise and train_fraction in (0,1] required')
    cfg=settings();sigma=float(level)*cfg['observation_noise_reference_mg_L']
    rng=np.random.default_rng(seed);values=np.asarray(clean['values'])+rng.normal(0,sigma,len(clean['values']))
    # Noise and subset random streams are independent of optimizer seeds.
    split=np.random.default_rng(seed+100000).permutation(len(values))
    count=max(1,int(round(len(values)*train_fraction)));chosen=np.sort(split[:count]);hold=np.sort(split[count:])
    obs={'time_indices':np.asarray(clean['time_indices'])[chosen],'cell_indices':np.asarray(clean['cell_indices'])[chosen],
         'values':values[chosen],'sigma_mg_L':np.full(len(chosen),sigma),'times':np.asarray(clean['times']),
         'original_indices':chosen,'clean_values':np.asarray(clean['values'])[chosen]}
    held={'time_indices':np.asarray(clean['time_indices'])[hold],'cell_indices':np.asarray(clean['cell_indices'])[hold],
          'values':values[hold],'clean_values':np.asarray(clean['values'])[hold],'sigma_mg_L':np.full(len(hold),sigma),
          'original_indices':hold}
    return obs,held


def fixed_pool(scene,count):
    valid=np.flatnonzero(~scene.chd)
    return valid[np.unique(np.linspace(0,len(valid)-1,min(count,len(valid)),dtype=int))]


def evolve(evaluate,initial,generations,rng,kind='mit',guide=None):
    pop=initial.copy();history=[];model=MITree3D(pop.shape[1])
    for gen in range(generations):
        fv=[evaluate(x) for x in pop];F=np.array([x[0] for x in fv]);V=np.array([x[1] for x in fv])
        if kind=='mit':
            elite,_,_=select(pop,F,V,max(4,len(pop)//2));model.fit(elite)
            kids=model.sample(len(pop),rng)+rng.normal(0,.035,pop.shape)
            kids=np.array([canonical(x) for x in np.clip(kids,0,1)])
        else:
            kids=[]
            for _ in range(len(pop)):
                a,b=pop[rng.integers(len(pop),size=2)];u=rng.random(a.shape)
                beta=np.where(u<=.5,(2*u)**(1/16),(1/(2*(1-u)))**(1/16))
                child=.5*((1+beta)*a+(1-beta)*b)
                if guide is not None:child=guide(child,rng)
                kids.append(np.clip(child+rng.normal(0,.04,child.shape),0,1))
            kids=np.array(kids)
        merged=np.concatenate([pop,kids]);fv=[evaluate(x) for x in merged]
        pop,F,V=select(merged,np.array([x[0] for x in fv]),np.array([x[1] for x in fv]),len(pop))
        history.append({'generation':gen,'feasible':int(np.sum(V<=1e-9))})
        progress('evolution',generation=gen+1,generations=generations,feasible=int(np.sum(V<=1e-9)))
        checkpoint_population(pop,F,V,history,rng)
    front=nondominated(F,V)
    if not len(front):raise RuntimeError('No feasible design; inspect constraints or increase budget')
    return pop[front],F[front],V[front],history


def run_monitor_uncertainty(profile='quick',population=24,generations=30,seed=42):
    if settings().get('workflow')=='standalone-v2':
        from borden3d_standalone import run_monitor
        return run_monitor(profile,population,generations,seed)
    if settings().get('workflow')=='linked-v2':
        from borden3d_linked import run_monitor
        return run_monitor(profile,population,generations,seed)
    if population<4 or generations<1:raise ValueError('population>=4 and generations>=1 required')
    cfg=settings();base=BordenScene(profile);out=output(0,profile)
    scenes=[conditional_scene(profile,f) for f in cfg['hydro_design_factors']]
    held=[conditional_scene(profile,f) for f in cfg['hydro_validation_factors']]
    pool=fixed_pool(base,45 if profile=='smoke' else 120);coords=pool_coordinates(base,pool)
    centers=np.array([(x,y) for x in [200.,260.,320.] for y in [290.,365.,440.]])
    ti=[np.argmin(abs(base.times()-t)) for t in base.cfg['time']['observation_days']]
    def responses(group):
        result=[]
        for si,sc in enumerate(group):
            rows=[]
            for ci,c in enumerate(centers):
                progress('monitor_responses',hydro_index=si+1,hydro_count=len(group),source_index=ci+1,source_count=len(centers))
                rows.append(sc.simulate(center=c)['C'][ti][:,pool].T)
            result.append(rows)
        return np.asarray(result)
    response_cache=os.environ.get('BORDEN3D_MONITOR_CACHE')
    cache_key=hashlib.sha256(json.dumps({'signatures':[s.signature for s in scenes+held],
        'pool':pool.tolist(),'centers':centers.tolist(),'time_indices':[int(x) for x in ti]},sort_keys=True).encode()).hexdigest()
    cache_path=Path(response_cache)/(cache_key+'.npz') if response_cache else None
    if cache_path is not None and cache_path.exists():
        with np.load(cache_path) as saved:
            design=saved['design'];validation=saved['validation']
        progress('monitor_responses_cache_loaded',cache_key=cache_key)
    else:
        design=responses(scenes);validation=responses(held)
        if cache_path is not None:
            cache_path.parent.mkdir(parents=True,exist_ok=True)
            np.savez_compressed(cache_path,design=design,validation=validation)
    sigma=cfg['monitor_noise_sd_mg_L']
    # Arrays: hydro x source x screen x time. All hydro/source cross pairs
    # with different source labels enter the robust separation.
    def distance(R,chosen):
        a=R[:,:,chosen,:].reshape(R.shape[0]*len(centers),-1)/sigma
        label=np.tile(np.arange(len(centers)),R.shape[0]);i,j=np.where(np.triu(label[:,None]!=label[None,:],1))
        return float(np.sqrt(np.min(np.sum((a[i]-a[j])**2,axis=1)))) if len(chosen) else 0.
    def geometry(x):
        ix=decode(x,pool,coords);enabled=x[:,-1]>.5;chosen=np.unique(ix[enabled]);cells=pool[chosen]
        v=float(max(0,2-len(chosen))+np.sum(enabled)-len(chosen))
        if len(cells)>1:
            xy=base.xyz[cells,:2];d=np.linalg.norm(xy[:,None]-xy[None,:],axis=2)+np.eye(len(cells))*1e9
            v+=max(0.,60.-d.min())/60.
        return chosen,float(np.sum(1000+20*(222-base.xyz[cells,2]))),v
    fronts={};metrics={}
    for name,R in [('nominal',design[:1]),('robust',design)]:
        def evaluate(x):
            chosen,cost,v=geometry(x);return np.array([-distance(R,chosen),cost]),v
        rng=np.random.default_rng(seed);initial=np.array([canonical(x) for x in rng.random((population,5,4))])
        X,F,V,H=evolve(evaluate,initial,generations,rng)
        fronts[name]=(X,F,V);np.savez_compressed(out/(name+'_front.npz'),X=X,F=F,V=V,pool=pool)
        metrics[name]={'solutions':len(X),'history':H}
    # Cost matching is explicit: choose the nominal design nearest the robust
    # maximum-separation design's actual cost, and disclose the remaining gap.
    Xr,Fr,_=fronts['robust'];ir=int(np.argmin(Fr[:,0]));target=Fr[ir,1]
    Xn,Fn,_=fronts['nominal'];indices={'robust':ir,'nominal':int(np.argmin(abs(Fn[:,1]-target)))}
    for name in ['nominal','robust']:
        X,F,V=fronts[name];chosen,cost,_=geometry(X[indices[name]])
        R=design[:,:,chosen,:].reshape(-1,len(chosen)*len(ti));labels=np.tile(np.arange(len(centers)),len(scenes))
        rng=np.random.default_rng(seed+3000);success=0;total=0
        for hs in validation:
            for p,truth in enumerate(hs[:,chosen,:].reshape(len(centers),-1)):
                for _ in range(20):
                    y=truth+rng.normal(0,sigma,len(truth));guess=labels[np.argmin(np.sum((R-y)**2,axis=1))]
                    success+=int(guess==p);total+=1
        metrics[name].update({'selected_cost_cny':cost,'design_cross_separation':distance(design,chosen),
            'heldout_cross_separation':distance(validation,chosen),'synthetic_classification_accuracy':success/total,
            'classification_trials':total,'heldout_hydro_count':len(held)})
        np.savetxt(out/(name+'_selected_wells.csv'),base.xyz[pool[chosen]],delimiter=',',header='x_m,y_m,z_screen_m',comments='')
    np.savez_compressed(out/'responses.npz',design=design,validation=validation,centers=centers,pool=pool)
    report={**provenance(base,cfg),'method':'MITEDA cross-hydro/source noise-whitened robust design','environment_uncertainty':True,
        'noise_sd_mg_L':sigma,'cost_matching_gap_cny':abs(metrics['nominal']['selected_cost_cny']-metrics['robust']['selected_cost_cny']),
        'comparison':metrics,'guarantee_scope':'Minimum distance over specified finite hypotheses only; no continuum or field-probability guarantee.'}
    json_write(out/'result.json',report);print(out);return report


def interval_summary(draws,truth,alpha=.05):
    lo,hi=np.quantile(draws,[alpha/2,1-alpha/2],axis=0)
    score=hi-lo+2/alpha*np.maximum(lo-truth,0)+2/alpha*np.maximum(truth-hi,0)
    return {'lower':lo.tolist(),'upper':hi.tolist(),'coverage_fraction':float(np.mean((truth>=lo)&(truth<=hi))),
            'mean_width':float(np.mean(hi-lo)),'mean_interval_score':float(np.mean(score))}


def run_tsb_uncertainty(profile='quick',stage1=500,stage2=2000,width=48,device='cpu',seed=42,draws=128,repetitions=1,noise_levels=None,train_fraction=None,network=None):
    if settings().get('workflow')=='standalone-v2':
        from borden3d_standalone import run_tsb
        return run_tsb(profile,stage1,stage2,width,device,seed,draws,repetitions,noise_levels,train_fraction,network=network)
    if settings().get('workflow')=='linked-v2':
        from borden3d_linked import run_tsb
        return run_tsb(profile,stage1,stage2,width,device,seed,draws,repetitions,noise_levels,train_fraction,
                       network=network)
    if network is not None:raise ValueError('Source interfaces require linked-v2')
    import torch
    from borden3d_bayesian import train_bayesian,predict_draws
    if repetitions<1:raise ValueError('repetitions>=1 required')
    cfg=settings();scene=BordenScene(profile);clean=clean_observations(scene);out=output(1,profile)
    levels=cfg['noise_levels'] if noise_levels is None else list(noise_levels)
    fraction=cfg['observation_train_fraction'] if train_fraction is None else train_fraction
    records=[];parameter_hits=[]
    truth=np.r_[scene.src['center_xy_m'],scene.src['first_concentration_mg_L']]
    for ni,level in enumerate(levels):
        for rep in range(repetitions):
            progress('tsb_dataset',noise_level=level,repetition=rep)
            noise_seed=seed+1000*ni+rep;algorithm_seed=seed+50000+rep
            obs,held=noisy_observations(clean,level,noise_seed,fraction)
            folder=out/('noise_%g_rep_%02d'%(level,rep));folder.mkdir(exist_ok=True)
            np.savez_compressed(folder/'observations.npz',**obs)
            model,guide,theta,pred,history=train_bayesian(scene,obs,cfg,stage1,stage2,width,draws,device,algorithm_seed)
            np.savez_compressed(folder/'posterior_joint_samples.npz',theta=theta,columns=np.array(['x_center_m','y_center_m','C_first_mg_L']))
            joint_meta={**provenance(scene,cfg),'noise_seed':noise_seed,'algorithm_seed':algorithm_seed,'noise_level':level,
                'noise_sd_mg_L':float(obs['sigma_mg_L'][0]),'stage1':stage1,'stage2':stage2,'draws':draws,
                'posterior_family':'q(weights) diagonal Gaussian times q(raw_source) full-covariance Gaussian; bounded logistic source',
                'conditional_hydro':'fixed nominal K and recharge','source_model':'smooth top-source soft-constraint generalized posterior',
                'calibration_validated':False,'research_performance_validated':False}
            json_write(folder/'posterior_metadata.json',joint_meta)
            if not records:
                np.savez_compressed(out/'posterior_joint_samples.npz',theta=theta,columns=np.array(['x_center_m','y_center_m','C_first_mg_L']))
                json_write(out/'posterior_metadata.json',joint_meta)
            torch.save({'network':model.state_dict(),'source_guide':guide.state_dict(),'width':width,'metadata':joint_meta},str(folder/'model.pt'))
            lo,hi=np.quantile(theta,[.025,.975],axis=0)
            parameters={'lower':lo.tolist(),'upper':hi.tolist(),'width_per_parameter':(hi-lo).tolist()}
            parameter_hits.append(((truth>=lo)&(truth<=hi)).astype(int))
            record={'noise_level':level,'noise_seed':noise_seed,'algorithm_seed':algorithm_seed,'repetition':rep,
                'train_observations':len(obs['values']),'heldout_observations':len(held['values']),
                'source_mean':theta.mean(axis=0).tolist(),'source_covariance':np.cov(theta.T).tolist(),'source_95_interval':parameters,
                'train_rmse_to_noisy_mg_L':float(np.sqrt(np.mean((pred.mean(axis=0)-obs['values'])**2))),
                'source_interval_contains_truth':parameter_hits[-1].tolist(),'history':history}
            if len(held['values']):
                latent=predict_draws(scene,model,held['time_indices'],held['cell_indices'],draws,device)
                rng=np.random.default_rng(noise_seed+70000);future=latent+rng.normal(0,held['sigma_mg_L'],latent.shape)
                record['heldout_latent']=interval_summary(latent,held['clean_values'])
                record['heldout_noisy_predictive']=interval_summary(future,held['values'])
                np.savez_compressed(folder/'heldout_predictive.npz',latent=latent,predictive=future,**held)
            # Diagnostic only: evaluate a posterior center with both physical
            # operators to expose, rather than hide, the inverse approximation.
            mean=theta.mean(axis=0);hard=scene.simulate(mean[:2],mean[2]);soft=scene.simulate(mean[:2],mean[2],smooth=True)
            hv=hard['C'][obs['time_indices'],obs['cell_indices']];sv=soft['C'][obs['time_indices'],obs['cell_indices']]
            record['hard_smooth_forward_rmse_mg_L']=float(np.sqrt(np.mean((hv-sv)**2)))
            record['neural_smooth_forward_rmse_mg_L']=float(np.sqrt(np.mean((pred.mean(axis=0)-sv)**2)))
            json_write(folder/'result.json',record);records.append(record)
    groups=[]
    for level in levels:
        rows=[r for r in records if r['noise_level']==level]
        groups.append({'noise_level':level,'datasets':len(rows),
            'source_interval_coverage_per_parameter':np.mean([r['source_interval_contains_truth'] for r in rows],axis=0).tolist(),
            'source_mean_width_per_parameter':np.mean([r['source_95_interval']['width_per_parameter'] for r in rows],axis=0).tolist()})
    report={**provenance(scene,cfg),'method':'two-stage variational Bayesian 3-D PINN','environment_uncertainty':True,'bayesian_sampling':True,
        'joint_source_draws_saved':True,'repetitions_per_noise':repetitions,'coverage_by_noise':groups,'results':records,
        'coverage_interpretation':'Per-dataset containment and heldout concentration coverage; small repetitions do not establish calibration.',
        'conditional_hydro_warning':'HP propagation adds hydro stress tests independently; this is not a jointly inferred source-hydro posterior.'}
    json_write(out/'result.json',report);print(out);return report


def run_fpa_uncertainty(runner,profile='quick',iterations=30,inner_epochs=100,device='cpu',seed=42,repetitions=1,noise_levels=None,train_fraction=None):
    if settings().get('workflow')=='standalone-v2':
        from borden3d_standalone import run_fpa
        return run_fpa(runner,profile,iterations,inner_epochs,device,seed,repetitions,noise_levels,train_fraction)
    if settings().get('workflow')=='linked-v2':
        from borden3d_linked import run_fpa
        return run_fpa(runner,profile,iterations,inner_epochs,device,seed,repetitions,noise_levels,train_fraction)
    if repetitions<1:raise ValueError('repetitions>=1 required')
    cfg=settings();scene=BordenScene(profile);clean=clean_observations(scene);out=output(2,profile)
    levels=cfg['noise_levels'] if noise_levels is None else list(noise_levels)
    fraction=cfg['observation_train_fraction'] if train_fraction is None else train_fraction
    truth=np.r_[scene.src['center_xy_m'],scene.src['first_concentration_mg_L']]
    inv=scene.cfg['inverse'];scale=np.r_[np.diff(np.array(inv['center_bounds_m']),axis=1).ravel(),np.diff(inv['concentration_bounds_mg_L'])]
    # Known hydro mismatch is generated separately and inverted with the nominal
    # model. It is not called an inferred hydrological uncertainty distribution.
    mismatch=conditional_scene(profile,cfg['fpa_mismatch_factors']).simulate()
    mismatched=dict(clean);mismatched['values']=mismatch['C'][clean['time_indices'],clean['cell_indices']]
    smooth=scene.simulate(smooth=True)
    approximation_error=float(np.sqrt(np.mean((smooth['C'][clean['time_indices'],clean['cell_indices']]-clean['values'])**2)))
    records=[]
    for condition,source in [('matched',clean),('hydro_mismatch',mismatched)]:
        for ni,level in enumerate(levels):
            for rep in range(repetitions):
                noise_seed=seed+1000*ni+rep;obs,_=noisy_observations(source,level,noise_seed,fraction)
                dataset=out/(condition+'_noise_%g_rep_%02d.npz'%(level,rep));np.savez_compressed(dataset,**obs)
                for fi,initial_fraction in enumerate(cfg['fpa_initial_fractions']):
                    progress('fpa_fit',condition=condition,noise_level=level,repetition=rep,initial_fraction=initial_fraction)
                    folder=out/('%s_n%g_r%d_i%d'%(condition,level,rep,fi))
                    result=runner(profile,iterations,inner_epochs,device,seed+60000,
                        observation_data=obs,output_dir=folder,initial_fraction=initial_fraction,
                        regularization=cfg['source_position_regularization'])
                    error=float(np.linalg.norm((np.array(result['source_estimate'])-truth)/scale))
                    records.append({'condition':condition,'noise_level':level,'noise_seed':noise_seed,'repetition':rep,
                        'initial_fraction':initial_fraction,'algorithm_seed':seed+60000,'scaled_parameter_error':error,
                        'observations':len(obs['values']),
                        'success':bool(error<=cfg['fpa_success_scaled_error']),'reference_gradient_fallbacks':result['reference_gradient_fallbacks'],
                        'iterations':len(result['history']),'accepted_updates':sum(int(x['accepted']) for x in result['history']),
                        'source_estimate':result['source_estimate'],'output_folder':str(folder)})
    # Distinct groups keep environmental/data changes separate from initial states.
    groups=[]
    for condition in ['matched','hydro_mismatch']:
        for level in levels:
            rows=[r for r in records if r['condition']==condition and r['noise_level']==level]
            errors=np.array([r['scaled_parameter_error'] for r in rows])
            groups.append({'condition':condition,'noise_level':level,'noise_repetitions':repetitions,'initializations_per_noise':len(cfg['fpa_initial_fractions']),
                'median_error':float(np.median(errors)),'max_error':float(errors.max()),'success_fraction':float(np.mean([r['success'] for r in rows]))})
    report={**provenance(scene,cfg),'method':'FPA-PINN perturbation stability experiment','environment_uncertainty':True,'posterior_claimed':False,
        'hard_smooth_at_nominal_truth_rmse_mg_L':approximation_error,
        'groups':groups,'runs':records,'success_threshold_scaled_error':cfg['fpa_success_scaled_error'],
        'interpretation':'Initializations are nested within noise realizations, not independent environmental samples; small runs test execution only.'}
    json_write(out/'result.json',report);print(out);return report


def run_control_uncertainty(profile='quick',population=20,generations=20,seed=42,posterior=None,
                            source_mode='auto',source_estimates=None,dataset_id=None,network=None):
    if settings().get('workflow')=='standalone-v2':
        from borden3d_standalone import run_control
        return run_control(profile,population,generations,seed,posterior,source_mode=source_mode,
                           source_estimates=source_estimates,dataset_id=dataset_id,network=network)
    if settings().get('workflow')=='linked-v2':
        from borden3d_linked import run_control
        return run_control(profile,population,generations,seed,posterior,source_mode=source_mode,
                           source_estimates=source_estimates,dataset_id=dataset_id,network=network)
    if source_mode not in ('auto','posterior') or any(x is not None for x in (source_estimates,dataset_id,network)):
        raise ValueError('Source interfaces require linked-v2')
    if population<4 or generations<1:raise ValueError('population>=4 and generations>=1 required')
    cfg=settings();base=BordenScene(profile);out=output(3,profile)
    posterior=Path(posterior) if posterior else output(1,profile)/'posterior_joint_samples.npz'
    with np.load(posterior) as a:theta=np.asarray(a['theta'],float)
    meta_path=posterior.with_name('posterior_metadata.json');meta=json.loads(meta_path.read_text(encoding='utf-8'))
    if meta.get('scene_signature')!=base.signature:raise ValueError('Posterior belongs to a different scene/code; rerun TSB uncertainty mode')
    if theta.ndim!=2 or theta.shape[1]!=3 or not np.all(np.isfinite(theta)):raise ValueError('Invalid joint source sample array')
    ntrain=cfg['posterior_design_draws'];ntest=cfg['posterior_validation_draws']
    if len(theta)<ntrain+ntest:raise ValueError('Insufficient disjoint posterior rows')
    rng=np.random.default_rng(seed+80000);rows=rng.permutation(len(theta));train_ids=rows[:ntrain];test_ids=rows[ntrain:ntrain+ntest]
    train_theta=theta[train_ids];test_theta=theta[test_ids];nominal=theta.mean(axis=0)
    scenes=[conditional_scene(profile,f) for f in cfg['hydro_design_factors']]
    held=[conditional_scene(profile,f) for f in cfg['hydro_validation_factors']]
    pool=fixed_pool(base,30 if profile=='smoke' else 80);coords=pool_coordinates(base,pool);ctl=base.cfg['control'];cache={}
    t0=int(np.argmin(abs(base.times()-ctl['start_day'])))
    # The physical guide uses inferred source samples, not a hidden true source.
    unpumped=[base.simulate(v[:2],v[2]) for v in train_theta]
    strength=np.mean([r['C'][t0,pool] for r in unpumped],axis=0)
    future=np.mean([r['C'][min(t0+1,len(r['times'])-1),pool]-r['C'][t0,pool] for r in unpumped],axis=0)
    weights=strength+np.maximum(future,0)+max(strength.max()*.02,1e-8);weights/=weights.sum()
    def evaluate_group(x,group,sources):
        ix=decode(x,pool,coords);rates=x[:,-1]*ctl['max_rate_m3_d']
        pumps=[{'cell':int(pool[i]),'rate':float(q)} for i,q in zip(ix,rates) if q>1e-8]
        cost=len(pumps)*ctl['construction_cost_cny']+float(rates.sum())*(ctl['end_day']-ctl['start_day'])*ctl['pumping_cost_cny_m3']
        masses=[];violations=[];max_draw=[]
        spacing=np.linalg.norm(base.xyz[pool[ix[0]],:2]-base.xyz[pool[ix[1]],:2])
        geometric=max(0.,ctl['min_well_spacing_m']-spacing)/ctl['min_well_spacing_m']
        for sc in group:
            for th in sources:
                # Cache keys include the entire joint source vector and hydro
                # signature; source marginals are never independently shuffled.
                key=(sc.signature,tuple(th),tuple(ix),tuple(rates))
                if key not in cache:
                    sim=sc.simulate(th[:2],th[2],pumps=pumps)
                    mass=float(np.dot(sc.water,sim['C'][-1])*1e-3)
                    draw=float(np.max(sc.base['head']-sim['pumped_system']['head'])) if sim['pumped_system'] is not None else 0.
                    cache[key]=(mass,draw)
                    if len(cache)%10==0:progress('control_forward_solves',cached_source_hydro_controls=len(cache))
                mass,draw=cache[key];masses.append(mass);max_draw.append(draw)
                violations.append(max(0.,draw-ctl['max_drawdown_m'])/ctl['max_drawdown_m']+geometric)
        return cost,np.array(masses),np.array(violations),np.array(max_draw)
    def guide(child,rng):
        ix=decode(child,pool,coords)
        for s in range(len(child)):
            near=np.argsort(np.linalg.norm(coords-coords[ix[s]],axis=1))[:7]
            best=near[np.argmax(strength[near]+np.maximum(future[near],0))]
            if rng.random()<.7:child[s,:3]+=.5*(coords[best]-child[s,:3])
            child[s,-1]+=.15*(strength[best]/max(strength.max(),1e-8)-child[s,-1])
        return child
    metrics={};fronts={}
    for name,group,sources in [('nominal',[base],nominal[None,:]),('robust',scenes,train_theta)]:
        def evaluate(x):
            cost,mass,v,draw=evaluate_group(x,group,sources)
            score=float(mass.mean()+cfg['control_variability_weight']*mass.std(ddof=0)) if name=='robust' else float(mass[0])
            return np.array([cost,score]),float(v.max())
        rng=np.random.default_rng(seed);initial=rng.random((population,2,4))
        for x in initial:x[:,:3]=coords[rng.choice(len(pool),2,replace=False,p=weights)]
        X,F,V,H=evolve(evaluate,initial,generations,rng,'nsga',guide)
        np.savez_compressed(out/(name+'_front.npz'),X=X,F=F,V=V,pool=pool)
        fronts[name]=(X,F,V);metrics[name]={'history':H,'solutions':len(X)}
    X,F,_=fronts['robust'];ri=int(np.argmin(F[:,1]));target=F[ri,0]
    indices={'robust':ri,'nominal':int(np.argmin(abs(fronts['nominal'][1][:,0]-target)))}
    for name in ['nominal','robust']:
        X,F,V=fronts[name];x=X[indices[name]]
        cost,mass,v,draw=evaluate_group(x,held,test_theta)
        dc,dm,dv,dd=evaluate_group(x,scenes,train_theta)
        metrics[name].update({'selected_cost_cny':cost,'design_mass_mean_kg':float(dm.mean()),'design_mass_sd_kg':float(dm.std()),
            'design_max_constraint_violation':float(dv.max()),'heldout_mass_mean_kg':float(mass.mean()),'heldout_mass_sd_kg':float(mass.std()),
            'heldout_mass_max_kg':float(mass.max()),'heldout_constraint_violation_fraction':float(np.mean(v>1e-9)),
            'heldout_max_drawdown_m':float(draw.max()),'heldout_scenarios':len(mass)})
        np.savez_compressed(out/(name+'_heldout.npz'),mass_kg=mass,violation=v,drawdown_m=draw)
        ix=decode(x,pool,coords)
        np.savetxt(out/(name+'_selected_pumping_wells.csv'),np.c_[base.xyz[pool[ix]],x[:,-1]*ctl['max_rate_m3_d']],delimiter=',',header='x_m,y_m,z_screen_m,Q_m3_d',comments='')
    np.savez_compressed(out/'propagated_joint_samples.npz',design_theta=train_theta,validation_theta=test_theta,design_row_ids=train_ids,validation_row_ids=test_ids)
    report={**provenance(base,cfg),'method':'HPNSGA-II finite-scenario mean-plus-SD control','environment_uncertainty':True,
        'posterior_path':str(posterior),'posterior_sha256':hashlib.sha256(posterior.read_bytes()).hexdigest(),'posterior_metadata':meta,
        'source_hydro_combination':'Cartesian product; hydro scenarios not probabilities; nominal-hydro source posterior is not re-inferred for each hydro scenario.',
        'design_scenarios':len(scenes)*ntrain,'validation_scenarios':len(held)*ntest,
        'comparison':metrics,'cost_matching_gap_cny':abs(metrics['nominal']['selected_cost_cny']-metrics['robust']['selected_cost_cny']),
        'guarantee_scope':'Hydraulic constraints enforced only in finite design set; mean-plus-SD is not a worst-case pollution guarantee.',
        'calibration_warning':'Posterior calibration is unverified, and small smoke budgets do not validate robustness improvements.'}
    json_write(out/'result.json',report);print(out);return report
