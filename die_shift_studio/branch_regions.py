"""Connected transition domains with rigid-component displacement potentials.

A path cover proposes cuts; connected components ensure no moving/fixed path
bypasses adaptation. Floating metal between serial cuts gets a Laplace weight,
so it is neither duplicated nor arbitrarily pinned to one endpoint.
"""
import numpy as np
import shapely
from .reconstruction_route import parts,clean_overlay


def terminal_tree(moving,fixed):
    """Connected bipartite cover of ALL terminals, without Cartesian duplicates."""
    if not moving or not fixed:return []
    count=len(moving);parents=list(range(count+len(fixed)))
    def root(i):
        while parents[i]!=i:parents[i]=parents[parents[i]];i=parents[i]
        return i
    edges=sorted((a.distance(b),i,j) for i,a in enumerate(moving) for j,b in enumerate(fixed))
    result=[]
    for _,i,j in edges:
        x,y=root(i),root(count+j)
        if x==y:continue
        parents[x]=y;result.append((i,j))
        if len(result)==len(parents)-1:break
    return result


def interior_interfaces(source,domain,rigid,tolerance):
    """Only material-to-material seams are controls, never source perimeter.

    Snapped Boolean operations can leave sub-DBU slivers along the exterior.
    Buffering a domain to recover coincident seams otherwise picks the whole
    sliver contour and transforms it into a long, detached drag handle.
    This display-only trim leaves copper partitions and exported seams intact.
    """
    exterior=source.boundary.buffer(tolerance*4)
    interfaces=[g.boundary.intersection(domain.buffer(tolerance*.05)).difference(exterior) for g in rigid]
    lines=[line for g in interfaces for line in shapely.get_parts(g)
           if line.geom_type=='LineString' and line.length>tolerance*8]
    from .straight_controls import straight
    return straight(shapely.line_merge(shapely.union_all(lines)),tolerance)


def transition_domains(source,rows,tol):
    """Split by every real seam before classifying path interiors.

    Unioning whole per-path partitions swallowed unrelated forks/terminals.
    A component belongs to A only when an interior route interval visits it.
    """
    from shapely.ops import split,substring
    if any(q.get('strict_box') is not None for q in rows):
        return shapely.union_all([q['nominal_regions']['A'] for q in rows])
    components=parts(source);intervals=[];blades=[]
    for row in rows:
        plan=row['route_plan'];a,b=row['interval'];eps=min(tol*8,(b-a)*.05)
        if b-a>tol:intervals.append(substring(plan.route,a+eps,b-eps))
        part=plan.partition(a,b,row.get('boundary_angles',(0.,0.)))
        if part is None:continue
        for name in ('near','far'):
            face=part[name]
            if face.geom_type!='LineString':continue
            xy=np.asarray(face.coords);v=xy[-1]-xy[0];v/=max(np.linalg.norm(v),tol)
            blades.append(shapely.LineString([xy[0]-v*tol*32,xy[-1]+v*tol*32]))
    for blade in blades:
        components=[piece for g in components for piece in (parts(split(g,blade)) if g.intersects(blade) else [g])]
    return shapely.union_all([g for g in components if any(g.intersection(line).length>tol*4 for line in intervals)])


