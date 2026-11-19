"""Multiple source-bound transition intervals on one physical route.

Serial zones share the total displacement. They must not each translate a
complete prefix by the full DIE motion, which would cover preceding zones.
"""
import copy
from collections import OrderedDict,defaultdict
import numpy as np
import shapely
from .reconstruction_edits import key


def expand(r,entries):
    attached=defaultdict(list);seeded={};result=[]
    for row in r.settings.reconstruction_added:
        layer,index,x,y,identity,*scope=row
        candidates=[e for e in entries if (e['layer'],e['index'])==(layer,index) and e['plan'] is not None]
        if scope and scope!=['seed']:candidates=[e for e in candidates if e['plan'].identity==scope[0]]
        if candidates:
            best=min(candidates,key=lambda e:e['plan'].route.distance(shapely.Point(x,y)))
            if scope==['seed']:seeded[id(best)]=(x,y,identity)
            else:attached[id(best)].append((x,y,identity,scope))
    for entry in entries:
        if (entry['layer'],entry['index']) in getattr(r,'box_refs',()):result.append(entry);continue
        plan=entry['plan'];extras=attached.get(id(entry),());seed=seeded.get(id(entry))
        if seed and plan is not None:
            x,y,identity=seed;child=copy.copy(plan);child.cache={};child.pose_memo=OrderedDict();child.memo_bytes=0
            child.identity=plan.identity+'~'+identity
            a,b=plan.nominal_first,plan.nominal_second;position=float(plan.route.project(shapely.Point(x,y)))
            changed,_=child.edit(a,b,0,position-child.tolerance*16) if position<a else child.edit(a,b,1,position+child.tolerance*16) if position>b else (child.partition(a,b),False)
            if changed is not None:child.nominal_first,child.nominal_second=changed['a'],changed['b']
            entry=dict(entry,plan=child,seeded=True);plan=child
        if plan is None or not extras:
            if plan is None or key(plan) not in r.settings.reconstruction_deleted:result.append(entry)
            continue
        base,_=r.interval(entry)
        if base is None:result.append(entry);continue
        a,b=(extras[0][3][1:] if extras[0][3] else (plan.nominal_first,plan.nominal_second))
        marks=sorted((float(plan.route.project(shapely.Point(x,y))),identity) for x,y,identity,scope in extras)
        # The full interval is partitioned into genuine, disjoint serial zones.
        # Cursor positions choose seams; clamp away from the rigid terminals.
        spacing=max(plan.tolerance*32,min(plan.guard,(b-a)/(len(marks)+1)*.05))
        boundaries=[a]
        for j,(s,_) in enumerate(marks):
            boundaries.append(float(np.clip(s,boundaries[-1]+spacing*4,b-(len(marks)-j)*spacing*4)))
        boundaries.append(b);rows=[]
        identities=[None,*[identity for _,identity in marks]]
        for j,identity in enumerate(identities):
            child=copy.copy(plan);child.cache={};child.pose_memo=OrderedDict();child.memo_bytes=0
            child.identity=plan.identity if identity is None else plan.identity.split('~')[0]+'~'+identity
            child.nominal_first=boundaries[j]+(spacing if j else 0)
            child.nominal_second=boundaries[j+1]-(spacing if j<len(identities)-1 else 0)
            local=dict(entry,plan=child,serial_base=plan.identity)
            if key(child) in r.settings.reconstruction_deleted:continue
            part,_=r.interval(local)
            if part is None:
                local.update(plan=None,orphan=False,warning='新增动区截面无法分离连接端；保留原图并标记')
            else:local['serial_part']=part
            rows.append(local)
        usable=[e for e in rows if e['plan'] is not None]
        usable.sort(key=lambda e:e['serial_part']['a'])
        merged=[]
        for e in usable:
            if merged and e['serial_part']['a']<=merged[-1]['serial_part']['b']+r.tol:
                lead=merged[-1];a=lead['serial_part']['a'];b=max(lead['serial_part']['b'],e['serial_part']['b'])
                joined=lead['plan'].partition(a,b)
                if joined is not None:
                    lead['serial_part']=joined;lead['merged_part']=joined
                    lead['plan'].merged_keys=(*getattr(lead['plan'],'merged_keys',()),key(e['plan']))
                    continue
            merged.append(e)
        usable=merged
        rows=[e for e in rows if e['plan'] is None]+usable
        total=sum(max(r.tol,e['serial_part']['b']-e['serial_part']['a']) for e in usable);used=0.;occupied=shapely.Polygon()
        fixed=usable[-1]['serial_part']['B'] if usable else entry['source']
        for e in usable:
            part=e['serial_part'];length=part['b']-part['a']
            e.update(serial_left=1-used/total,serial_right=max(0.,1-(used+length)/total),
                     serial_block=part['C'].difference(occupied),serial_fixed=fixed)
            occupied=shapely.union_all([occupied,part['A'],part['C']]);used+=length
        result.extend(rows)
    return result


