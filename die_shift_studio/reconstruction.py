
import numpy as np
import shapely
from .session_cache import SessionCache, EphemeralCache
from .preview import rigid_transform
from .drc_rules import effective_rules
from .resources import cancel_check,progress_slice

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


def _posed_failure_geometry(ps,source,tolerance):
    """Keep rigid copper in its posed or fixed position when the zone fails.

    Only the transition (A) is omitted and drawn as a dashed warning; the
    moving terminal side must still follow its DIE and the fixed side stays put
    instead of freezing the whole source polygon at the nominal position.
    """
    from .reconstruction_route import EMPTY
    for q in ps:
        regions=q.get('regions') or {}
        fixed=regions.get('B');moved=regions.get('C')
        fixed=EMPTY if fixed is None else fixed
        moved=EMPTY if moved is None else moved
        q.update(regions=dict(A=EMPTY,B=fixed,C=moved),bridge=EMPTY,moved=moved,fixed=fixed,
                 bridge_area_um2=0.,moved_area_um2=moved.area,fixed_area_um2=fixed.area)
    geoms=[q['regions'][k] for q in ps for k in 'BC' if not q['regions'][k].is_empty]
    geometry=shapely.union_all(geoms) if geoms else source
    if not geometry.is_valid:geometry=shapely.make_valid(geometry)
    polys=[g for g in shapely.get_parts(geometry) if g.geom_type=='Polygon']
    return shapely.union_all(polys) if polys else source


def _compose_feature(source,ps,tolerance,moving):
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
    # Never round one side of a seam independently. That creates artificial
    # disconnections when its matching transition retains source coordinates.
    fixed=ps[0]['regions']['B'] if len(ps)==1 and ps[0].get('source_scope_complete',True) else source.difference(occupied)
    if ambiguous:
        reason='多个DIE动区归属重叠；无法确定分区，保留源图'
        for q in ps:q.update(valid=False,connection_failed=True,warning=reason)
        geometry=_posed_failure_geometry(ps,source,tolerance)
    else:
        graph=any(q.get('graph_members') for q in ps)
        if moving and graph:
            # Quantize every physical branch on the same layout grid before
            # union. Keep the drawn A/B/C and the exported material consistent.
            fixed=shapely.set_precision(fixed,tolerance)
            for q in ps:
                for tag,geometry_key,area_key in (('A','bridge','bridge_area_um2'),('C','moved','moved_area_um2')):
                    material=shapely.set_precision(q['regions'][tag],tolerance)
                    q['regions'][tag]=q[geometry_key]=material
                    q[area_key]=material.area
            from .topology_checks import BRANCH_CONTACT,branch_cells,branch_contacts
            contacts=branch_contacts(branch_cells(ps),tolerance)
            if contacts:
                contact_regions={}
                rigid_collision=False
                for i,j,witness in contacts:
                    if i is None and j is None:
                        rigid_collision=True
                        i=min(range(len(ps)),key=lambda k:ps[k]['nominal_regions']['A'].distance(witness))
                    for row in (i,j):
                        if row is not None:contact_regions.setdefault(row,[]).append(witness)
                for row,witnesses in contact_regions.items():
                    q=ps[row];witness=shapely.union_all(witnesses)
                    q.update(branch_conflict=witness,connection_failed=True,valid=False,warning=BRANCH_CONTACT,
                             bridge=EMPTY,bridge_area_um2=0.)
                    q['regions']['A']=EMPTY
                if rigid_collision:
                    # Overlapping rigid pieces cannot both be drawn as certified
                    # copper. Keep each side in its real pose for diagnosis and
                    # mark the transition only; never freeze moving copper.
                    for q in ps:q.update(branch_rigid_unresolved=True)
                    return _posed_failure_geometry(ps,source,tolerance),occupied
        for q in ps:q['fixed']=fixed;q['regions']['B']=fixed;q['fixed_area_um2']=fixed.area
        geometry=shapely.union_all([fixed,*[q['regions'][k] for q in ps for k in 'AC']],grid_size=tolerance if moving else None)
    # With no material motion and a source-preserving style, partition overlay
    # round-off must neither create holes nor invent a failed connector.
    if not moving:return source,occupied
    from .reconstruction_route import parts
    # A formerly solid source has no legitimate hole to preserve here. Joint
    # DBU rounding can leave sub-grid closed seam slivers; fill ONLY those thin
    # derived slivers, not real channels, imported holes or disconnected gaps.
    if all(not p.interiors for p in parts(source)):
        repairs=[]
        for p in parts(geometry):
            for ring in p.interiors:
                hole=shapely.Polygon(ring)
                if hole.area>tolerance*hole.length:continue
                rect=shapely.get_coordinates(shapely.minimum_rotated_rectangle(hole))
                if len(rect)<4 or min(np.linalg.norm(np.diff(rect,axis=0),axis=1))>tolerance*2:continue
                repairs.append(hole)
                owner=min(ps,key=lambda q:q['regions']['A'].distance(hole) if not q['regions']['A'].is_empty else float('inf'))
                owner['regions']['A']=owner['regions']['A'].union(hole);owner['bridge']=owner['regions']['A']
                owner['seam_repair_area_um2']=owner.get('seam_repair_area_um2',0.)+hole.area
                owner['bridge_area_um2']=owner['bridge'].area
        if repairs:geometry=shapely.union_all([fixed,*[q['regions'][k] for q in ps for k in 'AC']])
    mismatch=(len(parts(geometry))!=len(parts(source)) or sum(len(q.interiors) for q in parts(geometry))!=sum(len(q.interiors) for q in parts(source)))
    if mismatch and all(q['regions']['A'].is_empty for q in ps):
        for q in ps:q.update(valid=False,connection_failed=True,warning='独立刚体分支已断开；当前没有可适配动区')
    if not geometry.is_valid:
        for q in ps:
            if q.get('fallback_regions') is not None:
                regions=dict(q['fallback_regions']);regions['B']=fixed
                q.update(valid=False,connection_failed=True,warning='组合连接发生冲突，已取消连接段',regions=regions,bridge=regions['A'],moved=regions['C'],fixed=fixed)
            else:
                q.update(valid=False,connection_failed=True,warning='随动分支连接关系已改变；保留各分支实体并标记')
        geometry=_posed_failure_geometry(ps,source,tolerance)
    return geometry,occupied


def compose_feature(source,ps,tolerance,moving):
    """A failed numerical overlay cannot erase copper or publish a success."""
    try:
        result=_compose_feature(source,ps,tolerance,moving)
        if not result[0].is_empty or source.is_empty:return result
        reason='型变后没有有效铜；保留原铜并标记失败'
    except shapely.errors.GEOSException as exc:reason='型变布尔运算未通过；保留原铜：'+str(exc)
    for q in ps:q.update(valid=False,connection_failed=True,warning=reason)
    return _posed_failure_geometry(ps,source,tolerance),shapely.Polygon()


def feature_moves(pieces,states,style):
    """Quantisation may depend only on owners that actually alter this source."""
    return any(np.any(states[q['die']]) and
        any(not q['nominal_regions'][k].is_empty for k in 'AC') for q in pieces)


def decorate_problems(data):
    from .reconstruction_events import associate,active_region,zone_key
    pieces=data.get('pieces',());found=associate(pieces,[*data.get('violations',()),*data.get('original_violations',())],data.get('dbu_um',.001))
    for q in pieces:
        rows=found[zone_key(q)];q['rule_witnesses']=tuple(rows)
        q['rule_violations']=tuple(sorted({v['kind'] for v in rows if not v.get('connection')}))
        q['problem_reasons']=tuple(dict.fromkeys(v['kind'] for v in rows))
        q['problem_region']=active_region(q) if rows else shapely.Polygon()


_PRIORITY_GRID_CACHE={}


