"""Shared constrained Pareto selection and 3-D heterogeneous MI-tree model."""
import numpy as np
from scipy.optimize import linear_sum_assignment

def canonical(x):
    x=np.asarray(x).copy()
    order=sorted(range(len(x)),key=lambda i:(-int(x[i,-1]>.5),*x[i,:3]))
    return x[order]

def align(x,template):
    cost=np.sum((x[:,None,:3]-template[None,:,:3])**2,axis=2)
    cost+=.2*((x[:,None,-1]>.5)!=(template[None,:,-1]>.5))
    r,c=linear_sum_assignment(cost);out=x.copy();out[c]=x[r];return out

def dominates(fa,fb,va=0.,vb=0.):
    if va>1e-9 or vb>1e-9:
        return va<vb if va!=vb else bool(np.all(fa<=fb) and np.any(fa<fb))
    return bool(np.all(fa<=fb) and np.any(fa<fb))

def nondominated(F,V):
    return np.asarray([i for i in range(len(F)) if V[i]<=1e-9 and not any(
        dominates(F[j],F[i],V[j],V[i]) for j in range(len(F)) if j!=i)],dtype=int)

def select(pop,F,V,size):
    remaining=list(range(len(pop)));order=[]
    while remaining and len(order)<size:
        front=[i for i in remaining if not any(dominates(F[j],F[i],V[j],V[i]) for j in remaining if j!=i)]
        if len(order)+len(front)>size:
            crowd=np.zeros(len(front));ff=F[front]
            for dim in range(F.shape[1]):
                ix=np.argsort(ff[:,dim]);crowd[ix[[0,-1]]]=np.inf
                if len(ix)>2:crowd[ix[1:-1]]+=(ff[ix[2:],dim]-ff[ix[:-2],dim])/max(np.ptp(ff[:,dim]),1e-12)
            front=[front[i] for i in np.argsort(-crowd)]
        order.extend(front[:size-len(order)]);remaining=[i for i in remaining if i not in front]
    return pop[order],F[order],V[order]

class MITree3D:
    """x/y/screen-depth/enabled slots, alignment, MI spanning tree, conditionals.

    Discretization estimates pairwise MI; conditional real-valued models use
    linear Gaussian fits, and discrete children use conditional frequency bins.
    These are optimizer sampling distributions, not environmental uncertainty.
    """
    def __init__(self,slots,min_std=.025,use_alignment=True):
        self.slots=slots;self.min_std=min_std;self.use_alignment=use_alignment

    def fit(self,elite):
        aligned=np.asarray([align(x,elite[0]) if self.use_alignment else x for x in elite]);self.data=aligned.reshape(len(elite),-1)
        n,d=self.data.shape;self.binary=np.arange(d)%4==3
        bins=np.clip((self.data*4).astype(int),0,3);bins[:,self.binary]=(self.data[:,self.binary]>.5).astype(int)
        mi=np.zeros((d,d))
        for i in range(d):
            for j in range(i):
                h=np.bincount(bins[:,i]*4+bins[:,j],minlength=16).reshape(4,4)/n
                px=h.sum(axis=1);py=h.sum(axis=0);den=px[:,None]*py[None,:];good=h>0
                mi[i,j]=mi[j,i]=np.sum(h[good]*np.log(h[good]/den[good]))
        self.parent=np.full(d,-1,int);used={0};self.order=[0]
        while len(used)<d:
            i,j=max(((i,j) for i in used for j in range(d) if j not in used),key=lambda ij:mi[ij])
            self.parent[j]=i;used.add(j);self.order.append(j)
        self.models={}
        for j in self.order:
            i=self.parent[j];y=self.data[:,j]
            if i<0:self.models[j]=('root',float(y.mean()),max(float(y.std()),self.min_std));continue
            x=self.data[:,i]
            if self.binary[j]:
                xb=np.clip((x*4).astype(int),0,3)
                probs=[float(y[xb==b].mean()) if np.any(xb==b) else float(y.mean()) for b in range(4)]
                self.models[j]=('binary',np.clip(probs,.05,.95))
            elif self.binary[i]:
                groups=[]
                for b in [0,1]:
                    v=y[(x>.5)==bool(b)];v=v if len(v) else y
                    groups.append((float(v.mean()),max(float(v.std()),self.min_std)))
                self.models[j]=('groups',groups)
            else:
                a=np.c_[x,np.ones(n)];coef=np.linalg.lstsq(a,y,rcond=None)[0]
                self.models[j]=('linear',coef,max(float(np.std(y-a@coef)),self.min_std))

    def sample(self,n,rng):
        d=self.data.shape[1];out=np.zeros((n,d))
        for j in self.order:
            model=self.models[j];i=self.parent[j]
            if model[0]=='root':
                out[:,j]=(rng.random(n)<np.clip(model[1],.05,.95)) if self.binary[j] else rng.normal(model[1],model[2],n)
            elif model[0]=='binary':out[:,j]=rng.random(n)<model[1][np.clip((out[:,i]*4).astype(int),0,3)]
            elif model[0]=='groups':
                p=np.asarray(model[1])[out[:,i].astype(int)];out[:,j]=rng.normal(p[:,0],p[:,1])
            else:out[:,j]=model[1][0]*out[:,i]+model[1][1]+rng.normal(0,model[2],n)
        return np.clip(out,0,1).reshape(n,self.slots,4)

def well_pool(scene,count=75):
    # Concentration-aware screens plus space coverage, with no duplicate cells.
    ref=scene.simulate();C=ref['C'][-1];valid=np.flatnonzero(~scene.chd & ~scene.source_mask(scene.src['center_xy_m']))
    k=min(len(valid),count//2);high=valid[np.argsort(C[valid])[-k:]]
    spatial=valid[np.linspace(0,len(valid)-1,count-k,dtype=int)]
    return np.unique(np.r_[high,spatial]),ref

def pool_coordinates(scene,pool):
    xyz=scene.xyz[pool];depth=(scene.cfg['geometry']['surface_m']-xyz[:,2])/(scene.cfg['geometry']['surface_m']-np.tile(scene.bottom,scene.nz)[pool])
    return np.c_[xyz[:,0]/np.max(scene.poly[:,0]),xyz[:,1]/np.max(scene.poly[:,1]),depth]

def decode(x,pool,coords):
    return np.argmin(np.sum((x[:,None,:3]-coords[None,:,:])**2,axis=2),axis=1)
