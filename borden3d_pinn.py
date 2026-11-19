"""Deterministic coordinate PINN with the shared 3-D finite-volume physics.

The spatial residual uses the conservative FV operator rather than pretending
that a rectangular constant-coefficient ADE describes the fan-shaped domain.
Bayesian sampling, observation noise and hydrogeological ensembles are OFF.
"""
import numpy as np
import torch
from torch import nn
from borden3d_progress import progress

def torch_sparse(A,device):
    a=A.tocoo()
    ix=torch.tensor(np.vstack([a.row,a.col]),dtype=torch.long,device=device)
    return torch.sparse_coo_tensor(ix,torch.tensor(a.data,dtype=torch.float32,device=device),a.shape).coalesce()

def features(scene,t,device):
    xyz=scene.xyz
    surface=scene.cfg['geometry']['surface_m']
    depth=(surface-xyz[:,2])/(surface-np.tile(scene.bottom,scene.nz))
    vals=np.c_[xyz[:,0]/scene.poly[:,0].max(),xyz[:,1]/scene.poly[:,1].max(),depth,np.full(scene.n,t/scene.src['end_day'])]
    return torch.tensor(vals,dtype=torch.float32,device=device)

class CoordinateNet(nn.Module):
    def __init__(self,width=48,positive=True):
        super().__init__();self.positive=positive
        self.net=nn.Sequential(nn.Linear(4,width),nn.Tanh(),nn.Linear(width,width),nn.Tanh(),nn.Linear(width,1))
        if positive:nn.init.constant_(self.net[-1].bias,-2.)

    def forward(self,x):
        y=self.net(x).squeeze(-1)
        if self.positive:y=torch.nn.functional.softplus(y)*x[:,3]/(x[:,3]+.002)
        return y

class SourceParameters(nn.Module):
    def __init__(self,scene,device):
        super().__init__();b=scene.cfg['inverse']
        self.register_buffer('lower',torch.tensor([b['center_bounds_m'][0][0],b['center_bounds_m'][1][0],b['concentration_bounds_mg_L'][0]],dtype=torch.float32,device=device))
        self.register_buffer('upper',torch.tensor([b['center_bounds_m'][0][1],b['center_bounds_m'][1][1],b['concentration_bounds_mg_L'][1]],dtype=torch.float32,device=device))
        self.raw=nn.Parameter(torch.full((3,),float(np.log(.25/.75)),device=device))

    def forward(self):return self.lower+(self.upper-self.lower)*torch.sigmoid(self.raw)

def smooth_source_torch(scene,theta,device):
    xy=torch.tensor(scene.xy,dtype=torch.float32,device=device)
    half=torch.tensor(scene.src['half_size_xy_m'],dtype=torch.float32,device=device)
    width=torch.tensor([scene.dx,scene.dy],dtype=torch.float32,device=device)*scene.cfg['inverse']['smooth_edge_cells']
    z=xy-theta[:2];s=torch.clamp((half+width-torch.abs(z))/(2*width),0,1)
    w=(s*s*(3-2*s)).prod(dim=1)
    return torch.cat([w,torch.zeros(scene.n-scene.nc,device=device)])

