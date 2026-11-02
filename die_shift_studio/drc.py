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
        self.capacity=256
    def cached(self,geometry,width,space,angle):
        key=(geometry.wkb,width,space,angle)
        if key in self.cache:self.hits+=1;self.cache.move_to_end(key);return self.cache[key]
        from .deformation import angle_regions
        self.misses+=1;dbu=self.model.data.dbu_um
        out=[('线宽不足',rule_region(geometry,width,dbu)),('线距不足',rule_region(geometry,space,dbu,'space'))]
        if angle:out.append(('最小内角不足',angle_regions(geometry,angle,dbu)))
        out=[(k,g) for k,g in out if not g.is_empty]
        self.cache[key]=out
        while len(self.cache)>self.capacity:self.cache.popitem(last=False)
        return out
    def frame(self,states,replacements=None,settings=None,progress=lambda *_:None):
        from .drc_rules import effective_rules
        m=self.model;p=self.p;dbu=m.data.dbu_um;replacements=replacements or {};groups={};items=[]
        width,space=effective_rules(p,settings)
        # Check the reconstructed fixed RDL, including unchanged source copper.
        # Do not union different nets: union would erase shorted boundaries.
        for key,role in m.roles.items():
            if role=='固定RDL':groups.setdefault(key.split('/')[0],[]).append(key)
        for n,(plane,keys) in enumerate(groups.items()):
            progress(round(90*n/max(1,len(groups))),f'全局 DRC · 固定 RDL {plane}')
            nets={};world=[]
            for key in keys:
                hidden={i for q in replacements.get(key,[]) for i in q['indices']}
                for i,poly in enumerate(m.cache.layers[key]['geometry']):
                    if i in hidden:continue
                    net=m.nodes[m.ids[key,i]]['net'];nets.setdefault(net,[]).append((key,poly))
                for q in replacements.get(key,[]):nets.setdefault(q['net'],[]).append((key,q['geometry']))
            for net,rows in nets.items():
                copper=shapely.union_all([v[1] for v in rows]);layers=tuple(sorted({v[0] for v in rows}));key=layers[0]
                angle=settings.deformation_min_angle_deg if settings else 0
                for kind,g in self.cached(copper,width,space,angle):
                    items.append(dict(layer=key,layers=layers,geometry=g,kind=kind,net=net))
                world.append((key,copper,net,layers))
            if world:
                tree=STRtree([r[1] for r in world])
                for i,(key,g,net,layers) in enumerate(world):
                    for j in tree.query(g,predicate='dwithin',distance=space):
                        if j<=i:continue
                        other=world[j]
                        bad=separation_region(g,other[1],space,dbu)
                        if not bad.is_empty:
                            items.append(dict(layer=key,layers=tuple(set(layers+other[3])),geometry=bad,kind='异网短接 / 线距不足',nets=(net,other[2])))
        # Enclosure uses actual adjacent metal only, with no test at unconnected vias.
        for r in m.records:
            targets=[]
            for key,i in m.connections.fixed_interfaces(r['key'],r['index']):
                replacement=next((q['geometry'] for q in replacements.get(key,[]) if i in q['indices']),None)
                targets.append(m.cache.layers[key]['geometry'][i] if replacement is None else replacement)
            if not targets:continue
            via=m.moved(r['poly'],r['owner'],states)
            missing=via.buffer(p.annulus_um/math.cos(math.pi/128),quad_segs=32).difference(shapely.union_all(targets))
            if missing.area>dbu*dbu:items.append(dict(layer=r['target_layers'][0],layers=tuple(r['target_layers']),geometry=missing,kind='孔盘包围不足'))
        from .edge_clearance import edge_violations
        items.extend(edge_violations(m,states,p,settings,replacements))
        progress(100,f'全局 DRC 完成 · {len(items)} 组标记')
        return items
