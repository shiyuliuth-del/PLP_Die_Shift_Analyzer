"""Full-support mixture importance sampling for rare package width failures."""
import math
import numpy as np
from scipy.special import logsumexp
from .distribution import normal_interval,normalization_axes
from .statistics import mean_and_bounds,std_devs


def mixture_batches(params,count,cfg,batch=64):
    from .width_yield import _truncated_quantile
    laws=[params.for_die(i) for i in range(count)]
    bounds=[mean_and_bounds(p,count) for p in laws]
    sigmas=[np.where(b>0,std_devs(p),0.) for p,(_,b) in zip(laws,bounds)]
    active=[i for i,(b,s) in enumerate(zip((x[1] for x in bounds),sigmas)) if ((b>0)&(s>0)).any()]
    if not active:
        mu_states=np.array([[mu for mu,_ in bounds]],float) if bounds else np.zeros((1,count,3))
        yield mu_states,None,1.,1
        return
    total=2**cfg.width_yield_exponent
    die_axes=[]
    bound=2.
    for p,(_,b),sig in zip(laws,bounds,sigmas):
        ax=(b>0)&(sig>0)
        ks=np.array([float(b[j]/sig[j]) if ax[j] else 0. for j in range(3)])
        qs=np.array([float(normal_interval(-ks[j],ks[j])) if ax[j] else 1. for j in range(3)])
        z_xy,err_xy=normalization_axes(ks[0],ks[1],math.inf,p.rho_xy,bool(ax[0]),bool(ax[1]),False)
        if ax[0] and ax[1] and 0<abs(p.rho_xy)<1:bound*=qs[0]/max(np.finfo(float).tiny,z_xy-err_xy)
        die_axes.append((ax,ks,qs,z_xy))
    for start in range(0,total,batch):
        n=min(batch,total-start)
        rng=np.random.default_rng(np.random.SeedSequence([cfg.width_yield_seed,start,350]))
        unit=rng.random((n,count,3));selector=rng.random(n)
        mode=np.full(n,-1,int)  # -1: q0; count: all DIE uniform; other: one DIE
        local=(selector>=.5)&(selector<.75)
        mode[local]=np.array(active)[np.minimum(len(active)-1,((selector[local]-.5)*4*len(active)).astype(int))]
        mode[selector>=.75]=count
        states=np.zeros_like(unit);ratios=[];log_target_ratio=np.zeros(n)
        for owner,(p,(mu,b),sig,(ax,ks,qs,mass)) in enumerate(zip(laws,bounds,sigmas,die_axes)):
            mask=(mode==owner)|(mode==count);rho=p.rho_xy
            z=np.zeros((n,3))
            for j in range(3):
                if ax[j]:z[:,j]=_truncated_quantile(unit[:,owner,j],-ks[j],ks[j])
            if ax[0] and ax[1] and abs(rho)==1:
                k_eff=min(ks[0],ks[1]);z[:,0]=_truncated_quantile(unit[:,owner,0],-k_eff,k_eff);z[:,1]=rho*z[:,0]
            elif ax[0] and ax[1] and rho:
                s=math.sqrt((1-rho)*(1+rho));center=rho*z[:,0]
                z[:,1]=center+s*_truncated_quantile(unit[:,owner,1],(-ks[1]-center)/s,(ks[1]-center)/s)
            for j in range(3):
                if ax[j]:z[mask,j]=-ks[j]+2*ks[j]*unit[mask,owner,j]
            if ax[0] and ax[1] and abs(rho)==1:
                k_eff=min(ks[0],ks[1]);z[mask,0]=-k_eff+2*k_eff*unit[mask,owner,0];z[mask,1]=rho*z[mask,0]
            states[:,owner]=mu+z*sig
            if owner not in active:continue
            log_q0=np.zeros(n);log_unif=0.
            normal=lambda v,q:-.5*v*v-.5*math.log(2*math.pi)-math.log(q)
            if ax[0] and ax[1] and abs(rho)==1:
                k_eff=min(ks[0],ks[1]);q_eff=float(normal_interval(-k_eff,k_eff))
                log_q0+=normal(z[:,0],q_eff);log_unif+=-math.log(2*k_eff)
            else:
                if ax[0]:log_q0+=normal(z[:,0],qs[0]);log_unif+=-math.log(2*ks[0])
                if ax[1]:
                    if not ax[0] or rho==0:log_q0+=normal(z[:,1],qs[1]);log_unif+=-math.log(2*ks[1])
                    else:
                        s=math.sqrt((1-rho)*(1+rho));center=rho*z[:,0]
                        acceptance=normal_interval((-ks[1]-center)/s,(ks[1]-center)/s)
                        v=(z[:,1]-center)/s
                        log_q0+=-.5*v*v-.5*math.log(2*math.pi)-math.log(s)-np.log(acceptance)
                        log_target_ratio+=math.log(qs[0]/mass)+np.log(acceptance);log_unif+=-math.log(2*ks[1])
            if ax[2]:log_q0+=normal(z[:,2],qs[2]);log_unif+=-math.log(2*ks[2])
            ratios.append(log_unif-log_q0)
        ratios=np.array(ratios)
        terms=np.vstack((np.full(n,math.log(.5)),math.log(.25/len(active))+ratios,math.log(.25)+ratios.sum(axis=0)))
        weights=np.exp(log_target_ratio-logsumexp(terms,axis=0))
        if np.max(weights)>bound*(1+1e-10):raise ArithmeticError('尾部积分权重超过理论上界')
        yield states,weights,bound,total


def bounded_interval(total,squares,n,bound,alpha=.01,claims=6):
    """Two-sided empirical Bernstein bound; union bound covers six means."""
    if n<2:return 0.,bound
    mean=total/n
    variance=max(0.,(squares-total*total/n)/(n-1))
    log=math.log(4*claims/alpha)
    radius=math.sqrt(2*variance*log/n)+7*bound*log/(3*(n-1))
    return max(0.,mean-radius),min(bound,mean+radius)
