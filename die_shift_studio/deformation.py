"""Constrained, source-derived RDL preview. No edits are made to the input GDS.

A compact C1/C2 displacement field preserves rigid landing regions and is the
identity outside its support. Candidate contours are adaptively subdivided,
snapped to the source DBU, and validated using KLayout width checks and actual
parallel native edge checks. Any failed constraint keeps the source. Only verified candidates are displayed.
"""
from collections import OrderedDict
from dataclasses import dataclass
import math
from functools import lru_cache
import numpy as np
import shapely
from shapely.geometry import Polygon,Point
from shapely.strtree import STRtree
from .preview import rigid_transform


def smooth_weight(t,continuity='C2'):
    """Unit plateau → identity; zero endpoint derivatives through chosen order."""
    t=np.clip(np.asarray(t,float),0,1)
    return 1-(t*t*(3-2*t) if continuity=='C1' else t**3*(10+t*(-15+6*t)))


from .native_backend import compute as _cached_native_region, geometry as native_operation

def snapped(geometry,dbu):
    result=native_operation('normalize',geometry,dbu)
    return result,result

def width_ok(geometry,width,dbu):
    from .drc import rule_region
    return rule_region(geometry,width,dbu).is_empty


def width_regions(geometry,width,dbu):
    from .drc import rule_region
    return rule_region(geometry,width,dbu)


def angle_regions(geometry,minimum,dbu):
    """Copper-interior convex angle, holes included; 0.2 degree DBU tolerance."""
    parts=[]
    from shapely.geometry.polygon import orient
    for poly in shapely.get_parts(geometry):
        if poly.geom_type!='Polygon':continue
        poly=orient(poly,sign=1.)
        for ring in [poly.exterior,*poly.interiors]:
            xy=np.asarray(ring.coords)[:-1];a=xy-np.roll(xy,1,axis=0);b=np.roll(xy,-1,axis=0)-xy
            turn=np.degrees(np.arctan2(a[:,0]*b[:,1]-a[:,1]*b[:,0],np.sum(a*b,axis=1)))
            angles=180-turn
            for i in np.flatnonzero(angles<minimum-.2):
                radius=max(dbu*2,min(np.linalg.norm(a[i]),np.linalg.norm(b[i]))*.25)
                parts.append(Point(xy[i]).buffer(radius).intersection(poly))
    return shapely.union_all(parts)


def baseline_check(item):
    from .drc import rule_region
    wkb,width,dbu,*spacing=item;poly=shapely.from_wkb(wkb)
    if not rule_region(poly,width,dbu).is_empty:return '原图形线宽不足'
    if spacing and not rule_region(poly,spacing[0],dbu,'notch').is_empty:return '原图形内部线距不足'
    return ''


class Field:
    def __init__(self,records,centers,states,inner,span,settings):
        self.records=records;self.centers=centers;self.states=states;self.inner=np.asarray(inner);self.span=span;self.settings=settings
    def weights(self,xy):
        distances=np.linalg.norm(xy[:,None,:]-np.array([r['center'] for r in self.records])[None,:,:],axis=2)
        return smooth_weight((distances-self.inner)/self.span,self.settings.deformation_continuity)
    def active_edges(self,a,b):
        active=np.zeros(len(a),bool);v=b-a;den=np.maximum(np.sum(v*v,axis=1),1e-30)
        for r,radius in zip(self.records,self.inner+self.span):
            center=np.asarray(r['center']);t=np.clip(np.sum((center-a)*v,axis=1)/den,0,1)
            active|=np.sum((a+t[:,None]*v-center)**2,axis=1)<=radius*radius
        return active
    def jacobian_bound(self):
        # ||D(T-I)|| < 1 is a global sufficient injectivity condition. It
        # prevents folds between sampled edges, not merely at drawn vertices.
        gradient=1.5 if self.settings.deformation_continuity=='C1' else 1.875
        points=np.array([r['center'] for r in self.records]);radii=self.inner+self.span
        owners=np.array([r['owner'] for r in self.records]);rates=2*np.abs(np.sin(self.states[:,2]/2))
        gamma=math.radians(self.settings.deformation_arc_angle_deg)
        factor=1. if self.settings.deformation_method=='bezier' else gamma/(2*math.sin(gamma/2))
        travel=np.array([np.linalg.norm(rigid_transform(point[None,:],self.centers[owner],self.states[owner])[0]-point) for point,owner in zip(points,owners)])
        coefficients=factor*gradient/self.span*(travel+rates[owners]*radii)
        distances=np.linalg.norm(points[:,None,:]-points[None,:,:],axis=2)
        overlap=distances<=radii[:,None]+radii[None,:]
        # At any location all active supports intersect each other, so the
        # active set is contained in every active disk's neighbour set. Taking
        # the maximum neighbour sum is a valid bound without adding remote pads.
        return max((coefficients[mask].sum()+rates[np.unique(owners[mask])].sum() for mask in overlap),default=0.)
    def __call__(self,xy):
        xy=np.asarray(xy);weights=self.weights(xy);out=xy.copy()
        for owner in sorted({r['owner'] for r in self.records}):
            columns=[i for i,r in enumerate(self.records) if r['owner']==owner]
            w=1-np.prod(1-weights[:,columns],axis=1);state=self.states[owner];center=self.centers[owner]
            if self.settings.deformation_method=='bezier':
                delta=rigid_transform(xy,center,state)-xy
                out+=w[:,None]*delta
            else:
                # Circular interpolation between each original point and its
                # rigid destination. A smooth clock gives C1/C2 support joins.
                gamma=math.radians(self.settings.deformation_arc_angle_deg)*(1 if state[2]>=0 else -1)
                phase=gamma*w;cot=1/math.tan(gamma/2)
                a=.5*(1-np.cos(phase)+cot*np.sin(phase))
                b=.5*(cot*(1-np.cos(phase))-np.sin(phase))
                delta=rigid_transform(xy,center,state)-xy
                out+=a[:,None]*delta+b[:,None]*np.column_stack((-delta[:,1],delta[:,0]))
        return out


