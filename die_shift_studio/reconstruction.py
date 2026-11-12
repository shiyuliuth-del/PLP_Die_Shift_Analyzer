
import numpy as np
import shapely
from .session_cache import SessionCache, EphemeralCache
from .preview import rigid_transform
from .drc_rules import effective_rules

AUTO_LANDING_DIAMETERS=2.5
SCOPE='名义路径上的共用A/B/C分区；明确分区的连接失败时保留两侧实体并断开标红；无法确定分区时保留源图，不代表产线签认或完整DRC。'


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


def compose_feature(source,ps,tolerance,moving):
    """One assembly contract for full frames and live edits; modifies local pieces."""
    from .reconstruction_route import EMPTY
    # Shared nets: each source fragment has one owner. Overlap between
    # independent nominal supports is ambiguous; do not double-move copper.
    occupied=EMPTY;ambiguous=False;by_owner={}
    for q in ps:
        support=shapely.union_all([q['nominal_regions']['A'],q['nominal_regions']['C']])
        if any(owner!=q['die'] and other.intersection(support).area>max(tolerance**2,source.area*1e-8) for owner,other in by_owner.items()):ambiguous=True
        by_owner[q['die']]=by_owner.get(q['die'],EMPTY).union(support)
        occupied=occupied.union(support)
    fixed=ps[0]['regions']['B'] if len(ps)==1 else (shapely.set_precision(source.difference(occupied),tolerance) if moving else source.difference(occupied))
    if ambiguous:
        reason='多个DIE动区归属重叠；无法确定分区，保留源图'
        for q in ps:q.update(valid=False,warning=reason,regions=dict(A=EMPTY,B=source,C=EMPTY),bridge=EMPTY,moved=EMPTY,fixed=source)
        geometry=source
    else:
        for q in ps:q['fixed']=fixed;q['regions']['B']=fixed;q['fixed_area_um2']=fixed.area
        geometry=shapely.union_all([fixed,*[q['regions'][k] for q in ps for k in 'AC']])
    from .reconstruction_route import parts
    mismatch=(len(parts(geometry))!=len(parts(source)) or sum(len(q.interiors) for q in parts(geometry))!=sum(len(q.interiors) for q in parts(source)))
    if not geometry.is_valid or (mismatch and not any(q.get('connection_failed') for q in ps)):
        for q in ps:
            if q.get('fallback_regions') is not None:
                regions=dict(q['fallback_regions']);regions['B']=fixed
                q.update(valid=False,connection_failed=True,warning='组合连接发生冲突，已取消连接段',regions=regions,bridge=regions['A'],moved=regions['C'],fixed=fixed)
        geometry=shapely.union_all([fixed,*[q['regions'][k] for q in ps for k in 'AC']])
        if not geometry.is_valid:
            geometry=source
            for q in ps:q.update(valid=False,warning='无法生成有效分区，保留源图供核对',regions=dict(A=EMPTY,B=source,C=EMPTY),bridge=EMPTY,moved=EMPTY,fixed=source)
    return geometry,occupied


