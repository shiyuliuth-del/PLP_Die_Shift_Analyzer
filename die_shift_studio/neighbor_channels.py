"""Adjacent openings in the full assessment rectangle; rigid landings never shrink."""
from collections import defaultdict
import numpy as np
import shapely
from shapely.strtree import STRtree


def occupied(ends,pairs,walls,geometries,half_depth,tol,source_refs=None):
    """Project the copper inside each finite rectangle, not just its centreline."""
    result=defaultdict(list)
    if not len(ends):return result
    delta=ends[:,1]-ends[:,0]
    length=np.linalg.norm(delta,axis=1)
    d=delta/np.maximum(length,tol)[:,None]
    side=np.column_stack((-d[:,1],d[:,0]))*half_depth
    rects=shapely.polygons(np.stack((ends[:,0]-side,ends[:,1]-side,ends[:,1]+side,ends[:,0]+side),axis=1))
    ci,wi=STRtree(geometries).query(rects)
    # Reject boxes outside either axis of the oriented gate before polygon
    # overlay. A long diagonal gate's axis-aligned envelope is very loose.
    bounds=shapely.bounds(geometries)
    center=(bounds[:,2:]+bounds[:,:2])/2;extent=(bounds[:,2:]-bounds[:,:2])/2
    offset=center[wi]-ends.mean(axis=1)[ci]
    t=np.column_stack((-d[ci,1],d[ci,0]))
    along=abs(np.einsum('ij,ij->i',offset,d[ci]))
    across=abs(np.einsum('ij,ij->i',offset,t))
    keep=(along<=length[ci]/2+np.einsum('ij,ij->i',extent[wi],abs(d[ci]))+tol)&(across<=half_depth+np.einsum('ij,ij->i',extent[wi],abs(t))+tol)
    ci,wi=ci[keep],wi[keep]
    keep=(wi!=pairs[ci,0]) & (wi!=pairs[ci,1]) & (length[ci]>tol)
    ci,wi=ci[keep],wi[keep]
    if not len(ci):return result
    if source_refs is not None:
        # An unrelated third wall closes this parent rectangle. Exact
        # intersection plus strict interior projection proves this without
        # computing polygon cuts. Keep all cuts when a local pad can lock a
        # strand, or when contact at the endpoint is numerically ambiguous.
        potential=np.fromiter((not walls[w].contacts.isdisjoint(source_refs[k]) for k,w in zip(ci,wi)),bool)
        protect=np.zeros(len(ends),bool);protect[ci[potential]]=True
        mid=np.einsum('ij,ij->i',center[wi]-ends[ci,0],d[ci])
        spread=np.einsum('ij,ij->i',extent[wi],abs(d[ci]))
        interior=(~protect[ci])&(mid-spread>tol)&(mid+spread<length[ci]-tol)
        v=np.flatnonzero(interior)
        touch=shapely.intersects(geometries[wi[v]],rects[ci[v]])
        ko=np.array([walls[w].kind=='禁布区' for w in wi[v]],bool)
        touch[ko] &= shapely.relate_pattern(geometries[wi[v[ko]]],rects[ci[v[ko]]],'T********')
        v=v[touch]
        blocked=np.zeros(len(ends),bool)
        for k,w in zip(ci[v],wi[v]):
            if not blocked[k]:
                blocked[k]=True;result[int(k)].append((int(w),0.,0.))
        keep=~blocked[ci];ci,wi=ci[keep],wi[keep]
        if not len(ci):return result
    hit=shapely.intersects(geometries[wi],rects[ci])
    ko=np.array([walls[w].kind=='禁布区' for w in wi],bool)
    hit[ko] &= shapely.relate_pattern(geometries[wi[ko]],rects[ci[ko]],'T********')
    ci,wi=ci[hit],wi[hit]
    if not len(ci):return result
    clips=shapely.intersection(rects[ci],geometries[wi])
    # Disconnected parts of a concave island leave independent openings.
    pieces,source=shapely.get_parts(clips,return_index=True)
    coords,ids=shapely.get_coordinates(pieces,return_index=True)
    if not len(ids):return result
    x=np.einsum('ij,ij->i',coords-ends[ci[source[ids]],0],d[ci[source[ids]]])
    lo=np.full(len(pieces),np.inf);hi=np.full(len(pieces),-np.inf)
    np.minimum.at(lo,ids,x);np.maximum.at(hi,ids,x)
    inside=(hi>tol)&(lo<length[ci[source]]-tol)
    rigid=np.array([walls[w].kind.endswith('孔盘') for w in wi[source]],bool)
    # A rigid via landing whose footprint enters the rectangle reserves its
    # entire transverse dimension. A partial circular chord is not a narrower pad.
    for chunk in np.array_split(np.flatnonzero(inside & rigid),max(1,int(np.ceil(np.count_nonzero(inside & rigid)/256)))):
        if not len(chunk):continue
        k=ci[source[chunk]]
        xy,at=shapely.get_coordinates(geometries[wi[source[chunk]]],return_index=True)
        dist=np.einsum('ij,ij->i',xy-ends[k[at],0],d[k[at]])
        left,right=np.full(len(chunk),np.inf),np.full(len(chunk),-np.inf)
        np.minimum.at(left,at,dist);np.maximum.at(right,at,dist)
        lo[chunk],hi[chunk]=left,right
    for p in np.flatnonzero(inside):
        k=int(ci[source[p]]);w=int(wi[source[p]])
        for pad in walls[w].pads:
            if pad.intersects(rects[k]):
                projected=(shapely.get_coordinates(pad)-ends[k,0])@d[k]
                lo[p]=min(lo[p],projected.min());hi[p]=max(hi[p],projected.max())
        result[k].append((w,max(0.,float(lo[p])),min(float(length[k]),float(hi[p]))))
    return result


