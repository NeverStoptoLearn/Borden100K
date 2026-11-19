"""Two-stage variational Bayesian 3-D PINN with bounded joint source draws.

This is a generalized posterior with explicit soft-physics scores. Its scale
must be calibrated; this implementation does not assert interval calibration.
"""
import math
import numpy as np
import torch
from torch import nn
from borden3d_pinn import CoordinateNet,features,smooth_source_torch,torch_sparse
from borden3d_progress import progress,checkpoint_torch


class BayesianCoordinateNet(nn.Module):
    def __init__(self,warm,prior_sd=1.):
        super().__init__();self.prior_sd=prior_sd
        self.mu=nn.ParameterList();self.rho=nn.ParameterList()
        for layer in warm.net:
            if isinstance(layer,nn.Linear):
                for p in (layer.weight,layer.bias):
                    self.mu.append(nn.Parameter(p.detach().clone()))
                    self.rho.append(nn.Parameter(torch.full_like(p,-4.)))

    def sample(self):
        return [m+torch.nn.functional.softplus(r)*torch.randn_like(m) for m,r in zip(self.mu,self.rho)]

    def forward(self,x,draw):
        y=x
        for i in range(0,len(draw),2):
            y=torch.nn.functional.linear(y,draw[i],draw[i+1])
            if i<len(draw)-2:y=torch.tanh(y)
        return torch.nn.functional.softplus(y.squeeze(-1))*x[:,3]/(x[:,3]+.002)

    def kl(self):
        total=0.
        for m,r in zip(self.mu,self.rho):
            s=torch.nn.functional.softplus(r)
            total+=torch.sum(torch.log(self.prior_sd/s)+(s*s+m*m)/(2*self.prior_sd**2)-.5)
        return total


class JointSourceGuide(nn.Module):
    def __init__(self,scene,settings,device):
        super().__init__();inv=scene.cfg['inverse']
        self.register_buffer('lower',torch.tensor([inv['center_bounds_m'][0][0],inv['center_bounds_m'][1][0],inv['concentration_bounds_mg_L'][0]],device=device))
        self.register_buffer('upper',torch.tensor([inv['center_bounds_m'][0][1],inv['center_bounds_m'][1][1],inv['concentration_bounds_mg_L'][1]],device=device))
        self.mean=nn.Parameter(torch.full((3,),float(np.log(.25/.75)),device=device))
        raw=np.log(np.expm1(settings['variational_initial_raw_sd']))
        self.raw_chol=nn.Parameter(torch.eye(3,device=device)*float(raw))
        self.prior_sd=float(settings['source_prior_raw_sd'])

    def chol(self):
        diag=torch.nn.functional.softplus(torch.diag(self.raw_chol))+1e-6
        return torch.tril(self.raw_chol,-1)+torch.diag(diag)

    def transform(self,z):return self.lower+(self.upper-self.lower)*torch.sigmoid(z)

    def sample(self,n=1):
        z=self.mean+torch.randn(n,3,device=self.mean.device)@self.chol().T
        return self.transform(z)

    def kl(self):
        L=self.chol();s=self.prior_sd
        return .5*((torch.sum(L*L)+torch.sum(self.mean*self.mean))/(s*s)-3+6*math.log(s)-2*torch.log(torch.diag(L)).sum())


