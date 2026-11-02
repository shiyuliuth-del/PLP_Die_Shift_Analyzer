"""Adjacent openings in the full assessment rectangle; rigid landings never shrink."""
from collections import defaultdict
import numpy as np
import shapely
from shapely.strtree import STRtree


def occupied(ends,pairs,walls,geometries,half_depth,tol):
    """Project the copper inside each finite rectangle, not just its centreline."""
    result=defaultdict(list)
    if not len(ends):return result
    delta=ends[:,1]-ends[:,0]
    length=np.linalg.norm(delta,axis=1)
    d=delta/np.maximum(length,tol)[:,None]
    side=np.column_stack((-d[:,1],d[:,0]))*half_depth
    rects=shapely.polygons(np.stack((ends[:,0]-side,ends[:,1]-side,ends[:,1]+side,ends[:,0]+side),axis=1))
    ci,wi=STRtree(geometries).query(rects,predicate='intersects')
    keep=(wi!=pairs[ci,0]) & (wi!=pairs[ci,1]) & (length[ci]>tol)
    ci,wi=ci[keep],wi[keep]
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


def local_openings(gate,ends,spans,nominal_spans,walls,tol):
    """Keep each nominal strand between its original neighbours, in order.

    For an arriving pad, use its own nominal projection to find the side of
    each original strand. A strand carrying this pad occupies the rigid wall.
    """
    i,j=gate.pair;g0=gate.nominal_gap
    axis=(gate.ends[1]-gate.ends[0])/g0
    gap=np.linalg.norm(ends[1]-ends[0])
    direction=(ends[1]-ends[0])/gap if gap>tol else axis
    nodes=[]
    for w,l,r in sorted(spans,key=lambda item:(item[1],-item[2],item[0])):
        originals=[(a,b) for ref,a,b in nominal_spans if ref==w]
        if originals:
            l0,r0=min(originals,key=lambda s:abs((s[0]+s[1])*.5-(l+r)*.5))
        else:
            x=(shapely.get_coordinates(walls[w].geometry)-gate.ends[0])@axis
            l0,r0=float(x.min()),float(x.max())
        nodes.append((w,l,r,l0,r0))
    # Overlap forms a continuous solid. Keep its two actual boundary owners.
    blocks=[]
    for n in nodes:
        if blocks and n[1]<=blocks[-1][1][2]+tol:
            if n[2]>blocks[-1][1][2]:blocks[-1][1]=n
        else:blocks.append([n,n])
    a=(i,0.,0.,0.,0.);b=(j,gap,gap,g0,g0)
    intervals=list(zip([a,*[v[1] for v in blocks]],[*[v[0] for v in blocks],b]))
    lanes=[[] for _ in intervals];closed=set();locked=set()
    for n in nodes:
        # Local connectivity matters. A remote pad on the same net cannot
        # lock another parallel pass of that net.
        candidates=[i for i,(t,refs) in enumerate(zip(gate.positions,gate.strand_refs))
                    if walls[n[0]].contacts.intersection(refs) and n[3]-gate.widths[i]/2-tol<=t<=n[4]+gate.widths[i]/2+tol]
        if candidates:locked.add(min(candidates,key=lambda i:abs(gate.positions[i]-(n[3]+n[4])/2)))
    for index,t in enumerate(gate.positions):
        if index in locked:continue
        centres=[(n[3]+n[4])/2 for block in blocks for n in block[:1]]
        slot=sum(t>center for center in centres)
        lanes[slot].append(index)
        # If the two original sides exchanged places, the original assignment
        # cannot use the apparent gap on the opposite side.
        if any(t<center for center in centres[:slot]) or any(t>center for center in centres[slot:]):
            closed.add(slot)
    for k,(left,right) in enumerate(intervals):
        if not lanes[k]:continue
        l,r=left[2],right[1]
        known={ref for ref,_,_ in nominal_spans}|{i,j}
        original=(max(0.,right[3]-left[4]) if left[0] in known and right[0] in known else
                  walls[left[0]].geometry.distance(walls[right[0]].geometry))
        yield dict(pair=(left[0],right[0]),indices=lanes[k],nominal_gap=original,
                   gap=0. if k in closed else max(0.,r-l),point=ends[0]+direction*((l+r)/2),
                   locked=len(locked),split=True)
