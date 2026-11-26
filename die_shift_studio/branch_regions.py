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


def _fast_union(geoms):
    non_empty=[g for g in geoms if g is not None and not g.is_empty]
    if not non_empty:return shapely.Polygon()
    if len(non_empty)==1:return non_empty[0]
    return shapely.union_all(non_empty)


def interior_interfaces(source,domain,rigid,tolerance,*,exterior=None):
    """Only material-to-material seams are controls, never source perimeter.

    Snapped Boolean operations can leave sub-DBU slivers along the exterior.
    Buffering a domain to recover coincident seams otherwise picks the whole
    sliver contour and transforms it into a long, detached drag handle.
    This display-only trim leaves copper partitions and exported seams intact.
    """
    if not rigid:return shapely.GeometryCollection()
    if exterior is None:exterior=source.boundary.buffer(tolerance*4)
    dom_buf=domain.buffer(tolerance*.05)
    dx0,dy0,dx1,dy1=dom_buf.bounds
    local_ext=exterior.intersection(shapely.box(dx0-tolerance,dy0-tolerance,dx1+tolerance,dy1+tolerance))
    interfaces=[g.boundary.intersection(dom_buf).difference(local_ext) for g in rigid]
    lines=[line for g in interfaces for line in shapely.get_parts(g)
           if line.geom_type=='LineString' and line.length>tolerance*8]
    if not lines:return shapely.GeometryCollection()
    from .straight_controls import straight
    return straight(shapely.line_merge(_fast_union(lines)),tolerance)


def path_sections(rows):
    return [q.get('branch_section') or q['route_plan'].partition(*q['interval'],q.get('boundary_angles',(0.,0.))) for q in rows]


def transition_domains(source,rows,tol,sections=None):
    """Split by every real seam before classifying path interiors.

    Unioning whole per-path partitions swallowed unrelated forks/terminals.
    A component belongs to A only when an interior route interval visits it.
    """
    from shapely.ops import split,substring
    strict=[q['nominal_regions']['A'] for q in rows if q.get('strict_box') is not None]
    components=parts(source.difference(_fast_union(strict))) if strict else parts(source)
    intervals=[];blades={}
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
        if row.get('strict_box') is not None:continue
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
        bx0,by0,bx1,by1=blade.bounds
        next_components=[]
        for g in components:
            gx0,gy0,gx1,gy1=g.bounds
            if gx0<=bx1 and gx1>=bx0 and gy0<=by1 and gy1>=by0 and g.intersects(blade):
                next_components.extend(parts(split(g,blade)))
            else:
                next_components.append(g)
        components=next_components
    selected=[];rigid=[]
    interval_tree=shapely.STRtree(intervals) if intervals else None
    for g in components:
        if interval_tree is not None:
            hits=interval_tree.query(g,predicate='intersects')
            is_selected=any(g.intersection(intervals[int(idx)]).length>tol*4 for idx in hits)
        else:
            is_selected=False
        (selected if is_selected else rigid).append(g)
    # Keep the split cells. Re-differencing their union from the original
    # outline can weld independent rigid branches along an ulp-wide residue.
    return _fast_union([*strict,*selected]),rigid


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


def display_blade(selected, interfaces, guides, tol):
    """Recover a full straight section when nearly coincident cuts split its ink."""
    if selected.geom_type!='LineString' or selected.is_empty:return selected
    candidates=[]
    for guide in guides:
        if guide.geom_type!='LineString' or guide.length<=selected.length+tol:continue
        # A shared branch may give two cuts a slightly different tilt. Require
        # support along the whole guide, so nearby unrelated branches cannot
        # extend a short, legitimate section.
        corridor=max(tol*8,guide.length*.005)
        if not guide.buffer(corridor).covers(selected):continue
        support=guide.intersection(interfaces.buffer(corridor)).length
        if support>=guide.length-tol*16:candidates.append(guide)
    return min(candidates,key=lambda g:(g.distance(selected),-g.length),default=selected)


