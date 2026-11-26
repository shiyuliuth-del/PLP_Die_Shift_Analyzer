"""Ignored reconstruction has explicit stationary and whole-source rigid modes."""
import numpy as np
import shapely
from .statistics import mean_and_bounds
from .reconstruction_ownership import divide


def fixed_enclosures_safe(r,ref,vias):
    source=r.model.cache.layers[ref[0]]['geometry'][ref[1]]
    owners={v['owner'] for v in vias if v['owner']>=0}
    if len(owners)!=1:return False
    rdl_net=int(r.model.nodes[r.model.ids[ref]]['net'])
    owner=next(iter(owners));mu,b=mean_and_bounds(r.p.for_die(owner),len(r.model.bound.centers))
    radius=np.linalg.norm(shapely.get_coordinates(source)-r.model.bound.centers[owner],axis=1).max()
    reach=np.linalg.norm(abs(mu[:2])+b[:2])+2*radius*np.sin(min(np.pi,abs(mu[2])+b[2])/2)
    inner=source if r.settings.reconstruction_fixed_via_mode=='global' else source.buffer(-reach-4*r.tol)
    return all(inner.covers(v['poly'].buffer(r.rules.p.annulus_um)) for v in r.stays
               if v['net']==rdl_net and source.intersects(v['poly']))


def entries(r):
    out=[]
    for ref in sorted(set(r.settings.reconstruction_ignored)|set(r.auto_ignored)):
        if ref in getattr(r,'box_refs',()):continue
        vias=r.vias_by_target.get(ref,())
        internal=r.die_internal.get(ref)
        if not vias and internal is None:continue
        source=r.model.cache.layers[ref[0]]['geometry'][ref[1]]
        owners=[internal] if internal is not None else sorted({v['owner'] for v in vias if v['owner']>=0})
        if not owners:continue
        following=(ref in r.settings.reconstruction_following) if ref in r.settings.reconstruction_ignored else (ref in r.settings.reconstruction_following or ref in getattr(r,'auto_following',()))
        node=r.model.nodes[r.model.ids[ref]]
        mine=list(vias) or [dict(center=np.asarray(source.centroid.coords[0]))]
        out.append(dict(layer=ref[0],index=ref[1],owner=owners[0],follow_owners=tuple(owners),following=following,
                        refs=tuple((v['key'],v['index']) for v in vias),source=source,routing_source=source,
                        land=source,net=node['net'],mine=mine,anchors=[],plan=None,orphan=False,
                        ignored=True,warning='忽略重构：整块随动' if following else '忽略重构：整块静止'))
    return out


