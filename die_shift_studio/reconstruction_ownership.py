"""Disjoint nominal ownership for actual terminal branches on a shared net."""
from collections import defaultdict
from functools import lru_cache
from itertools import combinations

import numpy as np
import shapely
from shapely.ops import split


@lru_cache(maxsize=128)
def divide(wkb,terminals,width,tolerance):
    from .reconstruction_route import trace,parts
    source=shapely.from_wkb(wkb)
    vias=[(owner,shapely.from_wkb(blob)) for owner,blob in terminals if owner>=0]
    if len({owner for owner,_ in vias})<2:return {}
    candidates=sorted((a.distance(b),i,j) for (i,(owner,a)),(j,(other,b)) in combinations(enumerate(vias),2) if owner!=other)
    seams=[];regions=[(source,set(range(len(vias))))]
    while any(len({vias[i][0] for i in ids})>1 for _,ids in regions):
        for _,i,j in candidates:
            match=next((k for k,(_,ids) in enumerate(regions) if i in ids and j in ids),None)
            if match is None:continue
            region,ids=regions[match]
            a,b=vias[i][1],vias[j][1]
            route=trace(region,a.centroid.coords[0],b.centroid.coords[0],width)
            if route is None:continue
            pads=[vias[k][1] for k in sorted(ids)]
            for fraction in (.5,.4,.6,.3,.7,.2,.8):
                station=route.length*fraction;point=np.asarray(route.interpolate(station).coords[0])
                step=max(tolerance,.01)
                delta=np.asarray(route.interpolate(min(route.length,station+step)).coords[0])-np.asarray(route.interpolate(max(0.,station-step)).coords[0])
                length=np.linalg.norm(delta)
                if length<tolerance:continue
                normal=np.array([-delta[1],delta[0]])/length;size=route.length+source.length
                sections=[g for g in shapely.get_parts(region.intersection(shapely.LineString([point-normal*size,point+normal*size]))) if g.geom_type=='LineString']
                if not sections:continue
                face=min(sections,key=lambda g:g.distance(shapely.Point(point)));xy=np.asarray(face.coords)
                if np.any(shapely.intersects(pads,face)):continue
                blade=shapely.LineString([xy[0]-normal*tolerance*32,xy[-1]+normal*tolerance*32])
                try:children=parts(split(region,blade))
                except (ValueError,shapely.errors.GEOSException):continue
                if len(children)!=2:continue
                groups=[{k for k in ids if child.intersection(vias[k][1]).area>tolerance*tolerance} for child in children]
                if not all(groups) or groups[0]&groups[1] or groups[0]|groups[1]!=ids or any({i,j}<=group for group in groups):continue
                # A DIE may have terminals on both sides of a cut. Keep those
                # branches separate instead of giving two DIEs overlapping A.
                regions[match:match+1]=list(zip(children,groups));seams.append(face)
                break
            else:continue
            break
        else:return {}
    by_owner=defaultdict(list)
    seam=shapely.union_all(seams).buffer(tolerance*8)
    for region,ids in regions:
        owner=vias[min(ids)][0]
        by_owner[owner].append((region,seam.intersection(region)))
    return dict(by_owner)
