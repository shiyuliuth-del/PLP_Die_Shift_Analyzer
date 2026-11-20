"""Final electrical guard and bounded connected routing of actual copper.

Width/enclosure are soft repair targets; distinct nets, physical via bodies,
connected components and holes are hard constraints. No warning artwork is
ever used as fabricated copper.
"""
import math
import numpy as np
import shapely
from .resources import cancel_check
from .reconstruction_route import parts
from .routing_quality import Landing,pad_loss


def signature(g):
    polygons=parts(g)
    return len(polygons),sum(len(p.interiors) for p in polygons)


def failure_message(rows):
    details='；'.join(f'{layer} #{index+1}：{reason}' for (layer,index),reason in rows[:8])
    return '当前姿态无法生成保持电连接、原拓扑且无异网接触的完整图形，未提交该结果为完整制造图；诊断预览显示失败位置。'+details


def covers_ports(g,ports,tol):
    # Full enclosure is a process rule. Electrical continuity requires a real
    # positive-area contact, not merely coincident boundaries or a marker line.
    if not len(ports):return True
    ports=np.asarray(ports,dtype=object)
    # Exact containment is common; calculate overlaps only at partially
    # enclosed or missing ports. Tiny whole ports still need the area test.
    inside=shapely.covers(g,ports)&(shapely.area(ports)>tol*tol)
    return bool(np.all(shapely.area(shapely.intersection(g,ports[~inside]))>tol*tol))


def separate_pair(a,b,source_a,source_b,ports_a,ports_b,tol,space=0.,protected=((),())):
    """Share the overlap corridor; clipping only one pad can be impossible."""
    overlap=a.intersection(b)
    if overlap.is_empty:return a,b
    location=max(shapely.get_parts(overlap),key=lambda g:g.area+tol*g.length).representative_point()
    center=np.asarray(location.coords[0]);directions=[]
    if ports_a and ports_b:
        pa=min(ports_a,key=lambda g:g.distance(location));pb=min(ports_b,key=lambda g:g.distance(location))
        directions.append(np.asarray(pb.centroid.coords[0])-np.asarray(pa.centroid.coords[0]))
    from shapely.ops import nearest_points
    pa=nearest_points(source_a,location)[0];pb=nearest_points(source_b,location)[0]
    directions.append(np.asarray(pb.coords[0])-np.asarray(pa.coords[0]))
    directions.extend((np.array([1.,0.]),np.array([0.,1.])))
    rect=shapely.get_coordinates(shapely.minimum_rotated_rectangle(overlap))
    if len(rect)>2:directions.extend(np.diff(rect,axis=0)[:2])
    gap=max(tol*8,space);size=a.length+b.length+1;patch=overlap.buffer(gap+tol*8,quad_segs=4);best=None
    for normal in directions:
        length=np.linalg.norm(normal)
        if length<=tol:continue
        for sign in (-1.,1.):
            n=normal/length*sign;u=np.array([-n[1],n[0]])
            for shift in (0.,-.2,.2):
                c=center+shift*np.sqrt(max(overlap.area,tol*tol))*n
                edge0=c-n*gap/2;edge1=c+n*gap/2
                ha=shapely.Polygon([edge0-u*size,edge0+u*size,edge0+u*size-n*size,edge0-u*size-n*size])
                hb=shapely.Polygon([edge1-u*size,edge1+u*size,edge1+u*size+n*size,edge1-u*size+n*size])
                ga=shapely.union_all(parts(a.difference(patch.difference(ha))),grid_size=tol)
                gb=shapely.union_all(parts(b.difference(patch.difference(hb))),grid_size=tol)
                if (not ga.is_empty and not gb.is_empty and ga.is_valid and gb.is_valid
                    and signature(ga)==signature(source_a) and signature(gb)==signature(source_b)
                    and covers_ports(ga,ports_a,tol) and covers_ports(gb,ports_b,tol)
                    and not any(ga.intersects(p) for p in ports_b)
                    and not any(gb.intersects(p) for p in ports_a)
                    and not ga.intersects(gb)):
                    loss_a,loss_b=pad_loss(ga,protected[0],tol),pad_loss(gb,protected[1],tol)
                    score=(loss_a+loss_b,max(loss_a,loss_b),max((a.area-ga.area)/max(a.area,tol*tol),(b.area-gb.area)/max(b.area,tol*tol)))
                    if best is None or score<best[0]:best=(score,ga,gb)
    return None if best is None else best[1:]


