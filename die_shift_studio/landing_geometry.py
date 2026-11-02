"""Recognise the pad part of a joined trace only when its contour supports it."""
import math
import numpy as np
import shapely
from shapely.geometry import Point,box


def embedded_pad(via,copper,required,dbu):
    if not copper.covers(via):return required
    center=np.asarray(via.centroid.coords[0]);bounds=via.bounds
    extra=via.distance(copper.boundary)
    if extra<=dbu:return required
    xy=shapely.get_coordinates(copper.exterior)
    # A rectangular pad joined to a feeder still has remnants on all four
    # facing sides. A long trace or a pour with only two sides is not a pad.
    a,b=xy[:-1],xy[1:]
    x0,y0,x1,y1=bounds
    edges=(x0-extra,y0-extra,x1+extra,y1+extra)
    tol=max(2*dbu,extra*1.e-6)
    def side(dim,value,lo,hi):
        other=1-dim
        use=(abs(a[:,dim]-value)<=tol)&(abs(b[:,dim]-value)<=tol)
        overlap=np.minimum(hi,np.maximum(a[use,other],b[use,other]))-np.maximum(lo,np.minimum(a[use,other],b[use,other]))
        return np.maximum(overlap,0.).sum() >= (hi-lo)*.15
    if (side(0,edges[0],edges[1],edges[3]) and side(0,edges[2],edges[1],edges[3])
            and side(1,edges[1],edges[0],edges[2]) and side(1,edges[3],edges[0],edges[2])):
        pad=box(*edges)
        if copper.buffer(tol).covers(pad):return required.union(pad)
    # Round landings have many equal-radius contour edges around the via.
    # Demand at least a half-circle of evidence; the feeder may hide an arc.
    radius=Point(center).distance(copper.boundary)
    if radius<=dbu:return required
    vectors=xy-center;norms=np.linalg.norm(vectors,axis=1)
    matched=abs(norms-radius)<=max(2*dbu,radius*.021)
    idx=np.flatnonzero(matched[:-1]&matched[1:])
    if len(idx)<6:return required
    a,b=vectors[idx],vectors[idx+1]
    angles=np.abs(np.arctan2(a[:,0]*b[:,1]-a[:,1]*b[:,0],np.einsum('ij,ij->i',a,b)))
    use=angles<np.pi/5
    if angles[use].sum()<math.pi:return required
    radius=float(np.median(norms[idx[use]]))
    disk=Point(center).buffer(radius,quad_segs=48)
    # Restore the detected concentric pad, including the arc hidden by its
    # feeder. Its reserved outline is convex for a convex via. Keep irregular
    # via cut-outs in the mandatory enclosure when no circular pad is evidenced.
    landing=required.union(disk)
    return landing.convex_hull if via.equals(via.convex_hull) else landing