def fixed_between_owners(source, rows, blocks, ref, tol):
    """Expose stationary copper between cuts on opposite sides of an owner seam."""
    if len({q['die'] for q in rows})<2:return ()
    zones=[(q['die'],q['nominal_regions']['A']) for q in rows
           if not q.get('box_invalid') and not q['nominal_regions']['A'].is_empty]
    if len({owner for owner,_ in zones})<2:return ()
    from .between_motion import anchor_of
    fixed=_fast_union([q['nominal_regions']['B'] for q in rows])
    moving=_fast_union([q['nominal_regions']['C'] for q in rows])
    covered=_fast_union([b['nominal'] for b in blocks])
    cells=source.difference(_fast_union([g for _,g in zones]))
    tree=shapely.STRtree([g.boundary for _,g in zones])
    result=[]
    for cell in parts(cells):
        delta=max(tol**2,cell.length*tol)
        if cell.area<=delta or (not covered.is_empty and cell.difference(covered).area<=delta):continue
        if cell.difference(fixed).area>delta or cell.intersection(moving).area>delta:continue
        contact=cell.boundary.buffer(tol*2)
        owners={zones[i][0] for i in tree.query(contact,predicate='intersects')
                if tree.geometries[i].intersection(contact).length>tol*8}
        if len(owners)<2:continue
        ax,ay=anchor_of(cell)
        result.append(dict(ref=ref,id=(*ref,ax,ay),nominal=cell,geometry=cell,mode='average',custom=False,
                           moves=False,locked=True))
    return tuple(result)