def occupied_batches(ends,pairs,walls,geometries,half_depth,tol,source_refs=None):
    """Parallel GEOS work for independent gates; all indices remain chart-local."""
    if len(ends)<1200:return occupied(ends,pairs,walls,geometries,half_depth,tol,source_refs)
    from .resources import resource_profile,parallel_map,geometry_budget
    profile=resource_profile();n=min(profile['workers'],max(1,len(ends)//450))
    if n<2:return occupied(ends,pairs,walls,geometries,half_depth,tol,source_refs)
    # Keep GEOS objects private including interior indices made by predicates.
    from dataclasses import replace
    packed=shapely.to_wkb(geometries)
    pads=[shapely.to_wkb(w.pads) if w.pads else () for w in walls]
    n=min(n,geometry_budget(profile,sum(map(len,packed))+sum(sum(map(len,row)) for row in pads)))
    if n<2:return occupied(ends,pairs,walls,geometries,half_depth,tol,source_refs)
    profile=dict(profile,workers=n)
    def solve(chunk):
        start,stop=chunk
        local=shapely.from_wkb(packed)
        local_walls=[replace(w,pads=shapely.from_wkb(p)) if len(p) else w for w,p in zip(walls,pads)]
        result=occupied(ends[start:stop],pairs[start:stop],local_walls,local,half_depth,tol,
                        None if source_refs is None else source_refs[start:stop])
        return {start+k:v for k,v in result.items()}
    stride=(len(ends)+n-1)//n
    batches=parallel_map(solve,[(k,min(k+stride,len(ends))) for k in range(0,len(ends),stride)],profile)
    result={}
    for batch in batches:result.update(batch)
    return result


def local_openings(gate,ends,spans,nominal_spans,walls,tol,evidence_only=False,projections=None):
    """Keep each nominal strand between its original neighbours, in order.

    For an arriving pad, use its own nominal projection to find the side of
    each original strand. A strand carrying this pad occupies the rigid wall.
    """
    i,j=gate.pair;g0=gate.nominal_gap
    axis=(gate.ends[1]-gate.ends[0])/g0
    gap=np.linalg.norm(ends[1]-ends[0])
    direction=(ends[1]-ends[0])/gap if gap>tol else axis
    nodes=[];locked=set()
    projections={} if projections is None else projections
    for w,l,r in spans:
        options=projections.get(w)
        if options is None:
            originals=[(a,b) for ref,a,b in nominal_spans if ref==w]
            if not originals:
                x=(shapely.get_coordinates(walls[w].geometry)-gate.ends[0])@axis
                originals=[(float(x.min()),float(x.max()))]
            options=[]
            for a,b in originals:
                candidates=[k for k,(t,refs) in enumerate(zip(gate.positions,gate.strand_refs))
                            if not walls[w].contacts.isdisjoint(refs) and a-gate.widths[k]/2-tol<=t<=b+gate.widths[k]/2+tol]
                strand=min(candidates,key=lambda k:abs(gate.positions[k]-(a+b)/2)) if candidates else None
                options.append((a,b,strand))
            if len(projections)<64:projections[w]=options
        a,b,strand=options[0] if len(options)==1 else min(options,key=lambda v:abs((v[0]+v[1])*.5-(l+r)*.5))
        if strand is not None:locked.add(strand)
        nodes.append((w,l,r,a,b))
    if evidence_only and not locked:return
    nodes.sort(key=lambda n:(n[1],-n[2],n[0]))
    # Overlap forms a continuous solid. Keep its two actual boundary owners.
    blocks=[]
    for n in nodes:
        if blocks and n[1]<=blocks[-1][1][2]+tol:
            if n[2]>blocks[-1][1][2]:blocks[-1][1]=n
        else:blocks.append([n,n])
    a=(i,0.,0.,0.,0.);b=(j,gap,gap,g0,g0)
    intervals=list(zip([a,*[v[1] for v in blocks]],[*[v[0] for v in blocks],b]))
    lanes=[[] for _ in intervals];closed=set()
    centres=[(left[3]+left[4])/2 for left,_ in blocks]
    for index,t in enumerate(gate.positions):
        if index in locked:continue
        slot=sum(t>center for center in centres)
        lanes[slot].append(index)
        # If the two original sides exchanged places, the original assignment
        # cannot use the apparent gap on the opposite side.
        if any(t<center for center in centres[:slot]) or any(t>center for center in centres[slot:]):
            closed.add(slot)
    for k,(left,right) in enumerate(intervals):
        if not lanes[k]:continue
        l,r=left[2],right[1]
        if evidence_only:
            yield dict(pair=(left[0],right[0]),indices=lanes[k],point=ends[0]+direction*((l+r)/2),locked=len(locked))
            continue
        known={ref for ref,_,_ in nominal_spans}|{i,j}
        original=(max(0.,right[3]-left[4]) if left[0] in known and right[0] in known else
                  walls[left[0]].geometry.distance(walls[right[0]].geometry))
        yield dict(pair=(left[0],right[0]),indices=lanes[k],nominal_gap=original,
                   gap=0. if k in closed else max(0.,r-l),point=ends[0]+direction*((l+r)/2),
                   locked=len(locked),split=True)
