"""Deterministic fan-shaped Borden benchmark: Darcy flow and 3-D FV transport.

All lengths m, time days, water flows m3/day, concentration mg/L.
The original cell-source is Dirichlet concentration, NOT a mass-rate source.
Inverse methods additionally have a documented smooth penalty approximation.
"""
from pathlib import Path
import argparse, json, hashlib, os
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import splu, spsolve, cg, LinearOperator
from scipy.spatial import cKDTree

ROOT=Path(__file__).resolve().parent
CONFIG=ROOT/'borden3d_config.json'

def read_config(path=CONFIG):
    if not isinstance(path,dict) and Path(path)==CONFIG:
        path=os.environ.get('BORDEN3D_SCENE_CONFIG',path)
    cfg=json.loads(json.dumps(path)) if isinstance(path,dict) else json.loads(Path(path).read_text(encoding='utf-8'))
    if cfg.get('uncertainty_enabled') is not False:
        raise ValueError('This phase supports deterministic input only.')
    return cfg

def inside_polygon(x,y,poly):
    x,y=np.broadcast_arrays(x,y);inside=np.zeros(x.shape,dtype=bool)
    for a,b in zip(poly,np.roll(poly,-1,axis=0)):
        crosses=(a[1]>y)!=(b[1]>y)
        inside ^= crosses & (x<(b[0]-a[0])*(y-a[1])/(b[1]-a[1]+1e-300)+a[0])
    return inside

def json_write(path,data):
    p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

