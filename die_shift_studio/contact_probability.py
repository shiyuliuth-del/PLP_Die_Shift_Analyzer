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
from shapely.strtree import STRtree
from shapely.ops import nearest_points
from .statistics import statistical_state,mean_and_bounds
from .distribution import normalizer
from .preview import rigid_transform


def interval_mass(lo,hi):
    lo=np.asarray(lo);hi=np.asarray(hi)
    return np.maximum(0,np.where(lo>=0,ndtr(-lo)-ndtr(-hi),ndtr(hi)-ndtr(lo)))


def reachable_targets(model,record,params):
    from .contact import nearby_foreign
    from .probability_overlay import input_envelope
    if not len(record['foreign']):return np.empty(0,int)
    owner=record['owner'];p=params.for_die(owner);count=len(model.bound.centers)
    center=model.bound.centers[owner];mu,bounds=mean_and_bounds(p,count)
    shape=record['poly'];xy=shapely.get_coordinates(shape)
    mean_via=shapely.set_coordinates(shape,rigid_transform(xy,center,mu))
    reach=np.linalg.norm(bounds[:2])+2*np.linalg.norm(xy-center,axis=1).max()*math.sin(min(math.pi,bounds[2])/2)
    radius=p.annulus_um+p.line_space_um+max(1e-9,model.data.dbu_um*1e-6)
    ids=nearby_foreign(model,record,mean_via,radius+reach)
    if len(ids):
        env=input_envelope(shape,center,p,count)
        ids=ids[shapely.dwithin(env,model.node_geometry[ids],radius)]
    return ids


def local_obstacles(model,record,params,ids):
    # For every allowed pose all copper that can be contacted lies inside
    # this rectangle. Clip long foreign nets once for both union and pair tests.
    others=model.node_geometry[ids]
    heavy=np.flatnonzero(shapely.get_num_coordinates(others)>96)
    if not len(heavy):return others
    owner=record['owner'];center=model.bound.centers[owner]
    p=params.for_die(owner);mu,b=mean_and_bounds(p,len(model.bound.centers))
    xy=shapely.get_coordinates(record['poly']);at_mean=rigid_transform(xy,center,mu)
    lever=np.linalg.norm(xy-center,axis=1).max()
    padding=b[:2]+2*lever*math.sin(min(math.pi,b[2])/2)+p.annulus_um+p.line_space_um+2*max(1e-9,model.data.dbu_um*1e-6)
    lo=at_mean.min(axis=0)-padding;hi=at_mean.max(axis=0)+padding
    domain=shapely.box(*lo,*hi)
    clipped=shapely.intersection(others[heavy],domain)
    local=others.copy()
    for i,g in zip(heavy,clipped):
        if not g.is_empty:local[i]=g
    return local


def location_witness(model,record,params,node):
    """Find an attained interior contact; an unsuccessful probe is never a pass."""
    owner=record['owner'];p=params.for_die(owner);center=model.bound.centers[owner]
    mu,b=mean_and_bounds(p,len(model.bound.centers));shape=record['poly'];target=model.node_geometry[node]
    radius=p.annulus_um+p.line_space_um;xy=shapely.get_coordinates(shape)
    for theta in mu[2]+np.unique(np.array([0.,-1.,1.,-.5,.5])*b[2]):
        state=np.array([*mu[:2],theta])
        via=shapely.set_coordinates(shape,rigid_transform(xy,center,state))
        a,t=nearest_points(via,target);delta=np.array(t.coords[0])-a.coords[0]
        if abs(p.rho_xy)==1 and b[0]*b[1]>0:
            direction=b[:2]*[1,p.rho_xy]
            nearest=np.clip(delta@direction/(direction@direction),-1,1)
            shifts=direction*np.array([nearest,-1.,1.,0.])[:,None]
        else:
            shifts=np.array([np.clip(delta,-b[:2],b[:2]),[0.,0.],
                             *([x*b[0],y*b[1]] for x in (-1,0,1) for y in (-1,0,1))])
        poses=state+np.column_stack((shifts,np.zeros(len(shifts))))
        candidates=shapely.set_coordinates(np.full(len(shifts),via,object),
                    (shapely.get_coordinates(via)[None]+shifts[:,None,:]).reshape(-1,2))
        hits=np.flatnonzero(shapely.distance(candidates,target)<radius)
        if hits.size:
            i=hits[0];point=nearest_points(candidates[i],target)[1]
            return dict(node=int(node),state=poses[i].tolist(),point=list(point.coords[0]))
    return None


