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
    if not len(marks):return None
    a,b=float(marks.min()),float(marks.max())
    moving=[];fixed=[];ambiguous=False;anchors=shapely.union_all(plan.anchors)
    for g in parts(plan.source.difference(active)):
        m=g.intersection(plan.land).area>plan.tolerance**2;f=g.intersection(anchors).area>plan.tolerance**2
        if m and f:ambiguous=True
        if m or (not f and plan.route.project(g.representative_point())<(a+b)/2):moving.append(g)
        else:fixed.append(g)
    c=shapely.union_all(moving);d=shapely.union_all(fixed)
    near=shapely.line_merge(active.boundary.intersection(c.boundary)) if not c.is_empty else shapely.LineString()
    far=shapely.line_merge(active.boundary.intersection(d.boundary)) if not d.is_empty else shapely.LineString()
    if near.is_empty:near=shapely.Point(plan.route.coords[0]);a=0.
    if far.is_empty:far=shapely.Point(plan.route.coords[-1]);b=plan.length
    angles=[]
    for edge,s in ((near,a),(far,b)):
        if edge.geom_type!='LineString':angles.append(0.);continue
        u=np.asarray(edge.coords[-1])-edge.coords[0];v=plan.tangent(s);normal=np.array([-v[1],v[0]])
        angle=math.atan2(u[1],u[0])-math.atan2(normal[1],normal[0]);angles.append((angle+math.pi/2)%math.pi-math.pi/2)
    return dict(A=active,B=d,C=c,near=near,far=far,a=a,b=b,angles=tuple(angles),manual_angles=(True,True),strict_box=polygon,
                box_invalid=ambiguous,box_complex=active.geom_type!='Polygon' or near.geom_type not in ('LineString','Point') or far.geom_type not in ('LineString','Point'))