class BordenScene:
    def __init__(self,profile='quick',config=CONFIG):
        self.cfg=read_config(config);self.profile=profile
        signature=json.dumps({'config':self.cfg,'profile':profile},sort_keys=True).encode('utf-8')
        self.signature=hashlib.sha256(signature+Path(__file__).read_bytes()).hexdigest()
        g=self.cfg['geometry'];self.hydro=self.cfg['hydro'];self.src=self.cfg['source']
        self.nx,self.ny,self.nz=g['grid_profiles'][profile]
        poly=np.asarray(g['boundary_xy_m'],float);self.poly=poly
        self.dx=np.max(poly[:,0])/self.nx;self.dy=np.max(poly[:,1])/self.ny
        xc=(np.arange(self.nx)+.5)*self.dx;yc=(np.arange(self.ny)+.5)*self.dy
        X,Y=np.meshgrid(xc,yc);self.mask2=inside_polygon(X,Y,poly)
        jj,ii=np.where(self.mask2);self.jj,self.ii=jj,ii;self.nc=len(ii)
        self.xy=np.c_[X[self.mask2],Y[self.mask2]]
        self.bottom=np.interp(self.xy[:,0],g['bottom_x_m'],g['bottom_z_m'])
        self.dz=(g['surface_m']-self.bottom)/self.nz
        # k=0 is the uppermost layer, with terrain-following layer elevations.
        self.xyz=np.concatenate([np.c_[self.xy,g['surface_m']-(k+.5)*self.dz] for k in range(self.nz)])
        self.n=len(self.xyz);self.vol=np.tile(self.dx*self.dy*self.dz,self.nz)
        self.water=self.vol*self.hydro['porosity']
        self.ids=np.full((self.ny,self.nx),-1,int);self.ids[jj,ii]=np.arange(self.nc)
        eu=[];ev=[];axes=[];dist=[];areas=[]
        border=np.zeros(self.nc,bool);up_area=np.zeros(self.n)
        for a,(j,i) in enumerate(zip(jj,ii)):
            for dj,di,axis,step,width in [(0,1,0,self.dx,self.dy),(1,0,1,self.dy,self.dx)]:
                j2,i2=j+dj,i+di
                b=self.ids[j2,i2] if j2<self.ny and i2<self.nx else -1
                if b>=0:
                    for k in range(self.nz):
                        eu.append(k*self.nc+a);ev.append(k*self.nc+b);axes.append(axis)
                        dist.append(step);areas.append(width*(self.dz[a]+self.dz[b])/2)
            for dj,di in [(0,1),(0,-1),(1,0),(-1,0)]:
                j2,i2=j+dj,i+di
                if not(0<=j2<self.ny and 0<=i2<self.nx) or self.ids[j2,i2]<0:
                    border[a]=True
                    if di==-1 and self.xy[a,0]<self.hydro['upstream_x_limit_m']:
                        for k in range(self.nz):up_area[k*self.nc+a]+=self.dy*self.dz[a]
            for k in range(self.nz-1):
                eu.append(k*self.nc+a);ev.append((k+1)*self.nc+a);axes.append(2)
                dist.append(self.dz[a]);areas.append(self.dx*self.dy)
        self.eu=np.asarray(eu,int);self.ev=np.asarray(ev,int);self.axis=np.asarray(axes,int)
        self.distance=np.asarray(dist);self.area=np.asarray(areas)
        kval=np.array([self.hydro['kx_m_d'],self.hydro['ky_m_d'],self.hydro['kz_m_d']])
        log_k=np.asarray(self.hydro.get('cell_log_k',np.zeros(self.n)),float)
        if log_k.shape!=(self.n,) or not np.all(np.isfinite(log_k)):
            raise ValueError('cell_log_k must contain one finite log multiplier per active cell')
        self.cell_k=np.exp(log_k)[:,None]*kval[None,:]
        ka=self.cell_k[self.eu,self.axis];kb=self.cell_k[self.ev,self.axis]
        self.conductance=(2*ka*kb/(ka+kb))*self.area/self.distance
        self.flow_lap=self.edge_laplacian(self.conductance)
        radius=np.linalg.norm(self.xy-np.array([0,325.9]),axis=1)
        chd=border & (radius>=self.hydro['downstream_radius_m'])
        if not np.any(chd):raise ValueError('Grid has no downstream boundary.')
        self.chd=np.tile(chd,self.nz);self.free=np.flatnonzero(~self.chd)
        self.bound=np.flatnonzero(self.chd)
        self.external_in=np.zeros(self.n)
        enhanced=self.source_mask(self.src['center_xy_m'])[:self.nc]
        recharge=np.where(enhanced,self.hydro['enhanced_recharge_m_d'],self.hydro['recharge_m_d'])
        self.external_in[:self.nc]=recharge*self.dx*self.dy
        self.external_in+=up_area*self.hydro['upstream_flux_m_d']
        self.tree=cKDTree(self.xyz/np.array([self.dx,self.dy,np.mean(self.dz)]))
        self.base=self.flow_transport([])

    def edge_laplacian(self,t):
        a,b=self.eu,self.ev
        return sparse.coo_matrix((np.r_[t,t,-t,-t],(np.r_[a,b,a,b],np.r_[a,b,b,a])),shape=(self.n,self.n)).tocsr()

    def source_mask(self,center):
        d=np.abs(self.xy-np.asarray(center));half=np.asarray(self.src['half_size_xy_m'])
        m=np.all(d<=half,axis=1)
        if not np.any(m):m[np.argmin(np.sum((d/half)**2,axis=1))]=True
        out=np.zeros(self.n,bool);out[:self.nc]=m
        return out

    def smooth_mask(self,center,derivative=False):
        widths=self.cfg['inverse']['smooth_edge_cells']*np.array([self.dx,self.dy])
        half=np.asarray(self.src['half_size_xy_m']);x=self.xy-np.asarray(center)
        # Compact support avoids imposing an almost-Dirichlet value throughout
        # the domain through tiny but nonzero logistic tails.
        s=np.clip((half+widths-np.abs(x))/(2*widths),0,1)
        w=s*s*(3-2*s);out=np.zeros(self.n);out[:self.nc]=w[:,0]*w[:,1]
        if not derivative:return out
        dw=3*s*(1-s)*np.sign(x)/widths
        deriv=np.zeros((self.n,2));deriv[:self.nc,0]=dw[:,0]*w[:,1];deriv[:self.nc,1]=dw[:,1]*w[:,0]
        return out,deriv

    def source_penalty(self,center):
        w,dw=self.smooth_mask(center,True);eps=1e-8
        base=(self.water/np.min(np.diff(self.times()))+self.base['L'].diagonal())*self.cfg['inverse']['dirichlet_penalty_factor']
        return base*w/(1-w+eps),base[:,None]*(1+eps)*dw/(1-w[:,None]+eps)**2

    def nearest(self,points):
        p=np.atleast_2d(points)
        return self.tree.query(p/np.array([self.dx,self.dy,np.mean(self.dz)]))[1].astype(int)

    def flow_transport(self,pumps):
        extraction=np.zeros(self.n)
        for p in pumps:
            idx=int(p['cell']);q=float(p['rate'])
            if idx<0 or idx>=self.n or self.chd[idx] or q<0:raise ValueError('Invalid pumping screen/rate.')
            extraction[idx]+=q
        rhs=self.external_in-extraction
        h=np.full(self.n,self.hydro['downstream_head_m'])
        A=self.flow_lap[self.free][:,self.free].tocsc()
        b=rhs[self.free]-self.flow_lap[self.free][:,self.bound]@h[self.bound]
        if len(self.free)>25000:
            inv=1/A.diagonal();M=LinearOperator(A.shape,matvec=lambda x:inv*x)
            sol,info=cg(A,b,x0=h[self.free],tol=1e-10,maxiter=10000,M=M)
            if info:raise RuntimeError('Darcy CG failed: '+str(info))
            h[self.free]=sol
        else:h[self.free]=spsolve(A,b)
        edge_q=self.conductance*(h[self.eu]-h[self.ev])
        # CHD water exchange is recovered from the discrete balance.
        exchange=np.zeros(self.n);exchange[self.bound]=(self.flow_lap@h-rhs)[self.bound]
        incoming=self.external_in+np.maximum(exchange,0)
        outgoing=extraction+np.maximum(-exchange,0)
        # Diagonal dispersion approximation: full 3-D connectivity, no cross terms.
        vel=np.zeros((self.n,3));counts=np.zeros((self.n,3))
        for ax in range(3):
            sel=self.axis==ax;v=edge_q[sel]/(self.area[sel]*self.hydro['porosity'])
            for nodes in [self.eu[sel],self.ev[sel]]:
                np.add.at(vel[:,ax],nodes,v);np.add.at(counts[:,ax],nodes,1)
        vel/=np.maximum(counts,1);speed=np.linalg.norm(vel,axis=1)
        dispers=np.zeros_like(vel);small=self.hydro['diffusion_m2_d']
        for ax in [0,1]:
            dispers[:,ax]=self.hydro['alpha_TH_m']*speed+(self.hydro['alpha_L_m']-self.hydro['alpha_TH_m'])*vel[:,ax]**2/np.maximum(speed,1e-20)+small
        dispers[:,2]=self.hydro['alpha_TV_m']*speed+small
        da=dispers[self.eu,self.axis];db=dispers[self.ev,self.axis]
        diff=2*da*db/np.maximum(da+db,1e-30)*self.hydro['porosity']*self.area/self.distance
        L=self.edge_laplacian(diff)
        pos=edge_q>=0;fr=np.where(pos,self.eu,self.ev);to=np.where(pos,self.ev,self.eu);q=np.abs(edge_q)
        adv=sparse.coo_matrix((np.r_[q,-q],(np.r_[fr,to],np.r_[fr,fr])),shape=(self.n,self.n)).tocsr()
        L=L+adv+sparse.diags(outgoing)
        residual=self.flow_lap@h-rhs-exchange
        return {'head':h,'L':L.tocsr(),'velocity':vel,'pumps':extraction,
                'flow_balance_max_m3_d':float(np.max(np.abs(residual))),
                'boundary_exchange':exchange,'incoming_water':incoming,'outgoing_water':outgoing}

    def times(self):
        dt=self.cfg['time'][self.profile+'_dt_day'];s=self.src
        return np.unique(np.r_[0,np.arange(dt,s['end_day'],dt),s['switch_day'],s['end_day'],self.cfg['time']['observation_days'],self.cfg['control'].get('period_edges_day',[])])

    def concentration(self,t,amplitude=None):
        a=self.src['first_concentration_mg_L'] if amplitude is None else float(amplitude)
        # A step ending at the switch uses the first-period concentration.
        return a if t<=self.src['switch_day']+1e-8 else a*self.src['second_ratio']

    def simulate(self,center=None,amplitude=None,pumps=None,smooth=False,return_system=False,pump_schedule=None,end_day=None):
        center=self.src['center_xy_m'] if center is None else center
        if pumps and pump_schedule:raise ValueError('Use pumps or pump_schedule, not both')
        schedule=[]
        if pumps:
            schedule=[{'start_day':self.cfg['control']['start_day'],'end_day':self.cfg['control']['end_day'],'pumps':pumps}]
        elif pump_schedule is not None:schedule=sorted(pump_schedule,key=lambda x:x['start_day'])
        for i,item in enumerate(schedule):
            if not 0<=item['start_day']<item['end_day']<=self.src['end_day']:
                raise ValueError('Invalid pumping period')
            if i and item['start_day']<schedule[i-1]['end_day']:raise ValueError('Overlapping pumping periods')
        stop=self.src['end_day'] if end_day is None else float(end_day)
        if not 0<stop<=self.src['end_day']:raise ValueError('Invalid simulation end_day')
        edges=[v for p in schedule for v in (p['start_day'],p['end_day'])]
        times=np.unique(np.r_[self.times(),edges,stop]);times=times[times<=stop]
        C=np.zeros((len(times),self.n));system=self.base
        systems=[self.flow_transport(p['pumps']) for p in schedule]
        pumped=systems[-1] if systems else None;cache={};sources=[]
        mask=self.source_mask(center);free=np.flatnonzero(~mask);fixed=np.flatnonzero(mask)
        penalty=self.source_penalty(center)[0] if smooth else np.zeros(self.n)
        source_flux=[];mass_balance=[];pump_mass=[];boundary_mass=[];drawdown=[]
        for k in range(1,len(times)):
            dt=times[k]-times[k-1];mid=(times[k]+times[k-1])/2
            system=self.base
            for period,psystem in zip(schedule,systems):
                if period['start_day']<=mid<period['end_day']:system=psystem;break
            L=system['L'];src=self.concentration(mid,amplitude);key=(float(dt),id(L),smooth)
            if key not in cache:
                A=(L+sparse.diags(self.water/dt)).tocsc()
                if smooth:
                    A=A+sparse.diags(penalty);cache[key]=(splu(A.tocsc()),A)
                else:cache[key]=(splu(A[free][:,free].tocsc()),A)
            lu,A=cache[key];rhs=self.water/dt*C[k-1]
            if smooth:C[k]=lu.solve(rhs+penalty*src)
            else:
                C[k,fixed]=src
                C[k,free]=lu.solve(rhs[free]-A[free][:,fixed]@C[k,fixed])
            defect=self.water/dt*(C[k]-C[k-1])+L@C[k]
            sf=penalty*(src-C[k]) if smooth else np.where(mask,defect,0)
            source_flux.append(float(np.sum(sf)*1000))
            mass_balance.append(float(abs(np.sum(defect)-np.sum(sf))*1000))
            pump_mass.append(float(dt*np.dot(system['pumps'],C[k])*1e-3))
            boundary_mass.append(float(dt*np.dot(np.maximum(-system['boundary_exchange'],0),C[k])*1e-3))
            drawdown.append(float(np.max(self.base['head']-system['head'])))
            sources.append((dt,system,key))
        if np.min(C)<-1e-7 or not np.all(np.isfinite(C)):raise RuntimeError('Invalid transport concentrations.')
        result={'times':times,'C':C,'source_flux_mg_d':source_flux,'mass_balance_error_mg_d':mass_balance,
                'pumped_system':pumped,'pumped_systems':systems,'max_drawdown_m':max(drawdown,default=0.),
                'pumped_mass_kg':float(sum(pump_mass)),'boundary_output_mass_kg':float(sum(boundary_mass)),
                'net_source_input_mass_kg':float(np.dot(np.diff(times),source_flux)*1e-6),
                'mode':'smooth_inverse_penalty' if smooth else 'exact_cell_Dirichlet'}
        if return_system:result.update({'cache':cache,'steps':sources,'penalty':penalty})
        return result

    def smooth_objective(self,theta,obs,gradient=False):
        """Exact discrete adjoint of the smooth inverse approximation.

        theta=(x,y,Cfirst); source concentration changes by the fixed ratio at 40y.
        No hidden truth is read. Gradients are with respect to metres and mg/L.
        """
        result=self.simulate(theta[:2],theta[2],smooth=True,return_system=True)
        C=result['C'];ti=np.asarray(obs['time_indices']);wi=np.asarray(obs['cell_indices'])
        pred=C[ti,wi];scale=500.;res=(pred-np.asarray(obs['values']))/scale
        obj=.5*np.mean(res**2)
        if not gradient:return obj,pred,result
        forcing=np.zeros_like(C)
        np.add.at(forcing,(ti,wi),res/(len(res)*scale))
        p,dp=self.source_penalty(theta[:2]);g=np.zeros(3);lams=np.zeros_like(C)
        for k in range(len(result['times'])-1,0,-1):
            dt,system,key=result['steps'][k-1];lu,A=result['cache'][key]
            rhs=forcing[k].copy()
            if k<len(C)-1:rhs+=self.water/(result['times'][k+1]-result['times'][k])*lams[k+1]
            lam=lu.solve(rhs,trans='T');lams[k]=lam
            mid=(result['times'][k]+result['times'][k-1])/2
            ratio=self.concentration(mid,1.)
            g[:2]+=np.sum(lam[:,None]*dp*(theta[2]*ratio-C[k])[:,None],axis=0)
            g[2]+=np.dot(lam,p*ratio)
        result['adjoint']=lams;return obj,g,pred,result

    def well_candidates(self,count=45):
        valid=np.flatnonzero(~self.chd)
        # A fixed space-filling pool; screen elevation is a genuine 3-D variable.
        stride=max(1,len(valid)//count)
        return valid[::stride][:count]

def prepare_dataset(scene,outdir):
    out=Path(outdir);out.mkdir(parents=True,exist_ok=True)
    ref=scene.simulate();np.savez_compressed(out/'reference_evaluation_only.npz',times=ref['times'],C=ref['C'])
    requested=[]
    for x,y in [(350,340),(430,370),(520,410),(650,460),(800,520),(400,280),(550,550),(700,350)]:
        col=np.argmin(np.sum((scene.xy-[x,y])**2,axis=1))
        for fraction in [.15,.50,.85]:
            k=min(scene.nz-1,int(fraction*scene.nz));requested.append(k*scene.nc+col)
    cells=np.unique(requested);obs_times=scene.cfg['time']['observation_days']
    ti=np.array([np.argmin(abs(ref['times']-t)) for t in obs_times])
    ii,jj=np.meshgrid(ti,cells,indexing='ij');vals=ref['C'][ii,jj]
    np.savez_compressed(out/'observations.npz',time_indices=ii.ravel(),cell_indices=jj.ravel(),values=vals.ravel(),times=ref['times'])
    np.savetxt(out/'monitoring_wells.csv',np.c_[cells,scene.xyz[cells]],delimiter=',',header='cell,x_m,y_m,z_m',comments='')
    np.savez_compressed(out/'grid.npz',xyz=scene.xyz,volume_m3=scene.vol,mask=scene.mask2,head=scene.base['head'])
    json_write(out/'metadata.json',{'profile':scene.profile,'cells':scene.n,'noise_added':False,'scene_signature':scene.signature,
        'geometry':'fan_polygon_variable_bottom_in_computation','source_condition':'top_cell_prescribed_concentration',
        'concentration_mode':ref['mode'],'source_switch_day':14600,'time_end_day':21900,
        'bottom_range_m':[float(scene.bottom.min()),float(scene.bottom.max())],
        'max_flow_balance_m3_d':scene.base['flow_balance_max_m3_d'],
        'max_transport_balance_mg_d':max(ref['mass_balance_error_mg_d']),
        'concentration_min_max':[float(ref['C'].min()),float(ref['C'].max())]})
    return out

def load_observations(path,scene=None):
    if scene is not None:
        metadata=json.loads((Path(path)/'metadata.json').read_text(encoding='utf-8'))
        if metadata.get('scene_signature')!=scene.signature:
            raise ValueError('Dataset differs from current scene/code. Run run_borden3d.py --task prepare --profile '+scene.profile)
    with np.load(Path(path)/'observations.npz') as a:return {k:a[k] for k in a.files}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--profile',choices=['smoke','quick','full'],default='quick')
    parser.add_argument('--out',default=str(ROOT/'Borden3D_共享数据'))
    args=parser.parse_args();scene=BordenScene(args.profile);prepare_dataset(scene,args.out)
    print(json.dumps({'cells':scene.n,'dataset':args.out},ensure_ascii=False))
