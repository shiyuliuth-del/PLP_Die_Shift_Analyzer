"""Shared native geometric checks; independent of any adaptation feature."""
import numpy as np
import shapely
from shapely.geometry import Point
from .native_backend import geometry as native_operation

def snapped(geometry,dbu):
    result=native_operation('normalize',geometry,dbu)
    return result,result

def width_ok(geometry,width,dbu):
    from .drc import rule_region
    return rule_region(geometry,width,dbu).is_empty


def width_regions(geometry,width,dbu):
    from .drc import rule_region
    return rule_region(geometry,width,dbu)


def corner_angles(poly,dbu):
    """Interior angle at each vertex of the finished exterior copper contour."""
    ring=poly.simplify(max(0.,dbu)*.5,preserve_topology=True).exterior
    if ring.is_empty:return
    xy=np.asarray(ring.coords)[:-1]
    if not ring.is_ccw:xy=xy[::-1]
    # Canonical order preserves identical witness geometry on reversed input.
    left=np.flatnonzero(xy[:,0]==xy[:,0].min())
    xy=np.roll(xy,-left[np.argmin(xy[left,1])],axis=0)
    a=xy-np.roll(xy,1,axis=0);b=np.roll(xy,-1,axis=0)-xy
    lengths=np.minimum(np.linalg.norm(a,axis=1),np.linalg.norm(b,axis=1))
    turn=np.degrees(np.arctan2(a[:,0]*b[:,1]-a[:,1]*b[:,0],np.sum(a*b,axis=1)))
    yield xy,180-turn,lengths



def corner_violations(geometry,minimum,dbu):
    if minimum<=0:return
    from .reconstruction_route import parts as polygons
    for polygon in polygons(geometry):
        for xy,angles,lengths in corner_angles(polygon,dbu):
            bad=(lengths>0)&(angles<minimum-.2)
            if np.any(bad):
                radii=np.maximum(dbu*2,np.minimum(2.,lengths[bad]*.25))
                yield polygon,xy[bad],radii


def angle_regions(geometry,minimum,dbu):
    if minimum<=0:return shapely.Polygon()
    return shapely.union_all([Point(point).buffer(radius).intersection(polygon)
                              for polygon,points,radii in corner_violations(geometry,minimum,dbu)
                              for point,radius in zip(points,radii)])


def has_angle_violation(geometry,minimum,dbu,region=None):
    """Candidate scoring needs one witness, not an entire DRC overlay."""
    if region is None:return next(corner_violations(geometry,minimum,dbu),None) is not None
    if region.is_empty:return False
    x0,y0,x1,y1=region.bounds
    for polygon,points,radii in corner_violations(geometry,minimum,dbu):
        x,y=points.T
        mask=(x+radii>=x0)&(x-radii<=x1)&(y+radii>=y0)&(y-radii<=y1)
        for point,radius in zip(points[mask],radii[mask]):
            if Point(point).buffer(radius).intersection(polygon).intersects(region):return True
    return False