def warp_polygon(poly,field,tolerance,step,prepared=None):
    """Quarter/midpoint refinement controls contour chord error; never repairs folds."""
    def ring(r):
        # Bounded pre-segmentation also prevents missing compact support on a long edge.
        original=np.asarray(r.coords);a,b=original[:-1],original[1:]
        active=field.active_edges(a,b) if hasattr(field,'active_edges') else np.ones(len(a),bool)
        key=(r.wkb,float(step),active.tobytes())
        if prepared is not None and key in prepared:xy=prepared[key]
        else:
            counts=np.where(active,np.maximum(1,np.ceil(np.linalg.norm(b-a,axis=1)/step)).astype(int),1)
            ids=np.repeat(np.arange(len(a)),counts);offset=np.arange(len(ids))-np.repeat(np.r_[0,counts.cumsum()[:-1]],counts)
            xy=np.vstack((a[ids]+(b-a)[ids]*(offset/counts[ids])[:,None],original[-1]))
            if prepared is not None:prepared[key]=xy
        for _ in range(10):
            a,b=xy[:-1],xy[1:];fa,fb=field(a),field(b);bad=np.zeros(len(a),bool)
            for t in (.25,.5,.75):
                bad|=np.linalg.norm(field(a*(1-t)+b*t)-(fa*(1-t)+fb*t),axis=1)>tolerance
            if not bad.any():return field(xy)
            if len(xy)>150000:raise ValueError('曲线细分预算不足')
            indices=np.repeat(np.arange(len(a)),1+bad);out=xy[indices].copy();extra=np.cumsum(1+bad)[bad]-1
            out[extra]=(a[bad]+b[bad])/2;xy=np.vstack((out,xy[-1]))
        raise ValueError('曲线误差未收敛')
    parts=[]
    pending=[poly]
    while pending:
        p=pending.pop()
        if p.is_empty:continue
        if p.geom_type in ('MultiPolygon','GeometryCollection'):
            pending.extend(reversed(list(p.geoms)));continue
        # A clipping plane may touch a remote branch in a zero-area line/point.
        # It is not copper area and must not crash adaptation-band rendering.
        if p.geom_type!='Polygon':
            if p.area==0:continue
            raise ValueError('型变区域含非面几何')
        q=Polygon(ring(p.exterior),[ring(r) for r in p.interiors])
        if not q.is_valid or q.is_empty or len(q.interiors)!=len(p.interiors):raise ValueError('型变折叠或拓扑改变')
        parts.append(q)
    return shapely.union_all(parts) if parts else Polygon()


@dataclass
class Component:
    key:str
    indices:tuple
    net:int
    poly:object
    records:list
    anchors:list


