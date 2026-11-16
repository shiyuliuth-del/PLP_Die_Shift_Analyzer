"""Connected transition domains with rigid-component displacement potentials.

A path cover proposes cuts; connected components ensure no moving/fixed path
bypasses adaptation. Floating metal between serial cuts gets a Laplace weight,
so it is neither duplicated nor arbitrarily pinned to one endpoint.
"""
import numpy as np
import shapely
from .reconstruction_route import parts,clean_overlay


def assemble(r,source,rows,states):
    from .reconstruction import transform
    owner=rows[0]['die'];pose=states[owner];center=r.model.bound.centers[owner]
    moving=shapely.union_all([q['moving_nominal'] for q in rows]);fixed=shapely.union_all([q['fixed_nominal'] for q in rows])
    active=shapely.union_all([q['nominal_regions']['A'] for q in rows]);rigid=[];known={}
    for g in parts(source.difference(active)):
        m=g.intersection(moving).area>r.tol*r.tol;f=g.intersection(fixed).area>r.tol*r.tol
        if m and f:active=shapely.union_all([active,g]);continue
        index=len(rigid);rigid.append(g)
        if m or f:known[index]=float(m)
    domains=parts(clean_overlay(active,r.tol));physical_count=len(rigid)
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
    cposed=shapely.union_all([transform(rigid[i],center,pose*values[i]) for i in mobile]);style=r.settings.reconstruction_style
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
    for group,ids in zip(domains,neighbours):
        members=[q for q in rows if group.intersects(q['nominal_regions']['A'])]
        if not members or not ids:continue
        q=dict(members[0]);posed=deform(group,ids);valid=posed.is_valid and posed.geom_type=='Polygon'
        if not valid:posed=shapely.Polygon()
        high=max(values[i] for i in ids);low=min(values[i] for i in ids)
        def face(weight):
            interfaces=[group.boundary.intersection(rigid[i].boundary) for i in ids if i<physical_count and abs(values[i]-weight)<1e-10]
            surface=shapely.union_all(interfaces)
            if not surface.is_empty:return surface
            terminals=[r.model.nodes[r.model.ids[ref]]['poly'].centroid for member in members for ref in member['via_refs']] if weight>.5 else [v['poly'].centroid for v in r.stays if v['net']==q['net'] and source.intersects(v['poly'])]
            return shapely.union_all(terminals)
        nearcut,farcut=face(high),face(low)
        q.update(regions=dict(A=posed,B=b,C=cposed),nominal_regions=dict(A=group,B=b,C=c),
                 bridge=posed,movable=c,moved=cposed,fixed=b,immovable=b,span=group,
                 moved_cut=transform(nearcut,center,pose*high),cut=nearcut,extra_cut=transform(farcut,center,pose*low),endpoint_weights=(high,low),
                 fallback_regions=dict(A=shapely.Polygon(),B=b,C=cposed),connection_failed=not valid,valid=valid,missing_region=group,
                 warning=q['warning'] if valid else '分叉动区型变折叠，已取消连接段',
                 graph_members=tuple(members),route_identity='graph:'+':'.join(sorted(v['route_identity'] for v in members)),
                 source_scope_complete=False,bridge_area_um2=posed.area,movable_area_um2=c.area,moved_area_um2=cposed.area,fixed_area_um2=b.area,
                 whole_landing=nearcut.geom_type in ('Point','MultiPoint'),whole_fixed_landing=farcut.geom_type in ('Point','MultiPoint'),
                 via_refs=tuple(sorted({ref for v in members for ref in v['via_refs']})))
        result.append(q)
    return result


def consolidate(r,pieces,states):
    groups={};other=[]
    for q in pieces:
        if q.get('graph_branch'):groups.setdefault((q['layer'],q['index'],q['die']),[]).append(q)
        else:other.append(q)
    for rows in groups.values():other.extend(assemble(r,rows[0]['route_plan'].source,rows,states))
    return other


def editable_piece(piece,point):
    if not piece.get('graph_members'):return piece
    p=shapely.Point(point)
    member=min(piece['graph_members'],key=lambda q:min(q['moved_cut'].distance(p),q['extra_cut'].distance(p)))
    return dict(member,graph_edit=True,endpoint_weights=piece['endpoint_weights'])
