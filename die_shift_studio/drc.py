"""The same parallel-edge rules serve global display and candidate acceptance.

KLayout supplies unshielded edge pairs, not a raster approximation. Reported edge portions retain common contour vertices for adjacent pairs.
Their shared endpoints are excluded before producing a marker. A 5 degree
angle limit is independent of the minimum allowed copper corner angle.
"""
from collections import OrderedDict
import math
import numpy as np
import shapely
from shapely.strtree import STRtree

PARALLEL_DEGREES=5.

from .native_backend import compute,geometry as native_operation

def rule_region(geometry,distance,dbu,kind='width'):
    return native_operation(kind,geometry,dbu,distance)

def rule_area(geometry,distance,dbu,kind='width'):
    return compute(kind,geometry.wkb,float(dbu),float(distance))[1]

def local_foreign(a,b,distance,dbu):
    """Remote edges cannot contribute to an inter-net distance violation.

    Clip beyond the rule distance plus two DBU; introduced clipping edges are
    then outside the search threshold. Actual overlap is retained exactly.
    """
    if shapely.get_num_coordinates(b)<512:return b
    x0,y0,x1,y1=a.bounds;pad=distance+max(2*dbu,1e-7)
    return b.intersection(shapely.box(x0-pad,y0-pad,x1+pad,y1+pad))

def separation_area(a,b,distance,dbu):
    b=local_foreign(a,b,distance,dbu)
    overlap=a.intersection(b)
    return (max(dbu*dbu,overlap.area) if not overlap.is_empty else 0.)+compute('separation',a.wkb,float(dbu),float(distance),b.wkb)[1]

def separation_region(a,b,distance,dbu):
    b=local_foreign(a,b,distance,dbu)
    overlap=a.intersection(b)
    if not overlap.is_empty and overlap.area==0:overlap=overlap.buffer(dbu)
    return shapely.union_all([overlap,native_operation('separation',a,dbu,distance,b)])

class GlobalDRC:
    """Full layout DRC with bounded, per-copper immutable geometry caching.

    Static layers and same-owner rigid layers are checked once in local space;
    only cross-owner spacing and deformed copper require new edge checks.
    """
    def __init__(self,model,parameters):
        self.model=model;self.p=parameters;self.cache=OrderedDict();self.hits=0;self.misses=0
        self.capacity=max(512,min(8192,len(model.nodes)*3));self.pairs={}
        import threading
        self.lock=threading.RLock()
    def cached(self,geometry,width,space,angle):
        key=(geometry.wkb,width,space,angle)
        with self.lock:
            if key in self.cache:self.hits+=1;self.cache.move_to_end(key);return self.cache[key]
        from .geometry_rules import angle_regions
        self.misses+=1;dbu=self.model.data.dbu_um
        out=[('线宽不足',rule_region(geometry,width,dbu)),('线距不足',rule_region(geometry,space,dbu,'space'))]
        if angle:out.append(('最小内角不足',angle_regions(geometry,angle,dbu)))
        out=[(k,g) for k,g in out if not g.is_empty]
        with self.lock:
            self.cache[key]=out
            while len(self.cache)>self.capacity:self.cache.popitem(last=False)
        return out
    def frame(self,states,replacements=None,settings=None,progress=lambda *_:None,owners=None,scopes=None):
        from .current_drc import frame
        return frame(self,states,replacements,settings,progress,owners,scopes)