class DeformationPreview:
    def __init__(self,contact,parameters,result,settings):
        contact.configure_parameters(parameters);self.model=contact;self.p=parameters;self.settings=settings;self.cache=__import__('die_shift_studio.session_cache',fromlist=['SessionCache']).SessionCache();self.components=[]
        # Statistical insufficiency is an exclusion, never evidence of sufficient routing.
        ca=(result or {}).get('contact_analysis',{})
        self.blocked={(q['source_layer'],q['source_index']) for q in (result or {}).get('issues',[])}
        self.unresolved={(q['source_layer'],q['source_index']) for q in ca.get('unresolved',[])}
        self.blocked|=self.unresolved
        con=contact.connections;grouped={}
        for r in contact.records:
            for key,index in con.fixed_interfaces(r['key'],r['index']):
                component=con.component(key,index)
                grouped.setdefault((key,component),[]).append(r)
        for (key,component),records in grouped.items():
            # Include all fragments of this SAME-PLANE connected copper component.
            members=[con.nodes[n] for n in con.components[component] if con.nodes[n][0]==key]
            indices=tuple(q[1] for q in members);poly=shapely.union_all([q[2] for q in members])
            net=contact.nodes[contact.ids[key,indices[0]]]['net'];anchors=[]
            for index in indices:
                for side,links in [('lower',con.outgoing[key].get(index,[])),('upper',con.incoming[key].get(index,[]))]:
                    for link in links:
                        target=link[side];j=link[side+'_index']
                        if contact.roles[target]=='固定过孔':anchors.append(contact.cache.layers[target]['geometry'][j])
            records=list({(r['key'],r['index']):r for r in records}.values())
            self.components.append(Component(key,indices,net,poly,records,anchors))
        from .drc_rules import effective_rules
        self.rule_width,self.rule_space=effective_rules(self.p,self.settings)
        self.originals={k:contact.cache.layers[k]['geometry'] for k in {c.key for c in self.components}}
        self.trees={k:STRtree(v) for k,v in self.originals.items()}
        self.component_base={};self.contours={};self.preferred_span={};self.route_templates={};self.last_fields={};self.sources={};self.intrinsic_cache=OrderedDict()
        from .resources import parallel_map,resource_profile
        vertices=sum(shapely.get_num_coordinates(c.poly) for c in self.components)
        process=len(self.components)>=8 and vertices>=200000 and resource_profile()['process_workers']>1
        checks=parallel_map(baseline_check,[(c.poly.wkb,self.rule_width,self.model.data.dbu_um,self.rule_space) for c in self.components],process=process)
        for c,reason in zip(self.components,checks):
            self.component_base[c.key,c.indices]='源铜图形并非单一连通区' if c.poly.geom_type!='Polygon' else reason
        self.executor=None;self.execution_used='serial';self.worker_count=1;self.frame_generation=0

    def release_workers(self):
        if self.executor is not None:
            self.executor.shutdown(wait=True,cancel_futures=True);self.executor=None

    def close(self):
        self.release_workers();self.cache.close()

    def prepare_pose(self,states):
        moved=[self.model.moved(r['poly'],r['owner'],states) for r in self.model.records]
        self._posed_vias={(r['key'],r['index']):v for r,v in zip(self.model.records,moved)}
        self._via_required={(r['key'],r['index']):v.buffer(self.p.annulus_um/math.cos(math.pi/128),quad_segs=32) for r,v in zip(self.model.records,moved)}
        self._via_controls=[v.buffer(self.p.annulus_um+self.p.line_space_um,quad_segs=32) for v in moved]
        self._moved_via_trees={}
        for plane in self.model.fixed_trees:
            ids=[i for i,r in enumerate(self.model.records) if any(k.split('/')[0]==plane for k in r['target_layers'])]
            self._moved_via_trees[plane]=(ids,STRtree([moved[i] for i in ids]))
        return moved

    def proposals(self,fn,tasks,states,replacements,iteration,quick,progress):
        if not tasks:return []
        from .resources import resource_profile,parallel_map
        cfg=self.settings;profile=resource_profile();mode=cfg.deformation_execution
        if mode=='auto':mode='process' if len(tasks)>=12 and (not quick or self.executor is not None) else 'serial'
        maximum=profile['process_workers'] if mode=='process' else profile['workers']
        if mode=='process':
            coordinates=sum(int(shapely.get_num_coordinates(v['geometry']).sum()) for v in self.model.cache.layers.values())
            estimate=max(768*1024**2,coordinates*128+self.model.data.count*2048+256*1024**2)
            maximum=min(maximum,max(1,int(profile['available_memory_mb']*1024**2*.35)//estimate))
        workers=min(maximum,cfg.deformation_workers or maximum,len(tasks))
        if workers<2 or mode=='serial':
            profile['workers']=1;self.execution_used='serial';self.worker_count=1
            return parallel_map(fn,tasks,profile,progress=progress)
        self.execution_used=mode;self.worker_count=workers
        if mode=='thread':
            profile['workers']=workers
            return parallel_map(fn,tasks,profile,progress=progress)
        from concurrent.futures import ProcessPoolExecutor,as_completed
        import multiprocessing,logging
        from .adaptation_workers import initialize,search_job
        futures={}
        try:
            if self.executor is None:
                self.executor=ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn'),
                    initializer=initialize,initargs=(self.model,self.p,cfg))
            stamp=(self.frame_generation,iteration)
            futures={self.executor.submit(search_job,((c.key,c.indices),signature,states,replacements,stamp,quick)):i
                     for i,(c,active,anchors,signature,context) in enumerate(tasks)}
            results=[None]*len(tasks)
            for completed,future in enumerate(as_completed(futures),1):
                results[futures[future]]=future.result();progress(completed,len(tasks))
            return results
        except InterruptedError:
            # Changing the selected pose cancels this request. It must not be
            # mistaken for a process failure and launch a redundant serial solve.
            for future in futures:future.cancel()
            raise
        except Exception:
            logging.getLogger(__name__).exception('并行型变搜索失败；改为本进程重算，仍执行全部约束检查')
            self.release_workers();self.execution_used='serial fallback';self.worker_count=1
            profile['workers']=1
            return parallel_map(fn,tasks,profile,progress=progress)
    def _replacements(self,accepted):
        out={}
        for a in accepted.values():
            c=a['component'];g=a['geometry'];field=a['field']
            if 'display' in a:
                out.setdefault(c.key,[]).append(a['display']);continue
            support=getattr(field,'support',None)
            if support is None:support=shapely.union_all([Point(r['center']).buffer(inner+a['span'],quad_segs=32) for r,inner in zip(field.records,field.inner)])
            dark=g.difference(c.poly.difference(support))
            adaptation=getattr(field,'adapted_band',Polygon())
            a['display']=dict(indices=c.indices,geometry=g,dark=dark,adaptation=adaptation,mode=getattr(field,'mode','local'),span_um=a['span'],net=c.net,verified=getattr(field,'verified',True))
            out.setdefault(c.key,[]).append(a['display'])
        return out
    def _snapshot(self,replacements):
        overrides={};removed=set()
        for key,values in replacements.items():
            for q in values:
                ids=[self.model.ids[key,i] for i in q['indices']]
                overrides[ids[0]]=q['geometry'];removed.update(ids[1:])
        trees={}
        for plane,(ids,_) in self.model.fixed_trees.items():
            ids=[i for i in ids if i not in removed];geoms=[overrides.get(i,self.model.nodes[i]['poly']) for i in ids]
            trees[plane]=(ids,geoms,STRtree(geoms))
        return trees
    def frame(self,states,current,progress=lambda *_:None,quick=False):
        states=np.asarray(states,float);key=(states.tobytes(),bool(quick))
        if key in self.cache:self.cache.move_to_end(key);return self.cache[key]
        hit=lambda q:{(v['source_layer'],v['source_index']) for v in q['issues']}
        initial=hit(current);blocked=initial.copy();accepted={};provisional={};diagnostics={};history=[];previous_active={};attempted={};attempted_full=set()
        moved_vias=self.prepare_pose(states);self.frame_generation+=1
        # Accepted replacements are monotone in eligible interfaces. No speculative
        # unchecked topology is drawn; best-effort candidates are explicitly flagged.
        limit=self.settings.deformation_iterations
        for iteration in range(limit):
            changed=False;before=blocked.copy();previous_accepted=accepted.copy();previous_signatures=previous_active.copy()
            snapshot=self._snapshot(self._replacements(accepted));tasks=[]
            def context_for(c,active,snapshot):
                ids,geoms,tree=snapshot[c.key.split('/')[0]]
                travel=max(np.linalg.norm(states[r['owner'],:2])+2*abs(math.sin(states[r['owner'],2]/2))*np.linalg.norm(shapely.get_coordinates(c.poly)-self.model.bound.centers[r['owner']],axis=1).max() for r in active)
                near=tree.query(c.poly,predicate='dwithin',distance=travel+self.p.annulus_um+self.p.line_space_um+2*self.p.line_width_um)
                return tuple(sorted((ids[j],hash(geoms[j])) for j in near if self.model.nodes[ids[j]]['net']!=c.net))
            for c in self.components:
                ck=(c.key,c.indices)
                active=[r for r in c.records if np.any(states[r['owner']])]
                signature=tuple((r['key'],r['index']) for r in active)
                if not active or previous_active.get(ck)==signature:continue
                context=context_for(c,active,snapshot);stamp=(signature,context)
                if attempted.get(ck)==stamp:continue
                attempted[ck]=stamp
                if len(active)==len(c.records):attempted_full.add(ck)
                anchors=c.anchors+[r['poly'] for r in c.records if r not in active]
                tasks.append((c,active,anchors,signature,context))
            def proposal(task):
                c,active,anchors,signature,context=task
                try:return self.search(c,active,anchors,states,moved_vias,snapshot,progress,quick),''
                except (ValueError,RuntimeError) as exc:return None,str(exc)
            results=self.proposals(proposal,tasks,states,self._replacements(accepted),iteration,quick,
                lambda n,total:progress(round(85*n/max(1,total)),f'型变迭代 {iteration+1}/{limit} · 搜索 {n}/{total} · {self.execution_used} {self.worker_count}'))
            latest_snapshot=snapshot
            for task,(candidate_result,reason) in zip(tasks,results):
                c,active,anchors,signature,context=task;ck=(c.key,c.indices)
                if not reason:
                    if latest_snapshot is None:latest_snapshot=self._snapshot(self._replacements(accepted))
                    latest=latest_snapshot
                    if context_for(c,active,latest)!=context:
                        try:
                            candidate,field,span=candidate_result
                            bad=self.candidate_violations(c,candidate,c.records,c.anchors,states,moved_vias,latest)
                            if bad:candidate_result=self.search(c,active,anchors,states,moved_vias,latest,progress,quick)
                            else:
                                field.verified=True;field.failure_reasons=[];field.candidate_violations=[]
                                if field.mode=='three_part' and not hasattr(field,'adapted_band'):
                                    field.adapted_band=warp_polygon(field.band,field,max(self.model.data.dbu_um,self.settings.deformation_tolerance_um),max(1,self.p.line_width_um/2)).intersection(candidate)
                        except (ValueError,RuntimeError) as exc:reason=str(exc)
                    if not reason:
                        candidate,field,span=candidate_result
                        if not field.verified:
                            provisional[ck]=dict(component=c,geometry=candidate,field=field,span=span)
                            reason='最优候选仍有DRC问题：'+'；'.join(field.failure_reasons)
                            if self.component_base[ck]:reason=self.component_base[ck]+'；'+reason
                if reason:
                    diagnostics[ck]=dict(layer=c.key,indices=c.indices,reason=reason,point=list(c.poly.representative_point().coords[0]));continue
                self.last_fields[(c.key,c.indices,signature)]=field;self.preferred_span[ck]=span
                accepted[ck]=dict(component=c,geometry=candidate,field=field,span=span)
                previous_active[ck]=signature;diagnostics.pop(ck,None);provisional.pop(ck,None);changed=True;latest_snapshot=None
            if changed:
                # Each proposal has already checked ALL other moving-via control
                # regions. Revalidate the complete batch once, avoiding O(M*N)
                # identical buffer transformations for M accepted copper regions.
                updated=self.model.current(states,self.p,self._replacements(accepted));new_hit=hit(updated)
                if new_hit-blocked:
                    for ck,a in accepted.items():
                        if ck not in previous_accepted:
                            c=a['component'];diagnostics[ck]=dict(layer=c.key,indices=c.indices,reason='整轮接触复核未通过，保留源图形',point=list(c.poly.representative_point().coords[0]))
                    accepted=previous_accepted;previous_active=previous_signatures
                    history.append(dict(iteration=iteration+1,newly_sufficient=0,remaining=len(blocked),accepted=len(accepted),rollback='新增接触，整轮回退'));break
                blocked=new_hit;current=updated
            newly=before-blocked;history.append(dict(iteration=iteration+1,newly_sufficient=len(newly),remaining=len(blocked),accepted=len(accepted)))
            if not changed:break
        # Every moving interface gets a search outcome, including blocked ones.
        # Unverified candidates remain internal and are never drawn.
        for c in self.components:
            ck=(c.key,c.indices)
            if ck in accepted or ck in provisional or ck in attempted_full:continue
            active=[r for r in c.records if np.any(states[r['owner']])]
            if not active:continue
            try:
                candidate,field,span=self.search(c,active,c.anchors,states,moved_vias,self._snapshot(self._replacements(accepted)),progress,quick)
                entry=dict(component=c,geometry=candidate,field=field,span=span)
                if field.verified:accepted[ck]=entry;diagnostics.pop(ck,None)
                else:
                    provisional[ck]=entry
                    diagnostics[ck]=dict(layer=c.key,indices=c.indices,reason='最优候选仍有DRC问题：'+'；'.join(field.failure_reasons),point=list(c.poly.representative_point().coords[0]))
            except (ValueError,RuntimeError) as exc:
                diagnostics.setdefault(ck,dict(layer=c.key,indices=c.indices,reason=str(exc),point=list(c.poly.representative_point().coords[0])))
        # A neighbour may need to move at the same time to free the corridor.
        # Try the best pending candidates together, pruning failures against the
        # restored source until a verified fixed point remains. Never draw trials.
        joint=dict(accepted);joint.update(provisional);joint_rounds=0
        while joint and joint_rounds<self.settings.deformation_joint_rounds:
            snapshot=self._snapshot(self._replacements(joint));remove=[]
            for ck,a in joint.items():
                if ck in accepted:continue
                c=a['component']
                if self.candidate_violations(c,a['geometry'],c.records,c.anchors,states,moved_vias,snapshot):remove.append(ck)
            if not remove:break
            for ck in remove:joint.pop(ck)
            joint_rounds+=1
        # Exhausting a configurable pruning budget is not an acceptance proof.
        joint_snapshot=self._snapshot(self._replacements(joint))
        for ck,a in joint.items():
            if ck in accepted:continue
            c=a['component']
            if self.candidate_violations(c,a['geometry'],c.records,c.anchors,states,moved_vias,joint_snapshot):continue
            a['field'].verified=True;a['field'].candidate_violations=[];a['field'].failure_reasons=[];a.pop('display',None)
            if a['field'].mode=='three_part' and not hasattr(a['field'],'adapted_band'):
                a['field'].adapted_band=warp_polygon(a['field'].band,a['field'],max(self.model.data.dbu_um,self.settings.deformation_tolerance_um),max(1,self.p.line_width_um/2)).intersection(a['geometry'])
            accepted[ck]=a;diagnostics.pop(ck,None)
        # Roll back unsafe copper, then recheck neighbours against the ACTUAL
        # restored source. Removing one replacement can invalidate another.
        # This finite fixed point only removes candidates; it must terminate.
        from .drc import GlobalDRC
        if not hasattr(self,'drc'):self.drc=GlobalDRC(self.model,self.p)
        rollback_rounds=0;final_drc=None
        while accepted:
            replacements=self._replacements(accepted);final_snapshot=self._snapshot(replacements);rejected=[]
            for ck,a in accepted.items():
                c=a['component']
                bad=self.candidate_violations(c,a['geometry'],c.records,c.anchors,states,moved_vias,final_snapshot)
                if bad:
                    rejected.append(ck)
                    diagnostics[ck]=dict(layer=c.key,indices=c.indices,reason='最终布局复核不通过，恢复原图：'+'；'.join(sorted({v['kind'] for v in bad})),point=list(c.poly.representative_point().coords[0]))
            # Merged same-net copper can expose a notch absent from an isolated
            # candidate. The final plane-level DRC is also an acceptance gate.
            final_drc=self.drc.frame(states,replacements,self.settings,progress)
            for ck,a in accepted.items():
                c=a['component']
                bad=[v['kind'] for v in final_drc if c.net in v.get('nets',(v.get('net'),)) and v['geometry'].intersects(a['geometry'])]
                if bad and ck not in rejected:
                    rejected.append(ck)
                    diagnostics[ck]=dict(layer=c.key,indices=c.indices,reason='最终固定RDL全层DRC未通过，恢复原图：'+'；'.join(sorted(set(bad))),point=list(c.poly.representative_point().coords[0]))
            if not rejected:break
            for ck in rejected:accepted.pop(ck)
            final_drc=None
            rollback_rounds+=1
        displayed=accepted;replacements=self._replacements(accepted)
        interfaces=[]
        for c in self.components:
            ck=(c.key,c.indices)
            active=[r for r in c.records if np.any(states[r['owner']])]
            if active and ck not in accepted:
                diag=diagnostics.setdefault(ck,dict(layer=c.key,indices=c.indices,reason='有限候选搜索未找到满足约束的型变，恢复原图',point=list(c.poly.representative_point().coords[0])))
                if '恢复原图' not in diag['reason']:diag['reason']+='；恢复原图'
                diag['points']=[list(self.model.moved(r['poly'],r['owner'],states).centroid.coords[0]) for r in active]
            for r in c.records:
                interfaces.append(dict(layer=c.key,indices=c.indices,via_layer=r['key'],via_index=r['index'],owner=r['owner'],status='已适配' if ck in accepted else ('无需移动' if not np.any(states[r['owner']]) else '未适配，恢复原图'),reason=diagnostics.get(ck,{}).get('reason','')))
        current=self.model.current(states,self.p,replacements);blocked=hit(current)
        violations=list(final_drc) if final_drc is not None else self.drc.frame(states,replacements,self.settings,progress)
        # Package / keepout and current control intrusions supplement metal DRC.
        for a in displayed.values():
            for violation in getattr(a['field'],'candidate_violations',[]):
                if violation['kind'] in ('超出封装','禁布区侵入','固定端覆盖不足'):
                    violations.append(dict(violation,layer=a['component'].key))
        for q in current['intrusions']:violations.append(dict(layer=q['target_layer'],geometry=q['geometry'],kind='控制区接触其他网络'))
        out=dict(replacements=replacements,diagnostics=list(diagnostics.values()),violations=violations,iterations=history,
                 initial_blocked_count=len(initial),released_count=len(initial-blocked),blocked_count=len(blocked),
                 unresolved_count=len(diagnostics),accepted_count=len(accepted),best_effort_count=0,rejected_candidates=len(provisional),joint_rounds=joint_rounds,rollback_rounds=rollback_rounds,interfaces=interfaces,search_quality='interactive' if quick else 'complete_family',states=states.copy(),
                 execution=self.execution_used,workers=self.worker_count,iteration_limit=limit,iteration_limit_reached=len(history)>=limit and bool(changed))
        self.cache[key]=out
        progress(100,('型变达到搜索预算：' if out['iteration_limit_reached'] else '型变搜索完成：')+f'释放 {out["released_count"]} 个窗口，剩余 {len(blocked)} 个')
        return out
    def candidate_violations(self,c,candidate,records,anchors,states,moved_vias,snapshot,cheap=False,intrinsic_only=False):
        from .drc import rule_area,separation_area
        p=self.p;dbu=self.model.data.dbu_um;bad=[]
        def add(kind,g):
            if not g.is_empty:bad.append(dict(kind=kind,geometry=g,area_um2=g.area))
        def measure(kind,area):
            if area>0:bad.append(dict(kind=kind,area_um2=area))
        if not cheap:
            from hashlib import sha256
            intrinsic_key=sha256(candidate.wkb).digest()
            if intrinsic_key in self.intrinsic_cache:
                bad.extend(self.intrinsic_cache[intrinsic_key]);self.intrinsic_cache.move_to_end(intrinsic_key)
            else:
                measure('线宽不足',rule_area(candidate,self.rule_width,dbu))
                measure('内部线距不足',rule_area(candidate,self.rule_space,dbu,'notch'))
                add('最小内角不足',angle_regions(candidate,self.settings.deformation_min_angle_deg,dbu))
                self.intrinsic_cache[intrinsic_key]=tuple(bad)
                while len(self.intrinsic_cache)>512:self.intrinsic_cache.popitem(last=False)
        if intrinsic_only:return bad
        add('超出封装',candidate.difference(self.model.case['board']))
        for r in records:
            via=self._posed_vias.get((r['key'],r['index'])) if hasattr(self,'_posed_vias') else None
            if via is None:via=self.model.moved(r['poly'],r['owner'],states)
            landing=self._via_required.get((r['key'],r['index'])) if hasattr(self,'_via_required') else None
            if landing is None:landing=via.buffer(p.annulus_um/math.cos(math.pi/128),quad_segs=32)
            missing=landing.difference(candidate)
            if not candidate.covers(via) or candidate.boundary.distance(via)+dbu*.01<p.annulus_um:add('孔盘包围不足',missing if not missing.is_empty else via.boundary.buffer(dbu))
        for g in anchors:add('固定端覆盖不足',g.difference(candidate))
        for plane in {k.split('/')[0] for r in records for k in r['target_layers']}:
            if snapshot is None:
                ids,tree=self.model.fixed_trees[plane];geoms=tree.geometries
            else:ids,geoms,tree=snapshot[plane]
            nearby=[geoms[j] for j in tree.query(candidate,predicate='dwithin',distance=self.rule_space) if self.model.nodes[ids[j]]['net']!=c.net]
            if nearby:
                foreign=shapely.union_all(nearby)
                if not cheap:measure('异网线距不足',separation_area(candidate,foreign,self.rule_space,dbu))
                if candidate.distance(foreign)+dbu*.01<self.rule_space:
                    add('异网最小净距不足',candidate.intersection(foreign.buffer(self.rule_space,quad_segs=32)))
        if hasattr(self,'_moved_via_trees'):
            ids,tree=self._moved_via_trees[c.key.split('/')[0]]
            others=[self._via_controls[ids[j]] for j in tree.query(candidate,predicate='dwithin',distance=p.annulus_um+p.line_space_um+dbu) if self.model.records[ids[j]]['net']!=c.net]
        else:others=[g.buffer(p.annulus_um+p.line_space_um,quad_segs=32) for r,g in zip(self.model.records,moved_vias) if r['net']!=c.net and any(k.split('/')[0]==c.key.split('/')[0] for k in r['target_layers'])]
        for g in others:add('其他运动孔控制区侵入',candidate.intersection(g))
        for k,v in self.model.cache.layers.items():
            if self.model.roles.get(k)=='禁布区':
                for g in v['geometry']:add('禁布区侵入',candidate.intersection(g))
        return bad

    def search(self,c,records,anchors,states,moved_vias,snapshot=None,progress=lambda *_:None,quick=False):
        p=self.p;cfg=self.settings;dbu=self.model.data.dbu_um;tol=max(dbu,cfg.deformation_tolerance_um)
        # Enclosure is an OUTPUT constraint, not a reason to reject the source.
        # Grow only the nominal landing where necessary before moving it. Keep
        # a DBU-sized guard against inscribed buffer chords and snapping.
        source_key=(c.key,c.indices,tuple((r['key'],r['index']) for r in records))
        prepared=self.sources.get(source_key)
        if prepared is None:
            landing=[r['poly'].buffer(p.annulus_um/math.cos(math.pi/128)+2*dbu,quad_segs=32) for r in records]
            source=shapely.union_all([c.poly,*landing])
            if source.geom_type!='Polygon' or not source.is_valid or len(source.interiors)!=len(c.poly.interiors):raise ValueError('孔盘生长改变连通拓扑，未生成不合法候选')
            inner=[max(np.linalg.norm(shapely.get_coordinates(g)-r['center'],axis=1))+2*dbu for r,g in zip(records,landing)]
            routing_records=[dict(r,poly=g) for r,g in zip(records,landing)]
            self.sources[source_key]=(source,inner,routing_records)
        else:source,inner,routing_records=prepared
        travel=max(np.linalg.norm(rigid_transform(np.array([r['center']]),self.model.bound.centers[r['owner']],states[r['owner']])[0]-r['center']) for r in records)
        scale=max(p.line_width_um*2,travel*2,5.);last='未找到满足约束的过渡';best=None
        contour=self.contours.setdefault((c.key,c.indices),{});preferred=self.preferred_span.get((c.key,c.indices),0.)
        spans=sorted(set([scale*cfg.deformation_growth**i for i in range(cfg.deformation_local_steps)]+([preferred] if preferred else [])))
        field_key=(c.key,c.indices,tuple((r['key'],r['index']) for r in records))
        def fields():
            if field_key in self.last_fields:
                import copy
                def at_pose(original):
                    q=copy.copy(original)
                    if hasattr(q,'fields'):q.fields=[at_pose(f) for f in q.fields]
                    else:q.states=states
                    return q
                field=at_pose(self.last_fields[field_key]);yield field,field.span
                if quick:return
            for span in spans:
                field=Field(records,self.model.bound.centers,states,inner,span,cfg);field.mode='local'
                if any(records[i]['owner']!=records[j]['owner'] and np.linalg.norm(np.array(records[i]['center'])-records[j]['center'])<inner[i]+inner[j]+2*span for i in range(len(records)) for j in range(i)):continue
                yield field,span
            # A pad close to a fixed terminal may have plenty of transverse
            # room but no room for a circular support disk. A directional
            # C1/C2 plateau can use the actual terminal gap without moving the
            # fixed end. The grown landing, not merely the bare via, is rigid.
            if len({r['owner'] for r in records})==1 and anchors:
                from dataclasses import replace
                from itertools import islice
                from .route_adaptation import section_candidates,SlabField
                local_key=('pad_sections',source_key)
                if local_key not in self.route_templates:
                    local_cfg=replace(cfg,deformation_min_length=.5,deformation_corner_guard=.05)
                    self.route_templates[local_key]=list(islice(section_candidates(source,routing_records,anchors,p.line_width_um,dbu,local_cfg),6))
                for option in self.route_templates[local_key]:
                    field=SlabField(records,self.model.bound.centers,states,cfg,*option);field.mode='directional_pad'
                    yield field,field.span
            if not cfg.deformation_route_enabled:return
            from .route_adaptation import routed_fields
            foreign=[]
            if snapshot is not None:
                ids,geoms,tree=snapshot[c.key.split('/')[0]]
                foreign=[geoms[j] for j in tree.query(source.buffer(travel+p.line_space_um)) if self.model.nodes[ids[j]]['net']!=c.net]
            from itertools import islice
            options=routed_fields(source,routing_records,anchors,self.model.bound.centers,states,cfg,p.line_width_um,dbu,foreign,self.route_templates)
            yield from islice(options,min(2,cfg.deformation_route_candidates) if quick else cfg.deformation_route_candidates)
        for attempt,(field,span) in enumerate(fields()):
            progress(20,f'型变搜索 · {c.key} · '+('三段追溯' if field.mode=='three_part' else '孔盘调整')+f' {attempt+1}')
            if field.jacobian_bound()>=.98 and not (hasattr(field,'injectivity_certificate') and field.injectivity_certificate(source)):
                if hasattr(field,'injectivity_certificate'):
                    field.gentle_profile=True
                    if not field.injectivity_certificate(source):last='无法证明位移场无折叠';continue
                else:last='无法证明位移场无折叠';continue
            if anchors:
                pts=shapely.get_coordinates(anchors)
                if np.max(np.linalg.norm(field(pts)-pts,axis=1),initial=0)>dbu*.01:last='过渡范围到达固定连接端';continue
            try:
                candidate=warp_polygon(source,field,tol,max(1.,min(inner)/2,p.line_width_um/2),contour)
                candidate,region=snapped(candidate,dbu)
            except ValueError as exc:last=str(exc);continue
            if candidate.geom_type!='Polygon' or not candidate.is_valid or len(candidate.interiors)!=len(c.poly.interiors):last='型变拓扑改变';continue
            support=getattr(field,'support',None)
            if support is None:support=shapely.union_all([Point(r['center']).buffer(inner[i]+span,quad_segs=32) for i,r in enumerate(records)])
            angle_bad=angle_regions(candidate,cfg.deformation_min_angle_deg,dbu)
            if not angle_bad.is_empty and support.covers(angle_bad):
                for radius in (p.line_width_um*.25,p.line_width_um*.5,p.line_width_um,2*p.line_width_um):
                    q=native_operation("round",candidate,dbu,radius)
                    delta=q.symmetric_difference(candidate)
                    local=[g for g in shapely.get_parts(delta) if support.covers(g)]
                    if not local:continue
                    q=candidate.symmetric_difference(shapely.union_all(local))
                    if q.is_valid and q.geom_type=='Polygon' and len(q.interiors)==len(c.poly.interiors) and angle_regions(q,cfg.deformation_min_angle_deg,dbu).is_empty:
                        candidate,region=snapped(q,dbu);break
            # GEOS rejects impossible envelope/anchor/clearance candidates before
            # native edge extraction. Only promising shapes pay the full DRC cost.
            bad=self.candidate_violations(c,candidate,records,anchors,states,moved_vias,snapshot,cheap=True)
            if not bad:bad=self.candidate_violations(c,candidate,records,anchors,states,moved_vias,snapshot,intrinsic_only=True)
            field.verified=not bad;field.failure_reasons=sorted({v['kind'] for v in bad});field.candidate_violations=bad
            score=(sum(v['area_um2'] for v in bad),len(bad),candidate.symmetric_difference(c.poly).area)
            if best is None or score<best[0]:best=(score,candidate,field,span)
            if not bad:
                self.preferred_span[c.key,c.indices]=span;self.last_fields[field_key]=field
                if field.mode=='three_part':field.adapted_band=warp_polygon(field.band,field,tol,max(1,p.line_width_um/2)).intersection(candidate)
                return candidate,field,span
            last='；'.join(field.failure_reasons)
        if best is not None:
            _,candidate,field,span=best;self.last_fields[field_key]=field
            bad=self.candidate_violations(c,candidate,records,anchors,states,moved_vias,snapshot)
            field.verified=not bad;field.failure_reasons=sorted({v['kind'] for v in bad});field.candidate_violations=bad
            if field.mode=='three_part':field.adapted_band=warp_polygon(field.band,field,tol,max(1,p.line_width_um/2)).intersection(candidate)
            return candidate,field,span
        raise ValueError(last+'（有限搜索未解，保留源图形）')
