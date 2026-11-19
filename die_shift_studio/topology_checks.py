"""Bounded parallel, read-only connectivity checks on private GEOS values."""
from collections import defaultdict
import numpy as np
import shapely
from .resources import parallel_map,resource_profile


def same_connections(source,copper,nominal_ports,ports,tol):
    """Keep terminal groups and hole count on each original copper island."""
    from .topology_routing import covers_ports
    from .reconstruction_route import parts
    before=parts(source);after=parts(copper)
    if len(before)!=len(after) or not covers_ports(copper,ports,tol):return False
    if len(before)==1:return len(before[0].interiors)==len(after[0].interiors)
    def groups(polygons,terminals):
        return sorted((len(g.interiors),tuple(i for i,p in enumerate(terminals) if g.intersection(p).area>tol*tol)) for g in polygons)
    return groups(before,nominal_ports)==groups(after,ports)

def inspect_many(r,objects,original,ports,manual_masks,manual_before,terminal_sources):
    rows=[];found={}
    keepout=r.rules.keepout.wkb
    refs=list(objects)
    originals=shapely.to_wkb([original[ref] for ref in refs])
    current=shapely.to_wkb(list(objects.values()))
    terminals=shapely.to_wkb([geom for ref in refs for geom in ports[ref]])
    start=0
    for i,ref in enumerate(refs):
        end=start+len(ports[ref])
        nominal=tuple(p.wkb for _,_,p in terminal_sources[ref]) if original[ref].geom_type!='Polygon' else ()
        key=('electrical-check-v54',current[i],originals[i],tuple(terminals[start:end]),nominal,
             keepout,r.tol,manual_masks[ref].wkb if ref in manual_masks else None,
             manual_before[ref].wkb if ref in manual_before else None)
        start=end
        cached=r.geometry_pool.get(key)
        if cached is not None:found[ref]=cached
        else:rows.append((ref,key))
    def one(row):
        ref,key=row;_,blob,source,terminal_blobs,nominal,obstacle,tol,mask,before=key
        # No prepared object or STRtree escapes its lane.
        copper=shapely.from_wkb(blob);initial=shapely.from_wkb(source)
        valid=copper.is_valid and same_connections(initial,copper,shapely.from_wkb(nominal),shapely.from_wkb(terminal_blobs),tol)
        if valid and mask is not None:valid=copper.symmetric_difference(shapely.from_wkb(before)).difference(shapely.from_wkb(mask).buffer(tol*2)).area<=tol*tol
        forbidden=shapely.from_wkb(obstacle)
        intrusion=not forbidden.is_empty and shapely.relate_pattern(copper,forbidden,'T********')
        return ref,key,(bool(valid),bool(intrusion))
    profile=resource_profile()
    if len(rows)<12:profile=dict(profile,workers=1)
    for ref,key,value in parallel_map(one,rows,profile):
        size=sum(len(v) for v in key if isinstance(v,bytes))+sum(map(len,key[3]))+sum(map(len,key[4]))+384
        r.geometry_pool.put(key,value,size=size);found[ref]=value
    return found

def foreign_pairs(objects,nets):
    refs=list(objects)
    planes=defaultdict(list)
    for i,ref in enumerate(refs):
        planes[nets[ref][0]].append(i)
    result=[]
    for rows in planes.values():
        if len(rows)<2:continue
        geos=[objects[refs[i]] for i in rows]
        a,b=shapely.STRtree(geos).query(geos,predicate='intersects')
        netids=np.asarray([nets[refs[i]][1] for i in rows])
        take=(b>a)&(netids[a]!=netids[b])
        result.extend((rows[i],rows[j]) for i,j in zip(a[take],b[take]))
    # Retain the original resolution order before applying any repair.
    return [(refs[i],refs[j]) for i,j in sorted(result)]