def complex_mode(r,ref,vias,stationary_certificate):
    """Choose only a rigid mode certified over the complete independent box."""
    source=r.model.cache.layers[ref[0]]['geometry'][ref[1]]
    rdl_net=int(r.model.nodes[r.model.ids[ref]]['net'])
    fixed=[v for v in r.stays if v['net']==rdl_net and source.intersects(v['poly'])]
    from .drc_rules import effective_rules
    width,space=effective_rules(r.p,r.settings)
    from .geometry_rules import width_ok
    from .drc_policy import enabled
    intrinsic_bad=int(enabled(r.settings,'width') and not width_ok(source,width,r.tol))
    def reach(g,owners):
        values=[0.]
        for owner in owners:
            if owner<0:continue
            mu,b=mean_and_bounds(r.p.for_die(owner),len(r.model.bound.centers))
            radius=np.linalg.norm(shapely.get_coordinates(g)-r.model.bound.centers[owner],axis=1).max()
            values.append(np.linalg.norm(abs(mu[:2])+b[:2])+2*radius*np.sin(min(np.pi,abs(mu[2])+b[2])/2))
        return max(values)
    owner_set={v['owner'] for v in vias if v['owner']>=0};choices=[]
    following=len(owner_set)==1 and fixed_enclosures_safe(r,ref,vias) and all(source.covers(v['poly'].buffer(r.rules.p.annulus_um+2*r.tol)) for v in vias)
    for mode,valid in ((False,bool(stationary_certificate)),(True,following)):
        if not valid:continue
        amount=reach(source,owner_set) if mode else 0.
        swept=source.buffer(amount+2*r.tol)
        board=r.model.case['board'].buffer(-r.rules.p.package_clearance_um)
        if not board.covers(swept):continue
        margin=board.boundary.distance(swept);safe=True
        for node in r.model.nodes:
            key=node['key'];role=r.model.roles[key]
            if role!='固定RDL' or key.split('/')[0]!=ref[0].split('/')[0] or node['net']==rdl_net:continue
            other=r.vias_by_target.get((key,node['index']),())
            expansion=reach(node['poly'],{v['owner'] for v in other})
            gap=swept.distance(node['poly'])-expansion-space
            # No physical contact may be hidden by automatic ignore. A
            # dimensional spacing deficit is ranked, not called a short.
            if gap+space<2*r.tol:safe=False;break
            margin=min(margin,gap)
        if safe and not (not r.rules.keepout.is_empty and shapely.relate_pattern(swept,r.rules.keepout,'T********')):
            # Conservative whole-input envelopes. Prefer no rule deficit,
            # then the smallest residual dimensional deficit and larger room.
            choices.append((-(intrinsic_bad+int(margin<0)),-max(0.,-margin),margin,not mode,mode))
    return max(choices)[-1] if choices else None



def linked_fixed_vias(r,entries):
    """Use nominal stack links, including fixed-via chains, without editing the model."""
    from collections import defaultdict
    roots={(q['layer'],q['index']):q.get('follow_owners',(q['die'],) if 'die' in q else ()) for q in entries if q.get('ignored') and q.get('following',q.get('ignored_following'))}
    links=defaultdict(set)
    for row in r.model.connections.links:
        a=(row['upper'],row['upper_index']);b=(row['lower'],row['lower_index'])
        links[a].add(b);links[b].add(a)
    attached=defaultdict(set)
    for ref,owners in roots.items():
        if not owners:continue
        seen={ref};queue=[ref]
        for item in queue:
            for via in links[item]-seen:
                if r.model.roles.get(via[0])!='固定过孔':continue
                seen.add(via);queue.append(via);attached[via].update(owners)
    return {ref:tuple(sorted(owners)) for ref,owners in sorted(attached.items())}


def linked_owners(r,states):
    if not r.settings.links_fixed_vias:return {},()
    if getattr(r,'_linked_plans',None) is not r._plans:
        r._linked_vias=linked_fixed_vias(r,r._plans or ())
        r._linked_plans=r._plans
    from .current_drc import _matrix
    owners={};conflicts=[];individual=r.settings.reconstruction_fixed_via_mode=='individual'
    selected=set(r.settings.reconstruction_fixed_via_follows)
    matrices={}
    for ref,candidates in getattr(r,'_linked_vias',{}).items():
        if individual and ref not in selected:continue
        first,*others=candidates
        for owner in candidates:
            if owner not in matrices:matrices[owner]=_matrix(r.model,owner,states)
        if all(np.allclose(matrices[first],matrices[other],atol=r.tol*.01,rtol=0) for other in others):
            owners[ref]=first
        else:
            conflicts.append((ref,'联动固定过孔连接多个不同刚体姿态，无法同时随动'))
    return owners,tuple(conflicts)


def add_linked_geometry(r,out,states):
    owners,conflicts=linked_owners(r,states)
    if not owners and not conflicts:return out
    result={layer:list(rows) for layer,rows in out.items()}
    for ref,owner in [*owners.items(),*((ref,-1) for ref,_ in conflicts)]:
        if owner>=0 and not np.any(states[owner]):continue
        node=r.model.nodes[r.model.ids[ref]]
        result.setdefault(ref[0],[]).append(dict(indices=(ref[1],),geometry=r.model.moved(node['poly'],owner,states),
                                                  net=node['net'],motion_owner=owner))
    return result
