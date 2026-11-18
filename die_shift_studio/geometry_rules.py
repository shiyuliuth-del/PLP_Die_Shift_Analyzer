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
    """Angles inside copper. Sub-grid deviations cannot define independent corners."""
    from shapely.geometry.polygon import orient
    poly=orient(poly.simplify(max(0.,dbu)*.5,preserve_topology=True),sign=1.)
    for ring in (poly.exterior,*poly.interiors):
        xy=np.asarray(ring.coords)[:-1]
        a=xy-np.roll(xy,1,axis=0);b=np.roll(xy,-1,axis=0)-xy
        lengths=np.minimum(np.linalg.norm(a,axis=1),np.linalg.norm(b,axis=1))
        turn=np.degrees(np.arctan2(a[:,0]*b[:,1]-a[:,1]*b[:,0],np.sum(a*b,axis=1)))
        yield xy,180-turn,lengths


def angle_regions(geometry,minimum,dbu):
    if minimum<=0:return shapely.Polygon()
    parts=[]
    for poly in shapely.get_parts(geometry):
        if poly.geom_type!='Polygon':continue
        for xy,angles,lengths in corner_angles(poly,dbu):
            for i in np.flatnonzero((lengths>0)&(angles<minimum-.2)):
                radius=max(dbu*2,min(2.,lengths[i]*.25))
                parts.append(Point(xy[i]).buffer(radius).intersection(poly))
    return shapely.union_all(parts)