def serial_render(r,entry,part,center,pose):
    from .reconstruction import transform
    from .reconstruction_route import warp
    left,right=entry['serial_left'],entry['serial_right']
    theta=right*pose[2];co,si=np.cos(theta),np.sin(theta)
    translation=np.array([[co,si],[-si,co]])@((left-right)*pose[:2])
    relative=np.r_[translation,(left-right)*pose[2]]
    geos,error=warp(part,entry['plan'],center,relative,r.settings.reconstruction_style)
    adjusted=dict(part,C=entry['serial_block'],B=entry['serial_fixed'])
    if error:return adjusted,None,error
    active=transform(geos['A'],center,pose*right)
    seams=shapely.union_all([transform(part['C'],center,pose*left).boundary,transform(part['B'],center,pose*right).boundary])
    # Native candidate coordinates may have been rounded by one DBU. Restore
    # shared source endpoints AFTER composing partial rigid rotations.
    active=shapely.snap(active,seams,r.tol*2)
    regions=dict(A=active,C=transform(entry['serial_block'],center,pose*left),B=entry['serial_fixed'])
    return adjusted,regions,None


def live_serial(original,settings,old,ref,draft=False):
    """Recompose every serial segment together, including intermediate copper."""
    from .reconstruction import Reconstructor
    from .reconstruction_status import clone_plan_pool
    from .resources import cancel_check
    cancel_check()
    # Only this source's nominal paths are needed during a pointer move. Reuse
    # completed ownership/enclosure classification; do not rebuild all targets.
    base=getattr(original,'_base_plans',None)
    pool=clone_plan_pool({'local':[q for q in base if (q['layer'],q['index'])==ref]}) if base is not None else clone_plan_pool(original.plan_pool)
    same_rules=(settings.drc_checks,settings.drc_tolerance_um,settings.drc_min_angle_deg)==(original.settings.drc_checks,original.settings.drc_tolerance_um,original.settings.drc_min_angle_deg)
    rebuilt=Reconstructor(original.model,original.p,settings,plan_pool=pool,reference=original if same_rules else None)
    rebuilt.vias_by_target={ref:original.vias_by_target[ref]} if ref in original.vias_by_target else {}
    if base is not None and (not draft or (pool['local'] and ref not in rebuilt.box_refs and ref not in settings.reconstruction_ignored and ref not in original.settings.reconstruction_ignored and ref not in original.box_refs)):
        rebuilt.auto_following=set(original.auto_following);rebuilt.auto_ignored=dict(original.auto_ignored);rebuilt.safe_vias=dict(original.safe_vias)
        rebuilt._envelopes=original._envelopes;rebuilt._plans=expand(rebuilt,pool['local'])
    try:
        local=rebuilt.frame(old['states'],targets={ref},diagnostics=False,quick=True,_raw=True)
        data=old['reconstruction']
        rows=[q for q in data['pieces'] if (q['layer'],q['index'])!=ref]+local['pieces']
        changed={k:[v for v in values if not (k==ref[0] and ref[1] in v['indices']) and (draft or 'motion_owner' not in v)] for k,values in data['replacements'].items()}
        changed={k:values for k,values in changed.items() if values}
        for k,values in local['replacements'].items():
            changed.setdefault(k,[]).extend(values)
        if draft:
            repaired,blocked=(),()
        else:
            from .topology_routing import resolve
            if settings.reconstruction_fixed_via_mode!='off':
                # A local edit can change the motion of a shared fixed via.
                rebuilt._plans=[q for q in original._plans if (q['layer'],q['index'])!=ref]+list(rebuilt._plans or ())
            changed,rows,repaired,blocked=resolve(rebuilt,changed,rows,old['states'])
            from .ignored_motion import add_linked_geometry
            changed=add_linked_geometry(rebuilt,changed,old['states'])
    finally:
        rebuilt.close()
    return dict(old,_reconstruction_changed_refs=tuple(sorted({ref,*repaired})),reconstruction=dict(data,configuration=settings.reconstruction_key(),pieces=rows,replacements=changed,violations=[],constraints_complete=False,repair_refs=repaired,hard_failures=blocked,electrically_valid=not bool(blocked)))