def train_bayesian(scene,obs,settings,stage1=100,stage2=1500,width=32,draws=256,device='cpu',seed=42):
    if stage1<0 or stage2<1 or draws<16:raise ValueError('stage1>=0, stage2>=1 and draws>=16 required')
    torch.manual_seed(seed);torch.set_num_threads(2);rng=np.random.default_rng(seed)
    times=scene.times();xs=[features(scene,t,device) for t in times]
    warm=CoordinateNet(width).to(device);guide=JointSourceGuide(scene,settings,device)
    L=torch_sparse(scene.base['L'],device)
    water=torch.tensor(scene.water,dtype=torch.float32,device=device)
    diag=torch.tensor(scene.base['L'].diagonal(),dtype=torch.float32,device=device)
    target=torch.tensor(np.asarray(obs['values'])/500.,dtype=torch.float32,device=device)
    sigma=torch.tensor(np.asarray(obs['sigma_mg_L'])/500.,dtype=torch.float32,device=device)
    obs_t=np.asarray(obs['time_indices']);obs_i=torch.tensor(obs['cell_indices'],dtype=torch.long,device=device)
    history=[]

    def scores(forward,theta,active_times):
        mask=smooth_source_torch(scene,theta,device);pde=0.;boundary=0.
        for k in active_times:
            c=forward(xs[k]);old=forward(xs[k-1]);dt=times[k]-times[k-1]
            value=theta[2]/500.*scene.concentration((times[k]+times[k-1])/2,1.)
            residual=(water*(c-old)/dt+torch.sparse.mm(L,c[:,None]).squeeze(1))/(water/dt+diag+1e-8)
            pde+=torch.mean(((1-mask)*residual)**2)
            boundary+=torch.sum(mask*(c-value)**2)/torch.clamp(mask.sum(),min=1e-8)
        return pde/len(active_times)/(2*settings['physical_residual_scale']**2),boundary/len(active_times)/(2*settings['source_boundary_residual_scale']**2)

    opt=torch.optim.Adam(warm.parameters(),lr=.002)
    for epoch in range(stage1):
        opt.zero_grad();active=rng.choice(np.arange(1,len(times)),min(3,len(times)-1),replace=False)
        theta=guide.transform(guide.mean).detach();_,boundary=scores(warm,theta,active)
        loss=boundary+torch.mean(warm(xs[0])**2);loss.backward();opt.step()
        history.append({'epoch':epoch,'stage':1,'loss':float(loss.detach())})
        if epoch%50==0 or epoch==stage1-1:
            progress('tsb_warm',epoch=epoch+1,epochs=stage1,loss=history[-1]['loss'])
        if (epoch+1)%100==0 or epoch==stage1-1:
            checkpoint_torch('tsb_warm',{'network':warm.state_dict(),'source_guide':guide.state_dict(),'optimizer':opt.state_dict(),'epoch':epoch,'torch_rng':torch.get_rng_state(),'numpy_rng':rng.bit_generator.state})
    model=BayesianCoordinateNet(warm,settings['weight_prior_sd']).to(device)
    opt=torch.optim.Adam(list(model.parameters())+list(guide.parameters()),lr=.002)
    for epoch in range(stage2):
        opt.zero_grad();draw=model.sample();theta=guide.sample()[0]
        forward=lambda x:model(x,draw)
        # A coordinate network has no inter-point layers: selecting inputs first
        # yields the same values/gradients without allocating full-grid data graphs.
        pred=torch.cat([forward(xs[int(k)][obs_i[np.flatnonzero(obs_t==k)]]) for k in np.unique(obs_t)])
        order=np.concatenate([np.flatnonzero(obs_t==k) for k in np.unique(obs_t)])
        tid=torch.tensor(order,dtype=torch.long,device=device)
        nll=torch.sum(.5*((pred-target[tid])/sigma[tid])**2+torch.log(sigma[tid])+0.5*math.log(2*math.pi))
        active=rng.choice(np.arange(1,len(times)),min(3,len(times)-1),replace=False)
        pde,boundary=scores(forward,theta,active)
        kl=model.kl()+guide.kl();loss=nll+pde+boundary+kl
        if not torch.isfinite(loss):raise RuntimeError('Nonfinite variational objective')
        loss.backward();torch.nn.utils.clip_grad_norm_(list(model.parameters())+list(guide.parameters()),100.);opt.step()
        history.append({'epoch':stage1+epoch,'stage':2,'loss':float(loss.detach()),'negative_log_likelihood':float(nll.detach()),'physical_score':float(pde.detach()),'boundary_score':float(boundary.detach()),'kl':float(kl.detach())})
        if epoch%50==0 or epoch==stage2-1:
            progress('tsb_variational',epoch=epoch+1,epochs=stage2,**{k:v for k,v in history[-1].items() if k not in ['epoch','stage']})
        if (epoch+1)%100==0 or epoch==stage2-1:
            checkpoint_torch('tsb_variational',{'network':model.state_dict(),'source_guide':guide.state_dict(),'optimizer':opt.state_dict(),'epoch':epoch,'history':history,'torch_rng':torch.get_rng_state(),'cuda_rng':torch.cuda.get_rng_state_all() if device.startswith('cuda') else None,'numpy_rng':rng.bit_generator.state})
    with torch.no_grad():
        theta_draws=guide.sample(draws).cpu().numpy();concentration=[]
        # Each weight draw is kept fixed across all times and observation cells.
        for _ in range(draws):
            draw=model.sample();p=np.zeros(len(obs_t))
            for k in np.unique(obs_t):
                ix=np.flatnonzero(obs_t==k);idx=torch.tensor(ix,dtype=torch.long,device=device)
                p[ix]=model(xs[int(k)][obs_i[idx]],draw).cpu().numpy()*500.
            concentration.append(p)
    return model,guide,theta_draws,np.asarray(concentration),history


def predict_draws(scene,model,time_indices,cell_indices,n=128,device='cpu'):
    output=[];xs={int(k):features(scene,scene.times()[int(k)],device) for k in np.unique(time_indices)}
    with torch.no_grad():
        for _ in range(n):
            draw=model.sample();v=np.zeros(len(time_indices))
            for k,x in xs.items():
                ix=np.flatnonzero(np.asarray(time_indices)==k)
                v[ix]=model(x[torch.tensor(np.asarray(cell_indices)[ix],dtype=torch.long,device=device)],draw).cpu().numpy()*500.
            output.append(v)
    return np.asarray(output)