def _cached_priority_grid(params,die_count):
    from .width_grid import WidthGrid
    from dataclasses import asdict
    from .session_cache import logical_fingerprint
    key=(logical_fingerprint(asdict(params)),int(die_count))
    grid=_PRIORITY_GRID_CACHE.get(key)
    if grid is None:
        grid=WidthGrid(params,die_count,3)
        if len(_PRIORITY_GRID_CACHE)>32:_PRIORITY_GRID_CACHE.clear()
        _PRIORITY_GRID_CACHE[key]=grid
    return grid


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

    def __init__(self,model,parameters,settings,cache_group=None,plan_pool=None,geometry_pool=None,*,reference=None):
        from .reconstruction_route import EMPTY
        self.model=model;self.p=parameters;self.settings=settings
        from .reconstruction_rules import FixedRules
        if reference is None:
            self.rules=FixedRules(model,parameters,settings)
        else:
            # Geometry and nominal groups are immutable. Worker-local caches
            # and every rule consumer still use this frame's settings.
            from copy import copy
            self.rules=copy(reference.rules)
            self.rules.cfg=settings;self.rules.contexts={};self.rules.hard_memo={}
        self.cache=SessionCache(group=cache_group)
        self.block_cache=EphemeralCache(budget=16*1024**2,max_entries=2048)
        self.tol=max(1e-6,model.data.dbu_um)
        from .drc_rules import construction_rules
        self._rule_width,self.rule_space=construction_rules(parameters,settings,self.tol)
        self.overlap_um=parameters.annulus_um;self.length_um=self.width_um=self.extra_um=0.
        from .statistics import mean_and_bounds
        die_count=len(model.bound.centers)
        self.active_dies=frozenset(owner for owner in range(die_count)
                                   if np.any(mean_and_bounds(parameters.for_die(owner),die_count)[1]>0.))
        self.auto_ignored={};self.auto_following=set();self.safe_vias={};self.die_internal={}
        self.vias=[];self.stays=[];self.vias_by_target={};self._plans=None;self.manual_quality={};self.manual_masses={}
        self.plan_pool=plan_pool if plan_pool is not None else {}
        self._last_raw_frames={}
        self._nominal_intervals={}
        from .resources import resource_profile
        geometry_mb=max(4,min(512,resource_profile()['memory_budget_mb']//24))
        self.geometry_pool=geometry_pool if geometry_pool is not None else EphemeralCache(
            budget=geometry_mb*1024**2,max_entries=max(4096,128*geometry_mb))
        from .manual_regions import ordered_boxes_by_source
        box_fields=('reconstruction_boxes','reconstruction_angle_boxes','reconstruction_box_order','reconstruction_box_starts')
        use_reference=reference is not None and all(getattr(settings,k)==getattr(reference.settings,k) for k in box_fields)
        self.ordered_source_boxes=reference.ordered_source_boxes if use_reference else ordered_boxes_by_source(model,settings)
        excluded=set(settings.reconstruction_box_excluded)
        excluded.update(set(settings.reconstruction_ignored)&set(settings.reconstruction_following))
        self.box_refs=self.ordered_source_boxes.keys()-excluded
        self.box_partitions={};self._entry_catalogue=None;self._entries_by_ref={}

        self.cuts={(str(l),int(i),normalize_via_refs(v)):(nx,ny,x,y) for l,i,v,nx,ny,x,y in settings.reconstruction_cuts}
        self.extra_cuts={(str(l),int(i),normalize_via_refs(v)):(nx,ny,x,y) for l,i,v,nx,ny,x,y in settings.reconstruction_extra_cuts}
        self.second_cuts=settings.reconstruction_second_cuts
        self.dropped={(str(l),int(i),normalize_via_refs(v)) for l,i,v in settings.reconstruction_dropped_cuts}
        if reference is not None:
            self.vias=reference.vias;self.stays=reference.stays;self.vias_by_target=reference.vias_by_target.copy()
            if reference.rules.identity==self.rules.identity and reference.rules._nominal_rows is not None:
                self.rules._nominal_rows=reference.rules._nominal_rows
            return
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
        from .resources import parallel_map
        from .reconstruction_route import trace,RoutePlan
        from .statistics import mean_and_bounds
        if self._plans is not None:return self._plans
        from .statistics import ExtremeCatalog
        catalogue=ExtremeCatalog(self.model.case,self.p)
        from dataclasses import replace
        probability_parameters=replace(self.p,target_nsigma=0)
        priority_grid=None if getattr(self,'measured_only',False) else _cached_priority_grid(probability_parameters,len(self.model.bound.centers))
        local_poses=catalogue.locals
        if priority_grid is not None and self.settings.evaluation_scope=='risk':
            priority_grid,local_poses=self.risk_priority(catalogue)
        from .reconstruction_enclosure import enclosure_certificate
        from .reconstruction_route import trace as _trace_route
        def _mst_max(geoms):
            if len(geoms)<2:return 0.
            rem=set(range(1,len(geoms)));worst=0.
            dists={j:geoms[0].distance(geoms[j]) for j in rem}
            while rem:
                nxt=min(rem,key=dists.__getitem__)
                worst=max(worst,dists[nxt]);rem.remove(nxt)
                for j in rem:
                    d=geoms[nxt].distance(geoms[j])
                    if d<dists[j]:dists[j]=d
            return worst
        def _side_clustered(source_poly,polys,pt_a,pt_b,min_cross):
            if len(polys)<2:return True
            cross_line=_trace_route(source_poly,pt_a,pt_b,self._rule_width)
            cross_len=cross_line.length if cross_line is not None else min_cross
            rem=set(range(1,len(polys)))
            dists={j:polys[0].distance(polys[j]) for j in rem}
            prev={j:0 for j in rem}
            buf=source_poly.buffer(self.tol)
            while rem:
                nxt=min(rem,key=dists.__getitem__)
                u,v=polys[prev[nxt]],polys[nxt]
                if not buf.covers(shapely.shortest_line(u,v)):
                    t=_trace_route(source_poly,np.asarray(u.centroid.coords[0]),np.asarray(v.centroid.coords[0]),self._rule_width)
                    if t is None or t.length>=cross_len*.5:return False
                rem.remove(nxt)
                for j in rem:
                    d=polys[nxt].distance(polys[j])
                    if d<dists[j]:dists[j]=d;prev[j]=nxt
            return True
        paired={};paired_clusters={}
        for ref,vias in self.vias_by_target.items():
            if len(vias)<2 or any(v['owner']<0 for v in vias):continue
            source=self.model.cache.layers[ref[0]]['geometry'][ref[1]]
            ref_net=int(self.model.nodes[self.model.ids[ref]]['net'])
            if source.geom_type!='Polygon' or any(v['net']==ref_net and source.intersection(v['poly']).area>self.tol**2 for v in self.stays):continue
            if not all(source.intersection(v['poly']).area>self.tol**2 for v in vias):continue
            if len(vias)==2:
                paired[ref]=tuple(((v['key'],v['index']),v['owner']) for v in vias)
                continue
            by_owner={}
            for v in vias:by_owner.setdefault(v['owner'],[]).append(v)
            if len(by_owner)!=2:continue
            (o0,m0),(o1,m1)=sorted(by_owner.items())
            p0=[v['poly'] for v in m0];p1=[v['poly'] for v in m1]
            min_cross=min(a.distance(b) for a in p0 for b in p1)
            if min_cross>self.tol and _mst_max(p0)<min_cross and _mst_max(p1)<min_cross:
                va=min(m0,key=lambda v:min(v['poly'].distance(b) for b in p1))
                vb=min(m1,key=lambda v:va['poly'].distance(v['poly']))
                if _side_clustered(source,p0,va['center'],vb['center'],min_cross) and _side_clustered(source,p1,va['center'],vb['center'],min_cross):
                    paired[ref]=(((va['key'],va['index']),o0),((vb['key'],vb['index']),o1))
                    paired_clusters[ref]={o0:(va,m0,m1),o1:(vb,m1,m0)}
        groups=[]
        def classify(item):
            ref,vias=item
            if ref in paired or ref in self.settings.reconstruction_forced:return ref,[],None,None
            source=self.model.cache.layers[ref[0]]['geometry'][ref[1]]
            if ref in self.box_refs:return ref,[],None,None
            if any(layer==ref[0] and source.intersects(shapely.box(*bounds)) for layer,bounds,mode in self.settings.reconstruction_regions):return ref,[],None,None
            certificates=[enclosure_certificate(self.model,self.rules.p,source,[v]) for v in vias]
            safe=[v for v,proof in zip(vias,certificates) if proof]
            cert=dict(certificates[0],interfaces=len(vias),minimum_margin_um=min(q['minimum_margin_um'] for q in certificates)) if vias and all(certificates) else None
            from .ignored_motion import fixed_enclosures_safe,complex_mode
            if ref in self.settings.reconstruction_following and not fixed_enclosures_safe(self,ref,vias):cert=None;safe=[]
            manual=ref in self.settings.reconstruction_ignored or any(row[:2]==ref for row in self.settings.reconstruction_intervals)
            mode=None if manual else complex_mode(self,ref,vias,cert)
            if mode is not None:cert=dict(certified=True,scope='完整输入盒刚体包围及邻网/封装空间比较',following=mode)
            elif not manual:cert=None;safe=[]
            return ref,safe,cert,mode
        mode=self.settings.reconstruction_die_mode
        if mode!='auto':
            refs=[(key,i) for key,role in self.model.roles.items() if role=='固定RDL'
                  for i in range(len(self.model.cache.layers[key]['geometry']))]
            if refs:
                dies=shapely.STRtree(self.model.case['dies'])
                geometries=[self.model.cache.layers[k]['geometry'][i] for k,i in refs]
                source,owners=dies.query(geometries,predicate='covered_by')
                groups_by_ref={}
                for i,owner in zip(source,owners):
                    groups_by_ref.setdefault(refs[int(i)],[]).append(int(owner))
                manual={tuple(row[:2]) for name in (self.settings.reconstruction_intervals,self.settings.reconstruction_added,
                          self.settings.reconstruction_cuts,self.settings.reconstruction_deleted) for row in name}
                manual.update(self.settings.reconstruction_forced)
                manual.update(self.settings.reconstruction_ignored)
                manual.update(self.box_refs)
                for ref,who in groups_by_ref.items():
                    if len(who)!=1 or ref in paired:continue
                    rdl_net=int(self.model.nodes[self.model.ids[ref]]['net'])
                    src=self.model.cache.layers[ref[0]]['geometry'][ref[1]]
                    if any(v['net']==rdl_net and src.intersects(v['poly']) for v in self.stays):continue
                    self.die_internal[ref]=who[0]
                    if ref in manual:continue
                    self.auto_ignored[ref]=dict(certified=False,scope='DIE内动区判断',following=(mode=='follow'))
                    if mode=='follow':self.auto_following.add(ref)
        candidates=[item for item in self.vias_by_target.items() if item[0] not in self.die_internal]
        for ref,safe,cert,mode in parallel_map(classify,candidates):
            self.safe_vias[ref]=safe
            if cert:self.auto_ignored[ref]=cert
            if mode:self.auto_following.add(ref)
        for (layer,index),vias in sorted(self.vias_by_target.items()):
            if (layer,index) in self.auto_ignored:continue
            safe_ids={(v['key'],v['index']) for v in self.safe_vias.get((layer,index),())}
            for owner in sorted({v['owner'] for v in vias if v['owner']>=0}):
                mine=[v for v in vias if v['owner']==owner and (v['key'],v['index']) not in safe_ids]
                if not mine:continue
                groups.append((layer,index,owner,mine,vias))
        # Buffered foreign copper contains every possible position in the full
        # independent multi-DIE input box. It is a conservative feasibility check,
        # not a claim to have finitely enumerated a continuous distribution.
        envelopes=getattr(self,'_envelopes',None)
        if envelopes is None:
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
            cancel_check()
            layer,index,owner,mine,all_vias=task
            original_source=self.model.cache.layers[layer]['geometry'][index]
            net=int(self.model.nodes[self.model.ids[layer,index]]['net'])
            from .reconstruction_ownership import divide
            pair=paired.get((layer,index))
            same_die=pair is not None and all(v['owner']==owner for v in all_vias)
            if same_die:
                halves=divide(original_source.wkb,tuple((i,v['poly'].wkb) for i,v in enumerate(all_vias)),self._rule_width,self.tol)
                if len(halves)==2:
                    return [dict(entry,paired_vias=pair) for i,via in enumerate(all_vias)
                            for domain,seam in halves[i] for entry in
                            build_domain(layer,index,owner,[via],all_vias,original_source,original_source,seam,True,
                                         ownership_domain=domain,opposite=[all_vias[1-i]])]
            allocation=divide(original_source.wkb,tuple((v['owner'],v['poly'].wkb) for v in all_vias),self._rule_width,self.tol)
            domains=allocation.get(owner,[(original_source,shapely.Polygon())])
            cluster_info=paired_clusters.get((layer,index))
            if cluster_info is not None and owner in cluster_info:
                va,m_local,m_opp=cluster_info[owner]
                vb=cluster_info[next(o for o in cluster_info if o!=owner)][0]
                src_dom,seam_dom=domains[0] if len(domains)==1 else (original_source,shapely.Polygon())
                return [dict(entry,paired_vias=pair) for entry in
                        build_domain(layer,index,owner,[va],[va,vb],original_source,original_source,seam_dom,True,
                                     ownership_domain=src_dom,opposite=[vb],
                                     cluster_pair=(m_local,[v['poly'] for v in m_opp]))]
            flexible=(bool(allocation) and len(allocation)==2 and all(len(v)==1 for v in allocation.values())
                      and not any(v['net']==net and original_source.intersects(v['poly']) for v in self.stays))
            return [dict(entry,paired_vias=pair) for source,seam in domains
                    for local in ([v for v in mine if source.intersection(v['poly']).area>self.tol*self.tol],)
                    if local for entry in build_domain(layer,index,owner,local,all_vias,original_source,
                                                       original_source if flexible else source,seam,bool(allocation),
                                                       ownership_domain=source if flexible else None)]
        def build_domain(layer,index,owner,mine,all_vias,original_source,source,seam,allocated,ownership_domain=None,opposite=None,cluster_pair=None):
            refs=normalize_via_refs((v['key'],v['index']) for v in mine)
            net=int(self.model.nodes[self.model.ids[layer,index]]['net'])
            from .landing_geometry import embedded_pad
            landing=lambda v:embedded_pad(v['poly'],source,v['poly'].buffer(self.p.annulus_um,quad_segs=32),self.tol).intersection(source) if source.geom_type=='Polygon' else v['poly'].buffer(self.p.annulus_um,quad_segs=32).intersection(source)
            land=shapely.union_all([landing(v) for v in mine])
            fixed=[landing(v) for v in self.stays
                   if v['net']==net and source.intersects(v['poly'])]
            other_vias=[v for v in all_vias if v['owner']!=owner] if opposite is None else opposite
            fixed.extend(landing(v) for v in other_vias)
            if not seam.is_empty and ownership_domain is None:fixed.append(seam)
            fixed=[g for g in fixed if not g.is_empty]
            disjoint=[g for g in fixed if g.intersection(land).area<=self.tol*self.tol]
            if disjoint:fixed=disjoint
            from .reconstruction_clusters import anchor_clusters,inward_routes
            fixed=anchor_clusters(source,fixed,land,self._rule_width,self.tol)
            land_all=land
            base=dict(layer=layer,index=index,owner=owner,refs=refs,source=original_source,land=land,net=net,mine=mine,anchors=fixed,ownership_seam=seam)
            def build_source(source,land,fixed,moving=None,pair_route=False,cluster_vias=None):
                moving=mine if moving is None else moving
                full_moving=cluster_vias[0] if cluster_vias is not None else moving
                local_refs=normalize_via_refs((v['key'],v['index']) for v in full_moving)
                local_base=dict(base,routing_source=source,land=land,anchors=fixed,mine=full_moving,refs=local_refs)
                if not fixed:return dict(local_base,plan=None,orphan=True,warning='无固定连接端，整块按所属DIE平移')
                if land.is_empty:return dict(local_base,plan=None,orphan=False,warning='名义落盘无可用铜，保留原图')
                stop=min(fixed,key=lambda g:g.distance(land));legacy_end=np.asarray(stop.representative_point().coords[0])
                ports=[v['center'] for v in self.stays if v['net']==net and stop.intersection(v['poly']).area>self.tol*self.tol]
                if not ports and ownership_domain is not None:
                    ports=[v['center'] for v in other_vias if stop.intersection(v['poly']).area>self.tol*self.tol]
                center=np.mean(ports,axis=0) if ports else np.asarray(stop.centroid.coords[0])
                end=center if source.covers(shapely.Point(center)) and stop.buffer(self.tol).covers(shapely.Point(center)) else legacy_end
                candidates=[]
                pairs=[(v['center'],end) for v in sorted(moving,key=lambda v:-np.linalg.norm(v['center']-end))[:8]]
                if any(row[:2]==(layer,index) for name in ('reconstruction_intervals','reconstruction_angles','reconstruction_deleted','reconstruction_added') for row in getattr(self.settings,name,())):
                    pairs.extend((v['center'],legacy_end) for v in moving[:8])
                if len(moving)>1:
                    start=np.asarray(land.centroid.coords[0]);stop_all=shapely.union_all(fixed).centroid
                    if source.covers(shapely.Point(start)) and source.covers(stop_all):pairs.insert(0,(start,np.asarray(stop_all.coords[0])))
                with_manual=any(row[:2]==(layer,index) for name in ('reconstruction_intervals','reconstruction_angles','reconstruction_deleted','reconstruction_added') for row in getattr(self.settings,name,()))
                pairs.extend(inward_routes(source,moving))
                seen=set();traced=[]
                real_fixed=tuple(v['poly'] for v in self.stays if v['net']==net and v['poly'].intersects(source) and any(a.intersects(v['poly']) for a in fixed))
                cluster_moving=shapely.union_all([v['poly'] for v in cluster_vias[0]]) if cluster_vias is not None else None
                cluster_fixed=shapely.union_all(cluster_vias[1]) if cluster_vias is not None else None
                found_anchored=False
                for start,endpoint in pairs:
                    if found_anchored and not with_manual and all(not g.dwithin(shapely.Point(endpoint),self.tol*8) for g in fixed):continue
                    line=trace(source,start,endpoint,self._rule_width)
                    if line is not None and line.wkb in seen:continue
                    if line is not None:seen.add(line.wkb)
                    if line is None:continue
                    traced.append(line)
                    plan=RoutePlan(layer,index,owner,local_base['refs'],source,line,land,tuple(fixed),self.tol,0.,memo_budget=memo_budget)
                    terminal_fixed=tuple(cluster_vias[1]) if cluster_vias is not None else real_fixed
                    plan.terminal_vias=(tuple(v['poly'] for v in full_moving),terminal_fixed)
                    if cluster_moving is not None:plan.cluster_moving=cluster_moving
                    if cluster_fixed is not None:plan.cluster_fixed=cluster_fixed
                    physical_start=any(g.dwithin(shapely.Point(line.coords[0]),self.tol) for g in plan.terminal_vias[0])
                    plan.physical_end=any(g.dwithin(shapely.Point(line.coords[-1]),self.tol) for g in terminal_fixed)
                    plan.reaches_anchor=plan.physical_end or any(g.dwithin(shapely.Point(line.coords[-1]),self.tol*8) for g in fixed)
                    plan.anchored_bent=plan.reaches_anchor and len(plan.xy)>2
                    plan.pad_modes=(self.settings.reconstruction_allow_moving_pad and physical_start,False)
                    plan.pad_trigger=self.settings.reconstruction_pad_trigger_um
                    if ownership_domain is not None and not seam.is_empty:
                        from .reconstruction_route import terminal_marks
                        seam_marks=terminal_marks(line,seam)
                        if len(seam_marks):plan.default_fixed_start=float(seam_marks.min())
                        plan.ownership_domain=ownership_domain
                    # Opposite moving terminals share a nominal middle seam. Neither
                    # owner's transition may consume the other owner's half-route.
                    opposite_owner=any(v['owner']!=owner and stop.intersects(v['poly']) for v in all_vias)
                    if not allocated and (opposite_owner or not any(v['net']==net and source.intersects(v['poly']) for v in self.stays)):
                        plan.fixed_start=(plan.land_end+plan.fixed_start)*.5
                    mu,bounds=mean_and_bounds(self.p.for_die(owner),len(self.model.bound.centers))
                    radius=float(np.linalg.norm(shapely.get_coordinates(source)-self.model.bound.centers[owner],axis=1).max())
                    travel=float(np.linalg.norm(np.abs(mu[:2])+bounds[:2])+2*radius*np.sin(min(np.pi,abs(mu[2])+bounds[2])/2))
                    if pair_route:plan.preferred_land=land_all
                    partition=plan.choose(travel)
                    if partition is not None:
                        candidates.append((not bool(plan.warning),partition['b']-partition['a'],plan))
                        found_anchored|=plan.anchored_bent
                if not candidates:
                    from .plane_route import candidate
                    alternative=candidate(local_base,traced,self)
                    if alternative is not None:return alternative
                    return dict(local_base,plan=None,orphan=False,warning='拓扑无法分出相连的动/静/平移区；保留原图')
                # A saved cut/angle belongs to its exact source route. The fast
                # IPC ranking must not silently choose another valid trace and
                # thereby drop the user's interval on a different route identity.
                roots={str(row[4]).split('~')[0] for name in ('reconstruction_intervals','reconstruction_angles','reconstruction_deleted')
                       for row in getattr(self.settings,name,())
                       if (row[0],row[1],row[2],normalize_via_refs(row[3]))==candidates[0][2].key}
                roots.update(str(row[5]).split('~')[0] for row in self.settings.reconstruction_added
                             if row[:2]==(layer,index) and len(row)>5 and row[5]!='seed')
                saved=[q for q in candidates if q[2].identity in roots]
                if saved:candidates=saved
                else:
                    # Inward chords are useful for wide straight bars, but on bent
                    # traces they stop before a corner and create an uneditable stub.
                    from .parallel_zones import alignment
                    anchored=[row for row in candidates if getattr(row[2],'anchored_bent',False)]
                    if anchored:candidates=anchored
                    else:
                        physical=[row for row in candidates if row[2].reaches_anchor]
                        if physical:candidates=physical
                        aligned=[row for row in candidates if alignment(row[2])>=np.cos(np.deg2rad(5))]
                        if aligned:candidates=aligned
                if pair_route or getattr(self,'measured_only',False):
                    # Individual pair copper is discarded by branch assembly.
                    # Native DRC on every pair checked a fictitious full net and
                    # repeated it for all poses. Select a nominal section here;
                    # the assembled physical domain is checked once afterwards.
                    def branch_rank(row):
                        plan=row[2];a,b=plan.nominal_first,plan.nominal_second
                        variation=plan.width_variation(a,b)
                        part=plan.partition(a,b);common=part is not None and land_all.difference(part['C']).area<=self.tol*self.tol
                        return (not common,not row[0],variation>.05,variation if variation>.05 else 0.,-(b-a))
                    plan=min(candidates,key=branch_rank)[2]
                    return dict(local_base,plan=plan,orphan=False,warning=plan.warning,
                                extreme_checks=0,extreme_invalid=0,all_die_space_certified=False,priority_failure_mass=None)
                # The search is performed once over every local discrete extreme,
                # never against the currently selected pose. Other-DIE windows use
                # the full-input displacement envelope above; final per-pose spacing
                # is independently measured on the joint reconstructed layout.
                from .reconstruction_route import warp
                ranked=[];best_mass=float('inf')
                trials=[]
                for _,length,plan in candidates:
                    for a,b in getattr(plan,'candidate_intervals',((plan.nominal_first,plan.nominal_second),)):
                        trials.append((b-a,plan,a,b))
                trials=list({(id(plan),a,b):(length,plan,a,b) for length,plan,a,b in trials}.values())
                def uniform_rank(q):
                    variation=q[1].width_variation(q[2],q[3])
                    return (variation>.05,variation if variation>.05 else 0.,-q[0])
                # Preserve a long alternative as well as the best uniform runs;
                # a uniformity heuristic must not prune away the lower-risk route.
                chosen=sorted(trials,key=uniform_rank)[:2]+[max(trials,key=lambda q:q[0])]
                unique={(id(plan),a,b):(length,plan,a,b) for length,plan,a,b in chosen}
                for length,plan,a,b in unique.values():
                    part=plan.partition(a,b)
                    invalid=0
                    for pose in local_poses[owner]:
                        cancel_check()
                        _,error=warp(part,plan,self.model.bound.centers[owner],pose,'c2',0)
                        invalid+=bool(error)
                    mass,_=self.rules.candidate_mass(plan,part,priority_grid,'c2',best_mass)
                    tree,nets=envelopes[layer]
                    sweep=shapely.union_all([part['A'],part['C']]).buffer((travel+self.rule_space+2*self.tol)/np.cos(np.pi/48),quad_segs=12)
                    neighbours=tree.query(sweep,predicate='intersects')
                    certified=not any(nets[int(j)]!=net for j in neighbours)
                    variation=plan.width_variation(a,b)
                    ranked.append((mass,invalid,variation if variation>.05 else 0.,-length,not certified,plan,a,b))
                    best_mass=min(best_mass,mass)
                    if mass==0 and not invalid:break  # first zero-risk candidate is already longest
                mass,invalid,_,_,space_unproven,plan,a,b=min(ranked,key=lambda q:q[:5])
                plan.nominal_first=a;plan.nominal_second=b
                warning=plan.warning
                if space_unproven:warning=(warning+'；' if warning else '')+'全输入多DIE包络未证明线距充足，按当前几何复核'
                if invalid:warning=f'基准曲线在{invalid}/{len(local_poses[owner])}个本DIE极端需进一步适配；以当前样式最终检查为准'
                return dict(local_base,plan=plan,orphan=False,warning=warning,extreme_checks=len(local_poses[owner]),extreme_invalid=invalid,all_die_space_certified=not space_unproven,priority_failure_mass=mass)
            if len(mine)>1 or len(fixed)>1:
                def _mst_max(geoms):
                    if len(geoms)<2:return 0.
                    remaining=set(range(1,len(geoms)));worst=0.
                    dists={j:geoms[0].distance(geoms[j]) for j in remaining}
                    while remaining:
                        nxt=min(remaining,key=dists.__getitem__)
                        worst=max(worst,dists[nxt]);remaining.remove(nxt)
                        for j in remaining:
                            d=geoms[nxt].distance(geoms[j])
                            if d<dists[j]:dists[j]=d
                    return worst
                real_stays=[v for v in self.stays if v['net']==net and source.intersects(v['poly'])]
                real_stay_polys=[v['poly'] for v in real_stays]
                extra_anchors=[g for g in fixed if not any(g.intersects(p) for p in real_stay_polys)]
                f_polys=real_stay_polys+extra_anchors
                m_polys=[v['poly'] for v in mine]
                if not extra_anchors and len(m_polys)>=2 and len(f_polys)>=2:
                    min_cross=min(a.distance(b) for a in m_polys for b in f_polys)
                    if min_cross>self.tol and _mst_max(m_polys)<min_cross and _mst_max(f_polys)<min_cross:
                        va=min(mine,key=lambda v:min(v['poly'].distance(b) for b in f_polys))
                        if real_stays and min(va['poly'].distance(p) for p in real_stay_polys)<=min_cross+self.tol:
                            vb=min(real_stays,key=lambda v:va['poly'].distance(v['poly']))
                            lb=landing(vb)
                            vb_pt=vb['center']
                        else:
                            lb=min(fixed,key=lambda g:va['poly'].distance(g))
                            vb_pt=np.asarray(lb.centroid.coords[0])
                        from .reconstruction_route import trace as _trace
                        cross_line=_trace(source,va['center'],vb_pt,self._rule_width)
                        cross_len=cross_line.length if cross_line is not None else min_cross
                        def _side_clustered(polys):
                            rem=set(range(1,len(polys)))
                            dists={j:polys[0].distance(polys[j]) for j in rem}
                            prev={j:0 for j in rem}
                            buf=source.buffer(self.tol)
                            while rem:
                                nxt=min(rem,key=dists.__getitem__)
                                u,v=polys[prev[nxt]],polys[nxt]
                                if not buf.covers(shapely.shortest_line(u,v)):
                                    t=_trace(source,np.asarray(u.centroid.coords[0]),np.asarray(v.centroid.coords[0]),self._rule_width)
                                    if t is None or t.length>=cross_len*.5:
                                        return False
                                rem.remove(nxt)
                                for j in rem:
                                    d=polys[nxt].distance(polys[j])
                                    if d<dists[j]:dists[j]=d;prev[j]=nxt
                            return True
                        if _side_clustered(m_polys) and _side_clustered(f_polys):
                            clustered=build_source(source,landing(va),[lb],[va],cluster_vias=(mine,f_polys))
                            plan_c=clustered.get('plan')
                            if plan_c is not None:
                                part_c=plan_c.partition(plan_c.nominal_first,plan_c.nominal_second)
                                if (part_c is not None and
                                    shapely.union_all(m_polys).difference(part_c['C']).area<=self.tol*self.tol and
                                    shapely.union_all(f_polys).difference(part_c['B']).area<=self.tol*self.tol):
                                    return [clustered]
                rows=[]
                from .branch_regions import terminal_tree
                pairs=terminal_tree([v['poly'] for v in mine],fixed)
                edited=(layer,index) in self.box_refs or any(row[:2]==(layer,index) for name in ('reconstruction_intervals','reconstruction_angles','reconstruction_added','reconstruction_deleted','reconstruction_cuts','reconstruction_extra_cuts','reconstruction_second_cuts') for row in getattr(self.settings,name,()))
                if edited:pairs=[(i,j) for i in range(len(mine)) for j in range(len(fixed))]
                for number,(i,j) in enumerate(pairs):
                    cancel_check();via,anchor=mine[i],fixed[j]
                    row=build_source(source,landing(via),[anchor],[via],pair_route=True)
                    row.update(graph_branch=True,moving_nominal=land,fixed_nominal=shapely.union_all(fixed))
                    rows.append(row)
                    if progress and number%8==0:progress(25,f'分叉连接 {layer} #{index+1}：{number+1}/{len(pairs)}')
                if rows and all(q['plan'] is not None for q in rows):return rows
            from .reconstruction_route import branch_sources
            branches=branch_sources(source,land,fixed,mine[0]['center'],self._rule_width,self.tol,0.)
            if branches:
                return [dict(build_source(g,common,[anchor]),branch=i+1,branch_count=len(branches)) for i,(g,common,anchor) in enumerate(branches)]
            combined=build_source(source,land,fixed,cluster_vias=cluster_pair)
            if combined['plan'] is not None or combined['orphan']:return [combined]
            # In a wide/overlapping pad, embedded_pad can cover the other
            # terminal. Its entire inferred disk is not a rigid constraint;
            # retain the actual via plus REQUIRED enclosure instead.
            required=lambda v:v['poly'].buffer(self.p.annulus_um,quad_segs=32).intersection(source)
            from .reconstruction_clusters import terminal_clusters
            clusters=terminal_clusters(source,mine,self.p.annulus_um,self.tol)
            rows=[]
            for cluster in clusters:
                ids={id(v) for v in cluster}
                local_land=shapely.union_all([required(v) for v in cluster])
                protected=[required(v) for v in self.stays if v['net']==net and source.intersects(v['poly'])]
                if not seam.is_empty:protected.append(seam)
                protected.extend(required(v) for v in all_vias if v['owner']!=owner)
                overlap=[g for g in protected if g.intersection(local_land).area>self.tol*self.tol]
                protected=[g for g in protected if g.intersection(local_land).area<=self.tol*self.tol]
                if not protected:
                    xy=shapely.get_coordinates(source);dist=shapely.distance(shapely.points(xy),local_land)
                    anchor=shapely.Point(xy[int(np.argmax(dist))]).buffer(self.tol*16).intersection(source)
                    protected=[anchor]
                protected=anchor_clusters(source,protected,local_land,self._rule_width,self.tol)
                parts=branch_sources(source,local_land,protected,cluster[0]['center'],self._rule_width,self.tol,0.)
                if parts:
                    rows.extend(dict(build_source(g,trunk,[anchor],cluster),branch=j+1,branch_count=len(parts)) for j,(g,trunk,anchor) in enumerate(parts))
                else:
                    row=build_source(source,local_land,protected,cluster)
                    if overlap:row['warning']+='；运动与固定孔包围区域重叠，按最终几何复核共同孔盘'
                    rows.append(row)
            return rows
        # Route construction is pose-independent and reused after every handle edit.
        from .session_cache import logical_fingerprint
        base_key=logical_fingerprint((self.model.data.geometry_id,self.model.roles,self.model.connections.order,
                                'source-routes-v82',self.settings.evaluation_scope=='risk',self.rules.identity,bool(getattr(self,'measured_only',False)),self.settings.reconstruction_allow_moving_pad,self.settings.reconstruction_pad_trigger_um,probability_parameters.__dict__,self._rule_width,'c2'))
        # Forcing/editing one source must not rebuild every source path. Mine
        # membership includes enclosure filtering; all-via ownership controls
        # allocation. Existing path identities survive later manual edits.
        keys=[logical_fingerprint((base_key,l,i,owner,[(v['key'],v['index']) for v in mine],
                                   [(v['key'],v['index'],v['owner']) for v in all_vias]))
              for l,i,owner,mine,all_vias in groups]
        missing=[(key,task) for key,task in zip(keys,groups) if key not in self.plan_pool]
        from .resources import native_phase_profile
        built=parallel_map(lambda item:(item[0],build(item[1])),missing,profile=native_phase_profile(),
                           progress=(lambda n,total:progress(round(65*n/max(1,total)),f'建立全姿态共用动区 {n}/{total}')) if progress else None)
        self.plan_pool.update(built)
        keep=set(keys)
        for old_key in list(self.plan_pool):
            if len(self.plan_pool)<=max(64,4*len(keys)):break
            if old_key not in keep:self.plan_pool.pop(old_key,None)
        from .reconstruction_zones import expand
        self._base_plans=[entry for key in keys for entry in self.plan_pool[key]]
        from .parallel_zones import coordinate
        self._base_plans=coordinate(self,self._base_plans)
        ignored=set(self.settings.reconstruction_ignored)-self.box_refs
        self._plans=expand(self,[e for e in self._base_plans if (e['layer'],e['index']) not in ignored])
        from .ignored_motion import entries as ignored_entries
        self._plans.extend(ignored_entries(self))
        return self._plans

    def risk_priority(self,catalogue=None):
        if getattr(self,'_risk_priority',None) is None:
            from types import SimpleNamespace
            from .evaluation_scope import focused_catalog,conditional_weights,digits_for
            grid,indices=focused_catalog(self.model,self.p,catalogue,scope='risk')
            weights,_,_=conditional_weights(grid,self.p,indices)
            digits=np.asarray([digits_for(grid,i) for i in indices])
            projected=[np.unique(digits[:,owner]) for owner in range(grid.count)]
            self._risk_priority=(SimpleNamespace(locals=grid.locals,weights=[
                np.bincount(digits[:,owner],weights,minlength=radix) for owner,radix in enumerate(grid.radices)]),
                [poses[rows] for poses,rows in zip(grid.locals,projected)])
        return self._risk_priority

    def _box_applies(self,plan,box_poly):
        domain=getattr(plan,'ownership_domain',None)
        if domain is None or domain.intersects(box_poly):return True
        ref=tuple(plan.key[:2])
        from .reconstruction_edits import key as edit_key
        deleted=set(self.settings.reconstruction_deleted)
        peers=[e for e in (self._plans or getattr(self,'_base_plans',None) or ())
               if (e['layer'],e['index'])==ref and e.get('plan') is not None
               and e['plan'].owner!=plan.owner and edit_key(e['plan']) not in deleted]
        return not any(getattr(e['plan'],'ownership_domain',e['plan'].source).intersects(box_poly) for e in peers)

    def _interval_without_boxes(self,entry):
        plan=entry['plan']
        if plan is None:return None,False
        a,b=plan.nominal_first,plan.nominal_second;manual=False
        for l,i,owner,refs,identity,left,right in self.settings.reconstruction_intervals:
            if (l,i,owner,normalize_via_refs(refs))==plan.key and identity==plan.identity:
                from .reconstruction_edits import blade_angles
                plan.cut_mode='manual'
                angles,pinned=blade_angles(self.settings,plan,left,plan.fixed_side(right))
                part=plan.partition(float(left),float(right),angles,tight=True)
                if part is not None:
                    a,b=part['a'],part['b'];manual=True
                    if not self.settings.reconstruction_regions:return dict(part,manual_angles=pinned),True
        if entry.get('seeded') and not self.settings.reconstruction_regions:
            from .reconstruction_edits import apply_angles
            plan.cut_mode='manual'
            return apply_angles(self.settings,plan,plan.partition(a,b,tight=True)),True
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
        from .reconstruction_edits import apply_angles
        if manual:plan.cut_mode='manual'
        base=plan.partition(a,b,tight=manual) if manual else plan.partition(a,b)
        return apply_angles(self.settings,plan,base),manual

    def interval(self,entry):
        plan=entry['plan']
        if plan is None:return None,False
        if 'merged_part' in entry:return entry['merged_part'],entry.get('serial_explicit',True)
        return self._interval_without_boxes(entry)

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
        if not hasattr(self,'priority_grid'):
            self.priority_grid=self.risk_priority()[0] if self.settings.evaluation_scope=='risk' else WidthGrid(replace(self.p,target_nsigma=0),len(self.model.bound.centers),3)
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

    def complete_copper(self,raw,states,progress=None,*,quick=False,deformation=None):
        """Validate the supplied raw copper once and reuse it for the fallback decision."""
        from .topology_routing import resolve
        out,pieces,changed,blocked=resolve(self,raw['replacements'],raw['pieces'],states,progress_slice(progress,35,.3))
        if blocked and not quick:
            blocked_refs={tuple(ref) for ref,_ in blocked}
            if any((q['layer'],q['index']) in blocked_refs and not q.get('ignored') and (q.get('route_available') or q.get('route_plan') is not None) for q in pieces):
                alternate=self._raw_frame(states,deformation,progress_slice(progress,65,.1),True,diagnostics=False)
                ao,ap,ac,ab=resolve(self,alternate['replacements'],alternate['pieces'],states,progress_slice(progress,75,.15))
                if len(ab)<len(blocked):raw,out,pieces,changed,blocked=alternate,ao,ap,ac,ab
        return raw,out,pieces,changed,blocked

    def frame(self,states,deformation=None,progress=None,quick=False,*,targets=None,diagnostics=True,render_cache=None,_raw=False):
        if _raw:return self._raw_frame(states,deformation,progress,quick,targets=targets,diagnostics=diagnostics,render_cache=render_cache)
        if targets is not None and len(targets)==0 and not diagnostics and getattr(self,'measured_only',False):
            return dict(complete=True,replacements={},pieces=[],repair_refs=set(),hard_failures=[],violations=[],original_violations=(),electrically_valid=True,constraints_complete=False,
                        summary=dict(pieces=0,bridges=0,conflicts=0,warnings=0,electrical_repairs=0,unroutable=0))
        states=np.asarray(states,float);key=(states.tobytes(),'electrical-continuity',bool(quick),bool(diagnostics),None if targets is None else tuple(sorted(targets)))
        hit=self.cache.get(key) if diagnostics else None
        if hit is not None:return hit
        raw=self._raw_frame(states,deformation,progress_slice(progress,0,.35),quick,diagnostics=False,render_cache=render_cache)
        raw,out,pieces,changed,blocked=self.complete_copper(raw,states,progress,quick=quick,deformation=deformation)
        from .topology_routing import spacing
        from .ignored_motion import add_linked_geometry
        out=add_linked_geometry(self,out,states)
        result=dict(raw,replacements=out,pieces=pieces,repair_refs=changed,hard_failures=blocked,
                    electrically_valid=not bool(blocked),constraints_complete=bool(diagnostics))
        result['violations']=self.rules.check(out,states,progress_slice(progress,90,.09)) if diagnostics else []
        result['original_violations']=self.rules.nominal_check() if diagnostics else ()
        if diagnostics:spacing(self,out,pieces)
        decorate_problems(result)
        result['summary']=dict(raw['summary'],bridges=sum(not q['bridge'].is_empty for q in pieces),
                               conflicts=sum(bool(q['conflict']) for q in pieces),warnings=sum(bool(q['warning']) for q in pieces),
                               electrical_repairs=len(changed),unroutable=len(blocked))
        if targets is not None:
            include=set(map(tuple,targets))|set(changed)
            result['pieces']=[q for q in pieces if (q['layer'],q['index']) in include]
            result['between_blocks']=tuple(b for b in result.get('between_blocks',()) if b['ref'] in include)
            result['endpoint_blocks']=tuple(b for b in result.get('endpoint_blocks',()) if b['ref'] in include)
            result['replacements']={layer:[q for q in rows if any((layer,i) in include for i in q['indices'])] for layer,rows in out.items()}
            result['replacements']={layer:rows for layer,rows in result['replacements'].items() if rows}
        if diagnostics:
            if not np.any(states) and targets is None:self.cache.pin(key)
            self.cache[key]=result
        if progress:progress(100,f'重构预演完成；{len(changed)}处电连接补偿，{len(blocked)}处无可用解')
        return result

    def _raw_frame(self,states,deformation=None,progress=None,quick=False,*,targets=None,diagnostics=True,render_cache=None):
        from .reconstruction_route import warp,EMPTY,failed_connection,missing_area
        states=np.asarray(states,float)
        key=(states.tobytes(),'shared-route-v42-quick' if quick else 'shared-route-v42',bool(diagnostics),None if targets is None else tuple(sorted(targets)))
        hit=self.cache.get(key) if diagnostics else None
        if hit is not None:return hit
        catalogue=self._plans_for_all_poses(progress);pieces=[];features={}
        if self._entry_catalogue is not catalogue:
            by_ref={}
            for entry in catalogue:
                by_ref.setdefault((entry['layer'],entry['index']),[]).append(entry)
            self._entries_by_ref=by_ref;self._entry_catalogue=catalogue
            self._nominal_intervals={}
        selected=None if targets is None else set(map(tuple,targets))
        entries=catalogue if selected is None else [q for ref in sorted(selected) for q in self._entries_by_ref.get(ref,())]
        nominal_intervals=self._nominal_intervals
        from .reconstruction_incremental import reusable
        retained,retained_pieces,retained_between,retained_endpoint=reusable(self,states,selected)
        retained_refs={(layer,i) for layer,rows in retained.items() for row in rows for i in row['indices']}
        entries=[q for q in entries if (selected is None or (q['layer'],q['index']) in selected) and (q['layer'],q['index']) not in retained_refs]
        from .moving_pair import MultiPairFrame,frames as paired_frames,repose
        from .reconstruction_zones import serial_boundary_states
        references=paired_frames(entries,self.model.bound.centers,states,settings=self.settings,interval_fn=lambda e:self.interval(e))
        local_states={};orders={}
        from .between_motion import STATIONARY,equivalent_pose,motion_order,parse_die_mode,serial_poses,solve_serial_weights
        owner_frames={}
        for entry in entries:
            ref=entry['layer'],entry['index']
            if ref in references:
                if ref not in local_states:local_states[ref]=states.copy()
                ref_pose=references[ref]
                if isinstance(ref_pose,MultiPairFrame):
                    owner_frame=ref_pose.per_owner.get(entry['owner'])
                    if owner_frame is not None:local_states[ref][entry['owner']]=owner_frame.relative
                else:
                    local_states[ref][entry['owner']]=ref_pose.relative
                owner=entry['owner']
                if (ref,owner) not in orders:
                    relative=ref_pose.per_owner.get(owner) if isinstance(ref_pose,MultiPairFrame) else ref_pose
                    if relative is not None:
                        owner_frames[ref,owner]=relative
                        point=entry['source'].representative_point().coords[0]
                        orders[ref,owner]=motion_order(point,self.model.bound.centers[owner],states[owner],ref_pose)
        adjusted=[]
        shares={}
        for entry in entries:
            ref=(entry['layer'],entry['index']);owner=entry['owner']
            frame=owner_frames.get((ref,owner))
            modes=entry.get('serial_modes',())
            has_die=any(parse_die_mode(m) is not None for m in modes)
            stationary=STATIONARY in modes or has_die
            reverse=not orders.get((ref,owner),True)
            if not reverse and not (stationary and ((frame is not None and np.any(frame.base)) or has_die)):
                adjusted.append(entry);continue
            values={'motion_reversed':reverse}
            if stationary and ((frame is not None and np.any(frame.base)) or has_die):
                ident=(ref,owner,modes)
                if ident not in shares:
                    point=entry['source'].representative_point().coords[0]
                    ref_center=frame.center if frame is not None else self.model.bound.centers[owner]
                    end_pose=frame.base if frame is not None else np.zeros(3)
                    knot_poses={i:equivalent_pose(ref_center,self.model.bound.centers[d],states[d])
                                for i,m in enumerate(modes,1) if (d:=parse_die_mode(m)) is not None and 0<=d<len(states)}
                    absolute=serial_poses(modes,states[owner],end_pose,ref_center,point,knot_poses=knot_poses)
                    shares[ident]=tuple(frame.local_pose(s) for s in absolute) if frame is not None else tuple(absolute)
                i=entry['serial_position'];values['serial_local_states']=shares[ident][i:i+2]
            elif modes:
                if modes not in shares:shares[modes]=solve_serial_weights(modes,moving_is_larger=False)
                weights=shares[modes];i=entry['serial_position']
                values.update(serial_left=weights[i],serial_right=weights[i+1])
            # Keep stable base-entry identity for nominal interval memoization.
            adjusted.append(dict(entry,**values,nominal_entry=entry))
        entries=adjusted
        # Independent source features share no mutable geometry. GEOS releases
        # the GIL for warp/validation, so larger frames use the existing hardware
        # admission policy rather than serially processing every route.
        def render(entry):
            if selected is not None and (entry['layer'],entry['index']) not in selected:return None
            ref=entry['layer'],entry['index'];pose=local_states.get(ref,states)[entry['owner']]
            serial_states=tuple(s.tobytes() for s in entry.get('serial_local_states',()))
            token=(id(entry.get('nominal_entry',entry)),pose.tobytes(),entry.get('motion_reversed',False),serial_states)
            if render_cache is not None and token in render_cache:
                part,manual,regions,error,warning=render_cache[token]
                return part,manual,None if regions is None else dict(regions),error,warning
            cancel_check()
            if token[0] not in nominal_intervals:
                nominal_intervals[token[0]]=self.interval(entry)
            part,manual=nominal_intervals[token[0]]
            if part is None:return part,manual,None,None,entry['warning']
            from .reconstruction_route import part_key
            block=entry.get('serial_block',EMPTY)
            local_key=(entry['plan'].key,entry['plan'].identity,part_key(part),part.get('manual_angles'),bool(manual),bool(diagnostics),bool(quick),self.rules.identity,self.settings.reconstruction_style,self.settings.reconstruction_boundary_mode,tuple(self.model.bound.centers[entry['owner']]),token[1],entry.get('serial_left'),entry.get('serial_right'),serial_states,block.wkb)
            cached=self.geometry_pool.get(local_key)
            if cached is not None:
                saved_part,saved_manual,saved_regions,saved_error,saved_warning=cached
                return saved_part,saved_manual,None if saved_regions is None else dict(saved_regions),saved_error,saved_warning
            from .reconstruction_angles import solve
            if part.get('box_invalid'):
                regions=dict(A=EMPTY,B=part['B'],C=transform(part['C'],self.model.bound.centers[entry['owner']],pose));error='框选区域未切断全部运动—固定路径，保留严格框选范围'
            elif part.get('box_complex'):
                regions=dict(A=part['A'],B=part['B'],C=transform(part['C'],self.model.bound.centers[entry['owner']],pose));error=None
            elif not np.any(pose) and 'serial_left' not in entry:
                regions={k:part[k] for k in 'ABC'};error=None
            elif entry.get('graph_branch') or getattr(entry['plan'],'plane_domain',False):
                from .plane_route import live_part
                part=live_part(self,entry['plan'],part,self.model.bound.centers[entry['owner']],pose);regions={k:part[k] for k in 'ABC'};error=None
            elif 'serial_left' in entry:
                from .reconstruction_zones import serial_render
                part,regions,error=serial_render(self,entry,part,self.model.bound.centers[entry['owner']],pose)
            else:part,regions,error,quality=solve(self,entry['plan'],part,self.model.bound.centers[entry['owner']],pose,quick=quick,native_check=False)
            warning=self._manual_warning(entry,part) if not getattr(entry['plan'],'plane_domain',False) and manual and diagnostics and 'serial_left' not in entry and self.settings.reconstruction_boundary_mode=='perpendicular' else entry['warning']
            if part.get('box_refitted'):warning='已在选框命中的路径补齐截面'+('；'+warning if warning else '')
            self.geometry_pool.put(local_key,(part,manual,None if regions is None else dict(regions),error,warning))
            if render_cache is not None:render_cache[token]=(part,manual,None if regions is None else dict(regions),error,warning)
            return part,manual,regions,error,warning
        from .resources import parallel_map
        rendered=parallel_map(render,entries) if len(entries)>16 else list(map(render,entries))
        for number,(entry,prepared) in enumerate(zip(entries,rendered)):

            layer,index,owner=entry['layer'],entry['index'],entry['owner'];source=entry['source'];plan=entry['plan']
            if selected is not None and (layer,index) not in selected:continue
            if (layer,index) not in self.box_refs and self._cover_lookup(self.dropped,None,layer,index,entry['refs']):continue
            part,manual,regions,error,warning=prepared;valid=True;fallback=None
            pose=local_states.get((layer,index),states)[owner]
            left,right=serial_boundary_states(entry,pose)
            if part is not None:
                fallback=dict(A=EMPTY,B=part['B'],C=transform(part['C'],self.model.bound.centers[owner],pose)) if part.get('strict_box') is not None or entry.get('graph_branch') or getattr(plan,'plane_domain',False) else failed_connection(part,plan,self.model.bound.centers[owner],pose)
                if 'serial_left' in entry:
                    fallback=dict(A=EMPTY,B=entry['serial_fixed'],C=transform(entry['serial_block'],self.model.bound.centers[owner],left))
                if error:regions=dict(fallback);warning=error+'；已取消连接段';valid=False
                near=part['near'];moved_cut=transform(near,self.model.bound.centers[owner],left)
                far=transform(part['far'],self.model.bound.centers[owner],right);a,b=part['a'],part['b']
            elif entry.get('ignored'):
                following=entry.get('following',False)
                owners=entry.get('follow_owners',(owner,))
                matrices=[]
                for who in owners:
                    co,si=np.cos(states[who,2]),np.sin(states[who,2]);rot=np.array([[co,-si],[si,co]])
                    center=self.model.bound.centers[who];matrices.append(np.r_[rot.ravel(),center-rot@center+states[who,:2]])
                compatible=not following or all(np.allclose(matrices[0],m,rtol=0,atol=self.tol*.01) for m in matrices[1:])
                if following and compatible:
                    part=dict(A=EMPTY,B=EMPTY,C=source);regions=dict(A=EMPTY,B=EMPTY,C=transform(source,self.model.bound.centers[owner],pose))
                else:
                    part=dict(A=EMPTY,B=source,C=EMPTY);regions=dict(part)
                if not compatible:valid=False;warning='整块随动连接了不同刚体变换的DIE，无法同时跟随；保留源图'
                near=far=moved_cut=None;a=b=0.
            elif entry['orphan']:
                scope=entry.get('routing_source',source)
                part=dict(A=EMPTY,B=EMPTY,C=scope)
                regions=dict(A=EMPTY,B=EMPTY,C=transform(scope,self.model.bound.centers[owner],pose))
                near=far=moved_cut=None;a=b=0.
            else:
                part=dict(A=EMPTY,B=source,C=EMPTY);regions={k:part[k] for k in 'ABC'}
                near=far=moved_cut=None;a=b=0.;valid=False
            shift=float(np.linalg.norm(self.model.moved(shapely.Point(entry['mine'][0]['center']),owner,states).coords[0]-entry['mine'][0]['center']))
            feature=features.setdefault((layer,index),dict(source=source,pieces=[],net=entry['net']))
            piece=dict(ownership_seam=entry.get('ownership_seam',EMPTY),strict_box=part.get('strict_box'),box_invalid=part.get('box_invalid',False),graph_branch=entry.get('graph_branch',False) or part.get('box_complex',False),moving_nominal=entry.get('moving_nominal') if not part.get('box_complex') else plan.land,fixed_nominal=entry.get('fixed_nominal') if not part.get('box_complex') else shapely.union_all(plan.anchors),ignored=entry.get('ignored',False),ignored_following=entry.get('following',False),follow_owners=entry.get('follow_owners',(owner,)),die=owner,layer=layer,index=index,net=entry['net'],via_refs=entry['refs'],vias=[v[1] for v in entry['refs']],
                       boxed=entry.get('boxed',False),box_groups=entry.get('box_groups',()),
                       fallback_regions=fallback if part.get('near') is not None else None,connection_failed=bool(error) or not valid,
                       missing_region=part['A'] if part.get('strict_box') is not None or getattr(plan,'plane_domain',False) else missing_area(part,plan,self.model.bound.centers[owner],pose) if plan is not None and part.get('near') is not None else (regions['C'] if entry.get('ignored') and entry.get('following') and not regions['C'].is_empty else source),
                       moving_handle_endpoint=(transform(shapely.Point(plan.route.coords[0]),self.model.bound.centers[owner],pose) if plan is not None else None),
                       moving_terminals=shapely.union_all([self.model.moved(v['poly'],owner,states) for v in entry['mine'] if 'poly' in v]),
                       source=source,source_scope_complete=bool(entry.get('routing_source',source) is source if plan is None else plan.source is source),endpoint_weights=(entry.get('serial_left',1.),entry.get('serial_right',0.)),branch=entry.get('branch',1),branch_count=entry.get('branch_count',1),route_plan=plan,interval=(a,b),boundary_angles=part.get('angles',(0.,0.)),route_identity=None if plan is None else plan.identity,
                       nominal_regions={k:part[k] for k in 'ABC'},regions=regions,
                       movable=part['C'],moved=regions['C'],fixed=regions['B'],immovable=regions['B'],bridge=regions['A'],
                       cut=near,moved_cut=moved_cut,extra_cut=far,span=part['A'],frame_seam=False,
                       manual_angles=part.get('manual_angles',(False,False)),
                       whole_landing=bool(plan and a==0),whole_fixed_landing=bool(far is not None and far.geom_type=='Point'),chain_ends=[entry['mine'][0]['center'].tolist(),entry['anchors'][0].centroid.coords[0] if entry['anchors'] else entry['mine'][0]['center'].tolist()],
                       chain_anchor=None,seam=[None if near is None else list(near.centroid.coords[0]),None if moved_cut is None else list(moved_cut.centroid.coords[0])],
                       movable_area_um2=part['C'].area,moved_area_um2=regions['C'].area,fixed_area_um2=regions['B'].area,
                       shift_um=shift,cut_distance_um=a,chain_span_um=b-a,gap_um=None if regions['B'].is_empty or regions['C'].is_empty else float(regions['B'].distance(regions['C'])),
                       bridge_length_um=0 if near is None else near.length,bridge_area_um2=regions['A'].area,bridge_span_um=b-a,
                       clearance_um=None,conflict=False,conflict_net=None,conflict_link=None,manual=manual,manual_lost=False,
                       cut_clamped=False,cut_window=None,orphan=entry['orphan'],follows=entry.get('following',entry['orphan']),warning=warning,valid=valid,priority_failure_mass=self.manual_masses.get((plan.key,plan.identity,a,b),entry.get('priority_failure_mass')) if plan else None)
            if piece['graph_branch']:
                piece['branch_section']=part if part.get('strict_box') is not None else entry.get('merged_part') if piece['boxed'] else None
                piece['motion_reversed']=entry.get('motion_reversed',False)
            if 'serial_local_states' in entry:
                piece['local_boundary_states']=(left,right)
            if 'serial_between' in entry:
                blk,mode_b,custom_b,anchor_b=entry['serial_between']
                if anchor_b is not None and not blk.is_empty:
                    die_b=parse_die_mode(mode_b)
                    if die_b is not None and 0<=die_b<len(states):
                        posed_blk=transform(blk,self.model.bound.centers[die_b],states[die_b])
                    else:
                        posed_blk=transform(blk,self.model.bound.centers[owner],left)
                    piece['serial_between_block']=dict(ref=(layer,index),id=(layer,index,*anchor_b),nominal=blk,geometry=posed_blk,mode=mode_b,custom=custom_b,weight=entry['serial_left'])
            pieces.append(piece);feature['pieces'].append(piece)
        from .branch_regions import consolidate,fixed_between_owners
        pieces=consolidate(self,pieces,states,local_states,owner_frames)
        features={}
        for q in pieces:
            features.setdefault((q['layer'],q['index']),dict(source=q['source'],pieces=[],net=q['net']))['pieces'].append(q)
        out={};conflicts=0;between_blocks=list(retained_between);endpoint_blocks=list(retained_endpoint)
        from .between_motion import anchor_of,endpoint_modes
        for (layer,index),feature in features.items():
            ps=feature['pieces'];source=feature['source']
            between_start=len(between_blocks)
            owners=sorted({q['die'] for q in ps}|{v['owner'] for v in self.vias_by_target.get((layer,index),()) if v['owner']>=0})
            ref_between_overrides=tuple(o for o in getattr(self.settings,'reconstruction_between_overrides',()) if (o[0],o[1])==(layer,index))
            ref_ep_overrides=tuple(o for o in getattr(self.settings,'reconstruction_endpoint_overrides',()) if (o[0],o[1])==(layer,index))
            compose_key=('composed-continuity-v83',self.rules.identity,layer,index,quick,self.settings.reconstruction_style,self.settings.reconstruction_boundary_mode,self.settings.reconstruction_allow_moving_pad,
                         getattr(self.settings,'reconstruction_between_motion','max'),ref_between_overrides,ref_ep_overrides,
                         tuple((owner,states[owner].tobytes(),self.model.bound.centers[owner].tobytes()) for owner in owners),
                         tuple((q['route_identity'],q['interval'],q['boundary_angles'],q.get('ignored'),q.get('ignored_following'),q['endpoint_weights'],tuple(s.tobytes() for s in q.get('local_boundary_states',())),q['strict_box'].wkb if q.get('strict_box') is not None else None) for q in ps))
            saved=self.geometry_pool.get(compose_key)
            if saved is None:
                pose_states=local_states.get((layer,index),states)
                moving=feature_moves(ps,pose_states,self.settings.reconstruction_style) or any(
                    np.any(s) for q in ps for s in q.get('local_boundary_states',()))
                geometry,occupied=compose_feature(source,ps,self.tol,moving)
                names=('regions','bridge','fixed','moved','branch_conflict','branch_rigid_unresolved','connection_failed','valid','warning','seam_repair_area_um2','bridge_area_um2','fixed_area_um2','moved_area_um2')
                updates=tuple({k:(dict(q[k]) if k=='regions' else q[k]) for k in names if k in q} for q in ps)
                self.geometry_pool.put(compose_key,(geometry,occupied,updates))
            else:
                geometry,occupied,updates=saved
                for q,update in zip(ps,updates):q.update(update);q['regions']=dict(q['regions'])
            ref_pose=references.get((layer,index))
            middle_moves=False
            paired_owners={v['owner'] for v in self.vias_by_target.get((layer,index),()) if v['owner']>=0} if ref_pose is not None else set()
            if ref_pose is not None:
                geometry=repose(ps,geometry,ref_pose)
                if isinstance(ref_pose,MultiPairFrame):
                    active=paired_owners & self.active_dies
                    die_mid=parse_die_mode(ref_pose.mode)
                    if die_mid is not None:middle_moves=die_mid in self.active_dies
                    else:middle_moves=(active==paired_owners) if ref_pose.mode=='min' else ref_pose.mode!=STATIONARY and bool(active)
            for q in ps:
                if q.get('branch_rigid_unresolved'):continue
                blocks=q.get('between_blocks',())
                if 'serial_between_block' in q:blocks=(*blocks,q['serial_between_block'])
                base_owners=paired_owners-{q['die']} if len(paired_owners)>1 else paired_owners
                base_moves=middle_moves if isinstance(ref_pose,MultiPairFrame) else bool(base_owners & self.active_dies)
                for block in blocks:
                    if block['nominal'].area<=self.tol**2:continue
                    item=dict(block)
                    die_blk=parse_die_mode(block['mode'])
                    if die_blk is not None:
                        item['moves']=die_blk in self.active_dies
                    else:
                        if ref_pose is not None and np.any(ref_pose.base):item['geometry']=transform(item['geometry'],ref_pose.center,ref_pose.base)
                        weight=block['weight']
                        item['moves']=block['mode']!=STATIONARY and bool((q['die'] in self.active_dies and weight>1e-10) or (base_moves and weight<1.-1e-10))
                    between_blocks.append(item)
            if isinstance(ref_pose,MultiPairFrame) and ref_pose.nominal_core.area>self.tol**2:
                from .reconstruction_route import parts
                from .between_motion import lookup_mode,pair_middle_pose
                core_nom=ref_pose.nominal_core
                existing_mid=shapely.union_all([b['nominal'] for b in between_blocks[between_start:]])
                if not existing_mid.is_empty:core_nom=core_nom.difference(existing_mid)
                core_parts=[g for g in parts(core_nom) if g.area>max(self.tol**2*16,1e-4)]
                owners_pair=sorted(paired_owners)[:2] if len(paired_owners)>=2 else ()
                for part_core in core_parts:
                    ax,ay=anchor_of(part_core)
                    p_mode,p_custom=lookup_mode(self.settings,(layer,index),part_core,self.tol) if len(core_parts)>1 or not existing_mid.is_empty else (ref_pose.mode,ref_pose.custom)
                    if len(owners_pair)==2 and (len(core_parts)>1 or not existing_mid.is_empty):
                        pc_center,pc_state=pair_middle_pose(part_core,self.model.bound.centers[owners_pair[0]],states[owners_pair[0]],self.model.bound.centers[owners_pair[1]],states[owners_pair[1]],p_mode,centers=self.model.bound.centers,states=states)
                    else:
                        pc_center,pc_state=ref_pose.center,ref_pose.base
                    die_mid=parse_die_mode(p_mode)
                    if die_mid is not None:p_moves=die_mid in self.active_dies
                    elif len(owners_pair)==2:
                        active=paired_owners & self.active_dies
                        p_moves=(active==paired_owners) if p_mode=='min' else p_mode!=STATIONARY and bool(active)
                    else:
                        p_moves=middle_moves
                    posed_core=transform(part_core,pc_center,pc_state) if np.any(pc_state) else part_core
                    between_blocks.append(dict(ref=(layer,index),id=(layer,index,ax,ay),nominal=part_core,geometry=posed_core,mode=p_mode,custom=p_custom,moves=p_moves))
            if ref_pose is None:
                between_blocks.extend(fixed_between_owners(source,ps,between_blocks[between_start:],(layer,index),self.tol))
            stationary=[b for b in between_blocks[between_start:] if b['mode']==STATIONARY]
            if stationary:
                ps[0]['stationary_regions']=shapely.union_all([b['nominal'] for b in stationary])
                for block in stationary:block['geometry']=block['nominal']
            graph_eps=[b for q in ps for b in q.pop('endpoint_blocks',())]
            if graph_eps:
                endpoint_blocks.extend(graph_eps)
            elif any(q.get('route_plan') is not None and not q.get('ignored') for q in ps):
                from .reconstruction_route import parts
                mid_union=shapely.union_all([b['nominal'] for b in between_blocks[between_start:]])
                ep_items=[]
                for q in ps:
                    if q.get('route_plan') is None or q.get('ignored'):continue
                    if 'serial_left' in q and q.get('endpoint_weights',(1.,0.))[0]<1.-1e-10:continue
                    c_nom=q['nominal_regions']['C']
                    if not mid_union.is_empty:c_nom=c_nom.difference(mid_union)
                    for g in parts(c_nom):
                        if g.area>self.tol**2:ep_items.append(('moving','follow',q['die'],g,q))
                if ref_pose is None:
                    b_nom=shapely.union_all([q['nominal_regions']['B'] for q in ps if q.get('route_plan') is not None and not q.get('ignored')])
                    if not mid_union.is_empty:b_nom=b_nom.difference(mid_union)
                    for g in parts(b_nom):
                        if g.area>self.tol**2:
                            near_q=min((q for q in ps if q.get('route_plan') is not None),key=lambda q:q['nominal_regions']['A'].distance(g),default=ps[0])
                            ep_items.append(('fixed',STATIONARY,near_q['die'],g,None))
                if ep_items:
                    ep_comps={idx:item[3] for idx,item in enumerate(ep_items)}
                    ep_defs={idx:item[1] for idx,item in enumerate(ep_items)}
                    ep_res=endpoint_modes(self.settings,(layer,index),ep_comps,ep_defs,self.tol)
                    for idx,(ep_kind,ep_def,ep_owner,ep_nom,ep_q) in enumerate(ep_items):
                        ep_mode,ep_custom=ep_res[idx]
                        ep_center=self.model.bound.centers[ep_owner]
                        ep_state=states[ep_owner]
                        posed_nom=transform(ep_nom,ep_center,ep_state) if np.any(ep_state) else ep_nom
                        if ep_kind=='moving' and ep_mode==STATIONARY:
                            ep_geom=ep_nom
                            if ep_q is not None and ep_mode!=ep_def:
                                geometry=shapely.union_all([geometry.difference(posed_nom),ep_nom],grid_size=self.tol)
                                ep_q['regions']['C']=ep_q['regions']['C'].difference(posed_nom)
                                ep_q['moved']=ep_q['regions']['C']
                                ep_q['regions']['B']=ep_q['regions']['B'].union(ep_nom)
                                ep_q['fixed']=ep_q['regions']['B']
                        elif ep_kind=='fixed' and ep_mode=='follow':
                            ep_geom=posed_nom
                            if ep_mode!=ep_def:
                                geometry=shapely.union_all([geometry.difference(ep_nom),posed_nom],grid_size=self.tol)
                                for q in ps:
                                    q['regions']['B']=q['regions']['B'].difference(ep_nom)
                                    q['fixed']=q['regions']['B']
                                ps[0]['regions']['C']=ps[0]['regions']['C'].union(posed_nom)
                                ps[0]['moved']=ps[0]['regions']['C']
                        else:
                            ep_geom=posed_nom if ep_mode=='follow' else ep_nom
                        ax,ay=anchor_of(ep_nom)
                        endpoint_blocks.append(dict(
                            ref=(layer,index),id=(layer,index,ax,ay),kind=ep_kind,
                            default_mode=ep_def,mode=ep_mode,custom=ep_custom,
                            nominal=ep_nom,geometry=ep_geom,owner=ep_owner,
                            moves=(ep_mode=='follow' and ep_owner in self.active_dies),locked=False,
                        ))
            out.setdefault(layer,[]).append(dict(indices=(index,),geometry=geometry,net=feature['net'],movable=occupied,moved=shapely.union_all([q['moved'] for q in ps])))
        for layer,rows in retained.items():out.setdefault(layer,[]).extend(rows)
        pieces.extend(retained_pieces)
        # Measure against final neighbouring copper, not source poses.
        for layer in (out if diagnostics else ()):
            replaced={q['indices'][0]:q['geometry'] for q in out[layer]}
            copper=[replaced.get(i,g) for i,g in enumerate(self.model.cache.layers[layer]['geometry'])]
            nets=np.array([int(self.model.nodes[self.model.ids[layer,i]]['net']) for i in range(len(copper))],int)
            tree=shapely.STRtree(copper)
            layer_pieces=[q for q in pieces if q['layer']==layer]
            def measure(item):
                idx,net=item
                if not np.any(nets!=net):return idx,None
                probe=shapely.from_wkb(replaced[idx].wkb)
                shapely.prepare(probe)
                nearby=tree.query(probe,predicate='dwithin',distance=self.rule_space)
                foreign=[int(j) for j in nearby if nets[int(j)]!=net]
                if foreign:
                    dists=shapely.distance(probe,[copper[j] for j in foreign])
                    best=int(np.argmin(dists))
                    i=foreign[best];gap=float(dists[best])
                else:
                    # No foreign copper within rule_space; find nearest foreign only via fallback when needed
                    others=np.flatnonzero(nets!=net)
                    if not len(others):return idx,None
                    indices=tree.query_nearest(probe,all_matches=True)
                    f_near=[int(j) for j in indices if nets[int(j)]!=net]
                    if f_near:
                        dists=shapely.distance(probe,[copper[j] for j in f_near])
                        best=int(np.argmin(dists));i=f_near[best];gap=float(dists[best])
                    else:
                        dists=shapely.distance(probe,[copper[int(j)] for j in others])
                        best=int(np.argmin(dists));i=int(others[best]);gap=float(dists[best])
                return idx,dict(clearance_um=gap,conflict=gap<self.rule_space-self.tol,conflict_net=int(nets[i]),
                                conflict_link=shapely.get_coordinates(shapely.shortest_line(probe,copper[i])).tolist())
            measures=dict(parallel_map(measure,sorted({(q['index'],q['net']) for q in layer_pieces})))
            for q in layer_pieces:
                if measures[q['index']] is not None:q.update(measures[q['index']]);conflicts+=int(q['conflict'])
        result=dict(complete=True,reused_sources=len(retained_refs),auto_ignored=self.auto_ignored,rules=dict(width_um=self.rules.width,space_um=self.rules.space,annulus_um=self.p.annulus_um),configuration=self.settings.reconstruction_key(),dbu_um=self.tol,pieces=pieces,replacements=out,between_blocks=tuple(between_blocks),endpoint_blocks=tuple(endpoint_blocks),scope='共同端点按全输入概率参考选择；保持所选样式比较有限候选。未形成有效连接时取消连接段并保留两端；实际工况规则另检。不代表全局最优布线或产线签认。',
                    parameters=dict(length_um=0.,width_um=0.,overlap_um=self.p.annulus_um,landing_diameters=AUTO_LANDING_DIAMETERS,shared_nominal=True,connection_style=self.settings.reconstruction_style,boundary_mode=self.settings.reconstruction_boundary_mode),
                    summary=dict(pieces=len(pieces),bridges=sum(not q['bridge'].is_empty for q in pieces),orphans=sum(q['orphan'] for q in pieces),conflicts=conflicts,
                                 warnings=sum(bool(q['warning']) for q in pieces),groups_used=len(features),manual_cuts=sum(q['manual'] for q in pieces),
                                 rule_space_um=self.rule_space,movable_area_um2=sum(q['movable_area_um2'] for q in pieces),
                                 fixed_area_um2=sum(q['fixed_area_um2'] for q in pieces),cover_area_um2=sum(q['bridge_area_um2'] for q in pieces),
                                 max_shift_um=max((q['shift_um'] for q in pieces),default=0.),regions_used=len(self.settings.reconstruction_regions),regions_ambiguous=0))
        result['violations']=self.rules.check(out,states,progress_slice(progress,90,.09)) if diagnostics else []
        result['original_violations']=self.rules.nominal_check() if diagnostics else ()
        result['constraints_complete']=bool(diagnostics)
        decorate_problems(result)
        if diagnostics:
            if not np.any(states) and targets is None:self.cache.pin(key)
            self.cache[key]=result
        if targets is None:
            self._last_raw_frames[states.tobytes()]=result
            while len(self._last_raw_frames)>4:self._last_raw_frames.pop(next(iter(self._last_raw_frames)))
        if progress:progress(100,f'重构预演：{len(pieces)} 个共用动区')
        return result
