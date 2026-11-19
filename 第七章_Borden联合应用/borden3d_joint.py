"""Shared finite-rank log-Gaussian K model and differentiable Darcy physics.

The covariance is a random-Fourier approximation to an anisotropic squared
exponential kernel. Basis frequencies are fixed; field coefficients are random
unknowns, not optimizer seeds. No site-calibrated probability claim is made.
"""
import copy, hashlib, json
import numpy as np
from scipy.sparse.linalg import spsolve
import torch
from torch import nn
from borden3d_common import BordenScene, read_config


DEFAULTS = {
    'field_modes': 6, 'field_basis_seed': 731, 'field_log_sd': .5,
    'field_correlation_m': [250., 250., 5.], 'log_mean_k_sd': .25,
    'log_recharge_sd': .2, 'truth_seed': 4081, 'design_seed': 4091,
    'design_scenarios': 5, 'validation_scenarios': 4,
    'control_periods': 4, 'control_max_rate_change_fraction': .4,
    'monitor_count': 5, 'monitor_pool': 120, 'control_wells': 2,
    'control_pool': 80, 'posterior_design_draws': 8,
    'posterior_validation_draws': 16, 'observation_end_day': 14600.,
    'inference_source_mode': 'smooth',
}


class JointModel:
    def __init__(self, profile, settings):
        self.settings = copy.deepcopy(settings)
        self.options = {**DEFAULTS, **settings.get('linked_model', {})}
        self.profile = profile
        cfg = read_config()
        periods = int(self.options['control_periods'])
        if periods < 1: raise ValueError('control_periods must be positive')
        cfg['control']['period_edges_day'] = np.linspace(
            cfg['control']['start_day'], cfg['control']['end_day'], periods+1).tolist()
        self.base = BordenScene(profile, cfg)
        self.config = cfg
        self.cutoff = float(self.options['observation_end_day'])
        if not 0 < self.cutoff <= cfg['control']['start_day']:
            raise ValueError('Observations must end no later than the control decision')
        self.observation_times = np.asarray(cfg['time']['observation_days'], float)
        self.observation_times = self.observation_times[self.observation_times <= self.cutoff]
        if not len(self.observation_times): raise ValueError('No pre-decision observations')
        m = int(self.options['field_modes'])
        lengths = np.asarray(self.options['field_correlation_m'], float)
        if m < 2 or m % 2 or lengths.shape != (3,) or np.any(lengths <= 0):
            raise ValueError('Use a positive even field_modes and three positive correlation lengths')
        rng = np.random.default_rng(self.options['field_basis_seed'])
        self.frequencies = rng.normal(size=(m//2, 3))/lengths
        phase = self.base.xyz @ self.frequencies.T
        self.basis = np.concatenate([np.cos(phase), np.sin(phase)], axis=1)/np.sqrt(m//2)
        self.modes = m
        self.dimension = 5+m
        self.columns = ['x_center_m','y_center_m','C_first_mg_L','log_mean_K_multiplier',
                        'log_recharge_multiplier'] + ['logK_coefficient_%02d'%j for j in range(m)]
        inv = cfg['inverse']
        self.lower = np.array([inv['center_bounds_m'][0][0],inv['center_bounds_m'][1][0],inv['concentration_bounds_mg_L'][0]])
        self.upper = np.array([inv['center_bounds_m'][0][1],inv['center_bounds_m'][1][1],inv['concentration_bounds_mg_L'][1]])
        content = json.dumps({'config':cfg,'profile':profile,'options':self.options,'columns':self.columns,
                              'inference_settings':self.settings},sort_keys=True)
        # Freeze the complete shared implementation, not merely the geometry.
        from pathlib import Path
        files = ['borden3d_joint.py','borden3d_common.py','borden3d_linked.py','borden3d_sources.py','borden3d_bayesian.py','borden3d_pinn.py','borden3d_evolution.py']
        code = b''.join((Path(__file__).parent/f).read_bytes() for f in files)
        self.signature = hashlib.sha256(content.encode()+code).hexdigest()

    def prior_rows(self, count, seed, nominal_source=True):
        rng = np.random.default_rng(seed)
        rows = np.zeros((count,self.dimension))
        if nominal_source:
            rows[:,:3] = np.r_[self.base.src['center_xy_m'], self.base.src['first_concentration_mg_L']]
        else:
            z = rng.normal(0,self.settings['source_prior_raw_sd'],(count,3))
            rows[:,:3] = self.lower+(self.upper-self.lower)/(1+np.exp(-z))
        rows[:,3] = rng.normal(0,self.options['log_mean_k_sd'],count)
        rows[:,4] = rng.normal(0,self.options['log_recharge_sd'],count)
        rows[:,5:] = rng.normal(size=(count,self.modes))
        return rows

    def scene(self, row):
        row = np.asarray(row,float)
        if row.shape != (self.dimension,) or not np.all(np.isfinite(row)):
            raise ValueError('A complete source-hydro row is required')
        logk = row[3]+self.options['field_log_sd']*(self.basis@row[5:])
        cfg = copy.deepcopy(self.config)
        cfg['hydro']['cell_log_k'] = logk.tolist()
        for key in ('recharge_m_d','enhanced_recharge_m_d'):
            cfg['hydro'][key] *= float(np.exp(row[4]))
        return BordenScene(self.profile,cfg)


class JointGuide(nn.Module):
    """Full-covariance q(source, mean K, recharge, spatial coefficients)."""
    def __init__(self, spec, device):
        super().__init__();self.spec=spec
        self.register_buffer('lower',torch.tensor(spec.lower,dtype=torch.float32,device=device))
        self.register_buffer('upper',torch.tensor(spec.upper,dtype=torch.float32,device=device))
        mean=torch.zeros(spec.dimension,device=device);mean[:3]=float(np.log(.25/.75))
        self.mean=nn.Parameter(mean)
        raw=float(np.log(np.expm1(spec.settings['variational_initial_raw_sd'])))
        self.raw_chol=nn.Parameter(torch.eye(spec.dimension,device=device)*raw)
        sd=torch.ones(spec.dimension,device=device);sd[:3]=spec.settings['source_prior_raw_sd']
        self.register_buffer('prior_sd',sd)

    def chol(self):
        return torch.tril(self.raw_chol,-1)+torch.diag(torch.nn.functional.softplus(torch.diag(self.raw_chol))+1e-6)

    def transform(self,z):
        source=self.lower+(self.upper-self.lower)*torch.sigmoid(z[...,:3])
        scales=z.new_tensor([self.spec.options['log_mean_k_sd'],self.spec.options['log_recharge_sd']])
        return torch.cat([source,z[...,3:5]*scales,z[...,5:]],dim=-1)

    def sample(self,n=1):
        return self.transform(self.mean+torch.randn(n,len(self.mean),device=self.mean.device)@self.chol().T)

    def kl(self):
        L=self.chol();s=self.prior_sd
        return .5*((L.square().sum(dim=1)+self.mean.square()).div(s.square()).sum()-len(s)
                    +2*torch.log(s).sum()-2*torch.log(torch.diag(L)).sum())


class DarcySolve(torch.autograd.Function):
    """Sparse exact forward/adjoint solve; differentiate edge conductances/RHS."""
    @staticmethod
    def forward(ctx, conductance, rhs, scene):
        c=conductance.detach().cpu().double().numpy();b=rhs.detach().cpu().double().numpy()
        lap=scene.edge_laplacian(c);free=scene.free;bound=scene.bound
        h=np.full(scene.n,scene.hydro['downstream_head_m'])
        A=lap[free][:,free].tocsc()
        h[free]=spsolve(A,b[free]-lap[free][:,bound]@h[bound])
        ctx.A=A;ctx.scene=scene;ctx.h=h
        ctx.save_for_backward(conductance,rhs)
        # Return head relative to the fixed boundary datum. Fluxes use head
        # differences; this avoids float32 cancellation around the 219 m datum.
        return conductance.new_tensor(h-scene.hydro['downstream_head_m'])

    @staticmethod
    def backward(ctx, grad):
        c,rhs=ctx.saved_tensors;s=ctx.scene
        lam=np.zeros(s.n);lam[s.free]=spsolve(ctx.A.T,grad.detach().cpu().double().numpy()[s.free])
        dc=-(lam[s.eu]-lam[s.ev])*(ctx.h[s.eu]-ctx.h[s.ev])
        return c.new_tensor(dc),rhs.new_tensor(lam),None


class DifferentiableTransport:
    """Same Darcy, upwind advection and diagonal dispersion as BordenScene."""
    def __init__(self,spec,row,device):
        s=spec.base;self.scene=s;self.device=device
        self.u=torch.tensor(s.eu,dtype=torch.long,device=device)
        self.v=torch.tensor(s.ev,dtype=torch.long,device=device)
        def tensor(x):return torch.as_tensor(x,dtype=row.dtype,device=device)
        basis=tensor(spec.basis)
        logk=row[3]+spec.options['field_log_sd']*(basis@row[5:])
        kval=tensor([s.hydro['kx_m_d'],s.hydro['ky_m_d'],s.hydro['kz_m_d']])
        axis=torch.tensor(s.axis,dtype=torch.long,device=device)
        ka=torch.exp(logk[self.u])*kval[axis];kb=torch.exp(logk[self.v])*kval[axis]
        conductance=2*ka*kb/(ka+kb)*tensor(s.area/s.distance)
        recharge=np.zeros(s.n)
        enhanced=s.source_mask(s.src['center_xy_m'])[:s.nc]
        recharge[:s.nc]=np.where(enhanced,s.hydro['enhanced_recharge_m_d'],s.hydro['recharge_m_d'])*s.dx*s.dy
        rhs=tensor(s.external_in-recharge)+tensor(recharge)*torch.exp(row[4])
        h=DarcySolve.apply(conductance,rhs,s)
        q=conductance*(h[self.u]-h[self.v])
        divergence=torch.zeros_like(h).index_add(0,self.u,q).index_add(0,self.v,-q)
        exchange=(divergence-rhs)*tensor(s.chd)
        self.outgoing=torch.clamp(-exchange,min=0)
        velocity=[]
        for ax in range(3):
            ix=np.flatnonzero(s.axis==ax);ix=torch.tensor(ix,dtype=torch.long,device=device)
            value=q[ix]/tensor(s.area[s.axis==ax]*s.hydro['porosity'])
            counts=np.zeros(s.n);np.add.at(counts,s.eu[s.axis==ax],1);np.add.at(counts,s.ev[s.axis==ax],1)
            z=torch.zeros_like(h).index_add(0,self.u[ix],value).index_add(0,self.v[ix],value)
            velocity.append(z/tensor(np.maximum(counts,1)))
        vel=torch.stack(velocity,dim=1);speed=torch.sqrt(torch.sum(vel.square(),dim=1)+1e-30)
        dispers=[]
        for ax in (0,1):
            dispers.append(s.hydro['alpha_TH_m']*speed+(s.hydro['alpha_L_m']-s.hydro['alpha_TH_m'])*vel[:,ax].square()/speed+s.hydro['diffusion_m2_d'])
        dispers.append(s.hydro['alpha_TV_m']*speed+s.hydro['diffusion_m2_d'])
        d=torch.stack(dispers,dim=1);da=d[self.u,axis];db=d[self.v,axis]
        self.diff=2*da*db/torch.clamp(da+db,min=1e-30)*tensor(s.hydro['porosity']*s.area/s.distance)
        self.q=q
        self.diagonal=torch.zeros_like(h).index_add(0,self.u,self.diff).index_add(0,self.v,self.diff)
        self.diagonal=self.diagonal.index_add(0,self.u,torch.clamp(q,min=0)).index_add(0,self.v,torch.clamp(-q,min=0))+self.outgoing

    def apply(self,c):
        flux=self.diff*(c[self.u]-c[self.v])+torch.clamp(self.q,min=0)*c[self.u]+torch.clamp(self.q,max=0)*c[self.v]
        return torch.zeros_like(c).index_add(0,self.u,flux).index_add(0,self.v,-flux)+self.outgoing*c


def train_joint_bayesian(spec,obs,stage1,stage2,width,draws,device,seed,method='tsbpinn',source_initial=None):
    from borden3d_pinn import CoordinateNet,features,smooth_source_torch
    from borden3d_bayesian import BayesianCoordinateNet
    from borden3d_progress import progress
    if stage1<0 or stage2<1 or draws<4:raise ValueError('Invalid Bayesian training budget')
    torch.manual_seed(seed);torch.set_num_threads(2);rng=np.random.default_rng(seed)
    scene=spec.base;cfg=spec.settings;times=scene.times()
    xs=[features(scene,t,device) for t in times]
    warm=CoordinateNet(width).to(device);guide=JointGuide(spec,device)
    if source_initial is not None:
        initial=np.asarray(source_initial,float)
        if initial.shape!=(3,) or not np.all(np.isfinite(initial)) or np.any(initial<=spec.lower) or np.any(initial>=spec.upper):
            raise ValueError('Source initial values must be finite and strictly inside bounds')
        fraction=(initial-spec.lower)/(spec.upper-spec.lower)
        with torch.no_grad():guide.mean[:3].copy_(guide.mean.new_tensor(np.log(fraction/(1-fraction))))
    water=torch.tensor(scene.water,dtype=torch.float32,device=device)
    obs_t=np.asarray(obs['time_indices']);obs_cells=np.asarray(obs['cell_indices'])
    inputs=torch.stack([xs[int(k)][int(i)] for k,i in zip(obs_t,obs_cells)])
    target=torch.tensor(obs['values']/500.,dtype=torch.float32,device=device)
    sigma=torch.tensor(obs['sigma_mg_L']/500.,dtype=torch.float32,device=device)
    allowed=np.flatnonzero((times>0)&(times<=spec.cutoff))
    history=[]
    def scores(forward,row,active,operator):
        mask=smooth_source_torch(scene,row[:3],device);pde=0.;boundary=0.
        for k in active:
            c=forward(xs[k]);prev=forward(xs[k-1]);dt=times[k]-times[k-1]
            src=row[2]/500.*scene.concentration((times[k]+times[k-1])/2,1.)
            residual=(water*(c-prev)/dt+operator.apply(c))/(water/dt+operator.diagonal+1e-8)
            pde=pde+torch.mean(((1-mask)*residual).square())
            boundary=boundary+torch.sum(mask*(c-src).square())/torch.clamp(mask.sum(),min=1e-8)
        return pde/len(active)/(2*cfg['physical_residual_scale']**2),boundary/len(active)/(2*cfg['source_boundary_residual_scale']**2)
    opt=torch.optim.Adam(warm.parameters(),lr=.002)
    fixed=guide.transform(guide.mean).detach();op=DifferentiableTransport(spec,fixed,device)
    for epoch in range(stage1):
        opt.zero_grad();active=rng.choice(allowed,min(3,len(allowed)),replace=False)
        pde,bound=scores(warm,fixed,active,op);loss=pde+bound
        loss.backward();opt.step();history.append({'stage':1,'epoch':epoch,'loss':float(loss.detach())})
    deterministic=method=='pinn'
    model=warm if deterministic else BayesianCoordinateNet(warm,cfg['weight_prior_sd']).to(device)
    opt=torch.optim.Adam(list(model.parameters())+list(guide.parameters()),lr=.002)
    for epoch in range(stage2):
        opt.zero_grad()
        if deterministic:
            row=guide.transform(guide.mean);forward=model
        else:
            weight=model.sample();row=guide.sample()[0];forward=lambda x:model(x,weight)
        pred=forward(inputs)
        nll=torch.sum(.5*((pred-target)/sigma).square()+torch.log(sigma)+.5*np.log(2*np.pi))
        active=rng.choice(allowed,min(3,len(allowed)),replace=False)
        operator=DifferentiableTransport(spec,row,device)
        pde,bound=scores(forward,row,active,operator)
        kl=guide.mean.new_tensor(0.) if deterministic else model.kl()+guide.kl()
        loss=nll+pde+bound+kl
        if not torch.isfinite(loss):raise RuntimeError('Nonfinite joint variational loss')
        loss.backward();torch.nn.utils.clip_grad_norm_(list(model.parameters())+list(guide.parameters()),100.);opt.step()
        history.append({'stage':2,'epoch':epoch,'loss':float(loss.detach()),'nll':float(nll.detach()),'physics':float(pde.detach()),'source':float(bound.detach()),'kl':float(kl.detach())})
        if epoch%50==0:progress('joint_source_hydro_vi',epoch=epoch+1,epochs=stage2,loss=history[-1]['loss'])
    with torch.no_grad():
        rows=(guide.transform(guide.mean).repeat(draws,1) if deterministic else guide.sample(draws)).cpu().numpy()
    return model,guide,rows,history