def assemble(r,source,rows,states):
    from .reconstruction import transform
    owner=rows[0]['die'];pose=states[owner];center=r.model.bound.centers[owner]
    moving=shapely.union_all([q['moving_nominal'] for q in rows]);fixed=shapely.union_all([q['fixed_nominal'] for q in rows])
    active=transition_domains(source,rows,r.tol);rigid=[];known={}
    physical_moving=shapely.union_all([g for q in rows for g in getattr(q['route_plan'],'terminal_vias',((moving,),(fixed,)))[0]])
    physical_fixed=shapely.union_all([g for q in rows for g in getattr(q['route_plan'],'terminal_vias',((moving,),(fixed,)))[1]])
    for g in parts(source.difference(active)):
        # Enclosure masks may overlap a cut by a sub-DBU sliver. Only actual
        # terminal bodies decide ownership; those masks cannot swallow a fork.
        m=g.intersection(physical_moving).area>r.tol*r.tol;f=g.intersection(physical_fixed).area>r.tol*r.tol
        if m and f and not any(q.get('strict_box') is not None for q in rows):active=shapely.union_all([active,g]);continue
        index=len(rigid);rigid.append(g)
        if m or f:known[index]=float(m)
    domains=sorted(parts(clean_overlay(active,r.tol)),key=lambda g:g.bounds);physical_count=len(rigid)
    # Real terminal regions provide Dirichlet conditions even in whole-pad mode.
    for g,v in ((moving,1.),(fixed,0.)):
        known[len(rigid)]=v;rigid.append(g)
    neighbours=[];edges={}
    for domain in domains:
        ids=[i for i,g in enumerate(rigid) if not g.is_empty and domain.distance(g)<=r.tol*4]
        neighbours.append(ids)
        for ix,i in enumerate(ids):
            for j in ids[ix+1:]:edges[i,j]=edges.get((i,j),0.)+1/max(r._rule_width,rigid[i].distance(rigid[j]))
    unknown=[i for i in range(physical_count) if i not in known];index={v:i for i,v in enumerate(unknown)}
    mat=np.zeros((len(unknown),len(unknown)));rhs=np.zeros(len(unknown))
    for (i,j),weight in edges.items():
        for a,b in ((i,j),(j,i)):
            if a not in index:continue
            mat[index[a],index[a]]+=weight
            if b in index:mat[index[a],index[b]]-=weight
            else:rhs[index[a]]+=weight*known[b]
    values=dict(known)
    if unknown:
        solved=np.linalg.lstsq(mat,rhs,rcond=None)[0]
        values.update((i,float(np.clip(v,0,1))) for i,v in zip(unknown,solved))
    mobile=[i for i in range(physical_count) if values[i]>1e-12];static=[i for i in range(physical_count) if i not in mobile]
    c=shapely.union_all([rigid[i] for i in mobile]);b=shapely.union_all([rigid[i] for i in static])
    cposed=shapely.union_all([clean_overlay(transform(rigid[i],center,pose*values[i]),r.tol) for i in mobile]);style=r.settings.reconstruction_style
    def deform(g,ids):
        if not np.any(pose):return g
        dense=shapely.segmentize(g,max(r.tol*8,min(r._rule_width/3,5.)))
        def coordinates(xy):
            points=shapely.points(xy);distances=np.asarray([shapely.distance(points,rigid[i]) for i in ids])
            power={'linear':1,'c1':2,'c2':3,'arc':2}[style]
            weights=np.maximum(distances,r.tol*1e-6)**(-power);weights/=weights.sum(axis=0)
            fraction=np.asarray([values[i] for i in ids])@weights
            from .preview import rigid_transform
            # Interpolated angle and translation match each floating rigid block
            # exactly at its boundary; smoothing order sets the endpoint slope.
            v=xy-center;angles=pose[2]*fraction;co=np.cos(angles);si=np.sin(angles)
            return np.column_stack([co*v[:,0]-si*v[:,1],si*v[:,0]+co*v[:,1]])+center+fraction[:,None]*pose[:2]
        return shapely.transform(dense,coordinates)
    result=[]
    for domain_index,(group,ids) in enumerate(zip(domains,neighbours)):
        from shapely.ops import substring
        members=[q for q in rows if (q.get('strict_box') is not None and group.intersection(q['nominal_regions']['A']).area>r.tol*r.tol) or group.intersection(substring(q['route_plan'].route,q['interval'][0]+r.tol*4,q['interval'][1]-r.tol*4)).length>r.tol*4]
        if not members or not ids:continue
        q=dict(members[0]);posed=deform(group,ids);valid=posed.is_valid and posed.geom_type=='Polygon'
        flow_steps=0
        if not valid:
            from .continuous_warp import domain
            alternative,flow_steps=domain(group,[rigid[i] for i in ids],[values[i] for i in ids],center,pose,r._rule_width,r.tol)
            if alternative is None:
                # Large common copper can contain overlapping moving/fixed
                # landing reservations. Those overlapping virtual reservations
                # cannot both be rigid. Preserve the real material interfaces;
                # terminal coverage is checked on the resulting physical net.
                physical=[i for i in ids if i<physical_count]
                if physical:alternative,flow_steps=domain(group,[rigid[i] for i in physical],[values[i] for i in physical],center,pose,r._rule_width,r.tol)
            if alternative is not None:posed=alternative;valid=True
        if any(q.get('box_invalid') for q in members):valid=False
        if not valid:posed=shapely.Polygon()
        high=max(values[i] for i in ids);low=min(values[i] for i in ids)
        def face(weight,side):
            # Difference/union can leave ulp-different seam coordinates. A
            # display-only sub-DBU neighbourhood recovers the real line; isolated
            # touch points are NOT whole-pad controls.
            seam=interior_interfaces(source,group,[rigid[i] for i in ids if i<physical_count and abs(values[i]-weight)<1e-10],r.tol)
            if any(m.get('strict_box') is not None for m in members):
                from .manual_regions import partition
                faces=[partition(m['route_plan'],m['strict_box']) for m in members if m.get('strict_box') is not None]
                seam=shapely.union_all([p['near' if side==0 else 'far'] for p in faces if p is not None and p['near' if side==0 else 'far'].geom_type not in ('Point','MultiPoint')])
            terminals=[]
            for member in members:
                plan=member['route_plan'];s=member['interval'][side]
                endpoint=plan.route.coords[0 if side==0 else -1]
                landing=plan.land if side==0 else shapely.union_all(plan.anchors)
                whole=(s==0 if side==0 else abs(s-plan.length)<=r.tol)
                if getattr(plan,'pad_modes',(True,True))[side] and (whole or landing.difference(group).area<=r.tol*r.tol):
                    actual=getattr(plan,'terminal_vias',((),()))[side]
                    terminal=min(actual,key=lambda g:g.distance(shapely.Point(endpoint))).centroid if actual else shapely.Point(endpoint)
                    terminals.append(terminal)
            return shapely.union_all([seam,*terminals])
        nearcut,farcut=face(high,0),face(low,1)
        # A common trunk with exactly two material seams is a normal strip,
        # even if several terminal paths share it. Use endpoint-matched curves
        # instead of a distance field which can pinch its two sides inward.
        if style in ('linear','arc') and nearcut.geom_type=='LineString' and farcut.geom_type=='LineString' and high>1-1e-10 and low<1e-10 and not any(m.get('strict_box') is not None for m in members):
            from .connection_curves import endpoint_active
            plan=q['route_plan'];part=dict(A=group,B=b,C=c,near=nearcut,far=farcut,a=q['interval'][0],b=q['interval'][1],angles=q.get('boundary_angles',(0.,0.)))
            try:
                candidate=endpoint_active(part,plan,center,pose,style)
                # Keep only a connected, valid candidate, preserving branch
                # material outside this transition; final whole-net guard runs.
                if candidate.is_valid and candidate.geom_type=='Polygon' and not candidate.is_empty:posed=candidate;valid=True
            except (ValueError,shapely.errors.GEOSException):pass
        # Whole-pad controls are physical terminal locations. Floating material
        # weights apply to interface lines, never to the terminal itself.
        if nearcut.geom_type in ('Point','MultiPoint'):high=1.
        if farcut.geom_type in ('Point','MultiPoint'):low=0.
        q.update(regions=dict(A=posed,B=b,C=cposed),nominal_regions=dict(A=group,B=b,C=c),
                 bridge=posed,movable=c,moved=cposed,fixed=b,immovable=b,span=group,
                 moved_cut=transform(nearcut,center,pose*high),cut=nearcut,extra_cut=transform(farcut,center,pose*low),endpoint_weights=(high,low),
                 fallback_regions=dict(A=shapely.Polygon(),B=b,C=cposed),connection_failed=not valid,valid=valid,missing_region=group,
                 warning=q['warning'] if valid else '分叉动区型变折叠，已取消连接段',
                 graph_members=tuple(members),route_identity='graph:'+':'.join(sorted(v['route_identity'] for v in members))+'@'+str(domain_index),
                 source_scope_complete=False,flow_steps=flow_steps,bridge_area_um2=posed.area,movable_area_um2=c.area,moved_area_um2=cposed.area,fixed_area_um2=b.area,
                 whole_landing=nearcut.geom_type in ('Point','MultiPoint'),whole_fixed_landing=farcut.geom_type in ('Point','MultiPoint'),
                 via_refs=tuple(sorted({ref for v in members for ref in v['via_refs']})))
        result.append(q)
    return result