def assemble(r,source,rows,states,reference=None,full_states=None):
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
    merged_rigid=merge_rigid_cells(rigid_cells,r.tol)
    # Absorb tiny non-terminal crossing-cut slivers into the adjacent active
    # domain or neighbouring rigid cell so they never steal display blades.
    sliver_area=max(r.tol**2*64,source.area*1e-6)
    sliver_width=max(r._rule_width*.5,5.)
    kept_rigid=[]
    for g in merged_rigid:
        m=g.intersection(physical_moving).area>r.tol*r.tol;f=g.intersection(physical_fixed).area>r.tol*r.tol
        if not m and not f and g.area<=sliver_area and 2.*g.area/max(g.length,1e-6)<=sliver_width and active.boundary.intersection(g.boundary.buffer(r.tol*.1)).length>r.tol*4:
            active=shapely.union_all([active,g]);continue
        kept_rigid.append((g,m,f))
    for g,m,f in kept_rigid:
        # Enclosure masks may overlap a cut by a sub-DBU sliver. Only actual
        # terminal bodies decide ownership; those masks cannot swallow a fork.
        if m and f and not any(q.get('strict_box') is not None for q in rows):active=shapely.union_all([active,g]);continue
        index=len(rigid);rigid.append(g)
        if m or f:known[index]=float(m)
    domains=sorted(parts(clean_overlay(active,r.tol)),key=lambda g:g.bounds)
    # Absorb narrow terminal-free floating rigid slivers sandwiched between two
    # transition domains of the same owner (e.g. close parallel branch cuts on
    # a shared copper neck) so they form one clean, wider transition zone.
    if len(domains)>1 and not any(q.get('strict_box') is not None or q.get('boxed') for q in rows):
        from .between_motion import STATIONARY,component_modes
        ref=(rows[0]['layer'],rows[0]['index'])
        policies=component_modes(r.settings,ref,dict(enumerate(rigid)),r.tol)
        gap_limit=max(r._rule_width*2.5,50.)
        absorbed=set()
        domain_tree=shapely.STRtree(domains)
        for i,g in enumerate(rigid):
            if i in known or policies[i][0]==STATIONARY:continue
            hit_ids=domain_tree.query(g,predicate='dwithin',distance=r.tol*4*(1+1e-6))
            if len(hit_ids)<2:continue
            touching=[domains[int(idx)] for idx in hit_ids if domains[int(idx)].distance(g)<=r.tol*4]
            if len(touching)<2:continue
            equiv_width=2.*g.area/max(g.length,1e-6)
            min_gap=min(a.distance(b) for ix,a in enumerate(touching) for b in touching[ix+1:])
            if min_gap<=gap_limit or equiv_width<=gap_limit:
                absorbed.add(i)
        if absorbed:
            active=_fast_union([active,*(rigid[i] for i in absorbed)])
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
    neighbours=[];edges={};memberships={};contacts=[]
    rigid_edges=[g.boundary.buffer(r.tol*2) for g in rigid[:physical_count]]
    rigid_non_empty=[(i,g) for i,g in enumerate(rigid) if not g.is_empty]
    rigid_tree=shapely.STRtree([g for _,g in rigid_non_empty]) if rigid_non_empty else None
    for domain in domains:
        if rigid_tree is not None:
            ids=sorted(rigid_non_empty[int(k)][0] for k in rigid_tree.query(domain,predicate='dwithin',distance=r.tol*4*(1+1e-6))
                       if domain.distance(rigid_non_empty[int(k)][1])<=r.tol*4)
        else:
            ids=[]
        neighbours.append(ids)
        contour=domain.boundary
        touching=[i for i in ids if i<physical_count and contour.intersection(rigid_edges[i]).length>r.tol*8]
        contacts.append(touching)
        for i in touching:memberships[i]=memberships.get(i,0)+1
        for ix,i in enumerate(ids):
            for j in ids[ix+1:]:edges[i,j]=edges.get((i,j),0.)+1.
    unknown=[i for i in range(physical_count) if i not in known]
    from .between_motion import STATIONARY,anchor_of,component_modes,endpoint_modes,equivalent_pose,graph_poses,parse_die_mode,solve_graph_weights
    ref=(rows[0]['layer'],rows[0]['index'])
    orig_known=dict(known)
    ep_candidates={i:rigid[i] for i in orig_known if i<physical_count and rigid[i].area>r.tol**2}
    ep_defaults={i:('follow' if orig_known[i]>=0.5 else STATIONARY) for i in ep_candidates}
    ep_meta=endpoint_modes(r.settings,ref,ep_candidates,ep_defaults,r.tol)
    for i,(ep_mode,ep_custom) in ep_meta.items():
        if ep_custom:
            known[i]=1. if ep_mode=='follow' else 0.
    between_meta=component_modes(r.settings,ref,dict(enumerate(rigid[:physical_count])),r.tol)
    between_modes={i:between_meta[i][0] for i in unknown}
    stationary={i for i in unknown if between_modes[i]==STATIONARY and memberships.get(i,0)>1}
    between_modes={i:mode if i in stationary or mode!=STATIONARY else 'max' for i,mode in between_modes.items()}
    die_knots={i:d for i,mode in between_modes.items() if (d:=parse_die_mode(mode)) is not None}
    complex_motion=(stationary and reference is not None and np.any(reference.base)) or bool(die_knots)
    absolute_modes=between_modes
    if rows[0].get('motion_reversed'):
        between_modes={i:{'max':'min','min':'max'}.get(mode,mode) for i,mode in between_modes.items()}
    values=solve_graph_weights(physical_count,known,edges,between_modes)
    motions={i:pose*weight for i,weight in values.items()}
    if complex_motion:
        all_states=states if full_states is None else full_states
        anchors={i:(reference.boundary_state(weight) if reference is not None else pose*weight) for i,weight in known.items()}
        point=source.representative_point().coords[0]
        knot_poses={i:equivalent_pose(center,r.model.bound.centers[d],all_states[d])
                    for i,d in die_knots.items() if 0<=d<len(all_states)}
        absolute=graph_poses(physical_count,anchors,edges,absolute_modes,center,point,knot_poses=knot_poses)
        motions={i:reference.local_pose(state) for i,state in absolute.items()} if reference is not None else absolute
        shifted=shifted or any(np.any(s) for s in motions.values())
    mobile=[i for i in range(physical_count) if (i in die_knots) or (values[i]>1e-12 or (complex_motion and np.any(motions[i])))]
    static=[i for i in range(physical_count) if i not in mobile]
    c=_fast_union([rigid[i] for i in mobile]);b=_fast_union([rigid[i] for i in static])
    posed_rigid=[clean_overlay(transform(g,center,motions[i]),r.tol)
                 if shifted and np.any(motions[i]) else g for i,g in enumerate(rigid[:physical_count])]
    cposed=_fast_union([posed_rigid[i] for i in mobile]);style=r.settings.reconstruction_style
    source_exterior=source.boundary.buffer(r.tol*4)
    def deform(g,ids):
        if not shifted:return g
        dense=shapely.segmentize(g,max(r.tol*8,min(r._rule_width/3,5.)))
        def coordinates(xy):
            points=shapely.points(xy);distances=np.asarray([shapely.distance(points,rigid[i]) for i in ids])
            power={'linear':1,'c1':2,'c2':3,'arc':2}[style]
            weights=np.maximum(distances,r.tol*1e-6)**(-power);weights/=weights.sum(axis=0)
            states=np.asarray([motions[i] for i in ids])
            interpolated=weights.T@states
            nearest=np.argmin(distances,axis=0)
            on_seam=np.min(distances,axis=0)<=r.tol*.05
            interpolated[on_seam]=states[nearest[on_seam]]
            # Interpolated angle and translation match each floating rigid block
            # exactly at its boundary; smoothing order sets the endpoint slope.
            v=xy-center;angles=interpolated[:,2];co=np.cos(angles);si=np.sin(angles)
            return np.column_stack([co*v[:,0]-si*v[:,1],si*v[:,0]+co*v[:,1]])+center+interpolated[:,:2]
        return shapely.transform(dense,coordinates)
    result=[];visible_memberships={};static_domains=[]
    from shapely.ops import substring
    paths=[]
    for q,part in zip(rows,sections):
        left,right=q['interval'];trim=min(r.tol*4,(right-left)*.05)
        paths.append((q,part,substring(q['route_plan'].route,left+trim,right-trim)))
    path_tree=shapely.STRtree([line for _,_,line in paths]) if paths else None
    strict_rows=[(idx,q,part) for idx,(q,part,_) in enumerate(paths) if q.get('strict_box') is not None]
    for domain_index,(group,ids,touching) in enumerate(zip(domains,neighbours,contacts)):
        cand_idx=set(int(k) for k in path_tree.query(group,predicate='intersects')) if path_tree is not None else set()
        cand_idx.update(idx for idx,_,_ in strict_rows)
        contributors=[(paths[idx][0],paths[idx][1]) for idx in sorted(cand_idx)
                      if (paths[idx][0].get('strict_box') is not None and group.intersection(paths[idx][0]['nominal_regions']['A']).area>r.tol*r.tol)
                      or group.intersection(paths[idx][2]).length>r.tol*4]
        members=[q for q,part in contributors]
        if not members:continue
        if not ids and not any(m.get('boxed') for m in members):continue
        q=dict(members[0]);posed=None;flow_steps=0
        q.pop('serial_between_block',None)
        q.pop('local_boundary_states',None)
        high=max((values[i] for i in ids),default=0.);low=min((values[i] for i in ids),default=0.)
        if not complex_motion and high<=1e-10 and not any(m.get('boxed') for m in members) and not any(
                memberships[i]>1 and i not in known for i in touching):
            # A fixed-only isolated cut is not a transition. Its material
            # still belongs to the feature when another domain is emitted.
            static_domains.append(group)
            continue
        guides=[_fast_union([part[name] for _,part in contributors if part is not None]) for name in ('near','far')]
        def face(weight,side):
            # Difference/union can leave ulp-different seam coordinates. A
            # display-only sub-DBU neighbourhood recovers the real line; isolated
            # touch points are NOT whole-pad controls.
            seam=interior_interfaces(source,group,[rigid[i] for i in ids if i<physical_count and abs(values[i]-weight)<1e-10],r.tol,exterior=source_exterior)
            if any(m.get('strict_box') is not None for m in members):
                name='near' if side==0 else 'far'
                chosen=[p[name] for m,p in contributors if m.get('strict_box') is not None and p is not None
                        and p[name].geom_type=='LineString' and p[name].length>r.tol
                        and p[name].intersection(group.buffer(r.tol*4)).length>=p[name].length-r.tol*8]
                if chosen:seam=_fast_union(chosen)
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
        if not complex_motion and shifted and style in ('linear','arc') and nearcut.geom_type=='LineString' and farcut.geom_type=='LineString' and high>1-1e-10 and low<1e-10 and not any(m.get('strict_box') is not None for m in members):
            from .connection_curves import endpoint_active
            plan=q['route_plan'];part=dict(A=group,B=b,C=c,near=full_blade(nearcut,'near'),far=full_blade(farcut,'far'),a=q['interval'][0],b=q['interval'][1],angles=q.get('boundary_angles',(0.,0.)))
            try:
                candidate=endpoint_active(part,plan,center,pose,style)
                # Keep only a connected, valid candidate, preserving branch
                # material outside this transition; final whole-net guard runs.
                if candidate.is_valid and candidate.geom_type=='Polygon' and not candidate.is_empty:posed=candidate
            except (ValueError,shapely.errors.GEOSException):pass
        if posed is None:
            first=motions[ids[0]] if ids else np.zeros(3)
            posed=transform(group,center,first) if all(np.max(abs(motions[i]-first))<=1e-10 for i in ids) else deform(group,ids)
        valid=posed.is_valid and posed.geom_type=='Polygon'
        if not valid:
            from .continuous_warp import domain_poses
            alternative,flow_steps=domain_poses(group,[rigid[i] for i in ids],[motions[i] for i in ids],center,r._rule_width,r.tol)
            if alternative is None:
                # Large common copper can contain overlapping moving/fixed
                # landing reservations. Those overlapping virtual reservations
                # cannot both be rigid. Preserve the real material interfaces;
                # terminal coverage is checked on the resulting physical net.
                physical=[i for i in ids if i<physical_count]
                if physical:alternative,flow_steps=domain_poses(group,[rigid[i] for i in physical],[motions[i] for i in physical],center,r._rule_width,r.tol)
            if alternative is not None:posed=alternative;valid=True
        invalid_box=any(q.get('box_invalid') for q in members)
        # Whole-pad controls are physical terminal locations. Floating material
        # weights apply to interface lines, never to the terminal itself.
        near_original,far_original=nearcut,farcut
        nearcut=representative(near_original,far_original,r.tol,focus=guides[0] if not guides[0].is_empty else None)
        # Each side follows its material section and its own source route.
        remaining=[g for g in leaves(far_original,r.tol) if nearcut.is_empty or g.hausdorff_distance(nearcut)>r.tol*8]
        farcut=representative(shapely.GeometryCollection(remaining),nearcut,r.tol,focus=guides[1] if not guides[1].is_empty else None)
        nearcut=display_blade(nearcut,near_original,[p['near'] for _,p in contributors if p is not None],r.tol)
        farcut=display_blade(farcut,shapely.union_all(remaining),[p['far'] for _,p in contributors if p is not None],r.tol)
        if nearcut.geom_type in ('Point','MultiPoint'):high=1.
        def state_at(cut,weight):
            if not complex_motion or cut.is_empty or cut.geom_type in ('Point','MultiPoint'):return pose*weight
            around=cut.buffer(r.tol*8)
            contact=[(rigid[i].boundary.intersection(around).length,i) for i in touching]
            return motions[max(contact)[1]] if contact else pose*weight
        left,right=state_at(nearcut,high),state_at(farcut,low)
        missing_face=farcut.is_empty or nearcut.is_empty
        if missing_face and not any(m.get('boxed') for m in members):continue
        valid=valid and not invalid_box and not missing_face and bool(ids)
        if not valid:posed=shapely.Polygon()
        warning=q['warning'] if valid else ('框选动区缺少有效的分支连接截面' if missing_face or invalid_box else '分叉动区型变折叠，已取消连接段')
        for i in touching:visible_memberships[i]=visible_memberships.get(i,0)+1
        q.update(regions=dict(A=posed,B=b,C=cposed),nominal_regions=dict(A=group,B=b,C=c),
                 bridge=posed,movable=c,moved=cposed,fixed=b,immovable=b,span=group,
                 moved_cut=transform(nearcut,center,left),cut=nearcut,extra_cut=transform(farcut,center,right),endpoint_weights=(high,low),
                 fallback_regions=dict(A=shapely.Polygon(),B=b,C=cposed),connection_failed=not valid,valid=valid,missing_region=group,
                 warning=warning,box_invalid=invalid_box or missing_face,
                 box_groups=tuple(sorted({g for member in members for g in member.get('box_groups',())})),
                 graph_members=tuple(members),route_identity='graph:'+':'.join(sorted(v['route_identity'] for v in members))+'@'+str(domain_index),
                 source_scope_complete=False,flow_steps=flow_steps,bridge_area_um2=posed.area,movable_area_um2=c.area,moved_area_um2=cposed.area,fixed_area_um2=b.area,
                 whole_landing=nearcut.geom_type in ('Point','MultiPoint'),whole_fixed_landing=farcut.geom_type in ('Point','MultiPoint'),
                 via_refs=tuple(sorted({ref for v in members for ref in v['via_refs']})))
        result.append(q)
        if complex_motion:q['local_boundary_states']=(left,right)
    if result:
        # All domains report the same complete rigid material, including
        # fixed-only cuts that follow no moving terminal.
        if static_domains:
            b=_fast_union([b,*static_domains])
            for q in result:
                for name in ('regions','nominal_regions','fallback_regions'):
                    q[name]['B']=b
                q.update(fixed=b,immovable=b,fixed_area_um2=b.area)
        between_blocks=[]
        for i,count in sorted(visible_memberships.items()):
            if count<2 or rigid[i].area<=r.tol**2:continue
            ax,ay=anchor_of(rigid[i])
            mode,custom=('average',False) if i in known else between_meta[i]
            between_blocks.append(dict(ref=ref,id=(*ref,ax,ay),nominal=rigid[i],geometry=posed_rigid[i],
                                       mode=mode,custom=custom,weight=values[i],locked=i in known))
        result[0]['between_blocks']=tuple(between_blocks)
        endpoint_blocks=[]
        for i in sorted(ep_candidates):
            ax,ay=anchor_of(rigid[i])
            ep_mode,ep_custom=ep_meta[i]
            endpoint_blocks.append(dict(
                ref=ref,id=(*ref,ax,ay),kind='moving' if orig_known[i]>=0.5 else 'fixed',
                default_mode=ep_defaults[i],mode=ep_mode,custom=ep_custom,
                nominal=rigid[i],geometry=posed_rigid[i],owner=owner,
                moves=(ep_mode=='follow' and owner in getattr(r,'active_dies',(owner,))),locked=False,
            ))
        result[0]['endpoint_blocks']=tuple(endpoint_blocks)
        result[0]['branch_rigid']=(*zip(rigid[:physical_count],posed_rigid),*((g,g) for g in static_domains))
    return result