def weld_interfaces(geometry,expected,tol):
    """Create actual copper at sub-grid seams before trying a new route."""
    components=parts(geometry)
    if len(components)<=expected[0]:return None
    tree=shapely.STRtree(components)
    i,j=tree.query(components,predicate='dwithin',distance=2*tol)
    patches=[]
    for a,b in zip(i,j):
        if b<=a:continue
        # Add only the strip jointly adjacent to both existing components.
        # Buffer-and-close of the complete copper would also change remote edges.
        l,r=components[a],components[b]
        shared=l.buffer(tol*2,quad_segs=2).intersection(r.buffer(tol*2,quad_segs=2))
        if shared.area>tol*tol:patches.append(shared)
    if not patches:return None
    candidate=shapely.union_all([geometry,*patches],grid_size=tol)
    if candidate.is_valid and signature(candidate)==expected:return candidate
    return None


def connected_candidate(source,current,ports,obstacles,board,width,tol,space=0.,*,require_full=False,reroute=True):
    """Clip foreign copper, then join separated material through real free space."""
    expected=signature(source);gap=max(tol*4,space)
    forbidden=obstacles.buffer(gap,quad_segs=4) if not obstacles.is_empty else obstacles
    if require_full:
        if any(p.intersection(forbidden).area>p.length*tol for p in ports):return None
        if board is not None and pad_loss(board,ports,tol)>0.:return None
    elif any(p.difference(forbidden).area<=tol*tol for p in ports):return None
    def covers(geometry):
        return pad_loss(geometry,ports,tol)==0. if require_full else covers_ports(geometry,ports,tol)
    if (current.is_valid and signature(current)==expected and covers(current)
        and not current.intersects(obstacles) and (board is None or board.buffer(tol).covers(current))):return current
    full=shapely.union_all([current,*ports],grid_size=tol)
    if not forbidden.is_empty:full=full.difference(forbidden,grid_size=tol)
    if board is not None:full=full.intersection(board,grid_size=tol)
    full=shapely.union_all(parts(shapely.make_valid(full)),grid_size=tol)
    if not covers(full):return None
    if expected[0]==1 and len(parts(full))>1:
        # Clipping with a nearby obstacle may leave grid-edge flakes. They
        # are not intended terminals or separate components of the source.
        cleaned=[g for g in parts(full) if g.area>g.length*tol or any(g.intersects(p) for p in ports)]
        full=shapely.union_all(cleaned,grid_size=tol)
    # A new hole inside originally solid copper may be a folded overlap. Fill
    # it only if the area contains no foreign net or other hard obstacle.
    if expected[1]==0:
        fills=[shapely.Polygon(h) for p in parts(full) for h in p.interiors]
        if fills and all(not h.intersects(forbidden) for h in fills):full=shapely.union_all([full,*fills],grid_size=tol)
    def acceptable(g):
        return (not g.is_empty and g.is_valid and signature(g)==expected and covers(g)
                and not g.intersects(obstacles) and (board is None or board.buffer(tol).covers(g)))
    if acceptable(full):return full
    welded=weld_interfaces(full,expected,tol)
    if welded is not None and acceptable(welded):return welded
    # Keep every material component and its terminal identity. A connector is
    # accepted only when it decreases the component count without adding holes.
    if not reroute or len(parts(full))<=expected[0] or expected[0]!=1:return None
    bounds=shapely.union_all([source,full]).bounds
    for padding in (max(width*2,tol*16),max(width*8,tol*32)):
        region=shapely.box(bounds[0]-padding,bounds[1]-padding,bounds[2]+padding,bounds[3]+padding)
        if board is not None:region=region.intersection(board)
        free=region.difference(forbidden);working=full
        cores={}
        for _ in range(max(1,len(parts(full))-1)):
            cancel_check();components=parts(working)
            if len(components)==1:break
            pairs=sorted((a.distance(b),i,j) for i,a in enumerate(components) for j,b in enumerate(components) if i<j)
            joined=None
            for _,i,j in pairs:
                for diameter in dict.fromkeys((width,width*.75,width*.5,width*.25,width*.125,max(tol*8,width*.0625),tol*4)):
                    cancel_check();radius=max(tol*2,diameter/2)
                    if radius not in cores:cores[radius]=parts(free.buffer(-radius,quad_segs=4))
                    for domain in cores[radius]:
                        a=components[i].intersection(domain);b=components[j].intersection(domain)
                        if not a.is_empty and not b.is_empty:break
                    else:continue
                    ends=shapely.shortest_line(a,b)
                    from .reconstruction_route import trace
                    line=None
                    start,end=ends.coords[0],ends.coords[-1]
                    # Most repairs are near one cut. Restrict the first
                    # search to that throat before tracing across the full net.
                    x0,y0,x1,y1=ends.bounds
                    margin=max(width*4,tol*32)
                    local=domain.intersection(shapely.box(x0-margin,y0-margin,x1+margin,y1+margin))
                    if domain.covers(ends):line=ends
                    elif not local.equals(domain):
                        line=trace(local,start,end,diameter,visibility_budget=4096)
                    if line is None:line=trace(domain,start,end,diameter,visibility_budget=8192)
                    if line is None:continue
                    wire=line.buffer(radius,quad_segs=8,join_style=1)
                    candidate=shapely.union_all([working,wire],grid_size=tol)
                    if (candidate.is_valid and len(parts(candidate))<len(components)
                        and signature(candidate)[1]==expected[1] and not candidate.intersects(obstacles)
                        and covers(candidate)):
                        joined=candidate;break
                if joined is not None:break
            if joined is None:break
            working=joined
        if acceptable(working):return working
    return None


