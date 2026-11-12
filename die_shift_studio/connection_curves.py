"""Endpoint-matched straight and circular connections, in micrometres.

A general pair of end tangents cannot be joined by one circle. Arc mode uses
two actual circular arcs with a common tangent (a biarc), tessellated only for
the polygon/GDS/DXF representation. Invalid/reversed joins are rejected.
"""
import math
import numpy as np
import shapely

STYLES={'linear':'直线','c1':'一阶','c2':'二阶','arc':'圆弧'}


def _arc(start,tangent,end,tolerance):
    chord=end-start;normal=np.array([-tangent[1],tangent[0]])
    h=float(chord@normal);length=float(np.linalg.norm(chord))
    if length<=tolerance:return np.array([start,end])
    if abs(h)<1e-10*max(1.,length):
        if chord@tangent<=0:raise ValueError('圆弧端点反向')
        return np.array([start,end])
    radius=float(chord@chord)/(2*h);center=start+radius*normal
    v0=start-center;v1=end-center
    angle=math.atan2(v0[0]*v1[1]-v0[1]*v1[0],v0@v1)
    if angle*radius<0:angle+=math.copysign(2*math.pi,radius)
    if abs(angle)>math.pi+1e-7:raise ValueError('圆弧发生回绕')
    step=2*math.acos(np.clip(1-tolerance/abs(radius),-1,1))
    count=max(2,int(math.ceil(abs(angle)/max(step,1e-5)))+1)
    if count>20000:raise ValueError('圆弧离散精度超过资源上限')
    t=np.linspace(0,angle,count);co=np.cos(t);si=np.sin(t)
    points=center+np.column_stack([co*v0[0]-si*v0[1],si*v0[0]+co*v0[1]])
    points[0]=start;points[-1]=end
    return points


def biarc(start,end,t0,t1,tolerance):
    start,end,t0,t1=[np.asarray(v,float) for v in (start,end,t0,t1)]
    t0=t0/np.linalg.norm(t0);t1=t1/np.linalg.norm(t1)
    v=end-start;length2=float(v@v)
    if length2<=tolerance*tolerance:raise ValueError('圆弧连接端点重合')
    dot=float(v@(t0+t1));delta=max(0.,1-float(t0@t1))
    denominator=dot+math.sqrt(dot*dot+2*delta*length2)
    if denominator<=1e-12:raise ValueError('圆弧端点方向不能形成前进连接')
    d=length2/denominator;middle=(start+end+d*(t0-t1))/2
    first=_arc(start,t0,middle,tolerance)
    second=_arc(end,-t1,middle,tolerance)[::-1]
    return np.vstack([first,second[1:]])


def _face_ends(plan,face,s):
    xy=np.asarray(face.coords)[[0,-1]];t=plan.tangent(s);normal=np.array([-t[1],t[0]])
    return xy[np.argsort(xy@normal)]


def _side_tangent(source,point,direction,tol):
    """Real source boundary tangent, oriented toward the route's far end."""
    xy=shapely.get_coordinates(source.boundary)
    a,b=xy[:-1],xy[1:];v=b-a;length=np.linalg.norm(v,axis=1)
    good=length>tol*.01;a,b,v,length=a[good],b[good],v[good],length[good]
    t=np.clip(np.sum((point-a)*v,axis=1)/(length*length),0,1)
    distance=np.linalg.norm(a+t[:,None]*v-point,axis=1)
    choices=np.flatnonzero(distance<=max(tol*2,float(distance.min())+tol*.01))
    i=max(choices,key=lambda j:abs(float(v[j]@direction))/length[j])
    tangent=v[i]/length[i]
    return tangent if tangent@direction>=0 else -tangent


def endpoint_active(part,plan,center,state,style):
    from .preview import rigid_transform
    active_part=part;landing=shapely.Polygon()
    if part['a']==0:
        # The blue point is a UI handle, never a zero-width electrical neck.
        # Preserve the whole original landing as part of A; connect beyond it.
        active_part=None
        for offset in (16,32,64):
            start=plan.land_end+plan.tolerance*offset
            if start>=part['b']:break
            active_part=plan.partition(start,part['b'])
            if active_part is not None:break
        if active_part is None:raise ValueError('整孔盘外没有可连接的截面')
        landing=shapely.transform(active_part['C'],lambda p:rigid_transform(p,center,state))
    near=_face_ends(plan,active_part['near'],active_part['a'])
    far=_face_ends(plan,active_part['far'],active_part['b'])
    moved=rigid_transform(near,center,state)
    sides=[]
    for i in range(2):
        if style=='linear':side=np.array([moved[i],far[i]])
        else:
            t0=_side_tangent(plan.source,near[i],plan.tangent(active_part['a']),plan.tolerance)
            t1=_side_tangent(plan.source,far[i],plan.tangent(active_part['b']),plan.tolerance)
            angle=float(state[2]);co,si=math.cos(angle),math.sin(angle)
            t0=np.array([co*t0[0]-si*t0[1],si*t0[0]+co*t0[1]])
            side=biarc(moved[i],far[i],t0,t1,max(plan.tolerance*.5,1e-6))
        sides.append(side)
    strip=shapely.Polygon(np.vstack([sides[0],sides[1][::-1]]))
    if not strip.is_valid:raise ValueError('连接轮廓发生反向交叉')
    return shapely.union_all([strip,landing]) if not landing.is_empty else strip
