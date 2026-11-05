"""Joint package probabilities on the original RDL. Shared poses, never marginal products."""
from dataclasses import replace
import math
import numpy as np
from scipy.special import ndtr,ndtri
from scipy.stats import beta,qmc
from .distribution import bounded_poses,normal_interval,normalization
from .statistics import mean_and_bounds
from .width_analysis import WidthAnalyzer
from .width_yield_geometry import WidthEvents

BATCH=32


def _truncated_quantile(unit,lo,hi):
    # On positive tails interpolate survival probability to retain precision.
    left=ndtr(lo)+unit*normal_interval(lo,hi)
    right=ndtr(-hi)+(1-unit)*normal_interval(lo,hi)
    clip=lambda x:np.clip(x,np.finfo(float).tiny,np.nextafter(1.,0.))
    return np.where(np.asarray(lo)>=0,-ndtri(clip(right)),ndtri(clip(left)))


def joint_batches(params,count,cfg):
    laws=[params.for_die(i) for i in range(count)]
    bounds=[mean_and_bounds(p,count) for p in laws]
    if not any(b.any() for _,b in bounds):
        yield np.zeros((1,count,3)),None,'exact',1
        return
    deterministic=cfg.yield_method=='partition'
    n=(2**int(math.log2(cfg.yield_box_budget)) if deterministic else 2**cfg.yield_sample_exponent)
    integrator=qmc.Sobol(3*count,scramble=False) if deterministic else None
    for start in range(0,n,BATCH):
        if not deterministic:
            samples=[]
            for owner,p in enumerate(laws):
                seed=int(np.random.SeedSequence([cfg.random_seed,start,owner]).generate_state(1)[0])
                local=replace(cfg,probability_method='monte_carlo',sample_exponent=int(math.log2(BATCH)),random_seed=seed)
                samples.append(bounded_poses(p,count,local))
            yield np.stack(samples,axis=1),None,'mc',n
            continue
        # Deterministic joint cubature. Draw X from its truncated marginal, then
        # Y from its conditional truncated marginal. The conditional acceptance
        # weights restore the jointly truncated XY law, also across independent DIEs.
        unit=integrator.random(BATCH).reshape(BATCH,count,3)+.5/n
        weights=np.ones(BATCH);states=np.zeros_like(unit)
        for i,(p,(mu,b)) in enumerate(zip(laws,bounds)):
            k=p.scale_nsigma;rho=p.rho_xy
            z=_truncated_quantile(unit[:,i],-k,k)
            if b[0] and b[1] and rho:
                if abs(rho)==1:z[:,1]=rho*z[:,0]
                else:
                    s=math.sqrt((1-rho)*(1+rho));center=rho*z[:,0]
                    lo=(-k-center)/s;hi=(k-center)/s
                    z[:,1]=center+s*_truncated_quantile(unit[:,i,1],lo,hi)
                    mass=normalization(k,rho,True,True,False)[0]
                    weights*=normal_interval(-k,k)*normal_interval(lo,hi)/mass
            states[:,i]=mu+z*(b/k)
        yield states,weights,'cubature',n


def packed_events(red,yellow,weights=None):
    any_risk=red_risk=0;lines={}
    for i,(r,y) in enumerate(zip(red,yellow)):
        bit=1<<i
        if r:red_risk|=bit
        if r or y:any_risk|=bit
        for ref in r|y:lines[ref]=lines.get(ref,0)|bit
    return len(red),any_risk,red_risk,lines,weights


def policy_yields(bank,ignored):
    """Union event masks by sample before weighting. Ignoring needs no geometry rerun."""
    passed=np.zeros(3);mass=0.;samples=0
    for n,all_risk,red,per_line,weights in bank['chunks']:
        ignored_hit=0
        for ref in ignored:ignored_hit|=per_line.get(ref,0)
        failures=(all_risk,red|ignored_hit,ignored_hit)
        samples+=n
        if weights is None:
            mass+=n;passed+=[n-int(mask).bit_count() for mask in failures]
        else:
            mass+=float(weights.sum())
            for i,mask in enumerate(failures):
                safe=np.fromiter((not (mask>>j)&1 for j in range(n)),bool,count=n)
                passed[i]+=weights[safe].sum()
    if not mass:return None
    estimates=passed/mass
    intervals=None
    if bank['method']=='mc':
        alpha=.01/3
        intervals=[(float(beta.ppf(alpha/2,s,samples-s+1)) if s else 0.,
                    float(beta.ppf(1-alpha/2,s+1,samples-s)) if s<samples else 1.) for s in passed]
        if not ignored:intervals[2]=(1.,1.)
    return dict(values=estimates,intervals=intervals,samples=samples,
                total=bank['total'],method=bank['method'])


def measure_width_yield(model,params,cfg,cache_group,progress,partial=None):
    # Width acceptance refers to the source layout, independent of a displayed
    # individual pose, a repaired preview, colours, and the current ignored set.
    if not any(r in ('运动RDL','固定RDL') for r in model.roles.values()):
        raise ValueError('图纸尚未设置 RDL 层')
    if math.isinf(params.target_nsigma):params=replace(params,target_nsigma=0)
    model.configure_parameters(params);model.bound.acceleration=cfg.acceleration
    analyzer=WidthAnalyzer(model,params,cfg,cache_group)
    analyzer.probability=None  # Bounds used solely to fade preview annotations are irrelevant here.
    try:
        progress(1,'建立整包宽度通道')
        events=WidthEvents(analyzer,params,lambda n,t:progress(1+round(.08*n),t))
        count=len(model.bound.centers)
        chunks=[];done=0;next_preview=BATCH
        for states,weights,method,total in joint_batches(params,count,cfg):
            red,yellow=events.classify(states)
            chunks.append(packed_events(red,yellow,weights));done+=len(states)
            progress(10+round(90*done/total),f'整包宽度良率 {done}/{total}')
            if partial and done>=next_preview and done<total:
                partial(dict(chunks=tuple(chunks),method=method,total=total,scale_nsigma=params.scale_nsigma))
                next_preview=done*2
        return dict(chunks=tuple(chunks),method=method,total=total,scale_nsigma=params.scale_nsigma)
    finally:analyzer.close()
