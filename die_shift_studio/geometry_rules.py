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


def angle_regions(geometry,minimum,dbu):
    """Copper-interior convex angle, holes included; 0.2 degree DBU tolerance."""
    parts=[]
    from shapely.geometry.polygon import orient
    for poly in shapely.get_parts(geometry):
        if poly.geom_type!='Polygon':continue
        poly=orient(poly,sign=1.)
        for ring in [poly.exterior,*poly.interiors]:
            xy=np.asarray(ring.coords)[:-1];a=xy-np.roll(xy,1,axis=0);b=np.roll(xy,-1,axis=0)-xy
            turn=np.degrees(np.arctan2(a[:,0]*b[:,1]-a[:,1]*b[:,0],np.sum(a*b,axis=1)))
            angles=180-turn
            for i in np.flatnonzero(angles<minimum-.2):
                radius=max(dbu*2,min(np.linalg.norm(a[i]),np.linalg.norm(b[i]))*.25)
                parts.append(Point(xy[i]).buffer(radius).intersection(poly))
    return shapely.union_all(parts)


