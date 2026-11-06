"""Full-support mixture importance sampling for rare package width failures."""
import math
import numpy as np
from scipy.special import logsumexp
from .distribution import normal_interval,normalization
from .statistics import mean_and_bounds


def mixture_batches(params,count,cfg,batch=64):
    from .width_yield import _truncated_quantile
    laws=[params.for_die(i) for i in range(count)]
    bounds=[mean_and_bounds(p,count) for p in laws]
    active=[i for i,(_,b) in enumerate(bounds) if b.any()]
    if not active:
        yield np.zeros((1,count,3)),None,1.,1
        return
    total=2**cfg.width_yield_exponent;k=params.scale_nsigma
    q=float(normal_interval(-k,k))
    normalizers=[normalization(k,p.rho_xy,bool(b[0]),bool(b[1]),False) for p,(_,b) in zip(laws,bounds)]
    # q0 is conditional-truncated Gaussian. f/q0 <= product(q/Z) for
    # correlated XY; the mixture retains q0 with mass 1/2.
    bound=2.
    for p,(_,b),(z,err) in zip(laws,bounds,normalizers):
        if b[0] and b[1] and 0<abs(p.rho_xy)<1:bound*=q/max(np.finfo(float).tiny,z-err)
    for start in range(0,total,batch):
        n=min(batch,total-start)
        rng=np.random.default_rng(np.random.SeedSequence([cfg.width_yield_seed,start,350]))
        unit=rng.random((n,count,3));selector=rng.random(n)
        mode=np.full(n,-1,int)  # -1: q0; count: all DIE uniform; other: one DIE
        local=(selector>=.5)&(selector<.75)
        mode[local]=np.array(active)[np.minimum(len(active)-1,((selector[local]-.5)*4*len(active)).astype(int))]
        mode[selector>=.75]=count
        states=np.zeros_like(unit);ratios=[];log_target_ratio=np.zeros(n)
        for owner,(p,(mu,b),(mass,_)) in enumerate(zip(laws,bounds,normalizers)):
            mask=(mode==owner)|(mode==count);rho=p.rho_xy
            z=_truncated_quantile(unit[:,owner],-k,k)
            if b[0] and b[1] and abs(rho)==1:z[:,1]=rho*z[:,0]
            elif b[0] and b[1] and rho:
                s=math.sqrt((1-rho)*(1+rho));center=rho*z[:,0]
                z[:,1]=center+s*_truncated_quantile(unit[:,owner,1],(-k-center)/s,(k-center)/s)
            z[mask]=-k+2*k*unit[mask,owner]
            if b[0] and b[1] and abs(rho)==1:z[mask,1]=rho*z[mask,0]
            states[:,owner]=mu+z*b/k
            if owner not in active:continue
            log_q0=np.zeros(n);dimensions=0
            normal=lambda v:-.5*v*v-.5*math.log(2*math.pi)-math.log(q)
            if b[0]:log_q0+=normal(z[:,0]);dimensions+=1
            if b[1]:
                if not b[0] or rho==0:log_q0+=normal(z[:,1]);dimensions+=1
                elif abs(rho)<1:
                    s=math.sqrt((1-rho)*(1+rho));center=rho*z[:,0]
                    acceptance=normal_interval((-k-center)/s,(k-center)/s)
                    v=(z[:,1]-center)/s
                    log_q0+=-.5*v*v-.5*math.log(2*math.pi)-math.log(s)-np.log(acceptance)
                    log_target_ratio+=math.log(q/mass)+np.log(acceptance);dimensions+=1
            if b[2]:log_q0+=normal(z[:,2]);dimensions+=1
            ratios.append(-dimensions*math.log(2*k)-log_q0)
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
