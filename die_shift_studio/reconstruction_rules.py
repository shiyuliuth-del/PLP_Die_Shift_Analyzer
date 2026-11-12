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
        self.contexts={};self.hard_memo={}
        from .session_cache import logical_fingerprint
        self.identity=logical_fingerprint((model.data.geometry_id,model.roles,model.connections.order,self.width,self.space,p.annulus_um))

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

    def context(self,plan):
        tag=(plan.key,plan.identity)
        if tag in self.contexts:return self.contexts[tag]
        key=self.refnet[plan.layer,plan.index];refs=self.nets[key]
        neighbourhood=plan.source.buffer(2*max(self.width,self.space)+2*self.dbu)
        rest=shapely.union_all([self.model.nodes[self.model.ids[r]]['poly'].intersection(neighbourhood) for r in refs
                               if r!=(plan.layer,plan.index) and self.model.nodes[self.model.ids[r]]['poly'].intersects(neighbourhood)])
        vias=[v for v in self.vias[key] if v[1] in (-1,plan.owner) and self.model.nodes[self.model.ids[v[0]]]['poly'].intersects(plan.source)]
        others=[g for k,g in self.original.items() if k[0]==key[0] and k!=key]
        value=(key,rest,vias,others,shapely.STRtree(others));self.contexts[tag]=value;return value

    def best_regions(self,plan,part,center,pose,style):
        """Keep endpoints fixed; compare finite style-preserving curve candidates.

        The reference neighbours are nominal and fixed, so this decision depends
        only on the owning DIE. Final posed neighbours are checked separately.
        """
        from .reconstruction_route import warp
        from .turn_connection import VARIANTS
        token=('rule-choice-v39',self.identity,style,part['a'],part['b'],np.asarray(center).tobytes(),np.asarray(pose).tobytes())
        if token in plan.pose_memo:
            value,_,_=plan.pose_memo[token];plan.pose_memo.move_to_end(token)
            geos,error,score=value;return None if geos is None else dict(geos),error,score
        regions,error=warp(part,plan,center,pose,style)
        if error:return None,error,(float('inf'),float('inf'))
        key,rest,vias,others,tree=self.context(plan);states=np.zeros((len(self.model.bound.centers),3));states[plan.owner]=pose
        def quality(candidate):
            from .native_backend import any_violation
            copper=shapely.union_all([rest,*candidate.values()]);kinds=set();amount=0.
            for op,value in (('width',self.width),('space',self.space)):
                if any_violation(op,copper.wkb,self.dbu,value):kinds.add(op)
            for ref,owner,required in vias:
                missing=self.model.moved(required,owner,states).difference(copper).area
                if missing>self.dbu*self.dbu:kinds.add('enclosure');amount+=missing/max(self.width*self.space,1e-12)
            if self.board is not None and (not self.board.covers(copper) or copper.distance(self.board.boundary)<self.space-self.dbu*.001):
                kinds.add('board');amount+=copper.difference(self.inside).area/max(self.width*self.space,1e-12)
            if not self.keepout.is_empty and shapely.relate_pattern(copper,self.keepout,'T********'):
                kinds.add('keepout');amount+=copper.intersection(self.keepout).area/max(self.width*self.space,1e-12)
            for j in tree.query(copper,predicate='dwithin',distance=self.space):
                other=others[int(j)]
                if self.pair_fails(copper,other):
                    kinds.add('foreign');gap=copper.distance(other)
                    amount+=(self.space-gap)/self.space+(copper.intersection(other).area/max(self.width*self.space,1e-12) if gap==0 else 0)
            return (len(kinds),amount)
        score=quality(regions);best=regions;best_score=score
        hard_key=(plan.identity,part['a'],part['b'])
        if hard_key not in self.hard_memo:
            seam=shapely.union_all([part['near'],part['far']]);permanent=False
            for g in (part['B'],part['C']):
                if g.is_empty:continue
                for op,value in (('width',self.width),('space',self.space)):
                    bad=rule_region(g,max(self.dbu,value-4*self.dbu),self.dbu,op)
                    if not bad.is_empty and bad.distance(seam)>max(self.width,self.space)*2:permanent=True
            self.hard_memo[hard_key]=permanent
        permanent=self.hard_memo[hard_key]
        if score[0] and style!='linear' and np.any(pose) and not permanent:
            for v in range(1,len(VARIANTS)+1):
                alternate,reason=warp(part,plan,center,pose,style,v)
                if reason:continue
                candidate_score=quality(alternate)
                if candidate_score<best_score:best,best_score=alternate,candidate_score
                if best_score[0]==0:break
        size=sum(int(shapely.get_num_coordinates(g))*24 for g in best.values())+512
        if size<=plan.memo_budget:
            plan.pose_memo[token]=((dict(best),None,best_score),None,size);plan.memo_bytes+=size
            while plan.memo_bytes>plan.memo_budget or len(plan.pose_memo)>32:
                _,old=plan.pose_memo.popitem(last=False);plan.memo_bytes-=old[2]
        return dict(best),None,best_score

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
        failure=0.;count=0;weights=grid.weights[plan.owner]
        for i in np.argsort(-weights):
            if weights[i]<=0:continue
            regions,error,score=self.best_regions(plan,part,self.model.bound.centers[plan.owner],grid.locals[plan.owner][i],style)
            bad=bool(error) or score[0]>0
            if bad:failure+=float(weights[i]);count+=1
            if failure>limit+1e-15:break
        return float(failure),count
