"""Exact source-clipped, optionally rotated manual reconstruction regions."""
import math
import numpy as np
import shapely


def normalize(rows):
    out=[]
    if not isinstance(rows,(tuple,list)):raise ValueError('框选动区须为列表')
    for layer,vertices in rows:
        points=np.asarray(vertices,float)
        if points.shape!=(4,2) or not np.isfinite(points).all() or np.max(abs(points))>1e12:raise ValueError('框选动区须为四个有限坐标')
        polygon=shapely.Polygon(points)
        if not polygon.is_valid or polygon.area<=0:raise ValueError('框选动区必须为有效四边形')
        out.append((str(layer),tuple(tuple(map(float,p)) for p in points)))
    return tuple(out)


def partition(plan,polygon):
    from .reconstruction_route import parts
    active=plan.source.intersection(polygon)
    if active.is_empty or active.area<=plan.tolerance**2:return None
    marks=shapely.line_locate_point(plan.route,shapely.points(shapely.get_coordinates(plan.route.intersection(polygon))))
    if not len(marks):marks=shapely.line_locate_point(plan.route,shapely.points(shapely.get_coordinates(active)))
    a,b=float(marks.min()),float(marks.max())
    moving=[];fixed=[];ambiguous=False;terminals=getattr(plan,'terminal_vias',((plan.land,),tuple(plan.anchors)))
    moving_terminal=shapely.union_all(terminals[0]);anchors=shapely.union_all(terminals[1])
    for g in parts(plan.source.difference(active)):
        m=g.intersection(moving_terminal).area>plan.tolerance**2;f=g.intersection(anchors).area>plan.tolerance**2
        if m and f:ambiguous=True
        if m or (not f and plan.route.project(g.representative_point())<(a+b)/2):moving.append(g)
        else:fixed.append(g)
    c=shapely.union_all(moving);d=shapely.union_all(fixed)
    # Rectangular box edges are the authoritative cut lines. Never line-merge
    # different box sides into an L-shaped or zig-zag reconstruction boundary.
    def seam(material,s):
        edges=[]
        segments=[(u,v) for part in shapely.get_parts(polygon) if part.geom_type=='Polygon' for xy in [np.asarray(part.exterior.coords)] for u,v in zip(xy[:-1],xy[1:])]
        # Boolean intersections may round a rotated side by a few ulps. Do
        # not substitute a pad point merely because two nominal lines miss.
        rings=[np.asarray(r.coords) for g in parts(material) for r in (g.exterior,*g.interiors)]
        if not rings:return shapely.LineString()
        starts=np.concatenate([r[:-1] for r in rings]);ends=np.concatenate([r[1:] for r in rings])
        eps=max(plan.tolerance*1e-5,32*np.spacing(max(1.,float(np.max(np.abs(starts))))))
        for u,v in segments:
            length=np.linalg.norm(v-u)
            if length<=eps:continue
            axis=(v-u)/length;normal=np.array([-axis[1],axis[0]])
            mask=(abs((starts-u)@normal)<=eps)&(abs((ends-u)@normal)<=eps)
            if not mask.any():continue
            spans=np.sort(np.column_stack(((starts[mask]-u)@axis,(ends[mask]-u)@axis)),axis=1).clip(0,length)
            runs=[]
            for lo,hi in sorted(map(tuple,spans)):
                if hi-lo<=eps:continue
                if runs and lo<=runs[-1][1]+eps:runs[-1][1]=max(hi,runs[-1][1])
                else:runs.append([lo,hi])
            edges.extend(shapely.LineString([u+lo*axis,u+hi*axis]) for lo,hi in runs if hi-lo>plan.tolerance)
        return min(edges,key=lambda g:(g.distance(plan.route.interpolate(s)),-g.length)) if edges else shapely.LineString()
    near=seam(c,a) if not c.is_empty else shapely.LineString()
    far=seam(d,b) if not d.is_empty else shapely.LineString()
    if near.is_empty:near=shapely.Point(plan.route.coords[0]);a=0.
    if far.is_empty:far=shapely.Point(plan.route.coords[-1]);b=plan.length
    angles=[]
    for edge,s in ((near,a),(far,b)):
        if edge.geom_type!='LineString':angles.append(0.);continue
        u=np.asarray(edge.coords[-1])-edge.coords[0];v=plan.tangent(s);normal=np.array([-v[1],v[0]])
        angle=math.atan2(u[1],u[0])-math.atan2(normal[1],normal[0]);angles.append((angle+math.pi/2)%math.pi-math.pi/2)
    return dict(A=active,B=d,C=c,near=near,far=far,a=a,b=b,angles=tuple(angles),manual_angles=(True,True),strict_box=polygon,
                box_invalid=ambiguous,box_complex=active.geom_type!='Polygon' or near.geom_type not in ('LineString','Point') or far.geom_type not in ('LineString','Point'))