def consolidate(r,pieces,states):
    groups={};other=[]
    for q in pieces:
        if q.get('graph_branch'):groups.setdefault((q['layer'],q['index'],q['die']),[]).append(q)
        else:other.append(q)
    for rows in groups.values():
        source=rows[0]['route_plan'].source
        cache_key=('branch-assembly-local-intervals-v50',r.rules.identity,r.settings.reconstruction_style,
                   tuple(r.model.bound.centers[rows[0]['die']]),states[rows[0]['die']].tobytes(),
                   tuple((q['route_identity'],q['interval'],q['boundary_angles'],tuple(q['nominal_regions'][k].wkb for k in 'ABC')) for q in rows))
        cached=r.geometry_pool.get(cache_key)
        if cached is not None:
            other.extend(dict(q,regions=dict(q['regions'])) for q in cached);continue
        try:
            assembled=assemble(r,source,rows,states)
            r.geometry_pool.put(cache_key,tuple(dict(q,regions=dict(q['regions'])) for q in assembled))
            other.extend(assembled)
        except shapely.errors.GEOSException as exc:
            # A numerical failure is a failed connection with retained source,
            # never an empty replacement or a silently repaired fabrication.
            empty=shapely.Polygon()
            for row in rows:
                q=dict(row,valid=False,connection_failed=True,graph_branch=False,
                    warning='分叉型变布尔运算未通过；保留原铜：'+str(exc),
                    missing_region=row['nominal_regions']['A'],source_scope_complete=False,
                    regions=dict(A=empty,B=source,C=empty),nominal_regions=dict(A=empty,B=source,C=empty),
                    bridge=empty,movable=empty,moved=empty,fixed=source,immovable=source,
                    bridge_area_um2=0.,movable_area_um2=0.,moved_area_um2=0.,fixed_area_um2=source.area)
                other.append(q)
    return other


