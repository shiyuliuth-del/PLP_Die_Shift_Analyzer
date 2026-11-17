"""Physical measurements at each native DRC witness, in um or degrees."""
import math
import numpy as np
import shapely
from shapely.ops import nearest_points

def measured(item,witness):
    if item.get('actual_um') is not None or item.get('actual_deg') is not None:return {}
    pair=item.get('_measurement_pair')
    if pair is not None:
        radius=max(item.get('required_um') or 0.,1e-6)*2
        local=witness.buffer(radius)
        a,b=(g.intersection(local) for g in pair)
        return {'actual_um':float(a.distance(b))} if not a.is_empty and not b.is_empty else {}
    enclosure=item.get('_measurement_enclosure')
    if enclosure is not None:
        via,copper=enclosure
        if copper.covers(via):return {'actual_um':float(via.boundary.distance(copper.boundary))}
        outside=via.difference(copper)
        points=shapely.points(shapely.get_coordinates(outside))
        return {'actual_um':-float(np.max(shapely.distance(points,copper),initial=0.))}
    copper=item.get('_measurement_copper')
    if copper is None:return {}
    if item.get('required_deg') is not None:
        from shapely.geometry.polygon import orient
        angles=[]
        for polygon in shapely.get_parts(copper):
            if polygon.geom_type!='Polygon':continue
            polygon=orient(polygon,sign=1.)
            for ring in (polygon.exterior,*polygon.interiors):
                xy=np.asarray(ring.coords)[:-1]
                for i,b in enumerate(xy):
                    if witness.distance(shapely.Point(b))>1e-5:continue
                    u=b-xy[i-1];v=xy[(i+1)%len(xy)]-b
                    if min(np.linalg.norm(u),np.linalg.norm(v))<1e-12:continue
                    turn=math.atan2(u[0]*v[1]-u[1]*v[0],float(u@v))
                    angles.append(math.degrees(math.pi-turn))
        return {'actual_deg':min(angles)} if angles else {}
    requirement=item.get('required_um')
    if requirement is None:return {}
    region=witness.buffer(max(requirement,1e-6)*1.1);edges=[]
    for polygon in shapely.get_parts(copper):
        if polygon.geom_type!='Polygon':continue
        for ring in (polygon.exterior,*polygon.interiors):
            xy=np.asarray(ring.coords)
            for a,b in zip(xy[:-1],xy[1:]):
                d=b-a;length=np.linalg.norm(d)
                if length<1e-9:continue
                edge=shapely.LineString([a,b])
                if edge.intersects(region):edges.append((edge,d/length))
    is_width='线宽' in item['kind'];values=[];cosine=math.cos(math.radians(5))
    for i,(a,u) in enumerate(edges):
        for b,v in edges[i+1:]:
            if abs(float(u@v))<cosine or a.intersects(b):continue # adjacent/shared vertices are not widths
            gap=a.distance(b)
            if gap>requirement+1e-6:continue
            line=shapely.LineString(nearest_points(a,b));mid=line.interpolate(.5,normalized=True)
            if line.distance(witness)>1e-5 or bool(copper.contains(mid))!=is_width:continue
            values.append(float(gap))
    return {'actual_um':min(values)} if values else {}
