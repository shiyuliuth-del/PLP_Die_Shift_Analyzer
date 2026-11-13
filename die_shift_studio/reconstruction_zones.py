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
        total=sum(max(r.tol,e['serial_part']['b']-e['serial_part']['a']) for e in usable);used=0.;occupied=shapely.Polygon()
        fixed=usable[-1]['serial_part']['B'] if usable else entry['source']
        for e in usable:
            part=e['serial_part'];length=part['b']-part['a']
            e.update(serial_left=1-used/total,serial_right=max(0.,1-(used+length)/total),
                     serial_block=part['C'].difference(occupied),serial_fixed=fixed)
            occupied=shapely.union_all([occupied,part['A'],part['C']]);used+=length
        for i,e in enumerate(usable):
            e['plan'].zone_limits=(usable[i-1]['serial_part']['b']+r.tol*16 if i else 0.,
                                  usable[i+1]['serial_part']['a']-r.tol*16 if i+1<len(usable) else e['plan'].length)
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
    regions=dict(A=transform(geos['A'],center,pose*right),
                 C=transform(entry['serial_block'],center,pose*left),B=entry['serial_fixed'])
    return adjusted,regions,None


def live_serial(window,settings,old,ref):
    """Recompose every serial segment together, including intermediate copper."""
    from .reconstruction import Reconstructor
    from .reconstruction_status import clone_plan_pool
    original=window.engine.reconstructor
    rebuilt=Reconstructor(original.model,original.p,settings,plan_pool=clone_plan_pool(original.plan_pool))
    try:local=rebuilt.frame(old['states'],targets={ref},diagnostics=False)
    finally:
        rebuilt.cache.close();rebuilt.block_cache.clear()
    data=old['reconstruction'];rows=[q for q in data['pieces'] if (q['layer'],q['index'])!=ref]+local['pieces']
    changed={k:[v for v in values if not (k==ref[0] and ref[1] in v['indices'])] for k,values in data['replacements'].items()}
    for k,values in local['replacements'].items():changed.setdefault(k,[]).extend(values)
    return dict(old,reconstruction=dict(data,configuration=settings.reconstruction_key(),pieces=rows,replacements=changed,violations=[],constraints_complete=False))
