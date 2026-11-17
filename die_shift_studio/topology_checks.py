"""Bounded parallel, read-only connectivity checks on private GEOS values."""
import shapely
from .resources import parallel_map,resource_profile

def inspect_many(r,objects,original,ports,manual_masks,manual_before):
    from .topology_routing import signature,covers_ports
    rows=[];found={}
    keepout=r.rules.keepout.wkb
    for ref,g in objects.items():
        key=('electrical-check-v50',g.wkb,original[ref].wkb,tuple(p.wkb for p in ports[ref]),
             keepout,r.tol,manual_masks[ref].wkb if ref in manual_masks else None,
             manual_before[ref].wkb if ref in manual_before else None)
        cached=r.geometry_pool.get(key)
        if cached is not None:found[ref]=cached
        else:rows.append((ref,key))
    def one(row):
        ref,key=row;_,blob,source,terminal_blobs,obstacle,tol,mask,before=key
        # No prepared object or STRtree escapes its lane.
        copper=shapely.from_wkb(blob);initial=shapely.from_wkb(source)
        valid=copper.is_valid and signature(copper)==signature(initial) and covers_ports(copper,[shapely.from_wkb(b) for b in terminal_blobs],tol)
        if valid and mask is not None:valid=copper.symmetric_difference(shapely.from_wkb(before)).difference(shapely.from_wkb(mask).buffer(tol*2)).area<=tol*tol
        forbidden=shapely.from_wkb(obstacle)
        intrusion=not forbidden.is_empty and shapely.relate_pattern(copper,forbidden,'T********')
        return ref,key,(bool(valid),bool(intrusion))
    profile=resource_profile()
    if len(rows)<12:profile=dict(profile,workers=1)
    for ref,key,value in parallel_map(one,rows,profile):r.geometry_pool.put(key,value);found[ref]=value
    return found

def foreign_pairs(objects,nets):
    refs=list(objects);geos=[objects[r] for r in refs]
    if not geos:return []
    a,b=shapely.STRtree(geos).query(geos,predicate='intersects')
    return [(refs[int(i)],refs[int(j)]) for i,j in zip(a,b) if j>i and nets[refs[int(i)]][0]==nets[refs[int(j)]][0] and nets[refs[int(i)]]!=nets[refs[int(j)]]]
