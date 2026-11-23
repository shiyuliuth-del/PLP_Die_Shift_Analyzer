"""Connected transition domains with rigid-component displacement potentials.

A path cover proposes cuts; connected components ensure no moving/fixed path
bypasses adaptation. Floating metal between serial cuts gets a Laplace weight,
so it is neither duplicated nor arbitrarily pinned to one endpoint.
"""
import numpy as np
import shapely
import shapely.affinity
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


def path_sections(rows):
    return [q['route_plan'].partition(*q['interval'],q.get('boundary_angles',(0.,0.))) for q in rows]


def transition_domains(source,rows,tol,sections=None):
    """Split by every real seam before classifying path interiors.

    Unioning whole per-path partitions swallowed unrelated forks/terminals.
    A component belongs to A only when an interior route interval visits it.
    """
    from shapely.ops import split,substring
    if any(q.get('strict_box') is not None for q in rows):
        active=shapely.union_all([q['nominal_regions']['A'] for q in rows])
        return active,parts(source.difference(active))
    components=parts(source);intervals=[];blades={}
    if sections is None:sections=path_sections(rows)
    # If pose-tilted blades from different branch members cross inside source,
    # revert the colliding members to perpendicular cuts so seams never crisscross.
    row_faces=[]
    for row,part in zip(rows,sections):
        faces=[]
        if part is not None:
            for name in ('near','far'):
                face=part[name]
                if face.geom_type=='LineString':faces.append(face)
        row_faces.append(faces)
    crossing=set()
    for i in range(len(rows)):
        if not any(abs(v)>1e-6 for v in rows[i].get('boundary_angles',(0.,0.))):continue
        for j in range(len(rows)):
            if i==j:continue
            if any(a.intersects(b) and not a.equals(b) for a in row_faces[i] for b in row_faces[j]):
                crossing.add(i);crossing.add(j)
    if crossing:
        sections=list(sections)
        for i in crossing:
            if not any(rows[i].get('manual_angles',(False,False))):
                fallback=rows[i]['route_plan'].partition(*rows[i]['interval'],(0.,0.))
                if fallback is not None:sections[i]=fallback
    for row,part in zip(rows,sections):
        plan=row['route_plan'];a,b=row['interval'];eps=min(tol*8,(b-a)*.05)
        if b-a>tol:intervals.append(substring(plan.route,a+eps,b-eps))
        if part is None:continue
        for name in ('near','far'):
            face=part[name]
            if face.geom_type!='LineString':continue
            xy=np.asarray(face.coords);v=xy[-1]-xy[0];v/=max(np.linalg.norm(v),tol)
            blade=shapely.LineString([xy[0]-v*tol*32,xy[-1]+v*tol*32])
            blades.setdefault(blade.normalize().wkb,blade)
    for blade in blades.values():
        components=[piece for g in components for piece in (parts(split(g,blade)) if g.intersects(blade) else [g])]
    selected=[];rigid=[]
    for g in components:
        target=selected if any(g.intersection(line).length>tol*4 for line in intervals) else rigid
        target.append(g)
    # Keep the split cells. Re-differencing their union from the original
    # outline can weld independent rigid branches along an ulp-wide residue.
    return shapely.union_all(selected),rigid


def merge_rigid_cells(cells,tol):
    """Rejoin split cells that share a material edge, never a point."""
    if len(cells)<2:return cells
    tree=shapely.STRtree(cells);a,b=tree.query(cells,predicate='dwithin',distance=tol*.05)
    boundaries=[g.boundary for g in cells];parents=list(range(len(cells)))
    def root(i):
        while parents[i]!=i:i=parents[i]
        return i
    for i,j in zip(a,b):
        if j<=i:continue
        left,right=root(i),root(j)
        if left==right:continue
        edge=boundaries[i].intersection(boundaries[j].buffer(tol*.05))
        if edge.length>tol*8:parents[right]=left
    groups={}
    for i,g in enumerate(cells):groups.setdefault(root(i),[]).append(g)
    return [group[0] if len(group)==1 else shapely.union_all(group) for group in groups.values()]


