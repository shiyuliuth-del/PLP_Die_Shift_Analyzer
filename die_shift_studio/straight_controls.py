"""Preserve separate straight control blades instead of joining fork corners."""
import numpy as np
import shapely

def straight(geometry,tol):
    lines=[];points=[]
    for g in shapely.get_parts(geometry):
        if g.geom_type=='Point':points.append(g);continue
        if g.geom_type!='LineString':continue
        xy=np.asarray(g.coords);start=xy[0];previous=xy[0];axis=None
        for end in xy[1:]:
            vector=end-previous;length=np.linalg.norm(vector)
            if length<=tol*.01:continue
            unit=vector/length
            if axis is not None and abs(float(axis[0]*unit[1]-axis[1]*unit[0]))>1e-6:
                lines.append(shapely.LineString([start,previous]));start=previous
            axis=unit;previous=end
        if np.linalg.norm(previous-start)>tol:lines.append(shapely.LineString([start,previous]))
    return shapely.GeometryCollection([*lines,*points]) if points and lines else shapely.MultiLineString(lines) if len(lines)>1 else lines[0] if lines else shapely.union_all(points)


def leaves(geometry,tol):
    result=[];stack=[geometry]
    while stack:
        g=stack.pop()
        if g is None or g.is_empty:continue
        if g.geom_type in ('LineString','Point'):
            result.extend(shapely.get_parts(straight(g,tol)))
        elif hasattr(g,'geoms'):stack.extend(g.geoms)
    return result


def representative(geometry, opposite, tol, focus=None):
    """One editable handle; underlying domains and port ownership stay intact."""
    choices=leaves(geometry,tol)
    points=[g for g in choices if g.geom_type=='Point'];lines=[g for g in choices if g.geom_type=='LineString' and g.length>tol]
    target=opposite if opposite is not None and not opposite.is_empty else geometry
    return min(points or lines,key=lambda g:(g.distance(focus) if focus is not None else 0.,g.distance(target),-g.length,g.bounds),default=shapely.LineString())
