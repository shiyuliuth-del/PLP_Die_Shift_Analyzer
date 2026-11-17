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
        following=ref in r.settings.reconstruction_following or ref in getattr(r,'auto_following',())
        out.append(dict(layer=ref[0],index=ref[1],owner=owners[0],follow_owners=tuple(owners),following=following,
                        refs=tuple((v['key'],v['index']) for v in vias),source=source,routing_source=source,
                        land=source,net=vias[0]['net'],mine=list(vias),anchors=[],plan=None,orphan=False,
                        ignored=True,warning='忽略重构：整块随动' if following else '忽略重构：整块静止'))
    return out


def complex_mode(r,ref,vias,stationary_certificate):
    """Choose only a rigid mode certified over the complete independent box."""
    source=r.model.cache.layers[ref[0]]['geometry'][ref[1]]
    fixed=[v for v in r.stays if v['net']==vias[0]['net'] and source.intersects(v['poly'])]
    if len(vias)+len(fixed)<4:return None
    from .drc_rules import effective_rules
    width,space=effective_rules(r.p,r.settings)
    from .geometry_rules import width_ok
    if not width_ok(source,width,r.tol):return None
    def reach(g,owners):
        values=[0.]
        for owner in owners:
            if owner<0:continue
            mu,b=mean_and_bounds(r.p.for_die(owner),len(r.model.bound.centers))
            radius=np.linalg.norm(shapely.get_coordinates(g)-r.model.bound.centers[owner],axis=1).max()
            values.append(np.linalg.norm(abs(mu[:2])+b[:2])+2*radius*np.sin(min(np.pi,abs(mu[2])+b[2])/2))
        return max(values)
    owner_set={v['owner'] for v in vias if v['owner']>=0};choices=[]
    following=len(owner_set)==1 and fixed_enclosures_safe(r,ref,vias) and all(source.covers(v['poly'].buffer(r.p.annulus_um+2*r.tol)) for v in vias)
    for mode,valid in ((False,bool(stationary_certificate)),(True,following)):
        if not valid:continue
        amount=reach(source,owner_set) if mode else 0.
        swept=source.buffer(amount+2*r.tol)
        board=r.model.case['board'].buffer(-r.p.package_clearance_um)
        if not board.covers(swept):continue
        margin=board.boundary.distance(swept);safe=True
        for node in r.model.nodes:
            key=node['key'];role=r.model.roles[key]
            if role!='固定RDL' or key.split('/')[0]!=ref[0].split('/')[0] or node['net']==vias[0]['net']:continue
            other=r.vias_by_target.get((key,node['index']),())
            expansion=reach(node['poly'],{v['owner'] for v in other})
            gap=swept.distance(node['poly'])-expansion-space
            if gap<2*r.tol:safe=False;break
            margin=min(margin,gap)
        if safe and all(not swept.intersects(g) for g in r.model.case.get('obstacles',())):choices.append((margin,not mode,mode))
    return max(choices)[2] if choices else None