def contact_probability(model,record,p,budget=2048,candidates=None,obstacles=None):
    model.configure_parameters(p)
    foreign=reachable_targets(model,record,p) if candidates is None else np.asarray(candidates,int)
    if not len(foreign):return dict(lower=0.,upper=0.,estimate=0.,verdict='通过',boxes=0,threshold=p.failure_threshold,witness=None)
    others=local_obstacles(model,record,p,foreign) if obstacles is None else obstacles
    p=p.for_die(record['owner']);count=len(model.bound.centers);mu,cov,_=statistical_state(p,count)
    sx,sy,st=np.sqrt(np.diag(cov));rho=p.rho_xy
    root=np.array([[sx,0.,0.],[rho*sy,sy*math.sqrt(max(0.,1-rho*rho)),0.],[0.,0.,st]])
    active=np.flatnonzero(np.any(root!=0,axis=0));root=root[:,active]
    alpha=p.failure_threshold;normalization,normalization_error=normalizer(p,count);_,hard_bounds=mean_and_bounds(p,count)
    radius=p.annulus_um+p.line_space_um
    tol=max(1e-9,model.data.dbu_um*1e-6)
    center=model.bound.centers[record['owner']];shape=record['poly'];vertices=shapely.get_coordinates(shape)
    lever=float(np.linalg.norm(vertices-center,axis=1).max())
    tree=STRtree(others)
    def distance(state):
        via=shapely.set_coordinates(shape,rigid_transform(vertices,center,state));index=int(tree.nearest(via))
        return float(via.distance(others[index])),via,int(foreign[index])
    if not len(active):
        d,via,j=distance(mu);value=float(d<=radius+tol)
        return dict(lower=value,upper=value,estimate=value,verdict='窗口不足' if value else '通过',boxes=1,threshold=alpha,
                    witness=dict(node=j,point=list(nearest_points(via,model.nodes[j]['poly'])[1].coords[0]),state=mu.tolist()) if value else None)
    # Reserve at most one eighth of the acceptance budget for all omitted tails.
    if alpha==0:
        # Every active latent coordinate has a finite bound from the hard box.
        # root has full column rank even for zero axes or |rho|=1.
        hi=np.abs(np.linalg.pinv(root))@hard_bounds;lo=-hi;tail=0.
    else:
        tail_z=float(norm.isf(alpha/(16*len(active))))
        lo=np.full(len(active),-tail_z);hi=-lo
        # Union bound avoids cancellation at very small acceptance budgets.
        tail=min(1.,float(2*len(active)*norm.sf(tail_z)))
    queue=[];lower=0.;unknown=0.;counter=0;witness=None;boxes=0;positive_contact=False
    def push_many(candidates):
        nonlocal lower,unknown,counter,witness,boxes,positive_contact
        lows=np.asarray([q[0] for q in candidates]);highs=np.asarray([q[1] for q in candidates])
        masses=np.prod(interval_mass(lows,highs),axis=1)
        mid=(lows+highs)/2;half=(highs-lows)/2;states=mu+mid@root.T
        spans=half@np.abs(root).T;low=states-spans;high=states+spans
        valid=((masses>0) if alpha else np.all(highs>lows,axis=1))&np.all(high>=mu-hard_bounds,axis=1)&np.all(low<=mu+hard_bounds,axis=1)
        contained=np.all(low>=mu-hard_bounds-1e-13,axis=1)&np.all(high<=mu+hard_bounds+1e-13,axis=1)
        ids=np.flatnonzero(valid)
        if not len(ids):return
        low=np.maximum(low[ids],mu-hard_bounds);high=np.minimum(high[ids],mu+hard_bounds)
        states=(low+high)/2;physical_half=(high-low)/2
        delta=np.linalg.norm(physical_half[:,:2],axis=1)+2*lever*np.sin(np.minimum(math.pi,physical_half[:,2])/2)
        local=vertices-center;co=np.cos(states[:,2,None]);si=np.sin(states[:,2,None])
        points=np.stack((co*local[:,0]-si*local[:,1],si*local[:,0]+co*local[:,1]),axis=-1)+center+states[:,None,:2]
        vias=shapely.set_coordinates(np.full(len(points),shape,object),points.reshape(-1,2)) if shape.interiors else shapely.polygons(points)
        if len(others)==1:
            nearest=np.zeros(len(ids),int);ds=shapely.distance(vias,others[0])
        else:
            pairs,distances=tree.query_nearest(vias,all_matches=False,return_distance=True)
            nearest=np.empty(len(ids),int);nearest[pairs[0]]=pairs[1]
            ds=np.empty(len(ids));ds[pairs[0]]=distances
        boxes+=len(ids)
        for at,i in enumerate(ids):
            mass=float(masses[i]);d=float(ds[at]);span=float(delta[at])
            if d>radius+span+tol:continue
            if contained[i] and d+span<=radius+tol:
                lower+=mass;positive_contact=True
                if witness is None:
                    j=int(foreign[nearest[at]])
                    witness=dict(node=j,point=list(nearest_points(vias[at],model.nodes[j]['poly'])[1].coords[0]),state=states[at].tolist())
                continue
            score=(np.linalg.norm(root[:2],axis=0)+lever*np.abs(root[2]))*(highs[i]-lows[i])
            axis=int(np.argmax(score));counter+=1;unknown+=mass
            heapq.heappush(queue,(-mass,counter,lows[i],highs[i],axis))
    push_many([(lo,hi)])
    while queue and boxes+2<=budget and ((not positive_contact) if alpha==0 else (lower*(1-1e-12)/(normalization+normalization_error)<=alpha and (lower+unknown+tail)*(1+1e-12)/(normalization-normalization_error)>alpha)):
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
    if alpha==0:
        # A positive-volume latent box has positive Gaussian mass even when
        # floating point CDF products underflow. Never turn that into a pass.
        verdict='窗口不足' if positive_contact else '概率待细化' if queue else '通过'
        if (positive_contact or queue) and upper==0:upper=1.  # no numerical certificate for its magnitude
    else:verdict='窗口不足' if lower>alpha else '通过' if upper<=alpha else '概率待细化'
    return dict(lower=float(lower),upper=float(upper),estimate=None,threshold=alpha,verdict=verdict,boxes=boxes,
                witness=witness,positive_probability_proven=positive_contact,tail_bound=tail/normalization,normalization=normalization,method='自适应正态状态盒积分（概率上下界）')