def train_two_stage(scene,obs,stage1=500,stage2=2000,width=48,device='cpu',seed=42):
    torch.manual_seed(seed);np.random.seed(seed)
    torch.set_num_threads(2)
    model=CoordinateNet(width).to(device);params=SourceParameters(scene,device)
    L=torch_sparse(scene.base['L'],device);water=torch.tensor(scene.water,dtype=torch.float32,device=device)
    diag=torch.tensor(scene.base['L'].diagonal(),dtype=torch.float32,device=device)
    optimizer=torch.optim.Adam([{'params':model.parameters(),'lr':.002},{'params':params.parameters(),'lr':.005}])
    times=scene.times();inputs=[features(scene,t,device) for t in times]
    obs_i=torch.tensor(obs['cell_indices'],dtype=torch.long,device=device)
    obs_t=np.asarray(obs['time_indices']);target=torch.tensor(obs['values']/500.,dtype=torch.float32,device=device)
    history=[];rng=np.random.default_rng(seed)
    for epoch in range(stage1+stage2):
        optimizer.zero_grad();theta=params();w=smooth_source_torch(scene,theta,device)
        active_times=rng.choice(np.arange(1,len(times)),size=min(3,len(times)-1),replace=False)
        source_loss=0.;physics_loss=0.
        for k in active_times:
            c=model(inputs[k]);prev=model(inputs[k-1]);dt=times[k]-times[k-1]
            src=theta[2]/500.*scene.concentration((times[k]+times[k-1])/2,1.)
            source_loss+=torch.sum(w*(c-src)**2)/torch.clamp(w.sum(),min=1e-8)
            residual=water*(c-prev)/dt+torch.sparse.mm(L,c[:,None]).squeeze(1)
            scaling=water/dt+diag+1e-8
            physics_loss+=torch.mean(((1-w)*residual/scaling)**2)
        initial_loss=torch.mean(model(inputs[0])**2)
        if epoch<stage1:
            loss=source_loss+initial_loss;data_loss=torch.tensor(0.,device=device)
        else:
            pred=torch.zeros(len(target),device=device)
            for k in np.unique(obs_t):
                loc=np.flatnonzero(obs_t==k);idx=torch.tensor(loc,dtype=torch.long,device=device)
                pred[idx]=model(inputs[int(k)])[obs_i[idx]]
            data_loss=torch.mean((pred-target)**2)
            loss=10*data_loss+physics_loss+source_loss+initial_loss
        loss.backward()
        if epoch<stage1:params.raw.grad=None
        torch.nn.utils.clip_grad_norm_(model.parameters(),10.);optimizer.step()
        history.append({'epoch':epoch,'stage':1 if epoch<stage1 else 2,'loss':float(loss.detach()),'observation_loss':float(data_loss.detach())})
    model.eval()
    with torch.no_grad():
        prediction=np.zeros(len(target))
        for k in np.unique(obs_t):
            loc=np.flatnonzero(obs_t==k);prediction[loc]=model(inputs[int(k)])[obs_i[torch.tensor(loc,device=device)]].cpu().numpy()*500
        theta=params().cpu().numpy()
    return model,theta,prediction,history

def train_physics_surrogate(scene,result,theta,epochs=100,width=48,adjoint=False,device='cpu',seed=42):
    """Residual-only primal/adjoint network for the hybrid FPA update.

    FV fields are used for diagnostics/scaling only, not as supervised targets.
    The discrete adjoint reference controls inaccurate neural gradient directions.
    """
    torch.manual_seed(seed);torch.set_num_threads(2)
    net=CoordinateNet(width,positive=not adjoint).to(device)
    times=result['times'];inputs=[features(scene,t,device) for t in times]
    water=torch.tensor(scene.water,dtype=torch.float32,device=device)
    pen=torch.tensor(result['penalty'],dtype=torch.float32,device=device)
    L=torch_sparse(scene.base['L'].T if adjoint else scene.base['L'],device)
    diag=torch.tensor(scene.base['L'].diagonal(),dtype=torch.float32,device=device)
    forcing=np.zeros_like(result['C']) if adjoint else None
    scale=max(float(np.max(abs(result['adjoint']))),1e-14) if adjoint else 500.
    if adjoint:
        forcing=result['adjoint_forcing']
        forcing=torch.tensor(forcing/scale,dtype=torch.float32,device=device)
    optimizer=torch.optim.Adam(net.parameters(),lr=.003);rng=np.random.default_rng(seed)
    for epoch in range(epochs):
        optimizer.zero_grad();loss=0.
        for k in rng.choice(np.arange(1,len(times)),size=min(3,len(times)-1),replace=False):
            v=net(inputs[k]);dt=times[k]-times[k-1]
            if adjoint:
                future=net(inputs[k+1]) if k<len(times)-1 else torch.zeros_like(v)
                right=water/(times[k+1]-times[k])*future if k<len(times)-1 else torch.zeros_like(v)
                residual=water/dt*v+torch.sparse.mm(L,v[:,None]).squeeze(1)+pen*v-right-forcing[k]
            else:
                prev=net(inputs[k-1]);src=scene.concentration((times[k]+times[k-1])/2,theta[2])/scale
                residual=water/dt*(v-prev)+torch.sparse.mm(L,v[:,None]).squeeze(1)+pen*(v-src)
            loss+=torch.mean((residual/(water/dt+diag+pen+1e-8))**2)
        loss.backward();optimizer.step()
        if epoch%25==0 or epoch==epochs-1:
            progress('fpa_adjoint_training' if adjoint else 'fpa_state_training',epoch=epoch+1,epochs=epochs,loss=float(loss.detach()))
    with torch.no_grad():pred=np.array([net(x).cpu().numpy()*scale for x in inputs])
    truth=result['adjoint'] if adjoint else result['C']
    error=float(np.linalg.norm(pred-truth)/max(np.linalg.norm(truth),1e-15))
    return net,pred,error
