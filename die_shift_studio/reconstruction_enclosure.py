"""Sufficient full-input enclosure certificates for unchanged fixed copper.

Every sampled rotation chord is enlarged by its analytic sagitta; this is not
an extrema-only or zero-hit sampling test. No probability tail is discarded.
"""
from functools import lru_cache
import math
import numpy as np
import shapely
from .statistics import mean_and_bounds
from .resources import cancel_check


@lru_cache(maxsize=1024)
def reachable_enclosure(wkb,center,mean,bounds,annulus,dbu):
    via=shapely.from_wkb(wkb);xy=shapely.get_coordinates(via)-center
    radius=float(np.linalg.norm(xy,axis=1).max(initial=0.));span=min(2*math.pi,2*bounds[2])
    error=max(dbu*.25,1e-7)
    step=2*math.acos(max(-1.,1-error/radius)) if radius>error else math.pi
    intervals=max(1,min(256,math.ceil(span/max(step,1e-6))))
    angles=np.linspace(mean[2]-span/2,mean[2]+span/2,intervals+1)
    co=np.cos(angles)[:,None];si=np.sin(angles)[:,None]
    moved=np.stack([co*xy[:,0]-si*xy[:,1],si*xy[:,0]+co*xy[:,1]],axis=2)+center
    shifts=np.unique(np.array([[mean[0]+sx*bounds[0],mean[1]+sy*bounds[1]] for sx in (-1,1) for sy in (-1,1)]),axis=0)
    points=(moved.reshape(-1,2)[:,None,:]+shifts).reshape(-1,2)
    sagitta=radius*(1-math.cos(span/intervals/2))
    hull=shapely.MultiPoint(points).convex_hull
    # GEOS circle buffers are inscribed; circumscribe their angular chords.
    return hull.buffer((annulus+sagitta+2*dbu)/math.cos(math.pi/128),quad_segs=32)


def enclosure_certificate(model,p,source,vias):
    if not vias:return None
    dbu=max(model.data.dbu_um,1e-6);count=len(model.bound.centers);worst=math.inf
    for via in vias:
        cancel_check();owner=via['owner']
        if owner<0:return None
        mu,b=mean_and_bounds(p.for_die(owner),count);center=tuple(model.bound.centers[owner])
        radius=float(np.linalg.norm(shapely.get_coordinates(via['poly'])-center,axis=1).max(initial=0.))
        reach=float(np.linalg.norm(abs(mu[:2])+b[:2])+2*radius*math.sin(min(math.pi,abs(mu[2])+b[2])/2))
        conservative=via['poly'].buffer((p.annulus_um+reach+2*dbu)/math.cos(math.pi/128),quad_segs=32)
        envelope=conservative if source.covers(conservative) else reachable_enclosure(via['poly'].wkb,center,tuple(mu),tuple(b),p.annulus_um,dbu)
        if not source.covers(envelope):return None
        worst=min(worst,float(source.boundary.distance(envelope)))
    return dict(certified=True,scope='完整输入偏移盒，包含连续转角及固定RDL包围量',minimum_margin_um=worst,interfaces=len(vias),method='保守刚体包络；旋转弦加解析弓高外包')
