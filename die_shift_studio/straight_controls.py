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
