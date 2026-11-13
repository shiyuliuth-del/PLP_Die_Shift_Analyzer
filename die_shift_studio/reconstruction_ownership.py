"""One shared source seam between independently moving terminals on a net."""
from functools import lru_cache
import numpy as np
import shapely
from shapely.ops import split


@lru_cache(maxsize=128)
def divide(wkb,terminals,width,tolerance):
    from .reconstruction_route import trace,parts
    source=shapely.from_wkb(wkb)
    vias=[(owner,shapely.from_wkb(blob)) for owner,blob in terminals if owner>=0]
    if len({o for o,_ in vias})<2:return {}
    regions=[source];seams=[]
    candidates=sorted((a.distance(b),i,j) for i,(o,a) in enumerate(vias) for j,(p,b) in enumerate(vias) if j>i and p!=o)
    for _,i,j in candidates:
        a,b=vias[i][1],vias[j][1]
        region=next((g for g in regions if g.covers(a) and g.covers(b)),None)
        if region is None:continue
        route=trace(region,a.centroid.coords[0],b.centroid.coords[0],width)
        if route is None:continue
        middle=route.length/2;point=np.asarray(route.interpolate(middle).coords[0])
        delta=np.asarray(route.interpolate(min(route.length,middle+max(tolerance,.01))).coords[0])-np.asarray(route.interpolate(max(0.,middle-max(tolerance,.01))).coords[0])
        if np.linalg.norm(delta)<tolerance:continue
        normal=np.array([-delta[1],delta[0]])/np.linalg.norm(delta);size=route.length+source.length
        sections=[g for g in shapely.get_parts(region.intersection(shapely.LineString([point-normal*size,point+normal*size]))) if g.geom_type=='LineString']
        if not sections:continue
        face=min(sections,key=lambda g:g.distance(shapely.Point(point)));xy=np.asarray(face.coords)
        blade=shapely.LineString([xy[0]-normal*tolerance*32,xy[-1]+normal*tolerance*32])
        try:children=parts(split(region,blade))
        except (ValueError,shapely.errors.GEOSException):continue
        groups=[{o for o,v in vias if g.intersection(v).area>tolerance*tolerance} for g in children]
        if len(children)!=2 or not all(groups) or groups[0]&groups[1]:continue
        regions.remove(region);regions.extend(children);seams.append(face)
    out={};seam=shapely.union_all(seams).buffer(tolerance*8)
    for region in regions:
        owners={o for o,v in vias if region.intersection(v).area>tolerance*tolerance}
        if len(owners)!=1:return {}
        owner=next(iter(owners))
        if owner in out:return {}
        out[owner]=(region,seam.intersection(region))
    return out
