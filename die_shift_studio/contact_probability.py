"""Bounded Gaussian integration for foreign-net contact (independent of display pixels).

Every state box has an exact independent-normal mass and a Hausdorff motion
bound. Entirely safe / contacting boxes contribute to certified probability
bounds; unresolved boxes and omitted Gaussian tails remain in the upper bound.
No zero-hit sample is treated as proof of a safe rare event.
"""
import heapq
import math
import numpy as np
import shapely
from scipy.special import ndtr
from scipy.stats import norm
from shapely.geometry import Polygon
from shapely.strtree import STRtree
from shapely.ops import nearest_points
from .statistics import statistical_state,mean_and_bounds
from .distribution import normalizer
from .preview import rigid_transform


def interval_mass(lo,hi):
    lo=np.asarray(lo);hi=np.asarray(hi)
    return np.maximum(0,np.where(lo>=0,ndtr(-lo)-ndtr(-hi),ndtr(hi)-ndtr(lo)))


def contact_probability(model,record,p,budget=2048):
    p=p.for_die(record['owner']);count=len(model.bound.centers);mu,cov,_=statistical_state(p,count)
    sx,sy,st=np.sqrt(np.diag(cov));rho=p.rho_xy
    root=np.array([[sx,0.,0.],[rho*sy,sy*math.sqrt(max(0.,1-rho*rho)),0.],[0.,0.,st]])
    active=np.flatnonzero(np.any(root!=0,axis=0));root=root[:,active]
    alpha=float(2*norm.sf(p.target_nsigma));normalization,normalization_error=normalizer(p,count);_,hard_bounds=mean_and_bounds(p,count)
    radius=p.annulus_um+p.line_space_um
    tol=max(1e-9,model.data.dbu_um*1e-6)
    center=model.bound.centers[record['owner']];vertices=np.asarray(record['poly'].exterior.coords)
    lever=float(np.linalg.norm(vertices-center,axis=1).max())
    foreign=np.asarray(record['foreign'],int)
    if not len(foreign):return dict(lower=0.,upper=0.,estimate=0.,verdict='通过',boxes=0,threshold=alpha,witness=None)
    others=model.node_geometry[foreign]
    mean_via=Polygon(rigid_transform(vertices,center,mu))
    reach=float(np.linalg.norm(hard_bounds[:2])+2*lever*math.sin(min(math.pi,hard_bounds[2])/2))
    keep=shapely.distance(mean_via,others)<=radius+reach+tol
    foreign=foreign[keep];others=others[keep]
    if not len(foreign):return dict(lower=0.,upper=0.,estimate=0.,verdict='通过',boxes=0,threshold=alpha,witness=None)
    from .probability_overlay import input_envelope
    envelope=input_envelope(record['poly'],center,p,count)
    keep=shapely.distance(envelope,others)<=radius+tol
    foreign=foreign[keep];others=others[keep]
    if not len(foreign):return dict(lower=0.,upper=0.,estimate=0.,verdict='通过',boxes=0,threshold=alpha,witness=None)
    tree=STRtree(others)
    def distance(state):
        via=Polygon(rigid_transform(vertices,center,state));index=int(tree.nearest(via))
        return float(via.distance(others[index])),via,int(foreign[index])
    if not len(active):
        d,via,j=distance(mu);value=float(d<=radius+tol)
        return dict(lower=value,upper=value,estimate=value,verdict='窗口不足' if value else '通过',boxes=1,threshold=alpha,
                    witness=dict(node=j,point=list(nearest_points(via,model.nodes[j]['poly'])[1].coords[0]),state=mu.tolist()) if value else None)
    # Reserve at most one eighth of the acceptance budget for all omitted tails.
    tail_z=float(norm.isf(alpha/(16*len(active))))
    lo=np.full(len(active),-tail_z);hi=-lo
    # Union bound avoids cancellation in 1 - (1 - tiny)^dimension.
    tail=min(1.,float(2*len(active)*norm.sf(tail_z)))
    queue=[];lower=0.;unknown=0.;counter=0;witness=None;boxes=0
    def push_many(candidates):
        nonlocal lower,unknown,counter,witness,boxes
        lows=np.asarray([q[0] for q in candidates]);highs=np.asarray([q[1] for q in candidates])
        masses=np.prod(interval_mass(lows,highs),axis=1)
        mid=(lows+highs)/2;half=(highs-lows)/2;states=mu+mid@root.T
        spans=half@np.abs(root).T;low=states-spans;high=states+spans
        valid=(masses>0)&np.all(high>=mu-hard_bounds,axis=1)&np.all(low<=mu+hard_bounds,axis=1)
        contained=np.all(low>=mu-hard_bounds-1e-13,axis=1)&np.all(high<=mu+hard_bounds+1e-13,axis=1)
        ids=np.flatnonzero(valid)
        if not len(ids):return
        low=np.maximum(low[ids],mu-hard_bounds);high=np.minimum(high[ids],mu+hard_bounds)
        states=(low+high)/2;physical_half=(high-low)/2
        delta=np.linalg.norm(physical_half[:,:2],axis=1)+2*lever*np.sin(np.minimum(math.pi,physical_half[:,2])/2)
        local=vertices-center;co=np.cos(states[:,2,None]);si=np.sin(states[:,2,None])
        points=np.stack((co*local[:,0]-si*local[:,1],si*local[:,0]+co*local[:,1]),axis=-1)+center+states[:,None,:2]
        vias=shapely.polygons(points)
        pairs,distances=tree.query_nearest(vias,all_matches=False,return_distance=True)
        nearest=np.empty(len(ids),int);nearest[pairs[0]]=pairs[1]
        ds=np.empty(len(ids));ds[pairs[0]]=distances;boxes+=len(ids)
        for at,i in enumerate(ids):
            mass=float(masses[i]);d=float(ds[at]);span=float(delta[at])
            if d>radius+span+tol:continue
            if contained[i] and d+span<=radius+tol:
                lower+=mass
                if witness is None:
                    j=int(foreign[nearest[at]])
                    witness=dict(node=j,point=list(nearest_points(vias[at],model.nodes[j]['poly'])[1].coords[0]),state=states[at].tolist())
                continue
            score=(np.linalg.norm(root[:2],axis=0)+lever*np.abs(root[2]))*(highs[i]-lows[i])
            axis=int(np.argmax(score));counter+=1;unknown+=mass
            heapq.heappush(queue,(-mass,counter,lows[i],highs[i],axis))
    push_many([(lo,hi)])
    while queue and boxes+2<=budget and lower*(1-1e-12)/(normalization+normalization_error)<=alpha and (lower+unknown+tail)*(1+1e-12)/(normalization-normalization_error)>alpha:
        candidates=[]
        for _ in range(min(16,len(queue),(budget-boxes)//2)):
            negative,_,lo,hi,axis=heapq.heappop(queue);unknown=max(0.,unknown+negative)
            mid=(lo[axis]+hi[axis])/2;a=hi.copy();a[axis]=mid;b=lo.copy();b[axis]=mid
            candidates.extend([(lo,a),(b,hi)])
        push_many(candidates)
        # Re-sum the remaining masses: subtracting ~1 then comparing with 12σ
        # (~1e-33) would otherwise lose the very tail being evaluated.
        unknown=math.fsum(-item[0] for item in queue)
    upper=min(1.,(lower+unknown+tail)*(1+1e-12)/(normalization-normalization_error))
    lower=max(0.,lower*(1-1e-12)/(normalization+normalization_error))
    # Bounds are certificates of threshold decisions, not a converged estimate.
    verdict='窗口不足' if lower>alpha else '通过' if upper<=alpha else '概率待细化'
    return dict(lower=float(lower),upper=float(upper),estimate=None,threshold=alpha,verdict=verdict,boxes=boxes,
                witness=witness,tail_bound=tail/normalization,normalization=normalization,method='自适应正态状态盒积分（概率上下界）')