def cached_connection(r,source,current,ports,obstacles,board,width,tol,space,require_full=False,reroute=True):
    """Repeat local scenes across package combinations without re-routing.

    Actual posed obstacles and every physical terminal are part of the key;
    reuse never substitutes nominal neighbours for a changed neighbour.
    """
    cancel_check()
    blobs=(source.wkb,current.wkb,tuple(p.wkb for p in ports),obstacles.wkb,None if board is None else board.wkb)
    key=('electrical-local-repair-v58',*blobs,float(width),float(tol),float(space),require_full,reroute)
    value=r.geometry_pool.get(key)
    if value is not None:return None if value[0] is None else shapely.from_wkb(value[0])
    result=connected_candidate(source,current,ports,obstacles,board,width,tol,space,require_full=require_full,reroute=reroute)
    blob=None if result is None else result.wkb
    size=512+sum(len(b) for b in (blobs[0],blobs[1],*blobs[2],blobs[3],blobs[4] or b'',blob or b''))*2
    r.geometry_pool.put(key,(blob,),size=size)
    return result


def _adopt(rows,geometry,tol,reason):
    """Keep material regions disjoint and attach compensation to its real A area."""
    def material(g):
        values=parts(g)
        if len(values)==1:return values[0]
        return shapely.union_all(values,grid_size=tol)
    def overlay(a,b,op):
        return material(getattr(shapely,op)(a,b,grid_size=tol))
    before=shapely.union_all([g for q in rows for g in q['regions'].values()],grid_size=tol)
    delta=before.symmetric_difference(geometry,grid_size=tol)
    if delta.area<=tol*tol:reason='原铜已包含当前全部连接端，保留原图；未伪装成型变'
    fixed=overlay(shapely.union_all([q['regions']['B'] for q in rows],grid_size=tol),geometry,'intersection')
    moving=overlay(overlay(shapely.union_all([q['regions']['C'] for q in rows],grid_size=tol),geometry,'intersection'),fixed,'difference')
    remaining=overlay(geometry,shapely.union_all([fixed,moving],grid_size=tol),'difference')
    if len(rows)==1:
        regions=[remaining]
    else:
        assigned=shapely.Polygon();regions=[]
        support=[q['regions']['A'] if not q['regions']['A'].is_empty else q.get('missing_region',shapely.Polygon()) for q in rows]
        for g in support:
            a=overlay(overlay(remaining,g.buffer(tol*4),'intersection'),assigned,'difference')
            regions.append(a);assigned=overlay(assigned,a,'union')
        extras=[[] for _ in rows];leftovers=parts(overlay(remaining,assigned,'difference'))
        if leftovers:
            for g in leftovers:
                index=min(range(len(rows)),key=lambda i:support[i].distance(g) if not support[i].is_empty else float('inf'))
                # Sub-grid residuals next to the actual rigid outline stay rigid.
                if g.area<=tol*g.length*2 and not (fixed.is_empty and moving.is_empty):
                    near=overlay(g,support[index].buffer(tol*8),'intersection') if not support[index].is_empty else shapely.Polygon()
                    far=overlay(g,near,'difference');extras[index].append(near)
                    if moving.is_empty or (not fixed.is_empty and fixed.distance(far)<=moving.distance(far)):fixed=overlay(fixed,far,'union')
                    else:moving=overlay(moving,far,'union')
                else:extras[index].append(g)
            regions=[shapely.union_all([g,*items],grid_size=tol) if items else g for g,items in zip(regions,extras)]
    for q,a in zip(rows,regions):
        for name in ('moved_cut','extra_cut'):
            cut=q.get(name)
            if cut is not None and cut.geom_type not in ('Point','MultiPoint'):
                q[name]=cut.intersection(geometry.buffer(tol*.05))
        q.update(regions=dict(A=a,B=fixed,C=moving),bridge=a,fixed=fixed,moved=moving,
                 bridge_area_um2=a.area,fixed_area_um2=fixed.area,moved_area_um2=moving.area,
                 connection_failed=False,valid=True,routing_recovered=True,routing_note=reason,
                 warning=reason,compensation_region=delta.intersection(a.buffer(tol*4),grid_size=tol))


