"""Owner-aware viewport queries. Culling never changes analysis or feature IDs."""
import numpy as np
from shapely.geometry import box
from shapely.strtree import STRtree
from .selection import inverse_geometry

class ViewportIndex:
    def __init__(self,cache,case,motions,parameters=None):
        self.bound=cache.bind(case,motions,cache.layers,parameters) if case else None
        self.groups={}
        for key,q in cache.layers.items():
            owners=self.bound.owners[key] if self.bound else np.full(len(q['geometry']),-1)
            self.groups[key]=[(int(owner),ids,STRtree(q['geometry'][ids])) for owner in np.unique(owners) for ids in [np.flatnonzero(owners==owner)]]
    def query(self,key,bounds,states=None):
        region=box(*bounds);found=[]
        for owner,ids,tree in self.groups[key]:
            local=inverse_geometry(region,self.bound.centers[owner],states[owner]) if owner>=0 and states is not None else region
            found.extend(ids[tree.query(local)])
        return np.sort(found).astype(int)

def expanded_bounds(xlim,ylim,margin=.35):
    x0,x1=sorted(xlim);y0,y1=sorted(ylim);dx=(x1-x0)*margin;dy=(y1-y0)*margin
    return (x0-dx,y0-dy,x1+dx,y1+dy)

def overlaps(a,b):return a[0]<=b[2] and a[2]>=b[0] and a[1]<=b[3] and a[3]>=b[1]
