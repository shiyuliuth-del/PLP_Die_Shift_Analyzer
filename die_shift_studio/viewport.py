"""Owner-aware viewport queries. Culling never changes analysis or feature IDs."""
import numpy as np
from shapely.geometry import box
from shapely.strtree import STRtree
from .selection import inverse_geometry

class ViewportIndex:
    def __init__(self,cache,case,motions,parameters=None):
        self.bound=cache.bind(case,motions,cache.layers,parameters) if case else None
        self.groups={};self.last_query={}
        for key,q in cache.layers.items():
            owners=self.bound.owners[key] if self.bound else np.full(len(q['geometry']),-1)
            self.groups[key]=[(int(owner),ids,STRtree(q['geometry'][ids])) for owner in np.unique(owners) for ids in [np.flatnonzero(owners==owner)]]
    def query(self,key,bounds,states=None):
        groups=self.groups[key]
        pose=None if states is None else tuple(tuple(states[o]) for o,_,_ in groups if o>=0)
        x0,y0,x1,y1=bounds;dx=x1-x0;dy=y1-y0
        saved=self.last_query.get(key)
        if saved is not None and saved[0]==pose:
            a,b,c,d=saved[1]
            if a<=x0<=x1<=c and b<=y0<=y1<=d and c-a<=2*dx and d-b<=2*dy:return saved[2]
        # A small guard outside the already padded viewport lets a pan reuse
        # native paths. Zooming in far enough or moving an owner re-queries.
        guard=(x0-.15*dx,y0-.15*dy,x1+.15*dx,y1+.15*dy);region=box(*guard);found=[]
        for owner,ids,tree in groups:
            local=inverse_geometry(region,self.bound.centers[owner],states[owner]) if owner>=0 and states is not None else region
            found.extend(ids[tree.query(local)])
        selected=np.sort(found).astype(int);self.last_query[key]=(pose,guard,selected)
        return selected

def expanded_bounds(xlim,ylim,margin=.35):
    x0,x1=sorted(xlim);y0,y1=sorted(ylim);dx=(x1-x0)*margin;dy=(y1-y0)*margin
    return (x0-dx,y0-dy,x1+dx,y1+dy)

def overlaps(a,b):return a[0]<=b[2] and a[2]>=b[0] and a[1]<=b[3] and a[3]>=b[1]