def move_side(settings,plan,press,cursor,visible_box=None):
    """Move only the pressed side of its source rectangle in nominal coordinates."""
    from dataclasses import replace
    choices=[];press=np.asarray(press,float);cursor=np.asarray(cursor,float)
    for i,(layer,verts) in enumerate(settings.reconstruction_boxes):
        if layer!=plan.layer or not plan.source.intersects(shapely.Polygon(verts)):continue
        xy=np.asarray(verts,float)
        for j in range(4):
            edge=shapely.LineString([xy[j],xy[(j+1)%4]])
            choices.append((edge.distance(shapely.Point(press)),i,j))
    if not choices:return None
    distance,i,j=min(choices);rows=list(settings.reconstruction_boxes);layer,verts=rows[i];xy=np.array(verts,float)
    if distance>plan.tolerance*4 and visible_box is not None and visible_box.geom_type=='Polygon':
        contour=np.asarray(visible_box.exterior.coords[:-1],float)
        if contour.shape==(4,2):
            xy=contour.copy()
            j=min(range(4),key=lambda n:shapely.LineString([xy[n],xy[(n+1)%4]]).distance(shapely.Point(press)))
    a,b=j,(j+1)%4;v=xy[b]-xy[a];n=np.array([-v[1],v[0]])/np.linalg.norm(v)
    thickness=(xy[(j+2)%4]-xy[b])@n
    if thickness<0:n=-n;thickness=-thickness
    xy[[a,b]]+=min(float((cursor-press)@n),thickness-plan.tolerance*2)*n
    rows[i]=(layer,tuple(map(tuple,xy)));cfg=replace(settings,reconstruction_boxes=tuple(rows))
    bounds=shapely.union_all([shapely.Polygon(v) for k,v in rows if k==layer and plan.source.intersects(shapely.Polygon(v))]);part=usable_partition(plan,bounds)
    return (cfg,part) if part is not None else None


def boxes_by_source(model, rows):
    by_layer={}
    for layer, xy in rows:
        if layer in model.cache.layers:
            by_layer.setdefault(layer,[]).append(shapely.Polygon(xy))
    result={}
    for layer, boxes in by_layer.items():
        geometries=model.cache.layers[layer]['geometry']
        tree=shapely.STRtree(geometries)
        i,j=tree.query(boxes,predicate='intersects')
        for box,ref in zip(i,j):
            result.setdefault((layer,int(ref)),[]).append(boxes[int(box)])
    return {ref:shapely.union_all(areas) for ref,areas in result.items()}


def usable_partition(plan, polygon):
    """Keep valid cuts; extend trapped boxes across the local trace if needed."""
    initial = partition(plan, polygon)
    def good(part):
        if not part or part['box_invalid'] or part['box_complex']:
            return False
        # An empty outer cut is legitimate only when its terminal is included.
        terminals = getattr(plan, 'terminal_vias', ((plan.land,), tuple(plan.anchors)))
        for name, ports in zip(('near', 'far'), terminals):
            if part[name].geom_type == 'Point' and not any(part['A'].intersects(g) for g in ports):
                return False
        return not part['A'].is_empty
    if good(initial) or initial is None:
        return initial

    xy = shapely.get_coordinates(polygon)
    s = plan.route.project(polygon.representative_point())
    direction = np.asarray(plan.tangent(s))
    if not np.linalg.norm(direction):
        return initial
    basis = np.column_stack((direction, [-direction[1], direction[0]]))
    center = np.asarray(plan.route.interpolate(s).coords[0])
    local = (xy-center)@basis
    lo, hi = local.min(axis=0), local.max(axis=0)
    span = np.maximum(hi-lo, plan.tolerance*4)
    # Extend width first, around a local route, without growing along the
    # entire connected plane. Grow length only if the selection cannot split
    # that source with a transverse cut alone.
    base = max(float(getattr(plan, 'width', 0.)), min(span)*.5, plan.tolerance*8)
    for across, along in ((.5,0),(1,0),(2,0),(4,0),(2,.5),(4,1),(8,2)):
        pad = np.array([along,across])*base
        a,b = lo-pad,hi+pad
        grown = shapely.Polygon(np.array([[a[0],a[1]],[b[0],a[1]],[b[0],b[1]],[a[0],b[1]]])@basis.T+center)
        result = partition(plan, grown)
        if good(result):
            return result
    return initial
