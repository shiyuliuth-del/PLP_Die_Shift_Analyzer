"""Conservative zone probabilities; a finite grid never certifies a rare event.

A deliberately restricted continuous certificate is available for rectangular
straight traces under translations with a certified perpendicular candidate. Other configurations
retain [0,1] even when their finite-grid estimate is small.
"""
import math
import numpy as np
import shapely
from scipy.special import ndtr
from .statistics import mean_and_bounds
from .model_yield import PhysicalMass
from .width_grid import weighted_pair
from .extreme_filter import ProblemIndex
from .resources import cancel_check


def extreme_weights(grid,p):
    out=[];error=0.
    for owner,poses in enumerate(grid.locals):
        law=PhysicalMass(p.for_die(owner),grid.count);mu,b=mean_and_bounds(p.for_die(owner),grid.count)
        axes=[np.unique(poses[:,j]) for j in range(3)]
        edges=[np.r_[m-d,(a[:-1]+a[1:])/2,m+d] if d else np.array([0.,0.]) for a,m,d in zip(axes,mu,b)]
        edges=[np.clip(a,m-d,m+d) for a,m,d in zip(edges,mu,b)]
        weights=[]
        for pose in poses:
            digits=[int(np.searchsorted(a,v)) for a,v in zip(axes,pose)]
            lo=np.array([e[d] for e,d in zip(edges,digits)]);hi=np.array([e[d+1] for e,d in zip(edges,digits)])
            mass,err=law(lo,hi);weights.append(mass);error+=err
        weights=np.array(weights);total=weights.sum()
        if total<=0:raise ArithmeticError('重构极端网格概率质量为零')
        error+=abs(1-total);out.append(weights/total)
    return out,error


def central_certificate(r,entry,entries):
    """A sufficient bound, never a numerical-DRC sample masquerading as proof.

    F(x)=x+w(s)D has ||grad(F-I)|| <= max|dw/ds| |D| < .35.
    For a rectangular source, nonincident original edges remain separated by
    (1-L)*minimum_clearance. Curved source edges, rotation, alternative boundary
    geometries and moving reconstructed neighbours deliberately return unknown.
    """
    if len(entries)!=1:return None
    plan=entry.get('plan')
    if plan is None or r.settings.reconstruction_style=='arc':return None
    source=plan.source
    if source.geom_type!='Polygon' or source.interiors or not source.equals(source.minimum_rotated_rectangle):return None
    chord=shapely.LineString([plan.route.coords[0],plan.route.coords[-1]])
    if plan.route.hausdorff_distance(chord)>r.tol*1e-4:return None
    count=len(r.model.bound.centers);owner=entry['owner'];p=r.p.for_die(owner);mu,b=mean_and_bounds(p,count)
    if b[2] or mu[2]:return None
    part,_=r.interval(entry)
    if part is None:return None
    start=part['a'] if part['a'] else plan.land_end
    end=plan.fixed_start if part['far'].geom_type=='Point' else part['b'];span=end-start
    if span<=r.tol:return None
    net=r.rules.refnet[entry['layer'],entry['index']]
    # Even a distant unsupported return bend must not be silently excluded.
    for other in entries:
        if r.rules.refnet[other['layer'],other['index']]!=net:return None
    if sum(e['source'].wkb==source.wkb for e in entries)!=1:return None
    if r.rules.nets[net]!=[(entry['layer'],entry['index'])]:return None
    vias=r.rules.vias[net]
    for ref,o,required in vias:
        if o==owner:
            if part['near'].geom_type=='Point':
                from .connection_curves import connection_data
                try:effective,*_=connection_data(part,plan,r.model.bound.centers[owner],np.zeros(3));container=effective['C']
                except ValueError:return None
            else:container=part['C']
        elif o<0:
            if part['far'].geom_type=='Point':
                from .connection_curves import connection_data
                try:effective,*_=connection_data(part,plan,r.model.bound.centers[owner],np.zeros(3));container=effective['B']
                except ValueError:return None
            else:container=part['B']
        else:return None
        if not container.buffer(-4*r.tol).covers(required):return None
    old_clearance=float(source.minimum_clearance)
    slope={'linear':1.,'c1':1.5,'c2':1.875}[r.settings.reconstruction_style]
    other=shapely.union_all([r.model.nodes[r.model.ids[ref]]['poly'] for key,refs in r.rules.nets.items() if key[0]==net[0] for ref in refs if ref!=(entry['layer'],entry['index'])])
    distance=source.distance(other) if not other.is_empty else math.inf
    def safe(alpha):
        reach=float(np.linalg.norm(abs(mu[:2])+alpha*b[:2]));lipschitz=slope*reach/span
        # 48 intervals bound the weight interpolation error; rounding is <2 DBU.
        error=4*r.tol+12*reach/(8*48**2)
        if lipschitz>=.35 or (1-lipschitz)*old_clearance<=max(r.rules.width,r.rules.space)+4*error:return False
        if distance<=r.rules.space+reach+2*error:return False
        envelope=source.buffer((reach+error)/math.cos(math.pi/128),quad_segs=32)
        if r.rules.inside is not None and not r.rules.inside.covers(envelope):return False
        if not r.rules.keepout.is_empty and envelope.intersects(r.rules.keepout):return False
        return True
    if not safe(0):return None
    lo,hi=0.,1.
    for _ in range(36):
        cancel_check();middle=(lo+hi)/2
        if safe(middle):lo=middle
        else:hi=middle
    law=PhysicalMass(p,count);mass,error=law(mu-lo*b,mu+lo*b)
    return dict(lower=0.,upper=min(1.,max(0.,1-mass)+error+1e-14),certified=True,safe_fraction=lo,
                reason='平移矩形导体的中心安全盒：双Lipschitz边距、静态邻网及包围量充分界限')


def attach(bank,r):
    weights,error=extreme_weights(bank['grid'],r.p)
    entries=r._plans_for_all_poses(lambda *_:None)
    lookup={(e['layer'],e['index'],e['owner'],e['plan'].identity if e['plan'] else None):e for e in entries}
    result=dict(bank,zones={},scale_nsigma=r.p.scale_nsigma)
    threshold=2*float(ndtr(-r.p.scale_nsigma))
    for key,row in bank['zones'].items():
        cancel_check();current=dict(row);_,estimate=weighted_pair(ProblemIndex(bank['grid'],row['clauses']),weights)
        bound=central_certificate(r,lookup[key],entries) if row['has_failure'] and key in lookup else None
        probability=bound or dict(lower=0.,upper=1.,certified=False,reason='离散极端不能证明连续概率上界，保留未定风险')
        if all(n==1 for n in bank['grid'].radices):probability=dict(lower=float(row['has_failure']),upper=float(row['has_failure']),certified=True,reason='所有偏移自由度均锁零')
        current['probability']=dict(probability,estimate=estimate,threshold=threshold,mass_error=error)
        result['zones'][key]=current
    return result
