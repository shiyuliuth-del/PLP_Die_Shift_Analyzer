"""Ignored reconstruction has explicit stationary and whole-source rigid modes."""
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
        vias=r.vias_by_target.get(ref,())
        if not vias:continue
        source=r.model.cache.layers[ref[0]]['geometry'][ref[1]]
        owners=sorted({v['owner'] for v in vias if v['owner']>=0})
        if not owners:continue
        following=ref in r.settings.reconstruction_following
        out.append(dict(layer=ref[0],index=ref[1],owner=owners[0],follow_owners=tuple(owners),following=following,
                        refs=tuple((v['key'],v['index']) for v in vias),source=source,routing_source=source,
                        land=source,net=vias[0]['net'],mine=list(vias),anchors=[],plan=None,orphan=False,
                        ignored=True,warning='忽略重构：整块随动' if following else '忽略重构：整块静止'))
    return out
