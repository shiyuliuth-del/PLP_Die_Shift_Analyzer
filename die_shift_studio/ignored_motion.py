"""Ignored reconstruction keeps copper rigid per connected DIE, not stationary."""
import numpy as np
import shapely
from .statistics import mean_and_bounds
from .reconstruction_ownership import divide


def fixed_enclosures_safe(r,ref,vias):
    source=r.model.cache.layers[ref[0]]['geometry'][ref[1]]
    owners={v['owner'] for v in vias if v['owner']>=0}
    if len(owners)!=1:return False
    owner=next(iter(owners));mu,b=mean_and_bounds(r.p.for_die(owner),len(r.model.bound.centers))
    radius=np.linalg.norm(shapely.get_coordinates(source)-r.model.bound.centers[owner],axis=1).max()
    reach=np.linalg.norm(abs(mu[:2])+b[:2])+2*radius*np.sin(min(np.pi,abs(mu[2])+b[2])/2)
    inner=source.buffer(-reach-4*r.tol)
    return all(inner.covers(v['poly'].buffer(r.p.annulus_um)) for v in r.stays
               if v['net']==vias[0]['net'] and source.intersects(v['poly']))


def entries(r):
    out=[]
    for ref in sorted(set(r.settings.reconstruction_ignored)|set(r.auto_ignored)):
        vias=r.vias_by_target.get(ref,());source=r.model.cache.layers[ref[0]]['geometry'][ref[1]]
        owners=sorted({v['owner'] for v in vias if v['owner']>=0})
        allocation=divide(source.wkb,tuple((v['owner'],v['poly'].wkb) for v in vias),r._rule_width,r.tol)
        if len(owners)>1 and not allocation:
            # No arbitrary nearest-DIE rigid motion is allowed for an inseparable net.
            out.append(dict(layer=ref[0],index=ref[1],owner=owners[0],refs=tuple((v['key'],v['index']) for v in vias),source=source,routing_source=source,land=source,net=vias[0]['net'],mine=list(vias),anchors=[],plan=None,orphan=False,ignored=True,warning='跨DIE忽略铜无法沿连接路径分离；保留源图并标记，需要恢复重构'))
            continue
        for owner in owners:
            mine=[v for v in vias if v['owner']==owner];scope=allocation.get(owner,(source,None))[0]
            out.append(dict(layer=ref[0],index=ref[1],owner=owner,refs=tuple((v['key'],v['index']) for v in mine),
                            source=source,routing_source=scope,land=shapely.union_all([v['land'] for v in mine]),net=mine[0]['net'],
                            mine=mine,anchors=[],plan=None,orphan=True,ignored=True,warning='忽略重构：按连接DIE刚体随动，固定过孔包围量仍检查'))
    return out
