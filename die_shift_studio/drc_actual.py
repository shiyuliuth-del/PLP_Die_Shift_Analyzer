"""Physical measurements at each native DRC witness, in um or degrees."""
import math
import numpy as np
import shapely
from shapely.ops import nearest_points
from .resources import cancel_check
from functools import lru_cache

@lru_cache(maxsize=96)
def segments(blob):
    starts=[];ends=[]
    for polygon in shapely.get_parts(shapely.from_wkb(blob)):
        if polygon.geom_type!='Polygon':continue
        for ring in (polygon.exterior,*polygon.interiors):
            xy=np.asarray(ring.coords);starts.extend(xy[:-1]);ends.extend(xy[1:])
    a=np.asarray(starts);b=np.asarray(ends);length=np.linalg.norm(b-a,axis=1);good=length>1e-9
    return a[good],b[good],(b-a)[good]/length[good,None]


def measured(item,witness):
    cancel_check()
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
    # Native witnesses already locate the violating edge portions. A vector
    # bounding-box query avoids rescanning and pairing an entire long net.
    lo=np.asarray(witness.bounds[:2])-1e-5;hi=np.asarray(witness.bounds[2:])+1e-5
    aa,bb,units=segments(copper.wkb)
    ids=np.flatnonzero(np.all(np.maximum(aa,bb)>=lo,axis=1)&np.all(np.minimum(aa,bb)<=hi,axis=1))
    edges=[(shapely.LineString([aa[i],bb[i]]),units[i]) for i in ids]
    is_width='线宽' in item['kind'];values=[];cosine=math.cos(math.radians(5))
    for i,(a,u) in enumerate(edges):
        cancel_check()
        for b,v in edges[i+1:]:
            if abs(float(u@v))<cosine or a.intersects(b):continue # adjacent/shared vertices are not widths
            gap=a.distance(b)
            if gap>requirement+1e-6:continue
            # Parallel segments have infinitely many nearest pairs. GEOS may
            # choose a remote endpoint outside this native marker; project the
            # marker's own stations instead of mistaking that for no value.
            stations=[witness.centroid,*[shapely.Point(v) for v in shapely.get_coordinates(witness)]]
            for station in stations:
                pa=a.interpolate(a.project(station));pb=b.interpolate(b.project(pa));line=shapely.LineString([pa,pb]);mid=line.interpolate(.5,normalized=True)
                if line.distance(witness)>1e-5 or bool(copper.contains(mid))!=is_width:continue
                values.append(float(pa.distance(pb)))
    return {'actual_um':min(values)} if values else {}