def assemble(r,source,rows,states):
    from .reconstruction import transform
    from .straight_controls import leaves,representative
    owner=rows[0]['die'];pose=states[owner];center=r.model.bound.centers[owner];shifted=bool(np.any(pose))
    moving=shapely.union_all([q['moving_nominal'] for q in rows]);fixed=shapely.union_all([q['fixed_nominal'] for q in rows])
    sections=path_sections(rows)
    active,rigid_cells=transition_domains(source,rows,r.tol,sections);rigid=[];known={}
    seam=shapely.union_all([q['ownership_seam'] for q in rows if 'ownership_seam' in q])
    # The boundary with independently owned copper stays anchored in source
    # coordinates. It is a real interface even without a fixed via on this half.
    fixed=shapely.union_all([fixed,seam])
    physical_moving=shapely.union_all([g for q in rows for g in getattr(q['route_plan'],'terminal_vias',((moving,),(fixed,)))[0]])
    physical_fixed=shapely.union_all([seam,*[g for q in rows for g in getattr(q['route_plan'],'terminal_vias',((moving,),(fixed,)))[1]]])
    for g in merge_rigid_cells(rigid_cells,r.tol):
        # Enclosure masks may overlap a cut by a sub-DBU sliver. Only actual
        # terminal bodies decide ownership; those masks cannot swallow a fork.
        m=g.intersection(physical_moving).area>r.tol*r.tol;f=g.intersection(physical_fixed).area>r.tol*r.tol
        if m and f and not any(q.get('strict_box') is not None for q in rows):active=shapely.union_all([active,g]);continue
        index=len(rigid);rigid.append(g)
        if m or f:known[index]=float(m)
    domains=sorted(parts(clean_overlay(active,r.tol)),key=lambda g:g.bounds)
    # Absorb narrow terminal-free floating rigid slivers sandwiched between two
    # transition domains of the same owner (e.g. close parallel branch cuts on
    # a shared copper neck) so they form one clean, wider transition zone.
    if len(domains)>1 and not any(q.get('strict_box') is not None for q in rows):
        gap_limit=max(r._rule_width*2.5,50.)
        absorbed=set()
        for i,g in enumerate(rigid):
            if i in known:continue
            touching=[d for d in domains if d.distance(g)<=r.tol*4]
            if len(touching)<2:continue
            equiv_width=2.*g.area/max(g.length,1e-6)
            min_gap=min(a.distance(b) for ix,a in enumerate(touching) for b in touching[ix+1:])
            if min_gap<=gap_limit or equiv_width<=gap_limit:
                absorbed.add(i)
        if absorbed:
            active=shapely.union_all([active,*(rigid[i] for i in absorbed)])
            new_rigid=[];new_known={}
            for i,g in enumerate(rigid):
                if i in absorbed:continue
                if i in known:new_known[len(new_rigid)]=known[i]
                new_rigid.append(g)
            rigid,known=new_rigid,new_known
            domains=sorted(parts(clean_overlay(active,r.tol)),key=lambda g:g.bounds)
    physical_count=len(rigid)
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
        if not shifted:return g
        dense=shapely.segmentize(g,max(r.tol*8,min(r._rule_width/3,5.)))
        def coordinates(xy):
            points=shapely.points(xy);distances=np.asarray([shapely.distance(points,rigid[i]) for i in ids])
            power={'linear':1,'c1':2,'c2':3,'arc':2}[style]
            weights=np.maximum(distances,r.tol*1e-6)**(-power);weights/=weights.sum(axis=0)
            motions=np.asarray([values[i] for i in ids])
            fraction=motions@weights
            nearest=np.argmin(distances,axis=0)
            on_seam=np.min(distances,axis=0)<=r.tol*.05
            fraction[on_seam]=motions[nearest[on_seam]]
            from .preview import rigid_transform
            # Interpolated angle and translation match each floating rigid block
            # exactly at its boundary; smoothing order sets the endpoint slope.
            v=xy-center;angles=pose[2]*fraction;co=np.cos(angles);si=np.sin(angles)
            return np.column_stack([co*v[:,0]-si*v[:,1],si*v[:,0]+co*v[:,1]])+center+fraction[:,None]*pose[:2]
        return shapely.transform(dense,coordinates)
    result=[]
    from shapely.ops import substring
    paths=[]
    for q,part in zip(rows,sections):
        left,right=q['interval'];trim=min(r.tol*4,(right-left)*.05)
        paths.append((q,part,substring(q['route_plan'].route,left+trim,right-trim)))
    for domain_index,(group,ids) in enumerate(zip(domains,neighbours)):
        contributors=[(q,part) for q,part,line in paths
                      if (q.get('strict_box') is not None and group.intersection(q['nominal_regions']['A']).area>r.tol*r.tol)
                      or group.intersection(line).length>r.tol*4]
        members=[q for q,part in contributors]
        if not members or not ids:continue
        q=dict(members[0]);posed=None;flow_steps=0
        high=max(values[i] for i in ids);low=min(values[i] for i in ids)
        if high-low<=1e-10 and high<=1e-10:continue  # fixed-to-fixed copper has no moving terminal
        guides=[shapely.union_all([part[name] for _,part in contributors if part is not None]) for name in ('near','far')]
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
                if side==0 and plan.allows_pad_point and (whole or landing.difference(group).area<=r.tol*r.tol):
                    actual=getattr(plan,'terminal_vias',((),()))[side]
                    terminal=min(actual,key=lambda g:g.distance(shapely.Point(endpoint))).centroid if actual else shapely.Point(endpoint)
                    terminals.append(terminal)
            return shapely.union_all([seam,*terminals])
        nearcut,farcut=face(high,0),face(low,1)
        def full_blade(cut,side):
            # The trimmed selection artwork must never shorten the copper
            # connector. Reuse the original complete material cross-section.
            blades=[part[side] for _,part in contributors if part is not None and part[side].geom_type=='LineString']
            if not blades:return cut
            blade=min(blades,key=lambda g:g.hausdorff_distance(cut))
            return blade if blade.hausdorff_distance(cut)<=r.tol*12 else cut
        # A common trunk with exactly two material seams is a normal strip,
        # even if several terminal paths share it. Use endpoint-matched curves
        # instead of a distance field which can pinch its two sides inward.
        if shifted and style in ('linear','arc') and nearcut.geom_type=='LineString' and farcut.geom_type=='LineString' and high>1-1e-10 and low<1e-10 and not any(m.get('strict_box') is not None for m in members):
            from .connection_curves import endpoint_active
            plan=q['route_plan'];part=dict(A=group,B=b,C=c,near=full_blade(nearcut,'near'),far=full_blade(farcut,'far'),a=q['interval'][0],b=q['interval'][1],angles=q.get('boundary_angles',(0.,0.)))
            try:
                candidate=endpoint_active(part,plan,center,pose,style)
                # Keep only a connected, valid candidate, preserving branch
                # material outside this transition; final whole-net guard runs.
                if candidate.is_valid and candidate.geom_type=='Polygon' and not candidate.is_empty:posed=candidate
            except (ValueError,shapely.errors.GEOSException):pass
        if posed is None:posed=deform(group,ids)
        valid=posed.is_valid and posed.geom_type=='Polygon'
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
        # Whole-pad controls are physical terminal locations. Floating material
        # weights apply to interface lines, never to the terminal itself.
        near_original,far_original=nearcut,farcut
        nearcut=representative(near_original,far_original,r.tol,focus=guides[0] if not guides[0].is_empty else None)
        # Each side follows its material section and its own source route.
        remaining=[g for g in leaves(far_original,r.tol) if nearcut.is_empty or g.hausdorff_distance(nearcut)>r.tol*8]
        farcut=representative(shapely.GeometryCollection(remaining),nearcut,r.tol,focus=guides[1] if not guides[1].is_empty else None)
        if nearcut.geom_type in ('Point','MultiPoint'):high=1.
        if farcut.is_empty or nearcut.is_empty:
            # A disconnected Boolean remnant is not another editable zone.
            # The source composition retains unassigned material as B.
            continue
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


def consolidate(r,pieces,states,local_states=None):
    groups={};other=[]
    for q in pieces:
        if q.get('graph_branch') and q.get('route_plan') is not None:groups.setdefault((q['layer'],q['index'],q['die'],q['route_plan'].source.wkb),[]).append(q)
        else:other.append(q)
    for rows in groups.values():
        source=rows[0]['route_plan'].source
        posed=(local_states or {}).get((rows[0]['layer'],rows[0]['index']),states)
        cache_key=('branch-assembly-local-intervals-v57',r.rules.identity,r.settings.reconstruction_style,
                   tuple(r.model.bound.centers[rows[0]['die']]),posed[rows[0]['die']].tobytes(),
                   tuple((q['route_identity'],q['interval'],q['boundary_angles'],tuple(q['nominal_regions'][k].wkb for k in 'ABC')) for q in rows))
        cached=r.geometry_pool.get(cache_key)
        if cached is not None:
            other.extend(dict(q,regions=dict(q['regions'])) for q in cached);continue
        try:
            assembled=assemble(r,source,rows,posed)
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
            if not side:
                # The posed terminal for this path, not the union of all ports.
                actual=q.get('moving_handle_endpoint')
                if actual is not None:return actual.distance(p)
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