def consolidate(r,pieces,states,local_states=None,owner_frames=None):
    groups={};other=[]
    for q in pieces:
        if q.get('graph_branch') and q.get('route_plan') is not None:groups.setdefault((q['layer'],q['index'],q['die'],q['route_plan'].source.wkb),[]).append(q)
        else:other.append(q)
    for rows in groups.values():
        source=rows[0]['route_plan'].source
        posed=(local_states or {}).get((rows[0]['layer'],rows[0]['index']),states)
        ref=(rows[0]['layer'],rows[0]['index'])
        ref_overrides=tuple(o for o in getattr(r.settings,'reconstruction_between_overrides',()) if (o[0],o[1])==ref)
        ref_ep_overrides=tuple(o for o in getattr(r.settings,'reconstruction_endpoint_overrides',()) if (o[0],o[1])==ref)
        reference=(owner_frames or {}).get((ref,rows[0]['die']))
        die_override_states=tuple(states[int(o[4][4:])].tobytes() for o in ref_overrides if o[4].startswith('die:') and o[4][4:].isdigit() and int(o[4][4:])<len(states))
        cache_key=('branch-assembly-local-intervals-v83',r.rules.identity,r.settings.reconstruction_style,
                   getattr(r.settings,'reconstruction_between_motion','max'),ref_overrides,ref_ep_overrides,
                   tuple(r.model.bound.centers[rows[0]['die']]),posed[rows[0]['die']].tobytes(),die_override_states,
                   None if reference is None else reference.base.tobytes(),
                   tuple((q['route_identity'],q['interval'],q['boundary_angles'],q.get('boxed'),q.get('box_groups'),
                          q.get('motion_reversed'),tuple(q['nominal_regions'][k].wkb for k in 'ABC')) for q in rows))
        cached=r.geometry_pool.get(cache_key)
        if cached is not None:
            other.extend(dict(q,regions=dict(q['regions'])) for q in cached);continue
        try:
            assembled=assemble(r,source,rows,posed,reference,full_states=states)
            r.geometry_pool.put(cache_key,tuple(dict(q,regions=dict(q['regions'])) for q in assembled))
            other.extend(assembled)
        except shapely.errors.GEOSException as exc:
            # A numerical failure is a failed connection with retained posed
            # copper; the moving side must not be frozen at the nominal spot.
            empty=shapely.Polygon()
            for row in rows:
                regions=row.get('regions') or {}
                fixed=regions.get('B');moved=regions.get('C')
                fixed=source if fixed is None or fixed.is_empty else fixed
                moved=empty if moved is None else moved
                other.append(dict(row,valid=False,connection_failed=True,graph_branch=False,
                    warning='分叉型变布尔运算未通过；保留动/静铜并标记连接：'+str(exc),
                    missing_region=row['nominal_regions']['A'],source_scope_complete=False,branch_rigid_unresolved=True,
                    regions=dict(A=empty,B=fixed,C=moved),bridge=empty,moved=moved,fixed=fixed,
                    bridge_area_um2=0.,moved_area_um2=moved.area,fixed_area_um2=fixed.area))
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
    pose={name:piece[name] for name in ('endpoint_weights','boundary_states','boundary_center','relative_pose') if name in piece}
    return dict(member,**pose,graph_edit=True,drag_members=tuple(dict(row,**pose) for row in companions))
