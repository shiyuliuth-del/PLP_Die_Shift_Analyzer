"""Bounded tangent-preserving return-bend candidates between the real cut faces."""
import math
import numpy as np
import shapely
from .connection_curves import connection_data,biarc,_side_tangent,assemble_strip


VARIANTS=((1.,0.),(2.,1.),(2.,-1.),(3.,2.),(3.,-2.),(4.,4.),(4.,-4.))


def bezier(controls,t):
    controls=np.asarray(controls,float);n=len(controls)-1
    weights=np.array([math.comb(n,i)*t**i*(1-t)**(n-i) for i in range(n+1)]).T
    return weights@controls


def turn_active(part,plan,center,state,style,variant):
    if style=='linear':raise ValueError('直线模式不生成回头弯')
    active,landing,fixed_landing,near,moved,far,t0,t1=connection_data(part,plan,center,state)
    start=moved.mean(axis=0);end=far.mean(axis=0)
    h0=np.linalg.norm(moved[1]-moved[0])/2;h1=np.linalg.norm(far[1]-far[0])/2
    if min(h0,h1)<=plan.tolerance:raise ValueError('连接截面没有可用宽度')
    distance=np.linalg.norm(end-start)
    if distance<=plan.tolerance:raise ValueError('连接端点重合')
    reach,bias=VARIANTS[(variant-1)%len(VARIANTS)]
    normal=np.array([-t0[1],t0[0]]);height=bias*max(4*max(h0,h1),distance*.3)
    length=max(distance,4*max(h0,h1))*reach
    tolerance=max(plan.tolerance*.5,1e-6)
    co,si=np.cos(state[2]),np.sin(state[2]);rotation=np.array([[co,-si],[si,co]])
    edge0=np.array([rotation@_side_tangent(plan.source,q,plan.tangent(active['a']),plan.tolerance) for q in near])
    edge1=np.array([_side_tangent(plan.source,q,plan.tangent(active['b']),plan.tolerance) for q in far])
    if style=='arc':
        if not bias:height=max(4*max(h0,h1),distance*.3)
        middle=(start+end)/2+normal*height;tm=t0+t1
        tm=tm/max(np.linalg.norm(tm),1e-12)
        if (end-start)@tm<0:tm=-tm
        else:tm=(end-start)/distance
        nm=np.array([-tm[1],tm[0]]);sides=[]
        for i,sign in enumerate((-1,1)):
            mid=middle+sign*nm*(h0+h1)/2
            a=biarc(moved[i],mid,edge0[i],tm,tolerance)
            b=biarc(mid,far[i],tm,edge1[i],tolerance)
            sides.append(np.vstack([a,b[1:]]))
        strip=shapely.Polygon(np.vstack([sides[0],sides[1][::-1]]))
        if strip.is_empty or not strip.is_valid:raise ValueError('圆弧回头连接没有足够宽度')
        return assemble_strip(strip,landing,fixed_landing,moved,far,plan.tolerance)
    else:
        degree=3 if style=='c1' else 5
        n0=np.array([-t0[1],t0[0]]);n1=np.array([-t1[1],t1[0]])
        slopes0=(edge0@n0)/np.maximum(edge0@t0,1e-9);slopes1=(edge1@n1)/np.maximum(edge1@t1,1e-9)
        if max(abs(slopes0).max(),abs(slopes1).max())>10:raise ValueError('端部切向不能形成稳定回头连接')
        d0=length*(t0+slopes0.mean()*n0);d1=length*(t1+slopes1.mean()*n1)
        dh0=length*(slopes0[1]-slopes0[0])/2;dh1=length*(slopes1[1]-slopes1[0])/2
        if degree==3:
            controls=np.array([start,start+d0/3,end-d1/3,end]);widths=[h0,h0+dh0/3,h1-dh1/3,h1]
        else:
            controls=np.array([start,start+d0/5,start+2*d0/5,end-2*d1/5,end-d1/5,end])
            widths=[h0,h0+dh0/5,h0+2*dh0/5,h1-2*dh1/5,h1-dh1/5,h1]
        bound=degree*(degree-1)*np.max(np.linalg.norm(np.diff(controls,n=2,axis=0),axis=1))+128*abs(height)
        n=min(4096,max(128,int(np.ceil(np.sqrt(bound/(8*tolerance))))))
        u=np.linspace(0,1,n+1);points=bezier(controls,u)
        derivative=bezier(degree*np.diff(controls,axis=0),u)
        if degree==3:bump=16*u*u*(1-u)**2;dbump=32*u*(1-u)*(1-2*u)
        else:bump=64*u**3*(1-u)**3;dbump=192*u*u*(1-u)**2*(1-2*u)
        points+=bump[:,None]*height*normal;derivative+=dbump[:,None]*height*normal
        speed=np.linalg.norm(derivative,axis=1)
        if np.min(speed)<1e-8:raise ValueError('回头连接出现尖点')
        tangents=derivative/speed[:,None]
    normals=np.column_stack([-tangents[:,1],tangents[:,0]])
    # The end section normal and its first (and C2 second) derivatives match
    # the original straight boundary pieces, including tapered traces.
    def smooth(t):return 3*t*t-2*t**3 if style=='c1' else 10*t**3-15*t**4+6*t**5
    for distance_to_end,normal_end in ((u,n0),(1-u,n1)):
        blend=smooth(np.clip(distance_to_end/.15,0,1))
        normals=blend[:,None]*normals+(1-blend)[:,None]*normal_end
    norm=np.linalg.norm(normals,axis=1)
    if norm.min()<1e-8:raise ValueError('回头连接法线退化')
    normals/=norm[:,None]
    half=bezier(widths,u)
    if half.min()<=plan.tolerance:raise ValueError('回头过渡的宽度退化')
    # Only an interior four-DBU guard; both real cut faces remain bit-identical.
    half+=plan.tolerance*4*np.sin(np.pi*u)**4
    low=points-normals*half[:,None];high=points+normals*half[:,None]
    low[0]=moved[0];high[0]=moved[1];low[-1]=far[0];high[-1]=far[1]
    strip=shapely.Polygon(np.vstack([low,high[::-1]]))
    if strip.is_empty or not strip.is_valid:raise ValueError('回头连接折叠或没有足够转弯宽度')
    return assemble_strip(strip,landing,fixed_landing,moved,far,plan.tolerance)
