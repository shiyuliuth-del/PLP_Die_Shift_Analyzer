"""Pose-dependent cut angles around the two shared source arclength positions.

The finite search never exchanges endpoints or changes the user's connection
style. Every finalist uses the same full-polygon native rule evaluation.
"""
import math
import numpy as np
import shapely
from .connection_curves import connection_data,_side_tangent
from .reconstruction_route import part_key,warp
from .resources import cancel_check


def _angle(v):return math.atan2(v[1],v[0])

def wrap(v):return (v+math.pi)%(2*math.pi)-math.pi


def side_direction(sides,point,fallback):
    """Tangent of the actual reconstructed edge, excluding the artificial cut."""
    best=None
    for side in sides:
        xy=np.asarray(side.coords);a=xy[:-1];v=np.diff(xy,axis=0);length=np.linalg.norm(v,axis=1)
        good=length>1e-12;a=a[good];v=v[good];length=length[good]
        if not len(a):continue
        t=np.clip(np.sum((point-a)*v,axis=1)/length**2,0,1)
        distances=np.linalg.norm(a+t[:,None]*v-point,axis=1);i=int(np.argmin(distances))
        if best is None or distances[i]<best[0]:best=(distances[i],v[i]/length[i])
    if best is None:return fallback
    direction=best[1]
    return direction if direction@fallback>=0 else -direction


def measure(part,plan,center,pose,regions):
    effective,landing,fixed,near,moved,far,t0,t1=connection_data(part,plan,center,pose)
    body=regions['A'].difference(shapely.union_all([landing,fixed]))
    faces=shapely.union_all([shapely.LineString(moved),shapely.LineString(far)]).buffer(plan.tolerance*4)
    sides=[g for g in shapely.get_parts(body.boundary.difference(faces)) if g.geom_type=='LineString']
    sides=sorted(sides,key=lambda g:g.length,reverse=True)[:2]
    width=float(sides[0].distance(sides[1])) if len(sides)==2 else 0.
    # Transverse cut angles measured against BOTH neighbouring boundary sides.
    co,si=np.cos(pose[2]),np.sin(pose[2]);rotation=np.array([[co,-si],[si,co]])
    angles=[]
    for j in range(2):
        direction=far[j]-moved[j];length=np.linalg.norm(direction)
        if length<plan.tolerance:angles.extend((0.,0.));continue
        direction/=length
        a=rotation@_side_tangent(plan.source,near[j],plan.tangent(effective['a']),plan.tolerance)
        b=_side_tangent(plan.source,far[j],plan.tangent(effective['b']),plan.tolerance)
        left=side_direction(sides,moved[j],direction);right=side_direction(sides,far[j],direction)
        face0=moved[1]-moved[0];face0/=np.linalg.norm(face0);face1=far[1]-far[0];face1/=np.linalg.norm(face1)
        angles.extend(math.acos(np.clip(abs(edge@face),0,1)) for edge,face in ((a,face0),(left,face0),(b,face1),(right,face1)))
    # A slanted cut shares its turning burden with BOTH adjacent zones. The
    # smallest of the four corner angles dominates; the average breaks ties.
    face_penalty=sum(abs(v) for v in part.get('angles',(0.,0.)))*.02
    return width,min(angles),float(np.mean(angles))-face_penalty


def solve(r,plan,base,center,pose,quick=False):
    mode=r.settings.reconstruction_boundary_mode;style=r.settings.reconstruction_style
    if mode=='perpendicular':
        regions,error,quality=r.rules.best_regions(plan,base,center,pose,style)
        return base,regions,error,quality
    key=('angle-v40',mode,style,quick,part_key(base),np.asarray(center).tobytes(),np.asarray(pose).tobytes())
    hit=plan.cache.get(key)
    if hit is not None:
        part,regions,error,quality=hit
        return part,None if regions is None else dict(regions),error,quality
    trials=[base]
    try:
        _,_,_,_,near,far,t0,t1=connection_data(base,plan,center,pose)
        heading=_angle(far.mean(axis=0)-near.mean(axis=0))
        goal=np.clip([wrap(heading-_angle(t0)),wrap(heading-_angle(t1))],-math.pi/3,math.pi/3)
        if mode=='balanced':goal*=.5
        angles=[goal,goal*.5,goal*1.5,np.array([goal[0],0.]),np.array([0.,goal[1]])]
        for delta in (() if quick else (-math.pi/12,math.pi/12)):angles.append(np.clip(goal+delta,-math.pi/3,math.pi/3))
        for values in (angles[:1] if quick else angles):
            cancel_check()
            values=np.clip(np.asarray(values,float),-math.pi/3,math.pi/3)
            if base['near'].geom_type=='Point':values=np.array([0.,values[1]])
            if base['far'].geom_type=='Point':values=np.array([values[0],0.])
            part=plan.partition(base['a'],base['b'],values)
            if part is not None and part_key(part) not in {part_key(p) for p in trials}:trials.append(part)
    except ValueError:pass
    ranked=[]
    for part in trials:
        cancel_check();regions,error=warp(part,plan,center,pose,style)
        if error:continue
        try:width,least,mean=measure(part,plan,center,pose,regions)
        except (ValueError,shapely.errors.GEOSException):continue
        rank=(-width,-least,-mean) if mode=='wide' else (-least,-mean,-width)
        ranked.append((rank,part))
    if quick and ranked:
        _,part=min(ranked,key=lambda q:q[0]);regions,error=warp(part,plan,center,pose,style)
        return part,regions,error,None
    # Test the base plus the three best geometric angles against native rules.
    best=None
    finalists=[base,*[part for _,part in sorted(ranked,key=lambda q:q[0])[:3]]]
    seen=set()
    for part in finalists:
        if part_key(part) in seen:continue
        seen.add(part_key(part));cancel_check()
        regions,error,quality=r.rules.best_regions(plan,part,center,pose,style)
        if error:continue
        try:width,least,mean=measure(part,plan,center,pose,regions)
        except (ValueError,shapely.errors.GEOSException):width=least=mean=0.
        score=(*quality,*((-width,-least,-mean) if mode=='wide' else (-least,-mean,-width)))
        if best is None or score<best[0]:best=(score,part,regions,quality)
    if best is None:
        regions,error,quality=r.rules.best_regions(plan,base,center,pose,style)
        answer=(base,regions,error,quality)
    else:answer=(best[1],best[2],None,best[3])
    if len(plan.cache)>512:plan.cache.clear()
    plan.cache[key]=(answer[0],None if answer[1] is None else dict(answer[1]),answer[2],answer[3])
    return answer