def resolve(r,replacements,pieces,states,progress=lambda *_:None):
    """Deterministic whole-pose collision resolution; immutable source identities."""
    out={k:[dict(v) for v in values] for k,values in replacements.items()}
    pieces=[dict(q,regions=dict(q['regions'])) for q in pieces];by_ref={};ports={}
    for q in pieces:by_ref.setdefault((q['layer'],q['index']),[]).append(q)
    inputs=getattr(r,'_topology_inputs',None)
    if inputs is None:
        original={};nets={};terminals={};lands={}
        for key,refs in r.rules.nets.items():
            for ref in refs:
                original[ref]=r.model.nodes[r.model.ids[ref]]['poly'];nets[ref]=key;terminals[ref]=[];lands[ref]=[]
                for via,owner,required in r.rules.vias[key]:
                    physical=r.model.nodes[r.model.ids[via]]['poly']
                    if original[ref].intersection(physical).area<=r.tol*r.tol:continue
                    terminals[ref].append((via,owner,physical))
                    lands[ref].append(Landing.from_source(physical,required,original[ref],r.tol))
        r._topology_inputs=inputs=(original,nets,terminals,lands)
    original,nets,terminals,lands=inputs
    from .ignored_motion import linked_owners
    linked,link_failures=linked_owners(r,states)
    def actual_owner(node):
        return linked.get((node['key'],node['index']),node['owner'])
    # GEOS prepared predicates have mutable native indexes. Frame geometries
    # can also have been prepared by a different overlay/worker; keep native
    # query operands private to this invocation, including nominal copper.
    def private(g):return shapely.from_wkb(g.wkb)
    objects=dict(zip(original,shapely.from_wkb(shapely.to_wkb(list(original.values())))))
    for layer,rows in out.items():
        for row in rows:
            for i in row['indices']:objects[layer,i]=private(row['geometry'])
    mutable={ref for ref,rows in by_ref.items() if any(not q.get('ignored') for q in rows)}
    enclosures={};protected={}
    for ref in objects:
        ports[ref]=[];enclosures[ref]=[];protected[ref]=[]
        for (via,owner,_),landing in zip(terminals[ref],lands[ref]):
            owner=linked.get(via,owner)
            ports[ref].append(private(r.model.moved(landing.via,owner,states)))
            enclosures[ref].append(private(r.model.moved(landing.enclosure,owner,states)))
            protected[ref].append(private(r.model.moved(landing.protected,owner,states)))
    manual_masks={ref:shapely.union_all([q['regions']['A'] for q in rows]) for ref,rows in by_ref.items() if any(q.get('strict_box') is not None for q in rows)}
    manual_before={ref:objects[ref] for ref in manual_masks}
    def preserves_box(ref,g):
        return ref not in manual_masks or g.symmetric_difference(manual_before[ref]).difference(manual_masks[ref].buffer(r.tol*2)).area<=r.tol*r.tol
    changed=set();repairs={};blocked=list(link_failures);external={};immutable={}
    for ref,values in ports.items():
        external.setdefault(nets[ref][0],[]).extend((nets[ref],g) for g in values)
    for node in r.model.nodes:
        if r.model.roles[node['key']]=='固定RDL':continue
        key=(node['key'].split('/')[0],node['net']);g=private(r.model.moved(node['poly'],actual_owner(node),states))
        external.setdefault(key[0],[]).append((key,g));immutable.setdefault(key[0],[]).append((node,g))
    external={plane:(values,shapely.STRtree([g for net,g in values])) for plane,values in external.items()}
    def foreign_hits(ref,g):
        table=external.get(nets[ref][0])
        if table is None:return []
        values,tree=table
        return [values[int(i)][1] for i in tree.query(g,predicate='intersects') if values[int(i)][0]!=nets[ref]]
    def obstacles(ref):
        b=shapely.union_all([original[ref],objects[ref]]).bounds;pad=r._rule_width*8+r.rule_space+r.tol*32
        box=shapely.box(b[0]-pad,b[1]-pad,b[2]+pad,b[3]+pad)
        # The search cannot leave its width*8 window; retain an extra
        # spacing+DBU halo before clipping. Remote parts of a long neighbour
        # then cannot invalidate an otherwise identical local repair cache.
        return shapely.union_all([g.intersection(box) for other,g in objects.items() if nets[other][0]==nets[ref][0] and nets[other]!=nets[ref] and g.intersects(box)]+[r.rules.keepout.intersection(box),*[g.intersection(box) for g in foreign_hits(ref,box)]])
    def enters_keepout(g):return not r.rules.keepout.is_empty and shapely.relate_pattern(g,r.rules.keepout,'T********')
    def acceptable(ref,candidate):
        return (preserves_box(ref,candidate) and not foreign_hits(ref,candidate) and
                same_connections(original[ref],candidate,[g for _,_,g in terminals[ref]],ports[ref],r.tol))
    def attempt(ref,reason,*,pad_only=False):
        if ref not in mutable or any(q.get('box_invalid') for q in by_ref.get(ref,())):return None
        foreign=obstacles(ref)
        # Distinct nets, connectivity and topology are hard. Within feasible
        # copper, exhaust clearance choices for a full pad before trimming it.
        goal_sets=(protected[ref],enclosures[ref],ports[ref])
        seen=set()
        for targets in goal_sets:
            key=tuple(g.wkb for g in targets)
            if key in seen:continue
            seen.add(key)
            for space in dict.fromkeys((r.rule_space,r.rule_space*.5,0.)):
                candidate=cached_connection(r,original[ref],objects[ref],targets,foreign,r.rules.board,r._rule_width,r.tol,space,True,not pad_only)
                if candidate is not None and acceptable(ref,candidate):
                    return candidate,reason
        if pad_only:return None
        for space in dict.fromkeys((r.rule_space,r.rule_space*.5,0.)):
            candidate=cached_connection(r,original[ref],objects[ref],ports[ref],foreign,r.rules.board,r._rule_width,r.tol,space)
            if candidate is not None and acceptable(ref,candidate):
                return candidate,reason
        return None
    from .topology_checks import inspect_many,foreign_pairs,same_connections
    checked=inspect_many(r,objects,original,ports,manual_masks,manual_before,terminals)
    # Recover electrical discontinuities first; the actual posed foreign copper
    # is an obstacle even when full native DRC artwork was not requested.
    for number,ref in enumerate(sorted(mutable)):
        cancel_check();progress(round(40*number/max(1,len(mutable))),f'核验实际连接 {number+1}/{len(mutable)} · {ref[0]} #{ref[1]+1}')
        current=objects[ref]
        collided=foreign_hits(ref,current)
        missing=bool(np.any(states) and not all(p.difference(current).area<=p.length*r.tol for p in protected[ref]))
        already_connected=checked[ref][0] and not checked[ref][1] and not collided and not any(q.get('connection_failed') for q in by_ref[ref])
        if already_connected and not missing:continue
        candidate=attempt(ref,'保连通补偿：已调整连接轮廓，工艺规则另检',pad_only=already_connected)
        if candidate is not None and (not checked[ref][0] or collided or checked[ref][1] or
                pad_loss(candidate[0],protected[ref],r.tol)<pad_loss(current,protected[ref],r.tol) or
                any(q.get('connection_failed') for q in by_ref[ref])):
            objects[ref]=candidate[0];changed.add(ref);repairs[ref]=candidate[1]
    # Removing one side of an overlap cannot create a new foreign contact.
    # A rerouted candidate is checked against every current neighbour before use.
    refs=list(objects)
    for iteration in range(max(1,len(refs)*2)):
        cancel_check();progress(40+round(30*iteration/max(1,len(refs)*2)),f'消除异网接触 · 第 {iteration+1} 轮');pairs=foreign_pairs(objects,nets)
        if not pairs:break
        progressed=False
        for left,right in pairs:
            if not objects[left].intersects(objects[right]):continue
            candidates=[]
            def offer(changes,reason):
                losses=[pad_loss(changes.get(ref,objects[ref]),protected[ref],r.tol) for ref in (left,right)]
                clearance=changes.get(left,objects[left]).distance(changes.get(right,objects[right]))
                edit=sum(objects[ref].symmetric_difference(g).area for ref,g in changes.items())
                candidates.append(((sum(losses),max(losses),max(0.,r.rule_space-clearance),edit),changes,reason))
            if left in mutable and right in mutable:
                for space in dict.fromkeys((r.rule_space,r.rule_space*.5,0.)):
                    candidate=separate_pair(objects[left],objects[right],original[left],original[right],ports[left],ports[right],
                                            r.tol,space,(protected[left],protected[right]))
                    if candidate is not None and all(acceptable(ref,g) for ref,g in zip((left,right),candidate)):
                        offer(dict(zip((left,right),candidate)),'共同通道补偿：保留可行孔盘并调整线路')
                        if candidates[-1][0][0]==0.:break
            if not candidates or candidates[0][0][0]>0.:
                for ref in (left,right):
                    candidate=attempt(ref,'异网避让补偿：局部收窄或绕接，工艺规则另检')
                    if candidate is not None:offer({ref:candidate[0]},candidate[1])
            if not candidates:continue
            _,changes,reason=min(candidates,key=lambda item:item[0])
            for ref,g in changes.items():
                objects[ref]=g;changed.add(ref);repairs[ref]=reason
            progressed=True
        if not progressed:break
    if np.any(states):
        from .drc_policy import enabled
        from .routing_quality import refine
        width=r.rules.width if enabled(r.settings,'width') else 0.
        angle=r.settings.drc_min_angle_deg if enabled(r.settings,'angle') else 0.
        for ref in sorted(mutable if width or angle else ()):
            cancel_check()
            current=objects[ref]
            if not current.is_valid or not acceptable(ref,current) or enters_keepout(current):continue
            active=shapely.union_all([q['regions']['A'] for q in by_ref[ref]])
            if active.is_empty or not any(np.any(q.get('relative_pose',states[q['die']])) for q in by_ref[ref]):continue
            foreign=obstacles(ref)
            gap=min(r.rule_space,current.distance(foreign)) if not foreign.is_empty else r.rule_space
            keep=shapely.union_all(protected[ref])
            free=active.buffer(r._rule_width,quad_segs=4).difference(foreign.buffer(max(r.tol*4,gap-r.tol)))
            if r.rules.board is not None:free=free.intersection(r.rules.board)
            loss=pad_loss(current,protected[ref],r.tol)
            def valid(candidate):
                return (candidate.is_valid and acceptable(ref,candidate) and
                        not candidate.intersects(foreign) and not enters_keepout(candidate) and
                        (r.rules.board is None or r.rules.board.buffer(r.tol).covers(candidate)) and
                        candidate.distance(foreign)>=gap-r.tol*2 and
                        pad_loss(candidate,protected[ref],r.tol)<=loss+r.tol*r.tol)
            candidate=refine(current,active,keep,free,r._rule_width,width,angle,r.tol,valid)
            if candidate is not current:
                objects[ref]=candidate;changed.add(ref)
                repairs[ref]='保留孔盘及连接，局部扩宽并修整转角'
    for ref,reason in repairs.items():
        _adopt(by_ref[ref],objects[ref],r.tol,reason)
    if changed:checked.update(inspect_many(r,{ref:objects[ref] for ref in changed},original,ports,manual_masks,manual_before,terminals))
    previous_percent=-1
    for number,ref in enumerate(sorted(objects)):
        percent=70+round(20*number/max(1,len(objects)))
        if percent!=previous_percent:progress(percent,f'最终连接核验 {number+1}/{len(objects)}');previous_percent=percent
        if any(q.get('box_invalid') for q in by_ref.get(ref,())):blocked.append((ref,'框选动区没有切断运动—固定连接路径，严格区域不能完成适配'))
        if not checked[ref][0]:blocked.append((ref,'无法获得包含全部过孔且保持原拓扑的连通铜'))
        if checked[ref][1]:blocked.append((ref,'固定RDL进入禁布区，无法在保持电连接的同时避让'))
        if foreign_hits(ref,objects[ref]):blocked.append((ref,'固定RDL仍接触其他网络的实际过孔或同层金属'))
    for left,right in foreign_pairs(objects,nets):
        blocked.extend(((left,'异网实体接触仍无法避让'),(right,'异网实体接触仍无法避让')))
    for plane,values in immutable.items():
        for node,g in values:
            if (r.model.roles[node['key']]=='运动RDL' or (node['key'],node['index']) in linked) and enters_keepout(g):blocked.append(((node['key'],node['index']),'运动RDL进入禁布区，无法通过固定RDL改线消除'))
        nodes=[n for n,g in values]
        if len({actual_owner(n) for n in nodes})<2:continue
        actual=[g for n,g in values];tree=shapely.STRtree(actual)
        for i,g in enumerate(actual):
            for j in tree.query(g,predicate='intersects'):
                if j>i and nodes[i]['net']!=nodes[int(j)]['net']:
                    blocked.extend((((n['key'],n['index']),'同层异网金属实体接触，无法通过固定RDL改线消除') for n in (nodes[i],nodes[int(j)])))
    from .local_failures import locate
    for ref,reason in blocked:
        rows=by_ref.get(ref,())
        if not rows:continue
        ids,witness=locate(rows,objects[ref],ports[ref],foreign_hits(ref,objects[ref])+[objects[other] for other in refs if other!=ref and nets[other][0]==nets[ref][0] and nets[other]!=nets[ref] and objects[other].intersects(objects[ref])],r.rules.keepout,r.tol)
        for i,q in enumerate(rows):
            if i in ids:q.update(connection_failed=True,valid=False,warning=reason,hard_unroutable=True,failure_witness=witness)

    for layer,rows in out.items():
        for row in rows:
            if len(row['indices'])==1:row['geometry']=objects[layer,row['indices'][0]]
    progress(100,f'电连接核验完成 · {len(set(blocked))} 项未完成连接')
    return out,pieces,tuple(sorted(changed)),tuple(sorted(set(blocked)))


def spacing(r,out,pieces):
    geos=r.rules.geometries(out);planes={}
    for key,g in geos.items():
        if not g.is_empty:planes.setdefault(key[0],[]).append((key,g))
    tables={plane:(values,shapely.STRtree([g for key,g in values])) for plane,values in planes.items() if len(values)>1}
    measured={}
    for q in pieces:
        key=r.rules.refnet[q['layer'],q['index']]
        table=tables.get(key[0]);copper=geos[key]
        if table is None or copper.is_empty:continue
        if key not in measured:
            values,tree=table
            # First include exact coincident geometry from a different net.
            hits=[j for j in tree.query(copper,predicate='intersects') if values[int(j)][0]!=key]
            indices=hits if hits else tree.query_nearest(copper,exclusive=True)
            net,other=values[int(min(indices))]
            distance=copper.distance(other)
            measured[key]=dict(clearance_um=distance,conflict=distance<r.rule_space-r.tol,conflict_net=net[1],
                               conflict_link=shapely.get_coordinates(shapely.shortest_line(copper,other)).tolist())
        q.update(measured[key])
