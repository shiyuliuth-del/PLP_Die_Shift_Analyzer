"""Fixed-RDL rules shared by candidate ranking, live feedback and yield.

Nets are the NOMINAL electrical identities. Reconstructed copper must never be
merged with another net before checking shorts. Visibility is not an input.
"""
from collections import defaultdict
from functools import lru_cache
import math
import numpy as np
import shapely
from .drc import rule_region,separation_region
from .drc_rules import effective_rules
from .reconstruction import transform


@lru_cache(maxsize=512)
def intrinsic(wkb,width,space,dbu):
    g=shapely.from_wkb(wkb)
    return tuple((name,bad) for name,bad in
                 (('线宽不足',rule_region(g,width,dbu)),('线距不足',rule_region(g,space,dbu,'space')))
                 if not bad.is_empty)


class FixedRules:
    def __init__(self,model,p,cfg):
        self.model=model;self.p=p;self.cfg=cfg;self.width,self.space=effective_rules(p,cfg)
        self.dbu=max(model.data.dbu_um,1e-6);self.nets={};self.refnet={};self.vias=defaultdict(list)
        for node in model.nodes:
            layer=node['key']
            if model.roles.get(layer)!='固定RDL':continue
            key=(layer.split('/')[0],node['net']);ref=(layer,node['index'])
            self.nets.setdefault(key,[]).append(ref);self.refnet[ref]=key
        self.original={key:shapely.union_all([model.nodes[model.ids[ref]]['poly'] for ref in refs]) for key,refs in self.nets.items()}
        links=defaultdict(set)
        for link in model.connections.links:
            a=(link['upper'],link['upper_index']);b=(link['lower'],link['lower_index'])
            for v,r in ((a,b),(b,a)):
                if model.roles.get(v[0]) in ('运动过孔','固定过孔') and r in self.refnet:links[v].add(self.refnet[r])
        for r in model.records:
            ref=(r['key'],r['index'])
            links[ref].update(self.refnet[q] for q in model.connections.fixed_interfaces(*ref) if q in self.refnet)
        for ref,keys in links.items():
            node=model.nodes[model.ids[ref]]
            # Circumscribed enclosure: do not silently under-check circle chords.
            required=node['poly'].buffer(p.annulus_um/math.cos(math.pi/128),quad_segs=32)
            for key in keys:self.vias[key].append((ref,int(node['owner']),required))
        self.board=model.case.get('board');self.inside=self.board.buffer(-self.space) if self.board is not None else None
        from .width_keepout import forbidden
        self.keepout=forbidden(model)

    def geometries(self,replacements):
        changed={}
        for layer,rows in (replacements or {}).items():
            for row in rows:
                for index in row['indices']:changed[layer,index]=row['geometry']
        return {key:shapely.union_all([changed.get(ref,self.model.nodes[self.model.ids[ref]]['poly']) for ref in refs])
                if any(ref in changed for ref in refs) else self.original[key] for key,refs in self.nets.items()}

    def local(self,key,g,states,vias=None):
        out=list(intrinsic(g.wkb,self.width,self.space,self.dbu))
        for ref,owner,required in (self.vias[key] if vias is None else vias):
            target=self.model.moved(required,owner,states)
            missing=target.difference(g)
            if missing.area>self.dbu*self.dbu:out.append(('孔盘包围不足',missing))
        if self.board is not None and (not self.board.covers(g) or g.distance(self.board.boundary)<self.space-self.dbu*.001):
            bad=g.difference(self.inside)
            if not bad.is_empty:out.append(('封装边缘线距不足',bad))
        if not self.keepout.is_empty and shapely.relate_pattern(g,self.keepout,'T********'):
            out.append(('进入禁布区',g.intersection(self.keepout)))
        return out

    def pair(self,a,b):
        if a.distance(b)>=self.space:return shapely.Polygon()
        return separation_region(a,b,self.space,self.dbu)

    def pair_fails(self,a,b):
        if a.distance(b)>=self.space:return False
        if a.intersects(b):return True
        from .native_backend import any_violation
        return any_violation('separation',a.wkb,self.dbu,self.space,b.wkb)

    def passes(self,key,g,states,vias=None):
        """Boolean version: cheap exact failures precede native edge-pair work."""
        for ref,owner,required in (self.vias[key] if vias is None else vias):
            if self.model.moved(required,owner,states).difference(g).area>self.dbu*self.dbu:return False
        if self.board is not None and (not self.board.covers(g) or g.distance(self.board.boundary)<self.space-self.dbu*.001):return False
        if not self.keepout.is_empty and shapely.relate_pattern(g,self.keepout,'T********'):return False
        from .native_backend import any_violation
        return not (any_violation('width',g.wkb,self.dbu,self.width) or any_violation('space',g.wkb,self.dbu,self.space))

    def check(self,replacements,states,progress=lambda *_:None):
        geos=self.geometries(replacements);out=[];keys=list(geos)
        for n,key in enumerate(keys):
            progress(round(70*n/max(1,len(keys))),'检查重构线宽、线距与包围量')
            for kind,g in self.local(key,geos[key],states):
                out.append(dict(kind=kind,geometry=g,layers=tuple(sorted({r[0] for r in self.nets[key]})),net=key))
        for plane in {k[0] for k in keys}:
            selected=[k for k in keys if k[0]==plane];values=[geos[k] for k in selected];tree=shapely.STRtree(values)
            for i,a in enumerate(values):
                for j in tree.query(a,predicate='dwithin',distance=self.space):
                    if j<=i:continue
                    g=self.pair(a,values[j])
                    if not g.is_empty:
                        layers=tuple(sorted({r[0] for k in (selected[i],selected[j]) for r in self.nets[k]}))
                        out.append(dict(kind='异网短接 / 线距不足',geometry=g,layers=layers,net=(selected[i],selected[j])))
        progress(100,f'重构约束检查完成 · {len(out)} 组违规')
        return out

    def candidate_mass(self,plan,part,grid,style,limit=math.inf):
        """Probability-ranked local search, other nets at their nominal shapes.

        This is a bounded candidate heuristic, NOT the joint final yield. The
        final integrator checks actual independently reconstructed neighbours.
        """
        from .reconstruction_route import warp
        key=self.refnet[plan.layer,plan.index];refs=self.nets[key]
        neighbourhood=plan.source.buffer(2*max(self.width,self.space)+2*self.dbu)
        rest=shapely.union_all([self.model.nodes[self.model.ids[r]]['poly'].intersection(neighbourhood) for r in refs if r!=(plan.layer,plan.index) and self.model.nodes[self.model.ids[r]]['poly'].intersects(neighbourhood)])
        vias=[v for v in self.vias[key] if self.model.nodes[self.model.ids[v[0]]]['poly'].intersects(plan.source)]
        others=[g for k,g in self.original.items() if k[0]==key[0] and k!=key]
        tree=shapely.STRtree(others);failure=0.;count=0
        states=np.zeros((grid.count,3));weights=grid.weights[plan.owner]
        for i in np.argsort(-weights):
            if weights[i]<=0:continue
            states[plan.owner]=grid.locals[plan.owner][i]
            regions,error=warp(part,plan,self.model.bound.centers[plan.owner],states[plan.owner],style)
            bad=bool(error)
            if not bad:
                copper=shapely.union_all([rest,*regions.values()])
                bad=not self.passes(key,copper,states,vias)
                if not bad:
                    bad=any(self.pair_fails(copper,others[int(j)]) for j in tree.query(copper,predicate='dwithin',distance=self.space))
            if bad:failure+=float(weights[i]);count+=1
            if failure>limit+1e-15:break
        return float(failure),count
