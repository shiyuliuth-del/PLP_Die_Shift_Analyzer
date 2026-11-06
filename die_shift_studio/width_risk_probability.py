"""Per-line unions and per-mouth events, from the same whole-input yield bank."""
import math
import numpy as np
from matplotlib.colors import to_rgb

def enrich(bank):
    """Run in the numerical worker; paint only looks up immutable records."""
    method=bank['method']
    def record(p,success,interval=None):
        return dict(p=float(np.clip(p,0,1)),success=float(np.clip(success,0,1)),interval=interval,method=method)
    if method=='grid':
        from .width_grid import weighted_pair
        from .extreme_filter import ProblemIndex
        memo={}
        def evaluate(clauses):
            if clauses not in memo:
                success,failed=weighted_pair(ProblemIndex(bank['grid'],clauses),bank['grid'].weights)
                memo[clauses]=record(failed,success)
            return memo[clauses]
        lines={key:evaluate(v) for key,v in bank['by_line'].items()}
        sites={key:evaluate(v) for key,v in bank.get('by_site',{}).items()}
        default=record(0.,1.)
    else:
        chunks=bank['chunks'];site_chunks=bank.get('site_chunks',[{} for _ in chunks])
        totals={};mass=mass2=0.;nseen=0
        for (n,_,_,line_masks,weights),sites in zip(chunks,site_chunks):
            weights=np.ones(n) if weights is None else np.asarray(weights)
            squared=weights**2;wm=float(weights.sum());w2=float(squared.sum())
            masks={('line',key):mask for key,mask in line_masks.items()}
            masks.update({('site',key):mask for key,mask in sites.items()})
            for key in masks:
                if key not in totals:totals[key]=np.array([0.,mass,0.,mass2])
            for key,stat in totals.items():
                mask=masks.get(key,0)
                if not mask:stat[1]+=wm;stat[3]+=w2;continue
                hit=np.fromiter((bool(mask>>i&1) for i in range(n)),bool,count=n)
                stat+=weights[hit].sum(),weights[~hit].sum(),squared[hit].sum(),squared[~hit].sum()
            mass+=wm;mass2+=w2;nseen+=n
        if not mass:raise ArithmeticError('宽度概率积分质量为0')
        def evaluate(stat):
            fail,passed,fail2,passed2=stat;interval=None
            if method=='importance':
                from .width_sampling import bounded_interval
                ml,mh=bounded_interval(mass,mass2,nseen,bank['weight_bound'])
                fl,fh=bounded_interval(fail,fail2,nseen,bank['weight_bound'])
                sl,sh=bounded_interval(passed,passed2,nseen,bank['weight_bound'])
                low=max(fl/mh,1-min(1.,sh/ml)) if ml else fl/mh
                high=min(1.,fh/ml,1-sl/mh) if ml else 1-sl/mh
                interval=(max(0.,low),max(0.,min(1.,high)))
            return record(fail/mass,passed/mass,interval)
        lines={key:evaluate(v) for (kind,key),v in totals.items() if kind=='line'}
        sites={key:evaluate(v) for (kind,key),v in totals.items() if kind=='site'}
        default=evaluate(np.array([0.,mass,0.,mass2]))
    return dict(bank,probability_lines=lines,probability_sites=sites,default_probability=default)

def lookup(bank,kind,key):
    if bank is None or key is None:return None
    return bank.get('probability_'+kind,{}).get(key,bank.get('default_probability'))

def lookup_union(bank,keys):
    keys=tuple(dict.fromkeys(k for k in keys if k is not None))
    if bank is None or not keys:return None
    if len(keys)==1:return lookup(bank,'sites',keys[0])
    if bank['method']=='grid':
        from .width_grid import weighted_pair
        from .extreme_filter import ProblemIndex
        clauses=tuple(sorted({c for key in keys for c in bank['by_site'].get(key,())},key=lambda c:c.allowed))
        passed,failed=weighted_pair(ProblemIndex(bank['grid'],clauses),bank['grid'].weights)
        return dict(p=failed,success=passed,interval=None,method='grid')
    copies=[]
    for masks in bank.get('site_chunks',[]):
        combined=0
        for key in keys:combined|=masks.get(key,0)
        copies.append({('union',):combined} if combined else {})
    local=dict(bank,chunks=tuple((n,a,r,{},w) for n,a,r,_,w in bank['chunks']),site_chunks=tuple(copies))
    return lookup(enrich(local),'sites',('union',))


def probability_text(value,decimals):
    if value is None:return '待计算'
    from .width_yield import percentage_text
    p,s=value['p'],value['success'];method=value['method']
    interval=value.get('interval')
    if method=='exact':return percentage_text(p,s,decimals)
    if method=='grid':
        return '≈'+percentage_text(p,s,decimals)+('（网格未命中）' if p==0 else '')
    if interval and p==0:return '≤'+percentage_text(interval[1],max(0.,1-interval[1]),decimals)+'（样本未命中）'
    if interval and s==0:return '≥'+percentage_text(interval[0],max(0.,1-interval[0]),decimals)
    return '≈'+percentage_text(p,s,decimals)

def probability_color(color,value):
    """Probability changes RGB lightness only; callers retain their fixed alpha."""
    base=np.asarray(to_rgb(color))
    if value is None:level=.25
    else:
        p=min(1.,max(0.,value['p']))
        level=math.log1p(1e12*p)/math.log1p(1e12)
    pale=.9+.1*base;deep=.72*base
    return tuple((1-level)*pale+level*deep)
