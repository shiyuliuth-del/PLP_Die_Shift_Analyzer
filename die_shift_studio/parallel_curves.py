"""Constant-normal-width candidates with staggered side/cut intersections."""
import math
import numpy as np
import shapely

from .connection_curves import connection_data,_side_tangent,biarc,assemble_strip


def _bezier(controls,t):
    n=len(controls)-1
    return sum(math.comb(n,i)*(1-t[:,None])**(n-i)*t[:,None]**i*p for i,p in enumerate(controls))


def offset_active(part,plan,center,state,style,factor=1.):
    effective,landing,fixed,near,moved,far,_,_=connection_data(part,plan,center,state)
    tangents=[]
    co,si=np.cos(state[2]),np.sin(state[2]);rotation=np.array([[co,-si],[si,co]])
    for j,face in enumerate((near,far)):
        s=effective['a'] if j==0 else effective['b']
        t=sum(_side_tangent(plan.source,p,plan.tangent(s),plan.tolerance) for p in face)
        t/=max(np.linalg.norm(t),1e-20);tangents.append(rotation@t if j==0 else t)
    t0,t1=tangents;c0=moved.mean(axis=0);c1=far.mean(axis=0)
    n0=np.array([-t0[1],t0[0]]);n1=np.array([-t1[1],t1[0]])
    widths=np.array([abs((moved[1]-moved[0])@n0),abs((far[1]-far[0])@n1)])
    length=np.linalg.norm(c1-c0)
    if min(widths)<plan.tolerance or length<plan.tolerance*8:raise ValueError('等宽连接截面退化')
    # Straight collars allow each side to meet an oblique face at a DIFFERENT
    # arclength. Matching the same parameter on both sides would thin the neck.
    skew=max(abs((moved[1]-moved[0])@t0),abs((far[1]-far[0])@t1))/2
    collar=max(skew+plan.tolerance*8,min(widths.min()*.2,length*.06))
    p0=c0+t0*collar;p1=c1-t1*collar;distance=np.linalg.norm(p1-p0)*factor
    if (p1-p0)@(t0+t1)<=plan.tolerance:raise ValueError('等宽前进连接端点交错')
    if style=='arc':
        mid=biarc(p0,p1,t0,t1,max(plan.tolerance*.2,1e-6))
        tangent=np.gradient(mid,axis=0);tangent[0]=t0;tangent[-1]=t1
        fraction=np.r_[0,np.cumsum(np.linalg.norm(np.diff(mid,axis=0),axis=1))];fraction/=fraction[-1]
    else:
        controls=np.array([p0,p0+t0*distance/3,p1-t1*distance/3,p1]) if style=='c1' else np.array([p0,p0+t0*distance/5,p0+2*t0*distance/5,p1-2*t1*distance/5,p1-t1*distance/5,p1])
        bound=(len(controls)-1)*(len(controls)-2)*np.linalg.norm(np.diff(controls,n=2,axis=0),axis=1).max(initial=0.)
        count=min(2049,max(129,int(math.ceil(math.sqrt(max(bound,1)/max(4*plan.tolerance,1e-7))))+1))
        fraction=np.linspace(0,1,count);mid=_bezier(controls,fraction);tangent=_bezier(np.diff(controls,axis=0)*(len(controls)-1),fraction)
    norm=np.linalg.norm(tangent,axis=1)
    if norm.min()<1e-10:raise ValueError('等宽连接中心线发生折返奇点')
    tangent/=norm[:,None];normal=np.column_stack([-tangent[:,1],tangent[:,0]])
    blend=3*fraction**2-2*fraction**3 if style=='c1' else 10*fraction**3-15*fraction**4+6*fraction**5
    width=widths[0]+(widths[1]-widths[0])*blend
    sides=[]
    extension=max(widths)*2+skew+1
    for i,sign in enumerate((-1,1)):
        inner=mid+sign*normal*width[:,None]/2
        points=np.vstack([c0-extension*t0+sign*n0*widths[0]/2,inner,c1+extension*t1+sign*n1*widths[1]/2])
        line=shapely.LineString(points)
        # Project known seam endpoints onto their straight collars; use exact
        # original doubles at both ends for bit-identical ABC joins.
        start=line.project(shapely.Point(moved[i]));end=line.project(shapely.Point(far[i]))
        if end<=start:raise ValueError('等宽侧边顺序错误')
        marks=np.r_[0,np.cumsum(np.linalg.norm(np.diff(points,axis=0),axis=1))]
        side=np.vstack([moved[i],points[(marks>start)&(marks<end)],far[i]]);sides.append(side)
    strip=shapely.Polygon(np.vstack([sides[0],sides[1][::-1]]))
    if not strip.is_valid:raise ValueError('等宽曲线自交')
    return assemble_strip(strip,landing,fixed,moved,far,plan.tolerance)