def editable_piece(piece,point,kind=None):
    if not piece.get('graph_members'):return piece
    p=shapely.Point(point)
    names=('extra_cut',) if kind=='extra' else ('moved_cut',) if kind=='moved' else ('moved_cut','extra_cut')
    side=1 if kind=='extra' else 0
    controls=shapely.get_parts(piece[names[0]])
    nearest=min((g for g in controls if not g.is_empty),key=lambda g:g.distance(p),default=None)
    point_mode=nearest is not None and nearest.geom_type=='Point'
    def distance(q):
        if point_mode:
            plan=q['route_plan'];endpoint=shapely.Point(plan.route.coords[-1 if side else 0])
            if not side:return q['moving_terminals'].distance(p)
            return endpoint.distance(p)
        return min(q[name].distance(p) for name in names)
    member=min(piece['graph_members'],key=distance)
    companions=[];side=1 if kind=='extra' else 0;name=names[0]
    if kind in ('moved','extra'):
        nearest_distance=distance(member);tol=member['route_plan'].tolerance
        whole=point_mode or piece[name].geom_type in ('Point','MultiPoint')
        # A common trunk cut belongs to several terminal paths. Editing just
        # one of them left the other path's A over it, so the boundary appeared
        # frozen. Couple only coincident cuts / the SAME terminal, not branches
        # elsewhere on the net; after a fork they become separate controls.
        endpoint=shapely.Point(member['route_plan'].route.coords[0 if side==0 else -1])
        for row in piece['graph_members']:
            candidate=shapely.Point(row['route_plan'].route.coords[0 if side==0 else -1])
            # A common seam may be assembled from slightly skew source cuts.
            # Distance at the cursor alone misses the other contributors and
            # leaves their overlapping A domains pinned. Couple contributors to
            # THIS connected visible seam, not separate fork faces elsewhere.
            shared_face=(not whole and nearest is not None and nearest.geom_type=='LineString'
                         and row[name].intersection(nearest.buffer(tol*4)).length>tol*16)
            if shared_face or distance(row)<=nearest_distance+tol*4 or (whole and candidate.distance(endpoint)<=tol*4):companions.append(row)
    if point_mode and kind in ('moved','extra'):
        # A union can swallow an individual path cut while retaining another
        # branch's seam. A displayed terminal point starts at that terminal,
        # not at the now-internal member's old arclength (which caused jumps).
        def at_terminal(row):
            plan=row['route_plan'];a,b=row['interval']
            origin=shapely.Point(plan.route.coords[-1 if side else 0])
            return dict(row,interval=(a,plan.length) if side else (0.,b),
                        **{name:nearest,**({} if side else {'cut':origin})})
        member=at_terminal(member);companions=[at_terminal(row) for row in companions]
    return dict(member,graph_edit=True,endpoint_weights=piece['endpoint_weights'],drag_members=tuple(companions))