def decorate_problems(data):
    """Selectable red warning areas are annotations, never replacement copper."""
    for q in data.get('pieces',()):
        own=(q['layer'].split('/')[0],q['net'])
        support=shapely.union_all([q['regions']['A'],q['regions']['C'],q['nominal_regions']['B'],q.get('moving_terminals',shapely.Polygon())])
        kinds={v['kind'] for v in data.get('violations',())
               if (v['net']==own or own in v['net']) and v['geometry'].intersects(support)}
        q['rule_violations']=tuple(sorted(kinds))
        reasons=list(q['rule_violations'])
        if not q['valid']:reasons.insert(0,q.get('warning') or '当前未生成有效连接')
        q['problem_reasons']=tuple(dict.fromkeys(reasons))
        if reasons:
            q['problem_region']=q['missing_region'] if q.get('connection_failed') else q['regions']['A']
            if q['problem_region'].is_empty:q['problem_region']=q.get('missing_region',q['source'])
        else:q['problem_region']=shapely.Polygon()


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
        from .reconstruction_rules import FixedRules
        self.rules=FixedRules(model,parameters,settings)
        self.cache=SessionCache(group=cache_group)
        self.block_cache=EphemeralCache(budget=16*1024**2,max_entries=2048)
        self.tol=max(1e-6,model.data.dbu_um);self._rule_width,self.rule_space=effective_rules(parameters,settings)
        self.overlap_um=parameters.annulus_um;self.length_um=self.width_um=self.extra_um=0.
        self.vias=[];self.stays=[];self.vias_by_target={};self._plans=None;self.manual_quality={};self.manual_masses={}
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

    def close(self):
        self.cache.close();self.block_cache.clear()
        for entry in self._plans or ():
            plan=entry.get('plan')
            if plan is not None:plan.cache.clear();plan.pose_memo.clear();plan.memo_bytes=0


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
        from .width_grid import WidthGrid
        from dataclasses import replace
        probability_parameters=replace(self.p,target_nsigma=0)
        priority_grid=WidthGrid(probability_parameters,len(self.model.bound.centers),3)
        groups=[]
        for (layer,index),vias in sorted(self.vias_by_target.items()):
            for owner in sorted({v['owner'] for v in vias if v['owner']>=0}):
                mine=[v for v in vias if v['owner']==owner]
                groups.append((layer,index,owner,mine,vias))
        # Buffered foreign copper contains every possible position in the full
        # independent multi-DIE input box. It is a conservative feasibility check,
        # not a claim to have finitely enumerated a continuous distribution.
        envelopes={}
        for layer,role in self.model.roles.items():
            if role!='固定RDL':continue
            values=[];nets=[]
            for index,source in enumerate(self.model.cache.layers[layer]['geometry']):
                radius=0.
                for owner in {v['owner'] for v in self.vias_by_target.get((layer,index),()) if v['owner']>=0}:
                    mu,b=mean_and_bounds(self.p.for_die(owner),len(self.model.bound.centers))
                    r=float(np.max(np.linalg.norm(shapely.get_coordinates(source)-self.model.bound.centers[owner],axis=1)))
                    radius=max(radius,float(np.linalg.norm(abs(mu[:2])+b[:2])+2*r*np.sin(min(np.pi,abs(mu[2])+b[2])/2)))
                values.append(source.buffer((radius+2*self.tol)/np.cos(np.pi/48),quad_segs=12) if radius else source)
                nets.append(int(self.model.nodes[self.model.ids[layer,index]]['net']))
            envelopes[layer]=(shapely.STRtree(values),nets)
        self._envelopes=envelopes
        from .resources import resource_profile
        memo_budget=max(128*1024,min(2*1024**2,int(resource_profile()['available_memory_mb']*1024**2*.08/max(1,len(groups)))))
        def build(task):
            layer,index,owner,mine,all_vias=task
            source=self.model.cache.layers[layer]['geometry'][index]
            refs=normalize_via_refs((v['key'],v['index']) for v in mine)
            net=int(self.model.nodes[self.model.ids[layer,index]]['net'])
            from .landing_geometry import embedded_pad
            landing=lambda v:embedded_pad(v['poly'],source,v['poly'].buffer(self.p.annulus_um,quad_segs=32),self.tol).intersection(source) if source.geom_type=='Polygon' else v['poly'].buffer(self.p.annulus_um,quad_segs=32).intersection(source)
            land=shapely.union_all([landing(v) for v in mine])
            fixed=[landing(v) for v in self.stays
                   if v['net']==net and source.intersects(v['poly'])]
            fixed.extend(landing(v) for v in all_vias if v['owner']!=owner)
            fixed=[g for g in fixed if not g.is_empty]
            base=dict(layer=layer,index=index,owner=owner,refs=refs,source=source,land=land,net=net,mine=mine,anchors=fixed)
            def build_source(source,land,fixed):
                local_base=dict(base,land=land,anchors=fixed)
                if not fixed:return dict(local_base,plan=None,orphan=True,warning='无固定连接端，整块按所属DIE平移')
                if land.is_empty:return dict(local_base,plan=None,orphan=False,warning='名义落盘无可用铜，保留原图')
                stop=min(fixed,key=lambda g:g.distance(land));end=np.asarray(stop.representative_point().coords[0])
                candidates=[]
                for via in sorted(mine,key=lambda v:-np.linalg.norm(v['center']-end))[:8]:
                    line=trace(source,via['center'],end,self._rule_width)
                    if line is None:continue
                    plan=RoutePlan(layer,index,owner,refs,source,line,land,tuple(fixed),self.tol,self.settings.reconstruction_corner_guard_um,memo_budget=memo_budget)
                    # Opposite moving terminals share a nominal middle seam. Neither
                    # owner's transition may consume the other owner's half-route.
                    if not any(v['net']==net and source.intersects(v['poly']) for v in self.stays):
                        plan.fixed_start=(plan.land_end+plan.fixed_start)*.5
                    mu,bounds=mean_and_bounds(self.p.for_die(owner),len(self.model.bound.centers))
                    radius=float(np.linalg.norm(shapely.get_coordinates(source)-self.model.bound.centers[owner],axis=1).max())
                    travel=float(np.linalg.norm(np.abs(mu[:2])+bounds[:2])+2*radius*np.sin(min(np.pi,abs(mu[2])+bounds[2])/2))
                    partition=plan.choose(travel)
                    if partition is not None:candidates.append((not bool(plan.warning),partition['b']-partition['a'],plan))
                if not candidates:return dict(local_base,plan=None,orphan=False,warning='拓扑无法分出相连的动/静/平移区；保留原图')
                # The search is performed once over every local discrete extreme,
                # never against the currently selected pose. Other-DIE windows use
                # the full-input displacement envelope above; final per-pose spacing
                # is independently measured on the joint reconstructed layout.
                from .reconstruction_route import warp
                ranked=[];best_mass=float('inf')
                trials=[]
                for _,length,plan in candidates:
                    for a,b in getattr(plan,'candidate_intervals',((plan.nominal_first,plan.nominal_second),))[:3]:
                        trials.append((b-a,plan,a,b))
                for length,plan,a,b in sorted(trials,key=lambda q:q[0],reverse=True)[:3]:
                    part=plan.partition(a,b)
                    invalid=0
                    for pose in catalogue.locals[owner]:
                        _,error=warp(part,plan,self.model.bound.centers[owner],pose,self.settings.reconstruction_style)
                        invalid+=bool(error)
                    mass,_=self.rules.candidate_mass(plan,part,priority_grid,self.settings.reconstruction_style,best_mass)
                    tree,nets=envelopes[layer]
                    sweep=shapely.union_all([part['A'],part['C']]).buffer((travel+self.rule_space+2*self.tol)/np.cos(np.pi/48),quad_segs=12)
                    neighbours=tree.query(sweep,predicate='intersects')
                    certified=not any(nets[int(j)]!=net for j in neighbours)
                    ranked.append((mass,invalid,-length,not certified,plan,a,b))
                    best_mass=min(best_mass,mass)
                    if mass==0 and not invalid:break  # first zero-risk candidate is already longest
                mass,invalid,_,space_unproven,plan,a,b=min(ranked,key=lambda q:q[:4])
                plan.nominal_first=a;plan.nominal_second=b
                warning=plan.warning
                if space_unproven:warning=(warning+'；' if warning else '')+'全输入多DIE包络未证明线距充足，按当前几何复核'
                if invalid:warning=f'共有{invalid}/{len(catalogue.locals[owner])}个本DIE极端不能保持合法区域；当前可用最长候选'
                return dict(local_base,plan=plan,orphan=False,warning=warning,extreme_checks=len(catalogue.locals[owner]),extreme_invalid=invalid,all_die_space_certified=not space_unproven,priority_failure_mass=mass)
            from .reconstruction_route import branch_sources
            branches=branch_sources(source,land,fixed,mine[0]['center'],self._rule_width,self.tol,self.settings.reconstruction_corner_guard_um) if len({v['owner'] for v in all_vias})==1 else None
            if branches:
                return [dict(build_source(g,common,[anchor]),branch=i+1,branch_count=len(branches)) for i,(g,common,anchor) in enumerate(branches)]
            return [build_source(source,land,fixed)]
        # Route construction is pose-independent and reused after every handle edit.
        from .session_cache import logical_fingerprint
        key=logical_fingerprint((self.model.data.geometry_id,self.model.roles,self.model.connections.order,
                                probability_parameters.__dict__,self._rule_width,self.settings.reconstruction_corner_guard_um,self.settings.reconstruction_style))
        if key not in self.plan_pool:
            from .resources import parallel_map
            self.plan_pool[key]=[entry for rows in parallel_map(build,groups,progress=lambda n,total:progress(round(65*n/max(1,total)),f'建立全姿态共用动区 {n}/{total}')) for entry in rows]
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

    def _manual_warning(self,entry,part):
        plan=entry['plan'];key=(plan.key,plan.identity,part['a'],part['b'])
        if key in self.manual_quality:return self.manual_quality[key]
        from .statistics import ExtremeCatalog,mean_and_bounds
        from .reconstruction_route import warp
        poses=ExtremeCatalog(self.model.case,self.p).locals[plan.owner]
        invalid=sum(warp(part,plan,self.model.bound.centers[plan.owner],pose,self.settings.reconstruction_style)[1] is not None for pose in poses)
        mu,b=mean_and_bounds(self.p.for_die(plan.owner),len(self.model.bound.centers))
        support=shapely.union_all([part['A'],part['C']]);radius=np.linalg.norm(shapely.get_coordinates(support)-self.model.bound.centers[plan.owner],axis=1).max()
        travel=np.linalg.norm(abs(mu[:2])+b[:2])+2*radius*np.sin(min(np.pi,abs(mu[2])+b[2])/2)
        sweep=support.buffer((travel+self.rule_space+2*self.tol)/np.cos(np.pi/48),quad_segs=12)
        tree,nets=self._envelopes[plan.layer]
        unproven=any(nets[int(i)]!=entry['net'] for i in tree.query(sweep,predicate='intersects'))
        text=f'手动动区在{invalid}/{len(poses)}个本DIE极端不能保持合法区域' if invalid else ''
        if unproven:text+=('；' if text else '')+'全输入多DIE包络未证明线距充足，按当前几何复核'
        from dataclasses import replace
        from .width_grid import WidthGrid
        if not hasattr(self,'priority_grid'):self.priority_grid=WidthGrid(replace(self.p,target_nsigma=0),len(self.model.bound.centers),3)
        mass,_=self.rules.candidate_mass(plan,part,self.priority_grid,self.settings.reconstruction_style)
        self.manual_masses[key]=mass
        if mass:text+=('；' if text else '')+f'局部规则失败质量约{mass*100:.3g}%（有限网格，整包结果见重构良率）'
        self.manual_quality[key]=text;return text

    def edited(self,piece,which,nominal_point):
        """Project a mouse position to one nominal endpoint; the other is immutable."""
        from dataclasses import replace
        plan=piece.get('route_plan')
        if plan is None:return self.settings,None
        value=float(plan.route.project(shapely.Point(nominal_point)))
        part,clamped=plan.edit(piece['interval'][0],piece['interval'][1],which,value)
        if part is None:return self.settings,None
        rows=[r for r in self.settings.reconstruction_intervals if (r[0],r[1],r[2],normalize_via_refs(r[3]),r[4])!=(*plan.key,plan.identity)]
        rows.append((*plan.key,plan.identity,part['a'],part['b']))
        updated=replace(self.settings,reconstruction_intervals=tuple(rows))
        return updated,dict(part,clamped=clamped)

    def frame(self,states,deformation=None,progress=lambda *_:None,quick=False,*,targets=None,diagnostics=True):
        from .reconstruction_route import warp,EMPTY,failed_connection,missing_area
        states=np.asarray(states,float)
        key=(states.tobytes(),'shared-route-v39-branch2',bool(diagnostics),None if targets is None else tuple(sorted(targets)))
        hit=self.cache.get(key) if diagnostics else None
        if hit is not None:return hit
        entries=self._plans_for_all_poses(progress);pieces=[];features={}
        selected=None if targets is None else set(map(tuple,targets))
        if selected is not None:entries=[q for q in entries if (q['layer'],q['index']) in selected]
        # Independent source features share no mutable geometry. GEOS releases
        # the GIL for warp/validation, so larger frames use the existing hardware
        # admission policy rather than serially processing every route.
        def render(entry):
            if selected is not None and (entry['layer'],entry['index']) not in selected:return None
            part,manual=self.interval(entry)
            if part is None:return part,manual,None,None,entry['warning']
            regions,error,quality=self.rules.best_regions(entry['plan'],part,self.model.bound.centers[entry['owner']],states[entry['owner']],self.settings.reconstruction_style)
            warning=self._manual_warning(entry,part) if manual else entry['warning']
            return part,manual,regions,error,warning
        from .resources import parallel_map
        rendered=parallel_map(render,entries) if len(entries)>16 else list(map(render,entries))
        for number,(entry,prepared) in enumerate(zip(entries,rendered)):

            layer,index,owner=entry['layer'],entry['index'],entry['owner'];source=entry['source'];plan=entry['plan']
            if selected is not None and (layer,index) not in selected:continue
            if self._cover_lookup(self.dropped,None,layer,index,entry['refs']):continue
            part,manual,regions,error,warning=prepared;valid=True;fallback=None
            if part is not None:
                fallback=failed_connection(part,plan,self.model.bound.centers[owner],states[owner])
                if error:regions=dict(fallback);warning=error+'；已取消连接段';valid=False
                near=part['near'];moved_cut=transform(near,self.model.bound.centers[owner],states[owner])
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
                       fallback_regions=fallback if part.get('near') is not None else None,connection_failed=bool(error),
                       missing_region=missing_area(part,plan,self.model.bound.centers[owner],states[owner]) if plan is not None and part.get('near') is not None else source,
                       moving_terminals=shapely.union_all([self.model.moved(v['poly'],owner,states) for v in entry['mine']]) if diagnostics else EMPTY,
                       source=source,branch=entry.get('branch',1),branch_count=entry.get('branch_count',1),route_plan=plan,interval=(a,b),route_identity=None if plan is None else plan.identity,
                       nominal_regions={k:part[k] for k in 'ABC'},regions=regions,
                       movable=part['C'],moved=regions['C'],fixed=regions['B'],immovable=regions['B'],bridge=regions['A'],
                       cut=near,moved_cut=moved_cut,extra_cut=far,span=part['A'],frame_seam=False,
                       whole_landing=bool(plan and a==0),whole_fixed_landing=bool(far is not None and far.geom_type=='Point'),chain_ends=[entry['mine'][0]['center'].tolist(),entry['anchors'][0].centroid.coords[0] if entry['anchors'] else entry['mine'][0]['center'].tolist()],
                       chain_anchor=None,seam=[None if near is None else list(near.centroid.coords[0]),None if moved_cut is None else list(moved_cut.centroid.coords[0])],
                       movable_area_um2=part['C'].area,moved_area_um2=regions['C'].area,fixed_area_um2=regions['B'].area,
                       shift_um=shift,cut_distance_um=a,chain_span_um=b-a,gap_um=None if regions['B'].is_empty or regions['C'].is_empty else float(regions['B'].distance(regions['C'])),
                       bridge_length_um=0 if near is None else near.length,bridge_area_um2=regions['A'].area,bridge_span_um=b-a,
                       clearance_um=None,conflict=False,conflict_net=None,conflict_link=None,manual=manual,manual_lost=False,
                       cut_clamped=False,cut_window=None,orphan=entry['orphan'],follows=entry['orphan'],warning=warning,valid=valid,priority_failure_mass=self.manual_masses.get((plan.key,plan.identity,a,b),entry.get('priority_failure_mass')) if plan else None)
            pieces.append(piece);feature['pieces'].append(piece)
        out={};conflicts=0
        for (layer,index),feature in features.items():
            ps=feature['pieces'];source=feature['source']
            geometry,occupied=compose_feature(source,ps,self.tol,bool(np.any(states)) or self.settings.reconstruction_style in ('linear','arc'))
            out.setdefault(layer,[]).append(dict(indices=(index,),geometry=geometry,net=feature['net'],movable=occupied,moved=shapely.union_all([q['moved'] for q in ps])))
        # Measure against final neighbouring copper, not source poses.
        for layer in (out if diagnostics else ()):
            replaced={q['indices'][0]:q['geometry'] for q in out[layer]}
            copper=[replaced.get(i,g) for i,g in enumerate(self.model.cache.layers[layer]['geometry'])]
            nets=[int(self.model.nodes[self.model.ids[layer,i]]['net']) for i in range(len(copper))]
            trees={}
            layer_pieces=[q for q in pieces if q['layer']==layer]
            for net in {q['net'] for q in layer_pieces}:
                others=[i for i,n in enumerate(nets) if n!=net]
                trees[net]=(shapely.STRtree([copper[i] for i in others]),others)
            def measure(item):
                idx,net=item;tree,others=trees[net]
                if not others:return idx,None
                probe=replaced[idx]
                # Actual overlaps need no nearest-boundary search. Preparing the
                # one probe amortises the batched predicate over candidate nets.
                nearby=tree.query(probe)
                shapely.prepare(probe)
                hit=nearby[shapely.intersects(probe,tree.geometries[nearby])]
                if len(hit):i=others[int(hit[0])];gap=0.
                else:
                    indices,distances=tree.query_nearest(probe,return_distance=True,all_matches=False)
                    i=others[int(indices[0])];gap=float(distances[0])
                return idx,dict(clearance_um=gap,conflict=gap<self.rule_space-self.tol,conflict_net=nets[i],
                                conflict_link=shapely.get_coordinates(shapely.shortest_line(probe,copper[i])).tolist())
            measures=dict(parallel_map(measure,sorted({(q['index'],q['net']) for q in layer_pieces})))
            for q in layer_pieces:
                if measures[q['index']] is not None:q.update(measures[q['index']]);conflicts+=int(q['conflict'])
        result=dict(complete=True,pieces=pieces,replacements=out,scope='共同端点按全输入概率参考选择；保持所选样式比较有限候选。未形成有效连接时取消连接段并保留两端；实际工况规则另检。不代表全局最优布线或产线签认。',
                    parameters=dict(length_um=0.,width_um=0.,overlap_um=self.p.annulus_um,landing_diameters=AUTO_LANDING_DIAMETERS,shared_nominal=True,connection_style=self.settings.reconstruction_style),
                    summary=dict(pieces=len(pieces),bridges=sum(not q['bridge'].is_empty for q in pieces),orphans=sum(q['orphan'] for q in pieces),conflicts=conflicts,
                                 warnings=sum(bool(q['warning']) for q in pieces),groups_used=len(features),manual_cuts=sum(q['manual'] for q in pieces),
                                 rule_space_um=self.rule_space,movable_area_um2=sum(q['movable_area_um2'] for q in pieces),
                                 fixed_area_um2=sum(q['fixed_area_um2'] for q in pieces),cover_area_um2=sum(q['bridge_area_um2'] for q in pieces),
                                 max_shift_um=max((q['shift_um'] for q in pieces),default=0.),regions_used=len(self.settings.reconstruction_regions),regions_ambiguous=0))
        result['violations']=self.rules.check(out,states,progress) if diagnostics else []
        result['constraints_complete']=bool(diagnostics)
        decorate_problems(result)
        if diagnostics:
            if not np.any(states) and targets is None:self.cache.pin(key)
            self.cache[key]=result
        progress(100,f'重构预演：{len(pieces)} 个共用动区')
        return result
