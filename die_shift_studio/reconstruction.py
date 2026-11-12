
import numpy as np
import shapely
from .session_cache import SessionCache, EphemeralCache
from .preview import rigid_transform
from .drc_rules import effective_rules

AUTO_LANDING_DIAMETERS=2.5
SCOPE='名义路径上的共用A/B/C分区；失败保留原图，不代表产线签认或完整DRC。'


def transform(geometry,center,state):
    """Rigid transform of any geometry, matching how the vias are moved."""
    return shapely.set_coordinates(geometry,rigid_transform(shapely.get_coordinates(geometry),center,state))


def normalize_via_refs(vias):
    """Canonical identity, retaining legacy integer-only project rows."""
    values = tuple(vias or ())
    if values and isinstance(values[0], (tuple, list)):
        return tuple(sorted({(str(v[0]), int(v[1])) for v in values}))
    return tuple(sorted({int(v) for v in values}))


def cut_identity_matches(row_refs, piece_refs):
    row, piece = normalize_via_refs(row_refs), normalize_via_refs(piece_refs)
    if row == piece:
        return True
    return bool(row and piece and isinstance(row[0], int)
                and isinstance(piece[0], tuple)
                and row == tuple(sorted(v[1] for v in piece)))


class Reconstructor:
    def _qualified_refs(self, layer, index, refs):
        refs=normalize_via_refs(refs)
        if not refs or isinstance(refs[0],tuple):return refs
        available=self.vias_by_target.get((str(layer),int(index)),())
        resolved=[]
        for number in refs:
            matches={(str(v['key']),int(v['index'])) for v in available if v['index']==number}
            if len(matches)!=1:return ()  # Never guess an ambiguous legacy layer.
            resolved.extend(matches)
        return normalize_via_refs(resolved)

    def _matching_cut_rows(self, candidates, layer, index, chain):
        """Prefer exact qualified edits, then covers, then safe legacy rows.

        A previously merged piece can leave a covering row before a newer
        chain-specific edit. Insertion order must not hide that exact edit.
        """
        want=set(normalize_via_refs(chain))
        matches=[]
        for l,i,refs,value in candidates:
            if str(l)!=str(layer) or int(i)!=int(index):continue
            refs=normalize_via_refs(refs)
            got=set(self._qualified_refs(layer,index,refs))
            if not got or not want<=got:continue
            legacy=not isinstance(refs[0],tuple)
            matches.append(((legacy,got!=want),value))
        for _,value in sorted(matches,key=lambda match:match[0]):
            yield value

    def _cover_lookup(self,exact,rows,layer,index,chain):
        if isinstance(exact,(set,frozenset)):
            candidates=((l,i,v,True) for l,i,v in exact)
        else:
            candidates=((l,i,v,value) for (l,i,v),value in (exact or {}).items())
        return next(self._matching_cut_rows(candidates,layer,index,chain),None)

    def __init__(self,model,parameters,settings,cache_group=None,plan_pool=None):
        from .reconstruction_route import EMPTY
        self.model=model;self.p=parameters;self.settings=settings
        self.cache=SessionCache(group=cache_group)
        self.block_cache=EphemeralCache(budget=16*1024**2,max_entries=2048)
        self.tol=max(1e-6,model.data.dbu_um);self._rule_width,self.rule_space=effective_rules(parameters,settings)
        self.overlap_um=parameters.annulus_um;self.length_um=self.width_um=self.extra_um=0.
        self.vias=[];self.stays=[];self.vias_by_target={};self._plans=None
        self.plan_pool=plan_pool if plan_pool is not None else {}
        self.cuts={(str(l),int(i),normalize_via_refs(v)):(nx,ny,x,y) for l,i,v,nx,ny,x,y in settings.reconstruction_cuts}
        self.extra_cuts={(str(l),int(i),normalize_via_refs(v)):(nx,ny,x,y) for l,i,v,nx,ny,x,y in settings.reconstruction_extra_cuts}
        self.second_cuts=settings.reconstruction_second_cuts
        self.dropped={(str(l),int(i),normalize_via_refs(v)) for l,i,v in settings.reconstruction_dropped_cuts}
        for key,role in model.roles.items():
            if role not in ('运动过孔','固定过孔'):continue
            for index,poly in enumerate(model.cache.layers[key]['geometry']):
                if poly.is_empty:continue
                node=model.nodes[model.ids[key,index]];center=np.asarray(poly.centroid.coords[0],float)
                item=dict(key=key,index=index,owner=int(node['owner']),poly=poly,center=center,net=int(node['net']))
                if role=='固定过孔':self.stays.append(item);continue
                targets=model.connections.fixed_interfaces(key,index)
                if not targets:continue
                item.update(targets=targets,land=poly.buffer(self.overlap_um,quad_segs=16),land_diameter=2*np.sqrt(poly.area/np.pi)+2*self.overlap_um)
                self.vias.append(item)
                for ref in targets:self.vias_by_target.setdefault(tuple(ref),[]).append(item)

    def close(self):self.cache.close();self.block_cache.clear()

    def _own_frame(self,owner):
        # Physical chip geometry is independent of a custom rotation pivot.
        return self.model.case['dies'][owner] if 0<=owner<len(self.model.case['dies']) else None

    def _pinned_second(self,rows,layer,index,vias):
        values=((l,i,v,(x,y,nx,ny)) for l,i,v,nx,ny,x,y in rows)
        return next(self._matching_cut_rows(values,layer,index,vias),None)

    def _plans_for_all_poses(self,progress):
        from .reconstruction_route import trace,RoutePlan
        from .statistics import mean_and_bounds
        if self._plans is not None:return self._plans
        from .statistics import ExtremeCatalog
        catalogue=ExtremeCatalog(self.model.case,self.p)
        groups=[]
        for (layer,index),vias in sorted(self.vias_by_target.items()):
            for owner in sorted({v['owner'] for v in vias if v['owner']>=0}):
                mine=[v for v in vias if v['owner']==owner]
                groups.append((layer,index,owner,mine,vias))
        def build(task):
            layer,index,owner,mine,all_vias=task
            source=self.model.cache.layers[layer]['geometry'][index]
            refs=normalize_via_refs((v['key'],v['index']) for v in mine)
            net=int(self.model.nodes[self.model.ids[layer,index]]['net'])
            land=shapely.intersection(source,shapely.union_all([v['land'] for v in mine]))
            fixed=[v['poly'].buffer(self.p.annulus_um,quad_segs=16).intersection(source) for v in self.stays
                   if v['net']==net and source.intersects(v['poly'])]
            fixed.extend(v['land'].intersection(source) for v in all_vias if v['owner']!=owner)
            fixed=[g for g in fixed if not g.is_empty]
            base=dict(layer=layer,index=index,owner=owner,refs=refs,source=source,land=land,net=net,mine=mine,anchors=fixed)
            if not fixed:return dict(base,plan=None,orphan=True,warning='无固定连接端，整块按所属DIE平移')
            if land.is_empty:return dict(base,plan=None,orphan=False,warning='名义落盘无可用铜，保留原图')
            stop=min(fixed,key=lambda g:g.distance(land));end=np.asarray(stop.representative_point().coords[0])
            candidates=[]
            for via in sorted(mine,key=lambda v:-np.linalg.norm(v['center']-end))[:8]:
                line=trace(source,via['center'],end,self._rule_width)
                if line is None:continue
                plan=RoutePlan(layer,index,owner,refs,source,line,land,tuple(fixed),self.tol,self.settings.reconstruction_corner_guard_um)
                # Opposite moving terminals share a nominal middle seam. Neither
                # owner's transition may consume the other owner's half-route.
                if not any(v['net']==net and source.intersects(v['poly']) for v in self.stays):
                    plan.fixed_start=(plan.land_end+plan.fixed_start)*.5
                mu,bounds=mean_and_bounds(self.p.for_die(owner),len(self.model.bound.centers))
                radius=float(np.linalg.norm(shapely.get_coordinates(source)-self.model.bound.centers[owner],axis=1).max())
                travel=float(np.linalg.norm(np.abs(mu[:2])+bounds[:2])+2*radius*np.sin(min(np.pi,abs(mu[2])+bounds[2])/2))
                partition=plan.choose(travel)
                if partition is not None:candidates.append((not bool(plan.warning),partition['b']-partition['a'],plan))
            if not candidates:return dict(base,plan=None,orphan=False,warning='拓扑无法分出相连的动/静/平移区；保留原图')
            # The search is performed once over every local discrete extreme,
            # never against the currently selected pose. Other-DIE windows use
            # the full-input displacement envelope above; final per-pose spacing
            # is independently measured on the joint reconstructed layout.
            from .reconstruction_route import warp
            ranked=[]
            for _,length,plan in sorted(candidates,key=lambda q:q[1],reverse=True)[:3]:
                part=plan.partition(plan.nominal_first,plan.nominal_second)
                invalid=0
                for pose in catalogue.locals[owner]:
                    _,error=warp(part,plan,self.model.bound.centers[owner],pose)
                    invalid+=bool(error)
                ranked.append((invalid,-length,plan))
                if not invalid:break  # first passing candidate is the longest
            invalid,_,plan=min(ranked,key=lambda q:q[:2])
            warning=plan.warning
            if invalid:warning=f'共有{invalid}/{len(catalogue.locals[owner])}个本DIE极端不能保持合法区域；当前可用最长候选'
            return dict(base,plan=plan,orphan=False,warning=warning,extreme_checks=len(catalogue.locals[owner]),extreme_invalid=invalid)
        # Route construction is pose-independent and reused after every handle edit.
        from .session_cache import logical_fingerprint
        key=logical_fingerprint((self.model.data.geometry_id,self.model.roles,self.model.connections.order,
                                self.p.__dict__,self._rule_width,self.settings.reconstruction_corner_guard_um))
        if key not in self.plan_pool:
            from .resources import parallel_map
            self.plan_pool[key]=parallel_map(build,groups,progress=lambda n,total:progress(round(65*n/max(1,total)),f'建立全姿态共用动区 {n}/{total}'))
        self._plans=self.plan_pool[key]
        return self._plans

    def interval(self,entry):
        plan=entry['plan']
        if plan is None:return None,False
        a,b=plan.nominal_first,plan.nominal_second;manual=False
        for l,i,owner,refs,identity,left,right in self.settings.reconstruction_intervals:
            if (l,i,owner,normalize_via_refs(refs))==plan.key and identity==plan.identity:
                part=plan.partition(float(left),float(right))
                if part is not None:return part,True
        # Migrate legacy cut planes by projecting their NOMINAL points on the route.
        for which,rows in ((0,self.settings.reconstruction_cuts),(1,self.settings.reconstruction_second_cuts or self.settings.reconstruction_extra_cuts)):
            point=next(self._matching_cut_rows(((l,i,v,(x,y)) for l,i,v,nx,ny,x,y in rows),plan.layer,plan.index,plan.refs),None)
            if point is not None:
                part,_=plan.edit(a,b,which,float(plan.route.project(shapely.Point(point))))
                if part is not None:a,b=part['a'],part['b'];manual=True
        # Saved manual boxes select an interval on the trace, not a pose-local
        # floating rectangle. Static boxes can shorten but never invert it.
        for layer,bounds,role in self.settings.reconstruction_regions:
            if layer!=plan.layer:continue
            portion=plan.route.intersection(shapely.box(*bounds))
            if portion.is_empty:continue
            marks=shapely.line_locate_point(plan.route,shapely.points(shapely.get_coordinates(portion)))
            if len(marks):
                lo,hi=float(min(marks)),float(max(marks))
                if role=='move':
                    changed,_=plan.edit(a,b,1,max(b,hi))
                    if changed:a,b=changed['a'],changed['b']
                    changed,_=plan.edit(a,b,0,min(a,lo))
                else:changed,_=plan.edit(a,b,1,min(b,lo)) if lo>a else (None,True)
                if changed:a,b=changed['a'],changed['b'];manual=True
        return plan.partition(a,b),manual

    def edited(self,piece,which,nominal_point):
        """Project a mouse position to one nominal endpoint; the other is immutable."""
        from dataclasses import replace
        plan=piece.get('route_plan')
        if plan is None:return self.settings,None
        value=float(plan.route.project(shapely.Point(nominal_point)))
        part,clamped=plan.edit(piece['interval'][0],piece['interval'][1],which,value)
        if part is None:return self.settings,None
        rows=[r for r in self.settings.reconstruction_intervals if (r[0],r[1],r[2],normalize_via_refs(r[3]))!=plan.key]
        rows.append((*plan.key,plan.identity,part['a'],part['b']))
        updated=replace(self.settings,reconstruction_intervals=tuple(rows))
        return updated,dict(part,clamped=clamped)

    def frame(self,states,deformation=None,progress=lambda *_:None,quick=False,*,targets=None):
        from .reconstruction_route import warp,EMPTY
        states=np.asarray(states,float)
        key=(states.tobytes(),'shared-route-v38',None if targets is None else tuple(sorted(targets)))
        hit=self.cache.get(key)
        if hit is not None:return hit
        entries=self._plans_for_all_poses(progress);pieces=[];features={}
        selected=None if targets is None else set(map(tuple,targets))
        for number,entry in enumerate(entries):
            layer,index,owner=entry['layer'],entry['index'],entry['owner'];source=entry['source'];plan=entry['plan']
            if selected is not None and (layer,index) not in selected:continue
            if self._cover_lookup(self.dropped,None,layer,index,entry['refs']):continue
            part,manual=self.interval(entry);warning=entry['warning'];valid=True
            if part is not None:
                regions,error=warp(part,plan,self.model.bound.centers[owner],states[owner])
                if error:regions={k:part[k] for k in 'ABC'};warning=error;valid=False
                near=part['near'];moved_cut=transform(near,self.model.bound.centers[owner],states[owner]) if valid else near
                far=part['far'];a,b=part['a'],part['b']
            elif entry['orphan']:
                part=dict(A=EMPTY,B=EMPTY,C=source)
                regions=dict(A=EMPTY,B=EMPTY,C=transform(source,self.model.bound.centers[owner],states[owner]))
                near=far=moved_cut=None;a=b=0.
            else:
                part=dict(A=EMPTY,B=source,C=EMPTY);regions={k:part[k] for k in 'ABC'}
                near=far=moved_cut=None;a=b=0.;valid=False
            shift=float(np.linalg.norm(self.model.moved(shapely.Point(entry['mine'][0]['center']),owner,states).coords[0]-entry['mine'][0]['center']))
            feature=features.setdefault((layer,index),dict(source=source,pieces=[],net=entry['net']))
            piece=dict(die=owner,layer=layer,index=index,net=entry['net'],via_refs=entry['refs'],vias=[v[1] for v in entry['refs']],
                       route_plan=plan,interval=(a,b),route_identity=None if plan is None else plan.identity,
                       nominal_regions={k:part[k] for k in 'ABC'},regions=regions,
                       movable=part['C'],moved=regions['C'],fixed=regions['B'],immovable=regions['B'],bridge=regions['A'],
                       cut=near,moved_cut=moved_cut,extra_cut=far,span=part['A'],frame_seam=False,
                       whole_landing=bool(plan and a==0),chain_ends=[entry['mine'][0]['center'].tolist(),entry['anchors'][0].centroid.coords[0] if entry['anchors'] else entry['mine'][0]['center'].tolist()],
                       chain_anchor=None,seam=[None if near is None else list(near.centroid.coords[0]),None if moved_cut is None else list(moved_cut.centroid.coords[0])],
                       movable_area_um2=part['C'].area,moved_area_um2=regions['C'].area,fixed_area_um2=regions['B'].area,
                       shift_um=shift,cut_distance_um=a,chain_span_um=b-a,gap_um=None if regions['B'].is_empty or regions['C'].is_empty else float(regions['B'].distance(regions['C'])),
                       bridge_length_um=0 if near is None else near.length,bridge_area_um2=regions['A'].area,bridge_span_um=b-a,
                       clearance_um=None,conflict=False,conflict_net=None,conflict_link=None,manual=manual,manual_lost=False,
                       cut_clamped=False,cut_window=None,orphan=entry['orphan'],follows=entry['orphan'],warning=warning,valid=valid)
            pieces.append(piece);feature['pieces'].append(piece)
        out={};conflicts=0
        for (layer,index),feature in features.items():
            ps=feature['pieces'];source=feature['source']
            # Shared nets: each source fragment has one owner. Overlap between
            # independent nominal supports is ambiguous; do not double-move copper.
            occupied=EMPTY;ambiguous=False
            for q in ps:
                support=shapely.union_all([q['nominal_regions']['A'],q['nominal_regions']['C']])
                if occupied.intersection(support).area>max(self.tol**2,source.area*1e-8):ambiguous=True
                occupied=occupied.union(support)
            fixed=shapely.set_precision(source.difference(occupied),self.tol) if np.any(states) else source.difference(occupied)
            if ambiguous or any(not q['valid'] for q in ps):
                reason='多个DIE动区重叠；保留原图' if ambiguous else next(q['warning'] for q in ps if not q['valid'])
                for q in ps:q.update(valid=False,warning=reason,regions=dict(A=EMPTY,B=source,C=EMPTY),bridge=EMPTY,moved=EMPTY,fixed=source)
                geometry=source
            else:
                for q in ps:q['fixed']=fixed;q['regions']['B']=fixed;q['fixed_area_um2']=fixed.area
                geometry=shapely.union_all([fixed,*[q['regions'][k] for q in ps for k in 'AC']])
            from .reconstruction_route import parts
            if not geometry.is_valid or len(parts(geometry))!=len(parts(source)):
                geometry=source
                for q in ps:q.update(valid=False,warning='最终几何未通过连通检查；保留原图',regions=dict(A=EMPTY,B=source,C=EMPTY),bridge=EMPTY,moved=EMPTY,fixed=source)
            out.setdefault(layer,[]).append(dict(indices=(index,),geometry=geometry,net=feature['net'],movable=occupied,moved=shapely.union_all([q['moved'] for q in ps])))
        # Measure against final neighbouring copper, not source poses.
        for layer in out:
            replaced={q['indices'][0]:q['geometry'] for q in out[layer]}
            copper=[replaced.get(i,g) for i,g in enumerate(self.model.cache.layers[layer]['geometry'])]
            nets=[int(self.model.nodes[self.model.ids[layer,i]]['net']) for i in range(len(copper))]
            trees={};measures={}
            for q in (q for q in pieces if q['layer']==layer):
                net=q['net'];idx=q['index']
                if net not in trees:
                    others=[i for i,n in enumerate(nets) if n!=net]
                    trees[net]=(shapely.STRtree([copper[i] for i in others]),others)
                tree,others=trees[net]
                if not others:continue
                if idx not in measures:
                    probe=replaced[idx]
                    indices,distances=tree.query_nearest(probe,return_distance=True,all_matches=False)
                    i=others[int(indices[0])];gap=float(distances[0])
                    measures[idx]=dict(clearance_um=gap,conflict=gap<self.rule_space-self.tol,conflict_net=nets[i],
                                       conflict_link=shapely.get_coordinates(shapely.shortest_line(probe,copper[i])).tolist())
                q.update(measures[idx]);conflicts+=int(q['conflict'])
        result=dict(complete=True,pieces=pieces,replacements=out,scope='全输入位移包络下选择名义追溯动区，所有姿态共用。未通过拓扑或折叠检查时保留原图；不代表产线签认或完整DRC。',
                    parameters=dict(length_um=0.,width_um=0.,overlap_um=self.p.annulus_um,landing_diameters=AUTO_LANDING_DIAMETERS,shared_nominal=True),
                    summary=dict(pieces=len(pieces),bridges=sum(not q['bridge'].is_empty for q in pieces),orphans=sum(q['orphan'] for q in pieces),conflicts=conflicts,
                                 warnings=sum(bool(q['warning']) for q in pieces),groups_used=len(features),manual_cuts=sum(q['manual'] for q in pieces),
                                 rule_space_um=self.rule_space,movable_area_um2=sum(q['movable_area_um2'] for q in pieces),
                                 fixed_area_um2=sum(q['fixed_area_um2'] for q in pieces),cover_area_um2=sum(q['bridge_area_um2'] for q in pieces),
                                 max_shift_um=max((q['shift_um'] for q in pieces),default=0.),regions_used=len(self.settings.reconstruction_regions),regions_ambiguous=0))
        if not np.any(states) and targets is None:self.cache.pin(key)
        self.cache[key]=result;progress(100,f'重构预演：{len(pieces)} 个共用动区')
        return result
