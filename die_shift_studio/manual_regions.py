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
    route_crosses=bool(len(marks))
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
        for u,v in segments:
            edge=shapely.LineString([u,v]);hit=edge.intersection(material.boundary)
            for line in shapely.get_parts(hit):
                if line.geom_type=='LineString' and line.length>plan.tolerance:edges.append(line)
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


def move_side(settings,plan,press,cursor):
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
    _,i,j=min(choices);rows=list(settings.reconstruction_boxes);layer,verts=rows[i];xy=np.array(verts,float)
    a,b=j,(j+1)%4;v=xy[b]-xy[a];n=np.array([-v[1],v[0]])/np.linalg.norm(v)
    thickness=(xy[(j+2)%4]-xy[b])@n
    if thickness<0:n=-n;thickness=-thickness
    xy[[a,b]]+=min(float((cursor-press)@n),thickness-plan.tolerance*2)*n
    rows[i]=(layer,tuple(map(tuple,xy)));cfg=replace(settings,reconstruction_boxes=tuple(rows))
    bounds=shapely.union_all([shapely.Polygon(v) for k,v in rows if k==layer and plan.source.intersects(shapely.Polygon(v))]);part=partition(plan,bounds)
    return (cfg,part) if part is not None else None
